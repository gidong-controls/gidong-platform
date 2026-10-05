import type {
  Conversation,
  ConversationCreate,
  ConversationPage,
  EngineeringProgramFocusResponse,
  EngineeringLocation,
  EngineeringLocationFocusResponse,
  EngineeringProgramsResponse,
  AttachXG5000Request,
  AttachXG5000Response,
  PrepareOfflineCandidateSessionRequest,
  PrepareOfflineCandidateSessionResponse,
  ReviewedNoSaveCloseRequest,
  ReviewedNoSaveCloseResponse,
  BrowserSessionResponse,
  CancelRunResponse,
  ChangeReconciliationResponse,
  ChangeTargetFocusResponse,
  ChangeTransaction,
  ConnectLSControllerRequest,
  ControllerRestoreBeginRequest,
  ControllerRestoreCompleteRequest,
  ControllerRestoreCompleted,
  ControllerRestoreHandoff,
  ControllerRestorePreflight,
  ControllerRestorePreflightRequest,
  
  CreateChangeTransactionRequest,
  DecideChangeTransactionRequest,
  ExecuteChangeTransactionRequest,
  
  CreateKnowledgeMemoryRequest,
  HostBootstrapResponse,
  HostShutdownResponse,
  HostRestartResponse,
  HostStatusResponse,
  ImportKnowledgeRequest,
  KnowledgeImportResult,
  KnowledgeSearchResponse,
  KnowledgeSource,
  KnowledgeSourcePage,
  KnowledgeSummary,
  LSControllerObservation,
  LSControllerRead,
  LSControllerTrace,
  LocalWorkspaceCompare,
  ModelCapabilityResult,
  ModelSettings,
  
  
  
  
  
  
  OpenLocalWorkspaceRequest,
  OpenLocalWorkspaceResponse,
  ProblemDetails,
  
  
  ReadLSControllerRequest,
  TraceLSControllerRequest,
  ReconcileChangeTransactionRequest,
  
  
  ResumeLocalWorkspaceRequest,
  ResumeEngineeringMissionRequest,
  ReverifyBlockedEngineeringMissionStepRequest,
  EngineeringMission,
  SaveModelSettingsRequest,
  SendMessageRequest,
  SendMessageResponse,
  
  XG5000ProjectFileSelection,
  XG5000InstancesResponse,
  LaunchIdleXG5000Response,
  LaunchIdleXG5000LaunchMode,
} from "./types";
import {
  isRecord,
  parseChangeTransaction,
  parseChangeTransactionList,
} from "./changeContracts";

export class HostApiError extends Error {
  readonly status: number;
  readonly problem?: ProblemDetails;

  constructor(message: string, status: number, problem?: ProblemDetails) {
    super(message);
    this.name = "HostApiError";
    this.status = status;
    this.problem = problem;
  }
}

/** A successful Host response that does not satisfy this web client's contract. */
export class HostResponseContractError extends Error {
  constructor() {
    super("Host returned an unsupported response contract.");
    this.name = "HostResponseContractError";
  }
}

function idleLaunchResponse(value: unknown, launchMode: LaunchIdleXG5000LaunchMode): LaunchIdleXG5000Response {
  return parseHostResponseContract(value, (response) => {
    const receiptMode = isRecord(response) && response.launchMode === undefined ? "when_none"
      : isRecord(response) ? response.launchMode : null;
    if (
      !isRecord(response) || response.operation !== "xg5000.launch_idle" ||
      response.verified !== true || response.status !== "launched" ||
      response.launchAction !== "process_start" || receiptMode !== launchMode ||
      typeof response.noExistingInstances !== "boolean" ||
      (launchMode === "when_none" && response.noExistingInstances !== true) ||
      response.blankWindow !== true || response.modalObserved !== false ||
      response.attentionReason !== null || response.processExited !== false ||
      response.processTerminated !== false || typeof response.receiptDigest !== "string" ||
      !/^[0-9a-f]{64}$/.test(response.receiptDigest) ||
      !isRecord(response.launchedIdentity) || !isRecord(response.idleBinding)
    ) {
      throw new Error("Invalid idle launch receipt.");
    }
    const launched = response.launchedIdentity;
    const binding = response.idleBinding;
    for (const identity of [launched, binding]) {
      if (
        typeof identity.pid !== "number" || !Number.isSafeInteger(identity.pid) || identity.pid <= 0 ||
        typeof identity.process_create_time !== "number" ||
        !Number.isFinite(identity.process_create_time) || identity.process_create_time <= 0 ||
        typeof identity.executable !== "string" || !identity.executable.trim()
      ) {
        throw new Error("Invalid idle launch identity.");
      }
    }
    if (
      typeof binding.hwnd !== "number" || !Number.isSafeInteger(binding.hwnd) || binding.hwnd <= 0 ||
      binding.pid !== launched.pid || binding.process_create_time !== launched.process_create_time ||
      binding.executable !== launched.executable
    ) {
      throw new Error("Idle launch identity mismatch.");
    }
    return { ...response, launchMode: receiptMode } as unknown as LaunchIdleXG5000Response;
  });
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  operationId?: string;
};

function problemMessage(problem: ProblemDetails | undefined, status: number): string {
  if (typeof problem?.detail === "string") {
    return problem.detail;
  }
  return problem?.title ?? `Host 요청에 실패했습니다. (${status})`;
}

function parseHostResponseContract<T>(
  value: unknown,
  parse: (value: unknown) => T,
): T {
  try {
    return parse(value);
  } catch (error) {
    if (error instanceof HostResponseContractError) {
      throw error;
    }
    throw new HostResponseContractError();
  }
}

function transactionFromEnvelope(value: unknown): ChangeTransaction {
  return parseHostResponseContract(value, (response) => {
    if (!isRecord(response)) {
      throw new Error("Host returned a malformed change transaction response.");
    }
    return parseChangeTransaction(response.transaction);
  });
}

function reconciliationFromEnvelope(
  value: unknown,
): ChangeReconciliationResponse {
  return parseHostResponseContract(value, (response) => {
    if (!isRecord(response) || typeof response.reconciled !== "boolean") {
      throw new Error("Host returned a malformed change reconciliation response.");
    }
    return {
      transaction: parseChangeTransaction(response.transaction),
      reconciled: response.reconciled,
    };
  });
}

const RUN_START_STATES = new Set<SendMessageResponse["state"]>([
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
const CANONICAL_RUN_ID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;

function runStartResponse(
  value: unknown,
  expectedOperationId: string,
): SendMessageResponse {
  return parseHostResponseContract(value, (response) => {
    if (
      !isRecord(response) ||
      typeof response.runId !== "string" ||
      !CANONICAL_RUN_ID.test(response.runId) ||
      response.operationId !== expectedOperationId ||
      typeof response.state !== "string" ||
      !RUN_START_STATES.has(
        response.state as SendMessageResponse["state"],
      )
    ) {
      throw new Error("Host returned a malformed run-start response.");
    }
    return {
      runId: response.runId,
      operationId: response.operationId,
      state: response.state as SendMessageResponse["state"],
    };
  });
}

export class HostApiClient {
  readonly baseUrl: string;
  private csrfToken?: string;
  private sessionToken?: string;
  private onUnauthorized?: () => void;

  constructor(baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  setCsrfToken(token: string | undefined): void {
    this.csrfToken = token;
  }

  setSessionToken(token: string | undefined): void {
    this.sessionToken = token;
  }

  setUnauthorizedHandler(handler: (() => void) | undefined): void {
    this.onUnauthorized = handler;
  }

  getAuthorizationHeader(): string | undefined {
    return this.sessionToken ? `Bearer ${this.sessionToken}` : undefined;
  }

  invalidateSession(): void {
    this.sessionToken = undefined;
    this.csrfToken = undefined;
    this.onUnauthorized?.();
  }

  private async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const { body, operationId, ...requestInit } = options;
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");

    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (this.csrfToken && options.method && options.method !== "GET") {
      headers.set("X-ControlStudio-CSRF", this.csrfToken);
    }
    if (operationId) {
      headers.set("Idempotency-Key", operationId);
    }
    const sessionProtected =
      path.startsWith("/api/v1/") || path === "/api/session/refresh";
    if (sessionProtected && this.sessionToken) {
      headers.set("Authorization", `Bearer ${this.sessionToken}`);
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...requestInit,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    if (response.status === 401 && sessionProtected) {
      this.invalidateSession();
    }

    if (!response.ok) {
      let problem: ProblemDetails | undefined;
      try {
        problem = (await response.json()) as ProblemDetails;
      } catch {
        problem = undefined;
      }
      throw new HostApiError(
        problemMessage(problem, response.status),
        response.status,
        problem,
      );
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  getBootstrap(signal?: AbortSignal): Promise<HostBootstrapResponse> {
    return this.request<HostBootstrapResponse>("/api/v1/bootstrap", { signal });
  }

  exchangeHandoffTicket(
    handoffTicket: string,
    signal?: AbortSignal,
  ): Promise<BrowserSessionResponse> {
    this.csrfToken = undefined;
    this.sessionToken = undefined;
    return this.request<BrowserSessionResponse>("/api/session", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${handoffTicket}`,
      },
      signal,
    });
  }

  refreshBrowserSession(): Promise<BrowserSessionResponse> {
    return this.request<BrowserSessionResponse>("/api/session/refresh", {
      method: "POST",
    }).then((session) => {
      this.sessionToken = session.sessionToken;
      this.csrfToken = undefined;
      return session;
    });
  }

  getStatus(signal?: AbortSignal): Promise<HostStatusResponse> {
    return this.request<HostStatusResponse>("/api/v1/status", { signal });
  }

  getConversation(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<Conversation> {
    return this.request<Conversation>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
      { signal },
    );
  }

  listConversations(
    limit = 20,
    offset = 0,
    signal?: AbortSignal,
  ): Promise<ConversationPage> {
    const query = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    return this.request<ConversationPage>(
      `/api/v1/conversations?${query.toString()}`,
      { signal },
    );
  }

  createConversation(request: ConversationCreate): Promise<Conversation> {
    return this.request<Conversation>("/api/v1/conversations", {
      method: "POST",
      body: request,
    });
  }

  sendMessage(
    conversationId: string,
    request: SendMessageRequest,
  ): Promise<SendMessageResponse> {
    return this.request<unknown>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    ).then((value) => runStartResponse(value, request.operationId));
  }

  

  getModelSettings(signal?: AbortSignal): Promise<ModelSettings> {
    return this.request<ModelSettings>("/api/v1/settings/model", { signal });
  }

  saveModelSettings(
    request: SaveModelSettingsRequest,
  ): Promise<ModelSettings> {
    return this.request<ModelSettings>("/api/v1/settings/model", {
      method: "PUT",
      body: request,
    });
  }

  testModelSettings(
    request: SaveModelSettingsRequest,
  ): Promise<ModelCapabilityResult> {
    return this.request<ModelCapabilityResult>(
      "/api/v1/settings/model/test",
      {
        method: "POST",
        body: request,
      },
    );
  }

  getKnowledgeSummary(signal?: AbortSignal): Promise<KnowledgeSummary> {
    return this.request<KnowledgeSummary>("/api/v1/knowledge/summary", {
      signal,
    });
  }

  listKnowledgeSources(
    options: {
      kinds?: string[];
      statuses?: string[];
      limit?: number;
      signal?: AbortSignal;
    } = {},
  ): Promise<KnowledgeSourcePage> {
    const query = new URLSearchParams({
      limit: String(options.limit ?? 200),
    });
    options.kinds?.forEach((kind) => query.append("kinds", kind));
    options.statuses?.forEach((status) => query.append("statuses", status));
    return this.request<KnowledgeSourcePage>(
      `/api/v1/knowledge/sources?${query.toString()}`,
      { signal: options.signal },
    );
  }

  searchKnowledge(options: {
    query: string;
    vendor?: string;
    kinds?: string[];
    scopes?: string[];
    productFamily?: string;
    version?: string;
    limit?: number;
    signal?: AbortSignal;
  }): Promise<KnowledgeSearchResponse> {
    const query = new URLSearchParams({
      query: options.query,
      limit: String(options.limit ?? 8),
    });
    if (options.vendor) query.set("vendor", options.vendor);
    if (options.productFamily) {
      query.set("product_family", options.productFamily);
    }
    if (options.version) query.set("version", options.version);
    options.kinds?.forEach((kind) => query.append("kinds", kind));
    options.scopes?.forEach((scope) => query.append("scopes", scope));
    return this.request<KnowledgeSearchResponse>(
      `/api/v1/knowledge/search?${query.toString()}`,
      { signal: options.signal },
    );
  }

  importLocalKnowledge(
    request: ImportKnowledgeRequest,
  ): Promise<KnowledgeImportResult> {
    return this.request<KnowledgeImportResult>(
      "/api/v1/knowledge/import-local",
      {
        method: "POST",
        body: request,
      },
    );
  }

  proposeKnowledgeMemory(
    request: CreateKnowledgeMemoryRequest,
  ): Promise<KnowledgeSource> {
    return this.request<KnowledgeSource>("/api/v1/knowledge/memories", {
      method: "POST",
      body: request,
    });
  }

  confirmKnowledgeMemory(
    sourceId: string,
    expectedRevision: string,
  ): Promise<KnowledgeSource> {
    return this.request<KnowledgeSource>(
      `/api/v1/knowledge/memories/${encodeURIComponent(sourceId)}/confirm`,
      {
        method: "POST",
        body: { expectedRevision },
      },
    );
  }

  deleteKnowledgeSource(sourceId: string): Promise<void> {
    return this.request<void>(
      `/api/v1/knowledge/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    );
  }

  getXG5000Instances(signal?: AbortSignal): Promise<XG5000InstancesResponse> {
    return this.request<XG5000InstancesResponse>("/api/v1/xg5000/instances", {
      signal,
    });
  }

  

  

  launchIdleXG5000(launchMode: LaunchIdleXG5000LaunchMode = "when_none"): Promise<LaunchIdleXG5000Response> {
    if (launchMode !== "when_none" && launchMode !== "new_instance") {
      return Promise.reject(new HostResponseContractError());
    }
    return this.request<unknown>("/api/v1/xg5000/launch-idle", {
      method: "POST",
      body: { confirmIdleLaunch: true, timeoutSeconds: 20, launchMode },
    }).then((value) => idleLaunchResponse(value, launchMode));
  }

  

  preflightControllerProjectRestore(
    request: ControllerRestorePreflightRequest,
  ): Promise<ControllerRestorePreflight> {
    return this.request<ControllerRestorePreflight>(
      "/api/v1/controller-project-restore/preflight",
      { method: "POST", body: request },
    );
  }

  beginControllerProjectRestore(
    request: ControllerRestoreBeginRequest,
  ): Promise<ControllerRestoreHandoff> {
    return this.request<ControllerRestoreHandoff>(
      "/api/v1/controller-project-restore/begin",
      { method: "POST", body: request, operationId: request.operationId },
    );
  }

  completeControllerProjectRestore(
    request: ControllerRestoreCompleteRequest,
  ): Promise<ControllerRestoreCompleted> {
    return this.request<ControllerRestoreCompleted>(
      "/api/v1/controller-project-restore/complete",
      { method: "POST", body: request, operationId: request.operationId },
    );
  }

  resumeEngineeringMission(
    conversationId: string,
    request: ResumeEngineeringMissionRequest,
  ): Promise<SendMessageResponse> {
    return this.request<unknown>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/engineering-mission/resume`,
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    ).then((value) => runStartResponse(value, request.operationId));
  }

  reverifyBlockedEngineeringMissionStep(
    conversationId: string,
    request: ReverifyBlockedEngineeringMissionStepRequest,
  ): Promise<EngineeringMission> {
    return this.request<EngineeringMission>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/engineering-mission/reverify-blocked-step`,
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    );
  }

  

  

  

  openLocalWorkspace(
    request: OpenLocalWorkspaceRequest,
  ): Promise<OpenLocalWorkspaceResponse> {
    return this.request<OpenLocalWorkspaceResponse>(
      "/api/v1/workspaces/open-local",
      {
        method: "POST",
        body: request,
      },
    );
  }

  resumeLocalWorkspace(
    workspaceId: string,
    request: ResumeLocalWorkspaceRequest,
  ): Promise<OpenLocalWorkspaceResponse> {
    return this.request<OpenLocalWorkspaceResponse>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/resume`,
      {
        method: "POST",
        body: request,
      },
    );
  }

  

  compareLocalWorkspace(workspaceId: string): Promise<LocalWorkspaceCompare> {
    return this.request<LocalWorkspaceCompare>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/compare`,
    );
  }

  selectXG5000ProjectFile(): Promise<XG5000ProjectFileSelection> {
    return this.request<XG5000ProjectFileSelection>(
      "/api/v1/files/select-xg5000-project",
      {
        method: "POST",
      },
    );
  }

  listEngineeringPrograms(
    signal?: AbortSignal,
  ): Promise<EngineeringProgramsResponse> {
    return this.request<EngineeringProgramsResponse>(
      "/api/v1/engineering/programs",
      { signal },
    );
  }

  focusEngineeringProgram(
    programId: string,
    request?: { displayIntent: "LD" },
  ): Promise<EngineeringProgramFocusResponse> {
    return this.request<EngineeringProgramFocusResponse>(
      `/api/v1/engineering/programs/${encodeURIComponent(programId)}/focus`,
      { method: "POST", ...(request === undefined ? {} : { body: request }) },
    );
  }

  focusEngineeringLocation(
    conversationId: string,
    messageId: string,
    location: EngineeringLocation,
  ): Promise<EngineeringLocationFocusResponse> {
    return this.request<EngineeringLocationFocusResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/engineering-location/focus`,
      {
        method: "POST",
        body: { location },
      },
    );
  }

  attachXG5000(
    request: AttachXG5000Request,
  ): Promise<AttachXG5000Response> {
    return this.request<AttachXG5000Response>("/api/v1/xg5000/attach", {
      method: "POST",
      body: request,
    });
  }

  prepareOfflineCandidateSession(
    request: PrepareOfflineCandidateSessionRequest,
  ): Promise<PrepareOfflineCandidateSessionResponse> {
    return this.request<PrepareOfflineCandidateSessionResponse>(
      "/api/v1/engineering/offline-candidate-session",
      { method: "POST", body: request },
    );
  }

  connectLSController(
    request: ConnectLSControllerRequest,
  ): Promise<LSControllerObservation> {
    return this.request<LSControllerObservation>(
      "/api/v1/controllers/ls/connect",
      {
        method: "POST",
        body: request,
      },
    );
  }

  getLSControllerStatus(signal?: AbortSignal): Promise<LSControllerObservation> {
    return this.request<LSControllerObservation>(
      "/api/v1/controllers/ls/status",
      { signal },
    );
  }

  readLSController(
    request: ReadLSControllerRequest,
  ): Promise<LSControllerRead> {
    return this.request<LSControllerRead>("/api/v1/controllers/ls/read", {
      method: "POST",
      body: request,
    });
  }

  reviewedNoSaveClose(
    request: ReviewedNoSaveCloseRequest,
  ): Promise<ReviewedNoSaveCloseResponse> {
    return this.request<ReviewedNoSaveCloseResponse>(
      "/api/v1/xg5000/reviewed-no-save-close",
      { method: "POST", body: request },
    );
  }

  traceLSController(
    request: TraceLSControllerRequest,
  ): Promise<LSControllerTrace> {
    return this.request<LSControllerTrace>("/api/v1/controllers/ls/trace", {
      method: "POST",
      body: request,
    });
  }

  disconnectLSController(): Promise<void> {
    return this.request<void>("/api/v1/controllers/ls/connection", {
      method: "DELETE",
    });
  }

  shutdownHost(): Promise<HostShutdownResponse | void> {
    return this.request<HostShutdownResponse | void>(
      "/api/v1/host/shutdown",
      {
        method: "POST",
      },
    );
  }

  cancelRun(runId: string): Promise<CancelRunResponse> {
    return this.request<CancelRunResponse>(
      `/api/v1/runs/${encodeURIComponent(runId)}/cancel`,
      {
        method: "POST",
      },
    );
  }

  createChangeTransaction(
    request: CreateChangeTransactionRequest,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      "/api/v1/change-transactions",
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    ).then(transactionFromEnvelope);
  }

  getChangeTransaction(
    transactionId: string,
    signal?: AbortSignal,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}`,
      { signal },
    ).then((value) => parseHostResponseContract(value, parseChangeTransaction));
  }

  restartHost(): Promise<HostRestartResponse> {
    return this.request<HostRestartResponse>("/api/v1/host/restart", {
      method: "POST",
    });
  }

  getRestartStatus(restartReceipt: string): Promise<HostRestartResponse> {
    return this.request<HostRestartResponse>(
      `/api/v1/host/restart/${encodeURIComponent(restartReceipt)}`,
    );
  }

  focusChangeTarget(transactionId: string): Promise<ChangeTargetFocusResponse> {
    return this.request<ChangeTargetFocusResponse>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/focus-target`,
      { method: "POST" },
    );
  }

  acknowledgeChangeReceipt(
    transactionId: string,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/receipt-acknowledgement`,
      {
        method: "POST",
      },
    ).then(transactionFromEnvelope);
  }

  listChangeTransactions(
    conversationId?: string,
    limit = 20,
    signal?: AbortSignal,
  ): Promise<ChangeTransaction[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (conversationId) {
      query.set("conversation_id", conversationId);
    }
    return this.request<unknown>(
      `/api/v1/change-transactions?${query.toString()}`,
      { signal },
    ).then((value) =>
      parseHostResponseContract(value, parseChangeTransactionList),
    );
  }

  decideChangeTransaction(
    transactionId: string,
    request: DecideChangeTransactionRequest,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/decision`,
      {
        method: "POST",
        body: request,
      },
    ).then(transactionFromEnvelope);
  }

  executeChangeTransaction(
    transactionId: string,
    request: ExecuteChangeTransactionRequest,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/execute`,
      {
        method: "POST",
        body: request,
      },
    ).then(transactionFromEnvelope);
  }

  

  reconcileChangeTransaction(
    transactionId: string,
    request: ReconcileChangeTransactionRequest,
  ): Promise<ChangeReconciliationResponse> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/reconcile`,
      {
        method: "POST",
        body: request,
      },
    ).then(reconciliationFromEnvelope);
  }
}

export const hostApi = new HostApiClient();
