from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .agent_client import (
    _SAFE_PRIOR_DESIGN_FAILURE_REASON_CODES,
    _SAFE_PRIOR_DESIGN_RUN_ID,
    AgentSidecarCrashed,
    AgentSupervisor,
    SubprocessAgentClient,
    _bounded_design_failure_diagnostics,
    _bounded_prior_design_failure_diagnostics,
    _safe_design_edit_attempt_summary,
    _validated_approval_handoff,
    build_historical_evidence_bundle,
    project_historical_evidence,
    sidecar_failure_detail,
    sidecar_failure_diagnostic,
)
from .agent_protocol import (
    AgentProtocolError,
    native_checkpoint_boundary,
    scrub_agent_tool_result,
    validated_native_checkpoint_snapshot,
    validated_transcript_checkpoint,
)
from .agent_runtime import (
    AgentOutputContractDiagnostic,
    AgentOutputContractError,
    AgentRuntimeDiagnostic,
    AgentRuntimeError,
    ModelCapabilityResult,
    provider_failure_detail,
    validated_native_project_start,
)
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
    ChangePlanContract,
    ChangePlanV1,
    ChangeReceiptAcknowledgementV1,
    ChangeReconciliationResponseV1,
    ChangeTargetFocusV1,
    ChangeTargetIdentityV1,
    ChangeTransactionCreatedV1,
    ChangeTransactionV1,
    CloseIdleXG5000InstanceAttentionV1,
    CloseIdleXG5000InstanceRequestV1,
    CloseIdleXG5000InstanceResponseV1,
    CloseIdleXG5000RequestV1,
    ConfirmKnowledgeMemoryRequestV1,
    ConnectLSControllerRequestV1,
    ControllerRestoreBeginRequestV1,
    ControllerRestoreCompletedV1,
    ControllerRestoreCompleteRequestV1,
    ControllerRestoreHandoffV1,
    ControllerRestorePreflightRequestV1,
    ControllerRestorePreflightV1,
    ConversationCompletionV1,
    ConversationCreateV1,
    ConversationMessageV1,
    ConversationNativeExecutionV1,
    ConversationPageV1,
    ConversationSummaryV1,
    ConversationV1,
    CreateChangeTransactionRequestV1,
    CreateKnowledgeMemoryRequestV1,
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
    EngineeringProgramFocusRequestV1,
    EngineeringProgramFocusV1,
    EngineeringProgramsV1,
    EngineeringProgramV1,
    EngineeringSessionV1,
    EngineeringSystemStatusV1,
    EngineeringTargetBindingV1,
    ExecuteChangeTransactionRequestV1,
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
    ModelCapabilityFlagsV1,
    ModelCapabilityResultV1,
    ModelSettingsV1,
    NativeExecutionResumeRequestV1,
    OfflineOperandMissionV1,
    OpenLocalWorkspaceRequestV1,
    OpenLocalWorkspaceResponseV1,
    ProductFeatureStatusV1,
    ProjectBriefV1,
    ProjectPlanArtifactV1,
    ProjectPlanAttachmentV1,
    ProjectPlanReviewRequestV1,
    ProjectPlanReviewResponseV1,
    ProjectPlanReviewV1,
    ProjectPlanRevisionDeltaV1,
    ReadLSControllerRequestV1,
    ReconcileChangeTransactionRequestV1,
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
    MCPChangeExecutionService,
    _same_path,
    _sha256_file,
    _transaction_lookup_checkpoint,
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
    mcp_rejection_projection,
    validate_workspace_compare_result,
)
from .failure_location import host_failure_location_detail, safe_host_failure_location
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
    MAX_KNOWLEDGE_FILE_BYTES,
    SUPPORTED_KNOWLEDGE_SUFFIXES,
    KnowledgeIngestionError,
    extract_local_knowledge_document,
)
from .mcp_client import (
    AGENT_TOOL_NAMES,
    MCPGateway,
    MCPStartupError,
    MCPToolContractError,
    MCPToolError,
    MCPToolRejectedError,
    MCPTransportError,
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
    validate_reasoning_effort,
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
from .native_completion import (
    NativeCompletionLedger,
    completion_review_current,
    completion_state_key,
)
from .native_recovery_wait import owned_launch_wait_requires_park
from .native_workspace_host import (
    NativeWorkspaceExecution,
    latest_native_checkpoint,
    latest_native_checkpoint_summary,
    native_admission_generation,
    native_checkpoint_matches_request,
    native_followup_checkpoint,
    persist_native_checkpoint_with_index,
    saved_project_observation,
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
from .proposal_step_binding import (
    bound_proposal_step_id,
    record_proposal_step_binding,
)
from .restart import RestartBootstrapError, RestartHandoff, consume_restart_handoff
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
    program_source_identity,
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

_SAFE_HOST_SQLITE_STAGES = frozenset({
    "host_run", "native_checkpoint_restore", "native_checkpoint_resume_persist",
    "native_checkpoint_persist", "native_tool_receipt_persist",
    "native_saved_binding_persist", "native_event_persist",
})
_SAFE_STORE_SQLITE_STAGES = frozenset({
    "connection_open", "connection_foreign_keys", "connection_busy_timeout",
    "native_checkpoint_connect", "native_checkpoint_upsert", "native_checkpoint_commit",
})
_SAFE_SQLITE_ERROR_NAMES = frozenset({
    "SQLITE_ERROR", "SQLITE_INTERNAL", "SQLITE_PERM", "SQLITE_ABORT", "SQLITE_BUSY",
    "SQLITE_LOCKED", "SQLITE_NOMEM", "SQLITE_READONLY", "SQLITE_INTERRUPT", "SQLITE_IOERR",
    "SQLITE_CORRUPT", "SQLITE_NOTFOUND", "SQLITE_FULL", "SQLITE_CANTOPEN", "SQLITE_PROTOCOL",
    "SQLITE_EMPTY", "SQLITE_SCHEMA", "SQLITE_TOOBIG", "SQLITE_CONSTRAINT", "SQLITE_MISMATCH",
    "SQLITE_MISUSE", "SQLITE_NOLFS", "SQLITE_AUTH", "SQLITE_FORMAT", "SQLITE_RANGE", "SQLITE_NOTADB",
    "SQLITE_ERROR_MISSING_COLLSEQ", "SQLITE_ERROR_RETRY", "SQLITE_ERROR_SNAPSHOT",
    "SQLITE_BUSY_RECOVERY", "SQLITE_BUSY_SNAPSHOT", "SQLITE_BUSY_TIMEOUT",
    "SQLITE_LOCKED_SHAREDCACHE", "SQLITE_LOCKED_VTAB", "SQLITE_ABORT_ROLLBACK",
    "SQLITE_READONLY_RECOVERY", "SQLITE_READONLY_CANTLOCK", "SQLITE_READONLY_ROLLBACK",
    "SQLITE_READONLY_DBMOVED", "SQLITE_READONLY_CANTINIT", "SQLITE_READONLY_DIRECTORY",
    "SQLITE_CONSTRAINT_CHECK", "SQLITE_CONSTRAINT_COMMITHOOK", "SQLITE_CONSTRAINT_FOREIGNKEY",
    "SQLITE_CONSTRAINT_FUNCTION", "SQLITE_CONSTRAINT_NOTNULL", "SQLITE_CONSTRAINT_PRIMARYKEY",
    "SQLITE_CONSTRAINT_TRIGGER", "SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_VTAB",
    "SQLITE_CONSTRAINT_ROWID", "SQLITE_CONSTRAINT_PINNED", "SQLITE_CONSTRAINT_DATATYPE",
})


def _host_sqlite_boundary(stage: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Tag only an actual local SQLite exception; preserve its original identity and outcome."""
    try:
        return callback(*args, **kwargs)
    except sqlite3.Error as exc:
        try:
            exc.controlstudio_host_sqlite_stage = stage if stage in _SAFE_HOST_SQLITE_STAGES else "host_run"
        except Exception:
            pass
        raise


def _safe_host_sqlite_failure(exc: BaseException) -> dict[str, Any] | None:
    if not isinstance(exc, sqlite3.Error):
        return None
    # No exception text, SQL, parameters, traceback or provider fields enter this projection.
    code = getattr(exc, "sqlite_errorcode", None)
    if type(code) is not int or not 0 < code <= 65535 or not 1 <= (code & 255) <= 28:
        code = None
    name = getattr(exc, "sqlite_errorname", None)
    if (type(name) is not str or name not in _SAFE_SQLITE_ERROR_NAMES
            or code is None or getattr(sqlite3, name, None) != code):
        name = "unknown"
    stage = getattr(exc, "controlstudio_host_sqlite_stage", None)
    if type(stage) is not str or stage not in _SAFE_HOST_SQLITE_STAGES:
        stage = "host_run"
    result = {"stage": stage, "sqliteErrorCode": code, "sqliteErrorName": name}
    store_stage = getattr(exc, "controlstudio_store_stage", None)
    if type(store_stage) is str and store_stage in _SAFE_STORE_SQLITE_STAGES:
        result["storeStage"] = store_stage
    return result


def _host_sqlite_failure_detail(diagnostic: Mapping[str, Any]) -> str:
    return (
        "Host SQLite diagnostic: "
        f"stage={diagnostic['stage']}; sqliteErrorCode={diagnostic['sqliteErrorCode'] if diagnostic['sqliteErrorCode'] is not None else 'unknown'}; "
        f"sqliteErrorName={diagnostic['sqliteErrorName']}"
        + (f"; storeStage={diagnostic['storeStage']}" if "storeStage" in diagnostic else "")
        + "."
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


def _idle_launch_failure_detail(error: Exception) -> str:
    """Preserve the CLI's bounded diagnostic without exposing arbitrary MCP text."""

    detail = "the blank XG5000 launch could not be verified"
    diagnostic = _cli_failure_diagnostic(error)
    if diagnostic is None:
        return detail
    kind, message = diagnostic
    if kind == "OSError" and re.fullmatch(
        r"Operating system failure winerror 740(?: errno [0-9]+)?", message
    ):
        return (
            "XG5000 실행이 Windows 오류 740(권한 상승 필요)으로 거부됐습니다. "
            "XG5000 실행에 필요한 관리자 권한과 UAC 승인을 직접 확인해 주세요."
        )
    return f"{detail}: {kind}: {message}"


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


def _validate_reviewed_no_save_close_binding(
    raw: Mapping[str, Any],
    *,
    pid: int,
    hwnd: int,
    process_create_time: float,
    context: Mapping[str, Any] | None = None,
    require_no_discard: bool = False,
) -> dict[str, Any]:
    """Validate the exact reviewed-close receipt shared by close/reopen paths."""

    binding = raw.get("reviewed_binding")
    if (
        raw.get("operation") != "xg5000.reviewed_no_save_close"
        or raw.get("verified") is not True
        or raw.get("source_closed") is not True
        or raw.get("automatic_workspace_open") is not False
        or raw.get("process_terminated") is not False
        or not isinstance(binding, dict)
        or binding.get("pid") != pid
        or binding.get("hwnd") != hwnd
        or type(binding.get("process_create_time")) not in {int, float}
        or not math.isfinite(binding["process_create_time"])
        or binding["process_create_time"] <= 0
        or type(process_create_time) not in {int, float}
        or not math.isfinite(process_create_time)
        or process_create_time <= 0
        or abs(float(binding["process_create_time"]) - process_create_time) >= 0.001
        or (
            require_no_discard
            and (
                raw.get("discardUnsavedChanges") is not False
                or raw.get("unsavedChangesDiscarded") is not False
            )
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MCP reviewed close result did not prove the exact target closure",
        )
    if any(
        binding.get(field) != value
        for field, value in (context or {}).items()
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MCP reviewed close result changed the exact target context",
        )
    return dict(binding)


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
    launch_mode: Literal["when_none", "new_instance"] = "when_none",
) -> LaunchIdleXG5000ResponseV1:
    if raw.get("launch_mode", "when_none") != launch_mode:
        raise ValueError("MCP idle launch mode does not match the request")
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
        | {"receipt_digest": receipt_digest, "launch_mode": raw.get("launch_mode", "when_none")}
    )


def _launch_idle_xg5000_attention(
    raw: dict[str, Any],
    *,
    receipt_digest: str,
    launch_mode: Literal["when_none", "new_instance"] = "when_none",
) -> LaunchIdleXG5000AttentionV1:
    if raw.get("launch_mode", "when_none") != launch_mode:
        raise ValueError("MCP idle launch mode does not match the request")
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
        | {"receipt_digest": receipt_digest, "launch_mode": raw.get("launch_mode", "when_none")}
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
    "project.inspect_symbols_ui": (
        "program_location",
        "변수·설명 창 조사",
        "현재 XG5000의 변수·설명 관련 창과 컨트롤 상태를 읽었습니다.",
        "confirmed",
    ),
    "project.open_symbols_ui": (
        "program_location",
        "변수·설명 창 탐색",
        "현재 프로젝트의 변수·설명 창 탐색 결과와 표시 상태를 조사했습니다.",
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

# Tool activity is a deliberately smaller projection than Agent/MCP results.
# Keep every value Host-authored and tied to the same explicit tool allowlist
# used for verified evidence; model arguments, paths, endpoints, and raw result
# text must never enter the durable activity event.
_AGENT_TOOL_ACTIVITY_ROLE: dict[str, str] = {
    "xg5000.session_status": "상태 확인",
    "sim.target_status": "안전 확인",
    "project.list_programs": "프로젝트 읽기",
    "project.inspect_symbols_ui": "변수·설명 조사",
    "project.open_symbols_ui": "변수·설명 탐색",
    "program.focus": "프로그램 탐색",
    "program.read_ld": "래더 읽기",
    "project.hardware_context": "하드웨어 분석",
    "workspace.compare": "작업 사본 비교",
    "workspace.hardware_context": "하드웨어 분석",
    "workspace.structure_index": "구조 분석",
    "workspace.search": "근거 검색",
    "project.structure_index": "구조 분석",
    "project.search": "근거 검색",
    "program.change_target_analysis": "변경 영향 분석",
    
    
    
    
    "program.logic_graph": "논리 분석",
    "program.state_model": "상태 분석",
    "device.causal_impact": "영향 분석",
    "device.references": "참조 분석",
    "project.behavior_report": "동작 분석",
    "project.snapshot": "읽기 스냅샷",
    "controller.ls.status": "온라인 읽기",
    "controller.ls.read": "온라인 읽기",
    "controller.ls.trace": "온라인 추적",
}
_AGENT_TOOL_ACTIVITY_TARGET: dict[str, str] = {
    **{
        tool_name: "현재 작업 사본"
        for tool_name in {
            "workspace.compare",
            "workspace.hardware_context",
            "workspace.structure_index",
            "workspace.search",
        }
    },
    **{
        tool_name: "연결된 LS PLC"
        for tool_name in {
            "controller.ls.status",
            "controller.ls.read",
            "controller.ls.trace",
        }
    },
    "xg5000.session_status": "현재 XG5000 세션",
    "sim.target_status": "현재 XG5000 세션",
    **{
        tool_name: "현재 XG5000 프로젝트"
        for tool_name in {
            "project.list_programs",
            "project.inspect_symbols_ui",
            "project.open_symbols_ui",
            "program.focus",
            "program.read_ld",
            "project.hardware_context",
            "project.structure_index",
            "project.search",
            "program.change_target_analysis",
            
            
            
            
            "program.logic_graph",
            "program.state_model",
            "device.causal_impact",
            "device.references",
            "project.behavior_report",
            "project.snapshot",
        }
    },
}

_AGENT_TOOL_FAILURE_REASON_BY_CODE: dict[str, str] = {
    "cancelled": "선택한 도구 실행이 취소되었습니다.",
    "tool_rejected": "Host가 선택한 도구 요청을 거부했습니다.",
    "transport_error": "도구 실행 서비스와 통신하지 못했습니다.",
    "host_policy": "Host 안전 정책이 선택한 도구 실행을 허용하지 않았습니다.",
    "change_verification_rolled_back": (
        "변경 검증에 실패했으며 작업 사본을 원복했습니다."
    ),
    "change_preflight_failed_before_mutation": (
        "실행 사전조건을 충족하지 못해 작업 사본을 변경하지 않았습니다."
    ),
    "change_execution_failed": "직접 변경 transaction이 실패 상태로 종료되었습니다.",
    "change_proposal_invalid": "변경 계약의 입력 검증에 실패하여 제안을 만들지 않았습니다.",
    "design_revision_conflict": "설계 초안이 갱신되어 현재 내용을 다시 확인해야 합니다.",
    "design_edit_not_applied": "요청한 설계 수정을 적용하지 못했습니다.",
    "design_edit_unchanged": "기존 내용과 같아 초안을 변경하지 않았습니다.",
    "native_execution_not_completed": "네이티브 실행이 완료되지 않았습니다. 반환된 작업 상태와 실패 관측을 확인해야 합니다.",
    "design_edit_scope_rejected": "현재 수정 범위 제한으로 해당 변경을 적용하지 못했습니다.",
    "design_edit_schema_invalid": "설계 내용 형식이 맞지 않아 해당 수정을 적용하지 못했습니다.",
    "design_publish_blocked": "검사를 통과하지 못해 검토안을 확정하지 않았습니다.",
}
_AGENT_TOOL_FAILURE_REASON_BY_KIND: dict[str, str] = {
    "tool_error": "도구가 요청을 처리하지 못했습니다.",
    "host_policy": "Host 안전 정책이 도구 실행을 허용하지 않았습니다.",
    "transport": "도구 실행 서비스와 통신하지 못했습니다.",
    "cancelled": "도구 실행이 취소되었습니다.",
}


def _agent_tool_failure_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project current and historical failures without replaying raw diagnostics."""

    rejection = mcp_rejection_projection(payload.get("mcp_rejection"))
    diagnostic = {"mcp_rejection": rejection} if rejection is not None else {}
    reason_code = payload.get("reason_code")
    if isinstance(reason_code, str) and reason_code in _AGENT_TOOL_FAILURE_REASON_BY_CODE:
        projected = {
            "failure_reason": _AGENT_TOOL_FAILURE_REASON_BY_CODE[reason_code],
            "reason_code": reason_code,
        }
        if reason_code == "change_proposal_invalid" and payload.get("stage") == "proposal":
            projected["stage"] = "proposal"
        return {**projected, **diagnostic}
    failure_kind = payload.get("failure_kind")
    return {
        "failure_reason": _AGENT_TOOL_FAILURE_REASON_BY_KIND.get(
            str(failure_kind),
            "도구 실행을 완료하지 못했습니다.",
        ),
        **diagnostic,
    }


def _prior_design_failure_observation(
    events: Sequence[StoredEvent],
    *,
    conversation_id: str,
    current_run_id: str,
) -> dict[str, Any] | None:
    """Select the latest prior terminal design edit for a planning restart.

    The event journal is the only source of this observation.  A later
    successful ``design.edit`` is a deliberate tombstone for older failures;
    an older failure must never be resurrected after a successful edit.  This
    projection carries no design revision, digest, or validity claim: the
    next Agent run must inspect/read the current draft before deciding what to
    do.
    """

    for event in sorted(events, key=lambda item: item.sequence, reverse=True):
        if (
            event.conversation_id != conversation_id
            or event.run_id is None
            or event.run_id == current_run_id
            or event.event_type not in {"tool.completed", "tool.failed"}
            or not isinstance(event.payload, Mapping)
            or event.payload.get("tool_name") != "design.edit"
        ):
            continue
        if event.event_type == "tool.completed":
            # The latest terminal edit, regardless of whether it changed the
            # draft, supersedes all older failure observations.
            return None
        projected = _agent_tool_failure_projection(event.payload)
        reason_code = projected.get("reason_code")
        if reason_code not in _SAFE_PRIOR_DESIGN_FAILURE_REASON_CODES:
            # Do not fall back to an older event when the latest failure is not
            # a recognized, safely projectable design-edit failure.
            return None
        if (
            not isinstance(event.sequence, int)
            or isinstance(event.sequence, bool)
            or not 1 <= event.sequence <= 9_223_372_036_854_775_807
            or not isinstance(event.run_id, str)
            or _SAFE_PRIOR_DESIGN_RUN_ID.fullmatch(event.run_id) is None
            or not isinstance(event.created_at, datetime)
            or event.created_at.tzinfo is None
            or event.created_at.utcoffset() is None
        ):
            return None
        return {
            "kind": "historical_design_edit_failure",
            "runId": event.run_id,
            "eventSequence": event.sequence,
            "eventCreatedAt": event.created_at.isoformat(),
            "reasonCode": reason_code,
            "diagnostics": _bounded_prior_design_failure_diagnostics(event.payload),
        }
    return None


def _bounded_tool_activity_text(value: str, *, maximum: int = 180) -> str:
    return " ".join(value.split())[:maximum]


_DESIGN_READ_ACTIVITY_SECTIONS = frozenset(
    {
        "narrative",
        "technical_configuration",
        "index_acceptance_bindings",
        "simulation_scenario",
        "collection_scope",
        "io_points",
        "operating_modes",
        "control_networks",
        "ladder_rungs",
        "timers",
        "sequences",
        "alarms",
        "recovery_rules",
        "safety_rules",
    }
)


def _safe_design_read_activity_metadata(
    value: object,
) -> dict[str, Any] | None:
    """Project one successful design.read identity into durable activity.

    The design.read result contains the requested section values, so it must
    never be copied into an activity event.  This helper accepts only the
    small, Host-defined identity vocabulary needed to distinguish a current
    read from a historical read.  A malformed or untrusted projection is
    omitted rather than partially persisted.
    """

    if not isinstance(value, Mapping):
        return None
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        return None
    if any(
        type(section) is not str
        or section not in _DESIGN_READ_ACTIVITY_SECTIONS
        for section in sections
    ):
        return None
    if (
        len(sections) > len(_DESIGN_READ_ACTIVITY_SECTIONS)
        or len(sections) != len(set(sections))
    ):
        return None
    read_revision = value.get("read_revision")
    current_revision = value.get("current_revision")
    is_current_revision = value.get("is_current_revision")
    schema_requested = value.get("schema_requested")
    if (
        type(read_revision) is not int
        or read_revision < 0
        or type(current_revision) is not int
        or current_revision < 0
        or type(is_current_revision) is not bool
        or is_current_revision != (read_revision == current_revision)
        or type(schema_requested) is not bool
    ):
        return None
    schema_request_explicit = value.get("schema_request_explicit")
    if schema_request_explicit is not None and type(schema_request_explicit) is not bool:
        return None
    provided_sections = value.get("schema_sections_provided")
    if provided_sections is not None and (
        not isinstance(provided_sections, list)
        or any(type(section) is not str or section not in sections for section in provided_sections)
        or len(provided_sections) > len(sections)
        or len(provided_sections) != len(set(provided_sections))
    ):
        return None
    return {
        "sections": list(sections),
        "read_revision": read_revision,
        "current_revision": current_revision,
        "is_current_revision": is_current_revision,
        "revision_scope": "current" if is_current_revision else "historical",
        "schema_requested": schema_requested,
        **({"schema_request_explicit": schema_request_explicit}
           if schema_request_explicit is not None else {}),
        **({"schema_sections_provided": list(provided_sections)}
           if provided_sections is not None else {}),
    }


def _design_read_activity_from_result(
    arguments: Mapping[str, Any],
    result: object,
) -> dict[str, Any] | None:
    """Build the bounded activity identity from the Host-owned read result."""

    if not isinstance(result, Mapping):
        return None
    schema_requested = arguments.get("include_schema", False)
    if type(schema_requested) is not bool:
        return None
    requested_sections = arguments.get("sections")
    result_sections = result.get("sections")
    if (
        not isinstance(requested_sections, list)
        or not isinstance(result_sections, Mapping)
        or any(type(section) is not str for section in requested_sections)
        or any(type(section) is not str for section in result_sections)
        or set(requested_sections) != set(result_sections)
    ):
        return None
    schemas = result.get("sectionSchemas")
    provided_metadata = {}
    if (
        result.get("sectionSchemaBasis") == "current_design_edit"
        and isinstance(schemas, Mapping)
        and all(isinstance(schema, Mapping) for schema in schemas.values())
    ):
        provided_metadata["schema_sections_provided"] = list(schemas)
    return _safe_design_read_activity_metadata(
        {
            "sections": requested_sections,
            "read_revision": result.get("readRevision"),
            "current_revision": result.get("currentRevision"),
            "is_current_revision": result.get("isCurrentRevision"),
            "schema_requested": schema_requested,
            "schema_request_explicit": "include_schema" in arguments,
            **provided_metadata,
        }
    )


def _agent_tool_activity_projection(
    *,
    tool_name: str,
    event_type: str,
    change_request_scope: str,
    change_outcome_state: str | None,
    simulator_target_status: dict[str, Any] | None,
    design_read_activity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Host-owned compact-card fields for one durable tool event."""

    canonical_tool_name = AGENT_TOOL_NAMES.get(tool_name, tool_name)
    design_activity = {
        "design.inspect": (
            "설계 진행 상태 확인",
            "다음 작업 판단",
            "저장된 설계 초안과 남은 작업을 확인했습니다.",
        ),
        "design.read": (
            "설계 내용 확인",
            "부분 설계 검토",
            "수정에 필요한 설계 부분을 확인했습니다.",
        ),
        "design.search": (
            "설계 사용 위치 검색",
            "수정 대상 조사",
            "요청한 문자열이 쓰인 설계 항목과 위치를 확인했습니다.",
        ),
        "design.edit": (
            "설계 내용 작성",
            "부분 설계 수정",
            "설계 초안의 필요한 부분을 저장했습니다.",
        ),
        "design.check": (
            "설계 오류 검사",
            "설계 검증",
            "현재 설계의 참조와 실행 가능성을 검사했습니다.",
        ),
        "design.publish_review": (
            "검토안 확정",
            "검토 준비",
            "검사를 통과한 설계를 사용자 검토안으로 확정했습니다.",
        ),
    }.get(canonical_tool_name)
    if design_activity is not None:
        label, role, result = design_activity
        projection = {
            "activity_label": label,
            "activity_role": role,
            "activity_target": "현재 프로젝트 설계 초안",
        }
        if event_type == "tool.completed":
            projection["activity_result"] = result
            if canonical_tool_name == "design.read":
                bounded_read_activity = _safe_design_read_activity_metadata(
                    design_read_activity
                )
                if bounded_read_activity is not None:
                    projection["design_read_activity"] = bounded_read_activity
        return projection
    if canonical_tool_name == "change.propose_offline":
        if change_request_scope == "permanent_offline_revision":
            result = {
                "COMPLETED": "작업 사본 수정·검사·저장을 완료했습니다.",
                "ROLLED_BACK": "변경 검증 뒤 작업 사본을 원복했습니다.",
                "FAILED": "변경 transaction이 실패 상태로 끝났습니다.",
            }.get(change_outcome_state, "수정·검사·저장 결과를 확정했습니다.")
            projection = {
                "activity_label": "작업 사본 수정·검사·저장",
                "activity_role": "오프라인 변경 실행",
                "activity_target": "현재 작업 사본",
            }
        elif change_request_scope == "offline_candidate_revision":
            result = "승인할 오프라인 변경 후보를 만들었습니다."
            projection = {
                "activity_label": "승인용 오프라인 변경 후보 준비",
                "activity_role": "변경 제안",
                "activity_target": "후보 작업 사본",
            }
        else:
            result = "오프라인 변경 요청 처리를 마쳤습니다."
            projection = {
                "activity_label": "오프라인 변경 요청 처리",
                "activity_role": "변경 요청",
                "activity_target": "현재 작업 사본",
            }
        if event_type == "tool.completed":
            projection["activity_result"] = result
        return projection

    definition = _AGENT_TOOL_EVIDENCE.get(canonical_tool_name)
    role = _AGENT_TOOL_ACTIVITY_ROLE.get(canonical_tool_name)
    target = _AGENT_TOOL_ACTIVITY_TARGET.get(canonical_tool_name)
    if definition is None or role is None or target is None:
        return {}

    projection = {
        "activity_role": role,
        "activity_target": target,
    }
    if event_type == "tool.completed":
        result = definition[2]
        if canonical_tool_name == "sim.target_status":
            if simulator_target_status == {
                "verified": True,
                "classification": "SIMULATOR_TARGET",
            }:
                result = "현재 세션이 XG-SIM 대상임을 확인했습니다."
            elif simulator_target_status == {
                "verified": False,
                "classification": "NOT_SIMULATOR_TARGET",
            }:
                result = "현재 세션은 XG-SIM 대상으로 확인되지 않았습니다."
        projection["activity_result"] = _bounded_tool_activity_text(result)
    return projection


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


def _workspace_engineering_conversation_session_id(workspace_id: str, revision_id: str) -> str:
    """Server-owned file-only conversation identity; never native authority."""
    target_digest = hashlib.sha256(
        json.dumps([workspace_id, revision_id], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"workspace:{target_digest}"

_ENGINEERING_TARGET_PRECONDITION_MESSAGE = (
    "이 대화가 생성된 XG5000 대상과 현재 대상이 다릅니다. "
    "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
)


def _native_checkpoint_target_matches(current: object, binding: object, *, file_only: bool = False) -> bool:
    """Compare target fields only; callers retain recovery ownership authority."""
    if not isinstance(current, dict) or not isinstance(binding, dict):
        return False
    if any(not isinstance(binding.get(key), str) or not binding[key]
            for key in ("workspace_id", "revision_id", "project_path")):
        return False
    if any(current.get(key) != binding[key] for key in ("workspace_id", "revision_id")):
        return False
    if not file_only:
        return current.get("project_path") == binding["project_path"]
    try:
        path = current.get("working_path")
        return (isinstance(path, str) and bool(path)
            and _normalized_workspace_path(path) == _normalized_workspace_path(binding["project_path"]))
    except (ValueError, TypeError, OSError):
        return False

def _native_resume_latest_attempt_matches_checkpoint(detail, source, source_message, latest, store) -> bool:
    """One read-only admission predicate shared by Web projection and resume."""
    if latest is None or source is None or source_message is None:
        return False
    return store.native_resume_latest_attempt_matches_checkpoint(
        conversation_id=detail.id, source_run_id=source.id, latest_run_id=latest.id,
        expected_error_detail=_ENGINEERING_TARGET_PRECONDITION_MESSAGE)


class AgentRunPreconditionError(RuntimeError):
    """A product-authored precondition message safe to display and persist."""


async def _proposal_precondition_result(
    propose: Callable[[], Awaitable[dict[str, Any]]],
    transaction_exists: Callable[[], bool],
) -> dict[str, Any]:
    try:
        return await propose()
    except AgentRunPreconditionError as exc:
        if transaction_exists():
            raise
        return {
            "status": "not_created",
            "execution_permitted": False,
            "next_action": "revise_proposal",
            "reason_code": "proposal_precondition",
            "message": str(exc)[:4000],
        }


def _agent_tool_evidence_ref(
    *,
    conversation_id: str,
    run_id: str,
    tool_name: str,
    request_id: str,
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
    call_identity = tool_call_id or request_id
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
        "requestId": request_id,
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
        "xg5000.session_status": (
            "controlstudio-xg5000-session-status-evidence/v1"
        ),
        "program.read_ld": "controlstudio-program-read-ld-evidence/v1",
        "project.hardware_context": "controlstudio-hardware-context-evidence/v1",
        "workspace.compare": "controlstudio-workspace-compare-evidence/v1",
        "workspace.hardware_context": (
            "controlstudio-workspace-hardware-context-evidence/v1"
        ),
        "program.change_target_analysis": "controlstudio-change-target-evidence/v2",
        
        
        
        
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
        if canonical_tool_name == "program.read_ld":
            program_name = evidence_contract.get("programName")
            if isinstance(program_name, str) and 0 < len(program_name) <= 512:
                evidence["title"] = f"{program_name} · 래더 읽기"
                evidence["summary"] = (
                    f"{program_name} 프로그램은 비어 있습니다."
                    if evidence_contract.get("contentState") == "empty"
                    else f"{program_name} 프로그램에서 접점과 명령을 읽었습니다."
                )
                evidence["location"] = {"vendor": "LS", "program": program_name}
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
    if value["knowledge_ref"].partition("?")[0] != f"knowledge://{value['source_id']}":
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


def _planning_message_display(content: str, value: Any) -> str:
    """Show the entered brief, keeping execution instructions in stored content."""
    try:
        brief = ProjectBriefV1.model_validate(value)
    except (ValueError, TypeError):
        brief = None
    if brief is None:
        return content
    consultation = _planning_prompt_from_brief(brief)
    creation = consultation.removesuffix(_LEGACY_PROJECT_BRIEF_FOOTER) + (
        "자료와 요구사항을 확인한 뒤 새 프로젝트와 첫 LD 프로그램을 준비하고, 같은 대화에서 "
        "LD 작성·검사·저장까지 진행해 주세요. 준비에 필요한 조건이 불명확하면 질문해 주세요."
    )
    if content not in {consultation, creation}:
        return content
    sections = [f"{brief.project_name} 프로젝트를 만들고 싶습니다.", brief.machine_purpose]
    for label, text in (
        ("동작 순서", brief.sequence),
        ("하드웨어·입출력", brief.hardware_and_io),
        ("안전·운영 조건", brief.safety_and_interlocks),
        ("운전·복구", brief.operating_modes),
        ("성능·완료 기준", brief.performance_and_acceptance),
    ):
        if text:
            sections.append(f"{label}\n{text}")
    if brief.attachment_source_ids:
        sections.append(f"참고 자료 {len(brief.attachment_source_ids)}개 첨부")
    return "\n\n".join(sections)


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


def _validate_agent_approval_handoff(
    output: Any, *, transaction: ChangeTransaction | None,
    proposed_authority: tuple[str, str] | None,
    transcript: Any, conversation_id: str, run_id: str, actor_id: str,
    workspace_id: str | None, revision_id: str | None,
) -> dict[str, str]:
    """Admit an approval yield, not a model-authored mission or native result."""
    try:
        handoff = _validated_approval_handoff(output.approval_handoff)
    except (AttributeError, TypeError, ValueError) as exc:
        raise AgentOutputContractError("Agent approval handoff is invalid") from exc
    if output.workflow != "engineering" or any(getattr(output, field, None) is not None for field in (
        "engineering_mission", "digest", "cycle_disposition",
        "engineering_analysis", "engineering_analysis_digest",
        "plan_status", "plan_artifact", "plan_digest", "project_name",
    )):
        raise AgentOutputContractError("Agent approval handoff carries model completion artifacts")
    if (
        transaction is None
        or proposed_authority != (handoff["transactionId"], handoff["planDigest"])
        or transaction.id != handoff["transactionId"]
        or transaction.plan_digest != handoff["planDigest"]
        or transaction.conversation_id != conversation_id
        or transaction.run_id != run_id
        or transaction.requested_by_actor_id != actor_id
        or transaction.state is not ChangeTransactionState.PENDING_APPROVAL
        or transaction.approval_expires_at <= datetime.now(timezone.utc)
    ):
        raise AgentOutputContractError("Agent approval handoff differs from current run authority")
    if (
        transcript is None or transcript.source_run_id != run_id
        or transcript.conversation_id != conversation_id
        or transcript.workspace_id != workspace_id
        or transcript.revision_id != revision_id
    ):
        raise AgentOutputContractError("Agent approval handoff has no current completed transcript")
    _, _, tool_parts, _ = validated_transcript_checkpoint(transcript.checkpoint, transcript.digest)
    if not any(
        part.get("part_kind") == "tool-return"
        and part.get("outcome") == "success"
        and isinstance(part.get("content"), dict)
        and part["content"].get("transaction_id") == transaction.id
        and part["content"].get("plan_digest") == transaction.plan_digest
        and part["content"].get("state") == "PENDING_APPROVAL"
        and part["content"].get("execution_permitted") is False
        and part["content"].get("next_action") == "human_approval_required"
        for part in tool_parts
    ):
        raise AgentOutputContractError("Agent approval handoff lacks its completed proposal result")
    return handoff


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








def _stored_candidate_continuation_scope(
    run_metadata: Mapping[str, Any], message_role: MessageRole | None,
) -> tuple[str, str] | None:
    """Recover candidate intent across retries without inheriting direct authority.

    Continuations deliberately have an analysis-only ordinary scope. Their
    separately journaled candidate intent must survive a later failed attempt;
    the terminal receipt/target gates still authorize every subsequent run.
    """
    verification = run_metadata.get("terminal_candidate_reproposal_verification_scope")
    if (
        message_role is MessageRole.SYSTEM
        and run_metadata.get("engineering_mission_continue") is True
        and run_metadata.get("change_request_scope") == "analysis_only"
        and run_metadata.get("terminal_candidate_reproposal_scope") == "offline_candidate_revision"
        and verification in {
            "offline_native_check_only", "offline_native_reopen", 
        }
    ):
        return "offline_candidate_revision", str(verification)
    return None


def _explicit_user_direct_change_authority(
    *,
    request_message_role: MessageRole | None,
    engineering_mission_continue: bool,
    change_request_scope: str,
    verification_scope: str,
) -> bool:
    """Return true only for the exact user-authored run carrying direct authority."""

    return bool(
        request_message_role is MessageRole.USER
        and not engineering_mission_continue
        and change_request_scope == "permanent_offline_revision"
        and verification_scope == "offline_native_check_only"
    )


def _proposal_scopes_for_run(
    *,
    change_request_scope: str,
    verification_scope: str,
    terminal_candidate_reproposal_authorized: bool,
    terminal_candidate_reproposal_verification_scope: Any,
) -> tuple[str, str]:
    """Separate ordinary run authority from the narrow terminal candidate path."""

    return change_request_scope, verification_scope


def _allowed_verification_scopes_for_run(
    *,
    request_scope: str,
    verification_scope: str,
    simulation_capability_ready: bool,
    engineering_mission_continue: bool,
    terminal_candidate_reproposal_authorized: bool,
) -> tuple[str, ...]:
    """LD operand proposals use the explicitly selected verification scope."""
    return (verification_scope,)




def _proposal_verification_scope_for_callback(
    *,
    plan_kind: Any,
    requested_scope: Any,
    explicit_scope: str,
    allowed_scopes: tuple[str, ...],
) -> str:
    """Validate the model-selected scope at the change callback boundary."""

    if requested_scope != explicit_scope:
        raise ValueError(
            "legacy operand proposal verification_scope must equal "
            "the explicit user selection"
        )
    if not isinstance(requested_scope, str):
        raise ValueError("proposal verification_scope must be text")
    return requested_scope


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
    event_payload = dict(event.payload)
    if event.event_type == "tool.failed":
        # Older Host versions persisted raw exception text in failure_reason.
        # Re-project every replay so a new compact card never exposes that
        # historical path, endpoint, or provider diagnostic.
        event_payload.pop("failure_reason", None)
        # Validation details remain durable for support/audit retrieval but are
        # not part of the ordinary browser activity card.
        event_payload.pop("diagnostics", None)
        event_payload.update(_agent_tool_failure_projection(event.payload))
    payload = {
        "eventId": f"event-{event.sequence}",
        "sequence": event.sequence,
        "occurredAt": event.created_at.isoformat(),
        "conversationId": event.conversation_id,
        "runId": event.run_id,
        "eventType": event.event_type,
        "payloadSchemaVersion": "1",
        "payload": event_payload,
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


def _conversation_completion_v1(
    detail: ConversationDetail,
    *,
    project_plan_authority_digests: tuple[str, str] | None = None,
    engineering_mission: EngineeringMission | None = None,
    native_completion_current_state: dict[str, Any] | None = None,
    native_completion_saved_sha256: str | None = None,
) -> ConversationCompletionV1:
    """Project goal state separately from completion of one Agent turn.

    ``RunState.COMPLETED`` means only that one Agent turn produced a durable
    response.  For planning, a ready/published revision is a completed design
    phase that still needs the next product phase. Goal completion comes from
    a current native completion review or a validated terminal engineering mission.
    """

    def digest_or_none(value: Any) -> str | None:
        return (
            value
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            else None
        )

    latest_answer = next((item for item in reversed(detail.messages)
                          if item.role is MessageRole.ASSISTANT), None)
    if latest_answer is not None and latest_answer.metadata.get("native_ld_execution") is True:
        latest_user = next((item for item in reversed(detail.messages) if item.role is MessageRole.USER), None)
        if (latest_user is not None and detail.messages.index(latest_user) > detail.messages.index(latest_answer)):
            return ConversationCompletionV1(status="not_claimed", phase="engineering", basis="no_completion_evidence")
        review = latest_answer.metadata.get("native_completion_review")
        if completion_review_current(review, detail=detail, current_state=native_completion_current_state,
                                     current_saved_sha256=native_completion_saved_sha256):
            return ConversationCompletionV1(
                status="goal_complete" if review["scope"] == "project_goal" else "request_complete",
                phase="engineering", basis=("native_project_goal_assessed" if review["scope"] == "project_goal"
                    else "native_request_assessed"), source_message_id=latest_answer.id,
                source_digest=review["assessment_sha256"])
        disposition = latest_answer.metadata.get("native_request_disposition")
        status = disposition.get("status") if isinstance(disposition, dict) else None
        # This is the recorded model stop reason, not independently verified success.
        trusted_shape = bool(isinstance(disposition, dict)
            and disposition.get("schema_version") == "1"
            and disposition.get("summary") == latest_answer.content
            and disposition.get("is_goal_completion_evidence") is False)
        basis = ("native_request_blocked" if trusted_shape and status == "blocked"
                 else "native_request_needs_user" if trusted_shape and status == "needs_user"
                 else "native_completion_unverified")
        return ConversationCompletionV1(
            status="blocked" if basis == "native_request_blocked" else "not_claimed",
            phase="engineering", basis=basis, source_message_id=latest_answer.id)

    if detail.workflow == "new_project_planning":
        latest_plan_message = next(
            (
                item
                for item in reversed(detail.messages)
                if item.role is MessageRole.ASSISTANT
                and isinstance(item.metadata.get("plan_artifact"), dict)
                and digest_or_none(item.metadata.get("plan_digest")) is not None
            ),
            None,
        )
        if latest_plan_message is None:
            return ConversationCompletionV1(
                status="not_claimed",
                phase="planning",
                basis="no_completion_evidence",
            )
        plan_digest = digest_or_none(latest_plan_message.metadata.get("plan_digest"))
        plan_status = latest_plan_message.metadata.get("plan_status")
        if plan_status == "needs_clarification":
            basis = "planning_needs_clarification"
        elif plan_status == "ready_for_review":
            review = latest_plan_message.project_plan_review
            reviewed = bool(
                review is not None
                and review.schema_version == "2"
                and review.plan_digest == plan_digest
                and project_plan_authority_digests is not None
                and review.project_brief_digest == project_plan_authority_digests[0]
                and review.attachment_revision_digest == project_plan_authority_digests[1]
            )
            basis = "planning_reviewed" if reviewed else "planning_ready_for_review"
        else:
            return ConversationCompletionV1(
                status="not_claimed",
                phase="planning",
                basis="no_completion_evidence",
                source_message_id=latest_plan_message.id,
                source_digest=plan_digest,
            )
        return ConversationCompletionV1(
            # A planning publication is deliberately never an end-to-end goal
            # claim. Native build, persistence, readback, and testing belong to
            # the subsequent engineering phase. Clarification is not even a
            # completed planning phase.
            status=(
                "phase_complete"
                if basis in {"planning_ready_for_review", "planning_reviewed"}
                else "not_claimed"
            ),
            phase="planning",
            basis=basis,
            source_message_id=latest_plan_message.id,
            source_digest=plan_digest,
        )

    if engineering_mission is None:
        return ConversationCompletionV1(
            status="not_claimed",
            phase="engineering",
            basis="no_completion_evidence",
        )
    try:
        snapshot, canonical_digest = _canonical_engineering_mission(
            engineering_mission.engineering_mission
        )
    except (TypeError, ValueError):
        return ConversationCompletionV1(
            status="blocked",
            phase="engineering",
            basis="invalid_engineering_mission",
            source_digest=digest_or_none(engineering_mission.digest),
        )
    if canonical_digest != engineering_mission.digest:
        return ConversationCompletionV1(
            status="blocked",
            phase="engineering",
            basis="invalid_engineering_mission",
            source_digest=digest_or_none(engineering_mission.digest),
        )
    mission_status = snapshot["status"]
    if mission_status == "completed":
        return ConversationCompletionV1(
            status="goal_complete",
            phase="engineering",
            basis="engineering_objective_achieved",
            source_digest=canonical_digest,
        )
    if mission_status == "blocked":
        return ConversationCompletionV1(
            status="blocked",
            phase="engineering",
            basis="engineering_blocked",
            source_digest=canonical_digest,
        )
    basis_by_status = {
        "waiting_for_user": "engineering_waiting_for_user",
        "waiting_for_approval": "engineering_waiting_for_approval",
    }
    if mission_status in basis_by_status:
        basis = basis_by_status[mission_status]
    elif snapshot["stopReason"] == "cycle_budget_exhausted":
        basis = "engineering_cycle_budget_exhausted"
    else:
        basis = "engineering_in_progress"
    return ConversationCompletionV1(
        status="not_claimed",
        phase="engineering",
        basis=basis,
        source_digest=canonical_digest,
    )


def _conversation_v1(
    detail: ConversationDetail,
    *,
    project_plan_authority_digests: tuple[str, str] | None = None,
    engineering_mission: EngineeringMission | None = None,
    native_completion_current_state: dict[str, Any] | None = None,
    native_completion_saved_sha256: str | None = None,
) -> ConversationV1:
    request_run_ids = {
        run.request_message_id: run.id
        for run in detail.runs
        if run.request_message_id is not None
    }
    run_states = {run.id: run.state.value.casefold() for run in detail.runs}
    messages = [
        ConversationMessageV1(
            message_id=item.id,
            role=item.role.value,
            content=(
                _planning_message_display(
                    item.content,
                    item.metadata.get("project_brief"),
                )
                if item.role.value == "user" and item.metadata.get("structured_project_brief") is True
                else item.content
            ),
            created_at=item.created_at,
            run_id=(
                str(item.metadata["run_id"])
                if isinstance(item.metadata.get("run_id"), str)
                else request_run_ids.get(item.id)
            ),
            run_state=run_states.get(str(item.metadata["run_id"])
                if isinstance(item.metadata.get("run_id"), str) else request_run_ids.get(item.id)),
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
        goal_completion=_conversation_completion_v1(
            detail,
            project_plan_authority_digests=project_plan_authority_digests,
            engineering_mission=engineering_mission,
            native_completion_current_state=native_completion_current_state,
            native_completion_saved_sha256=native_completion_saved_sha256,
        ),
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


def _latest_historical_transcript_for_run(
    store: HostStore, *, conversation_id: str,
    workspace_id: str | None, revision_id: str | None,
    checkpoint_updated_at: datetime | None,
) -> dict[str, Any] | None:
    """Select newer historical messages independently of mission authority."""
    transcript = store.get_latest_engineering_transcript(
        conversation_id, workspace_id=workspace_id, revision_id=revision_id,
    )
    # Equal timestamps retain the selected mission checkpoint's history.
    if transcript is None or (
        checkpoint_updated_at is not None and transcript.updated_at <= checkpoint_updated_at
    ):
        return None
    source_run = store.get_run(transcript.source_run_id)
    if (source_run is None or source_run.conversation_id != conversation_id
            or source_run.request_message_id != transcript.request_message_id):
        raise AgentRunPreconditionError("Agent transcript source run mismatched")
    checkpoint, _, _, _ = validated_transcript_checkpoint(transcript.checkpoint, transcript.digest)
    return checkpoint


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
    legacy_completion_kind: str,
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
    previous_completion_kind = previous.get("completionKind", legacy_completion_kind)
    if current.get("completionKind") != previous_completion_kind:
        raise ValueError("engineering mission completionKind changed autonomously")
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
        reasoning_effort=value.reasoning_effort,
        client_input_limit_tokens=value.client_input_limit_tokens,
        has_api_key=bool(value.secrets.api_key),
        external_endpoint=is_external_model_endpoint(value.base_url),
        updated_at=value.updated_at,
        model_context=value.model_context,
    )


def _local_workspace_v1(value: Any) -> LocalWorkspaceV1:
    if not isinstance(value, dict):
        raise TypeError("workspace result must be an object")
    fields = {name: value[name] for name in LocalWorkspaceV1.model_fields if name in value}
    return LocalWorkspaceV1.model_validate(fields)


def _local_workspace_display_name(value: Any, project_path: str | None) -> str:
    """Display the retained source name independently of the IDE working basename."""
    source = value.get("source") if isinstance(value, dict) else None
    display_name = source.get("display_name") if isinstance(source, dict) else None
    if isinstance(display_name, str) and display_name:
        return display_name
    original_path = source.get("original_path") if isinstance(source, dict) else None
    if isinstance(original_path, str) and original_path.strip():
        original_name = Path(original_path).name
        if original_name:
            return original_name
    return Path(project_path).name if project_path else "XG5000 프로젝트"


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


def _workspace_resume_stable_identity(value: LocalWorkspaceV1) -> dict[str, str]:
    """Match a retained workspace without freezing its mutable saved version."""
    identity = _workspace_resume_identity(value)
    identity.pop("working_current_sha256")
    return identity


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
) -> tuple[ChangeRecoveryTarget, str, dict[str, Any]]:
    """Open one exact working copy after its journaled XG5000 target has died.

    This intentionally does not attach or bind the controlled launch.  The
    recovery executor remains the only consumer of the resulting target.
    """

    target = transaction.runtime_target
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
    ):
        raise ChangeExecutionPreconditionError(
            "active recovery workspace is not the transaction's verified working copy"
        )
    try:
        disk_working_hash = _sha256_file(Path(workspace.working_copy.path))
    except OSError as exc:
        raise ChangeExecutionPreconditionError(
            "active recovery working-copy file is unavailable"
        ) from exc
    recovery_launch_arguments: dict[str, Any] = {}
    availability_pids = {requested_pid}
    if hmac.compare_digest(disk_working_hash, target.artifact_hash.casefold()):
        # Preserve the existing current-baseline relaunch behavior.
        recovery_working_hash = disk_working_hash
    else:
        raise ChangeExecutionPreconditionError(
            "active recovery working copy differs from the transaction baseline"
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
    if any(
        instance.get("pid") in availability_pids
        for instance in instances
    ):
        raise ChangeExecutionPreconditionError(
            "the journaled XG5000 target is still available for recovery"
        )

    try:
        launch = await mcp.call_workspace_tool(
            "xg5000.open_project",
            {
                "workspace_id": workspace.workspace_id,
                "expected_working_hash": recovery_working_hash,
                **recovery_launch_arguments,
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
            and all(isinstance(item, dict) for item in observed_instances)
            and sum(
                item.get("pid") == pid and item.get("hwnd") == hwnd
                for item in observed_instances
            ) == 1
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
            == recovery_working_hash
            and launch.get("window_detected") is True
            and isinstance(launch.get("process_create_time"), (int, float))
            and not isinstance(launch.get("process_create_time"), bool)
            and math.isfinite(float(launch["process_create_time"]))
            and float(launch["process_create_time"]) > 0
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
    try:
        receipt_digest = _canonical_json_digest(launch)
    except (TypeError, ValueError) as exc:
        raise ChangeExecutionPreconditionError(
            "recovery-only XG5000 relaunch receipt is not durable JSON"
        ) from exc
    relaunch_evidence = {
        "host_transaction_id": transaction.id,
        "workspace_id": workspace.workspace_id,
        "revision_id": workspace.revision_id,
        "project_path": str(workspace.working_copy.path),
        "project_hash": recovery_working_hash,
        "pid": pid,
        "hwnd": hwnd,
        "process_create_time": float(launch["process_create_time"]),
        "receipt_digest": receipt_digest,
        "launch_receipt": launch,
    }
    return ChangeRecoveryTarget(pid=pid, hwnd=hwnd), recovery_working_hash, relaunch_evidence






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
            
            "baselineldsha256",
            "baselineldcanonicalsha256",
        }
        if (
            key.startswith("_")
            or normalized
            in {
                "clipboard",
                "clipboardpayload",
                
                "ldpayload",
                "privatepayload",
                "privatematerial",
            }
            or normalized.endswith("payload")
            or (not baseline_digest_field and normalized.startswith(
                (
                    
                    "rawld",
                    
                    "baselineld",
                    
                    "privateld",
                )
            ))
        ):
            raise ValueError(f"change plan contains forbidden private field: {key}")
        _reject_private_change_plan_material(item)




def _parse_change_plan(value: Any) -> ChangePlanContract:
    if isinstance(value, ChangePlanV1):
        _reject_private_change_plan_material(value.model_dump(mode="json"))
        return value
    if not isinstance(value, dict):
        raise TypeError("change plan must be an object")
    _reject_private_change_plan_material(value)
    plan_kind = value.get("planKind", value.get("plan_kind"))
    if plan_kind is None:
        return ChangePlanV1.model_validate(value)
    raise ValueError(f"unsupported change plan kind: {plan_kind}")


def _native_preparation_failure_detail(evidence: Any) -> str:
    preparation = evidence.get("preparation") if isinstance(evidence, Mapping) else None
    stage = preparation.get("stage") if isinstance(preparation, Mapping) else None
    code = preparation.get("mcp_error_code") if isinstance(preparation, Mapping) else None
    if (
        isinstance(stage, str)
        and stage in {"snapshot", "backup", "plan"}
        and isinstance(code, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", code)
    ):
        return f"Native change stopped before application at {stage} ({code})."
    return "Native change stopped before a verified application."


def _public_change_error_detail(value: ChangeTransaction) -> str | None:
    """Expose only Host-authored failure categories, including on old rows."""

    error_code = value.error_code
    if error_code is None:
        return None
    if error_code in {"explicit_recovery_rolled_back", }:
        return None
    if error_code == "change_preflight_failed_before_mutation":
        return "Native preflight failed before execution; no mutation was attempted."
    if error_code == "native_change_not_applied":
        return _native_preparation_failure_detail(value.result.get("verification"))
    if error_code in {
        "native_change_outcome_uncertain",
        "unexpected_apply_failure",
        "verification_outcome_uncertain",
    }:
        return (
            "The native outcome is uncertain; inspect the durable recovery "
            "state before any retry."
        )
    if "rolled_back" in error_code or "rollback" in error_code:
        return "Verification did not pass; consult the durable rollback receipt."
    return "The change transaction did not complete successfully."


def _public_change_result_projection(value: Any) -> Any:
    """Suppress legacy free-text diagnostics without changing durable receipts."""

    if isinstance(value, Mapping):
        projected: dict[Any, Any] = {}
        for key, item in value.items():
            if (
                isinstance(key, str)
                and (key.casefold() == "error" or key.casefold().endswith("_error"))
                and isinstance(item, str)
            ):
                projected[key] = "private diagnostic suppressed"
            else:
                projected[key] = _public_change_result_projection(item)
        return projected
    if isinstance(value, list):
        return [_public_change_result_projection(item) for item in value]
    if isinstance(value, tuple):
        return [_public_change_result_projection(item) for item in value]
    return value


def _change_transaction_v1(value: ChangeTransaction) -> ChangeTransactionV1:
    approval = value.approval
    return ChangeTransactionV1(
        transaction_id=value.id,
        operation_id=value.operation_id,
        conversation_id=value.conversation_id,
        run_id=value.run_id,
        state=value.state.value.casefold(),
        target_identity=ChangeTargetIdentityV1.model_validate(value.target.model_dump()),
        execution_target_identity=(
            ChangeTargetIdentityV1.model_validate(value.execution_target.model_dump())
            if value.execution_target is not None else None
        ),
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
        result=_public_change_result_projection(value.result),
        error_code=value.error_code,
        error_detail=_public_change_error_detail(value),
        created_at=value.created_at,
        updated_at=value.updated_at,
        execution_started_at=value.execution_started_at,
        verification_started_at=value.verification_started_at,
        completed_at=value.completed_at,
        receipt_acknowledged_at=value.receipt_acknowledged_at,
        receipt_acknowledged_by=value.receipt_acknowledged_by,
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
    same_endpoint = existing is not None and existing.base_url == base_url
    reasoning_effort = validate_reasoning_effort(
        existing.reasoning_effort
        if "reasoning_effort" not in body.model_fields_set
        and same_endpoint
        and existing is not None
        else body.reasoning_effort
    )
    if is_external_model_endpoint(base_url) and not body.external_model_consent:
        raise ModelSettingsError(
            "external_model_consent is required before sending data to this endpoint"
        )
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
        reasoning_effort=reasoning_effort,
        client_input_limit_tokens=(existing.client_input_limit_tokens
            if 'client_input_limit_tokens' not in body.model_fields_set
            and existing is not None
            else body.client_input_limit_tokens),
        updated_at=datetime.now(timezone.utc),
        capability_verified_at=None,
        secrets=ModelSecrets(api_key=api_key, custom_headers=headers),
    )


def _existing_model_settings_for_write(
    repository: ModelConnectionRepository,
    body: SaveModelSettingsRequestV1,
) -> StoredModelConnection | None:
    try:
        return repository.get()
    except StoredModelSettingsError:
        # A broken legacy credential must not trap the user in an unrecoverable
        # GET-before-PUT cycle. Both secret fields must be explicitly supplied;
        # otherwise their omission could silently erase valid user data.
        if {"api_key", "custom_headers"} <= body.model_fields_set:
            return None
        raise


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
        value.reasoning_effort,
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


def _native_ld_edit_ready(session: dict[str, Any] | None, workspace: dict[str, Any] | None) -> bool:
    """Native working-copy binding is not the legacy COM transaction capability."""
    if _host_native_binding_ready(session, workspace):
        return True
    if not _engineering_session_read_ready(session) or not isinstance(workspace, dict):
        return False
    assert isinstance(session, dict)
    working = workspace.get("working_copy")
    metadata = session.get("metadata", {})
    if (not isinstance(working, dict) or workspace.get("source_kind") not in {"local_file", "new_build", "restored_from_controller"}
            or not all(workspace.get(key) for key in ("workspace_id", "revision_id"))
            or not isinstance(session.get("session_id"), str) or not session["session_id"]
            or any(type(session.get(key)) is not int or session[key] <= 0 for key in ("pid", "hwnd"))
            or any(not isinstance(value, str) or not value for value in (working.get("path"), session.get("project_path")))
            or metadata.get("project_identity_assurance") not in {"strong_readback", "controlled_launch_read_only"}
            or session.get("project_hash") != working.get("current_sha256")):
        return False
    expected_hash = session.get("project_hash")
    saved = metadata.get("native_ld_saved_observation")
    if (isinstance(saved, dict) and all(saved.get(key) == session.get(key)
            for key in ("session_id", "pid", "hwnd", "project_path"))
            and saved.get("current_project_hash") == expected_hash
            and isinstance(saved.get("disk_receipt"), dict)
            and saved["disk_receipt"].get("sha256") == expected_hash
            and saved["disk_receipt"].get("stable_during_read") is True):
        expected_hash = saved.get("anchor_project_hash")
    proof = next((metadata[key] for key in ("project_identity_proof", "controlled_open_read_proof", "offline_candidate_mutation_binding")
                  if isinstance(metadata.get(key), dict)), None)
    if (not isinstance(proof, dict) or proof.get("pid") != session.get("pid")
            or proof.get("main_hwnd") != session.get("hwnd") or proof.get("project_hash") != expected_hash
            or not isinstance(proof.get("process_create_time"), (int, float))
            or isinstance(proof["process_create_time"], bool)
            or not math.isfinite(float(proof["process_create_time"])) or proof["process_create_time"] <= 0):
        return False
    try:
        paths = [os.path.normcase(str(Path(str(value)).resolve())) for value in (
            working.get("path"), session.get("project_path"), proof.get("project_path"))]
    except (OSError, RuntimeError, ValueError):
        return False
    return all(paths[0] == path for path in paths[1:])


def _host_native_binding_ready(session: dict[str, Any] | None, workspace: dict[str, Any] | None) -> bool:
    """A retained anchor authorizes only a freshly observed, same-live native target."""
    if not isinstance(session, dict) or not isinstance(workspace, dict):
        return False
    metadata = session.get("metadata")
    evidence = metadata.get("native_ld_host_revalidation") if isinstance(metadata, dict) else None
    binding = evidence.get("binding") if isinstance(evidence, dict) else None
    anchor = binding.get("session") if isinstance(binding, dict) else None
    observed = evidence.get("observed") if isinstance(evidence, dict) else None
    if (not isinstance(anchor, dict) or not isinstance(observed, dict)
            or not isinstance(anchor.get("metadata"), dict)
            or "native_ld_host_revalidation" in anchor["metadata"]
            or not _native_ld_edit_ready(anchor, workspace)
            or any(binding.get(key) != workspace.get(key) for key in ("workspace_id", "revision_id"))
            or any(session.get(key) != anchor.get(key) for key in ("pid", "hwnd", "project_path", "project_hash"))
            or any(observed.get(key) != session.get(key) for key in ("pid", "hwnd", "project_path", "project_hash"))
            or observed.get("main_hwnd") != session.get("hwnd")
            or observed.get("method") != "same_live_native_observe"):
        return False
    created = _engineering_session_process_create_time(anchor)
    actual_created = observed.get("process_create_time")
    return (created is not None and type(actual_created) in {int, float}
            and math.isfinite(actual_created) and abs(actual_created - created) < 0.001)


def _engineering_session_process_create_time(session: dict[str, Any] | None) -> float | None:
    """Return only an exact existing session lifetime proof, never an estimate."""

    if not isinstance(session, dict):
        return None
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return None
    host_evidence = metadata.get("native_ld_host_revalidation")
    if isinstance(host_evidence, dict):
        binding = host_evidence.get("binding")
        anchor = binding.get("session") if isinstance(binding, dict) else None
        if (isinstance(anchor, dict) and isinstance(anchor.get("metadata"), dict)
                and "native_ld_host_revalidation" not in anchor["metadata"]):
            return _engineering_session_process_create_time(anchor)
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








def _quarantined_diagnostic_context(
    *,
    conversation: Conversation,
    mission: EngineeringMission | None,
    transaction: ChangeTransaction | None,
    session: dict[str, Any] | None,
    workspace: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Permit observation of one retained uncertain change, never a new mutation."""
    if mission is None or transaction is None or transaction.state is not ChangeTransactionState.QUARANTINED:
        return None
    target = transaction.runtime_target
    _canonical, digest = _canonical_engineering_mission(mission.engineering_mission)
    normal_session_bound = (
        _engineering_session_read_ready(session)
        and _engineering_session_exact_lifetime_binding(session)
    )
    if (
        mission.conversation_id != conversation.id
        or transaction.conversation_id != conversation.id
        or mission.change_transaction_id != transaction.id
        or digest != mission.digest
        or mission.workspace_id != conversation.workspace_id
        or mission.revision_id != conversation.revision_id
        or target.workspace_id != mission.workspace_id
        or target.revision_id != mission.revision_id
        or not normal_session_bound
        or not isinstance(session, dict)
        or (
            (
                session.get("session_id") != target.engineering_session_id
                or session.get("pid") != target.engineering_process_id
                or session.get("hwnd") != target.engineering_window_handle
                or session.get("project_hash") != target.artifact_hash
                or not _same_path(str(session.get("project_path") or ""), target.project_path)
            )
        )
        or not isinstance(workspace, dict)
        or workspace.get("workspace_id") != target.workspace_id
        or workspace.get("revision_id") != target.revision_id
        or (workspace.get("working_copy") or {}).get("current_sha256") != target.artifact_hash
        or not _same_path(str((workspace.get("working_copy") or {}).get("path") or ""), target.project_path)
        or (workspace.get("source") or {}).get("sha256") != target.source_artifact_hash
    ):
        raise AgentRunPreconditionError("quarantined diagnostic target is no longer exact")
    verification = transaction.result.get("verification")
    rejection = verification.get("native_rejection") if isinstance(verification, dict) else None
    native_code = rejection.get("code") if isinstance(rejection, dict) else None
    code = native_code if isinstance(native_code, str) else transaction.error_code or "outcome_uncertain"
    return {
        "state": "QUARANTINED",
        "code": code if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", code) else "outcome_uncertain",
    }


async def _healthy_current_recovery(executor, state, anchor, workspace, generation, revalidate, current_generation):
    """Fresh current proof; a successful result does not settle prior outcomes."""
    if (generation is None or not executor.allow_simulator or executor.recovery_only
            or executor.recovery_pending or executor.operation_recoveries):
        return None
    if state is not None:
        previous = state.get('new_binding') if isinstance(state, dict) and state.get('phase') == 'rebound' else None
        if (not isinstance(previous, dict) or any(previous.get(key) != anchor.get(key) for key in
                ('workspace_id', 'revision_id', 'pid', 'hwnd', 'project_path', 'process_create_time'))):
            return None
    try:
        session = await revalidate(workspace)
        if (not isinstance(session, dict)
                or any(session.get(k) != anchor[k] for k in ('pid', 'hwnd', 'project_path'))
                or _engineering_session_process_create_time(session) != anchor['process_create_time']
                or session.get('project_hash') != workspace.working_copy.current_sha256
                or current_generation(executor) != generation):
            return None
        observed = await executor._execute('simulator', {'action': 'observe'})
        constraints = observed.get('current_constraints') if isinstance(observed, dict) else None
        saved = observed.get('saved_project') if isinstance(observed, dict) else None
        navigation = observed.get('navigation_input') if isinstance(observed, dict) else None
        inactive = observed.get('current_session_observation') if isinstance(observed, dict) else None
        continuation = observed.get('current_operation_continuation') if isinstance(observed, dict) else None
        context = getattr(executor, '_diagnostic_call_context', None)
        continuable = False
        if isinstance(continuation, dict) and isinstance(constraints, dict) and isinstance(saved, dict):
            from controlstudio_contracts.native_operation_continuation import (
                operation_constraints_digest,
                validate_current_operation_continuation,
            )
            try:
                proof = validate_current_operation_continuation(continuation)
                continuable = (proof['source_tool_name'] == 'simulator'
                    and isinstance(context, dict) and proof['source_call_id'] == context.get('call_id')
                    and proof['host_request_id'] == context.get('request_id')
                    and proof['admitted_generation'] == generation and proof['saved_sha256'] == saved.get('sha256')
                    and proof['constraints_schema'] == constraints.get('schema')
                    and proof['constraints_sha256'] == operation_constraints_digest(constraints)
                    and proof['route_tool_name'] == 'simulator')
            except (ValueError, KeyError, TypeError):
                pass
        reconciled = (isinstance(observed, dict) and observed.get('read_only') is False
            and observed.get('action_attempted') is True and observed.get('simulator_control_action_attempted') is False
            and observed.get('input_state_unknown') is False and observed.get('load_state') == 'ended'
            and observed.get('end_reconciled_without_simulator_control') is True
            and observed.get('current_session_inactive_verified') is True
            and isinstance(inactive, dict) and inactive.get('current_session_inactive_verified') is True
            and isinstance(navigation, dict) and navigation.get('attempted') is True
            and navigation.get('outcome') == 'opened' and navigation.get('close_outcome') in {'closed', 'already_closed'}
            and navigation.get('control_action_attempted') is False
            and isinstance(constraints, dict) and constraints.get('journal_state') == 'ended')
        if (not isinstance(observed, dict) or observed.get('ok') is not True
                or observed.get('action') != 'observe'
                or (not reconciled and (observed.get('read_only') is False or observed.get('action_attempted') is True))
                or observed.get('input_state_unknown') is True
                or observed.get('error') or observed.get('failure')
                or not isinstance(constraints, dict) or constraints.get('schema') != 'xg5000-simulator-current-constraints/v1'
                or constraints.get('evidence_scope') != 'current_binding_journal_snapshot'
                or (not continuable and (constraints.get('start_behavior') != 'fresh_native_preflight'
                    or constraints.get('start_journal_blocker') is not None
                    or constraints.get('journal_state') not in {'not_loaded', 'different_binding', 'loaded', 'ended'}))
                or not isinstance(saved, dict) or saved.get('stable_during_read') is not True
                or saved.get('sha256') != workspace.working_copy.current_sha256
                or current_generation(executor) != generation):
            return None
        return {'ok': True, 'rebound': False, 'relaunch_not_required': True,
                'current_binding_verified': True, 'current_request_input_attempted': reconciled,
                'simulator_observation': observed, 'automatic_replay': False}
    except (MCPToolError, HTTPException, OSError, ValueError, TypeError, KeyError):
        return None


def _pending_recovery_conversation_target(stored, current, anchor, *, file_only):
    """Use a validated recovery owner solely to retain the conversation identity."""
    return bool(isinstance(anchor, dict) and stored is not None
                and stored == tuple(anchor.get(key) for key in ('workspace_id', 'revision_id', 'session_id'))
                and (current is None or file_only))


def _native_workspace_recovery_state_key(conversation_id: str, workspace_id: str, revision_id: str) -> str:
    """Bounded key for the exact ordered identity tuple, without changing it."""
    identity = (conversation_id, workspace_id, revision_id)
    if any(not isinstance(value, str) or not value for value in identity):
        raise ValueError("recovery state identity must contain three nonempty strings")
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "native_workspace_recovery:v1:" + hashlib.sha256(encoded).hexdigest()


def _engineering_session_v1(session: dict[str, Any]) -> EngineeringSessionV1:
    """Project the public DTO without discarding the internal MCP session."""
    return EngineeringSessionV1.model_validate({
        name: session[name] for name in EngineeringSessionV1.model_fields if name in session
    })


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


async def _find_existing_workspace_session(
    mcp: MCPGateway,
    workspace: LocalWorkspaceV1,
    *,
    on_unverified: Callable[[int, int, Any], None] | None = None,
) -> dict[str, Any] | None:
    """Reuse a uniquely proven live file, never a matching window title alone."""

    observed = await mcp.call_read_tool("xg5000.instances", {})
    instances = observed.get("instances") if isinstance(observed, dict) else None
    if not isinstance(instances, list) or len(instances) > 8:
        raise HTTPException(
            status_code=409,
            detail="열려 있는 프로젝트를 자동으로 선택하지 못했습니다. 연결할 창을 선택해 주세요.",
        )
    matches: list[dict[str, Any]] = []
    unverified_candidate = False
    seen: set[tuple[int, int]] = set()
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        pid, hwnd = instance.get("pid"), instance.get("hwnd")
        if (
            isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
            or isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0
            or (pid, hwnd) in seen
        ):
            continue
        seen.add((pid, hwnd))
        try:
            result = await mcp.call_read_tool(
                "xg5000.attach",
                {
                    "pid": pid,
                    "hwnd": hwnd,
                    "access_profile": "analysis",
                    "workspace_id": workspace.workspace_id,
                    "project_path": workspace.working_copy.path,
                    "project_hash": workspace.working_copy.current_sha256,
                },
            )
        except MCPToolError:
            unverified_candidate = True
            continue
        validated = _validated_engineering_session_result(
            result, pid=pid, hwnd=hwnd,
            project_path=workspace.working_copy.path,
            project_hash=workspace.working_copy.current_sha256,
        )
        if validated is not None:
            matches.append(validated)
        elif on_unverified is not None:
            on_unverified(pid, hwnd, result)
            raw_session = result.get("session", result) if isinstance(result, dict) else {}
            diagnostic = _safe_identity_diagnostic(raw_session if isinstance(raw_session, dict) else {})
            if diagnostic.get("native_file_code") != "WINDOW_TITLE_MISMATCH":
                unverified_candidate = True
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="같은 작업 사본을 연 창이 여러 개입니다. 이어서 작업할 창을 선택해 주세요.",
        )
    if not matches and unverified_candidate:
        raise HTTPException(status_code=409, detail="열려 있는 프로젝트를 재검증하지 못했습니다. 기존 창을 보존했습니다.")
    return matches[0] if matches else None


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
    validated = _validated_engineering_session_result(
        status_result,
        session_id=session_id,
        pid=pid,
        hwnd=hwnd,
        project_path=project_path,
        project_hash=project_hash,
    )
    if validated is None:
        return None
    # session_status describes retained state. Only a fresh inventory can
    # establish that this exact process/window/lifetime is still present.
    observed = await mcp.call_read_tool("xg5000.instances", {})
    instances = observed.get("instances") if isinstance(observed, dict) else None
    if not isinstance(instances, list) or any(
        not isinstance(item, dict) or any(type(item.get(key)) is not int or item[key] <= 0
                                         for key in ("pid", "hwnd"))
        for item in instances
    ):
        raise MCPToolError("Retained session instances were not observed")
    candidates = [item for item in instances if item.get("pid") == pid or item.get("hwnd") == hwnd]
    if not candidates:
        return None
    if (len(candidates) != 1 or candidates[0].get("pid") != pid
            or candidates[0].get("hwnd") != hwnd or candidates[0].get("visible") is not True):
        raise MCPToolError("The retained session target was not exactly observed")
    created = _engineering_session_process_create_time(validated)
    lifetime = candidates[0].get("process_create_time")
    if (created is None or type(lifetime) not in {int, float}
            or not math.isfinite(lifetime) or abs(lifetime - created) >= 0.001):
        raise MCPToolError("The retained session process lifetime changed")
    return validated


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


def _planning_requirement_message_sources(
    messages: Sequence[Message],
    *,
    retained_message_ids: set[str],
    narrative_adoption_boundary_message_id: str | None = None,
) -> list[dict[str, str]]:
    """Keep explicit provenance plus user messages after narrative adoption."""

    reviewed = next(
        (
            item
            for item in reversed(messages)
            if item.project_plan_review is not None
            and isinstance(item.metadata.get("plan_artifact"), dict)
        ),
        None,
    )
    source_ids = set(retained_message_ids or ())
    if reviewed is not None:
        source_ids.update(
            source.get("messageId")
            for source in reviewed.metadata["plan_artifact"].get(
                "requirementSources", []
            )
            if isinstance(source, dict)
            and isinstance(source.get("messageId"), str)
        )
    reviewed_index = next(
        (
            index
            for index, item in enumerate(messages)
            if reviewed is not None and item.id == reviewed.id
        ),
        -1,
    )
    adoption_index = next(
        (
            index
            for index, item in enumerate(messages)
            if narrative_adoption_boundary_message_id is not None
            and item.id == narrative_adoption_boundary_message_id
            and item.role is MessageRole.USER
        ),
        -1,
    )
    boundary_index = adoption_index if adoption_index >= 0 else reviewed_index
    adopted_requirement_contents = {
        item.content
        for index, item in enumerate(messages)
        if adoption_index >= 0
        and index <= adoption_index
        and item.role is MessageRole.USER
        and not bool(item.metadata.get("structured_project_brief"))
    }
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].role is MessageRole.USER
            and not bool(messages[index].metadata.get("structured_project_brief"))
        ),
        -1,
    )
    return [
        {"message_id": item.id, "content": item.content}
        for index, item in enumerate(messages)
        if item.role is MessageRole.USER
        and not bool(item.metadata.get("structured_project_brief"))
        and (
            item.id in source_ids
            or index == latest_user_index
            or index > boundary_index
            and not (
                adoption_index >= 0
                and item.content in adopted_requirement_contents
            )
        )
    ]






async def _reviewed_change_close_checkpoint(
    mcp: MCPGateway, transaction: ChangeTransaction | None,
) -> dict[str, Any] | None:
    if transaction is None:
        return None
    try:
        checkpoint, terminal, _symbol_evidence = _transaction_lookup_checkpoint(
            await mcp.call_recovery_tool(
                "change.transaction_lookup", {"host_transaction_id": transaction.id},
            ),
            host_transaction_id=transaction.id,
        )
    except (MCPToolError, ChangeExecutionPreconditionError) as exc:
        raise HTTPException(status_code=409, detail="현재 변경 거래의 복구 대상을 조회하지 못했습니다.") from exc
    return checkpoint if terminal is None else None




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
    expected_frame: dict[str, Any] | None = None,
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
            if expected_frame is not None:
                actual_created=instance.get('process_create_time')
                expected_created=expected_frame.get('process_create_time')
                if (pid!=expected_frame.get('pid') or hwnd!=expected_frame.get('hwnd')
                        or type(actual_created) not in {int,float} or not math.isfinite(actual_created)
                        or type(expected_created) not in {int,float} or not math.isfinite(expected_created)
                        or expected_created<=0 or abs(actual_created-expected_created)>=0.001):
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
            if expected_frame is not None:
                created=_engineering_session_process_create_time(raw_session)
                if created is None or abs(created-expected_frame['process_create_time'])>=0.001:
                    return None
            session = bind_session(attached)
            mutation_identity_verified = _engineering_session_identity_verified(session)
            return (
                XG5000ProjectLaunchV1(
                    operation="xg5000.open_project",
                    # A late attach is not itself a launch receipt.  Preserve
                    # read-only late reconciliation, but never label a plain
                    # native-file observation as a verified fresh open.
                    verified=mutation_identity_verified,
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
                        "exact workspace path, hash, PID, and HWND readback; "
                        + (
                            "the existing mutation identity was also verified"
                            if mutation_identity_verified
                            else "no fresh controlled-launch mutation proof was available"
                        )
                    ),
                ),
                session,
            )
        await sleep(min(0.25, max(0.0, deadline - monotonic())))
    return None












async def _rebind_recorded_change_session(
    mcp: MCPGateway,
    transaction: ChangeTransaction,
    workspace: dict[str, Any],
    *,
    expected_project_path: Path,
    expected_project_hash: str,
) -> dict[str, Any] | None:
    """Reconnect the recorded editor through a fresh read-only identity check."""
    workspace_id = workspace.get("workspace_id")
    session_id = transaction.runtime_target.engineering_session_id
    pid = transaction.runtime_target.engineering_process_id
    hwnd = transaction.runtime_target.engineering_window_handle
    if (
        not isinstance(workspace_id, str)
        or not workspace_id
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(hwnd, int)
        or isinstance(hwnd, bool)
        or hwnd <= 0
    ):
        return None

    retained = await _revalidate_exact_engineering_session(
        mcp,
        session_id=session_id,
        pid=pid,
        hwnd=hwnd,
        project_path=str(expected_project_path),
        project_hash=expected_project_hash,
    )
    if retained is not None:
        return retained

    attached = await mcp.call_read_tool(
        "xg5000.attach",
        {
            "pid": pid,
            "hwnd": hwnd,
            "access_profile": "analysis",
            "project_path": str(expected_project_path),
            "project_hash": expected_project_hash,
            "workspace_id": workspace_id,
        },
    )
    return _validated_engineering_session_result(
        attached,
        pid=pid,
        hwnd=hwnd,
        project_path=str(expected_project_path),
        project_hash=expected_project_hash,
    )


def _safe_identity_diagnostic(session: dict[str, Any]) -> dict[str, str]:
    metadata = session.get("metadata")
    diagnostic = metadata.get("strong_identity_diagnostic") if isinstance(metadata, dict) else None
    if not isinstance(diagnostic, dict):
        return {}
    return {key: value for key, value in diagnostic.items()
            if key in {"stage", "code", "native_file_stage", "native_file_code"}
            and isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_]{1,80}", value)}


def _agent_runtime_user_message(diagnostic: AgentRuntimeDiagnostic) -> str:
    """Return the bounded user-facing message for a sidecar runtime diagnostic."""

    if diagnostic.exception_kind == "working_memory_validation":
        return "이번 실행을 완료하지 못했습니다. 대화와 실행 기록에서 현재 작업 상태를 확인해 주세요."
    if diagnostic.reason_code == "tool_call_budget":
        return "도구 호출 한도에 도달해 실행이 종료됐습니다."
    if diagnostic.reason_code.startswith("provider_http_"):
        return (
            f"모델 서버가 HTTP {diagnostic.reason_code.removeprefix('provider_http_')} "
            "오류를 반환해 실행을 완료하지 못했습니다."
        )
    if diagnostic.reason_code == "provider_timeout":
        return (
            "모델 응답을 기다리는 시간이 초과되어 이번 요청을 완료하지 못했습니다. "
            "입력한 내용은 대화에 남아 있습니다."
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
    # Development defaults bootstrap an empty installation. A user-selected
    # endpoint and its verification receipt remain authoritative on restart.
    if (
        development_model_custom_headers is not None
        and model_settings_readable
        and configured_model is None
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

    change_executor = change_executor or MCPChangeExecutionService(
        mcp,
        checkpoint_callback=store.record_native_change_checkpoint,
    )
    run_tasks: dict[str, asyncio.Task[None]] = {}
    active_change_executions: set[str] = set()
    # A single explicit native-test authority may dispatch at most one resume
    # attempt during its model run.  The entry is cleared with the run task;
    # a new user message is the only way to request a deliberate retest.
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
    # Restart restores evidence, not a ready session or an MCP capability.
    # The legacy single value is only the selected view. Retain each workspace's
    # independently proven lineage before another workspace replaces that view.
    native_workspace_binding = store.get_host_state("native_workspace_binding")
    native_workspace_bindings = store.get_host_state("native_workspace_bindings:v1")

    def native_workspace_binding_key(workspace: dict[str, Any] | None) -> str | None:
        if (not isinstance(workspace, dict)
                or any(not isinstance(workspace.get(key), str) or not workspace[key]
                       for key in ("workspace_id", "revision_id"))):
            return None
        return _canonical_json_digest({key: workspace[key] for key in ("workspace_id", "revision_id")})

    def retained_native_workspace_binding(workspace: dict[str, Any] | None) -> dict[str, Any] | None:
        key = native_workspace_binding_key(workspace)
        retained = native_workspace_bindings.get(key) if isinstance(native_workspace_bindings, dict) else None
        if (not isinstance(retained, dict) or not isinstance(workspace, dict)
                or any(retained.get(field) != workspace.get(field) for field in ("workspace_id", "revision_id"))
                or not isinstance(retained.get("session"), dict)):
            return None
        # Selection restores evidence only; each existing caller still checks
        # current workspace/hash, exact lifetime and fresh native observation.
        return deepcopy(retained)

    if native_workspace_bindings is None:
        native_workspace_bindings = {}
        # One-time migration accepts only the old value's own current workspace
        # and original proof. A three-field checkpoint or another workspace's
        # session cannot manufacture this lineage. An existing map, even empty,
        # is authoritative, so a retired legacy view cannot be resurrected.
        legacy = native_workspace_binding
        key = native_workspace_binding_key(active_workspace)
        if (key is not None and isinstance(legacy, dict)
                and all(legacy.get(field) == active_workspace.get(field)
                        for field in ("workspace_id", "revision_id"))
                and _native_ld_edit_ready(legacy.get("session"), active_workspace)):
            native_workspace_bindings[key] = deepcopy(legacy)
        store.set_host_state("native_workspace_bindings:v1", native_workspace_bindings)
    elif not isinstance(native_workspace_bindings, dict):
        raise ValueError("Persisted native workspace bindings are invalid")
    native_workspace_binding = retained_native_workspace_binding(active_workspace)
    store.set_host_state("native_workspace_binding", native_workspace_binding)

    def update_native_workspace_binding(
        session: dict[str, Any] | None = None,
        *,
        workspace: dict[str, Any] | None = None,
        closed_target: tuple[int, int] | None = None,
        closed_process_create_time: float | None = None,
    ) -> None:
        nonlocal native_workspace_binding, native_workspace_bindings
        selected = workspace if workspace is not None else active_workspace
        updated = deepcopy(native_workspace_bindings)
        if closed_target is not None:
            # Retirement is about the exact original frame/lifetime, never the
            # currently selected workspace or a recycled PID/HWND generation.
            if (type(closed_process_create_time) not in {int, float}
                    or not math.isfinite(closed_process_create_time) or closed_process_create_time <= 0):
                raise ValueError("Native binding retirement requires its original process lifetime")
            for key, retained in tuple(updated.items()):
                anchor = retained.get("session") if isinstance(retained, dict) else None
                created = _engineering_session_process_create_time(anchor)
                if (isinstance(anchor, dict) and closed_target == (anchor.get("pid"), anchor.get("hwnd"))
                        and created is not None and abs(created - closed_process_create_time) < 0.001):
                    del updated[key]
        if session is not None:
            key = native_workspace_binding_key(selected)
            if _host_native_binding_ready(session, selected):
                updated[key] = deepcopy(session["metadata"]["native_ld_host_revalidation"]["binding"])
            elif _native_ld_edit_ready(session, selected):
                updated[key] = {
                    "workspace_id": selected["workspace_id"],
                    "revision_id": selected["revision_id"],
                    "session": deepcopy(session),
                }
            # An unsuccessful attach is not evidence that an earlier native
            # lineage retired. Retain it for the unchanged fresh revalidation.
        # Commit the per-workspace authority first. The compatibility view is
        # derived afterwards; interrupted writes cannot revive a retired entry.
        store.set_host_state("native_workspace_bindings:v1", updated)
        native_workspace_bindings = updated
        native_workspace_binding = retained_native_workspace_binding(active_workspace)
        store.set_host_state("native_workspace_binding", native_workspace_binding)

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

    def resolve_project_brief_attachments(
        brief: dict[str, Any] | ProjectBriefV1,
    ) -> list[dict[str, Any]]:
        validated = brief if isinstance(brief, ProjectBriefV1) else ProjectBriefV1.model_validate(brief)
        return resolve_engineering_attachment_ids(validated.attachment_source_ids)

    def resolve_engineering_attachment_ids(source_ids) -> list[dict[str, Any]]:
        attachments = []
        for source_id in source_ids:
            source = knowledge_store.get_source(source_id)
            if source is None or source.kind not in _PROJECT_BRIEF_ATTACHMENT_KINDS or source.status is not KnowledgeStatus.CONFIRMED:
                raise ValueError("project brief attachment is unavailable or unconfirmed")
            attachment = ProjectPlanAttachmentV1.model_validate({
                "source_id": source.source_id, "kind": source.kind.value,
                "title": source.title, "scope": source.scope,
                "vendor": source.vendor, "product_family": source.product_family,
                "version": source.version, "language": source.language,
                "revision": source.revision, "sha256": source.sha256,
                "authority": source.authority,
            })
            attachments.append(attachment.model_dump(mode="json", by_alias=True))
        return attachments

    def resolve_native_engineering_sources(detail, target) -> list[dict[str, Any]]:
        # Explicit attachments keep their existing validation failures.
        attachments = (resolve_project_brief_attachments(detail.project_brief)
                       if detail.project_brief is not None else [])
        workspace_id, revision_id = target.get('workspace_id'), target.get('revision_id')
        if isinstance(workspace_id, str) and workspace_id:
            scoped = knowledge_store.confirmed_sources_for_scope(
                scope='project:' + workspace_id, kinds=_PROJECT_BRIEF_ATTACHMENT_KINDS)
            attachments.extend(resolve_engineering_attachment_ids(item.source_id for item in scoped))
            if isinstance(revision_id, str) and revision_id:
                origin_brief = store.native_project_origin_brief(
                    workspace_id=workspace_id, revision_id=revision_id)
                if origin_brief is not None:
                    validated_origin = ProjectBriefV1.model_validate(origin_brief)
                    inherited_ids = []
                    for source_id in validated_origin.attachment_source_ids:
                        source = knowledge_store.get_source(source_id)
                        if (source is not None and source.kind in _PROJECT_BRIEF_ATTACHMENT_KINDS
                                and source.status is KnowledgeStatus.CONFIRMED):
                            inherited_ids.append(source_id)
                    attachments.extend(resolve_engineering_attachment_ids(inherited_ids))
        by_id = {}
        for item in attachments:
            value = {'source_id': item['sourceId'], 'sha256': item['sha256'], 'revision': item['revision']}
            previous = by_id.get(value['source_id'])
            if previous is not None and previous != value:
                raise ValueError('engineering source changed while resolving attachments')
            by_id[value['source_id']] = value
        return [by_id[key] for key in sorted(by_id)]

    def current_project_plan_authority_digests(conversation_id: str) -> tuple[str, str]:
        conversation = store.get_conversation(conversation_id)
        if conversation is None or conversation.project_brief is None:
            raise ProjectPlanReviewConflict("project brief is unavailable")
        try:
            return (
                calculate_project_brief_digest(conversation.project_brief),
                calculate_project_plan_attachment_revision_digest(resolve_project_brief_attachments(conversation.project_brief)),
            )
        except (TypeError, ValueError) as exc:
            raise ProjectPlanReviewConflict("project brief attachment revisions are unavailable") from exc

    def bind_agent_workspace_target(workspace: LocalWorkspaceV1 | dict[str, Any]) -> None:
        update_native_workspace_binding(workspace=(workspace.model_dump(mode="json")
                                                   if isinstance(workspace, LocalWorkspaceV1) else workspace))
        execution_broker.bind_agent_workspace(workspace_agent_binding(workspace))
    active_ls_controller: dict[str, Any] | None = store.get_host_state("active_ls_controller")












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
        if (_engineering_session_read_ready(session)
                or _native_ld_edit_ready(session, active_workspace)):
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
                return workspace_id, revision_id, _workspace_engineering_conversation_session_id(workspace_id, revision_id)
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





    async def refresh_terminal_change_identity(
        transaction: ChangeTransaction,
        *,
        retain_engineering_session: bool,
    ) -> bool:
        """Refresh Host identity after a saved/reopened LD checkpoint."""

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

        terminal_check_only = bool(
            transaction.plan.get("verification_scope")
            == "offline_native_check_only"
            and transaction.state is ChangeTransactionState.COMPLETED
            and not False
        )
        if terminal_check_only:
            try:
                retained_session = await _rebind_recorded_change_session(
                    mcp,
                    transaction,
                    refreshed_workspace,
                    expected_project_path=expected_path,
                    expected_project_hash=digest,
                )
                if retained_session is None:
                    raise ChangeExecutionPreconditionError(
                        "terminal check-only target could not be revalidated"
                    )
            except (
                MCPToolError,
                ChangeExecutionPreconditionError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                logger.warning(
                    "terminal check-only target could not be rebound "
                    "(transaction=%s, kind=%s)",
                    transaction.id,
                    type(exc).__name__,
                )
                active_engineering_session = None
                bind_agent_workspace_target(active_workspace)
                return False
            active_engineering_session = retained_session
            broker_session = dict(retained_session)
            broker_session.update(workspace_agent_binding(refreshed_workspace))
            execution_broker.bind_agent_session(broker_session)
            return True
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
            retain_engineering_session
            and isinstance(session, dict)
            and (
                session.get("session_id") == transaction.runtime_target.engineering_session_id
            )
            and session.get("pid") == transaction.runtime_target.engineering_process_id
            and session.get("hwnd") == transaction.runtime_target.engineering_window_handle
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

    launch_wait_request_context = contextvars.ContextVar("owned_launch_request", default=None)

    def pending_owned_launch_resume(request_path):
        """Find the exact parked Agent launch owned by this explicit Web resume."""
        lease = store.active_target_operation_lease()
        if (not isinstance(lease, dict) or lease.get('state') != 'STALE_BLOCKED'
                or lease.get('owner_kind') != 'workspace_resume'):
            return None
        operation_id = lease['operation_id']
        wait = store.get_host_state('owned_launch_wait_operation:' + operation_id)
        owner = wait.get('owner') if isinstance(wait, dict) else None
        scope = owner.get('scope') if isinstance(owner, dict) else None
        fields = {'target_lease_id', 'operation_id', 'workspace_id', 'revision_id',
                  'project_path', 'project_hash', 'source_run_id'}
        if (not isinstance(scope, dict) or set(scope) != fields
                or owner != {'owner_ref': _canonical_json_digest(scope), 'scope': scope}
                or any(not isinstance(scope[k], str) or not scope[k] for k in fields)
                or request_path != f"/api/v1/workspaces/{scope['workspace_id']}/resume"
                or scope['operation_id'] != operation_id or scope['target_lease_id'] != lease['id']
                or wait.get('exclusion_lease_id') != lease['id']
                or wait.get('state') not in {'parked', 'launch_pending', 'binding_pending',
                    'launch_unresolved', 'owned_modal', 'binding_ready', 'ready'}):
            return None
        try:
            original_target = json.loads(lease['target_identity_json'])
            if (set(original_target) != fields - {'target_lease_id'}
                    or any(original_target[k] != scope[k] for k in original_target if k != 'project_path')
                    or _normalized_workspace_path(original_target['project_path']) != _normalized_workspace_path(scope['project_path'])):
                return None
            source_run = store.get_run(scope['source_run_id'])
            if source_run is None or source_run.state not in {RunState.COMPLETED, RunState.CANCELLED, RunState.FAILED}:
                return None
            key = _native_workspace_recovery_state_key(source_run.conversation_id,
                scope['workspace_id'], scope['revision_id'])
            recovery = store.get_host_state(key)
            if (not isinstance(recovery, dict) or recovery.get('phase') != 'launch_pending'
                    or recovery.get('operation_id') != operation_id
                    or recovery.get('source_run_id') != scope['source_run_id']
                    or recovery.get('conversation_id') != source_run.conversation_id
                    or recovery.get('saved_sha256') != scope['project_hash']):
                return None
            binding, frame, pending = recovery.get('binding'), recovery.get('launch_pending_frame'), wait.get('pending')
            if (not isinstance(binding, dict) or not isinstance(frame, dict) or not isinstance(pending, dict)
                    or any(binding.get(k) != scope[k] for k in ('workspace_id', 'revision_id'))
                    or _normalized_workspace_path(binding.get('project_path')) != _normalized_workspace_path(scope['project_path'])
                    or any(type(frame.get(k)) is not int or frame[k] <= 0 for k in ('pid', 'hwnd'))
                    or type(frame.get('process_create_time')) not in {int, float}
                    or not math.isfinite(frame['process_create_time']) or frame['process_create_time'] <= 0
                    or any(pending.get(k) != frame[k] for k in ('pid', 'hwnd', 'process_create_time'))
                    or pending.get('launch_operation_id') != recovery.get('launch_operation_id')
                    or not isinstance(recovery.get('launch_operation_id'), str) or not recovery['launch_operation_id']):
                return None
            return {'operation_id': operation_id, 'source_run_id': scope['source_run_id'],
                'target_identity': original_target, 'owner': deepcopy(owner),
                'frame': deepcopy(frame), 'recovery_key': key, 'recovery': deepcopy(recovery)}
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            return None

    def complete_owned_launch_resume(workspace, session):
        context = launch_wait_request_context.get()
        original = context.get('recovery') if isinstance(context, dict) else None
        if original is None:
            return
        wait = store.get_host_state('owned_launch_wait_operation:' + original['operation_id'])
        state = store.get_host_state(original['recovery_key'])
        frame = original['frame']
        scope = original['owner']['scope']
        if (state != original['recovery'] or not isinstance(wait, dict) or wait.get('state') != 'ready'
                or wait.get('owner') != original['owner']
                or (workspace.workspace_id, workspace.revision_id) != (scope['workspace_id'], scope['revision_id'])
                or workspace.working_copy.current_sha256 != scope['project_hash']
                or any(session.get(k) != frame[k] for k in ('pid', 'hwnd'))
                or _engineering_session_process_create_time(session) != frame['process_create_time']
                or session.get('project_hash') != workspace.working_copy.current_sha256
                or _normalized_workspace_path(session.get('project_path')) != _normalized_workspace_path(workspace.working_copy.path)):
            raise HTTPException(status_code=409, detail='Original recovery completion scope changed')
        state.update(phase='rebound', rebound_kind='original_owned_launch_web_reconciled',
            new_binding={'workspace_id': workspace.workspace_id, 'revision_id': workspace.revision_id,
                **{k: session[k] for k in ('session_id', 'pid', 'hwnd', 'project_path')},
                'process_create_time': frame['process_create_time']})
        store.set_host_state(original['recovery_key'], state)

    async def target_switch_lease(
        request: Request,
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> AsyncIterator[None]:
        """Serialize workspace/attach requests for their complete route lifetime."""

        holder_token = str(uuid.uuid4())
        quarantined_reviewed_close = False
        allowed_change_transaction_id: str | None = None
        try:
            raw_body = await request.json()
        except (json.JSONDecodeError, RuntimeError):
            raw_body = {}
        if request.url.path == "/api/v1/xg5000/reviewed-no-save-close":
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
                checkpoint = await _reviewed_change_close_checkpoint(mcp, transaction)
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
                    or not _same_path(checkpoint.get("project_path"), transaction.target.project_path)
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
                request.state.prior_terminal_reviewed_close = False
                request.state.explicit_clean_reviewed_close = False
        elif request.url.path.endswith("/workspaces/open-local"):
            owner_kind = "workspace_open"
        elif request.url.path.endswith("/resume"):
            owner_kind = "workspace_resume"
        else:
            owner_kind = "engineering_attach"
        if not quarantined_reviewed_close:
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
        original_recovery = None
        try:
            store.reconcile_retired_owned_launch_target_lease()
            if owner_kind == 'workspace_resume':
                original_recovery = pending_owned_launch_resume(request.url.path)
                if original_recovery is not None:
                    operation_id = original_recovery['operation_id']
                    request_identity = original_recovery['target_identity']
            store.acquire_target_operation_lease(
                operation_id=operation_id,
                owner_kind=owner_kind,
                holder_token=holder_token,
                target_identity=request_identity,
                allowed_change_transaction_id=allowed_change_transaction_id,
            )
            lease_acquired = True
        except TargetOperationLeaseConflict as exc:
            store.active_target_operation_lease()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        request_token = launch_wait_request_context.set({"request": request,
            "operation_id": operation_id, "holder_token": holder_token, 'recovery': original_recovery})
        try:
            yield
        finally:
            launch_wait_request_context.reset(request_token)
            if lease_acquired:
                launch_wait = store.get_host_state(f"owned_launch_wait_operation:{operation_id}")
                recovered = (store.get_host_state(original_recovery['recovery_key'])
                    if original_recovery is not None else None)
                if (owned_launch_wait_requires_park(launch_wait)
                        or original_recovery is not None and (not isinstance(recovered, dict)
                            or recovered.get('phase') != 'rebound'
                            or recovered.get('operation_id') != operation_id)):
                    store.park_target_operation_lease(operation_id=operation_id, holder_token=holder_token,
                        reason="owned_launch_binding_wait_requires_read_only_rejoin")
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
                        json.dumps(program_source_identity(binding), sort_keys=True),
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
        initial_approval_actor_id: str | None = None,
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
            initial_approval_actor_id=initial_approval_actor_id,
        )
        if created:
            publish_change_event(
                (
                    "approval.resolved"
                    if initial_approval_actor_id is not None
                    else "approval.requested"
                ),
                transaction,
            )
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
        store.reconcile_retired_owned_launch_target_lease()
        store.supersede_stale_target_lease_for_durable_change_recovery()

        async def initialize_mcp_and_workspace() -> None:
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
                    transaction.target.workspace_id != restored.workspace_id
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
                    if transaction.state in {
                        ChangeTransactionState.PENDING_APPROVAL,
                        ChangeTransactionState.APPROVED,
                    }:
                        if transaction.target.artifact_hash == restored.working_copy.current_sha256:
                            session = await _rebind_recorded_change_session(
                                mcp,
                                transaction,
                                active_workspace,
                                expected_project_path=Path(restored.working_copy.path).resolve(),
                                expected_project_hash=restored.working_copy.current_sha256,
                            )
                            if session is not None:
                                bind_engineering_session(session)
                    elif transaction.state in {
                        ChangeTransactionState.COMPLETED,
                        ChangeTransactionState.ROLLED_BACK,
                        ChangeTransactionState.OBSERVING,
                    }:
                        await refresh_terminal_change_identity(
                            transaction,
                            retain_engineering_session=False,
                        )
                except (MCPToolError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.warning(
                        "latest workspace change could not be rebound at startup "
                        "(transaction=%s, kind=%s)",
                        transaction.id,
                        type(exc).__name__,
                    )
                break

        async def start_mcp() -> None:
            try:
                await initialize_mcp_and_workspace()
            finally:
                task = asyncio.current_task()
                if task is None or not task.cancelling():
                    # Startup is asynchronous. Browsers may have bootstrapped
                    # while tools were starting and need a fresh readiness view.
                    store.append_event(
                        event_type="host.status",
                        payload=current_status_v1().model_dump(mode="json", by_alias=True),
                    )

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
                        reasoning_effort=settings.reasoning_effort,
                        api_key=settings.secrets.api_key,
                        custom_headers=settings.secrets.custom_headers,
                        capability_verified_at=result.tested_at,
                        model_context=result.model_context,
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
    app.state.knowledge_store = knowledge_store
    app.state.mcp = mcp
    app.state.agent_supervisor = agent_supervisor
    app.state.model_repository = model_repository
    app.state.run_tasks = run_tasks
    app.state.change_executor = change_executor
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
            # Expired credentials still fail authentication. Keep only the
            # existing bounded refresh grace, so a rejected request cannot
            # erase the official renewal authority before refresh is attempted.
            if (
                expires_at is not None
                and expires_at + BROWSER_SESSION_REFRESH_GRACE <= datetime.now(timezone.utc)
            ):
                browser_sessions.pop(bearer_token, None)
                browser_session_readiness.pop(bearer_token, None)
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
            if expires_at + BROWSER_SESSION_REFRESH_GRACE <= now:
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
            if expires_at + BROWSER_SESSION_REFRESH_GRACE <= now:
                browser_sessions.pop(token, None)
                browser_session_readiness.pop(token, None)
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
                browser_session_readiness.pop(supplied_token, None)
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
                        ("Host가 관리하는 작업본에 native LD 부분 수정이 준비됐습니다. "
                         "영구 오프라인 수정 권한은 요청별로 선택합니다."
                         if _native_ld_edit_ready(active_session, active_workspace)
                         else "ControlStudio가 연 정확한 XG5000 PID에 읽기 전용 "
                              "프로젝트 세션이 연결되었습니다. 변경 권한은 닫혀 있습니다.")
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
                native_ld_edit_ready=bool(mcp_status.status == "ready" and _native_ld_edit_ready(active_session, active_workspace)),
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
                        _local_workspace_display_name(active_workspace, active_project_path)
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
                *([] if mcp_status.status == "ready" else []),
                *(["online_observe"] if mcp_status.status == "ready" else []),
                *(["offline_mutate"] if offline_mutation_configured else []),
                *(
                    []
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
                        "입력한 요구사항을 대화에서 검토합니다. 새 프로젝트 작성은 "
                        "별도로 선택한 요청 범위에서 진행하며 XG5000 작업 도구 연결이 필요합니다."
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

    @app.get("/api/v1/project-tools/connection")
    async def project_tools_connection_v1() -> dict[str, str | int]:
        return mcp.project_tools_connection_status()

    @app.post("/api/v1/project-tools/reconnect", status_code=status.HTTP_202_ACCEPTED)
    async def reconnect_project_tools_v1(
        _csrf: None = Depends(require_browser_csrf),
    ) -> dict[str, str | int]:
        # Admission and connection scheduling are atomic with normal run submission.
        async with run_submission_lock:
            if mcp.status.status != "ready":
                if any(not task.done() for task in run_tasks.values()) or active_change_executions:
                    raise HTTPException(status_code=409, detail="작업이 진행 중이어서 연결을 복구할 수 없습니다. 작업이 끝난 뒤 다시 확인해 주세요.")
                if store.active_target_operation_lease() is not None:
                    raise HTTPException(status_code=409, detail="작업 대상 연결이 진행 중입니다. 해당 작업이 끝난 뒤 다시 확인해 주세요.")
            try:
                mcp.request_reconnect()
            except MCPTransportError as exc:
                raise HTTPException(status_code=503, detail="앱 종료 중에는 연결을 복구할 수 없습니다.") from exc
            return mcp.project_tools_connection_status()

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
            store.selected_target_recovery_authority()
            store.active_target_operation_lease()
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

    async def _restart_is_safe() -> None:
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
        store.selected_target_recovery_authority()
        store.active_target_operation_lease()

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
            await _restart_is_safe()
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
                preparation = asyncio.create_task(asyncio.to_thread(restart_callback, handoff))
                while True:
                    try:
                        await asyncio.shield(preparation)
                        break
                    except asyncio.CancelledError:
                        # The fixed bootstrap may already have spawned. Join its
                        # outcome before publishing receipt/shutdown ownership.
                        if preparation.cancelled():
                            raise
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
        # Capture the replay boundary before reading snapshot state so events
        # committed during snapshot construction remain in the live stream.
        snapshot_sequence = store.latest_event_sequence()
        bootstrap = BootstrapV1(
            status=current_status_v1(),
            csrf_token=browser_csrf_token(request),
            client_session_id=str(uuid.uuid4()),
            last_event_sequence=snapshot_sequence,
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
            current_engineering_conversation_target()
            if body.workflow == "engineering" and body.target_scope == "current"
            else None
        )
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
        conversation = _conversation_v1(
            detail,
            project_plan_authority_digests=project_plan_authority_digests,
            engineering_mission=store.get_engineering_mission(detail.id),
            native_completion_current_state=store.get_host_state(completion_state_key(detail.workspace_id, detail.revision_id)),
            native_completion_saved_sha256=(active_workspace.get("working_copy", {}).get("current_sha256")
                if isinstance(active_workspace, dict) and active_workspace.get("workspace_id") == detail.workspace_id
                and active_workspace.get("revision_id") == detail.revision_id else None),
        )
        latest_run = max(detail.runs, key=lambda item: item.created_at, default=None)
        conversation.allow_simulator = bool(latest_run and latest_run.metadata.get("allow_simulator") is True)
        scope = latest_run.metadata.get("change_request_scope") if latest_run else None
        if scope in {"analysis_only", "offline_candidate_revision", "permanent_offline_revision"}:
            conversation.request_scope = scope
        try:
            retained = await asyncio.to_thread(latest_native_checkpoint_summary, store, conversation_id)
        except (AgentProtocolError, KeyError, TypeError, ValueError):
            # A corrupt checkpoint must disable resume, not hide the public chat.
            retained = None
        if retained is not None:
            source = store.get_run(retained["source_run_id"])
            binding = retained["binding"]
            source_message = next((item for item in detail.messages
                if source is not None and item.id == source.request_message_id), None)
            resumable = bool(_native_resume_latest_attempt_matches_checkpoint(
                detail, source, source_message, latest_run, store)
                and native_resume_source_state_allowed(detail, source)
                and native_resume_target_matches_checkpoint(detail, source.id, binding))
            conversation.native_execution = ConversationNativeExecutionV1(
                source_run_id=source.id, request_message_id=source.request_message_id,
                state=source.state.value.casefold(), resumable=resumable,
                completed_tool_calls=retained["completed_tool_calls"],
            )
        return conversation

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
        nonlocal active_engineering_session, active_workspace
        settings: StoredModelConnection | None = None
        native_execution_started = False
        native_start_continuation = False
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
            history = [item for item in detail.messages if item.id != request_message_id]
            request_message = next(
                (item for item in detail.messages if item.id == request_message_id),
                None,
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
                in {
                    "offline_native_check_only",
                    "offline_native_reopen",
                    
                }
                else "offline_native_reopen"
            )
            native_resume_source = (request_message.metadata.get("resume_native_run_id")
                if request_message is not None else None)
            resume_checkpoint = None
            if native_resume_source is not None:
                # Admission reads source metadata only. Validate the full history
                # before model/native work, and reuse this local result below.
                resume_checkpoint = await asyncio.to_thread(
                    _host_sqlite_boundary, "native_checkpoint_restore",
                    latest_native_checkpoint, store, conversation_id)
                if (not isinstance(resume_checkpoint, dict)
                        or resume_checkpoint.get("source_run_id") != native_resume_source):
                    raise AgentRunPreconditionError("재개할 native 요청과 보존된 이력이 다릅니다.")
            expected_engineering_target = (
                current_engineering_conversation_target()
                if workflow == "engineering"
                else None
            )
            stored_target_values = (
                detail.workspace_id,
                detail.revision_id,
                detail.engineering_session_id,
            )
            stored_engineering_target = (
                tuple(str(item) for item in stored_target_values)
                if all(isinstance(item, str) and item for item in stored_target_values)
                else None
            )
            targetless_analysis_run = bool(
                workflow == "engineering"
                and change_request_scope == "analysis_only"
                and all(item is None for item in stored_target_values)
                and detail.engineering_handoff is None
            )
            if targetless_analysis_run:
                # An unbound conversation stays independent of a project opened
                # elsewhere in the app, including across refresh and restart.
                expected_engineering_target = None
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
            recovery_source = (request_message.metadata.get('resume_native_run_id')
                or request_message.metadata.get('workspace_recovery_source_run_id')) if request_message is not None else None
            recovery_anchor = (pending_native_recovery_anchor(detail, recovery_source)
                if workflow == 'engineering' and change_request_scope == 'permanent_offline_revision'
                and verification_scope == 'offline_native_check_only' else None)
            if _pending_recovery_conversation_target(stored_engineering_target,
                    mcp.agent_session_identity_snapshot, recovery_anchor,
                    file_only=(recovery_anchor is not None and recovery_workspace_only_binding(
                        mcp.agent_session_identity_snapshot, recovery_anchor))):
                # This is the durable owner identity, not an admission of a live frame.
                # The native execution below remains restricted to the pending recovery.
                expected_engineering_target = stored_engineering_target
            if (
                workflow == "engineering"
                and stored_engineering_target != expected_engineering_target
            ):
                store.append_event(
                    conversation_id=conversation_id, run_id=run_id,
                    event_type="run.admission_rejected",
                    payload={"stage":"engineering_target_binding", "native_execution_started":False},
                )
                raise AgentRunPreconditionError(_ENGINEERING_TARGET_PRECONDITION_MESSAGE)
            if (
                workflow == "engineering"
                and expected_engineering_target is None
                and not targetless_analysis_run
            ):
                raise AgentRunPreconditionError(
                    "the engineering run has no verified target binding"
                )
            native_engineering_status_snapshot: dict[str, Any] | None = None
            if workflow == "engineering" and detail.engineering_handoff is not None:
                assert expected_engineering_target is not None
                try:
                    native_engineering_status_snapshot = (
                        None
                    )
                    # Readiness does not imply that the source passed its trial.
                    # Preserve diagnostics from this exact, validated handoff.
                    handoff = detail.engineering_handoff
                except (ProjectPlanReviewConflict, ValueError) as exc:
                    logger.warning(
                        "native engineering handoff precondition failed: %s",
                        exc,
                        exc_info=True,
                    )
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
            engineering_mission_continue = bool(
                request_message is not None
                and request_message.role is MessageRole.SYSTEM
                and request_message.metadata.get("engineering_mission_continue") is True
            )
            terminal_candidate_reproposal_scope = (
                request_message.metadata.get("terminal_candidate_reproposal_scope")
                if engineering_mission_continue and request_message is not None
                else None
            )
            terminal_candidate_reproposal_verification_scope = (
                request_message.metadata.get(
                    "terminal_candidate_reproposal_verification_scope"
                )
                if engineering_mission_continue and request_message is not None
                else None
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
            planning_prior_design_failure: dict[str, Any] | None = None
            previous_plan_snapshot = None
            if workflow == "new_project_planning":
                previous_plan_snapshot = latest_project_plan_snapshot(history)
                native_preparation_run = (
                    change_request_scope == "permanent_offline_revision"
                    and verification_scope == "offline_native_check_only"
                    and not targetless_analysis_run
                )
                if not native_preparation_run:
                    planning_events: list[StoredEvent] = []
                    after_event_sequence = 0
                    while True:
                        event_page = store.list_events(
                            after_sequence=after_event_sequence,
                            conversation_id=conversation_id,
                            limit=250,
                        )
                        planning_events.extend(event_page)
                        if len(event_page) < 250:
                            break
                        after_event_sequence = event_page[-1].sequence
                    planning_prior_design_failure = _prior_design_failure_observation(
                        planning_events,
                        conversation_id=conversation_id,
                        current_run_id=run_id,
                    )
                if detail.project_brief is not None:
                    try:
                        planning_attachments = resolve_project_brief_attachments(
                            detail.project_brief
                        )
                    except ValueError as exc:
                        raise AgentRunPreconditionError(
                            f"첨부 자료를 사용할 수 없습니다: {exc}"
                        ) from exc
                    planning_project_brief_digest = calculate_project_brief_digest(
                        detail.project_brief
                    )
                    requirement_message_sources = (
                        _planning_requirement_message_sources(
                            detail.messages,
                            retained_message_ids=(
                                None
                            ),
                            narrative_adoption_boundary_message_id=(
                                None
                            ),
                        )
                    )
                    # Keep adoption/message provenance unchanged. Incomplete
                    # working citations stay available through design tools,
                    # not in the strict trusted-ready advisory handoff.
                    planning_brief = {
                        **detail.project_brief,
                        "attachments": planning_attachments,
                        "requirement_message_sources": requirement_message_sources,
                    }
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
                if workflow == "engineering"
                and not targetless_analysis_run
                and isinstance(active_workspace, dict)
                else None
            )
            engineering_session_snapshot = (
                deepcopy(active_engineering_session)
                if workflow == "engineering"
                and not targetless_analysis_run
                and (_engineering_session_read_ready(active_engineering_session)
                     or (change_request_scope == "permanent_offline_revision"
                         and verification_scope == "offline_native_check_only"
                         and _native_ld_edit_ready(active_engineering_session, active_workspace)))
                else None
            )
            if (
                workflow == "engineering"
                and not targetless_analysis_run
                and mcp.status.status != "ready"
            ):
                raise AgentRunPreconditionError("MCP 안전 코어가 준비되지 않았습니다.")

            preparation_run = workflow == "new_project_planning" and change_request_scope == "permanent_offline_revision"
            if (workflow in {"engineering", "new_project_planning"} and change_request_scope == "permanent_offline_revision"
                    and verification_scope == "offline_native_check_only" and not targetless_analysis_run):
                # A direct user-authorized working-copy run owns one native loop;
                # it does not turn its observations into a planning mission.
                pending_anchor=(pending_native_recovery_anchor(detail,request_message.metadata.get('resume_native_run_id')
                    or request_message.metadata.get('workspace_recovery_source_run_id'))
                    if request_message is not None and not preparation_run else None)
                if not preparation_run and pending_anchor is None and (not isinstance(knowledge_workspace_snapshot, dict)
                        or not _native_ld_edit_ready(engineering_session_snapshot, knowledge_workspace_snapshot)):
                    raise AgentRunPreconditionError("수정할 작업 사본의 편집 대상이 연결되지 않았습니다.")
                target = pending_anchor if pending_anchor is not None else mcp.agent_session_identity_snapshot
                preparation = None
                if preparation_run:
                    if mcp.status.status != "ready" or not isinstance(detail.project_brief, dict):
                        raise AgentRunPreconditionError("신규 프로젝트 준비를 위한 MCP와 요구사항이 필요합니다.")
                    from .native_project_preparation import validate_preparation_display_name
                    preparation_key = f"native_project_preparation:{conversation_id}"
                    preparation = store.get_host_state(preparation_key)
                    if preparation is not None:
                        validate_preparation_display_name(preparation["reservation"], detail.project_brief["project_name"])
                    def preparation_target_identity(value: object) -> object:
                        if not isinstance(value, dict):
                            return value
                        path = value.get("working_path") or value.get("project_path")
                        return {
                            **{key: value.get(key) for key in (
                                "workspace_id", "revision_id", "source_kind", "source_sha256",
                                "provenance_receipt_digest", "working_sha256")},
                            "path": str(Path(path).resolve()).casefold() if isinstance(path, str) else None,
                        }
                    # A Host restart can leave the selected working copy file-only
                    # while its previously owned editor remains open. Revalidate
                    # that exact retained frame before using the existing saved
                    # previous-project path for native preparation.
                    if ((target is None or (isinstance(target, dict) and not any(
                            target.get(k) is not None for k in ('pid', 'hwnd', 'session_id'))))
                            and isinstance(active_workspace, dict)
                            and not (isinstance(preparation, dict) and preparation.get('idle_frame_authority'))):
                        retained = retained_native_workspace_binding(active_workspace)
                        anchor = retained.get('session') if isinstance(retained, dict) else None
                        workspace_binding = workspace_agent_binding(active_workspace)
                        prior = preparation.get('initial_target') if isinstance(preparation, dict) else target
                        same_workspace = (isinstance(prior, dict)
                            and preparation_target_identity(prior) == preparation_target_identity(workspace_binding)
                            and not any(prior.get(k) is not None for k in ('pid', 'hwnd', 'session_id')))
                        same_workspace = same_workspace and (target is None or
                            preparation_target_identity(target) == preparation_target_identity(workspace_binding))
                        if same_workspace and isinstance(anchor, dict):
                            created = _engineering_session_process_create_time(anchor)
                            observed = await mcp.call_read_tool('xg5000.instances', {})
                            instances = observed.get('instances', []) if isinstance(observed, dict) else []
                            frame = preparation.get('frame') if isinstance(preparation, dict) else None
                            frame_matches = (frame is None or (isinstance(frame, dict)
                                and frame.get('pid') == anchor.get('pid') and frame.get('hwnd') == anchor.get('hwnd')
                                and frame.get('expected_process_create_time') == created))
                            if (len(instances) == 1 and isinstance(instances[0], dict) and created is not None
                                    and instances[0].get('pid') == anchor.get('pid')
                                    and instances[0].get('hwnd') == anchor.get('hwnd')
                                    and instances[0].get('process_create_time') == created and frame_matches):
                                if store.live_change_approval_transaction(mutation_scope=f"workspace:{workspace_binding['workspace_id']}",
                                                                         now=datetime.now(timezone.utc)) is not None:
                                    raise AgentRunPreconditionError("현재 작업 사본의 실행 중인 변경 거래를 먼저 종료해 주세요.")
                                candidate = await revalidate_native_workspace_binding(_local_workspace_v1(active_workspace))
                                if candidate is not None and _native_ld_edit_ready(candidate, active_workspace):
                                    bind_engineering_session(candidate)
                                    target = mcp.agent_session_identity_snapshot
                                    if preparation is not None:
                                        preparation['initial_target'] = deepcopy(target)
                                        store.set_host_state(preparation_key, preparation)
                    if preparation is None:
                        if isinstance(target, dict) and all(target.get(key) for key in ("pid", "hwnd")):
                            if not _native_ld_edit_ready(active_engineering_session, active_workspace):
                                raise AgentRunPreconditionError("현재 창은 준비 권한이 결속된 작업 사본이 아닙니다. 원본 프로젝트는 그대로 유지합니다.")
                            if store.live_change_approval_transaction(mutation_scope=f"workspace:{target['workspace_id']}",
                                                                     now=datetime.now(timezone.utc)) is not None:
                                raise AgentRunPreconditionError("현재 작업 사본의 실행 중인 변경 거래를 먼저 종료해 주세요.")
                        observed = await mcp.call_read_tool("xg5000.instances", {})
                        instances = observed.get("instances", []) if isinstance(observed, dict) else []
                        if isinstance(target, dict) and all(target.get(key) for key in ("pid", "hwnd")):
                            instances = [item for item in instances if all(item.get(key) == target[key] for key in ("pid", "hwnd"))]
                            # Restored native authority retains its lifetime in
                            # the revalidation anchor, just like other native
                            # callers. A legacy partial identity is not that proof.
                            created = _engineering_session_process_create_time(active_engineering_session)
                            actual_created = instances[0].get("process_create_time") if len(instances) == 1 else None
                            if (created is None or not isinstance(actual_created, (int, float))
                                    or isinstance(actual_created, bool) or not math.isfinite(actual_created)
                                    or abs(actual_created - created) >= 0.001):
                                raise AgentRunPreconditionError("신규 프로젝트 준비 전 XG5000 프로세스가 변경되었습니다.")
                        if len(instances) != 1 or not isinstance(instances[0].get("process_create_time"), (int, float)):
                            raise AgentRunPreconditionError("신규 프로젝트를 준비할 XG5000 창을 하나 선택해 주세요.")
                        frame = {key: instances[0][key] for key in ("pid", "hwnd")}
                        frame["expected_process_create_time"] = instances[0]["process_create_time"]
                        reservation = await mcp.call_workspace_tool("workspace.reserve_native", {
                            "workspace_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()),
                            "project_name": "project",
                            "display_name": detail.project_brief["project_name"]})
                        preparation = {"reservation": reservation, "frame": frame, "initial_target": deepcopy(target)}
                        store.set_host_state(preparation_key, preparation)
                    initial_target = preparation["initial_target"]
                    if (isinstance(initial_target, dict)
                            and any(initial_target.get(key) is not None for key in ("session_id", "pid", "hwnd"))
                            and active_engineering_session is None
                            and (target is None or not all(target.get(key) for key in ("session_id", "pid", "hwnd")))):
                        # A pending creation may have closed the former project.
                        # Restore only its persisted preparation frame, never the
                        # former editor/session capability or a project-open input.
                        retained_binding = retained_native_workspace_binding(initial_target)
                        retained = retained_binding.get("session") if isinstance(retained_binding, dict) else None
                        frame = preparation["frame"]
                        if (not isinstance(retained, dict)
                                or any(retained.get(key) != frame.get(key) for key in ("pid", "hwnd"))
                                or _engineering_session_process_create_time(retained) != frame.get("expected_process_create_time")
                                or any(retained_binding.get(key) != initial_target.get(key) for key in ("workspace_id", "revision_id"))):
                            raise AgentRunPreconditionError("보존한 신규 프로젝트 준비 프레임의 권한을 확인할 수 없습니다.")
                        observed = await mcp.call_read_tool("xg5000.instances", {})
                        instances = observed.get("instances", []) if isinstance(observed, dict) else []
                        matches = [row for row in instances if row.get("pid") == frame["pid"] and row.get("hwnd") == frame["hwnd"]]
                        if (len(matches) != 1 or matches[0].get("process_create_time") != frame["expected_process_create_time"]):
                            raise AgentRunPreconditionError("재개할 신규 프로젝트 준비 창이 변경되었습니다.")
                        if not preparation.get("idle_frame_authority"):
                            frame.update(previous_project_path=retained["project_path"], previous_project_hash=retained["project_hash"])
                        if isinstance(target, dict) and any(target.get(key) != initial_target.get(key) for key in ("workspace_id", "revision_id")):
                            raise AgentRunPreconditionError("신규 준비 복원 중 작업 사본이 변경되었습니다.")
                        preparation["initial_target"] = deepcopy(target)
                        preparation["preparation_frame_restored"] = True
                        initial_target = deepcopy(target)
                        store.set_host_state(preparation_key, preparation)
                    # A verified reconnect can renew the Host session id while
                    # retaining the same workspace, revision, frame and file.
                    # The CLI still checks the original process lifetime before
                    # any creation input, including retained preparation retries.
                    if preparation_target_identity(initial_target) != preparation_target_identity(target):
                        raise AgentRunPreconditionError("신규 프로젝트 준비 중 초기 IDE 대상이 변경되었습니다.")
                    if (isinstance(preparation["initial_target"], dict)
                            and any(preparation["initial_target"].get(key) is not None for key in ("session_id", "pid", "hwnd"))
                            and not preparation.get("idle_frame_authority")
                            and not preparation.get("preparation_frame_restored")):
                        # Agent identity intentionally omits the private saved
                        # hash. Supply the previous working-copy proof from the
                        # native-authorized Host session, including after retry.
                        if not _native_ld_edit_ready(active_engineering_session, active_workspace):
                            raise AgentRunPreconditionError("신규 작성 전에 현재 작업 사본을 다시 연결해 주세요.")
                        previous_path = active_engineering_session.get("project_path")
                        previous_hash = active_engineering_session.get("project_hash")
                        initial_path = initial_target.get("working_path") or initial_target.get("project_path")
                        frame = preparation["frame"]
                        created = _engineering_session_process_create_time(active_engineering_session)
                        if (any(active_engineering_session.get(key) != frame.get(key) for key in ("pid", "hwnd"))
                                or created is None or abs(created - frame["expected_process_create_time"]) >= 0.001):
                            raise AgentRunPreconditionError("신규 프로젝트 준비를 위해 연결한 XG5000 프레임이 변경되었습니다.")
                        if (not isinstance(previous_path, str) or not isinstance(initial_path, str)
                                or Path(previous_path).resolve() != Path(initial_path).resolve()
                                or not isinstance(previous_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_hash)):
                            raise AgentRunPreconditionError("현재 작업 사본의 저장본 식별 정보가 불완전합니다.")
                        preparation["frame"].update(previous_project_path=previous_path,
                                                    previous_project_hash=previous_hash)
                        # The durable reservation survives a reconnect; this
                        # run must compare against the newly verified live
                        # session snapshot, not its former static file binding.
                        preparation["initial_target"] = deepcopy(target)
                        store.set_host_state(preparation_key, preparation)
                    target = {**preparation["reservation"], **preparation["frame"]}
                if not preparation_run and (not isinstance(target, dict) or any(not target.get(field) for field in (
                    "workspace_id", "revision_id", "session_id", "pid", "hwnd", "project_path"))):
                    raise AgentRunPreconditionError("수정할 작업 사본의 Host 대상 결속이 불완전합니다.")
                if not preparation_run and pending_anchor is None and (
                    any(target.get(key) != knowledge_workspace_snapshot.get(key) for key in ("workspace_id", "revision_id"))
                    or any(target.get(key) != engineering_session_snapshot.get(key) for key in ("session_id", "pid", "hwnd", "project_path"))
                ):
                    raise AgentRunPreconditionError("native 편집 대상이 이 대화의 검증된 작업 사본과 다릅니다.")
                if store.live_change_approval_transaction(
                    mutation_scope=f"workspace:{detail.workspace_id}", now=datetime.now(timezone.utc),
                ) is not None:
                    raise AgentRunPreconditionError("현재 작업 사본의 기존 변경 거래가 아직 종료되지 않았습니다.")
                retained_mission = store.get_engineering_mission(conversation_id)
                retained_change = (store.get_change_transaction(retained_mission.change_transaction_id)
                    if retained_mission is not None and retained_mission.change_transaction_id is not None else None)
                if retained_change is not None and retained_change.state is ChangeTransactionState.QUARANTINED:
                    raise AgentRunPreconditionError("현재 작업 사본의 미확정 변경 복구가 먼저 필요합니다.")
                binding = {key: target[key] for key in ("workspace_id", "revision_id", "project_path")}
                index_key = f"native_ld_checkpoint_latest:{conversation_id}"
                previous = (resume_checkpoint if native_resume_source is not None else
                    await asyncio.to_thread(_host_sqlite_boundary, "native_checkpoint_restore",
                                            latest_native_checkpoint, store, conversation_id))
                resume_checkpoint = None  # Do not retain a second full-tree reference.
                prior_run = store.get_run(previous.get("source_run_id", "")) if isinstance(previous, dict) else None
                resume_source = request_message.metadata.get("resume_native_run_id") if request_message is not None else None
                native_checkpoint = None
                if previous is not None and previous.get("binding") == binding and prior_run is not None:
                    if resume_source is not None:
                        if (resume_source != prior_run.id or not native_resume_source_state_allowed(detail, prior_run)
                                or not native_checkpoint_matches_request(previous["checkpoint"], prompt, binding)):
                            raise AgentRunPreconditionError("재개할 native 요청과 보존된 이력이 다릅니다.")
                        native_checkpoint = previous["checkpoint"]
                    elif prior_run.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                        native_checkpoint = (previous["checkpoint"]
                            if native_checkpoint_matches_request(previous["checkpoint"], prompt, binding)
                            and prior_run.state in {RunState.FAILED, RunState.CANCELLED}
                            else await native_checkpoint_boundary(asyncio.to_thread(
                                native_followup_checkpoint, previous, prompt, binding)))
                elif resume_source is not None:
                    raise AgentRunPreconditionError("재개할 native 작업 대상이 변경되었습니다.")
                checkpoint_key = f"native_ld_checkpoint:{run_id}"
                if native_checkpoint is not None:
                    native_checkpoint = await native_checkpoint_boundary(asyncio.to_thread(
                        validated_native_checkpoint_snapshot, native_checkpoint, decoded_json=False))
                    checkpoint_digest = native_checkpoint.digest
                    await native_checkpoint_boundary(asyncio.to_thread(
                        _host_sqlite_boundary, "native_checkpoint_resume_persist", persist_native_checkpoint_with_index,
                        store, checkpoint_key, index_key, binding,
                        native_checkpoint, checkpoint_digest, source_run_id=run_id))

                # The normalized resume checkpoint is independent; release the prior full tree.
                del previous

                raw_receipt_prefix = f"native_ld_receipt:{run_id}:"
                raw_receipt_sequence = 0

                def record_raw(name: str, arguments: dict[str, Any], result: Any) -> None:
                    nonlocal raw_receipt_sequence
                    if raw_receipt_sequence == 0:
                        _host_sqlite_boundary("native_tool_receipt_persist", store.set_host_state, f"native_ld_receipts:{run_id}", {
                            "schemaVersion": "2", "binding": binding,
                            "storage": "ordered_private_host_state",
                            "record_key_prefix": raw_receipt_prefix,
                        })
                    _host_sqlite_boundary("native_tool_receipt_persist", store.set_host_state, f"{raw_receipt_prefix}{raw_receipt_sequence:08d}", {
                        "sequence": raw_receipt_sequence, "tool_name": name,
                        "arguments": deepcopy(arguments), "result": deepcopy(result),
                    })
                    raw_receipt_sequence += 1

                def record_process_diagnostic(observation):
                    # Private, run-owned read-only artifact; never a native fact or model input.
                    _host_sqlite_boundary('native_vendor_process_diagnostic_persist',store.set_host_state,
                        f'native_vendor_process_lifecycle:{run_id}',{
                            'schema':'controlstudio-native-vendor-process-artifact/v1',
                            'conversation_id':conversation_id,'run_id':run_id,
                            'observation':deepcopy(observation)})

                def refresh_saved(receipt: dict[str, Any]) -> None:
                    nonlocal active_workspace, active_engineering_session
                    if not isinstance(active_workspace, dict) or not isinstance(active_engineering_session, dict):
                        raise AgentRunPreconditionError("저장 관측 중 작업 대상이 변경되었습니다.")
                    current = mcp.agent_session_identity_snapshot
                    if current is None or any(current.get(key) != target.get(key) for key in (
                        "workspace_id", "revision_id", "session_id", "pid", "hwnd", "project_path")):
                        raise AgentRunPreconditionError("저장 관측 중 작업 대상이 변경되었습니다.")
                    digest = receipt["sha256"]
                    workspace, session = deepcopy(active_workspace), deepcopy(active_engineering_session)
                    workspace["working_copy"]["current_sha256"] = digest
                    workspace["working_copy"]["state"] = ("clean" if digest == workspace["working_copy"].get("baseline_sha256") else "modified")
                    metadata = session.setdefault("metadata", {})
                    old_saved = metadata.get("native_ld_saved_observation") or {}
                    metadata["native_ld_saved_observation"] = {
                        **{key: session[key] for key in ("session_id", "pid", "hwnd", "project_path")},
                        "anchor_project_hash": old_saved.get("anchor_project_hash", session["project_hash"]),
                        "current_project_hash": digest, "disk_receipt": deepcopy(receipt),
                    }
                    session["project_hash"] = digest
                    host_evidence = metadata.get("native_ld_host_revalidation")
                    if isinstance(host_evidence, dict):
                        anchor = host_evidence["binding"]["session"]
                        anchor_saved = deepcopy(metadata["native_ld_saved_observation"])
                        anchor_saved["session_id"] = anchor["session_id"]
                        anchor.setdefault("metadata", {})["native_ld_saved_observation"] = anchor_saved
                        anchor["project_hash"] = digest
                        host_evidence["observed"]["project_hash"] = digest
                    active_workspace, active_engineering_session = workspace, session
                    _host_sqlite_boundary("native_saved_binding_persist", store.set_host_state, "active_workspace", workspace)
                    _host_sqlite_boundary("native_saved_binding_persist", update_native_workspace_binding, session)
                    broker_binding = {**session, **workspace_agent_binding(workspace)}
                    execution_broker.bind_agent_session(broker_binding)

                pending_recovery_ui_identity=None
                pending_recovery_ui_anchor=None
                pending_recovery_projection_identity=None

                def publish_workspace_recovery_scope(executor,state):
                    """The pending operation owns its original frame until disposition."""
                    nonlocal pending_recovery_ui_identity,pending_recovery_ui_anchor,pending_recovery_projection_identity
                    pending=state['phase'] in {'close_pending','closed','launch_pending','launch_terminal'}
                    executor.recovery_pending=pending
                    executor.recovery_only=pending
                    pending_recovery_ui_identity=None
                    pending_recovery_ui_anchor=None
                    pending_recovery_projection_identity=None
                    if pending:
                        anchor={**{k:executor.target[k] for k in
                            ('workspace_id','revision_id','session_id','pid','hwnd','project_path')},
                            'process_create_time':executor.expected_process_create_time}
                        original=state.get('binding')
                        if (isinstance(original,dict)
                                and all(original.get(k)==anchor[k] for k in ('workspace_id','revision_id'))
                                and _normalized_workspace_path(original.get('project_path'))==_normalized_workspace_path(anchor['project_path'])):
                            pending_recovery_projection_identity={k:deepcopy(state[k]) for k in
                                ('operation_id','source_run_id','binding','saved_sha256')}
                            if (state['phase']=='close_pending' and all(original.get(k)==anchor[k]
                                    for k in ('pid','hwnd','process_create_time'))):
                                pending_recovery_ui_anchor=anchor
                                pending_recovery_ui_identity=deepcopy(pending_recovery_projection_identity)

                async def restore_pending_recovery_owner(executor):
                    nonlocal active_engineering_session
                    if preparation_run or native_checkpoint is None:
                        return
                    key=_native_workspace_recovery_state_key(conversation_id,
                        executor.target['workspace_id'],executor.target['revision_id'])
                    state=store.get_host_state(key)
                    if not isinstance(state,dict) or state.get('phase') not in {
                            'close_pending','closed','launch_pending','launch_terminal'}:
                        return
                    source=store.get_run(state.get('source_run_id',''))
                    if (state.get('conversation_id')!=conversation_id or source is None
                            or source.conversation_id!=conversation_id
                            or source.state not in {RunState.FAILED,RunState.CANCELLED,RunState.COMPLETED}
                            or source.metadata.get('change_request_scope')!='permanent_offline_revision'
                            or source.metadata.get('verification_scope')!='offline_native_check_only'):
                        raise AgentRunPreconditionError('Pending recovery owner is not a terminal request in this conversation')
                    anchor=state.get('binding')
                    if (not isinstance(anchor,dict) or not anchor.get('session_id')
                            or any(type(anchor.get(k)) is not int or anchor[k]<=0 for k in ('pid','hwnd'))
                            or type(anchor.get('process_create_time')) not in {int,float}
                            or not math.isfinite(anchor['process_create_time']) or anchor['process_create_time']<=0
                            or type(executor.expected_process_create_time) not in {int,float}
                            or not math.isfinite(executor.expected_process_create_time) or executor.expected_process_create_time<=0
                            or any(anchor.get(k)!=executor.target.get(k) for k in ('workspace_id','revision_id'))
                            or _normalized_workspace_path(anchor.get('project_path'))!=_normalized_workspace_path(executor.target.get('project_path'))):
                        raise AgentRunPreconditionError('Pending recovery original target differs from the restored request')
                    original_generation=(all(anchor.get(k)==executor.target.get(k) for k in ('pid','hwnd'))
                        and anchor['process_create_time']==executor.expected_process_create_time)
                    recovery_source=(resume_source or (request_message.metadata.get('workspace_recovery_source_run_id')
                        if request_message is not None else None))
                    owns_recovery=(recovery_source is not None and prior_run is not None
                        and recovery_source==prior_run.id
                        and recovery_source_run_matches(prior_run.id,state['source_run_id'],conversation_id))
                    if not owns_recovery:
                        from .native_workspace_host import native_recovery_target_retired
                        from .store import verified_owned_launch_retirement
                        # A follow-up checkpoint carries conversation context, not
                        # the old recovery's ownership. Keep terminal history exact;
                        # admit only an independently bound replacement after both
                        # the failed launch and its original frame have retired.
                        if (state['phase']=='launch_terminal' and not original_generation
                                and verified_owned_launch_retirement(state.get('terminal_failure')) is not None
                                and admitted_native_workspace_generation(executor) is not None
                                and native_recovery_target_retired(anchor,anchor['process_create_time'])):
                            return
                        raise AgentRunPreconditionError('Pending recovery requires its original request resume')
                    if not _pending_native_recovery_saved_matches(anchor,executor.target['workspace_id'],
                            executor.target['revision_id'],state.get('saved_sha256')):
                        raise AgentRunPreconditionError('Pending recovery original target differs from the restored request')
                    current=mcp.agent_session_identity_snapshot
                    if recovery_workspace_only_binding(current,anchor) and not original_generation:
                        raise AgentRunPreconditionError('Pending recovery replacement frame was not observed during restore')
                    if not recovery_workspace_only_binding(current,anchor):
                        if (current is not None and (not isinstance(current,dict)
                                or any(current.get(k)!=executor.target.get(k) for k in
                                    ('workspace_id','revision_id','session_id','pid','hwnd','project_path')))):
                            raise AgentRunPreconditionError('Pending recovery current target differs from the restored request')
                        observed=await mcp.call_read_tool('xg5000.instances',{})
                        rows=observed.get('instances') if isinstance(observed,dict) else None
                        if (not isinstance(rows,list) or any(not isinstance(row,dict)
                                or type(row.get('pid')) is not int or type(row.get('hwnd')) is not int for row in rows)):
                            raise AgentRunPreconditionError('Pending recovery instances were not observed during restore')
                        matches=[row for row in rows if isinstance(row,dict)
                            and (row.get('pid')==executor.target['pid'] or row.get('hwnd')==executor.target['hwnd'])]
                        if not matches:
                            if not original_generation:
                                raise AgentRunPreconditionError('Pending recovery replacement frame was not observed during restore')
                            # Absence withdraws stale native readiness, not proof of
                            # retirement. The existing recovery callback must still
                            # inspect the original process/main before any reopen.
                            active_engineering_session=None
                            bind_agent_workspace_target(active_workspace)
                        elif (len(matches)!=1 or matches[0].get('visible') is not True
                                or any(matches[0].get(k)!=executor.target[k] for k in ('pid','hwnd'))
                                or matches[0].get('process_create_time')!=executor.expected_process_create_time):
                            raise AgentRunPreconditionError('Pending recovery live frame/lifetime changed during restore')
                        elif original_generation:
                            state['current_binding']={**deepcopy(executor.target),
                                'process_create_time':executor.expected_process_create_time}
                    original_source=state['source_run_id']
                    if original_source!=run_id:
                        state.setdefault('resumed_source_runs',[]).append(original_source)
                        state['source_run_id']=run_id
                    store.set_host_state(key,state)
                    publish_workspace_recovery_scope(executor,state)

                async def validate_pending_recovery_owned_ui(executor, *, require_live_frame=True):
                    """Fresh read/cancel scope of the original close owner; never a binding."""
                    identity=pending_recovery_ui_identity
                    anchor=pending_recovery_ui_anchor
                    if (not isinstance(identity,dict) or not isinstance(anchor,dict)
                            or executor.recovery_only is not True or executor.recovery_pending is not True
                            or mcp.status.status!='ready'):
                        raise MCPToolError('Original pending close has no owned-UI scope')
                    fields=('workspace_id','revision_id','session_id','pid','hwnd','project_path')
                    if (any(executor.target.get(k)!=anchor.get(k) for k in fields)
                            or executor.expected_process_create_time!=anchor['process_create_time']):
                        raise MCPToolError('Original pending close target changed')
                    key=_native_workspace_recovery_state_key(conversation_id,executor.target['workspace_id'],executor.target['revision_id'])
                    def exact_state():
                        state=store.get_host_state(key)
                        return (isinstance(state,dict) and state.get('conversation_id')==conversation_id
                            and state.get('phase')=='close_pending' and identity['source_run_id']==run_id
                            and all(state.get(k)==v for k,v in identity.items()))
                    def current_scope():
                        current=mcp.agent_session_identity_snapshot
                        return (recovery_workspace_only_binding(current,anchor)
                            or isinstance(current,dict) and all(current.get(k)==executor.target[k] for k in fields)
                            and current.get('project_hash')==identity['saved_sha256']
                            and current.get('working_sha256')==identity['saved_sha256'])
                    if not exact_state() or not current_scope():
                        raise MCPToolError('Original pending close operation/source scope changed')
                    if require_live_frame:
                        observed=await mcp.call_read_tool('xg5000.instances',{})
                        instances=observed.get('instances') if isinstance(observed,dict) else None
                        candidates=([item for item in instances if isinstance(item,dict)
                            and (item.get('pid')==anchor['pid'] or item.get('hwnd')==anchor['hwnd'])]
                            if isinstance(instances,list) else [])
                        if (len(candidates)!=1 or candidates[0].get('visible') is not True
                                or any(type(candidates[0].get(k)) is not int for k in ('pid','hwnd'))
                                or type(candidates[0].get('process_create_time')) not in {int,float}
                                or any(candidates[0].get(k)!=anchor[k] for k in ('pid','hwnd','process_create_time'))):
                            raise MCPToolError('Original pending close frame/lifetime was not freshly observed')
                    workspace=_local_workspace_v1(active_workspace)
                    checked=_local_workspace_v1(await mcp.call_workspace_tool('workspace.status',{'workspace_id':executor.target['workspace_id']}))
                    saved=saved_project_observation(Path(workspace.working_copy.path))
                    if (_workspace_resume_identity(checked)!=_workspace_resume_identity(workspace)
                            or any(getattr(workspace,k)!=executor.target[k] for k in ('workspace_id','revision_id'))
                            or _normalized_workspace_path(workspace.working_copy.path)!=_normalized_workspace_path(executor.target['project_path'])
                            or checked.source.original_state!='unchanged'
                            or checked.source.original_current_sha256!=checked.source.sha256
                            or checked.working_copy.current_sha256!=identity['saved_sha256']
                            or saved.get('stable_during_read') is not True or saved.get('sha256')!=identity['saved_sha256']
                            or not exact_state() or not current_scope()):
                        raise MCPToolError('Original pending close workspace/saved identity changed')
                    return deepcopy(identity)

                def current_workspace_recovery(executor, *, rebound=False):
                    state=store.get_host_state(_native_workspace_recovery_state_key(conversation_id,
                        executor.target['workspace_id'],executor.target['revision_id']))
                    if not isinstance(state,dict) or state.get('conversation_id')!=conversation_id:
                        return None
                    phase=state.get('phase')
                    if state.get('source_run_id')!=run_id:
                        # A completed recovery can survive an exact checkpoint
                        # resume. Reuse its current observation only; do not
                        # transfer or rewrite the prior operation/history.
                        recovery_source=(resume_source or (request_message.metadata.get('workspace_recovery_source_run_id')
                            if request_message is not None else None))
                        if (phase!='rebound' or native_checkpoint is None or prior_run is None
                                or recovery_source!=prior_run.id
                                or not recovery_source_run_matches(prior_run.id,state.get('source_run_id'),conversation_id)):
                            return None
                    if phase not in {'save_pending','save_terminal','saved','close_pending','closed','launch_pending','launch_terminal','rebound'}:
                        return None
                    current=mcp.agent_session_identity_snapshot
                    pending_projection=(phase in {'close_pending','closed','launch_pending','launch_terminal'}
                        and executor.recovery_only and executor.recovery_pending
                        and isinstance(pending_recovery_projection_identity,dict))
                    file_only=(pending_projection and recovery_workspace_only_binding(current,state['binding']))
                    identity=(state.get('new_binding') if phase=='rebound' else state.get('current_binding') or state.get('binding'))
                    physical=('workspace_id','revision_id','pid','hwnd','project_path')
                    if pending_projection:
                        if (not isinstance(pending_recovery_projection_identity,dict)
                                or any(state.get(k)!=v for k,v in pending_recovery_projection_identity.items())):
                            return None
                        original=state['binding']
                        if any(original.get(k)!=executor.target.get(k) for k in ('pid','hwnd')) or original.get('process_create_time')!=executor.expected_process_create_time:
                            if (any(original.get(k)!=executor.target.get(k) for k in ('workspace_id','revision_id'))
                                    or _normalized_workspace_path(original.get('project_path'))!=_normalized_workspace_path(executor.target.get('project_path'))
                                    or not _pending_native_recovery_saved_matches(original,executor.target['workspace_id'],
                                        executor.target['revision_id'],state.get('saved_sha256'))):
                                return None
                            return {'schema_version':'1','provenance':'host_current_recovery_state/v1',
                                'operation_id':state['operation_id'],'phase':phase,
                                'recovery_only':True,'native_binding_available':False}
                        if phase=='close_pending' or file_only:
                            # Reuse the producer's same-physical/saved proof. An
                            # exact file-only view keeps its original anchor.
                            identity=_pending_native_recovery_target_anchor(state,
                                executor.target['workspace_id'],executor.target['revision_id'])
                        elif not _pending_native_recovery_saved_matches(state['binding'],
                                executor.target['workspace_id'],executor.target['revision_id'],state.get('saved_sha256')):
                            return None
                        original=state.get('binding')
                        if (not isinstance(identity,dict) or not isinstance(original,dict)
                                or any(identity.get(k)!=original.get(k) for k in physical)
                                or identity.get('process_create_time')!=original.get('process_create_time')):
                            return None
                    fields=physical if file_only else (*physical,'session_id')
                    if (pending_projection and not file_only and current is not None
                            and (not isinstance(current,dict) or any(current.get(k)!=executor.target.get(k) for k in fields))):
                        return None
                    if (not isinstance(identity,dict) or any(identity.get(k)!=executor.target.get(k) for k in fields)
                            or identity.get('process_create_time')!=executor.expected_process_create_time
                            or not isinstance(state.get('operation_id'),str) or not state['operation_id'].strip()):
                        return None
                    available=(not file_only and isinstance(current,dict) and all(current.get(k)==executor.target.get(k) for k in fields)
                        and _engineering_session_process_create_time(active_engineering_session)==executor.expected_process_create_time
                        and (_engineering_session_identity_verified(active_engineering_session)
                             or _native_ld_edit_ready(active_engineering_session,active_workspace)))
                    if phase=='rebound' and not available:
                        return None
                    return {'schema_version':'1','provenance':'host_current_recovery_state/v1',
                        'operation_id':state['operation_id'],'phase':phase,
                        'recovery_only':True if file_only else False if rebound else executor.recovery_only,'native_binding_available':bool(available)}

                def admitted_native_workspace_generation(executor):
                    """Admission identity only; no foreground or continuing-liveness proof.

                    A normal verified reconnect can replace a failed recovery's
                    old frame without changing that operation or its unknown
                    outcomes. Bind the current generation to existing Host proof.
                    """
                    if executor.recovery_only is not False or executor.recovery_pending is not False:
                        return None
                    current=mcp.agent_session_identity_snapshot
                    retained=retained_native_workspace_binding(active_workspace)
                    session=active_engineering_session
                    if (mcp.status.status!='ready' or not isinstance(current,dict)
                            or not isinstance(retained,dict) or not isinstance(session,dict)):
                        return None
                    anchor=retained.get('session')
                    target=executor.target
                    fields=('workspace_id','revision_id','session_id','pid','hwnd','project_path')
                    if (not isinstance(anchor,dict) or not isinstance(target,dict)
                            or any(not isinstance(target.get(k),str) or not target[k].strip()
                                   for k in ('workspace_id','revision_id','session_id','project_path'))
                            or any(type(target.get(k)) is not int or target[k]<=0 for k in ('pid','hwnd'))
                            or any(current.get(k)!=target[k] for k in fields)
                            or any(session.get(k)!=target[k] for k in ('session_id','pid','hwnd','project_path'))
                            or any(anchor.get(k)!=target[k] for k in ('pid','hwnd','project_path'))):
                        return None
                    if anchor.get('session_id')!=target['session_id']:
                        # Normal same-live revalidation preserves the original
                        # proven anchor while renewing only the Host session.
                        metadata=session.get('metadata')
                        evidence=metadata.get('native_ld_host_revalidation') if isinstance(metadata,dict) else None
                        if (not isinstance(evidence,dict) or evidence.get('binding')!=retained
                                or not _host_native_binding_ready(session,active_workspace)):
                            return None
                    created=executor.expected_process_create_time
                    if (type(created) not in {int,float} or not math.isfinite(created) or created<=0
                            or _engineering_session_process_create_time(session)!=created
                            or _engineering_session_process_create_time(anchor)!=created):
                        return None
                    try:
                        workspace=_local_workspace_v1(active_workspace)
                        expected=workspace_agent_binding(workspace)
                        if (any(expected.get(k)!=target[k] or retained.get(k)!=target[k]
                                for k in ('workspace_id','revision_id'))
                                or any(current.get(k)!=value for k,value in expected.items())
                                or workspace.source.original_state!='unchanged'
                                or workspace.source.original_current_sha256!=workspace.source.sha256
                                or _normalized_workspace_path(workspace.working_copy.path)
                                   !=_normalized_workspace_path(target['project_path'])
                                or any(value.get('project_hash')!=workspace.working_copy.current_sha256
                                       for value in (session,anchor,current))
                                or not all(_engineering_session_identity_verified(value)
                                           or _native_ld_edit_ready(value,active_workspace)
                                           for value in (session,anchor))):
                            return None
                        return native_admission_generation(target, created)
                    except (KeyError,OSError,RuntimeError,TypeError,ValueError):
                        return None

                async def pending_recovery_idle(executor, ui_identity):
                    identity=await validate_pending_recovery_owned_ui(executor)
                    observed=await executor._execute('native_editor',{'action':'observe'},persist_observation=False)
                    scene=observed.get('observation',{})
                    if (observed.get('ok') is not True or observed.get('read_only') is not True
                            or observed.get('action_attempted') is not False or observed.get('error') or observed.get('failure')
                            or observed.get('input_state_unknown') is True or not isinstance(scene,dict)
                            or scene.get('owned_modals') != []
                            or scene.get('observation_phase') is not None or not isinstance(scene.get('controls'),list)
                            or not isinstance(scene.get('native_status'),dict) or scene['native_status'].get('error')):
                        return None
                    import ctypes
                    from ctypes import wintypes
                    user=ctypes.WinDLL('user32',use_last_error=True)
                    for name in ('IsWindowVisible','IsWindowEnabled'):
                        function=getattr(user,name);function.argtypes=[wintypes.HWND];function.restype=wintypes.BOOL
                    if (not user.IsWindowVisible(identity['binding']['hwnd'])
                            or not user.IsWindowEnabled(identity['binding']['hwnd'])
                            or user.IsWindowVisible(ui_identity['hwnd'])):
                        return None
                    if await validate_pending_recovery_owned_ui(executor) != identity:
                        raise MCPToolError('Pending recovery idle frame changed')
                    return observed

                async def wait_workspace_recovery(executor, previous_result, *, prior_ui_identity=None, defer_only=False):
                    """Continue this operation only after original-owner evidence changes."""
                    from .native_recovery_wait import (
                        RecoveryMaterialChange,
                        RecoveryWaitScopeChanged,
                        completed_owned_cancel,
                        recovery_continuation,
                        response_input_unknown,
                        restored_cancel_receipt,
                        undelivered_close_reservation,
                        wait_for_recovery_change,
                    )
                    from .native_workspace_host import native_recovery_target_retired
                    key=_native_workspace_recovery_state_key(conversation_id,
                        executor.target['workspace_id'],executor.target['revision_id'])
                    state=store.get_host_state(key)
                    if not isinstance(state,dict) or state.get('phase')!='close_pending':
                        return previous_result
                    if pending_recovery_ui_identity is None and current_workspace_recovery(executor) is not None:
                        # A replacement candidate has no original-frame modal
                        # authority. Its real recover callback owns retirement.
                        return previous_result
                    # No different operation can be adopted by an idle waiter.
                    identity={**deepcopy(pending_recovery_ui_identity or {}), 'phase':'close_pending'}
                    if (identity.get('operation_id')!=state.get('operation_id')
                            or identity.get('source_run_id')!=run_id
                            or not isinstance(identity.get('binding'),dict)):
                        raise MCPToolError('Pending recovery has no exact wait owner')
                    original=deepcopy(identity['binding'])
                    barrier_scope={k:deepcopy(identity[k]) for k in ('operation_id','binding','saved_sha256')}
                    if defer_only:
                        if not isinstance(prior_ui_identity,dict):
                            raise MCPToolError('Pending UI response has no original issued identity')
                        after_response=await validate_pending_recovery_owned_ui(executor,require_live_frame=False)
                        if {**after_response,'phase':'close_pending'}!=identity:
                            raise MCPToolError('Pending UI response wait owner changed')
                        # Return the original response unchanged, including its
                        # unknown. The next real recovery callback owns the wait.
                        state['owned_ui_response_wait']={'scope':barrier_scope,
                            'ui_identity':deepcopy(prior_ui_identity),
                            'input_state_unknown':response_input_unknown(previous_result),
                            'completed_cancel':completed_owned_cancel(previous_result,prior_ui_identity),
                            'response_result':deepcopy(previous_result)}
                        prior_reconsideration=state.get('close_reconsideration')
                        if (isinstance(prior_reconsideration,dict) and prior_reconsideration.get('consumed') is True
                                and prior_reconsideration.get('ui_identity')!=prior_ui_identity):
                            state.pop('close_reconsideration')
                        store.set_host_state(key,state)
                        result={**previous_result,
                            'current_workspace_recovery':current_workspace_recovery(executor),
                            'current_workspace_continuation':recovery_continuation(identity,state='physical_wait')}
                        executor.record_raw('recover_workspace',{},result)
                        return result
                    barrier=state.get('owned_ui_response_wait')
                    if barrier is not None:
                        if (not isinstance(barrier,dict) or barrier.get('scope')!=barrier_scope
                                or not isinstance(barrier.get('ui_identity'),dict)
                                or type(barrier.get('input_state_unknown')) is not bool):
                            raise MCPToolError('Pending UI response belongs to another recovery scope')
                        prior_ui_identity=deepcopy(barrier['ui_identity'])
                        if not barrier['input_state_unknown'] and 'completed_cancel' not in barrier:
                            original_run=prior_ui_identity.get('pending_recovery_identity',{}).get('source_run_id')
                            if original_run in [state['source_run_id'],*state.get('resumed_source_runs',[])]:
                                header=store.get_host_state(f'native_ld_receipts:{original_run}')
                                if (isinstance(header,dict) and all(header.get('binding',{}).get(k)==original[k]
                                        for k in ('workspace_id','revision_id','project_path'))):
                                    restored=restored_cancel_receipt(store.recent_native_receipts(original_run),
                                        prior_ui_identity,barrier_scope)
                                    if restored is not None:
                                        restored_response,receipt_ref=restored
                                        barrier.update(completed_cancel=True,response_result=restored_response,
                                            response_receipt_ref=receipt_ref)
                                        store.set_host_state(key,state)
                    prior_ui_unresolved=(isinstance(barrier,dict) and barrier['input_state_unknown'])
                    unresolved_ui_absent=False
                    async def validate_scope():
                        actual=await validate_pending_recovery_owned_ui(executor,require_live_frame=False)
                        return {**actual,'phase':'close_pending'}
                    async def observe_change():
                        nonlocal unresolved_ui_absent
                        if native_recovery_target_retired(original,original['process_create_time']):
                            return RecoveryMaterialChange('original_retired')
                        reservation=state.get('close_reconsideration')
                        undelivered=undelivered_close_reservation(state,barrier_scope)
                        ready_reservation=(isinstance(reservation,dict) and reservation.get('scope')==barrier_scope
                            and reservation.get('consumed') is False and reservation.get('ui_identity')==prior_ui_identity
                            and completed_owned_cancel(reservation.get('response_result',{}),prior_ui_identity))
                        if (isinstance(barrier,dict) and barrier.get('completed_cancel') is True
                                and (not isinstance(reservation,dict) or undelivered is not None or ready_reservation)):
                            idle=await pending_recovery_idle(executor,prior_ui_identity)
                            if idle is not None:
                                if isinstance(reservation,dict):
                                    if undelivered is not None:
                                        reservation['consumed']=False
                                        reservation.setdefault('released_failure_digests',[]).append(undelivered)
                                else:
                                    state['close_reconsideration']={'scope':barrier_scope,'consumed':False,
                                        'ui_identity':deepcopy(prior_ui_identity),
                                        'response_result':deepcopy(barrier['response_result'])}
                                store.set_host_state(key,state)
                                return RecoveryMaterialChange('owned_modal_completed',idle)
                        try:
                            observed=await executor._execute('native_editor',{'action':'observe'},
                                persist_observation=False)
                        except Exception:
                            # A failed read is not retirement. Only the existing
                            # exact original lifetime/main predicate can prove it.
                            if native_recovery_target_retired(original,original['process_create_time']):
                                return RecoveryMaterialChange('original_retired')
                            raise
                        if native_recovery_target_retired(original,original['process_create_time']):
                            return RecoveryMaterialChange('original_retired')
                        scene=observed.get('observation',{}) if isinstance(observed,dict) else {}
                        modals=scene.get('owned_modals') if isinstance(scene,dict) else None
                        if (not isinstance(observed,dict) or observed.get('ok') is not True
                                or observed.get('error') or observed.get('failure')
                                or observed.get('input_state_unknown') is True):
                            # Backend failures keep their existing raw record and
                            # stop observation. They are never retirement evidence.
                            raise MCPToolError('Pending recovery observation did not complete')
                        if prior_ui_unresolved and not unresolved_ui_absent:
                            if not isinstance(modals,list):
                                return None
                            # Public results deliberately omit HWND. The current
                            # scene's Host-private issued ledger owns that identity.
                            current_ui=[executor.owned_ui.get(item.get('ui_ref'))
                                if isinstance(item,dict) else None for item in modals]
                            if (any(not isinstance(item,dict) for item in current_ui)
                                    or any(item.get('hwnd')==prior_ui_identity.get('hwnd') for item in current_ui)):
                                return None  # A newly minted ref never replays this unknown response.
                            unresolved_ui_absent=True
                        issued=[item for item in modals or [] if isinstance(item,dict)
                            and item.get('ui_kind')=='modal' and item.get('enabled') is True
                            and item.get('ui_ref') in executor.owned_ui
                            and executor.owned_ui[item['ui_ref']].get('pending_recovery_identity')
                                ==pending_recovery_ui_identity
                            and item.get('allowed_responses')==['cancel']]
                        if (observed.get('ok') is True and observed.get('read_only') is True
                                and observed.get('action_attempted') is False and issued):
                            return RecoveryMaterialChange('owned_modal',observed)
                        return None
                    # Intermediate state is durable diagnostic truth, not a
                    # completed model tool result and never native authority.
                    waiting={**previous_result,
                        'current_workspace_continuation':recovery_continuation(identity,state='physical_wait')}
                    executor.record_raw('recover_workspace',{},waiting)
                    try:
                        change=await wait_for_recovery_change(identity,
                            validate_scope=validate_scope,observe_change=observe_change)
                    except RecoveryWaitScopeChanged as error:
                        raise MCPToolError('Pending recovery wait scope changed') from error
                    if change.kind in {'owned_modal','owned_modal_completed'}:
                        # Idle duplicates remain ephemeral. Persist exactly the
                        # real capability-bearing observation that wakes the model.
                        executor.record_raw('native_editor',{'action':'observe'},change.observation)
                        result={**previous_result,
                            'current_workspace_recovery':current_workspace_recovery(executor),
                            'pending_owned_ui':change.observation,
                            'current_workspace_continuation':recovery_continuation(
                                identity,state='ready',route_tool_name=('respond_owned_ui'
                                    if change.kind=='owned_modal' else 'recover_workspace'))}
                    else:
                        # The existing state machine observes closed and may
                        # continue its original reopen once. It never repeats close.
                        result=await native_checkpoint_boundary(executor._execute('recover_workspace',{}))
                    executor.record_raw('recover_workspace',{},result)
                    return result

                async def recover_native_workspace(executor: Any) -> dict[str, Any]:
                    result=await recover_native_workspace_operation(executor)
                    current=current_workspace_recovery(executor,rebound=result.get('ok') is True and result.get('rebound') is True)
                    # Managed recovery can publish or clear the native binding
                    # without a Web action. Notify the existing typed-status
                    # consumer once after this joined operation has settled.
                    store.append_event(event_type="host.status", conversation_id=conversation_id, run_id=run_id,
                        payload=current_status_v1().model_dump(mode="json", by_alias=True))
                    generation = (admitted_native_workspace_generation(executor)
                        if result.get('ok') is True and result.get('rebound') is True
                        and isinstance(current, dict) and current.get('phase') == 'rebound'
                        and current.get('operation_id') == result.get('operation_id')
                        and current.get('native_binding_available') is True
                        and current.get('recovery_only') is False else None)
                    return {**result,**({'current_workspace_recovery':current} if current is not None else {}),
                        **({'native_binding_generation':generation} if generation is not None else {})}

                async def recover_native_workspace_operation(executor: Any) -> dict[str, Any]:
                    """Same-run serialized manager operation; external active-run guard stays intact."""
                    nonlocal target, active_workspace, active_engineering_session
                    from controlstudio_contracts.native_failure import (
                        build_native_failure,
                        validate_native_failure,
                    )

                    from .native_workspace_host import native_recovery_target_retired
                    old=deepcopy(executor.target)
                    key=_native_workspace_recovery_state_key(conversation_id,old['workspace_id'],old['revision_id'])
                    state=store.get_host_state(key)
                    operation_selected=False
                    prior_generation_operation=None
                    anchor={**{k:old[k] for k in ('workspace_id','revision_id','session_id','pid','hwnd','project_path')},
                            'process_create_time':executor.expected_process_create_time}
                    def persist():
                        store.set_host_state(key,state)
                        publish_workspace_recovery_scope(executor,state)
                    def pending(reason):
                        return {'ok':False,'rebound':False,'phase':state['phase'],
                            'error':{'kind':'workspace_recovery_pending','message':reason},
                            'input_state_unknown':True,'automatic_replay':False,
                            'current_request_input_attempted':False,'prior_operation_outcome':'unknown',
                            'next_action':'Observe actual owned UI or the original late-open outcome; never repeat close/open blindly.'}
                    def same_generation(left,right):
                        return (all(left.get(k)==right.get(k) for k in ('workspace_id','revision_id','pid','hwnd','project_path'))
                            and left.get('process_create_time')==right.get('process_create_time'))
                    def scoped_generation_binding(value):
                        return (isinstance(value,dict) and set(value)==set(anchor)
                            and all(isinstance(value.get(k),str) and value[k].strip()
                                    for k in ('workspace_id','revision_id','session_id','project_path'))
                            and all(type(value.get(k)) is int and value[k]>0 for k in ('pid','hwnd'))
                            and type(value.get('process_create_time')) in {int,float}
                            and math.isfinite(value['process_create_time']) and value['process_create_time']>0
                            and all(value[k]==anchor[k] for k in ('workspace_id','revision_id'))
                            and _normalized_workspace_path(value['project_path'])==_normalized_workspace_path(anchor['project_path']))
                    def archive():
                        previous=deepcopy(state)
                        history=previous.pop('completed_operations',[])
                        if not isinstance(history,list) or any(not isinstance(item,dict) or 'completed_operations' in item for item in history):
                            raise MCPToolError('Workspace recovery operation history is invalid')
                        return [*history,previous]
                    def known_no_input(failure):
                        return (isinstance(failure,dict) and failure.get('input_state_unknown') is False
                                and (failure.get('dispatch_state') in {'not_attempted','not_delivered'}
                                     or failure.get('action_attempted') is False))
                    def launch_retired(failure):
                        import math
                        proof=failure.get('details',{}).get('launch_retirement') if isinstance(failure,dict) else None
                        return (isinstance(proof,dict) and proof.get('schema')=='controlstudio-launch-retirement/v1'
                            and proof.get('terminal') is True and proof.get('retired_verified') is True
                            and proof.get('launched_identity_observed') is True
                            and type(proof.get('launched_pid')) is int and proof['launched_pid']>0
                            and set(proof)=={'schema','launched_identity_observed','launched_pid','process_create_time','terminal',
                                'retired_verified','observation_method','retirement_state','cleanup_effect','automatic_replay'}
                            and type(proof.get('process_create_time')) in {int,float}
                            and math.isfinite(proof['process_create_time']) and proof['process_create_time']>0
                            and proof.get('retirement_state') in {'absent','different_lifetime'}
                            and proof.get('observation_method')=='psutil_exact_identity_after_existing_cleanup/v1'
                            and proof.get('automatic_replay') is False)
                    try:
                        if (type(anchor['process_create_time']) not in {int,float}
                                or not math.isfinite(anchor['process_create_time']) or anchor['process_create_time']<=0):
                            raise MCPToolError('Workspace recovery requires an exact finite process lifetime')
                        # Operation outcome and native input ownership are separate.
                        # A new independently admitted generation can retire the
                        # previous owner's authority without settling its history.
                        if state is not None and (not isinstance(state,dict) or not scoped_generation_binding(state.get('binding'))
                                or state.get('conversation_id')!=conversation_id
                                or state.get('phase') not in {'save_pending','save_terminal','saved','close_pending','closed','launch_pending','launch_terminal','rebound'}
                                or not isinstance(state.get('operation_id'),str) or not state['operation_id'].strip()
                                or state.get('phase')=='rebound' and not scoped_generation_binding(state.get('new_binding'))):
                            raise MCPToolError('Workspace recovery state is invalid')
                        current=mcp.agent_session_identity_snapshot
                        pending_phase=state is not None and state.get('phase') in {'close_pending','closed','launch_pending','launch_terminal'}
                        # The effective owner follows the completed rebound, not
                        # the original frame. This handoff applies before every
                        # phase continuation; a new session alone never qualifies.
                        previous=(state.get('new_binding') if state is not None and state['phase']=='rebound'
                            else state['binding'] if state is not None else None)
                        foreign_generation=(state is not None
                            and any(previous[k]!=anchor[k] for k in ('pid','hwnd','process_create_time')))
                        if (state is not None and state['phase']!='rebound' and state['binding']!=anchor
                                and same_generation(state['binding'],anchor)
                                and isinstance(current,dict) and current.get('session_id')==old.get('session_id')
                                and _engineering_session_process_create_time(active_engineering_session)==anchor['process_create_time']
                                and _native_ld_edit_ready(active_engineering_session,active_workspace)):
                            state['current_binding']=deepcopy(anchor);persist()
                        exact_binding=(state is None or state['binding']==anchor
                            or state.get('current_binding')==anchor and same_generation(state['binding'],anchor))
                        if pending_phase and not foreign_generation and (not exact_binding or state.get('source_run_id')!=run_id):
                            # A cold resume is authorized only by the existing
                            # exact native checkpoint source/run validation.
                            if (native_checkpoint is None or prior_run is None
                                    or (resume_source!=prior_run.id and request_message.metadata.get('workspace_recovery_source_run_id')!=prior_run.id)
                                    or (state.get('source_run_id')!=prior_run.id and not recovery_source_run_matches(prior_run.id,state.get('source_run_id'),conversation_id))
                                    or not exact_binding):
                                raise MCPToolError('Pending recovery has no exact same-run or resumed-run anchor')
                            state['source_run_id']=run_id;persist()
                        if not pending_phase or foreign_generation:
                            executor._check_target()
                        elif (current is not None and not recovery_workspace_only_binding(current,anchor)
                                and any(current.get(k)!=old.get(k) for k in ('workspace_id','revision_id','session_id','pid','hwnd','project_path'))):
                            raise MCPToolError('Pending recovery cannot adopt another workspace or working target')
                        workspace=_local_workspace_v1(active_workspace)
                        checked=_local_workspace_v1(await mcp.call_workspace_tool('workspace.status',{'workspace_id':old['workspace_id']}))
                        if (_workspace_resume_stable_identity(checked)!=_workspace_resume_stable_identity(workspace)
                                or checked.workspace_id!=old['workspace_id'] or checked.revision_id!=old['revision_id']
                                or checked.source.original_state!='unchanged'
                                or checked.source.original_current_sha256!=checked.source.sha256):
                            raise MCPToolError('Workspace recovery must preserve the exact source and working identity')
                        if state is None or isinstance(state, dict) and state.get('phase') == 'rebound':
                            healthy = await _healthy_current_recovery(executor, state, anchor, workspace,
                                admitted_native_workspace_generation(executor), revalidate_native_workspace_binding,
                                admitted_native_workspace_generation)
                            if healthy is not None:
                                return healthy
                        history=[]
                        retained_candidate_session=None
                        if pending_phase and foreign_generation and state.get('source_run_id')==run_id:
                            if not native_recovery_target_retired(previous,previous['process_create_time']):
                                return pending('The original recovery frame has not retired; the replacement remains a candidate only.')
                            if not _pending_native_recovery_saved_matches(previous,old['workspace_id'],old['revision_id'],state['saved_sha256']):
                                raise MCPToolError('Pending recovery saved identity changed before candidate revalidation')
                            retained_candidate_session=await revalidate_native_workspace_binding(workspace)
                            created=(_engineering_session_process_create_time(retained_candidate_session)
                                if isinstance(retained_candidate_session,dict) else None)
                            if (not isinstance(retained_candidate_session,dict) or created!=anchor['process_create_time']
                                    or any(retained_candidate_session.get(k)!=old[k] for k in ('pid','hwnd','project_path'))
                                    or retained_candidate_session.get('project_hash')!=state['saved_sha256']
                                    or not (_engineering_session_identity_verified(retained_candidate_session)
                                        or _native_ld_edit_ready(retained_candidate_session,active_workspace))):
                                raise MCPToolError('Pending recovery replacement did not freshly revalidate its exact saved binding')
                            # Only the original lifetime is closed. Keep its
                            # operation and unknown history; adopt through the
                            # existing retained-binding rebound below.
                            state['phase']='closed';persist()
                        elif foreign_generation:
                            if pending_phase and (state['phase']!='launch_terminal'
                                    or not launch_retired(state.get('terminal_failure'))):
                                raise MCPToolError('Pending recovery has no exact same-run owner')
                            if not native_recovery_target_retired(previous,previous['process_create_time']):
                                raise MCPToolError('Previous recovery owner has not retired its exact process/main')
                            # Reuse Host binding admission, but do not promote
                            # its cached proof to continuing liveness. Observe
                            # this exact current frame/lifetime and saved bytes
                            # before choosing a NEW explicit operation.
                            generation=admitted_native_workspace_generation(executor)
                            if generation is None:
                                raise MCPToolError('New recovery requires an independent current Host generation')
                            observed=await mcp.call_read_tool('xg5000.instances',{})
                            instances=observed.get('instances') if isinstance(observed,dict) else None
                            candidates=([item for item in instances if isinstance(item,dict)
                                and (item.get('pid')==anchor['pid'] or item.get('hwnd')==anchor['hwnd'])]
                                if isinstance(instances,list) else [])
                            if (len(candidates)!=1
                                    or any(type(candidates[0].get(k)) is not int for k in ('pid','hwnd'))
                                    or type(candidates[0].get('process_create_time')) not in {int,float}
                                    or any(candidates[0].get(k)!=anchor[k]
                                    for k in ('pid','hwnd','process_create_time')) or candidates[0].get('visible') is not True):
                                raise MCPToolError('New recovery current frame/lifetime was not exactly observed')
                            saved=saved_project_observation(Path(workspace.working_copy.path))
                            executor._check_target()
                            if (saved.get('stable_during_read') is not True
                                    or saved.get('sha256')!=checked.working_copy.current_sha256
                                    or admitted_native_workspace_generation(executor)!=generation):
                                raise MCPToolError('New recovery current saved binding changed during observation')
                            # Additive private durable evidence, ignored by all
                            # current-authority projections. Preserve the ENTIRE
                            # old operation (including unknown and completed
                            # history); never archive unknown as completed.
                            prior_generation_operation={'input_authority_retired_by':generation,
                                'retired_owner':deepcopy(previous),'owner_retirement_observed':True,
                                'operation':deepcopy(state)}
                            state=None
                        operation_selected=True
                        if (state is not None and state['phase']=='saved' and exact_binding
                                and state['saved_sha256']!=checked.working_copy.current_sha256
                                and not state.get('close_result') and not state.get('close_attempt_history')
                                and not state.get('close_reconsideration')):
                            rejection=state.get('close_rejection')
                            if isinstance(rejection,dict) and rejection.get('schema')=='controlstudio-native-failure/v1':
                                rejected_close=validate_native_failure(rejection)
                                rejected_details=rejected_close.get('details',{})
                                if (known_no_input(rejected_details)
                                        and rejected_details.get('action_attempted') is False
                                        and rejected_details.get('source_closed') is False):
                                    # A new explicit request owns a fresh save barrier.
                                    # Keep the prior known rejection and its saved bytes
                                    # exact; pending or ambiguous close never enters here.
                                    history=archive();state=None
                        if state is not None and state['phase'] in {'rebound','save_terminal'}:
                            if state['phase']=='rebound' and state.get('new_binding')!=anchor:
                                # Completed history grants no control over a new target.
                                # This explicit request can start a NEW operation only
                                # under the independent, current Host binding admission.
                                if admitted_native_workspace_generation(executor) is None:
                                    raise MCPToolError('Completed recovery cannot authorize another current target')
                            elif state['phase']=='save_terminal' and not exact_binding:
                                raise MCPToolError('Completed recovery cannot authorize another current target')
                            history=archive();state=None
                        if state is not None and state['phase']=='launch_terminal':
                            if not launch_retired(state.get('terminal_failure')):
                                return pending('The original launch has no exact terminal retirement proof.')
                            # This is a NEW explicit model request, after the
                            # preceding request recorded terminal failure.
                            history=archive()
                            state={'binding':anchor,'conversation_id':conversation_id,'saved_sha256':state['saved_sha256'],'phase':'closed',
                                'source_run_id':run_id,'operation_id':str(uuid.uuid4()),'completed_operations':history}
                            persist()
                        if state is None:
                            # Save/read are the existing bound primitives. An
                            # ACK alone is never proof of a stably saved file.
                            state={'binding':anchor,'conversation_id':conversation_id,'saved_sha256':workspace.working_copy.current_sha256,
                                   'phase':'save_pending','source_run_id':run_id,'operation_id':str(uuid.uuid4()),
                                   'completed_operations':history}
                            if prior_generation_operation is not None:
                                state['prior_generation_operation']=prior_generation_operation
                            persist()
                            save=await executor._execute('save_project',{})
                            state['save_result']=save;persist()
                            if save.get('ok') is not True or save.get('error') or save.get('failure'):
                                # An unknown save is retained and never repeated.
                                # Only an explicit known no-input result permits
                                # a new save operation on a subsequent call.
                                if known_no_input(save):state['phase']='save_terminal';persist()
                                return {'ok':False,'phase':'save_pending','rebound':False,'save_result':save,'automatic_replay':False}
                        if state['phase']=='save_pending':
                            # The attempted save can change the mutable version even
                            # when its UI receipt fails. Observe it afresh; never replay.
                            checked=_local_workspace_v1(await mcp.call_workspace_tool('workspace.status',{'workspace_id':old['workspace_id']}))
                            # Read stable disk without repeating an unknown save.
                            # The normal close primitive independently refuses
                            # dirty discard; disk read alone is not IDE equality.
                            saved=await executor._execute('read_saved_project',{})
                            if saved.get('stable_during_read') is not True:
                                raise MCPToolError('Workspace recovery saved file was not stable')
                            workspace=_local_workspace_v1(active_workspace)
                            if (_workspace_resume_stable_identity(checked)!=_workspace_resume_stable_identity(workspace)
                                    or checked.working_copy.current_sha256!=saved.get('sha256')
                                    or workspace.working_copy.current_sha256!=saved.get('sha256')):
                                raise MCPToolError('Workspace recovery saved lineage changed')
                            state.update(saved_sha256=saved['sha256'],phase='saved',saved_result=saved)
                            persist()
                        if state['phase']!='saved' and state['saved_sha256']!=checked.working_copy.current_sha256:
                            # workspace.status was observed before an actual
                            # save; re-read after the existing lineage update.
                            checked=_local_workspace_v1(await mcp.call_workspace_tool('workspace.status',{'workspace_id':old['workspace_id']}))
                            if checked.working_copy.current_sha256!=state['saved_sha256']:
                                raise MCPToolError('Saved working copy changed during recovery')
                        if (retained_candidate_session is None and not (state['binding']==anchor
                                or state.get('current_binding')==anchor and same_generation(state['binding'],anchor))):
                            return pending('The recorded recovery belongs to another generation; it grants no current control authority.')
                        reconsidered_close=False
                        if state['phase']=='close_pending':
                            from .native_recovery_wait import consume_close_reconsideration
                            proof=state.get('close_reconsideration')
                            scope={k:deepcopy(state[k]) for k in ('operation_id','binding','saved_sha256')}
                            if (isinstance(proof,dict) and proof.get('scope')==scope and proof.get('consumed') is False
                                    and await pending_recovery_idle(executor,proof['ui_identity']) is not None):
                                reconsidered_close=consume_close_reconsideration(state,scope)
                                if reconsidered_close:
                                    state.setdefault('close_attempt_history',[]).append({
                                        'close_result':deepcopy(state.get('close_result')),
                                        'failure':deepcopy(state.get('last_failure')),
                                        'reconsideration':deepcopy(proof)})
                                    persist()
                        if state['phase']=='saved' or reconsidered_close:
                            saved=(saved_project_observation(Path(workspace.working_copy.path)) if reconsidered_close
                                   else await executor._execute('read_saved_project',{}))
                            if (saved.get('stable_during_read') is not True
                                    or saved.get('sha256')!=state['saved_sha256']):
                                raise MCPToolError('Saved working copy changed before normal close')
                            workspace=_local_workspace_v1(active_workspace)
                            state['phase']='close_pending';persist()
                            close_attempted=False
                            def mark_close():
                                nonlocal close_attempted
                                close_attempted=True
                            close=await _close_existing_workspace_for_fresh_reopen(workspace,deepcopy(active_engineering_session),
                                timeout_seconds=30.0,return_recovery_outcome=True,before_dispatch=mark_close)
                            state['close_result']=deepcopy(close);persist()
                            if close.get('source_closed') is False:
                                state['phase']='saved';persist()
                                return {'ok':False,'rebound':False,'phase':'saved','close_result':close,
                                    'input_state_unknown':False,'automatic_replay':False,
                                    'next_action':'The normal close preserved unsaved edits. Save or resolve the actual owned UI before a new recover request.'}
                        if state['phase']=='close_pending':
                            if not native_recovery_target_retired(state['binding'],state['binding']['process_create_time']):
                                if current is None:
                                    # Revalidate exactly the original live frame
                                    # read-only; never reopen or repeat close.
                                    reconciled=await _reconcile_late_workspace_open(mcp,workspace,bind_session=bind_engineering_session,
                                        timeout_seconds=3.0,expected_frame=state['binding'])
                                    if reconciled is not None:
                                        _,session=reconciled
                                        new=mcp.agent_session_identity_snapshot
                                        created=_engineering_session_process_create_time(session)
                                        if (not isinstance(new,dict) or created!=state['binding']['process_create_time']
                                                or not same_generation(state['binding'],{**new,'process_create_time':created})
                                                or not _native_ld_edit_ready(session,workspace.model_dump(mode='json'))):
                                            raise MCPToolError('Original pending frame did not revalidate exactly')
                                        target=deepcopy(new);executor.target=deepcopy(new);executor.expected_process_create_time=created
                                        state['current_binding']={**new,'process_create_time':created};persist()
                                        executor.owned_ui.clear();executor.variable_comments.clear();executor.check_results.clear()
                                        return {**pending('The original live frame was freshly revalidated; observe and explicitly respond to its owned modal.'),
                                            'old_target_revalidated':True,'operation_id':state['operation_id']}
                                return pending('The original normal close has not retired its exact process/main; fresh owned modal handling is required.')
                            state['phase']='closed';persist()
                        def record_launch_pending(observed):
                            if all(k in observed for k in ('pid','hwnd','process_create_time')):
                                state['launch_pending_frame']={k:observed[k] for k in ('pid','hwnd','process_create_time')}
                                state['launch_operation_id']=observed['launch_operation_id']
                            if observed.get('state')=='original_retired':
                                state['phase']='launch_terminal'
                                state['terminal_failure']={'details':{'launch_retirement':observed.get('launch_retirement')}}
                            persist()
                        if state['phase']=='closed':
                            # A Host restart loses current readiness, not a separate
                            # retained, owned native frame. Use the same exact live
                            # revalidation as normal Web resume before launching.
                            # Pending close/launch operations never enter this path.
                            reused=(retained_candidate_session if retained_candidate_session is not None
                                else await revalidate_native_workspace_binding(workspace))
                            if reused is not None:
                                session=bind_engineering_session(reused)
                                new=mcp.agent_session_identity_snapshot
                                created=_engineering_session_process_create_time(session)
                                if (not isinstance(new,dict) or created is None or not math.isfinite(created) or created<=0
                                        or any(new.get(k)!=old.get(k) for k in ('workspace_id','revision_id','project_path'))
                                        or any(new.get(k)!=session.get(k) for k in ('session_id','pid','hwnd','project_path'))
                                        or not (_engineering_session_identity_verified(session)
                                            or _native_ld_edit_ready(session,workspace.model_dump(mode='json')))):
                                    raise MCPToolError('Retained workspace recovery did not revalidate its exact native target')
                                target=deepcopy(new);executor.target=deepcopy(new);executor.expected_process_create_time=created
                                state.update(phase='rebound',rebound_kind='retained_native_binding_revalidated',
                                    new_binding={**{k:new[k] for k in ('workspace_id','revision_id','session_id','pid','hwnd','project_path')},
                                                 'process_create_time':created})
                                persist()
                                return {'ok':True,'rebound':True,'phase':'rebound','workspace_id':old['workspace_id'],
                                    'revision_id':old['revision_id'],'saved_sha256':state['saved_sha256'],
                                    'operation_id':state['operation_id'],'retained_target_revalidated':True,
                                    'current_request_input_attempted':False,'launch_attempted':False,
                                    'source_preserved':True,'loaded_authority_inherited':False,
                                    'requires_fresh_program_observation':True,'automatic_replay':False,
                                    'prior_unknown_preserved':True}
                            state['phase']='launch_pending';persist()
                            active_engineering_session=None
                            execution_broker.clear_agent_session()
                            # One controlled launch only. Unknown outcomes keep
                            # launch_pending; later requests reconcile read-only.
                            launch,session=await open_workspace_in_xg5000(workspace,timeout_seconds=120.0,require_controlled_launch=True,
                                binding_wait_identity={'operation_id':state['operation_id'],'source_run_id':state['source_run_id']},
                                binding_wait_record=record_launch_pending)
                        elif state['phase']=='launch_pending':
                            launch,session=await open_workspace_in_xg5000(workspace,timeout_seconds=120.0,require_controlled_launch=True,
                                binding_wait_identity={'operation_id':state['operation_id'],'source_run_id':state['source_run_id'],'reconcile_only':True},
                                expected_pending_frame=state.get('launch_pending_frame'), binding_wait_record=record_launch_pending)
                        else:
                            return pending('The original recovery stage requires actual inspection before another operation.')
                        new=mcp.agent_session_identity_snapshot
                        created=_engineering_session_process_create_time(session)
                        if (not isinstance(new,dict) or created is None or not math.isfinite(created) or created<=anchor['process_create_time']
                                or any(new.get(k)!=old.get(k) for k in ('workspace_id','revision_id','project_path'))
                                or not (_engineering_session_identity_verified(session) or _native_ld_edit_ready(session,workspace.model_dump(mode='json')))):
                            raise MCPToolError('Workspace recovery did not prove a fresh exact working-copy target')
                        target=deepcopy(new);executor.target=deepcopy(new);executor.expected_process_create_time=created
                        state.update(phase='rebound',new_binding={**{k:new[k] for k in ('workspace_id','revision_id','session_id','pid','hwnd','project_path')},'process_create_time':created})
                        persist()
                        return {'ok':True,'rebound':True,'phase':'rebound','workspace_id':old['workspace_id'],
                            'revision_id':old['revision_id'],'saved_sha256':state['saved_sha256'],
                            'operation_id':state['operation_id'],
                            'source_preserved':True,
                            'normal_close_verified':(isinstance(state.get('close_result'),dict)
                                and state['close_result'].get('source_closed') is True),
                            'source_retirement_observed':True,'fresh_target_verified':True,
                            'loaded_authority_inherited':False,'requires_fresh_program_observation':True,
                            'automatic_replay':False,'prior_unknown_preserved':True}
                    except Exception as error:
                        details={'phase':state.get('phase') if isinstance(state,dict) else 'preflight',
                                 'automatic_replay':False,'input_state_unknown':isinstance(state,dict) and state.get('phase') in {'save_pending','close_pending','launch_pending'}}
                        cause=error
                        for _ in range(4):
                            payload=getattr(cause,'mcp_error_payload',None)
                            if isinstance(payload,dict) and isinstance(payload.get('error'),dict) and 'nativeFailure' in payload['error']:
                                details['failure']=validate_native_failure(payload['error']['nativeFailure'])
                                break
                            cause=getattr(cause,'__cause__',None)
                            if cause is None:break
                        failure=details.get('failure')
                        failure_details=failure.get('details',{}) if isinstance(failure,dict) else {}
                        if operation_selected and isinstance(state,dict) and state['phase']=='save_pending':
                            state['save_failure']=deepcopy(failure or {'message':str(error)})
                            if known_no_input(failure_details):state['phase']='save_terminal';details['input_state_unknown']=False
                            persist()
                        dirty_preserved=(isinstance(failure,dict) and failure.get('code')=='XG5000_SAFE_CLOSE_UNSAVED_CHANGES'
                            and failure_details.get('operation')=='xg5000.reviewed_no_save_close'
                            and failure_details.get('source_closed') is False
                            and failure_details.get('discardUnsavedChanges') is False
                            and failure_details.get('unsavedChangesDiscarded') is False
                            and isinstance(failure_details.get('close'),dict)
                            and failure_details['close'].get('close_action')=='unsaved_changes')
                        if operation_selected and isinstance(state,dict) and state['phase']=='close_pending' and (known_no_input(failure_details) and not locals().get('reconsidered_close',False) or dirty_preserved):
                            # No close was dispatched. Preserve the rejection,
                            # and let a later explicit call recheck preconditions.
                            state.update(phase='saved',close_rejection=failure);persist()
                            details['input_state_unknown']=False
                        elif operation_selected and isinstance(state,dict) and state['phase']=='close_pending' and not locals().get('close_attempted',True) and not locals().get('reconsidered_close',False):
                            state.update(phase='saved',close_rejection={'message':str(error)});persist()
                            details['input_state_unknown']=False
                        if operation_selected and isinstance(state,dict) and state['phase']=='launch_pending' and isinstance(failure,dict):
                            state['terminal_failure']=failure
                            if launch_retired(failure):state['phase']='launch_terminal'
                            persist()
                        # Report the committed disposition, not the phase before
                        # terminal retirement/no-input evidence was reconciled.
                        if isinstance(state,dict):
                            details['phase']=state['phase']
                            details['input_state_unknown']=state['phase'] in {'save_pending','close_pending','launch_pending'}
                        next_action='Inspect actual owned UI or the original close/open outcome; do not retry input blindly.'
                        if details['phase']=='launch_terminal':
                            next_action='The original launched process is verified retired. Inspect the recorded failure before deciding whether to explicitly request a new recovery; another recover_workspace call starts a new launch operation.'
                        elif details['phase']=='launch_pending':
                            next_action='The original launch outcome remains unresolved. On the same physical generation, observe owned UI or explicitly call recover_workspace only to reconcile that original operation; never replay its unknown input. If Host has independently rebound this workspace to a different physical generation, an explicit recover request may select a new operation only after Host freshly verifies the current frame/lifetime and saved identity. The prior operation and its unknown outcomes remain historical, without settlement or replay. A generation token or new session alone proves neither freshness nor permission; this guidance does not settle or authorize retry of an unknown simulator Start.'
                        if operation_selected and isinstance(state,dict):state['last_failure']=deepcopy(details);persist()
                        return {'ok':False,'rebound':False,**details,
                            'error':{'nativeFailure':build_native_failure('workspace_recovery_failed',str(error),details),
                                     'kind':'workspace_recovery_failed'},
                            'next_action':next_action}

                def current_native_process_create_time():
                    # The MCP snapshot deliberately omits Host session proof.
                    # Supply only this matching Host-owned session lifetime;
                    # no foreground or continuing-liveness claim is made.
                    current=mcp.agent_session_identity_snapshot
                    session=active_engineering_session
                    fields=('workspace_id','revision_id','session_id','pid','hwnd','project_path')
                    if (not isinstance(current,dict) or not isinstance(session,dict)
                            or any(current.get(k)!=target.get(k) for k in fields)
                            or any(session.get(k)!=target.get(k) for k in ('session_id','pid','hwnd','project_path'))):
                        return None
                    return _engineering_session_process_create_time(session)

                if preparation_run:
                    from .native_project_preparation import NativeProjectPreparation

                    async def prepared_target(result: dict[str, Any]) -> dict[str, Any]:
                        nonlocal active_workspace, active_engineering_session, target
                        workspace = _local_workspace_v1(result["workspace"])
                        if not _native_ld_edit_ready(result["session"], result["workspace"]):
                            raise AgentOutputContractError("Native preparation did not verify its editor/session")
                        active_workspace = workspace.model_dump(mode="json")
                        store.set_host_state("active_workspace", active_workspace)
                        session = bind_engineering_session(result)
                        store.bind_native_created_conversation(conversation_id=conversation_id, run_id=run_id,
                            workspace_id=workspace.workspace_id, revision_id=workspace.revision_id,
                            engineering_session_id=session["session_id"])
                        target = mcp.agent_session_identity_snapshot
                        store.append_event(event_type="host.status", conversation_id=conversation_id, run_id=run_id,
                            payload=current_status_v1().model_dump(mode="json", by_alias=True))
                        return target

                    async def cancelled_preparation(result: dict[str, Any]) -> None:
                        preparation["previous_reservation"] = deepcopy(preparation["reservation"])
                        preparation["reservation"] = deepcopy(result["reservation"])
                        preparation["frame"] = deepcopy(result["frame"])
                        preparation["idle_frame_authority"] = True
                        preparation["cancellation_result"] = deepcopy(result)
                        binding["project_path"] = result["reservation"]["project_path"]
                        store.set_host_state(preparation_key, preparation)

                    owner = NativeProjectPreparation(gateway=mcp, reservation=preparation["reservation"],
                        frame=preparation["frame"], initial_target=preparation["initial_target"], on_prepared=prepared_target,
                        on_cancelled=cancelled_preparation, idle_frame_authority=preparation.get("idle_frame_authority") is True,
                        current_target=lambda: mcp.agent_session_identity_snapshot,
                        current_process_create_time=current_native_process_create_time,
                        pending_recovery_owned_ui=validate_pending_recovery_owned_ui,
                        wait_workspace_recovery=wait_workspace_recovery,
                        record_raw=record_raw, refresh_saved=refresh_saved,
                        record_process_diagnostic=record_process_diagnostic,
                        recover_workspace=recover_native_workspace,
                        current_recovery_admission=admitted_native_workspace_generation)
                else:
                    def recovery_current_target():
                        current=mcp.agent_session_identity_snapshot
                        # File-only binding preserves recovery/checkpoint ownership,
                        # never the retired frame's native or owned-UI authority.
                        if pending_anchor is not None and recovery_workspace_only_binding(current,pending_anchor):
                            return None
                        return current
                    owner = NativeWorkspaceExecution(gateway=mcp, target=target,
                        current_target=recovery_current_target,
                        current_process_create_time=current_native_process_create_time,
                        pending_recovery_owned_ui=validate_pending_recovery_owned_ui,
                        wait_workspace_recovery=wait_workspace_recovery,
                        record_raw=record_raw, refresh_saved=refresh_saved,
                        record_process_diagnostic=record_process_diagnostic,
                        recover_workspace=recover_native_workspace,
                        current_recovery_admission=admitted_native_workspace_generation,
                        captured_source={key: knowledge_workspace_snapshot.get("source", {}).get(key)
                                         for key in ("captured_path", "sha256")})
                owner.allow_simulator = bool(request_message is not None and request_message.metadata.get("allow_simulator") is True)
                owner.expected_process_create_time = (preparation["frame"]["expected_process_create_time"] if preparation_run
                    else pending_anchor['process_create_time'] if pending_anchor is not None
                    else _engineering_session_process_create_time(engineering_session_snapshot))
                await restore_pending_recovery_owner(owner)
                recovery_generation=(admitted_native_workspace_generation(owner) if not preparation_run else None)
                def completion_snapshot(fresh_saved: bool):
                    # Identity/source binding, not a blanket process-live requirement.
                    try:
                        if owner.recovery_pending or owner.recovery_only:
                            return None
                        owner._check_target()
                        created = current_native_process_create_time()
                        if (type(created) not in {int, float} or not math.isfinite(created)
                                or created != owner.expected_process_create_time
                                or not isinstance(active_workspace, dict)
                                or any(active_workspace.get(k) != owner.target.get(k) for k in ("workspace_id", "revision_id"))):
                            return None
                        working = active_workspace.get("working_copy", {})
                        saved_sha = working.get("current_sha256")
                        if (not isinstance(saved_sha, str) or re.fullmatch("[0-9a-f]{64}", saved_sha) is None
                                or _normalized_workspace_path(working.get("path", "")) != _normalized_workspace_path(owner.target["project_path"])):
                            return None
                        if fresh_saved:
                            saved = saved_project_observation(Path(owner.target["project_path"]))
                            owner._check_target()
                            if saved.get("stable_during_read") is not True or saved.get("sha256") != saved_sha:
                                return None
                        return {**{k: owner.target[k] for k in ("workspace_id", "revision_id", "pid", "hwnd")},
                            "project_path": _normalized_workspace_path(owner.target["project_path"]),
                            "process_create_time": created, "saved_sha256": saved_sha}
                    except (MCPToolError, KeyError, TypeError, ValueError):
                        return None
                completion_users = [{"message_id": item.id, "content": item.content}
                    for item in detail.messages if item.role is MessageRole.USER]
                completion_goal = (detail.project_brief if detail.project_brief is not None else
                    {"origin": "host_preserved_native_project_start_request"}
                    if any(item.role is MessageRole.USER and isinstance(item.metadata.get("native_project_start"), dict)
                           for item in detail.messages) else None)
                owner.completion_ledger = NativeCompletionLedger(store=store, conversation_id=conversation_id,
                    run_id=run_id, request_message_id=request_message_id, workspace_id=target["workspace_id"],
                    revision_id=target["revision_id"], request_content=prompt, user_messages=completion_users,
                    project_brief=completion_goal, snapshot=completion_snapshot,
                    receipt_sequence=lambda: raw_receipt_sequence,
                    allowed_runs={**{item.id: item.request_message_id for item in detail.runs if item.request_message_id},
                                  run_id: request_message_id})
                execution_broker.bind_native_run(conversation_id, run_id, owner)
                native_execution_started = True

                async def native_checkpoint_callback(checkpoint: dict[str, Any], digest: str) -> None:
                    getattr(owner,'check_checkpoint_target',owner._check_target)()
                    if not native_checkpoint_matches_request(checkpoint, prompt, binding):
                        raise AgentOutputContractError("Native checkpoint objective differs from its bound request")
                    await native_checkpoint_boundary(asyncio.to_thread(
                        _host_sqlite_boundary, "native_checkpoint_persist", persist_native_checkpoint_with_index,
                        store, checkpoint_key, index_key, binding,
                        checkpoint, digest, source_run_id=run_id))

                async def native_event(event_type: str, payload: dict[str, Any]) -> None:
                    if event_type != "tool.completion_preflight":
                        _host_sqlite_boundary("native_event_persist", store.append_event,
                            event_type=event_type, conversation_id=conversation_id, run_id=run_id, payload=payload)

                async def native_token(delta: str) -> None:
                    _host_sqlite_boundary("native_event_persist", store.append_event,
                        event_type="agent.token", conversation_id=conversation_id, run_id=run_id, payload={"delta": delta})

                async def native_progress(stage: str, elapsed: int) -> None:
                    _host_sqlite_boundary("native_event_persist", store.append_event,
                        event_type="agent.progress", conversation_id=conversation_id, run_id=run_id,
                        payload={"stage": stage, "elapsedSeconds": elapsed})

                recovery_state=(current_workspace_recovery(owner) if not preparation_run else None)
                try:
                    if owner.recovery_only and recovery_state is None:
                        raise AgentRunPreconditionError("보존된 작업 사본 복구의 현재 상태를 확인할 수 없습니다.")
                    native_engineering_sources = resolve_native_engineering_sources(detail, target)
                    runtime = runtime_factory(settings, execution_broker)
                    if hasattr(runtime, "bind_knowledge_snapshot"):
                        async def native_knowledge_snapshot() -> list[dict[str, Any]]:
                            return knowledge_store.agent_source_snapshot()
                        runtime.bind_knowledge_snapshot(native_knowledge_snapshot)
                    output = await runtime.run(prompt=prompt, history=history, conversation_id=conversation_id, run_id=run_id,
                        trusted_context={"workflow": workflow, "change_request_scope": change_request_scope,
                            "verification_scope": verification_scope, "native_ld_execution": {
                                **({"completion_context": completion_context}
                                   if (completion_context := owner.completion_ledger.context()) is not None else {}),
                                "schema_version": "1", "workspace_id": target["workspace_id"], "revision_id": target["revision_id"],
                                "engineering_sources": native_engineering_sources,
                                # Opaque content-cache scope only. The Agent receives no
                                # native target/path authority and each Host call rebinds.
                                "restore_scope": hashlib.sha256(json.dumps([conversation_id, binding],
                                    sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest(),
                                "program_name": None, "transport": "responses" if settings.model == "gpt-6-luna" else "chat",
                                **({"preparation": True} if preparation_run else {}),
                                "workspace_recovery": True,
                                **({"simulation": True} if owner.allow_simulator else {}),
                                **({"workspace_recovery_generation":recovery_generation} if recovery_generation else {}),
                                **({"current_workspace_recovery":recovery_state} if recovery_state is not None else {})}},
                        native_checkpoint=native_checkpoint, on_native_checkpoint=native_checkpoint_callback,
                        on_tool_event=native_event, on_token=native_token, on_progress=native_progress)
                    getattr(owner,'check_checkpoint_target',owner._check_target)()
                    if output.workflow != "native_ld_partial_edit" or any(getattr(output, key, None) is not None for key in (
                        "engineering_mission", "digest", "cycle_disposition", "engineering_analysis", "approval_handoff",
                        "plan_artifact", "plan_digest", "plan_status", "project_name")):
                        raise AgentOutputContractError("Native run returned a legacy completion artifact")
                    completion_review = owner.completion_ledger.evaluate(
                        (output.native_request_disposition or {}).get("completion_assessment"),
                        (output.native_request_disposition or {}).get("status"))
                    message = store.append_message(conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                        content=output.content, metadata={"run_id": run_id, "status": "complete", "workflow": "engineering",
                            "native_completion_review": completion_review,
                            "native_ld_execution": True, "model": output.model,
                            "input_tokens": output.input_tokens + request_message.metadata.get('native_project_start_usage', {}).get('input_tokens', 0),
                            "output_tokens": output.output_tokens + request_message.metadata.get('native_project_start_usage', {}).get('output_tokens', 0),
                            "native_request_disposition": output.native_request_disposition})
                    store.append_event(event_type="agent.message", conversation_id=conversation_id, run_id=run_id,
                                       payload={"message_id": message.id, "content": message.content})
                    close_diagnostic=getattr(owner,'close_vendor_process_diagnostics',None)
                    if close_diagnostic is not None:
                        await native_checkpoint_boundary(close_diagnostic())
                    store.update_run_state(run_id=run_id, state=RunState.COMPLETED)
                finally:
                    try:
                        close_diagnostic=getattr(owner,'close_vendor_process_diagnostics',None)
                        if close_diagnostic is not None:
                            await native_checkpoint_boundary(close_diagnostic())
                    finally:
                        execution_broker.release_native_run(conversation_id, run_id)
                return

            if workflow == "engineering":
                store.expire_change_approvals(conversation_id=conversation_id)
            stored_engineering_mission = (
                store.get_engineering_mission(conversation_id)
                if workflow == "engineering"
                else None
            )
            diagnostic_transaction = (
                store.get_change_transaction(stored_engineering_mission.change_transaction_id)
                if stored_engineering_mission is not None
                and stored_engineering_mission.change_transaction_id is not None
                else None
            )
            quarantine_failure = (
                _quarantined_diagnostic_context(
                    conversation=detail,
                    mission=stored_engineering_mission,
                    transaction=diagnostic_transaction,
                    session=engineering_session_snapshot,
                    workspace=knowledge_workspace_snapshot,
                )
                if workflow == "engineering"
                and request_message is not None
                and request_message.role is MessageRole.USER
                and not engineering_mission_continue
                else None
            )
            quarantine_diagnostic_only = quarantine_failure is not None
            analysis_completion_run = targetless_analysis_run or quarantine_diagnostic_only
            if quarantine_diagnostic_only:
                # Do not resume/replace the durable mission. This local run can
                # inspect its retained target while recovery owns the mutation lease.
                stored_engineering_mission = None
                change_request_scope = "analysis_only"
            if (
                stored_engineering_mission is not None
                and not engineering_mission_continue
                and stored_engineering_mission.change_transaction_id is None
            ):
                # A new user request must not be captured by an active mission
                # left behind by a failed Agent run.  Validate the durable record
                # before ignoring it, and retain it until the new run checkpoints;
                # that gives us crash-safe, journaled replacement rather than an
                # eager destructive delete.
                try:
                    failed_mission_candidate, failed_mission_digest = (
                        _canonical_engineering_mission(
                            stored_engineering_mission.engineering_mission
                        )
                    )
                except (TypeError, ValueError):
                    failed_mission_candidate = None
                    failed_mission_digest = ""
                previous_mission_run = store.get_run(
                    stored_engineering_mission.updated_by_run_id
                )
                if (
                    failed_mission_candidate is not None
                    and hmac.compare_digest(
                        failed_mission_digest,
                        stored_engineering_mission.digest,
                    )
                    and previous_mission_run is not None
                    and previous_mission_run.conversation_id == conversation_id
                    and previous_mission_run.state is RunState.FAILED
                    and failed_mission_candidate.get("status") == "active"
                ):
                    store.append_event(
                        event_type="engineering_mission.replacement_started",
                        conversation_id=conversation_id,
                        run_id=run_id,
                        payload={
                            "previous_run_id": previous_mission_run.id,
                            "reason": "failed_run_new_user_request",
                        },
                    )
                    stored_engineering_mission = None
            if targetless_analysis_run and stored_engineering_mission is not None:
                raise AgentRunPreconditionError(
                    "대상 없는 분석 대화에는 대상 결속 mission을 재사용할 수 없습니다."
                )
            engineering_mission_snapshot: dict[str, Any] | None = None
            engineering_agent_checkpoint: dict[str, Any] | None = None
            engineering_checkpoint_updated_at: datetime | None = None
            historical_evidence_bundle: dict[str, Any] | None = None
            mission_change_transaction_id: str | None = None
            linked_change: ChangeTransaction | None = None
            linked_terminal_change_state: ChangeTransactionState | None = None
            current_change_outcome: dict[str, Any] | None = None
            current_change_outcome_evidence: dict[str, Any] | None = None
            fresh_terminal_objective = False
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
                    engineering_checkpoint_updated_at = stored_agent_checkpoint.updated_at
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
                fresh_terminal_objective = (
                    not engineering_mission_continue
                    and (
                        engineering_mission_snapshot.get("status")
                        in {"completed", "blocked"}
                        or (
                            engineering_mission_snapshot.get("status") == "waiting_for_approval"
                            and request_message is not None
                            and request_message.role is MessageRole.USER
                            and linked_change is not None
                            and linked_terminal_change_state is not None
                            and linked_change.receipt_acknowledged_at is not None
                        )
                    )
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
                and not fresh_terminal_objective
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
            if fresh_terminal_objective:
                # A terminal mission, including approval-waiting state whose
                # exact linked change was acknowledged after termination, may
                # be followed by a new explicit objective. The old checkpoint
                # and evidence stay journaled; only this run starts afresh.
                engineering_mission_snapshot = None
                engineering_agent_checkpoint = None
                engineering_checkpoint_updated_at = None
                historical_evidence_bundle = None
                mission_change_transaction_id = None
                linked_change = None
                linked_terminal_change_state = None
                current_change_outcome = None
                current_change_outcome_evidence = None
            # Mission authority stays in the validated checkpoint. A newer
            # same-context transcript supplies only historical messages.
            transcript_checkpoint = (
                _latest_historical_transcript_for_run(
                    store, conversation_id=conversation_id,
                    workspace_id=detail.workspace_id, revision_id=detail.revision_id,
                    checkpoint_updated_at=engineering_checkpoint_updated_at,
                )
                if not quarantine_diagnostic_only else None
            )
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
            terminal_candidate_reproposal_requested = bool(
                terminal_candidate_reproposal_scope is not None
                or terminal_candidate_reproposal_verification_scope is not None
            )
            (
                bound_proposal_step_id(store, linked_change, engineering_mission_snapshot)
                if linked_change is not None
                else None
            )
            if (
                terminal_candidate_reproposal_requested
                and not False
            ):
                raise AgentRunPreconditionError(
                    "terminal candidate reproposal authority is invalid"
                )
            (
                proposal_change_request_scope,
                proposal_verification_scope,
            ) = _proposal_scopes_for_run(
                change_request_scope=change_request_scope,
                verification_scope=verification_scope,
                terminal_candidate_reproposal_authorized=(
                    False
                ),
                terminal_candidate_reproposal_verification_scope=(
                    terminal_candidate_reproposal_verification_scope
                ),
            )
            allowed_verification_scopes: tuple[str, ...] = (
                proposal_verification_scope,
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
            agent_tool_event_observed = False
            pending_design_read_activity: dict[str, Any] | None = None
            evidence_free_general_analysis_completion = False
            direct_change_prior_evidence_ids: set[str] | None = None

            async def publish_token(delta: str) -> None:
                store.append_event(
                    event_type="agent.token",
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload={"delta": delta},
                )

            async def persist_transcript_checkpoint(
                checkpoint: dict[str, Any], digest: str,
            ) -> None:
                store.checkpoint_engineering_transcript(
                    conversation_id=conversation_id, run_id=run_id,
                    workspace_id=detail.workspace_id, revision_id=detail.revision_id,
                    checkpoint=checkpoint, digest=digest,
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
                nonlocal evidence_free_general_analysis_completion
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
                if cycle < 1:
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
                if progression_base is None and "completionKind" not in canonical_snapshot:
                    raise AgentOutputContractError(
                        "new engineering mission requires completionKind"
                    )
                if cycle_disposition == "checkpoint":
                    if canonical_snapshot != progression_base:
                        raise AgentOutputContractError(
                            "transcript checkpoint cannot change the engineering mission"
                        )
                    try:
                        store.checkpoint_engineering_mission(
                            conversation_id=conversation_id, run_id=run_id,
                            cycle=cycle, cycle_disposition="checkpoint",
                            engineering_mission=canonical_snapshot, digest=digest,
                            agent_checkpoint=agent_checkpoint,
                            agent_checkpoint_digest=agent_checkpoint_digest,
                            change_transaction_id=(
                                latest_mission_checkpoint[6]
                                if latest_mission_checkpoint is not None
                                else mission_change_transaction_id
                            ),
                        )
                    except EngineeringMissionConflict as exc:
                        raise AgentOutputContractError(str(exc)) from exc
                    return
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
                if direct_change_prior_evidence_ids is not None:
                    current_run_target_evidence_refs.difference_update(
                        direct_change_prior_evidence_ids
                    )
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
                            legacy_completion_kind=(
                                "native_change"
                                if change_request_scope
                                in {
                                    "offline_candidate_revision",
                                    "permanent_offline_revision",
                                }
                                else "analysis"
                            ),
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
                    allow_evidence_free_general_completion = bool(
                        change_request_scope in {"analysis_only", "offline_candidate_revision"}
                        and not engineering_mission_continue
                        and engineering_mission_snapshot is None
                        and not agent_tool_event_observed
                        and not current_run_evidence_refs
                        and proposed_change_authority is None
                        and initial_pending_change_authority is None
                        and current_change_outcome is None
                        and target_live_change_transaction is None
                    )
                    if (
                        not completed_evidence_refs
                        and not allow_evidence_free_general_completion
                    ):
                        raise AgentOutputContractError(
                            "completed engineering mission has no Host-bound evidence"
                        )
                    evidence_free_general_analysis_completion = (
                        not completed_evidence_refs
                        and allow_evidence_free_general_completion
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
                completion_kind = canonical_snapshot.get("completionKind")
                requires_exact_change_outcome = (
                    completion_kind == "native_change"
                    if completion_kind is not None
                    else (
                        change_request_scope
                        in {
                            "offline_candidate_revision",
                            "permanent_offline_revision",
                        }
                        or (
                            engineering_mission_continue
                            and mission_change_transaction_id is not None
                        )
                    )
                )
                if (
                    mission_status == "completed"
                    and requires_exact_change_outcome
                    and linked_terminal_change_state
                    is not ChangeTransactionState.COMPLETED
                ):
                    raise AgentOutputContractError(
                        "offline change mission completion requires an exact "
                        "successfully completed Host change outcome"
                    )
                if cycle_disposition == "continue" and (
                    mission_status != "active" or stop_reason != "continuing"
                ):
                    raise AgentOutputContractError(
                        "continuing mission checkpoint has an invalid stop condition"
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
                nonlocal agent_tool_event_observed
                nonlocal pending_design_read_activity
                if event_type not in {
                    "tool.selected",
                    "tool.started",
                    "tool.completed",
                    "tool.failed",
                    "tool.completion_preflight",
                }:
                    raise ValueError("unsupported agent tool event type")
                completion_preflight = event_type == "tool.completion_preflight"
                if not completion_preflight:
                    agent_tool_event_observed = True
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
                if tool_name == "design.edit":
                    edit_attempt = _safe_design_edit_attempt_summary(
                        payload.get("edit_attempt")
                    )
                    if edit_attempt is not None:
                        safe_payload["edit_attempt"] = edit_attempt
                failure_kind = payload.get("failure_kind")
                if failure_kind in {
                    "tool_error",
                    "host_policy",
                    "cancelled",
                    "transport",
                }:
                    safe_payload["failure_kind"] = failure_kind
                if event_type == "tool.failed":
                    # Never persist exception/provider text. The optional code
                    # is a closed Host vocabulary; the fallback is the closed
                    # failure_kind vocabulary above.
                    safe_payload.update(_agent_tool_failure_projection(payload))
                    if tool_name.startswith("design."):
                        diagnostics = _bounded_design_failure_diagnostics(payload)
                        if diagnostics:
                            safe_payload["diagnostics"] = diagnostics
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
                change_outcome_state = (
                    current_change_outcome.get("state")
                    if isinstance(current_change_outcome, dict)
                    and isinstance(current_change_outcome.get("state"), str)
                    else None
                )
                design_read_activity = None
                if AGENT_TOOL_NAMES.get(tool_name, tool_name) == "design.read":
                    if event_type == "tool.completed":
                        design_read_activity = pending_design_read_activity
                    if event_type in {"tool.completed", "tool.failed"}:
                        pending_design_read_activity = None
                safe_payload.update(
                    _agent_tool_activity_projection(
                        tool_name=tool_name,
                        event_type=(
                            "tool.completed" if completion_preflight else event_type
                        ),
                        change_request_scope=change_request_scope,
                        change_outcome_state=change_outcome_state,
                        simulator_target_status=simulator_target_status,
                        design_read_activity=design_read_activity,
                    )
                )
                if completion_preflight:
                    return _agent_tool_evidence_ref(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_name=tool_name,
                        request_id=request_id,
                        tool_call_id=(
                            tool_call_id
                            if isinstance(tool_call_id, str) and tool_call_id
                            else None
                        ),
                        evidence_contract=evidence_contract,
                        evidence_context=evidence_context,
                        simulator_target_status=simulator_target_status,
                    )
                if tool_name == "search_engineering_knowledge":
                    safe_payload.update(activity_label="설비 자료·명령 매뉴얼 조회", activity_role="자료 조사")
                    count = payload.get("knowledge_result_count")
                    if event_type == "tool.completed" and type(count) is int and 0 <= count <= 50:
                        safe_payload["activity_result"] = f"출처가 있는 자료 {count}건을 조회했습니다."
                if event_type == "tool.completed":
                    evidence = _agent_tool_evidence_ref(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_name=tool_name,
                        request_id=request_id,
                        tool_call_id=(
                            tool_call_id if isinstance(tool_call_id, str) and tool_call_id else None
                        ),
                        evidence_contract=evidence_contract,
                        evidence_context=evidence_context,
                        simulator_target_status=simulator_target_status,
                    )
                    store.append_tool_completion(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool_payload=safe_payload,
                        evidence_payload=evidence,
                    )
                    return evidence
                store.append_event(
                    event_type=event_type,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    payload=safe_payload,
                )
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

            run_execution_broker = (
                MCPExecutionBroker(mcp.diagnostic_session(engineering_session_snapshot))
                if quarantine_diagnostic_only
                else execution_broker
            )
            runtime = runtime_factory(settings, run_execution_broker)
            bind_knowledge_snapshot = getattr(runtime, "bind_knowledge_snapshot", None)
            if callable(bind_knowledge_snapshot):

                async def sync_knowledge_sources_for_agent() -> list[dict[str, Any]]:
                    # The Host remains the authority for original source
                    # lifecycle.  It transfers a sanitized snapshot only; the
                    # private Agent owns all query/retrieval/reranking.
                    return knowledge_store.agent_source_snapshot()

                bind_knowledge_snapshot(sync_knowledge_sources_for_agent)
            bind_change_planner = getattr(runtime, "bind_change_planner", None)
            fresh_user_proposal_requested = bool(
                request_message is not None
                and request_message.role is MessageRole.USER
                and proposal_change_request_scope == "offline_candidate_revision"
                and target_live_change_transaction is None
            )
            if (
                callable(bind_change_planner)
                and workflow == "engineering"
                and (
                    current_change_outcome is None
                    or fresh_user_proposal_requested
                    or _expired_exact_operand_reproposal_callback_permitted(
                        engineering_mission_snapshot,
                        current_change_outcome,
                        rejected_reauthorized=rejected_change_reauthorized,
                    )
                )
                and proposal_change_request_scope != "analysis_only"
                and isinstance(knowledge_workspace_snapshot, dict)
                and _engineering_session_identity_verified(engineering_session_snapshot)
                and target_live_change_transaction is None
            ):
                # Candidate compilation and transaction creation are safe offline
                # planning operations. Execution, when explicitly requested by the
                # permanent check-only scope, still performs the independent
                # valid-bundle preflight below.
                allowed_verification_scopes = _allowed_verification_scopes_for_run(
                    request_scope=proposal_change_request_scope,
                    verification_scope=proposal_verification_scope,
                    simulation_capability_ready=False,
                    engineering_mission_continue=engineering_mission_continue,
                    terminal_candidate_reproposal_authorized=(
                        False
                    ),
                )

                async def propose_change_impl(**values: Any) -> dict[str, Any]:
                    nonlocal current_change_outcome
                    nonlocal current_change_outcome_evidence
                    nonlocal detail
                    nonlocal direct_change_prior_evidence_ids
                    nonlocal expected_engineering_target
                    nonlocal linked_change
                    nonlocal linked_terminal_change_state
                    nonlocal proposed_change_authority
                    if not engineering_conversation_matches_current_target(detail):
                        raise AgentRunPreconditionError(
                            "변경 계약을 만들기 전에 대상이 바뀌었습니다. "
                            "현재 대상에서 새 엔지니어링 대화를 시작해 주세요."
                        )
                    direct_execution_requested = (
                        _explicit_user_direct_change_authority(
                            request_message_role=(
                                request_message.role
                                if request_message is not None
                                else None
                            ),
                            engineering_mission_continue=engineering_mission_continue,
                            change_request_scope=change_request_scope,
                            verification_scope=verification_scope,
                        )
                    )
                    if (
                        direct_execution_requested
                        and direct_change_prior_evidence_ids is None
                    ):
                        direct_change_prior_evidence_ids = {
                            str(event.payload["evidenceId"])
                            for event in store.list_run_evidence(
                                conversation_id=conversation_id,
                                run_id=run_id,
                            )
                            if isinstance(event.payload.get("evidenceId"), str)
                            and event.payload["evidenceId"]
                        }
                    operation_id = f"agent-change:{run_id}"
                    existing_transaction = next(
                        (item for item in store.list_change_transactions(
                            conversation_id=conversation_id, limit=100,
                        ) if item.operation_id == operation_id),
                        None,
                    )
                    try:
                        selected_proposal_verification_scope: str | None = None
                        proposal_step_id = values.pop("_mission_step_id", None)
                        if proposal_step_id is not None:
                            raise ValueError("unexpected proposal mission-step metadata")
                        _reject_private_change_plan_material(values)
                        plan_kind = values.get("plan_kind")
                        selected_proposal_verification_scope = (
                            _proposal_verification_scope_for_callback(
                                plan_kind=plan_kind,
                                requested_scope=values.get("verification_scope"),
                                explicit_scope=proposal_verification_scope,
                                allowed_scopes=allowed_verification_scopes,
                            )
                        )
                        if plan_kind is None:
                            if offline_operand_mission is not None:
                                _require_offline_operand_mission_plan(
                                    offline_operand_mission, values
                                )
                            plan = ChangePlanV1(
                                change_mode=values["change_mode"],
                                request_scope=proposal_change_request_scope,
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
                        if existing_transaction is None:
                            if isinstance(exc, ValidationError):
                                message = "; ".join(
                                    ".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
                                    for error in exc.errors(include_input=False, include_url=False)[:12]
                                )
                            else:
                                message = str(exc)
                            return {
                                "status": "not_created",
                                "execution_permitted": False,
                                "next_action": "revise_proposal",
                                "message": message[:4000],
                            }
                        raise AgentRunPreconditionError(
                            f"Agent change proposal is invalid: {exc}"
                        ) from exc
                    if existing_transaction is not None:
                        try:
                            requested_risk = ChangeRiskLevel(
                                str(values["risk_level"]).upper()
                            )
                            stored_plan = _parse_change_plan(
                                existing_transaction.plan
                            )
                            recomputed_digest = calculate_plan_digest(
                                target=existing_transaction.target,
                                risk_level=existing_transaction.risk_level,
                                intent=existing_transaction.intent,
                                plan=stored_plan.model_dump(mode="json"),
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            raise AgentRunPreconditionError(
                                "기존 변경 계약을 안전하게 다시 확인할 수 없습니다."
                            ) from exc
                        proposed_plan_data = plan.model_dump(mode="json")
                        stored_plan_data = stored_plan.model_dump(mode="json")
                        if (
                            existing_transaction.run_id != run_id
                            or existing_transaction.requested_by_actor_id
                            != request_actor_id
                            or existing_transaction.intent != str(values["intent"])
                            or existing_transaction.risk_level != requested_risk
                            or proposed_plan_data != stored_plan_data
                            or not hmac.compare_digest(
                                recomputed_digest,
                                existing_transaction.plan_digest,
                            )
                        ):
                            raise AgentRunPreconditionError(
                                "같은 실행의 기존 변경 계약이 현재 제안과 정확히 "
                                "일치하지 않습니다."
                            )
                        transaction, created = existing_transaction, False
                    else:
                        try:
                            transaction, created = create_server_change_transaction(
                                operation_id=operation_id,
                                actor_id=request_actor_id,
                                conversation_id=conversation_id,
                                run_id=run_id,
                                intent=str(values["intent"]),
                                risk_level=str(values["risk_level"]),
                                plan=plan,
                                approval_ttl_seconds=900,
                                initial_approval_actor_id=(
                                    request_actor_id
                                    if direct_execution_requested
                                    else None
                                ),
                            )
                        except ChangeExecutionPreconditionError as exc:
                            raise AgentRunPreconditionError(
                                f"변경 계약을 만들 수 없습니다: {exc}"
                            ) from exc
                    if (
                        transaction.conversation_id != conversation_id
                        or transaction.run_id != run_id
                        or transaction.target != current_change_target()
                        or transaction.plan.get("request_scope")
                        != proposal_change_request_scope
                        or transaction.plan.get("verification_scope")
                        != selected_proposal_verification_scope
                        or transaction.requested_by_actor_id != request_actor_id
                    ):
                        raise AgentRunPreconditionError(
                            "저장된 변경 계약의 actor, run, 대상 또는 선택 범위가 현재 "
                            "요청과 일치하지 않습니다."
                        )
                    if proposal_step_id is not None:
                        try:
                            record_proposal_step_binding(store, transaction, proposal_step_id)
                        except ValueError as exc:
                            raise AgentRunPreconditionError(
                                "변경 제안과 실행 단계의 저장된 연결이 일치하지 않습니다."
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
                    if (
                        not direct_execution_requested
                        and transaction.state
                        is not ChangeTransactionState.PENDING_APPROVAL
                    ):
                        raise AgentRunPreconditionError(
                            "승인 후보 callback은 승인 대기 상태의 변경 계약만 "
                            "반환할 수 있습니다."
                        )
                    response = {
                        "transaction_id": transaction.id,
                        "state": transaction.state.value,
                        "plan_digest": transaction.plan_digest,
                        "approval_expires_at": (transaction.approval_expires_at.isoformat()),
                        "created": created,
                        "execution_permitted": False,
                        "next_action": "human_approval_required",
                    }
                    if direct_execution_requested:
                        try:
                            approved, approval_recorded = (
                                store.decide_change_transaction(
                                    transaction_id=transaction.id,
                                    decision=ApprovalDecision.APPROVE,
                                    expected_plan_digest=transaction.plan_digest,
                                    actor_id=request_actor_id,
                                )
                            )
                        except ChangeStoreConflict as exc:
                            raise AgentRunPreconditionError(
                                "직접 변경의 일회성 승인 권한을 확인할 수 없습니다."
                            ) from exc
                        if approved is None:
                            raise AgentRunPreconditionError(
                                "직접 변경 transaction을 다시 확인할 수 없습니다."
                            )
                        if approval_recorded:
                            publish_change_event("approval.resolved", approved)
                        if approved.state is ChangeTransactionState.EXPIRED:
                            raise AgentRunPreconditionError(
                                "직접 변경 권한의 유효 시간이 만료되었습니다."
                            )

                        terminal = approved
                        if terminal.state is ChangeTransactionState.APPROVED:
                            try:
                                await _execute_change_transaction_internal(
                                    transaction.id,
                                    ExecuteChangeTransactionRequestV1(
                                        expected_plan_digest=transaction.plan_digest
                                    ),
                                    terminalize_preflight_failure=True,
                                )
                            except Exception as exc:
                                terminal = (
                                    store.get_change_transaction(transaction.id)
                                    or terminal
                                )
                                if terminal.state in {
                                    ChangeTransactionState.COMPLETED,
                                    ChangeTransactionState.ROLLED_BACK,
                                    ChangeTransactionState.FAILED,
                                }:
                                    pass
                                elif terminal.state in {
                                    ChangeTransactionState.EXECUTING,
                                    ChangeTransactionState.VERIFYING,
                                    ChangeTransactionState.OBSERVING,
                                    ChangeTransactionState.QUARANTINED,
                                }:
                                    raise AgentRunPreconditionError(
                                        "직접 변경 결과를 안전하게 확정할 수 없습니다. "
                                        "저장된 transaction을 복구 화면에서 확인해 주세요."
                                    ) from exc
                                else:
                                    raise AgentRunPreconditionError(
                                        "직접 변경의 안전 실행 사전조건을 충족하지 "
                                        "못했습니다."
                                    ) from exc
                            else:
                                terminal = (
                                    store.get_change_transaction(transaction.id)
                                    or terminal
                                )

                        if terminal.state in {
                            ChangeTransactionState.EXECUTING,
                            ChangeTransactionState.VERIFYING,
                            ChangeTransactionState.OBSERVING,
                            ChangeTransactionState.QUARANTINED,
                        }:
                            raise AgentRunPreconditionError(
                                "직접 변경 결과를 안전하게 확정할 수 없습니다. "
                                "저장된 transaction을 복구 화면에서 확인해 주세요."
                            )
                        if terminal.state not in {
                            ChangeTransactionState.COMPLETED,
                            ChangeTransactionState.ROLLED_BACK,
                            ChangeTransactionState.FAILED,
                        }:
                            raise AgentRunPreconditionError(
                                "직접 변경 transaction이 허용된 종료 상태에 도달하지 "
                                "못했습니다."
                            )

                        refreshed_conversation_target = (
                            current_engineering_conversation_target()
                        )
                        if (
                            refreshed_conversation_target is not None
                            and expected_engineering_target is not None
                            and refreshed_conversation_target[:2]
                            == expected_engineering_target[:2]
                        ):
                            try:
                                detail = store.rebind_engineering_conversation_session(
                                    conversation_id=conversation_id,
                                    workspace_id=refreshed_conversation_target[0],
                                    revision_id=refreshed_conversation_target[1],
                                    engineering_session_id=(
                                        refreshed_conversation_target[2]
                                    ),
                                    # This rebind also has to work before the first
                                    # durable mission checkpoint.  The existing
                                    # preparation reason is the deliberately narrow
                                    # path that permits that case while still
                                    # enforcing the exact workspace/revision pair.
                                    reason="offline_candidate_preparation",
                                )
                            except EngineeringMissionConflict:
                                logger.warning(
                                    "terminal direct change conversation session could "
                                    "not be rebound (transaction=%s)",
                                    terminal.id,
                                )
                            else:
                                expected_engineering_target = (
                                    refreshed_conversation_target
                                )
                        linked_change = terminal
                        linked_terminal_change_state = terminal.state
                        current_change_outcome = change_outcome_projection(terminal)
                        current_change_outcome_evidence = change_outcome_evidence(
                            current_change_outcome
                        )
                        outcome_evidence_id = str(
                            current_change_outcome_evidence["evidenceId"]
                        )
                        if not any(
                            event.payload.get("evidenceId") == outcome_evidence_id
                            for event in store.list_run_evidence(
                                conversation_id=conversation_id,
                                run_id=run_id,
                            )
                        ):
                            store.append_event(
                                event_type="evidence.added",
                                conversation_id=conversation_id,
                                run_id=run_id,
                                payload=current_change_outcome_evidence,
                            )
                        response.update(
                            {
                                "state": terminal.state.value,
                                "execution_permitted": True,
                                "next_action": {
                                    ChangeTransactionState.COMPLETED: (
                                        "change_execution_completed"
                                    ),
                                    ChangeTransactionState.ROLLED_BACK: (
                                        "change_verification_failed_rolled_back"
                                    ),
                                    ChangeTransactionState.FAILED: (
                                        "change_execution_failed"
                                    ),
                                }[terminal.state],
                                "change_outcome": current_change_outcome,
                            }
                        )
                    return response

                async def propose_change(**values: Any) -> dict[str, Any]:
                    return await _proposal_precondition_result(
                        lambda: propose_change_impl(**values),
                        lambda: any(
                            item.operation_id == f"agent-change:{run_id}"
                            for item in store.list_change_transactions(
                                conversation_id=conversation_id, limit=100,
                            )
                        ),
                    )

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
                        **(
                            {}
                        ),
                        **(
                            {}
                        ),
                        **(
                            {
                                "prior_design_failure_observation": (
                                    planning_prior_design_failure
                                )
                            }
                            if planning_prior_design_failure is not None
                            else {}
                        ),
                    }
                    if workflow == "new_project_planning"
                    else {
                        "workflow": "engineering",
                        **(
                            {}
                        ),
                        **(
                            {"targetless_analysis": True, "project_start_available": bool(
                                request_message is not None and request_message.role is MessageRole.USER)}
                            if targetless_analysis_run
                            else {}
                        ),
                        **(
                            {"quarantine_diagnostic_only": True, "quarantine_failure": quarantine_failure}
                            if quarantine_diagnostic_only else {}
                        ),
                        **(
                            engineering_session_snapshot
                            if _engineering_session_read_ready(
                                engineering_session_snapshot
                            )
                            else {}
                        ),
                        "change_request_scope": proposal_change_request_scope,
                        
                        "fresh_user_proposal_requested": fresh_user_proposal_requested,
                        "verification_scope": proposal_verification_scope,
                        "allowed_verification_scopes": list(
                            allowed_verification_scopes
                        ),
                        **(
                            {}
                        ),
                        **(
                            {}
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
                transcript_checkpoint=transcript_checkpoint,
                on_transcript_checkpoint=persist_transcript_checkpoint,
                on_token=publish_token,
                on_tool_event=publish_tool_event,
                on_progress=publish_progress,
                on_mission_checkpoint=(
                    None if analysis_completion_run else persist_mission_checkpoint
                ),
            )
            if getattr(output, 'native_project_start', None) is not None:
                if (not targetless_analysis_run or workflow != 'engineering'
                        or request_message is None or request_message.role is not MessageRole.USER):
                    raise AgentOutputContractError('New-project start is outside its original unbound request')
                handoff = validated_native_project_start(output.native_project_start)
                brief = ProjectBriefV1(project_name=handoff['project_name'],
                    machine_purpose='Original user request ' + request_message_id + '; preserved verbatim in this conversation.').model_dump()
                await asyncio.sleep(0)  # Cancellation before durable consumption creates nothing.
                store.consume_unbound_project_start(conversation_id=conversation_id, run_id=run_id,
                    request_message_id=request_message_id, prompt=prompt, handoff=handoff, project_brief=brief,
                    usage={'input_tokens': output.input_tokens, 'output_tokens': output.output_tokens},
                    public_plan=output.content)
                await asyncio.sleep(0)  # A consumed-but-interrupted request is not automatically replayed.
                native_start_continuation = True
                await execute_agent_run(conversation_id=conversation_id, run_id=run_id,
                    request_message_id=request_message_id, prompt=prompt)
                return
            if getattr(output, "approval_handoff", None) is not None:
                transaction = (
                    store.get_change_transaction(proposed_change_authority[0])
                    if proposed_change_authority is not None else None
                )
                handoff = _validate_agent_approval_handoff(
                    output, transaction=transaction,
                    proposed_authority=proposed_change_authority,
                    transcript=store.get_latest_engineering_transcript(
                        conversation_id, workspace_id=detail.workspace_id,
                        revision_id=detail.revision_id,
                    ),
                    conversation_id=conversation_id, run_id=run_id,
                    actor_id=request_actor_id, workspace_id=detail.workspace_id,
                    revision_id=detail.revision_id,
                )
                assistant_message = store.append_message(
                    conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                    content="변경안을 승인 대기 상태로 저장했습니다. 실제 프로젝트 변경은 아직 실행하지 않았습니다.",
                    metadata={
                        "run_id": run_id, "status": "complete", "workflow": workflow,
                        "model": output.model, "input_tokens": output.input_tokens,
                        "output_tokens": output.output_tokens,
                        "approval_handoff": handoff,
                    },
                )
                store.append_event(
                    event_type="agent.message", conversation_id=conversation_id, run_id=run_id,
                    payload={"message_id": assistant_message.id, "content": assistant_message.content},
                )
                store.update_run_state(run_id=run_id, state=RunState.COMPLETED)
                return
            final_mission: dict[str, Any] | None = None
            if workflow == "engineering":
                if quarantine_diagnostic_only:
                    # Diagnostic output cannot complete or overwrite the PLC mission.
                    final_mission = None
                elif targetless_analysis_run:
                    mission_fields = (
                        output.engineering_mission,
                        output.digest,
                        output.cycle_disposition,
                    )
                    if any(item is not None for item in mission_fields):
                        if any(item is None for item in mission_fields):
                            raise AgentOutputContractError(
                                "targetless analysis returned incomplete mission state"
                            )
                        try:
                            final_mission, final_mission_digest = (
                                _canonical_engineering_mission(
                                    output.engineering_mission
                                )
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
                else:
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
            if workflow == "new_project_planning" and (
                output.plan_artifact is not None or output.plan_digest is not None
            ):
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
                            requirement_messages={
                                item.id: item.content
                                for item in detail.messages
                                if item.role is MessageRole.USER
                            },
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
            elif workflow == "new_project_planning" and output.plan_status == "ready_for_review":
                raise AgentOutputContractError("An executable review requires a project plan artifact")
            engineering_analysis, engineering_analysis_digest = (
                (None, None)
                if analysis_completion_run
                else validate_engineering_analysis_artifact(
                    output.engineering_analysis,
                    output.engineering_analysis_digest,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    mission=final_mission,
                    prior_mission=engineering_mission_snapshot,
                    current_outcome_evidence=current_change_outcome_evidence,
                )
            )
            if evidence_free_general_analysis_completion:
                if (
                    change_request_scope != "analysis_only"
                    or engineering_mission_continue
                    or agent_tool_event_observed
                    or store.list_run_evidence(
                        conversation_id=conversation_id,
                        run_id=run_id,
                    )
                    or proposed_change_authority is not None
                    or current_change_outcome is not None
                ):
                    raise AgentOutputContractError(
                        "evidence-free completion exceeded the general analysis boundary"
                    )
                structured_target_fields = (
                    "observations",
                    "inferences",
                    "hypotheses",
                    "alternatives",
                    "locations",
                    "relations",
                    "coverage",
                    "projectStructure",
                    "evidenceRefs",
                )
                if engineering_analysis is not None and any(
                    engineering_analysis.get(field)
                    for field in structured_target_fields
                ):
                    raise AgentOutputContractError(
                        "evidence-free general analysis returned target-specific "
                        "structured claims"
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
                        if workflow == "engineering"
                        and not analysis_completion_run
                        and output.digest is not None
                        else {}
                    ),
                }
            if workflow == "engineering" and not analysis_completion_run:
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
            if not native_start_continuation:
                store.update_run_state(run_id=run_id, state=RunState.CANCELLED)
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize every provider/runtime failure
            host_failure_location = None
            if native_start_continuation:
                raise  # The delegated phase exclusively owns its terminal state/evidence. Runs are never reissued.
            retained_direct_transaction = (
                store.get_change_transaction(proposed_change_authority[0])
                if proposed_change_authority is not None
                else None
            )
            retained_direct_change_outcome = bool(
                retained_direct_transaction is not None
                and proposed_change_authority is not None
                and retained_direct_transaction.plan_digest
                == proposed_change_authority[1]
                and retained_direct_transaction.plan.get("request_scope")
                == "permanent_offline_revision"
                and retained_direct_transaction.plan.get("verification_scope")
                == "offline_native_check_only"
                and retained_direct_transaction.state
                in {
                    ChangeTransactionState.COMPLETED,
                    ChangeTransactionState.ROLLED_BACK,
                    ChangeTransactionState.FAILED,
                }
            )
            if isinstance(exc, AgentRunPreconditionError):
                host_failure_location = safe_host_failure_location(exc, native_execution_started=native_execution_started)
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
                    if retained_direct_change_outcome:
                        logger.warning(
                            "agent run %s failed final structured output processing; "
                            "a terminal direct change outcome from this run was retained",
                            run_id,
                        )
                    elif retained_current_run_change_contract:
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
                    if retained_direct_change_outcome:
                        logger.warning(
                            "agent run %s failed final structured output processing "
                            "after a terminal direct change outcome from this run was "
                            "retained (stage=%s, exception_kind=%s, reason_code=%s, "
                            "validator_code=%s, validation_errors=%s)",
                            run_id,
                            diagnostic.stage,
                            diagnostic.exception_kind,
                            diagnostic.reason_code,
                            diagnostic.validator_code,
                            validation_codes or "none",
                        )
                    elif retained_current_run_change_contract:
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
                if retained_direct_change_outcome:
                    user_message = (
                        "Host의 직접 변경 transaction은 종료 상태로 안전하게 "
                        "기록되었습니다. 다만 모델의 최종 근거·응답 계약 처리에는 "
                        "실패했습니다. 변경 실행 기록에서 정확한 결과를 확인해 주세요."
                    )
                elif retained_current_run_change_contract:
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
                        "계획서 생성을 완료하지 못했습니다. 입력한 요구사항은 "
                        "남아 있으며 프로젝트 파일은 변경하지 않았습니다. "
                        "아래 대화에서 다시 요청할 수 있습니다."
                    )
                error_code = "agent_output_contract"
                error_detail = (
                    "Final response processing failed after the Host retained a "
                    "canonical terminal direct change outcome for this run; the durable "
                    "transaction remains authoritative."
                    if retained_direct_change_outcome
                    else (
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
                        "(stage=%s, exception_kind=%s, reason_code=%s, provider_safe=%s)",
                        run_id,
                        diagnostic.stage,
                        diagnostic.exception_kind,
                        diagnostic.reason_code,
                        provider_failure_detail(diagnostic) or "unavailable",
                    )
                    user_message = _agent_runtime_user_message(diagnostic)
                    error_code = f"agent_runtime_{diagnostic.reason_code}"
                    error_detail = (
                        "Agent runtime failed with a safe diagnostic: "
                        f"stage={diagnostic.stage}; "
                        f"exceptionKind={diagnostic.exception_kind}; "
                        f"reasonCode={diagnostic.reason_code}."
                    )
            elif isinstance(exc,AgentSidecarCrashed):
                sidecar_diagnostic=sidecar_failure_diagnostic(exc)
                logger.error("agent run %s failed (type=AgentSidecarCrashed, sidecar_safe=%s, private_detail_suppressed=true)",
                    run_id,sidecar_failure_detail(sidecar_diagnostic) if sidecar_diagnostic is not None else 'unavailable')
                user_message = "에이전트 실행 프로세스와 통신을 완료하지 못했습니다. 저장된 작업은 유지됩니다."
                error_code = type(exc).__name__
                error_detail = "Agent dependency request failed; provider detail was suppressed (AgentSidecarCrashed)."
            elif _safe_host_sqlite_failure(exc) is not None:
                sqlite_diagnostic = _safe_host_sqlite_failure(exc)
                try:
                    logger.error("agent run %s failed (host_sqlite_diagnostic=%s, detail_suppressed=true)",
                                 run_id, _host_sqlite_failure_detail(sqlite_diagnostic))
                except Exception:
                    pass
                user_message = "앱의 실행 기록 처리 중 오류가 발생했습니다."
                error_code = "host_sqlite"
                error_detail = "Host local SQLite operation failed; private detail was suppressed."
            else:
                raw_detail = str(exc)
                credential_material_detected = (
                    settings is not None
                    and redact_known_model_secrets(raw_detail, settings.secrets) != raw_detail
                )
                logger.error(
                    "agent run %s failed (type=%s, provider_detail_suppressed=true, "
                    "credential_material_detected=%s, internal_protocol_reason=%s)",
                    run_id,
                    type(exc).__name__,
                    credential_material_detected,
                    (
                        str(exc)
                        if isinstance(exc, AgentProtocolError)
                        else "suppressed"
                    ),
                )
                host_failure_location = safe_host_failure_location(exc, native_execution_started=native_execution_started)
                user_message = "앱이 요청을 준비하거나 실행하는 중 오류가 발생했습니다. 저장된 작업은 유지됩니다."
                error_code = type(exc).__name__
                error_detail = (
                    "Host request processing failed; private detail was suppressed "
                    f"({type(exc).__name__})."
                )
            if retained_direct_change_outcome and not isinstance(
                exc,
                AgentOutputContractError,
            ):
                user_message = (
                    "Host의 직접 변경 transaction은 종료 상태로 안전하게 "
                    "기록되었습니다. 이후 에이전트 응답 생성에 실패했으므로 변경 "
                    "실행 기록에서 정확한 결과를 확인해 주세요."
                )
                error_detail += (
                    " A canonical terminal direct change transaction was retained; "
                    "its durable receipt remains authoritative."
                )
            if native_execution_started:
                user_message = "에이전트 실행이 완료되지 않았습니다. 이미 수행한 native 입력과 관측은 그대로 보존되어 있습니다."
                error_detail = "Native owner did not complete; actual dispatched inputs, observations and completed checkpoint prefix remain retained."
            sidecar_diagnostic=sidecar_failure_diagnostic(exc)
            if sidecar_diagnostic is not None:
                error_detail += " Safe sidecar diagnostic: " + sidecar_failure_detail(sidecar_diagnostic)
            sqlite_diagnostic = _safe_host_sqlite_failure(exc)
            if sqlite_diagnostic is not None:
                error_detail += " " + _host_sqlite_failure_detail(sqlite_diagnostic)
            if native_execution_started and isinstance(exc, AgentRuntimeError) and exc.diagnostic is not None:
                diagnostic = exc.diagnostic
                error_detail += (
                    " Safe runtime diagnostic: "
                    f"stage={diagnostic.stage}; exceptionKind={diagnostic.exception_kind}; "
                    f"reasonCode={diagnostic.reason_code}."
                )
                provider_detail = provider_failure_detail(diagnostic)
                if provider_detail:
                    error_detail += " " + provider_detail + "."
            if host_failure_location is not None:
                error_detail += host_failure_location_detail(host_failure_location)
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

    def validate_message_idempotent_replay_authority(
        existing_run: Any,
        *,
        conversation_id: str,
        body: SendMessageRequestV1,
        request: Request,
    ) -> None:
        if existing_run.conversation_id != conversation_id:
            raise HTTPException(status_code=409, detail="operationId belongs to another conversation")
        if existing_run.metadata.get("resume_native_run_id") != body.resume_native_run_id:
            raise HTTPException(status_code=409, detail="operationId belongs to a different native resume request")
        # A renewed local profile session may explicitly resume the same source.
        # Exact conversation, source and execution scope must still match.
        if body.resume_native_run_id is not None and (
            existing_run.metadata.get("change_request_scope") != body.change_request_scope
            or existing_run.metadata.get("verification_scope") != body.verification_scope
            or existing_run.metadata.get("allow_simulator", False) != body.allow_simulator
        ):
            raise HTTPException(status_code=409, detail="native resume replay authority differs")

    def recovery_source_run_matches(source_run_id: str, recorded_run_id: str, conversation_id: str) -> bool:
        # Checkpoint and small recovery state use separate durable writes.
        # Interrupted handoff is recoverable through exact server-validated
        # resume ancestry; this does not adopt an unrelated follow-up request.
        visited=set()
        while source_run_id and source_run_id not in visited:
            visited.add(source_run_id)
            source=store.get_run(source_run_id)
            if (source is None or source.conversation_id!=conversation_id
                    or source.state not in {RunState.FAILED,RunState.CANCELLED,RunState.COMPLETED}
                    or source.metadata.get('change_request_scope')!='permanent_offline_revision'
                    or source.metadata.get('verification_scope')!='offline_native_check_only'):
                return False
            resume_parent=source.metadata.get('resume_native_run_id')
            recovery_parent=source.metadata.get('workspace_recovery_source_run_id')
            if (any(parent is not None and (not isinstance(parent,str) or not parent) for parent in (resume_parent,recovery_parent))
                    or resume_parent is not None and recovery_parent is not None and resume_parent!=recovery_parent):
                return False
            if source_run_id==recorded_run_id:return True
            source_run_id=resume_parent or recovery_parent
        return False

    def recovery_workspace_only_binding(current: object, anchor: dict[str, Any]) -> bool:
        """Exact file-only workspace identity, solely for a preserved recovery owner."""
        if not isinstance(current,dict):
            return False
        try:
            expected=workspace_agent_binding(active_workspace)
            # Exact keys exclude partial native identities, including null fields.
            return (set(current)==set(expected) and current==expected
                and all(expected.get(k)==anchor.get(k) for k in ('workspace_id','revision_id'))
                and _normalized_workspace_path(expected['working_path'])==_normalized_workspace_path(anchor['project_path']))
        except (ValueError,TypeError,KeyError,OSError):
            return False

    def _pending_native_recovery_saved_matches(anchor,workspace_id,revision_id,saved_sha256):
        """The existing stable source/working proof; no session or native authority."""
        try:
            workspace=_local_workspace_v1(active_workspace)
            observed=saved_project_observation(Path(workspace.working_copy.path))
            if (workspace.workspace_id!=workspace_id or workspace.revision_id!=revision_id
                    or workspace.source.original_state!='unchanged'
                    or workspace.source.original_current_sha256!=workspace.source.sha256
                    or _normalized_workspace_path(workspace.working_copy.path)!=_normalized_workspace_path(anchor['project_path'])
                    or workspace.working_copy.current_sha256!=saved_sha256
                    or observed.get('stable_during_read') is not True
                    or observed.get('sha256')!=saved_sha256):
                return False
        except (ValueError,TypeError,KeyError,OSError):
            return False
        return True

    def _pending_native_recovery_target_anchor(state, workspace_id, revision_id):
        """Read-only target proof shared by pending resume and its bootstrap view."""
        if not isinstance(state,dict):
            return None
        anchor=state.get('binding')
        if (not isinstance(anchor,dict) or anchor.get('workspace_id')!=workspace_id
                or anchor.get('revision_id')!=revision_id or not anchor.get('session_id')
                or type(anchor.get('pid')) is not int or anchor['pid']<=0
                or type(anchor.get('hwnd')) is not int or anchor['hwnd']<=0
                or type(anchor.get('process_create_time')) not in {int,float}
                or not math.isfinite(anchor['process_create_time']) or anchor['process_create_time']<=0):
            return None
        current=mcp.agent_session_identity_snapshot
        revalidated=False
        if current is not None and not recovery_workspace_only_binding(current,anchor) and any(current.get(k)!=anchor.get(k) for k in
                ('workspace_id','revision_id','session_id','pid','hwnd','project_path')):
            # A new session ID is not a new physical generation. Accept only
            # the existing normal fresh file/lifetime revalidation contract.
            revalidated=(state['phase']=='close_pending'
                and all(current.get(k)==anchor.get(k) for k in ('workspace_id','revision_id','pid','hwnd','project_path'))
                and _engineering_session_process_create_time(active_engineering_session)==anchor['process_create_time']
                and _native_ld_edit_ready(active_engineering_session,active_workspace))
            if not revalidated:return None
        if not _pending_native_recovery_saved_matches(anchor,workspace_id,revision_id,state.get('saved_sha256')):
            return None
        return {**deepcopy(current),'process_create_time':anchor['process_create_time']} if revalidated else deepcopy(anchor)

    def pending_native_recovery_anchor(detail: Any, source_run_id: str | None) -> dict[str, Any] | None:
        """Preserved interrupted recovery only; never a general edit binding."""
        if not source_run_id or not detail.workspace_id or not detail.revision_id:
            return None
        source=store.get_run(source_run_id)
        state=store.get_host_state(_native_workspace_recovery_state_key(detail.id,detail.workspace_id,detail.revision_id))
        if (source is None or source.conversation_id!=detail.id or source.state not in {RunState.FAILED,RunState.CANCELLED,RunState.COMPLETED}
                or not isinstance(state,dict) or state.get('conversation_id')!=detail.id
                or (state.get('source_run_id')!=source_run_id and not recovery_source_run_matches(source_run_id,state.get('source_run_id'),detail.id))
                or not isinstance(state.get('operation_id'),str)
                or state.get('phase') not in {'close_pending','closed','launch_pending','launch_terminal'}):
            return None
        return _pending_native_recovery_target_anchor(state,detail.workspace_id,detail.revision_id)

    def native_resume_source_state_allowed(detail: Any, source: Any) -> bool:
        """Completed replies may resume only their still-pending managed recovery."""
        return bool(source is not None and (
            source.state in {RunState.FAILED, RunState.CANCELLED}
            or (source.state is RunState.COMPLETED
                and source.metadata.get("change_request_scope") == "permanent_offline_revision"
                and source.metadata.get("verification_scope") == "offline_native_check_only"
                and pending_native_recovery_anchor(detail, source.id) is not None)))

    def native_resume_target_matches_checkpoint(detail: Any, source_run_id: str, binding: object) -> bool:
        """Shared Web/submission target gate; file-only requires its recovery owner."""
        current = mcp.agent_session_identity_snapshot
        preparation = store.get_host_state(f"native_project_preparation:{detail.id}")
        if (detail.workflow == "new_project_planning" and isinstance(preparation, dict)
                and preparation.get("initial_target") == current
                and _native_checkpoint_target_matches(preparation.get("reservation"), binding)):
            return True
        if _native_checkpoint_target_matches(current, binding):
            return True
        anchor = pending_native_recovery_anchor(detail, source_run_id)
        return bool(anchor is not None
            and _native_checkpoint_target_matches(anchor, binding)
            # An unresolved managed launch clears current native readiness.
            # Its validated owner can resume recovery without admitting a frame.
            and (current is None or (recovery_workspace_only_binding(current, anchor)
                and _native_checkpoint_target_matches(current, binding, file_only=True))))

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
                validate_message_idempotent_replay_authority(
                    existing_run,
                    conversation_id=conversation_id,
                    body=body,
                    request=request,
                )
                return SendMessageResponseV1(
                    run_id=existing_run.id,
                    operation_id=body.operation_id,
                    state=existing_run.state.value.casefold(),
                )
            effective_change_request_scope: str | None = None
            effective_verification_scope = "offline_native_reopen"
            if body.workflow == "engineering" or body.change_request_scope == "permanent_offline_revision":
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
            if body.workflow == "new_project_planning" and body.change_request_scope == "permanent_offline_revision" and body.project_brief is not None:
                authoritative_content = authoritative_content.removesuffix(_LEGACY_PROJECT_BRIEF_FOOTER) + (
                    "자료와 요구사항을 확인한 뒤 새 프로젝트와 첫 LD 프로그램을 준비하고, 같은 대화에서 "
                    "LD 작성·검사·저장까지 진행해 주세요. 준비에 필요한 조건이 불명확하면 질문해 주세요."
                )
            detail = store.get_conversation(conversation_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            # Record the current authenticated actor on the new run while
            # preserving the original actor and interrupted source history.
            if body.resume_native_run_id is not None:
                retained = await asyncio.to_thread(latest_native_checkpoint_summary, store, conversation_id)
                retained_source = retained["source_run_id"] if retained is not None else None
                source = store.get_run(body.resume_native_run_id)
                source_message = next((item for item in detail.messages
                    if source is not None and item.id == source.request_message_id), None)
                latest_run = max(detail.runs, key=lambda item: item.created_at, default=None)
                if (retained_source != body.resume_native_run_id
                        or source is None or source.conversation_id != conversation_id
                        or not native_resume_source_state_allowed(detail, source)
                        or not _native_resume_latest_attempt_matches_checkpoint(detail, source, source_message, latest_run, store)
                        or not native_resume_target_matches_checkpoint(detail, source.id, retained["binding"])
                        or source_message is None or source_message.content != body.content
                        or source.metadata.get("change_request_scope") != "permanent_offline_revision"
                        or source.metadata.get("verification_scope") != "offline_native_check_only"):
                    raise HTTPException(status_code=409, detail="native resume does not match the latest interrupted request")
            # An authenticated explicit ordinary follow-up can also continue
            # a naturally completed, still-pending managed recovery. It is not
            # an implicit replay or a general targetless edit permission.
            recovery_source=body.resume_native_run_id
            if recovery_source is None:
                latest=max(detail.runs,key=lambda item:item.created_at,default=None)
                if latest is not None and latest.state is RunState.COMPLETED:
                    recovery_source=latest.id
            recovery_anchor=(pending_native_recovery_anchor(detail,recovery_source)
                if (effective_change_request_scope=='permanent_offline_revision'
                    and effective_verification_scope=='offline_native_check_only') else None)
            targetless_engineering_conversation = bool(
                detail.workflow == "engineering"
                and detail.workspace_id is None
                and detail.revision_id is None
                and detail.engineering_session_id is None
                and detail.engineering_handoff is None
            )
            if (
                targetless_engineering_conversation
                and effective_change_request_scope != "analysis_only"
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="targetless engineering conversations permit analysis_only requests",
                )
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
            if detail.workflow == "engineering" and effective_change_request_scope != "permanent_offline_revision":
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
                    allows_fresh_terminal_objective = (
                        body.workflow == "engineering"
                        and terminal_snapshot["status"]
                        in {"completed", "blocked"}
                    )
                    if (
                        terminal_snapshot["status"] in {"completed", "blocked"}
                        and not allows_fresh_terminal_objective
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "terminal engineering missions are read-only; "
                                "start a new engineering conversation for a new objective"
                            ),
                        )
            # A fresh recovery anchor already proved the same physical frame,
            # lifetime and saved source. Bind its ephemeral session through the
            # same durable-target/mission checks used for ordinary submission.
            # Exact file-only recovery may bind only its server-owned conversation
            # identity. The native anchor and recovery-only capabilities stay intact.
            recovery_session_target = (current_engineering_conversation_target()
                if recovery_anchor is not None else None)
            recovery_file_only_revalidated = bool(recovery_anchor is not None
                and recovery_session_target == (
                    detail.workspace_id, detail.revision_id,
                    _workspace_engineering_conversation_session_id(detail.workspace_id, detail.revision_id))
                and recovery_workspace_only_binding(mcp.agent_session_identity_snapshot, recovery_anchor))
            recovery_session_revalidated = bool(recovery_anchor is not None
                and recovery_session_target is not None
                and (recovery_file_only_revalidated
                    or recovery_session_target == tuple(recovery_anchor.get(key)
                        for key in ("workspace_id", "revision_id", "session_id"))))
            if (
                detail.workflow == "engineering"
                and not targetless_engineering_conversation
                and not engineering_conversation_matches_current_target(detail)
                and (recovery_anchor is None or recovery_session_revalidated)
            ):
                current_target = current_engineering_conversation_target()
                mission = store.get_engineering_mission(conversation_id)
                durable_target_matches = bool(
                    body.workflow == "engineering"
                    and current_target is not None
                    and current_target[:2]
                    == (detail.workspace_id, detail.revision_id)
                    and not current_target[0].startswith("controller-ls:")
                    and (recovery_anchor is None or current_target == recovery_session_target)
                )
                if durable_target_matches:
                    assert current_target is not None
                    if mission is not None:
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
                            workspace_id=current_target[0],
                            revision_id=current_target[1],
                            engineering_session_id=current_target[2],
                            reason="engineering_message_submission",
                        )
                    except EngineeringMissionConflict as exc:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=str(exc),
                        ) from exc
                    durable_target_matches = (
                        engineering_conversation_matches_current_target(detail)
                    )
                if not durable_target_matches:
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
                and recovery_anchor is None
                and not (
                    bool(mcp.status.status == "ready"
                         and _native_ld_edit_ready(active_engineering_session, active_workspace))
                    if (effective_change_request_scope == "permanent_offline_revision"
                        and effective_verification_scope == "offline_native_check_only")
                    else offline_mutation_ready()
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "offline change work requires an exact source-preserving "
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
                            "allow_simulator": body.allow_simulator,
                            "actor_id": _request_actor_id(request),
                            **({"resume_native_run_id": body.resume_native_run_id, "native_resume_admission_version": 1} if body.resume_native_run_id else {}),
                            **({"workspace_recovery_source_run_id":recovery_source} if recovery_anchor is not None else {}),
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
                        "allow_simulator": body.allow_simulator,
                        **({"resume_native_run_id": body.resume_native_run_id} if body.resume_native_run_id else {}),
                            **({"workspace_recovery_source_run_id":recovery_source} if recovery_anchor is not None else {}),
                        **({}),
                        **({}),
                        **({"project_brief": project_brief} if project_brief is not None else {}),
                        **(
                            {"change_request_scope": (effective_change_request_scope)}
                            if effective_change_request_scope is not None
                            else {}
                        ),
                        **(
                            {"verification_scope": effective_verification_scope}
                            if effective_change_request_scope is not None
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
            except ProjectPlanReviewConflict as exc:
                # A second Host process may have created the same operation
                # after the initial lookup.  Preserve idempotent replay even
                # when the source changed in the meantime; compare only the
                # stored typed authority and actor, never a fresh source digest.
                raced_run = store.get_run_by_operation_id(body.operation_id)
                if raced_run is not None:
                    if raced_run.conversation_id != conversation_id:
                        raise HTTPException(
                            status_code=409,
                            detail="operationId belongs to another conversation",
                        ) from exc
                    validate_message_idempotent_replay_authority(
                        raced_run,
                        conversation_id=conversation_id,
                        body=body,
                        request=request,
                    )
                    return SendMessageResponseV1(
                        run_id=raced_run.id,
                        operation_id=body.operation_id,
                        state=raced_run.state.value.casefold(),
                    )
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
                validate_message_idempotent_replay_authority(
                    run,
                    conversation_id=conversation_id,
                    body=body,
                    request=request,
                )
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
                None
                if not completed_task.cancelled():
                    completed_task.exception()

            task.add_done_callback(finish_run_task)
            return SendMessageResponseV1(
                run_id=run.id,
                operation_id=body.operation_id,
                state=run.state.value.casefold(),
            )

    @app.post(
        "/api/v1/conversations/{conversation_id}/native-execution/resume",
        response_model=SendMessageResponseV1,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_native_execution_v1(
        conversation_id: str, body: NativeExecutionResumeRequestV1, request: Request,
    ) -> SendMessageResponseV1:
        source = store.get_run(body.source_run_id)
        detail = store.get_conversation(conversation_id)
        original = next((item for item in detail.messages if source is not None
                         and item.id == source.request_message_id), None) if detail is not None else None
        if source is None or source.conversation_id != conversation_id or original is None:
            raise HTTPException(status_code=409, detail="native resume source is unavailable")
        # All target, scope, idempotency, active-run and scheduling checks use
        # the same submission path as the original request.
        return await send_message_v1(conversation_id, SendMessageRequestV1(
            content=original.content, operation_id=body.operation_id, workflow=detail.workflow,
            change_request_scope="permanent_offline_revision", verification_scope="offline_native_check_only",
            resume_native_run_id=body.source_run_id, allow_simulator=body.allow_simulator,
        ), request)

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
            store.expire_change_approvals(conversation_id=conversation_id)
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
            linked_change: ChangeTransaction | None = None
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
            mission_scope = (
                str(mission_scope)
                if mission_scope
                in {
                    "offline_candidate_revision",
                    "permanent_offline_revision",
                }
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
            mission_verification_scope = (
                str(mission_verification_scope)
                if mission_verification_scope
                in {
                    "offline_native_check_only",
                    "offline_native_reopen",
                    
                }
                else "offline_native_reopen"
            )
            # Mission metadata predates the stricter new-message matrix and may
            # legitimately carry permanent+reopen or candidate+check-only.
            # Preserve those values only while validating a narrow terminal
            # recovery/reproposal. A SYSTEM continuation never inherits the
            # original user message's general mutation authority.
            stored_candidate_scope = _stored_candidate_continuation_scope(
                mission_run.metadata if mission_run is not None else {},
                mission_request_message.role if mission_request_message is not None else None,
            )
            if stored_candidate_scope is not None:
                mission_scope, mission_verification_scope = stored_candidate_scope

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

            terminal_candidate_reproposal_permitted = False
            continuation_scope = "analysis_only"
            continuation_verification_scope = "offline_native_reopen"
            continuation_prompt = (
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
                        "verification_scope": continuation_verification_scope,
                        **(
                            {
                                "terminal_candidate_reproposal_scope": (
                                    "offline_candidate_revision"
                                ),
                                "terminal_candidate_reproposal_verification_scope": (
                                    mission_verification_scope
                                ),
                            }
                            if terminal_candidate_reproposal_permitted
                            else {}
                        ),
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
                        "verification_scope": continuation_verification_scope,
                        "engineering_mission_continue": True,
                        **(
                            {
                                "terminal_candidate_reproposal_scope": (
                                    "offline_candidate_revision"
                                ),
                                "terminal_candidate_reproposal_verification_scope": (
                                    mission_verification_scope
                                ),
                            }
                            if terminal_candidate_reproposal_permitted
                            else {}
                        ),
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
        durable_mutation_stage = store.get_mutation_stage_change_for_run(run.id)
        active_change_execution = next(
            (
                transaction
                for transaction_id in tuple(active_change_executions)
                if (transaction := store.get_change_transaction(transaction_id))
                is not None
                and transaction.run_id == run.id
            ),
            None,
        )
        if (
            run.state
            in {
                RunState.APPROVED_PENDING_EXECUTION,
                RunState.EXECUTING,
                RunState.VERIFYING,
            }
            or durable_mutation_stage is not None
            # finish_change_execution durably terminalizes before the exact
            # target identity refresh and callback tool terminal are recorded.
            # Keep cancellation closed throughout that in-memory execution
            # boundary as well, even though the durable transaction is already
            # COMPLETED/ROLLED_BACK/FAILED for a short interval.
            or active_change_execution is not None
            or run_id in ()
        ):
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
        if not transaction.target.same_artifact(current_change_target()):
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
        current = store.get_change_transaction(transaction_id)
        if current is None:
            raise HTTPException(status_code=404, detail="change transaction not found")
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

    async def _execute_change_transaction_internal(
        transaction_id: str,
        body: ExecuteChangeTransactionRequestV1,
        *,
        terminalize_preflight_failure: bool = False,
    ) -> ChangeExecutionResponseV1:
        def trusted_workspace_open_failure(
            error: Exception,
        ) -> tuple[str, str] | None:
            """Extract only a typed MCP reason code from the failed open cause."""

            if not isinstance(error, HTTPException) or error.status_code != 409:
                return None
            current: BaseException | None = error
            seen: set[int] = set()
            for _ in range(8):
                if current is None or id(current) in seen:
                    break
                seen.add(id(current))
                if isinstance(current, MCPToolRejectedError):
                    payload = current.mcp_error_payload
                    error_payload = payload.get("error") if isinstance(payload, dict) else None
                    reason_code = (
                        error_payload.get("code")
                        if isinstance(error_payload, dict)
                        else None
                    )
                    if (
                        isinstance(payload, dict)
                        and payload.get("schema_version") == "1.0"
                        and payload.get("status") == "error"
                        and isinstance(reason_code, str)
                        and re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", reason_code)
                    ):
                        # The Host only knows that its verified-workspace open
                        # step failed; do not infer a lower-level native phase.
                        return reason_code, "workspace_open"
                    return None
                current = current.__cause__ or current.__context__
            return None

        def terminal_preflight_response() -> ChangeExecutionResponseV1:
            try:
                failed, recorded = store.fail_approved_change_preflight(
                    transaction_id=transaction_id,
                    expected_plan_digest=body.expected_plan_digest,
                )
            except ChangeStoreConflict as conflict:
                raise HTTPException(status_code=409, detail=str(conflict)) from conflict
            if failed is None:
                raise HTTPException(
                    status_code=404,
                    detail="change transaction not found",
                )
            if recorded:
                publish_change_event("change.transaction.updated", failed)
            return ChangeExecutionResponseV1(
                transaction=_change_transaction_v1(failed),
                execution_started=False,
            )

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
            if terminalize_preflight_failure:
                return terminal_preflight_response()
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
            if terminalize_preflight_failure:
                return terminal_preflight_response()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            if terminalize_preflight_failure:
                logger.error(
                    "direct change transaction %s could not acquire its target lease "
                    "(exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
                )
                return terminal_preflight_response()
            raise
        try:
            execution_target = current_change_target()
            if not transaction.target.same_artifact(execution_target):
                raise ChangeExecutionPreconditionError(
                    "active workspace artifact differs from the approved artifact"
                )
            if (
                execution_target.engineering_session_id is None
                or execution_target.engineering_process_id is None
                or execution_target.engineering_window_handle is None
            ):
                raise ChangeExecutionPreconditionError("execution requires a verified current session")
            await change_executor.preflight(
                transaction.model_copy(update={"execution_target": execution_target})
            )
        except ChangeExecutionPreconditionError as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_preflight_failed",
            )
            if terminalize_preflight_failure:
                return terminal_preflight_response()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except asyncio.CancelledError:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_preflight_interrupted",
            )
            if terminalize_preflight_failure:
                terminal_preflight_response()
            raise
        except Exception as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_preflight_interrupted",
            )
            preflight_diagnostic = trusted_workspace_open_failure(exc)
            if preflight_diagnostic is not None and not terminalize_preflight_failure:
                reason_code, stage = preflight_diagnostic
                try:
                    recorded_transaction, recorded = (
                        store.record_approved_change_preflight_failure(
                            transaction_id=transaction_id,
                            expected_plan_digest=body.expected_plan_digest,
                            reason_code=reason_code,
                            stage=stage,
                        )
                    )
                except Exception as record_error:
                    logger.warning(
                        "could not persist approved change preflight receipt "
                        "(exception_type=%s)",
                        type(record_error).__name__,
                    )
                else:
                    if recorded and recorded_transaction is not None:
                        try:
                            publish_change_event(
                                "change.transaction.updated",
                                recorded_transaction,
                            )
                        except Exception as event_error:
                            logger.warning(
                                "could not publish approved change preflight receipt "
                                "(exception_type=%s)",
                                type(event_error).__name__,
                            )
            if terminalize_preflight_failure:
                logger.error(
                    "direct change transaction %s failed before native execution "
                    "(exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
                )
                return terminal_preflight_response()
            raise
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
                execution_target=execution_target,
            )
        except MutationLeaseConflict as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="durable_mutation_lease_conflict",
            )
            if terminalize_preflight_failure:
                return terminal_preflight_response()
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
            if terminalize_preflight_failure:
                return terminal_preflight_response()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            store.release_target_operation_lease(
                operation_id=transaction_id,
                holder_token=holder_token,
                reason="change_execution_not_started",
            )
            if terminalize_preflight_failure:
                logger.error(
                    "direct change transaction %s could not enter execution "
                    "(exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
                )
                return terminal_preflight_response()
            raise
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
                    error_detail=_native_preparation_failure_detail(exc.evidence),
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
                    error_detail=(
                        "Native change execution ended with an uncertain outcome; "
                        "private provider detail was suppressed."
                    ),
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
                    error_detail=(
                        "Native apply failed unexpectedly; private provider detail "
                        f"was suppressed ({type(exc).__name__})."
                    ),
                )
                assert quarantined is not None
                publish_change_event("change.transaction.updated", quarantined)
                logger.error(
                    "change transaction %s apply failed (exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
                )
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
                    error_detail=(
                        "Native verification ended without a certain outcome; private "
                        f"provider detail was suppressed ({type(exc).__name__})."
                    ),
                )
                assert quarantined is not None
                publish_change_event("change.transaction.updated", quarantined)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                logger.error(
                    "change transaction %s verification failed (exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
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
        "/api/v1/change-transactions/{transaction_id}/execute",
        response_model=ChangeExecutionResponseV1,
    )
    async def execute_change_transaction_v1(
        transaction_id: str,
        body: ExecuteChangeTransactionRequestV1,
        request: Request,
        _run_guard: None = Depends(engineering_target_run_guard),
    ) -> ChangeExecutionResponseV1:
        response = await _execute_change_transaction_internal(transaction_id, body)
        if response.transaction.state == ChangeTransactionState.ROLLED_BACK.value:
            pass
        return response


    @app.post(
        "/api/v1/xg5000/close-idle-instance",
        response_model=CloseIdleXG5000InstanceResponseV1,
    )
    async def close_idle_xg5000_window_v1(
        body: CloseIdleXG5000RequestV1,
        request: Request,
        _csrf: None = Depends(require_browser_csrf),
        _target_lease: None = Depends(target_switch_lease),
    ) -> CloseIdleXG5000InstanceResponseV1:
        """Close one user-selected blank window while preserving the selected target."""
        if active_change_executions:
            raise HTTPException(status_code=409, detail="작업이 진행 중이어서 빈 창을 닫을 수 없습니다.")
        session = active_engineering_session
        if (isinstance(session, dict) and session.get("pid") == body.pid
                and session.get("hwnd") == body.hwnd):
            raise HTTPException(status_code=409, detail="현재 연결된 작업 창은 빈 창 정리로 닫을 수 없습니다.")
        arguments = {
            "pid": body.pid, "hwnd": body.hwnd,
            "process_create_time": body.process_create_time,
            "confirm_idle_close": True, "timeout_seconds": body.timeout_seconds,
        }
        try:
            # Keep started managed input joined before the dependency releases
            # its operation lease, including a cancelled HTTP waiter.
            raw = await native_checkpoint_boundary(mcp.call_workspace_tool("xg5000.close_idle_instance", arguments))
            if not isinstance(raw, dict):
                raise TypeError("MCP idle-instance close receipt is not an object")
            digest = _canonical_json_digest(raw)
            outcome = (_close_idle_xg5000_instance_attention(raw, receipt_digest=digest)
                if raw.get("status") == "needs_attention"
                else _close_idle_xg5000_instance_response(raw, receipt_digest=digest))
            binding = outcome.idle_binding
            if (binding.get("pid") != body.pid or binding.get("hwnd") != body.hwnd
                    or type(binding.get("process_create_time")) not in (int, float)
                    or not math.isfinite(binding["process_create_time"])
                    or abs(binding["process_create_time"] - body.process_create_time) >= 0.001
                    or not isinstance(binding.get("executable"), str) or not binding["executable"]):
                raise ValueError("MCP idle-instance close receipt binding does not match")
            attention = isinstance(outcome, CloseIdleXG5000InstanceAttentionV1)
            store.append_event(
                event_type=("xg5000.idle_instance_close_needs_attention" if attention else "xg5000.idle_instance_closed"),
                payload={"actorId": _request_actor_id(request), "idleBinding": dict(binding),
                    "receiptDigest": outcome.receipt_digest, "closeAction": outcome.close_action,
                    "noSavePromptClicked": False, "processTerminated": False},
            )
            if attention:
                raise HTTPException(status_code=409, detail={
                    "code": "idle_xg5000_needs_attention",
                    "message": "빈 창 닫기 결과를 확인하지 못했습니다. 창 목록을 다시 확인해 주세요.",
                    "attentionReason": outcome.attention_reason,
                    "processExited": False, "processTerminated": False,
                })
            return outcome
        except MCPToolError as exc:
            raise HTTPException(status_code=409,
                detail="빈 창 닫기 결과를 확인하지 못했습니다. 창 목록을 다시 확인해 주세요.") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="닫기 응답을 검증하지 못했습니다. 창 목록을 다시 확인해 주세요.") from exc

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
        prior_reconciliation = transaction.result.get("reconciliation")
        relaunch_evidence = (
            prior_reconciliation.get("recovery_target_relaunch")
            if isinstance(prior_reconciliation, dict) else None
        )
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
                getattr(current_target, field_name) != getattr(transaction.runtime_target, field_name)
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
                recovery_target, recovery_working_hash, relaunch_evidence = await _relaunch_unavailable_change_recovery_target(
                    mcp,
                    transaction,
                    recovery_workspace,
                    requested_pid=body.pid,
                    requested_hwnd=body.hwnd,
                )
                if not isinstance(active_workspace, dict):
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace disappeared after verified relaunch"
                    )
                refreshed_workspace = dict(active_workspace)
                refreshed_working_copy = active_workspace.get("working_copy")
                if not isinstance(refreshed_working_copy, dict):
                    raise ChangeExecutionPreconditionError(
                        "active recovery workspace has no working-copy identity after relaunch"
                    )
                refreshed_working_copy = dict(refreshed_working_copy)
                refreshed_working_copy["current_sha256"] = recovery_working_hash
                refreshed_working_copy["state"] = (
                    "clean"
                    if recovery_working_hash
                    == str(refreshed_working_copy.get("baseline_sha256") or "").casefold()
                    else "modified"
                )
                refreshed_workspace["working_copy"] = refreshed_working_copy
                active_engineering_session = None
                execution_broker.clear_agent_session()
                active_workspace = refreshed_workspace
                store.set_host_state("active_workspace", active_workspace)
                bind_agent_workspace_target(active_workspace)
                # Persist the validated launch before any further native await.
                # Cancellation or a Host restart must not orphan this window.
                retained_evidence = dict(prior_reconciliation or {})
                retained_evidence["recovery_target_relaunch"] = relaunch_evidence
                if restoration_receipt is not None:
                    retained_evidence["working_copy_restore"] = restoration_receipt
                persisted = store.resolve_quarantined_change(
                    transaction_id=transaction_id,
                    state=ChangeTransactionState.QUARANTINED,
                    evidence=retained_evidence,
                    actor_id=_request_actor_id(request),
                    error_code=transaction.error_code,
                    error_detail=transaction.error_detail,
                    held_target_lease_token=holder_token,
                )
                if persisted is None:
                    raise ChangeExecutionPreconditionError("recovery transaction disappeared after launch")
                transaction = persisted
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
                logger.error(
                    "change transaction %s reconciliation failed (exception_type=%s)",
                    transaction_id,
                    type(exc).__name__,
                )
            if outcome is not None and restoration_receipt is not None:
                outcome.evidence["working_copy_restore"] = restoration_receipt
            if outcome is not None and relaunch_evidence is not None:
                outcome.evidence["recovery_target_relaunch"] = relaunch_evidence
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
                        **({"recovery_target_relaunch": relaunch_evidence}
                           if relaunch_evidence is not None else {}),
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
            response = ChangeReconciliationResponseV1(
                transaction=_change_transaction_v1(resolved),
                reconciled=resolved.state is not ChangeTransactionState.QUARANTINED,
            )
        finally:
            active_change_executions.discard(transaction_id)
        if resolved is not None and resolved.state is ChangeTransactionState.ROLLED_BACK:
            pass
        assert response is not None
        return response

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
                if events:
                    await asyncio.sleep(0)
                    continue
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
            existing = _existing_model_settings_for_write(model_repository, body)
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
                reasoning_effort=candidate.reasoning_effort,
                **(
                    {"client_input_limit_tokens": candidate.client_input_limit_tokens}
                    if "client_input_limit_tokens" in body.model_fields_set
                    else {}
                ),
                api_key=candidate.secrets.api_key,
                custom_headers=candidate.secrets.custom_headers,
                capability_verified_at=result.tested_at,
                model_context=result.model_context,
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
            model_context=result.model_context,
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

        return await import_knowledge_file(Path(selected_path), body)

    async def import_knowledge_file(selected_path: Path, body: ImportKnowledgeRequestV1) -> KnowledgeImportResultV1:
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

    @app.post("/api/v1/knowledge/import-file", response_model=KnowledgeImportResultV1)
    async def import_uploaded_knowledge_v1(
        request: Request, file_name: str = Query(alias="fileName", min_length=1, max_length=255),
        _csrf: None = Depends(require_browser_csrf),
    ) -> KnowledgeImportResultV1:
        # A browser File is content, never a Host-selected filesystem path.
        if (Path(file_name).name != file_name or any(c in file_name for c in '/\\:')
                or any(ord(c) < 32 for c in file_name) or file_name.endswith((".", " "))
                or Path(file_name).suffix.casefold() not in SUPPORTED_KNOWLEDGE_SUFFIXES
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", file_name)):
            raise HTTPException(status_code=422, detail="Unsupported document filename")
        metadata = dict(request.query_params)
        metadata.pop("fileName", None)
        try:
            body = ImportKnowledgeRequestV1.model_validate(metadata)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Invalid document applicability") from exc
        with tempfile.TemporaryDirectory(prefix="controlstudio-knowledge-") as temporary:
            path = Path(temporary) / file_name
            size = 0
            with path.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_KNOWLEDGE_FILE_BYTES:
                        raise HTTPException(status_code=413, detail="Document exceeds the 100 MB import limit")
                    output.write(chunk)
            if size == 0:
                raise HTTPException(status_code=422, detail="Document is empty")
            return await import_knowledge_file(path, body)

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

    def record_workspace_attach_failure(
        workspace: LocalWorkspaceV1,
        pid: int,
        hwnd: int,
        result: Any,
        *,
        phase: str,
    ) -> None:
        session = result.get("session", result) if isinstance(result, dict) else {}
        store.append_event(
            event_type="workspace.attach_unverified",
            payload={
                "workspace_id": workspace.workspace_id,
                "revision_id": workspace.revision_id,
                "pid": pid,
                "hwnd": hwnd,
                "phase": phase,
                "strong_identity_diagnostic": _safe_identity_diagnostic(
                    session if isinstance(session, dict) else {}
                ),
            },
        )

    async def revalidate_native_workspace_binding(workspace: LocalWorkspaceV1) -> dict[str, Any] | None:
        """Reconnect native-only authority; never recover cached MCP/COM readiness."""
        binding = retained_native_workspace_binding(workspace.model_dump(mode="json"))
        if not isinstance(binding, dict):
            return None
        anchor = binding.get("session")
        current_workspace = workspace.model_dump(mode="json")
        if (not isinstance(anchor, dict)
                or any(binding.get(key) != current_workspace.get(key) for key in ("workspace_id", "revision_id"))):
            # A candidate for another workspace must not retire the selected anchor.
            return None
        pid, hwnd = anchor.get("pid"), anchor.get("hwnd")
        created = _engineering_session_process_create_time(anchor)

        async def exact_instance() -> dict[str, Any] | None:
            result = await mcp.call_read_tool("xg5000.instances", {})
            instances = result.get("instances") if isinstance(result, dict) else None
            if not isinstance(instances, list):
                raise MCPToolError("Native target instances were not observed")
            candidates = [item for item in instances if isinstance(item, dict)
                          and (item.get("pid") == pid or item.get("hwnd") == hwnd)]
            if not candidates:
                return None
            if (len(candidates) != 1 or candidates[0].get("pid") != pid
                    or candidates[0].get("hwnd") != hwnd or candidates[0].get("visible") is not True):
                raise MCPToolError("The retained native target was not exactly observed")
            lifetime = candidates[0].get("process_create_time")
            if (created is None or type(lifetime) not in {int, float}
                    or not math.isfinite(lifetime) or abs(lifetime - created) >= 0.001):
                raise MCPToolError("The retained native process lifetime changed")
            return candidates[0]

        phase = "exact_instance"
        try:
            if await exact_instance() is None:
                update_native_workspace_binding(closed_target=(pid, hwnd), closed_process_create_time=created)
                return None
            # A fresh strong attach proves the current saved identity independently
            # of the old cache; the native-only fallback still requires its lineage.
            phase = "stable_working_file"
            before = saved_project_observation(Path(workspace.working_copy.path))
            if before.get("stable_during_read") is not True or before.get("sha256") != workspace.working_copy.current_sha256:
                raise MCPToolError("The native working file was not stably observed")
            phase = "strong_attach"
            attached = await mcp.call_read_tool("xg5000.attach", {
                "pid": pid, "hwnd": hwnd, "access_profile": "analysis",
                "workspace_id": workspace.workspace_id,
                "project_path": workspace.working_copy.path,
                "project_hash": workspace.working_copy.current_sha256,
            })
            validated = _validated_engineering_session_result(attached, pid=pid, hwnd=hwnd,
                project_path=workspace.working_copy.path, project_hash=workspace.working_copy.current_sha256)
            if validated is not None:
                phase = "strong_attach_stability"
                if (await exact_instance() is None
                        or _engineering_session_process_create_time(validated) is None
                        or abs(_engineering_session_process_create_time(validated) - created) >= 0.001):
                    raise MCPToolError("The native target changed during strong revalidation")
                after = saved_project_observation(Path(workspace.working_copy.path))
                if (after.get("stable_during_read") is not True
                        or any(after.get(key) != before.get(key) for key in ("sha256", "size", "mtime_ns"))):
                    raise MCPToolError("The working file changed during strong revalidation")
                return validated
            record_workspace_attach_failure(workspace, pid, hwnd, attached, phase="native_binding_revalidation")
            raw_session = attached.get("session", attached) if isinstance(attached, dict) else {}
            diagnostic = _safe_identity_diagnostic(raw_session if isinstance(raw_session, dict) else {})
            # This is the observed save-after-file-use-loss boundary, not a
            # blanket exemption for any failed identity predicate.
            if (diagnostic.get("native_file_stage") != "file_use"
                    or diagnostic.get("native_file_code") != "SELECTED_PID_FILE_USE_NOT_OBSERVED"):
                raise MCPToolError("Native target title/path/hash revalidation did not pass")
            phase = "retained_native_lineage"
            if not _native_ld_edit_ready(anchor, current_workspace):
                raise MCPToolError("The retained native workspace/hash lineage differs")
            phase = "native_only_observation"
            observed_session = _engineering_session_v1(raw_session)
            raw = await mcp.call_workspace_tool("xg5000.editor", {"action": "observe", "pid": pid, "hwnd": hwnd})
            scene = raw.get("observation") if isinstance(raw, dict) else None
            if (not isinstance(raw, dict) or raw.get("ok") is not True or not isinstance(scene, dict)
                    or scene.get("pid") != pid or scene.get("hwnd") != hwnd or scene.get("main_hwnd") != hwnd):
                raise MCPToolError("The exact native editor was not observed")
            if await exact_instance() is None:
                raise MCPToolError("The native target disappeared during revalidation")
            after = saved_project_observation(Path(workspace.working_copy.path))
            if (after.get("stable_during_read") is not True
                    or any(after.get(key) != before.get(key) for key in ("sha256", "size", "mtime_ns"))):
                raise MCPToolError("The working file changed during native revalidation")
            session_id = str(uuid.uuid4())
            metadata = {"project_identity_assurance": "unverified",
                        "native_ld_host_revalidation": {"binding": binding, "observed": {
                            "method": "same_live_native_observe", "pid": pid, "hwnd": hwnd,
                            "main_hwnd": hwnd, "process_create_time": created,
                            "project_path": anchor["project_path"], "project_hash": anchor["project_hash"],
                        }}}
            saved = anchor.get("metadata", {}).get("native_ld_saved_observation")
            if isinstance(saved, dict):
                metadata["native_ld_saved_observation"] = {**deepcopy(saved), "session_id": session_id}
            return {"session_id": session_id, "pid": pid, "hwnd": hwnd,
                    "stage": observed_session.stage,
                    "project_path": anchor["project_path"], "project_hash": anchor["project_hash"],
                    "plc_policy": "read_only_unverified", "metadata": metadata}
        except (MCPToolError, OSError, ValueError) as exc:
            store.append_event(event_type="workspace.native_binding_revalidation_failed", payload={
                "workspace_id": workspace.workspace_id, "revision_id": workspace.revision_id,
                "phase": phase, "exception_kind": type(exc).__name__,
            })
            raise HTTPException(status_code=409,
                detail="기존 native 편집 대상을 재검증하지 못했습니다. 작업 사본과 기존 창은 보존했습니다.") from exc

    def workspace_candidate_session(result: Any) -> dict[str, Any]:
        """Validate a staged result without selecting it or inheriting old authority."""
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
        return dict(session)

    def bind_engineering_session(result: Any) -> dict[str, Any]:
        nonlocal active_engineering_session
        candidate = workspace_candidate_session(result)
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
                str(Path(str(candidate.get("project_path") or "")).resolve())
            )
            and str(working_copy.get("current_sha256") or "").casefold()
            == str(candidate.get("project_hash") or "").casefold()
        ):
            bound_workspace_id = str(workspace["workspace_id"])
        broker_session = None
        if (_engineering_session_read_ready(candidate)
                or _native_ld_edit_ready(candidate, workspace)):
            broker_session = dict(candidate)
            if bound_workspace_id is not None:
                broker_session.update(workspace_agent_binding(workspace))
        # Validate the complete projection before any mandatory publication.
        # Retained lineage remains evidence; live readiness is published only
        # after its required persistence and broker binding both succeed.
        update_native_workspace_binding(candidate)
        if broker_session is not None:
            execution_broker.bind_agent_session(broker_session)
        elif bound_workspace_id is not None:
            bind_agent_workspace_target(workspace)
        else:
            execution_broker.clear_agent_session()
        active_engineering_session = candidate
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
        if isinstance(exc, MCPToolRejectedError):
            error_payload = exc.mcp_error_payload
            error = error_payload.get("error") if isinstance(error_payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,120}", code):
                detail = f"XG5000 MCP rejected the native operation ({code})."
            else:
                detail = "XG5000 MCP rejected the native operation."
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

        detail = str(exc)
        unavailable = "MCP is not ready" in detail or "disconnected" in detail
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE if unavailable else status.HTTP_409_CONFLICT
            ),
            detail=(
                "XG5000 MCP 연결이 끊겨 프로그램 작업을 완료하지 못했습니다."
                if unavailable
                else "XG5000 MCP에서 engineering 작업을 완료하지 못했습니다."
            ),
        )

    def current_native_program_scope() -> dict[str, Any] | None:
        if not _native_ld_edit_ready(active_engineering_session, active_workspace):
            return None
        session = deepcopy(active_engineering_session)
        binding = workspace_agent_binding(active_workspace)
        identity = mcp.agent_session_identity_snapshot
        created = _engineering_session_process_create_time(session)
        selected = {**binding, **{key: session[key] for key in
            ('session_id', 'pid', 'hwnd', 'project_path', 'project_hash')}}
        if (not isinstance(identity, dict) or any(identity.get(key) != value for key, value in selected.items())
                or type(created) not in (int, float) or not math.isfinite(created) or created <= 0):
            raise HTTPException(status_code=409, detail="현재 작업 사본의 프로그램 대상을 확인하지 못했습니다.")
        selected['process_create_time'] = created
        digest = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        return {'binding': binding, 'identity': identity, 'created': created, 'digest': digest}

    def assert_native_program_scope(scope: dict[str, Any]) -> None:
        current = current_native_program_scope()
        if current != scope:
            raise HTTPException(status_code=409, detail="프로그램 목록의 작업 대상이 바뀌었습니다. 목록을 새로 확인해 주세요.")

    def native_workspace_programs_from_mcp(raw: Any, scope: dict[str, Any]) -> list[EngineeringProgramV1]:
        binding = scope['binding']
        index = raw.get('structure_index') if isinstance(raw, dict) else None
        project = index.get('project_binding') if isinstance(index, dict) else None
        source = project.get('source_receipt') if isinstance(project, dict) else None
        sections = index.get('sections') if isinstance(index, dict) else None
        units = sections.get('code_units') if isinstance(sections, dict) else None
        coverage = units.get('coverage') if isinstance(units, dict) else None
        items = units.get('items') if isinstance(units, dict) else None
        catalog = raw.get('program_catalog') if isinstance(raw, dict) else None
        def digest(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        if (not isinstance(raw, dict) or raw.get('operation') != 'workspace.structure_index'
                or raw.get('schema_version') != '1.0' or raw.get('schema') != 'xg5000.workspace-structure-index/1.0'
                or raw.get('workspace_binding') != binding
                or raw.get('receipt_digest') != digest({k:v for k,v in raw.items() if k not in {'operation','schema_version','receipt_digest'}})
                or not isinstance(index, dict) or index.get('schema') != 'xg5000.project.structure-index/1.0'
                or index.get('read_only') is not True or index.get('status') not in {'complete','partial'}
                or index.get('index_digest') != digest({k:v for k,v in index.items() if k != 'index_digest'})
                or not isinstance(project, dict) or project.get('project_sha256') != binding['working_sha256']
                or os.path.normcase(str(Path(str(project.get('project_path') or '')).resolve()))
                   != os.path.normcase(str(Path(binding['working_path']).resolve()))
                or not isinstance(source, dict) or source.get('observed') is not True
                or source.get('project_sha256') != binding['working_sha256']
                or not isinstance(items, list) or not isinstance(coverage, dict)
                or any(type(coverage.get(k)) is not int for k in ('count','total','returned','offset','limit'))
                or coverage['offset'] != 0 or coverage['limit'] != 500
                or coverage['returned'] != len(items) or coverage['count'] != len(items) or coverage['total'] != len(items)
                or len(items) > 500 or coverage.get('truncated') is not False or coverage.get('next_offset') is not None
                or not isinstance(catalog, dict) or catalog.get('complete') is not True or catalog.get('read_only') is not True
                or catalog.get('live_editor_catalog') is not False
                or catalog.get('catalog_source') != 'sha_bound_project_structure_index'
                or catalog.get('project_sha256') != binding['working_sha256'] or not isinstance(catalog.get('programs'), list)
                or type(catalog.get('program_count')) is not int or catalog['program_count'] != len(catalog['programs'])):
            raise ValueError('The complete saved program catalog does not match its current workspace proof')
        programs = [engineering_program_from_mcp(item) for item in catalog['programs']]
        names = [item.get('name') for item in items if isinstance(item, dict)
            and item.get('code_unit_type') == 'Program' and item.get('language') == 'LD'
            and isinstance(item.get('name'), str) and item['name'].strip()]
        if ([p.name for p in programs] != sorted(names, key=str.casefold)
                or any(p.language != 'LD' for p in programs)
                or len({p.name.casefold() for p in programs}) != len(programs)
                or len({p.program_id for p in programs}) != len(programs)):
            raise ValueError('The saved program catalog identities are incomplete or ambiguous')
        return programs

    async def read_current_native_programs(scope: dict[str, Any]) -> list[EngineeringProgramV1]:
        assert_native_program_scope(scope)
        raw = await mcp.call_read_tool('workspace.structure_index', {'offset': 0, 'limit': 500})
        assert_native_program_scope(scope)
        try:
            return native_workspace_programs_from_mcp(raw, scope)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="저장된 프로그램 목록의 완전한 근거를 확인하지 못했습니다.") from exc

    async def admit_native_program_ui(scope: dict[str, Any]) -> None:
        assert_native_program_scope(scope)
        target = scope['identity']
        saved = await asyncio.to_thread(saved_project_observation, Path(target['project_path']))
        instances = await mcp.call_read_tool('xg5000.instances', {})
        candidates = [item for item in instances.get('instances', []) if isinstance(item, dict)
            and (item.get('pid') == target['pid'] or item.get('hwnd') == target['hwnd'])] if isinstance(instances, dict) else []
        if (saved.get('stable_during_read') is not True or saved.get('sha256') != scope['binding']['working_sha256']
                or len(candidates) != 1 or candidates[0].get('pid') != target['pid']
                or candidates[0].get('hwnd') != target['hwnd'] or candidates[0].get('visible') is not True
                or type(candidates[0].get('process_create_time')) not in (int, float)
                or candidates[0]['process_create_time'] != scope['created']):
            raise HTTPException(status_code=409, detail="프로그램을 열 현재 XG5000 작업 대상을 확인하지 못했습니다.")
        assert_native_program_scope(scope)

    async def open_current_native_program(scope: dict[str, Any], program: EngineeringProgramV1) -> EngineeringProgramFocusV1:
        operation_id = uuid.uuid4().hex
        actual = []
        def record(name, arguments, value):
            actual.append(deepcopy(value))
            store.set_host_state('native_program_ui_receipt:' + operation_id, {
                'schema_version': '1', 'action': 'open_program', 'catalog_binding_digest': scope['digest'],
                'tool_name': name, 'arguments': deepcopy(arguments), 'result': deepcopy(value)})
        def current_target():
            current = current_native_program_scope()
            return current['identity'] if current == scope else None
        def reject_save(_receipt):
            raise MCPToolError('Program UI opening cannot save or rotate a binding')
        owner = NativeWorkspaceExecution(gateway=mcp, target=scope['identity'], current_target=current_target,
            current_process_create_time=lambda: scope['created'] if current_target() is not None else None,
            expected_process_create_time=scope['created'], record_raw=record, refresh_saved=reject_save,
            program_open_binding={
                'workspace_id': scope['binding']['workspace_id'], 'revision_id': scope['binding']['revision_id'],
                'project_path': scope['identity']['project_path'], 'project_sha256': scope['binding']['working_sha256'],
                'process_create_time': scope['created']})
        result = await execution_broker.open_native_ui_program(owner, program.name,
            admission=lambda: admit_native_program_ui(scope))
        assert_native_program_scope(scope)
        raw = actual[-1] if actual and isinstance(actual[-1], dict) else {}
        opened = raw.get('program_activation')
        scene = raw.get('observation')
        active = opened.get('program') if isinstance(opened, dict) else None
        editor = opened.get('editor') if isinstance(opened, dict) else None
        activation = opened.get('activation') if isinstance(opened, dict) else None
        file_binding = raw.get('program_open_file_binding')
        target = scope['identity']
        if (not isinstance(result, dict) or result.get('ok') is not True or result.get('input_state_unknown') is not False
                or result.get('error') or raw.get('ok') is not True or raw.get('read_only') is not True
                or not isinstance(file_binding, dict) or file_binding.get('verified') is not True
                or file_binding.get('before_verified') is not True or file_binding.get('after_verified') is not True
                or not isinstance(opened, dict) or opened.get('verified') is not True or opened.get('exact_program_active') is not True
                or opened.get('pid') != target['pid'] or opened.get('hwnd') != target['hwnd'] or opened.get('program_name') != program.name
                or not isinstance(active, dict) or active.get('name') != program.name or active.get('mdi_hwnd') != opened.get('mdi_hwnd')
                or not isinstance(editor, dict) or type(editor.get('hwnd')) is not int or editor['hwnd'] <= 0
                or active.get('editor_hwnd') != editor['hwnd'] or not isinstance(activation, dict)
                or activation.get('verified') is not True or activation.get('active_after') != editor['hwnd']
                or not isinstance(scene, dict) or scene.get('pid') != target['pid'] or scene.get('hwnd') != target['hwnd']
                or scene.get('main_hwnd') != target['hwnd'] or scene.get('target_is_foreground') is not True
                or scene.get('foreground_hwnd') != target['hwnd'] or scene.get('foreground_pid') != target['pid']):
            from .native_execution_results import native_result_event
            raise HTTPException(status_code=409, detail={
                'message': '프로그램 열기를 확인하지 못했습니다. 현재 XG5000 상태를 확인해 주세요.',
                'scope_verified': True, 'activation_verified': False, 'native_result': native_result_event('native_editor',
                    {'action': 'open_program', 'program_name': program.name}, result)['native_result']})
        return EngineeringProgramFocusV1(verified=True, read_only=True, project_unchanged=True, program=program)

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
        scope = current_native_program_scope()
        if scope is None:
            require_engineering_read_target()
        try:
            if scope is not None:
                programs = await read_current_native_programs(scope)
                return EngineeringProgramsV1(programs=programs, catalog_binding_digest=scope['digest'],
                    catalog_source='saved_working_copy')
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

    @app.post("/api/v1/engineering/symbols/export")
    async def export_engineering_symbols_v1(
        _csrf: None = Depends(require_browser_csrf),
    ) -> dict[str, Any]:
        target = require_engineering_read_target()
        try:
            raw_result = await mcp.call_read_tool(
                "project.export_symbols",
                {"timeout_seconds": 60},
            )
        except MCPToolContractError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="XG5000 MCP returned an invalid symbols export result.",
            ) from exc
        except MCPToolError as exc:
            raise engineering_gateway_error(exc) from exc

        if not isinstance(raw_result, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP returned an invalid XG5000 symbols export result.",
            )
        export_data = raw_result.get("export")
        csv_data = export_data.get("csv") if isinstance(export_data, dict) else None
        required_csv_fields = {
            "sha256",
            "byte_size",
            "encoding",
            "remarks",
            "columns",
            "metadata",
            "row_count",
            "rows",
        }
        if (
            raw_result.get("ok") is not True
            or raw_result.get("verified") is not True
            or raw_result.get("read_only") is not True
            or raw_result.get("operation") != "project.export_symbols"
            or raw_result.get("project_unchanged") is not True
            or type(raw_result.get("pid")) is not int
            or raw_result.get("pid") != target.get("pid")
            or type(raw_result.get("hwnd")) is not int
            or raw_result.get("hwnd") != target.get("hwnd")
            or not isinstance(export_data, dict)
            or export_data.get("verified") is not True
            or not isinstance(csv_data, dict)
            or not required_csv_fields.issubset(csv_data)
            or not isinstance(csv_data.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", csv_data["sha256"]) is None
            or type(csv_data.get("byte_size")) is not int
            or csv_data["byte_size"] < 0
            or not isinstance(csv_data.get("encoding"), str)
            or not isinstance(csv_data.get("columns"), list)
            or not isinstance(csv_data.get("metadata"), dict)
            or type(csv_data.get("row_count")) is not int
            or csv_data["row_count"] < 0
            or not isinstance(csv_data.get("rows"), list)
            or len(csv_data["rows"]) != csv_data["row_count"]
            or any(
                not isinstance(row, list)
                or len(row) != len(csv_data["columns"])
                or any(not isinstance(cell, str) for cell in row)
                for row in csv_data["rows"]
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="MCP did not verify the current-target symbols export.",
            )

        # Preserve the exporter-parsed CSV result, including symbol rows and
        # vendor metadata, without flattening or interpreting it in Host.
        return {
            "ok": True,
            "verified": True,
            "read_only": True,
            "operation": "project.export_symbols",
            "project_unchanged": True,
            "pid": target["pid"],
            "hwnd": target["hwnd"],
            "export": export_data,
        }

    @app.post(
        "/api/v1/engineering/programs/{program_id}/focus",
        response_model=EngineeringProgramFocusV1,
    )
    async def focus_engineering_program_v1(
        program_id: str,
        body: EngineeringProgramFocusRequestV1 | None = None,
        _csrf: None = Depends(require_browser_csrf),
    ) -> EngineeringProgramFocusV1:
        scope = current_native_program_scope()
        if scope is None:
            require_engineering_read_target()
            if body is not None and body.catalog_binding_digest is not None:
                raise HTTPException(status_code=409, detail="프로그램 목록의 작업 대상이 바뀌었습니다. 목록을 새로 확인해 주세요.")
        elif body is None or body.catalog_binding_digest != scope['digest']:
            raise HTTPException(status_code=409, detail="현재 작업 사본의 프로그램 목록을 먼저 새로 확인해 주세요.")
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
            if scope is not None:
                programs = await read_current_native_programs(scope)
                matches = [item for item in programs if item.program_id == normalized_program_id]
                if len(matches) != 1:
                    raise HTTPException(status_code=409, detail="선택한 프로그램이 현재 저장본의 목록과 다릅니다.")
                return await open_current_native_program(scope, matches[0])
            params = {"program_id": normalized_program_id}
            if body is not None:
                params["display_intent"] = body.display_intent
            raw_result = await mcp.call_read_tool(
                "program.focus",
                params,
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

    @asynccontextmanager
    async def join_owned_workspace_launch(workspace, open_arguments, *, binding_wait_identity=None,
                                          expected_pending_frame=None, record_physical_pending=None):
        from .native_recovery_wait import (
            OwnedLaunchMaterialChange,
            RecoveryWaitScopeChanged,
            wait_for_owned_launch_binding,
        )
        request_context = launch_wait_request_context.get()
        own_lease = False
        if request_context is not None:
            operation_id = request_context['operation_id']
            holder = request_context['holder_token']
            original_recovery = request_context.get('recovery')
            source_run_id = original_recovery['source_run_id'] if original_recovery is not None else None
            if original_recovery is not None:
                expected_pending_frame = original_recovery['frame']
        elif isinstance(binding_wait_identity, dict):
            operation_id = binding_wait_identity['operation_id']
            source_run_id = binding_wait_identity['source_run_id']
            if binding_wait_identity.get('reconcile_only'):
                previous_wait = store.get_host_state(f"owned_launch_wait_operation:{operation_id}")
                original_owner = previous_wait.get('owner') if isinstance(previous_wait, dict) else None
                original_scope = original_owner.get('scope') if isinstance(original_owner, dict) else None
                if not isinstance(original_scope, dict):
                    raise MCPToolError('Original launch has no durable Host owner')
                expected_scope = {
                    'target_lease_id': original_scope.get('target_lease_id'),
                    'operation_id': operation_id, 'workspace_id': workspace.workspace_id,
                    'revision_id': workspace.revision_id,
                    'project_path': str(Path(workspace.working_copy.path).resolve()),
                    'project_hash': workspace.working_copy.current_sha256.casefold(),
                    'source_run_id': original_scope.get('source_run_id')}
                if original_owner != {'owner_ref': _canonical_json_digest(expected_scope), 'scope': expected_scope}:
                    raise MCPToolError('Original launch scope differs from this request')
                # Resume transfers the recovery callback, not the original
                # native launch/lease identity. Reclaim that exact parked lease.
                source_run_id = original_scope['source_run_id']
            holder = str(uuid.uuid4())
            store.acquire_target_operation_lease(operation_id=operation_id, owner_kind='workspace_resume',
                holder_token=holder, target_identity={'operation_id':operation_id,'source_run_id':source_run_id,
                    'workspace_id':workspace.workspace_id,'revision_id':workspace.revision_id,
                    'project_path':_normalized_workspace_path(workspace.working_copy.path),
                    'project_hash':workspace.working_copy.current_sha256})
            own_lease = True
        else:
            # Existing callers without a durable Host owner retain old cleanup.
            yield await mcp.call_workspace_tool('xg5000.open_project', open_arguments), lambda: None
            return
        state = None
        request_failed = False
        try:
            target_lease=store.active_target_operation_lease()
            if not isinstance(target_lease,dict) or target_lease.get('operation_id')!=operation_id or target_lease.get('holder_token')!=holder:
                raise MCPToolError('Original launch has no current Host owner')
            exclusion_lease_id = target_lease['id']
            scope={'target_lease_id':target_lease['id'],'operation_id':operation_id,'workspace_id':workspace.workspace_id,
                'revision_id':workspace.revision_id,'project_path':str(Path(workspace.working_copy.path).resolve()),
                'project_hash':workspace.working_copy.current_sha256.casefold(),'source_run_id':source_run_id}
            owner={'owner_ref':_canonical_json_digest(scope),'scope':scope}
            key=f"owned_launch_wait_operation:{operation_id}"
            previous=store.get_host_state(key)
            retirement_only = False
            if isinstance(previous, dict) and previous.get('state') == 'ready' and request_context is not None:
                prior_owner = previous.get('owner')
                prior_scope = prior_owner.get('scope') if isinstance(prior_owner, dict) else None
                if (isinstance(prior_scope, dict)
                        and prior_owner.get('owner_ref') == _canonical_json_digest(prior_scope)
                        and prior_scope.get('operation_id') == operation_id
                        and prior_scope.get('source_run_id') is None
                        and isinstance(previous.get('exclusion_lease_id'), str)
                        and previous['exclusion_lease_id'] != exclusion_lease_id):
                    # Admission completed and its request lease was released.
                    # A new HTTP lease starts a new native launch generation,
                    # even when route/body and the saved file are unchanged.
                    previous = None
            if previous is not None and (not isinstance(previous,dict) or previous.get('owner')!=owner):
                if isinstance(previous,dict) and previous.get('state') in {'original_retired','not_started'}:
                    previous=None
                elif isinstance(previous,dict):
                    # The original native generation is immutable until retired.
                    # A new request mutex may only reobserve the same business scope.
                    native_owner = previous.get('owner')
                    native_scope = native_owner.get('scope') if isinstance(native_owner,dict) else None
                    if (not isinstance(native_scope,dict) or set(native_scope)!=set(scope)
                            or native_owner.get('owner_ref')!=_canonical_json_digest(native_scope)
                            or any(native_scope[k]!=v for k,v in scope.items()
                                   if k not in {'target_lease_id','project_hash'})):
                        raise MCPToolError('Original launch scope differs from this request')
                    if native_scope['project_hash'] != scope['project_hash']:
                        if request_context is None:
                            raise MCPToolError('Original launch scope differs from this request')
                        # The immutable saved scope can still identify its retired
                        # process. It cannot admit a binding for a changed file.
                        retirement_only = True
                    owner=deepcopy(native_owner)
                else:
                    raise MCPToolError('Original launch wait scope differs from this request')
            state=deepcopy(previous) if previous is not None else {'owner':owner,'state':'reserved','pending':None}
            state['exclusion_lease_id'] = exclusion_lease_id
            def persist_wait():
                store.set_host_state(f"owned_launch_wait:{owner['owner_ref']}",state)
                store.set_host_state(key,state)
            persist_wait()
            force_observe=previous is not None or bool(binding_wait_identity and binding_wait_identity.get('reconcile_only'))
            first=True

            async def validate_scope():
                store.heartbeat_target_operation_lease(operation_id=operation_id,holder_token=holder)
                lease=store.active_target_operation_lease()
                saved=store.get_host_state(key)
                if (not isinstance(lease,dict) or lease.get('operation_id')!=operation_id
                        or lease.get('holder_token')!=holder or lease.get('id')!=exclusion_lease_id or not isinstance(saved,dict) or saved.get('owner')!=owner):
                    raise RecoveryWaitScopeChanged('Original launch wait owner changed')
                return owner

            async def disconnected():
                return shutdown_event.is_set() or (request_context is not None
                    and await request_context['request'].is_disconnected())

            def record_pending(observed):
                if expected_pending_frame is not None and any(observed.get(k)!=expected_pending_frame.get(k)
                        for k in ('pid','hwnd','process_create_time')):
                    raise RecoveryWaitScopeChanged('Original pending launch frame changed')
                state.update(state=observed['state'],pending=deepcopy(observed))
                persist_wait()
                if record_physical_pending is not None:
                    record_physical_pending(observed)

            async def observe():
                nonlocal first
                pending=state.get('pending') or {}
                action='observe' if force_observe or not first else 'launch'
                now=datetime.now(timezone.utc)
                claims={'owner':owner,'action':action,'lease_ref':pending.get('lease_ref'),
                    'launch_operation_id':pending.get('launch_operation_id'),
                    'issued_at':now.isoformat(),'expires_at':(now+timedelta(minutes=2)).isoformat()}
                meta={'controlstudio/launch-binding-wait':mcp.sign_mutation_authorization(claims)}
                arguments=dict(open_arguments)
                if retirement_only:
                    arguments['expected_working_hash'] = owner['scope']['project_hash']
                if action=='observe':arguments['timeout_seconds']=LATE_WORKSPACE_RECONCILE_SECONDS
                first=False
                return await mcp.call_workspace_tool('xg5000.open_project',arguments,private_meta=meta)
            try:
                result=await wait_for_owned_launch_binding(owner,validate_scope=validate_scope,
                    observe_binding=observe,record_pending=record_pending,request_disconnected=disconnected)
                if retirement_only:
                    raise MCPToolError('Original launch saved scope changed; only its retirement may be reconciled')
                frame=state.get('pending') or expected_pending_frame
                if isinstance(frame,dict) and all(k in frame for k in ('pid','hwnd','process_create_time')):
                    instance=result.get('instance')
                    if (not isinstance(instance,dict) or result.get('launch_pid')!=frame['pid']
                            or instance.get('hwnd')!=frame['hwnd'] or result.get('process_create_time')!=frame['process_create_time']):
                        raise RecoveryWaitScopeChanged('Original launch completion frame changed')
                # A native binding receipt is not yet the common Open's validated
                # attach/admission. Keep ownership through that caller's await.
                state.update(state='binding_ready');persist_wait()
                def admitted():
                    state.update(state='ready');persist_wait()
                yield result, admitted
            except OwnedLaunchMaterialChange as change:
                material=change.observation['state']
                raise HTTPException(status_code=409,detail={
                    'kind':'workspace_recovery_pending','phase':'launch_pending',
                    'operation_id':operation_id,'material_state':material,
                    'native_binding_available':False,'automatic_replay':False,
                    'message':'XG5000 대화상자 또는 원래 실행 상태를 확인한 뒤 같은 작업에 다시 연결하세요.'}) from change
            except asyncio.CancelledError:
                # Navigation/tab disconnect and existing run cancellation park this
                # same operation. Neither returns native authority or kills the GUI.
                state.update(state='not_started' if first and not force_observe else 'parked');persist_wait()
                raise
            except Exception as error:
                # The backend can return exact retirement as a structured
                # failure before any pending-frame observation was available.
                # Retain that terminal proof instead of parking a dead launch.
                from .store import verified_owned_launch_retirement
                cause=error
                for _ in range(4):
                    payload=getattr(cause,'mcp_error_payload',None)
                    rejection=payload.get('error') if isinstance(payload,dict) else None
                    failure=rejection.get('nativeFailure') if isinstance(rejection,dict) else None
                    proof=verified_owned_launch_retirement(failure)
                    frame=state.get('pending')
                    if proof is not None and (frame is None or isinstance(frame,dict)
                            and frame.get('pid')==proof['launched_pid']
                            and frame.get('process_create_time')==proof['process_create_time']):
                        state.update(state='original_retired',terminal_failure=deepcopy(failure));persist_wait()
                        break
                    cause=getattr(cause,'__cause__',None)
                    if cause is None:break
                if state.get('state')!='original_retired':
                    state.update(state='parked');persist_wait()
                raise
        except BaseException:
            request_failed = True
            raise
        finally:
            if own_lease:
                try:
                    if state is None or owned_launch_wait_requires_park(state):
                        store.park_target_operation_lease(operation_id=operation_id,holder_token=holder,
                            reason='owned_launch_binding_wait_requires_read_only_rejoin')
                    else:
                        store.release_target_operation_lease(operation_id=operation_id,holder_token=holder,
                            reason='owned_launch_binding_wait_finished')
                except Exception:
                    if not request_failed:
                        raise
                    logger.warning('Owned launch lease finalization failed; original request failure preserved')

    async def complete_workspace_launch(workspace, raw_launch, *, independent_from_pid,
                                        require_controlled_launch, candidate_only):
        nonlocal active_engineering_session
        accept_session = workspace_candidate_session if candidate_only else bind_engineering_session
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
                    record_workspace_attach_failure(
                        workspace, requested_pid, requested_hwnd, attached,
                        phase="workspace_launch",
                    )
                    logger.warning(
                        "verified workspace launch attach returned a mismatched or "
                        "non-read-ready session for pid=%s hwnd=%s",
                        requested_pid,
                        requested_hwnd,
                    )
                else:
                    session = accept_session(validated)
        if require_controlled_launch:
            # Keep the exact fresh-launch receipt boundary. Native LD working-
            # copy capability is distinct from legacy engineering approval;
            # neither accepts a late/weak attach or promotes the other's proof.
            launch_instance_pid = (
                int(launch.instance["pid"])
                if isinstance(launch.instance, dict)
                and isinstance(launch.instance.get("pid"), int)
                and not isinstance(launch.instance.get("pid"), bool)
                else None
            )
            if (
                launch.verified is not True
                or not launch.window_detected
                or not launch.project_identity_read_only_bound_to_selected_pid
                or launch.identity_assurance
                not in {
                    "controlled_launch_read_only",
                    "controlled_launch_native_file_read_only",
                    "controlled_launch_official_open_read_only",
                }
                or launch_instance_pid is None
                or launch.launch_pid != launch_instance_pid
                or _normalized_workspace_path(launch.working_path)
                != _normalized_workspace_path(workspace.working_copy.path)
                or launch.working_hash.casefold()
                != workspace.working_copy.current_sha256.casefold()
                or (
                    independent_from_pid is not None
                    and launch_instance_pid == independent_from_pid
                )
                or session is None
                or not (
                    _engineering_session_identity_verified(session)
                    or _native_ld_edit_ready(session, workspace.model_dump(mode="json"))
                )
            ):
                if session is not None and not candidate_only:
                    active_engineering_session = None
                    execution_broker.clear_agent_session()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "XG5000 fresh reopen did not return the controlled launch "
                        "identity required for editing the working copy."
                    ),
                )
        return launch, session

    async def open_workspace_in_xg5000(
        workspace: LocalWorkspaceV1,
        *,
        timeout_seconds: float,
        independent_from_pid: int | None = None,
        require_controlled_launch: bool = False,
        candidate_only: bool = False,
        binding_wait_identity: dict[str, Any] | None = None,
        expected_pending_frame: dict[str, Any] | None = None,
        binding_wait_record: Callable | None = None,
    ) -> tuple[XG5000ProjectLaunchV1, dict[str, Any] | None]:
        accept_session = workspace_candidate_session if candidate_only else bind_engineering_session
        owned_wait = launch_wait_request_context.get() is not None or binding_wait_identity is not None
        open_arguments: dict[str, Any] = {
            "workspace_id": workspace.workspace_id,
            "expected_working_hash": workspace.working_copy.current_sha256,
            "timeout_seconds": timeout_seconds,
        }
        if independent_from_pid is not None:
            open_arguments["independent_from_pid"] = independent_from_pid
        try:
            async with join_owned_workspace_launch(workspace, open_arguments,
                    binding_wait_identity=binding_wait_identity, expected_pending_frame=expected_pending_frame,
                    record_physical_pending=binding_wait_record) as (raw_launch, admitted):
                completed = await complete_workspace_launch(workspace, raw_launch,
                    independent_from_pid=independent_from_pid,
                    require_controlled_launch=require_controlled_launch, candidate_only=candidate_only)
                if (completed[0].verified is True and completed[1] is not None
                        and (_engineering_session_identity_verified(completed[1])
                             or _native_ld_edit_ready(completed[1], workspace.model_dump(mode="json")))):
                    admitted()
                return completed
        except MCPToolError as exc:
            logger.warning(
                "XG5000 could not open the verified workspace working copy: %s",
                exc,
            )
            if not require_controlled_launch and not owned_wait:
                reconciled = await _reconcile_late_workspace_open(
                    mcp,
                    workspace,
                    bind_session=accept_session,
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

    async def _close_existing_workspace_for_fresh_reopen(
        workspace: LocalWorkspaceV1,
        session: dict[str, Any],
        *,
        timeout_seconds: float,
        return_recovery_outcome: bool = False,
        before_dispatch: Callable | None = None,
    ) -> dict[str, Any] | None:
        """Close one weak existing-process observation without discarding edits."""

        working_path = workspace.working_copy.path
        working_hash = workspace.working_copy.current_sha256
        try:
            pid = int(session["pid"])
            hwnd = int(session["hwnd"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="기존 XG5000 세션 identity가 완전하지 않아 새로 열 수 없습니다.",
            ) from exc
        if pid <= 0 or hwnd <= 0:
            raise HTTPException(
                status_code=409,
                detail="기존 XG5000 세션 identity가 유효하지 않아 새로 열 수 없습니다.",
            )
        if (
            _normalized_workspace_path(str(session.get("project_path") or ""))
            != _normalized_workspace_path(working_path)
            or str(session.get("project_hash") or "").casefold()
            != working_hash.casefold()
        ):
            raise HTTPException(
                status_code=409,
                detail="기존 XG5000 세션이 현재 working copy와 일치하지 않습니다.",
            )
        process_create_time = _engineering_session_process_create_time(session)
        if process_create_time is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "기존 XG5000 프로세스 lifetime proof가 없어 저장 없이 닫기를 "
                    "확인할 수 없습니다."
                ),
            )

        close_arguments: dict[str, Any] = {
            "pid": pid,
            "hwnd": hwnd,
            "process_create_time": process_create_time,
            "confirm_no_save": True,
            # Safe mode cancels an editor save prompt.  It never clicks IDNO,
            # so a dirty in-memory editor remains available to the user.
            "discard_unsaved_changes": False,
            "timeout_seconds": min(max(float(timeout_seconds), 0.1), 60.0),
            "project_path": working_path,
            "project_hash": working_hash,
        }
        if isinstance(session.get("session_id"), str) and session["session_id"]:
            close_arguments["session_id"] = session["session_id"]
        try:
            if before_dispatch is not None:
                before_dispatch()
            raw_close = await mcp.call_workspace_tool(
                "xg5000.reviewed_no_save_close",
                close_arguments,
            )
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail=_reviewed_no_save_close_failure_detail(exc),
            ) from exc
        if not isinstance(raw_close, dict):
            raise HTTPException(
                status_code=502,
                detail="MCP returned an invalid reviewed close result",
            )
        if raw_close.get("source_closed") is not True:
            # This is the expected safe-mode dirty boundary.  Do not clear or
            # replace the existing Host session in this branch.
            if (
                raw_close.get("discardUnsavedChanges") is False
                and raw_close.get("unsavedChangesDiscarded") is False
            ):
                if return_recovery_outcome:
                    # This is an authoritative completed refusal, not a new
                    # close authority. No dirty content was discarded.
                    return deepcopy(raw_close)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "기존 XG5000 편집기에 저장되지 않은 변경이 있어 새 작업 복사본 "
                        "프로세스를 열지 않았습니다."
                    ),
                )
            raise HTTPException(
                status_code=502,
                detail="MCP reviewed close result did not prove source closure",
            )
        _validate_reviewed_no_save_close_binding(
            raw_close,
            pid=pid,
            hwnd=hwnd,
            process_create_time=process_create_time,
            context={
                field: close_arguments[field]
                for field in ("session_id", "project_path", "project_hash")
                if field in close_arguments
            },
            require_no_discard=True,
        )
        update_native_workspace_binding(closed_target=(pid, hwnd), closed_process_create_time=process_create_time)
        if return_recovery_outcome:
            # The recovery owner persists this verified close prefix before its
            # exact original PID/lifetime/main retirement gate. A global desktop
            # inventory is neither that proof nor another close-input outcome.
            return deepcopy(raw_close)
        try:
            observed = await mcp.call_read_tool("xg5000.instances", {})
        except MCPToolError as exc:
            raise HTTPException(
                status_code=409,
                detail="기존 XG5000 창이 닫혔는지 확인하지 못했습니다.",
            ) from exc
        instances = observed.get("instances") if isinstance(observed, dict) else None
        if not isinstance(instances, list) or any(
            isinstance(item, dict) and item.get("pid") == pid for item in instances
        ):
            raise HTTPException(
                status_code=409,
                detail="기존 XG5000 프로세스가 아직 닫히지 않아 새로 열지 않았습니다.",
            )

        return deepcopy(raw_close) if return_recovery_outcome else None

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
        return await activate_local_workspace(
            workspace,
            open_in_xg5000=body.open_in_xg5000,
            timeout_seconds=body.timeout_seconds,
        )

    def select_local_workspace(
        workspace: LocalWorkspaceV1,
        session: dict[str, Any] | None,
    ) -> None:
        """Commit one already-validated selection without closing either IDE."""
        nonlocal active_engineering_session, active_workspace
        active_engineering_session = None
        execution_broker.clear_agent_session()
        active_workspace = workspace.model_dump(mode="json")
        store.set_host_state("active_workspace", active_workspace)
        clear_ls_controller_target()
        if session is None:
            bind_agent_workspace_target(workspace)
        else:
            bind_engineering_session(session)

    async def activate_local_workspace(
        workspace: LocalWorkspaceV1,
        *,
        open_in_xg5000: bool,
        timeout_seconds: float,
    ) -> OpenLocalWorkspaceResponseV1:
        # Launch/attach is a candidate until the existing controlled proof passes.
        # Failure or cancellation leaves the previous selection and session intact.
        launch: XG5000ProjectLaunchV1 | None = None
        session: dict[str, Any] | None = None
        if open_in_xg5000:
            launch, session = await open_workspace_in_xg5000(
                workspace,
                timeout_seconds=timeout_seconds,
                require_controlled_launch=True,
                candidate_only=True,
            )
        select_local_workspace(workspace, session)

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
            session=_engineering_session_v1(session) if session is not None else None,
        )


    async def resume_workspace_editor(
        workspace: LocalWorkspaceV1,
        cached_session: dict[str, Any] | None,
        *,
        timeout_seconds: float,
        force_fresh: bool = False,
        candidate_only: bool = False,
    ) -> tuple[XG5000ProjectLaunchV1 | None, dict[str, Any]]:
        nonlocal active_engineering_session
        accept_session = workspace_candidate_session if candidate_only else bind_engineering_session

        if (candidate_only and isinstance(active_workspace, dict)
                and (active_workspace.get('workspace_id'), active_workspace.get('revision_id'))
                    != (workspace.workspace_id, workspace.revision_id)):
            current_workspace = _local_workspace_v1(active_workspace)
            current_session = await revalidate_native_workspace_binding(current_workspace)
            if current_session is not None:
                # Close only the freshly proven owned working copy. The existing
                # safe-close helper preserves dirty/unknown buffers and must prove
                # retirement before the requested workspace can be opened.
                await _close_existing_workspace_for_fresh_reopen(
                    current_workspace, current_session, timeout_seconds=timeout_seconds)
                active_engineering_session = None
                execution_broker.clear_agent_session()

        def discard_unverified_session() -> None:
            nonlocal active_engineering_session
            if (isinstance(active_workspace, dict)
                    and active_workspace.get("workspace_id") == workspace.workspace_id
                    and active_workspace.get("revision_id") == workspace.revision_id):
                active_engineering_session = None
                execution_broker.clear_agent_session()

        reused_session: dict[str, Any] | None = None
        if not force_fresh:
            reused_session = await revalidate_native_workspace_binding(workspace)
        if reused_session is None and isinstance(cached_session, dict):
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
                try:
                    reused_session = await _revalidate_exact_engineering_session(
                        mcp,
                        session_id=raw_session_id,
                        pid=raw_pid,
                        hwnd=raw_hwnd,
                        project_path=workspace.working_copy.path,
                        project_hash=workspace.working_copy.current_sha256,
                    )
                except MCPToolError as exc:
                    discard_unverified_session()
                    raise HTTPException(
                        status_code=409,
                        detail="열려 있는 프로젝트를 재검증하지 못했습니다. 기존 창을 보존했습니다.",
                    ) from exc
        if reused_session is None:
            if isinstance(cached_session, dict):
                # A failed fresh read is unverified, not proof of process death.
                # Do not retain cached readiness if candidate lookup then fails.
                discard_unverified_session()
            # Restart clears the in-memory session, not the user's XG5000
            # windows. Prove the existing file binding before opening another
            # copy; a stale caption or cached PID cannot authorize this reuse.
            try:
                reused_session = await _find_existing_workspace_session(
                    mcp, workspace,
                    on_unverified=lambda pid, hwnd, result: record_workspace_attach_failure(
                        workspace, pid, hwnd, result, phase="existing_window",
                    ),
                )
            except MCPToolError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="열려 있는 프로젝트를 확인하지 못했습니다. 기존 창을 보존했습니다.",
                ) from exc
        if (reused_session is not None and not force_fresh
                and not (_engineering_session_identity_verified(reused_session)
                         or _native_ld_edit_ready(reused_session, workspace.model_dump(mode="json")))):
            discard_unverified_session()
            raise HTTPException(status_code=409,
                detail="The existing editor binding was not verified; its memory buffer was preserved.")
        if reused_session is not None and (
            force_fresh or not (
                _engineering_session_identity_verified(reused_session)
                or _native_ld_edit_ready(reused_session, workspace.model_dump(mode="json"))
            )
        ):
            # Reuse freshly proven native working-copy identity without
            # granting legacy engineering approval. Explicit force_fresh and
            # genuinely weak bindings retain the reviewed safe-close boundary.
            previous_pid = int(reused_session["pid"])
            await _close_existing_workspace_for_fresh_reopen(
                workspace,
                reused_session,
                timeout_seconds=timeout_seconds,
            )
            discard_unverified_session()
            launch, session = await open_workspace_in_xg5000(
                workspace,
                timeout_seconds=timeout_seconds,
                require_controlled_launch=True,
                candidate_only=candidate_only,
            )
            # The old process has already been closed, so it cannot be passed
            # as the launcher's ``independent_from_pid`` source (that CLI
            # option intentionally requires a live source window).  Keep the
            # old PID only as a Host-side identity comparison to reject an
            # unexpected PID reuse after the normal fresh launch proof.
            if (
                launch.launch_pid == previous_pid
                or (session is not None and session.get("pid") == previous_pid)
            ):
                discard_unverified_session()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "XG5000 fresh reopen returned the previously closed PID; "
                        "the working copy was not rebound for editing."
                    ),
                )
        elif reused_session is not None:
            request_context = launch_wait_request_context.get()
            pending_launch = (store.get_host_state(
                f"owned_launch_wait_operation:{request_context['operation_id']}")
                if request_context is not None else None)
            if owned_launch_wait_requires_park(pending_launch):
                # A fresh attach does not finish an earlier delegated launch.
                # Rejoin its exact durable owner through the read-only observe
                # path, then let the common admission finish that same lease.
                launch, session = await open_workspace_in_xg5000(
                    workspace, timeout_seconds=timeout_seconds,
                    require_controlled_launch=True, candidate_only=candidate_only,
                )
            else:
                session = accept_session(reused_session)
                launch = None
        else:
            discard_unverified_session()
            launch, session = await open_workspace_in_xg5000(
                workspace,
                timeout_seconds=timeout_seconds,
                require_controlled_launch=force_fresh or candidate_only,
                candidate_only=candidate_only,
            )
        if session is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "working copy는 XG5000에서 열었지만 정확한 읽기 전용 분석 "
                    "세션을 다시 검증하지 못했습니다."
                ),
            )
        return launch, session

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
        persisted = (active_workspace if isinstance(active_workspace, dict)
                     and active_workspace.get("workspace_id") == workspace_id else None)
        revisions: set[str] = set()
        if persisted is None:
            # Only server-owned conversation metadata can introduce an old target.
            # Neither a client-supplied path/session nor the current target's authority
            # is transferred to the requested workspace.
            offset = 0
            while True:
                conversations, total = store.list_conversations(limit=100, offset=offset)
                revisions.update(item.revision_id for item in conversations
                    if item.workspace_id == workspace_id and isinstance(item.revision_id, str))
                offset += len(conversations)
                if not conversations or offset >= total:
                    break
            if not revisions:
                raise HTTPException(status_code=404,
                    detail="the requested workspace has no retained conversation binding")

        # A resume never trusts a session from an earlier target operation. The
        # persisted workspace and current on-disk artifacts must be reverified
        # first; an existing session is reusable only after an exact fresh read
        # from the same MCP process proves its identity and process lifetime.
        cached_session = (
            dict(active_engineering_session)
            if persisted is not None and isinstance(active_engineering_session, dict)
            else None
        )
        try:
            persisted_workspace = _local_workspace_v1(persisted) if persisted is not None else None
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail="the active persisted workspace identity is invalid",
            ) from exc
        try:
            readiness = await mcp.wait_until_ready(timeout_seconds=body.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=503, detail=(
                "XG5000 작업 도구가 아직 준비 중입니다. 잠시 후 연결 상태를 확인해 주세요."
            )) from exc
        except MCPTransportError as exc:
            raise HTTPException(status_code=503, detail=(
                "XG5000 작업 도구 연결 준비를 완료하지 못했습니다. 연결 상태를 확인해 주세요."
            )) from exc
        if readiness.status != "ready":
            raise HTTPException(status_code=503, detail=(
                "XG5000 작업 도구 연결이 준비되지 않았습니다. 연결 상태를 확인해 주세요."
            ))
        try:
            checked = await mcp.call_workspace_tool(
                "workspace.status",
                {"workspace_id": workspace_id},
            )
        except MCPTransportError as exc:
            raise HTTPException(status_code=503, detail=(
                "XG5000 작업 도구와의 연결을 확인하지 못했습니다. 연결 상태를 확인해 주세요."
            )) from exc
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
        if (workspace.workspace_id != workspace_id
                or (persisted_workspace is None and workspace.revision_id not in revisions)
                or (persisted_workspace is not None
                    and _workspace_resume_stable_identity(workspace) != _workspace_resume_stable_identity(persisted_workspace))):
            raise HTTPException(
                status_code=409,
                detail=(
                    "저장된 작업공간 identity와 현재 working copy가 일치하지 "
                    "않아 자동 재개를 차단했습니다."
                ),
            )

        launch, session = await resume_workspace_editor(
            workspace, cached_session, timeout_seconds=body.timeout_seconds,
            candidate_only=True,
        )
        # Recheck the same server-owned artifact after awaited editor verification.
        # A changed artifact is not success and never triggers another launch here.
        after = _local_workspace_v1(await mcp.call_workspace_tool(
            "workspace.status", {"workspace_id": workspace_id}))
        if _workspace_resume_identity(after) != _workspace_resume_identity(workspace):
            raise HTTPException(status_code=409,
                detail="The workspace changed during editor validation; selection was not changed.")
        select_local_workspace(workspace, session)
        complete_owned_launch_resume(workspace, session)
        store.append_event(
            event_type="workspace.resumed",
            payload={
                "workspace_id": workspace.workspace_id,
                "revision_id": workspace.revision_id,
                "working_hash": workspace.working_copy.current_sha256,
                "xg5000_window_detected": bool(launch and launch.window_detected),
                "engineering_session_attached": True,
                "strong_identity_diagnostic": _safe_identity_diagnostic(session),
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
            session=_engineering_session_v1(session),
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
                and not _native_ld_edit_ready(active_engineering_session, active_workspace)
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

    @app.post("/api/v1/xg5000/editor")
    async def xg5000_editor_v1(
        body: dict[str, Any],
        _csrf: None = Depends(require_browser_csrf),
    ) -> dict[str, Any]:
        """Execute one explicit editor action through the existing elevated MCP."""
        try:
            return await mcp.call_workspace_tool("xg5000.editor", body)
        except MCPToolRejectedError as exc:
            # Keep the same validated native failure on the HTTP boundary.
            # A readable message cannot replace the operation's evidence.
            payload = exc.mcp_error_payload
            detail = (payload if isinstance(payload, dict)
                      and "nativeFailure" in payload.get("error", {}) else str(exc))
            raise HTTPException(status_code=409, detail=detail) from exc
        except MCPToolError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/v1/xg5000/instances", response_model=XG5000InstancesV1)
    async def xg5000_instances_v1() -> XG5000InstancesV1:
        try:
            result = await mcp.call_read_tool("xg5000.instances", {})
        except MCPToolRejectedError as exc:
            raise HTTPException(
                status_code=502,
                detail="XG5000 창 목록 조회가 실패했습니다. 연결 서비스는 응답했습니다.",
            ) from exc
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
                    "preflight_observed_at": preflight.observed_at,
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
                    "launch_mode": body.launch_mode,
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
                        launch_mode=body.launch_mode,
                    )
                )
            else:
                outcome = _launch_idle_xg5000_response(
                    raw,
                    receipt_digest=receipt_digest,
                    launch_mode=body.launch_mode,
                )
            _validate_launched_xg5000_identity(outcome.launched_identity)
            if outcome.idle_binding is not None:
                _validate_idle_xg5000_binding(outcome.idle_binding)
                if any(
                    outcome.idle_binding[field] != outcome.launched_identity[field]
                    for field in ("pid", "process_create_time", "executable")
                ):
                    raise ValueError("MCP idle binding does not match its launched child")
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
                        "launchMode": outcome.launch_mode,
                        "noExistingInstances": outcome.no_existing_instances,
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
                        "launchMode": outcome.launch_mode,
                        "noExistingInstances": outcome.no_existing_instances,
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
                    "launchMode": outcome.launch_mode,
                    "noExistingInstances": outcome.no_existing_instances,
                    "workspaceBound": False,
                    "processTerminated": False,
                },
            )
            return outcome
        except MCPToolError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_idle_launch_failure_detail(exc),
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

        arguments: dict[str, Any] = {
            "pid": body.pid,
            "hwnd": body.hwnd,
            "access_profile": "analysis",
        }
        if body.project_path:
            arguments["project_path"] = body.project_path
        elif (
            not body.allow_unbound_existing_target
            and working_copy
            and isinstance(working_copy.get("path"), str)
        ):
            arguments["project_path"] = working_copy["path"]
        if body.project_hash:
            arguments["project_hash"] = body.project_hash
        elif (
            not body.allow_unbound_existing_target
            and working_copy
            and isinstance(working_copy.get("current_sha256"), str)
        ):
            arguments["project_hash"] = working_copy["current_sha256"]
        if (
            not body.allow_unbound_existing_target
            and isinstance(active_workspace, dict)
            and isinstance(active_workspace.get("workspace_id"), str)
            and working_copy
            and isinstance(arguments.get("project_path"), str)
            and isinstance(working_copy.get("path"), str)
            and _normalized_workspace_path(arguments["project_path"])
            == _normalized_workspace_path(working_copy["path"])
            and arguments.get("project_hash") == working_copy.get("current_sha256")
        ):
            # The controlled-open receipt is bound to a workspace as well as
            # PID/path/hash. Keep that identity on manual attach after an open
            # response fails, but never infer it for an unrelated window.
            arguments["workspace_id"] = active_workspace["workspace_id"]
        try:
            result = (
                await mcp.call_read_tool("xg5000.attach", arguments)
            )
        except MCPToolError as exc:
            logger.warning(
                "XG5000 analysis rebind failed: %s",
                exc,
                exc_info=True,
            )
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
            store.append_event(
                event_type="workspace.attach_unverified",
                payload={
                    "workspace_id": arguments.get("workspace_id"),
                    "revision_id": (
                        active_workspace.get("revision_id")
                        if isinstance(active_workspace, dict)
                        and arguments.get("workspace_id") == active_workspace.get("workspace_id")
                        else None
                    ),
                    "pid": body.pid,
                    "hwnd": body.hwnd,
                    "phase": "manual_exact_attach",
                    "strong_identity_diagnostic": _safe_identity_diagnostic(
                        raw_session if isinstance(raw_session, dict) else {}
                    ),
                },
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
        prior_terminal_cleanup = bool(
            getattr(request.state, "prior_terminal_reviewed_close", False)
        )
        require_no_discard = prior_terminal_cleanup or bool(
            getattr(request.state, "explicit_clean_reviewed_close", False)
        )
        if require_no_discard:
            arguments["discard_unsaved_changes"] = False
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
            if (
                require_no_discard
                and isinstance(raw, dict)
                and raw.get("source_closed") is False
                and raw.get("discardUnsavedChanges") is False
                and raw.get("unsavedChangesDiscarded") is False
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The reviewed editor has unsaved changes; safe cleanup was canceled."
                    ),
                )
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
        binding = _validate_reviewed_no_save_close_binding(
            raw,
            pid=body.pid,
            hwnd=body.hwnd,
            process_create_time=body.process_create_time,
            context={
                field: arguments[field]
                for field in ("session_id", "project_path", "project_hash")
                if field in arguments
            },
            require_no_discard=require_no_discard,
        )
        update_native_workspace_binding(closed_target=(body.pid, body.hwnd),
                                        closed_process_create_time=body.process_create_time)
        store.selected_target_recovery_authority()
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
            not prior_terminal_cleanup
            and isinstance(session, dict)
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
