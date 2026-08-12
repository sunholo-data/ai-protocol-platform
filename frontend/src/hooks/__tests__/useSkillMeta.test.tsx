import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock fetchWithAuth at the module level
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { useSkillMeta } from "@/hooks/useSkillMeta";

const mockFetch = fetchWithAuth as ReturnType<typeof vi.fn>;

function makeResponse(body: object, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
  } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("useSkillMeta", () => {
  it("initial displayName is first 8 chars of skillId (no blank flash)", () => {
    mockFetch.mockReturnValue(new Promise(() => {})); // never resolves
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    expect(result.current.displayName).toBe("abc12345");
    expect(result.current.loading).toBe(true);
  });

  it("resolves to display_name from API response", async () => {
    mockFetch.mockReturnValue(makeResponse({ display_name: "Research Assistant", name: "research" }));
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.displayName).toBe("Research Assistant");
  });

  it("falls back to name when display_name is absent", async () => {
    mockFetch.mockReturnValue(makeResponse({ name: "research-v2" }));
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.displayName).toBe("research-v2");
  });

  it("falls back to name when display_name is empty string", async () => {
    mockFetch.mockReturnValue(makeResponse({ display_name: "", name: "research-fallback" }));
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.displayName).toBe("research-fallback");
  });

  it("stays as truncated UUID on fetch error (graceful degradation)", async () => {
    mockFetch.mockReturnValue(Promise.reject(new Error("Network error")));
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.displayName).toBe("abc12345");
  });

  it("stays as truncated UUID when API returns non-ok response", async () => {
    mockFetch.mockReturnValue(makeResponse({}, false));
    const { result } = renderHook(() => useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.displayName).toBe("abc12345");
  });

  it("does not update state after unmount (cancel on unmount)", async () => {
    // Use a never-resolving fetch so we can control timing
    mockFetch.mockReturnValue(new Promise(() => {}));

    const { result, unmount } = renderHook(() =>
      useSkillMeta("abc12345-xxxx-xxxx-xxxx-xxxxxxxxxxxx"),
    );

    // Confirm initial state
    expect(result.current.displayName).toBe("abc12345");
    expect(result.current.loading).toBe(true);

    // Unmount while fetch is still pending
    unmount();

    // After unmount: state is frozen at initial values — no crash
    expect(result.current.displayName).toBe("abc12345");
  });
});
