import type {
  Conversation,
  ConversationCreate,
  ConversationPage,
  EngineeringProgramFocusResponse,
  EngineeringProgramsResponse,
  AttachXG5000Request,
  AttachXG5000Response,
  BrowserSessionResponse,
  CancelRunResponse,
  ChangeReconciliationResponse,
  ChangeReceiptAcknowledgementResponse,
  ChangeTransaction,
  ChangeDecisionResponse,
  ChangeExecutionResponse,
  ChangeTransactionCreatedResponse,
  ConnectLSControllerRequest,
  CreateNativeProjectRequest,
  CreateChangeTransactionRequest,
  DecideChangeTransactionRequest,
  ExecuteChangeTransactionRequest,
  CreateKnowledgeMemoryRequest,
  HostBootstrapResponse,
  HostShutdownResponse,
  HostStatusResponse,
  ImportKnowledgeRequest,
  KnowledgeImportResult,
  KnowledgeSearchResponse,
  KnowledgeSource,
  KnowledgeSourcePage,
  KnowledgeSummary,
  LSControllerObservation,
  LSControllerRead,
  ModelCapabilityResult,
  ModelSettings,
  NativeProjectCreatedResponse,
  NativeProjectOperation,
  NativeProjectReconciliationResponse,
  OpenLocalWorkspaceRequest,
  OpenLocalWorkspaceResponse,
  ProblemDetails,
  ProjectPlanReviewRequest,
  ProjectPlanReviewResponse,
  ReadLSControllerRequest,
  ReconcileChangeTransactionRequest,
  ReconcileNativeProjectRequest,
  ResumeLocalWorkspaceRequest,
  SaveModelSettingsRequest,
  SendMessageRequest,
  SendMessageResponse,
  XG5000ProjectFileSelection,
  XG5000InstancesResponse,
} from "./types";

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

  attachXG5000(
    request: AttachXG5000Request,
  ): Promise<AttachXG5000Response> {
    return this.request<AttachXG5000Response>("/api/v1/xg5000/attach", {
      method: "POST",
      body: request,
    });
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
    return this.request<ChangeTransactionCreatedResponse>(
      "/api/v1/change-transactions",
      {
        method: "POST",
        body: request,
        operationId: request.operationId,
      },
    ).then((response) => response.transaction);
  }

  getChangeTransaction(
    transactionId: string,
    signal?: AbortSignal,
  ): Promise<ChangeTransaction> {
    return this.request<ChangeTransaction>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}`,
      { signal },
    );
  }

  acknowledgeChangeReceipt(
    transactionId: string,
  ): Promise<ChangeTransaction> {
    return this.request<ChangeReceiptAcknowledgementResponse>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/receipt-acknowledgement`,
      {
        method: "POST",
      },
    ).then((response) => response.transaction);
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
    return this.request<ChangeTransaction[]>(
      `/api/v1/change-transactions?${query.toString()}`,
      { signal },
    );
  }

  decideChangeTransaction(
    transactionId: string,
    request: DecideChangeTransactionRequest,
  ): Promise<ChangeTransaction> {
    return this.request<ChangeDecisionResponse>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/decision`,
      {
        method: "POST",
        body: request,
      },
    ).then((response) => response.transaction);
  }

  executeChangeTransaction(
    transactionId: string,
    request: ExecuteChangeTransactionRequest,
  ): Promise<ChangeTransaction> {
    return this.request<ChangeExecutionResponse>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/execute`,
      {
        method: "POST",
        body: request,
      },
    ).then((response) => response.transaction);
  }

  reconcileChangeTransaction(
    transactionId: string,
    request: ReconcileChangeTransactionRequest,
  ): Promise<ChangeReconciliationResponse> {
    return this.request<ChangeReconciliationResponse>(
      `/api/v1/change-transactions/${encodeURIComponent(transactionId)}/reconcile`,
      {
        method: "POST",
        body: request,
      },
    );
  }
}

export const hostApi = new HostApiClient();
