import { describe, expect, it } from "vitest";

import { computeIncludedDocIds } from "@/lib/docContext";
import type { DocTabData } from "@/components/doc-browser/DocTab";

function tab(id: string, included: boolean): DocTabData {
  return { id, filename: `${id}.docx`, format: "docx", included };
}

describe("computeIncludedDocIds (multi-doc-context-fix 1.22 D2)", () => {
  it("returns an empty array when there are no tabs", () => {
    expect(computeIncludedDocIds([])).toEqual([]);
  });

  it("returns an empty array when no tab is included", () => {
    expect(
      computeIncludedDocIds([tab("a", false), tab("b", false)]),
    ).toEqual([]);
  });

  it("returns just one id when only one tab is included", () => {
    expect(
      computeIncludedDocIds([tab("a", true), tab("b", false)]),
    ).toEqual(["a"]);
  });

  it("LOCKS the multi-doc contract: TWO included tabs => BOTH ids in order", () => {
    // The reported bug pattern: user opens privacy notice, then claim
    // incident; both tabs ticked. The chat page's includedDocIds derivation
    // must surface BOTH ids — anything less and the backend never receives
    // the second doc to load. This test pins the contract end-to-end with
    // the backend multi-doc tests in tests/unit/test_session_callbacks.py.
    expect(
      computeIncludedDocIds([
        tab("privacy", true),
        tab("claim", true),
      ]),
    ).toEqual(["privacy", "claim"]);
  });

  it("preserves tab order so the agent reads docs in the order the user opened them", () => {
    expect(
      computeIncludedDocIds([
        tab("z", true),
        tab("a", true),
        tab("m", true),
      ]),
    ).toEqual(["z", "a", "m"]);
  });

  it("skips excluded tabs while preserving order of included ones", () => {
    expect(
      computeIncludedDocIds([
        tab("a", true),
        tab("b", false),
        tab("c", true),
      ]),
    ).toEqual(["a", "c"]);
  });
});
