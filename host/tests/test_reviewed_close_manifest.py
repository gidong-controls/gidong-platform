"""Validate the public manifest boundary without installing a private adapter."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from controlstudio_host import mcp_client
from controlstudio_host.mcp_client import MCPGateway, MCPStartupError


def manifest_fixture():
    policies = {
        "controller.restore_preflight": ("read", "read_only", False, "read-only"),
        "controller.begin_restore_handoff": ("workspace_write", "mutation", False, "operation-lock"),
        "controller.complete_restore_handoff": ("workspace_write", "mutation", False, "operation-lock"),
        "program.apply_ld_patch": ("offline_mutate", "mutation", True, "single-use-approval-and-journal"),
        "xg5000.reviewed_no_save_close": ("workspace_write", "mutation", False, "explicit-confirmation-no-replay"),
    }
    schema = {"inputSchema": {"type": "object", "properties": {}}, "outputSchema": None}
    schema_digest = digest(schema)
    tools = [SimpleNamespace(name=name, **schema) for name in policies]
    contracts = [{"contract_version": mcp_client.MCP_PRODUCT_CONTRACT_VERSION,
        "tool_name": name, "schema_digest": schema_digest, "required_capability": capability,
        "risk_class": "high" if approval else "low", "side_effect_class": effect,
        "target_binding_required": True, "approval_required": approval, "reversible": False,
        "idempotency_behavior": replay, "evidence_schema": "controlstudio-mcp-operation-evidence/v1",
        "operation_timeout_seconds": 60, "supported_product": mcp_client.MCP_PRODUCT_NAME}
        for name, (capability, effect, approval, replay) in policies.items()]
    return tools, {"contract_version": mcp_client.MCP_PRODUCT_CONTRACT_VERSION,
        "manifest_schema": mcp_client.MCP_PRODUCT_MANIFEST_SCHEMA,
        "product": mcp_client.MCP_PRODUCT_NAME, "tools": contracts}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("invalid", [None, "read_only", "read-only", "operation-lock"])
def test_reviewed_close_manifest_rejects_false_side_effect_or_replay_policy(invalid):
    tools, manifest = manifest_fixture()
    contract = next(item for item in manifest["tools"]
        if item["tool_name"] == "xg5000.reviewed_no_save_close")
    if invalid == "read_only":
        contract["side_effect_class"] = invalid
    elif invalid is not None:
        contract["idempotency_behavior"] = invalid
    unsigned = {key: manifest[key] for key in ("contract_version", "manifest_schema", "product", "tools")}
    manifest["manifest_digest"] = digest(unsigned)
    if invalid is None:
        MCPGateway._validate_product_contract_manifest(tools, manifest)
    else:
        with pytest.raises(MCPStartupError, match="mutation contract|reviewed close replay contract"):
            MCPGateway._validate_product_contract_manifest(tools, manifest)
