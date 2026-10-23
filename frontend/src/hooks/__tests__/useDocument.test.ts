/**
 * useDocument — Firestore onSnapshot, with an API fallback for browsers that
 * have no Firebase session (tailnet / local mode) or whose listener is denied.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Snap = { id: string; exists: () => boolean; data: () => Record<string, unknown> };
type SnapCb = (snap: Snap) => void;
type ErrCb = (err: { code: string }) => void;
const listeners: { ref: unknown; next: SnapCb; error?: ErrCb; unsub: ReturnType<typeof vi.fn> }[] = [];

vi.mock("firebase/firestore", () => ({
  doc: vi.fn((_db: unknown, ...path: string[]) => ({ path: path.join("/") })),
  onSnapshot: vi.fn((ref: unknown, next: SnapCb, error?: ErrCb) => {
    const unsub = vi.fn();
    listeners.push({ ref, next, error, unsub });
    return unsub;
  }),
}));

const getFirestoreDb = vi.fn();
vi.mock("@/lib/firebase", () => ({
  getFirestoreDb: () => getFirestoreDb(),
  firestoreTimestampToIso: (v: unknown) => {
    if (!v) return null;
    if (typeof v === "string") return v;
    if (typeof v === "object" && v !== null && "toDate" in v) {
      return (v as { toDate: () => Date }).toDate().toISOString();
    }
    return null;
  },
}));
vi.mock("@/lib/anonymousGroupAuth", () => ({ isAnonymousGroupAuthMode: () => false }));

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

import { DOCUMENT_POLL_MS, useDocument } from "../useDocument";
import { notifyDocumentsChanged } from "@/lib/documentEvents";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PARSED_AT = new Date("2026-10-01T12:34:56.789Z");

function record(parseStatus = "parsed") {
  return {
    id: "d1",
    userId: "u1",
    originalFilename: "contract.pdf",
    sourceFormat: "pdf",
    parseStatus,
    parseError: null,
    sourceUrl: "gs://b/contract.pdf",
    blockCount: 1,
    headingCount: 1,
    blocks: [{ type: "heading", level: 1, text: "Agreement" }],
  };
}

const API_URL = "/api/proxy/api/documents/d1";

function apiCalls(): number {
  return fetchWithAuth.mock.calls.filter(([u]) => u === API_URL).length;
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

describe("useDocument — Firestore available", () => {
  it("reads via onSnapshot only and never calls the API", async () => {
    getFirestoreDb.mockReturnValue({});
    const { result } = renderHook(() => useDocument("d1"));
    expect(listeners).toHaveLength(1);
    expect(listeners[0].ref).toEqual({ path: "parsed_documents/d1" });

    act(() => {
      listeners[0].next({ id: "d1", exists: () => true, data: () => ({ ...record(), parsedAt: { toDate: () => PARSED_AT } }) });
    });
    expect(result.current.doc?.originalFilename).toBe("contract.pdf");
    expect(result.current.doc?.blocks).toHaveLength(1);
    expect(result.current.doc?.parsedAt).toBe("2026-10-01T12:34:56.789Z");
    expect(result.current.error).toBeNull();
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("reports a missing document from the snapshot", () => {
    getFirestoreDb.mockReturnValue({});
    const { result } = renderHook(() => useDocument("d1"));
    act(() => {
      listeners[0].next({ id: "d1", exists: () => false, data: () => ({}) });
    });
    expect(result.current.error).toBe("Document not found.");
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("falls back to the API when the listener is denied", async () => {
    getFirestoreDb.mockReturnValue({});
    fetchWithAuth.mockImplementation(async () => json(record()));
    const { result } = renderHook(() => useDocument("d1"));
    act(() => listeners[0].error?.({ code: "permission-denied" }));
    await waitFor(() => expect(result.current.doc?.id).toBe("d1"));
    expect(listeners[0].unsub).toHaveBeenCalled();
    expect(apiCalls()).toBe(1);
  });
});

describe("useDocument — no Firestore client (API fallback)", () => {
  beforeEach(() => getFirestoreDb.mockReturnValue(null));

  it("loads the record from the owner-checked route, same shape as the Firestore path", async () => {
    // Python serialises the same instant as "+00:00" with microseconds.
    fetchWithAuth.mockImplementation(async () => json({ ...record(), parsedAt: "2026-10-01T12:34:56.789000+00:00" }));
    const { result } = renderHook(() => useDocument("d1"));
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.doc).not.toBeNull());
    expect(listeners).toHaveLength(0);
    expect(fetchWithAuth).toHaveBeenCalledWith(API_URL);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.doc).toEqual({
      id: "d1",
      originalFilename: "contract.pdf",
      sourceFormat: "pdf",
      parseStatus: "parsed",
      parseError: null,
      sourceUrl: "gs://b/contract.pdf",
      parsedAt: "2026-10-01T12:34:56.789Z",
      updatedAt: null,
      summary: { totalBlocks: 1, headings: 1, tables: 0, images: 0, changes: 0 },
      blocks: [{ type: "heading", level: 1, text: "Agreement" }],
      template: null,
    });
  });

  it("polls while pending and stops once parsed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let status = "pending";
    fetchWithAuth.mockImplementation(async () => json(record(status)));
    const { result } = renderHook(() => useDocument("d1"));
    await waitFor(() => expect(result.current.doc?.parseStatus).toBe("pending"));
    expect(apiCalls()).toBe(1);

    status = "pending_ai_extraction";
    await act(async () => {
      vi.advanceTimersByTime(DOCUMENT_POLL_MS);
    });
    await waitFor(() => expect(result.current.doc?.parseStatus).toBe("pending_ai_extraction"));
    expect(apiCalls()).toBe(2);
    // A poll keeps the doc on screen — no loading flash.
    expect(result.current.isLoading).toBe(false);

    status = "parsed";
    await act(async () => {
      vi.advanceTimersByTime(DOCUMENT_POLL_MS);
    });
    await waitFor(() => expect(result.current.doc?.parseStatus).toBe("parsed"));
    expect(apiCalls()).toBe(3);

    await act(async () => {
      vi.advanceTimersByTime(DOCUMENT_POLL_MS * 3);
    });
    expect(apiCalls()).toBe(3);
  });

  it("does not poll a parsed document", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchWithAuth.mockImplementation(async () => json(record("parsed")));
    const { result } = renderHook(() => useDocument("d1"));
    await waitFor(() => expect(result.current.doc).not.toBeNull());
    await act(async () => {
      vi.advanceTimersByTime(DOCUMENT_POLL_MS * 3);
    });
    expect(apiCalls()).toBe(1);
  });

  it("refetches when the documents bus fires", async () => {
    fetchWithAuth.mockImplementation(async () => json(record()));
    const { result } = renderHook(() => useDocument("d1"));
    await waitFor(() => expect(result.current.doc).not.toBeNull());
    act(() => notifyDocumentsChanged());
    await waitFor(() => expect(apiCalls()).toBe(2));
  });

  it("surfaces a 403 as a visible error, and retry reloads", async () => {
    fetchWithAuth.mockImplementation(async () => json({ detail: "denied" }, 403));
    const { result } = renderHook(() => useDocument("d1"));
    await waitFor(() => expect(result.current.error).toBe("You don't have access to this document."));
    expect(result.current.doc).toBeNull();
    expect(result.current.isLoading).toBe(false);

    fetchWithAuth.mockImplementation(async () => json(record()));
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.doc?.id).toBe("d1"));
    expect(result.current.error).toBeNull();
  });

  it("surfaces 404 and network errors", async () => {
    fetchWithAuth.mockImplementation(async () => json({ detail: "nope" }, 404));
    const { result, rerender } = renderHook(({ id }) => useDocument(id), { initialProps: { id: "d1" } });
    await waitFor(() => expect(result.current.error).toBe("Document not found."));

    fetchWithAuth.mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });
    rerender({ id: "d2" });
    await waitFor(() => expect(result.current.error).toBe("Couldn't load this document (network error)."));
    expect(result.current.isLoading).toBe(false);
  });

  it("does nothing for a null docId", () => {
    const { result } = renderHook(() => useDocument(null));
    expect(fetchWithAuth).not.toHaveBeenCalled();
    expect(result.current.doc).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });
});
