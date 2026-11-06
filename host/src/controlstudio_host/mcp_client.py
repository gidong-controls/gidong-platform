from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from controlstudio_contracts import MCPProductManifestV1, ProgramCheckFailureObservationV1
from controlstudio_contracts.native_failure import (
    NativeFailureProtocolError,
    validate_native_failure,
)
from controlstudio_contracts.native_ld_commands import (
    NATIVE_EDITOR_COMPOSITE_SECONDS,
    NATIVE_EDITOR_DEFAULT_SECONDS,
    native_ld_composite_actions,
    native_ld_declaration_seconds,
    native_ld_entry_seconds,
)
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED
from pydantic import ValidationError
from typing_extensions import Self

from .windows_mcp_transport import ControlStudioWindowsStdioTransport, _MCPStartupProbe

EXPECTED_SERVER_NAME = "xg5000-mcp-server"
MCP_PRODUCT_CONTRACT_VERSION = "1.0"
MCP_PRODUCT_MANIFEST_SCHEMA = "controlstudio-mcp-product-manifest/v1"
MCP_PRODUCT_NAME = "ls-xg5000"
MCP_PRODUCT_MANIFEST_TOOL = "controlstudio.contract_manifest"
CONTROLLER_OBSERVATION_TTL_SECONDS = 15.0
# Public workspace launch waits at most 120 seconds inside the CLI.  MCP owns
# 15 additional seconds for exact-child cleanup and its failure envelope; the
# Host transport then reserves 5 seconds to receive/decode that envelope.
MCP_WORKSPACE_LAUNCH_OPERATION_TIMEOUT_SECONDS = 135.0
HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS = 140.0
logger = logging.getLogger(__name__)
_ALLOWED_CONTROLLER_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)

# This catalog covers analysis, navigation and explicitly owned modal handling.
# Project changes, saves and simulator control use separate Host/native routes;
# advertising an MCP tool does not add it to this model-facing catalog.
AGENT_TOOL_NAMES: dict[str, str] = {
    "get_xg5000_session_status": "xg5000.session_status",
    "get_simulator_target_status": "sim.target_status",
    "list_project_programs": "project.list_programs",
    "inspect_project_symbols": "project.inspect_symbols_ui",
    "open_project_symbols": "project.open_symbols_ui",
    "export_project_symbols": "project.export_symbols",
    "focus_xg5000_program": "program.focus",
    "read_ladder_program": "program.read_ld",
    "inspect_project_hardware_context": "project.hardware_context",
    "inspect_project_structure": "project.structure_index",
    "search_project_evidence": "project.search",
    "analyze_change_target": "program.change_target_analysis",
    
    
    
    
    "build_program_logic_graph": "program.logic_graph",
    "build_program_state_model": "program.state_model",
    "trace_device_causal_impact": "device.causal_impact",
    "find_device_references": "device.references",
    "build_project_behavior_report": "project.behavior_report",
    "capture_project_snapshot": "project.snapshot",
    "inspect_xg5000_modal": "xg5000.inspect_owned_modal",
    "cancel_xg5000_modal": "xg5000.cancel_owned_modal",
    "get_ls_controller_status": "controller.ls.status",
    "read_ls_controller_devices": "controller.ls.read",
    "trace_ls_controller_devices": "controller.ls.trace",
    "compare_workspace_source": "workspace.compare",
    "inspect_workspace_hardware_context": "workspace.hardware_context",
    "inspect_workspace_structure": "workspace.structure_index",
    "search_workspace_evidence": "workspace.search",
}
AGENT_CONTROLLER_MCP_TOOLS = frozenset(
    {
        "controller.ls.status",
        "controller.ls.read",
        "controller.ls.trace",
    }
)
AGENT_MODAL_MUTATION_MCP_TOOLS = frozenset({"xg5000.cancel_owned_modal"})
AGENT_READ_ANALYZE_MCP_TOOLS = frozenset(AGENT_TOOL_NAMES.values()) - AGENT_MODAL_MUTATION_MCP_TOOLS
HOST_SESSION_MCP_TOOLS = frozenset({"xg5000.instances", "xg5000.attach"})
HOST_NAVIGATION_MCP_TOOLS = frozenset(
    {"program.focus_location", }
)
HOST_WORKSPACE_MCP_TOOLS = frozenset(
    {
        
        
        
        
        "workspace.open_native",
        "workspace.reserve_native",
        "workspace.prepare_native",
        "workspace.inspect_native_preparation",
        "workspace.cancel_native_preparation",
        "workspace.simulator",
        "workspace.status",
        "workspace.compare",
        "workspace.structure_index",
        "project.export_symbols",
        "project.inspect_symbols_ui",
        "project.open_symbols_ui",
        "xg5000.open_project",
        "xg5000.editor",
        "xg5000.inspect_abandoned_operation",
        "xg5000.settle_abandoned_operation",
        "xg5000.cleanup_abandoned_workspace_launches",
        "xg5000.resume_terminal_change_session",
        "xg5000.reviewed_no_save_close",
        "xg5000.close_idle_instance",
        "xg5000.launch_idle",
    }
)
HOST_CHANGE_MCP_TOOLS = frozenset(
    {
        "project.snapshot",
        "project.backup",
        "program.plan_change",
        "program.apply_ld_patch",
        
        
        
        
        
        
        
        
        "project.diff",
        "project.validate_change",
        "project.verify_ld_operand_reopen",
        "project.commit_save",
        "project.rollback_change",
        "change.transaction_status",
        "change.abort",
        "change.commit",
    }
)
HOST_RECOVERY_MCP_TOOLS = frozenset(
    {
        "change.release_terminal_lease",
        "change.transaction_lookup",
        "change.restore_verified_quarantined_backup",
        "xg5000.attach",
        "xg5000.session_status",
    }
)
HOST_CONTROLLER_MCP_TOOLS = AGENT_CONTROLLER_MCP_TOOLS
HOST_RESTORE_MCP_TOOLS = frozenset(
    {
        "controller.restore_preflight",
        "controller.begin_restore_handoff",
        "controller.complete_restore_handoff",
    }
)
READ_ANALYZE_MCP_TOOLS = (
    AGENT_READ_ANALYZE_MCP_TOOLS
    | HOST_SESSION_MCP_TOOLS
    | HOST_NAVIGATION_MCP_TOOLS
)
REQUIRED_PRODUCT_MCP_TOOLS = (
    READ_ANALYZE_MCP_TOOLS
    | AGENT_MODAL_MUTATION_MCP_TOOLS
    | HOST_WORKSPACE_MCP_TOOLS
    | HOST_CHANGE_MCP_TOOLS
    | HOST_RECOVERY_MCP_TOOLS
    | HOST_RESTORE_MCP_TOOLS
    | frozenset({MCP_PRODUCT_MANIFEST_TOOL})
)
SESSION_IDENTITY_FIELDS = ("session_id", "pid", "hwnd", "project_path", "project_hash")
WORKSPACE_STATIC_IDENTITY_FIELDS = (
    "workspace_id",
    "revision_id",
    "source_kind",
    "provenance_receipt_digest",
    "source_sha256",
    "working_path",
    "working_sha256",
)
AGENT_TOOL_REQUIRED_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "workspace.compare": ("workspace_id",),
    "workspace.hardware_context": ("workspace_id", "revision_id"),
    "workspace.structure_index": ("workspace_id", "revision_id"),
    "workspace.search": ("workspace_id", "revision_id"),
    "xg5000.session_status": ("session_id",),
    "sim.target_status": SESSION_IDENTITY_FIELDS,
    "project.list_programs": SESSION_IDENTITY_FIELDS,
    "project.inspect_symbols_ui": SESSION_IDENTITY_FIELDS,
    "project.open_symbols_ui": SESSION_IDENTITY_FIELDS,
    "project.export_symbols": SESSION_IDENTITY_FIELDS,
    "program.focus": SESSION_IDENTITY_FIELDS,
    "program.focus_location": SESSION_IDENTITY_FIELDS,
    
    "program.read_ld": SESSION_IDENTITY_FIELDS,
    "project.hardware_context": SESSION_IDENTITY_FIELDS,
    "project.structure_index": SESSION_IDENTITY_FIELDS,
    "project.search": SESSION_IDENTITY_FIELDS,
    "program.change_target_analysis": SESSION_IDENTITY_FIELDS,
    
    
    
    
    "program.logic_graph": SESSION_IDENTITY_FIELDS,
    "program.state_model": SESSION_IDENTITY_FIELDS,
    "device.causal_impact": SESSION_IDENTITY_FIELDS,
    "device.references": SESSION_IDENTITY_FIELDS,
    "project.behavior_report": SESSION_IDENTITY_FIELDS,
    "project.snapshot": SESSION_IDENTITY_FIELDS,
    "xg5000.inspect_owned_modal": SESSION_IDENTITY_FIELDS,
    "xg5000.cancel_owned_modal": SESSION_IDENTITY_FIELDS,
}
CONTROLLER_ENDPOINT_FIELDS = ("host", "port", "timeout_seconds")

MUTATING_MCP_TOOLS = frozenset(
    {
        "xg5000.editor",
        "xg5000.reviewed_no_save_close",
        "xg5000.settle_abandoned_operation",
        
        
        "controller.begin_restore_handoff",
        "controller.complete_restore_handoff",
        
        
        "workspace.open_native",
        "xg5000.open_project",
        "xg5000.cleanup_abandoned_workspace_launches",
        "project.backup",
        "program.apply_ld_patch",
        
        
        
        
        "project.validate_change",
        "project.rollback_change",
        "project.diff",
        "program.check",
        "project.commit_save",
        "change.commit",
        "change.abort",
        "change.release_terminal_lease",
        "sim.start",
        "sim.write_program",
        "sim.run",
        "sim.runtime_proof",
        "sim.stop",
        "xg5000.close_idle_instance",
        "xg5000.launch_idle",
        "xg5000.cancel_owned_modal",
    }
)


def _editor_batch_sdk_timeout(name: str, arguments: dict[str, Any]) -> float | None:
    if name in {"xg5000.inspect_abandoned_operation", "xg5000.settle_abandoned_operation"}:
        # Recovery includes a native read, two process snapshots and mutex admission.
        return 180.0
    if name != "xg5000.editor":
        return None
    if arguments.get("action") == "find_ld_devices":
        queries = arguments.get("queries")
        if (not isinstance(queries, list) or not queries
                or any(not isinstance(query, str) or not 1 <= len(query) <= 128 for query in queries)):
            raise MCPToolError("find_ld_devices requires a non-empty ordered queries array")
        # Shared native setup + per-query120 + CLI return15 + PID-lock30 + SDK15.
        return 120.0 * (len(queries) + 1) + 60.0
    if arguments.get("action") != "apply_ld_edits":
        # Match CLI standalone classification without importing its package.
        composite = arguments.get('action') in native_ld_composite_actions() or arguments.get('action') in {'set_variable_comment', 'set_declaration_comments', 'inspect_variable_comment', 'activate_find_result', 'set_rung_comment', 'paste_observed_ld_fragment', 'inspect_rung_comment', 'inspect_declaration_comments', 'find_ld_device', 'goto_step'}
        # Native budget + CLI return15 + PID-lock30 + SDK margin15.
        return (NATIVE_EDITOR_COMPOSITE_SECONDS if composite else NATIVE_EDITOR_DEFAULT_SECONDS) + 60.0
    entries = arguments.get("entries")
    actions = {"write_ld_program", "write_ld_element", "insert_contact", "insert_parallel_contact",
               "delete_ld_element", "set_ld_vertical_wire", "set_ld_horizontal_wire",
               "insert_ld_row_before", "insert_ld_row_after", "insert_ld_instruction"}
    if (not isinstance(entries, list) or not entries
            or any(not isinstance(entry, dict) or not isinstance(entry.get("action"), str)
                   or entry["action"] not in actions for entry in entries)):
        raise MCPToolPreflightError('EDITOR_BATCH_ARGUMENTS_INVALID',
            "apply_ld_edits requires supported ordered native entries",
            details={'invalid_fields': ['entries'], 'supported_actions': sorted(actions)})
    capture_after = arguments.get("capture_after", False)
    if type(capture_after) is not bool:
        raise MCPToolPreflightError('EDITOR_BATCH_ARGUMENTS_INVALID',
            "capture_after must be a boolean", details={'invalid_fields': ['capture_after']})
    # Native admission30 + sum(existing child budgets) + CLI margin15 +
    # service PID-lock admission30 + SDK margin15. Optional post-batch capture
    # adds normal read30 and view-restore30; no per-entry reads or batch ceiling.
    return (90.0 + sum(native_ld_declaration_seconds(entry) if entry['action'] == 'write_ld_program' else native_ld_entry_seconds(entry["action"]) for entry in entries)
            + (60.0 if capture_after else 0.0))


class MCPStartupError(RuntimeError):
    """The bundled MCP process did not satisfy the Host identity contract."""


class MCPToolError(RuntimeError):
    """A Host-owned MCP call failed."""


class MCPToolPreflightError(MCPToolError):
    """An owned argument guard rejected a request before any native input."""

    def __init__(self, kind: str, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.native_failure = validate_native_failure({
            'schema': 'controlstudio-native-failure/v1', 'kind': kind,
            'message': message, 'details': {**details, 'phase': 'preflight',
                'execution_status': 'not_started', 'action_attempted': False,
                'input_state_unknown': False, 'automatic_replay': False}})

    def as_result(self) -> dict[str, Any]:
        failure = validate_native_failure(self.native_failure)
        return {'ok': False, 'status': 'not_started', 'read_only': True,
            'action_attempted': False, 'input_state_unknown': False, 'automatic_replay': False,
            'error': {'kind': failure['kind'], 'message': failure['message'],
                'nativeFailure': failure, **{field: failure['details'][field] for field in
                    ('invalid_fields', 'missing_fields') if field in failure['details']}}}


class MCPToolRejectedError(MCPToolError):
    """The connected MCP rejected a validly delivered tool request."""

    def __init__(
        self,
        message: str,
        *,
        mcp_error_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.mcp_error_payload = mcp_error_payload


class MCPToolContractError(MCPToolError):
    """The connected MCP returned content that violates its declared schema."""




def _exact_tool_error_payload(
    value: str | dict[str, Any], *, tool_name: str | None = None,
) -> dict[str, Any] | None:
    """Accept only the complete stdio ToolError envelope, never a substring."""

    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return None
    # A declared native protocol must either validate or fail explicitly.  It
    # must not silently fall back to interpreting a human-readable message.
    native = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict) and "nativeFailure" in payload["error"]:
        try:
            native = validate_native_failure(payload["error"]["nativeFailure"])
        except (NativeFailureProtocolError, TypeError, ValueError) as exc:
            raise MCPToolContractError("MCP native failure violates the shared protocol") from exc
        if (set(payload) != {"schema_version", "status", "error"}
                or payload.get("schema_version") != "1.0"
                or payload.get("status") != "error"
                or not {"type", "message", "nativeFailure"} <= set(payload["error"])
                or set(payload["error"]) - {"type", "message", "code", "nativeFailure", "programCheckFailure"}
                or not isinstance(payload["error"].get("type"), str)
                or not isinstance(payload["error"].get("message"), str)
                or ("code" in payload["error"] and (
                    not isinstance(payload["error"]["code"], str)
                    or re.fullmatch(r"[A-Za-z0-9_]{1,120}", payload["error"]["code"]) is None))):
            raise MCPToolContractError("MCP native failure has an incompatible error envelope")
    recovery = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict) and "operationRecovery" in payload["error"]:
        typed = payload["error"]
        candidate = typed["operationRecovery"]
        if (typed.get("type") != "OperationQuarantinedError"
                or typed.get("code") != "XG5000_OPERATION_QUARANTINED"
                or not isinstance(candidate, dict)
                or type(candidate.get("input_state_unknown")) is not bool):
            return None
        fields = {"state", "quarantined", "inspection_required", "old_outcome_unknown",
                  "input_state_unknown", "automatic_replay"}
        if candidate["input_state_unknown"] is False:fields.add("action_attempted")
        if (set(candidate) != fields or candidate.get("state") != "quarantined"
                or any(candidate.get(key) is not True for key in
                       ("quarantined", "inspection_required", "old_outcome_unknown"))
                or candidate.get("automatic_replay") is not False
                or (candidate["input_state_unknown"] is False and candidate.get("action_attempted") is not False)):
            return None
        recovery = dict(candidate)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "status", "error"}
        or payload.get("schema_version") != "1.0"
        or payload.get("status") != "error"
        or not isinstance(payload.get("error"), dict)
        or (native is None and set(payload["error"]) not in (
            {"type", "message"}, {"type", "message", "code"},
            {"type", "message", "code", "programCheckFailure"},
            {"type", "message", "code", "operationRecovery"},
        ))
        or not isinstance(payload["error"].get("type"), str)
        or not isinstance(payload["error"].get("message"), str)
        or (
            "code" in payload["error"]
            and (
                not isinstance(payload["error"].get("code"), str)
                or re.fullmatch(r"[A-Za-z0-9_]{1,120}", payload["error"]["code"]) is None
            )
        )
    ):
        return None
    error = {
        "type": payload["error"]["type"],
        "message": payload["error"]["message"],
    }
    if native is not None:
        error["nativeFailure"] = native
    if "code" in payload["error"]:
        error["code"] = payload["error"]["code"]
    if recovery is not None:
        error["operationRecovery"] = recovery
    if "programCheckFailure" in payload["error"]:
        if error.get("code") != "PROGRAM_CHECK_FAILED":
            if native is not None:
                raise MCPToolContractError("MCP native failure has an incompatible program check code")
            return None
        try:
            observation = ProgramCheckFailureObservationV1.model_validate(
                payload["error"]["programCheckFailure"],
            )
        except (ValidationError, TypeError, ValueError) as exc:
            if native is not None:
                raise MCPToolContractError("MCP native failure has an invalid program check observation") from exc
            return None
        error["programCheckFailure"] = observation.model_dump(mode="json", by_alias=True)
    return {
        "schema_version": "1.0",
        "status": "error",
        "error": error,
    }


def _log_bounded_change_rejection(
    tool_name: str, payload: dict[str, Any] | None
) -> None:
    """Retain only a validated MCP error envelope in the private Host log."""

    if payload is None:
        return
    error = payload["error"]
    logger.warning(
        "Host change MCP rejection (tool=%s, type=%s, message=%s)",
        tool_name,
        error["type"],
        error["message"],
    )


class MCPTransportError(MCPToolError):
    """The MCP transport is unavailable or failed during a request."""


def _is_sdk_output_schema_error(name: str, exc: Exception) -> bool:
    """Recognize only FastMCP's bounded RuntimeError wrapper for schema checks."""

    if type(exc) is not RuntimeError:
        return False
    message = str(exc)
    if message == (
        f"Tool {name} has an output schema but did not return structured content"
    ):
        return True
    prefixes = (
        f"Invalid structured content returned by tool {name}:",
        f"Invalid schema for tool {name}:",
    )
    if not message.startswith(prefixes):
        return False

    # FastMCP wraps jsonschema's two validation exception types in a plain
    # RuntimeError. Requiring that cause avoids treating unrelated RuntimeError
    # messages as a contract fault; neither message nor cause is exposed.
    cause = exc.__cause__ or exc.__context__
    return (
        cause is not None
        and type(cause).__module__.startswith("jsonschema.")
        and type(cause).__name__ in {"ValidationError", "SchemaError"}
    )


def _monorepo_mcp_command() -> str | None:
    """Resolve the installed PLC MCP console entry point in a Git dev workspace."""

    platform_repository = Path(__file__).resolve().parents[3]
    if not (platform_repository / ".git").exists():
        return None
    development_root = platform_repository.parent
    repository = development_root / "gidong-plc-mcp"
    for executable in (
        repository / ".venv" / "Scripts" / "controlstudio-plc-mcp.exe",
        repository / ".venv" / "bin" / "controlstudio-plc-mcp",
    ):
        if executable.is_file():
            return str(executable)
    return None


def _mcp_startup_execution_context(command: str, environment: dict[str,str]) -> dict[str,bool | str]:
    """Closed comparisons only; never retain environment values, paths or identities."""
    def matches(actual: str | None, expected: str | None) -> bool:
        return bool(actual and expected and os.path.normcase(str(Path(actual).resolve()))
                    == os.path.normcase(str(Path(expected).resolve())))
    loop_name = type(asyncio.get_running_loop()).__name__
    loop_kind = ("selector" if loop_name == "_WindowsSelectorEventLoop" else
                 "proactor" if loop_name == "ProactorEventLoop" else "other")
    workspace = Path(__file__).resolve().parents[4]
    return {"command_matches_monorepo":matches(command,_monorepo_mcp_command()),
            "cli_command_matches_monorepo":matches(environment.get("CONTROLSTUDIO_XG5000_CLI_COMMAND"),
                str(workspace/"controlstudio-xg5000-cli/.venv/Scripts/controlstudio-xg5000.exe")),
            "pythonhome_present":"PYTHONHOME" in environment,
            "pythonpath_present":"PYTHONPATH" in environment,"loop_kind":loop_kind}


@dataclass(frozen=True)
class MCPStatus:
    status: str
    server_name: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    tool_count: int = 0
    agent_tool_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _StartupRetryClient(Client[Any]):
    """Keep the SDK's typed initialize timeout before its context unwraps it."""

    initialize_timed_out = False

    def __init__(self, *args: Any, startup_deadline: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._startup_deadline = startup_deadline

    async def initialize(self, timeout: Any = None) -> Any:
        try:
            remaining = self._startup_deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("MCP startup deadline expired before initialize")
            if timeout is None:
                timeout = (remaining if self._init_timeout is None
                           else min(self._init_timeout, remaining))
            async with asyncio.timeout_at(self._startup_deadline):
                return await super().initialize(timeout=timeout)
        except RuntimeError as exc:
            if isinstance(exc.__cause__, TimeoutError):
                self.initialize_timed_out = True
            raise


def _safe_mcp_call_failure(exc: Exception) -> str:
    """Classify closed SDK scalars only; exception messages/data are never exposed."""
    if isinstance(exc, TimeoutError):
        return "request timed out"
    if isinstance(exc, McpError):
        code = exc.error.code
        if type(code) is int and code == 408:
            return "request timed out"
        if type(code) is int and code == CONNECTION_CLOSED:
            return "connection closed"
    return "call failed"


class MCPGateway:
    """Own one bundled MCP process and serialize every XG5000-facing call.

    The MCP server is launched without its unsafe validation flag.  The Host also
    filters the model-facing catalog. Project mutation and native execution use
    their separate Host-authorized routes rather than that analysis catalog.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
        init_timeout_seconds: float | None = None,
        startup_timeout_seconds: float = 60.0,
        authorization_secret: str | None = None,
    ) -> None:
        # An explicit command selects the installed MCP artifact. A Git
        # development workspace may resolve its sibling installed console entry;
        # otherwise the console entry is resolved through PATH.
        configured_command = command or os.environ.get("CONTROLSTUDIO_MCP_COMMAND")
        if configured_command is not None:
            self._command = configured_command
            self._args = list(args) if args is not None else []
        elif development_command := _monorepo_mcp_command():
            self._command = development_command
            self._args = list(args) if args is not None else []
        else:
            self._command = "controlstudio-plc-mcp"
            self._args = list(args) if args is not None else []
        self._cwd = cwd
        # Startup owns the default initialize budget; an explicit cap may shorten it.
        self._init_timeout_seconds = init_timeout_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._startup_cleanup_incomplete = False
        self._authorization_secret = (
            authorization_secret
            or os.environ.get("CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET")
            or base64.urlsafe_b64encode(secrets.token_bytes(48))
            .decode("ascii")
            .rstrip("=")
        )
        try:
            padding = "=" * (-len(self._authorization_secret) % 4)
            self._authorization_signing_key = base64.urlsafe_b64decode(
                self._authorization_secret + padding
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "authorization_secret must be URL-safe base64"
            ) from exc
        if len(self._authorization_signing_key) < 32:
            raise ValueError("authorization_secret must decode to at least 32 bytes")
        self._client: Client[Any] | None = None
        self._active_session_identity: dict[str, Any] | None = None
        self._active_controller_endpoint: dict[str, Any] | None = None
        self._controller_observation_expires_at: float | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._call_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._status = MCPStatus(status="stopped")

    @property
    def status(self) -> MCPStatus:
        return self._status

    @property
    def agent_mcp_client(self) -> Client[Any]:
        """Expose the ready transport only to the Platform execution broker."""

        if self._client is None or self._status.status != "ready":
            raise MCPStartupError("MCP is not ready")
        return self._client

    @property
    def agent_session_bound(self) -> bool:
        return self._active_session_identity is not None

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any] | None:
        """Return a copy of the Host-owned identity used for Agent tool injection."""

        identity = self._active_session_identity
        return dict(identity) if identity is not None else None

    @property
    def controller_endpoint_snapshot(self) -> dict[str, Any] | None:
        """Return a copy of the Host-owned endpoint used for controller tools."""

        endpoint = self._active_controller_endpoint
        return dict(endpoint) if endpoint is not None else None

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        missing = [
            field
            for field in SESSION_IDENTITY_FIELDS
            if field not in session or session[field] is None or session[field] == ""
        ]
        if missing:
            raise MCPToolError(
                "cannot bind an incomplete engineering session: " + ", ".join(missing)
            )
        identity = {field: session[field] for field in SESSION_IDENTITY_FIELDS}
        if any(field in session for field in WORKSPACE_STATIC_IDENTITY_FIELDS):
            identity.update(self._workspace_static_identity(session))
        self._active_session_identity = identity

    def bind_agent_workspace(self, workspace: dict[str, Any]) -> None:
        self._active_session_identity = self._workspace_static_identity(workspace)

    @staticmethod
    def _workspace_static_identity(value: dict[str, Any]) -> dict[str, Any]:
        missing = [field for field in WORKSPACE_STATIC_IDENTITY_FIELDS if field not in value]
        if missing:
            raise MCPToolError(
                "cannot bind an incomplete engineering workspace: " + ", ".join(missing)
            )
        identity = {field: value[field] for field in WORKSPACE_STATIC_IDENTITY_FIELDS}
        source_kind = identity["source_kind"]
        provenance_digest = identity["provenance_receipt_digest"]
        hashes = (
            provenance_digest,
            identity["source_sha256"],
            identity["working_sha256"],
        )
        if (
            not all(
                isinstance(identity[field], str) and identity[field]
                for field in ("workspace_id", "revision_id", "working_path")
            )
            or source_kind not in {"local_file", "restored_from_controller", "new_build"}
            or (source_kind == "local_file") != (provenance_digest is None)
            or any(
                value is not None
                and (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                )
                for value in hashes
            )
        ):
            raise MCPToolError("cannot bind an invalid engineering workspace identity")
        return identity

    def clear_agent_session(self) -> None:
        self._active_session_identity = None

    def diagnostic_session(self, session: dict[str, Any]) -> MCPDiagnosticSession:
        return MCPDiagnosticSession(self, session)

    def bind_controller_endpoint(
        self,
        endpoint: dict[str, Any],
        *,
        observation_current: bool = False,
    ) -> None:
        self._active_controller_endpoint = self._normalize_controller_endpoint(endpoint)
        self._controller_observation_expires_at = (
            time.monotonic() + CONTROLLER_OBSERVATION_TTL_SECONDS
            if observation_current
            else None
        )

    @property
    def controller_observation_current(self) -> bool:
        expires_at = self._controller_observation_expires_at
        return (
            self._active_controller_endpoint is not None
            and expires_at is not None
            and time.monotonic() < expires_at
        )

    def clear_controller_endpoint(self) -> None:
        self._active_controller_endpoint = None
        self._controller_observation_expires_at = None

    async def start(self, *, _startup_deadline: float | None = None) -> MCPStatus:
        async with self._lifecycle_lock:
            self._stopping = False
            if self._client is not None:
                return self._status

            if self._startup_cleanup_incomplete:
                raise MCPStartupError("MCP previous startup process retirement is unconfirmed")

            stage = "process_initialize"
            startup_started = time.monotonic()
            loop = asyncio.get_running_loop()
            startup_deadline = (
                _startup_deadline if _startup_deadline is not None
                else loop.time() + self._startup_timeout_seconds
            )
            if loop.time() >= startup_deadline:
                raise MCPStartupError("MCP startup deadline expired before process creation")

            async def startup_step(awaitable: Any) -> Any:
                # One deadline across all readiness stages; tool-call budgets are separate.
                async with asyncio.timeout_at(startup_deadline):
                    return await awaitable
            startup_probe = _MCPStartupProbe(max(0.0, startup_deadline - loop.time()))

            def startup_stage(value: str) -> None:
                nonlocal stage
                stage = value
                self._status = MCPStatus(status="starting", error=stage)
                logger.info("MCP startup stage=%s", stage)

            startup_stage("process_initialize")
            environment = _mcp_environment(authorization_secret=self._authorization_secret)
            try:
                startup_probe.execution_context = _mcp_startup_execution_context(self._command,environment)
            except Exception:
                pass  # Optional diagnostics cannot replace original startup handling.
            transport = _mcp_stdio_transport(
                command=self._command,
                args=self._args,
                environment=environment,
                cwd=self._cwd,
                startup_probe=startup_probe,
            )
            client: Client[Any] = _StartupRetryClient(
                transport,
                name="controlstudio-host",
                init_timeout=(startup_deadline - loop.time() if self._init_timeout_seconds is None
                              else min(self._init_timeout_seconds, startup_deadline - loop.time())),
                startup_deadline=startup_deadline,
            )
            startup_probe.start()
            try:
                await startup_step(client.__aenter__())
                startup_stage("tool_catalog")
                initialize_result = client.initialize_result
                if initialize_result is None:
                    raise MCPStartupError("MCP initialize result is missing")

                server_name = initialize_result.serverInfo.name
                server_version = initialize_result.serverInfo.version
                protocol_version = initialize_result.protocolVersion
                if server_name != EXPECTED_SERVER_NAME:
                    raise MCPStartupError(f"unexpected MCP server name: {server_name!r}")
                if not isinstance(server_version, str) or not server_version.strip():
                    raise MCPStartupError("MCP server version is missing")

                tools = await startup_step(client.list_tools())
                startup_stage("contract_manifest")
                tool_names = {tool.name for tool in tools}
                missing = sorted(REQUIRED_PRODUCT_MCP_TOOLS - tool_names)
                if missing:
                    raise MCPStartupError(
                        "required product MCP tools are missing: " + ", ".join(missing)
                    )
                self._validate_agent_identity_contracts(tools)
                manifest_result = await startup_step(
                    client.call_tool(MCP_PRODUCT_MANIFEST_TOOL, {})
                )
                manifest = self._manifest_result_data(manifest_result)
                self._validate_product_contract_manifest(tools, manifest)
                if not MUTATING_MCP_TOOLS.intersection(tool_names):
                    raise MCPStartupError(
                        "MCP tool catalog is incomplete; safety surface cannot be audited"
                    )

                if asyncio.get_running_loop().time() >= startup_deadline:
                    raise TimeoutError("MCP startup deadline expired")
                self._client = client
                self._status = MCPStatus(
                    status="ready",
                    server_name=server_name,
                    server_version=server_version,
                    protocol_version=protocol_version,
                    tool_count=len(tools),
                    agent_tool_count=len(AGENT_READ_ANALYZE_MCP_TOOLS),
                )
                logger.info(
                    "MCP startup ready server=%s version=%s tools=%d agent_tools=%d",
                    server_name,
                    server_version,
                    len(tools),
                    len(AGENT_READ_ANALYZE_MCP_TOOLS),
                )
                self._monitor_task = asyncio.create_task(
                    self._monitor_connection(),
                    name="controlstudio-mcp-monitor",
                )
            except BaseException as exc:
                self._client = None
                self._active_session_identity = None
                failure = (
                    str(exc) if isinstance(exc, MCPStartupError)
                    else f"MCP startup failed at {stage} ({type(exc).__name__})"
                )
                self._status = MCPStatus(status="unavailable", error=failure)
                logger.exception(
                    "MCP startup failed stage=%s elapsed_ms=%d error_type=%s",
                    stage,
                    int((time.monotonic() - startup_started) * 1000),
                    type(exc).__name__,
                )
                try:
                    await client.__aexit__(type(exc), exc, exc.__traceback__)
                except Exception as cleanup_exc:  # failed entry may rethrow its session error
                    logger.warning(
                        "MCP startup cleanup failed error_type=%s",
                        type(cleanup_exc).__name__,
                    )
                finally:
                    # A later explicit reconnect must also prove the prior child retired.
                    # Never clear this block on a request that merely asks to start again.
                    self._startup_cleanup_incomplete = (
                        startup_probe.transport_observed and not startup_probe.process_retired
                    )
                # Diagnostics must not replace original state/cleanup/failure handling.
                try:
                    startup_probe.stop()
                    logger.warning(
                        "CONTROLSTUDIO_MCP_STARTUP_SAFE %s",
                        json.dumps(startup_probe.failure_summary(stage, exc), separators=(",", ":")),
                    )
                except Exception:
                    pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
                # One initialization-only retry uses the existing owner lifecycle.
                # A failed retry already owns _reconnect_task, so it cannot recurse.
                if (stage == "process_initialize"
                        and isinstance(client, _StartupRetryClient)
                        and client.initialize_timed_out
                        and startup_probe.process_retired
                        and loop.time() < startup_deadline
                        and not self._stopping
                        and (self._reconnect_task is None or self._reconnect_task.done())):
                    self._reconnect_task = asyncio.create_task(
                        self._recover_connection(startup_deadline=startup_deadline),
                        name="controlstudio-mcp-reconnect"
                    )
                if isinstance(exc, MCPStartupError):
                    raise
                raise MCPStartupError(failure) from exc
            finally:
                try:
                    startup_probe.stop()
                except Exception:
                    pass
            return self._status

    async def stop(self) -> None:
        self._stopping = True
        reconnect_task = self._reconnect_task
        self._reconnect_task = None
        if reconnect_task is not None and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)
        async with self._lifecycle_lock:
            client = self._client
            monitor_task = self._monitor_task
            self._client = None
            self._active_session_identity = None
            self._active_controller_endpoint = None
            self._controller_observation_expires_at = None
            self._monitor_task = None
            if monitor_task is not None:
                monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)
            if client is not None:
                await client.__aexit__(None, None, None)
            self._status = MCPStatus(status="stopped")

    async def call_read_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a read/analyze tool from a trusted Host endpoint.

        This path shares the exact same lock as model-selected MCP calls, so two
        browsers or a direct endpoint cannot concurrently drive the XG5000 UI.
        """

        if name not in READ_ANALYZE_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in read-only Host mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        safe_arguments = self._safe_arguments(name, arguments or {})
        try:
            async with self._call_lock:
                result = await client.call_tool(name, safe_arguments)
        except ToolError as exc:
            self._controller_observation_expires_at = None
            payload = _exact_tool_error_payload(str(exc))
            _log_bounded_change_rejection(name, payload)
            raise MCPToolRejectedError(
                f"{name} was rejected by MCP: {exc}", mcp_error_payload=payload
            ) from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._controller_observation_expires_at = None
            self._raise_call_failure(name, exc, error_type=MCPToolError)
        try:
            return self._decode_tool_result(name, result)
        except MCPToolRejectedError as exc:
            _log_bounded_change_rejection(name, exc.mcp_error_payload)
            raise

    def project_tools_connection_status(self) -> dict[str, str | int]:
        # Project only existing connection ownership; no DB or native observation.
        owner = self._reconnect_task
        pending = self._lifecycle_lock.locked() or (owner is not None and not owner.done())
        state = ("ready" if self._status.status == "ready" else
                 "starting" if pending or self._status.status == "starting" else "unavailable")
        return {"state": state, "waitTimeoutMs": max(1, int(self._startup_timeout_seconds * 1000))}

    def request_reconnect(self) -> None:
        """One explicit request shares the existing lifecycle; never replay a tool."""
        if self._status.status == "ready":
            return
        if self._stopping:
            raise MCPTransportError("MCP is stopping")
        if self.project_tools_connection_status()["state"] == "starting":
            return
        self._reconnect_task = asyncio.create_task(
            self._recover_connection(), name="controlstudio-mcp-reconnect"
        )

    async def wait_until_ready(self, *, timeout_seconds: float) -> MCPStatus:
        """Wait only for an already-owned connection; never start or replay it."""
        if (isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and positive")
        deadline = time.monotonic() + min(timeout_seconds, self._startup_timeout_seconds)

        async def wait_current_lifecycle() -> None:
            # Cancellation releases only this waiter, never the connection owner.
            async with self._lifecycle_lock:
                pass

        while self._status.status != "ready":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("MCP readiness wait expired")
            reconnect = self._reconnect_task
            if (reconnect is not None and not reconnect.done()
                    and reconnect is not asyncio.current_task()):
                waiter = asyncio.shield(reconnect)
            elif self._lifecycle_lock.locked():
                waiter = wait_current_lifecycle()
            else:
                # No live owner to wait for: preserve its unavailable/stopped state.
                return self._status
            try:
                await asyncio.wait_for(waiter, timeout=remaining)
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                raise MCPTransportError("MCP connection readiness failed") from exc
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError("MCP readiness wait expired")
        return self._status

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *, private_meta: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Host-owned local workspace tool that is never model-visible."""

        if name not in HOST_WORKSPACE_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host workspace mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPTransportError("MCP is not ready")
        safe_arguments = dict(arguments or {})
        batch_timeout = _editor_batch_sdk_timeout(name, safe_arguments)
        try:
            async with self._call_lock:
                result = await client.call_tool(
                    name,
                    safe_arguments,
                    **({"meta": private_meta} if private_meta is not None else {}),
                    **(
                        {
                            "timeout": (
                                HOST_WORKSPACE_LAUNCH_TRANSPORT_TIMEOUT_SECONDS
                            )
                        }
                        if name == "xg5000.open_project"
                        else {"timeout": 180.0} if name in {"workspace.prepare_native", "workspace.cancel_native_preparation", "workspace.simulator"}
                        else {"timeout": batch_timeout} if batch_timeout is not None else {}
                    ),
                )
        except ToolError as exc:
            payload = _exact_tool_error_payload(str(exc))
            raise MCPToolRejectedError(
                f"{name} was rejected by MCP: {exc}",
                mcp_error_payload=payload,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPTransportError)
        return self._decode_tool_result(name, result)

    async def call_change_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Host-owned change tool that is never exposed to the model."""

        if name not in HOST_CHANGE_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host change mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        try:
            async with self._call_lock:
                result = await client.call_tool(name, dict(arguments or {}))
        except ToolError as exc:
            payload = _exact_tool_error_payload(str(exc), tool_name=name)
            _log_bounded_change_rejection(name, payload)
            raise MCPToolRejectedError(
                f"{name} was rejected by MCP: {exc}",
                mcp_error_payload=payload,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPToolError)
        try:
            return self._decode_tool_result(name, result)
        except MCPToolRejectedError as exc:
            _log_bounded_change_rejection(name, exc.mcp_error_payload)
            raise

    async def call_recovery_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Host-owned recovery bootstrap tool outside the model surface."""

        if name not in HOST_RECOVERY_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host recovery mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        try:
            async with self._call_lock:
                result = await client.call_tool(name, dict(arguments or {}))
        except ToolError as exc:
            payload = _exact_tool_error_payload(str(exc))
            _log_bounded_change_rejection(name, payload)
            raise MCPToolRejectedError(
                f"{name} was rejected by MCP: {exc}",
                mcp_error_payload=payload,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPToolError)
        try:
            return self._decode_tool_result(name, result)
        except MCPToolRejectedError as exc:
            _log_bounded_change_rejection(name, exc.mcp_error_payload)
            raise

    async def call_controller_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        endpoint: dict[str, Any] | None = None,
    ) -> Any:
        """Call one read-only LS controller tool through a Host-owned endpoint."""

        if name not in HOST_CONTROLLER_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host controller mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        safe_arguments = self._controller_arguments(
            name,
            arguments or {},
            endpoint=endpoint,
        )
        try:
            async with self._call_lock:
                result = await client.call_tool(name, safe_arguments)
        except ToolError as exc:
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPToolError)
        return self._decode_tool_result(name, result)

    async def call_restore_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Host-only XG5000 restore-surface observation tool."""

        if name not in HOST_RESTORE_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host restore mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        try:
            async with self._call_lock:
                result = await client.call_tool(name, dict(arguments or {}))
        except ToolError as exc:
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPToolError)
        return self._decode_tool_result(name, result)

    def sign_mutation_authorization(
        self,
        claims: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign exact server-owned mutation claims for one MCP nonce."""

        unsigned = dict(claims)
        unsigned.pop("signature", None)
        serialized = json.dumps(
            unsigned,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(
            self._authorization_signing_key,
            serialized,
            hashlib.sha256,
        ).hexdigest()
        return {**unsigned, "signature": signature}

    async def call_agent_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_tool: Any,
        session_identity: dict[str, Any] | None = None,
    ) -> Any:
        """Execute one broker-authorized Agent proposal through the MCP wire.

        Progress events and model-facing schemas belong to ``ExecutionBroker``.
        This gateway remains the only component that can inject target identity,
        serialize MCP access, and mark a broken transport unavailable.
        """

        if name not in AGENT_READ_ANALYZE_MCP_TOOLS | AGENT_MODAL_MUTATION_MCP_TOOLS:
            raise MCPToolError(f"agent attempted a blocked MCP tool: {name}")
        try:
            safe_arguments = self._safe_arguments(name, arguments, session_identity=session_identity)
            batch_timeout = _editor_batch_sdk_timeout(name, safe_arguments)
            async with self._call_lock:
                if batch_timeout is not None:
                    result = await call_tool(name, safe_arguments, timeout=batch_timeout)
                else:
                    result = await call_tool(name, safe_arguments)
        except ToolError:
            # A validation or domain error is a healthy MCP response. Preserve
            # it for the agent loop without poisoning the transport status.
            if name in AGENT_CONTROLLER_MCP_TOOLS:
                self._controller_observation_expires_at = None
            raise
        except MCPToolError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - MCP SDK raises transport-specific errors
            self._raise_call_failure(name, exc, error_type=MCPTransportError)
        return result

    async def execute_agent_tool(
        self, name: str, arguments: dict[str, Any], *, session_identity: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an allowed Agent tool through the Host-owned MCP client.

        The sidecar protocol bridge uses ``call_agent_tool`` here. This method
        unwraps the result only after the same identity/endpoint injection and
        serialized transport call have completed inside this gateway.
        """

        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPTransportError("MCP is not ready")
        try:
            result = await self.call_agent_tool(
                name,
                arguments,
                call_tool=client.call_tool,
                session_identity=session_identity,
            )
        except ToolError as exc:
            raise MCPToolRejectedError(
                f"{name} was rejected by MCP: {exc}",
                mcp_error_payload=_exact_tool_error_payload(str(exc)),
            ) from exc
        return self._decode_tool_result(name, result)

    def _raise_call_failure(
        self,
        name: str,
        exc: Exception,
        *,
        error_type: type[MCPToolError],
    ) -> None:
        """Separate SDK output-schema faults from actual transport failures."""

        if _is_sdk_output_schema_error(name, exc):
            # SDK RuntimeError text can embed rejected content/schema details.
            # Expose only a bounded classification and keep MCP ready.
            raise MCPToolContractError(
                f"{name} returned content that violates its MCP output schema"
            ) from None
        if (isinstance(exc, McpError) and type(exc.error.code) is int
                and exc.error.code in {-32700, -32600, -32601, -32602, -32603}):
            # Standard JSON-RPC errors are received responses, not evidence of
            # a lost channel. Preserve the caller's failure/uncertainty path;
            # neither raw message/data nor native success is inferred here.
            raise error_type(
                f"{name} returned JSON-RPC error (code={exc.error.code})"
            ) from exc
        failure = _safe_mcp_call_failure(exc)
        logging.getLogger('uvicorn.error.controlstudio_mcp_call').warning(
            'MCP call failure safe: classification=%s', failure)
        self._mark_connection_lost(exc)
        raise error_type(f"{name} {failure}") from exc

    def _safe_arguments(
        self, name: str, arguments: dict[str, Any], *, session_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_arguments = dict(arguments)
        if name in AGENT_CONTROLLER_MCP_TOOLS:
            if not self.controller_observation_current:
                raise MCPToolError(
                    "the agent has no current Host-verified LS controller observation"
                )
            return self._controller_arguments(name, safe_arguments)
        if name == "project.snapshot":
            safe_arguments.pop("workspace_id", None)
            safe_arguments.pop("plan_kind", None)
            safe_arguments.pop("host_transaction_id", None)
            safe_arguments["begin_change_transaction"] = False
        if name == "xg5000.attach":
            requested_profile = safe_arguments.get("access_profile")
            if requested_profile not in {None, "analysis"}:
                raise MCPToolError("the read-only Host permits only the analysis access profile")
            safe_arguments["access_profile"] = "analysis"
        required_identity_fields = AGENT_TOOL_REQUIRED_IDENTITY_FIELDS.get(name)
        if required_identity_fields is not None:
            identity = session_identity if session_identity is not None else self._active_session_identity
            if identity is None:
                raise MCPToolError("the agent has no Host-bound XG5000 engineering session")
            missing = [
                field
                for field in required_identity_fields
                if field not in identity or identity[field] is None or identity[field] == ""
            ]
            if missing:
                raise MCPToolError(
                    "the Host-bound XG5000 engineering session is incomplete: "
                    + ", ".join(missing)
                )
            protected_fields = (
                set(SESSION_IDENTITY_FIELDS)
                | set(WORKSPACE_STATIC_IDENTITY_FIELDS)
                | set(required_identity_fields)
            )
            for field in protected_fields:
                if (
                    field in required_identity_fields
                    and field in safe_arguments
                    and str(safe_arguments[field]) != str(identity[field])
                ):
                    raise MCPToolError(
                        f"agent supplied stale or mismatched session identity: {field}"
                    )
                safe_arguments.pop(field, None)
            for field in required_identity_fields:
                safe_arguments[field] = identity[field]
        return safe_arguments

    def _controller_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        endpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name not in HOST_CONTROLLER_MCP_TOOLS:
            raise MCPToolError(f"tool is not an LS controller observation tool: {name}")
        bound = self._normalize_controller_endpoint(
            endpoint if endpoint is not None else self._active_controller_endpoint
        )
        safe_arguments = dict(arguments)
        for field in CONTROLLER_ENDPOINT_FIELDS:
            if field in safe_arguments and safe_arguments[field] != bound[field]:
                raise MCPToolError(
                    f"agent supplied stale or mismatched controller endpoint: {field}"
                )
            safe_arguments.pop(field, None)
        safe_arguments.update(bound)
        return safe_arguments

    @staticmethod
    def _normalize_controller_endpoint(
        endpoint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(endpoint, dict):
            raise MCPToolError("no Host-bound LS controller endpoint is selected")
        try:
            address = ipaddress.ip_address(endpoint.get("host"))
        except ValueError as exc:
            raise MCPToolError(
                "controller host must be a numeric unicast IPv4 or IPv6 address"
            ) from exc
        if address.is_unspecified or address.is_multicast:
            raise MCPToolError(
                "controller host must be a numeric unicast IPv4 or IPv6 address"
            )
        if not any(
            address.version == network.version and address in network
            for network in _ALLOWED_CONTROLLER_NETWORKS
        ):
            raise MCPToolError(
                "controller host must be in an allowed private controller network"
            )
        port = endpoint.get("port", 2004)
        if isinstance(port, bool) or port != 2004:
            raise MCPToolError("controller port must be the documented XGT FEnet TCP port 2004")
        timeout = endpoint.get("timeout_seconds", 2.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise MCPToolError("controller timeout must be between 0.1 and 10 seconds")
        timeout_seconds = float(timeout)
        if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 10:
            raise MCPToolError("controller timeout must be between 0.1 and 10 seconds")
        return {
            "host": address.compressed,
            "port": port,
            "timeout_seconds": timeout_seconds,
        }

    @staticmethod
    def _validate_agent_identity_contracts(tools: list[Any]) -> None:
        tools_by_name = {tool.name: tool for tool in tools}
        for name, expected_fields in AGENT_TOOL_REQUIRED_IDENTITY_FIELDS.items():
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            schema = tool.inputSchema
            properties = schema.get("properties")
            required = schema.get("required")
            if not isinstance(properties, dict) or not isinstance(required, list):
                raise MCPStartupError(f"{name} has an invalid MCP input schema")
            expected_identity = set(expected_fields)
            relevant_identity = set(SESSION_IDENTITY_FIELDS) | expected_identity
            declared_identity = set(properties).intersection(relevant_identity)
            required_identity = set(required).intersection(relevant_identity)
            if declared_identity != expected_identity or required_identity != expected_identity:
                raise MCPStartupError(
                    f"{name} MCP identity contract changed; expected required fields "
                    + ", ".join(expected_fields)
                )
        modal_contracts = {
            "xg5000.inspect_owned_modal": {
                "properties": set(SESSION_IDENTITY_FIELDS) | {"schema_version"},
                "required": set(SESSION_IDENTITY_FIELDS),
                "read_only": True,
                "mutation_scope": "none",
            },
            "xg5000.cancel_owned_modal": {
                "properties": set(SESSION_IDENTITY_FIELDS)
                | {"snapshot_digest", "button_hwnd", "schema_version"},
                "required": set(SESSION_IDENTITY_FIELDS)
                | {"snapshot_digest", "button_hwnd"},
                "read_only": False,
                "mutation_scope": "owned-modal-cancel-only",
            },
        }
        for name, expected in modal_contracts.items():
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            schema = tool.inputSchema
            properties = schema.get("properties")
            required = schema.get("required")
            meta = getattr(tool, "meta", None)
            if (
                not isinstance(properties, dict)
                or set(properties) != expected["properties"]
                or set(required or ()) != expected["required"]
                or schema.get("additionalProperties") is not False
                or not isinstance(meta, dict)
                or meta.get("controlstudio/agentCallable") is not True
                or meta.get("controlstudio/readOnly") is not expected["read_only"]
                or meta.get("controlstudio/mutationScope") != expected["mutation_scope"]
                or meta.get("controlstudio/controllerWrite") is not False
            ):
                raise MCPStartupError(f"{name} owned-modal contract changed")
            version_schema = properties.get("schema_version")
            if (
                not isinstance(version_schema, dict)
                or version_schema.get("type") != "string"
                or "schema_version" in expected["required"]
            ):
                raise MCPStartupError(f"{name} schema-version contract changed")
            if name == "xg5000.cancel_owned_modal":
                digest_schema = properties.get("snapshot_digest")
                button_schema = properties.get("button_hwnd")
                if (
                    not isinstance(digest_schema, dict)
                    or digest_schema.get("type") != "string"
                    or digest_schema.get("pattern") != "^[0-9a-f]{64}$"
                    or not isinstance(button_schema, dict)
                    or button_schema.get("type") != "integer"
                    or button_schema.get("minimum") != 1
                ):
                    raise MCPStartupError(
                        "xg5000.cancel_owned_modal selection contract changed"
                    )
        for name in AGENT_CONTROLLER_MCP_TOOLS:
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            schema = tool.inputSchema
            properties = schema.get("properties")
            required = schema.get("required")
            if not isinstance(properties, dict) or not isinstance(required, list):
                raise MCPStartupError(f"{name} has an invalid MCP input schema")
            if not set(CONTROLLER_ENDPOINT_FIELDS).issubset(properties):
                raise MCPStartupError(f"{name} MCP endpoint contract is incomplete")
            if "host" not in required:
                raise MCPStartupError(f"{name} MCP endpoint contract must require host")
            meta = getattr(tool, "meta", None)
            if not isinstance(meta, dict) or (
                meta.get("controlstudio/endpointAuthority") != "host"
                or meta.get("controlstudio/readOnly") is not True
                or meta.get("controlstudio/canAuthorizeWrite") is not False
            ):
                raise MCPStartupError(
                    f"{name} MCP endpoint authority or read-only contract changed"
                )
        restore_tool = tools_by_name.get("controller.restore_preflight")
        if restore_tool is not None:
            properties = restore_tool.inputSchema.get("properties")
            required = restore_tool.inputSchema.get("required")
            meta = getattr(restore_tool, "meta", None)
            if (
                not isinstance(properties, dict)
                or set(properties) != {"pid", "hwnd", "schema_version"}
                or set(required or ()) != {"pid", "hwnd"}
                or not isinstance(meta, dict)
                or meta.get("controlstudio/endpointAuthority")
                != "host-selected-xg5000-window"
                or meta.get("controlstudio/readOnly") is not True
                or meta.get("controlstudio/agentCallable") is not False
                or meta.get("controlstudio/canAuthorizeWrite") is not False
            ):
                raise MCPStartupError(
                    "controller restore preflight read-only target contract changed"
                )
        restore_action_fields = {
            "controller.begin_restore_handoff": {
                "pid",
                "hwnd",
                "operation_id",
                "preflight_receipt_digest",
                "preflight_observed_at",
                "profile_digest",
                "timeout_seconds",
                "schema_version",
            },
            "controller.complete_restore_handoff": {
                "pid",
                "hwnd",
                "operation_id",
                "handoff",
                "timeout_seconds",
                "schema_version",
            },
        }
        for name, expected_fields in restore_action_fields.items():
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            meta = getattr(tool, "meta", None)
            properties = tool.inputSchema.get("properties")
            required = tool.inputSchema.get("required")
            if (
                not isinstance(properties, dict)
                or not isinstance(required, list)
                or set(properties) != expected_fields
                or (name == "controller.begin_restore_handoff" and (
                    "preflight_observed_at" not in required
                    or properties["preflight_observed_at"] != {
                        "type": "string", "minLength": 1, "maxLength": 64, "format": "date-time"}
                ))
                or not isinstance(meta, dict)
                or meta.get("controlstudio/endpointAuthority")
                != "host-selected-xg5000-window"
                or meta.get("controlstudio/readOnly") is not False
                or meta.get("controlstudio/agentCallable") is not False
                or meta.get("controlstudio/canAuthorizeWrite") is not False
            ):
                raise MCPStartupError(f"{name} Host-owned restore contract changed")

    @staticmethod
    def _manifest_result_data(result: Any) -> dict[str, Any]:
        if getattr(result, "is_error", False):
            raise MCPStartupError("MCP product contract manifest request failed")
        value = getattr(result, "structured_content", None)
        if value is None:
            value = getattr(result, "data", None)
        if value is None:
            try:
                value = json.loads(MCPGateway._result_text(result))
            except (json.JSONDecodeError, TypeError) as exc:
                raise MCPStartupError("MCP product contract manifest is missing") from exc
        value = MCPGateway._unwrap_success_envelope(value)
        if not isinstance(value, dict):
            raise MCPStartupError("MCP product contract manifest is invalid")
        return value

    @staticmethod
    def _validate_product_contract_manifest(tools: list[Any], manifest: dict[str, Any]) -> None:
        try:
            parsed = MCPProductManifestV1.model_validate(manifest)
        except ValidationError as exc:
            raise MCPStartupError("MCP product contract manifest is invalid") from exc
        if (
            parsed.contract_version != MCP_PRODUCT_CONTRACT_VERSION
            or parsed.manifest_schema != MCP_PRODUCT_MANIFEST_SCHEMA
            or parsed.product != MCP_PRODUCT_NAME
        ):
            raise MCPStartupError("MCP product contract version is incompatible")
        unsigned = {
            "contract_version": parsed.contract_version,
            "manifest_schema": parsed.manifest_schema,
            "product": parsed.product,
            "tools": [tool.model_dump(mode="json") for tool in parsed.tools],
        }
        expected_manifest_digest = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(parsed.manifest_digest, expected_manifest_digest):
            raise MCPStartupError("MCP product contract manifest digest mismatched")
        contracts = {item.tool_name: item for item in parsed.tools}
        tool_names = {tool.name for tool in tools}
        if set(contracts) != tool_names or len(contracts) != len(parsed.tools):
            raise MCPStartupError("MCP product contract tool manifest mismatched")
        for tool in tools:
            contract = contracts[tool.name]
            schema = {
                "inputSchema": getattr(tool, "inputSchema", None) or {},
                "outputSchema": getattr(tool, "outputSchema", None) or None,
            }
            digest = hashlib.sha256(
                json.dumps(
                    schema,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(contract.schema_digest, digest):
                raise MCPStartupError(
                    f"MCP product contract schema digest mismatched for {tool.name}"
                )
        for name in MUTATING_MCP_TOOLS.intersection(tool_names):
            contract = contracts[name]
            if contract.side_effect_class != "mutation":
                raise MCPStartupError(
                    f"MCP mutation contract is incomplete for {name}"
                )
        restore_contract = contracts.get("controller.restore_preflight")
        reviewed_close_contract = contracts.get("xg5000.reviewed_no_save_close")
        if (
            reviewed_close_contract is None
            or reviewed_close_contract.idempotency_behavior != "explicit-confirmation-no-replay"
        ):
            raise MCPStartupError("MCP reviewed close replay contract is incompatible")
        if (
            restore_contract is None
            or restore_contract.required_capability != "read"
            or restore_contract.side_effect_class != "read_only"
            or restore_contract.target_binding_required is not True
            or restore_contract.approval_required is not False
            or restore_contract.idempotency_behavior != "read-only"
        ):
            raise MCPStartupError(
                "MCP controller restore preflight manifest contract is incompatible"
            )
        for name in (
            "controller.begin_restore_handoff",
            "controller.complete_restore_handoff",
        ):
            contract = contracts.get(name)
            if (
                contract is None
                or contract.required_capability != "workspace_write"
                or contract.side_effect_class != "mutation"
                or contract.target_binding_required is not True
                or contract.approval_required is not False
                or contract.idempotency_behavior != "operation-lock"
            ):
                raise MCPStartupError(
                    f"MCP {name} manifest contract is incompatible"
                )
        for apply_tool in (
            "program.apply_ld_patch",
            
        ):
            apply_contract = contracts.get(apply_tool)
            if (
                apply_contract is None
                or not apply_contract.target_binding_required
                or not apply_contract.approval_required
                or apply_contract.idempotency_behavior
                != "single-use-approval-and-journal"
            ):
                raise MCPStartupError(
                    f"MCP apply authorization contract is incompatible for {apply_tool}"
                )

    @staticmethod
    def _unwrap_success_envelope(value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("status") == "ok"
            and "data" in value
        ):
            return value["data"]
        return value

    def _decode_tool_result(self, name: str, result: Any) -> Any:
        if result.is_error:
            structured = getattr(result, "structured_content", None)
            rendered = self._result_text(result)
            payload = _exact_tool_error_payload(
                structured if structured is not None else rendered, tool_name=name,
            )
            display = payload["error"]["message"] if payload is not None else rendered
            raise MCPToolRejectedError(
                f"{name} failed: {display}", mcp_error_payload=payload,
            )
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

    @staticmethod
    def _result_text(result: Any) -> str:
        parts: list[str] = []
        for content in result.content:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)

    async def _monitor_connection(self) -> None:
        while self._client is not None:
            await asyncio.sleep(5)
            client = self._client
            if client is None:
                return
            try:
                async with self._call_lock:
                    await client.ping()
            except Exception as exc:  # noqa: BLE001 - ping may surface transport-specific errors
                self._mark_connection_lost(exc)
                return

    def _mark_connection_lost(self, exc: Exception) -> None:
        previous = self._status
        self._status = MCPStatus(
            status="unavailable",
            server_name=previous.server_name,
            server_version=previous.server_version,
            protocol_version=previous.protocol_version,
            tool_count=previous.tool_count,
            agent_tool_count=previous.agent_tool_count,
            error=f"MCP connection lost ({type(exc).__name__})",
        )
        reconnect_task = self._reconnect_task
        if (
            not self._stopping
            and (reconnect_task is None or reconnect_task.done())
        ):
            self._reconnect_task = asyncio.create_task(
                self._recover_connection(),
                name="controlstudio-mcp-reconnect",
            )

    async def _recover_connection(self, *, startup_deadline: float | None = None) -> None:
        """Replace one broken stdio client without restarting or re-elevating Host."""

        try:
            async with self._lifecycle_lock:
                if self._stopping or self._status.status == "ready":
                    return
                client = self._client
                monitor_task = self._monitor_task
                self._client = None
                self._monitor_task = None
                self._active_session_identity = None
                self._active_controller_endpoint = None
                self._controller_observation_expires_at = None
                if monitor_task is not None and monitor_task is not asyncio.current_task():
                    monitor_task.cancel()
                    await asyncio.gather(monitor_task, return_exceptions=True)
                if client is not None:
                    try:
                        await client.__aexit__(None, None, None)
                    except BaseException:
                        logger.warning("Broken MCP client cleanup failed during reconnect", exc_info=True)
            if self._stopping:
                return
            try:
                await self.start(_startup_deadline=startup_deadline)
            except MCPStartupError:
                logger.exception("Automatic MCP reconnect failed")
        finally:
            if self._reconnect_task is asyncio.current_task():
                self._reconnect_task = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.stop()


def _mcp_environment(*, authorization_secret: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET"] = authorization_secret
    return environment


class MCPDiagnosticSession:
    """One run's journaled target, sharing transport but never global selection."""

    def __init__(self, gateway: MCPGateway, session: dict[str, Any]) -> None:
        missing = [field for field in SESSION_IDENTITY_FIELDS if not session.get(field)]
        if missing:
            raise MCPToolError("incomplete diagnostic session")
        self._gateway = gateway
        self._identity = {field: session[field] for field in SESSION_IDENTITY_FIELDS}

    @property
    def agent_mcp_client(self) -> Client[Any]:
        return self._gateway.agent_mcp_client

    @property
    def agent_session_identity_snapshot(self) -> dict[str, Any]:
        return dict(self._identity)

    @property
    def controller_endpoint_snapshot(self) -> None:
        return None

    async def execute_agent_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in {"xg5000.session_status", "xg5000.inspect_owned_modal", "xg5000.cancel_owned_modal"}:
            raise MCPToolError("tool is not allowed in quarantine diagnostics")
        return await self._gateway.execute_agent_tool(name, arguments, session_identity=self._identity)


def _mcp_stdio_transport(
    *, command: str, args: list[str], environment: dict[str, str], cwd: str | None,
    startup_probe: _MCPStartupProbe | None = None
) -> Any:
    """Use the reviewed Job policy only for the Host's local Windows MCP child."""

    transport_type = ControlStudioWindowsStdioTransport if os.name == "nt" else StdioTransport
    kwargs: dict[str, Any] = {"command": command, "args": args, "env": environment, "cwd": cwd}
    if os.name == "nt":
        kwargs["startup_probe"] = startup_probe
    return transport_type(**kwargs)
