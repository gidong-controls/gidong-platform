import type { ConversationSummary } from "../api/types";
import { Icon } from "./Icon";

export interface ConversationHistoryDialogProps {
  open: boolean;
  items: readonly ConversationSummary[];
  activeConversationId: string | null;
  loading: boolean;
  selectingConversationId: string | null;
  notice: string;
  onClose: () => void;
  onSelect: (conversationId: string) => void;
}

const MISSION_STATUS_LABEL = {
  active: "미완료",
  waiting_for_user: "사용자 입력 대기",
  waiting_for_approval: "승인 대기",
  completed: "완료",
  blocked: "진행 중단",
} as const;

function updatedLabel(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime())
    ? value
    : new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(timestamp);
}

export function ConversationHistoryDialog({
  open,
  items,
  activeConversationId,
  loading,
  selectingConversationId,
  notice,
  onClose,
  onSelect,
}: ConversationHistoryDialogProps) {
  if (!open) return null;

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="conversation-history-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="conversation-history-title"
      >
        <header>
          <div>
            <h2 id="conversation-history-title">작업 기록</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="작업 기록 닫기"
            onClick={onClose}
          >
            <Icon name="close" size={18} />
          </button>
        </header>

        <div className="conversation-history-dialog__body">
          <p>
            이전 대화와 작업 결과를 다시 볼 수 있습니다. 다른 프로젝트의
            기록은 읽기 전용으로 열립니다.
          </p>
          {loading ? (
            <div className="conversation-history-empty">작업 기록을 불러오는 중…</div>
          ) : items.length === 0 ? (
            <div className="conversation-history-empty">저장된 작업이 없습니다.</div>
          ) : (
            <ol className="conversation-history-list">
              {items.map((item) => {
                const current = item.conversationId === activeConversationId;
                const missionStatus = item.engineeringMission?.status;
                return (
                  <li key={item.conversationId}>
                    <button
                      type="button"
                      className={current ? "is-current" : undefined}
                      disabled={selectingConversationId !== null}
                      aria-current={current ? "true" : undefined}
                      onClick={() => onSelect(item.conversationId)}
                    >
                      <span className="conversation-history-item__heading">
                        <strong>{item.title || "제목 없는 작업"}</strong>
                        {current && <em>현재 열림</em>}
                      </span>
                      <span>
                        {item.workflow === "engineering" ? "프로젝트 작업" : "새 프로젝트"}
                        {missionStatus
                          ? ` · ${MISSION_STATUS_LABEL[missionStatus]}`
                          : ""}
                      </span>
                      <time dateTime={item.updatedAt}>{updatedLabel(item.updatedAt)}</time>
                      {selectingConversationId === item.conversationId && (
                        <small>기록 여는 중…</small>
                      )}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
          {notice && <p className="conversation-history-notice">{notice}</p>}
        </div>
      </section>
    </div>
  );
}
