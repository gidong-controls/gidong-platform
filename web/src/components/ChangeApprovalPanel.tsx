import { useState } from "react";
import type {
  ChangeCoverageArea,
  ChangeCoverageItem,
  ChangeCoverageStatus,
  ChangePlan,
  ChangeRiskLevel,
  ChangeTransaction,
  OfflineNativeValidationStep,
} from "../api/types";
import { Icon } from "./Icon";

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
  completed: "적용·오프라인 검사 완료",
  rolled_back: "검증 후 원복됨",
  failed: "실패",
  quarantined: "결과 확인 필요",
  rejected: "거절됨",
  expired: "승인 만료",
};

const VALIDATION_STEP_LABELS: Record<OfflineNativeValidationStep, string> = {
  structural_diff: "변경 전후 네이티브 구조 diff 확인",
  xg5000_program_check: "XG5000 프로그램 검사",
  project_save: "오프라인 작업 복사본 저장",
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

function targetLabel(plan: ChangePlan): string {
  const target = plan.target;
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

function hasExecutableChangeContract(plan: ChangePlan): boolean {
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

function executionReceiptChecks(transaction: ChangeTransaction) {
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
  ];
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
  const [receiptBoundaryAcknowledged, setReceiptBoundaryAcknowledged] =
    useState(false);
  const canDecide = transaction.state === "pending_approval";
  const contractReady = hasExecutableChangeContract(transaction.plan);
  const canExecuteApproved = transaction.state === "approved";
  const terminal = TERMINAL_CHANGE_STATES.has(transaction.state);
  const successfulOfflineReceipt =
    transaction.state === "completed" ||
    transaction.state === "rolled_back";
  const receiptChecks = executionReceiptChecks(transaction);
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
            {targetLabel(transaction.plan) && (
              <p>{targetLabel(transaction.plan)}</p>
            )}
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
        <span>
          XG5000 오프라인 작업 복사본의 정확한 피연산자 1개를 변경하고
          구조 diff·프로그램 검사·저장까지 확인합니다.
        </span>
        <span>
          XG-SIM 시험, 실제 PLC readback, 런중 수정과 설비 동작 검증은 이
          실행에 포함되지 않습니다.
        </span>
      </div>

      <div className="change-values">
        <ValueTable
          title="현재"
          values={transaction.plan.current}
          tone="current"
        />
        <span className="change-arrow" aria-hidden="true">
          <Icon name="arrow" size={20} />
        </span>
        <ValueTable
          title="제안"
          values={transaction.plan.proposed}
          tone="proposed"
        />
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
            <p className="panel-kicker">VERIFICATION RESPONSIBILITY</p>
            <h3 id="change-verification-boundary-title">검증 책임 범위</h3>
          </div>
        </div>
        <div className="change-verification-columns">
          <section className="change-auto-verification">
            <h4>ControlStudio가 자동으로 확인</h4>
            <p>
              범위: 오프라인 작업 복사본의 네이티브 변경과 XG5000 검사까지
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
            <dt>프로젝트</dt>
            <dd>{transaction.targetIdentity.projectPath}</dd>
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
          {transaction.errorDetail}
        </p>
      )}
      {notice && (
        <p className="change-notice" role="status">
          {notice}
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
                disabled={busy}
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
              {onOpenTarget && (
                <button
                  type="button"
                  className="button secondary-button"
                  disabled={busy}
                  onClick={onOpenTarget}
                >
                  XG5000에서 프로그램 열기
                </button>
              )}
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
