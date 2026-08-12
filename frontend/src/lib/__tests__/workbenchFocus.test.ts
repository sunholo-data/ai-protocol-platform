// v6.23.0 B4 — auto-focus rule tests.
//
// The blink case (third test) is THE regression guard: it is the sequence that
// the old "compare against the previous render" implementation got wrong, and
// the one that made a deliberate Workspace click appear not to register.

import { describe, expect, it } from "vitest";
import { forgetFocusedResult, nextFocusedResult } from "@/lib/workbenchFocus";

describe("nextFocusedResult", () => {
  it("focuses the first result to arrive", () => {
    const seen = new Set<string>();
    expect(nextFocusedResult(["prices"], seen)).toBe("prices");
  });

  it("does not re-focus a result that is merely still present", () => {
    const seen = new Set<string>();
    nextFocusedResult(["prices"], seen);
    // Steady state — the same list every render must never move focus, or the
    // user could not navigate anywhere at all.
    expect(nextFocusedResult(["prices"], seen)).toBeNull();
    expect(nextFocusedResult(["prices"], seen)).toBeNull();
  });

  it("does NOT re-focus after a surface blinks out and returns — the B4 guard", () => {
    const seen = new Set<string>();
    expect(nextFocusedResult(["prices"], seen)).toBe("prices");

    // The blink: `listArtifacts()` skips surfaces whose `state.surface` is null,
    // so a re-registration removes the artifact for one render...
    expect(nextFocusedResult([], seen)).toBeNull();
    // ...and then it is back. Under the old semantics `prevIds` had been
    // overwritten with the empty set here, so this returned "prices" and stole
    // focus from wherever the user had navigated. It must return null.
    expect(nextFocusedResult(["prices"], seen)).toBeNull();
  });

  it("focuses the LAST of several results arriving together", () => {
    // extract → extract → compare: focus tracks the latest result, not the first.
    const seen = new Set<string>();
    expect(nextFocusedResult(["clauses-a", "clauses-b", "comparison"], seen)).toBe("comparison");
  });

  it("focuses a genuinely new result while an older one is still open", () => {
    const seen = new Set<string>();
    nextFocusedResult(["prices"], seen);
    expect(nextFocusedResult(["prices", "sources"], seen)).toBe("sources");
  });

  it("re-focuses a result the user explicitly CLOSED and the agent re-emitted", () => {
    // A close is deliberate, unlike a blink — so the latch is released and the
    // re-emission is treated as the new arrival it is.
    const seen = new Set<string>();
    nextFocusedResult(["prices"], seen);
    forgetFocusedResult("prices", seen);
    expect(nextFocusedResult(["prices"], seen)).toBe("prices");
  });

  it("survives an empty workbench without focusing anything", () => {
    const seen = new Set<string>();
    expect(nextFocusedResult([], seen)).toBeNull();
  });
});
