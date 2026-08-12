"""ADK-contract guard C4: an A2UI surface reaches the wire ONLY when a per-request
LatencyTracker is bound to the async context.

`emit_a2ui_surface` enqueues onto a `LatencyTracker` reached via a ContextVar
(`get_current_tracker()`). An SSE endpoint must `set_current_tracker(...)` before
running `stream_agui_events`; without the bind, `get_current_tracker()` returns
the module NULL tracker and EVERY emit silently no-ops — the recurring "A2UI won't
render in the Workspace" trap (the agent narrates "I updated the Workspace" but
the tab stays empty; artifactCount stays 0).

This locks both branches of the invariant using the REAL production functions
(`get_current_tracker` / `set_current_tracker` / `emit_a2ui_surface` /
`drain_stage_events`): bound → the A2UI_SURFACE event is on the queue; unbound →
it vanishes into the NULL tracker.

Part of `make adk-conformance`. See docs/design/v6.17.0/adk-contract-checklist.md.
"""

from __future__ import annotations

import pytest

from observability.timing import (
    _NULL_TRACKER,  # the sentinel returned when nothing is bound
    A2UI_SURFACE_EVENT_NAME,
    LatencyTracker,
    get_current_tracker,
    reset_current_tracker,
    set_current_tracker,
)

pytestmark = pytest.mark.adk_contract

_MESSAGES = [{"createSurface": {"surfaceId": "main", "catalogId": "c"}}]


def _emit_like_production() -> None:
    """Exactly what an emit site does: reach the tracker via the ContextVar and emit."""
    get_current_tracker().emit_a2ui_surface(
        surface_id="main",
        messages=_MESSAGES,
        source_id="src-1",
        artifact={"kind": "demo", "title": "Demo", "description": "d"},
    )


def test_unbound_tracker_silently_drops_the_surface():
    # No set_current_tracker in this context -> the NULL tracker. This IS the
    # recurring trap: the emit runs, returns cleanly, and nothing surfaces.
    assert get_current_tracker() is _NULL_TRACKER, "expected an unbound context (NULL tracker)"
    _emit_like_production()
    assert _NULL_TRACKER.drain_stage_events() == [], "the NULL tracker must never accumulate/surface events"


def test_bound_tracker_puts_the_surface_on_the_wire():
    # Mirror stream_agui_events: bind a per-request tracker, then emit.
    tracker = LatencyTracker(skill_id="s", session_id="sess", user_id="u")
    token = set_current_tracker(tracker)
    try:
        assert get_current_tracker() is tracker
        _emit_like_production()
    finally:
        reset_current_tracker(token)

    drained = tracker.drain_stage_events()
    names = [e.name for e in drained]
    assert A2UI_SURFACE_EVENT_NAME in names, "a bound emit must enqueue an A2UI_SURFACE event for the SSE drain"
    surface = next(e for e in drained if e.name == A2UI_SURFACE_EVENT_NAME)
    assert surface.value["surfaceId"] == "main"
    assert surface.value["messages"] == _MESSAGES

    # And after the reset the context is unbound again — a later unbound emit no-ops.
    assert get_current_tracker() is _NULL_TRACKER
