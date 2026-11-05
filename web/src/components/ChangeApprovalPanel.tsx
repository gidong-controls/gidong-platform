import { isLegacyChangePlanV1 } from "../api/changeContracts";
import type {
  ChangeCoverageArea,
  ChangeCoverageItem,
  ChangeCoverageStatus,
  ChangePlanV1,
  ChangeRiskLevel,
  ChangeTransaction,
  OfflineNativeValidationStep,
} from "../api/types";
import { Icon } from "./Icon";

type ChangeValidationStep = OfflineNativeValidationStep;

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
  onApprove: () => void;
  onExecuteApproved: () => void;
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
  if (items.length === 0) return null;
  return (
    <section className={`change-plan-list ${warning ? "change-warning" : ""}`}>
      <h4>{title}</h4>
      <ul>
        {items.map((item, index) => (
          <li key={`${index}:${item}`}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function targetLabel(legacyPlan: ChangePlanV1 | undefined): string {
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

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
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
          <dt>분석 근거</dt>
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
          <dt>하드웨어 확인 근거</dt>
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

function hasExecutableChangeContract(legacyPlan: ChangePlanV1 | undefined): boolean {
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
  // An offline candidate is explicitly limited to the bound working copy.
  // The retained contract selects check-only or reopen verification. Its retained
  // hardware evidence must still be complete and digest-bound below, but signed
  // runtime blockers are displayed rather than treated as permission to reject
  // the offline-only contract. A permanent offline revision retains the
  // stricter resolved-hardware gate.
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
        <strong>
          {`검토한 영역 ${items.length}개`}
        </strong>
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

export function ChangeApprovalPanel({
  transaction,
  busy,
  notice,
  onApprove,
  onExecuteApproved,
  onReject,
  onRequestRevision,
  onOpenTarget,
  onOpenEvidence,
  onRefresh,
  onDismiss,
}: ChangeApprovalPanelProps) {
  const legacyPlan = isLegacyChangePlanV1(transaction.plan) ? transaction.plan : undefined;
  if (!legacyPlan) {
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
              <p className="panel-kicker">변경 정보 확인 필요</p>
              <h2 id="unsupported-change-title">변경 정보를 확인할 수 없습니다</h2>
            </div>
          </div>
        </header>
        <p className="change-notice change-error" role="alert">
          변경 정보를 확인할 수 없어 승인·실행을 중지했습니다. 새로고침한 뒤 다시 확인하세요.
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
              변경 정보 새로고침
            </button>
          </div>
        </footer>
      </section>
    );
  }
  const canDecide = transaction.state === "pending_approval";
  const directCheckOnlyPlan =
    transaction.plan.requestScope === "permanent_offline_revision" &&
    transaction.plan.verificationScope === "offline_native_check_only";
  const checkOnlyPlan =
    transaction.plan.verificationScope === "offline_native_check_only";
  const publicErrorCode =
    typeof transaction.errorCode === "string" &&
    /^[a-z0-9_.-]{1,120}$/i.test(transaction.errorCode)
      ? transaction.errorCode
      : "unknown";
  const contractReady = hasExecutableChangeContract(legacyPlan);
  const canExecuteApproved = transaction.state === "approved";
  const terminal = TERMINAL_CHANGE_STATES.has(transaction.state);
  const target = targetLabel(legacyPlan);
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
            ? "사용자가 선택한 오프라인 후보 revision · 승인된 변경을 작업 복사본에만 적용·저장하는 계약"
            : directCheckOnlyPlan
              ? "사용자가 명시한 작업 사본 수정 요청 · 1회 권한으로 구조 diff·Program Check·저장 실행 · 별도 승인 대기 없음"
              : "이전 계약 형식 · 실행 전 재검증 필요"}
        </span>
        <span>XG5000 오프라인 작업 복사본의 정확한 피연산자 1개를 변경하고 구조 diff·프로그램 검사·저장으로 확인합니다.</span>
      </div>
      <TargetEvidenceReview plan={legacyPlan} />
      <div className="change-values">
        <ValueTable title="현재" values={legacyPlan.current} tone="current" />
        <span className="change-arrow" aria-hidden="true"><Icon name="arrow" size={20} /></span>
        <ValueTable title="제안" values={legacyPlan.proposed} tone="proposed" />
      </div>

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
            <p className="panel-kicker">VERIFICATION</p>
            <h3 id="change-verification-boundary-title">확인 항목</h3>
          </div>
        </div>
        <div className="change-verification-columns">
          <section className="change-auto-verification">
            <h4>ControlStudio가 자동으로 확인</h4>
            <p>
              {checkOnlyPlan
                ? "범위: 구조 readback, XG5000 검사와 저장. 정상 성공 경로에서는 독립 재열기를 하지 않으며, 검증 실패 복구에서는 원복 확인을 위해 독립 재열기가 수행될 수 있습니다."
                : "범위: 승인된 피연산자 변경, XG5000 검사, 저장, 새 프로세스 재열기와 LD readback"}
            </p>
            <ul>
              {transaction.plan.validation.map((step) => (
                <li key={step}>{VALIDATION_STEP_LABELS[step]}</li>
              ))}
            </ul>
          </section>
          {transaction.plan.manualFollowUp.length > 0 && (
            <section className="change-manual-verification">
              <h4>수동 후속 작업</h4>
              <ul>
                {transaction.plan.manualFollowUp.map((item, index) => (
                  <li key={`${index}:${item}`}>{item}</li>
                ))}
              </ul>
            </section>
          )}
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
          <p>Host에 기록된 상태: {STATE_LABELS[transaction.state]}</p>
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
          {`작업을 완료하지 못했습니다. 오류 코드: ${publicErrorCode}`}
        </p>
      )}
      {notice && (
        <p className="change-notice" role="status">
          {notice}
        </p>
      )}
      {canDecide && !contractReady && (
        <p className="change-notice change-error" role="alert">
          "현재 프로그램의 대상 근거 또는 변경안의 필수 정보가 부족합니다." 수정 요청으로 부족한 내용을 보완할 수 있습니다.
        </p>
      )}
      {canExecuteApproved && (
        <p className="change-notice" role="status">
          승인은 기록됐지만 아직 적용되지 않았습니다. 현재 실행 계약에서는
          기록된 승인을 취소할 수 없으며, 적용 전 대상과 현재 상태를 다시
          검증합니다.
        </p>
      )}
      {canExecuteApproved && !contractReady && (
        <p className="change-notice change-error" role="alert">
          승인 기록은 존재하지만 현재 Web이 검증한 계약과 일치하지 않아 실행을
          차단했습니다. 상태를 새로고침한 뒤 다시 확인하세요.
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
              disabled={busy}
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
                  !contractReady
                }
                onClick={onExecuteApproved}
              >
                {busy
                  ? "대상 재검증 중…"
                  : "승인된 작업 복사본 변경 실행"}
              </button>
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
