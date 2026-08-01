from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import pytest
from controlstudio_contracts import ExternalToolRequestV1

from controlstudio_host.execution_broker import MCPExecutionBroker
from controlstudio_host.mcp_client import (
    AGENT_CONTROLLER_MCP_TOOLS,
    AGENT_TOOL_NAMES,
    MUTATING_MCP_TOOLS,
    MCPGateway,
    MCPStartupError,
    MCPStatus,
    MCPToolError,
    MCPTransportError,
)

IDENTITY = {
    "session_id": "session-current",
    "pid": 101,
    "hwnd": 202,
    "project_path": "C:\\projects\\machine.xgwx",
    "project_hash": "a" * 64,
}


def test_gateway_accepts_explicit_packaged_mcp_artifact() -> None:
    gateway = MCPGateway(
        command="C:/artifacts/controlstudio-plc-mcp.exe",
        args=["--stdio"],
    )
    assert gateway._command == "C:/artifacts/controlstudio-plc-mcp.exe"
    assert gateway._args == ["--stdio"]
    assert not hasattr(gateway, "_mcp_source")


def test_gateway_default_never_discovers_monorepo_or_sibling_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTROLSTUDIO_MCP_COMMAND", raising=False)
    gateway = MCPGateway()
    assert gateway._command == "controlstudio-plc-mcp"
    assert gateway._args == []


def _catalog_tool(name: str) -> SimpleNamespace:
    if name in AGENT_CONTROLLER_MCP_TOOLS:
        properties = {
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "timeout_seconds": {"type": "number"},
            "variables": {"type": "array", "items": {"type": "string"}},
        }
        required = ["host"]
    else:
        properties = {
            "session_id": {"type": "string"},
            "pid": {"type": "integer"},
            "hwnd": {"type": "integer"},
            "project_path": {"type": "string"},
            "project_hash": {"type": "string"},
            "program_id": {"type": "string"},
        }
        required = (
            ["session_id"]
            if name == "xg5000.session_status"
            else ["session_id", "pid", "hwnd", "project_path", "project_hash"]
        )
    if name == "project.snapshot":
        properties.update(
            {
                "workspace_id": {"type": "string"},
                "begin_change_transaction": {"type": "boolean"},
            }
        )
    return SimpleNamespace(
        name=name,
        description=f"Local public contract for {name}",
        inputSchema={"type": "object", "properties": properties, "required": required},
    )


class _CatalogClient:
    async def list_tools(self) -> list[SimpleNamespace]:
        return [_catalog_tool(name) for name in AGENT_TOOL_NAMES.values()]


@pytest.mark.anyio
async def test_local_public_agent_catalog_is_target_scrubbed() -> None:
    gateway = MCPGateway(command="local-mcp-test-double")
    gateway._client = _CatalogClient()
    gateway._status = MCPStatus(status="ready")
    broker = MCPExecutionBroker(gateway)
    broker.bind_agent_session(IDENTITY)
    gateway.bind_controller_endpoint(
        {"host": "100.64.12.34", "port": 2004, "timeout_seconds": 2},
        observation_current=True,
    )

    catalog = await broker.agent_tool_catalog()

    assert {tool.tool_name for tool in catalog} == set(AGENT_TOOL_NAMES)
    assert not MUTATING_MCP_TOOLS.intersection(
        {AGENT_TOOL_NAMES[name] for name in {tool.tool_name for tool in catalog}}
    )
    for tool in catalog:
        properties = tool.parameters_json_schema.get("properties", {})
        assert not {
            "session_id",
            "pid",
            "hwnd",
            "project_path",
            "project_hash",
        }.intersection(properties)
    snapshot = next(tool for tool in catalog if tool.tool_name == "capture_project_snapshot")
    assert not {"workspace_id", "begin_change_transaction"}.intersection(
        snapshot.parameters_json_schema.get("properties", {})
    )
    controller = next(
        tool for tool in catalog if tool.tool_name == "get_ls_controller_status"
    )
    assert not {"host", "port", "timeout_seconds"}.intersection(
        controller.parameters_json_schema.get("properties", {})
    )


def test_read_only_gateway_forces_analysis_attach_profile() -> None:
    gateway = MCPGateway()
    assert gateway._safe_arguments("xg5000.attach", {"pid": 1234}) == {
        "pid": 1234,
        "access_profile": "analysis",
    }
    with pytest.raises(MCPToolError, match="analysis access profile"):
        gateway._safe_arguments(
            "xg5000.attach", {"pid": 1234, "access_profile": "offline_engineering"}
        )


def test_gateway_authorization_signature_uses_canonical_public_claims() -> None:
    secret = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")
    gateway = MCPGateway(authorization_secret=secret)
    claims = {
        "schema": "controlstudio-change-authorization/v1",
        "authorization_id": "11111111-1111-4111-8111-111111111111",
        "actor_id": "engineer@example.test",
        "transaction_id": "22222222-2222-4222-8222-222222222222",
        "session_id": "session-current",
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "project_identity_digest": "a" * 64,
        "target_identity_digest": "c" * 64,
        "plan_id": "44444444-4444-4444-8444-444444444444",
        "plan_digest": "b" * 64,
        "capability": "offline_mutate",
        "allowed_operation": "program.apply_ld_patch",
        "arguments_digest": "d" * 64,
        "issued_at": "2026-07-29T00:00:00.000+00:00",
        "expires_at": "2026-07-29T00:02:00.000+00:00",
        "nonce": "55555555-5555-4555-8555-555555555555",
    }
    signed = gateway.sign_mutation_authorization(claims)
    decoded_secret = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    expected = hmac.new(
        decoded_secret,
        json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert signed["signature"] == expected


def test_gateway_rejects_incompatible_product_contract_schema_digest() -> None:
    tool = SimpleNamespace(
        name="program.apply_ld_patch",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "pid": {"type": "integer"},
                "hwnd": {"type": "integer"},
                "project_path": {"type": "string"},
                "project_hash": {"type": "string"},
            },
        },
        outputSchema=None,
    )
    schema = {"inputSchema": tool.inputSchema, "outputSchema": None}
    contract = {
        "contract_version": "1.0", "tool_name": tool.name,
        "schema_digest": hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "required_capability": "offline_mutate", "risk_class": "high",
        "side_effect_class": "mutation", "target_binding_required": True,
        "approval_required": True, "reversible": True,
        "idempotency_behavior": "single-use-approval-and-journal",
        "evidence_schema": "controlstudio-mcp-operation-evidence/v1",
        "operation_timeout_seconds": 120, "supported_product": "ls-xg5000",
    }
    unsigned = {
        "contract_version": "1.0",
        "manifest_schema": "controlstudio-mcp-product-manifest/v1",
        "product": "ls-xg5000", "tools": [contract],
    }
    manifest = {
        **unsigned,
        "manifest_digest": hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    MCPGateway._validate_product_contract_manifest([tool], manifest)
    incompatible = {**manifest, "tools": [{**contract, "schema_digest": "f" * 64}]}
    incompatible_unsigned = {key: value for key, value in incompatible.items() if key != "manifest_digest"}
    incompatible["manifest_digest"] = hashlib.sha256(
        json.dumps(incompatible_unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(MCPStartupError, match="schema digest mismatched"):
        MCPGateway._validate_product_contract_manifest([tool], incompatible)


def test_agent_calls_are_pinned_to_host_bound_session() -> None:
    gateway = MCPGateway()
    gateway.bind_agent_session(IDENTITY)
    safe = gateway._safe_arguments("program.read_ld", {"program_id": "main"})
    assert safe == {"program_id": "main", **IDENTITY}
    with pytest.raises(MCPToolError, match="mismatched session identity: pid"):
        gateway._safe_arguments("program.read_ld", {"program_id": "main", "pid": 999})


def test_agent_identity_injection_fails_closed_without_complete_host_identity() -> None:
    gateway = MCPGateway()
    with pytest.raises(MCPToolError, match="no Host-bound"):
        gateway._safe_arguments("program.read_ld", {"program_id": "main"})
    with pytest.raises(MCPToolError, match="incomplete"):
        gateway.bind_agent_session({**IDENTITY, "project_hash": ""})


def test_controller_tools_require_current_host_observation_and_inject_endpoint() -> None:
    gateway = MCPGateway()
    gateway.bind_controller_endpoint({"host": "100.64.12.34", "port": 2004, "timeout_seconds": 2})
    with pytest.raises(MCPToolError, match="no current"):
        gateway._safe_arguments("controller.ls.read", {"data_type": "WORD", "variables": ["%MW100"]})
    gateway.bind_controller_endpoint(
        {"host": "100.64.12.34", "port": 2004, "timeout_seconds": 2}, observation_current=True
    )
    assert gateway._safe_arguments("controller.ls.read", {"data_type": "WORD", "variables": ["%MW100"]}) == {
        "host": "100.64.12.34", "port": 2004, "timeout_seconds": 2.0,
        "data_type": "WORD", "variables": ["%MW100"],
    }
    with pytest.raises(MCPToolError, match="mismatched.*host"):
        gateway._safe_arguments(
            "controller.ls.read", {"host": "192.168.0.99", "data_type": "WORD", "variables": ["%MW100"]}
        )


@pytest.mark.parametrize("host", ["plc.example", "8.8.8.8", "0.0.0.0", "224.0.0.1", "127.0.0.1", "169.254.10.20", "::1", "fe80::1"])
def test_controller_endpoint_rejects_nonlocal_or_nonnumeric_targets(host: str) -> None:
    with pytest.raises(MCPToolError):
        MCPGateway().bind_controller_endpoint({"host": host, "port": 2004, "timeout_seconds": 2})


def test_host_owned_calls_unwrap_the_mcp_success_envelope() -> None:
    assert MCPGateway._unwrap_success_envelope({"status": "ok", "data": {"workspace_id": "workspace-1"}}) == {"workspace_id": "workspace-1"}
    assert MCPGateway._unwrap_success_envelope({"status": "custom"}) == {"status": "custom"}


class _CapturingClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._error = error

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if self._error is not None:
            raise self._error
        return SimpleNamespace(is_error=False, data={"status": "ok", "data": {"program_id": arguments["program_id"]}}, structured_content=None)


def _ready_gateway(client: _CapturingClient) -> MCPGateway:
    gateway = MCPGateway()
    gateway._client = client  # Public request path is exercised against this transport double.
    gateway._status = MCPStatus(status="ready")
    gateway.bind_agent_session(IDENTITY)
    return gateway


@pytest.mark.anyio
async def test_public_external_tool_request_injects_target_and_audits_only_alias() -> None:
    client = _CapturingClient()
    broker = MCPExecutionBroker(_ready_gateway(client))
    events: list[tuple[str, dict[str, Any]]] = []

    async def sink(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    result = await broker.execute_external_tool_request(
        ExternalToolRequestV1(
            request_id="request-1", conversation_id="conversation-1", run_id="run-1",
            tool_name="read_ladder_program", tool_call_id="call-1", arguments={"program_id": "main"},
        ), sink=sink,
    )
    assert result == {"program_id": "main"}
    assert client.calls == [("program.read_ld", {"program_id": "main", **IDENTITY})]
    assert events == [
        ("tool.started", {"tool_name": "read_ladder_program", "tool_call_id": "call-1"}),
        ("tool.completed", {"tool_name": "read_ladder_program", "tool_call_id": "call-1"}),
    ]


@pytest.mark.anyio
async def test_public_external_tool_request_rejects_unknown_alias_and_stale_identity() -> None:
    client = _CapturingClient()
    broker = MCPExecutionBroker(_ready_gateway(client))

    async def sink(_kind: str, _payload: dict[str, Any]) -> None:
        return None

    with pytest.raises(MCPToolError, match="unknown"):
        await broker.execute_external_tool_request(
            ExternalToolRequestV1(request_id="1", conversation_id="c", run_id="r", tool_name="program.apply_ld_patch"), sink=sink
        )
    with pytest.raises(MCPToolError, match="mismatched session identity"):
        await broker.execute_external_tool_request(
            ExternalToolRequestV1(request_id="2", conversation_id="c", run_id="r", tool_name="read_ladder_program", arguments={"program_id": "main", "pid": 999}), sink=sink
        )
    assert client.calls == []


@pytest.mark.anyio
async def test_public_external_tool_request_classifies_transport_failure() -> None:
    broker = MCPExecutionBroker(_ready_gateway(_CapturingClient(error=OSError("pipe disconnected"))))
    events: list[tuple[str, dict[str, Any]]] = []

    async def sink(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    with pytest.raises(MCPTransportError, match="disconnected"):
        await broker.execute_external_tool_request(
            ExternalToolRequestV1(request_id="request", conversation_id="conversation", run_id="run", tool_name="read_ladder_program", arguments={"program_id": "main"}), sink=sink
        )
    assert events[-1] == ("tool.failed", {"tool_name": "read_ladder_program", "failure_kind": "transport"})
