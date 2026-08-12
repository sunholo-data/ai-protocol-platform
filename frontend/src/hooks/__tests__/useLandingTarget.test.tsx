import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { useLandingTarget } from "@/hooks/useLandingTarget";

const mockFetch = fetchWithAuth as unknown as ReturnType<typeof vi.fn>;

function res(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

describe("useLandingTarget", () => {
  beforeEach(() => mockFetch.mockReset());

  it("does nothing until enabled", () => {
    const { result } = renderHook(() => useLandingTarget(false));
    expect(result.current.kind).toBe("loading");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("resumes the most-recent session", async () => {
    mockFetch.mockResolvedValueOnce(
      res(200, { session_id: "sess-1", skill_id: "skill-a", slug: "ppa-expert", owner_id: "aitana-platform" }),
    );
    const { result } = renderHook(() => useLandingTarget(true));
    await waitFor(() => expect(result.current.kind).toBe("resume"));
    expect(result.current).toMatchObject({
      kind: "resume",
      href: "/chat/@aitana-platform/ppa-expert?session=sess-1",
    });
  });

  it("falls back to the primary skill (matching default_skill) when no session", async () => {
    // call order: recent (204) → Promise.all[ clients/me, skills ]
    mockFetch
      .mockResolvedValueOnce(res(204, null))
      .mockResolvedValueOnce(res(200, { default_skill: "ppa-expert" }))
      .mockResolvedValueOnce(
        res(200, [
          { skillId: "w", slug: "web-researcher", ownerId: "aitana-platform" },
          { skillId: "a", slug: "ppa-expert", ownerId: "aitana-platform" },
        ]),
      );
    const { result } = renderHook(() => useLandingTarget(true));
    await waitFor(() => expect(result.current.kind).toBe("fresh"));
    expect(result.current).toMatchObject({ kind: "fresh", href: "/chat/@aitana-platform/ppa-expert" });
  });

  it("falls back to the first skill when default_skill is unset", async () => {
    mockFetch
      .mockResolvedValueOnce(res(204, null))
      .mockResolvedValueOnce(res(200, { default_skill: null }))
      .mockResolvedValueOnce(res(200, [{ skillId: "w", slug: "web-researcher", ownerId: "aitana-platform" }]));
    const { result } = renderHook(() => useLandingTarget(true));
    await waitFor(() => expect(result.current.kind).toBe("fresh"));
    expect(result.current).toMatchObject({ kind: "fresh", href: "/chat/@aitana-platform/web-researcher" });
  });

  it("returns landing when there are no skills", async () => {
    mockFetch
      .mockResolvedValueOnce(res(204, null))
      .mockResolvedValueOnce(res(200, {}))
      .mockResolvedValueOnce(res(200, []));
    const { result } = renderHook(() => useLandingTarget(true));
    await waitFor(() => expect(result.current.kind).toBe("landing"));
  });

  it("degrades to landing on error", async () => {
    mockFetch.mockResolvedValue({
      status: 200,
      ok: true,
      json: async () => {
        throw new Error("bad json");
      },
    });
    const { result } = renderHook(() => useLandingTarget(true));
    await waitFor(() => expect(result.current.kind).toBe("landing"));
  });
});
