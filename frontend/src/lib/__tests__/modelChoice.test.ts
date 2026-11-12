// The composer's Model menu: what it shows for a skill (docs/design/v6.42.0/model-choice.md).
import { describe, expect, it } from "vitest";
import { buildModelOptions, choiceLabel, type ModelMenu, type SkillModelInfo } from "@/lib/modelChoice";

const GLOBAL: ModelMenu = {
  offered: "all",
  levels: {
    fast: { model: "gemini-3-5-flash-lite", label: "Gemini 3.5 Flash-Lite" },
    thorough: { model: "claude-opus-5-5", label: "Claude Opus 5.5" },
  },
  models: [
    { id: "claude-opus-5-5", label: "Claude Opus 5.5", note: "deepest reasoning" },
    { id: "gemini-3-8-flash", label: "Gemini 3.8 Flash", note: "balanced" },
  ],
  labels: { pro: "Gemini 3.8 Flash", lite: "Gemini 3.5 Flash-Lite", "claude-haiku-5-5": "Claude Haiku 5.5", "gemini-3-8-flash": "Gemini 3.8 Flash", "claude-opus-5-5": "Claude Opus 5.5" },
  resolved: { pro: "gemini-3-8-flash", lite: "gemini-3-5-flash-lite", smart: "claude-opus-5-5" },
};

// EU-only: smart and pro are the same model.
const EU: ModelMenu = {
  ...GLOBAL,
  levels: {
    fast: { model: "gemini-3-5-flash-lite-eu", label: "Gemini 3.5 Flash-Lite" },
    thorough: { model: "gemini-3-8-flash-eu", label: "Gemini 3.8 Flash" },
  },
  models: [{ id: "gemini-3-8-flash-eu", label: "Gemini 3.8 Flash" }],
  resolved: { pro: "gemini-3-8-flash-eu", lite: "gemini-3-5-flash-lite-eu", smart: "gemini-3-8-flash-eu" },
};

const writer: SkillModelInfo = { name: "Writer", model: "pro", thinking: "dynamic", modelChoice: true, canDelegate: false };
const values = (o: { value: string }[]) => o.map((x) => x.value);

describe("buildModelOptions", () => {
  it("offers Auto, Fast and Thorough, with the real model names", () => {
    const o = buildModelOptions(GLOBAL, writer, false);
    expect(o.show).toBe(true);
    expect(values(o.levels)).toEqual(["auto", "fast", "thorough"]);
    expect(o.levels[0].detail).toBe("Writer uses Gemini 3.8 Flash");
    expect(o.levels[2].detail).toContain("Claude Opus 5.5");
    expect(o.models).toEqual([]);
  });

  it("lists specific models only for people who opted in", () => {
    expect(values(buildModelOptions(GLOBAL, writer, true).models)).toEqual(["model:claude-opus-5-5", "model:gemini-3-8-flash"]);
  });

  it("hides a level that would run exactly like Auto (Thorough on an EU-only deployment)", () => {
    expect(values(buildModelOptions(EU, writer, false).levels)).toEqual(["auto", "fast"]);
  });

  it("keeps Thorough on EU for a skill that thinks less than it could", () => {
    expect(values(buildModelOptions(EU, { ...writer, thinking: "low" }, false).levels)).toContain("thorough");
  });

  it("a front door offers every level: the specialists it hands to follow the pick", () => {
    const door = { ...writer, name: "Sunholo", model: "claude-haiku-5-5", modelChoice: false, canDelegate: true };
    const o = buildModelOptions(EU, door, false);
    expect(o.locked).toBeNull();
    expect(values(o.levels)).toEqual(["auto", "fast", "thorough"]);
    expect(o.levels[0].detail).toMatch(/each assistant/i);
  });

  it("a skill that keeps its model says so instead of offering choices", () => {
    const o = buildModelOptions(GLOBAL, { ...writer, name: "GPT", modelChoice: false }, true);
    expect(o.locked).toBe("GPT always uses Gemini 3.8 Flash.");
    expect(o.levels).toEqual([]);
  });

  it("shows nothing when the deployment turns it off, or before the menu or skill load", () => {
    expect(buildModelOptions({ ...GLOBAL, offered: "off" }, writer, true).show).toBe(false);
    expect(buildModelOptions(null, writer, true).show).toBe(false);
    expect(buildModelOptions(GLOBAL, null, true).show).toBe(false);
  });

  it("shows no row when there is nothing to choose between", () => {
    const liteSkill = { ...writer, model: "lite" };
    const menu = { ...EU, levels: { fast: EU.levels.fast } };
    expect(buildModelOptions(menu, liteSkill, false).show).toBe(false);
  });
});

describe("choiceLabel", () => {
  it("names the pick for the chip, nothing for Auto", () => {
    expect(choiceLabel(GLOBAL, "auto")).toBeNull();
    expect(choiceLabel(GLOBAL, undefined)).toBeNull();
    expect(choiceLabel(GLOBAL, "thorough")).toBe("Thorough");
    expect(choiceLabel(GLOBAL, "model:claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(choiceLabel(GLOBAL, "model:unknown-id")).toBe("unknown-id");
  });
});
