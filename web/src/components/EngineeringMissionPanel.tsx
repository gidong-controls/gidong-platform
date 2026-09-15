import type {
  EngineeringMission,
  EngineeringMissionStatus,
  EngineeringMissionStepStatus,
} from "../api/types";

export interface EngineeringMissionPanelProps {
  mission: EngineeringMission;
  availableEvidenceIds: readonly string[];
  evidenceTitles?: Record<string, string>;
  onOpenEvidence: (evidenceId: string, trigger: HTMLButtonElement) => void;
  resumeAvailable: boolean;
  resumeUnavailableMessage?: string;
  resumeBusy: boolean;
  onResume: () => void;
  reverifyAvailable: boolean;
  reverifyUnavailableMessage?: string;
  reverifyBusy: boolean;
  onReverifyBlockedStep: () => void;
  startNewAvailable: boolean;
  startNewBusy: boolean;
  onStartNew: () => void;
}

const STATUS_LABEL: Record<EngineeringMissionStatus, string> = {
  active: "진행 중",
  waiting_for_user: "사용자 입력 대기",
  waiting_for_approval: "사람 승인 대기",
  completed: "목표 달성",
  blocked: "차단됨",
};

const STEP_STATUS_LABEL: Record<EngineeringMissionStepStatus, string> = {
  pending: "대기",
  active: "수행 중",
  completed: "완료",
  blocked: "차단",
};

export function EngineeringMissionPanel({
  mission,
  availableEvidenceIds,
  evidenceTitles = {},
  onOpenEvidence,
  resumeAvailable,
  resumeUnavailableMessage,
  resumeBusy,
  onResume,
  reverifyAvailable,
  reverifyUnavailableMessage,
  reverifyBusy,
  onReverifyBlockedStep,
  startNewAvailable,
  startNewBusy,
  onStartNew,
}: EngineeringMissionPanelProps) {
  const { snapshot } = mission;
  const completed = snapshot.steps.filter(
    (step) => step.status === "completed",
  ).length;
  const waitingForChangeOutcome = snapshot.status === "waiting_for_approval";
  const canResume =
    (snapshot.status === "active" || waitingForChangeOutcome) &&
    resumeAvailable;

  return (
    <section className="engineering-mission" aria-label="엔지니어링 에이전트 작업 계획">
      <header className="engineering-mission__header">
        <div>
          <p className="eyebrow">작업 계획</p>
          <h3>{snapshot.objective}</h3>
        </div>
        <div className="engineering-mission__status">
          <span className={`mission-status mission-status--${snapshot.status}`}>
            {STATUS_LABEL[snapshot.status]}
          </span>
          <small>
            {completed}/{snapshot.steps.length} 단계 완료
          </small>
        </div>
      </header>

      <ol className="engineering-mission__steps">
        {snapshot.steps.map((step) => (
          <li
            key={step.stepId}
            className={`mission-step mission-step--${step.status}`}
          >
            <span className="mission-step__marker" aria-hidden="true" />
            <div>
              <div className="mission-step__heading">
                <strong>{step.title}</strong>
                <span>{STEP_STATUS_LABEL[step.status]}</span>
              </div>
              {step.outcome && <p>{step.outcome}</p>}
              {step.evidenceRefs.length > 0 && (
                <details>
                  <summary>참고한 자료 {step.evidenceRefs.length}건</summary>
                  <ul>
                    {step.evidenceRefs.map((evidenceRef, index) => (
                      <li key={evidenceRef}>
                        <button
                          type="button"
                          className="mission-evidence-link"
                          disabled={!availableEvidenceIds.includes(evidenceRef)}
                          title={
                            availableEvidenceIds.includes(evidenceRef)
                              ? "이 단계의 근거 열기"
                              : "현재 대화의 근거 목록에 아직 불러오지 못했습니다."
                          }
                          onClick={(event) =>
                            onOpenEvidence(evidenceRef, event.currentTarget)
                          }
                        >
                          <span>{evidenceTitles[evidenceRef] || `참고 자료 ${index + 1}`}</span>
                          <span>
                            {availableEvidenceIds.includes(evidenceRef)
                              ? "근거 열기"
                              : "불러오지 못함"}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        ))}
      </ol>

      {snapshot.blockingQuestions.length > 0 && (
        <div className="engineering-mission__blockers">
          <strong>진행에 필요한 답변</strong>
          <ul>
            {snapshot.blockingQuestions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      )}

      <details className="engineering-mission__criteria">
        <summary>완료 조건 {snapshot.completionCriteria.length}개</summary>
        <ol>
          {snapshot.completionCriteria.map((criterion) => (
            <li key={criterion}>{criterion}</li>
          ))}
        </ol>
      </details>

      <footer className="engineering-mission__footer">
        <div>
          {snapshot.nextAction && (
            <p>
              <strong>다음 행동</strong> {snapshot.nextAction}
            </p>
          )}
          <details>
            <summary>기술 기록</summary>
            <small>기록 버전 {mission.revision} · <code>{mission.digest}</code></small>
          </details>
        </div>
        {(snapshot.status === "active" || waitingForChangeOutcome) && (
          <button
            type="button"
            className="button primary-button"
            data-testid="engineering-mission-resume"
            disabled={!canResume || resumeBusy}
            aria-busy={resumeBusy || undefined}
            title={
              !canResume
                ? resumeUnavailableMessage ||
                  (waitingForChangeOutcome
                    ? "변경이 완료·원복·실패·거절·만료로 확정되면 계속할 수 있습니다."
                    : undefined)
                : undefined
            }
            onClick={onResume}
          >
            {resumeBusy
              ? "작업 재개 중…"
              : waitingForChangeOutcome
                ? canResume
                  ? "결과 확인하고 계속"
                  : "승인·실행·복구 결과 대기"
                : "작업 계속 실행"}
          </button>
        )}
        {snapshot.status === "blocked" && (
          <button
            type="button"
            className="button primary-button"
            data-testid="engineering-mission-reverify"
            disabled={!reverifyAvailable || reverifyBusy}
            aria-busy={reverifyBusy || undefined}
            title={!reverifyAvailable ? reverifyUnavailableMessage : undefined}
            onClick={onReverifyBlockedStep}
          >
            {reverifyBusy ? "차단 단계 준비 중…" : "차단 단계 다시 검증"}
          </button>
        )}
        {(snapshot.status === "completed" || snapshot.status === "blocked") && (
          <button
            type="button"
            className="button secondary-button"
            disabled={!startNewAvailable || startNewBusy}
            aria-busy={startNewBusy || undefined}
            onClick={onStartNew}
          >
            {startNewBusy ? "새 작업 준비 중…" : "새 엔지니어링 작업"}
          </button>
        )}
      </footer>
    </section>
  );
}
