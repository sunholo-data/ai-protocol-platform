"""Skill request processor — orchestrates a single user turn.

Replaces v5's `process_assistant_request()` with an ADK-native flow:

  1. Look up the skill config; 404 if missing *or* not visible to the caller
     (existence leak prevented).
  2. Build a per-user LlmAgent via `adk.agent.create_agent_with_thinking`.
     The heuristic router picks `fast` vs `thinking` from the user message.
  3. Wrap the agent with `ag_ui_adk.ADKAgent`, using the shared singleton
     session service from `adk.session.get_session_service()` so sessions
     persist across requests within the same process.
  4. Construct an AG-UI `RunAgentInput` and yield each translated event
     as a dict.

Deliberately kept thin — the SSE endpoint owns auth, response shaping,
and header handling; this module just produces the event stream.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from ag_ui.core import RunAgentInput, UserMessage
from google.genai.errors import ClientError
from opentelemetry import trace

from adk import agent_cache as _agent_cache
from adk.agent import _HeuristicRouter, create_agent_with_thinking
from adk.agui import build_agui_adk_agent, stream_agui_events
from adk.model_errors import ErrorClass, ModelTurnError, classify
from adk.session import get_session_service
from auth.access_context import AccessContext
from auth.firebase_auth import User
from budget import BudgetExceededError
from db.models import SkillConfig
from skills.skill_config import get_skill, resolve_skill_ref

logger = logging.getLogger(__name__)

_session_service = get_session_service()


def record_shell_mode(skill: SkillConfig) -> str:
    """Tag the active request span with the skill's resolved page-level shell
    mode (v6.4.0 SHELL-MODES) so Cloud Trace can group sessions by shell.

    A skill with no `shell` block resolves to ``chat-primary`` — the same
    default the frontend ShellRouter applies. Returns the resolved mode.
    Safe when no span is recording (set_attribute is a no-op).
    """
    mode = skill.shell.mode if skill.shell else "chat-primary"
    trace.get_current_span().set_attribute("shell.mode", mode)
    return mode


class SkillNotFoundError(Exception):
    """Raised when a skill is missing OR not visible to the caller.

    The streaming endpoint collapses both cases into a 404 to avoid
    leaking skill existence to users who cannot see them.

    ``reason`` keeps the two apart for the SERVER LOG only — "no skill by that
    ref" and "exists but the caller lacks the tag" need completely different
    fixes, and collapsing them made a tag-gate rejection indistinguishable
    from a typo (2026-08-05: a bare 404 "Skill not found" cost a debugging
    session). It is deliberately NOT echoed to the client, which still gets an
    identical response either way.
    """

    def __init__(self, skill_id: str, reason: str = "missing") -> None:
        super().__init__(f"Skill not found: {skill_id!r} ({reason})")
        # ``skill_id`` is the REF the caller sent (may be a slug, not a doc id).
        self.skill_id = skill_id
        self.ref = skill_id
        self.reason = reason


async def process_skill_request(
    skill_id: str,
    user: User,
    access: AccessContext,
    session_id: str | None,
    message: str,
    attachments: list[dict[str, Any]] | None = None,
    document_ids: list[str] | None = None,
    resumed_session: bool = False,
    a2ui_surface_state: dict[str, Any] | None = None,
) -> AsyncGenerator[dict, None]:
    """Yield AG-UI events for one turn of `skill_id`.

    Args:
        skill_id: The skill to invoke.
        user: Authenticated caller (used for permission closures).
        access: Per-request access context for the skill-visibility check.
        session_id: Existing thread ID to resume, or None to start fresh.
        message: The user's message for this turn.
        attachments: Optional attachment metadata (not used in v6.0;
            reserved for v6.1).
        document_ids: Optional Firestore document IDs the user wants in
            context for this turn. The before_agent_callback loads each
            document's blocks as a separate session artifact so the AI
            can read all of them. Re-sending the same ids next turn is
            cheap — the loader skips ids it has already loaded.
        resumed_session: True when the user reached this chat by clicking
            a conversation thread from the per-document Conversations
            panel. Triggers eager doc injection in the LLM request so
            the agent doesn't have to discover the doc via
            ``load_artifacts``. Fresh chats stay on the standard flow.
        a2ui_surface_state: Optional per-turn snapshot of every active
            A2UI surface's data model, as captured by the frontend's
            ``readA2uiSurfaceState`` helper at sendMessage time. Shape:
            ``{surfaceId: {catalogId, dataModel}}``. Seeded into
            ``initial_state["a2ui_surface_state"]`` so the
            ``wrap_with_a2ui_surface_context`` InstructionProvider can
            inject it into the agent's prompt. None when no surfaces
            are active (the common case before any A2UI render).

    Raises:
        SkillNotFoundError: when the skill is missing or not readable.
    """
    # Canonical id first (the overwhelmingly common case, and the seam most
    # tests patch), then fall back to resolving a friendly slug (CLAUDE.md #9).
    # The frontend holds whichever identifier it happened to resolve — a UUID
    # for one-assistant, the slug for skill-authoring-assistant — and only the
    # UUID used to work, so the slug 404'd deployed while passing locally
    # (local fixtures use slug-as-doc-id).
    skill = get_skill(skill_id)
    if skill is None:
        skill = resolve_skill_ref(skill_id, getattr(user, "uid", None))
    if skill is None:
        logger.warning("skill stream: no skill matches ref %r (id or slug)", skill_id)
        raise SkillNotFoundError(skill_id, reason="missing")
    if not access.can_access_skill(skill):
        # Same 404 on the wire (no existence leak) — but say WHICH it was here,
        # with the gate that rejected, so this is one log line to diagnose.
        logger.warning(
            "skill stream: %s (%s) exists but caller uid=%s is not permitted — accessControl=%s caller_tags=%s",
            skill.skill_id,
            getattr(skill, "slug", "") or "",
            getattr(user, "uid", "?"),
            getattr(skill, "access_control", None),
            getattr(access, "group_tags", None),
        )
        raise SkillNotFoundError(skill_id, reason="forbidden")

    # Downstream must key off the CANONICAL id, never the alias the caller
    # sent — the session index, agent factory and surface-action binding gate
    # all compare raw ids (CLAUDE.md #9: normalize at the boundary).
    skill_id = skill.skill_id

    # v6.4.0 SHELL-MODES: tag the request span with the resolved shell mode.
    record_shell_mode(skill)

    # B1 (chat-history-fixes v6.1.0): synchronously create the session-index
    # row in Firestore *before* the SSE stream opens. The previous home for
    # this write was the ADK before_agent_callback, which fires inside the
    # async agent run — so a user reload between POST returning and the
    # callback completing 404'd on GET /api/sessions/{id}. Doing it here
    # closes the race window. The callback is now idempotent: it observes
    # the existing row and short-circuits.
    thread_id = session_id or f"thread-{uuid.uuid4().hex[:12]}"
    _ensure_session_index(thread_id, skill_id, user.uid, document_ids, getattr(user, "email", "") or "")

    # Pass the per-request access context so skill delegation is access-filtered
    # (v6.7.0 SKILL-DELEGATION). Without this the delegate access check in
    # create_agent is inert — a parent could hand a user a specialist they
    # cannot access.
    #
    # Cached by (skill_id, skill.updated_at, access) — the agent is a pure
    # function of those, so a repeat turn in the same session skips the full
    # rebuild (model chain + tools + MCP toolsets + recursive delegate builds).
    # See adk.agent_cache for the key/TTL rationale (v6.14.0 cold-start work).
    agent_or_router, _agent_cache_hit = _agent_cache.get_or_build(
        skill,
        access,
        lambda: create_agent_with_thinking(skill, user, access_context=access),
    )
    logger.debug("skill=%s agent_cache_%s", skill_id, "hit" if _agent_cache_hit else "miss")
    if isinstance(agent_or_router, _HeuristicRouter):
        agent = agent_or_router.pick_agent(message)
        routing_choice = "thinking" if agent is agent_or_router.thinking else "fast"
        logger.info("skill=%s routing=%s", skill_id, routing_choice)
    else:
        agent = agent_or_router
        routing_choice = "single"

    # Stash the resolved model + routing on the per-request LatencyTracker
    # so the structured ttft log line and any LATENCY_REPORT event can
    # surface them. Off mode short-circuits inside set_model.
    from observability.timing import STAGE_AGENT_FACTORY_DONE, get_current_tracker

    # TTFT mark: agent factory has finished. The gap from
    # session_index_done → agent_factory_done isolates pure factory cost
    # (model resolve + tool resolve + sub-agent build + planner) from the
    # downstream ag_ui_adk wrap + ADK runner setup that follows.
    # See docs/design/v6.1.0/ttft-optimization.md M1.
    get_current_tracker().mark(STAGE_AGENT_FACTORY_DONE)

    model_used = ""
    raw_model = getattr(agent, "model", None)
    if isinstance(raw_model, str):
        model_used = raw_model
    elif raw_model is not None:
        model_used = getattr(raw_model, "model", "") or str(raw_model)
    get_current_tracker().set_model(model_used, routing_choice)

    # Thread user.uid through so ag_ui_adk creates the Vertex session under
    # the same uid Firestore stores as owner_uid. Without this, ag_ui_adk's
    # default extractor uses f"thread_user_{thread_id}" — divergence point
    # documented in docs/design/v6.1.0/chat-history-deep-fixes-2.md (1.15).
    agui_agent = build_agui_adk_agent(agent, user_id=user.uid, session_service=_session_service)

    initial_state: dict[str, Any] = {}
    if document_ids:
        # NOTE: bare ``document_ids`` round-trips on the AG-UI wire because
        # ag_ui_adk emits it in STATE_SNAPSHOT. We considered ``temp:`` prefix
        # to suppress the round-trip, but ag_ui_adk applies wire state via
        # ``update_session_state`` → ``append_event`` → ADK's
        # ``_trim_temp_delta_state``, which strips temp keys *before*
        # persistence; ag_ui_adk then re-fetches the session via ``get_session``
        # so the temp value (only on a transient copy) is gone before the
        # runner starts. Temp prefix is for in-invocation callback writes, not
        # wire inputs. The bug is mitigated at the parser layer instead:
        # ``_extract_document_ids`` reads forwardedProps first and ignores the
        # round-tripped state value (see fast_api_app.py:298).
        initial_state["document_ids"] = list(document_ids)
    if resumed_session:
        # Read by make_document_injector — eager-inject loaded docs into
        # the first LLM request of every turn for resumed sessions.
        initial_state["app:resumed_session"] = True
    if a2ui_surface_state:
        # Sprint 2.10 — per-turn snapshot of every active A2UI surface's
        # dataModel + catalogId. The wrap_with_a2ui_surface_context
        # InstructionProvider reads this from ctx.state on the next
        # agent turn and injects the values into the system prompt
        # under the `a2ui_surface_context.{surfaceId}` namespace.
        # Empty/None bypasses (the snapshot is omitted for skills that
        # haven't rendered any A2UI yet — InstructionProvider is a
        # no-op when state has neither this key nor namespaced action
        # writes).
        initial_state["a2ui_surface_state"] = a2ui_surface_state
    run_input = RunAgentInput(
        threadId=thread_id,
        runId=f"run-{uuid.uuid4().hex[:8]}",
        state=initial_state,
        messages=[
            UserMessage(
                id=f"msg-{uuid.uuid4().hex[:8]}",
                role="user",
                content=message,
            )
        ],
        tools=[],
        context=[],
        forwardedProps={},
    )

    try:
        async for event in stream_agui_events(agui_agent, run_input):
            yield event
    except BudgetExceededError as exc:
        # Sprint 2.12 — the budget enforcer's before_model callback
        # refused the turn (cohort over cap). Translate to a typed
        # AG-UI RUN_ERROR carrying the decision's message + retry-after
        # so the frontend BudgetBanner can render a countdown. The
        # `code` field follows the existing VERTEX_AUTH_FAILED pattern;
        # `retry_after_seconds` rides as a passthrough field (the
        # RunErrorEventSchema is passthrough — extras survive).
        decision = exc.decision
        logger.warning(
            "skill=%s budget exceeded: identity_value=opaque retry_after=%ss",
            skill_id,
            decision.retry_after_seconds,
        )
        yield {
            "type": "RUN_ERROR",
            "message": decision.message or "Budget exceeded.",
            "code": "BUDGET_EXCEEDED",
            "retry_after_seconds": decision.retry_after_seconds,
        }
    except ClientError as exc:
        # Vertex AI / Gemini API failures bubble up as ClientError. Translate
        # to an AG-UI RUN_ERROR event so the chat UI can render an actionable
        # banner instead of a frozen stream. The most common cause in dev is
        # ADC quota_project drift surfaced as 401 CREDENTIALS_MISSING.
        message, code = _translate_client_error(exc)
        logger.error("skill=%s upstream API error: %s", skill_id, exc)
        yield {"type": "RUN_ERROR", "message": message, "code": code}
    except ModelTurnError as exc:
        # MODEL-RELIABILITY M3: ResilientLlm exhausted retries + the whole
        # fallback chain. The classification of the LAST failure rides on
        # the exception — emit it as-is.
        logger.error("skill=%s model turn failed after fallbacks: %s", skill_id, exc)
        yield _model_error_event(exc.error_class)
    except Exception as exc:
        # MODEL-RELIABILITY M2: LiteLLM (Claude/OpenAI) exceptions used to
        # propagate uncaught here — the stream just died and the user got a
        # generic network error 30-90s later. Classify: known provider
        # errors become typed RUN_ERRORs; anything unclassifiable re-raises
        # unchanged (a code bug should still crash loudly, not masquerade
        # as a model outage).
        error_class = classify(exc)
        if error_class.provider == "unknown":
            raise
        logger.error(
            "skill=%s model provider error (%s %s): %s",
            skill_id,
            error_class.provider,
            error_class.status,
            exc,
        )
        yield _model_error_event(error_class)


_MODEL_ERROR_MESSAGES = {
    "MODEL_RATE_LIMITED": "The AI model is receiving too many requests right now.",
    "MODEL_UNAVAILABLE": "The AI model for this skill is temporarily unavailable. Try again in a minute.",
    "MODEL_AUTH_FAILED": "The AI model rejected this deployment's credentials. This needs an operator fix.",
    "MODEL_REQUEST_INVALID": "The AI model could not accept this request (it may be too large).",
}


def _model_error_event(error_class: ErrorClass) -> dict:
    """Typed RUN_ERROR payload for a classified model failure.

    Mirrors the BUDGET_EXCEEDED shape: `code` for the frontend branch,
    `retry_after_seconds` as a passthrough extra when the provider gave one.
    Never includes prompt content — provider/status/code only.
    """
    event: dict = {
        "type": "RUN_ERROR",
        "message": _MODEL_ERROR_MESSAGES.get(error_class.code, _MODEL_ERROR_MESSAGES["MODEL_UNAVAILABLE"]),
        "code": error_class.code,
    }
    if error_class.retry_after is not None:
        event["retry_after_seconds"] = error_class.retry_after
    return event


def _ensure_session_index(
    thread_id: str,
    skill_id: str,
    owner_uid: str,
    document_ids: list[str] | None,
    owner_email: str = "",
) -> None:
    """Synchronously create the chat_sessions/{thread_id} row if absent,
    and ArrayUnion this turn's document_ids onto it whether or not the
    row already existed.

    See B1 in docs/design/v6.1.0/chat-history-fixes.md for the original
    race-fix that motivated the synchronous create. The doc-id ArrayUnion
    on every turn is the "stranded session" fix: a session that lands
    with ``documentIds=[]`` (e.g. user typed before opening a tab, or
    reloaded onto a 404'd session_id) would otherwise stay invisible
    from every per-doc Conversations panel — ``list_sessions_for_document``
    uses Firestore ``array_contains``, which skips empty lists. The
    async loader's own ``add_session_documents`` only fires after a
    successful artifact load and only on flush turns, so depending on
    it leaves a window where the user has clearly attached a doc to
    the chat but the doc panel shows "No conversations yet". Reified
    by ``test_session_index_document_ids_grow_when_doc_added_after_empty_first_turn``.

    Failures are logged and swallowed — the after_agent_callback still
    runs as a fallback flusher.
    """
    from db.chat_sessions import (
        add_session_documents,
        clear_provisional,
        create_session_index,
        get_session_index,
        owner_domain_of,
        set_session_skill,
    )

    try:
        existing = get_session_index(thread_id)
    except Exception as exc:
        logger.warning("session-index existence check failed for %s: %s", thread_id, exc)
        return

    docs = list(document_ids) if document_ids else []

    if existing is None:
        anchor_doc_id = docs[0] if docs else None
        access_control = _derive_initial_access_control(anchor_doc_id)
        try:
            create_session_index(
                session_id=thread_id,
                skill_id=skill_id,
                owner_uid=owner_uid,
                owner_domain=owner_domain_of(owner_email),
                access_control=access_control,
                document_ids=docs,
            )
            logger.info("chat_sessions/%s index created synchronously (owner=%s)", thread_id, owner_uid)
        except Exception as exc:
            logger.warning("synchronous session-index write failed for %s: %s", thread_id, exc)
        return

    # v6.10.0 unified-adk-handoff: the index FOLLOWS the current skill. The
    # confirm→switch re-issues this turn on the specialist over the SAME thread,
    # but the row was created on the door — so without this the door's skill_id
    # sticks and the surface-action binding gate (URL skill == session skill)
    # 403s the specialist's own form (the 2026-07-15 test failure). Any
    # authenticated chat turn from a different skill re-points the row.
    # First real turn on a row the bootstrap pre-created: promote it out of
    # provisional so it appears in history (and re-stamp firstMessageAt, which
    # was set at page-mount time). Best-effort — a failure here must never break
    # the turn; the row simply stays hidden until the next one.
    if getattr(existing, "provisional", False):
        try:
            # Clamp only against a stamp a real turn actually wrote. On a
            # bootstrap-created row that has never flushed (turn_count == 0),
            # ``last_message_at`` IS the page-mount time — clamping to it pins
            # firstMessageAt to page-mount, which is precisely the skew
            # clear_provisional exists to remove. No turns yet ⇒ "now" is the
            # first message's time.
            clear_provisional(
                thread_id,
                not_after=existing.last_message_at if (existing.turn_count or 0) > 0 else None,
            )
        except Exception as exc:
            logger.warning("session-index provisional clear failed for %s: %s", thread_id, exc)

    if existing.skill_id != skill_id:
        try:
            set_session_skill(thread_id, skill_id, existing.skill_id)
            logger.info(
                "chat_sessions/%s skill follows turn: %s -> %s",
                thread_id,
                existing.skill_id,
                skill_id,
            )
        except Exception as exc:
            logger.warning("session-index skill-follow update failed for %s: %s", thread_id, exc)

    # Row already exists. ArrayUnion the current turn's docs so a
    # session created with empty docs (turn 1 typed before opening a
    # tab) still shows up under the doc's panel as soon as the user
    # attaches one — without waiting for the async loader's own
    # add_session_documents call which only fires on flush turns.
    if docs:
        try:
            add_session_documents(thread_id, docs)
        except Exception as exc:
            logger.warning(
                "synchronous documentIds union failed for %s: %s",
                thread_id,
                exc,
            )


def _derive_initial_access_control(document_id: str | None):
    """Resolve initial access_control for a new session row from its anchor doc.

    Mirrors ``adk.callbacks._derive_access_control`` but kept local to this
    module to avoid importing private helpers across package boundaries.
    """
    from db.models.access import AccessControl

    if not document_id:
        return AccessControl(type="private")
    try:
        from db.firestore import get_document

        doc = get_document("parsed_documents", document_id)
        if doc and "accessControl" in doc:
            ac_data = doc["accessControl"]
            if isinstance(ac_data, dict):
                return AccessControl.model_validate(ac_data)
    except Exception as exc:
        logger.warning("could not derive access_control for new session: %s", exc)
    return AccessControl(type="private")


def _translate_client_error(exc: ClientError) -> tuple[str, str]:
    """Map a google.genai ClientError to a (user_message, error_code) pair."""
    status = getattr(exc, "code", None)
    raw = str(exc)
    if status == 401 or "CREDENTIALS_MISSING" in raw or "UNAUTHENTICATED" in raw:
        return (
            "Backend can't authenticate to Vertex AI. "
            "Local dev: run `gcloud auth application-default set-quota-project "
            "$GOOGLE_CLOUD_PROJECT` and restart the backend. "
            "Production: confirm the service account has roles/aiplatform.user.",
            "VERTEX_AUTH_FAILED",
        )
    return (f"Upstream API error ({status or '?'}): {raw}", "UPSTREAM_API_ERROR")
