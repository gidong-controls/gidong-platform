"""Small public MCP stdio mock enforcing the Product Contract v1 boundary."""
from __future__ import annotations

import json
import sys
from typing import Any

from .common import canonical_digest, json_line

CONTRACT_VERSION = "1.0"
MANIFEST_SCHEMA = "controlstudio-mcp-product-manifest/v1"
READ_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"scope": {"enum": ["summary"]}}}
MUTATION_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"change": {"type": "string"}, "approval": {"type": "object"}}, "required": ["change"]}
MANIFEST_INPUT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}}
SESSION_FIELDS = ("session_id", "pid", "hwnd", "project_path", "project_hash")
IDENTITY_FIELDS = {
    "xg5000.session_status": ("session_id",),
    "project.list_programs": SESSION_FIELDS,
    "program.focus": SESSION_FIELDS,
    "program.read_ld": SESSION_FIELDS,
    "program.logic_graph": SESSION_FIELDS,
    "program.state_model": SESSION_FIELDS,
    "device.references": SESSION_FIELDS,
    "project.behavior_report": SESSION_FIELDS,
    "project.snapshot": SESSION_FIELDS,
    "evidence.get": ("session_id",),
}
CONTROLLER_TOOLS = {"controller.ls.status", "controller.ls.read"}
MUTATING_TOOLS = {
    "project.create_native", "workspace.open_native", "xg5000.open_project",
    "project.backup", "program.plan_change", "program.apply_ld_patch",
    "project.diff", "project.validate_change", "project.commit_save",
    "project.rollback_change", "change.abort", "change.commit",
}
PRODUCT_TOOLS = (
    "xg5000.session_status", "project.list_programs", "program.focus",
    "program.read_ld", "program.logic_graph", "program.state_model",
    "device.references", "project.behavior_report", "project.snapshot",
    "evidence.get", "controller.ls.status", "controller.ls.read",
    "xg5000.instances", "xg5000.attach", "project.create_native",
    "workspace.open_native", "workspace.status", "xg5000.open_project",
    "project.backup", "program.plan_change", "program.apply_ld_patch",
    "project.diff", "project.validate_change", "project.commit_save",
    "project.rollback_change", "change.transaction_status", "change.abort",
    "change.commit", "change.transaction_lookup",
)


def product_schema(name: str) -> dict[str, Any]:
    properties = {field: {"type": "string"} for field in IDENTITY_FIELDS.get(name, ())}
    required = list(IDENTITY_FIELDS.get(name, ()))
    if name in CONTROLLER_TOOLS:
        properties.update({
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "timeout_seconds": {"type": "number"},
        })
        required.append("host")
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
        "required": required,
    }


def contract(name: str, schema: dict[str, Any], *, mutation: bool) -> dict[str, Any]:
    digest_payload = {"inputSchema": schema, "outputSchema": None}
    return {"contract_version": CONTRACT_VERSION, "tool_name": name, "schema_digest": canonical_digest(digest_payload),
            "required_capability": "workspace.read" if not mutation else "project.change", "risk_class": "low" if not mutation else "high",
            "side_effect_class": "read" if not mutation else "mutation", "target_binding_required": mutation,
            "approval_required": mutation, "reversible": False, "idempotency_behavior": "safe-repeat" if not mutation else "not-executed",
            "evidence_schema": "controlstudio.mock.evidence/v1", "operation_timeout_seconds": 30, "supported_product": "controlstudio-mock"}


INPUT_SCHEMAS = {
    "controlstudio.contract_manifest": MANIFEST_INPUT_SCHEMA,
    "workspace.inspect": READ_SCHEMA,
    "project.apply_mock": MUTATION_SCHEMA,
    **{name: product_schema(name) for name in PRODUCT_TOOLS},
}
TOOLS = [
    contract(name, schema, mutation=name in MUTATING_TOOLS or name == "project.apply_mock")
    for name, schema in INPUT_SCHEMAS.items()
]
for item in TOOLS:
    if item["tool_name"] == "program.apply_ld_patch":
        item["idempotency_behavior"] = "single-use-approval-and-journal"
MANIFEST_BASE = {"contract_version": CONTRACT_VERSION, "manifest_schema": MANIFEST_SCHEMA, "product": "ls-xg5000", "tools": TOOLS}
MANIFEST = {**MANIFEST_BASE, "manifest_digest": canonical_digest(MANIFEST_BASE)}


def rpc_success(identifier: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def rpc_error(identifier: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json_line(data)}], "structuredContent": data, "isError": is_error}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    identifier, method, params = message.get("id"), message.get("method"), message.get("params", {})
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return rpc_success(identifier, {"protocolVersion": str(params.get("protocolVersion") or "2024-11-05"), "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "xg5000-mcp-server", "version": "0.1.0"}})
    if method == "ping":
        return rpc_success(identifier, {})
    if method == "tools/list":
        return rpc_success(identifier, {"tools": [{
            "name": item["tool_name"],
            "description": "ControlStudio public contract mock.",
            "inputSchema": INPUT_SCHEMAS[item["tool_name"]],
            **({"_meta": {"controlstudio/endpointAuthority": "host", "controlstudio/readOnly": True, "controlstudio/canAuthorizeWrite": False}} if item["tool_name"] in CONTROLLER_TOOLS else {}),
        } for item in TOOLS]})
    if method != "tools/call" or not isinstance(params, dict):
        return rpc_error(identifier, -32601, f"unsupported method: {method}")
    name, arguments = params.get("name"), params.get("arguments", {})
    if name == "controlstudio.contract_manifest":
        return rpc_success(identifier, tool_result(MANIFEST))
    if name == "workspace.inspect":
        return rpc_success(identifier, tool_result({"ok": True, "evidence": {"schema": "controlstudio.mock.evidence/v1", "scope": "summary"}}))
    if name == "project.apply_mock":
        if not isinstance(arguments, dict) or not isinstance(arguments.get("approval"), dict):
            return rpc_success(identifier, tool_result({"ok": False, "failure_kind": "approval_required", "message": "mutation refused: Platform approval is required"}, True))
        return rpc_success(identifier, tool_result({"ok": False, "failure_kind": "mock_mutation_disabled", "message": "mock never performs mutations"}, True))
    return rpc_success(identifier, tool_result({"ok": False, "failure_kind": "unknown_tool"}, True))


def main() -> int:
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            reply = handle(message if isinstance(message, dict) else {})
        except Exception as exc:  # noqa: BLE001 - a protocol server must reply rather than terminate
            reply = rpc_error(None, -32603, str(exc))
        if reply is not None:
            sys.stdout.write(json_line(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
