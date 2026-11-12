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
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/skills?ownerId=uid-1");
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/skills?ownerId=aitana-platform");
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

  it("a uid change fetches that uid's skills and ignores the previous uid's late result", async () => {
    let releaseFirst!: (r: Response) => void;
    mockFetch.mockImplementation((url: string) =>
      url.endsWith("uid-1")
        ? new Promise<Response>((r) => (releaseFirst = r))
        : makeResponse(url.endsWith("uid-2") ? [ownSkill({ skillId: "own-2" })] : []),
    );
    const { result, rerender } = renderHook(({ uid }) => useUserSkills(uid), {
      initialProps: { uid: "uid-1" as string | null },
    });
    rerender({ uid: "uid-2" });
    await waitFor(() => expect(result.current.skills.map((s) => s.skillId)).toEqual(["own-2"]));
    // Each uid gets its own platform list (it is filtered by the caller's
    // access), so 4 requests: own + platform, per uid.
    expect(mockFetch).toHaveBeenCalledTimes(4);
    releaseFirst({ ok: true, json: () => Promise.resolve([ownSkill({ skillId: "own-1" })]) } as Response);
    await new Promise((r) => setTimeout(r, 0));
    expect(result.current.skills.map((s) => s.skillId)).toEqual(["own-2"]);
  });

  it("several readers on one page share one pair of requests", async () => {
    mockFetch.mockImplementation(makeRouter([ownSkill({ skillId: "own-1" })], []));
    const a = renderHook(() => useUserSkills("uid-1"));
    const b = renderHook(() => useUserSkills("uid-1"));
    const c = renderHook(() => useUserSkills("uid-1"));
    await waitFor(() => expect(c.result.current.isLoading).toBe(false));
    expect(a.result.current.skills).toEqual(b.result.current.skills);
    expect(mockFetch).toHaveBeenCalledTimes(2); // was 6 (ShellChrome + 2× useProjectChatHref)
  });

  it("never shares one viewer's platform list with the next viewer", async () => {
    // The platform list is filtered by the CALLER's access. Two people signing
    // in on one tab inside the share window must each get their own fetch.
    mockFetch.mockImplementation((input: string) =>
      input.includes("aitana-platform") ? makeResponse([platformSkill()]) : makeResponse([]),
    );
    const first = renderHook(() => useUserSkills("uid-a"));
    await waitFor(() => expect(first.result.current.isLoading).toBe(false));
    const second = renderHook(() => useUserSkills("uid-b"));
    await waitFor(() => expect(second.result.current.isLoading).toBe(false));

    const platformCalls = mockFetch.mock.calls.filter(([url]) => String(url).includes("aitana-platform"));
    expect(platformCalls).toHaveLength(2);
  });
});
