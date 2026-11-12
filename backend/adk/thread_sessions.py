"""Bounded lookup for the platform's native-ID chat sessions.

The platform has always used ``thread_id == session_id`` (including bootstrap
and transcript reads). ag-ui-adk 0.10's general alias-recovery scan loads every
session's full state on a miss. With Vertex that can allocate gigabytes before
RUN_STARTED. This manager deliberately implements the platform's native-ID
contract, rather than ag-ui's optional generated-ID/alias mode.

Ownership, invalid IDs and storage failures remain loud. In particular, a
failed read must never become permission to create a replacement conversation.
"""

from __future__ import annotations

import re
from typing import Any

from ag_ui_adk.session_manager import SessionManager


class NativeThreadSessionManager(SessionManager):
    """Resolve only the addressed session; never enumerate conversation state."""

    async def resolve_existing_session(self, thread_id: str, app_name: str, user_id: str) -> Any | None:
        # The SDK also accepts full resource names and shortens them. A chat
        # thread must be one literal ID, otherwise two index rows can address
        # the same unstamped bootstrapped conversation.
        if not isinstance(thread_id, str) or re.fullmatch(r"[A-Za-z0-9_-]+", thread_id) is None:
            raise ValueError("Invalid thread ID: expected one native session ID")
        session = await self._session_service.get_session(app_name=app_name, user_id=user_id, session_id=thread_id)
        if session is not None:
            # Vertex enforces user ownership, including through the read memo.
            # Check app/thread stamps too: engine-wide IDs must not alias a
            # different thread or an app sharing the same engine.
            if session.user_id != user_id or not self._claimable_by(session, app_name, thread_id):
                raise ValueError("Session does not belong to this user, app and thread")
            self._cache_session(session.id, app_name, user_id, session)
        return session

    async def _get_native_session(
        self, session_id: str, app_name: str, user_id: str, *, listed: list | None = None
    ) -> Any | None:
        # Used after a concurrent create. Keep the same ownership checks and
        # bounded read, rather than the superclass's engine-wide list fallback.
        return await self.resolve_existing_session(session_id, app_name, user_id)


def uses_vertex_sessions(service: Any) -> bool:
    """Recognize our read-memo wrapper without hiding Vertex ownership rules."""
    from google.adk.sessions import VertexAiSessionService

    from adk.session_memo import ReadMemoSessionService

    while isinstance(service, ReadMemoSessionService):
        service = service._inner
    return isinstance(service, VertexAiSessionService)
