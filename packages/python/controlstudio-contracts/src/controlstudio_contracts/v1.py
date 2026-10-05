"""Minimal v1 contracts shared across Platform, Agent, and PLC MCP boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CONTRACT_VERSION_V1 = "1.0"
AGENT_PROTOCOL_VERSION_V1 = "1.0"


class AgentManifestV1(BaseModel):
    """Versioned declaration made during the Host/Agent sidecar handshake."""

    protocol_version: Literal["1.0"] = AGENT_PROTOCOL_VERSION_V1
    agent_id: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=50)


class AgentToolDefinitionV1(BaseModel):
    """Model-facing, Host-approved external tool schema.

    ``tool_name`` is an Agent alias, never an MCP wire method. Target identity,
    controller endpoints, and authorization are deliberately absent.
    """

    protocol_version: Literal["1.0"] = AGENT_PROTOCOL_VERSION_V1
    tool_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    parameters_json_schema: dict[str, Any] = Field(default_factory=dict)
    # Host-owned transport patience, never a model argument or permission to
    # extend the underlying controller/native operation's safety deadlines.
    result_timeout_seconds: float = Field(default=150.0, strict=True, gt=0, le=3600)


class EventEnvelopeV1(BaseModel):
    """The stable envelope for product events."""

    event_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0)
    occurred_at: datetime
    trace_id: str = Field(min_length=1, max_length=200)
    conversation_id: str | None = Field(default=None, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    operation_id: str | None = Field(default=None, max_length=200)
    event_type: str = Field(min_length=1, max_length=200)
    payload_schema_version: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSourceV1(BaseModel):
    """Immutable source evidence held by Platform for a user workspace."""

    original_artifact_id: str = Field(min_length=1, max_length=200)
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: datetime
    original_state: Literal["unchanged", "changed", "missing", "unreadable"]


class WorkspaceWorkingCopyV1(BaseModel):
    """A mutable copy whose identity is checked before any execution."""

    workspace_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)
    baseline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["clean", "modified", "quarantined"]


class AuditRecordV1(BaseModel):
    """Append-only Platform audit record; secrets and raw targets are excluded."""

    audit_id: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    actor_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    operation_id: str | None = Field(default=None, max_length=200)
    outcome: Literal["accepted", "rejected", "completed", "failed", "quarantined"]
    details: dict[str, Any] = Field(default_factory=dict)


class ExternalToolRequestV1(BaseModel):
    """An Agent proposal for Platform-owned execution."""

    protocol_version: Literal["1.0"] = CONTRACT_VERSION_V1
    request_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)
    tool_call_id: str | None = Field(default=None, max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExternalToolResultV1(BaseModel):
    """Sanitized execution status returned across the Agent boundary."""

    protocol_version: Literal["1.0"] = CONTRACT_VERSION_V1
    request_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)
    status: Literal["completed", "failed"]
    failure_kind: Literal["tool_error", "host_policy", "cancelled", "transport"] | None = None


class MCPProductToolContractV1(BaseModel):
    """Product-level metadata that is independent of the MCP wire version."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION_V1
    tool_name: str = Field(min_length=1, max_length=200)
    schema_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_capability: str = Field(min_length=1, max_length=100)
    risk_class: str = Field(min_length=1, max_length=100)
    side_effect_class: str = Field(min_length=1, max_length=100)
    target_binding_required: bool
    approval_required: bool
    reversible: bool
    idempotency_behavior: str = Field(min_length=1, max_length=100)
    evidence_schema: str = Field(min_length=1, max_length=100)
    operation_timeout_seconds: int = Field(gt=0, le=3600)
    supported_product: str = Field(min_length=1, max_length=200)


class MCPProductManifestV1(BaseModel):
    """Product-contract negotiation document served by PLC MCP."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION_V1
    manifest_schema: Literal["controlstudio-mcp-product-manifest/v1"]
    product: str = Field(min_length=1, max_length=200)
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    tools: list[MCPProductToolContractV1] = Field(min_length=1, max_length=200)
