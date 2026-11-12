import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { useTenantDefaultSkill } from "@/hooks/useTenantDefaultSkill";
import type { Skill } from "@/types/skill";

const mockFetch = fetchWithAuth as ReturnType<typeof vi.fn>;

const SKILLS = [
  { skillId: "id-a", slug: "tenant-a-door" },
  { skillId: "id-b", slug: "tenant-b-door" },
] as Skill[];

function respond(defaultSkill: string) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ default_skill: defaultSkill }) } as Response);
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("useTenantDefaultSkill", () => {
  it("does not fetch until there is a signed-in user", () => {
    const { result } = renderHook(() => useTenantDefaultSkill(SKILLS, null));
    expect(result.current).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("resolves the tenant default slug to the skill id", async () => {
    mockFetch.mockImplementation(() => respond("tenant-a-door"));
    const { result } = renderHook(() => useTenantDefaultSkill(SKILLS, "uid-a"));
    await waitFor(() => expect(result.current).toBe("id-a"));
  });

  it("refetches for the next user instead of keeping the previous tenant's default", async () => {
    // A different-tenant sign-in on the same tab, inside the 60 s share window.
    mockFetch.mockImplementationOnce(() => respond("tenant-a-door"));
    mockFetch.mockImplementationOnce(() => respond("tenant-b-door"));
    const { result, rerender } = renderHook(({ uid }) => useTenantDefaultSkill(SKILLS, uid), {
      initialProps: { uid: "uid-a" as string | null },
    });
    await waitFor(() => expect(result.current).toBe("id-a"));

    rerender({ uid: "uid-b" });
    await waitFor(() => expect(result.current).toBe("id-b"));
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("forgets the previous tenant's default the moment the user signs out", async () => {
    mockFetch.mockImplementation(() => respond("tenant-a-door"));
    const { result, rerender } = renderHook(({ uid }) => useTenantDefaultSkill(SKILLS, uid), {
      initialProps: { uid: "uid-a" as string | null },
    });
    await waitFor(() => expect(result.current).toBe("id-a"));

    rerender({ uid: null });
    expect(result.current).toBeNull();
  });
});
