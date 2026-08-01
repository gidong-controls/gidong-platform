from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ProjectPlanReview(BaseModel):
    conversation_id: str
    message_id: str
    plan_digest: str
    reviewed_section_ids: list[str]
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


class ConversationCreate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class UserMessageCreate(StrictModel):
    content: str = Field(min_length=1, max_length=1_000_000)


class Conversation(BaseModel):
    id: str
    title: str | None
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: dict[str, str] | None = None
    workspace_id: str | None = None
    revision_id: str | None = None
    engineering_session_id: str | None = None
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


class ConversationPage(BaseModel):
    items: list[Conversation]
    total: int
    limit: int
    offset: int


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


class BootstrapFeatures(BaseModel):
    conversations: bool = True
    event_replay: bool = True
    mutations: bool = False
    service_worker: bool = False
    remote_access: bool = False


class BootstrapResponse(BaseModel):
    service: Literal["controlstudio-host"] = "controlstudio-host"
    build_id: str
    api_version: Literal["1"] = "1"
    host_instance_id: str
    canonical_origin: str
    latest_event_sequence: int
    features: BootstrapFeatures
