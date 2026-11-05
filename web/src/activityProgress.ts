export interface CoalescibleActivity {
  id: string;
  coalesceKey?: string;
}

export function isHistoricalRunReplay(
  event: { sequence: number; eventType: string }, snapshotSequence: number,
): boolean {
  return event.sequence <= snapshotSequence && (
    event.eventType === "run.created" || event.eventType === "run.state" ||
    event.eventType === "run.state_changed" || event.eventType === "agent.token" ||
    event.eventType === "agent.progress"
  );
}

export function formatReportedElapsedSeconds(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return null;
  }
  if (value < 1) {
    return null;
  }
  if (value >= 60) {
    const seconds = Math.floor(value);
    return `${Math.floor(seconds / 60)}분${seconds % 60 ? ` ${seconds % 60}초` : ""}`;
  }
  return `${Math.floor(value)}초`;
}

export function activityDetail(
  progress: boolean,
  label: string | undefined,
  previous: string | undefined,
  current: string,
): string {
  return progress ? current : label ?? previous ?? current;
}

export function agentProgressDetail(
  _runId: string,
  elapsedSeconds: unknown,
): string {
  const elapsed = formatReportedElapsedSeconds(elapsedSeconds);
  return elapsed ? `전체 작업 ${elapsed} 경과` : "";
}

export function prependCoalescedActivity<T extends CoalescibleActivity>(
  current: readonly T[],
  next: T,
): T[] {
  return [
    next,
    ...current.filter(
      (item) =>
        item.id !== next.id &&
        (!next.coalesceKey || item.coalesceKey !== next.coalesceKey),
    ),
  ];
}

interface ToolActivity {
  runId?: string;
  sequence: number;
  toolState?: string;
  resultSummary?: string;
}

export function latestCompletedToolResult(
  activities: readonly ToolActivity[],
  runId: string | null,
): string | undefined {
  if (!runId) return undefined;
  const completed = activities.filter(
    (activity) => activity.runId === runId && activity.toolState === "completed" && activity.resultSummary,
  );
  const latest = completed.reduce<ToolActivity | undefined>(
    (result, activity) => !result || activity.sequence > result.sequence ? activity : result,
    undefined,
  );
  return latest?.resultSummary;
}
