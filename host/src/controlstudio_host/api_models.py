from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from controlstudio_contracts.model_input_policy import MAX_CLIENT_INPUT_LIMIT_TOKENS
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

pass


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
    window_handle: int | None = None
    process_create_time: float | None = Field(default=None, gt=0)
    session_id: str | None = None
    project_path: str | None = Field(default=None, max_length=32_768)
    project_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    session_verified: bool = False
    read_ready: bool = False
    native_ld_edit_ready: bool = False
    offline_candidate_eligible: bool = False
    target_workspace_id: str | None = Field(default=None, min_length=1, max_length=256)
    target_revision_id: str | None = Field(default=None, min_length=1, max_length=256)
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
        "not_verified",
    ]
    current_ready: bool
    detail: str
    capability_status: Literal[
        "missing",
        "valid",
        "expired",
        "runtime_mismatch",
        "runtime_checked_at_plan",
    ] | None = None


class HostStatusV1(ApiModel):
    schema_version: Literal["1"] = "1"
    release_channel: Literal["development", "beta", "production"] = "development"
    product_release: str = "source"
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


class HostRestartResponseV1(ApiModel):
    """Non-secret receipt for polling a Host-owned replacement."""

    status: Literal["scheduled", "ready"]
    restart_receipt: str = Field(min_length=32, max_length=512)


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
    rationale: str | None = None
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
    rationale: str | None = None
    verification_method: str | None = None


class ProjectPlanAttachmentV1(ApiModel):
    source_id: str
    title: str
    kind: Literal["manual", "standard", "project_document"]
    scope: str
    vendor: str | None = None
    product_family: str | None = None
    version: str | None = None
    language: str | None = None
    authority: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str


class ProjectPlanRevisionItemV1(ApiModel):
    item_id: str
    kind: Literal["plan_item", "open_decision"]
    section_id: str
    section_title: str
    text: str
    status: Literal[
        "confirmed",
        "proposed",
        "unknown",
        "conflicting",
        "not_applicable",
    ]
    source: Literal["user", "agent"]


class ProjectPlanRevisionDeltaV1(ApiModel):
    schema_version: Literal["1"] = "1"
    previous_message_id: str
    previous_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_fields: list[
        Literal[
            "projectName",
            "status",
            "summary",
            "objective",
            "attachments",
            "technicalConfiguration",
            "design",
            "sections",
            "openDecisions",
            "completeness",
        ]
    ] = Field(default_factory=list, max_length=10)
    changed_section_ids: list[str] = Field(default_factory=list, max_length=101)
    added_items: list[ProjectPlanRevisionItemV1] = Field(default_factory=list, max_length=500)
    removed_items: list[ProjectPlanRevisionItemV1] = Field(default_factory=list, max_length=500)


class ProjectPlanCompletenessV1(ApiModel):
    total_items: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    proposed: int = Field(ge=0)
    unknown: int = Field(ge=0)
    conflicting: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    blocking: int = Field(ge=0)


class ProjectPlanSignalConditionV1(ApiModel):
    signal_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    state: Literal["on", "off"]


def _io_safe_state_schema(schema: dict[str, Any]) -> None:
    field = "safeState" if "safeState" in schema.get("properties", {}) else "safe_state"
    schema["allOf"] = [
        {
            "if": {"properties": {"kind": {"enum": kinds}}, "required": ["kind"]},
            "then": {"properties": {field: {"enum": states}}, "required": [field]},
        }
        for kinds, states in (
            (["digital_input"], ["not_applicable"]),
            (["digital_output", "internal_bit"], ["on", "off"]),
        )
    ]


class ProjectPlanIoPointV1(ApiModel):
    model_config = ConfigDict(json_schema_extra=_io_safe_state_schema)

    signal_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    address: str = Field(pattern=r"^[PM]\d{3,4}[0-9A-F]$")
    # Historical drafts predate explicit native symbol types.  Keep them
    # readable, but require the value when materializing a symbol update.
    native_type: str | None = Field(
        default=None,
        min_length=1,
        alias="nativeType",
        exclude_if=lambda value: value is None,
    )
    kind: Literal["digital_input", "digital_output", "internal_bit"]
    description: str
    normal_state: Literal["on", "off", "unknown"]
    safe_state: Literal["on", "off", "not_applicable"] = Field(
        description="digital_input requires not_applicable; digital_output and "
        "internal_bit require on or off. This is a kind/safe_state relationship, "
        "not a prescribed safety-state choice for writable signals.",
    )

    @field_validator("native_type")
    @classmethod
    def validate_native_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("native_type must be non-blank plain text")
        return value

    @model_validator(mode="after")
    def validate_safe_state(self) -> ProjectPlanIoPointV1:
        if self.kind == "digital_input" and self.safe_state != "not_applicable":
            raise PydanticCustomError(
                "io_safe_state_relation",
                "IO kind and safe state violate the required relationship",
                {
                    "constraintKind": "kind_safe_state_relation",
                    "constraintField": "safe_state",
                    "allowedKinds": ["digital_input"],
                    "allowedStates": ["not_applicable"],
                },
            )
        if self.kind != "digital_input" and self.safe_state == "not_applicable":
            raise PydanticCustomError(
                "io_safe_state_relation",
                "IO kind and safe state violate the required relationship",
                {
                    "constraintKind": "kind_safe_state_relation",
                    "constraintField": "safe_state",
                    "allowedKinds": ["digital_output", "internal_bit"],
                    "allowedStates": ["on", "off"],
                },
            )
        return self


class ProjectPlanOperatingModeV1(ApiModel):
    mode_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: str
    activation: ProjectPlanSignalConditionV1
    allowed_transitions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("allowed_transitions")
    @classmethod
    def validate_unique_transitions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("allowedTransitions must not contain duplicates")
        return value


class ProjectPlanControlNetworkV1(ApiModel):
    network_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    mode_id: str | None = None
    step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    permit_all: list[ProjectPlanSignalConditionV1] = Field(max_length=20)
    output_signal_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")

    @field_validator("permit_all")
    @classmethod
    def validate_unique_permits(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("permitAll must not contain duplicate conditions")
        return value


class ProjectPlanLadderContactConditionV1(ApiModel):
    kind: Literal["contact"]
    source_type: Literal["signal", "timer", "step", "sequence_reset"]
    source_id: str = Field(max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    state: Literal["on", "off"]


class ProjectPlanLadderDataRegisterOperandV1(ApiModel):
    """One directly addressed signed-16 data-register operand."""

    kind: Literal["data_register"]
    address: str = Field(pattern=r"^D\d{5}$")


class ProjectPlanLadderConstantOperandV1(ApiModel):
    """One literal in the native signed-16 comparison domain."""

    kind: Literal["constant"]
    value: StrictInt = Field(ge=-32_768, le=32_767)


ProjectPlanLadderCompareOperandV1 = Annotated[
    ProjectPlanLadderDataRegisterOperandV1 | ProjectPlanLadderConstantOperandV1,
    Field(discriminator="kind"),
]


class ProjectPlanLadderCompareConditionV1(ApiModel):
    """A typed signed-16 LOAD comparison over D registers or constants."""

    kind: Literal["compare"]
    lhs: ProjectPlanLadderCompareOperandV1
    relation: Literal["eq", "ne", "gt", "lt", "ge", "le"]
    rhs: ProjectPlanLadderCompareOperandV1


class ProjectPlanLadderCompositeConditionV1(ApiModel):
    kind: Literal["all_of", "any_of"]
    terms: list["ProjectPlanLadderConditionV1"] = Field(
        min_length=2,
        max_length=20,
    )


ProjectPlanLadderConditionV1 = Annotated[
    ProjectPlanLadderContactConditionV1
    | ProjectPlanLadderCompareConditionV1
    | ProjectPlanLadderCompositeConditionV1,
    Field(discriminator="kind"),
]
ProjectPlanLadderCompositeConditionV1.model_rebuild()


class ProjectPlanLadderActionV1(ApiModel):
    instruction: Literal["OUT", "SET", "RST", "TON"]
    target_type: Literal["signal", "timer", "step", "sequence_reset"]
    target_id: str = Field(max_length=80, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def validate_instruction_target(self) -> ProjectPlanLadderActionV1:
        allowed_targets = {
            "OUT": {"signal"},
            "SET": {"signal", "step", "sequence_reset"},
            "RST": {"signal", "timer"},
            "TON": {"timer"},
        }
        if self.target_type not in allowed_targets[self.instruction]:
            raise ValueError(
                f"{self.instruction} does not support targetType {self.target_type}"
            )
        return self


class ProjectPlanLadderMoveActionV1(ApiModel):
    instruction: Literal["MOV"]
    source_constant: int = Field(ge=-32_768, le=32_767)
    destination_address: str = Field(pattern=r"^D\d{5}$")


class ProjectPlanLadderIncrementActionV1(ApiModel):
    instruction: Literal["INCP"]
    destination_address: str = Field(pattern=r"^D\d{5}$")


ProjectPlanLadderAction = Annotated[
    ProjectPlanLadderActionV1 | ProjectPlanLadderMoveActionV1 | ProjectPlanLadderIncrementActionV1,
    Field(discriminator="instruction"),
]


class ProjectPlanLadderRungV1(ApiModel):
    rung_id: str = Field(max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=500)
    condition: ProjectPlanLadderConditionV1
    action: ProjectPlanLadderAction

    @field_validator("description")
    @classmethod
    def validate_nonblank_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("ladder rung description cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_condition_depth(self) -> ProjectPlanLadderRungV1:
        def depth(condition: ProjectPlanLadderConditionV1) -> int:
            if isinstance(
                condition,
                (ProjectPlanLadderContactConditionV1, ProjectPlanLadderCompareConditionV1),
            ):
                return 1
            return 1 + max(depth(term) for term in condition.terms)

        if depth(self.condition) > 8:
            raise ValueError("ladder condition depth cannot exceed 8")
        return self


class ProjectPlanTimerV1(ApiModel):
    timer_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    address: str = Field(pattern=r"^T\d{3,4}$")
    instruction: Literal["TON"]
    time_base_ms: Literal[1, 10, 100]
    preset_ticks: int = Field(ge=1, le=65_535)
    duration_ms: int = Field(ge=1)
    mode_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    enable_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    description: str

    @field_validator("enable_all")
    @classmethod
    def validate_unique_enable_conditions(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("enableAll must not contain duplicate conditions")
        return value

    @model_validator(mode="after")
    def validate_duration(self) -> ProjectPlanTimerV1:
        if self.duration_ms != self.time_base_ms * self.preset_ticks:
            raise ValueError("durationMs must equal timeBaseMs multiplied by presetTicks")
        return self


class ProjectPlanSequenceStepV1(ApiModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    address: str = Field(pattern=r"^S(?:\d{2}|1[01]\d|12[0-7])\.\d{2}$")
    complete_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    description: str

    @field_validator("complete_all")
    @classmethod
    def validate_unique_completion_conditions(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("completeAll must not contain duplicate conditions")
        return value


class ProjectPlanSequenceV1(ApiModel):
    sequence_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    mode_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    restart_policy: Literal["require_start_release", "continuous"]
    start_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    reset_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    steps: list[ProjectPlanSequenceStepV1] = Field(min_length=1, max_length=99)

    @field_validator("start_all", "reset_all")
    @classmethod
    def validate_unique_sequence_conditions(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("sequence conditions must not contain duplicates")
        return value

    @field_validator("steps")
    @classmethod
    def validate_unique_steps(
        cls, value: list[ProjectPlanSequenceStepV1]
    ) -> list[ProjectPlanSequenceStepV1]:
        step_ids = [item.step_id for item in value]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("sequence steps must not contain duplicate stepIds")
        return value


class ProjectPlanAlarmV1(ApiModel):
    alarm_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    latch_signal_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    mode_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    trigger_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    affected_output_signal_ids: list[str] = Field(min_length=1, max_length=50)
    description: str

    @field_validator("trigger_all")
    @classmethod
    def validate_unique_alarm_conditions(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("triggerAll must not contain duplicate conditions")
        return value

    @field_validator("affected_output_signal_ids")
    @classmethod
    def validate_unique_alarm_outputs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("alarm outputs must not contain duplicates")
        return value


class ProjectPlanRecoveryRuleV1(ApiModel):
    recovery_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    when_all: list[ProjectPlanSignalConditionV1] = Field(min_length=1, max_length=20)
    reset_alarm_ids: list[str] = Field(default_factory=list, max_length=50)
    reset_sequence_ids: list[str] = Field(default_factory=list, max_length=20)
    description: str

    @field_validator("when_all")
    @classmethod
    def validate_unique_recovery_conditions(
        cls, value: list[ProjectPlanSignalConditionV1]
    ) -> list[ProjectPlanSignalConditionV1]:
        keys = [(item.signal_id, item.state) for item in value]
        if len(set(keys)) != len(keys):
            raise ValueError("whenAll must not contain duplicate conditions")
        return value

    @field_validator("reset_alarm_ids", "reset_sequence_ids")
    @classmethod
    def validate_unique_recovery_targets(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("recovery reset targets must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_reset_scope(self) -> ProjectPlanRecoveryRuleV1:
        if not self.reset_alarm_ids and not self.reset_sequence_ids:
            raise ValueError("recovery must reset at least one alarm or sequence")
        return self


class ProjectPlanSafetyRuleV1(ApiModel):
    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    classification: Literal["process_interlock", "external_safety_boundary"]
    permit_when: ProjectPlanSignalConditionV1 | None = None
    affected_output_signal_ids: list[str] = Field(min_length=1, max_length=50)
    action: Literal["deenergize", "external_hardware"]

    @field_validator("affected_output_signal_ids")
    @classmethod
    def validate_unique_safety_outputs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("safety outputs must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_boundary_semantics(self) -> ProjectPlanSafetyRuleV1:
        if self.classification == "process_interlock" and (
            self.action != "deenergize" or self.permit_when is None
        ):
            raise ValueError("process interlock requires a deenergize permit")
        if self.classification == "external_safety_boundary" and (
            self.action != "external_hardware" or self.permit_when is not None
        ):
            raise ValueError("external safety boundary must remain outside PLC logic")
        return self


ProjectPlanCollectionNotApplicableBasisV1 = Literal[
    "single_mode_operation",
    "no_timing_behavior",
    "no_sequence_behavior",
    "no_alarm_behavior",
    "no_recovery_behavior",
    "no_physical_or_hazard_outputs",
]


class ProjectPlanCollectionScopeDecisionV1(ApiModel):
    status: Literal["confirmed", "not_applicable"]
    reason: str = Field(min_length=1, max_length=2_000)
    evidence: list[str] = Field(min_length=1, max_length=20)
    basis: ProjectPlanCollectionNotApplicableBasisV1 | None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("collection scope reason cannot be blank")
        return normalized

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("collection scope evidence cannot contain blank items")
        return normalized

    @model_validator(mode="after")
    def validate_basis(self) -> ProjectPlanCollectionScopeDecisionV1:
        if self.status == "confirmed" and self.basis is not None:
            raise ValueError("confirmed collection scope cannot have an N/A basis")
        if self.status == "not_applicable" and self.basis is None:
            raise ValueError("not_applicable collection scope requires an explicit basis")
        return self


class ProjectPlanNativeCollectionScopeV1(ApiModel):
    io_points: ProjectPlanCollectionScopeDecisionV1
    operating_modes: ProjectPlanCollectionScopeDecisionV1
    control_networks: ProjectPlanCollectionScopeDecisionV1
    timers: ProjectPlanCollectionScopeDecisionV1
    sequences: ProjectPlanCollectionScopeDecisionV1
    alarms: ProjectPlanCollectionScopeDecisionV1
    recovery_rules: ProjectPlanCollectionScopeDecisionV1
    safety_rules: ProjectPlanCollectionScopeDecisionV1


class ProjectPlanIndexAcceptanceBindingsV1(ApiModel):
    schema_version: Literal["controlstudio-index-acceptance-bindings/v1"] = "controlstudio-index-acceptance-bindings/v1"
    signals: dict[str, str]
    modes: dict[str, str]
    alarms: dict[str, str]
    timer_id: str = Field(min_length=1)


class ProjectPlanNativeDesignV1(ApiModel):
    schema_version: Literal["2"] = "2"
    collection_scope: ProjectPlanNativeCollectionScopeV1 | None = None
    index_acceptance_bindings: ProjectPlanIndexAcceptanceBindingsV1 | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    # A scenario is an optional, model-authored singleton.  Keep it absent
    # from legacy serialized designs when it was never supplied; the
    # planning draft transport uses the same omission-preserving contract.
    simulation_scenario: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    native_symbols: list[dict[str, Any]] | None = Field(
        default=None, max_length=4096, exclude_if=lambda value: value is None,
        description=(
            "Optional ordered, model-authored native CSV symbol metadata. Supply "
            "explicit symbol, address, data_type and comment; this does not declare "
            "I/O, generate logic or infer timer/counter labels or native types."
        ),
    )
    io_points: list[ProjectPlanIoPointV1] = Field(max_length=500)
    operating_modes: list[ProjectPlanOperatingModeV1] = Field(max_length=50)
    control_networks: list[ProjectPlanControlNetworkV1] = Field(max_length=200)
    ladder_rungs: list[ProjectPlanLadderRungV1] = Field(
        default_factory=list,
    )
    timers: list[ProjectPlanTimerV1] = Field(default_factory=list, max_length=100)
    sequences: list[ProjectPlanSequenceV1] = Field(default_factory=list, max_length=20)
    alarms: list[ProjectPlanAlarmV1] = Field(default_factory=list, max_length=100)
    recovery_rules: list[ProjectPlanRecoveryRuleV1] = Field(
        default_factory=list,
        max_length=100,
    )
    safety_rules: list[ProjectPlanSafetyRuleV1] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_unique_entity_identities(self) -> ProjectPlanNativeDesignV1:
        io_by_id = {point.signal_id: point for point in self.io_points}
        for network in self.control_networks:
            output = io_by_id.get(network.output_signal_id)
            if output is not None and output.kind == "digital_output" and not network.permit_all:
                raise ValueError("digital_output control networks require explicit permit_all")
        collections = (
            (self.io_points, "signal_id", "ioPoints"),
            (self.operating_modes, "mode_id", "operatingModes"),
            (self.control_networks, "network_id", "controlNetworks"),
            (self.ladder_rungs, "rung_id", "ladderRungs"),
            (self.timers, "timer_id", "timers"),
            (self.sequences, "sequence_id", "sequences"),
            (self.alarms, "alarm_id", "alarms"),
            (self.recovery_rules, "recovery_id", "recoveryRules"),
            (self.safety_rules, "rule_id", "safetyRules"),
        )
        for values, field, label in collections:
            identities = [getattr(item, field) for item in values]
            if len(set(identities)) != len(identities):
                raise ValueError(f"{label} must not contain duplicate identities")
        rung_action_identities = [
            json.dumps(
                {
                    "condition": rung.condition.model_dump(mode="json", by_alias=True),
                    "action": rung.action.model_dump(mode="json", by_alias=True),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for rung in self.ladder_rungs
        ]
        if len(set(rung_action_identities)) != len(rung_action_identities):
            raise ValueError(
                "ladderRungs must not contain duplicate condition/action pairs"
            )
        return self

    @model_validator(mode="after")
    def validate_collection_scope(self) -> ProjectPlanNativeDesignV1:
        if self.collection_scope is None:
            return self
        collections = {
            "io_points": self.io_points,
            "operating_modes": self.operating_modes,
            "control_networks": self.control_networks,
            "timers": self.timers,
            "sequences": self.sequences,
            "alarms": self.alarms,
            "recovery_rules": self.recovery_rules,
            "safety_rules": self.safety_rules,
        }
        allowed_bases = {
            "operating_modes": {"single_mode_operation"},
            "timers": {"no_timing_behavior"},
            "sequences": {"no_sequence_behavior"},
            "alarms": {"no_alarm_behavior"},
            "recovery_rules": {"no_recovery_behavior"},
            "safety_rules": {"no_physical_or_hazard_outputs"},
        }
        for name, values in collections.items():
            decision = getattr(self.collection_scope, name)
            if decision.status == "confirmed" and not values:
                raise ValueError(f"confirmed {name} collection cannot be empty")
            if decision.status == "not_applicable" and values:
                raise ValueError(f"not_applicable {name} collection must be empty")
            if name in {"io_points", "control_networks"}:
                if decision.status != "confirmed":
                    raise ValueError(f"executable build requires confirmed {name}")
            elif (
                decision.status == "not_applicable"
                and decision.basis not in allowed_bases[name]
            ):
                raise ValueError(f"{name} has an unsupported N/A basis")
        if (
            self.collection_scope.safety_rules.status == "not_applicable"
            and self.collection_scope.safety_rules.basis
            == "no_physical_or_hazard_outputs"
            and any(
                item.kind == "digital_output"
                or (item.kind != "digital_input" and item.address.startswith("P"))
                for item in self.io_points
            )
        ):
            raise ValueError(
                "physical outputs require a confirmed process or external safety boundary"
            )
        return self


class ProjectPlanTechnicalConfigurationV1(ApiModel):
    plc_series: Literal["XGK", "XGB"]
    cpu_model: str = Field(min_length=1, max_length=64)
    program_name: str = Field(min_length=1, max_length=80)
    language: Literal["LD"]
    description: str = Field(default="", max_length=1_024)

    @field_validator("cpu_model", "program_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("technical configuration values cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class ProjectRequirementSourceV1(ApiModel):
    field: Literal[
        "sequence", "hardware_and_io", "safety_and_interlocks",
        "operating_modes", "performance_and_acceptance",
    ]
    quote: str = Field(min_length=1, max_length=20_000)
    message_id: str | None = Field(
        default=None, min_length=1, max_length=200,
        exclude_if=lambda value: value is None,
    )


class ProjectPlanArtifactV1(ApiModel):
    requirement_sources: list[ProjectRequirementSourceV1] = Field(
        default_factory=list, max_length=5, exclude_if=lambda value: not value,
    )
    schema_version: Literal["2"] = "2"
    project_name: str
    status: Literal["needs_clarification", "ready_for_review"]
    summary: str
    objective: str
    attachments: list[ProjectPlanAttachmentV1] = Field(default_factory=list, max_length=20)
    technical_configuration: ProjectPlanTechnicalConfigurationV1 | None = None
    design: ProjectPlanNativeDesignV1
    sections: list[ProjectPlanSectionV1]
    open_decisions: list[ProjectPlanOpenDecisionV1]
    completeness: ProjectPlanCompletenessV1


class ProjectPlanReviewV1(ApiModel):
    schema_version: Literal["1", "2"] = "1"
    authority_current: bool = False
    message_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # A pre-v12 review is returned with an empty scope so clients can display
    # the conversation while treating that legacy acknowledgement as invalid.
    reviewed_section_ids: list[str] = Field(default_factory=list, max_length=100)
    reviewed_item_ids: list[str] = Field(default_factory=list, max_length=100_000)
    project_brief_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    attachment_revision_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    actor_id: str
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_execution_authority(self) -> ProjectPlanReviewV1:
        if self.schema_version == "2" and (
            not self.reviewed_section_ids
            or not self.reviewed_item_ids
            or self.project_brief_digest is None
            or self.attachment_revision_digest is None
        ):
            raise ValueError("v2 project plan review authority is incomplete")
        return self


EngineeringEvidenceIdV1 = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]


class EngineeringObservationV1(ApiModel):
    statement: str = Field(min_length=1, max_length=4_000)
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(min_length=1, max_length=50)
    fact_kind: Literal["location", "relation", "project_structure"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    fact_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        exclude_if=lambda value: value is None,
    )


class EngineeringInferenceV1(ApiModel):
    statement: str = Field(min_length=1, max_length=4_000)
    confidence: Literal["low", "medium", "high"]
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(default_factory=list, max_length=50)


class EngineeringHypothesisV1(ApiModel):
    hypothesis: str = Field(min_length=1, max_length=4_000)
    confidence: Literal["low", "medium", "high"]
    supporting_evidence_refs: list[EngineeringEvidenceIdV1] = Field(
        default_factory=list,
        max_length=50,
    )
    counterevidence: list[str] = Field(default_factory=list, max_length=50)
    verification_steps: list[str] = Field(default_factory=list, max_length=50)
    verification: Literal["open", "verified", "rejected"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    outcome: str | None = Field(
        default=None,
        min_length=1,
        max_length=4_000,
        exclude_if=lambda value: value is None,
    )
    verification_evidence_refs: list[EngineeringEvidenceIdV1] | None = Field(
        default=None,
        max_length=50,
        exclude_if=lambda value: value is None,
    )


class EngineeringAlternativeV1(ApiModel):
    option: str = Field(min_length=1, max_length=4_000)
    expected_impact: list[str] = Field(default_factory=list, max_length=50)
    risks: list[str] = Field(default_factory=list, max_length=50)
    required_changes: list[str] = Field(
        default_factory=list,
        max_length=50,
        exclude_if=lambda value: not value,
    )
    verification_steps: list[str] = Field(default_factory=list, max_length=50)
    rollback: list[str] = Field(default_factory=list, max_length=50)
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(
        default_factory=list,
        max_length=50,
        exclude_if=lambda value: not value,
    )


class EngineeringUnknownV1(ApiModel):
    statement: str = Field(min_length=1, max_length=4_000)
    blocking: bool
    verification_step: str = Field(min_length=1, max_length=4_000)


class EngineeringLocationV2(ApiModel):
    evidence_ref: EngineeringEvidenceIdV1
    program: str = Field(min_length=1, max_length=500)
    network_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        exclude_if=lambda value: value is None,
    )
    network_index: int | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        exclude_if=lambda value: value is None,
    )
    rung_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        exclude_if=lambda value: value is None,
    )
    visual_row: int | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        exclude_if=lambda value: value is None,
    )
    token_offset: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000,
        exclude_if=lambda value: value is None,
    )
    device: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        exclude_if=lambda value: value is None,
    )
    access: Literal[
        "read",
        "write",
        "state_write",
        "condition",
        "action",
        "argument",
        "unknown",
    ]
    semantic_status: Literal["observed", "structured", "partial"]

class EngineeringRelationV2(ApiModel):
    evidence_ref: EngineeringEvidenceIdV1
    relation_kind: Literal[
        "read",
        "write",
        "state_write",
        "condition",
        "causal_path",
        "state_transition",
    ]
    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=500)
    device_chain: list[str] = Field(default_factory=list, max_length=100)
    direct: bool
    semantic_status: Literal["structured", "partial"]
    locations: list[EngineeringLocationV2] = Field(default_factory=list, max_length=100)

    @field_validator("device_chain")
    @classmethod
    def validate_device_chain(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("engineering relation deviceChain cannot contain blanks")
        return value

    @model_validator(mode="after")
    def validate_causal_path(self) -> EngineeringRelationV2:
        if self.relation_kind != "causal_path":
            return self
        if len(self.device_chain) < 2:
            raise ValueError("causal_path requires a deviceChain with at least two devices")
        if self.device_chain[0] != self.source or self.device_chain[-1] != self.target:
            raise ValueError("causal_path deviceChain must start at source and end at target")
        if self.direct != (len(self.device_chain) == 2):
            raise ValueError("causal_path direct must exactly reflect deviceChain length")
        return self


class EngineeringCoverageV2(ApiModel):
    area: Literal[
        "programs",
        "logic_graph",
        "cross_references",
        "state_model",
        "causal_paths",
        "hardware",
        "runtime",
        "external_integrations",
        "project_structure",
    ]
    status: Literal["complete", "partial", "not_observed", "not_applicable"]
    observed_count: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000,
        exclude_if=lambda value: value is None,
    )
    total_count: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000,
        exclude_if=lambda value: value is None,
    )
    truncated: bool
    limitations: list[str] = Field(default_factory=list, max_length=100)
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(default_factory=list, max_length=100)

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("engineering coverage limitations cannot contain blanks")
        return value

    @model_validator(mode="after")
    def validate_coverage_state(self) -> EngineeringCoverageV2:
        if self.status == "complete" and self.truncated:
            raise ValueError("complete engineering coverage cannot be truncated")
        if self.status in {"partial", "not_observed"} and not self.limitations:
            raise ValueError(
                "partial or not_observed engineering coverage requires limitations"
            )
        if (
            self.observed_count is not None
            and self.total_count is not None
            and self.observed_count > self.total_count
        ):
            raise ValueError("engineering coverage observedCount cannot exceed totalCount")
        return self


class EngineeringProjectStructureAttributeV2(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    value: str | int | bool

    @model_validator(mode="after")
    def validate_attribute(self) -> EngineeringProjectStructureAttributeV2:
        if isinstance(self.value, str) and (
            not self.value or len(self.value) > 4_096
        ):
            raise ValueError("project structure string attribute is invalid")
        if isinstance(self.value, int) and not isinstance(self.value, bool) and not (
            -(2**31) <= self.value <= 100_000_000
        ):
            raise ValueError("project structure integer attribute is out of range")
        return self


class EngineeringProjectStructureFactV2(ApiModel):
    evidence_ref: EngineeringEvidenceIdV1
    kind: Literal[
        "configuration",
        "task",
        "code_unit",
        "symbol",
        "device",
        "call_edge",
        "parameter",
        "module",
        "communication",
    ]
    entity_id: str = Field(min_length=1, max_length=500)
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=4_096,
        exclude_if=lambda value: value is None,
    )
    parent_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        exclude_if=lambda value: value is None,
    )
    order: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000,
        exclude_if=lambda value: value is None,
    )
    status: Literal["observed", "partial"]
    attributes: list[EngineeringProjectStructureAttributeV2] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_attributes(self) -> EngineeringProjectStructureFactV2:
        names = [item.name for item in self.attributes]
        if names != sorted(set(names)):
            raise ValueError("project structure fact attributes must be sorted and unique")
        return self

class EngineeringAnalysisArtifactV1(ApiModel):
    schema_version: Literal["1", "2", "3", "4"] = "1"
    observations: list[EngineeringObservationV1] = Field(default_factory=list, max_length=100)
    inferences: list[EngineeringInferenceV1] = Field(default_factory=list, max_length=100)
    hypotheses: list[EngineeringHypothesisV1] = Field(default_factory=list, max_length=50)
    alternatives: list[EngineeringAlternativeV1] = Field(default_factory=list, max_length=20)
    locations: list[EngineeringLocationV2] | None = Field(
        default=None,
        max_length=500,
        exclude_if=lambda value: value is None,
    )
    relations: list[EngineeringRelationV2] | None = Field(
        default=None,
        max_length=500,
        exclude_if=lambda value: value is None,
    )
    coverage: list[EngineeringCoverageV2] | None = Field(
        default=None,
        max_length=9,
        exclude_if=lambda value: value is None,
    )
    project_structure: list[EngineeringProjectStructureFactV2] | None = Field(
        default=None,
        max_length=200,
        exclude_if=lambda value: value is None,
    )
    unknowns: list[EngineeringUnknownV1] = Field(default_factory=list, max_length=100)
    next_actions: list[str] = Field(default_factory=list, max_length=50)
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_versioned_structure(self) -> EngineeringAnalysisArtifactV1:
        structures = (self.locations, self.relations, self.coverage)
        if self.schema_version == "1" and any(item is not None for item in structures):
            raise ValueError("v1 engineering analysis cannot contain v2 structure")
        if self.schema_version == "1" and self.project_structure is not None:
            raise ValueError("v1 engineering analysis cannot contain project structure")
        if self.schema_version in {"2", "3", "4"} and any(
            item is None for item in structures
        ):
            raise ValueError(
                "structured engineering analysis requires locations, relations, coverage"
            )
        if self.schema_version in {"3", "4"}:
            if any(
                len(item.evidence_refs) != 1
                or item.fact_kind is None
                or item.fact_fingerprint is None
                for item in self.observations
            ):
                raise ValueError(
                    "v3 observations require one exact structural fact reference"
                )
            for alternative in self.alternatives:
                details = (
                    alternative.expected_impact,
                    alternative.risks,
                    alternative.required_changes,
                    alternative.verification_steps,
                    alternative.rollback,
                    alternative.evidence_refs,
                )
                if any(not values for values in details) or any(
                    not value.strip() or value != value.strip()
                    for values in details[:-1]
                    for value in values
                ):
                    raise ValueError(
                        "v3 alternatives require evidence and complete decision details"
                    )
            if self.schema_version == "4":
                for hypothesis in self.hypotheses:
                    if (
                        hypothesis.verification is None
                        or hypothesis.verification_evidence_refs is None
                        or not hypothesis.verification_steps
                        or any(
                            not step.strip()
                            for step in hypothesis.verification_steps
                        )
                    ):
                        raise ValueError(
                            "v4 hypotheses require a verification state and plan"
                        )
                    if hypothesis.verification == "open":
                        if (
                            hypothesis.outcome is not None
                            or hypothesis.verification_evidence_refs
                        ):
                            raise ValueError(
                                "an open v4 hypothesis cannot contain a result"
                            )
                    elif (
                        hypothesis.outcome is None
                        or not hypothesis.outcome.strip()
                        or not hypothesis.verification_evidence_refs
                    ):
                        raise ValueError(
                            "a terminal v4 hypothesis requires outcome evidence"
                        )
        elif any(
            item.fact_kind is not None or item.fact_fingerprint is not None
            for item in self.observations
        ) or any(
            item.required_changes or item.evidence_refs for item in self.alternatives
        ) or any(
            item.verification is not None
            or item.outcome is not None
            or item.verification_evidence_refs is not None
            for item in self.hypotheses
        ):
            raise ValueError("legacy engineering analysis contains newer fields")
        coverage_areas = [item.area for item in self.coverage or []]
        if len(coverage_areas) != len(set(coverage_areas)):
            raise ValueError("engineering analysis coverage areas must be unique")
        return self


EngineeringMissionStopReasonV1 = Literal[
    "continuing",
    "cycle_budget_exhausted",
    "needs_user_input",
    "needs_human_approval",
    "objective_achieved",
    "blocked_by_missing_capability",
]


class EngineeringMissionStepV1(ApiModel):
    step_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=4_000)
    status: Literal["pending", "active", "completed", "blocked"]
    outcome: str | None = Field(default=None, min_length=1, max_length=4_000)
    evidence_refs: list[EngineeringEvidenceIdV1] = Field(default_factory=list, max_length=50)

    @field_validator("step_id", "title", "outcome")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("engineering mission step text cannot be blank")
        return normalized

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("engineering mission step evidenceRefs must be unique")
        return value

    @model_validator(mode="after")
    def validate_completed_outcome(self) -> EngineeringMissionStepV1:
        if self.status == "completed" and self.outcome is None:
            raise ValueError("completed engineering mission step requires outcome")
        return self


class EngineeringMissionSnapshotV1(ApiModel):
    schema_version: Literal["1"] = "1"
    objective: str = Field(min_length=1, max_length=4_000)
    completion_kind: Literal["analysis", "native_change"] | None = None
    status: Literal[
        "active",
        "waiting_for_user",
        "waiting_for_approval",
        "completed",
        "blocked",
    ]
    completion_criteria: list[str] = Field(min_length=1, max_length=50)
    steps: list[EngineeringMissionStepV1] = Field(min_length=1, max_length=50)
    active_step_id: str | None = Field(default=None, min_length=1, max_length=100)
    blocking_questions: list[str] = Field(default_factory=list, max_length=50)
    next_action: str | None = Field(default=None, min_length=1, max_length=4_000)
    stop_reason: EngineeringMissionStopReasonV1

    @field_validator("objective", "active_step_id", "next_action")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("engineering mission text cannot be blank")
        return normalized

    @field_validator("completion_criteria", "blocking_questions")
    @classmethod
    def normalize_text_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 4_000 for item in normalized):
            raise ValueError("engineering mission list items must be non-blank text")
        return normalized

    @model_validator(mode="after")
    def validate_state(self) -> EngineeringMissionSnapshotV1:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("engineering mission stepId values must be unique")
        active_steps = [step.step_id for step in self.steps if step.status == "active"]
        if self.status == "active":
            if len(active_steps) > 1 or self.active_step_id != (
                active_steps[0] if active_steps else None
            ):
                raise ValueError(
                    "active engineering mission step must match activeStepId when present"
                )
            if self.blocking_questions or self.next_action is None:
                raise ValueError(
                    "active engineering mission requires nextAction and no blockingQuestions"
                )
            if self.stop_reason not in {"continuing", "cycle_budget_exhausted"}:
                raise ValueError("active engineering mission stopReason is invalid")
        elif active_steps or self.active_step_id is not None:
            raise ValueError("non-active engineering mission cannot contain an active step")

        if self.status == "completed":
            if any(step.status != "completed" for step in self.steps):
                raise ValueError("completed engineering mission requires completed steps")
            if self.blocking_questions or self.next_action is not None:
                raise ValueError(
                    "completed engineering mission cannot retain questions or nextAction"
                )
            if self.stop_reason != "objective_achieved":
                raise ValueError("completed engineering mission stopReason is invalid")
        elif self.status == "waiting_for_user":
            if not self.blocking_questions:
                raise ValueError("waiting engineering mission requires blockingQuestions")
            if self.stop_reason != "needs_user_input":
                raise ValueError("waiting engineering mission stopReason is invalid")
        elif self.status == "waiting_for_approval":
            if self.stop_reason != "needs_human_approval":
                raise ValueError("approval-waiting engineering mission stopReason is invalid")
        elif self.status == "blocked":
            if not any(step.status == "blocked" for step in self.steps):
                raise ValueError("blocked engineering mission requires a blocked step")
            if self.stop_reason != "blocked_by_missing_capability":
                raise ValueError("blocked engineering mission stopReason is invalid")
        return self


def canonical_engineering_mission_snapshot(value: Any) -> tuple[dict[str, Any], str]:
    """Validate and serialize the one Host-canonical engineering mission form."""

    snapshot = EngineeringMissionSnapshotV1.model_validate(value).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    digest = hashlib.sha256(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return snapshot, digest


class EngineeringMissionV1(ApiModel):
    schema_version: Literal["1"] = "1"
    conversation_id: str
    workspace_id: str
    revision_id: str
    revision: int = Field(ge=1)
    snapshot: EngineeringMissionSnapshotV1
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_by_run_id: str
    change_transaction_id: EngineeringEvidenceIdV1 | None = None
    created_at: datetime
    updated_at: datetime


class EngineeringMissionSummaryV1(ApiModel):
    schema_version: Literal["1"] = "1"
    conversation_id: str
    workspace_id: str
    revision_id: str
    revision: int = Field(ge=1)
    status: Literal[
        "active",
        "waiting_for_user",
        "waiting_for_approval",
        "completed",
        "blocked",
    ]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_by_run_id: str
    change_transaction_id: EngineeringEvidenceIdV1 | None = None
    updated_at: datetime


class ConversationCompletionV1(ApiModel):
    """External completion state for a conversation's user-visible goal.

    A completed Agent turn (and a published planning revision) is not the same
    thing as completing the user's end-to-end goal.  The Host exposes this
    distinction explicitly so clients cannot infer goal completion from a run
    state or a planning publication alone.
    """

    schema_version: Literal["1"] = "1"
    status: Literal["not_claimed", "phase_complete", "goal_complete", "request_complete", "blocked"]
    phase: Literal["planning", "engineering"]
    basis: Literal[
        "no_completion_evidence",
        "planning_needs_clarification",
        "planning_ready_for_review",
        "planning_reviewed",
        "engineering_in_progress",
        "engineering_waiting_for_user",
        "engineering_waiting_for_approval",
        "engineering_cycle_budget_exhausted",
        "engineering_objective_achieved",
        "engineering_blocked",
        "invalid_engineering_mission",
        "native_request_blocked",
        "native_request_needs_user",
        "native_completion_unverified",
        "native_request_assessed",
        "native_project_goal_assessed",
    ]
    source_message_id: str | None = None
    source_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ConversationMessageV1(ApiModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    run_id: str | None = None
    run_state: Literal["idle", "analyzing", "waiting_for_user", "waiting_for_approval", "approved_pending_execution",
                       "executing", "verifying", "completed", "cancelled", "failed", "quarantined"] | None = None
    operation_id: str | None = None
    status: Literal["streaming", "complete", "failed"] = "complete"
    workflow: Literal["engineering", "new_project_planning"] | None = None
    plan_status: Literal["needs_clarification", "ready_for_review"] | None = None
    project_name: str | None = None
    plan_artifact: ProjectPlanArtifactV1 | None = None
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_review: ProjectPlanReviewV1 | None = None
    plan_revision: ProjectPlanRevisionDeltaV1 | None = None
    engineering_analysis: EngineeringAnalysisArtifactV1 | None = None
    engineering_analysis_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class ProjectBriefV1(ApiModel):
    supplemental_requirements: str = Field(default="", max_length=20_000)
    project_name: str = Field(min_length=1, max_length=200)
    machine_purpose: str = Field(min_length=1, max_length=10_000)
    sequence: str = Field(default="", max_length=20_000)
    hardware_and_io: str = Field(default="", max_length=20_000)
    safety_and_interlocks: str = Field(default="", max_length=20_000)
    operating_modes: str = Field(default="", max_length=20_000)
    performance_and_acceptance: str = Field(default="", max_length=20_000)
    attachment_source_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("attachment_source_ids")
    @classmethod
    def validate_attachment_source_ids(cls, value: list[str]) -> list[str]:
        if any(
            not item
            or len(item) > 256
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", item) is None
            for item in value
        ):
            raise ValueError("attachmentSourceIds contains an invalid source id")
        if len(set(value)) != len(value):
            raise ValueError("attachmentSourceIds must not contain duplicates")
        return value


class EngineeringTargetBindingV1(ApiModel):
    workspace_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    engineering_session_id: str = Field(min_length=1, max_length=256)


class EngineeringConversationHandoffV1(ApiModel):
    """Immutable planning-to-engineering provenance exposed to HTTP clients.

    Reopen evidence was added after the first handoff records were persisted.
    The fields are therefore nullable at this DTO boundary so a legacy record
    can still be read.  New handoffs are constructed only from the Host's
    accepted-native-project path, which requires and fills both values before
    persistence; a partially populated pair is never accepted here.
    """

    schema_version: Literal["1"] = "1"
    pass
    planning_conversation_id: str = Field(min_length=1, max_length=256)
    planning_message_id: str = Field(min_length=1, max_length=256)
    reviewed_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_brief_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attachment_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_bundle_id: str = Field(min_length=1, max_length=256)
    build_receipt_schema_version: Literal[
        "controlstudio-native-build-receipt/v3"
    ]
    build_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reopen_receipt_schema_version: Literal[
        "controlstudio-native-reopen-receipt/v2"
    ] | None = None
    reopen_receipt_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_reopen_receipt_pair(self) -> EngineeringConversationHandoffV1:
        if (self.reopen_receipt_schema_version is None) != (
            self.reopen_receipt_digest is None
        ):
            raise ValueError(
                "reopen receipt schema version and digest must be supplied together"
            )
        return self


class ConversationNativeProjectV1(ApiModel):
    pass
    state: Literal["allocated", "executing", "completed", "failed", "quarantined"]
    updated_at: datetime


class ConversationNativeExecutionV1(ApiModel):
    source_run_id: str
    request_message_id: str | None = None
    state: str
    resumable: bool
    completed_tool_calls: int = Field(ge=0)


class ConversationV1(ApiModel):
    conversation_id: str
    title: str
    workflow: Literal["engineering", "new_project_planning"]
    project_brief: ProjectBriefV1 | None = None
    engineering_target: EngineeringTargetBindingV1 | None = None
    engineering_handoff: EngineeringConversationHandoffV1 | None = None
    engineering_mission: EngineeringMissionV1 | None = None
    latest_native_project: ConversationNativeProjectV1 | None = None
    native_execution: ConversationNativeExecutionV1 | None = None
    request_scope: Literal["analysis_only", "offline_candidate_revision", "permanent_offline_revision"] | None = None
    allow_simulator: bool = False
    # The Host always supplies this projection in current responses.  Keeping
    # a deterministic default makes the additive field backward-compatible
    # for legacy model constructors and persisted response fixtures while
    # retaining a non-null schema for new clients.
    goal_completion: ConversationCompletionV1 = Field(
        default_factory=lambda: ConversationCompletionV1(
            status="not_claimed",
            phase="planning",
            basis="no_completion_evidence",
        )
    )
    messages: list[ConversationMessageV1]
    updated_at: datetime


class ConversationSummaryV1(ApiModel):
    conversation_id: str
    title: str
    workflow: Literal["engineering", "new_project_planning"]
    engineering_target: EngineeringTargetBindingV1 | None = None
    engineering_handoff: EngineeringConversationHandoffV1 | None = None
    engineering_mission: EngineeringMissionSummaryV1 | None = None
    updated_at: datetime


class ConversationPageV1(ApiModel):
    items: list[ConversationSummaryV1]
    total: int
    limit: int
    offset: int


class EmptyNativeProgramSourceV1(ApiModel):
    """Identity of an empty program in the bound saved native project."""

    kind: Literal["saved-native"]
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    program_id: str = Field(min_length=1, max_length=512)
    program_name: str = Field(min_length=1, max_length=80)
    source_program_data_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConversationCreateV1(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: ProjectBriefV1 | None = None
    pass
    target_scope: Literal["current", "none"] = "current"

    @model_validator(mode="after")
    def validate_workflow_contract(self) -> ConversationCreateV1:
        if self.workflow == "new_project_planning" and self.project_brief is None:
            raise ValueError("projectBrief is required for new_project_planning")
        if self.workflow == "engineering" and self.project_brief is not None:
            raise ValueError("projectBrief is only valid for new_project_planning")
        pass
        if self.target_scope == "none" and (
            self.workflow != "engineering" or None is not None
        ):
            raise ValueError("targetScope none is only valid for general engineering analysis")
        return self


class OfflineOperandMissionV1(ApiModel):
    """Host-owned typed intent for one evidence-derived offline LD operand edit."""

    schema_version: Literal["1"] = "1"
    program_id: str = Field(min_length=1, max_length=256)
    program_name: str = Field(min_length=1, max_length=512)
    expected_operand: str = Field(min_length=1, max_length=256)
    replacement_operand: str = Field(min_length=1, max_length=256)
    token_offset_source: Literal["fresh_ld_evidence"]

    @model_validator(mode="after")
    def validate_distinct_operands(self) -> OfflineOperandMissionV1:
        if self.expected_operand == self.replacement_operand:
            raise ValueError("offline operand mission requires a changed operand")
        return self


class SendMessageRequestV1(ApiModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    operation_id: str = Field(min_length=8, max_length=128)
    workflow: Literal["engineering", "new_project_planning"] = "engineering"
    project_brief: ProjectBriefV1 | None = None
    # This is a typed, already reviewed native-create request supplied by the
    # user-facing planning flow.  It is a forward reference because the native
    # request model is declared later in this module; the model is rebuilt
    # after that declaration below.  Host execution still revalidates review,
    # actor, run and target authority separately.
    pass
    # A reviewed test request is a separate authority.  Creating a native
    # project never grants the model permission to start XG-SIM.
    pass
    change_request_scope: (
        Literal[
            "analysis_only",
            "offline_candidate_revision",
            "permanent_offline_revision",
        ]
        | None
    ) = None
    verification_scope: Literal[
        "offline_native_check_only",
        "offline_native_reopen",
        
    ] = "offline_native_reopen"
    offline_operand_mission: OfflineOperandMissionV1 | None = None
    resume_native_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    allow_simulator: bool = False

    @model_validator(mode="after")
    def validate_project_brief_workflow(self) -> SendMessageRequestV1:
        if self.allow_simulator and self.change_request_scope != "permanent_offline_revision":
            raise ValueError("Local simulator verification requires the native working-copy scope")
        if (
            self.change_request_scope == "permanent_offline_revision"
            and "verification_scope" not in self.model_fields_set
        ):
            self.verification_scope = "offline_native_check_only"
        if self.workflow == "engineering" and self.project_brief is not None:
            raise ValueError("projectBrief is only valid for new_project_planning")
        if self.resume_native_run_id is not None and (
            self.change_request_scope != "permanent_offline_revision"
        ):
            raise ValueError("native resume requires the bound permanent offline request")
        pass
        pass
        pass
        pass
        pass
        pass
        pass
        if self.workflow == "new_project_planning" and self.change_request_scope not in {None, "permanent_offline_revision"}:
            raise ValueError("planning accepts consultation or an explicit native creation request")
        if (
            self.workflow != "engineering"
            and self.verification_scope != "offline_native_reopen"
            and self.change_request_scope != "permanent_offline_revision"
        ):
            raise ValueError("verificationScope is only valid for engineering messages")
        pass
        if (
            self.verification_scope == "offline_native_check_only"
            and self.change_request_scope != "permanent_offline_revision"
        ):
            raise ValueError(
                "offline_native_check_only requires permanent_offline_revision"
            )
        if (
            self.change_request_scope == "permanent_offline_revision"
            and self.verification_scope != "offline_native_check_only"
        ):
            raise ValueError(
                "permanent_offline_revision requires offline_native_check_only"
            )
        if self.offline_operand_mission is not None and (
            self.workflow != "engineering"
            or self.change_request_scope != "offline_candidate_revision"
            or self.verification_scope != "offline_native_reopen"
        ):
            raise ValueError(
                "offlineOperandMission requires engineering offline_candidate_revision "
                "with offline_native_reopen"
            )
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


class EngineeringMissionResumeRequestV1(ApiModel):
    operation_id: str = Field(min_length=8, max_length=128)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value):
            raise ValueError("operation_id contains invalid characters")
        return value


class NativeExecutionResumeRequestV1(EngineeringMissionResumeRequestV1):
    source_run_id: str = Field(min_length=1, max_length=128)
    allow_simulator: bool = False


class EngineeringMissionBlockedStepReverifyRequestV1(ApiModel):
    """Explicit user authority to reopen one durably blocked read step."""

    operation_id: str = Field(min_length=8, max_length=128)
    completed_report: bool = False

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value):
            raise ValueError("operation_id contains invalid characters")
        return value


class ProjectPlanReviewRequestV1(ApiModel):
    schema_version: Literal["2"] = "2"
    message_id: str = Field(min_length=1, max_length=256)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_section_ids: list[str] = Field(min_length=1, max_length=100)
    reviewed_item_ids: list[str] = Field(min_length=1, max_length=100_000)

    @field_validator("reviewed_section_ids")
    @classmethod
    def validate_reviewed_section_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("reviewedSectionIds contains an invalid section id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedSectionIds must not contain duplicates")
        return value

    @field_validator("reviewed_item_ids")
    @classmethod
    def validate_reviewed_item_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 2_000 for item in value):
            raise ValueError("reviewedItemIds contains an invalid item id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedItemIds must not contain duplicates")
        return value


class ProjectPlanReviewResponseV1(ApiModel):
    review: ProjectPlanReviewV1
    review_recorded: bool


class BrowserSessionV1(ApiModel):
    session_token: str
    expires_at: datetime


class LauncherHandoffV1(ApiModel):
    handoff_ticket: str
    readiness_receipt: str
    expires_at: datetime


class LauncherReadinessV1(ApiModel):
    """Launcher-only observation of a single browser bootstrap handoff."""

    status: Literal["pending", "ready"]
    expires_at: datetime


class RunCancellationV1(ApiModel):
    run_id: str
    state: str
    terminal: bool
    idempotent: bool
    detail: str | None = None


VerificationScope = Literal["offline_native_check_only", "offline_native_reopen"]
pass
ChangeMode = Literal["offline_working_copy_revision"]
ChangeRequestScope = Literal[
    "offline_candidate_revision",
    "permanent_offline_revision",
]
OfflineNativeValidationStep = Literal[
    "structural_diff",
    "xg5000_program_check",
    "project_save",
    "project_reopen",
]
OFFLINE_NATIVE_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
    "project_reopen",
)
OFFLINE_NATIVE_CHECK_ONLY_VALIDATION_STEPS = (
    "structural_diff",
    "xg5000_program_check",
    "project_save",
)
pass
pass
pass
# Historical public name remains the v1 contract for existing readers.
pass
pass
pass
pass
pass
pass
pass
pass
pass
pass

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
pass


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


def is_hardware_free_m_operand_target(
    target: dict[str, Any],
    current: dict[str, Any],
    proposed: dict[str, Any],
) -> bool:
    """Recognize the narrow internal-M route that has no hardware dependency."""

    current_value = current.get("value", current.get("operand"))
    proposed_value = proposed.get("value", proposed.get("operand"))
    current_semantics = current.get("semantics")
    proposed_semantics = proposed.get("semantics")
    physical_candidates = target.get(
        "physicalIoCandidates",
        target.get("physical_io_candidates"),
    )
    return (
        isinstance(physical_candidates, list)
        and not physical_candidates
        and isinstance(current_value, str)
        and isinstance(proposed_value, str)
        and re.fullmatch(r"M[0-9A-F]{4,5}", current_value.upper()) is not None
        and re.fullmatch(r"M[0-9A-F]{4,5}", proposed_value.upper()) is not None
        and isinstance(current_semantics, dict)
        and isinstance(proposed_semantics, dict)
        and current_semantics.get("kind") in {"contact_operand", "coil_operand"}
        and proposed_semantics.get("kind") in {"contact_operand", "coil_operand"}
        and current_semantics.get("status") == "confirmed"
        and proposed_semantics.get("status") == "confirmed"
        and current_semantics.get("device") == current_value.upper()
        and proposed_semantics.get("device") == proposed_value.upper()
    )


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
    verification_scope: VerificationScope = "offline_native_reopen"
    validation: list[OfflineNativeValidationStep] = Field(
        default_factory=lambda: list(OFFLINE_NATIVE_VALIDATION_STEPS),
        min_length=3,
        max_length=4,
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
        if (
            not isinstance(token_offset, int)
            or isinstance(token_offset, bool)
            or token_offset < 0
        ):
            raise ValueError("plan.target.tokenOffset must be a non-negative integer")
        if not isinstance(current_value, str) or not current_value:
            raise ValueError("plan.current.value is required")
        if not isinstance(proposed_value, str) or not proposed_value:
            raise ValueError("plan.proposed.value is required")
        if (
            tuple(self.validation)
            != (
                OFFLINE_NATIVE_CHECK_ONLY_VALIDATION_STEPS
                if self.verification_scope == "offline_native_check_only"
                else OFFLINE_NATIVE_VALIDATION_STEPS
            )
        ):
            raise ValueError(
                f"{self.verification_scope} has an invalid native validation sequence"
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

        analysis_evidence_ref = self.target.get(
            "analysisEvidenceRef",
            self.target.get("analysis_evidence_ref"),
        )
        analysis_digest = self.target.get(
            "analysisDigest",
            self.target.get("analysis_digest"),
        )
        analysis_receipt_digest = self.target.get(
            "analysisReceiptDigest",
            self.target.get("analysis_receipt_digest"),
        )
        project_binding_digest = self.target.get(
            "projectBindingDigest",
            self.target.get("project_binding_digest"),
        )
        network_id = self.target.get("networkId", self.target.get("network_id"))
        network_digest = self.target.get(
            "networkDigest",
            self.target.get("network_digest"),
        )
        record_kind = self.target.get("recordKind", self.target.get("record_kind"))
        visual_row = self.target.get("visualRow", self.target.get("visual_row"))
        column = self.target.get("column")
        network_index = self.target.get(
            "networkIndex",
            self.target.get("network_index"),
        )
        rung_id = self.target.get("rungId", self.target.get("rung_id"))
        rung_role = self.target.get("rungRole", self.target.get("rung_role"))
        hardware_evidence_ref = self.target.get(
            "hardwareEvidenceRef",
            self.target.get("hardware_evidence_ref"),
        )
        hardware_context_digest = self.target.get(
            "hardwareContextDigest",
            self.target.get("hardware_context_digest"),
        )
        hardware_receipt_digest = self.target.get(
            "hardwareReceiptDigest",
            self.target.get("hardware_receipt_digest"),
        )
        hardware_project_binding_digest = self.target.get(
            "hardwareProjectBindingDigest",
            self.target.get("hardware_project_binding_digest"),
        )
        hardware_context = self.target.get(
            "hardwareContext",
            self.target.get("hardware_context"),
        )
        hardware_context_binding = self.target.get(
            "hardwareContextBinding",
            self.target.get("hardware_context_binding"),
        )
        runtime_deployment_ready = self.target.get(
            "runtimeDeploymentReady",
            self.target.get("runtime_deployment_ready"),
        )
        runtime_deployment_blockers = self.target.get(
            "runtimeDeploymentBlockingReasons",
            self.target.get("runtime_deployment_blocking_reasons"),
        )
        if self.request_scope == "offline_candidate_revision":
            if (
                runtime_deployment_ready is not False
                or not isinstance(runtime_deployment_blockers, list)
                or not runtime_deployment_blockers
                or any(
                    not isinstance(item, str) or not item
                    for item in runtime_deployment_blockers
                )
                or len(set(runtime_deployment_blockers))
                != len(runtime_deployment_blockers)
                or "online_runtime_deployment_unsupported"
                not in runtime_deployment_blockers
            ):
                raise ValueError(
                    "offline candidate plans require exact runtime deployment blockers"
                )
        elif (
            runtime_deployment_ready is not None
            or runtime_deployment_blockers is not None
        ):
            raise ValueError(
                "runtime-blocked candidate evidence is valid only for "
                "offline_candidate_revision"
            )
        current_semantics = self.current.get("semantics")
        proposed_semantics = self.proposed.get("semantics")
        if not isinstance(current_semantics, dict) or current_semantics.get("status") != "confirmed":
            raise ValueError("plan.current.semantics must be the confirmed native receipt")
        if not isinstance(proposed_semantics, dict) or proposed_semantics.get("status") != "confirmed":
            raise ValueError("plan.proposed.semantics must be the confirmed native receipt")
        # A confirmed internal-M operand may omit hardware authority altogether.
        # If a receipt is supplied, retain the normal binding validation below.
        hardware_free_m_route = (
            all(
                self.target.get(field_name) is None
                for field_name in (
                    "hardwareContextBinding",
                    "hardware_context_binding",
                )
            )
            and is_hardware_free_m_operand_target(
                self.target,
                self.current,
                self.proposed,
            )
        )
        try:
            canonical_evidence_ref = str(uuid.UUID(str(analysis_evidence_ref)))
        except ValueError as exc:
            raise ValueError(
                "plan.target.analysisEvidenceRef must be a canonical UUID"
            ) from exc
        if canonical_evidence_ref != analysis_evidence_ref:
            raise ValueError("plan.target.analysisEvidenceRef must be a canonical UUID")
        if hardware_free_m_route:
            if any(
                self.target.get(field_name) is not None
                for field_name in (
                    "hardwareEvidenceRef",
                    "hardware_evidence_ref",
                    "hardwareContextDigest",
                    "hardware_context_digest",
                    "hardwareReceiptDigest",
                    "hardware_receipt_digest",
                    "hardwareProjectBindingDigest",
                    "hardware_project_binding_digest",
                    "hardwareContext",
                    "hardware_context",
                    "hardwareContextBinding",
                    "hardware_context_binding",
                )
            ):
                raise ValueError("pure M operand plan must not contain hardware authority")
            hardware_context = {}
            hardware_context_binding = {}
        else:
            try:
                canonical_hardware_evidence_ref = str(
                    uuid.UUID(str(hardware_evidence_ref))
                )
            except ValueError as exc:
                raise ValueError(
                    "plan.target.hardwareEvidenceRef must be a canonical UUID"
                ) from exc
            if canonical_hardware_evidence_ref != hardware_evidence_ref:
                raise ValueError("plan.target.hardwareEvidenceRef must be a canonical UUID")
        for field_name, value in (
            ("analysisDigest", analysis_digest),
            ("analysisReceiptDigest", analysis_receipt_digest),
            ("projectBindingDigest", project_binding_digest),
            ("networkDigest", network_digest),
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"plan.target.{field_name} must be a SHA-256 digest")
        if not hardware_free_m_route:
            for field_name, value in (
                ("hardwareContextDigest", hardware_context_digest),
                ("hardwareReceiptDigest", hardware_receipt_digest),
                ("hardwareProjectBindingDigest", hardware_project_binding_digest),
            ):
                if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise ValueError(f"plan.target.{field_name} must be a SHA-256 digest")
        if not isinstance(network_id, str) or not network_id:
            raise ValueError("plan.target.networkId is required")
        if not isinstance(record_kind, str) or not record_kind:
            raise ValueError("plan.target.recordKind is required")
        if not isinstance(visual_row, int) or isinstance(visual_row, bool) or visual_row < 0:
            raise ValueError("plan.target.visualRow must be a non-negative integer")
        if not isinstance(column, int) or isinstance(column, bool) or column < 0:
            raise ValueError("plan.target.column must be a non-negative integer")
        if (
            not isinstance(network_index, int)
            or isinstance(network_index, bool)
            or network_index < 0
        ):
            raise ValueError("plan.target.networkIndex must be a non-negative integer")
        if not isinstance(rung_id, str) or not rung_id:
            raise ValueError("plan.target.rungId is required")
        if rung_role not in {"condition", "output"}:
            raise ValueError("plan.target.rungRole must be condition or output")
        if not hardware_free_m_route and not isinstance(hardware_context, dict):
            raise TypeError("plan.target.hardwareContext must be the exact Host receipt")
        if not hardware_free_m_route and not isinstance(hardware_context_binding, dict):
            raise TypeError(
                "plan.target.hardwareContextBinding must be the exact analysis receipt"
            )
        timer_areas = hardware_context.get(
            "timerAreas",
            hardware_context.get("timer_areas"),
        )
        io_modules = hardware_context.get(
            "ioModules",
            hardware_context.get("io_modules"),
        )
        io_mapping = hardware_context.get(
            "ioMapping",
            hardware_context.get("io_mapping"),
        )
        external_wiring_observed = hardware_context.get(
            "externalWiringObserved",
            hardware_context.get("external_wiring_observed"),
        )
        if not hardware_free_m_route and not isinstance(hardware_context.get("cpu"), dict):
            raise TypeError("plan.target.hardwareContext.cpu is required")
        if not hardware_free_m_route and not isinstance(timer_areas, list):
            raise TypeError("plan.target.hardwareContext.timerAreas is required")
        if not hardware_free_m_route and not isinstance(io_modules, list):
            raise TypeError("plan.target.hardwareContext.ioModules is required")
        if not hardware_free_m_route and not isinstance(io_mapping, dict):
            raise TypeError("plan.target.hardwareContext.ioMapping is required")
        if not hardware_free_m_route and not isinstance(external_wiring_observed, bool):
            raise TypeError(
                "plan.target.hardwareContext.externalWiringObserved is required"
            )
        hardware_blockers = hardware_context.get("blockers")
        hardware_ready = hardware_context.get(
            "offlineCandidateReady",
            hardware_context.get("offline_candidate_ready"),
        )
        if not hardware_free_m_route and (
            not isinstance(hardware_blockers, list)
            or any(
                not isinstance(item, str) or not item
                for item in hardware_blockers
            )
            or not isinstance(hardware_ready, bool)
            or hardware_ready != (not hardware_blockers)
        ):
            raise ValueError("plan.target.hardwareContext readiness is inconsistent")
        required_hardware_context = hardware_context_binding.get(
            "required_context"
        )
        allowed_hardware_context = {
            "cpu",
            "timer_area",
            "io_mapping",
            "external_wiring",
        }
        cpu_context = hardware_context.get("cpu")
        observed_hardware_status = {
            "cpu_status": (
                cpu_context.get("status")
                if isinstance(cpu_context, dict)
                else "unobserved"
            ),
            "timer_area_status": (
                "observed"
                if timer_areas
                and all(
                    isinstance(area, dict) and area.get("status") == "observed"
                    for area in timer_areas
                )
                else "unobserved"
            ),
            "io_mapping_status": (
                io_mapping.get("status")
                if isinstance(io_mapping, dict)
                else "unobserved"
            ),
            "external_wiring_observed": external_wiring_observed,
            "external_wiring_status": (
                "observed" if external_wiring_observed else "unobserved"
            ),
        }
        hardware_blocker_context = {
            "active_configuration_unresolved": "cpu",
            "cpu_identity_conflict": "cpu",
            "cpu_model_unobserved": "cpu",
            "timer_area_parameter_unobserved": "timer_area",
            "timer_area_ranges_overlap": "timer_area",
            "io_parameter_unobserved": "io_mapping",
            "io_device_offset_parameter_unobserved": "io_mapping",
        }
        relevant_hardware_blockers = (
            [
                blocker
                for blocker in hardware_blockers
                if hardware_blocker_context.get(blocker)
                in required_hardware_context
                or blocker not in hardware_blocker_context
            ]
            if isinstance(required_hardware_context, list)
            else []
        )
        if not hardware_free_m_route and (
            hardware_context_binding.get("schema")
            != "xg5000.project.hardware-context/1.0"
            or hardware_context_binding.get("context_digest")
            != hardware_context_digest
            or not isinstance(required_hardware_context, list)
            or not required_hardware_context
            or any(not isinstance(item, str) for item in required_hardware_context)
            or len(set(required_hardware_context)) != len(required_hardware_context)
            or any(
                item not in allowed_hardware_context
                for item in required_hardware_context
            )
            or (
                self.request_scope != "offline_candidate_revision"
                and (
                    hardware_context_binding.get("blocking_reasons") != []
                    or any(
                        hardware_context_binding.get(name) != status
                        for name, status in observed_hardware_status.items()
                    )
                    or any(
                        hardware_context_binding.get(f"{item}_status")
                        != "observed"
                        for item in required_hardware_context
                    )
                    or bool(relevant_hardware_blockers)
                )
            )
        ):
            raise ValueError(
                "plan.target.hardwareContextBinding has unobserved required context"
            )

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
        if current_value == proposed_value:
            raise ValueError("plan proposed operand must differ from the current operand")
        try:
            current_length = len(current_value.encode("utf-16-le"))
            proposed_length = len(proposed_value.encode("utf-16-le"))
        except UnicodeEncodeError as exc:
            raise ValueError("plan current/proposed operands must be valid UTF-16 text") from exc
        if current_length != proposed_length:
            raise ValueError(
                "plan current/proposed native operands must have equal UTF-16 byte length"
            )


pass
pass
pass

pass
pass
pass
pass
pass








pass
pass
pass
pass
pass
pass
pass
pass


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


CHANGE_ATTEMPT_IDENTITY_SCHEMA = "controlstudio-change-attempt-identity/v1"
CHANGE_ATTEMPT_RESULT_SCHEMA = "controlstudio-change-attempt-result/v1"


class ChangeAttemptIdentityV1(ApiModel):
    """Immutable Host identity for one candidate/source execution attempt.

    A conversation and working copy may survive a follow-up request, but the
    source revision, candidate, or approved plan cannot silently reuse an old
    native receipt.  ``attempt_id`` is deliberately derived from every input
    below, so resume is stable only for the exact same attempt inputs.
    """

    schema_value: Literal["controlstudio-change-attempt-identity/v1"] = Field(
        default=CHANGE_ATTEMPT_IDENTITY_SCHEMA,
        alias="schema",
    )
    attempt_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    transaction_id: str = Field(min_length=1, max_length=256)
    workspace_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    source_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    working_artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def serialize_identity(self, handler: Any) -> dict[str, Any]:
        serialized = handler(self)
        if "schema_value" in serialized:
            serialized["schema"] = serialized.pop("schema_value")
        return serialized

    @model_validator(mode="after")
    def validate_attempt_digest(self) -> ChangeAttemptIdentityV1:
        unsigned = self.model_dump(mode="json", exclude={"attempt_id"})
        if self.attempt_id != _canonical_sha256(unsigned):
            raise ValueError("change attempt identity digest is invalid")
        return self


def canonical_change_attempt_identity(
    *,
    transaction_id: str,
    workspace_id: str,
    revision_id: str,
    source_artifact_hash: str,
    working_artifact_hash: str,
    candidate_digest: str,
    plan_digest: str,
) -> dict[str, Any]:
    """Build and validate the one canonical attempt identity envelope."""

    unsigned = {
        "schema": CHANGE_ATTEMPT_IDENTITY_SCHEMA,
        "transaction_id": transaction_id,
        "workspace_id": workspace_id,
        "revision_id": revision_id,
        "source_artifact_hash": source_artifact_hash,
        "working_artifact_hash": working_artifact_hash,
        "candidate_digest": candidate_digest,
        "plan_digest": plan_digest,
    }
    identity = {
        **unsigned,
        "attempt_id": _canonical_sha256(unsigned),
    }
    return ChangeAttemptIdentityV1.model_validate(identity).model_dump(mode="json")


class ChangeAttemptResultV1(ApiModel):
    """Host binding of native persistence/readback and the test receipt."""

    schema_value: Literal["controlstudio-change-attempt-result/v1"] = Field(
        default=CHANGE_ATTEMPT_RESULT_SCHEMA,
        alias="schema",
    )
    attempt: ChangeAttemptIdentityV1
    native_persisted_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_readback_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def serialize_result(self, handler: Any) -> dict[str, Any]:
        serialized = handler(self)
        if "schema_value" in serialized:
            serialized["schema"] = serialized.pop("schema_value")
        return serialized

    @model_validator(mode="after")
    def validate_result_digest(self) -> ChangeAttemptResultV1:
        unsigned = self.model_dump(mode="json", exclude={"binding_digest"})
        if self.binding_digest != _canonical_sha256(unsigned):
            raise ValueError("change attempt result binding digest is invalid")
        return self


def canonical_change_attempt_result(
    *,
    attempt: ChangeAttemptIdentityV1 | dict[str, Any],
    native_persisted_hash: str,
    native_readback_hash: str,
    test_receipt_digest: str,
) -> dict[str, Any]:
    """Build and validate the Host-side result binding for one attempt."""

    normalized_attempt = ChangeAttemptIdentityV1.model_validate(attempt).model_dump(
        mode="json"
    )
    unsigned = {
        "schema": CHANGE_ATTEMPT_RESULT_SCHEMA,
        "attempt": normalized_attempt,
        "native_persisted_hash": native_persisted_hash,
        "native_readback_hash": native_readback_hash,
        "test_receipt_digest": test_receipt_digest,
    }
    return ChangeAttemptResultV1.model_validate(
        {**unsigned, "binding_digest": _canonical_sha256(unsigned)}
    ).model_dump(mode="json")








# Resolve historical planning metadata for stored conversation compatibility.
ProjectPlanNativeDesignV1.model_rebuild()






























pass






pass




























ChangePlanContract = ChangePlanV1


class CreateChangeTransactionRequestV1(ApiModel):
    operation_id: str = Field(min_length=8, max_length=128)
    conversation_id: str | None = None
    run_id: str | None = None
    intent: str = Field(min_length=1, max_length=10_000)
    risk_level: Literal["low", "medium", "high", "critical"]
    plan: ChangePlanContract
    approval_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value):
            raise ValueError("operation_id contains invalid characters")
        return value

    @model_validator(mode="after")
    def validate_new_transaction_scope(self) -> CreateChangeTransactionRequestV1:
        scope_pair = (
            self.plan.request_scope,
            self.plan.verification_scope,
        )
        allowed_scope_pairs = {
            ("offline_candidate_revision", "offline_native_reopen"),
            ("permanent_offline_revision", "offline_native_check_only"),
        }
        if scope_pair not in allowed_scope_pairs:
            raise ValueError(
                "new change transactions require candidate+reopen "
                "or permanent+check-only scope"
            )
        return self


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
    """Explicit XG5000 target for recovery-only reconciliation."""

    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    relaunch_if_unavailable: bool = False
    # This is deliberately not a general artifact rebind.  It authorizes the
    # MCP-owned, journal-pinned backup restoration which precedes the existing
    # recovery-only relaunch path.
    restore_verified_backup: bool = False
    expected_current_project_hash: str | None = Field(default=None)

    @field_validator("expected_current_project_hash")
    @classmethod
    def validate_expected_current_project_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("expected_current_project_hash must be a SHA-256 value")
        return normalized

    @model_validator(mode="after")
    def validate_verified_backup_restore_request(self) -> ReconcileChangeTransactionRequestV1:
        if self.restore_verified_backup and not self.expected_current_project_hash:
            raise ValueError(
                "expected_current_project_hash is required when restore_verified_backup is true"
            )
        if self.restore_verified_backup and not self.relaunch_if_unavailable:
            raise ValueError(
                "restore_verified_backup requires recovery-only XG5000 relaunch"
            )
        return self


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
    execution_target_identity: ChangeTargetIdentityV1 | None = None
    mutation_scope: str
    risk_level: Literal["low", "medium", "high", "critical"]
    intent: str
    plan: ChangePlanContract
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


ReasoningEffortV1 = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
]


class ModelSettingsV1(ApiModel):
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    base_url: str
    model: str
    custom_header_names: list[str]
    timeout_seconds: float
    reasoning_effort: ReasoningEffortV1 | None = None
    client_input_limit_tokens: int | None = Field(
        default=None, gt=0, le=MAX_CLIENT_INPUT_LIMIT_TOKENS, strict=True
    )
    has_api_key: bool
    external_endpoint: bool
    updated_at: datetime | None = None
    model_context: ModelContextV1 | None = None


MODEL_CONTEXT_REQUIRED_INPUT_TOKENS = 200_000
MODEL_CONTEXT_OUTPUT_RESERVE_TOKENS = 16_608

ModelContextStatusV1 = Literal["verified", "insufficient", "unknown"]


class ModelContextV1(ApiModel):
    """An explicit, provider-neutral context-capacity observation.

    The Host may forward this projection when a trusted provider capability
    source has actually measured the endpoint.  ``None`` is intentional: it
    means the endpoint's capacity is unknown, not that the product requirement
    has been met.  The contract keeps the 200k input requirement explicit so
    callers cannot silently substitute the currently observed 196608 window.
    """

    detected_total_tokens: int | None = Field(default=None, ge=0, strict=True)
    required_input_tokens: int = Field(
        default=MODEL_CONTEXT_REQUIRED_INPUT_TOKENS,
        ge=1,
        strict=True,
    )
    output_reserve_tokens: int = Field(
        default=MODEL_CONTEXT_OUTPUT_RESERVE_TOKENS,
        ge=MODEL_CONTEXT_OUTPUT_RESERVE_TOKENS,
        strict=True,
    )
    source: str = Field(default="unknown", min_length=1, max_length=200)
    history_budget_tokens: int | None = Field(default=None, ge=0, strict=True)
    history_budget_status: ModelContextStatusV1 = "unknown"
    history_budget_source: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model context source must be non-empty")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def discard_serialized_status(cls, value: Any) -> Any:
        """Treat the wire-only capability status as a derived read value.

        ``ModelContextV1`` is also the persistence boundary for the provider
        observation.  A serialized observation now contains the computed
        ``status`` field, so old and new stored payloads must be decodable
        without allowing that field to become caller-controlled state.  The
        canonical status is always recomputed from the measured fields below.
        """

        if isinstance(value, dict) and "status" in value:
            value = dict(value)
            value.pop("status", None)
        return value

    @model_validator(mode="after")
    def validate_requirement(self) -> ModelContextV1:
        if self.required_input_tokens != MODEL_CONTEXT_REQUIRED_INPUT_TOKENS:
            raise ValueError(
                "model context required_input_tokens must remain 200000"
            )
        if self.history_budget_status == "verified" and (
            self.history_budget_tokens is None or self.history_budget_source is None
        ):
            raise ValueError(
                "verified history budget requires history_budget_tokens and "
                "history_budget_source"
            )
        if self.history_budget_status == "insufficient" and (
            self.history_budget_tokens is None and self.history_budget_source is None
        ):
            raise ValueError(
                "insufficient history budget requires a measured budget or source"
            )
        return self

    @computed_field(return_type=ModelContextStatusV1)
    @property
    def status(self) -> ModelContextStatusV1:
        if self.detected_total_tokens is None:
            return "unknown"
        if self.detected_total_tokens < (
            self.required_input_tokens + self.output_reserve_tokens
        ):
            return "insufficient"
        return "verified"

    @property
    def available_input_tokens(self) -> int | None:
        if self.detected_total_tokens is None:
            return None
        return max(0, self.detected_total_tokens - self.output_reserve_tokens)

    @property
    def product_capability_status(self) -> ModelContextStatusV1:
        return self.status


class SaveModelSettingsRequestV1(ApiModel):
    protocol: Literal["openai_chat_completions"] = "openai_chat_completions"
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    api_key: str | None = Field(default=None, max_length=2048)
    custom_headers: dict[str, str] | None = None
    timeout_seconds: float = Field(default=60, ge=1, le=3600)
    reasoning_effort: ReasoningEffortV1 | None = None
    client_input_limit_tokens: int | None = Field(
        default=None, gt=0, le=MAX_CLIENT_INPUT_LIMIT_TOKENS, strict=True
    )
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
    model_context: ModelContextV1 | None = None


class XG5000InstancesV1(ApiModel):
    status: Literal["ready"]
    instances: list[dict[str, Any]]


class ControllerRestorePreflightRequestV1(ApiModel):
    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)


class ControllerRestoreBeginRequestV1(ControllerRestorePreflightRequestV1):
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    timeout_seconds: float = Field(default=15, ge=1, le=60)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if str(uuid.UUID(value)) != value:
            raise ValueError("operationId must use canonical UUID form")
        return value


class ControllerRestoreCompleteRequestV1(ApiModel):
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    timeout_seconds: float = Field(default=60, ge=10, le=120)

    @field_validator("operation_id")
    @classmethod
    def validate_operation_id(cls, value: str) -> str:
        if str(uuid.UUID(value)) != value:
            raise ValueError("operationId must use canonical UUID form")
        return value


class ControllerRestoreExecutableV1(ApiModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_version: str = Field(min_length=1, max_length=128)
    product_version: str = Field(min_length=1, max_length=128)
    pe_machine: str = Field(min_length=1, max_length=32)


class ControllerRestoreMenuResolutionV1(ApiModel):
    requested_path: list[str] = Field(min_length=1, max_length=8)
    actual_path: list[str] = Field(min_length=1, max_length=8)
    command_id: int = Field(ge=0)
    enabled: bool


class ControllerRestoreTargetV1(ApiModel):
    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    process_create_time: float = Field(gt=0)
    title: str = Field(max_length=512)
    class_name: str = Field(max_length=256)
    executable: ControllerRestoreExecutableV1


class ControllerRestoreSurfaceProfileV1(ApiModel):
    receipt_schema: Literal[
        "controlstudio-controller-restore-surface-profile/v1"
    ] = Field(alias="schema")
    executable: ControllerRestoreExecutableV1
    open_from_plc: ControllerRestoreMenuResolutionV1 | None
    save_as: ControllerRestoreMenuResolutionV1 | None
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_profile_digest(self) -> ControllerRestoreSurfaceProfileV1:
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"profile_digest"},
        )
        if _canonical_sha256(unsigned) != self.profile_digest:
            raise ValueError("controller restore surface profile digest mismatched")
        return self


ControllerRestoreBlockerV1 = Literal[
    "open_from_plc_menu_unresolved",
    "open_from_plc_menu_disabled",
    "save_as_menu_unresolved",
    "save_as_menu_disabled",
    "modal_dialog_present",
]


class ControllerRestorePreflightV1(ApiModel):
    receipt_schema: Literal["controlstudio-controller-restore-preflight/v1"] = Field(
        alias="schema"
    )
    observed_at: str = Field(min_length=1, max_length=64)
    target: ControllerRestoreTargetV1
    surface_profile: ControllerRestoreSurfaceProfileV1
    visible_dialog_count: int = Field(ge=0)
    ready: bool
    blockers: list[ControllerRestoreBlockerV1] = Field(max_length=5)
    read_only: Literal[True]
    invoked: Literal[False]
    limitations: list[str] = Field(min_length=1, max_length=8)
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_preflight_receipt(self) -> ControllerRestorePreflightV1:
        try:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("controller restore observedAt must be an ISO timestamp") from exc
        if observed.tzinfo is None:
            raise ValueError("controller restore observedAt must include a timezone")
        if self.surface_profile.executable != self.target.executable:
            raise ValueError("controller restore executable identity mismatched")
        expected_blockers: list[str] = []
        for name, value in (
            ("open_from_plc", self.surface_profile.open_from_plc),
            ("save_as", self.surface_profile.save_as),
        ):
            if value is None:
                expected_blockers.append(f"{name}_menu_unresolved")
            elif value.enabled is not True:
                expected_blockers.append(f"{name}_menu_disabled")
        if self.visible_dialog_count:
            expected_blockers.append("modal_dialog_present")
        expected_ready = not expected_blockers
        if self.blockers != expected_blockers or self.ready != expected_ready:
            raise ValueError("controller restore readiness does not match its observations")
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_digest"},
        )
        if _canonical_sha256(unsigned) != self.receipt_digest:
            raise ValueError("controller restore preflight receipt digest mismatched")
        return self


class ControllerRestoreDialogV1(ApiModel):
    hwnd: int = Field(gt=0)
    title: str = Field(max_length=512)
    class_name: str = Field(max_length=256)


class ControllerRestoreHandoffV1(ApiModel):
    receipt_schema: Literal["controlstudio-controller-restore-handoff/v1"] = Field(
        alias="schema"
    )
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    started_at: datetime
    expires_at: datetime
    target: ControllerRestoreTargetV1
    preflight_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dialog: ControllerRestoreDialogV1
    invoked: Literal[True]
    confirmed: Literal[False]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_handoff(self) -> ControllerRestoreHandoffV1:
        if str(uuid.UUID(self.operation_id)) != self.operation_id:
            raise ValueError("restore handoff operationId is not canonical")
        if self.expires_at <= self.started_at:
            raise ValueError("restore handoff expiry must follow its start")
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_digest"},
        )
        if _canonical_sha256(unsigned) != self.receipt_digest:
            raise ValueError("controller restore handoff digest mismatched")
        return self


class ReviewedProjectPlanBindingV1(ApiModel):
    schema_version: Literal["2"]
    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_brief_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    attachment_revision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_section_ids: list[str] = Field(min_length=1, max_length=100)
    reviewed_item_ids: list[str] = Field(min_length=1, max_length=100_000)

    @field_validator("reviewed_section_ids")
    @classmethod
    def validate_reviewed_section_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("reviewedSectionIds contains an invalid section id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedSectionIds must not contain duplicates")
        return value

    @field_validator("reviewed_item_ids")
    @classmethod
    def validate_reviewed_item_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 2_000 for item in value):
            raise ValueError("reviewedItemIds contains an invalid item id")
        if len(set(value)) != len(value):
            raise ValueError("reviewedItemIds must not contain duplicates")
        return value




















# ``SendMessageRequestV1`` is defined before these native request models so the
# public message DTO can keep workflow models grouped with other message types.
# Resolve all forward references only after the complete authority contract is
# available.
SendMessageRequestV1.model_rebuild()




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
    conversation_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
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
    evidence_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )


class TraceLSControllerRequestV1(ReadLSControllerRequestV1):
    sample_count: int = Field(ge=2, le=64)
    interval_ms: int = Field(ge=50, le=5_000)

    @model_validator(mode="after")
    def validate_trace_window(self) -> TraceLSControllerRequestV1:
        if (self.sample_count - 1) * self.interval_ms > 30_000:
            raise ValueError("controller trace sampling window cannot exceed 30 seconds")
        return self


class LSControllerTraceSampleV1(ApiModel):
    index: int = Field(ge=0, le=63)
    observed_at: datetime
    offset_ms: int = Field(ge=0)
    values: list[LSControllerValueV1] = Field(min_length=1, max_length=16)


class LSControllerTraceSummaryV1(ApiModel):
    variable: str
    first_unsigned_value: int = Field(ge=0)
    last_unsigned_value: int = Field(ge=0)
    minimum_unsigned_value: int = Field(ge=0)
    maximum_unsigned_value: int = Field(ge=0)
    change_count: int = Field(ge=0, le=63)
    changed: bool

    @model_validator(mode="after")
    def validate_summary(self) -> LSControllerTraceSummaryV1:
        if self.minimum_unsigned_value > self.maximum_unsigned_value:
            raise ValueError("controller trace summary minimum cannot exceed maximum")
        if self.changed != (self.change_count > 0):
            raise ValueError("controller trace summary changed flag is inconsistent")
        for value in (self.first_unsigned_value, self.last_unsigned_value):
            if not self.minimum_unsigned_value <= value <= self.maximum_unsigned_value:
                raise ValueError("controller trace summary endpoint is outside its range")
        return self


class LSControllerTraceTransitionV1(ApiModel):
    variable: str
    from_sample: int = Field(ge=0, le=62)
    to_sample: int = Field(ge=1, le=63)
    observed_at: datetime
    from_raw_hex: str = Field(pattern=r"^[0-9A-F]+$")
    to_raw_hex: str = Field(pattern=r"^[0-9A-F]+$")
    from_unsigned_value: int = Field(ge=0)
    to_unsigned_value: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_adjacent_transition(self) -> LSControllerTraceTransitionV1:
        if self.to_sample != self.from_sample + 1:
            raise ValueError("controller trace transition must connect adjacent samples")
        if self.from_raw_hex == self.to_raw_hex:
            raise ValueError("controller trace transition must contain a value change")
        return self


class LSControllerTraceV1(ApiModel):
    schema_version: str
    operation: Literal["controller.ls.trace"]
    endpoint: LSControllerEndpointV1
    started_at: datetime
    completed_at: datetime
    data_type: LSControllerDataType
    variables: list[str] = Field(min_length=1, max_length=16)
    sample_count: int = Field(ge=2, le=64)
    interval_ms: int = Field(ge=50, le=5_000)
    capture_duration_ms: int = Field(ge=0)
    status_before: LSControllerRuntimeStatusV1
    status_after: LSControllerRuntimeStatusV1
    identity_before: LSControllerProvisionalIdentityV1
    identity_after: LSControllerProvisionalIdentityV1
    identity_stable: Literal[True]
    mode_stable: bool
    samples: list[LSControllerTraceSampleV1] = Field(min_length=2, max_length=64)
    summaries: list[LSControllerTraceSummaryV1] = Field(min_length=1, max_length=16)
    transition_count: int = Field(ge=0)
    transition_returned_count: int = Field(ge=0, le=256)
    transitions_truncated: bool
    transitions: list[LSControllerTraceTransitionV1] = Field(max_length=256)
    limitations: list[str] = Field(min_length=2, max_length=20)
    read_only: Literal[True]
    strong_enough_for_write: Literal[False]
    evidence_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )

    @model_validator(mode="after")
    def validate_trace_contract(self) -> LSControllerTraceV1:
        if self.sample_count != len(self.samples):
            raise ValueError("controller trace sample count is inconsistent")
        if [sample.index for sample in self.samples] != list(range(self.sample_count)):
            raise ValueError("controller trace sample indexes are not contiguous")
        expected_variables = [item.upper() for item in self.variables]
        if len(set(expected_variables)) != len(expected_variables):
            raise ValueError("controller trace variables must be unique")
        for sample in self.samples:
            observed = [item.variable.upper() for item in sample.values]
            if observed != expected_variables:
                raise ValueError("controller trace sample variables changed")
        if [item.variable.upper() for item in self.summaries] != expected_variables:
            raise ValueError("controller trace summary variables changed")
        if self.transition_returned_count != len(self.transitions):
            raise ValueError("controller trace returned transition count is inconsistent")
        if self.transition_count < self.transition_returned_count:
            raise ValueError("controller trace transition count is inconsistent")
        if self.transitions_truncated != (
            self.transition_count != self.transition_returned_count
        ):
            raise ValueError("controller trace transition truncation is inconsistent")
        if (
            self.identity_before.fingerprint_sha256
            != self.identity_after.fingerprint_sha256
        ):
            raise ValueError("controller identity changed during trace")
        if self.completed_at < self.started_at:
            raise ValueError("controller trace completion precedes its start")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("controller trace limitations cannot contain blanks")
        return self


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
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
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


class ControllerRestoreWorkspaceProvenanceV1(ApiModel):
    receipt_schema: Literal[
        "controlstudio-controller-restore-workspace-provenance/v1"
    ] = Field(alias="schema")
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    handoff_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_pid: int = Field(gt=0)
    source_hwnd: int = Field(gt=0)
    saved_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_provenance(self) -> ControllerRestoreWorkspaceProvenanceV1:
        if str(uuid.UUID(self.operation_id)) != self.operation_id:
            raise ValueError("restore provenance operationId is not canonical")
        unsigned = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_digest"},
        )
        if _canonical_sha256(unsigned) != self.receipt_digest:
            raise ValueError("controller restore provenance digest mismatched")
        return self


class NewBuildWorkspaceProvenanceV1(ApiModel):
    receipt_schema: Literal[
        "controlstudio-new-build-workspace-provenance/v1"
    ] = Field(alias="schema")
    # Compatibility reader for already captured legacy builds.
    native_project_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    reviewed_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_bundle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_provenance(self) -> NewBuildWorkspaceProvenanceV1:
        if self.native_project_id is None or str(uuid.UUID(self.native_project_id)) != self.native_project_id:
            raise ValueError("new-build provenance nativeProjectId is not canonical")
        unsigned = {
            "schema": self.receipt_schema,
            "native_project_id": self.native_project_id,
            "reviewed_plan_digest": self.reviewed_plan_digest,
            "build_bundle_id": self.build_bundle_id,
            "build_receipt_digest": self.build_receipt_digest,
            "saved_project_sha256": self.saved_project_sha256,
        }
        if _canonical_sha256(unsigned) != self.receipt_digest:
            raise ValueError("new-build provenance digest mismatched")
        return self


class NativeCreationWorkspaceProvenanceV1(ApiModel):
    receipt_schema: Literal["controlstudio-native-creation-provenance/v1"] = Field(alias="schema")
    workspace_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    creation_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_provenance(self) -> NativeCreationWorkspaceProvenanceV1:
        if str(uuid.UUID(self.workspace_id)) != self.workspace_id:
            raise ValueError("native creation workspace identity is not canonical")
        unsigned = {"schema": self.receipt_schema, "workspace_id": self.workspace_id,
                    "creation_receipt_digest": self.creation_receipt_digest,
                    "saved_project_sha256": self.saved_project_sha256}
        if _canonical_sha256(unsigned) != self.receipt_digest:
            raise ValueError("native creation provenance digest mismatched")
        return self


class LocalWorkspaceV1(ApiModel):
    workspace_id: str
    revision_id: str
    source_kind: Literal["local_file", "restored_from_controller", "new_build"]
    vendor: Literal["ls"]
    engineering_system: Literal["XG5000"]
    created_at: datetime
    source: LocalWorkspaceSourceV1
    working_copy: LocalWorkingCopyV1
    provenance: (
        ControllerRestoreWorkspaceProvenanceV1
        | NewBuildWorkspaceProvenanceV1
        | NativeCreationWorkspaceProvenanceV1
        | None
    ) = None

    @model_validator(mode="after")
    def validate_source_provenance(self) -> LocalWorkspaceV1:
        expected_provenance_type = {
            "local_file": type(None),
            "restored_from_controller": ControllerRestoreWorkspaceProvenanceV1,
            "new_build": (NewBuildWorkspaceProvenanceV1, NativeCreationWorkspaceProvenanceV1),
        }[self.source_kind]
        if not isinstance(self.provenance, expected_provenance_type):
            raise ValueError("workspace source kind and provenance disagree")
        if isinstance(self.provenance, NativeCreationWorkspaceProvenanceV1) and self.provenance.workspace_id != self.workspace_id:
            raise ValueError("native creation provenance belongs to another workspace")
        if (
            self.provenance is not None
            and self.provenance.saved_project_sha256 != self.source.sha256
        ):
            raise ValueError("workspace provenance does not match workspace source")
        return self


class WorkspaceCompareFileV1(ApiModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class WorkspaceCompareDigestItemV1(ApiModel):
    id: str = Field(min_length=1, max_length=4_096)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceCompareChangedItemV1(ApiModel):
    id: str = Field(min_length=1, max_length=4_096)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    working_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceCompareSectionV1(ApiModel):
    source_count: int = Field(ge=0)
    working_count: int = Field(ge=0)
    added_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    added: list[WorkspaceCompareDigestItemV1]
    removed: list[WorkspaceCompareDigestItemV1]
    changed: list[WorkspaceCompareChangedItemV1]
    details_truncated: bool


class WorkspaceStructureCompareV1(ApiModel):
    status: Literal["same", "changed", "partial"]
    source_index_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    working_index_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: dict[str, WorkspaceCompareSectionV1]

    @field_validator("sections")
    @classmethod
    def validate_sections(
        cls,
        value: dict[str, WorkspaceCompareSectionV1],
    ) -> dict[str, WorkspaceCompareSectionV1]:
        expected = {
            "configurations",
            "tasks",
            "code_units",
            "symbols",
            "devices",
            "call_edges",
            "parameters",
            "modules",
            "communications",
            "opaque_containers",
        }
        if set(value) != expected:
            raise ValueError("workspace structure comparison section set is incomplete")
        return value


class WorkspaceCompareLimitationV1(ApiModel):
    code: str = Field(min_length=1, max_length=128)
    detail: str = Field(min_length=1, max_length=512)


class LocalWorkspaceCompareV1(ApiModel):
    receipt_schema: Literal["xg5000.workspace-compare/1.0"] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    ok: Literal[True]
    verified: Literal[True]
    workspace_id: str
    revision_id: str
    source_kind: Literal["local_file", "restored_from_controller", "new_build"]
    source: WorkspaceCompareFileV1
    working_copy: WorkspaceCompareFileV1
    file_identical: bool
    status: Literal[
        "identical",
        "file_changed",
        "file_changed_structure_partial",
        "file_changed_structure_changed",
        "file_changed_structure_same",
    ]
    structure: WorkspaceStructureCompareV1 | None = None
    limitations: list[WorkspaceCompareLimitationV1]
    compare_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_compare_state(self) -> LocalWorkspaceCompareV1:
        if self.file_identical != (self.status == "identical"):
            raise ValueError("workspace compare file identity and status disagree")
        if self.file_identical:
            if self.structure is not None and self.structure.status not in {"same", "partial"}:
                raise ValueError("identical workspace comparison structure is invalid")
            return self
        structure_status = self.status.startswith("file_changed_structure_")
        if structure_status != (self.structure is not None):
            raise ValueError("workspace compare structure and status disagree")
        if self.structure is not None and not self.status.endswith(self.structure.status):
            raise ValueError("workspace compare structure status disagrees with the result status")
        return self


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
        "controlled_launch_official_open_read_only",
        "controlled_launch_native_file_read_only",
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
    catalog_binding_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    catalog_source: Literal["saved_working_copy"] | None = None


class EngineeringProgramFocusRequestV1(ApiModel):
    display_intent: Literal["LD"]
    catalog_binding_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EngineeringProgramFocusV1(ApiModel):
    status: Literal["focused"] = "focused"
    verified: Literal[True]
    read_only: Literal[True]
    project_unchanged: Literal[True]
    program: EngineeringProgramV1


class EngineeringLocationFocusRequestV1(ApiModel):
    location: EngineeringLocationV2


class EngineeringLocationFocusV1(ApiModel):
    status: Literal["focused"] = "focused"
    verified: Literal[True]
    read_only: Literal[True]
    project_unchanged: Literal[True]
    program: EngineeringProgramV1
    location: EngineeringLocationV2
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")




class ChangeTargetFocusV1(ApiModel):
    status: Literal["focused"] = "focused"
    verified: Literal[True]
    read_only: Literal[True]
    project_unchanged: Literal[True]
    transaction_id: str = Field(min_length=1, max_length=128)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    focus_kind: Literal["native_location",  "program"]
    program: EngineeringProgramV1
    location: EngineeringLocationV2 | None = None
    payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_focus_kind(self) -> ChangeTargetFocusV1:
        native_location = self.focus_kind == "native_location"
        if native_location != (
            self.location is not None and self.payload_sha256 is not None
        ):
            raise ValueError("native_location focus requires exact location and payload digest")
        if not native_location and (
            self.location is not None or self.payload_sha256 is not None
        ):
            raise ValueError("only native_location focus may carry an LD location")
        return self


class OpenLocalWorkspaceResponseV1(ApiModel):
    workspace: LocalWorkspaceV1
    launch: XG5000ProjectLaunchV1 | None = None
    session: EngineeringSessionV1 | None = None


class ControllerRestoreCompletedV1(OpenLocalWorkspaceResponseV1):
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    restoration_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved_project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttachXG5000RequestV1(ApiModel):
    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    project_path: str | None = Field(default=None, max_length=32_768)
    project_hash: str | None = Field(default=None, min_length=64, max_length=64)
    # Recovery must never silently bind a selected old IDE window to whichever
    # workspace happens to be active in the browser.
    allow_unbound_existing_target: bool = False


class AttachXG5000ResponseV1(ApiModel):
    status: Literal["attached"] = "attached"
    session: EngineeringSessionV1


class PrepareOfflineCandidateSessionRequestV1(ApiModel):
    conversation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class PrepareOfflineCandidateSessionResponseV1(ApiModel):
    """A prepared candidate remains subject to the normal approval broker."""

    status: Literal["offline_candidate_bound"] = "offline_candidate_bound"
    session: EngineeringSessionV1


class LaunchIdleXG5000RequestV1(ApiModel):
    confirm_idle_launch: Literal[True]
    timeout_seconds: float = Field(default=20, ge=0.1, le=30)
    launch_mode: Literal["when_none", "new_instance"] = "when_none"


class LaunchIdleXG5000ResponseV1(ApiModel):
    operation: Literal["xg5000.launch_idle"]
    verified: Literal[True]
    status: Literal["launched"]
    launch_action: Literal["process_start"]
    launch_mode: Literal["when_none", "new_instance"] = "when_none"
    launched_identity: dict[str, Any]
    idle_binding: dict[str, Any]
    no_existing_instances: StrictBool
    blank_window: Literal[True]
    modal_observed: Literal[False]
    attention_reason: None
    process_exited: Literal[False]
    process_terminated: Literal[False]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_launch_mode(self) -> LaunchIdleXG5000ResponseV1:
        if self.launch_mode == "when_none" and not self.no_existing_instances:
            raise ValueError("when-none idle launch requires no existing instances")
        return self


class LaunchIdleXG5000AttentionV1(ApiModel):
    operation: Literal["xg5000.launch_idle"]
    verified: Literal[False]
    status: Literal["needs_attention"]
    launch_action: Literal["process_start"]
    launch_mode: Literal["when_none", "new_instance"] = "when_none"
    launched_identity: dict[str, Any]
    idle_binding: dict[str, Any] | None
    no_existing_instances: StrictBool
    blank_window: Literal[False]
    modal_observed: bool
    attention_reason: Literal[
        "launch_timeout",
        "nonblank_window",
        "owned_modal",
        "process_exited",
    ]
    process_exited: bool
    process_terminated: Literal[False]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attention_outcome(self) -> LaunchIdleXG5000AttentionV1:
        if self.launch_mode == "when_none" and not self.no_existing_instances:
            raise ValueError("when-none idle launch requires no existing instances")
        if self.modal_observed != (self.attention_reason == "owned_modal"):
            raise ValueError("idle-launch attention receipt has an invalid modal outcome")
        if self.idle_binding is None and self.attention_reason not in {
            "launch_timeout",
            "process_exited",
        }:
            raise ValueError("idle-launch attention receipt lost its required identity")
        return self


class ReviewedNoSaveCloseRequestV1(ApiModel):
    """Explicit user-confirmed close; it never implies opening another workspace."""

    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    process_create_time: float = Field(gt=0)
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    project_path: str | None = Field(default=None, min_length=1, max_length=32_768)
    project_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirm_no_save: Literal[True]
    timeout_seconds: float = Field(default=45, gt=0, le=60)


class ReviewedNoSaveCloseResponseV1(ApiModel):
    operation: Literal["xg5000.reviewed_no_save_close"]
    verified: Literal[True]
    source_closed: Literal[True]
    automatic_workspace_open: Literal[False]
    process_terminated: Literal[False]
    reviewed_binding: dict[str, Any]
    close: dict[str, Any]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    next_step: Literal["explicit_user_confirmed_workspace_open_required"]


class CloseIdleXG5000RequestV1(ApiModel):
    """Explicit standalone close of one exact blank instance; no change contract."""

    pid: StrictInt = Field(gt=0)
    hwnd: StrictInt = Field(gt=0)
    process_create_time: float = Field(gt=0, allow_inf_nan=False)
    confirm_idle_close: Literal[True]
    timeout_seconds: float = Field(default=12, ge=0.1, le=15, allow_inf_nan=False)


class CloseIdleXG5000InstanceRequestV1(ExecuteChangeTransactionRequestV1):
    """Recovery-scoped request to close one proven blank XG5000 instance."""

    pid: int = Field(gt=0)
    hwnd: int = Field(gt=0)
    process_create_time: float = Field(gt=0)
    confirm_idle_close: Literal[True]
    timeout_seconds: float = Field(default=12, ge=0.1, le=15)


class CloseIdleXG5000InstanceResponseV1(ApiModel):
    operation: Literal["xg5000.close_idle_instance"]
    verified: Literal[True]
    status: Literal["closed"]
    idle_binding: dict[str, Any]
    close_action: Literal["wm_close"]
    prompt_observed: Literal[False]
    modal_observed: Literal[False]
    no_save_prompt_clicked: Literal[False]
    process_exited: Literal[True]
    process_terminated: Literal[False]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CloseIdleXG5000InstanceAttentionV1(ApiModel):
    operation: Literal["xg5000.close_idle_instance"]
    verified: Literal[False]
    status: Literal["needs_attention"]
    idle_binding: dict[str, Any]
    close_action: Literal["wm_close"] | None
    prompt_observed: bool
    modal_observed: bool
    attention_reason: Literal[
        "owned_modal_before_close",
        "owned_modal_after_close",
        "close_timeout",
    ]
    no_save_prompt_clicked: Literal[False]
    process_exited: Literal[False]
    process_terminated: Literal[False]
    receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attention_outcome(self) -> CloseIdleXG5000InstanceAttentionV1:
        expected = {
            "owned_modal_before_close": (None, True, True),
            "owned_modal_after_close": ("wm_close", True, True),
            "close_timeout": ("wm_close", False, False),
        }[self.attention_reason]
        if (self.close_action, self.prompt_observed, self.modal_observed) != expected:
            raise ValueError("idle-instance attention receipt has an invalid close outcome")
        return self


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
