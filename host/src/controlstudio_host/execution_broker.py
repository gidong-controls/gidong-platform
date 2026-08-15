"""Platform-owned execution boundary for Agent Protocol requests.

The broker exposes a serializable catalog only.  Pydantic AI toolsets and raw
MCP clients are intentionally private to neither the Host nor the Agent
protocol: the Host maps each proposal to its existing MCP gateway after it has
bound the active run and target.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol

from controlstudio_contracts import (
    AgentToolDefinitionV1,
    ExternalToolRequestV1,
    ExternalToolResultV1,
)

from .api_models import (
    LocalWorkspaceCompareV1,
    MneInstructionBatchPatchCandidateV5,
    MneInstructionPatchCandidateV4,
    MneRungPatchCandidateV6,
    MneSeriesGuardCandidateV3,
)
from .mcp_client import (
    AGENT_CONTROLLER_MCP_TOOLS,
    AGENT_TOOL_NAMES,
    AGENT_TOOL_REQUIRED_IDENTITY_FIELDS,
    CONTROLLER_ENDPOINT_FIELDS,
    SESSION_IDENTITY_FIELDS,
    MCPGateway,
    MCPStartupError,
    MCPToolError,
    MCPTransportError,
)
from .structural_evidence import build_structural_evidence_contract

logger = logging.getLogger(__name__)

ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
ControllerPreflight = Callable[[str, str], Awaitable[bool]]

_WORKSPACE_COMPARE_FIELDS = {
    "schema",
    "ok",
    "verified",
    "workspace_id",
    "revision_id",
    "source_kind",
    "source",
    "working_copy",
    "file_identical",
    "status",
    "structure",
    "limitations",
    "compare_digest",
}


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def validate_workspace_compare_result(
    result: Any,
    *,
    expected_workspace_id: str,
) -> LocalWorkspaceCompareV1:
    if not isinstance(result, Mapping):
        raise MCPToolError("workspace comparison returned no structured result")
    if set(result) != _WORKSPACE_COMPARE_FIELDS | {"operation", "schema_version"}:
        raise MCPToolError("workspace comparison shape mismatched")
    supplied_digest = result.get("compare_digest")
    unsigned = {
        name: result[name]
        for name in _WORKSPACE_COMPARE_FIELDS
        if name != "compare_digest"
    }
    if (
        result.get("operation") != "workspace.compare"
        or not isinstance(supplied_digest, str)
        or supplied_digest != _canonical_digest(unsigned)
    ):
        raise MCPToolError("workspace comparison digest is invalid")
    try:
        comparison = LocalWorkspaceCompareV1.model_validate(
            unsigned | {"compare_digest": supplied_digest}
        )
    except ValueError as exc:
        raise MCPToolError("workspace comparison contract is invalid") from exc
    if comparison.workspace_id != expected_workspace_id:
        raise MCPToolError("workspace comparison target changed")
    return comparison


class ExecutionBroker(Protocol):
    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def clear_agent_session(self) -> None: ...

    def bind_controller_preflight(self, callback: ControllerPreflight) -> None: ...

    async def agent_tool_catalog(
        self, *, conversation_id: str, run_id: str
    ) -> list[AgentToolDefinitionV1]: ...

    async def execute_external_tool_request(
        self, request: ExternalToolRequestV1, *, sink: ToolEventSink
    ) -> Any: ...


class MCPExecutionBroker:
    """Map Agent v1 tool proposals to Host-authorized MCP gateway calls."""

    def __init__(self, gateway: MCPGateway) -> None:
        self._gateway = gateway
        self._controller_preflight: ControllerPreflight | None = None

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        self._gateway.bind_agent_session(session)

    def clear_agent_session(self) -> None:
        self._gateway.clear_agent_session()

    def bind_controller_preflight(self, callback: ControllerPreflight) -> None:
        self._controller_preflight = callback

    async def agent_tool_catalog(
        self, *, conversation_id: str, run_id: str
    ) -> list[AgentToolDefinitionV1]:
        controller_authorized = (
            await self._controller_preflight(conversation_id, run_id)
            if self._controller_preflight is not None
            else False
        )
        available = {tool.name: tool for tool in await self._gateway.agent_mcp_client.list_tools()}
        definitions: list[AgentToolDefinitionV1] = []
        for alias, raw_name in AGENT_TOOL_NAMES.items():
            if raw_name in AGENT_CONTROLLER_MCP_TOOLS and not controller_authorized:
                continue
            tool = available.get(raw_name)
            if tool is None:
                raise MCPStartupError(f"required Agent tool is missing: {raw_name}")
            schema = getattr(tool, "inputSchema", None)
            if not isinstance(schema, dict):
                schema = getattr(tool, "parameters", None)
            if not isinstance(schema, dict):
                raise MCPStartupError(f"{raw_name} has no input schema")
            prepared = self._public_schema(raw_name, schema)
            if prepared is None:
                continue
            description = getattr(tool, "description", None)
            definitions.append(
                AgentToolDefinitionV1(
                    tool_name=alias,
                    description=description if isinstance(description, str) else None,
                    parameters_json_schema=prepared,
                )
            )
        return definitions

    async def execute_external_tool_request(
        self, request: ExternalToolRequestV1, *, sink: ToolEventSink
    ) -> Any:
        raw_name = AGENT_TOOL_NAMES.get(request.tool_name)
        if raw_name is None:
            raise MCPToolError("Agent requested an unknown external tool")
        event_payload = self._event_payload(request)
        await self._emit_event(sink, "tool.started", event_payload)
        try:
            controller_authorized = (
                await self._controller_preflight(
                    request.conversation_id,
                    request.run_id,
                )
                if self._controller_preflight is not None
                else False
            )
            if raw_name in AGENT_CONTROLLER_MCP_TOOLS and not controller_authorized:
                raise MCPToolError(
                    "controller tool is not authorized for this engineering target"
                )
            expected_session_identity = self._gateway.agent_session_identity_snapshot
            result = await self._gateway.execute_agent_tool(raw_name, request.arguments)
            if (
                self._gateway.agent_session_identity_snapshot
                != expected_session_identity
            ):
                raise MCPToolError(
                    "Host-bound engineering session changed during tool execution"
                )
            evidence_contract = self._evidence_contract(
                raw_name,
                result,
                expected_session_identity=expected_session_identity,
            )
        except MCPTransportError:
            await self._emit_failure(sink, request, "transport")
            raise
        except MCPToolError as exc:
            logger.warning("Agent tool rejected (tool=%s): %s", raw_name, exc)
            await self._emit_failure(sink, request, "host_policy")
            raise
        except asyncio.CancelledError:
            await self._emit_failure(sink, request, "cancelled")
            raise
        except Exception as exc:
            await self._emit_failure(sink, request, "transport")
            raise MCPToolError("Platform execution broker failed") from exc
        ExternalToolResultV1(request_id=request.request_id, tool_name=request.tool_name, status="completed")
        completed_payload = dict(event_payload)
        if evidence_contract is not None:
            completed_payload["evidence_contract"] = evidence_contract
        await self._emit_event(sink, "tool.completed", completed_payload)
        return result

    @staticmethod
    def _canonical_digest(value: Any) -> str:
        return _canonical_digest(value)

    @classmethod
    def _evidence_contract(
        cls,
        raw_name: str,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if raw_name == "project.hardware_context":
            return cls._hardware_context_evidence_contract(result)
        if raw_name == "workspace.compare":
            return cls._workspace_compare_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "program.change_target_analysis":
            return cls._change_target_evidence_contract(result)
        if raw_name in {
            "program.compile_mne_instruction_batch_patch_candidate",
            "program.compile_mne_instruction_patch_candidate",
            "program.compile_mne_rung_patch_candidate",
            "program.compile_mne_series_guard_candidate",
        }:
            return cls._mne_patch_candidate_evidence_contract(raw_name, result)
        return build_structural_evidence_contract(
            raw_name,
            result,
            expected_session_identity=expected_session_identity,
        )

    @classmethod
    def _workspace_compare_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_workspace_id = (
            expected_session_identity.get("workspace_id")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        if not isinstance(expected_workspace_id, str):
            raise MCPToolError("workspace comparison target is not bound")
        comparison = validate_workspace_compare_result(
            result,
            expected_workspace_id=expected_workspace_id,
        )
        sections = comparison.structure.sections if comparison.structure is not None else {}
        contract = {
            "schema": "controlstudio-workspace-compare-evidence/v1",
            "workspaceId": comparison.workspace_id,
            "revisionId": comparison.revision_id,
            "sourceKind": comparison.source_kind,
            "sourceSha256": comparison.source.sha256,
            "workingSha256": comparison.working_copy.sha256,
            "fileIdentical": comparison.file_identical,
            "status": comparison.status,
            "structureStatus": (
                comparison.structure.status if comparison.structure is not None else None
            ),
            "structuralChangeCount": sum(
                item.added_count + item.removed_count + item.changed_count
                for item in sections.values()
            ),
            "limitationCodes": [item.code for item in comparison.limitations],
            "compareDigest": comparison.compare_digest,
        }
        contract["receiptDigest"] = cls._canonical_digest(contract)
        return contract

    @classmethod
    def _hardware_context_evidence_contract(
        cls,
        result: Any,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise MCPToolError("hardware context returned no structured result")
        context = dict(result)
        supplied_digest = context.pop("context_digest", None)
        if (
            context.get("schema") != "xg5000.project.hardware-context/1.0"
            or not isinstance(supplied_digest, str)
            or supplied_digest != cls._canonical_digest(context)
        ):
            raise MCPToolError("hardware context digest is invalid")
        project_binding = result.get("project_binding")
        cpu = result.get("cpu")
        timer_areas = result.get("timer_areas")
        io_modules = result.get("io_modules")
        io_mapping = result.get("io_mapping")
        external_wiring_observed = result.get("external_wiring_observed")
        blockers = result.get("blockers")
        ready = result.get("offline_candidate_ready")
        if not isinstance(project_binding, Mapping) or not isinstance(cpu, Mapping):
            raise MCPToolError("hardware context project or CPU binding is invalid")
        if not isinstance(timer_areas, list) or any(
            not isinstance(item, Mapping) for item in timer_areas
        ):
            raise MCPToolError("hardware context timer areas are invalid")
        if not isinstance(io_modules, list) or any(
            not isinstance(item, Mapping) for item in io_modules
        ):
            raise MCPToolError("hardware context I/O modules are invalid")
        if not isinstance(io_mapping, Mapping) or not isinstance(
            external_wiring_observed,
            bool,
        ):
            raise MCPToolError("hardware context I/O mapping is invalid")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item for item in blockers
        ):
            raise MCPToolError("hardware context blockers are invalid")
        if not isinstance(ready, bool):
            raise MCPToolError("hardware context readiness is invalid")

        if ready != (not blockers):
            raise MCPToolError("hardware context readiness contradicts its blockers")

        contract: dict[str, Any] = {
            "schema": "controlstudio-hardware-context-evidence/v1",
            "contextDigest": supplied_digest,
            "projectBindingDigest": cls._canonical_digest(project_binding),
            "projectHash": project_binding.get("project_hash"),
            "cpu": dict(cpu),
            "timerAreas": [dict(item) for item in timer_areas],
            "ioModules": [dict(item) for item in io_modules],
            "ioMapping": dict(io_mapping),
            "externalWiringObserved": external_wiring_observed,
            "blockers": list(blockers),
            "offlineCandidateReady": ready,
        }
        if (
            not isinstance(contract["projectHash"], str)
            or not contract["projectHash"]
        ):
            raise MCPToolError("hardware context is missing its project hash")
        contract["receiptDigest"] = cls._canonical_digest(contract)
        return contract

    @classmethod
    def _change_target_evidence_contract(
        cls,
        result: Any,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise MCPToolError("change target analysis returned no structured result")
        analysis = result.get("analysis")
        if not isinstance(analysis, Mapping):
            raise MCPToolError("change target analysis returned no receipt")
        canonical_analysis = dict(analysis)
        supplied_digest = canonical_analysis.pop("analysis_digest", None)
        if (
            not isinstance(supplied_digest, str)
            or supplied_digest != cls._canonical_digest(canonical_analysis)
        ):
            raise MCPToolError("change target analysis receipt digest is invalid")
        target = analysis.get("target")
        current_semantics = analysis.get("current_semantics")
        proposed_semantics = analysis.get("proposed_semantics")
        impact = analysis.get("impact")
        project_binding = analysis.get("project_binding")
        hardware_context_binding = analysis.get("hardware_context_binding")
        blockers = analysis.get("blocking_reasons")
        if not all(
            isinstance(value, Mapping)
            for value in (
                target,
                current_semantics,
                impact,
                project_binding,
            )
        ):
            raise MCPToolError("change target analysis receipt is incomplete")
        if hardware_context_binding is not None and not isinstance(
            hardware_context_binding,
            Mapping,
        ):
            raise MCPToolError("change target hardware context binding is invalid")
        if proposed_semantics is not None and not isinstance(proposed_semantics, Mapping):
            raise MCPToolError("change target proposed semantics are invalid")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) for item in blockers
        ):
            raise MCPToolError("change target blockers are invalid")
        assert isinstance(target, Mapping)
        assert isinstance(current_semantics, Mapping)
        assert isinstance(impact, Mapping)
        assert isinstance(project_binding, Mapping)
        contract: dict[str, Any] = {
            "schema": "controlstudio-change-target-evidence/v1",
            "analysisDigest": supplied_digest,
            "projectBindingDigest": cls._canonical_digest(project_binding),
            "programId": target.get("program_id"),
            "programName": target.get("program_name"),
            "valueTokenOffset": target.get("value_token_offset"),
            "currentValue": target.get("value"),
            "proposedValue": analysis.get("proposed_value"),
            "recordKind": target.get("record_kind"),
            "visualRow": target.get("visual_row"),
            "column": target.get("column"),
            "networkId": target.get("network_id"),
            "networkDigest": target.get("network_digest"),
            "networkIndex": target.get("network_index"),
            "networkSemanticStatus": target.get("network_semantic_status"),
            "rungId": target.get("rung_id"),
            "rungRole": target.get("rung_role"),
            "rungSemanticStatus": target.get("rung_semantic_status"),
            "currentSemantics": dict(current_semantics),
            "proposedSemantics": (
                dict(proposed_semantics)
                if isinstance(proposed_semantics, Mapping)
                else None
            ),
            "primaryDevices": list(impact.get("primary_devices") or []),
            "physicalOutputCandidates": list(
                impact.get("physical_output_candidates") or []
            ),
            "multipleWriterDevices": list(
                impact.get("multiple_writer_devices") or []
            ),
            "hardwareContextBinding": (
                dict(hardware_context_binding)
                if isinstance(hardware_context_binding, Mapping)
                else None
            ),
            "offlineCandidateReady": analysis.get("offline_candidate_ready") is True,
            "blockingReasons": list(blockers),
        }
        required_text = (
            "analysisDigest",
            "projectBindingDigest",
            "programId",
            "programName",
            "currentValue",
            "recordKind",
            "networkId",
            "networkDigest",
        )
        if any(not isinstance(contract[field], str) or not contract[field] for field in required_text):
            raise MCPToolError("change target evidence is missing exact native identity")
        if (
            not isinstance(contract["valueTokenOffset"], int)
            or isinstance(contract["valueTokenOffset"], bool)
            or contract["valueTokenOffset"] < 0
        ):
            raise MCPToolError("change target evidence has an invalid value token offset")
        if isinstance(hardware_context_binding, Mapping):
            hardware_digest = hardware_context_binding.get("context_digest")
            if (
                hardware_context_binding.get("schema")
                != "xg5000.project.hardware-context/1.0"
                or not isinstance(hardware_digest, str)
                or len(hardware_digest) != 64
            ):
                raise MCPToolError("change target evidence has an invalid hardware binding")
        elif (
            contract["offlineCandidateReady"] is True
            or "hardware_context_unobserved" not in blockers
        ):
            raise MCPToolError("change target evidence omitted required hardware context")
        contract["receiptDigest"] = cls._canonical_digest(contract)
        return contract

    @classmethod
    def _mne_patch_candidate_evidence_contract(
        cls, raw_name: str, result: Any
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise MCPToolError("MNE patch compiler returned no structured result")
        if not isinstance(result.get("candidate"), Mapping):
            raise MCPToolError("MNE patch compiler returned no candidate receipt")
        contract_by_tool = {
            "program.compile_mne_series_guard_candidate": (
                MneSeriesGuardCandidateV3,
                "controlstudio-mne-series-guard-candidate-evidence/v1",
            ),
            "program.compile_mne_instruction_patch_candidate": (
                MneInstructionPatchCandidateV4,
                "controlstudio-mne-instruction-patch-candidate-evidence/v1",
            ),
            "program.compile_mne_instruction_batch_patch_candidate": (
                MneInstructionBatchPatchCandidateV5,
                "controlstudio-mne-instruction-batch-patch-candidate-evidence/v1",
            ),
            "program.compile_mne_rung_patch_candidate": (
                MneRungPatchCandidateV6,
                "controlstudio-mne-rung-patch-candidate-evidence/v1",
            ),
        }
        model, schema = contract_by_tool[raw_name]
        try:
            candidate = model.model_validate(result["candidate"]).model_dump(
                mode="json",
                by_alias=False,
            )
        except ValueError as exc:
            raise MCPToolError("MNE patch compiler candidate is invalid") from exc
        contract = {
            "schema": schema,
            "candidate": candidate,
        }
        contract["receiptDigest"] = cls._canonical_digest(contract)
        return contract

    def _public_schema(self, raw_name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        engineering_tool = raw_name in AGENT_TOOL_REQUIRED_IDENTITY_FIELDS
        controller_tool = raw_name in AGENT_CONTROLLER_MCP_TOOLS
        if not engineering_tool and not controller_tool:
            raise MCPStartupError(f"agent tool has no Host target policy: {raw_name}")
        if engineering_tool:
            identity = self._gateway.agent_session_identity_snapshot
            required_identity = AGENT_TOOL_REQUIRED_IDENTITY_FIELDS[raw_name]
            if identity is None or any(
                not isinstance(identity.get(field), (str, int)) or identity.get(field) == ""
                for field in required_identity
            ):
                return None
        if controller_tool and not self._gateway.controller_observation_current:
            return None
        result = deepcopy(schema)
        properties = result.get("properties")
        required = result.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise MCPStartupError(f"{raw_name} has an invalid model-facing input schema")
        blocked = set(SESSION_IDENTITY_FIELDS)
        blocked.update(AGENT_TOOL_REQUIRED_IDENTITY_FIELDS.get(raw_name, ()))
        if controller_tool:
            blocked.update(CONTROLLER_ENDPOINT_FIELDS)
        if raw_name == "project.snapshot":
            blocked.update({"workspace_id", "begin_change_transaction"})
        for field in blocked:
            properties.pop(field, None)
        result["required"] = [field for field in required if field not in blocked]
        return result

    @staticmethod
    def _event_payload(request: ExternalToolRequestV1) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tool_name": request.tool_name,
            "request_id": request.request_id,
        }
        if request.tool_call_id is not None:
            result["tool_call_id"] = request.tool_call_id
        return result

    async def _emit_failure(
        self, sink: ToolEventSink, request: ExternalToolRequestV1, failure_kind: str
    ) -> None:
        ExternalToolResultV1(
            request_id=request.request_id,
            tool_name=request.tool_name,
            status="failed",
            failure_kind=failure_kind,
        )
        await self._emit_event(
            sink, "tool.failed", {**self._event_payload(request), "failure_kind": failure_kind}
        )

    @staticmethod
    async def _emit_event(sink: ToolEventSink, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await sink(event_type, payload)
        except Exception:
            logger.exception("Agent tool progress sink failed (event_type=%s)", event_type)
