/**
 * useDocBrowser — Firestore listeners, with an API fallback for browsers that
 * have no Firebase session (tailnet / local mode) or whose listener is denied.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type SnapCb = (snap: { docs: { id: string; data: () => Record<string, unknown> }[] }) => void;
type ErrCb = (err: { code: string }) => void;
const listeners: { ref: unknown; next: SnapCb; error?: ErrCb; unsub: ReturnType<typeof vi.fn> }[] = [];

vi.mock("firebase/firestore", () => ({
  collection: vi.fn((...path: unknown[]) => ({ path: path.slice(1).join("/") })),
  query: vi.fn((col: unknown) => ({ q: col })),
  where: vi.fn(),
  onSnapshot: vi.fn((ref: unknown, next: SnapCb, error?: ErrCb) => {
    const unsub = vi.fn();
    listeners.push({ ref, next, error, unsub });
    return unsub;
  }),
}));

const getFirestoreDb = vi.fn();
vi.mock("@/lib/firebase", () => ({
  getFirestoreDb: () => getFirestoreDb(),
  firestoreTimestampToIso: (v: unknown) => (typeof v === "string" ? v : null),
}));
vi.mock("@/lib/anonymousGroupAuth", () => ({ isAnonymousGroupAuthMode: () => false }));

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

import { PENDING_POLL_MS, useDocBrowser } from "../useDocBrowser";
import { notifyDocumentsChanged } from "@/lib/documentEvents";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const FOLDERS = {
  folders: [
    { id: "created", name: "Created", userId: "u1", docCount: 1, parsedCount: 1, system: true },
    { id: "f1", name: "Contracts", userId: "u1", docCount: 2, parsedCount: 1 },
  ],
};

function docsBody(status = "parsed") {
  return {
    documents: [
      { id: "d1", originalFilename: "a.pdf", sourceFormat: "pdf", parseStatus: status, folderId: "f1", userId: "u1", createdAt: "2026-09-01T00:00:00Z" },
    ],
  };
}

/** Route mock: folders list vs documents list. */
function routeFetch(docs: () => Response = () => json(docsBody())) {
  fetchWithAuth.mockImplementation(async (url: string) =>
    url === "/api/proxy/api/folders" ? json(FOLDERS) : docs(),
  );
}

function docCalls(): number {
  return fetchWithAuth.mock.calls.filter(([u]) => String(u).includes("/documents")).length;
}

beforeEach(() => {
  listeners.length = 0;
  fetchWithAuth.mockReset();
  getFirestoreDb.mockReset();
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useDocBrowser — Firestore available", () => {
  it("uses the listeners only and never calls the API", async () => {
    getFirestoreDb.mockReturnValue({});
    const { result } = renderHook(() => useDocBrowser("u1"));
    expect(listeners).toHaveLength(1);
    act(() => {
      listeners[0].next({ docs: [{ id: "f1", data: () => ({ name: "Contracts", userId: "u1" }) }] });
    });
    expect(result.current.folders.map((f) => f.id)).toEqual(["f1"]);
    expect(result.current.activeFolderId).toBe("f1");
    expect(listeners).toHaveLength(2); // the documents listener for f1
    act(() => {
      listeners[1].next({ docs: [{ id: "d1", data: () => ({ originalFilename: "a.pdf", parseStatus: "parsed" }) }] });
    });
    expect(result.current.documents.map((d) => d.originalFilename)).toEqual(["a.pdf"]);
    // An upload notification does not double-fetch on the Firestore path.
    act(() => notifyDocumentsChanged());
    expect(fetchWithAuth).not.toHaveBeenCalled();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("falls back to the API when a listener is denied", async () => {
    getFirestoreDb.mockReturnValue({});
    routeFetch();
    const { result } = renderHook(() => useDocBrowser("u1"));
    act(() => listeners[0].error?.({ code: "permission-denied" }));
    expect(listeners[0].unsub).toHaveBeenCalled();
    await waitFor(() => expect(result.current.documents.map((d) => d.id)).toEqual(["d1"]));
    expect(result.current.folders.map((f) => f.id)).toEqual(["created", "f1"]);
  });
});

describe("useDocBrowser — API fallback (no Firestore client)", () => {
  beforeEach(() => getFirestoreDb.mockReturnValue(null));

  it("lists folders and the default (user-owned) folder's documents", async () => {
    routeFetch();
    const { result } = renderHook(() => useDocBrowser("u1"));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.documents).toHaveLength(1));
    expect(listeners).toHaveLength(0);
    expect(result.current.loading).toBe(false);
    expect(result.current.folders.find((f) => f.id === "created")?.system).toBe(true);
    expect(result.current.activeFolderId).toBe("f1"); // not the system "Created" folder
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/folders/f1/documents");
    expect(result.current.documents[0]).toMatchObject({
      id: "d1",
      originalFilename: "a.pdf",
      parseStatus: "parsed",
      folderId: "f1",
      source: "uploaded",
      hasA2ui: false,
    });
  });

  it("refetches when the folder changes and when documents change", async () => {
    routeFetch();
    const { result } = renderHook(() => useDocBrowser("u1"));
    await waitFor(() => expect(result.current.documents).toHaveLength(1));
    act(() => result.current.setActiveFolderId("created"));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/folders/created/documents"));
    const before = fetchWithAuth.mock.calls.length;
    act(() => notifyDocumentsChanged());
    await waitFor(() => expect(fetchWithAuth.mock.calls.length).toBe(before + 2)); // folders + documents
  });

  it("re-selecting the active folder keeps its documents (folder row re-opened)", async () => {
    routeFetch();
    const { result } = renderHook(() => useDocBrowser("u1"));
    await waitFor(() => expect(result.current.documents).toHaveLength(1));
    const calls = docCalls();
    act(() => result.current.setActiveFolderId("f1"));
    expect(result.current.documents).toHaveLength(1);
    expect(docCalls()).toBe(calls);
  });

  it("polls while a document is pending and stops once none are", async () => {
    vi.useFakeTimers();
    let n = 0;
    routeFetch(() => json(docsBody(++n < 3 ? "pending" : "parsed")));
    const { result } = renderHook(() => useDocBrowser("u1"));
    const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await flush();
    await flush();
    expect(result.current.documents[0].parseStatus).toBe("pending");
    expect(docCalls()).toBe(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(PENDING_POLL_MS); });
    await flush();
    expect(docCalls()).toBe(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(PENDING_POLL_MS); });
    await flush();
    expect(docCalls()).toBe(3);
    expect(result.current.documents[0].parseStatus).toBe("parsed");

    await act(async () => { await vi.advanceTimersByTimeAsync(PENDING_POLL_MS * 3); });
    expect(docCalls()).toBe(3); // no more polling
  });

  it("surfaces an API error and recovers on retry", async () => {
    fetchWithAuth.mockResolvedValueOnce(json({ detail: "boom" }, 500));
    const { result } = renderHook(() => useDocBrowser("u1"));
    await waitFor(() => expect(result.current.error).toMatch(/HTTP 500/));
    expect(result.current.loading).toBe(false);
    routeFetch();
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.documents).toHaveLength(1));
    expect(result.current.error).toBeNull();
  });

  it("surfaces a network failure", async () => {
    fetchWithAuth.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useDocBrowser("u1"));
    await waitFor(() => expect(result.current.error).toMatch(/network error/));
  });
});
