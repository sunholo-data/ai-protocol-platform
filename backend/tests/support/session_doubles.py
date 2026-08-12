"""Session doubles that enforce Vertex's ownership semantics (AIPLA #35).

`InMemorySessionService` returns ``None`` when a non-owner asks for a session.
Real `VertexAiSessionService` **raises** — it compares the stored owner against
the requesting `user_id`:

    if response.user_id != user_id:
        raise ValueError("Session ... does not belong to user")

That difference is not academic. The reporting fork changed its anonymous-group
uid derivation, correctly updated its Firestore *queries* to match both old and
new schemes, and shipped. Sessions created under the old scheme were owned by
the legacy uid, so the exact-match check rejected them; `ag_ui_adk`'s
SessionManager swallowed the error to `None`; the reused threadId then collided
on `create_session` with "already exists"; the background run died and chat
returned no text. MCP-app tool events were unaffected, so the sims kept working
while chat went silent — a genuinely confusing signature to debug live.

Every chat-path test used `InMemorySessionService`, so CI could not have caught
it: a silent ``None`` is exactly what the buggy path produced, and it is
indistinguishable from "this session does not exist". Use
:class:`OwnershipEnforcingSessionService` wherever a test touches session
identity — it makes "not yours" loud, which is what Vertex does.
"""

from __future__ import annotations

from typing import Any

from google.adk.sessions import InMemorySessionService


class OwnershipEnforcingSessionService(InMemorySessionService):
    """`InMemorySessionService` + Vertex's exact-owner check on reads.

    Deliberately a subclass rather than a reimplementation: everything except
    the ownership rule should behave exactly like the service the rest of the
    suite already uses, so a test that fails here fails for the ownership
    reason and nothing else.
    """

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any = None,
    ):
        # Look the session up as its real owner would, so we can compare owners
        # rather than just returning None for a mismatch — None is what the
        # permissive double already does, and it is the wrong signal: it looks
        # like "no such session" when the truth is "not yours".
        for owner_sessions in getattr(self, "sessions", {}).get(app_name, {}).items():
            owner_uid, by_id = owner_sessions
            if session_id in by_id and owner_uid != user_id:
                raise ValueError(
                    f"Session {session_id} does not belong to user {user_id}. "
                    f"(owner={owner_uid}) — Vertex enforces exact-match ownership; "
                    "an identity/uid migration needs a compatibility shim."
                )

        return await super().get_session(app_name=app_name, user_id=user_id, session_id=session_id, config=config)
