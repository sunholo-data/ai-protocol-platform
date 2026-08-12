import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => mockFetch(...args),
}));

import { useSessionActivity } from "../useSessionActivity";

describe("useSessionActivity", () => {
  beforeEach(() => mockFetch.mockReset());

  it("maps backend tool_calls, delegations and session start", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        tool_calls: [
          { id: "c1", name: "ai_search", status: "success", ts: 5, argsJson: "{}", resultContent: "ok" },
          { id: "c2", name: "broken", status: "error", ts: 6 },
        ],
        delegations: [
          { id: "d1", target: "specialist", targetDisplay: "Specialist", mode: "auto", ts: 7 },
        ],
        session_start_ts: 3,
      }),
    });
    const { result } = renderHook(() => useSessionActivity("s1"));
    await waitFor(() => expect(result.current.toolCalls.length).toBe(2));
    expect(result.current.toolCalls[0]).toMatchObject({ id: "c1", name: "ai_search", status: "success", ts: 5 });
    expect(result.current.toolCalls[1].status).toBe("error");
    expect(result.current.toolCalls[1].resultContent).toBeUndefined();
    expect(result.current.delegations).toHaveLength(1);
    expect(result.current.delegations[0]).toMatchObject({ target: "specialist", mode: "auto", ts: 7 });
    expect(result.current.sessionStartTs).toBe(3);
  });

  it("returns [] on error response", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 500 });
    const { result } = renderHook(() => useSessionActivity("s1"));
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(result.current.toolCalls).toEqual([]);
  });

  it("returns [] and does not fetch when there is no session", () => {
    const { result } = renderHook(() => useSessionActivity(null));
    expect(result.current.toolCalls).toEqual([]);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("re-fetches when refetchKey changes (action-run settled → sync persisted history)", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ tool_calls: [], delegations: [], session_start_ts: 1 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tool_calls: [{ id: "extract-1", name: "extract_ppa_clauses", status: "success", ts: 9 }],
          delegations: [],
          session_start_ts: 1,
        }),
      });

    const { result, rerender } = renderHook(
      ({ key }: { key: number }) => useSessionActivity("s1", key),
      { initialProps: { key: 0 } },
    );
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(result.current.toolCalls).toEqual([]);

    rerender({ key: 1 });
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.toolCalls.length).toBe(1));
    expect(result.current.toolCalls[0]).toMatchObject({ id: "extract-1", status: "success" });
  });

  it("does not re-fetch when refetchKey is unchanged across re-renders", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ tool_calls: [], delegations: [], session_start_ts: 1 }),
    });
    const { rerender } = renderHook(
      ({ key }: { key: number }) => useSessionActivity("s1", key),
      { initialProps: { key: 3 } },
    );
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    rerender({ key: 3 });
    rerender({ key: 3 });
    // Still one fetch — same session, same key.
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
