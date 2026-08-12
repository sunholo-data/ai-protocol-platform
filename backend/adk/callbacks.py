"""ADK callback hooks for the Aitana platform.

Callbacks wired into every skill by `adk.agent.create_agent`:
  * `before_tool_callback`   = `make_permission_enforcer(email, domain)`
  * `before_agent_callback`  = `make_before_agent(skill_id)` composed with
                               `make_session_tracker(owner_uid)` — the latter
                               creates/initialises the ChatSessionIndex on the
                               first turn of a new session.
  * `after_agent_callback`   = `make_after_agent_response(owner_uid)` — bumps
                               counters in the index and generates a title
                               after turn 2.
  * `after_tool_callback`    = `_handle_large_output`

`make_*` factories capture per-user context in closures to avoid threading
it through ADK session state.

Debounce: turnCount / lastMessageAt are flushed to Firestore every
`_TURN_FLUSH_INTERVAL` turns OR when the title needs to be set — keeps
Firestore QPS low during bursty agent loops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from opentelemetry import trace

from adk.a2ui_result_render import (
    is_render_payload_tool,
    render_for_emit,
)
from auth.access_context import AccessContext
from auth.permissions import ToolPermissionDenied, can_use_tool
from db.chat_sessions import add_session_documents, get_session_index
from db.models.access import AccessControl

logger = logging.getLogger(__name__)

# When true, documents are stored in a per-user Vertex AI RAG corpus instead
# of ADK artifacts. The agent uses the search_documents FunctionTool for
# retrieval; eager full-doc injection is skipped. Default false so forks
# without Agent Engine are unaffected.
_RAG_DOCUMENTS_ENABLED = os.environ.get("RAG_DOCUMENTS_ENABLED", "").lower() in ("1", "true")

# RAG-mode session state keys
# STATE SCOPING (issue #38, 2026-07-27). ADK state prefixes are NOT cosmetic:
# `app:` is shared by EVERY session of the app — across all users and tenants —
# `user:` is shared across one user's sessions, and no prefix is session-only.
# These four were all `app:`, which was wrong in three different ways (proven on
# test: a turn counter climbed 125→131 across sessions belonging to BOTH
# yourcompany.com and acme-energy.example).
#   - docs_files / rag_corpus_name / doc_load_error describe ONE USER's private
#     RAG corpus → `user:`. As `app:` they were a cross-user leak: a user who had
#     uploaded nothing inherited another user's corpus name, and `search_documents`
#     would have queried THEIR documents (the "no documents yet" guard fails open
#     because the key is non-empty). RAG_DOCUMENTS_ENABLED=true on dev+test.
#   - the turn counter describes ONE SESSION → no prefix.
_STATE_DOCS_FILES = "user:docs_files"  # list[str] of doc_ids imported to the USER's RAG corpus
_STATE_RAG_CORPUS_NAME = "user:rag_corpus_name"  # per-user corpus (rag.corpus.get_or_create_user_corpus)

# Only flush counter updates every N turns to reduce Firestore write amplification.
_TURN_FLUSH_INTERVAL = 5

# Tool responses larger than this (in characters of their string form) are
# offloaded to an ADK artifact and replaced with a short pointer in the LLM
# context — keeps the agent from paying megabytes of tokens per turn.
_LARGE_OUTPUT_THRESHOLD = 50_000

# Tools whose result IS a UI render payload are exempt from the large-output
# offload above — offloading strands the render (the frontend / server-side
# A2UI transform receives the artifact pointer instead of the data). This is now
# REGISTRY-DRIVEN (tool-results-as-a2ui / 7.3): registering a result→A2UI
# mapping in ``adk.a2ui_result_render`` marks the tool as a render payload via
# ``is_render_payload_tool`` (imported at top) — retiring the hardcoded
# ``_RENDER_PAYLOAD_TOOLS`` set that used to live here. Adding a renderable tool
# needs a mapping, not an edit to this file.


# --- before_tool_callback ---

# ADK-internal control tools are not user-facing capabilities and must NOT be
# gated by the tool-permission system. `transfer_to_agent` (skill delegation)
# is already access-controlled upstream — the agent factory only wires delegate
# sub_agents the requesting user can access (SKILL-DELEGATION M1), so the
# transfer target set is pre-filtered. Gating it here would break auto-mode
# delegation for any user whose permission set omits it (and surface a
# confusing "Calling transfer_to_agent…" label).
_INTERNAL_CONTROL_TOOLS = frozenset({"transfer_to_agent"})


def make_permission_enforcer(
    user_email: str,
    user_domain: str,
) -> Any:
    """Return a ``before_tool_callback`` that enforces tool permissions."""

    def _enforcer(
        tool: BaseTool,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        tool_name = tool.name
        if tool_name in _INTERNAL_CONTROL_TOOLS:
            return None
        if not can_use_tool(user_email, user_domain, tool_name):
            logger.info(
                "perm: blocked %s for %s (tool=%s)",
                user_email,
                tool_context.agent_name,
                tool_name,
            )
            raise ToolPermissionDenied(user_email, tool_name)

        # TTFT: emit a STAGE_PROGRESS label per tool call so the UI can
        # show "Calling search…" instead of an indefinite cursor while
        # the model waits on the tool. Each call gets its own mark name
        # (suffixed by a per-turn counter) — same name twice would be
        # idempotent and the second tool's label would never fire.
        from observability.timing import STAGE_TOOL_CALL_STARTED, get_current_tracker

        tracker = get_current_tracker()
        tracker.mark(
            f"{STAGE_TOOL_CALL_STARTED}_{tracker.tools_invoked_count}",
            user_label=f"Calling {tool_name}…",
        )

        return None

    return _enforcer


# --- before_agent_callback ---


# v6.11.0 — any A2UI form submission arrives as an `[a2ui:action] {json}` chat
# message; persist it to session state so it's durable, resume-safe, and readable
# by tools / instruction providers (not lost the moment the turn ends).
_A2UI_SUBMISSION_RE = re.compile(r"^\[a2ui:([A-Za-z0-9_]+)\]\s*(\{.*\})\s*$", re.DOTALL)
# Session-state key: {action: latest submitted context}.
A2UI_FORMS_STATE_KEY = "a2ui_forms"


def _capture_a2ui_submission(state: Any, callback_context: Any) -> None:
    """Persist an ``[a2ui:action] {json}`` submission to ``state[a2ui_forms]``
    (latest context per action, session-scoped). Fail-open — never breaks the turn."""
    try:
        content = getattr(callback_context, "user_content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            return
        text = "".join(p.text for p in parts if getattr(p, "text", None)).strip()
        m = _A2UI_SUBMISSION_RE.match(text)
        if not m:
            return
        ctx = json.loads(m.group(2))
        if not isinstance(ctx, dict):
            return
        forms = dict(state.get(A2UI_FORMS_STATE_KEY) or {})
        forms[m.group(1)] = ctx
        state[A2UI_FORMS_STATE_KEY] = forms
    except Exception as exc:
        logger.warning("a2ui submission capture failed (suppressed): %s", exc)


def make_before_agent(
    skill_id: str,
    tool_configs: dict[str, Any] | None = None,
    access_context: AccessContext | None = None,
    *,
    delegation_parent_id: str | None = None,
    delegation_display: str = "",
    delegation_avatar: str = "",
) -> Any:
    """Return a ``before_agent_callback`` that:

    1. Annotates the current OTEL span with the original (pre-sanitization)
       ``skill_id`` and, if the SSE endpoint has set ``routing_choice`` on
       session state, that too.
    2. (RESOURCE-ACCESS M3) If ``tool_configs`` + ``access_context`` are
       provided, resolves any ``bucket_folders`` entries to signed URLs and
       stashes them under ``callback_context.state['signed_urls']``.
       Downstream tools then read URLs from state instead of re-hitting
       Firestore on every turn.
    3. (SKILL-DELEGATION M3) If ``delegation_parent_id`` is set, this agent was
       built as an auto-mode delegate; its before_agent fires exactly when the
       parent transfers to it, so emit the ``AGENT_DELEGATION`` marker here.

    Captures ``skill_id`` in a closure so we keep the original kebab-case /
    UUID form rather than the sanitized agent name.

    tool_configs shape (convention for M3):
        {"<tool_name>": {"bucket_folders": [{"bucket_id": "...", "folder_id": "..."}]}}
    TODO(v6.1): formalize this shape in SkillMetadata once the first real
    storage-backed tool lands.
    """

    def _callback(callback_context: Any) -> None:
        span = trace.get_current_span()
        span.set_attribute("skill_id", skill_id)
        state = callback_context.state if hasattr(callback_context, "state") else None
        routing_choice = state.get("routing_choice") if state is not None else None
        if routing_choice:
            span.set_attribute("routing_choice", routing_choice)

        # COMPACTION-LATENCY M1 — keep routine compaction out of TTFT.
        _demote_pre_request_compaction(callback_context)

        # Persist any A2UI form submission once per turn, at the root skill
        # (delegates see the same user message). Session-scoped, durable, resume-safe.
        if state is not None and not delegation_parent_id:
            _capture_a2ui_submission(state, callback_context)

        # Delegation handoff signal — fires on the delegate's activation.
        if delegation_parent_id:
            from observability.timing import get_current_tracker

            get_current_tracker().mark_delegation(
                parent=delegation_parent_id,
                target=skill_id,
                target_display=delegation_display or skill_id,
                mode="auto",
                avatar=delegation_avatar,
            )

        # Signed URL plumbing — only if caller wired in a ctx + non-empty configs
        if access_context is None or not tool_configs or state is None:
            return
        _populate_signed_urls(tool_configs, access_context, state)

    return _callback


def _demote_pre_request_compaction(callback_context: Any) -> None:
    """Raise this turn's PRE-REQUEST compaction trigger to emergency-only.

    Measured, 2026-08-06 over 18 real turns: compaction cost +13.6s of TTFT and
    +28.2s of post-answer tail, and **every compacting turn compacted twice** —
    ADK's pre-request and post-invocation paths both fired on the same turn.

    Those two paths read different config objects, which is what makes this a
    four-line fix rather than an ADK fight:

        pre-request      invocation_context.events_compaction_config  <- demoted here
        post-invocation  app.events_compaction_config                 <- untouched

    So routine compaction now happens only at the END of a turn, exactly when
    the user starts reading the answer, instead of ambushing their next
    question. The pre-request path stays armed at a much higher threshold
    because it is the safety net against exceeding the context window — a slow
    turn beats a failed one.

    Fail-open and silent: this is an optimisation, and a turn that runs with the
    routine threshold is merely slower, not broken. Never raise from here.
    """
    try:
        ictx = getattr(callback_context, "_invocation_context", None)
        config = getattr(ictx, "events_compaction_config", None) if ictx is not None else None
        if config is None:
            return
        from adk.compaction_settings import apply_threshold_overrides, compaction_enabled
        from adk.session import emergency_compaction_config

        # TUNING-CONSOLE (1b): admin thresholds win over the coded per-model
        # table. Applied here because this is the one place ADK exposes a
        # per-INVOCATION config — `App.events_compaction_config` is built once at
        # import and a Firestore edit can never reach it without a restart.
        config = apply_threshold_overrides(config, where="pre-request")

        if not compaction_enabled():
            # Admin switched the token trigger off. Clearing the threshold is how
            # ADK expresses "don't"; the sliding-window backstop is App-level and
            # deliberately still armed, so context can't grow unbounded.
            ictx.events_compaction_config = config.model_copy(update={"token_threshold": None})
            return

        # The emergency threshold is derived from THIS agent's context window,
        # so read the model actually running the turn rather than a default.
        agent = getattr(ictx, "agent", None)
        model = getattr(agent, "model", None)
        model_id = getattr(model, "model", None) or (model if isinstance(model, str) else "") or ""

        # Assign a COPY — this object may be shared with the App.
        ictx.events_compaction_config = emergency_compaction_config(config, model_id)
    except Exception as exc:
        logger.debug("could not demote pre-request compaction (harmless): %s", exc)


def _populate_signed_urls(
    tool_configs: dict[str, Any],
    ctx: AccessContext,
    state: Any,
) -> None:
    """Resolve tool_configs → folder configs → signed URLs. Never crashes the run.

    Looked-up folders that don't exist or the user can't access are skipped
    silently. If the IAM signer is unavailable, ``build_signed_urls_for_folders``
    sets ``state['signed_urls_unavailable']=True``.
    """
    # Lazy imports: keep callbacks.py light and avoid circular imports with
    # buckets/folder_config, which pulls db.firestore at import time.
    from auth.signed_urls import build_signed_urls_for_folders
    from buckets.folder_config import get_folder

    folder_refs: list[tuple[str, str]] = []
    for _tool, config in tool_configs.items():
        if not isinstance(config, dict):
            continue
        for entry in config.get("bucket_folders", []) or []:
            if isinstance(entry, dict) and "bucket_id" in entry and "folder_id" in entry:
                folder_refs.append((entry["bucket_id"], entry["folder_id"]))

    if not folder_refs:
        return

    folders = []
    for bucket_id, folder_id in folder_refs:
        try:
            folder = get_folder(bucket_id, folder_id)
        except Exception as exc:
            logger.warning("failed to load folder %s/%s: %s", bucket_id, folder_id, exc)
            continue
        if folder is not None:
            folders.append(folder)

    # Populate state even if folders is empty — callers can rely on the key.
    # Use __setitem__ on ADK's state proxy the same way existing callers do.
    temp: dict[str, Any] = {}
    build_signed_urls_for_folders(folders, ctx, state=temp)
    for key in ("signed_urls", "signed_urls_unavailable"):
        if key in temp:
            state[key] = temp[key]


# --- before_agent_callback: document loader ---

# Frontend sets this to True when the user enters a chat by clicking a
# conversation thread from the per-document Conversations panel — signal
# that the document context should be eagerly loaded into the LLM request
# so the agent doesn't have to discover it via load_artifacts (which it
# can fumble — calls with empty args, etc.). Fresh chats keep the standard
# tool-discovered flow.
_STATE_RESUMED_SESSION = "resumed_session"  # THIS session was resumed → session-scoped (#38)
# Tracks which doc ids have been *successfully loaded as artifacts*. Two
# invariants ride on this list:
#   1. The loader is idempotent across turns — re-running with the same
#      ids is a no-op, so adding a tab mid-session only loads the new doc.
#   2. The injector treats every id here as having a saved
#      doc:{id}.json artifact. Stranding an id with no artifact behind
#      it leaves the agent silently without context (the injector skips,
#      the LLM falls back to retrieve_artifact, and "I couldn't find an
#      artifact" lands in front of the user — the 2026-04-28 bug).
# Failures (exception OR blocks=None) are NOT recorded here so a transient
# Firestore hiccup or an in-flight parse self-heals on the next turn.
# `user:` not `app:` (#38): the intent was "survives across sessions" — meaning
# across THIS USER's sessions — but `app:` shares it with every user, so one
# user's loaded-doc ids suppressed another user's load (the orphan probe below
# is what kept this from being visible). `user:` gives the intended persistence
# without the cross-user bleed.
_STATE_DOCS_LOADED = "user:docs_loaded"
# Map of doc_id -> error string for any doc that failed to load. Per-doc so a
# single bad doc doesn't suppress the error message for a different one.
_STATE_DOC_LOAD_ERROR = "user:doc_load_error"  # per-user load errors — see the scoping note at the top


def _resolve_user_id(callback_context: Any, state: Any) -> str:
    """Return the authenticated user id for the turn.

    The `user:id` / `user_id` state keys are NEVER populated by the request path
    (only read), so relying on them silently skipped RAG doc-loading on every
    turn. The ADK invocation always carries the real uid (skill_processor passes
    `user_id=user.uid` to build_agui_adk_agent → session.user_id), so prefer that
    and fall back to state for any path that does set it.
    """
    inv = getattr(callback_context, "_invocation_context", None)
    uid = getattr(inv, "user_id", None) if inv is not None else None
    if uid:
        return uid
    try:
        return state.get("user:id") or state.get("user_id") or ""
    except Exception:
        return ""


async def _rag_loader(callback_context: Any, state: Any, document_ids: list[str]) -> None:
    """RAG path for the document loader — imports GCS documents into the user's corpus.

    Replaces the artifact save/orphan-probe path when ``RAG_DOCUMENTS_ENABLED=true``.
    Idempotent: tracks imported doc ids in ``app:docs_files``; already-imported
    docs are skipped. Corpus name is cached in ``app:rag_corpus_name`` for the
    search_documents tool.
    """
    user_id: str = _resolve_user_id(callback_context, state)
    if not user_id:
        logger.warning("doc loader (RAG): no user_id (invocation or state) — skipping")
        return

    rag_loaded: list[str] = list(state.get(_STATE_DOCS_FILES) or [])
    rag_loaded_set = set(rag_loaded)
    to_import = [d for d in document_ids if d and d not in rag_loaded_set]

    logger.warning(
        "doc loader (RAG): turn start — document_ids=%s rag_loaded=%s",
        document_ids,
        rag_loaded,
    )

    from rag.corpus import get_or_create_user_corpus, import_document_from_gcs

    # Errors are surfaced to the AI (not just logs) via _inject_rag_doc_id_hint,
    # which reads this same map — "fail loudly to the AI, degrade gracefully to
    # the user." A RAG outage must NEVER kill the turn: doc_id tools
    # (compare_ppa_contracts / extract_ppa_clauses) read parsed_documents
    # directly, so the agent can still answer without the corpus.
    errors: dict[str, str] = dict(state.get(_STATE_DOC_LOAD_ERROR) or {})

    try:
        corpus_name = await get_or_create_user_corpus(user_id)
        state[_STATE_RAG_CORPUS_NAME] = corpus_name
    except Exception as exc:
        # Whole corpus unavailable (e.g. the Vertex RAG service agent can't read
        # the source bucket). Record it for every doc so the AI knows search is
        # down, and return without raising.
        reason = str(exc)[:200]
        logger.warning("doc loader (RAG): corpus unavailable — search degraded: %s", exc)
        for _d in to_import:
            errors[_d] = f"document search unavailable (RAG corpus error: {reason})"
        if errors:
            state[_STATE_DOC_LOAD_ERROR] = errors
        return

    if not to_import:
        logger.info("doc loader (RAG): nothing to import — corpus=%s", corpus_name)
        return

    from db.firestore import get_document as _get_fs_doc

    for doc_id in to_import:
        try:
            doc_data = _get_fs_doc("parsed_documents", doc_id) or {}
            gcs_uri: str | None = doc_data.get("sourceUrl")
            if not gcs_uri:
                errors[doc_id] = "no source file recorded — cannot add to document search"
                logger.warning("doc loader (RAG): no sourceUrl for doc:%s — skipping", doc_id)
                continue
            await import_document_from_gcs(corpus_name, gcs_uri)
            rag_loaded.append(doc_id)
            errors.pop(doc_id, None)  # a retry cleared a prior failure
            logger.info("doc loader (RAG): imported doc:%s from %s", doc_id, gcs_uri)
        except Exception as exc:
            # Fail loudly to the AI, degrade gracefully to the user: record WHY
            # this doc couldn't be added to search (surfaced in the id-hint) but
            # never raise — the turn continues and doc_id tools still work.
            reason = str(exc)[:200]
            logger.warning("doc loader (RAG): failed to import doc:%s — search degraded: %s", doc_id, exc)
            errors[doc_id] = f"could not add to document search (RAG import failed: {reason})"

    state[_STATE_DOCS_FILES] = rag_loaded
    # Reflect the current error map so a resolved retry doesn't leave a stale
    # "search unavailable" note misleading the agent on later turns.
    if errors:
        state[_STATE_DOC_LOAD_ERROR] = errors
    elif _STATE_DOC_LOAD_ERROR in state:
        del state[_STATE_DOC_LOAD_ERROR]

    if rag_loaded:
        session = getattr(callback_context, "session", None)
        session_id = getattr(session, "id", None) if session else None
        if session_id:
            try:
                add_session_documents(session_id, rag_loaded)
            except Exception as exc:
                logger.warning("doc loader (RAG): failed to update session docs: %s", exc)


def make_document_loader() -> Any:
    """Return a before_agent_callback that loads document blocks into session artifacts.

    Reads ``document_ids`` (list[str]) from session state — set by skill_processor
    when one or more documents are attached to the request. Saves each as a
    separate session-scoped artifact ``doc:{id}.json`` (application/json) which
    ``load_artifacts_tool`` auto-injects into the model's context.

    Incremental: tracks loaded ids in ``app:docs_loaded`` (list[str]) so when
    the user adds a tab mid-session we only load the *new* doc, and a failed
    doc isn't retried every turn. Failures are recorded per-doc in
    ``app:doc_load_error`` (dict[str, str]) — non-fatal.
    """

    async def _loader(callback_context: Any) -> None:
        state = getattr(callback_context, "state", None)
        if state is None:
            logger.info("doc loader: skipped — callback_context.state is None")
            return

        document_ids: list[str] = list(state.get("document_ids") or [])

        if _RAG_DOCUMENTS_ENABLED:
            await _rag_loader(callback_context, state, document_ids)
            return

        loaded_raw: list[str] = list(state.get(_STATE_DOCS_LOADED) or [])

        # WARNING level (not INFO) so this single forensic line surfaces in
        # .dev-logs/backend.log without re-configuring Python's root logger.
        # See docs/design/v6.1.0/multi-doc-context-fix.md (1.22) — D1.
        logger.warning(
            "doc loader: turn start — document_ids=%s prior loaded=%s",
            document_ids,
            loaded_raw,
        )

        # Self-heal sessions that were stranded by the pre-2026-04-28 loader,
        # where a failed load still appended the id to _STATE_DOCS_LOADED. The
        # injector's load_artifact then returned nothing and the agent told
        # the user "I couldn't find an artifact". Probe each prior-loaded id;
        # drop ones whose artifact is missing so they re-load below.
        loaded: list[str] = []
        orphans: list[str] = []
        for doc_id in loaded_raw:
            try:
                art = await callback_context.load_artifact(filename=f"doc:{doc_id}.json")
            except Exception as exc:
                logger.warning("doc loader: orphan probe error for %s: %s", doc_id, exc)
                orphans.append(doc_id)
                continue
            if art is None or getattr(art, "inline_data", None) is None:
                orphans.append(doc_id)
                continue
            loaded.append(doc_id)
        if orphans:
            logger.warning(
                "doc loader: dropping %d orphaned id(s) from app:docs_loaded "
                "(no artifact behind them) — will re-load: %s",
                len(orphans),
                orphans,
            )
        loaded_set = set(loaded)

        to_load = [d for d in document_ids if d and d not in loaded_set]
        if not to_load:
            # Initialise the flag so the absence of docs is also recorded.
            state[_STATE_DOCS_LOADED] = loaded
            logger.info("doc loader: nothing to load — verified loaded=%s", loaded)
            return

        logger.info("doc loader: will load %d new doc(s): %s", len(to_load), to_load)

        from google.genai.types import Blob, Part

        from tools.documents.context import build_document_context

        errors: dict[str, str] = dict(state.get(_STATE_DOC_LOAD_ERROR) or {})
        successfully_loaded: list[str] = []

        for doc_id in to_load:
            try:
                _content, blocks = build_document_context(doc_id, mode="blocks")
                if not blocks:
                    errors[doc_id] = (
                        "Document has no parsed content. Re-upload the document to make it available to the AI."
                    )
                    logger.warning("document loader: no blocks for doc:%s — skipping artifact", doc_id)
                    continue
                artifact = Part(
                    inline_data=Blob(
                        data=json.dumps(blocks).encode("utf-8"),
                        mime_type="application/json",
                    )
                )
                await callback_context.save_artifact(
                    filename=f"doc:{doc_id}.json",
                    artifact=artifact,
                )
                successfully_loaded.append(doc_id)
                # Retry succeeded: clear any stale error from a prior turn.
                errors.pop(doc_id, None)
                logger.info(
                    "document artifact saved: doc:%s.json (%d blocks)",
                    doc_id,
                    len(blocks),
                )
            except Exception as exc:
                logger.warning("document loader failed for %s: %s", doc_id, exc)
                errors[doc_id] = str(exc)

        loaded.extend(successfully_loaded)
        state[_STATE_DOCS_LOADED] = loaded
        # Reflect the current error map. Clear it back to {} when a retry
        # resolved every prior failure — leaving "Firestore unavailable"
        # in state for a doc that's now happily attached would mislead
        # the agent. Never introduce the key on a clean first run.
        if errors:
            state[_STATE_DOC_LOAD_ERROR] = errors
        elif _STATE_DOC_LOAD_ERROR in state:
            state[_STATE_DOC_LOAD_ERROR] = {}

        # Stranded-session-prevention (1.23) Option 2: when turn 1
        # requests docs and EVERY one fails, the session row will land
        # with ``documentIds=[]`` and stay invisible to per-doc panels
        # until a future turn succeeds. Per-doc WARNINGs above get lost
        # in noise; this single ERROR is the greppable signal.
        if to_load and not successfully_loaded and not loaded_raw:
            session_for_log = getattr(callback_context, "session", None)
            session_id_for_log = getattr(session_for_log, "id", "?") if session_for_log else "?"
            logger.error(
                "doc loader: TURN-1 INVARIANT VIOLATED — session=%s requested %d doc(s) "
                "%s but every load failed (%s). Session row will have documentIds=[] "
                "and will not appear in any per-doc Conversations panel until a "
                "subsequent turn succeeds.",
                session_id_for_log,
                len(to_load),
                to_load,
                list(errors),
            )

        # Mirror successfully loaded docs onto the ChatSessionIndex so the
        # session shows up under each doc's history panel. Best-effort: if
        # Firestore is down, the artifact load already succeeded — the
        # history panel is a discoverability nicety, not a correctness gate.
        if successfully_loaded:
            session = getattr(callback_context, "session", None)
            session_id = getattr(session, "id", None) if session else None
            if session_id:
                try:
                    from db.chat_sessions import add_session_documents

                    add_session_documents(session_id, successfully_loaded)
                except Exception as exc:
                    logger.warning(
                        "failed to update chat_sessions/%s documentIds: %s",
                        session_id,
                        exc,
                    )

    return _loader


def _inject_rag_doc_id_hint(callback_context: Any, llm_request: Any) -> None:
    """In RAG mode, inject an id-only hint naming the docs attached this turn.

    RAG routes full content to the corpus (search_documents), not context — so
    the model would otherwise never learn which docs the user attached, and a
    compare/extract skill (whose tools take an explicit doc_id) would tell the
    user "nothing is attached". This surfaces the doc_ids without the token cost
    of full injection. Mirrors the non-RAG injector's guards + insert position.
    """
    state = getattr(callback_context, "state", None)
    doc_ids = [d for d in list((state or {}).get("document_ids") or []) if d] if state is not None else []
    contents = getattr(llm_request, "contents", None)
    if not doc_ids or not contents:
        logger.info("doc injector: RAG mode — no attached doc ids to hint (docs=%s)", doc_ids)
        return
    last = contents[-1]
    if getattr(last, "role", None) != "user":
        return
    if any(getattr(p, "function_response", None) for p in (getattr(last, "parts", None) or [])):
        return  # mid-turn tool round-trip — don't re-inject

    from google.genai.types import Content, Part

    hint = (
        "[Attached documents this turn (doc_ids): "
        + ", ".join(doc_ids)
        + ". Treat these as the documents the user attached — do NOT ask them to "
        "re-specify or claim none are attached. Pass these doc_ids directly to your "
        "document tools (e.g. extract_ppa_clauses / compare_ppa_contracts); their "
        "full text is also searchable via search_documents.]"
    )

    # Surface RAG load failures to the AI (fail loudly to the AI, degrade
    # gracefully to the user). _rag_loader records why a doc couldn't be added to
    # the search corpus; tell the model so it uses the doc_id tools (which read
    # the parsed document directly, no corpus needed) and briefly warns the user
    # instead of silently producing a broken/empty answer.
    errors = (state.get(_STATE_DOC_LOAD_ERROR) or {}) if state is not None else {}
    failed = [(d, errors[d]) for d in doc_ids if d in errors]
    if failed:
        hint += (
            " [WARNING — document search (RAG) is degraded this turn: "
            + "; ".join(f"{d}: {msg}" for d, msg in failed)
            + ". search_documents may return nothing for these documents. Do NOT rely on it — "
            "analyse the documents with tools that take an explicit doc_id "
            "(compare_ppa_contracts / extract_ppa_clauses), which read the parsed document "
            "directly, and briefly tell the user that semantic document search is temporarily "
            "unavailable so your answer is based on direct analysis.]"
        )

    contents.insert(-1, Content(role="user", parts=[Part.from_text(text=hint)]))
    logger.info(
        "doc injector: RAG mode — injected id-hint for %d attached doc(s)%s",
        len(doc_ids),
        f" ({len(failed)} with load errors)" if failed else "",
    )


def make_document_injector() -> Any:
    """Return a ``before_model_callback`` that eagerly inlines loaded
    documents into the LLM request whenever any documents are attached
    to the session.

    Why: ADK's standard ``load_artifacts_tool`` makes the agent decide
    whether to call it — and Gemini sometimes calls it with empty
    ``artifact_names``, in which case nothing actually reaches the model
    and the agent confidently says "you haven't provided a document".
    The user has *signalled* intent by attaching the document (clicking
    a doc tab, or resuming a thread that had docs attached), so we skip
    that gamble and put the blocks directly in the LLM request.

    Scope (chat-history-deep-fixes-3 / Bug F): fires whenever
    ``state[_STATE_DOCS_LOADED]`` is non-empty, regardless of whether
    the session is fresh or resumed. Earlier scope ("only resumed")
    was a conservative initial choice that left fresh chats relying on
    Gemini's flaky tool-discovery — the user reported the failure
    end-to-end ("the tool tries to load artifacts but doesn't see the
    doc") so we drop the gate.

    Per-turn behaviour: only fires for the first model call of each turn
    (when the trailing content is the user's text, not a tool
    function_response) so we don't re-inject during in-turn tool
    roundtrips. Each turn's request is rebuilt from session events, so
    we have to inject again on every user turn — the alternative
    (persisting injected content into events) would bloat history.
    """

    async def _injector(callback_context: Any, llm_request: Any) -> None:
        # TTFT: mark the end of the before-model chain on every entry. This
        # is the moment immediately before the model is invoked — perfect
        # anchor for the "Thinking…" stage label. We mark even when there
        # are no docs to inject, since the model is about to run either
        # way.
        from observability.timing import STAGE_BEFORE_MODEL_DONE, get_current_tracker

        get_current_tracker().mark(STAGE_BEFORE_MODEL_DONE, user_label="Thinking…")

        if _RAG_DOCUMENTS_ENABLED:
            # Full-doc CONTENT goes to the corpus (search_documents retrieves
            # chunks) — that's the RAG token win, so we don't inject content.
            # But skills whose tools take an explicit doc_id (compare_ppa_contracts,
            # extract_ppa_clauses) still need to KNOW which docs are attached this
            # turn, so inject a compact id-only hint (no content).
            _inject_rag_doc_id_hint(callback_context, llm_request)
            return

        state = getattr(callback_context, "state", None)
        if state is None:
            logger.info("doc injector: skipped — state is None")
            return

        loaded: list[str] = list(state.get(_STATE_DOCS_LOADED) or [])
        if not loaded:
            logger.info(
                "doc injector: skipped — app:docs_loaded is empty (document_ids=%s)",
                state.get("document_ids"),
            )
            return

        contents = getattr(llm_request, "contents", None)
        if not contents:
            logger.info("doc injector: skipped — llm_request.contents empty")
            return
        last = contents[-1]
        if getattr(last, "role", None) != "user":
            logger.info(
                "doc injector: skipped — trailing content role=%s (not 'user')",
                getattr(last, "role", None),
            )
            return
        # If the last user content is actually a function_response from a
        # tool round-trip, this is a follow-up model call mid-turn —
        # don't re-inject.
        last_parts = getattr(last, "parts", None) or []
        if any(getattr(p, "function_response", None) for p in last_parts):
            logger.info("doc injector: skipped — mid-turn tool round-trip")
            return

        from google.genai.types import Content, Part

        injected = 0
        for doc_id in loaded:
            try:
                artifact = await callback_context.load_artifact(filename=f"doc:{doc_id}.json")
            except Exception as exc:
                logger.warning("doc injector: load_artifact failed for %s: %s", doc_id, exc)
                continue
            if not artifact or not getattr(artifact, "inline_data", None):
                logger.warning(
                    "doc injector: artifact missing for %s — orphan in app:docs_loaded "
                    "(loader's orphan recovery will retry next turn)",
                    doc_id,
                )
                continue
            data = artifact.inline_data.data
            if not data:
                logger.warning("doc injector: artifact empty for %s", doc_id)
                continue
            blocks_json = data.decode("utf-8", errors="replace") if isinstance(data, bytes | bytearray) else str(data)
            doc_content = Content(
                role="user",
                parts=[
                    Part.from_text(
                        text=(f"[Attached document: doc:{doc_id}.json — provided by the user]\n{blocks_json}")
                    )
                ],
            )
            # Insert before the latest user message so the model reads
            # docs first, then the question.
            contents.insert(-1, doc_content)
            injected += 1

        logger.info(
            "doc injector: prepended %d/%d document(s) to LLM request (loaded=%s)",
            injected,
            len(loaded),
            loaded,
        )

    return _injector


# --- session index callbacks (CHAT-HISTORY sprint) ---

# SESSION-scoped (#38). This gates "first turn of THIS session" — under `app:`
# the first session to initialise set it for EVERY later session, so the
# `_STATE_TURN_COUNT = 0` reset on the existing-row path never ran again and the
# counter climbed forever across users (125→131 across two tenants on test).
# The index row still appeared only because process_skill_request writes it
# synchronously, which masked the skipped callback.
_STATE_INITIALIZED = "chat_session_initialized"
_STATE_TURN_COUNT = "chat_session_turn_count"  # SESSION-scoped (no prefix) — see the scoping note at the top


def make_session_tracker(owner_uid: str, skill_id: str) -> Any:
    """Return a ``before_agent_callback`` that creates the ChatSessionIndex once.

    ADK has no dedicated "session created" hook; ``before_agent_callback``
    fires at the start of every turn. We use the
    ``chat_session_initialized`` state flag to run creation only once
    per session.

    ``owner_uid`` and ``skill_id`` are captured in closures from the
    authenticated request + the skill being invoked so we don't re-read
    them on every turn. The skill_id closure is what makes
    ``list_sessions_for_skill`` work — earlier the tracker pulled
    skill_id from session state, but nothing set it there, so every row
    landed in Firestore as ``skillId: "unknown"`` and the per-skill
    sidebar always came back empty.
    """

    def _tracker(callback_context: Any) -> None:
        state = getattr(callback_context, "state", None)
        if state is None:
            return
        if state.get(_STATE_INITIALIZED):
            return

        # First turn of this session — create the index row.
        session = getattr(callback_context, "session", None)
        session_id = getattr(session, "id", None) if session else None
        if not session_id:
            return

        # B1 idempotency (chat-history-fixes v6.1.0): process_skill_request
        # writes the index row synchronously at the top of the SSE stream so
        # GET /api/sessions/{id} works even if the user reloads before this
        # callback fires. If that synchronous write already landed, this
        # callback must NOT re-create the row — that would clobber any
        # title / turnCount / documentIds updates already on it.
        try:
            existing = get_session_index(session_id)
        except Exception as exc:
            logger.warning("idempotency check failed for %s, attempting create: %s", session_id, exc)
            existing = None
        if existing is not None:
            state[_STATE_INITIALIZED] = True
            state[_STATE_TURN_COUNT] = 0
            return

        # Multi-doc sessions: store the full list on the index so
        # ``list_sessions_for_document(doc_id)`` finds this session under
        # each of its docs via ``array_contains``. Access-control still
        # derives from the first doc — that's the session's "anchor" for
        # the initial visibility decision.
        document_ids: list[str] = list(state.get("document_ids") or [])
        anchor_doc_id: str | None = document_ids[0] if document_ids else None

        access_control = _derive_access_control(anchor_doc_id)

        try:
            from db.chat_sessions import create_session_index

            create_session_index(
                session_id=session_id,
                skill_id=skill_id,
                owner_uid=owner_uid,
                access_control=access_control,
                document_ids=document_ids,
            )
            state[_STATE_INITIALIZED] = True
            state[_STATE_TURN_COUNT] = 0
            logger.info("chat_sessions/%s index created (owner=%s)", session_id, owner_uid)
        except Exception as exc:
            logger.warning("failed to create session index for %s: %s", session_id, exc)

    return _tracker


def _derive_access_control(document_id: str | None) -> AccessControl:
    """Derive the initial access control for a new session.

    If the session is attached to a document, copy the document's
    accessControl. Otherwise default to private.
    """
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
        logger.warning("could not fetch document %s for access_control: %s", document_id, exc)
    return AccessControl(type="private")


def _try_generate_title(session: Any) -> str | None:
    """Attempt to generate a title from session events. Returns None on any failure."""
    events = list(getattr(session, "events", None) or [])
    try:
        from db.title_generator import generate_title_fast

        return generate_title_fast(events[:8])
    except Exception as exc:
        logger.warning("title generation raised: %s", exc)
        return None


def _flush_session_index(session_id: str, turn_count: int, title: str | None) -> None:
    """Write counter update (and optionally title) to Firestore."""
    try:
        from db.chat_sessions import update_session_fields

        update: dict[str, Any] = {
            "turnCount": turn_count,
            "lastMessageAt": datetime.now(UTC).isoformat(),
        }
        if title is not None:
            update["title"] = title
        update_session_fields(session_id, update)
    except Exception as exc:
        logger.warning("failed to update session index for %s: %s", session_id, exc)


def make_after_agent_response() -> Any:
    """Return an ``after_agent_callback`` that maintains the ChatSessionIndex.

    After each turn:
    - Increments the in-memory turn counter stored in session state.
    - Flushes ``turnCount`` + ``lastMessageAt`` to Firestore every
      ``_TURN_FLUSH_INTERVAL`` turns.
    - Triggers title generation after exactly turn 2 (first full exchange).
    """

    def _after_response(callback_context: Any) -> None:
        state = getattr(callback_context, "state", None)
        if state is None or not state.get(_STATE_INITIALIZED):
            return

        session = getattr(callback_context, "session", None)
        session_id = getattr(session, "id", None) if session else None
        if not session_id:
            return

        turn_count: int = int(state.get(_STATE_TURN_COUNT) or 0) + 1
        state[_STATE_TURN_COUNT] = turn_count

        # B3 (chat-history-fixes v6.1.0): retry title generation on a later
        # flush turn if turn 2 produced None (thin context). ``state["titleSet"]``
        # is set to True only on a successful generation, so retries stop
        # once the session has a title.
        needs_title_gen = turn_count == 2 or (turn_count >= 4 and not state.get("titleSet"))
        # Turn 1 ALWAYS flushes. The debounce below is for bursty loops, but with
        # interval 5 the first flush landed on turn 2 (via title-gen) — so after a
        # single message the row still carried the ``lastMessageAt`` written when
        # the row was created, which for a bootstrap-created row is page-MOUNT
        # time. The sidebar renders that stamp, so a conversation you just posted
        # to displayed as "52m ago" (observed live on test 2026-08-05) and sorted
        # by that stale time too. One extra write per session is a fair price for
        # the history list telling the truth from the first message.
        flush_counters = turn_count == 1 or (turn_count % _TURN_FLUSH_INTERVAL == 0) or needs_title_gen
        if not flush_counters:
            return

        title = _try_generate_title(session) if needs_title_gen else None
        if title is not None:
            state["titleSet"] = True
        _flush_session_index(session_id, turn_count, title)

        # B2 (chat-history-fixes v6.1.0): keep ``documentIds`` in sync with
        # the docs the user has open in this session. ``make_document_loader``
        # adds ids to state mid-conversation; without this ArrayUnion sync,
        # ``list_sessions_for_document`` would never see those docs because
        # they were missing from Firestore.
        try:
            add_session_documents(session_id, list(state.get("document_ids") or []))
        except Exception as exc:
            logger.warning("failed to sync documentIds for %s: %s", session_id, exc)

    return _after_response


# --- after_agent_callback composition ---


AfterAgentCallback = Callable[[Any], Any] | Callable[[Any], Awaitable[Any]]


def compose_after_agent_callbacks(*callbacks: AfterAgentCallback) -> Callable[[Any], Awaitable[Any]]:
    """Compose after-agent callbacks; the first non-None Content return wins.

    ADK semantics: an after-agent callback either mutates state and returns
    None, OR returns a follow-up ``genai.types.Content`` event that ADK
    appends to the response stream. The bespoke ``_composed_after_agent``
    wrapper in agent.py was annotated ``-> None`` and silently discarded
    each callback's return value, so a callback that wanted to surface a
    Card (e.g. structured-extraction JSON Part) had no path to the wire.

    This helper restores ADK's "first non-None return is the follow-up"
    contract while composing N callbacks in order. Sync and async
    callbacks are both accepted; awaitable returns are awaited.

    Args:
        *callbacks: One or more after-agent callbacks. Order matters —
            the first to return non-None short-circuits the chain
            (mirrors ADK's own composition semantics for tool callbacks).

    Returns:
        A single async callback that ADK can pass as
        ``after_agent_callback=...``.

    G26 contribution from gde-ap-agent fork (2026-06-03 feedback memo) —
    see docs/design/template/template-protocol-defaults.md.
    """

    async def _composed(callback_context: Any) -> Any:
        for cb in callbacks:
            result = cb(callback_context)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                return result
        return None

    return _composed


# --- after_agent_callback (legacy no-op — kept for tests that import it) ---


def _after_agent(callback_context: Any) -> None:
    """Retained for import compatibility; agent factory now uses make_after_agent_response."""
    return None


# --- after_tool_callback ---


async def _handle_large_output(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> Any:
    """Offload oversize tool responses to an ADK artifact.

    Returns the original response untouched when ``len(str(tool_response))``
    is at or below the threshold. For larger responses, saves the full
    payload as a Part-wrapped artifact and returns a short pointer string
    the model can reference.

    ASYNC (v6.10.0 fix): ``tool_context.save_artifact`` is a coroutine. This
    callback used to be sync and called it WITHOUT awaiting — the coroutine was
    never executed ("coroutine 'Context.save_artifact' was never awaited"), so
    the artifact was never written and every subsequent ``retrieve_artifact`` /
    ``load_artifact`` 404'd ("not found"). ADK awaits async after_tool callbacks,
    so making this async + awaiting the save is the fix.
    """
    tool_name = getattr(tool, "name", "tool")
    # Tools whose result IS a UI render payload must NOT be offloaded: the
    # server-side result→A2UI transform (or the legacy frontend workbench) reads
    # the typed JSON to draw the surface, so replacing the body with an artifact
    # pointer strands the render (the "[large response saved as artifact …]"
    # bug). The model also consumes these directly, so exempting them removes a
    # load_artifact round-trip too. Registry-driven — a tool is a render payload
    # iff it has a registered result→A2UI mapping (see adk.a2ui_result_render).
    if isinstance(tool_name, str) and is_render_payload_tool(tool_name):
        return tool_response

    text = str(tool_response)
    if len(text) <= _LARGE_OUTPUT_THRESHOLD:
        return tool_response

    artifact_name = f"{tool_name}_response_{tool_context.invocation_id}"
    # Lazy import — avoids pulling google.genai.types at module import time
    # (and keeps the test mock path simple).
    from google.genai import types as genai_types

    part = genai_types.Part.from_text(text=text)
    try:
        await tool_context.save_artifact(filename=artifact_name, artifact=part)
    except Exception as exc:  # pragma: no cover - ADK artifact service errors
        logger.warning("save_artifact failed for %s: %s", artifact_name, exc)
        return tool_response

    logger.info("offloaded large tool response to artifact %s (%d chars)", artifact_name, len(text))
    return (
        f"[large response saved as artifact '{artifact_name}' — "
        f"{len(text):,} chars. Load via tool_context.load_artifact('{artifact_name}') "
        f"if you need the full content.]"
    )


# --- after_tool_callback: result → A2UI surface emitter (Model B) ---


def _coerce_typed_result(tool_response: Any) -> Any:
    """Unwrap a tool response to the typed object a result→A2UI transform reads.

    Tools return either a dict/list or a JSON string
    (``compare_ppa_contracts`` returns ``model_dump_json()``). We also peel the
    ``{"result": …}`` envelope once — the server-side mirror of the client's
    ``src/lib/toolResult.ts`` (the two wire hazards handled once). Returns
    ``None`` when the payload isn't structured JSON (nothing to render).
    """
    value = tool_response
    # Bounded loop: at most a JSON-string decode + a couple of envelope peels.
    for _ in range(4):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped[0] not in "{[":
                return None
            try:
                value = json.loads(stripped)
            except (ValueError, TypeError):
                return None
        elif isinstance(value, dict) and set(value.keys()) == {"result"}:
            value = value["result"]
        else:
            break
    return value if isinstance(value, dict | list) else None


# Session-scoped state key prefix for rehydratable A2UI surfaces (7.5 M3). The
# session-history endpoint reads keys with this prefix to replay the workbench
# on resume. Session-scoped (no `app:` prefix) so surfaces never cross sessions.
A2UI_SURFACE_STATE_PREFIX = "a2ui_surface:"


def _stash_surface_for_resume(
    tool_context: ToolContext,
    rendered: Any,
    source_id: str,
    tool_name: str,
) -> None:
    """Persist a rendered A2UI surface into session state for resume rehydration.

    Fail-open: a stash failure logs and never breaks the turn (the live emit
    already reached the client). Stores the same payload shape the live CUSTOM
    event carries so the frontend replay path is identical to the live one.
    """
    try:
        state = getattr(tool_context, "state", None)
        if state is None:
            return
        payload = {
            "surfaceId": rendered.surface_id,
            "messages": rendered.messages,
            "artifact": rendered.artifact,
            "sourceId": source_id,
            "toolName": tool_name,
            # Epoch ms so the frontend index/timeline keeps its ordering across a
            # refresh (first-seen order). Overwrites on re-emit to a surface —
            # the surface's latest render is what rehydrates.
            "createdAt": datetime.now(UTC).timestamp() * 1000,
        }
        state[f"{A2UI_SURFACE_STATE_PREFIX}{rendered.surface_id}"] = json.dumps(payload)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("a2ui surface stash failed (suppressed): %s", exc)


# Session-state key (temp: — never persisted) where the A2UI emitter stashes the
# tool's CALL ARGS so a transform can read them. Added v6.23.0: an MCP result is
# just rows, with no trace of the request, so a transform had no way to tell an
# answer from an intermediate probe. See a2ui_bigquery_render.
A2UI_TOOL_ARGS_STATE_KEY = "temp:_a2ui_tool_args"


def make_a2ui_result_emitter() -> Callable[..., Any]:
    """Return an ``after_tool_callback`` that renders a registered tool result
    to A2UI and pushes it to the ``workspace`` surface as an out-of-model CUSTOM
    event (tool-results-as-a2ui / 7.3, Model B).

    Purely observational: always returns ``None`` so it never rewrites the tool
    response (composed as an observer AFTER ``_handle_large_output``, so it sees
    the un-offloaded render payload). Fail-open — a render bug logs at WARNING
    and never breaks the chat turn.

    A per-request emit counter makes each surface push's idempotency key unique
    (``{invocation_id}:{tool_name}:{seq}``) so progressive fill — extract →
    extract → compare each emitting a workspace update — is NOT deduped by the
    frontend SurfaceRegistry's ``consumedToolCallIds`` guard, while a genuine
    SSE re-delivery of the same event (same key) still collapses.
    """
    seq = 0

    def _emit(
        tool: BaseTool,
        args: dict[str, Any],
        tool_context: ToolContext,
        tool_response: Any,
    ) -> None:
        nonlocal seq
        try:
            tool_name = getattr(tool, "name", "")
            if not isinstance(tool_name, str) or not tool_name:
                return None
            typed = _coerce_typed_result(tool_response)
            if typed is None:
                # Some mapped tools (the web/enterprise search AgentTools) return
                # free TEXT but carry renderable side-data in tool_context.state
                # (their grounding sources). Let a registered mapping render from
                # context instead of bailing — the transform reads state, not the
                # body. Non-mapped text tools still short-circuit (unchanged).
                if not is_render_payload_tool(tool_name):
                    return None
                typed = {}
            # Make the CALL ARGS readable by the transform. Some results are only
            # interpretable alongside the request that produced them — the MCP
            # envelope, for instance, carries no trace of it, so a BigQuery result
            # cannot otherwise tell whether the agent meant it as the answer or as
            # an intermediate probe. `temp:` keeps it out of persisted state; the
            # transform reads it the same way search transforms read grounding
            # metadata (see backend/adk/CLAUDE.md trap 1).
            try:
                tool_context.state[A2UI_TOOL_ARGS_STATE_KEY] = args if isinstance(args, dict) else {}
            except Exception as exc:  # pragma: no cover - defensive, never break a turn
                logger.debug("a2ui emitter: could not stash tool args: %s", exc)
            rendered = render_for_emit(tool_name, typed, tool_context)
            if rendered is None:
                return None
            seq += 1
            invocation_id = getattr(tool_context, "invocation_id", "") or ""
            source_id = f"{invocation_id}:{tool_name}:{seq}"

            from observability.timing import get_current_tracker

            # Route to the mapping's declared artifact surface (7.5) with its
            # optional metadata — a new renderable tool gets its own workbench
            # tab just by declaring surface + artifact_meta in its mapping.
            get_current_tracker().emit_a2ui_surface(
                rendered.surface_id, rendered.messages, source_id, rendered.artifact
            )
            # Durability groundwork (7.5 M3): stash the rendered surface in
            # SESSION-scoped state so a page refresh / resume can rehydrate the
            # workbench (tabs + index) without re-running the tool. Keyed by
            # surface_id but WITHOUT the `app:` prefix on purpose — surface ids
            # like the stable "ppa_comparison" are NOT session-unique, so an
            # app-scoped (cross-session) key would leak one session's artifact
            # into another (CLAUDE.md cross-session rule). Session-scoped keeps
            # each session's workbench private + is still persisted. Latest emit
            # to a surface wins (its complete render). Generic: any mapped tool
            # rehydrates for free — no per-tool resume logic.
            _stash_surface_for_resume(tool_context, rendered, source_id, tool_name)
            logger.info(
                "a2ui-result: pushed %d msg(s) tool=%s surface=%s (source=%s)",
                len(rendered.messages),
                tool_name,
                rendered.surface_id,
                source_id,
            )
        except Exception as exc:
            logger.warning("a2ui result emit failed (suppressed): %s", exc)
        return None

    return _emit


# === Model-B guard: the agent must never hand-author A2UI into chat ==========
# CLAUDE.md #7 (protocols first) says a Model-B skill (`a2ui.enabled: false`)
# never authors UI — the backend result→A2UI emitter does. Disabling the toolset
# stops the agent CALLING an A2UI tool, but nothing stops it TYPING A2UI JSON as
# prose, which is exactly what shipped to a user on 2026-07-17: the PPA expert
# skipped `extract_ppa_clauses`, fetched raw content, then printed a v0.9
# createSurface/updateComponents blob into the chat. Instructions already said
# "do NOT author any UI" and were ignored — so this is enforced at the boundary
# rather than trusted to model goodwill.

_A2UI_MARKERS = ('"createSurface"', '"updateComponents"', '"updateDataModel"', '"deleteSurface"')
_A2UI_FENCE_RE = re.compile(r"```(?:json)?\s*(?:\[.*?\]|\{.*?\})\s*```", re.DOTALL)
_A2UI_BARE_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _looks_like_authored_a2ui(text: str) -> bool:
    """True when a blob carries the A2UI v0.9 wire shape (not just the words)."""
    return "v0.9" in text and any(marker in text for marker in _A2UI_MARKERS)


def strip_authored_a2ui(text: str) -> tuple[str, bool]:
    """Strip hand-authored A2UI JSON out of a model's chat text.

    Returns ``(cleaned_text, stripped?)``. Only removes blobs that actually look
    like A2UI v0.9 messages, so ordinary JSON the user asked for is untouched.
    """
    if not text or not _looks_like_authored_a2ui(text):
        return text, False

    stripped = False

    def _drop(match: re.Match[str]) -> str:
        nonlocal stripped
        if _looks_like_authored_a2ui(match.group(0)):
            stripped = True
            return ""
        return match.group(0)

    cleaned = _A2UI_FENCE_RE.sub(_drop, text)
    cleaned = _A2UI_BARE_ARRAY_RE.sub(_drop, cleaned)
    if not stripped:
        return text, False
    # Collapse the hole the blob left behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, True


def make_authored_a2ui_stripper():
    """after_model_callback that removes hand-authored A2UI from chat text.

    Mutates the response parts in place. The workbench still renders from the
    tool result via the registered result→A2UI mapping; this only stops the raw
    JSON reaching the user as a wall of text.
    """

    async def _after_model(callback_context: object, llm_response: object) -> None:
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if not text:
                continue
            cleaned, did_strip = strip_authored_a2ui(text)
            if did_strip:
                part.text = cleaned
                logger.warning(
                    "authored_a2ui_stripped: a Model-B agent hand-authored A2UI JSON into "
                    "chat text; stripped before it reached the user (the workbench renders "
                    "from the tool result, not from model-authored UI)"
                )

    return _after_model
