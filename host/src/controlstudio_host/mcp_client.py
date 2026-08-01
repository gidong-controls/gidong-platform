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
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from controlstudio_contracts import MCPProductManifestV1
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from typing_extensions import Self

EXPECTED_SERVER_NAME = "xg5000-mcp-server"
MCP_PRODUCT_CONTRACT_VERSION = "1.0"
MCP_PRODUCT_MANIFEST_SCHEMA = "controlstudio-mcp-product-manifest/v1"
MCP_PRODUCT_NAME = "ls-xg5000"
MCP_PRODUCT_MANIFEST_TOOL = "controlstudio.contract_manifest"
CONTROLLER_OBSERVATION_TTL_SECONDS = 15.0
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

# The first product increment is deliberately read/analyze-only.  A tool missing
# from this set cannot be offered to the model even if the MCP server advertises
# it.  In particular, change, save, simulator, and direct-control tools stay out.
AGENT_TOOL_NAMES: dict[str, str] = {
    "get_xg5000_session_status": "xg5000.session_status",
    "list_project_programs": "project.list_programs",
    "focus_xg5000_program": "program.focus",
    "read_ladder_program": "program.read_ld",
    "build_program_logic_graph": "program.logic_graph",
    "build_program_state_model": "program.state_model",
    "find_device_references": "device.references",
    "build_project_behavior_report": "project.behavior_report",
    "capture_project_snapshot": "project.snapshot",
    "get_session_evidence": "evidence.get",
    "get_ls_controller_status": "controller.ls.status",
    "read_ls_controller_devices": "controller.ls.read",
}
AGENT_CONTROLLER_MCP_TOOLS = frozenset(
    {
        "controller.ls.status",
        "controller.ls.read",
    }
)
AGENT_READ_ANALYZE_MCP_TOOLS = frozenset(AGENT_TOOL_NAMES.values())
HOST_SESSION_MCP_TOOLS = frozenset({"xg5000.instances", "xg5000.attach"})
HOST_WORKSPACE_MCP_TOOLS = frozenset(
    {
        "project.create_native",
        "workspace.open_native",
        "workspace.status",
        "xg5000.open_project",
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
        "project.commit_save",
        "project.rollback_change",
        "change.transaction_status",
        "change.abort",
        "change.commit",
    }
)
HOST_RECOVERY_MCP_TOOLS = frozenset(
    {
        "change.transaction_lookup",
        "xg5000.attach",
    }
)
HOST_CONTROLLER_MCP_TOOLS = AGENT_CONTROLLER_MCP_TOOLS
READ_ANALYZE_MCP_TOOLS = AGENT_READ_ANALYZE_MCP_TOOLS | HOST_SESSION_MCP_TOOLS
REQUIRED_PRODUCT_MCP_TOOLS = (
    READ_ANALYZE_MCP_TOOLS
    | HOST_WORKSPACE_MCP_TOOLS
    | HOST_CHANGE_MCP_TOOLS
    | HOST_RECOVERY_MCP_TOOLS
    | frozenset({MCP_PRODUCT_MANIFEST_TOOL})
)
SESSION_IDENTITY_FIELDS = ("session_id", "pid", "hwnd", "project_path", "project_hash")
AGENT_TOOL_REQUIRED_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "xg5000.session_status": ("session_id",),
    "project.list_programs": SESSION_IDENTITY_FIELDS,
    "program.focus": SESSION_IDENTITY_FIELDS,
    "program.read_ld": SESSION_IDENTITY_FIELDS,
    "program.logic_graph": SESSION_IDENTITY_FIELDS,
    "program.state_model": SESSION_IDENTITY_FIELDS,
    "device.references": SESSION_IDENTITY_FIELDS,
    "project.behavior_report": SESSION_IDENTITY_FIELDS,
    "project.snapshot": SESSION_IDENTITY_FIELDS,
    "evidence.get": ("session_id",),
}
CONTROLLER_ENDPOINT_FIELDS = ("host", "port", "timeout_seconds")

MUTATING_MCP_TOOLS = frozenset(
    {
        "project.create_native",
        "workspace.open_native",
        "xg5000.open_project",
        "project.backup",
        "program.plan_change",
        "program.apply_ld_patch",
        "project.validate_change",
        "project.rollback_change",
        "project.diff",
        "program.insert_blank",
        "program.to_ld",
        "program.check",
        "project.commit_save",
        "change.commit",
        "change.abort",
        "sim.start",
        "sim.write_program",
        "sim.run",
        "sim.runtime_proof",
        "sim.stop",
    }
)


class MCPStartupError(RuntimeError):
    """The bundled MCP process did not satisfy the Host identity contract."""


class MCPToolError(RuntimeError):
    """A direct, read-only MCP call failed."""


class MCPTransportError(MCPToolError):
    """The MCP transport failed after the gateway marked it unavailable."""


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


class MCPGateway:
    """Own one bundled MCP process and serialize every XG5000-facing call.

    The MCP server is launched without its unsafe validation flag.  The Host also
    filters the tool surface before it reaches the model, giving two independent
    barriers against accidental project mutation in this increment.
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
        init_timeout_seconds: float = 20.0,
        authorization_secret: str | None = None,
    ) -> None:
        # Deployments start a version-pinned MCP artifact from an explicit
        # immutable path or PATH. Platform never discovers checkout-relative
        # or sibling MCP source.
        configured_command = command or os.environ.get("CONTROLSTUDIO_MCP_COMMAND")
        if configured_command is not None:
            self._command = configured_command
            self._args = list(args) if args is not None else []
        else:
            self._command = "controlstudio-plc-mcp"
            self._args = list(args) if args is not None else []
        self._cwd = cwd
        self._init_timeout_seconds = init_timeout_seconds
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
        self._active_session_identity = {field: session[field] for field in SESSION_IDENTITY_FIELDS}

    def clear_agent_session(self) -> None:
        self._active_session_identity = None

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

    async def start(self) -> MCPStatus:
        async with self._lifecycle_lock:
            if self._client is not None:
                return self._status

            self._status = MCPStatus(status="starting")
            transport = StdioTransport(
                command=self._command,
                args=self._args,
                env=_mcp_environment(authorization_secret=self._authorization_secret),
                cwd=self._cwd,
            )
            client: Client[Any] = Client(
                transport,
                name="controlstudio-host",
                init_timeout=self._init_timeout_seconds,
            )
            try:
                await client.__aenter__()
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

                tools = await client.list_tools()
                tool_names = {tool.name for tool in tools}
                missing = sorted(REQUIRED_PRODUCT_MCP_TOOLS - tool_names)
                if missing:
                    raise MCPStartupError(
                        "required product MCP tools are missing: " + ", ".join(missing)
                    )
                self._validate_agent_identity_contracts(tools)
                manifest_result = await client.call_tool(MCP_PRODUCT_MANIFEST_TOOL, {})
                manifest = self._manifest_result_data(manifest_result)
                self._validate_product_contract_manifest(tools, manifest)
                if not MUTATING_MCP_TOOLS.intersection(tool_names):
                    raise MCPStartupError(
                        "MCP tool catalog is incomplete; safety surface cannot be audited"
                    )

                self._client = client
                self._status = MCPStatus(
                    status="ready",
                    server_name=server_name,
                    server_version=server_version,
                    protocol_version=protocol_version,
                    tool_count=len(tools),
                    agent_tool_count=len(AGENT_READ_ANALYZE_MCP_TOOLS),
                )
                self._monitor_task = asyncio.create_task(
                    self._monitor_connection(),
                    name="controlstudio-mcp-monitor",
                )
            except BaseException as exc:
                await client.__aexit__(type(exc), exc, exc.__traceback__)
                self._client = None
                self._active_session_identity = None
                self._status = MCPStatus(status="unavailable", error=str(exc))
                if isinstance(exc, MCPStartupError):
                    raise
                raise MCPStartupError(f"failed to start bundled MCP: {exc}") from exc
            return self._status

    async def stop(self) -> None:
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
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:
            self._controller_observation_expires_at = None
            self._mark_connection_lost(exc)
            raise MCPToolError(f"{name} failed because MCP disconnected") from exc
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

    async def call_workspace_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Host-owned local workspace tool that is never model-visible."""

        if name not in HOST_WORKSPACE_MCP_TOOLS:
            raise MCPToolError(f"tool is not allowed in Host workspace mode: {name}")
        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        try:
            async with self._call_lock:
                result = await client.call_tool(name, dict(arguments or {}))
        except ToolError as exc:
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:
            self._mark_connection_lost(exc)
            raise MCPToolError(f"{name} failed because MCP disconnected") from exc
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

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
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:
            self._mark_connection_lost(exc)
            raise MCPToolError(f"{name} failed because MCP disconnected") from exc
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

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
            raise MCPToolError(f"{name} was rejected by MCP: {exc}") from exc
        except Exception as exc:
            self._mark_connection_lost(exc)
            raise MCPToolError(f"{name} failed because MCP disconnected") from exc
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

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
        except Exception as exc:
            self._mark_connection_lost(exc)
            raise MCPToolError(f"{name} failed because MCP disconnected") from exc
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

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
    ) -> Any:
        """Execute one broker-authorized Agent proposal through the MCP wire.

        Progress events and model-facing schemas belong to ``ExecutionBroker``.
        This gateway remains the only component that can inject target identity,
        serialize MCP access, and mark a broken transport unavailable.
        """

        if name not in AGENT_READ_ANALYZE_MCP_TOOLS:
            raise MCPToolError(f"agent attempted a blocked MCP tool: {name}")
        try:
            safe_arguments = self._safe_arguments(name, arguments)
            async with self._call_lock:
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
        except Exception as exc:
            self._mark_connection_lost(exc)
            raise MCPTransportError(f"{name} failed because MCP disconnected") from exc
        return result

    async def execute_agent_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute an Agent-approved read through the Host-owned MCP client.

        Unlike ``call_agent_tool`` this is used by the sidecar protocol bridge,
        not Pydantic AI's in-process MCP toolset.  The method deliberately
        unwraps the result only after the same identity/endpoint injection and
        serialized transport call have completed inside this gateway.
        """

        client = self._client
        if client is None or self._status.status != "ready":
            raise MCPToolError("MCP is not ready")
        result = await self.call_agent_tool(
            name,
            arguments,
            call_tool=client.call_tool,
        )
        if result.is_error:
            raise MCPToolError(f"{name} failed: {self._result_text(result)}")
        if result.data is not None:
            return self._unwrap_success_envelope(result.data)
        if result.structured_content is not None:
            return self._unwrap_success_envelope(result.structured_content)
        text = self._result_text(result)
        try:
            return self._unwrap_success_envelope(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            return text

    def _safe_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        safe_arguments = dict(arguments)
        if name in AGENT_CONTROLLER_MCP_TOOLS:
            if not self.controller_observation_current:
                raise MCPToolError(
                    "the agent has no current Host-verified LS controller observation"
                )
            return self._controller_arguments(name, safe_arguments)
        if name == "project.snapshot":
            safe_arguments.pop("workspace_id", None)
            safe_arguments["begin_change_transaction"] = False
        if name == "xg5000.attach":
            requested_profile = safe_arguments.get("access_profile")
            if requested_profile not in {None, "analysis"}:
                raise MCPToolError("the read-only Host permits only the analysis access profile")
            safe_arguments["access_profile"] = "analysis"
        required_identity_fields = AGENT_TOOL_REQUIRED_IDENTITY_FIELDS.get(name)
        if required_identity_fields is not None:
            identity = self._active_session_identity
            if identity is None:
                raise MCPToolError("the agent has no Host-bound XG5000 engineering session")
            missing = [
                field
                for field in SESSION_IDENTITY_FIELDS
                if field not in identity or identity[field] is None or identity[field] == ""
            ]
            if missing:
                raise MCPToolError(
                    "the Host-bound XG5000 engineering session is incomplete: "
                    + ", ".join(missing)
                )
            for field in SESSION_IDENTITY_FIELDS:
                if field in safe_arguments and str(safe_arguments[field]) != str(identity[field]):
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
            declared_identity = set(properties).intersection(SESSION_IDENTITY_FIELDS)
            required_identity = set(required).intersection(SESSION_IDENTITY_FIELDS)
            expected_identity = set(expected_fields)
            if declared_identity != expected_identity or required_identity != expected_identity:
                raise MCPStartupError(
                    f"{name} MCP identity contract changed; expected required fields "
                    + ", ".join(expected_fields)
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

    @staticmethod
    def _manifest_result_data(result: Any) -> dict[str, Any]:
        if getattr(result, "is_error", False):
            raise MCPStartupError("MCP product contract manifest request failed")
        value = getattr(result, "data", None)
        if value is None:
            value = getattr(result, "structured_content", None)
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
        apply_contract = contracts.get("program.apply_ld_patch")
        if (
            apply_contract is None
            or not apply_contract.target_binding_required
            or not apply_contract.approval_required
            or apply_contract.idempotency_behavior != "single-use-approval-and-journal"
        ):
            raise MCPStartupError("MCP apply authorization contract is incompatible")

    @staticmethod
    def _unwrap_success_envelope(value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("status") == "ok"
            and "data" in value
        ):
            return value["data"]
        return value

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

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.stop()


def _mcp_environment(*, authorization_secret: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["CONTROLSTUDIO_MCP_AUTHORIZATION_SECRET"] = authorization_secret
    return environment
