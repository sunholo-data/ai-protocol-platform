import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import { useBackendReady } from "../useBackendReady";

// Patch global fetch so the hook is testable without network I/O.
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useBackendReady", () => {
  it("flips ready=true on the first 200 from /api/proxy/health", async () => {
    fetchMock.mockResolvedValueOnce(new Response("ok", { status: 200 }));
    const { result } = renderHook(() => useBackendReady());
    expect(result.current.ready).toBe(false);
    await waitFor(() => {
      expect(result.current.ready).toBe(true);
    });
    // Confirms the right path was probed
    expect(fetchMock.mock.calls[0][0]).toBe("/api/proxy/health");
  });

  it("retries with backoff on non-200; resolves once a probe succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(new Response("", { status: 502 }))
      .mockResolvedValueOnce(new Response("ok", { status: 200 }));
    const { result } = renderHook(() => useBackendReady());

    // First attempt registered immediately
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });
    expect(result.current.ready).toBe(false);

    // Advance through the backoff sleeps so attempts 2 + 3 fire
    await vi.advanceTimersByTimeAsync(1100);
    await vi.advanceTimersByTimeAsync(1600);

    await waitFor(() => {
      expect(result.current.ready).toBe(true);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("treats network errors the same as 5xx — retries instead of stalling at false", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce(new Response("ok", { status: 200 }));
    const { result } = renderHook(() => useBackendReady());

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });
    expect(result.current.ready).toBe(false);

    await vi.advanceTimersByTimeAsync(1100);
    await waitFor(() => {
      expect(result.current.ready).toBe(true);
    });
  });
});
