"""``ReadMemoSessionService`` — one fetch per turn, no write on the critical path.

Measured on a deployed fork (2026-09-21): a turn read the SAME Agent Engine
session three times in ~100 ms (ag_ui_adk's cache check, its state read, the
Runner's read) and awaited two writes (the user's message, the before-agent
state delta) before the model was asked — ~2.4 s of a 4.2 s first token. The
properties pinned here are the ones that make collapsing that safe:

1. Reads inside one request return the SAME object (ADK mutates it in place).
2. Nothing survives the request; outside one, every call is passthrough.
3. Writes apply in memory immediately and go to the wire in order, without
   the caller waiting; the real object is never appended to twice.
4. A failed write is logged, never raised, and never blocks the ones behind it.
5. The stream opens the memo around the run and drains it at the end.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from adk import session_memo
from adk.session_memo import ReadMemoSessionService, drain_pending_session_writes, session_read_memo


class _Inner:
    """Replicates what matters about ``VertexAiSessionService``: a fresh Session
    object per ``get_session``, and an ``append_event`` that applies the
    in-memory half to whatever session it is handed before writing."""

    def __init__(self, *, delay: float = 0.0, fail_first: bool = False) -> None:
        self.gets = 0
        self.started: list[str] = []
        self.finished: list[str] = []
        self.seen: list[Session] = []
        self._delay = delay
        self._fail_first = fail_first

    async def get_session(self, *, app_name, user_id, session_id, config=None):
        self.gets += 1
        return Session(app_name=app_name, user_id=user_id, id=session_id)

    async def create_session(self, *, app_name, user_id, state=None, session_id=None):
        return Session(app_name=app_name, user_id=user_id, id=session_id or "new", state=state or {})

    async def append_event(self, session, event):
        self.started.append(event.author)
        self.seen.append(session)
        session.events.append(event)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_first and len(self.started) == 1:
            raise RuntimeError("wire down")
        self.finished.append(event.author)
        return event

    async def delete_session(self, *, app_name, user_id, session_id):
        return None

    async def list_sessions(self, *, app_name, user_id=None):
        raise NotImplementedError

    def extra_helper(self) -> str:
        return "forwarded"


def _event(author: str) -> Event:
    role = "user" if author == "user" else "model"
    return Event(author=author, content=types.Content(role=role, parts=[types.Part.from_text(text="hej")]))


def _build(**kw) -> tuple[ReadMemoSessionService, _Inner]:
    inner = _Inner(**kw)
    return ReadMemoSessionService(inner), inner


# --- reads ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_reads_in_one_request_fetch_once_and_share_the_object():
    svc, inner = _build()
    with session_read_memo():
        a = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        b = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        c = await svc.get_session(app_name="app", user_id="u", session_id="s1")
    assert inner.gets == 1
    assert a is b is c  # the same object, not an equal copy


@pytest.mark.asyncio
async def test_outside_a_request_every_read_reaches_the_backend():
    svc, inner = _build()
    await svc.get_session(app_name="app", user_id="u", session_id="s1")
    await svc.get_session(app_name="app", user_id="u", session_id="s1")
    assert inner.gets == 2


@pytest.mark.asyncio
async def test_nothing_survives_the_request():
    svc, inner = _build()
    with session_read_memo():
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
    with session_read_memo():
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
    assert inner.gets == 2
    assert not session_memo.memo_active()


@pytest.mark.asyncio
async def test_a_different_session_or_user_is_its_own_fetch():
    svc, inner = _build()
    with session_read_memo():
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.get_session(app_name="app", user_id="u", session_id="s2")
        await svc.get_session(app_name="app", user_id="v", session_id="s1")
    assert inner.gets == 3


@pytest.mark.asyncio
async def test_a_config_shaped_read_bypasses_the_memo():
    """``num_recent_events`` is a different VIEW; serving the full object for
    it, or memoising the partial one, would both be wrong."""
    svc, inner = _build()
    with session_read_memo():
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.get_session(
            app_name="app", user_id="u", session_id="s1", config=GetSessionConfig(num_recent_events=2)
        )
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
    assert inner.gets == 2


@pytest.mark.asyncio
async def test_create_populates_the_memo_and_delete_evicts():
    svc, inner = _build()
    with session_read_memo():
        created = await svc.create_session(app_name="app", user_id="u", session_id="s1")
        got = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        assert got is created and inner.gets == 0
        await svc.delete_session(app_name="app", user_id="u", session_id="s1")
        await svc.get_session(app_name="app", user_id="u", session_id="s1")
        assert inner.gets == 1


@pytest.mark.asyncio
async def test_a_task_started_inside_the_request_shares_the_memo():
    """ag_ui_adk runs the ADK Runner in a task it creates inside ``run()``; a
    task copies the context it was created in, so the Runner's read must land
    in the SAME memo as the reads made before the task existed."""
    svc, inner = _build()
    with session_read_memo():
        first = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        second = await asyncio.create_task(svc.get_session(app_name="app", user_id="u", session_id="s1"))
    assert inner.gets == 1 and first is second


def test_unknown_attributes_are_forwarded_to_the_inner_service():
    svc, _ = _build()
    assert svc.extra_helper() == "forwarded"


# --- writes -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_apply_in_memory_now_and_reach_the_wire_in_order_without_blocking():
    svc, inner = _build(delay=0.02)
    with session_read_memo():
        session = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.append_event(session, _event("user"))
        await svc.append_event(session, _event("agent"))
        await svc.append_event(session, _event("agent"))
        # None of the three waited for the wire; all three are in memory.
        assert inner.finished == []
        assert [e.author for e in session.events] == ["user", "agent", "agent"]
        await drain_pending_session_writes()
    assert inner.started == ["user", "agent", "agent"]  # chained, not raced
    assert inner.finished == ["user", "agent", "agent"]


@pytest.mark.asyncio
async def test_the_real_session_is_not_appended_to_twice():
    """The inner service repeats the in-memory half before it writes; it must
    do so on a shadow, or the user's message appears twice in the history."""
    svc, inner = _build()
    with session_read_memo():
        session = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.append_event(session, _event("user"))
        await drain_pending_session_writes()
    assert len(session.events) == 1
    shadow = inner.seen[0]
    assert shadow is not session and shadow.id == session.id and shadow.app_name == session.app_name


@pytest.mark.asyncio
async def test_a_partial_event_is_passed_straight_through():
    """ADK never persists partials; the base in-memory half returns early on
    them and so must we — without queueing a wire write for nothing."""
    svc, inner = _build()
    partial = Event(
        author="agent", partial=True, content=types.Content(role="model", parts=[types.Part.from_text(text="h")])
    )
    with session_read_memo():
        session = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.append_event(session, partial)
        assert session_memo._pending_writes.get() == []
    assert inner.started == ["agent"]  # the inner saw it synchronously, as before


@pytest.mark.asyncio
async def test_outside_a_request_writes_are_synchronous_as_before():
    svc, inner = _build(delay=0.01)
    session = Session(app_name="app", user_id="u", id="s1")
    await svc.append_event(session, _event("user"))
    assert inner.finished == ["user"]


@pytest.mark.asyncio
async def test_a_failed_write_is_logged_not_raised_and_does_not_stop_the_next(caplog):
    svc, inner = _build(fail_first=True)
    with session_read_memo(), caplog.at_level(logging.ERROR, logger="adk.session_memo"):
        session = await svc.get_session(app_name="app", user_id="u", session_id="s1")
        await svc.append_event(session, _event("user"))
        await svc.append_event(session, _event("agent"))
        await drain_pending_session_writes()
    assert inner.finished == ["agent"]
    assert any("background session write failed" in r.message for r in caplog.records)


# --- wiring -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stream_opens_the_memo_around_the_run_and_drains_at_the_end():
    """The wiring guard: the memo is worthless if ``stream_agui_events`` does
    not open it, and a write still in flight when the response ends may never
    land (Cloud Run throttles the CPU after the response)."""
    from adk.agui import stream_agui_events

    svc, inner = _build(delay=0.02)
    seen: list[bool] = []

    class FakeAgent:
        async def run(self, run_input):
            seen.append(session_memo.memo_active())
            session = await svc.get_session(app_name="app", user_id="u", session_id="s1")
            await svc.append_event(session, _event("user"))
            if False:  # pragma: no cover - keeps this an async generator
                yield None

    assert not session_memo.memo_active()
    async for _ in stream_agui_events(FakeAgent(), run_input=None, heartbeat_seconds=0):  # type: ignore[arg-type]
        pass
    assert seen == [True]
    assert inner.finished == ["user"]
    assert not session_memo.memo_active()
