import { describe, it, expect } from "vitest";
import { parseA2uiSubmission } from "../a2uiSubmission";

describe("parseA2uiSubmission (6.11)", () => {
  it("parses the [a2ui:action] {json} form and humanizes fields", () => {
    const s = parseA2uiSubmission('[a2ui:savePreferences] {"tone":["neutral"],"style":["concise"],"verbosity":["short"]}');
    expect(s?.action).toBe("savePreferences");
    expect(s?.fields).toEqual([
      { key: "Tone", value: "neutral" },
      { key: "Style", value: "concise" },
      { key: "Verbosity", value: "short" },
    ]);
  });

  it("flattens arrays and formats booleans", () => {
    const s = parseA2uiSubmission('[a2ui:submit] {"regions":["EU","US"],"agreed":true}');
    expect(s?.fields).toEqual([
      { key: "Regions", value: "EU, US" },
      { key: "Agreed", value: "Yes" },
    ]);
  });

  it("returns null for ordinary messages", () => {
    expect(parseA2uiSubmission("what is in danish news today")).toBeNull();
    expect(parseA2uiSubmission("[a2ui:savePreferences] not-json")).toBeNull();
    expect(parseA2uiSubmission("")).toBeNull();
  });

  it("drops empty values", () => {
    const s = parseA2uiSubmission('[a2ui:x] {"a":"","b":"keep"}');
    expect(s?.fields).toEqual([{ key: "B", value: "keep" }]);
  });
});
