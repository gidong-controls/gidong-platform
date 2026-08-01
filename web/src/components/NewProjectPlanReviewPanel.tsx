import { useEffect, useMemo, useState } from "react";
import type {
  EngineeringPlanItem,
  EngineeringPlanOpenDecision,
  EngineeringPlanItemSource,
  EngineeringPlanItemStatus,
  EngineeringPlanReview,
  NewProjectEngineeringPlan,
} from "../api/types";

export interface NewProjectPlanReviewPanelProps {
  artifact: NewProjectEngineeringPlan;
  planDigest: string;
  review?: EngineeringPlanReview;
  busy: boolean;
  onReview: (planDigest: string, reviewedSectionIds: string[]) => void;
  onRequestRevision: () => void;
}

const STATUS_LABELS: Record<EngineeringPlanItemStatus, string> = {
  confirmed: "확인됨",
  proposed: "제안",
  unknown: "미확인",
  conflicting: "충돌",
  not_applicable: "해당 없음",
};

const SOURCE_LABELS: Record<EngineeringPlanItemSource, string> = {
  user: "사용자 제공",
  agent: "에이전트 작성",
};

function getCoveragePercent(
  totalItemCount: number,
  knownItemCount: number,
) {
  if (totalItemCount <= 0) {
    return 0;
  }
  return Math.min(
    100,
    Math.round((knownItemCount / totalItemCount) * 100),
  );
}

function formatReviewedAt(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function PlanItemRow({ item }: { item: EngineeringPlanItem }) {
  return (
    <li
      className={[
        "engineering-plan-item",
        item.blocking ? "engineering-plan-item--blocking" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="engineering-plan-item__main">
        <p>{item.text}</p>
        <div className="engineering-plan-item__meta">
          <span
            className={`engineering-plan-pill engineering-plan-pill--${item.status}`}
          >
            {STATUS_LABELS[item.status]}
          </span>
          <span className="engineering-plan-pill engineering-plan-pill--source">
            {SOURCE_LABELS[item.source]}
          </span>
          {item.blocking && (
            <strong className="engineering-plan-blocking-label">
              검토 차단
            </strong>
          )}
        </div>
      </div>
      {item.verificationMethod && (
        <p className="engineering-plan-verification">
          <span>확인 방법</span>
          {item.verificationMethod}
        </p>
      )}
      {item.knowledgeRefs && item.knowledgeRefs.length > 0 && (
        <div className="engineering-plan-knowledge">
          <span>적용 근거</span>
          {item.knowledgeRefs.map((reference) => (
            <code key={reference}>{reference}</code>
          ))}
        </div>
      )}
    </li>
  );
}

function OpenDecisionRow({
  decision,
}: {
  decision: EngineeringPlanOpenDecision;
}) {
  return (
    <li className="engineering-plan-item engineering-plan-item--blocking">
      <div className="engineering-plan-item__main">
        <p>{decision.question}</p>
        <div className="engineering-plan-item__meta">
          <span className="engineering-plan-pill engineering-plan-pill--unknown">
            {STATUS_LABELS.unknown}
          </span>
          <span className="engineering-plan-pill engineering-plan-pill--source">
            {SOURCE_LABELS.agent}
          </span>
          <strong className="engineering-plan-blocking-label">검토 차단</strong>
        </div>
      </div>
      {decision.verificationMethod && (
        <p className="engineering-plan-verification">
          <span>확인 방법</span>
          {decision.verificationMethod}
        </p>
      )}
    </li>
  );
}

export function NewProjectPlanReviewPanel({
  artifact,
  planDigest,
  review,
  busy,
  onReview,
  onRequestRevision,
}: NewProjectPlanReviewPanelProps) {
  const [selectedSectionId, setSelectedSectionId] = useState(
    artifact.sections[0]?.sectionId ?? "",
  );
  const [acknowledgedSectionIds, setAcknowledgedSectionIds] = useState<
    string[]
  >(
    review?.planDigest === planDigest ? review.reviewedSectionIds : [],
  );

  useEffect(() => {
    if (
      !artifact.sections.some(
        (section) => section.sectionId === selectedSectionId,
      )
    ) {
      setSelectedSectionId(artifact.sections[0]?.sectionId ?? "");
    }
  }, [artifact.sections, selectedSectionId]);

  useEffect(() => {
    setAcknowledgedSectionIds(
      review?.planDigest === planDigest ? review.reviewedSectionIds : [],
    );
  }, [planDigest, review]);

  const selectedSection =
    artifact.sections.find(
      (section) => section.sectionId === selectedSectionId,
    ) ?? artifact.sections[0];

  const blockingDecisions = useMemo(
    () => artifact.openDecisions.filter((decision) => decision.blocking),
    [artifact.openDecisions],
  );
  const coverage = getCoveragePercent(
    artifact.completeness.totalItems,
    artifact.completeness.confirmed +
      artifact.completeness.proposed +
      artifact.completeness.notApplicable,
  );
  const sectionIds = artifact.sections.map((section) => section.sectionId);
  const hasEverySectionAcknowledged =
    acknowledgedSectionIds.length === sectionIds.length &&
    sectionIds.every((sectionId) =>
      acknowledgedSectionIds.includes(sectionId),
    );
  const currentReview =
    review?.planDigest === planDigest &&
    review.reviewedSectionIds.length === sectionIds.length &&
    sectionIds.every((sectionId) =>
      review.reviewedSectionIds.includes(sectionId),
    )
      ? review
      : undefined;
  const staleReview = Boolean(review && !currentReview);
  const canReview =
    artifact.status === "ready_for_review" &&
    artifact.completeness.blocking === 0 &&
    blockingDecisions.length === 0 &&
    hasEverySectionAcknowledged;

  const reviewLockReason =
    artifact.status !== "ready_for_review"
      ? "미확인 사항을 먼저 해결해야 합니다."
      : artifact.completeness.blocking > 0 ||
          blockingDecisions.length > 0
        ? "검토를 차단하는 결정 사항을 먼저 해결해야 합니다."
        : !hasEverySectionAcknowledged
          ? "모든 계획 섹션을 하나씩 확인하고 검토 표시해 주세요."
        : "";

  const setSectionAcknowledged = (
    sectionId: string,
    acknowledged: boolean,
  ) => {
    setAcknowledgedSectionIds((current) => {
      if (acknowledged) {
        return sectionIds.filter(
          (candidate) =>
            candidate === sectionId || current.includes(candidate),
        );
      }
      return current.filter((candidate) => candidate !== sectionId);
    });
  };

  return (
    <section
      className="engineering-plan-review"
      aria-labelledby="engineering-plan-title"
    >
      <header className="engineering-plan-header">
        <div>
          <p className="eyebrow">새 프로젝트 · Engineering Plan</p>
          <h2 id="engineering-plan-title">{artifact.projectName}</h2>
          <p className="engineering-plan-summary">{artifact.summary}</p>
        </div>
        <div className="engineering-plan-readiness" aria-live="polite">
          <span
            className={`engineering-plan-readiness__status engineering-plan-readiness__status--${artifact.status}`}
          >
            {artifact.status === "ready_for_review"
              ? "검토 준비됨"
              : "확인 필요"}
          </span>
          <strong>{coverage}% 작성</strong>
          <span>
            {artifact.completeness.totalItems}개 계획 항목
          </span>
        </div>
      </header>

      <div className="engineering-plan-coverage">
        <progress
          aria-label="계획 작성 범위"
          max={100}
          value={coverage}
        />
        <dl>
          <div>
            <dt>확인</dt>
            <dd>{artifact.completeness.confirmed}</dd>
          </div>
          <div>
            <dt>제안</dt>
            <dd>{artifact.completeness.proposed}</dd>
          </div>
          <div>
            <dt>미확인</dt>
            <dd>
              {artifact.completeness.unknown +
                artifact.completeness.conflicting}
            </dd>
          </div>
          <div className="engineering-plan-coverage__blocking">
            <dt>차단</dt>
            <dd>{artifact.completeness.blocking}</dd>
          </div>
        </dl>
      </div>

      <section
        className="engineering-plan-objective"
        aria-labelledby="engineering-plan-objective-title"
      >
        <h3 id="engineering-plan-objective-title">프로젝트 목표</h3>
        <p>{artifact.objective}</p>
      </section>

      {artifact.openDecisions.length > 0 && (
        <section
          className="engineering-plan-decisions"
          aria-labelledby="engineering-plan-decisions-title"
        >
          <div className="engineering-plan-decisions__heading">
            <div>
              <p className="eyebrow">사람이 결정할 항목</p>
              <h3 id="engineering-plan-decisions-title">열린 결정</h3>
            </div>
            {blockingDecisions.length > 0 && (
              <strong role="status">
                검토 차단 {blockingDecisions.length}건
              </strong>
            )}
          </div>
          <ul className="engineering-plan-item-list">
            {blockingDecisions.map((decision) => (
              <OpenDecisionRow
                key={decision.decisionId}
                decision={decision}
              />
            ))}
          </ul>
        </section>
      )}

      <div className="engineering-plan-workspace">
        <nav
          className="engineering-plan-navigation"
          aria-label="계획 섹션"
        >
          <ol>
            {artifact.sections.map((section, index) => {
              const blockingCount = section.items.filter(
                (item) => item.blocking,
              ).length;
              const unknownCount = section.items.filter(
                (item) =>
                  item.status === "unknown" || item.status === "conflicting",
              ).length;
              const selected = section.sectionId === selectedSection?.sectionId;
              return (
                <li key={section.sectionId}>
                  <button
                    type="button"
                    className={selected ? "is-selected" : ""}
                    aria-current={selected ? "true" : undefined}
                    aria-controls="engineering-plan-section-detail"
                    onClick={() => setSelectedSectionId(section.sectionId)}
                  >
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{section.title}</strong>
                    {(blockingCount > 0 || unknownCount > 0) && (
                      <small>
                        {blockingCount > 0
                          ? `차단 ${blockingCount}`
                          : `미확인 ${unknownCount}`}
                      </small>
                    )}
                    {acknowledgedSectionIds.includes(section.sectionId) && (
                      <small className="engineering-plan-reviewed-section">
                        검토함
                      </small>
                    )}
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <section
          id="engineering-plan-section-detail"
          className="engineering-plan-section-detail"
          aria-live="polite"
          aria-labelledby="engineering-plan-section-title"
        >
          {selectedSection ? (
            <>
              <div className="engineering-plan-section-detail__heading">
                <h3 id="engineering-plan-section-title">
                  {selectedSection.title}
                </h3>
                <span>{selectedSection.items.length}개 항목</span>
              </div>
              {!currentReview && (
                <label className="engineering-plan-section-acknowledgement">
                  <input
                    type="checkbox"
                    checked={acknowledgedSectionIds.includes(
                      selectedSection.sectionId,
                    )}
                    disabled={busy}
                    onChange={(event) =>
                      setSectionAcknowledged(
                        selectedSection.sectionId,
                        event.currentTarget.checked,
                      )
                    }
                  />
                  <span>
                    이 섹션의 항목·상태·확인 방법을 검토했습니다
                  </span>
                </label>
              )}
              {selectedSection.items.length > 0 ? (
                <ul className="engineering-plan-item-list">
                  {selectedSection.items.map((item) => (
                    <PlanItemRow key={item.itemId} item={item} />
                  ))}
                </ul>
              ) : (
                <p className="engineering-plan-empty">
                  아직 작성된 항목이 없습니다. 수정 요청에서 필요한 정보를
                  추가해 주세요.
                </p>
              )}
            </>
          ) : (
            <p className="engineering-plan-empty">
              검토할 계획 섹션이 아직 없습니다.
            </p>
          )}
        </section>
      </div>

      <details className="engineering-plan-technical-details">
        <summary>계획 식별 정보</summary>
        <dl>
          <div>
            <dt>스키마</dt>
            <dd>{artifact.schemaVersion}</dd>
          </div>
          <div>
            <dt>계획 digest</dt>
            <dd>
              <code>{planDigest}</code>
            </dd>
          </div>
        </dl>
      </details>

      {staleReview && (
        <p className="engineering-plan-stale-review" role="alert">
          계획이 변경되어 이전 검토 완료 기록은 더 이상 유효하지 않습니다.
        </p>
      )}

      <footer className="engineering-plan-actions">
        <button
          className="button secondary-button"
          type="button"
          disabled={busy}
          onClick={onRequestRevision}
        >
          수정 요청
        </button>

        {currentReview ? (
          <p className="engineering-plan-reviewed" role="status">
            <strong>계획 검토 완료</strong>
            <span>
              {currentReview.actorId} ·{" "}
              <time dateTime={currentReview.reviewedAt}>
                {formatReviewedAt(currentReview.reviewedAt)}
              </time>
            </span>
          </p>
        ) : (
          <div className="engineering-plan-review-action">
            <button
              className="button primary-button"
              type="button"
              disabled={busy || !canReview}
              aria-describedby={
                !canReview ? "engineering-plan-review-lock" : undefined
              }
              onClick={() =>
                onReview(
                  planDigest,
                  sectionIds.filter((sectionId) =>
                    acknowledgedSectionIds.includes(sectionId),
                  ),
                )
              }
            >
              {busy ? "처리 중…" : "이 계획 검토 완료"}
            </button>
            {!canReview && (
              <small id="engineering-plan-review-lock">
                {reviewLockReason}
              </small>
            )}
          </div>
        )}
      </footer>
    </section>
  );
}
