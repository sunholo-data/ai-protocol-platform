import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { useUserSkills } from "@/hooks/useUserSkills";
import type { Skill } from "@/types/skill";

const mockFetch = fetchWithAuth as ReturnType<typeof vi.fn>;

function makeResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
  } as Response);
}

const SKILL: Skill = {
  name: "research",
  description: "",
  instructions: "",
  skillMetadata: {
    author: "test",
    version: "1.0",
    model: "gemini-2.5-flash",
    tools: [],
    toolConfigs: {},
    subSkills: [],
  },
  references: {},
  assets: {},
  skillId: "skill-1",
  displayName: "Research",
  avatar: "",
  ownerEmail: "u@example.com",
  ownerId: "uid-1",
  accessControl: { type: "private" },
  protocols: {
    mcp: { enabled: false },
    a2a: { enabled: false },
    agui: { enabled: true },
    a2ui: { enabled: false },
    mcpApps: { enabled: false },
  },
  initialMessage: "",
  tags: [],
  featured: false,
  usageCount: 0,
  createdAt: 0,
  updatedAt: 0,
};

beforeEach(() => {
  mockFetch.mockReset();
});

function ownSkill(overrides: Partial<Skill> = {}): Skill {
  return { ...SKILL, ownerId: "uid-1", skillId: "own-1", name: "own", ...overrides };
}

function platformSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    ...SKILL,
    ownerId: "aitana-platform",
    skillId: "plat-1",
    name: "general-assistant",
    accessControl: { type: "public" },
    ...overrides,
  };
}

// Mock implementation that routes by URL — own vs platform vs unknown.
function makeRouter(own: Skill[], platform: Skill[]) {
  return (input: string) => {
    if (input.includes("ownerId=uid-1")) return makeResponse(own);
    if (input.includes("ownerId=aitana-platform")) return makeResponse(platform);
    return makeResponse([], false);
  };
}

describe("useUserSkills", () => {
  it("returns empty list and does not fetch when uid is null", () => {
    const { result } = renderHook(() => useUserSkills(null));
    expect(result.current.skills).toEqual([]);
    expect(result.current.isLoading).toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("fetches own + platform skills in parallel and merges them", async () => {
    const own = [ownSkill({ skillId: "own-1" })];
    const platform = [
      platformSkill({ skillId: "plat-1", name: "general-assistant" }),
      platformSkill({ skillId: "plat-2", name: "code-assistant" }),
    ];
    mockFetch.mockImplementation(makeRouter(own, platform));

    const { result } = renderHook(() => useUserSkills("uid-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/skills?ownerId=uid-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/proxy/api/skills?ownerId=aitana-platform",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    // Own skills come first, platform skills last.
    expect(result.current.skills.map((s) => s.skillId)).toEqual([
      "own-1",
      "plat-1",
      "plat-2",
    ]);
  });

  it("merges platform skills even when the user has none of their own", async () => {
    const platform = [platformSkill({ skillId: "plat-1" })];
    mockFetch.mockImplementation(makeRouter([], platform));

    const { result } = renderHook(() => useUserSkills("uid-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.skills.map((s) => s.skillId)).toEqual(["plat-1"]);
  });

  it("dedupes platform skills that overlap with the user's own list (by skillId)", async () => {
    // Edge case: same skillId in both responses (e.g. backend leaks a config
    // into both queries). Own copy wins, no duplicate row.
    const shared = ownSkill({ skillId: "shared", name: "shared-skill" });
    mockFetch.mockImplementation(makeRouter([shared], [{ ...shared, name: "platform-shared" }]));

    const { result } = renderHook(() => useUserSkills("uid-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.skills).toHaveLength(1);
    expect(result.current.skills[0].name).toBe("shared-skill");
  });

  it("sets error and clears skills when either fetch fails", async () => {
    mockFetch.mockImplementation((input: string) => {
      if (input.includes("ownerId=uid-1")) return makeResponse({}, false);
      return makeResponse([platformSkill()]);
    });

    const { result } = renderHook(() => useUserSkills("uid-1"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBe("Could not load skills.");
    expect(result.current.skills).toEqual([]);
  });

  it("aborts in-flight request when uid changes", async () => {
    mockFetch.mockReturnValue(new Promise(() => {})); // never resolves
    const { rerender, unmount } = renderHook(({ uid }) => useUserSkills(uid), {
      initialProps: { uid: "uid-1" as string | null },
    });
    // 2 fetches per uid (own + platform).
    expect(mockFetch).toHaveBeenCalledTimes(2);
    const firstSignal = (mockFetch.mock.calls[0][1] as { signal: AbortSignal }).signal;
    expect(firstSignal.aborted).toBe(false);

    rerender({ uid: "uid-2" });
    expect(firstSignal.aborted).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(4);

    unmount();
  });
});
