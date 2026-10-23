// useToolLabels — loads /api/tools once, never throws, falls back to the
// static map + humanise when the catalog fetch fails.

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args),
}));

import { __resetToolLabelsCache, useToolLabels } from "@/hooks/useToolLabels";

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

describe("useToolLabels", () => {
  beforeEach(() => {
    __resetToolLabelsCache();
    fetchWithAuth.mockReset();
  });
  afterEach(() => __resetToolLabelsCache());

  it("resolves catalog labels once /api/tools loads", async () => {
    fetchWithAuth.mockReturnValue(
      ok({ tools: [{ name: "my_context", label: "Your setup", description: "", category: "" }] }),
    );
    const { result } = renderHook(() => useToolLabels());
    await waitFor(() => expect(result.current("my_context")).toBe("Your setup"));
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/proxy/api/tools");
    // Framework tools still come from the static map.
    expect(result.current("transfer_to_agent")).toBe("Hand over to another skill");
  });

  it("fetches the catalog once per page load, shared across consumers", async () => {
    fetchWithAuth.mockReturnValue(ok({ tools: [{ name: "a_tool", label: "A label" }] }));
    const a = renderHook(() => useToolLabels());
    const b = renderHook(() => useToolLabels());
    await waitFor(() => expect(a.result.current("a_tool")).toBe("A label"));
    await waitFor(() => expect(b.result.current("a_tool")).toBe("A label"));
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  });

  it("falls back to the static map + humanise when the fetch fails", async () => {
    fetchWithAuth.mockReturnValue(Promise.reject(new Error("offline")));
    const { result } = renderHook(() => useToolLabels());
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(result.current("web_search_agent")).toBe("Web search");
    expect(result.current("demo_research_worker")).toBe("Demo research worker");
  });

  it("falls back on a non-OK response and on a synchronous throw", async () => {
    fetchWithAuth.mockReturnValue(Promise.resolve({ ok: false, status: 500 }));
    const first = renderHook(() => useToolLabels());
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalled());
    expect(first.result.current("my_context")).toBe("My context");

    __resetToolLabelsCache();
    fetchWithAuth.mockImplementation(() => {
      throw new Error("boom");
    });
    const second = renderHook(() => useToolLabels());
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledTimes(2));
    expect(second.result.current("my_context")).toBe("My context");
  });
});
