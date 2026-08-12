"""Unit tests for the TTFT instrumentation tracker.

Covers the three-mode kill switch (full/log/off), span attribute emission,
STAGE_PROGRESS Custom event enqueueing, fail-open behaviour, and the
contextvar accessor.

Important: ``observability.timing`` reads ``AITANA_TTFT_MODE`` once at
import time. Tests flip modes by monkeypatching the module-level
``_ENABLED`` / ``_FULL`` / ``TTFT_MODE`` constants in place rather than
reloading the module — reloading would leave any consumer that already
imported ``LatencyTracker`` (e.g. ``fast_api_app``) holding a stale
class. Production never re-imports the module, so the in-place patch
also matches reality more closely than a reload would.
"""

from __future__ import annotations

import logging

import pytest

import observability.timing as timing


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(timing, "TTFT_MODE", mode, raising=True)
    monkeypatch.setattr(timing, "_ENABLED", mode != "off", raising=True)
    monkeypatch.setattr(timing, "_FULL", mode == "full", raising=True)


def test_mode_full_records_marks_emits_span_attrs_and_stage_events(monkeypatch):
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_AGENT_FACTORY_DONE)
    tracker.mark(timing.STAGE_RUNNER_SETUP_DONE)
    tracker.mark(timing.STAGE_BEFORE_AGENT_DONE, user_label="Reading 1 document…")
    tracker.mark(timing.STAGE_BEFORE_MODEL_DONE, user_label="Thinking…")

    payload = tracker.report_payload()
    assert "request_received_ms" in payload
    assert "agent_factory_done_ms" in payload
    assert "runner_setup_done_ms" in payload
    assert "before_agent_done_ms" in payload
    assert "before_model_done_ms" in payload
    assert payload["ttft_mode"] == "full"

    events = tracker.drain_stage_events()
    assert len(events) == 2
    labels = [e.value["label"] for e in events]
    assert labels == ["Reading 1 document…", "Thinking…"]
    # Once drained, queue is empty.
    assert tracker.drain_stage_events() == []


def test_set_model_emits_model_resolved_in_full_mode(monkeypatch):
    """The resolved model is surfaced to the UI (Activity header) as a
    MODEL_RESOLVED custom event, riding the same drain as STAGE_PROGRESS (8.2)."""
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.set_model("claude-opus-4-8", "single")

    events = tracker.drain_stage_events()
    assert len(events) == 1
    assert events[0].name == timing.MODEL_RESOLVED_EVENT_NAME
    assert events[0].value == {"model": "claude-opus-4-8", "routing": "single"}


def test_set_model_no_event_without_a_model(monkeypatch):
    """An empty model string must not enqueue an event that would blank the
    header (defensive — the extraction can come back empty)."""
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.set_model("", "single")
    assert tracker.drain_stage_events() == []


def test_set_model_no_event_in_off_mode(monkeypatch):
    _set_mode(monkeypatch, "off")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.set_model("claude-opus-4-8", "single")
    assert tracker.drain_stage_events() == []


def test_mode_off_is_truly_noop(monkeypatch, caplog):
    """The kill switch path is the M5 A/B baseline. Off mode MUST:
    - not record marks
    - not emit STAGE_PROGRESS events (drain returns [])
    - not log the structured ttft line
    """
    _set_mode(monkeypatch, "off")

    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_BEFORE_AGENT_DONE, user_label="Reading 1 document…")
    tracker.set_model("gemini-2.5-flash", "fast")
    tracker.increment_tool_invocations()

    # Marks dict stays empty in off mode.
    payload = tracker.report_payload()
    assert "request_received_ms" not in payload
    assert payload["model_used"] == ""  # set_model short-circuited
    assert payload["tools_invoked_count"] == 0  # increment short-circuited

    # No STAGE_PROGRESS events ever enqueued.
    assert tracker.drain_stage_events() == []

    # No latency report event built.
    assert tracker.build_latency_report_event() is None

    # emit_log writes nothing.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="observability.timing"):
        tracker.emit_log()
    assert not [r for r in caplog.records if "ttft" in r.getMessage()]


def test_mode_log_emits_log_but_no_stage_events(monkeypatch, caplog):
    _set_mode(monkeypatch, "log")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_BEFORE_MODEL_DONE, user_label="Thinking…")

    # log mode: marks are recorded.
    assert "request_received_ms" in tracker.report_payload()
    # log mode: no STAGE_PROGRESS events.
    assert tracker.drain_stage_events() == []

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="observability.timing"):
        tracker.emit_log()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ttft" in m for m in msgs)


def test_mark_failure_does_not_break_request(monkeypatch):
    """If something inside ``mark()`` raises (clock weirdness, span
    corruption), the request must continue. Fail-open is a hard guarantee."""
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")

    # Sabotage: replace the marks dict with something that raises on
    # contains/setitem to simulate the worst-case mark() body explosion.
    class _ExplodingDict(dict):
        def __contains__(self, _key):
            raise RuntimeError("contains is broken")

        def __setitem__(self, _key, _value):
            raise RuntimeError("setitem is broken")

    tracker._marks = _ExplodingDict()

    # Must not raise. Just logs at WARNING.
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_BEFORE_MODEL_DONE, user_label="Thinking…")


def test_mark_is_idempotent_first_observation_wins(monkeypatch):
    """First-token style marks must NOT be overwritten by later events."""
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_FIRST_MODEL_TOKEN)
    first = tracker.report_payload()["first_model_token_ms"]
    # Burn cycles so perf_counter would tick if the second mark were
    # not properly idempotent.
    _burn = sum(range(2000))
    assert _burn  # silences unused-result lint without changing behaviour
    tracker.mark(timing.STAGE_FIRST_MODEL_TOKEN)
    second = tracker.report_payload()["first_model_token_ms"]
    assert first == second


def test_get_current_tracker_returns_noop_when_unset(monkeypatch):
    _set_mode(monkeypatch, "full")
    # Default contextvar is None — accessor returns the null tracker.
    null = timing.get_current_tracker()
    # Calling mark on it must not raise and must record nothing the caller
    # can see.
    null.mark(timing.STAGE_REQUEST_RECEIVED)
    assert null.drain_stage_events() == []


def test_set_and_reset_current_tracker_round_trip(monkeypatch):
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    token = timing.set_current_tracker(tracker)
    try:
        assert timing.get_current_tracker() is tracker
    finally:
        timing.reset_current_tracker(token)
    # After reset, accessor falls back to the null tracker.
    assert timing.get_current_tracker() is not tracker


def test_latency_report_event_shape(monkeypatch):
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_FIRST_MODEL_TOKEN)
    tracker.set_model("gemini-2.5-flash", "fast")
    tracker.increment_tool_invocations()

    event = tracker.build_latency_report_event()
    assert event is not None
    assert event.name == timing.LATENCY_REPORT_EVENT_NAME
    assert event.value["model_used"] == "gemini-2.5-flash"
    assert event.value["routing_choice"] == "fast"
    assert event.value["tools_invoked_count"] == 1
    assert event.value["ttft_mode"] == "full"
    assert "first_model_token_ms" in event.value


def test_module_level_mode_resolution_is_lowercase_and_validated():
    """The module rejects unknown modes at import time and falls back to
    full. The constant lives at module scope; this test asserts the
    invariant against whatever value the test runner started with."""
    assert timing.TTFT_MODE in {"full", "log", "off"}


# TTFT-OPTIMIZATION M1: the new finer-grained marks must be exported as
# stable string constants (downstream code grep for them; renaming the
# string would silently break the BigQuery query that reads ttft logs).
def test_optimization_m1_stage_constants_exposed():
    assert timing.STAGE_AGENT_FACTORY_DONE == "agent_factory_done"
    assert timing.STAGE_RUNNER_SETUP_DONE == "runner_setup_done"


def test_optimization_m1_marks_appear_in_log_line(monkeypatch, caplog):
    """When the agent_factory_done and runner_setup_done marks fire,
    they must show up as <name>_ms keys in the structured ttft log line
    so the operator can attribute the 5.7s gap from the baseline."""
    _set_mode(monkeypatch, "full")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_REQUEST_RECEIVED)
    tracker.mark(timing.STAGE_SESSION_INDEX_DONE)
    tracker.mark(timing.STAGE_AGENT_FACTORY_DONE)
    tracker.mark(timing.STAGE_RUNNER_SETUP_DONE)
    tracker.mark(timing.STAGE_BEFORE_AGENT_DONE)
    tracker.mark(timing.STAGE_BEFORE_MODEL_DONE)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="observability.timing"):
        tracker.emit_log()
    records = [r for r in caplog.records if "ttft" in r.getMessage()]
    assert records, "expected one ttft log line"
    payload = getattr(records[0], "json_fields", None)
    assert payload, "ttft log line must carry json_fields"
    for key in (
        "request_received_ms",
        "session_index_done_ms",
        "agent_factory_done_ms",
        "runner_setup_done_ms",
        "before_agent_done_ms",
        "before_model_done_ms",
    ):
        assert key in payload, f"missing {key!r} in ttft log payload"


def test_optimization_m1_marks_silent_in_off_mode(monkeypatch):
    """Kill switch must extend to the new marks too — off mode adds no
    payload keys for them."""
    _set_mode(monkeypatch, "off")
    tracker = timing.LatencyTracker(skill_id="s1", session_id="t1", user_id="u1")
    tracker.mark(timing.STAGE_AGENT_FACTORY_DONE)
    tracker.mark(timing.STAGE_RUNNER_SETUP_DONE)
    payload = tracker.report_payload()
    assert "agent_factory_done_ms" not in payload
    assert "runner_setup_done_ms" not in payload
