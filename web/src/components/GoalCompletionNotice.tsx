import type { ConversationCompletion } from "../api/types";

export interface GoalCompletionNoticeProps {
  completion: ConversationCompletion;
  runActive?: boolean;
}

interface GoalCompletionCopy {
  label: string;
  detail: string;
}

function completionCopy(
  completion: ConversationCompletion,
): GoalCompletionCopy | null {
  switch (completion.status) {
    case "phase_complete":
      return {
        label: "계획 단계 완료",
        detail:
          "전체 목표 완료가 아닙니다. 프로젝트 생성·저장·재열기·검증이 남아 있습니다.",
      };
    case "goal_complete":
      return {
        label: "전체 목표 완료",
        detail: "Host가 최종 목표의 완료 근거를 확인했습니다.",
      };
    case "blocked":
      return {
        label: "목표 진행 중단",
        detail: "완료 근거가 없어 전체 목표 완료로 표시하지 않습니다.",
      };
    case "not_claimed":
      return null;
  }
}

/**
 * Render the Host-owned end-to-end goal state without treating a planning
 * artifact or a completed Agent turn as the user's completed goal.
 */
export function GoalCompletionNotice({
  completion,
  runActive = false,
}: GoalCompletionNoticeProps) {
  if (runActive) return null;
  const copy = completionCopy(completion);
  if (!copy) return null;

  return (
    <aside
      className={`goal-completion-notice goal-completion-notice--${completion.status}`}
      aria-label="전체 목표 상태"
      aria-live="polite"
      data-goal-completion-status={completion.status}
    >
      <strong>{copy.label}</strong>
      <span>{copy.detail}</span>
    </aside>
  );
}
