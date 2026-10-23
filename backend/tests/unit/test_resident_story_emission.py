"""Live emission of the resident session story (v6.40.0 M7 Phase 3).

Phase 2 shaped the surface; this is the half that puts it on the wire WHILE the
run is still going, which is the actual complaint — `ask_assistant` blocks for
up to 180s and the user sees nothing until it returns.

The properties worth protecting here are the ones whose failure looks like
success:

  * one surfaceId per run, so repeat emissions UPDATE rather than accumulate a
    workbench tab per poll tick;
  * a distinct sourceId per emission, because that is the registry's
    idempotency key and a repeated one is silently dropped;
  * the observer can never fail the turn it decorates.
"""

from __future__ import annotations

import asyncio

import pytest

from adk.a2ui_resident_render import RESIDENT_SURFACE, STORY_ENABLED_ENV, STORY_STATE_KEY


class _State(dict):
    pass


class _Ctx:
    def __init__(self):
        self.state = _State()
        self.invocation_id = "inv-1"


class _Tracker:
    """Stands in for the per-request LatencyTracker."""

    def __init__(self):
        self.emissions = []

    def emit_a2ui_surface(self, surface_id, messages, source_id, artifact=None):
        self.emissions.append({"surface": surface_id, "messages": messages, "source": source_id, "artifact": artifact})


def _task(state="working", events=None, answer=None):
    artifacts = []
    if events is not None:
        artifacts.append({"artifactId": "events", "parts": [{"kind": "data", "data": {"events": events}}]})
    if answer is not None:
        artifacts.append({"artifactId": "response", "parts": [{"kind": "text", "text": answer}]})
    return {"id": "t-1", "status": {"state": state}, "artifacts": artifacts}


@pytest.fixture
def observer(monkeypatch):
    """A story observer wired to a fake tracker."""
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    from tools import resident_agent

    tracker = _Tracker()
    monkeypatch.setattr("observability.timing.get_current_tracker", lambda: tracker)
    monkeypatch.setattr("observability.timing.has_current_tracker", lambda: True)
    monkeypatch.setattr(resident_agent, "_instance_label", lambda _uid: "resident-test")
    ctx = _Ctx()
    return resident_agent._story_observer(ctx, "u1"), tracker, ctx


# ── the emission contract ────────────────────────────────────────────────────


def test_every_tick_targets_the_SAME_surface(observer):
    """Accumulating a tab per poll is the failure that looks like success."""
    on_poll, tracker, _ = observer
    for i in range(4):
        on_poll(_task(events=[{"t": i, "type": "text", "text": f"step {i}"}]))
    assert len(tracker.emissions) == 4
    assert {e["surface"] for e in tracker.emissions} == {RESIDENT_SURFACE}


def test_every_tick_carries_a_DISTINCT_source_id(observer):
    """The frontend SurfaceRegistry dedupes on sourceId. A repeated one means
    the second update is dropped and the surface freezes mid-run."""
    on_poll, tracker, _ = observer
    for i in range(3):
        on_poll(_task(events=[{"t": i, "type": "text", "text": f"step {i}"}]))
    sources = [e["source"] for e in tracker.emissions]
    assert len(set(sources)) == len(sources)
    assert all(s.startswith("inv-1:ask_assistant:poll:") for s in sources)


def test_the_artifact_metadata_rides_along(observer):
    """Without it the emission registers no workbench artifact, artifactCount
    stays 0, and the tab never appears — trap (4) in backend/adk/CLAUDE.md."""
    on_poll, tracker, _ = observer
    on_poll(_task(events=[{"t": 1, "type": "text", "text": "x"}]))
    assert tracker.emissions[0]["artifact"]["kind"] == "resident-session"


def test_a_tick_with_nothing_to_show_emits_nothing(observer):
    """An empty surface is worse than none."""
    on_poll, tracker, _ = observer
    on_poll(_task(state="submitted"))
    assert tracker.emissions == []


def test_the_story_is_recorded_for_the_final_mapping_every_tick(observer):
    """Recorded every tick, not just the last: a turn that times out or fails
    still has a story worth leaving behind."""
    on_poll, _, ctx = observer
    on_poll(_task(events=[{"t": 1, "type": "text", "text": "x"}]))
    story = ctx.state[STORY_STATE_KEY]
    assert story["instance"] == "resident-test"
    assert story["turns"][-1]["state"] == "working"
    on_poll(_task(state="failed", events=[{"t": 2, "type": "text", "text": "y"}]))
    assert ctx.state[STORY_STATE_KEY]["turns"][-1]["state"] == "failed"


def test_a_turn_is_rewritten_in_place_not_appended_per_tick(observer):
    """The loop polls every 2s for up to 180s. One turn must stay ONE entry,
    or a three-minute run becomes ninety identical cards."""
    on_poll, _, ctx = observer
    for i in range(5):
        on_poll(_task(events=[{"t": i, "type": "text", "text": f"x{i}"}]))
    assert len(ctx.state[STORY_STATE_KEY]["turns"]) == 1


def test_a_second_question_adds_a_turn_and_keeps_the_first(observer):
    """The resident remembers the whole conversation; the surface used to
    replace it each turn and was the only party losing the thread."""
    on_poll, _, ctx = observer
    on_poll(
        {
            "id": "task-a",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": "events",
                    "parts": [{"kind": "data", "data": {"events": [{"t": 1, "type": "text", "text": "a"}]}}],
                }
            ],
        }
    )
    on_poll(
        {
            "id": "task-b",
            "status": {"state": "working"},
            "artifacts": [
                {
                    "artifactId": "events",
                    "parts": [{"kind": "data", "data": {"events": [{"t": 2, "type": "text", "text": "b"}]}}],
                }
            ],
        }
    )
    turns = ctx.state[STORY_STATE_KEY]["turns"]
    assert [t["task_id"] for t in turns] == ["task-a", "task-b"]


def test_the_question_is_recorded_with_its_turn(monkeypatch):
    """Without it the transcript is a list of tool calls with nothing saying
    what was asked."""
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    from tools import resident_agent

    tracker = _Tracker()
    monkeypatch.setattr("observability.timing.get_current_tracker", lambda: tracker)
    monkeypatch.setattr("observability.timing.has_current_tracker", lambda: True)
    monkeypatch.setattr(resident_agent, "_instance_label", lambda _uid: "")
    ctx = _Ctx()
    on_poll = resident_agent._story_observer(ctx, "u1", question="what is in the workspace?")
    on_poll(_task(events=[{"t": 1, "type": "text", "text": "x"}]))
    assert ctx.state[STORY_STATE_KEY]["turns"][-1]["question"] == "what is in the workspace?"


def test_flag_off_means_no_observer_at_all(monkeypatch):
    """Not a no-op observer — None, so converse keeps its fast path."""
    monkeypatch.delenv(STORY_ENABLED_ENV, raising=False)
    from tools import resident_agent

    assert resident_agent._story_observer(_Ctx(), "u1") is None


def test_no_tool_context_means_no_observer(monkeypatch):
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    from tools import resident_agent

    assert resident_agent._story_observer(None, "u1") is None


def test_an_unbound_tracker_is_reported_not_swallowed(monkeypatch, caplog):
    """With no tracker bound every emit silently no-ops, the frontend registers
    nothing, and the agent narrates a Workspace update that never happened.
    That is the compare-launcher bug; it must at least reach the log."""
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    from tools import resident_agent

    monkeypatch.setattr("observability.timing.has_current_tracker", lambda: False)
    monkeypatch.setattr(resident_agent, "_instance_label", lambda _uid: "")
    with caplog.at_level("WARNING"):
        assert resident_agent._story_observer(_Ctx(), "u1") is not None
    assert "no LatencyTracker bound" in caplog.text


# ── the observer must never break the turn ───────────────────────────────────


def test_converse_swallows_an_observer_that_raises():
    """A decoration must never be able to fail the turn it decorates."""
    from protocols import a2a_client

    calls = {"n": 0}

    async def fake_get_task(base_url, task_id, *, transport=None):
        calls["n"] += 1
        return _task(state="completed", answer="done")

    async def fake_send(*a, **k):
        return _task(state="working")

    async def run():
        orig_get, orig_send = a2a_client.get_task, a2a_client.send_message
        a2a_client.get_task, a2a_client.send_message = fake_get_task, fake_send
        try:

            def boom(_task):
                raise RuntimeError("observer exploded")

            return await a2a_client.converse("https://x", "hi", context_id="c", poll_s=0, on_poll=boom)
        finally:
            a2a_client.get_task, a2a_client.send_message = orig_get, orig_send

    task = asyncio.run(run())
    assert (task.get("status") or {}).get("state") == "completed"


def test_converse_observes_the_first_task_and_every_poll():
    """The first task is where `submitted` is seen. Skipping it means the user
    watches nothing until the first 2s tick — visible, and avoidable."""
    from protocols import a2a_client

    seen = []
    states = iter(["working", "completed"])

    async def fake_get_task(base_url, task_id, *, transport=None):
        return _task(state=next(states))

    async def fake_send(*a, **k):
        return _task(state="submitted")

    async def run():
        orig_get, orig_send = a2a_client.get_task, a2a_client.send_message
        a2a_client.get_task, a2a_client.send_message = fake_get_task, fake_send
        try:
            return await a2a_client.converse(
                "https://x",
                "hi",
                context_id="c",
                poll_s=0,
                on_poll=lambda t: seen.append((t.get("status") or {}).get("state")),
            )
        finally:
            a2a_client.get_task, a2a_client.send_message = orig_get, orig_send

    asyncio.run(run())
    assert seen == ["submitted", "working", "completed"]


def test_converse_without_an_observer_is_unchanged():
    from protocols import a2a_client

    async def fake_get_task(base_url, task_id, *, transport=None):
        return _task(state="completed", answer="done")

    async def fake_send(*a, **k):
        return _task(state="working")

    async def run():
        orig_get, orig_send = a2a_client.get_task, a2a_client.send_message
        a2a_client.get_task, a2a_client.send_message = fake_get_task, fake_send
        try:
            return await a2a_client.converse("https://x", "hi", context_id="c", poll_s=0)
        finally:
            a2a_client.get_task, a2a_client.send_message = orig_get, orig_send

    assert (asyncio.run(run()).get("status") or {}).get("state") == "completed"


def test_answer_of_still_finds_the_answer_past_the_events_artifact():
    """The run loop writes `events` FIRST, so it is artifacts[0]. `answer_of`
    takes the first TEXT part of any artifact — correct only while the events
    artifact carries none. It carries a `data` part; this pins that."""
    from protocols.a2a_client import answer_of

    task = _task(events=[{"t": 1, "type": "text", "text": "internal narration"}], answer="the real answer")
    assert task["artifacts"][0]["artifactId"] == "events"
    assert answer_of(task) == "the real answer"
