"""ADK Agent factory — creates agents from SkillConfig documents.

Workshop W2b — ADK: The Foundation (factory)
  `create_agent()` reads a SkillConfig from Firestore and assembles an
  LlmAgent. The three-line model router (Gemini / Claude / LiteLlm) and
  the `_HeuristicRouter` thinking-tier are the moments to linger on during
  the talk — they show what ADK's model abstraction buys you.

The factory has three layers:
  1. `resolve_model(model_id)` — maps a skill's model string to the correct
     ADK model wrapper (Gemini / Claude / LiteLlm).
  2. `resolve_tools(...)` — from `adk.tools` — wraps callables as
     FunctionTool instances for the agent.
  3. `create_agent(skill_config, user)` and `create_agent_with_thinking(...)`
     — assemble the above into an ADK LlmAgent with per-user callbacks.

Thinking strategy (3 tiers, see docs/design/v6.0.0/agent-factory.md):
  A. Gemini only, no `thinking_model` → single agent with a planner at the
     skill's declared `thinking:` depth (off | low | dynamic; default dynamic),
     built via `config.thinking` so the 2.5-vs-3.x parameter split lives in one
     place. `off` attaches NO planner (thinking_budget=0).
  B. Claude/OpenAI, no `thinking_model` → single agent, no planner (reasoning
     is carried by reasoning_effort in `resolve_model`).
  C. Any provider, `thinking_model` set → `_HeuristicRouter(fast, thinking,
     picker)` that picks between two agents via `_should_think(message)`. BOTH
     agents get an explicit planner (fast at the skill's declared depth,
     thinking always DYNAMIC) — Gemini-only; a Claude/OpenAI thinking_model
     gets none.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

from google.adk.agents import LlmAgent
from google.adk.code_executors import BuiltInCodeExecutor
from google.adk.models import Gemini
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.planners import BuiltInPlanner
from google.adk.tools import AgentTool, BaseTool, FunctionTool
from google.adk.tools.load_artifacts_tool import load_artifacts_tool
from google.adk.tools.load_memory_tool import load_memory_tool
from google.adk.tools.preload_memory_tool import preload_memory_tool

from adk import (
    a2ui_bigquery_render as _a2ui_bigquery_render,  # noqa: F401 — registers the scoped ad-hoc BigQuery tab mapping + offload exemption (6.23)
)
from adk import (
    a2ui_elicitation_render as _a2ui_elicitation_render,  # noqa: F401 — registers the generic elicitation-in-chat mapping (8.1)
)
from adk import (
    a2ui_entsoe_render as _a2ui_entsoe_render,  # noqa: F401 — registers the ENTSO-E prices tab mapping + offload exemption (6.12)
)
from adk import (
    a2ui_maps_render as _a2ui_maps_render,  # noqa: F401 — registers the Maps Grounding Lite attribution mapping + offload exemption (6.23). NOT optional: attribution is a licence condition.
)
from adk import (
    a2ui_obligation_render as _a2ui_obligation_render,  # noqa: F401 — registers obligation result→A2UI mappings at import
)
from adk import a2ui_ppa_render as _a2ui_ppa_render  # noqa: F401 — registers result→A2UI mappings at import
from adk import (
    a2ui_sources_render as _a2ui_sources_render,  # noqa: F401 — registers web-search Sources tab mapping (6.11)
)
from adk.a2ui import A2uiToolConfig, make_a2ui_toolset
from adk.a2ui_surface_context import wrap_with_a2ui_surface_context
from adk.artifact_tools import retrieve_artifact
from adk.callbacks import (
    _handle_large_output,
    compose_after_agent_callbacks,
    make_a2ui_result_emitter,
    make_after_agent_response,
    make_authored_a2ui_stripper,
    make_before_agent,
    make_document_injector,
    make_document_loader,
    make_permission_enforcer,
    make_session_tracker,
)
from adk.elicitation import ElicitationEnvelope, ElicitationField, make_elicitation_result, request_confirmation
from adk.iframe_context import wrap_with_iframe_context
from adk.instruction_provider_chain import compose_instruction_providers
from adk.mcp_observability import (
    compose_after_tool_callbacks,
    compose_before_tool_callbacks,
    make_mcp_after_tool_callback,
    make_mcp_before_tool_callback,
)
from adk.output_format_context import wrap_with_output_format
from adk.platform_preamble_context import wrap_with_platform_preamble
from adk.resilient_llm import ResilientLlm
from adk.saved_forms_context import wrap_with_saved_forms
from adk.today_context import wrap_with_today
from adk.tools import resolve_mcp_tools, resolve_tools
from auth.access_context import AccessContext
from auth.firebase_auth import User
from config.models import (
    ChainLink,
    active_residency_policy,
    api_name_for,
    entry_for,
    load_models_config,
)
from config.thinking import ThinkDepth, thinking_config_for
from db.models import DelegateRule, FallbackConfig, SkillConfig
from skills.skill_config import find_by_slug, find_jobs, get_skill
from tools.structured_extraction import structured_extraction_callback

logger = logging.getLogger(__name__)


# --- Model routing ---


# Claude generations that use the NEW extended-thinking API — `thinking.type=
# "adaptive"` + `output_config.effort` — and REJECT the old `thinking={type:
# "enabled", budget_tokens:N}`:
#   • 5 family (`claude-<name>-5`: sonnet-5, fable-5, mythos-5, opus-5…) — a hard
#     400 on the old shape (every sonnet-5 handoff RUN_ERRORed, 2026-07-24).
#   • opus/sonnet 4.6-4.9 (`-4-6`…`-4-9`) — 4.7/4.8 also removed budget_tokens;
#     the API TOLERATES the old shape on opus-4-8 but silently does no thinking
#     (the "dark ThinkingPanel", 2026-07-16), so it must use the new shape too.
# NOT matched (stay on reasoning_effort, which litellm maps per-model correctly):
# the 4.5/4.0/3.x line (`-4-5`, `-4-0`, `-3-…`) and haiku (excluded by the caller).
# 5-family match requires a NAME segment before `-5` so `-4-5` (sonnet-4-5,
# haiku-4-5) is never mistaken for Claude 5.
_CLAUDE_ADAPTIVE_API = re.compile(r"(?:-[a-z]+-5|-4-[6-9])$")


def _claude_uses_adaptive_thinking(resolved_model: str) -> bool:
    """True for Claude models on the new adaptive-thinking API (5 family + opus/
    sonnet 4.6-4.9), which reject the old `enabled+budget_tokens` shape.

    `claude-sonnet-5` / `claude-fable-5` / `claude-opus-4-8` / `claude-opus-4-7`
    / `claude-sonnet-4-6` → True; `claude-sonnet-4-5` / `claude-opus-4-5` /
    `claude-3-opus` → False (old reasoning_effort path)."""
    return bool(_CLAUDE_ADAPTIVE_API.search(resolved_model))


def resolve_model(model_id: str) -> Gemini | LiteLlm:
    """Create the correct ADK model wrapper for the given model reference.

    Accepts a logical tier name, a registry id, or a raw provider api name
    (see config.models.api_name_for). After collapsing to an api name:

    - `gemini-*` -> `Gemini(model=...)` (Vertex AI via ADC)
    - `claude-*` -> `LiteLlm(model="anthropic/...")` (direct Anthropic API; not Vertex)
    - `gpt-*` / `o3*` -> `LiteLlm(model="openai/...")` (requires OPENAI_API_KEY)

    Raises:
        ValueError: If the reference does not resolve to a known provider prefix.
    """
    resolved = api_name_for(model_id)
    if resolved.startswith("gemini-"):
        # Vertex region/endpoint availability is NOT uniform across Gemini
        # generations (verified per-model via live probes — see
        # config/models.yaml comments). An entry with an explicit `location`
        # (e.g. a Gemini 3.x model whose only EU option is the jurisdictional
        # multi-region endpoint, location="eu", not a europe-west* region)
        # is pinned there. Failing that, `residency: global` entries (404 on
        # the default region-pinned client, GOOGLE_CLOUD_LOCATION=europe-west1)
        # route through location="global". Note: global endpoint = NOT
        # EU-resident; eu-strict deployments reject these at
        # resolve_model_chain before this branch matters.
        entry = entry_for(model_id)
        if entry is not None and entry.location:
            return RegionalGemini(model=resolved, location=entry.location)
        if entry is not None and entry.residency == "global":
            return RegionalGemini(model=resolved, location="global")
        return Gemini(model=resolved)
    if resolved.startswith("claude-"):
        # Direct Anthropic API (via LiteLLM), NOT Vertex Model Garden. We use
        # the direct API because the Claude models WE PIN (claude-opus-4-8,
        # claude-sonnet-5, claude-haiku-4-5) are not in Vertex Model Garden for
        # this project — verified 2026-07-21. Note the earlier "not served in
        # ANY region" was too strong: Vertex europe-west1 DOES serve older
        # Claude (claude-3-opus, claude-sonnet-4-5), just not our current
        # flagships — so this is a model-GENERATION gap, not a provider gap, and
        # an EU-resident Claude path becomes possible once a pinned flagship
        # lands in europe-west1. Requires ANTHROPIC_API_KEY. The direct API is a
        # US-egress path, so reserve Claude tiers for reasoning that does not
        # stream restricted customer content off the EU edge. See CLAUDE.md
        # privacy boundary.
        #
        # Adaptive thinking (MODEL-RELIABILITY M4, live-verified 2026-07-10):
        # litellm forwards `thinking` to Anthropic and streams the summarized
        # reasoning as reasoning_content deltas -> ADK thought parts ->
        # REASONING events -> ThinkingPanel. `display: summarized` is
        # mandatory on Opus 4.7+ (default `omitted` streams EMPTY thinking
        # text — the v5 keep-alive-via-thinking-tokens trick silently dies
        # without it). Haiku rejects adaptive with a 400 (live-verified), so
        # it stays bare. CLAUDE_ADAPTIVE_THINKING=off is the kill switch if
        # TTFT/eval regresses (sprint plan M4 assumption).
        kwargs: dict = {}
        if os.environ.get("CLAUDE_ADAPTIVE_THINKING", "on").strip().lower() != "off" and "haiku" not in resolved:
            _EFFORT = {"smart": "high", "default": "medium", "fast": "low"}
            entry = entry_for(model_id)
            effort = _EFFORT.get(getattr(entry, "tier", None) or "", "high")
            if _claude_uses_adaptive_thinking(resolved):
                # Claude 5 family (sonnet-5, fable-5, …) REMOVED the old thinking
                # API: `thinking={type:"enabled", budget_tokens:N}` returns a hard
                # 400 ("use thinking.type.adaptive and output_config.effort").
                # litellm 1.82.6 only rewrites reasoning_effort→output_config for
                # 4.6 models (AnthropicConfig._is_claude_4_6_model); for a `-5`
                # model it still emits the removed enabled+budget shape → every
                # handoff to a sonnet-5 delegate RUN_ERRORed (live 2026-07-24).
                # So send the NEW shape ourselves — litellm forwards both `thinking`
                # and `output_config` to Anthropic verbatim (transformation.py
                # param=="thinking" passthrough + output_config handling). `display:
                # summarized` keeps the ThinkingPanel fed (default `omitted` streams
                # empty thinking text).
                kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
                kwargs["output_config"] = {"effort": effort}
            else:
                # Pre-5 Claude (opus-4-8/4-7/4-6, sonnet-4-6, …): reasoning_effort
                # works — litellm maps it to the shape each accepts (4.6 →
                # output_config; opus-4-x → enabled+budget). Do NOT switch these to
                # the adaptive shape in this fix: opus-4-8 is the working default
                # and destabilising it belongs in its own change. Haiku has no
                # extended thinking, so it stays bare (excluded above).
                kwargs["reasoning_effort"] = effort
        return LiteLlm(model=f"anthropic/{resolved}", **kwargs)
    if resolved.startswith("gpt-") or resolved.startswith("o3"):
        # For REASONING models (gpt-5.x, o-series) pass reasoning_effort
        # explicitly (mirrors the Claude `thinking` branch above) for TWO reasons:
        #   1. Tools + reasoning: gpt-5.4+ with BOTH function tools AND
        #      reasoning_effort is rejected on /v1/chat/completions ("use
        #      /v1/responses"). LiteLLM auto-bridges such calls to the Responses
        #      API — but ONLY when reasoning_effort is present at call time
        #      (main.py::responses_api_bridge_check requires `reasoning_effort is
        #      not None`); it does NOT add a default before that check. So a bare
        #      LiteLlm(model="openai/…") sends tools to chat/completions and
        #      RUN_ERRORs on every tool-using turn (live 2026-07-16, gpt-5.6-sol).
        #      Passing reasoning_effort routes to /v1/responses where tools +
        #      reasoning coexist.
        #   2. It sets the thinking depth per tier (the point of a reasoner skill).
        # A non-reasoning model (e.g. gpt-4o) would REJECT reasoning_effort, so
        # only pass it to reasoning-capable models.
        if resolved.startswith("gpt-5") or resolved.startswith(("o1", "o3", "o4")):
            _EFFORT = {"smart": "high", "default": "medium", "fast": "low"}
            entry = entry_for(model_id)
            effort = _EFFORT.get(getattr(entry, "tier", None) or "", "medium")
            # reasoning_effort triggers the Responses-API bridge + sets depth (see
            # above). reasoning={"summary":"auto"} makes the Responses API RETURN
            # the reasoning summary so it streams to the ThinkingPanel — without it
            # the model reasons but the summary is dark (verified 2026-07-16).
            # allowed_openai_params keeps litellm from dropping `reasoning` as an
            # unsupported chat param before it reaches the responses bridge.
            return LiteLlm(
                model=f"openai/{resolved}",
                reasoning_effort=effort,
                reasoning={"summary": "auto"},
                allowed_openai_params=["reasoning"],
            )
        return LiteLlm(model=f"openai/{resolved}")
    raise ValueError(f"Unsupported model: {model_id!r} (resolved to {resolved!r})")


# --- MODEL-RELIABILITY M3: residency-gated fallback chains -------------------


class ResidencyViolationError(Exception):
    """A skill pins a model whose egress violates the deployment residency
    policy. Raised at agent-build time (load), never mid-turn — a
    misconfigured skill must fail loudly in admin, not silently swap models."""


class RegionalGemini(Gemini):
    """Gemini pinned to a specific Vertex region (tier-1a cross-region rungs).

    ADK's ``Gemini.api_client`` builds its client from env
    (``GOOGLE_CLOUD_LOCATION``); this override pins ``location`` per chain
    entry instead. Vertex quota is per project+region, so a 429 in
    europe-west1 genuinely clears in europe-west4. ``retry_options`` stays
    unset by design — ResilientLlm owns ALL retries (stacked layers
    multiply attempts and blow the <30s failover budget).
    """

    location: str = ""

    @cached_property
    def api_client(self):
        from google.genai import Client
        from google.genai import types as genai_types

        return Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=self.location,
            http_options=genai_types.HttpOptions(headers=self._tracking_headers()),
        )


def _residency_of(ref_or_api: str) -> str:
    """Residency for a registry ref or raw api name. Registry entries carry
    an explicit tag; raw names are inferred by prefix (gemini-* runs on our
    region-pinned Vertex client → eu; anything else → us, the fail-safe)."""
    entry = entry_for(ref_or_api)
    if entry is not None:
        return entry.residency
    return "eu" if api_name_for(ref_or_api).startswith("gemini-") else "us"


_PROVIDER_KEY_ENVS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def _provider_key_missing(api_name: str) -> str | None:
    """Env var name if the api's provider key is required but not mounted."""
    if api_name.startswith("claude-"):
        needed = "ANTHROPIC_API_KEY"
    elif api_name.startswith("gpt-") or api_name.startswith("o3"):
        needed = "OPENAI_API_KEY"
    else:
        return None
    return None if os.environ.get(needed) else needed


def _resolve_link(link: ChainLink) -> Gemini | LiteLlm:
    api = api_name_for(link.id)
    if link.location:
        if not api.startswith("gemini-"):
            raise ValueError(
                f"chain entry {link.id!r} sets location={link.location!r} but only Gemini is region-pinnable"
            )
        return RegionalGemini(model=api, location=link.location)
    return resolve_model(link.id)


def resolve_model_chain(model_ref: str, fallback: FallbackConfig | None = None) -> BaseLlm:
    """Resolve a skill's model ref into its full fallback chain (M3).

    THE residency choke point: under ``eu-strict`` no non-EU entry can enter
    the chain — a pinned non-EU primary raises ``ResidencyViolationError``
    (tier refs never do: the tier variant already resolved to an EU model),
    and non-EU fallback entries are dropped with a loud warning regardless
    of skill config. Under ``unrestricted``, egress-NARROWING fallback
    (us→eu, e.g. claude→gemini) is always legal; egress-WIDENING (eu→us)
    requires the skill's explicit ``fallback.allow_cross_provider`` opt-in.

    Chain entries whose provider key isn't mounted are skipped with a
    warning (deploy-drift-proof — a fork without ANTHROPIC_API_KEY must not
    crash turns on a fallback it can't serve). A resulting single-member
    chain returns the bare model: zero behavior change when no fallback is
    actually available.
    """
    policy = active_residency_policy()
    primary_api = api_name_for(model_ref)
    primary_residency = _residency_of(model_ref)

    if policy == "eu-strict" and primary_residency != "eu":
        raise ResidencyViolationError(
            f"model {model_ref!r} (-> {primary_api!r}, residency={primary_residency}) is not EU-resident, "
            f"but this deployment enforces MODEL_RESIDENCY_POLICY=eu-strict. Use an EU model or a tier "
            f"(tiers resolve to EU variants automatically)."
        )

    # Fallback candidates: per-skill override wins, else the registry chain.
    entry = entry_for(model_ref)
    if fallback is not None and fallback.models:
        candidates = [ChainLink(id=ref) for ref in fallback.models]
    elif entry is not None:
        candidates = list(entry.fallbacks)
    else:
        candidates = []

    allow_widening = bool(fallback and fallback.allow_cross_provider)
    members: list[BaseLlm] = [resolve_model(model_ref)]
    for link in candidates:
        link_api = api_name_for(link.id)
        link_residency = _residency_of(link.id)
        if policy == "eu-strict" and link_residency != "eu":
            logger.warning(
                "residency: dropping fallback %s (residency=%s) from %s chain — deployment is eu-strict "
                "(per-skill allow_cross_provider cannot override deployment policy)",
                link.id,
                link_residency,
                model_ref,
            )
            continue
        if policy != "eu-strict" and primary_residency == "eu" and link_residency != "eu" and not allow_widening:
            logger.warning(
                "residency: dropping egress-widening fallback %s (eu primary -> %s fallback) from %s chain — "
                "set fallback.allowCrossProvider: true on the skill to opt in",
                link.id,
                link_residency,
                model_ref,
            )
            continue
        missing_key = _provider_key_missing(link_api)
        if missing_key:
            logger.warning(
                "fallback %s skipped: %s not mounted on this deployment (chain for %s)",
                link.id,
                missing_key,
                model_ref,
            )
            continue
        members.append(_resolve_link(link))

    if len(members) == 1:
        return members[0]

    policy_cfg = load_models_config().fallback_policy
    return ResilientLlm(
        chain=members,
        max_retries_per_model=int(policy_cfg.get("max_retries_per_model", 2)),
    )


# --- Name sanitisation ---
# ADK's LlmAgent name validator requires `^[a-zA-Z_][a-zA-Z0-9_]*$`. Skill
# IDs default to UUIDs (contain hyphens) and may start with a digit; kebab-
# case names also have hyphens. Sanitize once at the factory boundary.

_VALID_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_agent_name(skill_id: str) -> str:
    safe = skill_id.replace("-", "_")
    if not safe:
        return "s_"
    if not (safe[0].isalpha() or safe[0] == "_"):
        safe = "s_" + safe
    if not _VALID_IDENT.match(safe):
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", safe)
    return safe


def _delegate_agent_name(skill: SkillConfig, taken: set[str]) -> str:
    """Readable, valid, UNIQUE identifier for a delegate wired as a sub_agent
    (v6.10.0 unified handoff, D2). ADK's ``TransferToAgentTool`` enum-constrains
    ``agent_name`` to these — so the model reasons over the slug
    (``one_obligation_analysis``) instead of a sanitized uuid, and can never emit
    an invalid target. Falls back to the uuid form on a bad/empty slug, and
    disambiguates collisions with a numeric suffix. Mutates ``taken``."""
    slug = str(getattr(skill, "slug", "") or "")
    base = re.sub(r"[^a-zA-Z0-9_]", "_", slug).strip("_")
    if base and not (base[0].isalpha() or base[0] == "_"):
        base = "s_" + base
    if not base or not _VALID_IDENT.match(base):
        base = _safe_agent_name(skill.skill_id)
    name = base
    i = 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    taken.add(name)
    return name


# --- Thinking strategy ---

# Keywords that suggest a user message warrants deeper reasoning. Kept short
# and boring on purpose: false positives route to the better model, the cost
# of being wrong is a few extra tokens — we prefer a high recall heuristic.
THINK_KEYWORDS = frozenset(
    {
        "analyze",
        "analyse",
        "reason",
        "compare",
        "evaluate",
        "plan",
        "design",
        "debug",
        "explain",
        "derive",
        "prove",
    }
)


def _should_think(message: str) -> bool:
    """Heuristic: does this message warrant the thinking model?

    Rules (any one triggers thinking):
      - length > 280 chars (beyond a typical one-liner)
      - contains any THINK_KEYWORDS word
      - has >=2 question marks (compound / multi-part question)
    """
    if len(message) > 280:
        return True
    if message.count("?") >= 2:
        return True
    tokens = {t.strip(".,!?:;").lower() for t in message.split()}
    return bool(tokens & THINK_KEYWORDS)


def _planner_for(skill_config: SkillConfig) -> BuiltInPlanner | None:
    """Return a BuiltInPlanner for Gemini skills with no thinking_model.

    - Gemini + no thinking_model: `BuiltInPlanner(thinking_budget=-1)` —
      Gemini 2.5's dynamic thinking (the model decides per request).
    - Claude / OpenAI: BuiltInPlanner is Gemini-specific; return None.
    - thinking_model set: routing happens in Python via _HeuristicRouter;
      the single-agent case doesn't apply, so return None here.
    """
    if skill_config.skill_metadata.thinking_model is not None:
        # A two-agent skill's planners are built explicitly by
        # create_agent_with_thinking (fast gets the skill's declared depth,
        # thinking gets DYNAMIC) and passed as _planner_override, so this
        # single-agent path does not apply.
        #
        # This used to mean the five deepest skills (ppa-expert,
        # obligation-analysis, doc-compare, document-analyst, code-assistant)
        # ran with NO thinking config on EITHER agent — the skills most meant
        # to reason were the only ones without a planner, thinking at the API
        # default with include_thoughts unset (i.e. silently, no ThinkingPanel).
        return None
    # model may be a tier name (`lite`/`smart`) — resolve to the api name before
    # the provider prefix check, else a Gemini `lite` skill loses its planner.
    if not api_name_for(skill_config.skill_metadata.model).startswith("gemini-"):
        return None
    # Depth comes from the skill's own `thinking:` field (default `dynamic` —
    # what every Gemini skill got unconditionally before the field existed),
    # resolved through the shared seam (config/thinking.py) rather than a
    # literal here. The seam owns the 2.5-vs-3.x parameter split (3.x accepts
    # thinking_level; 2.5 rejects it with a hard 400) and preserves
    # include_thoughts=True (MODEL-RELIABILITY M4) — without which Gemini
    # thinks silently and a long thinking phase reads to the user as a hang.
    return _planner_at(ThinkDepth(skill_config.skill_metadata.thinking), skill_config.skill_metadata.model)


def _planner_at(depth: ThinkDepth, model_ref: str) -> BuiltInPlanner | None:
    """A BuiltInPlanner at `depth` for `model_ref`, or None for non-Gemini.

    BuiltInPlanner is Gemini-specific and Claude/OpenAI carry their own
    reasoning_effort wiring in `resolve_model`, so a None ThinkingConfig means
    "attach no planner" — never "attach a planner with no config".
    """
    cfg = thinking_config_for(depth, model_ref)
    return BuiltInPlanner(thinking_config=cfg) if cfg is not None else None


# --- Skill delegation helpers (v6.7.0 SKILL-DELEGATION) ---


def _resolve_delegate_skill(ref: str, owner_id: str) -> SkillConfig | None:
    """Resolve a delegate reference (a skill DOC ID or a SLUG) to its config.

    `delegation.allow` entries are typically authored as SLUGS (e.g.
    `one-ppa-expert`). Locally the seeded doc id equals the slug so `get_skill`
    resolves it directly; on a deployed env the doc id is a generated UUID, so
    `get_skill(slug)` misses — fall back to a slug lookup within the PARENT's
    owner namespace (platform skills delegate to platform specialists). Without
    this fallback the deployed handoff silently finds zero delegates."""
    sub = get_skill(ref)
    if sub is None:
        sub = find_by_slug(owner_id, ref)
    return sub


def _resolve_accessible_delegates(
    delegate_ids: list[str],
    parent_config: SkillConfig,
    access_context: AccessContext | None,
) -> list[SkillConfig]:
    """Look up delegate skill configs and keep only those the user can access.

    `delegation.allow` (and the legacy `sub_skills` alias) is a ceiling, not a
    grant. Deny-by-default: unknown ids are skipped, inaccessible targets are
    dropped, and a missing `access_context` yields an empty list (fail-safe —
    the request path always supplies one, so a None here is a programming
    error and we refuse to leak).
    """
    if access_context is None:
        logger.warning(
            "delegation requested by %r but no access_context supplied; no delegates (fail-safe)",
            parent_config.skill_id,
        )
        return []
    resolved: list[SkillConfig] = []
    seen_ids: set[str] = set()
    for sub_id in delegate_ids:
        sub = _resolve_delegate_skill(sub_id, parent_config.owner_id)
        if sub is None:
            logger.warning(
                "delegate %r referenced by %r not found; skipping",
                sub_id,
                parent_config.skill_id,
            )
            continue
        # A slug in `allow` and a discovered doc-id (8.3) can resolve to the SAME
        # skill — dedupe by resolved id so it isn't wired as two sub_agents.
        if sub.skill_id in seen_ids:
            continue
        if not access_context.can_access_skill(sub):
            logger.info(
                "delegate %r not accessible to requesting user; dropping (deny-by-default)",
                sub_id,
            )
            continue
        seen_ids.add(sub.skill_id)
        resolved.append(sub)
    return resolved


def accessible_delegate_rules(
    parent_config: SkillConfig,
    access_context: AccessContext | None,
) -> list[tuple[SkillConfig, DelegateRule]]:
    """(SkillConfig, DelegateRule) for every access-filtered delegate a skill wires.

    The single source of truth for a skill's reachable delegate set: explicit
    ``delegation.allow`` (+ legacy ``sub_skills``) PLUS discovered ``job:true``
    skills when ``discover_jobs`` is on, each access-filtered against the user
    (deny-by-default). ``create_agent`` uses this to build sub_agents / the
    request_handoff catalog, and the ``surface-action-run`` confirm→switch loop
    (8.2) uses it to VALIDATE a confirm-handoff target — so a forged
    ``target_skill_id`` can never re-issue a turn on a skill the door (and user)
    can't reach. Does NOT apply the recursion depth bound — that stays in
    ``create_agent`` (which knows the current stack depth)."""
    md = parent_config.skill_metadata
    deleg = md.delegation
    if not (deleg.enabled or md.sub_skills):
        return []
    # allow wins; sub_skills is the legacy alias (folded in as auto-floor rules).
    rules = deleg.rules(extra_skills=(list(md.sub_skills) if not deleg.allow else None))
    # 8.3 JOBS: discovered jobs become rules at their self-declared floor.
    if deleg.enabled and deleg.discover_jobs and access_context is not None:
        pinned = {r.skill for r in rules}
        for job in find_jobs(parent_config.owner_id):
            if job.skill_id == parent_config.skill_id or job.skill_id in pinned:
                continue
            rules.append(DelegateRule(skill=job.skill_id, floor=job.skill_metadata.job_floor))
            pinned.add(job.skill_id)
    if not rules:
        return []
    accessible = _resolve_accessible_delegates([r.skill for r in rules], parent_config, access_context)
    # Map each accessible delegate back to its rule (floor) by RESOLVED skill_id
    # — a rule's `skill` may be an id or a slug (get_skill is cached).
    rule_by_id: dict[str, DelegateRule] = {}
    for rule in rules:
        resolved_sub = _resolve_delegate_skill(rule.skill, parent_config.owner_id)
        if resolved_sub is not None:
            rule_by_id[resolved_sub.skill_id] = rule
    return [(sub, rule_by_id.get(sub.skill_id) or DelegateRule(skill=sub.skill_id, floor="auto")) for sub in accessible]


# Reserved surface-action the confirm/submit fires; the confirm→switch loop
# (8.2 M3, surface-action-run) recognises it and re-issues the turn on the
# target skill. The `context` carries the target + parent skill ids.
CONFIRM_DELEGATION_ACTION = "confirm_delegation"
# Level ordering for clamping the AI's chosen level up to a delegate's floor.
_HANDOFF_LEVEL_ORDER = {"confirm": 1, "confirm_with_fields": 2}


def _build_handoff_envelope(parent_id: str, skill: SkillConfig, rule: DelegateRule) -> ElicitationEnvelope:
    """Build the confirm / confirm-with-fields elicitation envelope for a
    confirm-floor delegate (v6.10.0). This is what the `before_tool_callback`
    floor policy returns AS the `transfer_to_agent` result when the target's
    floor demands confirmation — short-circuiting the native transfer.

    The FLOOR alone decides the kind (no AI-chosen level): `confirm_with_fields`
    when the rule declares fields, else `confirm`. cwf with no valid fields
    degrades to a plain confirm (never an invalid empty form)."""
    display = getattr(skill, "display_name", "") or skill.name
    kind = "confirm_with_fields" if rule.floor == "confirm_with_fields" else "confirm"
    parsed_fields: list[ElicitationField] = []
    if kind == "confirm_with_fields":
        try:
            parsed_fields = [ElicitationField.model_validate(f) for f in (rule.fields or [])]
        except Exception as exc:  # malformed field spec -> degrade, never dead-end
            logger.warning("delegate %r has invalid fields; degrading to confirm: %s", skill.skill_id, exc)
            parsed_fields = []
        if not parsed_fields:
            kind = "confirm"
    message = f"Hand this conversation to {display}?"
    if skill.description:
        message = f"{message} {skill.description}"
    return ElicitationEnvelope(
        kind=kind,  # type: ignore[arg-type]
        action=CONFIRM_DELEGATION_ACTION,
        message=message,
        reason=skill.description or "",
        fields=parsed_fields,
        context={"target_skill_id": skill.skill_id, "parent_skill_id": parent_id},
    )


def make_handoff_policy_callback(
    parent_id: str, delegate_map: dict[str, tuple[SkillConfig, DelegateRule]]
) -> Callable[..., dict | None]:
    """The SINGLE handoff policy point (v6.10.0 unified-adk-handoff).

    A `before_tool_callback` over ADK's native `transfer_to_agent`. The model
    only ever knows one handoff verb; the delegate's configured FLOOR — not an
    AI judgement — decides the disposition:

      * floor `auto`               -> return None; the native in-turn transfer
                                       proceeds (transfer_to_agent action set by
                                       the tool body, AGENT_DELEGATION marker on
                                       the delegate's before_agent).
      * floor `confirm` / `confirm_with_fields`
                                   -> return the elicitation envelope AS the tool
                                      result. A non-None return SKIPS the tool
                                      (verified ADK semantics), so the transfer
                                      NEVER happens; the 8.1 primitive renders the
                                      card/form in chat and the 8.2 full switch
                                      completes it on Proceed.

    `delegate_map` is keyed by the enum-constrained sub_agent name, so an unknown
    `agent_name` returns None and ADK's own validation answers."""

    def handoff_policy(tool: BaseTool, args: dict, tool_context: object = None) -> dict | None:
        if getattr(tool, "name", "") != "transfer_to_agent":
            return None
        agent_name = str((args or {}).get("agent_name") or "")
        target = delegate_map.get(agent_name)
        if target is None:
            return None  # unknown target -> ADK's enum/validation speaks
        skill, rule = target
        if rule.floor == "auto":
            return None  # transparent native transfer
        # confirm / confirm_with_fields -> short-circuit; NO transfer happens.
        envelope = _build_handoff_envelope(parent_id, skill, rule)
        from observability.timing import get_current_tracker

        display = getattr(skill, "display_name", "") or skill.name
        get_current_tracker().mark_delegation(
            parent=parent_id, target=skill.skill_id, target_display=display, mode=envelope.kind
        )
        return make_elicitation_result(envelope, tool_context=tool_context)

    return handoff_policy


@dataclass
class _HeuristicRouter:
    """Wraps two agents (`fast` and `thinking`) and a picker heuristic.

    Not itself an ADK Agent — the SSE endpoint calls `pick_agent(message)`
    to choose which agent to hand to the Runner for a given turn.
    """

    fast: LlmAgent
    thinking: LlmAgent
    picker: Callable[[str], bool]

    def pick_agent(self, message: str) -> LlmAgent:
        return self.thinking if self.picker(message) else self.fast


# --- Model-aware search tool wiring ---


def _resolve_search_tools(
    tool_names: list[str],
    tool_configs: dict,
) -> list:
    """Return search AgentTools for a skill — one or two sub-agents as needed.

    All models (Gemini, Claude, OpenAI) use the sub-agent pattern so the root
    agent never has grounding built-ins alongside FunctionTools (400 INVALID_ARGUMENT).
    ADK tracks this as TODO(b/448114567) and will remove the workaround when fixed upstream.

    google_search and VertexAiSearchTool use incompatible API-level tool types
    (google_search vs retrieval) and cannot share an agent, so each gets its own:
      - google_search → GoogleSearchAgentTool (ADK-native, propagates grounding metadata)
      - ai_search     → AgentTool(enterprise_search_agent, propagate_grounding_metadata=True)
      - both          → two AgentTools, both returned
    """
    from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool

    from tools.search_agent import create_enterprise_search_agent, create_web_search_agent

    wants_web = "google_search" in tool_names
    wants_enterprise = "ai_search" in tool_names

    if not (wants_web or wants_enterprise):
        return []

    result = []
    if wants_web:
        result.append(GoogleSearchAgentTool(create_web_search_agent()))
    if wants_enterprise:
        # Accept both `datastore_id` (the canonical key — matches resource_ids.py,
        # the wiring test, and docs/ops/adk-search-tools.md) and the shorthand
        # `datastore`. Several SKILL.md files shipped the `datastore` spelling,
        # which silently fell through to the skip branch below and disabled
        # enterprise search entirely — the tool was requested but never attached,
        # so it never appeared in a demo. Reading both keys means a stale seed or
        # a fork using either spelling still wires the tool. `datastore_id` wins
        # if somehow both are present.
        # Datastore precedence:
        #   1. the skill's own toolConfigs.ai_search.datastore_id / .datastore
        #      (per-skill override — e.g. ONE points at its own contract corpus)
        #   2. the platform default env var VERTEX_AI_SEARCH_DATASTORE_ID
        #      (Aitana: the shared `aitana3` datastore in project aitana-ai-search)
        # so any skill that simply lists `ai_search` in its tools gets a working
        # corpus for free, and specialists can still point elsewhere.
        ai_search_cfg = tool_configs.get("ai_search") or {}
        datastore_id: str | None = (
            ai_search_cfg.get("datastore_id")
            or ai_search_cfg.get("datastore")
            or os.environ.get("VERTEX_AI_SEARCH_DATASTORE_ID", "").strip()
            or None
        )
        if datastore_id:
            # G15 (template-fork-ergonomics.md): expand bare ids
            # (e.g. `ds-ap-vendors`) to the full Vertex resource path
            # before handing to VertexAiSearchTool. Already-full paths
            # pass through unchanged, so a SKILL.md that pins an explicit
            # project/region keeps that exact value.
            from tools.resource_ids import resolve_resource_id

            try:
                expanded = resolve_resource_id("vertex_datastore", datastore_id)
            except RuntimeError as e:
                logger.warning(
                    "ai_search: failed to expand datastore_id %r (%s); "
                    "passing bare value through and hoping Vertex accepts it",
                    datastore_id,
                    e,
                )
                expanded = datastore_id
            result.append(AgentTool(create_enterprise_search_agent(expanded), propagate_grounding_metadata=True))
        else:
            logger.warning(
                "ai_search tool requested but no datastore configured "
                "(skill toolConfigs.ai_search.datastore_id / .datastore, nor the "
                "VERTEX_AI_SEARCH_DATASTORE_ID env default); skipping enterprise search"
            )
    return result


def _resolve_code_executor(
    tool_names: list[str],
    model_id: str,
) -> tuple[BuiltInCodeExecutor | None, list]:
    """Return (code_executor, extra_tools) for a skill's code execution needs.

    Gemini agents: BuiltInCodeExecutor attached directly to the LlmAgent.
    Claude/OpenAI agents: AgentTool wrapping a Gemini CodeAgent sub-agent.

    Returns:
        (executor, tools) — executor is None when model is non-Gemini or no
        code_execution tool requested; tools is empty for Gemini agents.
    """
    if "code_execution" not in tool_names:
        return None, []

    # The Gemini-native BuiltInCodeExecutor (integrated: the model writes + runs
    # code in one turn) can be used DIRECTLY only when code execution is the
    # skill's SOLE capability — Gemini rejects a builtin tool combined with ANY
    # function tool ("Multiple tools are supported only when they are all search
    # tools"). So a skill that MIXES code with other tools (e.g. the ONE front
    # door with delegation), or a non-Gemini skill, delegates to a Gemini code
    # sub-agent via AgentTool — which is a function tool and coexists with
    # everything (the sub-agent runs BuiltInCodeExecutor in isolation).
    other_tools = [t for t in tool_names if t != "code_execution"]
    if model_id.startswith("gemini-") and not other_tools:
        return BuiltInCodeExecutor(), []

    from tools.code_execution.agent import create_code_agent

    return None, [AgentTool(create_code_agent())]


# --- Agent factory ---


def create_agent(
    skill_config: SkillConfig,
    user: User,
    *,
    access_context: AccessContext | None = None,
    _seen: set[str] | None = None,
    _model_override: str | None = None,
    _planner_override: BuiltInPlanner | None = None,
    _delegation_parent_id: str | None = None,
) -> LlmAgent:
    """Build an ADK LlmAgent from a SkillConfig + authenticated User.

    - `name` = sanitized `skill_id` (ADK rejects hyphens)
    - `instruction` = `skill_config.instructions` (Agent Skills spec field)
    - `tools` resolved from `skill_metadata.tools` (unknowns skipped+logged)
    - `sub_agents` recursed from `skill_metadata.sub_skills` (skill IDs
      looked up via `skills.skill_config.get_skill`); cycle-detected via
      the private `_seen` set.
    - `before_tool_callback` = `make_permission_enforcer(user.email, user.domain)`

    Args:
        _seen: internal. Set of skill IDs already on the current call stack,
            used to detect cycles. Callers should leave as None.
        _model_override: internal. When building router sub-agents,
            _create_router_sub_agent uses this to swap the model without
            duplicating the recursion logic.
        _planner_override: internal. Same deal for the planner — None means
            "use _planner_for(skill_config)".

    Raises:
        ValueError: if a sub-skill cycle is detected.
    """
    seen = set(_seen) if _seen else set()
    if skill_config.skill_id in seen:
        raise ValueError(
            f"Sub-skill cycle detected: {skill_config.skill_id!r} already on the resolution stack {seen!r}"
        )
    seen.add(skill_config.skill_id)

    md = skill_config.skill_metadata
    effective_model = _model_override or md.model
    # MODEL-RELIABILITY M3: resolve the full fallback chain (residency-gated).
    # Single-member chains come back as the bare model — zero change for
    # skills with no fallbacks configured.
    model = resolve_model_chain(effective_model, md.fallback)
    # Resolved api name for provider-prefix checks below (effective_model may be
    # a tier name like `lite`/`smart` since v6.6.0 M1).
    effective_model_api = api_name_for(effective_model)
    # Default tools every skill gets (opt-out via toolConfigs.defaults in SKILL.md):
    #   load_artifacts_tool  - LLM-driven artifact retrieval (legacy path; the
    #                          before_model_callback in callbacks.py also
    #                          eager-injects docs on resumed sessions).
    #   retrieve_artifact    - keyword/section search inside a known artifact.
    #   load_memory_tool     - LLM-driven semantic search over the Vertex
    #                          memory bank. Required for cross-session recall.
    #   preload_memory_tool  - auto-fetches relevant memories before the LLM
    #                          turn (same memory bank). Pairs with
    #                          load_memory_tool: preload primes context,
    #                          load_memory follows up for deeper queries.
    _defaults_cfg = md.tool_configs.get("defaults", {}) if isinstance(md.tool_configs, dict) else {}
    # Gemini constraint guard (2026-06-11): builtin tools (code_execution,
    # GoogleSearchAgentTool, etc.) cannot be combined with function tools.
    # Symptom is a 400 "Multiple tools are supported only when they are all
    # search tools." from generate_content_stream.
    #
    # If the skill declares code_execution we silently force-disable the
    # default artifact + memory toolset rather than let an opt-in slip
    # through and emit a confusing 400 on the first chat turn. Skill
    # authors who want both behaviours (impossible today) would still need
    # to think about it explicitly; this guard just prevents the silent
    # foot-gun where the SKILL.md author thought "I only listed
    # code_execution, why is this broken?"
    # True only when this skill will use the DIRECT Gemini BuiltInCodeExecutor —
    # i.e. code_execution is its SOLE tool on a Gemini model (see
    # _resolve_code_executor). In that case NO function tool may coexist, so the
    # artifact/memory defaults are off (and a comprehensive strip runs below). A
    # skill that MIXES code with other tools uses the code sub-agent DELEGATE and
    # keeps its defaults.
    _code_tools_only = [t for t in (md.tools or []) if t != "code_execution"] if isinstance(md.tools, list) else []
    _uses_builtin_code_executor = (
        isinstance(md.tools, list)
        and "code_execution" in md.tools
        and not _code_tools_only
        and effective_model_api.startswith("gemini-")
    )
    _artifacts_default = False if _uses_builtin_code_executor else True
    _memory_default = False if _uses_builtin_code_executor else True
    tools = [
        *([load_artifacts_tool, retrieve_artifact] if _defaults_cfg.get("artifacts", _artifacts_default) else []),
        *([load_memory_tool, preload_memory_tool] if _defaults_cfg.get("memory", _memory_default) else []),
        *resolve_tools(md.tools, md.tool_configs),
    ]
    tools.extend(_resolve_search_tools(md.tools, md.tool_configs))
    tools.extend(resolve_mcp_tools(md.tool_configs))
    from adk.callbacks import _RAG_DOCUMENTS_ENABLED

    if _RAG_DOCUMENTS_ENABLED:
        from tools.rag_tool import search_documents

        tools.append(search_documents)
    # MULTI-SURFACE-A2UI M1 — read the skill's `tool_configs.a2ui` block so
    # the toolset emits `surface_id`/`update_mode` siblings alongside
    # `validated_a2ui_json`. Defaults (no a2ui block) preserve pre-M1
    # inline-in-chat behaviour. Invalid combinations (e.g. patch+chat)
    # raise here at agent-build time, not at the first tool call.
    # `a2ui.enabled` IS the Model-A gate: it controls whether the agent gets the
    # direct send_a2ui_json_to_client toolset. A Model-B skill (result→A2UI
    # mapping — its workbench is drawn from tool results, interaction is
    # chat:send) must set `enabled: false` so the agent CAN'T author A2UI — else,
    # asked to "explain a difference", it renders invalid UI instead of replying
    # (7.5 M3). Kept as `enabled` (not a new field) to preserve the default-True
    # backwards-compat workshop demos rely on; Model-B render runs via the
    # after_tool_callback regardless of this flag.
    a2ui_cfg = A2uiToolConfig.from_tool_configs(md.tool_configs)
    if a2ui_cfg.enabled:
        tools.append(make_a2ui_toolset(config=a2ui_cfg))
    code_executor, code_tools = _resolve_code_executor(md.tools, effective_model_api)
    tools.extend(code_tools)
    # v6.12.0 — the agent-authored elicitation path. Give the model
    # `request_confirmation` so it can raise its OWN A2UI chat form (confirm /
    # confirm-with-fields) from its judgement — engine-validated, read back
    # authoritatively. Default ON; a TTFT-critical front door opts out
    # (`enableConfirmation: false`) to keep its request schema tiny.
    if md.enable_confirmation:
        tools.append(FunctionTool(request_confirmation))
    # Gemini builtin-tool constraint (the definitive guard): when the DIRECT
    # BuiltInCodeExecutor is used it cannot coexist with ANY function tool
    # ("Multiple tools are supported only when they are all search tools" → 400).
    # Strip every auto-injected function tool (artifacts/memory/RAG
    # search_documents/request_confirmation) — the sole-tool check above means
    # there are no skill-declared tools to lose. (Fixes the live code-assistant
    # 400, 2026-07-17. Mixed-tool skills use the code sub-agent delegate instead
    # and keep their tools.)
    if code_executor is not None:
        tools = []
    planner = _planner_override if _planner_override is not None else _planner_for(skill_config)

    # --- Access-aware skill delegation (v6.7.0 SKILL-DELEGATION; per-delegate
    # floors v6.8.0 8.2 first-impression-elicited-handoff) ---
    # Delegate targets (`delegation.allow` rules, and the deprecated `sub_skills`
    # alias) are resolved + access-filtered ONCE (deny-by-default; fail-safe when
    # access_context is absent). Each accessible delegate's per-delegate FLOOR
    # then decides its disposition:
    #   - floor "auto"          -> wired as an ADK sub_agent so the model can
    #                              transfer transparently (transfer_to_agent),
    #                              with per-delegate graceful degradation.
    #   - floor confirm / cwf    -> reachable ONLY via the `request_handoff` tool
    #                              (no silent transfer); the tool clamps the AI's
    #                              chosen level up to the floor and renders an A2UI
    #                              confirm card / field form in chat (8.1 primitive).
    # `request_handoff` is attached whenever ANY delegate needs confirmation, over
    # ALL accessible delegates (so the AI may also confirm-route an auto one).
    # `max_depth` (default 1) is checked against the current skill's depth on the
    # resolution stack (root = 0), so the default is "root delegates one hop".
    # v6.10.0 unified-adk-handoff: ONE handoff verb. EVERY accessible delegate is
    # wired as a sub_agent, so ADK's native, enum-constrained `transfer_to_agent`
    # is the model's only handoff tool. A single `before_tool_callback`
    # (make_handoff_policy_callback) enforces each delegate's FLOOR:
    #   - auto        -> real recursive sub_agent; the native transfer runs in-turn.
    #   - confirm/cwf -> STUB sub_agent (visible to the transfer enum, never runs);
    #                    the policy callback short-circuits the transfer and returns
    #                    the 8.1 elicitation envelope -> the 8.2 full switch.
    # `delegate_map` (enum name -> (skill, rule)) is the callback's lookup.
    sub_agents: list[LlmAgent] = []
    delegate_map: dict[str, tuple[SkillConfig, DelegateRule]] = {}
    deleg = md.delegation
    depth = len(seen) - 1  # `seen` already includes this skill
    # The full access-filtered delegate set (explicit allow + legacy sub_skills +
    # discovered jobs) — see accessible_delegate_rules, the shared resolver.
    # The recursion depth bound stays here.
    accessible_rules: list[tuple[SkillConfig, DelegateRule]] = (
        accessible_delegate_rules(skill_config, access_context) if depth < deleg.max_depth else []
    )
    if accessible_rules:
        _taken_names: set[str] = set()
        for sub, rule in accessible_rules:
            name = _delegate_agent_name(sub, _taken_names)
            if rule.floor == "auto":
                try:
                    child = create_agent(
                        sub,
                        user,
                        access_context=access_context,
                        _seen=seen,
                        _delegation_parent_id=skill_config.skill_id,
                    )
                except ValueError:
                    # Cycle detection is a hard error — never degrade past it.
                    raise
                except Exception as exc:  # graceful degradation: one bad delegate ≠ dead parent
                    logger.warning(
                        "delegate %r of %r failed to build; skipping (graceful degradation): %s",
                        sub.skill_id,
                        skill_config.skill_id,
                        exc,
                    )
                    continue
                child.name = name  # slug enum name (D2); the model reasons over it
                sub_agents.append(child)
            else:
                # confirm/cwf -> stub: name + description only (no tools, no
                # instruction). Cheap (the door's factory cost stays flat — the
                # REAL agent is built on the specialist's page after the switch).
                # It is never executed: the policy callback short-circuits first.
                sub_agents.append(
                    LlmAgent(
                        name=name,
                        model=model,
                        description=sub.description or (getattr(sub, "display_name", "") or sub.name),
                    )
                )
            delegate_map[name] = (sub, rule)

    _before_agent = make_before_agent(
        skill_config.skill_id,
        tool_configs=md.tool_configs,
        access_context=access_context,
        # When this agent was built AS an auto-mode delegate, its before_agent
        # fires exactly on activation (the transfer) — emit the AGENT_DELEGATION
        # marker there so the UI can show the handoff. None for a root skill.
        delegation_parent_id=_delegation_parent_id,
        delegation_display=(getattr(skill_config, "display_name", "") or skill_config.name),
        delegation_avatar=(getattr(skill_config, "avatar", "") or ""),
    )
    _session_tracker = make_session_tracker(user.uid, skill_config.skill_id)
    _document_loader = make_document_loader()
    _document_injector = make_document_injector()

    async def _composed_before_agent(callback_context: object) -> None:
        # TTFT mark: ADK has finished its runner setup and is now invoking
        # our before_agent_callback. The gap from agent_factory_done →
        # runner_setup_done attributes ag_ui_adk wrap + ADK runner enter
        # + plugin setup — the second-largest unexplained cost the M1
        # baseline revealed. See docs/design/v6.1.0/ttft-optimization.md.
        from observability.timing import STAGE_RUNNER_SETUP_DONE, get_current_tracker

        get_current_tracker().mark(STAGE_RUNNER_SETUP_DONE)

        _before_agent(callback_context)
        _session_tracker(callback_context)
        await _document_loader(callback_context)

        # TTFT: mark the end of the synchronous before-agent chain. Show a
        # user-facing "Reading N documents…" label ONLY for documents attached to
        # THIS turn — NOT the persistent, app-scoped RAG-corpus tracking
        # (`app:docs_loaded`/`app:docs_files`), which survives across sessions and
        # would flash "Reading 2 documents" on a fresh chat with nothing attached
        # (2026-07-16 report: fresh session, "No open documents", yet the label
        # showed the prior session's corpus docs). `document_ids` is this turn's
        # attachment, seeded by skill_processor from forwardedProps.
        from observability.timing import (
            STAGE_BEFORE_AGENT_DONE,
            get_current_tracker,
        )

        state = getattr(callback_context, "state", None)
        turn_docs = [d for d in (list(state.get("document_ids") or []) if state is not None else []) if d]
        label: str | None = None
        if turn_docs:
            suffix = "s" if len(turn_docs) != 1 else ""
            label = f"Reading {len(turn_docs)} document{suffix}…"
        get_current_tracker().mark(STAGE_BEFORE_AGENT_DONE, user_label=label)

    # G26 (template-protocol-defaults.md): compose after-agent callbacks via
    # the shared helper so the first non-None Content return reaches the
    # AG-UI wire. The bespoke ``async def _composed_after_agent(...) -> None``
    # wrapper this replaces silently dropped each callback's return value —
    # a 12-line bug surfaced by gde-ap-agent's AP demo polish (2026-06-03)
    # because the schema-validated JSON Part the callback produced never
    # reached the frontend's JsonCardBuilder. Both current callbacks
    # (_after_agent_response, structured_extraction_callback) return None
    # today; the helper sets up the pattern so a fork or future template
    # callback CAN return Content and have it surface as a follow-up event.
    _composed_after_agent = compose_after_agent_callbacks(
        make_after_agent_response(),
        structured_extraction_callback,
    )

    # Sprint 2.12 (M2): pluggable budget enforcement. The before/after
    # model callback pair consults the registered enforcer pre-call,
    # raises BudgetExceededError on hard block (caught by AG-UI's
    # error translator), and reconciles the held projection with
    # realised usage post-call. No-ops when no enforcer is registered
    # OR the skill has no `tool_configs.budget` block — back-compat
    # with every existing skill.
    from adk.budget_config import BudgetConfig
    from budget import get_registered_enforcer
    from budget.callback import make_budget_callbacks

    _budget_before, _budget_after = make_budget_callbacks(
        get_registered_enforcer(),
        user=user,
        skill_id=skill_config.skill_id,
        budget_config=BudgetConfig.from_tool_configs(md.tool_configs),
    )

    async def _composed_before_model(callback_context: object, llm_request: object) -> None:
        # Document injector runs FIRST so docs are visible to the
        # budget projection (longer prompt = higher projected cost).
        # The injector predates the budget gate; if dropping a
        # participant here, see test_composed_before_model.py.
        await _document_injector(callback_context, llm_request)
        await _budget_before(callback_context, llm_request)

    # Model-B guard: when the agent has NO A2UI toolset (a2ui.enabled: false) it
    # must not author UI at all — including by TYPING A2UI JSON into chat, which
    # the toolset gate cannot prevent (2026-07-17: the PPA expert printed a v0.9
    # createSurface blob to a user). Strip it at the boundary; the workbench
    # still renders from the tool result via the registered mapping.
    _a2ui_stripper = None if a2ui_cfg.enabled else make_authored_a2ui_stripper()

    async def _composed_after_model(callback_context: object, llm_response: object) -> None:
        await _budget_after(callback_context, llm_response)
        if _a2ui_stripper is not None:
            await _a2ui_stripper(callback_context, llm_response)

    # M2B-BACKEND (MCP-APP-INTEGRATIONS): tag OTel spans on every MCP tool
    # call with mcp_app.server_id, and mcp_app.has_ui_resource=true when the
    # tool returned an EmbeddedResource carrying a UI app. Composed AFTER the
    # existing callbacks so permission-enforcer / large-output handlers keep
    # their override semantics; observability is purely additive.
    # before_tool chain: permission enforcer (skips transfer_to_agent — it's an
    # internal control tool) → the handoff floor policy (v6.10.0; only present
    # when this skill has delegates) → MCP tagger. The policy returns the
    # elicitation envelope for a confirm/cwf transfer, short-circuiting it.
    _perm_enforcer = make_permission_enforcer(user.email, user.domain)
    if delegate_map:
        _perm_enforcer = compose_before_tool_callbacks(
            _perm_enforcer,
            make_handoff_policy_callback(skill_config.skill_id, delegate_map),
        )
    _before_tool = compose_before_tool_callbacks(
        _perm_enforcer,
        make_mcp_before_tool_callback(),
    )
    # after_tool chain: _handle_large_output may rewrite the response (offload
    # pointer); the MCP tagger + the result→A2UI emitter are purely
    # observational and run against the effective response. The A2UI emitter
    # (tool-results-as-a2ui / 7.3) renders any tool with a registered mapping
    # onto the workspace surface, out of the model's context.
    _after_tool = compose_after_tool_callbacks(
        _handle_large_output,
        make_mcp_after_tool_callback(),
        make_a2ui_result_emitter(),
    )

    return LlmAgent(
        name=_safe_agent_name(skill_config.skill_id),
        model=model,
        # Chained InstructionProviders, applied LEFT-TO-RIGHT over the raw
        # skill instructions. `wrap_with_platform_preamble` is FIRST so it
        # PREPENDS the admin-configured platform preamble (shared identity /
        # house-style, v6.14.0) — the skill body that follows takes precedence
        # for its domain. Every later wrapper APPENDS its block after the skill
        # body, in order:
        #   * iframe-context (sprint 1.25): mcp_app_context.* block when
        #     an MCP App iframe pushed `ui/update-model-context`.
        #   * A2UI surface-context (sprint 2.10): a2ui_surface_context
        #     block when frontend SurfaceModels have active data OR a
        #     user dispatched an A2uiClientAction.
        #   * saved-forms / today / output-format (SVG-fence) tails.
        # Each wrapper passes through unchanged when its input is empty
        # (no state / preamble disabled), so this is safe to apply
        # unconditionally for every skill. Adding another wrapper later is
        # just one more argument here — no nesting to re-order.
        instruction=compose_instruction_providers(
            skill_config.instructions,
            wrap_with_platform_preamble,
            wrap_with_iframe_context,
            wrap_with_a2ui_surface_context,
            wrap_with_saved_forms,
            wrap_with_today,
            wrap_with_output_format,
        ),
        description=skill_config.description,
        tools=tools,
        sub_agents=sub_agents,
        planner=planner,
        code_executor=code_executor,
        before_agent_callback=_composed_before_agent,
        before_model_callback=_composed_before_model,
        after_agent_callback=_composed_after_agent,
        after_model_callback=_composed_after_model,
        before_tool_callback=_before_tool,
        after_tool_callback=_after_tool,
    )


def create_agent_with_thinking(
    skill_config: SkillConfig,
    user: User,
    *,
    access_context: AccessContext | None = None,
) -> LlmAgent | _HeuristicRouter:
    """Dispatch to the three-tier thinking strategy.

    - thinking_model unset → single `create_agent(...)` (planner may be
      attached for Gemini via `_planner_for`, at the skill's `thinking:` depth).
    - thinking_model set → two agents built (fast from `metadata.model`,
      thinking from `metadata.thinking_model`), wrapped in `_HeuristicRouter`.
      Each gets an EXPLICIT planner: fast at the skill's declared depth,
      thinking always DYNAMIC (both Gemini-only).

    See module docstring for the three tiers in full.
    """
    md = skill_config.skill_metadata
    if md.thinking_model is None:
        return create_agent(skill_config, user, access_context=access_context)

    # Tier 3: two agents + picker. Build both via the same recursive factory
    # so sub-skills/tools/callbacks stay wired identically.
    #
    # Each agent gets an EXPLICIT planner for its role — the fast agent at the
    # skill's declared depth, the thinking agent always DYNAMIC (being the
    # thinking one IS the point). Previously both were built with no planner at
    # all, so these five skills — the deepest in the fleet — reasoned at the
    # bare API default with include_thoughts unset, i.e. thought silently with
    # a dark ThinkingPanel. `_planner_at` returns None for a Claude/OpenAI
    # thinking_model, which keeps their reasoning_effort path untouched.
    fast = create_agent(
        skill_config,
        user,
        access_context=access_context,
        _planner_override=_planner_at(ThinkDepth(md.thinking), md.model),
    )
    thinking = create_agent(
        skill_config,
        user,
        access_context=access_context,
        _model_override=md.thinking_model,
        _planner_override=_planner_at(ThinkDepth.DYNAMIC, md.thinking_model),
    )
    return _HeuristicRouter(fast=fast, thinking=thinking, picker=_should_think)
