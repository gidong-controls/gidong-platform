from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from controlstudio_contracts import (
    AuditRecordV1,
    EventEnvelopeV1,
    ExternalToolRequestV1,
    ExternalToolResultV1,
    MCPProductManifestV1,
    MCPProductToolContractV1,
    WorkspaceSourceV1,
    WorkspaceWorkingCopyV1,
)


def test_v1_contract_models_preserve_public_boundaries() -> None:
    event = EventEnvelopeV1(
        event_id="event-1",
        sequence=4,
        occurred_at=datetime.now(timezone.utc),
        trace_id="trace-1",
        conversation_id="conversation-1",
        run_id="run-1",
        operation_id=None,
        event_type="tool.completed",
        payload_schema_version="1.0",
        payload={"tool_name": "project.list_programs"},
    )
    request = ExternalToolRequestV1(
        request_id="request-1",
        conversation_id="conversation-1",
        run_id="run-1",
        tool_name="project.list_programs",
        tool_call_id="call-1",
        arguments={"name_filter": "MAIN"},
    )
    result = ExternalToolResultV1(
        request_id=request.request_id,
        tool_name=request.tool_name,
        status="completed",
    )
    tool = MCPProductToolContractV1(
        tool_name="project.list_programs",
        schema_digest="a" * 64,
        required_capability="Read",
        risk_class="read_only",
        side_effect_class="none",
        target_binding_required=True,
        approval_required=False,
        reversible=True,
        idempotency_behavior="idempotent",
        evidence_schema="project-program-list-v1",
        operation_timeout_seconds=30,
        supported_product="LS XG5000",
    )
    manifest = MCPProductManifestV1(
        manifest_schema="controlstudio-mcp-product-manifest/v1",
        product="LS XG5000",
        manifest_digest="b" * 64,
        tools=[tool],
    )

    assert event.operation_id is None
    assert request.protocol_version == "1.0"
    assert result.failure_kind is None
    assert manifest.tools[0].tool_name == "project.list_programs"

    with pytest.raises(ValidationError):
        ExternalToolRequestV1(
            protocol_version="2.0",
            request_id="request-1",
            conversation_id="conversation-1",
            run_id="run-1",
            tool_name="project.list_programs",
        )


def test_workspace_and_audit_contracts_have_no_raw_target_or_secret_fields() -> None:
    source = WorkspaceSourceV1(
        original_artifact_id="artifact-1",
        original_sha256="a" * 64,
        captured_at=datetime.now(timezone.utc),
        original_state="unchanged",
    )
    working_copy = WorkspaceWorkingCopyV1(
        workspace_id="workspace-1",
        revision_id="revision-1",
        baseline_sha256="a" * 64,
        current_sha256="b" * 64,
        state="modified",
    )
    audit = AuditRecordV1(
        audit_id="audit-1",
        occurred_at=datetime.now(timezone.utc),
        actor_id="user-1",
        action="approval.recorded",
        trace_id="trace-1",
        outcome="accepted",
    )

    assert source.original_artifact_id == "artifact-1"
    assert working_copy.state == "modified"
    assert audit.model_dump()["outcome"] == "accepted"
    assert "approval_secret" not in AuditRecordV1.model_fields
    assert "raw_target_path" not in WorkspaceWorkingCopyV1.model_fields


def test_public_json_schemas_keep_required_agent_event_and_mcp_product_fields() -> None:
    root = Path(__file__).resolve().parents[4]
    expected_required = {
        "schemas/events/v1/event-envelope.schema.json": {
            "event_id",
            "sequence",
            "occurred_at",
            "trace_id",
            "conversation_id",
            "run_id",
            "operation_id",
            "event_type",
            "payload_schema_version",
            "payload",
        },
        "schemas/agent/v1/external-tool-request.schema.json": {
            "protocol_version",
            "request_id",
            "conversation_id",
            "run_id",
            "tool_name",
            "tool_call_id",
            "arguments",
        },
        "schemas/agent/v1/external-tool-result.schema.json": {
            "protocol_version",
            "request_id",
            "tool_name",
            "status",
            "failure_kind",
        },
        "schemas/mcp-product/v1/tool-contract.schema.json": {
            "contract_version",
            "tool_name",
            "schema_digest",
            "required_capability",
            "risk_class",
            "side_effect_class",
            "target_binding_required",
            "approval_required",
            "reversible",
            "idempotency_behavior",
            "evidence_schema",
            "operation_timeout_seconds",
            "supported_product",
        },
        "schemas/mcp-product/v1/tool-manifest.schema.json": {
            "contract_version",
            "manifest_schema",
            "product",
            "manifest_digest",
            "tools",
        },
        "schemas/workspace/v1/workspace-source.schema.json": {
            "original_artifact_id",
            "original_sha256",
            "captured_at",
            "original_state",
        },
        "schemas/workspace/v1/working-copy.schema.json": {
            "workspace_id",
            "revision_id",
            "baseline_sha256",
            "current_sha256",
            "state",
        },
        "schemas/audit/v1/audit-record.schema.json": {
            "audit_id",
            "occurred_at",
            "actor_id",
            "action",
            "trace_id",
            "run_id",
            "operation_id",
            "outcome",
            "details",
        },
    }

    for relative_path, required in expected_required.items():
        schema = json.loads((root / relative_path).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert set(schema["required"]) == required
        assert schema["additionalProperties"] is False
