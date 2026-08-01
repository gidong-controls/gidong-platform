"""Platform-owned execution boundary for Agent Protocol requests.

The broker exposes a serializable catalog only.  Pydantic AI toolsets and raw
MCP clients are intentionally private to neither the Host nor the Agent
protocol: the Host maps each proposal to its existing MCP gateway after it has
bound the active run and target.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Protocol

from controlstudio_contracts import (
    AgentToolDefinitionV1,
    ExternalToolRequestV1,
    ExternalToolResultV1,
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

logger = logging.getLogger(__name__)

ToolEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class ExecutionBroker(Protocol):
    def bind_agent_session(self, session: dict[str, Any]) -> None: ...

    def clear_agent_session(self) -> None: ...

    async def agent_tool_catalog(self) -> list[AgentToolDefinitionV1]: ...

    async def execute_external_tool_request(
        self, request: ExternalToolRequestV1, *, sink: ToolEventSink
    ) -> Any: ...


class MCPExecutionBroker:
    """Map Agent v1 tool proposals to Host-authorized MCP gateway calls."""

    def __init__(self, gateway: MCPGateway) -> None:
        self._gateway = gateway

    def bind_agent_session(self, session: dict[str, Any]) -> None:
        self._gateway.bind_agent_session(session)

    def clear_agent_session(self) -> None:
        self._gateway.clear_agent_session()

    async def agent_tool_catalog(self) -> list[AgentToolDefinitionV1]:
        available = {tool.name: tool for tool in await self._gateway.agent_mcp_client.list_tools()}
        definitions: list[AgentToolDefinitionV1] = []
        for alias, raw_name in AGENT_TOOL_NAMES.items():
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
            result = await self._gateway.execute_agent_tool(raw_name, request.arguments)
        except MCPTransportError:
            await self._emit_failure(sink, request, "transport")
            raise
        except MCPToolError:
            await self._emit_failure(sink, request, "host_policy")
            raise
        except asyncio.CancelledError:
            await self._emit_failure(sink, request, "cancelled")
            raise
        except Exception as exc:
            await self._emit_failure(sink, request, "transport")
            raise MCPToolError("Platform execution broker failed") from exc
        ExternalToolResultV1(request_id=request.request_id, tool_name=request.tool_name, status="completed")
        await self._emit_event(sink, "tool.completed", event_payload)
        return result

    def _public_schema(self, raw_name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        engineering_tool = raw_name in AGENT_TOOL_REQUIRED_IDENTITY_FIELDS
        controller_tool = raw_name in AGENT_CONTROLLER_MCP_TOOLS
        if not engineering_tool and not controller_tool:
            raise MCPStartupError(f"agent tool has no Host target policy: {raw_name}")
        if engineering_tool and not self._gateway.agent_session_bound:
            return None
        if controller_tool and not self._gateway.controller_observation_current:
            return None
        result = deepcopy(schema)
        properties = result.get("properties")
        required = result.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise MCPStartupError(f"{raw_name} has an invalid model-facing input schema")
        blocked = set(SESSION_IDENTITY_FIELDS)
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
        result: dict[str, Any] = {"tool_name": request.tool_name}
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
