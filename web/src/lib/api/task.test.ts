import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchTaskOverview,
  subscribeTaskEvents,
  type TaskEventSource,
} from "@/lib/api/task";

/** An EventSource stand-in: the test fires the events the server would. */
class FakeSource implements TaskEventSource {
  readonly url: string;
  readonly listeners = new Map<
    string,
    (event: MessageEvent) => void
  >();
  onopen: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(
    kind: string,
    listener: (event: MessageEvent) => void,
  ): void {
    this.listeners.set(kind, listener);
  }

  close(): void {
    this.closed = true;
  }

  emit(kind: string, data: string): void {
    this.listeners.get(kind)?.({ data } as MessageEvent);
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the task change stream", () => {
  it("listens to the task events endpoint and forwards revisions", () => {
    const captured: { source: FakeSource | null } = { source: null };
    const revs: number[] = [];
    let opened = 0;
    const close = subscribeTaskEvents(
      {
        onEvent: (rev) => revs.push(rev),
        onOpen: () => {
          opened += 1;
        },
      },
      (url) => {
        captured.source = new FakeSource(url);
        return captured.source;
      },
    );
    const source = captured.source;
    if (source === null) throw new Error("the stream did not open a source");

    expect(source.url).toBe("/api/v1/task/events");
    source.onopen?.();
    expect(opened).toBe(1);
    source.emit("task", JSON.stringify({ rev: 3 }));
    expect(revs).toEqual([3]);
    // A malformed or non-numeric event is not an invalidation.
    source.emit("task", "not json");
    source.emit("task", JSON.stringify({ rev: "3" }));
    expect(revs).toEqual([3]);

    close();
    expect(source.closed).toBe(true);
  });
});

describe("the task API paths", () => {
  it("reads the overview from the versioned task route", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ view: "overview", rev: 2 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchTaskOverview();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/task?view=overview",
    );
  });
});
