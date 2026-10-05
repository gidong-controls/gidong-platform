import type { ChangeCoverageArea, ChangeCoverageItem, ChangePlan, ChangePlanV1, ChangeTransaction } from "./types";

const SHA256 = /^[0-9a-f]{64}$/;
const COVERAGE_AREAS = new Set<ChangeCoverageArea>([
  "target_identity",
  "value_semantics",
  "direct_impact",
  "indirect_impact",
  "modes",
  "runtime",
  "external_integrations",
  "safety",
  "validation",
  "rollback",
]);
const COVERAGE_STATUSES = new Set([
  "confirmed",
  "not_applicable_with_reason",
  "unknown_blocking",
]);
const CHANGE_STATES = new Set([
  "pending_approval",
  "approved",
  "executing",
  "verifying",
  "observing",
  "completed",
  "rolled_back",
  "failed",
  "quarantined",
  "rejected",
  "expired",
]);
const RISK_LEVELS = new Set(["low", "medium", "high", "critical"]);
const CHECK_ONLY_VALIDATION = [
  "structural_diff",
  "xg5000_program_check",
  "project_save",
] as const;
const REOPEN_VALIDATION = [...CHECK_ONLY_VALIDATION, "project_reopen"] as const;

function matchesValidation(
  value: unknown,
  expected: readonly string[],
): boolean {
  return (
    Array.isArray(value) &&
    value.join("\u0000") === expected.join("\u0000")
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isStringList(
  value: unknown,
  nonempty = false,
  maxLength = 200,
): value is string[] {
  return (
    Array.isArray(value) &&
    (!nonempty || value.length > 0) &&
    value.length <= maxLength &&
    value.every(
      (item) =>
        typeof item === "string" &&
        item.trim().length > 0 &&
        item.length <= 4_000,
    )
  );
}

function containsPrivatePayloadKey(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(containsPrivatePayloadKey);
  }
  if (!isRecord(value)) {
    return false;
  }
  return Object.entries(value).some(([key, item]) => {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    const publicBaselineDigest =
      [
        "baselineldsha256",
        "baselineldcanonicalsha256",
      ].includes(normalized) &&
      typeof item === "string" &&
      SHA256.test(item);
    return (
      key.startsWith("_") ||
      normalized.endsWith("payload") ||
      normalized === "clipboard" ||
      normalized === "privatematerial" ||
      normalized.startsWith("rawld") ||
      (!publicBaselineDigest && normalized.startsWith("baselineld")) ||
      normalized.startsWith("privateld") ||
      containsPrivatePayloadKey(item)
    );
  });
}

function isCoverageItem(value: unknown): value is ChangeCoverageItem {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.area === "string" &&
    COVERAGE_AREAS.has(value.area as ChangeCoverageArea) &&
    typeof value.status === "string" &&
    COVERAGE_STATUSES.has(value.status) &&
    typeof value.summary === "string" &&
    value.summary.trim().length > 0 &&
    value.summary.length <= 4_000 &&
    isStringList(value.evidenceRefs, false, 100)
  );
}

function isCoverage(value: unknown, exact = false): value is ChangeCoverageItem[] {
  if (
    !Array.isArray(value) ||
    value.length > COVERAGE_AREAS.size ||
    !value.every(isCoverageItem)
  ) {
    return false;
  }
  const areas = new Set(value.map((item) => item.area));
  return !exact || (value.length === COVERAGE_AREAS.size && areas.size === value.length);
}

export function isLegacyChangePlanV1(value: unknown): value is ChangePlanV1 {
  if (
    !isRecord(value) ||
    (Object.prototype.hasOwnProperty.call(value, "planKind") &&
      value.planKind !== undefined) ||
    containsPrivatePayloadKey(value) ||
    !isRecord(value.target) ||
    !isRecord(value.current) ||
    !isRecord(value.proposed) ||
    !isStringList(value.impact) ||
    !isStringList(value.risks) ||
    !isStringList(value.manualFollowUp) ||
    !isStringList(value.rollback, true) ||
    !(
      ((value.requestScope === "permanent_offline_revision" ||
        value.requestScope === "offline_candidate_revision") &&
        value.verificationScope === "offline_native_check_only" &&
        matchesValidation(value.validation, CHECK_ONLY_VALIDATION)) ||
      (value.verificationScope === "offline_native_reopen" &&
        matchesValidation(value.validation, REOPEN_VALIDATION))
    )
  ) {
    return false;
  }
  return (
    (value.coverage === undefined || isCoverage(value.coverage)) &&
    [
      value.assumptions,
      value.blockingUnknowns,
      value.alternatives,
      value.acceptanceCriteria,
      value.observationSignals,
      value.stopConditions,
    ].every((items) => items === undefined || isStringList(items))
  );
}

function isOptionalText(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "string" && value.length > 0)
  );
}

function isOptionalErrorCode(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "string" && /^[a-z0-9_]{1,128}$/.test(value))
  );
}

function isTargetIdentity(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.workspaceId === "string" &&
    value.workspaceId.length > 0 &&
    typeof value.revisionId === "string" &&
    value.revisionId.length > 0 &&
    typeof value.artifactHash === "string" &&
    SHA256.test(value.artifactHash) &&
    typeof value.sourceArtifactHash === "string" &&
    SHA256.test(value.sourceArtifactHash) &&
    typeof value.projectPath === "string" &&
    value.projectPath.length > 0 &&
    typeof value.vendor === "string" &&
    value.vendor.length > 0 &&
    typeof value.engineeringSystem === "string" &&
    value.engineeringSystem.length > 0 &&
    isOptionalText(value.engineeringSessionId) &&
    (value.engineeringProcessId === undefined ||
      value.engineeringProcessId === null ||
      (typeof value.engineeringProcessId === "number" &&
        Number.isSafeInteger(value.engineeringProcessId) &&
        value.engineeringProcessId > 0)) &&
    (value.engineeringWindowHandle === undefined ||
      value.engineeringWindowHandle === null ||
      (typeof value.engineeringWindowHandle === "number" &&
        Number.isSafeInteger(value.engineeringWindowHandle) &&
        value.engineeringWindowHandle > 0))
  );
}

function isApproval(value: unknown): boolean {
  if (value === undefined || value === null) {
    return true;
  }
  return (
    isRecord(value) &&
    (value.decision === "approve" || value.decision === "reject") &&
    typeof value.planDigest === "string" &&
    SHA256.test(value.planDigest) &&
    typeof value.actorId === "string" &&
    value.actorId.length > 0 &&
    typeof value.decidedAt === "string" &&
    value.decidedAt.length > 0
  );
}

export function isChangeTransaction(
  value: unknown,
): value is ChangeTransaction {
  if (
    !isRecord(value) ||
    typeof value.transactionId !== "string" ||
    value.transactionId.length === 0 ||
    typeof value.operationId !== "string" ||
    value.operationId.length === 0 ||
    !isOptionalText(value.conversationId) ||
    !isOptionalText(value.runId) ||
    typeof value.state !== "string" ||
    !CHANGE_STATES.has(value.state) ||
    !isTargetIdentity(value.targetIdentity) ||
    (value.executionTargetIdentity != null && !isTargetIdentity(value.executionTargetIdentity)) ||
    typeof value.mutationScope !== "string" ||
    value.mutationScope.length === 0 ||
    typeof value.riskLevel !== "string" ||
    !RISK_LEVELS.has(value.riskLevel) ||
    typeof value.intent !== "string" ||
    value.intent.length === 0 ||
    typeof value.planDigest !== "string" ||
    !SHA256.test(value.planDigest) ||
    typeof value.requestedByActorId !== "string" ||
    value.requestedByActorId.length === 0 ||
    typeof value.approvalExpiresAt !== "string" ||
    value.approvalExpiresAt.length === 0 ||
    !isApproval(value.approval) ||
    !isRecord(value.result) ||
    !isChangePlan(value.plan) ||
    !isOptionalErrorCode(value.errorCode) ||
    !isOptionalText(value.errorDetail) ||
    typeof value.createdAt !== "string" ||
    value.createdAt.length === 0 ||
    typeof value.updatedAt !== "string" ||
    value.updatedAt.length === 0 ||
    !isOptionalText(value.executionStartedAt) ||
    !isOptionalText(value.verificationStartedAt) ||
    !isOptionalText(value.completedAt) ||
    (value.receiptAcknowledgedAt !== null &&
      !isOptionalText(value.receiptAcknowledgedAt)) ||
    (value.receiptAcknowledgedBy !== null &&
      !isOptionalText(value.receiptAcknowledgedBy))
  ) {
    return false;
  }
  return true;
}

export function parseChangeTransaction(value: unknown): ChangeTransaction {
  if (!isChangeTransaction(value)) {
    throw new Error("Host returned a malformed or unsupported change transaction.");
  }
  return value;
}

export function parseChangeTransactionList(value: unknown): ChangeTransaction[] {
  if (!Array.isArray(value)) {
    throw new Error("Host returned a malformed change transaction list.");
  }
  return value.map(parseChangeTransaction);
}

export function hasSupportedChangeRecoveryContract(value: unknown): boolean {
  return isLegacyChangePlanV1(value);
}

export function isChangePlan(value: unknown): value is ChangePlan {
  return isLegacyChangePlanV1(value);
}
