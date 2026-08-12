import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { useSlugResolution } from "@/hooks/useSlugResolution";

const mockFetch = fetchWithAuth as ReturnType<typeof vi.fn>;

function makeResponse(body: object, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("useSlugResolution", () => {
  it("starts in loading state with no skillId", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useSlugResolution(["@user-1", "general"]));
    expect(result.current.loading).toBe(true);
    expect(result.current.skillId).toBeNull();
    expect(result.current.notFound).toBe(false);
  });

  it("resolves to skillId on 200", async () => {
    mockFetch.mockReturnValue(makeResponse({ skillId: "abc-123" }));
    const { result } = renderHook(() => useSlugResolution(["@user-1", "general-assistant"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.skillId).toBe("abc-123");
    expect(result.current.notFound).toBe(false);
  });

  it("calls /api/proxy/api/skills/by-slug with URL-encoded segments", async () => {
    mockFetch.mockReturnValue(makeResponse({ skillId: "abc-123" }));
    renderHook(() => useSlugResolution(["@user-1", "general"]));
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/skills/by-slug/user-1/general",
    );
  });

  it("resolves a bare skill-id via the by-id endpoint (skillHref's slug-less fallback)", async () => {
    // A skill with no slug yet links as /chat/{skillId}; the resolver must honor
    // it (this was the bug — it 404'd such skills client-side without fetching).
    mockFetch.mockReturnValue(makeResponse({ skillId: "b123b609" }));
    const { result } = renderHook(() => useSlugResolution(["b123b609"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(false);
    expect(result.current.skillId).toBe("b123b609");
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/skills/b123b609");
  });

  it("returns notFound (bare-id) on 404 from backend", async () => {
    mockFetch.mockReturnValue(makeResponse({}, false, 404));
    const { result } = renderHook(() => useSlugResolution(["nonexistent-id"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(true);
  });

  it("returns notFound on a genuinely malformed path (3 segments)", async () => {
    const { result } = renderHook(() => useSlugResolution(["a", "b", "c"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns notFound when a 2-segment path's first segment lacks @ prefix", async () => {
    const { result } = renderHook(() => useSlugResolution(["user-1", "slug"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns notFound on 404 from backend", async () => {
    mockFetch.mockReturnValue(makeResponse({}, false, 404));
    const { result } = renderHook(() => useSlugResolution(["@user-1", "missing"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(true);
    expect(result.current.skillId).toBeNull();
  });

  it("surfaces error on non-404 failure", async () => {
    mockFetch.mockReturnValue(makeResponse({}, false, 500));
    const { result } = renderHook(() => useSlugResolution(["@user-1", "general"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 500");
    expect(result.current.notFound).toBe(false);
  });

  it("does not update state after unmount", async () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    const { result, unmount } = renderHook(() =>
      useSlugResolution(["@user-1", "general"]),
    );
    expect(result.current.loading).toBe(true);
    unmount();
    expect(result.current.skillId).toBeNull();
  });

  it("decodes URL-encoded path segments before validating + fetching", async () => {
    // Next.js URL-encodes route params, so /chat/@foo/slug arrives in this
    // hook as ["%40foo", "slug"]. Bug regression: the validator was checking
    // startsWith("@") on the raw segment, silently flipping notFound and
    // never firing the fetch.
    mockFetch.mockReturnValue(makeResponse({ skillId: "abc-123" }));
    const { result } = renderHook(() =>
      useSlugResolution(["%40aitana-platform", "general-assistant"]),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(false);
    expect(result.current.skillId).toBe("abc-123");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/skills/by-slug/aitana-platform/general-assistant",
    );
  });

  it("decodes percent-encoded slug as well", async () => {
    mockFetch.mockReturnValue(makeResponse({ skillId: "abc-123" }));
    renderHook(() =>
      useSlugResolution(["%40owner", "weird%20slug"]),
    );
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    // Decoded once for validation, then re-encoded by encodeURIComponent
    // when constructing the URL — round-trip should be lossless.
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/skills/by-slug/owner/weird%20slug",
    );
  });

  it("does not fetch while disabled (auth still hydrating)", async () => {
    mockFetch.mockReturnValue(makeResponse({ skillId: "abc-123" }));
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useSlugResolution(["@user-1", "general"], enabled),
      { initialProps: { enabled: false } },
    );

    // While disabled: stays in loading state, never fetches.
    expect(result.current.loading).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();

    // Once enabled: fetch fires and resolves.
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(result.current.skillId).toBe("abc-123");
  });
});
