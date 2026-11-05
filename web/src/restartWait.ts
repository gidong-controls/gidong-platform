// Match LauncherSettings.startup_timeout_seconds (180), not the old 45s UI wait.
// The Host replacement does not re-enter Launcher or inherit its local overrides.
export const HOST_RESTART_WAIT_MS = 180_000;
const PROBE_TIMEOUT_MS = 1_000; // LauncherSettings.probe_timeout_seconds.
const POLL_INTERVAL_MS = 300;

export class RestartWaitError extends Error {
  constructor(readonly reason: "timeout" | "invalid_receipt") {
    super(reason === "timeout" ? "Host restart readiness timed out." : "Host restart receipt mismatch.");
    this.name = "RestartWaitError";
  }
}

class RestartProbeTimeout extends Error {}

function aborted(): DOMException {
  return new DOMException("Restart observation cancelled.", "AbortError");
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(aborted()); return; }
    const cancel = () => { clearTimeout(timer); reject(aborted()); };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, ms);
    signal.addEventListener("abort", cancel, { once: true });
  });
}

async function probe(
  getStatus: (signal: AbortSignal) => Promise<unknown>,
  signal: AbortSignal,
  timeoutMs: number,
): Promise<unknown> {
  if (signal.aborted) throw aborted();
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = () => {};
  const stopped = new Promise<never>((_, reject) => {
    cancel = () => { reject(aborted()); controller.abort(); };
    signal.addEventListener("abort", cancel, { once: true });
    timer = setTimeout(() => {
      reject(new RestartProbeTimeout());
      controller.abort();
    }, timeoutMs);
  });
  try {
    // Bound even an adapter that fails to settle promptly after abort.
    return await Promise.race([getStatus(controller.signal), stopped]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
    controller.abort();
  }
}

export async function waitForRestartReady(options: {
  receipt: string;
  deadline: number;
  signal: AbortSignal;
  getStatus: (signal: AbortSignal) => Promise<unknown>;
  isTransientError: (error: unknown) => boolean;
  now?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}): Promise<void> {
  const now = options.now ?? (() => performance.now());
  const sleep = options.sleep ?? pause;
  if (options.receipt.length < 32 || options.receipt.length > 512 || !Number.isFinite(options.deadline)) {
    throw new RestartWaitError("invalid_receipt");
  }
  while (now() < options.deadline) {
    if (options.signal.aborted) throw aborted();
    try {
      const result = await probe(options.getStatus, options.signal,
        Math.min(PROBE_TIMEOUT_MS, options.deadline - now()));
      if (options.signal.aborted) throw aborted();
      if (now() >= options.deadline) throw new RestartWaitError("timeout");
      if (typeof result !== "object" || result === null ||
          !("restartReceipt" in result) || result.restartReceipt !== options.receipt ||
          !("status" in result) || (result.status !== "scheduled" && result.status !== "ready")) {
        throw new RestartWaitError("invalid_receipt");
      }
      if (result.status === "ready") return;
    } catch (error) {
      if (options.signal.aborted) throw aborted();
      if (!(error instanceof RestartProbeTimeout) && !options.isTransientError(error)) throw error;
    }
    const remaining = options.deadline - now();
    if (remaining > 0) await sleep(Math.min(POLL_INTERVAL_MS, remaining), options.signal);
  }
  throw new RestartWaitError("timeout");
}
