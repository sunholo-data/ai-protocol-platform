import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReplyReadyNotification } from "@/hooks/useReplyReadyNotification";

const showNotification = vi.fn(async () => {});

function setVisibility(v: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: v });
}

describe("useReplyReadyNotification", () => {
  beforeEach(() => {
    showNotification.mockClear();
    Object.defineProperty(window, "Notification", {
      configurable: true,
      writable: true,
      value: Object.assign(function Notification() {}, { permission: "granted" }),
    });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { getRegistration: async () => ({ showNotification }) },
    });
  });
  afterEach(() => {
    setVisibility("visible");
    delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  });

  const run = (props: { isLoading: boolean; hasError: boolean }) =>
    renderHook((p) => useReplyReadyNotification({ skillName: "Cloud costs", sessionId: "s1", ...p }), {
      initialProps: props,
    });

  it("notifies when a turn finishes in a hidden tab — skill name only, no reply text", async () => {
    setVisibility("hidden");
    const { rerender } = run({ isLoading: true, hasError: false });
    rerender({ isLoading: false, hasError: false });
    await waitFor(() => expect(showNotification).toHaveBeenCalledTimes(1));
    const [title, opts] = showNotification.mock.calls[0] as unknown as [string, NotificationOptions];
    expect(title).toBe("Reply ready");
    expect(opts.body).toBe("in Cloud costs");
    expect(opts.tag).toBe("s1");
  });

  it("stays quiet when the tab is visible", async () => {
    setVisibility("visible");
    const { rerender } = run({ isLoading: true, hasError: false });
    rerender({ isLoading: false, hasError: false });
    await new Promise((r) => setTimeout(r, 20));
    expect(showNotification).not.toHaveBeenCalled();
  });

  it("stays quiet when the turn ended in an error", async () => {
    setVisibility("hidden");
    const { rerender } = run({ isLoading: true, hasError: false });
    rerender({ isLoading: false, hasError: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(showNotification).not.toHaveBeenCalled();
  });
});
