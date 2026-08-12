"""Retry + loud-failure wrapper around ``VertexAiSessionService``.

## Why this exists

We use Agent Engine as a *standalone session store* (``VertexAiSessionService``
pointed at a bare reasoning-engine resource) rather than deploying the agent onto
Agent Engine — see ``bootstrap_agent_engine.py`` and
docs/design .../use-an-adk-agent. That standalone write path (``create_session``
/ ``append_event``) had **no resilience**: a transient Vertex error (5xx / 429 /
timeout) silently dropped a conversation's events, while the separate Firestore
``chat_sessions`` mirror writer kept recording ``turnCount``. The result — seen
on test — is a mirror row that advertises "121 turns" with **no readable
canonical transcript** (issue #30). The loss was *invisible*: no error surfaced,
so it looked identical to a healthy session until someone tried to re-open it.

Model calls MUST go through a resilience layer (backend/CLAUDE.md); session
persistence had none. This closes that gap for the write path:

  * **Retry** ``create_session`` / ``append_event`` on transient errors with
    bounded exponential backoff. A failed attempt didn't append, so retrying is
    safe (no double-write).
  * **Never-silent give-up**: after the last attempt, log at ``ERROR`` with the
    op + ids so the drop is *visible in logs* — then re-raise so ADK/ag_ui_adk
    sees the failure too (we surface it, we don't swallow it).
  * Reads (``get`` / ``list`` / ``delete``) are left to the parent unchanged.

Wrapping happens once, at the ``get_session_service`` construction seam
(``adk/session.py``); every caller (skill_processor, the messages endpoint,
ag_ui_adk) then shares the resilient instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.events import Event
from google.adk.sessions import Session, VertexAiSessionService
from google.api_core import exceptions as gapi

logger = logging.getLogger(__name__)

# Transient Vertex/gRPC failures worth retrying. 4xx (bad request, not-found,
# permission) are NOT here — retrying them just wastes time and re-raises the
# same permanent error.
_RETRYABLE: tuple[type[BaseException], ...] = (
    gapi.ServiceUnavailable,  # 503
    gapi.TooManyRequests,  # 429
    gapi.DeadlineExceeded,  # 504-ish / gRPC deadline
    gapi.GatewayTimeout,  # 504
    gapi.InternalServerError,  # 500
    gapi.Aborted,  # 409 concurrency abort — safe to retry a fresh attempt
    gapi.RetryError,  # api_core gave up its own inner retry
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)

_MAX_ATTEMPTS = 4
_BASE_DELAY_S = 0.25  # 0.25, 0.5, 1.0 between the 4 attempts


class ResilientVertexSessionService(VertexAiSessionService):
    """``VertexAiSessionService`` with retry + loud failure on the write path."""

    async def _with_retry(self, op: str, ids: str, call):  # type: ignore[no-untyped-def]
        last: BaseException | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await call()
            except _RETRYABLE as exc:
                last = exc
                if attempt < _MAX_ATTEMPTS:
                    delay = _BASE_DELAY_S * (2 ** (attempt - 1))
                    logger.warning(
                        "session-persist: transient %s failure (%s) on %s [attempt %d/%d] — retrying in %.2fs",
                        op,
                        type(exc).__name__,
                        ids,
                        attempt,
                        _MAX_ATTEMPTS,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                # Final give-up — make the loss VISIBLE (never-silent) then re-raise.
                logger.error(
                    "session-persist: %s FAILED after %d attempts on %s (%s) — "
                    "conversation events for this session were NOT persisted to Agent Engine; "
                    "the chat_sessions mirror row will exist without a canonical transcript (issue #30)",
                    op,
                    _MAX_ATTEMPTS,
                    ids,
                    type(exc).__name__,
                )
                raise
        # Unreachable (loop either returns or raises), but satisfies type-checkers.
        assert last is not None
        raise last

    async def create_session(  # type: ignore[override]
        self,
        *,
        app_name: str,
        user_id: str,
        state: dict[str, Any] | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> Session:
        return await self._with_retry(
            "create_session",
            f"app={app_name} user={user_id} session={session_id}",
            lambda: super(ResilientVertexSessionService, self).create_session(
                app_name=app_name,
                user_id=user_id,
                state=state,
                session_id=session_id,
                **kwargs,
            ),
        )

    async def append_event(self, session: Session, event: Event) -> Event:  # type: ignore[override]
        return await self._with_retry(
            "append_event",
            f"session={getattr(session, 'id', '?')} author={getattr(event, 'author', '?')}",
            lambda: super(ResilientVertexSessionService, self).append_event(session, event),
        )
