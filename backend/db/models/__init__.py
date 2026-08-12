"""Pydantic models for all entities.

These define the data contracts between backend components
and map directly to Firestore document schemas.

Skills follow the Agent Skills spec (agentskills.io/specification)
with Aitana platform metadata as a separate layer.
"""

from __future__ import annotations

import re
import time
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from db.models.access import AccessControl, AccessType
from db.models.buckets import BucketConfig, BucketFolderConfig
from db.models.group_tags import GroupTag
from db.models.platform_config import (
    CONVERSATION_PLACEHOLDER,
    PLATFORM_CONFIG_DOC_ID,
    PREAMBLE_MAX_LEN,
    SUMMARIZER_PROMPT_MAX_LEN,
    CompactionSettings,
    PlatformConfig,
)

# Agent Skills spec: lowercase kebab-case, no leading/trailing/consecutive hyphens
_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_CONSECUTIVE_HYPHENS = re.compile(r"--")

# Slug: 3-60 chars, kebab-case, no leading/trailing hyphens.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$")
# Words that would shadow Next.js routes or have reserved meaning in URLs.
RESERVED_SLUGS = frozenset({"new", "settings", "marketplace", "me", "api", "admin", "chat", "skill", "dev"})


# === Layer 1: Agent Skills Spec ===


class DelegationMode(StrEnum):
    """How a skill hands a turn to an allow-listed specialist skill.

    - ``auto``    : the parent LLM transfers autonomously (ADK ``transfer_to_agent``).
    - ``suggest`` : the parent proposes a handoff; the user confirms before it happens.

    See docs/design/v6.7.0/skill-delegation.md.
    """

    AUTO = "auto"
    SUGGEST = "suggest"


# Per-delegate confirmation floor (v6.8.0 8.2 first-impression-elicited-handoff).
# The parent AI judges the handoff level at runtime; the floor is a CEILING on
# that autonomy — the effective level is max(AI judgement, floor). "auto" = may
# transfer transparently (ADK transfer_to_agent); "confirm" = at least a user OK
# (an A2UI confirm card in chat); "confirm_with_fields" = OK + collected inputs.
DelegateFloor = Literal["auto", "confirm", "confirm_with_fields"]


class DelegateRule(BaseModel):
    """One ``delegation.allow`` entry with its confirmation floor. A bare string
    in ``allow`` is sugar for ``DelegateRule(skill=..., floor=<block default>)``
    — the block ``mode`` maps auto→auto, suggest→confirm."""

    skill: str = Field(min_length=1)
    floor: DelegateFloor = "auto"
    # Optional default field specs collected for a confirm_with_fields handoff
    # (the agent may author its own at runtime; these are the fallback). Raw
    # dicts — validated when the handoff tool builds the elicitation envelope,
    # so db.models stays independent of adk.elicitation.
    fields: list[dict] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class DelegationConfig(BaseModel):
    """Per-skill delegation policy (v6.7.0 SKILL-DELEGATION; per-delegate floors
    added v6.8.0 8.2).

    A skill may hand a turn to a specialist skill in ``allow`` when the parent
    judges it would do better. Delegate targets are ALWAYS access-filtered
    against the requesting user at agent-build time — ``allow`` is a ceiling,
    not a grant (see ``adk.agent.create_agent``). Default ``enabled=False`` keeps
    every existing skill single-agent. ``allow`` entries may be bare skill ids
    (inherit the block ``mode`` as their floor) or ``DelegateRule`` objects with
    an explicit per-delegate ``floor``.
    """

    enabled: bool = False
    mode: DelegationMode = DelegationMode.AUTO
    allow: list[str | DelegateRule] = Field(default_factory=list)  # skill IDs/slugs or DelegateRules
    max_depth: int = Field(default=1, alias="maxDepth")  # hops from the root skill
    # v6.8.0 8.3 JOBS: opt into offering ANY accessible skill tagged `job:true`
    # (access-filtered) as a delegate, in addition to explicit `allow`. Curated
    # doors keep an explicit `allow` for a predictable menu; generic doors set
    # this so a new job skill is reachable with only its SKILL.md — no door edit.
    # The access filter stays the hard gate; discovery only NARROWS, never widens.
    discover_jobs: bool = Field(default=False, alias="discoverJobs")

    model_config = {"populate_by_name": True}

    def rules(self, *, extra_skills: list[str] | None = None) -> list[DelegateRule]:
        """Normalise mixed string/rule ``allow`` entries (+ any legacy
        ``sub_skills`` passed as ``extra_skills``) into ``DelegateRule``s,
        de-duplicated by skill in order. Bare strings inherit the block ``mode``
        as their floor (auto→auto, suggest→confirm)."""
        default_floor: DelegateFloor = "confirm" if self.mode == DelegationMode.SUGGEST else "auto"
        out: list[DelegateRule] = []
        seen: set[str] = set()
        for entry in [*self.allow, *[str(s) for s in (extra_skills or [])]]:
            rule = entry if isinstance(entry, DelegateRule) else DelegateRule(skill=str(entry), floor=default_floor)
            if rule.skill and rule.skill not in seen:
                seen.add(rule.skill)
                out.append(rule)
        return out


class FallbackConfig(BaseModel):
    """Per-skill model-fallback override (MODEL-RELIABILITY M3).

    ``models`` overrides the registry's default chain for this skill's
    primary model. ``allow_cross_provider`` opts in to egress-WIDENING
    fallback (eu primary -> non-eu fallback) under an ``unrestricted``
    deployment; it can NEVER override an ``eu-strict`` deployment policy —
    residency is enforced by construction in ``adk.agent.resolve_model_chain``.
    Default empty = registry chain applies.
    """

    models: list[str] = Field(default_factory=list)  # registry ids, in order
    allow_cross_provider: bool = Field(default=False, alias="allowCrossProvider")

    model_config = {"populate_by_name": True}


class SkillMetadata(BaseModel):
    """Agent Skills spec metadata field — platform-specific config stored in SKILL.md frontmatter."""

    author: str = "aitana"
    version: str = "1.0"
    model: str = "gemini-2.5-flash"
    thinking_model: str | None = Field(default=None, alias="thinkingModel")
    # How hard this skill's PRIMARY agent should think (config/thinking.py).
    # A front door and a chaining specialist want opposite answers: measured
    # 2026-07-21, dynamic thinking costs 6.1x on mechanical work for identical
    # correctness, but the analytical case spends 1886 thinking tokens earning
    # its keep. `dynamic` is the back-compat default (what every Gemini skill
    # got unconditionally before this field existed).
    #   off     — mechanical, single right answer (data-extractor)
    #   low     — front doors: route/delegate fast, first token is the product
    #   dynamic — specialists that chain tools and genuinely reason
    # Ignored for Claude/OpenAI skills, which carry reasoning_effort instead.
    thinking: Literal["off", "low", "dynamic"] = "dynamic"

    @field_validator("thinking", mode="before")
    @classmethod
    def _yaml_off_is_not_a_boolean(cls, v):
        """Accept `thinking: off` written bare in SKILL.md YAML.

        YAML 1.1 parses an unquoted `off` as the BOOLEAN False, so the most
        natural spelling of this field silently became `False` and failed
        Literal validation. It cost a real seed failure: data-extractor was
        the only skill declaring `off`, and it was the only one in
        `failed: [...]` — invisible because the seed step is non-fatal.
        Coerce it back rather than making every author remember the quotes.
        `on`/`yes` (-> True) have no sensible depth and still fail loudly.
        """
        return "off" if v is False else v

    tools: list[str] = []
    tool_configs: dict = Field(default_factory=dict, alias="toolConfigs")
    # DEPRECATED (v6.7.0): superseded by `delegation.allow`. Kept as a read-compat
    # alias — a bare `subSkills` list still delegates (access-filtered) so
    # pre-delegation skills keep working, but new config should use `delegation`.
    sub_skills: list[str] = Field(default_factory=list, alias="subSkills")
    delegation: DelegationConfig = Field(default_factory=DelegationConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    # v6.8.0 8.3 JOBS: mark a skill as a delegatable "job" (an expensive specialist
    # op — e.g. obligation analysis, contract compare). A job is discoverable by
    # opted-in doors (`delegation.discover_jobs`) without a hand-maintained allow
    # entry. `job_floor` is the confirmation level a discovering door applies — the
    # job's OWN trust requirement (a light "proceed?" OK by default; a job that
    # needs structured inputs the AI can't infer sets `confirm_with_fields`).
    job: bool = False
    job_floor: DelegateFloor = Field(default="confirm", alias="jobFloor")
    # v6.11.0 — dropdown grouping. Free-form so forks define their own taxonomy;
    # the ONE tenant uses `assistant` (general front doors), `specialist`
    # (domain experts — PPA, compare), `tool` (narrow utilities — researcher).
    # `None` falls into an "Other skills" bucket. This is a PRESENTATION hint
    # only (the skill dropdown groups by it); it never gates access — that stays
    # `access_control` + `delegation`. Friendly-name rule: this is a human/AI-
    # readable label, never an id.
    category: str | None = None
    # v6.12.0 — give the agent the `request_confirmation` tool so it can author
    # its OWN A2UI chat form (confirm / confirm-with-fields) from its judgement,
    # engine-validated + read back authoritatively. Default ON (most skills
    # benefit); a TTFT-critical front door keeps its toolset tiny by setting this
    # False. Independent of a tool authoring its own elicitation
    # (map_ppa_obligations) — a skill may have both. See v6.12.0
    # mcp-elicitation-adoption.md (same envelope, agent-sourced).
    enable_confirmation: bool = Field(default=True, alias="enableConfirmation")

    model_config = {"populate_by_name": True}


# === Layer 2: Aitana Platform Metadata ===
# AccessControl now lives in db/models/access.py and is re-exported above so
# resource-access-control (1A.1b) can share the exact same schema.


class ProtocolConfig(BaseModel):
    enabled: bool = False


class Protocols(BaseModel):
    mcp: ProtocolConfig = ProtocolConfig()
    a2a: ProtocolConfig = ProtocolConfig()
    agui: ProtocolConfig = ProtocolConfig(enabled=True)
    a2ui: ProtocolConfig = ProtocolConfig()
    mcpApps: ProtocolConfig = ProtocolConfig()


# === v6.4.0 4.5 SKILL-ONBOARDING: per-skill welcome / onboarding affordances ===


class ExampleDocument(BaseModel):
    """One pre-loaded example document a skill offers in its WorkbenchPane
    Workspace tab when a chat is fresh. Click → existing doc-import-by-reference
    path (no upload). See docs/design/v6.4.0/skill-onboarding.md.

    ``bucket`` is OPTIONAL: when empty, the skills API fills it per-request from
    the caller's tenant ``documents_bucket`` (``resolve_documents_bucket``), so a
    shared SKILL.md points every env's library at that env's own llmops bucket
    (which is what aitana3 indexes) — no hardcoded per-env bucket, no customer
    project. See `_fill_welcome_buckets` in skills/routes.py."""

    bucket: str = ""
    object: str
    label: str
    thumbnail: str | None = None
    summary: str | None = None

    model_config = {"populate_by_name": True}


class BucketBrowserConfig(BaseModel):
    """Sidebar bucket-browser config — mounts a GCSFileBrowser in the
    sidebar as a 3rd SidebarSection when set. SA must have read access
    to the bucket (existing v6.3.0 client-tenant-management grants).

    ``bucket`` is OPTIONAL — empty means "use the caller's tenant
    ``documents_bucket``", filled per-request by the skills API (see
    ``ExampleDocument``)."""

    bucket: str = ""
    root_path: str = Field(default="", alias="rootPath")
    label: str = ""
    default_open: bool = Field(default=False, alias="defaultOpen")

    model_config = {"populate_by_name": True}


class ExamplePrompt(BaseModel):
    """One first-look ACTION card — a ready-made prompt that shows off a
    capability the skill can actually do (v6.12.0).

    `example_documents` only ever advertised "import a document"; a skill's real
    range (market data, comparison, analysis, research) was invisible until you
    knew to ask. Clicking a card sends `prompt` as a normal chat message, so the
    demo path IS the product path — no special-case runner.

    Write `prompt` self-contained (name the documents by their FRIENDLY label —
    the agent resolves them via its library tools) so a click works from a cold
    session with nothing open.
    """

    label: str
    prompt: str
    summary: str | None = None
    # Short uppercase category shown on the card (e.g. "MARKET DATA", "COMPARE").
    badge: str | None = None

    model_config = {"populate_by_name": True}


class WelcomeConfig(BaseModel):
    """Per-skill onboarding config — intro greeting, example documents, example
    prompts, sidebar bucket browser. All fields optional and nullable; older
    skills without `welcome` round-trip unchanged. See
    docs/design/v6.4.0/skill-onboarding.md for the full schema and rationale."""

    intro_message: str | None = Field(default=None, alias="introMessage")
    example_documents: list[ExampleDocument] = Field(default_factory=list, alias="exampleDocuments")
    example_prompts: list[ExamplePrompt] = Field(default_factory=list, alias="examplePrompts")
    bucket_browser: BucketBrowserConfig | None = Field(default=None, alias="bucketBrowser")
    # Which TENANT owns this library — the skills API fills any empty
    # example_documents / bucket_browser `bucket` from `clients/{documents_tenant}.
    # documents_bucket` (that tenant's per-env llmops bucket), VIEWER-INDEPENDENT.
    # Empty = fall back to the viewer's own documents_bucket (back-compat).
    documents_tenant: str = Field(default="", alias="documentsTenant")

    model_config = {"populate_by_name": True}


# === v6.4.0 SHELL-MODES: per-skill page-level shell shape ===


class ShellChat(BaseModel):
    """How the chat surface is presented within a shell. `column` is the
    classic ChatShell middle column; the drawer positions are used by the
    doc-compare / workbench-primary shells where chat is secondary."""

    position: str = "column"  # column | right-drawer | left-drawer | floating | hidden
    default_state: str = Field(default="open", alias="defaultState")  # open | minimised | hidden

    model_config = {"populate_by_name": True}

    @field_validator("position")
    @classmethod
    def _validate_position(cls, v: str) -> str:
        allowed = {"column", "right-drawer", "left-drawer", "floating", "hidden"}
        if v not in allowed:
            raise ValueError(f"chat.position must be one of {sorted(allowed)}")
        return v

    @field_validator("default_state")
    @classmethod
    def _validate_default_state(cls, v: str) -> str:
        allowed = {"open", "minimised", "hidden"}
        if v not in allowed:
            raise ValueError(f"chat.default_state must be one of {sorted(allowed)}")
        return v


class ShellWorkbenchTab(BaseModel):
    """A statically-declared workbench tab whose content is bound to a
    protocol-emitted surface. `content_source` is `a2ui:<surface>`,
    `mcp_app:<server>`, or `fixed:<component>` (the last is a v6.5 hook)."""

    id: str
    label: str
    content_source: str = Field(alias="contentSource")
    default_active: bool = Field(default=False, alias="defaultActive")

    model_config = {"populate_by_name": True}


class ShellWorkbench(BaseModel):
    """Optional workbench config for workbench-primary shells. Tabs may also
    be derived from A2UI surface emissions at runtime when none are declared."""

    default_tab: str | None = Field(default=None, alias="defaultTab")
    tabs: list[ShellWorkbenchTab] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class SkillShell(BaseModel):
    """Page-level shell shape a skill declares in SKILL.md frontmatter. When a
    skill leaves `shell` null, the platform renders the chat-primary ChatShell
    (the post-4.3 layout). `custom` is accepted but resolves to ChatShell in
    v1; a registry hook is a v6.5 follow-up. See
    docs/design/v6.4.0/skill-driven-shell-modes.md."""

    mode: str = "chat-primary"  # chat-primary | doc-compare | workbench-primary | custom
    chat: ShellChat = Field(default_factory=ShellChat)
    workbench: ShellWorkbench | None = None

    model_config = {"populate_by_name": True}

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        allowed = {"chat-primary", "doc-compare", "workbench-primary", "custom"}
        if v not in allowed:
            raise ValueError(f"shell.mode must be one of {sorted(allowed)}")
        return v


# === v6.6.0 ONE-FORK-CONVERGENCE: per-skill persona (avatar + style + voice) ===


class SkillVoiceConfig(BaseModel):
    """Text-to-speech voice for a skill's read-aloud output. Consumed by the
    /api/voice/* routes (v6.6.0 M2). All fields optional; unset falls back to
    the skill/env default. ONE's default is Aitana — a warm Spanish female
    professional voice on the premium tier (es-ES-Chirp3-HD-Aoede)."""

    # Read-aloud on/off for this skill. Default True so existing skills keep
    # the speaker button; an author can turn it off to hide read-aloud in this
    # skill's chats. Gated further by the global NEXT_PUBLIC_ENABLE_READ_ALOUD.
    enabled: bool = True
    tts_provider: str | None = Field(default=None, alias="ttsProvider")  # gcp_chirp3hd | gcp_gemini | browser
    tts_voice: str | None = Field(default=None, alias="ttsVoice")  # e.g. es-ES-Chirp3-HD-Aoede
    language: str | None = None  # BCP-47 short, e.g. "es"
    rate: float = 1.0  # 0.25-4.0 (1.0 = natural)
    voice_prompt: str | None = Field(default=None, alias="voicePrompt")  # Gemini-TTS style direction

    model_config = {"populate_by_name": True}

    @field_validator("rate")
    @classmethod
    def _validate_rate(cls, v: float) -> float:
        if not 0.25 <= v <= 4.0:
            raise ValueError("voice.rate must be between 0.25 and 4.0")
        return v


class SkillPersona(BaseModel):
    """A skill's professional identity bundle — avatar, interaction style, and
    voice. Promotes the legacy top-level `avatar` into a first-class object so
    the Skill Studio can edit identity + voice in one place. `interaction_style`
    is appended as a short directive to the skill instruction at agent build
    time. See docs/design/v6.6.0/one-app-fork-convergence.md (Workstream C)."""

    display_name: str = Field(default="", alias="displayName")  # overrides displayName in chat
    avatar: str = ""  # URL to the chat-bubble avatar
    interaction_style: Literal["concise", "rigorous", "warm", "socratic"] = Field(
        default="concise", alias="interactionStyle"
    )
    bio: str | None = None  # short professional descriptor
    voice: SkillVoiceConfig | None = None

    model_config = {"populate_by_name": True}


# === Combined Skill Document ===


class SkillConfig(BaseModel):
    """Firestore document model for a skill.

    Layer 1 (Agent Skills spec): name, description, instructions,
    skill_metadata, references, assets.

    Layer 2 (Aitana metadata): skill_id, display_name, avatar,
    owner_email, access_control, protocols, tags, etc.
    """

    # --- Agent Skills spec fields (Layer 1) ---
    name: str
    description: str = ""
    instructions: str = ""
    skill_metadata: SkillMetadata = Field(default_factory=SkillMetadata, alias="skillMetadata")
    references: dict[str, str] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict)

    # --- Aitana platform metadata (Layer 2) ---
    skill_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="skillId")
    slug: str | None = None
    display_name: str = Field(default="", alias="displayName")
    avatar: str = ""
    owner_email: str = Field(default="", alias="ownerEmail")
    owner_id: str = Field(default="", alias="ownerId")
    access_control: AccessControl = Field(default_factory=AccessControl, alias="accessControl")
    protocols: Protocols = Field(default_factory=Protocols)
    initial_message: str = Field(default="", alias="initialMessage")
    tags: list[str] = Field(default_factory=list)
    featured: bool = False
    usage_count: int = Field(default=0, alias="usageCount")
    # v6.9.0 9.2: provenance marker. "template" = seeded from a SKILL.md on disk
    # (the platform seeder refreshes it from disk on redeploy). "firestore" =
    # created/edited durably in-product via the admin API — the seeder must NOT
    # clobber it. None = legacy/unknown → treated as template (clobberable) for
    # backward-compat.
    managed_by: str | None = Field(default=None, alias="managedBy")
    created_at: float = Field(default_factory=time.time, alias="createdAt")
    updated_at: float = Field(default_factory=time.time, alias="updatedAt")
    v5_assistant_id: str | None = Field(default=None, alias="v5AssistantId")
    # v6.4.0 4.5 SKILL-ONBOARDING: per-skill onboarding affordances.
    # Optional / nullable / additive — legacy skills round-trip unchanged.
    welcome: WelcomeConfig | None = None
    # v6.4.0 SHELL-MODES: per-skill page-level shell shape. None = chat-primary
    # (existing ChatShell). Optional / nullable / additive.
    shell: SkillShell | None = None
    # v6.6.0 ONE-FORK-CONVERGENCE: per-skill persona (avatar + style + voice).
    # Optional / nullable / additive. Legacy `avatar`-only records fold in below.
    persona: SkillPersona | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _fold_legacy_avatar(self) -> SkillConfig:
        """Back-compat: a skill saved before v6.6.0 has a top-level `avatar` and
        no `persona`. Surface it as `persona.avatar` so downstream code can read
        identity from one place. Skills with an explicit persona are left alone;
        skills with neither keep `persona = None`."""
        if self.persona is None and self.avatar:
            self.persona = SkillPersona(avatar=self.avatar)
        return self

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("name must be 1-64 characters")
        if not _NAME_PATTERN.match(v) or _CONSECUTIVE_HYPHENS.search(v):
            raise ValueError(
                "name must be lowercase kebab-case (a-z, 0-9, hyphens), no leading, trailing, or consecutive hyphens"
            )
        return v

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        if not v:
            raise ValueError("description must not be empty (1-1024 characters)")
        if len(v) > 1024:
            raise ValueError("description must be at most 1024 characters")
        return v

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, v: str) -> str:
        # 20K, not 10K: composed platform skills (SKILL.md body + standard
        # tool-error/persona blocks) legitimately run ~10K — one-doc-compare
        # is 9.4K, one-ppa-expert 10.3K. The old 10K cap turned normal edits
        # into corruption that blanked the SkillsBar (deploy Trap 22).
        if len(v) > 20_000:
            raise ValueError("instructions must be at most 20,000 characters")
        return v

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SLUG_PATTERN.match(v):
            raise ValueError(
                "slug must be 3-60 chars, lowercase kebab-case (a-z, 0-9, hyphens), no leading or trailing hyphens"
            )
        if v in RESERVED_SLUGS:
            raise ValueError(f"slug '{v}' is reserved")
        return v

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        if len(v) > 10:
            raise ValueError("maximum 10 tags")
        for tag in v:
            if len(tag) > 50:
                raise ValueError(f"tag '{tag[:20]}...' exceeds 50 characters")
        return v


# === Other entities ===


class Message(BaseModel):
    message_id: str = Field(alias="messageId")
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: float
    metadata: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class UserProfile(BaseModel):
    user_id: str = Field(alias="userId")
    email: str
    display_name: str = Field(default="", alias="displayName")
    created_at: float = Field(default=0, alias="createdAt")
    last_active: float = Field(default=0, alias="lastActive")
    rag_corpus_name: str | None = Field(default=None, alias="ragCorpusName")

    model_config = {"populate_by_name": True}


# === Document models (see db/models/document.py) ===

from db.models.document import (  # noqa: E402
    Block,
    BlockType,
    DocMetadata,
    DocSummary,
    DocumentStatus,
    EditedBlock,
    ParsedDocument,
)

__all__ = [
    "CONVERSATION_PLACEHOLDER",
    "PLATFORM_CONFIG_DOC_ID",
    "PREAMBLE_MAX_LEN",
    "SUMMARIZER_PROMPT_MAX_LEN",
    "AccessControl",
    "AccessType",
    "Block",
    "BlockType",
    "BucketConfig",
    "BucketFolderConfig",
    "CompactionSettings",
    "DelegateFloor",
    "DelegateRule",
    "DelegationConfig",
    "DelegationMode",
    "DocMetadata",
    "DocSummary",
    "DocumentStatus",
    "EditedBlock",
    "GroupTag",
    "Message",
    "ParsedDocument",
    "PlatformConfig",
    "ProtocolConfig",
    "Protocols",
    "SkillConfig",
    "SkillMetadata",
    "UserProfile",
]
