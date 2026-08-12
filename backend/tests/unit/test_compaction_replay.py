"""Compaction replay must observe, never change (findings log §3.2, §4).

This endpoint exists because every wrong turn in the compaction work was a
MEASUREMENT failure — a canary that passed under both a working and a broken
config, a probe whose synthetic fixture was degenerate. Replaying over REAL
recorded sessions is how the two open questions get answered.

Which makes its own correctness load-bearing in an unusual way: a replay tool
that mutated the sessions it studies would corrupt the evidence and make repeat
runs incomparable. The read-only guarantee is asserted here first.
"""

from __future__ import annotations

import pytest
from google.adk.events.event import Event
from google.genai import types

from admin.compaction_replay_routes import ReplayRequest, replay_compaction


class _Scope:
    class user:
        uid = "admin-uid"


def _event(i: int, ts: float, text: str | None = None) -> Event:
    e = Event(
        author="user" if i % 2 == 0 else "model",
        content=types.Content(role="user", parts=[types.Part(text=text or f"turn {i} content")]),
    )
    e.timestamp = ts
    return e


class _Session:
    def __init__(self, events):
        self.events = events


class _SessionService:
    """Records every call so a mutation attempt is caught, not assumed absent."""

    def __init__(self, session):
        self._session = session
        self.appended: list = []

    async def get_session(self, **_kw):
        return self._session

    async def append_event(self, **kw):  # pragma: no cover — must never run
        self.appended.append(kw)


@pytest.fixture
def wired(monkeypatch):
    """Patch the lazily-imported collaborators the route resolves at call time."""
    session = _Session([_event(i, float(i)) for i in range(30)])
    svc = _SessionService(session)
    monkeypatch.setattr("adk.session.get_session_service", lambda: svc)
    return svc


class _StubSummarizer:
    def __init__(self, result="a summary"):
        self._result = result
        self.calls = 0

    async def maybe_summarize_events(self, *, events):
        self.calls += 1
        if self._result is None:
            return None
        from google.adk.events.event_actions import EventActions, EventCompaction

        return Event(
            author="user",
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=events[0].timestamp,
                    end_timestamp=events[-1].timestamp,
                    compacted_content=types.Content(role="model", parts=[types.Part(text=self._result)]),
                )
            ),
        )


@pytest.mark.asyncio
async def test_replay_never_appends_to_the_session(wired, monkeypatch):
    """THE guarantee. A tool that mutated the sessions it studies would corrupt
    its own evidence and make repeat runs incomparable."""
    monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: _StubSummarizer())
    await replay_compaction(ReplayRequest(session_id="s1", user_id="u1", event_retention_size=5), _Scope())
    assert wired.appended == [], "replay wrote to the session — it must be read-only"


@pytest.mark.asyncio
async def test_reports_a_decline_explicitly(wired, monkeypatch):
    """The §3.2 diagnostic: the summariser ran and produced nothing.

    In production that means history was NOT compacted and the model call was
    paid for nothing — invisible unless something says so out loud.
    """
    monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: _StubSummarizer(result=None))
    res = await replay_compaction(ReplayRequest(session_id="s1", user_id="u1", event_retention_size=5), _Scope())
    assert res.declined is True
    assert res.summary is None
    assert any("DECLINED" in n for n in res.notes)


@pytest.mark.asyncio
async def test_a_raising_summariser_is_data_not_a_500(wired, monkeypatch):
    """A replay that explodes is a FINDING about the summariser. Returning 500
    would lose the diagnostic and tell the operator nothing."""

    class _Boom:
        async def maybe_summarize_events(self, *, events):
            raise RuntimeError("model exploded")

    monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: _Boom())
    res = await replay_compaction(ReplayRequest(session_id="s1", user_id="u1", event_retention_size=5), _Scope())
    assert res.declined is True
    assert any("RAISED" in n for n in res.notes)


@pytest.mark.asyncio
async def test_selection_only_makes_no_model_call(wired, monkeypatch):
    """Answering 'would this compact at all' must be free.

    Retention gates whether candidates exist, so this is the cheap first
    question — and it must not spend a model call to answer it.
    """
    stub = _StubSummarizer()
    monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: stub)
    res = await replay_compaction(
        ReplayRequest(session_id="s1", user_id="u1", event_retention_size=5, summarize=False), _Scope()
    )
    assert stub.calls == 0
    assert res.selected_events > 0
    assert res.summary is None


@pytest.mark.asyncio
async def test_retention_above_event_count_selects_nothing_and_says_why(wired, monkeypatch):
    """The retention floor (findings log §1) is the most surprising behaviour in
    the subsystem — a session that looks un-compactable is usually just below
    it. The note has to explain that, or the operator concludes it is broken."""
    monkeypatch.setattr("adk.compaction_summarizer.build_compaction_summarizer", lambda: _StubSummarizer())
    res = await replay_compaction(ReplayRequest(session_id="s1", user_id="u1", event_retention_size=999), _Scope())
    assert res.selected_events == 0
    assert any("retention" in n.lower() for n in res.notes)


@pytest.mark.asyncio
async def test_prompt_override_missing_placeholder_is_rejected_up_front(wired):
    """`str.format` would raise mid-summarisation with a confusing KeyError.
    Same validation the tuning console owes — fail at the boundary."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await replay_compaction(
            ReplayRequest(session_id="s1", user_id="u1", event_retention_size=5, prompt_template="no placeholder here"),
            _Scope(),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_missing_session_is_404_not_a_crash(monkeypatch):
    class _Empty:
        async def get_session(self, **_kw):
            return None

    monkeypatch.setattr("adk.session.get_session_service", lambda: _Empty())
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await replay_compaction(ReplayRequest(session_id="nope", user_id="u1"), _Scope())
    assert exc.value.status_code == 404
