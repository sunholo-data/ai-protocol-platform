// Skill Studio — proposal contract + pure draft-patch applier.
//
// The `skill-authoring-assistant` copilot replies in prose AND emits fenced
// ```json blocks of the shape `{ "proposals": [ { kind, label, value?, spec? } ] }`.
// `parseProposals` extracts those blocks; `applyProposal` maps ONE proposal onto
// a NEW draft SkillConfig object (immutable — never mutates its input).
//
// The 9 kinds and their draft-patch semantics are pinned here (see
// docs/design/v6.6.0/one-app-fork-convergence-sprint.md M3). This is the single
// source of truth the AuthoringCopilot and the Studio form share.

import type {
  SkillMetadata,
  SkillPersona,
  SkillVoiceConfig,
  WelcomeConfig,
} from "@/types/skill";

/** The nine proposal kinds the authoring copilot may emit. */
export type ProposalKind =
  | "set_display_name"
  | "set_description"
  | "set_instructions"
  | "set_model_tier"
  | "add_sub_skill"
  | "set_tools"
  | "set_persona"
  | "add_a2ui_surface"
  | "set_welcome";

/** A single authoring proposal. `value` xor `spec` carries the payload
 * depending on `kind` (string / string[] for value-kinds; a partial object
 * for `set_persona` / `set_welcome`). `label` is the human-readable card
 * title. Unknown extra fields are tolerated (forward-compat). */
export interface Proposal {
  kind: ProposalKind;
  label: string;
  value?: string | string[];
  spec?: Record<string, unknown>;
}

/** The mutable slice of a SkillConfig the Studio form + copilot edit. This is a
 * structural subset of `Skill` (frontend/src/types/skill.ts) — deliberately
 * loose so a "new" draft can start from an empty object without every required
 * field of the full wire type. */
export interface StudioDraft {
  skillId?: string;
  /** Owner uid of the loaded skill. "aitana-platform" ⇒ platform-owned
   * (read-only unless you're a platform admin) — the editor offers Fork. */
  ownerId?: string;
  name?: string;
  displayName?: string;
  description?: string;
  instructions?: string;
  avatar?: string;
  skillMetadata?: Partial<SkillMetadata>;
  persona?: Partial<SkillPersona>;
  welcome?: Partial<WelcomeConfig>;
  accessControl?: Record<string, unknown>;
  [key: string]: unknown;
}

const PROPOSAL_KINDS: ReadonlySet<string> = new Set<ProposalKind>([
  "set_display_name",
  "set_description",
  "set_instructions",
  "set_model_tier",
  "add_sub_skill",
  "set_tools",
  "set_persona",
  "add_a2ui_surface",
  "set_welcome",
]);

function isProposal(v: unknown): v is Proposal {
  if (!v || typeof v !== "object") return false;
  const obj = v as Record<string, unknown>;
  return (
    typeof obj.kind === "string" &&
    PROPOSAL_KINDS.has(obj.kind) &&
    typeof obj.label === "string"
  );
}

/**
 * Extract every ```json fenced block from `text`, JSON-parse each, and collect
 * the `proposals` array from any block that has one. Malformed JSON blocks are
 * silently skipped (never throws). Multiple blocks are all consumed. Individual
 * array entries that don't match the Proposal shape are dropped.
 */
export function parseProposals(text: string): Proposal[] {
  if (!text) return [];
  const out: Proposal[] = [];
  // Match ```json … ``` fences (case-insensitive language tag, tolerant of
  // surrounding whitespace). `[\s\S]*?` is non-greedy so adjacent blocks don't
  // merge into one match.
  const fenceRe = /```json\s*\n?([\s\S]*?)```/gi;
  let match: RegExpExecArray | null;
  while ((match = fenceRe.exec(text)) !== null) {
    const body = match[1]?.trim();
    if (!body) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      continue; // malformed block — ignore, don't throw
    }
    if (!parsed || typeof parsed !== "object") continue;
    const proposals = (parsed as { proposals?: unknown }).proposals;
    if (!Array.isArray(proposals)) continue;
    for (const p of proposals) {
      if (isProposal(p)) out.push(p);
    }
  }
  return out;
}

function dedupePush(list: string[] | undefined, value: string): string[] {
  const base = list ?? [];
  return base.includes(value) ? base : [...base, value];
}

/**
 * Apply ONE proposal to `draft`, returning a NEW draft object. Never mutates
 * the input (structural-share where untouched). Unknown kinds return the draft
 * unchanged (defensive — parseProposals already filters, but keep pure).
 */
export function applyProposal(draft: StudioDraft, proposal: Proposal): StudioDraft {
  const next: StudioDraft = { ...draft };
  const { kind, value, spec } = proposal;

  switch (kind) {
    case "set_display_name":
      if (typeof value === "string") next.displayName = value;
      return next;

    case "set_description":
      if (typeof value === "string") next.description = value;
      return next;

    case "set_instructions":
      if (typeof value === "string") next.instructions = value;
      return next;

    case "set_model_tier":
      if (value === "lite" || value === "pro" || value === "smart") {
        next.skillMetadata = { ...next.skillMetadata, model: value };
      }
      return next;

    case "add_sub_skill":
      if (typeof value === "string") {
        next.skillMetadata = {
          ...next.skillMetadata,
          subSkills: dedupePush(next.skillMetadata?.subSkills, value),
        };
      }
      return next;

    case "set_tools":
      if (Array.isArray(value)) {
        next.skillMetadata = {
          ...next.skillMetadata,
          tools: [...value],
        };
      }
      return next;

    case "set_persona":
      if (spec && typeof spec === "object") {
        const specVoice = (spec as { voice?: unknown }).voice;
        const mergedVoice =
          specVoice && typeof specVoice === "object"
            ? ({ ...next.persona?.voice, ...(specVoice as SkillVoiceConfig) } as SkillVoiceConfig)
            : next.persona?.voice;
        next.persona = {
          ...next.persona,
          ...(spec as Partial<SkillPersona>),
          ...(mergedVoice ? { voice: mergedVoice } : {}),
        };
      }
      return next;

    case "add_a2ui_surface":
      if (value === "workspace" || value === "sidebar" || value === "modal") {
        next.skillMetadata = {
          ...next.skillMetadata,
          toolConfigs: {
            ...next.skillMetadata?.toolConfigs,
            a2ui: { default_surface: value, default_update_mode: "replace" },
          },
        };
      }
      return next;

    case "set_welcome":
      if (spec && typeof spec === "object") {
        next.welcome = { ...next.welcome, ...(spec as Partial<WelcomeConfig>) };
      }
      return next;

    default:
      return next;
  }
}
