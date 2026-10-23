import { afterEach, describe, expect, it, vi } from "vitest";

// The alias is read at module load, so each case re-imports with its env.
async function load(alias: string) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_PLATFORM_OWNER_ALIAS", alias);
  const { skillHref } = await import("@/components/navigation/skillHref");
  const owner = await import("@/lib/platformOwner");
  return { skillHref, ...owner };
}

describe("platform owner URL alias", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("shows the friendly alias for platform skills and resolves it back", async () => {
    const { skillHref, ownerIdFromUrlSegment } = await load("sunholo");
    expect(skillHref({ skillId: "x", slug: "web-researcher", ownerId: "aitana-platform" })).toBe(
      "/chat/@sunholo/web-researcher",
    );
    expect(ownerIdFromUrlSegment("sunholo")).toBe("aitana-platform");
    // Old links keep working.
    expect(ownerIdFromUrlSegment("aitana-platform")).toBe("aitana-platform");
    // A user's own skills are untouched.
    expect(skillHref({ skillId: "y", slug: "mine", ownerId: "uid-123" })).toBe("/chat/@uid-123/mine");
  });

  it("keeps the raw owner id when no alias is set", async () => {
    const { skillHref, ownerIdFromUrlSegment } = await load("");
    expect(skillHref({ skillId: "x", slug: "web-researcher", ownerId: "aitana-platform" })).toBe(
      "/chat/@aitana-platform/web-researcher",
    );
    expect(ownerIdFromUrlSegment("sunholo")).toBe("sunholo");
  });
});
