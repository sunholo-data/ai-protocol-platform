import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDocumentSessions } from "../useDocumentSessions";

const BASE_SESSION = {
  session_id: "s1",
  document_ids: ["doc-1"],
  skill_id: "skill-1",
  owner_uid: "user-uid",
  access_control: { type: "private" },
  title: "Test session",
  turn_count: 3,
  first_message_at: new Date().toISOString(),
  last_message_at: new Date().toISOString(),
  archived_at: null,
  is_owner: true,
  can_fork: true,
};

function mockFetch(sessions = [BASE_SESSION], ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve({ sessions, next_cursor: null }),
  });
}

describe("useDocumentSessions", () => {
  let savedFetch: typeof global.fetch;

  beforeEach(() => {
    savedFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = savedFetch;
  });

  it("returns sessions on successful fetch", async () => {
    global.fetch = mockFetch() as typeof global.fetch;
    const { result } = renderHook(() => useDocumentSessions("doc-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.sessions).toEqual([BASE_SESSION]);
    expect(result.current.error).toBeNull();
  });

  it("sets error state when HTTP response is not ok", async () => {
    global.fetch = mockFetch([], false) as typeof global.fetch;
    const { result } = renderHook(() => useDocumentSessions("doc-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe("Failed to load chat history");
    expect(result.current.sessions).toEqual([]);
  });

  it("sets error state when fetch rejects", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("Network error")) as typeof global.fetch;
    const { result } = renderHook(() => useDocumentSessions("doc-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe("Failed to load chat history");
  });

  it("aborts pending request on unmount", () => {
    const abortSpy = vi.fn();
    const fakeController = { abort: abortSpy, signal: { aborted: false } };
    vi.spyOn(globalThis, "AbortController").mockImplementation(
      () => fakeController as unknown as AbortController
    );
    // fetch never resolves so the abort fires before any response
    global.fetch = vi.fn().mockReturnValue(new Promise(() => {})) as typeof global.fetch;

    const { unmount } = renderHook(() => useDocumentSessions("doc-1"));
    unmount();
    expect(abortSpy).toHaveBeenCalled();
  });

  it("does not fetch when docId is null", () => {
    global.fetch = vi.fn() as typeof global.fetch;
    renderHook(() => useDocumentSessions(null));
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("refetches when window regains focus", async () => {
    global.fetch = mockFetch() as typeof global.fetch;
    renderHook(() => useDocumentSessions("doc-1"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });

  it("refetches when a sessions-changed event is dispatched (cross-panel sync)", async () => {
    global.fetch = mockFetch() as typeof global.fetch;
    renderHook(() => useDocumentSessions("doc-1"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

    // Another panel (skill-level sidebar) deletes a session and dispatches.
    act(() => {
      window.dispatchEvent(new CustomEvent("aitana:sessions-changed"));
    });

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });
});
