/**
 * Skill types — mirrors backend/db/models.py SkillConfig.
 *
 * Layer 1: Agent Skills spec fields (name, description, instructions, skillMetadata)
 * Layer 2: Aitana platform metadata (skillId, displayName, accessControl, etc.)
 *
 * Generated from: backend SkillConfig.model_json_schema(by_alias=True)
 * Source of truth: backend/db/models.py
 */

export interface SkillMetadata {
  author: string;
  version: string;
  model: string;
  thinkingModel?: string | null;
  tools: string[];
  toolConfigs: Record<string, Record<string, unknown>>;
  subSkills: string[];
  delegation?: DelegationConfig;
  /** v6.11.0 — skill-dropdown grouping hint (presentation only, never gates
   * access). ONE taxonomy: "assistant" | "specialist" | "tool"; null → "Other
   * skills". Mirrors backend SkillMetadata.category. */
  category?: string | null;
  /** v6.12.0 — give the agent the request_confirmation tool (author its own
   * A2UI chat form). Default true; a TTFT-critical front door sets false.
   * Mirrors backend SkillMetadata.enable_confirmation. */
  enableConfirmation?: boolean;
}

/** One `delegation.allow` entry. The backend (db/models DelegateRule) serializes
 * these as `{skill, floor}` objects; a bare string is accepted as sugar. The UI
 * only edits membership, so it reads the skill id off either shape. */
export type DelegateEntry = string | { skill: string; floor?: string };

/** Per-skill delegation policy (v6.7.0 SKILL-DELEGATION). Mirrors backend
 * DelegationConfig. `allow` lists the specialist skills the parent may hand off
 * to (as ids or {skill, floor} rules); the backend access-filters them per
 * requesting user at agent-build time. */
export interface DelegationConfig {
  enabled: boolean;
  mode: "auto" | "suggest";
  allow: DelegateEntry[];
  maxDepth?: number;
  /** 8.3 discoverJobs — carried through so a Studio save doesn't drop it. */
  discoverJobs?: boolean;
}

// === v6.6.0 ONE-FORK-CONVERGENCE: per-skill persona (avatar + style + voice) ===
// Mirrors backend/db/models/__init__.py SkillVoiceConfig / SkillPersona.

export type InteractionStyle = "concise" | "rigorous" | "warm" | "socratic";

export interface SkillVoiceConfig {
  /** Read-aloud on/off for this skill. Default true; false hides the speaker
   * button in this skill's chats. Gated further by NEXT_PUBLIC_ENABLE_READ_ALOUD. */
  enabled?: boolean;
  ttsProvider?: string | null;
  ttsVoice?: string | null;
  language?: string | null;
  rate?: number;
  voicePrompt?: string | null;
}

export interface SkillPersona {
  displayName?: string;
  avatar?: string;
  interactionStyle?: InteractionStyle;
  bio?: string | null;
  voice?: SkillVoiceConfig | null;
}

export interface AccessControl {
  type: "private" | "public" | "domain" | "specific" | "tagged";
  domain?: string | null;
  emails?: string[] | null;
  tags?: string[] | null;
}

export interface ProtocolConfig {
  enabled: boolean;
}

export interface Protocols {
  mcp: ProtocolConfig;
  a2a: ProtocolConfig;
  agui: ProtocolConfig;
  a2ui: ProtocolConfig;
  mcpApps: ProtocolConfig;
}

export interface Skill {
  // Layer 1: Agent Skills spec
  name: string;
  description: string;
  instructions: string;
  skillMetadata: SkillMetadata;
  references: Record<string, string>;
  assets: Record<string, string>;

  // Layer 2: Aitana platform metadata
  skillId: string;
  slug?: string | null;
  displayName: string;
  avatar: string;
  ownerEmail: string;
  ownerId: string;
  accessControl: AccessControl;
  protocols: Protocols;
  initialMessage: string;
  tags: string[];
  featured: boolean;
  usageCount: number;
  createdAt: number;
  updatedAt: number;
  v5AssistantId?: string | null;
  // v6.4.0 4.5 SKILL-ONBOARDING — per-skill onboarding affordances
  // (intro_message, example_documents, sidebar bucket browser).
  // Optional / nullable; legacy skills omit this. See
  // docs/design/v6.4.0/skill-onboarding.md.
  welcome?: WelcomeConfig | null;
  // v6.4.0 SHELL-MODES — per-skill page-level shell shape. Optional /
  // nullable; null/missing → chat-primary (ChatShell). See
  // docs/design/v6.4.0/skill-driven-shell-modes.md.
  shell?: SkillShell | null;
  // v6.6.0 ONE-FORK-CONVERGENCE — per-skill persona (avatar + style + voice).
  // Optional / nullable / additive. See docs/design/v6.6.0/…
  persona?: SkillPersona | null;
}

// === v6.4.0 4.5 SKILL-ONBOARDING types ============================

export interface ExampleDocument {
  bucket: string;
  object: string;
  label: string;
  thumbnail?: string | null;
  summary?: string | null;
}

/** A first-look ACTION card (v6.12.0) — a ready-made prompt showing off a
 * capability the skill can actually do. Clicking sends `prompt` as a normal
 * chat message, so the demo path is the product path. Mirrors backend
 * db/models ExamplePrompt. */
export interface ExamplePrompt {
  label: string;
  prompt: string;
  summary?: string | null;
  /** Short uppercase category shown on the card, e.g. "MARKET DATA". */
  badge?: string | null;
}

export interface BucketBrowserConfig {
  /** OPTIONAL — empty means "the viewer's own tenant documents_bucket", filled
   * per-request by the skills API (`_fill_welcome_buckets`). Skill Studio always
   * leaves this empty: an author picks a FOLDER, never a bucket, so a skill can
   * never point at another tenant's storage. */
  bucket?: string;
  rootPath?: string;
  label?: string;
  defaultOpen?: boolean;
}

export interface WelcomeConfig {
  introMessage?: string | null;
  exampleDocuments?: ExampleDocument[];
  examplePrompts?: ExamplePrompt[];
  bucketBrowser?: BucketBrowserConfig | null;
}

// === v6.4.0 SHELL-MODES types =====================================

export type ShellMode = "chat-primary" | "doc-compare" | "workbench-primary" | "custom";

export type ShellChatPosition = "column" | "right-drawer" | "left-drawer" | "floating" | "hidden";

export type ShellChatState = "open" | "minimised" | "hidden";

export interface ShellChat {
  position?: ShellChatPosition;
  defaultState?: ShellChatState;
}

export interface ShellWorkbenchTab {
  id: string;
  label: string;
  contentSource: string; // "a2ui:<surface>" | "mcp_app:<server>" | "fixed:<component>"
  defaultActive?: boolean;
}

export interface ShellWorkbench {
  defaultTab?: string | null;
  tabs?: ShellWorkbenchTab[];
}

export interface SkillShell {
  mode: ShellMode;
  chat?: ShellChat;
  workbench?: ShellWorkbench | null;
}
