"""Host-side supervisor for the versioned Pydantic AI Agent sidecar."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from controlstudio_contracts import ExternalToolRequestV1
from pydantic import ValidationError

from .agent_protocol import (
    MAX_PROTOCOL_LINE_BYTES,
    AgentProtocolError,
    AgentProtocolMessage,
    assert_agent_safe_payload,
    decode_message,
    encode_message,
    parse_manifest,
    scrub_agent_tool_result,
)
from .agent_runtime import (
    MAX_ENGINEERING_AUTONOMOUS_CYCLES,
    AgentOutputContractDiagnostic,
    AgentOutputContractError,
    AgentOutputValidationIssue,
    AgentRunOutput,
    AgentRuntimeDiagnostic,
    AgentRuntimeError,
    ChangePlanCallback,
    MissionCheckpointCallback,
    ModelCapabilityResult,
    ProgressCallback,
    TokenCallback,
    ToolEventCallback,
)
from .api_models import OfflineOperandMissionV1
from .execution_broker import ExecutionBroker
from .mcp_client import (
    AGENT_TOOL_NAMES,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
)
from .model_connection import StoredModelConnection
from .models import Message, MessageRole
from .native_engineering_status import validate_native_engineering_status
from .structural_evidence import (
    STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
    STRUCTURAL_ANALYSIS_TOOL_COVERAGE,
    canonical_digest,
    logic_graph_context,
    project_search_context,
    state_model_context,
    structural_observation_statement,
    structural_receipt_tool_name,
    validate_project_search_context,
    validate_structural_evidence_contract,
)

logger = logging.getLogger(__name__)

_HISTORICAL_EVIDENCE_BUNDLE_KEYS = {
    "schemaVersion",
    "missionDigest",
    "items",
    "bundleDigest",
}
_HISTORICAL_EVIDENCE_ITEM_KEYS = {"sourceRunId", "evidence"}
_MAX_HISTORICAL_EVIDENCE_ITEMS = 2_500
_MAX_HISTORICAL_EVIDENCE_BUNDLE_BYTES = 1_500_000
_MAX_AGENT_CHECKPOINT_BYTES = 1_500_000
_DURABLE_RESUME_SYNC_CAPABILITY = "durable-resume-sync-v1"

# The exact typed operand route is Host-selected from a durable mission rather
# than inferred from conversational prose.  Its sidecar finalization is fully
# deterministic after an approval-only proposal succeeds.  Keep this wire
# prompt ASCII-only: older sidecars use the prompt's script to select a
# user-prose validator, while their deterministic approval message is English.
# The original objective remains in the signed Host mission snapshot.
_EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT = (
    "Continue the exact Host-bound offline operand mission. The trusted mission "
    "snapshot is authoritative. Use no target outside that snapshot. Any change "
    "proposal remains approval-only; do not mutate a project, controller, or "
    "runtime state."
)

_SIDECAR_STRIPPED_ENVIRONMENT = frozenset(
    {
        "CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET",
        "CONTROLSTUDIO_APPROVAL_SECRET",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _exact_offline_operand_continuation_prompt(
    trusted_context: Mapping[str, Any],
) -> str | None:
    """Return the compatibility prompt only for a bound active exact route.

    This does not alter the durable mission, tool catalog, approval authority,
    or final-output validation.  It is deliberately unavailable before the
    Host has supplied a typed operand mission and an existing mission snapshot.
    """

    if trusted_context.get("workflow") != "engineering":
        return None
    try:
        operand_mission = OfflineOperandMissionV1.model_validate(
            trusted_context.get("offline_operand_mission")
        )
    except (TypeError, ValueError):
        return None
    mission = trusted_context.get("engineering_mission")
    if not isinstance(mission, dict) or mission.get("schemaVersion") != "1":
        return None
    if mission.get("status") != "active":
        return None
    objective = mission.get("objective")
    steps = mission.get("steps")
    if (
        not isinstance(objective, str)
        or not isinstance(steps, list)
        or not all(
            value in objective
            for value in (
                operand_mission.program_name,
                operand_mission.expected_operand,
                operand_mission.replacement_operand,
            )
        )
        or mission.get("activeStepId") != "propose_offline_change"
        or not any(
            isinstance(step, dict)
            and step.get("stepId") == "propose_offline_change"
            and step.get("status") == "active"
            for step in steps
        )
    ):
        return None
    return _EXACT_OFFLINE_OPERAND_CONTINUATION_PROMPT


def validate_historical_evidence_bundle(
    value: object,
    *,
    mission_digest: str | None = None,
    expected_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate the exact Host-owned projection of mission-bound old evidence."""

    if not isinstance(value, dict) or set(value) != _HISTORICAL_EVIDENCE_BUNDLE_KEYS:
        raise AgentProtocolError("historical evidence bundle fields are invalid")
    items = value.get("items")
    bound_mission_digest = value.get("missionDigest")
    bundle_digest = value.get("bundleDigest")
    if (
        value.get("schemaVersion") != "1"
        or not isinstance(bound_mission_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", bound_mission_digest) is None
        or (mission_digest is not None and bound_mission_digest != mission_digest)
        or not isinstance(bundle_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", bundle_digest) is None
        or not isinstance(items, list)
        or len(items) > _MAX_HISTORICAL_EVIDENCE_ITEMS
    ):
        raise AgentProtocolError("historical evidence bundle binding is invalid")
    evidence_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != _HISTORICAL_EVIDENCE_ITEM_KEYS:
            raise AgentProtocolError("historical evidence item fields are invalid")
        source_run_id = item.get("sourceRunId")
        evidence = item.get("evidence")
        evidence_id = evidence.get("evidenceId") if isinstance(evidence, dict) else None
        tool_name = evidence.get("toolName") if isinstance(evidence, dict) else None
        try:
            canonical_evidence_id = (
                str(uuid.UUID(evidence_id)) if isinstance(evidence_id, str) else None
            )
        except ValueError:
            canonical_evidence_id = None
        if (
            not isinstance(source_run_id, str)
            or not source_run_id
            or len(source_run_id) > 256
            or canonical_evidence_id != evidence_id
            or not isinstance(tool_name, str)
            or not tool_name
        ):
            raise AgentProtocolError("historical evidence item is invalid")
        assert_agent_safe_payload(evidence)
        evidence_ids.append(evidence_id)
    if evidence_ids != sorted(evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
        raise AgentProtocolError("historical evidence ids are not canonical and unique")
    if expected_evidence_ids is not None and set(evidence_ids) != expected_evidence_ids:
        raise AgentProtocolError("historical evidence does not match the durable mission")
    encoded_bundle = _canonical_json(value).encode("utf-8")
    if len(encoded_bundle) > _MAX_HISTORICAL_EVIDENCE_BUNDLE_BYTES:
        raise AgentProtocolError("historical evidence bundle exceeds its durable bound")
    unsigned = {key: value[key] for key in value if key != "bundleDigest"}
    actual_digest = hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_digest, bundle_digest):
        raise AgentProtocolError("historical evidence bundle digest mismatched")
    return json.loads(_canonical_json(value))


def build_historical_evidence_bundle(
    *,
    mission_digest: str,
    evidence: Mapping[str, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Build one deterministic bundle from exact durable journal rows."""

    unsigned = {
        "schemaVersion": "1",
        "missionDigest": mission_digest,
        "items": [
            {
                "sourceRunId": source_run_id,
                "evidence": payload,
            }
            for evidence_id in sorted(evidence)
            for source_run_id, payload in [evidence[evidence_id]]
        ],
    }
    bundle = {
        **unsigned,
        "bundleDigest": hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest(),
    }
    return validate_historical_evidence_bundle(
        bundle,
        mission_digest=mission_digest,
        expected_evidence_ids=set(evidence),
    )
_STRUCTURAL_ANALYSIS_TOOL_ALIASES = {
    alias: raw_name
    for alias, raw_name in AGENT_TOOL_NAMES.items()
    if raw_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE
}
_MNE_CANDIDATE_TOOL_ALIASES = frozenset(
    {
        "compile_instruction_batch_patch_candidate",
        "compile_instruction_patch_candidate",
        "compile_rung_patch_candidate",
        "compile_series_guard_candidate",
    }
)


def _safe_mne_candidate_rejection_error(
    request: ExternalToolRequestV1,
    error: MCPToolRejectedError,
) -> dict[str, Any] | None:
    """Return one MCP-validated compiler rejection for the Agent failure frame."""

    if request.tool_name not in _MNE_CANDIDATE_TOOL_ALIASES:
        return None
    payload = error.mcp_error_payload
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "status", "error"}
        or payload.get("schema_version") != "1.0"
        or payload.get("status") != "error"
        or not isinstance(payload.get("error"), dict)
        or set(payload["error"]) != {"type", "message"}
        or not isinstance(payload["error"].get("type"), str)
        or not isinstance(payload["error"].get("message"), str)
    ):
        return None
    assert_agent_safe_payload(payload)
    return payload
_STRUCTURAL_TOOL_RESULT_PROJECTION_SCHEMA = (
    "controlstudio-structural-analysis-tool-result-projection/v1"
)
_PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA = (
    "controlstudio-project-structure-evidence-projection/v1"
)
_STRUCTURAL_TOOL_RESULT_FRAME_MAX_BYTES = min(
    512_000,
    MAX_PROTOCOL_LINE_BYTES - 1,
)
_STRUCTURAL_TOOL_RESULT_GUIDANCE = (
    "The raw MCP result was intentionally omitted. Use "
    "_controlstudio_evidence.receipt as the authoritative source for canonical "
    "structural facts, scope, coverage, and evidence citation. modelContext, when "
    "present, is a bounded Host-revalidated interpretation for inferences only; "
    "do not copy it into observations."
)
_OUTPUT_CONTRACT_STAGES = frozenset(
    {
        "starting",
        "engineering_analysis",
        "planning_narrative",
        "planning_native_design",
        "planning_finalize",
    }
)
_OUTPUT_CONTRACT_EXCEPTION_KINDS = frozenset(
    {
        "model_retry",
        "unexpected_model_behavior",
        "incomplete_tool_call",
    }
)
_OUTPUT_CONTRACT_REASON_CODES = frozenset(
    {
        "validator_rejected",
        "schema_validation",
        "incomplete_output",
        "unexpected_model_output",
    }
)
_RUNTIME_EXCEPTION_KINDS = frozenset(
    {
        "usage_limit_exceeded",
        "model_transport",
        "model_api_error",
        "tool_failure",
        "runtime_error",
    }
)
_RUNTIME_REASON_CODES = frozenset(
    {
        "tool_call_budget",
        "request_budget",
        "token_budget",
        "runtime_budget",
        "provider_timeout",
        "provider_connection",
        "provider_rejected",
        "tool_error",
        "host_policy",
        "transport",
        "cancelled",
        "runtime_invariant",
    }
)
_CHANGE_OUTCOME_KEYS = frozenset(
    {
        "schemaVersion",
        "transactionId",
        "planDigest",
        "state",
        "disposition",
        "evidenceId",
        "outcomeDigest",
        "verificationEvidenceDigest",
        "errorCode",
    }
)
_CHANGE_OUTCOME_DISPOSITIONS = {
    "COMPLETED": "applied",
    "ROLLED_BACK": "rolled_back",
    "FAILED": "failed",
    "REJECTED": "rejected",
    "EXPIRED": "expired",
}
_OUTPUT_CONTRACT_VALIDATOR_CODES = frozenset(
    {
        "not_applicable",
        "shortened_knowledge_reference",
        "unreturned_knowledge_reference",
        "invented_controller",
        "invented_device_address",
        "invented_contact_polarity",
        "invalid_collection_evidence",
        "structured_output_contract",
        "native_collection_evidence",
        "native_collection_confirmed_empty",
        "native_collection_not_applicable_nonempty",
        "native_collection_scope",
        "native_recovery_scope",
        "native_safety_scope",
        "native_io_safe_state",
        "native_duplicate_identity",
        "native_unknown_reference",
        "native_condition_invalid",
        "native_sequence_structure",
        "native_output_network_coverage",
        "native_output_safety_coverage",
        "native_ladder_shape",
        "native_ladder_unknown_reference",
        "native_ladder_invalid_target",
        "native_ladder_action_universe",
        "native_ladder_out_guarantee",
        "native_ladder_execution_guarantee",
        "native_configuration",
        "native_attempt_status",
        "continuation_approval",
        "continuation_citation",
        "continuation_contract",
        "continuation_state_transition",
        "continuation_mission_anchor",
        "continuation_evidence_completion",
        "continuation_evidence_coverage",
        "continuation_mission_binding",
        "continuation_no_progress",
        "continuation_structural_binding",
    }
)
_VALIDATION_LOCATION_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-[],"
)


class AgentSidecarError(RuntimeError):
    """A sanitized failure reported by the supervised Agent process."""


class AgentSidecarCrashed(AgentSidecarError):
    """The sidecar exited or violated the protocol while a Host operation ran."""


class AgentSupervisor:
    """Own the child lifecycle and its stdout-only NDJSON protocol.

    The command is intentionally fixed by default.  Neither the MCP process nor
    its authorization secret is inherited as an explicit command argument.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        startup_timeout_seconds: float = 30.0,
        shutdown_timeout_seconds: float = 3.0,
        message_timeout_seconds: float = 600.0,
        pipe_drain_timeout_seconds: float = 5.0,
    ) -> None:
        # A shipped Platform resolves the installed private artifact from PATH
        # or an explicitly injected immutable artifact command. It never
        # discovers a checkout-relative or sibling Agent source tree.
        configured_command = _configured_agent_command()
        if command is not None:
            self._command = tuple(command)
        elif configured_command is not None:
            self._command = configured_command
        elif development_command := _monorepo_agent_command():
            self._command = development_command
        else:
            self._command = ("controlstudio-agent",)
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._message_timeout_seconds = message_timeout_seconds
        self._pipe_drain_timeout_seconds = pipe_drain_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._messages: asyncio.Queue[AgentProtocolMessage | None] = asyncio.Queue()
        self._lifecycle_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._manifest_agent_id: str | None = None
        self._manifest_capabilities: frozenset[str] = frozenset()
        self._active_run_id: str | None = None

    @property
    def manifest_agent_id(self) -> str | None:
        return self._manifest_agent_id

    async def ensure_started(self) -> None:
        async with self._lifecycle_lock:
            if self._process is not None and self._process.returncode is None:
                return
            await self._discard_dead_process()
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_sidecar_environment(),
                    limit=MAX_PROTOCOL_LINE_BYTES + 1,
                )
            except OSError as exc:
                raise AgentSidecarCrashed("Agent sidecar could not be started") from exc
            self._process = process
            messages: asyncio.Queue[AgentProtocolMessage | None] = asyncio.Queue()
            self._messages = messages
            self._stdout_task = asyncio.create_task(
                self._read_stdout(process, messages),
                name="controlstudio-agent-sidecar-stdout",
            )
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(process), name="controlstudio-agent-sidecar-stderr"
            )
            try:
                initialized = await asyncio.wait_for(
                    self._request({"type": "initialize"}, expected="initialized"),
                    timeout=self._startup_timeout_seconds,
                )
                manifest_payload = initialized.payload.get("manifest")
                if not isinstance(manifest_payload, Mapping):
                    raise AgentProtocolError("Agent initialization omitted a manifest")
                manifest = parse_manifest(manifest_payload)
                self._manifest_agent_id = manifest.agent_id
                self._manifest_capabilities = frozenset(manifest.capabilities)
                health = await asyncio.wait_for(
                    self._request({"type": "health"}, expected="health.result"),
                    timeout=self._startup_timeout_seconds,
                )
                if health.payload.get("status") != "ready":
                    raise AgentProtocolError("Agent sidecar health check failed")
            except BaseException as exc:
                await self._terminate_process()
                if isinstance(exc, AgentSidecarError):
                    raise
                raise AgentSidecarCrashed("Agent sidecar handshake failed") from exc

    async def run(
        self,
        payload: Mapping[str, Any],
        *,
        on_message: Callable[[AgentProtocolMessage], Awaitable[None]],
        historical_evidence: Mapping[str, Any] | None = None,
        agent_checkpoint: Mapping[str, Any] | None = None,
    ) -> None:
        """Run one supervised Agent operation until its terminal sidecar frame."""

        async with self._run_lock:
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise AgentProtocolError("Agent run id is invalid")
            try:
                await self.ensure_started()
                self._active_run_id = run_id
                if (
                    historical_evidence is not None or agent_checkpoint is not None
                ) and (
                    _DURABLE_RESUME_SYNC_CAPABILITY
                    not in self._manifest_capabilities
                ):
                    raise AgentProtocolError(
                        "Agent sidecar does not support durable resume sync"
                    )
                if historical_evidence is not None:
                    await self._sync_historical_evidence_locked(
                        historical_evidence
                    )
                if agent_checkpoint is not None:
                    await self._sync_agent_checkpoint_locked(agent_checkpoint)
                await self._send("run.start", payload)
                while True:
                    message = await self._next_message_with_timeout("run response")
                    if message.type == "run.started":
                        continue
                    await on_message(message)
                    if message.type in {"run.final", "run.error", "run.cancelled"}:
                        return
            except BaseException:
                # A callback may have durably rejected a checkpoint or failed
                # while dispatching a tool. Never let frames from that run
                # remain queued for the next operation: the next request must
                # handshake with a fresh sidecar and a fresh message queue.
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise
            finally:
                if self._active_run_id == run_id:
                    self._active_run_id = None

    async def _sync_historical_evidence_locked(
        self,
        bundle: Mapping[str, Any],
    ) -> None:
        """Sync one exact bundle while the caller holds the run lock."""

        bundle_digest = bundle.get("bundleDigest")
        mission_digest = bundle.get("missionDigest")
        items = bundle.get("items")
        if (
            not isinstance(bundle_digest, str)
            or not isinstance(mission_digest, str)
            or not isinstance(items, list)
        ):
            raise AgentProtocolError("historical evidence sync bundle is invalid")
        ready = await self._request(
            {
                "type": "historical_evidence.sync.begin",
                "payload": {
                    "schemaVersion": "1",
                    "missionDigest": mission_digest,
                    "bundleDigest": bundle_digest,
                    "itemCount": len(items),
                },
            },
            expected="historical_evidence.sync.ready",
        )
        if ready.payload.get("bundleDigest") != bundle_digest:
            raise AgentProtocolError("historical evidence sync ready digest mismatched")
        for index, item in enumerate(items):
            accepted = await self._request(
                {
                    "type": "historical_evidence.sync.item",
                    "payload": {
                        "bundleDigest": bundle_digest,
                        "index": index,
                        "item": item,
                    },
                },
                expected="historical_evidence.sync.accepted",
            )
            if (
                accepted.payload.get("bundleDigest") != bundle_digest
                or accepted.payload.get("index") != index
            ):
                raise AgentProtocolError(
                    "historical evidence sync item acknowledgement mismatched"
                )
        synced = await self._request(
            {
                "type": "historical_evidence.sync.commit",
                "payload": {"bundleDigest": bundle_digest},
            },
            expected="historical_evidence.synced",
        )
        if synced.payload != {
            "missionDigest": mission_digest,
            "bundleDigest": bundle_digest,
            "itemCount": len(items),
        }:
            raise AgentProtocolError("historical evidence sync acknowledgement is invalid")

    async def _sync_agent_checkpoint_locked(
        self,
        checkpoint: Mapping[str, Any],
    ) -> None:
        """Sync the bounded checkpoint outside the aggregate run.start frame."""

        canonical_checkpoint = json.loads(_canonical_json(checkpoint))
        assert_agent_safe_payload(canonical_checkpoint)
        encoded = _canonical_json(canonical_checkpoint).encode("utf-8")
        if len(encoded) > _MAX_AGENT_CHECKPOINT_BYTES:
            raise AgentProtocolError("Agent checkpoint exceeds its durable bound")
        checkpoint_digest = hashlib.sha256(encoded).hexdigest()
        synced = await self._request(
            {
                "type": "engineering_checkpoint.sync",
                "payload": {
                    "checkpoint": canonical_checkpoint,
                    "checkpointDigest": checkpoint_digest,
                },
            },
            expected="engineering_checkpoint.synced",
        )
        if synced.payload != {"checkpointDigest": checkpoint_digest}:
            raise AgentProtocolError("Agent checkpoint sync acknowledgement is invalid")

    async def send_tool_result(self, payload: Mapping[str, Any]) -> None:
        await self._send("tool.result", payload)

    async def sync_knowledge(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """Replace the Agent's disposable derived source/index snapshot."""

        async with self._run_lock:
            try:
                await self.ensure_started()
                await self._send("knowledge.sync", {"documents": list(documents)})
                response = await self._next_message_with_timeout("knowledge sync")
                if response.type != "knowledge.synced":
                    raise AgentProtocolError(
                        "Agent sidecar rejected knowledge synchronization"
                    )
                count = response.payload.get("count")
                if not isinstance(count, int) or count < 0:
                    raise AgentProtocolError(
                        "Agent knowledge synchronization result is invalid"
                    )
                return count
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def search_knowledge(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ask the Agent sidecar to rank its disposable derived index."""

        async with self._run_lock:
            try:
                await self.ensure_started()
                await self._send("knowledge.search", payload)
                response = await self._next_message_with_timeout("knowledge search")
                if response.type != "knowledge.search.result":
                    raise AgentProtocolError("Agent sidecar rejected knowledge search")
                results = response.payload.get("results")
                if not isinstance(results, list):
                    raise AgentProtocolError("Agent knowledge search result is invalid")
                return dict(response.payload)
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def cancel_run(self, run_id: str) -> None:
        process = self._process
        if (
            process is None
            or process.returncode is not None
            or self._active_run_id != run_id
        ):
            return
        try:
            await self._send("run.cancel", {"run_id": run_id})
        except AgentSidecarError:
            # The caller is already cancelling; a child crash cannot make a
            # Host cancellation fail or turn into a different run result.
            return

    async def test_capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async with self._run_lock:
            try:
                await self.ensure_started()
                await self._send("capabilities.test", payload)
                while True:
                    message = await self._next_message_with_timeout(
                        "capability response"
                    )
                    if message.type == "capabilities.result":
                        return message.payload
                    if message.type == "protocol.error":
                        raise AgentSidecarError(
                            "Agent sidecar rejected the capability request"
                        )
            except BaseException:
                async with self._lifecycle_lock:
                    await self._terminate_process()
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            process = self._process
            if process is None:
                return
            if process.returncode is None:
                try:
                    await self._send("shutdown", {})
                    while True:
                        response = await self._next_message_with_timeout(
                            "shutdown response",
                            timeout_seconds=self._shutdown_timeout_seconds,
                        )
                        if response.type == "shutdown.complete":
                            break
                except (AgentSidecarError, asyncio.TimeoutError):
                    pass
            await self._terminate_process()

    async def _request(
        self,
        command: Mapping[str, Any],
        *,
        expected: str,
    ) -> AgentProtocolMessage:
        message_type = command.get("type")
        if not isinstance(message_type, str):
            raise AgentProtocolError("Agent command type is invalid")
        await self._send(message_type, command.get("payload", {}))
        while True:
            response = await self._next_message_with_timeout(
                f"{message_type} response"
            )
            if response.type == expected:
                return response
            if response.type == "protocol.error":
                raise AgentProtocolError("Agent sidecar rejected a protocol command")

    async def _send(self, message_type: str, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise AgentSidecarCrashed("Agent sidecar is not running")
        try:
            encoded = encode_message(message_type, payload)
            async with self._write_lock:
                process.stdin.write(encoded)
                await asyncio.wait_for(
                    process.stdin.drain(), timeout=self._pipe_drain_timeout_seconds
                )
        except (BrokenPipeError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            await self._terminate_process()
            raise AgentSidecarCrashed("Agent sidecar pipe closed") from exc

    async def _next_message(self) -> AgentProtocolMessage:
        message = await self._messages.get()
        if message is None:
            await self._terminate_process()
            raise AgentSidecarCrashed("Agent sidecar exited unexpectedly")
        if message.type == "protocol.error":
            raise AgentSidecarError("Agent sidecar reported a protocol error")
        return message

    async def _next_message_with_timeout(
        self,
        operation: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AgentProtocolMessage:
        try:
            return await asyncio.wait_for(
                self._next_message(),
                timeout=(
                    self._message_timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            )
        except asyncio.TimeoutError as exc:
            await self._terminate_process()
            raise AgentSidecarCrashed(f"Agent sidecar timed out waiting for {operation}") from exc

    async def _read_stdout(
        self,
        process: asyncio.subprocess.Process,
        messages: asyncio.Queue[AgentProtocolMessage | None],
    ) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = decode_message(line)
                except AgentProtocolError:
                    logger.warning("Agent sidecar emitted an invalid stdout frame")
                    break
                await messages.put(message)
        finally:
            await messages.put(None)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        # stderr is allowed for diagnostics, but do not mirror arbitrary child
        # text into Host logs where endpoint data or credentials could spread.
        emitted = False
        while await process.stderr.readline():
            emitted = True
        if emitted:
            logger.warning("Agent sidecar emitted diagnostics on stderr")

    async def _discard_dead_process(self) -> None:
        if self._process is not None:
            await self._terminate_process()

    async def _terminate_process(self) -> None:
        process = self._process
        stdout_task = self._stdout_task
        stderr_task = self._stderr_task
        self._process = None
        self._stdout_task = None
        self._stderr_task = None
        self._manifest_agent_id = None
        self._manifest_capabilities = frozenset()
        self._active_run_id = None
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    # Windows can report the timeout just as the child exits.
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout_seconds)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        # The transport already observed the child exit.
                        pass
                    await process.wait()
        tasks = [task for task in (stdout_task, stderr_task) if task is not None]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


class SubprocessAgentClient:
    """Production Agent runtime adapter; Host retains all external authority."""

    def __init__(
        self,
        settings: StoredModelConnection,
        broker: ExecutionBroker,
        supervisor: AgentSupervisor,
    ) -> None:
        self._settings = settings
        self._broker = broker
        self._supervisor = supervisor
        self._change_plan_callback: ChangePlanCallback | None = None
        self._knowledge_snapshot_callback: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None

    def bind_change_planner(self, callback: ChangePlanCallback) -> None:
        self._change_plan_callback = callback

    def bind_knowledge_snapshot(
        self, callback: Callable[[], Awaitable[list[dict[str, Any]]]]
    ) -> None:
        self._knowledge_snapshot_callback = callback

    async def run(
        self,
        *,
        prompt: str,
        history: Sequence[Message],
        conversation_id: str,
        run_id: str,
        trusted_context: dict[str, Any] | None = None,
        agent_checkpoint: dict[str, Any] | None = None,
        on_token: TokenCallback | None = None,
        on_tool_event: ToolEventCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_mission_checkpoint: MissionCheckpointCallback | None = None,
    ) -> AgentRunOutput:
        if self._knowledge_snapshot_callback is not None:
            # Sync before every run: deletion/update and a restarted sidecar
            # cannot retain stale Agent-derived index entries.
            documents = await self._knowledge_snapshot_callback()
            await self._supervisor.sync_knowledge(documents)
        tools = await self._broker.agent_tool_catalog(
            conversation_id=conversation_id,
            run_id=run_id,
        )
        mne_change_planner_available = (
            self._change_plan_callback is not None
            and isinstance(trusted_context, dict)
            and trusted_context.get("mne_change_planner_available") is True
        )
        if not mne_change_planner_available:
            tools = [
                tool
                for tool in tools
                if tool.tool_name not in _MNE_CANDIDATE_TOOL_ALIASES
            ]
        catalog = {tool.tool_name for tool in tools}
        safe_trusted_context = _safe_trusted_context(
            trusted_context,
            knowledge_search_available=self._knowledge_snapshot_callback is not None,
            change_planner_available=self._change_plan_callback is not None,
            mne_change_planner_available=mne_change_planner_available,
        ) or {}
        historical_evidence = safe_trusted_context.pop(
            "historical_evidence",
            None,
        )
        if historical_evidence is not None:
            safe_trusted_context["historical_evidence_ref"] = {
                "schemaVersion": "1",
                "missionDigest": historical_evidence["missionDigest"],
                "bundleDigest": historical_evidence["bundleDigest"],
                "itemCount": len(historical_evidence["items"]),
            }
        checkpoint_digest: str | None = None
        if agent_checkpoint is not None:
            if "engineering_mission" not in safe_trusted_context:
                raise AgentProtocolError(
                    "Agent checkpoint requires a durable engineering mission"
                )
            encoded_checkpoint = _canonical_json(agent_checkpoint).encode("utf-8")
            if len(encoded_checkpoint) > _MAX_AGENT_CHECKPOINT_BYTES:
                raise AgentProtocolError("Agent checkpoint exceeds its durable bound")
            checkpoint_digest = hashlib.sha256(encoded_checkpoint).hexdigest()
        sidecar_prompt = (
            _exact_offline_operand_continuation_prompt(safe_trusted_context)
            or prompt
        )
        payload = {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "prompt": sidecar_prompt,
            "history": [] if agent_checkpoint is not None else _safe_history(history),
            "trusted_context": safe_trusted_context,
            "model": _model_payload(self._settings),
            "tools": [tool.model_dump(mode="json") for tool in tools],
        }
        if checkpoint_digest is not None:
            payload["agent_checkpoint_ref"] = {
                "schemaVersion": "1",
                "checkpointDigest": checkpoint_digest,
            }
        assert_agent_safe_payload(payload)
        result: AgentRunOutput | None = None
        last_progress: tuple[str, int] | None = None
        last_mission_cycle = 0
        last_mission_disposition: str | None = None

        async def handle(message: AgentProtocolMessage) -> None:
            nonlocal last_mission_cycle, last_mission_disposition
            nonlocal last_progress, result
            if message.type == "run.progress":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent progress belongs to another run")
                stage = message.payload.get("stage")
                elapsed_seconds = message.payload.get("elapsed_seconds")
                if stage not in {
                    "starting",
                    "engineering_analysis",
                    "planning_narrative",
                    "planning_native_design",
                    "planning_finalize",
                } or (
                    isinstance(elapsed_seconds, bool)
                    or not isinstance(elapsed_seconds, int)
                    or elapsed_seconds < 0
                ):
                    raise AgentProtocolError("Agent progress payload is invalid")
                progress = (stage, elapsed_seconds)
                if progress != last_progress:
                    last_progress = progress
                    if on_progress is not None:
                        await on_progress(stage, elapsed_seconds)
                return
            if message.type == "run.token":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent token belongs to another run")
                delta = message.payload.get("delta")
                if isinstance(delta, str) and on_token is not None:
                    await on_token(delta)
                return
            if message.type == "run.mission_checkpoint":
                if set(message.payload) != {
                    "run_id",
                    "cycle",
                    "cycle_disposition",
                    "mission",
                    "mission_digest",
                    "agent_checkpoint",
                    "agent_checkpoint_digest",
                }:
                    raise AgentProtocolError("Agent mission checkpoint payload is invalid")
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError(
                        "Agent mission checkpoint belongs to another run"
                    )
                cycle = message.payload.get("cycle")
                cycle_disposition = message.payload.get("cycle_disposition")
                mission = message.payload.get("mission")
                mission_digest = message.payload.get("mission_digest")
                agent_checkpoint_value = message.payload.get("agent_checkpoint")
                agent_checkpoint_digest = message.payload.get("agent_checkpoint_digest")
                if (
                    isinstance(cycle, bool)
                    or not isinstance(cycle, int)
                    or cycle < 1
                    or cycle > MAX_ENGINEERING_AUTONOMOUS_CYCLES
                    or cycle != last_mission_cycle + 1
                    or last_mission_disposition == "respond"
                    or cycle_disposition not in {"continue", "respond"}
                    or not isinstance(mission, dict)
                    or not isinstance(mission_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", mission_digest) is None
                    or not isinstance(agent_checkpoint_value, dict)
                    or not isinstance(agent_checkpoint_digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", agent_checkpoint_digest) is None
                    or on_mission_checkpoint is None
                ):
                    raise AgentProtocolError("Agent mission checkpoint is invalid")
                assert_agent_safe_payload(mission)
                await on_mission_checkpoint(
                    mission,
                    mission_digest,
                    cycle,
                    cycle_disposition,
                    agent_checkpoint_value,
                    agent_checkpoint_digest,
                )
                last_mission_cycle = cycle
                last_mission_disposition = cycle_disposition
                return
            if message.type == "tool.request":
                request = _tool_request_from_message(message, conversation_id, run_id)
                await self._dispatch_tool_request(
                    request,
                    catalog=catalog,
                    on_tool_event=on_tool_event,
                )
                return
            if message.type == "run.final":
                if message.payload.get("run_id") != run_id:
                    raise AgentProtocolError("Agent final result belongs to another run")
                result = _output_from_payload(message.payload.get("output"))
                return
            if message.type == "run.cancelled":
                raise asyncio.CancelledError
            if message.type == "run.error":
                if message.payload.get("code") == "output_contract":
                    diagnostic = _output_contract_diagnostic_from_payload(
                        message.payload.get("diagnostic")
                    )
                    raise AgentOutputContractError(
                        "Agent output failed its structured contract",
                        diagnostic=diagnostic,
                    )
                if message.payload.get("code") == "agent_runtime":
                    diagnostic = _runtime_diagnostic_from_payload(
                        message.payload.get("diagnostic")
                    )
                    raise AgentRuntimeError(
                        "Agent sidecar runtime could not complete the run",
                        diagnostic=diagnostic,
                    )
                raise AgentSidecarError("Agent sidecar could not complete the run")
            raise AgentProtocolError("unexpected Agent sidecar run message")

        try:
            await self._supervisor.run(
                payload,
                on_message=handle,
                historical_evidence=historical_evidence,
                agent_checkpoint=agent_checkpoint,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._supervisor.cancel_run(run_id))
            raise
        if result is None:
            raise AgentSidecarError("Agent sidecar ended without a final result")
        return result

    async def _dispatch_tool_request(
        self,
        request: ExternalToolRequestV1,
        *,
        catalog: set[str],
        on_tool_event: ToolEventCallback | None,
    ) -> None:
        evidence_ref: dict[str, Any] | None = None

        async def capture_event(
            event_type: str,
            payload: dict[str, Any],
        ) -> None:
            nonlocal evidence_ref
            if on_tool_event is None:
                return
            returned = await on_tool_event(event_type, payload)
            if event_type == "tool.completed" and isinstance(returned, Mapping):
                evidence_id = returned.get("evidenceId")
                if isinstance(evidence_id, str) and evidence_id:
                    evidence_ref = dict(returned)

        callback_event_payload: dict[str, Any] | None = None

        async def send_failure(failure_kind: str) -> None:
            if callback_event_payload is not None:
                await capture_event(
                    "tool.failed",
                    {**callback_event_payload, "failure_kind": failure_kind},
                )
            await self._supervisor.send_tool_result(
                {
                    "request_id": request.request_id,
                    "status": "failed",
                    "failure_kind": failure_kind,
                }
            )

        try:
            if request.tool_name in catalog:
                sink = capture_event
                value = await self._broker.execute_external_tool_request(request, sink=sink)
            elif (
                request.tool_name == "change.propose_offline"
                and self._change_plan_callback is not None
            ):
                callback_event_payload = {
                    "tool_name": request.tool_name,
                    "request_id": request.request_id,
                }
                if request.tool_call_id is not None:
                    callback_event_payload["tool_call_id"] = request.tool_call_id
                await capture_event("tool.started", callback_event_payload)
                value = await self._change_plan_callback(**request.arguments)
                await capture_event("tool.completed", callback_event_payload)
            else:
                raise AgentSidecarError("Agent requested a disallowed Host callback")
        except asyncio.CancelledError:
            await send_failure("cancelled")
            raise
        except MCPToolRejectedError as exc:
            compiler_error = _safe_mne_candidate_rejection_error(request, exc)
            if compiler_error is not None:
                # The broker already recorded a failed tool event.  This is not
                # evidence or success.  Preserve the normal failed frame, with
                # the safe compiler detail needed for model-side correction.
                await self._supervisor.send_tool_result(
                    {
                        "request_id": request.request_id,
                        "status": "failed",
                        "failure_kind": "tool_error",
                        "error": compiler_error,
                    }
                )
                return
            await send_failure("tool_error")
            return
        except MCPTransportError:
            await send_failure("transport")
            return
        except MCPToolError:
            await send_failure("host_policy")
            return
        except Exception:  # noqa: BLE001 - never expose Host callback diagnostics to Agent
            await send_failure("host_policy")
            return
        if request.tool_name in _STRUCTURAL_ANALYSIS_TOOL_ALIASES:
            try:
                completed_payload = _structural_tool_result_payload(
                    request_id=request.request_id,
                    tool_alias=request.tool_name,
                    evidence_ref=evidence_ref,
                    raw_result=value,
                )
            except (AgentProtocolError, MCPToolError):
                logger.warning(
                    "Host withheld an invalid structural-analysis Agent result frame"
                )
                await self._supervisor.send_tool_result(
                    {
                        "request_id": request.request_id,
                        "status": "failed",
                        "failure_kind": "host_policy",
                    }
                )
                return
            await self._supervisor.send_tool_result(completed_payload)
            return

        safe_result = scrub_agent_tool_result(value)
        if evidence_ref is not None:
            safe_evidence_ref = scrub_agent_tool_result(evidence_ref)
            if isinstance(safe_result, dict):
                safe_result = {
                    **safe_result,
                    "_controlstudio_evidence": safe_evidence_ref,
                }
            else:
                safe_result = {
                    "result": safe_result,
                    "_controlstudio_evidence": safe_evidence_ref,
                }
        await self._supervisor.send_tool_result(
            {
                "request_id": request.request_id,
                "status": "completed",
                "result": safe_result,
            }
        )

    async def test_capabilities(self) -> ModelCapabilityResult:
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id, "model": _model_payload(self._settings)}
        assert_agent_safe_payload(payload)
        response = await self._supervisor.test_capabilities(payload)
        if response.get("request_id") != request_id:
            raise AgentProtocolError("Agent capability result belongs to another request")
        result = response.get("result")
        if not isinstance(result, dict):
            failure_messages = {
                "model_authentication": (
                    "모델 엔드포인트 인증에 실패했습니다. API 키 또는 인증 헤더를 확인하세요."
                ),
                "model_timeout": "모델 엔드포인트 연결 시간이 초과되었습니다.",
                "model_connection": "모델 엔드포인트에 연결할 수 없습니다.",
                "model_rate_limited": (
                    "모델 엔드포인트의 요청 한도 또는 용량 제한에 걸렸습니다."
                ),
                "model_http_error": "모델 엔드포인트가 기능 검사 요청을 거부했습니다.",
                "model_runtime_dependency": (
                    "Agent 런타임 의존성을 불러오지 못했습니다. 개발 로그와 설치 BOM을 확인하세요."
                ),
            }
            message = failure_messages.get(
                response.get("code"),
                "Agent sidecar가 모델 기능을 확인하지 못했습니다.",
            )
            return ModelCapabilityResult.failed(message)
        try:
            tested_at = datetime.fromisoformat(str(result["tested_at"]))
            return ModelCapabilityResult(
                success=bool(result["success"]),
                tested_at=tested_at,
                completion=bool(result["completion"]),
                streaming=bool(result["streaming"]),
                tool_calling=bool(result["tool_calling"]),
                tool_result_continuation=bool(result["tool_result_continuation"]),
                structured_output=bool(result["structured_output"]),
                message=str(result["message"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentProtocolError("Agent capability result is invalid") from exc


def _model_payload(settings: StoredModelConnection) -> dict[str, Any]:
    return {
        "protocol": settings.protocol,
        "base_url": settings.base_url,
        "model": settings.model,
        "timeout_seconds": settings.timeout_seconds,
        # Model credentials are needed by the process that calls the model, but
        # are never persisted, logged, passed in argv, or sent to MCP.
        "api_key": settings.secrets.api_key,
        "custom_headers": settings.secrets.custom_headers,
    }


def _configured_agent_command() -> tuple[str, ...] | None:
    value = os.environ.get("CONTROLSTUDIO_AGENT_COMMAND", "").strip()
    if not value:
        return None
    parts = shlex.split(value, posix=False)
    command = tuple(
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
        else part
        for part in parts
    )
    if not command:
        return None
    return command


def _monorepo_agent_command() -> tuple[str, ...] | None:
    """Resolve the Agent's installed console entry point for local development.

    This intentionally does not import private Agent source or walk its source
    tree.  A release installation has no adjacent development environment and
    therefore falls through to the packaged command on ``PATH``.
    """

    platform_repository = Path(__file__).resolve().parents[3]
    if not (platform_repository / ".git").exists():
        return None
    development_root = platform_repository.parent
    repository = development_root / "gidong-agent"
    for executable in (
        repository / ".venv" / "Scripts" / "controlstudio-agent.exe",
        repository / ".venv" / "bin" / "controlstudio-agent",
    ):
        if executable.is_file():
            return (str(executable),)
    return None


def _sidecar_environment() -> dict[str, str]:
    """Pass ordinary process settings while withholding Host approval material."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SIDECAR_STRIPPED_ENVIRONMENT
    }
    return environment


def _safe_history(history: Sequence[Message]) -> list[dict[str, Any]]:
    return [
        {
            "role": item.role.value,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
            "structured_project_brief": bool(
                item.metadata.get("structured_project_brief")
            ),
        }
        for item in history[-100:]
        if item.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]


def _safe_trusted_context(
    value: dict[str, Any] | None,
    *,
    knowledge_search_available: bool,
    change_planner_available: bool,
    mne_change_planner_available: bool,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("workflow") == "new_project_planning":
        brief = value.get("project_brief")
        safe_brief = {
            key: item
            for key, item in brief.items()
            if isinstance(key, str) and isinstance(item, str)
        } if isinstance(brief, dict) else {}
        if isinstance(brief, dict):
            source_ids = brief.get("attachment_source_ids")
            if isinstance(source_ids, list) and all(
                isinstance(item, str) for item in source_ids
            ):
                safe_brief["attachment_source_ids"] = list(source_ids)
            attachments = brief.get("attachments")
            if isinstance(attachments, list):
                safe_brief["attachments"] = [
                    {
                        key: item
                        for key, item in attachment.items()
                        if key
                        in {
                            "source_id",
                            "title",
                            "kind",
                            "scope",
                            "vendor",
                            "product_family",
                            "version",
                            "language",
                            "authority",
                            "sha256",
                            "revision",
                        }
                        and (isinstance(item, str) or item is None)
                    }
                    for attachment in attachments
                    if isinstance(attachment, dict)
                ]
        return {
            "workflow": "new_project_planning",
            "project_brief": safe_brief,
            "current_prompt_is_structured_brief": bool(
                value.get("current_prompt_is_structured_brief")
            ),
            "knowledge_search_available": knowledge_search_available,
        }
    context: dict[str, Any] = {
        "workflow": "engineering",
        "knowledge_search_available": knowledge_search_available,
        "change_planner_available": change_planner_available,
        "mne_change_planner_available": mne_change_planner_available,
    }
    if value.get("change_request_scope") in {
        "analysis_only",
        "offline_candidate_revision",
        "permanent_offline_revision",
    }:
        context["change_request_scope"] = value["change_request_scope"]
    if value.get("verification_scope") in {
        "offline_native_reopen",
        "offline_native_reopen_xgsim",
    }:
        context["verification_scope"] = value["verification_scope"]
    if "offline_operand_mission" in value:
        try:
            mission = OfflineOperandMissionV1.model_validate(
                value["offline_operand_mission"]
            ).model_dump(mode="json", by_alias=True)
        except (TypeError, ValueError) as exc:
            raise AgentProtocolError("offline operand mission is invalid") from exc
        context["offline_operand_mission"] = mission
    if isinstance(value.get("stage"), str):
        context["stage"] = value["stage"]
    engineering_mission = value.get("engineering_mission")
    if isinstance(engineering_mission, dict):
        context["engineering_mission"] = engineering_mission
    if "historical_evidence" in value:
        if not isinstance(engineering_mission, dict):
            raise AgentProtocolError(
                "historical evidence requires a durable engineering mission"
            )
        steps = engineering_mission.get("steps")
        if not isinstance(steps, list):
            raise AgentProtocolError("engineering mission evidence binding is invalid")
        expected_evidence_ids: set[str] = set()
        for step in steps:
            evidence_refs = step.get("evidenceRefs") if isinstance(step, dict) else None
            if not isinstance(evidence_refs, list) or any(
                not isinstance(evidence_id, str) for evidence_id in evidence_refs
            ):
                raise AgentProtocolError(
                    "engineering mission evidence binding is invalid"
                )
            expected_evidence_ids.update(evidence_refs)
        mission_digest = hashlib.sha256(
            _canonical_json(engineering_mission).encode("utf-8")
        ).hexdigest()
        context["historical_evidence"] = validate_historical_evidence_bundle(
            value["historical_evidence"],
            mission_digest=mission_digest,
            expected_evidence_ids=expected_evidence_ids,
        )
    if "native_engineering_status" in value:
        context["native_engineering_status"] = validate_native_engineering_status(
            value["native_engineering_status"]
        )
    pending_change = value.get("pending_change")
    if isinstance(pending_change, dict) and set(pending_change) == {
        "transactionId",
        "state",
        "planDigest",
        "approvalExpiresAt",
    }:
        context["pending_change"] = dict(pending_change)
    change_outcome = value.get("change_outcome")
    if isinstance(change_outcome, dict) and set(change_outcome) == _CHANGE_OUTCOME_KEYS:
        transaction_id = change_outcome.get("transactionId")
        evidence_id = change_outcome.get("evidenceId")
        state = change_outcome.get("state")
        disposition = change_outcome.get("disposition")
        digests = (
            change_outcome.get("planDigest"),
            change_outcome.get("outcomeDigest"),
        )
        verification_digest = change_outcome.get("verificationEvidenceDigest")
        error_code = change_outcome.get("errorCode")
        try:
            canonical_ids = bool(
                isinstance(transaction_id, str)
                and str(uuid.UUID(transaction_id)) == transaction_id
                and isinstance(evidence_id, str)
                and str(uuid.UUID(evidence_id)) == evidence_id
            )
        except ValueError:
            canonical_ids = False
        if (
            change_outcome.get("schemaVersion") == "1"
            and canonical_ids
            and isinstance(state, str)
            and _CHANGE_OUTCOME_DISPOSITIONS.get(state) == disposition
            and all(
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                for digest in digests
            )
            and (
                verification_digest is None
                or (
                    isinstance(verification_digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", verification_digest)
                    is not None
                )
            )
            and (error_code is None or isinstance(error_code, str))
        ):
            context["change_outcome"] = dict(change_outcome)
    return context


def _tool_request_from_message(
    message: AgentProtocolMessage,
    conversation_id: str,
    run_id: str,
) -> ExternalToolRequestV1:
    try:
        request = ExternalToolRequestV1.model_validate(message.payload)
    except ValidationError as exc:
        raise AgentProtocolError("Agent external tool request is invalid") from exc
    if request.conversation_id != conversation_id or request.run_id != run_id:
        raise AgentProtocolError("Agent external tool request targets another run")
    return request


def _structural_observation_facts(
    receipt: Mapping[str, Any],
) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for fact_kind, field in (
        ("location", "locationFacts"),
        ("relation", "relationFacts"),
        ("project_structure", "structureFacts"),
    ):
        values = receipt.get(field, [])
        if not isinstance(values, list):
            raise AgentProtocolError("Structural observation facts are invalid")
        for value in values[:16]:
            if not isinstance(value, Mapping) or not isinstance(
                value.get("fingerprint"),
                str,
            ):
                raise AgentProtocolError("Structural observation fact is invalid")
            facts.append(
                {
                    "factKind": fact_kind,
                    "factFingerprint": str(value["fingerprint"]),
                    "statement": structural_observation_statement(
                        fact_kind,
                        value,
                    ),
                }
            )
    return facts


def _structural_evidence_projection(
    *,
    evidence_tool_name: str,
    evidence_ref: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the exact evidence object exposed to the Agent."""

    expected_tool_name = structural_receipt_tool_name(evidence_tool_name)
    try:
        validated_receipt = validate_structural_evidence_contract(
            evidence_ref.get("receipt"),
            expected_tool=expected_tool_name,
        )
    except (TypeError, ValueError, MCPToolError) as exc:
        raise AgentProtocolError("Structural analysis evidence receipt is invalid") from exc
    if expected_tool_name == "project.structure_index":
        raw_scope = validated_receipt["scope"]
        projected_receipt: dict[str, Any] = {
            "schema": _PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA,
            "toolName": expected_tool_name,
            "scope": {
                "scopeKey": raw_scope["scopeKey"],
                "query": raw_scope["query"],
                "coverageUnitKeys": raw_scope["coverageUnitKeys"],
                "countsAdditive": raw_scope["countsAdditive"],
                "payloadSetDigest": raw_scope["payloadSetDigest"],
            },
            "coverage": validated_receipt["coverage"],
            "receiptIndexTruncated": validated_receipt["receiptIndexTruncated"],
            "receiptIndexLimitations": validated_receipt[
                "receiptIndexLimitations"
            ],
            "structureFactCount": validated_receipt["structureFactCount"],
            "structureFactReturnedCount": validated_receipt[
                "structureFactReturnedCount"
            ],
            "structureFactsTruncated": validated_receipt[
                "structureFactsTruncated"
            ],
            "structureFactsDigest": validated_receipt["structureFactsDigest"],
            "structureFacts": validated_receipt["structureFacts"],
        }
        projected_receipt["projectionDigest"] = canonical_digest(projected_receipt)
        safe_evidence_ref = {
            **{key: value for key, value in evidence_ref.items() if key != "receipt"},
            "receipt": projected_receipt,
        }
    else:
        safe_evidence_ref = scrub_agent_tool_result(evidence_ref)
        if not isinstance(safe_evidence_ref, dict) or safe_evidence_ref != evidence_ref:
            raise AgentProtocolError("Structural analysis evidence is invalid")
    retained_model_context = safe_evidence_ref.get("modelContext")
    retained_model_context_digest = safe_evidence_ref.get("modelContextDigest")
    if expected_tool_name == "project.search" and retained_model_context is not None:
        try:
            canonical_model_context = validate_project_search_context(
                retained_model_context,
                receipt=validated_receipt,
            )
        except (TypeError, ValueError, MCPToolError) as exc:
            raise AgentProtocolError(
                "Project search evidence context is invalid"
            ) from exc
        if canonical_model_context != retained_model_context:
            raise AgentProtocolError(
                "Project search evidence context is not canonical"
            )
        if retained_model_context_digest != canonical_digest(
            canonical_model_context
        ):
            raise AgentProtocolError(
                "Project search evidence context digest is invalid"
            )
    elif expected_tool_name == "project.search" and retained_model_context_digest is not None:
        raise AgentProtocolError(
            "Project search evidence context digest has no context"
        )
    elif expected_tool_name != "project.search" and (
        retained_model_context is not None
        or retained_model_context_digest is not None
    ):
        raise AgentProtocolError(
            "Structural analysis evidence has an unexpected model context"
        )
    safe_evidence_ref = {
        **safe_evidence_ref,
        "observationFacts": _structural_observation_facts(validated_receipt),
    }
    receipt = safe_evidence_ref.get("receipt")
    expected_receipt_schema = (
        _PROJECT_STRUCTURE_EVIDENCE_PROJECTION_SCHEMA
        if expected_tool_name == "project.structure_index"
        else STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA
    )
    if (
        not isinstance(safe_evidence_ref.get("evidenceId"), str)
        or not isinstance(receipt, Mapping)
        or receipt.get("schema") != expected_receipt_schema
        or receipt.get("toolName") != expected_tool_name
    ):
        raise AgentProtocolError("Structural analysis evidence receipt is invalid")
    assert_agent_safe_payload(safe_evidence_ref)
    return safe_evidence_ref, validated_receipt


def project_historical_evidence(evidence_ref: Mapping[str, Any]) -> dict[str, Any]:
    """Project a journaled receipt exactly as it was exposed in its source run."""

    tool_name = evidence_ref.get("toolName")
    if not isinstance(tool_name, str) or not tool_name:
        raise AgentProtocolError("Historical evidence toolName is invalid")
    if tool_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE:
        projected, _ = _structural_evidence_projection(
            evidence_tool_name=tool_name,
            evidence_ref=evidence_ref,
        )
        return projected
    safe_evidence_ref = scrub_agent_tool_result(evidence_ref)
    if not isinstance(safe_evidence_ref, dict):
        raise AgentProtocolError("Historical evidence is not Agent-safe")
    assert_agent_safe_payload(safe_evidence_ref)
    return safe_evidence_ref


def _structural_tool_result_payload(
    *,
    request_id: str,
    tool_alias: str,
    evidence_ref: Mapping[str, Any] | None,
    raw_result: Any,
) -> dict[str, Any]:
    """Project a structural result to its bounded Host-authored receipt."""

    evidence_tool_name = _STRUCTURAL_ANALYSIS_TOOL_ALIASES.get(tool_alias)
    if evidence_tool_name is None or not isinstance(evidence_ref, Mapping):
        raise AgentProtocolError("Structural analysis result has no Host evidence receipt")
    expected_tool_name = structural_receipt_tool_name(evidence_tool_name)
    safe_evidence_ref, validated_receipt = _structural_evidence_projection(
        evidence_tool_name=evidence_tool_name,
        evidence_ref=evidence_ref,
    )
    result: dict[str, Any] = {
        "schema": _STRUCTURAL_TOOL_RESULT_PROJECTION_SCHEMA,
        "toolName": expected_tool_name,
        "rawResultOmitted": True,
        "authoritativeResultPath": "_controlstudio_evidence.receipt",
        "guidance": _STRUCTURAL_TOOL_RESULT_GUIDANCE,
        "_controlstudio_evidence": safe_evidence_ref,
    }
    if expected_tool_name == "program.logic_graph":
        model_context = logic_graph_context(raw_result)
    elif expected_tool_name == "program.state_model":
        model_context = state_model_context(raw_result)
    elif expected_tool_name == "project.search":
        model_context = validate_project_search_context(
            project_search_context(raw_result),
            receipt=validated_receipt,
        )
        if safe_evidence_ref.get("modelContext") != model_context:
            raise AgentProtocolError(
                "Journaled project search context differs from the tool result"
            )
    else:
        model_context = None
    if model_context is not None:
        result["modelContext"] = (
            model_context
            if expected_tool_name == "project.search"
            else {
                "sourceReceiptDigest": validated_receipt["receiptDigest"],
                **model_context,
            }
        )
    payload = {
        "request_id": request_id,
        "status": "completed",
        "result": result,
    }
    assert_agent_safe_payload(payload)
    encoded = encode_message("tool.result", payload)
    if len(encoded) > _STRUCTURAL_TOOL_RESULT_FRAME_MAX_BYTES:
        raise AgentProtocolError("Structural analysis result projection exceeds its wire budget")
    return payload


def _output_contract_diagnostic_from_payload(
    value: Any,
) -> AgentOutputContractDiagnostic | None:
    """Accept only non-sensitive, allowlisted sidecar failure diagnostics."""

    if not isinstance(value, Mapping) or set(value) != {
        "stage",
        "exceptionKind",
        "reasonCode",
        "validatorCode",
        "validationErrors",
    }:
        return None
    stage = value.get("stage")
    exception_kind = value.get("exceptionKind")
    reason_code = value.get("reasonCode")
    validator_code = value.get("validatorCode")
    raw_errors = value.get("validationErrors")
    if (
        not isinstance(stage, str)
        or stage not in _OUTPUT_CONTRACT_STAGES
        or not isinstance(exception_kind, str)
        or exception_kind not in _OUTPUT_CONTRACT_EXCEPTION_KINDS
        or not isinstance(reason_code, str)
        or reason_code not in _OUTPUT_CONTRACT_REASON_CODES
        or not isinstance(validator_code, str)
        or validator_code not in _OUTPUT_CONTRACT_VALIDATOR_CODES
        or not isinstance(raw_errors, list)
        or len(raw_errors) > 8
    ):
        return None

    validation_errors: list[AgentOutputValidationIssue] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping) or set(raw_error) != {"loc", "type"}:
            return None
        raw_location = raw_error.get("loc")
        error_type = raw_error.get("type")
        if (
            not isinstance(raw_location, list)
            or len(raw_location) > 24
            or not isinstance(error_type, str)
            or not 1 <= len(error_type) <= 80
            or not error_type[0].isascii()
            or not error_type[0].islower()
            or any(
                not character.isascii()
                or (not character.islower() and not character.isdigit())
                and character not in "_.-"
                for character in error_type
            )
        ):
            return None
        location: list[str | int] = []
        for item in raw_location:
            if isinstance(item, int) and not isinstance(item, bool):
                if item < 0 or item > 1_000_000:
                    return None
                location.append(item)
                continue
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= 120
                or any(
                    character not in _VALIDATION_LOCATION_CHARACTERS
                    for character in item
                )
            ):
                return None
            location.append(item)
        validation_errors.append(
            AgentOutputValidationIssue(
                loc=tuple(location),
                error_type=error_type,
            )
        )

    return AgentOutputContractDiagnostic(
        stage=stage,
        exception_kind=exception_kind,
        reason_code=reason_code,
        validator_code=validator_code,
        validation_errors=tuple(validation_errors),
    )


def _runtime_diagnostic_from_payload(value: Any) -> AgentRuntimeDiagnostic | None:
    """Accept only the sidecar's fixed non-sensitive runtime diagnostic codes."""

    if not isinstance(value, Mapping) or set(value) != {
        "stage",
        "exceptionKind",
        "reasonCode",
    }:
        return None
    stage = value.get("stage")
    exception_kind = value.get("exceptionKind")
    reason_code = value.get("reasonCode")
    if (
        not isinstance(stage, str)
        or stage not in _OUTPUT_CONTRACT_STAGES
        or not isinstance(exception_kind, str)
        or exception_kind not in _RUNTIME_EXCEPTION_KINDS
        or not isinstance(reason_code, str)
        or (
            reason_code not in _RUNTIME_REASON_CODES
            and re.fullmatch(r"provider_http_[45][0-9]{2}", reason_code) is None
        )
    ):
        return None
    return AgentRuntimeDiagnostic(
        stage=stage,
        exception_kind=exception_kind,
        reason_code=reason_code,
    )


def _output_from_payload(value: Any) -> AgentRunOutput:
    if not isinstance(value, Mapping):
        raise AgentProtocolError("Agent final output is invalid")
    try:
        workflow = value.get("workflow", "engineering")
        if workflow not in {"engineering", "new_project_planning"}:
            raise ValueError("workflow")
        plan_status = value.get("plan_status")
        if plan_status not in {None, "needs_clarification", "ready_for_review"}:
            raise ValueError("plan_status")
        content = value["content"]
        model = value["model"]
        if not isinstance(content, str) or not isinstance(model, str):
            raise TypeError("output fields")
        engineering_mission = value.get("engineering_mission")
        engineering_mission_digest = value.get("digest")
        cycle_disposition = value.get("cycle_disposition")
        mission_fields_present = (
            engineering_mission is not None,
            engineering_mission_digest is not None,
            cycle_disposition is not None,
        )
        if any(mission_fields_present) and not all(mission_fields_present):
            raise ValueError("incomplete engineering mission output")
        if engineering_mission is not None and (
            not isinstance(engineering_mission, dict)
            or not isinstance(engineering_mission_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", engineering_mission_digest) is None
            or cycle_disposition not in {"continue", "respond"}
        ):
            raise ValueError("engineering mission output")
        return AgentRunOutput(
            content=content,
            model=model,
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            workflow=workflow,
            plan_status=plan_status,
            project_name=value.get("project_name") if isinstance(value.get("project_name"), str) else None,
            plan_artifact=value.get("plan_artifact") if isinstance(value.get("plan_artifact"), dict) else None,
            plan_digest=value.get("plan_digest") if isinstance(value.get("plan_digest"), str) else None,
            engineering_analysis=(
                value.get("engineering_analysis")
                if isinstance(value.get("engineering_analysis"), dict)
                else None
            ),
            engineering_analysis_digest=(
                value.get("engineering_analysis_digest")
                if isinstance(value.get("engineering_analysis_digest"), str)
                else None
            ),
            engineering_mission=engineering_mission,
            digest=engineering_mission_digest,
            cycle_disposition=cycle_disposition,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentProtocolError("Agent final output is invalid") from exc
