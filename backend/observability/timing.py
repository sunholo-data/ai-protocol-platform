"""TTFT instrumentation — `LatencyTracker` with kill switch.

Captures per-stage timings for one chat turn and exposes them three ways:
  1. **OTel span attributes** (``aitana.ttft.*``) — survives in Cloud Trace.
  2. **Structured log line** (``event="ttft"``) — one line per request, lands
     in Cloud Logging as ``jsonPayload``; trivial to query in BigQuery.
  3. **AG-UI ``STAGE_PROGRESS`` Custom events** — user-facing labels
     ("Reading documents…", "Thinking…") interleaved into the SSE stream so
     the chat UI can decouple perceived TTFT from real TTFT.

The whole module is gated by ``AITANA_TTFT_MODE``:
  * ``full``   — all three outputs (default)
  * ``log``    — structured log only; no span attributes, no STAGE_PROGRESS
  * ``off``    — no-op; ``mark()`` returns immediately after a single bool check

The off mode exists specifically to A/B-test whether the instrumentation
itself adds latency. Empirically verifying overhead is the M5 deliverable
of the TTFT-INSTR sprint.

The tracker is reached from async callbacks via a ``contextvars.ContextVar``
rather than being passed through ADK session state — ADK state must be
JSON-serializable, and a Python object reference is not. ContextVars are
propagated automatically across ``await`` boundaries within the same task
tree, which is exactly the lifetime of one chat turn.
"""

from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar
from typing import Any

from ag_ui.core import CustomEvent, EventType
from opentelemetry import trace

logger = logging.getLogger(__name__)

# --- Mode resolution (read once at import) ---

_RAW_MODE = os.environ.get("AITANA_TTFT_MODE", "full").strip().lower()
if _RAW_MODE not in {"full", "log", "off"}:
    logger.warning("AITANA_TTFT_MODE=%r is not one of {full,log,off}; defaulting to 'full'", _RAW_MODE)
    _RAW_MODE = "full"

TTFT_MODE: str = _RAW_MODE
"""Resolved instrumentation mode for this process. One of: full, log, off."""

_ENABLED: bool = TTFT_MODE != "off"
"""Master kill switch. When False, every public method is a no-op."""

_FULL: bool = TTFT_MODE == "full"
"""True only in full mode — controls span attrs and STAGE_PROGRESS emission."""

# --- Stage names — single source of truth ---

# Internal mark names. These are used as both the suffix on the OTel span
# attribute (``aitana.ttft.<name>_ms``) and as the key in the structured log
# line. Adding a new stage means adding a constant here and one ``mark()``
# call at the right point in the request lifecycle.
STAGE_REQUEST_RECEIVED = "request_received"
STAGE_SESSION_INDEX_DONE = "session_index_done"
# Added 2026-04-28 (TTFT-OPTIMIZATION M1) to attribute the unexplained
# 5.7s gap between session_index_done and before_agent_done that the
# baseline run revealed. agent_factory_done captures the cost of
# create_agent_with_thinking(); runner_setup_done captures the gap from
# there to ADK actually invoking our before_agent_callback (ag_ui_adk
# wrap + ADK runner enter + plugin setup).
#
# We deliberately do NOT add a memory_load_done mark — PreloadMemoryTool
# runs inside ADK's BaseLlmFlow._preprocess_async, which executes
# between our before_agent_callback exit and our before_model_callback
# entry. The existing before_agent_done → before_model_done gap
# already measures that cost; an extra mark would be redundant.
# Verified by reading google.adk.flows.llm_flows.base_llm_flow source.
STAGE_AGENT_FACTORY_DONE = "agent_factory_done"
STAGE_RUNNER_SETUP_DONE = "runner_setup_done"
STAGE_BEFORE_AGENT_DONE = "before_agent_done"
STAGE_BEFORE_MODEL_DONE = "before_model_done"
STAGE_FIRST_MODEL_TOKEN = "first_model_token"
STAGE_FIRST_AGUI_EVENT = "first_agui_event"
STAGE_FIRST_SSE_BYTE = "first_sse_byte"
STAGE_TOOL_CALL_STARTED = "tool_call_started"

# AG-UI Custom event name. Frontend filters on this string.
STAGE_PROGRESS_EVENT_NAME = "STAGE_PROGRESS"
LATENCY_REPORT_EVENT_NAME = "LATENCY_REPORT"
# Skill→skill delegation marker (v6.7.0 SKILL-DELEGATION). Rendered by the
# frontend as a persistent transcript chip ("→ Delegated to <target>").
DELEGATION_EVENT_NAME = "AGENT_DELEGATION"
# Resolved model for THIS turn (v6.8.0). Emitted once per turn after the agent
# factory / router picks the model, so the Activity header shows the model that
# actually ran — not the front door's static config — when a router tier or a
# delegate on a different model answers. Carries {model, routing}.
MODEL_RESOLVED_EVENT_NAME = "MODEL_RESOLVED"
# Server-rendered A2UI surface push (v6.7.0 TOOL-RESULTS-A2UI, Model B). Carries
# {surfaceId, messages, sourceId}; the frontend routes messages straight to the
# SurfaceRegistry so A2UISurfaceMount draws them. Out of the model's context.
A2UI_SURFACE_EVENT_NAME = "A2UI_SURFACE"


# --- The tracker ---


class LatencyTracker:
    """Per-request TTFT recorder.

    Lifetime is one chat turn. Instantiate at the entry of the SSE handler;
    the constructor takes ``t_request_received`` so the request log line's
    timestamps are anchored at the moment the route handler started, not the
    moment the tracker was created.

    All methods are no-ops when ``AITANA_TTFT_MODE=off``. ``mark()`` is the
    hottest method on the request path — keep its fast path branchless.
    """

    __slots__ = (
        "_emitted",
        "_marks",
        "_model_used",
        "_pending_stage_events",
        "_routing_choice",
        "_session_id",
        "_skill_id",
        "_t0",
        "_tools_invoked",
        "_user_id",
    )

    def __init__(
        self,
        *,
        skill_id: str = "",
        session_id: str = "",
        user_id: str = "",
    ) -> None:
        self._t0: float = time.perf_counter()
        self._marks: dict[str, float] = {}
        # Queue of CustomEvent objects waiting to be interleaved by
        # stream_agui_events. Plain list — single-task per request, so no
        # locking needed.
        self._pending_stage_events: list[CustomEvent] = []
        self._skill_id = skill_id
        self._session_id = session_id
        self._user_id = user_id
        self._model_used: str = ""
        self._routing_choice: str = ""
        self._tools_invoked: int = 0
        self._emitted: bool = False

    # --- Public API ---

    def mark(self, name: str, user_label: str | None = None) -> None:
        """Record a stage timing.

        Args:
            name: Stage name (use one of the ``STAGE_*`` constants in this module).
            user_label: When set in ``full`` mode, also emits a STAGE_PROGRESS
                AG-UI Custom event with this label so the UI can show it as
                progress text inside the skeleton bubble. Set to None for
                stages that should be invisible to the user (the default).

        Fail-open: any exception is swallowed at WARNING level. Instrumentation
        must never break the chat path.
        """
        if not _ENABLED:
            return
        try:
            self._do_mark(name, user_label)
        except Exception as exc:
            logger.warning("LatencyTracker.mark(%r) failed (suppressed): %s", name, exc)

    def _do_mark(self, name: str, user_label: str | None) -> None:
        # Idempotent on re-mark: keep the first observation. ``first_*`` marks
        # in particular must not be overwritten by later events.
        if name in self._marks:
            return
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        self._marks[name] = elapsed_ms

        if _FULL:
            span = trace.get_current_span()
            try:
                span.set_attribute(f"aitana.ttft.{name}_ms", elapsed_ms)
            except Exception as exc:
                logger.warning("span attribute set failed (suppressed): %s", exc)

            if user_label is not None:
                try:
                    self._pending_stage_events.append(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name=STAGE_PROGRESS_EVENT_NAME,
                            value={
                                "stage": name,
                                "label": user_label,
                                "elapsed_ms": round(elapsed_ms, 2),
                            },
                        )
                    )
                except Exception as exc:
                    logger.warning("STAGE_PROGRESS enqueue failed (suppressed): %s", exc)

    def set_model(self, model_used: str, routing_choice: str = "") -> None:
        """Record the resolved model + routing decision for this turn.

        Called once after `_HeuristicRouter` (or single-agent factory) picks
        the agent. Surfaces in the structured log and the LATENCY_REPORT
        event.
        """
        if not _ENABLED:
            return
        self._model_used = model_used
        self._routing_choice = routing_choice
        if _FULL:
            try:
                span = trace.get_current_span()
                if model_used:
                    span.set_attribute("aitana.model.id", model_used)
                if routing_choice:
                    span.set_attribute("aitana.routing.choice", routing_choice)
            except Exception as exc:
                logger.warning("span attribute set failed (suppressed): %s", exc)
            # Surface the resolved model to the UI (Activity header). Rides the
            # same per-request drain as STAGE_PROGRESS/AGENT_DELEGATION. Only when
            # we actually know the model — an empty string would blank the header.
            if model_used:
                try:
                    self._pending_stage_events.append(
                        CustomEvent(
                            type=EventType.CUSTOM,
                            name=MODEL_RESOLVED_EVENT_NAME,
                            value={"model": model_used, "routing": routing_choice},
                        )
                    )
                except Exception as exc:
                    logger.warning("MODEL_RESOLVED enqueue failed (suppressed): %s", exc)

    def increment_tool_invocations(self) -> None:
        """Increment the tools-invoked counter for this turn."""
        if not _ENABLED:
            return
        self._tools_invoked += 1

    def mark_delegation(self, *, parent: str, target: str, target_display: str, mode: str, avatar: str = "") -> None:
        """Record a skill→skill delegation (auto handoff or suggest proposal).

        Sets OTEL span attributes and, in ``full`` mode, enqueues an
        ``AGENT_DELEGATION`` Custom event the UI renders as a persistent
        transcript marker. ``avatar`` is the delegate skill's avatar so the
        frontend can attribute each message to the agent that produced it (the
        bot bubble mark changes as the conversation hands off). Fail-open like
        ``mark()`` — instrumentation must never break the chat path.
        """
        if not _ENABLED:
            return
        try:
            if _FULL:
                span = trace.get_current_span()
                try:
                    span.set_attribute("aitana.delegation.parent", parent)
                    span.set_attribute("aitana.delegation.target", target)
                    span.set_attribute("aitana.delegation.mode", mode)
                except Exception as exc:
                    logger.warning("delegation span attr set failed (suppressed): %s", exc)
                self._pending_stage_events.append(
                    CustomEvent(
                        type=EventType.CUSTOM,
                        name=DELEGATION_EVENT_NAME,
                        value={
                            "parent": parent,
                            "target": target,
                            "target_display": target_display,
                            "mode": mode,
                            "avatar": avatar,
                        },
                    )
                )
        except Exception as exc:
            logger.warning("mark_delegation failed (suppressed): %s", exc)

    def emit_a2ui_surface(
        self,
        surface_id: str,
        messages: list[dict],
        source_id: str,
        artifact: dict | None = None,
    ) -> None:
        """Enqueue an ``A2UI_SURFACE`` CUSTOM event carrying server-rendered
        A2UI v0.9 messages for ``surface_id`` (tool-results-as-a2ui / 7.3).

        Drained by ``stream_agui_events`` onto the SSE stream — the A2UI never
        enters the model's context (Model B). ``source_id`` is a per-emission
        idempotency key the frontend SurfaceRegistry dedupes on (must be unique
        per distinct surface update, stable across a genuine SSE re-delivery).
        ``artifact`` (7.5) is optional metadata (``{kind,title,description}``)
        that drives the workbench tab title + the Workspace index.

        NOTE: unlike ``mark()`` / ``mark_delegation()``, this is intentionally
        NOT gated by ``AITANA_TTFT_MODE`` — workbench rendering is a product
        feature, not instrumentation, and must survive ``log``/``off`` mode. It
        rides the same pending-events queue (which already carries
        ``AGENT_DELEGATION``) purely to reuse the existing drain. Fail-open.
        """
        if not messages:
            return
        try:
            value: dict = {
                "surfaceId": surface_id,
                "messages": messages,
                "sourceId": source_id,
            }
            if artifact:
                value["artifact"] = artifact
            self._pending_stage_events.append(
                CustomEvent(
                    type=EventType.CUSTOM,
                    name=A2UI_SURFACE_EVENT_NAME,
                    value=value,
                )
            )
        except Exception as exc:
            logger.warning("emit_a2ui_surface enqueue failed (suppressed): %s", exc)

    def emit_reliability_event(self, name: str, value: dict) -> None:
        """Enqueue a MODEL_RETRY / MODEL_FALLBACK CUSTOM event (M3).

        Like ``emit_a2ui_surface``, intentionally NOT gated by
        ``AITANA_TTFT_MODE`` — degradation signaling is a product feature
        (axiom #5: 100% of degraded states user-visible), not
        instrumentation. Rides the same pending queue ``stream_agui_events``
        drains, including mid-silence on heartbeat ticks (M1), so a retry
        notice reaches the user WHILE the backoff sleep is happening, not
        after. Fail-open; payloads carry provider/model/code only — never
        prompt content.
        """
        try:
            self._pending_stage_events.append(CustomEvent(type=EventType.CUSTOM, name=name, value=value))
        except Exception as exc:
            logger.warning("emit_reliability_event enqueue failed (suppressed): %s", exc)

    @property
    def tools_invoked_count(self) -> int:
        """Read-only count of tool invocations seen so far this turn."""
        return self._tools_invoked

    @property
    def session_id(self) -> str:
        """The session this request serves ('' on the NULL tracker)."""
        return self._session_id

    @property
    def user_id(self) -> str:
        """The user this request serves ('' on the NULL tracker)."""
        return self._user_id

    def drain_stage_events(self) -> list[CustomEvent]:
        """Pop and return any STAGE_PROGRESS events queued since the last drain.

        Called by ``stream_agui_events`` between each ADK event yield so
        progress labels are interleaved into the SSE stream in the order
        their underlying marks fired.

        Returns an empty list when not in full mode (no events are ever
        enqueued).
        """
        if not self._pending_stage_events:
            return []
        events = self._pending_stage_events
        self._pending_stage_events = []
        return events

    def report_payload(self) -> dict[str, Any]:
        """Snapshot of all marks + metadata. Used by both ``emit_log()``
        and the optional LATENCY_REPORT AG-UI event."""
        return {
            "skill_id": self._skill_id,
            "session_id": self._session_id,
            "user_id": self._user_id,
            "model_used": self._model_used,
            "routing_choice": self._routing_choice,
            "tools_invoked_count": self._tools_invoked,
            "ttft_mode": TTFT_MODE,
            **{f"{name}_ms": round(ms, 2) for name, ms in self._marks.items()},
        }

    def build_latency_report_event(self) -> CustomEvent | None:
        """Build a final LATENCY_REPORT Custom event for ``aiplatform skill probe``.

        The CLI consumes this event to print the per-stage breakdown without
        having to scrape backend logs. Emission is opt-in (callers gate on
        ``?probe=1``); this method just builds the event.
        """
        if not _ENABLED:
            return None
        try:
            return CustomEvent(
                type=EventType.CUSTOM,
                name=LATENCY_REPORT_EVENT_NAME,
                value=self.report_payload(),
            )
        except Exception as exc:
            logger.warning("LATENCY_REPORT build failed (suppressed): %s", exc)
            return None

    def emit_log(self) -> None:
        """Write the single structured ``event="ttft"`` log line.

        Idempotent: safe to call from a ``finally:`` even if an upstream
        exception already triggered emission.
        """
        if not _ENABLED or self._emitted:
            return
        self._emitted = True
        try:
            payload = self.report_payload()
            payload["event"] = "ttft"
            # Total response time = current elapsed since t0.
            payload["total_response_ms"] = round((time.perf_counter() - self._t0) * 1000.0, 2)
            # ``extra`` becomes ``jsonPayload`` in Cloud Logging — the
            # message string is just for human readability in the local
            # dev console.
            logger.info(
                "ttft skill=%s ttft_ms=%s total_ms=%s mode=%s",
                self._skill_id,
                payload.get(f"{STAGE_FIRST_MODEL_TOKEN}_ms", "n/a"),
                payload["total_response_ms"],
                TTFT_MODE,
                extra={"json_fields": payload},
            )
        except Exception as exc:
            logger.warning("LatencyTracker.emit_log() failed (suppressed): %s", exc)


# --- Contextvar accessor (so async callbacks don't have to be re-plumbed) ---


class _NullLatencyTracker(LatencyTracker):
    """Returned by ``get_current_tracker()`` when no per-request tracker is
    bound. Every public method is a no-op so callbacks can call mark()
    unconditionally without checking for None."""

    def mark(self, name: str, user_label: str | None = None) -> None:
        return

    def set_model(self, model_used: str, routing_choice: str = "") -> None:
        return

    def increment_tool_invocations(self) -> None:
        return

    def mark_delegation(self, *, parent: str, target: str, target_display: str, mode: str, avatar: str = "") -> None:
        return

    def emit_a2ui_surface(
        self, surface_id: str, messages: list[dict], source_id: str, artifact: dict | None = None
    ) -> None:
        return

    def drain_stage_events(self):
        return []

    def build_latency_report_event(self):
        return None

    def emit_log(self) -> None:
        return


_NULL_TRACKER = _NullLatencyTracker(skill_id="", session_id="", user_id="")

_current_tracker: ContextVar[LatencyTracker | None] = ContextVar("aitana_ttft_tracker", default=None)


def set_current_tracker(tracker: LatencyTracker) -> Any:
    """Bind ``tracker`` to the current async context. Returns a token that
    callers can pass to ``reset_current_tracker`` to restore the prior
    binding (mirrors ``ContextVar.set``)."""
    return _current_tracker.set(tracker)


def reset_current_tracker(token: Any) -> None:
    """Restore the prior tracker binding."""
    try:
        _current_tracker.reset(token)
    except Exception:
        # Token from a different context — ignore. Tests reuse the same
        # event loop and may call this out of order.
        pass


def get_current_tracker() -> LatencyTracker:
    """Return the tracker for the current request, or a no-op tracker if
    none is set. Always safe to call ``mark()`` on the result."""
    tracker = _current_tracker.get()
    if tracker is None:
        return _NULL_TRACKER
    return tracker
