import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/firebase", () => ({
  getIdToken: vi.fn().mockResolvedValue("test-token"),
}));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

import { useSessionDocuments } from "@/hooks/useSessionDocuments";

function mockSession(documentIds: string[]) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ session: { document_ids: documentIds } }),
  } as Response);
}

function mockDocument(id: string, originalFilename: string, sourceFormat: string) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({ id, originalFilename, sourceFormat }),
  } as Response);
}

function mockHttpError(status = 500) {
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status,
    json: () => Promise.resolve({}),
  } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useSessionDocuments — route hydration into doc tabs", () => {
  // The chat page enters via several routes — see the route table at
  // the top of frontend/src/app/chat/[...path]/page.tsx. This hook is
  // the bridge for routes that resume an existing session: it reads
  // ``session.document_ids`` from the backend and rebuilds the tab list
  // with ``included: true``, so the user lands on the same workspace.

  it("returns null tabs while sessionId is null (fresh-chat route)", () => {
    // Route: user opened the chat URL without ?session=. The hook must
    // signal "leave openTabs alone" — null, not [] — so the page does
    // not clobber tabs the user opened with handleDocClick.
    const { result } = renderHook(() => useSessionDocuments(null));

    expect(result.current.tabs).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns [] for sessions that have no documents (fresh-then-writeback route)", async () => {
    // Route: user started a fresh chat, sent a message; URL writeback
    // sets ?session=newId. The hook fetches the session — backend stored
    // document_ids: [] because the first turn had nothing attached. The
    // hook must return [] so the tab-hydration effect can no-op cleanly.
    mockSession([]);

    const { result } = renderHook(() => useSessionDocuments("newId"));

    await waitFor(() => expect(result.current.tabs).toEqual([]));
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/sessions/newId",
      expect.any(Object),
    );
  });

  it("hydrates session document_ids into tabs with included=true (resume-with-docs route)", async () => {
    // Route: user clicked a thread from history (or from a doc's
    // Conversations panel). Tabs must mount with included=true so the
    // user can immediately ask about the docs without clicking each
    // checkbox. Also asserts that filename + format are populated from
    // the per-doc fetch — without that, the tab title falls back to
    // the raw id and the doc-browser UI looks broken on resume.
    mockSession(["doc-volunteers", "doc-claim"]);
    mockDocument("doc-volunteers", "VOLUNTEERS for the show.docx", "docx");
    mockDocument("doc-claim", "claim_incident_summary.docx", "docx");

    const { result } = renderHook(() => useSessionDocuments("sess-resume"));

    await waitFor(() => expect(result.current.tabs).toHaveLength(2));
    expect(result.current.tabs).toEqual([
      {
        id: "doc-volunteers",
        filename: "VOLUNTEERS for the show.docx",
        format: "docx",
        included: true,
      },
      {
        id: "doc-claim",
        filename: "claim_incident_summary.docx",
        format: "docx",
        included: true,
      },
    ]);
  });

  it("falls back to id+blank format when a doc fetch fails (partial-failure route)", async () => {
    // Route: a session's document_ids list contains an id whose document
    // record was deleted (or the user lost access). The session itself
    // still loads. The hook must produce a tab for the surviving doc,
    // and either skip or stub the failed one — never throw, never leave
    // ``tabs`` stuck at null.
    mockSession(["doc-good", "doc-missing"]);
    mockDocument("doc-good", "Readable.docx", "docx");
    mockHttpError(404); // doc-missing fetch returns 404

    const { result } = renderHook(() => useSessionDocuments("sess-partial"));

    await waitFor(() => expect(result.current.tabs).not.toBeNull());
    const tabs = result.current.tabs ?? [];
    const goodTab = tabs.find((t) => t.id === "doc-good");
    expect(goodTab).toEqual({
      id: "doc-good",
      filename: "Readable.docx",
      format: "docx",
      included: true,
    });
    // The missing doc still produces a tab so the user knows it WAS
    // attached to this thread; ``included: true`` keeps the contract.
    const missingTab = tabs.find((t) => t.id === "doc-missing");
    expect(missingTab?.included).toBe(true);
  });

  it("returns [] when the session fetch itself fails (resume-broken route)", async () => {
    // Route: user clicked a thread but the session row is gone (TTL,
    // permission revoked). The hook must not leave ``tabs`` at null —
    // that would freeze the tab-hydration effect indefinitely. Returning
    // [] lets the effect mark the session as synced and let the user
    // start adding tabs themselves.
    mockHttpError(404);

    const { result } = renderHook(() =>
      useSessionDocuments("sess-broken"),
    );

    await waitFor(() => expect(result.current.tabs).toEqual([]));
  });

  it("re-fetches when sessionId changes (switch-session route)", async () => {
    // Route: user clicks a different thread (handleSelectSession). The
    // hook must replace the tab list with the new session's docs,
    // not silently keep the previous session's hydration.
    mockSession(["doc-A"]);
    mockDocument("doc-A", "A.docx", "docx");

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionDocuments(sid),
      { initialProps: { sid: "sess-1" } },
    );
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));
    expect(result.current.tabs?.[0].id).toBe("doc-A");

    mockSession(["doc-B", "doc-C"]);
    mockDocument("doc-B", "B.docx", "docx");
    mockDocument("doc-C", "C.docx", "docx");

    rerender({ sid: "sess-2" });

    await waitFor(() => expect(result.current.tabs).toHaveLength(2));
    expect(result.current.tabs?.map((t) => t.id)).toEqual(["doc-B", "doc-C"]);
  });

  it("clears tabs back to null when sessionId becomes null (back-to-fresh route)", async () => {
    // Route: user clicks "+ New conversation" — handleNewSession clears
    // ?session= from the URL. The hook must return null (not []) so the
    // page's tab-hydration effect drops its lastSyncedSessionId guard
    // without clobbering openTabs the user has since added.
    mockSession(["doc-X"]);
    mockDocument("doc-X", "X.docx", "docx");

    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionDocuments(sid),
      { initialProps: { sid: "sess-1" as string | null } },
    );
    await waitFor(() => expect(result.current.tabs).toHaveLength(1));

    rerender({ sid: null });

    await waitFor(() => expect(result.current.tabs).toBeNull());
  });
});
