import { describe, expect, it } from "vitest";

import {
  combineInstructions,
  parseInstructions,
  EMPTY_STRUCTURED,
  type StructuredInstructions,
} from "../structuredInstructions";

describe("parseInstructions", () => {
  it("splits ## sections into fields", () => {
    const md = [
      "## Goal",
      "",
      "Review PPA contracts.",
      "",
      "## Guidelines",
      "",
      "Be precise and cite clauses.",
      "",
      "## Constraints",
      "",
      "Never invent numbers.",
      "",
      "## Output format",
      "",
      "Bullet points.",
    ].join("\n");
    const f = parseInstructions(md);
    expect(f.goal).toBe("Review PPA contracts.");
    expect(f.guidelines).toBe("Be precise and cite clauses.");
    expect(f.constraints).toBe("Never invent numbers.");
    expect(f.outputFormat).toBe("Bullet points.");
    expect(f.additional).toBe("");
  });

  it("puts legacy free-form text (no headings) into additional", () => {
    const md = "You are a helpful contract assistant. Answer clearly.";
    const f = parseInstructions(md);
    expect(f.goal).toBe("");
    expect(f.additional).toBe(md);
  });

  it("keeps unrecognised headings verbatim in additional", () => {
    const md = ["## Goal", "", "Do the thing.", "", "## Tone", "", "Friendly."].join("\n");
    const f = parseInstructions(md);
    expect(f.goal).toBe("Do the thing.");
    expect(f.additional).toContain("## Tone");
    expect(f.additional).toContain("Friendly.");
  });

  it("recognises heading aliases (Output, Behaviour)", () => {
    const md = ["## Behaviour", "", "Be warm.", "", "## Output", "", "JSON."].join("\n");
    const f = parseInstructions(md);
    expect(f.guidelines).toBe("Be warm.");
    expect(f.outputFormat).toBe("JSON.");
  });

  it("handles empty input", () => {
    expect(parseInstructions("")).toEqual(EMPTY_STRUCTURED);
  });
});

describe("combineInstructions", () => {
  it("emits only non-empty sections", () => {
    const md = combineInstructions({
      ...EMPTY_STRUCTURED,
      goal: "Do X.",
      constraints: "Never Y.",
    });
    expect(md).toBe("## Goal\n\nDo X.\n\n## Constraints\n\nNever Y.");
  });

  it("appends additional verbatim at the end", () => {
    const md = combineInstructions({
      ...EMPTY_STRUCTURED,
      goal: "G.",
      additional: "## Custom\n\nkeep me",
    });
    expect(md).toBe("## Goal\n\nG.\n\n## Custom\n\nkeep me");
  });

  it("returns empty string for all-empty fields", () => {
    expect(combineInstructions(EMPTY_STRUCTURED)).toBe("");
  });
});

describe("round-trip stability", () => {
  const cases: StructuredInstructions[] = [
    { goal: "A", guidelines: "B", constraints: "C", outputFormat: "D", additional: "" },
    { ...EMPTY_STRUCTURED, goal: "just a goal" },
    { ...EMPTY_STRUCTURED, additional: "free form legacy prose" },
    { goal: "G", guidelines: "", constraints: "C", outputFormat: "", additional: "## Extra\n\ntail" },
  ];
  it("parse(combine(x)) === x for structured inputs", () => {
    for (const f of cases) {
      expect(parseInstructions(combineInstructions(f))).toEqual(f);
    }
  });
});
