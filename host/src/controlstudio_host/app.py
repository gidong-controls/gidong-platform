from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .agent_client import AgentSupervisor, SubprocessAgentClient
from .agent_runtime import AgentOutputContractError, ModelCapabilityResult
from .api_models import (
    AgentStatusV1,
    AttachXG5000RequestV1,
    AttachXG5000ResponseV1,
    BootstrapV1,
    BrowserSessionV1,
    ChangeApprovalV1,
    ChangeDecisionRequestV1,
    ChangeDecisionResponseV1,
    ChangeExecutionResponseV1,
    ChangePlanV1,
    ChangeReceiptAcknowledgementV1,
    ChangeReconciliationResponseV1,
    ChangeTargetIdentityV1,
    ChangeTransactionCreatedV1,
    ChangeTransactionV1,
    ConfirmKnowledgeMemoryRequestV1,
    ConnectLSControllerRequestV1,
    ConversationCreateV1,
    ConversationMessageV1,
    ConversationPageV1,
    ConversationSummaryV1,
    ConversationV1,
    CreateChangeTransactionRequestV1,
    CreateKnowledgeMemoryRequestV1,
    CreateNativeProjectRequestV1,
    EngineeringProgramFocusV1,
    EngineeringProgramsV1,
    EngineeringProgramV1,
    EngineeringSystemStatusV1,
    EngineeringTargetBindingV1,
    ExecuteChangeTransactionRequestV1,
    HostStatusV1,
    ImportKnowledgeRequestV1,
    KnowledgeCitationV1,
    KnowledgeImportResultV1,
    KnowledgeSearchHitV1,
    KnowledgeSearchResponseV1,
    KnowledgeSourcePageV1,
    KnowledgeSourceV1,
    KnowledgeSummaryV1,
    LauncherHandoffV1,
    LocalWorkspaceV1,
    LSControllerConnectionStatusV1,
    LSControllerEndpointV1,
    LSControllerObservationV1,
    LSControllerReadV1,
    MCPStatusV1,
    ModelCapabilityFlagsV1,
    ModelCapabilityResultV1,
    ModelSettingsV1,
    NativeProjectCreatedV1,
    NativeProjectOperationV1,
    NativeProjectReconciliationV1,
    OpenLocalWorkspaceRequestV1,
    OpenLocalWorkspaceResponseV1,
    ProductFeatureStatusV1,
    ProjectBriefV1,
    ProjectPlanArtifactV1,
    ProjectPlanReviewRequestV1,
    ProjectPlanReviewResponseV1,
    ProjectPlanReviewV1,
    ReadLSControllerRequestV1,
    ReconcileChangeTransactionRequestV1,
    ReconcileNativeProjectRequestV1,
    ResumeLocalWorkspaceRequestV1,
    RunCancellationV1,
    SaveModelSettingsRequestV1,
    SendMessageRequestV1,
    SendMessageResponseV1,
    ServiceStatusV1,
    WorkspaceSummaryV1,
    XG5000InstancesV1,
    XG5000ProjectFileSelectionV1,
    XG5000ProjectLaunchV1,
)
from .change_execution import (
    ChangeExecutionFailedSafely,
    ChangeExecutionOutcomeUncertain,
    ChangeExecutionPreconditionError,
    ChangeExecutionService,
    ChangeRecoveryTarget,
    MCPChangeExecutionService,
    calculate_plan_digest,
)
from .config import HostConfig
from .control_patterns import iter_builtin_knowledge_documents
from .execution_broker import ExecutionBroker, MCPExecutionBroker
from .knowledge import (
    ControlPatternSeed,
    KnowledgeConflict,
    KnowledgeKind,
    KnowledgeSource,
    KnowledgeStatus,
    KnowledgeStore,
    PageRecord,
)
from .knowledge_ingestion import (
    KnowledgeIngestionError,
    extract_local_knowledge_document,
)
from .mcp_client import MCPGateway, MCPStartupError, MCPToolError
from .model_connection import (
    MODEL_PROTOCOL,
    ModelConnectionRepository,
    ModelSecrets,
    ModelSettingsError,
    SecretStoreError,
    StoredModelConnection,
    WindowsCredentialSecretStore,
    is_external_model_endpoint,
    redact_known_model_secrets,
    validate_custom_headers,
    validate_model_base_url,
    validate_model_name,
    validate_secret_text,
    validate_timeout,
)
from .models import (
    ApprovalDecision,
    BootstrapFeatures,
    BootstrapResponse,
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    ConversationPage,
    HealthResponse,
    MessageRole,
    RunState,
    StoredEvent,
)
from .native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectPreconditionError,
    NativeProjectRecoveryEvidence,
    NativeProjectRepository,
    NativeProjectService,
    NativeProjectSpec,
)
from .store import (
    ChangeStoreConflict,
    ConversationActiveRunConflict,
    ConversationWorkflowConflict,
    HostStore,
    MutationLeaseConflict,
    ProjectPlanReviewConflict,
    TargetOperationLeaseConflict,
)
from .windows_dialogs import (
    NativeFileDialogError,
    NativeFileDialogUnavailable,
    select_knowledge_document_file,
    select_xg5000_project_file,
)

API_VERSION = "1"
SERVICE_NAME = "controlstudio-host"
SERVICE_WORKER_PATHS = {"service-worker.js", "sw.js"}
BROWSER_SESSION_TTL = timedelta(hours=12)
BROWSER_SESSION_REFRESH_GRACE = timedelta(hours=24)
BROWSER_HANDOFF_TTL = timedelta(seconds=30)
LAUNCH_CHALLENGE_PREFIX = b"controlstudio-launcher-v1\0"
BROWSER_CSRF_PREFIX = b"controlstudio-browser-csrf-v1\0"
logger = logging.getLogger(__name__)

_AGENT_TOOL_EVIDENCE: dict[str, tuple[str, str, str, str]] = {
    "xg5000.session_status": (
        "validation",
        "XG5000 세션 상태",
        "현재 Host에 결합된 XG5000 읽기 세션 상태를 확인했습니다.",
        "confirmed",
    ),
    "project.list_programs": (
        "program_location",
        "프로그램 목록",
        "현재 대상 XG5000 프로젝트에서 프로그램 목록을 읽었습니다.",
        "confirmed",
    ),
    "program.focus": (
        "program_location",
        "XG5000 프로그램 위치",
        "대상 프로그램과 XG5000 전면 창을 읽기 전용으로 확인했습니다.",
        "confirmed",
    ),
    "program.read_ld": (
        "artifact",
        "래더 프로그램 원문",
        "현재 대상에서 선택한 LD 프로그램의 네이티브 구조를 읽었습니다.",
        "confirmed",
    ),
    "program.logic_graph": (
        "artifact",
        "래더 논리 그래프",
        "지원되는 LD 구간을 구조 그래프로 분석했습니다.",
        "supported",
    ),
    "program.state_model": (
        "artifact",
        "프로그램 상태 모델",
        "지원되는 LD 구간의 상태와 전이 조건을 분석했습니다.",
        "supported",
    ),
    "device.references": (
        "device_reference",
        "디바이스 참조",
        "현재 프로젝트에서 요청한 디바이스의 직접 참조를 추적했습니다.",
        "supported",
    ),
    "project.behavior_report": (
        "artifact",
        "프로젝트 동작 보고서",
        "현재 확보된 정적 증거 범위에서 프로젝트 동작을 분석했습니다.",
        "supported",
    ),
    "project.snapshot": (
        "artifact",
        "프로젝트 스냅샷",
        "변경을 시작하지 않는 읽기 전용 프로젝트 스냅샷을 확보했습니다.",
        "confirmed",
    ),
    "evidence.get": (
        "artifact",
        "MCP 증거 조회",
        "MCP 안전 코어가 보존한 증거 항목을 조회했습니다.",
        "confirmed",
    ),
    "controller.ls.status": (
        "runtime_observation",
        "LS PLC 연결 상태",
        "현재 허용된 LS PLC 읽기 세션 상태를 관찰했습니다.",
        "confirmed",
    ),
    "controller.ls.read": (
        "runtime_observation",
        "LS PLC 디바이스 관찰",
        "현재 허용된 LS PLC 디바이스 값을 읽기 전용으로 관찰했습니다.",
        "confirmed",
    ),
}

_CHANGE_COVERAGE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "target_identity": frozenset(
        {"xg5000.session_status", "program.read_ld", "project.snapshot"}
    ),
    "value_semantics": frozenset({"program.read_ld"}),
    "direct_impact": frozenset(
        {"device.references", "program.logic_graph", "project.behavior_report"}
    ),
    "indirect_impact": frozenset(
        {
            "device.references",
            "program.logic_graph",
            "program.state_model",
            "project.behavior_report",
        }
    ),
    "modes": frozenset(
        {"program.read_ld", "program.state_model", "project.behavior_report"}
    ),
    "runtime": frozenset({"controller.ls.status", "controller.ls.read"}),
    "external_integrations": frozenset({"project.behavior_report"}),
    "safety": frozenset(
        {"program.read_ld", "program.logic_graph", "program.state_model"}
    ),
    "validation": frozenset({"xg5000.session_status", "project.snapshot"}),
    "rollback": frozenset({"project.snapshot"}),
}
_CHANGE_COVERAGE_MUST_BE_CONFIRMED = frozenset(
    {
        "target_identity",
        "value_semantics",
        "direct_impact",
        "indirect_impact",
        "safety",
        "validation",
        "rollback",
    }
)

_LEGACY_PROJECT_BRIEF_LABELS = (
    ("project_name", "프로젝트 이름:"),
    ("machine_purpose", "설비 목적:"),
    ("sequence", "알고 있는 동작 순서·공정:"),
    ("hardware_and_io", "알고 있는 하드웨어·I/O·통신:"),
    ("safety_and_interlocks", "안전·인터록·운영 제약:"),
    ("operating_modes", "알고 있는 운전·초기화·복구 모드:"),
    ("performance_and_acceptance", "성능·품질·인수 기준:"),
)
_LEGACY_PROJECT_BRIEF_FOOTER = (
    "이 단계에서는 파일이나 XG5000을 변경하지 말고, 확인된 사실·가정·미결 질문을 "
    "구분한 검토 가능한 프로젝트 계획서를 작성해 주세요."
)
_LEGACY_UNKNOWN_VALUE = "아직 모름"


class AgentRunPreconditionError(RuntimeError):
    """A product-authored precondition message safe to display and persist."""


def _agent_tool_evidence_ref(
    *,
    conversation_id: str,
    run_id: str,
    tool_name: str,
    tool_call_id: str | None,
) -> dict[str, Any] | None:
    definition = _AGENT_TOOL_EVIDENCE.get(tool_name)
    if definition is None:
        return None
    kind, title, summary, confidence = definition
    call_identity = tool_call_id or str(uuid.uuid4())
    evidence_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "controlstudio-agent-evidence:"
                f"{conversation_id}:{run_id}:{tool_name}:{call_identity}"
            ),
        )
    )
    return {
        "evidenceId": evidence_id,
        "title": title,
        "kind": kind,
        "toolName": tool_name,
        "summary": summary,
        "confidence": confidence,
    }


def _knowledge_source_v1(value: KnowledgeSource) -> KnowledgeSourceV1:
    return KnowledgeSourceV1(
        source_id=value.source_id,
        kind=value.kind.value,
        title=value.title,
        scope=value.scope,
        vendor=value.vendor,
        product_family=value.product_family,
        version=value.version,
        language=value.language,
        authority=value.authority,
        status=value.status.value,
        sha256=value.sha256,
        locator=value.locator,
        revision=value.revision,
        supersedes=value.supersedes,
        metadata=value.metadata,
        confirmed_by=value.confirmed_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _agent_knowledge_hit_v1(value: Any) -> KnowledgeSearchHitV1:
    """Validate a sidecar result before it becomes a public Host response."""

    if not isinstance(value, dict):
        raise TypeError("Agent knowledge result is invalid")
    value = _utf8_safe_json(value)
    required_text = (
        "knowledge_ref", "chunk_id", "source_id", "content", "title", "kind",
        "scope", "authority",
    )
    if any(not isinstance(value.get(field), str) or not value[field] for field in required_text):
        raise ValueError("Agent knowledge result is missing authoritative citation fields")
    if not value["knowledge_ref"].startswith(f"knowledge://{value['source_id']}"):
        raise ValueError("Agent knowledge result citation is not canonical")
    optional_text = ("vendor", "product_family", "version", "language", "locator", "section")
    if any(value.get(field) is not None and not isinstance(value.get(field), str) for field in optional_text):
        raise TypeError("Agent knowledge result contains invalid metadata")
    page = value.get("page")
    if page is not None and (not isinstance(page, int) or page < 1):
        raise ValueError("Agent knowledge result contains invalid page")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("Agent knowledge result contains invalid metadata")
    return KnowledgeSearchHitV1(
        content=value["content"],
        score=0.0,
        channels=["agent_derived"],
        kind=value["kind"],
        scope=value["scope"],
        vendor=value.get("vendor"),
        product_family=value.get("product_family"),
        version=value.get("version"),
        language=value.get("language"),
        authority=value["authority"],
        status="confirmed",
        citation=KnowledgeCitationV1(
            knowledge_ref=value["knowledge_ref"],
            source_id=value["source_id"],
            chunk_id=value["chunk_id"],
            title=value["title"],
            locator=value.get("locator"),
            page=page,
            section=value.get("section"),
        ),
        metadata=metadata,
    )


def _utf8_safe_json(value: Any) -> Any:
    """Do not let malformed sidecar strings make the Host response invalid."""

    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, list):
        return [_utf8_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _utf8_safe_json(item) for key, item in value.items()}
    return value


def _builtin_control_pattern_seeds() -> tuple[ControlPatternSeed, ...]:
    seeds: list[ControlPatternSeed] = []
    for document in iter_builtin_knowledge_documents():
        metadata = document.get("metadata")
        seeds.append(
            ControlPatternSeed(
                pattern_id=str(metadata["pattern_id"]),
                title=str(document["title"]),
                content=str(document["content"]),
                vendor="generic",
                revision=str(document["revision"]),
                authority="controlstudio-curated-design-guidance",
                metadata=(metadata if isinstance(metadata, dict) else {}),
            )
        )
    return tuple(seeds)


def _planning_prompt_from_brief(brief: ProjectBriefV1) -> str:
    """Build the planning prompt from the server-validated brief authority."""

    def optional(value: str) -> str:
        return value or _LEGACY_UNKNOWN_VALUE

    return "\n".join(
        (
            "다음은 ControlStudio 서버가 검증하고 저장한 신규 PLC 프로젝트 요구사항입니다.",
            "자유 텍스트와 충돌할 경우 아래 구조화된 요구사항만 설계 범위의 권위로 사용하세요.",
            "",
            f"프로젝트 이름: {brief.project_name}",
            f"설비 목적: {brief.machine_purpose}",
            f"알고 있는 동작 순서·공정: {optional(brief.sequence)}",
            f"알고 있는 하드웨어·I/O·통신: {optional(brief.hardware_and_io)}",
            (f"안전·인터록·운영 제약: {optional(brief.safety_and_interlocks)}"),
            (f"알고 있는 운전·초기화·복구 모드: {optional(brief.operating_modes)}"),
            (f"성능·품질·인수 기준: {optional(brief.performance_and_acceptance)}"),
            "",
            _LEGACY_PROJECT_BRIEF_FOOTER,
        )
    )


def _event_frame(event: StoredEvent) -> str:
    payload = event.model_dump(mode="json")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


def _event_frame_v1(event: StoredEvent) -> str:
    payload = {
        "eventId": f"event-{event.sequence}",
        "sequence": event.sequence,
        "occurredAt": event.created_at.isoformat(),
        "conversationId": event.conversation_id,
        "runId": event.run_id,
        "eventType": event.event_type,
        "payloadSchemaVersion": "1",
        "payload": event.payload,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\ndata: {data}\n\n"


def _parse_legacy_project_brief(content: str) -> ProjectBriefV1 | None:
    values: dict[str, str] = {}
    current_field: str | None = None
    current_lines: list[str] = []
    last_label_index = -1

    def finish_current_field() -> None:
        if current_field is not None:
            values[current_field] = "\n".join(current_lines).strip()

    for line in content.splitlines():
        if line == _LEGACY_PROJECT_BRIEF_FOOTER:
            finish_current_field()
            current_field = None
            break

        matched_label = next(
            (
                (index, field, label)
                for index, (field, label) in enumerate(_LEGACY_PROJECT_BRIEF_LABELS)
                if line.startswith(label)
            ),
            None,
        )
        if matched_label is None:
            if current_field is not None:
                current_lines.append(line)
            continue

        label_index, field, label = matched_label
        if label_index <= last_label_index:
            return None
        finish_current_field()
        current_field = field
        current_lines = [line[len(label) :].lstrip()]
        last_label_index = label_index
    else:
        finish_current_field()

    project_name = values.get("project_name", "")
    machine_purpose = values.get("machine_purpose", "")
    if not project_name or not machine_purpose:
        return None

    optional_values = {
        field: "" if values.get(field, "") == _LEGACY_UNKNOWN_VALUE else values.get(field, "")
        for field, _label in _LEGACY_PROJECT_BRIEF_LABELS[2:]
    }
    try:
        return ProjectBriefV1(
            project_name=project_name,
            machine_purpose=machine_purpose,
            **optional_values,
        )
    except ValueError:
        return None


def _legacy_project_brief(detail: ConversationDetail) -> ProjectBriefV1 | None:
    if detail.workflow != "new_project_planning" or detail.project_brief is not None:
        return None

    for message in reversed(detail.messages):
        if message.role is not MessageRole.USER:
            continue
        if not any(
            line.startswith(label)
            for line in message.content.splitlines()
            for _field, label in _LEGACY_PROJECT_BRIEF_LABELS
        ):
            continue
        return _parse_legacy_project_brief(message.content)
    return None


def _conversation_v1(detail: ConversationDetail) -> ConversationV1:
    messages = [
        ConversationMessageV1(
            message_id=item.id,
            role=item.role.value,
            content=item.content,
            created_at=item.created_at,
            run_id=(
                str(item.metadata["run_id"])
                if isinstance(item.metadata.get("run_id"), str)
                else None
            ),
            operation_id=(
                str(item.metadata["operation_id"])
                if isinstance(item.metadata.get("operation_id"), str)
                else None
            ),
            status=("failed" if item.metadata.get("status") == "failed" else "complete"),
            workflow=(
                item.metadata["workflow"]
                if item.metadata.get("workflow") in {"engineering", "new_project_planning"}
                else None
            ),
            plan_status=(
                item.metadata["plan_status"]
                if item.metadata.get("plan_status") in {"needs_clarification", "ready_for_review"}
                else None
            ),
            project_name=(
                str(item.metadata["project_name"])
                if isinstance(item.metadata.get("project_name"), str)
                else None
            ),
            plan_artifact=(
                ProjectPlanArtifactV1.model_validate(item.metadata["plan_artifact"])
                if isinstance(item.metadata.get("plan_artifact"), dict)
                else None
            ),
            plan_digest=(
                str(item.metadata["plan_digest"])
                if isinstance(item.metadata.get("plan_digest"), str)
                else None
            ),
            plan_review=(
                ProjectPlanReviewV1(
                    message_id=item.project_plan_review.message_id,
                    plan_digest=item.project_plan_review.plan_digest,
                    reviewed_section_ids=(item.project_plan_review.reviewed_section_ids),
                    actor_id=item.project_plan_review.actor_id,
                    reviewed_at=item.project_plan_review.reviewed_at,
                )
                if item.project_plan_review is not None
                else None
            ),
        )
        for item in detail.messages
        if item.role in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
    ]
    return ConversationV1(
        conversation_id=detail.id,
        title=detail.title or "새 대화",
        workflow=detail.workflow,
        project_brief=detail.project_brief or _legacy_project_brief(detail),
        engineering_target=_conversation_target_v1(detail),
        messages=messages,
        updated_at=detail.updated_at,
    )


def _conversation_target_v1(
    value: Conversation | ConversationDetail,
) -> EngineeringTargetBindingV1 | None:
    values = (
        value.workspace_id,
        value.revision_id,
        value.engineering_session_id,
    )
    if all(isinstance(item, str) and item for item in values):
        return EngineeringTargetBindingV1(
            workspace_id=value.workspace_id,
            revision_id=value.revision_id,
            engineering_session_id=value.engineering_session_id,
        )
    return None


def _conversation_summary_v1(value: Conversation) -> ConversationSummaryV1:
    return ConversationSummaryV1(
        conversation_id=value.id,
        title=value.title or "새 대화",
        workflow=value.workflow,
        engineering_target=_conversation_target_v1(value),
        updated_at=value.updated_at,
    )


def _model_settings_v1(
    value: StoredModelConnection,
) -> ModelSettingsV1:
    return ModelSettingsV1(
        protocol=MODEL_PROTOCOL,
        base_url=value.base_url,
        model=value.model,
        custom_header_names=sorted(value.secrets.custom_headers),
        timeout_seconds=value.timeout_seconds,
        has_api_key=bool(value.secrets.api_key),
        external_endpoint=is_external_model_endpoint(value.base_url),
        updated_at=value.updated_at,
    )


def _local_workspace_v1(value: Any) -> LocalWorkspaceV1:
    if not isinstance(value, dict):
        raise TypeError("workspace result must be an object")
    fields = {name: value[name] for name in LocalWorkspaceV1.model_fields if name in value}
    return LocalWorkspaceV1.model_validate(fields)


def _workspace_resume_identity(value: LocalWorkspaceV1) -> dict[str, str]:
    """Return the immutable and current identity required for a safe resume."""

    def normalize_path(path: str) -> str:
        return path.replace("/", "\\").rstrip("\\").casefold()

    return {
        "workspace_id": value.workspace_id,
        "revision_id": value.revision_id,
        "source_kind": value.source_kind,
        "vendor": value.vendor,
        "engineering_system": value.engineering_system,
        "original_path": normalize_path(value.source.original_path),
        "captured_path": normalize_path(value.source.captured_path),
        "source_sha256": value.source.sha256.casefold(),
        "original_state": value.source.original_state,
        "original_current_sha256": (value.source.original_current_sha256 or "").casefold(),
        "working_path": normalize_path(value.working_copy.path),
        "working_baseline_sha256": value.working_copy.baseline_sha256.casefold(),
        "working_current_sha256": value.working_copy.current_sha256.casefold(),
    }


def _change_transaction_v1(value: ChangeTransaction) -> ChangeTransactionV1:
    approval = value.approval
    return ChangeTransactionV1(
        transaction_id=value.id,
        operation_id=value.operation_id,
        conversation_id=value.conversation_id,
        run_id=value.run_id,
        state=value.state.value.casefold(),
        target_identity=ChangeTargetIdentityV1.model_validate(value.target.model_dump()),
        mutation_scope=value.mutation_scope,
        risk_level=value.risk_level.value.casefold(),
        intent=value.intent,
        plan=ChangePlanV1.model_validate(value.plan),
        plan_digest=value.plan_digest,
        requested_by_actor_id=value.requested_by_actor_id,
        approval_expires_at=value.approval_expires_at,
        approval=(
            ChangeApprovalV1(
                decision=approval.decision.value.casefold(),
                plan_digest=approval.plan_digest,
                actor_id=approval.actor_id,
                decided_at=approval.decided_at,
            )
            if approval is not None
            else None
        ),
        result=value.result,
        error_code=value.error_code,
        error_detail=value.error_detail,
        created_at=value.created_at,
        updated_at=value.updated_at,
        execution_started_at=value.execution_started_at,
        verification_started_at=value.verification_started_at,
        completed_at=value.completed_at,
        receipt_acknowledged_at=value.receipt_acknowledged_at,
        receipt_acknowledged_by=value.receipt_acknowledged_by,
    )


def _native_project_operation_v1(
    value: NativeProjectOperation,
) -> NativeProjectOperationV1:
    return NativeProjectOperationV1(
        native_project_id=value.id,
        operation_id=value.operation_id,
        state=value.state.casefold(),
        request_digest=value.request_digest,
        actor_id=value.actor_id,
        request=value.request,
        plan_digest=value.plan_digest,
        destination_directory=value.destination_directory,
        result=value.result,
        error_code=value.error_code,
        error_detail=value.error_detail,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
    )


def _request_actor_id(request: Request) -> str:
    bearer = request.headers.get("authorization", "")
    token = bearer[7:] if bearer.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authenticated actor identity is unavailable",
        )
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"browser:{digest[:32]}"


def _ephemeral_model_settings(
    body: SaveModelSettingsRequestV1,
    existing: StoredModelConnection | None,
) -> StoredModelConnection:
    base_url = validate_model_base_url(
        body.base_url,
        allow_insecure_http=body.insecure_http_consent,
    )
    model = validate_model_name(body.model)
    timeout = validate_timeout(body.timeout_seconds)
    if is_external_model_endpoint(base_url) and not body.external_model_consent:
        raise ModelSettingsError(
            "external_model_consent is required before sending data to this endpoint"
        )
    same_endpoint = existing is not None and existing.base_url == base_url
    api_key = (
        existing.secrets.api_key
        if body.api_key is None and same_endpoint and existing is not None
        else body.api_key
    )
    if api_key is not None:
        api_key = validate_secret_text(api_key, "api_key")
    headers = (
        existing.secrets.custom_headers
        if body.custom_headers is None and same_endpoint and existing is not None
        else validate_custom_headers(body.custom_headers)
    )
    return StoredModelConnection(
        protocol=MODEL_PROTOCOL,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout,
        secret_ref="ephemeral-capability-test",
        updated_at=datetime.now(timezone.utc),
        capability_verified_at=None,
        secrets=ModelSecrets(api_key=api_key, custom_headers=headers),
    )


def _required_model_capabilities_ready(result: ModelCapabilityResult) -> bool:
    return result.success and all(
        (
            result.completion,
            result.streaming,
            result.tool_calling,
            result.tool_result_continuation,
            result.structured_output,
        )
    )


def _engineering_session_identity_verified(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    metadata = session.get("metadata")
    return isinstance(metadata, dict) and bool(
        metadata.get("project_identity_verified_against_selected_pid")
    )


def _engineering_session_read_ready(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    metadata = session.get("metadata")
    return isinstance(metadata, dict) and bool(
        metadata.get("project_identity_verified_against_selected_pid")
        or metadata.get("project_identity_read_only_bound_to_selected_pid")
    )


def create_app(
    config: HostConfig | None = None,
    *,
    store: HostStore | None = None,
    knowledge_store: KnowledgeStore | None = None,
    mcp: MCPGateway | None = None,
    model_repository: ModelConnectionRepository | None = None,
    runtime_factory: Callable[[StoredModelConnection, ExecutionBroker], Any] | None = None,
    agent_supervisor: AgentSupervisor | None = None,
    change_executor: ChangeExecutionService | None = None,
) -> FastAPI:
    config = config or HostConfig.from_env()
    store = store or HostStore(config.database_path)
    assert config.knowledge_database_path is not None
    knowledge_store = knowledge_store or KnowledgeStore(config.knowledge_database_path)
    knowledge_store.seed_control_patterns(_builtin_control_pattern_seeds())
    mcp = mcp or MCPGateway()
    execution_broker = MCPExecutionBroker(mcp)
    agent_supervisor = agent_supervisor or AgentSupervisor()
    if model_repository is None:
        try:
            model_repository = ModelConnectionRepository(
                store,
                WindowsCredentialSecretStore(),
            )
        except SecretStoreError:
            model_repository = None
    if runtime_factory is None:

        def runtime_factory(
            settings: StoredModelConnection,
            broker: ExecutionBroker,
        ) -> SubprocessAgentClient:
            return SubprocessAgentClient(settings, broker, agent_supervisor)
    change_executor = change_executor or MCPChangeExecutionService(
        mcp,
        checkpoint_callback=store.record_native_change_checkpoint,
    )
    native_project_repository = NativeProjectRepository(config.database_path)
    native_project_service = NativeProjectService(native_project_repository, mcp)
    host_instance_id = str(uuid.uuid4())
    run_tasks: dict[str, asyncio.Task[None]] = {}
    active_change_executions: set[str] = set()
    run_submission_lock = asyncio.Lock()
    file_dialog_lock = asyncio.Lock()
    browser_sessions: dict[str, datetime] = {}
    browser_handoff_tickets: dict[str, datetime] = {}
    shutdown_event = asyncio.Event()
    active_engineering_session: dict[str, Any] | None = None
    active_workspace: dict[str, Any] | None = store.get_host_state("active_workspace")
    active_ls_controller: dict[str, Any] | None = store.get_host_state("active_ls_controller")
    if active_ls_controller is not None and not config.validation_enable_online_observe:
        # Persisted preview data is historical evidence only.  Do not bind an
        # unverified controller endpoint in a normal product Host.
        active_ls_controller = None
    elif active_ls_controller is not None:
        persisted_endpoint = active_ls_controller.get("endpoint")
        try:
            mcp.bind_controller_endpoint(
                persisted_endpoint,
                observation_current=False,
            )
        except (AttributeError, MCPToolError):
            active_ls_controller = None
            store.set_host_state("active_ls_controller", None)
        else:
            # A previous process observation is historical evidence, not proof
            # that the PLC is reachable in this Host run.
            active_ls_controller = {"endpoint": dict(persisted_endpoint)}
            store.set_host_state("active_ls_controller", active_ls_controller)

    def current_change_target() -> ChangeTargetIdentity:
        workspace = active_workspace
        if not isinstance(workspace, dict):
            raise ChangeExecutionPreconditionError(
                "변경 계획을 만들기 전에 검증된 작업공간을 여세요."
            )
        working_copy = workspace.get("working_copy")
        if not isinstance(working_copy, dict):
            raise ChangeExecutionPreconditionError("작업공간 working copy identity가 없습니다.")
        source = workspace.get("source")
        if not isinstance(source, dict):
            raise ChangeExecutionPreconditionError("작업공간 source identity가 없습니다.")
        workspace_id = str(workspace.get("workspace_id") or "")
        revision_id = str(workspace.get("revision_id") or "")
        project_path = str(working_copy.get("path") or "")
        artifact_hash = str(working_copy.get("current_sha256") or "")
        source_artifact_hash = str(source.get("sha256") or "")
        if not all(
            (
                workspace_id,
                revision_id,
                project_path,
                artifact_hash,
                source_artifact_hash,
            )
        ):
            raise ChangeExecutionPreconditionError(
                "작업공간 identity가 변경 계약을 만들기에 불완전합니다."
            )

        session = (
            active_engineering_session
            if _engineering_session_identity_verified(active_engineering_session)
            else None
        )
        if session is not None and (
            str(session.get("project_path") or "").casefold() != project_path.casefold()
            or str(session.get("project_hash") or "").casefold() != artifact_hash.casefold()
        ):
            session = None
        return ChangeTargetIdentity(
            workspace_id=workspace_id,
            revision_id=revision_id,
            artifact_hash=artifact_hash,
            source_artifact_hash=source_artifact_hash,
            project_path=project_path,
            vendor=str(workspace.get("vendor") or "ls"),
            engineering_system=str(workspace.get("engineering_system") or "XG5000"),
            engineering_session_id=(str(session["session_id"]) if session is not None else None),
            engineering_process_id=(int(session["pid"]) if session is not None else None),
            engineering_window_handle=(int(session["hwnd"]) if session is not None else None),
        )

    def offline_mutation_ready() -> bool:
        if mcp.status.status != "ready":
            return False
        try:
            target = current_change_target()
        except ChangeExecutionPreconditionError:
            return False
        return bool(
            target.engineering_session_id
            and target.engineering_process_id is not None
            and target.engineering_window_handle is not None
        )

    def current_engineering_conversation_target() -> tuple[str, str, str] | None:
        """Return the exact server-owned target a conversation may discuss."""

        session = active_engineering_session
        if not _engineering_session_read_ready(session):
            return None
        assert isinstance(session, dict)
        session_id = str(session.get("session_id") or "")
        project_path = str(session.get("project_path") or "")
        project_hash = str(session.get("project_hash") or "")
        if not all((session_id, project_path, project_hash)):
            return None

        workspace = active_workspace
        if isinstance(workspace, dict):
            working_copy = workspace.get("working_copy")
            if isinstance(working_copy, dict):
                workspace_id = str(workspace.get("workspace_id") or "")
                revision_id = str(workspace.get("revision_id") or "")
                working_path = str(working_copy.get("path") or "")
                working_hash = str(working_copy.get("current_sha256") or "")
                if (
                    workspace_id
                    and revision_id
                    and working_path.casefold() == project_path.casefold()
                    and working_hash.casefold() == project_hash.casefold()
                ):
                    return workspace_id, revision_id, session_id

        # A directly attached XG5000 session has no durable workspace revision.
        # Mirror the revision presented in Host status so the client can make
        # the same fail-closed comparison without receiving project paths.
        metadata = session.get("metadata")
        version = str(metadata.get("version") or "") if isinstance(metadata, dict) else ""
        return session_id, version or project_hash, session_id

    def engineering_conversation_matches_current_target(
        detail: Conversation | ConversationDetail,
    ) -> bool:
        current = current_engineering_conversation_target()
        stored_values = (
            detail.workspace_id,
            detail.revision_id,
            detail.engineering_session_id,
        )
        stored = (
            (
                str(stored_values[0]),
                str(stored_values[1]),
                str(stored_values[2]),
            )
            if all(isinstance(item, str) and item for item in stored_values)
            else None
        )
        return current == stored

    def refresh_terminal_change_identity(
        transaction: ChangeTransaction,
        *,
        retain_engineering_session: bool,
    ) -> bool:
        """Refresh Host identity from the saved working copy after a terminal change."""

        nonlocal active_engineering_session, active_workspace
        if transaction.state not in {
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
        }:
            return False
        workspace = active_workspace
        working_copy = (
            workspace.get("working_copy")
            if isinstance(workspace, dict) and isinstance(workspace.get("working_copy"), dict)
            else None
        )
        if (
            workspace is None
            or working_copy is None
            or workspace.get("workspace_id") != transaction.target.workspace_id
        ):
            active_engineering_session = None
            execution_broker.clear_agent_session()
            return False
        try:
            expected_path = Path(transaction.target.project_path).resolve()
            active_path = Path(str(working_copy.get("path") or "")).resolve()
            if (
                os.path.normcase(str(active_path)) != os.path.normcase(str(expected_path))
                or not active_path.is_file()
            ):
                raise OSError("terminal change working copy is unavailable")
            digest_builder = hashlib.sha256()
            with active_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(chunk)
            digest = digest_builder.hexdigest()
        except OSError:
            active_engineering_session = None
            execution_broker.clear_agent_session()
            return False

        refreshed_workspace = dict(workspace)
        refreshed_working_copy = dict(working_copy)
        refreshed_working_copy["current_sha256"] = digest
        refreshed_working_copy["state"] = (
            "clean"
            if digest == str(refreshed_working_copy.get("baseline_sha256") or "").casefold()
            else "modified"
        )
        refreshed_workspace["working_copy"] = refreshed_working_copy
        active_workspace = refreshed_workspace
        store.set_host_state("active_workspace", active_workspace)

        session = active_engineering_session
        session_matches = (
            retain_engineering_session
            and isinstance(session, dict)
            and session.get("session_id") == transaction.target.engineering_session_id
            and session.get("pid") == transaction.target.engineering_process_id
            and session.get("hwnd") == transaction.target.engineering_window_handle
            and os.path.normcase(str(Path(str(session.get("project_path") or "")).resolve()))
            == os.path.normcase(str(expected_path))
        )
        if session_matches:
            refreshed_session = dict(session)
            refreshed_session["project_hash"] = digest
            active_engineering_session = refreshed_session
            execution_broker.bind_agent_session(refreshed_session)
        else:
            active_engineering_session = None
            execution_broker.clear_agent_session()
        return True

    async def target_switch_lease(request: Request) -> AsyncIterator[None]:
        """Serialize workspace/attach requests for their complete route lifetime."""

        holder_token = str(uuid.uuid4())
        try:
            raw_body = await request.json()
        except (json.JSONDecodeError, RuntimeError):
            raw_body = {}
        if request.url.path.endswith("/workspaces/open-local"):
            owner_kind = "workspace_open"
        elif request.url.path.endswith("/resume"):
            owner_kind = "workspace_resume"
        else:
            owner_kind = "engineering_attach"
        request_identity = {
            "route": request.url.path,
            "request": raw_body if isinstance(raw_body, dict) else {},
        }
        operation_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                json.dumps(
                    request_identity,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
        try:
            store.acquire_target_operation_lease(
                operation_id=operation_id,
                owner_kind=owner_kind,
                holder_token=holder_token,
                target_identity=request_identity,
            )
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            yield
        finally:
            store.release_target_operation_lease(
                operation_id=operation_id,
                holder_token=holder_token,
                reason=f"{owner_kind}_request_finished",
            )

    def publish_change_event(
        event_type: str,
        transaction: ChangeTransaction,
    ) -> None:
        serialized = _change_transaction_v1(transaction).model_dump(
            mode="json",
            by_alias=True,
        )
        store.append_event(
            event_type=event_type,
            conversation_id=transaction.conversation_id,
            run_id=transaction.run_id,
            payload={
                "transactionId": transaction.id,
                "transaction": serialized,
            },
        )

    def validate_plan_evidence_binding(
        plan: ChangePlanV1,
        *,
        conversation_id: str | None,
        run_id: str | None,
    ) -> None:
        if conversation_id is None or run_id is None:
            raise ChangeExecutionPreconditionError(
                "an executable change plan must be bound to one ControlStudio "
                "conversation and agent run"
            )
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for event in store.list_run_evidence(
            conversation_id=conversation_id,
            run_id=run_id,
        ):
            evidence_id = event.payload.get("evidenceId")
            if isinstance(evidence_id, str) and evidence_id:
                evidence_by_id[evidence_id] = event.payload
        referenced_ids = {
            evidence_ref
            for item in plan.coverage
            if item.status == "confirmed"
            for evidence_ref in item.evidence_refs
        }
        missing = sorted(referenced_ids.difference(evidence_by_id))
        if missing:
            raise ChangeExecutionPreconditionError(
                "confirmed change-plan evidenceRefs do not resolve to "
                "evidence.added events from the exact conversation and run: " + ", ".join(missing)
            )
        for item in plan.coverage:
            if (
                item.area in _CHANGE_COVERAGE_MUST_BE_CONFIRMED
                and item.status != "confirmed"
            ):
                raise ChangeExecutionPreconditionError(
                    f"change-plan coverage {item.area} must be confirmed with "
                    "server-authored evidence"
                )
            if item.status != "confirmed":
                continue
            allowed_tools = _CHANGE_COVERAGE_ALLOWED_TOOLS[item.area]
            observed_tools = {
                str(evidence_by_id[evidence_ref].get("toolName") or "")
                for evidence_ref in item.evidence_refs
                if evidence_ref in evidence_by_id
            }
            if not observed_tools.intersection(allowed_tools):
                raise ChangeExecutionPreconditionError(
                    f"change-plan coverage {item.area} has no applicable "
                    "server-authored evidence; received tools: "
                    + ", ".join(sorted(tool for tool in observed_tools if tool))
                )

    def validate_executable_plan_data(
        plan_data: dict[str, Any],
        *,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> ChangePlanV1:
        try:
            plan = ChangePlanV1.model_validate(plan_data)
            plan.validate_executable_contract()
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                f"change plan does not satisfy the executable safety contract: {exc}"
            ) from exc
        validate_plan_evidence_binding(
            plan,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        return plan

    def create_server_change_transaction(
        *,
        operation_id: str,
        actor_id: str,
        intent: str,
        risk_level: str,
        plan: ChangePlanV1,
        approval_ttl_seconds: int,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[ChangeTransaction, bool]:
        target = current_change_target()
        if (
            not target.engineering_session_id
            or target.engineering_process_id is None
            or target.engineering_window_handle is None
        ):
            raise ChangeExecutionPreconditionError(
                "a verified engineering session is required before creating "
                "an executable offline change transaction"
            )
        try:
            plan.validate_executable_contract()
        except ValueError as exc:
            raise ChangeExecutionPreconditionError(
                f"change plan does not satisfy the executable safety contract: {exc}"
            ) from exc
        validate_plan_evidence_binding(
            plan,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        risk = ChangeRiskLevel(risk_level.upper())
        plan_data = plan.model_dump(mode="json")
        plan_digest = calculate_plan_digest(
            target=target,
            risk_level=risk,
            intent=intent,
            plan=plan_data,
        )
        transaction, created = store.create_change_transaction(
            operation_id=operation_id,
            conversation_id=conversation_id,
            run_id=run_id,
            target=target,
            mutation_scope=f"workspace:{target.workspace_id}",
            risk_level=risk,
            intent=intent,
            plan=plan_data,
            plan_digest=plan_digest,
            requested_by_actor_id=actor_id,
            approval_expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=approval_ttl_seconds)
            ),
        )
        if created:
            publish_change_event("approval.requested", transaction)
        return transaction, created

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.mark_previous_instance_target_leases_stale()
        store.reconcile_interrupted_runs()
        store.reconcile_interrupted_change_transactions()
        native_project_repository.reconcile_interrupted()
        store.release_terminal_native_target_operation_leases()

        async def start_mcp() -> None:
            try:
                await mcp.start()
            except MCPStartupError:
                # The Host and settings UI remain usable. The separate
                # integration status is unavailable and calls fail closed.
                pass

        mcp_start_task = asyncio.create_task(
            start_mcp(),
            name="controlstudio-mcp-startup",
        )
        try:
            yield
        finally:
            for task in run_tasks.values():
                task.cancel()
            if run_tasks:
                await asyncio.gather(*run_tasks.values(), return_exceptions=True)
            if not mcp_start_task.done():
                mcp_start_task.cancel()
            await asyncio.gather(mcp_start_task, return_exceptions=True)
            await agent_supervisor.stop()
            await mcp.stop()

    app = FastAPI(
        title="ControlStudio Host",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.config = config
    app.state.store = store
    app.state.knowledge_store = knowledge_store
    app.state.mcp = mcp
    app.state.agent_supervisor = agent_supervisor
    app.state.model_repository = model_repository
    app.state.run_tasks = run_tasks
    app.state.change_executor = change_executor
    app.state.native_project_repository = native_project_repository
    app.state.native_project_service = native_project_service
    app.state.active_change_executions = active_change_executions
    app.state.host_instance_id = host_instance_id
    app.state.request_shutdown = None
    app.state.shutdown_event = shutdown_event
    app.state.browser_handoff_tickets = browser_handoff_tickets
    app.state.browser_sessions = browser_sessions
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))

    @app.middleware("http")
    async def local_origin_and_security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != config.canonical_origin:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "cross-origin requests are not allowed"},
            )

        path = request.url.path
        protected_api = path.startswith("/api/") and path not in {
            "/api/health",
            "/api/launcher/challenge",
            "/api/launcher/handoff",
            "/api/session",
            "/api/session/refresh",
        }
        if protected_api:
            bearer = request.headers.get("authorization", "")
            bearer_token = bearer[7:] if bearer.startswith("Bearer ") else ""
            expires_at = browser_sessions.get(bearer_token)
            authenticated = expires_at is not None and expires_at > datetime.now(timezone.utc)
            if expires_at is not None and not authenticated:
                browser_sessions.pop(bearer_token, None)
            if not authenticated:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "a valid ControlStudio launch session is required"},
                    headers={"Cache-Control": "no-store"},
                )

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; worker-src 'none'",
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def browser_csrf_token(request: Request) -> str:
        bearer = request.headers.get("authorization", "")
        session_token = bearer[7:] if bearer.startswith("Bearer ") else ""
        return hmac.new(
            config.launch_token.encode("utf-8"),
            BROWSER_CSRF_PREFIX + session_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def require_browser_csrf(request: Request) -> None:
        supplied = request.headers.get("x-controlstudio-csrf", "")
        expected = browser_csrf_token(request)
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="a valid ControlStudio CSRF token is required",
            )

    @app.get("/api/launcher/challenge")
    async def launcher_challenge(
        nonce: str = Query(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    ) -> dict[str, str]:
        proof = hmac.new(
            config.launch_token.encode("utf-8"),
            LAUNCH_CHALLENGE_PREFIX + nonce.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "algorithm": "HMAC-SHA256",
            "nonce": nonce,
            "proof": proof,
        }

    @app.post(
        "/api/launcher/handoff",
        response_model=LauncherHandoffV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def issue_browser_handoff(request: Request) -> LauncherHandoffV1:
        bearer = request.headers.get("authorization", "")
        supplied_token = bearer[7:] if bearer.startswith("Bearer ") else ""
        if not supplied_token or not hmac.compare_digest(
            supplied_token,
            config.launch_token,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid ControlStudio launcher credential",
            )
        now = datetime.now(timezone.utc)
        for ticket, expires_at in tuple(browser_handoff_tickets.items()):
            if expires_at <= now:
                browser_handoff_tickets.pop(ticket, None)
        handoff_ticket = secrets.token_urlsafe(48)
        expires_at = now + BROWSER_HANDOFF_TTL
        browser_handoff_tickets[handoff_ticket] = expires_at
        return LauncherHandoffV1(
            handoff_ticket=handoff_ticket,
            expires_at=expires_at,
        )

    @app.post("/api/session", response_model=BrowserSessionV1)
    async def establish_browser_session(request: Request) -> BrowserSessionV1:
        bearer = request.headers.get("authorization", "")
        supplied_ticket = bearer[7:] if bearer.startswith("Bearer ") else ""
        ticket_expires_at = browser_handoff_tickets.pop(supplied_ticket, None)
        now = datetime.now(timezone.utc)
        if ticket_expires_at is None or ticket_expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid, expired, or already consumed browser handoff ticket",
            )
        for token, expires_at in tuple(browser_sessions.items()):
            if expires_at <= now:
                browser_sessions.pop(token, None)
        session_token = secrets.token_urlsafe(48)
        expires_at = now + BROWSER_SESSION_TTL
        browser_sessions[session_token] = expires_at
        return BrowserSessionV1(
            session_token=session_token,
            expires_at=expires_at,
        )

    @app.post("/api/session/refresh", response_model=BrowserSessionV1)
    async def refresh_browser_session(request: Request) -> BrowserSessionV1:
        bearer = request.headers.get("authorization", "")
        supplied_token = bearer[7:] if bearer.startswith("Bearer ") else ""
        current_expiry = browser_sessions.get(supplied_token)
        now = datetime.now(timezone.utc)
        if (
            not supplied_token
            or current_expiry is None
            or current_expiry + BROWSER_SESSION_REFRESH_GRACE <= now
        ):
            if supplied_token:
                browser_sessions.pop(supplied_token, None)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or irrecoverably expired ControlStudio browser session",
            )
        browser_sessions.pop(supplied_token, None)
        session_token = secrets.token_urlsafe(48)
        expires_at = now + BROWSER_SESSION_TTL
        browser_sessions[session_token] = expires_at
        return BrowserSessionV1(
            session_token=session_token,
            expires_at=expires_at,
        )

    def current_status_v1() -> HostStatusV1:
        mcp_status = mcp.status
        model_connection: StoredModelConnection | None = None
        model_error: str | None = None
        if model_repository is not None:
            try:
                model_connection = model_repository.get()
            except SecretStoreError:
                model_error = "저장된 모델 자격 증명을 읽을 수 없습니다."
        else:
            model_error = "Windows 보안 자격 증명 저장소를 사용할 수 없습니다."

        if model_error:
            agent_state = "unavailable"
            agent_detail = model_error
        elif model_connection is None:
            agent_state = "unavailable"
            agent_detail = "OpenAI 호환 모델 연결을 설정해 주세요."
        elif model_connection.capability_verified_at is None:
            agent_state = "unavailable"
            agent_detail = "저장된 모델 연결의 에이전트 기능 검사가 필요합니다."
        else:
            agent_state = "ready"
            agent_detail = (
                "신규 프로젝트 계획과 엔지니어링 분석이 준비되었습니다."
                if mcp_status.status == "ready"
                else (
                    "신규 프로젝트 계획이 준비되었습니다. 엔지니어링 분석은 "
                    "MCP 안전 코어가 준비된 뒤 사용할 수 있습니다."
                )
            )

        mcp_state = (
            "ready"
            if mcp_status.status == "ready"
            else ("starting" if mcp_status.status == "starting" else "unavailable")
        )
        active_session = active_engineering_session if mcp_status.status == "ready" else None
        active_project_path = (
            str(active_session.get("project_path"))
            if active_session and active_session.get("project_path")
            else (
                str(active_workspace["working_copy"]["path"])
                if active_workspace
                and isinstance(active_workspace.get("working_copy"), dict)
                and active_workspace["working_copy"].get("path")
                else None
            )
        )
        active_metadata = (
            active_session.get("metadata")
            if active_session and isinstance(active_session.get("metadata"), dict)
            else {}
        )
        selected_pid_identity_verified = _engineering_session_identity_verified(active_session)
        selected_pid_read_ready = _engineering_session_read_ready(active_session)
        identity_assurance = str(active_metadata.get("project_identity_assurance") or "unverified")
        if identity_assurance not in {
            "strong_readback",
            "controlled_launch_read_only",
            "unverified",
        }:
            identity_assurance = "unverified"
        controller_observation: LSControllerObservationV1 | None = None
        controller_endpoint = None
        controller_timeout: float | None = None
        if isinstance(active_ls_controller, dict):
            endpoint_state = active_ls_controller.get("endpoint")
            if isinstance(endpoint_state, dict):
                controller_timeout = float(endpoint_state.get("timeout_seconds", 2.0))
                try:
                    controller_endpoint = LSControllerEndpointV1.model_validate(
                        {
                            "host": endpoint_state.get("host"),
                            "port": endpoint_state.get("port", 2004),
                            "transport": "tcp",
                            "protocol": "XGT_FENET",
                        }
                    )
                except ValueError:
                    controller_endpoint = None
            observation_state = active_ls_controller.get("observation")
            if isinstance(observation_state, dict):
                try:
                    controller_observation = LSControllerObservationV1.model_validate(
                        observation_state
                    )
                except ValueError:
                    controller_observation = None
            if controller_observation is not None:
                controller_endpoint = controller_observation.endpoint
            if not bool(getattr(mcp, "controller_observation_current", False)):
                controller_observation = None
        active_operation: dict[str, Any] | None = None
        for transaction_id in active_change_executions:
            transaction = store.get_change_transaction(transaction_id)
            if transaction is not None:
                active_operation = {
                    "transactionId": transaction.id,
                    "state": transaction.state.value.casefold(),
                    "kind": "change",
                }
                break
        native_operation = native_project_repository.blocking_operation()
        if active_operation is None and native_operation is not None:
            active_operation = {
                "nativeProjectId": native_operation.id,
                "operationId": native_operation.operation_id,
                "state": native_operation.state.casefold(),
                "kind": "native_project_create",
            }
        for run_id, task in run_tasks.items():
            if active_operation is not None:
                break
            if task.done():
                continue
            run = store.get_run(run_id)
            if run is not None:
                active_operation = {
                    "runId": run.id,
                    "conversationId": run.conversation_id,
                    "state": run.state.value.casefold(),
                }
                break
        return HostStatusV1(
            host=ServiceStatusV1(
                state="ready" if store.ping() else "degraded",
                detail="로컬 사용자 세션 Host",
                version=__version__,
            ),
            agent=AgentStatusV1(
                state=agent_state,
                detail=agent_detail,
                model_display_name=(
                    model_connection.model if model_connection is not None else None
                ),
                planning_ready=agent_state == "ready",
                engineering_ready=(agent_state == "ready" and mcp_status.status == "ready"),
            ),
            mcp=MCPStatusV1(
                state=mcp_state,
                detail=mcp_status.error,
                version=mcp_status.server_version,
                server_id=mcp_status.server_name,
                tool_count=mcp_status.tool_count,
                agent_tool_count=mcp_status.agent_tool_count,
            ),
            engineering_system=EngineeringSystemStatusV1(
                state=(
                    "ready"
                    if selected_pid_read_ready
                    else ("degraded" if active_session or active_workspace else "unavailable")
                ),
                detail=(
                    "선택한 PID와 프로젝트 정체성이 검증되었습니다."
                    if selected_pid_identity_verified
                    else (
                        "ControlStudio가 연 정확한 XG5000 PID에 읽기 전용 "
                        "프로젝트 세션이 연결되었습니다. 변경 권한은 닫혀 있습니다."
                        if selected_pid_read_ready
                        else (
                            "XG5000 창은 연결됐지만 프로젝트가 선택한 PID에서 "
                            "열렸다는 증명은 아직 부분적입니다."
                            if active_session
                            else (
                                "원본을 보존한 working copy가 준비됐으며 "
                                "XG5000 분석 세션 연결을 기다립니다."
                                if active_workspace
                                else "XG5000 실행 인스턴스를 선택하기 전입니다."
                            )
                        )
                    )
                ),
                process_id=(
                    int(active_session["pid"])
                    if active_session and active_session.get("pid")
                    else None
                ),
                session_id=(
                    str(active_session["session_id"])
                    if active_session and active_session.get("session_id")
                    else None
                ),
                session_verified=selected_pid_identity_verified,
                read_ready=selected_pid_read_ready,
                identity_assurance=identity_assurance,
            ),
            controller=LSControllerConnectionStatusV1(
                state=(
                    "ready"
                    if controller_observation is not None and mcp_status.status == "ready"
                    else ("degraded" if active_ls_controller is not None else "unavailable")
                ),
                detail=(
                    "LS PLC 읽기 전용 온라인 관찰이 준비되었습니다."
                    if controller_observation is not None and mcp_status.status == "ready"
                    else (
                        "선택한 LS PLC endpoint를 다시 확인해야 합니다."
                        if active_ls_controller is not None
                        else "온라인 LS PLC endpoint가 선택되지 않았습니다."
                    )
                ),
                endpoint=controller_endpoint,
                timeout_seconds=controller_timeout,
                observed_at=(
                    controller_observation.observed_at
                    if controller_observation is not None
                    else None
                ),
                provisional_identity=(
                    controller_observation.provisional_identity
                    if controller_observation is not None
                    else None
                ),
            ),
            workspace=(
                WorkspaceSummaryV1(
                    workspace_id=(
                        str(active_workspace["workspace_id"])
                        if active_workspace
                        else str(active_session["session_id"])
                    ),
                    display_name=(
                        Path(active_project_path).name if active_project_path else "XG5000 프로젝트"
                    ),
                    source="local",
                    revision=(
                        str(active_workspace["revision_id"])
                        if active_workspace and active_workspace.get("revision_id")
                        else (
                            str(active_metadata["version"])
                            if active_metadata.get("version")
                            else None
                        )
                    ),
                    artifact_hash=(
                        str(active_workspace["working_copy"]["current_sha256"])
                        if active_workspace
                        and isinstance(active_workspace.get("working_copy"), dict)
                        and active_workspace["working_copy"].get("current_sha256")
                        else (
                            str(active_session["project_hash"])
                            if active_session and active_session.get("project_hash")
                            else None
                        )
                    ),
                )
                if active_session or active_workspace
                else None
            ),
            capabilities=[
                "read",
                "analyze",
                "workspace_write",
                "open_local_project",
                "openai_compatible_model",
                "event_replay",
                "change_transactions",
                *(["offline_mutate"] if offline_mutation_ready() else []),
            ],
            feature_readiness=[
                ProductFeatureStatusV1(
                    feature_id="new_project_planning",
                    label="새 프로젝트 요구사항·계획",
                    maturity="supported",
                    verification="real_runtime",
                    current_ready=agent_state == "ready",
                    detail=(
                        "설비 요구사항을 구조화하고 검토 가능한 계획을 만드는 "
                        "Pydantic AI 경로입니다. 네이티브 로직 생성과는 별도입니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="local_project_analysis",
                    label="기존 XG5000 프로젝트 읽기·분석",
                    maturity="supported",
                    verification="real_xg5000",
                    current_ready=mcp_status.status == "ready",
                    detail=(
                        "대동 프로젝트에서 원본 보존 작업 복사본, 실제 XG5000 열기, "
                        "프로그램 목록 MCP 호출까지 검증했습니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="program_focus",
                    label="XG5000 프로그램 위치 열기",
                    maturity="supported",
                    verification="real_xg5000",
                    current_ready=selected_pid_read_ready,
                    detail=(
                        "정확한 PID/HWND·프로그램·프로젝트 hash 불변 계약으로 실제 "
                        "XG5000의 대상 프로그램 전면화를 검증했습니다. 현재 읽기 전용 "
                        "엔지니어링 세션이 준비된 경우에만 활성화합니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="native_project_create",
                    label="새 XG5000 네이티브 프로젝트 생성",
                    maturity="preview",
                    verification="fake_only",
                    current_ready=False,
                    detail=(
                        "계약과 fake backend만 검증됐습니다. 실제 XG5000 생성·로직·"
                        "검사·재열기·시뮬레이션이 닫히기 전에는 일반 실행하지 않습니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="online_observe",
                    label="LS PLC 온라인 읽기 전용 관찰",
                    maturity="preview",
                    verification="fake_only",
                    current_ready=False,
                    detail=(
                        "FEnet 상태·지정 디바이스 읽기 계약은 구현됐지만 실제 PLC에서 "
                        "검증하지 않았습니다. 일반 사용자 실행과 쓰기·프로젝트 복원은 "
                        "차단됩니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="offline_mutate",
                    label="승인 기반 오프라인 변경",
                    maturity="preview",
                    verification="isolated_harness",
                    current_ready=offline_mutation_ready(),
                    detail=(
                        "동일 길이 operand 한 개와 검사·저장·복구는 격리 하네스에서 "
                        "검증했습니다. 현재 대상 identity가 강하지 않으면 실행하지 않습니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="controller_project_restore",
                    label="PLC에서 새 작업공간으로 프로젝트 읽기",
                    maturity="planned",
                    verification="not_verified",
                    current_ready=False,
                    detail="프로그램·파라미터 복원과 누락 메타데이터 표시를 아직 구현하지 않았습니다.",
                ),
                ProductFeatureStatusV1(
                    feature_id="online_modify",
                    label="PLC 쓰기·런중 수정",
                    maturity="planned",
                    verification="not_verified",
                    current_ready=False,
                    detail="실제 테스트 PLC와 별도 현장 승인 전까지 구조적으로 차단합니다.",
                ),
                ProductFeatureStatusV1(
                    feature_id="multi_vendor",
                    label="Mitsubishi·Rockwell 어댑터",
                    maturity="planned",
                    verification="not_verified",
                    current_ready=False,
                    detail="현재 제품 어댑터는 LS XG5000만 구현돼 있습니다.",
                ),
            ],
            active_operation=active_operation,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health(response: Response) -> HealthResponse:
        database_ready = store.ping() and knowledge_store.ping()
        if not database_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            build_id=__version__,
            status="ready" if database_ready else "degraded",
            database="ready" if database_ready else "unavailable",
            static_ui="ready" if (config.static_dir / "index.html").is_file() else "unavailable",
        )

    @app.get("/api/v1/status", response_model=HostStatusV1)
    async def status_v1() -> HostStatusV1:
        return current_status_v1()

    def selected_ls_controller_endpoint() -> dict[str, Any]:
        if not isinstance(active_ls_controller, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="먼저 LS PLC의 읽기 전용 endpoint를 연결해 주세요.",
            )
        endpoint = active_ls_controller.get("endpoint")
        if not isinstance(endpoint, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="저장된 LS PLC endpoint가 유효하지 않습니다.",
            )
        return dict(endpoint)

    def controller_gateway_error(exc: MCPToolError) -> HTTPException:
        detail = str(exc)
        unavailable = "MCP is not ready" in detail or "disconnected" in detail
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE if unavailable else status.HTTP_502_BAD_GATEWAY
            ),
            detail=detail,
        )

    def validate_controller_endpoint(
        endpoint: Any,
        expected: dict[str, Any],
    ) -> None:
        if (
            endpoint.host != expected["host"]
            or endpoint.port != expected["port"]
            or endpoint.transport != "tcp"
            or endpoint.protocol != "XGT_FENET"
        ):
            raise ValueError("MCP returned an observation for a different endpoint")

    async def observe_ls_controller(
        endpoint: dict[str, Any],
    ) -> LSControllerObservationV1:
        try:
            raw = await mcp.call_controller_tool(
                "controller.ls.status",
                {},
                endpoint=endpoint,
            )
        except MCPToolError as exc:
            raise controller_gateway_error(exc) from exc
        try:
            observation = LSControllerObservationV1.model_validate(raw)
            validate_controller_endpoint(observation.endpoint, endpoint)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid read-only LS PLC status observation",
            ) from exc
        return observation

    @app.post(
        "/api/v1/controllers/ls/connect",
        response_model=LSControllerObservationV1,
    )
    async def connect_ls_controller_v1(
        body: ConnectLSControllerRequestV1,
    ) -> LSControllerObservationV1:
        nonlocal active_ls_controller
        if not config.validation_enable_online_observe:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "online observation is unavailable in this product build; "
                    "the current implementation has fake-backend evidence only"
                ),
            )
        endpoint = {
            "host": body.host,
            "port": body.port,
            "timeout_seconds": body.timeout_seconds,
        }
        observation = await observe_ls_controller(endpoint)
        active_ls_controller = {
            "endpoint": endpoint,
            "observation": observation.model_dump(mode="json", by_alias=True),
        }
        mcp.bind_controller_endpoint(endpoint, observation_current=True)
        store.set_host_state("active_ls_controller", active_ls_controller)
        store.append_event(
            event_type="controller.connected",
            payload={
                "vendor": "ls",
                "protocol": "XGT_FENET",
                "endpoint": observation.endpoint.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "observedAt": observation.observed_at.isoformat(),
                "readOnly": True,
                "strongEnoughForWrite": False,
            },
        )
        return observation

    @app.get(
        "/api/v1/controllers/ls/status",
        response_model=LSControllerObservationV1,
    )
    async def ls_controller_status_v1() -> LSControllerObservationV1:
        nonlocal active_ls_controller
        if not config.validation_enable_online_observe:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "online observation is unavailable in this product build; "
                    "the current implementation has fake-backend evidence only"
                ),
            )
        endpoint = selected_ls_controller_endpoint()
        try:
            observation = await observe_ls_controller(endpoint)
        except HTTPException:
            active_ls_controller = {"endpoint": endpoint}
            mcp.bind_controller_endpoint(endpoint, observation_current=False)
            store.set_host_state("active_ls_controller", active_ls_controller)
            raise
        active_ls_controller = {
            "endpoint": endpoint,
            "observation": observation.model_dump(mode="json", by_alias=True),
        }
        mcp.bind_controller_endpoint(endpoint, observation_current=True)
        store.set_host_state("active_ls_controller", active_ls_controller)
        store.append_event(
            event_type="controller.observed",
            payload={
                "vendor": "ls",
                "observedAt": observation.observed_at.isoformat(),
                "mode": observation.status.mode,
                "readOnly": True,
                "strongEnoughForWrite": False,
            },
        )
        return observation

    @app.post(
        "/api/v1/controllers/ls/read",
        response_model=LSControllerReadV1,
    )
    async def read_ls_controller_v1(
        body: ReadLSControllerRequestV1,
    ) -> LSControllerReadV1:
        if not config.validation_enable_online_observe:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "online observation is unavailable in this product build; "
                    "the current implementation has fake-backend evidence only"
                ),
            )
        endpoint = selected_ls_controller_endpoint()
        try:
            raw = await mcp.call_controller_tool(
                "controller.ls.read",
                {
                    "data_type": body.data_type,
                    "variables": body.variables,
                },
            )
        except MCPToolError as exc:
            raise controller_gateway_error(exc) from exc
        try:
            result = LSControllerReadV1.model_validate(raw)
            validate_controller_endpoint(result.endpoint, endpoint)
            if result.data_type != body.data_type:
                raise ValueError("MCP returned a different data type")
            observed_variables = [item.variable.casefold() for item in result.values]
            expected_variables = [item.casefold() for item in body.variables]
            if observed_variables != expected_variables:
                raise ValueError("MCP returned values for different variables")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid read-only LS PLC device observation",
            ) from exc
        store.append_event(
            event_type="controller.devices_read",
            payload={
                "vendor": "ls",
                "observedAt": result.observed_at.isoformat(),
                "dataType": result.data_type,
                "variables": body.variables,
                "readOnly": True,
                "strongEnoughForWrite": False,
            },
        )
        return result

    @app.delete(
        "/api/v1/controllers/ls/connection",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def disconnect_ls_controller_v1() -> Response:
        nonlocal active_ls_controller
        was_connected = active_ls_controller is not None
        active_ls_controller = None
        mcp.clear_controller_endpoint()
        store.set_host_state("active_ls_controller", None)
        if was_connected:
            store.append_event(
                event_type="controller.disconnected",
                payload={
                    "vendor": "ls",
                    "readOnly": True,
                    "strongEnoughForWrite": False,
                },
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/v1/host/shutdown",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def request_host_shutdown() -> dict[str, str]:
        async with run_submission_lock:
            active_run_ids = [run_id for run_id, task in run_tasks.items() if not task.done()]
            if active_run_ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="ControlStudio cannot exit while an agent run is active",
                )
            if active_change_executions:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("ControlStudio cannot exit while a change transaction is active"),
                )
            native_operation = native_project_repository.blocking_operation()
            if native_operation is not None and native_operation.state == "EXECUTING":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "ControlStudio cannot exit while native XG5000 project "
                        "creation is executing"
                    ),
                )
            target_operation = store.active_target_operation_lease()
            if target_operation is not None and target_operation.get("state") == "ACTIVE":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("ControlStudio cannot exit while an XG5000 target operation is active"),
                )
            shutdown_callback = app.state.request_shutdown
            if not callable(shutdown_callback):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Host shutdown is not available in this runtime",
                )
            shutdown_event.set()

        async def stop_after_response() -> None:
            await asyncio.sleep(0.05)
            shutdown_callback()

        asyncio.create_task(
            stop_after_response(),
            name="controlstudio-requested-shutdown",
        )
        return {"status": "shutting_down"}

    @app.get("/api/v1/bootstrap", response_model=BootstrapV1)
    async def bootstrap_v1(request: Request) -> BootstrapV1:
        return BootstrapV1(
            status=current_status_v1(),
            csrf_token=browser_csrf_token(request),
            client_session_id=str(uuid.uuid4()),
            last_event_sequence=store.latest_event_sequence(),
        )

    @app.get("/api/bootstrap", response_model=BootstrapResponse)
    async def bootstrap() -> BootstrapResponse:
        return BootstrapResponse(
            build_id=__version__,
            host_instance_id=host_instance_id,
            canonical_origin=config.canonical_origin or "",
            latest_event_sequence=store.latest_event_sequence(),
            features=BootstrapFeatures(mutations=True),
        )

    @app.post(
        "/api/conversations",
        response_model=Conversation,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_conversation(body: ConversationCreate) -> Conversation:
        return store.create_conversation(title=body.title)

    @app.get("/api/conversations", response_model=ConversationPage)
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> ConversationPage:
        items, total = store.list_conversations(limit=limit, offset=offset)
        return ConversationPage(items=items, total=total, limit=limit, offset=offset)

    @app.get(
        "/api/conversations/{conversation_id}",
        response_model=ConversationDetail,
    )
    async def get_conversation(conversation_id: str) -> ConversationDetail:
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation

    @app.post(
        "/api/conversations/{conversation_id}/messages",
        status_code=status.HTTP_410_GONE,
    )
    async def reject_legacy_user_message(
        conversation_id: str,
    ) -> None:
        del conversation_id
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "legacy direct message append is disabled; use the v1 atomic "
                "conversation run endpoint"
            ),
        )

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_conversation_v1(
        body: ConversationCreateV1,
    ) -> ConversationV1:
        engineering_target = (
            current_engineering_conversation_target() if body.workflow == "engineering" else None
        )
        conversation = store.create_conversation(
            title=body.title,
            workflow=body.workflow,
            project_brief=(
                body.project_brief.model_dump() if body.project_brief is not None else None
            ),
            workspace_id=(engineering_target[0] if engineering_target is not None else None),
            revision_id=(engineering_target[1] if engineering_target is not None else None),
            engineering_session_id=(
                engineering_target[2] if engineering_target is not None else None
            ),
        )
        detail = store.get_conversation(conversation.id)
        assert detail is not None
        return _conversation_v1(detail)

    @app.get(
        "/api/v1/conversations",
        response_model=ConversationPageV1,
    )
    async def list_conversations_v1(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> ConversationPageV1:
        items, total = store.list_conversations(limit=limit, offset=offset)
        return ConversationPageV1(
            items=[_conversation_summary_v1(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationV1,
    )
    async def get_conversation_v1(conversation_id: str) -> ConversationV1:
        detail = store.get_conversation(conversation_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return _conversation_v1(detail)

    @app.post(
        "/api/v1/conversations/{conversation_id}/project-plan/review",
        response_model=ProjectPlanReviewResponseV1,
    )
    async def review_project_plan_v1(
        conversation_id: str,
        body: ProjectPlanReviewRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> ProjectPlanReviewResponseV1:
        try:
            review, recorded = store.review_project_plan(
                conversation_id=conversation_id,
                message_id=body.message_id,
                plan_digest=body.plan_digest,
                reviewed_section_ids=body.reviewed_section_ids,
                actor_id=_request_actor_id(request),
            )
        except ProjectPlanReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if review is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return ProjectPlanReviewResponseV1(
            review=ProjectPlanReviewV1(
                message_id=review.message_id,
                plan_digest=review.plan_digest,
                reviewed_section_ids=review.reviewed_section_ids,
                actor_id=review.actor_id,
                reviewed_at=review.reviewed_at,
            ),
            review_recorded=recorded,
        )

    async def execute_agent_run(
        *,
        conversation_id: str,
        run_id: str,
        request_message_id: str,
        prompt: str,
    ) -> None:
        settings: StoredModelConnection | None = None
        workflow = "engineering"
        try:
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise AgentRunPreconditionError("대화 기록을 찾을 수 없습니다.")
            workflow = detail.workflow
            if workflow == "engineering" and not engineering_conversation_matches_current_target(
                detail
            ):
                raise AgentRunPreconditionError(
                    "이 대화가 생성된 XG5000 대상과 현재 대상이 다릅니다. "
                    "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                )
            if model_repository is None:
                raise AgentRunPreconditionError(
                    "Windows 보안 자격 증명 저장소를 사용할 수 없습니다."
                )
            settings = model_repository.get()
            if settings is None:
                raise AgentRunPreconditionError("먼저 OpenAI 호환 모델 연결을 설정해 주세요.")
            if settings.capability_verified_at is None:
                raise AgentRunPreconditionError(
                    "저장된 모델 연결의 에이전트 기능 검사가 필요합니다."
                )
            history = [item for item in detail.messages if item.id != request_message_id]
            request_message = next(
                (item for item in detail.messages if item.id == request_message_id),
                None,
            )
            request_actor_id = (
                str(request_message.metadata.get("actor_id"))
                if request_message is not None
                and isinstance(request_message.metadata.get("actor_id"), str)
                else f"agent-run:{run_id}"
            )
            workflow = (
                str(request_message.metadata["workflow"])
                if request_message is not None
                and request_message.metadata.get("workflow")
                in {"engineering", "new_project_planning"}
                else "engineering"
            )
            change_request_scope = (
                str(request_message.metadata["change_request_scope"])
                if request_message is not None
                and request_message.metadata.get("change_request_scope")
                in {
                    "analysis_only",
                    "offline_candidate_revision",
                    "permanent_offline_revision",
                }
                else "analysis_only"
            )
            planning_brief: dict[str, Any] | None = None
            if workflow == "new_project_planning":
                if detail.project_brief is not None:
                    planning_brief = detail.project_brief
                else:
                    legacy_brief = _legacy_project_brief(detail)
                    if legacy_brief is not None:
                        planning_brief = legacy_brief.model_dump()
            knowledge_workspace_snapshot = (
                deepcopy(active_workspace)
                if workflow == "engineering" and isinstance(active_workspace, dict)
                else None
            )
            engineering_session_snapshot = (
                deepcopy(active_engineering_session)
                if workflow == "engineering"
                and _engineering_session_read_ready(active_engineering_session)
                else None
            )
            if workflow == "engineering" and mcp.status.status != "ready":
                raise AgentRunPreconditionError("MCP 안전 코어가 준비되지 않았습니다.")

            async def publish_token(delta: str) -> None:
                store.append_event(
                    event_type="agent.token",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload={"delta": delta},
                )

            async def publish_tool_event(
                event_type: str,
                payload: dict[str, Any],
            ) -> None:
                if event_type not in {
                    "tool.started",
                    "tool.completed",
                    "tool.failed",
                }:
                    raise ValueError("unsupported agent tool event type")
                safe_payload: dict[str, Any] = {}
                tool_name = payload.get("tool_name")
                if not isinstance(tool_name, str) or not tool_name:
                    raise ValueError("agent tool event requires tool_name")
                safe_payload["tool_name"] = tool_name[:200]
                tool_call_id = payload.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id:
                    safe_payload["tool_call_id"] = tool_call_id[:256]
                failure_kind = payload.get("failure_kind")
                if failure_kind in {
                    "tool_error",
                    "host_policy",
                    "cancelled",
                    "transport",
                }:
                    safe_payload["failure_kind"] = failure_kind
                store.append_event(
                    event_type=event_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload=safe_payload,
                )
                if event_type == "tool.completed":
                    evidence = _agent_tool_evidence_ref(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_name=tool_name,
                        tool_call_id=(
                            tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
                        ),
                    )
                    if evidence is not None:
                        store.append_event(
                            event_type="evidence.added",
                            conversation_id=conversation_id,
                            run_id=run_id,
                            payload=evidence,
                        )

            runtime = runtime_factory(settings, execution_broker)
            bind_knowledge_snapshot = getattr(runtime, "bind_knowledge_snapshot", None)
            if callable(bind_knowledge_snapshot):

                async def sync_knowledge_sources_for_agent() -> list[dict[str, Any]]:
                    # The Host remains the authority for original source
                    # lifecycle.  It transfers a sanitized snapshot only; the
                    # private Agent owns all query/retrieval/reranking.
                    return knowledge_store.agent_source_snapshot()

                bind_knowledge_snapshot(sync_knowledge_sources_for_agent)
            bind_change_planner = getattr(runtime, "bind_change_planner", None)
            if (
                callable(bind_change_planner)
                and workflow == "engineering"
                and change_request_scope != "analysis_only"
                and isinstance(knowledge_workspace_snapshot, dict)
                and _engineering_session_identity_verified(engineering_session_snapshot)
            ):

                async def propose_change(**values: Any) -> dict[str, Any]:
                    if not engineering_conversation_matches_current_target(detail):
                        raise AgentRunPreconditionError(
                            "변경 계약을 만들기 전에 대상이 바뀌었습니다. "
                            "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                        )
                    plan = ChangePlanV1(
                        change_mode=values["change_mode"],
                        request_scope=change_request_scope,
                        target=values["target"],
                        current=values["current"],
                        proposed=values["proposed"],
                        impact=values["impact"],
                        risks=values["risks"],
                        assumptions=values["assumptions"],
                        blocking_unknowns=values["blocking_unknowns"],
                        alternatives=values["alternatives"],
                        acceptance_criteria=values["acceptance_criteria"],
                        observation_signals=values["observation_signals"],
                        stop_conditions=values["stop_conditions"],
                        coverage=values["coverage"],
                        verification_scope=values["verification_scope"],
                        validation=values["validation"],
                        manual_follow_up=values["manual_follow_up"],
                        rollback=values["rollback"],
                    )
                    try:
                        transaction, created = create_server_change_transaction(
                            operation_id=f"agent-change:{run_id}",
                            actor_id=request_actor_id,
                            conversation_id=conversation_id,
                            run_id=run_id,
                            intent=str(values["intent"]),
                            risk_level=str(values["risk_level"]),
                            plan=plan,
                            approval_ttl_seconds=900,
                        )
                    except ChangeExecutionPreconditionError as exc:
                        raise AgentRunPreconditionError(
                            f"변경 계약을 만들 수 없습니다: {exc}"
                        ) from exc
                    return {
                        "transaction_id": transaction.id,
                        "state": transaction.state.value,
                        "plan_digest": transaction.plan_digest,
                        "approval_expires_at": (transaction.approval_expires_at.isoformat()),
                        "created": created,
                        "execution_permitted": False,
                        "next_action": "human_approval_required",
                    }

                bind_change_planner(propose_change)
            output = await runtime.run(
                prompt=prompt,
                history=history,
                conversation_id=conversation_id,
                run_id=run_id,
                trusted_context=(
                    {
                        "workflow": "new_project_planning",
                        "project_brief": planning_brief,
                        "current_prompt_is_structured_brief": bool(
                            request_message.metadata.get("structured_project_brief")
                        )
                        if request_message is not None
                        else False,
                    }
                    if workflow == "new_project_planning"
                    else (
                        {
                            **engineering_session_snapshot,
                            "change_request_scope": change_request_scope,
                        }
                        if _engineering_session_read_ready(engineering_session_snapshot)
                        else None
                    )
                ),
                on_token=publish_token,
                on_tool_event=publish_tool_event,
            )
            assistant_message = store.append_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=output.content,
                metadata={
                    "run_id": run_id,
                    "status": "complete",
                    "model": output.model,
                    "external_model": is_external_model_endpoint(settings.base_url),
                    "input_tokens": output.input_tokens,
                    "output_tokens": output.output_tokens,
                    "workflow": workflow,
                    **(
                        {"plan_status": output.plan_status}
                        if workflow == "new_project_planning" and output.plan_status is not None
                        else {}
                    ),
                    **(
                        {"project_name": output.project_name}
                        if workflow == "new_project_planning" and output.project_name is not None
                        else {}
                    ),
                    **(
                        {
                            "plan_artifact": output.plan_artifact,
                            "plan_digest": output.plan_digest,
                        }
                        if (
                            workflow == "new_project_planning"
                            and output.plan_artifact is not None
                            and output.plan_digest is not None
                        )
                        else {}
                    ),
                },
            )
            assert assistant_message is not None
            store.append_event(
                event_type="agent.message",
                conversation_id=conversation_id,
                run_id=run_id,
                payload={
                    "message_id": assistant_message.id,
                    "content": assistant_message.content,
                },
            )
            store.update_run_state(run_id=run_id, state=RunState.COMPLETED)
        except asyncio.CancelledError:
            store.update_run_state(run_id=run_id, state=RunState.CANCELLED)
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize every provider/runtime failure
            if isinstance(exc, AgentRunPreconditionError):
                logger.warning("agent run %s failed a product precondition", run_id)
                user_message = str(exc)
                error_code = "runtime_precondition"
                error_detail = str(exc)
            elif isinstance(exc, AgentOutputContractError):
                logger.warning(
                    "agent run %s failed the structured output contract; "
                    "no model-authored plan was accepted",
                    run_id,
                )
                user_message = (
                    "모델 응답이 ControlStudio의 근거·안전 출력 계약을 충족하지 "
                    "못해 계획을 저장하지 않았습니다. 실제 변경은 수행되지 "
                    "않았습니다. 요구사항을 더 구체화하거나 다른 모델로 다시 "
                    "시도해 주세요."
                )
                error_code = "agent_output_contract"
                error_detail = (
                    "Model output failed the structured evidence/safety contract; "
                    "no model-authored plan or mutation was accepted."
                )
            else:
                raw_detail = str(exc)
                credential_material_detected = (
                    settings is not None
                    and redact_known_model_secrets(raw_detail, settings.secrets) != raw_detail
                )
                logger.error(
                    "agent run %s failed (type=%s, provider_detail_suppressed=true, "
                    "credential_material_detected=%s)",
                    run_id,
                    type(exc).__name__,
                    credential_material_detected,
                )
                user_message = "에이전트 실행에 실패했습니다. 모델 연결과 MCP 상태를 확인해 주세요."
                error_code = type(exc).__name__
                error_detail = (
                    "Agent dependency request failed; provider detail was suppressed "
                    f"({type(exc).__name__})."
                )
            store.append_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=user_message,
                metadata={
                    "run_id": run_id,
                    "status": "failed",
                    "workflow": workflow,
                },
            )
            store.update_run_state(
                run_id=run_id,
                state=RunState.FAILED,
                error_code=error_code,
                error_detail=error_detail,
            )

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=SendMessageResponseV1,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_message_v1(
        conversation_id: str,
        body: SendMessageRequestV1,
        request: Request,
    ) -> SendMessageResponseV1:
        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key is not None and idempotency_key != body.operation_id:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must match operationId",
            )

        async with run_submission_lock:
            if shutdown_event.is_set():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ControlStudio Host is shutting down",
                )
            effective_change_request_scope: str | None = None
            if body.workflow == "engineering":
                effective_change_request_scope = (
                    body.change_request_scope or "analysis_only"
                )
            authoritative_content = (
                _planning_prompt_from_brief(body.project_brief)
                if (body.workflow == "new_project_planning" and body.project_brief is not None)
                else body.content
            )
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            if (
                detail.workflow == "engineering"
                and not engineering_conversation_matches_current_target(detail)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "이 대화가 생성된 XG5000 대상과 현재 대상이 다릅니다. "
                        "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                    ),
                )
            try:
                request_message, run, created = store.create_user_message_and_run(
                    conversation_id=conversation_id,
                    content=authoritative_content,
                    operation_id=body.operation_id,
                    workflow=body.workflow,
                    project_brief=(
                        body.project_brief.model_dump() if body.project_brief is not None else None
                    ),
                    message_metadata={
                        "operation_id": body.operation_id,
                        "actor_id": _request_actor_id(request),
                        "workflow": body.workflow,
                        "structured_project_brief": body.project_brief is not None,
                        **(
                            {"change_request_scope": (effective_change_request_scope)}
                            if effective_change_request_scope is not None
                            else {}
                        ),
                    },
                )
            except ConversationWorkflowConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ConversationActiveRunConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if run is not None and run.conversation_id != conversation_id:
                raise HTTPException(
                    status_code=409,
                    detail="operationId belongs to another conversation",
                )
            if run is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            if not created:
                return SendMessageResponseV1(
                    run_id=run.id,
                    operation_id=body.operation_id,
                    state=run.state.value.casefold(),
                )
            assert request_message is not None

            task = asyncio.create_task(
                execute_agent_run(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    request_message_id=request_message.id,
                    prompt=authoritative_content,
                ),
                name=f"controlstudio-agent-{run.id}",
            )
            run_tasks[run.id] = task

            def finish_run_task(completed_task: asyncio.Task[None]) -> None:
                run_tasks.pop(run.id, None)
                if not completed_task.cancelled():
                    completed_task.exception()

            task.add_done_callback(finish_run_task)
            return SendMessageResponseV1(
                run_id=run.id,
                operation_id=body.operation_id,
                state=run.state.value.casefold(),
            )

    @app.post(
        "/api/v1/runs/{run_id}/cancel",
        response_model=RunCancellationV1,
    )
    async def cancel_run_v1(run_id: str) -> RunCancellationV1:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        terminal_states = {
            RunState.COMPLETED,
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.QUARANTINED,
        }
        if run.state in terminal_states:
            return RunCancellationV1(
                run_id=run.id,
                state=run.state.value.casefold(),
                terminal=True,
                idempotent=True,
                detail="run was already terminal",
            )
        if run.state in {
            RunState.APPROVED_PENDING_EXECUTION,
            RunState.EXECUTING,
            RunState.VERIFYING,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "a mutation-stage run cannot be cancelled until its actual "
                    "engineering state is reconciled"
                ),
            )

        task = run_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        latest = store.get_run(run_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="run not found")
        if latest.state not in terminal_states:
            latest = store.update_run_state(
                run_id=run_id,
                state=RunState.CANCELLED,
            )
            assert latest is not None
        return RunCancellationV1(
            run_id=latest.id,
            state=latest.state.value.casefold(),
            terminal=True,
            idempotent=latest.state is not RunState.CANCELLED,
            detail=(
                "run cancelled"
                if latest.state is RunState.CANCELLED
                else "run reached a terminal state before cancellation"
            ),
        )

    @app.post(
        "/api/v1/change-transactions",
        response_model=ChangeTransactionCreatedV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_change_transaction_v1(
        body: CreateChangeTransactionRequestV1,
        request: Request,
    ) -> ChangeTransactionCreatedV1:
        try:
            transaction, created = create_server_change_transaction(
                operation_id=body.operation_id,
                actor_id=_request_actor_id(request),
                conversation_id=body.conversation_id,
                run_id=body.run_id,
                intent=body.intent,
                risk_level=body.risk_level,
                plan=body.plan,
                approval_ttl_seconds=body.approval_ttl_seconds,
            )
        except (ChangeExecutionPreconditionError, ChangeStoreConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ChangeTransactionCreatedV1(
            transaction=_change_transaction_v1(transaction),
            created=created,
        )

    @app.get(
        "/api/v1/change-transactions",
        response_model=list[ChangeTransactionV1],
    )
    async def list_change_transactions_v1(
        conversation_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[ChangeTransactionV1]:
        return [
            _change_transaction_v1(transaction)
            for transaction in store.list_change_transactions(
                conversation_id=conversation_id,
                limit=limit,
            )
        ]

    @app.get(
        "/api/v1/change-transactions/{transaction_id}",
        response_model=ChangeTransactionV1,
    )
    async def get_change_transaction_v1(
        transaction_id: str,
    ) -> ChangeTransactionV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        return _change_transaction_v1(transaction)

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/receipt-acknowledgement",
        response_model=ChangeReceiptAcknowledgementV1,
    )
    async def acknowledge_change_receipt_v1(
        transaction_id: str,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> ChangeReceiptAcknowledgementV1:
        try:
            transaction, recorded = store.acknowledge_change_receipt(
                transaction_id=transaction_id,
                actor_id=_request_actor_id(request),
            )
        except ChangeStoreConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        return ChangeReceiptAcknowledgementV1(
            transaction=_change_transaction_v1(transaction),
            acknowledgement_recorded=recorded,
        )

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/decision",
        response_model=ChangeDecisionResponseV1,
    )
    async def decide_change_transaction_v1(
        transaction_id: str,
        body: ChangeDecisionRequestV1,
        request: Request,
    ) -> ChangeDecisionResponseV1:
        if body.decision == "approve":
            candidate = store.get_change_transaction(transaction_id)
            if candidate is None:
                raise HTTPException(status_code=404, detail="change transaction not found")
            try:
                validate_executable_plan_data(
                    candidate.plan,
                    conversation_id=candidate.conversation_id,
                    run_id=candidate.run_id,
                )
            except ChangeExecutionPreconditionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            transaction, recorded = store.decide_change_transaction(
                transaction_id=transaction_id,
                decision=ApprovalDecision(body.decision.upper()),
                expected_plan_digest=body.expected_plan_digest,
                actor_id=_request_actor_id(request),
            )
        except ChangeStoreConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if recorded:
            publish_change_event("approval.resolved", transaction)
        if transaction.state is ChangeTransactionState.EXPIRED:
            raise HTTPException(status_code=409, detail="change approval has expired")
        return ChangeDecisionResponseV1(
            transaction=_change_transaction_v1(transaction),
            decision_recorded=recorded,
        )

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/execute",
        response_model=ChangeExecutionResponseV1,
    )
    async def execute_change_transaction_v1(
        transaction_id: str,
        body: ExecuteChangeTransactionRequestV1,
    ) -> ChangeExecutionResponseV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if transaction.plan_digest != body.expected_plan_digest:
            raise HTTPException(status_code=409, detail="plan digest does not match")
        try:
            validate_executable_plan_data(
                transaction.plan,
                conversation_id=transaction.conversation_id,
                run_id=transaction.run_id,
            )
        except ChangeExecutionPreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        holder_token = str(uuid.uuid4())
        try:
            store.acquire_target_operation_lease(
                operation_id=transaction_id,
                owner_kind="offline_change",
                holder_token=holder_token,
                target_identity=transaction.target.model_dump(mode="json"),
                allowed_change_transaction_id=transaction_id,
            )
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            if current_change_target() != transaction.target:
                raise ChangeExecutionPreconditionError(
                    "active workspace or engineering-session identity changed"
                )
            await change_executor.preflight(transaction)
        except ChangeExecutionPreconditionError as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_preflight_failed",
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BaseException:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_preflight_interrupted",
            )
            raise

        try:
            executing = store.begin_change_execution(
                transaction_id=transaction_id,
                expected_plan_digest=body.expected_plan_digest,
            )
        except MutationLeaseConflict as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="durable_mutation_lease_conflict",
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"another change transaction owns this mutation scope: {exc.transaction_id}"
                ),
            ) from exc
        except ChangeStoreConflict as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_execution_not_started",
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        assert executing is not None
        publish_change_event("change.transaction.updated", executing)
        active_change_executions.add(transaction_id)
        try:
            try:
                apply_result = await change_executor.apply(executing)
            except ChangeExecutionFailedSafely as exc:
                failed = store.finish_change_execution(
                    transaction_id=transaction_id,
                    state=ChangeTransactionState.FAILED,
                    error_code="native_change_not_applied",
                    error_detail=str(exc),
                )
                assert failed is not None
                publish_change_event("change.transaction.updated", failed)
                return ChangeExecutionResponseV1(
                    transaction=_change_transaction_v1(failed),
                    execution_started=True,
                )
            except (ChangeExecutionOutcomeUncertain, asyncio.CancelledError) as exc:
                recovery_evidence = (
                    exc.evidence if isinstance(exc, ChangeExecutionOutcomeUncertain) else {}
                )
                quarantined = store.finish_change_execution(
                    transaction_id=transaction_id,
                    state=ChangeTransactionState.QUARANTINED,
                    verification_evidence={"recovery": recovery_evidence},
                    error_code="native_change_outcome_uncertain",
                    error_detail=str(exc),
                )
                assert quarantined is not None
                publish_change_event("change.transaction.updated", quarantined)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return ChangeExecutionResponseV1(
                    transaction=_change_transaction_v1(quarantined),
                    execution_started=True,
                )
            except Exception as exc:
                quarantined = store.finish_change_execution(
                    transaction_id=transaction_id,
                    state=ChangeTransactionState.QUARANTINED,
                    error_code="unexpected_apply_failure",
                    error_detail=f"{type(exc).__name__}: {exc}",
                )
                assert quarantined is not None
                publish_change_event("change.transaction.updated", quarantined)
                logger.exception("change transaction %s apply failed", transaction_id)
                return ChangeExecutionResponseV1(
                    transaction=_change_transaction_v1(quarantined),
                    execution_started=True,
                )

            verifying = store.mark_change_verifying(
                transaction_id=transaction_id,
                apply_evidence=apply_result.evidence,
            )
            store.heartbeat_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
            )
            assert verifying is not None
            publish_change_event("change.transaction.updated", verifying)
            try:
                verification = await change_executor.verify(
                    verifying,
                    apply_result,
                )
            except (Exception, asyncio.CancelledError) as exc:
                quarantined = store.finish_change_execution(
                    transaction_id=transaction_id,
                    state=ChangeTransactionState.QUARANTINED,
                    error_code="verification_outcome_uncertain",
                    error_detail=f"{type(exc).__name__}: {exc}",
                )
                assert quarantined is not None
                publish_change_event("change.transaction.updated", quarantined)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                logger.exception(
                    "change transaction %s verification failed",
                    transaction_id,
                )
                return ChangeExecutionResponseV1(
                    transaction=_change_transaction_v1(quarantined),
                    execution_started=True,
                )
            finished = store.finish_change_execution(
                transaction_id=transaction_id,
                state=verification.state,
                verification_evidence=verification.evidence,
                error_code=verification.error_code,
                error_detail=verification.error_detail,
            )
            assert finished is not None
            refresh_terminal_change_identity(
                finished,
                retain_engineering_session=True,
            )
            publish_change_event("change.transaction.updated", finished)
            store.append_event(
                event_type="host.status",
                payload=current_status_v1().model_dump(
                    mode="json",
                    by_alias=True,
                ),
            )
            return ChangeExecutionResponseV1(
                transaction=_change_transaction_v1(finished),
                execution_started=True,
            )
        finally:
            active_change_executions.discard(transaction_id)

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/reconcile",
        response_model=ChangeReconciliationResponseV1,
    )
    async def reconcile_change_transaction_v1(
        transaction_id: str,
        body: ReconcileChangeTransactionRequestV1,
        request: Request,
    ) -> ChangeReconciliationResponseV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if transaction.plan_digest != body.expected_plan_digest:
            raise HTTPException(status_code=409, detail="plan digest does not match")
        if transaction.state is not ChangeTransactionState.QUARANTINED:
            raise HTTPException(
                status_code=409,
                detail="only a quarantined change transaction can be reconciled",
            )
        holder_token = str(uuid.uuid4())
        try:
            store.acquire_target_operation_lease(
                operation_id=transaction_id,
                owner_kind="offline_change",
                holder_token=holder_token,
                target_identity=transaction.target.model_dump(mode="json"),
                allowed_change_transaction_id=transaction_id,
            )
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            current_target = current_change_target()
            stable_workspace_fields = (
                "workspace_id",
                "revision_id",
                "source_artifact_hash",
                "project_path",
                "vendor",
                "engineering_system",
            )
            if any(
                getattr(current_target, field_name) != getattr(transaction.target, field_name)
                for field_name in stable_workspace_fields
            ):
                raise ChangeExecutionPreconditionError("active recovery workspace identity changed")
        except ChangeExecutionPreconditionError as exc:
            store.park_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="recovery_target_identity_mismatch",
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        active_change_executions.add(transaction_id)
        try:
            try:
                outcome = await change_executor.reconcile(
                    transaction,
                    ChangeRecoveryTarget(pid=body.pid, hwnd=body.hwnd),
                )
            except (Exception, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    store.park_target_operation_lease(
                        operation_id=transaction_id,
                        holder_token=holder_token,
                        reason="reconciliation_cancelled",
                    )
                    raise
                outcome = None
                logger.exception(
                    "change transaction %s reconciliation failed",
                    transaction_id,
                )
            resolved = store.resolve_quarantined_change(
                transaction_id=transaction_id,
                state=(
                    outcome.state if outcome is not None else ChangeTransactionState.QUARANTINED
                ),
                evidence=(
                    outcome.evidence
                    if outcome is not None
                    else {"error": "reconciliation service failed unexpectedly"}
                ),
                actor_id=_request_actor_id(request),
                error_code=(
                    outcome.error_code if outcome is not None else "reconciliation_service_failed"
                ),
                error_detail=(
                    outcome.error_detail
                    if outcome is not None
                    else "The explicit reconciliation attempt did not complete."
                ),
            )
            assert resolved is not None
            if resolved.state is not ChangeTransactionState.QUARANTINED:
                refresh_terminal_change_identity(
                    resolved,
                    retain_engineering_session=False,
                )
            publish_change_event("change.transaction.updated", resolved)
            store.append_event(
                event_type="host.status",
                payload=current_status_v1().model_dump(
                    mode="json",
                    by_alias=True,
                ),
            )
            return ChangeReconciliationResponseV1(
                transaction=_change_transaction_v1(resolved),
                reconciled=resolved.state is not ChangeTransactionState.QUARANTINED,
            )
        finally:
            active_change_executions.discard(transaction_id)

    @app.get("/api/events")
    async def replay_events(
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        conversation_id: str | None = None,
        follow: bool = True,
    ) -> StreamingResponse:
        last_event_id = request.headers.get("last-event-id")
        if last_event_id:
            try:
                after_sequence = max(after_sequence, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

        async def stream() -> AsyncIterator[str]:
            cursor = after_sequence
            last_output = time.monotonic()
            while True:
                if shutdown_event.is_set():
                    return
                events = store.list_events(
                    after_sequence=cursor,
                    conversation_id=conversation_id,
                    include_global=conversation_id is not None,
                )
                for event in events:
                    if shutdown_event.is_set():
                        return
                    cursor = event.sequence
                    last_output = time.monotonic()
                    yield _event_frame(event)
                if not follow:
                    return
                if await request.is_disconnected():
                    return
                if time.monotonic() - last_output >= config.event_heartbeat_seconds:
                    last_output = time.monotonic()
                    yield ": keep-alive\n\n"
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=config.event_poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/events")
    async def replay_events_v1(
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        conversation_id: str | None = None,
        follow: bool = True,
    ) -> StreamingResponse:
        last_event_id = request.headers.get("last-event-id")
        if last_event_id:
            try:
                after_sequence = max(after_sequence, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="invalid Last-Event-ID",
                ) from exc

        async def stream_v1() -> AsyncIterator[str]:
            cursor = after_sequence
            last_output = time.monotonic()
            while True:
                if shutdown_event.is_set():
                    return
                events = store.list_events(
                    after_sequence=cursor,
                    conversation_id=conversation_id,
                    include_global=conversation_id is not None,
                )
                for event in events:
                    if shutdown_event.is_set():
                        return
                    cursor = event.sequence
                    last_output = time.monotonic()
                    yield _event_frame_v1(event)
                if not follow:
                    return
                if await request.is_disconnected():
                    return
                if time.monotonic() - last_output >= config.event_heartbeat_seconds:
                    last_output = time.monotonic()
                    yield ": keep-alive\n\n"
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=config.event_poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

        return StreamingResponse(
            stream_v1(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/settings/model", response_model=ModelSettingsV1)
    async def get_model_settings_v1() -> ModelSettingsV1:
        if model_repository is None:
            raise HTTPException(
                status_code=503,
                detail="Windows secure credential storage is unavailable",
            )
        try:
            value = model_repository.get()
        except SecretStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail="stored model credentials are unavailable",
            ) from exc
        if value is None:
            raise HTTPException(status_code=404, detail="model is not configured")
        return _model_settings_v1(value)

    @app.put("/api/v1/settings/model", response_model=ModelSettingsV1)
    async def save_model_settings_v1(
        body: SaveModelSettingsRequestV1,
    ) -> ModelSettingsV1:
        if model_repository is None:
            raise HTTPException(
                status_code=503,
                detail="Windows secure credential storage is unavailable",
            )
        try:
            existing = model_repository.get()
            candidate = _ephemeral_model_settings(body, existing)
        except ModelSettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SecretStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail="stored model credentials are unavailable",
            ) from exc
        runtime = runtime_factory(candidate, execution_broker)
        try:
            result = await runtime.test_capabilities()
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(
                status_code=502,
                detail=(
                    "model capability verification failed; verify the endpoint, key, "
                    "model name, TLS certificate, and tool-calling support"
                ),
            ) from exc
        if not _required_model_capabilities_ready(result):
            raise HTTPException(
                status_code=422,
                detail=(
                    "model capability verification did not satisfy all ControlStudio "
                    "agent requirements"
                ),
            )
        try:
            value = model_repository.save(
                base_url=candidate.base_url,
                model=candidate.model,
                timeout_seconds=candidate.timeout_seconds,
                api_key=candidate.secrets.api_key,
                custom_headers=candidate.secrets.custom_headers,
                capability_verified_at=result.tested_at,
                inherit_missing_secrets=False,
                allow_insecure_http=body.insecure_http_consent,
            )
        except (ModelSettingsError, SecretStoreError) as exc:
            raise HTTPException(
                status_code=503,
                detail="could not store verified model credentials securely",
            ) from exc
        return _model_settings_v1(value)

    @app.post(
        "/api/v1/settings/model/test",
        response_model=ModelCapabilityResultV1,
    )
    async def test_model_settings_v1(
        body: SaveModelSettingsRequestV1,
    ) -> ModelCapabilityResultV1:
        existing: StoredModelConnection | None = None
        if model_repository is not None:
            try:
                existing = model_repository.get()
            except SecretStoreError:
                existing = None
        try:
            candidate = _ephemeral_model_settings(body, existing)
        except ModelSettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        runtime = runtime_factory(candidate, execution_broker)
        try:
            result = await runtime.test_capabilities()
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(
                status_code=502,
                detail=(
                    "model capability test failed; verify the endpoint, key, "
                    "model name, TLS certificate, and tool-calling support"
                ),
            ) from exc
        return ModelCapabilityResultV1(
            success=result.success,
            tested_at=result.tested_at,
            capabilities=ModelCapabilityFlagsV1(
                completion=result.completion,
                streaming=result.streaming,
                tool_calling=result.tool_calling,
                tool_result_continuation=result.tool_result_continuation,
                structured_output=result.structured_output,
            ),
            message=result.message,
        )

    @app.get(
        "/api/v1/knowledge/summary",
        response_model=KnowledgeSummaryV1,
    )
    async def knowledge_summary_v1() -> KnowledgeSummaryV1:
        return KnowledgeSummaryV1.model_validate(knowledge_store.summary())

    @app.get(
        "/api/v1/knowledge/sources",
        response_model=KnowledgeSourcePageV1,
    )
    async def list_knowledge_sources_v1(
        kinds: Annotated[list[str] | None, Query()] = None,
        statuses: Annotated[list[str] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
    ) -> KnowledgeSourcePageV1:
        try:
            values = knowledge_store.list_sources(
                kinds=kinds,
                statuses=(
                    statuses
                    if statuses is not None
                    else [
                        KnowledgeStatus.PROPOSED.value,
                        KnowledgeStatus.CONFIRMED.value,
                    ]
                ),
                limit=limit,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return KnowledgeSourcePageV1(
            items=[_knowledge_source_v1(value) for value in values],
            total=len(values),
        )

    @app.get(
        "/api/v1/knowledge/search",
        response_model=KnowledgeSearchResponseV1,
    )
    async def search_knowledge_v1(
        request: Request,
        query: Annotated[str, Query(min_length=1, max_length=2_048)],
        vendor: Annotated[str | None, Query(max_length=128)] = None,
        kinds: Annotated[list[str] | None, Query()] = None,
        scopes: Annotated[list[str] | None, Query()] = None,
        product_family: Annotated[str | None, Query(max_length=256)] = None,
        version: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 8,
    ) -> KnowledgeSearchResponseV1:
        try:
            # Platform owns source lifecycle and performs no retrieval.  Each
            # request replaces the sidecar's derived snapshot so updates,
            # archive/delete, and a restarted Agent cannot retain stale text.
            await agent_supervisor.sync_knowledge(knowledge_store.agent_source_snapshot())
            result = await agent_supervisor.search_knowledge(
                {
                    "query": query,
                    "vendor": vendor,
                    "kinds": kinds,
                    "scopes": scopes,
                    "product_family": product_family,
                    "version": version,
                    "limit": limit,
                }
            )
            values = result.get("results")
            if not isinstance(values, list):
                raise TypeError("Agent knowledge search result is invalid")
            hits = [_agent_knowledge_hit_v1(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("Agent knowledge search unavailable: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent knowledge sidecar is unavailable",
            ) from exc
        return KnowledgeSearchResponseV1(
            query=query,
            retrieval_mode="agent_sidecar",
            results=hits,
        )

    @app.post(
        "/api/v1/knowledge/import-local",
        response_model=KnowledgeImportResultV1,
    )
    async def import_local_knowledge_v1(
        body: ImportKnowledgeRequestV1,
        _csrf: None = Depends(require_browser_csrf),
    ) -> KnowledgeImportResultV1:
        if file_dialog_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="another local file dialog is already open",
            )
        try:
            async with file_dialog_lock:
                selected_path = await asyncio.to_thread(select_knowledge_document_file)
        except NativeFileDialogUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=str(exc),
            ) from exc
        except NativeFileDialogError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        if selected_path is None:
            return KnowledgeImportResultV1(selected=False)

        try:
            report = await asyncio.to_thread(
                extract_local_knowledge_document,
                Path(selected_path),
            )
            local_authority = "user_imported_reference"
            applicability_digest = hashlib.sha256(
                json.dumps(
                    {
                        "kind": body.kind,
                        "scope": body.scope,
                        "vendor": body.vendor,
                        "product_family": body.product_family,
                        "version": body.version,
                        "language": body.language,
                        "authority": local_authority,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            external_key = f"local-document:{report.source_sha256}:{applicability_digest}"
            existing = knowledge_store.get_source_by_external_key(external_key)
            if existing is None:
                try:
                    source = knowledge_store.import_pages(
                        title=(body.title.strip() if body.title else Path(report.file_name).stem),
                        pages=(
                            PageRecord(
                                text=page.text,
                                page=page.page_number,
                                locator=f"{report.file_name}#page={page.page_number}",
                                section=page.section,
                            )
                            for page in report.pages
                        ),
                        kind=body.kind,
                        scope=body.scope,
                        authority=local_authority,
                        vendor=body.vendor,
                        product_family=body.product_family,
                        version=body.version,
                        language=body.language,
                        status=KnowledgeStatus.CONFIRMED,
                        locator=report.file_name,
                        revision=body.version or "1",
                        source_sha256=report.source_sha256,
                        external_key=external_key,
                        metadata={
                            "file_name": report.file_name,
                            "media_type": report.media_type,
                            "page_count": report.page_count,
                            "indexed_page_count": report.indexed_page_count,
                            "low_text_pages": list(report.low_text_pages),
                            "extraction_status": report.status,
                        },
                    )
                except KnowledgeConflict:
                    # Concurrent identical imports share the same authority
                    # record instead of turning a harmless race into HTTP 500.
                    source = knowledge_store.get_source_by_external_key(external_key)
                    if source is None:
                        raise
            else:
                source = existing
        except (KnowledgeIngestionError, KnowledgeConflict, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.append_event(
            event_type="knowledge.source_imported",
            payload={
                "sourceId": source.source_id,
                "kind": source.kind.value,
                "title": source.title,
                "vendor": source.vendor,
                "version": source.version,
                "extractionStatus": report.status,
            },
        )
        return KnowledgeImportResultV1(
            selected=True,
            source=_knowledge_source_v1(source),
            extraction_status=report.status,
            page_count=report.page_count,
            indexed_page_count=report.indexed_page_count,
            low_text_pages=list(report.low_text_pages),
        )

    @app.post(
        "/api/v1/knowledge/memories",
        response_model=KnowledgeSourceV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def propose_knowledge_memory_v1(
        body: CreateKnowledgeMemoryRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> KnowledgeSourceV1:
        try:
            source = knowledge_store.propose_memory(
                title=body.title,
                content=body.content,
                kind=body.kind,
                scope=body.scope,
                proposed_by=_request_actor_id(request),
                vendor=body.vendor,
                product_family=body.product_family,
                version=body.version,
                language=body.language,
                metadata={
                    "proposal_source": "browser",
                    "content": body.content,
                },
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.append_event(
            event_type="knowledge.memory_proposed",
            payload={
                "sourceId": source.source_id,
                "kind": source.kind.value,
                "title": source.title,
                "scope": source.scope,
            },
        )
        return _knowledge_source_v1(source)

    @app.post(
        "/api/v1/knowledge/memories/{source_id}/confirm",
        response_model=KnowledgeSourceV1,
    )
    async def confirm_knowledge_memory_v1(
        source_id: str,
        body: ConfirmKnowledgeMemoryRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> KnowledgeSourceV1:
        try:
            source = knowledge_store.confirm_memory(
                source_id,
                confirmed_by=_request_actor_id(request),
                expected_revision=body.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge memory not found") from exc
        except KnowledgeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.append_event(
            event_type="knowledge.memory_confirmed",
            payload={
                "sourceId": source.source_id,
                "kind": source.kind.value,
                "title": source.title,
                "scope": source.scope,
            },
        )
        return _knowledge_source_v1(source)

    @app.delete(
        "/api/v1/knowledge/sources/{source_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_knowledge_source_v1(
        source_id: str,
        _csrf: None = Depends(require_browser_csrf),
    ) -> Response:
        source = knowledge_store.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="knowledge source not found")
        if source.kind is KnowledgeKind.CONTROL_PATTERN:
            raise HTTPException(
                status_code=409,
                detail="built-in control patterns cannot be removed",
            )
        if not knowledge_store.delete_source(source_id):
            raise HTTPException(status_code=404, detail="knowledge source not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def bind_engineering_session(result: Any) -> dict[str, Any]:
        nonlocal active_engineering_session
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid attach result",
            )
        session = result["session"] if isinstance(result.get("session"), dict) else result
        required_identity = {"session_id", "pid", "hwnd", "project_path", "project_hash"}
        if not required_identity.issubset(session):
            raise HTTPException(
                status_code=502,
                detail="MCP attach result is missing verified identity fields",
            )
        active_engineering_session = dict(session)
        if _engineering_session_read_ready(active_engineering_session):
            execution_broker.bind_agent_session(active_engineering_session)
        else:
            execution_broker.clear_agent_session()
        return active_engineering_session

    def require_engineering_read_target() -> dict[str, Any]:
        if mcp.status.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="XG5000 MCP가 준비되지 않아 프로그램을 확인할 수 없습니다.",
            )
        session = active_engineering_session
        if not _engineering_session_read_ready(session):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="먼저 읽기 가능한 XG5000 프로젝트 세션을 연결해 주세요.",
            )
        assert isinstance(session, dict)
        required_identity = ("session_id", "pid", "hwnd", "project_path", "project_hash")
        if any(
            session.get(field) is None or session.get(field) == "" for field in required_identity
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 XG5000 프로젝트 세션의 대상 증명이 불완전합니다.",
            )
        return session

    def engineering_program_from_mcp(value: Any) -> EngineeringProgramV1:
        if not isinstance(value, dict):
            raise TypeError("program entry must be an object")
        if any(
            not isinstance(value.get(field), str) for field in ("program_id", "name", "language")
        ):
            raise TypeError("program identity fields must be strings")
        return EngineeringProgramV1.model_validate(
            {
                "program_id": value.get("program_id"),
                "name": value.get("name"),
                "language": value.get("language"),
            }
        )

    def engineering_gateway_error(exc: MCPToolError) -> HTTPException:
        detail = str(exc)
        unavailable = "MCP is not ready" in detail or "disconnected" in detail
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE if unavailable else status.HTTP_409_CONFLICT
            ),
            detail=(
                "XG5000 MCP 연결이 끊겨 프로그램 작업을 완료하지 못했습니다."
                if unavailable
                else (
                    "현재 XG5000 창과 프로젝트를 다시 확인해 주세요. "
                    "대상이 달라졌거나 더 이상 읽을 수 없습니다."
                )
            ),
        )

    @app.get(
        "/api/v1/engineering/programs",
        response_model=EngineeringProgramsV1,
    )
    async def list_engineering_programs_v1() -> EngineeringProgramsV1:
        require_engineering_read_target()
        try:
            raw_result = await mcp.call_read_tool(
                "project.list_programs",
                {"limit": 500},
            )
        except MCPToolError as exc:
            raise engineering_gateway_error(exc) from exc
        if not isinstance(raw_result, dict) or not isinstance(raw_result.get("programs"), list):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid XG5000 program catalog.",
            )
        pagination = raw_result.get("pagination")
        if isinstance(pagination, dict) and pagination.get("next_offset") is not None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="XG5000 program catalog exceeds the supported 500-program limit.",
            )
        try:
            programs = [engineering_program_from_mcp(value) for value in raw_result["programs"]]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid XG5000 program identity.",
            ) from exc
        program_ids = [program.program_id for program in programs]
        if len(program_ids) != len(set(program_ids)):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned duplicate XG5000 program identities.",
            )
        return EngineeringProgramsV1(programs=programs)

    @app.post(
        "/api/v1/engineering/programs/{program_id}/focus",
        response_model=EngineeringProgramFocusV1,
    )
    async def focus_engineering_program_v1(
        program_id: str,
        _csrf: None = Depends(require_browser_csrf),
    ) -> EngineeringProgramFocusV1:
        require_engineering_read_target()
        normalized_program_id = program_id.strip()
        if (
            normalized_program_id != program_id
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}",
                normalized_program_id,
            )
            is None
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="program_id is invalid",
            )
        try:
            raw_result = await mcp.call_read_tool(
                "program.focus",
                {"program_id": normalized_program_id},
            )
        except MCPToolError as exc:
            raise engineering_gateway_error(exc) from exc
        if not isinstance(raw_result, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid program focus result.",
            )
        raw_program = raw_result.get("program")
        activation = raw_result.get("activation")
        try:
            program = engineering_program_from_mcp(raw_program)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid focused program identity.",
            ) from exc
        if (
            program.program_id != normalized_program_id
            or raw_result.get("verified") is not True
            or raw_result.get("read_only") is not True
            or raw_result.get("project_unchanged") is not True
            or not isinstance(activation, dict)
            or activation.get("exact_program_active") is not True
            or activation.get("exact_window_foreground") is not True
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP did not verify the exact XG5000 program focus operation.",
            )
        return EngineeringProgramFocusV1(
            verified=True,
            read_only=True,
            project_unchanged=True,
            program=program,
        )

    async def open_workspace_in_xg5000(
        workspace: LocalWorkspaceV1,
        *,
        timeout_seconds: float,
    ) -> tuple[XG5000ProjectLaunchV1, dict[str, Any] | None]:
        try:
            raw_launch = await mcp.call_workspace_tool(
                "xg5000.open_project",
                {
                    "workspace_id": workspace.workspace_id,
                    "expected_working_hash": workspace.working_copy.current_sha256,
                    "timeout_seconds": timeout_seconds,
                },
            )
        except MCPToolError as exc:
            logger.warning(
                "XG5000 could not open the verified workspace working copy: %s",
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "working copy는 준비됐지만 XG5000에서 열지 못했습니다. "
                    "XG5000 설치와 파일 연결을 확인해 주세요."
                ),
            ) from exc
        if not isinstance(raw_launch, dict):
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid XG5000 launch result",
            )
        try:
            launch = XG5000ProjectLaunchV1.model_validate(
                {
                    name: raw_launch[name]
                    for name in XG5000ProjectLaunchV1.model_fields
                    if name in raw_launch
                }
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid XG5000 launch result",
            ) from exc

        session: dict[str, Any] | None = None
        instance = launch.instance
        if launch.window_detected and isinstance(instance, dict):
            try:
                attached = await mcp.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": int(instance["pid"]),
                        "hwnd": int(instance["hwnd"]),
                        "project_path": workspace.working_copy.path,
                        "project_hash": workspace.working_copy.current_sha256,
                        "workspace_id": workspace.workspace_id,
                    },
                )
            except (KeyError, TypeError, ValueError, MCPToolError):
                session = None
            else:
                session = bind_engineering_session(attached)
        return launch, session

    @app.post(
        "/api/v1/files/select-xg5000-project",
        response_model=XG5000ProjectFileSelectionV1,
    )
    async def select_xg5000_project_v1(
        _csrf: None = Depends(require_browser_csrf),
    ) -> XG5000ProjectFileSelectionV1:
        if file_dialog_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="an XG5000 project file dialog is already open",
            )
        try:
            async with file_dialog_lock:
                selected_path = await asyncio.to_thread(select_xg5000_project_file)
        except NativeFileDialogUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=str(exc),
            ) from exc
        except NativeFileDialogError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        if selected_path is None:
            return XG5000ProjectFileSelectionV1(selected=False)
        return XG5000ProjectFileSelectionV1(
            selected=True,
            path=selected_path,
        )

    @app.post(
        "/api/v1/workspaces/open-local",
        response_model=OpenLocalWorkspaceResponseV1,
    )
    async def open_local_workspace_v1(
        body: OpenLocalWorkspaceRequestV1,
        _target_lease: None = Depends(target_switch_lease),
    ) -> OpenLocalWorkspaceResponseV1:
        nonlocal active_engineering_session, active_workspace
        arguments: dict[str, Any] = {"source_path": body.source_path}
        if body.expected_source_hash is not None:
            arguments["expected_source_hash"] = body.expected_source_hash
        try:
            opened = await mcp.call_workspace_tool("workspace.open_native", arguments)
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "로컬 프로젝트의 안전한 작업공간을 만들지 못했습니다. "
                    "파일 경로·형식·접근 권한을 확인해 주세요."
                ),
            ) from exc
        try:
            workspace = _local_workspace_v1(opened)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid workspace identity",
            ) from exc
        # The new workspace is now the selected target. Drop any session bound
        # to the previous workspace before exposing the new identity, including
        # when the following XG5000 launch or attach fails.
        active_engineering_session = None
        execution_broker.clear_agent_session()
        active_workspace = workspace.model_dump(mode="json")
        store.set_host_state("active_workspace", active_workspace)

        launch: XG5000ProjectLaunchV1 | None = None
        session: dict[str, Any] | None = None
        if body.open_in_xg5000:
            launch, session = await open_workspace_in_xg5000(
                workspace,
                timeout_seconds=body.timeout_seconds,
            )

        store.append_event(
            event_type="workspace.opened",
            payload={
                "workspace_id": workspace.workspace_id,
                "revision_id": workspace.revision_id,
                "source_kind": workspace.source_kind,
                "working_hash": workspace.working_copy.current_sha256,
                "xg5000_window_detected": bool(launch and launch.window_detected),
                "engineering_session_attached": session is not None,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(
                mode="json",
                by_alias=True,
            ),
        )
        return OpenLocalWorkspaceResponseV1(
            workspace=workspace,
            launch=launch,
            session=session,
        )

    @app.post(
        "/api/v1/workspaces/{workspace_id}/resume",
        response_model=OpenLocalWorkspaceResponseV1,
    )
    async def resume_local_workspace_v1(
        workspace_id: str,
        body: ResumeLocalWorkspaceRequestV1,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> OpenLocalWorkspaceResponseV1:
        nonlocal active_engineering_session, active_workspace
        persisted = active_workspace
        if (
            not isinstance(persisted, dict)
            or str(persisted.get("workspace_id") or "") != workspace_id
        ):
            raise HTTPException(
                status_code=404,
                detail="the requested workspace is not the active persisted workspace",
            )

        # A resume never trusts a session from an earlier target operation. The
        # persisted workspace and current on-disk artifacts must be reverified
        # before a new XG5000 binding can be exposed to the agent.
        active_engineering_session = None
        execution_broker.clear_agent_session()
        try:
            persisted_workspace = _local_workspace_v1(persisted)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail="the active persisted workspace identity is invalid",
            ) from exc
        try:
            checked = await mcp.call_workspace_tool(
                "workspace.status",
                {"workspace_id": workspace_id},
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "저장된 작업공간을 다시 검증하지 못했습니다. working copy와 "
                    "immutable source 상태를 확인해 주세요."
                ),
            ) from exc
        try:
            workspace = _local_workspace_v1(checked)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid workspace status",
            ) from exc
        if (
            workspace.source.original_state != "unchanged"
            or workspace.source.original_current_sha256 != workspace.source.sha256
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "원본 프로젝트가 작업공간 생성 이후 변경·삭제되었거나 읽을 수 "
                    "없습니다. 현재 원본을 새 작업으로 열거나, 보존된 복사본 사용을 "
                    "명시적으로 선택해야 합니다."
                ),
            )
        if _workspace_resume_identity(workspace) != _workspace_resume_identity(persisted_workspace):
            raise HTTPException(
                status_code=409,
                detail=(
                    "저장된 작업공간 identity와 현재 working copy가 일치하지 "
                    "않아 자동 재개를 차단했습니다."
                ),
            )

        active_workspace = workspace.model_dump(mode="json")
        store.set_host_state("active_workspace", active_workspace)
        launch, session = await open_workspace_in_xg5000(
            workspace,
            timeout_seconds=body.timeout_seconds,
        )
        if session is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "working copy는 XG5000에서 열었지만 정확한 읽기 전용 분석 "
                    "세션을 다시 검증하지 못했습니다."
                ),
            )
        store.append_event(
            event_type="workspace.resumed",
            payload={
                "workspace_id": workspace.workspace_id,
                "revision_id": workspace.revision_id,
                "working_hash": workspace.working_copy.current_sha256,
                "xg5000_window_detected": launch.window_detected,
                "engineering_session_attached": True,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(
                mode="json",
                by_alias=True,
            ),
        )
        return OpenLocalWorkspaceResponseV1(
            workspace=workspace,
            launch=launch,
            session=session,
        )

    @app.get(
        "/api/v1/workspaces/{workspace_id}",
        response_model=LocalWorkspaceV1,
    )
    async def local_workspace_status_v1(workspace_id: str) -> LocalWorkspaceV1:
        nonlocal active_workspace
        try:
            result = await mcp.call_workspace_tool(
                "workspace.status",
                {"workspace_id": workspace_id},
            )
        except MCPToolError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        try:
            workspace = _local_workspace_v1(result)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid workspace status",
            ) from exc
        if (
            active_workspace is not None
            and active_workspace.get("workspace_id") == workspace.workspace_id
        ):
            active_workspace = workspace.model_dump(mode="json")
            store.set_host_state("active_workspace", active_workspace)
        return workspace

    @app.get("/api/v1/xg5000/instances", response_model=XG5000InstancesV1)
    async def xg5000_instances_v1() -> XG5000InstancesV1:
        try:
            result = await mcp.call_read_tool("xg5000.instances", {})
        except MCPToolError as exc:
            raise HTTPException(
                status_code=503,
                detail="XG5000 MCP is unavailable",
            ) from exc
        instances: list[dict[str, Any]]
        if isinstance(result, dict) and isinstance(result.get("instances"), list):
            instances = [item for item in result["instances"] if isinstance(item, dict)]
        elif isinstance(result, list):
            instances = [item for item in result if isinstance(item, dict)]
        else:
            instances = []
        return XG5000InstancesV1(status="ready", instances=instances)

    @app.post(
        "/api/v1/native-projects",
        response_model=NativeProjectCreatedV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_native_project_v1(
        body: CreateNativeProjectRequestV1,
        request: Request,
        response: Response,
    ) -> NativeProjectCreatedV1:
        nonlocal active_engineering_session, active_workspace
        if not config.validation_enable_native_project_create:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "native XG5000 project creation is unavailable in this "
                    "product build; the current implementation has fake-backend "
                    "evidence only"
                ),
            )
        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key != body.operation_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key must exactly match operationId",
            )
        actor_id = _request_actor_id(request)
        spec = NativeProjectSpec(
            operation_id=body.operation_id,
            project_name=body.project_name,
            workspace_parent=Path(body.workspace_parent),
            plc_series=body.plc_series,
            cpu_model=body.cpu_model,
            program_name=body.program_name,
            language=body.language,
            description=body.description,
            xg5000_pid=body.xg5000_pid,
            xg5000_hwnd=body.xg5000_hwnd,
            planning_conversation_id=body.reviewed_plan.conversation_id,
            planning_message_id=body.reviewed_plan.message_id,
            planning_plan_digest=body.reviewed_plan.plan_digest,
            reviewed_section_ids=tuple(body.reviewed_plan.reviewed_section_ids),
        )
        holder_token: str | None = None
        try:
            existing = native_project_service.replay_if_exists(
                spec,
                actor_id=actor_id,
            )
            if existing is not None:
                response.status_code = status.HTTP_200_OK
                return NativeProjectCreatedV1(
                    operation=_native_project_operation_v1(existing),
                    created=False,
                )
            _review, reviewed_artifact = store.require_reviewed_project_plan(
                conversation_id=body.reviewed_plan.conversation_id,
                message_id=body.reviewed_plan.message_id,
                plan_digest=body.reviewed_plan.plan_digest,
                reviewed_section_ids=body.reviewed_plan.reviewed_section_ids,
            )
            if reviewed_artifact.get("projectName") != body.project_name:
                raise ProjectPlanReviewConflict(
                    "native project name does not match the reviewed plan"
                )
            holder_token = str(uuid.uuid4())
            store.acquire_target_operation_lease(
                operation_id=body.operation_id,
                owner_kind="native_project_create",
                holder_token=holder_token,
                target_identity={
                    "operationId": body.operation_id,
                    "workspaceParent": str(Path(body.workspace_parent).expanduser().resolve()),
                    "projectName": body.project_name,
                    "plcSeries": body.plc_series,
                    "cpuModel": body.cpu_model,
                    "programName": body.program_name,
                    "language": body.language,
                    "xg5000Pid": body.xg5000_pid,
                    "xg5000Hwnd": body.xg5000_hwnd,
                    "reviewedPlan": body.reviewed_plan.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                },
            )
            store.require_reviewed_project_plan(
                conversation_id=body.reviewed_plan.conversation_id,
                message_id=body.reviewed_plan.message_id,
                plan_digest=body.reviewed_plan.plan_digest,
                reviewed_section_ids=body.reviewed_plan.reviewed_section_ids,
            )
            operation, created = await native_project_service.create(
                spec,
                actor_id=actor_id,
            )
        except ProjectPlanReviewConflict as exc:
            if holder_token is not None:
                store.release_target_operation_lease(
                    operation_id=body.operation_id,
                    holder_token=holder_token,
                    reason="reviewed_project_plan_changed_before_execution",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except NativeProjectConflict as exc:
            if holder_token is not None:
                store.release_target_operation_lease(
                    operation_id=body.operation_id,
                    holder_token=holder_token,
                    reason="native_project_conflict_before_execution",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except NativeProjectPreconditionError as exc:
            if holder_token is not None:
                current = native_project_repository.get(body.operation_id)
                if current is not None and current.state in {
                    "EXECUTING",
                    "QUARANTINED",
                }:
                    store.park_target_operation_lease(
                        operation_id=body.operation_id,
                        holder_token=holder_token,
                        reason="native_project_outcome_requires_recovery",
                    )
                else:
                    store.release_target_operation_lease(
                        operation_id=body.operation_id,
                        holder_token=holder_token,
                        reason="native_project_precondition_failed",
                    )
            detail = str(exc)
            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if "MCP is not ready" in detail
                    else status.HTTP_409_CONFLICT
                ),
                detail=detail,
            ) from exc
        except BaseException:
            if holder_token is not None:
                store.park_target_operation_lease(
                    operation_id=body.operation_id,
                    holder_token=holder_token,
                    reason="native_project_request_interrupted",
                )
            raise

        assert holder_token is not None
        if operation.state in {"COMPLETED", "FAILED"}:
            store.release_target_operation_lease(
                operation_id=body.operation_id,
                holder_token=holder_token,
                reason=f"native_project_{operation.state.casefold()}",
            )
        else:
            store.park_target_operation_lease(
                operation_id=body.operation_id,
                holder_token=holder_token,
                reason="native_project_outcome_requires_recovery",
            )

        if not created:
            response.status_code = status.HTTP_200_OK
        if operation.state == "COMPLETED":
            # XG5000 now displays the newly created source project. The MCP
            # workspace capture uses a distinct working copy, so neither the
            # prior target nor the unproven copy may remain bound as active.
            active_engineering_session = None
            active_workspace = None
            execution_broker.clear_agent_session()
            store.set_host_state("active_workspace", None)
        store.append_event(
            event_type="native_project.operation",
            payload={
                "nativeProjectId": operation.id,
                "operationId": operation.operation_id,
                "state": operation.state.casefold(),
                "created": created,
                "identityVerified": operation.result.get("identityVerified"),
                "controllerConnected": operation.result.get("controllerConnected"),
                "plcWritePerformed": operation.result.get("plcWritePerformed"),
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(
                mode="json",
                by_alias=True,
            ),
        )
        return NativeProjectCreatedV1(
            operation=_native_project_operation_v1(operation),
            created=created,
        )

    @app.get(
        "/api/v1/native-projects/{native_project_id}",
        response_model=NativeProjectOperationV1,
    )
    async def native_project_status_v1(
        native_project_id: str,
    ) -> NativeProjectOperationV1:
        try:
            normalized_id = str(uuid.UUID(native_project_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="native project operation not found",
            ) from exc
        if normalized_id != native_project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="native project operation not found",
            )
        operation = native_project_repository.get(native_project_id)
        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="native project operation not found",
            )
        return _native_project_operation_v1(operation)

    @app.post(
        "/api/v1/native-projects/{native_project_id}/reconcile",
        response_model=NativeProjectReconciliationV1,
    )
    async def reconcile_native_project_v1(
        native_project_id: str,
        body: ReconcileNativeProjectRequestV1,
    ) -> NativeProjectReconciliationV1:
        operation = native_project_repository.get(native_project_id)
        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="native project operation not found",
            )
        if operation.state != "QUARANTINED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="only a quarantined native project can be reconciled",
            )
        request_identity = operation.request
        holder_token = str(uuid.uuid4())
        try:
            store.acquire_target_operation_lease(
                operation_id=operation.operation_id,
                owner_kind="native_project_create",
                holder_token=holder_token,
                target_identity={
                    "operationId": operation.operation_id,
                    "workspaceParent": request_identity["workspaceParent"],
                    "projectName": request_identity["projectName"],
                    "plcSeries": request_identity["plcSeries"],
                    "cpuModel": request_identity["cpuModel"],
                    "programName": request_identity["programName"],
                    "language": request_identity["language"],
                    "xg5000Pid": request_identity["xg5000Pid"],
                    "xg5000Hwnd": request_identity["xg5000Hwnd"],
                    **(
                        {"reviewedPlan": request_identity["reviewedPlan"]}
                        if isinstance(request_identity.get("reviewedPlan"), dict)
                        else {}
                    ),
                },
            )
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            resolved = await native_project_service.reconcile(
                operation.operation_id,
                NativeProjectRecoveryEvidence(
                    pid=body.pid,
                    hwnd=body.hwnd,
                    expected_project_path=Path(body.expected_project_path),
                    expected_project_sha256=body.expected_project_sha256,
                ),
            )
        except NativeProjectConflict as exc:
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_recovery_conflict",
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BaseException:
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_recovery_interrupted",
            )
            raise

        if resolved.state in {"COMPLETED", "FAILED"}:
            store.release_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason=f"native_project_reconciled_{resolved.state.casefold()}",
            )
        else:
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_recovery_still_uncertain",
            )
        store.append_event(
            event_type="native_project.reconciled",
            payload={
                "nativeProjectId": resolved.id,
                "operationId": resolved.operation_id,
                "state": resolved.state.casefold(),
                "identityVerified": resolved.result.get("identityVerified"),
            },
        )
        return NativeProjectReconciliationV1(
            operation=_native_project_operation_v1(resolved),
            reconciled=resolved.state != "QUARANTINED",
        )

    @app.post(
        "/api/v1/xg5000/attach",
        response_model=AttachXG5000ResponseV1,
    )
    async def attach_xg5000_v1(
        body: AttachXG5000RequestV1,
        _target_lease: None = Depends(target_switch_lease),
    ) -> AttachXG5000ResponseV1:
        working_copy = (
            active_workspace.get("working_copy")
            if isinstance(active_workspace, dict)
            and isinstance(active_workspace.get("working_copy"), dict)
            else None
        )
        arguments: dict[str, Any] = {
            "pid": body.pid,
            "hwnd": body.hwnd,
            "access_profile": "analysis",
        }
        if body.project_path:
            arguments["project_path"] = body.project_path
        elif working_copy and isinstance(working_copy.get("path"), str):
            arguments["project_path"] = working_copy["path"]
        if body.project_hash:
            arguments["project_hash"] = body.project_hash
        elif working_copy and isinstance(working_copy.get("current_sha256"), str):
            arguments["project_hash"] = working_copy["current_sha256"]
        try:
            result = await mcp.call_read_tool("xg5000.attach", arguments)
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "XG5000 분석 세션을 검증하지 못했습니다. 대상 창과 "
                    "프로젝트 정체성을 다시 확인해 주세요."
                ),
            ) from exc
        session = bind_engineering_session(result)
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(
                mode="json",
                by_alias=True,
            ),
        )
        return AttachXG5000ResponseV1(session=session)

    @app.get("/service-worker.js", include_in_schema=False)
    @app.get("/sw.js", include_in_schema=False)
    async def reject_service_worker() -> Response:
        raise HTTPException(status_code=404, detail="service workers are disabled")

    static_root = config.static_dir
    static_index = static_root / "index.html"
    if static_index.is_file():

        @app.get("/{asset_path:path}", include_in_schema=False)
        async def serve_web_asset(asset_path: str) -> FileResponse:
            normalized_asset_path = asset_path.strip("/")
            if normalized_asset_path == "api" or normalized_asset_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            if normalized_asset_path in SERVICE_WORKER_PATHS:
                raise HTTPException(status_code=404, detail="service workers are disabled")

            candidate = (static_root / normalized_asset_path).resolve()
            try:
                candidate.relative_to(static_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="asset not found") from exc

            target: Path
            if normalized_asset_path and candidate.is_file():
                target = candidate
            else:
                target = static_index
            return FileResponse(target, headers={"Cache-Control": "no-store"})
    else:

        @app.get("/", include_in_schema=False)
        async def web_ui_unavailable() -> JSONResponse:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "ControlStudio web build is not installed"},
                headers={"Cache-Control": "no-store"},
            )

    return app
