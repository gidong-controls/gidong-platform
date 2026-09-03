import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { hostApi, HostApiError } from "./api/client";
import { isChangeTransaction } from "./api/changeContracts";
import { ControlStudioEventStream } from "./api/sse";
import {
  agentProgressDetail,
  prependCoalescedActivity,
} from "./activityProgress";
import {
  appendStreamingDraft,
  clearStreamingDraftForRun,
  type StreamingDraft,
  visibleStreamingDraftText,
} from "./streamingDraft";
import type {
  Conversation,
  ConversationSummary,
  ControllerRestoreHandoff,
  ControllerRestorePreflight,
  ChangeTransaction,
  EngineeringChangeRequestScope,
  EngineeringLocation,
  EngineeringProgram,
  EngineeringTargetBinding,
  EvidenceRef,
  HostEventEnvelope,
  HostStatusResponse,
  LocalWorkspaceCompare,
  NativeProjectOperation,
  ReviewedNoSaveCloseRequest,
  ReviewedNoSaveCloseResponse,
  RunState,
  ServiceState,
  XG5000Instance,
} from "./api/types";
import { ChangeApprovalPanel } from "./components/ChangeApprovalPanel";
import { ChangeRecoveryPanel } from "./components/ChangeRecoveryPanel";
import { ConversationHistoryDialog } from "./components/ConversationHistoryDialog";
import { EngineeringAnalysisPanel } from "./components/EngineeringAnalysisPanel";
import { EngineeringMissionPanel } from "./components/EngineeringMissionPanel";
import { Icon, type IconName } from "./components/Icon";
import {
  KnowledgeDialog,
  type ControlPatternSummary,
  type KnowledgeDialogService,
  type KnowledgeDocument,
  type KnowledgeMemory,
  type KnowledgeSearchHit as DialogKnowledgeSearchHit,
  type KnowledgeScope,
  type KnowledgeVendor,
} from "./components/KnowledgeDialog";
import { ModelSettingsDialog } from "./components/ModelSettingsDialog";
import { NativeProjectPanel } from "./components/NativeProjectPanel";
import {
  NewProjectBriefPanel,
  ProjectFlowStepper,
  type NewProjectBrief,
} from "./components/NewProjectBriefPanel";
import {
  NewProjectPlanReviewPanel,
  projectPlanReviewItemIds,
} from "./components/NewProjectPlanReviewPanel";
import { OnlineControllerPanel } from "./components/OnlineControllerPanel";
import { ProgramNavigatorPanel } from "./components/ProgramNavigatorPanel";

const CONVERSATION_STORAGE_KEY = "controlstudio.activeConversationId";
const BROWSER_SESSION_STORAGE_KEY = "controlstudio.browserSession";
const CONTROLLER_RESTORE_BLOCKER_LABELS: Record<
  ControllerRestorePreflight["blockers"][number],
  string
> = {
  open_from_plc_menu_unresolved: "Open from PLC 메뉴를 찾지 못함",
  open_from_plc_menu_disabled: "Open from PLC 메뉴가 비활성",
  save_as_menu_unresolved: "Save As 메뉴를 찾지 못함",
  save_as_menu_disabled: "Save As 메뉴가 비활성",
  modal_dialog_present: "열린 XG5000 대화상자가 있음",
};
function workspaceStructuralChangeCount(value: LocalWorkspaceCompare): number {
  if (!value.structure) return 0;
  return Object.values(value.structure.sections).reduce(
    (total, section) =>
      total + section.addedCount + section.removedCount + section.changedCount,
    0,
  );
}

function workspaceCompareSummary(value: LocalWorkspaceCompare): string {
  if (value.fileIdentical) return "원본과 작업본이 동일합니다.";
  if (!value.structure) return "파일이 변경됐습니다 · 구조 비교 불가";
  const partial = value.structure.status === "partial" ? " · 부분 비교" : "";
  return `파일이 변경됐습니다 · 구조 변경 ${workspaceStructuralChangeCount(value)}건${partial}`;
}

const INITIAL_STATUS: HostStatusResponse = {
  schemaVersion: "1",
  releaseChannel: "development",
  productRelease: "source",
  host: { state: "starting", detail: "ControlStudio Host 확인 중" },
  agent: { state: "unavailable", detail: "Host 연결 필요" },
  mcp: { state: "unavailable", detail: "Host 연결 필요" },
  engineeringSystem: {
    state: "unavailable",
    vendor: "ls",
    application: "XG5000",
    sessionVerified: false,
    readReady: false,
    identityAssurance: "unverified",
    detail: "연결되지 않음",
  },
  controller: {
    state: "unavailable",
    vendor: "ls",
    protocol: "XGT_FENET",
    readOnly: true,
    strongEnoughForWrite: false,
    detail: "연결되지 않음",
  },
  capabilities: [],
};

const SERVICE_COPY: Record<
  ServiceState,
  { label: string; tone: "good" | "waiting" | "danger" | "neutral" }
> = {
  ready: { label: "준비됨", tone: "good" },
  starting: { label: "확인 중", tone: "waiting" },
  degraded: { label: "제한됨", tone: "waiting" },
  unavailable: { label: "연결 안 됨", tone: "neutral" },
  quarantined: { label: "격리됨", tone: "danger" },
};

const RUN_COPY: Partial<Record<RunState, string>> = {
  analyzing: "분석 중",
  waiting_for_user: "사용자 입력 대기",
  waiting_for_approval: "승인 대기",
  executing: "실행 중",
  verifying: "검증 중",
  completed: "완료",
  cancelled: "취소됨",
  failed: "실패",
  quarantined: "격리됨",
};

const RUN_STATES = new Set<RunState>([
  "idle",
  "analyzing",
  "waiting_for_user",
  "waiting_for_approval",
  "approved_pending_execution",
  "executing",
  "verifying",
  "completed",
  "cancelled",
  "failed",
  "quarantined",
]);

function isRunState(value: unknown): value is RunState {
  return typeof value === "string" && RUN_STATES.has(value as RunState);
}

const TERMINAL_RUN_STATES = new Set<RunState>([
  "completed",
  "cancelled",
  "failed",
  "quarantined",
]);

const PRIMARY_CHANGE_STATES = new Set<ChangeTransaction["state"]>([
  "pending_approval",
  "approved",
  "executing",
  "verifying",
  "completed",
  "rolled_back",
  "failed",
  "quarantined",
  "rejected",
  "expired",
]);

const TERMINAL_CHANGE_OUTCOME_STATES = new Set<ChangeTransaction["state"]>([
  "completed",
  "rolled_back",
  "failed",
  "rejected",
  "expired",
]);

type MissionActionEligibility = {
  available: boolean;
  unavailableMessage: string;
};

function availableMissionAction(): MissionActionEligibility {
  return { available: true, unavailableMessage: "" };
}

function unavailableMissionAction(
  unavailableMessage: string,
): MissionActionEligibility {
  return { available: false, unavailableMessage };
}

function isRestorableChange(transaction: ChangeTransaction): boolean {
  return (
    isChangeTransaction(transaction) &&
    PRIMARY_CHANGE_STATES.has(transaction.state) &&
    (!TERMINAL_CHANGE_OUTCOME_STATES.has(transaction.state) ||
      !transaction.receiptAcknowledgedAt)
  );
}

const TOOL_ACTIVITY_NAMES: Record<string, string> = {
  get_xg5000_session_status: "XG5000 세션 확인",
  list_project_programs: "프로그램 목록 확인",
  focus_xg5000_program: "XG5000 프로그램 열기",
  read_ladder_program: "래더 프로그램 읽기",
  inspect_project_hardware_context: "프로젝트 하드웨어 확인",
  inspect_workspace_hardware_context: "작업공간 하드웨어 확인",
  inspect_workspace_structure: "작업공간 구조 분석",
  search_workspace_evidence: "작업공간 검색",
  inspect_project_structure: "프로젝트 구조 분석",
  search_project_evidence: "프로젝트 근거 검색",
  analyze_change_target: "변경 대상 분석",
  compile_instruction_batch_patch_candidate: "명령 일괄 변경안 컴파일",
  compile_instruction_patch_candidate: "명령 변경안 컴파일",
  compile_rung_patch_candidate: "Rung 변경안 컴파일",
  compile_series_guard_candidate: "직렬 조건 변경안 컴파일",
  build_program_logic_graph: "논리 구조 분석",
  build_program_state_model: "상태 모델 분석",
  trace_device_causal_impact: "디바이스 원인·영향 추적",
  find_device_references: "디바이스 참조 검색",
  build_project_behavior_report: "프로젝트 동작 분석",
  capture_project_snapshot: "프로젝트 스냅샷 확인",
  get_session_evidence: "검증 근거 확인",
  get_ls_controller_status: "PLC 상태 읽기",
  read_ls_controller_devices: "PLC 디바이스 읽기",
  trace_ls_controller_devices: "PLC 디바이스 변화 추적",
  compare_workspace_source: "원본과 작업 복사본 비교",
  "change.propose_offline": "오프라인 변경안 생성",
};

const AGENT_PROGRESS_COPY: Record<string, string> = {
  starting: "에이전트 실행을 준비하고 있습니다.",
  engineering_analysis: "PLC 분석을 구성하고 있습니다.",
  planning_narrative: "요구사항과 차단 결정을 정리하고 있습니다.",
  planning_native_design: "실행 가능한 native PLC 설계를 생성하고 있습니다.",
  planning_finalize: "계획 artifact와 digest를 확정하고 있습니다.",
};

const NATIVE_CHECKPOINT_COPY: Record<string, string> = {
  SNAPSHOT_CREATED: "변경 전 프로젝트 스냅샷을 고정했습니다.",
  BACKUP_CREATED: "변경 전 백업을 검증했습니다.",
  PLANNED: "승인된 변경 계획을 실행 기준선에 결속했습니다.",
};

interface ActivityItem {
  id: string;
  title: string;
  detail: string;
  time: string;
  tone?: "default" | "warning";
  eventType: HostEventEnvelope["eventType"];
  sequence: number;
  runId?: string;
  requestId?: string;
  toolCallId?: string;
  toolState?: "running" | "completed" | "failed";
  startedAt?: string;
  finishedAt?: string;
  evidenceId?: string;
  diagnostic?: string;
  coalesceKey?: string;
}

const TOOL_STATE_COPY = {
  running: "실행 중",
  completed: "완료",
  failed: "실패",
} as const;

function activityDuration(item: ActivityItem): string | null {
  if (!item.startedAt || !item.finishedAt) {
    return null;
  }
  const elapsed = Date.parse(item.finishedAt) - Date.parse(item.startedAt);
  if (!Number.isFinite(elapsed) || elapsed < 0) {
    return null;
  }
  if (elapsed < 1_000) {
    return `${elapsed}ms`;
  }
  return `${(elapsed / 1_000).toFixed(elapsed < 10_000 ? 1 : 0)}초`;
}

interface NewProjectPlanningSession {
  brief: NewProjectBrief;
}

interface ReturnWorkspaceSnapshot {
  conversation: Conversation;
  evidence: EvidenceRef[];
  activities: ActivityItem[];
  programs: EngineeringProgram[];
  activePanel: "programs" | "evidence" | "activity" | null;
}

interface EntryCardProps {
  icon: IconName;
  title: string;
  description: string;
  unavailableReason: string;
  available: boolean;
  accent: "lime" | "amber" | "blue";
  actionLabel?: string;
  onClick?: () => void;
}

function EntryCard({
  icon,
  title,
  description,
  unavailableReason,
  available,
  accent,
  actionLabel = "시작하기",
  onClick,
}: EntryCardProps) {
  const reasonId = `entry-reason-${title.replace(/\s+/g, "-")}`;

  return (
    <article
      className={`entry-card entry-${accent} ${
        available ? "entry-available" : "entry-planned"
      }`}
    >
      <div className="entry-card-top">
        <span className={`entry-icon entry-icon-${accent}`}>
          <Icon name={icon} size={22} />
        </span>
      </div>
      <div className="entry-copy">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <div className="entry-action">
        <button
          type="button"
          className="entry-button"
          disabled={!available}
          aria-describedby={available ? undefined : reasonId}
          onClick={onClick}
        >
          <span>{available ? actionLabel : "아직 사용할 수 없음"}</span>
          <Icon name={available ? "arrow" : "link"} size={17} />
        </button>
        {!available && (
          <p id={reasonId} className="entry-reason">
            <span className="reason-dot" />
            {unavailableReason}
          </p>
        )}
      </div>
    </article>
  );
}

interface StatusItemProps {
  label: string;
  state: ServiceState;
  detail: string;
  icon: IconName;
}

function StatusItem({ label, state, detail, icon }: StatusItemProps) {
  const copy = SERVICE_COPY[state];
  return (
    <div className="status-item">
      <span className="status-icon">
        <Icon name={icon} size={17} />
      </span>
      <span className="status-copy">
        <span className="status-label">{label}</span>
        <span className="status-detail">{detail}</span>
      </span>
      <span className={`status-pill status-${copy.tone}`}>
        <span className="status-dot" />
        {copy.label}
      </span>
    </div>
  );
}

function timeLabel(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function readStoredConversationId(): string | null {
  try {
    return window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeConversationId(conversationId: string): void {
  try {
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, conversationId);
  } catch {
    // The server still owns the conversation. A privacy-mode browser simply
    // starts a new one next time.
  }
}

function clearStoredConversationId(): void {
  try {
    window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
  } catch {
    // Nothing else is required.
  }
}

function currentEngineeringTarget(
  status: HostStatusResponse,
): EngineeringTargetBinding | null {
  if (
    status.engineeringSystem.sessionId &&
    status.engineeringSystem.targetWorkspaceId &&
    status.engineeringSystem.targetRevisionId
  ) {
    return {
      workspaceId: status.engineeringSystem.targetWorkspaceId,
      revisionId: status.engineeringSystem.targetRevisionId,
      engineeringSessionId: status.engineeringSystem.sessionId,
    };
  }
  if (
    status.controller.state === "ready" &&
    status.controller.provisionalIdentity &&
    status.engineeringSystem.targetWorkspaceId &&
    status.engineeringSystem.targetRevisionId
  ) {
    return {
      workspaceId: status.engineeringSystem.targetWorkspaceId,
      revisionId: status.engineeringSystem.targetRevisionId,
      engineeringSessionId:
        status.controller.provisionalIdentity.fingerprintSha256,
    };
  }
  if (
    status.engineeringSystem.readReady &&
    status.engineeringSystem.sessionId &&
    status.workspace
  ) {
    const revisionId =
      status.workspace.revision ?? status.workspace.artifactHash;
    if (revisionId) {
      return {
        workspaceId: status.workspace.workspaceId,
        revisionId,
        engineeringSessionId: status.engineeringSystem.sessionId,
      };
    }
  }
  if (
    status.controller.state === "ready" &&
    status.controller.endpoint &&
    status.controller.provisionalIdentity
  ) {
    const { endpoint, provisionalIdentity } = status.controller;
    const normalizedHost = endpoint.host.includes(":")
      ? `[${endpoint.host}]`
      : endpoint.host;
    return {
      workspaceId: `controller-ls:${provisionalIdentity.fingerprintSha256}`,
      revisionId: `XGT_FENET:tcp:${normalizedHost}:${endpoint.port}`,
      engineeringSessionId: provisionalIdentity.fingerprintSha256,
    };
  }
  return null;
}

function conversationMatchesCurrentTarget(
  conversation: Pick<Conversation, "workflow" | "engineeringTarget">,
  status: HostStatusResponse,
): boolean {
  const current = currentEngineeringTarget(status);
  const stored =
    conversation.workflow === "engineering"
      ? conversation.engineeringTarget ?? null
      : null;
  if (current === null || stored === null) {
    return current === stored;
  }
  return (
    current.workspaceId === stored.workspaceId &&
    current.revisionId === stored.revisionId &&
    current.engineeringSessionId === stored.engineeringSessionId
  );
}

function conversationMissionMatchesCurrentTarget(
  conversation: {
    workflow: Conversation["workflow"];
    engineeringMission?: {
      workspaceId: string;
      revisionId: string;
    } | null;
  },
  status: HostStatusResponse,
): boolean {
  const mission =
    conversation.workflow === "engineering"
      ? conversation.engineeringMission ?? null
      : null;
  const current = currentEngineeringTarget(status);
  if (!mission || !current) {
    return false;
  }
  return (
    mission.workspaceId === current.workspaceId &&
    mission.revisionId === current.revisionId
  );
}

function isReusableUnboundDraft(
  conversation: Conversation,
  status: HostStatusResponse,
): boolean {
  return (
    currentEngineeringTarget(status) === null &&
    conversation.workflow === "engineering" &&
    !conversation.engineeringTarget &&
    !conversation.engineeringHandoff &&
    !conversation.engineeringMission &&
    conversation.messages.length === 0
  );
}

function takeBrowserHandoffTicket(): string | null {
  const fragment = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  if (!fragment) {
    return null;
  }

  const parameters = new URLSearchParams(fragment);
  const ticket = parameters.get("handoff_ticket");
  if (ticket === null) {
    return null;
  }

  parameters.delete("handoff_ticket");
  const remainingFragment = parameters.toString();
  const cleanUrl = `${window.location.pathname}${window.location.search}${
    remainingFragment ? `#${remainingFragment}` : ""
  }`;
  window.history.replaceState(window.history.state, "", cleanUrl);
  return ticket || null;
}

let pendingLauncherSessionExchange: Promise<void> | null = null;
let browserSessionRefreshTimer: number | undefined;

interface StoredBrowserSession {
  sessionToken: string;
  expiresAt: string;
}

function clearBrowserSession(): void {
  if (browserSessionRefreshTimer !== undefined) {
    window.clearTimeout(browserSessionRefreshTimer);
    browserSessionRefreshTimer = undefined;
  }
  hostApi.setSessionToken(undefined);
  try {
    window.sessionStorage.removeItem(BROWSER_SESSION_STORAGE_KEY);
  } catch {
    // The in-memory token is still cleared.
  }
}

function scheduleBrowserSessionRefresh(session: StoredBrowserSession): void {
  if (browserSessionRefreshTimer !== undefined) {
    window.clearTimeout(browserSessionRefreshTimer);
  }

  const expiry = Date.parse(session.expiresAt);
  if (!Number.isFinite(expiry)) {
    browserSessionRefreshTimer = undefined;
    return;
  }

  const refreshMarginMs = 10 * 60 * 1000;
  const maximumTimerMs = 6 * 60 * 60 * 1000;
  const remainingUntilRefresh = expiry - Date.now() - refreshMarginMs;
  const delay = Math.min(
    maximumTimerMs,
    Math.max(1_000, remainingUntilRefresh),
  );

  browserSessionRefreshTimer = window.setTimeout(() => {
    browserSessionRefreshTimer = undefined;
    if (expiry - Date.now() > refreshMarginMs) {
      scheduleBrowserSessionRefresh(session);
      return;
    }
    void hostApi
      .refreshBrowserSession()
      .then((refreshed) => {
        storeBrowserSession(refreshed);
        return hostApi.getBootstrap();
      })
      .then((bootstrap) => hostApi.setCsrfToken(bootstrap.csrfToken))
      .catch(() => {
        clearBrowserSession();
      });
  }, delay);
}

function storeBrowserSession(session: StoredBrowserSession): void {
  hostApi.setSessionToken(session.sessionToken);
  scheduleBrowserSessionRefresh(session);
  try {
    window.sessionStorage.setItem(
      BROWSER_SESSION_STORAGE_KEY,
      JSON.stringify(session),
    );
  } catch {
    // The current page can continue with the in-memory token, but refresh will
    // require the Launcher to issue a new one-time exchange token.
  }
}

function restoreBrowserSession(): StoredBrowserSession | null {
  try {
    const raw = window.sessionStorage.getItem(BROWSER_SESSION_STORAGE_KEY);
    if (!raw) {
      hostApi.setSessionToken(undefined);
      return null;
    }
    const candidate = JSON.parse(raw) as Partial<StoredBrowserSession>;
    const expiry = Date.parse(candidate.expiresAt ?? "");
    if (
      typeof candidate.sessionToken !== "string" ||
      !candidate.sessionToken ||
      typeof candidate.expiresAt !== "string" ||
      !Number.isFinite(expiry)
    ) {
      clearBrowserSession();
      return null;
    }
    const restored = {
      sessionToken: candidate.sessionToken,
      expiresAt: candidate.expiresAt,
    };
    hostApi.setSessionToken(restored.sessionToken);
    if (expiry > Date.now()) {
      scheduleBrowserSessionRefresh(restored);
    }
    return restored;
  } catch {
    clearBrowserSession();
    return null;
  }
}

function establishLauncherSession(): Promise<void> {
  if (pendingLauncherSessionExchange) {
    return pendingLauncherSessionExchange;
  }

  let ticket = takeBrowserHandoffTicket();
  if (!ticket) {
    const restored = restoreBrowserSession();
    if (!restored || Date.parse(restored.expiresAt) > Date.now()) {
      return Promise.resolve();
    }
    const refresh = hostApi
      .refreshBrowserSession()
      .then((session) => storeBrowserSession(session));
    pendingLauncherSessionExchange = refresh.finally(() => {
      pendingLauncherSessionExchange = null;
    });
    return pendingLauncherSessionExchange;
  }

  const exchange = hostApi
    .exchangeHandoffTicket(ticket)
    .then((session) => storeBrowserSession(session));
  ticket = null;
  pendingLauncherSessionExchange = exchange.finally(() => {
    pendingLauncherSessionExchange = null;
  });
  return pendingLauncherSessionExchange;
}

function newOperationId(): string {
  const suffix =
    typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `message:${suffix}`;
}

function newCanonicalUuid(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function newProjectBriefMessage(
  brief: NewProjectBrief,
  updating: boolean,
): string {
  const optional = (value: string) => value || "아직 모름";
  return [
    updating
      ? "새 프로젝트 요구사항을 다음 내용으로 수정했습니다. 기존 계획을 다시 검토해 주세요."
      : "다음 요구사항으로 새 PLC 프로젝트 계획을 시작해 주세요.",
    "",
    `프로젝트 이름: ${brief.projectName}`,
    `설비 목적: ${brief.machinePurpose}`,
    `알고 있는 동작 순서·공정: ${optional(brief.sequence)}`,
    `알고 있는 하드웨어·I/O·통신: ${optional(brief.hardwareAndIo)}`,
    `안전·인터록·운영 제약: ${optional(brief.safetyAndInterlocks)}`,
    `알고 있는 운전·초기화·복구 모드: ${optional(brief.operatingModes)}`,
    `성능·품질·인수 기준: ${optional(brief.performanceAndAcceptance)}`,
    `첨부 자료 source IDs: ${brief.attachmentSourceIds.join(", ") || "없음"}`,
    "",
    "이 단계에서는 파일이나 XG5000을 변경하지 말고, 확인된 사실·가정·미결 질문을 구분한 검토 가능한 프로젝트 계획서를 작성해 주세요.",
  ].join("\n");
}

function planningSessionFromConversation(
  conversation: Conversation,
): NewProjectPlanningSession | null {
  if (
    conversation.workflow !== "new_project_planning" ||
    !conversation.projectBrief
  ) {
    return null;
  }

  return {
    brief: {
      ...conversation.projectBrief,
      operatingModes: conversation.projectBrief.operatingModes ?? "",
      performanceAndAcceptance:
        conversation.projectBrief.performanceAndAcceptance ?? "",
      attachmentSourceIds:
        conversation.projectBrief.attachmentSourceIds ?? [],
    },
  };
}

function projectBriefMatches(
  left: NewProjectBrief | undefined,
  right: NewProjectBrief,
): boolean {
  return (
    left?.projectName === right.projectName &&
    left.machinePurpose === right.machinePurpose &&
    left.sequence === right.sequence &&
    left.hardwareAndIo === right.hardwareAndIo &&
    left.safetyAndInterlocks === right.safetyAndInterlocks &&
    (left.operatingModes ?? "") === right.operatingModes &&
    (left.performanceAndAcceptance ?? "") === right.performanceAndAcceptance &&
    (left.attachmentSourceIds ?? []).join("\u0000") ===
      right.attachmentSourceIds.join("\u0000")
  );
}

function eventRunState(event: HostEventEnvelope): RunState | undefined {
  const state = event.payload.state;
  if (typeof state !== "string") {
    return undefined;
  }
  return state.toLowerCase() as RunState;
}

function knowledgeVendor(value: string | undefined): KnowledgeVendor {
  return value === "ls" ||
    value === "mitsubishi" ||
    value === "rockwell" ||
    value === "generic"
    ? value
    : "generic";
}

function knowledgeDocumentFromApi(
  source: Awaited<ReturnType<typeof hostApi.listKnowledgeSources>>["items"][number],
): KnowledgeDocument {
  if (
    source.kind !== "manual" &&
    source.kind !== "standard" &&
    source.kind !== "project_document"
  ) {
    throw new Error("문서가 아닌 지식 항목을 문서로 표시할 수 없습니다.");
  }
  return {
    sourceId: source.sourceId,
    title: source.title,
    kind: source.kind,
    status: source.status,
    scope: source.scope,
    vendor: knowledgeVendor(source.vendor),
    productFamily: source.productFamily,
    version: source.version,
    language: source.language,
    locator: source.locator,
    updatedAt: source.updatedAt,
    authority: source.authority,
    extractionStatus:
      source.metadata.extraction_status === "ready" ||
      source.metadata.extraction_status === "partial"
        ? source.metadata.extraction_status
        : undefined,
    pageCount:
      typeof source.metadata.page_count === "number"
        ? source.metadata.page_count
        : undefined,
    indexedPageCount:
      typeof source.metadata.indexed_page_count === "number"
        ? source.metadata.indexed_page_count
        : undefined,
    lowTextPages: Array.isArray(source.metadata.low_text_pages)
      ? source.metadata.low_text_pages.filter(
          (value): value is number => typeof value === "number",
        )
      : undefined,
  };
}

function knowledgeMemoryFromApi(
  source: Awaited<ReturnType<typeof hostApi.listKnowledgeSources>>["items"][number],
): KnowledgeMemory {
  if (source.kind !== "memory" && source.kind !== "preference") {
    throw new Error("기억 또는 선호가 아닌 항목입니다.");
  }
  const [rawScope, ...scopeParts] = source.scope.split(":");
  const scope: KnowledgeScope =
    rawScope === "project" ||
    rawScope === "site" ||
    rawScope === "team" ||
    rawScope === "vendor"
      ? rawScope
      : "project";
  return {
    sourceId: source.sourceId,
    title: source.title,
    content:
      typeof source.metadata.content === "string"
        ? source.metadata.content
        : "",
    kind: source.kind,
    status: source.status,
    scope,
    scopeLabel: scopeParts.join(":") || source.scope,
    vendor: source.vendor ? knowledgeVendor(source.vendor) : null,
    revision: source.revision,
    confirmedBy: source.confirmedBy,
    updatedAt: source.updatedAt,
  };
}

const KNOWLEDGE_DIALOG_SERVICE: KnowledgeDialogService = {
  async search(request) {
    const response = await hostApi.searchKnowledge({
      query: request.query,
      vendor: request.vendor,
      kinds: request.kind ? [request.kind] : undefined,
      limit: request.limit,
    });
    return response.results.map(
      (hit): DialogKnowledgeSearchHit => ({
        chunkId: hit.citation.chunkId,
        sourceId: hit.citation.sourceId,
        sourceTitle: hit.citation.title,
        content: hit.content,
        score: hit.score,
        kind: hit.kind,
        status: hit.status,
        scope: hit.scope,
        vendor: hit.vendor ? knowledgeVendor(hit.vendor) : null,
        productFamily: hit.productFamily,
        version: hit.version,
        authority: hit.authority,
        citation: {
          reference: hit.citation.knowledgeRef,
          title: hit.citation.title,
          locator: hit.citation.locator,
          page: hit.citation.page,
          section: hit.citation.section,
        },
      }),
    );
  },
  async listDocuments() {
    const response = await hostApi.listKnowledgeSources({
      kinds: ["manual", "standard", "project_document"],
      limit: 500,
    });
    return response.items.map(knowledgeDocumentFromApi);
  },
  async importLocalDocument(request) {
    const result = await hostApi.importLocalKnowledge({
      ...request,
      scope: request.scope,
      authority: "user_imported_reference",
    });
    if (!result.source) {
      return null;
    }
    return {
      ...knowledgeDocumentFromApi(result.source),
      extractionStatus: result.extractionStatus,
      pageCount: result.pageCount,
      indexedPageCount: result.indexedPageCount,
      lowTextPages: result.lowTextPages,
    };
  },
  async listMemories() {
    const response = await hostApi.listKnowledgeSources({
      kinds: ["memory", "preference"],
      limit: 500,
    });
    return response.items.map(knowledgeMemoryFromApi);
  },
  async proposeMemory(request) {
    const context =
      request.project ?? request.site ?? request.team ?? request.vendor ?? "default";
    const source = await hostApi.proposeKnowledgeMemory({
      title: request.title,
      content: request.content,
      kind: request.kind,
      scope: `${request.scope}:${context}`,
      vendor: request.vendor,
      language: "ko",
    });
    return knowledgeMemoryFromApi(source);
  },
  async confirmMemory(request) {
    const source = await hostApi.confirmKnowledgeMemory(
      request.sourceId,
      request.expectedRevision,
    );
    return knowledgeMemoryFromApi(source);
  },
  async listControlPatterns() {
    const response = await hostApi.listKnowledgeSources({
      kinds: ["control_pattern"],
      statuses: ["confirmed"],
      limit: 200,
    });
    return response.items.map((source): ControlPatternSummary => {
      const metadata = source.metadata;
      return {
        patternId:
          typeof metadata.pattern_id === "string"
            ? metadata.pattern_id
            : source.sourceId,
        title: source.title,
        intent: typeof metadata.intent === "string" ? metadata.intent : "",
        revision: source.revision,
        equipmentKind:
          typeof metadata.equipment_kind === "string"
            ? metadata.equipment_kind
            : null,
        requiredQuestions: Array.isArray(metadata.required_questions)
          ? metadata.required_questions.filter(
              (value): value is string => typeof value === "string",
            )
          : [],
        safetyBoundary:
          typeof metadata.safety_boundary === "string"
            ? metadata.safety_boundary
            : "벤더 매뉴얼, 실제 I/O, 위험성 평가와 현장 검증이 우선합니다.",
      };
    });
  },
};

function App() {
  const [status, setStatus] = useState<HostStatusResponse>(INITIAL_STATUS);
  const [connectionMessage, setConnectionMessage] = useState(
    "ControlStudio Host를 확인하고 있습니다.",
  );
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [conversationHistoryOpen, setConversationHistoryOpen] = useState(false);
  const [conversationHistory, setConversationHistory] = useState<
    ConversationSummary[]
  >([]);
  const [conversationHistoryLoading, setConversationHistoryLoading] =
    useState(false);
  const [conversationHistorySelectingId, setConversationHistorySelectingId] =
    useState<string | null>(null);
  const [conversationHistoryNotice, setConversationHistoryNotice] = useState("");
  const [activePanel, setActivePanel] = useState<
    "programs" | "evidence" | "activity" | null
  >(null);
  const [eventConnection, setEventConnection] = useState<
    "idle" | "connected" | "reconnecting"
  >("idle");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [activeRunState, setActiveRunState] = useState<RunState | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelNotice, setCancelNotice] = useState("");
  const [evidence, setEvidence] = useState<EvidenceRef[]>([]);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(
    null,
  );
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [debugMode, setDebugMode] = useState(false);
  const [programs, setPrograms] = useState<EngineeringProgram[]>([]);
  const [programsLoading, setProgramsLoading] = useState(false);
  const [programFocusBusyId, setProgramFocusBusyId] = useState<string>();
  const [programNotice, setProgramNotice] = useState("");
  const [message, setMessage] = useState("");
  const [changeRequestScope, setChangeRequestScope] =
    useState<EngineeringChangeRequestScope>("analysis_only");
  const [verificationScope, setVerificationScope] = useState<
    "offline_native_reopen" | "offline_native_reopen_xgsim"
  >("offline_native_reopen");
  const [sending, setSending] = useState(false);
  const [missionResumeBusy, setMissionResumeBusy] = useState(false);
  const [missionReverifyBusy, setMissionReverifyBusy] = useState(false);
  const [missionStartBusy, setMissionStartBusy] = useState(false);
  const [localProjectOpen, setLocalProjectOpen] = useState(false);
  const [localProjectPath, setLocalProjectPath] = useState("");
  const [localProjectBusy, setLocalProjectBusy] = useState(false);
  const [localProjectBrowseBusy, setLocalProjectBrowseBusy] = useState(false);
  const [localProjectNotice, setLocalProjectNotice] = useState("");
  const [reviewedCloseDialogOpen, setReviewedCloseDialogOpen] = useState(false);
  const [reviewedCloseBusy, setReviewedCloseBusy] = useState(false);
  const [reviewedCloseAttempted, setReviewedCloseAttempted] = useState(false);
  const [reviewedCloseNotice, setReviewedCloseNotice] = useState("");
  const [reviewedCloseReceipt, setReviewedCloseReceipt] =
    useState<ReviewedNoSaveCloseResponse | null>(null);
  const [reviewedCloseDialogTarget, setReviewedCloseDialogTarget] =
    useState<ReviewedNoSaveCloseRequest | null>(null);
  const [workspaceResumeBusy, setWorkspaceResumeBusy] = useState(false);
  const [workspaceResumeNotice, setWorkspaceResumeNotice] = useState("");
  const [workspaceCompare, setWorkspaceCompare] =
    useState<LocalWorkspaceCompare | null>(null);
  const [workspaceCompareBusy, setWorkspaceCompareBusy] = useState(false);
  const [workChooserOpen, setWorkChooserOpen] = useState(false);
  const [newProjectBriefOpen, setNewProjectBriefOpen] = useState(false);
  const [newProjectPlanning, setNewProjectPlanning] =
    useState<NewProjectPlanningSession | null>(null);
  const [projectPlanReviewBusy, setProjectPlanReviewBusy] = useState(false);
  const [nativeProjectOpen, setNativeProjectOpen] = useState(false);
  const [nativeProjectRestoreId, setNativeProjectRestoreId] = useState<
    string | null
  >(null);
  const [nativeProjectRestoreLoading, setNativeProjectRestoreLoading] =
    useState(false);
  const [onlineControllerOpen, setOnlineControllerOpen] = useState(false);
  const [instancePickerOpen, setInstancePickerOpen] = useState(false);
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [instancesLoading, setInstancesLoading] = useState(false);
  const [recoveryAttachIntent, setRecoveryAttachIntent] = useState(false);
  const [attachingHwnd, setAttachingHwnd] = useState<number | null>(null);
  const [offlineCandidateBindingBusy, setOfflineCandidateBindingBusy] =
    useState(false);
  const [restorePreflightBusyHwnd, setRestorePreflightBusyHwnd] = useState<
    number | null
  >(null);
  const [restorePreflight, setRestorePreflight] =
    useState<ControllerRestorePreflight | null>(null);
  const [restoreHandoff, setRestoreHandoff] =
    useState<ControllerRestoreHandoff | null>(null);
  const [restoreActionBusy, setRestoreActionBusy] = useState(false);
  const [instanceNotice, setInstanceNotice] = useState("");
  const [sessionRequired, setSessionRequired] = useState(false);
  const [shutdownDialogOpen, setShutdownDialogOpen] = useState(false);
  const [shutdownBusy, setShutdownBusy] = useState(false);
  const [shutdownNotice, setShutdownNotice] = useState("");
  const [shutdownComplete, setShutdownComplete] = useState(false);
  const [activeChange, setActiveChange] = useState<ChangeTransaction | null>(
    null,
  );
  // Deferring changes only changes this browser's navigation. It never
  // acknowledges, rejects, or otherwise changes the durable transaction.
  const [deferredChangeTransactionId, setDeferredChangeTransactionId] =
    useState<string | null>(null);
  const [missionChangeTransaction, setMissionChangeTransaction] =
    useState<ChangeTransaction | null>(null);
  const [changeBusy, setChangeBusy] = useState(false);
  const [changeNotice, setChangeNotice] = useState("");
  const streamRef = useRef<ControlStudioEventStream | null>(null);
  const streamingDraftRef = useRef<StreamingDraft | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const statusRefreshRef = useRef<Promise<void> | null>(null);
  const statusRefreshEpochRef = useRef(0);
  const conversationIdRef = useRef<string | null>(null);
  const missionChangeTransactionIdRef = useRef<string | null>(null);
  const missionChangeRefreshEpochRef = useRef(0);
  const conversationRefreshEpochRef = useRef(0);
  const targetActivationEpochRef = useRef(0);
  const changeRecoveryReplayPinRef = useRef<{
    transactionId: string;
    throughSequence: number;
  } | null>(null);
  const changePriorityRefreshEpochRef = useRef(0);
  const debugModeInitializedRef = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const returnWorkspaceRef = useRef<ReturnWorkspaceSnapshot | null>(null);
  const analysisProgramCatalogLoadRef = useRef<string | null>(null);
  const engineeringHandoffTransitionRef = useRef<{
    transitionKey: string;
    promise: Promise<void>;
  } | null>(null);
  const insightPanelRef = useRef<HTMLElement>(null);
  const insightReturnFocusRef = useRef<HTMLButtonElement | null>(null);

  const hostReady = status.host.state === "ready";
  const agentReady = status.agent.state === "ready";
  const planningAgentReady =
    status.agent.planningReady ?? agentReady;
  const engineeringAgentReady =
    status.agent.engineeringReady ?? agentReady;
  const mcpReady = status.mcp.state === "ready";
  const offlineMutateFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "offline_mutate",
  );
  const mneXgSimAvailable = Boolean(
    status.featureReadiness?.some(
      (feature) =>
        feature.featureId === "full_program_replacement" &&
        feature.capabilityStatus === "valid" &&
        feature.currentReady,
    ),
  );
  const offlineCandidateAvailable =
    hostReady &&
    mcpReady &&
    status.engineeringSystem.sessionVerified &&
    (offlineMutateFeature?.currentReady ??
      status.capabilities.includes("offline_mutate"));
  const offlineCandidateSelectable =
    offlineCandidateAvailable ||
    Boolean(status.engineeringSystem.offlineCandidateEligible);
  const planningConversationActive = Boolean(newProjectPlanning);
  const engineeringScopeVisible = !planningConversationActive;
  const selectedOfflineScopeUnavailable =
    engineeringScopeVisible &&
    changeRequestScope === "offline_candidate_revision" &&
    !offlineCandidateAvailable;
  const terminalEngineeringMission =
    conversation?.engineeringMission?.snapshot.status === "completed" ||
    conversation?.engineeringMission?.snapshot.status === "blocked";
  const missionChangeTransactionId =
    conversation?.engineeringMission?.changeTransactionId ?? null;
  const waitingMissionHasTerminalChangeOutcome = Boolean(
    conversation?.engineeringMission?.snapshot.status ===
      "waiting_for_approval" &&
      missionChangeTransactionId &&
      missionChangeTransaction &&
      missionChangeTransaction.transactionId === missionChangeTransactionId &&
      TERMINAL_CHANGE_OUTCOME_STATES.has(missionChangeTransaction.state),
  );
  const canCancelActiveRun =
    Boolean(activeRunId) &&
    (!activeRunState || !TERMINAL_RUN_STATES.has(activeRunState));
  const visibleStreamingText = visibleStreamingDraftText(
    streamingDraftRef.current,
    activeRunId,
    Boolean(
      activeRunState && !TERMINAL_RUN_STATES.has(activeRunState),
    ),
  )
    ? streamingText
    : "";
  const canChat =
    hostReady &&
    (planningConversationActive
      ? planningAgentReady
      : engineeringAgentReady) &&
    conversation !== null &&
    !canCancelActiveRun &&
    (planningConversationActive ||
      (!terminalEngineeringMission &&
        mcpReady &&
        (conversationMatchesCurrentTarget(conversation, status) ||
          conversationMissionMatchesCurrentTarget(conversation, status)) &&
        currentEngineeringTarget(status) !== null));
  const evaluateMissionResumeEligibility = (): MissionActionEligibility => {
    const mission = conversation?.engineeringMission;
    if (!conversation || !mission) {
      return unavailableMissionAction("저장된 엔지니어링 작업을 찾지 못했습니다. 대화를 다시 불러와 주세요.");
    }
    if (
      mission.snapshot.status !== "active" &&
      !waitingMissionHasTerminalChangeOutcome
    ) {
      return unavailableMissionAction("현재 미션 상태에서는 작업을 계속 실행할 수 없습니다.");
    }
    if (!hostReady || !engineeringAgentReady || !mcpReady) {
      return unavailableMissionAction("Host, Agent, MCP가 모두 준비된 뒤 작업을 계속 실행할 수 있습니다.");
    }
    if (canCancelActiveRun) {
      return unavailableMissionAction("이미 실행 중인 작업이 있습니다. 완료 또는 취소된 뒤 다시 실행해 주세요.");
    }
    if (sending) {
      return unavailableMissionAction("다른 요청을 처리 중입니다. 완료된 뒤 다시 실행해 주세요.");
    }
    if (!conversationMissionMatchesCurrentTarget(conversation, status)) {
      return unavailableMissionAction("현재 XG5000 작업 대상이 저장된 미션 대상과 다릅니다. 대상을 다시 확인해 주세요.");
    }
    return availableMissionAction();
  };
  const missionResumeEligibility = evaluateMissionResumeEligibility();
  const canResumeEngineeringMission = missionResumeEligibility.available;
  const canStartNewEngineeringMission =
    hostReady &&
    engineeringAgentReady &&
    mcpReady &&
    conversation !== null &&
    (conversation.engineeringMission?.snapshot.status === "completed" ||
      conversation.engineeringMission?.snapshot.status === "blocked") &&
    !canCancelActiveRun &&
    !sending &&
    conversationMissionMatchesCurrentTarget(conversation, status);
  const evaluateMissionReverifyEligibility = (): MissionActionEligibility => {
    const mission = conversation?.engineeringMission;
    if (!conversation || !mission) {
      return unavailableMissionAction("저장된 엔지니어링 작업을 찾지 못했습니다. 대화를 다시 불러와 주세요.");
    }
    if (mission.snapshot.status !== "blocked") {
      return unavailableMissionAction("차단된 단계만 다시 검증할 수 있습니다.");
    }
    if (!hostReady) {
      return unavailableMissionAction("Host가 준비된 뒤 차단 단계를 다시 검증할 수 있습니다.");
    }
    if (canCancelActiveRun) {
      return unavailableMissionAction("이미 실행 중인 작업이 있습니다. 완료 또는 취소된 뒤 다시 검증해 주세요.");
    }
    if (sending) {
      return unavailableMissionAction("다른 요청을 처리 중입니다. 완료된 뒤 다시 검증해 주세요.");
    }
    if (!conversationMissionMatchesCurrentTarget(conversation, status)) {
      return unavailableMissionAction("현재 XG5000 작업 대상이 저장된 미션 대상과 다릅니다. 대상을 다시 확인해 주세요.");
    }
    return availableMissionAction();
  };
  const missionReverifyEligibility = evaluateMissionReverifyEligibility();
  const canReverifyBlockedEngineeringMissionStep =
    missionReverifyEligibility.available;

  useEffect(() => {
    setChangeRequestScope("analysis_only");
    setVerificationScope("offline_native_reopen");
    setSelectedEvidenceId(null);
  }, [conversation?.conversationId]);

  useEffect(() => {
    if (
      debugModeInitializedRef.current ||
      status.host.state === "starting"
    ) {
      return;
    }
    debugModeInitializedRef.current = true;
    if (status.releaseChannel === "development") {
      setDebugMode(true);
      setActivePanel("activity");
    }
  }, [status.host.state, status.releaseChannel]);

  const refreshConversation = useCallback(async (
    conversationId: string,
    allowSwitch = false,
  ) => {
    const requestEpoch = ++conversationRefreshEpochRef.current;
    const detail = await hostApi.getConversation(conversationId);
    if (
      requestEpoch !== conversationRefreshEpochRef.current ||
      (!allowSwitch && conversationIdRef.current !== conversationId)
    ) {
      return detail;
    }
    conversationIdRef.current = detail.conversationId;
    setConversation(detail);
    setNewProjectPlanning(planningSessionFromConversation(detail));
    return detail;
  }, []);

  const applyHostStatus = useCallback((nextStatus: HostStatusResponse) => {
    // Invalidate any status request that started before a target-changing
    // operation completed. Otherwise a slow pre-resume snapshot can overwrite
    // the newly verified XG5000 binding and reopen the work chooser.
    statusRefreshEpochRef.current += 1;
    setStatus(nextStatus);
  }, []);

  const refreshHostStatus = useCallback((): Promise<void> => {
    if (statusRefreshRef.current) {
      return statusRefreshRef.current;
    }
    const requestEpoch = ++statusRefreshEpochRef.current;
    const request = hostApi
      .getStatus()
      .then((nextStatus) => {
        if (requestEpoch === statusRefreshEpochRef.current) {
          setStatus(nextStatus);
        }
      })
      .finally(() => {
        if (statusRefreshRef.current === request) {
          statusRefreshRef.current = null;
        }
      });
    statusRefreshRef.current = request;
    return request;
  }, []);

  const refreshMissionChangeTransaction = useCallback(
    async (transactionId = missionChangeTransactionIdRef.current) => {
      const refreshEpoch = ++missionChangeRefreshEpochRef.current;
      if (!transactionId) {
        setMissionChangeTransaction(null);
        return;
      }
      setMissionChangeTransaction(null);
      const transaction = await hostApi.getChangeTransaction(transactionId);
      if (
        refreshEpoch === missionChangeRefreshEpochRef.current &&
        missionChangeTransactionIdRef.current === transactionId
      ) {
        setMissionChangeTransaction(
          transaction.transactionId === transactionId ? transaction : null,
        );
      }
    },
    [],
  );

  useEffect(() => {
    missionChangeTransactionIdRef.current = missionChangeTransactionId;
    if (missionChangeTransactionId) {
      void refreshMissionChangeTransaction(missionChangeTransactionId).catch(
        () => undefined,
      );
    } else {
      missionChangeRefreshEpochRef.current += 1;
      setMissionChangeTransaction(null);
    }
  }, [missionChangeTransactionId, refreshMissionChangeTransaction]);

  const refreshServerPriorityChange = useCallback(async (): Promise<void> => {
    void refreshMissionChangeTransaction().catch(() => undefined);
    const requestEpoch = ++changePriorityRefreshEpochRef.current;
    try {
      // A target-changing status update invalidates the snapshot. Retry once
      // so an adjacent host.status event cannot make us drop the change event,
      // while still refusing to apply a pre-target snapshot.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const statusEpoch = statusRefreshEpochRef.current;
        const nextStatus = await hostApi.getStatus();
        if (requestEpoch !== changePriorityRefreshEpochRef.current) {
          return;
        }
        if (statusEpoch !== statusRefreshEpochRef.current) {
          continue;
        }

        const activeOperation = nextStatus.activeOperation;
        const activeTransaction =
          activeOperation?.kind === "change" &&
          typeof activeOperation.transactionId === "string"
            ? await hostApi.getChangeTransaction(activeOperation.transactionId)
            : null;
        if (requestEpoch !== changePriorityRefreshEpochRef.current) {
          return;
        }
        if (statusEpoch !== statusRefreshEpochRef.current) {
          continue;
        }

        const currentConversationId = conversationIdRef.current;
        const recentTransactions = currentConversationId
          ? await hostApi.listChangeTransactions(currentConversationId, 20)
          : [];
        if (requestEpoch !== changePriorityRefreshEpochRef.current) {
          return;
        }
        if (statusEpoch !== statusRefreshEpochRef.current) {
          continue;
        }

        applyHostStatus(nextStatus);
        const transaction =
          activeTransaction &&
          currentConversationId &&
          activeTransaction.conversationId === currentConversationId &&
          isRestorableChange(activeTransaction)
            ? activeTransaction
            : recentTransactions.find(isRestorableChange) ?? null;
        setActiveChange(
          transaction,
        );
        return;
      }
    } catch (error) {
      if (requestEpoch === changePriorityRefreshEpochRef.current) {
        throw error;
      }
    }
  }, [applyHostStatus, refreshMissionChangeTransaction]);

  const addActivity = useCallback(
    (
      event: HostEventEnvelope,
      title: string,
      detail: string,
      tone: ActivityItem["tone"] = "default",
    ) => {
      const toolCallId =
        typeof event.payload.toolCallId === "string"
          ? event.payload.toolCallId
          : typeof event.payload.tool_call_id === "string"
            ? event.payload.tool_call_id
            : undefined;
      const requestId =
        typeof event.payload.requestId === "string"
          ? event.payload.requestId
          : typeof event.payload.request_id === "string"
            ? event.payload.request_id
            : undefined;
      const correlationId = toolCallId ?? requestId;
      const toolState =
        event.eventType === "tool.started"
          ? "running"
          : event.eventType === "tool.completed"
            ? "completed"
            : event.eventType === "tool.failed"
              ? "failed"
              : undefined;
      const progressStage =
        event.eventType === "agent.progress" && typeof event.payload.stage === "string"
          ? event.payload.stage
          : undefined;
      const coalesceKey = progressStage
        ? `${event.runId ?? "agent"}\u0000${progressStage}`
        : undefined;
      setActivities((current) => {
        const existing =
          toolState && correlationId
            ? current.find(
                (item) =>
                  item.runId === event.runId &&
                  (item.toolCallId ?? item.requestId) === correlationId,
              )
            : coalesceKey
              ? current.find((item) => item.coalesceKey === coalesceKey)
            : undefined;
        const next: ActivityItem = {
          id: existing?.id ?? event.eventId,
          title,
          detail,
          time: timeLabel(event.occurredAt),
          tone,
          eventType: event.eventType,
          sequence: event.sequence,
          runId: event.runId,
          requestId,
          toolCallId,
          toolState,
          startedAt:
            existing?.startedAt ??
            (event.eventType === "tool.started" ? event.occurredAt : undefined),
          finishedAt:
            event.eventType === "tool.completed" ||
            event.eventType === "tool.failed"
              ? event.occurredAt
              : existing?.finishedAt,
          evidenceId: existing?.evidenceId,
          diagnostic: [
            typeof event.payload.cycle === "number"
              ? `cycle=${event.payload.cycle}`
              : null,
            typeof event.payload.cycleDisposition === "string"
              ? `disposition=${event.payload.cycleDisposition}`
              : null,
            typeof event.payload.failure_kind === "string"
              ? `failure=${event.payload.failure_kind}`
              : null,
            typeof event.payload.state === "string"
              ? `state=${event.payload.state}`
              : null,
            typeof event.payload.digest === "string"
              ? `digest=${event.payload.digest}`
              : null,
          ]
            .filter((value): value is string => value !== null)
            .join(" · ") || undefined,
          coalesceKey,
        };
        return prependCoalescedActivity(
          current.filter(
            (item) =>
              !(
                toolCallId &&
                item.runId === event.runId &&
                item.toolCallId === toolCallId
              ),
          ),
          next,
        ).slice(0, 50);
      });
    },
    [],
  );

  const handleHostEvent = useCallback(
    (event: HostEventEnvelope) => {
      const currentConversationId = conversationIdRef.current;
      if (
        event.conversationId &&
        currentConversationId &&
        event.conversationId !== currentConversationId
      ) {
        return;
      }

      const recoveryReplayPin = changeRecoveryReplayPinRef.current;
      const historicalChangeReplay =
        recoveryReplayPin !== null &&
        event.sequence <= recoveryReplayPin.throughSequence;
      if (
        recoveryReplayPin !== null &&
        event.sequence > recoveryReplayPin.throughSequence
      ) {
        changeRecoveryReplayPinRef.current = null;
      }

      if (event.eventType === "host.status") {
        // Historical event replay can contain status payloads written by an
        // older Host schema. Treat the event only as an invalidation signal
        // and fetch the current, fully typed snapshot before replacing UI
        // state. This also keeps every connected browser on the same
        // server-authoritative target after another client changes it.
        void refreshHostStatus()
          .catch(() => {
            setConnectionMessage(
              "Host 상태 변경을 다시 확인하지 못했습니다.",
            );
          });
      }

      if (event.eventType === "agent.token") {
        const nextDraft = appendStreamingDraft(
          streamingDraftRef.current,
          event.runId,
          typeof event.payload.delta === "string" ? event.payload.delta : undefined,
        );
        if (nextDraft !== streamingDraftRef.current) {
          streamingDraftRef.current = nextDraft;
          setStreamingText(nextDraft?.text ?? "");
        }
        return;
      }

      if (event.eventType === "agent.progress") {
        const stage = event.payload.stage;
        if (typeof stage === "string") {
          const label = AGENT_PROGRESS_COPY[stage];
          if (label) {
            addActivity(
              event,
              label,
              agentProgressDetail(event.runId ?? "agent", event.payload.elapsedSeconds),
            );
          }
        }
        return;
      }

      if (event.eventType === "evidence.added") {
        const nextEvidence = event.payload as unknown as EvidenceRef;
        if (nextEvidence.evidenceId) {
          setEvidence((current) => [
            nextEvidence,
            ...current.filter(
              (item) => item.evidenceId !== nextEvidence.evidenceId,
            ),
          ]);
          if (nextEvidence.toolCallId) {
            setActivities((current) =>
              current.map((item) =>
                item.runId === event.runId &&
                item.toolCallId === nextEvidence.toolCallId
                  ? { ...item, evidenceId: nextEvidence.evidenceId }
                  : item,
              ),
            );
          }
        }
      }

      if (
        !historicalChangeReplay &&
        (event.eventType === "approval.requested" ||
          event.eventType === "approval.resolved" ||
          event.eventType === "approval.expired" ||
          event.eventType === "change.transaction.updated" ||
          event.eventType === "change.simulation_finalized" ||
          event.eventType === "change.receipt_acknowledged" ||
          event.eventType === "operation.quarantined")
      ) {
        void refreshServerPriorityChange().catch(() => {
          setChangeNotice(
            "우선순위에 따른 변경 계약 상태를 다시 불러오지 못했습니다.",
          );
        });
      }

      if (
        event.eventType === "message.appended" ||
        event.eventType === "agent.message" ||
        event.eventType === "mission.checkpointed" ||
        event.eventType === "mission.blocked_step_reverified" ||
        event.eventType === "mission.recovery_digest_repaired" ||
        event.eventType === "run.created" ||
        event.eventType === "run.state" ||
        event.eventType === "run.state_changed"
      ) {
        if (currentConversationId) {
          void refreshConversation(currentConversationId).catch(() => {
            setConnectionMessage("대화 기록을 다시 불러오지 못했습니다.");
          });
        }
      }

      if (
        event.eventType === "controller.connected" ||
        event.eventType === "controller.observed" ||
        event.eventType === "controller.disconnected"
      ) {
        void refreshHostStatus()
          .catch(() => {
            setConnectionMessage("온라인 PLC 상태를 다시 불러오지 못했습니다.");
          });
        if (event.eventType === "controller.disconnected") {
          setOnlineControllerOpen(false);
        }
      }

      if (event.eventType === "agent.message") {
        const nextDraft = clearStreamingDraftForRun(
          streamingDraftRef.current,
          event.runId,
        );
        if (nextDraft !== streamingDraftRef.current) {
          streamingDraftRef.current = nextDraft;
          setStreamingText("");
        }
        addActivity(event, "에이전트 응답을 저장했습니다.", event.runId ?? "agent");
        return;
      }

      if (
        event.eventType === "run.created" ||
        event.eventType === "run.state" ||
        event.eventType === "run.state_changed"
      ) {
        const runState = eventRunState(event);
        if (runState) {
          if (TERMINAL_RUN_STATES.has(runState)) {
            const nextDraft = clearStreamingDraftForRun(
              streamingDraftRef.current,
              event.runId,
            );
            if (nextDraft !== streamingDraftRef.current) {
              streamingDraftRef.current = nextDraft;
              setStreamingText("");
            }
          }
          if (event.runId) {
            setActiveRunId(event.runId);
          }
          setActiveRunState(runState);
          setConnectionMessage(`에이전트 작업 상태: ${RUN_COPY[runState] ?? runState}`);
          addActivity(
            event,
            `작업 상태: ${RUN_COPY[runState] ?? runState}`,
            event.runId ?? "run",
            runState === "failed" || runState === "quarantined"
              ? "warning"
              : "default",
          );
        }
        return;
      }

      if (event.eventType === "change.native_checkpoint") {
        const stage = event.payload.stage;
        const label = typeof stage === "string" ? NATIVE_CHECKPOINT_COPY[stage] : undefined;
        if (label) {
          addActivity(event, label, "XG5000 변경 실행");
        }
        return;
      }

      const eventLabels: Partial<Record<HostEventEnvelope["eventType"], string>> =
        {
          "mission.checkpointed": "에이전트 작업 계획과 진행 상태를 저장했습니다.",
          "agent.checkpointed": "에이전트 실행 체크포인트를 저장했습니다.",
          "conversation.created": "서버에 새 대화를 만들었습니다.",
          "message.appended": "대화 메시지를 저장했습니다.",
          "tool.started": "Agent 도구 작업을 시작했습니다.",
          "tool.completed": "Agent 도구 작업이 끝났습니다.",
          "tool.failed": "Agent 도구 작업이 실패했습니다.",
          "approval.requested": "검토가 필요한 변경안이 도착했습니다.",
          "approval.resolved": "변경안 승인 결정을 기록했습니다.",
          "approval.expired": "변경안 승인 기한이 만료됐습니다.",
          "change.execution_started": "승인된 XG5000 변경 실행을 시작했습니다.",
          "change.verification_started": "변경 결과의 XG5000 검증을 시작했습니다.",
          "change.execution_finished": "변경 실행 결과를 확정했습니다.",
          "change.working_copy_restored": "현재 작업 복사본을 보존하고 검증된 백업을 복원했습니다.",
          "change.simulation_finalized": "XG-SIM Keep/Revert 결정을 확정했습니다.",
          "change.receipt_acknowledged": "변경 결과 확인을 기록했습니다.",
          "controller.connected": "LS PLC에 읽기 전용으로 연결했습니다.",
          "controller.observed": "LS PLC 운전 상태를 다시 읽었습니다.",
          "controller.devices_read": "LS PLC 디바이스 값을 읽었습니다.",
          "controller.devices_traced": "LS PLC 디바이스 값 변화를 추적했습니다.",
          "controller.disconnected": "LS PLC 연결을 해제했습니다.",
          "knowledge.source_imported": "엔지니어링 지식 문서를 가져왔습니다.",
          "knowledge.memory_proposed": "현장 기억을 검토 대기로 저장했습니다.",
          "knowledge.memory_confirmed": "사람이 확인한 현장 기억을 활성화했습니다.",
          "operation.quarantined": "결과가 불명확해 작업을 격리했습니다.",
        };
      const label = eventLabels[event.eventType];
      if (label) {
        const toolName =
          typeof event.payload.toolName === "string"
            ? event.payload.toolName
            : typeof event.payload.tool_name === "string"
              ? event.payload.tool_name
              : undefined;
        addActivity(
          event,
          label,
          toolName
            ? TOOL_ACTIVITY_NAMES[toolName] ?? "읽기·분석 작업"
            : event.runId ?? "ControlStudio",
          event.eventType === "operation.quarantined" ||
            event.eventType === "tool.failed"
            ? "warning"
            : "default",
        );
        if (event.eventType === "tool.started") {
          setActivePanel((current) => current ?? "activity");
        }
      }
    },
    [
      addActivity,
      refreshConversation,
      refreshHostStatus,
      refreshServerPriorityChange,
    ],
  );

  const startEventStream = useCallback(
    (
      conversationId: string,
      afterSequence: number,
    ) => {
      streamRef.current?.stop();
      const query = new URLSearchParams({ conversation_id: conversationId });
      const stream = new ControlStudioEventStream(
        `/api/v1/events?${query.toString()}`,
        handleHostEvent,
        (streamState) => {
          if (streamState === "connected") {
            setEventConnection("connected");
          } else if (streamState === "reconnecting") {
            setEventConnection("reconnecting");
          } else {
            setEventConnection("idle");
          }
        },
        {
          getAuthorizationHeader: () => hostApi.getAuthorizationHeader(),
          onUnauthorized: () => hostApi.invalidateSession(),
        },
      );
      streamRef.current = stream;
      stream.start(afterSequence);
    },
    [handleHostEvent],
  );

  const resetConversationScopedUi = useCallback(() => {
    changeRecoveryReplayPinRef.current = null;
    changePriorityRefreshEpochRef.current += 1;
    analysisProgramCatalogLoadRef.current = null;
    returnWorkspaceRef.current = null;
    setMessage("");
    streamingDraftRef.current = null;
    setStreamingText("");
    setEvidence([]);
    setActivities([]);
    setPrograms([]);
    setProgramsLoading(false);
    setProgramFocusBusyId(undefined);
    setProgramNotice("");
    setActivePanel(null);
    setActiveRunId(null);
    setActiveRunState(null);
    setActiveChange(null);
    setDeferredChangeTransactionId(null);
    missionChangeTransactionIdRef.current = null;
    missionChangeRefreshEpochRef.current += 1;
    setMissionChangeTransaction(null);
    setChangeNotice("");
    setCancelNotice("");
  }, []);

  const startFreshConversation = useCallback(
    async (title: string): Promise<Conversation | null> => {
      const activationEpoch = ++targetActivationEpochRef.current;
      const refreshEpoch = ++conversationRefreshEpochRef.current;
      streamRef.current?.stop();
      clearStoredConversationId();
      conversationIdRef.current = null;
      setConversation(null);
      setNewProjectPlanning(null);
      resetConversationScopedUi();

      const created = await hostApi.createConversation({ title });
      if (
        activationEpoch !== targetActivationEpochRef.current ||
        refreshEpoch !== conversationRefreshEpochRef.current
      ) {
        return null;
      }
      storeConversationId(created.conversationId);
      conversationIdRef.current = created.conversationId;
      setConversation(created);
      startEventStream(created.conversationId, 0);
      return created;
    },
    [resetConversationScopedUi, startEventStream],
  );

  const ensureConversation = useCallback(async (
    currentStatus: HostStatusResponse,
    targetActivationEpoch?: number,
  ): Promise<Conversation | null> => {
    const requestEpoch = ++conversationRefreshEpochRef.current;
    const stale = () =>
      requestEpoch !== conversationRefreshEpochRef.current ||
      (targetActivationEpoch !== undefined &&
        targetActivationEpoch !== targetActivationEpochRef.current);
    const restoreIfMatching = async (
      conversationId: string,
    ): Promise<Conversation | null> => {
      const detail = await hostApi.getConversation(conversationId);
      if (stale()) {
        return null;
      }
      if (
        !conversationMatchesCurrentTarget(detail, currentStatus) &&
        !conversationMissionMatchesCurrentTarget(detail, currentStatus) &&
        !isReusableUnboundDraft(detail, currentStatus)
      ) {
        return null;
      }
      conversationIdRef.current = detail.conversationId;
      setConversation(detail);
      setNewProjectPlanning(planningSessionFromConversation(detail));
      return detail;
    };

    const storedId = readStoredConversationId();
    if (storedId) {
      try {
        const restored = await restoreIfMatching(storedId);
        if (stale()) {
          return null;
        }
        if (restored) {
          return restored;
        }
        clearStoredConversationId();
      } catch (error) {
        if (stale()) {
          return null;
        }
        if (!(error instanceof HostApiError) || error.status !== 404) {
          throw error;
        }
        clearStoredConversationId();
      }
    }

    // A target-bound engineering conversation may be recovered only for the
    // exact currently attached target. When no target is attached, selecting
    // an arbitrary historical planning conversation surprises the user and
    // can put the wrong machine/project context on screen after restart.
    if (currentEngineeringTarget(currentStatus)) {
      const recent = await hostApi.listConversations(100);
      if (stale()) {
        return null;
      }
      const latest = recent.items.find((item) =>
        conversationMatchesCurrentTarget(item, currentStatus) ||
        conversationMissionMatchesCurrentTarget(item, currentStatus),
      );
      if (latest) {
        try {
          const restored = await restoreIfMatching(latest.conversationId);
          if (stale()) {
            return null;
          }
          if (restored) {
            storeConversationId(restored.conversationId);
            return restored;
          }
        } catch (error) {
          if (stale()) {
            return null;
          }
          if (!(error instanceof HostApiError) || error.status !== 404) {
            throw error;
          }
        }
      }
    }

    changeRecoveryReplayPinRef.current = null;
    changePriorityRefreshEpochRef.current += 1;
    const created = await hostApi.createConversation({
      title: "PLC 분석 대화",
    });
    if (stale()) {
      return null;
    }
    storeConversationId(created.conversationId);
    conversationIdRef.current = created.conversationId;
    setConversation(created);
    return created;
  }, []);

  const activateEngineeringTargetConversation = useCallback(
    async (nextStatus: HostStatusResponse): Promise<Conversation | null> => {
      const activationEpoch = ++targetActivationEpochRef.current;
      applyHostStatus(nextStatus);
      if (!currentEngineeringTarget(nextStatus)) {
        return null;
      }
      const previousConversationId = conversationIdRef.current;
      const activeConversation = await ensureConversation(
        nextStatus,
        activationEpoch,
      );
      if (
        activeConversation === null ||
        activationEpoch !== targetActivationEpochRef.current
      ) {
        return null;
      }
      if (previousConversationId !== activeConversation.conversationId) {
        resetConversationScopedUi();
      }
      if (previousConversationId !== activeConversation.conversationId) {
        startEventStream(activeConversation.conversationId, 0);
      }
      return activeConversation;
    },
    [
      applyHostStatus,
      ensureConversation,
      resetConversationScopedUi,
      startEventStream,
    ],
  );

  const openConversationHistory = useCallback(async () => {
    setConversationHistoryOpen(true);
    setConversationHistoryLoading(true);
    setConversationHistoryNotice("");
    try {
      const page = await hostApi.listConversations(100);
      setConversationHistory(page.items);
    } catch (error) {
      setConversationHistoryNotice(
        error instanceof HostApiError
          ? error.message
          : "작업 기록을 불러오지 못했습니다.",
      );
    } finally {
      setConversationHistoryLoading(false);
    }
  }, []);

  const selectConversationHistory = useCallback(
    async (conversationId: string) => {
      if (conversationId === conversationIdRef.current) {
        setConversationHistoryOpen(false);
        return;
      }
      const activationEpoch = ++targetActivationEpochRef.current;
      const refreshEpoch = ++conversationRefreshEpochRef.current;
      setConversationHistorySelectingId(conversationId);
      setConversationHistoryNotice("");
      try {
        const detail = await hostApi.getConversation(conversationId);
        if (
          activationEpoch !== targetActivationEpochRef.current ||
          refreshEpoch !== conversationRefreshEpochRef.current
        ) {
          return;
        }
        streamRef.current?.stop();
        resetConversationScopedUi();
        storeConversationId(detail.conversationId);
        conversationIdRef.current = detail.conversationId;
        setConversation(detail);
        setNewProjectPlanning(planningSessionFromConversation(detail));
        startEventStream(detail.conversationId, 0);
        setConversationHistoryOpen(false);
        const matchesCurrentTarget =
          conversationMatchesCurrentTarget(detail, status) ||
          conversationMissionMatchesCurrentTarget(detail, status);
        if (detail.workflow === "engineering" && matchesCurrentTarget) {
          setNewProjectBriefOpen(false);
          setNativeProjectOpen(false);
          setLocalProjectOpen(false);
          setInstancePickerOpen(false);
          setOnlineControllerOpen(false);
          setWorkChooserOpen(false);
        }
        setConnectionMessage(
          matchesCurrentTarget
            ? "저장된 작업 기록을 열었습니다."
            : "현재 엔지니어링 대상과 다른 작업 기록을 읽기 전용으로 열었습니다.",
        );
      } catch (error) {
        if (
          activationEpoch === targetActivationEpochRef.current &&
          refreshEpoch === conversationRefreshEpochRef.current
        ) {
          setConversationHistoryNotice(
            error instanceof HostApiError
              ? error.message
              : "선택한 작업 기록을 열지 못했습니다.",
          );
        }
      } finally {
        setConversationHistorySelectingId((current) =>
          current === conversationId ? null : current,
        );
      }
    },
    [resetConversationScopedUi, startEventStream, status],
  );

  const connectHost = useCallback(async () => {
    requestAbortRef.current?.abort();
    changeRecoveryReplayPinRef.current = null;
    changePriorityRefreshEpochRef.current += 1;
    const controller = new AbortController();
    requestAbortRef.current = controller;

    setConnectionMessage("ControlStudio Host를 확인하고 있습니다.");
    try {
      await establishLauncherSession();
      if (controller.signal.aborted) {
        return;
      }
      const bootstrap = await hostApi.getBootstrap(controller.signal);
      setSessionRequired(false);
      hostApi.setCsrfToken(bootstrap.csrfToken);
      applyHostStatus(bootstrap.status);
      const activeOperation = bootstrap.status.activeOperation;
      const operationConversationId = activeOperation?.conversationId;
      const targetAtBootstrap = currentEngineeringTarget(bootstrap.status);
      let operationConversation: Conversation | null = null;
      let restoreActiveOperation = !operationConversationId;
      if (operationConversationId) {
        try {
          operationConversation = await hostApi.getConversation(
            operationConversationId,
            controller.signal,
          );
          // A global/quarantined operation from another workspace is still
          // available in history, but must not replace the current target's
          // conversation or make its composer read-only after startup.
          restoreActiveOperation =
            targetAtBootstrap === null ||
            conversationMatchesCurrentTarget(
              operationConversation,
              bootstrap.status,
            ) ||
            conversationMissionMatchesCurrentTarget(
              operationConversation,
              bootstrap.status,
            );
        } catch (error) {
          if (!(error instanceof HostApiError) || error.status !== 404) {
            throw error;
          }
          restoreActiveOperation = false;
        }
      }
      const activeNativeProject =
        restoreActiveOperation &&
        activeOperation?.kind === "native_project_create" &&
        activeOperation.nativeProjectId
          ? activeOperation.nativeProjectId
          : null;
      const recoverableChangeId =
        restoreActiveOperation &&
        activeOperation?.kind === "change" &&
        activeOperation.transactionId
          ? activeOperation.transactionId
          : null;
      if (!restoreActiveOperation) {
        setActiveRunId(null);
        setActiveRunState(null);
        setActiveChange(null);
        setDeferredChangeTransactionId(null);
      }
      if (activeNativeProject) {
        setNativeProjectRestoreId(activeNativeProject);
        setLocalProjectOpen(false);
        setInstancePickerOpen(false);
        setOnlineControllerOpen(false);
        setNativeProjectOpen(true);
      }
      if (restoreActiveOperation && activeOperation?.runId) {
        setActiveRunId(activeOperation.runId);
        if (isRunState(activeOperation.state)) {
          setActiveRunState(activeOperation.state);
        }
      }
      let activeConversation: Conversation;
      if (operationConversationId && restoreActiveOperation) {
        try {
          activeConversation = await refreshConversation(
            operationConversationId,
            true,
          );
          storeConversationId(activeConversation.conversationId);
        } catch (error) {
          if (!(error instanceof HostApiError) || error.status !== 404) {
            throw error;
          }
          const restored = await ensureConversation(bootstrap.status);
          if (restored === null) {
            return;
          }
          activeConversation = restored;
        }
      } else {
        const restored = await ensureConversation(bootstrap.status);
        if (restored === null) {
          return;
        }
        activeConversation = restored;
      }
      if (controller.signal.aborted) {
        return;
      }
      const restoredPlanning =
        planningSessionFromConversation(activeConversation);
      if (restoredPlanning) {
        setNewProjectPlanning(restoredPlanning);
      }
      const recoverableChange = recoverableChangeId
        ? await hostApi.getChangeTransaction(
            recoverableChangeId,
            controller.signal,
          )
        : null;
      if (controller.signal.aborted) {
        return;
      }
      const recentChanges = activeNativeProject
        ? []
        : await hostApi.listChangeTransactions(
            activeConversation.conversationId,
            20,
            controller.signal,
          );
      if (controller.signal.aborted) {
        return;
      }
      const restorableChange = activeNativeProject
        ? null
        : recoverableChange &&
            recoverableChange.conversationId ===
              activeConversation.conversationId &&
            isRestorableChange(recoverableChange)
          ? recoverableChange
          : recentChanges.find(
              (transaction) => isRestorableChange(transaction),
            ) ?? null;
      changePriorityRefreshEpochRef.current += 1;
      changeRecoveryReplayPinRef.current = restorableChange
        ? {
            transactionId: restorableChange.transactionId,
            throughSequence: bootstrap.lastEventSequence,
          }
        : null;
      setActiveChange(restorableChange ?? null);
      setConnectionMessage(
        restorableChange
          ? "Host와 대화를 복원하고 확인 또는 복구가 필요한 변경 계약을 다시 불러왔습니다."
          : activeNativeProject
            ? "중단되거나 격리된 새 프로젝트 생성 작업을 다시 불러왔습니다. 생성 요청은 재실행하지 않습니다."
            : restoredPlanning
              ? "Host와 신규 프로젝트 계획 대화를 복원했습니다."
              : "Host와 서버 대화가 준비되었습니다. 엔지니어링 요청을 시작할 수 있습니다.",
      );
      startEventStream(
        activeConversation.conversationId,
        0,
      );
    } catch (error) {
      if (!controller.signal.aborted) {
        const authenticationRequired =
          error instanceof HostApiError &&
          (error.status === 401 || error.status === 403);
        if (authenticationRequired) {
          clearBrowserSession();
        }
        setSessionRequired(authenticationRequired);
        applyHostStatus({
          ...INITIAL_STATUS,
          host: {
            state: "unavailable",
            detail: authenticationRequired
              ? "Launcher 세션 인증 필요"
              : "Launcher 또는 Host를 시작해 주세요",
          },
        });
        setConnectionMessage(
          error instanceof Error && error.name === "AbortError"
            ? "Host 확인이 취소되었습니다."
            : authenticationRequired
              ? "ControlStudio Launcher에서 다시 열어 세션을 인증해 주세요."
            : "Host에 연결할 수 없습니다. ControlStudio를 다시 시작해 주세요.",
        );
      }
    }
  }, [applyHostStatus, ensureConversation, startEventStream]);

  const refreshStatus = useCallback(async () => {
    try {
      const nextStatus = await hostApi.getStatus();
      applyHostStatus(nextStatus);
      setConnectionMessage("Host 상태를 다시 확인했습니다.");
    } catch {
      setConnectionMessage("Host 상태를 다시 확인하지 못했습니다.");
    }
  }, [applyHostStatus]);

  const openLocalProject = async (openInXg5000: boolean) => {
    const sourcePath = localProjectPath.trim();
    if (!sourcePath || localProjectBusy) {
      return;
    }
    setLocalProjectBusy(true);
    setLocalProjectNotice("");
    try {
      const result = await hostApi.openLocalWorkspace({
        sourcePath,
        openInXg5000,
        timeoutSeconds: 120,
      });
      const nextStatus = await hostApi.getStatus();
      if (currentEngineeringTarget(nextStatus)) {
        await activateEngineeringTargetConversation(nextStatus);
        setWorkChooserOpen(false);
        setLocalProjectOpen(false);
        setConnectionMessage(
          result.session && nextStatus.engineeringSystem.readReady
            ? "원본을 보존한 작업 복사본을 XG5000에서 열고 분석 세션에 연결했습니다."
            : "원본을 보존한 작업 복사본의 정적 구조 분석을 시작했습니다. LD 의미 분석은 XG5000 연결 뒤 사용할 수 있습니다.",
        );
        if (nextStatus.agent.state === "ready") {
          composerRef.current?.focus();
        } else {
          setSettingsOpen(true);
        }
      } else if (result.session) {
        setLocalProjectNotice(
          "기존 XG5000 실행 창에서는 프로젝트 정체성을 확인하지 못해 분석을 시작하지 않았습니다. 해당 XG5000 창을 닫고 다시 시도하면 ControlStudio가 새 읽기 전용 창을 안전하게 엽니다.",
        );
      } else if (result.launch?.windowDetected) {
        setLocalProjectNotice(
          "작업 복사본은 XG5000에서 열렸지만 자동 연결을 완료하지 못했습니다. 실행 창 선택에서 연결해 주세요.",
        );
      } else {
        applyHostStatus(nextStatus);
        setLocalProjectNotice(
          "안전한 작업 복사본은 만들었습니다. XG5000 창을 확인한 뒤 실행 창 선택으로 연결해 주세요.",
        );
      }
    } catch (error) {
      if (error instanceof HostApiError && error.status === 409) {
        // The Host commits the source-preserving workspace before it launches
        // XG5000. Continue with its static target when only the launch fails.
        try {
          const nextStatus = await hostApi.getStatus();
          if (currentEngineeringTarget(nextStatus)) {
            await activateEngineeringTargetConversation(nextStatus);
            setWorkChooserOpen(false);
            setLocalProjectOpen(false);
            setConnectionMessage(
              `${error.message} 작업 복사본의 정적 구조 분석은 계속 사용할 수 있습니다.`,
            );
            if (nextStatus.agent.state === "ready") {
              composerRef.current?.focus();
            } else {
              setSettingsOpen(true);
            }
            return;
          }
          applyHostStatus(nextStatus);
        } catch {
          // Keep the original operation failure as the user-facing result.
        }
      }
      setLocalProjectNotice(
        error instanceof HostApiError
          ? error.message
          : "로컬 프로젝트를 열지 못했습니다.",
      );
    } finally {
      setLocalProjectBusy(false);
    }
  };

  const browseLocalProject = async () => {
    if (localProjectBrowseBusy || localProjectBusy) {
      return;
    }
    setLocalProjectBrowseBusy(true);
    setLocalProjectNotice("");
    try {
      const result = await hostApi.selectXG5000ProjectFile();
      if (result.selected && result.path) {
        setLocalProjectPath(result.path);
        setLocalProjectNotice(
          "프로젝트를 선택했습니다. 복사본으로 열기를 눌러 계속하세요.",
        );
      }
    } catch (error) {
      setLocalProjectNotice(
        error instanceof HostApiError
          ? error.message
          : "Windows 프로젝트 선택 창을 열지 못했습니다.",
      );
    } finally {
      setLocalProjectBrowseBusy(false);
    }
  };

  const resumeWorkspace = async () => {
    const workspaceId = status.workspace?.workspaceId;
    if (!workspaceId || workspaceResumeBusy) {
      return;
    }
    setWorkspaceResumeBusy(true);
    setWorkspaceResumeNotice("");
    try {
      const result = await hostApi.resumeLocalWorkspace(workspaceId, {
        timeoutSeconds: 120,
      });
      const nextStatus = await hostApi.getStatus();
      if (result.session && nextStatus.engineeringSystem.readReady) {
        const pendingNativeProjectId =
          nextStatus.activeOperation?.kind === "native_project_create" &&
          nextStatus.activeOperation.nativeProjectId
            ? nextStatus.activeOperation.nativeProjectId
            : nativeProjectRestoreId;
        if (pendingNativeProjectId) {
          applyHostStatus(nextStatus);
          // An explicitly accepted native project keeps its durable planning
          // lineage until the Host revalidates the new read-ready session and
          // idempotently creates or rebinds the provenance conversation.
          setNativeProjectRestoreId(pendingNativeProjectId);
          setNativeProjectOpen(true);
        } else {
          // Restore an exact durable mission for this workspace/revision before
          // falling back to a new target-bound engineering conversation.
          await activateEngineeringTargetConversation(nextStatus);
        }
        setWorkChooserOpen(false);
        setLocalProjectOpen(false);
        setConnectionMessage(
          pendingNativeProjectId
            ? "The accepted native workspace was revalidated; its exact planning, build, and XG-SIM handoff is being rebound."
            : "저장된 작업 복사본과 해시를 다시 확인하고 XG5000 분석 세션을 복원했습니다.",
        );
        if (!pendingNativeProjectId && nextStatus.agent.state === "ready") {
          composerRef.current?.focus();
        } else if (!pendingNativeProjectId) {
          setSettingsOpen(true);
        }
      } else {
        applyHostStatus(nextStatus);
        setWorkspaceResumeNotice(
          "작업 복사본은 확인했지만 XG5000 분석 세션을 다시 연결하지 못했습니다. 실행 창을 확인해 주세요.",
        );
      }
    } catch (error) {
      setWorkspaceResumeNotice(
        error instanceof HostApiError
          ? error.message
          : "저장된 작업을 다시 열지 못했습니다.",
      );
    } finally {
      setWorkspaceResumeBusy(false);
    }
  };

  const loadEngineeringPrograms = async () => {
    if (programsLoading) {
      return;
    }
    setProgramsLoading(true);
    setProgramNotice("");
    try {
      const result = await hostApi.listEngineeringPrograms();
      setPrograms(result.programs);
      if (result.programs.length === 0) {
        setProgramNotice("");
      }
    } catch (error) {
      setPrograms([]);
      setProgramNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 프로그램 목록을 불러오지 못했습니다.",
      );
    } finally {
      setProgramsLoading(false);
    }
  };

  const focusEngineeringProgram = async (programId: string) => {
    if (programFocusBusyId) {
      return;
    }
    setProgramFocusBusyId(programId);
    setProgramNotice("");
    try {
      const result = await hostApi.focusEngineeringProgram(programId);
      setProgramNotice(
        `${result.program.name} 프로그램을 XG5000에서 열었습니다. 프로젝트 내용은 변경되지 않았습니다.`,
      );
    } catch (error) {
      setProgramNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000에서 프로그램을 열지 못했습니다.",
      );
    } finally {
      setProgramFocusBusyId(undefined);
    }
  };

  const openInstancePicker = async (recoverExistingTarget = false) => {
    setRecoveryAttachIntent(recoverExistingTarget);
    setInstancePickerOpen(true);
    setInstancesLoading(true);
    setRestorePreflight(null);
    setInstanceNotice("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      if (response.instances.length === 0) {
        setInstanceNotice(
          "실행 중인 XG5000 창을 찾지 못했습니다. XG5000에서 프로젝트를 연 뒤 다시 확인해 주세요.",
        );
      }
    } catch (error) {
      setInstances([]);
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 실행 창을 확인하지 못했습니다.",
      );
    } finally {
      setInstancesLoading(false);
    }
  };

  const attachInstance = async (instance: XG5000Instance) => {
    const attachForReviewedClose = recoveryAttachIntent;
    setAttachingHwnd(instance.hwnd);
    setInstanceNotice("");
    try {
      await hostApi.attachXG5000({
        pid: instance.pid,
        hwnd: instance.hwnd,
        // A locally opened static workspace can coexist with an older XG5000
        // process.  In that recovery state the Host must discover and prove
        // the selected window's identity itself, never inherit the new
        // workspace's path/hash.
        allowUnboundExistingTarget: attachForReviewedClose,
      });
      const nextStatus = await hostApi.getStatus();
      if (
        nextStatus.engineeringSystem.sessionVerified ||
        nextStatus.engineeringSystem.readReady
      ) {
        if (attachForReviewedClose) {
          applyHostStatus(nextStatus);
          setInstancePickerOpen(false);
          setLocalProjectOpen(true);
          setConnectionMessage(
            "선택한 기존 XG5000 창의 정체성을 확인했습니다. 저장 없이 닫으려면 표시된 PID/HWND를 검토하세요.",
          );
          return;
        }
        await activateEngineeringTargetConversation(nextStatus);
        setWorkChooserOpen(false);
        setInstancePickerOpen(false);
        setConnectionMessage(
          nextStatus.engineeringSystem.sessionVerified
            ? "선택한 XG5000 PID와 프로젝트 정체성을 검증했습니다."
            : "ControlStudio가 연 XG5000 PID에 읽기 전용 분석 세션을 연결했습니다. 변경 권한은 닫혀 있습니다.",
        );
        if (nextStatus.agent.state === "ready") {
          composerRef.current?.focus();
        } else {
          setSettingsOpen(true);
        }
      } else {
        applyHostStatus(nextStatus);
        setInstanceNotice(
          "이 실행 창에서는 프로젝트 정체성을 확인할 수 없어 분석을 시작하지 않았습니다. 창을 닫고 기존 로컬 프로젝트 열기에서 다시 열어 주세요.",
        );
        setConnectionMessage(
          "읽기 전용 분석 대상 확인에 실패했습니다. 기존 XG5000 창에는 연결하지 않았습니다.",
        );
      }
    } catch (error) {
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 분석 세션을 연결하지 못했습니다.",
      );
    } finally {
      setAttachingHwnd(null);
    }
  };

  const prepareOfflineCandidateSession = async () => {
    if (offlineCandidateBindingBusy) {
      return;
    }
    setOfflineCandidateBindingBusy(true);
    setConnectionMessage("");
    try {
      await hostApi.prepareOfflineCandidateSession();
      const nextStatus = await hostApi.getStatus();
      applyHostStatus(nextStatus);
      if (!nextStatus.engineeringSystem.sessionVerified) {
        setConnectionMessage(
          "오프라인 후보 세션의 PID와 작업 복사본 정체성을 확인하지 못했습니다.",
        );
        return;
      }
      await activateEngineeringTargetConversation(nextStatus);
      setChangeRequestScope("offline_candidate_revision");
      setVerificationScope("offline_native_reopen");
      setConnectionMessage(
        "보호 작업 복사본을 승인 가능한 오프라인 후보 대상으로 준비했습니다. 변경은 제안과 승인 뒤에만 적용됩니다.",
      );
      composerRef.current?.focus();
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "오프라인 후보 편집 세션을 준비하지 못했습니다. XG5000 창과 작업 복사본은 변경하지 않았습니다.",
      );
    } finally {
      setOfflineCandidateBindingBusy(false);
    }
  };

  const focusEngineeringLocation = async (
    conversationId: string,
    messageId: string,
    programId: string,
    location: EngineeringLocation,
  ) => {
    if (programFocusBusyId) {
      return;
    }
    setProgramFocusBusyId(programId);
    setProgramNotice("");
    try {
      const result = await hostApi.focusEngineeringLocation(
        conversationId,
        messageId,
        location,
      );
      setProgramNotice(
        `${result.program.name}의 화면 행 ${result.location.visualRow} 위치를 현재 분석 근거와 대조한 뒤 XG5000에 표시했습니다.`,
      );
    } catch (error) {
      setProgramNotice(
        error instanceof HostApiError
          ? error.message
          : "현재 분석 근거의 정확한 XG5000 위치를 열지 못했습니다.",
      );
    } finally {
      setProgramFocusBusyId(undefined);
    }
  };

  const preflightControllerProjectRestore = async (
    instance: XG5000Instance,
  ) => {
    setRestorePreflightBusyHwnd(instance.hwnd);
    setRestorePreflight(null);
    setRestoreHandoff(null);
    setInstanceNotice("");
    try {
      const receipt = await hostApi.preflightControllerProjectRestore({
        pid: instance.pid,
        hwnd: instance.hwnd,
      });
      setRestorePreflight(receipt);
    } catch (error) {
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 복원 메뉴 표면을 확인하지 못했습니다.",
      );
    } finally {
      setRestorePreflightBusyHwnd(null);
    }
  };

  const beginControllerProjectRestore = async (instance: XG5000Instance) => {
    if (
      restorePreflight?.ready !== true ||
      restorePreflight.target.pid !== instance.pid ||
      restorePreflight.target.hwnd !== instance.hwnd
    ) {
      setInstanceNotice("먼저 이 XG5000 창의 복원 표면을 확인하세요.");
      return;
    }
    setRestoreActionBusy(true);
    setInstanceNotice("");
    try {
      const handoff = await hostApi.beginControllerProjectRestore({
        pid: instance.pid,
        hwnd: instance.hwnd,
        operationId: newCanonicalUuid(),
        timeoutSeconds: 15,
      });
      setRestoreHandoff(handoff);
      setInstanceNotice(
        "XG5000의 PLC에서 열기 창에서 연결 대상을 선택하고 업로드를 끝낸 다음 ‘업로드 완료’ 버튼을 누르세요.",
      );
    } catch (error) {
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "XG5000 PLC 프로젝트 읽기 창을 열지 못했습니다.",
      );
    } finally {
      setRestoreActionBusy(false);
    }
  };

  const completeControllerProjectRestore = async () => {
    if (!restoreHandoff) {
      return;
    }
    setRestoreActionBusy(true);
    setInstanceNotice("");
    try {
      const restored = await hostApi.completeControllerProjectRestore({
        operationId: restoreHandoff.operationId,
        timeoutSeconds: 90,
      });
      setRestoreHandoff(null);
      setWorkspaceCompare(null);
      const nextStatus = await hostApi.getStatus();
      if (
        nextStatus.engineeringSystem.sessionVerified ||
        nextStatus.engineeringSystem.readReady
      ) {
        await activateEngineeringTargetConversation(nextStatus);
        setInstancePickerOpen(false);
        setWorkChooserOpen(false);
      } else {
        applyHostStatus(nextStatus);
      }
      setConnectionMessage(
        `PLC 프로젝트를 immutable source와 working copy로 복원했습니다. SHA-256 ${restored.savedProjectSha256}`,
      );
      setInstanceNotice(
        restored.session
          ? "복원된 working copy를 새 XG5000 분석 세션으로 열었습니다."
          : "복원 작업공간은 저장됐습니다. 원본 업로드 창을 닫은 뒤 작업공간 계속하기로 분석 세션을 여세요.",
      );
    } catch (error) {
      setInstanceNotice(
        error instanceof HostApiError
          ? error.message
          : "업로드된 프로젝트를 검증된 작업공간으로 마무리하지 못했습니다.",
      );
    } finally {
      setRestoreActionBusy(false);
    }
  };

  const cancelActiveRun = async () => {
    const runId = activeRunId;
    if (!runId || cancelBusy) {
      return;
    }
    setCancelBusy(true);
    setCancelNotice("");
    try {
      const result = await hostApi.cancelRun(runId);
      setActiveRunId(result.runId);
      setActiveRunState(result.state);
      const stateLabel = RUN_COPY[result.state] ?? result.state;
      const message = result.idempotent
        ? `작업은 이미 종료 상태입니다: ${stateLabel}`
        : result.detail ?? `작업 취소 결과: ${stateLabel}`;
      setCancelNotice(message);
      setConnectionMessage(message);
      const refreshes: Promise<unknown>[] = [
        hostApi.getStatus().then((nextStatus) => applyHostStatus(nextStatus)),
      ];
      if (conversation?.conversationId) {
        refreshes.push(refreshConversation(conversation.conversationId));
      }
      await Promise.allSettled(refreshes);
    } catch (error) {
      const message =
        error instanceof HostApiError
          ? error.message
          : "작업 취소 요청에 실패했습니다.";
      setCancelNotice(message);
      setConnectionMessage(message);
    } finally {
      setCancelBusy(false);
    }
  };

  const shutdownControlStudio = async () => {
    setShutdownBusy(true);
    setShutdownNotice("");
    try {
      await hostApi.shutdownHost();
      streamRef.current?.stop();
      requestAbortRef.current?.abort();
      clearBrowserSession();
      setShutdownComplete(true);
      setShutdownDialogOpen(false);
    } catch (error) {
      setShutdownNotice(
        error instanceof HostApiError && error.status === 409
          ? `현재 작업 때문에 종료할 수 없습니다: ${error.message} 실행 중인 작업을 먼저 취소하거나 완료될 때까지 기다려 주세요.`
          : error instanceof HostApiError
            ? error.message
            : "ControlStudio Host 종료 요청에 실패했습니다.",
      );
    } finally {
      setShutdownBusy(false);
    }
  };

  const refreshChange = async () => {
    if (!activeChange || changeBusy) {
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      await refreshServerPriorityChange();
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "변경 계약 상태를 확인하지 못했습니다.",
      );
    } finally {
      setChangeBusy(false);
    }
  };

  const reconcileChange = async (
    pid: number,
    hwnd: number,
    options: {
      relaunchIfUnavailable?: boolean;
      restoreVerifiedBackup?: boolean;
      expectedCurrentProjectHash?: string;
    } = {},
  ) => {
    if (
      !activeChange ||
      activeChange.state !== "quarantined" ||
      changeBusy
    ) {
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      const response = await hostApi.reconcileChangeTransaction(
        activeChange.transactionId,
        {
          expectedPlanDigest: activeChange.planDigest,
          pid,
          hwnd,
          ...(options.relaunchIfUnavailable ? { relaunchIfUnavailable: true } : {}),
          ...(options.restoreVerifiedBackup ? { restoreVerifiedBackup: true } : {}),
          ...(options.expectedCurrentProjectHash
            ? { expectedCurrentProjectHash: options.expectedCurrentProjectHash }
            : {}),
        },
      );
      if (!response.reconciled) {
        setChangeNotice(
          "종료 증거가 충분하지 않아 격리를 유지합니다. 자동 재시도나 변경 재적용은 하지 않습니다.",
        );
      } else if (
        response.transaction.errorCode ===
        "explicit_recovery_aborted_before_mutation"
      ) {
        setChangeNotice(
          "변경 전에 중단된 사실을 확인했습니다. 제안 변경을 적용하지 않고 작업 잠금을 해제했습니다.",
        );
      } else if (response.transaction.state === "rolled_back") {
        setChangeNotice(
          "원래 상태로 원복하고 저장한 증거를 확인했습니다. 제안 변경을 다시 적용하지 않았습니다.",
        );
      } else if (response.transaction.state === "completed") {
        setChangeNotice(
          "이미 커밋된 네이티브 상태임을 확인했습니다. 복구 중 추가 적용은 하지 않았습니다.",
        );
      } else {
        setChangeNotice("격리 복구 상태와 증거가 갱신되었습니다.");
      }
      try {
        await refreshServerPriorityChange();
      } catch {
        setConnectionMessage(
          "복구 결과는 기록됐지만 우선순위 상태를 새로 읽지 못했습니다.",
        );
      }
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "격리된 변경의 복구 증거를 확인하지 못했습니다.",
      );
      void refreshServerPriorityChange().catch(() => undefined);
    } finally {
      setChangeBusy(false);
    }
  };

  const rejectChange = async () => {
    if (!activeChange || changeBusy) {
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      await hostApi.decideChangeTransaction(
        activeChange.transactionId,
        {
          decision: "reject",
          expectedPlanDigest: activeChange.planDigest,
        },
      );
      setChangeNotice("변경안을 거절했습니다. 실제 변경은 실행되지 않았습니다.");
      try {
        await refreshServerPriorityChange();
      } catch {
        setConnectionMessage(
          "거절은 기록됐지만 우선순위 상태를 새로 읽지 못했습니다.",
        );
      }
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "변경안 거절을 기록하지 못했습니다.",
      );
    } finally {
      setChangeBusy(false);
    }
  };

  const requestChangeRevision = async () => {
    if (
      !activeChange ||
      activeChange.state !== "pending_approval" ||
      changeBusy
    ) {
      return;
    }
    const transaction = activeChange;
    setChangeBusy(true);
    setChangeNotice("");
    try {
      const rejected = await hostApi.decideChangeTransaction(
        transaction.transactionId,
        {
          decision: "reject",
          expectedPlanDigest: transaction.planDigest,
        },
      );
      await hostApi.acknowledgeChangeReceipt(rejected.transactionId);
      setActiveChange(null);
      try {
        await refreshServerPriorityChange();
      } catch {
        setChangeNotice(
          "기존 변경안은 닫았지만 다음 변경 계약을 다시 불러오지 못했습니다.",
        );
      }
      setMessage(
        `"${transaction.intent}" 변경안을 실행하지 말고 다음 요구를 반영해 다시 조사·작성해 주세요: `,
      );
      window.setTimeout(() => composerRef.current?.focus(), 0);
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "기존 변경안을 닫고 수정 요청으로 돌아가지 못했습니다.",
      );
    } finally {
      setChangeBusy(false);
    }
  };

  const openActiveChangeTarget = async () => {
    if (!activeChange || changeBusy) {
      return;
    }
    setChangeNotice("");
    setChangeBusy(true);
    try {
      const result = await hostApi.focusChangeTarget(activeChange.transactionId);
      setChangeNotice(
        result.focusKind === "native_location"
          ? `${result.program.name}의 승인 대상 network/rung/operand 위치를 현재 XG5000에서 확인했습니다.`
          : result.focusKind === "mne_anchor" && result.mneAnchor
            ? `${result.program.name}의 승인 기준 MNE ${result.mneAnchor.instructionIndex + 1}행 (${result.mneAnchor.command}${result.mneAnchor.operand ? ` ${result.mneAnchor.operand}` : ""})을 현재 XG5000에서 복사 검증했습니다.`
            : `${result.program.name} 프로그램을 현재 XG5000에서 확인했습니다. 전체 프로그램 후보는 아래의 ordered target과 digest로 검토하세요.`,
      );
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "현재 변경 계약의 XG5000 대상을 열지 못했습니다.",
      );
    } finally {
      setChangeBusy(false);
    }
  };

  const restartControlStudio = async () => {
    setShutdownBusy(true);
    setShutdownNotice("");
    try {
      const restart = await hostApi.restartHost();
      streamRef.current?.stop();
      requestAbortRef.current?.abort();
      const deadline = Date.now() + 45_000;
      while (Date.now() < deadline) {
        try {
          const status = await hostApi.getRestartStatus(restart.restartReceipt);
          if (status.status === "ready") {
            window.location.reload();
            return;
          }
        } catch {
          // A refused connection is expected while the old listener hands off
          // to its already-elevated replacement. Never retry with UAC here.
        }
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      }
      setShutdownNotice(
        "Host 재시작 준비 시간이 초과되었습니다. UAC를 다시 요청하지 않았습니다. 상태를 확인한 뒤 Launcher에서 다시 열어 주세요.",
      );
    } catch (error) {
      setShutdownNotice(
        error instanceof HostApiError && error.status === 409
          ? `현재 작업 때문에 재시작할 수 없습니다: ${error.message}`
          : error instanceof HostApiError
            ? error.message
            : "ControlStudio Host 재시작 요청에 실패했습니다.",
      );
    } finally {
      setShutdownBusy(false);
    }
  };

  const executeApprovedTransaction = async (
    approved: ChangeTransaction,
  ): Promise<void> => {
    const executed = await hostApi.executeChangeTransaction(
      approved.transactionId,
      {
        expectedPlanDigest: approved.planDigest,
      },
    );
    setChangeNotice(
      executed.state === "completed"
        ? "변경 적용, 구조 diff, XG5000 프로그램 검사와 저장을 완료했습니다. 시뮬레이션·실제 PLC 검증은 별도입니다."
        : executed.state === "rolled_back"
          ? "오프라인 검사 결과에 따라 변경을 원래 상태로 복구했습니다."
          : executed.state === "quarantined"
            ? "실제 결과를 확정할 수 없어 작업을 격리했습니다. 자동 재실행하지 않습니다."
            : "변경 트랜잭션 상태가 갱신되었습니다.",
    );
    try {
      await refreshServerPriorityChange();
    } catch {
      setConnectionMessage(
        "변경 결과는 기록됐지만 우선순위 상태를 새로 읽지 못했습니다.",
      );
    }
  };

  const approveChange = async () => {
    if (!activeChange || activeChange.state !== "pending_approval" || changeBusy) {
      return;
    }
    const transactionId = activeChange.transactionId;
    setChangeBusy(true);
    setChangeNotice("");
    try {
      await hostApi.decideChangeTransaction(transactionId, {
        decision: "approve",
        expectedPlanDigest: activeChange.planDigest,
      });
      setChangeNotice(
        "정확한 변경 계약에 대한 승인을 기록했습니다. 실제 적용은 다음 단계에서 별도로 시작합니다.",
      );
      try {
        await refreshServerPriorityChange();
      } catch {
        setConnectionMessage(
          "승인은 기록됐지만 우선순위 상태를 새로 읽지 못했습니다.",
        );
      }
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "변경안 승인을 기록하지 못했습니다.",
      );
      void refreshServerPriorityChange().catch(() => undefined);
    } finally {
      setChangeBusy(false);
    }
  };

  const executeApprovedChange = async () => {
    if (!activeChange || activeChange.state !== "approved" || changeBusy) {
      return;
    }
    const approved = activeChange;
    setChangeBusy(true);
    setChangeNotice("적용 전 대상과 현재 상태를 다시 검증합니다.");
    try {
      await executeApprovedTransaction(approved);
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "승인된 변경을 실행하지 못했습니다.",
      );
      void refreshServerPriorityChange().catch(() => undefined);
    } finally {
      setChangeBusy(false);
    }
  };

  const acknowledgeChangeReceipt = async () => {
    if (
      !activeChange ||
      !TERMINAL_CHANGE_OUTCOME_STATES.has(activeChange.state) ||
      changeBusy
    ) {
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      await hostApi.acknowledgeChangeReceipt(activeChange.transactionId);
      setActiveChange(null);
      try {
        await refreshServerPriorityChange();
      } catch {
        setChangeNotice(
          "결과 확인은 기록했지만 다음 변경 계약을 다시 불러오지 못했습니다.",
        );
      }
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "변경 결과 확인을 기록하지 못했습니다.",
      );
    } finally {
      setChangeBusy(false);
    }
  };

  useEffect(() => {
    hostApi.setUnauthorizedHandler(() => {
      clearBrowserSession();
      streamRef.current?.stop();
      setSessionRequired(true);
      applyHostStatus({
        ...INITIAL_STATUS,
        host: {
          state: "unavailable",
          detail: "Launcher 세션 인증 필요",
        },
      });
      setConnectionMessage(
        "브라우저 세션이 만료되었습니다. ControlStudio Launcher에서 다시 열어 주세요.",
      );
    });
    void connectHost();
    return () => {
      hostApi.setUnauthorizedHandler(undefined);
      requestAbortRef.current?.abort();
      streamRef.current?.stop();
    };
  }, [connectHost]);

  useEffect(() => {
    const reconnectForBrowserHandoff = () => {
      const fragment = window.location.hash.startsWith("#")
        ? window.location.hash.slice(1)
        : window.location.hash;
      if (new URLSearchParams(fragment).get("handoff_ticket")) {
        void connectHost();
      }
    };
    window.addEventListener("hashchange", reconnectForBrowserHandoff);
    return () => {
      window.removeEventListener("hashchange", reconnectForBrowserHandoff);
    };
  }, [connectHost]);

  useEffect(() => {
    const focusFeature = status.featureReadiness?.find(
      (feature) => feature.featureId === "program_focus",
    );
    const locatedMessage = [...(conversation?.messages ?? [])]
      .reverse()
      .find((item) => {
        const analysis = item.engineeringAnalysis;
        return (
          (analysis?.locations?.length ?? 0) > 0 ||
          (analysis?.relations ?? []).some(
            (relation) => relation.locations.length > 0,
          )
        );
      });
    // A verified program-list tool result is enough to refresh the existing
    // read-only navigator; a concise answer need not also carry a location.
    const catalogEvidence = evidence.find(
      (item) =>
        (item as EvidenceRef & { toolName?: string }).toolName ===
        "project.list_programs",
    );
    const catalogTriggerId =
      catalogEvidence?.evidenceId ?? locatedMessage?.messageId;
    const catalogKey = catalogTriggerId
      ? `${status.workspace?.workspaceId ?? "active-session"}:${conversation?.conversationId ?? "none"}:${catalogTriggerId}`
      : null;
    const canLoad =
      !shutdownComplete &&
      hostReady &&
      mcpReady &&
      status.engineeringSystem.readReady &&
      status.engineeringSystem.application === "XG5000" &&
      focusFeature?.currentReady === true &&
      focusFeature.verification === "real_xg5000";
    if (
      catalogKey === null ||
      !canLoad ||
      programs.length > 0 ||
      programsLoading ||
      analysisProgramCatalogLoadRef.current === catalogKey
    ) {
      return;
    }
    analysisProgramCatalogLoadRef.current = catalogKey;
    void loadEngineeringPrograms();
  }, [
    conversation,
    evidence,
    hostReady,
    mcpReady,
    programs.length,
    programsLoading,
    shutdownComplete,
    status,
  ]);

  const enterAcceptedNativeEngineering = useCallback(
    async (
      operation: NativeProjectOperation,
      readyStatus: HostStatusResponse,
    ): Promise<void> => {
      const nativeProjectId = operation.nativeProjectId;
      const reviewedPlan = operation.request.reviewedPlan;
      const operationResult = operation.result;
      const readyTarget = currentEngineeringTarget(readyStatus);
      if (
        operationResult?.workspaceBound !== true ||
        !readyTarget
      ) {
        throw new Error(
          "Accepted native project no longer matches the active planning target.",
        );
      }
      const transitionKey = [
        nativeProjectId,
        readyTarget.workspaceId,
        readyTarget.revisionId,
        readyTarget.engineeringSessionId,
      ].join(":");
      while (engineeringHandoffTransitionRef.current) {
        const existingTransition = engineeringHandoffTransitionRef.current;
        if (existingTransition.transitionKey === transitionKey) {
          return existingTransition.promise;
        }
        try {
          await existingTransition.promise;
        } catch {
          // A failed transition clears its own slot; the current exact target
          // may then make one fresh, independently validated attempt.
        }
      }
      if (conversationIdRef.current !== reviewedPlan.conversationId) {
        throw new Error(
          "Accepted native project no longer matches the active planning conversation.",
        );
      }

      const transition = (async (): Promise<void> => {
        const created = await hostApi.createConversation({
          title: `${operation.request.projectName} engineering`,
          workflow: "engineering",
          nativeProjectId,
        });
        const handoff = created.engineeringHandoff;
        if (
          created.workflow !== "engineering" ||
          !conversationMatchesCurrentTarget(created, readyStatus) ||
          handoff?.schemaVersion !== "1" ||
          handoff.nativeProjectId !== nativeProjectId ||
          handoff.planningConversationId !== reviewedPlan.conversationId ||
          handoff.planningMessageId !== reviewedPlan.messageId ||
          handoff.reviewedPlanDigest !== reviewedPlan.planDigest ||
          handoff.buildBundleId !== operation.request.buildBundle.bundleId ||
          !/^[0-9a-f]{64}$/i.test(handoff.reopenReceiptDigest) ||
          handoff.projectSha256 !==
            operationResult.workspaceRegistration?.workspace?.workingCopy
              .currentSha256
        ) {
          throw new Error(
            "Host returned an engineering conversation with mismatched handoff provenance.",
          );
        }

        changeRecoveryReplayPinRef.current = null;
        changePriorityRefreshEpochRef.current += 1;
        streamRef.current?.stop();
        storeConversationId(created.conversationId);
        conversationIdRef.current = created.conversationId;
        applyHostStatus(readyStatus);
        setConversation(created);
        setNewProjectPlanning(null);
        setNativeProjectOpen(false);
        setNativeProjectRestoreId(null);
        setNewProjectBriefOpen(false);
        setWorkChooserOpen(false);
        setMessage("");
        setStreamingText("");
        setEvidence([]);
        setActivities([]);
        setPrograms([]);
        setProgramsLoading(false);
        setProgramFocusBusyId(undefined);
        setProgramNotice("");
        setActivePanel(null);
        setActiveRunId(null);
        setActiveRunState(null);
        setActiveChange(null);
        setChangeNotice("");
        setCancelNotice("");
        returnWorkspaceRef.current = null;
        startEventStream(created.conversationId, 0);
        setConnectionMessage(
          "검토한 계획, LD 생성·Program Check·저장과 독립 재열기 readback 근거를 엔지니어링 대화에 연결했습니다.",
        );
        window.setTimeout(() => composerRef.current?.focus(), 0);
      })();
      engineeringHandoffTransitionRef.current = {
        transitionKey,
        promise: transition,
      };
      try {
        await transition;
      } finally {
        if (engineeringHandoffTransitionRef.current?.promise === transition) {
          engineeringHandoffTransitionRef.current = null;
        }
      }
    },
    [applyHostStatus, startEventStream],
  );

  if (shutdownComplete) {
    return (
      <main className="shutdown-screen" aria-labelledby="shutdown-complete-title">
        <span className="shutdown-mark" aria-hidden="true">
          <Icon name="power" size={28} />
        </span>
        <p className="eyebrow">CONTROLSTUDIO HOST STOPPED</p>
        <h1 id="shutdown-complete-title">ControlStudio를 종료했습니다</h1>
        <p>
          Host와 MCP 프로세스에 종료를 요청했습니다. 이 브라우저 또는 PWA 창은
          안전하게 닫아도 됩니다.
        </p>
        <p className="shutdown-restart-note">
          다시 사용하려면 Windows의 ControlStudio 아이콘으로 실행해 주세요.
        </p>
      </main>
    );
  }

  const leaveNewProjectPlanning = async () => {
    if (canCancelActiveRun || sending) {
      setCancelNotice(
        "실행 중인 계획 작업을 먼저 취소하거나 완료될 때까지 기다려 주세요.",
      );
      return;
    }
    const returnWorkspace = returnWorkspaceRef.current;
    returnWorkspaceRef.current = null;
    setNewProjectPlanning(null);
    setNewProjectBriefOpen(false);
    setNativeProjectOpen(false);
    setWorkChooserOpen(false);
    setMessage("");
    setStreamingText("");
    setActiveRunId(null);
    setActiveRunState(null);
    streamRef.current?.stop();

    if (returnWorkspace) {
      storeConversationId(returnWorkspace.conversation.conversationId);
      conversationIdRef.current =
        returnWorkspace.conversation.conversationId;
      setConversation(returnWorkspace.conversation);
      setEvidence(returnWorkspace.evidence);
      setActivities(returnWorkspace.activities);
      setPrograms(returnWorkspace.programs);
      setActivePanel(returnWorkspace.activePanel);
      startEventStream(returnWorkspace.conversation.conversationId, 0);
      setConnectionMessage("기존 엔지니어링 작업으로 돌아왔습니다.");
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return;
    }

    setEvidence([]);
    setActivities([]);
    setPrograms([]);
    setActivePanel(null);
    clearStoredConversationId();
    conversationIdRef.current = null;
    setConversation(null);
    setWorkChooserOpen(true);
    setConnectionMessage("새 프로젝트 계획을 닫았습니다.");
  };

  const openWorkChooser = () => {
    if (activeChange) {
      setDeferredChangeTransactionId(activeChange.transactionId);
      setConnectionMessage(
        "확인이 필요한 이전 변경은 작업 기록에 보존했습니다. 새 작업을 시작할 수 있습니다.",
      );
    }
    if (conversation?.workflow === "engineering") {
      returnWorkspaceRef.current = {
        conversation,
        evidence,
        activities,
        programs,
        activePanel,
      };
    }
    setNativeProjectOpen(false);
    setNewProjectBriefOpen(false);
    setLocalProjectOpen(false);
    setInstancePickerOpen(false);
    setOnlineControllerOpen(false);
    setWorkChooserOpen(true);
  };

  const returnToCurrentWork = () => {
    setNativeProjectOpen(false);
    setNewProjectBriefOpen(false);
    setLocalProjectOpen(false);
    setInstancePickerOpen(false);
    setOnlineControllerOpen(false);
    setWorkChooserOpen(false);
    returnWorkspaceRef.current = null;
    window.setTimeout(() => composerRef.current?.focus(), 0);
  };

  const closeNewProjectBrief = () => {
    setNewProjectBriefOpen(false);
    if (newProjectPlanning) {
      setWorkChooserOpen(false);
      return;
    }
    if (returnWorkspaceRef.current) {
      setWorkChooserOpen(false);
      returnWorkspaceRef.current = null;
      window.setTimeout(() => composerRef.current?.focus(), 0);
      return;
    }
    setWorkChooserOpen(true);
  };

  const submitNewProjectBrief = async (brief: NewProjectBrief) => {
    if (
      !hostReady ||
      !planningAgentReady ||
      sending ||
      canCancelActiveRun
    ) {
      if (canCancelActiveRun) {
        setCancelNotice(
          "실행 중인 계획 작업을 먼저 취소하거나 완료될 때까지 기다려 주세요.",
        );
      }
      return;
    }

    const updating = Boolean(newProjectPlanning);
    const previousConversation = conversation;
    const previousPlanning = newProjectPlanning;
    const requestContent = newProjectBriefMessage(brief, updating);
    const requestOperationId = newOperationId();
    let createdPlanningConversation = false;
    let planningConversationId =
      updating && conversation?.workflow === "new_project_planning"
        ? conversation.conversationId
        : null;
    setSending(true);
    setCancelNotice("");
    setStreamingText("");
    setConnectionMessage(
      updating
        ? "수정한 요구사항을 계획 에이전트에 전달하고 있습니다."
        : "새 프로젝트 계획 대화를 준비하고 있습니다.",
    );

    try {
      let planningConversation = conversation;
      if (!updating || !planningConversation) {
        changeRecoveryReplayPinRef.current = null;
        changePriorityRefreshEpochRef.current += 1;
        planningConversation = await hostApi.createConversation({
          title: `${brief.projectName} · 신규 설계`,
          workflow: "new_project_planning",
          projectBrief: brief,
        });
        createdPlanningConversation = true;
        planningConversationId = planningConversation.conversationId;
        storeConversationId(planningConversation.conversationId);
        conversationIdRef.current = planningConversation.conversationId;
        setConversation(planningConversation);
        setEvidence([]);
        setActivities([]);
        setActiveRunId(null);
        setActiveRunState(null);
        startEventStream(planningConversation.conversationId, 0);
      }

      const result = await hostApi.sendMessage(
        planningConversation.conversationId,
        {
          content: requestContent,
          operationId: requestOperationId,
          workflow: "new_project_planning",
          projectBrief: brief,
        },
      );
      setNewProjectPlanning({ brief });
      setWorkChooserOpen(false);
      setNewProjectBriefOpen(false);
      setNativeProjectOpen(false);
      setActiveChange(null);
      setChangeNotice("");
      setActiveRunId(result.runId);
      setActiveRunState(result.state);
      await refreshConversation(planningConversation.conversationId);
      setConnectionMessage(
        "요구사항을 바탕으로 계획서를 만들고 있습니다. 파일과 XG5000은 변경하지 않습니다.",
      );
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "새 프로젝트 계획을 시작하지 못했습니다.",
      );
      if (planningConversationId) {
        let acceptedByServer = false;
        try {
          const detail = await hostApi.getConversation(
            planningConversationId,
          );
          acceptedByServer =
            detail.workflow === "new_project_planning" &&
            projectBriefMatches(detail.projectBrief, brief) &&
            detail.messages.some(
              (item) =>
                item.role === "user" &&
                item.workflow === "new_project_planning" &&
                item.operationId === requestOperationId,
            );
          if (acceptedByServer) {
            storeConversationId(detail.conversationId);
            conversationIdRef.current = detail.conversationId;
            setConversation(detail);
            setNewProjectPlanning(planningSessionFromConversation(detail));
            setWorkChooserOpen(false);
            setNewProjectBriefOpen(false);
            setNativeProjectOpen(false);
            try {
              const nextStatus = await hostApi.getStatus();
              applyHostStatus(nextStatus);
              if (
                nextStatus.activeOperation?.conversationId ===
                  detail.conversationId &&
                nextStatus.activeOperation.runId
              ) {
                setActiveRunId(nextStatus.activeOperation.runId);
                if (isRunState(nextStatus.activeOperation.state)) {
                  setActiveRunState(nextStatus.activeOperation.state);
                }
              }
            } catch {
              // The persisted message and SSE stream remain authoritative.
            }
            setConnectionMessage(
              "응답 연결은 끊겼지만 서버가 계획 요청을 보관했습니다. 실행 상태를 다시 확인해 주세요.",
            );
          }
        } catch {
          acceptedByServer = false;
        }

        if (!acceptedByServer) {
          setNewProjectPlanning(previousPlanning);
          setNewProjectBriefOpen(true);
          if (createdPlanningConversation) {
            streamRef.current?.stop();
            setActiveRunId(null);
            setActiveRunState(null);
            if (previousConversation) {
              storeConversationId(previousConversation.conversationId);
              conversationIdRef.current = previousConversation.conversationId;
              setConversation(previousConversation);
              startEventStream(previousConversation.conversationId, 0);
            } else {
              clearStoredConversationId();
              conversationIdRef.current = null;
              setConversation(null);
            }
          }
        }
      } else if (updating) {
        setNewProjectPlanning(previousPlanning);
        setNewProjectBriefOpen(true);
      }
    } finally {
      setSending(false);
    }
  };

  const sendMessage = async () => {
    const content = message.trim();
    const conversationId = conversation?.conversationId;
    if (!content || !conversationId || !canChat || sending) {
      return;
    }
    if (
      changeRequestScope === "offline_candidate_revision" &&
      !offlineCandidateAvailable
    ) {
      setConnectionMessage(
        "오프라인 후보를 만들려면 원본 보존 작업 복사본과 strong XG5000 대상 결속이 필요합니다.",
      );
      return;
    }
    if (
      verificationScope === "offline_native_reopen_xgsim" &&
      !mneXgSimAvailable
    ) {
      setConnectionMessage("XG-SIM 검증 capability가 현재 준비되지 않았습니다.");
      return;
    }
    setSending(true);
    setStreamingText("");
    setActivePanel("activity");
    setConnectionMessage(
      conversation.workflow === "new_project_planning"
        ? "계획 보완 요청을 에이전트에 전달하고 있습니다."
        : "엔지니어링 요청을 에이전트에 전달하고 있습니다.",
    );
    try {
      const result = await hostApi.sendMessage(conversationId, {
        content,
        operationId: newOperationId(),
        workflow: conversation.workflow,
        changeRequestScope: planningConversationActive
          ? undefined
          : changeRequestScope,
        verificationScope:
          planningConversationActive ||
          changeRequestScope !== "offline_candidate_revision"
            ? undefined
            : verificationScope,
      });
      setMessage("");
      setActiveRunId(result.runId);
      setActiveRunState(result.state);
      setCancelNotice("");
      await refreshConversation(conversationId);
      setConnectionMessage(
        planningConversationActive
          ? "계획 에이전트가 요구사항과 안전 조건을 반영해 계획서를 보완합니다."
          : "에이전트가 MCP 근거를 확인하고 필요한 경우 승인 가능한 변경안을 만듭니다.",
      );
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "메시지를 보내지 못했습니다.",
      );
    } finally {
      setSending(false);
    }
  };

  const finalizeObservedSimulation = async (decision: "accept" | "revert") => {
    if (!activeChange || activeChange.state !== "observing" || changeBusy) return;
    const verification = activeChange.result.verification;
    const receipt = verification && typeof verification === "object" && !Array.isArray(verification)
      ? (verification as Record<string, unknown>).simulation_receipt
      : undefined;
    const receiptDigest = receipt && typeof receipt === "object" && !Array.isArray(receipt)
      ? (receipt as Record<string, unknown>).receipt_digest
      : undefined;
    if (typeof receiptDigest !== "string" || !/^[0-9a-f]{64}$/.test(receiptDigest)) {
      setChangeNotice("XG-SIM receipt digest가 없어 Keep/Revert를 실행할 수 없습니다.");
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      const finalized = await hostApi.finalizeSimulationChange(activeChange.transactionId, {
        decision,
        expectedPlanDigest: activeChange.planDigest,
        expectedSimulationReceiptDigest: receiptDigest,
      });
      setChangeNotice(
        finalized.state === "completed"
          ? "XG-SIM receipt를 검토한 Keep 결정을 기록했습니다."
          : "원래 rung을 복구하고 저장·재열기 검증을 기록했습니다.",
      );
      await refreshServerPriorityChange();
    } catch (error) {
      setChangeNotice(error instanceof HostApiError ? error.message : "XG-SIM Keep/Revert 결정을 완료하지 못했습니다.");
    } finally {
      setChangeBusy(false);
    }
  };

  const compareWorkspace = async () => {
    const workspaceId = status.workspace?.workspaceId;
    if (!workspaceId || workspaceCompareBusy) {
      return;
    }
    setWorkspaceCompareBusy(true);
    setWorkspaceCompare(null);
    setWorkspaceResumeNotice("");
    try {
      const comparison = await hostApi.compareLocalWorkspace(workspaceId);
      setWorkspaceCompare(comparison);
    } catch (error) {
      setWorkspaceResumeNotice(
        error instanceof HostApiError
          ? error.message
          : "원본과 작업본을 비교하지 못했습니다.",
      );
    } finally {
      setWorkspaceCompareBusy(false);
    }
  };

  const resumeEngineeringMission = async () => {
    const conversationId = conversation?.conversationId;
    const mission = conversation?.engineeringMission;
    if (missionResumeBusy) {
      setConnectionMessage("저장된 엔지니어링 작업 재개 요청을 이미 처리 중입니다.");
      return;
    }
    const eligibility = evaluateMissionResumeEligibility();
    if (!eligibility.available) {
      setConnectionMessage(eligibility.unavailableMessage);
      return;
    }
    if (
      !conversationId ||
      !mission
    ) {
      setConnectionMessage("저장된 엔지니어링 작업을 찾지 못했습니다. 대화를 다시 불러와 주세요.");
      return;
    }

    setMissionResumeBusy(true);
    setStreamingText("");
    setConnectionMessage(
      mission.snapshot.status === "waiting_for_approval"
        ? "에이전트가 확정된 변경 결과를 확인하고 저장된 작업 계획을 계속 실행합니다."
        : "에이전트가 저장된 작업 계획의 다음 단계를 계속 실행합니다.",
    );
    try {
      const result = await hostApi.resumeEngineeringMission(conversationId, {
        operationId: newOperationId(),
      });
      setActiveRunId(result.runId);
      setActiveRunState(result.state);
      await refreshConversation(conversationId);
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "저장된 엔지니어링 작업을 재개하지 못했습니다.",
      );
    } finally {
      setMissionResumeBusy(false);
    }
  };

  const reverifyBlockedEngineeringMissionStep = async () => {
    const conversationId = conversation?.conversationId;
    const mission = conversation?.engineeringMission;
    if (missionReverifyBusy) {
      setConnectionMessage("차단 단계 재검증 요청을 이미 처리 중입니다.");
      return;
    }
    const eligibility = evaluateMissionReverifyEligibility();
    if (!eligibility.available) {
      setConnectionMessage(eligibility.unavailableMessage);
      return;
    }
    if (
      !conversationId ||
      !mission
    ) {
      setConnectionMessage("저장된 엔지니어링 작업을 찾지 못했습니다. 대화를 다시 불러와 주세요.");
      return;
    }
    setMissionReverifyBusy(true);
    try {
      await hostApi.reverifyBlockedEngineeringMissionStep(conversationId, {
        operationId: newOperationId(),
      });
      await refreshConversation(conversationId);
      setConnectionMessage(
        "차단 단계를 다시 검증할 준비가 되었습니다. 자동으로 Agent를 다시 실행하지 않습니다.",
      );
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "차단 단계를 다시 검증하지 못했습니다.",
      );
    } finally {
      setMissionReverifyBusy(false);
    }
  };

  const startNewEngineeringMission = async () => {
    // The button already reflects `canStartNewEngineeringMission`. Rechecking
    // that render-derived value here can silently discard an enabled click
    // before the fresh conversation POST is reached.
    if (missionStartBusy) {
      return;
    }
    setMissionStartBusy(true);
    setConnectionMessage("완료된 작업 기록을 보존하고 새 엔지니어링 작업을 준비합니다.");
    try {
      if (
        !status.engineeringSystem.readReady &&
        status.controller.endpoint &&
        conversation?.engineeringMission
      ) {
        await hostApi.getLSControllerStatus();
        const refreshedStatus = await hostApi.getStatus();
        applyHostStatus(refreshedStatus);
        const refreshedTarget = currentEngineeringTarget(refreshedStatus);
        if (
          !refreshedTarget ||
          refreshedTarget.workspaceId !==
            conversation.engineeringMission.workspaceId ||
          refreshedTarget.revisionId !== conversation.engineeringMission.revisionId
        ) {
          throw new HostApiError(
            "LS PLC 대상이 바뀌었습니다. 현재 대상을 확인한 뒤 새 작업을 시작해 주세요.",
            409,
          );
        }
      }
      const created = await startFreshConversation("새 PLC 엔지니어링 작업");
      if (created === null) {
        return;
      }
      setConnectionMessage("새 엔지니어링 작업이 준비되었습니다.");
    } catch (error) {
      setConnectionMessage(
        error instanceof HostApiError
          ? error.message
          : "새 엔지니어링 작업을 시작하지 못했습니다.",
      );
    } finally {
      setMissionStartBusy(false);
    }
  };

  const localAnalysisAvailable = hostReady && mcpReady;
  const newProjectPlanningAvailable = hostReady && planningAgentReady;
  const nativeProjectFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "native_project_create",
  );
  const onlineObserveFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "online_observe",
  );
  const fullProgramReplacementFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "full_program_replacement",
  );
  const programFocusFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "program_focus",
  );
  const nativeProjectAvailable =
    hostReady &&
    mcpReady &&
    (nativeProjectFeature
      ? nativeProjectFeature.maturity === "supported" &&
        nativeProjectFeature.currentReady
      : status.capabilities.includes("native_project_create"));
  const onlineObserveAvailable =
    hostReady &&
    mcpReady &&
    (onlineObserveFeature
      ? onlineObserveFeature.currentReady &&
        onlineObserveFeature.verification !== "not_verified"
      : status.capabilities.includes("online_observe"));
  const currentMessages = conversation?.messages ?? [];
  const canBrowseEngineeringPrograms =
    status.engineeringSystem.readReady &&
    status.engineeringSystem.application === "XG5000";
  const programFocusAvailable =
    hostReady &&
    mcpReady &&
    programFocusFeature?.currentReady === true &&
    programFocusFeature.verification === "real_xg5000";
  const programFocusUnavailableReason =
    programFocusFeature?.detail ||
    "XG5000 위치 열기는 실제 환경 검증 전 비활성입니다.";
  const selectedEngineeringTarget = currentEngineeringTarget(status);
  const workspaceRevision =
    status.workspace?.revision ?? status.workspace?.artifactHash ?? null;
  const workspaceTargetActive =
    Boolean(status.workspace) &&
    selectedEngineeringTarget?.workspaceId === status.workspace?.workspaceId &&
    selectedEngineeringTarget?.revisionId === workspaceRevision;
  const workspaceStaticTargetActive =
    workspaceTargetActive && !status.engineeringSystem.readReady;
  const directXgTargetActive =
    status.engineeringSystem.readReady && !workspaceTargetActive;
  const reviewedCloseTarget =
    status.engineeringSystem.sessionId &&
    status.engineeringSystem.processId &&
    status.engineeringSystem.windowHandle &&
    status.engineeringSystem.processCreateTime &&
    status.engineeringSystem.projectPath &&
    status.engineeringSystem.projectHash
      ? {
          sessionId: status.engineeringSystem.sessionId,
          pid: status.engineeringSystem.processId,
          hwnd: status.engineeringSystem.windowHandle,
          processCreateTime: status.engineeringSystem.processCreateTime,
          projectPath: status.engineeringSystem.projectPath,
          projectHash: status.engineeringSystem.projectHash,
        }
      : null;
  const recoveryAttachRequired =
    Boolean(status.workspace) && !status.engineeringSystem.readReady;

  const openReviewedCloseDialog = () => {
    if (!reviewedCloseTarget) return;
    setReviewedCloseAttempted(false);
    setReviewedCloseBusy(false);
    setReviewedCloseNotice("");
    setReviewedCloseReceipt(null);
    setReviewedCloseDialogTarget({
      ...reviewedCloseTarget,
      confirmNoSave: true,
    });
    setReviewedCloseDialogOpen(true);
  };

  const reviewedNoSaveClose = async () => {
    if (
      !reviewedCloseDialogTarget ||
      reviewedCloseBusy ||
      reviewedCloseAttempted
    ) {
      return;
    }
    setReviewedCloseAttempted(true);
    setReviewedCloseBusy(true);
    setReviewedCloseNotice("");
    try {
      const receipt = await hostApi.reviewedNoSaveClose({
        ...reviewedCloseDialogTarget,
      });
      setReviewedCloseReceipt(receipt);
      applyHostStatus(await hostApi.getStatus());
      setConnectionMessage(
        "검토한 XG5000 창을 저장 없이 닫았습니다. 새 프로젝트 열기는 별도로 선택해야 합니다.",
      );
    } catch (error) {
      setReviewedCloseNotice(
        error instanceof HostApiError
          ? error.message
          : "저장 없이 닫기를 완료하지 못했습니다. 현재 XG5000 창은 그대로 유지됩니다.",
      );
    } finally {
      setReviewedCloseBusy(false);
    }
  };
  const controllerTargetActive =
    !status.engineeringSystem.readReady &&
    status.controller.state === "ready" &&
    selectedEngineeringTarget !== null;
  const hasEngineeringTarget = selectedEngineeringTarget !== null;
  const activeProjectHash = workspaceTargetActive
    ? status.workspace?.artifactHash ?? null
    : directXgTargetActive &&
        selectedEngineeringTarget &&
        /^[0-9a-f]{64}$/.test(selectedEngineeringTarget.revisionId)
      ? selectedEngineeringTarget.revisionId
      : null;
  const entryPanelOpen =
    newProjectBriefOpen ||
    nativeProjectOpen ||
    localProjectOpen ||
    instancePickerOpen ||
    onlineControllerOpen;
  const hasPrimaryChange = Boolean(
    activeChange &&
      activeChange.transactionId !== deferredChangeTransactionId &&
      (PRIMARY_CHANGE_STATES.has(activeChange.state) ||
        (activeChange.result.reconciliation !== null &&
          typeof activeChange.result.reconciliation === "object")),
  );
  const showAgentWorkspace =
    !entryPanelOpen &&
    !workChooserOpen &&
    (planningConversationActive || hasEngineeringTarget || canCancelActiveRun);
  const showStartSection =
    !hasPrimaryChange &&
    !showAgentWorkspace &&
    (workChooserOpen ||
      entryPanelOpen ||
      (!hasEngineeringTarget && !planningConversationActive));
  const showEntryChoices =
    !entryPanelOpen &&
    !planningConversationActive &&
    (workChooserOpen || !hasEngineeringTarget);
  const latestPlanningAssistant = [...currentMessages].reverse().find(
    (item) =>
      item.role === "assistant" &&
      item.workflow === "new_project_planning" &&
      Boolean(item.planArtifact) &&
      typeof item.planDigest === "string" &&
      /^[0-9a-f]{64}$/.test(item.planDigest),
  );
  const latestPlanningResult =
    latestPlanningAssistant?.status === "complete" &&
    latestPlanningAssistant.planStatus
      ? latestPlanningAssistant
      : undefined;
  const latestPlanningArtifact = latestPlanningResult?.planArtifact;
  const latestPlanningDigest = latestPlanningResult?.planDigest;
  const latestPlanningItemIds = latestPlanningArtifact
    ? projectPlanReviewItemIds(latestPlanningArtifact)
    : undefined;
  const latestPlanningSectionIds =
    latestPlanningArtifact?.sections.map((section) => section.sectionId) ?? [];
  const latestPlanningReview =
    latestPlanningResult?.planReview &&
    latestPlanningResult.planReview.schemaVersion === "2" &&
    latestPlanningResult.planReview.authorityCurrent === true &&
    latestPlanningResult.planReview.planDigest === latestPlanningDigest &&
    latestPlanningArtifact &&
    latestPlanningItemIds &&
    latestPlanningArtifact.technicalConfiguration &&
    latestPlanningArtifact.design.collectionScope &&
    latestPlanningArtifact.design.ladderRungs?.length &&
    typeof latestPlanningResult.planReview.projectBriefDigest === "string" &&
    /^[0-9a-f]{64}$/.test(
      latestPlanningResult.planReview.projectBriefDigest,
    ) &&
    typeof latestPlanningResult.planReview.attachmentRevisionDigest ===
      "string" &&
    /^[0-9a-f]{64}$/.test(
      latestPlanningResult.planReview.attachmentRevisionDigest,
    ) &&
    latestPlanningResult.planReview.reviewedSectionIds.length ===
      latestPlanningSectionIds.length &&
    latestPlanningResult.planReview.reviewedSectionIds.every(
      (sectionId, index) => sectionId === latestPlanningSectionIds[index],
    ) &&
    latestPlanningResult.planReview.reviewedItemIds.length ===
      latestPlanningItemIds.length &&
    latestPlanningResult.planReview.reviewedItemIds.every(
      (itemId, index) => itemId === latestPlanningItemIds[index],
    )
      ? latestPlanningResult.planReview
      : undefined;
  const planningHasResponse = Boolean(
    newProjectPlanning && latestPlanningResult,
  );
  const planningReadyForTechnicalSetup =
    Boolean(newProjectPlanning) &&
    latestPlanningArtifact?.status === "ready_for_review" &&
    Boolean(latestPlanningArtifact.technicalConfiguration) &&
    Boolean(latestPlanningArtifact.design.collectionScope) &&
    Boolean(latestPlanningArtifact.design.ladderRungs?.length) &&
    Boolean(latestPlanningDigest) &&
    Boolean(latestPlanningReview);

  const openTechnicalSetup = async () => {
    if (
      nativeProjectRestoreLoading ||
      !conversation ||
      !latestPlanningResult ||
      !latestPlanningReview
    ) {
      return;
    }
    setNativeProjectRestoreLoading(true);
    try {
      const saved = await hostApi.getCompletedUnboundNativeProject(
        conversation.conversationId,
        latestPlanningResult.messageId,
        latestPlanningReview.planDigest,
      );
      setNativeProjectRestoreId(saved.nativeProjectId);
      setNativeProjectOpen(true);
    } catch (error) {
      if (error instanceof HostApiError && error.status === 404) {
        setNativeProjectRestoreId(null);
        setNativeProjectOpen(true);
      } else {
        setConnectionMessage(
          error instanceof HostApiError
            ? error.message
            : "저장된 프로젝트 생성 작업을 확인하지 못했습니다.",
        );
      }
    } finally {
      setNativeProjectRestoreLoading(false);
    }
  };

  const reviewLatestProjectPlan = async (
    planDigest: string,
    reviewedSectionIds: string[],
    reviewedItemIds: string[],
  ) => {
    if (
      !conversation ||
      !latestPlanningResult ||
      !latestPlanningArtifact?.technicalConfiguration ||
      !latestPlanningArtifact?.design.collectionScope ||
      !latestPlanningArtifact?.design.ladderRungs?.length ||
      latestPlanningResult.planDigest !== planDigest ||
      projectPlanReviewBusy
    ) {
      return;
    }
    setProjectPlanReviewBusy(true);
    setCancelNotice("");
    try {
      await hostApi.reviewProjectPlan(conversation.conversationId, {
        schemaVersion: "2",
        messageId: latestPlanningResult.messageId,
        planDigest,
        reviewedSectionIds,
        reviewedItemIds,
      });
      await refreshConversation(conversation.conversationId);
      setCancelNotice(
        "이 계획 버전에 대한 사람의 검토를 기록했습니다. 계획이 바뀌면 다시 검토해야 합니다.",
      );
    } catch (error) {
      setCancelNotice(
        error instanceof HostApiError
          ? error.message
          : "현재 계획 버전의 검토를 기록하지 못했습니다.",
      );
    } finally {
      setProjectPlanReviewBusy(false);
    }
  };

  const requestProjectPlanRevision = () => {
    setMessage(
      "현재 Engineering Plan을 다음 요구에 맞게 수정해 주세요: ",
    );
    window.setTimeout(() => composerRef.current?.focus(), 0);
  };
  const systemAttentionDetail =
    [
      status.host,
      status.agent,
      status.mcp,
      status.engineeringSystem,
      status.controller,
    ].find((service) => service.state === "quarantined")?.detail ??
    [
      status.host,
      status.agent,
      status.mcp,
      status.engineeringSystem,
      status.controller,
    ].find((service) => service.state === "degraded")?.detail;
  const systemNeedsAttention = Boolean(systemAttentionDetail);
  const primaryChange = hasPrimaryChange ? activeChange : null;
  const openInsightPanel = (
    panel: "programs" | "evidence" | "activity",
    trigger?: HTMLButtonElement,
  ) => {
    if (trigger) {
      insightReturnFocusRef.current = trigger;
    }
    setActivePanel(panel);
    if (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 860px)").matches
    ) {
      window.setTimeout(() => {
        insightPanelRef.current?.scrollIntoView?.({
          behavior: "smooth",
          block: "start",
        });
        insightPanelRef.current?.focus({ preventScroll: true });
      }, 0);
    }
  };
  const openMissionEvidence = (
    evidenceId: string,
    trigger: HTMLButtonElement,
  ) => {
    if (!evidence.some((item) => item.evidenceId === evidenceId)) {
      return;
    }
    setSelectedEvidenceId(evidenceId);
    openInsightPanel("evidence", trigger);
    window.setTimeout(() => {
      const evidenceItem = document.getElementById(`evidence-${evidenceId}`);
      evidenceItem?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      evidenceItem?.focus({ preventScroll: true });
    }, 0);
  };
  const closeInsightPanel = () => {
    setActivePanel(null);
    const returnFocus = insightReturnFocusRef.current;
    insightReturnFocusRef.current = null;
    window.setTimeout(() => returnFocus?.focus(), 0);
  };
  const engineeringSessionLabel = status.engineeringSystem.sessionVerified
    ? "강한 프로젝트 일치"
    : status.engineeringSystem.readReady
      ? "읽기 대상 확인"
      : status.controller.state === "ready"
        ? "PLC 읽기 전용 연결"
        : workspaceStaticTargetActive
          ? "오프라인 구조 분석"
        : status.workspace
          ? "프로젝트 확인 제한"
          : planningConversationActive
            ? "신규 설계 · 계획 단계"
            : "대상 선택 전";
  const agentStatusDetail = status.agent.modelDisplayName
    ? `${status.agent.modelDisplayName} · ${status.agent.detail ?? "기능 검사 대기"}`
    : status.agent.detail ?? "모델 연결 대기";
  const headerTarget = newProjectPlanning?.brief.projectName
    ? newProjectPlanning.brief.projectName
    : workspaceTargetActive && status.workspace
      ? status.workspace.displayName
      : status.engineeringSystem.readReady && status.engineeringSystem.processId
        ? `XG5000 PID ${status.engineeringSystem.processId}`
      : status.controller.endpoint
        ? `${status.controller.endpoint.host}:${status.controller.endpoint.port}`
        : status.workspace
          ? `${status.workspace.displayName} · 저장됨, 재검증 필요`
          : "엔지니어링 대상 선택 전";
  const engineeringDebugPanel = debugMode ? (
    <section
      className="engineering-debug-panel"
      aria-label="에이전트 디버깅 상태"
    >
      <header>
        <div>
          <p className="eyebrow">AGENT DEBUG</p>
          <h3>실행·미션 진단</h3>
        </div>
        <span>읽기 전용</span>
      </header>
      <dl>
        <div>
          <dt>Conversation</dt>
          <dd><code>{conversation?.conversationId ?? "-"}</code></dd>
        </div>
        <div>
          <dt>Run</dt>
          <dd>
            <code>{activeRunId ?? "-"}</code>
            {activeRunState ? ` · ${activeRunState}` : ""}
          </dd>
        </div>
        <div>
          <dt>Target</dt>
          <dd>
            <code>
              {selectedEngineeringTarget
                ? `${selectedEngineeringTarget.workspaceId} / ${selectedEngineeringTarget.revisionId}`
                : "-"}
            </code>
          </dd>
        </div>
        <div>
          <dt>Identity</dt>
          <dd>
            <code>
              {status.engineeringSystem.identityAssurance ?? "unverified"}
            </code>
            {` · read=${status.engineeringSystem.readReady === true ? "yes" : "no"}`}
            {` · strong=${status.engineeringSystem.sessionVerified ? "yes" : "no"}`}
          </dd>
        </div>
        <div>
          <dt>Host capabilities</dt>
          <dd>
            <code>{status.capabilities.join(", ") || "-"}</code>
          </dd>
        </div>
        <div>
          <dt>Agent runtime</dt>
          <dd><code>{status.agent.state}</code>{` · ${agentStatusDetail}`}</dd>
        </div>
        <div>
          <dt>Offline change</dt>
          <dd>
            {offlineMutateFeature
              ? `${offlineMutateFeature.currentReady ? "ready" : "blocked"} · ${offlineMutateFeature.detail}`
              : "not reported"}
          </dd>
        </div>
        <div>
          <dt>MNE structure changes</dt>
          <dd>
            {fullProgramReplacementFeature
              ? `${fullProgramReplacementFeature.capabilityStatus ?? "missing"} · live runtime check ${fullProgramReplacementFeature.currentReady ? "pending execution" : "blocked"} · ${fullProgramReplacementFeature.detail}`
              : "not reported"}
          </dd>
        </div>
        <div>
          <dt>Mission</dt>
          <dd>
            {conversation?.engineeringMission ? (
              <>
                revision {conversation.engineeringMission.revision} ·{" "}
                {conversation.engineeringMission.snapshot.status} ·{" "}
                {conversation.engineeringMission.snapshot.stopReason}
                <br />
                <code>{conversation.engineeringMission.digest}</code>
              </>
            ) : "-"}
          </dd>
        </div>
        <div>
          <dt>Active step</dt>
          <dd>
            <code>
              {conversation?.engineeringMission?.snapshot.activeStepId ?? "-"}
            </code>
          </dd>
        </div>
        <div>
          <dt>Evidence / events</dt>
          <dd>{evidence.length} / {activities.length}</dd>
        </div>
      </dl>
      {showStartSection && activities.length > 0 && (
        <div className="engineering-debug-events">
          <h4>최근 Agent 행동</h4>
          <ol>
            {activities.slice(-8).map((item) => (
              <li key={item.id}>
                <strong>{item.toolState ? item.detail : item.title}</strong>
                <span>
                  {item.toolState
                    ? `${TOOL_STATE_COPY[item.toolState]}${
                        activityDuration(item)
                          ? ` · ${activityDuration(item)}`
                          : ""
                      }`
                    : item.detail}
                </span>
                <code>
                  #{item.sequence} · {item.eventType}
                  {item.requestId ? ` · request=${item.requestId}` : ""}
                  {item.toolCallId ? ` · ${item.toolCallId}` : ""}
                </code>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p>
        원문 도구 인수·자격 증명·PLC 쓰기 권한은 디버깅 화면에도 표시하지
        않습니다.
      </p>
    </section>
  ) : null;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        본문으로 바로가기
      </a>

      <header className="app-header">
        <div className="brand-block" aria-label="ControlStudio">
          <span className="brand-mark" aria-hidden="true">
            <span />
          </span>
          <div className="brand-copy">
            <strong>ControlStudio</strong>
            <span>
              PLC 엔지니어링 에이전트 · {(status.releaseChannel ?? "development").toUpperCase()}
              {(status.productRelease ?? "source") !== "source"
                ? ` ${status.productRelease}`
                : ""}
            </span>
          </div>
        </div>

        <div className="header-context">
          <strong>{headerTarget}</strong>
          <span className="session-summary">
            <span
              className={`session-chip ${
                status.engineeringSystem.sessionVerified ||
                status.engineeringSystem.readReady
                  ? "session-verified"
                  : "session-limited"
              }`}
            >
              {engineeringSessionLabel}
            </span>
            <span>
              {status.controller.state === "ready" && !status.workspace
                ? "온라인 관찰 · 쓰기 차단"
                : planningConversationActive
                  ? "계획 전용 · 파일과 PLC 변경 없음"
                : (offlineMutateFeature?.currentReady ??
                      status.capabilities.includes("offline_mutate")) &&
                    status.engineeringSystem.sessionVerified
                  ? "분석 · 승인 기반 오프라인 변경"
                  : "분석 모드 · 변경 차단"}
            </span>
          </span>
        </div>

        <div className="header-actions">
          {onlineObserveAvailable && (
            <button
              className="header-text-button"
              type="button"
              disabled={canCancelActiveRun}
              onClick={() => {
                setWorkChooserOpen(false);
                setNativeProjectOpen(false);
                setLocalProjectOpen(false);
                setInstancePickerOpen(false);
                setOnlineControllerOpen(true);
              }}
            >
              {status.controller.state === "ready"
                ? "PLC 관찰 관리"
                : "온라인 관찰"}
            </button>
          )}
          {hasEngineeringTarget && !planningConversationActive && (
            <button
              className="header-text-button"
              type="button"
              disabled={canCancelActiveRun}
              onClick={openWorkChooser}
            >
              새 작업
            </button>
          )}
          <button
            className="header-text-button header-history-button"
            type="button"
            disabled={canCancelActiveRun}
            onClick={() => void openConversationHistory()}
            aria-label="작업 기록"
          >
            <Icon name="history" size={16} />
            <span className="header-history-button__label">작업 기록</span>
          </button>
          <button
            className="header-text-button"
            type="button"
            onClick={() => setKnowledgeOpen(true)}
            aria-label="엔지니어링 지식 열기"
          >
            <Icon name="database" size={16} />
            지식
          </button>
          <button
            className="header-text-button"
            type="button"
            aria-pressed={debugMode}
            onClick={() => {
              setDebugMode((current) => !current);
              if (!debugMode) {
                setActivePanel("activity");
              }
            }}
          >
            <Icon name="activity" size={16} />
            {debugMode ? "디버깅 종료" : "디버깅"}
          </button>
          <button
            className="connection-button"
            type="button"
            onClick={() => void refreshStatus()}
            aria-label="Host 상태 다시 확인"
          >
            <span
              className={`connection-light ${
                hostReady ? "connection-online" : ""
              }`}
            />
            <span>{hostReady ? "Host 준비됨" : "Host 연결 안 됨"}</span>
            <Icon name="refresh" size={15} />
          </button>
          <button
            className="icon-button header-icon-button"
            type="button"
            onClick={() => setSettingsOpen(true)}
            aria-label="모델 연결 설정 열기"
          >
            <Icon name="settings" size={20} />
          </button>
          <button
            className="shutdown-button"
            type="button"
            onClick={() => {
              setShutdownNotice("");
              setShutdownDialogOpen(true);
            }}
            aria-label="ControlStudio 종료"
          >
            <Icon name="power" size={16} />
            <span>종료</span>
          </button>
        </div>
      </header>

      <details className="system-details">
        <summary>
          <span>
            <Icon name="shield" size={15} />
            시스템 상세
          </span>
          <span>
            {SERVICE_COPY[status.host.state].label} ·{" "}
            {status.agent.modelDisplayName ?? "모델 미설정"} ·{" "}
            {engineeringSessionLabel}
          </span>
        </summary>
        <section
          className="system-status"
          aria-label="Host, 에이전트, MCP와 XG5000 상세 상태"
        >
          <StatusItem
            label="HOST"
            state={status.host.state}
            detail={status.host.detail ?? "상태 정보 없음"}
            icon="database"
          />
          <StatusItem
            label="AGENT"
            state={status.agent.state}
            detail={agentStatusDetail}
            icon="spark"
          />
          <StatusItem
            label="MCP"
            state={status.mcp.state}
            detail={
              status.mcp.agentToolCount !== undefined
                ? `AI 최대 허용 ${status.mcp.agentToolCount}개 · MCP 등록 ${
                    status.mcp.toolCount ?? 0
                  }개`
                : status.mcp.detail ?? "안전 코어 대기"
            }
            icon="shield"
          />
          <StatusItem
            label={status.engineeringSystem.application}
            state={status.engineeringSystem.state}
            detail={
              status.engineeringSystem.processId
                ? `PID ${status.engineeringSystem.processId}`
                : status.engineeringSystem.detail ?? "대상 선택 전"
            }
            icon="cpu"
          />
          <StatusItem
            label="LS PLC"
            state={status.controller.state}
            detail={
              status.controller.endpoint
                ? `${status.controller.endpoint.host}:${status.controller.endpoint.port} · 읽기 전용`
                : status.controller.detail ?? "온라인 대상 선택 전"
            }
            icon="plc"
          />
        </section>
        {status.featureReadiness &&
          status.featureReadiness.length > 0 && (
            <section
              className="feature-readiness"
              aria-label="제품 기능 검증 수준"
            >
              <div className="feature-readiness-heading">
                <strong>제품 기능 검증 수준</strong>
                <span>구현 여부와 실제 환경 검증을 분리해 표시합니다.</span>
              </div>
              <ul>
                {status.featureReadiness.map((feature) => (
                  <li key={feature.featureId}>
                    <div>
                      <strong>{feature.label}</strong>
                      <span>{feature.detail}</span>
                    </div>
                    <span
                      className={`feature-badge feature-${feature.maturity}`}
                    >
                      {feature.maturity === "supported"
                        ? feature.currentReady
                          ? "사용 가능"
                          : "환경 준비 필요"
                        : feature.maturity === "preview"
                          ? "실험 기능"
                          : "미구현"}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
      </details>

      <main id="main-content" className="main-content">
        {sessionRequired && (
          <section className="session-required" aria-labelledby="session-title">
            <Icon name="shield" size={20} />
            <div>
              <h2 id="session-title">Launcher 세션 인증이 필요합니다</h2>
              <p>
                이 주소를 직접 새로 열어서는 제어 API를 사용할 수 없습니다.
                Windows의 ControlStudio 아이콘으로 다시 실행해 주세요.
              </p>
            </div>
          </section>
        )}

        {systemNeedsAttention && (
          <section className="system-alert" role="status">
            <Icon name="shield" size={18} />
            <div>
              <strong>현재 연결에 확인이 필요한 항목이 있습니다</strong>
              <span>{systemAttentionDetail}</span>
            </div>
          </section>
        )}

        {hasEngineeringTarget &&
          !planningConversationActive &&
          !workChooserOpen &&
          !entryPanelOpen &&
          !primaryChange && (
            <button
              className="mobile-new-work-button"
              type="button"
              disabled={canCancelActiveRun}
              onClick={openWorkChooser}
            >
              <Icon name="new" size={16} />
              새 작업 시작
            </button>
          )}

        {primaryChange && (
          <>
            <ChangeApprovalPanel
              transaction={primaryChange}
              busy={changeBusy || canCancelActiveRun}
              notice={changeNotice}
              mneExecutionReady={
                fullProgramReplacementFeature?.currentReady === true
              }
              mneExecutionDetail={fullProgramReplacementFeature?.detail}
              onApprove={() => void approveChange()}
              onExecuteApproved={() => void executeApprovedChange()}
              onFinalizeSimulation={(decision) => void finalizeObservedSimulation(decision)}
              onReject={() => void rejectChange()}
              onRequestRevision={() => void requestChangeRevision()}
              onOpenTarget={() => void openActiveChangeTarget()}
              onOpenEvidence={() => openInsightPanel("evidence")}
              onRefresh={() => void refreshChange()}
              onDismiss={() => void acknowledgeChangeReceipt()}
            />
            {(primaryChange.state === "quarantined" ||
              (primaryChange.result.reconciliation !== null &&
                typeof primaryChange.result.reconciliation === "object")) && (
              <ChangeRecoveryPanel
                transaction={primaryChange}
                busy={changeBusy || canCancelActiveRun}
                currentWorkspaceHash={status.workspace?.artifactHash}
                onReconcile={reconcileChange}
              />
            )}
          </>
        )}

        {showStartSection && (
          <section className="start-section" aria-label="엔지니어링 시작">
            {showEntryChoices && (
              <>
                <div className="section-heading">
                  <div>
                    <h1 id="start-title">어디서 시작할까요?</h1>
                    <p>
                      새 설비의 요구사항부터 시작하거나, 기존 프로젝트를 안전한
                      작업 복사본으로 열어 분석하세요.
                    </p>
                  </div>
                  <div className="start-heading-actions">
                    {hasEngineeringTarget &&
                      workChooserOpen &&
                      returnWorkspaceRef.current && (
                        <button
                          className="button secondary-button"
                          type="button"
                          onClick={returnToCurrentWork}
                        >
                          현재 작업으로 돌아가기
                        </button>
                      )}
                    <div className="safety-caption">
                      <Icon name="shield" size={16} />
                      <span>승인·권한 경계 적용</span>
                    </div>
                  </div>
                </div>

                {status.workspace &&
                  (!hasEngineeringTarget ||
                    !status.engineeringSystem.readReady) && (
                  <section
                    className="workspace-resume-card"
                    aria-labelledby="workspace-resume-title"
                  >
                    <span className="workspace-resume-icon" aria-hidden="true">
                      <Icon name="history" size={20} />
                    </span>
                    <div>
                      <p className="eyebrow">
                        저장된 작업공간 · 재검증 필요
                      </p>
                      <h2 id="workspace-resume-title">
                        {status.workspace.displayName}
                      </h2>
                      <p>
                        원본이 아닌 저장된 작업 복사본을 해시로 다시 확인한 뒤
                        XG5000 읽기 전용 분석을 이어갑니다.
                      </p>
                      {status.workspace.artifactHash && (
                        <code>
                          SHA-256 {status.workspace.artifactHash.slice(0, 12)}…
                        </code>
                      )}
                      {workspaceResumeNotice && (
                        <span className="workspace-resume-notice" role="status">
                          {workspaceResumeNotice}
                        </span>
                      )}
                      {workspaceCompare?.workspaceId === status.workspace.workspaceId && (
                        <div className="workspace-compare-result" role="status">
                          <strong>
                            {workspaceCompareSummary(workspaceCompare)}
                          </strong>
                          <span>
                            비교 영수증 {workspaceCompare.compareDigest.slice(0, 12)}…
                          </span>
                          {workspaceCompare.limitations.map((item) => (
                            <span key={item.code}>{item.detail}</span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="workspace-resume-actions">
                      <button
                        className="button secondary-button"
                        type="button"
                        disabled={workspaceCompareBusy}
                        onClick={() => void compareWorkspace()}
                      >
                        {workspaceCompareBusy ? "비교 중…" : "원본과 비교"}
                      </button>
                      <button
                        className="button primary-button"
                        type="button"
                        disabled={workspaceResumeBusy || !localAnalysisAvailable}
                        onClick={() => void resumeWorkspace()}
                      >
                        {workspaceResumeBusy
                          ? "대상 재검증 중…"
                          : "이 작업 계속하기"}
                      </button>
                      {!status.engineeringSystem.readReady && (
                        <button
                          className="button secondary-button"
                          type="button"
                          disabled={workspaceResumeBusy || !localAnalysisAvailable}
                          onClick={() => {
                            setWorkChooserOpen(false);
                            void openInstancePicker(false);
                          }}
                        >
                          기존 XG5000 창에 이 작업 연결
                        </button>
                      )}
                    </div>
                  </section>
                )}

                <div className="entry-grid">
                  <EntryCard
                    icon="new"
                    title="새 프로젝트"
                    description="설비 목적과 안전 조건을 정리해 검토 가능한 계획서를 만듭니다. XG5000 프로젝트 생성은 별도 검증 기능입니다."
                    accent="lime"
                    available={newProjectPlanningAvailable}
                    actionLabel="요구사항부터 시작"
                    unavailableReason={
                      hostReady
                        ? status.agent.detail ?? "모델 기능 검사 필요"
                        : "Host 연결 필요"
                    }
                    onClick={() => {
                      setWorkChooserOpen(false);
                      setLocalProjectOpen(false);
                      setInstancePickerOpen(false);
                      setOnlineControllerOpen(false);
                      setNativeProjectOpen(false);
                      setNewProjectBriefOpen(true);
                    }}
                  />
                  <EntryCard
                    icon="folder"
                    title="기존 로컬 프로젝트"
                    description="원본을 보존한 작업 복사본을 XG5000에서 열고 구조·디바이스·영향 범위를 분석합니다."
                    accent="amber"
                    available={localAnalysisAvailable}
                    actionLabel={
                      status.workspace ? "다른 프로젝트 열기" : "프로젝트 열기"
                    }
                    unavailableReason={
                      hostReady
                        ? "MCP 읽기·분석 안전 코어 준비 필요"
                        : "Host 연결 필요"
                    }
                    onClick={() => {
                      setWorkChooserOpen(false);
                      setNewProjectBriefOpen(false);
                      setNativeProjectOpen(false);
                      setInstancePickerOpen(false);
                      setOnlineControllerOpen(false);
                      setLocalProjectNotice("");
                      setLocalProjectOpen(true);
                    }}
                  />
                </div>

                {recoveryAttachRequired && (
                  <div className="recovery-entry" role="status">
                    <Icon name="warning" size={18} />
                    <div>
                      <strong>이전에 열린 XG5000 창을 먼저 확인</strong>
                      <p>
                        현재 작업 복사본은 offline 분석 대상으로만 전환됐습니다. Host는
                        남아 있을 수 있는 XG5000 창을 이 작업 복사본으로 추측 결속하거나
                        자동으로 닫지 않습니다.
                      </p>
                    </div>
                    <button
                      className="button secondary-button"
                      type="button"
                      onClick={() => {
                        setWorkChooserOpen(false);
                        void openInstancePicker(true);
                      }}
                    >
                      PID/HWND로 기존 창 확인
                    </button>
                  </div>
                )}

                {onlineObserveAvailable && (
                  <details className="alternative-entry">
                    <summary>다른 연결 방법</summary>
                  <div className="auxiliary-entry">
                    <span className="auxiliary-entry-icon" aria-hidden="true">
                      <Icon name="activity" size={18} />
                    </span>
                    <div>
                      <strong>
                        온라인 LS PLC 읽기 전용 관찰
                        {onlineObserveFeature?.maturity === "preview" && (
                          <span className="preview-inline-badge">
                            실험 기능 · 실제 PLC 미검증
                          </span>
                        )}
                      </strong>
                      <p>
                        RUN/STOP과 지정 디바이스 값을 확인합니다. 프로젝트 업로드와
                        쓰기·런중 수정은 하지 않습니다.
                      </p>
                    </div>
                    <button
                      className="button secondary-button"
                      type="button"
                      disabled={canCancelActiveRun}
                      onClick={() => {
                        setWorkChooserOpen(false);
                        setNativeProjectOpen(false);
                        setLocalProjectOpen(false);
                        setInstancePickerOpen(false);
                        setOnlineControllerOpen(true);
                      }}
                    >
                      읽기 전용 연결
                    </button>
                  </div>
                  </details>
                )}

                {nativeProjectRestoreId && (
                  <div className="recovery-entry" role="status">
                    <Icon name="warning" size={18} />
                    <div>
                      <strong>확인이 필요한 최근 프로젝트 생성 작업</strong>
                      <p>
                        생성 요청을 다시 보내지 않고 저장된 증거와 현재 XG5000
                        상태로 결과를 확인합니다.
                      </p>
                    </div>
                    <button
                      className="button secondary-button"
                      type="button"
                      onClick={() => {
                        setWorkChooserOpen(false);
                        setNewProjectBriefOpen(false);
                        setLocalProjectOpen(false);
                        setInstancePickerOpen(false);
                        setOnlineControllerOpen(false);
                        setNativeProjectOpen(true);
                      }}
                    >
                      복구 작업 확인
                    </button>
                  </div>
                )}
              </>
            )}

          {newProjectBriefOpen && (
            <NewProjectBriefPanel
              initialBrief={newProjectPlanning?.brief}
              busy={sending}
              nativeProjectAvailable={nativeProjectAvailable}
              onCancel={closeNewProjectBrief}
              onManageKnowledge={() => setKnowledgeOpen(true)}
              onSubmit={(brief) => void submitNewProjectBrief(brief)}
            />
          )}

          {nativeProjectOpen && (
            <>
              {newProjectPlanning && !nativeProjectRestoreId && (
                <section className="technical-setup-context">
                  <ProjectFlowStepper
                    activeStep={3}
                    nativeProjectAvailable
                  />
                  <div>
                    <strong>계획 검토 이후의 기술 구성 단계입니다</strong>
                    <span>
                      검토한 프로젝트 이름·PLC 시리즈·CPU·프로그램 설정을 그대로
                      인계하고, 사람이 전 항목을 확인한 ladderRungs의 순서·분기·극성을
                      그대로 LD 프로그램으로 구성합니다. mnemonic readback, LD 변환, 프로그램
                      검사·저장과 별도 XG5000 프로세스 재열기까지 수행하며 PLC
                      연결·다운로드·쓰기는 하지 않습니다.
                    </span>
                  </div>
                </section>
              )}
              <NativeProjectPanel
                status={status}
                onEngineeringReady={enterAcceptedNativeEngineering}
                initialNativeProjectId={nativeProjectRestoreId ?? undefined}
                initialProjectName={
                  nativeProjectRestoreId
                    ? undefined
                    : latestPlanningArtifact?.projectName
                }
                initialDescription={
                  nativeProjectRestoreId
                    ? undefined
                    : latestPlanningArtifact?.technicalConfiguration?.description
                }
                reviewedProjectName={
                  nativeProjectRestoreId
                    ? undefined
                    : latestPlanningArtifact?.projectName
                }
                reviewedTechnicalConfiguration={
                  nativeProjectRestoreId
                    ? undefined
                    : latestPlanningArtifact?.technicalConfiguration ?? undefined
                }
                reviewedPlan={
                  !nativeProjectRestoreId &&
                  conversation &&
                  latestPlanningResult &&
                  latestPlanningArtifact?.technicalConfiguration &&
                  latestPlanningArtifact?.design.collectionScope &&
                  latestPlanningArtifact?.design.ladderRungs?.length &&
                  latestPlanningReview
                    ? {
                        schemaVersion: "2",
                        conversationId: conversation.conversationId,
                        messageId: latestPlanningResult.messageId,
                        planDigest: latestPlanningReview.planDigest,
                        projectBriefDigest:
                          latestPlanningReview.projectBriefDigest!,
                        attachmentRevisionDigest:
                          latestPlanningReview.attachmentRevisionDigest!,
                        reviewedSectionIds:
                          latestPlanningReview.reviewedSectionIds,
                        reviewedItemIds:
                          latestPlanningReview.reviewedItemIds,
                      }
                    : undefined
                }
                onClose={() => {
                  setNativeProjectOpen(false);
                  if (
                    status.activeOperation?.kind !== "native_project_create"
                  ) {
                    setNativeProjectRestoreId(null);
                  }
                }}
                onStatusChanged={applyHostStatus}
              />
            </>
          )}

          {localProjectOpen && (
            <section
              className="local-project-picker"
              aria-labelledby="local-project-title"
            >
              <div className="instance-picker-heading">
                <div>
                  <p className="eyebrow">SOURCE-PRESERVING WORKSPACE</p>
                  <h2 id="local-project-title">기존 로컬 프로젝트 열기</h2>
                </div>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="로컬 프로젝트 열기 닫기"
                  onClick={() => setLocalProjectOpen(false)}
                >
                  <Icon name="close" size={18} />
                </button>
              </div>
              <p className="instance-explanation">
                원본 파일은 변경하지 않습니다. 해시로 고정한 별도 작업 복사본을
                만든 뒤 그 복사본만 XG5000에서 엽니다.
              </p>
              {recoveryAttachRequired && (
                <div className="inline-notice warning-notice reviewed-close-recovery" role="status">
                  <Icon name="warning" size={17} />
                  <span>
                    현재 작업 복사본은 정적 분석 대상으로 열려 있습니다. 이전 XG5000
                    창은 추측하여 닫지 않습니다. 아래의 “이미 열린 XG5000 창 선택”에서
                    PID와 HWND를 직접 선택해 해당 창의 프로젝트 정체성을 먼저 검증하세요.
                  </span>
                </div>
              )}
              {reviewedCloseTarget && (
                <div className="reviewed-close-entry" role="status">
                  <div>
                    <strong>현재 XG5000 창을 새 프로젝트 전에 정리</strong>
                    <p>
                      확인된 PID {reviewedCloseTarget.pid} · HWND {reviewedCloseTarget.hwnd}
                      창만 저장 없이 닫을 수 있습니다. 새 프로젝트 열기는 자동으로
                      실행되지 않습니다.
                    </p>
                  </div>
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={localProjectBusy || localProjectBrowseBusy}
                    onClick={openReviewedCloseDialog}
                  >
                    저장 없이 닫기 검토
                  </button>
                </div>
              )}
              <form
                className="local-project-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void openLocalProject(true);
                }}
              >
                <label htmlFor="local-project-path">XG5000 프로젝트 경로</label>
                <div className="local-project-row">
                  <input
                    id="local-project-path"
                    type="text"
                    value={localProjectPath}
                    onChange={(event) => setLocalProjectPath(event.target.value)}
                    placeholder="C:\projects\machine.xgwx"
                    autoComplete="off"
                    spellCheck={false}
                    disabled={localProjectBusy || localProjectBrowseBusy}
                  />
                  <button
                    className="button secondary-button local-project-browse"
                    type="button"
                    disabled={localProjectBusy || localProjectBrowseBusy}
                    onClick={() => void browseLocalProject()}
                  >
                    {localProjectBrowseBusy ? "선택 창 여는 중…" : "찾아보기"}
                  </button>
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={
                      !localProjectPath.trim() ||
                      localProjectBusy ||
                      localProjectBrowseBusy
                    }
                    onClick={() => void openLocalProject(false)}
                  >
                    구조만 분석
                  </button>
                  <button
                    className="button primary-button"
                    type="submit"
                    disabled={
                      !localProjectPath.trim() ||
                      localProjectBusy ||
                      localProjectBrowseBusy
                    }
                  >
                    {localProjectBusy ? "여는 중…" : "복사본으로 열기"}
                  </button>
                </div>
              </form>
              {localProjectNotice && (
                <p className="instance-notice" role="status">
                  {localProjectNotice}
                </p>
              )}
              <div className="instance-picker-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={localProjectBusy || localProjectBrowseBusy}
                  onClick={() => {
                    setLocalProjectOpen(false);
                    void openInstancePicker(recoveryAttachRequired);
                  }}
                >
                  이미 열린 XG5000 창 선택
                </button>
              </div>
            </section>
          )}

          {onlineControllerOpen && (
            <OnlineControllerPanel
              status={status}
              conversationId={conversation?.conversationId ?? null}
              onClose={() => setOnlineControllerOpen(false)}
              targetChangeDisabled={canCancelActiveRun}
              onInvestigateEvidence={(evidenceId, prompt) => {
                setSelectedEvidenceId(evidenceId);
                setActivePanel("evidence");
                setMessage(prompt);
                setOnlineControllerOpen(false);
                window.setTimeout(() => composerRef.current?.focus(), 0);
              }}
              onStatusChanged={async (nextStatus) => {
                const activeConversation =
                  await activateEngineeringTargetConversation(nextStatus);
                if (activeConversation) {
                  setWorkChooserOpen(false);
                  setConnectionMessage(
                    "검증된 LS PLC 대상에 엔지니어링 미션 작업공간을 연결했습니다.",
                  );
                }
              }}
            />
          )}

          {instancePickerOpen && (
            <section
              className="instance-picker"
              aria-labelledby="instance-picker-title"
            >
              <div className="instance-picker-heading">
                <div>
                  <p className="eyebrow">ANALYSIS-ONLY ENGINEERING SESSION</p>
                  <h2 id="instance-picker-title">XG5000 실행 창 선택</h2>
                </div>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="XG5000 선택 닫기"
                  onClick={() => setInstancePickerOpen(false)}
                >
                  <Icon name="close" size={18} />
                </button>
              </div>
              <p className="instance-explanation">
                이 연결은 XG5000 엔지니어링 IDE의 프로젝트를 읽고 분석하기 위한
                세션입니다. 실제 온라인 PLC 연결이나 PLC 프로젝트 업로드가
                아닙니다. 선택한 PID와 프로젝트 정체성이 완전히 증명되지 않으면
                상태가 <strong>제한됨</strong>으로 유지되며 MCP가 불확실한 작업을
                차단합니다.
              </p>

              {instancesLoading ? (
                <p className="instance-notice">실행 중인 XG5000 창 확인 중…</p>
              ) : instances.length > 0 ? (
                <ul className="instance-list">
                  {instances.map((instance) => (
                    <li key={`${instance.pid}:${instance.hwnd}`}>
                      <div className="instance-card-copy">
                        <strong>{instance.title || "XG5000"}</strong>
                        <span>
                          PID {instance.pid} · HWND {instance.hwnd}
                          {instance.version ? ` · ${instance.version}` : ""}
                        </span>
                        {restorePreflight?.target.hwnd === instance.hwnd && (
                          <div className="restore-preflight-result" role="status">
                            <strong>
                              {restorePreflight.ready
                                ? "복원 메뉴 표면 확인됨"
                                : "복원 메뉴 표면 미확인"}
                            </strong>
                            <span>
                              메뉴 관측은 읽기 전용입니다. 복원 시작은 XG5000의 공식
                              업로드 창을 열고, 완료 뒤 별도 경로 저장과 해시 검증을 수행합니다.
                            </span>
                            <span>
                              Open from PLC: {restorePreflight.surfaceProfile.openFromPlc
                                ?.actualPath.join(" → ") ?? "미해석"}
                            </span>
                            <span>
                              Save As: {restorePreflight.surfaceProfile.saveAs
                                ?.actualPath.join(" → ") ?? "미해석"}
                            </span>
                            {restorePreflight.blockers.length > 0 && (
                              <span>
                                차단 사유:{" "}
                                {restorePreflight.blockers
                                  .map((item) => CONTROLLER_RESTORE_BLOCKER_LABELS[item])
                                  .join(", ")}
                              </span>
                            )}
                            <span>
                              XG5000 {restorePreflight.target.executable.fileVersion} · profile{" "}
                              {restorePreflight.surfaceProfile.profileDigest}
                            </span>
                          </div>
                        )}
                      </div>
                      <div className="instance-card-actions">
                        <button
                          className="button secondary-button"
                          type="button"
                          disabled={
                            attachingHwnd !== null || restorePreflightBusyHwnd !== null
                          }
                          onClick={() => void preflightControllerProjectRestore(instance)}
                        >
                          {restorePreflightBusyHwnd === instance.hwnd
                            ? "표면 확인 중"
                            : "복원 표면 확인"}
                        </button>
                        {restorePreflight?.target.hwnd === instance.hwnd &&
                          restorePreflight.ready &&
                          !restoreHandoff && (
                            <button
                              className="button primary-button"
                              type="button"
                              disabled={restoreActionBusy || attachingHwnd !== null}
                              onClick={() => void beginControllerProjectRestore(instance)}
                            >
                              {restoreActionBusy ? "복원 창 여는 중…" : "PLC에서 열기 시작"}
                            </button>
                          )}
                        {restoreHandoff?.target.hwnd === instance.hwnd && (
                          <button
                            className="button primary-button"
                            type="button"
                            disabled={restoreActionBusy}
                            onClick={() => void completeControllerProjectRestore()}
                          >
                            {restoreActionBusy ? "저장·검증 중…" : "업로드 완료"}
                          </button>
                        )}
                        <button
                          className="button primary-button"
                          type="button"
                          disabled={
                            attachingHwnd !== null ||
                            restorePreflightBusyHwnd !== null ||
                            restoreActionBusy ||
                            restoreHandoff !== null
                          }
                          onClick={() => void attachInstance(instance)}
                        >
                          {attachingHwnd === instance.hwnd
                            ? "검증 중…"
                            : "분석 세션 연결"}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}

              {instanceNotice && (
                <p className="instance-notice" role="status">
                  {instanceNotice}
                </p>
              )}

              <div className="instance-picker-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={
                    instancesLoading ||
                    attachingHwnd !== null ||
                    restorePreflightBusyHwnd !== null
                  }
                  onClick={() => void openInstancePicker(recoveryAttachIntent)}
                >
                  다시 확인
                </button>
              </div>
            </section>
          )}
          {engineeringDebugPanel}
          </section>
        )}

        {showAgentWorkspace && (
          <section
            className={`workspace-grid ${activePanel ? "insight-open" : ""}`}
            aria-label="에이전트 작업 공간"
          >
          {!newProjectPlanning && hasEngineeringTarget && (
            <section
              className="target-proof-bar"
              aria-label="현재 엔지니어링 대상"
            >
              <div className="target-proof-name">
                <p className="eyebrow">ACTIVE ENGINEERING TARGET</p>
                <strong>
                  {workspaceTargetActive && status.workspace
                    ? status.workspace.displayName
                    : directXgTargetActive && status.engineeringSystem.processId
                      ? `XG5000 PID ${status.engineeringSystem.processId}`
                      : controllerTargetActive && status.controller.endpoint
                        ? `${status.controller.endpoint.host}:${status.controller.endpoint.port}`
                        : "대상 확인 중"}
                </strong>
              </div>
              <dl>
                <div>
                  <dt>벤더</dt>
                  <dd>
                    {workspaceTargetActive
                      ? status.workspace?.vendor?.toUpperCase() ?? "LS"
                      : "LS"}
                  </dd>
                </div>
                <div>
                  <dt>엔지니어링 도구</dt>
                  <dd>
                    {controllerTargetActive
                      ? "XGT FEnet 읽기 전용 관찰"
                      : status.engineeringSystem.application}
                    {!controllerTargetActive && status.engineeringSystem.processId
                      ? ` · PID ${status.engineeringSystem.processId}`
                      : ""}
                  </dd>
                </div>
                <div>
                  <dt>대상 보증</dt>
                  <dd>{engineeringSessionLabel}</dd>
                </div>
                <div>
                  <dt>현재 권한</dt>
                  <dd>
                    {(offlineMutateFeature?.currentReady ??
                      status.capabilities.includes("offline_mutate")) &&
                    status.engineeringSystem.sessionVerified
                      ? "분석 · 승인 기반 오프라인 변경"
                      : "읽기·분석 전용"}
                  </dd>
                </div>
                {activeProjectHash && (
                  <div>
                    <dt>프로젝트 해시</dt>
                    <dd>
                      <code>{activeProjectHash.slice(0, 12)}…</code>
                    </dd>
                  </div>
                )}
              </dl>
            </section>
          )}
          {newProjectPlanning && (
            <section
              className="project-plan-context"
              aria-labelledby="project-plan-context-title"
            >
              <ProjectFlowStepper
                activeStep={2}
                nativeProjectAvailable={nativeProjectAvailable}
              />
              <div className="project-plan-summary">
                <div>
                  <p className="eyebrow">PLANNING ONLY · NO PROJECT WRITE</p>
                  <h2 id="project-plan-context-title">
                    {newProjectPlanning.brief.projectName}
                  </h2>
                  <p>
                    {planningHasResponse
                      ? "계획서 초안을 검토하고, 빠진 조건이나 잘못된 가정을 대화로 수정하세요."
                      : "에이전트가 요구사항을 구조화하고 필요한 확인 질문을 준비하고 있습니다."}
                  </p>
                </div>
                <div className="project-plan-actions">
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={sending || canCancelActiveRun}
                    onClick={() => setNewProjectBriefOpen(true)}
                  >
                    요구사항 수정
                  </button>
                  <button
                    className="button primary-button"
                    type="button"
                    disabled={
                      !planningReadyForTechnicalSetup ||
                      !nativeProjectAvailable ||
                      nativeProjectRestoreLoading ||
                      sending ||
                      canCancelActiveRun
                    }
                    title={
                      latestPlanningArtifact &&
                      (!latestPlanningArtifact.technicalConfiguration ||
                        !latestPlanningArtifact.design.collectionScope ||
                        !latestPlanningArtifact.design.ladderRungs?.length)
                        ? "검토할 XG5000 기술 구성, 명시적 collectionScope와 ladderRungs가 있는 새 계획으로 다시 계획해야 합니다"
                        : !planningReadyForTechnicalSetup
                        ? "미결 결정을 해소하고 현재 Engineering Plan 버전에 대한 사람의 검토를 기록해야 합니다"
                        : !nativeProjectAvailable
                        ? "XG5000 새 프로젝트 생성 코어가 준비되어야 합니다"
                        : undefined
                    }
                    onClick={() => void openTechnicalSetup()}
                  >
                    기술 구성으로
                  </button>
                  <button
                    className="project-plan-cancel"
                    type="button"
                    disabled={sending || canCancelActiveRun}
                    onClick={() => void leaveNewProjectPlanning()}
                  >
                    계획 닫기
                  </button>
                </div>
              </div>
              <p className="project-stage-limit">
                {canCancelActiveRun
                  ? "계획 작업이 실행 중입니다. 요구사항 수정·기술 구성·계획 닫기는 완료되거나 취소된 뒤 가능합니다."
                  : latestPlanningArtifact &&
                      (!latestPlanningArtifact.technicalConfiguration ||
                        !latestPlanningArtifact.design.collectionScope ||
                        !latestPlanningArtifact.design.ladderRungs?.length)
                    ? "이전 형식 계획은 읽기 전용입니다. XG5000 기술 구성, 명시적 collectionScope와 ladderRungs를 포함해 다시 계획한 뒤 새 버전을 검토해 주세요."
                  : latestPlanningArtifact &&
                      latestPlanningArtifact.status === "ready_for_review" &&
                      !latestPlanningReview
                    ? "다음 단계 전에 아래 Engineering Plan의 범위·가정·안전 조건·검증 계획을 검토 완료해야 합니다."
                  : nativeProjectAvailable
                    ? "검토된 XG5000 기술 구성과 ladderRungs를 그대로 인계해 프로그램을 생성합니다. rung IR/source digest, exact mnemonic readback, LD 변환, 프로그램 검사와 저장까지 지원하며 PLC 연결·다운로드·쓰기는 별도 승인 흐름입니다."
                    : "현재 사용 가능한 범위는 계획서 검토까지입니다. XG5000 프로젝트 생성·로직·검사·재열기·시뮬레이션은 아직 일반 실행할 수 없습니다."}
              </p>
            </section>
          )}
          {newProjectPlanning &&
            latestPlanningArtifact &&
            latestPlanningDigest && (
              <NewProjectPlanReviewPanel
                artifact={latestPlanningArtifact}
                planDigest={latestPlanningDigest}
                review={latestPlanningResult.planReview}
                revision={latestPlanningResult.planRevision}
                busy={
                  projectPlanReviewBusy ||
                  sending ||
                  canCancelActiveRun
                }
                onReview={(digest, sectionIds, itemIds) =>
                  void reviewLatestProjectPlan(digest, sectionIds, itemIds)
                }
                onRequestRevision={requestProjectPlanRevision}
              />
            )}
          <article className="conversation-panel">
            <header className="panel-header conversation-header">
              <div>
                <span className="panel-icon">
                  <Icon name="message" size={18} />
                </span>
                <div>
                  <p className="panel-kicker">
                    {newProjectPlanning
                      ? "PROJECT PLANNING AGENT"
                      : "ENGINEERING AGENT"}
                  </p>
                  <h2>{conversation?.title ?? "대화 준비 중"}</h2>
                </div>
              </div>
              <div className="run-controls">
                <div className="insight-toggles" aria-label="작업 정보">
                  {canBrowseEngineeringPrograms && (
                    <button
                      type="button"
                      aria-pressed={activePanel === "programs"}
                      onClick={(event) => {
                        if (activePanel === "programs") {
                          closeInsightPanel();
                          return;
                        }
                        openInsightPanel("programs", event.currentTarget);
                        if (programs.length === 0 && !programsLoading) {
                          void loadEngineeringPrograms();
                        }
                      }}
                    >
                      <Icon name="folder" size={14} />
                      프로그램
                      {programs.length > 0 ? ` ${programs.length}` : ""}
                    </button>
                  )}
                  {evidence.length > 0 && (
                    <button
                      type="button"
                      aria-pressed={activePanel === "evidence"}
                      onClick={(event) => {
                        if (activePanel === "evidence") {
                          closeInsightPanel();
                          return;
                        }
                        openInsightPanel("evidence", event.currentTarget);
                      }}
                    >
                      <Icon name="evidence" size={14} />
                      근거 {evidence.length}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-pressed={activePanel === "activity"}
                    onClick={(event) => {
                      if (activePanel === "activity") {
                        closeInsightPanel();
                        return;
                      }
                      openInsightPanel("activity", event.currentTarget);
                    }}
                  >
                    <Icon name="activity" size={14} />
                    진행 내역
                  </button>
                </div>
                <div className="stream-state" aria-live="polite">
                  <span
                    className={`stream-dot stream-${eventConnection}`}
                    aria-hidden="true"
                  />
                  {activeRunState &&
                  !TERMINAL_RUN_STATES.has(activeRunState)
                    ? RUN_COPY[activeRunState] ?? activeRunState
                    : eventConnection === "connected"
                      ? "실시간 연결"
                      : eventConnection === "reconnecting"
                        ? "재연결 중"
                        : "연결 대기"}
                </div>
                {canCancelActiveRun && (
                  <button
                    className="cancel-run-button"
                    type="button"
                    disabled={cancelBusy}
                    onClick={() => void cancelActiveRun()}
                  >
                    {cancelBusy ? "취소 요청 중…" : "실행 중 작업 취소"}
                  </button>
                )}
                {cancelNotice && (
                  <span className="run-notice" role="status">
                    {cancelNotice}
                  </span>
                )}
              </div>
            </header>

            {conversation?.engineeringMission && !newProjectPlanning && (
              <EngineeringMissionPanel
                mission={conversation.engineeringMission}
                availableEvidenceIds={evidence.map((item) => item.evidenceId)}
                onOpenEvidence={openMissionEvidence}
                resumeAvailable={canResumeEngineeringMission}
                resumeUnavailableMessage={
                  missionResumeEligibility.unavailableMessage
                }
                resumeBusy={missionResumeBusy}
                onResume={() => void resumeEngineeringMission()}
                reverifyAvailable={canReverifyBlockedEngineeringMissionStep}
                reverifyUnavailableMessage={
                  missionReverifyEligibility.unavailableMessage
                }
                reverifyBusy={missionReverifyBusy}
                onReverifyBlockedStep={() =>
                  void reverifyBlockedEngineeringMissionStep()
                }
                startNewAvailable={canStartNewEngineeringMission}
                startNewBusy={missionStartBusy}
                onStartNew={() => void startNewEngineeringMission()}
              />
            )}

            <div className="conversation-body" aria-live="polite">
              {conversation?.engineeringHandoff && (
                <aside className="engineering-handoff-lineage">
                  <strong>Accepted engineering baseline</strong>
                  <span>
                    planning {conversation.engineeringHandoff.planningConversationId}
                    {" · plan "}
                    {conversation.engineeringHandoff.reviewedPlanDigest.slice(0, 12)}
                    {" · build "}
                    {conversation.engineeringHandoff.buildBundleId.slice(0, 12)}
                    {" · reopen "}
                    {conversation.engineeringHandoff.reopenReceiptSchemaVersion.replace(
                      "controlstudio-native-reopen-receipt/",
                      "",
                    )}
                  </span>
                </aside>
              )}
              {currentMessages.length === 0 && !visibleStreamingText ? (
                <div className="empty-conversation">
                  <span className="agent-orb" aria-hidden="true">
                    <Icon name="spark" size={26} />
                  </span>
                  <p className="empty-kicker">CONTROLSTUDIO AGENT</p>
                  <h3>
                    {newProjectPlanning
                      ? "프로젝트 계획을 준비하고 있습니다"
                      : "무엇을 확인할까요?"}
                  </h3>
                  <p>
                    {newProjectPlanning
                      ? "설비 목적, 동작 순서, I/O, 모드, 인터록과 검증 방법을 분리해 검토 가능한 계획으로 만듭니다."
                      : "단편적인 래더 답변이 아니라 프로젝트 전체의 근거, 영향, 불확실성과 안전 조건을 함께 확인합니다."}
                  </p>
                  {!newProjectPlanning && (
                    <div className="prompt-suggestions" aria-label="질문 예시">
                      {[
                        "프로젝트에 어떤 프로그램이 있는지 알려 줘",
                        "M0320A가 사용된 위치를 찾아 줘",
                        "초기동작 프로그램의 구조를 설명해 줘",
                      ].map((suggestion) => (
                        <button
                          key={suggestion}
                          type="button"
                          disabled={!canChat}
                          onClick={() => {
                            setMessage(suggestion);
                            composerRef.current?.focus();
                          }}
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <ol className="message-list" aria-label="대화 메시지">
                  {currentMessages.map((item) => (
                    <li
                      key={item.messageId}
                      className={`message-item message-${item.role} ${
                        item.status === "failed" ? "message-failed" : ""
                      } ${
                        item.role === "assistant" &&
                        item.workflow === "new_project_planning" &&
                        item.messageId === latestPlanningResult?.messageId
                          ? "message-plan-review"
                          : ""
                      }`}
                    >
                      <div className="message-meta">
                        <strong>
                          {item.role === "user"
                            ? "사용자"
                            : item.role === "assistant"
                              ? "ControlStudio"
                              : "시스템"}
                        </strong>
                        <time dateTime={item.createdAt}>
                          {timeLabel(item.createdAt)}
                        </time>
                        {item.role === "assistant" &&
                          item.workflow === "new_project_planning" &&
                          item.messageId === latestPlanningResult?.messageId &&
                          item.planStatus && (
                            <span className="plan-review-badge">
                              {item.planStatus === "ready_for_review" &&
                              item.planArtifact?.technicalConfiguration &&
                              item.planArtifact?.design.collectionScope &&
                              item.planArtifact?.design.ladderRungs?.length
                                ? "계획 검토 가능"
                                : item.planStatus === "ready_for_review"
                                  ? "기술 구성·범위·LD rung 포함 재계획 필요"
                                  : "추가 확인 필요"}
                            </span>
                          )}
                      </div>
                      <div className="message-content">
                        {item.planArtifact ? (
                          <p className="plan-message-reference">
                            {item.messageId === latestPlanningResult?.messageId
                              ? "위 Engineering Plan에 이 응답의 설계 항목과 미결 결정을 구조화했습니다. 수정할 내용은 아래 대화에 남겨 주세요."
                              : "이전 Engineering Plan 버전입니다. 현재 검토 대상은 화면 상단의 최신 계획입니다."}
                          </p>
                        ) : (
                          <>
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {item.content}
                            </ReactMarkdown>
                            {item.engineeringAnalysis && (
                              <EngineeringAnalysisPanel
                                analysis={item.engineeringAnalysis}
                                digest={item.engineeringAnalysisDigest}
                                programs={programs}
                                focusAvailable={programFocusAvailable}
                                focusBusyProgramId={programFocusBusyId}
                                onFocusLocation={(programId, location) => {
                                  const activeConversationId = conversationIdRef.current;
                                  if (!activeConversationId) {
                                    return;
                                  }
                                  void focusEngineeringLocation(
                                    activeConversationId,
                                    item.messageId,
                                    programId,
                                    location,
                                  );
                                }}
                              />
                            )}
                          </>
                        )}
                      </div>
                    </li>
                  ))}
                  {visibleStreamingText && (
                    <li className="message-item message-assistant message-streaming">
                      <div className="message-meta">
                        <strong>ControlStudio</strong>
                        <span>응답 작성 중</span>
                      </div>
                      <div className="message-content">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {visibleStreamingText}
                        </ReactMarkdown>
                      </div>
                    </li>
                  )}
                </ol>
              )}
            </div>

            <footer className="composer">
              <label htmlFor="agent-message" className="sr-only">
                에이전트에게 메시지 보내기
              </label>
              <textarea
                ref={composerRef}
                id="agent-message"
                rows={2}
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                disabled={!canChat || sending}
                placeholder={
                  !hostReady
                    ? "Host에 연결한 뒤 대화할 수 있습니다"
                    : planningConversationActive &&
                        !planningAgentReady
                      ? "계획 모델 연결을 설정해 주세요"
                      : planningConversationActive
                        ? "계획을 보완하거나 에이전트의 질문에 답해 주세요"
                    : !engineeringAgentReady
                      ? "엔지니어링 모델 연결을 설정해 주세요"
                      : !mcpReady
                      ? "MCP 안전 코어를 확인해 주세요"
                      : !hasEngineeringTarget
                        ? "먼저 XG5000 프로젝트나 온라인 LS PLC를 선택해 주세요"
                        : "증상이나 확인할 동작을 설명해 주세요"
                }
              />
              {engineeringScopeVisible && (
                <fieldset className="change-request-scope">
                  <legend>에이전트 작업 범위</legend>
                  <label>
                    <input
                      type="radio"
                      name="change-request-scope"
                      value="analysis_only"
                      checked={changeRequestScope === "analysis_only"}
                      onChange={() => {
                        setChangeRequestScope("analysis_only");
                        setVerificationScope("offline_native_reopen");
                      }}
                      disabled={sending}
                    />
                    <span>
                      <strong>읽기·분석</strong>
                      변경 transaction을 만들지 않음
                    </span>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={verificationScope === "offline_native_reopen_xgsim"}
                      onChange={(event) =>
                        setVerificationScope(
                          event.target.checked
                            ? "offline_native_reopen_xgsim"
                            : "offline_native_reopen",
                        )
                      }
                      disabled={
                        sending ||
                        changeRequestScope !== "offline_candidate_revision" ||
                        !offlineCandidateAvailable ||
                        !mneXgSimAvailable
                      }
                    />
                    <span>
                      <strong>저장 후 XG-SIM 검증 요청</strong>
                      V6 OUT rung 후보에서만 지원되며, 정적 검증을 대체하지 않습니다.
                    </span>
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="change-request-scope"
                      value="offline_candidate_revision"
                      checked={
                        changeRequestScope === "offline_candidate_revision"
                      }
                      onChange={() => {
                        if (offlineCandidateAvailable) {
                          setChangeRequestScope("offline_candidate_revision");
                        } else {
                          void prepareOfflineCandidateSession();
                        }
                      }}
                      disabled={
                        sending ||
                        offlineCandidateBindingBusy ||
                        !offlineCandidateSelectable
                      }
                    />
                    <span>
                      <strong>
                        {offlineCandidateBindingBusy
                          ? "오프라인 후보 확인 중"
                          : "승인할 오프라인 후보 준비"}
                      </strong>
                      제안 생성 · 승인 후에만 작업 복사본 실행 가능
                    </span>
                  </label>
                  <p>
                    {offlineCandidateAvailable
                      ? "정적 저장·재열기 검증이 기본이며, V6 OUT rung 후보에는 XG-SIM 검증을 명시 요청할 수 있습니다."
                      : status.engineeringSystem.offlineCandidateEligible
                        ? "이 선택은 현재 읽기 전용 작업 복사본을 승인 후보로 다시 검증합니다. 변경은 제안과 승인 뒤에만 적용됩니다."
                        : "오프라인 후보는 원본 보존 작업 복사본과 검증된 XG5000 대상에서만 선택할 수 있습니다."}
                  </p>
                </fieldset>
              )}
              <div className="composer-footer">
                <span>
                  {engineeringScopeVisible
                    ? selectedOfflineScopeUnavailable
                      ? "현재 대상에서는 오프라인 후보를 만들 수 없습니다"
                      : `${changeRequestScope === "analysis_only" ? "읽기·분석" : "오프라인 후보 준비"} · Enter 전송`
                    : "Enter 전송 · Shift+Enter 줄바꿈"}
                </span>
                <button
                  className="send-button"
                  type="button"
                  disabled={
                    !canChat ||
                    !message.trim() ||
                    sending ||
                    selectedOfflineScopeUnavailable
                  }
                  aria-label="메시지 보내기"
                  onClick={() => void sendMessage()}
                >
                  <Icon name="send" size={18} />
                </button>
              </div>
            </footer>
          </article>

          {activePanel && (
            <aside
              ref={insightPanelRef}
              className="insight-panel"
              tabIndex={-1}
              aria-label="열린 작업 정보"
            >
            <div
              className="panel-tabs"
              role="tablist"
              aria-label="작업 정보"
            >
              {canBrowseEngineeringPrograms && (
                <button
                  type="button"
                  role="tab"
                  aria-selected={activePanel === "programs"}
                  onClick={() => {
                    setActivePanel("programs");
                    if (programs.length === 0 && !programsLoading) {
                      void loadEngineeringPrograms();
                    }
                  }}
                >
                  <Icon name="folder" size={16} />
                  프로그램
                  <span>{programs.length}</span>
                </button>
              )}
              {evidence.length > 0 && (
                <button
                  type="button"
                  role="tab"
                  aria-selected={activePanel === "evidence"}
                  onClick={() => setActivePanel("evidence")}
                >
                  <Icon name="evidence" size={16} />
                  근거
                  <span>{evidence.length}</span>
                </button>
              )}
              <button
                type="button"
                role="tab"
                aria-selected={activePanel === "activity"}
                onClick={() => setActivePanel("activity")}
              >
                <Icon name="activity" size={16} />
                진행
                <span>{activities.length}</span>
              </button>
              <button
                className="close-insight-button"
                type="button"
                aria-label="작업 정보 닫기"
                onClick={closeInsightPanel}
              >
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="insight-content" role="tabpanel">
              {activePanel === "programs" && (
                <ProgramNavigatorPanel
                  programs={programs}
                  loading={programsLoading}
                  focusAvailable={programFocusAvailable}
                  focusUnavailableReason={programFocusUnavailableReason}
                  focusBusyProgramId={programFocusBusyId}
                  notice={programNotice}
                  onRefresh={() => void loadEngineeringPrograms()}
                  onFocus={(programId) =>
                    void focusEngineeringProgram(programId)
                  }
                />
              )}
              {activePanel === "evidence" &&
                (evidence.length > 0 ? (
                  <ul className="evidence-list">
                    {evidence.map((item) => (
                      <li
                        key={item.evidenceId}
                        id={`evidence-${item.evidenceId}`}
                        className={
                          selectedEvidenceId === item.evidenceId
                            ? "evidence-item evidence-item--selected"
                            : "evidence-item"
                        }
                        tabIndex={-1}
                      >
                        <span className="evidence-kind">{item.kind}</span>
                        <strong>{item.title}</strong>
                        <p>{item.summary}</p>
                        <code className="evidence-id">{item.evidenceId}</code>
                        {item.location && (
                          <span className="evidence-location">
                            <Icon name="link" size={14} />
                            {[
                              item.location.project,
                              item.location.program,
                              item.location.network,
                            ]
                              .filter(Boolean)
                              .join(" · ") || "근거 위치"}
                          </span>
                        )}
                        {item.knowledgeRef && (
                          <code className="evidence-knowledge-ref">
                            {item.knowledgeRef}
                          </code>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="empty-insight">
                    <span>
                      <Icon name="evidence" size={23} />
                    </span>
                    <h3>아직 수집된 근거가 없습니다</h3>
                    <p>
                      분석 결과가 구조화된 근거 이벤트를 제공하면 프로그램 위치,
                      참조와 검증 결과가 여기에 표시됩니다.
                    </p>
                  </div>
                ))}

              {activePanel === "activity" && engineeringDebugPanel}

              {activePanel === "activity" &&
                (activities.length > 0 ? (
                  <ol className="activity-list">
                    {activities.map((item) => (
                      <li
                        key={item.id}
                        className={`${item.tone ?? "default"}${
                          item.toolState ? ` tool-${item.toolState}` : ""
                        }`}
                      >
                        <span className="activity-marker" />
                        <div>
                          <strong>
                            {item.toolState ? item.detail : item.title}
                          </strong>
                          <p>
                            {item.toolState
                              ? `${TOOL_STATE_COPY[item.toolState]}${
                                  activityDuration(item)
                                    ? ` · ${activityDuration(item)}`
                                    : ""
                                }`
                              : item.detail}
                          </p>
                          {item.evidenceId && (
                            <button
                              className="activity-evidence-link"
                              type="button"
                              onClick={(event) =>
                                openMissionEvidence(
                                  item.evidenceId!,
                                  event.currentTarget,
                                )
                              }
                            >
                              연결된 근거 열기
                            </button>
                          )}
                          {debugMode && (
                            <code className="activity-debug-meta">
                              #{item.sequence} · {item.eventType}
                              {item.runId ? ` · run=${item.runId}` : ""}
                              {item.requestId
                                ? ` · request=${item.requestId}`
                                : ""}
                              {item.toolCallId
                                ? ` · toolCallId=${item.toolCallId}`
                                : ""}
                              {item.diagnostic ? ` · ${item.diagnostic}` : ""}
                            </code>
                          )}
                        </div>
                        <time>{item.time}</time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="empty-insight">
                    <span>
                      <Icon name="activity" size={23} />
                    </span>
                    <h3>실행 활동이 없습니다</h3>
                    <p>
                      대화를 시작하면 서버가 소유한 실행 상태와 MCP 도구 진행
                      이벤트가 순서대로 표시됩니다.
                    </p>
                  </div>
                ))}
            </div>

            </aside>
          )}
          </section>
        )}

        <div className="connection-notice" role="status" aria-live="polite">
          {connectionMessage}
        </div>
      </main>

      <ConversationHistoryDialog
        open={conversationHistoryOpen}
        items={conversationHistory}
        activeConversationId={conversation?.conversationId ?? null}
        loading={conversationHistoryLoading}
        selectingConversationId={conversationHistorySelectingId}
        notice={conversationHistoryNotice}
        onClose={() => {
          if (conversationHistorySelectingId === null) {
            setConversationHistoryOpen(false);
          }
        }}
        onSelect={(conversationId) =>
          void selectConversationHistory(conversationId)
        }
      />
      <ModelSettingsDialog
        open={settingsOpen}
        hostReady={hostReady}
        onClose={() => setSettingsOpen(false)}
        onSaved={() => void refreshStatus()}
      />
      <KnowledgeDialog
        open={knowledgeOpen}
        service={KNOWLEDGE_DIALOG_SERVICE}
        projectScopeHint={status.workspace?.workspaceId}
        onClose={() => setKnowledgeOpen(false)}
      />

      {reviewedCloseDialogOpen && reviewedCloseDialogTarget && (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={() => {
            if (!reviewedCloseBusy) setReviewedCloseDialogOpen(false);
          }}
        >
          <section
            className="shutdown-dialog reviewed-close-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reviewed-close-dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="shutdown-dialog-header">
              <span>
                <Icon name="warning" size={20} />
              </span>
              <div>
                <p className="eyebrow">REVIEWED NO-SAVE CLOSE</p>
                <h2 id="reviewed-close-dialog-title">저장하지 않고 닫기</h2>
              </div>
            </header>
            <div className="shutdown-dialog-body">
              {!reviewedCloseReceipt ? (
                <>
                  <p>
                    다음에 표시된 정확한 XG5000 세션만 닫습니다. 저장 대화상자가
                    예상과 다르거나 정체성이 달라지면 작업은 중단되고 창은 닫지 않습니다.
                  </p>
                  <dl className="reviewed-close-binding">
                    <div><dt>프로젝트</dt><dd>{reviewedCloseDialogTarget.projectPath}</dd></div>
                    <div><dt>PID / HWND</dt><dd>{reviewedCloseDialogTarget.pid} / {reviewedCloseDialogTarget.hwnd}</dd></div>
                    <div><dt>프로세스 생성 시각</dt><dd>{reviewedCloseDialogTarget.processCreateTime}</dd></div>
                    <div><dt>세션 ID</dt><dd>{reviewedCloseDialogTarget.sessionId}</dd></div>
                    <div><dt>프로젝트 해시</dt><dd>{reviewedCloseDialogTarget.projectHash}</dd></div>
                  </dl>
                  <p>
                    이 동작은 한 번만 요청하며, 성공해도 다른 프로젝트를 열거나 다시
                    시도하지 않습니다.
                  </p>
                </>
              ) : (
                <div className="reviewed-close-result" role="status">
                  <strong>저장 없이 닫기 완료</strong>
                  <p>
                    PID {reviewedCloseReceipt.reviewedBinding.pid} · HWND {reviewedCloseReceipt.reviewedBinding.hwnd}
                    의 확인된 세션을 닫았고, durable receipt를 기록했습니다.
                  </p>
                  <code>{reviewedCloseReceipt.receiptDigest}</code>
                  <p>새 프로젝트는 아래 버튼을 누른 뒤 별도로 선택해 여세요.</p>
                </div>
              )}
              {reviewedCloseNotice && (
                <div className="inline-notice warning-notice reviewed-close-recovery" role="status">
                  <Icon name="warning" size={17} />
                  <span>{reviewedCloseNotice}</span>
                </div>
              )}
            </div>
            <footer className="shutdown-dialog-actions">
              {!reviewedCloseReceipt ? (
                <>
                  <button
                    className="button secondary-button"
                    type="button"
                    disabled={reviewedCloseBusy}
                    onClick={() => setReviewedCloseDialogOpen(false)}
                  >
                    취소
                  </button>
                  <button
                    className="button danger-button"
                    type="button"
                    disabled={reviewedCloseBusy || reviewedCloseAttempted}
                    onClick={() => void reviewedNoSaveClose()}
                  >
                    {reviewedCloseBusy ? "닫는 중…" : "저장하지 않고 닫기"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="button secondary-button"
                    type="button"
                    onClick={() => setReviewedCloseDialogOpen(false)}
                  >
                    결과 닫기
                  </button>
                  <button
                    className="button primary-button"
                    type="button"
                    onClick={() => {
                      setReviewedCloseDialogOpen(false);
                      setWorkChooserOpen(false);
                      setLocalProjectOpen(true);
                    }}
                  >
                    별도로 프로젝트 열기
                  </button>
                </>
              )}
            </footer>
          </section>
        </div>
      )}

      {shutdownDialogOpen && (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={() => {
            if (!shutdownBusy) {
              setShutdownDialogOpen(false);
            }
          }}
        >
          <section
            className="shutdown-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="shutdown-dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="shutdown-dialog-header">
              <span>
                <Icon name="power" size={20} />
              </span>
              <div>
                <p className="eyebrow">EXPLICIT HOST SHUTDOWN</p>
                <h2 id="shutdown-dialog-title">ControlStudio 종료</h2>
              </div>
            </header>
            <div className="shutdown-dialog-body">
              <p>
                Host와 MCP 프로세스를 명시적으로 종료합니다. 브라우저나 PWA
                창을 닫는 것만으로는 ControlStudio가 종료되지 않습니다.
              </p>
              <p>
                실행 중인 분석 또는 안전 확인 작업이 있으면 Host가 종료를
                거부하며, 해당 이유를 이 화면에 표시합니다.
              </p>
              <p>
                개발 코드 변경을 반영할 때는 재시작을 선택하세요. 이미 실행 중인
                elevated Host가 replacement를 시작하므로 추가 UAC 확인을 요청하지
                않으며, XG5000은 종료하거나 다시 열지 않습니다.
              </p>
              {shutdownNotice && (
                <div
                  className="inline-notice warning-notice shutdown-notice"
                  role="status"
                >
                  <Icon name="warning" size={17} />
                  <span>{shutdownNotice}</span>
                </div>
              )}
              {cancelNotice && (
                <p className="shutdown-cancel-result" role="status">
                  {cancelNotice}
                </p>
              )}
            </div>
            <footer className="shutdown-dialog-actions">
              <button
                className="button secondary-button"
                type="button"
                disabled={shutdownBusy}
                onClick={() => setShutdownDialogOpen(false)}
              >
                계속 사용
              </button>
              {canCancelActiveRun && (
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={shutdownBusy || cancelBusy}
                  onClick={() => void cancelActiveRun()}
                >
                  {cancelBusy ? "취소 요청 중…" : "실행 중 작업 취소"}
                </button>
              )}
              <button
                className="button secondary-button"
                type="button"
                disabled={shutdownBusy}
                onClick={() => void restartControlStudio()}
              >
                {shutdownBusy ? "재시작 확인 중…" : "코드 다시 불러오기 (UAC 없음)"}
              </button>
              <button
                className="button danger-button"
                type="button"
                disabled={shutdownBusy}
                onClick={() => void shutdownControlStudio()}
              >
                {shutdownBusy ? "종료 확인 중…" : "Host와 MCP 종료"}
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
