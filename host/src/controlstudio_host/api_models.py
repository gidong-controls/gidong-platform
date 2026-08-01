from __future__ import annotations

import ipaddress
import math
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


_ALLOWED_LS_CONTROLLER_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
    )
)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


KnowledgeKindV1 = Literal[
    "manual",
    "standard",
    "project_document",
    "memory",
    "preference",
    "control_pattern",
]
KnowledgeStatusV1 = Literal["proposed", "confirmed", "superseded", "archived"]


ServiceState = Literal[
    "ready",
    "starting",
    "degraded",
    "unavailable",
    "quarantined",
]


class ServiceStatusV1(ApiModel):
    state: ServiceState
    detail: str | None = None
    version: str | None = None


class AgentStatusV1(ServiceStatusV1):
    model_display_name: str | None = None
    planning_ready: bool = False
    engineering_ready: bool = False


class MCPStatusV1(ServiceStatusV1):
    server_id: str | None = None
    tool_count: int | None = None
    agent_tool_count: int | None = None


class EngineeringSystemStatusV1(ServiceStatusV1):
    vendor: str = "ls"
    application: str = "XG5000"
    process_id: int | None = None
    session_id: str | None = None
    session_verified: bool = False
    read_ready: bool = False
    identity_assurance: Literal[
        "strong_readback",
        "controlled_launch_read_only",
        "unverified",
    ] = "unverified"


def _numeric_unicast_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise ValueError("host must be a numeric unicast IPv4 or IPv6 address") from exc
    if address.is_unspecified or address.is_multicast:
        raise ValueError("host must be a numeric unicast IPv4 or IPv6 address")
    if not any(
        address.version == network.version and address in network
        for network in _ALLOWED_LS_CONTROLLER_NETWORKS
    ):
        raise ValueError("host must be in an RFC1918, CGNAT, or IPv6 ULA controller network")
    return address.compressed


class LSControllerEndpointV1(ApiModel):
    host: str
    port: int
    transport: Literal["tcp"] = "tcp"
    protocol: Literal["XGT_FENET"] = "XGT_FENET"


class LSControllerModuleV1(ApiModel):
    base: int
    slot: int


class LSControllerRuntimeStatusV1(ApiModel):
    mode: str
    state_flags: list[str]
    system_state_raw: int
    padt_connection_flag: int
    configuration_error: int
    configuration_warning: int


class LSControllerProvisionalIdentityV1(ApiModel):
    vendor: Literal["LS ELECTRIC"]
    protocol: Literal["XGT_FENET"]
    cpu_family: str | None = None
    cpu_info_code: int
    cpu_type_flag: int
    os_version: str
    module: LSControllerModuleV1
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_strength: str
    strong_enough_for_write: Literal[False]
    write_authorization_reason: str


class LSControllerObservationV1(ApiModel):
    schema_version: str
    operation: Literal["controller.ls.status"]
    endpoint: LSControllerEndpointV1
    observed_at: datetime
    read_only: Literal[True]
    status: LSControllerRuntimeStatusV1
    provisional_identity: LSControllerProvisionalIdentityV1


class LSControllerConnectionStatusV1(ServiceStatusV1):
    vendor: Literal["ls"] = "ls"
    protocol: Literal["XGT_FENET"] = "XGT_FENET"
    endpoint: LSControllerEndpointV1 | None = None
    timeout_seconds: float | None = None
    read_only: Literal[True] = True
    strong_enough_for_write: Literal[False] = False
    observed_at: datetime | None = None
    provisional_identity: LSControllerProvisionalIdentityV1 | None = None


class WorkspaceSummaryV1(ApiModel):
    workspace_id: str
    display_name: str
    source: Literal["local"]
    revision: str | None = None
    artifact_hash: str | None = None
    vendor: str = "ls"
    cpu_model: str | None = None


class ProductFeatureStatusV1(ApiModel):
    feature_id: Literal[
        "new_project_planning",
        "local_project_analysis",
        "program_focus",
        "native_project_create",
        "online_observe",
        "offline_mutate",
        "controller_project_restore",
        "online_modify",
        "multi_vendor",
    ]
    label: str
    maturity: Literal["supported", "preview", "planned"]
    verification: Literal[
        "real_runtime",
        "real_xg5000",
        "isolated_harness",
        "fake_only",
        "not_verified",
    ]
    current_ready: bool
    detail: str


class HostStatusV1(ApiModel):
    schema_version: Literal["1"] = "1"
    host: ServiceStatusV1
    agent: AgentStatusV1
    mcp: MCPStatusV1
    engineering_system: EngineeringSystemStatusV1
    controller: LSControllerConnectionStatusV1
    workspace: WorkspaceSummaryV1 | None = None
    capabilities: list[str]
    feature_readiness: list[ProductFeatureStatusV1] = Field(default_factory=list)
    active_operation: dict[str, Any] | None = None


class BootstrapV1(ApiModel):
    schema_version: Literal["1"] = "1"
    status: HostStatusV1
    csrf_token: str | None = None
    client_session_id: str
    last_event_sequence: int


class ProjectPlanItemV1(ApiModel):
    item_id: str
    text: str
    status: Literal[
        "confirmed",
        "proposed",
        "unknown",
        "conflicting",
        "not_applicable",
    ]
    source: Literal["user", "agent"]
    blocking: bool
    verification_method: str | None = None
    knowledge_refs: list[str] = Field(default_factory=list, max_length=50)


class ProjectPlanSectionV1(ApiModel):
    section_id: str
    title: str
    items: list[ProjectPlanItemV1]


class ProjectPlanOpenDecisionV1(ApiModel):
    decision_id: str
    question: str
    status: Literal["unknown"]
    source: Literal["agent"]
    blocking: Literal[True]
    verification_method: str | None = None


class ProjectPlanCompletenessV1(ApiModel):
    total_items: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    proposed: int = Field(ge=0)
    unknown: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    blocking: int = Field(ge=0)


class ProjectPlanArtifactV1(ApiModel):
    schema_version: Literal["1"] = "1"
    project_name: str
    status: Literal["needs_clarification", "ready_for_review"]
    summary: str
    objective: str
    sections: list[ProjectPlanSectionV1]
    open_decisions: list[ProjectPlanOpenDecisionV1]
    completeness: ProjectPlanCompletenessV1


class ProjectPlanReviewV1(ApiModel):
    message_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # A pre-v12 review is returned with an empty scope so clients can display
    # the conversation while treating that legacy acknowledgement as invalid.
    reviewed_section_ids: list[str] = Field(default_factory=list, max_length=100)
    actor_id: str
    reviewed_at: datetime


class ConversationMessageV1(ApiModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    run_id: str | None = None
    operation_id: str | None = None
    status: Literal["streaming", "complete", "failed"] = "complete"
    workflow: Literal["engineering", "new_project_planning"] | None = None
    plan_status: Literal["needs_clarification", "ready_for_review"] | None = None
    project_name: str | None = None
    plan_artifact: ProjectPlanArtifactV1 | None = None
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_review: ProjectPlanReviewV1 | None = None


class ProjectBriefV1(ApiModel):
    project_name: str = Field(min_length=1, max_length=200)
    machine_purpose: str = Field(min_length=1, max_length=10_000)
    sequence: str = Field(default="", max_length=20_000)
    hardware_and_io: str = Field(default="", max_length=20_000)
    safety_and_interlocks: str = Field(default="", max_length=20_000)
    operating_modes: str = Field(default="", max_length=20_000)
    performance_and_acceptance: str = Field(default="", max_length=20_000)


class EngineeringTargetBindingV1(ApiModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    engineering_session_id: str = Field(min_length=1, max_length=256)


class ConversationV1(ApiModel):
    conversation_id: str
    title: str
    workflow: Literal["engineering", "new_project_planning"]
    project_brief: ProjectBriefV1 | None = None
    engineering_target: EngineeringTargetBindingV1 | None = None
    messages: list[ConversationMessageV1]
    updated_at: datetime


class ConversationSummaryV1(ApiModel):
    conversation_id: str
    title: str
    workflow: Literal["engineering", "new_project_planning"]
    engineering_target: EngineeringTargetBindingV1 | None = None
    updated_at: datetime


class ConversationPageV1(ApiModel):
    items: list[ConversationSummaryV1]
    total: int
    limit: int
    offset: int


class ConversationCreateV1(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: ProjectBriefV1 | None = None

    @model_validator(mode="after")
    def validate_workflow_contract(self) -> ConversationCreateV1:
        if self.workflow == "new_project_planning" and self.project_brief is None:
            raise ValueError("projectBrief is required for new_project_planning")
        if self.workflow == "engineering" and self.project_brief is not None:
            raise ValueError("projectBrief is only valid for new_project_planning")
        return self


class SendMessageRequestV1(ApiModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    operation_id: str = Field(min_length=8, max_length=128)
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: ProjectBriefV1 | None = None
    change_request_scope: (
        Literal[
            "analysis_only",
            "offline_candidate_revision",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_project_brief_workflow(self) -> SendMessageRequestV1:
        if self.workflow == "engineering" and self.project_brief is not None:
            raise ValueError("projectBrief is only valid for new_project_planning")
        if self.workflow == "new_project_planning" and self.change_request_scope is not None:
            raise ValueError("changeRequestScope is only valid for engineering messages")
        return self

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value):
            raise ValueError("operation_id contains invalid characters")
        return value


class SendMessageResponseV1(ApiModel):
    run_id: str
    operation_id: str
    state: str


class ProjectPlanReviewRequestV1(ApiModel):
    message_id: str = Field(min_length=1, max_length=256)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_section_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("reviewed_section_ids")
    @classmethod
    def validate_reviewed_section_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("reviewedSectionIds contains an invalid section id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedSectionIds must not contain duplicates")
        return value


class ProjectPlanReviewResponseV1(ApiModel):
    review: ProjectPlanReviewV1
    review_recorded: bool


class BrowserSessionV1(ApiModel):
    session_token: str
    expires_at: datetime


class LauncherHandoffV1(ApiModel):
    handoff_ticket: str
    expires_at: datetime


class RunCancellationV1(ApiModel):
    run_id: str
    state: str
    terminal: bool
    idempotent: bool
    detail: str | None = None


VerificationScope = Literal["offline_native_check_only"]
ChangeMode = Literal["offline_working_copy_revision"]
ChangeRequestScope = Literal[
    "offline_candidate_revision",
    "permanent_offline_revision",
]
OfflineNativeValidationStep = Literal[
    "structural_diff",
    "xg5000_program_check",
    "project_save",
]
OFFLINE_NATIVE_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
)

ChangeCoverageArea = Literal[
    "target_identity",
    "value_semantics",
    "direct_impact",
    "indirect_impact",
    "modes",
    "runtime",
    "external_integrations",
    "safety",
    "validation",
    "rollback",
]
ChangeCoverageStatus = Literal[
    "confirmed",
    "not_applicable_with_reason",
    "unknown_blocking",
]
REQUIRED_CHANGE_COVERAGE_AREAS = (
    "target_identity",
    "value_semantics",
    "direct_impact",
    "indirect_impact",
    "modes",
    "runtime",
    "external_integrations",
    "safety",
    "validation",
    "rollback",
)


class ChangeCoverageItemV1(ApiModel):
    area: ChangeCoverageArea
    status: ChangeCoverageStatus
    summary: str = Field(min_length=1, max_length=4_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coverage summary must not be blank")
        return normalized

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("coverage evidenceRefs must not contain blank values")
        return normalized


class ChangePlanV1(ApiModel):
    # Defaults preserve read compatibility with transactions created before the
    # coverage contract existed. New transactions must pass
    # validate_executable_contract() and cannot rely on these defaults.
    change_mode: ChangeMode = "offline_working_copy_revision"
    request_scope: ChangeRequestScope = "permanent_offline_revision"
    target: dict[str, Any]
    current: dict[str, Any]
    proposed: dict[str, Any]
    impact: list[str] = Field(default_factory=list, max_length=200)
    risks: list[str] = Field(default_factory=list, max_length=200)
    assumptions: list[str] = Field(default_factory=list, max_length=200)
    blocking_unknowns: list[str] = Field(default_factory=list, max_length=200)
    alternatives: list[str] = Field(default_factory=list, max_length=200)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)
    observation_signals: list[str] = Field(default_factory=list, max_length=200)
    stop_conditions: list[str] = Field(default_factory=list, max_length=200)
    coverage: list[ChangeCoverageItemV1] = Field(
        default_factory=list,
        max_length=len(REQUIRED_CHANGE_COVERAGE_AREAS),
    )
    verification_scope: VerificationScope = "offline_native_check_only"
    validation: list[OfflineNativeValidationStep] = Field(
        default_factory=lambda: list(OFFLINE_NATIVE_VALIDATION_STEPS),
        min_length=3,
        max_length=3,
    )
    manual_follow_up: list[str] = Field(default_factory=list, max_length=200)
    rollback: list[str] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_native_operand_plan(self) -> ChangePlanV1:
        program_id = self.target.get(
            "programId",
            self.target.get("program_id"),
        )
        token_offset = self.target.get(
            "tokenOffset",
            self.target.get("token_offset"),
        )
        current_value = self.current.get("value", self.current.get("operand"))
        proposed_value = self.proposed.get("value", self.proposed.get("operand"))
        if not isinstance(program_id, str) or not program_id:
            raise ValueError("plan.target.programId is required")
        if not isinstance(token_offset, int) or token_offset < 0:
            raise ValueError("plan.target.tokenOffset must be a non-negative integer")
        if not isinstance(current_value, str) or not current_value:
            raise ValueError("plan.current.value is required")
        if not isinstance(proposed_value, str) or not proposed_value:
            raise ValueError("plan.proposed.value is required")
        if tuple(self.validation) != OFFLINE_NATIVE_VALIDATION_STEPS:
            raise ValueError(
                "offline_native_check_only must execute structural_diff, "
                "xg5000_program_check, and project_save in that order"
            )
        return self

    def validate_executable_contract(self) -> None:
        """Reject an incomplete plan before it can become a transaction.

        ChangePlanV1 is also used to deserialize historical transaction
        receipts. Keeping deserialization tolerant avoids hiding recovery
        evidence, while every new transaction calls this stricter gate.
        """

        explicitly_required_fields = {
            "change_mode",
            "request_scope",
            "impact",
            "risks",
            "assumptions",
            "blocking_unknowns",
            "alternatives",
            "acceptance_criteria",
            "observation_signals",
            "stop_conditions",
            "coverage",
            "verification_scope",
            "validation",
            "manual_follow_up",
            "rollback",
        }
        missing_explicit_fields = sorted(
            explicitly_required_fields.difference(self.model_fields_set)
        )
        if missing_explicit_fields:
            raise ValueError(
                "plan is missing explicit executable-contract fields: "
                + ", ".join(missing_explicit_fields)
            )

        required_text_lists = {
            "impact": self.impact,
            "risks": self.risks,
            "assumptions": self.assumptions,
            "alternatives": self.alternatives,
            "acceptanceCriteria": self.acceptance_criteria,
            "observationSignals": self.observation_signals,
            "stopConditions": self.stop_conditions,
            "manualFollowUp": self.manual_follow_up,
            "rollback": self.rollback,
        }
        for field_name, values in required_text_lists.items():
            if not values:
                raise ValueError(f"plan.{field_name} must contain at least one explicit item")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"plan.{field_name} must contain only non-empty text items")

        if self.blocking_unknowns:
            raise ValueError("plan.blockingUnknowns must be empty before transaction creation")

        coverage_by_area: dict[str, ChangeCoverageItemV1] = {}
        for item in self.coverage:
            if item.area in coverage_by_area:
                raise ValueError(f"plan.coverage contains duplicate area: {item.area}")
            coverage_by_area[item.area] = item
        missing_areas = [
            area for area in REQUIRED_CHANGE_COVERAGE_AREAS if area not in coverage_by_area
        ]
        if missing_areas:
            raise ValueError("plan.coverage is missing required areas: " + ", ".join(missing_areas))
        for area in REQUIRED_CHANGE_COVERAGE_AREAS:
            item = coverage_by_area[area]
            if item.status == "unknown_blocking":
                raise ValueError(
                    f"plan.coverage[{area}] is unknown_blocking and cannot be approved"
                )
            if item.status == "confirmed" and not item.evidence_refs:
                raise ValueError(f"plan.coverage[{area}] confirmed status requires evidenceRefs")

        current_value = self.current.get("value", self.current.get("operand"))
        proposed_value = self.proposed.get("value", self.proposed.get("operand"))
        assert isinstance(current_value, str)
        assert isinstance(proposed_value, str)
        try:
            current_length = len(current_value.encode("utf-16-le"))
            proposed_length = len(proposed_value.encode("utf-16-le"))
        except UnicodeEncodeError as exc:
            raise ValueError("plan current/proposed operands must be valid UTF-16 text") from exc
        if current_length != proposed_length:
            raise ValueError(
                "plan current/proposed native operands must have equal UTF-16 byte length"
            )


class CreateChangeTransactionRequestV1(ApiModel):
    operation_id: str = Field(min_length=8, max_length=128)
    conversation_id: str | None = None
    run_id: str | None = None
    intent: str = Field(min_length=1, max_length=10_000)
    risk_level: Literal["low", "medium", "high", "critical"]
    plan: ChangePlanV1
    approval_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value):
            raise ValueError("operation_id contains invalid characters")
        return value


class ChangeDecisionRequestV1(ApiModel):
    decision: Literal["approve", "reject"]
    expected_plan_digest: str = Field(min_length=64, max_length=64)

    @field_validator("expected_plan_digest")
    @classmethod
    def validate_expected_plan_digest(cls, value: str) -> str:
        normalized = value.casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("expected_plan_digest must be a SHA-256 value")
        return normalized


class ExecuteChangeTransactionRequestV1(ApiModel):
    expected_plan_digest: str = Field(min_length=64, max_length=64)

    @field_validator("expected_plan_digest")
    @classmethod
    def validate_expected_plan_digest(cls, value: str) -> str:
        normalized = value.casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("expected_plan_digest must be a SHA-256 value")
        return normalized


class ReconcileChangeTransactionRequestV1(ExecuteChangeTransactionRequestV1):
    """Explicit XG5000 window selected for recovery-only reattachment."""

    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)


class ChangeTargetIdentityV1(ApiModel):
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


class ChangeApprovalV1(ApiModel):
    decision: Literal["approve", "reject"]
    plan_digest: str
    actor_id: str
    decided_at: datetime


class ChangeTransactionV1(ApiModel):
    transaction_id: str
    operation_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    state: str
    target_identity: ChangeTargetIdentityV1
    mutation_scope: str
    risk_level: Literal["low", "medium", "high", "critical"]
    intent: str
    plan: ChangePlanV1
    plan_digest: str
    requested_by_actor_id: str
    approval_expires_at: datetime
    approval: ChangeApprovalV1 | None = None
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


class ChangeTransactionCreatedV1(ApiModel):
    transaction: ChangeTransactionV1
    created: bool


class ChangeDecisionResponseV1(ApiModel):
    transaction: ChangeTransactionV1
    decision_recorded: bool


class ChangeExecutionResponseV1(ApiModel):
    transaction: ChangeTransactionV1
    execution_started: bool


class ChangeReconciliationResponseV1(ApiModel):
    transaction: ChangeTransactionV1
    reconciled: bool


class ChangeReceiptAcknowledgementV1(ApiModel):
    transaction: ChangeTransactionV1
    acknowledgement_recorded: bool


class ModelSettingsV1(ApiModel):
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    base_url: str
    model: str
    custom_header_names: list[str]
    timeout_seconds: float
    has_api_key: bool
    external_endpoint: bool
    updated_at: datetime | None = None


class SaveModelSettingsRequestV1(ApiModel):
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=2048)
    custom_headers: dict[str, str] | None = None
    timeout_seconds: float = Field(default=60, ge=1, le=300)
    external_model_consent: bool = False
    insecure_http_consent: bool = False


class ModelCapabilityFlagsV1(ApiModel):
    completion: bool
    streaming: bool
    tool_calling: bool
    tool_result_continuation: bool
    structured_output: bool


class ModelCapabilityResultV1(ApiModel):
    success: bool
    tested_at: datetime
    capabilities: ModelCapabilityFlagsV1
    message: str


class XG5000InstancesV1(ApiModel):
    status: Literal["ready"]
    instances: list[dict[str, Any]]


class ReviewedProjectPlanBindingV1(ApiModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_section_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("reviewed_section_ids")
    @classmethod
    def validate_reviewed_section_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("reviewedSectionIds contains an invalid section id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedSectionIds must not contain duplicates")
        return value


class CreateNativeProjectRequestV1(ApiModel):
    operation_id: str
    project_name: str = Field(min_length=1, max_length=80)
    workspace_parent: str = Field(min_length=1, max_length=32_768)
    plc_series: str = Field(min_length=1, max_length=32)
    cpu_model: str = Field(min_length=1, max_length=64)
    program_name: str = Field(default="MAIN", min_length=1, max_length=80)
    language: str = Field(default="LD", min_length=1, max_length=32)
    description: str = Field(default="", max_length=1024)
    xg5000_pid: int = Field(gt=0)
    xg5000_hwnd: int = Field(gt=0)
    reviewed_plan: ReviewedProjectPlanBindingV1

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        try:
            normalized = str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("operationId must be a UUID") from exc
        if normalized != value:
            raise ValueError("operationId must use canonical UUID form")
        return normalized


class NativeProjectOperationV1(ApiModel):
    native_project_id: str
    operation_id: str
    state: Literal[
        "allocated",
        "executing",
        "completed",
        "failed",
        "quarantined",
    ]
    request_digest: str
    actor_id: str
    request: dict[str, Any]
    plan_digest: str | None = None
    destination_directory: str
    result: dict[str, Any]
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class NativeProjectCreatedV1(ApiModel):
    operation: NativeProjectOperationV1
    created: bool


class ReconcileNativeProjectRequestV1(ApiModel):
    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    expected_project_path: str = Field(min_length=1, max_length=32_768)
    expected_project_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class NativeProjectReconciliationV1(ApiModel):
    operation: NativeProjectOperationV1
    reconciled: bool


class ConnectLSControllerRequestV1(ApiModel):
    host: str = Field(min_length=1, max_length=128)
    port: Literal[2004] = 2004
    timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return _numeric_unicast_host(value)

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout_seconds(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value


LSControllerDataType = Literal["BIT", "BYTE", "WORD", "DWORD", "LWORD"]


class ReadLSControllerRequestV1(ApiModel):
    data_type: LSControllerDataType
    variables: list[str] = Field(min_length=1, max_length=16)

    @field_validator("data_type", mode="before")
    @classmethod
    def normalize_data_type(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        pattern = re.compile(r"^%[A-Za-z][XxBbWwDdLl][A-Za-z0-9.]{0,13}$")
        for variable in value:
            item = variable.strip()
            if not pattern.fullmatch(item):
                raise ValueError(f"invalid LS direct variable name: {variable}")
            normalized.append(item)
        if len({item.casefold() for item in normalized}) != len(normalized):
            raise ValueError("variables must not contain duplicates")
        return normalized


class LSControllerValueV1(ApiModel):
    variable: str
    raw_hex: str = Field(pattern=r"^[0-9A-F]+$")
    unsigned_value: int = Field(ge=0)


class LSControllerReadV1(ApiModel):
    schema_version: str
    operation: Literal["controller.ls.read"]
    endpoint: LSControllerEndpointV1
    observed_at: datetime
    data_type: LSControllerDataType
    values: list[LSControllerValueV1] = Field(min_length=1, max_length=16)
    read_only: Literal[True]
    strong_enough_for_write: Literal[False]


class OpenLocalWorkspaceRequestV1(ApiModel):
    source_path: str = Field(min_length=1, max_length=32_768)
    expected_source_hash: str | None = Field(default=None, min_length=64, max_length=64)
    open_in_xg5000: bool = True
    timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator("expected_source_hash")
    @classmethod
    def validate_expected_source_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("expected_source_hash must be a SHA-256 value")
        return normalized


class XG5000ProjectFileSelectionV1(ApiModel):
    selected: bool
    path: str | None = Field(default=None, max_length=32_768)

    @model_validator(mode="after")
    def validate_selection(self) -> XG5000ProjectFileSelectionV1:
        if self.selected and not self.path:
            raise ValueError("path is required when selected is true")
        if not self.selected and self.path is not None:
            raise ValueError("path must be absent when selected is false")
        return self


class ResumeLocalWorkspaceRequestV1(ApiModel):
    timeout_seconds: float = Field(default=30, gt=0, le=120)


class LocalWorkspaceSourceV1(ApiModel):
    original_path: str
    captured_path: str
    sha256: str
    original_state: Literal["unchanged", "changed", "missing", "unreadable"]
    original_current_sha256: str | None = None


class LocalWorkingCopyV1(ApiModel):
    path: str
    baseline_sha256: str
    current_sha256: str
    state: Literal["clean", "modified"]


class LocalWorkspaceV1(ApiModel):
    workspace_id: str
    revision_id: str
    source_kind: Literal["local_file"]
    vendor: Literal["ls"]
    engineering_system: Literal["XG5000"]
    created_at: datetime
    source: LocalWorkspaceSourceV1
    working_copy: LocalWorkingCopyV1


class XG5000ProjectLaunchV1(ApiModel):
    operation: Literal["xg5000.open_project"]
    verified: bool
    workspace_id: str
    working_path: str
    working_hash: str
    launch_pid: int
    window_detected: bool
    project_identity_verified_against_selected_pid: bool
    project_identity_read_only_bound_to_selected_pid: bool = False
    identity_assurance: Literal[
        "strong_readback",
        "controlled_launch_read_only",
        "unverified",
    ] = "unverified"
    instance: dict[str, Any] | None = None
    observed_instances: list[dict[str, Any]]
    detail: str


class EngineeringSessionV1(ApiModel):
    schema_version: str | None = None
    session_id: str
    pid: int
    hwnd: int
    stage: str
    target: str | None = None
    project_path: str
    project_hash: str
    plc_policy: str | None = None
    capabilities: dict[str, Any] | None = None
    authorization: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    metadata: dict[str, Any]


class EngineeringProgramV1(ApiModel):
    program_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    name: str = Field(min_length=1, max_length=512)
    language: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z][A-Za-z0-9_+-]*$",
    )

    @field_validator("name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(not character.isprintable() for character in normalized):
            raise ValueError("program name must be printable text")
        return normalized

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.strip().upper()


class EngineeringProgramsV1(ApiModel):
    programs: list[EngineeringProgramV1]


class EngineeringProgramFocusV1(ApiModel):
    status: Literal["focused"] = "focused"
    verified: Literal[True]
    read_only: Literal[True]
    project_unchanged: Literal[True]
    program: EngineeringProgramV1


class OpenLocalWorkspaceResponseV1(ApiModel):
    workspace: LocalWorkspaceV1
    launch: XG5000ProjectLaunchV1 | None = None
    session: EngineeringSessionV1 | None = None


class AttachXG5000RequestV1(ApiModel):
    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    project_path: str | None = Field(default=None, max_length=32_768)
    project_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AttachXG5000ResponseV1(ApiModel):
    status: Literal["attached"] = "attached"
    session: EngineeringSessionV1


class KnowledgeSourceV1(ApiModel):
    source_id: str
    kind: KnowledgeKindV1
    title: str
    scope: str
    vendor: str | None = None
    product_family: str | None = None
    version: str | None = None
    language: str | None = None
    authority: str
    status: KnowledgeStatusV1
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: str | None = None
    revision: str
    supersedes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    confirmed_by: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeSourcePageV1(ApiModel):
    items: list[KnowledgeSourceV1]
    total: int


class KnowledgeCitationV1(ApiModel):
    knowledge_ref: str
    source_id: str
    chunk_id: str
    title: str
    locator: str | None = None
    page: int | None = None
    section: str | None = None


class KnowledgeSearchHitV1(ApiModel):
    content: str
    score: float
    channels: list[str]
    kind: KnowledgeKindV1
    scope: str
    vendor: str | None = None
    product_family: str | None = None
    version: str | None = None
    language: str | None = None
    authority: str
    status: KnowledgeStatusV1
    citation: KnowledgeCitationV1
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchResponseV1(ApiModel):
    query: str
    retrieval_mode: Literal["agent_sidecar"] = "agent_sidecar"
    results: list[KnowledgeSearchHitV1]


class KnowledgeSummaryV1(ApiModel):
    source_count: int
    chunk_count: int
    manual_count: int
    pattern_count: int
    confirmed_memory_count: int
    proposed_memory_count: int
    retrieval_mode: Literal["agent_sidecar"] = "agent_sidecar"


class ImportKnowledgeRequestV1(ApiModel):
    title: str | None = Field(default=None, max_length=512)
    kind: Literal["manual", "standard", "project_document"]
    scope: str = Field(default="global", min_length=1, max_length=256)
    vendor: Literal["ls", "mitsubishi", "rockwell", "generic"]
    product_family: str | None = Field(default=None, max_length=256)
    version: str | None = Field(default=None, max_length=256)
    language: str = Field(default="en", min_length=2, max_length=32)
    # A local file picker proves user selection, not vendor provenance or an
    # organization/project approval. Those authorities require a separate
    # review workflow and cannot be self-asserted by this endpoint.
    authority: Literal["user_imported_reference"] = "user_imported_reference"

    @model_validator(mode="after")
    def require_manual_applicability(self) -> ImportKnowledgeRequestV1:
        if self.kind == "manual" and (not self.product_family or not self.product_family.strip()):
            raise ValueError("productFamily is required for a vendor manual")
        if self.kind == "manual" and (not self.version or not self.version.strip()):
            raise ValueError("version is required for a vendor manual")
        return self


class KnowledgeImportResultV1(ApiModel):
    selected: bool
    source: KnowledgeSourceV1 | None = None
    extraction_status: Literal["ready", "partial"] | None = None
    page_count: int | None = None
    indexed_page_count: int | None = None
    low_text_pages: list[int] = Field(default_factory=list)


class CreateKnowledgeMemoryRequestV1(ApiModel):
    title: str = Field(min_length=1, max_length=512)
    kind: Literal["memory", "preference"]
    scope: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=50_000)
    vendor: Literal["ls", "mitsubishi", "rockwell", "generic"] | None = None
    product_family: str | None = Field(default=None, max_length=256)
    version: str | None = Field(default=None, max_length=256)
    language: str = Field(default="ko", min_length=2, max_length=32)


class ConfirmKnowledgeMemoryRequestV1(ApiModel):
    expected_revision: str = Field(min_length=1, max_length=128)
