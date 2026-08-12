"""AG-UI / ADK glue for the skill streaming endpoint.

`ag_ui_adk.ADKAgent` already converts ADK events to AG-UI events (its
1.3kloc `event_translator.py` handles the full mapping). Rolling our own
translator just to re-emit the same SSE sequence would duplicate that
work and drift against upstream. Instead this module does the three
things the library does *not* do:

  * `build_agui_adk_agent(agent, ...)` — wraps an ADK agent with platform
    defaults (``app_name``, the three real backing services from
    ``adk.session``, thread-id-as-session-id) so the skill processor gets
    a ready-to-run bridge.
  * `stream_agui_events(agui_agent, run_input)` — serializes each AG-UI
    event to a JSON-safe dict (what the SSE layer writes to the wire).
  * **Terminal-event deduplication (G41 — template-agui-terminal-dedup.md):**
    when a tool call raises mid-stream, ``ag_ui_adk`` (per its current
    1.x line) can emit RUN_ERROR via the queue-based background path
    AND THEN fall through to a RUN_FINISHED emission in the surrounding
    try-block. @ag-ui/client's state machine correctly rejects the
    duplicate terminal event with "Cannot send event type 'RUN_FINISHED':
    The run has already errored". ``stream_agui_events`` enforces the
    spec invariant — at most ONE terminal event per run — by tracking
    whether we've already yielded RUN_ERROR/RUN_FINISHED and dropping
    any subsequent terminal events with a warning log. Surfaced by
    the gde-ap-agent fork (2026-06-06) during a long tool-throw demo.

Design reconciliation (2026-04-21): the AGENT-FACTORY sprint plan called
for a `_to_agui_event(adk_event)` helper "moved from the spike". The
spike used the library, not a hand-rolled translator, so there is no
such logic to move. The library boundary — `ADKAgent.run()` — is where
this module integrates instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui_adk import ADKAgent
from google.adk.agents import BaseAgent
from google.adk.artifacts import BaseArtifactService
from google.adk.memory import BaseMemoryService
from google.adk.sessions import BaseSessionService

logger = logging.getLogger(__name__)

APP_NAME = "aitana_platform"
_DEFAULT_APP_NAME = APP_NAME  # backwards-compat alias

# G41 (template-agui-terminal-dedup.md): the two AG-UI event types whose
# arrival closes a run. The spec mandates at most one per run; the
# `@ag-ui/client` state machine throws on a second one. We use the
# string values rather than the enum so this module doesn't have to
# import `EventType` (kept dep-light for fast test imports).
_TERMINAL_EVENT_TYPES = frozenset({"RUN_ERROR", "RUN_FINISHED"})

# MODEL-RELIABILITY M1: no-op CUSTOM event emitted during silent phases so
# (a) idle-timeout intermediaries (undici bodyTimeout, LB idle) see traffic
# and (b) the frontend mid-stream watchdog has something to reset on. A
# CUSTOM event — not an SSE comment — because @ag-ui/client's parser only
# surfaces `data:` lines to subscribers; a comment would keep the pipe warm
# but leave the watchdog blind. CAUTION: @ag-ui/client's state machine rejects
# ANY event before RUN_STARTED ("First event must be 'RUN_STARTED'"), so no
# CUSTOM (heartbeat, STAGE_PROGRESS, MODEL_RESOLVED) may PRECEDE it — pre-agent
# events are buffered in stream_agui_events and flushed only AFTER RUN_STARTED.
HEARTBEAT_EVENT_NAME = "HEARTBEAT"
_DEFAULT_HEARTBEAT_SECONDS = 20.0


def _heartbeat_interval(override: float | None) -> float:
    if override is not None:
        return override
    try:
        return float(os.environ.get("AGUI_HEARTBEAT_SECONDS", _DEFAULT_HEARTBEAT_SECONDS))
    except ValueError:
        return _DEFAULT_HEARTBEAT_SECONDS


def _heartbeat_event(n: int) -> dict:
    # Plain dict, same shape CustomEvent.model_dump(by_alias=True) produces —
    # built by hand to keep the hot path allocation-light.
    return {"type": "CUSTOM", "name": HEARTBEAT_EVENT_NAME, "value": {"n": n}}


def _deployment_app():
    """The `App` declared in ``backend/app.py``.

    Imported lazily and inside the function, not at module scope: ``app.py``
    imports from ``adk.*`` (agent factory, artifact tools, session services), so
    a top-level import here would close a cycle. It also builds the root agent
    and its whole tool tree at import time, which module-level would put GCP SDK
    construction on the critical path of anything that merely imports
    ``adk.agui`` — including the CLI and most unit tests.

    Kept as a seam (rather than inlined) so a test or a fork can substitute an
    App without monkeypatching a module attribute.
    """
    from app import app as deployment_app

    return deployment_app


def build_agui_adk_agent(
    agent: BaseAgent,
    *,
    user_id: str | None = None,
    session_service: BaseSessionService | None = None,
    memory_service: BaseMemoryService | None = None,
    artifact_service: BaseArtifactService | None = None,
    app_name: str = APP_NAME,
) -> ADKAgent:
    """Wrap a built ADK agent as an AG-UI middleware agent.

    Defaults every backing service to the singletons in ``adk.session`` so
    the production skill stream gets the *real* Vertex/GCS backends, not
    ag_ui_adk's silent in-memory fallback. Tests pass explicit services and
    keep working unchanged.

    ``user_id`` MUST be the authenticated Firebase uid in production paths
    (chat-history-deep-fixes-2 / 1.15). When omitted, ag_ui_adk falls back
    to a default extractor that derives the user_id from the AG-UI
    thread_id (``f"thread_user_{thread_id}"``). The Firestore
    ``chat_sessions/{id}.owner_uid`` is written from the Firebase uid, so
    the default extractor produces a Vertex session under a different
    user_id than the one we look it up by — every subsequent
    ``GET /api/sessions/{id}/messages`` then 500s with
    ``ValueError: Session ... does not belong to user``. Pass the Firebase
    uid here to keep the (app_name, user_id, session_id) triple consistent.

    ``use_in_memory_services=True`` is left set so the credential service
    (which we don't have a real backend for) gets ag_ui_adk's
    InMemoryCredentialService default. Our explicit
    ``session_service``/``memory_service``/``artifact_service`` arguments
    win over the in-memory fallback because ag_ui_adk uses
    ``provided or InMemoryX()`` — see
    ``ag_ui_adk/adk_agent.py:176-184``.

    ``use_thread_id_as_session_id=True`` so AG-UI threadIds map 1:1 onto
    ADK sessions; default is False (mints a fresh ADK session per turn
    and discards conversation memory between turns).
    """
    # Lazy import: adk.session imports heavy GCP SDKs whose presence we
    # don't want at module-import time (test isolation, fast CLI startup).
    from adk.session import (
        get_artifact_service,
        get_memory_service,
        get_session_service,
    )

    # v6.23.0 COMPACTION-WIRE M1 — build via `from_app` so the deployment's App
    # (and therefore `events_compaction_config`) reaches the Runner.
    #
    # WHY: ag_ui_adk only sets its internal `_app` from `from_app()`. Built the
    # old way (`ADKAgent(adk_agent=...)`), `_app` stays None and `_create_runner`
    # takes the component branch — `Runner(app_name=…, agent=…)` with NO App. A
    # Runner with `app is None` disables both compaction triggers
    # (runners.py:622 and :1480 each guard on it), which is why the entire
    # tuning table in `adk/session.py` had never affected a chat turn. Measured:
    # 25 turns at compaction_interval=10 produced 0 compactions.
    #
    # The App we pass carries OUR per-skill agent as root, not the deployment's
    # global root agent. `from_app` does `cls(adk_agent=app.root_agent, ...)`,
    # so handing it the App unmodified would make every skill execute the global
    # root agent — the PPA expert, the doc comparer and the Studio copilot all
    # silently becoming the same generic assistant, with no error raised.
    # `app_name` rides on the same copy (the App owns the name under `from_app`).
    base_app = _deployment_app()

    # TUNING-CONSOLE (1b) — the admin thresholds must be applied HERE, not only
    # in the before-agent callback.
    #
    # The two compaction paths read DIFFERENT config objects (findings log §1),
    # and the one that does the ROUTINE work is the post-invocation path, which
    # reads `app.events_compaction_config` (`runners.py:622`). The callback can
    # only reach `invocation_context.events_compaction_config` — the pre-request
    # path — and that value is then replaced by the emergency threshold anyway.
    # So an override applied only there is INERT for routine compaction: measured
    # 2026-08-11, admin threshold 3000 / retention 5 over a 29-event session
    # produced zero compactions. (Findings-log trap 1 in its purest form: the
    # unit tests asserted the override function returns an overridden copy, and
    # nothing checked it reached the code that triggers compaction.)
    #
    # Safe to set on `request_app` because it is already a per-request
    # `model_copy` — never mutate `base_app`, which is shared process-wide
    # (trap 5).
    from adk.compaction_settings import apply_threshold_overrides, compaction_enabled

    compaction_config = apply_threshold_overrides(base_app.events_compaction_config, where="post-invocation")
    if compaction_config is not None and not compaction_enabled():
        # Admin switched compaction off: drop the token trigger. ADK's
        # sliding-window backstop still applies, so context stays bounded.
        compaction_config = compaction_config.model_copy(update={"token_threshold": None})

    request_app = base_app.model_copy(
        update={"root_agent": agent, "name": app_name, "events_compaction_config": compaction_config}
    )

    kwargs: dict[str, Any] = {
        "session_service": session_service or get_session_service(),
        "memory_service": memory_service or get_memory_service(),
        "artifact_service": artifact_service or get_artifact_service(),
        "use_in_memory_services": True,
        "use_thread_id_as_session_id": True,
        # DO NOT REMOVE. ag_ui_adk's SessionManager runs a background sweep
        # (every ``cleanup_interval_seconds``) that DELETES any tracked session
        # idle longer than ``session_timeout_seconds`` — and both defaults are
        # hostile to a persistent chat product: 1200s (20 min) and
        # ``delete_session_on_cleanup=True``. With Vertex as the session store
        # that delete is permanent: it removes the session AND its events from
        # Agent Engine, so a conversation the user had 20 minutes ago is gone.
        #
        # The Firestore ``chat_sessions`` index is metadata-only and is NOT
        # touched by the sweep, so the row survives — the conversation keeps
        # listing in the left panel and resuming it renders a blank chat
        # (GET /sessions/{id}/messages finds no ADK session and, before the
        # companion fix, returned 200 with an empty list). That is the
        # "I select yesterday's conversation and it doesn't load" bug: on test,
        # 19 of 75 real conversations had been deleted this way.
        #
        # Sessions are durable by design here — Vertex applies its own 365-day
        # TTL — so cleanup must never delete. The timeout is raised too so the
        # sweep stops treating a 20-minute-idle chat as garbage at all.
        "delete_session_on_cleanup": False,
        "session_timeout_seconds": 86400,
    }
    if user_id is not None:
        kwargs["user_id"] = user_id
    # NOTE: every session knob above must stay passed EXPLICITLY. `from_app`
    # re-declares them with ag_ui_adk's own hostile defaults
    # (delete_session_on_cleanup=True, session_timeout_seconds=1200,
    # use_thread_id_as_session_id=False) — the exact values that permanently
    # deleted 19 of 75 conversations. `TestSessionSafetySurvivesTheWiring` in
    # tests/unit/test_compaction_reaches_chat_runner.py fails loudly if any of
    # them regress.
    return ADKAgent.from_app(request_app, **kwargs)


async def stream_agui_events(
    agui_agent: ADKAgent,
    run_input: RunAgentInput,
    heartbeat_seconds: float | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the agent and yield each AG-UI event as a plain dict.

    `ADKAgent.run()` yields `ag_ui.core.BaseEvent` pydantic models. We
    serialize via `model_dump(by_alias=True)` so SSE writers can call
    `json.dumps(event)` without bespoke encoders.

    TTFT instrumentation: between each ADK event we drain any pending
    STAGE_PROGRESS Custom events queued on the per-request LatencyTracker
    (see ``observability/timing.py``). ``first_agui_event`` and
    ``first_model_token`` (= first TEXT_MESSAGE_CONTENT) are marked here.
    All instrumentation calls short-circuit when ``AITANA_TTFT_MODE=off``.

    Heartbeats (MODEL-RELIABILITY M1): every ``heartbeat_seconds`` of
    agent silence a no-op ``CUSTOM {name: HEARTBEAT}`` event is emitted
    (and any queued STAGE_PROGRESS flushed) so long thinking phases and
    slow tool calls never present a byte-silent wire. ``heartbeat_seconds``
    overrides the ``AGUI_HEARTBEAT_SECONDS`` env (default 20); ``<= 0``
    disables.
    """
    # Lazy import: avoid pulling observability into module-import path of
    # tests that don't exercise the streaming code.
    from observability.timing import (
        STAGE_FIRST_AGUI_EVENT,
        STAGE_FIRST_MODEL_TOKEN,
        get_current_tracker,
    )

    tracker = get_current_tracker()
    first_agui_event_seen = False
    first_model_token_seen = False
    # NEVER SILENT (CLAUDE.md #8): did this run yield anything the user can see —
    # a token or a tool call? If not, RUN_FINISHED is a silent failure and gets
    # converted to a visible RUN_ERROR at the terminal below.
    produced_output = False
    # G41 (template-agui-terminal-dedup.md): the AG-UI spec mandates at
    # most one terminal event per run (RUN_ERROR XOR RUN_FINISHED). The
    # vendored ag_ui_adk's queue-based execution path can emit both —
    # the background task pushes RUN_ERROR onto the event queue (and we
    # yield it normally), then control returns to the outer try-block
    # which still falls through to emit RUN_FINISHED because the queue-
    # delivered error doesn't propagate as a Python exception. The
    # @ag-ui/client state machine correctly rejects the duplicate with
    # "Cannot send event type 'RUN_FINISHED': The run has already
    # errored". We keep the FIRST terminal event we see and drop any
    # subsequent terminal events with a warning log.
    terminal_event_yielded: str | None = None

    # Buffer any events already queued before the agent yields its first event
    # (e.g. MODEL_RESOLVED, enqueued by set_model in skill_processor before this
    # generator runs). They MUST NOT be yielded before RUN_STARTED: the
    # @ag-ui/client state machine rejects ANY event preceding RUN_STARTED
    # ("First event must be 'RUN_STARTED'"), which fails the whole turn on the
    # client. Flush them immediately AFTER the first real event instead.
    buffered_pre_agent = [ev.model_dump(by_alias=True, exclude_none=True) for ev in tracker.drain_stage_events()]

    hb_interval = _heartbeat_interval(heartbeat_seconds)
    hb_count = 0
    source = agui_agent.run(run_input).__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(source))
            if hb_interval > 0:
                try:
                    event = await asyncio.wait_for(asyncio.shield(pending), timeout=hb_interval)
                except TimeoutError:
                    # Nothing may follow a terminal event — if one has already
                    # gone out, stay silent while draining the tail of `source`.
                    if terminal_event_yielded is not None:
                        continue
                    # Silence tick: keep the wire warm and flush any stage
                    # labels queued mid-silence (e.g. "Thinking…") — the
                    # underlying anext task keeps running via shield().
                    hb_count += 1
                    yield _heartbeat_event(hb_count)
                    drained = False
                    for stage_event in tracker.drain_stage_events():
                        drained = True
                        yield stage_event.model_dump(by_alias=True, exclude_none=True)
                    # M4 silent-phase fallback label: no visible token yet and
                    # nothing fresher queued -> keep the user informed that the
                    # model is working (models with omitted thinking display
                    # emit no REASONING traffic at all). Same label repeats ~
                    # every heartbeat; the frontend overwrites in place.
                    if not first_model_token_seen and not drained:
                        yield {"type": "CUSTOM", "name": "STAGE_PROGRESS", "value": {"label": "Thinking…"}}
                    continue
                except StopAsyncIteration:
                    break
            else:
                try:
                    event = await pending
                except StopAsyncIteration:
                    break
            pending = None
            if not first_agui_event_seen:
                tracker.mark(STAGE_FIRST_AGUI_EVENT)
                first_agui_event_seen = True

            # First TEXT_MESSAGE_CONTENT == first model-emitted token reaching
            # the wire. Earlier signals (RUN_STARTED, TEXT_MESSAGE_START) are
            # handshake events ag_ui_adk emits before the model speaks.
            event_type = getattr(event, "type", None)
            type_value: str | None = None
            if event_type is not None:
                type_value = getattr(event_type, "value", str(event_type))
                # NEVER SILENT (CLAUDE.md #8): track whether this run produced
                # ANY user-visible outcome — a token, a tool call, or a rendered
                # A2UI surface (a workbench render with no chat text is still a
                # real result, and must NOT be mistaken for an empty run).
                # Reasoning does NOT count: a turn that only thinks and stops is
                # a failed turn, not an answer.
                if type_value in ("TEXT_MESSAGE_CONTENT", "TOOL_CALL_START"):
                    produced_output = True
                elif type_value == "CUSTOM" and getattr(event, "name", None) == "A2UI_SURFACE":
                    produced_output = True
                if not first_model_token_seen:
                    if type_value == "TEXT_MESSAGE_CONTENT":
                        tracker.mark(STAGE_FIRST_MODEL_TOKEN)
                        first_model_token_seen = True
                    elif type_value == "TOOL_CALL_START":
                        tracker.increment_tool_invocations()

            # Terminal-event ordering (G41 + the 2026-07-15 ordering crash). A
            # terminal event (RUN_ERROR / RUN_FINISHED) MUST be the LAST event on
            # the wire: @ag-ui/client rejects BOTH a duplicate terminal AND any
            # event that FOLLOWS a terminal ("Cannot send event type 'CUSTOM':
            # the run has already errored"). Once one has gone out, DROP every
            # subsequent event (any type) — keep pulling the source only so a
            # duplicate terminal is logged for upstream-bug frequency.
            if terminal_event_yielded is not None:
                if type_value in _TERMINAL_EVENT_TYPES:
                    logger.warning(
                        "agui_terminal_dedup: dropped duplicate terminal event "
                        "(first=%s, dropped=%s, thread_id=%s); see "
                        "docs/design/template/template-agui-terminal-dedup.md",
                        terminal_event_yielded,
                        type_value,
                        getattr(run_input, "thread_id", "<unknown>"),
                    )
                continue

            if type_value in _TERMINAL_EVENT_TYPES:
                # NEVER SILENT (CLAUDE.md #8): a RUN_FINISHED carrying no text
                # and no tool call is a SILENT FAILURE — the turn dies and the
                # UI just stops, with nothing to see and nothing to retry.
                # Measured on deployed dev 2026-07-17: a lite model intermittently
                # ends a turn having emitted only reasoning (ADK logs "The last
                # event is partial, which is not expected"), and the user is left
                # staring at a dead session. Convert it to a visible, retryable
                # terminal error. RUN_STARTED has already gone out, so RUN_ERROR
                # is a legal terminal for the @ag-ui/client state machine.
                if type_value == "RUN_FINISHED" and not produced_output:
                    logger.warning(
                        "agui_empty_run: RUN_FINISHED with no text and no tool call "
                        "(thread_id=%s) — surfacing as RUN_ERROR so the turn is not "
                        "a silent no-op",
                        getattr(run_input, "thread_id", "<unknown>"),
                    )
                    terminal_event_yielded = "RUN_ERROR"
                    yield {
                        "type": "RUN_ERROR",
                        # `code` lets the frontend recognise this specific transient
                        # failure and auto-retry ONCE before showing the user an
                        # error (a fresh run is protocol-correct; a server-side
                        # retry here would emit a second RUN_STARTED mid-stream,
                        # which @ag-ui/client rejects).
                        "code": "EMPTY_RUN",
                        "message": (
                            "The assistant ended the turn without a reply — no answer and no "
                            "tool call. This is usually a transient model error. Please retry."
                        ),
                    }
                    continue
                # First terminal — yield it, then suppress everything after
                # (buffered flush + stage drain + heartbeat are all gated below).
                terminal_event_yielded = type_value
                yield event.model_dump(by_alias=True, exclude_none=True)
                continue

            yield event.model_dump(by_alias=True, exclude_none=True)

            # Flush the pre-agent events (buffered above) now that the first real
            # event — RUN_STARTED — is on the wire. One-shot; cleared after.
            if buffered_pre_agent:
                for ev in buffered_pre_agent:
                    yield ev
                buffered_pre_agent = []

            # After each ADK event, flush any STAGE_PROGRESS that fired during
            # callback execution (e.g. before_model_callback marks
            # ``before_model_done`` with label "Thinking…"). Done in-loop so
            # the order on the wire matches the order marks fired.
            for stage_event in tracker.drain_stage_events():
                yield stage_event.model_dump(by_alias=True, exclude_none=True)
    finally:
        # Consumer disconnected (or agent errored): don't leak the in-flight
        # anext task — cancelling it also propagates into the underlying
        # ag_ui_adk generator so the ADK run can clean up.
        if pending is not None and not pending.done():
            pending.cancel()
