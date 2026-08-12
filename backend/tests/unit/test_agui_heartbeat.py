"""MODEL-RELIABILITY M1 — SSE heartbeats in ``stream_agui_events``.

A silent phase (model thinking, long tool call, slow first token) used
to produce ZERO bytes on the wire: intermediaries with idle timeouts
(undici bodyTimeout, LB idle) could kill a healthy stream, and the
frontend's mid-stream watchdog had nothing to reset on. The fix: while
awaiting the next ADK event, ``stream_agui_events`` emits a no-op
AG-UI ``CUSTOM {name: HEARTBEAT}`` event every ``heartbeat_seconds`` of
silence.

Why a CUSTOM event and not an SSE comment line: the @ag-ui/client
parser collects only ``data:`` lines per block (comment-only blocks are
silently skipped — verified against the bundled parser), so a comment
would keep intermediaries alive but be INVISIBLE to the subscriber-side
watchdog. One CUSTOM event serves both. Heartbeats ride the same
pre-/inter-event CUSTOM path STAGE_PROGRESS already uses, so ordering
relative to RUN_STARTED is already proven on the wire.

Config surface: ``heartbeat_seconds`` param > ``AGUI_HEARTBEAT_SECONDS``
env > 20s default; ``<= 0`` disables (used by tests that assert exact
event sequences).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from adk.agui import HEARTBEAT_EVENT_NAME, stream_agui_events


class _FakeEventType:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeEvent:
    def __init__(self, type_value: str, **extra: Any) -> None:
        self.type = _FakeEventType(type_value)
        self._extra = extra

    def model_dump(self, *, by_alias: bool = True, exclude_none: bool = True) -> dict:
        return {"type": self.type.value, **self._extra}


class _SlowAguiAgent:
    """Yields scripted events with a sleep before each one."""

    def __init__(self, events: list[_FakeEvent], delay_before_each: float) -> None:
        self._events = events
        self._delay = delay_before_each

    async def run(self, _run_input: Any):
        for event in self._events:
            await asyncio.sleep(self._delay)
            yield event


def _make_run_input(thread_id: str = "thread-hb") -> MagicMock:
    ri = MagicMock()
    ri.thread_id = thread_id
    return ri


@pytest.fixture
def noop_tracker():
    tracker = MagicMock()
    tracker.drain_stage_events.return_value = []
    with patch("observability.timing.get_current_tracker", return_value=tracker):
        yield tracker


def _is_heartbeat(event: dict) -> bool:
    return event.get("type") == "CUSTOM" and event.get("name") == HEARTBEAT_EVENT_NAME


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_heartbeat_emitted_during_silent_gap(noop_tracker):
    """A gap longer than the interval produces at least one heartbeat
    BETWEEN the surrounding real events (position matters: the wire must
    carry traffic during the silence, not after it)."""
    agent = _SlowAguiAgent(
        # TOOL_CALL_START keeps this a REAL (non-empty) run so the never-silent
        # guard doesn't rewrite the terminal — this test is about heartbeats.
        [_FakeEvent("RUN_STARTED"), _FakeEvent("TOOL_CALL_START"), _FakeEvent("RUN_FINISHED")],
        delay_before_each=0.12,
    )

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=0.03))

    types = [e["type"] for e in events]
    assert types[0] != "RUN_STARTED" or _is_heartbeat(events[0]) is False  # sanity: list non-empty
    started = next(i for i, e in enumerate(events) if e["type"] == "RUN_STARTED")
    finished = next(i for i, e in enumerate(events) if e["type"] == "RUN_FINISHED")
    between = events[started + 1 : finished]
    assert any(_is_heartbeat(e) for e in between), f"no heartbeat between events: {types}"
    # Real events still arrive intact and in order.
    assert started < finished


@pytest.mark.asyncio
async def test_no_heartbeat_on_fast_stream(noop_tracker):
    """Events arriving faster than the interval produce zero heartbeats —
    heartbeats are for silence, not stream bloat."""
    agent = _SlowAguiAgent(
        [_FakeEvent("RUN_STARTED"), _FakeEvent("TEXT_MESSAGE_CONTENT", delta="x"), _FakeEvent("RUN_FINISHED")],
        delay_before_each=0.0,
    )

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=5.0))

    assert not any(_is_heartbeat(e) for e in events)
    assert [e["type"] for e in events] == ["RUN_STARTED", "TEXT_MESSAGE_CONTENT", "RUN_FINISHED"]


@pytest.mark.asyncio
async def test_heartbeat_disabled_with_nonpositive_interval(noop_tracker):
    """``heartbeat_seconds=0`` disables heartbeats entirely (exact-sequence
    consumers like the terminal-dedup tests rely on this)."""
    agent = _SlowAguiAgent(
        [_FakeEvent("RUN_STARTED"), _FakeEvent("RUN_FINISHED")],
        delay_before_each=0.08,
    )

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=0))

    assert not any(_is_heartbeat(e) for e in events)


@pytest.mark.asyncio
async def test_heartbeat_shape_is_agui_custom_event(noop_tracker):
    """Heartbeats must be spec-legal AG-UI CUSTOM events the client's
    onCustomEvent branch can ignore by name — a malformed data line would
    error the whole @ag-ui/client stream."""
    agent = _SlowAguiAgent([_FakeEvent("RUN_FINISHED")], delay_before_each=0.1)

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=0.03))

    heartbeats = [e for e in events if _is_heartbeat(e)]
    assert heartbeats, "expected at least one heartbeat"
    hb = heartbeats[0]
    assert hb["type"] == "CUSTOM"
    assert hb["name"] == HEARTBEAT_EVENT_NAME
    # Monotonic counter aids debugging dropped-connection reports.
    assert hb["value"]["n"] >= 1


@pytest.mark.asyncio
async def test_pre_agent_events_flush_after_run_started_not_before(noop_tracker):
    """Events enqueued BEFORE the agent yields (MODEL_RESOLVED from set_model,
    which runs in skill_processor before this generator) must NOT precede
    RUN_STARTED. The @ag-ui/client state machine rejects ANY event before
    RUN_STARTED ("First event must be 'RUN_STARTED'"), failing the whole turn on
    the client (2026-07-15 live break). They must be buffered and flushed
    immediately AFTER RUN_STARTED. NOTE: contra an earlier comment, pre-RUN_STARTED
    CUSTOM ordering is NOT "proven on the wire" — the client hard-rejects it."""
    model_event = _FakeEvent("CUSTOM", name="MODEL_RESOLVED", value={"model": "claude-opus-4-8"})
    # First drain (the pre-agent drain) returns MODEL_RESOLVED; the rest empty.
    drains = iter([[model_event]])
    noop_tracker.drain_stage_events.side_effect = lambda: next(drains, [])

    agent = _SlowAguiAgent(
        [
            _FakeEvent("RUN_STARTED"),
            _FakeEvent("TEXT_MESSAGE_CONTENT", delta="hi"),
            _FakeEvent("RUN_FINISHED"),
        ],
        delay_before_each=0.0,
    )

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=0))

    order = [e.get("name") if e["type"] == "CUSTOM" else e["type"] for e in events]
    assert order[0] == "RUN_STARTED", f"first frame must be RUN_STARTED, got {order}"
    assert "MODEL_RESOLVED" in order, f"MODEL_RESOLVED must not be dropped: {order}"
    assert order.index("MODEL_RESOLVED") == 1, f"MODEL_RESOLVED must immediately follow RUN_STARTED: {order}"


@pytest.mark.asyncio
async def test_stage_events_flush_during_silence(noop_tracker):
    """STAGE_PROGRESS queued during a silent phase (e.g. before_model
    callback fires 'Thinking…') must reach the wire on the next heartbeat
    tick, not wait for the next ADK event — that's the whole activity-
    transparency point."""
    stage_event = _FakeEvent("CUSTOM", name="STAGE_PROGRESS", value={"label": "Thinking…"})
    # First drain calls return nothing, then the stage event appears while
    # the agent is still silent.
    drains = iter([[], [], [stage_event]])
    noop_tracker.drain_stage_events.side_effect = lambda: next(drains, [])

    # TOOL_CALL_START = real output, so the never-silent guard leaves the
    # terminal alone; this test is about mid-silence stage flushing.
    agent = _SlowAguiAgent([_FakeEvent("TOOL_CALL_START"), _FakeEvent("RUN_FINISHED")], delay_before_each=0.15)

    events = await _collect(stream_agui_events(agent, _make_run_input(), heartbeat_seconds=0.03))

    finished = next(i for i, e in enumerate(events) if e["type"] == "RUN_FINISHED")
    before_finish = events[:finished]
    assert any(e.get("name") == "STAGE_PROGRESS" for e in before_finish), (
        "stage event queued mid-silence should flush before the run ends"
    )
