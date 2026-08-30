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
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol

from controlstudio_contracts import (
    AgentToolDefinitionV1,
    ExternalToolRequestV1,
    ExternalToolResultV1,
)

from .agent_protocol import scrub_agent_tool_result
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
    WORKSPACE_STATIC_IDENTITY_FIELDS,
    MCPGateway,
    MCPStartupError,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
)
from .structural_evidence import (
    build_structural_evidence_contract,
    project_search_context,
)

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
_WORKSPACE_HARDWARE_CONTEXT_FIELDS = {
    "schema",
    "workspace_id",
    "revision_id",
    "source_kind",
    "source_sha256",
    "working_sha256",
    "hardware_context",
    "receipt_digest",
}
_WORKSPACE_STRUCTURE_INDEX_FIELDS = {
    "schema",
    "workspace_binding",
    "structure_index",
    "receipt_digest",
}
_WORKSPACE_SEARCH_FIELDS = {
    "schema",
    "workspace_binding",
    "project_search",
    "receipt_digest",
}
_WORKSPACE_STATIC_BINDING_FIELDS = {
    "workspace_id",
    "revision_id",
    "source_kind",
    "provenance_receipt_digest",
    "source_sha256",
    "working_path",
    "working_sha256",
}
_WORKSPACE_STATIC_TOOLS = frozenset(
    {
        "workspace.compare",
        "workspace.hardware_context",
        "workspace.structure_index",
        "workspace.search",
    }
)
_PROJECT_STRUCTURE_INDEX_ENVELOPE_FIELDS = {
    "session_id",
    "operation",
    "ok",
    "verified",
    "structure_index",
    "index_digest",
    "receipt_path",
    "project_hash_before",
    "project_hash_after",
    "project_unchanged",
    "operation_id",
}
_HARDWARE_CONTEXT_BINDING_PRECONDITION = (
    "project.hardware_context must be called before binding its digest"
)


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _expected_workspace_identity(
    value: Mapping[str, Any] | None,
) -> tuple[str, str]:
    workspace_id = value.get("workspace_id") if isinstance(value, Mapping) else None
    revision_id = value.get("revision_id") if isinstance(value, Mapping) else None
    if not isinstance(workspace_id, str) or not isinstance(revision_id, str):
        raise MCPToolError("workspace target is not bound")
    return workspace_id, revision_id


def _expected_workspace_static_identity(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MCPToolError("workspace target is not bound")
    identity = {field: value.get(field) for field in _WORKSPACE_STATIC_BINDING_FIELDS}
    if any(field not in value for field in _WORKSPACE_STATIC_BINDING_FIELDS):
        raise MCPToolError("workspace target lineage is not bound")
    _validated_workspace_static_binding(
        identity,
        expected_identity=identity,
        label="target",
    )
    return identity


def _validated_workspace_static_binding(
    value: Any,
    *,
    expected_identity: Mapping[str, Any],
    label: str,
) -> tuple[str, str, str, str, str, str | None]:
    if not isinstance(value, Mapping) or set(value) != _WORKSPACE_STATIC_BINDING_FIELDS:
        raise MCPToolError(f"workspace {label} binding is invalid")
    workspace_id = value.get("workspace_id")
    revision_id = value.get("revision_id")
    source_kind = value.get("source_kind")
    provenance_digest = value.get("provenance_receipt_digest")
    source_sha256 = value.get("source_sha256")
    working_path = value.get("working_path")
    working_sha256 = value.get("working_sha256")
    if (
        workspace_id != expected_identity.get("workspace_id")
        or revision_id != expected_identity.get("revision_id")
        or source_kind != expected_identity.get("source_kind")
        or provenance_digest
        != expected_identity.get("provenance_receipt_digest")
        or source_sha256 != expected_identity.get("source_sha256")
        or str(working_path).replace("/", "\\").casefold()
        != str(expected_identity.get("working_path") or "")
        .replace("/", "\\")
        .casefold()
        or working_sha256 != expected_identity.get("working_sha256")
        or source_kind not in {
            "local_file",
            "restored_from_controller",
            "new_build",
        }
        or (source_kind == "local_file") != (provenance_digest is None)
        or (
            provenance_digest is not None
            and not _is_sha256(provenance_digest)
        )
        or not isinstance(working_path, str)
        or not working_path.casefold().endswith(".xgwx")
        or not all(_is_sha256(item) for item in (source_sha256, working_sha256))
    ):
        raise MCPToolError(f"workspace {label} binding is invalid")
    return (
        workspace_id,
        revision_id,
        source_kind,
        working_path,
        working_sha256,
        provenance_digest,
    )


def _validate_workspace_status_lineage(
    value: Any,
    *,
    expected_identity: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise MCPToolError("workspace status lineage is invalid")
    source = value.get("source")
    working = value.get("working_copy")
    provenance = value.get("provenance")
    if (
        value.get("verified") is not True
        or not isinstance(source, Mapping)
        or not isinstance(working, Mapping)
        or (provenance is not None and not isinstance(provenance, Mapping))
    ):
        raise MCPToolError("workspace status lineage is invalid")
    source_kind = value.get("source_kind")
    provenance_digest = None
    if isinstance(provenance, Mapping):
        provenance_digest = provenance.get(
            "receiptDigest" if source_kind == "restored_from_controller" else "receipt_digest"
        )
    _validated_workspace_static_binding(
        {
            "workspace_id": value.get("workspace_id"),
            "revision_id": value.get("revision_id"),
            "source_kind": source_kind,
            "provenance_receipt_digest": provenance_digest,
            "source_sha256": source.get("sha256"),
            "working_path": working.get("path"),
            "working_sha256": working.get("current_sha256"),
        },
        expected_identity=expected_identity,
        label="status",
    )


def validate_workspace_compare_result(
    result: Any,
    *,
    expected_workspace_id: str,
    expected_revision_id: str | None = None,
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
    if (
        expected_revision_id is not None
        and comparison.revision_id != expected_revision_id
    ):
        raise MCPToolError("workspace comparison revision changed")
    return comparison


class ExecutionBroker(Protocol):
    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None: ...

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
        # Context receipts are Host-validated evidence, retained per Agent run.
        # They are never an authority to mutate or deploy a controller.
        self._retained_hardware_contexts: dict[
            tuple[str, str], dict[str, dict[str, Any]]
        ] = {}

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        # A hardware-context digest is scoped to the exact MCP/XG5000 session.
        self._retained_hardware_contexts.clear()
        self._gateway.bind_agent_session(session)

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None:
        self._gateway.bind_agent_workspace(workspace)

    def clear_agent_session(self) -> None:
        self._retained_hardware_contexts.clear()
        self._gateway.clear_agent_session()

    def bind_retained_hardware_context_evidence(
        self,
        *,
        conversation_id: str,
        run_id: str,
        evidence: list[Mapping[str, Any]],
    ) -> None:
        """Retain only exact Host-journaled hardware receipts for one run."""

        key = (conversation_id, run_id)
        retained: dict[str, dict[str, Any]] = {}
        for item in evidence:
            if item.get("toolName") != "project.hardware_context":
                continue
            receipt = item.get("receipt")
            contract = self._validated_retained_hardware_context_contract(receipt)
            digest = contract["contextDigest"]
            previous = retained.get(digest)
            if previous is not None and previous != contract:
                raise MCPToolError("retained hardware context evidence is ambiguous")
            retained[digest] = contract
        if retained:
            self._retained_hardware_contexts[key] = retained

    @staticmethod
    def _validated_retained_hardware_context_contract(
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise MCPToolError("retained hardware context evidence is invalid")
        contract = dict(value)
        receipt_digest = contract.pop("receiptDigest", None)
        if (
            contract.get("schema") != "controlstudio-hardware-context-evidence/v1"
            or not _is_sha256(contract.get("contextDigest"))
            or not _is_sha256(contract.get("projectHash"))
            or not isinstance(receipt_digest, str)
            or receipt_digest != _canonical_digest(contract)
        ):
            raise MCPToolError("retained hardware context evidence is invalid")
        return {**contract, "receiptDigest": receipt_digest}

    @classmethod
    def _hardware_context_contract_from_result(cls, result: Any) -> dict[str, Any]:
        # The stdio MCP envelope carries the context under hardware_context;
        # in-process fixtures may return the context directly.
        candidate = (
            result.get("hardware_context")
            if isinstance(result, Mapping) and isinstance(result.get("hardware_context"), Mapping)
            else result
        )
        return cls._hardware_context_evidence_contract(candidate)

    def _retain_hardware_context_contract(
        self,
        *,
        conversation_id: str,
        run_id: str,
        contract: Mapping[str, Any],
    ) -> None:
        validated = self._validated_retained_hardware_context_contract(contract)
        self._retained_hardware_contexts.setdefault((conversation_id, run_id), {})[
            validated["contextDigest"]
        ] = validated

    @staticmethod
    def _hardware_context_engineering_facts(
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project stable hardware facts, excluding session-local receipt identity."""

        return {
            key: contract.get(key)
            for key in (
                "schema",
                "projectHash",
                "cpu",
                "timerAreas",
                "ioModules",
                "ioMapping",
                "externalWiringObserved",
                "blockers",
                "offlineCandidateReady",
            )
        }

    @staticmethod
    def _is_hardware_context_binding_precondition(exc: MCPToolRejectedError) -> bool:
        """Allow exactly one known session-local ToolError shape to rebind.

        MCPGateway retains a parsed ToolError envelope.  Lightweight test
        gateways may construct the exception directly, so accept the same
        *complete* JSON envelope after the exact gateway prefix as a fallback.
        No substring matching is allowed at this retry boundary.
        """

        payload = getattr(exc, "mcp_error_payload", None)
        if payload is None:
            prefix = "program.change_target_analysis was rejected by MCP: "
            rendered = str(exc)
            if not rendered.startswith(prefix):
                return False
            try:
                payload = json.loads(rendered[len(prefix) :])
            except json.JSONDecodeError:
                return False
        return bool(
            isinstance(payload, Mapping)
            and set(payload) == {"schema_version", "status", "error"}
            and payload.get("schema_version") == "1.0"
            and payload.get("status") == "error"
            and isinstance(payload.get("error"), Mapping)
            and set(payload["error"]) == {"type", "message"}
            and payload["error"].get("type") == "SessionError"
            and payload["error"].get("message")
            == _HARDWARE_CONTEXT_BINDING_PRECONDITION
        )

    async def _rebind_hardware_context_and_retry_change_analysis(
        self,
        *,
        request: ExternalToolRequestV1,
        expected_session_identity: Mapping[str, Any] | None,
        original_error: MCPToolRejectedError,
    ) -> Any:
        requested_digest = request.arguments.get("hardware_context_digest")
        retained = self._retained_hardware_contexts.get(
            (request.conversation_id, request.run_id), {}
        )
        retained_contract = retained.get(requested_digest) if isinstance(
            requested_digest, str
        ) else None
        if retained_contract is None:
            raise original_error
        expected_project_hash = (
            expected_session_identity.get("project_hash")
            if isinstance(expected_session_identity, Mapping)
            else None
        )
        if (
            not _is_sha256(requested_digest)
            or retained_contract.get("projectHash") != expected_project_hash
        ):
            raise MCPToolError("retained hardware context does not match the Host target")

        rebound_result = await self._gateway.execute_agent_tool(
            "project.hardware_context",
            {},
        )
        rebound_contract = self._hardware_context_contract_from_result(rebound_result)
        rebound_digest = rebound_contract.get("contextDigest")
        if (
            not _is_sha256(rebound_digest)
            or rebound_contract.get("projectHash") != expected_project_hash
            or self._hardware_context_engineering_facts(rebound_contract)
            != self._hardware_context_engineering_facts(retained_contract)
        ):
            raise MCPToolError(
                "rebound hardware context differs from retained Host evidence"
            )
        if self._gateway.agent_session_identity_snapshot != expected_session_identity:
            raise MCPToolError(
                "Host-bound engineering session changed during hardware context rebind"
            )
        self._retain_hardware_context_contract(
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            contract=rebound_contract,
        )
        # The context digest deliberately contains session_id/PID/HWND.  A
        # verified XG5000 reopen therefore produces a new digest even when the
        # exact project and every engineering hardware fact are unchanged.
        # Rebind only that session-local digest after the strict comparison
        # above.  One retry only; any second cache miss or other error escapes.
        rebound_arguments = dict(request.arguments)
        rebound_arguments["hardware_context_digest"] = rebound_digest
        return await self._gateway.execute_agent_tool(
            "program.change_target_analysis",
            rebound_arguments,
        )

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
        evidence_context: dict[str, Any] | None = None
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
            try:
                result = await self._gateway.execute_agent_tool(raw_name, request.arguments)
            except MCPToolRejectedError as exc:
                if (
                    raw_name == "program.change_target_analysis"
                    and self._is_hardware_context_binding_precondition(exc)
                ):
                    result = await self._rebind_hardware_context_and_retry_change_analysis(
                        request=request,
                        expected_session_identity=expected_session_identity,
                        original_error=exc,
                    )
                else:
                    raise
            if raw_name in _WORKSPACE_STATIC_TOOLS:
                expected_workspace = _expected_workspace_static_identity(
                    expected_session_identity
                )
                current_workspace = await self._gateway.call_workspace_tool(
                    "workspace.status",
                    {"workspace_id": expected_workspace["workspace_id"]},
                )
                _validate_workspace_status_lineage(
                    current_workspace,
                    expected_identity=expected_workspace,
                )
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
            if raw_name == "project.hardware_context" and evidence_contract is not None:
                self._retain_hardware_context_contract(
                    conversation_id=request.conversation_id,
                    run_id=request.run_id,
                    contract=evidence_contract,
                )
            if raw_name in {"project.search", "workspace.search"}:
                evidence_context = project_search_context(result)
                if (
                    evidence_contract is None
                    or evidence_context["sourceReceiptDigest"]
                    != evidence_contract.get("receiptDigest")
                ):
                    raise MCPToolError(
                        "Project search context is not bound to its evidence receipt"
                    )
        except MCPTransportError:
            await self._emit_failure(sink, request, "transport")
            raise
        except MCPToolRejectedError as exc:
            logger.warning("Agent tool rejected by MCP (tool=%s): %s", raw_name, exc)
            await self._emit_failure(
                sink,
                request,
                "tool_error",
                failure_reason=str(exc),
            )
            raise
        except MCPToolError as exc:
            logger.warning("Agent tool denied by Host (tool=%s): %s", raw_name, exc)
            await self._emit_failure(
                sink,
                request,
                "host_policy",
                failure_reason=str(exc),
            )
            raise
        except asyncio.CancelledError:
            await self._emit_failure(sink, request, "cancelled")
            raise
        except Exception as exc:
            await self._emit_failure(sink, request, "host_policy")
            raise MCPToolError("Platform execution broker failed") from exc
        ExternalToolResultV1(request_id=request.request_id, tool_name=request.tool_name, status="completed")
        completed_payload = dict(event_payload)
        if evidence_contract is not None:
            completed_payload["evidence_contract"] = evidence_contract
        if evidence_context is not None:
            completed_payload["evidence_context"] = evidence_context
        await self._emit_event(sink, "tool.completed", completed_payload)
        return result

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
        if raw_name == "program.read_ld":
            return cls._program_read_ld_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.compare":
            return cls._workspace_compare_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.hardware_context":
            return cls._workspace_hardware_context_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.structure_index":
            return cls._workspace_structure_index_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "workspace.search":
            return cls._workspace_search_evidence_contract(
                result,
                expected_session_identity=expected_session_identity,
            )
        if raw_name == "project.structure_index":
            return cls._project_structure_index_evidence_contract(
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
    def _program_read_ld_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping) or not isinstance(
            expected_session_identity,
            Mapping,
        ):
            raise MCPToolError("program.read_ld evidence is not target-bound")
        missing_identity = [
            field
            for field in SESSION_IDENTITY_FIELDS
            if field not in expected_session_identity
        ]
        if missing_identity:
            raise MCPToolError("program.read_ld session identity is incomplete")
        session_id = expected_session_identity["session_id"]
        pid = expected_session_identity["pid"]
        hwnd = expected_session_identity["hwnd"]
        project_path = expected_session_identity["project_path"]
        project_hash = expected_session_identity["project_hash"]
        program = result.get("program")
        if (
            result.get("operation") != "program.read_ld"
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("stable_repeat") is not True
            or result.get("project_unchanged") is not True
            or not isinstance(session_id, str)
            or not session_id
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(hwnd, bool)
            or not isinstance(hwnd, int)
            or hwnd <= 0
            or not isinstance(project_path, str)
            or not project_path
            or not _is_sha256(project_hash)
            or result.get("project_hash_before") != project_hash
            or result.get("project_hash_after") != project_hash
            or result.get("project_sha256_before") != project_hash
            or result.get("project_sha256_after") != project_hash
            or not _is_sha256(result.get("payload_sha256"))
            or not _is_sha256(result.get("decoded_program_sha256"))
            or not isinstance(program, Mapping)
            or not isinstance(program.get("program_id"), str)
            or not program.get("program_id")
            or not isinstance(program.get("name"), str)
            or not program.get("name")
        ):
            raise MCPToolError("program.read_ld evidence binding is invalid")
        contract = {
            "schema": "controlstudio-program-read-ld-evidence/v1",
            "operation": "program.read_ld",
            "sessionId": session_id,
            "pid": pid,
            "hwnd": hwnd,
            "projectPath": project_path,
            "projectSha256": project_hash,
            "programId": program["program_id"],
            "programName": program["name"],
            "payloadSha256": result["payload_sha256"],
            "decodedProgramSha256": result["decoded_program_sha256"],
            "verified": True,
            "stableRepeat": True,
            "projectUnchanged": True,
        }
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _project_structure_index_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        session_identity_fields = set(SESSION_IDENTITY_FIELDS)
        allowed_identity_shapes = {
            frozenset(session_identity_fields),
            frozenset(session_identity_fields | set(WORKSPACE_STATIC_IDENTITY_FIELDS)),
        }
        if (
            not isinstance(result, Mapping)
            or set(result) != _PROJECT_STRUCTURE_INDEX_ENVELOPE_FIELDS
            or not isinstance(expected_session_identity, Mapping)
            or frozenset(expected_session_identity) not in allowed_identity_shapes
        ):
            raise MCPToolError("project structure index envelope shape mismatched")
        bound_session_identity = {
            field: expected_session_identity[field] for field in SESSION_IDENTITY_FIELDS
        }
        expected_session_id = bound_session_identity["session_id"]
        expected_project_hash = bound_session_identity["project_hash"]
        structure_index = result.get("structure_index")
        supplied_index_digest = result.get("index_digest")
        receipt_path = result.get("receipt_path")
        operation_id = result.get("operation_id")
        try:
            canonical_operation_id = (
                str(uuid.UUID(operation_id))
                if isinstance(operation_id, str)
                else None
            )
        except ValueError:
            canonical_operation_id = None
        if (
            result.get("operation") != "project.structure_index"
            or result.get("ok") is not True
            or result.get("verified") is not True
            or result.get("project_unchanged") is not True
            or not isinstance(expected_session_id, str)
            or not expected_session_id
            or result.get("session_id") != expected_session_id
            or not _is_sha256(expected_project_hash)
            or result.get("project_hash_before") != expected_project_hash
            or result.get("project_hash_after") != expected_project_hash
            or not _is_sha256(supplied_index_digest)
            or not isinstance(structure_index, Mapping)
            or structure_index.get("index_digest") != supplied_index_digest
            or not isinstance(receipt_path, str)
            or not receipt_path
            or len(receipt_path) > 32_768
            or canonical_operation_id != operation_id
        ):
            raise MCPToolError("project structure index envelope binding is invalid")
        return build_structural_evidence_contract(
            "project.structure_index",
            structure_index,
            expected_session_identity=bound_session_identity,
        )

    @classmethod
    def _workspace_compare_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        comparison = validate_workspace_compare_result(
            result,
            expected_workspace_id=str(expected_identity["workspace_id"]),
            expected_revision_id=str(expected_identity["revision_id"]),
        )
        if (
            comparison.source_kind != expected_identity["source_kind"]
            or comparison.source.sha256 != expected_identity["source_sha256"]
            or comparison.working_copy.sha256 != expected_identity["working_sha256"]
        ):
            raise MCPToolError("workspace comparison lineage changed")
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
        contract["receiptDigest"] = _canonical_digest(contract)
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
            or supplied_digest != _canonical_digest(context)
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
            "projectBindingDigest": _canonical_digest(project_binding),
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
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _workspace_hardware_context_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        expected_workspace_id = expected_identity["workspace_id"]
        expected_revision_id = expected_identity["revision_id"]
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_HARDWARE_CONTEXT_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace hardware context shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_HARDWARE_CONTEXT_FIELDS
            if name != "receipt_digest"
        }
        workspace_id = result.get("workspace_id")
        revision_id = result.get("revision_id")
        source_sha256 = result.get("source_sha256")
        working_sha256 = result.get("working_sha256")
        if (
            result.get("operation") != "workspace.hardware_context"
            or result.get("schema") != "xg5000.workspace-hardware-context/1.0"
            or workspace_id != expected_workspace_id
            or revision_id != expected_revision_id
            or result.get("source_kind") != expected_identity["source_kind"]
            or source_sha256 != expected_identity["source_sha256"]
            or working_sha256 != expected_identity["working_sha256"]
            or not all(_is_sha256(value) for value in (source_sha256, working_sha256))
            or result.get("receipt_digest") != _canonical_digest(unsigned)
        ):
            raise MCPToolError("workspace hardware context binding is invalid")
        hardware_context = result.get("hardware_context")
        if not isinstance(hardware_context, Mapping):
            raise MCPToolError("workspace hardware context is missing")
        context_contract = cls._hardware_context_evidence_contract(hardware_context)
        project_binding = hardware_context.get("project_binding")
        if (
            not isinstance(project_binding, Mapping)
            or project_binding.get("session_id")
            != f"workspace:{workspace_id}:{revision_id}"
            or project_binding.get("pid") != 0
            or project_binding.get("hwnd") != 0
            or project_binding.get("project_hash") != working_sha256
        ):
            raise MCPToolError("workspace hardware project binding is invalid")
        context_projection = scrub_agent_tool_result(
            {
                "cpu": context_contract["cpu"],
                "timerAreas": context_contract["timerAreas"],
                "ioModules": context_contract["ioModules"],
                "ioMapping": context_contract["ioMapping"],
            }
        )
        contract = {
            "schema": "controlstudio-workspace-hardware-context-evidence/v1",
            "workspaceId": workspace_id,
            "revisionId": revision_id,
            "sourceKind": result.get("source_kind"),
            "sourceSha256": source_sha256,
            "workingSha256": working_sha256,
            "contextDigest": context_contract["contextDigest"],
            **context_projection,
            "externalWiringObserved": context_contract["externalWiringObserved"],
            "blockers": context_contract["blockers"],
            "offlineCandidateReady": context_contract["offlineCandidateReady"],
        }
        if contract["sourceKind"] not in {
            "local_file",
            "restored_from_controller",
            "new_build",
        }:
            raise MCPToolError("workspace hardware source kind is invalid")
        contract["receiptDigest"] = _canonical_digest(contract)
        return contract

    @classmethod
    def _workspace_structure_index_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_STRUCTURE_INDEX_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace structure index shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_STRUCTURE_INDEX_FIELDS
            if name != "receipt_digest"
        }
        (
            workspace_id,
            revision_id,
            _source_kind,
            working_path,
            working_sha256,
            _provenance_digest,
        ) = _validated_workspace_static_binding(
            result.get("workspace_binding"),
            expected_identity=expected_identity,
            label="structure",
        )
        if (
            result.get("operation") != "workspace.structure_index"
            or result.get("schema") != "xg5000.workspace-structure-index/1.0"
            or result.get("receipt_digest") != _canonical_digest(unsigned)
        ):
            raise MCPToolError("workspace structure binding is invalid")
        structure_index = result.get("structure_index")
        return build_structural_evidence_contract(
            "project.structure_index",
            structure_index,
            expected_session_identity={
                "session_id": f"workspace:{workspace_id}:{revision_id}",
                "pid": 0,
                "hwnd": 0,
                "project_path": working_path,
                "project_hash": working_sha256,
            },
            allow_workspace_static_identity=True,
        )

    @classmethod
    def _workspace_search_evidence_contract(
        cls,
        result: Any,
        *,
        expected_session_identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        expected_identity = _expected_workspace_static_identity(expected_session_identity)
        if not isinstance(result, Mapping) or set(result) != (
            _WORKSPACE_SEARCH_FIELDS | {"operation", "schema_version"}
        ):
            raise MCPToolError("workspace search shape mismatched")
        unsigned = {
            name: result[name]
            for name in _WORKSPACE_SEARCH_FIELDS
            if name != "receipt_digest"
        }
        (
            _workspace_id,
            _revision_id,
            _source_kind,
            _working_path,
            working_sha256,
            _provenance_digest,
        ) = _validated_workspace_static_binding(
            result.get("workspace_binding"),
            expected_identity=expected_identity,
            label="search",
        )
        project_search = result.get("project_search")
        if (
            result.get("operation") != "workspace.search"
            or result.get("schema") != "xg5000.workspace-search/1.0"
            or result.get("receipt_digest") != _canonical_digest(unsigned)
            or not isinstance(project_search, Mapping)
            or project_search.get("project_hash_before") != working_sha256
            or project_search.get("project_hash_after") != working_sha256
        ):
            raise MCPToolError("workspace search binding is invalid")
        return build_structural_evidence_contract("project.search", project_search)

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
            or supplied_digest != _canonical_digest(canonical_analysis)
        ):
            raise MCPToolError("change target analysis receipt digest is invalid")
        target = analysis.get("target")
        current_semantics = analysis.get("current_semantics")
        proposed_semantics = analysis.get("proposed_semantics")
        impact = analysis.get("impact")
        project_binding = analysis.get("project_binding")
        hardware_context_binding = analysis.get("hardware_context_binding")
        blockers = analysis.get("blocking_reasons")
        runtime_deployment_ready = analysis.get("runtime_deployment_ready")
        runtime_deployment_blockers = analysis.get(
            "runtime_deployment_blocking_reasons"
        )
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
        if analysis.get("schema") != "xg5000.program.change-target-analysis/1.1":
            raise MCPToolError("change target analysis schema is unsupported")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item for item in blockers
        ):
            raise MCPToolError("change target blockers are invalid")
        if (
            not isinstance(runtime_deployment_ready, bool)
            or runtime_deployment_ready is not False
            or not isinstance(runtime_deployment_blockers, list)
            or not runtime_deployment_blockers
            or any(
                not isinstance(item, str) or not item
                for item in runtime_deployment_blockers
            )
            or len(set(runtime_deployment_blockers)) != len(runtime_deployment_blockers)
            or "online_runtime_deployment_unsupported"
            not in runtime_deployment_blockers
        ):
            raise MCPToolError("change target runtime deployment boundary is invalid")
        offline_candidate_ready = analysis.get("offline_candidate_ready")
        if (
            not isinstance(offline_candidate_ready, bool)
            or offline_candidate_ready != (not blockers)
        ):
            raise MCPToolError("change target readiness and blockers are inconsistent")
        assert isinstance(target, Mapping)
        assert isinstance(current_semantics, Mapping)
        assert isinstance(impact, Mapping)
        assert isinstance(project_binding, Mapping)
        physical_io_candidates = impact.get("physical_io_candidates")
        physical_io_hardware_bindings = impact.get("physical_io_hardware_bindings")
        if (
            not isinstance(physical_io_candidates, list)
            or any(not isinstance(item, str) or not item for item in physical_io_candidates)
            or len(set(physical_io_candidates)) != len(physical_io_candidates)
            or not isinstance(physical_io_hardware_bindings, list)
            or any(not isinstance(item, Mapping) for item in physical_io_hardware_bindings)
        ):
            raise MCPToolError("change target physical I/O binding evidence is invalid")
        candidate_devices = set(physical_io_candidates)
        observed_devices: set[str] = set()
        unresolved_devices: set[str] = set()
        field_io_bindings: list[dict[str, Any]] = []
        for item in physical_io_hardware_bindings:
            assert isinstance(item, Mapping)
            device = item.get("device")
            binding_status = item.get("binding_status")
            if (
                not isinstance(device, str)
                or not device
                or device not in candidate_devices
                or device in observed_devices
                or binding_status not in {"bound", "unresolved", "ambiguous"}
            ):
                raise MCPToolError("change target physical I/O status evidence is invalid")
            observed_devices.add(device)
            binding = item.get("field_io_binding")
            if binding_status != "bound":
                if binding is not None:
                    raise MCPToolError("unresolved physical I/O cannot contain a field binding")
                if binding_status == "unresolved" and (
                    not isinstance(item.get("reason"), str) or not item.get("reason")
                ):
                    raise MCPToolError("unresolved physical I/O omitted its reason")
                expected_directions = item.get("expected_directions")
                if binding_status == "ambiguous" and (
                    not isinstance(expected_directions, list)
                    or len(expected_directions) < 2
                    or any(
                        not isinstance(direction, str) or not direction
                        for direction in expected_directions
                    )
                ):
                    raise MCPToolError("ambiguous physical I/O directions are invalid")
                unresolved_devices.add(device)
                continue
            if not isinstance(binding, Mapping):
                raise MCPToolError("bound physical I/O omitted its field binding")
            binding_digest = binding.get("binding_digest")
            unsigned_binding = {
                key: value for key, value in binding.items() if key != "binding_digest"
            }
            if (
                binding.get("schema") != "xg5000.project.field-io-binding/1.0"
                or binding.get("device") != device
                or not isinstance(binding_digest, str)
                or binding_digest != _canonical_digest(unsigned_binding)
            ):
                raise MCPToolError("change target field I/O binding is invalid")
            # The Agent needs typed terminal identity, not a project path or
            # raw XML/profile source material.
            field_io_bindings.append(
                {
                    "device": binding.get("device"),
                    "direction": binding.get("direction"),
                    "pointKind": binding.get("point_kind"),
                    "module": binding.get("module"),
                    "channel": binding.get("channel"),
                    "terminalRange": binding.get("terminal_range"),
                    "proposalScope": binding.get("proposal_scope"),
                    "bindingDigest": binding.get("binding_digest"),
                }
            )
        if observed_devices != candidate_devices:
            raise MCPToolError(
                "every physical I/O candidate must have one typed binding status"
            )
        unresolved_blocker_prefixes = (
            "device_not_observed",
            "io_ld_role_ambiguous",
            "io_module_rack_slot_channel_unresolved",
        )
        for device in unresolved_devices:
            if not any(
                f"{prefix}:{device}" in runtime_deployment_blockers
                for prefix in unresolved_blocker_prefixes
            ):
                raise MCPToolError(
                    "unresolved physical I/O candidate has no explicit runtime blocker"
                )
        contract: dict[str, Any] = {
            "schema": "controlstudio-change-target-evidence/v2",
            "analysisDigest": supplied_digest,
            "projectBindingDigest": _canonical_digest(project_binding),
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
            "physicalIoCandidates": list(physical_io_candidates),
            "physicalIoBindings": field_io_bindings,
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
            "offlineCandidateReady": offline_candidate_ready,
            "blockingReasons": list(blockers),
            "runtimeDeploymentReady": runtime_deployment_ready,
            "runtimeDeploymentBlockingReasons": list(
                runtime_deployment_blockers
            ),
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
        contract["receiptDigest"] = _canonical_digest(contract)
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
        hardware_context_digest = result.get("hardware_context_digest")
        field_io_bindings = result.get("field_io_bindings")
        if hardware_context_digest is not None or field_io_bindings:
            if (
                not isinstance(hardware_context_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", hardware_context_digest) is None
                or not isinstance(field_io_bindings, list)
            ):
                raise MCPToolError("MNE patch physical I/O binding is invalid")
            projected: list[dict[str, Any]] = []
            for binding in field_io_bindings:
                if (
                    not isinstance(binding, Mapping)
                    or binding.get("schema")
                    != "xg5000.project.field-io-binding/1.0"
                    or not isinstance(binding.get("binding_digest"), str)
                    or len(str(binding["binding_digest"])) != 64
                ):
                    raise MCPToolError("MNE patch field I/O terminal binding is invalid")
                projected.append(
                    {
                        "device": binding.get("device"),
                        "direction": binding.get("direction"),
                        "pointKind": binding.get("point_kind"),
                        "module": binding.get("module"),
                        "channel": binding.get("channel"),
                        "terminalRange": {
                            key: binding.get("terminal_range", {}).get(key)
                            for key in (
                                "address_start",
                                "address_end",
                                "binding_kind",
                                "status",
                            )
                        }
                        if isinstance(binding.get("terminal_range"), Mapping)
                        else None,
                        "bindingDigest": binding.get("binding_digest"),
                    }
                )
            contract["hardwareContextDigest"] = hardware_context_digest
            contract["fieldIoBindings"] = projected
            candidate_bindings = candidate.get("field_io_bindings")
            if (
                candidate.get("hardware_context_digest") != hardware_context_digest
                or not isinstance(candidate_bindings, list)
                or [
                    {
                        "device": item.get("device"),
                        "direction": item.get("direction"),
                        "point_kind": item.get("point_kind"),
                        "module": item.get("module"),
                        "channel": item.get("channel"),
                        "terminal_range": item.get("terminal_range"),
                        "binding_digest": item.get("binding_digest"),
                    }
                    for item in candidate_bindings
                ]
                != [
                    {
                        "device": item.get("device"),
                        "direction": item.get("direction"),
                        "point_kind": item.get("pointKind"),
                        "module": item.get("module"),
                        "channel": item.get("channel"),
                        "terminal_range": item.get("terminalRange"),
                        "binding_digest": item.get("bindingDigest"),
                    }
                    for item in projected
                ]
            ):
                raise MCPToolError(
                    "MNE patch candidate hardware binding differs from compiler evidence"
                )
        elif candidate.get("hardware_context_digest") is not None or candidate.get(
            "field_io_bindings"
        ):
            raise MCPToolError("MNE patch candidate has an unproven hardware binding")
        contract["receiptDigest"] = _canonical_digest(contract)
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
            # Agent snapshots are read-only evidence.  The Host-owned change
            # executor alone can request a private MNE snapshot or bind it to a
            # durable transaction after explicit approval.
            blocked.update(
                {
                    "workspace_id",
                    "begin_change_transaction",
                    "plan_kind",
                    "host_transaction_id",
                }
            )
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
        self,
        sink: ToolEventSink,
        request: ExternalToolRequestV1,
        failure_kind: str,
        *,
        failure_reason: str | None = None,
    ) -> None:
        ExternalToolResultV1(
            request_id=request.request_id,
            tool_name=request.tool_name,
            status="failed",
            failure_kind=failure_kind,
        )
        payload = {**self._event_payload(request), "failure_kind": failure_kind}
        if failure_reason is not None:
            # MCP/Host errors are framework-owned diagnostics. Keep only one
            # bounded line; never include request arguments or tool results.
            bounded_reason = " ".join(failure_reason.split())[:1_000]
            if bounded_reason:
                payload["failure_reason"] = bounded_reason
        await self._emit_event(sink, "tool.failed", payload)

    @staticmethod
    async def _emit_event(sink: ToolEventSink, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await sink(event_type, payload)
        except Exception:
            logger.exception("Agent tool progress sink failed (event_type=%s)", event_type)
