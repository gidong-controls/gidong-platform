import { useEffect, useMemo, useState } from "react";
import type {
  EngineeringPlanItem,
  EngineeringPlanOpenDecision,
  EngineeringPlanItemSource,
  EngineeringPlanItemStatus,
  EngineeringPlanLadderAction,
  EngineeringPlanLadderCondition,
  EngineeringPlanReview,
  EngineeringPlanRevision,
  NewProjectEngineeringPlan,
} from "../api/types";

export interface NewProjectPlanReviewPanelProps {
  artifact: NewProjectEngineeringPlan;
  planDigest: string;
  review?: EngineeringPlanReview;
  revision?: EngineeringPlanRevision;
  busy: boolean;
  onReview: (
    planDigest: string,
    reviewedSectionIds: string[],
    reviewedItemIds: string[],
  ) => void;
  onRequestRevision: () => void;
}

export function projectPlanReviewItemIds(
  artifact: NewProjectEngineeringPlan,
): string[] | undefined {
  const ladderRungs = artifact.design.ladderRungs;
  if (
    !artifact.technicalConfiguration ||
    !artifact.design.collectionScope ||
    !ladderRungs ||
    ladderRungs.length === 0
  ) {
    return undefined;
  }
  const controlNetworkSectionIndex = artifact.sections.findIndex(
    (section) => section.sectionId === "native-control-networks",
  );
  if (
    controlNetworkSectionIndex < 0 ||
    artifact.sections[controlNetworkSectionIndex + 1]?.sectionId !==
      "native-ladder-rungs"
  ) {
    return undefined;
  }
  const itemIds = artifact.sections.flatMap((section) =>
    section.items.map((item) => item.itemId),
  );
  if (
    itemIds.length === 0 ||
    itemIds.some((itemId) => !itemId) ||
    new Set(itemIds).size !== itemIds.length
  ) {
    return undefined;
  }
  const escape = (value: string) => value.replaceAll("~", "~0").replaceAll("/", "~1");
  const condition = (value: { signalId: string; state: "on" | "off" }) =>
    `${escape(value.signalId)}:${value.state}`;
  const expected: string[] = [
    "technical/cpuModel",
    "technical/description",
    "technical/language",
    "technical/plcSeries",
    "technical/programName",
    "technical/projectName",
    "scope/ioPoints",
    "scope/operatingModes",
    "scope/controlNetworks",
    "scope/ladderRungs",
    "scope/timers",
    "scope/sequences",
    "scope/alarms",
    "scope/recoveryRules",
    "scope/safetyRules",
  ];
  artifact.design.ioPoints.forEach((point) => {
    const base = `design/ioPoints/${escape(point.signalId)}`;
    expected.push(`${base}/mapping`, `${base}/normalState`, `${base}/safeState`);
  });
  artifact.design.operatingModes.forEach((mode) => {
    const base = `design/operatingModes/${escape(mode.modeId)}`;
    expected.push(`${base}/activation`, `${base}/transitions`);
    mode.allowedTransitions.forEach((target) =>
      expected.push(`${base}/transition/${escape(target)}`),
    );
  });
  artifact.design.controlNetworks.forEach((network) => {
    const base = `design/controlNetworks/${escape(network.networkId)}`;
    expected.push(`${base}/output`);
    network.permitAll.forEach((permit) =>
      expected.push(`${base}/permit/${condition(permit)}`),
    );
    expected.push(`${base}/mode`, `${base}/step`);
  });
  const expectedLadderValues = new Map<string, unknown>([
    [
      "scope/ladderRungs",
      {
        status: "confirmed",
        count: ladderRungs.length,
        ids: ladderRungs.map((rung) => rung.rungId),
      },
    ],
  ]);
  ladderRungs.forEach((rung, position) => {
    const base = `design/ladderRungs/${escape(rung.rungId)}`;
    expected.push(
      `${base}/position`,
      `${base}/description`,
      `${base}/condition`,
      `${base}/action`,
    );
    expectedLadderValues.set(`${base}/position`, position);
    expectedLadderValues.set(`${base}/description`, rung.description);
    expectedLadderValues.set(`${base}/condition`, rung.condition);
    expectedLadderValues.set(`${base}/action`, rung.action);
  });
  artifact.design.timers.forEach((timer) => {
    const base = `design/timers/${escape(timer.timerId)}`;
    expected.push(`${base}/address`, `${base}/duration`);
    timer.enableAll.forEach((enable) =>
      expected.push(`${base}/enable/${condition(enable)}`),
    );
    expected.push(`${base}/mode`, `${base}/step`);
  });
  artifact.design.sequences.forEach((sequence) => {
    const base = `design/sequences/${escape(sequence.sequenceId)}`;
    expected.push(`${base}/restartPolicy`);
    sequence.startAll.forEach((start) =>
      expected.push(`${base}/start/${condition(start)}`),
    );
    sequence.resetAll.forEach((reset) =>
      expected.push(`${base}/reset/${condition(reset)}`),
    );
    expected.push(`${base}/mode`);
    sequence.steps.forEach((step) => {
      const stepBase = `${base}/step/${escape(step.stepId)}`;
      expected.push(`${stepBase}/address`);
      step.completeAll.forEach((complete) =>
        expected.push(`${stepBase}/complete/${condition(complete)}`),
      );
    });
  });
  artifact.design.alarms.forEach((alarm) => {
    const base = `design/alarms/${escape(alarm.alarmId)}`;
    expected.push(`${base}/latch`);
    alarm.triggerAll.forEach((trigger) =>
      expected.push(`${base}/trigger/${condition(trigger)}`),
    );
    alarm.affectedOutputSignalIds.forEach((output) =>
      expected.push(`${base}/output/${escape(output)}`),
    );
    expected.push(`${base}/mode`, `${base}/step`);
  });
  artifact.design.recoveryRules.forEach((recovery) => {
    const base = `design/recoveryRules/${escape(recovery.recoveryId)}`;
    expected.push(`${base}/alarms`, `${base}/sequences`);
    recovery.whenAll.forEach((when) =>
      expected.push(`${base}/when/${condition(when)}`),
    );
    recovery.resetAlarmIds.forEach((alarm) =>
      expected.push(`${base}/alarm/${escape(alarm)}`),
    );
    recovery.resetSequenceIds.forEach((sequence) =>
      expected.push(`${base}/sequence/${escape(sequence)}`),
    );
  });
  artifact.design.safetyRules.forEach((rule) => {
    const base = `design/safetyRules/${escape(rule.ruleId)}`;
    expected.push(`${base}/classification`, `${base}/action`, `${base}/permit`);
    if (rule.permitWhen) {
      expected.push(`${base}/permit/${condition(rule.permitWhen)}`);
    }
    rule.affectedOutputSignalIds.forEach((output) =>
      expected.push(`${base}/output/${escape(output)}`),
    );
  });
  const expectedSet = new Set(expected);
  const semanticItems = artifact.sections.flatMap((section) =>
    section.items
      .filter(
        (item) =>
        item.itemId.startsWith("technical/") ||
        item.itemId.startsWith("scope/") ||
        item.itemId.startsWith("design/"),
      )
      .map((item) => ({ item, sectionId: section.sectionId })),
  );
  const canonicalJson = (value: unknown): string => {
    if (Array.isArray(value)) {
      return `[${value.map(canonicalJson).join(",")}]`;
    }
    if (value !== null && typeof value === "object") {
      const record = value as Record<string, unknown>;
      return `{${Object.keys(record)
        .sort()
        .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
        .join(",")}}`;
    }
    return JSON.stringify(value) ?? "undefined";
  };
  if (
    expectedSet.size !== expected.length ||
    semanticItems.length !== expected.length ||
    semanticItems.some(({ item, sectionId }) => {
      if (!expectedSet.has(item.itemId)) return true;
      try {
        const parsed = JSON.parse(item.text) as {
          path?: unknown;
          value?: unknown;
        };
        const expectedLadderValue = expectedLadderValues.get(item.itemId);
        return (
          parsed.path !== item.itemId ||
          (item.itemId === "scope/ladderRungs" ||
          item.itemId.startsWith("design/ladderRungs/")
            ? sectionId !== "native-ladder-rungs" ||
              !expectedLadderValues.has(item.itemId) ||
              canonicalJson(parsed.value) !== canonicalJson(expectedLadderValue)
            : false)
        );
      } catch {
        return true;
      }
    })
  ) {
    return undefined;
  }
  return itemIds;
}

function sameOrderedValues(left: string[], right: string[]) {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

interface SemanticReviewValue {
  path: string;
  value: unknown;
}

function semanticReviewValue(item: EngineeringPlanItem) {
  if (
    !item.itemId.startsWith("technical/") &&
    !item.itemId.startsWith("scope/") &&
    !item.itemId.startsWith("design/")
  ) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(item.text) as Partial<SemanticReviewValue>;
    return parsed.path === item.itemId && "value" in parsed
      ? (parsed as SemanticReviewValue)
      : undefined;
  } catch {
    return undefined;
  }
}

function semanticReviewLabel(path: string) {
  if (path === "technical/projectName") return "XG5000 프로젝트 이름";
  if (path === "technical/plcSeries") return "PLC 시리즈";
  if (path === "technical/cpuModel") return "CPU 모델";
  if (path === "technical/programName") return "첫 프로그램 이름";
  if (path === "technical/language") return "프로그램 언어";
  if (path === "technical/description") return "XG5000 프로젝트 설명";
  if (path === "scope/ladderRungs") return "LD rung 전체 순서와 식별자";
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/position")) {
    return "LD rung 실행 순서";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/description")) {
    return "LD rung 설명";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/condition")) {
    return "LD rung 조건·분기·접점 극성";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/action")) {
    return "LD rung 명령·typed 대상";
  }
  if (path.startsWith("scope/")) return "제어 묶음 전체 범위";
  if (path.endsWith("/mapping")) return "I/O 기호·주소·종류";
  if (path.endsWith("/normalState")) return "정상 상태";
  if (path.endsWith("/safeState")) return "안전 상태";
  if (path.endsWith("/activation")) return "모드 활성 조건";
  if (path.endsWith("/transitions")) return "허용 전이 전체 목록";
  if (path.includes("/transition/")) return "허용 전이 관계";
  if (path.endsWith("/output")) return "제어 출력";
  if (path.includes("/output/")) return "영향 출력 관계";
  if (path.endsWith("/address")) return "실행 주소";
  if (path.endsWith("/duration")) return "타이머 시간·베이스·프리셋";
  if (path.endsWith("/restartPolicy")) return "재시작 정책";
  if (path.endsWith("/latch")) return "알람 래치";
  if (path.endsWith("/alarms")) return "복구 알람 전체 목록";
  if (path.endsWith("/sequences")) return "복구 시퀀스 전체 목록";
  if (path.endsWith("/classification")) return "안전 분류";
  if (path.endsWith("/action")) return "안전 동작";
  if (path.endsWith("/permit")) return "안전 허가 조건(없음 포함)";
  if (path.endsWith("/mode")) return "적용 모드(전체 포함)";
  if (path.endsWith("/step")) return "적용 스텝(전체 포함)";
  if (path.includes("/permit/")) return "동시 허가 조건";
  if (path.includes("/enable/")) return "타이머 활성 조건";
  if (path.includes("/start/")) return "시퀀스 시작 조건";
  if (path.includes("/reset/")) return "시퀀스 복귀 조건";
  if (path.includes("/complete/")) return "스텝 완료 조건";
  if (path.includes("/trigger/")) return "알람 발생 조건";
  if (path.includes("/when/")) return "복구 실행 조건";
  if (path.includes("/alarm/")) return "알람 복구 대상";
  if (path.includes("/sequence/")) return "시퀀스 복구 대상";
  return "실행 설계 값";
}

function LadderConditionView({
  condition,
}: {
  condition: EngineeringPlanLadderCondition;
}) {
  if (condition.kind === "contact") {
    return (
      <code>
        {condition.sourceType}:{condition.sourceId} · {condition.state.toUpperCase()} ·{" "}
        {condition.state === "on" ? "정접(NO)" : "부접(NC)"}
      </code>
    );
  }
  return (
    <div className="engineering-plan-ladder-condition">
      <strong>
        {condition.kind === "all_of" ? "AND · 모든 분기" : "OR · 어느 분기든"}
      </strong>
      <ol>
        {condition.terms.map((term, index) => (
          <li key={`${condition.kind}-${index}`}>
            <span>분기 {index + 1}</span>
            <LadderConditionView condition={term} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function ladderActionLabel(action: EngineeringPlanLadderAction) {
  if (action.instruction === "MOV") {
    return `MOV · ${action.sourceConstant} → ${action.destinationAddress}`;
  }
  return `${action.instruction} · ${action.targetType}:${action.targetId}`;
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

const REVISION_FIELD_LABELS: Record<
  EngineeringPlanRevision["changedFields"][number],
  string
> = {
  projectName: "프로젝트 이름",
  status: "검토 상태",
  summary: "요약",
  objective: "프로젝트 목표",
  attachments: "첨부 자료",
  technicalConfiguration: "XG5000 기술 구성",
  design: "구조화 제어 설계",
  sections: "계획 섹션",
  openDecisions: "열린 결정",
  completeness: "작성 현황",
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

function PlanItemRow({
  item,
  reviewControl,
}: {
  item: EngineeringPlanItem;
  reviewControl?: {
    checked: boolean;
    disabled: boolean;
    onChange: (checked: boolean) => void;
  };
}) {
  const semantic = semanticReviewValue(item);
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
        {reviewControl && (
          <label className="engineering-plan-section-acknowledgement">
            <input
              type="checkbox"
              checked={reviewControl.checked}
              disabled={reviewControl.disabled}
              onChange={(event) =>
                reviewControl.onChange(event.currentTarget.checked)
              }
            />
            <span>이 항목을 읽었으며 이 값이 실행 설계로 확정됨을 확인합니다.</span>
          </label>
        )}
        {semantic ? (
          <div>
            <strong>{semanticReviewLabel(semantic.path)}</strong>
            {semantic.path.startsWith("design/ladderRungs/") &&
            semantic.path.endsWith("/condition") ? (
              <LadderConditionView
                condition={semantic.value as EngineeringPlanLadderCondition}
              />
            ) : semantic.path.startsWith("design/ladderRungs/") &&
              semantic.path.endsWith("/action") ? (
              <p>
                <code>
                  {ladderActionLabel(
                    semantic.value as EngineeringPlanLadderAction,
                  )}
                </code>
              </p>
            ) : (
              <pre>{JSON.stringify(semantic.value, null, 2)}</pre>
            )}
            <details>
              <summary>canonical path/value</summary>
              <code>{semantic.path}</code>
              <pre>{item.text}</pre>
            </details>
          </div>
        ) : (
          <p>{item.text}</p>
        )}
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
      <div className="engineering-plan-item__details">
        {item.rationale && (
          <p className="engineering-plan-rationale">
            <span>설계 사유</span>
            {item.rationale}
          </p>
        )}
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
      </div>
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
      <div className="engineering-plan-item__details">
        {decision.rationale && (
          <p className="engineering-plan-rationale">
            <span>차단 사유</span>
            {decision.rationale}
          </p>
        )}
        {decision.verificationMethod && (
          <p className="engineering-plan-verification">
            <span>확인 방법</span>
            {decision.verificationMethod}
          </p>
        )}
      </div>
    </li>
  );
}

export function NewProjectPlanReviewPanel({
  artifact,
  planDigest,
  review,
  revision,
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
    review?.schemaVersion === "2" && review.planDigest === planDigest
      ? review.reviewedSectionIds
      : [],
  );
  const expectedItemIds = useMemo(
    () => projectPlanReviewItemIds(artifact),
    [artifact],
  );
  const [acknowledgedItemIds, setAcknowledgedItemIds] = useState<string[]>(
    review?.schemaVersion === "2" && review.planDigest === planDigest
      ? review.reviewedItemIds
      : [],
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
      review?.schemaVersion === "2" && review.planDigest === planDigest
        ? review.reviewedSectionIds
        : [],
    );
    setAcknowledgedItemIds(
      review?.schemaVersion === "2" && review.planDigest === planDigest
        ? review.reviewedItemIds
        : [],
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
  const hasExplicitCollectionScope = Boolean(
    artifact.design.collectionScope,
  );
  const hasExplicitTechnicalConfiguration = Boolean(
    artifact.technicalConfiguration,
  );
  const ladderRungs = artifact.design.ladderRungs ?? [];
  const hasExplicitLadderRungs = ladderRungs.length > 0;
  const hasReviewableAuthority =
    hasExplicitCollectionScope &&
    hasExplicitTechnicalConfiguration &&
    hasExplicitLadderRungs;
  const hasExactReviewUniverse = Boolean(expectedItemIds);
  const legacyReview = Boolean(review && review.schemaVersion !== "2");
  const staleAuthorityReview = Boolean(
    review?.schemaVersion === "2" && review.authorityCurrent !== true,
  );
  const hasEverySectionAcknowledged =
    acknowledgedSectionIds.length === sectionIds.length &&
    sectionIds.every(
      (sectionId, index) => acknowledgedSectionIds[index] === sectionId,
    );
  const hasEveryItemAcknowledged = Boolean(
    expectedItemIds &&
      expectedItemIds.length === acknowledgedItemIds.length &&
      expectedItemIds.every(
        (itemId, index) => acknowledgedItemIds[index] === itemId,
      ),
  );
  const currentReview =
    hasReviewableAuthority &&
    expectedItemIds &&
    review?.schemaVersion === "2" &&
    review.authorityCurrent === true &&
    review.planDigest === planDigest &&
    typeof review.projectBriefDigest === "string" &&
    /^[0-9a-f]{64}$/.test(review.projectBriefDigest) &&
    typeof review.attachmentRevisionDigest === "string" &&
    /^[0-9a-f]{64}$/.test(review.attachmentRevisionDigest) &&
    sameOrderedValues(review.reviewedSectionIds, sectionIds) &&
    sameOrderedValues(review.reviewedItemIds, expectedItemIds)
      ? review
      : undefined;
  const staleReview = Boolean(
    review && hasReviewableAuthority && !currentReview,
  );
  const canReview =
    artifact.status === "ready_for_review" &&
    hasReviewableAuthority &&
    hasExactReviewUniverse &&
    !legacyReview &&
    !staleAuthorityReview &&
    artifact.completeness.blocking === 0 &&
    blockingDecisions.length === 0 &&
    hasEverySectionAcknowledged &&
    hasEveryItemAcknowledged;

  const sectionReviewLockReason =
    artifact.status !== "ready_for_review"
      ? "미확인 사항을 먼저 해결해야 합니다."
      : !hasReviewableAuthority
        ? "이전 계획은 읽기만 가능합니다. 명시적 XG5000 기술 구성, collectionScope, ladderRungs로 다시 계획해 주세요."
      : artifact.completeness.blocking > 0 ||
          blockingDecisions.length > 0
        ? "검토를 차단하는 결정 사항을 먼저 해결해야 합니다."
        : !hasEverySectionAcknowledged
          ? "모든 계획 섹션을 하나씩 확인하고 검토 표시해 주세요."
        : "";

  const reviewLockReason = staleAuthorityReview
    ? "프로젝트 브리프 또는 첨부 소스 revision이 변경되어 기존 검토가 stale 상태입니다. 현재 근거로 재계획해 주세요."
    : !hasExactReviewUniverse
    ? "이전 계획에는 전 실행 관계를 포함한 검토 항목이 없습니다. 새 계획으로 다시 계획해 주세요."
    : legacyReview
    ? "이전 섹션 전용 검토는 실행 권한이 아닙니다. 새 계획으로 다시 계획한 뒤 모든 항목을 검토해 주세요."
    : hasEverySectionAcknowledged && !hasEveryItemAcknowledged
      ? "모든 계획 항목과 실행 관계의 실제 값을 하나씩 확인해 주세요."
      : sectionReviewLockReason;

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

  const setItemAcknowledged = (itemId: string, acknowledged: boolean) => {
    setAcknowledgedItemIds((current) => {
      if (acknowledged) {
        return (
          expectedItemIds?.filter(
            (candidate) => candidate === itemId || current.includes(candidate),
          ) ?? current
        );
      }
      return current.filter((candidate) => candidate !== itemId);
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

      {revision && (
        <section
          className="engineering-plan-revision"
          aria-labelledby="engineering-plan-revision-title"
        >
          <div className="engineering-plan-revision__heading">
            <div>
              <p className="eyebrow">PLAN REVISION</p>
              <h3 id="engineering-plan-revision-title">
                이전 계획 대비 변경
              </h3>
            </div>
            <dl>
              <div>
                <dt>추가</dt>
                <dd>{revision.addedItems.length}</dd>
              </div>
              <div>
                <dt>삭제</dt>
                <dd>{revision.removedItems.length}</dd>
              </div>
              <div>
                <dt>변경 섹션</dt>
                <dd>{revision.changedSectionIds.length}</dd>
              </div>
            </dl>
          </div>
          {revision.changedFields.length === 0 ? (
            <p className="engineering-plan-revision__unchanged">
              이전 계획과 내용이 같습니다.
            </p>
          ) : (
            <>
              <p className="engineering-plan-revision__fields">
                <span>변경된 내용</span>
                {revision.changedFields
                  .map((field) => REVISION_FIELD_LABELS[field])
                  .join(" · ")}
              </p>
              {revision.changedSectionIds.length > 0 && (
                <p className="engineering-plan-revision__sections">
                  <span>변경된 범위</span>
                  {revision.changedSectionIds
                    .map(
                      (sectionId) =>
                        artifact.sections.find(
                          (section) => section.sectionId === sectionId,
                        )?.title ??
                        (sectionId === "open-decisions"
                          ? "열린 결정"
                          : sectionId),
                    )
                    .join(" · ")}
                </p>
              )}
              {(revision.addedItems.length > 0 ||
                revision.removedItems.length > 0) && (
                <div className="engineering-plan-revision__changes">
                  {revision.addedItems.length > 0 && (
                    <div>
                      <h4>추가된 항목</h4>
                      <ul>
                        {revision.addedItems.map((item) => (
                          <li key={`added-${item.itemId}`}>
                            <span>{item.sectionTitle}</span>
                            {item.text}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {revision.removedItems.length > 0 && (
                    <div>
                      <h4>빠진 항목</h4>
                      <ul>
                        {revision.removedItems.map((item) => (
                          <li key={`removed-${item.itemId}`}>
                            <span>{item.sectionTitle}</span>
                            {item.text}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      )}

      <section
        className="engineering-plan-native-design"
        aria-labelledby="engineering-plan-native-design-title"
      >
        <div className="engineering-plan-native-design__heading">
          <div>
            <p className="eyebrow">XG5000 실행 제안 · 검토 전</p>
            <h3 id="engineering-plan-native-design-title">구조화 제어 설계 제안</h3>
          </div>
          <dl>
            <div><dt>I/O</dt><dd>{artifact.design.ioPoints.length}</dd></div>
            <div><dt>모드</dt><dd>{artifact.design.operatingModes.length}</dd></div>
            <div><dt>네트워크</dt><dd>{artifact.design.controlNetworks.length}</dd></div>
            <div><dt>LD rung</dt><dd>{ladderRungs.length}</dd></div>
            <div><dt>타이머</dt><dd>{artifact.design.timers.length}</dd></div>
            <div><dt>시퀀스</dt><dd>{artifact.design.sequences.length}</dd></div>
            <div><dt>알람</dt><dd>{artifact.design.alarms.length}</dd></div>
            <div><dt>복구</dt><dd>{artifact.design.recoveryRules.length}</dd></div>
            <div><dt>안전 경계</dt><dd>{artifact.design.safetyRules.length}</dd></div>
          </dl>
        </div>
        {artifact.design.ioPoints.length > 0 ? (
          <div className="engineering-plan-native-design__table-wrap">
            <table>
              <thead>
                <tr>
                  <th>심볼</th>
                  <th>주소</th>
                  <th>종류</th>
                  <th>정상 / 안전 상태</th>
                </tr>
              </thead>
              <tbody>
                {artifact.design.ioPoints.map((point) => (
                  <tr key={point.signalId}>
                    <td><strong>{point.symbol}</strong><small>{point.description}</small></td>
                    <td><code>{point.address}</code></td>
                    <td>{point.kind}</td>
                    <td>{point.normalState} / {point.safeState}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="engineering-plan-empty">
            실제 주소가 확정되지 않아 적용 가능한 I/O 설계가 없습니다.
          </p>
        )}
        {ladderRungs.length > 0 ? (
          <div className="engineering-plan-native-design__table-wrap">
            <table>
              <thead>
                <tr>
                  <th>순서 / rung</th>
                  <th>설명</th>
                  <th>조건 분기 / 극성</th>
                  <th>명령 / typed 대상</th>
                </tr>
              </thead>
              <tbody>
                {ladderRungs.map((rung, position) => (
                  <tr key={`ladder-${rung.rungId}`}>
                    <td>
                      <strong>{position + 1}</strong>
                      <small>{rung.rungId}</small>
                    </td>
                    <td>{rung.description}</td>
                    <td>
                      <LadderConditionView condition={rung.condition} />
                    </td>
                    <td>
                      <code>{ladderActionLabel(rung.action)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="engineering-plan-empty">
            실행할 LD rung IR이 없어 이 계획은 읽기 전용입니다.
          </p>
        )}
        {(artifact.design.timers.length > 0 ||
          artifact.design.sequences.length > 0 ||
          artifact.design.alarms.length > 0 ||
          artifact.design.recoveryRules.length > 0) && (
          <div className="engineering-plan-native-design__table-wrap">
            <table>
              <thead>
                <tr>
                  <th>구성</th>
                  <th>이름</th>
                  <th>주소·대상</th>
                  <th>실행 조건</th>
                </tr>
              </thead>
              <tbody>
                {artifact.design.timers.map((timer) => (
                  <tr key={`timer-${timer.timerId}`}>
                    <td>TON 타이머</td>
                    <td><strong>{timer.name}</strong><small>{timer.timerId}</small></td>
                    <td><code>{timer.address}</code><small>{timer.presetTicks} × {timer.timeBaseMs} ms = {timer.durationMs} ms</small></td>
                    <td>{timer.enableAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}</td>
                  </tr>
                ))}
                {artifact.design.sequences.map((sequence) => (
                  <tr key={`sequence-${sequence.sequenceId}`}>
                    <td>STEP 시퀀스</td>
                    <td>
                      <strong>{sequence.name}</strong>
                      <small>{sequence.sequenceId}</small>
                      <small>
                        {sequence.restartPolicy === "require_start_release"
                          ? "재기동: START 해제 필요"
                          : "재기동: 연속 반복"}
                      </small>
                    </td>
                    <td>
                      {sequence.steps.map((step) => (
                        <span key={step.stepId}>
                          <code>{step.address}</code>
                          <small>{step.name}</small>
                        </span>
                      ))}
                    </td>
                    <td>
                      <div>시작 {sequence.startAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}</div>
                      {sequence.steps.map((step) => (
                        <div key={`${step.stepId}-complete`}>
                          {step.name} 완료 {step.completeAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}
                        </div>
                      ))}
                      <div>복귀 {sequence.resetAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}</div>
                    </td>
                  </tr>
                ))}
                {artifact.design.alarms.map((alarm) => (
                  <tr key={`alarm-${alarm.alarmId}`}>
                    <td>래치 알람</td>
                    <td><strong>{alarm.name}</strong><small>{alarm.alarmId}</small></td>
                    <td><code>{alarm.latchSignalId}</code><small>{alarm.affectedOutputSignalIds.join(", ")}</small></td>
                    <td>{alarm.triggerAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}</td>
                  </tr>
                ))}
                {artifact.design.recoveryRules.map((rule) => (
                  <tr key={`recovery-${rule.recoveryId}`}>
                    <td>복구</td>
                    <td><strong>{rule.name}</strong><small>{rule.recoveryId}</small></td>
                    <td>{rule.resetAlarmIds.join(", ")} · {rule.resetSequenceIds.join(", ")}</td>
                    <td>{rule.whenAll.map((item) => `${item.signalId}=${item.state}`).join(" ∧ ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section
        className="engineering-plan-native-design"
        aria-labelledby="engineering-plan-execution-review-title"
      >
        <div className="engineering-plan-native-design__heading">
          <div>
            <p className="eyebrow">HUMAN EXECUTION AUTHORITY</p>
            <h3 id="engineering-plan-execution-review-title">
              확정 실행 설계 · 전 항목 검토
            </h3>
          </div>
          <p>
            아래 제안의 모든 값과 관계를 개별 확인해야만 실행 설계로 확정됩니다.
            자동 확인되는 항목은 없습니다.
          </p>
        </div>
        {artifact.sections.map((section) => (
          <section key={`review-${section.sectionId}`}>
            <h4>{section.title}</h4>
            {section.items.length > 0 ? (
              <ul className="engineering-plan-item-list">
                {section.items.map((item) => (
                  <PlanItemRow
                    key={`review-${item.itemId}`}
                    item={item}
                    reviewControl={{
                      checked: acknowledgedItemIds.includes(item.itemId),
                      disabled:
                        busy ||
                        Boolean(currentReview) ||
                        legacyReview ||
                        !hasExactReviewUniverse,
                      onChange: (checked) =>
                        setItemAcknowledged(item.itemId, checked),
                    }}
                  />
                ))}
              </ul>
            ) : (
              <p className="engineering-plan-empty">
                이 섹션에는 검토할 항목이 없습니다. 섹션 범위는 아래에서 별도로 확인합니다.
              </p>
            )}
          </section>
        ))}
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
              {!currentReview && hasReviewableAuthority && (
                <label className="engineering-plan-section-acknowledgement">
                  <input
                    type="checkbox"
                    checked={acknowledgedSectionIds.includes(
                      selectedSection.sectionId,
                    )}
                    disabled={busy || legacyReview || !hasExactReviewUniverse}
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
      {!hasReviewableAuthority && (
        <p className="engineering-plan-stale-review" role="alert">
          이 계획은 검토된 XG5000 기술 구성, 명시적 collectionScope 또는 실행할
          ladderRungs가 없는 이전 형식이므로 읽기 전용입니다. 기술 구성·적용
          범위·LD rung을 포함해 다시 계획한 뒤 새 버전을 검토해 주세요.
        </p>
      )}

      <footer className="engineering-plan-actions">
        <button
          className="button secondary-button"
          type="button"
          disabled={busy}
          onClick={onRequestRevision}
        >
          {hasReviewableAuthority
            ? "수정 요청"
            : "기술 구성·범위·LD rung 포함 재계획 요청"}
        </button>

        {currentReview ? (
          <p className="engineering-plan-reviewed" role="status">
            <strong>확정 실행 설계 검토 완료</strong>
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
                  expectedItemIds?.filter((itemId) =>
                    acknowledgedItemIds.includes(itemId),
                  ) ?? [],
                )
              }
            >
              {busy ? "처리 중…" : "제안 전체를 확정 실행 설계로 검토 완료"}
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
