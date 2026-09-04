import type {
  ChangeCoverageArea,
  ChangeCoverageItem,
  ChangePlan,
  ChangePlanV1,
  ChangeTransaction,
  MneFullProgramReplacementChangePlanV2,
  MneInstructionBatchPatchCandidateReceipt,
  MneInstructionBatchPatchChangePlanV5,
  MneInstructionPatchCandidateReceipt,
  MneInstructionPatchChangePlanV4,
  MneInstructionPatchRow,
  MneProgramReplacementCommand,
  MneRungCondition,
  MneRungPatchCandidateReceipt,
  MneRungPatchChangePlanV6,
  MneSeriesGuardCandidateReceipt,
  MneSeriesGuardPatchChangePlanV3,
} from "./types";

export type StructuralMnePlan =
  | {
      kind: "full_program_replacement";
      plan: MneFullProgramReplacementChangePlanV2;
    }
  | {
      kind: "series_guard_patch";
      plan: MneSeriesGuardPatchChangePlanV3;
    }
  | {
      kind: "instruction_patch";
      plan: MneInstructionPatchChangePlanV4;
    }
  | {
      kind: "instruction_batch_patch";
      plan: MneInstructionBatchPatchChangePlanV5;
    }
  | {
      kind: "rung_patch";
      plan: MneRungPatchChangePlanV6;
    };

const SHA256 = /^[0-9a-f]{64}$/;
const MNE_COMPILER_CANDIDATE_COVERAGE_AREAS: ChangeCoverageArea[] = [
  "target_identity",
  "direct_impact",
];
const MNE_BOOLEAN_CONTACT_COMMANDS = new Set([
  "LOAD",
  "LOAD NOT",
  "AND",
  "AND NOT",
  "OR",
  "OR NOT",
]);
const MNE_PATCH_ANCHOR_COMMANDS = new Set([
  ...MNE_BOOLEAN_CONTACT_COMMANDS,
  "OUT",
  "SET",
  "RST",
  "TON",
]);
const CONTACT_OPERAND =
  /^(?:[PM]\d{3,4}[0-9A-F]|T\d{3,4}|S(?:\d{2}|1[01]\d|12[0-7])\.\d{2})$/;
const WRITABLE_OPERAND =
  /^(?:[PM]\d{3,4}[0-9A-F]|S(?:\d{2}|1[01]\d|12[0-7])\.\d{2})$/;
const TON_OPERAND = /^T\d{3,4} ([1-9]\d{0,4})$/;

const MNE_COMMANDS = new Set<MneProgramReplacementCommand>([
  "LOAD",
  "LOAD NOT",
  "AND",
  "AND NOT",
  "OR",
  "OR NOT",
  "AND LOAD",
  "OR LOAD",
  "OUT",
  "SET",
  "RST",
  "TON",
  "END",
]);
const NO_OPERAND_COMMANDS = new Set<MneProgramReplacementCommand>([
  "AND LOAD",
  "OR LOAD",
  "END",
]);
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
  "completed",
  "rolled_back",
  "failed",
  "quarantined",
  "rejected",
  "expired",
]);
const RISK_LEVELS = new Set(["low", "medium", "high", "critical"]);

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

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const keys = Object.keys(value);
  return (
    keys.length === expected.length &&
    expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
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
    return (
      key.startsWith("_") ||
      normalized.endsWith("payload") ||
      normalized === "clipboard" ||
      normalized === "privatematerial" ||
      normalized.startsWith("rawmne") ||
      normalized.startsWith("rawld") ||
      normalized.startsWith("baselinemne") ||
      normalized.startsWith("baselineld") ||
      normalized.startsWith("privatemne") ||
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

function isMneCandidateCoverage(
  value: unknown,
  candidateEvidenceId: string,
): value is ChangeCoverageItem[] {
  if (!isCoverage(value) || value.length < 2) return false;
  const byArea = new Map(value.map((item) => [item.area, item]));
  if (byArea.size !== value.length || value.some((item) => item.status === "confirmed" && item.evidenceRefs.length === 0)) return false;
  return MNE_COMPILER_CANDIDATE_COVERAGE_AREAS.every((area) => {
    const item = byArea.get(area);
    return item?.status === "confirmed" && item.evidenceRefs.includes(candidateEvidenceId);
  });
}

function isCanonicalMneInstruction(value: unknown): boolean {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["command", "operand"]) ||
    typeof value.command !== "string" ||
    !MNE_COMMANDS.has(value.command as MneProgramReplacementCommand) ||
    (value.operand !== null &&
      (typeof value.operand !== "string" || value.operand.length > 128))
  ) {
    return false;
  }
  const command = value.command as MneProgramReplacementCommand;
  if (NO_OPERAND_COMMANDS.has(command)) {
    return value.operand === null;
  }
  if (typeof value.operand !== "string" || value.operand !== value.operand.trim().toUpperCase()) {
    return false;
  }
  if (["LOAD", "LOAD NOT", "AND", "AND NOT", "OR", "OR NOT"].includes(command)) {
    return CONTACT_OPERAND.test(value.operand);
  }
  if (command === "SET") {
    return WRITABLE_OPERAND.test(value.operand);
  }
  if (command === "OUT" || command === "RST") {
    return /^[PM]\d{3,4}[0-9A-F]$/.test(value.operand);
  }
  const ton = TON_OPERAND.exec(value.operand);
  return command === "TON" && ton !== null && Number(ton[1]) <= 65_535;
}

export function isMneFullProgramReplacementPlan(
  value: unknown,
): value is MneFullProgramReplacementChangePlanV2 {
  if (
    !isRecord(value) ||
    value.planKind !== "mne_full_program_replacement" ||
    containsPrivatePayloadKey(value) ||
    !hasExactKeys(value, [
      "planKind",
      "changeMode",
      "requestScope",
      "targetProgram",
      "baselineBinding",
      "impact",
      "risks",
      "assumptions",
      "blockingUnknowns",
      "alternatives",
      "acceptanceCriteria",
      "observationSignals",
      "stopConditions",
      "coverage",
      "verificationScope",
      "validation",
      "manualFollowUp",
      "rollback",
    ]) ||
    value.changeMode !== "offline_working_copy_revision" ||
    !["offline_candidate_revision", "permanent_offline_revision"].includes(
      String(value.requestScope),
    ) ||
    value.verificationScope !== "offline_native_reopen" ||
    !Array.isArray(value.validation) ||
    value.validation.join("\u0000") !==
      [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
        "project_reopen",
      ].join("\u0000") ||
    !isStringList(value.impact, true) ||
    !isStringList(value.risks, true) ||
    !isStringList(value.assumptions) ||
    !isStringList(value.alternatives, true) ||
    !isStringList(value.acceptanceCriteria, true) ||
    !isStringList(value.observationSignals, true) ||
    !isStringList(value.stopConditions, true) ||
    !isStringList(value.manualFollowUp, true) ||
    !isStringList(value.rollback, true) ||
    !Array.isArray(value.blockingUnknowns) ||
    value.blockingUnknowns.length !== 0 ||
    !isCoverage(value.coverage, true)
  ) {
    return false;
  }

  const target = value.targetProgram;
  if (
    !isRecord(target) ||
    !hasExactKeys(target, [
      "schema",
      "programId",
      "programName",
      "designDigest",
      "instructionCount",
      "instructions",
      "targetProgramDigest",
    ]) ||
    target.schema !== "controlstudio-mne-target-program/v1" ||
    typeof target.programId !== "string" ||
    target.programId.length === 0 ||
    target.programId.length > 512 ||
    target.programId !== target.programId.trim() ||
    typeof target.programName !== "string" ||
    target.programName.length === 0 ||
    target.programName.length > 80 ||
    target.programName !== target.programName.trim() ||
    typeof target.designDigest !== "string" ||
    !SHA256.test(target.designDigest) ||
    typeof target.targetProgramDigest !== "string" ||
    !SHA256.test(target.targetProgramDigest) ||
    typeof target.instructionCount !== "number" ||
    !Number.isSafeInteger(target.instructionCount) ||
    !Array.isArray(target.instructions) ||
    target.instructionCount !== target.instructions.length ||
    target.instructions.length < 1 ||
    target.instructions.length > 4_096 ||
    !target.instructions.every(isCanonicalMneInstruction)
  ) {
    return false;
  }
  const baseline = value.baselineBinding;
  if (
    !isRecord(baseline) ||
    !(
      hasExactKeys(baseline, [
      "schema",
      "projectSha256",
      "programId",
      "programName",
      "ldPayloadSha256",
      ]) ||
      hasExactKeys(baseline, [
        "schema",
        "projectSha256",
        "programId",
        "programName",
        "ldPayloadSha256",
        "ldCanonicalSha256",
      ])
    ) ||
    baseline.schema !== "controlstudio-mne-approved-baseline/v1" ||
    typeof baseline.projectSha256 !== "string" ||
    !SHA256.test(baseline.projectSha256) ||
    baseline.programId !== target.programId ||
    baseline.programName !== target.programName ||
    typeof baseline.ldPayloadSha256 !== "string" ||
    !SHA256.test(baseline.ldPayloadSha256) ||
    (baseline.ldCanonicalSha256 !== undefined &&
      (typeof baseline.ldCanonicalSha256 !== "string" ||
        !SHA256.test(baseline.ldCanonicalSha256)))
  ) {
    return false;
  }
  const endIndexes = target.instructions.flatMap((instruction, index) =>
    isRecord(instruction) && instruction.command === "END" ? [index] : [],
  );
  return endIndexes.length === 1 && endIndexes[0] === target.instructions.length - 1;
}

export function isMneSeriesGuardPatchPlan(
  value: unknown,
): value is MneSeriesGuardPatchChangePlanV3 {
  if (
    !isMneCandidatePlanEnvelope(value, "mne_series_guard_patch")
  ) {
    return false;
  }

  const rawCandidate = value.candidate;
  if (
    !isMnePatchCandidateEnvelope(
      rawCandidate,
      "xg5000.mne-series-guard-candidate/1.0",
    )
  ) {
    return false;
  }
  const candidate = rawCandidate;
  if (
    candidate.targetInstructionCount !== candidate.baselineInstructionCount + 1 ||
    candidate.targetInstructionCount > 4_096
  ) {
    return false;
  }
  const patch = candidate.patch;
  if (
    !isRecord(patch) ||
    !hasExactKeys(patch, [
      "schema",
      "guardCommand",
      "guardOperand",
      "outputOperand",
      "outputInstructionIndex",
    ]) ||
    patch.schema !== "controlstudio-mne-series-guard-patch/v1" ||
    (patch.guardCommand !== "AND" && patch.guardCommand !== "AND NOT") ||
    typeof patch.guardOperand !== "string" ||
    !CONTACT_OPERAND.test(patch.guardOperand) ||
    patch.guardOperand !== patch.guardOperand.trim().toUpperCase() ||
    typeof patch.outputOperand !== "string" ||
    !/^[PM]\d{3,4}[0-9A-F]$/.test(patch.outputOperand) ||
    patch.outputOperand !== patch.outputOperand.trim().toUpperCase() ||
    typeof patch.outputInstructionIndex !== "number" ||
    !Number.isSafeInteger(patch.outputInstructionIndex) ||
    patch.outputInstructionIndex < 1 ||
    patch.outputInstructionIndex >= candidate.baselineInstructionCount - 1
  ) {
    return false;
  }
  return isApprovedBaselineForCandidate(value.baselineBinding, candidate);
}

const MNE_CANDIDATE_PLAN_KEYS = [
  "planKind",
  "changeMode",
  "requestScope",
  "candidateEvidenceId",
  "candidate",
  "baselineBinding",
  "impact",
  "risks",
  "assumptions",
  "blockingUnknowns",
  "alternatives",
  "acceptanceCriteria",
  "observationSignals",
  "stopConditions",
  "coverage",
  "verificationScope",
  "validation",
  "manualFollowUp",
  "rollback",
] as const;

function isMneCandidatePlanEnvelope(
  value: unknown,
  planKind:
    | "mne_series_guard_patch"
    | "mne_instruction_patch"
    | "mne_instruction_batch_patch"
    | "mne_rung_patch",
): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    value.planKind === planKind &&
    !containsPrivatePayloadKey(value) &&
    hasExactKeys(value, MNE_CANDIDATE_PLAN_KEYS) &&
    value.changeMode === "offline_working_copy_revision" &&
    ["offline_candidate_revision", "permanent_offline_revision"].includes(
      String(value.requestScope),
    ) &&
    typeof value.candidateEvidenceId === "string" &&
    /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(
      value.candidateEvidenceId,
    ) &&
    (value.verificationScope === "offline_native_reopen" ||
      (planKind === "mne_rung_patch" &&
        value.verificationScope === "offline_native_reopen_xgsim")) &&
    Array.isArray(value.validation) &&
    value.validation.join("\u0000") ===
      [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
        "project_reopen",
      ].join("\u0000") &&
    isStringList(value.impact, true) &&
    isStringList(value.risks, true) &&
    isStringList(value.assumptions) &&
    isStringList(value.alternatives, true) &&
    isStringList(value.acceptanceCriteria, true) &&
    isStringList(value.observationSignals, true) &&
    isStringList(value.stopConditions, true) &&
    isStringList(value.manualFollowUp, true) &&
    isStringList(value.rollback, true) &&
    Array.isArray(value.blockingUnknowns) &&
    value.blockingUnknowns.length === 0 &&
    isMneCandidateCoverage(value.coverage, value.candidateEvidenceId)
  );
}

function isMnePatchCandidateEnvelope(
  value: unknown,
  schema: "xg5000.mne-series-guard-candidate/1.0",
): value is MneSeriesGuardCandidateReceipt;
function isMnePatchCandidateEnvelope(
  value: unknown,
  schema: "xg5000.mne-instruction-patch-candidate/1.0",
): value is MneInstructionPatchCandidateReceipt;
function isMnePatchCandidateEnvelope(
  value: unknown,
  schema: "xg5000.mne-instruction-batch-patch-candidate/1.0",
): value is MneInstructionBatchPatchCandidateReceipt;
function isMnePatchCandidateEnvelope(
  value: unknown,
  schema: "xg5000.mne-rung-patch-candidate/1.0",
): value is MneRungPatchCandidateReceipt;
function isMnePatchCandidateEnvelope(
  value: unknown,
  schema:
    | "xg5000.mne-series-guard-candidate/1.0"
    | "xg5000.mne-instruction-patch-candidate/1.0"
    | "xg5000.mne-instruction-batch-patch-candidate/1.0"
    | "xg5000.mne-rung-patch-candidate/1.0",
): value is Record<string, unknown> {
  const patchKey = schema.endsWith("batch-patch-candidate/1.0")
    ? "patches"
    : "patch";
  const candidateKeys = [
    "schema", "projectSha256", "programId", "programName",
    "baselineMneSha256", "baselineLdSha256", "baselineLdCanonicalSha256", "baselineInstructionCount",
    patchKey, "targetInstructionCount", "targetMneSha256", "designDigest",
    "targetProgramDigest", ...(schema === "xg5000.mne-rung-patch-candidate/1.0"
      ? ["simulationProfile"] : []), "candidateDigest",
  ];
  if (
    !isRecord(value) ||
    !(
      hasExactKeys(value, candidateKeys) ||
      hasExactKeys(value, candidateKeys.filter(
        (key) => key !== "baselineLdCanonicalSha256",
      ))
    )
  ) {
    return false;
  }
  return (
    value.schema === schema &&
    typeof value.projectSha256 === "string" &&
    SHA256.test(value.projectSha256) &&
    typeof value.programId === "string" &&
    value.programId.length > 0 &&
    value.programId.length <= 512 &&
    value.programId === value.programId.trim() &&
    typeof value.programName === "string" &&
    value.programName.length > 0 &&
    value.programName.length <= 80 &&
    value.programName === value.programName.trim() &&
    [
      value.baselineMneSha256,
      value.baselineLdSha256,
      ...(value.baselineLdCanonicalSha256 === undefined
        ? []
        : [value.baselineLdCanonicalSha256]),
      value.targetMneSha256,
      value.designDigest,
      value.targetProgramDigest,
      value.candidateDigest,
    ].every((digest) => typeof digest === "string" && SHA256.test(digest)) &&
    typeof value.baselineInstructionCount === "number" &&
    Number.isSafeInteger(value.baselineInstructionCount) &&
    value.baselineInstructionCount >= 2 &&
    value.baselineInstructionCount <= 4_096 &&
    typeof value.targetInstructionCount === "number" &&
    Number.isSafeInteger(value.targetInstructionCount) &&
    value.targetInstructionCount >= 2 &&
    value.targetInstructionCount <= 4_096
  );
}

function isApprovedBaselineForCandidate(
  value: unknown,
  candidate:
    | MneSeriesGuardCandidateReceipt
    | MneInstructionPatchCandidateReceipt
    | MneInstructionBatchPatchCandidateReceipt
    | MneRungPatchCandidateReceipt,
): boolean {
  return (
    isRecord(value) &&
    (hasExactKeys(value, [
      "schema",
      "projectSha256",
      "programId",
      "programName",
      "ldPayloadSha256",
    ]) || hasExactKeys(value, [
      "schema",
      "projectSha256",
      "programId",
      "programName",
      "ldPayloadSha256",
      "ldCanonicalSha256",
    ])) &&
    value.schema === "controlstudio-mne-approved-baseline/v1" &&
    value.projectSha256 === candidate.projectSha256 &&
    value.programId === candidate.programId &&
    value.programName === candidate.programName &&
    (candidate.baselineLdCanonicalSha256 === undefined
      ? value.ldPayloadSha256 === candidate.baselineLdSha256
      : value.ldCanonicalSha256 === candidate.baselineLdCanonicalSha256)
  );
}

function isMneInstructionPatchRow(
  value: unknown,
  allowEnd = false,
): value is MneInstructionPatchRow {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["index", "command", "operand", "instructionDigest"]) &&
    typeof value.index === "number" &&
    Number.isSafeInteger(value.index) &&
    value.index >= 0 &&
    isCanonicalMneInstruction({ command: value.command, operand: value.operand }) &&
    (allowEnd || value.command !== "END") &&
    typeof value.instructionDigest === "string" &&
    SHA256.test(value.instructionDigest)
  );
}

function isMneInstructionPatchValue(
  patch: unknown,
  baselineInstructionCount: number,
): patch is MneInstructionPatchCandidateReceipt["patch"] {
  return (
    isRecord(patch) &&
    hasExactKeys(patch, [
      "schema",
      "operation",
      "anchorInstructionIndex",
      "anchor",
      "instruction",
      "contextBefore",
      "contextAfter",
    ]) &&
    patch.schema === "controlstudio-mne-instruction-patch/v1" &&
    ["insert_before", "insert_after", "replace", "delete"].includes(
      String(patch.operation),
    ) &&
    typeof patch.anchorInstructionIndex === "number" &&
    Number.isSafeInteger(patch.anchorInstructionIndex) &&
    patch.anchorInstructionIndex >= 0 &&
    patch.anchorInstructionIndex < baselineInstructionCount - 1 &&
    isMneInstructionPatchRow(patch.anchor) &&
    MNE_PATCH_ANCHOR_COMMANDS.has(patch.anchor.command) &&
    patch.anchor.index === patch.anchorInstructionIndex &&
    (patch.contextBefore === null
      ? patch.anchorInstructionIndex === 0
      : isMneInstructionPatchRow(patch.contextBefore, true) &&
        patch.contextBefore.index === patch.anchorInstructionIndex - 1) &&
    patch.contextAfter !== null &&
    isMneInstructionPatchRow(patch.contextAfter, true) &&
    patch.contextAfter.index === patch.anchorInstructionIndex + 1 &&
    (patch.operation === "delete") === (patch.instruction === null) &&
    (!MNE_BOOLEAN_CONTACT_COMMANDS.has(patch.anchor.command) ||
      (patch.operation === "replace" &&
        patch.instruction !== null &&
        isRecord(patch.instruction) &&
        typeof patch.instruction.command === "string" &&
        MNE_BOOLEAN_CONTACT_COMMANDS.has(patch.instruction.command))) &&
    (patch.instruction === null ||
      (isRecord(patch.instruction) &&
        isCanonicalMneInstruction(patch.instruction) &&
        patch.instruction.command !== "END"))
  );
}

function instructionPatchDelta(
  patch: MneInstructionPatchCandidateReceipt["patch"],
): number {
  return patch.operation === "delete"
    ? -1
    : patch.operation === "replace"
      ? 0
      : 1;
}

export function isMneInstructionPatchPlan(
  value: unknown,
): value is MneInstructionPatchChangePlanV4 {
  if (!isMneCandidatePlanEnvelope(value, "mne_instruction_patch")) {
    return false;
  }
  const rawCandidate = value.candidate;
  if (
    !isMnePatchCandidateEnvelope(
      rawCandidate,
      "xg5000.mne-instruction-patch-candidate/1.0",
    )
  ) {
    return false;
  }
  const candidate = rawCandidate;
  const patch = candidate.patch;
  if (!isMneInstructionPatchValue(patch, candidate.baselineInstructionCount)) {
    return false;
  }
  if (
    candidate.targetInstructionCount !==
    candidate.baselineInstructionCount + instructionPatchDelta(patch)
  ) {
    return false;
  }
  return isApprovedBaselineForCandidate(value.baselineBinding, candidate);
}

export function isMneInstructionBatchPatchPlan(
  value: unknown,
): value is MneInstructionBatchPatchChangePlanV5 {
  if (!isMneCandidatePlanEnvelope(value, "mne_instruction_batch_patch")) {
    return false;
  }
  const rawCandidate = value.candidate;
  if (
    !isMnePatchCandidateEnvelope(
      rawCandidate,
      "xg5000.mne-instruction-batch-patch-candidate/1.0",
    )
  ) {
    return false;
  }
  const candidate = rawCandidate;
  if (
    candidate.patches.length < 2 ||
    candidate.patches.length > 16 ||
    !candidate.patches.every((patch) =>
      isMneInstructionPatchValue(patch, candidate.baselineInstructionCount),
    )
  ) {
    return false;
  }
  const anchorIndexes = candidate.patches.map(
    (patch) => patch.anchorInstructionIndex,
  );
  if (
    anchorIndexes.some(
      (index, position) => position > 0 && index <= anchorIndexes[position - 1],
    ) ||
    candidate.targetInstructionCount !==
      candidate.baselineInstructionCount +
        candidate.patches.reduce(
          (total, patch) => total + instructionPatchDelta(patch),
          0,
        )
  ) {
    return false;
  }
  return isApprovedBaselineForCandidate(value.baselineBinding, candidate);
}

function isMneRungCondition(
  value: unknown,
  depth = 1,
): value is MneRungCondition {
  if (!isRecord(value)) {
    return false;
  }
  if (value.kind === "contact") {
    return (
      hasExactKeys(value, ["kind", "operand", "state"]) &&
      (value.state === "on" || value.state === "off") &&
      isCanonicalMneInstruction({
        command: value.state === "off" ? "LOAD NOT" : "LOAD",
        operand: value.operand,
      })
    );
  }
  return (
    depth <= 8 &&
    (value.kind === "all_of" || value.kind === "any_of") &&
    hasExactKeys(value, ["kind", "terms"]) &&
    Array.isArray(value.terms) &&
    value.terms.length >= 2 &&
    value.terms.length <= 20 &&
    value.terms.every((term) => isMneRungCondition(term, depth + 1))
  );
}

function mneRungConditionInstructionCount(value: MneRungCondition): number {
  if (value.kind === "contact") {
    return 1;
  }
  return value.terms.slice(1).reduce(
    (count, term) =>
      count +
      (term.kind === "contact"
        ? 1
        : mneRungConditionInstructionCount(term) + 1),
    mneRungConditionInstructionCount(value.terms[0]),
  );
}

function sameMneRungCondition(
  left: MneRungCondition,
  right: MneRungCondition,
): boolean {
  if (left.kind !== right.kind) {
    return false;
  }
  if (left.kind === "contact" && right.kind === "contact") {
    return left.operand === right.operand && left.state === right.state;
  }
  if (left.kind === "contact" || right.kind === "contact") {
    return false;
  }
  return (
    left.terms.length === right.terms.length &&
    left.terms.every((term, index) =>
      sameMneRungCondition(term, right.terms[index]),
    )
  );
}

function isRungAction(value: unknown): boolean {
  return (
    isCanonicalMneInstruction(value) &&
    isRecord(value) &&
    ["OUT", "SET", "RST", "TON"].includes(String(value.command))
  );
}

function isMneRungBaseline(value: unknown, baselineCount: number): boolean {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "startInstructionIndex",
      "conditionInstructionCount",
      "actionInstructionCount",
      "condition",
      "actions",
      "rungDigest",
    ]) ||
    !Number.isSafeInteger(value.startInstructionIndex) ||
    Number(value.startInstructionIndex) < 0 ||
    !Number.isSafeInteger(value.conditionInstructionCount) ||
    Number(value.conditionInstructionCount) < 1 ||
    Number(value.conditionInstructionCount) > 128 ||
    !Number.isSafeInteger(value.actionInstructionCount) ||
    Number(value.actionInstructionCount) < 1 ||
    Number(value.actionInstructionCount) > 16 ||
    !isMneRungCondition(value.condition) ||
    !Array.isArray(value.actions) ||
    value.actions.length !== value.actionInstructionCount ||
    typeof value.rungDigest !== "string" ||
    !SHA256.test(value.rungDigest)
  ) {
    return false;
  }
  const actionStart =
    Number(value.startInstructionIndex) + Number(value.conditionInstructionCount);
  return (
    actionStart + value.actions.length <= baselineCount - 1 &&
    value.actions.every(
      (action, index) =>
        isMneInstructionPatchRow(action) &&
        isRungAction(action) &&
        action.index === actionStart + index,
    )
  );
}

function isMneRungTarget(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["condition", "actions", "rungDigest"]) &&
    isMneRungCondition(value.condition) &&
    mneRungConditionInstructionCount(value.condition) <= 128 &&
    Array.isArray(value.actions) &&
    value.actions.length >= 1 &&
    value.actions.length <= 16 &&
    value.actions.every(isRungAction) &&
    typeof value.rungDigest === "string" &&
    SHA256.test(value.rungDigest)
  );
}

function sameRungActions(left: unknown[], right: unknown[]): boolean {
  return left.every(
    (item, index) =>
      isRecord(item) &&
      isRecord(right[index]) &&
      item.command === right[index].command &&
      item.operand === right[index].operand,
  );
}

function isMneRungXgSimProfile(value: unknown, targetRungDigest: unknown): boolean {
  const commonKeys = [
    "schema", "output_addresses", "target_rung_digest", "program_count",
    "catalog_digest", "profile_digest",
  ];
  if (
    !isRecord(value) ||
    value.schema !== "controlstudio-mne-rung-xgsim-profile/v1" ||
    value.target_rung_digest !== targetRungDigest || value.program_count !== 1 ||
    typeof value.catalog_digest !== "string" || !SHA256.test(value.catalog_digest) ||
    typeof value.profile_digest !== "string" || !SHA256.test(value.profile_digest) ||
    !Array.isArray(value.output_addresses) || value.output_addresses.length === 0 ||
    new Set(value.output_addresses).size !== value.output_addresses.length ||
    !value.output_addresses.every((address) => typeof address === "string" && /^[PM]\d{3,4}[0-9A-F]$/.test(address))
  ) return false;
  if (hasExactKeys(value, [...commonKeys, "scenario"])) {
    const scenario = value.scenario;
    if (!isRecord(scenario) || !hasExactKeys(scenario, ["steps"]) || !Array.isArray(scenario.steps) || scenario.steps.length < 2 || scenario.steps.length > 16) return false;
    const names = new Set<string>();
    let drives: string[] | null = null;
    return scenario.steps.every((step) => {
      if (!isRecord(step) || !hasExactKeys(step, ["name", "drive", "observe"]) || typeof step.name !== "string" || !step.name.trim() || names.has(step.name) || !isRecord(step.drive) || !isRecord(step.observe)) return false;
      names.add(step.name);
      const drive = step.drive as Record<string, unknown>;
      const observe = step.observe as Record<string, unknown>;
      const driveKeys = Object.keys(drive);
      if (driveKeys.length === 0 || Object.keys(observe).length === 0 || !driveKeys.every((address) => /^[PM]\d{3,4}[0-9A-F]$/.test(address) && (drive[address] === 0 || drive[address] === 1)) || !Object.entries(observe).every(([address, bit]) => /^[PM]\d{3,4}[0-9A-F]$/.test(address) && (bit === 0 || bit === 1)) || driveKeys.some((address) => Object.prototype.hasOwnProperty.call(observe, address)) || !(value.output_addresses as string[]).every((address) => Object.prototype.hasOwnProperty.call(observe, address))) return false;
      if (drives === null) drives = driveKeys.sort();
      return drives.length === driveKeys.length && drives.every((address) => Object.prototype.hasOwnProperty.call(drive, address));
    });
  }
  if (!hasExactKeys(value, [
    ...commonKeys, "normal_assignments", "deny_assignments", "restore_assignments",
  ])) return false;
  const names = ["normal_assignments", "deny_assignments", "restore_assignments"] as const;
  const assignments = names.map((name) => value[name]);
  if (assignments.some((assignment) => !isRecord(assignment) || Object.keys(assignment).length === 0)) return false;
  const keys = Object.keys(assignments[0] as Record<string, unknown>);
  return assignments.every((assignment) => {
    const record = assignment as Record<string, unknown>;
    return Object.keys(record).length === keys.length && keys.every((key) =>
      Object.prototype.hasOwnProperty.call(record, key) && /^[PM]\d{3,4}[0-9A-F]$/.test(key) && (record[key] === 0 || record[key] === 1),
    );
  }) && !(value.output_addresses as string[]).some((address) => keys.includes(address));
}

export function isMneRungPatchPlan(
  value: unknown,
): value is MneRungPatchChangePlanV6 {
  if (!isMneCandidatePlanEnvelope(value, "mne_rung_patch")) {
    return false;
  }
  const rawCandidate = value.candidate;
  if (
    !isMnePatchCandidateEnvelope(
      rawCandidate,
      "xg5000.mne-rung-patch-candidate/1.0",
    )
  ) {
    return false;
  }
  const candidate = rawCandidate;
  const patch = candidate.patch;
  if (
    !isRecord(patch) ||
    !hasExactKeys(patch, [
      "schema",
      "operation",
      "anchor",
      "anchorRung",
      "targetRung",
    ]) ||
    patch.schema !== "controlstudio-mne-rung-patch/v1" ||
    !["replace_condition", "replace_rung", "insert_before", "delete"].includes(
      String(patch.operation),
    ) ||
    !isMneInstructionPatchRow(patch.anchor) ||
    !["OUT", "SET", "RST", "TON", "END"].includes(patch.anchor.command) ||
    patch.anchor.index >= candidate.baselineInstructionCount ||
    (patch.anchorRung !== null &&
      !isMneRungBaseline(
        patch.anchorRung,
        candidate.baselineInstructionCount,
      )) ||
    (patch.targetRung !== null && !isMneRungTarget(patch.targetRung))
  ) {
    return false;
  }
  const baseline = patch.anchorRung;
  const target = patch.targetRung;
  let expectedCount = candidate.baselineInstructionCount;
  if (patch.operation === "insert_before") {
    if (target === null || !isRecord(target)) return false;
    if (patch.anchor.command === "END") {
      if (patch.anchor.index !== candidate.baselineInstructionCount - 1) return false;
      if (
        !isRecord(baseline) ||
        Number(baseline.startInstructionIndex) +
          Number(baseline.conditionInstructionCount) +
          Number(baseline.actionInstructionCount) !==
          patch.anchor.index
      ) return false;
    } else if (!isRecord(baseline)) return false;
    expectedCount +=
      mneRungConditionInstructionCount(target.condition as MneRungCondition) +
      (target.actions as unknown[]).length;
  } else {
    if (!isRecord(baseline) || patch.anchor.command === "END") return false;
    if (
      !(baseline.actions as unknown[]).some(
        (item) =>
          isRecord(item) &&
          item.index === (patch.anchor as MneInstructionPatchRow).index &&
          item.command === (patch.anchor as MneInstructionPatchRow).command &&
          item.operand === (patch.anchor as MneInstructionPatchRow).operand,
      )
    ) return false;
    if (patch.operation === "replace_condition") {
      if (
        !isRecord(target) ||
        sameMneRungCondition(
          baseline.condition as MneRungCondition,
          target.condition as MneRungCondition,
        ) ||
        !sameRungActions(
          target.actions as unknown[],
          baseline.actions as unknown[],
        )
      ) return false;
      expectedCount +=
        mneRungConditionInstructionCount(target.condition as MneRungCondition) -
        Number(baseline.conditionInstructionCount);
    } else if (patch.operation === "replace_rung") {
      if (!isRecord(target)) return false;
      if (
        sameMneRungCondition(
          baseline.condition as MneRungCondition,
          target.condition as MneRungCondition,
        ) &&
        sameRungActions(
          target.actions as unknown[],
          baseline.actions as unknown[],
        )
      ) return false;
      expectedCount +=
        mneRungConditionInstructionCount(target.condition as MneRungCondition) +
        (target.actions as unknown[]).length -
        Number(baseline.conditionInstructionCount) -
        Number(baseline.actionInstructionCount);
    } else {
      if (target !== null) return false;
      expectedCount -=
        Number(baseline.conditionInstructionCount) +
        Number(baseline.actionInstructionCount);
    }
  }
  if (candidate.targetInstructionCount !== expectedCount) return false;
  if (
    value.verificationScope === "offline_native_reopen_xgsim" &&
    (!isRecord(target) || !isMneRungXgSimProfile(candidate.simulationProfile, target.rungDigest))
  ) return false;
  return isApprovedBaselineForCandidate(value.baselineBinding, candidate);
}

export function getStructuralMnePlan(value: unknown): StructuralMnePlan | null {
  if (isMneFullProgramReplacementPlan(value)) {
    return { kind: "full_program_replacement", plan: value };
  }
  if (isMneSeriesGuardPatchPlan(value)) {
    return { kind: "series_guard_patch", plan: value };
  }
  if (isMneInstructionPatchPlan(value)) {
    return { kind: "instruction_patch", plan: value };
  }
  if (isMneInstructionBatchPatchPlan(value)) {
    return { kind: "instruction_batch_patch", plan: value };
  }
  if (isMneRungPatchPlan(value)) {
    return { kind: "rung_patch", plan: value };
  }
  return null;
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
    value.verificationScope !== "offline_native_reopen" ||
    !Array.isArray(value.validation) ||
    value.validation.join("\u0000") !==
      [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
        "project_reopen",
      ].join("\u0000")
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

/**
 * Recovery may act only on a plan the UI can validate exactly.  Legacy LD
 * operand plans predate planKind, so their own strict validator is the
 * authoritative compatibility boundary.
 */
export function hasSupportedChangeRecoveryContract(value: unknown): boolean {
  if (isMneFullProgramReplacementPlan(value)) {
    return (
      value.verificationScope === "offline_native_reopen" &&
      value.validation.join("\u0000") ===
        [
          "structural_diff",
          "xg5000_program_check",
          "project_save",
          "project_reopen",
        ].join("\u0000")
    );
  }
  return isLegacyChangePlanV1(value);
}

export function isChangePlan(value: unknown): value is ChangePlan {
  if (!isRecord(value)) {
    return false;
  }
  if (!Object.prototype.hasOwnProperty.call(value, "planKind")) {
    return isLegacyChangePlanV1(value);
  }
  return (
    isMneFullProgramReplacementPlan(value) ||
    isMneSeriesGuardPatchPlan(value) ||
    isMneInstructionPatchPlan(value) ||
    isMneInstructionBatchPatchPlan(value) ||
    isMneRungPatchPlan(value)
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
