from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .agent_client import (
    AgentSupervisor,
    SubprocessAgentClient,
    build_historical_evidence_bundle,
    project_historical_evidence,
)
from .agent_protocol import AgentProtocolError, scrub_agent_tool_result
from .agent_runtime import (
    MAX_ENGINEERING_AUTONOMOUS_CYCLES,
    AgentOutputContractDiagnostic,
    AgentOutputContractError,
    AgentRuntimeDiagnostic,
    AgentRuntimeError,
    ModelCapabilityResult,
)
from .api_models import (
    AgentStatusV1,
    AttachXG5000RequestV1,
    AttachXG5000ResponseV1,
    PrepareOfflineCandidateSessionRequestV1,
    PrepareOfflineCandidateSessionResponseV1,
    BootstrapV1,
    BrowserSessionV1,
    ChangeApprovalV1,
    ChangeDecisionRequestV1,
    ChangeDecisionResponseV1,
    ChangeExecutionResponseV1,
    ChangePlanContract,
    ChangePlanV1,
    ChangePlanV2,
    ChangePlanV3,
    ChangePlanV4,
    ChangePlanV5,
    ChangePlanV6,
    ChangeReceiptAcknowledgementV1,
    ChangeReconciliationResponseV1,
    ChangeTargetFocusV1,
    ChangeTargetIdentityV1,
    ChangeTransactionCreatedV1,
    ChangeTransactionV1,
    CloseIdleXG5000InstanceRequestV1,
    CloseIdleXG5000InstanceResponseV1,
    CloseIdleXG5000InstanceAttentionV1,
    ConfirmKnowledgeMemoryRequestV1,
    ConnectLSControllerRequestV1,
    ControllerRestoreBeginRequestV1,
    ControllerRestoreCompletedV1,
    ControllerRestoreCompleteRequestV1,
    ControllerRestoreHandoffV1,
    ControllerRestorePreflightRequestV1,
    ControllerRestorePreflightV1,
    ConversationCreateV1,
    ConversationMessageV1,
    ConversationPageV1,
    ConversationSummaryV1,
    ConversationV1,
    CreateChangeTransactionRequestV1,
    CreateKnowledgeMemoryRequestV1,
    CreateNativeProjectRequestV1,
    EngineeringAnalysisArtifactV1,
    EngineeringConversationHandoffV1,
    EngineeringLocationFocusRequestV1,
    EngineeringLocationFocusV1,
    EngineeringLocationV2,
    EngineeringMissionBlockedStepReverifyRequestV1,
    EngineeringMissionResumeRequestV1,
    EngineeringMissionSnapshotV1,
    EngineeringMissionSummaryV1,
    EngineeringMissionV1,
    EngineeringProgramFocusV1,
    EngineeringProgramsV1,
    EngineeringProgramV1,
    EngineeringSystemStatusV1,
    EngineeringTargetBindingV1,
    ExecuteChangeTransactionRequestV1,
    FinalizeSimulationChangeRequestV1,
    HostRestartResponseV1,
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
    LauncherReadinessV1,
    LaunchIdleXG5000AttentionV1,
    LaunchIdleXG5000RequestV1,
    LaunchIdleXG5000ResponseV1,
    LocalWorkspaceCompareV1,
    LocalWorkspaceV1,
    LSControllerConnectionStatusV1,
    LSControllerEndpointV1,
    LSControllerObservationV1,
    LSControllerReadV1,
    LSControllerTraceV1,
    MCPStatusV1,
    MneAnchorFocusV1,
    MneFullProgramBaselineBindingV2,
    MneInstructionBatchPatchCandidateV5,
    MneInstructionPatchCandidateV4,
    MNE_COMPILER_CANDIDATE_REQUIRED_COVERAGE_AREAS,
    MneRungPatchCandidateV6,
    MneSeriesGuardCandidateV3,
    ModelCapabilityFlagsV1,
    ModelCapabilityResultV1,
    ModelSettingsV1,
    NativeProjectCreatedV1,
    NativeProjectOperationV1,
    NativeProjectReconciliationV1,
    NativeProjectResumedV1,
    NativeSimulationTrialDecisionRequestV1,
    NativeSimulationTrialDecisionV1,
    NewBuildWorkspaceProvenanceV1,
    OfflineOperandMissionV1,
    OpenLocalWorkspaceRequestV1,
    OpenLocalWorkspaceResponseV1,
    ProductFeatureStatusV1,
    ProjectBriefV1,
    ProjectPlanArtifactV1,
    ProjectPlanReviewRequestV1,
    ProjectPlanReviewResponseV1,
    ProjectPlanReviewV1,
    ProjectPlanRevisionDeltaV1,
    ReadLSControllerRequestV1,
    ReconcileChangeTransactionRequestV1,
    ReconcileNativeProjectRequestV1,
    ResumeNativeProjectRequestV1,
    ResumeLocalWorkspaceRequestV1,
    ReviewedNoSaveCloseRequestV1,
    ReviewedNoSaveCloseResponseV1,
    RunCancellationV1,
    SaveModelSettingsRequestV1,
    SendMessageRequestV1,
    SendMessageResponseV1,
    ServiceStatusV1,
    TraceLSControllerRequestV1,
    WorkspaceSummaryV1,
    XG5000InstancesV1,
    XG5000ProjectFileSelectionV1,
    XG5000ProjectLaunchV1,
    canonical_engineering_mission_snapshot,
    is_hardware_free_m_operand_target,
)
from .change_execution import (
    ChangeExecutionFailedSafely,
    ChangeExecutionOutcomeUncertain,
    ChangeExecutionPreconditionError,
    ChangeExecutionService,
    ChangeRecoveryTarget,
    calculate_plan_digest,
    validated_terminal_ld_reopen,
)
from .change_outcome import (
    TERMINAL_CHANGE_OUTCOME_DISPOSITIONS,
    change_outcome_evidence,
    change_outcome_projection,
)
from .config import HostConfig
from .control_patterns import iter_builtin_knowledge_documents
from .database_upgrade import protect_database_upgrades
from .execution_broker import (
    ExecutionBroker,
    MCPExecutionBroker,
    validate_workspace_compare_result,
)
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
from .mcp_client import AGENT_TOOL_NAMES, MCPGateway, MCPStartupError, MCPToolError
from .mne_change_execution import (
    MNE_PATCH_PLAN_CONTRACTS,
    MNE_STRUCTURAL_PLAN_KINDS,
    CompositeChangeExecutionService,
    validated_terminal_mne_reopen,
)
from .model_connection import (
    MODEL_PROTOCOL,
    ModelConnectionRepository,
    ModelSecrets,
    ModelSettingsError,
    StoredModelConnection,
    StoredModelSettingsError,
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
    ChangeRiskLevel,
    ChangeTargetIdentity,
    ChangeTransaction,
    ChangeTransactionState,
    Conversation,
    ConversationDetail,
    EngineeringMission,
    HealthResponse,
    Message,
    MessageRole,
    RunState,
    StoredEvent,
)
from .native_engineering_status import derive_native_engineering_status
from .native_projects import (
    NativeProjectConflict,
    NativeProjectOperation,
    NativeProjectPreconditionError,
    NativeProjectRecoveryEvidence,
    NativeProjectRepository,
    NativeProjectService,
    NativeProjectSpec,
    _native_simulation_trial_id,
    validated_new_build_workspace_provenance,
)
from .native_session_identity import (
    fresh_native_trial_identity,
    native_trial_session_identity,
)
from .project_planning import (
    build_project_plan_revision,
    calculate_project_brief_digest,
    calculate_project_plan_attachment_revision_digest,
    latest_project_plan_snapshot,
    project_plan_review_item_ids,
    validate_project_plan_artifact,
    validate_project_plan_scope_evidence,
)
from .restart import RestartBootstrapError, RestartHandoff, consume_restart_handoff
from .ssh_handoff import SshInteractiveHandoffJournal
from .store import (
    ChangeStoreConflict,
    ConversationActiveRunConflict,
    ConversationWorkflowConflict,
    EngineeringMissionConflict,
    HostStore,
    MutationLeaseConflict,
    ProjectPlanReviewConflict,
    TargetOperationLeaseConflict,
)
from .structural_evidence import (
    AMBIGUOUS_STRUCTURAL_SCOPE_LIMITATION,
    STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA,
    STRUCTURAL_ANALYSIS_TOOL_COVERAGE,
    location_membership_value,
    relation_membership_value,
    structural_observation_statement,
    structural_receipt_tool_name,
    structure_membership_value,
    validate_project_search_context,
    validate_structural_evidence_contract,
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
# A consumed handoff needs enough time for the browser to request the initial
# bootstrap, while remaining short-lived and strictly in-memory.
BROWSER_READINESS_TTL = timedelta(minutes=2)
LATE_WORKSPACE_RECONCILE_SECONDS = 15.0
LAUNCH_CHALLENGE_PREFIX = b"controlstudio-launcher-v1\0"
BROWSER_CSRF_PREFIX = b"controlstudio-browser-csrf-v1\0"
_PROJECT_BRIEF_ATTACHMENT_KINDS = frozenset(
    {
        KnowledgeKind.MANUAL,
        KnowledgeKind.STANDARD,
        KnowledgeKind.PROJECT_DOCUMENT,
    }
)
logger = logging.getLogger(__name__)


_WORKSPACE_CLI_FAILURE_DETAIL = re.compile(
    r"workspace_cli_failure\(kind=([A-Za-z][A-Za-z0-9_.-]{0,79}),message=([^()]{1,480})\)"
)


def _cli_failure_diagnostic(error: Exception) -> tuple[str, str] | None:
    """Return only the adapter-validated CLI diagnostic, if present."""

    match = _WORKSPACE_CLI_FAILURE_DETAIL.search(str(error))
    if match is None:
        return None
    kind, message = match.groups()
    # Adapter accepts ASCII single-line safe text only. Keep this second
    # validation at the Host trust boundary in case an MCP server is replaced.
    if (
        not message
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in message)
        or any(character in message for character in ("\\", "/", ":", "=", "\"", "'", "{", "}"))
    ):
        return None
    return kind, message


def _workspace_cli_failure_detail(error: Exception) -> str:
    """Expose a validated workspace CLI reason, never arbitrary MCP text."""

    diagnostic = _cli_failure_diagnostic(error)
    if diagnostic is None:
        return "the verified workspace could not be reopened in XG5000 before acceptance"
    kind, message = diagnostic
    return f"the verified workspace could not be reopened in XG5000 before acceptance: {kind}: {message}"


def _reviewed_no_save_close_failure_detail(error: MCPToolError) -> str:
    """Expose only the adapter's bounded reviewed-close failure envelope."""

    diagnostic = _cli_failure_diagnostic(error)
    if diagnostic is None:
        return (
            "저장 없이 닫기 검토를 완료하지 못했습니다. 대상 창은 계속 선택되어 "
            "있습니다."
        )
    kind, message = diagnostic
    return (
        "저장 없이 닫기 검토를 완료하지 못했습니다: "
        f"{kind}: {message}. 대상 창은 계속 선택되어 있습니다."
    )


def _reviewed_no_save_close_response(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
) -> ReviewedNoSaveCloseResponseV1:
    """Project a verified CLI receipt onto the intentionally small Host response."""

    return ReviewedNoSaveCloseResponseV1.model_validate(
        {
            field: raw.get(field)
            for field in (
                "operation",
                "verified",
                "source_closed",
                "automatic_workspace_open",
                "process_terminated",
                "reviewed_binding",
                "close",
                "next_step",
            )
        }
        | {"receipt_digest": receipt_digest}
    )


def _close_idle_xg5000_instance_response(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
) -> CloseIdleXG5000InstanceResponseV1:
    """Project the exact normal-close receipt; save-prompt outcomes are not success."""

    return CloseIdleXG5000InstanceResponseV1.model_validate(
        {
            field: raw.get(field)
            for field in (
                "operation",
                "verified",
                "status",
                "idle_binding",
                "close_action",
                "prompt_observed",
                "modal_observed",
                "no_save_prompt_clicked",
                "process_exited",
                "process_terminated",
            )
        }
        | {"receipt_digest": receipt_digest}
    )


def _close_idle_xg5000_instance_attention(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
) -> CloseIdleXG5000InstanceAttentionV1:
    """Project a bounded no-close result without treating it as a completion."""

    return CloseIdleXG5000InstanceAttentionV1.model_validate(
        {
            field: raw.get(field)
            for field in (
                "operation",
                "verified",
                "status",
                "idle_binding",
                "close_action",
                "prompt_observed",
                "modal_observed",
                "attention_reason",
                "no_save_prompt_clicked",
                "process_exited",
                "process_terminated",
            )
        }
        | {"receipt_digest": receipt_digest}
    )


def _launch_idle_xg5000_response(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
) -> LaunchIdleXG5000ResponseV1:
    return LaunchIdleXG5000ResponseV1.model_validate(
        {
            field: raw.get(field)
            for field in (
                "operation",
                "verified",
                "status",
                "launch_action",
                "launched_identity",
                "idle_binding",
                "no_existing_instances",
                "blank_window",
                "modal_observed",
                "attention_reason",
                "process_exited",
                "process_terminated",
            )
        }
        | {"receipt_digest": receipt_digest}
    )


def _launch_idle_xg5000_attention(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
) -> LaunchIdleXG5000AttentionV1:
    return LaunchIdleXG5000AttentionV1.model_validate(
        {
            field: raw.get(field)
            for field in (
                "operation",
                "verified",
                "status",
                "launch_action",
                "launched_identity",
                "idle_binding",
                "no_existing_instances",
                "blank_window",
                "modal_observed",
                "attention_reason",
                "process_exited",
                "process_terminated",
            )
        }
        | {"receipt_digest": receipt_digest}
    )


def _validate_idle_xg5000_binding(binding: dict[str, Any]) -> None:
    if (
        not isinstance(binding.get("pid"), int)
        or isinstance(binding.get("pid"), bool)
        or binding["pid"] <= 0
        or not isinstance(binding.get("hwnd"), int)
        or isinstance(binding.get("hwnd"), bool)
        or binding["hwnd"] <= 0
        or not isinstance(binding.get("process_create_time"), (int, float))
        or isinstance(binding.get("process_create_time"), bool)
        or float(binding["process_create_time"]) <= 0
        or not isinstance(binding.get("executable"), str)
        or not binding["executable"]
    ):
        raise ValueError("MCP idle XG5000 receipt has an invalid exact identity binding")


def _validate_launched_xg5000_identity(identity: dict[str, Any]) -> None:
    if (
        not isinstance(identity.get("pid"), int)
        or isinstance(identity.get("pid"), bool)
        or identity["pid"] <= 0
        or not isinstance(identity.get("process_create_time"), (int, float))
        or isinstance(identity.get("process_create_time"), bool)
        or float(identity["process_create_time"]) <= 0
        or not isinstance(identity.get("executable"), str)
        or not identity["executable"]
    ):
        raise ValueError("MCP idle XG5000 receipt has an invalid launched identity")


@dataclass(frozen=True)
class _BrowserHandoffTicket:
    expires_at: datetime
    readiness_receipt: str


@dataclass
class _BrowserReadinessReceipt:
    expires_at: datetime
    session_established: bool = False
    bootstrap_completed: bool = False

_AGENT_TOOL_EVIDENCE: dict[str, tuple[str, str, str, str]] = {
    "xg5000.session_status": (
        "validation",
        "XG5000 세션 상태",
        "현재 Host에 결합된 XG5000 읽기 세션 상태를 확인했습니다.",
        "confirmed",
    ),
    "sim.target_status": (
        "validation",
        "XG-SIM 대상 상태",
        "선택된 XG5000 상태 표시줄을 읽기 전용으로 확인했습니다.",
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
    "project.hardware_context": (
        "artifact",
        "프로젝트 하드웨어 컨텍스트",
        "현재 XG5000 프로젝트에서 CPU·타이머 영역·I/O 모듈 근거를 읽었습니다.",
        "confirmed",
    ),
    "workspace.compare": (
        "artifact",
        "Workspace source comparison",
        "Compared the immutable source artifact with the current working copy.",
        "confirmed",
    ),
    "workspace.hardware_context": (
        "artifact",
        "Workspace hardware context",
        "Observed CPU, timer-area, and I/O configuration from the bound working copy.",
        "confirmed",
    ),
    "workspace.structure_index": (
        "artifact",
        "Workspace file structure index",
        "Observed a bounded SHA-bound structure inventory from the selected working copy; it is not live runtime, wiring, or PLC evidence.",
        "supported",
    ),
    "workspace.search": (
        "program_location",
        "Workspace literal search",
        "Located bounded literal matches in the selected working copy; it is not live ladder, runtime, PLC, or change evidence.",
        "supported",
    ),
    "project.structure_index": (
        "artifact",
        "XG5000 project structure index",
        "Observed a bounded, digest-bound inventory of project configuration, tasks, code units, symbols, calls, modules, and communications.",
        "supported",
    ),
    "project.search": (
        "program_location",
        "Current project search",
        "Located literal program, symbol, comment, device, and LD-record matches in the current project.",
        "confirmed",
    ),
    "program.change_target_analysis": (
        "artifact",
        "변경 대상 네이티브 분석",
        "정확한 값 토큰을 network·rung·값 의미·인과 경로·물리 출력 후보와 결속했습니다.",
        "confirmed",
    ),
    "program.compile_mne_series_guard_candidate": (
        "artifact",
        "MNE series guard candidate",
        "Compiled a bounded, digest-only structural guard candidate for one exact OUT instruction.",
        "confirmed",
    ),
    "program.compile_mne_instruction_patch_candidate": (
        "artifact",
        "MNE instruction patch candidate",
        "Compiled one bounded instruction edit from the private program baseline.",
        "confirmed",
    ),
    "program.compile_mne_instruction_batch_patch_candidate": (
        "artifact",
        "MNE instruction batch patch candidate",
        "Compiled 2–16 related instruction edits from one private program baseline.",
        "confirmed",
    ),
    "program.compile_mne_rung_patch_candidate": (
        "artifact",
        "LD rung 변경 후보",
        "현재 프로그램을 기준으로 승인 검토용 rung 변경 후보를 컴파일했습니다.",
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
    "device.causal_impact": (
        "device_reference",
        "디바이스 인과·영향 경로",
        "현재 프로젝트에서 요청한 디바이스의 상류 원인과 하류 영향을 다단계로 추적했습니다.",
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
    "controller.ls.trace": (
        "runtime_observation",
        "LS PLC 디바이스 변화 추적",
        "선택한 LS PLC 디바이스의 시간별 값과 전이를 읽기 전용으로 관찰했습니다.",
        "confirmed",
    ),
}
_STATIC_WORKSPACE_EVIDENCE_TOOLS = frozenset(
    {"workspace.structure_index", "workspace.search"}
)

_CHANGE_COVERAGE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "target_identity": frozenset(
        {
            "xg5000.session_status",
            "program.read_ld",
            "project.hardware_context",
            "program.change_target_analysis",
            "project.snapshot",
        }
    ),
    "value_semantics": frozenset(
        {
            "program.read_ld",
            "project.hardware_context",
            "program.change_target_analysis",
        }
    ),
    "direct_impact": frozenset(
        {
            "device.causal_impact",
            "device.references",
            "program.logic_graph",
            "project.hardware_context",
            "program.change_target_analysis",
            "project.behavior_report",
        }
    ),
    "indirect_impact": frozenset(
        {
            "device.causal_impact",
            "project.hardware_context",
            "program.change_target_analysis",
        }
    ),
    "modes": frozenset({"program.read_ld", "program.state_model", "project.behavior_report"}),
    "runtime": frozenset(
        {"controller.ls.status", "controller.ls.read", "controller.ls.trace"}
    ),
    "external_integrations": frozenset({"project.behavior_report"}),
    "safety": frozenset(
        {
            "device.causal_impact",
            "program.read_ld",
            "project.hardware_context",
            "program.change_target_analysis",
            "program.logic_graph",
            "program.state_model",
        }
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
_MNE_FULL_PROGRAM_REQUIRED_EVIDENCE_TOOLS = frozenset(
    {
        "project.structure_index",
        "program.logic_graph",
        "program.state_model",
        "device.references",
        "device.causal_impact",
        "project.behavior_report",
        "project.hardware_context",
    }
)
_MNE_COVERAGE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "target_identity": frozenset(
        {
            "project.structure_index",
            "program.logic_graph",
            "program.state_model",
            "project.hardware_context",
        }
    ),
    "value_semantics": frozenset(
        {
            "program.logic_graph",
            "program.state_model",
            "project.behavior_report",
            "project.hardware_context",
        }
    ),
    "direct_impact": frozenset(
        {
            "program.logic_graph",
            "device.references",
            "device.causal_impact",
            "project.behavior_report",
            "project.hardware_context",
        }
    ),
    "indirect_impact": frozenset(
        {
            "project.structure_index",
            "device.references",
            "device.causal_impact",
            "project.behavior_report",
        }
    ),
    "modes": frozenset({"program.state_model", "project.behavior_report"}),
    "runtime": frozenset(
        {
            "project.behavior_report",
            "controller.ls.status",
            "controller.ls.read",
            "controller.ls.trace",
        }
    ),
    "external_integrations": frozenset(
        {
            "project.structure_index",
            "project.behavior_report",
            "project.hardware_context",
        }
    ),
    "safety": frozenset(
        {
            "program.logic_graph",
            "program.state_model",
            "device.references",
            "device.causal_impact",
            "project.behavior_report",
            "project.hardware_context",
        }
    ),
    "validation": frozenset(
        {
            "xg5000.session_status",
            "project.structure_index",
            "program.logic_graph",
            "program.state_model",
            "project.hardware_context",
            "project.snapshot",
        }
    ),
    "rollback": frozenset(
        {
            "project.structure_index",
            "project.hardware_context",
            "project.snapshot",
        }
    ),
}

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
    evidence_contract: dict[str, Any] | None = None,
    evidence_context: dict[str, Any] | None = None,
    simulator_target_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    canonical_tool_name = AGENT_TOOL_NAMES.get(tool_name, tool_name)
    definition = _AGENT_TOOL_EVIDENCE.get(canonical_tool_name)
    if definition is None:
        return None
    kind, title, summary, confidence = definition
    call_identity = tool_call_id or str(uuid.uuid4())
    evidence_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "controlstudio-agent-evidence:"
                f"{conversation_id}:{run_id}:{canonical_tool_name}:{call_identity}"
            ),
        )
    )
    evidence = {
        "evidenceId": evidence_id,
        "title": title,
        "kind": kind,
        "toolName": canonical_tool_name,
        "summary": summary,
        "confidence": confidence,
    }
    if tool_call_id is not None:
        evidence["toolCallId"] = tool_call_id
    if canonical_tool_name == "sim.target_status" and isinstance(
        simulator_target_status, dict
    ):
        if simulator_target_status == {
            "verified": True,
            "classification": "SIMULATOR_TARGET",
        }:
            evidence["summary"] = (
                "선택된 XG5000 상태 표시줄에서 XG-SIM 대상을 검증했습니다. "
                "이 관찰은 읽기 전용입니다."
            )
        elif simulator_target_status == {
            "verified": False,
            "classification": "NOT_SIMULATOR_TARGET",
        }:
            evidence["summary"] = (
                "선택된 XG5000 상태 표시줄을 읽었지만 XG-SIM 대상으로 검증되지는 "
                "않았습니다. 이는 XG-SIM offline 판정이 아닙니다."
            )
    receipt_schema = {
        "program.read_ld": "controlstudio-program-read-ld-evidence/v1",
        "project.hardware_context": "controlstudio-hardware-context-evidence/v1",
        "workspace.compare": "controlstudio-workspace-compare-evidence/v1",
        "workspace.hardware_context": (
            "controlstudio-workspace-hardware-context-evidence/v1"
        ),
        "program.change_target_analysis": "controlstudio-change-target-evidence/v2",
        "program.compile_mne_series_guard_candidate": (
            "controlstudio-mne-series-guard-candidate-evidence/v1"
        ),
        "program.compile_mne_instruction_patch_candidate": (
            "controlstudio-mne-instruction-patch-candidate-evidence/v1"
        ),
        "program.compile_mne_instruction_batch_patch_candidate": (
            "controlstudio-mne-instruction-batch-patch-candidate-evidence/v1"
        ),
        "program.compile_mne_rung_patch_candidate": (
            "controlstudio-mne-rung-patch-candidate-evidence/v1"
        ),
        **{
            tool_name: STRUCTURAL_ANALYSIS_EVIDENCE_SCHEMA
            for tool_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE
        },
    }.get(canonical_tool_name)
    if receipt_schema is not None:
        if not isinstance(evidence_contract, dict):
            raise ValueError(f"{canonical_tool_name} completed without its evidence contract")
        canonical_contract = dict(evidence_contract)
        receipt_digest = canonical_contract.pop("receiptDigest", None)
        actual_digest = hashlib.sha256(
            json.dumps(
                canonical_contract,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if (
            evidence_contract.get("schema") != receipt_schema
            or not isinstance(receipt_digest, str)
            or receipt_digest != actual_digest
        ):
            raise ValueError(f"{canonical_tool_name} evidence contract digest is invalid")
        evidence["receipt"] = evidence_contract
    if canonical_tool_name in {"project.search", "workspace.search"}:
        if not isinstance(evidence_context, dict) or not isinstance(
            evidence_contract,
            dict,
        ):
            raise ValueError(
                f"{canonical_tool_name} completed without its model context"
            )
        canonical_context = validate_project_search_context(
            evidence_context,
            receipt=evidence_contract,
        )
        evidence["modelContext"] = canonical_context
        evidence["modelContextDigest"] = hashlib.sha256(
            json.dumps(
                canonical_context,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    elif evidence_context is not None:
        raise ValueError(f"{canonical_tool_name} returned an unexpected model context")
    return evidence


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
        "knowledge_ref",
        "chunk_id",
        "source_id",
        "content",
        "title",
        "kind",
        "scope",
        "authority",
    )
    if any(not isinstance(value.get(field), str) or not value[field] for field in required_text):
        raise ValueError("Agent knowledge result is missing authoritative citation fields")
    if not value["knowledge_ref"].startswith(f"knowledge://{value['source_id']}"):
        raise ValueError("Agent knowledge result citation is not canonical")
    optional_text = ("vendor", "product_family", "version", "language", "locator", "section")
    if any(
        value.get(field) is not None and not isinstance(value.get(field), str)
        for field in optional_text
    ):
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
            (
                "사용자가 첨부한 자료 ID: "
                + (", ".join(brief.attachment_source_ids) or _LEGACY_UNKNOWN_VALUE)
            ),
            "",
            _LEGACY_PROJECT_BRIEF_FOOTER,
        )
    )


def _require_offline_operand_mission_plan(
    mission: dict[str, Any], values: dict[str, Any]
) -> None:
    """Bind an Agent legacy operand proposal to the typed Host mission seed."""
    target = values.get("target")
    current = values.get("current")
    proposed = values.get("proposed")
    if (
        not isinstance(target, dict)
        or target.get("programId", target.get("program_id")) != mission["programId"]
        or target.get("programName", target.get("program_name")) != mission["programName"]
        or not isinstance(target.get("tokenOffset", target.get("token_offset")), int)
        or not isinstance(current, dict)
        or current.get("value", current.get("operand")) != mission["expectedOperand"]
        or not isinstance(proposed, dict)
        or proposed.get("value", proposed.get("operand")) != mission["replacementOperand"]
    ):
        raise ValueError("proposal differs from the Host-owned offline operand mission")


def _rejected_exact_operand_reauthorization_permitted(
    *,
    request_message: Message | None,
    linked_change: ChangeTransaction | None,
    change_outcome: dict[str, Any] | None,
    offline_operand_mission: dict[str, Any] | None,
) -> bool:
    """Accept a rejected retry only from the same explicit typed user intent."""

    if (
        request_message is None
        or request_message.role is not MessageRole.USER
        or request_message.metadata.get("engineering_mission_continue") is True
        or linked_change is None
        or linked_change.state is not ChangeTransactionState.REJECTED
        or not isinstance(change_outcome, dict)
        or change_outcome.get("state") != "REJECTED"
        or change_outcome.get("disposition") != "rejected"
        or change_outcome.get("transactionId") != linked_change.id
        or change_outcome.get("planDigest") != linked_change.plan_digest
        or not isinstance(offline_operand_mission, dict)
    ):
        return False
    try:
        reauthorization = OfflineOperandMissionV1.model_validate(
            offline_operand_mission
        ).model_dump(mode="json", by_alias=True)
    except (TypeError, ValueError):
        return False
    plan = linked_change.plan
    target = plan.get("target") if isinstance(plan, dict) else None
    current = plan.get("current") if isinstance(plan, dict) else None
    proposed = plan.get("proposed") if isinstance(plan, dict) else None
    if not all(isinstance(value, dict) for value in (target, current, proposed)):
        return False
    assert isinstance(target, dict) and isinstance(current, dict) and isinstance(proposed, dict)
    plan_kind = plan.get("planKind", plan.get("plan_kind"))
    plan_program_name = target.get("programName", target.get("program_name"))
    legacy_program_name_omitted = (
        plan_kind is None
        and "programName" not in target
        and "program_name" not in target
    )
    return (
        target.get("programId", target.get("program_id"))
        == reauthorization["programId"]
        # Historical ChangePlanV1 transactions did not require programName in
        # target. The exact programId and operand pair still bind that rejected
        # decision; the fresh typed mission supplies the name, and every new
        # proposal is separately checked by _require_offline_operand_mission_plan.
        and (
            plan_program_name == reauthorization["programName"]
            or legacy_program_name_omitted
        )
        and current.get("value", current.get("operand"))
        == reauthorization["expectedOperand"]
        and proposed.get("value", proposed.get("operand"))
        == reauthorization["replacementOperand"]
    )


def _expired_exact_operand_reproposal_callback_permitted(
    mission: dict[str, Any] | None,
    change_outcome: dict[str, Any] | None,
    *,
    rejected_reauthorized: bool = False,
) -> bool:
    """Permit a fresh approval-only retry after one safely terminal authority.

    This does not revive the old transaction.  It merely makes the proposal
    callback available to the Agent's exact M/P-operand route, which still must
    supply new run-local target/snapshot evidence (and hardware evidence when
    the target requires it) and is limited to one Host transaction by
    ``proposed_change_authority``. Besides expiration, the
    Host admits only two narrow terminal outcomes: ``native_change_not_applied``
    (which is emitted before native mutation can be accepted as applied), and
    ``explicit_recovery_rolled_back`` with its Host verification digest. The
    latter proves the uncertain editor state was restored and saved. A rejected
    proposal is admitted only after a new user-authored, typed operand mission
    explicitly reauthorizes the exact request. Every uncertain/quarantined or
    otherwise terminal outcome remains closed.
    """

    retryable_outcome = bool(
        isinstance(change_outcome, dict)
        and (
            (
                change_outcome.get("state") == "EXPIRED"
                and change_outcome.get("disposition") == "expired"
            )
            or (
                rejected_reauthorized
                and change_outcome.get("state") == "REJECTED"
                and change_outcome.get("disposition") == "rejected"
            )
            or (
                change_outcome.get("state") == "FAILED"
                and change_outcome.get("disposition") == "failed"
                and change_outcome.get("errorCode") == "native_change_not_applied"
            )
            or (
                change_outcome.get("state") == "ROLLED_BACK"
                and change_outcome.get("disposition") == "rolled_back"
                and change_outcome.get("errorCode") == "explicit_recovery_rolled_back"
                and isinstance(change_outcome.get("verificationEvidenceDigest"), str)
                and re.fullmatch(
                    r"[0-9a-f]{64}",
                    change_outcome["verificationEvidenceDigest"],
                )
                is not None
            )
        )
    )
    if (
        not isinstance(mission, dict)
        or not retryable_outcome
        or not isinstance(mission.get("objective"), str)
        or not isinstance(mission.get("steps"), list)
    ):
        return False
    operands = list(
        dict.fromkeys(
            re.findall(
                r"(?<![A-Z0-9])[MP][0-9A-F]{4,5}(?![A-Z0-9])",
                mission["objective"].upper(),
            )
        )
    )
    if (
        len(operands) != 2
        or len(operands[0].encode("utf-16-le"))
        != len(operands[1].encode("utf-16-le"))
    ):
        return False
    proposal_steps = [
        step
        for step in mission["steps"]
        if isinstance(step, dict)
        and "propose_offline_change" in str(step.get("title", ""))
    ]
    hardware_steps = [
        step
        for step in mission["steps"]
        if isinstance(step, dict)
        and "inspect_project_hardware_context" in str(step.get("title", ""))
    ]
    target_steps = [
        step
        for step in mission["steps"]
        if isinstance(step, dict)
        and "analyze_change_target" in str(step.get("title", ""))
    ]
    if not (
        len(proposal_steps) == len(hardware_steps) == len(target_steps) == 1
    ):
        return False
    proposal_step = proposal_steps[0]
    completed_prerequisites = all(
        step.get("status") == "completed"
        and isinstance(step.get("evidenceRefs"), list)
        and bool(step["evidenceRefs"])
        for step in (*hardware_steps, *target_steps)
    )
    if not completed_prerequisites:
        return False
    # The Agent deterministically reactivates this exact clean checkpoint only
    # after it sees the expired terminal outcome.  The callback must therefore
    # be bound before that reactivation, but no live/pending/rejected authority
    # can enter through this branch.
    clean_terminal_wait = (
        mission.get("status") == "waiting_for_approval"
        and mission.get("stopReason") == "needs_human_approval"
        and mission.get("activeStepId") is None
        and proposal_step.get("status") == "pending"
    )
    active_retry = (
        mission.get("status") == "active"
        and mission.get("stopReason") == "continuing"
        and proposal_step.get("stepId") == mission.get("activeStepId")
        and proposal_step.get("status") == "active"
    )
    return clean_terminal_wait or active_retry


def _internal_mne_rung_reproposal_callback_permitted(
    mission: dict[str, Any] | None,
    change_outcome: dict[str, Any] | None,
    linked_change: ChangeTransaction | None,
) -> bool:
    """Admit one fresh internal-only MNE rung proposal after a safe terminal stop.

    The old authority remains terminal.  This narrow callback gate only lets
    the Agent compile the current editor baseline and create a new proposal
    for the exact same durable conversation after approval expiration, a proven
    pre-mutation failure, or a verified isolated-recovery rollback. Physical
    I/O, rejection, ordinary user rollback, uncertain failure, and every other
    terminal outcome stay outside this route.
    """

    if (
        not isinstance(mission, dict)
        or mission.get("status") not in {"waiting_for_approval", "active"}
        or not isinstance(change_outcome, dict)
        or linked_change is None
    ):
        return False
    retryable_terminal = (
        (
            change_outcome.get("state") == "EXPIRED"
            and change_outcome.get("disposition") == "expired"
            and linked_change.state is ChangeTransactionState.EXPIRED
        )
        or (
            change_outcome.get("state") == "FAILED"
            and change_outcome.get("disposition") == "failed"
            and change_outcome.get("errorCode") == "native_change_not_applied"
            and linked_change.state is ChangeTransactionState.FAILED
        )
        or (
            change_outcome.get("state") == "ROLLED_BACK"
            and change_outcome.get("disposition") == "rolled_back"
            and change_outcome.get("errorCode") == "explicit_mne_recovery_rolled_back"
            and isinstance(change_outcome.get("verificationEvidenceDigest"), str)
            and re.fullmatch(
                r"[0-9a-f]{64}",
                change_outcome["verificationEvidenceDigest"],
            )
            is not None
            and linked_change.state is ChangeTransactionState.ROLLED_BACK
        )
    )
    if not retryable_terminal:
        return False
    plan = linked_change.plan
    if not isinstance(plan, dict) or (
        plan.get("plan_kind", plan.get("planKind")) != "mne_rung_patch"
        or plan.get("change_mode", plan.get("changeMode"))
        != "offline_working_copy_revision"
        or plan.get("request_scope", plan.get("requestScope"))
        != "offline_candidate_revision"
        or plan.get("verification_scope", plan.get("verificationScope"))
        != "offline_native_reopen_xgsim"
    ):
        return False
    candidate = plan.get("candidate")
    if not isinstance(candidate, dict) or any(
        key in candidate
        for key in (
            "field_io_bindings",
            "fieldIoBindings",
            "hardware_context_digest",
            "hardwareContextDigest",
        )
    ):
        return False
    patch = candidate.get("patch")
    profile = candidate.get("simulation_profile", candidate.get("simulationProfile"))
    if not isinstance(patch, dict) or not isinstance(profile, dict):
        return False
    target_rung = patch.get("target_rung", patch.get("targetRung"))
    scenario = profile.get("scenario")
    if (
        not isinstance(target_rung, dict)
        or not isinstance(target_rung.get("condition"), dict)
        or not isinstance(target_rung["condition"].get("operand"), str)
        or not isinstance(target_rung.get("actions"), list)
        or not isinstance(scenario, dict)
        or not isinstance(scenario.get("steps"), list)
        or not isinstance(profile.get("output_addresses", profile.get("outputAddresses")), list)
    ):
        return False
    addresses = [target_rung["condition"]["operand"]]
    for action in target_rung["actions"]:
        if not isinstance(action, dict) or not isinstance(action.get("operand"), str):
            return False
        addresses.append(action["operand"])
    for step in scenario["steps"]:
        if not isinstance(step, dict):
            return False
        for assignments in (step.get("drive"), step.get("observe")):
            if not isinstance(assignments, dict):
                return False
            addresses.extend(assignments)
    addresses.extend(profile.get("output_addresses", profile.get("outputAddresses")))
    return bool(addresses) and all(
        isinstance(address, str)
        and re.fullmatch(r"M\d{3,4}[0-9A-F]", address) is not None
        for address in addresses
    )


def _acknowledged_terminal_source_run_change(
    mission: EngineeringMission,
    transactions: list[ChangeTransaction],
) -> ChangeTransaction | None:
    """Recover one missing mission link from its exact accepted source run.

    This is intentionally an in-memory bridge for an older checkpoint that was
    persisted before its candidate run later reached a durable terminal state.
    A normal successful checkpoint remains the only writer of the repaired
    link.  Recency, title, and target-only matching are never sufficient.
    """

    if mission.change_transaction_id is not None:
        return None
    candidates = [
        transaction
        for transaction in transactions
        if transaction.conversation_id == mission.conversation_id
        and transaction.run_id == mission.updated_by_run_id
        and transaction.state in TERMINAL_CHANGE_OUTCOME_DISPOSITIONS
        and transaction.receipt_acknowledged_at is not None
        and transaction.target.workspace_id == mission.workspace_id
        and transaction.target.revision_id == mission.revision_id
        and transaction.mutation_scope == f"workspace:{mission.workspace_id}"
    ]
    return candidates[0] if len(candidates) == 1 else None


_EXACT_OPERAND_RETRY_TRIO_TOOLS = frozenset(
    {
        "project.hardware_context",
        "program.change_target_analysis",
        "project.snapshot",
    }
)
_EXACT_OPERAND_RETRY_AFTER_HARDWARE_ACTION = (
    "Call analyze_change_target with the refreshed current-run hardware context "
    "before proposing the exact named-program operand candidate."
)


def _authorized_exact_operand_retry_route_refresh(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    retry_permitted: bool,
    prior_evidence: dict[str, dict[str, Any]],
    current_run_evidence: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Admit only the first fresh-search checkpoint of a safe terminal retry.

    The Agent resets the exact read-only route before it can reuse a prior
    session's search, hardware, target, or snapshot.  A terminal retry is the
    sole exception to immutable completed steps: the checkpoint must have
    completed a single fresh search and be waiting to read fresh hardware.
    This permits no proposal, mutation, or arbitrary mission rewrite.
    """

    if (
        not retry_permitted
        or previous is None
        or current.get("status") != "active"
        or current.get("stopReason") != "continuing"
    ):
        return set(), set()
    previous_steps = {
        step.get("stepId"): step
        for step in previous.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("stepId"), str)
    }
    current_steps = {
        step.get("stepId"): step
        for step in current.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("stepId"), str)
    }
    if set(previous_steps) != set(current_steps):
        return set(), set()

    def one_step(marker: str) -> tuple[str, dict[str, Any]] | None:
        matches = [
            (step_id, step)
            for step_id, step in current_steps.items()
            if marker in str(step.get("title") or "")
        ]
        return matches[0] if len(matches) == 1 else None

    hardware = one_step("inspect_project_hardware_context")
    target = one_step("analyze_change_target")
    proposal = one_step("propose_offline_change")
    # Korean exact-location titles do not include the English ``search`` token;
    # identify the unique remaining completed step that carries project.search.
    search_matches = [
        (step_id, step)
        for step_id, step in current_steps.items()
        if step_id not in {
            hardware[0] if hardware else None,
            target[0] if target else None,
            proposal[0] if proposal else None,
        }
        and previous_steps.get(step_id, {}).get("status") == "completed"
        and all(
            prior_evidence.get(evidence_id, {}).get("toolName") == "project.search"
            for evidence_id in previous_steps.get(step_id, {}).get("evidenceRefs", [])
        )
        and bool(previous_steps.get(step_id, {}).get("evidenceRefs"))
    ]
    if (
        hardware is None
        or target is None
        or proposal is None
        or len(search_matches) != 1
    ):
        return set(), set()
    search_step_id, current_search = search_matches[0]
    hardware_step_id, current_hardware = hardware
    target_step_id, current_target = target
    proposal_step_id, current_proposal = proposal
    previous_search = previous_steps[search_step_id]
    previous_hardware = previous_steps[hardware_step_id]
    previous_target = previous_steps[target_step_id]
    previous_proposal = previous_steps[proposal_step_id]
    if (
        current.get("activeStepId") != hardware_step_id
        or "inspect_project_hardware_context" not in str(current.get("nextAction") or "")
        or any(
            previous_step.get("status") != "completed"
            or not isinstance(previous_step.get("evidenceRefs"), list)
            or not previous_step["evidenceRefs"]
            for previous_step in (previous_search, previous_hardware, previous_target)
        )
        or previous_proposal.get("status") not in {"active", "pending"}
        or current_search.get("status") != "completed"
        or current_hardware.get("status") != "active"
        or current_target.get("status") != "pending"
        or current_proposal.get("status") != "pending"
        or current_hardware.get("evidenceRefs") != []
        or current_target.get("evidenceRefs") != []
        or current_proposal.get("evidenceRefs") != []
    ):
        return set(), set()
    for step_id, previous_step in previous_steps.items():
        if step_id in {search_step_id, hardware_step_id, target_step_id, proposal_step_id}:
            continue
        if current_steps.get(step_id) != previous_step:
            return set(), set()
    removed_refs = set().union(
        previous_search.get("evidenceRefs", []),
        previous_hardware.get("evidenceRefs", []),
        previous_target.get("evidenceRefs", []),
        previous_proposal.get("evidenceRefs", []),
    )
    expected_tools = {
        search_step_id: "project.search",
        hardware_step_id: "project.hardware_context",
        target_step_id: "program.change_target_analysis",
        proposal_step_id: "project.snapshot",
    }
    if any(
        prior_evidence.get(evidence_id, {}).get("toolName") != expected_tools[step_id]
        for step_id, previous_step in (
            (search_step_id, previous_search),
            (hardware_step_id, previous_hardware),
            (target_step_id, previous_target),
            (proposal_step_id, previous_proposal),
        )
        for evidence_id in previous_step.get("evidenceRefs", [])
    ):
        return set(), set()
    fresh_search_refs = set(current_search.get("evidenceRefs", [])).difference(
        previous_search.get("evidenceRefs", [])
    )
    if (
        len(fresh_search_refs) != 1
        or set(current_search.get("evidenceRefs", [])).intersection(removed_refs)
        or not removed_refs.isdisjoint(current_run_evidence)
    ):
        return set(), set()
    fresh_search = current_run_evidence.get(next(iter(fresh_search_refs)))
    if (
        not isinstance(fresh_search, dict)
        or fresh_search.get("evidenceId") not in fresh_search_refs
        or fresh_search.get("toolName") != "project.search"
        or not isinstance(fresh_search.get("receipt"), dict)
        or fresh_search["receipt"].get("schema")
        != "controlstudio-structural-analysis-evidence/v1"
    ):
        return set(), set()
    return removed_refs, {search_step_id, hardware_step_id, target_step_id}


def _authorized_exact_operand_retry_evidence_compaction(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    retry_permitted: bool,
    prior_evidence: dict[str, dict[str, Any]],
    current_run_evidence: dict[str, dict[str, Any]],
) -> set[str]:
    """Replace only a terminal retry's superseded proposal receipt trio.

    The removed receipts and terminal change remain in the Host event journal.
    Only the bounded resumable working set on the active proposal step is
    compacted; completed-step and unrelated evidence stay immutable.
    """

    if (
        not retry_permitted
        or previous is None
        or current.get("status") != "active"
        or current.get("stopReason") != "continuing"
        or current.get("nextAction") != _EXACT_OPERAND_RETRY_AFTER_HARDWARE_ACTION
    ):
        return set()
    proposal_step_id = current.get("activeStepId")
    if not isinstance(proposal_step_id, str) or not proposal_step_id:
        return set()
    previous_steps = {
        step.get("stepId"): step
        for step in previous.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("stepId"), str)
    }
    current_steps = {
        step.get("stepId"): step
        for step in current.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("stepId"), str)
    }
    if set(previous_steps) != set(current_steps):
        return set()
    previous_proposal = previous_steps.get(proposal_step_id)
    current_proposal = current_steps.get(proposal_step_id)
    if (
        not isinstance(previous_proposal, dict)
        or not isinstance(current_proposal, dict)
        or previous_proposal.get("title") != current_proposal.get("title")
        or "propose_offline_change" not in str(current_proposal.get("title") or "")
        or previous_proposal.get("status") not in {"active", "pending"}
        or current_proposal.get("status") != "active"
        or not (
            (
                previous.get("status") == "active"
                and previous.get("activeStepId") == proposal_step_id
            )
            or (
                previous.get("status") == "waiting_for_approval"
                and previous.get("activeStepId") is None
                and previous.get("stopReason") == "needs_human_approval"
            )
        )
    ):
        return set()
    for step_id, previous_step in previous_steps.items():
        if step_id == proposal_step_id:
            continue
        current_step = current_steps.get(step_id)
        if not isinstance(current_step, dict) or current_step != previous_step:
            return set()
    previous_refs = set(previous_proposal.get("evidenceRefs", []))
    current_refs = set(current_proposal.get("evidenceRefs", []))
    removed_refs = previous_refs.difference(current_refs)
    added_refs = current_refs.difference(previous_refs)
    if (
        not removed_refs
        or not removed_refs.isdisjoint(current_run_evidence)
        or len(added_refs) != 1
    ):
        return set()
    fresh_evidence_id = next(iter(added_refs))
    fresh_evidence = current_run_evidence.get(fresh_evidence_id)
    fresh_receipt = (
        fresh_evidence.get("receipt") if isinstance(fresh_evidence, dict) else None
    )
    if (
        not isinstance(fresh_evidence, dict)
        or fresh_evidence.get("evidenceId") != fresh_evidence_id
        or fresh_evidence.get("toolName") != "project.hardware_context"
        or not isinstance(fresh_receipt, dict)
        or fresh_receipt.get("schema")
        != "controlstudio-hardware-context-evidence/v1"
        or not isinstance(fresh_receipt.get("contextDigest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", fresh_receipt["contextDigest"]) is None
    ):
        return set()
    prunable_refs = {
        evidence_id
        for evidence_id in previous_refs
        if (
            (evidence := prior_evidence.get(evidence_id)) is not None
            and evidence.get("evidenceId") == evidence_id
            and evidence.get("toolName") in _EXACT_OPERAND_RETRY_TRIO_TOOLS
        )
    }
    if removed_refs != prunable_refs:
        return set()
    return removed_refs


def _matching_change_outcome_supplemental_evidence(
    change_transaction_id: str | None,
    evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return an outcome receipt only for the transaction being checkpointed.

    A terminal receipt can reopen the exact operand route, whose next proposal
    creates a new pending transaction.  The prior terminal receipt is durable
    history, but cannot supplement the new pending transaction: the store
    projects supplemental evidence from the linked transaction and pending
    transactions deliberately have no terminal outcome projection.
    """

    if not isinstance(change_transaction_id, str) or not change_transaction_id:
        return None
    receipt = evidence.get("receipt") if isinstance(evidence, dict) else None
    if not isinstance(receipt, dict):
        return None
    if receipt.get("transactionId") != change_transaction_id:
        return None
    return evidence


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


def _canonical_json_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


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


def _conversation_v1(
    detail: ConversationDetail,
    *,
    project_plan_authority_digests: tuple[str, str] | None = None,
    engineering_mission: EngineeringMission | None = None,
) -> ConversationV1:
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
                and item.metadata["plan_artifact"].get("schemaVersion") == "2"
                else None
            ),
            plan_digest=(
                str(item.metadata["plan_digest"])
                if isinstance(item.metadata.get("plan_digest"), str)
                else None
            ),
            plan_review=(
                ProjectPlanReviewV1(
                    schema_version=item.project_plan_review.schema_version,
                    authority_current=(
                        item.project_plan_review.schema_version == "2"
                        and project_plan_authority_digests is not None
                        and item.project_plan_review.project_brief_digest
                        == project_plan_authority_digests[0]
                        and item.project_plan_review.attachment_revision_digest
                        == project_plan_authority_digests[1]
                    ),
                    message_id=item.project_plan_review.message_id,
                    plan_digest=item.project_plan_review.plan_digest,
                    reviewed_section_ids=(item.project_plan_review.reviewed_section_ids),
                    reviewed_item_ids=item.project_plan_review.reviewed_item_ids,
                    project_brief_digest=item.project_plan_review.project_brief_digest,
                    attachment_revision_digest=(
                        item.project_plan_review.attachment_revision_digest
                    ),
                    actor_id=item.project_plan_review.actor_id,
                    reviewed_at=item.project_plan_review.reviewed_at,
                )
                if item.project_plan_review is not None
                else None
            ),
            plan_revision=(
                ProjectPlanRevisionDeltaV1.model_validate(item.metadata["plan_revision"])
                if isinstance(item.metadata.get("plan_revision"), dict)
                else None
            ),
            engineering_analysis=(
                EngineeringAnalysisArtifactV1.model_validate(item.metadata["engineering_analysis"])
                if isinstance(item.metadata.get("engineering_analysis"), dict)
                else None
            ),
            engineering_analysis_digest=(
                str(item.metadata["engineering_analysis_digest"])
                if isinstance(
                    item.metadata.get("engineering_analysis_digest"),
                    str,
                )
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
        engineering_handoff=_conversation_handoff_v1(detail),
        engineering_mission=_engineering_mission_v1(engineering_mission),
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


def _conversation_handoff_v1(
    value: Conversation | ConversationDetail,
) -> EngineeringConversationHandoffV1 | None:
    if not isinstance(value.engineering_handoff, dict):
        return None
    return EngineeringConversationHandoffV1.model_validate(
        value.engineering_handoff
    )


def _engineering_mission_v1(
    value: EngineeringMission | None,
) -> EngineeringMissionV1 | None:
    if value is None:
        return None
    return EngineeringMissionV1(
        conversation_id=value.conversation_id,
        workspace_id=value.workspace_id,
        revision_id=value.revision_id,
        revision=value.revision,
        snapshot=EngineeringMissionSnapshotV1.model_validate(
            value.engineering_mission
        ),
        digest=value.digest,
        updated_by_run_id=value.updated_by_run_id,
        change_transaction_id=value.change_transaction_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _engineering_mission_summary_v1(
    value: EngineeringMission | None,
) -> EngineeringMissionSummaryV1 | None:
    if value is None:
        return None
    snapshot = EngineeringMissionSnapshotV1.model_validate(
        value.engineering_mission
    )
    return EngineeringMissionSummaryV1(
        conversation_id=value.conversation_id,
        workspace_id=value.workspace_id,
        revision_id=value.revision_id,
        revision=value.revision,
        status=snapshot.status,
        digest=value.digest,
        updated_by_run_id=value.updated_by_run_id,
        change_transaction_id=value.change_transaction_id,
        updated_at=value.updated_at,
    )


def _conversation_summary_v1(
    value: Conversation,
    *,
    engineering_mission: EngineeringMission | None = None,
) -> ConversationSummaryV1:
    return ConversationSummaryV1(
        conversation_id=value.id,
        title=value.title or "새 대화",
        workflow=value.workflow,
        engineering_target=_conversation_target_v1(value),
        engineering_handoff=_conversation_handoff_v1(value),
        engineering_mission=_engineering_mission_summary_v1(engineering_mission),
        updated_at=value.updated_at,
    )


def _canonical_engineering_mission(
    value: Any,
) -> tuple[dict[str, Any], str]:
    return canonical_engineering_mission_snapshot(value)


def _engineering_mission_evidence_refs(value: dict[str, Any]) -> set[str]:
    return {
        evidence_ref
        for step in value["steps"]
        for evidence_ref in step["evidenceRefs"]
    }


def _historical_evidence_bundle_for_mission(
    store: HostStore,
    *,
    conversation_id: str,
    mission: dict[str, Any],
    mission_digest: str,
    supplemental_evidence: tuple[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence_refs = _engineering_mission_evidence_refs(mission)
    events = store.resolve_conversation_evidence(
        conversation_id=conversation_id,
        evidence_ids=evidence_refs,
        require_all=supplemental_evidence is None,
    )
    projected_evidence: dict[str, tuple[str, dict[str, Any]]] = {}
    for evidence_id, event in events.items():
        payload = project_historical_evidence(event.payload)
        if payload.get("toolName") == "host.change_outcome":
            receipt = payload.get("receipt")
            transaction_id = (
                receipt.get("transactionId") if isinstance(receipt, dict) else None
            )
            transaction = (
                store.get_change_transaction(transaction_id)
                if isinstance(transaction_id, str)
                else None
            )
            if (
                transaction is None
                or transaction.conversation_id != conversation_id
                or transaction.state not in TERMINAL_CHANGE_OUTCOME_DISPOSITIONS
                or payload
                != change_outcome_evidence(change_outcome_projection(transaction))
            ):
                raise EngineeringMissionConflict(
                    "durable change outcome evidence no longer matches its transaction"
                )
        projected_evidence[evidence_id] = (str(event.run_id), payload)
    missing_refs = evidence_refs.difference(projected_evidence)
    if missing_refs:
        source_run_id, payload = supplemental_evidence or ("", {})
        supplemental_id = payload.get("evidenceId")
        if missing_refs != {supplemental_id} or not source_run_id:
            raise EngineeringMissionConflict(
                "durable mission evidence is missing: " + ", ".join(sorted(missing_refs))
            )
        projected_evidence[str(supplemental_id)] = (
            source_run_id,
            project_historical_evidence(payload),
        )
    return build_historical_evidence_bundle(
        mission_digest=mission_digest,
        evidence=projected_evidence,
    )


def _journaled_hardware_context_evidence_for_mission(
    store: HostStore,
    *,
    conversation_id: str,
    mission: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return Host-journaled receipts, never the model-safe projection."""

    events = store.resolve_conversation_evidence(
        conversation_id=conversation_id,
        evidence_ids=_engineering_mission_evidence_refs(mission),
    )
    return [event.payload for event in events.values()]


def _validate_engineering_mission_progression(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    current_run_evidence_refs: set[str],
    allowed_removed_evidence_refs: set[str] | None = None,
    allowed_reopened_step_ids: set[str] | None = None,
) -> None:
    allowed_removed = allowed_removed_evidence_refs or set()
    allowed_reopened = allowed_reopened_step_ids or set()
    if previous["objective"] != current["objective"]:
        raise ValueError("engineering mission objective changed autonomously")
    if previous["completionCriteria"] != current["completionCriteria"]:
        raise ValueError(
            "engineering mission completionCriteria changed autonomously"
        )
    previous_steps = {step["stepId"]: step for step in previous["steps"]}
    current_steps = {step["stepId"]: step for step in current["steps"]}
    for previous_step in previous["steps"]:
        current_step = current_steps.get(previous_step["stepId"])
        if current_step is None:
            raise ValueError("engineering mission step was removed autonomously")
        if current_step["title"] != previous_step["title"]:
            raise ValueError("engineering mission stepId was reused with a new title")
        removed_refs = set(previous_step["evidenceRefs"]).difference(
            current_step["evidenceRefs"]
        )
        if not removed_refs.issubset(allowed_removed):
            raise ValueError("engineering mission step lost evidence")
        if previous_step["status"] != "completed":
            continue
        if (
            current_step["status"] != "completed"
            and previous_step["stepId"] not in allowed_reopened
        ):
            raise ValueError("completed engineering mission step was reopened")
        if (
            current_step["status"] == "completed"
            and current_step.get("outcome") != previous_step.get("outcome")
            and previous_step["stepId"] not in allowed_reopened
        ):
            raise ValueError("completed engineering mission step history changed")
    for step_id, current_step in current_steps.items():
        previous_step = previous_steps.get(step_id)
        previous_step_refs = (
            set(previous_step["evidenceRefs"])
            if previous_step is not None
            else set()
        )
        added_refs = set(current_step["evidenceRefs"]).difference(
            previous_step_refs
        )
        if not added_refs.issubset(current_run_evidence_refs):
            raise ValueError(
                "engineering mission step reused historical evidence without a current observation"
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
        "provenance_receipt_digest": (
            value.provenance.receipt_digest if value.provenance is not None else ""
        ),
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


def _normalized_workspace_path(value: str) -> str:
    """Normalize a local workspace path only for exact identity comparisons."""

    return os.path.normcase(str(Path(value).resolve()))


def _validated_verified_quarantined_backup_restore(
    value: object,
    *,
    transaction: ChangeTransaction,
    expected_current_project_hash: str,
    expected_native_transaction_id: str,
    expected_native_plan_digest: str,
) -> dict[str, Any]:
    """Accept only the narrow MCP receipt for an exact journal-backup restore.

    The Host deliberately receives no private backup or preservation paths.  It
    may refresh its active workspace identity only after MCP has proven that it
    preserved the unexpected current artifact and restored the journal's exact
    pre-change artifact.
    """

    if not isinstance(value, dict):
        raise ChangeExecutionPreconditionError(
            "verified backup restoration returned an invalid MCP receipt"
        )
    expected_baseline = transaction.target.artifact_hash.casefold()
    required_exact = {
        "transaction_id": expected_native_transaction_id,
        "host_transaction_id": transaction.id,
        "plan_digest": expected_native_plan_digest.casefold(),
        "workspace_id": transaction.target.workspace_id,
        "verified_backup_project_hash": expected_baseline,
        "restored_project_hash": expected_baseline,
    }
    if value.get("restored") is not True:
        raise ChangeExecutionPreconditionError(
            "verified backup restoration did not confirm an exact restore"
        )
    if any(value.get(field) != expected for field, expected in required_exact.items()):
        raise ChangeExecutionPreconditionError(
            "verified backup restoration receipt differs from the quarantined transaction"
        )
    preservation_fields = (
        "observed_current_project_hash",
        "preserved_current_project_hash",
        "preservation_ref",
        "preservation_digest",
    )
    if all(field in value and value[field] is None for field in preservation_fields):
        # The journal proved that the canonical destination was absent, so no
        # current bytes existed to preserve before restoring the exact backup.
        return value
    if (
        value.get("observed_current_project_hash")
        != expected_current_project_hash.casefold()
        or value.get("preserved_current_project_hash")
        != expected_current_project_hash.casefold()
    ):
        raise ChangeExecutionPreconditionError(
            "verified backup restoration receipt differs from the quarantined transaction"
        )
    preservation_ref = value.get("preservation_ref")
    preservation_digest = value.get("preservation_digest")
    if (
        not isinstance(preservation_ref, str)
        or not preservation_ref
        or not isinstance(preservation_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", preservation_digest.casefold()) is None
    ):
        raise ChangeExecutionPreconditionError(
            "verified backup restoration receipt has incomplete preservation evidence"
        )
    return value


def _workspace_launch_binding_matches(launch: dict[str, Any], working_path: str) -> bool:
    """Match launch receipts, which differ from the attached session's assurance."""

    if launch.get("identity_assurance") == "controlled_launch_native_file_read_only":
        return (
            launch.get("native_file_usage_verified") is True
            and launch.get("official_open_binding_verified") is False
        )
    receipt = launch.get("open_ui_fallback")
    return (
        launch.get("identity_assurance") == "controlled_launch_official_open_read_only"
        and launch.get("native_file_usage_verified") is False
        and launch.get("official_open_binding_verified") is True
        and isinstance(receipt, dict)
        and receipt.get("status") == "passed"
        and receipt.get("dialog_closed") is True
        and isinstance(receipt.get("project_path"), str)
        and bool(receipt["project_path"].strip())
        and _normalized_workspace_path(receipt["project_path"])
        == _normalized_workspace_path(working_path)
    )


async def _relaunch_unavailable_change_recovery_target(
    mcp: MCPGateway,
    transaction: ChangeTransaction,
    workspace: LocalWorkspaceV1,
    *,
    requested_pid: int,
    requested_hwnd: int,
) -> ChangeRecoveryTarget:
    """Open one exact working copy after its journaled XG5000 target has died.

    This intentionally does not attach or bind the controlled launch.  The
    recovery executor remains the only consumer of the resulting target.
    """

    target = transaction.target
    if (
        target.engineering_process_id is None
        or target.engineering_window_handle is None
        or requested_pid != target.engineering_process_id
        or requested_hwnd != target.engineering_window_handle
    ):
        raise ChangeExecutionPreconditionError(
            "recovery relaunch requires the transaction's journaled PID and HWND"
        )
    try:
        active_path = _normalized_workspace_path(workspace.working_copy.path)
        journaled_path = _normalized_workspace_path(target.project_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ChangeExecutionPreconditionError(
            "active recovery workspace path is invalid"
        ) from exc
    if (
        workspace.workspace_id != target.workspace_id
        or workspace.revision_id != target.revision_id
        or active_path != journaled_path
        or workspace.working_copy.current_sha256.casefold()
        != target.artifact_hash.casefold()
    ):
        raise ChangeExecutionPreconditionError(
            "active recovery workspace is not the transaction's verified working copy"
        )
    try:
        observed = await mcp.call_read_tool("xg5000.instances", {})
    except MCPToolError as exc:
        raise ChangeExecutionPreconditionError(
            "XG5000 availability could not be determined for recovery relaunch"
        ) from exc
    instances = observed.get("instances") if isinstance(observed, dict) else None
    if not isinstance(instances, list) or any(not isinstance(item, dict) for item in instances):
        raise ChangeExecutionPreconditionError(
            "XG5000 instance inventory is invalid for recovery relaunch"
        )
    if len(instances) > 1:
        raise ChangeExecutionPreconditionError(
            "multiple XG5000 instances prevent recovery-only relaunch"
        )
    if instances:
        instance = instances[0]
        if (
            instance.get("pid") == requested_pid
            and instance.get("hwnd") == requested_hwnd
        ):
            raise ChangeExecutionPreconditionError(
                "the journaled XG5000 target is still available for recovery"
            )
        raise ChangeExecutionPreconditionError(
            "a different XG5000 target prevents recovery-only relaunch"
        )

    try:
        launch = await mcp.call_workspace_tool(
            "xg5000.open_project",
            {
                "workspace_id": workspace.workspace_id,
                "expected_working_hash": workspace.working_copy.current_sha256,
                # Cold XG5000 starts on the engineering workstation have
                # exposed their exact PID/HWND before the UI thread becomes
                # responsive. Recovery already owns one bounded launch and
                # cleanup lease, so give that same attempt the product's
                # maximum launch window instead of killing it at 60 seconds.
                "timeout_seconds": 120.0,
            },
        )
    except MCPToolError as exc:
        # The reconciliation endpoint maps this precondition failure directly
        # to its 409 detail.  Retain the MCP/XG5000 project-open reason so a
        # recovery operator can act on the concrete failure rather than a
        # generic proof failure.
        raise ChangeExecutionPreconditionError(str(exc)) from exc
    instance = launch.get("instance") if isinstance(launch, dict) else None
    observed_instances = launch.get("observed_instances") if isinstance(launch, dict) else None
    try:
        launch_pid = launch.get("launch_pid")
        pid = instance.get("pid") if isinstance(instance, dict) else None
        hwnd = instance.get("hwnd") if isinstance(instance, dict) else None
        one_returned_instance = (
            isinstance(observed_instances, list)
            and len(observed_instances) == 1
            and isinstance(observed_instances[0], dict)
            and observed_instances[0].get("pid") == pid
            and observed_instances[0].get("hwnd") == hwnd
        )
        receipt_matches = (
            isinstance(launch, dict)
            and launch.get("ok") is True
            and launch.get("operation") == "xg5000.open_project"
            and launch.get("verified") is True
            and launch.get("workspace_id") == workspace.workspace_id
            and launch.get("revision_id") == workspace.revision_id
            and _normalized_workspace_path(str(launch.get("working_path") or ""))
            == active_path
            and str(launch.get("working_hash") or "").casefold()
            == workspace.working_copy.current_sha256.casefold()
            and launch.get("window_detected") is True
            and isinstance(
                launch.get("project_identity_verified_against_selected_pid"), bool
            )
            and launch.get("project_identity_read_only_bound_to_selected_pid") is True
            and _workspace_launch_binding_matches(launch, workspace.working_copy.path)
            and not isinstance(launch_pid, bool)
            and not isinstance(pid, bool)
            and not isinstance(hwnd, bool)
            and isinstance(launch_pid, int)
            and isinstance(pid, int)
            and isinstance(hwnd, int)
            and launch_pid == pid
            and pid > 0
            and hwnd > 0
            and pid != requested_pid
            and hwnd != requested_hwnd
            and one_returned_instance
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        receipt_matches = False
    if not receipt_matches:
        raise ChangeExecutionPreconditionError(
            "recovery-only XG5000 relaunch receipt is incomplete or mismatched"
        )
    return ChangeRecoveryTarget(pid=pid, hwnd=hwnd)


async def _record_native_handoff_after_freshness(
    freshness: Awaitable[Any],
    write: Callable[[], tuple[Any, bool]],
) -> tuple[Any, bool]:
    await freshness
    return write()


def _reject_private_change_plan_material(value: Any) -> None:
    """Keep raw editor payloads and private implementation fields out of plans."""

    if isinstance(value, list):
        for item in value:
            _reject_private_change_plan_material(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("change plan object keys must be text")
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        # Compiler candidates expose only these baseline SHA-256 identities,
        # never the corresponding baseline source or payload.
        baseline_digest_field = normalized in {
            "baselinemnesha256",
            "baselineldsha256",
            "baselineldcanonicalsha256",
        }
        if (
            key.startswith("_")
            or normalized
            in {
                "clipboard",
                "clipboardpayload",
                "mnepayload",
                "ldpayload",
                "privatepayload",
                "privatematerial",
            }
            or normalized.endswith("payload")
            or (not baseline_digest_field and normalized.startswith(
                (
                    "rawmne",
                    "rawld",
                    "baselinemne",
                    "baselineld",
                    "privatemne",
                    "privateld",
                )
            ))
        ):
            raise ValueError(f"change plan contains forbidden private field: {key}")
        _reject_private_change_plan_material(item)


def _parse_change_plan(value: Any) -> ChangePlanContract:
    if isinstance(
        value,
        (
            ChangePlanV1,
            ChangePlanV2,
            ChangePlanV3,
            ChangePlanV4,
            ChangePlanV5,
            ChangePlanV6,
        ),
    ):
        _reject_private_change_plan_material(value.model_dump(mode="json"))
        return value
    if not isinstance(value, dict):
        raise TypeError("change plan must be an object")
    _reject_private_change_plan_material(value)
    plan_kind = value.get("planKind", value.get("plan_kind"))
    if plan_kind is None:
        return ChangePlanV1.model_validate(value)
    if plan_kind == "mne_full_program_replacement":
        return ChangePlanV2.model_validate(value)
    patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan_kind)
    if patch_contract is not None:
        return patch_contract.plan_model.model_validate(value)
    raise ValueError(f"unsupported change plan kind: {plan_kind}")


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
        plan=_parse_change_plan(value.plan),
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


def _native_project_target_lease_identity(
    value: NativeProjectOperation,
) -> dict[str, Any]:
    """Rebuild the exact durable lease identity used by native creation."""

    request = value.request
    identity: dict[str, Any] = {
        "operationId": value.operation_id,
        "workspaceParent": request["workspaceParent"],
        "projectName": request["projectName"],
        "plcSeries": request["plcSeries"],
        "cpuModel": request["cpuModel"],
        "programName": request["programName"],
        "language": request["language"],
        "xg5000Pid": request["xg5000Pid"],
        "xg5000Hwnd": request["xg5000Hwnd"],
    }
    if isinstance(request.get("reviewedPlan"), dict):
        identity["reviewedPlan"] = request["reviewedPlan"]
    build_bundle = request.get("buildBundle")
    if isinstance(build_bundle, dict) and isinstance(build_bundle.get("bundleId"), str):
        identity["buildBundleId"] = build_bundle["bundleId"]
    return identity


def _native_project_target_lease_must_remain(
    value: NativeProjectOperation,
) -> bool:
    # A completed, independently reopened offline build is terminal. Historic
    # XG-SIM receipts remain attached for audit, but an optional simulator
    # quarantine cannot serialize or block a new offline target.
    return value.state in {"EXECUTING", "QUARANTINED"}


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


def _model_capability_cache_key(value: StoredModelConnection) -> tuple[Any, ...]:
    return (
        value.protocol,
        value.base_url,
        value.model,
        value.timeout_seconds,
        value.secrets.api_key,
        tuple(sorted(value.secrets.custom_headers.items())),
    )


def _engineering_session_identity_verified(session: dict[str, Any] | None) -> bool:
    """Return whether this session can enter the offline-mutation lifecycle.

    A selected PID's native-file proof is intentionally sufficient for
    read-only analysis, but it is not the same-COM proof required by MCP for
    a mutation transaction.  Keep that distinction at the Host boundary so
    status and candidate preflight do not advertise authority MCP will deny.
    """

    if not isinstance(session, dict):
        return False
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("project_identity_verified_against_selected_pid") is True:
        proof = metadata.get("project_identity_proof")
        same_com_proof = bool(
            isinstance(proof, dict)
            and proof.get("method") == "same_rot_object_project_tree_and_path_v1"
            and proof.get("same_active_object") is True
            and proof.get("stable_repeat") is True
            and proof.get("pid") == session.get("pid")
            and proof.get("main_hwnd") == session.get("hwnd")
            and str(proof.get("project_hash") or "").casefold()
            == str(session.get("project_hash") or "").casefold()
        )
        if same_com_proof:
            return True
    if (
        metadata.get("project_identity_read_only_bound_to_selected_pid") is not True
        or metadata.get("project_identity_assurance") != "controlled_launch_read_only"
    ):
        return False
    candidate = metadata.get("offline_candidate_mutation_binding")
    if isinstance(candidate, dict):
        if not (
            candidate.get("purpose") == "offline_candidate_revision"
            and candidate.get("source_kind") == "new_build"
            and candidate.get("pid") == session.get("pid")
            and candidate.get("main_hwnd") == session.get("hwnd")
            and str(candidate.get("project_hash") or "").casefold()
            == str(session.get("project_hash") or "").casefold()
            and _is_canonical_uuid(candidate.get("workspace_id"))
            and _is_canonical_uuid(candidate.get("revision_id"))
            and _is_canonical_uuid(candidate.get("native_project_id"))
            and _is_canonical_uuid(candidate.get("authorization_id"))
            and isinstance(candidate.get("process_create_time"), (int, float))
            and not isinstance(candidate.get("process_create_time"), bool)
            and math.isfinite(float(candidate["process_create_time"]))
            and float(candidate["process_create_time"]) > 0
            and all(
                isinstance(candidate.get(field), str)
                and re.fullmatch(r"[0-9a-f]{64}", candidate[field]) is not None
                for field in (
                    "build_receipt_digest",
                    "provenance_receipt_digest",
                )
            )
        ):
            return False
        try:
            candidate_path = os.path.normcase(
                str(Path(str(candidate.get("project_path") or "")).resolve())
            )
            session_path = os.path.normcase(
                str(Path(str(session.get("project_path") or "")).resolve())
            )
        except (OSError, RuntimeError, ValueError):
            return False
        return bool(str(candidate.get("project_path") or "").strip()) and candidate_path == session_path
    proof = metadata.get("controlled_open_read_proof")
    if not isinstance(proof, dict) or proof.get("method") != (
        "controlled_open_verified_cli_receipt_v3"
    ):
        return False
    if proof.get("proof_source") not in {
        "verified_workspace_launch_cli",
        "verified_ld_reopen_cli",
        "verified_mne_reopen_cli",
    }:
        return False
    if (
        proof.get("pid") != session.get("pid")
        or proof.get("main_hwnd") != session.get("hwnd")
        or not _is_canonical_uuid(proof.get("workspace_id"))
        or not str(proof.get("window_title") or "").strip()
    ):
        return False
    created = proof.get("process_create_time")
    if (
        not isinstance(created, (int, float))
        or isinstance(created, bool)
        or not math.isfinite(float(created))
        or float(created) <= 0
    ):
        return False
    if str(proof.get("project_hash") or "").casefold() != str(
        session.get("project_hash") or ""
    ).casefold():
        return False
    try:
        proof_path = os.path.normcase(str(Path(str(proof.get("project_path") or "")).resolve()))
        session_path = os.path.normcase(
            str(Path(str(session.get("project_path") or "")).resolve())
        )
    except (OSError, RuntimeError, ValueError):
        return False
    return bool(str(proof.get("project_path") or "").strip()) and proof_path == session_path


def _engineering_session_read_ready(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    metadata = session.get("metadata")
    return isinstance(metadata, dict) and bool(
        metadata.get("project_identity_verified_against_selected_pid")
        or metadata.get("project_identity_read_only_bound_to_selected_pid")
    )


def _engineering_session_process_create_time(session: dict[str, Any] | None) -> float | None:
    """Return only an exact existing session lifetime proof, never an estimate."""

    if not isinstance(session, dict):
        return None
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key in (
        "project_identity_proof",
        "controlled_open_read_proof",
        "offline_candidate_mutation_binding",
    ):
        proof = metadata.get(key)
        value = proof.get("process_create_time") if isinstance(proof, dict) else None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
        ):
            return float(value)
    return None


def _engineering_session_exact_lifetime_binding(session: dict[str, Any]) -> bool:
    """Require the retained process-lifetime proof to bind this exact target."""

    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return False
    proof = metadata.get("project_identity_proof")
    if not isinstance(proof, dict):
        proof = metadata.get("controlled_open_read_proof")
    if not isinstance(proof, dict):
        proof = metadata.get("offline_candidate_mutation_binding")
    if not isinstance(proof, dict):
        return False
    created = proof.get("process_create_time")
    if (
        proof.get("pid") != session.get("pid")
        or proof.get("main_hwnd") != session.get("hwnd")
        or not isinstance(created, (int, float))
        or isinstance(created, bool)
        or not math.isfinite(float(created))
        or float(created) <= 0
        or str(proof.get("project_hash") or "").casefold()
        != str(session.get("project_hash") or "").casefold()
    ):
        return False
    try:
        return os.path.normcase(
            str(Path(str(proof.get("project_path") or "")).resolve())
        ) == os.path.normcase(str(Path(str(session.get("project_path") or "")).resolve()))
    except (OSError, RuntimeError, ValueError):
        return False


def _validated_engineering_session_result(
    result: Any,
    *,
    session_id: str | None = None,
    pid: int,
    hwnd: int,
    project_path: str | None,
    project_hash: str | None,
) -> dict[str, Any] | None:
    """Accept only one read-ready session bound to the requested live identity."""

    raw_session = (
        result.get("session")
        if isinstance(result, dict) and isinstance(result.get("session"), dict)
        else result
    )
    if not isinstance(raw_session, dict):
        return None
    if session_id is not None and raw_session.get("session_id") != session_id:
        return None
    if raw_session.get("pid") != pid or raw_session.get("hwnd") != hwnd:
        return None
    if project_path is not None:
        try:
            observed_path = Path(str(raw_session.get("project_path") or "")).resolve()
            expected_path = Path(project_path).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if os.path.normcase(str(observed_path)) != os.path.normcase(str(expected_path)):
            return None
    if (
        project_hash is not None
        and str(raw_session.get("project_hash") or "").casefold()
        != project_hash.casefold()
    ):
        return None
    if not _engineering_session_read_ready(raw_session):
        return None
    metadata = raw_session.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("project_identity_assurance") not in {
        "strong_readback",
        "controlled_launch_read_only",
    }:
        return None
    if _engineering_session_process_create_time(raw_session) is None:
        return None
    if session_id is not None and not _engineering_session_exact_lifetime_binding(raw_session):
        return None
    return raw_session


async def _revalidate_exact_engineering_session(
    mcp: MCPGateway,
    *,
    session_id: str,
    pid: int,
    hwnd: int,
    project_path: str,
    project_hash: str,
) -> dict[str, Any] | None:
    """Read the owning MCP session again; never promote cached session data."""

    if not isinstance(session_id, str) or not session_id or pid <= 0 or hwnd <= 0:
        return None
    try:
        status_result = await mcp.call_recovery_tool(
            "xg5000.session_status",
            {"session_id": session_id},
        )
    except MCPToolError:
        return None
    return _validated_engineering_session_result(
        status_result,
        session_id=session_id,
        pid=pid,
        hwnd=hwnd,
        project_path=project_path,
        project_hash=project_hash,
    )


async def _rebind_reconciled_change_session(
    mcp: MCPGateway,
    transaction: ChangeTransaction,
    *,
    expected_project_path: str,
    expected_project_hash: str,
) -> dict[str, Any] | None:
    """Promote only the terminal checkpoint's still-live exact MCP session."""

    checkpoint = _reconciled_terminal_checkpoint(transaction)
    if checkpoint is None:
        return None
    session_id = checkpoint.get("active_session_id")
    pid = checkpoint.get("pid")
    hwnd = checkpoint.get("hwnd")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(hwnd, int)
        or isinstance(hwnd, bool)
        or pid <= 0
        or hwnd <= 0
        or str(checkpoint.get("current_project_hash") or "").casefold()
        != expected_project_hash.casefold()
    ):
        return None
    try:
        checkpoint_path = checkpoint.get("project_path")
        if not isinstance(checkpoint_path, str):
            return None
        if os.path.normcase(str(Path(checkpoint_path).resolve())) != os.path.normcase(
            str(Path(transaction.target.project_path).resolve())
        ):
            return None
        if os.path.normcase(str(Path(expected_project_path).resolve())) != os.path.normcase(
            str(Path(transaction.target.project_path).resolve())
        ):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        status_result = await mcp.call_recovery_tool(
            "xg5000.session_status",
            {"session_id": session_id},
        )
    except MCPToolError as exc:
        # A terminal resume is only for an MCP restart that discarded SESSIONS;
        # any other read failure must remain fail-closed.
        if "session not found" not in str(exc).casefold():
            return None
    else:
        return _validated_engineering_session_result(
            status_result,
            session_id=session_id,
            pid=pid,
            hwnd=hwnd,
            project_path=expected_project_path,
            project_hash=expected_project_hash,
        )
    terminal_transaction_id = checkpoint.get("transaction_id")
    terminal_plan_digest = checkpoint.get("plan_digest")
    if (
        checkpoint.get("state") != "ROLLED_BACK_SAVED"
        or not isinstance(terminal_transaction_id, str)
        or not terminal_transaction_id
        or not isinstance(terminal_plan_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", terminal_plan_digest.casefold()) is None
    ):
        return None
    try:
        resumed = await mcp.call_workspace_tool(
            "xg5000.resume_terminal_change_session",
            {
                "pid": pid,
                "hwnd": hwnd,
                "project_path": expected_project_path,
                "project_hash": expected_project_hash.casefold(),
                "workspace_id": transaction.target.workspace_id,
                "terminal_transaction_id": terminal_transaction_id,
                "terminal_expected_plan_digest": terminal_plan_digest.casefold(),
            },
        )
    except MCPToolError:
        return None
    resumed_session_id = resumed.get("session_id") if isinstance(resumed, dict) else None
    if not isinstance(resumed_session_id, str) or not resumed_session_id:
        return None
    return _validated_engineering_session_result(
        resumed,
        session_id=resumed_session_id,
        pid=pid,
        hwnd=hwnd,
        project_path=expected_project_path,
        project_hash=expected_project_hash,
    )


def _reconciled_terminal_checkpoint(
    transaction: ChangeTransaction,
) -> dict[str, Any] | None:
    """Return a terminal generic-reconciliation checkpoint, or no authority."""

    reconciliation = transaction.result.get("reconciliation")
    if not isinstance(reconciliation, dict):
        return None
    for evidence_key in ("final_status", "status", "lookup"):
        evidence = reconciliation.get(evidence_key)
        candidate = (
            evidence.get("transaction_checkpoint") if isinstance(evidence, dict) else None
        )
        if (
            isinstance(candidate, dict)
            and candidate.get("terminal") is True
            and candidate.get("recovery_action") == "already_terminal"
            and candidate.get("state")
            in {"COMMITTED", "ROLLED_BACK_SAVED", "ABORTED_NO_MUTATION"}
        ):
            return candidate
    return None


def _is_exact_terminal_mne_no_mutation_abort(
    transaction: ChangeTransaction,
) -> bool:
    """Recognize the one MNE terminal outcome that changed no native file state."""

    if (
        transaction.state is not ChangeTransactionState.ROLLED_BACK
        or transaction.error_code != "mne_recovery_aborted_before_mutation"
        or transaction.plan.get("plan_kind") not in MNE_STRUCTURAL_PLAN_KINDS
    ):
        return False
    reconciliation = transaction.result.get("reconciliation")
    checkpoint = (
        reconciliation.get("abort_checkpoint")
        if isinstance(reconciliation, dict)
        else None
    )
    return bool(
        isinstance(checkpoint, dict)
        and reconciliation.get("native_mutation_performed") is False
        and checkpoint.get("host_transaction_id") == transaction.id
        and checkpoint.get("state") == "ABORTED_NO_MUTATION"
        and checkpoint.get("mutation_possible") is False
        and checkpoint.get("terminal") is True
        and checkpoint.get("recovery_action") == "already_terminal"
        and checkpoint.get("lease_status") == "released"
        and checkpoint.get("current_project_hash") == transaction.target.artifact_hash
    )


async def _recover_locked_reconciled_change_session(
    mcp: MCPGateway,
    transaction: ChangeTransaction,
    *,
    expected_project_path: str,
) -> tuple[str, dict[str, Any]] | None:
    """Use terminal MCP evidence only while an XG file lock blocks rehashing."""

    checkpoint = _reconciled_terminal_checkpoint(transaction)
    recovered_hash = (
        checkpoint.get("current_project_hash") if isinstance(checkpoint, dict) else None
    )
    if not isinstance(recovered_hash, str) or re.fullmatch(
        r"[0-9a-f]{64}", recovered_hash.casefold()
    ) is None:
        return None
    session = await _rebind_reconciled_change_session(
        mcp,
        transaction,
        expected_project_path=expected_project_path,
        expected_project_hash=recovered_hash.casefold(),
    )
    if session is None:
        return None
    return recovered_hash.casefold(), session


async def _reconcile_late_workspace_open(
    mcp: MCPGateway,
    workspace: LocalWorkspaceV1,
    *,
    bind_session: Callable[[Any], dict[str, Any]],
    timeout_seconds: float = LATE_WORKSPACE_RECONCILE_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[XG5000ProjectLaunchV1, dict[str, Any]] | None:
    """Attach one late-ready exact workspace process without launching again."""

    deadline = monotonic() + max(0.0, timeout_seconds)
    while monotonic() < deadline:
        try:
            observed = await mcp.call_read_tool("xg5000.instances", {})
        except MCPToolError:
            return None
        instances = observed.get("instances") if isinstance(observed, dict) else None
        if not isinstance(instances, list) or any(
            not isinstance(item, dict) for item in instances
        ):
            return None
        if len(instances) > 1:
            return None
        if len(instances) == 1:
            instance = instances[0]
            try:
                pid = int(instance["pid"])
                hwnd = int(instance["hwnd"])
            except (KeyError, TypeError, ValueError):
                return None
            if pid <= 0 or hwnd <= 0:
                return None
            try:
                attached = await mcp.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": pid,
                        "hwnd": hwnd,
                        "project_path": workspace.working_copy.path,
                        "project_hash": workspace.working_copy.current_sha256,
                        "workspace_id": workspace.workspace_id,
                    },
                )
            except MCPToolError:
                return None
            raw_session = (
                attached.get("session")
                if isinstance(attached, dict)
                and isinstance(attached.get("session"), dict)
                else attached
            )
            try:
                session_path = Path(str(raw_session["project_path"])).resolve()
                expected_path = Path(workspace.working_copy.path).resolve()
            except (KeyError, TypeError, ValueError, OSError):
                return None
            if (
                not isinstance(raw_session, dict)
                or raw_session.get("pid") != pid
                or raw_session.get("hwnd") != hwnd
                or os.path.normcase(str(session_path))
                != os.path.normcase(str(expected_path))
                or str(raw_session.get("project_hash") or "").casefold()
                != workspace.working_copy.current_sha256.casefold()
                or not _engineering_session_read_ready(raw_session)
            ):
                return None
            metadata = raw_session["metadata"]
            identity_assurance = metadata.get("project_identity_assurance")
            if identity_assurance not in {
                "strong_readback",
                "controlled_launch_read_only",
            }:
                return None
            session = bind_session(attached)
            return (
                XG5000ProjectLaunchV1(
                    operation="xg5000.open_project",
                    verified=True,
                    workspace_id=workspace.workspace_id,
                    working_path=workspace.working_copy.path,
                    working_hash=workspace.working_copy.current_sha256,
                    launch_pid=pid,
                    window_detected=True,
                    project_identity_verified_against_selected_pid=bool(
                        raw_session["metadata"].get(
                            "project_identity_verified_against_selected_pid"
                        )
                    ),
                    project_identity_read_only_bound_to_selected_pid=bool(
                        raw_session["metadata"].get(
                            "project_identity_read_only_bound_to_selected_pid"
                        )
                    ),
                    identity_assurance=identity_assurance,
                    instance=dict(instance),
                    observed_instances=[dict(instance)],
                    detail=(
                        "the single late-ready XG5000 process was attached only after "
                        "exact workspace path, hash, PID, and HWND readback"
                    ),
                ),
                session,
            )
        await sleep(min(0.25, max(0.0, deadline - monotonic())))
    return None


async def _rebind_terminal_mne_session(
    mcp: MCPGateway,
    transaction: ChangeTransaction,
    workspace: dict[str, Any],
    *,
    expected_project_path: Path,
    expected_project_hash: str,
) -> dict[str, Any] | None:
    reopen = validated_terminal_mne_reopen(transaction)
    if reopen is None:
        return None
    workspace_id = workspace.get("workspace_id")
    try:
        reopened_pid = int(reopen["pid"])
        reopened_hwnd = int(reopen["hwnd"])
        reopened_path = Path(str(reopen["project_path"])).resolve()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ChangeExecutionPreconditionError(
            "terminal MNE reopen identity is incomplete"
        ) from exc
    if (
        not isinstance(workspace_id, str)
        or not workspace_id
        or reopened_pid <= 0
        or reopened_hwnd <= 0
        or reopen.get("project_sha256") != expected_project_hash
        or os.path.normcase(str(reopened_path))
        != os.path.normcase(str(expected_project_path))
    ):
        raise ChangeExecutionPreconditionError(
            "terminal MNE reopen differs from the refreshed working copy"
        )
    attached = await mcp.call_read_tool(
        "xg5000.attach",
        {
            "pid": reopened_pid,
            "hwnd": reopened_hwnd,
            "access_profile": "analysis",
            "project_path": str(expected_project_path),
            "project_hash": expected_project_hash,
            "workspace_id": workspace_id,
        },
    )
    session = (
        attached.get("session")
        if isinstance(attached, dict) and isinstance(attached.get("session"), dict)
        else attached
    )
    try:
        session_path = Path(str(session["project_path"])).resolve()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ChangeExecutionPreconditionError(
            "terminal MNE attach returned no exact project identity"
        ) from exc
    if (
        not isinstance(session, dict)
        or session.get("pid") != reopened_pid
        or session.get("hwnd") != reopened_hwnd
        or session.get("project_hash") != expected_project_hash
        or os.path.normcase(str(session_path))
        != os.path.normcase(str(expected_project_path))
        or not _engineering_session_read_ready(session)
    ):
        raise ChangeExecutionPreconditionError(
            "terminal MNE attach did not prove the independently reopened target"
        )
    return dict(session)


def _agent_runtime_user_message(diagnostic: AgentRuntimeDiagnostic) -> str:
    """Return the bounded user-facing message for a sidecar runtime diagnostic."""

    if diagnostic.reason_code == "tool_call_budget":
        return (
            "모델이 분석 도구 호출 예산을 소진해 결과를 저장하지 "
            "않았습니다. 실제 변경은 수행되지 않았습니다."
        )
    if diagnostic.reason_code.startswith("provider_http_"):
        return (
            f"모델 서버가 HTTP {diagnostic.reason_code.removeprefix('provider_http_')} "
            "오류를 반환해 실행을 완료하지 못했습니다. 실제 변경은 수행되지 않았습니다."
        )
    if diagnostic.exception_kind in {"model_transport", "model_api_error"}:
        return "모델 연결이 완료되지 않아 에이전트 실행을 종료했습니다."
    if diagnostic.exception_kind == "tool_failure":
        return "엔지니어링 도구 호출을 완료하지 못했습니다. 실행 기록을 확인해 주세요."
    return "에이전트 내부 실행이 완료되지 않았습니다."


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
    assert config.knowledge_database_path is not None
    database_upgrade = (
        protect_database_upgrades(
            config.database_path, config.knowledge_database_path
        )
        if store is None and knowledge_store is None
        else nullcontext()
    )
    with database_upgrade:
        store = store or HostStore(config.database_path)
        knowledge_store = knowledge_store or KnowledgeStore(config.knowledge_database_path)
        knowledge_store.seed_control_patterns(_builtin_control_pattern_seeds())
    ssh_interactive_handoff_journal = SshInteractiveHandoffJournal(store)
    mcp = mcp or MCPGateway()
    execution_broker = MCPExecutionBroker(mcp)
    agent_supervisor = agent_supervisor or AgentSupervisor()
    if model_repository is None:
        model_repository = ModelConnectionRepository(store)
    try:
        configured_model = model_repository.get()
        model_settings_readable = True
    except StoredModelSettingsError:
        configured_model = None
        model_settings_readable = False
    development_model_base_url = config.development_model_base_url
    development_model_name = config.development_model_name
    development_model_api_key = config.development_model_api_key
    development_model_custom_headers: dict[str, str] | None = None
    if (
        config.release_channel == "development"
        and development_model_base_url
        and development_model_name
        and development_model_api_key
    ):
        development_model_custom_headers = {
            "x-litellm-api-key": development_model_api_key,
        }
    development_model_matches = (
        development_model_custom_headers is not None
        and configured_model is not None
        and configured_model.base_url == development_model_base_url
        and configured_model.model == development_model_name
        and configured_model.timeout_seconds == config.development_model_timeout_seconds
        and configured_model.secrets.api_key == development_model_api_key
        and configured_model.secrets.custom_headers
        == development_model_custom_headers
    )
    if (
        development_model_custom_headers is not None
        and model_settings_readable
        and not development_model_matches
    ):
        assert development_model_base_url is not None
        assert development_model_name is not None
        assert development_model_api_key is not None
        model_repository.save(
            base_url=development_model_base_url,
            model=development_model_name,
            timeout_seconds=config.development_model_timeout_seconds,
            api_key=development_model_api_key,
            custom_headers=development_model_custom_headers,
            inherit_missing_secrets=False,
            allow_insecure_http=True,
        )
    if runtime_factory is None:

        def runtime_factory(
            settings: StoredModelConnection,
            broker: ExecutionBroker,
        ) -> SubprocessAgentClient:
            return SubprocessAgentClient(settings, broker, agent_supervisor)

    change_executor = change_executor or CompositeChangeExecutionService(
        mcp,
        checkpoint_callback=store.record_native_change_checkpoint,
    )
    native_project_repository = NativeProjectRepository(config.database_path)
    native_project_service = NativeProjectService(native_project_repository, mcp)
    run_tasks: dict[str, asyncio.Task[None]] = {}
    active_change_executions: set[str] = set()
    run_submission_lock = asyncio.Lock()
    file_dialog_lock = asyncio.Lock()
    browser_sessions: dict[str, datetime] = {}
    browser_handoff_tickets: dict[str, _BrowserHandoffTicket] = {}
    browser_session_readiness: dict[str, str] = {}
    browser_readiness_receipts: dict[str, _BrowserReadinessReceipt] = {}
    restart_handoff = consume_restart_handoff()
    restart_receipts: dict[str, str] = {}
    if restart_handoff is not None:
        browser_sessions[restart_handoff.session_token] = restart_handoff.session_expires_at
        restart_receipts[restart_handoff.receipt] = "ready"
    recent_model_verification: tuple[tuple[Any, ...], ModelCapabilityResult] | None = None
    shutdown_event = asyncio.Event()
    active_engineering_session: dict[str, Any] | None = None
    active_workspace: dict[str, Any] | None = store.get_host_state("active_workspace")

    def workspace_agent_binding(
        workspace: LocalWorkspaceV1 | dict[str, Any],
    ) -> dict[str, Any]:
        parsed = workspace if isinstance(workspace, LocalWorkspaceV1) else _local_workspace_v1(workspace)
        return {
            "workspace_id": parsed.workspace_id,
            "revision_id": parsed.revision_id,
            "source_kind": parsed.source_kind,
            "provenance_receipt_digest": (
                parsed.provenance.receipt_digest if parsed.provenance is not None else None
            ),
            "source_sha256": parsed.source.sha256.casefold(),
            "working_path": parsed.working_copy.path,
            "working_sha256": parsed.working_copy.current_sha256.casefold(),
        }

    def bind_agent_workspace_target(workspace: LocalWorkspaceV1 | dict[str, Any]) -> None:
        execution_broker.bind_agent_workspace(workspace_agent_binding(workspace))
    active_ls_controller: dict[str, Any] | None = store.get_host_state("active_ls_controller")

    def resolve_project_brief_attachments(
        brief: dict[str, Any] | ProjectBriefV1,
    ) -> list[dict[str, Any]]:
        values = brief.model_dump() if isinstance(brief, ProjectBriefV1) else brief
        source_ids = values.get("attachment_source_ids")
        if source_ids is None:
            return []
        if not isinstance(source_ids, list) or not all(
            isinstance(source_id, str) for source_id in source_ids
        ):
            raise ValueError("project brief attachment source ids are invalid")
        attachments: list[dict[str, Any]] = []
        for source_id in source_ids:
            source = knowledge_store.get_source(source_id)
            if source is None:
                raise ValueError(f"attached project source no longer exists: {source_id}")
            if source.kind not in _PROJECT_BRIEF_ATTACHMENT_KINDS:
                raise ValueError(
                    f"attached source is not a manual, standard, or project document: {source_id}"
                )
            if source.status is not KnowledgeStatus.CONFIRMED:
                raise ValueError(f"attached project source is not confirmed: {source_id}")
            attachments.append(
                {
                    "source_id": source.source_id,
                    "title": source.title,
                    "kind": source.kind.value,
                    "scope": source.scope,
                    "vendor": source.vendor,
                    "product_family": source.product_family,
                    "version": source.version,
                    "language": source.language,
                    "authority": source.authority,
                    "sha256": source.sha256,
                    "revision": source.revision,
                }
            )
        return attachments

    def current_project_plan_authority_digests(
        conversation_id: str,
    ) -> tuple[str, str]:
        detail = store.get_conversation(conversation_id)
        if detail is None:
            raise ProjectPlanReviewConflict("planning conversation was not found")
        if detail.workflow != "new_project_planning" or not detail.project_brief:
            raise ProjectPlanReviewConflict(
                "native execution requires the current Host project brief"
            )
        try:
            brief = ProjectBriefV1.model_validate(detail.project_brief)
            attachments = resolve_project_brief_attachments(brief)
            return (
                calculate_project_brief_digest(brief),
                calculate_project_plan_attachment_revision_digest(attachments),
            )
        except (TypeError, ValueError) as exc:
            raise ProjectPlanReviewConflict(
                "current project brief or attachment source revisions are invalid"
            ) from exc

    if active_ls_controller is not None:
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

    def clear_ls_controller_target() -> None:
        nonlocal active_ls_controller
        if active_ls_controller is None:
            return
        active_ls_controller = None
        mcp.clear_controller_endpoint()
        store.set_host_state("active_ls_controller", None)
        store.append_event(
            event_type="controller.disconnected",
            payload={
                "vendor": "ls",
                "readOnly": True,
                "strongEnoughForWrite": False,
            },
        )

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

    def current_ls_controller_conversation_target() -> tuple[str, str, str] | None:
        if (
            mcp.status.status != "ready"
            or not bool(getattr(mcp, "controller_observation_current", False))
            or not isinstance(active_ls_controller, dict)
        ):
            return None
        raw_observation = active_ls_controller.get("observation")
        raw_endpoint = active_ls_controller.get("endpoint")
        if not isinstance(raw_observation, dict) or not isinstance(raw_endpoint, dict):
            return None
        try:
            observation = LSControllerObservationV1.model_validate(raw_observation)
            observed_address = ipaddress.ip_address(observation.endpoint.host)
            selected_address = ipaddress.ip_address(str(raw_endpoint.get("host") or ""))
            selected_port = int(raw_endpoint["port"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            observed_address != selected_address
            or observation.endpoint.port != selected_port
            or selected_port != 2004
            or observation.endpoint.transport != "tcp"
            or observation.endpoint.protocol != "XGT_FENET"
        ):
            return None
        normalized_host = observed_address.compressed
        if observed_address.version == 6:
            normalized_host = f"[{normalized_host}]"
        fingerprint = observation.provisional_identity.fingerprint_sha256
        return (
            f"controller-ls:{fingerprint}",
            f"XGT_FENET:tcp:{normalized_host}:{selected_port}",
            fingerprint,
        )

    def current_engineering_conversation_target() -> tuple[str, str, str] | None:
        """Return the exact server-owned target a conversation may discuss."""

        if mcp.status.status != "ready":
            return None
        session = active_engineering_session
        if _engineering_session_read_ready(session):
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

            # Direct attach has no Host workspace row, so derive an opaque target
            # identity that survives a new XG5000 session without exposing its path.
            normalized_path = os.path.normcase(os.path.abspath(project_path))
            direct_workspace_id = "direct-xg5000:" + hashlib.sha256(
                normalized_path.encode("utf-8")
            ).hexdigest()
            return direct_workspace_id, project_hash, session_id

        controller_target = current_ls_controller_conversation_target()
        if controller_target is not None:
            return controller_target
        workspace = active_workspace
        if isinstance(workspace, dict):
            workspace_id = str(workspace.get("workspace_id") or "")
            revision_id = str(workspace.get("revision_id") or "")
            bound_target = mcp.agent_session_identity_snapshot
            if (
                workspace_id
                and revision_id
                and isinstance(bound_target, dict)
                and bound_target.get("workspace_id") == workspace_id
            ):
                target_digest = hashlib.sha256(
                    json.dumps(
                        [workspace_id, revision_id],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                return workspace_id, revision_id, f"workspace:{target_digest}"
        return None

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

    def accepted_native_project_handoff(
        native_project_id: str,
        engineering_target: tuple[str, str, str],
    ) -> tuple[str, dict[str, Any]]:
        """Reconstruct accepted plan/build/simulation lineage from Host authority."""

        candidate = native_project_repository.get(native_project_id)
        if candidate is None:
            raise NativeProjectConflict("native project operation was not found")
        if candidate.state != "COMPLETED":
            raise NativeProjectConflict(
                "engineering handoff requires a completed native project"
            )

        request = candidate.request
        result = candidate.result
        reviewed_plan = request.get("reviewedPlan")
        build_bundle = request.get("buildBundle")
        build_receipt = result.get("buildBundleReceipt")
        reopen_verification = result.get("reopenVerification")
        workspace_registration = result.get("workspaceRegistration")
        if not all(
            isinstance(value, dict)
            for value in (
                reviewed_plan,
                build_bundle,
                build_receipt,
                reopen_verification,
                workspace_registration,
            )
        ):
            raise NativeProjectConflict(
                "native project handoff provenance is incomplete"
            )
        assert isinstance(reviewed_plan, dict)
        assert isinstance(build_bundle, dict)
        assert isinstance(build_receipt, dict)
        assert isinstance(reopen_verification, dict)
        assert isinstance(workspace_registration, dict)

        reopen_receipt = reopen_verification.get("receipt")
        workspace = workspace_registration.get("workspace")
        source = workspace.get("source") if isinstance(workspace, dict) else None
        working_copy = (
            workspace.get("working_copy") if isinstance(workspace, dict) else None
        )
        stored_build_provenance = validated_new_build_workspace_provenance(
            candidate,
            workspace,
            require_capture_verification=True,
        )
        active_build_provenance = validated_new_build_workspace_provenance(
            candidate,
            active_workspace,
        )
        active_source = (
            active_workspace.get("source") if isinstance(active_workspace, dict) else None
        )
        active_working_copy = (
            active_workspace.get("working_copy") if isinstance(active_workspace, dict) else None
        )
        try:
            active_workspace_is_exact = bool(
                isinstance(source, dict)
                and isinstance(working_copy, dict)
                and isinstance(active_workspace, dict)
                and isinstance(active_source, dict)
                and isinstance(active_working_copy, dict)
                and active_workspace.get("workspace_id")
                == workspace.get("workspace_id")
                == engineering_target[0]
                and active_workspace.get("revision_id")
                == workspace.get("revision_id")
                == engineering_target[1]
                and active_workspace.get("vendor") == workspace.get("vendor")
                and active_workspace.get("engineering_system")
                == workspace.get("engineering_system")
                and active_source.get("sha256") == source.get("sha256")
                and os.path.normcase(
                    str(Path(str(active_source.get("original_path") or "")).resolve())
                )
                == os.path.normcase(str(Path(str(source.get("original_path") or "")).resolve()))
                and os.path.normcase(
                    str(Path(str(active_source.get("captured_path") or "")).resolve())
                )
                == os.path.normcase(str(Path(str(source.get("captured_path") or "")).resolve()))
                and active_working_copy.get("baseline_sha256")
                == working_copy.get("baseline_sha256")
                and active_working_copy.get("current_sha256") == working_copy.get("current_sha256")
                and active_working_copy.get("state") == working_copy.get("state")
                and os.path.normcase(
                    str(Path(str(active_working_copy.get("path") or "")).resolve())
                )
                == os.path.normcase(str(Path(str(working_copy.get("path") or "")).resolve()))
            )
        except (OSError, ValueError):
            active_workspace_is_exact = False
        if (
            stored_build_provenance is None
            or active_build_provenance != stored_build_provenance
            or not active_workspace_is_exact
        ):
            raise NativeProjectConflict(
                "native project handoff requires the exact new-build workspace provenance"
            )
        if (
            result.get("engineeredProgramReady") is not True
            or result.get("workspaceBound") is not True
            or result.get("identityVerified") is not True
            or result.get("controllerConnected") is not False
            or result.get("plcWritePerformed") is not False
            or workspace_registration.get("state") != "captured_and_bound"
            or reopen_verification.get("state") != "verified"
            or not isinstance(reopen_receipt, dict)
            or reopen_receipt.get("schemaVersion")
            != "controlstudio-native-reopen-receipt/v2"
            or reopen_receipt.get("status") != "REOPEN_VERIFIED"
            or not isinstance(workspace, dict)
            or not isinstance(working_copy, dict)
        ):
            raise NativeProjectConflict(
                "native project does not have verified offline reopen evidence"
            )

        planning_conversation_id = reviewed_plan.get("conversationId")
        planning_message_id = reviewed_plan.get("messageId")
        reviewed_plan_digest = reviewed_plan.get("planDigest")
        project_brief_digest = reviewed_plan.get("projectBriefDigest")
        attachment_revision_digest = reviewed_plan.get(
            "attachmentRevisionDigest"
        )
        reviewed_section_ids = reviewed_plan.get("reviewedSectionIds")
        reviewed_item_ids = reviewed_plan.get("reviewedItemIds")
        if (
            not isinstance(planning_conversation_id, str)
            or not planning_conversation_id
            or not isinstance(planning_message_id, str)
            or not planning_message_id
            or not isinstance(reviewed_plan_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", reviewed_plan_digest) is None
            or not isinstance(project_brief_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", project_brief_digest) is None
            or not isinstance(attachment_revision_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", attachment_revision_digest) is None
            or not isinstance(reviewed_section_ids, list)
            or not reviewed_section_ids
            or not all(isinstance(item, str) for item in reviewed_section_ids)
            or not isinstance(reviewed_item_ids, list)
            or not reviewed_item_ids
            or not all(isinstance(item, str) for item in reviewed_item_ids)
        ):
            raise NativeProjectConflict("reviewed plan handoff binding is invalid")
        try:
            store.require_reviewed_project_plan(
                conversation_id=planning_conversation_id,
                message_id=planning_message_id,
                plan_digest=reviewed_plan_digest,
                reviewed_section_ids=reviewed_section_ids,
                reviewed_item_ids=reviewed_item_ids,
                project_brief_digest=project_brief_digest,
                attachment_revision_digest=attachment_revision_digest,
                expected_handoff_native_project_id=candidate.id,
            )
        except ProjectPlanReviewConflict as exc:
            raise NativeProjectConflict(
                "reviewed plan handoff authority is no longer exact"
            ) from exc

        bundle_id = build_bundle.get("bundleId")
        build_receipt_schema = build_receipt.get("schemaVersion")
        if (
            not isinstance(bundle_id, str)
            or not bundle_id
            or build_receipt_schema
            != "controlstudio-native-build-receipt/v3"
            or build_receipt.get("status") != "APPLIED_VERIFIED"
            or build_receipt.get("phase") != "engineered_control_program"
            or build_receipt.get("bundleId") != bundle_id
            or build_receipt.get("reviewedPlanDigest") != reviewed_plan_digest
            or reopen_receipt.get("buildBundleId") != bundle_id
            or reopen_receipt.get("reviewedPlanDigest") != reviewed_plan_digest
        ):
            raise NativeProjectConflict(
                "verified build and reopen provenance do not match"
            )

        workspace_id, revision_id, engineering_session_id = engineering_target
        build_project_sha256 = result.get("projectSha256")
        project_sha256 = working_copy.get("current_sha256")
        project_path = working_copy.get("path")
        reopen_project = reopen_receipt.get("projectReadback")
        build_program = build_receipt.get("programReadback")
        try:
            active_project_path_matches = bool(
                isinstance(project_path, str)
                and project_path
                and isinstance(active_engineering_session, dict)
                and os.path.normcase(
                    str(
                        Path(
                            str(active_engineering_session.get("project_path") or "")
                        ).resolve()
                    )
                )
                == os.path.normcase(str(Path(project_path).resolve()))
            )
        except (OSError, ValueError):
            active_project_path_matches = False
        if (
            workspace.get("workspace_id") != workspace_id
            or workspace.get("revision_id") != revision_id
            or not isinstance(project_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", project_sha256) is None
            or not isinstance(build_project_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", build_project_sha256) is None
            or not isinstance(build_program, dict)
            or build_program.get("savedProjectSha256") != build_project_sha256
            or not isinstance(reopen_project, dict)
            or reopen_project.get("projectSha256") != project_sha256
            or reopen_project.get("projectUnchanged") is not True
            or not isinstance(active_engineering_session, dict)
            or active_engineering_session.get("session_id")
            != engineering_session_id
            or active_engineering_session.get("project_hash") != project_sha256
            or not active_project_path_matches
        ):
            raise NativeProjectConflict(
                "accepted native project is not the current read-ready target"
            )

        return planning_conversation_id, {
            "schemaVersion": "1",
            "nativeProjectId": candidate.id,
            "planningConversationId": planning_conversation_id,
            "planningMessageId": planning_message_id,
            "reviewedPlanDigest": reviewed_plan_digest,
            "projectBriefDigest": project_brief_digest,
            "attachmentRevisionDigest": attachment_revision_digest,
            "buildBundleId": bundle_id,
            "buildReceiptSchemaVersion": build_receipt_schema,
            "buildReceiptDigest": _canonical_json_digest(build_receipt),
            "reopenReceiptSchemaVersion": reopen_receipt["schemaVersion"],
            "reopenReceiptDigest": _canonical_json_digest(reopen_receipt),
            "projectSha256": project_sha256,
        }

    def native_engineering_status_for_conversation(
        detail: ConversationDetail,
        engineering_target: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        """Re-prove a stored handoff and expose only mission-continuity facts."""

        raw_handoff = detail.engineering_handoff
        if raw_handoff is None:
            return None
        if not isinstance(raw_handoff, dict):
            raise NativeProjectConflict("engineering handoff record is invalid")
        try:
            stored_handoff = EngineeringConversationHandoffV1.model_validate(
                raw_handoff
            ).model_dump(mode="json", by_alias=True)
        except (TypeError, ValueError) as exc:
            raise NativeProjectConflict("engineering handoff record is invalid") from exc
        if raw_handoff != stored_handoff:
            raise NativeProjectConflict("engineering handoff record is not exact")

        planning_conversation_id, current_handoff_raw = (
            accepted_native_project_handoff(
                str(stored_handoff["nativeProjectId"]),
                engineering_target,
            )
        )
        current_handoff = EngineeringConversationHandoffV1.model_validate(
            current_handoff_raw
        ).model_dump(mode="json", by_alias=True)
        if (
            planning_conversation_id != stored_handoff["planningConversationId"]
            or current_handoff != stored_handoff
        ):
            raise NativeProjectConflict(
                "engineering handoff no longer matches accepted native provenance"
            )

        operation = native_project_repository.get(
            str(stored_handoff["nativeProjectId"])
        )
        reviewed_plan = operation.request.get("reviewedPlan") if operation else None
        if not isinstance(reviewed_plan, dict):
            raise NativeProjectConflict("reviewed plan handoff binding is missing")
        if any(
            reviewed_plan.get(reviewed_key) != stored_handoff.get(handoff_key)
            for reviewed_key, handoff_key in (
                ("conversationId", "planningConversationId"),
                ("messageId", "planningMessageId"),
                ("planDigest", "reviewedPlanDigest"),
                ("projectBriefDigest", "projectBriefDigest"),
                ("attachmentRevisionDigest", "attachmentRevisionDigest"),
            )
        ):
            raise NativeProjectConflict("reviewed plan handoff binding changed")
        reviewed_section_ids = reviewed_plan.get("reviewedSectionIds")
        reviewed_item_ids = reviewed_plan.get("reviewedItemIds")
        if (
            not isinstance(reviewed_section_ids, list)
            or not reviewed_section_ids
            or not all(isinstance(item, str) for item in reviewed_section_ids)
            or not isinstance(reviewed_item_ids, list)
            or not reviewed_item_ids
            or not all(isinstance(item, str) for item in reviewed_item_ids)
        ):
            raise NativeProjectConflict("reviewed plan handoff coverage is invalid")
        try:
            _review, reviewed_artifact = store.require_reviewed_project_plan(
                conversation_id=str(stored_handoff["planningConversationId"]),
                message_id=str(stored_handoff["planningMessageId"]),
                plan_digest=str(stored_handoff["reviewedPlanDigest"]),
                reviewed_section_ids=reviewed_section_ids,
                reviewed_item_ids=reviewed_item_ids,
                project_brief_digest=str(stored_handoff["projectBriefDigest"]),
                attachment_revision_digest=str(
                    stored_handoff["attachmentRevisionDigest"]
                ),
                expected_handoff_native_project_id=str(
                    stored_handoff["nativeProjectId"]
                ),
            )
        except ProjectPlanReviewConflict as exc:
            raise NativeProjectConflict(
                "reviewed plan handoff authority is no longer exact"
            ) from exc
        planning_detail = store.get_conversation(
            str(stored_handoff["planningConversationId"])
        )
        if planning_detail is None or not isinstance(
            planning_detail.project_brief,
            dict,
        ):
            raise NativeProjectConflict("reviewed project brief is unavailable")
        try:
            return derive_native_engineering_status(
                handoff=stored_handoff,
                reviewed_plan=reviewed_artifact,
                project_brief=planning_detail.project_brief,
            )
        except (TypeError, ValueError) as exc:
            raise NativeProjectConflict(
                "native engineering status could not be derived exactly"
            ) from exc

    async def revalidate_native_handoff_freshness(
        engineering_target: tuple[str, str, str],
        *,
        native_project_id: str,
        trial_id: str,
        receipt_digest: str,
    ) -> tuple[LocalWorkspaceV1, dict[str, Any]]:
        """Refresh both MCP authorities immediately before the durable handoff write."""

        cached_workspace_raw, cached_session = (
            deepcopy(active_workspace),
            deepcopy(active_engineering_session),
        )
        if (
            not isinstance(cached_workspace_raw, dict)
            or not isinstance(cached_session, dict)
        ):
            raise NativeProjectConflict("native project handoff target is no longer active")
        try:
            cached_workspace = _local_workspace_v1(cached_workspace_raw)
            cached_identity = native_trial_session_identity(
                cached_session,
                cached_workspace,
            )
        except (TypeError, ValueError):
            cached_identity = None
        cached_resume = (
            cached_identity.get("native_trial_resume")
            if isinstance(cached_identity, dict)
            else None
        )
        if (
            not isinstance(cached_resume, dict)
            or cached_resume.get("native_project_id") != native_project_id
            or cached_resume.get("trial_id") != trial_id
            or cached_resume.get("receipt_digest") != receipt_digest
            or cached_session.get("session_id") != engineering_target[2]
        ):
            raise NativeProjectConflict("native project handoff cached identity changed")
        try:
            fresh_workspace, fresh_session = await fresh_native_trial_identity(
                mcp,
                cached_workspace,
                cached_session,
            )
        except (MCPToolError, ValueError) as exc:
            raise NativeProjectConflict(
                "native project handoff freshness could not be verified"
            ) from exc
        if (
            active_workspace != cached_workspace_raw
            or active_engineering_session != cached_session
            or current_engineering_conversation_target() != engineering_target
            or not _engineering_session_read_ready(fresh_session)
        ):
            raise NativeProjectConflict(
                "native project handoff target drifted before it could be recorded"
            )
        return fresh_workspace, fresh_session

    def pending_native_project_handoff(
        engineering_target: tuple[str, str, str] | None,
    ) -> tuple[NativeProjectOperation, str] | None:
        """Select an accepted native project that has not claimed a conversation.

        An existing handoff is durable history, not a pending operation.  In
        particular, a later reattach to the same workspace must not advertise
        its original planning conversation as the active operation and replace
        a user's currently selected engineering conversation.
        """

        fallback_without_conversation: tuple[NativeProjectOperation, str] | None = None
        for candidate in native_project_repository.engineering_handoff_candidates():
            reviewed_plan = candidate.request.get("reviewedPlan")
            registration = candidate.result.get("workspaceRegistration")
            workspace = (
                registration.get("workspace") if isinstance(registration, dict) else None
            )
            source = workspace.get("source") if isinstance(workspace, dict) else None
            working_copy = (
                workspace.get("working_copy") if isinstance(workspace, dict) else None
            )
            if (
                not isinstance(reviewed_plan, dict)
                or not isinstance(workspace, dict)
                or not isinstance(source, dict)
                or not isinstance(working_copy, dict)
            ):
                continue
            stored_build_provenance = validated_new_build_workspace_provenance(
                candidate,
                workspace,
                require_capture_verification=True,
            )
            if stored_build_provenance is None:
                continue
            planning_conversation_id = reviewed_plan.get("conversationId")
            expected_workspace_id = workspace.get("workspace_id")
            expected_revision_id = workspace.get("revision_id")
            expected_path = working_copy.get("path")
            expected_hash = working_copy.get("current_sha256")
            if not all(
                isinstance(value, str) and value
                for value in (
                    planning_conversation_id,
                    expected_workspace_id,
                    expected_revision_id,
                    expected_path,
                    expected_hash,
                )
            ):
                continue
            assert isinstance(planning_conversation_id, str)
            assert isinstance(expected_workspace_id, str)
            assert isinstance(expected_revision_id, str)
            assert isinstance(expected_path, str)
            assert isinstance(expected_hash, str)

            current_target_matches = False
            if (
                engineering_target is not None
                and engineering_target[0] == expected_workspace_id
                and engineering_target[1] == expected_revision_id
                and isinstance(active_engineering_session, dict)
                and isinstance(active_workspace, dict)
            ):
                active_working_copy = active_workspace.get("working_copy")
                active_source = active_workspace.get("source")
                active_build_provenance = validated_new_build_workspace_provenance(
                    candidate,
                    active_workspace,
                )
                try:
                    current_target_matches = bool(
                        isinstance(active_working_copy, dict)
                        and isinstance(active_source, dict)
                        and active_build_provenance == stored_build_provenance
                        and active_workspace.get("workspace_id") == expected_workspace_id
                        and active_workspace.get("revision_id") == expected_revision_id
                        and active_workspace.get("vendor") == workspace.get("vendor")
                        and active_workspace.get("engineering_system")
                        == workspace.get("engineering_system")
                        and active_source.get("sha256") == source.get("sha256")
                        and os.path.normcase(
                            str(Path(str(active_source.get("original_path") or "")).resolve())
                        )
                        == os.path.normcase(
                            str(Path(str(source.get("original_path") or "")).resolve())
                        )
                        and os.path.normcase(
                            str(Path(str(active_source.get("captured_path") or "")).resolve())
                        )
                        == os.path.normcase(
                            str(Path(str(source.get("captured_path") or "")).resolve())
                        )
                        and active_working_copy.get("baseline_sha256")
                        == working_copy.get("baseline_sha256")
                        and active_working_copy.get("state") == working_copy.get("state")
                        and os.path.normcase(
                            str(Path(str(active_working_copy.get("path") or "")).resolve())
                        )
                        == os.path.normcase(str(Path(expected_path).resolve()))
                        and str(active_working_copy.get("current_sha256") or "").casefold()
                        == expected_hash.casefold()
                        and os.path.normcase(
                            str(
                                Path(
                                    str(
                                        active_engineering_session.get("project_path")
                                        or ""
                                    )
                                ).resolve()
                            )
                        )
                        == os.path.normcase(str(Path(expected_path).resolve()))
                        and str(
                            active_engineering_session.get("project_hash") or ""
                        ).casefold()
                        == expected_hash.casefold()
                    )
                except (OSError, ValueError):
                    current_target_matches = False

            existing = store.get_engineering_handoff_conversation(candidate.id)
            # A handoff conversation owns this accepted operation already.
            # Its continuity is re-proved only when that conversation is
            # explicitly opened; status must not promote it over a current
            # same-workspace conversation after an ordinary attach.
            if existing is not None:
                continue
            pending = (candidate, planning_conversation_id)
            if current_target_matches:
                try:
                    accepted_native_project_handoff(candidate.id, engineering_target)
                except (NativeProjectConflict, ProjectPlanReviewConflict):
                    continue
                return pending
            if fallback_without_conversation is None:
                fallback_without_conversation = pending
        return fallback_without_conversation

    async def refresh_terminal_change_identity(
        transaction: ChangeTransaction,
        *,
        retain_engineering_session: bool,
    ) -> bool:
        """Refresh Host identity after a saved/reopened MNE checkpoint."""

        nonlocal active_engineering_session, active_workspace
        if transaction.state not in {
            ChangeTransactionState.COMPLETED,
            ChangeTransactionState.ROLLED_BACK,
            ChangeTransactionState.OBSERVING,
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
        except (OSError, RuntimeError, ValueError):
            active_engineering_session = None
            execution_broker.clear_agent_session()
            return False
        try:
            digest_builder = hashlib.sha256()
            with active_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_builder.update(chunk)
            digest = digest_builder.hexdigest()
        except OSError:
            # XG5000 can exclusively lock the just-saved working copy.  Do not
            # treat the old Host memory as proof; only a terminal reconciliation
            # checkpoint followed by a fresh read from the same MCP session may
            # supply the exact saved hash while that lock is held.
            try:
                recovered = await _recover_locked_reconciled_change_session(
                    mcp,
                    transaction,
                    expected_project_path=str(expected_path),
                )
            except (MCPToolError, OSError, RuntimeError, TypeError, ValueError):
                recovered = None
            if recovered is not None:
                recovered_hash, reconciled_session = recovered
                if reconciled_session is not None:
                    refreshed_workspace = dict(workspace)
                    refreshed_working_copy = dict(working_copy)
                    refreshed_working_copy["current_sha256"] = recovered_hash.casefold()
                    refreshed_working_copy["state"] = (
                        "clean"
                        if recovered_hash.casefold()
                        == str(
                            refreshed_working_copy.get("baseline_sha256") or ""
                        ).casefold()
                        else "modified"
                    )
                    refreshed_workspace["working_copy"] = refreshed_working_copy
                    active_workspace = refreshed_workspace
                    store.set_host_state("active_workspace", active_workspace)
                    active_engineering_session = reconciled_session
                    broker_session = dict(reconciled_session)
                    broker_session.update(workspace_agent_binding(refreshed_workspace))
                    execution_broker.bind_agent_session(broker_session)
                    return True
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

        terminal_mne_no_mutation_abort = _is_exact_terminal_mne_no_mutation_abort(
            transaction
        )
        terminal_mne_change = bool(
            transaction.plan.get("plan_kind") in MNE_STRUCTURAL_PLAN_KINDS
            and transaction.state
            in {
                ChangeTransactionState.COMPLETED,
                ChangeTransactionState.ROLLED_BACK,
            }
            and not terminal_mne_no_mutation_abort
        )
        if not terminal_mne_change:
            try:
                reopened_session = validated_terminal_ld_reopen(transaction)
            except ChangeExecutionPreconditionError as exc:
                logger.warning(
                    "terminal LD target could not be rebound (transaction=%s, kind=%s)",
                    transaction.id,
                    type(exc).__name__,
                )
                active_engineering_session = None
                bind_agent_workspace_target(active_workspace)
                return False
            if reopened_session is not None:
                active_engineering_session = reopened_session
                broker_session = dict(reopened_session)
                broker_session.update(workspace_agent_binding(refreshed_workspace))
                execution_broker.bind_agent_session(broker_session)
                return True
        if terminal_mne_change:
            try:
                reopened_session = await _rebind_terminal_mne_session(
                    mcp,
                    transaction,
                    refreshed_workspace,
                    expected_project_path=expected_path,
                    expected_project_hash=digest,
                )
                if reopened_session is None:
                    raise ChangeExecutionPreconditionError(
                        "terminal MNE change has no independent reopen receipt"
                    )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning(
                    "terminal MNE target could not be rebound (transaction=%s, kind=%s)",
                    transaction.id,
                    type(exc).__name__,
                )
                active_engineering_session = None
                bind_agent_workspace_target(active_workspace)
                return False
            active_engineering_session = reopened_session
            broker_session = dict(reopened_session)
            broker_session.update(workspace_agent_binding(refreshed_workspace))
            execution_broker.bind_agent_session(broker_session)
            return True

        # A generic recovered LD transaction can finish in the MCP session used
        # for rollback/save.  That session has a different identity from the
        # pre-quarantine target, so retaining the old target would be unsafe.
        # Re-read the exact checkpoint session from that same MCP before making
        # it available to the Host or Agent.
        try:
            reconciled_session = await _rebind_reconciled_change_session(
                mcp,
                transaction,
                expected_project_path=str(expected_path),
                expected_project_hash=digest,
            )
        except (MCPToolError, OSError, RuntimeError, TypeError, ValueError):
            reconciled_session = None
        if reconciled_session is not None:
            active_engineering_session = reconciled_session
            broker_session = dict(reconciled_session)
            broker_session.update(workspace_agent_binding(refreshed_workspace))
            execution_broker.bind_agent_session(broker_session)
            return True

        session = active_engineering_session
        session_matches = (
            (retain_engineering_session or terminal_mne_no_mutation_abort)
            and isinstance(session, dict)
            and (
                session.get("session_id") == transaction.target.engineering_session_id
                or (
                    terminal_mne_no_mutation_abort
                    and str(session.get("project_hash") or "").casefold()
                    == digest.casefold()
                )
            )
            and session.get("pid") == transaction.target.engineering_process_id
            and session.get("hwnd") == transaction.target.engineering_window_handle
            and os.path.normcase(str(Path(str(session.get("project_path") or "")).resolve()))
            == os.path.normcase(str(expected_path))
        )
        if session_matches:
            refreshed_session = dict(session)
            refreshed_session["project_hash"] = digest
            active_engineering_session = refreshed_session
            broker_session = dict(refreshed_session)
            if isinstance(active_workspace, dict) and isinstance(
                active_workspace.get("workspace_id"),
                str,
            ):
                broker_session.update(workspace_agent_binding(active_workspace))
            execution_broker.bind_agent_session(broker_session)
        else:
            active_engineering_session = None
            bind_agent_workspace_target(active_workspace)
        return True

    async def engineering_target_run_guard() -> AsyncIterator[None]:
        """Keep one engineering target stable for the complete Agent run."""

        async with run_submission_lock:
            if any(not task.done() for task in run_tasks.values()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "engineering target cannot change during an active Agent run"
                    ),
                )
            yield

    async def target_switch_lease(
        request: Request,
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> AsyncIterator[None]:
        """Serialize workspace/attach requests for their complete route lifetime."""

        holder_token = str(uuid.uuid4())
        native_trial_operation: NativeProjectOperation | None = None
        quarantined_reviewed_close = False
        allowed_change_transaction_id: str | None = None
        try:
            raw_body = await request.json()
        except (json.JSONDecodeError, RuntimeError):
            raw_body = {}
        native_trial_match = re.fullmatch(
            r"/api/v1/native-projects/([0-9a-f-]{36})/simulation-trial/decision",
            request.url.path,
        )
        if native_trial_match is not None:
            native_trial_operation = native_project_repository.get(
                native_trial_match.group(1)
            )
        if native_trial_operation is not None:
            owner_kind = "native_project_create"
            operation_id = native_trial_operation.operation_id
            request_identity = _native_project_target_lease_identity(
                native_trial_operation
            )
        elif request.url.path == "/api/v1/xg5000/reviewed-no-save-close":
            recovery_authority = store.selected_target_recovery_authority()
            if (
                recovery_authority is None
                or recovery_authority.get("owner_kind") != "offline_change"
                or recovery_authority.get("state") != ChangeTransactionState.QUARANTINED.value
            ):
                owner_kind = "engineering_attach"
            else:
                transaction_id = str(recovery_authority.get("operation_id") or "")
                transaction = store.get_change_transaction(transaction_id)
                reconciliation = (
                    transaction.result.get("reconciliation")
                    if transaction is not None and isinstance(transaction.result, dict)
                    else None
                )
                checkpoint = (
                    reconciliation.get("status_checkpoint")
                    if isinstance(reconciliation, dict)
                    else None
                )
                requested_pid = (
                    raw_body.get("pid") if isinstance(raw_body, dict) else None
                )
                requested_hwnd = (
                    raw_body.get("hwnd") if isinstance(raw_body, dict) else None
                )
                workspace_id = (
                    active_workspace.get("workspace_id")
                    if isinstance(active_workspace, dict)
                    else None
                )
                if (
                    transaction is None
                    or transaction.state is not ChangeTransactionState.QUARANTINED
                    or not isinstance(checkpoint, dict)
                    or checkpoint.get("host_transaction_id") != transaction.id
                    or checkpoint.get("workspace_id") != transaction.target.workspace_id
                    or checkpoint.get("state") != ChangeTransactionState.QUARANTINED.value
                    or checkpoint.get("recovery_action") != "rollback_required"
                    or requested_pid is None
                    or isinstance(requested_pid, bool)
                    or not isinstance(requested_pid, int)
                    or requested_hwnd is None
                    or isinstance(requested_hwnd, bool)
                    or not isinstance(requested_hwnd, int)
                    or checkpoint.get("pid") != requested_pid
                    or checkpoint.get("hwnd") != requested_hwnd
                    or workspace_id != transaction.target.workspace_id
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "reviewed close target does not match the current "
                            "quarantined change recovery"
                        ),
                    )
                owner_kind = "offline_change"
                operation_id = transaction.id
                request_identity = transaction.target.model_dump(mode="json")
                allowed_change_transaction_id = transaction.id
                quarantined_reviewed_close = True
        elif request.url.path.endswith("/workspaces/open-local"):
            owner_kind = "workspace_open"
        elif request.url.path.endswith("/resume"):
            owner_kind = "workspace_resume"
        else:
            owner_kind = "engineering_attach"
        if native_trial_operation is None and not quarantined_reviewed_close:
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
        lease_acquired = False
        try:
            store.acquire_target_operation_lease(
                operation_id=operation_id,
                owner_kind=owner_kind,
                holder_token=holder_token,
                target_identity=request_identity,
                allowed_change_transaction_id=allowed_change_transaction_id,
            )
            lease_acquired = True
        except TargetOperationLeaseConflict as exc:
            parked_lease = store.active_target_operation_lease()
            if not (
                request.url.path == "/api/v1/xg5000/attach"
                and exc.stale
                and exc.owner_kind == "native_project_create"
                and isinstance(parked_lease, dict)
                and parked_lease.get("operation_id") == exc.operation_id
                and parked_lease.get("owner_kind") == "native_project_create"
                and parked_lease.get("state") == "STALE_BLOCKED"
            ):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            yield
        finally:
            if lease_acquired:
                current_native_operation = (
                    native_project_repository.get(native_trial_operation.operation_id)
                    if native_trial_operation is not None
                    else None
                )
                if (
                    current_native_operation is not None
                    and _native_project_target_lease_must_remain(current_native_operation)
                ):
                    store.park_target_operation_lease(
                        operation_id=operation_id,
                        holder_token=holder_token,
                        reason="native_simulation_trial_requires_decision_or_recovery",
                    )
                elif quarantined_reviewed_close:
                    store.park_target_operation_lease(
                        operation_id=operation_id,
                        holder_token=holder_token,
                        reason="quarantined_reviewed_close_requires_reconciliation",
                    )
                else:
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
        analysis_evidence_ref = plan.target.get(
            "analysisEvidenceRef",
            plan.target.get("analysis_evidence_ref"),
        )
        analysis_evidence = evidence_by_id.get(str(analysis_evidence_ref))
        if (
            not isinstance(analysis_evidence, dict)
            or analysis_evidence.get("toolName")
            != "program.change_target_analysis"
        ):
            raise ChangeExecutionPreconditionError(
                "change plan is not bound to a change-target analysis receipt "
                "from the exact conversation and run"
            )
        receipt = analysis_evidence.get("receipt")
        if not isinstance(receipt, dict):
            raise ChangeExecutionPreconditionError(
                "change-target analysis evidence has no durable receipt"
            )
        exact_receipt_fields = {
            "analysisDigest": plan.target.get(
                "analysisDigest",
                plan.target.get("analysis_digest"),
            ),
            "receiptDigest": plan.target.get(
                "analysisReceiptDigest",
                plan.target.get("analysis_receipt_digest"),
            ),
            "projectBindingDigest": plan.target.get(
                "projectBindingDigest",
                plan.target.get("project_binding_digest"),
            ),
            "programId": plan.target.get(
                "programId",
                plan.target.get("program_id"),
            ),
            "valueTokenOffset": plan.target.get(
                "tokenOffset",
                plan.target.get("token_offset"),
            ),
            "recordKind": plan.target.get(
                "recordKind",
                plan.target.get("record_kind"),
            ),
            "visualRow": plan.target.get(
                "visualRow",
                plan.target.get("visual_row"),
            ),
            "column": plan.target.get("column"),
            "networkId": plan.target.get(
                "networkId",
                plan.target.get("network_id"),
            ),
            "networkDigest": plan.target.get(
                "networkDigest",
                plan.target.get("network_digest"),
            ),
            "networkIndex": plan.target.get(
                "networkIndex",
                plan.target.get("network_index"),
            ),
            "rungId": plan.target.get("rungId", plan.target.get("rung_id")),
            "rungRole": plan.target.get("rungRole", plan.target.get("rung_role")),
            "primaryDevices": plan.target.get(
                "primaryDevices",
                plan.target.get("primary_devices"),
            ),
            "physicalIoCandidates": plan.target.get(
                "physicalIoCandidates",
                plan.target.get("physical_io_candidates"),
            ),
            "physicalIoBindings": plan.target.get(
                "physicalIoBindings",
                plan.target.get("physical_io_bindings"),
            ),
            "physicalOutputCandidates": plan.target.get(
                "physicalOutputCandidates",
                plan.target.get("physical_output_candidates"),
            ),
            "multipleWriterDevices": plan.target.get(
                "multipleWriterDevices",
                plan.target.get("multiple_writer_devices"),
            ),
            "hardwareContextBinding": plan.target.get(
                "hardwareContextBinding",
                plan.target.get("hardware_context_binding"),
            ),
            "runtimeDeploymentReady": plan.target.get(
                "runtimeDeploymentReady",
                plan.target.get("runtime_deployment_ready"),
            ),
            "runtimeDeploymentBlockingReasons": plan.target.get(
                "runtimeDeploymentBlockingReasons",
                plan.target.get("runtime_deployment_blocking_reasons"),
            ),
            "currentValue": plan.current.get(
                "value",
                plan.current.get("operand"),
            ),
            "proposedValue": plan.proposed.get(
                "value",
                plan.proposed.get("operand"),
            ),
            "currentSemantics": plan.current.get("semantics"),
            "proposedSemantics": plan.proposed.get("semantics"),
        }
        # The Agent only receives the public projection of this binding.  Its
        # raw project hash remains Host-owned and is checked below against the
        # independently retained analysis and hardware receipts.
        exact_receipt_fields["hardwareContextBinding"] = scrub_agent_tool_result(
            exact_receipt_fields["hardwareContextBinding"]
        )
        mismatched_receipt_fields = [
            field_name
            for field_name, expected in exact_receipt_fields.items()
            if scrub_agent_tool_result(receipt.get(field_name)) != expected
        ]
        if mismatched_receipt_fields:
            raise ChangeExecutionPreconditionError(
                "change plan differs from its native analysis receipt: "
                + ", ".join(mismatched_receipt_fields)
            )
        if (
            receipt.get("offlineCandidateReady") is not True
            or receipt.get("blockingReasons") != []
        ):
            raise ChangeExecutionPreconditionError(
                "change-target analysis still has blocking native evidence gaps"
            )
        if plan.request_scope == "offline_candidate_revision":
            runtime_deployment_ready = receipt.get("runtimeDeploymentReady")
            runtime_deployment_blockers = receipt.get(
                "runtimeDeploymentBlockingReasons"
            )
            if (
                receipt.get("schema") != "controlstudio-change-target-evidence/v2"
                or runtime_deployment_ready is not False
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
                raise ChangeExecutionPreconditionError(
                    "offline candidate analysis must retain its runtime deployment block"
                )

        def validate_coverage(hardware_evidence_ref: str | None) -> None:
            receipt_required_areas = {
                "target_identity",
                "value_semantics",
                "direct_impact",
                "indirect_impact",
                "safety",
            }
            coverage_by_area = {item.area: item for item in plan.coverage}
            missing_receipt_bindings = sorted(
                area
                for area in receipt_required_areas
                if coverage_by_area.get(area) is None
                or analysis_evidence_ref not in coverage_by_area[area].evidence_refs
            )
            if missing_receipt_bindings:
                raise ChangeExecutionPreconditionError(
                    "change-target receipt must bind the exact native impact coverage: "
                    + ", ".join(missing_receipt_bindings)
                )
            if hardware_evidence_ref is not None:
                hardware_receipt_required_areas = {
                    "target_identity",
                    "value_semantics",
                    "direct_impact",
                    "safety",
                }
                missing_hardware_bindings = sorted(
                    area
                    for area in hardware_receipt_required_areas
                    if coverage_by_area.get(area) is None
                    or hardware_evidence_ref not in coverage_by_area[area].evidence_refs
                )
                if missing_hardware_bindings:
                    raise ChangeExecutionPreconditionError(
                        "hardware-context receipt must bind CPU, timer-area, I/O, and "
                        "safety coverage: " + ", ".join(missing_hardware_bindings)
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
                if observed_tools.intersection(_STATIC_WORKSPACE_EVIDENCE_TOOLS):
                    raise ChangeExecutionPreconditionError(
                        "static workspace file evidence cannot satisfy change-plan coverage"
                    )
                if not observed_tools.intersection(allowed_tools):
                    raise ChangeExecutionPreconditionError(
                        f"change-plan coverage {item.area} has no applicable "
                        "server-authored evidence; received tools: "
                        + ", ".join(sorted(tool for tool in observed_tools if tool))
                    )

        hardware_free_m_route = (
            receipt.get("hardwareContextBinding") is None
            and all(
                plan.target.get(field_name) is None
                for field_name in (
                    "hardwareContextBinding",
                    "hardware_context_binding",
                )
            )
            and is_hardware_free_m_operand_target(
                plan.target,
                plan.current,
                plan.proposed,
            )
        )
        if hardware_free_m_route:
            hardware_fields = (
                ("hardwareEvidenceRef", "hardware_evidence_ref"),
                ("hardwareContextDigest", "hardware_context_digest"),
                ("hardwareReceiptDigest", "hardware_receipt_digest"),
                ("hardwareProjectBindingDigest", "hardware_project_binding_digest"),
                ("hardwareContext", "hardware_context"),
                ("hardwareContextBinding", "hardware_context_binding"),
            )
            if any(
                plan.target.get(camel) is not None or plan.target.get(snake) is not None
                for camel, snake in hardware_fields
            ):
                raise ChangeExecutionPreconditionError(
                    "hardware-free M plan must not carry hardware authority"
                )
            validate_coverage(None)
            return
        hardware_evidence_ref = plan.target.get(
            "hardwareEvidenceRef",
            plan.target.get("hardware_evidence_ref"),
        )
        hardware_evidence = evidence_by_id.get(str(hardware_evidence_ref))
        if (
            not isinstance(hardware_evidence, dict)
            or hardware_evidence.get("toolName") != "project.hardware_context"
        ):
            raise ChangeExecutionPreconditionError(
                "change plan is not bound to a hardware-context receipt from "
                "the exact conversation and run"
            )
        hardware_receipt = hardware_evidence.get("receipt")
        hardware_context = plan.target.get(
            "hardwareContext",
            plan.target.get("hardware_context"),
        )
        if not isinstance(hardware_receipt, dict) or not isinstance(
            hardware_context,
            dict,
        ):
            raise ChangeExecutionPreconditionError(
                "change plan has no durable hardware-context receipt"
            )
        hardware_receipt_fields = {
            "contextDigest": plan.target.get(
                "hardwareContextDigest",
                plan.target.get("hardware_context_digest"),
            ),
            "receiptDigest": plan.target.get(
                "hardwareReceiptDigest",
                plan.target.get("hardware_receipt_digest"),
            ),
            "projectBindingDigest": plan.target.get(
                "hardwareProjectBindingDigest",
                plan.target.get("hardware_project_binding_digest"),
            ),
            "cpu": hardware_context.get("cpu"),
            "timerAreas": hardware_context.get(
                "timerAreas",
                hardware_context.get("timer_areas"),
            ),
            "ioModules": hardware_context.get(
                "ioModules",
                hardware_context.get("io_modules"),
            ),
            "ioMapping": hardware_context.get(
                "ioMapping",
                hardware_context.get("io_mapping"),
            ),
            "externalWiringObserved": hardware_context.get(
                "externalWiringObserved",
                hardware_context.get("external_wiring_observed"),
            ),
            "blockers": hardware_context.get("blockers"),
            "offlineCandidateReady": hardware_context.get(
                "offlineCandidateReady",
                hardware_context.get("offline_candidate_ready"),
            ),
        }
        mismatched_hardware_fields = [
            field_name
            for field_name, expected in hardware_receipt_fields.items()
            if scrub_agent_tool_result(hardware_receipt.get(field_name)) != expected
        ]
        if mismatched_hardware_fields:
            raise ChangeExecutionPreconditionError(
                "change plan differs from its hardware-context receipt: "
                + ", ".join(mismatched_hardware_fields)
            )
        hardware_binding = receipt.get("hardwareContextBinding")
        required_hardware_context = (
            hardware_binding.get("required_context")
            if isinstance(hardware_binding, dict)
            else None
        )
        hardware_cpu = hardware_receipt.get("cpu")
        hardware_timer_areas = hardware_receipt.get("timerAreas")
        hardware_io_mapping = hardware_receipt.get("ioMapping")
        observed_hardware_status = {
            "cpu_status": (
                hardware_cpu.get("status")
                if isinstance(hardware_cpu, dict)
                else "unobserved"
            ),
            "timer_area_status": (
                "observed"
                if isinstance(hardware_timer_areas, list)
                and hardware_timer_areas
                and all(
                    isinstance(area, dict) and area.get("status") == "observed"
                    for area in hardware_timer_areas
                )
                else "unobserved"
            ),
            "io_mapping_status": (
                hardware_io_mapping.get("status")
                if isinstance(hardware_io_mapping, dict)
                else "unobserved"
            ),
            "external_wiring_observed": hardware_receipt.get(
                "externalWiringObserved"
            ),
            "external_wiring_status": (
                "observed"
                if hardware_receipt.get("externalWiringObserved") is True
                else "unobserved"
            ),
        }
        required_hardware_status = {
            "cpu": "cpu_status",
            "timer_area": "timer_area_status",
            "io_mapping": "io_mapping_status",
            "external_wiring": "external_wiring_status",
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
                for blocker in hardware_receipt.get("blockers", [])
                if hardware_blocker_context.get(blocker)
                in required_hardware_context
                or blocker not in hardware_blocker_context
            ]
            if isinstance(required_hardware_context, list)
            else []
        )
        if (
            not isinstance(hardware_binding, dict)
            or hardware_binding.get("schema")
            != "xg5000.project.hardware-context/1.0"
            or hardware_binding.get("context_digest")
            != hardware_receipt.get("contextDigest")
            or hardware_binding.get("project_hash")
            != hardware_receipt.get("projectHash")
            or not isinstance(required_hardware_context, list)
            or not required_hardware_context
            or any(not isinstance(item, str) for item in required_hardware_context)
            or len(set(required_hardware_context)) != len(required_hardware_context)
            or any(
                item not in required_hardware_status
                for item in required_hardware_context
            )
            or (
                plan.request_scope != "offline_candidate_revision"
                and (
                    hardware_binding.get("blocking_reasons") != []
                    or any(
                        hardware_binding.get(name) != status
                        for name, status in observed_hardware_status.items()
                    )
                    or any(
                        hardware_binding.get(required_hardware_status[name])
                        != "observed"
                        for name in required_hardware_context
                    )
                    or bool(relevant_hardware_blockers)
                )
            )
        ):
            raise ChangeExecutionPreconditionError(
                "change-target analysis has an unobserved required hardware context"
            )
        validate_coverage(hardware_evidence_ref)

    def validate_mne_coverage_evidence(
        plan: ChangePlanV2 | ChangePlanV3 | ChangePlanV4 | ChangePlanV5 | ChangePlanV6,
        *,
        conversation_id: str | None,
        run_id: str | None,
        require_full_program_evidence: bool,
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        if conversation_id is None or run_id is None:
            raise ChangeExecutionPreconditionError(
                "an MNE change plan must be bound to one ControlStudio "
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

        all_referenced_ids: set[str] = set()
        confirmed_referenced_ids: set[str] = set()
        for item in plan.coverage:
            if len(set(item.evidence_refs)) != len(item.evidence_refs):
                raise ChangeExecutionPreconditionError(
                    f"MNE coverage {item.area} repeats an evidenceRef"
                )
            for evidence_ref in item.evidence_refs:
                try:
                    canonical_ref = str(uuid.UUID(evidence_ref))
                except ValueError as exc:
                    raise ChangeExecutionPreconditionError(
                        "MNE evidenceRefs must be canonical UUIDs"
                    ) from exc
                if canonical_ref != evidence_ref:
                    raise ChangeExecutionPreconditionError(
                        "MNE evidenceRefs must be canonical UUIDs"
                    )
                all_referenced_ids.add(evidence_ref)
                if item.status == "confirmed":
                    confirmed_referenced_ids.add(evidence_ref)
        missing_refs = sorted(all_referenced_ids.difference(evidence_by_id))
        if missing_refs:
            raise ChangeExecutionPreconditionError(
                "MNE evidenceRefs do not resolve to the exact conversation "
                "and run: " + ", ".join(missing_refs)
            )

        coverage_by_area = {item.area: item for item in plan.coverage}
        required_confirmed_areas = (
            _CHANGE_COVERAGE_MUST_BE_CONFIRMED
            if require_full_program_evidence
            else frozenset(MNE_COMPILER_CANDIDATE_REQUIRED_COVERAGE_AREAS)
        )
        for area in required_confirmed_areas:
            if coverage_by_area[area].status != "confirmed":
                raise ChangeExecutionPreconditionError(
                    f"MNE coverage {area} must be confirmed with "
                    "server-authored evidence"
                )
        compiler_tool: str | None = None
        if not require_full_program_evidence:
            patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan.plan_kind)
            compiler_tool = (
                patch_contract.compiler_tool if patch_contract is not None else None
            )
            candidate_evidence_id = getattr(plan, "candidate_evidence_id", None)
            if (
                compiler_tool is None
                or not isinstance(candidate_evidence_id, str)
                or evidence_by_id.get(candidate_evidence_id, {}).get("toolName")
                != compiler_tool
                or any(
                    candidate_evidence_id
                    not in coverage_by_area[area].evidence_refs
                    for area in required_confirmed_areas
                )
            ):
                raise ChangeExecutionPreconditionError(
                    "compiler candidate coverage must bind the exact current-run receipt"
                )
        for item in plan.coverage:
            if item.status != "confirmed":
                continue
            observed_tools = {
                str(evidence_by_id[evidence_ref].get("toolName") or "")
                for evidence_ref in item.evidence_refs
            }
            if observed_tools.intersection(_STATIC_WORKSPACE_EVIDENCE_TOOLS):
                raise ChangeExecutionPreconditionError(
                    "static workspace file evidence cannot satisfy MNE coverage"
                )
            allowed_tools = _MNE_COVERAGE_ALLOWED_TOOLS[item.area]
            if compiler_tool is not None and item.area in required_confirmed_areas:
                allowed_tools = allowed_tools | frozenset({compiler_tool})
            if not observed_tools.intersection(allowed_tools):
                raise ChangeExecutionPreconditionError(
                    f"MNE coverage {item.area} has no applicable "
                    "server-authored evidence; received tools: "
                    + ", ".join(sorted(tool for tool in observed_tools if tool))
                )

        if require_full_program_evidence:
            selected_tools = {
                str(evidence_by_id[evidence_ref].get("toolName") or "")
                for evidence_ref in confirmed_referenced_ids
            }
            missing_tools = sorted(
                _MNE_FULL_PROGRAM_REQUIRED_EVIDENCE_TOOLS.difference(selected_tools)
            )
            if missing_tools:
                raise ChangeExecutionPreconditionError(
                    "full-program proposal is missing required Host evidence tools: "
                    + ", ".join(missing_tools)
                )
        return evidence_by_id, confirmed_referenced_ids

    def validate_mne_program_evidence_binding(
        plan: ChangePlanV2,
        *,
        target_identity: ChangeTargetIdentity,
        conversation_id: str | None,
        run_id: str | None,
    ) -> MneFullProgramBaselineBindingV2:
        evidence_by_id, confirmed_referenced_ids = validate_mne_coverage_evidence(
            plan,
            conversation_id=conversation_id,
            run_id=run_id,
            require_full_program_evidence=True,
        )

        target = plan.target_program
        target_bound_tools: set[str] = set()
        target_payload_identities: set[tuple[str, str]] = set()
        structure_project_hashes: set[str] = set()
        hardware_project_hashes: set[str] = set()
        required_program_tools = (
            _MNE_FULL_PROGRAM_REQUIRED_EVIDENCE_TOOLS
            - {"project.structure_index", "project.hardware_context"}
        )
        for evidence_ref in confirmed_referenced_ids:
            evidence = evidence_by_id[evidence_ref]
            tool_name = str(evidence.get("toolName") or "")
            if tool_name in STRUCTURAL_ANALYSIS_TOOL_COVERAGE:
                try:
                    receipt = validate_structural_evidence_contract(
                        evidence.get("receipt"),
                        expected_tool=structural_receipt_tool_name(tool_name),
                    )
                except (TypeError, ValueError, MCPToolError) as exc:
                    raise ChangeExecutionPreconditionError(
                        "full-program structural evidence receipt is invalid"
                    ) from exc
                if tool_name == "project.structure_index":
                    structure_project_hashes.add(
                        str(receipt["projectSnapshot"]["projectSha256"])
                    )
                    continue
                exact_bindings = [
                    binding
                    for binding in receipt["scope"]["payloadBindings"]
                    if binding["programId"] == target.program_id
                    and binding["program"] == target.program_name
                ]
                if exact_bindings:
                    target_bound_tools.add(tool_name)
                    target_payload_identities.update(
                        (str(binding["program"]), str(binding["payloadSha256"]))
                        for binding in exact_bindings
                    )
                continue
            if tool_name != "project.hardware_context":
                continue
            raw_receipt = evidence.get("receipt")
            if not isinstance(raw_receipt, dict):
                raise ChangeExecutionPreconditionError(
                    "full-program hardware evidence has no durable receipt"
                )
            unsigned = dict(raw_receipt)
            receipt_digest = unsigned.pop("receiptDigest", None)
            actual_digest = hashlib.sha256(
                json.dumps(
                    unsigned,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            project_hash = raw_receipt.get("projectHash")
            if (
                raw_receipt.get("schema")
                != "controlstudio-hardware-context-evidence/v1"
                or not isinstance(receipt_digest, str)
                or not hmac.compare_digest(receipt_digest, actual_digest)
                or raw_receipt.get("blockers") != []
                or raw_receipt.get("offlineCandidateReady") is not True
                or not isinstance(project_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", project_hash) is None
            ):
                raise ChangeExecutionPreconditionError(
                    "full-program hardware evidence receipt is not ready"
                )
            hardware_project_hashes.add(project_hash)

        missing_program_bindings = sorted(
            required_program_tools.difference(target_bound_tools)
        )
        if missing_program_bindings:
            raise ChangeExecutionPreconditionError(
                "full-program evidence is not bound to the exact programId and "
                "programName for: " + ", ".join(missing_program_bindings)
            )
        if len(target_payload_identities) != 1:
            raise ChangeExecutionPreconditionError(
                "full-program structural receipts do not share one stable exact "
                "program payload identity"
            )
        if (
            len(structure_project_hashes) != 1
            or len(hardware_project_hashes) != 1
            or structure_project_hashes != hardware_project_hashes
        ):
            raise ChangeExecutionPreconditionError(
                "full-program structure and hardware evidence disagree on the "
                "exact project snapshot"
            )
        evidence_project_hash = next(iter(structure_project_hashes))
        if evidence_project_hash != target_identity.artifact_hash:
            raise ChangeExecutionPreconditionError(
                "full-program evidence project hash differs from the current "
                "Host engineering target"
            )
        baseline_program_name, baseline_payload_sha256 = next(
            iter(target_payload_identities)
        )
        expected_binding = MneFullProgramBaselineBindingV2(
            project_sha256=evidence_project_hash,
            program_id=target.program_id,
            program_name=baseline_program_name,
            ld_payload_sha256=baseline_payload_sha256,
        )
        if plan.baseline_binding is not None and plan.baseline_binding != expected_binding:
            raise ChangeExecutionPreconditionError(
                "full-program approved baseline differs from current Host evidence"
            )
        return expected_binding

    def mne_patch_candidate_for_run(
        *,
        plan_kind: str,
        conversation_id: str,
        run_id: str,
        evidence_id: str,
    ) -> (
        MneSeriesGuardCandidateV3
        | MneInstructionPatchCandidateV4
        | MneInstructionBatchPatchCandidateV5
        | MneRungPatchCandidateV6
    ):
        patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan_kind)
        if patch_contract is None:
            raise ChangeExecutionPreconditionError(
                "unsupported MNE patch candidate kind"
            )
        matched = [
            event.payload
            for event in store.list_run_evidence(
                conversation_id=conversation_id,
                run_id=run_id,
            )
            if event.payload.get("evidenceId") == evidence_id
        ]
        if len(matched) != 1:
            raise ChangeExecutionPreconditionError(
                "candidateEvidenceId must resolve to one current-run compiler receipt"
            )
        payload = matched[0]
        receipt = payload.get("receipt")
        if (
            payload.get("toolName") != patch_contract.compiler_tool
            or not isinstance(receipt, dict)
        ):
            raise ChangeExecutionPreconditionError(
                "candidateEvidenceId is not the requested MNE patch compiler receipt"
            )
        unsigned = dict(receipt)
        receipt_digest = unsigned.pop("receiptDigest", None)
        try:
            candidate = patch_contract.candidate_model.model_validate(
                receipt.get("candidate")
            )
        except (TypeError, ValueError) as exc:
            raise ChangeExecutionPreconditionError(
                "MNE patch compiler receipt has an invalid candidate"
            ) from exc
        if (
            receipt.get("schema") != patch_contract.evidence_schema
            or not isinstance(receipt_digest, str)
            or not hmac.compare_digest(
                receipt_digest,
                hashlib.sha256(
                    json.dumps(
                        unsigned,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
            )
        ):
            raise ChangeExecutionPreconditionError(
                "MNE patch compiler receipt digest is invalid"
            )
        return candidate

    def validate_mne_patch_evidence_binding(
        plan: ChangePlanV3 | ChangePlanV4 | ChangePlanV5 | ChangePlanV6,
        *,
        target_identity: ChangeTargetIdentity,
        conversation_id: str | None,
        run_id: str | None,
    ) -> MneFullProgramBaselineBindingV2:
        """Bind one compiler candidate to its current target and evidence coverage."""
        candidate = plan.candidate
        validate_mne_coverage_evidence(
            plan,
            conversation_id=conversation_id,
            run_id=run_id,
            require_full_program_evidence=False,
        )
        if candidate.project_sha256 != target_identity.artifact_hash:
            raise ChangeExecutionPreconditionError(
                "MNE patch candidate differs from the current Host engineering target"
            )
        if candidate.baseline_ld_canonical_sha256 is None:
            raise ChangeExecutionPreconditionError(
                "MNE patch candidate lacks a canonical LD baseline identity"
            )
        observed = mne_patch_candidate_for_run(
            plan_kind=plan.plan_kind,
            conversation_id=conversation_id,
            run_id=run_id,
            evidence_id=plan.candidate_evidence_id,
        )
        if observed != candidate:
            raise ChangeExecutionPreconditionError(
                "MNE patch compiler receipt differs from the approved candidate"
            )
        baseline = MneFullProgramBaselineBindingV2(
            project_sha256=candidate.project_sha256,
            program_id=candidate.program_id,
            program_name=candidate.program_name,
            ld_payload_sha256=candidate.baseline_ld_sha256,
            ld_canonical_sha256=candidate.baseline_ld_canonical_sha256,
        )
        if plan.baseline_binding is not None and (
            plan.baseline_binding.project_sha256 != baseline.project_sha256
            or plan.baseline_binding.program_id != baseline.program_id
            or plan.baseline_binding.program_name != baseline.program_name
            or plan.baseline_binding.ld_canonical_sha256
            != baseline.ld_canonical_sha256
        ):
            raise ChangeExecutionPreconditionError(
                "MNE patch approved baseline differs from its compiler candidate"
            )
        return baseline

    def validate_engineering_analysis_artifact(
        artifact_data: dict[str, Any] | None,
        supplied_digest: str | None,
        *,
        conversation_id: str,
        run_id: str,
        mission: dict[str, Any] | None,
        prior_mission: dict[str, Any] | None,
        current_outcome_evidence: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if artifact_data is None and supplied_digest is None:
            return None, None
        if artifact_data is None or supplied_digest is None:
            raise AgentOutputContractError(
                "Agent engineering analysis artifact and digest must be returned together"
            )
        try:
            artifact = EngineeringAnalysisArtifactV1.model_validate(artifact_data)
        except ValueError as exc:
            raise AgentOutputContractError(
                "Agent engineering analysis artifact is invalid"
            ) from exc
        canonical = artifact.model_dump(mode="json", by_alias=True)
        actual_digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if supplied_digest != actual_digest:
            raise AgentOutputContractError(
                "Agent engineering analysis digest does not match its artifact"
            )

        authoritative_evidence = [
            event.payload
            for event in store.list_run_evidence(
                conversation_id=conversation_id,
                run_id=run_id,
            )
            if isinstance(event.payload.get("evidenceId"), str)
            and event.payload["evidenceId"]
        ]
        if (
            current_outcome_evidence is not None
            and all(
                evidence.get("evidenceId")
                != current_outcome_evidence["evidenceId"]
                for evidence in authoritative_evidence
            )
        ):
            authoritative_evidence.insert(0, current_outcome_evidence)
        authoritative_evidence_ids = [
            str(evidence["evidenceId"]) for evidence in authoritative_evidence
        ]
        if artifact.evidence_refs != authoritative_evidence_ids:
            raise AgentOutputContractError(
                "Agent engineering analysis evidenceRefs do not exactly match "
                "the Host evidence for this run"
            )
        structured_referenced_ids = {
            evidence_ref
            for observation in artifact.observations
            for evidence_ref in observation.evidence_refs
        }
        referenced_ids = set(structured_referenced_ids)
        referenced_ids.update(
            evidence_ref
            for inference in artifact.inferences
            for evidence_ref in inference.evidence_refs
        )
        referenced_ids.update(
            evidence_ref
            for hypothesis in artifact.hypotheses
            for evidence_ref in hypothesis.supporting_evidence_refs
        )
        referenced_ids.update(
            evidence_ref
            for hypothesis in artifact.hypotheses
            for evidence_ref in (hypothesis.verification_evidence_refs or [])
        )
        referenced_ids.update(
            evidence_ref
            for alternative in artifact.alternatives
            for evidence_ref in alternative.evidence_refs
        )
        locations = artifact.locations or []
        relations = artifact.relations or []
        coverage_items = artifact.coverage or []
        project_structure_items = artifact.project_structure or []
        structured_referenced_ids.update(
            location.evidence_ref for location in locations
        )
        structured_referenced_ids.update(
            relation.evidence_ref for relation in relations
        )
        structured_referenced_ids.update(
            location.evidence_ref
            for relation in relations
            for location in relation.locations
        )
        structured_referenced_ids.update(
            evidence_ref
            for coverage in coverage_items
            for evidence_ref in coverage.evidence_refs
        )
        structured_referenced_ids.update(
            item.evidence_ref for item in project_structure_items
        )
        referenced_ids.update(structured_referenced_ids)
        historical_evidence_ids = (
            _engineering_mission_evidence_refs(prior_mission)
            if prior_mission is not None
            else set()
        )
        current_evidence_ids = set(authoritative_evidence_ids)
        if not referenced_ids.issubset(
            current_evidence_ids | historical_evidence_ids
        ):
            raise AgentOutputContractError(
                "Agent engineering analysis item evidenceRefs are not Host-authored"
            )
        if artifact.schema_version in {"2", "3", "4"}:
            historical_structural_ids = structured_referenced_ids.difference(
                current_evidence_ids
            )
            try:
                historical_structural_evidence = [
                    event.payload
                    for event in store.resolve_conversation_evidence(
                        conversation_id=conversation_id,
                        evidence_ids=historical_structural_ids,
                    ).values()
                ]
            except EngineeringMissionConflict as exc:
                raise AgentOutputContractError(str(exc)) from exc
            structural_receipts: dict[str, dict[str, Any]] = {}
            structural_ids_by_area: dict[str, list[str]] = {}
            for evidence in [
                *authoritative_evidence,
                *historical_structural_evidence,
            ]:
                tool_name = evidence.get("toolName")
                if not isinstance(tool_name, str) or (
                    tool_name not in STRUCTURAL_ANALYSIS_TOOL_COVERAGE
                ):
                    continue
                evidence_id = str(evidence["evidenceId"])
                try:
                    receipt = validate_structural_evidence_contract(
                        evidence.get("receipt"),
                        expected_tool=structural_receipt_tool_name(tool_name),
                    )
                except (TypeError, ValueError, MCPToolError) as exc:
                    raise AgentOutputContractError(
                        "Host structural evidence receipt is missing or invalid"
                    ) from exc
                structural_receipts[evidence_id] = receipt
                area = STRUCTURAL_ANALYSIS_TOOL_COVERAGE[tool_name]
                structural_ids_by_area.setdefault(area, []).append(evidence_id)

            payload_identity_by_program: dict[str, tuple[str, str]] = {}
            for receipt in structural_receipts.values():
                for binding in receipt["scope"]["payloadBindings"]:
                    program_id = str(binding["programId"])
                    identity = (
                        str(binding["program"]),
                        str(binding["payloadSha256"]),
                    )
                    previous = payload_identity_by_program.get(program_id)
                    if previous is not None and previous != identity:
                        raise AgentOutputContractError(
                            "Host structural receipts disagree on a program payload snapshot"
                        )
                    payload_identity_by_program[program_id] = identity

            def location_value(item: Any) -> dict[str, Any]:
                value = item.model_dump(mode="json", by_alias=True)
                value.pop("evidenceRef", None)
                return value

            def relation_value(item: Any) -> dict[str, Any]:
                value = item.model_dump(mode="json", by_alias=True)
                value.pop("evidenceRef", None)
                nested = value.get("locations")
                if not isinstance(nested, list):
                    raise AgentOutputContractError(
                        "Agent engineering relation locations are invalid"
                    )
                value["locations"] = [
                    {
                        key: nested_value
                        for key, nested_value in location.items()
                        if key != "evidenceRef"
                    }
                    for location in nested
                ]
                return value

            if artifact.schema_version in {"3", "4"}:
                observed_evidence_ids: set[str] = set()
                for observation in artifact.observations:
                    evidence_id = observation.evidence_refs[0]
                    receipt = structural_receipts.get(evidence_id)
                    if receipt is None:
                        raise AgentOutputContractError(
                            "Agent observation is not bound to structural evidence"
                        )
                    field = {
                        "location": "locationFacts",
                        "relation": "relationFacts",
                        "project_structure": "structureFacts",
                    }[str(observation.fact_kind)]
                    match = next(
                        (
                            value
                            for value in receipt.get(field, [])
                            if value.get("fingerprint")
                            == observation.fact_fingerprint
                        ),
                        None,
                    )
                    if match is None or observation.statement != (
                        structural_observation_statement(
                            str(observation.fact_kind),
                            match,
                        )
                    ):
                        raise AgentOutputContractError(
                            "Agent observation is not an exact Host structural fact"
                        )
                    observed_evidence_ids.add(evidence_id)
                missing_observation_ids = {
                    evidence_id
                    for evidence_id, receipt in structural_receipts.items()
                    if any(
                        receipt.get(field)
                        for field in (
                            "locationFacts",
                            "relationFacts",
                            "structureFacts",
                        )
                    )
                }.difference(observed_evidence_ids)
                if missing_observation_ids:
                    raise AgentOutputContractError(
                        "Agent analysis omitted exact observations for structural evidence"
                    )

            if artifact.schema_version == "4":
                if mission is None:
                    raise AgentOutputContractError(
                        "v4 engineering analysis requires its durable mission"
                    )
                mission_evidence_refs = {
                    evidence_ref
                    for step in mission["steps"]
                    for evidence_ref in step.get("evidenceRefs", [])
                }
                terminal_hypothesis_refs = {
                    evidence_ref
                    for hypothesis in artifact.hypotheses
                    if hypothesis.verification != "open"
                    for evidence_ref in hypothesis.verification_evidence_refs
                }
                if not terminal_hypothesis_refs.issubset(
                    mission_evidence_refs
                ):
                    raise AgentOutputContractError(
                        "hypothesis verification evidence is absent from the mission"
                    )
                if mission["status"] == "completed" and any(
                    hypothesis.verification == "open"
                    for hypothesis in artifact.hypotheses
                ):
                    raise AgentOutputContractError(
                        "completed mission retains an open diagnostic hypothesis"
                    )

            for location in locations:
                receipt = structural_receipts.get(location.evidence_ref)
                if receipt is None:
                    raise AgentOutputContractError(
                        "Agent engineering location is not bound to a structural receipt"
                    )
                allowed = {
                    json.dumps(
                        location_membership_value(item),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    for item in receipt["locationFacts"]
                }
                candidate = json.dumps(
                    location_value(location),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if candidate not in allowed:
                    raise AgentOutputContractError(
                        "Agent engineering location is not an exact Host receipt fact"
                    )

            for relation in relations:
                receipt = structural_receipts.get(relation.evidence_ref)
                if receipt is None:
                    raise AgentOutputContractError(
                        "Agent engineering relation is not bound to a structural receipt"
                    )
                if any(
                    location.evidence_ref != relation.evidence_ref
                    for location in relation.locations
                ):
                    raise AgentOutputContractError(
                        "Agent engineering relation mixes evidence across receipts"
                    )
                allowed = {
                    json.dumps(
                        relation_membership_value(item),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    for item in receipt["relationFacts"]
                }
                candidate = json.dumps(
                    relation_value(relation),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if candidate not in allowed:
                    raise AgentOutputContractError(
                        "Agent engineering relation is not an exact Host receipt fact"
                    )

            for item in project_structure_items:
                receipt = structural_receipts.get(item.evidence_ref)
                if (
                    receipt is None
                    or receipt["toolName"] != "project.structure_index"
                ):
                    raise AgentOutputContractError(
                        "Agent project structure fact is not bound to a structure-index receipt"
                    )
                allowed = {
                    json.dumps(
                        structure_membership_value(value),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    for value in receipt["structureFacts"]
                }
                candidate_value = item.model_dump(mode="json", by_alias=True)
                candidate_value.pop("evidenceRef", None)
                candidate = json.dumps(
                    candidate_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if candidate not in allowed:
                    raise AgentOutputContractError(
                        "Agent project structure fact is not an exact Host receipt fact"
                    )

            top_location_evidence_refs = {
                location.evidence_ref for location in locations
            }
            relation_evidence_refs = {
                relation.evidence_ref for relation in relations
            }
            structure_evidence_refs = {
                item.evidence_ref for item in project_structure_items
            }
            for evidence_id, receipt in structural_receipts.items():
                if receipt["locationFacts"] and (
                    evidence_id not in top_location_evidence_refs
                ):
                    raise AgentOutputContractError(
                        "Agent engineering analysis omitted a representative location "
                        "from structural evidence"
                    )
                if receipt["relationFacts"] and (
                    evidence_id not in relation_evidence_refs
                ):
                    raise AgentOutputContractError(
                        "Agent engineering analysis omitted a representative relation "
                        "from structural evidence"
                    )
                if receipt.get("structureFacts") and (
                    evidence_id not in structure_evidence_refs
                ):
                    raise AgentOutputContractError(
                        "Agent engineering analysis omitted a representative project "
                        "structure fact from structural evidence"
                    )

            coverage_by_area = {item.area: item for item in coverage_items}
            for coverage_item in coverage_items:
                structural_areas = {
                    str(structural_receipts[evidence_ref]["coverage"]["area"])
                    for evidence_ref in coverage_item.evidence_refs
                    if evidence_ref in structural_receipts
                }
                if structural_areas and structural_areas != {coverage_item.area}:
                    raise AgentOutputContractError(
                        "Agent engineering coverage mixes receipts from another area"
                    )

            for area, evidence_ids in structural_ids_by_area.items():
                coverage_item = coverage_by_area.get(area)
                if coverage_item is None:
                    raise AgentOutputContractError(
                        "Agent engineering analysis v2 omitted structural coverage "
                        f"for {area}"
                    )
                if len(coverage_item.evidence_refs) != len(
                    set(coverage_item.evidence_refs)
                ) or set(coverage_item.evidence_refs) != set(evidence_ids):
                    raise AgentOutputContractError(
                        "Agent engineering coverage evidenceRefs do not exactly match "
                        f"the Host receipts for {area}"
                    )
                receipts_by_scope: dict[str, dict[str, Any]] = {}
                for evidence_id in evidence_ids:
                    receipt = structural_receipts[evidence_id]
                    scope_key = str(receipt["scope"]["scopeKey"])
                    previous = receipts_by_scope.get(scope_key)
                    if previous is not None and (
                        previous["coverage"] != receipt["coverage"]
                        or previous["scope"] != receipt["scope"]
                    ):
                        raise AgentOutputContractError(
                            "Host structural evidence has conflicting duplicate scopes"
                        )
                    receipts_by_scope[scope_key] = receipt
                aggregate_receipts = list(receipts_by_scope.values())
                receipt_coverages = [
                    receipt["coverage"] for receipt in aggregate_receipts
                ]
                statuses = [str(item["status"]) for item in receipt_coverages]
                if all(status == "complete" for status in statuses):
                    expected_status = "complete"
                elif all(status == "not_observed" for status in statuses):
                    expected_status = "not_observed"
                else:
                    expected_status = "partial"
                expected_truncated = any(
                    item["truncated"] is True for item in receipt_coverages
                )
                if expected_truncated:
                    expected_status = "partial"
                unit_sets = [
                    set(receipt["scope"]["coverageUnitKeys"])
                    for receipt in aggregate_receipts
                ]
                scopes_disjoint = all(
                    unit_sets[left].isdisjoint(unit_sets[right])
                    for left in range(len(unit_sets))
                    for right in range(left + 1, len(unit_sets))
                )
                payload_sets_compatible = area in {"logic_graph", "state_model"} or (
                    len(
                        {
                            str(receipt["scope"]["payloadSetDigest"])
                            for receipt in aggregate_receipts
                        }
                    )
                    == 1
                )
                counts_additive = len(aggregate_receipts) == 1 or (
                    scopes_disjoint
                    and payload_sets_compatible
                    and all(
                        receipt["scope"]["countsAdditive"] is True
                        for receipt in aggregate_receipts
                    )
                )
                expected_observed = (
                    sum(int(item["observedCount"]) for item in receipt_coverages)
                    if counts_additive
                    else None
                )
                expected_total = (
                    sum(int(item["totalCount"]) for item in receipt_coverages)
                    if counts_additive
                    and all("totalCount" in item for item in receipt_coverages)
                    else None
                )
                expected_limitations = sorted(
                    {
                        limitation
                        for item in receipt_coverages
                        for limitation in item["limitations"]
                    }
                )
                if not counts_additive:
                    expected_status = "partial"
                    expected_limitations = sorted(
                        {
                            *expected_limitations,
                            AMBIGUOUS_STRUCTURAL_SCOPE_LIMITATION,
                        }
                    )
                if (
                    coverage_item.status != expected_status
                    or coverage_item.truncated != expected_truncated
                    or coverage_item.observed_count != expected_observed
                    or coverage_item.total_count != expected_total
                    or coverage_item.limitations != expected_limitations
                ):
                    raise AgentOutputContractError(
                        "Agent engineering coverage does not exactly aggregate its "
                        f"Host structural receipts for {area}"
                    )
        return canonical, actual_digest

    def validate_executable_plan_data(
        plan_data: dict[str, Any],
        *,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> ChangePlanContract:
        try:
            plan = _parse_change_plan(plan_data)
            plan.validate_executable_contract()
        except (TypeError, ValueError) as exc:
            raise ChangeExecutionPreconditionError(
                f"change plan does not satisfy the executable safety contract: {exc}"
            ) from exc
        if isinstance(plan, ChangePlanV2):
            baseline_binding = validate_mne_program_evidence_binding(
                plan,
                target_identity=current_change_target(),
                conversation_id=conversation_id,
                run_id=run_id,
            )
            plan = plan.model_copy(update={"baseline_binding": baseline_binding})
            plan.validate_executable_contract()
        elif isinstance(plan, (ChangePlanV3, ChangePlanV4, ChangePlanV5, ChangePlanV6)):
            baseline_binding = validate_mne_patch_evidence_binding(
                plan,
                target_identity=current_change_target(),
                conversation_id=conversation_id,
                run_id=run_id,
            )
            plan = plan.model_copy(update={"baseline_binding": baseline_binding})
            plan.validate_executable_contract()
        else:
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
        plan: ChangePlanContract,
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
            plan = _parse_change_plan(plan)
            plan.validate_executable_contract()
        except (TypeError, ValueError) as exc:
            raise ChangeExecutionPreconditionError(
                f"change plan does not satisfy the executable safety contract: {exc}"
            ) from exc
        if isinstance(plan, ChangePlanV2):
            baseline_binding = validate_mne_program_evidence_binding(
                plan,
                target_identity=target,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            plan = plan.model_copy(update={"baseline_binding": baseline_binding})
            plan.validate_executable_contract()
        elif isinstance(plan, (ChangePlanV3, ChangePlanV4, ChangePlanV5, ChangePlanV6)):
            baseline_binding = validate_mne_patch_evidence_binding(
                plan,
                target_identity=target,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            plan = plan.model_copy(update={"baseline_binding": baseline_binding})
            plan.validate_executable_contract()
        else:
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
        repaired_mission_digests = (
            store.repair_buggy_reverified_engineering_mission_digests()
        )
        if repaired_mission_digests:
            logger.warning(
                "Repaired %d canonical engineering mission digest(s) from the "
                "bounded blocked-step recovery defect",
                repaired_mission_digests,
            )
        store.mark_previous_instance_target_leases_stale()
        store.reconcile_interrupted_runs()
        store.reconcile_interrupted_change_transactions()
        native_project_repository.reconcile_interrupted()
        store.release_terminal_native_target_operation_leases()
        store.supersede_stale_target_lease_for_durable_change_recovery()

        async def start_mcp() -> None:
            nonlocal active_workspace
            try:
                await mcp.start()
            except MCPStartupError:
                # The Host and settings UI remain usable. The separate
                # integration status is unavailable and calls fail closed.
                return
            workspace = active_workspace
            workspace_id = (
                str(workspace.get("workspace_id") or "")
                if isinstance(workspace, dict)
                else ""
            )
            if not workspace_id or active_ls_controller is not None:
                return
            try:
                restored = _local_workspace_v1(
                    await mcp.call_workspace_tool(
                        "workspace.status", {"workspace_id": workspace_id}
                    )
                )
            except (MCPToolError, ValueError):
                execution_broker.clear_agent_session()
                return
            active_workspace = restored.model_dump(mode="json")
            store.set_host_state("active_workspace", active_workspace)
            bind_agent_workspace_target(restored)
            for transaction in store.list_change_transactions(limit=100):
                if (
                    transaction.receipt_acknowledged_at is None
                    or transaction.state
                    not in {
                        ChangeTransactionState.COMPLETED,
                        ChangeTransactionState.ROLLED_BACK,
                    }
                    or transaction.target.workspace_id != restored.workspace_id
                    or transaction.target.revision_id != restored.revision_id
                ):
                    continue
                try:
                    if os.path.normcase(
                        str(Path(transaction.target.project_path).resolve())
                    ) != os.path.normcase(str(Path(restored.working_copy.path).resolve())):
                        continue
                except (OSError, RuntimeError, ValueError):
                    continue
                try:
                    await refresh_terminal_change_identity(
                        transaction,
                        retain_engineering_session=False,
                    )
                except (MCPToolError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.warning(
                        "acknowledged terminal change could not be rebound at startup "
                        "(transaction=%s, kind=%s)",
                        transaction.id,
                        type(exc).__name__,
                    )
                break

        mcp_start_task = asyncio.create_task(
            start_mcp(),
            name="controlstudio-mcp-startup",
        )

        async def verify_development_model() -> None:
            nonlocal recent_model_verification
            if config.release_channel != "development":
                return
            settings = model_repository.get()
            if settings is None or settings.capability_verified_at is not None:
                return
            logger.info(
                "Development model verification started model=%s",
                settings.model,
            )
            cache_key = _model_capability_cache_key(settings)
            try:
                runtime = runtime_factory(settings, execution_broker)
                result = await runtime.test_capabilities()
                if _required_model_capabilities_ready(result):
                    model_repository.save(
                        base_url=settings.base_url,
                        model=settings.model,
                        timeout_seconds=settings.timeout_seconds,
                        api_key=settings.secrets.api_key,
                        custom_headers=settings.secrets.custom_headers,
                        capability_verified_at=result.tested_at,
                        inherit_missing_secrets=False,
                        allow_insecure_http=True,
                    )
                    logger.info(
                        "Development model verification succeeded model=%s",
                        settings.model,
                    )
                else:
                    logger.warning(
                        "Development model verification incomplete model=%s "
                        "completion=%s streaming=%s tools=%s "
                        "tool_continuation=%s structured=%s reason=%s",
                        settings.model,
                        result.completion,
                        result.streaming,
                        result.tool_calling,
                        result.tool_result_continuation,
                        result.structured_output,
                        result.message,
                    )
            except Exception as exc:
                result = ModelCapabilityResult.failed(
                    "Development 모델 기능 검사가 실패했습니다. "
                    "개발 로그와 모델 연결 설정을 확인하세요."
                )
                logger.warning(
                    "Development model verification failed model=%s error_kind=%s",
                    settings.model,
                    type(exc).__name__,
                )
            recent_model_verification = (cache_key, result)
            store.append_event(
                event_type="host.status",
                payload=current_status_v1().model_dump(mode="json", by_alias=True),
            )

        model_verification_task = asyncio.create_task(
            verify_development_model(),
            name="controlstudio-development-model-verification",
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
            if not model_verification_task.done():
                model_verification_task.cancel()
            await asyncio.gather(mcp_start_task, return_exceptions=True)
            await asyncio.gather(model_verification_task, return_exceptions=True)
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
    app.state.ssh_interactive_handoff_journal = ssh_interactive_handoff_journal
    app.state.knowledge_store = knowledge_store
    app.state.mcp = mcp
    app.state.agent_supervisor = agent_supervisor
    app.state.model_repository = model_repository
    app.state.run_tasks = run_tasks
    app.state.change_executor = change_executor
    app.state.native_project_repository = native_project_repository
    app.state.native_project_service = native_project_service
    app.state.active_change_executions = active_change_executions
    app.state.request_shutdown = None
    app.state.request_restart = None
    app.state.shutdown_event = shutdown_event
    app.state.browser_handoff_tickets = browser_handoff_tickets
    app.state.browser_sessions = browser_sessions
    app.state.browser_readiness_receipts = browser_readiness_receipts
    app.state.restart_receipts = restart_receipts
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
            "/api/launcher/readiness",
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
        for ticket, record in tuple(browser_handoff_tickets.items()):
            if record.expires_at <= now:
                browser_handoff_tickets.pop(ticket, None)
        for receipt, record in tuple(browser_readiness_receipts.items()):
            if record.expires_at <= now:
                browser_readiness_receipts.pop(receipt, None)
        for session_token, expires_at in tuple(browser_sessions.items()):
            if expires_at <= now:
                browser_sessions.pop(session_token, None)
                browser_session_readiness.pop(session_token, None)
        handoff_ticket = secrets.token_urlsafe(48)
        readiness_receipt = secrets.token_urlsafe(48)
        expires_at = now + BROWSER_HANDOFF_TTL
        browser_handoff_tickets[handoff_ticket] = _BrowserHandoffTicket(
            expires_at=expires_at,
            readiness_receipt=readiness_receipt,
        )
        browser_readiness_receipts[readiness_receipt] = _BrowserReadinessReceipt(
            expires_at=now + BROWSER_READINESS_TTL,
        )
        return LauncherHandoffV1(
            handoff_ticket=handoff_ticket,
            readiness_receipt=readiness_receipt,
            expires_at=expires_at,
        )

    @app.get(
        "/api/launcher/readiness",
        response_model=LauncherReadinessV1,
    )
    async def browser_handoff_readiness(
        request: Request,
        response: Response,
        receipt: str = Query(min_length=32, max_length=512, pattern=r"^[A-Za-z0-9_-]+$"),
    ) -> LauncherReadinessV1:
        """Consume a launcher-held receipt only after browser bootstrap succeeds.

        The opaque receipt never enters the browser URL. The browser can only
        advance it by consuming the separate one-time ticket and then making a
        session-authenticated bootstrap request. This intentionally observes
        protocol state, never a Chromium title, pixels, or accessibility tree.
        """

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
        record = browser_readiness_receipts.get(receipt)
        if record is None or record.expires_at <= now:
            browser_readiness_receipts.pop(receipt, None)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="invalid, expired, or already consumed browser readiness receipt",
            )
        if not record.session_established or not record.bootstrap_completed:
            response.status_code = status.HTTP_202_ACCEPTED
            return LauncherReadinessV1(status="pending", expires_at=record.expires_at)

        # The launcher polls this endpoint while the browser bootstraps.  Keep
        # a ready receipt available for its short TTL so a late or duplicated
        # poll observes the same completed bootstrap instead of a misleading
        # 404 after an earlier poll won the race.  The receipt remains opaque,
        # launcher-token protected, and strictly in-memory; the browser never
        # receives it.
        return LauncherReadinessV1(status="ready", expires_at=record.expires_at)

    @app.post("/api/session", response_model=BrowserSessionV1)
    async def establish_browser_session(request: Request) -> BrowserSessionV1:
        bearer = request.headers.get("authorization", "")
        supplied_ticket = bearer[7:] if bearer.startswith("Bearer ") else ""
        ticket = browser_handoff_tickets.pop(supplied_ticket, None)
        now = datetime.now(timezone.utc)
        if ticket is None or ticket.expires_at <= now:
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
        readiness = browser_readiness_receipts.get(ticket.readiness_receipt)
        if readiness is None or readiness.expires_at <= now:
            browser_sessions.pop(session_token, None)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="browser handoff readiness receipt expired before session establishment",
            )
        readiness.session_established = True
        browser_session_readiness[session_token] = ticket.readiness_receipt
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
        readiness_receipt = browser_session_readiness.pop(supplied_token, None)
        session_token = secrets.token_urlsafe(48)
        expires_at = now + BROWSER_SESSION_TTL
        browser_sessions[session_token] = expires_at
        if readiness_receipt is not None:
            browser_session_readiness[session_token] = readiness_receipt
        return BrowserSessionV1(
            session_token=session_token,
            expires_at=expires_at,
        )

    def current_status_v1() -> HostStatusV1:
        mcp_status = mcp.status
        model_connection: StoredModelConnection | None = None
        model_error: str | None = None
        try:
            model_connection = model_repository.get()
        except StoredModelSettingsError:
            model_error = "저장된 모델 설정을 읽을 수 없습니다."

        if model_error:
            agent_state = "unavailable"
            agent_detail = model_error
        elif model_connection is None:
            agent_state = "unavailable"
            agent_detail = "OpenAI 호환 모델 연결을 설정해 주세요."
        elif model_connection.capability_verified_at is None:
            matching_result = (
                recent_model_verification[1]
                if recent_model_verification is not None
                and recent_model_verification[0]
                == _model_capability_cache_key(model_connection)
                else None
            )
            agent_state = "unavailable"
            agent_detail = "저장된 모델 연결의 에이전트 기능 검사가 필요합니다."
            if matching_result is None and config.release_channel == "development":
                agent_state = "starting"
                agent_detail = "Development 모델의 에이전트 기능을 확인하고 있습니다."
            elif (
                matching_result is not None
                and not _required_model_capabilities_ready(matching_result)
            ):
                agent_detail = matching_result.message
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
        offline_mutation_configured = offline_mutation_ready()
        active_working_copy = (
            active_workspace.get("working_copy")
            if isinstance(active_workspace, dict)
            and isinstance(active_workspace.get("working_copy"), dict)
            else None
        )
        active_workspace_provenance = (
            active_workspace.get("provenance")
            if isinstance(active_workspace, dict)
            and isinstance(active_workspace.get("provenance"), dict)
            else None
        )
        offline_candidate_eligible = bool(
            mcp_status.status == "ready"
            and selected_pid_read_ready
            and not selected_pid_identity_verified
            and isinstance(active_workspace, dict)
            and active_workspace.get("source_kind") == "new_build"
            and isinstance(active_working_copy, dict)
            and isinstance(active_workspace_provenance, dict)
            and _is_canonical_uuid(active_workspace_provenance.get("native_project_id"))
            and str(active_session.get("project_hash") or "").casefold()
            == str(active_working_copy.get("current_sha256") or "").casefold()
            and _normalized_workspace_path(str(active_session.get("project_path") or ""))
            == _normalized_workspace_path(str(active_working_copy.get("path") or ""))
        )
        mne_execution_configured = offline_mutation_configured
        restore_handoff_pending = False
        try:
            pending_restore = ControllerRestoreHandoffV1.model_validate(
                store.get_host_state("controller_restore_handoff")
            )
            restore_handoff_pending = pending_restore.expires_at > datetime.now(
                timezone.utc
            )
        except (TypeError, ValueError):
            pass
        restored_workspace_active = bool(
            isinstance(active_workspace, dict)
            and active_workspace.get("source_kind") == "restored_from_controller"
            and isinstance(active_workspace.get("provenance"), dict)
        )
        engineering_target = (
            current_engineering_conversation_target()
            if mcp_status.status == "ready"
            else None
        )
        workspace_static_target = bool(
            not selected_pid_read_ready
            and engineering_target is not None
            and isinstance(active_workspace, dict)
            and active_workspace.get("workspace_id") == engineering_target[0]
            and active_workspace.get("revision_id") == engineering_target[1]
        )
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
        recovery_authority = store.selected_target_recovery_authority()
        if (
            recovery_authority is not None
            and recovery_authority.get("owner_kind") == "offline_change"
        ):
            transaction = store.get_change_transaction(
                str(recovery_authority["operation_id"])
            )
            if transaction is not None:
                active_operation = {
                    "transactionId": transaction.id,
                    "conversationId": transaction.conversation_id,
                    "state": transaction.state.value.casefold(),
                    "kind": "change",
                }
        elif (
            recovery_authority is not None
            and recovery_authority.get("owner_kind") == "native_project_create"
        ):
            native_operation = native_project_repository.blocking_operation(
                str(recovery_authority["operation_id"])
            )
            if native_operation is not None:
                simulation = native_operation.result.get("simulationVerification")
                trial = simulation.get("trial") if isinstance(simulation, dict) else None
                native_reviewed_plan = native_operation.request.get("reviewedPlan")
                native_planning_conversation_id = (
                    native_reviewed_plan.get("conversationId")
                    if isinstance(native_reviewed_plan, dict)
                    else None
                )
                active_operation = {
                    "nativeProjectId": native_operation.id,
                    "operationId": native_operation.operation_id,
                    "state": native_operation.state.casefold(),
                    "kind": "native_project_create",
                    "simulationTrialState": (
                        trial.get("state") if isinstance(trial, dict) else None
                    ),
                }
                if (
                    isinstance(native_planning_conversation_id, str)
                    and native_planning_conversation_id
                ):
                    active_operation["conversationId"] = (
                        native_planning_conversation_id
                    )
        for transaction_id in active_change_executions:
            if active_operation is not None:
                break
            transaction = store.get_change_transaction(transaction_id)
            if transaction is not None:
                active_operation = {
                    "transactionId": transaction.id,
                    "conversationId": transaction.conversation_id,
                    "state": transaction.state.value.casefold(),
                    "kind": "change",
                }
                break
        recoverable_change = (
            store.recoverable_change_transaction()
            if active_operation is None
            else None
        )
        if active_operation is None and recoverable_change is not None:
            active_operation = {
                "transactionId": recoverable_change.id,
                "conversationId": recoverable_change.conversation_id,
                "state": recoverable_change.state.value.casefold(),
                "kind": "change",
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
        pending_handoff = (
            pending_native_project_handoff(current_engineering_conversation_target())
            if active_operation is None
            else None
        )
        if active_operation is None and pending_handoff is not None:
            pending_operation, planning_conversation_id = pending_handoff
            active_operation = {
                "nativeProjectId": pending_operation.id,
                "operationId": pending_operation.operation_id,
                "conversationId": planning_conversation_id,
                "state": pending_operation.state.casefold(),
                "kind": "native_project_create",
                "simulationTrialState": "ACCEPTED",
                "label": "accepted_native_project_engineering_handoff_pending",
            }
        return HostStatusV1(
            release_channel=config.release_channel,
            product_release=config.product_release,
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
                                "원본을 보존한 working copy의 정적 구조 비교가 "
                                "준비됐습니다. LD 의미 분석은 XG5000 연결 뒤 사용할 수 있습니다."
                                if workspace_static_target
                                else (
                                    "원본을 보존한 working copy가 준비됐으며 "
                                    "XG5000 분석 세션 연결을 기다립니다."
                                    if active_workspace
                                    else "XG5000 실행 인스턴스를 선택하기 전입니다."
                                )
                            )
                        )
                    )
                ),
                process_id=(
                    int(active_session["pid"])
                    if active_session and active_session.get("pid")
                    else None
                ),
                window_handle=(
                    int(active_session["hwnd"])
                    if active_session and active_session.get("hwnd")
                    else None
                ),
                process_create_time=_engineering_session_process_create_time(active_session),
                session_id=(
                    str(active_session["session_id"])
                    if active_session and active_session.get("session_id")
                    else (engineering_target[2] if workspace_static_target else None)
                ),
                session_verified=selected_pid_identity_verified,
                read_ready=selected_pid_read_ready,
                offline_candidate_eligible=offline_candidate_eligible,
                target_workspace_id=(
                    engineering_target[0] if engineering_target is not None else None
                ),
                target_revision_id=(
                    engineering_target[1] if engineering_target is not None else None
                ),
                identity_assurance=identity_assurance,
                project_path=(
                    str(active_session["project_path"])
                    if active_session and active_session.get("project_path")
                    else None
                ),
                project_hash=(
                    str(active_session["project_hash"]).casefold()
                    if active_session and active_session.get("project_hash")
                    else None
                ),
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
                *(["native_project_create"] if mcp_status.status == "ready" else []),
                *(["online_observe"] if mcp_status.status == "ready" else []),
                *(["offline_mutate"] if offline_mutation_configured else []),
                *(
                    ["mne_full_program_replacement"]
                    if mne_execution_configured
                    else []
                ),
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
                    label="새 XG5000 제어 프로젝트 생성",
                    maturity="supported",
                    verification="real_runtime",
                    current_ready=mcp_status.status == "ready",
                    detail=(
                        "검토 완료 계획, exact XG5000 PID/HWND, 빈 destination과 "
                        "일회성 Host 승인을 결속합니다. XGK/XGB 기본 조합 프로그램의 "
                        "mnemonic readback·LD 변환·Program Check·저장 hash와 보호 "
                        "작업 복사본의 독립 새 프로세스 재열기까지 확인합니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="online_observe",
                    label="LS PLC 온라인 읽기 전용 관찰",
                    maturity="supported",
                    verification="real_runtime",
                    current_ready=mcp_status.status == "ready",
                    detail=(
                        "사용자가 지정한 사설망 endpoint에서 FEnet 상태, 지정 device와 "
                        "최대 30초의 값 변화 trace를 읽습니다. 반환 identity는 "
                        "provisional이며 쓰기·프로젝트 복원은 구조적으로 제공하지 않습니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="offline_mutate",
                    label="승인 기반 오프라인 변경",
                    maturity="preview",
                    verification="real_runtime",
                    current_ready=offline_mutation_configured,
                    detail=(
                        "동일 길이 operand 한 개를 exact plan·승인·readback·저장·복구 "
                        "journal로 처리합니다. 현재 대상 identity가 강하지 않으면 실행하지 않습니다."
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="full_program_replacement",
                    label="기존 LD 구조 변경",
                    maturity="preview",
                    verification="not_verified",
                    current_ready=(
                        mne_execution_configured
                    ),
                    capability_status="runtime_checked_at_plan",
                    detail=(
                        "전체 프로그램·명령·완전 rung 변경은 exact selected-PID 결속과 "
                        "작업공간 path/hash, code-owned adapter 전략 및 실행 직전의 live "
                        "runtime 결속을 확인한 뒤 기존 승인·readback·저장·재열기 절차로 처리합니다."
                        if mne_execution_configured
                        else (
                            "MNE 구조 변경에는 selected PID의 strong project readback과 "
                            "active workspace의 exact project path/hash 결속이 먼저 "
                            "필요합니다."
                            if not offline_mutation_configured else "MNE adapter runtime binding을 준비할 수 없습니다."
                        )
                    ),
                ),
                ProductFeatureStatusV1(
                    feature_id="controller_project_restore",
                    label="PLC에서 새 작업공간으로 프로젝트 읽기",
                    maturity="preview",
                    verification="not_verified",
                    current_ready=(
                        restore_handoff_pending or restored_workspace_active
                    ),
                    detail=(
                        "XG5000의 PLC에서 열기 대화상자에서 사용자가 연결을 선택한 뒤, "
                        "SaveAsProject 경로·파일 해시·immutable source/working copy provenance를 "
                        "검증합니다. 실제 장비 acceptance 전까지 preview입니다."
                    ),
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
        _target_lease: None = Depends(target_switch_lease),
    ) -> LSControllerObservationV1:
        nonlocal active_engineering_session, active_ls_controller
        endpoint = {
            "host": body.host,
            "port": body.port,
            "timeout_seconds": body.timeout_seconds,
        }
        observation = await observe_ls_controller(endpoint)
        # Selecting the controller is an explicit engineering-target switch.
        # Keep the durable workspace, but require an exact XG5000 reattach before
        # it can become authoritative again.
        active_engineering_session = None
        execution_broker.clear_agent_session()
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
        execution_broker.clear_agent_session()
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

    async def refresh_bound_controller_target(
        expected: tuple[str | None, str | None, str | None],
    ) -> bool:
        if not (
            all(isinstance(value, str) and value for value in expected)
            and str(expected[0]).startswith("controller-ls:")
        ):
            return False
        try:
            await ls_controller_status_v1()
        except HTTPException as exc:
            raise MCPToolError(
                "Host could not refresh the bound LS controller observation"
            ) from exc
        current = current_ls_controller_conversation_target()
        if current != expected:
            raise MCPToolError(
                "LS controller identity changed during the engineering run"
            )
        return True

    async def preflight_agent_controller_target(
        conversation_id: str,
        run_id: str,
    ) -> bool:
        """Revalidate the exact engineering target owned by this run."""

        detail = store.get_conversation(conversation_id)
        run = store.get_run(run_id)
        if (
            detail is None
            or run is None
            or run.conversation_id != conversation_id
        ):
            raise MCPToolError("controller preflight run binding is invalid")
        if detail.workflow != "engineering":
            return False
        expected = (
            detail.workspace_id,
            detail.revision_id,
            detail.engineering_session_id,
        )
        if isinstance(expected[0], str) and expected[0].startswith("controller-ls:"):
            return await refresh_bound_controller_target(expected)
        if current_engineering_conversation_target() != expected:
            raise MCPToolError(
                "XG5000 engineering target changed during the engineering run"
            )
        return False

    execution_broker.bind_controller_preflight(preflight_agent_controller_target)

    async def controller_observation_conversation(
        conversation_id: str,
    ) -> tuple[ConversationDetail, tuple[str, str, str]]:
        detail = store.get_conversation(conversation_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="engineering conversation not found",
            )
        expected = (
            detail.workspace_id,
            detail.revision_id,
            detail.engineering_session_id,
        )
        if (
            detail.workflow != "engineering"
            or not all(isinstance(value, str) and value for value in expected)
            or not str(expected[0]).startswith("controller-ls:")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the conversation is not bound to an LS controller target",
            )
        await ls_controller_status_v1()
        if current_ls_controller_conversation_target() != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the LS controller identity no longer matches the conversation",
            )
        return detail, (str(expected[0]), str(expected[1]), str(expected[2]))

    def persist_controller_observation_evidence(
        *,
        conversation: ConversationDetail,
        target: tuple[str, str, str],
        result: LSControllerReadV1 | LSControllerTraceV1,
    ) -> None:
        if result.evidence_id is None:
            raise ValueError("controller observation evidence id is missing")
        observation = result.model_dump(
            mode="json",
            by_alias=True,
            exclude={"evidence_id"},
        )
        receipt = {
            "schema": "controlstudio-controller-observation-evidence/v1",
            "toolName": result.operation,
            "conversationId": conversation.id,
            "target": {
                "workspaceId": target[0],
                "revisionId": target[1],
                "engineeringSessionId": target[2],
            },
            "observation": observation,
        }
        receipt["receiptDigest"] = _canonical_json_digest(receipt)
        if isinstance(result, LSControllerReadV1):
            title = "LS PLC 디바이스 관찰"
            facts = ", ".join(
                f"{item.variable}={item.unsigned_value}" for item in result.values
            )
            summary = f"{result.data_type} 읽기: {facts}"
        else:
            title = "LS PLC 디바이스 변화 추적"
            facts = ", ".join(
                (
                    f"{item.variable} {item.first_unsigned_value}→"
                    f"{item.last_unsigned_value} (변화 {item.change_count}회)"
                )
                for item in result.summaries
            )
            summary = f"{result.sample_count}회 추적: {facts}"
        store.append_event(
            event_type="evidence.added",
            conversation_id=conversation.id,
            payload={
                "evidenceId": result.evidence_id,
                "title": title,
                "kind": "runtime_observation",
                "toolName": result.operation,
                "summary": summary,
                "confidence": "confirmed",
                "location": {"vendor": "LS Electric"},
                "sourceAuthority": "host_validated_controller_observation",
                "sourceStatus": "read_only",
                "receipt": receipt,
            },
        )

    @app.post(
        "/api/v1/controllers/ls/read",
        response_model=LSControllerReadV1,
    )
    async def read_ls_controller_v1(
        body: ReadLSControllerRequestV1,
    ) -> LSControllerReadV1:
        conversation: ConversationDetail | None = None
        expected_target: tuple[str, str, str] | None = None
        if body.conversation_id is not None:
            conversation, expected_target = await controller_observation_conversation(
                body.conversation_id
            )
        endpoint = selected_ls_controller_endpoint()
        evidence_id = str(uuid.uuid4()) if conversation is not None else None
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
            result = LSControllerReadV1.model_validate(
                {**raw, "evidenceId": evidence_id}
            )
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
        if body.conversation_id is not None and expected_target is not None:
            _, observed_target = await controller_observation_conversation(
                body.conversation_id
            )
            if observed_target != expected_target:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="the LS controller target changed during the device read",
                )
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
        if conversation is not None and expected_target is not None:
            persist_controller_observation_evidence(
                conversation=conversation,
                target=expected_target,
                result=result,
            )
        return result

    @app.post(
        "/api/v1/controllers/ls/trace",
        response_model=LSControllerTraceV1,
    )
    async def trace_ls_controller_v1(
        body: TraceLSControllerRequestV1,
    ) -> LSControllerTraceV1:
        conversation: ConversationDetail | None = None
        expected_target: tuple[str, str, str] | None = None
        if body.conversation_id is not None:
            conversation, expected_target = await controller_observation_conversation(
                body.conversation_id
            )
        endpoint = selected_ls_controller_endpoint()
        evidence_id = str(uuid.uuid4()) if conversation is not None else None
        try:
            raw = await mcp.call_controller_tool(
                "controller.ls.trace",
                {
                    "data_type": body.data_type,
                    "variables": body.variables,
                    "sample_count": body.sample_count,
                    "interval_ms": body.interval_ms,
                },
            )
        except MCPToolError as exc:
            raise controller_gateway_error(exc) from exc
        try:
            result = LSControllerTraceV1.model_validate(
                {**raw, "evidenceId": evidence_id}
            )
            validate_controller_endpoint(result.endpoint, endpoint)
            if (
                result.data_type != body.data_type
                or result.sample_count != body.sample_count
                or result.interval_ms != body.interval_ms
            ):
                raise ValueError("MCP returned a different trace request")
            observed_variables = [item.casefold() for item in result.variables]
            expected_variables = [item.casefold() for item in body.variables]
            if observed_variables != expected_variables:
                raise ValueError("MCP traced different variables")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid read-only LS PLC device trace",
            ) from exc
        if body.conversation_id is not None and expected_target is not None:
            _, observed_target = await controller_observation_conversation(
                body.conversation_id
            )
            if observed_target != expected_target:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="the LS controller target changed during the device trace",
                )
        store.append_event(
            event_type="controller.devices_traced",
            payload={
                "vendor": "ls",
                "startedAt": result.started_at.isoformat(),
                "completedAt": result.completed_at.isoformat(),
                "dataType": result.data_type,
                "variables": result.variables,
                "sampleCount": result.sample_count,
                "intervalMs": result.interval_ms,
                "transitionCount": result.transition_count,
                "transitionsTruncated": result.transitions_truncated,
                "identityStable": result.identity_stable,
                "modeStable": result.mode_stable,
                "readOnly": True,
                "strongEnoughForWrite": False,
            },
        )
        if conversation is not None and expected_target is not None:
            persist_controller_observation_evidence(
                conversation=conversation,
                target=expected_target,
                result=result,
            )
        return result

    @app.delete(
        "/api/v1/controllers/ls/connection",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def disconnect_ls_controller_v1(
        _target_lease: None = Depends(target_switch_lease),
    ) -> Response:
        was_connected = active_ls_controller is not None
        clear_ls_controller_target()
        if was_connected and isinstance(active_workspace, dict):
            bind_agent_workspace_target(active_workspace)
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
            recovery_authority = store.selected_target_recovery_authority()
            native_operation = native_project_repository.blocking_operation(
                str(recovery_authority["operation_id"])
                if recovery_authority is not None
                and recovery_authority.get("owner_kind")
                == "native_project_create"
                else None
            )
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

    def _restart_is_safe() -> None:
        active_run_ids = [run_id for run_id, task in run_tasks.items() if not task.done()]
        if active_run_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ControlStudio cannot restart while an agent run is active",
            )
        if active_change_executions:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ControlStudio cannot restart while a change transaction is active",
            )
        recovery_authority = store.selected_target_recovery_authority()
        native_operation = native_project_repository.blocking_operation(
            str(recovery_authority["operation_id"])
            if recovery_authority is not None
            and recovery_authority.get("owner_kind") == "native_project_create"
            else None
        )
        if native_operation is not None and native_operation.state == "EXECUTING":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ControlStudio cannot restart while native XG5000 project creation is executing",
            )
        target_operation = store.active_target_operation_lease()
        if target_operation is not None and target_operation.get("state") == "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ControlStudio cannot restart while an XG5000 target operation is active",
            )

    @app.post(
        "/api/v1/host/restart",
        response_model=HostRestartResponseV1,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def request_host_restart(
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> HostRestartResponseV1:
        """Replace this Host after the response, without re-running UAC.

        The process command and environment are owned by ``__main__``.  The
        browser contributes no executable, path, command, or environment data.
        """

        async with run_submission_lock:
            _restart_is_safe()
            restart_callback = app.state.request_restart
            if not callable(restart_callback):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Host restart is not available in this runtime",
                )
            bearer = request.headers.get("authorization", "")
            session_token = bearer[7:] if bearer.startswith("Bearer ") else ""
            expires_at = browser_sessions.get(session_token)
            if expires_at is None or expires_at <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="a valid ControlStudio launch session is required",
                )
            shutdown_callback = app.state.request_shutdown
            if not callable(shutdown_callback):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Host shutdown is not available in this runtime",
                )
            receipt = secrets.token_urlsafe(32)
            handoff = RestartHandoff(receipt, session_token, expires_at)
            try:
                restart_callback(handoff)
            except RestartBootstrapError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Host restart bootstrap is unavailable",
                ) from exc
            restart_receipts[receipt] = "scheduled"
            shutdown_event.set()

        async def restart_after_response() -> None:
            await asyncio.sleep(0.05)
            shutdown_callback()

        asyncio.create_task(
            restart_after_response(),
            name="controlstudio-requested-restart",
        )
        return HostRestartResponseV1(status="scheduled", restart_receipt=receipt)

    @app.get(
        "/api/v1/host/restart/{restart_receipt}",
        response_model=HostRestartResponseV1,
    )
    async def host_restart_status(restart_receipt: str) -> HostRestartResponseV1:
        if not 32 <= len(restart_receipt) <= 512:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="restart receipt not found")
        state = restart_receipts.get(restart_receipt)
        if state is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="restart receipt not found")
        return HostRestartResponseV1(status=state, restart_receipt=restart_receipt)

    @app.get("/api/v1/bootstrap", response_model=BootstrapV1)
    async def bootstrap_v1(request: Request) -> BootstrapV1:
        bootstrap = BootstrapV1(
            status=current_status_v1(),
            csrf_token=browser_csrf_token(request),
            client_session_id=str(uuid.uuid4()),
            last_event_sequence=store.latest_event_sequence(),
        )
        bearer = request.headers.get("authorization", "")
        session_token = bearer[7:] if bearer.startswith("Bearer ") else ""
        readiness_receipt = browser_session_readiness.get(session_token)
        readiness = (
            browser_readiness_receipts.get(readiness_receipt)
            if readiness_receipt is not None
            else None
        )
        if (
            readiness is not None
            and readiness.expires_at > datetime.now(timezone.utc)
            and readiness.session_established
        ):
            readiness.bootstrap_completed = True
        return bootstrap

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationV1,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_conversation_v1(
        body: ConversationCreateV1,
        response: Response,
    ) -> ConversationV1:
        project_brief = body.project_brief.model_dump() if body.project_brief is not None else None
        if project_brief is not None:
            try:
                resolve_project_brief_attachments(project_brief)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        engineering_target = (
            current_engineering_conversation_target() if body.workflow == "engineering" else None
        )
        if body.native_project_id is not None:
            if engineering_target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "accepted native project handoff requires the current "
                        "read-ready engineering target"
                    ),
                )
            try:
                planning_conversation_id, raw_provenance = (
                    accepted_native_project_handoff(
                        body.native_project_id,
                        engineering_target,
                    )
                )
                provenance = EngineeringConversationHandoffV1.model_validate(
                    raw_provenance
                ).model_dump(mode="json", by_alias=True)
                conversation, created = store.create_engineering_handoff_conversation(
                    title=body.title,
                    native_project_id=body.native_project_id,
                    planning_conversation_id=planning_conversation_id,
                    workspace_id=engineering_target[0],
                    revision_id=engineering_target[1],
                    engineering_session_id=engineering_target[2],
                    provenance=provenance,
                )
            except (NativeProjectConflict, ProjectPlanReviewConflict, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            if not created:
                response.status_code = status.HTTP_200_OK
        else:
            conversation = store.create_conversation(
                title=body.title,
                workflow=body.workflow,
                project_brief=project_brief,
                workspace_id=(
                    engineering_target[0] if engineering_target is not None else None
                ),
                revision_id=(
                    engineering_target[1] if engineering_target is not None else None
                ),
                engineering_session_id=(
                    engineering_target[2] if engineering_target is not None else None
                ),
            )
        detail = store.get_conversation(conversation.id)
        assert detail is not None
        return _conversation_v1(
            detail,
            engineering_mission=store.get_engineering_mission(detail.id),
        )

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
            items=[
                _conversation_summary_v1(
                    item,
                    engineering_mission=store.get_engineering_mission(item.id),
                )
                for item in items
            ],
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
        project_plan_authority_digests: tuple[str, str] | None = None
        if detail.workflow == "new_project_planning":
            try:
                project_plan_authority_digests = (
                    current_project_plan_authority_digests(conversation_id)
                )
            except ProjectPlanReviewConflict:
                # Keep the conversation readable, but fail closed when its brief or
                # attached source revisions can no longer establish review authority.
                pass
        return _conversation_v1(
            detail,
            project_plan_authority_digests=project_plan_authority_digests,
            engineering_mission=store.get_engineering_mission(detail.id),
        )

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
            _brief_digest, attachment_revision_digest = (
                current_project_plan_authority_digests(conversation_id)
            )
            review, recorded = store.review_project_plan(
                conversation_id=conversation_id,
                message_id=body.message_id,
                plan_digest=body.plan_digest,
                reviewed_section_ids=body.reviewed_section_ids,
                reviewed_item_ids=body.reviewed_item_ids,
                attachment_revision_digest=attachment_revision_digest,
                actor_id=_request_actor_id(request),
            )
        except ProjectPlanReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if review is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return ProjectPlanReviewResponseV1(
            review=ProjectPlanReviewV1(
                schema_version=review.schema_version,
                authority_current=True,
                message_id=review.message_id,
                plan_digest=review.plan_digest,
                reviewed_section_ids=review.reviewed_section_ids,
                reviewed_item_ids=review.reviewed_item_ids,
                project_brief_digest=review.project_brief_digest,
                attachment_revision_digest=review.attachment_revision_digest,
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
        # This is assigned only by the current run's Host proposal callback.
        # It lets the sanitized failure response distinguish a rejected final
        # artifact from a case where the durable approval contract already exists.
        proposed_change_authority: tuple[str, str] | None = None
        try:
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise AgentRunPreconditionError("대화 기록을 찾을 수 없습니다.")
            workflow = detail.workflow
            if (
                workflow == "engineering"
                and isinstance(detail.workspace_id, str)
                and detail.workspace_id.startswith("controller-ls:")
            ):
                try:
                    await preflight_agent_controller_target(
                        conversation_id,
                        run_id,
                    )
                except MCPToolError as exc:
                    raise AgentRunPreconditionError(
                        "the bound LS controller could not be revalidated"
                    ) from exc
            if workflow == "engineering" and not engineering_conversation_matches_current_target(
                detail
            ):
                raise AgentRunPreconditionError(
                    "이 대화가 생성된 XG5000 대상과 현재 대상이 다릅니다. "
                    "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                )
            expected_engineering_target = (
                current_engineering_conversation_target()
                if workflow == "engineering"
                else None
            )
            if workflow == "engineering" and expected_engineering_target is None:
                raise AgentRunPreconditionError(
                    "the engineering run has no verified target binding"
                )
            native_engineering_status_snapshot: dict[str, Any] | None = None
            if workflow == "engineering" and detail.engineering_handoff is not None:
                assert expected_engineering_target is not None
                try:
                    native_engineering_status_snapshot = (
                        native_engineering_status_for_conversation(
                            detail,
                            expected_engineering_target,
                        )
                    )
                except (NativeProjectConflict, ProjectPlanReviewConflict, ValueError) as exc:
                    raise AgentRunPreconditionError(
                        "accepted native-project mission handoff is no longer exact"
                    ) from exc
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
            engineering_mission_continue = bool(
                request_message is not None
                and request_message.role is MessageRole.SYSTEM
                and request_message.metadata.get("engineering_mission_continue") is True
            )
            if (
                request_message is not None
                and request_message.metadata.get("engineering_mission_continue") is True
                and not engineering_mission_continue
            ):
                raise AgentRunPreconditionError(
                    "engineering mission continuation requires a Host system message"
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
            verification_scope = (
                str(request_message.metadata["verification_scope"])
                if request_message is not None
                and request_message.metadata.get("verification_scope")
                in {"offline_native_reopen", "offline_native_reopen_xgsim"}
                else "offline_native_reopen"
            )
            offline_operand_mission: dict[str, Any] | None = None
            if request_message is not None and isinstance(
                request_message.metadata.get("offline_operand_mission"), dict
            ):
                try:
                    offline_operand_mission = OfflineOperandMissionV1.model_validate(
                        request_message.metadata["offline_operand_mission"]
                    ).model_dump(mode="json", by_alias=True)
                except (TypeError, ValueError) as exc:
                    raise AgentRunPreconditionError(
                        "Host-owned offline operand mission is invalid"
                    ) from exc
                if (
                    change_request_scope != "offline_candidate_revision"
                    or verification_scope != "offline_native_reopen"
                ):
                    raise AgentRunPreconditionError(
                        "offline operand mission scope is not executable"
                    )
            planning_brief: dict[str, Any] | None = None
            planning_project_brief_digest: str | None = None
            planning_attachment_revision_digest: str | None = None
            previous_plan_snapshot = None
            if workflow == "new_project_planning":
                previous_plan_snapshot = latest_project_plan_snapshot(history)
                if detail.project_brief is not None:
                    try:
                        planning_attachments = resolve_project_brief_attachments(
                            detail.project_brief
                        )
                    except ValueError as exc:
                        raise AgentRunPreconditionError(
                            f"첨부 자료를 사용할 수 없습니다: {exc}"
                        ) from exc
                    planning_brief = {
                        **detail.project_brief,
                        "attachments": planning_attachments,
                    }
                    planning_project_brief_digest = calculate_project_brief_digest(
                        detail.project_brief
                    )
                    planning_attachment_revision_digest = (
                        calculate_project_plan_attachment_revision_digest(
                            planning_attachments
                        )
                    )
                else:
                    raise AgentRunPreconditionError(
                        "legacy planning conversation has no Host-owned project brief; start a new plan"
                    )
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

            stored_engineering_mission = (
                store.get_engineering_mission(conversation_id)
                if workflow == "engineering"
                else None
            )
            engineering_mission_snapshot: dict[str, Any] | None = None
            engineering_agent_checkpoint: dict[str, Any] | None = None
            historical_evidence_bundle: dict[str, Any] | None = None
            mission_change_transaction_id: str | None = None
            linked_change: ChangeTransaction | None = None
            linked_terminal_change_state: ChangeTransactionState | None = None
            current_change_outcome: dict[str, Any] | None = None
            current_change_outcome_evidence: dict[str, Any] | None = None
            fresh_completed_analysis_objective = False
            if stored_engineering_mission is not None:
                if (
                    stored_engineering_mission.workspace_id != detail.workspace_id
                    or stored_engineering_mission.revision_id != detail.revision_id
                ):
                    raise AgentRunPreconditionError(
                        "stored engineering mission target does not match the conversation"
                    )
                try:
                    engineering_mission_snapshot, stored_mission_digest = (
                        _canonical_engineering_mission(
                            stored_engineering_mission.engineering_mission
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise AgentRunPreconditionError(
                        "stored engineering mission is invalid"
                    ) from exc
                if stored_mission_digest != stored_engineering_mission.digest:
                    raise AgentRunPreconditionError(
                        "stored engineering mission digest does not match"
                    )
                stored_agent_checkpoint = store.get_engineering_agent_checkpoint(
                    conversation_id
                )
                if stored_agent_checkpoint is not None:
                    checkpoint_encoded = json.dumps(
                        stored_agent_checkpoint.checkpoint,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                    if (
                        stored_agent_checkpoint.workspace_id != detail.workspace_id
                        or stored_agent_checkpoint.revision_id != detail.revision_id
                        or stored_agent_checkpoint.updated_by_run_id
                        != stored_engineering_mission.updated_by_run_id
                        or stored_agent_checkpoint.mission_digest
                        != stored_engineering_mission.digest
                        or not hmac.compare_digest(
                            stored_agent_checkpoint.digest,
                            hashlib.sha256(checkpoint_encoded).hexdigest(),
                        )
                    ):
                        raise AgentRunPreconditionError(
                            "stored Agent checkpoint does not match the durable mission"
                        )
                    engineering_agent_checkpoint = stored_agent_checkpoint.checkpoint
                try:
                    historical_evidence_bundle = (
                        _historical_evidence_bundle_for_mission(
                            store,
                            conversation_id=conversation_id,
                            mission=engineering_mission_snapshot,
                            mission_digest=stored_mission_digest,
                        )
                    )
                except (AgentProtocolError, EngineeringMissionConflict) as exc:
                    raise AgentRunPreconditionError(str(exc)) from exc
                mission_change_transaction_id = (
                    stored_engineering_mission.change_transaction_id
                )
                linked_change = (
                    store.get_change_transaction(mission_change_transaction_id)
                    if mission_change_transaction_id is not None
                    else None
                )
                if mission_change_transaction_id is None:
                    recovered_link = _acknowledged_terminal_source_run_change(
                        stored_engineering_mission,
                        store.list_change_transactions(
                            conversation_id=conversation_id,
                            limit=100,
                        ),
                    )
                    if recovered_link is not None:
                        mission_change_transaction_id = recovered_link.id
                        linked_change = recovered_link
                if linked_change is not None and (
                    linked_change.conversation_id != conversation_id
                ):
                    raise AgentRunPreconditionError(
                        "stored mission change link belongs to another conversation"
                    )
                if engineering_mission_snapshot.get("status") == "waiting_for_approval":
                    if (
                        linked_change is None
                    ):
                        raise AgentRunPreconditionError(
                            "approval-waiting mission has no exact durable change link"
                        )
                    if linked_change.state not in (
                        ChangeTransactionState.PENDING_APPROVAL,
                        *TERMINAL_CHANGE_OUTCOME_DISPOSITIONS,
                    ):
                        raise AgentRunPreconditionError(
                            "linked change still requires execution, simulation, or recovery"
                        )
                elif linked_change is not None and (
                    linked_change.state not in TERMINAL_CHANGE_OUTCOME_DISPOSITIONS
                ):
                    raise AgentRunPreconditionError(
                        "non-waiting mission retains a non-terminal change"
                    )
                if (
                    linked_change is not None
                    and linked_change.state in TERMINAL_CHANGE_OUTCOME_DISPOSITIONS
                ):
                    linked_terminal_change_state = linked_change.state
                    linked_outcome = change_outcome_projection(linked_change)
                    if linked_outcome["evidenceId"] not in (
                        _engineering_mission_evidence_refs(
                            engineering_mission_snapshot
                        )
                    ):
                        current_change_outcome = linked_outcome
                        current_change_outcome_evidence = change_outcome_evidence(
                            current_change_outcome
                        )
                requested_outcome_transaction_id = (
                    request_message.metadata.get("change_outcome_transaction_id")
                    if request_message is not None
                    else None
                )
                if (
                    engineering_mission_continue
                    and requested_outcome_transaction_id is not None
                    and requested_outcome_transaction_id
                    != (
                        mission_change_transaction_id
                    )
                ):
                    raise AgentRunPreconditionError(
                        "mission continuation change outcome binding changed"
                    )
                fresh_completed_analysis_objective = (
                    not engineering_mission_continue
                    and change_request_scope == "analysis_only"
                    and engineering_mission_snapshot.get("status") == "completed"
                )
            if engineering_mission_continue and engineering_mission_snapshot is None:
                raise AgentRunPreconditionError(
                    "engineering mission continuation has no durable checkpoint"
                )
            approval_now = datetime.now(timezone.utc)
            live_change_transaction = next(
                (
                    transaction
                    for transaction in store.list_change_transactions(
                        conversation_id=conversation_id,
                        limit=100,
                    )
                    if transaction.state
                    in {
                        ChangeTransactionState.PENDING_APPROVAL,
                        ChangeTransactionState.APPROVED,
                    }
                    and transaction.approval_expires_at > approval_now
                ),
                None,
            )
            pending_change_transaction = (
                live_change_transaction
                if live_change_transaction is not None
                and live_change_transaction.state
                is ChangeTransactionState.PENDING_APPROVAL
                else None
            )
            if (
                engineering_mission_snapshot is not None
                and engineering_mission_snapshot.get("status")
                == "waiting_for_approval"
                and current_change_outcome is None
                and (
                    pending_change_transaction is None
                    or pending_change_transaction.id
                    != mission_change_transaction_id
                )
            ):
                raise AgentRunPreconditionError(
                    "approval-waiting mission no longer matches its pending change"
                )
            if fresh_completed_analysis_objective:
                # A completed analysis-only mission may be followed by a new
                # analysis objective in the same conversation.  The previous
                # mission/checkpoint and its evidence remain journaled; this
                # run starts a normal new durable mission instead of resuming it.
                engineering_mission_snapshot = None
                engineering_agent_checkpoint = None
                historical_evidence_bundle = None
                mission_change_transaction_id = None
                linked_change = None
                linked_terminal_change_state = None
                current_change_outcome = None
                current_change_outcome_evidence = None
            target_live_change_transaction = (
                store.live_change_approval_transaction(
                    mutation_scope=f"workspace:{detail.workspace_id}",
                    now=approval_now,
                )
                if detail.workspace_id is not None
                else None
            )
            pending_change_summary = (
                {
                    "transactionId": pending_change_transaction.id,
                    "state": pending_change_transaction.state.value,
                    "planDigest": pending_change_transaction.plan_digest,
                    "approvalExpiresAt": pending_change_transaction.approval_expires_at.isoformat(),
                }
                if pending_change_transaction is not None
                else None
            )
            initial_pending_change_authority = (
                (
                    pending_change_transaction.id,
                    pending_change_transaction.plan_digest,
                )
                if pending_change_transaction is not None
                else None
            )
            rejected_change_reauthorized = (
                _rejected_exact_operand_reauthorization_permitted(
                    request_message=request_message,
                    linked_change=linked_change,
                    change_outcome=current_change_outcome,
                    offline_operand_mission=offline_operand_mission,
                )
            )
            exact_operand_reproposal_permitted = (
                _expired_exact_operand_reproposal_callback_permitted(
                    engineering_mission_snapshot,
                    current_change_outcome,
                    rejected_reauthorized=rejected_change_reauthorized,
                )
            )
            internal_mne_rung_reproposal_permitted = (
                _internal_mne_rung_reproposal_callback_permitted(
                    engineering_mission_snapshot,
                    current_change_outcome,
                    linked_change,
                )
            )

            latest_mission_checkpoint: tuple[
                dict[str, Any],
                str,
                int,
                str,
                dict[str, Any],
                str,
                str | None,
            ] | None = None

            async def publish_token(delta: str) -> None:
                store.append_event(
                    event_type="agent.token",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload={"delta": delta},
                )

            async def publish_progress(stage: str, elapsed_seconds: int) -> None:
                store.append_event(
                    event_type="agent.progress",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload={
                        "stage": stage,
                        "elapsedSeconds": elapsed_seconds,
                    },
                )

            async def _persist_mission_checkpoint_impl(
                snapshot: dict[str, Any],
                digest: str,
                cycle: int,
                cycle_disposition: str,
                agent_checkpoint: dict[str, Any],
                agent_checkpoint_digest: str,
            ) -> None:
                nonlocal latest_mission_checkpoint
                if workflow != "engineering":
                    raise AgentOutputContractError(
                        "planning run returned an engineering mission checkpoint"
                    )
                assert expected_engineering_target is not None
                if expected_engineering_target[0].startswith("controller-ls:"):
                    try:
                        await preflight_agent_controller_target(
                            conversation_id,
                            run_id,
                        )
                    except MCPToolError as exc:
                        raise AgentOutputContractError(
                            "Agent controller target could not be revalidated"
                        ) from exc
                if (
                    current_engineering_conversation_target()
                    != expected_engineering_target
                ):
                    raise AgentOutputContractError(
                        "Agent engineering target changed before mission checkpoint"
                    )
                if isinstance(cycle, bool) or not isinstance(cycle, int):
                    raise AgentOutputContractError(
                        "Agent engineering mission checkpoint cycle is invalid"
                    )
                if cycle < 1 or cycle > MAX_ENGINEERING_AUTONOMOUS_CYCLES:
                    raise AgentOutputContractError(
                        "Agent engineering mission checkpoint cycle is out of bounds"
                    )
                if latest_mission_checkpoint is None:
                    if cycle != 1:
                        raise AgentOutputContractError(
                            "first Agent engineering mission checkpoint cycle must be 1"
                        )
                else:
                    previous_cycle = latest_mission_checkpoint[2]
                    previous_disposition = latest_mission_checkpoint[3]
                    if previous_disposition != "continue":
                        raise AgentOutputContractError(
                            "Agent engineering mission checkpoint followed a response"
                        )
                    if cycle != previous_cycle + 1:
                        raise AgentOutputContractError(
                            "Agent engineering mission checkpoint cycle is out of sequence"
                        )
                try:
                    canonical_snapshot, actual_digest = (
                        _canonical_engineering_mission(snapshot)
                    )
                except (TypeError, ValueError) as exc:
                    raise AgentOutputContractError(
                        "Agent engineering mission checkpoint is invalid"
                    ) from exc
                if actual_digest != digest:
                    raise AgentOutputContractError(
                        "Agent engineering mission checkpoint digest does not match"
                    )
                progression_base = (
                    latest_mission_checkpoint[0]
                    if latest_mission_checkpoint is not None
                    else engineering_mission_snapshot
                )
                current_run_evidence = store.list_run_evidence(
                    conversation_id=conversation_id,
                    run_id=run_id,
                )
                current_run_evidence_refs = {
                    evidence_id
                    for event in current_run_evidence
                    if isinstance(
                        evidence_id := event.payload.get("evidenceId"),
                        str,
                    )
                    and evidence_id
                }
                if current_change_outcome_evidence is not None:
                    current_run_evidence_refs.add(
                        str(current_change_outcome_evidence["evidenceId"])
                    )
                current_run_target_evidence_refs = {
                    evidence_id
                    for event in current_run_evidence
                    if event.payload.get("toolName") != "host.change_outcome"
                    and isinstance(
                        evidence_id := event.payload.get("evidenceId"),
                        str,
                    )
                    and evidence_id
                }
                if progression_base is not None:
                    try:
                        prior_evidence_events = store.resolve_conversation_evidence(
                            conversation_id=conversation_id,
                            evidence_ids=_engineering_mission_evidence_refs(
                                progression_base
                            ),
                        )
                        allowed_removed_evidence_refs = (
                            _authorized_exact_operand_retry_evidence_compaction(
                                progression_base,
                                canonical_snapshot,
                                retry_permitted=exact_operand_reproposal_permitted,
                                prior_evidence={
                                    evidence_id: event.payload
                                    for evidence_id, event in prior_evidence_events.items()
                                },
                                current_run_evidence={
                                    str(event.payload["evidenceId"]): event.payload
                                    for event in current_run_evidence
                                    if isinstance(
                                        event.payload.get("evidenceId"), str
                                    )
                                },
                            )
                        )
                        (
                            route_refresh_removed_evidence_refs,
                            route_refresh_reopened_step_ids,
                        ) = _authorized_exact_operand_retry_route_refresh(
                            progression_base,
                            canonical_snapshot,
                            retry_permitted=exact_operand_reproposal_permitted,
                            prior_evidence={
                                evidence_id: event.payload
                                for evidence_id, event in prior_evidence_events.items()
                            },
                            current_run_evidence={
                                str(event.payload["evidenceId"]): event.payload
                                for event in current_run_evidence
                                if isinstance(event.payload.get("evidenceId"), str)
                            },
                        )
                        _validate_engineering_mission_progression(
                            progression_base,
                            canonical_snapshot,
                            current_run_evidence_refs=current_run_evidence_refs,
                            allowed_removed_evidence_refs=(
                                allowed_removed_evidence_refs
                                | route_refresh_removed_evidence_refs
                            ),
                            allowed_reopened_step_ids=route_refresh_reopened_step_ids,
                        )
                    except (EngineeringMissionConflict, ValueError) as exc:
                        raise AgentOutputContractError(str(exc)) from exc
                mission_status = canonical_snapshot["status"]
                stop_reason = canonical_snapshot["stopReason"]
                checkpoint_evidence_refs = {
                    evidence_ref
                    for step in canonical_snapshot["steps"]
                    for evidence_ref in step.get("evidenceRefs", [])
                }
                if (
                    progression_base is None
                    and not checkpoint_evidence_refs.issubset(
                        current_run_evidence_refs
                    )
                ):
                    raise AgentOutputContractError(
                        "first engineering mission checkpoint cites evidence outside "
                        "the current Host run"
                    )
                completed_evidence_refs = {
                    evidence_ref
                    for step in canonical_snapshot["steps"]
                    if step.get("status") == "completed"
                    for evidence_ref in step.get("evidenceRefs", [])
                }
                if mission_status == "completed":
                    if not completed_evidence_refs:
                        raise AgentOutputContractError(
                            "completed engineering mission has no Host-bound evidence"
                        )
                    if (
                        linked_terminal_change_state
                        in {
                            ChangeTransactionState.COMPLETED,
                            ChangeTransactionState.ROLLED_BACK,
                        }
                        and not completed_evidence_refs.intersection(
                            current_run_target_evidence_refs
                        )
                    ):
                        raise AgentOutputContractError(
                            "an applied or rolled-back change requires fresh target "
                            "evidence before mission completion"
                        )
                try:
                    _historical_evidence_bundle_for_mission(
                        store,
                        conversation_id=conversation_id,
                        mission=canonical_snapshot,
                        mission_digest=digest,
                        supplemental_evidence=(
                            (run_id, current_change_outcome_evidence)
                            if current_change_outcome_evidence is not None
                            else None
                        ),
                    )
                except (AgentProtocolError, EngineeringMissionConflict) as exc:
                    raise AgentOutputContractError(
                        "engineering mission evidence cannot be durably resumed"
                    ) from exc
                approval_now = datetime.now(timezone.utc)
                live_pending_changes = [
                    transaction
                    for transaction in store.list_change_transactions(
                        conversation_id=conversation_id,
                        limit=100,
                    )
                    if transaction.state is ChangeTransactionState.PENDING_APPROVAL
                    and transaction.approval_expires_at is not None
                    and transaction.approval_expires_at > approval_now
                ]
                if len(live_pending_changes) > 1:
                    raise AgentOutputContractError(
                        "engineering mission has multiple live change approvals"
                    )
                checkpoint_change_transaction_id = mission_change_transaction_id
                if mission_status == "waiting_for_approval":
                    if len(live_pending_changes) != 1:
                        raise AgentOutputContractError(
                            "approval-waiting mission has no live approval authority"
                        )
                    authorized_change = (
                        proposed_change_authority
                        if proposed_change_authority is not None
                        else initial_pending_change_authority
                    )
                    live_change = live_pending_changes[0]
                    if authorized_change != (
                        live_change.id,
                        live_change.plan_digest,
                    ):
                        raise AgentOutputContractError(
                            "approval-waiting mission differs from the exact Host "
                            "change authority for this run"
                        )
                    checkpoint_change_transaction_id = live_change.id
                elif live_pending_changes:
                    raise AgentOutputContractError(
                        "a live change approval requires an approval-waiting mission"
                    )
                elif current_change_outcome is not None:
                    checkpoint_change_transaction_id = str(
                        current_change_outcome["transactionId"]
                    )
                if (
                    mission_status == "completed"
                    and change_request_scope == "offline_candidate_revision"
                    and linked_terminal_change_state is None
                ):
                    raise AgentOutputContractError(
                        "offline candidate mission completion requires an exact "
                        "terminal Host change outcome"
                    )
                if cycle_disposition == "continue" and (
                    mission_status != "active" or stop_reason != "continuing"
                ):
                    raise AgentOutputContractError(
                        "continuing mission checkpoint has an invalid stop condition"
                    )
                if (
                    cycle_disposition == "respond"
                    and mission_status == "active"
                    and (
                        stop_reason != "cycle_budget_exhausted"
                        or cycle != MAX_ENGINEERING_AUTONOMOUS_CYCLES
                    )
                ):
                    raise AgentOutputContractError(
                        "active responding mission checkpoint must exhaust the "
                        "autonomous cycle budget"
                    )
                if cycle_disposition == "continue":
                    try:
                        store.checkpoint_engineering_mission(
                            conversation_id=conversation_id,
                            run_id=run_id,
                            cycle=cycle,
                            cycle_disposition=cycle_disposition,
                            engineering_mission=canonical_snapshot,
                            digest=digest,
                            agent_checkpoint=agent_checkpoint,
                            agent_checkpoint_digest=agent_checkpoint_digest,
                            change_transaction_id=checkpoint_change_transaction_id,
                            supplemental_evidence=(
                                _matching_change_outcome_supplemental_evidence(
                                    checkpoint_change_transaction_id,
                                    current_change_outcome_evidence,
                                )
                            ),
                        )
                    except EngineeringMissionConflict as exc:
                        raise AgentOutputContractError(str(exc)) from exc
                latest_mission_checkpoint = (
                    canonical_snapshot,
                    digest,
                    cycle,
                    cycle_disposition,
                    agent_checkpoint,
                    agent_checkpoint_digest,
                    checkpoint_change_transaction_id,
                )

            async def persist_mission_checkpoint(
                snapshot: dict[str, Any],
                digest: str,
                cycle: int,
                cycle_disposition: str,
                agent_checkpoint: dict[str, Any],
                agent_checkpoint_digest: str,
            ) -> None:
                """Attach a safe Host rejection category at the callback boundary."""

                try:
                    await _persist_mission_checkpoint_impl(
                        snapshot,
                        digest,
                        cycle,
                        cycle_disposition,
                        agent_checkpoint,
                        agent_checkpoint_digest,
                    )
                except AgentOutputContractError as exc:
                    if exc.diagnostic is not None:
                        raise
                    # Never persist the exception text: it can originate in a
                    # model-authored checkpoint or a lower-level validation error.
                    # This fixed Host-owned mapping distinguishes a checkpoint
                    # rejection from a sidecar output-schema rejection without
                    # putting checkpoint data or tool arguments in the receipt.
                    validator_code = {
                        "engineering mission step lost evidence": "evidence_removed",
                        "completed engineering mission step was reopened": (
                            "completed_step_reopened"
                        ),
                        "completed engineering mission step history changed": (
                            "completed_step_history_changed"
                        ),
                        "engineering mission objective changed autonomously": (
                            "objective_changed"
                        ),
                        "engineering mission completionCriteria changed autonomously": (
                            "completion_criteria_changed"
                        ),
                        "engineering mission step was removed autonomously": "step_removed",
                        "engineering mission stepId was reused with a new title": (
                            "step_identity_changed"
                        ),
                        "engineering mission step reused historical evidence without a current observation": (
                            "historical_evidence_reused"
                        ),
                        "approval-waiting mission has no live approval authority": (
                            "approval_state"
                        ),
                        "approval-waiting mission differs from the exact Host change authority for this run": (
                            "approval_authority_mismatch"
                        ),
                        "a live change approval requires an approval-waiting mission": (
                            "approval_state"
                        ),
                        "engineering mission has multiple live change approvals": (
                            "multiple_live_approvals"
                        ),
                    }.get(str(exc), "mission_checkpoint_contract")
                    raise AgentOutputContractError(
                        str(exc),
                        diagnostic=AgentOutputContractDiagnostic(
                            stage="mission_checkpoint",
                            exception_kind="host_validation",
                            reason_code="progression_rejected",
                            validator_code=validator_code,
                            validation_errors=(),
                        ),
                    ) from exc

            async def publish_tool_event(
                event_type: str,
                payload: dict[str, Any],
            ) -> dict[str, Any] | None:
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
                request_id = payload.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("agent tool event requires request_id")
                safe_payload["request_id"] = request_id[:256]
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
                failure_reason = payload.get("failure_reason")
                if isinstance(failure_reason, str) and failure_reason:
                    safe_payload["failure_reason"] = failure_reason[:1_000]
                evidence_contract = payload.get("evidence_contract")
                if evidence_contract is not None and not isinstance(
                    evidence_contract,
                    dict,
                ):
                    raise ValueError("agent tool evidence contract must be an object")
                evidence_context = payload.get("evidence_context")
                if evidence_context is not None and not isinstance(
                    evidence_context,
                    dict,
                ):
                    raise ValueError("agent tool evidence context must be an object")
                simulator_target_status = payload.get("simulator_target_status")
                if simulator_target_status is not None and not isinstance(
                    simulator_target_status, dict
                ):
                    raise ValueError("simulator target status must be an object")
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
                        evidence_contract=evidence_contract,
                        evidence_context=evidence_context,
                        simulator_target_status=simulator_target_status,
                    )
                    if evidence is not None:
                        store.append_event(
                            event_type="evidence.added",
                            conversation_id=conversation_id,
                            run_id=run_id,
                            payload=evidence,
                        )
                        return evidence
                return None

            if workflow == "engineering" and historical_evidence_bundle is not None:
                # Historical evidence passed to the model is deliberately
                # projected/scrubbed and may omit Host-only binding fields.
                # The rebind guard must instead consume the exact journaled
                # receipts selected by this durable mission.
                assert engineering_mission_snapshot is not None
                try:
                    retained_hardware_evidence = (
                        _journaled_hardware_context_evidence_for_mission(
                            store,
                            conversation_id=conversation_id,
                            mission=engineering_mission_snapshot,
                        )
                    )
                except EngineeringMissionConflict as exc:
                    raise AgentRunPreconditionError(str(exc)) from exc
                execution_broker.bind_retained_hardware_context_evidence(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    evidence=retained_hardware_evidence,
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
            mne_change_planner_available = False
            if (
                callable(bind_change_planner)
                and workflow == "engineering"
                and (
                    current_change_outcome is None
                    or _expired_exact_operand_reproposal_callback_permitted(
                        engineering_mission_snapshot,
                        current_change_outcome,
                        rejected_reauthorized=rejected_change_reauthorized,
                    )
                    or internal_mne_rung_reproposal_permitted
                )
                and change_request_scope != "analysis_only"
                and isinstance(knowledge_workspace_snapshot, dict)
                and _engineering_session_identity_verified(engineering_session_snapshot)
                and target_live_change_transaction is None
            ):
                # Candidate compilation and the approval-only contract are safe
                # offline planning operations.  Keep them available when the
                # developer-certified execution bundle is absent; execute still
                # performs the independent valid-bundle preflight below.
                mne_change_planner_available = offline_mutation_ready()

                async def propose_change(**values: Any) -> dict[str, Any]:
                    nonlocal proposed_change_authority
                    if not engineering_conversation_matches_current_target(detail):
                        raise AgentRunPreconditionError(
                            "변경 계약을 만들기 전에 대상이 바뀌었습니다. "
                            "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                        )
                    try:
                        _reject_private_change_plan_material(values)
                        plan_kind = values.get("plan_kind")
                        patch_contract = MNE_PATCH_PLAN_CONTRACTS.get(plan_kind)
                        if plan_kind in MNE_STRUCTURAL_PLAN_KINDS and values.get(
                            "verification_scope"
                        ) != verification_scope:
                            raise ValueError(
                                "MNE proposal verification_scope must equal the explicit user selection"
                            )
                        if (
                            plan_kind not in MNE_STRUCTURAL_PLAN_KINDS
                            and values.get("verification_scope")
                            != "offline_native_reopen"
                        ):
                            raise ValueError(
                                "legacy operand proposal verification scope is fixed"
                            )
                        if (
                            verification_scope == "offline_native_reopen_xgsim"
                            and (patch_contract is None or not patch_contract.supports_xgsim)
                        ):
                            raise ValueError(
                                "XG-SIM verification is only supported for V6 MNE rung patches"
                            )
                        if plan_kind in MNE_STRUCTURAL_PLAN_KINDS and not offline_mutation_ready():
                            raise AgentRunPreconditionError(
                                "MNE structural change planning is not currently available"
                            )
                        if plan_kind == "mne_full_program_replacement":
                            expected_fields = {
                                "plan_kind",
                                "intent",
                                "risk_level",
                                "change_mode",
                                "target_program",
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
                            if set(values) != expected_fields:
                                raise ValueError(
                                    "full-program proposal callback fields are invalid"
                                )
                            target_program = values["target_program"]
                            if not isinstance(target_program, dict) or set(
                                target_program
                            ) != {"program_id", "program_name", "instructions"}:
                                raise ValueError(
                                    "target_program must contain only exact identity "
                                    "and typed instructions"
                                )
                            plan: ChangePlanContract = ChangePlanV2(
                                plan_kind=plan_kind,
                                change_mode=values["change_mode"],
                                request_scope=change_request_scope,
                                target_program=target_program,
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
                        elif patch_contract is not None:
                            expected_fields = {
                                "plan_kind", "intent", "risk_level", "change_mode",
                                "candidate_evidence_id", "impact", "risks", "assumptions",
                                "blocking_unknowns", "alternatives", "acceptance_criteria",
                                "observation_signals", "stop_conditions", "coverage",
                                "verification_scope", "validation", "manual_follow_up", "rollback",
                            }
                            if set(values) != expected_fields:
                                raise ValueError("MNE patch proposal callback fields are invalid")
                            candidate = mne_patch_candidate_for_run(
                                plan_kind=plan_kind,
                                conversation_id=conversation_id,
                                run_id=run_id,
                                evidence_id=str(values["candidate_evidence_id"]),
                            )
                            plan = patch_contract.plan_model(
                                plan_kind=plan_kind,
                                change_mode=values["change_mode"],
                                request_scope=change_request_scope,
                                candidate=candidate,
                                candidate_evidence_id=values["candidate_evidence_id"],
                                impact=values["impact"], risks=values["risks"],
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
                        elif plan_kind is None:
                            if offline_operand_mission is not None:
                                _require_offline_operand_mission_plan(
                                    offline_operand_mission, values
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
                                acceptance_criteria=values[
                                    "acceptance_criteria"
                                ],
                                observation_signals=values[
                                    "observation_signals"
                                ],
                                stop_conditions=values["stop_conditions"],
                                coverage=values["coverage"],
                                verification_scope=values[
                                    "verification_scope"
                                ],
                                validation=values["validation"],
                                manual_follow_up=values["manual_follow_up"],
                                rollback=values["rollback"],
                            )
                        else:
                            raise ValueError(
                                f"unsupported Agent change plan kind: {plan_kind}"
                            )
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AgentRunPreconditionError(
                            f"Agent change proposal is invalid: {exc}"
                        ) from exc
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
                    returned_authority = (transaction.id, transaction.plan_digest)
                    if (
                        proposed_change_authority is not None
                        and proposed_change_authority != returned_authority
                    ):
                        raise AgentRunPreconditionError(
                            "변경 계약 callback이 같은 run에서 다른 권한을 반환했습니다."
                        )
                    proposed_change_authority = returned_authority
                    response = {
                        "transaction_id": transaction.id,
                        "state": transaction.state.value,
                        "plan_digest": transaction.plan_digest,
                        "approval_expires_at": (transaction.approval_expires_at.isoformat()),
                        "created": created,
                        "execution_permitted": False,
                        "next_action": "human_approval_required",
                    }
                    if isinstance(plan, ChangePlanV2):
                        response.update(
                            {
                                "plan_kind": plan.plan_kind,
                                "instruction_count": (
                                    plan.target_program.instruction_count
                                ),
                                "design_digest": plan.target_program.design_digest,
                                "target_program_digest": (
                                    plan.target_program.target_program_digest
                                ),
                            }
                        )
                    elif isinstance(
                        plan,
                        (ChangePlanV3, ChangePlanV4, ChangePlanV5, ChangePlanV6),
                    ):
                        response.update(
                            {
                                "plan_kind": plan.plan_kind,
                                "instruction_count": plan.candidate.target_instruction_count,
                                "design_digest": plan.candidate.design_digest,
                                "target_program_digest": plan.candidate.target_program_digest,
                                "candidate_digest": plan.candidate.candidate_digest,
                            }
                        )
                    return response

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
                    else {
                        "workflow": "engineering",
                        **(
                            engineering_session_snapshot
                            if _engineering_session_read_ready(
                                engineering_session_snapshot
                            )
                            else {}
                        ),
                        "change_request_scope": change_request_scope,
                        "mne_change_planner_available": mne_change_planner_available,
                        "verification_scope": verification_scope,
                        **(
                            {
                                "internal_mne_rung_reproposal_permitted": True
                            }
                            if internal_mne_rung_reproposal_permitted
                            else {}
                        ),
                        **(
                            {"offline_operand_mission": offline_operand_mission}
                            if offline_operand_mission is not None
                            else {}
                        ),
                        **(
                            {"engineering_mission": engineering_mission_snapshot}
                            if engineering_mission_snapshot is not None
                            else {}
                        ),
                        **(
                            {"historical_evidence": historical_evidence_bundle}
                            if historical_evidence_bundle is not None
                            else {}
                        ),
                        **(
                            {
                                "native_engineering_status": (
                                    native_engineering_status_snapshot
                                )
                            }
                            if native_engineering_status_snapshot is not None
                            else {}
                        ),
                        **(
                            {"pending_change": pending_change_summary}
                            if pending_change_summary is not None
                            else {}
                        ),
                        **(
                            {"change_outcome": current_change_outcome}
                            if current_change_outcome is not None
                            else {}
                        ),
                    }
                ),
                agent_checkpoint=engineering_agent_checkpoint,
                on_token=publish_token,
                on_tool_event=publish_tool_event,
                on_progress=publish_progress,
                on_mission_checkpoint=persist_mission_checkpoint,
            )
            final_mission: dict[str, Any] | None = None
            if workflow == "engineering":
                if (
                    output.engineering_mission is None
                    or output.digest is None
                    or output.cycle_disposition is None
                    or latest_mission_checkpoint is None
                ):
                    raise AgentOutputContractError(
                        "engineering run did not return a durable mission checkpoint"
                    )
                try:
                    final_mission, final_mission_digest = (
                        _canonical_engineering_mission(output.engineering_mission)
                    )
                except (TypeError, ValueError) as exc:
                    raise AgentOutputContractError(
                        "Agent final engineering mission is invalid"
                    ) from exc
                if final_mission_digest != output.digest:
                    raise AgentOutputContractError(
                        "Agent final engineering mission digest does not match"
                    )
                if output.cycle_disposition != "respond":
                    raise AgentOutputContractError(
                        "Agent final engineering mission must stop its cycle loop"
                    )
                if (
                    final_mission,
                    output.digest,
                    output.cycle_disposition,
                ) != (
                    latest_mission_checkpoint[0],
                    latest_mission_checkpoint[1],
                    latest_mission_checkpoint[3],
                ):
                    raise AgentOutputContractError(
                        "Agent final engineering mission differs from its last checkpoint"
                    )
            elif (
                output.engineering_mission is not None
                or output.digest is not None
                or output.cycle_disposition is not None
                or latest_mission_checkpoint is not None
            ):
                raise AgentOutputContractError(
                    "planning run returned engineering mission state"
                )
            plan_revision: dict[str, Any] | None = None
            if workflow == "new_project_planning":
                try:
                    current_plan_artifact = validate_project_plan_artifact(
                        output.plan_artifact,
                        output.plan_digest,
                        require_current_ready_contract=True,
                    )
                except (TypeError, ValueError) as exc:
                    raise AgentOutputContractError(str(exc)) from exc
                assert isinstance(output.plan_digest, str)
                if (
                    planning_project_brief_digest is None
                    or planning_attachment_revision_digest is None
                ):
                    raise AgentOutputContractError(
                        "new executable plan requires a current Host project brief"
                    )
                try:
                    artifact_attachment_digest = (
                        calculate_project_plan_attachment_revision_digest(
                            current_plan_artifact.get("attachments", [])
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise AgentOutputContractError(
                        "project plan attachments do not match the trusted source contract"
                    ) from exc
                if (
                    artifact_attachment_digest
                    != planning_attachment_revision_digest
                ):
                    raise AgentOutputContractError(
                        "project plan attachment revisions differ from the Host context"
                    )
                if current_plan_artifact.get("status") == "ready_for_review":
                    try:
                        project_plan_review_item_ids(current_plan_artifact)
                        validate_project_plan_scope_evidence(
                            current_plan_artifact,
                            detail.project_brief,
                        )
                    except (TypeError, ValueError) as exc:
                        raise AgentOutputContractError(
                            "ready project plan lacks exact executable review authority"
                        ) from exc
                if previous_plan_snapshot is not None:
                    plan_revision = build_project_plan_revision(
                        previous=previous_plan_snapshot,
                        current_plan_digest=output.plan_digest,
                        current_artifact=current_plan_artifact,
                    )
                    ProjectPlanRevisionDeltaV1.model_validate(plan_revision)
            engineering_analysis, engineering_analysis_digest = (
                validate_engineering_analysis_artifact(
                    output.engineering_analysis,
                    output.engineering_analysis_digest,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    mission=final_mission,
                    prior_mission=engineering_mission_snapshot,
                    current_outcome_evidence=current_change_outcome_evidence,
                )
            )
            assistant_message_metadata = {
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
                            "project_brief_digest": planning_project_brief_digest,
                            "attachment_revision_digest": (
                                planning_attachment_revision_digest
                            ),
                            **(
                                {"plan_revision": plan_revision}
                                if plan_revision is not None
                                else {}
                            ),
                        }
                        if (
                            workflow == "new_project_planning"
                            and output.plan_artifact is not None
                            and output.plan_digest is not None
                        )
                        else {}
                    ),
                    **(
                        {
                            "engineering_analysis": engineering_analysis,
                            "engineering_analysis_digest": engineering_analysis_digest,
                        }
                        if (
                            workflow == "engineering"
                            and engineering_analysis is not None
                            and engineering_analysis_digest is not None
                        )
                        else {}
                    ),
                    **(
                        {"engineering_mission_digest": output.digest}
                        if workflow == "engineering" and output.digest is not None
                        else {}
                    ),
                }
            if workflow == "engineering":
                assert final_mission is not None
                assert output.digest is not None
                assert latest_mission_checkpoint is not None
                try:
                    _durable_mission, assistant_message = (
                        store.checkpoint_engineering_mission(
                            conversation_id=conversation_id,
                            run_id=run_id,
                            cycle=latest_mission_checkpoint[2],
                            cycle_disposition="respond",
                            engineering_mission=final_mission,
                            digest=output.digest,
                            agent_checkpoint=latest_mission_checkpoint[4],
                            agent_checkpoint_digest=latest_mission_checkpoint[5],
                            change_transaction_id=latest_mission_checkpoint[6],
                            supplemental_evidence=(
                                _matching_change_outcome_supplemental_evidence(
                                    latest_mission_checkpoint[6],
                                    current_change_outcome_evidence,
                                )
                            ),
                            final_message_content=output.content,
                            final_message_metadata=assistant_message_metadata,
                        )
                    )
                except EngineeringMissionConflict as exc:
                    raise AgentOutputContractError(str(exc)) from exc
            else:
                assistant_message = store.append_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=output.content,
                    metadata=assistant_message_metadata,
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
                diagnostic = exc.diagnostic
                retained_current_run_change_contract = (
                    proposed_change_authority is not None
                )
                if diagnostic is None:
                    if retained_current_run_change_contract:
                        logger.warning(
                            "agent run %s failed final structured output processing; "
                            "a Host approval-pending change contract from this run "
                            "was retained",
                            run_id,
                        )
                    else:
                        logger.warning(
                            "agent run %s failed the structured output contract; "
                            "no model-authored plan was accepted",
                            run_id,
                        )
                else:
                    validation_codes = ",".join(
                        (
                            ".".join(str(item) for item in issue.loc) or "<root>"
                        )
                        + ":"
                        + issue.error_type
                        for issue in diagnostic.validation_errors
                    )
                    if retained_current_run_change_contract:
                        logger.warning(
                            "agent run %s failed final structured output processing "
                            "after a Host approval-pending change contract from this run "
                            "was retained (stage=%s, exception_kind=%s, reason_code=%s, "
                            "validator_code=%s, validation_errors=%s)",
                            run_id,
                            diagnostic.stage,
                            diagnostic.exception_kind,
                            diagnostic.reason_code,
                            diagnostic.validator_code,
                            validation_codes or "none",
                        )
                    else:
                        logger.warning(
                            "agent run %s failed the structured output contract "
                            "(stage=%s, exception_kind=%s, reason_code=%s, "
                            "validator_code=%s, validation_errors=%s); "
                            "no model-authored plan was accepted",
                            run_id,
                            diagnostic.stage,
                            diagnostic.exception_kind,
                            diagnostic.reason_code,
                            diagnostic.validator_code,
                            validation_codes or "none",
                        )
                if retained_current_run_change_contract:
                    user_message = (
                        "Host가 이 실행에서 만든 승인 대기 변경 계약은 유지됩니다. "
                        "다만 최종 응답의 근거·안전 출력 계약 처리에는 실패했습니다. "
                        "이 상태는 실제 변경이 실행되었거나 성공했음을 뜻하지 않습니다."
                    )
                elif (
                    diagnostic is not None
                    and diagnostic.stage == "engineering_analysis"
                ):
                    user_message = (
                        "분석 응답을 완료하지 못했습니다. 확인된 도구 실행 결과는 "
                        "실행 기록에 남아 있습니다."
                    )
                else:
                    user_message = (
                        "모델 응답이 ControlStudio의 근거·안전 출력 계약을 충족하지 "
                        "못해 계획을 저장하지 않았습니다. 실제 변경은 수행되지 "
                        "않았습니다. 요구사항을 더 구체화하거나 다른 모델로 다시 "
                        "시도해 주세요."
                    )
                error_code = "agent_output_contract"
                error_detail = (
                    "Final response processing failed the structured evidence/safety "
                    "contract after the Host created an approval-pending change contract "
                    "for this run; the retained contract was not invalidated. No execution "
                    "or mutation outcome is implied."
                    if retained_current_run_change_contract
                    else (
                        "Model output failed the structured evidence/safety contract; "
                        "no model-authored plan or mutation was accepted."
                    )
                )
                if diagnostic is not None:
                    validation_codes = ",".join(
                        (
                            ".".join(str(item) for item in issue.loc) or "<root>"
                        )
                        + ":"
                        + issue.error_type
                        for issue in diagnostic.validation_errors
                    )
                    error_detail += (
                        " Safe diagnostic: "
                        f"stage={diagnostic.stage}; "
                        f"exceptionKind={diagnostic.exception_kind}; "
                        f"reasonCode={diagnostic.reason_code}; "
                        f"validatorCode={diagnostic.validator_code}; "
                        f"validationErrors={validation_codes or 'none'}."
                    )
            elif isinstance(exc, AgentRuntimeError):
                diagnostic = exc.diagnostic
                if diagnostic is None:
                    logger.error(
                        "agent run %s failed in the private runtime; diagnostic missing",
                        run_id,
                    )
                    user_message = "에이전트 내부 실행이 완료되지 않았습니다."
                    error_code = "agent_runtime"
                    error_detail = "Agent runtime failed without an accepted diagnostic."
                else:
                    logger.error(
                        "agent run %s failed in the private runtime "
                        "(stage=%s, exception_kind=%s, reason_code=%s)",
                        run_id,
                        diagnostic.stage,
                        diagnostic.exception_kind,
                        diagnostic.reason_code,
                    )
                    user_message = _agent_runtime_user_message(diagnostic)
                    error_code = f"agent_runtime_{diagnostic.reason_code}"
                    error_detail = (
                        "Agent runtime failed with a safe diagnostic: "
                        f"stage={diagnostic.stage}; "
                        f"exceptionKind={diagnostic.exception_kind}; "
                        f"reasonCode={diagnostic.reason_code}."
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
            existing_run = store.get_run_by_operation_id(body.operation_id)
            if existing_run is not None:
                if existing_run.conversation_id != conversation_id:
                    raise HTTPException(
                        status_code=409,
                        detail="operationId belongs to another conversation",
                    )
                return SendMessageResponseV1(
                    run_id=existing_run.id,
                    operation_id=body.operation_id,
                    state=existing_run.state.value.casefold(),
                )
            effective_change_request_scope: str | None = None
            effective_verification_scope = "offline_native_reopen"
            if body.workflow == "engineering":
                effective_change_request_scope = body.change_request_scope or "analysis_only"
                effective_verification_scope = body.verification_scope
            offline_operand_mission = (
                body.offline_operand_mission.model_dump(mode="json", by_alias=True)
                if body.offline_operand_mission is not None
                else None
            )
            project_brief = (
                body.project_brief.model_dump() if body.project_brief is not None else None
            )
            if project_brief is not None:
                try:
                    resolve_project_brief_attachments(project_brief)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
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
                and isinstance(detail.workspace_id, str)
                and detail.workspace_id.startswith("controller-ls:")
            ):
                try:
                    await refresh_bound_controller_target(
                        (
                            detail.workspace_id,
                            detail.revision_id,
                            detail.engineering_session_id,
                        )
                    )
                except MCPToolError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="the bound LS controller could not be revalidated",
                    ) from exc
            if detail.workflow == "engineering":
                terminal_mission = store.get_engineering_mission(conversation_id)
                if terminal_mission is not None:
                    try:
                        terminal_snapshot, terminal_digest = (
                            _canonical_engineering_mission(
                                terminal_mission.engineering_mission
                            )
                        )
                    except (TypeError, ValueError) as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="stored engineering mission is invalid",
                        ) from exc
                    if terminal_digest != terminal_mission.digest:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="stored engineering mission digest does not match",
                        )
                    allows_fresh_completed_analysis = (
                        body.workflow == "engineering"
                        and effective_change_request_scope == "analysis_only"
                        and terminal_snapshot["status"] == "completed"
                    )
                    if (
                        terminal_snapshot["status"] in {"completed", "blocked"}
                        and not allows_fresh_completed_analysis
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "terminal engineering missions are read-only; "
                                "start a new engineering conversation for a new objective"
                            ),
                        )
            if (
                detail.workflow == "engineering"
                and not engineering_conversation_matches_current_target(detail)
            ):
                current_target = current_engineering_conversation_target()
                mission = store.get_engineering_mission(conversation_id)
                mission_target_matches = bool(
                    body.workflow == "engineering"
                    and current_target is not None
                    and mission is not None
                    and current_target[:2]
                    == (mission.workspace_id, mission.revision_id)
                )
                if mission_target_matches:
                    assert current_target is not None and mission is not None
                    try:
                        _mission_snapshot, mission_digest = (
                            _canonical_engineering_mission(
                                mission.engineering_mission
                            )
                        )
                    except (TypeError, ValueError) as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="stored engineering mission is invalid",
                        ) from exc
                    if mission_digest != mission.digest:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="stored engineering mission digest does not match",
                        )
                    try:
                        detail = store.rebind_engineering_conversation_session(
                            conversation_id=conversation_id,
                            workspace_id=mission.workspace_id,
                            revision_id=mission.revision_id,
                            engineering_session_id=current_target[2],
                            reason="engineering_message_submission",
                        )
                    except EngineeringMissionConflict as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=str(exc),
                        ) from exc
                    mission_target_matches = (
                        engineering_conversation_matches_current_target(detail)
                    )
                if not mission_target_matches:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "이 대화가 생성된 XG5000 대상과 현재 대상이 다릅니다. "
                            "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                        ),
                    )
            if (
                detail.workflow == "engineering"
                and effective_change_request_scope != "analysis_only"
                and not offline_mutation_ready()
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "offline candidate work requires an exact source-preserving "
                        "workspace and strong selected-PID XG5000 binding"
                    ),
                )
            try:
                request_message, run, created = store.create_user_message_and_run(
                    conversation_id=conversation_id,
                    content=authoritative_content,
                    operation_id=body.operation_id,
                    workflow=body.workflow,
                    project_brief=project_brief,
                    run_metadata=(
                        {
                            "change_request_scope": effective_change_request_scope,
                            "verification_scope": effective_verification_scope,
                            **(
                                {"offline_operand_mission": offline_operand_mission}
                                if offline_operand_mission is not None
                                else {}
                            ),
                        }
                        if effective_change_request_scope is not None
                        else None
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
                        **(
                            {"verification_scope": effective_verification_scope}
                            if body.workflow == "engineering"
                            else {}
                        ),
                        **(
                            {"offline_operand_mission": offline_operand_mission}
                            if offline_operand_mission is not None
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
        "/api/v1/conversations/{conversation_id}/engineering-mission/reverify-blocked-step",
        response_model=EngineeringMissionV1,
    )
    async def reverify_blocked_engineering_mission_step_v1(
        conversation_id: str,
        body: EngineeringMissionBlockedStepReverifyRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> EngineeringMissionV1:
        """User-only recovery for a false-negative blocked read step.

        This changes no PLC/XG5000 state and deliberately creates no Agent run.
        A later explicit user action remains necessary to continue the mission.
        """

        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key is not None and idempotency_key != body.operation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key must match operationId",
            )
        async with run_submission_lock:
            if shutdown_event.is_set():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="ControlStudio Host is shutting down",
                )
            if any(not task.done() for task in run_tasks.values()):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="blocked mission step cannot be reverified while an agent run is active",
                )
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
            if detail.workflow != "engineering":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="blocked mission reverify requires an engineering conversation",
                )
            current_target = current_engineering_conversation_target()
            if current_target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="engineering target is not read-ready",
                )
            mission = store.get_engineering_mission(conversation_id)
            if mission is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="conversation has no durable engineering mission",
                )
            if current_target[:2] != (mission.workspace_id, mission.revision_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="current engineering target differs from the mission target",
                )
            try:
                store.rebind_engineering_conversation_session(
                    conversation_id=conversation_id,
                    workspace_id=mission.workspace_id,
                    revision_id=mission.revision_id,
                    engineering_session_id=current_target[2],
                )
                recovered, _created = store.reverify_blocked_engineering_mission_step(
                    conversation_id=conversation_id,
                    operation_id=body.operation_id,
                    completed_report=body.completed_report,
                )
            except EngineeringMissionConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            return _engineering_mission_v1(recovered)

    @app.post(
        "/api/v1/conversations/{conversation_id}/engineering-mission/resume",
        response_model=SendMessageResponseV1,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_engineering_mission_v1(
        conversation_id: str,
        body: EngineeringMissionResumeRequestV1,
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
            existing_run = store.get_run_by_operation_id(body.operation_id)
            if existing_run is not None:
                if existing_run.conversation_id != conversation_id:
                    raise HTTPException(
                        status_code=409,
                        detail="operationId belongs to another conversation",
                    )
                return SendMessageResponseV1(
                    run_id=existing_run.id,
                    operation_id=body.operation_id,
                    state=existing_run.state.value.casefold(),
                )
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            if detail.workflow != "engineering":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="engineering mission resume requires an engineering conversation",
                )
            if (
                isinstance(detail.workspace_id, str)
                and detail.workspace_id.startswith("controller-ls:")
            ):
                try:
                    await refresh_bound_controller_target(
                        (
                            detail.workspace_id,
                            detail.revision_id,
                            detail.engineering_session_id,
                        )
                    )
                except MCPToolError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="the bound LS controller could not be revalidated",
                    ) from exc
            mission = store.get_engineering_mission(conversation_id)
            if mission is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="conversation has no durable engineering mission",
                )
            try:
                mission_snapshot, mission_digest = _canonical_engineering_mission(
                    mission.engineering_mission
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="stored engineering mission is invalid",
                ) from exc
            if mission_digest != mission.digest:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="stored engineering mission digest does not match",
                )
            mission_status = mission_snapshot["status"]
            change_outcome_transaction_id: str | None = None
            if mission_status == "waiting_for_approval":
                linked_change = (
                    store.get_change_transaction(mission.change_transaction_id)
                    if mission.change_transaction_id is not None
                    else None
                )
                if (
                    linked_change is None
                    or linked_change.conversation_id != conversation_id
                    or linked_change.state
                    not in TERMINAL_CHANGE_OUTCOME_DISPOSITIONS
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "approval-waiting mission can resume only after its "
                            "exact change reaches a terminal outcome"
                        ),
                    )
                change_outcome_transaction_id = linked_change.id
            elif mission_status != "active":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="only an active engineering mission can be resumed",
                )
            mission_run = store.get_run(mission.updated_by_run_id)
            mission_request_message = (
                next(
                    (
                        message
                        for message in detail.messages
                        if message.id == mission_run.request_message_id
                    ),
                    None,
                )
                if mission_run is not None
                and mission_run.conversation_id == conversation_id
                and mission_run.request_message_id is not None
                else None
            )
            mission_scope = (
                mission_run.metadata.get("change_request_scope")
                if mission_run is not None
                and mission_run.conversation_id == conversation_id
                else None
            )
            if mission_scope is None and mission_request_message is not None:
                mission_scope = mission_request_message.metadata.get(
                    "change_request_scope"
                )
            continuation_scope = (
                "offline_candidate_revision"
                if mission_scope == "offline_candidate_revision"
                else "analysis_only"
            )
            mission_verification_scope = (
                mission_run.metadata.get("verification_scope")
                if mission_run is not None
                and mission_run.conversation_id == conversation_id
                else None
            )
            if (
                mission_verification_scope is None
                and mission_request_message is not None
            ):
                mission_verification_scope = mission_request_message.metadata.get(
                    "verification_scope"
                )
            verification_scope = (
                str(mission_verification_scope)
                if mission_verification_scope
                in {"offline_native_reopen", "offline_native_reopen_xgsim"}
                else "offline_native_reopen"
            )

            current_target = current_engineering_conversation_target()
            if current_target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="engineering target is not read-ready",
                )
            if current_target[:2] != (mission.workspace_id, mission.revision_id):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="current engineering target differs from the mission target",
                )
            try:
                store.rebind_engineering_conversation_session(
                    conversation_id=conversation_id,
                    workspace_id=mission.workspace_id,
                    revision_id=mission.revision_id,
                    engineering_session_id=current_target[2],
                )
            except EngineeringMissionConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

            internal_mne_rung_reproposal_permitted = (
                change_outcome_transaction_id is not None
                and _internal_mne_rung_reproposal_callback_permitted(
                    mission_snapshot,
                    change_outcome_projection(linked_change),
                    linked_change,
                )
            )
            continuation_prompt = (
                "Continue the current durable engineering mission from its saved "
                "checkpoint. The Host authorized only the internal-M MNE rung path "
                "to compile the current baseline and request one newly bound "
                "proposal; do not revive the terminal approval."
                if internal_mne_rung_reproposal_permitted
                else (
                    "Continue the current durable engineering mission from its saved "
                    "checkpoint. Inspect the Host-bound terminal change outcome, bind "
                    "it to the affected mission step, and continue verification or "
                    "close the objective without proposing the same change again."
                    if change_outcome_transaction_id is not None
                    else (
                        "Continue the current durable engineering mission from its "
                        "saved checkpoint. Execute the next justified read-only "
                        "investigation steps, retain evidence, and stop only at an "
                        "explicit mission condition."
                    )
                )
            )
            try:
                request_message, run, created = store.create_user_message_and_run(
                    conversation_id=conversation_id,
                    content=continuation_prompt,
                    operation_id=body.operation_id,
                    workflow="engineering",
                    message_role=MessageRole.SYSTEM,
                    run_metadata={
                        "engineering_mission_continue": True,
                        "change_request_scope": continuation_scope,
                        "verification_scope": verification_scope,
                        **(
                            {
                                "change_outcome_transaction_id": (
                                    change_outcome_transaction_id
                                )
                            }
                            if change_outcome_transaction_id is not None
                            else {}
                        ),
                    },
                    message_metadata={
                        "operation_id": body.operation_id,
                        "actor_id": _request_actor_id(request),
                        "workflow": "engineering",
                        "change_request_scope": continuation_scope,
                        "verification_scope": verification_scope,
                        "engineering_mission_continue": True,
                        **(
                            {
                                "change_outcome_transaction_id": (
                                    change_outcome_transaction_id
                                )
                            }
                            if change_outcome_transaction_id is not None
                            else {}
                        ),
                    },
                )
            except (ConversationActiveRunConflict, ConversationWorkflowConflict) as exc:
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
                    prompt=continuation_prompt,
                ),
                name=f"controlstudio-agent-{run.id}",
            )
            run_tasks[run.id] = task

            def finish_mission_run_task(completed_task: asyncio.Task[None]) -> None:
                run_tasks.pop(run.id, None)
                if not completed_task.cancelled():
                    completed_task.exception()

            task.add_done_callback(finish_mission_run_task)
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
        "/api/v1/change-transactions/{transaction_id}/focus-target",
        response_model=ChangeTargetFocusV1,
    )
    async def focus_change_transaction_target_v1(
        transaction_id: str,
        _csrf: None = Depends(require_browser_csrf),
    ) -> ChangeTargetFocusV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if current_change_target() != transaction.target:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="change target is not the current verified XG5000 target",
            )
        try:
            plan = validate_executable_plan_data(
                transaction.plan,
                conversation_id=transaction.conversation_id,
                run_id=transaction.run_id,
            )
        except ChangeExecutionPreconditionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        recomputed_plan_digest = calculate_plan_digest(
            target=transaction.target,
            risk_level=transaction.risk_level,
            intent=transaction.intent,
            plan=plan.model_dump(mode="json"),
        )
        if not hmac.compare_digest(recomputed_plan_digest, transaction.plan_digest):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stored change plan digest is not Host-canonical",
            )

        if isinstance(plan, ChangePlanV1):
            target = plan.target
            program_id = str(target.get("programId", target.get("program_id")) or "")
            program_name = str(
                target.get("programName", target.get("program_name")) or ""
            )
            token_offset = target.get("tokenOffset", target.get("token_offset"))
            visual_row = target.get("visualRow", target.get("visual_row"))
            current_value = str(plan.current.get("value", plan.current.get("operand")) or "")
            try:
                raw_readback = await mcp.call_read_tool(
                    "program.read_ld",
                    {
                        "program_id": program_id,
                        "program_name": program_name,
                        "use_cache": False,
                    },
                )
            except MCPToolError as exc:
                raise engineering_gateway_error(exc) from exc
            if not isinstance(raw_readback, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP returned an invalid native program readback",
                )
            try:
                readback_program = engineering_program_from_mcp(
                    raw_readback.get("program")
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP returned an invalid native program identity",
                ) from exc
            payload_sha256 = raw_readback.get("payload_sha256")
            if (
                raw_readback.get("verified") is not True
                or raw_readback.get("stable_repeat") is not True
                or raw_readback.get("project_unchanged") is not True
                or readback_program.program_id != program_id
                or not isinstance(payload_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP did not verify the current native program payload",
                )
            location = EngineeringLocationV2(
                evidence_ref=str(
                    target.get(
                        "analysisEvidenceRef",
                        target.get("analysis_evidence_ref"),
                    )
                ),
                program=readback_program.name,
                network_id=str(target.get("networkId", target.get("network_id"))),
                network_index=target.get(
                    "networkIndex",
                    target.get("network_index"),
                ),
                rung_id=str(target.get("rungId", target.get("rung_id"))),
                visual_row=visual_row,
                token_offset=token_offset,
                device=current_value,
                access=(
                    "condition"
                    if target.get("rungRole", target.get("rung_role")) == "condition"
                    else "action"
                ),
                semantic_status="structured",
            )
            focused = await focus_exact_native_location(
                location=location,
                program_id=program_id,
                program_name=program_name,
                expected_payload_sha256=payload_sha256,
            )
            return ChangeTargetFocusV1(
                verified=True,
                read_only=True,
                project_unchanged=True,
                transaction_id=transaction.id,
                plan_digest=transaction.plan_digest,
                focus_kind="native_location",
                program=focused.program,
                location=focused.location,
                payload_sha256=focused.payload_sha256,
            )

        if isinstance(plan, (ChangePlanV3, ChangePlanV4, ChangePlanV5, ChangePlanV6)):
            candidate = plan.candidate
            if isinstance(plan, ChangePlanV3):
                instruction_index = candidate.patch.output_instruction_index
                command = "OUT"
                operand = candidate.patch.output_operand
                instruction_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "index": instruction_index,
                            "command": command,
                            "operand": operand,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
            else:
                anchor = (
                    candidate.patches[0].anchor
                    if isinstance(plan, ChangePlanV5)
                    else candidate.patch.anchor
                )
                instruction_index = anchor.index
                command = anchor.command
                operand = anchor.operand
                instruction_digest = anchor.instruction_digest
            try:
                raw_focus = await mcp.call_read_tool(
                    "program.focus_mne_anchor",
                    {
                        "program_id": candidate.program_id,
                        "program_name": candidate.program_name,
                        "instruction_index": instruction_index,
                        "expected_command": command,
                        "expected_operand": operand,
                        "expected_instruction_digest": instruction_digest,
                        "expected_mne_sha256": candidate.baseline_mne_sha256,
                        "expected_ld_sha256": candidate.baseline_ld_sha256,
                        "expected_ld_canonical_sha256": candidate.baseline_ld_canonical_sha256,
                        "expected_instruction_count": candidate.baseline_instruction_count,
                    },
                )
            except MCPToolError as exc:
                raise engineering_gateway_error(exc) from exc
            if not isinstance(raw_focus, dict):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP returned an invalid mnemonic focus receipt",
                )
            try:
                focused_program = engineering_program_from_mcp(raw_focus.get("program"))
                focused_anchor = MneAnchorFocusV1.model_validate(
                    {
                        "instruction_index": raw_focus.get("instruction_index"),
                        "command": raw_focus.get("command"),
                        "operand": raw_focus.get("operand"),
                        "instruction_digest": raw_focus.get("instruction_digest"),
                        "baseline_mne_sha256": raw_focus.get("baseline_mne_sha256"),
                        "baseline_ld_sha256": raw_focus.get("baseline_ld_sha256"),
                        "baseline_ld_canonical_sha256": raw_focus.get(
                            "baseline_ld_canonical_sha256"
                        ),
                        "baseline_instruction_count": candidate.baseline_instruction_count,
                    }
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP returned an invalid mnemonic anchor receipt",
                ) from exc
            if (
                raw_focus.get("verified") is not True
                or raw_focus.get("read_only") is not True
                or raw_focus.get("project_unchanged") is not True
                or focused_program.program_id != candidate.program_id
                or focused_program.name != candidate.program_name
                or focused_anchor.instruction_index != instruction_index
                or focused_anchor.command != command
                or focused_anchor.operand != operand
                or not hmac.compare_digest(
                    focused_anchor.instruction_digest,
                    instruction_digest,
                )
                or not hmac.compare_digest(
                    focused_anchor.baseline_mne_sha256,
                    candidate.baseline_mne_sha256,
                )
                or not hmac.compare_digest(
                    str(focused_anchor.baseline_ld_canonical_sha256 or ""),
                    str(candidate.baseline_ld_canonical_sha256 or ""),
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="MCP did not verify the approved mnemonic anchor",
                )
            return ChangeTargetFocusV1(
                verified=True,
                read_only=True,
                project_unchanged=True,
                transaction_id=transaction.id,
                plan_digest=transaction.plan_digest,
                focus_kind="mne_anchor",
                program=focused_program,
                mne_anchor=focused_anchor,
            )

        program_id = plan.target_program.program_id
        focused_program = await focus_engineering_program_v1(program_id, None)
        return ChangeTargetFocusV1(
            verified=True,
            read_only=True,
            project_unchanged=True,
            transaction_id=transaction.id,
            plan_digest=transaction.plan_digest,
            focus_kind="program",
            program=focused_program.program,
        )

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
        candidate = store.get_change_transaction(transaction_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        proposing_task = (
            run_tasks.get(candidate.run_id) if candidate.run_id is not None else None
        )
        if proposing_task is not None and not proposing_task.done():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="change decision must wait for the proposing Agent run",
            )
        if body.decision == "approve":
            try:
                validated_plan = validate_executable_plan_data(
                    candidate.plan,
                    conversation_id=candidate.conversation_id,
                    run_id=candidate.run_id,
                )
            except ChangeExecutionPreconditionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            recomputed_plan_digest = calculate_plan_digest(
                target=candidate.target,
                risk_level=candidate.risk_level,
                intent=candidate.intent,
                plan=validated_plan.model_dump(mode="json"),
            )
            if not hmac.compare_digest(
                recomputed_plan_digest,
                candidate.plan_digest,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="stored change plan digest is not Host-canonical",
                )
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
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> ChangeExecutionResponseV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if transaction.plan_digest != body.expected_plan_digest:
            raise HTTPException(status_code=409, detail="plan digest does not match")
        try:
            _parse_change_plan(transaction.plan)
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
                    verification_evidence=exc.evidence or None,
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
            await refresh_terminal_change_identity(
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
        "/api/v1/change-transactions/{transaction_id}/simulation-decision",
        response_model=ChangeExecutionResponseV1,
    )
    async def finalize_simulation_change_v1(
        transaction_id: str,
        body: FinalizeSimulationChangeRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> ChangeExecutionResponseV1:
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if (
            transaction.plan_digest != body.expected_plan_digest
            or transaction.plan.get("verification_scope") != "offline_native_reopen_xgsim"
        ):
            raise HTTPException(status_code=409, detail="simulation decision does not match the observing contract")
        existing_finalization = transaction.result.get("simulation_finalization")
        if transaction.state in {ChangeTransactionState.COMPLETED, ChangeTransactionState.ROLLED_BACK}:
            if (
                isinstance(existing_finalization, dict)
                and existing_finalization.get("decision") == body.decision
                and existing_finalization.get("expected_plan_digest") == body.expected_plan_digest
                and existing_finalization.get("expected_simulation_receipt_digest")
                == body.expected_simulation_receipt_digest
            ):
                return ChangeExecutionResponseV1(
                    transaction=_change_transaction_v1(transaction), execution_started=False
                )
            raise HTTPException(status_code=409, detail="simulation decision was already resolved differently")
        if transaction.state is not ChangeTransactionState.OBSERVING:
            raise HTTPException(status_code=409, detail="change is not awaiting a simulation decision")
        if transaction_id in active_change_executions:
            raise HTTPException(status_code=409, detail="simulation decision is already in progress")
        current_target = current_change_target()
        for field_name in (
            "workspace_id", "revision_id", "source_artifact_hash", "project_path",
            "vendor", "engineering_system", "engineering_session_id",
            "engineering_process_id", "engineering_window_handle",
        ):
            if getattr(current_target, field_name) != getattr(transaction.target, field_name):
                raise HTTPException(status_code=409, detail="active workspace or engineering session changed")
        active_change_executions.add(transaction_id)
        try:
            try:
                outcome = await change_executor.finalize_observing(
                    transaction,
                    decision=body.decision,
                    expected_plan_digest=body.expected_plan_digest,
                    expected_simulation_receipt_digest=body.expected_simulation_receipt_digest,
                )
            except (MCPToolError, ChangeExecutionPreconditionError, ValueError, TypeError) as exc:
                # No blind retry after a native response loss or uncertain boundary.
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            finalized = store.finalize_observing_change(
                transaction_id=transaction_id,
                expected_plan_digest=body.expected_plan_digest,
                expected_simulation_receipt_digest=body.expected_simulation_receipt_digest,
                state=outcome.state,
                evidence=outcome.evidence,
                actor_id=_request_actor_id(request),
                decision=body.decision,
            )
            assert finalized is not None
            await refresh_terminal_change_identity(
                finalized,
                retain_engineering_session=True,
            )
            publish_change_event("change.transaction.updated", finalized)
            store.append_event(
                event_type="host.status",
                payload=current_status_v1().model_dump(mode="json", by_alias=True),
            )
            return ChangeExecutionResponseV1(
                transaction=_change_transaction_v1(finalized), execution_started=False
            )
        except ChangeStoreConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            active_change_executions.discard(transaction_id)

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/close-idle-instance",
        response_model=CloseIdleXG5000InstanceResponseV1,
    )
    async def close_idle_xg5000_instance_v1(
        transaction_id: str,
        body: CloseIdleXG5000InstanceRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> CloseIdleXG5000InstanceResponseV1:
        """Close one exact blank XG5000 instance without changing the recovery target."""

        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if transaction.plan_digest != body.expected_plan_digest:
            raise HTTPException(status_code=409, detail="plan digest does not match")
        if transaction.state is not ChangeTransactionState.QUARANTINED:
            raise HTTPException(
                status_code=409,
                detail="only a quarantined change transaction can close an idle XG5000 instance",
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

        arguments = {
            "pid": body.pid,
            "hwnd": body.hwnd,
            "process_create_time": body.process_create_time,
            "confirm_idle_close": True,
            "timeout_seconds": body.timeout_seconds,
        }
        try:
            raw = await mcp.call_workspace_tool("xg5000.close_idle_instance", arguments)
            if not isinstance(raw, dict):
                raise TypeError("MCP idle-instance close receipt is not an object")
            receipt_digest = _canonical_json_digest(raw)
            if raw.get("status") == "needs_attention":
                outcome: CloseIdleXG5000InstanceResponseV1 | CloseIdleXG5000InstanceAttentionV1 = (
                    _close_idle_xg5000_instance_attention(
                        raw,
                        receipt_digest=receipt_digest,
                    )
                )
            else:
                outcome = _close_idle_xg5000_instance_response(
                    raw,
                    receipt_digest=receipt_digest,
                )
            binding = outcome.idle_binding
            if (
                binding.get("pid") != body.pid
                or binding.get("hwnd") != body.hwnd
                or not isinstance(binding.get("process_create_time"), (int, float))
                or isinstance(binding.get("process_create_time"), bool)
                or abs(float(binding["process_create_time"]) - body.process_create_time)
                >= 0.001
                or not isinstance(binding.get("executable"), str)
                or not binding["executable"]
            ):
                raise ValueError("MCP idle-instance close receipt binding does not match")
            if isinstance(outcome, CloseIdleXG5000InstanceAttentionV1):
                store.append_event(
                    event_type="xg5000.idle_instance_close_needs_attention",
                    conversation_id=transaction.conversation_id,
                    run_id=transaction.run_id,
                    payload={
                        "actorId": _request_actor_id(request),
                        "transactionId": transaction.id,
                        "planDigest": transaction.plan_digest,
                        "idleBinding": dict(binding),
                        "receiptDigest": outcome.receipt_digest,
                        "attentionReason": outcome.attention_reason,
                        "closeAction": outcome.close_action,
                        "promptObserved": outcome.prompt_observed,
                        "modalObserved": outcome.modal_observed,
                        "noSavePromptClicked": False,
                        "processTerminated": False,
                    },
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "idle_xg5000_needs_attention",
                        "status": outcome.status,
                        "attentionReason": outcome.attention_reason,
                        "idleBinding": dict(binding),
                        "closeAction": outcome.close_action,
                        "promptObserved": outcome.prompt_observed,
                        "modalObserved": outcome.modal_observed,
                        "noSavePromptClicked": False,
                        "processExited": False,
                        "processTerminated": False,
                    },
                )
            completed = outcome
            store.append_event(
                event_type="xg5000.idle_instance_closed",
                conversation_id=transaction.conversation_id,
                run_id=transaction.run_id,
                payload={
                    "actorId": _request_actor_id(request),
                    "transactionId": transaction.id,
                    "planDigest": transaction.plan_digest,
                    "idleBinding": dict(binding),
                    "receiptDigest": completed.receipt_digest,
                    "closeAction": "wm_close",
                    "noSavePromptClicked": False,
                    "processTerminated": False,
                },
            )
            return completed
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail="the idle XG5000 instance could not be verified closed",
            ) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid idle XG5000 close receipt",
            ) from exc
        finally:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="idle_instance_close_request_finished",
            )

    @app.post(
        "/api/v1/change-transactions/{transaction_id}/reconcile",
        response_model=ChangeReconciliationResponseV1,
    )
    async def reconcile_change_transaction_v1(
        transaction_id: str,
        body: ReconcileChangeTransactionRequestV1,
        request: Request,
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> ChangeReconciliationResponseV1:
        nonlocal active_engineering_session, active_workspace
        transaction = store.get_change_transaction(transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
        if transaction.plan_digest != body.expected_plan_digest:
            raise HTTPException(status_code=409, detail="plan digest does not match")
        try:
            _parse_change_plan(transaction.plan)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"stored change plan is invalid: {exc}",
            ) from exc
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
        restoration_receipt: dict[str, Any] | None = None
        try:
            current_target = current_change_target()
            stable_workspace_fields = (
                "workspace_id",
                "revision_id",
                "source_artifact_hash",
                "vendor",
                "engineering_system",
            )
            try:
                same_project_path = (
                    _normalized_workspace_path(current_target.project_path)
                    == _normalized_workspace_path(transaction.target.project_path)
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise ChangeExecutionPreconditionError(
                    "active recovery workspace path is invalid"
                ) from exc
            if not same_project_path or any(
                getattr(current_target, field_name) != getattr(transaction.target, field_name)
                for field_name in stable_workspace_fields
            ):
                raise ChangeExecutionPreconditionError("active recovery workspace identity changed")
            if body.restore_verified_backup:
                if (
                    body.expected_current_project_hash is None
                    or current_target.artifact_hash.casefold()
                    != body.expected_current_project_hash.casefold()
                ):
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace hash differs from the reviewed current artifact"
                    )
                lookup = await mcp.call_recovery_tool(
                    "change.transaction_lookup",
                    {"host_transaction_id": transaction.id},
                )
                checkpoint = (
                    lookup.get("transaction_checkpoint")
                    if isinstance(lookup, dict)
                    else None
                )
                native_transaction_id = (
                    checkpoint.get("transaction_id")
                    if isinstance(checkpoint, dict)
                    else None
                )
                if (
                    not isinstance(native_transaction_id, str)
                    or not native_transaction_id
                    or not isinstance(checkpoint.get("plan_digest"), str)
                    or re.fullmatch(
                        r"[0-9a-f]{64}", str(checkpoint["plan_digest"]).casefold()
                    ) is None
                    or checkpoint.get("workspace_id") != transaction.target.workspace_id
                ):
                    raise ChangeExecutionPreconditionError(
                        "native recovery checkpoint differs from the quarantined transaction"
                    )
                native_plan_digest = str(checkpoint["plan_digest"]).casefold()
                restored = await mcp.call_recovery_tool(
                    "change.restore_verified_quarantined_backup",
                    {
                        "host_transaction_id": transaction.id,
                        "transaction_id": native_transaction_id,
                        "expected_plan_digest": native_plan_digest,
                        "workspace_id": transaction.target.workspace_id,
                        "expected_current_project_hash": body.expected_current_project_hash,
                    },
                )
                restoration_receipt = _validated_verified_quarantined_backup_restore(
                    restored,
                    transaction=transaction,
                    expected_current_project_hash=body.expected_current_project_hash,
                    expected_native_transaction_id=native_transaction_id,
                    expected_native_plan_digest=native_plan_digest,
                )
                # This is a new durable recovery event, not a rewrite of the
                # original quarantined transaction receipt.  It must survive a
                # later relaunch failure or request cancellation.
                store.append_event(
                    event_type="change.working_copy_restored",
                    payload={
                        "transactionId": transaction.id,
                        "hostPlanDigest": transaction.plan_digest,
                        "nativeTransactionId": native_transaction_id,
                        "nativePlanDigest": native_plan_digest,
                        "workspaceId": transaction.target.workspace_id,
                        "restore": restoration_receipt,
                    },
                    conversation_id=transaction.conversation_id,
                )
                if not isinstance(active_workspace, dict):
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace disappeared after verified backup restoration"
                    )
                refreshed_workspace = dict(active_workspace)
                refreshed_working_copy = active_workspace.get("working_copy")
                if not isinstance(refreshed_working_copy, dict):
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace has no working-copy identity after restoration"
                    )
                refreshed_working_copy = dict(refreshed_working_copy)
                refreshed_working_copy["current_sha256"] = transaction.target.artifact_hash.casefold()
                refreshed_working_copy["state"] = (
                    "clean"
                    if transaction.target.artifact_hash.casefold()
                    == str(refreshed_working_copy.get("baseline_sha256") or "").casefold()
                    else "modified"
                )
                refreshed_workspace["working_copy"] = refreshed_working_copy
                # MCP has proved no XG5000 was open during the atomic file restore;
                # any retained live session therefore cannot describe this artifact.
                active_engineering_session = None
                execution_broker.clear_agent_session()
                active_workspace = refreshed_workspace
                store.set_host_state("active_workspace", active_workspace)
                bind_agent_workspace_target(active_workspace)
            recovery_target = ChangeRecoveryTarget(pid=body.pid, hwnd=body.hwnd)
            if body.relaunch_if_unavailable:
                try:
                    recovery_workspace = _local_workspace_v1(active_workspace)
                except (TypeError, ValueError) as exc:
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace is invalid"
                    ) from exc
                recovery_target = await _relaunch_unavailable_change_recovery_target(
                    mcp,
                    transaction,
                    recovery_workspace,
                    requested_pid=body.pid,
                    requested_hwnd=body.hwnd,
                )
        except (ChangeExecutionPreconditionError, MCPToolError) as exc:
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
                    recovery_target,
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
            if outcome is not None and restoration_receipt is not None:
                outcome.evidence["working_copy_restore"] = restoration_receipt
            resolved = store.resolve_quarantined_change(
                transaction_id=transaction_id,
                state=(
                    outcome.state if outcome is not None else ChangeTransactionState.QUARANTINED
                ),
                evidence=(
                    outcome.evidence
                    if outcome is not None
                    else {
                        "error": "reconciliation service failed unexpectedly",
                        **(
                            {"working_copy_restore": restoration_receipt}
                            if restoration_receipt is not None
                            else {}
                        ),
                    }
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
                await refresh_terminal_change_identity(
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
        try:
            value = model_repository.get()
        except StoredModelSettingsError as exc:
            raise HTTPException(
                status_code=503,
                detail="stored model settings are unavailable",
            ) from exc
        if value is None:
            raise HTTPException(status_code=404, detail="model is not configured")
        return _model_settings_v1(value)

    @app.put("/api/v1/settings/model", response_model=ModelSettingsV1)
    async def save_model_settings_v1(
        body: SaveModelSettingsRequestV1,
    ) -> ModelSettingsV1:
        nonlocal recent_model_verification
        try:
            existing = model_repository.get()
            candidate = _ephemeral_model_settings(body, existing)
        except ModelSettingsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StoredModelSettingsError as exc:
            raise HTTPException(
                status_code=503,
                detail="stored model settings are unavailable",
            ) from exc
        cache_key = _model_capability_cache_key(candidate)
        result = (
            recent_model_verification[1]
            if recent_model_verification is not None
            and recent_model_verification[0] == cache_key
            and _required_model_capabilities_ready(recent_model_verification[1])
            and datetime.now(timezone.utc) - recent_model_verification[1].tested_at
            <= timedelta(minutes=5)
            else None
        )
        if result is None:
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
        except (ModelSettingsError, StoredModelSettingsError) as exc:
            raise HTTPException(
                status_code=503,
                detail="could not store verified model settings",
            ) from exc
        return _model_settings_v1(value)

    @app.post(
        "/api/v1/settings/model/test",
        response_model=ModelCapabilityResultV1,
    )
    async def test_model_settings_v1(
        body: SaveModelSettingsRequestV1,
    ) -> ModelCapabilityResultV1:
        nonlocal recent_model_verification
        existing: StoredModelConnection | None = None
        try:
            existing = model_repository.get()
        except StoredModelSettingsError:
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
        recent_model_verification = (
            _model_capability_cache_key(candidate),
            result,
        )
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
                                locator=(
                                    f"{report.file_name}#"
                                    f"{page.locator_fragment or 'page=' + str(page.page_number)}"
                                ),
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
                            "record_unit": report.record_unit,
                            "column_headers": list(report.column_headers),
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
        workspace = active_workspace
        working_copy = workspace.get("working_copy") if isinstance(workspace, dict) else None
        bound_workspace_id: str | None = None
        if (
            isinstance(working_copy, dict)
            and isinstance(workspace.get("workspace_id"), str)
            and os.path.normcase(
                str(Path(str(working_copy.get("path") or "")).resolve())
            )
            == os.path.normcase(
                str(Path(str(active_engineering_session.get("project_path") or "")).resolve())
            )
            and str(working_copy.get("current_sha256") or "").casefold()
            == str(active_engineering_session.get("project_hash") or "").casefold()
        ):
            bound_workspace_id = str(workspace["workspace_id"])
        if _engineering_session_read_ready(active_engineering_session):
            broker_session = dict(active_engineering_session)
            if bound_workspace_id is not None:
                broker_session.update(workspace_agent_binding(workspace))
            execution_broker.bind_agent_session(broker_session)
        elif bound_workspace_id is not None:
            bind_agent_workspace_target(workspace)
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

    async def focus_exact_native_location(
        *,
        location: EngineeringLocationV2,
        program_id: str,
        program_name: str,
        expected_payload_sha256: str,
    ) -> EngineeringLocationFocusV1:
        if location.visual_row is None or location.token_offset is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="engineering location has no exact native row and token offset",
            )
        arguments: dict[str, Any] = {
            "program_id": program_id,
            "program_name": program_name,
            "value_token_offset": location.token_offset,
            "visual_row": location.visual_row,
            "expected_payload_sha256": expected_payload_sha256,
        }
        if location.device is not None:
            arguments["expected_device"] = location.device
        try:
            raw_result = await mcp.call_read_tool(
                "program.focus_location",
                arguments,
            )
        except MCPToolError as exc:
            raise engineering_gateway_error(exc) from exc
        if not isinstance(raw_result, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid engineering location focus result",
            )
        raw_location = raw_result.get("location")
        viewport = raw_result.get("viewport")
        try:
            program = engineering_program_from_mcp(raw_result.get("program"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid focused program identity",
            ) from exc
        if (
            raw_result.get("verified") is not True
            or raw_result.get("read_only") is not True
            or raw_result.get("project_unchanged") is not True
            or raw_result.get("stable_repeat") is not True
            or raw_result.get("payload_sha256") != expected_payload_sha256
            or program.program_id != program_id
            or program.name != location.program
            or not isinstance(raw_location, dict)
            or raw_location.get("visual_row") != location.visual_row
            or raw_location.get("value_token_offset") != location.token_offset
            or (
                location.device is not None
                and raw_location.get("device") != location.device
            )
            or not isinstance(viewport, dict)
            or viewport.get("source_visible") is not True
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP did not verify the exact engineering evidence location",
            )
        return EngineeringLocationFocusV1(
            verified=True,
            read_only=True,
            project_unchanged=True,
            program=program,
            location=location,
            payload_sha256=expected_payload_sha256,
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

    @app.post(
        (
            "/api/v1/conversations/{conversation_id}/messages/{message_id}"
            "/engineering-location/focus"
        ),
        response_model=EngineeringLocationFocusV1,
    )
    async def focus_engineering_location_v1(
        conversation_id: str,
        message_id: str,
        body: EngineeringLocationFocusRequestV1,
        _csrf: None = Depends(require_browser_csrf),
    ) -> EngineeringLocationFocusV1:
        require_engineering_read_target()
        detail = store.get_conversation(conversation_id)
        if (
            detail is None
            or detail.workflow != "engineering"
            or not engineering_conversation_matches_current_target(detail)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="engineering conversation is not bound to the current XG5000 target",
            )
        messages = [item for item in detail.messages if item.id == message_id]
        if len(messages) != 1 or messages[0].role is not MessageRole.ASSISTANT:
            raise HTTPException(status_code=404, detail="engineering analysis message not found")
        message = messages[0]
        analysis_value = message.metadata.get("engineering_analysis")
        supplied_analysis_digest = message.metadata.get("engineering_analysis_digest")
        run_id = message.metadata.get("run_id")
        if (
            not isinstance(analysis_value, dict)
            or not isinstance(supplied_analysis_digest, str)
            or not isinstance(run_id, str)
            or not run_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="message has no durable engineering analysis authority",
            )
        try:
            analysis = EngineeringAnalysisArtifactV1.model_validate(analysis_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stored engineering analysis is invalid",
            ) from exc
        canonical_analysis = analysis.model_dump(mode="json", by_alias=True)
        if _canonical_json_digest(canonical_analysis) != supplied_analysis_digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="stored engineering analysis digest does not match",
            )
        analysis_locations = list(analysis.locations or []) + [
            location
            for relation in analysis.relations or []
            for location in relation.locations
        ]
        requested_location = body.location.model_dump(mode="json", by_alias=True)
        if not any(
            location.model_dump(mode="json", by_alias=True) == requested_location
            for location in analysis_locations
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="requested location is not present in the stored engineering analysis",
            )
        evidence = [
            event.payload
            for event in store.list_run_evidence(
                conversation_id=conversation_id,
                run_id=run_id,
            )
            if event.payload.get("evidenceId") == body.location.evidence_ref
        ]
        if len(evidence) != 1 or not isinstance(evidence[0].get("toolName"), str):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="engineering location evidence is unavailable",
            )
        try:
            receipt = validate_structural_evidence_contract(
                evidence[0].get("receipt"),
                expected_tool=structural_receipt_tool_name(
                    str(evidence[0]["toolName"])
                ),
            )
        except (TypeError, ValueError, MCPToolError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="engineering location evidence receipt is invalid",
            ) from exc
        requested_fact = dict(requested_location)
        requested_fact.pop("evidenceRef", None)
        matching_facts = [
            fact
            for fact in receipt["locationFacts"]
            if location_membership_value(fact) == requested_fact
        ]
        payload_bindings = [
            binding
            for binding in receipt["scope"]["payloadBindings"]
            if binding["program"] == body.location.program
        ]
        if (
            len(matching_facts) != 1
            or len(payload_bindings) != 1
            or body.location.visual_row is None
            or body.location.token_offset is None
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="engineering location does not have one exact native payload binding",
            )
        binding = payload_bindings[0]
        return await focus_exact_native_location(
            location=body.location,
            program_id=str(binding["programId"]),
            program_name=body.location.program,
            expected_payload_sha256=str(binding["payloadSha256"]),
        )

    async def open_workspace_in_xg5000(
        workspace: LocalWorkspaceV1,
        *,
        timeout_seconds: float,
        independent_from_pid: int | None = None,
    ) -> tuple[XG5000ProjectLaunchV1, dict[str, Any] | None]:
        open_arguments: dict[str, Any] = {
            "workspace_id": workspace.workspace_id,
            "expected_working_hash": workspace.working_copy.current_sha256,
            "timeout_seconds": timeout_seconds,
        }
        if independent_from_pid is not None:
            open_arguments["independent_from_pid"] = independent_from_pid
        try:
            raw_launch = await mcp.call_workspace_tool(
                "xg5000.open_project",
                open_arguments,
            )
        except MCPToolError as exc:
            logger.warning(
                "XG5000 could not open the verified workspace working copy: %s",
                exc,
            )
            reconciled = await _reconcile_late_workspace_open(
                mcp,
                workspace,
                bind_session=bind_engineering_session,
            )
            if reconciled is not None:
                return reconciled
            raise HTTPException(
                status_code=409,
                detail=(
                    "working copy는 보존되어 있습니다. XG5000 열기를 완료하지 못했습니다. "
                    f"진단: {type(exc).__name__}: {exc}"
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
                requested_pid = int(instance["pid"])
                requested_hwnd = int(instance["hwnd"])
                attached = await mcp.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": requested_pid,
                        "hwnd": requested_hwnd,
                        "project_path": workspace.working_copy.path,
                        "project_hash": workspace.working_copy.current_sha256,
                        "workspace_id": workspace.workspace_id,
                    },
                )
            except (KeyError, TypeError, ValueError, MCPToolError) as exc:
                logger.warning("verified workspace launch attach failed: %s", exc)
                session = None
            else:
                validated = _validated_engineering_session_result(
                    attached,
                    pid=requested_pid,
                    hwnd=requested_hwnd,
                    project_path=workspace.working_copy.path,
                    project_hash=workspace.working_copy.current_sha256,
                )
                if validated is None:
                    logger.warning(
                        "verified workspace launch attach returned a mismatched or "
                        "non-read-ready session for pid=%s hwnd=%s",
                        requested_pid,
                        requested_hwnd,
                    )
                else:
                    session = bind_engineering_session(validated)
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
                detail=f"로컬 프로젝트 작업공간 생성 실패: {exc}",
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
        clear_ls_controller_target()
        bind_agent_workspace_target(workspace)

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
        # first; an existing session is reusable only after an exact fresh read
        # from the same MCP process proves its identity and process lifetime.
        cached_session = (
            dict(active_engineering_session)
            if isinstance(active_engineering_session, dict)
            else None
        )
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
        clear_ls_controller_target()
        bind_agent_workspace_target(workspace)
        reused_session: dict[str, Any] | None = None
        if isinstance(cached_session, dict):
            raw_session_id = cached_session.get("session_id")
            raw_pid = cached_session.get("pid")
            raw_hwnd = cached_session.get("hwnd")
            if (
                isinstance(raw_session_id, str)
                and isinstance(raw_pid, int)
                and not isinstance(raw_pid, bool)
                and isinstance(raw_hwnd, int)
                and not isinstance(raw_hwnd, bool)
            ):
                reused_session = await _revalidate_exact_engineering_session(
                    mcp,
                    session_id=raw_session_id,
                    pid=raw_pid,
                    hwnd=raw_hwnd,
                    project_path=workspace.working_copy.path,
                    project_hash=workspace.working_copy.current_sha256,
                )
        if reused_session is not None:
            session = bind_engineering_session(reused_session)
            launch = None
        else:
            active_engineering_session = None
            execution_broker.clear_agent_session()
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
                "xg5000_window_detected": bool(launch and launch.window_detected),
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
            if (
                active_ls_controller is None
                and not _engineering_session_read_ready(active_engineering_session)
            ):
                bind_agent_workspace_target(workspace)
        return workspace

    @app.get(
        "/api/v1/workspaces/{workspace_id}/compare",
        response_model=LocalWorkspaceCompareV1,
    )
    async def local_workspace_compare_v1(workspace_id: str) -> LocalWorkspaceCompareV1:
        try:
            result = await mcp.call_workspace_tool(
                "workspace.compare",
                {"workspace_id": workspace_id},
            )
        except MCPToolError as exc:
            raise HTTPException(status_code=404, detail="workspace comparison unavailable") from exc
        try:
            comparison = validate_workspace_compare_result(
                result,
                expected_workspace_id=workspace_id,
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid workspace comparison",
            ) from exc
        return comparison

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
        "/api/v1/controller-project-restore/preflight",
        response_model=ControllerRestorePreflightV1,
    )
    async def controller_project_restore_preflight_v1(
        body: ControllerRestorePreflightRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
    ) -> ControllerRestorePreflightV1:
        try:
            raw = await mcp.call_restore_tool(
                "controller.restore_preflight",
                {"pid": body.pid, "hwnd": body.hwnd},
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="XG5000 restore surface could not be observed for that window",
            ) from exc
        try:
            if not isinstance(raw, dict) or set(raw) != {
                "schema_version",
                "operation",
                "ok",
                "verified",
                "preflight",
            }:
                raise ValueError("unexpected MCP restore preflight envelope")
            if (
                raw["schema_version"] != "1.0"
                or raw["operation"] != "controller.restore_preflight"
                or raw["ok"] is not True
                or raw["verified"] is not True
            ):
                raise ValueError("unverified MCP restore preflight envelope")
            preflight = ControllerRestorePreflightV1.model_validate(raw["preflight"])
            if preflight.target.pid != body.pid or preflight.target.hwnd != body.hwnd:
                raise ValueError("MCP restore preflight target mismatched")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid controller restore preflight receipt",
            ) from exc
        public_receipt = preflight.model_dump(mode="json", by_alias=True)
        store.set_host_state("controller_restore_preflight", public_receipt)
        store.append_event(
            event_type="controller_restore.preflight",
            payload={
                "actorId": _request_actor_id(request),
                "pid": body.pid,
                "hwnd": body.hwnd,
                "ready": preflight.ready,
                "blockers": list(preflight.blockers),
                "profileDigest": preflight.surface_profile.profile_digest,
                "receiptDigest": preflight.receipt_digest,
                "readOnly": True,
                "invoked": False,
            },
        )
        return preflight

    @app.post(
        "/api/v1/controller-project-restore/begin",
        response_model=ControllerRestoreHandoffV1,
    )
    async def controller_project_restore_begin_v1(
        body: ControllerRestoreBeginRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> ControllerRestoreHandoffV1:
        nonlocal active_engineering_session
        existing_handoff = store.get_host_state("controller_restore_handoff")
        if existing_handoff is not None:
            try:
                existing = ControllerRestoreHandoffV1.model_validate(existing_handoff)
            except (TypeError, ValueError):
                store.set_host_state("controller_restore_handoff", None)
            else:
                if existing.expires_at > datetime.now(timezone.utc):
                    if existing.operation_id == body.operation_id:
                        return existing
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Another controller restore handoff is already pending",
                    )
                store.set_host_state("controller_restore_handoff", None)
        stored = store.get_host_state("controller_restore_preflight")
        try:
            preflight = ControllerRestorePreflightV1.model_validate(stored)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run restore preflight for this XG5000 window first",
            ) from exc
        if (
            preflight.ready is not True
            or preflight.target.pid != body.pid
            or preflight.target.hwnd != body.hwnd
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The stored restore preflight does not match this XG5000 window",
            )
        try:
            raw = await mcp.call_restore_tool(
                "controller.begin_restore_handoff",
                {
                    "pid": body.pid,
                    "hwnd": body.hwnd,
                    "operation_id": body.operation_id,
                    "preflight_receipt_digest": preflight.receipt_digest,
                    "profile_digest": preflight.surface_profile.profile_digest,
                    "timeout_seconds": body.timeout_seconds,
                },
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="XG5000 Open-from-PLC handoff could not be started",
            ) from exc
        try:
            if not isinstance(raw, dict) or set(raw) != {
                "schema_version",
                "operation",
                "ok",
                "verified",
                "handoff",
            }:
                raise ValueError("unexpected restore handoff envelope")
            if (
                raw["schema_version"] != "1.0"
                or raw["operation"] != "controller.begin_restore_handoff"
                or raw["ok"] is not True
                or raw["verified"] is not True
            ):
                raise ValueError("unverified restore handoff envelope")
            handoff = ControllerRestoreHandoffV1.model_validate(raw["handoff"])
            if (
                handoff.operation_id != body.operation_id
                or handoff.target.pid != body.pid
                or handoff.target.hwnd != body.hwnd
                or handoff.preflight_receipt_digest != preflight.receipt_digest
            ):
                raise ValueError("restore handoff binding mismatched")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid restore handoff receipt",
            ) from exc
        store.set_host_state(
            "controller_restore_handoff",
            handoff.model_dump(mode="json", by_alias=True),
        )
        active_engineering_session = None
        execution_broker.clear_agent_session()
        store.append_event(
            event_type="controller_restore.handoff_started",
            payload={
                "actorId": _request_actor_id(request),
                "operationId": handoff.operation_id,
                "pid": handoff.target.pid,
                "hwnd": handoff.target.hwnd,
                "dialogHwnd": handoff.dialog.hwnd,
                "receiptDigest": handoff.receipt_digest,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        return handoff

    @app.post(
        "/api/v1/controller-project-restore/complete",
        response_model=ControllerRestoreCompletedV1,
    )
    async def controller_project_restore_complete_v1(
        body: ControllerRestoreCompleteRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> ControllerRestoreCompletedV1:
        nonlocal active_engineering_session, active_workspace
        stored = store.get_host_state("controller_restore_handoff")
        if stored is None:
            try:
                completed = ControllerRestoreCompletedV1.model_validate(
                    store.get_host_state("controller_restore_last_completion")
                )
            except (TypeError, ValueError):
                completed = None
            if completed is not None and completed.operation_id == body.operation_id:
                return completed
        try:
            handoff = ControllerRestoreHandoffV1.model_validate(stored)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No valid controller restore handoff is pending",
            ) from exc
        if handoff.operation_id != body.operation_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Controller restore operation identity mismatched",
            )
        try:
            raw = await mcp.call_restore_tool(
                "controller.complete_restore_handoff",
                {
                    "pid": handoff.target.pid,
                    "hwnd": handoff.target.hwnd,
                    "operation_id": handoff.operation_id,
                    "handoff": handoff.model_dump(mode="json", by_alias=True),
                    "timeout_seconds": body.timeout_seconds,
                },
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Complete the vendor upload dialog before finishing controller restore"
                ),
            ) from exc
        try:
            if not isinstance(raw, dict) or set(raw) != {
                "schema_version",
                "operation",
                "ok",
                "verified",
                "restoration",
            }:
                raise ValueError("unexpected restore completion envelope")
            restoration = raw["restoration"]
            expected_keys = {
                "schema",
                "operationId",
                "completedAt",
                "handoffReceiptDigest",
                "sourceTarget",
                "savedProject",
                "workspace",
                "provenance",
                "verified",
                "receiptDigest",
            }
            if (
                raw["schema_version"] != "1.0"
                or raw["operation"] != "controller.complete_restore_handoff"
                or raw["ok"] is not True
                or raw["verified"] is not True
                or not isinstance(restoration, dict)
                or set(restoration) != expected_keys
                or restoration["schema"]
                != "controlstudio-controller-restored-workspace/v1"
                or restoration["operationId"] != handoff.operation_id
                or restoration["handoffReceiptDigest"] != handoff.receipt_digest
                or restoration["verified"] is not True
            ):
                raise ValueError("restore completion binding mismatched")
            unsigned = {
                key: restoration[key]
                for key in expected_keys - {"receiptDigest"}
            }
            expected_digest = hashlib.sha256(
                json.dumps(
                    unsigned,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(expected_digest, restoration["receiptDigest"]):
                raise ValueError("restore completion digest mismatched")
            saved_project = restoration["savedProject"]
            if not isinstance(saved_project, dict) or set(saved_project) != {
                "path",
                "sha256",
                "size",
                "saveResultCode",
            }:
                raise ValueError("restore saved-project receipt is invalid")
            workspace = _local_workspace_v1(restoration["workspace"])
            if (
                workspace.source_kind != "restored_from_controller"
                or workspace.provenance is None
                or workspace.provenance.operation_id != handoff.operation_id
                or workspace.provenance.handoff_receipt_digest != handoff.receipt_digest
                or workspace.provenance.saved_project_sha256 != saved_project["sha256"]
                or workspace.source.sha256 != saved_project["sha256"]
            ):
                raise ValueError("restored workspace provenance mismatched")
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid restored workspace receipt",
            ) from exc

        active_engineering_session = None
        execution_broker.clear_agent_session()
        active_workspace = workspace.model_dump(mode="json")
        store.set_host_state("active_workspace", active_workspace)
        clear_ls_controller_target()
        bind_agent_workspace_target(workspace)
        store.set_host_state("controller_restore_handoff", None)
        launch: XG5000ProjectLaunchV1 | None = None
        session: dict[str, Any] | None = None
        try:
            launch, session = await open_workspace_in_xg5000(
                workspace,
                timeout_seconds=body.timeout_seconds,
                independent_from_pid=handoff.target.pid,
            )
        except HTTPException:
            logger.warning(
                "restored workspace was captured but its working copy could not be opened"
            )
        store.append_event(
            event_type="controller_restore.completed",
            payload={
                "actorId": _request_actor_id(request),
                "operationId": handoff.operation_id,
                "workspaceId": workspace.workspace_id,
                "revisionId": workspace.revision_id,
                "sourceSha256": workspace.source.sha256,
                "restorationReceiptDigest": restoration["receiptDigest"],
                "engineeringSessionAttached": session is not None,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        completed = ControllerRestoreCompletedV1(
            operation_id=handoff.operation_id,
            restoration_receipt_digest=restoration["receiptDigest"],
            saved_project_sha256=saved_project["sha256"],
            workspace=workspace,
            launch=launch,
            session=session,
        )
        store.set_host_state(
            "controller_restore_last_completion",
            completed.model_dump(mode="json", by_alias=True),
        )
        return completed

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
        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key != body.operation_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key must exactly match operationId",
            )
        actor_id = _request_actor_id(request)
        holder_token: str | None = None
        try:
            current_brief_digest, current_attachment_digest = (
                current_project_plan_authority_digests(
                    body.reviewed_plan.conversation_id
                )
            )
            if (
                body.reviewed_plan.project_brief_digest != current_brief_digest
                or body.reviewed_plan.attachment_revision_digest
                != current_attachment_digest
            ):
                raise ProjectPlanReviewConflict(
                    "project brief or attachment source revisions changed after review"
                )
            _review, reviewed_artifact = store.require_reviewed_project_plan(
                conversation_id=body.reviewed_plan.conversation_id,
                message_id=body.reviewed_plan.message_id,
                plan_digest=body.reviewed_plan.plan_digest,
                reviewed_section_ids=body.reviewed_plan.reviewed_section_ids,
                reviewed_item_ids=body.reviewed_plan.reviewed_item_ids,
                project_brief_digest=body.reviewed_plan.project_brief_digest,
                attachment_revision_digest=(
                    body.reviewed_plan.attachment_revision_digest
                ),
                expected_handoff_native_project_id=body.operation_id,
            )
            if reviewed_artifact.get("projectName") != body.project_name:
                raise ProjectPlanReviewConflict(
                    "native project name does not match the reviewed plan"
                )
            reviewed_technical = reviewed_artifact.get("technicalConfiguration")
            if reviewed_technical != {
                "plcSeries": body.plc_series,
                "cpuModel": body.cpu_model,
                "programName": body.program_name,
                "language": body.language,
                "description": body.description,
            }:
                raise ProjectPlanReviewConflict(
                    "native project technical configuration does not match the reviewed plan"
                )
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
                reviewed_item_ids=tuple(body.reviewed_plan.reviewed_item_ids),
                project_brief_digest=body.reviewed_plan.project_brief_digest,
                attachment_revision_digest=(
                    body.reviewed_plan.attachment_revision_digest
                ),
                planning_artifact=reviewed_artifact,
            )
            build_bundle = native_project_service.build_bundle(spec)
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
                    "buildBundleId": build_bundle["bundleId"],
                },
            )
            current_brief_digest, current_attachment_digest = (
                current_project_plan_authority_digests(
                    body.reviewed_plan.conversation_id
                )
            )
            if (
                body.reviewed_plan.project_brief_digest != current_brief_digest
                or body.reviewed_plan.attachment_revision_digest
                != current_attachment_digest
            ):
                raise ProjectPlanReviewConflict(
                    "project brief or attachment source revisions changed before execution"
                )
            store.require_reviewed_project_plan(
                conversation_id=body.reviewed_plan.conversation_id,
                message_id=body.reviewed_plan.message_id,
                plan_digest=body.reviewed_plan.plan_digest,
                reviewed_section_ids=body.reviewed_plan.reviewed_section_ids,
                reviewed_item_ids=body.reviewed_plan.reviewed_item_ids,
                project_brief_digest=body.reviewed_plan.project_brief_digest,
                attachment_revision_digest=(
                    body.reviewed_plan.attachment_revision_digest
                ),
                expected_handoff_native_project_id=body.operation_id,
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
                current = native_project_repository.get(body.operation_id)
                if current is not None and _native_project_target_lease_must_remain(
                    current
                ):
                    store.park_target_operation_lease(
                        operation_id=body.operation_id,
                        holder_token=holder_token,
                        reason="native_project_request_interrupted",
                    )
                else:
                    store.release_target_operation_lease(
                        operation_id=body.operation_id,
                        holder_token=holder_token,
                        reason="native_project_interrupted_before_uncertain_mutation",
                    )
            raise

        assert holder_token is not None
        if not _native_project_target_lease_must_remain(operation):
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
            reopen = operation.result.get("reopenVerification")
            reopen_bound = False
            if (
                isinstance(reopen, dict)
                and reopen.get("state") == "verified"
                and isinstance(reopen.get("workspace"), dict)
                and isinstance(reopen.get("session"), dict)
            ):
                reopened_workspace = _local_workspace_v1(reopen["workspace"])
                reopened_session = reopen["session"]
                if (
                    Path(str(reopened_session["project_path"])).resolve()
                    == Path(reopened_workspace.working_copy.path).resolve()
                    and str(reopened_session["project_hash"]).casefold()
                    == reopened_workspace.working_copy.current_sha256.casefold()
                ):
                    bind_engineering_session(reopened_session)
                    active_workspace = reopened_workspace.model_dump(mode="json")
                    store.set_host_state("active_workspace", active_workspace)
                    reopen_bound = True
            if not reopen_bound:
                # Same-process creation remains valid, but it is not an active
                # engineering target until the captured copy is independently
                # reopened and read back against the reviewed saved build.
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
                "freshProcessReopenVerified": (
                    isinstance(operation.result.get("reopenVerification"), dict)
                    and operation.result["reopenVerification"].get("state") == "verified"
                ),
                "nativeSimulationVerified": (
                    isinstance(operation.result.get("simulationVerification"), dict)
                    and operation.result["simulationVerification"].get("state") == "verified"
                ),
                "nativeSimulationTrialState": (
                    operation.result.get("simulationVerification", {}).get("trial", {}).get("state")
                    if isinstance(operation.result.get("simulationVerification"), dict)
                    and isinstance(operation.result["simulationVerification"].get("trial"), dict)
                    else None
                ),
                "engineeredProgramReady": operation.result.get("engineeredProgramReady"),
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
        "/api/v1/native-projects/recovery",
        response_model=NativeProjectOperationV1,
    )
    async def completed_unbound_native_project_v1(
        conversation_id: str = Query(
            alias="conversationId", min_length=1, max_length=256
        ),
        message_id: str = Query(alias="messageId", min_length=1, max_length=256),
        plan_digest: str = Query(
            alias="planDigest", pattern=r"^[0-9a-f]{64}$"
        ),
    ) -> NativeProjectOperationV1:
        try:
            operation = native_project_repository.completed_unbound_for_reviewed_plan(
                conversation_id=conversation_id,
                message_id=message_id,
                plan_digest=plan_digest,
            )
        except NativeProjectConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="completed unbound native project not found for reviewed plan",
            )
        return _native_project_operation_v1(operation)

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
        "/api/v1/native-projects/{native_project_id}/simulation-trial/decision",
        response_model=NativeSimulationTrialDecisionV1,
    )
    async def decide_native_simulation_trial_v1(
        native_project_id: str,
        body: NativeSimulationTrialDecisionRequestV1,
        request: Request,
        _target_lease: None = Depends(target_switch_lease),
    ) -> NativeSimulationTrialDecisionV1:
        nonlocal active_engineering_session, active_workspace
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
        candidate = native_project_repository.get(native_project_id)
        if candidate is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="native project operation not found",
            )

        refreshed_session: dict[str, Any] | None = None
        reopened_workspace: LocalWorkspaceV1 | None = None
        simulation = candidate.result.get("simulationVerification")
        candidate_trial = simulation.get("trial") if isinstance(simulation, dict) else None
        candidate_receipt = (
            simulation.get("receipt") if isinstance(simulation, dict) else None
        )
        candidate_build_bundle = candidate.request.get("buildBundle")
        candidate_bundle_id = (
            candidate_build_bundle.get("bundleId")
            if isinstance(candidate_build_bundle, dict)
            else None
        )
        try:
            expected_candidate_trial_id = (
                _native_simulation_trial_id(
                    candidate.operation_id,
                    candidate_bundle_id,
                )
                if isinstance(candidate_bundle_id, str) and candidate_bundle_id
                else None
            )
        except ValueError:
            expected_candidate_trial_id = None
        if (
            not isinstance(candidate_trial, dict)
            or candidate_trial.get("trialId") != expected_candidate_trial_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="XG-SIM trial identity does not match the native build bundle",
            )
        if (
            body.decision == "accept"
            and isinstance(candidate_trial, dict)
            and candidate_trial.get("state") == "OBSERVING"
            and isinstance(candidate_receipt, dict)
            and candidate_receipt.get("schemaVersion")
            == "controlstudio-native-simulation-receipt/v3"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "legacy v3 XG-SIM observations cannot be accepted; "
                    "verify the exact receipt and explicitly revert the trial"
                ),
            )
        candidate_reopen = candidate.result.get("reopenVerification")
        candidate_workspace = (
            candidate_reopen.get("workspace")
            if isinstance(candidate_reopen, dict)
            else None
        )

        def accepted_trial_binding_is_current() -> bool:
            if (
                not isinstance(candidate_trial, dict)
                or candidate_trial.get("state") != "ACCEPTED"
                or candidate.result.get("engineeredProgramReady") is not True
                or candidate.result.get("workspaceBound") is not True
                or not isinstance(candidate_workspace, dict)
                or not isinstance(active_engineering_session, dict)
                or not isinstance(active_workspace, dict)
                or not _engineering_session_read_ready(active_engineering_session)
            ):
                return False
            expected_working = candidate_workspace.get("working_copy")
            active_working = active_workspace.get("working_copy")
            expected_provenance = validated_new_build_workspace_provenance(
                candidate,
                candidate_workspace,
                require_capture_verification=True,
            )
            active_provenance = validated_new_build_workspace_provenance(
                candidate,
                active_workspace,
            )
            active_metadata = active_engineering_session.get("metadata")
            active_trial_resume = (
                active_metadata.get("native_trial_resume")
                if isinstance(active_metadata, dict)
                else None
            )
            if (
                not isinstance(expected_working, dict)
                or not isinstance(active_working, dict)
                or expected_provenance is None
                or active_provenance != expected_provenance
                or not isinstance(active_trial_resume, dict)
            ):
                return False
            try:
                parsed_active_workspace = _local_workspace_v1(active_workspace)
                parsed_provenance = parsed_active_workspace.provenance
                return (
                    active_workspace.get("workspace_id")
                    == candidate_workspace.get("workspace_id")
                    and active_workspace.get("revision_id")
                    == candidate_workspace.get("revision_id")
                    and expected_provenance["receipt_digest"]
                    == active_trial_resume.get("provenance_receipt_digest")
                    and Path(str(active_working["path"])).resolve()
                    == Path(str(expected_working["path"])).resolve()
                    and str(active_working["current_sha256"]).casefold()
                    == str(expected_working["current_sha256"]).casefold()
                    and Path(str(active_engineering_session["project_path"])).resolve()
                    == Path(str(expected_working["path"])).resolve()
                    and str(active_engineering_session["project_hash"]).casefold()
                    == str(expected_working["current_sha256"]).casefold()
                    and active_trial_resume.get("native_project_id") == candidate.id
                    and active_trial_resume.get("trial_id")
                    == candidate_trial.get("trialId")
                    and active_trial_resume.get("receipt_digest")
                    == candidate_trial.get("receiptDigest")
                    and active_trial_resume.get("source_kind") == "new_build"
                    and active_trial_resume.get("workspace_id")
                    == candidate_workspace.get("workspace_id")
                    and active_trial_resume.get("revision_id")
                    == candidate_workspace.get("revision_id")
                    and isinstance(parsed_provenance, NewBuildWorkspaceProvenanceV1)
                    and native_trial_session_identity(
                        active_engineering_session,
                        parsed_active_workspace,
                    )
                    is not None
                )
            except (KeyError, TypeError, ValueError, OSError):
                return False

        accepted_trial_already_bound = accepted_trial_binding_is_current()
        if body.decision == "accept" and accepted_trial_already_bound:
            engineering_target = current_engineering_conversation_target()
            if engineering_target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="accepted XG-SIM target is no longer current",
                )
            try:
                reopened_workspace, refreshed_session = (
                    await revalidate_native_handoff_freshness(
                        engineering_target,
                        native_project_id=candidate.id,
                        trial_id=str(candidate_trial.get("trialId") or ""),
                        receipt_digest=str(candidate_trial.get("receiptDigest") or ""),
                    )
                )
            except NativeProjectConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="accepted XG-SIM target identity changed before reuse",
                ) from exc
        needs_acceptance_preflight = (
            body.decision == "accept"
            and isinstance(candidate_trial, dict)
            and candidate_trial.get("state") in {"OBSERVING", "ACCEPTED"}
            and not accepted_trial_already_bound
        )
        if needs_acceptance_preflight:
            reopen = candidate.result.get("reopenVerification")
            stored_session = simulation.get("session") if isinstance(simulation, dict) else None
            workspace = reopen.get("workspace") if isinstance(reopen, dict) else None
            stored_launch = reopen.get("launch") if isinstance(reopen, dict) else None
            if (
                not isinstance(stored_session, dict)
                or not isinstance(workspace, dict)
                or not isinstance(stored_launch, dict)
                or reopen.get("state") != "verified"
                or simulation.get("state") != "verified"
                or candidate_trial.get("safeRestorationVerified") is not True
                or (
                    candidate_trial.get("state") == "ACCEPTED"
                    and (
                        candidate.result.get("engineeredProgramReady") is not True
                        or candidate.result.get("workspaceBound") is not True
                    )
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="verified reopened workspace is unavailable for acceptance",
                )
            stored_receipt_digest = str(candidate_trial.get("receiptDigest") or "")
            if body.expected_receipt_digest != stored_receipt_digest:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="simulation trial receipt digest changed before acceptance",
                )
            try:
                raw_workspace = await mcp.call_workspace_tool(
                    "workspace.status",
                    {"workspace_id": str(workspace.get("workspace_id") or "")},
                )
            except MCPToolError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="protected workspace could not be revalidated before acceptance",
                ) from exc
            try:
                stored_build_provenance = validated_new_build_workspace_provenance(
                    candidate,
                    workspace,
                    require_capture_verification=True,
                )
                reopened_build_provenance = validated_new_build_workspace_provenance(
                    candidate,
                    raw_workspace,
                    require_capture_verification=True,
                )
                stored_workspace = _local_workspace_v1(workspace)
                reopened_workspace = _local_workspace_v1(raw_workspace)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="protected workspace identity is invalid before acceptance",
                ) from exc
            simulation_receipt = (
                simulation.get("receipt") if isinstance(simulation, dict) else None
            )
            receipt_project = (
                simulation_receipt.get("projectReadback")
                if isinstance(simulation_receipt, dict)
                else None
            )
            reopened_provenance = reopened_workspace.provenance
            stored_provenance = stored_workspace.provenance
            workspace_matches = (
                stored_build_provenance is not None
                and reopened_build_provenance == stored_build_provenance
                and reopened_workspace.source_kind == "new_build"
                and stored_workspace.source_kind == "new_build"
                and isinstance(reopened_provenance, NewBuildWorkspaceProvenanceV1)
                and isinstance(stored_provenance, NewBuildWorkspaceProvenanceV1)
                and reopened_provenance == stored_provenance
                and reopened_provenance.saved_project_sha256 == reopened_workspace.source.sha256
                and reopened_workspace.workspace_id == stored_workspace.workspace_id
                and reopened_workspace.revision_id == stored_workspace.revision_id
                and reopened_workspace.source.sha256 == stored_workspace.source.sha256
                and reopened_workspace.source.original_state == "unchanged"
                and reopened_workspace.source.original_current_sha256
                == stored_workspace.source.sha256
                and reopened_workspace.working_copy.path
                == stored_workspace.working_copy.path
                and reopened_workspace.working_copy.baseline_sha256
                == stored_workspace.working_copy.baseline_sha256
                and reopened_workspace.working_copy.current_sha256
                == stored_workspace.working_copy.current_sha256
                and isinstance(receipt_project, dict)
                and receipt_project.get("projectSha256")
                == reopened_workspace.working_copy.current_sha256
                and receipt_project.get("reopenedProjectSha256")
                == reopened_workspace.working_copy.baseline_sha256
                and receipt_project.get("projectUnchangedDuringRuntime") is True
            )
            if not workspace_matches:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="protected workspace changed before trial acceptance",
                )

            launch_instance = stored_launch.get("instance")
            stored_metadata = stored_session.get("metadata")
            try:
                process_create_time = float(stored_launch["process_create_time"])
                launch_title = str(launch_instance["title"])
                launch_matches = (
                    isinstance(launch_instance, dict)
                    and stored_launch.get("verified") is True
                    and stored_launch.get("window_detected") is True
                    and stored_launch.get("process_is_independent") is True
                    and process_create_time > 0
                    and launch_title != ""
                    and int(stored_launch["launch_pid"]) == int(stored_session["pid"])
                    and int(launch_instance["pid"]) == int(stored_session["pid"])
                    and int(launch_instance["hwnd"]) == int(stored_session["hwnd"])
                    and stored_launch.get("workspace_id") == reopened_workspace.workspace_id
                    and stored_launch.get("revision_id") == reopened_workspace.revision_id
                    and Path(str(stored_launch["working_path"])).resolve()
                    == Path(reopened_workspace.working_copy.path).resolve()
                    and str(stored_launch["working_hash"]).casefold()
                    == reopened_workspace.working_copy.baseline_sha256.casefold()
                    and isinstance(stored_metadata, dict)
                    and stored_metadata.get("title") == launch_title
                    and _engineering_session_read_ready(stored_session)
                )
            except (KeyError, TypeError, ValueError, OSError):
                launch_matches = False
                process_create_time = 0.0
                launch_title = ""
            if not launch_matches:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="verified XG5000 process lifetime is invalid before acceptance",
                )

            def sign_resume_authorization(
                *,
                pid: int,
                hwnd: int,
                target_process_create_time: float,
                title: str,
            ) -> dict[str, Any]:
                issued_at = datetime.now(timezone.utc)
                return mcp.sign_mutation_authorization(
                    {
                        "schema": "controlstudio-native-session-resume-authorization/v2",
                        "authorization_id": str(uuid.uuid4()),
                        "actor_id": _request_actor_id(request),
                        "native_project_id": candidate.id,
                        "trial_id": str(candidate_trial.get("trialId") or ""),
                        "receipt_digest": stored_receipt_digest,
                        "workspace_provenance_receipt_digest": (reopened_provenance.receipt_digest),
                        "workspace_id": reopened_workspace.workspace_id,
                        "revision_id": reopened_workspace.revision_id,
                        "pid": pid,
                        "hwnd": hwnd,
                        "project_path": reopened_workspace.working_copy.path,
                        "project_hash": reopened_workspace.working_copy.current_sha256,
                        "process_create_time": target_process_create_time,
                        "title": title,
                        "capability": "analysis_session_resume",
                        "allowed_operation": "xg5000.attach",
                        "issued_at": issued_at.isoformat(timespec="milliseconds"),
                        "expires_at": (issued_at + timedelta(minutes=2)).isoformat(
                            timespec="milliseconds"
                        ),
                        "nonce": str(uuid.uuid4()),
                    }
                )

            try:
                expected_pid = int(stored_session["pid"])
                expected_hwnd = int(stored_session["hwnd"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="stored XG5000 session identity is invalid before acceptance",
                ) from exc
            expected_process_create_time = process_create_time
            expected_title = launch_title

            # Native creation deliberately leaves the independently reopened
            # target unbound while its simulation trial is OBSERVING.  The
            # Host process may also have been restarted since verification,
            # which cleanly closes an MCP-owned reopened XG5000 child.  First
            # inspect the live windows without binding anything.  Reuse only
            # the exact stored PID/HWND/title; otherwise open the already
            # revalidated working copy in a fresh controlled process.
            try:
                raw_instances = await mcp.call_read_tool("xg5000.instances", {})
            except MCPToolError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="XG5000 instances could not be revalidated before acceptance",
                ) from exc
            live_instances = (
                raw_instances.get("instances") if isinstance(raw_instances, dict) else None
            )
            if not isinstance(live_instances, list) or any(
                not isinstance(item, dict) for item in live_instances
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="XG5000 instance inventory is invalid before acceptance",
                )
            stored_target_matches = [
                item
                for item in live_instances
                if item.get("pid") == expected_pid
                and item.get("hwnd") == expected_hwnd
                and item.get("title") == expected_title
            ]
            if len(stored_target_matches) > 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="stored XG5000 target identity is ambiguous before acceptance",
                )
            if not stored_target_matches:
                reopen_receipt = reopen.get("receipt")
                receipt_source_process = (
                    reopen_receipt.get("sourceProcess")
                    if isinstance(reopen_receipt, dict)
                    else None
                )
                try:
                    source_pid = int(stored_launch.get("independent_from_pid") or 0)
                    source_request_pid = int(candidate.request["xg5000Pid"])
                    source_hwnd = int(candidate.request["xg5000Hwnd"])
                    receipt_source_pid = int(receipt_source_process["pid"])
                    source_evidence_matches = (
                        source_pid > 0
                        and source_pid == source_request_pid == receipt_source_pid
                        and source_hwnd > 0
                    )
                except (KeyError, TypeError, ValueError):
                    source_evidence_matches = False
                    source_pid = 0
                    source_hwnd = 0
                if not source_evidence_matches:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="source XG5000 identity is invalid before workspace recovery",
                    )
                live_source_matches = [
                    item
                    for item in live_instances
                    if item.get("pid") == source_pid
                    and item.get("hwnd") == source_hwnd
                ]
                if live_instances and (
                    len(live_instances) != 1 or len(live_source_matches) != 1
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "a different XG5000 target is open; the verified workspace "
                            "cannot be recovered automatically"
                        ),
                    )
                launch_arguments: dict[str, Any] = {
                    "workspace_id": reopened_workspace.workspace_id,
                    "expected_working_hash": (
                        reopened_workspace.working_copy.current_sha256
                    ),
                    "timeout_seconds": 60.0,
                }
                if live_source_matches:
                    launch_arguments["independent_from_pid"] = source_pid
                try:
                    raw_launch = await mcp.call_workspace_tool(
                        "xg5000.open_project",
                        launch_arguments,
                    )
                except MCPToolError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=_workspace_cli_failure_detail(exc),
                    ) from exc
                fresh_instance = (
                    raw_launch.get("instance") if isinstance(raw_launch, dict) else None
                )
                try:
                    fresh_pid = int(fresh_instance["pid"])
                    fresh_hwnd = int(fresh_instance["hwnd"])
                    fresh_title = str(fresh_instance["title"])
                    fresh_process_create_time = float(raw_launch["process_create_time"])
                    existing_pids = {int(item["pid"]) for item in live_instances}
                    fresh_launch_matches = (
                        not isinstance(fresh_instance.get("pid"), bool)
                        and not isinstance(fresh_instance.get("hwnd"), bool)
                        and not isinstance(raw_launch.get("process_create_time"), bool)
                        and raw_launch.get("ok") is True
                        and raw_launch.get("operation") == "xg5000.open_project"
                        and raw_launch.get("verified") is True
                        and raw_launch.get("workspace_id")
                        == reopened_workspace.workspace_id
                        and raw_launch.get("revision_id")
                        == reopened_workspace.revision_id
                        and Path(str(raw_launch.get("working_path"))).resolve()
                        == Path(reopened_workspace.working_copy.path).resolve()
                        and str(raw_launch.get("working_hash") or "").casefold()
                        == reopened_workspace.working_copy.current_sha256.casefold()
                        and raw_launch.get("launch_pid") == fresh_pid
                        and raw_launch.get("window_detected") is True
                        and raw_launch.get("process_is_independent") is True
                        and raw_launch.get(
                            "project_identity_read_only_bound_to_selected_pid"
                        )
                        is True
                        and _workspace_launch_binding_matches(
                            raw_launch, reopened_workspace.working_copy.path
                        )
                        and fresh_pid > 0
                        and fresh_pid not in existing_pids
                        and fresh_pid != expected_pid
                        and fresh_hwnd > 0
                        and fresh_title != ""
                        and fresh_process_create_time > 0
                        and (
                            (
                                raw_launch.get("independent_from_pid") == source_pid
                                and fresh_pid != source_pid
                            )
                            if live_source_matches
                            else raw_launch.get("independent_from_pid") is None
                        )
                    )
                except (KeyError, TypeError, ValueError, OSError):
                    fresh_launch_matches = False
                    fresh_pid = 0
                    fresh_hwnd = 0
                    fresh_title = ""
                    fresh_process_create_time = 0.0
                if not fresh_launch_matches:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "fresh XG5000 launch did not preserve the verified "
                            "workspace identity"
                        ),
                    )
                expected_pid = fresh_pid
                expected_hwnd = fresh_hwnd
                expected_process_create_time = fresh_process_create_time
                expected_title = fresh_title

            resume_authorization = sign_resume_authorization(
                pid=expected_pid,
                hwnd=expected_hwnd,
                target_process_create_time=expected_process_create_time,
                title=expected_title,
            )
            try:
                raw_status = await mcp.call_read_tool(
                    "xg5000.attach",
                    {
                        "pid": expected_pid,
                        "hwnd": expected_hwnd,
                        "project_path": reopened_workspace.working_copy.path,
                        "project_hash": reopened_workspace.working_copy.current_sha256,
                        "workspace_id": reopened_workspace.workspace_id,
                        "resume_authorization": resume_authorization,
                    },
                )
            except MCPToolError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="independently reopened XG5000 target is unavailable before acceptance",
                ) from exc
            refreshed = (
                raw_status.get("session")
                if isinstance(raw_status, dict) and isinstance(raw_status.get("session"), dict)
                else raw_status
            )
            if not isinstance(refreshed, dict):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="XG5000 session identity is unavailable before acceptance",
                )
            refreshed_identity = native_trial_session_identity(
                refreshed,
                reopened_workspace,
            )
            expected_resume = {
                "native_project_id": candidate.id,
                "trial_id": candidate_trial.get("trialId"),
                "receipt_digest": stored_receipt_digest,
                "source_kind": "new_build",
                "provenance_receipt_digest": reopened_provenance.receipt_digest,
                "workspace_id": reopened_workspace.workspace_id,
                "revision_id": reopened_workspace.revision_id,
                "pid": expected_pid,
                "main_hwnd": expected_hwnd,
                "project_path": str(Path(reopened_workspace.working_copy.path).resolve()),
                "project_hash": reopened_workspace.working_copy.current_sha256.casefold(),
                "process_create_time": expected_process_create_time,
                "authorization_id": resume_authorization["authorization_id"],
            }
            identity_matches = bool(
                refreshed_identity is not None
                and refreshed_identity["title"] == expected_title
                and refreshed_identity["native_trial_resume"] == expected_resume
            )
            try:
                stored_hash_matches = (
                    str(stored_session["project_hash"]).casefold()
                    == reopened_workspace.working_copy.current_sha256.casefold()
                )
            except (KeyError, TypeError, ValueError):
                stored_hash_matches = False
            if (
                not identity_matches
                or not stored_hash_matches
                or not _engineering_session_read_ready(refreshed)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("independently reopened XG5000 target changed before trial acceptance"),
                )
            refreshed_session = refreshed

        try:
            operation, recorded = native_project_repository.decide_simulation_trial(
                native_project_id,
                decision=body.decision,
                expected_receipt_digest=body.expected_receipt_digest,
                actor_id=_request_actor_id(request),
            )
        except NativeProjectConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        if body.decision == "accept" and refreshed_session is not None:
            assert reopened_workspace is not None
            active_workspace = reopened_workspace.model_dump(mode="json")
            store.set_host_state("active_workspace", active_workspace)
            bind_engineering_session(refreshed_session)
        elif body.decision == "revert":
            project_path = str(operation.result.get("projectPath") or "")
            active_path = (
                str(active_engineering_session.get("project_path") or "")
                if isinstance(active_engineering_session, dict)
                else ""
            )
            if (
                project_path
                and active_path
                and (Path(project_path).resolve() == Path(active_path).resolve())
            ):
                active_engineering_session = None
                active_workspace = None
                execution_broker.clear_agent_session()
                store.set_host_state("active_workspace", None)

        trial = operation.result.get("simulationVerification", {}).get("trial", {})
        store.append_event(
            event_type="native_simulation.trial_decision",
            payload={
                "nativeProjectId": operation.id,
                "operationId": operation.operation_id,
                "trialId": trial.get("trialId") if isinstance(trial, dict) else None,
                "state": trial.get("state") if isinstance(trial, dict) else None,
                "receiptDigest": (trial.get("receiptDigest") if isinstance(trial, dict) else None),
                "decisionRecorded": recorded,
                "sessionRebound": refreshed_session is not None,
                "workspaceBound": operation.result.get("workspaceBound"),
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        return NativeSimulationTrialDecisionV1(
            operation=_native_project_operation_v1(operation),
            decision_recorded=recorded,
        )

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
        holder_token = str(uuid.uuid4())
        try:
            store.acquire_target_operation_lease(
                operation_id=operation.operation_id,
                owner_kind="native_project_create",
                holder_token=holder_token,
                target_identity=_native_project_target_lease_identity(operation),
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
                    observed_project_path=(
                        Path(body.observed_project_path)
                        if body.observed_project_path is not None
                        else None
                    ),
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

        if not _native_project_target_lease_must_remain(resolved):
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
        "/api/v1/native-projects/{native_project_id}/resume",
        response_model=NativeProjectResumedV1,
    )
    async def resume_native_project_v1(
        native_project_id: str,
        body: ResumeNativeProjectRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> NativeProjectResumedV1:
        nonlocal active_engineering_session, active_workspace
        operation = native_project_repository.get(native_project_id)
        if operation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="native project operation not found")
        if operation.state not in {"QUARANTINED", "COMPLETED"}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="only a quarantined or unbound completed native project can be resumed")
        holder_token = str(uuid.uuid4())
        try:
            store.acquire_target_operation_lease(
                operation_id=operation.operation_id,
                # Resume continues the original native-project lifecycle.  Keeping
                # this owner kind lets this exact quarantined operation reclaim its
                # own parked create lease, while other operations remain blocked.
                owner_kind="native_project_create",
                holder_token=holder_token,
                target_identity=_native_project_target_lease_identity(operation),
            )
            resumed = await native_project_service.resume(
                operation.operation_id,
                actor_id=_request_actor_id(request),
                pid=body.pid,
                hwnd=body.hwnd,
                expected_request_digest=body.expected_request_digest,
                reopened_pid=body.reopened_pid,
                reopened_hwnd=body.reopened_hwnd,
            )
        except (NativeProjectConflict, NativeProjectPreconditionError) as exc:
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_resume_rejected",
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except TargetOperationLeaseConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except BaseException:
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_resume_interrupted",
            )
            raise
        if _native_project_target_lease_must_remain(resumed):
            store.park_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason="native_project_resume_requires_recovery",
            )
        else:
            store.release_target_operation_lease(
                operation_id=operation.operation_id,
                holder_token=holder_token,
                reason=f"native_project_resume_{resumed.state.casefold()}",
            )
        if resumed.state == "COMPLETED":
            reopen = resumed.result.get("reopenVerification")
            accepted_session = (
                reopen.get("session")
                if isinstance(reopen, dict)
                else None
            )
            reopened_workspace = (
                reopen.get("workspace")
                if isinstance(reopen, dict)
                else None
            )
            if (
                isinstance(reopen, dict)
                and reopen.get("state") == "verified"
                and isinstance(accepted_session, dict)
                and isinstance(reopened_workspace, dict)
            ):
                workspace = _local_workspace_v1(reopened_workspace)
                if (
                    Path(str(accepted_session.get("project_path") or "")).resolve()
                    == Path(workspace.working_copy.path).resolve()
                    and str(accepted_session.get("project_hash") or "").casefold()
                    == workspace.working_copy.current_sha256.casefold()
                ):
                    bind_engineering_session(accepted_session)
                    active_workspace = workspace.model_dump(mode="json")
                    store.set_host_state("active_workspace", active_workspace)
                else:
                    active_engineering_session = None
                    execution_broker.clear_agent_session()
            else:
                active_engineering_session = None
                execution_broker.clear_agent_session()
        store.append_event(
            event_type="native_project.resumed",
            payload={
                "nativeProjectId": resumed.id,
                "operationId": resumed.operation_id,
                "state": resumed.state.casefold(),
                "lifecycleMode": resumed.result.get("resumeProvenance", {}).get("lifecycleMode")
                if isinstance(resumed.result.get("resumeProvenance"), dict)
                else None,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        return NativeProjectResumedV1(
            operation=_native_project_operation_v1(resumed),
            resumed=resumed.state != "QUARANTINED",
        )

    @app.post(
        "/api/v1/xg5000/launch-idle",
        response_model=LaunchIdleXG5000ResponseV1,
    )
    async def launch_idle_xg5000_v1(
        body: LaunchIdleXG5000RequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> LaunchIdleXG5000ResponseV1:
        """Launch exactly one blank XG5000 instance without binding a workspace."""

        try:
            raw = await mcp.call_workspace_tool(
                "xg5000.launch_idle",
                {
                    "confirm_idle_launch": True,
                    "timeout_seconds": body.timeout_seconds,
                },
            )
            if not isinstance(raw, dict):
                raise TypeError("MCP idle XG5000 launch receipt is not an object")
            receipt_digest = _canonical_json_digest(raw)
            if raw.get("status") == "needs_attention":
                outcome: LaunchIdleXG5000ResponseV1 | LaunchIdleXG5000AttentionV1 = (
                    _launch_idle_xg5000_attention(
                        raw,
                        receipt_digest=receipt_digest,
                    )
                )
            else:
                outcome = _launch_idle_xg5000_response(
                    raw,
                    receipt_digest=receipt_digest,
                )
            _validate_launched_xg5000_identity(outcome.launched_identity)
            if outcome.idle_binding is not None:
                _validate_idle_xg5000_binding(outcome.idle_binding)
            if isinstance(outcome, LaunchIdleXG5000AttentionV1):
                store.append_event(
                    event_type="xg5000.idle_launch_needs_attention",
                    payload={
                        "actorId": _request_actor_id(request),
                        "receiptDigest": outcome.receipt_digest,
                        "attentionReason": outcome.attention_reason,
                        "launchedIdentity": outcome.launched_identity,
                        "idleBinding": outcome.idle_binding,
                        "launchAction": outcome.launch_action,
                        "processExited": outcome.process_exited,
                        "processTerminated": False,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "idle_xg5000_launch_needs_attention",
                        "attentionReason": outcome.attention_reason,
                        "launchedIdentity": outcome.launched_identity,
                        "idleBinding": outcome.idle_binding,
                        "launchAction": outcome.launch_action,
                        "processExited": outcome.process_exited,
                        "processTerminated": False,
                    },
                )
            store.append_event(
                event_type="xg5000.idle_launched",
                payload={
                    "actorId": _request_actor_id(request),
                    "receiptDigest": outcome.receipt_digest,
                    "launchedIdentity": outcome.launched_identity,
                    "idleBinding": dict(outcome.idle_binding),
                    "launchAction": outcome.launch_action,
                    "workspaceBound": False,
                    "processTerminated": False,
                },
            )
            return outcome
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the blank XG5000 launch could not be verified",
            ) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid blank XG5000 launch receipt",
            ) from exc

    @app.post(
        "/api/v1/xg5000/attach",
        response_model=AttachXG5000ResponseV1,
    )
    async def attach_xg5000_v1(
        body: AttachXG5000RequestV1,
        request: Request,
        _target_lease: None = Depends(target_switch_lease),
    ) -> AttachXG5000ResponseV1:
        working_copy = (
            active_workspace.get("working_copy")
            if isinstance(active_workspace, dict)
            and isinstance(active_workspace.get("working_copy"), dict)
            else None
        )
        saved_build_rebind_operation_id = (
            str(active_workspace["provenance"]["native_project_id"])
            if (
                not body.allow_unbound_existing_target
                and body.project_path is None
                and body.project_hash is None
                and isinstance(active_workspace, dict)
                and isinstance(active_workspace.get("provenance"), dict)
                and isinstance(
                    active_workspace["provenance"].get("native_project_id"), str
                )
                and active_workspace["provenance"]["native_project_id"]
            )
            else None
        )

        def parked_native_reopen_expectation() -> dict[str, str] | None:
            """Return only the durable fresh-reopen identity for a read attach."""

            parked_lease = store.active_target_operation_lease()
            if not (
                isinstance(parked_lease, dict)
                and parked_lease.get("owner_kind") == "native_project_create"
                and parked_lease.get("state") == "STALE_BLOCKED"
                and isinstance(parked_lease.get("operation_id"), str)
            ):
                return None
            operation = native_project_repository.get(parked_lease["operation_id"])
            if operation is None or not isinstance(operation.result, dict):
                return None
            reopen = operation.result.get("reopenVerification")
            registration = operation.result.get("workspaceRegistration")
            if not isinstance(reopen, dict) or reopen.get("state") != "verified":
                return None
            session = reopen.get("session")
            workspace = registration.get("workspace") if isinstance(registration, dict) else None
            working = workspace.get("working_copy") if isinstance(workspace, dict) else None
            if not (
                isinstance(session, dict)
                and session.get("pid") == body.pid
                and session.get("hwnd") == body.hwnd
                and isinstance(working, dict)
                and isinstance(working.get("path"), str)
                and isinstance(working.get("current_sha256"), str)
            ):
                return None
            project_path = working["path"]
            project_hash = working["current_sha256"]
            try:
                exact_path = _normalized_workspace_path(
                    str(session.get("project_path") or "")
                ) == _normalized_workspace_path(project_path)
            except (OSError, RuntimeError, ValueError):
                return None
            if not (
                exact_path
                and str(session.get("project_hash") or "").casefold()
                == project_hash.casefold()
            ):
                return None
            return {"project_path": project_path, "project_hash": project_hash}

        arguments: dict[str, Any] = {
            "pid": body.pid,
            "hwnd": body.hwnd,
            "access_profile": "analysis",
        }
        expectation = (
            parked_native_reopen_expectation()
            if (
                not body.allow_unbound_existing_target
                and body.project_path is None
                and body.project_hash is None
            )
            else None
        )
        if body.project_path:
            arguments["project_path"] = body.project_path
        elif expectation is not None:
            arguments["project_path"] = expectation["project_path"]
        elif (
            not body.allow_unbound_existing_target
            and working_copy
            and isinstance(working_copy.get("path"), str)
        ):
            arguments["project_path"] = working_copy["path"]
        if body.project_hash:
            arguments["project_hash"] = body.project_hash
        elif expectation is not None:
            arguments["project_hash"] = expectation["project_hash"]
        elif (
            not body.allow_unbound_existing_target
            and working_copy
            and isinstance(working_copy.get("current_sha256"), str)
        ):
            arguments["project_hash"] = working_copy["current_sha256"]
        try:
            result = (
                await native_project_service.rebind_completed_bound_analysis_session(
                    saved_build_rebind_operation_id,
                    actor_id=_request_actor_id(request),
                    pid=body.pid,
                    hwnd=body.hwnd,
                    active_workspace=active_workspace,
                )
                if saved_build_rebind_operation_id is not None
                else await mcp.call_read_tool("xg5000.attach", arguments)
            )
        except (MCPToolError, NativeProjectConflict) as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "XG5000 분석 세션을 검증하지 못했습니다. 대상 창과 "
                    "프로젝트 정체성을 다시 확인해 주세요."
                ),
            ) from exc
        validated = _validated_engineering_session_result(
            result,
            pid=body.pid,
            hwnd=body.hwnd,
            project_path=(str(arguments["project_path"]) if "project_path" in arguments else None),
            project_hash=(str(arguments["project_hash"]) if "project_hash" in arguments else None),
        )
        if validated is None:
            raw_session = (
                result.get("session")
                if isinstance(result, dict) and isinstance(result.get("session"), dict)
                else result
            )
            metadata = raw_session.get("metadata") if isinstance(raw_session, dict) else None
            if (
                isinstance(raw_session, dict)
                and raw_session.get("plc_policy") == "read_only_unverified"
                and isinstance(metadata, dict)
                and metadata.get("project_identity_assurance") == "unverified"
            ):
                detail = (
                    "현재 프로젝트 경로를 검증하지 못했습니다. 창은 보존됩니다. "
                    "ControlStudio에서 working copy를 열어 연결해 주세요."
                )
            else:
                detail = (
                    "XG5000 창은 찾았지만 요청한 PID/HWND, 프로젝트, 프로세스 "
                    "수명에 묶인 읽기 세션을 확인하지 못했습니다."
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )
        clear_ls_controller_target()
        session = bind_engineering_session(validated)
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(
                mode="json",
                by_alias=True,
            ),
        )
        return AttachXG5000ResponseV1(session=session)

    @app.post(
        "/api/v1/engineering/offline-candidate-session",
        response_model=PrepareOfflineCandidateSessionResponseV1,
    )
    async def prepare_offline_candidate_session_v1(
        request: Request,
        body: PrepareOfflineCandidateSessionRequestV1,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> PrepareOfflineCandidateSessionResponseV1:
        """Bind the displayed saved-build copy for a user-requested candidate.

        This performs no edit and grants no direct mutation capability.  It
        replaces the analysis-only rebind with one Host-signed binding that the
        existing offline approval broker can later use for this exact target.
        """

        session_before_bind = active_engineering_session
        if not _engineering_session_read_ready(session_before_bind):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 XG5000 읽기 세션을 먼저 확인해 주세요.",
            )
        if not isinstance(active_workspace, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="오프라인 후보에는 현재 보호 작업공간이 필요합니다.",
            )
        conversation_before_bind = store.get_conversation(body.conversation_id)
        if conversation_before_bind is None:
            raise HTTPException(status_code=404, detail="현재 대화를 찾지 못했습니다.")
        if (
            conversation_before_bind.workflow != "engineering"
            or conversation_before_bind.workspace_id != active_workspace.get("workspace_id")
            or conversation_before_bind.revision_id != active_workspace.get("revision_id")
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="선택한 대화와 현재 작업 복사본이 다릅니다.",
            )
        provenance = active_workspace.get("provenance")
        operation_id = (
            str(provenance.get("native_project_id") or "")
            if isinstance(provenance, dict)
            else ""
        )
        try:
            pid = int(session_before_bind["pid"])
            hwnd = int(session_before_bind["hwnd"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 분석 세션의 PID/HWND 정체성이 없습니다.",
            ) from exc
        if not _is_canonical_uuid(operation_id) or pid <= 0 or hwnd <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 분석 대상은 오프라인 후보로 준비할 수 없습니다.",
            )
        try:
            result = await native_project_service.rebind_completed_bound_analysis_session(
                operation_id,
                actor_id=_request_actor_id(request),
                pid=pid,
                hwnd=hwnd,
                active_workspace=active_workspace,
                offline_candidate_revision=True,
            )
        except (MCPToolError, NativeProjectConflict) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "현재 XG5000 창을 보호 작업 복사본의 오프라인 후보 대상으로 "
                    "다시 검증하지 못했습니다. 창이나 작업공간은 변경하지 않았습니다."
                ),
            ) from exc
        working_copy = active_workspace.get("working_copy")
        validated = _validated_engineering_session_result(
            result,
            pid=pid,
            hwnd=hwnd,
            project_path=(
                str(working_copy["path"])
                if isinstance(working_copy, dict) and isinstance(working_copy.get("path"), str)
                else None
            ),
            project_hash=(
                str(working_copy["current_sha256"])
                if isinstance(working_copy, dict)
                and isinstance(working_copy.get("current_sha256"), str)
                else None
            ),
        )
        if validated is None or not _engineering_session_identity_verified(validated):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP가 오프라인 후보 PID/작업 복사본 결합을 완전히 확인하지 못했습니다.",
            )
        try:
            store.rebind_engineering_conversation_session(
                conversation_id=body.conversation_id,
                workspace_id=str(active_workspace["workspace_id"]),
                revision_id=str(active_workspace["revision_id"]),
                engineering_session_id=str(validated["session_id"]),
                reason="offline_candidate_preparation",
            )
        except EngineeringMissionConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        clear_ls_controller_target()
        bound_session = bind_engineering_session(validated)
        store.append_event(
            event_type="engineering.offline_candidate_bound",
            payload={
                "actorId": _request_actor_id(request),
                "conversationId": body.conversation_id,
                "workspaceId": active_workspace.get("workspace_id"),
                "revisionId": active_workspace.get("revision_id"),
                "nativeProjectId": operation_id,
                "pid": pid,
                "hwnd": hwnd,
            },
        )
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        return PrepareOfflineCandidateSessionResponseV1(session=bound_session)

    @app.post(
        "/api/v1/xg5000/reviewed-no-save-close",
        response_model=ReviewedNoSaveCloseResponseV1,
    )
    async def reviewed_no_save_close_v1(
        body: ReviewedNoSaveCloseRequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> ReviewedNoSaveCloseResponseV1:
        """Explicitly close one displayed target; opening another remains separate."""

        nonlocal active_engineering_session
        session = active_engineering_session
        arguments = {
            "pid": body.pid,
            "hwnd": body.hwnd,
            "process_create_time": body.process_create_time,
            "confirm_no_save": True,
            "timeout_seconds": body.timeout_seconds,
        }
        for field in ("session_id", "project_path", "project_hash"):
            value = getattr(body, field)
            if value is not None:
                if field == "project_path":
                    try:
                        arguments[field] = str(Path(value).resolve())
                    except (OSError, ValueError) as exc:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="reviewed close project-path metadata is invalid",
                        ) from exc
                else:
                    arguments[field] = value.strip()
        try:
            raw = await mcp.call_workspace_tool("xg5000.reviewed_no_save_close", arguments)
            receipt_digest = _canonical_json_digest(raw)
            completed = _reviewed_no_save_close_response(
                raw,
                receipt_digest=receipt_digest,
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_reviewed_no_save_close_failure_detail(exc),
            ) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid reviewed no-save close receipt",
            ) from exc
        binding = completed.reviewed_binding
        if (
            binding.get("pid") != body.pid
            or binding.get("hwnd") != body.hwnd
            or not isinstance(binding.get("process_create_time"), (int, float))
            or abs(float(binding["process_create_time"]) - body.process_create_time) >= 0.001
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP reviewed close receipt binding does not match the confirmed target",
            )
        if any(binding.get(field) != arguments[field] for field in ("session_id", "project_path", "project_hash") if field in arguments):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP reviewed close receipt context does not match the confirmed target",
            )
        store.append_event(
            event_type="xg5000.reviewed_no_save_closed",
            payload={
                "actorId": _request_actor_id(request),
                "binding": dict(binding),
                "receiptDigest": receipt_digest,
                "automaticWorkspaceOpen": False,
                "processTerminated": False,
            },
        )
        active_session_lifetime = _engineering_session_process_create_time(session)
        if (
            isinstance(session, dict)
            and session.get("pid") == body.pid
            and session.get("hwnd") == body.hwnd
            and (
                active_session_lifetime is None
                or abs(active_session_lifetime - body.process_create_time) < 0.001
            )
        ):
            active_engineering_session = None
            execution_broker.clear_agent_session()
        store.append_event(
            event_type="host.status",
            payload=current_status_v1().model_dump(mode="json", by_alias=True),
        )
        return completed

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
