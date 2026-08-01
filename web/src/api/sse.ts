import type { HostEventEnvelope } from "./types";

export type EventStreamState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

interface EventStreamOptions {
  baseDelayMs?: number;
  maxDelayMs?: number;
  random?: () => number;
  fetch?: typeof fetch;
  getAuthorizationHeader?: () => string | undefined;
  onUnauthorized?: () => void;
}

export class ControlStudioEventStream {
  private readonly endpoint: string;
  private readonly onEvent: (event: HostEventEnvelope) => void;
  private readonly onState: (state: EventStreamState) => void;
  private readonly options: Required<
    Omit<EventStreamOptions, "getAuthorizationHeader" | "onUnauthorized">
  > &
    Pick<EventStreamOptions, "getAuthorizationHeader" | "onUnauthorized">;
  private abortController?: AbortController;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private reconnectAttempt = 0;
  private connectionEpoch = 0;
  private stopped = true;
  private lastSequence = 0;

  constructor(
    endpoint: string,
    onEvent: (event: HostEventEnvelope) => void,
    onState: (state: EventStreamState) => void,
    options: EventStreamOptions = {},
  ) {
    this.endpoint = endpoint;
    this.onEvent = onEvent;
    this.onState = onState;
    this.options = {
      baseDelayMs: options.baseDelayMs ?? 800,
      maxDelayMs: options.maxDelayMs ?? 15_000,
      random: options.random ?? Math.random,
      fetch: options.fetch ?? window.fetch.bind(window),
      getAuthorizationHeader: options.getAuthorizationHeader,
      onUnauthorized: options.onUnauthorized,
    };
  }

  start(afterSequence = 0): void {
    this.stop(false);
    this.stopped = false;
    this.lastSequence = Math.max(0, afterSequence);
    this.connect("connecting");
  }

  stop(emitState = true): void {
    this.stopped = true;
    this.connectionEpoch += 1;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }
    this.abortController?.abort();
    this.abortController = undefined;
    if (emitState) {
      this.onState("disconnected");
    }
  }

  private connect(state: EventStreamState): void {
    if (this.stopped) {
      return;
    }
    this.onState(state);

    const epoch = ++this.connectionEpoch;
    const controller = new AbortController();
    this.abortController?.abort();
    this.abortController = controller;
    void this.consume(epoch, controller);
  }

  private async consume(
    epoch: number,
    controller: AbortController,
  ): Promise<void> {
    try {
      const url = new URL(this.endpoint, window.location.origin);
      if (this.lastSequence > 0) {
        url.searchParams.set("after_sequence", String(this.lastSequence));
      }

      const headers = new Headers({
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      });
      const authorization = this.options.getAuthorizationHeader?.();
      if (authorization) {
        headers.set("Authorization", authorization);
      }
      if (this.lastSequence > 0) {
        headers.set("Last-Event-ID", String(this.lastSequence));
      }

      const response = await this.options.fetch(url.toString(), {
        method: "GET",
        headers,
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!this.isCurrent(epoch)) {
        return;
      }
      if (response.status === 401) {
        this.stopped = true;
        this.options.onUnauthorized?.();
        this.onState("disconnected");
        return;
      }
      if (!response.ok || !response.body) {
        throw new Error(`event stream failed with status ${response.status}`);
      }

      this.reconnectAttempt = 0;
      this.onState("connected");
      await this.readFrames(response.body, epoch);
      if (this.isCurrent(epoch)) {
        this.scheduleReconnect();
      }
    } catch (error) {
      if (
        this.isCurrent(epoch) &&
        !(error instanceof DOMException && error.name === "AbortError")
      ) {
        this.scheduleReconnect();
      }
    } finally {
      if (this.abortController === controller) {
        this.abortController = undefined;
      }
    }
  }

  private async readFrames(
    body: ReadableStream<Uint8Array>,
    epoch: number,
  ): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (this.isCurrent(epoch)) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        buffer = this.consumeCompleteFrames(buffer);
      }
    } finally {
      reader.releaseLock();
    }
  }

  private consumeCompleteFrames(input: string): string {
    let buffer = input;
    while (true) {
      const match = /\r?\n\r?\n/.exec(buffer);
      if (!match || match.index === undefined) {
        return buffer;
      }
      const frame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      this.acceptFrame(frame);
    }
  }

  private acceptFrame(frame: string): void {
    const data: string[] = [];
    for (const rawLine of frame.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(":")) {
        continue;
      }
      const separator = rawLine.indexOf(":");
      const field = separator >= 0 ? rawLine.slice(0, separator) : rawLine;
      let value = separator >= 0 ? rawLine.slice(separator + 1) : "";
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
      if (field === "data") {
        data.push(value);
      }
    }
    if (data.length === 0) {
      return;
    }

    try {
      const event = normalizeEvent(JSON.parse(data.join("\n")) as unknown);
      if (!event || event.sequence <= this.lastSequence) {
        return;
      }
      this.lastSequence = event.sequence;
      this.onEvent(event);
    } catch {
      // The Host remains authoritative. Invalid frames are ignored and the
      // next reconnect resumes from the last fully validated sequence.
    }
  }

  private isCurrent(epoch: number): boolean {
    return !this.stopped && epoch === this.connectionEpoch;
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) {
      return;
    }
    const exponential = Math.min(
      this.options.maxDelayMs,
      this.options.baseDelayMs * 2 ** this.reconnectAttempt,
    );
    const jitter = 0.85 + this.options.random() * 0.3;
    const delay = Math.round(exponential * jitter);
    this.reconnectAttempt += 1;

    this.onState("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      this.connect("reconnecting");
    }, delay);
  }
}

function normalizeEvent(value: unknown): HostEventEnvelope | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const event = value as Partial<HostEventEnvelope>;
  if (
    typeof event.eventId !== "string" ||
    typeof event.sequence !== "number" ||
    typeof event.occurredAt !== "string" ||
    typeof event.eventType !== "string" ||
    event.payloadSchemaVersion !== "1" ||
    !event.payload ||
    typeof event.payload !== "object"
  ) {
    return null;
  }
  return event as HostEventEnvelope;
}
