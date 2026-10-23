import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
vi.mock("@/lib/documentEvents", () => ({ subscribeDocumentsChanged: () => () => {} }));

import { useKnowledgeBases } from "../useKnowledgeBases";

function json(body: unknown, status = 200): Response {
  return { ok: status < 300, status, json: async () => body } as unknown as Response;
}

beforeEach(() => fetchWithAuth.mockReset());

describe("useKnowledgeBases", () => {
  it("is unavailable when the backend has no knowledge bases (404)", async () => {
    fetchWithAuth.mockResolvedValue(json({ detail: "Not Found" }, 404));
    const { result } = renderHook(() => useKnowledgeBases());
    await waitFor(() => expect(result.current.available).toBe(false));
  });

  it("maps the list by id with sync progress", async () => {
    fetchWithAuth.mockResolvedValue(
      json([{ kbId: "llm-1", name: "llm", documentCount: 4, lastSyncSummary: { state: "done", total: 5, failed: 1 } }]),
    );
    const { result } = renderHook(() => useKnowledgeBases());
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.byId["llm-1"]).toMatchObject({ documentCount: 4, state: "done", total: 5, failed: 1 });
  });

  it("re-polls while a knowledge base is indexing, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchWithAuth
      .mockResolvedValueOnce(json([{ kbId: "k", name: "k", documentCount: 0, lastSyncSummary: { state: "indexing", total: 2, done: 1 } }]))
      .mockResolvedValue(json([{ kbId: "k", name: "k", documentCount: 2, lastSyncSummary: { state: "done" } }]));
    const { result } = renderHook(() => useKnowledgeBases());
    await waitFor(() => expect(result.current.byId.k?.state).toBe("indexing"));
    await vi.advanceTimersByTimeAsync(4100);
    await waitFor(() => expect(result.current.byId.k?.state).toBe("done"));
    const calls = fetchWithAuth.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetchWithAuth.mock.calls.length).toBe(calls);
    vi.useRealTimers();
  });
});
