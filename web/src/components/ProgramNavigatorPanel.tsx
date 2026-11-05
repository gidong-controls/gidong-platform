export interface ProgramNavigatorItem {
  programId: string;
  name: string;
  language?: string;
  kind?: string;
}

interface ProgramNavigatorPanelProps {
  programs: ProgramNavigatorItem[];
  loading: boolean;
  focusAvailable: boolean;
  focusUnavailableReason?: string;
  focusBusyProgramId?: string;
  notice?: string;
  onRefresh: () => void;
  onFocus: (programId: string) => void;
}

function programMetadata(program: ProgramNavigatorItem): string[] {
  return [program.language, program.kind].filter(
    (value): value is string => Boolean(value?.trim()),
  );
}

export function ProgramNavigatorPanel({
  programs,
  loading,
  focusAvailable,
  focusUnavailableReason,
  focusBusyProgramId,
  notice,
  onRefresh,
  onFocus,
}: ProgramNavigatorPanelProps) {
  const focusBusy = Boolean(focusBusyProgramId);
  const unavailable = loading || focusBusy;
  const hasPrograms = programs.length > 0;
  const hasLoadError = !loading && !hasPrograms && Boolean(notice?.trim());

  return (
    <section
      className="program-navigator"
      aria-labelledby="program-navigator-title"
      aria-busy={loading || undefined}
    >
      <header className="program-navigator__header">
        <div>
          <h2 id="program-navigator-title">XG5000 프로그램</h2>
          <p>
            {hasPrograms
              ? `${programs.length}개 · 프로그램 목록`
              : "프로그램 목록"}
          </p>
        </div>
        <button
          type="button"
          className="secondary-action"
          disabled={unavailable}
          aria-label="XG5000 프로그램 목록 새로 고침"
          onClick={onRefresh}
        >
          {loading ? "불러오는 중…" : "새로 고침"}
        </button>
      </header>

      <p className="program-navigator__description">
        래더는 XG5000에서 확인합니다. 여기서는 열 위치만 선택합니다.
      </p>

      {!focusAvailable && (
        <p className="program-navigator__focus-limit">
          {focusUnavailableReason?.trim() ||
            "XG5000 위치 열기는 실제 환경 검증 전 비활성입니다."}
        </p>
      )}

      {loading && !hasPrograms && (
        <p className="program-navigator__state" role="status" aria-live="polite">
          XG5000에서 프로그램 목록을 불러오고 있습니다…
        </p>
      )}

      {hasLoadError && (
        <div className="program-navigator__state" role="alert">
          <strong>프로그램 목록을 불러오지 못했습니다.</strong>
          <p>{notice}</p>
          <button type="button" disabled={unavailable} onClick={onRefresh}>
            다시 시도
          </button>
        </div>
      )}

      {!loading && !hasPrograms && !hasLoadError && (
        <div className="program-navigator__state">
          <strong>표시할 프로그램이 없습니다.</strong>
          <p>
            XG5000에 프로젝트가 열려 있는지 확인한 뒤 목록을 새로 고치세요.
          </p>
        </div>
      )}

      {notice?.trim() && hasPrograms && (
        <p className="program-navigator__notice" role="status" aria-live="polite">
          {notice}
        </p>
      )}

      {hasPrograms && (
        <ul
          className="program-navigator__list"
          aria-label="XG5000 프로그램 목록"
        >
          {programs.map((program) => {
            const isOpening = focusBusyProgramId === program.programId;
            const metadata = programMetadata(program);
            return (
              <li key={program.programId} className="program-navigator__item">
                <div>
                  <strong>{program.name}</strong>
                  {metadata.length > 0 && <small>{metadata.join(" · ")}</small>}
                </div>
                <button
                  type="button"
                  disabled={unavailable || !focusAvailable}
                  aria-busy={isOpening || undefined}
                  aria-label={`${program.name} XG5000에서 열기`}
                  onClick={() => onFocus(program.programId)}
                >
                  {isOpening ? "여는 중…" : "XG5000에서 열기"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
