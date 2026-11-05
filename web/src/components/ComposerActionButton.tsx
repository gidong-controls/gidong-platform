import { Icon } from "./Icon";

interface ComposerActionButtonProps {
  running: boolean;
  cancelBusy: boolean;
  sendDisabled: boolean;
  writeRequest: boolean;
  onSend: () => void;
  onCancel: () => void;
}

export function ComposerActionButton({
  running, cancelBusy, sendDisabled, writeRequest, onSend, onCancel,
}: ComposerActionButtonProps) {
  const label = running
    ? cancelBusy ? "중단 요청 중" : "작업 중단 요청"
    : "메시지 보내기";
  return (
    <button
      className="send-button"
      type="button"
      disabled={running ? cancelBusy : sendDisabled}
      aria-label={label}
      aria-busy={running && cancelBusy || undefined}
      title={label}
      onClick={running ? onCancel : onSend}
    >
      {running ? <Icon name="stop" size={18} />
        : writeRequest ? <span>보내기</span> : <Icon name="send" size={18} />}
    </button>
  );
}
