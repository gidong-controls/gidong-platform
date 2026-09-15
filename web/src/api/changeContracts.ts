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
const CANONICAL_UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
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

function hasExactMneExecutionScope(
  value: Record<string, unknown>,
  options: { supportsXgSim: boolean },
): boolean {
  if (
    value.requestScope !== "offline_candidate_revision" &&
    value.requestScope !== "permanent_offline_revision"
  ) {
    return false;
  }
  if (value.verificationScope === "offline_native_check_only") {
    return (
      matchesValidation(value.validation, CHECK_ONLY_VALIDATION)
    );
  }
  if (value.verificationScope === "offline_native_reopen") {
    return matchesValidation(value.validation, REOPEN_VALIDATION);
  }
  return (
    value.requestScope === "offline_candidate_revision" &&
    options.supportsXgSim &&
    value.verificationScope === "offline_native_reopen_xgsim" &&
    matchesValidation(value.validation, REOPEN_VALIDATION)
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

function isNullableBoundedText(value: unknown, maximum = 256): boolean {
  return value === null || (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maximum &&
    value === value.trim()
  );
}

function isMneFieldIoBinding(value: unknown): boolean {
  const physicalAddress = (item: unknown): item is string =>
    typeof item === "string" &&
    item.length <= 32 &&
    /^P[0-9A-F]+$/.test(item);
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "device",
      "direction",
      "pointKind",
      "module",
      "channel",
      "terminalRange",
      "bindingDigest",
    ]) ||
    !physicalAddress(value.device) ||
    (value.direction !== "input" && value.direction !== "output") ||
    (value.direction === "input") !== (value.pointKind === "digital_input") ||
    (value.direction === "output") !== (value.pointKind === "digital_output") ||
    typeof value.bindingDigest !== "string" ||
    !SHA256.test(value.bindingDigest)
  ) {
    return false;
  }

  const module = value.module;
  const channel = value.channel;
  const terminalRange = value.terminalRange;
  if (
    !isRecord(module) ||
    !hasExactKeys(module, [
      "rack_base",
      "slot",
      "module_id",
      "model",
      "module_type",
      "status",
    ]) ||
    ![module.rack_base, module.slot].every(
      (item) => typeof item === "number" &&
        Number.isSafeInteger(item) && item >= 0 && item <= 255,
    ) ||
    typeof module.module_id !== "number" ||
    !Number.isSafeInteger(module.module_id) ||
    module.module_id < 0 ||
    module.module_id > 65_535 ||
    !isNullableBoundedText(module.model, 128) ||
    (typeof module.model === "string" &&
      !/^(?:(?:XBC|XBM|XEC|XMC|XEM|XBE|XBF|XGF|XGL)-[A-Z0-9/+.-]+|(?:XGK|XGI|XGR)-CPU[A-Z0-9/+.-]*)$/i.test(
        module.model,
      )) ||
    !isNullableBoundedText(module.module_type, 128) ||
    (typeof module.module_type === "string" &&
      (/[/\\:]/.test(module.module_type) || /[\x00-\x1f]/.test(module.module_type))) ||
    module.status !== "observed" ||
    !isRecord(channel) ||
    !hasExactKeys(channel, ["index", "status", "derivation"]) ||
    typeof channel.index !== "number" ||
    !Number.isSafeInteger(channel.index) ||
    channel.index < 0 ||
    channel.index > 65_535 ||
    channel.status !== "derived_from_verified_terminal_range" ||
    channel.derivation !== "address offset within the exact configured module range" ||
    !isRecord(terminalRange) ||
    !hasExactKeys(terminalRange, [
      "address_start",
      "address_end",
      "binding_kind",
      "status",
    ]) ||
    !physicalAddress(terminalRange.address_start) ||
    !physicalAddress(terminalRange.address_end) ||
    (terminalRange.binding_kind !== "built_in_terminal_range" &&
      terminalRange.binding_kind !== "slot_reservation") ||
    (terminalRange.status !== "observed" && terminalRange.status !== "documented")
  ) {
    return false;
  }
  const deviceOffset = BigInt(`0x${value.device.slice(1)}`);
  const startOffset = BigInt(`0x${terminalRange.address_start.slice(1)}`);
  const endOffset = BigInt(`0x${terminalRange.address_end.slice(1)}`);
  return startOffset <= deviceOffset && deviceOffset <= endOffset;
}

function hasExactMneHardwareBinding(value: Record<string, unknown>): boolean {
  const hasDigest = Object.prototype.hasOwnProperty.call(
    value,
    "hardwareContextDigest",
  );
  const hasBindings = Object.prototype.hasOwnProperty.call(
    value,
    "fieldIoBindings",
  );
  if (hasDigest !== hasBindings) return false;
  if (!hasDigest) return true;
  if (value.hardwareContextDigest === null) {
    return Array.isArray(value.fieldIoBindings) && value.fieldIoBindings.length === 0;
  }
  if (
    typeof value.hardwareContextDigest !== "string" ||
    !SHA256.test(value.hardwareContextDigest) ||
    !Array.isArray(value.fieldIoBindings) ||
    value.fieldIoBindings.length === 0 ||
    value.fieldIoBindings.length > 64 ||
    !value.fieldIoBindings.every(isMneFieldIoBinding)
  ) {
    return false;
  }
  const keys = value.fieldIoBindings.map((binding) => {
    const typed = binding as Record<string, unknown>;
    return `${String(typed.device)}\u0000${String(typed.direction)}`;
  });
  return keys.every((key, index) => index === 0 || keys[index - 1] < key) &&
    new Set(keys).size === keys.length;
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
        "baselinemnesha256",
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
      normalized.startsWith("rawmne") ||
      normalized.startsWith("rawld") ||
      (!publicBaselineDigest && normalized.startsWith("baselinemne")) ||
      (!publicBaselineDigest && normalized.startsWith("baselineld")) ||
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
    !hasExactMneExecutionScope(value, { supportsXgSim: false }) ||
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
    hasExactMneExecutionScope(value, {
      supportsXgSim: planKind === "mne_rung_patch",
    }) &&
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
  // The Host's Pydantic response serializer can retain these two defaulted
  // aliases for a native-only candidate.  They are not candidate material in
  // that form (the digest omits them), so recognize only the exact inert pair
  // rather than weakening the public candidate envelope.
  const inertNativeDefaults = ["hardwareContextDigest", "fieldIoBindings"];
  const candidateKeyVariants = [
    candidateKeys,
    candidateKeys.filter((key) => key !== "baselineLdCanonicalSha256"),
  ].flatMap((keys) => [keys, [...keys, ...inertNativeDefaults]]);
  if (
    !isRecord(value) ||
    !candidateKeyVariants.some((keys) => hasExactKeys(value, keys))
  ) {
    return false;
  }
  if (!hasExactMneHardwareBinding(value)) return false;
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
    isRecord(value) &&
    isCanonicalMneInstruction({
      command: value.command,
      operand: value.operand,
    }) &&
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
    let totalHoldMs = 0;
    return scenario.steps.every((step) => {
      if (!isRecord(step) || !hasExactKeys(step, ["name", "drive", "observe", ...("hold_ms" in step ? ["hold_ms"] : [])]) || typeof step.name !== "string" || !step.name.trim() || names.has(step.name) || !isRecord(step.drive) || !isRecord(step.observe)) return false;
      const holdMs = "hold_ms" in step ? step.hold_ms : 0;
      if (typeof holdMs !== "number" || !Number.isSafeInteger(holdMs) || holdMs < 0 || holdMs > 10_000) return false;
      totalHoldMs += holdMs;
      if (totalHoldMs > 30_000) return false;
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
    !isMneInstructionPatchRow(patch.anchor, true) ||
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

/**
 * Recovery may act only on a plan the UI can validate exactly. Every typed
 * structural MNE validator owns its scope, validation order, candidate, and
 * baseline assertions. Legacy LD operand plans predate planKind, so their own
 * strict validator remains the authoritative compatibility boundary.
 */
export function hasSupportedChangeRecoveryContract(value: unknown): boolean {
  return getStructuralMnePlan(value) !== null || isLegacyChangePlanV1(value);
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

function terminalReceiptEvidence(
  transaction: ChangeTransaction,
): Record<string, unknown> | null {
  const verification = isRecord(transaction.result.verification)
    ? transaction.result.verification
    : null;
  const reconciliation = isRecord(transaction.result.reconciliation)
    ? transaction.result.reconciliation
    : null;
  const simulationFinalization = isRecord(
    transaction.result.simulation_finalization,
  )
    ? transaction.result.simulation_finalization
    : null;
  const terminalKey = transaction.state === "rolled_back"
    ? "rollback"
    : "verification";
  if (simulationFinalization && isRecord(simulationFinalization[terminalKey])) {
    return { ...(verification ?? {}), ...simulationFinalization };
  }
  if (reconciliation) {
    return reconciliation;
  }
  return verification;
}

function hasReceiptExecutionBoundary(
  transaction: ChangeTransaction,
  evidence: Record<string, unknown>,
): boolean {
  if (
    evidence.verification_scope !== transaction.plan.verificationScope ||
    !matchesValidation(evidence.executed_checks, transaction.plan.validation) ||
    !matchesValidation(evidence.manual_follow_up, transaction.plan.manualFollowUp)
  ) {
    return false;
  }
  const expectedNotExecuted = transaction.plan.verificationScope ===
    "offline_native_check_only"
    ? ["project_reopen", "xg_sim", "runtime_or_online_validation"]
    : ["xg_sim", "runtime_or_online_validation"];
  return matchesValidation(evidence.not_executed, expectedNotExecuted);
}

function nonemptyRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) && Object.keys(value).length > 0 ? value : null;
}

interface LegacyLdReceiptBinding {
  mcpTransactionId: string;
  mcpPlanId: string;
  mcpPlanDigest: string;
  projectIdentityDigest: string;
  programId: string;
  programName: string;
  tokenOffset: number;
  expectedOperand: string;
  requestedReplacement: string;
  nativeReplacement: string | null;
  payloadSha256Before: string | null;
  payloadCanonicalSha256After: string | null;
}

function firstLegacyField(
  value: Record<string, unknown>,
  ...keys: string[]
): unknown {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(value, key)) return value[key];
  }
  return undefined;
}

function legacyPlanTarget(
  transaction: ChangeTransaction,
): Omit<LegacyLdReceiptBinding, "mcpTransactionId" | "mcpPlanId" |
  "mcpPlanDigest" | "projectIdentityDigest" | "nativeReplacement" |
  "payloadSha256Before" | "payloadCanonicalSha256After"> | null {
  const plan = transaction.plan;
  if (!isLegacyChangePlanV1(plan)) return null;
  const target = plan.target;
  const current = plan.current;
  const proposed = plan.proposed;
  const programId = firstLegacyField(target, "program_id", "programId", "program");
  const programName = firstLegacyField(target, "program_name", "programName");
  const tokenOffset = firstLegacyField(target, "token_offset", "tokenOffset", "token");
  const expectedOperand = firstLegacyField(current, "value", "operand");
  const requestedReplacement = firstLegacyField(proposed, "value", "operand");
  if (
    typeof programId !== "string" || programId.length === 0 ||
    typeof programName !== "string" || programName.length === 0 ||
    !Number.isInteger(tokenOffset) || Number(tokenOffset) < 0 ||
    typeof expectedOperand !== "string" || expectedOperand.length === 0 ||
    typeof requestedReplacement !== "string" || requestedReplacement.length === 0
  ) return null;
  return {
    programId,
    programName,
    tokenOffset: Number(tokenOffset),
    expectedOperand,
    requestedReplacement,
  };
}

function hasLegacyCheckpoint(
  value: unknown,
  transaction: ChangeTransaction,
  binding: LegacyLdReceiptBinding,
  states: ReadonlySet<string>,
  requirePlan: boolean,
): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const pid = value.pid;
  const hwnd = value.hwnd;
  if (
    value.schema !== "controlstudio-change-transaction-checkpoint/v1" ||
    value.host_transaction_id !== transaction.transactionId ||
    value.transaction_id !== binding.mcpTransactionId ||
    value.workspace_id !== transaction.targetIdentity.workspaceId ||
    value.project_identity_digest !== binding.projectIdentityDigest ||
    typeof value.active_session_id !== "string" || value.active_session_id.length === 0 ||
    !Number.isInteger(pid) || Number(pid) <= 0 ||
    !Number.isInteger(hwnd) || Number(hwnd) <= 0 ||
    value.project_path !== transaction.targetIdentity.projectPath ||
    typeof value.current_project_hash !== "string" || !SHA256.test(value.current_project_hash) ||
    typeof value.state !== "string" || !states.has(value.state)
  ) return false;
  if (!requirePlan) return true;
  if (
    value.plan_id !== binding.mcpPlanId ||
    value.plan_digest !== binding.mcpPlanDigest ||
    value.program_id !== binding.programId ||
    value.program_name !== binding.programName ||
    value.token_offset !== binding.tokenOffset ||
    value.expected !== binding.expectedOperand ||
    value.replacement !== binding.requestedReplacement
  ) return false;
  if (binding.payloadCanonicalSha256After === null) {
    return value.payload_canonical_sha256_planned === undefined &&
      value.native_replacement === undefined;
  }
  return value.payload_canonical_sha256_planned ===
      binding.payloadCanonicalSha256After &&
    value.native_replacement === binding.nativeReplacement;
}

function hasLegacyVerifiedOperation(
  value: unknown,
  operation: string,
  transaction: ChangeTransaction,
  binding: LegacyLdReceiptBinding,
  states: ReadonlySet<string>,
  requirePlan = true,
): value is Record<string, unknown> {
  const receipt = nonemptyRecord(value);
  return Boolean(
    receipt && receipt.ok === true && receipt.verified === true &&
      receipt.operation === operation &&
      receipt.transaction_id === binding.mcpTransactionId &&
      typeof receipt.transaction_state === "string" &&
      states.has(receipt.transaction_state) &&
      hasLegacyCheckpoint(
        receipt.transaction_checkpoint,
        transaction,
        binding,
        new Set([String(receipt.transaction_state)]),
        requirePlan,
      ),
  );
}

function exactLegacyLdDiff(
  value: unknown,
  binding: LegacyLdReceiptBinding,
  nativeReplacement: string,
  expectedBefore: string | null,
): { before: string; after: string } | null {
  if (!isRecord(value) || !hasExactKeys(value, [
    "program", "payload_sha256_before", "payload_sha256_after",
    "payload_size_before", "payload_size_after", "byte_length_unchanged",
    "change_count", "changes",
  ])) return null;
  const changes = value.changes;
  if (
    value.program !== binding.programName ||
    typeof value.payload_sha256_before !== "string" ||
    !SHA256.test(value.payload_sha256_before) ||
    typeof value.payload_sha256_after !== "string" ||
    !SHA256.test(value.payload_sha256_after) ||
    (expectedBefore !== null && value.payload_sha256_before !== expectedBefore) ||
    !Number.isInteger(value.payload_size_before) || Number(value.payload_size_before) <= 0 ||
    value.payload_size_after !== value.payload_size_before ||
    value.byte_length_unchanged !== true || value.change_count !== 1 ||
    !Array.isArray(changes) || changes.length !== 1 ||
    !isRecord(changes[0]) || !hasExactKeys(changes[0], ["token_offset", "before", "after"]) ||
    changes[0].token_offset !== binding.tokenOffset ||
    changes[0].before !== binding.expectedOperand ||
    changes[0].after !== nativeReplacement
  ) return null;
  return {
    before: value.payload_sha256_before,
    after: value.payload_sha256_after,
  };
}

function legacyApplyBinding(
  transaction: ChangeTransaction,
): LegacyLdReceiptBinding | null {
  const target = legacyPlanTarget(transaction);
  const envelope = nonemptyRecord(transaction.result.apply);
  if (!target || !envelope) return null;
  const plan = nonemptyRecord(envelope.mcp_plan);
  const applied = nonemptyRecord(envelope.apply);
  const projectIdentityDigest = envelope.project_identity_digest;
  if (
    !plan || !applied ||
    envelope.host_transaction_id !== transaction.transactionId ||
    envelope.host_plan_digest !== transaction.planDigest ||
    typeof envelope.mcp_transaction_id !== "string" || envelope.mcp_transaction_id.length === 0 ||
    typeof envelope.mcp_plan_id !== "string" || envelope.mcp_plan_id.length === 0 ||
    typeof envelope.mcp_plan_digest !== "string" || !SHA256.test(envelope.mcp_plan_digest) ||
    typeof projectIdentityDigest !== "string" || !SHA256.test(projectIdentityDigest) ||
    plan.transaction_id !== envelope.mcp_transaction_id ||
    plan.plan_id !== envelope.mcp_plan_id || plan.plan_digest !== envelope.mcp_plan_digest ||
    plan.workspace_id !== transaction.targetIdentity.workspaceId ||
    plan.project_identity_digest !== projectIdentityDigest ||
    plan.session_id !== transaction.targetIdentity.engineeringSessionId ||
    plan.project_hash !== transaction.targetIdentity.artifactHash ||
    plan.program_id !== target.programId || plan.program_name !== target.programName ||
    plan.token_offset !== target.tokenOffset || plan.expected !== target.expectedOperand ||
    plan.replacement !== target.requestedReplacement ||
    typeof plan.native_replacement !== "string" || plan.native_replacement.length === 0 ||
    typeof plan.payload_sha256_before !== "string" || !SHA256.test(plan.payload_sha256_before) ||
    typeof plan.payload_sha256_planned !== "string" || !SHA256.test(plan.payload_sha256_planned) ||
    typeof plan.payload_canonical_sha256_planned !== "string" ||
    !SHA256.test(plan.payload_canonical_sha256_planned)
  ) return null;
  const binding: LegacyLdReceiptBinding = {
    ...target,
    mcpTransactionId: envelope.mcp_transaction_id,
    mcpPlanId: envelope.mcp_plan_id,
    mcpPlanDigest: envelope.mcp_plan_digest,
    projectIdentityDigest,
    nativeReplacement: plan.native_replacement as string,
    payloadSha256Before: plan.payload_sha256_before,
    payloadCanonicalSha256After: plan.payload_canonical_sha256_planned,
  };
  if (
    !hasLegacyVerifiedOperation(
      envelope.snapshot, "project.snapshot", transaction, binding,
      new Set(["SNAPSHOT_CREATED"]), false,
    ) ||
    !hasLegacyVerifiedOperation(
      envelope.backup, "project.backup", transaction, binding,
      new Set(["BACKUP_CREATED"]), false,
    ) ||
    !hasLegacyVerifiedOperation(
      applied, "program.apply_ld_patch", transaction, binding,
      new Set(["APPLIED"]), true,
    ) ||
    applied.plan_id !== binding.mcpPlanId || applied.matches_plan !== true ||
    applied.matches_native_plan !== true || applied.project_unchanged !== true ||
    applied.payload_sha256_before !== binding.payloadSha256Before ||
    applied.payload_canonical_sha256_after !== binding.payloadCanonicalSha256After
  ) return null;
  const diff = exactLegacyLdDiff(
    applied.diff, binding, binding.nativeReplacement as string,
    binding.payloadSha256Before,
  );
  if (!diff || applied.payload_sha256_after !== diff.after) return null;
  return binding;
}

function legacyLookupBinding(
  transaction: ChangeTransaction,
  value: unknown,
): LegacyLdReceiptBinding | null {
  const target = legacyPlanTarget(transaction);
  const lookup = nonemptyRecord(value);
  const checkpoint = lookup && nonemptyRecord(lookup.transaction_checkpoint);
  if (
    !target || !lookup || !checkpoint || lookup.ok !== true ||
    lookup.verified !== true || lookup.operation !== "change.transaction_lookup" ||
    lookup.found !== true || lookup.host_transaction_id !== transaction.transactionId ||
    typeof checkpoint.transaction_id !== "string" || checkpoint.transaction_id.length === 0 ||
    typeof checkpoint.plan_id !== "string" || checkpoint.plan_id.length === 0 ||
    typeof checkpoint.plan_digest !== "string" || !SHA256.test(checkpoint.plan_digest) ||
    typeof checkpoint.project_identity_digest !== "string" ||
    !SHA256.test(checkpoint.project_identity_digest)
  ) return null;
  const canonical = checkpoint.payload_canonical_sha256_planned;
  const nativeReplacement = checkpoint.native_replacement;
  const legacy = canonical === undefined && nativeReplacement === undefined;
  if (!legacy && (
    typeof canonical !== "string" || !SHA256.test(canonical) ||
    typeof nativeReplacement !== "string" || nativeReplacement.length === 0
  )) return null;
  const binding: LegacyLdReceiptBinding = {
    ...target,
    mcpTransactionId: checkpoint.transaction_id,
    mcpPlanId: checkpoint.plan_id,
    mcpPlanDigest: checkpoint.plan_digest,
    projectIdentityDigest: checkpoint.project_identity_digest,
    nativeReplacement: legacy ? null : nativeReplacement as string,
    payloadSha256Before: null,
    payloadCanonicalSha256After: legacy ? null : canonical as string,
  };
  if (!hasLegacyCheckpoint(
    checkpoint, transaction, binding, new Set([String(checkpoint.state)]), true,
  )) return null;
  const stored = transaction.result.native_checkpoint;
  if (stored !== undefined) {
    if (!isRecord(stored)) return null;
    const pairs: Array<[unknown, unknown]> = [
      [stored.host_transaction_id, transaction.transactionId],
      [stored.host_plan_digest, transaction.planDigest],
      [stored.mcp_transaction_id, binding.mcpTransactionId],
      [stored.mcp_plan_id, binding.mcpPlanId],
      [stored.mcp_plan_digest, binding.mcpPlanDigest],
      [stored.workspace_id, transaction.targetIdentity.workspaceId],
      [stored.project_identity_digest, binding.projectIdentityDigest],
      [stored.program_id, binding.programId],
      [stored.program_name, binding.programName],
      [stored.token_offset, binding.tokenOffset],
      [stored.expected, binding.expectedOperand],
      [stored.replacement, binding.requestedReplacement],
      [stored.native_replacement, binding.nativeReplacement ?? undefined],
      [stored.payload_canonical_sha256_planned,
        binding.payloadCanonicalSha256After ?? undefined],
    ];
    if (pairs.some(([actual, expected]) => actual !== expected)) return null;
  }
  return binding;
}

function legacyStatusScope(
  value: unknown,
  transaction: ChangeTransaction,
  binding: LegacyLdReceiptBinding,
  states: ReadonlySet<string>,
): { sessionId: string; pid: number; hwnd: number; projectHash: string } | null {
  const receipt = nonemptyRecord(value);
  const native = receipt && nonemptyRecord(receipt.transaction);
  const checkpoint = receipt && nonemptyRecord(receipt.transaction_checkpoint);
  if (
    !receipt || !native || !checkpoint || receipt.ok !== true ||
    receipt.verified !== true || receipt.operation !== "change.transaction_status" ||
    native.transaction_id !== binding.mcpTransactionId ||
    typeof native.state !== "string" || !states.has(native.state) ||
    typeof native.active_session_id !== "string" || native.active_session_id.length === 0 ||
    !Number.isInteger(native.active_pid) || Number(native.active_pid) <= 0 ||
    !Number.isInteger(native.active_hwnd) || Number(native.active_hwnd) <= 0 ||
    native.project_path !== transaction.targetIdentity.projectPath ||
    typeof native.current_project_hash !== "string" || !SHA256.test(native.current_project_hash) ||
    !hasLegacyCheckpoint(
      checkpoint, transaction, binding, new Set([native.state]), true,
    ) ||
    checkpoint.active_session_id !== native.active_session_id ||
    checkpoint.pid !== native.active_pid || checkpoint.hwnd !== native.active_hwnd ||
    checkpoint.current_project_hash !== native.current_project_hash
  ) return null;
  return {
    sessionId: native.active_session_id,
    pid: Number(native.active_pid),
    hwnd: Number(native.active_hwnd),
    projectHash: native.current_project_hash,
  };
}

function legacyDiffReceipt(
  value: unknown,
  transaction: ChangeTransaction,
  binding: LegacyLdReceiptBinding,
  reconciliation: boolean,
): {
  before: string;
  after: string;
  canonical: string | null;
  nativeReplacement: string;
} | null {
  if (!hasLegacyVerifiedOperation(
    value, "project.diff", transaction, binding, new Set(["DIFF_VERIFIED"]),
  )) return null;
  const receipt = value;
  if (
    receipt.plan_id !== binding.mcpPlanId || receipt.matches_plan !== true ||
    receipt.matches_token_plan !== true || receipt.matches_native_plan !== true ||
    receipt.project_unchanged !== true ||
    receipt.requested_replacement !== binding.requestedReplacement ||
    typeof receipt.native_replacement !== "string" || receipt.native_replacement.length === 0 ||
    (binding.nativeReplacement !== null &&
      receipt.native_replacement !== binding.nativeReplacement) ||
    receipt.native_contract_reconciliation !== reconciliation
  ) return null;
  const diff = exactLegacyLdDiff(
    receipt.diff, binding, receipt.native_replacement, binding.payloadSha256Before,
  );
  if (!diff) return null;
  const canonical = receipt.payload_canonical_sha256_after;
  if (binding.payloadCanonicalSha256After === null) {
    if (canonical !== undefined) return null;
    return { ...diff, canonical: null, nativeReplacement: receipt.native_replacement };
  }
  if (canonical !== binding.payloadCanonicalSha256After) return null;
  return {
    ...diff,
    canonical: binding.payloadCanonicalSha256After,
    nativeReplacement: receipt.native_replacement,
  };
}

function legacyValidationReceipt(
  value: unknown,
  transaction: ChangeTransaction,
  binding: LegacyLdReceiptBinding,
  diff: {
    before: string;
    after: string;
    canonical: string | null;
    nativeReplacement: string;
  },
): boolean {
  if (!hasLegacyVerifiedOperation(
    value, "project.validate_change", transaction, binding, new Set(["CHECKED"]),
  )) return false;
  const receipt = value;
  const programCheck = nonemptyRecord(receipt.program_check);
  const readback = exactLegacyLdDiff(
    receipt.readback_diff,
    binding,
    diff.nativeReplacement,
    diff.before,
  );
  return Boolean(
    programCheck && programCheck.ok === true && programCheck.verified === true &&
      receipt.plan_id === binding.mcpPlanId && receipt.matches_native_plan === true &&
      receipt.status === "validated" && receipt.rollback === null &&
      receipt.project_unchanged === true && readback &&
      readback.before === diff.before && readback.after === diff.after &&
      (diff.canonical === null
        ? receipt.payload_canonical_sha256_after === undefined
        : receipt.payload_canonical_sha256_after === diff.canonical),
  );
}

function hasValidLegacyTerminalReceipt(
  transaction: ChangeTransaction,
  evidence: Record<string, unknown>,
): boolean {
  if (
    !isLegacyChangePlanV1(transaction.plan) ||
    transaction.approval?.decision !== "approve" ||
    transaction.approval.planDigest !== transaction.planDigest ||
    !SHA256.test(transaction.planDigest)
  ) {
    return false;
  }
  const reconciliation = nonemptyRecord(transaction.result.reconciliation);
  const applyBinding = legacyApplyBinding(transaction);
  const lookupBinding = reconciliation
    ? legacyLookupBinding(transaction, evidence.lookup)
    : null;
  const binding = applyBinding ?? lookupBinding;
  if (!binding || (reconciliation && !lookupBinding)) return false;
  if (applyBinding && lookupBinding) {
    const exact: Array<keyof LegacyLdReceiptBinding> = [
      "mcpTransactionId", "mcpPlanId", "mcpPlanDigest",
      "projectIdentityDigest", "programId", "programName", "tokenOffset",
      "expectedOperand", "requestedReplacement", "nativeReplacement",
      "payloadCanonicalSha256After",
    ];
    if (exact.some((key) => applyBinding[key] !== lookupBinding[key])) return false;
  }

  if (transaction.state === "completed") {
    const sourceStatus = reconciliation
      ? legacyStatusScope(
        evidence.status, transaction, binding, new Set(["QUARANTINED"]),
      )
      : {
        sessionId: transaction.targetIdentity.engineeringSessionId,
        pid: transaction.targetIdentity.engineeringProcessId,
        hwnd: transaction.targetIdentity.engineeringWindowHandle,
        projectHash: transaction.targetIdentity.artifactHash,
      };
    if (
      !sourceStatus ||
      typeof sourceStatus.sessionId !== "string" || sourceStatus.sessionId.length === 0 ||
      !Number.isInteger(sourceStatus.pid) || Number(sourceStatus.pid) <= 0 ||
      !Number.isInteger(sourceStatus.hwnd) || Number(sourceStatus.hwnd) <= 0
    ) return false;
    const diffKey = reconciliation ? "native_contract_reconciliation" : "diff";
    const diff = legacyDiffReceipt(
      evidence[diffKey], transaction, binding, Boolean(reconciliation),
    );
    if (
      !diff || !legacyValidationReceipt(
        evidence.validation, transaction, binding, diff,
      ) ||
      !hasLegacyVerifiedOperation(
        evidence.save, "project.commit_save", transaction, binding,
        new Set(["SAVED"]),
      )
    ) {
      return false;
    }
    const save = evidence.save;
    if (
      save.project_path !== transaction.targetIdentity.projectPath ||
      typeof save.project_hash !== "string" || !SHA256.test(save.project_hash)
    ) return false;
    const savedStatus = legacyStatusScope(
      evidence.saved_transaction_status, transaction, binding, new Set(["SAVED"]),
    );
    if (
      !savedStatus || savedStatus.projectHash !== save.project_hash ||
      savedStatus.sessionId !== sourceStatus.sessionId ||
      savedStatus.pid !== sourceStatus.pid || savedStatus.hwnd !== sourceStatus.hwnd
    ) return false;
    let commitSessionId = savedStatus.sessionId;
    let commitPid = savedStatus.pid;
    let commitHwnd = savedStatus.hwnd;
    if (transaction.plan.verificationScope === "offline_native_check_only") {
      if (
        Object.prototype.hasOwnProperty.call(evidence, "fresh_reopen") ||
        Object.prototype.hasOwnProperty.call(evidence, "fresh_reopen_attach")
      ) return false;
    } else {
      if (!hasLegacyVerifiedOperation(
        evidence.fresh_reopen, "project.verify_ld_operand_reopen", transaction,
        binding, new Set(["SAVED"]),
      )) return false;
      const reopen = evidence.fresh_reopen;
      const readback = nonemptyRecord(reopen.program_readback);
      const fresh = nonemptyRecord(reopen.fresh_reopen);
      const attached = nonemptyRecord(evidence.fresh_reopen_attach);
      const attachedSession = attached && isRecord(attached.session)
        ? attached.session
        : attached;
      if (
        !readback || !fresh || !attachedSession || readback.program_id !== binding.programId ||
        readback.program_name !== binding.programName || readback.stable_repeat !== true ||
        readback.final_view !== "LD" ||
        (diff.canonical !== null && readback.ld_canonical_sha256 !== diff.canonical) ||
        fresh.source_pid !== transaction.targetIdentity.engineeringProcessId ||
        fresh.source_hwnd !== transaction.targetIdentity.engineeringWindowHandle ||
        fresh.source_closed !== true || fresh.workspace_id !== transaction.targetIdentity.workspaceId ||
        fresh.project_path !== transaction.targetIdentity.projectPath ||
        fresh.project_sha256 !== save.project_hash ||
        attachedSession.pid !== fresh.pid || attachedSession.hwnd !== fresh.hwnd ||
        attachedSession.project_path !== transaction.targetIdentity.projectPath ||
        attachedSession.project_hash !== save.project_hash ||
        typeof attachedSession.session_id !== "string" || attachedSession.session_id.length === 0
      ) return false;
      commitSessionId = attachedSession.session_id;
      commitPid = Number(attachedSession.pid);
      commitHwnd = Number(attachedSession.hwnd);
    }
    if (!hasLegacyVerifiedOperation(
      evidence.commit, "change.commit", transaction, binding,
      new Set(["COMMITTED"]),
    )) return false;
    const commit = evidence.commit;
    const commitCheckpoint = nonemptyRecord(commit.transaction_checkpoint);
    if (!commitCheckpoint) return false;
    return commit.project_hash === save.project_hash &&
      commit.project_unchanged === true &&
      commitCheckpoint.active_session_id === commitSessionId &&
      commitCheckpoint.pid === commitPid && commitCheckpoint.hwnd === commitHwnd &&
      commitCheckpoint.terminal === true &&
      commitCheckpoint.recovery_action === "already_terminal" &&
      commitCheckpoint.lease_status === "released";
  }

  const prefixKeys = ["diff", "validation", "save", "saved_transaction_status"];
  let gap = false;
  for (const key of prefixKeys) {
    if (evidence[key] === undefined) gap = true;
    else if (gap || nonemptyRecord(evidence[key]) === null) return false;
  }
  const statusKey = reconciliation ? "status" : "recovery_transaction_status";
  const recoveryStatus = legacyStatusScope(
    evidence[statusKey], transaction, binding,
    new Set(["APPLYING", "APPLIED", "DIFF_VERIFIED", "CHECKED", "SAVED", "QUARANTINED"]),
  );
  if (!recoveryStatus) return false;
  let prefixDiff: ReturnType<typeof legacyDiffReceipt> = null;
  if (evidence.diff !== undefined) {
    prefixDiff = legacyDiffReceipt(
      evidence.diff, transaction, binding, Boolean(reconciliation),
    );
    if (!prefixDiff) return false;
  }
  if (evidence.validation !== undefined && (
    !prefixDiff || !legacyValidationReceipt(
      evidence.validation, transaction, binding, prefixDiff,
    )
  )) return false;
  if (evidence.save !== undefined) {
    if (!hasLegacyVerifiedOperation(
      evidence.save, "project.commit_save", transaction, binding,
      new Set(["SAVED"]),
    )) return false;
    const save = evidence.save;
    if (
      typeof save.project_hash !== "string" || !SHA256.test(save.project_hash) ||
      save.project_path !== transaction.targetIdentity.projectPath ||
      (evidence.saved_transaction_status !== undefined &&
        legacyStatusScope(
          evidence.saved_transaction_status, transaction, binding, new Set(["SAVED"]),
        )?.projectHash !== save.project_hash)
    ) return false;
  }
  if (!hasLegacyVerifiedOperation(
    evidence.rollback, "project.rollback_change", transaction, binding,
    new Set(["ROLLED_BACK_EDITOR"]),
  )) return false;
  const rollback = evidence.rollback;
  if (
    rollback.plan_id !== binding.mcpPlanId || rollback.status !== "rolled_back" ||
    rollback.matches_original_canonical !== true ||
    rollback.matches_original_logic_and_nonvolatile !== true
  ) return false;
  if (!hasLegacyVerifiedOperation(
    evidence.rollback_save, "project.commit_save", transaction, binding,
    new Set(["ROLLED_BACK_SAVED"]),
  )) return false;
  const rollbackSave = evidence.rollback_save;
  const rollbackCheckpoint = nonemptyRecord(
    rollbackSave.transaction_checkpoint,
  );
  if (!rollbackCheckpoint) return false;
  if (
    rollbackSave.project_path !== transaction.targetIdentity.projectPath ||
    typeof rollbackSave.project_hash !== "string" || !SHA256.test(rollbackSave.project_hash) ||
    rollbackCheckpoint.terminal !== true ||
    rollbackCheckpoint.recovery_action !== "already_terminal" ||
    rollbackCheckpoint.lease_status !== "released"
  ) return false;
  if (!reconciliation) return true;
  const attached = nonemptyRecord(evidence.recovery_attach);
  const attachedSession = attached && isRecord(attached.session)
    ? attached.session
    : attached;
  const finalStatus = legacyStatusScope(
    evidence.final_status, transaction, binding, new Set(["ROLLED_BACK_SAVED"]),
  );
  return Boolean(
    attachedSession && finalStatus &&
      attachedSession.session_id === recoveryStatus.sessionId &&
      attachedSession.pid === recoveryStatus.pid && attachedSession.hwnd === recoveryStatus.hwnd &&
      attachedSession.project_path === transaction.targetIdentity.projectPath &&
      attachedSession.project_hash === recoveryStatus.projectHash &&
      finalStatus.sessionId === recoveryStatus.sessionId &&
      finalStatus.pid === recoveryStatus.pid && finalStatus.hwnd === recoveryStatus.hwnd &&
      finalStatus.projectHash === rollbackSave.project_hash,
  );
}

function hasIndependentMneReopen(value: unknown): boolean {
  return (
    isRecord(value) &&
    value.verified === true &&
    value.process_is_independent === true &&
    value.stable_readback === true &&
    typeof value.ld_canonical_sha256 === "string" &&
    SHA256.test(value.ld_canonical_sha256) &&
    typeof value.project_sha256 === "string" &&
    SHA256.test(value.project_sha256)
  );
}

const MNE_PUBLIC_CHECKPOINT_FIELDS = new Set([
  "schema",
  "host_transaction_id",
  "transaction_id",
  "snapshot_id",
  "snapshot_digest",
  "artifact_visibility",
  "backup_id",
  "plan_id",
  "plan_digest",
  "plan_kind",
  "workspace_id",
  "project_identity_digest",
  "active_session_id",
  "pid",
  "hwnd",
  "project_path",
  "current_project_hash",
  "state",
  "program_id",
  "program_name",
  "baseline_digest",
  "before_mne_sha256",
  "target_mne_sha256",
  "adapter_binding_digest",
  "runtime_identity_digest",
  "target_program_digest",
  "rollback_ld_sha256",
  "simulation_receipt_digest",
  "lease_generation",
  "mutation_possible",
  "recovery_action",
  "terminal",
  "lease_status",
]);
const MNE_CHECKPOINT_REQUIRED_FIELDS = [
  "schema",
  "host_transaction_id",
  "transaction_id",
  "snapshot_id",
  "snapshot_digest",
  "artifact_visibility",
  "plan_kind",
  "workspace_id",
  "project_identity_digest",
  "active_session_id",
  "pid",
  "hwnd",
  "project_path",
  "current_project_hash",
  "state",
  "lease_generation",
  "mutation_possible",
  "recovery_action",
  "terminal",
  "lease_status",
] as const;
const MNE_CHECKPOINT_OPTIONAL_DIGESTS = [
  "plan_digest",
  "baseline_digest",
  "before_mne_sha256",
  "target_mne_sha256",
  "adapter_binding_digest",
  "runtime_identity_digest",
  "target_program_digest",
  "rollback_ld_sha256",
  "simulation_receipt_digest",
] as const;

function sameLocalProjectPath(left: unknown, right: unknown): boolean {
  const normalize = (value: unknown) => typeof value === "string"
    ? value.replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase()
    : null;
  const normalizedLeft = normalize(left);
  return normalizedLeft !== null && normalizedLeft === normalize(right);
}

function isPositiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function mneTargetProgramIdentity(transaction: ChangeTransaction): {
  programId: string;
  programName: string;
  targetProgramDigest: string;
} | null {
  const structural = getStructuralMnePlan(transaction.plan);
  if (!structural) return null;
  const plan = structural.plan;
  const target = "candidate" in plan ? plan.candidate : plan.targetProgram;
  return {
    programId: target.programId,
    programName: target.programName,
    targetProgramDigest: target.targetProgramDigest,
  };
}

function isBoundMneNoMutationCheckpoint(
  value: unknown,
  transaction: ChangeTransaction,
  targetProgram: ReturnType<typeof mneTargetProgramIdentity>,
): value is Record<string, unknown> {
  if (
    !targetProgram ||
    !isRecord(value) ||
    Object.keys(value).some((key) => !MNE_PUBLIC_CHECKPOINT_FIELDS.has(key)) ||
    !MNE_CHECKPOINT_REQUIRED_FIELDS.every((key) =>
      Object.prototype.hasOwnProperty.call(value, key)
    ) ||
    value.schema !== "controlstudio-change-transaction-checkpoint/v1" ||
    value.host_transaction_id !== transaction.transactionId ||
    typeof value.transaction_id !== "string" ||
    !CANONICAL_UUID.test(value.transaction_id) ||
    typeof value.snapshot_id !== "string" ||
    !CANONICAL_UUID.test(value.snapshot_id) ||
    typeof value.snapshot_digest !== "string" ||
    !SHA256.test(value.snapshot_digest) ||
    value.artifact_visibility !== "private_digest_only" ||
    value.plan_kind !== "mne_full_program_replacement" ||
    value.workspace_id !== transaction.targetIdentity.workspaceId ||
    typeof value.project_identity_digest !== "string" ||
    !SHA256.test(value.project_identity_digest) ||
    typeof value.active_session_id !== "string" ||
    value.active_session_id.length === 0 ||
    !isPositiveSafeInteger(value.pid) ||
    !isPositiveSafeInteger(value.hwnd) ||
    !sameLocalProjectPath(
      value.project_path,
      transaction.targetIdentity.projectPath,
    ) ||
    value.current_project_hash !== transaction.targetIdentity.artifactHash ||
    typeof value.state !== "string" ||
    value.state.length === 0 ||
    typeof value.lease_generation !== "string" ||
    !CANONICAL_UUID.test(value.lease_generation) ||
    typeof value.mutation_possible !== "boolean" ||
    typeof value.recovery_action !== "string" ||
    typeof value.terminal !== "boolean" ||
    typeof value.lease_status !== "string"
  ) {
    return false;
  }
  if (
    (Object.prototype.hasOwnProperty.call(value, "backup_id") &&
      (typeof value.backup_id !== "string" || !CANONICAL_UUID.test(value.backup_id))) ||
    Object.prototype.hasOwnProperty.call(value, "plan_id") !==
      Object.prototype.hasOwnProperty.call(value, "plan_digest") ||
    (Object.prototype.hasOwnProperty.call(value, "plan_id") &&
      (typeof value.plan_id !== "string" || !CANONICAL_UUID.test(value.plan_id))) ||
    MNE_CHECKPOINT_OPTIONAL_DIGESTS.some((key) =>
      Object.prototype.hasOwnProperty.call(value, key) &&
      (typeof value[key] !== "string" || !SHA256.test(value[key]))
    )
  ) {
    return false;
  }
  return (
    (value.program_id === undefined || value.program_id === targetProgram.programId) &&
    (value.program_name === undefined || value.program_name === targetProgram.programName) &&
    (value.target_program_digest === undefined ||
      value.target_program_digest === targetProgram.targetProgramDigest)
  );
}

function checkpointFieldsEqual(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
  excluded: ReadonlySet<string>,
): boolean {
  const leftKeys = Object.keys(left).filter((key) => !excluded.has(key)).sort();
  const rightKeys = Object.keys(right).filter((key) => !excluded.has(key)).sort();
  return matchesValidation(leftKeys, rightKeys) &&
    leftKeys.every((key) => left[key] === right[key]);
}

function checkpointMatchesStoredNative(
  checkpoint: Record<string, unknown>,
  transaction: ChangeTransaction,
): boolean {
  const stored = transaction.result.native_checkpoint;
  if (stored === undefined) return true;
  if (!isRecord(stored)) return false;
  const exact = [
    ["host_transaction_id", transaction.transactionId],
    ["host_plan_digest", transaction.planDigest],
    ["mcp_transaction_id", checkpoint.transaction_id],
    ["snapshot_id", checkpoint.snapshot_id],
    ["snapshot_digest", checkpoint.snapshot_digest],
    ["artifact_visibility", checkpoint.artifact_visibility],
    ["backup_id", checkpoint.backup_id],
    ["mcp_plan_id", checkpoint.plan_id],
    ["mcp_plan_digest", checkpoint.plan_digest],
    ["workspace_id", transaction.targetIdentity.workspaceId],
    ["project_hash", transaction.targetIdentity.artifactHash],
    ["project_identity_digest", checkpoint.project_identity_digest],
    ["plan_kind", "mne_full_program_replacement"],
    ["program_id", checkpoint.program_id],
    ["program_name", checkpoint.program_name],
    ["target_program_digest", checkpoint.target_program_digest],
    ["before_mne_sha256", checkpoint.before_mne_sha256],
    ["target_mne_sha256", checkpoint.target_mne_sha256],
    ["adapter_binding_digest", checkpoint.adapter_binding_digest],
    ["runtime_identity_digest", checkpoint.runtime_identity_digest],
    ["baseline_digest", checkpoint.baseline_digest],
    ["rollback_ld_sha256", checkpoint.rollback_ld_sha256],
  ] as const;
  return (
    ["SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"].includes(
      String(stored.stage),
    ) &&
    sameLocalProjectPath(stored.project_path, transaction.targetIdentity.projectPath) &&
    exact.every(([key, expected]) =>
      expected === undefined || stored[key] === expected
    )
  );
}

/** Validate the exact, bounded recovery receipt proving no MNE mutation. */
export function hasValidMneNoMutationAbortReceipt(
  transaction: ChangeTransaction,
): boolean {
  const targetProgram = mneTargetProgramIdentity(transaction);
  const reconciliation = transaction.result.reconciliation;
  if (
    !targetProgram ||
    transaction.state !== "rolled_back" ||
    transaction.errorCode !== "mne_recovery_aborted_before_mutation" ||
    transaction.approval?.decision !== "approve" ||
    transaction.approval.planDigest !== transaction.planDigest ||
    !SHA256.test(transaction.planDigest) ||
    !CANONICAL_UUID.test(transaction.transactionId) ||
    !CANONICAL_UUID.test(transaction.targetIdentity.workspaceId) ||
    !isRecord(reconciliation)
  ) {
    return false;
  }
  const baseFields = [
    "verification_scope",
    "executed_checks",
    "not_executed",
    "manual_follow_up",
    "lookup_checkpoint",
    "abort_checkpoint",
    "native_mutation_performed",
  ];
  const recoveredFields = [...baseFields, "recovery_attach", "status_checkpoint"];
  if (
    !hasExactKeys(reconciliation, baseFields) &&
    !hasExactKeys(reconciliation, recoveredFields)
  ) {
    return false;
  }
  if (
    reconciliation.verification_scope !== transaction.plan.verificationScope ||
    !matchesValidation(reconciliation.executed_checks, []) ||
    !matchesValidation(reconciliation.not_executed, [
      ...REOPEN_VALIDATION,
      "xg_sim",
      "runtime_or_online_validation",
    ]) ||
    !matchesValidation(
      reconciliation.manual_follow_up,
      transaction.plan.manualFollowUp,
    ) ||
    reconciliation.native_mutation_performed !== false
  ) {
    return false;
  }
  const lookup = reconciliation.lookup_checkpoint;
  const aborted = reconciliation.abort_checkpoint;
  if (
    !isBoundMneNoMutationCheckpoint(lookup, transaction, targetProgram) ||
    !isBoundMneNoMutationCheckpoint(aborted, transaction, targetProgram) ||
    !checkpointMatchesStoredNative(lookup, transaction) ||
    aborted.state !== "ABORTED_NO_MUTATION" ||
    aborted.mutation_possible !== false ||
    aborted.recovery_action !== "already_terminal" ||
    aborted.terminal !== true ||
    aborted.lease_status !== "released"
  ) {
    return false;
  }
  const dispositionFields = new Set([
    "state",
    "mutation_possible",
    "recovery_action",
    "terminal",
    "lease_status",
  ]);
  if (lookup.state === "ABORTED_NO_MUTATION") {
    return hasExactKeys(reconciliation, baseFields) &&
      checkpointFieldsEqual(lookup, aborted, new Set());
  }
  if (
    !["SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"].includes(
      String(lookup.state),
    ) ||
    lookup.mutation_possible !== false ||
    lookup.recovery_action !== "abort_safe" ||
    lookup.terminal !== false ||
    !hasExactKeys(reconciliation, recoveredFields)
  ) {
    return false;
  }
  const status = reconciliation.status_checkpoint;
  const attach = reconciliation.recovery_attach;
  if (
    !isBoundMneNoMutationCheckpoint(status, transaction, targetProgram) ||
    !isRecord(attach) ||
    !(hasExactKeys(attach, ["mode", "session_id", "pid", "hwnd", "project_hash"]) ||
      hasExactKeys(attach, [
        "mode",
        "session_id",
        "pid",
        "hwnd",
        "project_hash",
        "plc_policy",
      ])) ||
    typeof attach.mode !== "string" ||
    attach.mode.length === 0 ||
    attach.session_id !== status.active_session_id ||
    attach.pid !== status.pid ||
    attach.hwnd !== status.hwnd ||
    attach.project_hash !== transaction.targetIdentity.artifactHash ||
    (attach.mode === "existing_bound_session"
      ? Object.prototype.hasOwnProperty.call(attach, "plc_policy")
      : attach.plc_policy !== "offline_abort_recovery_only") ||
    status.mutation_possible !== false ||
    status.recovery_action !== "abort_safe" ||
    status.terminal !== false ||
    !["SNAPSHOT_CREATED", "BACKUP_CREATED", "PLANNED"].includes(
      String(status.state),
    ) ||
    !checkpointFieldsEqual(
      lookup,
      status,
      new Set(["active_session_id", "pid", "hwnd", "lease_generation", ...dispositionFields]),
    ) ||
    !checkpointFieldsEqual(status, aborted, dispositionFields)
  ) {
    return false;
  }
  return true;
}

/**
 * Validate the bounded public receipt needed before the Web may describe and
 * acknowledge a successful offline outcome. Recovery/no-mutation and uncertain
 * states deliberately remain review-only.
 */
export function hasValidChangeExecutionReceipt(
  transaction: ChangeTransaction,
): boolean {
  if (
    transaction.state !== "completed" &&
    transaction.state !== "rolled_back"
  ) {
    return false;
  }
  const evidence = terminalReceiptEvidence(transaction);
  const structuralPlan = getStructuralMnePlan(transaction.plan);
  if (
    structuralPlan &&
    transaction.state === "rolled_back" &&
    transaction.errorCode === "mne_recovery_aborted_before_mutation"
  ) {
    return hasValidMneNoMutationAbortReceipt(transaction);
  }
  if (!evidence || !hasReceiptExecutionBoundary(transaction, evidence)) {
    return false;
  }

  if (structuralPlan) {
    if (transaction.state === "rolled_back") {
      const rollback = isRecord(evidence.rollback) ? evidence.rollback : null;
      return Boolean(
        rollback &&
          rollback.verified === true &&
          rollback.program_check_verified === true &&
          rollback.save_verified === true &&
          rollback.ld_raw_equal === true &&
          rollback.ld_canonical_equal === true &&
          typeof rollback.rollback_digest === "string" &&
          SHA256.test(rollback.rollback_digest) &&
          hasIndependentMneReopen(rollback.reopen),
      );
    }

    const apply = isRecord(transaction.result.apply)
      ? transaction.result.apply
      : null;
    const application = apply && isRecord(apply.application)
      ? apply.application
      : null;
    if (
      !application ||
      application.verified_in_editor !== true ||
      typeof application.application_digest !== "string" ||
      !SHA256.test(application.application_digest)
    ) {
      return false;
    }

    const verification = isRecord(evidence.verification)
      ? evidence.verification
      : null;
    if (
      !verification ||
      verification.verified !== true ||
      verification.program_check_verified !== true ||
      verification.save_verified !== true ||
      typeof verification.verification_digest !== "string" ||
      !SHA256.test(verification.verification_digest)
    ) {
      return false;
    }
    return transaction.plan.verificationScope === "offline_native_check_only"
      ? verification.reopen === null
      : hasIndependentMneReopen(verification.reopen);
  }

  return hasValidLegacyTerminalReceipt(transaction, evidence);
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
