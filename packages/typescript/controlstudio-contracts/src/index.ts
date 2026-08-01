export * from "./http-openapi.generated.js";

/**
 * Convenience DTO names consumed by the current web client.  The web client
 * re-exports this package verbatim, so there is only one authored API type
 * surface.  `contract_guard.py` binds this package to the FastAPI snapshot.
 */
export type ServiceState =
  | "ready"
  | "starting"
  | "degraded"
  | "unavailable"
  | "quarantined";

export type RunState =
  | "idle"
  | "analyzing"
  | "waiting_for_user"
  | "waiting_for_approval"
  | "approved_pending_execution"
  | "executing"
  | "verifying"
  | "completed"
  | "cancelled"
  | "failed"
  | "quarantined";

export interface ServiceStatus {
  state: ServiceState;
  detail?: string;
  version?: string;
}

export interface AgentStatus extends ServiceStatus {
  modelDisplayName?: string;
  planningReady?: boolean;
  engineeringReady?: boolean;
}

export interface McpStatus extends ServiceStatus {
  serverId?: string;
  toolCount?: number;
  agentToolCount?: number;
}

export interface EngineeringSystemStatus extends ServiceStatus {
  vendor: string;
  application: string;
  processId?: number;
  sessionId?: string;
  sessionVerified: boolean;
  readReady?: boolean;
  identityAssurance?:
    | "strong_readback"
    | "controlled_launch_read_only"
    | "unverified";
}

export interface LSControllerEndpoint {
  host: string;
  port: number;
  transport: "tcp";
  protocol: "XGT_FENET";
}

export interface LSControllerModule {
  base: number;
  slot: number;
}

export interface LSControllerProvisionalIdentity {
  vendor: "LS ELECTRIC";
  protocol: "XGT_FENET";
  cpuFamily?: string;
  cpuInfoCode: number;
  cpuTypeFlag: number;
  osVersion: string;
  module: LSControllerModule;
  fingerprintSha256: string;
  evidenceStrength: string;
  strongEnoughForWrite: false;
  writeAuthorizationReason: string;
}

export interface LSControllerRuntimeStatus {
  mode: string;
  stateFlags: string[];
  systemStateRaw: number;
  padtConnectionFlag: number;
  configurationError: number;
  configurationWarning: number;
}

export interface LSControllerObservation {
  schemaVersion: string;
  operation: "controller.ls.status";
  endpoint: LSControllerEndpoint;
  observedAt: string;
  readOnly: true;
  status: LSControllerRuntimeStatus;
  provisionalIdentity: LSControllerProvisionalIdentity;
}

export interface LSControllerConnectionStatus extends ServiceStatus {
  vendor: "ls";
  protocol: "XGT_FENET";
  endpoint?: LSControllerEndpoint;
  timeoutSeconds?: number;
  readOnly: true;
  strongEnoughForWrite: false;
  observedAt?: string;
  provisionalIdentity?: LSControllerProvisionalIdentity;
}

export type LSControllerDataType =
  | "BIT"
  | "BYTE"
  | "WORD"
  | "DWORD"
  | "LWORD";

export interface ConnectLSControllerRequest {
  host: string;
  port: number;
  timeoutSeconds: number;
}

export interface ReadLSControllerRequest {
  dataType: LSControllerDataType;
  variables: string[];
}

export interface LSControllerValue {
  variable: string;
  rawHex: string;
  unsignedValue: number;
}

export interface LSControllerRead {
  schemaVersion: string;
  operation: "controller.ls.read";
  endpoint: LSControllerEndpoint;
  observedAt: string;
  dataType: LSControllerDataType;
  values: LSControllerValue[];
  readOnly: true;
  strongEnoughForWrite: false;
}

export interface WorkspaceSummary {
  workspaceId: string;
  displayName: string;
  source: "local";
  revision?: string;
  artifactHash?: string;
  vendor: string;
  cpuModel?: string;
}

export type ProductFeatureId =
  | "new_project_planning"
  | "local_project_analysis"
  | "program_focus"
  | "native_project_create"
  | "online_observe"
  | "offline_mutate"
  | "controller_project_restore"
  | "online_modify"
  | "multi_vendor";

export interface ProductFeatureStatus {
  featureId: ProductFeatureId;
  label: string;
  maturity: "supported" | "preview" | "planned";
  verification:
    | "real_runtime"
    | "real_xg5000"
    | "isolated_harness"
    | "fake_only"
    | "not_verified";
  currentReady: boolean;
  detail: string;
}

export interface HostStatusResponse {
  schemaVersion: "1";
  host: ServiceStatus;
  agent: AgentStatus;
  mcp: McpStatus;
  engineeringSystem: EngineeringSystemStatus;
  controller: LSControllerConnectionStatus;
  workspace?: WorkspaceSummary;
  capabilities: string[];
  featureReadiness?: ProductFeatureStatus[];
  activeOperation?: {
    kind?: "change" | "native_project_create";
    runId?: string;
    transactionId?: string;
    nativeProjectId?: string;
    operationId?: string;
    conversationId?: string;
    state?: RunState | NativeProjectState;
    label?: string;
  };
}

export interface HostBootstrapResponse {
  schemaVersion: "1";
  status: HostStatusResponse;
  csrfToken?: string;
  clientSessionId: string;
  lastEventSequence: number;
}

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus = "streaming" | "complete" | "failed";

export interface ConversationMessage {
  messageId: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  runId?: string;
  operationId?: string;
  status: MessageStatus;
  workflow?: "engineering" | "new_project_planning";
  planStatus?: "needs_clarification" | "ready_for_review";
  projectName?: string;
  planArtifact?: NewProjectEngineeringPlan;
  planDigest?: string;
  planReview?: EngineeringPlanReview;
}

export interface Conversation {
  conversationId: string;
  title: string;
  messages: ConversationMessage[];
  updatedAt: string;
  workflow: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
  engineeringTarget?: EngineeringTargetBinding | null;
}

export interface EngineeringTargetBinding {
  workspaceId: string;
  revisionId: string;
  engineeringSessionId: string;
}

export interface ConversationSummary {
  conversationId: string;
  title: string;
  workflow: "engineering" | "new_project_planning";
  engineeringTarget?: EngineeringTargetBinding | null;
  updatedAt: string;
}

export interface ConversationPage {
  items: ConversationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProjectBrief {
  projectName: string;
  machinePurpose: string;
  sequence: string;
  hardwareAndIo: string;
  safetyAndInterlocks: string;
  operatingModes: string;
  performanceAndAcceptance: string;
}

export interface ConversationCreate {
  title?: string;
  workflow?: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
}

export type EngineeringChangeRequestScope =
  | "analysis_only"
  | "offline_candidate_revision";

export interface SendMessageRequest {
  content: string;
  operationId: string;
  workflow?: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
  changeRequestScope?: EngineeringChangeRequestScope;
}

export interface SendMessageResponse {
  runId: string;
  operationId: string;
  state: RunState;
}

export interface EvidenceRef {
  evidenceId: string;
  title: string;
  kind:
    | "program_location"
    | "device_reference"
    | "runtime_observation"
    | "artifact"
    | "validation"
    | "knowledge";
  summary: string;
  location?: {
    vendor: string;
    project?: string;
    program?: string;
    network?: string;
  };
  confidence?: "confirmed" | "supported" | "hypothesis";
  knowledgeRef?: string;
  sourceAuthority?: string;
  sourceStatus?: string;
}

export type KnowledgeKind =
  | "manual"
  | "standard"
  | "project_document"
  | "memory"
  | "preference"
  | "control_pattern";

export type KnowledgeStatus =
  | "proposed"
  | "confirmed"
  | "superseded"
  | "archived";

export interface KnowledgeSource {
  sourceId: string;
  kind: KnowledgeKind;
  title: string;
  scope: string;
  vendor?: string;
  productFamily?: string;
  version?: string;
  language?: string;
  authority: string;
  status: KnowledgeStatus;
  sha256: string;
  locator?: string;
  revision: string;
  supersedes?: string;
  metadata: Record<string, unknown>;
  confirmedBy?: string;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeSourcePage {
  items: KnowledgeSource[];
  total: number;
}

export interface KnowledgeSearchHit {
  content: string;
  score: number;
  channels: string[];
  kind: KnowledgeKind;
  scope: string;
  vendor?: string;
  productFamily?: string;
  version?: string;
  language?: string;
  authority: string;
  status: KnowledgeStatus;
  citation: {
    knowledgeRef: string;
    sourceId: string;
    chunkId: string;
    title: string;
    locator?: string;
    page?: number;
    section?: string;
  };
  metadata: Record<string, unknown>;
}

export interface KnowledgeSearchResponse {
  query: string;
  retrievalMode: "word_gram_rrf";
  results: KnowledgeSearchHit[];
}

export interface KnowledgeSummary {
  sourceCount: number;
  chunkCount: number;
  manualCount: number;
  patternCount: number;
  confirmedMemoryCount: number;
  proposedMemoryCount: number;
  retrievalMode: "word_gram_rrf";
}

export interface ImportKnowledgeRequest {
  title?: string;
  kind: "manual" | "standard" | "project_document";
  scope: string;
  vendor: "ls" | "mitsubishi" | "rockwell" | "generic";
  productFamily?: string;
  version?: string;
  language: string;
  authority: "user_imported_reference";
}

export interface KnowledgeImportResult {
  selected: boolean;
  source?: KnowledgeSource;
  extractionStatus?: "ready" | "partial";
  pageCount?: number;
  indexedPageCount?: number;
  lowTextPages: number[];
}

export interface CreateKnowledgeMemoryRequest {
  title: string;
  kind: "memory" | "preference";
  scope: string;
  content: string;
  vendor?: "ls" | "mitsubishi" | "rockwell" | "generic";
  productFamily?: string;
  version?: string;
  language: string;
}

export interface ModelSettings {
  protocol: "openai_chat_completions";
  baseUrl: string;
  model: string;
  customHeaderNames: string[];
  timeoutSeconds: number;
  hasApiKey: boolean;
  externalEndpoint: boolean;
  updatedAt?: string;
}

export interface SaveModelSettingsRequest {
  protocol: "openai_chat_completions";
  baseUrl: string;
  model: string;
  apiKey?: string;
  customHeaders?: Record<string, string>;
  timeoutSeconds: number;
  externalModelConsent: boolean;
  insecureHttpConsent: boolean;
}

export interface ModelCapabilityResult {
  success: boolean;
  testedAt: string;
  capabilities: {
    completion: boolean;
    streaming: boolean;
    toolCalling: boolean;
    toolResultContinuation: boolean;
    structuredOutput: boolean;
  };
  message: string;
}

export interface XG5000Instance {
  pid: number;
  hwnd: number;
  title?: string;
  version?: string;
  [key: string]: unknown;
}

export interface EngineeringProgram {
  programId: string;
  name: string;
  language: string;
}

export interface EngineeringProgramsResponse {
  programs: EngineeringProgram[];
}

export interface EngineeringProgramFocusResponse {
  status: "focused";
  verified: true;
  readOnly: true;
  projectUnchanged: true;
  program: EngineeringProgram;
}

export interface XG5000InstancesResponse {
  status: "ready" | "unavailable";
  instances: XG5000Instance[];
}

export type NativeProjectState =
  | "allocated"
  | "executing"
  | "completed"
  | "failed"
  | "quarantined";

export interface CreateNativeProjectRequest {
  operationId: string;
  projectName: string;
  workspaceParent: string;
  plcSeries: string;
  cpuModel: string;
  programName: string;
  language: string;
  description: string;
  xg5000Pid?: number;
  xg5000Hwnd?: number;
  reviewedPlan: ReviewedProjectPlanBinding;
}

export interface NativeProjectResult {
  operation?: "project.create_native";
  projectPath?: string;
  projectSha256?: string;
  projectSize?: number;
  destinationDirectory?: string;
  planDigest?: string;
  xg5000Pid?: number;
  xg5000Hwnd?: number;
  identityVerified?: boolean;
  identityEvidence?: Record<string, unknown>;
  controllerConnected?: boolean;
  plcWritePerformed?: boolean;
  workspaceBound?: boolean;
  workspaceBindingReason?: string;
  workspaceRegistration?: {
    state: "captured_not_bound" | "capture_failed";
    detail: string;
    workspace?: LocalWorkspace;
  };
  recovery?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface NativeProjectOperation {
  nativeProjectId: string;
  operationId: string;
  state: NativeProjectState;
  requestDigest: string;
  actorId: string;
  request: CreateNativeProjectRequest;
  planDigest?: string;
  destinationDirectory: string;
  result?: NativeProjectResult;
  errorCode?: string;
  errorDetail?: string;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
}

export interface NativeProjectCreatedResponse {
  operation: NativeProjectOperation;
  created: boolean;
}

export interface ReconcileNativeProjectRequest {
  pid: number;
  hwnd: number;
  expectedProjectPath: string;
  expectedProjectSha256?: string;
}

export interface NativeProjectReconciliationResponse {
  operation: NativeProjectOperation;
  reconciled: boolean;
}

export interface OpenLocalWorkspaceRequest {
  sourcePath: string;
  expectedSourceHash?: string;
  openInXg5000: boolean;
  timeoutSeconds: number;
}

export interface ResumeLocalWorkspaceRequest {
  timeoutSeconds: number;
}

export interface XG5000ProjectFileSelection {
  selected: boolean;
  path?: string;
}

export interface LocalWorkspace {
  workspaceId: string;
  revisionId: string;
  sourceKind: "local_file";
  vendor: "ls";
  engineeringSystem: "XG5000";
  createdAt: string;
  source: {
    originalPath: string;
    capturedPath: string;
    sha256: string;
    originalState: "unchanged" | "changed" | "missing" | "unreadable";
    originalCurrentSha256?: string;
  };
  workingCopy: {
    path: string;
    baselineSha256: string;
    currentSha256: string;
    state: "clean" | "modified";
  };
}

export interface OpenLocalWorkspaceResponse {
  workspace: LocalWorkspace;
  launch?: {
    workspaceId: string;
    workingPath: string;
    workingHash: string;
    launchPid: number;
    windowDetected: boolean;
    projectIdentityVerifiedAgainstSelectedPid: boolean;
    instance?: XG5000Instance;
    detail: string;
  };
  session?: Record<string, unknown>;
}

export interface AttachXG5000Request {
  pid: number;
  hwnd: number;
  projectPath?: string;
  projectHash?: string;
}

export interface AttachXG5000Response {
  status: "attached";
  session: Record<string, unknown>;
}

export interface HostShutdownResponse {
  status?: "shutting_down";
  detail?: string;
}

export interface BrowserSessionResponse {
  sessionToken: string;
  expiresAt: string;
}

export interface CancelRunResponse {
  runId: string;
  state: RunState;
  terminal: boolean;
  idempotent?: boolean;
  detail?: string;
}

export type ChangeRiskLevel = "low" | "medium" | "high" | "critical";
export type ChangeVerificationScope = "offline_native_check_only";
export type ChangeMode = "offline_working_copy_revision";
export type ChangeRequestScope =
  | "offline_candidate_revision"
  | "permanent_offline_revision";
export type OfflineNativeValidationStep =
  | "structural_diff"
  | "xg5000_program_check"
  | "project_save";

export type ChangeCoverageArea =
  | "target_identity"
  | "value_semantics"
  | "direct_impact"
  | "indirect_impact"
  | "modes"
  | "runtime"
  | "external_integrations"
  | "safety"
  | "validation"
  | "rollback";

export type ChangeCoverageStatus =
  | "confirmed"
  | "not_applicable_with_reason"
  | "unknown_blocking";

export interface ChangeCoverageItem {
  area: ChangeCoverageArea;
  status: ChangeCoverageStatus;
  summary: string;
  evidenceRefs: string[];
}

export type ChangeTransactionState =
  | "pending_approval"
  | "approved"
  | "executing"
  | "verifying"
  | "completed"
  | "rolled_back"
  | "failed"
  | "quarantined"
  | "rejected"
  | "expired";

export interface ChangePlan {
  changeMode?: ChangeMode;
  requestScope?: ChangeRequestScope;
  target: Record<string, unknown>;
  current: Record<string, unknown>;
  proposed: Record<string, unknown>;
  impact: string[];
  risks: string[];
  assumptions?: string[];
  blockingUnknowns?: string[];
  alternatives?: string[];
  acceptanceCriteria?: string[];
  observationSignals?: string[];
  stopConditions?: string[];
  coverage?: ChangeCoverageItem[];
  verificationScope: ChangeVerificationScope;
  validation: OfflineNativeValidationStep[];
  manualFollowUp: string[];
  rollback: string[];
  [key: string]: unknown;
}

export interface ChangeTargetIdentity {
  workspaceId: string;
  revisionId: string;
  artifactHash: string;
  sourceArtifactHash: string;
  projectPath: string;
  vendor: string;
  engineeringSystem: string;
  engineeringSessionId?: string;
  engineeringProcessId?: number;
  engineeringWindowHandle?: number;
}

export interface ChangeApproval {
  decision: "approve" | "reject";
  planDigest: string;
  actorId: string;
  decidedAt: string;
}

export interface ChangeTransaction {
  transactionId: string;
  operationId: string;
  conversationId?: string;
  runId?: string;
  state: ChangeTransactionState;
  targetIdentity: ChangeTargetIdentity;
  mutationScope: string;
  riskLevel: ChangeRiskLevel;
  intent: string;
  plan: ChangePlan;
  planDigest: string;
  requestedByActorId: string;
  approvalExpiresAt: string;
  approval?: ChangeApproval;
  result: Record<string, unknown>;
  errorCode?: string;
  errorDetail?: string;
  createdAt: string;
  updatedAt: string;
  executionStartedAt?: string;
  verificationStartedAt?: string;
  completedAt?: string;
  receiptAcknowledgedAt?: string | null;
  receiptAcknowledgedBy?: string | null;
}

export interface CreateChangeTransactionRequest {
  operationId: string;
  conversationId?: string;
  runId?: string;
  intent: string;
  riskLevel: ChangeRiskLevel;
  plan: ChangePlan;
  approvalTtlSeconds: number;
}

export interface DecideChangeTransactionRequest {
  decision: "approve" | "reject";
  expectedPlanDigest: string;
}

export interface ExecuteChangeTransactionRequest {
  expectedPlanDigest: string;
}

export interface ReconcileChangeTransactionRequest {
  expectedPlanDigest: string;
  pid: number;
  hwnd: number;
}

export interface ChangeTransactionCreatedResponse {
  transaction: ChangeTransaction;
  created: boolean;
}

export interface ChangeDecisionResponse {
  transaction: ChangeTransaction;
  decisionRecorded: boolean;
}

export interface ChangeExecutionResponse {
  transaction: ChangeTransaction;
  executionStarted: boolean;
}

export interface ChangeReconciliationResponse {
  transaction: ChangeTransaction;
  reconciled: boolean;
}

export interface ChangeReceiptAcknowledgementResponse {
  transaction: ChangeTransaction;
  acknowledgementRecorded: boolean;
}

export type HostEventType =
  | "conversation.created"
  | "message.appended"
  | "host.status"
  | "run.created"
  | "run.state"
  | "run.state_changed"
  | "agent.token"
  | "agent.message"
  | "tool.started"
  | "tool.progress"
  | "tool.completed"
  | "tool.failed"
  | "evidence.added"
  | "approval.requested"
  | "approval.resolved"
  | "change.transaction.updated"
  | "change.receipt_acknowledged"
  | "controller.connected"
  | "controller.observed"
  | "controller.devices_read"
  | "controller.disconnected"
  | "knowledge.source_imported"
  | "knowledge.memory_proposed"
  | "knowledge.memory_confirmed"
  | "operation.quarantined";

export interface HostEventEnvelope<TPayload = Record<string, unknown>> {
  eventId: string;
  sequence: number;
  occurredAt: string;
  conversationId?: string;
  runId?: string;
  eventType: HostEventType;
  payloadSchemaVersion: "1";
  payload: TPayload;
}

export interface ProblemDetails {
  title?: string;
  status?: number;
  detail?: string | Array<Record<string, unknown>>;
  code?: string;
}

export type EngineeringPlanStatus = "needs_clarification" | "ready_for_review";

export type EngineeringPlanItemStatus =
  | "confirmed"
  | "proposed"
  | "unknown"
  | "conflicting"
  | "not_applicable";

export type EngineeringPlanItemSource = "user" | "agent";

export interface EngineeringPlanItem {
  itemId: string;
  text: string;
  status: EngineeringPlanItemStatus;
  source: EngineeringPlanItemSource;
  blocking: boolean;
  verificationMethod?: string;
  knowledgeRefs?: string[];
}

export interface EngineeringPlanSection {
  sectionId: string;
  title: string;
  items: EngineeringPlanItem[];
}

export interface EngineeringPlanCompleteness {
  totalItems: number;
  confirmed: number;
  proposed: number;
  unknown: number;
  conflicting: number;
  notApplicable: number;
  blocking: number;
}

export interface EngineeringPlanReview {
  messageId: string;
  planDigest: string;
  reviewedSectionIds: string[];
  actorId: string;
  reviewedAt: string;
}

export interface EngineeringPlanOpenDecision {
  decisionId: string;
  question: string;
  status: "unknown";
  source: "agent";
  blocking: true;
  verificationMethod?: string;
}

/**
 * Server-owned planning artifact for a new PLC project.
 *
 * The artifact keeps facts, proposals, unknowns, and conflicts distinct so
 * plan review cannot silently turn an agent assumption into a requirement.
 */
export interface NewProjectEngineeringPlan {
  schemaVersion: "1";
  projectName: string;
  status: EngineeringPlanStatus;
  summary: string;
  objective: string;
  sections: EngineeringPlanSection[];
  openDecisions: EngineeringPlanOpenDecision[];
  completeness: EngineeringPlanCompleteness;
}

export interface ProjectPlanReviewRequest {
  messageId: string;
  planDigest: string;
  reviewedSectionIds: string[];
}

export interface ProjectPlanReviewResponse {
  review: EngineeringPlanReview;
  reviewRecorded: boolean;
}

export interface ReviewedProjectPlanBinding {
  conversationId: string;
  messageId: string;
  planDigest: string;
  reviewedSectionIds: string[];
}
