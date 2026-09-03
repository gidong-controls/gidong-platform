import { useState } from "react";
import {
  getStructuralMnePlan,
  isLegacyChangePlanV1,
  isMneRungPatchPlan,
  type StructuralMnePlan,
} from "../api/changeContracts";
import type {
  ChangeCoverageArea,
  ChangeCoverageItem,
  ChangeCoverageStatus,
  ChangePlanV1,
  ChangeRiskLevel,
  ChangeTransaction,
  MneFullProgramReplacementChangePlanV2,
  MneFullProgramValidationStep,
  MneInstructionBatchPatchCandidateReceipt,
  MneInstructionBatchPatchChangePlanV5,
  MneInstructionPatchCandidateReceipt,
  MneInstructionPatchChangePlanV4,
  MneProgramReplacementInstruction,
  MneRungCondition,
  MneRungPatchCandidateReceipt,
  MneRungPatchChangePlanV6,
  MneSeriesGuardCandidateReceipt,
  MneSeriesGuardPatchChangePlanV3,
  OfflineNativeValidationStep,
} from "../api/types";
import { Icon } from "./Icon";

type ChangeValidationStep =
  | OfflineNativeValidationStep
  | MneFullProgramValidationStep;

const RISK_LABELS: Record<ChangeRiskLevel, string> = {
  low: "낮음",
  medium: "보통",
  high: "높음",
  critical: "매우 높음",
};

const STATE_LABELS: Record<ChangeTransaction["state"], string> = {
  pending_approval: "승인 대기",
  approved: "승인됨",
  executing: "적용 중",
  verifying: "검증 중",
  observing: "XG-SIM 검증 결과 확인 대기",
  completed: "적용·오프라인 검사 완료",
  rolled_back: "검증 후 원복됨",
  failed: "실패",
  quarantined: "결과 확인 필요",
  rejected: "거절됨",
  expired: "승인 만료",
};

const VALIDATION_STEP_LABELS: Record<ChangeValidationStep, string> = {
  structural_diff: "변경 전후 네이티브 구조 diff 확인",
  xg5000_program_check: "XG5000 프로그램 검사",
  project_save: "오프라인 작업 복사본 저장",
  project_reopen: "독립 XG5000 프로세스에서 프로젝트 재열기와 동일 프로그램 readback",
};

const COVERAGE_AREA_LABELS: Record<ChangeCoverageArea, string> = {
  target_identity: "대상 식별",
  value_semantics: "값·단위 의미",
  direct_impact: "직접 영향",
  indirect_impact: "간접 영향",
  modes: "운전·복구 모드",
  runtime: "현재 런타임",
  external_integrations: "HMI·통신·외부 연계",
  safety: "설비·안전",
  validation: "검증 범위",
  rollback: "복구",
};

const COVERAGE_STATUS_LABELS: Record<ChangeCoverageStatus, string> = {
  confirmed: "증거 확인",
  not_applicable_with_reason: "사유와 함께 제외",
  unknown_blocking: "미확인 · 실행 차단",
};

const REQUIRED_COVERAGE_AREAS = Object.keys(
  COVERAGE_AREA_LABELS,
) as ChangeCoverageArea[];

interface ChangeApprovalPanelProps {
  transaction: ChangeTransaction;
  busy: boolean;
  notice?: string;
  mneExecutionReady?: boolean;
  mneExecutionDetail?: string;
  onApprove: () => void;
  onExecuteApproved: () => void;
  onFinalizeSimulation?: (decision: "accept" | "revert") => void;
  onReject: () => void;
  onRequestRevision?: () => void;
  onOpenTarget?: () => void;
  onOpenEvidence?: () => void;
  onRefresh: () => void;
  onDismiss: () => void;
}

const TERMINAL_CHANGE_STATES = new Set<ChangeTransaction["state"]>([
  "completed",
  "rolled_back",
  "failed",
  "rejected",
  "expired",
]);

function displayValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function ValueTable({
  title,
  values,
  tone,
}: {
  title: string;
  values: Record<string, unknown>;
  tone: "current" | "proposed";
}) {
  const entries = Object.entries(values);
  return (
    <section className={`change-value-card change-value-${tone}`}>
      <h4>{title}</h4>
      {entries.length > 0 ? (
        <dl>
          {entries.map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{displayValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p>표시할 값이 없습니다.</p>
      )}
    </section>
  );
}

function PlanList({
  title,
  items,
  warning = false,
}: {
  title: string;
  items: string[];
  warning?: boolean;
}) {
  return (
    <section className={`change-plan-list ${warning ? "change-warning" : ""}`}>
      <h4>{title}</h4>
      {items.length > 0 ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${index}:${item}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>없음</p>
      )}
    </section>
  );
}

function targetLabel(
  structuralMnePlan: StructuralMnePlan | null,
  legacyPlan: ChangePlanV1 | undefined,
): string {
  if (structuralMnePlan) {
    if (structuralMnePlan.kind === "full_program_replacement") {
      return `${structuralMnePlan.plan.targetProgram.programName} · ${structuralMnePlan.plan.targetProgram.programId}`;
    }
    return `${structuralMnePlan.plan.candidate.programName} / ${structuralMnePlan.plan.candidate.programId}`;
  }
  if (!legacyPlan) {
    return "";
  }
  const target = legacyPlan.target;
  return [
    target.program ?? target.programId ?? target.program_id,
    target.network,
    target.location ?? target.tokenOffset ?? target.token_offset,
    target.device,
  ]
    .map((part) => (typeof part === "number" ? `token ${part}` : part))
    .filter((part): part is string => typeof part === "string" && Boolean(part))
    .join(" · ");
}

function SeriesGuardPatchReview({
  plan,
}: {
  plan: MneSeriesGuardPatchChangePlanV3;
}) {
  const { candidate: receipt } = plan;
  const { patch } = receipt;
  return (
    <section
      className="change-target-evidence change-series-guard-target"
      aria-labelledby="change-series-guard-target-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">COMPILED SERIES-GUARD PATCH</p>
          <h3 id="change-series-guard-target-title">Series guard insertion</h3>
        </div>
      </div>
      <p className="change-series-guard-diff" aria-label="Exact structural diff">
        <code>+ {patch.guardCommand} {patch.guardOperand}</code>
        <span>
          immediately before instruction {patch.outputInstructionIndex + 1}:{" "}
          <code>OUT {patch.outputOperand}</code>
        </span>
      </p>
      <CandidateDigestDetails
        receipt={receipt}
        evidenceId={plan.candidateEvidenceId}
      />
      <p>
        The MCP compiled and the Host bound this candidate. Raw MNE, LD,
        clipboard data, and private payloads are intentionally not displayed.
      </p>
    </section>
  );
}

function InstructionPatchReview({
  plan,
}: {
  plan: MneInstructionPatchChangePlanV4;
}) {
  const { candidate: receipt } = plan;
  const { patch } = receipt;
  const operation = {
    insert_before: "기준 명령 앞에 1행 삽입",
    insert_after: "기준 명령 뒤에 1행 삽입",
    replace: "기준 명령 1행 교체",
    delete: "기준 명령 1행 삭제",
  }[patch.operation];
  const anchorText = instructionText(patch.anchor);
  const newInstructionText = patch.instruction
    ? instructionText(patch.instruction)
    : "";
  const changedInstruction =
    patch.operation === "delete"
      ? "− " + anchorText
      : patch.operation === "replace"
        ? "− " + anchorText + " → + " + newInstructionText
        : "+ " + newInstructionText;
  return (
    <section
      className="change-target-evidence change-series-guard-target"
      aria-labelledby="change-instruction-patch-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">COMPILED SINGLE-INSTRUCTION PATCH</p>
          <h3 id="change-instruction-patch-title">{operation}</h3>
        </div>
      </div>
      <p className="change-series-guard-diff" aria-label="Exact structural diff">
        <span>
          기준 행 {patch.anchorInstructionIndex + 1}:{" "}
          <code>{anchorText}</code>
        </span>
        <code>{changedInstruction}</code>
        <span>
          인접 문맥:{" "}
          {patch.contextBefore ? instructionText(patch.contextBefore) : "START"}
          {" → [anchor] → "}
          {patch.contextAfter ? instructionText(patch.contextAfter) : "END"}
        </span>
      </p>
      <CandidateDigestDetails
        receipt={receipt}
        evidenceId={plan.candidateEvidenceId}
      />
      <p>
        The MCP compiled this one-instruction change from the private baseline.
        Raw MNE, LD, clipboard data, and private payloads are not displayed.
      </p>
    </section>
  );
}

function InstructionBatchPatchReview({
  plan,
}: {
  plan: MneInstructionBatchPatchChangePlanV5;
}) {
  const { candidate: receipt } = plan;
  return (
    <section
      className="change-target-evidence change-series-guard-target"
      aria-labelledby="change-instruction-batch-patch-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">COMPILED INSTRUCTION BATCH</p>
          <h3 id="change-instruction-batch-patch-title">
            관련 명령 {receipt.patches.length}개 변경
          </h3>
        </div>
      </div>
      <ol className="change-full-program-instructions">
        {receipt.patches.map((patch) => {
          const changedInstruction =
            patch.operation === "delete"
              ? `- ${instructionText(patch.anchor)}`
              : patch.operation === "replace"
                ? `- ${instructionText(patch.anchor)} / + ${instructionText(patch.instruction!)}`
                : `+ ${instructionText(patch.instruction!)}`;
          return (
            <li key={`${patch.anchorInstructionIndex}:${patch.operation}`}>
              <code>#{patch.anchorInstructionIndex + 1}</code>
              <strong>{patch.operation}</strong>
              <code>{changedInstruction}</code>
              <span>
                {patch.contextBefore
                  ? instructionText(patch.contextBefore)
                  : "START"}
                {" → [anchor] → "}
                {patch.contextAfter
                  ? instructionText(patch.contextAfter)
                  : "END"}
              </span>
            </li>
          );
        })}
      </ol>
      <CandidateDigestDetails
        receipt={receipt}
        evidenceId={plan.candidateEvidenceId}
      />
      <p>
        모든 행은 같은 비공개 기준본에서 컴파일되며 하나의 승인·검증·롤백
        경계로 처리됩니다. Raw MNE와 LD는 표시하지 않습니다.
      </p>
    </section>
  );
}

function RungConditionTree({ condition }: { condition: MneRungCondition }) {
  if (condition.kind === "contact") {
    return (
      <code>
        {condition.state === "off" ? "NOT " : ""}
        {condition.operand}
      </code>
    );
  }
  return (
    <div>
      <strong>{condition.kind === "all_of" ? "ALL" : "ANY"}</strong>
      <ul>
        {condition.terms.map((term, index) => (
          <li key={`${condition.kind}:${index}`}>
            <RungConditionTree condition={term} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function RungPatchReview({
  plan,
}: {
  plan: MneRungPatchChangePlanV6;
}) {
  const { candidate: receipt } = plan;
  const { patch: rawPatch } = receipt;
  const operationTitle = {
    insert_before: `${instructionText(rawPatch.anchor)} 앞 완전 rung 삽입`,
    replace_condition: `${instructionText(rawPatch.anchor)} 공유 rung 조건 교체`,
    replace_rung: `${instructionText(rawPatch.anchor)} 완전 rung 교체`,
    delete: `${instructionText(rawPatch.anchor)} 포함 완전 rung 삭제`,
  }[rawPatch.operation];
  const operationDetail =
    rawPatch.operation === "insert_before"
      ? `삽입 위치 #${rawPatch.anchor.index + 1} 앞 · ${receipt.targetInstructionCount - receipt.baselineInstructionCount}행 추가`
      : rawPatch.operation === "delete"
        ? `삭제 범위 #${rawPatch.anchorRung!.startInstructionIndex + 1}–#${rawPatch.anchorRung!.startInstructionIndex + rawPatch.anchorRung!.conditionInstructionCount + rawPatch.anchorRung!.actionInstructionCount} · rung 전체`
        : rawPatch.operation === "replace_rung"
          ? `교체 범위 #${rawPatch.anchorRung!.startInstructionIndex + 1}–#${rawPatch.anchorRung!.startInstructionIndex + rawPatch.anchorRung!.conditionInstructionCount + rawPatch.anchorRung!.actionInstructionCount} · 조건과 공유 action 전체`
          : `기존 조건 #${rawPatch.anchorRung!.startInstructionIndex + 1}–#${rawPatch.anchorRung!.startInstructionIndex + rawPatch.anchorRung!.conditionInstructionCount} · 공유 action ${rawPatch.anchorRung!.actionInstructionCount}개 유지`;
  return (
    <section
      className="change-target-evidence change-series-guard-target"
      aria-labelledby="change-rung-patch-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">COMPILED COMPLETE RUNG PATCH</p>
          <h3 id="change-rung-patch-title">{operationTitle}</h3>
        </div>
      </div>
      <p>{operationDetail}</p>
      <div className="change-values">
        <section className="change-plan-list">
          <h4>{rawPatch.operation === "delete" ? "삭제할 rung" : "현재 위치"}</h4>
          {rawPatch.anchorRung ? (
            <>
              <RungConditionTree condition={rawPatch.anchorRung.condition} />
              <ol>
                {rawPatch.anchorRung.actions.map((action) => (
                  <li key={action.instructionDigest}>
                    <code>{instructionText(action)}</code>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p>없음 — 최종 END 앞에 삽입</p>
          )}
        </section>
        <span className="change-arrow" aria-hidden="true">
          <Icon name="arrow" size={20} />
        </span>
        <section className="change-plan-list">
          <h4>
            {rawPatch.operation === "insert_before"
              ? "삽입할 rung"
              : rawPatch.operation === "delete"
                ? "삭제 후"
                : "교체 후 rung"}
          </h4>
          {rawPatch.targetRung ? (
            <>
              <RungConditionTree condition={rawPatch.targetRung.condition} />
              <ol>
                {rawPatch.targetRung.actions.map((action, index) => (
                  <li key={`${action.command}:${action.operand ?? ""}:${index}`}>
                    <code>{instructionText(action)}</code>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p>없음 — rung 전체 삭제</p>
          )}
        </section>
      </div>
      <p>
        Baseline rung digest: <code>{rawPatch.anchorRung?.rungDigest ?? "none"}</code>
      </p>
      <CandidateDigestDetails
        receipt={receipt}
        evidenceId={plan.candidateEvidenceId}
      />
      {plan.verificationScope === "offline_native_reopen_xgsim" && receipt.simulationProfile && (
        <section className="change-verification-boundary">
          <h4>저장 후 XG-SIM 검증 프로필</h4>
          <p>아래 입력과 예상 관찰값을 XG-SIM에서 순서대로 확인합니다. 물리 PLC에는 연결하거나 쓰지 않습니다.</p>
          {"scenario" in receipt.simulationProfile ? (
            <ol className="change-plan-list">
              {receipt.simulationProfile.scenario.steps.map((step, index) => (
                <li key={`${index}:${step.name}`}>
                  <strong>{step.name}</strong>
                  <dl>
                    <div><dt>입력</dt><dd><code>{Object.entries(step.drive).map(([key, value]) => `${key}=${value}`).join(", ")}</code></dd></div>
                    <div><dt>예상 관찰값</dt><dd><code>{Object.entries(step.observe).map(([key, value]) => `${key}=${value}`).join(", ")}</code></dd></div>
                  </dl>
                </li>
              ))}
            </ol>
          ) : (
            <dl>
              <div><dt>정상 입력</dt><dd><code>{Object.entries(receipt.simulationProfile.normalAssignments).map(([key, value]) => `${key}=${value}`).join(", ")}</code></dd></div>
              <div><dt>차단 입력</dt><dd><code>{Object.entries(receipt.simulationProfile.denyAssignments).map(([key, value]) => `${key}=${value}`).join(", ")}</code></dd></div>
            </dl>
          )}
          <dl>
            <div><dt>관찰 OUT</dt><dd><code>{receipt.simulationProfile.outputAddresses.join(", ")}</code></dd></div>
            <div><dt>프로필 digest</dt><dd><code>{receipt.simulationProfile.profileDigest}</code></dd></div>
          </dl>
        </section>
      )}
      <p>
        조건 AST 전체와 action, baseline slice가 후보 digest에 묶입니다. Raw MNE와
        LD는 MCP 내부 기준본에만 남습니다.
      </p>
    </section>
  );
}

function FullProgramTargetReview({
  plan,
}: {
  plan: MneFullProgramReplacementChangePlanV2;
}) {
  return (
    <section
      className="change-target-evidence change-full-program-target"
      aria-labelledby="change-full-program-target-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">TYPED FULL-PROGRAM TARGET</p>
          <h3 id="change-full-program-target-title">전체 프로그램 교체 설계</h3>
        </div>
      </div>
      <dl>
        <div>
          <dt>프로그램 이름</dt>
          <dd>{plan.targetProgram.programName}</dd>
        </div>
        <div>
          <dt>프로그램 ID</dt>
          <dd>
            <code>{plan.targetProgram.programId}</code>
          </dd>
        </div>
        <div>
          <dt>명령 수</dt>
          <dd>{plan.targetProgram.instructionCount.toLocaleString("ko-KR")}</dd>
        </div>
        <div>
          <dt>설계 digest</dt>
          <dd>
            <code>{plan.targetProgram.designDigest}</code>
          </dd>
        </div>
        <div>
          <dt>대상 프로그램 digest</dt>
          <dd>
            <code>{plan.targetProgram.targetProgramDigest}</code>
          </dd>
        </div>
        <div>
          <dt>승인된 프로젝트 기준</dt>
          <dd>
            <code>{plan.baselineBinding.projectSha256.slice(0, 12)}…</code>
          </dd>
        </div>
        <div>
          <dt>승인된 LD 원본 기준</dt>
          <dd>
            <code>{plan.baselineBinding.ldPayloadSha256.slice(0, 12)}…</code>
          </dd>
        </div>
        {plan.baselineBinding.ldCanonicalSha256 && (
          <div>
            <dt>승인된 LD 의미 기준</dt>
            <dd><code>{plan.baselineBinding.ldCanonicalSha256.slice(0, 12)}…</code></dd>
          </div>
        )}
      </dl>
      <details className="change-full-program-instructions" open>
        <summary>
          순서가 고정된 전체 typed instruction ·{" "}
          {plan.targetProgram.instructionCount.toLocaleString("ko-KR")}개
        </summary>
        <ol>
          {plan.targetProgram.instructions.map((instruction, index) => (
            <li key={index} value={index + 1}>
              <code>{instruction.command}</code>
              {instruction.operand !== null && <span>{instruction.operand}</span>}
            </li>
          ))}
        </ol>
      </details>
      <p>
        화면에는 Host가 검증한 typed 명령과 digest만 표시합니다. 원본 MNE/LD,
        클립보드 자료와 로컬 경로는 표시하지 않습니다.
      </p>
    </section>
  );
}

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function isObservingMneSimulationReceipt(
  receipt: Record<string, unknown> | undefined,
): boolean {
  const stop = objectValue(receipt?.stop);
  const restore = receipt?.restore;
  const observation = objectValue(receipt?.observation);
  const phases = observation?.observations;
  return Boolean(
    receipt?.schema === "controlstudio-mne-rung-xgsim-receipt/v1" &&
      receipt.controller_connected === false &&
      receipt.plc_write_performed === false &&
      stop?.ok === true &&
      stop.verified === true &&
      Array.isArray(restore) && restore.length > 0 &&
      restore.every((item) => objectValue(item)?.matched === true) &&
      Array.isArray(phases) && phases.length === 3 &&
      phases.every((item, index) => {
        const phase = objectValue(item);
        return phase?.phase === ["normal", "deny", "restore"][index] &&
          phase.expected === [1, 0, 1][index] && phase.matched === true;
      }),
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function digestLabel(value: unknown): string {
  return typeof value === "string" && value.length >= 16
    ? `${value.slice(0, 16)}…`
    : "—";
}

function semanticsLabel(value: unknown): string {
  const semantics = objectValue(value);
  if (!semantics) {
    return "의미 receipt 없음";
  }
  if (semantics.kind === "timer_instruction") {
    const duration =
      typeof semantics.duration_ms === "number"
        ? `${semantics.duration_ms} ms`
        : "시간 미확정";
    return [
      semantics.instruction,
      semantics.timer_device,
      `preset ${semantics.preset_value ?? "—"}`,
      `base ${semantics.time_base_ms ?? "—"} ms`,
      duration,
    ]
      .filter((item): item is string => typeof item === "string")
      .join(" · ");
  }
  return [semantics.kind, semantics.device, semantics.action, semantics.contact]
    .filter((item): item is string => typeof item === "string")
    .join(" · ");
}

function instructionText(
  instruction: Pick<MneProgramReplacementInstruction, "command" | "operand">,
): string {
  return instruction.operand
    ? instruction.command + " " + instruction.operand
    : instruction.command;
}

function CandidateDigestDetails({
  receipt,
  evidenceId,
}: {
  receipt:
    | MneSeriesGuardCandidateReceipt
    | MneInstructionPatchCandidateReceipt
    | MneInstructionBatchPatchCandidateReceipt
    | MneRungPatchCandidateReceipt;
  evidenceId: string;
}) {
  return (
    <dl>
      <div><dt>Program</dt><dd>{receipt.programName}</dd></div>
      <div><dt>Program ID</dt><dd><code>{receipt.programId}</code></dd></div>
      <div><dt>Project digest</dt><dd><code>{receipt.projectSha256}</code></dd></div>
      <div><dt>Instruction count</dt><dd>{receipt.baselineInstructionCount} → {receipt.targetInstructionCount}</dd></div>
      <div><dt>Baseline MNE digest</dt><dd><code>{receipt.baselineMneSha256}</code></dd></div>
      <div><dt>Baseline LD digest</dt><dd><code>{receipt.baselineLdSha256}</code></dd></div>
      {receipt.baselineLdCanonicalSha256 && <div><dt>Baseline LD canonical digest</dt><dd><code>{receipt.baselineLdCanonicalSha256}</code></dd></div>}
      <div><dt>Target MNE digest</dt><dd><code>{receipt.targetMneSha256}</code></dd></div>
      <div><dt>Target program digest</dt><dd><code>{receipt.targetProgramDigest}</code></dd></div>
      <div><dt>Design digest</dt><dd><code>{receipt.designDigest}</code></dd></div>
      <div><dt>Candidate digest</dt><dd><code>{receipt.candidateDigest}</code></dd></div>
      <div><dt>Candidate evidence ID</dt><dd><code>{evidenceId}</code></dd></div>
    </dl>
  );
}

function observedItems(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value
        .map((item) => objectValue(item))
        .filter((item): item is Record<string, unknown> => Boolean(item))
    : [];
}

function timerAreaLabel(value: Record<string, unknown>): string {
  return `${displayValue(value.device_start)}–${displayValue(value.device_end)} · ${displayValue(value.time_base_ms)} ms · ${displayValue(value.status)}`;
}

function ioModuleLabel(value: Record<string, unknown>): string {
  const location = [value.base, value.slot]
    .filter((item) => item !== null && item !== undefined)
    .map(displayValue)
    .join("/");
  const bindings = observedItems(value.device_bindings)
    .map(
      (binding) =>
        `${displayValue(binding.direction)} ${displayValue(binding.address_start ?? binding.device_start)}–${displayValue(binding.address_end ?? binding.device_end)}`,
    )
    .join(" / ");
  return [location, value.model, value.module_type, bindings, value.status]
    .filter((item) => item !== null && item !== undefined && item !== "")
    .map(displayValue)
    .join(" · ");
}

function ioChannelLabel(value: Record<string, unknown>): string {
  return [
    value.kind,
    value.device_prefix ?? value.device_type,
    value.offset,
    value.status,
  ]
    .filter((item) => item !== null && item !== undefined && item !== "")
    .map(displayValue)
    .join(" · ");
}

function TargetEvidenceReview({ plan }: { plan: ChangePlanV1 }) {
  const target = plan.target;
  const primaryDevices = stringList(
    target.primaryDevices ?? target.primary_devices,
  );
  const physicalOutputs = stringList(
    target.physicalOutputCandidates ?? target.physical_output_candidates,
  );
  const multipleWritersValue =
    target.multipleWriterDevices ?? target.multiple_writer_devices;
  const multipleWriters: unknown[] = Array.isArray(multipleWritersValue)
    ? multipleWritersValue
    : [];
  const hardwareContext = objectValue(
    target.hardwareContext ?? target.hardware_context,
  );
  const cpu = objectValue(hardwareContext?.cpu);
  const timerAreas = observedItems(
    hardwareContext?.timerAreas ?? hardwareContext?.timer_areas,
  );
  const ioModules = observedItems(
    hardwareContext?.ioModules ?? hardwareContext?.io_modules,
  );
  const ioMapping = objectValue(
    hardwareContext?.ioMapping ?? hardwareContext?.io_mapping,
  );
  const ioChannels = observedItems(ioMapping?.channels);
  const externalWiringObserved =
    hardwareContext?.externalWiringObserved ??
    hardwareContext?.external_wiring_observed;
  const hardwareBlockers = stringList(hardwareContext?.blockers);
  const hardwareBinding = objectValue(
    target.hardwareContextBinding ?? target.hardware_context_binding,
  );
  const requiredHardwareContext = stringList(
    hardwareBinding?.required_context,
  );
  const targetHardwareBlockers = stringList(
    hardwareBinding?.blocking_reasons,
  );
  return (
    <section
      className="change-target-evidence"
      aria-labelledby="change-target-evidence-title"
    >
      <div className="change-verification-heading">
        <Icon name="evidence" size={18} />
        <div>
          <p className="panel-kicker">NATIVE TARGET RECEIPT</p>
          <h3 id="change-target-evidence-title">네이티브 변경 대상 근거</h3>
        </div>
      </div>
      <dl>
        <div>
          <dt>위치</dt>
          <dd>
            network {displayValue(target.networkIndex ?? target.network_index)} ·
            rung {displayValue(target.rungId ?? target.rung_id)} · row{" "}
            {displayValue(target.visualRow ?? target.visual_row)} · column{" "}
            {displayValue(target.column)}
          </dd>
        </div>
        <div>
          <dt>현재 의미</dt>
          <dd>{semanticsLabel(plan.current.semantics)}</dd>
        </div>
        <div>
          <dt>제안 의미</dt>
          <dd>{semanticsLabel(plan.proposed.semantics)}</dd>
        </div>
        <div>
          <dt>분석 대상 디바이스</dt>
          <dd>{primaryDevices.join(", ") || "없음"}</dd>
        </div>
        <div>
          <dt>물리 출력 후보</dt>
          <dd>{physicalOutputs.join(", ") || "정적 경로에서 발견되지 않음"}</dd>
        </div>
        <div>
          <dt>다중 쓰기</dt>
          <dd>{multipleWriters.length}개 디바이스</dd>
        </div>
        <div>
          <dt>CPU</dt>
          <dd>
            {[cpu?.model, cpu?.series, cpu?.status]
              .filter(
                (item) =>
                  item !== null && item !== undefined && item !== "",
              )
              .map(displayValue)
              .join(" · ") || "관측되지 않음"}
          </dd>
        </div>
        <div>
          <dt>타이머 영역</dt>
          <dd>
            {timerAreas.map(timerAreaLabel).join(", ") || "관측되지 않음"}
          </dd>
        </div>
        <div>
          <dt>I/O 모듈</dt>
          <dd>{ioModules.map(ioModuleLabel).join(", ") || "관측되지 않음"}</dd>
        </div>
        <div>
          <dt>I/O 주소 기준</dt>
          <dd>{ioChannels.map(ioChannelLabel).join(", ") || "관측되지 않음"}</dd>
        </div>
        <div>
          <dt>외부 배선</dt>
          <dd>{externalWiringObserved === true ? "관측됨" : "관측되지 않음"}</dd>
        </div>
        <div>
          <dt>하드웨어 차단 사유</dt>
          <dd>{hardwareBlockers.join(", ") || "없음"}</dd>
        </div>
        <div>
          <dt>이번 변경의 필수 하드웨어 근거</dt>
          <dd>{requiredHardwareContext.join(", ") || "결속되지 않음"}</dd>
        </div>
        <div>
          <dt>변경 대상 하드웨어 차단</dt>
          <dd>{targetHardwareBlockers.join(", ") || "없음"}</dd>
        </div>
        <div>
          <dt>Network digest</dt>
          <dd>
            <code>{digestLabel(target.networkDigest ?? target.network_digest)}</code>
          </dd>
        </div>
        <div>
          <dt>Analysis receipt</dt>
          <dd>
            <code>
              {digestLabel(
                target.analysisReceiptDigest ?? target.analysis_receipt_digest,
              )}
            </code>
          </dd>
        </div>
        <div>
          <dt>Hardware context</dt>
          <dd>
            <code>
              {digestLabel(
                target.hardwareContextDigest ??
                  target.hardware_context_digest,
              )}
            </code>
          </dd>
        </div>
        <div>
          <dt>Hardware receipt</dt>
          <dd>
            <code>
              {digestLabel(
                target.hardwareReceiptDigest ??
                  target.hardware_receipt_digest,
              )}
            </code>
          </dd>
        </div>
      </dl>
      <p>
        이 근거는 현재 오프라인 working copy의 정적 LD 구조만 결속합니다. 물리
        위험, 외부 배선과 실제 운전 상태는 온라인 사용 전에 별도 현장 근거가
        필요합니다.
      </p>
    </section>
  );
}

const MNE_MUST_BE_CONFIRMED = new Set<ChangeCoverageArea>([
  "target_identity",
  "value_semantics",
  "direct_impact",
  "indirect_impact",
  "safety",
  "validation",
  "rollback",
]);

function hasExecutableMneContract(
  plan:
    | MneFullProgramReplacementChangePlanV2
    | MneSeriesGuardPatchChangePlanV3
    | MneInstructionPatchChangePlanV4
    | MneInstructionBatchPatchChangePlanV5
    | MneRungPatchChangePlanV6,
): boolean {
  const coverageByArea = new Map(
    plan.coverage.map((item) => [item.area, item]),
  );
  return (
    (plan.verificationScope === "offline_native_reopen" ||
      (isMneRungPatchPlan(plan) &&
        plan.verificationScope === "offline_native_reopen_xgsim" &&
        plan.candidate.simulationProfile !== null)) &&
    plan.validation.join("\u0000") ===
      [
        "structural_diff",
        "xg5000_program_check",
        "project_save",
        "project_reopen",
      ].join("\u0000") &&
    plan.blockingUnknowns.length === 0 &&
    coverageByArea.size === REQUIRED_COVERAGE_AREAS.length &&
    REQUIRED_COVERAGE_AREAS.every((area) => {
      const item = coverageByArea.get(area);
      return Boolean(
        item &&
          item.status !== "unknown_blocking" &&
          (!MNE_MUST_BE_CONFIRMED.has(area) || item.status === "confirmed") &&
          (item.status !== "confirmed" || item.evidenceRefs.length > 0),
      );
    })
  );
}

function hasExecutableChangeContract(
  structuralMnePlan: StructuralMnePlan | null,
  legacyPlan: ChangePlanV1 | undefined,
): boolean {
  if (structuralMnePlan) {
    return hasExecutableMneContract(structuralMnePlan.plan);
  }
  if (!legacyPlan) {
    return false;
  }
  const plan = legacyPlan;
  const coverage = plan.coverage ?? [];
  const coverageByArea = new Map(coverage.map((item) => [item.area, item]));
  const requiredLists = [
    plan.impact,
    plan.risks,
    plan.assumptions ?? [],
    plan.alternatives ?? [],
    plan.acceptanceCriteria ?? [],
    plan.observationSignals ?? [],
    plan.stopConditions ?? [],
    plan.manualFollowUp,
    plan.rollback,
  ];
  const currentValue = plan.current.value ?? plan.current.operand;
  const proposedValue = plan.proposed.value ?? plan.proposed.operand;
  const currentSemantics = objectValue(plan.current.semantics);
  const proposedSemantics = objectValue(plan.proposed.semantics);
  const target = plan.target;
  const hardwareContext = objectValue(
    target.hardwareContext ?? target.hardware_context,
  );
  const hardwareCpu = objectValue(hardwareContext?.cpu);
  const hardwareTimerAreasValue =
    hardwareContext?.timerAreas ?? hardwareContext?.timer_areas;
  const hardwareTimerAreas = observedItems(hardwareTimerAreasValue);
  const hardwareIoModulesValue =
    hardwareContext?.ioModules ?? hardwareContext?.io_modules;
  const hardwareIoMapping = objectValue(
    hardwareContext?.ioMapping ?? hardwareContext?.io_mapping,
  );
  const externalWiringObserved =
    hardwareContext?.externalWiringObserved ??
    hardwareContext?.external_wiring_observed;
  const hardwareBlockers = stringList(hardwareContext?.blockers);
  const hardwareBinding = objectValue(
    target.hardwareContextBinding ?? target.hardware_context_binding,
  );
  const requiredHardwareContext = stringList(
    hardwareBinding?.required_context,
  );
  const hardwareStatus: Record<string, unknown> = {
    cpu: hardwareCpu?.status,
    timer_area:
      hardwareTimerAreas.length > 0 &&
      hardwareTimerAreas.every((item) => item.status === "observed")
        ? "observed"
        : "unobserved",
    io_mapping: hardwareIoMapping?.status,
    external_wiring:
      externalWiringObserved === true ? "observed" : "unobserved",
  };
  const allowedHardwareContext = new Set(Object.keys(hardwareStatus));
  const hardwareBlockerContext: Record<string, string> = {
    active_configuration_unresolved: "cpu",
    cpu_identity_conflict: "cpu",
    cpu_model_unobserved: "cpu",
    timer_area_parameter_unobserved: "timer_area",
    timer_area_ranges_overlap: "timer_area",
    io_parameter_unobserved: "io_mapping",
    io_device_offset_parameter_unobserved: "io_mapping",
  };
  const relevantHardwareBlockers = hardwareBlockers.filter((blocker) => {
    const contextName = hardwareBlockerContext[blocker];
    return (
      contextName === undefined || requiredHardwareContext.includes(contextName)
    );
  });
  const requiredHardwareObserved =
    requiredHardwareContext.length > 0 &&
    requiredHardwareContext.every(
      (name) =>
        allowedHardwareContext.has(name) &&
        hardwareStatus[name] === "observed" &&
        hardwareBinding?.[`${name}_status`] === hardwareStatus[name],
    );
  // An offline candidate is explicitly limited to the bound working copy and
  // native reopen verification.  Its retained hardware evidence must still be
  // complete and digest-bound below, but signed runtime blockers are displayed
  // rather than treated as permission to reject the offline-only contract.
  // A permanent offline revision retains the stricter resolved-hardware gate.
  const requiresResolvedHardware =
    plan.requestScope === "permanent_offline_revision";
  const hardwareReadyForScope =
    !requiresResolvedHardware ||
    (stringList(hardwareBinding?.blocking_reasons).length === 0 &&
      requiredHardwareObserved &&
      relevantHardwareBlockers.length === 0);
  return (
    plan.changeMode === "offline_working_copy_revision" &&
    (plan.requestScope === "offline_candidate_revision" ||
      plan.requestScope === "permanent_offline_revision") &&
    (plan.blockingUnknowns?.length ?? 0) === 0 &&
    requiredLists.every(
      (items) =>
        items.length > 0 &&
        items.every((item) => typeof item === "string" && item.trim()),
    ) &&
    REQUIRED_COVERAGE_AREAS.every((area) => {
      const item = coverageByArea.get(area);
      return Boolean(
        item &&
          item.status !== "unknown_blocking" &&
          (item.status !== "confirmed" || item.evidenceRefs.length > 0),
      );
    }) &&
    coverageByArea.size === REQUIRED_COVERAGE_AREAS.length &&
    typeof currentValue === "string" &&
    typeof proposedValue === "string" &&
    typeof (target.analysisEvidenceRef ?? target.analysis_evidence_ref) ===
      "string" &&
    typeof (target.analysisDigest ?? target.analysis_digest) === "string" &&
    typeof (
      target.analysisReceiptDigest ?? target.analysis_receipt_digest
    ) === "string" &&
    typeof (target.projectBindingDigest ?? target.project_binding_digest) ===
      "string" &&
    typeof (target.networkId ?? target.network_id) === "string" &&
    typeof (target.networkDigest ?? target.network_digest) === "string" &&
    typeof (target.rungId ?? target.rung_id) === "string" &&
    typeof (target.hardwareEvidenceRef ?? target.hardware_evidence_ref) ===
      "string" &&
    typeof (
      target.hardwareContextDigest ?? target.hardware_context_digest
    ) === "string" &&
    typeof (
      target.hardwareReceiptDigest ?? target.hardware_receipt_digest
    ) === "string" &&
    typeof (
      target.hardwareProjectBindingDigest ??
      target.hardware_project_binding_digest
    ) === "string" &&
    hardwareBinding?.schema === "xg5000.project.hardware-context/1.0" &&
    hardwareBinding.context_digest ===
      (target.hardwareContextDigest ?? target.hardware_context_digest) &&
    Array.isArray(hardwareBinding.blocking_reasons) &&
    hardwareContext !== undefined &&
    hardwareCpu !== undefined &&
    Array.isArray(hardwareTimerAreasValue) &&
    Array.isArray(hardwareIoModulesValue) &&
    hardwareIoMapping !== undefined &&
    typeof externalWiringObserved === "boolean" &&
    Array.isArray(hardwareContext.blockers) &&
    hardwareReadyForScope &&
    typeof (
      hardwareContext.offlineCandidateReady ??
      hardwareContext.offline_candidate_ready
    ) === "boolean" &&
    currentSemantics?.status === "confirmed" &&
    proposedSemantics?.status === "confirmed" &&
    new TextEncoder().encode(currentValue).length > 0 &&
    currentValue.length === proposedValue.length
  );
}

function CoverageReview({
  items,
  onOpenEvidence,
}: {
  items: ChangeCoverageItem[];
  onOpenEvidence?: () => void;
}) {
  if (items.length === 0) {
    return (
      <section className="change-coverage change-coverage--legacy">
        <h3>영향 조사 완전성</h3>
        <p>
          이 기록은 구조화된 영향 조사 계약 이전에 만들어졌습니다. Host가
          현재 완전성 gate를 다시 통과시키기 전에는 실행할 수 없습니다.
        </p>
      </section>
    );
  }
  return (
    <section
      className="change-coverage"
      aria-labelledby="change-coverage-title"
    >
      <div className="change-coverage__heading">
        <div>
          <p className="panel-kicker">IMPACT COVERAGE</p>
          <h3 id="change-coverage-title">영향 조사 완전성</h3>
        </div>
        <strong>{items.length}/10 영역</strong>
      </div>
      <ul>
        {items.map((item) => (
          <li
            key={item.area}
            className={`change-coverage__item change-coverage__item--${item.status}`}
          >
            <div>
              <strong>{COVERAGE_AREA_LABELS[item.area]}</strong>
              <span>{COVERAGE_STATUS_LABELS[item.status]}</span>
            </div>
            <p>{item.summary}</p>
            {item.evidenceRefs.length > 0 && (
              <button
                type="button"
                className="change-coverage__evidence"
                disabled={!onOpenEvidence}
                onClick={onOpenEvidence}
              >
                연결된 근거 {item.evidenceRefs.length}건 보기
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function hasObjectField(value: unknown, field: string): boolean {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      field in value,
  );
}

interface MneReceiptBundle {
  application?: Record<string, unknown>;
  verification?: Record<string, unknown>;
  rollback?: Record<string, unknown>;
  reopen?: Record<string, unknown>;
}

function mneReceiptBundle(transaction: ChangeTransaction): MneReceiptBundle {
  const applyEvidence = objectValue(transaction.result.apply);
  const verificationEvidence = objectValue(transaction.result.verification);
  const reconciliation = objectValue(transaction.result.reconciliation);
  const verification =
    objectValue(verificationEvidence?.verification) ??
    objectValue(reconciliation?.verification);
  const rollback =
    objectValue(verificationEvidence?.rollback) ??
    objectValue(reconciliation?.rollback);
  return {
    application: objectValue(applyEvidence?.application),
    verification,
    rollback,
    reopen: objectValue((rollback ?? verification)?.reopen),
  };
}

function safeDigest(value: unknown): string | undefined {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value)
    ? value
    : undefined;
}

function ReceiptBoolean({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  if (typeof value !== "boolean") {
    return null;
  }
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value ? "검증됨" : "검증되지 않음"}</dd>
    </div>
  );
}

function ReceiptDigest({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  const digest = safeDigest(value);
  if (!digest) {
    return null;
  }
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <code>{digest}</code>
      </dd>
    </div>
  );
}

function MneReceiptReview({ transaction }: { transaction: ChangeTransaction }) {
  const { application, verification, rollback, reopen } =
    mneReceiptBundle(transaction);
  if (!application && !verification && !rollback) {
    return null;
  }
  const programId = [rollback, verification, reopen, application]
    .map((receipt) => receipt?.program_id)
    .find((value): value is string => typeof value === "string");
  const programName = [rollback, verification, reopen, application]
    .map((receipt) => receipt?.program_name)
    .find((value): value is string => typeof value === "string");
  return (
    <section
      className="change-mne-receipts"
      aria-labelledby="change-mne-receipts-title"
    >
      <h4 id="change-mne-receipts-title">전체 프로그램 검증 receipt</h4>
      {(programId || programName) && (
        <p>
          {programName ?? "이름 미확인"} · <code>{programId ?? "ID 미확인"}</code>
        </p>
      )}
      <dl>
        {application && (
          <>
            <ReceiptBoolean
              label="편집기 exact readback"
              value={application.verified_in_editor}
            />
            <ReceiptDigest
              label="Application receipt digest"
              value={application.application_digest}
            />
          </>
        )}
        {verification && (
          <>
            <ReceiptBoolean label="최종 검증" value={verification.verified} />
            <ReceiptBoolean
              label="XG5000 Program Check"
              value={verification.program_check_verified}
            />
            <ReceiptBoolean
              label="프로젝트 저장"
              value={verification.save_verified}
            />
            <ReceiptDigest
              label="Verification receipt digest"
              value={verification.verification_digest}
            />
          </>
        )}
        {rollback && (
          <>
            <ReceiptBoolean label="Rollback 검증" value={rollback.verified} />
            <ReceiptBoolean
              label="Raw LD exact equality"
              value={rollback.ld_raw_equal}
            />
            <ReceiptBoolean
              label="Canonical LD equality"
              value={rollback.ld_canonical_equal}
            />
            <ReceiptBoolean
              label="Rollback Program Check"
              value={rollback.program_check_verified}
            />
            <ReceiptBoolean
              label="Rollback 저장"
              value={rollback.save_verified}
            />
            <ReceiptDigest
              label="Rollback receipt digest"
              value={rollback.rollback_digest}
            />
          </>
        )}
        {reopen && (
          <>
            <ReceiptBoolean label="독립 재열기" value={reopen.verified} />
            <ReceiptBoolean
              label="별도 XG5000 프로세스"
              value={reopen.process_is_independent}
            />
            <ReceiptBoolean
              label="Stable readback"
              value={reopen.stable_readback}
            />
            <ReceiptBoolean
              label="재열기 LD raw equality"
              value={reopen.ld_raw_equal}
            />
            <ReceiptDigest
              label="재열기 프로젝트 digest"
              value={reopen.project_sha256}
            />
          </>
        )}
      </dl>
      <p>
        이 화면은 검증 상태와 digest만 표시하며 receipt의 경로, raw MNE/LD 및
        비공개 payload는 표시하지 않습니다.
      </p>
    </section>
  );
}

function executionReceiptChecks(
  transaction: ChangeTransaction,
  structuralMnePlan: StructuralMnePlan | null,
) {
  if (structuralMnePlan) {
    const { application, verification, rollback, reopen } =
      mneReceiptBundle(transaction);
    const finalReceipt = rollback ?? verification;
    return [
      {
        label:
          structuralMnePlan.kind === "series_guard_patch"
            ? "Series-guard structural readback"
            : structuralMnePlan.kind === "rung_patch"
              ? "Complete-rung structural readback"
              : structuralMnePlan.kind === "instruction_batch_patch"
                ? "Instruction-batch structural readback"
                : structuralMnePlan.kind === "instruction_patch"
                  ? "Single-instruction structural readback"
                  : "Typed full-program structural readback",
        passed:
          finalReceipt?.verified === true &&
          (rollback
            ? rollback.ld_raw_equal === true
            : application?.verified_in_editor === true),
      },
      {
        label: "XG5000 Program Check",
        passed: finalReceipt?.program_check_verified === true,
      },
      {
        label: "오프라인 작업 복사본 저장",
        passed: finalReceipt?.save_verified === true,
      },
      {
        label: "독립 프로세스 재열기와 stable readback",
        passed:
          reopen?.verified === true &&
          reopen.process_is_independent === true &&
          reopen.stable_readback === true &&
          reopen.ld_raw_equal === true,
      },
    ];
  }
  const result = transaction.result;
  const verification =
    result.verification &&
    typeof result.verification === "object" &&
    !Array.isArray(result.verification)
      ? result.verification
      : {};
  return [
    {
      label: "승인된 피연산자 적용 증거",
      passed:
        hasObjectField(result, "apply") ||
        result.structuralDiffVerified === true,
    },
    {
      label: "네이티브 구조 diff",
      passed:
        hasObjectField(verification, "diff") ||
        result.structuralDiffVerified === true,
    },
    {
      label: "XG5000 프로그램 검사",
      passed:
        hasObjectField(verification, "validation") ||
        result.programCheckPassed === true,
    },
    {
      label: "오프라인 작업 복사본 저장",
      passed:
        hasObjectField(verification, "save") ||
        result.projectSaved === true,
    },
    {
      label: "새 XG5000 프로세스 재열기와 LD P0021 readback",
      passed: hasObjectField(verification, "fresh_reopen") &&
        hasObjectField(verification, "fresh_reopen_attach"),
    },
  ];
}

export function ChangeApprovalPanel({
  transaction,
  busy,
  notice,
  mneExecutionReady = true,
  mneExecutionDetail,
  onApprove,
  onExecuteApproved,
  onFinalizeSimulation,
  onReject,
  onRequestRevision,
  onOpenTarget,
  onOpenEvidence,
  onRefresh,
  onDismiss,
}: ChangeApprovalPanelProps) {
  const [receiptBoundaryAcknowledged, setReceiptBoundaryAcknowledged] =
    useState(false);
  const structuralMnePlan = getStructuralMnePlan(transaction.plan);
  const mnePlan = structuralMnePlan?.kind === "full_program_replacement"
    ? structuralMnePlan.plan
    : undefined;
  const seriesGuardPlan = structuralMnePlan?.kind === "series_guard_patch"
    ? structuralMnePlan.plan
    : undefined;
  const instructionPatchPlan = structuralMnePlan?.kind === "instruction_patch"
    ? structuralMnePlan.plan
    : undefined;
  const instructionBatchPatchPlan =
    structuralMnePlan?.kind === "instruction_batch_patch"
      ? structuralMnePlan.plan
    : undefined;
  const rungPatchPlan = structuralMnePlan?.kind === "rung_patch"
    ? structuralMnePlan.plan
    : undefined;
  const legacyPlan = !structuralMnePlan && isLegacyChangePlanV1(transaction.plan)
    ? transaction.plan
    : undefined;
  if (!structuralMnePlan && !legacyPlan) {
    return (
      <section
        className="change-approval"
        aria-labelledby="unsupported-change-title"
      >
        <header className="change-approval-header">
          <div className="change-approval-heading">
            <span className="change-shield">
              <Icon name="warning" size={20} />
            </span>
            <div>
              <p className="panel-kicker">UNSUPPORTED CHANGE CONTRACT</p>
              <h2 id="unsupported-change-title">변경 계약을 안전하게 표시할 수 없습니다</h2>
            </div>
          </div>
        </header>
        <p className="change-notice change-error" role="alert">
          Host가 알 수 없거나 잘못된 planKind를 반환했습니다. 승인, 실행 및 복구를
          중단하고 계약을 다시 불러오세요.
        </p>
        {notice && (
          <p className="change-notice" role="status">
            {notice}
          </p>
        )}
        <footer className="change-actions">
          <div>
            <button
              type="button"
              className="button secondary-button"
              disabled={busy}
              onClick={onRefresh}
            >
              계약 다시 불러오기
            </button>
          </div>
        </footer>
      </section>
    );
  }
  const canDecide = transaction.state === "pending_approval";
  const contractReady = hasExecutableChangeContract(
    structuralMnePlan,
    legacyPlan,
  );
  const canExecuteApproved = transaction.state === "approved";
  const simulationReceipt = objectValue(
    objectValue(transaction.result.verification)?.simulation_receipt,
  );
  const simulationReceiptDigest =
    typeof simulationReceipt?.receipt_digest === "string" &&
    /^[0-9a-f]{64}$/.test(simulationReceipt.receipt_digest)
      ? simulationReceipt.receipt_digest
      : null;
  const observingSimulation =
    transaction.state === "observing" &&
    simulationReceiptDigest !== null &&
    isObservingMneSimulationReceipt(simulationReceipt);
  const terminal = TERMINAL_CHANGE_STATES.has(transaction.state);
  const successfulOfflineReceipt =
    transaction.state === "completed" ||
    transaction.state === "rolled_back";
  const receiptChecks = executionReceiptChecks(transaction, structuralMnePlan);
  const target = targetLabel(structuralMnePlan, legacyPlan);
  const expiresAt = new Date(transaction.approvalExpiresAt);
  const expiryText = Number.isNaN(expiresAt.getTime())
    ? transaction.approvalExpiresAt
    : new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "short",
        timeStyle: "medium",
      }).format(expiresAt);

  return (
    <section
      className={`change-approval change-state-${transaction.state}`}
      aria-labelledby="change-approval-title"
    >
      <header className="change-approval-header">
        <div className="change-approval-heading">
          <span className="change-shield">
            <Icon name="shield" size={20} />
          </span>
          <div>
            <p className="panel-kicker">CHANGE CONTRACT</p>
            <h2 id="change-approval-title">{transaction.intent}</h2>
            {target && <p>{target}</p>}
          </div>
        </div>
        <div className="change-state-stack">
          <span className={`risk-pill risk-${transaction.riskLevel}`}>
            위험 {RISK_LABELS[transaction.riskLevel]}
          </span>
          <span className="change-state-pill">
            {STATE_LABELS[transaction.state]}
          </span>
        </div>
      </header>

      <div className="change-capability-boundary" role="note">
        <strong>현재 실행 범위</strong>
        <span>
          변경 성격:{" "}
          {transaction.plan.requestScope === "offline_candidate_revision"
            ? "사용자가 선택한 오프라인 후보 revision · 저장됨 · 자동 원복 없음"
            : transaction.plan.requestScope === "permanent_offline_revision"
              ? "영구 오프라인 작업 복사본 revision"
              : "이전 계약 형식 · 실행 전 재검증 필요"}
        </span>
        {rungPatchPlan ? (
          <span>
            {rungPatchPlan.candidate.patch.operation === "insert_before"
              ? "선택한 rung 앞에 승인된 완전 rung을 삽입합니다."
              : rungPatchPlan.candidate.patch.operation === "replace_rung"
                ? "조건과 공유 action 그룹 전체를 같은 위치에서 교체합니다."
                : rungPatchPlan.candidate.patch.operation === "delete"
                ? "선택한 공유 action 그룹을 포함한 완전 rung을 삭제합니다."
                : "선택한 공유 action 그룹은 보존하고 Boolean 조건 전체를 교체합니다."} 동일
            baseline 재컴파일·구조 readback·Program Check·저장·독립 재열기로 검증합니다.
          </span>
        ) : instructionBatchPatchPlan ? (
          <span>
            같은 비공개 기준본에서 컴파일된 관련 명령 2–16개를 한 번의
            승인·구조 readback·Program Check·저장·독립 재열기로 검증합니다.
          </span>
        ) : instructionPatchPlan ? (
          <span>
            비공개 기준본에서 유일하게 찾은 명령을 기준으로 한 행만 삽입·교체·삭제하고
            구조 readback·Program Check·저장·독립 프로세스 재열기까지 확인합니다.
          </span>
        ) : seriesGuardPlan ? (
          <span>
            승인된 단일 OUT 바로 앞에 컴파일된 접점 1행만 삽입하고 구조
            readback·Program Check·저장·독립 프로세스 재열기까지 확인합니다.
          </span>
        ) : mnePlan ? (
          <span>
            지정된 프로그램 전체를 typed instruction 설계로 교체하고 구조
            readback·Program Check·저장·독립 프로세스 재열기까지 확인합니다.
          </span>
        ) : (
          <span>
            XG5000 오프라인 작업 복사본의 정확한 피연산자 1개를 변경하고
            구조 diff·프로그램 검사·저장까지 확인합니다.
          </span>
        )}
        <span>
          {rungPatchPlan?.verificationScope === "offline_native_reopen_xgsim"
            ? "컴파일된 XG-SIM normal→deny→restore 관찰 뒤 CPU 정지·입력 원복을 확인하고, 사용자가 Keep/Revert를 선택합니다. PLC 연결·쓰기는 포함되지 않습니다."
            : "XG-SIM 시험, 실제 PLC readback, 런중 수정과 설비 동작 검증은 이 실행에 포함되지 않습니다."}
        </span>
      </div>

      {mnePlan ? (
        <FullProgramTargetReview plan={mnePlan} />
      ) : rungPatchPlan ? (
        <RungPatchReview plan={rungPatchPlan} />
      ) : seriesGuardPlan ? (
        <SeriesGuardPatchReview plan={seriesGuardPlan} />
      ) : instructionBatchPatchPlan ? (
        <InstructionBatchPatchReview plan={instructionBatchPatchPlan} />
      ) : instructionPatchPlan ? (
        <InstructionPatchReview plan={instructionPatchPlan} />
      ) : legacyPlan ? (
        <>
          <TargetEvidenceReview plan={legacyPlan} />
          <div className="change-values">
            <ValueTable
              title="현재"
              values={legacyPlan.current}
              tone="current"
            />
            <span className="change-arrow" aria-hidden="true">
              <Icon name="arrow" size={20} />
            </span>
            <ValueTable
              title="제안"
              values={legacyPlan.proposed}
              tone="proposed"
            />
          </div>
        </>
      ) : null}

      <div className="change-plan-grid">
        <PlanList title="예상 영향" items={transaction.plan.impact} />
        <PlanList
          title="확인된 위험"
          items={transaction.plan.risks}
          warning
        />
        <PlanList title="복구 계획" items={transaction.plan.rollback} />
      </div>

      {(transaction.plan.blockingUnknowns?.length ?? 0) > 0 && (
        <PlanList
          title="미확인 · 실행 차단"
          items={transaction.plan.blockingUnknowns ?? []}
          warning
        />
      )}

      <CoverageReview
        items={transaction.plan.coverage ?? []}
        onOpenEvidence={onOpenEvidence}
      />

      <div className="change-trial-contract">
        <PlanList
          title="성공 기준"
          items={transaction.plan.acceptanceCriteria ?? []}
        />
        <PlanList
          title="관찰할 신호"
          items={transaction.plan.observationSignals ?? []}
        />
        <PlanList
          title="즉시 중단 조건"
          items={transaction.plan.stopConditions ?? []}
          warning
        />
      </div>

      <section
        className="change-verification-boundary"
        aria-labelledby="change-verification-boundary-title"
      >
        <div className="change-verification-heading">
          <Icon name="shield" size={18} />
          <div>
            <p className="panel-kicker">VERIFICATION RESPONSIBILITY</p>
            <h3 id="change-verification-boundary-title">검증 책임 범위</h3>
          </div>
        </div>
        <div className="change-verification-columns">
          <section className="change-auto-verification">
            <h4>ControlStudio가 자동으로 확인</h4>
            <p>
              {instructionBatchPatchPlan
                ? "범위: 승인된 명령 묶음 readback, XG5000 검사, 저장과 독립 재열기"
                : rungPatchPlan
                  ? "범위: 승인된 완전 rung patch readback, XG5000 검사, 저장과 독립 재열기"
                : instructionPatchPlan
                  ? "범위: 승인된 단일 명령 readback, XG5000 검사, 저장과 독립 재열기"
                  : seriesGuardPlan
                    ? "범위: 승인된 guard 1행 삽입 readback, XG5000 검사, 저장과 독립 재열기"
                    : mnePlan
                      ? "범위: typed 전체 프로그램 readback, XG5000 검사, 저장과 독립 재열기"
                      : "범위: 승인된 피연산자 변경, XG5000 검사, 저장, 새 프로세스 재열기와 LD readback"}
            </p>
            <ul>
              {transaction.plan.validation.map((step) => (
                <li key={step}>{VALIDATION_STEP_LABELS[step]}</li>
              ))}
            </ul>
          </section>
          <section className="change-manual-verification">
            <h4>사람이 직접 확인할 현장·후속 작업</h4>
            <p>
              아래 항목은 자동 검증 결과에 포함되지 않으며, 완료 전 별도로
              수행하고 기록해야 합니다.
            </p>
            {transaction.plan.manualFollowUp.length > 0 ? (
              <ul>
                {transaction.plan.manualFollowUp.map((item, index) => (
                  <li key={`${index}:${item}`}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="change-manual-empty">
                등록된 수동 후속 항목이 없습니다. 실기 PLC·설비 동작 검증이
                자동으로 완료됐다는 뜻이 아닙니다.
              </p>
            )}
          </section>
        </div>
      </section>

      {(terminal || transaction.state === "quarantined") && (
        <section
          className="change-execution-receipt"
          aria-labelledby="change-execution-receipt-title"
        >
          <div className="change-verification-heading">
            <Icon name="check" size={18} />
            <div>
              <p className="panel-kicker">EXECUTION RECEIPT</p>
              <h3 id="change-execution-receipt-title">실제 실행 결과</h3>
            </div>
          </div>
          <ul>
            {receiptChecks.map((check) => (
              <li key={check.label} data-passed={check.passed}>
                <Icon name={check.passed ? "check" : "warning"} size={16} />
                <span>{check.label}</span>
                <strong>{check.passed ? "증거 있음" : "확인되지 않음"}</strong>
              </li>
            ))}
          </ul>
          {structuralMnePlan && <MneReceiptReview transaction={transaction} />}
          <p>
            이 receipt는 오프라인 작업 복사본과 XG5000 프로그램 검사 결과만
            말합니다. XG-SIM·실제 PLC·설비 동작의 합격 판정은 아닙니다.
          </p>
          {successfulOfflineReceipt && (
            <label className="change-receipt-acknowledgement">
              <input
                type="checkbox"
                checked={receiptBoundaryAcknowledged}
                onChange={(event) =>
                  setReceiptBoundaryAcknowledged(event.target.checked)
                }
              />
              <span>
                자동 검증 범위를 확인했습니다. 위 수동 후속 검증은 별도로
                완료·기록해야 함을 이해합니다.
              </span>
            </label>
          )}
        </section>
      )}

      <details className="change-contract-detail">
        <summary>고정된 실행 계약과 기술 식별 정보</summary>
        <dl>
          <div>
            <dt>작업공간</dt>
            <dd>{transaction.targetIdentity.workspaceId}</dd>
          </div>
          <div>
            <dt>리비전</dt>
            <dd>{transaction.targetIdentity.revisionId}</dd>
          </div>
          <div>
            <dt>아티팩트 해시</dt>
            <dd>
              <code>
                {transaction.targetIdentity.artifactHash.slice(0, 16)}…
              </code>
            </dd>
          </div>
          <div>
            <dt>승인 만료</dt>
            <dd>{expiryText}</dd>
          </div>
          <div>
            <dt>Transaction</dt>
            <dd>
              <code>{transaction.transactionId}</code>
            </dd>
          </div>
          <div>
            <dt>Plan digest</dt>
            <dd>
              <code>{transaction.planDigest}</code>
            </dd>
          </div>
          <div>
            <dt>자동 검증 범위</dt>
            <dd>{transaction.plan.verificationScope}</dd>
          </div>
          <div>
            <dt>가정</dt>
            <dd>
              {(transaction.plan.assumptions ?? []).join(" · ") || "없음"}
            </dd>
          </div>
          <div>
            <dt>검토한 대안</dt>
            <dd>
              {(transaction.plan.alternatives ?? []).join(" · ") || "없음"}
            </dd>
          </div>
        </dl>
      </details>

      {transaction.errorDetail && (
        <p className="change-notice change-error" role="alert">
          {mnePlan
            ? `전체 프로그램 작업이 안전하게 완료되지 않았습니다. 오류 코드: ${transaction.errorCode ?? "unknown"}`
            : transaction.errorDetail}
        </p>
      )}
      {notice && (
        <p className="change-notice" role="status">
          {mnePlan
            ? "전체 프로그램 변경 상태가 갱신되었습니다. 알려진 receipt 항목을 확인하세요."
            : notice}
        </p>
      )}
      {canDecide && !contractReady && (
        <p className="change-notice change-error" role="alert">
          영향·위험·성공 기준과 10개 필수 조사 영역이 현재 Host 안전 계약을
          충족하지 않습니다. 이 계약은 승인할 수 없으며 수정 요청으로 다시
          작성해야 합니다.
        </p>
      )}
      {canExecuteApproved && (
        <p className="change-notice" role="status">
          승인은 기록됐지만 아직 적용되지 않았습니다. 현재 실행 계약에서는
          기록된 승인을 취소할 수 없으며, 적용 전 대상과 현재 상태를 다시
          검증합니다.
        </p>
      )}
      {observingSimulation && (
        <section className="change-verification-boundary" aria-label="XG-SIM observing receipt">
          <h3>XG-SIM 검증 완료 · Keep 또는 Revert 선택 필요</h3>
          <p>정상→차단→복원 OUT 관찰, CPU 정지와 입력 바이트 원복이 receipt에 기록됐습니다. PLC 연결·쓰기 없음.</p>
          <dl>
            <div><dt>Receipt digest</dt><dd><code>{simulationReceiptDigest}</code></dd></div>
            <div><dt>STOP</dt><dd>{objectValue(simulationReceipt?.stop)?.verified === true ? "verified" : "unverified"}</dd></div>
            <div><dt>입력 원복</dt><dd>{Array.isArray(simulationReceipt?.restore) && simulationReceipt.restore.every((item) => objectValue(item)?.matched === true) ? "verified" : "unverified"}</dd></div>
          </dl>
        </section>
      )}
      {canExecuteApproved && !contractReady && (
        <p className="change-notice change-error" role="alert">
          승인 기록은 존재하지만 현재 Web이 검증한 계약과 일치하지 않아 실행을
          차단했습니다. Host에서 계약을 다시 검증하세요.
        </p>
      )}
      {canExecuteApproved && structuralMnePlan && !mneExecutionReady && (
        <p className="change-notice change-error" role="alert">
          {mneExecutionDetail ??
            "이 환경에는 승인된 MNE 실행 capability가 준비되지 않았습니다."}
        </p>
      )}

      <footer className="change-actions">
        <p>
          승인은 위 대상·현재값·제안·digest에만 1회 유효합니다. 대상이나 계획이
          바뀌면 다시 승인해야 합니다.
        </p>
        <div>
          {!canDecide && !canExecuteApproved && !terminal && (
            <button
              type="button"
              className="button secondary-button"
              disabled={busy}
              onClick={onRefresh}
            >
              상태 다시 확인
            </button>
          )}
          {terminal && (
            <button
              type="button"
              className="button primary-button"
              disabled={
                busy ||
                (successfulOfflineReceipt && !receiptBoundaryAcknowledged)
              }
              onClick={onDismiss}
            >
              확인하고 대화로 돌아가기
            </button>
          )}
          {(canDecide || canExecuteApproved) && onOpenTarget && (
            <button
              type="button"
              className="button secondary-button"
              disabled={busy}
              onClick={onOpenTarget}
            >
              {busy
                ? "XG5000 대상 확인 중…"
                : legacyPlan
                  ? "XG5000에서 변경 위치 열기"
                  : "XG5000에서 프로그램 열기"}
            </button>
          )}
          {canExecuteApproved && (
            <>
              <button
                type="button"
                className="button secondary-button"
                disabled={busy}
                onClick={onRefresh}
              >
                상태 다시 확인
              </button>
              <button
                type="button"
                className="button primary-button change-approve-button"
                disabled={
                  busy ||
                  !contractReady ||
                  (Boolean(structuralMnePlan) && !mneExecutionReady)
                }
                onClick={onExecuteApproved}
              >
                {busy
                  ? "대상 재검증 중…"
                  : "승인된 작업 복사본 변경 실행"}
              </button>
            </>
          )}
          {observingSimulation && onFinalizeSimulation && (
            <>
              <button type="button" className="button secondary-button" disabled={busy} onClick={() => onFinalizeSimulation("revert")}>원래 rung으로 Revert</button>
              <button type="button" className="button primary-button change-approve-button" disabled={busy} onClick={() => onFinalizeSimulation("accept")}>검증된 변경 Keep</button>
            </>
          )}
          {canDecide && (
            <>
              {onRequestRevision && (
                <button
                  type="button"
                  className="button secondary-button"
                  disabled={busy}
                  onClick={onRequestRevision}
                >
                  수정 요청
                </button>
              )}
              <button
                type="button"
                className="button secondary-button"
                disabled={busy}
                onClick={onReject}
              >
                거절
              </button>
              <button
                type="button"
                className="button primary-button change-approve-button"
                disabled={busy || !contractReady}
                onClick={onApprove}
              >
                {busy ? "승인 확인 중…" : "이 변경 계약 승인"}
              </button>
            </>
          )}
        </div>
      </footer>
    </section>
  );
}
