export interface CoalescibleActivity {
  id: string;
  coalesceKey?: string;
}

export function formatReportedElapsedSeconds(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return null;
  }
  if (value < 1) {
    return `${Math.round(value * 1_000)}ms`;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)}초`;
}

export function agentProgressDetail(
  runId: string,
  elapsedSeconds: unknown,
): string {
  const elapsed = formatReportedElapsedSeconds(elapsedSeconds);
  return elapsed ? `${runId} · ${elapsed} 경과` : runId;
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
