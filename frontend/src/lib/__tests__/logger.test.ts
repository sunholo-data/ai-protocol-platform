// lib/logger.ts — FRONTEND-OBS M3
//
// The client half of the observability path. What matters here is not that
// events arrive — it is that the logger can NEVER degrade the app it observes.
// A telemetry client that throws, loops, or grows unbounded is worse than no
// telemetry at all, so most of these tests are about restraint under failure.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createLogger,
  MAX_BUFFERED_EVENTS,
  BATCH_SIZE,
  FLUSH_INTERVAL_MS,
  FLUSH_BYTE_BUDGET,
} from "../logger";

const fetchWithAuth = vi.hoisted(() => vi.fn());
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth }));

function okResponse() {
  return Promise.resolve({ ok: true, status: 202 } as Response);
}

/** Parse the events array out of the Nth POST. */
function postedEvents(call: number) {
  const init = fetchWithAuth.mock.calls[call][1] as RequestInit;
  return JSON.parse(init.body as string).events as Array<Record<string, unknown>>;
}

beforeEach(() => {
  vi.useFakeTimers();
  fetchWithAuth.mockReset();
  fetchWithAuth.mockImplementation(okResponse);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("batching", () => {
  it("does not POST before the batch is full", () => {
    const log = createLogger({ enabled: true });
    for (let i = 0; i < BATCH_SIZE - 1; i++) log.event(`e${i}`);
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("POSTs once the batch fills, with every event in one request", async () => {
    const log = createLogger({ enabled: true });
    for (let i = 0; i < BATCH_SIZE; i++) log.event(`e${i}`);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    expect(postedEvents(0)).toHaveLength(BATCH_SIZE);
  });

  it("flushes a partial batch on the interval timer", async () => {
    const log = createLogger({ enabled: true });
    log.event("lonely");
    expect(fetchWithAuth).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    expect(postedEvents(0)).toHaveLength(1);
  });

  it("POSTs to the ingest route through fetchWithAuth, never a bare fetch", async () => {
    const log = createLogger({ enabled: true });
    log.event("x");
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    expect(fetchWithAuth.mock.calls[0][0]).toBe("/api/proxy/api/client-logs");
    expect((fetchWithAuth.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });
});

describe("unload flush", () => {
  it("uses keepalive on visibilitychange, because sendBeacon cannot carry the Bearer", async () => {
    const log = createLogger({ enabled: true });
    log.event("before-hide");
    log.handleVisibilityChange("hidden");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    expect((fetchWithAuth.mock.calls[0][1] as RequestInit).keepalive).toBe(true);
  });

  it("does not POST on hide when there is nothing buffered", () => {
    const log = createLogger({ enabled: true });
    log.handleVisibilityChange("hidden");
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });
});

describe("back-pressure", () => {
  it("caps the buffer and reports the drop rather than growing without bound", async () => {
    const log = createLogger({ enabled: true });
    // Never let a flush drain the buffer while we overfill it.
    fetchWithAuth.mockImplementation(() => new Promise(() => {}));
    for (let i = 0; i < MAX_BUFFERED_EVENTS + 50; i++) log.event(`e${i}`);
    expect(log.bufferedCount()).toBeLessThanOrEqual(MAX_BUFFERED_EVENTS);
    expect(log.droppedCount()).toBeGreaterThan(0);
  });

  it("drops the OLDEST events, keeping the ones nearest the failure", async () => {
    const log = createLogger({ enabled: true });
    fetchWithAuth.mockImplementation(() => new Promise(() => {}));
    for (let i = 0; i < MAX_BUFFERED_EVENTS + 10; i++) log.event(`e${i}`);
    const bodies = log.peek().map((e) => e.body);
    expect(bodies).not.toContain("e0");
    expect(bodies).toContain(`e${MAX_BUFFERED_EVENTS + 9}`);
  });
});

describe("never degrades the app", () => {
  it("does not throw when the ingest route rejects", async () => {
    const log = createLogger({ enabled: true });
    fetchWithAuth.mockRejectedValue(new Error("network down"));
    log.event("x");
    await expect(vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS)).resolves.not.toThrow();
  });

  it("does not throw when the ingest route 500s", async () => {
    const log = createLogger({ enabled: true });
    fetchWithAuth.mockResolvedValue({ ok: false, status: 500 } as Response);
    log.event("x");
    await expect(vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS)).resolves.not.toThrow();
  });

  it("backs off after repeated failures instead of retrying every interval", async () => {
    const log = createLogger({ enabled: true });
    fetchWithAuth.mockRejectedValue(new Error("down"));
    log.event("a");
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const afterFirst = fetchWithAuth.mock.calls.length;
    // Several more intervals pass; a naive implementation would POST on each.
    log.event("b");
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS * 3);
    expect(fetchWithAuth.mock.calls.length).toBeLessThan(afterFirst + 3);
  });
});

describe("kill switch", () => {
  it("never POSTs when disabled", async () => {
    const log = createLogger({ enabled: false });
    for (let i = 0; i < BATCH_SIZE * 2; i++) log.event(`e${i}`);
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS * 2);
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("still buffers nothing when disabled, so it cannot leak memory", () => {
    const log = createLogger({ enabled: false });
    for (let i = 0; i < 100; i++) log.event(`e${i}`);
    expect(log.bufferedCount()).toBe(0);
  });
});

describe("event shape", () => {
  it("names fields after the OTel LogRecord so Phase 3 is a transport swap", async () => {
    const log = createLogger({ enabled: true });
    log.event("stream_run_failed", { durationMs: 12 });
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const [e] = postedEvents(0);
    expect(e).toHaveProperty("timestamp");
    expect(e).toHaveProperty("severityText");
    expect(e).toHaveProperty("body", "stream_run_failed");
    expect((e.attributes as Record<string, unknown>).durationMs).toBe(12);
  });

  it("attaches the ambient context so client events join backend turns", async () => {
    const log = createLogger({ enabled: true });
    log.setContext({ threadId: "t-1", sessionId: "s-1" });
    log.event("x");
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const attrs = postedEvents(0)[0].attributes as Record<string, unknown>;
    expect(attrs.threadId).toBe("t-1");
    expect(attrs.sessionId).toBe("s-1");
  });

  it("records an Error's name, message and stack under ERROR severity", async () => {
    const log = createLogger({ enabled: true });
    log.error(new TypeError("cannot read x"), { route: "/chat" });
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const [e] = postedEvents(0);
    expect(e.severityText).toBe("ERROR");
    const attrs = e.attributes as Record<string, unknown>;
    expect(attrs.errorName).toBe("TypeError");
    expect(attrs.errorMessage).toBe("cannot read x");
    expect(attrs.stack).toBeTruthy();
    expect(attrs.route).toBe("/chat");
  });

  it("marks warn as WARN and event as INFO", async () => {
    const log = createLogger({ enabled: true });
    log.warn("stream_truncated_no_terminal");
    log.event("surface_registered");
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const events = postedEvents(0);
    expect(events[0].severityText).toBe("WARN");
    expect(events[1].severityText).toBe("INFO");
  });

  it("truncates an oversized stack rather than letting the batch be rejected", async () => {
    const log = createLogger({ enabled: true });
    const err = new Error("big");
    err.stack = "x".repeat(20_000);
    log.error(err);
    await vi.advanceTimersByTimeAsync(FLUSH_INTERVAL_MS);
    const attrs = postedEvents(0)[0].attributes as Record<string, unknown>;
    expect((attrs.stack as string).length).toBeLessThanOrEqual(8_192);
  });
});

describe("byte budget", () => {
  // Browsers cap keepalive request bodies at 64KB in flight; the backend body
  // cap is generous, but the unload flush is the one that must never exceed
  // what the browser will actually send. Errors carry up to 8KB stacks, so a
  // batch of 20 can be ~250KB — far past the limit — and would be dropped
  // whole at the worst possible moment. Flush early on bytes, not just count.

  function bigError(i: number) {
    const err = new Error(`e${i}`);
    err.stack = "x".repeat(7_000);
    return err;
  }

  it("flushes before the batch fills once the buffered bytes pass the budget", async () => {
    const log = createLogger({ enabled: true });
    // ~7KB each; the budget is well under 20 of these.
    for (let i = 0; i < BATCH_SIZE - 1; i++) log.error(bigError(i));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchWithAuth).toHaveBeenCalled();
    for (const call of fetchWithAuth.mock.calls) {
      const body = (call[1] as RequestInit).body as string;
      expect(body.length).toBeLessThanOrEqual(FLUSH_BYTE_BUDGET + 8_192 + 1_024);
    }
  });

  it("every keepalive flush on hide stays under the browser's 64KB in-flight cap", async () => {
    const log = createLogger({ enabled: true });
    for (let i = 0; i < 12; i++) log.error(bigError(i));
    log.handleVisibilityChange("hidden");
    await vi.advanceTimersByTimeAsync(0);
    const keepalive = fetchWithAuth.mock.calls.filter((c) => (c[1] as RequestInit).keepalive);
    expect(keepalive.length).toBeGreaterThan(0);
    for (const call of keepalive) {
      expect(((call[1] as RequestInit).body as string).length).toBeLessThan(64 * 1024);
    }
  });

  it("small events still batch to BATCH_SIZE — the budget only bites on big ones", async () => {
    const log = createLogger({ enabled: true });
    for (let i = 0; i < BATCH_SIZE; i++) log.event(`e${i}`);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    expect(postedEvents(0)).toHaveLength(BATCH_SIZE);
  });
});
