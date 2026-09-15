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
  onOpenTechnicalSetup?: () => void;
  technicalSetupDisabled?: boolean;
  technicalSetupTitle?: string;
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
  const expectedExactValues = new Map<string, unknown>();
  artifact.requirementSources?.forEach((source) => {
    if (!source.messageId) return;
    const path = `requirement/message/${escape(source.messageId)}/${escape(source.field)}`;
    expected.push(path);
    expectedExactValues.set(path, {
      field: source.field,
      messageId: source.messageId,
      quote: source.quote,
    });
  });
  const bindings = artifact.design.indexAcceptanceBindings;
  if (bindings) {
    Object.keys(bindings.signals).forEach((role) =>
      expected.push(`design/indexAcceptanceBindings/signals/${escape(role)}`),
    );
    Object.keys(bindings.modes).forEach((role) =>
      expected.push(`design/indexAcceptanceBindings/modes/${escape(role)}`),
    );
    Object.keys(bindings.alarms).forEach((role) =>
      expected.push(`design/indexAcceptanceBindings/alarms/${escape(role)}`),
    );
    expected.push("design/indexAcceptanceBindings/timer");
  }
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
  expectedLadderValues.forEach((value, path) => expectedExactValues.set(path, value));
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
        item.itemId.startsWith("requirement/") ||
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
        const expectedValue = expectedExactValues.get(item.itemId);
        return (
          parsed.path !== item.itemId ||
          (expectedExactValues.has(item.itemId)
            ? sectionId !==
                (item.itemId.startsWith("requirement/")
                  ? "user-requirements"
                  : "native-ladder-rungs") ||
              canonicalJson(parsed.value) !== canonicalJson(expectedValue)
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
    !item.itemId.startsWith("requirement/") &&
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
  if (path.startsWith("requirement/message/")) return "사용자 요구 근거";
  if (path === "technical/projectName") return "XG5000 프로젝트 이름";
  if (path === "technical/plcSeries") return "PLC 시리즈";
  if (path === "technical/cpuModel") return "CPU 모델";
  if (path === "technical/programName") return "첫 프로그램 이름";
  if (path === "technical/language") return "프로그램 언어";
  if (path === "technical/description") return "XG5000 프로젝트 설명";
  if (path === "scope/ladderRungs") return "래더 실행 순서와 식별 정보";
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/position")) {
    return "래더 실행 순서";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/description")) {
    return "래더 동작 설명";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/condition")) {
    return "래더 조건·분기·접점 종류";
  }
  if (path.startsWith("design/ladderRungs/") && path.endsWith("/action")) {
    return "래더 명령·대상";
  }
  if (path.startsWith("design/indexAcceptanceBindings/")) {
    return "시뮬레이션 역할 연결";
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
  if (action.instruction === "INCP") {
    return `횟수 +1 · 조건이 켜질 때 한 번 · ${action.destinationAddress}`;
  }
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
  technicalConfiguration: "프로젝트 설정",
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

export function ReviewedPlanActions({
  review,
  onOpenTechnicalSetup,
  technicalSetupDisabled,
  technicalSetupTitle,
}: {
  review: EngineeringPlanReview;
  onOpenTechnicalSetup?: () => void;
  technicalSetupDisabled: boolean;
  technicalSetupTitle?: string;
}) {
  return (
    <div className="engineering-plan-reviewed-actions">
      <p className="engineering-plan-reviewed" role="status">
        <strong>설계 검토 완료</strong>
        <span>
          <time dateTime={review.reviewedAt}>
            {formatReviewedAt(review.reviewedAt)}
          </time>
        </span>
      </p>
      {onOpenTechnicalSetup && (
        <button
          className="button primary-button"
          type="button"
          disabled={technicalSetupDisabled}
          title={technicalSetupTitle}
          onClick={onOpenTechnicalSetup}
        >
          다음 단계 · 프로젝트 설정 확인
        </button>
      )}
    </div>
  );
}

const PLAN_VALUE_LABELS: Record<string, string> = {
  name: "이름", address: "주소", signalId: "신호", sourceId: "조건 대상",
  sourceType: "조건 종류", targetId: "동작 대상", targetType: "대상 종류",
  type: "종류", kind: "종류", state: "상태", direction: "입출력 방향",
  dataType: "데이터 형식", normalState: "정상 상태", safeState: "안전 상태",
  status: "확인 상태", reason: "이유", evidence: "참고 내용", unit: "단위",
  duration: "시간", durationMs: "시간(ms)", preset: "설정값", timeBaseMs: "시간 단위(ms)",
  timerId: "타이머", modeId: "운전 모드", sequenceId: "시퀀스", stepId: "단계",
  alarmId: "알람", outputId: "출력", ioPointId: "입출력 신호", value: "값",
  enabled: "사용", required: "필수", classification: "분류", action: "동작",
  condition: "조건", terms: "조건 목록", restartPolicy: "재시작 방식",
  from: "이전 단계", to: "다음 단계", source: "출처", members: "포함 항목",
  includedIds: "포함 항목", excludedIds: "제외 항목", rationale: "설계 이유",
  plcSeries: "PLC 종류", cpuModel: "CPU", language: "프로그램 언어",
};

const COLLECTION_SCOPE_LABELS: Record<string, string> = {
  ioPoints: "입출력 신호",
  operatingModes: "실행 운전 모드",
  controlNetworks: "제어 네트워크",
  ladderRungs: "래더 실행 순서",
  timers: "타이머",
  sequences: "시퀀스",
  alarms: "알람",
  recoveryRules: "복구 규칙",
  safetyRules: "안전 규칙",
};

function collectionScopeSummary(path: string, value: unknown) {
  const collection = path.startsWith("scope/") ? path.slice("scope/".length) : "";
  const label = COLLECTION_SCOPE_LABELS[collection];
  if (!label || value === null || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const status = record.status;
  const count = record.count;
  if (
    (status !== "confirmed" && status !== "not_applicable") ||
    typeof count !== "number" ||
    !Number.isSafeInteger(count) ||
    count < 0
  ) {
    return undefined;
  }
  return {
    label,
    statusLabel: status === "confirmed" ? "확인됨" : "해당 없음",
    countLabel: `${count}개 항목`,
  };
}

const SIMULATION_SIGNAL_ROLE_LABELS: Record<string, string> = {
  automatic_start: "자동 시작 버튼",
  manual_start: "수동 동작 버튼",
  stop: "정지 입력",
  reset: "복구·리셋 버튼",
  select_automatic: "자동 모드 선택",
  select_manual: "수동 모드 선택",
  emergency_stop: "비상정지 입력",
  door_open: "도어 열림 입력",
  ready: "준비 완료 입력",
  move_complete: "이동 완료 입력",
  motion: "이동 명령 출력",
  automatic_mode: "자동 모드 상태",
  manual_mode: "수동 모드 상태",
  running: "운전 상태",
  moving: "이동 중 상태",
  any_alarm: "알람 종합 상태",
};

const SIMULATION_MODE_ROLE_LABELS: Record<string, string> = {
  automatic: "자동 운전 모드",
  manual: "수동 운전 모드",
};

const SIMULATION_ALARM_ROLE_LABELS: Record<string, string> = {
  emergency: "비상정지 알람",
  door: "도어 열림 알람",
  timeout: "이동 시간초과 알람",
};

const REQUIREMENT_FIELD_LABELS: Record<string, string> = {
  sequence: "동작 순서",
  hardware_and_io: "하드웨어·입출력",
  safety_and_interlocks: "안전·인터록",
  operating_modes: "운전 모드",
  performance_and_acceptance: "성능·인수 조건",
};

function requirementSourceSummary(path: string, value: unknown) {
  if (
    !path.startsWith("requirement/message/") ||
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.field !== "string" || typeof record.quote !== "string") {
    return undefined;
  }
  return {
    fieldLabel: REQUIREMENT_FIELD_LABELS[record.field] ?? "사용자 요구",
    quote: record.quote,
  };
}

function simulationBindingSummary(path: string, value: unknown) {
  if (
    !path.startsWith("design/indexAcceptanceBindings/") ||
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    return undefined;
  }
  const record = value as Record<string, unknown>;
  const role = typeof record.role === "string" ? record.role : "";
  if (path.includes("/signals/")) {
    const name = typeof record.signalName === "string" ? record.signalName : "연결 신호";
    const symbol = typeof record.signalSymbol === "string" ? record.signalSymbol : "";
    return {
      roleLabel: SIMULATION_SIGNAL_ROLE_LABELS[role] ?? "시험 신호",
      valueLabel: symbol && symbol !== name ? `${name} (${symbol})` : name,
    };
  }
  if (path.includes("/modes/")) {
    return {
      roleLabel: SIMULATION_MODE_ROLE_LABELS[role] ?? "시험 운전 모드",
      valueLabel: typeof record.modeName === "string" ? record.modeName : "연결 모드",
    };
  }
  if (path.includes("/alarms/")) {
    return {
      roleLabel: SIMULATION_ALARM_ROLE_LABELS[role] ?? "시험 알람",
      valueLabel: typeof record.alarmName === "string" ? record.alarmName : "연결 알람",
    };
  }
  if (path.endsWith("/timer")) {
    return {
      roleLabel: "5초 시간초과 타이머",
      valueLabel: typeof record.timerName === "string" ? record.timerName : "연결 타이머",
    };
  }
  return undefined;
}

function SemanticPlanValueView({ semantic }: { semantic: SemanticReviewValue }) {
  const requirementSource = requirementSourceSummary(
    semantic.path,
    semantic.value,
  );
  if (requirementSource) {
    return (
      <p className="engineering-plan-requirement-source-summary">
        <strong>{requirementSource.fieldLabel}</strong>
        <span>{requirementSource.quote}</span>
      </p>
    );
  }
  const scopeSummary = collectionScopeSummary(semantic.path, semantic.value);
  if (scopeSummary) {
    return (
      <p className="engineering-plan-collection-scope-summary">
        <strong>{scopeSummary.label}</strong>
        <span>{scopeSummary.statusLabel}</span>
        <span>{scopeSummary.countLabel}</span>
      </p>
    );
  }
  const bindingSummary = simulationBindingSummary(semantic.path, semantic.value);
  if (bindingSummary) {
    return (
      <p className="engineering-plan-simulation-binding-summary">
        <strong>{bindingSummary.roleLabel}</strong>
        <span>{bindingSummary.valueLabel}</span>
      </p>
    );
  }
  return <PlanValueView value={semantic.value} />;
}

function PlanValueView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>없음</span>;
  if (typeof value === "boolean") return <span>{value ? "예" : "아니요"}</span>;
  if (Array.isArray(value)) return value.length
    ? <ul>{value.map((entry, index) => <li key={index}><PlanValueView value={entry} /></li>)}</ul>
    : <span>없음</span>;
  if (typeof value === "object") return (
    <dl className="plan-readable-value">
      {Object.entries(value).map(([key, entry]) => <div key={key}>
        <dt>{PLAN_VALUE_LABELS[key] ?? key}</dt><dd><PlanValueView value={entry} /></dd>
      </div>)}
    </dl>
  );
  return <span>{String(value)}</span>;
}

function PlanRevisionItemView({
  item,
}: {
  item: EngineeringPlanRevision["addedItems"][number];
}) {
  let semantic: SemanticReviewValue | undefined;
  try {
    const parsed = JSON.parse(item.text) as Partial<SemanticReviewValue>;
    if (typeof parsed.path === "string" && "value" in parsed) {
      semantic = parsed as SemanticReviewValue;
    }
  } catch {
    semantic = undefined;
  }
  return (
    <li>
      <span>{item.sectionTitle}</span>
      {semantic ? (
        <div>
          <strong>{semanticReviewLabel(semantic.path)}</strong>
          <SemanticPlanValueView semantic={semantic} />
          <details>
            <summary>기술 기록 보기</summary>
            <code>{semantic.path}</code>
            <pre>{item.text}</pre>
          </details>
        </div>
      ) : (
        <p>{item.text}</p>
      )}
    </li>
  );
}

export function PlanItemRow({
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
            <span>이 내용대로 진행</span>
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
              <SemanticPlanValueView semantic={semantic} />
            )}
            <details>
              <summary>기술 기록 보기</summary>
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
      <details className="engineering-plan-item__details">
        <summary>설계 근거와 확인 방법</summary>
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
      </details>
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
  onOpenTechnicalSetup,
  technicalSetupDisabled = false,
  technicalSetupTitle,
}: NewProjectPlanReviewPanelProps) {
  const overviewSectionId =
    artifact.sections.find((section) => section.title === "설계 제안")?.sectionId ??
    artifact.sections[0]?.sectionId ??
    "";
  const [selectedSectionId, setSelectedSectionId] = useState(
    overviewSectionId,
  );
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const expectedItemIds = useMemo(
    () => projectPlanReviewItemIds(artifact),
    [artifact],
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
    setReviewConfirmed(false);
    setSelectedSectionId(overviewSectionId);
  }, [overviewSectionId, planDigest]);

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
  const missingSimulationProfileReviewRows = Boolean(
    artifact.design.indexAcceptanceBindings &&
      !artifact.sections.some(
        (section) =>
          section.sectionId === "native-simulation-profile" &&
          section.items.some((item) =>
            item.itemId.startsWith("design/indexAcceptanceBindings/"),
          ),
      ),
  );
  const legacyReview = Boolean(review && review.schemaVersion !== "2");
  const staleAuthorityReview = Boolean(
    review?.schemaVersion === "2" && review.authorityCurrent !== true,
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
    reviewConfirmed;

  const sectionReviewLockReason =
    artifact.status !== "ready_for_review"
      ? "미확인 사항을 먼저 해결해야 합니다."
      : !hasReviewableAuthority
        ? "프로젝트 설정과 실행할 프로그램 설계가 부족합니다. 대화에서 계획 보완을 요청해 주세요."
      : artifact.completeness.blocking > 0 ||
          blockingDecisions.length > 0
        ? "검토를 차단하는 결정 사항을 먼저 해결해야 합니다."
        : !reviewConfirmed
          ? "핵심 설계와 필요한 상세 내용을 확인한 뒤 검토 확인란을 선택해 주세요."
        : "";

  const reviewLockReason = staleAuthorityReview
    ? "요구사항이나 첨부 자료가 바뀌었습니다. 변경된 내용으로 계획을 다시 작성하고 검토해 주세요."
    : missingSimulationProfileReviewRows
    ? "이 계획에는 시뮬레이션 신호 연결 검토 항목이 없습니다. 연결 내용을 포함해 계획을 다시 발행한 뒤 검토해 주세요."
    : !hasExactReviewUniverse
    ? "이전 계획에는 전 실행 관계를 포함한 검토 항목이 없습니다. 새 계획으로 다시 계획해 주세요."
    : legacyReview
    ? "이전 섹션 전용 검토는 실행 권한이 아닙니다. 새 계획으로 다시 계획한 뒤 내용을 검토해 주세요."
    : sectionReviewLockReason;

  return (
    <section
      className="engineering-plan-review"
      aria-labelledby="engineering-plan-title"
    >
      <header className="engineering-plan-header">
        <div>
          <p className="eyebrow">새 프로젝트 계획</p>
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
        </div>
      </header>

      <details className="engineering-plan-coverage">
        <summary>계획 항목 현황</summary>
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
      </details>

      <section
        className="engineering-plan-objective"
        aria-labelledby="engineering-plan-objective-title"
      >
        <h3 id="engineering-plan-objective-title">프로젝트 목표</h3>
        <p>{artifact.objective}</p>
      </section>

      {revision && (
        <details className="engineering-plan-revision">
          <summary>
            이전 계획에서 바뀐 내용 · 추가 {revision.addedItems.length} · 제외{" "}
            {revision.removedItems.length}
          </summary>
          <section aria-labelledby="engineering-plan-revision-title">
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
                          <PlanRevisionItemView
                            key={`added-${item.itemId}`}
                            item={item}
                          />
                        ))}
                      </ul>
                    </div>
                  )}
                  {revision.removedItems.length > 0 && (
                    <div>
                      <h4>빠진 항목</h4>
                      <ul>
                        {revision.removedItems.map((item) => (
                          <PlanRevisionItemView
                            key={`removed-${item.itemId}`}
                            item={item}
                          />
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
          </section>
        </details>
      )}

      {(artifact.design.ioPoints.length > 0 || ladderRungs.length > 0) && <details
        className="engineering-plan-native-design"
        aria-labelledby="engineering-plan-native-design-title"
      >
        <summary>프로그램 설계 상세</summary>
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
                  <th>명령 / 대상</th>
                </tr>
              </thead>
              <tbody>
                {ladderRungs.map((rung, position) => (
                  <tr key={`ladder-${rung.rungId}`}>
                    <td>
                      <strong>{position + 1}</strong>
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
            실행할 래더 프로그램이 아직 설계되지 않아 이 계획은 읽기만 가능합니다.
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
      </details>}

      {artifact.openDecisions.length > 0 && (
        <section
          className="engineering-plan-decisions"
          aria-labelledby="engineering-plan-decisions-title"
        >
          <div className="engineering-plan-decisions__heading">
            <div>
              <h3 id="engineering-plan-decisions-title">확인할 질문</h3>
            </div>
            {blockingDecisions.length > 0 && (
              <strong role="status">
                답변 필요 {blockingDecisions.length}건
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

      <details
        className="engineering-plan-technical-details"
        data-testid="project-plan-technical-details"
      >
        <summary>기술 세부 정보</summary>
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
          {currentReview && (
            <div>
              <dt>검토자 식별 정보</dt>
              <dd><code>{currentReview.actorId}</code></dd>
            </div>
          )}
        </dl>
      </details>

      {staleReview && (
        <p className="engineering-plan-stale-review" role="alert">
          계획이 변경되어 이전 검토 완료 기록은 더 이상 유효하지 않습니다.
        </p>
      )}
      {!hasReviewableAuthority && artifact.status === "ready_for_review" && (
        <p className="engineering-plan-stale-review" role="alert">
          프로그램 생성에 필요한 설계 내용이 부족합니다. 대화에서 계획을 보완해 주세요.
        </p>
      )}

      <footer className="engineering-plan-actions">
        <button
          className="button secondary-button"
          type="button"
          disabled={busy}
          onClick={onRequestRevision}
        >
          대화에서 계획 보완
        </button>

        {currentReview ? (
          <ReviewedPlanActions
            review={currentReview}
            onOpenTechnicalSetup={onOpenTechnicalSetup}
            technicalSetupDisabled={technicalSetupDisabled}
            technicalSetupTitle={technicalSetupTitle}
          />
        ) : hasReviewableAuthority ? (
          <div className="engineering-plan-review-action">
            <label className="engineering-plan-section-acknowledgement">
              <input
                type="checkbox"
                checked={reviewConfirmed}
                disabled={busy || legacyReview || !hasExactReviewUniverse}
                onChange={(event) =>
                  setReviewConfirmed(event.currentTarget.checked)
                }
              />
              <span>핵심 설계와 필요한 상세 내용을 확인했습니다</span>
            </label>
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
                  sectionIds,
                  expectedItemIds ?? [],
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
        ) : null}
      </footer>
    </section>
  );
}
