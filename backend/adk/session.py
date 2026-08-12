"""ADK service factories — env-var-driven backend selection.

Returns Vertex AI Agent Engine backends when ``AGENT_ENGINE_ID`` is set,
in-memory backends otherwise. Local dev points at the **dev Agent Engine**
(same pattern as Firebase/Firestore: laptop talks to real cloud resources via
ADC) so chat history survives uvicorn auto-reloads and is observable in the
same place as Cloud Run dev.

Service URI helpers are used by get_fast_api_app() which accepts URI strings.
Direct service constructors are available for testing and custom wiring.
"""

from __future__ import annotations

import logging
import os

from google.adk.apps.app import EventsCompactionConfig
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService, VertexAiMemoryBankService
from google.adk.sessions import InMemorySessionService, VertexAiSessionService

from config.gcp import require_gcp_project

logger = logging.getLogger(__name__)

# Model-aware compaction config. See backend/config/models.yaml for the full
# model registry. EventsCompactionConfig lives on App, not Agent or Runner.
#
# COMPACTION IS LOSSY AND INVISIBLE — read this before tuning the numbers.
# When a compaction fires, ADK materialises a summary event and *filters the
# raw events out of the request sent to the model*
# (`flows/llm_flows/contents.py::_process_compaction_events`). The raw events
# stay in the session store, so the UI transcript, the Firestore mirror and the
# Activity tab all still show every turn — but the model can no longer see them,
# and has no way to reach back for them. A user watching a full transcript
# reasonably assumes the model sees it too.
#
# That is not hypothetical: at the 2026-08-06 ONE UAT a ~12-turn expert
# conversation compacted once at turn 10, and the "summarise everything we just
# worked out" payoff request produced the wrong material. See
# docs/design/v6.23.0/conversation-context-fidelity.md.
#
# TWO TRIGGERS, AND THEY ARE NOT INTERCHANGEABLE (verified on google-adk 1.31.1):
#
#   token_threshold      — fires on real context pressure. Checked pre-request by
#                          `CompactionRequestProcessor`
#                          (flows/llm_flows/compaction.py) off
#                          `invocation_context.events_compaction_config`, and
#                          again post-invocation inside
#                          `_run_compaction_for_sliding_window` off
#                          `app.events_compaction_config`. This is the trigger we
#                          want doing the work.
#   compaction_interval  — fires on a raw count of user turns, blind to their
#                          size. Checked post-invocation by the Runner
#                          (runners.py:622).
#
# Turn count is the wrong trigger on a 1M-token model: ten short clarifying
# turns might be 8K tokens, and discarding them buys nothing while losing
# exactly the detail the user spent those turns establishing. So the token
# threshold is the primary mechanism and the interval is a backstop, set high
# enough that it only catches pathological sessions (many huge turns that
# somehow never trip the token check).
#
# Thresholds are ~25% of the usable window: early enough that one large turn
# can't blow the context, late enough that a normal expert conversation never
# compacts at all.
#
# `event_retention_size` is REQUIRED whenever `token_threshold` is set (ADK's
# model validator rejects one without the other) and is the token-mode analogue
# of `overlap_size`: when token compaction fires, the last N *raw events*
# survive uncompacted. Note events, NOT turns — one turn is several events
# (user message, tool call, tool response, model reply), so a tool-heavy turn
# can be 4-5 events on its own. These are set generously because this is
# precisely the "what does the model still remember verbatim" knob.
#
# NOTE: gpt-5.4 must come before gpt-5 so the more-specific prefix wins.
_COMPACTION_CONFIGS = {
    # 1M context (Gemini 3.x, GPT-5.4)
    "gemini-": EventsCompactionConfig(
        compaction_interval=40, overlap_size=5, token_threshold=250_000, event_retention_size=60
    ),
    "gpt-5.4": EventsCompactionConfig(
        compaction_interval=40, overlap_size=5, token_threshold=250_000, event_retention_size=60
    ),
    # 200K-400K context (Claude, other GPT-5.x)
    "claude-": EventsCompactionConfig(
        compaction_interval=20, overlap_size=4, token_threshold=120_000, event_retention_size=40
    ),
    "gpt-5": EventsCompactionConfig(
        compaction_interval=20, overlap_size=4, token_threshold=120_000, event_retention_size=40
    ),
}
# Unknown model → assume the SMALLEST window. Compacting too eagerly degrades an
# answer; overflowing the context fails the turn outright.
_DEFAULT_COMPACTION = EventsCompactionConfig(
    compaction_interval=20, overlap_size=4, token_threshold=120_000, event_retention_size=40
)

# Ops override for the token trigger, so a threshold can be tuned (or compaction
# effectively disabled for a debugging session) without a redeploy. Applies to
# every family — it is an escape hatch, not a second tuning surface.
_TOKEN_THRESHOLD_ENV = "COMPACTION_TOKEN_THRESHOLD"

# COMPACTION-LATENCY M1 — how much higher the PRE-REQUEST trigger sits than the
# routine one. Measured cost of compaction (2026-08-06, 18 real turns):
#
#     TTFT   14,682 -> 28,326 ms   (+13.6s, paid before the answer starts)
#     tail    3,141 -> 31,330 ms   (+28.2s, paid after it finishes)
#     worst turn: 90s total. And EVERY compacting turn compacted TWICE.
#
# Twice, because ADK runs two independent compaction paths and both fired:
#
#   pre-request       CompactionRequestProcessor, off invocation_context
#                     -> lands squarely in TTFT: the user stares at nothing
#                        while we summarise a conversation they can already see
#   post-invocation   Runner, off app.events_compaction_config
#                     -> runs at the END of a turn, i.e. exactly when the user
#                        starts reading. Already anticipatory, and free if we
#                        stop blocking on it.
#
# They read DIFFERENT config objects (verified on google-adk 1.31.1), which is
# the whole trick: raise the threshold on the per-invocation copy and the
# pre-request path stops firing, while the App-level config keeps the
# post-invocation path doing the routine work.
#
# The pre-request path is NOT disabled — it is the safety net that stops a turn
# exceeding the model's context window, and a failed turn is worse than a slow
# one. It is moved to a level only a genuinely at-risk turn reaches, so the user
# waits only when the alternative is an error.
#
# DERIVED FROM THE MODEL'S REAL WINDOW, not a multiple of the routine threshold.
# The first cut used `routine * 3` and it FAILED IN MEASUREMENT: a relative
# threshold moves with the routine one, so once a conversation is big enough it
# crosses both and the pre-request path fires again. Live run, 2026-08-06:
# turn 15 compacted once with TTFT at baseline (the fix working), turn 16
# compacted twice with TTFT +14s (the fix defeated). "Emergency" has to be an
# ABSOLUTE line near the point of actual failure, or it is just a second
# routine threshold.
_EMERGENCY_WINDOW_FRACTION = 0.8

# Fallback when the model isn't in the registry (a raw api name, a fork's own
# model). Assumes the smallest window we ship — an emergency threshold that is
# too LOW merely compacts in-request sooner, which is the pre-M1 behaviour;
# too HIGH risks a failed turn.
_FALLBACK_CONTEXT_WINDOW = 200_000

# Built once, shared by every config copy. The summarizer is stateless and
# resolving a model chain is not free, so rebuilding it per call would put a
# registry lookup on a path that runs for every agent construction.
_summarizer_singleton = None
_summarizer_built = False


def _compaction_summarizer():
    """The explicit summarizer (see adk/compaction_summarizer.py).

    Lazy + memoised, including a memoised None: if the model chain can't be
    resolved we must not retry the lookup on every call, and ADK falls back to
    its own default summarizer (which drops tool results but still works).
    """
    global _summarizer_singleton, _summarizer_built
    if not _summarizer_built:
        from adk.compaction_summarizer import build_compaction_summarizer

        _summarizer_singleton = build_compaction_summarizer()
        _summarizer_built = True
    return _summarizer_singleton


def get_compaction_config(model_id: str) -> EventsCompactionConfig:
    """Return model-appropriate EventsCompactionConfig.

    Larger context windows get a higher token threshold and a higher turn-count
    backstop. An unrecognised model gets the SMALLEST window's config, because
    compacting too eagerly costs answer quality while overflowing the context
    fails the turn outright.

    ``COMPACTION_TOKEN_THRESHOLD`` overrides the token trigger for every family.

    Args:
        model_id: The model identifier string (e.g. "gemini-2.5-flash",
            "claude-sonnet-4-6"). Matched by prefix against the model family.

    Returns:
        EventsCompactionConfig tuned for the model's context window size. Always
        a FRESH copy carrying an explicit summarizer — see below.
    """
    config = _DEFAULT_COMPACTION
    for prefix, candidate in _COMPACTION_CONFIGS.items():
        if model_id.startswith(prefix):
            config = candidate
            break

    # Never hand out the module-level singleton. ADK's
    # `_ensure_compaction_summarizer` MUTATES the config in place
    # (`config.summarizer = LlmEventSummarizer(llm=agent.canonical_model)`), so
    # returning the shared object would let the first skill that compacts pin
    # its own model as the summarizer for every skill afterwards — a `lite`
    # front door would leave Claude and `pro` skills summarising on flash-lite
    # for the life of the container. Verified: without this copy the mutation
    # leaks into later callers AND into `app.events_compaction_config`.
    #
    # Setting `summarizer` ourselves also makes that ADK branch return early, so
    # the copy is belt-and-braces — but the copy is what makes it SAFE if the
    # summarizer ever fails to build and comes back None.
    config = config.model_copy(update={"summarizer": _compaction_summarizer()})

    override = os.environ.get(_TOKEN_THRESHOLD_ENV)
    if override:
        try:
            threshold = int(override)
        except ValueError:
            # Never let a typo'd env var silently restore turn-count compaction —
            # that failure would be invisible and would look like a model bug.
            logger.warning(
                "%s=%r is not an integer; ignoring and using the %s default (%s).",
                _TOKEN_THRESHOLD_ENV,
                override,
                model_id,
                config.token_threshold,
            )
        else:
            if threshold <= 0:
                logger.warning(
                    "%s=%d must be > 0 (ADK rejects it); ignoring.",
                    _TOKEN_THRESHOLD_ENV,
                    threshold,
                )
            else:
                logger.info(
                    "%s=%d overriding the %s default (%s).",
                    _TOKEN_THRESHOLD_ENV,
                    threshold,
                    model_id,
                    config.token_threshold,
                )
                return config.model_copy(update={"token_threshold": threshold})
    return config


def context_window_for(model_id: str) -> int:
    """The model's real context window, from the registry.

    Falls back to the smallest window we ship when the model isn't registered
    (a raw api name, or a fork's own model). Erring small is the safe direction:
    a low emergency threshold just compacts in-request sooner, which is only the
    pre-M1 behaviour, while a high one risks a failed turn.
    """
    if not model_id:
        return _FALLBACK_CONTEXT_WINDOW
    try:
        from config.models import entry_for, load_models_config

        # Tier name or registry id (`gemini-2-5-flash`).
        entry = entry_for(model_id)
        if entry is not None and entry.context_window:
            return int(entry.context_window)

        # Raw API NAME (`gemini-3.6-flash`). `entry_for` returns None for these
        # by design, and this is the form the running agent actually carries
        # (`agent.model.model`) — so without this branch every production lookup
        # would silently take the fallback and quietly halve the emergency line.
        for candidate in load_models_config().models:
            if candidate.api_name == model_id and candidate.context_window:
                return int(candidate.context_window)
    except Exception as exc:
        logger.debug("context window lookup failed for %r (%s); using fallback", model_id, exc)
    return _FALLBACK_CONTEXT_WINDOW


def emergency_compaction_config(config: EventsCompactionConfig, model_id: str = "") -> EventsCompactionConfig:
    """The same config with the token trigger raised to emergency-only.

    Applied to the PER-INVOCATION copy so the pre-request processor stops doing
    routine work. The App-level config is untouched, so post-invocation
    compaction — which runs at the end of a turn, while the user reads — still
    keeps the conversation in budget.

    The emergency threshold is an ABSOLUTE line derived from the model's context
    window, NOT a multiple of the routine threshold. The first cut used
    ``routine * 3`` and measurement killed it: a relative threshold rises with
    the routine one, so a large conversation crosses both and the pre-request
    path fires anyway (live: turn 15 fixed, turn 16 defeated). Emergency has to
    mean "this turn is about to overflow", not "somewhat more than usual".

    Never LOWERS the threshold — on a small-window model the derived value can
    land under the routine one, and lowering it would make the pre-request path
    fire *more* eagerly, the exact opposite of the point.

    Returns a COPY. Mutating the input would defeat the whole thing: these
    configs are shared, and ADK itself mutates ``summarizer`` in place.
    """
    if config.token_threshold is None:
        # No token trigger to demote (a config relying on the turn-count
        # backstop alone). Leave it exactly as it is.
        return config
    emergency = int(context_window_for(model_id) * _EMERGENCY_WINDOW_FRACTION)
    if emergency <= config.token_threshold:
        # No demotion possible. Legitimate on a genuinely small-window model,
        # but it ALSO happens when `model_id` couldn't be resolved and we fell
        # back to the smallest window — in which case the latency fix silently
        # does nothing. Log it, so a no-op is observable rather than assumed.
        logger.info(
            "compaction: no pre-request demotion for model=%r "
            "(emergency line %d <= routine %d) — routine compaction may still land in TTFT",
            model_id or "<unknown>",
            emergency,
            config.token_threshold,
        )
        return config
    return config.model_copy(update={"token_threshold": emergency})


def _normalize_agent_engine_id(value: str) -> str:
    """Accept either a full resource name or just the numeric ID; return numeric ID.

    ADK's VertexAiSessionService / VertexAiMemoryBankService expect the trailing
    numeric suffix. If a caller passes the full `projects/.../reasoningEngines/NNN`
    resource name, the SDK builds a URL with a doubled `reasoningEngines/` prefix
    and every session call 404s. Strip defensively so either form works.
    """
    return value.rstrip("/").rsplit("/", 1)[-1] if "/" in value else value


def _force_in_memory_session() -> bool:
    """Local-dev escape hatch — force InMemory* services even when
    AGENT_ENGINE_ID is set.

    Why: from a laptop the Vertex Agent Engine session-service round-trip
    to europe-west1 dominates per-turn TTFT (~5.7s of a 9s first-token
    time, per docs/design/v6.1.0/ttft-optimization.md M1 baseline).
    Cloud Run in europe-west1 pays only ~120ms for the same call, so
    production behaviour is unaffected — this flag is for laptops.

    Set ``AITANA_LOCAL_SESSION=memory`` in a developer's shell or
    ``backend/.env`` to opt in. Any other value (including unset) keeps
    Vertex when ``AGENT_ENGINE_ID`` is set, matching the historical
    default.

    The flag intentionally affects BOTH session AND memory services —
    they share the same ``AGENT_ENGINE_ID`` and the same per-turn
    round-trip pattern. Artifact service (GCS) is left alone; it's
    touched on document upload, not on every chat turn.
    """
    return os.environ.get("AITANA_LOCAL_SESSION", "").strip().lower() == "memory"


_session_service_singleton: InMemorySessionService | VertexAiSessionService | None = None


def _reset_session_service_for_tests() -> None:
    """Reset the singleton so tests can exercise different env-var combinations."""
    global _session_service_singleton
    _session_service_singleton = None


def get_session_service() -> InMemorySessionService | VertexAiSessionService:
    """Get session service — Vertex AI Agent Engine or in-memory.

    Returns a module-level singleton so all callers (skill_processor, messages
    endpoint) share the same in-memory store in local dev. In prod the Vertex
    AI service is stateless so multiple instances would be fine, but a
    singleton is still cheaper to construct.
    """
    global _session_service_singleton
    if _session_service_singleton is None:
        agent_engine_id = os.environ.get("AGENT_ENGINE_ID")
        if agent_engine_id and not _force_in_memory_session():
            # ResilientVertexSessionService (not the bare parent): the standalone
            # Vertex write path had no retry/loud-failure, so a transient error
            # silently dropped a conversation while the Firestore mirror kept
            # counting turns (issue #30). Import here to keep the module import
            # light and avoid a cycle.
            from adk.resilient_session import ResilientVertexSessionService

            _session_service_singleton = ResilientVertexSessionService(
                project=require_gcp_project(),
                location=os.environ["GOOGLE_CLOUD_LOCATION"],
                agent_engine_id=_normalize_agent_engine_id(agent_engine_id),
            )
            # Log the active backend ONCE per instance so "were sessions even
            # going to Vertex on this instance?" is answerable from logs (the
            # #30 divergence gave no such signal).
            logger.info("session-service: Vertex Agent Engine (resilient), engine=%s", agent_engine_id)
        else:
            _session_service_singleton = InMemorySessionService()
            logger.warning(
                "session-service: IN-MEMORY (AGENT_ENGINE_ID=%s, force_in_memory=%s) — "
                "sessions are NOT durable on this instance; a restart/scale loses them",
                bool(agent_engine_id),
                _force_in_memory_session(),
            )
    return _session_service_singleton


def get_memory_service() -> InMemoryMemoryService | VertexAiMemoryBankService:
    """Get memory service — Vertex AI Agent Engine or in-memory."""
    agent_engine_id = os.environ.get("AGENT_ENGINE_ID")
    if agent_engine_id and not _force_in_memory_session():
        return VertexAiMemoryBankService(
            project=require_gcp_project(),
            location=os.environ["GOOGLE_CLOUD_LOCATION"],
            agent_engine_id=_normalize_agent_engine_id(agent_engine_id),
        )
    return InMemoryMemoryService()


_artifact_service_singleton: InMemoryArtifactService | GcsArtifactService | None = None


def _reset_artifact_service_for_tests() -> None:
    """Reset the singleton so tests can exercise different env-var combinations."""
    global _artifact_service_singleton
    _artifact_service_singleton = None


def get_artifact_service() -> InMemoryArtifactService | GcsArtifactService:
    """Get artifact service — GCS or in-memory, process-level singleton.

    Singleton ensures the upload endpoint and ADK runner share the same
    InMemoryArtifactService in local dev. In prod GCS is shared by bucket name
    and a singleton is still cheaper to construct.
    """
    global _artifact_service_singleton
    if _artifact_service_singleton is None:
        bucket = os.environ.get("ADK_ARTIFACT_BUCKET")
        if bucket:
            _artifact_service_singleton = GcsArtifactService(bucket_name=bucket)
        else:
            _artifact_service_singleton = InMemoryArtifactService()
    return _artifact_service_singleton


# --- URI helpers for get_fast_api_app() ---


def get_session_service_uri() -> str | None:
    """Get session service URI for get_fast_api_app(). None = in-memory."""
    agent_engine_id = os.environ.get("AGENT_ENGINE_ID")
    if agent_engine_id and not _force_in_memory_session():
        return f"agentengine://{_normalize_agent_engine_id(agent_engine_id)}"
    return None


def get_artifact_service_uri() -> str | None:
    """Get artifact service URI for get_fast_api_app(). None = in-memory."""
    bucket = os.environ.get("ADK_ARTIFACT_BUCKET")
    if bucket:
        return f"gs://{bucket}"
    return None


def get_memory_service_uri() -> str | None:
    """Get memory service URI for get_fast_api_app(). None = in-memory."""
    agent_engine_id = os.environ.get("AGENT_ENGINE_ID")
    if agent_engine_id and not _force_in_memory_session():
        return f"agentengine://{_normalize_agent_engine_id(agent_engine_id)}"
    return None
