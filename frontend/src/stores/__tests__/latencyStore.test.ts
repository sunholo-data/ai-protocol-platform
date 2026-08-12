import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  clearMarks,
  getLatencyMarks,
  recordFirstEvent,
  recordFirstStageLabel,
  recordFirstTextChunk,
  recordServerReport,
  startMark,
  subscribeLatencyStore,
} from "../latencyStore";

beforeEach(() => clearMarks());
afterEach(() => clearMarks());

describe("latencyStore", () => {
  it("starts empty", () => {
    expect(getLatencyMarks()).toEqual([]);
  });

  it("startMark appends a new mark", () => {
    startMark("session-a", "msg-1", 100);
    const marks = getLatencyMarks();
    expect(marks).toHaveLength(1);
    expect(marks[0]).toMatchObject({
      id: "msg-1",
      sessionId: "session-a",
      tSend: 100,
      tFirstEvent: null,
      tFirstStageLabel: null,
      tFirstTextChunk: null,
      serverReport: null,
    });
  });

  it("recordFirstEvent / recordFirstStageLabel / recordFirstTextChunk patch the latest mark", () => {
    startMark("session-a", "msg-1", 100);
    recordFirstEvent(150);
    recordFirstStageLabel(160);
    recordFirstTextChunk(450);

    const m = getLatencyMarks()[0];
    expect(m.tFirstEvent).toBe(150);
    expect(m.tFirstStageLabel).toBe(160);
    expect(m.tFirstTextChunk).toBe(450);
  });

  it("first-observation wins — second recordFirstTextChunk does not overwrite", () => {
    startMark("session-a", "msg-1", 100);
    recordFirstTextChunk(450);
    recordFirstTextChunk(500);
    expect(getLatencyMarks()[0].tFirstTextChunk).toBe(450);
  });

  it("recordServerReport stores the LATENCY_REPORT payload", () => {
    startMark("session-a", "msg-1", 100);
    recordServerReport({ first_model_token_ms: 487, model_used: "gemini-2.5-flash" });
    expect(getLatencyMarks()[0].serverReport).toMatchObject({
      first_model_token_ms: 487,
      model_used: "gemini-2.5-flash",
    });
  });

  it("evicts marks from prior sessions on session change", () => {
    startMark("session-a", "msg-1", 100);
    startMark("session-a", "msg-2", 200);
    expect(getLatencyMarks()).toHaveLength(2);

    // Switching session purges the old marks — HUD always shows the
    // active conversation only.
    startMark("session-b", "msg-3", 300);
    const marks = getLatencyMarks();
    expect(marks).toHaveLength(1);
    expect(marks[0].sessionId).toBe("session-b");
  });

  it("caps the buffer at MAX_MARKS (20) inside the same session", () => {
    for (let i = 0; i < 25; i++) {
      startMark("session-a", `msg-${i}`, i);
    }
    const marks = getLatencyMarks();
    expect(marks.length).toBeLessThanOrEqual(20);
    // Latest is preserved.
    expect(marks[marks.length - 1].id).toBe("msg-24");
  });

  it("subscribers fire on every mutation", () => {
    let callCount = 0;
    const unsub = subscribeLatencyStore(() => {
      callCount++;
    });
    startMark("session-a", "msg-1", 100);
    recordFirstEvent(150);
    recordFirstTextChunk(400);
    unsub();
    // 3 mutations → at least 3 emits.
    expect(callCount).toBeGreaterThanOrEqual(3);
  });

  it("clearMarks empties the buffer and resets active session", () => {
    startMark("session-a", "msg-1", 100);
    clearMarks();
    expect(getLatencyMarks()).toEqual([]);
    // After clear, the next session id is treated fresh (no eviction
    // surprise).
    startMark("session-a", "msg-2", 200);
    expect(getLatencyMarks()).toHaveLength(1);
  });
});
