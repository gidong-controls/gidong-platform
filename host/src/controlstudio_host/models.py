from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ProjectPlanReview(BaseModel):
    schema_version: Literal["1", "2"] = "1"
    conversation_id: str
    message_id: str
    plan_digest: str
    reviewed_section_ids: list[str]
    reviewed_item_ids: list[str] = Field(default_factory=list)
    project_brief_digest: str | None = None
    attachment_revision_digest: str | None = None
    actor_id: str
    reviewed_at: datetime


class RunState(str, Enum):
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    APPROVED_PENDING_EXECUTION = "APPROVED_PENDING_EXECUTION"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class ChangeRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ChangeTransactionState(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    OBSERVING = "OBSERVING"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ChangeTargetIdentity(BaseModel):
    workspace_id: str
    revision_id: str
    artifact_hash: str
    source_artifact_hash: str
    project_path: str
    vendor: str
    engineering_system: str
    engineering_session_id: str | None = None
    engineering_process_id: int | None = None
    engineering_window_handle: int | None = None


class ChangeApproval(BaseModel):
    transaction_id: str
    decision: ApprovalDecision
    plan_digest: str
    actor_id: str
    decided_at: datetime


class ChangeTransaction(BaseModel):
    id: str
    operation_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    state: ChangeTransactionState
    target: ChangeTargetIdentity
    mutation_scope: str
    risk_level: ChangeRiskLevel
    intent: str
    plan: dict[str, Any]
    plan_digest: str
    requested_by_actor_id: str
    approval_expires_at: datetime
    approval: ChangeApproval | None = None
    result: dict[str, Any]
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime
    execution_started_at: datetime | None = None
    verification_started_at: datetime | None = None
    completed_at: datetime | None = None
    receipt_acknowledged_at: datetime | None = None
    receipt_acknowledged_by: str | None = None


class Conversation(BaseModel):
    id: str
    title: str | None
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: dict[str, Any] | None = None
    workspace_id: str | None = None
    revision_id: str | None = None
    engineering_session_id: str | None = None
    engineering_handoff: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class EngineeringMission(BaseModel):
    conversation_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    engineering_mission: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_by_run_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


class EngineeringAgentCheckpoint(BaseModel):
    conversation_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    mission_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cycle: int = Field(ge=1, le=8)
    checkpoint: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_by_run_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    id: str
    conversation_id: str
    sequence: int
    role: MessageRole
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    project_plan_review: ProjectPlanReview | None = None


class Run(BaseModel):
    id: str
    conversation_id: str
    operation_id: str | None = None
    state: RunState
    request_message_id: str | None
    metadata: dict[str, Any]
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(Conversation):
    messages: list[Message]
    runs: list[Run]


class StoredEvent(BaseModel):
    sequence: int
    conversation_id: str | None
    run_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class HealthResponse(BaseModel):
    service: Literal["controlstudio-host"] = "controlstudio-host"
    build_id: str
    api_version: Literal["1"] = "1"
    status: Literal["ready", "degraded"]
    database: Literal["ready", "unavailable"]
    static_ui: Literal["ready", "unavailable"]
