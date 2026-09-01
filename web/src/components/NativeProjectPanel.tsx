import { useEffect, useMemo, useRef, useState } from "react";
import { hostApi, HostApiError } from "../api/client";
import type {
  CompiledEngineeringPlanNativeCollectionScope,
  CreateNativeProjectRequest,
  EngineeringPlanTechnicalConfiguration,
  HostStatusResponse,
  NativeProjectOperation,
  NativeSimulationReceipt,
  NativeSimulationReceiptV3,
  NativeSimulationReceiptV4,
  NativeSimulationReceiptV5,
  ReviewedProjectPlanBinding,
  XG5000Instance,
} from "../api/types";
import { Icon } from "./Icon";

interface NativeProjectPanelProps {
  onClose: () => void;
  onStatusChanged: (status: HostStatusResponse) => void;
  status: HostStatusResponse;
  onEngineeringReady: (
    operation: NativeProjectOperation,
    status: HostStatusResponse,
  ) => Promise<void>;
  initialNativeProjectId?: string;
  initialProjectName?: string;
  initialDescription?: string;
  reviewedPlan?: ReviewedProjectPlanBinding;
  reviewedProjectName?: string;
  reviewedTechnicalConfiguration?: EngineeringPlanTechnicalConfiguration;
}

const TERMINAL_STATES = new Set(["completed", "failed", "quarantined"]);

type CollectionScopeKey = keyof CompiledEngineeringPlanNativeCollectionScope;

const COLLECTION_SCOPE_LABELS: Record<CollectionScopeKey, string> = {
  ioPoints: "I/O",
  operatingModes: "운전 모드",
  controlNetworks: "제어 네트워크",
  timers: "타이머",
  sequences: "시퀀스",
  alarms: "알람",
  recoveryRules: "복구 규칙",
  safetyRules: "안전 규칙",
};

type VerifiedNativeProjectOperation = NativeProjectOperation & {
  state: "completed";
  result: NonNullable<NativeProjectOperation["result"]> & {
    projectPath: string;
    projectSha256: string;
    identityVerified: true;
    controllerConnected: false;
    plcWritePerformed: false;
  };
};

function newOperationId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10).join(""),
  ].join("-");
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof HostApiError ? error.message : fallback;
}

function isAbsoluteWindowsPath(value: string): boolean {
  return /^[a-zA-Z]:[\\/]/.test(value) || /^\\\\[^\\]+\\[^\\]+/.test(value);
}

function displayInstance(instance: XG5000Instance): string {
  const title = instance.title?.trim();
  const target = `PID ${instance.pid} · HWND ${instance.hwnd}`;
  return title ? `${title} · ${target}` : `XG5000 · ${target}`;
}

function compiledCollectionScope(
  operation: NativeProjectOperation,
): CompiledEngineeringPlanNativeCollectionScope | undefined {
  return operation.request.buildBundle?.compiledProgram?.collectionScope;
}

function hasExplicitCompiledCollectionScope(
  operation: NativeProjectOperation,
): boolean {
  const scope = compiledCollectionScope(operation);
  return (
    scope !== undefined &&
    (Object.keys(COLLECTION_SCOPE_LABELS) as CollectionScopeKey[]).every(
      (key) =>
        scope[key]?.source === "explicit" &&
        (scope[key].status === "confirmed" ||
          scope[key].status === "not_applicable"),
    )
  );
}

function confirmedCollectionScopeLabels(
  operation: NativeProjectOperation,
): string[] {
  const scope = compiledCollectionScope(operation);
  if (!scope) {
    return [];
  }
  return (Object.keys(COLLECTION_SCOPE_LABELS) as CollectionScopeKey[])
    .filter(
      (key) => scope[key].source === "explicit" && scope[key].status === "confirmed",
    )
    .map((key) => COLLECTION_SCOPE_LABELS[key]);
}

function confirmedCollectionScopeSummary(
  operation: NativeProjectOperation,
): string {
  const labels = confirmedCollectionScopeLabels(operation);
  return labels.length > 0
    ? `${labels.join("·")} 제어 묶음`
    : "collectionScope의 confirmed 항목";
}

interface ExpectedNetworkRungCoverage {
  networkId: string;
  outputSignalId: string;
  rungId: string;
  conditionKind: "contact" | "all_of" | "any_of";
  status: "verified";
  stepId?: string;
  normalInputState: SimulationInputState;
  deniedInputState: SimulationInputState;
}

interface ExpectedMoveRungCoverage {
  rungId: string;
  conditionKind: "contact" | "all_of" | "any_of";
  condition: Record<string, unknown>;
  action: {
    instruction: "MOV";
    sourceConstant: number;
    destinationAddress: string;
  };
}

type SimulationInputState = Record<string, 0 | 1>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSupportedSimulationReceipt(
  value: unknown,
): value is NativeSimulationReceipt {
  if (!isRecord(value)) {
    return false;
  }
  const scenarioReceipt = value.scenarioReceipt;
  const coverage = isRecord(scenarioReceipt) ? scenarioReceipt.coverage : undefined;
  const preparation = value.simulationPreparation;
  const preparationReadback = isRecord(preparation)
    ? preparation.programReadback
    : undefined;
  const programBinding = value.programBinding;
  const simulator = value.simulator;
  const stop = isRecord(simulator) ? simulator.stop : undefined;
  const postcondition = isRecord(stop) ? stop.postcondition : undefined;
  const stopUiState = isRecord(postcondition) ? postcondition.ui_state : undefined;
  const inputBoundary = value.inputWriteBoundary;
  if (
    !isRecord(value.projectReadback) ||
    typeof value.projectReadback.reopenedProjectSha256 !== "string" ||
    typeof value.projectReadback.projectSha256 !== "string" ||
    !isRecord(preparation) ||
    !isRecord(preparationReadback) ||
    !Array.isArray(preparationReadback.rungIds) ||
    !Array.isArray(preparationReadback.exactInstructionReadback) ||
    !isRecord(programBinding) ||
    typeof programBinding.rungCount !== "number" ||
    typeof programBinding.ladderIrSha256 !== "string" ||
    !Array.isArray(programBinding.rungIds) ||
    !programBinding.rungIds.every((rungId) => typeof rungId === "string") ||
    !isRecord(simulator) ||
    !isRecord(stop) ||
    !isRecord(stop.confirmation) ||
    !isRecord(postcondition) ||
    !isRecord(stopUiState) ||
    !isRecord(stopUiState.selected_run) ||
    !isRecord(stopUiState.selected_stop) ||
    !Array.isArray(stopUiState.samples) ||
    !stopUiState.samples.every(
      (sample) =>
        isRecord(sample) &&
        Array.isArray(sample.dominant_rgb),
    ) ||
    !isRecord(inputBoundary) ||
    !isRecord(inputBoundary.restore) ||
    !isRecord(scenarioReceipt) ||
    typeof scenarioReceipt.scenarioCount !== "number" ||
    !isRecord(coverage) ||
    !Array.isArray(scenarioReceipt.scenarios) ||
    !scenarioReceipt.scenarios.every(isRecord) ||
    !Array.isArray(scenarioReceipt.inputWrites) ||
    !Array.isArray(coverage.networkIds) ||
    !Array.isArray(coverage.outputSignalIds)
  ) {
    return false;
  }
  if (value.schemaVersion === "controlstudio-native-simulation-receipt/v3") {
    return (
      scenarioReceipt.schemaVersion ===
        "controlstudio-native-simulation-scenarios/v1" &&
      isRecord(coverage.selectedNetworkRung) &&
      typeof coverage.selectedNetworkRung.rungId === "string" &&
      ["contact", "all_of", "any_of"].includes(
        String(coverage.selectedNetworkRung.conditionKind),
      ) &&
      coverage.selectedNetworkRung.status === "verified"
    );
  }
  if (value.schemaVersion === "controlstudio-native-simulation-receipt/v4") {
    return (
      scenarioReceipt.schemaVersion ===
        "controlstudio-native-simulation-scenarios/v2" &&
      Array.isArray(coverage.networkRungs) &&
      coverage.networkRungs.every(
        (binding) =>
          isRecord(binding) &&
          typeof binding.networkId === "string" &&
          typeof binding.outputSignalId === "string" &&
          typeof binding.rungId === "string" &&
          ["contact", "all_of", "any_of"].includes(
            String(binding.conditionKind),
          ) &&
          binding.status === "verified",
      )
    );
  }
  if (value.schemaVersion === "controlstudio-native-simulation-receipt/v5") {
    return (
      scenarioReceipt.schemaVersion ===
        "controlstudio-native-simulation-scenarios/v3" &&
      Array.isArray(coverage.networkRungs) &&
      coverage.networkRungs.every(
        (binding) =>
          isRecord(binding) &&
          typeof binding.networkId === "string" &&
          typeof binding.outputSignalId === "string" &&
          typeof binding.rungId === "string" &&
          ["contact", "all_of", "any_of"].includes(
            String(binding.conditionKind),
          ) &&
          binding.status === "verified",
      ) &&
      Array.isArray(coverage.moveRungs) &&
      coverage.moveRungs.every(
        (binding) =>
          isRecord(binding) &&
          typeof binding.rungId === "string" &&
          ["contact", "all_of", "any_of"].includes(
            String(binding.conditionKind),
          ) &&
          isRecord(binding.action) &&
          binding.action.instruction === "MOV" &&
          isRecord(binding.inputAssignment) &&
          binding.status === "verified",
      )
    );
  }
  return false;
}

function isExplicitHumanAcceptedTrial(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.decision)) {
    return false;
  }
  const decision = value.decision;
  return (
    value.schemaVersion === "controlstudio-native-simulation-trial/v1" &&
    typeof value.trialId === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      value.trialId,
    ) &&
    value.state === "ACCEPTED" &&
    value.safeRestorationVerified === true &&
    typeof value.receiptDigest === "string" &&
    /^[0-9a-f]{64}$/.test(value.receiptDigest) &&
    decision.outcome === "ACCEPTED" &&
    typeof decision.actorId === "string" &&
    decision.actorId.length > 0 &&
    typeof decision.decidedAt === "string" &&
    decision.decidedAt.length > 0 &&
    decision.receiptDigest === value.receiptDigest &&
    !("migratedFrom" in decision)
  );
}

function isPreTrialMigration(value: unknown): boolean {
  return (
    isRecord(value) &&
    isRecord(value.decision) &&
    value.decision.migratedFrom === "pre-trial-lifecycle"
  );
}

function mergeSimulationInputStates(
  states: SimulationInputState[],
): SimulationInputState | undefined {
  const merged: SimulationInputState = {};
  for (const state of states) {
    for (const [signalId, requested] of Object.entries(state)) {
      const previous = merged[signalId];
      if (previous !== undefined && previous !== requested) {
        return undefined;
      }
      merged[signalId] = requested;
    }
  }
  return merged;
}

function reviewedLadderConditionState(
  value: unknown,
  desired: boolean,
  inputSignalIds: Set<string>,
  fixedContactValues: Map<string, 0 | 1>,
): SimulationInputState | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  if (value.kind === "contact") {
    const sourceType = value.sourceType;
    const sourceId = value.sourceId;
    const state = value.state;
    if (
      typeof sourceType !== "string" ||
      typeof sourceId !== "string" ||
      (state !== "on" && state !== "off")
    ) {
      return undefined;
    }
    const requested: 0 | 1 = state === "on" ? 1 : 0;
    if (sourceType === "signal" && inputSignalIds.has(sourceId)) {
      return { [sourceId]: desired ? requested : requested === 1 ? 0 : 1 };
    }
    if (sourceType === "timer") {
      return undefined;
    }
    const fixedValue = fixedContactValues.get(`${sourceType}\u0000${sourceId}`);
    if (fixedValue === undefined || (fixedValue === requested) !== desired) {
      return undefined;
    }
    return {};
  }
  if (
    (value.kind !== "all_of" && value.kind !== "any_of") ||
    !Array.isArray(value.terms) ||
    value.terms.length < 2 ||
    value.terms.length > 20 ||
    !value.terms.every(isRecord)
  ) {
    return undefined;
  }
  const conjunction = value.kind === "all_of";
  if (desired === conjunction) {
    const states = value.terms.map((term) =>
      reviewedLadderConditionState(
        term,
        desired,
        inputSignalIds,
        fixedContactValues,
      ),
    );
    return states.every((state) => state !== undefined)
      ? mergeSimulationInputStates(states as SimulationInputState[])
      : undefined;
  }
  for (const selectedIndex of value.terms.keys()) {
    const states = value.terms.map((term, index) =>
      reviewedLadderConditionState(
        term,
        index === selectedIndex ? desired : !desired,
        inputSignalIds,
        fixedContactValues,
      ),
    );
    if (states.every((state) => state !== undefined)) {
      const merged = mergeSimulationInputStates(states as SimulationInputState[]);
      if (merged !== undefined) {
        return merged;
      }
    }
  }
  return undefined;
}

function reviewedInputConditionMatches(
  value: unknown,
  assignment: SimulationInputState,
  inputSignalIds: Set<string>,
): boolean | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  if (value.kind === "contact") {
    if (
      value.sourceType !== "signal" ||
      typeof value.sourceId !== "string" ||
      !inputSignalIds.has(value.sourceId) ||
      !(value.sourceId in assignment) ||
      (value.state !== "on" && value.state !== "off")
    ) {
      return undefined;
    }
    return assignment[value.sourceId] === (value.state === "on" ? 1 : 0);
  }
  if (
    (value.kind !== "all_of" && value.kind !== "any_of") ||
    !Array.isArray(value.terms) ||
    !value.terms.every(isRecord)
  ) {
    return undefined;
  }
  const matches = value.terms.map((term) =>
    reviewedInputConditionMatches(term, assignment, inputSignalIds),
  );
  if (matches.some((matched) => matched === undefined)) {
    return undefined;
  }
  return value.kind === "all_of"
    ? matches.every((matched) => matched === true)
    : matches.some((matched) => matched === true);
}

function reviewedConditionInputIds(
  value: unknown,
  inputSignalIds: Set<string>,
): Set<string> | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  if (value.kind === "contact") {
    return value.sourceType === "signal" &&
      typeof value.sourceId === "string" &&
      inputSignalIds.has(value.sourceId)
      ? new Set([value.sourceId])
      : undefined;
  }
  if (
    (value.kind !== "all_of" && value.kind !== "any_of") ||
    !Array.isArray(value.terms)
  ) {
    return undefined;
  }
  const termIds = value.terms.map((term) =>
    reviewedConditionInputIds(term, inputSignalIds),
  );
  return termIds.every((ids) => ids !== undefined)
    ? new Set(termIds.flatMap((ids) => [...(ids as Set<string>)]))
    : undefined;
}

function orderedExpectedMoveRungs(
  operation: NativeProjectOperation,
): { moves: ExpectedMoveRungCoverage[]; inputSignalIds: Set<string> } | undefined {
  const compiled = operation.request.buildBundle?.compiledProgram;
  const design = operation.request.buildBundle?.engineeringPlan.design;
  const ioPoints = design?.ioPoints;
  const rungs = compiled?.ladderIr?.rungs;
  if (
    compiled?.schemaVersion !== "controlstudio-xg5000-mnemonic-program/v4" ||
    compiled.ladderIr?.schemaVersion !== "controlstudio-ld-rung-ir/v1" ||
    !Array.isArray(ioPoints) ||
    !Array.isArray(rungs) ||
    !exactOrderedStrings(
      compiled.rungIds,
      rungs.map((rung) =>
        isRecord(rung) && typeof rung.rungId === "string"
          ? rung.rungId
          : undefined,
      ),
    )
  ) {
    return undefined;
  }
  const inputSignalIds = new Set<string>();
  for (const point of ioPoints) {
    if (point.kind === "digital_input" && typeof point.signalId === "string") {
      inputSignalIds.add(point.signalId);
    }
  }
  const moves: ExpectedMoveRungCoverage[] = [];
  for (const rung of rungs) {
    if (!isRecord(rung) || !isRecord(rung.action)) {
      return undefined;
    }
    if (rung.action.instruction !== "MOV") {
      continue;
    }
    const sourceConstant = rung.action.sourceConstant;
    const destinationAddress = rung.action.destinationAddress;
    if (
      typeof rung.rungId !== "string" ||
      !isRecord(rung.condition) ||
      !["contact", "all_of", "any_of"].includes(String(rung.condition.kind)) ||
      !Number.isInteger(sourceConstant) ||
      (sourceConstant as number) < -32768 ||
      (sourceConstant as number) > 32767 ||
      typeof destinationAddress !== "string" ||
      !/^D\d{5}$/.test(destinationAddress) ||
      reviewedConditionInputIds(rung.condition, inputSignalIds) === undefined
    ) {
      return undefined;
    }
    moves.push({
      rungId: rung.rungId,
      conditionKind: rung.condition.kind as ExpectedMoveRungCoverage["conditionKind"],
      condition: rung.condition,
      action: {
        instruction: "MOV",
        sourceConstant: sourceConstant as number,
        destinationAddress,
      },
    });
  }
  return { moves, inputSignalIds };
}

function orderedExpectedNetworkRungs(
  operation: NativeProjectOperation,
): ExpectedNetworkRungCoverage[] | undefined {
  const bundle = operation.request.buildBundle;
  const compiled = bundle?.compiledProgram;
  const design = bundle?.engineeringPlan.design;
  const networks = design?.controlNetworks;
  const ioPoints = design?.ioPoints;
  const modes = design?.operatingModes;
  const timers = design?.timers;
  const sequences = design?.sequences;
  const alarms = design?.alarms;
  const recoveries = design?.recoveryRules;
  if (
    compiled?.schemaVersion !== "controlstudio-xg5000-mnemonic-program/v4" ||
    !Array.isArray(networks) ||
    !Array.isArray(ioPoints) ||
    !Array.isArray(modes) ||
    !Array.isArray(timers) ||
    !Array.isArray(sequences) ||
    !Array.isArray(alarms) ||
    !Array.isArray(recoveries) ||
    networks.length < 1 ||
    [modes, timers, sequences, alarms, recoveries].some(
      (items) => items.length > 1 || !items.every(isRecord),
    ) ||
    !Array.isArray(compiled.networkIds) ||
    !Array.isArray(compiled.outputSignalIds) ||
    !Array.isArray(compiled.rungIds) ||
    !Array.isArray(compiled.stepIds) ||
    compiled.networkIds.length !== networks.length ||
    compiled.outputSignalIds.length !== networks.length ||
    compiled.ladderIr?.schemaVersion !== "controlstudio-ld-rung-ir/v1" ||
    !Array.isArray(compiled.ladderIr.rungs)
  ) {
    return undefined;
  }

  const inputSignalIds = new Set<string>();
  const allIoSignalIds = new Set<string>();
  for (const ioPoint of ioPoints) {
    if (
      !isRecord(ioPoint) ||
      typeof ioPoint.signalId !== "string" ||
      ioPoint.signalId.length === 0 ||
      allIoSignalIds.has(ioPoint.signalId)
    ) {
      return undefined;
    }
    allIoSignalIds.add(ioPoint.signalId);
    if (ioPoint.kind === "digital_input") {
      inputSignalIds.add(ioPoint.signalId);
    }
  }
  const sequence = sequences.length === 1 ? sequences[0] : undefined;
  const sequenceSteps =
    isRecord(sequence) &&
    Array.isArray(sequence.steps) &&
    sequence.steps.length === 2 &&
    sequence.steps.every(isRecord)
      ? sequence.steps
      : [];
  const reviewedStepIds = sequenceSteps.map((step) =>
    typeof step.stepId === "string" ? step.stepId : "",
  );
  if (
    reviewedStepIds.some((stepId) => stepId.length === 0) ||
    new Set(reviewedStepIds).size !== reviewedStepIds.length ||
    !exactOrderedStrings(compiled.stepIds, reviewedStepIds)
  ) {
    return undefined;
  }
  const alarm = alarms.length === 1 ? alarms[0] : undefined;
  const simulationLatchId =
    isRecord(alarm) && typeof alarm.latchSignalId === "string"
      ? alarm.latchSignalId
      : undefined;

  const seenNetworkIds = new Set<string>();
  const seenOutputSignalIds = new Set<string>();
  const seenRungIds = new Set<string>();
  const expected: ExpectedNetworkRungCoverage[] = [];
  for (const [index, network] of networks.entries()) {
    if (!isRecord(network)) {
      return undefined;
    }
    const networkId = network.networkId;
    const outputSignalId = network.outputSignalId;
    const rawStepId = network.stepId;
    const stepId = typeof rawStepId === "string" ? rawStepId : undefined;
    if (
      typeof networkId !== "string" ||
      networkId.length === 0 ||
      typeof outputSignalId !== "string" ||
      outputSignalId.length === 0 ||
      (rawStepId !== undefined && rawStepId !== null && !stepId) ||
      (stepId !== undefined && !compiled.stepIds.includes(stepId)) ||
      compiled.networkIds[index] !== networkId ||
      compiled.outputSignalIds[index] !== outputSignalId ||
      seenNetworkIds.has(networkId) ||
      seenOutputSignalIds.has(outputSignalId)
    ) {
      return undefined;
    }
    const matchingOutRungs = compiled.ladderIr.rungs.filter(
      (rung) =>
        isRecord(rung) &&
        isRecord(rung.action) &&
        rung.action.instruction === "OUT" &&
        rung.action.targetType === "signal" &&
        rung.action.targetId === outputSignalId,
    );
    if (matchingOutRungs.length !== 1) {
      return undefined;
    }
    const [rung] = matchingOutRungs;
    if (
      !isRecord(rung) ||
      !isRecord(rung.condition) ||
      typeof rung.rungId !== "string" ||
      rung.rungId.length === 0 ||
      seenRungIds.has(rung.rungId) ||
      !["contact", "all_of", "any_of"].includes(String(rung.condition.kind))
    ) {
      return undefined;
    }
    const fixedContactValues = new Map<string, 0 | 1>();
    if (simulationLatchId !== undefined) {
      fixedContactValues.set(`signal\u0000${simulationLatchId}`, 0);
    }
    for (const reviewedStepId of reviewedStepIds) {
      fixedContactValues.set(
        `step\u0000${reviewedStepId}`,
        reviewedStepId === stepId ? 1 : 0,
      );
    }
    const normalInputState = reviewedLadderConditionState(
      rung.condition,
      true,
      inputSignalIds,
      fixedContactValues,
    );
    const deniedInputState = reviewedLadderConditionState(
      rung.condition,
      false,
      inputSignalIds,
      fixedContactValues,
    );
    if (normalInputState === undefined || deniedInputState === undefined) {
      return undefined;
    }
    seenNetworkIds.add(networkId);
    seenOutputSignalIds.add(outputSignalId);
    seenRungIds.add(rung.rungId);
    expected.push({
      networkId,
      outputSignalId,
      rungId: rung.rungId,
      conditionKind: rung.condition.kind as ExpectedNetworkRungCoverage["conditionKind"],
      status: "verified",
      ...(stepId === undefined ? {} : { stepId }),
      normalInputState,
      deniedInputState,
    });
  }
  const orderedLadderRungIds = compiled.ladderIr.rungs.map((rung) =>
    isRecord(rung) && typeof rung.rungId === "string" ? rung.rungId : undefined,
  );
  if (
    orderedLadderRungIds.some((rungId) => rungId === undefined) ||
    new Set(orderedLadderRungIds).size !== orderedLadderRungIds.length ||
    !exactOrderedStrings(compiled.rungIds, orderedLadderRungIds)
  ) {
    return undefined;
  }
  return expected;
}

function exactOrderedStrings(
  actual: unknown,
  expected: Array<string | undefined>,
): boolean {
  return (
    Array.isArray(actual) &&
    actual.length === expected.length &&
    actual.every(
      (value, index) => typeof value === "string" && value === expected[index],
    )
  );
}

function expectedV4ScenarioIds(
  operation: NativeProjectOperation,
  expectedNetworks: ExpectedNetworkRungCoverage[],
  expectedMoves: ExpectedMoveRungCoverage[] = [],
): Set<string> | undefined {
  const bundle = operation.request.buildBundle;
  const compiled = bundle?.compiledProgram;
  const design = bundle?.engineeringPlan.design;
  const modes = design?.operatingModes;
  const timers = design?.timers;
  const sequences = design?.sequences;
  const alarms = design?.alarms;
  const recoveries = design?.recoveryRules;
  const safetyRules = design?.safetyRules;
  if (
    compiled?.schemaVersion !== "controlstudio-xg5000-mnemonic-program/v4" ||
    !Array.isArray(modes) ||
    !Array.isArray(timers) ||
    !Array.isArray(sequences) ||
    !Array.isArray(alarms) ||
    !Array.isArray(recoveries) ||
    !Array.isArray(safetyRules) ||
    [modes, timers, sequences, alarms, recoveries].some(
      (items) => items.length > 1 || !items.every(isRecord),
    ) ||
    !safetyRules.every(isRecord)
  ) {
    return undefined;
  }

  const ids = new Set<string>(["initial_normal_start"]);
  const addId = (prefix: string, value: unknown): boolean => {
    if (typeof value !== "string" || value.length === 0) {
      return false;
    }
    const scenarioId = `${prefix}:${value}`;
    if (ids.has(scenarioId)) {
      return false;
    }
    ids.add(scenarioId);
    return true;
  };
  for (const network of expectedNetworks) {
    if (!addId("network_ladder", network.networkId)) {
      return undefined;
    }
  }
  for (const move of expectedMoves) {
    if (!addId("move_ladder", move.rungId)) {
      return undefined;
    }
  }
  for (const mode of modes) {
    if (!addId("mode_removed", mode.modeId)) {
      return undefined;
    }
  }
  for (const alarm of alarms) {
    if (!addId("fault_recovery", alarm.alarmId)) {
      return undefined;
    }
  }
  for (const rule of safetyRules) {
    if (
      rule.classification === "process_interlock" &&
      !addId("process_interlock", rule.ruleId)
    ) {
      return undefined;
    }
  }

  const timer = timers.length === 1 ? timers[0] : undefined;
  const sequence = sequences.length === 1 ? sequences[0] : undefined;
  const recovery = recoveries.length === 1 ? recoveries[0] : undefined;
  if (timer !== undefined) {
    if (
      !addId(
        sequence === undefined ? "timer_complete" : "timer_rearm",
        timer.timerId,
      )
    ) {
      return undefined;
    }
  } else if (
    sequence !== undefined &&
    !addId("sequence_rearm", sequence.sequenceId)
  ) {
    return undefined;
  }
  if (
    sequence !== undefined &&
    recovery !== undefined &&
    !addId("sequence_recovery", recovery.recoveryId)
  ) {
    return undefined;
  }
  if (recovery !== undefined && sequence === undefined && alarms.length === 0) {
    return undefined;
  }

  const timerIds = timers.map((item) => item.timerId as string | undefined);
  const sequenceIds = sequences.map(
    (item) => item.sequenceId as string | undefined,
  );
  const alarmIds = alarms.map((item) => item.alarmId as string | undefined);
  const recoveryIds = recoveries.map(
    (item) => item.recoveryId as string | undefined,
  );
  if (
    !exactOrderedStrings(compiled.timerIds, timerIds) ||
    !exactOrderedStrings(compiled.sequenceIds, sequenceIds) ||
    !exactOrderedStrings(compiled.alarmIds, alarmIds) ||
    !exactOrderedStrings(compiled.recoveryIds, recoveryIds)
  ) {
    return undefined;
  }
  return ids;
}

function isLegacyV3NetworkCoverageVerified(
  receipt: NativeSimulationReceipt,
  expected: ExpectedNetworkRungCoverage[] | undefined,
): receipt is NativeSimulationReceiptV3 {
  // This legacy matcher sits beneath the human-ACCEPTED gate below. New
  // simulation runs are Host-authored current receipts and are never promoted here.
  if (
    receipt.schemaVersion !== "controlstudio-native-simulation-receipt/v3" ||
    receipt.scenarioReceipt.schemaVersion !==
      "controlstudio-native-simulation-scenarios/v1" ||
    expected?.length !== 1
  ) {
    return false;
  }
  const [binding] = expected;
  const coverage = receipt.scenarioReceipt.coverage;
  return (
    binding !== undefined &&
    exactOrderedStrings(coverage.networkIds, [binding.networkId]) &&
    exactOrderedStrings(coverage.outputSignalIds, [binding.outputSignalId]) &&
    coverage.selectedNetworkRung.rungId === binding.rungId &&
    coverage.selectedNetworkRung.conditionKind === binding.conditionKind &&
    coverage.selectedNetworkRung.status === "verified"
  );
}

function isV4NetworkCoverageVerified(
  receipt: NativeSimulationReceipt,
  expected: ExpectedNetworkRungCoverage[] | undefined,
): receipt is NativeSimulationReceiptV4 | NativeSimulationReceiptV5 {
  if (
    !(
      (receipt.schemaVersion === "controlstudio-native-simulation-receipt/v4" &&
        receipt.scenarioReceipt.schemaVersion ===
          "controlstudio-native-simulation-scenarios/v2") ||
      (receipt.schemaVersion === "controlstudio-native-simulation-receipt/v5" &&
        receipt.scenarioReceipt.schemaVersion ===
          "controlstudio-native-simulation-scenarios/v3")
    ) ||
    !expected
  ) {
    return false;
  }
  const coverage = receipt.scenarioReceipt.coverage;
  return (
    exactOrderedStrings(
      coverage.networkIds,
      expected.map((binding) => binding.networkId),
    ) &&
    exactOrderedStrings(
      coverage.outputSignalIds,
      expected.map((binding) => binding.outputSignalId),
    ) &&
    coverage.networkRungs.length === expected.length &&
    coverage.networkRungs.every((actual, index) => {
      const wanted = expected[index];
      return (
        wanted !== undefined &&
        Object.keys(actual).length === 5 &&
        actual.networkId === wanted.networkId &&
        actual.outputSignalId === wanted.outputSignalId &&
        actual.rungId === wanted.rungId &&
        actual.conditionKind === wanted.conditionKind &&
        actual.status === wanted.status
      );
    })
  );
}

function hasExactRecordValues(
  value: unknown,
  expected: Record<string, unknown>,
): boolean {
  if (!isRecord(value)) {
    return false;
  }
  const actualKeys = Object.keys(value).sort();
  const expectedKeys = Object.keys(expected).sort();
  return (
    exactOrderedStrings(actualKeys, expectedKeys) &&
    expectedKeys.every((key) => value[key] === expected[key])
  );
}

function isExactNetworkOutputObservation(
  value: unknown,
  binding: ExpectedNetworkRungCoverage,
  expectedValue: 0 | 1,
): boolean {
  if (!isRecord(value) || !isRecord(value.signals)) {
    return false;
  }
  return hasExactRecordValues(value.signals, {
    [binding.outputSignalId]: expectedValue,
    ...(binding.stepId === undefined ? {} : { [binding.stepId]: 1 }),
  });
}

function areV4NetworkScenariosVerified(
  receipt: NativeSimulationReceiptV4 | NativeSimulationReceiptV5,
  expected: ExpectedNetworkRungCoverage[],
  expectedScenarioIds: Set<string> | undefined,
): boolean {
  const scenarios = receipt.scenarioReceipt.scenarios;
  if (
    !Array.isArray(scenarios) ||
    scenarios.length === 0 ||
    expectedScenarioIds === undefined ||
    scenarios.length !== expectedScenarioIds.size
  ) {
    return false;
  }
  const scenarioIds = scenarios.map((scenario) =>
    isRecord(scenario) && typeof scenario.scenarioId === "string"
      ? scenario.scenarioId
      : "",
  );
  if (
    scenarioIds.some((scenarioId) => scenarioId.length === 0) ||
    new Set(scenarioIds).size !== scenarioIds.length ||
    scenarioIds.some((scenarioId) => !expectedScenarioIds.has(scenarioId)) ||
    scenarios.some((scenario) => {
      if (
        !isRecord(scenario) ||
        scenario.status !== "passed" ||
        !isRecord(scenario.assertions)
      ) {
        return true;
      }
      const assertions = Object.values(scenario.assertions);
      return assertions.length === 0 || assertions.some((value) => value !== true);
    })
  ) {
    return false;
  }

  return expected.every((binding) => {
    const scenarioId = `network_ladder:${binding.networkId}`;
    const matches = scenarios.filter(
      (scenario) =>
        isRecord(scenario) && scenario.scenarioId === scenarioId,
    );
    if (matches.length !== 1) {
      return false;
    }
    const [scenario] = matches;
    if (
      !isRecord(scenario) ||
      !hasExactRecordValues(scenario.assertions, {
        normalConditionEnergizedOutput: true,
        deniedConditionDeenergizedOutput: true,
        restoredConditionReenergizedOutput: true,
      }) ||
      !isRecord(scenario.inputAssignments) ||
      !hasExactRecordValues(scenario.inputAssignments.normal, binding.normalInputState) ||
      !hasExactRecordValues(scenario.inputAssignments.deny, binding.deniedInputState) ||
      !hasExactRecordValues(scenario.inputAssignments.restore, binding.normalInputState) ||
      !exactOrderedStrings(
        Object.keys(scenario.inputAssignments).sort(),
        ["deny", "normal", "restore"],
      )
    ) {
      return false;
    }
    const observations = scenario.observations;
    return (
      scenario.status === "passed" &&
      scenario.networkId === binding.networkId &&
      scenario.outputSignalId === binding.outputSignalId &&
      scenario.rungId === binding.rungId &&
      scenario.conditionKind === binding.conditionKind &&
      Array.isArray(observations) &&
      observations.length === 3 &&
      isExactNetworkOutputObservation(
        observations[0],
        binding,
        1,
      ) &&
      isExactNetworkOutputObservation(
        observations[1],
        binding,
        0,
      ) &&
      isExactNetworkOutputObservation(
        observations[2],
        binding,
        1,
      )
    );
  });
}

function areV5MoveScenariosVerified(
  receipt: NativeSimulationReceiptV5,
  expected: ExpectedMoveRungCoverage[],
  inputSignalIds: Set<string>,
): boolean {
  const actualCoverage = receipt.scenarioReceipt.coverage.moveRungs;
  if (actualCoverage.length !== expected.length) {
    return false;
  }
  const byDestination = new Map<string, ExpectedMoveRungCoverage[]>();
  for (const binding of expected) {
    const peers = byDestination.get(binding.action.destinationAddress) ?? [];
    peers.push(binding);
    byDestination.set(binding.action.destinationAddress, peers);
  }

  return expected.every((binding, index) => {
    const actual = actualCoverage[index];
    if (
      actual === undefined ||
      Object.keys(actual).length !== 5 ||
      actual.rungId !== binding.rungId ||
      actual.conditionKind !== binding.conditionKind ||
      actual.status !== "verified" ||
      !hasExactRecordValues(actual.action, binding.action) ||
      !isRecord(actual.inputAssignment)
    ) {
      return false;
    }
    const peers = byDestination.get(binding.action.destinationAddress) ?? [];
    const expectedInputIds = new Set<string>();
    for (const peer of peers) {
      const ids = reviewedConditionInputIds(peer.condition, inputSignalIds);
      if (ids === undefined) {
        return false;
      }
      ids.forEach((signalId) => expectedInputIds.add(signalId));
    }
    const assignmentKeys = Object.keys(actual.inputAssignment).sort();
    if (
      !exactOrderedStrings(assignmentKeys, [...expectedInputIds].sort()) ||
      Object.values(actual.inputAssignment).some(
        (value) => value !== 0 && value !== 1,
      )
    ) {
      return false;
    }
    const assignment = actual.inputAssignment as SimulationInputState;
    const active = peers.filter(
      (peer) =>
        reviewedInputConditionMatches(
          peer.condition,
          assignment,
          inputSignalIds,
        ) === true,
    );
    if (
      active.length !== 1 ||
      active[0]?.rungId !== binding.rungId ||
      peers.some(
        (peer) =>
          reviewedInputConditionMatches(
            peer.condition,
            assignment,
            inputSignalIds,
          ) === undefined,
      )
    ) {
      return false;
    }
    const scenario = receipt.scenarioReceipt.scenarios.find(
      (item) => item.scenarioId === `move_ladder:${binding.rungId}`,
    );
    const observation = isRecord(scenario) ? scenario.observation : undefined;
    return (
      isRecord(scenario) &&
      Object.keys(scenario).length === 8 &&
      scenario.status === "passed" &&
      scenario.rungId === binding.rungId &&
      scenario.conditionKind === binding.conditionKind &&
      hasExactRecordValues(scenario.action, binding.action) &&
      hasExactRecordValues(scenario.inputAssignment, assignment) &&
      hasExactRecordValues(scenario.assertions, {
        singleWriterSelected: true,
        destinationMatchedExpectedValue: true,
      }) &&
      isRecord(observation) &&
      typeof observation.beforeValue === "number" &&
      Number.isSafeInteger(observation.beforeValue) &&
      observation.beforeValue >= -32768 &&
      observation.beforeValue <= 32767 &&
      observation.beforeValue !== binding.action.sourceConstant &&
      hasExactRecordValues(observation, {
        address: binding.action.destinationAddress,
        byteOffset: Number(binding.action.destinationAddress.slice(1)) * 2,
        encoding: "little_endian_signed16",
        beforeValue: observation.beforeValue,
        expectedValue: binding.action.sourceConstant,
        observedValue: binding.action.sourceConstant,
        matched: true,
      })
    );
  });
}

function isVerifiedCompletion(
  operation: NativeProjectOperation,
): operation is VerifiedNativeProjectOperation {
  const receipt = operation.result?.buildBundleReceipt;
  const bundle = operation.request.buildBundle;
  const compiled = bundle?.compiledProgram;
  const expectedNetworkRungs = orderedExpectedNetworkRungs(operation);
  const expectedMovePlan = orderedExpectedMoveRungs(operation);
  const expectedScenarioIds =
    expectedNetworkRungs === undefined || expectedMovePlan === undefined
      ? undefined
      : expectedV4ScenarioIds(
          operation,
          expectedNetworkRungs,
          expectedMovePlan.moves,
        );
  const receiptVerified =
    receipt?.schemaVersion === "controlstudio-native-build-receipt/v3" &&
    receipt.status === "APPLIED_VERIFIED" &&
    receipt.phase === "engineered_control_program" &&
    receipt.bundleId === bundle?.bundleId &&
    compiled?.schemaVersion === "controlstudio-xg5000-mnemonic-program/v4" &&
    compiled.ladderIr.schemaVersion === "controlstudio-ld-rung-ir/v1" &&
    compiled.rungIds.length === compiled.ladderIr.rungs.length &&
    receipt.reviewedPlanDigest === operation.request.reviewedPlan.planDigest &&
    receipt.nativeReadback.identityVerified === true &&
    receipt.programReadback.programCheckPassed === true &&
    receipt.programReadback.finalView === "LD" &&
    receipt.programReadback.rungCount === compiled.rungIds.length &&
    Array.isArray(receipt.programReadback.rungIds) &&
    receipt.programReadback.rungIds.length === compiled.rungIds.length &&
    receipt.programReadback.rungIds.every(
      (rungId, index) => rungId === compiled.rungIds[index],
    ) &&
    receipt.programReadback.ladderIrSha256 === compiled.ladderIrSha256 &&
    receipt.programReadback.sourceSha256 === compiled.sourceSha256 &&
    receipt.programReadback.savedProjectSha256 ===
      operation.result?.projectSha256;
  const reopen = operation.result?.reopenVerification;
  const reopenReceipt = reopen?.receipt;
  const freshProcessReopenVerified =
    reopen?.state === "verified" &&
    reopenReceipt?.schemaVersion === "controlstudio-native-reopen-receipt/v2" &&
    reopenReceipt.status === "REOPEN_VERIFIED" &&
    reopenReceipt.buildBundleId === bundle?.bundleId &&
    reopenReceipt.reviewedPlanDigest === operation.request.reviewedPlan.planDigest &&
    reopenReceipt.sourceProcess.pid === operation.request.xg5000Pid &&
    reopenReceipt.reopenedProcess.independent === true &&
    reopenReceipt.reopenedProcess.pid !== operation.request.xg5000Pid &&
    reopenReceipt.projectReadback.projectUnchanged === true &&
    reopenReceipt.projectReadback.projectSha256 === operation.result?.projectSha256 &&
    reopenReceipt.programReadback.sourceSha256 ===
      receipt?.programReadback.sourceSha256 &&
    reopenReceipt.programReadback.rungCount === compiled?.rungIds.length &&
    Array.isArray(reopenReceipt.programReadback.rungIds) &&
    reopenReceipt.programReadback.rungIds.length === compiled?.rungIds.length &&
    reopenReceipt.programReadback.rungIds.every(
      (rungId, index) => rungId === compiled?.rungIds[index],
    ) &&
    reopenReceipt.programReadback.ladderIrSha256 === compiled?.ladderIrSha256 &&
    reopenReceipt.programReadback.programCheckPassed === true &&
    reopenReceipt.controllerConnected === false &&
    reopenReceipt.plcWritePerformed === false;
  const simulation = operation.result?.simulationVerification;
  const rawSimulationReceipt: unknown = simulation?.receipt;
  const simulationReceipt = isSupportedSimulationReceipt(rawSimulationReceipt)
    ? rawSimulationReceipt
    : undefined;
  const simulationNetworkEvidenceVerified =
    simulationReceipt !== undefined &&
    ((simulationReceipt.schemaVersion ===
      "controlstudio-native-simulation-receipt/v5" &&
      expectedNetworkRungs !== undefined &&
      expectedMovePlan !== undefined &&
      isV4NetworkCoverageVerified(simulationReceipt, expectedNetworkRungs) &&
      areV4NetworkScenariosVerified(
        simulationReceipt,
        expectedNetworkRungs,
        expectedScenarioIds,
      ) &&
      areV5MoveScenariosVerified(
        simulationReceipt,
        expectedMovePlan.moves,
        expectedMovePlan.inputSignalIds,
      )) ||
      (simulationReceipt.schemaVersion ===
        "controlstudio-native-simulation-receipt/v4" &&
        expectedNetworkRungs !== undefined &&
        expectedMovePlan?.moves.length === 0 &&
        isV4NetworkCoverageVerified(simulationReceipt, expectedNetworkRungs) &&
        areV4NetworkScenariosVerified(
          simulationReceipt,
          expectedNetworkRungs,
          expectedScenarioIds,
        )) ||
      (expectedMovePlan?.moves.length === 0 &&
        isLegacyV3NetworkCoverageVerified(
          simulationReceipt,
          expectedNetworkRungs,
        )));
  const stopUiState =
    simulationReceipt?.simulator.stop.postcondition.ui_state;
  const stopStatusEvidenceVerified =
    stopUiState?.verified === true &&
    stopUiState.method === "xg5000-simulator-status-pane/v1" &&
    stopUiState.classification === "STOP" &&
    stopUiState.pid > 0 &&
    stopUiState.hwnd > 0 &&
    stopUiState.foreground_pid === stopUiState.pid &&
    stopUiState.foreground_hwnd === stopUiState.hwnd &&
    stopUiState.status_candidate_count === 1 &&
    (stopUiState.status_text === "L, SIMULATOR, 정상" ||
      stopUiState.status_text === "L, SIMULATOR, NORMAL") &&
    stopUiState.geometry_verified === true &&
    stopUiState.capture_method === "PIL.ImageGrab/foreground-status-pane" &&
    stopUiState.consecutive_count === 3 &&
    stopUiState.selected_run.visible === true &&
    stopUiState.selected_run.enabled === true &&
    stopUiState.selected_stop.visible === true &&
    stopUiState.selected_stop.enabled === true &&
    Array.isArray(stopUiState.samples) &&
    stopUiState.samples.length === 3 &&
    stopUiState.samples.every(
      (sample, index) =>
        sample.index === index + 1 &&
        sample.classification === "STOP" &&
        sample.pixel_count > 0 &&
        sample.dominant_rgb.length === 3 &&
        sample.dominant_share >= 0.75 &&
        sample.stop_share >= 0.85 &&
        sample.run_share >= 0 &&
        sample.run_share < 0.1 &&
        sample.offline_share >= 0 &&
        sample.offline_share < 0.1 &&
        sample.stop_prototype_distance >= 0 &&
        sample.stop_prototype_distance <= 64 &&
        Math.hypot(
          sample.dominant_rgb[0] - 229,
          sample.dominant_rgb[1] - 20,
          sample.dominant_rgb[2],
        ) <= 64,
    );
  const nativeSimulationVerified =
    simulation?.state === "verified" &&
    isExplicitHumanAcceptedTrial(simulation.trial) &&
    simulationReceipt !== undefined &&
    simulationNetworkEvidenceVerified &&
    simulationReceipt.status === "SIMULATION_VERIFIED" &&
    simulationReceipt.buildBundleId === bundle?.bundleId &&
    simulationReceipt.reviewedPlanDigest ===
      operation.request.reviewedPlan.planDigest &&
    simulationReceipt.projectReadback.projectUnchangedDuringRuntime === true &&
    simulationReceipt.projectReadback.reopenedProjectSha256 ===
      reopenReceipt?.projectReadback.projectSha256 &&
    simulationReceipt.projectReadback.projectSha256 ===
      operation.result?.workspaceRegistration?.workspace?.workingCopy.currentSha256 &&
    simulationReceipt.simulationPreparation.hashBeforeSimulatorStart ===
      simulationReceipt.projectReadback.reopenedProjectSha256 &&
    simulationReceipt.simulationPreparation.hashAfterProgramWrite ===
      simulationReceipt.projectReadback.projectSha256 &&
    simulationReceipt.simulationPreparation.programReadback.sourceSha256 ===
      receipt?.programReadback.sourceSha256 &&
    simulationReceipt.simulationPreparation.programReadback.rungCount ===
      compiled?.rungIds.length &&
    Array.isArray(simulationReceipt.simulationPreparation.programReadback.rungIds) &&
    simulationReceipt.simulationPreparation.programReadback.rungIds.length ===
      compiled?.rungIds.length &&
    simulationReceipt.simulationPreparation.programReadback.rungIds.every(
      (rungId, index) => rungId === compiled?.rungIds[index],
    ) &&
    simulationReceipt.simulationPreparation.programReadback.ladderIrSha256 ===
      compiled?.ladderIrSha256 &&
    simulationReceipt.simulationPreparation.programReadback.programCheckPassed === true &&
    simulationReceipt.programBinding.sourceSha256 ===
      receipt?.programReadback.sourceSha256 &&
    simulationReceipt.programBinding.rungCount === compiled?.rungIds.length &&
    Array.isArray(simulationReceipt.programBinding.rungIds) &&
    simulationReceipt.programBinding.rungIds.length === compiled?.rungIds.length &&
    simulationReceipt.programBinding.rungIds.every(
      (rungId, index) => rungId === compiled?.rungIds[index],
    ) &&
    simulationReceipt.programBinding.ladderIrSha256 === compiled?.ladderIrSha256 &&
    simulationReceipt.simulator.product === "XG-SIM" &&
    simulationReceipt.simulator.programWritePerformed === true &&
    simulationReceipt.simulator.cpuRunVerified === true &&
    simulationReceipt.simulator.cpuStopped === true &&
    simulationReceipt.simulator.stop.ok === true &&
    simulationReceipt.simulator.stop.verified === true &&
    simulationReceipt.simulator.stop.confirmation.closed === true &&
    simulationReceipt.simulator.stop.postcondition.main_enabled === true &&
    stopStatusEvidenceVerified &&
    simulationReceipt.scenarioReceipt.status === "SCENARIOS_VERIFIED" &&
    simulationReceipt.scenarioReceipt.scenarios.length > 0 &&
    simulationReceipt.scenarioReceipt.scenarioCount ===
      simulationReceipt.scenarioReceipt.scenarios.length &&
    simulationReceipt.scenarioReceipt.outputBytesWritten === false &&
    simulationReceipt.inputWriteBoundary.onlyReviewedSimulationStimuli === true &&
    simulationReceipt.inputWriteBoundary.outputBytesWritten === false &&
    simulationReceipt.inputWriteBoundary.baselineRestoredAfterCpuStop === true &&
    simulationReceipt.controllerConnected === false &&
    simulationReceipt.plcWritePerformed === false;
  // XG-SIM evidence stays renderable for historic operations but is not part
  // of offline create/edit completion.
  void nativeSimulationVerified;
  return (
    operation.state === "completed" &&
    operation.result?.identityVerified === true &&
    operation.result.controllerConnected === false &&
    operation.result.plcWritePerformed === false &&
    typeof operation.result.projectPath === "string" &&
    operation.result.projectPath.length > 0 &&
    typeof operation.result.projectSha256 === "string" &&
    /^[0-9a-f]{64}$/i.test(operation.result.projectSha256) &&
    operation.result.engineeredProgramReady === true &&
    operation.result.workspaceBound === true &&
    operation.result.workspaceRegistration?.state === "captured_and_bound" &&
    hasExplicitCompiledCollectionScope(operation) &&
    receiptVerified &&
    freshProcessReopenVerified
  );
}

function expectedRecoveryProjectPath(
  operation: NativeProjectOperation,
): string {
  const destination = operation.destinationDirectory.replace(/[\\/]+$/, "");
  const projectName = operation.request.projectName.trim();
  return destination && projectName
    ? `${destination}\\${projectName}.xgwx`
    : "";
}

function verifiedCompletionNotice(operation: NativeProjectOperation): string {
  const registration = operation.result?.workspaceRegistration;
  const confirmedScope = confirmedCollectionScopeSummary(operation);
  if (registration?.state === "captured_and_bound") {
    const reopened = operation.result?.reopenVerification?.receipt?.reopenedProcess;
    return `새 오프라인 프로젝트에 ${confirmedScope}만 구성한 뒤 보호 작업 복사본을 별도 XG5000 PID ${reopened?.pid ?? "—"}에서 다시 열어 mnemonic·LD 왕복·프로그램 검사·파일 hash를 재확인했습니다. PLC 연결·다운로드·쓰기는 수행하지 않았습니다.`;
  }
  if (registration?.state === "captured_not_bound") {
    return "새 오프라인 프로젝트를 만들고 정체성을 확인했습니다. 보호 복사본은 캡처했지만 XG5000에 열린 생성 원본과 별개이므로 분석 세션에는 자동 연결하지 않았습니다.";
  }
  if (registration?.state === "capture_failed") {
    return "새 오프라인 프로젝트 생성은 확인했지만 분석용 보호 복사본 캡처에 실패했습니다. 기존 로컬 프로젝트 열기에서 생성 파일을 다시 여세요.";
  }
  if (operation.result?.recovery) {
    return "현재 XG5000 창과 생성 파일을 다시 읽어 프로젝트 생성 완료를 확인했습니다. 생성 요청을 다시 실행하지 않았습니다.";
  }
  if (operation.result?.buildBundleReceipt) {
    return `검토된 Engineering Plan ${operation.result.buildBundleReceipt.reviewedPlanDigest.slice(0, 12)}와 build bundle ${operation.result.buildBundleReceipt.bundleId.slice(0, 12)}로 ${confirmedScope}만 XG5000 프로그램에 구성하고, LD 변환·프로그램 검사·저장 readback을 확인했습니다.`;
  }
  return "XG5000에서 새 오프라인 프로젝트를 만들고 정체성을 확인했습니다.";
}

export function NativeProjectPanel({
  onClose,
  onStatusChanged,
  status,
  onEngineeringReady,
  initialNativeProjectId,
  initialProjectName = "",
  initialDescription = "",
  reviewedPlan,
  reviewedProjectName,
  reviewedTechnicalConfiguration,
}: NativeProjectPanelProps) {
  const [projectName, setProjectName] = useState(
    reviewedProjectName ?? initialProjectName,
  );
  const [workspaceParent, setWorkspaceParent] = useState("");
  const [plcSeries, setPlcSeries] = useState<"XGK" | "XGB">(
    reviewedTechnicalConfiguration?.plcSeries ?? "XGK",
  );
  const [cpuModel, setCpuModel] = useState(
    reviewedTechnicalConfiguration?.cpuModel ?? "",
  );
  const [programName, setProgramName] = useState(
    reviewedTechnicalConfiguration?.programName ?? "MAIN",
  );
  const language = "LD" as const;
  const [description, setDescription] = useState(
    reviewedTechnicalConfiguration?.description ?? initialDescription,
  );
  const [instances, setInstances] = useState<XG5000Instance[]>([]);
  const [selectedHwnd, setSelectedHwnd] = useState("");
  const [instancesLoading, setInstancesLoading] = useState(true);
  const [operationLoading, setOperationLoading] = useState(
    Boolean(initialNativeProjectId),
  );
  const [recoveryHwnd, setRecoveryHwnd] = useState("");
  const [reopenedHwnd, setReopenedHwnd] = useState("");
  const [recoveryObservedProjectPath, setRecoveryObservedProjectPath] =
    useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [operation, setOperation] = useState<NativeProjectOperation | null>(
    null,
  );
  const handoffInFlightRef = useRef<string | null>(null);
  const handoffCompletedRef = useRef<string | null>(null);
  const reviewedConfigurationLocked = Boolean(
    reviewedPlan && reviewedProjectName && reviewedTechnicalConfiguration,
  );
  const effectiveProjectName = reviewedConfigurationLocked
    ? reviewedProjectName!
    : projectName.trim();
  const effectivePlcSeries = reviewedConfigurationLocked
    ? reviewedTechnicalConfiguration!.plcSeries
    : plcSeries;
  const effectiveCpuModel = reviewedConfigurationLocked
    ? reviewedTechnicalConfiguration!.cpuModel
    : cpuModel.trim();
  const effectiveProgramName = reviewedConfigurationLocked
    ? reviewedTechnicalConfiguration!.programName
    : programName.trim();
  const effectiveLanguage = reviewedConfigurationLocked
    ? reviewedTechnicalConfiguration!.language
    : language;
  const effectiveDescription = reviewedConfigurationLocked
    ? reviewedTechnicalConfiguration!.description
    : description.trim();

  const multipleInstances = instances.length > 1;
  const selectedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === selectedHwnd) ??
      null,
    [instances, selectedHwnd],
  );
  const selectedRecoveryInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === recoveryHwnd) ??
      null,
    [instances, recoveryHwnd],
  );
  const selectedReopenedInstance = useMemo(
    () =>
      instances.find((instance) => String(instance.hwnd) === reopenedHwnd) ??
      null,
    [instances, reopenedHwnd],
  );
  const recoveryMode = Boolean(initialNativeProjectId);
  const recoveryTargetMatchesStored =
    Boolean(selectedRecoveryInstance) &&
    selectedRecoveryInstance?.pid === operation?.request.xg5000Pid &&
    selectedRecoveryInstance?.hwnd === operation?.request.xg5000Hwnd;
  const storedRecoveryTargetPresent = instances.some(
    (instance) =>
      instance.pid === operation?.request.xg5000Pid &&
      instance.hwnd === operation?.request.xg5000Hwnd,
  );
  const recoveryTargetIsUniqueIdleReplacement =
    Boolean(selectedRecoveryInstance) &&
    !storedRecoveryTargetPresent &&
    instances.length === 1 &&
    selectedRecoveryInstance?.title?.trim() === "XG5000";
  const recoveryTargetCanBeProbed =
    recoveryTargetMatchesStored || recoveryTargetIsUniqueIdleReplacement;
  const completedVerificationContinuation =
    operation?.state === "completed" &&
    operation.result?.engineeredProgramReady !== true &&
    operation.result?.workspaceBound !== true &&
    operation.result?.workspaceRegistration?.state === "captured_not_bound" &&
    operation.result?.reopenVerification !== undefined &&
    operation.result?.simulationVerification?.state !== "verified";
  const reuseVerifiedReopen =
    operation?.result?.reopenVerification?.state === "verified";
  const storedResumeSourceIsValid =
    typeof operation?.request.xg5000Pid === "number" &&
    Number.isSafeInteger(operation.request.xg5000Pid) &&
    typeof operation.request.xg5000Hwnd === "number" &&
    Number.isSafeInteger(operation.request.xg5000Hwnd);
  const reopenedTargetIsIndependent =
    Boolean(selectedReopenedInstance) &&
    selectedReopenedInstance?.pid !== operation?.request.xg5000Pid;

  const loadInstances = async () => {
    setInstancesLoading(true);
    setNotice("");
    try {
      const response = await hostApi.getXG5000Instances();
      setInstances(response.instances);
      setRecoveryHwnd((current) =>
        response.instances.some((item) => String(item.hwnd) === current)
          ? current
          : "",
      );
      setReopenedHwnd((current) =>
        response.instances.some((item) => String(item.hwnd) === current)
          ? current
          : "",
      );
      setSelectedHwnd((current) => {
        if (response.instances.some((item) => String(item.hwnd) === current)) {
          return current;
        }
        return response.instances.length === 1
          ? String(response.instances[0]?.hwnd ?? "")
          : "";
      });
    } catch (error) {
      setInstances([]);
      setSelectedHwnd("");
      setNotice(
        errorMessage(error, "XG5000 실행 상태를 확인하지 못했습니다."),
      );
    } finally {
      setInstancesLoading(false);
    }
  };

  useEffect(() => {
    void loadInstances();
    // The target list is intentionally captured once when the panel opens.
    // Users can request an explicit refresh before creating the project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!initialNativeProjectId) {
      setOperationLoading(false);
      return;
    }
    const controller = new AbortController();
    setOperationLoading(true);
    setNotice("");
    void hostApi
      .getNativeProject(initialNativeProjectId, controller.signal)
      .then((restored) => {
        setOperation(restored);
        if (restored.state === "quarantined") {
          setNotice(
            "생성 결과가 불명확해 격리된 작업입니다. 생성 요청은 다시 실행하지 않습니다. 정확한 XG5000 창을 직접 선택한 뒤 현재 상태만 다시 검사하세요.",
          );
        } else if (isVerifiedCompletion(restored)) {
          setNotice(verifiedCompletionNotice(restored));
        } else if (
          restored.result?.simulationVerification?.trial?.state === "OBSERVING"
        ) {
          setNotice(
            "XG-SIM 시나리오가 통과했고 CPU 정지와 입력 원복도 확인됐습니다. 아래 runtime receipt를 채택하거나 원복 완료 상태로 닫아 주세요.",
          );
        } else if (
          restored.result?.simulationVerification?.trial?.state === "QUARANTINED"
        ) {
          setNotice(
            restored.result.simulationVerification.detail ??
              "XG-SIM의 CPU 정지와 입력 원복을 증명하지 못해 시험 결과를 격리했습니다.",
          );
        } else if (restored.state === "failed") {
          setNotice(
            restored.errorDetail ??
              "현재 증거로 프로젝트가 생성되지 않은 것이 확인되었습니다.",
          );
        } else {
          setNotice(
            "Host가 소유한 생성 작업을 복원했습니다. 새 생성 요청을 보내지 않고 저장된 작업 상태만 확인합니다.",
          );
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setNotice(
            errorMessage(
              error,
              "중단된 새 프로젝트 생성 작업을 불러오지 못했습니다.",
            ),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setOperationLoading(false);
        }
      });
    return () => controller.abort();
  }, [initialNativeProjectId]);

  useEffect(() => {
    if (!operation || !isVerifiedCompletion(operation)) {
      return;
    }
    const acceptedWorkspace =
      operation.result.workspaceRegistration?.workspace;
    const currentRevision =
      status.workspace?.revision ?? status.workspace?.artifactHash;
    if (
      !acceptedWorkspace ||
      status.engineeringSystem.readReady !== true ||
      !status.engineeringSystem.sessionId ||
      !status.workspace ||
      status.workspace.workspaceId !== acceptedWorkspace.workspaceId ||
      currentRevision !== acceptedWorkspace.revisionId
    ) {
      return;
    }
    const handoffKey = [
      operation.nativeProjectId,
      status.workspace.workspaceId,
      currentRevision,
      status.engineeringSystem.sessionId,
    ].join(":");
    if (
      handoffCompletedRef.current === handoffKey ||
      handoffInFlightRef.current === handoffKey
    ) {
      return;
    }
    handoffInFlightRef.current = handoffKey;
    void onEngineeringReady(operation, status)
      .then(() => {
        handoffCompletedRef.current = handoffKey;
      })
      .catch((error: unknown) => {
        setNotice(
          errorMessage(
            error,
            "Accepted project could not be transferred to its engineering conversation.",
          ),
        );
      })
      .finally(() => {
        if (handoffInFlightRef.current === handoffKey) {
          handoffInFlightRef.current = null;
        }
      });
  }, [onEngineeringReady, operation, status]);

  const validate = (): string | null => {
    if (!effectiveProjectName) {
      return "프로젝트 이름을 입력해 주세요.";
    }
    if (!workspaceParent.trim() || !isAbsoluteWindowsPath(workspaceParent.trim())) {
      return "작업공간 상위 폴더를 Windows 절대 경로로 입력해 주세요.";
    }
    if (!effectivePlcSeries || !effectiveCpuModel) {
      return "XG5000에 표시되는 PLC 시리즈와 CPU 모델을 입력해 주세요.";
    }
    if (!["XGK", "XGB"].includes(effectivePlcSeries)) {
      return "제어 프로그램 생성은 현재 XGK 또는 XGB만 지원합니다.";
    }
    if (!effectiveProgramName) {
      return "첫 프로그램 이름을 입력해 주세요.";
    }
    if (
      !recoveryMode &&
      (!reviewedPlan ||
        !reviewedProjectName ||
        !reviewedTechnicalConfiguration)
    ) {
      return "최신 제안의 전 항목을 확인한 v2 확정 실행 설계가 필요합니다.";
    }
    if (
      !recoveryMode &&
      reviewedPlan &&
      (reviewedPlan.schemaVersion !== "2" ||
        !/^[0-9a-f]{64}$/.test(reviewedPlan.planDigest) ||
        !/^[0-9a-f]{64}$/.test(reviewedPlan.projectBriefDigest) ||
        !/^[0-9a-f]{64}$/.test(reviewedPlan.attachmentRevisionDigest) ||
        reviewedPlan.reviewedSectionIds.length === 0 ||
        reviewedPlan.reviewedItemIds.length === 0 ||
        new Set(reviewedPlan.reviewedSectionIds).size !==
          reviewedPlan.reviewedSectionIds.length ||
        new Set(reviewedPlan.reviewedItemIds).size !==
          reviewedPlan.reviewedItemIds.length)
    ) {
      return "확정 실행 설계의 계획·브리프·첨부 revision 결속 또는 전 항목 검토 범위가 올바르지 않습니다.";
    }
    if (instances.length === 0) {
      return "XG5000을 실행한 뒤 빈 창을 다시 확인해 주세요.";
    }
    if (multipleInstances && !selectedInstance) {
      return "여러 XG5000 창 중 새 프로젝트를 만들 정확한 PID/HWND를 선택해 주세요.";
    }
    return null;
  };

  const createProject = async () => {
    if (busy) {
      return;
    }
    const validationError = validate();
    if (validationError) {
      setNotice(validationError);
      return;
    }
    if (!reviewedPlan) {
      setNotice("최신 Engineering Plan 검토 기록을 확인하지 못했습니다.");
      return;
    }
    if (!selectedInstance) {
      setNotice("XG5000 대상의 정확한 PID/HWND를 선택해 주세요.");
      return;
    }

    const request: CreateNativeProjectRequest = {
      operationId: newOperationId(),
      projectName: effectiveProjectName,
      workspaceParent: workspaceParent.trim(),
      plcSeries: effectivePlcSeries,
      cpuModel: effectiveCpuModel,
      programName: effectiveProgramName,
      language: effectiveLanguage,
      description: effectiveDescription,
      reviewedPlan,
      xg5000Pid: selectedInstance.pid,
      xg5000Hwnd: selectedInstance.hwnd,
    };

    setBusy(true);
    setNotice("");
    setOperation(null);
    try {
      const created = await hostApi.createNativeProject(request);
      setOperation(created);
      if (isVerifiedCompletion(created)) {
        setNotice(verifiedCompletionNotice(created));
        onStatusChanged(await hostApi.getStatus());
      } else if (
        created.result?.simulationVerification?.trial?.state === "OBSERVING"
      ) {
        setNotice(
          "XG-SIM 시나리오가 통과했고 CPU 정지와 입력 원복도 확인됐습니다. runtime receipt를 검토한 뒤 채택 또는 원복 완료를 선택해 주세요.",
        );
      } else if (created.state === "completed") {
        setNotice(
          created.result?.simulationVerification?.detail ??
            created.result?.reopenVerification?.detail ??
            "생성·저장은 확인했지만 독립 재열기와 XG-SIM 운전 인수가 완료되지 않았습니다.",
        );
      } else if (created.state === "failed") {
        setNotice(
          created.errorDetail ??
            "새 프로젝트 생성이 실패했습니다. XG5000 상태를 확인해 주세요.",
        );
      } else if (created.state === "quarantined") {
        setNotice(
          created.errorDetail ??
            "생성 결과를 확정할 수 없어 작업을 격리했습니다. 다시 실행하지 마세요.",
        );
      } else {
        setNotice(
          "Host가 새 프로젝트를 만드는 중입니다. 완료 전에는 같은 요청을 다시 보내지 마세요.",
        );
      }
    } catch (error) {
      setNotice(errorMessage(error, "새 프로젝트를 만들지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const refreshOperation = async () => {
    if (!operation || busy) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.getNativeProject(operation.nativeProjectId);
      setOperation(next);
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
        onStatusChanged(await hostApi.getStatus());
      } else if (
        next.result?.simulationVerification?.trial?.state === "OBSERVING"
      ) {
        setNotice(
          "XG-SIM runtime receipt가 채택 결정을 기다리고 있습니다. CPU는 정지됐고 원래 입력값은 복원됐습니다.",
        );
      } else if (next.state === "completed") {
        setNotice(
          next.result?.simulationVerification?.detail ??
            next.result?.reopenVerification?.detail ??
            "생성·저장은 확인했지만 독립 재열기와 XG-SIM 운전 인수가 완료되지 않았습니다.",
        );
      } else if (next.state === "failed" || next.state === "quarantined") {
        setNotice(
          next.errorDetail ??
            (next.state === "quarantined"
              ? "결과를 확정할 수 없어 작업을 격리했습니다."
              : "새 프로젝트 생성이 실패했습니다."),
        );
      } else {
        setNotice("아직 생성 중입니다. 같은 요청을 다시 보내지 마세요.");
      }
    } catch (error) {
      setNotice(
        errorMessage(error, "새 프로젝트 생성 상태를 확인하지 못했습니다."),
      );
    } finally {
      setBusy(false);
    }
  };

  const decideSimulationTrial = async (
    decision: "accept" | "revert" | "acknowledge_quarantine",
  ) => {
    const trial = operation?.result?.simulationVerification?.trial;
    const resolvingObservedReceipt =
      trial?.state === "OBSERVING" &&
      typeof trial.receiptDigest === "string" &&
      decision !== "acknowledge_quarantine";
    const acknowledgingQuarantine =
      trial?.state === "QUARANTINED" && decision === "acknowledge_quarantine";
    if (
      !operation ||
      (!resolvingObservedReceipt && !acknowledgingQuarantine) ||
      busy
    ) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.decideNativeSimulationTrial(
        operation.nativeProjectId,
        {
          decision,
          ...(trial?.receiptDigest
            ? { expectedReceiptDigest: trial.receiptDigest }
            : {}),
        },
      );
      setOperation(next);
      if (decision === "accept" && isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
        try {
          onStatusChanged(await hostApi.getStatus());
        } catch {
          // The durable accepted receipt remains authoritative if status refresh fails.
        }
      } else if (
        next.result?.simulationVerification?.trial?.state === "REVERTED"
      ) {
        setNotice(
          "XG-SIM 시험 결과를 채택하지 않았습니다. CPU 정지와 원래 입력값 원복 증거는 보존했고 작업공간은 활성 대상으로 연결하지 않았습니다.",
        );
        try {
          onStatusChanged(await hostApi.getStatus());
        } catch {
          // The durable reverted receipt remains authoritative if status refresh fails.
        }
      } else if (
        next.result?.simulationVerification?.trial?.state ===
        "QUARANTINE_ACKNOWLEDGED"
      ) {
        setNotice(
          "복구 증거가 없는 XG-SIM 격리 상태를 확인했습니다. CPU 정지나 입력 원복을 완료로 주장하지 않으며 작업공간도 연결하지 않습니다.",
        );
        try {
          onStatusChanged(await hostApi.getStatus());
        } catch {
          // The durable acknowledgement remains authoritative if status refresh fails.
        }
      } else {
        setNotice("XG-SIM 시험 결정을 저장했지만 현재 상태를 확정하지 못했습니다.");
      }
    } catch (error) {
      setNotice(errorMessage(error, "XG-SIM 시험 결정을 저장하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const reconcileOperation = async () => {
    if (
      !operation ||
      operation.state !== "quarantined" ||
      !selectedRecoveryInstance ||
      !recoveryTargetCanBeProbed ||
      busy
    ) {
      return;
    }
    setBusy(true);
    setNotice("");
    const expectedProjectPath = expectedRecoveryProjectPath(operation);
    try {
      const next = await hostApi.reconcileNativeProject(
        operation.nativeProjectId,
        {
          pid: selectedRecoveryInstance.pid,
          hwnd: selectedRecoveryInstance.hwnd,
          expectedProjectPath,
          ...(recoveryObservedProjectPath.trim()
            ? { observedProjectPath: recoveryObservedProjectPath.trim() }
            : {}),
          ...(operation.result?.projectSha256
            ? { expectedProjectSha256: operation.result.projectSha256 }
            : {}),
        },
      );
      setOperation(next);
      setRecoveryHwnd("");
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
      } else if (next.state === "failed") {
        setNotice(
          next.errorDetail ??
            "할당된 폴더가 비어 있고 선택한 XG5000 창에는 다른 프로젝트가 열려 있어, 프로젝트가 생성되지 않은 것으로 확인되었습니다.",
        );
      } else {
        setNotice(
          next.errorDetail ??
            "현재 증거만으로 결과를 확정할 수 없어 격리를 유지합니다.",
        );
      }
      try {
        onStatusChanged(await hostApi.getStatus());
      } catch {
        // Reconciliation evidence remains authoritative even if status refresh fails.
      }
    } catch (error) {
      setNotice(errorMessage(error, "현재 XG5000 상태를 다시 검사하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const resumeNativeOperation = async () => {
    if (
      !operation ||
      operation.state !== "quarantined" ||
      !selectedRecoveryInstance ||
      !recoveryTargetMatchesStored ||
      !operation.requestDigest ||
      busy
    ) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.resumeNativeProject(operation.nativeProjectId, {
        pid: selectedRecoveryInstance.pid,
        hwnd: selectedRecoveryInstance.hwnd,
        expectedRequestDigest: operation.requestDigest,
      });
      setOperation(next);
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
      } else if (next.state === "quarantined") {
        setNotice(
          next.errorDetail ??
            "입력된 LD의 검사·저장 재개 결과를 확정할 수 없어 격리를 유지합니다.",
        );
      } else if (next.state === "completed") {
        setNotice(
          next.result?.simulationVerification?.detail ??
            next.result?.reopenVerification?.detail ??
            "입력된 LD의 검사·저장은 확인했지만 독립 재열기와 XG-SIM 운전 인수가 완료되지 않았습니다.",
        );
      } else {
        setNotice("입력된 LD의 검사·저장 재개 상태를 확인하고 있습니다.");
      }
      try {
        onStatusChanged(await hostApi.getStatus());
      } catch {
        // The durable resume result remains authoritative if status refresh fails.
      }
    } catch (error) {
      setNotice(errorMessage(error, "입력된 LD의 검사·저장을 재개하지 못했습니다."));
    } finally {
      setBusy(false);
    }
  };

  const continueCompletedVerification = async () => {
    const sourcePid = operation?.request.xg5000Pid;
    const sourceHwnd = operation?.request.xg5000Hwnd;
    if (
      !operation ||
      !completedVerificationContinuation ||
      typeof sourcePid !== "number" ||
      !Number.isSafeInteger(sourcePid) ||
      typeof sourceHwnd !== "number" ||
      !Number.isSafeInteger(sourceHwnd) ||
      (!reuseVerifiedReopen &&
        (!selectedReopenedInstance || !reopenedTargetIsIndependent)) ||
      !operation.requestDigest ||
      busy
    ) {
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const next = await hostApi.resumeNativeProject(operation.nativeProjectId, {
        pid: sourcePid,
        hwnd: sourceHwnd,
        expectedRequestDigest: operation.requestDigest,
        ...(!reuseVerifiedReopen && selectedReopenedInstance
          ? {
              reopenedPid: selectedReopenedInstance.pid,
              reopenedHwnd: selectedReopenedInstance.hwnd,
            }
          : {}),
      });
      setOperation(next);
      if (isVerifiedCompletion(next)) {
        setNotice(verifiedCompletionNotice(next));
      } else if (next.state === "quarantined") {
        setNotice(
          next.errorDetail ??
            "독립 재열기 또는 XG-SIM 검증 결과를 확정할 수 없어 격리를 유지합니다.",
        );
      } else if (next.state === "completed") {
        setNotice(
          next.result?.simulationVerification?.detail ??
            next.result?.reopenVerification?.detail ??
            "XG-SIM receipt의 명시적 채택 전에는 엔지니어링 준비 완료가 아닙니다.",
        );
      } else {
        setNotice("독립 재열기와 XG-SIM 검증 상태를 확인하고 있습니다.");
      }
      try {
        onStatusChanged(await hostApi.getStatus());
      } catch {
        // The durable continuation result remains authoritative if status refresh fails.
      }
    } catch (error) {
      setNotice(
        errorMessage(error, "저장된 프로젝트의 독립 재열기 검증을 재개하지 못했습니다."),
      );
    } finally {
      setBusy(false);
    }
  };

  const rawSimulationReceipt: unknown =
    operation?.result?.simulationVerification?.receipt;
  const simulationReceipt = isSupportedSimulationReceipt(rawSimulationReceipt)
    ? rawSimulationReceipt
    : undefined;
  const unsupportedSimulationReceipt =
    rawSimulationReceipt !== undefined && simulationReceipt === undefined;
  const simulationTrial = operation?.result?.simulationVerification?.trial;
  const explicitHumanAcceptance = isExplicitHumanAcceptedTrial(simulationTrial);
  const preTrialMigration = isPreTrialMigration(simulationTrial);
  const decisionNetworkRungs = operation
    ? orderedExpectedNetworkRungs(operation)
    : undefined;
  const decisionMovePlan = operation
    ? orderedExpectedMoveRungs(operation)
    : undefined;
  const decisionScenarioIds =
    operation && decisionNetworkRungs && decisionMovePlan
      ? expectedV4ScenarioIds(
          operation,
          decisionNetworkRungs,
          decisionMovePlan.moves,
        )
      : undefined;
  const simulationAcceptanceEligible =
    operation !== null &&
    operation !== undefined &&
    simulationReceipt !== undefined &&
    decisionNetworkRungs !== undefined &&
    decisionMovePlan !== undefined &&
    isV4NetworkCoverageVerified(simulationReceipt, decisionNetworkRungs) &&
    areV4NetworkScenariosVerified(
      simulationReceipt,
      decisionNetworkRungs,
      decisionScenarioIds,
    ) &&
    ((simulationReceipt.schemaVersion ===
      "controlstudio-native-simulation-receipt/v5" &&
      areV5MoveScenariosVerified(
        simulationReceipt,
        decisionMovePlan.moves,
        decisionMovePlan.inputSignalIds,
      )) ||
      (simulationReceipt.schemaVersion ===
        "controlstudio-native-simulation-receipt/v4" &&
        decisionMovePlan.moves.length === 0));
  const confirmedControlScope = operation
    ? confirmedCollectionScopeSummary(operation)
    : "collectionScope의 confirmed 항목";
  const terminal = operation ? TERMINAL_STATES.has(operation.state) : false;
  const failure =
    operation?.state === "failed" ||
    operation?.state === "quarantined" ||
    (operation?.state === "completed" &&
      !isVerifiedCompletion(operation) &&
      simulationTrial?.state !== "OBSERVING" &&
      simulationTrial?.state !== "REVERTED");
  const canEditRequest =
    !recoveryMode &&
    (!operation ||
      (operation.state === "failed" && operation.result === undefined));

  return (
    <section
      className="native-project-panel"
      aria-labelledby="native-project-title"
    >
      <div className="instance-picker-heading">
        <div>
          <p className="eyebrow">
            {simulationTrial?.state === "OBSERVING"
              ? "LS XG5000 · SIMULATION REVIEW"
              : simulationTrial?.state === "QUARANTINED"
                ? "LS XG5000 · SIMULATION RECOVERY"
                : simulationTrial?.state === "QUARANTINE_ACKNOWLEDGED"
                  ? "LS XG5000 · SIMULATION QUARANTINE"
                : completedVerificationContinuation
                    ? "LS XG5000 · SAVED PROJECT VERIFICATION"
                    : recoveryMode
                      ? "LS XG5000 · QUARANTINE RECOVERY"
                      : "STEP 3 · LS XG5000 TECHNICAL SETUP"}
          </p>
          <h2 id="native-project-title">
            {simulationTrial?.state === "OBSERVING"
              ? "XG-SIM 실행 결과 검토"
              : simulationTrial?.state === "QUARANTINED"
                ? "XG-SIM 상태 복구 필요"
                : simulationTrial?.state === "QUARANTINE_ACKNOWLEDGED"
                  ? "XG-SIM 미복구 상태 확인됨"
                : completedVerificationContinuation
                    ? "저장된 프로젝트 검증 재개"
                    : recoveryMode
                      ? "새 프로젝트 생성 결과 복구"
                      : "XG5000 제어 프로그램 구성"}
          </h2>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label={
            simulationTrial?.state === "OBSERVING"
              ? "XG-SIM 실행 결과 검토 닫기"
              : completedVerificationContinuation
                ? "저장된 프로젝트 검증 재개 닫기"
              : recoveryMode
                ? "새 프로젝트 생성 복구 닫기"
                : "새 프로젝트 만들기 닫기"
          }
          disabled={busy || operationLoading}
          onClick={onClose}
        >
          <Icon name="close" size={18} />
        </button>
      </div>

      <p className="native-project-explanation">
        {recoveryMode
          ? "Host에 저장된 동일 작업의 파일과 XG5000 현재 상태만 다시 읽습니다. 생성 요청 재실행, 자동 재적용, PLC 연결·다운로드·쓰기는 수행하지 않습니다."
          : "검토한 구조화 설계의 ladderRungs 순서·조건 분기·접점 극성·typed 명령만 XG5000의 빈 오프라인 프로젝트와 선택한 첫 프로그램에 적용합니다. 저장 뒤 보호 작업 복사본을 별도 새 XG5000 프로세스에서 다시 열어 rung IR/source digest·mnemonic·LD·프로그램 검사·파일 hash를 재확인하며 PLC 연결·다운로드·쓰기는 수행하지 않습니다."}
      </p>

      {operationLoading && (
        <p className="instance-notice" role="status">
          저장된 생성 작업을 복원하고 있습니다…
        </p>
      )}

      {canEditRequest && (
        <form
          className="native-project-form"
          onSubmit={(event) => {
            event.preventDefault();
            void createProject();
          }}
        >
          <label>
            <span>프로젝트 이름</span>
            <input
              type="text"
              value={effectiveProjectName}
              maxLength={80}
              autoComplete="off"
              disabled={busy || reviewedConfigurationLocked}
              placeholder="AssemblyLine01"
              onChange={(event) => setProjectName(event.target.value)}
            />
          </label>

          <label className="native-project-wide">
            <span>작업공간 상위 폴더</span>
            <input
              type="text"
              value={workspaceParent}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
              placeholder="C:\ControlStudio\workspaces"
              onChange={(event) => setWorkspaceParent(event.target.value)}
            />
            <small>
              기존 파일을 덮어쓰지 않습니다. Host가 이 폴더 아래에 새 전용
              폴더를 만듭니다.
            </small>
          </label>

          <label>
            <span>PLC 시리즈</span>
            <select
              value={effectivePlcSeries}
              disabled={busy || reviewedConfigurationLocked}
              onChange={(event) =>
                setPlcSeries(event.target.value === "XGB" ? "XGB" : "XGK")
              }
            >
              <option value="XGK" />
              <option value="XGB" />
            </select>
          </label>

          <label>
            <span>CPU 모델</span>
            <input
              type="text"
              value={effectiveCpuModel}
              maxLength={64}
              autoComplete="off"
              disabled={busy || reviewedConfigurationLocked}
              placeholder="XGK-CPUE"
              onChange={(event) => setCpuModel(event.target.value)}
            />
          </label>

          <label>
            <span>첫 프로그램 이름</span>
            <input
              type="text"
              value={effectiveProgramName}
              maxLength={80}
              autoComplete="off"
              disabled={busy || reviewedConfigurationLocked}
              onChange={(event) => setProgramName(event.target.value)}
            />
          </label>

          <label>
            <span>언어</span>
            <select
              value={effectiveLanguage}
              disabled
            >
              <option value="LD">LD</option>
            </select>
          </label>

          <label className="native-project-wide">
            <span>설명 (선택)</span>
            <textarea
              value={effectiveDescription}
              maxLength={1024}
              rows={3}
              disabled={busy || reviewedConfigurationLocked}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>

          {reviewedConfigurationLocked && (
            <p className="native-project-wide native-project-reviewed-configuration">
              프로젝트 이름·PLC 시리즈·CPU·프로그램·언어·설명은 검토한
              Engineering Plan 값입니다. 이 화면에서는 작업 폴더와 대상 XG5000
              창만 선택할 수 있습니다.
            </p>
          )}

          <fieldset className="native-project-target">
            <legend>XG5000 대상</legend>
            <div>
              {instancesLoading ? (
                <p>실행 중인 XG5000을 확인하고 있습니다…</p>
              ) : instances.length === 0 ? (
                <p>
                  실행 중인 XG5000이 없습니다. XG5000을 직접 실행한 뒤 빈
                  창이 보이면 다시 확인해 주세요.
                </p>
              ) : instances.length === 1 ? (
                <p>
                  <strong>{displayInstance(instances[0]!)}</strong>
                  <span>
                    이 창이 비어 있지 않거나 대화상자가 열려 있으면 Host가
                    생성을 거부합니다.
                  </span>
                </p>
              ) : (
                <label>
                  <span>새 프로젝트를 만들 빈 XG5000 창</span>
                  <select
                    value={selectedHwnd}
                    disabled={busy}
                    onChange={(event) => setSelectedHwnd(event.target.value)}
                  >
                    <option value="">정확한 PID/HWND를 선택해 주세요</option>
                    {instances.map((instance) => (
                      <option key={instance.hwnd} value={instance.hwnd}>
                        {displayInstance(instance)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                className="button secondary-button"
                type="button"
                disabled={busy || instancesLoading}
                onClick={() => void loadInstances()}
              >
                XG5000 다시 확인
              </button>
            </div>
          </fieldset>

          <div className="native-project-safety">
            <Icon name="shield" size={17} />
            <span>
              열린 프로젝트, 예상하지 않은 대화상자, 권한 불일치, 기존 파일
              충돌이 있으면 변경 없이 중단합니다.
            </span>
          </div>

          <div className="native-project-actions">
            <button
              className="button primary-button"
              type="submit"
              disabled={
                busy ||
                instancesLoading ||
                !effectiveProjectName ||
                !workspaceParent.trim() ||
                !effectivePlcSeries ||
                !effectiveCpuModel ||
                !effectiveProgramName ||
                !reviewedPlan ||
                !reviewedConfigurationLocked ||
                instances.length === 0 ||
                (multipleInstances && !selectedInstance)
              }
            >
              {busy ? "XG5000 확인 및 생성 중…" : "오프라인 프로젝트 만들기"}
            </button>
          </div>
        </form>
      )}

      {operation && (
        <section
          className={`native-project-result ${
            failure ? "native-project-result-failed" : ""
          }`}
          aria-labelledby="native-project-result-title"
        >
          <div className="native-project-result-heading">
            <span>
              <Icon
                name={failure ? "warning" : terminal ? "check" : "activity"}
                size={18}
              />
            </span>
            <div>
              <p className="eyebrow">작업 {operation.nativeProjectId}</p>
              <h3 id="native-project-result-title">
                {isVerifiedCompletion(operation)
                  ? "프로젝트 생성 완료"
                  : simulationTrial?.state === "OBSERVING"
                    ? "XG-SIM 결과 채택 대기"
                    : simulationTrial?.state === "QUARANTINED"
                      ? "XG-SIM 결과 격리"
                      : simulationTrial?.state === "QUARANTINE_ACKNOWLEDGED"
                        ? "XG-SIM 격리 경고 확인됨"
                    : simulationTrial?.state === "REVERTED"
                      ? "XG-SIM 시험 원복 완료"
                  : operation.state === "failed"
                    ? recoveryMode
                      ? "프로젝트 미생성 확인"
                      : "프로젝트 생성 실패"
                    : operation.state === "quarantined"
                      ? "생성 결과 확인 필요"
                      : operation.state === "completed"
                        ? "생성 결과 확인 필요"
                        : "프로젝트 생성 중"}
              </h3>
            </div>
          </div>

          {typeof operation.result?.projectPath === "string" &&
            operation.result.projectPath && (
            <dl className="native-project-proof">
              <div>
                <dt>프로젝트</dt>
                <dd>{operation.request.projectName}</dd>
              </div>
              <div>
                <dt>파일</dt>
                <dd>{operation.result.projectPath}</dd>
              </div>
              <div>
                <dt>구성</dt>
                <dd>
                  {operation.request.plcSeries} · {operation.request.cpuModel} ·{" "}
                  {operation.request.language}
                </dd>
              </div>
              <div>
                <dt>정체성</dt>
                <dd>
                  {operation.result.identityVerified
                    ? "XG5000 창과 생성 파일 일치 확인"
                    : "확인되지 않음"}
                </dd>
              </div>
              <div className="native-project-proof-wide">
                <dt>SHA-256</dt>
                <dd>
                  {typeof operation.result.projectSha256 === "string" ? (
                    <code>{operation.result.projectSha256}</code>
                  ) : (
                    "확인되지 않음"
                  )}
                </dd>
              </div>
              {operation.result.buildBundleReceipt && (
                <div className="native-project-proof-wide">
                  <dt>Engineering Plan 적용</dt>
                  <dd>
                    <code>
                      plan {operation.result.buildBundleReceipt.reviewedPlanDigest.slice(0, 12)}
                      {" · "}bundle {operation.result.buildBundleReceipt.bundleId.slice(0, 12)}
                    </code>
                    {` · ${typeof operation.result.buildBundleReceipt.programReadback.rungCount === "number" ? `${operation.result.buildBundleReceipt.programReadback.rungCount}개 rung` : "legacy receipt"} · mnemonic readback · LD 변환 · 프로그램 검사 · 저장 확인`}
                  </dd>
                </div>
              )}
              {operation.result.reopenVerification?.receipt && (
                <div className="native-project-proof-wide">
                  <dt>독립 재열기 증거</dt>
                  <dd>
                    <code>
                      source PID {operation.result.reopenVerification.receipt.sourceProcess.pid}
                      {" → fresh PID "}
                      {operation.result.reopenVerification.receipt.reopenedProcess.pid}
                    </code>
                    {` · ${operation.result.reopenVerification.receipt.programReadback.rungCount ?? "legacy"}개 rung · IR ${operation.result.reopenVerification.receipt.programReadback.ladderIrSha256?.slice(0, 12) ?? "legacy"} · ${operation.result.reopenVerification.receipt.programReadback.exactInstructionReadback.length}개 mnemonic 행 재확인 · LD 왕복 · 프로그램 검사 · 파일 무변경`}
                    <small>
                      reopen rung 순서 {operation.result.reopenVerification.receipt.programReadback.rungIds?.join(" → ") ?? "legacy"}
                    </small>
                  </dd>
                </div>
              )}
              {simulationReceipt && (
                <div className="native-project-proof-wide">
                  <dt>XG-SIM 운전 인수</dt>
                  <dd>
                    <code>
                      {simulationReceipt.scenarioReceipt.scenarioCount}
                      개 시나리오 · CPU STOP · 입력 원복
                    </code>
                    {` · rung ${simulationReceipt.programBinding.rungCount ?? "legacy"} · IR ${simulationReceipt.programBinding.ladderIrSha256?.slice(0, 12) ?? "legacy"} · ${confirmedControlScope} 대상 시나리오 확인`}
                    <small>
                      simulation rung 순서 {simulationReceipt.programBinding.rungIds?.join(" → ") ?? "legacy"}
                    </small>
                    {simulationReceipt.schemaVersion !==
                    "controlstudio-native-simulation-receipt/v3" ? (
                      simulationReceipt.scenarioReceipt.coverage.networkRungs.map(
                        (binding) => (
                          <small
                            key={`${binding.networkId}:${binding.outputSignalId}:${binding.rungId}`}
                          >
                            network {binding.networkId} · output {binding.outputSignalId}
                            {" · "}OUT rung {binding.rungId} · {binding.conditionKind}
                            {" · "}{binding.status}
                          </small>
                        ),
                      )
                    ) : (
                      <small>
                        legacy v3 single-network OUT rung{" "}
                        {simulationReceipt.scenarioReceipt.coverage.selectedNetworkRung.rungId}
                        {" · "}
                        {simulationReceipt.scenarioReceipt.coverage.selectedNetworkRung.conditionKind}
                        {" · 저장된 인수 증거(자동 승격 없음)"}
                      </small>
                    )}
                    {simulationReceipt.schemaVersion ===
                      "controlstudio-native-simulation-receipt/v5" &&
                      simulationAcceptanceEligible &&
                      simulationReceipt.scenarioReceipt.coverage.moveRungs.map(
                        (binding) => {
                          const scenario =
                            simulationReceipt.scenarioReceipt.scenarios.find(
                              (item) =>
                                item.scenarioId ===
                                `move_ladder:${binding.rungId}`,
                            );
                          const observation = isRecord(scenario?.observation)
                            ? scenario.observation
                            : undefined;
                          return (
                            <small key={`mov:${binding.rungId}`}>
                              MOV rung {binding.rungId} · {binding.action.sourceConstant}
                              {" → "}{binding.action.destinationAddress}
                              {" · "}{binding.conditionKind}
                              {" · "}{binding.status}
                              {observation && (
                                <>
                                  {" · "}{String(observation.address)} before{" "}
                                  {String(observation.beforeValue)} → observed{" "}
                                  {String(observation.observedValue)} (expected{" "}
                                  {String(observation.expectedValue)})
                                </>
                              )}
                            </small>
                          );
                        },
                      )}
                    <small>
                      XG-SIM 준비 hash {simulationReceipt.projectReadback.reopenedProjectSha256.slice(0, 12)}
                      {" → "}
                      {simulationReceipt.projectReadback.projectSha256.slice(0, 12)}
                      {" · runtime 중 파일 hash 고정"}
                    </small>
                  </dd>
                </div>
              )}
              {unsupportedSimulationReceipt && (
                <div className="native-project-proof-wide">
                  <dt>XG-SIM 운전 인수</dt>
                  <dd>
                    지원하지 않거나 구조가 불완전한 simulation receipt입니다. 과거
                    증거로만 표시하며 완료·작업공간 연결 권한으로 사용하지 않습니다.
                  </dd>
                </div>
              )}
              {simulationTrial && (
                <div className="native-project-proof-wide">
                  <dt>XG-SIM trial 상태</dt>
                  <dd>
                    <code>
                      {typeof simulationTrial.state === "string"
                        ? simulationTrial.state
                        : "INVALID"}{" "}
                      · receipt{" "}
                      {typeof simulationTrial.receiptDigest === "string"
                        ? simulationTrial.receiptDigest.slice(0, 12)
                        : "미확정"}
                    </code>
                    {simulationTrial.state === "OBSERVING"
                      ? " · 실행 증거 검토와 명시적 채택 결정을 기다리는 중"
                      : simulationTrial.state === "ACCEPTED"
                        ? explicitHumanAcceptance
                          ? " · 명시적 사용자 채택 완료 · 보호 작업 복사본 연결"
                          : preTrialMigration
                            ? " · pre-trial migration 과거 증거 · 읽기 전용 · 작업공간 연결 완료로 인정하지 않음"
                            : " · 채택 결정 결속 불완전 · 읽기 전용 · 작업공간 미결속"
                        : simulationTrial.state === "REVERTED"
                          ? " · 실행 증거 미채택 · CPU 정지와 입력 원복 상태 유지"
                          : simulationTrial.state === "QUARANTINED"
                            ? " · CPU 정지 또는 입력 원복 증거 불완전 · 재실행 금지"
                            : " · 미복구 격리 경고 확인 · 작업공간 미결속"}
                  </dd>
                </div>
              )}
              {operation.result.buildBundleReceipt && (
                <div className="native-project-proof-wide">
                  <dt>LD rung IR / 프로그램 적용 증거</dt>
                  <dd>
                    <code>
                      rung {operation.result.buildBundleReceipt.programReadback.rungCount ?? "legacy"}
                      {" · IR "}
                      {operation.result.buildBundleReceipt.programReadback.ladderIrSha256?.slice(0, 12) ?? "legacy"}
                      {" · source "}
                      {operation.result.buildBundleReceipt.programReadback.sourceSha256.slice(0, 12)}
                      {" · saved "}
                      {operation.result.buildBundleReceipt.programReadback.savedProjectSha256.slice(0, 12)}
                    </code>
                    {` · ${operation.result.buildBundleReceipt.programReadback.exactInstructionReadback.length}개 mnemonic 행 exact readback`}
                    <small>
                      rung 순서 {operation.result.buildBundleReceipt.programReadback.rungIds?.join(" → ") ?? "legacy"}
                    </small>
                  </dd>
                </div>
              )}
              <div className="native-project-proof-wide">
                <dt>PLC 영향</dt>
                <dd>
                  {operation.result.controllerConnected === false &&
                  operation.result.plcWritePerformed === false
                    ? "연결 안 함 · 다운로드 안 함 · 쓰기 안 함"
                    : operation.result.controllerConnected === true ||
                        operation.result.plcWritePerformed === true
                      ? "예상 밖의 PLC 영향 보고됨 · 사용 중지"
                      : "PLC 무영향 증거가 아직 확인되지 않음"}
                </dd>
              </div>
              {operation.result.workspaceRegistration && (
                <div className="native-project-proof-wide">
                  <dt>분석 작업공간</dt>
                  <dd>
                    {operation.result.workspaceRegistration.state ===
                    "captured_and_bound"
                      ? isVerifiedCompletion(operation)
                        ? "보호 작업 복사본 캡처 · 독립 새 XG5000 프로세스 재열기 · 현재 분석 세션 연결 완료"
                        : "과거 workspace binding 기록 · 현재 완료 조건 불충족 · 읽기 전용"
                      : operation.result.workspaceRegistration.state ===
                          "captured_not_bound"
                        ? operation.result.reopenVerification?.state === "verified"
                          ? simulationTrial?.state === "OBSERVING"
                            ? "보호 복사본 캡처 · 독립 재열기 확인 · XG-SIM receipt 채택 대기"
                            : simulationTrial?.state === "REVERTED"
                              ? "보호 복사본 캡처 · 독립 재열기 확인 · XG-SIM 결과 미채택"
                              : simulationTrial?.state === "QUARANTINED"
                                ? "보호 복사본 캡처 · 독립 재열기 확인 · XG-SIM 상태 격리"
                                : simulationTrial?.state ===
                                    "QUARANTINE_ACKNOWLEDGED"
                                  ? "보호 복사본 캡처 · 독립 재열기 확인 · XG-SIM 미복구 격리 확인"
                                : "보호 복사본 캡처 · 독립 재열기 확인 · 작업공간 미결속"
                          : "보호 복사본 캡처 완료 · 독립 재열기 미확인"
                        : "보호 복사본 캡처 실패 · 기존 로컬 프로젝트 열기 필요"}
                  </dd>
                </div>
              )}
            </dl>
            )}

          {simulationTrial?.state === "OBSERVING" && (
            <section
              className="native-project-recovery"
              aria-labelledby="native-simulation-trial-title"
            >
              <div>
                <p className="eyebrow">XG-SIM RUNTIME RECEIPT</p>
                <h4 id="native-simulation-trial-title">시뮬레이션 결과 결정</h4>
                <p>
                  {confirmedControlScope}에 해당하는 시나리오를 관찰했습니다.
                  시뮬레이터 CPU는 이미 정지됐고 시험 입력은 원래 값으로 복원됐습니다.
                  {simulationAcceptanceEligible
                    ? " 채택해야만 이 보호 작업 복사본을 현재 엔지니어링 대상으로 연결합니다."
                    : " 현재 receipt는 최신 네트워크/MOV 증거 gate를 통과하지 못해 채택할 수 없으며 읽기 전용으로만 표시합니다."}
                </p>
              </div>
              <div className="native-project-recovery-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={busy}
                  onClick={() => void decideSimulationTrial("revert")}
                >
                  시험 결과 미채택 · 원복 완료로 닫기
                </button>
                <button
                  className="button primary-button"
                  type="button"
                  disabled={busy || !simulationAcceptanceEligible}
                  onClick={() => void decideSimulationTrial("accept")}
                >
                  검증 결과 채택 · 작업공간 연결
                </button>
              </div>
            </section>
          )}

          {simulationTrial?.state === "QUARANTINED" && (
            <section
              className="native-project-recovery"
              aria-labelledby="native-simulation-quarantine-title"
            >
              <div>
                <p className="eyebrow">UNRESOLVED XG-SIM STATE</p>
                <h4 id="native-simulation-quarantine-title">
                  자동 재실행하지 않습니다
                </h4>
                <p>
                  완전한 CPU STOP·입력 원복 receipt를 받지 못했습니다. XG-SIM을
                  직접 정지·종료하고 필요한 현장 상태를 확인하세요. 아래 버튼은
                  복구 완료를 뜻하지 않고, 미복구 경고를 확인했다는 기록만 남깁니다.
                </p>
              </div>
              <div className="native-project-recovery-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void decideSimulationTrial("acknowledge_quarantine")
                  }
                >
                  미복구 격리 경고 확인 · 작업공간 연결 안 함
                </button>
              </div>
            </section>
          )}

          {!terminal && (
            <button
              className="button secondary-button"
              type="button"
              disabled={busy}
              onClick={() => void refreshOperation()}
            >
              생성 상태 다시 확인
            </button>
          )}
          {operation.state === "quarantined" && (
            <section
              className="native-project-recovery"
              aria-labelledby="native-project-recovery-title"
            >
              <div>
                <p className="eyebrow">READ-ONLY RECONCILIATION</p>
                <h4 id="native-project-recovery-title">
                  격리된 생성 결과 확인
                </h4>
                <p>
                  저장된 대상과 파일 경로를 현재 XG5000에서 다시 읽어 생성 여부만
                  판정합니다. 생성 명령은 다시 보내지 않습니다.
                </p>
              </div>

              <label>
                <span>검사할 예상 프로젝트 파일</span>
                <input
                  type="text"
                  readOnly
                  spellCheck={false}
                  value={expectedRecoveryProjectPath(operation)}
                />
              </label>

              <div className="native-project-recovery-target">
                <span>저장된 XG5000 대상</span>
                <strong>
                  PID {operation.request.xg5000Pid ?? "—"} · HWND{" "}
                  {operation.request.xg5000Hwnd ?? "—"}
                </strong>
              </div>

              <label>
                <span>현재 상태를 확인할 XG5000 창</span>
                <select
                  value={recoveryHwnd}
                  disabled={busy || instancesLoading}
                  onChange={(event) => setRecoveryHwnd(event.target.value)}
                >
                  <option value="">
                    정확한 PID/HWND를 직접 선택해 주세요
                  </option>
                  {instances.map((instance) => (
                    <option key={instance.hwnd} value={instance.hwnd}>
                      {displayInstance(instance)}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                <span>선택 창에 현재 열린 다른 프로젝트 파일</span>
                <input
                  type="text"
                  spellCheck={false}
                  value={recoveryObservedProjectPath}
                  disabled={busy}
                  placeholder="ROT 조회가 없을 때 PID의 실제 파일 사용을 확인할 .xgwx 경로"
                  onChange={(event) =>
                    setRecoveryObservedProjectPath(event.target.value)
                  }
                />
              </label>
              <p className="native-project-recovery-warning">
                일반 실행된 XG5000이 Automation 정보를 제공하지 않으면, 현재 창에
                실제로 열린 다른 프로젝트 경로를 입력해 PID의 Windows 파일 사용·제목·
                해시를 함께 확인합니다.
              </p>

              {!instancesLoading && instances.length === 0 && (
                <p className="native-project-recovery-warning">
                  실행 중인 XG5000 창이 없습니다. 원래 작업 창을 연 뒤 목록을 다시
                  확인해 주세요.
                </p>
              )}
              {selectedRecoveryInstance &&
                recoveryTargetIsUniqueIdleReplacement && (
                  <p className="native-project-recovery-warning">
                    저장된 XG5000은 종료됐고 유일한 빈 대체 창이 선택됐습니다. Host가
                    원래 PID/HWND 종료와 보존된 산출물을 읽기 전용으로 확인합니다.
                  </p>
                )}
              {selectedRecoveryInstance && !recoveryTargetCanBeProbed && (
                <p className="native-project-recovery-warning">
                  선택한 PID/HWND가 저장된 생성 대상과 다릅니다. 재검사를 실행하지
                  않습니다.
                </p>
              )}

              <div className="native-project-recovery-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={busy || instancesLoading}
                  onClick={() => void loadInstances()}
                >
                  XG5000 목록 다시 확인
                </button>
                <button
                  className="button primary-button"
                  type="button"
                  disabled={
                    busy ||
                    instancesLoading ||
                    !recoveryTargetCanBeProbed ||
                    !expectedRecoveryProjectPath(operation)
                  }
                  onClick={() => void reconcileOperation()}
                >
                  {busy ? "현재 상태 검사 중…" : "현재 상태 다시 검사"}
                </button>
              </div>

              <section className="native-project-recovery-resume">
                <p className="eyebrow">EXACT LD RESUME</p>
                <p>
                  저장된 PID/HWND와 요청 digest가 일치할 때만 현재 입력된 LD를
                  다시 읽어 Program Check와 저장만 재개합니다. 새 프로젝트 생성이나
                  IL 삽입은 실행하지 않으며 자동 재시도도 하지 않습니다.
                </p>
                {!recoveryTargetMatchesStored && (
                  <p className="native-project-recovery-warning">
                    재개하려면 저장된 원래 PID/HWND를 목록에서 정확히 선택해 주세요.
                    대체 빈 창은 재개 대상이 아닙니다.
                  </p>
                )}
                <button
                  className="button primary-button"
                  type="button"
                  disabled={
                    busy ||
                    instancesLoading ||
                    !recoveryTargetMatchesStored ||
                    !operation.requestDigest
                  }
                  onClick={() => void resumeNativeOperation()}
                >
                  {busy
                    ? "입력된 LD 검사·저장 재개 중…"
                    : "입력된 LD에서 검사·저장 재개"}
                </button>
              </section>
            </section>
          )}

          {completedVerificationContinuation && (
            <section
              className="native-project-recovery"
              aria-labelledby="native-project-completed-verification-title"
            >
              <div>
                <p className="eyebrow">SAVED PROJECT VERIFICATION</p>
                <h4 id="native-project-completed-verification-title">
                  {reuseVerifiedReopen
                    ? "저장된 프로젝트의 미완료 XG-SIM 검증 재개"
                    : "저장된 프로젝트의 독립 재열기 검증 재개"}
                </h4>
                <p>
                  검사·저장은 확인됐지만 엔지니어링 준비가 완료되지 않았습니다.
                  저장된 원본 대상과 request digest는 바꾸지 않습니다.
                  {reuseVerifiedReopen
                    ? " 검증된 재열기 session을 그대로 재사용해 미완료 XG-SIM 검증만 재개합니다."
                    : " 이미 시작한 fresh XG5000 창 하나를 직접 선택해 read-only 재열기와 이어지는 XG-SIM 검증을 재개합니다."}
                  새 프로젝트 생성, IL 삽입, 창 종료, 자동 재시도는 하지 않습니다.
                </p>
              </div>

              <div className="native-project-recovery-target">
                <span>저장된 원본 XG5000 대상</span>
                <strong>
                  PID {operation.request.xg5000Pid ?? "—"} · HWND{" "}
                  {operation.request.xg5000Hwnd ?? "—"}
                </strong>
              </div>

              {!storedResumeSourceIsValid && (
                <p className="native-project-recovery-warning">
                  저장된 원본 PID/HWND 결속이 완전하지 않아 독립 재열기 검증을 재개할
                  수 없습니다.
                </p>
              )}

              {reuseVerifiedReopen ? (
                <p>
                  저장된 검증 재열기 session을 Host가 다시 결속합니다. PID/HWND를
                  바꾸거나 새 창을 선택하지 않습니다.
                </p>
              ) : (
                <>
                  <label>
                    <span>독립 재열기 검증에 사용할 fresh XG5000 창</span>
                    <select
                      value={reopenedHwnd}
                      disabled={busy || instancesLoading}
                      onChange={(event) => setReopenedHwnd(event.target.value)}
                    >
                      <option value="">
                        새로 시작한 XG5000 PID/HWND를 직접 선택해 주세요
                      </option>
                      {instances
                        .filter(
                          (instance) =>
                            instance.pid !== operation.request.xg5000Pid,
                        )
                        .map((instance) => (
                          <option key={instance.hwnd} value={instance.hwnd}>
                            {displayInstance(instance)}
                          </option>
                        ))}
                    </select>
                  </label>

                  {!instancesLoading && instances.length === 0 && (
                    <p className="native-project-recovery-warning">
                      실행 중인 XG5000 창이 없습니다. 검증할 fresh 창을 시작한 뒤
                      목록을 다시 확인해 주세요.
                    </p>
                  )}
                  {selectedReopenedInstance &&
                    !reopenedTargetIsIndependent && (
                      <p className="native-project-recovery-warning">
                        저장된 원본 PID는 독립 재열기 대상으로 사용할 수 없습니다.
                        fresh XG5000 창을 선택해 주세요.
                      </p>
                    )}
                  {instances.length > 0 &&
                    !instances.some(
                      (instance) =>
                        instance.pid !== operation.request.xg5000Pid,
                    ) && (
                      <p className="native-project-recovery-warning">
                        저장된 원본과 다른 fresh XG5000 창이 필요합니다. 이 화면은
                        창을 시작하거나 종료하지 않습니다.
                      </p>
                    )}
                </>
              )}

              <div className="native-project-recovery-actions">
                <button
                  className="button secondary-button"
                  type="button"
                  disabled={busy || instancesLoading}
                  onClick={() => void loadInstances()}
                >
                  XG5000 목록 다시 확인
                </button>
                <button
                  className="button primary-button"
                  type="button"
                  disabled={
                    busy ||
                    !storedResumeSourceIsValid ||
                    (!reuseVerifiedReopen &&
                      (instancesLoading || !reopenedTargetIsIndependent)) ||
                    !operation.requestDigest
                  }
                  onClick={() => void continueCompletedVerification()}
                >
                  {busy
                    ? "저장된 검증 흐름 재개 중…"
                    : reuseVerifiedReopen
                      ? "미완료 XG-SIM 검증 재개"
                      : "독립 재열기·XG-SIM 검증 재개"}
                </button>
              </div>
            </section>
          )}
        </section>
      )}

      {notice && (
        <p
          className="instance-notice"
          role={failure ? "alert" : "status"}
        >
          {notice}
        </p>
      )}
    </section>
  );
}
