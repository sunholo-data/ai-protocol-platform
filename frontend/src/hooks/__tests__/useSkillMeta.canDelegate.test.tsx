// v6.23.0 — `canDelegate`, the front-door signal that decides whether the
// Workspace Home keeps a skill's example tiles up for the whole conversation.
//
// Why this needs real wire fixtures rather than a hand-made object: the field
// arrives under BOTH `skillMetadata` and `skill_metadata`, `discoverJobs` has a
// snake_case alias, and `subSkills` is a deprecated-but-live third form. Getting
// any of them wrong silently turns the persistence off (specialist behaviour on
// a front door) or on (tiles a skill can't route). Fixtures below mirror the
// real SKILL.md blocks in backend/skills/templates/.

import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

import { useSkillMeta } from "@/hooks/useSkillMeta";

function respond(body: unknown) {
  fetchWithAuth.mockResolvedValue({ ok: true, json: async () => body });
}

async function canDelegateFor(body: unknown): Promise<boolean> {
  respond(body);
  const { result } = renderHook(() => useSkillMeta("skill-1"));
  await waitFor(() => expect(result.current.loading).toBe(false));
  return result.current.canDelegate;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSkillMeta — canDelegate", () => {
  it("true for a curated front door (one-assistant shape)", async () => {
    expect(
      await canDelegateFor({
        skillMetadata: {
          delegation: {
            enabled: true,
            maxDepth: 2,
            discoverJobs: true,
            allow: [{ skill: "one-ppa-expert", floor: "auto" }],
          },
        },
      }),
    ).toBe(true);
  });

  it("true for a specialist that CAN hand off (one-ppa-expert shape)", async () => {
    // The rule follows the reason, not the skill name: this one delegates to
    // one-obligation-analysis, so its tiles lead somewhere.
    expect(
      await canDelegateFor({
        skillMetadata: {
          delegation: {
            enabled: true,
            maxDepth: 1,
            allow: [{ skill: "one-obligation-analysis", floor: "confirm" }],
          },
        },
      }),
    ).toBe(true);
  });

  it("true for a generic door with discoverJobs and an EMPTY allow list", async () => {
    // v6.8.0 8.3: a door can reach any accessible `job:true` skill with no allow
    // entry at all. Gating on `allow.length` alone would misread it as a
    // specialist.
    expect(
      await canDelegateFor({
        skillMetadata: { delegation: { enabled: true, discoverJobs: true, allow: [] } },
      }),
    ).toBe(true);
  });

  it("true for the snake_case discover_jobs alias", async () => {
    expect(
      await canDelegateFor({
        skill_metadata: { delegation: { enabled: true, discover_jobs: true, allow: [] } },
      }),
    ).toBe(true);
  });

  it("true for the DEPRECATED subSkills form, which still delegates", async () => {
    expect(await canDelegateFor({ skillMetadata: { subSkills: ["one-ppa-expert"] } })).toBe(true);
  });

  it("false for a job skill with no delegation at all (one-bigquery shape)", async () => {
    expect(await canDelegateFor({ skillMetadata: { tools: ["bq_query"] } })).toBe(false);
  });

  it("false when delegation is present but disabled", async () => {
    expect(
      await canDelegateFor({
        skillMetadata: { delegation: { enabled: false, allow: [{ skill: "x" }] } },
      }),
    ).toBe(false);
  });

  it("false when enabled with neither allow entries nor discoverJobs", async () => {
    expect(
      await canDelegateFor({ skillMetadata: { delegation: { enabled: true, allow: [] } } }),
    ).toBe(false);
  });

  it("false when the skills API fails — degrade to specialist, never over-promise", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 500 });
    const { result } = renderHook(() => useSkillMeta("skill-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.canDelegate).toBe(false);
  });
});
