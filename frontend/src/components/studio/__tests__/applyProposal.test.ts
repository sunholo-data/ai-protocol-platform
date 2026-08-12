import { describe, expect, it } from "vitest";
import {
  applyProposal,
  parseProposals,
  type Proposal,
  type StudioDraft,
} from "@/components/studio/applyProposal";

function base(): StudioDraft {
  return {
    displayName: "Old",
    description: "old desc",
    skillMetadata: { model: "lite", tools: ["a"], subSkills: ["x"], toolConfigs: {} },
    persona: { interactionStyle: "concise", voice: { rate: 1 } },
    welcome: {},
  };
}

describe("applyProposal — 9 kinds", () => {
  it("set_display_name → draft.displayName", () => {
    const out = applyProposal(base(), {
      kind: "set_display_name",
      label: "Set name",
      value: "New Name",
    });
    expect(out.displayName).toBe("New Name");
  });

  it("set_description → draft.description", () => {
    const out = applyProposal(base(), {
      kind: "set_description",
      label: "Set desc",
      value: "New description",
    });
    expect(out.description).toBe("New description");
  });

  it("set_instructions → draft.instructions", () => {
    const out = applyProposal(base(), {
      kind: "set_instructions",
      label: "Set instructions",
      value: "Follow these steps.",
    });
    expect(out.instructions).toBe("Follow these steps.");
  });

  it("set_model_tier → draft.skillMetadata.model", () => {
    const out = applyProposal(base(), {
      kind: "set_model_tier",
      label: "Use smart",
      value: "smart",
    });
    expect(out.skillMetadata?.model).toBe("smart");
  });

  it("set_model_tier accepts pro", () => {
    const out = applyProposal(base(), {
      kind: "set_model_tier",
      label: "Use pro",
      value: "pro",
    });
    expect(out.skillMetadata?.model).toBe("pro");
  });

  it("set_model_tier ignores unknown tier values", () => {
    const out = applyProposal(base(), {
      kind: "set_model_tier",
      label: "bad",
      value: "turbo",
    });
    expect(out.skillMetadata?.model).toBe("lite");
  });

  it("add_sub_skill → pushes to subSkills with dedupe", () => {
    const out = applyProposal(base(), {
      kind: "add_sub_skill",
      label: "add y",
      value: "y",
    });
    expect(out.skillMetadata?.subSkills).toEqual(["x", "y"]);

    const dupe = applyProposal(out, {
      kind: "add_sub_skill",
      label: "add y again",
      value: "y",
    });
    expect(dupe.skillMetadata?.subSkills).toEqual(["x", "y"]);
  });

  it("set_tools → replaces skillMetadata.tools", () => {
    const out = applyProposal(base(), {
      kind: "set_tools",
      label: "set tools",
      value: ["search", "code"],
    });
    expect(out.skillMetadata?.tools).toEqual(["search", "code"]);
  });

  it("set_persona → merges spec into persona (deep-merges voice)", () => {
    const out = applyProposal(base(), {
      kind: "set_persona",
      label: "warm",
      spec: { interactionStyle: "warm", voice: { ttsVoice: "es-ES-Wavenet-C" } },
    });
    expect(out.persona?.interactionStyle).toBe("warm");
    // existing voice.rate preserved, new ttsVoice merged in
    expect(out.persona?.voice).toEqual({ rate: 1, ttsVoice: "es-ES-Wavenet-C" });
  });

  it("add_a2ui_surface → sets skillMetadata.toolConfigs.a2ui", () => {
    const out = applyProposal(base(), {
      kind: "add_a2ui_surface",
      label: "workspace surface",
      value: "workspace",
    });
    expect(out.skillMetadata?.toolConfigs?.a2ui).toEqual({
      default_surface: "workspace",
      default_update_mode: "replace",
    });
  });

  it("set_welcome → merges spec into welcome", () => {
    const out = applyProposal(base(), {
      kind: "set_welcome",
      label: "greeting",
      spec: { introMessage: "Hi there" },
    });
    expect(out.welcome?.introMessage).toBe("Hi there");
  });
});

describe("applyProposal — immutability", () => {
  it("returns a NEW object and does not mutate the input", () => {
    const draft = base();
    const snapshot = JSON.parse(JSON.stringify(draft));
    const out = applyProposal(draft, {
      kind: "set_display_name",
      label: "x",
      value: "Changed",
    });
    expect(out).not.toBe(draft);
    // original untouched
    expect(draft).toEqual(snapshot);
    expect(draft.displayName).toBe("Old");
  });

  it("does not mutate nested skillMetadata on set_tools", () => {
    const draft = base();
    const originalTools = draft.skillMetadata?.tools;
    applyProposal(draft, { kind: "set_tools", label: "x", value: ["z"] });
    expect(draft.skillMetadata?.tools).toBe(originalTools);
    expect(draft.skillMetadata?.tools).toEqual(["a"]);
  });
});

describe("parseProposals", () => {
  it("extracts a single json block with a proposals array", () => {
    const text = [
      "Here is my plan.",
      "```json",
      JSON.stringify({
        proposals: [
          { kind: "set_display_name", label: "Name it", value: "Reviewer" },
        ],
      }),
      "```",
      "Apply if you like.",
    ].join("\n");
    const out = parseProposals(text);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ kind: "set_display_name", value: "Reviewer" });
  });

  it("ignores malformed JSON blocks without throwing", () => {
    const text = "```json\n{ not valid json ,, }\n```";
    expect(() => parseProposals(text)).not.toThrow();
    expect(parseProposals(text)).toEqual([]);
  });

  it("handles multiple blocks and concatenates proposals", () => {
    const text = [
      "```json",
      JSON.stringify({ proposals: [{ kind: "set_description", label: "d", value: "one" }] }),
      "```",
      "then",
      "```json",
      JSON.stringify({ proposals: [{ kind: "set_tools", label: "t", value: ["x"] }] }),
      "```",
    ].join("\n");
    const out = parseProposals(text);
    expect(out.map((p) => p.kind)).toEqual(["set_description", "set_tools"]);
  });

  it("drops array entries that are not valid proposals", () => {
    const text = [
      "```json",
      JSON.stringify({
        proposals: [
          { kind: "bogus_kind", label: "no", value: "x" },
          { kind: "set_description", label: "yes", value: "keep" },
          { label: "missing kind" },
        ],
      }),
      "```",
    ].join("\n");
    const out = parseProposals(text);
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe("set_description");
  });

  it("returns [] when there are no json blocks", () => {
    expect(parseProposals("just prose, no fences")).toEqual([]);
  });

  it("ignores blocks without a proposals array", () => {
    const text = "```json\n{ \"foo\": 1 }\n```";
    expect(parseProposals(text)).toEqual([]);
  });
});

// Type-only usage so the Proposal export is exercised.
const _typecheck: Proposal = { kind: "set_welcome", label: "x", spec: {} };
void _typecheck;
