"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { DirectLauncherConfig } from "@/components/workspace/DirectLaunchers";
import { localizedField, useLocale } from "@/i18n";
import { skillGreeting } from "@/hooks/useSkillText";
import type { SkillLocales, WelcomeConfig } from "@/types/skill";

interface SkillMeta {
  /** In the person's language (SKILL.md `locales:`), else the base name. */
  displayName: string;
  /** The skill's avatar (persona.avatar) — a /public path or URL. Shown on the
   * bot chat bubble so the speaking skill matches its switcher logo, letting
   * users tell which agent produced a message at a glance. Null → brand mark. */
  avatar: string | null;
  ownerId: string | null;
  slug: string | null;
  /** MCP server IDs this skill is configured to use, sourced from
   * skillMetadata.toolConfigs.mcp.servers. Empty array if none. The chat
   * page passes this to MessageBubble so MCPAppToolCallRouter can decide
   * which tool calls have a UI surface. */
  mcpServerIds: readonly string[];
  /** Skill's initialMessage (legacy) — falls back when welcome.introMessage
   * unset. v6.4.0 4.5 SKILL-ONBOARDING M1 (the AI-greeting source). In the
   * person's language when the skill translates it. */
  initialMessage: string;
  /** The greeting a fresh chat shows, in the person's language: see
   * `skillGreeting` (a translated initial_message → welcome.introMessage →
   * initialMessage). Null when the skill has none. */
  greeting: string | null;
  /** v6.4.0 4.5 SKILL-ONBOARDING welcome block — intro_message + example
   * documents + sidebar bucket browser. Null when skill omits the block. */
  welcome: WelcomeConfig | null;
  /** Model tier the skill runs on (e.g. "smart"/"pro"/"lite"). "" if unknown.
   * Surfaced in the Activity panel's context row. */
  model: string;
  /** Read-aloud voice config summary for the Activity context row. */
  voice: { enabled: boolean; language: string | null } | null;
  /** A2UI tool-config flags (toolConfigs.a2ui). Drives the workbench Compare
   * launcher: `allowActionTriggeredRuns` gates whether a launcher click can
   * fire a `start_compare` action through surface-action-run (vs. the
   * chat-intent fallback). Defaults to false when the skill omits the block. */
  a2ui: { allowActionTriggeredRuns: boolean; directLaunchers?: readonly DirectLauncherConfig[] };
  /** The skill's configured tool names (skillMetadata.tools). Empty when
   * unknown. Drives which workbench-launcher affordances appear — the compare
   * button needs `compare_ppa_contracts`, the analyze-obligations button needs
   * `map_ppa_obligations`. */
  tools: readonly string[];
  /** v6.23.0 — the skill can hand off to a specialist (delegation.enabled with a
   * non-empty allow list), i.e. it is a front door rather than a specialist.
   * Gates whether the Workspace Home keeps its example tiles up for the whole
   * conversation. See extractCanDelegate. */
  canDelegate: boolean;
  /** The skill's `thinking:` depth (off | low | dynamic). */
  thinking: string;
  /** False: the skill keeps its model whatever the composer's Model pick. */
  modelChoice: boolean;
  loading: boolean;
}

interface SkillMetadataResponse {
  model?: string;
  thinking?: string;
  modelChoice?: boolean;
  tools?: unknown;
  delegation?: unknown;
  subSkills?: unknown;
  sub_skills?: unknown;
  toolConfigs?: Record<string, Record<string, unknown> | undefined>;
}

interface SkillResponse {
  displayName?: string;
  display_name?: string;
  name?: string;
  avatar?: string;
  ownerId?: string;
  owner_id?: string;
  slug?: string | null;
  skillMetadata?: SkillMetadataResponse;
  skill_metadata?: SkillMetadataResponse;
  persona?: { voice?: { enabled?: boolean; language?: string | null } | null } | null;
  initialMessage?: string;
  initial_message?: string;
  welcome?: WelcomeConfig | null;
  locales?: SkillLocales | null;
}

function extractMcpServerIds(data: SkillResponse): readonly string[] {
  const meta = data.skillMetadata ?? data.skill_metadata;
  const servers = meta?.toolConfigs?.mcp?.servers;
  if (!Array.isArray(servers)) return [];
  return servers.filter((s): s is string => typeof s === "string");
}

function extractVoice(data: SkillResponse): SkillMeta["voice"] {
  const v = data.persona?.voice;
  if (!v) return null;
  return { enabled: v.enabled !== false, language: v.language ?? null };
}

function extractTools(data: SkillResponse): readonly string[] {
  const meta = data.skillMetadata ?? data.skill_metadata;
  const tools = meta?.tools;
  if (!Array.isArray(tools)) return [];
  return tools.filter((t): t is string => typeof t === "string");
}

/**
 * True when this skill is a FRONT DOOR — it can hand a request off to a
 * specialist (v6.7.0 delegation policy: `enabled` and at least one `allow`
 * entry). v6.23.0 uses it to decide whether the Workspace Home keeps advertising
 * the skill's example tiles for the whole conversation.
 *
 * A specialist that cannot delegate must NOT: its tiles describe work that gets
 * done by routing to another skill, so leaving them up mid-conversation would
 * promise something the skill cannot deliver (CLAUDE.md #8 — never a dead end).
 * Those skills keep the original first-turn-only onboarding behaviour.
 */
function extractCanDelegate(data: SkillResponse): boolean {
  const meta = data.skillMetadata ?? data.skill_metadata;
  // `subSkills` is the DEPRECATED pre-v6.7.0 form and still delegates
  // (read-compat alias in backend SkillMetadata), so it counts on its own —
  // gating purely on the `delegation` block would silently regress those skills.
  const subSkills = meta?.subSkills ?? meta?.sub_skills;
  if (Array.isArray(subSkills) && subSkills.length > 0) return true;

  const delegation = meta?.delegation as
    | { enabled?: unknown; allow?: unknown; discoverJobs?: unknown; discover_jobs?: unknown }
    | undefined;
  if (!delegation || delegation.enabled !== true) return false;
  // A curated door lists `allow`; a generic door sets `discoverJobs` and reaches
  // any accessible `job:true` skill with no allow entry at all (v6.8.0 8.3).
  // Either makes it a front door. Both alias spellings appear on the wire.
  if (delegation.discoverJobs === true || delegation.discover_jobs === true) return true;
  return Array.isArray(delegation.allow) && delegation.allow.length > 0;
}

function extractA2ui(data: SkillResponse): SkillMeta["a2ui"] {
  const meta = data.skillMetadata ?? data.skill_metadata;
  // toolConfigs is a raw dict (backend `tool_configs`, alias `toolConfigs`),
  // so inner keys pass through verbatim from SKILL.md — snake_case here.
  const a2ui = meta?.toolConfigs?.a2ui as Record<string, unknown> | undefined;
  const allow =
    a2ui?.allow_action_triggered_runs === true ||
    a2ui?.allowActionTriggeredRuns === true;
  const entries = a2ui?.direct_launchers;
  const directLaunchers = Array.isArray(entries) ? entries.filter((entry): entry is DirectLauncherConfig =>
    entry !== null && typeof entry === "object" && typeof entry.name === "string" &&
    typeof entry.label === "string" && typeof entry.tool === "string",
  ) : [];
  return { allowActionTriggeredRuns: allow, ...(directLaunchers.length ? { directLaunchers } : {}) };
}

export function useSkillMeta(skillId: string): SkillMeta {
  const [displayName, setDisplayName] = useState<string>(skillId.slice(0, 8));
  const [avatar, setAvatar] = useState<string | null>(null);
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [mcpServerIds, setMcpServerIds] = useState<readonly string[]>([]);
  const [initialMessage, setInitialMessage] = useState<string>("");
  const [welcome, setWelcome] = useState<WelcomeConfig | null>(null);
  const [locales, setLocales] = useState<SkillLocales | null>(null);
  const [model, setModel] = useState<string>("");
  const [voice, setVoice] = useState<SkillMeta["voice"]>(null);
  const [a2ui, setA2ui] = useState<SkillMeta["a2ui"]>({
    allowActionTriggeredRuns: false,
  });
  const [tools, setTools] = useState<readonly string[]>([]);
  const [canDelegate, setCanDelegate] = useState(false);
  const [thinking, setThinking] = useState("dynamic");
  const [modelChoice, setModelChoice] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth(`/api/proxy/api/skills/${skillId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as SkillResponse;
        if (!cancelled) {
          const display = data.displayName || data.display_name || data.name || skillId.slice(0, 8);
          setDisplayName(display);
          setAvatar(data.avatar || null);
          setOwnerId(data.ownerId || data.owner_id || null);
          setSlug(data.slug ?? null);
          setMcpServerIds(extractMcpServerIds(data));
          setInitialMessage(data.initialMessage || data.initial_message || "");
          setWelcome(data.welcome ?? null);
          setLocales(data.locales ?? null);
          const meta = data.skillMetadata ?? data.skill_metadata;
          setModel(meta?.model || "");
          setThinking(typeof meta?.thinking === "string" ? meta.thinking : "dynamic");
          setModelChoice(meta?.modelChoice !== false);
          setVoice(extractVoice(data));
          setA2ui(extractA2ui(data));
          setTools(extractTools(data));
          setCanDelegate(extractCanDelegate(data));
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
        // displayName stays as truncated UUID fallback; mcpServerIds stays empty
      });
    return () => {
      cancelled = true;
    };
  }, [skillId]);

  // The person's language, resolved on every render so switching it applies at
  // once (no refetch). The base values above stay as the server sent them.
  const locale = useLocale();
  return {
    displayName: localizedField(displayName, locales, "display_name", locale),
    avatar,
    ownerId,
    slug,
    mcpServerIds,
    initialMessage: localizedField(initialMessage, locales, "initial_message", locale),
    greeting: skillGreeting({ welcome, initialMessage, locales }, locale),
    welcome,
    model,
    voice,
    a2ui,
    tools,
    canDelegate,
    thinking,
    modelChoice,
    loading,
  };
}
