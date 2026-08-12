// PPA-COMPARE-LAUNCHER M2 — launcher gating tests.
// v6.23.0 WORKSPACE-HOME-PERSISTENCE: the artifact/workspace-surface gates were
// deleted, so the "hides once results exist" cases became their inverse — they
// are now the regression guards for Dana's most-repeated UAT complaint.

import { describe, expect, it } from "vitest";
import { shouldShowCompareLauncher } from "@/lib/compareLauncher";

/** A launcher-capable skill (one-doc-compare shape). */
const compareSkill = {
  optedIn: true,
  allowCompare: true,
  allowObligations: false,
};

describe("shouldShowCompareLauncher", () => {
  it("shows for an opted-in compare skill", () => {
    expect(shouldShowCompareLauncher(compareSkill)).toBe(true);
  });

  it("shows for an opted-in obligations-only skill (single-doc launcher)", () => {
    expect(
      shouldShowCompareLauncher({
        ...compareSkill,
        allowCompare: false,
        allowObligations: true,
      }),
    ).toBe(true);
  });

  it("hides for a skill that is NOT opted in", () => {
    expect(shouldShowCompareLauncher({ ...compareSkill, optedIn: false })).toBe(false);
  });

  it("hides for an opted-in skill with NO launcher-capable tool (elicitation-only front door)", () => {
    // 2026-07-21 regression: 44c426c granted the ONE front door
    // allow_action_triggered_runs purely for elicitation-form submits (gate 8).
    // The door owns neither compare_ppa_contracts nor map_ppa_obligations, so
    // the launcher must NOT hijack its Workspace Home — the first-look prompt
    // cards picker renders instead.
    expect(
      shouldShowCompareLauncher({
        ...compareSkill,
        allowCompare: false,
        allowObligations: false,
      }),
    ).toBe(false);
  });

  it("STILL shows once result artifacts exist — the v6.23.0 regression guard", () => {
    // Dana, 2026-08-06 UAT (raised 4×): "In the same chat, if you want to keep
    // the same conversation but use a new skill, you cannot do that in the
    // Workspace anymore." Results no longer evict the launcher; they open their
    // own Result tab beside a permanent Home. Visibility must therefore be a
    // pure function of the SKILL's capabilities — nothing about session state.
    expect(shouldShowCompareLauncher(compareSkill)).toBe(true);
  });

  it("depends only on skill capability, never on session state", () => {
    // Guards against a future re-introduction of an artifactCount /
    // workspaceHasContent gate by asserting the input surface itself: extra
    // session-shaped keys are ignored, they cannot change the answer.
    const withSessionNoise = {
      ...compareSkill,
      artifactCount: 7,
      workspaceHasContent: true,
      isFreshChat: false,
    } as unknown as Parameters<typeof shouldShowCompareLauncher>[0];
    expect(shouldShowCompareLauncher(withSessionNoise)).toBe(true);
  });
});
