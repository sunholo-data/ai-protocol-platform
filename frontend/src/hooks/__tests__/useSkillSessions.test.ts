import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import { useSkillSessions, useSharedSessions } from "@/hooks/useSkillSessions";

function mockOk(sessions: object[], next_cursor: string | null = null) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: () => Promise.resolve({ sessions, next_cursor }),
  } as Response);
}

function mockError(status = 500) {
  mockFetch.mockResolvedValueOnce({ ok: false, status } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
  // Prevent unhandled rejection noise for abort
  mockFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve({ sessions: [], next_cursor: null }) } as Response);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useSkillSessions", () => {
  // 2026-08-05: this list became CROSS-SKILL. Switching agent via the top bar
  // starts a new session on the new skill, so scoping history to the current
  // skill showed only the fragment for whichever agent the user was standing
  // on, and a sitting spread across two agents read as "it wasn't recorded".
  it("fetches ALL the user's conversations when no filter is set", async () => {
    mockOk([]);

    renderHook(() => useSkillSessions(null));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/proxy/api/sessions?page_size=30",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("narrows to one agent when a filter is set", async () => {
    mockOk([]);

    renderHook(() => useSkillSessions("my-skill"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/proxy/api/sessions?page_size=30&skill_id=my-skill",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it("returns sessions on success", async () => {
    const session = {
      session_id: "sess-1",
      skill_id: "my-skill",
      owner_uid: "u1",
      title: "Test session",
      turn_count: 2,
      first_message_at: "2026-04-24T10:00:00Z",
      last_message_at: "2026-04-24T10:01:00Z",
      archived_at: null,
      document_ids: [],
      is_owner: true,
    };
    mockOk([session]);

    const { result } = renderHook(() => useSkillSessions("my-skill"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0].session_id).toBe("sess-1");
    expect(result.current.error).toBeNull();
  });

  it("returns empty list when no sessions", async () => {
    mockOk([]);

    const { result } = renderHook(() => useSkillSessions("my-skill"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.sessions).toEqual([]);
  });

  it("sets error on HTTP failure", async () => {
    mockError(500);

    const { result } = renderHook(() => useSkillSessions("my-skill"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe("Failed to load sessions");
  });

  it("a null filter means ALL agents, not 'skip the fetch'", async () => {
    // Was "does not fetch when skillId is null" — that only ever passed by
    // timing (fetchWithAuth awaits a token, so the spy is not called in the
    // same tick). Null now explicitly means the cross-skill list.
    mockOk([]);
    renderHook(() => useSkillSessions(null));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/sessions?page_size=30");
  });

  it("refetches when a sessions-changed event is dispatched (cross-panel sync)", async () => {
    mockOk([{ session_id: "s1" }]);
    mockOk([{ session_id: "s1" }, { session_id: "s2" }]);

    const { result } = renderHook(() => useSkillSessions("my-skill"));

    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    expect(mockFetch).toHaveBeenCalledTimes(1);

    // Another panel deletes (or renames) a session and notifies.
    window.dispatchEvent(new CustomEvent("aitana:sessions-changed"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.sessions).toHaveLength(2));
  });
});

describe("useSkillSessions — older pages", () => {
  it("loads the next page with the cursor and appends it", async () => {
    mockOk([{ session_id: "s1" }], "s1");
    const { result } = renderHook(() => useSkillSessions(null));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    expect(result.current.hasMore).toBe(true);

    mockOk([{ session_id: "s2" }], null);
    result.current.loadMore();

    await waitFor(() => expect(result.current.sessions.map((s) => s.session_id)).toEqual(["s1", "s2"]));
    expect(mockFetch.mock.calls[1][0]).toBe("/api/proxy/api/sessions?page_size=30&cursor=s1");
    expect(result.current.hasMore).toBe(false);
  });

  it("a failed load-more keeps the list and flags only the load-more", async () => {
    mockOk([{ session_id: "s1" }], "s1");
    const { result } = renderHook(() => useSkillSessions(null));
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    mockError(500);
    result.current.loadMore();

    await waitFor(() => expect(result.current.loadMoreFailed).toBe(true));
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });
});

describe("useSharedSessions", () => {
  it("fetches sessions shared with the caller", async () => {
    mockOk([{ session_id: "s1", owner_name: "Daneel", can_reply: true }]);
    const { result } = renderHook(() => useSharedSessions());
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/sessions?shared=true&page_size=30",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.current.sessions[0].owner_name).toBe("Daneel");
  });

  it("surfaces an error instead of swallowing it", async () => {
    mockError(503);
    const { result } = renderHook(() => useSharedSessions());
    await waitFor(() => expect(result.current.error).toBe("Failed to load sessions"));
    expect(result.current.isLoading).toBe(false);
  });
});
