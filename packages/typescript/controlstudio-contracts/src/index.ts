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
  windowHandle?: number;
  processCreateTime?: number;
  sessionId?: string;
  projectPath?: string;
  projectHash?: string;
  targetWorkspaceId?: string;
  targetRevisionId?: string;
  sessionVerified: boolean;
  readReady?: boolean;
  offlineCandidateEligible?: boolean;
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
  conversationId?: string;
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
  evidenceId?: string;
}

export interface TraceLSControllerRequest extends ReadLSControllerRequest {
  sampleCount: number;
  intervalMs: number;
}

export interface LSControllerTraceSample {
  index: number;
  observedAt: string;
  offsetMs: number;
  values: LSControllerValue[];
}

export interface LSControllerTraceSummary {
  variable: string;
  firstUnsignedValue: number;
  lastUnsignedValue: number;
  minimumUnsignedValue: number;
  maximumUnsignedValue: number;
  changeCount: number;
  changed: boolean;
}

export interface LSControllerTraceTransition {
  variable: string;
  fromSample: number;
  toSample: number;
  observedAt: string;
  fromRawHex: string;
  toRawHex: string;
  fromUnsignedValue: number;
  toUnsignedValue: number;
}

export interface LSControllerTrace {
  schemaVersion: string;
  operation: "controller.ls.trace";
  endpoint: LSControllerEndpoint;
  startedAt: string;
  completedAt: string;
  dataType: LSControllerDataType;
  variables: string[];
  sampleCount: number;
  intervalMs: number;
  captureDurationMs: number;
  statusBefore: LSControllerRuntimeStatus;
  statusAfter: LSControllerRuntimeStatus;
  identityBefore: LSControllerProvisionalIdentity;
  identityAfter: LSControllerProvisionalIdentity;
  identityStable: true;
  modeStable: boolean;
  samples: LSControllerTraceSample[];
  summaries: LSControllerTraceSummary[];
  transitionCount: number;
  transitionReturnedCount: number;
  transitionsTruncated: boolean;
  transitions: LSControllerTraceTransition[];
  limitations: string[];
  readOnly: true;
  strongEnoughForWrite: false;
  evidenceId?: string;
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
  | "full_program_replacement"
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
    | "not_verified";
  currentReady: boolean;
  detail: string;
  capabilityStatus?:
    | "missing"
    | "valid"
    | "expired"
    | "runtime_mismatch"
    | "runtime_checked_at_plan";
}

export interface HostStatusResponse {
  schemaVersion: "1";
  releaseChannel?: "development" | "beta" | "production";
  productRelease?: string;
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
    state?: RunState | NativeProjectState | ChangeTransactionState;
    simulationTrialState?: NativeSimulationTrialState;
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

export type EngineeringConfidence = "low" | "medium" | "high";

export interface EngineeringObservation {
  statement: string;
  evidenceRefs: string[];
  factKind?: "location" | "relation" | "project_structure";
  factFingerprint?: string;
}

export interface EngineeringInference {
  statement: string;
  confidence: EngineeringConfidence;
  evidenceRefs: string[];
}

export interface EngineeringHypothesis {
  hypothesis: string;
  confidence: EngineeringConfidence;
  supportingEvidenceRefs: string[];
  counterevidence: string[];
  verificationSteps: string[];
}

export type EngineeringHypothesisVerification =
  | "open"
  | "verified"
  | "rejected";

export interface EngineeringHypothesisV4Open extends EngineeringHypothesis {
  verification: "open";
  verificationEvidenceRefs: [];
}

export interface EngineeringHypothesisV4Terminal extends EngineeringHypothesis {
  verification: "verified" | "rejected";
  outcome: string;
  verificationEvidenceRefs: string[];
}

export type EngineeringHypothesisV4 =
  | EngineeringHypothesisV4Open
  | EngineeringHypothesisV4Terminal;

export interface EngineeringAlternative {
  option: string;
  expectedImpact: string[];
  risks: string[];
  requiredChanges?: string[];
  verificationSteps: string[];
  rollback: string[];
  evidenceRefs?: string[];
}

export interface EngineeringUnknown {
  statement: string;
  blocking: boolean;
  verificationStep: string;
}

export type EngineeringLocationAccess =
  | "read"
  | "write"
  | "state_write"
  | "condition"
  | "action"
  | "argument"
  | "unknown";

export type EngineeringLocationSemanticStatus =
  | "observed"
  | "structured"
  | "partial";

export interface EngineeringLocation {
  evidenceRef: string;
  program: string;
  networkId?: string;
  networkIndex?: number;
  rungId?: string;
  visualRow?: number;
  tokenOffset?: number;
  device?: string;
  access: EngineeringLocationAccess;
  semanticStatus: EngineeringLocationSemanticStatus;
}

export type EngineeringRelationKind =
  | "read"
  | "write"
  | "state_write"
  | "condition"
  | "causal_path"
  | "state_transition";

export interface EngineeringRelation {
  evidenceRef: string;
  relationKind: EngineeringRelationKind;
  source: string;
  target: string;
  deviceChain: string[];
  direct: boolean;
  semanticStatus: "structured" | "partial";
  locations: EngineeringLocation[];
}

export type EngineeringCoverageArea =
  | "programs"
  | "logic_graph"
  | "cross_references"
  | "state_model"
  | "causal_paths"
  | "hardware"
  | "runtime"
  | "external_integrations"
  | "project_structure";

export type EngineeringCoverageStatus =
  | "complete"
  | "partial"
  | "not_observed"
  | "not_applicable";

export interface EngineeringCoverage {
  area: EngineeringCoverageArea;
  status: EngineeringCoverageStatus;
  observedCount?: number;
  totalCount?: number;
  truncated: boolean;
  limitations: string[];
  evidenceRefs: string[];
}

export type EngineeringProjectStructureKind =
  | "configuration"
  | "task"
  | "code_unit"
  | "symbol"
  | "device"
  | "call_edge"
  | "parameter"
  | "module"
  | "communication";

export interface EngineeringProjectStructureAttribute {
  name: string;
  value: string | number | boolean;
}

export interface EngineeringProjectStructureFact {
  evidenceRef: string;
  kind: EngineeringProjectStructureKind;
  entityId: string;
  name?: string;
  parentId?: string;
  order?: number;
  status: "observed" | "partial";
  attributes: EngineeringProjectStructureAttribute[];
}

interface EngineeringAnalysisArtifactBase<Hypothesis = EngineeringHypothesis> {
  observations: EngineeringObservation[];
  inferences: EngineeringInference[];
  hypotheses: Hypothesis[];
  alternatives: EngineeringAlternative[];
  locations?: EngineeringLocation[];
  relations?: EngineeringRelation[];
  coverage?: EngineeringCoverage[];
  projectStructure?: EngineeringProjectStructureFact[];
  unknowns: EngineeringUnknown[];
  nextActions: string[];
  evidenceRefs: string[];
}

export interface EngineeringAnalysisArtifactLegacy
  extends EngineeringAnalysisArtifactBase {
  schemaVersion: "1" | "2" | "3";
}

export interface EngineeringAnalysisArtifactV4
  extends EngineeringAnalysisArtifactBase<EngineeringHypothesisV4> {
  schemaVersion: "4";
}

export type EngineeringAnalysisArtifact =
  | EngineeringAnalysisArtifactLegacy
  | EngineeringAnalysisArtifactV4;

export type EngineeringMissionStatus =
  | "active"
  | "waiting_for_user"
  | "waiting_for_approval"
  | "completed"
  | "blocked";

export type EngineeringMissionStepStatus =
  | "pending"
  | "active"
  | "completed"
  | "blocked";

export type EngineeringMissionStopReason =
  | "continuing"
  | "objective_achieved"
  | "needs_user_input"
  | "needs_human_approval"
  | "blocked_by_missing_capability"
  | "cycle_budget_exhausted";

export interface EngineeringMissionStep {
  stepId: string;
  title: string;
  status: EngineeringMissionStepStatus;
  outcome?: string;
  evidenceRefs: string[];
}

export interface EngineeringMissionSnapshot {
  schemaVersion: "1";
  objective: string;
  status: EngineeringMissionStatus;
  completionCriteria: string[];
  steps: EngineeringMissionStep[];
  activeStepId?: string;
  blockingQuestions: string[];
  nextAction?: string;
  stopReason: EngineeringMissionStopReason;
}

export interface EngineeringMission {
  schemaVersion: "1";
  conversationId: string;
  workspaceId: string;
  revisionId: string;
  revision: number;
  snapshot: EngineeringMissionSnapshot;
  digest: string;
  updatedByRunId: string;
  changeTransactionId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface EngineeringMissionSummary {
  schemaVersion: "1";
  conversationId: string;
  workspaceId: string;
  revisionId: string;
  revision: number;
  status: EngineeringMissionStatus;
  digest: string;
  updatedByRunId: string;
  changeTransactionId?: string | null;
  updatedAt: string;
}

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
  planRevision?: EngineeringPlanRevision;
  engineeringAnalysis?: EngineeringAnalysisArtifact;
  engineeringAnalysisDigest?: string;
}

export interface Conversation {
  conversationId: string;
  title: string;
  messages: ConversationMessage[];
  updatedAt: string;
  workflow: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
  engineeringTarget?: EngineeringTargetBinding | null;
  engineeringHandoff?: EngineeringConversationHandoff | null;
  engineeringMission?: EngineeringMission | null;
}

export interface EngineeringTargetBinding {
  workspaceId: string;
  revisionId: string;
  engineeringSessionId: string;
}

export interface EngineeringConversationHandoff {
  schemaVersion: "1";
  nativeProjectId: string;
  planningConversationId: string;
  planningMessageId: string;
  reviewedPlanDigest: string;
  projectBriefDigest: string;
  attachmentRevisionDigest: string;
  buildBundleId: string;
  buildReceiptSchemaVersion: "controlstudio-native-build-receipt/v3";
  buildReceiptDigest: string;
  reopenReceiptSchemaVersion: "controlstudio-native-reopen-receipt/v2";
  reopenReceiptDigest: string;
  projectSha256: string;
}

export interface ConversationSummary {
  conversationId: string;
  title: string;
  workflow: "engineering" | "new_project_planning";
  engineeringTarget?: EngineeringTargetBinding | null;
  engineeringHandoff?: EngineeringConversationHandoff | null;
  engineeringMission?: EngineeringMissionSummary | null;
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
  attachmentSourceIds: string[];
}

export interface ConversationCreate {
  title?: string;
  workflow?: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
  nativeProjectId?: string;
}

export type EngineeringChangeRequestScope =
  | "analysis_only"
  | "offline_candidate_revision"
  | "permanent_offline_revision";

interface SendMessageRequestBase {
  content: string;
  operationId: string;
  workflow?: "engineering" | "new_project_planning";
  projectBrief?: ProjectBrief;
}

/**
 * A message carries one explicit authority boundary. Permanent work-copy
 * mutation is check/save-only; reopen and XG-SIM remain candidate workflows.
 */
export type SendMessageRequest = SendMessageRequestBase &
  (
    | {
        changeRequestScope?: "analysis_only";
        verificationScope?: "offline_native_reopen";
      }
    | {
        changeRequestScope: "offline_candidate_revision";
        verificationScope?:
          | "offline_native_reopen"
          | "offline_native_reopen_xgsim";
      }
    | {
        changeRequestScope: "permanent_offline_revision";
        verificationScope?: "offline_native_check_only";
      }
  );

export interface SendMessageResponse {
  runId: string;
  operationId: string;
  state: RunState;
}

export interface ResumeEngineeringMissionRequest {
  operationId: string;
}

export interface ReverifyBlockedEngineeringMissionStepRequest {
  operationId: string;
}

export interface EvidenceRef {
  evidenceId: string;
  requestId?: string;
  toolCallId?: string;
  toolName?: string;
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
  process_create_time?: number | null;
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

export interface EngineeringLocationFocusRequest {
  location: EngineeringLocation;
}

export interface EngineeringLocationFocusResponse {
  status: "focused";
  verified: true;
  readOnly: true;
  projectUnchanged: true;
  program: EngineeringProgram;
  location: EngineeringLocation;
  payloadSha256: string;
}

export interface MneAnchorFocus {
  instructionIndex: number;
  command: MneProgramReplacementCommand;
  operand: string | null;
  instructionDigest: string;
  baselineMneSha256: string;
  baselineLdSha256: string;
  baselineLdCanonicalSha256?: string;
  baselineInstructionCount: number;
}

export interface ChangeTargetFocusResponse {
  status: "focused";
  verified: true;
  readOnly: true;
  projectUnchanged: true;
  transactionId: string;
  planDigest: string;
  focusKind: "native_location" | "mne_anchor" | "program";
  program: EngineeringProgram;
  location?: EngineeringLocation | null;
  payloadSha256?: string | null;
  mneAnchor?: MneAnchorFocus | null;
}

export interface XG5000InstancesResponse {
  status: "ready" | "unavailable";
  instances: XG5000Instance[];
}

export interface ControllerRestorePreflightRequest {
  pid: number;
  hwnd: number;
}

export interface ControllerRestoreBeginRequest extends ControllerRestorePreflightRequest {
  operationId: string;
  timeoutSeconds: number;
}

export interface ControllerRestoreCompleteRequest {
  operationId: string;
  timeoutSeconds: number;
}

export interface ControllerRestoreMenuResolution {
  requestedPath: string[];
  actualPath: string[];
  commandId: number;
  enabled: boolean;
}

export interface ControllerRestorePreflight {
  schema: "controlstudio-controller-restore-preflight/v1";
  observedAt: string;
  target: {
    pid: number;
    hwnd: number;
    processCreateTime: number;
    title: string;
    className: string;
    executable: {
      sha256: string;
      fileVersion: string;
      productVersion: string;
      peMachine: string;
    };
  };
  surfaceProfile: {
    schema: "controlstudio-controller-restore-surface-profile/v1";
    executable: ControllerRestorePreflight["target"]["executable"];
    openFromPlc: ControllerRestoreMenuResolution | null;
    saveAs: ControllerRestoreMenuResolution | null;
    profileDigest: string;
  };
  visibleDialogCount: number;
  ready: boolean;
  blockers: Array<
    | "open_from_plc_menu_unresolved"
    | "open_from_plc_menu_disabled"
    | "save_as_menu_unresolved"
    | "save_as_menu_disabled"
    | "modal_dialog_present"
  >;
  readOnly: true;
  invoked: false;
  limitations: string[];
  receiptDigest: string;
}

export interface ControllerRestoreHandoff {
  schema: "controlstudio-controller-restore-handoff/v1";
  operationId: string;
  startedAt: string;
  expiresAt: string;
  target: ControllerRestorePreflight["target"];
  preflightReceiptDigest: string;
  profileDigest: string;
  dialog: { hwnd: number; title: string; className: string };
  invoked: true;
  confirmed: false;
  receiptDigest: string;
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
  plcSeries: "XGK" | "XGB";
  cpuModel: string;
  programName: string;
  language: "LD";
  description: string;
  xg5000Pid: number;
  xg5000Hwnd: number;
  reviewedPlan: ReviewedProjectPlanBinding;
}

export interface NativeBuildBundle {
  schemaVersion: "controlstudio-native-build-bundle/v2";
  bundleId: string;
  phase: "engineered_control_program";
  reviewedPlan: ReviewedProjectPlanBinding;
  engineeringPlan: NewProjectEngineeringPlan;
  technicalConfiguration: {
    projectName: string;
    plcSeries: "XGK" | "XGB";
    cpuModel: string;
    programName: string;
    language: "LD";
    description: string;
  };
  compiledProgram: {
    schemaVersion: "controlstudio-xg5000-mnemonic-program/v4";
    sourceLanguage: "XG5000_MNEMONIC";
    targetLanguage: "LD";
    programName: string;
    source: string;
    sourceSha256: string;
    rungIds: string[];
    ladderIr: {
      schemaVersion: "controlstudio-ld-rung-ir/v1";
      rungs: EngineeringPlanLadderRung[];
    };
    ladderIrSha256: string;
    networkIds: string[];
    outputSignalIds: string[];
    timerIds: string[];
    sequenceIds: string[];
    sequenceRestartPolicies: Record<
      string,
      "require_start_release" | "continuous"
    >;
    stepIds: string[];
    alarmIds: string[];
    recoveryIds: string[];
    collectionScope: CompiledEngineeringPlanNativeCollectionScope;
  };
}

export interface NativeBuildBundleReceipt {
  schemaVersion: "controlstudio-native-build-receipt/v3";
  bundleId: string;
  phase: "engineered_control_program";
  status: "APPLIED_VERIFIED";
  reviewedPlanDigest: string;
  nativeReadback: {
    projectName: string;
    projectPath: string;
    projectSha256: string;
    projectSize: number;
    destinationDirectory: string;
    plcSeries: "XGK" | "XGB";
    cpuModel: string;
    programName: string;
    language: "LD";
    description: string;
    identityVerified: true;
  };
  programReadback: {
    rungCount: number;
    rungIds: string[];
    ladderIrSha256: string;
    sourceSha256: string;
    exactInstructionReadback: string[];
    finalView: "LD";
    programCheckPassed: true;
    savedProjectSha256: string;
  };
}

export interface NativeReopenReceipt {
  schemaVersion: "controlstudio-native-reopen-receipt/v2";
  status: "REOPEN_VERIFIED";
  buildBundleId: string;
  reviewedPlanDigest: string;
  sourceProcess: {
    pid: number;
  };
  reopenedProcess: {
    pid: number;
    hwnd: number;
    independent: true;
  };
  projectReadback: {
    projectPath: string;
    projectSha256: string;
    projectUnchanged: true;
    identityAssurance: "controlled_launch_read_only";
  };
  programReadback: {
    programName: string;
    language: "LD";
    sourceSha256: string;
    rungCount: number;
    rungIds: string[];
    ladderIrSha256: string;
    exactInstructionReadback: string[];
    finalView: "LD";
    programCheckPassed: true;
  };
  controllerConnected: false;
  plcWritePerformed: false;
}

export interface NativeSimulationScenario {
  scenarioId: string;
  status: "passed";
  assertions: Record<string, boolean>;
  [key: string]: unknown;
}

export interface NativeSimulationMoveObservation {
  address: string;
  byteOffset: number;
  encoding: "little_endian_signed16";
  beforeValue: number;
  expectedValue: number;
  observedValue: number;
  matched: true;
}

export interface NativeSimulationMoveScenario extends NativeSimulationScenario {
  scenarioId: `move_ladder:${string}`;
  action: EngineeringPlanLadderMoveAction;
  inputAssignment: Record<string, 0 | 1>;
  observation: NativeSimulationMoveObservation;
}

export interface NativeSimulationNetworkRungCoverage {
  networkId: string;
  outputSignalId: string;
  rungId: string;
  conditionKind: "contact" | "all_of" | "any_of";
  status: "verified";
}

export interface NativeSimulationMoveRungCoverage {
  rungId: string;
  conditionKind: "contact" | "all_of" | "any_of";
  action: EngineeringPlanLadderMoveAction;
  inputAssignment: Record<string, 0 | 1>;
  status: "verified";
}

export interface NativeSimulationScenarioCoverageBase {
  collectionScope: Record<
    keyof EngineeringPlanNativeCollectionScope,
    "confirmed" | "not_applicable"
  >;
  inputSignalIds: string[];
  modeIds: string[];
  networkIds: string[];
  outputSignalIds: string[];
  timerIds: string[];
  sequenceIds: string[];
  sequenceRestartPolicies: Record<string, "require_start_release" | "continuous">;
  stepIds: string[];
  alarmIds: string[];
  recoveryIds: string[];
  processInterlockIds: string[];
  documentedExternalSafetyBoundaryIds: string[];
}

export interface NativeSimulationScenarioReceiptBase {
  status: "SCENARIOS_VERIFIED";
  verifiedAt: string;
  scenarioCount: number;
  scenarios: Array<NativeSimulationScenario | NativeSimulationMoveScenario>;
  addressMap: Record<string, unknown>;
  inputWrites: Array<Record<string, unknown>>;
  outputBytesWritten: false;
}

export interface NativeSimulationScenarioReceiptV1
  extends NativeSimulationScenarioReceiptBase {
  schemaVersion: "controlstudio-native-simulation-scenarios/v1";
  coverage: NativeSimulationScenarioCoverageBase & {
    selectedNetworkRung: {
      rungId: string;
      conditionKind: "contact" | "all_of" | "any_of";
      status: "verified";
    };
  };
}

export interface NativeSimulationScenarioReceiptV2
  extends NativeSimulationScenarioReceiptBase {
  schemaVersion: "controlstudio-native-simulation-scenarios/v2";
  coverage: NativeSimulationScenarioCoverageBase & {
    networkRungs: NativeSimulationNetworkRungCoverage[];
  };
}

export interface NativeSimulationScenarioReceiptV3
  extends NativeSimulationScenarioReceiptBase {
  schemaVersion: "controlstudio-native-simulation-scenarios/v3";
  coverage: NativeSimulationScenarioCoverageBase & {
    networkRungs: NativeSimulationNetworkRungCoverage[];
    moveRungs: NativeSimulationMoveRungCoverage[];
  };
}

export interface NativeSimulationReceiptBase {
  status: "SIMULATION_VERIFIED";
  buildBundleId: string;
  reviewedPlanDigest: string;
  projectReadback: {
    projectPath: string;
    projectSha256: string;
    reopenedProjectSha256: string;
    projectUnchangedDuringRuntime: true;
    identityAssurance: "controlled_launch_read_only";
  };
  simulationPreparation: {
    hashBeforeSimulatorStart: string;
    hashAfterSimulatorStart: string;
    hashAfterProgramWrite: string;
    hashTransitionObserved: boolean;
    programReadback: {
      programName: string;
      language: "LD";
      sourceSha256: string;
      rungCount: number;
      rungIds: string[];
      ladderIrSha256: string;
      exactInstructionReadback: string[];
      finalView: "LD";
      programCheckPassed: true;
    };
  };
  programBinding: {
    programName: string;
    sourceSha256: string;
    rungCount: number;
    rungIds: string[];
    ladderIrSha256: string;
    compiledProgramSchema: "controlstudio-xg5000-mnemonic-program/v4";
  };
  simulator: {
    product: "XG-SIM";
    bridge: "XimUtil.DeviceInterface";
    programWritePerformed: true;
    cpuRunVerified: true;
    cpuStopped: true;
    start: Record<string, unknown>;
    write: Record<string, unknown>;
    run: Record<string, unknown>;
    stop: {
      ok: true;
      verified: true;
      confirmation: {
        closed: true;
        [key: string]: unknown;
      };
      postcondition: {
        main_enabled: true;
        ui_state: {
          verified: true;
          method: "xg5000-simulator-status-pane/v1";
          classification: "STOP";
          pid: number;
          hwnd: number;
          foreground_pid: number;
          foreground_hwnd: number;
          dpi: number;
          main_rect: [number, number, number, number];
          status_candidate_count: 1;
          status_text: "L, SIMULATOR, 정상" | "L, SIMULATOR, NORMAL";
          status_rect: [number, number, number, number];
          status_relative_rect: [number, number, number, number];
          status_bottom_gap: number;
          geometry_verified: true;
          capture_method: "PIL.ImageGrab/foreground-status-pane";
          consecutive_count: 3;
          selected_run: {
            text: string;
            visible: true;
            enabled: true;
            rect: [number, number, number, number];
          };
          selected_stop: {
            text: string;
            visible: true;
            enabled: true;
            rect: [number, number, number, number];
          };
          samples: Array<{
            index: number;
            classification: "STOP";
            pixel_count: number;
            dominant_rgb: [number, number, number];
            dominant_share: number;
            stop_share: number;
            run_share: number;
            offline_share: number;
            stop_prototype_distance: number;
          }>;
          [key: string]: unknown;
        };
      };
      [key: string]: unknown;
    };
  };
  baseline: Record<string, unknown>;
  inputWriteBoundary: {
    onlyReviewedSimulationStimuli: true;
    outputBytesWritten: false;
    baselineRestoredAfterCpuStop: true;
    restore: {
      attempted: true;
      cpuStoppedBeforeRestore: true;
      matched: true;
      [key: string]: unknown;
    };
  };
  controllerConnected: false;
  plcWritePerformed: false;
}

export interface NativeSimulationReceiptV3 extends NativeSimulationReceiptBase {
  schemaVersion: "controlstudio-native-simulation-receipt/v3";
  scenarioReceipt: NativeSimulationScenarioReceiptV1;
}

export interface NativeSimulationReceiptV4 extends NativeSimulationReceiptBase {
  schemaVersion: "controlstudio-native-simulation-receipt/v4";
  scenarioReceipt: NativeSimulationScenarioReceiptV2;
}

export interface NativeSimulationReceiptV5 extends NativeSimulationReceiptBase {
  schemaVersion: "controlstudio-native-simulation-receipt/v5";
  scenarioReceipt: NativeSimulationScenarioReceiptV3;
}

export type NativeSimulationReceipt =
  | NativeSimulationReceiptV3
  | NativeSimulationReceiptV4
  | NativeSimulationReceiptV5;

export type NativeSimulationTrialState =
  | "OBSERVING"
  | "ACCEPTED"
  | "REVERTED"
  | "QUARANTINED"
  | "QUARANTINE_ACKNOWLEDGED";

export interface NativeSimulationTrial {
  schemaVersion: "controlstudio-native-simulation-trial/v1";
  trialId: string;
  state: NativeSimulationTrialState;
  receiptDigest?: string;
  observedAt?: string;
  quarantinedAt?: string;
  safeRestorationVerified: boolean;
  decision?: {
    outcome: "ACCEPTED" | "REVERTED" | "QUARANTINE_ACKNOWLEDGED";
    actorId: string;
    decidedAt: string;
    receiptDigest?: string;
    safeRestorationVerified?: boolean;
    migratedFrom?: "pre-trial-lifecycle";
  } | null;
}

export interface NativeProjectResult {
  operation?: "project.create_native";
  projectPath?: string;
  projectSha256?: string;
  projectSize?: number;
  destinationDirectory?: string;
  planDigest?: string;
  buildBundleId?: string;
  reviewedPlanDigest?: string;
  buildBundleReceipt?: NativeBuildBundleReceipt;
  xg5000Pid?: number;
  xg5000Hwnd?: number;
  identityVerified?: boolean;
  identityEvidence?: Record<string, unknown>;
  controllerConnected?: boolean;
  plcWritePerformed?: boolean;
  workspaceBound?: boolean;
  engineeredProgramReady?: boolean;
  workspaceBindingReason?: string;
  workspaceRegistration?: {
    state: "captured_and_bound" | "captured_not_bound" | "capture_failed";
    detail: string;
    workspace?: LocalWorkspace;
  };
  reopenVerification?: {
    state: "verified" | "not_verified";
    detail: string;
    receipt?: NativeReopenReceipt;
    launch?: Record<string, unknown>;
    workspace?: LocalWorkspace;
    session?: Record<string, unknown>;
  };
  simulationVerification?: {
    state: "verified" | "not_verified" | "quarantined";
    detail: string;
    receipt?: NativeSimulationReceipt;
    session?: Record<string, unknown>;
    trial?: NativeSimulationTrial;
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
  request: CreateNativeProjectRequest & {
    destinationDirectory: string;
    buildBundle: NativeBuildBundle;
  };
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

export interface NativeSimulationTrialDecisionRequest {
  decision: "accept" | "revert" | "acknowledge_quarantine";
  expectedReceiptDigest?: string;
}

export interface NativeSimulationTrialDecisionResponse {
  operation: NativeProjectOperation;
  decisionRecorded: boolean;
}

export interface ReconcileNativeProjectRequest {
  pid: number;
  hwnd: number;
  expectedProjectPath: string;
  observedProjectPath?: string;
  expectedProjectSha256?: string;
}

export interface NativeProjectReconciliationResponse {
  operation: NativeProjectOperation;
  reconciled: boolean;
}

export interface ResumeNativeProjectRequest {
  pid: number;
  hwnd: number;
  expectedRequestDigest: string;
  reopenedPid?: number;
  reopenedHwnd?: number;
}

export interface NativeProjectResumedResponse {
  operation: NativeProjectOperation;
  resumed: boolean;
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
  sourceKind: "local_file" | "restored_from_controller" | "new_build";
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
  provenance?:
    | {
        schema: "controlstudio-controller-restore-workspace-provenance/v1";
        operationId: string;
        handoffReceiptDigest: string;
        sourcePid: number;
        sourceHwnd: number;
        savedProjectSha256: string;
        receiptDigest: string;
      }
    | {
        schema: "controlstudio-new-build-workspace-provenance/v1";
        nativeProjectId: string;
        reviewedPlanDigest: string;
        buildBundleId: string;
        buildReceiptDigest: string;
        savedProjectSha256: string;
        receiptDigest: string;
      };
}

export interface WorkspaceCompareDigestItem {
  id: string;
  digest: string;
}

export interface WorkspaceCompareChangedItem {
  id: string;
  sourceDigest: string;
  workingDigest: string;
}

export interface WorkspaceCompareSection {
  sourceCount: number;
  workingCount: number;
  addedCount: number;
  removedCount: number;
  changedCount: number;
  added: WorkspaceCompareDigestItem[];
  removed: WorkspaceCompareDigestItem[];
  changed: WorkspaceCompareChangedItem[];
  detailsTruncated: boolean;
}

export interface LocalWorkspaceCompare {
  schema: "xg5000.workspace-compare/1.0";
  ok: true;
  verified: true;
  workspaceId: string;
  revisionId: string;
  sourceKind: "local_file" | "restored_from_controller" | "new_build";
  source: { sha256: string; size: number };
  workingCopy: { sha256: string; size: number };
  fileIdentical: boolean;
  status:
    | "identical"
    | "file_changed"
    | "file_changed_structure_partial"
    | "file_changed_structure_changed"
    | "file_changed_structure_same";
  structure: {
    status: "same" | "changed" | "partial";
    sourceIndexDigest: string;
    workingIndexDigest: string;
    sections: Record<string, WorkspaceCompareSection>;
  } | null;
  limitations: Array<{ code: string; detail: string }>;
  compareDigest: string;
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

export interface ControllerRestoreCompleted extends OpenLocalWorkspaceResponse {
  operationId: string;
  restorationReceiptDigest: string;
  savedProjectSha256: string;
}

export interface AttachXG5000Request {
  pid: number;
  hwnd: number;
  projectPath?: string;
  projectHash?: string;
  /** Attach only if the selected IDE can prove its own project identity. */
  allowUnboundExistingTarget?: boolean;
}

export interface AttachXG5000Response {
  status: "attached";
  session: Record<string, unknown>;
}

export interface PrepareOfflineCandidateSessionRequest {
  conversationId: string;
}

export interface PrepareOfflineCandidateSessionResponse {
  status: "offline_candidate_bound";
  session: Record<string, unknown>;
}

export interface HostShutdownResponse {
  status?: "shutting_down";
  detail?: string;
}

export interface ReviewedNoSaveCloseRequest {
  sessionId?: string | null;
  pid: number;
  hwnd: number;
  processCreateTime: number;
  projectPath?: string | null;
  projectHash?: string | null;
  confirmNoSave: true;
  timeoutSeconds?: number;
}

export interface ReviewedNoSaveCloseResponse {
  operation: "xg5000.reviewed_no_save_close";
  verified: true;
  sourceClosed: true;
  automaticWorkspaceOpen: false;
  processTerminated: false;
  reviewedBinding: {
    sessionId?: string | null;
    pid: number;
    hwnd: number;
    processCreateTime: number;
    projectPath?: string | null;
    projectHash?: string | null;
  };
  close: Record<string, unknown>;
  receiptDigest: string;
  nextStep: "explicit_user_confirmed_workspace_open_required";
}

export interface HostRestartResponse {
  status: "scheduled" | "ready";
  restartReceipt: string;
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
export type ChangeVerificationScope =
  | "offline_native_check_only"
  | "offline_native_reopen"
  | "offline_native_reopen_xgsim";
export type ChangeMode = "offline_working_copy_revision";
export type ChangeRequestScope =
  | "offline_candidate_revision"
  | "permanent_offline_revision";
export type OfflineNativeValidationStep =
  | "structural_diff"
  | "xg5000_program_check"
  | "project_save"
  | "project_reopen";
export type MneFullProgramValidationStep =
  | "structural_diff"
  | "xg5000_program_check"
  | "project_save"
  | "project_reopen";

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
  | "observing"
  | "completed"
  | "rolled_back"
  | "failed"
  | "quarantined"
  | "rejected"
  | "expired";

export interface ChangePlanV1 {
  /** Legacy operand plans predate planKind and remain readable as-is. */
  planKind?: never;
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
  verificationScope: "offline_native_check_only" | "offline_native_reopen";
  validation: OfflineNativeValidationStep[];
  manualFollowUp: string[];
  rollback: string[];
  [key: string]: unknown;
}

export type MneProgramReplacementCommand =
  | "LOAD"
  | "LOAD NOT"
  | "AND"
  | "AND NOT"
  | "OR"
  | "OR NOT"
  | "AND LOAD"
  | "OR LOAD"
  | "OUT"
  | "SET"
  | "RST"
  | "TON"
  | "END";

export interface MneProgramReplacementInstruction {
  command: MneProgramReplacementCommand;
  operand: string | null;
}

export interface MneFullProgramTarget {
  schema: "controlstudio-mne-target-program/v1";
  programId: string;
  programName: string;
  designDigest: string;
  instructionCount: number;
  instructions: MneProgramReplacementInstruction[];
  targetProgramDigest: string;
}

export interface MneFullProgramApprovedBaseline {
  schema: "controlstudio-mne-approved-baseline/v1";
  projectSha256: string;
  programId: string;
  programName: string;
  ldPayloadSha256: string;
  ldCanonicalSha256?: string;
}

export interface MneFullProgramReplacementChangePlanV2 {
  planKind: "mne_full_program_replacement";
  changeMode: "offline_working_copy_revision";
  requestScope: ChangeRequestScope;
  targetProgram: MneFullProgramTarget;
  baselineBinding: MneFullProgramApprovedBaseline;
  impact: string[];
  risks: string[];
  assumptions: string[];
  blockingUnknowns: string[];
  alternatives: string[];
  acceptanceCriteria: string[];
  observationSignals: string[];
  stopConditions: string[];
  coverage: ChangeCoverageItem[];
  verificationScope: "offline_native_check_only" | "offline_native_reopen";
  validation: MneFullProgramValidationStep[];
  manualFollowUp: string[];
  rollback: string[];
}

/**
 * Host-authored receipt for the one supported structural edit.  This is a
 * digest-only summary: the MCP keeps the baseline and compiled MNE private.
 */
export interface MneFieldIoModule {
  // These nested values are copied from the native MCP receipt and therefore
  // intentionally retain their snake_case wire spelling.
  rack_base: number;
  slot: number;
  module_id: number;
  model: string | null;
  module_type: string | null;
  status: "observed";
}

export interface MneFieldIoChannel {
  index: number;
  status: "derived_from_verified_terminal_range";
  derivation: "address offset within the exact configured module range";
}

export interface MneFieldIoTerminalRange {
  address_start: string;
  address_end: string;
  binding_kind: "built_in_terminal_range" | "slot_reservation";
  status: "observed" | "documented";
}

export interface MneFieldIoBinding {
  device: string;
  direction: "input" | "output";
  pointKind: "digital_input" | "digital_output";
  module: MneFieldIoModule;
  channel: MneFieldIoChannel;
  terminalRange: MneFieldIoTerminalRange;
  bindingDigest: string;
}

export interface MneSeriesGuardCandidateReceipt {
  schema: "xg5000.mne-series-guard-candidate/1.0";
  projectSha256: string;
  programId: string;
  programName: string;
  baselineMneSha256: string;
  baselineLdSha256: string;
  baselineLdCanonicalSha256?: string;
  baselineInstructionCount: number;
  patch: {
    schema: "controlstudio-mne-series-guard-patch/v1";
    guardCommand: "AND" | "AND NOT";
    guardOperand: string;
    outputOperand: string;
    outputInstructionIndex: number;
  };
  targetInstructionCount: number;
  targetMneSha256: string;
  designDigest: string;
  targetProgramDigest: string;
  hardwareContextDigest?: string | null;
  fieldIoBindings?: MneFieldIoBinding[];
  candidateDigest: string;
}

interface MneCandidateChangePlan {
  changeMode: "offline_working_copy_revision";
  requestScope: ChangeRequestScope;
  candidateEvidenceId: string;
  baselineBinding: MneFullProgramApprovedBaseline;
  impact: string[];
  risks: string[];
  assumptions: string[];
  blockingUnknowns: string[];
  alternatives: string[];
  acceptanceCriteria: string[];
  observationSignals: string[];
  stopConditions: string[];
  coverage: ChangeCoverageItem[];
  validation: MneFullProgramValidationStep[];
  manualFollowUp: string[];
  rollback: string[];
}

export interface MneSeriesGuardPatchChangePlanV3 extends MneCandidateChangePlan {
  planKind: "mne_series_guard_patch";
  verificationScope: Exclude<
    ChangeVerificationScope,
    "offline_native_reopen_xgsim"
  >;
  candidate: MneSeriesGuardCandidateReceipt;
}

export interface MneInstructionPatchRow {
  index: number;
  command: MneProgramReplacementCommand;
  operand: string | null;
  instructionDigest: string;
}

export interface MneInstructionPatchCandidateReceipt {
  schema: "xg5000.mne-instruction-patch-candidate/1.0";
  projectSha256: string;
  programId: string;
  programName: string;
  baselineMneSha256: string;
  baselineLdSha256: string;
  baselineLdCanonicalSha256?: string;
  baselineInstructionCount: number;
  patch: {
    schema: "controlstudio-mne-instruction-patch/v1";
    operation: "insert_before" | "insert_after" | "replace" | "delete";
    anchorInstructionIndex: number;
    anchor: MneInstructionPatchRow;
    instruction: MneProgramReplacementInstruction | null;
    contextBefore: MneInstructionPatchRow | null;
    contextAfter: MneInstructionPatchRow | null;
  };
  targetInstructionCount: number;
  targetMneSha256: string;
  designDigest: string;
  targetProgramDigest: string;
  hardwareContextDigest?: string | null;
  fieldIoBindings?: MneFieldIoBinding[];
  candidateDigest: string;
}

export interface MneInstructionPatchChangePlanV4 extends MneCandidateChangePlan {
  planKind: "mne_instruction_patch";
  verificationScope: Exclude<
    ChangeVerificationScope,
    "offline_native_reopen_xgsim"
  >;
  candidate: MneInstructionPatchCandidateReceipt;
}

export interface MneInstructionBatchPatchCandidateReceipt {
  schema: "xg5000.mne-instruction-batch-patch-candidate/1.0";
  projectSha256: string;
  programId: string;
  programName: string;
  baselineMneSha256: string;
  baselineLdSha256: string;
  baselineLdCanonicalSha256?: string;
  baselineInstructionCount: number;
  patches: MneInstructionPatchCandidateReceipt["patch"][];
  targetInstructionCount: number;
  targetMneSha256: string;
  designDigest: string;
  targetProgramDigest: string;
  hardwareContextDigest?: string | null;
  fieldIoBindings?: MneFieldIoBinding[];
  candidateDigest: string;
}

export interface MneInstructionBatchPatchChangePlanV5
  extends MneCandidateChangePlan {
  planKind: "mne_instruction_batch_patch";
  verificationScope: Exclude<
    ChangeVerificationScope,
    "offline_native_reopen_xgsim"
  >;
  candidate: MneInstructionBatchPatchCandidateReceipt;
}

export interface MneRungContactCondition {
  kind: "contact";
  operand: string;
  state: "on" | "off";
}

export interface MneRungCompositeCondition {
  kind: "all_of" | "any_of";
  terms: MneRungCondition[];
}

export type MneRungCondition =
  | MneRungContactCondition
  | MneRungCompositeCondition;

export interface MneRungBaselineReceipt {
  startInstructionIndex: number;
  conditionInstructionCount: number;
  actionInstructionCount: number;
  condition: MneRungCondition;
  actions: MneInstructionPatchRow[];
  rungDigest: string;
}

export interface MneRungTargetReceipt {
  condition: MneRungCondition;
  actions: MneProgramReplacementInstruction[];
  rungDigest: string;
}

export interface MneRungXgSimSimpleProfile {
  schema: "controlstudio-mne-rung-xgsim-profile/v1";
  // This is a digest-bound native adapter payload nested in the otherwise
  // camelCase public candidate receipt. Preserve its native wire spelling.
  normal_assignments: Record<string, 0 | 1>;
  deny_assignments: Record<string, 0 | 1>;
  restore_assignments: Record<string, 0 | 1>;
  output_addresses: string[];
  target_rung_digest: string;
  program_count: 1;
  catalog_digest: string;
  profile_digest: string;
}

export interface MneRungXgSimScenarioStep {
  name: string;
  drive: Record<string, 0 | 1>;
  observe: Record<string, 0 | 1>;
}

export interface MneRungXgSimScenarioProfile {
  schema: "controlstudio-mne-rung-xgsim-profile/v1";
  scenario: { steps: MneRungXgSimScenarioStep[] };
  output_addresses: string[];
  target_rung_digest: string;
  program_count: 1;
  catalog_digest: string;
  profile_digest: string;
}

export type MneRungXgSimProfile =
  | MneRungXgSimSimpleProfile
  | MneRungXgSimScenarioProfile;

export interface MneRungPatchCandidateReceipt {
  schema: "xg5000.mne-rung-patch-candidate/1.0";
  projectSha256: string;
  programId: string;
  programName: string;
  baselineMneSha256: string;
  baselineLdSha256: string;
  baselineLdCanonicalSha256?: string;
  baselineInstructionCount: number;
  patch: {
    schema: "controlstudio-mne-rung-patch/v1";
    operation:
      | "replace_condition"
      | "replace_rung"
      | "insert_before"
      | "delete";
    anchor: MneInstructionPatchRow;
    anchorRung: MneRungBaselineReceipt | null;
    targetRung: MneRungTargetReceipt | null;
  };
  targetInstructionCount: number;
  targetMneSha256: string;
  designDigest: string;
  targetProgramDigest: string;
  simulationProfile: MneRungXgSimProfile | null;
  hardwareContextDigest?: string | null;
  fieldIoBindings?: MneFieldIoBinding[];
  candidateDigest: string;
}

export interface MneRungPatchChangePlanV6
  extends MneCandidateChangePlan {
  planKind: "mne_rung_patch";
  verificationScope: ChangeVerificationScope;
  candidate: MneRungPatchCandidateReceipt;
}

export type ChangePlan =
  | ChangePlanV1
  | MneFullProgramReplacementChangePlanV2
  | MneSeriesGuardPatchChangePlanV3
  | MneInstructionPatchChangePlanV4
  | MneInstructionBatchPatchChangePlanV5
  | MneRungPatchChangePlanV6;
export type MneFullProgramReplacementChangePlanInputV2 = Omit<
  MneFullProgramReplacementChangePlanV2,
  "baselineBinding"
> & {
  baselineBinding?: MneFullProgramApprovedBaseline;
};
export type MneSeriesGuardPatchChangePlanInputV3 = Omit<
  MneSeriesGuardPatchChangePlanV3,
  "baselineBinding"
> & {
  baselineBinding?: MneFullProgramApprovedBaseline;
};
export type MneInstructionPatchChangePlanInputV4 = Omit<
  MneInstructionPatchChangePlanV4,
  "baselineBinding"
> & {
  baselineBinding?: MneFullProgramApprovedBaseline;
};
export type MneInstructionBatchPatchChangePlanInputV5 = Omit<
  MneInstructionBatchPatchChangePlanV5,
  "baselineBinding"
> & {
  baselineBinding?: MneFullProgramApprovedBaseline;
};
export type MneRungPatchChangePlanInputV6 = Omit<
  MneRungPatchChangePlanV6,
  "baselineBinding"
> & {
  baselineBinding?: MneFullProgramApprovedBaseline;
};
export type ChangePlanInput =
  | ChangePlanV1
  | MneFullProgramReplacementChangePlanInputV2
  | MneSeriesGuardPatchChangePlanInputV3
  | MneInstructionPatchChangePlanInputV4
  | MneInstructionBatchPatchChangePlanInputV5
  | MneRungPatchChangePlanInputV6;

export interface ChangeTargetIdentity {
  workspaceId: string;
  revisionId: string;
  artifactHash: string;
  sourceArtifactHash: string;
  projectPath: string;
  vendor: string;
  engineeringSystem: string;
  engineeringSessionId?: string | null;
  engineeringProcessId?: number | null;
  engineeringWindowHandle?: number | null;
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
  conversationId?: string | null;
  runId?: string | null;
  state: ChangeTransactionState;
  targetIdentity: ChangeTargetIdentity;
  mutationScope: string;
  riskLevel: ChangeRiskLevel;
  intent: string;
  plan: ChangePlan;
  planDigest: string;
  requestedByActorId: string;
  approvalExpiresAt: string;
  approval?: ChangeApproval | null;
  result: Record<string, unknown>;
  errorCode?: string | null;
  errorDetail?: string | null;
  createdAt: string;
  updatedAt: string;
  executionStartedAt?: string | null;
  verificationStartedAt?: string | null;
  completedAt?: string | null;
  receiptAcknowledgedAt?: string | null;
  receiptAcknowledgedBy?: string | null;
}

export interface CreateChangeTransactionRequest {
  operationId: string;
  conversationId?: string;
  runId?: string;
  intent: string;
  riskLevel: ChangeRiskLevel;
  plan: ChangePlanInput;
  approvalTtlSeconds: number;
}

export interface DecideChangeTransactionRequest {
  decision: "approve" | "reject";
  expectedPlanDigest: string;
}

export interface ExecuteChangeTransactionRequest {
  expectedPlanDigest: string;
}

export interface FinalizeSimulationChangeRequest {
  decision: "accept" | "revert";
  expectedPlanDigest: string;
  expectedSimulationReceiptDigest: string;
}

export interface ReconcileChangeTransactionRequest {
  expectedPlanDigest: string;
  pid: number;
  hwnd: number;
  relaunchIfUnavailable?: boolean;
  /** MCP restores only the exact journal-pinned backup after preserving the current file. */
  restoreVerifiedBackup?: boolean;
  /** Exact active working-copy SHA shown in the reviewed recovery panel. */
  expectedCurrentProjectHash?: string;
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
  | "conversation.engineering_handoff_created"
  | "change.simulation_finalized"
  | "conversation.engineering_session_rebound"
  | "message.appended"
  | "host.status"
  | "run.created"
  | "run.state"
  | "run.state_changed"
  | "agent.token"
  | "agent.progress"
  | "agent.message"
  | "mission.checkpointed"
  | "mission.blocked_step_reverified"
  | "mission.recovery_digest_repaired"
  | "agent.checkpointed"
  | "tool.selected"
  | "tool.started"
  | "tool.progress"
  | "tool.completed"
  | "tool.failed"
  | "evidence.added"
  | "project_plan.reviewed"
  | "approval.requested"
  | "approval.resolved"
  | "approval.decided"
  | "approval.expired"
  | "change.transaction_created"
  | "change.transaction.updated"
  | "change.receipt_acknowledged"
  | "change.execution_started"
  | "change.verification_started"
  | "change.native_checkpoint"
  | "change.execution_finished"
  | "change.working_copy_restored"
  | "change.reconciled"
  | "change.quarantine_reconciled"
  | "controller.connected"
  | "controller.observed"
  | "controller.devices_read"
  | "controller.devices_traced"
  | "controller.disconnected"
  | "knowledge.source_imported"
  | "knowledge.memory_proposed"
  | "knowledge.memory_confirmed"
  | "workspace.opened"
  | "workspace.resumed"
  | "native_project.operation"
  | "native_project.reconciled"
  | "native_simulation.trial_decision"
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
  rationale?: string;
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
  schemaVersion: "1" | "2";
  authorityCurrent?: boolean;
  messageId: string;
  planDigest: string;
  reviewedSectionIds: string[];
  reviewedItemIds: string[];
  projectBriefDigest?: string | null;
  attachmentRevisionDigest?: string | null;
  actorId: string;
  reviewedAt: string;
}

export interface EngineeringPlanOpenDecision {
  decisionId: string;
  question: string;
  status: "unknown";
  source: "agent";
  blocking: true;
  rationale?: string;
  verificationMethod?: string;
}

export interface EngineeringPlanRevisionItem {
  itemId: string;
  kind: "plan_item" | "open_decision";
  sectionId: string;
  sectionTitle: string;
  text: string;
  status: EngineeringPlanItemStatus;
  source: EngineeringPlanItemSource;
}

export interface EngineeringPlanRevision {
  schemaVersion: "1";
  previousMessageId: string;
  previousPlanDigest: string;
  currentPlanDigest: string;
  changedFields: Array<
    | "projectName"
    | "status"
    | "summary"
    | "objective"
    | "attachments"
    | "technicalConfiguration"
    | "design"
    | "sections"
    | "openDecisions"
    | "completeness"
  >;
  changedSectionIds: string[];
  addedItems: EngineeringPlanRevisionItem[];
  removedItems: EngineeringPlanRevisionItem[];
}

export interface EngineeringPlanSignalCondition {
  signalId: string;
  state: "on" | "off";
}

export interface EngineeringPlanIoPoint {
  signalId: string;
  symbol: string;
  address: string;
  kind: "digital_input" | "digital_output" | "internal_bit";
  description: string;
  normalState: "on" | "off" | "unknown";
  safeState: "on" | "off" | "not_applicable";
}

export interface EngineeringPlanOperatingMode {
  modeId: string;
  name: string;
  description: string;
  activation: EngineeringPlanSignalCondition;
  allowedTransitions: string[];
}

export interface EngineeringPlanControlNetwork {
  networkId: string;
  title: string;
  modeId?: string | null;
  stepId?: string | null;
  permitAll: EngineeringPlanSignalCondition[];
  outputSignalId: string;
}

export interface EngineeringPlanLadderContactCondition {
  kind: "contact";
  sourceType: "signal" | "timer" | "step" | "sequence_reset";
  sourceId: string;
  state: "on" | "off";
}

export interface EngineeringPlanLadderCompositeCondition {
  kind: "all_of" | "any_of";
  terms: EngineeringPlanLadderCondition[];
}

export type EngineeringPlanLadderCondition =
  | EngineeringPlanLadderContactCondition
  | EngineeringPlanLadderCompositeCondition;

export interface EngineeringPlanLadderTargetAction {
  instruction: "OUT" | "SET" | "RST" | "TON";
  targetType: "signal" | "timer" | "step" | "sequence_reset";
  targetId: string;
}

export interface EngineeringPlanLadderMoveAction {
  instruction: "MOV";
  sourceConstant: number;
  destinationAddress: string;
}

export type EngineeringPlanLadderAction =
  | EngineeringPlanLadderTargetAction
  | EngineeringPlanLadderMoveAction;

export interface EngineeringPlanLadderRung {
  rungId: string;
  description: string;
  condition: EngineeringPlanLadderCondition;
  action: EngineeringPlanLadderAction;
}

export interface EngineeringPlanTimer {
  timerId: string;
  name: string;
  address: string;
  instruction: "TON";
  timeBaseMs: 1 | 10 | 100;
  presetTicks: number;
  durationMs: number;
  modeId?: string | null;
  stepId?: string | null;
  enableAll: EngineeringPlanSignalCondition[];
  description: string;
}

export interface EngineeringPlanSequenceStep {
  stepId: string;
  name: string;
  address: string;
  completeAll: EngineeringPlanSignalCondition[];
  description: string;
}

export interface EngineeringPlanSequence {
  sequenceId: string;
  name: string;
  modeId?: string | null;
  restartPolicy: "require_start_release" | "continuous";
  startAll: EngineeringPlanSignalCondition[];
  resetAll: EngineeringPlanSignalCondition[];
  steps: EngineeringPlanSequenceStep[];
}

export interface EngineeringPlanAlarm {
  alarmId: string;
  name: string;
  latchSignalId: string;
  modeId?: string | null;
  stepId?: string | null;
  triggerAll: EngineeringPlanSignalCondition[];
  affectedOutputSignalIds: string[];
  description: string;
}

export interface EngineeringPlanRecoveryRule {
  recoveryId: string;
  name: string;
  whenAll: EngineeringPlanSignalCondition[];
  resetAlarmIds: string[];
  resetSequenceIds: string[];
  description: string;
}

export interface EngineeringPlanSafetyRule {
  ruleId: string;
  name: string;
  classification: "process_interlock" | "external_safety_boundary";
  permitWhen?: EngineeringPlanSignalCondition | null;
  affectedOutputSignalIds: string[];
  action: "deenergize" | "external_hardware";
}

export type EngineeringPlanCollectionNotApplicableBasis =
  | "single_mode_operation"
  | "no_timing_behavior"
  | "no_sequence_behavior"
  | "no_alarm_behavior"
  | "no_recovery_behavior"
  | "no_physical_or_hazard_outputs";

export interface EngineeringPlanCollectionScopeDecision {
  status: "confirmed" | "not_applicable";
  reason: string;
  evidence: string[];
  basis: EngineeringPlanCollectionNotApplicableBasis | null;
}

export interface EngineeringPlanNativeCollectionScope {
  ioPoints: EngineeringPlanCollectionScopeDecision;
  operatingModes: EngineeringPlanCollectionScopeDecision;
  controlNetworks: EngineeringPlanCollectionScopeDecision;
  timers: EngineeringPlanCollectionScopeDecision;
  sequences: EngineeringPlanCollectionScopeDecision;
  alarms: EngineeringPlanCollectionScopeDecision;
  recoveryRules: EngineeringPlanCollectionScopeDecision;
  safetyRules: EngineeringPlanCollectionScopeDecision;
}

export interface CompiledEngineeringPlanCollectionScopeDecision
  extends EngineeringPlanCollectionScopeDecision {
  source: "explicit";
}

export interface CompiledEngineeringPlanNativeCollectionScope {
  ioPoints: CompiledEngineeringPlanCollectionScopeDecision;
  operatingModes: CompiledEngineeringPlanCollectionScopeDecision;
  controlNetworks: CompiledEngineeringPlanCollectionScopeDecision;
  timers: CompiledEngineeringPlanCollectionScopeDecision;
  sequences: CompiledEngineeringPlanCollectionScopeDecision;
  alarms: CompiledEngineeringPlanCollectionScopeDecision;
  recoveryRules: CompiledEngineeringPlanCollectionScopeDecision;
  safetyRules: CompiledEngineeringPlanCollectionScopeDecision;
}

export interface EngineeringPlanNativeDesign {
  schemaVersion: "2";
  collectionScope?: EngineeringPlanNativeCollectionScope;
  ioPoints: EngineeringPlanIoPoint[];
  operatingModes: EngineeringPlanOperatingMode[];
  controlNetworks: EngineeringPlanControlNetwork[];
  ladderRungs?: EngineeringPlanLadderRung[];
  timers: EngineeringPlanTimer[];
  sequences: EngineeringPlanSequence[];
  alarms: EngineeringPlanAlarm[];
  recoveryRules: EngineeringPlanRecoveryRule[];
  safetyRules: EngineeringPlanSafetyRule[];
}

export interface EngineeringPlanTechnicalConfiguration {
  plcSeries: "XGK" | "XGB";
  cpuModel: string;
  programName: string;
  language: "LD";
  description: string;
}

export interface EngineeringPlanAttachment {
  sourceId: string;
  title: string;
  kind: "manual" | "standard" | "project_document";
  scope: string;
  vendor?: string;
  productFamily?: string;
  version?: string;
  language?: string;
  authority: string;
  sha256: string;
  revision: string;
}

/**
 * Server-owned planning artifact for a new PLC project.
 *
 * The artifact keeps facts, proposals, unknowns, and conflicts distinct so
 * plan review cannot silently turn an agent assumption into a requirement.
 */
export interface NewProjectEngineeringPlan {
  schemaVersion: "2";
  projectName: string;
  status: EngineeringPlanStatus;
  summary: string;
  objective: string;
  attachments: EngineeringPlanAttachment[];
  technicalConfiguration?: EngineeringPlanTechnicalConfiguration | null;
  design: EngineeringPlanNativeDesign;
  sections: EngineeringPlanSection[];
  openDecisions: EngineeringPlanOpenDecision[];
  completeness: EngineeringPlanCompleteness;
}

export interface ProjectPlanReviewRequest {
  schemaVersion: "2";
  messageId: string;
  planDigest: string;
  reviewedSectionIds: string[];
  reviewedItemIds: string[];
}

export interface ProjectPlanReviewResponse {
  review: EngineeringPlanReview;
  reviewRecorded: boolean;
}

export interface ReviewedProjectPlanBinding {
  schemaVersion: "2";
  conversationId: string;
  messageId: string;
  planDigest: string;
  projectBriefDigest: string;
  attachmentRevisionDigest: string;
  reviewedSectionIds: string[];
  reviewedItemIds: string[];
}
