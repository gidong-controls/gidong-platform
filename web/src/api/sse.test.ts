import { describe, expect, it, vi } from "vitest";
import { ControlStudioEventStream } from "./sse";
import type { HostEventEnvelope } from "./types";

const ENVELOPE: HostEventEnvelope = {
  eventId: "event-5",
  sequence: 5,
  occurredAt: "2026-07-28T10:00:00Z",
  conversationId: "conversation-1",
  eventType: "message.appended",
  payloadSchemaVersion: "1",
  payload: { messageId: "message-1" },
};

function frame(event: HostEventEnvelope): Uint8Array {
  return new TextEncoder().encode(
    `id: ${event.eventId}\ndata: ${JSON.stringify(event)}\n\n`,
  );
}

function streamingResponse(
  init: RequestInit | undefined,
  chunks: Uint8Array[],
  close = false,
): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      if (close) {
        controller.close();
        return;
      }
      init?.signal?.addEventListener(
        "abort",
        () =>
          controller.error(
            new DOMException("The operation was aborted.", "AbortError"),
          ),
        { once: true },
      );
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("ControlStudioEventStream", () => {
  it("streams authenticated SSE frames and drops duplicate sequences", async () => {
    const events: HostEventEnvelope[] = [];
    const states: string[] = [];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      expect(url.pathname).toBe("/api/v1/events");
      expect(url.searchParams.get("conversation_id")).toBe("conversation-1");
      expect(url.searchParams.get("after_sequence")).toBe("4");
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe("Bearer browser-session");
      expect(headers.get("Last-Event-ID")).toBe("4");
      return streamingResponse(init, [frame(ENVELOPE), frame(ENVELOPE)]);
    });

    const stream = new ControlStudioEventStream(
      "/api/v1/events?conversation_id=conversation-1",
      (event) => events.push(event),
      (state) => states.push(state),
      {
        fetch: fetchMock,
        getAuthorizationHeader: () => "Bearer browser-session",
      },
    );

    stream.start(4);
    await vi.waitFor(() => expect(events).toEqual([ENVELOPE]));
    expect(states).toContain("connected");
    expect(fetchMock).toHaveBeenCalledOnce();

    stream.stop();
    expect(states.at(-1)).toBe("disconnected");
  });

  it("reconnects from the last accepted sequence with bearer headers", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (_input, init) => {
      if (fetchMock.mock.calls.length === 1) {
        return streamingResponse(init, [frame(ENVELOPE)], true);
      }
      return streamingResponse(init, []);
    });
    const stream = new ControlStudioEventStream(
      "/api/v1/events?conversation_id=conversation-1",
      () => undefined,
      () => undefined,
      {
        baseDelayMs: 1,
        maxDelayMs: 1,
        random: () => 0.5,
        fetch: fetchMock,
        getAuthorizationHeader: () => "Bearer browser-session",
      },
    );

    stream.start(4);
    await vi.waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));

    const [input, init] = fetchMock.mock.calls[1]!;
    const url = new URL(String(input));
    expect(url.searchParams.get("after_sequence")).toBe("5");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer browser-session",
    );
    expect(new Headers(init?.headers).get("Last-Event-ID")).toBe("5");

    stream.stop();
  });

  it("invalidates the session and does not reconnect after a 401", async () => {
    const onUnauthorized = vi.fn();
    const states: string[] = [];
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 401 }));
    const stream = new ControlStudioEventStream(
      "/api/v1/events",
      () => undefined,
      (state) => states.push(state),
      {
        baseDelayMs: 1,
        maxDelayMs: 1,
        fetch: fetchMock,
        getAuthorizationHeader: () => "Bearer expired-session",
        onUnauthorized,
      },
    );

    stream.start();
    await vi.waitFor(() => expect(onUnauthorized).toHaveBeenCalledOnce());
    await new Promise((resolve) => setTimeout(resolve, 5));

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(states.at(-1)).toBe("disconnected");
    stream.stop(false);
  });
});
