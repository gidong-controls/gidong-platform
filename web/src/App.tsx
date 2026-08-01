import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { hostApi, HostApiError } from "./api/client";
import { ControlStudioEventStream } from "./api/sse";
import type {
  Conversation,
  ChangeTransaction,
  EngineeringChangeRequestScope,
  EngineeringProgram,
  EngineeringTargetBinding,
  EvidenceRef,
  HostEventEnvelope,
  HostStatusResponse,
  RunState,
  ServiceState,
  XG5000Instance,
} from "./api/types";
import { ChangeApprovalPanel } from "./components/ChangeApprovalPanel";
import { ChangeRecoveryPanel } from "./components/ChangeRecoveryPanel";
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
import { NewProjectPlanReviewPanel } from "./components/NewProjectPlanReviewPanel";
import { OnlineControllerPanel } from "./components/OnlineControllerPanel";
import { ProgramNavigatorPanel } from "./components/ProgramNavigatorPanel";

const CONVERSATION_STORAGE_KEY = "controlstudio.activeConversationId";
const BROWSER_SESSION_STORAGE_KEY = "controlstudio.browserSession";
const CHANGE_ACTION_PATTERN =
  /(?:바꿔|바꾸|변경|늘려|늘리|줄여|줄이|수정|적용|교체|조정)|\b(?:change|modify|increase|decrease|adjust|replace|apply)\b/iu;

function isChangeRequest(value: string): boolean {
  return CHANGE_ACTION_PATTERN.test(value);
}

const INITIAL_STATUS: HostStatusResponse = {
  schemaVersion: "1",
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

const ACKNOWLEDGEABLE_CHANGE_STATES = new Set<ChangeTransaction["state"]>([
  "completed",
  "rolled_back",
  "failed",
  "rejected",
  "expired",
]);

const TOOL_ACTIVITY_NAMES: Record<string, string> = {
  "xg5000.session_status": "XG5000 세션 확인",
  "project.list_programs": "프로그램 목록 확인",
  "program.focus": "XG5000 프로그램 열기",
  "program.read_ld": "래더 프로그램 읽기",
  "program.logic_graph": "논리 구조 분석",
  "program.state_model": "상태 모델 분석",
  "device.references": "디바이스 참조 검색",
  "project.behavior_report": "프로젝트 동작 분석",
  "project.snapshot": "프로젝트 스냅샷 확인",
  "evidence.get": "검증 근거 확인",
  "controller.ls.status": "PLC 상태 읽기",
  "controller.ls.read": "PLC 디바이스 읽기",
};

interface ActivityItem {
  id: string;
  title: string;
  detail: string;
  time: string;
  tone?: "default" | "warning";
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
    !status.engineeringSystem.readReady ||
    !status.engineeringSystem.sessionId ||
    !status.workspace
  ) {
    return null;
  }
  const revisionId =
    status.workspace.revision ?? status.workspace.artifactHash;
  if (!revisionId) {
    return null;
  }
  return {
    workspaceId: status.workspace.workspaceId,
    revisionId,
    engineeringSessionId: status.engineeringSystem.sessionId,
  };
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
    (left.performanceAndAcceptance ?? "") === right.performanceAndAcceptance
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
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [programs, setPrograms] = useState<EngineeringProgram[]>([]);
  const [programsLoading, setProgramsLoading] = useState(false);
  const [programFocusBusyId, setProgramFocusBusyId] = useState<string>();
  const [programNotice, setProgramNotice] = useState("");
  const [message, setMessage] = useState("");
  const [changeRequestScope, setChangeRequestScope] =
    useState<EngineeringChangeRequestScope | null>(null);
  const [sending, setSending] = useState(false);
  const [localProjectOpen, setLocalProjectOpen] = useState(false);
  const [localProjectPath, setLocalProjectPath] = useState("");
  const [localProjectBusy, setLocalProjectBusy] = useState(false);
  const [localProjectBrowseBusy, setLocalProjectBrowseBusy] = useState(false);
  const [localProjectNotice, setLocalProjectNotice] = useState("");
  const [workspaceResumeBusy, setWorkspaceResumeBusy] = useState(false);
  const [workspaceResumeNotice, setWorkspaceResumeNotice] = useState("");
  const [workChooserOpen, setWorkChooserOpen] = useState(false);
  const [newProjectBriefOpen, setNewProjectBriefOpen] = useState(false);
  const [newProjectPlanning, setNewProjectPlanning] =
    useState<NewProjectPlanningSession | null>(null);
  const [projectPlanReviewBusy, setProjectPlanReviewBusy] = useState(false);
  const [nativeProjectOpen, setNativeProjectOpen] = useState(false);
  const [nativeProjectRestoreId, setNativeProjectRestoreId] = useState<
    string | null
  >(null);
  const [onlineControllerOpen, setOnlineControllerOpen] = useState(false);
  const [instancePickerOpen, setInstancePickerOpen] = useState(false);
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [instancesLoading, setInstancesLoading] = useState(false);
  const [attachingHwnd, setAttachingHwnd] = useState<number | null>(null);
  const [instanceNotice, setInstanceNotice] = useState("");
  const [sessionRequired, setSessionRequired] = useState(false);
  const [shutdownDialogOpen, setShutdownDialogOpen] = useState(false);
  const [shutdownBusy, setShutdownBusy] = useState(false);
  const [shutdownNotice, setShutdownNotice] = useState("");
  const [shutdownComplete, setShutdownComplete] = useState(false);
  const [activeChange, setActiveChange] = useState<ChangeTransaction | null>(
    null,
  );
  const [changeBusy, setChangeBusy] = useState(false);
  const [changeNotice, setChangeNotice] = useState("");
  const streamRef = useRef<ControlStudioEventStream | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const statusRefreshRef = useRef<Promise<void> | null>(null);
  const statusRefreshEpochRef = useRef(0);
  const conversationIdRef = useRef<string | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const returnWorkspaceRef = useRef<ReturnWorkspaceSnapshot | null>(null);
  const insightPanelRef = useRef<HTMLElement>(null);
  const insightReturnFocusRef = useRef<HTMLButtonElement | null>(null);

  const hostReady = status.host.state === "ready";
  const agentReady = status.agent.state === "ready";
  const planningAgentReady =
    status.agent.planningReady ?? agentReady;
  const engineeringAgentReady =
    status.agent.engineeringReady ?? agentReady;
  const mcpReady = status.mcp.state === "ready";
  const planningConversationActive = Boolean(newProjectPlanning);
  const changeScopeRequired =
    !planningConversationActive && isChangeRequest(message);
  const canCancelActiveRun =
    Boolean(activeRunId) &&
    (!activeRunState || !TERMINAL_RUN_STATES.has(activeRunState));
  const canChat =
    hostReady &&
    (planningConversationActive
      ? planningAgentReady
      : engineeringAgentReady) &&
    Boolean(conversation) &&
    !canCancelActiveRun &&
    (planningConversationActive ||
      (mcpReady &&
        ((Boolean(status.workspace) &&
          (status.engineeringSystem.sessionVerified ||
            status.engineeringSystem.readReady)) ||
          status.controller.state === "ready")));

  useEffect(() => {
    setChangeRequestScope(null);
  }, [conversation?.conversationId]);

  const refreshConversation = useCallback(async (conversationId: string) => {
    const detail = await hostApi.getConversation(conversationId);
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

  const addActivity = useCallback(
    (
      event: HostEventEnvelope,
      title: string,
      detail: string,
      tone: ActivityItem["tone"] = "default",
    ) => {
      setActivities((current) =>
        [
          {
            id: event.eventId,
            title,
            detail,
            time: timeLabel(event.occurredAt),
            tone,
          },
          ...current.filter((item) => item.id !== event.eventId),
        ].slice(0, 50),
      );
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
        const delta = event.payload.delta;
        if (typeof delta === "string") {
          setStreamingText((current) => current + delta);
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
        }
      }

      if (
        event.eventType === "approval.requested" ||
        event.eventType === "approval.resolved" ||
        event.eventType === "change.transaction.updated" ||
        event.eventType === "operation.quarantined"
      ) {
        const embedded = event.payload.transaction;
        if (
          embedded &&
          typeof embedded === "object" &&
          "transactionId" in embedded
        ) {
          setActiveChange(embedded as unknown as ChangeTransaction);
        } else {
          const transactionId = event.payload.transactionId;
          if (typeof transactionId === "string") {
            void hostApi
              .getChangeTransaction(transactionId)
              .then(setActiveChange)
              .catch(() => {
                setChangeNotice(
                  "변경 계약 상태를 다시 불러오지 못했습니다.",
                );
              });
          }
        }
      }

      if (
        event.eventType === "message.appended" ||
        event.eventType === "agent.message" ||
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
        setStreamingText("");
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
          if (event.runId) {
            setActiveRunId(event.runId);
          }
          setActiveRunState(runState);
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

      const eventLabels: Partial<Record<HostEventEnvelope["eventType"], string>> =
        {
          "conversation.created": "서버에 새 대화를 만들었습니다.",
          "message.appended": "대화 메시지를 저장했습니다.",
          "tool.started": "MCP 작업을 시작했습니다.",
          "tool.completed": "MCP 작업이 끝났습니다.",
          "tool.failed": "MCP 작업이 실패했습니다.",
          "approval.requested": "검토가 필요한 변경안이 도착했습니다.",
          "approval.resolved": "변경안 승인 결정을 기록했습니다.",
          "change.transaction.updated": "변경 트랜잭션 상태가 갱신됐습니다.",
          "controller.connected": "LS PLC에 읽기 전용으로 연결했습니다.",
          "controller.observed": "LS PLC 운전 상태를 다시 읽었습니다.",
          "controller.devices_read": "LS PLC 디바이스 값을 읽었습니다.",
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
      }
    },
    [addActivity, refreshConversation, refreshHostStatus],
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

  const startFreshConversation = useCallback(
    async (title: string) => {
      streamRef.current?.stop();
      clearStoredConversationId();
      conversationIdRef.current = null;
      setConversation(null);
      setNewProjectPlanning(null);
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
      returnWorkspaceRef.current = null;

      const created = await hostApi.createConversation({ title });
      storeConversationId(created.conversationId);
      conversationIdRef.current = created.conversationId;
      setConversation(created);
      startEventStream(created.conversationId, 0);
      return created;
    },
    [startEventStream],
  );

  const ensureConversation = useCallback(async (
    currentStatus: HostStatusResponse,
  ) => {
    const restoreIfMatching = async (
      conversationId: string,
    ): Promise<Conversation | null> => {
      const detail = await hostApi.getConversation(conversationId);
      if (!conversationMatchesCurrentTarget(detail, currentStatus)) {
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
        if (restored) {
          return restored;
        }
        clearStoredConversationId();
      } catch (error) {
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
      const latest = recent.items.find((item) =>
        conversationMatchesCurrentTarget(item, currentStatus),
      );
      if (latest) {
        try {
          const restored = await restoreIfMatching(latest.conversationId);
          if (restored) {
            storeConversationId(restored.conversationId);
            return restored;
          }
        } catch (error) {
          if (!(error instanceof HostApiError) || error.status !== 404) {
            throw error;
          }
        }
      }
    }

    const created = await hostApi.createConversation({
      title: "PLC 분석 대화",
    });
    storeConversationId(created.conversationId);
    conversationIdRef.current = created.conversationId;
    setConversation(created);
    return created;
  }, []);

  const connectHost = useCallback(async () => {
    requestAbortRef.current?.abort();
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
      const activeNativeProject =
        bootstrap.status.activeOperation?.kind === "native_project_create" &&
        bootstrap.status.activeOperation.nativeProjectId
          ? bootstrap.status.activeOperation.nativeProjectId
          : null;
      if (activeNativeProject) {
        setNativeProjectRestoreId(activeNativeProject);
        setLocalProjectOpen(false);
        setInstancePickerOpen(false);
        setOnlineControllerOpen(false);
        setNativeProjectOpen(true);
      }
      if (bootstrap.status.activeOperation?.runId) {
        setActiveRunId(bootstrap.status.activeOperation.runId);
        if (
          bootstrap.status.activeOperation.state &&
          bootstrap.status.activeOperation.state !== "allocated"
        ) {
          setActiveRunState(bootstrap.status.activeOperation.state);
        }
      }
      const operationConversationId =
        bootstrap.status.activeOperation?.conversationId;
      let activeConversation: Conversation;
      if (operationConversationId) {
        try {
          activeConversation = await refreshConversation(
            operationConversationId,
          );
          storeConversationId(activeConversation.conversationId);
        } catch (error) {
          if (!(error instanceof HostApiError) || error.status !== 404) {
            throw error;
          }
          activeConversation = await ensureConversation(bootstrap.status);
        }
      } else {
        activeConversation = await ensureConversation(bootstrap.status);
      }
      if (controller.signal.aborted) {
        return;
      }
      const restoredPlanning =
        planningSessionFromConversation(activeConversation);
      if (restoredPlanning) {
        setNewProjectPlanning(restoredPlanning);
      }
      const recentChanges = await hostApi.listChangeTransactions(
        activeConversation.conversationId,
        20,
        controller.signal,
      );
      if (controller.signal.aborted) {
        return;
      }
      const restorableChange =
        bootstrap.status.activeOperation?.kind === "native_project_create"
          ? null
          : recentChanges.find(
              (transaction) =>
                PRIMARY_CHANGE_STATES.has(transaction.state) &&
                !transaction.receiptAcknowledgedAt,
            ) ?? null;
      setActiveChange(restorableChange ?? null);
      setConnectionMessage(
        restorableChange
          ? "Host와 대화를 복원하고 진행 중인 변경 계약을 다시 불러왔습니다."
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
        setStatus({
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

  const openLocalProject = async () => {
    const sourcePath = localProjectPath.trim();
    if (!sourcePath || localProjectBusy) {
      return;
    }
    setLocalProjectBusy(true);
    setLocalProjectNotice("");
    try {
      const result = await hostApi.openLocalWorkspace({
        sourcePath,
        openInXg5000: true,
        timeoutSeconds: 120,
      });
      const nextStatus = await hostApi.getStatus();
      applyHostStatus(nextStatus);
      if (result.session && nextStatus.engineeringSystem.readReady) {
        await startFreshConversation(
          `${nextStatus.workspace?.displayName ?? "XG5000 프로젝트"} 분석`,
        );
        setWorkChooserOpen(false);
        setLocalProjectOpen(false);
        setConnectionMessage(
          "원본을 보존한 작업 복사본을 XG5000에서 열고 분석 세션에 연결했습니다.",
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
        setLocalProjectNotice(
          "안전한 작업 복사본은 만들었습니다. XG5000 창을 확인한 뒤 실행 창 선택으로 연결해 주세요.",
        );
      }
    } catch (error) {
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
      applyHostStatus(nextStatus);
      if (result.session && nextStatus.engineeringSystem.readReady) {
        // Resume deliberately starts a fresh conversation after revalidating
        // the source, working copy, and XG5000 session. This prevents stale
        // evidence from a prior machine session from being carried forward.
        await startFreshConversation(
          `${nextStatus.workspace?.displayName ?? "XG5000 프로젝트"} 분석`,
        );
        setWorkChooserOpen(false);
        setLocalProjectOpen(false);
        setConnectionMessage(
          "저장된 작업 복사본과 해시를 다시 확인하고 XG5000 분석 세션을 복원했습니다.",
        );
        if (nextStatus.agent.state === "ready") {
          composerRef.current?.focus();
        } else {
          setSettingsOpen(true);
        }
      } else {
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

  const openInstancePicker = async () => {
    setInstancePickerOpen(true);
    setInstancesLoading(true);
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
    setAttachingHwnd(instance.hwnd);
    setInstanceNotice("");
    try {
      await hostApi.attachXG5000({
        pid: instance.pid,
        hwnd: instance.hwnd,
      });
      const nextStatus = await hostApi.getStatus();
      applyHostStatus(nextStatus);
      if (
        nextStatus.engineeringSystem.sessionVerified ||
        nextStatus.engineeringSystem.readReady
      ) {
        await startFreshConversation(
          `${nextStatus.workspace?.displayName ?? instance.title ?? "XG5000 프로젝트"} 분석`,
        );
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
      const transaction = await hostApi.getChangeTransaction(
        activeChange.transactionId,
      );
      setActiveChange(transaction);
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

  const reconcileChange = async (pid: number, hwnd: number) => {
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
        },
      );
      setActiveChange(response.transaction);
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
      void hostApi
        .getStatus()
        .then(setStatus)
        .catch(() => {
          setConnectionMessage(
            "복구 결과는 기록됐지만 Host 상태를 새로 읽지 못했습니다.",
          );
        });
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "격리된 변경의 복구 증거를 확인하지 못했습니다.",
      );
      void hostApi
        .getChangeTransaction(activeChange.transactionId)
        .then(setActiveChange)
        .catch(() => undefined);
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
      const transaction = await hostApi.decideChangeTransaction(
        activeChange.transactionId,
        {
          decision: "reject",
          expectedPlanDigest: activeChange.planDigest,
        },
      );
      setActiveChange(transaction);
      setChangeNotice("변경안을 거절했습니다. 실제 변경은 실행되지 않았습니다.");
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
    const target = activeChange.plan.target;
    const programId =
      typeof target.programId === "string"
        ? target.programId
        : typeof target.program_id === "string"
          ? target.program_id
          : undefined;
    if (!programId) {
      setChangeNotice(
        "이 변경 계약에는 XG5000에서 열 수 있는 정확한 program ID가 없습니다.",
      );
      return;
    }
    setChangeNotice("");
    await focusEngineeringProgram(programId);
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
    setActiveChange(executed);
    setChangeNotice(
      executed.state === "completed"
        ? "변경 적용, 구조 diff, XG5000 프로그램 검사와 저장을 완료했습니다. 시뮬레이션·실제 PLC 검증은 별도입니다."
        : executed.state === "rolled_back"
          ? "오프라인 검사 결과에 따라 변경을 원래 상태로 복구했습니다."
          : executed.state === "quarantined"
            ? "실제 결과를 확정할 수 없어 작업을 격리했습니다. 자동 재실행하지 않습니다."
            : "변경 트랜잭션 상태가 갱신되었습니다.",
    );
    const nextStatus = await hostApi.getStatus();
    applyHostStatus(nextStatus);
  };

  const approveChange = async () => {
    if (!activeChange || activeChange.state !== "pending_approval" || changeBusy) {
      return;
    }
    const transactionId = activeChange.transactionId;
    setChangeBusy(true);
    setChangeNotice("");
    try {
      const approved = await hostApi.decideChangeTransaction(transactionId, {
        decision: "approve",
        expectedPlanDigest: activeChange.planDigest,
      });
      setActiveChange(approved);
      setChangeNotice(
        "정확한 변경 계약에 대한 승인을 기록했습니다. 실제 적용은 다음 단계에서 별도로 시작합니다.",
      );
    } catch (error) {
      setChangeNotice(
        error instanceof HostApiError
          ? error.message
          : "변경안 승인을 기록하지 못했습니다.",
      );
      void hostApi
        .getChangeTransaction(transactionId)
        .then(setActiveChange)
        .catch(() => undefined);
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
      void hostApi
        .getChangeTransaction(approved.transactionId)
        .then(setActiveChange)
        .catch(() => undefined);
    } finally {
      setChangeBusy(false);
    }
  };

  const acknowledgeChangeReceipt = async () => {
    if (
      !activeChange ||
      !ACKNOWLEDGEABLE_CHANGE_STATES.has(activeChange.state) ||
      changeBusy
    ) {
      return;
    }
    setChangeBusy(true);
    setChangeNotice("");
    try {
      await hostApi.acknowledgeChangeReceipt(activeChange.transactionId);
      setActiveChange(null);
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
      setStatus({
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
    setStreamingText("");
    setConnectionMessage(
      updating
        ? "수정한 요구사항을 계획 에이전트에 전달하고 있습니다."
        : "새 프로젝트 계획 대화를 준비하고 있습니다.",
    );

    try {
      let planningConversation = conversation;
      if (!updating || !planningConversation) {
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
                if (
                  nextStatus.activeOperation.state &&
                  nextStatus.activeOperation.state !== "allocated"
                ) {
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
    if (changeScopeRequired && !changeRequestScope) {
      setConnectionMessage(
        "프로젝트에 쓰는 변경인지, 분석만 필요한지 먼저 선택해 주세요.",
      );
      return;
    }

    setSending(true);
    setStreamingText("");
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
        changeRequestScope: changeScopeRequired
          ? changeRequestScope ?? undefined
          : undefined,
      });
      setMessage("");
      setChangeRequestScope(null);
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

  const localAnalysisAvailable = hostReady && mcpReady;
  const newProjectPlanningAvailable = hostReady && planningAgentReady;
  const nativeProjectFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "native_project_create",
  );
  const onlineObserveFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "online_observe",
  );
  const offlineMutateFeature = status.featureReadiness?.find(
    (feature) => feature.featureId === "offline_mutate",
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
        onlineObserveFeature.verification !== "fake_only" &&
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
  const workspaceActive =
    Boolean(status.workspace) &&
    (status.engineeringSystem.sessionVerified ||
      status.engineeringSystem.readReady);
  const hasEngineeringTarget =
    workspaceActive ||
    status.controller.state === "ready";
  const entryPanelOpen =
    newProjectBriefOpen ||
    nativeProjectOpen ||
    localProjectOpen ||
    instancePickerOpen ||
    onlineControllerOpen;
  const hasPrimaryChange = Boolean(
    activeChange &&
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
  const latestPlanningMessage = [...currentMessages]
    .reverse()
    .find((item) => item.role === "assistant" || item.role === "user");
  const latestPlanningAssistant =
    latestPlanningMessage?.role === "assistant"
      ? latestPlanningMessage
      : undefined;
  const latestPlanningResult =
    latestPlanningAssistant?.status === "complete" &&
    latestPlanningAssistant.planStatus
      ? latestPlanningAssistant
      : undefined;
  const latestPlanningArtifact = latestPlanningResult?.planArtifact;
  const latestPlanningDigest = latestPlanningResult?.planDigest;
  const latestPlanningReview =
    latestPlanningResult?.planReview &&
    latestPlanningResult.planReview.planDigest === latestPlanningDigest &&
    latestPlanningArtifact &&
    latestPlanningResult.planReview.reviewedSectionIds.length ===
      latestPlanningArtifact.sections.length &&
    latestPlanningArtifact.sections.every((section) =>
      latestPlanningResult.planReview?.reviewedSectionIds.includes(
        section.sectionId,
      ),
    )
      ? latestPlanningResult.planReview
      : undefined;
  const planningHasResponse = Boolean(
    newProjectPlanning && latestPlanningResult,
  );
  const planningReadyForTechnicalSetup =
    Boolean(newProjectPlanning) &&
    latestPlanningArtifact?.status === "ready_for_review" &&
    Boolean(latestPlanningDigest) &&
    Boolean(latestPlanningReview);

  const reviewLatestProjectPlan = async (
    planDigest: string,
    reviewedSectionIds: string[],
  ) => {
    if (
      !conversation ||
      !latestPlanningResult ||
      latestPlanningResult.planDigest !== planDigest ||
      projectPlanReviewBusy
    ) {
      return;
    }
    setProjectPlanReviewBusy(true);
    setCancelNotice("");
    try {
      await hostApi.reviewProjectPlan(conversation.conversationId, {
        messageId: latestPlanningResult.messageId,
        planDigest,
        reviewedSectionIds,
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
        : status.workspace
          ? "프로젝트 확인 제한"
          : planningConversationActive
            ? "신규 설계 · 계획 단계"
            : "대상 선택 전";
  const headerTarget = newProjectPlanning?.brief.projectName
    ? newProjectPlanning.brief.projectName
    : workspaceActive && status.workspace
      ? status.workspace.displayName
      : status.controller.endpoint
        ? `${status.controller.endpoint.host}:${status.controller.endpoint.port}`
        : status.workspace
          ? `${status.workspace.displayName} · 저장됨, 재검증 필요`
          : "엔지니어링 대상 선택 전";

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
            <span>PLC 엔지니어링 에이전트</span>
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
            className="header-text-button"
            type="button"
            onClick={() => setKnowledgeOpen(true)}
            aria-label="엔지니어링 지식 열기"
          >
            <Icon name="database" size={16} />
            지식
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
            detail={
              status.agent.modelDisplayName ??
              status.agent.detail ??
              "모델 연결 대기"
            }
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
              busy={changeBusy}
              notice={changeNotice}
              onApprove={() => void approveChange()}
              onExecuteApproved={() => void executeApprovedChange()}
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
                busy={changeBusy}
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

                {status.workspace && !hasEngineeringTarget && (
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
                    </div>
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
                        ? "모델 연결 설정이 필요합니다"
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
              onCancel={closeNewProjectBrief}
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
                      지금 만들 수 있는 범위는 빈 XG5000 프로젝트 골격까지입니다.
                      로직 생성·검사·재열기·시뮬레이션은 아직 실행되지 않습니다.
                    </span>
                  </div>
                </section>
              )}
              <NativeProjectPanel
                initialNativeProjectId={nativeProjectRestoreId ?? undefined}
                initialProjectName={
                  nativeProjectRestoreId
                    ? undefined
                    : newProjectPlanning?.brief.projectName
                }
                initialDescription={
                  nativeProjectRestoreId
                    ? undefined
                    : newProjectPlanning?.brief.machinePurpose
                }
                reviewedPlan={
                  !nativeProjectRestoreId &&
                  conversation &&
                  latestPlanningResult &&
                  latestPlanningReview
                    ? {
                        conversationId: conversation.conversationId,
                        messageId: latestPlanningResult.messageId,
                        planDigest: latestPlanningReview.planDigest,
                        reviewedSectionIds:
                          latestPlanningReview.reviewedSectionIds,
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
                onStatusChanged={setStatus}
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
              <form
                className="local-project-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void openLocalProject();
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
                    void openInstancePicker();
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
              onClose={() => setOnlineControllerOpen(false)}
              onStatusChanged={setStatus}
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
                      <div>
                        <strong>{instance.title || "XG5000"}</strong>
                        <span>
                          PID {instance.pid} · HWND {instance.hwnd}
                          {instance.version ? ` · ${instance.version}` : ""}
                        </span>
                      </div>
                      <button
                        className="button primary-button"
                        type="button"
                        disabled={attachingHwnd !== null}
                        onClick={() => void attachInstance(instance)}
                      >
                        {attachingHwnd === instance.hwnd
                          ? "검증 중…"
                          : "분석 세션 연결"}
                      </button>
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
                  disabled={instancesLoading || attachingHwnd !== null}
                  onClick={() => void openInstancePicker()}
                >
                  다시 확인
                </button>
              </div>
            </section>
          )}
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
                  {status.workspace?.displayName ??
                    (status.controller.endpoint
                      ? `${status.controller.endpoint.host}:${status.controller.endpoint.port}`
                      : "대상 확인 중")}
                </strong>
              </div>
              <dl>
                <div>
                  <dt>벤더</dt>
                  <dd>{status.workspace?.vendor?.toUpperCase() ?? "LS"}</dd>
                </div>
                <div>
                  <dt>엔지니어링 도구</dt>
                  <dd>
                    {status.engineeringSystem.application}
                    {status.engineeringSystem.processId
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
                {status.workspace?.artifactHash && (
                  <div>
                    <dt>프로젝트 해시</dt>
                    <dd>
                      <code>{status.workspace.artifactHash.slice(0, 12)}…</code>
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
                      sending ||
                      canCancelActiveRun
                    }
                    title={
                      !planningReadyForTechnicalSetup
                        ? "미결 결정을 해소하고 현재 Engineering Plan 버전에 대한 사람의 검토를 기록해야 합니다"
                        : !nativeProjectAvailable
                        ? "XG5000 새 프로젝트 생성 코어가 준비되어야 합니다"
                        : undefined
                    }
                    onClick={() => setNativeProjectOpen(true)}
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
                      latestPlanningArtifact.status === "ready_for_review" &&
                      !latestPlanningReview
                    ? "다음 단계 전에 아래 Engineering Plan의 범위·가정·안전 조건·검증 계획을 검토 완료해야 합니다."
                  : nativeProjectAvailable
                    ? "다음 단계 제한: 현재는 빈 XG5000 프로젝트 골격 생성까지만 지원합니다. 일반 로직 생성·프로그램 검사·재열기·시뮬레이션은 아직 완료되지 않았습니다."
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
                review={latestPlanningReview}
                busy={
                  projectPlanReviewBusy ||
                  sending ||
                  canCancelActiveRun
                }
                onReview={(digest, sectionIds) =>
                  void reviewLatestProjectPlan(digest, sectionIds)
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

            <div className="conversation-body" aria-live="polite">
              {currentMessages.length === 0 && !streamingText ? (
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
                              {item.planStatus === "ready_for_review"
                                ? "계획 검토 가능"
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
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {item.content}
                          </ReactMarkdown>
                        )}
                      </div>
                    </li>
                  ))}
                  {streamingText && (
                    <li className="message-item message-assistant message-streaming">
                      <div className="message-meta">
                        <strong>ControlStudio</strong>
                        <span>응답 작성 중</span>
                      </div>
                      <div className="message-content">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {streamingText}
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
                onChange={(event) => {
                  const nextMessage = event.target.value;
                  setMessage(nextMessage);
                  if (!isChangeRequest(nextMessage)) {
                    setChangeRequestScope(null);
                  }
                }}
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
              {changeScopeRequired && (
                <fieldset className="change-request-scope">
                  <legend>이 변경 요청의 범위</legend>
                  <label>
                    <input
                      type="radio"
                      name="change-request-scope"
                      value="analysis_only"
                      checked={changeRequestScope === "analysis_only"}
                      onChange={() => setChangeRequestScope("analysis_only")}
                      disabled={sending}
                    />
                    <span>
                      <strong>분석·변경안만</strong>
                      프로젝트에 쓰지 않음
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
                      onChange={() =>
                        setChangeRequestScope("offline_candidate_revision")
                      }
                      disabled={sending}
                    />
                    <span>
                      <strong>오프라인 후보 revision</strong>
                      작업 복사본에 저장 · 자동 원복 없음
                    </span>
                  </label>
                  <p>
                    XG-SIM 시험과 온라인·런중 시험은 아직 사용할 수 없습니다.
                  </p>
                </fieldset>
              )}
              <div className="composer-footer">
                <span>
                  {changeScopeRequired && !changeRequestScope
                    ? "범위를 선택해야 전송할 수 있습니다"
                    : "Enter 전송 · Shift+Enter 줄바꿈"}
                </span>
                <button
                  className="send-button"
                  type="button"
                  disabled={
                    !canChat ||
                    !message.trim() ||
                    sending ||
                    (changeScopeRequired && !changeRequestScope)
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
                      <li key={item.evidenceId}>
                        <span className="evidence-kind">{item.kind}</span>
                        <strong>{item.title}</strong>
                        <p>{item.summary}</p>
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

              {activePanel === "activity" &&
                (activities.length > 0 ? (
                  <ol className="activity-list">
                    {activities.map((item) => (
                      <li key={item.id} className={item.tone}>
                        <span className="activity-marker" />
                        <div>
                          <strong>{item.title}</strong>
                          <p>{item.detail}</p>
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
