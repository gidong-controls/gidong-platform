export type StreamingDraft = Readonly<{
  runId: string;
  text: string;
}>;

export function appendStreamingDraft(
  current: StreamingDraft | null,
  runId: string | null | undefined,
  delta: string | undefined,
): StreamingDraft | null {
  if (!runId || !delta) {
    return current;
  }
  if (!current || current.runId !== runId) {
    return { runId, text: delta };
  }
  return { runId, text: current.text + delta };
}

export function clearStreamingDraftForRun(
  current: StreamingDraft | null,
  runId: string | null | undefined,
): StreamingDraft | null {
  return current?.runId === runId ? null : current;
}

export function visibleStreamingDraftText(
  current: StreamingDraft | null,
  activeRunId: string | null,
  activeRunIsLive: boolean,
): string {
  return current?.runId === activeRunId && activeRunIsLive
    ? current.text
    : "";
}
