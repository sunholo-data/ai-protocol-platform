"""One session fetch per turn, and no session write on the critical path.

## Why this exists

Measured on a deployed fork (AIPLA, Cloud Run europe-north1 → Agent Engine
europe-west1, 2026-09-21) with the ``ttft`` tracker and Cloud Trace: a student's
turn spent **~2.8 s before the model was asked anything**, flat regardless of
session length, of which ~2.4 s was Agent Engine round trips on the SAME
session:

* ag_ui_adk's cache-hit check ``get_session``           ~0.6 s
* ag_ui_adk's ``pending_tool_calls`` state read          ~0.6 s  (another get)
* the ADK Runner's own ``get_session``                   ~0.6 s
* the Runner's ``append_event`` of the user's message    ~0.6 s  (awaited before
                                                                  the agent starts)
* the before-agent state-delta ``append_event``          ~0.6 s  (awaited before
                                                                  the model is asked)

Every ``get_session`` is two HTTP calls (get + list events). Nothing between
those calls changes the session except the process itself, and nothing
downstream reads a write's *result* — only its in-memory half. The model's own
first token was ~1.0 s. Shipping this halved the student-perceived TTFT
(4.2 s → ~2.2 s, measured).

## What it does

``ReadMemoSessionService`` wraps any ``BaseSessionService``. Inside a request
(``session_read_memo()`` open, which ``stream_agui_events`` does around the run):

* **Reads are memoised** per ``(app_name, user_id, session_id)`` — the second
  and third ``get_session`` return the FIRST call's object. Safe because ADK
  mutates the ``Session`` object in place (``append_event`` appends to
  ``session.events`` and applies the state delta on that object before writing
  through), so every later reader holds exactly what the first reader's object
  has become. A config-shaped read (``GetSessionConfig``) is a different view
  and bypasses the memo.
* **Writes apply in memory now and go to the wire in the background, chained
  in order.** ``append_event`` applies the in-memory half to the real object,
  returns, and queues the wire write behind the previous one, so order on the
  wire matches order in memory. The inner service is handed a shadow copy
  (own ``events`` list, own ``state`` dict) so ITS in-memory half does not
  append to the real object a second time — the wire half reads nothing from
  the session but ``app_name`` and ``id``.
* **``drain_pending_session_writes()``** awaits the chain. The stream calls it
  when the run is over, before the response ends — Cloud Run throttles the CPU
  after that and a write still in flight might never land.

Outside a request the wrapper is transparent: every read hits the backend,
every write is synchronous.

## The trade this makes — read before wrapping a store that retries

A failed background write is **logged at ERROR with the ids, not raised**: the
reply it belongs to has already reached the user. ``ResilientVertexSessionService``
retries transient failures and then re-raises "so ADK sees it" (issue #30);
under this wrapper that re-raise ends in the drain's log line rather than
failing the turn. The failure is still never silent — it is just no longer
fatal to a turn whose reply is already on screen. Do NOT wrap a store whose
write failure must abort the turn.

## Why a memo and not a cache

Nothing outlives the request. The memo is a ``ContextVar`` opened by the stream
and closed with it; a second request for the same session on the same instance
re-fetches. The "cache + write-back" pattern was considered and rejected in
docs/design/v6.1.0/implemented/ttft-optimization.md (cross-instance coherence,
crash safety); this is deliberately narrower than that.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.sessions.base_session_service import (
    BaseSessionService,
    GetSessionConfig,
    ListSessionsResponse,
)

logger = logging.getLogger(__name__)

_MemoKey = tuple[str, str, str]

#: Per-request memo of ``get_session`` reads.
_read_memo: ContextVar[dict[_MemoKey, Session] | None] = ContextVar("session_read_memo", default=None)

#: Per-request chain of wire writes still in flight (each task awaits the previous).
_pending_writes: ContextVar[list[asyncio.Task[Any]] | None] = ContextVar("session_pending_writes", default=None)


def memo_active() -> bool:
    """True inside a ``session_read_memo()`` block (tests, diagnostics)."""
    return _read_memo.get() is not None


@contextlib.contextmanager
def session_read_memo() -> Iterator[None]:
    """Open the per-request memo + write chain. See the module docstring."""
    read_token = _read_memo.set({})
    write_token = _pending_writes.set([])
    try:
        yield
    finally:
        try:
            _pending_writes.reset(write_token)
            _read_memo.reset(read_token)
        except ValueError:
            # The stream route pulls its first event in the request's context
            # and iterates the rest inside Starlette's response task, so the
            # block can close in a different Context from the one it opened in
            # (the latency tracker tolerates the same split). Clear here
            # instead; the opening context ends with the request.
            _read_memo.set(None)
            _pending_writes.set(None)


async def drain_pending_session_writes() -> None:
    """Await every background write started in this request.

    Order between writes is the chain's own business (each waits for the
    previous), so this is only needed at the end of the request. A failed
    write is logged, never raised — see the module docstring for the trade.
    """
    pending = _pending_writes.get()
    if not pending:
        return
    tasks, pending[:] = list(pending), []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("session-memo: background session write failed (%s): %r", task.get_name(), result)


class ReadMemoSessionService(BaseSessionService):
    """``BaseSessionService`` wrapper: memoised reads + chained background writes.

    Wrap once, at the ``get_session_service`` construction seam, so every
    caller (ag_ui_adk, the Runner, the messages endpoint) shares it. Attributes
    the wrapper does not define are forwarded to the inner service.
    """

    def __init__(self, inner: BaseSessionService) -> None:
        self._inner = inner

    # --- reads ---------------------------------------------------------

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: GetSessionConfig | None = None,
    ) -> Session | None:
        memo = _read_memo.get() if config is None else None
        key: _MemoKey = (app_name, user_id, session_id)
        if memo is not None and key in memo:
            return memo[key]
        session = await self._inner.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id, config=config
        )
        if memo is not None and session is not None:
            memo[key] = session
        return session

    async def list_sessions(self, *, app_name: str, user_id: str | None = None) -> ListSessionsResponse:
        return await self._inner.list_sessions(app_name=app_name, user_id=user_id)

    # --- writes --------------------------------------------------------

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> Session:
        session = await self._inner.create_session(
            app_name=app_name, user_id=user_id, state=state, session_id=session_id
        )
        memo = _read_memo.get()
        if memo is not None:
            memo[(app_name, user_id, session.id)] = session
        return session

    async def append_event(self, session: Session, event: Event) -> Event:
        pending = _pending_writes.get()
        if pending is None or getattr(event, "partial", False):
            return await self._inner.append_event(session=session, event=event)

        applied = await BaseSessionService.append_event(self, session=session, event=event)
        shadow = session.model_copy(update={"events": [], "state": dict(session.state)})
        previous = pending[-1] if pending else None

        async def _write_in_order() -> Event:
            if previous is not None:
                # Order, not success: a failed earlier write is logged by the
                # drain; this one must still go out.
                await asyncio.gather(previous, return_exceptions=True)
            return await self._inner.append_event(session=shadow, event=event)

        pending.append(asyncio.create_task(_write_in_order(), name=f"session-write:{session.id}:{event.author}"))
        return applied

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        memo = _read_memo.get()
        if memo is not None:
            memo.pop((app_name, user_id, session_id), None)
        await self._inner.delete_session(app_name=app_name, user_id=user_id, session_id=session_id)

    # --- everything else -----------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)


__all__ = [
    "ReadMemoSessionService",
    "drain_pending_session_writes",
    "memo_active",
    "session_read_memo",
]
