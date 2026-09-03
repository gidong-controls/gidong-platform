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
  CreateNativeProjectRequest,
  CreateChangeTransactionRequest,
  DecideChangeTransactionRequest,
  ExecuteChangeTransactionRequest,
  FinalizeSimulationChangeRequest,
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
  NativeProjectCreatedResponse,
  NativeProjectOperation,
  NativeProjectReconciliationResponse,
  NativeProjectResumedResponse,
  NativeSimulationTrialDecisionRequest,
  NativeSimulationTrialDecisionResponse,
  OpenLocalWorkspaceRequest,
  OpenLocalWorkspaceResponse,
  ProblemDetails,
  ProjectPlanReviewRequest,
  ProjectPlanReviewResponse,
  ReadLSControllerRequest,
  TraceLSControllerRequest,
  ReconcileChangeTransactionRequest,
  ReconcileNativeProjectRequest,
  ResumeNativeProjectRequest,
  ResumeLocalWorkspaceRequest,
  ResumeEngineeringMissionRequest,
  ReverifyBlockedEngineeringMissionStepRequest,
  EngineeringMission,
  SaveModelSettingsRequest,
  SendMessageRequest,
  SendMessageResponse,
  XG5000ProjectFileSelection,
  XG5000InstancesResponse,
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

function transactionFromEnvelope(value: unknown): ChangeTransaction {
  if (!isRecord(value)) {
    throw new Error("Host returned a malformed change transaction response.");
  }
  return parseChangeTransaction(value.transaction);
}

function reconciliationFromEnvelope(
  value: unknown,
): ChangeReconciliationResponse {
  if (!isRecord(value) || typeof value.reconciled !== "boolean") {
    throw new Error("Host returned a malformed change reconciliation response.");
  }
  return {
    transaction: parseChangeTransaction(value.transaction),
    reconciled: value.reconciled,
  };
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
    return this.request<SendMessageResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    );
  }

  reviewProjectPlan(
    conversationId: string,
    request: ProjectPlanReviewRequest,
  ): Promise<ProjectPlanReviewResponse> {
    return this.request<ProjectPlanReviewResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/project-plan/review`,
      {
        method: "POST",
        body: request,
      },
    );
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

  createNativeProject(
    request: CreateNativeProjectRequest,
  ): Promise<NativeProjectOperation> {
    return this.request<NativeProjectCreatedResponse>(
      "/api/v1/native-projects",
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    ).then((response) => response.operation);
  }

  getNativeProject(
    nativeProjectId: string,
    signal?: AbortSignal,
  ): Promise<NativeProjectOperation> {
    return this.request<NativeProjectOperation>(
      `/api/v1/native-projects/${encodeURIComponent(nativeProjectId)}`,
      { signal },
    );
  }

  getCompletedUnboundNativeProject(
    conversationId: string,
    messageId: string,
    planDigest: string,
    signal?: AbortSignal,
  ): Promise<NativeProjectOperation> {
    const search = new URLSearchParams({
      conversationId,
      messageId,
      planDigest,
    });
    return this.request<NativeProjectOperation>(
      `/api/v1/native-projects/recovery?${search.toString()}`,
      { signal },
    );
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
    return this.request<SendMessageResponse>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/engineering-mission/resume`,
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    );
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

  decideNativeSimulationTrial(
    nativeProjectId: string,
    request: NativeSimulationTrialDecisionRequest,
  ): Promise<NativeProjectOperation> {
    return this.request<NativeSimulationTrialDecisionResponse>(
      `/api/v1/native-projects/${encodeURIComponent(nativeProjectId)}/simulation-trial/decision`,
      {
        method: "POST",
        body: request,
      },
    ).then((response) => response.operation);
  }

  reconcileNativeProject(
    nativeProjectId: string,
    request: ReconcileNativeProjectRequest,
  ): Promise<NativeProjectOperation> {
    return this.request<NativeProjectReconciliationResponse>(
      `/api/v1/native-projects/${encodeURIComponent(nativeProjectId)}/reconcile`,
      {
        method: "POST",
        body: request,
      },
    ).then((response) => response.operation);
  }

  resumeNativeProject(
    nativeProjectId: string,
    request: ResumeNativeProjectRequest,
  ): Promise<NativeProjectOperation> {
    return this.request<NativeProjectResumedResponse>(
      `/api/v1/native-projects/${encodeURIComponent(nativeProjectId)}/resume`,
      {
        method: "POST",
        body: request,
      },
    ).then((response) => response.operation);
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
  ): Promise<EngineeringProgramFocusResponse> {
    return this.request<EngineeringProgramFocusResponse>(
      `/api/v1/engineering/programs/${encodeURIComponent(programId)}/focus`,
      { method: "POST" },
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
      { method: "POST", body: JSON.stringify(request) },
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
    ).then(parseChangeTransaction);
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
    ).then(parseChangeTransactionList);
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

  finalizeSimulationChange(
    transactionId: string,
    request: FinalizeSimulationChangeRequest,
  ): Promise<ChangeTransaction> {
    return this.request<unknown>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/simulation-decision`,
      { method: "POST", body: request },
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
