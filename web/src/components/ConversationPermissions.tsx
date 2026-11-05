import { useEffect, useRef, useState } from "react";
import type { ConversationPermission } from "../requestPermissions";

export function ConversationPermissions({ scope, busy, canChange, onSelect, allowSimulator, onSimulatorChange }: {
  scope: ConversationPermission;
  allowSimulator: boolean;
  onSimulatorChange: (value: boolean) => void;
  busy: boolean;
  canChange: boolean;
  onSelect: (scope: ConversationPermission) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  return <div className="conversation-permissions" ref={root} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={(event) => {
    if (event.key === "Escape" && open) {
      event.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    }
  }}>
    <button ref={trigger} type="button" aria-expanded={open}
      aria-controls="conversation-permissions-options" onClick={() => setOpen(!open)}>
      {scope === "permanent_offline_revision" ? "권한: 작업 사본 수정" : scope === "analysis_only" ? "권한: 변경 안 함" : "권한: 적용 전 확인"}
    </button>
    {open && <div id="conversation-permissions-options" className="permissions-popover" role="group" aria-label="프로젝트 변경 권한">
      {([
        ["review_before_apply", "적용 전 확인 · 기본", "질문은 답하고, 수정 요청은 변경안을 먼저 보여줍니다."],
        ["analysis_only", "변경하지 않기", "질문에 답하고 프로젝트를 살펴봅니다."],
        ["permanent_offline_revision", "작업 사본 수정 허용", "이 대화의 요청한 변경을 작업 사본에 적용·검사·저장합니다."],
      ] as const).map(([value, title, description]) => <button key={value} type="button"
        aria-pressed={scope === value} disabled={busy || (value === "permanent_offline_revision" && !canChange)}
        onClick={() => { onSelect(value); setOpen(false); trigger.current?.focus(); }}>
        <strong>{title}</strong><span>{description}</span>
      </button>)}
      {scope === "permanent_offline_revision" && <label>
        <input type="checkbox" checked={allowSimulator} disabled={busy || !canChange}
          onChange={(event) => onSimulatorChange(event.target.checked)} />
        XG-SIM 시험 허용
        <span>저장한 작업 사본을 로컬 시뮬레이터에서 시험하고 필요하면 수정·재시험합니다.</span>
      </label>}
      {!canChange && <p>수정하려면 프로젝트의 작업 사본을 연결해 주세요.</p>}
      <p>선택한 권한은 후속 요청에도 유지됩니다. 진행 중인 작업이 끝나면 이 메뉴에서 바꿀 수 있습니다.</p>
    </div>}
  </div>;
}
