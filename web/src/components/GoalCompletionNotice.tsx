import type { ConversationCompletion } from "../api/types";

export interface GoalCompletionNoticeProps {
  completion: ConversationCompletion;
  runActive?: boolean;
}

interface GoalCompletionCopy {
  label: string;
}

function completionCopy(
  completion: ConversationCompletion,
): GoalCompletionCopy | null {
  switch (completion.status) {
    case "phase_complete":
      return {
        label: "계획 단계 완료",
      };
    case "request_complete":
      return { label: "요청한 작업 완료" };
    case "goal_complete":
      if (completion.basis === "native_project_goal_assessed") {
        return { label: "프로젝트 목표 완료" };
      }
      return {
        label: "전체 목표 완료",
      };
    case "blocked":
      return {
        label: "작업 중단",
      };
    case "not_claimed":
      if (completion.basis === "native_request_needs_user") {
        return { label: "필요한 정보 대기" };
      }
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
      aria-label={completion.status === "goal_complete" ? "전체 목표 상태" : "현재 요청 상태"}
      aria-live="polite"
      data-goal-completion-status={completion.status}
    >
      <strong>{copy.label}</strong>
    </aside>
  );
}
