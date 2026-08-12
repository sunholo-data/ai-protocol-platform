import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import { useSessionMessages } from "@/hooks/useSessionMessages";

function mockOk(messages: object[]) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve({ messages, session_id: "sess-1" }),
  } as Response);
}

function mockError(status = 500) {
  mockFetch.mockResolvedValueOnce({ ok: false, status } as Response);
}

function mock404() {
  mockFetch.mockResolvedValueOnce({ ok: false, status: 404 } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useSessionMessages", () => {
  it("does not fetch when sessionId is null", () => {
    renderHook(() => useSessionMessages(null));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns empty messages when sessionId is null", () => {
    const { result } = renderHook(() => useSessionMessages(null));
    expect(result.current.initialMessages).toEqual([]);
    expect(result.current.isLoadingHistory).toBe(false);
  });

  it("fetches the correct endpoint when sessionId is provided", async () => {
    mockOk([]);

    renderHook(() => useSessionMessages("sess-abc"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/proxy/api/sessions/sess-abc/messages",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("returns SkillMessage[] on success", async () => {
    mockOk([
      { role: "user", content: "Hello", timestamp: 1714000000 },
      { role: "assistant", content: "Hi!", timestamp: 1714000001 },
    ]);

    const { result } = renderHook(() => useSessionMessages("sess-1"));

    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.initialMessages).toHaveLength(2);
    expect(result.current.initialMessages[0].role).toBe("user");
    expect(result.current.initialMessages[0].content).toBe("Hello");
    expect(result.current.initialMessages[1].role).toBe("assistant");
    expect(result.current.historyError).toBeNull();
  });

  it("sets historyError on HTTP failure", async () => {
    mockError(500);

    const { result } = renderHook(() => useSessionMessages("sess-1"));

    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.historyError).toContain("starting fresh");
    expect(result.current.initialMessages).toEqual([]);
  });

  it("clears messages when sessionId changes to null", async () => {
    mockOk([{ role: "user", content: "Hi", timestamp: 1 }]);

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionMessages(sid),
      { initialProps: { sid: "sess-1" as string | null } },
    );

    await waitFor(() => expect(result.current.initialMessages).toHaveLength(1));

    rerender({ sid: null });
    expect(result.current.initialMessages).toEqual([]);
  });

  it("stranded-session-prevention (1.23) Option 1: 404 sets sessionGone=true and does NOT set historyError", async () => {
    // The hook must distinguish 404 (session truly gone) from 5xx
    // (transient). 404 surfaces as sessionGone so the chat page can
    // auto-redirect to a fresh URL via handleNewSession() instead of
    // letting the user keep typing into a stranded threadId.
    mock404();

    const { result } = renderHook(() => useSessionMessages("sess-gone"));

    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.sessionGone).toBe(true);
    expect(result.current.historyError).toBeNull();
    expect(result.current.initialMessages).toEqual([]);
  });

  it("stranded-session-prevention (1.23) Option 1: 5xx still sets historyError, NOT sessionGone", async () => {
    // Locks the floor: only 404 trips the auto-redirect path. Transient
    // backend errors (500, 502, 503) keep the user on the same threadId
    // with the existing "starting fresh" toast.
    mockError(500);

    const { result } = renderHook(() => useSessionMessages("sess-flake"));

    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.sessionGone).toBe(false);
    expect(result.current.historyError).toContain("starting fresh");
  });

  it("stranded-session-prevention (1.23) Option 1: sessionGone resets when sessionId changes", async () => {
    // After the chat page reads sessionGone and calls handleNewSession,
    // the URL drops ?session= and the hook gets a new sessionId.
    // sessionGone must reset to false on the new fetch so a future 404
    // on the new id can trip again.
    mock404();
    mockOk([{ role: "user", content: "fresh start", timestamp: 1 }]);

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionMessages(sid),
      { initialProps: { sid: "sess-gone" as string | null } },
    );

    await waitFor(() => expect(result.current.sessionGone).toBe(true));

    rerender({ sid: "sess-fresh" });

    await waitFor(() => expect(result.current.initialMessages).toHaveLength(1));
    expect(result.current.sessionGone).toBe(false);
  });

  it("D4 (chat-history-deep-fixes H4): refetches when sessionId changes from one id to another", async () => {
    // Bug C from chat-history-deep-fixes.md: clicking a thread in
    // DocumentHistoryPanel calls handleSelectSession → navigateToSession,
    // which updates the URL ?session=<new>. useSessionMessages should
    // then fetch the new session's messages. If the hook fails to refetch
    // (or the fetched messages never reach state), the user sees no
    // history when they select a thread.

    // First fetch: session A returns 1 message.
    mockOk([{ role: "user", content: "from session A", timestamp: 1 }]);
    // Second fetch (after rerender): session B returns 3 messages.
    mockOk([
      { role: "user", content: "Q1 in B", timestamp: 1 },
      { role: "assistant", content: "A1 in B", timestamp: 2 },
      { role: "user", content: "Q2 in B", timestamp: 3 },
    ]);

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => useSessionMessages(sid),
      { initialProps: { sid: "session-A" } },
    );

    await waitFor(() => expect(result.current.initialMessages).toHaveLength(1));
    expect(result.current.initialMessages[0].content).toBe("from session A");

    // Simulate user clicking a thread that points at session-B.
    rerender({ sid: "session-B" });

    // The hook MUST refetch and reflect session-B's messages.
    await waitFor(() => expect(result.current.initialMessages).toHaveLength(3));
    expect(result.current.initialMessages[0].content).toBe("Q1 in B");
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[1][0]).toBe(
      "/api/proxy/api/sessions/session-B/messages",
    );
  });
});

describe("useSessionMessages — transcript_unavailable (regression 2026-08-05)", () => {
  // ag_ui_adk's SessionManager sweep deleted idle sessions out of Vertex Agent
  // Engine while the Firestore index row survived, so a listed conversation
  // resumed to 200 + [] — a blank thread with no explanation. The backend now
  // distinguishes the case; the hook must surface it so the UI can say so.
  function mockOkWith(body: object) {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ messages: [], session_id: "sess-1", ...body }),
    } as Response);
  }

  it("exposes transcriptUnavailable=true when the backend flags a lost transcript", async () => {
    mockOkWith({ transcript_unavailable: true });
    const { result } = renderHook(() => useSessionMessages("sess-1"));

    await waitFor(() => expect(result.current.transcriptUnavailable).toBe(true));
    expect(result.current.initialMessages).toEqual([]);
    // Not the same thing as a session that never existed.
    expect(result.current.sessionGone).toBe(false);
    expect(result.current.historyError).toBeNull();
  });

  it("stays false for a normal empty conversation", async () => {
    mockOkWith({});
    const { result } = renderHook(() => useSessionMessages("sess-1"));

    await waitFor(() => expect(result.current.isLoadingHistory).toBe(false));
    expect(result.current.transcriptUnavailable).toBe(false);
  });

  it("resets when the session changes", async () => {
    mockOkWith({ transcript_unavailable: true });
    const { result, rerender } = renderHook(({ id }) => useSessionMessages(id), {
      initialProps: { id: "sess-1" },
    });
    await waitFor(() => expect(result.current.transcriptUnavailable).toBe(true));

    mockOkWith({});
    rerender({ id: "sess-2" });
    await waitFor(() => expect(result.current.transcriptUnavailable).toBe(false));
  });
});
