import { describe, expect, it, vi } from "vitest";
import {
  notifySessionsChanged,
  subscribeSessionsChanged,
  subscribeSessionsChangedDetailed,
} from "@/lib/sessionEvents";

describe("sessionEvents bus", () => {
  it("subscribeSessionsChanged fires the handler on every notify (no payload required)", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeSessionsChanged(handler);

    notifySessionsChanged();
    notifySessionsChanged();
    notifySessionsChanged({ deletedSessionId: "x" });

    expect(handler).toHaveBeenCalledTimes(3);
    unsubscribe();
  });

  it("subscribeSessionsChangedDetailed exposes the deletedSessionId payload to consumers", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeSessionsChangedDetailed(handler);

    notifySessionsChanged({ deletedSessionId: "session-X" });
    expect(handler).toHaveBeenCalledWith({ deletedSessionId: "session-X" });

    notifySessionsChanged();
    expect(handler).toHaveBeenLastCalledWith({});

    unsubscribe();
  });

  it("unsubscribe removes the listener so future notifies don't fire", () => {
    const handler = vi.fn();
    const unsubscribe = subscribeSessionsChanged(handler);
    notifySessionsChanged();
    expect(handler).toHaveBeenCalledTimes(1);

    unsubscribe();
    notifySessionsChanged();
    expect(handler).toHaveBeenCalledTimes(1); // still 1 — unsubscribed
  });
});
