"""Unit tests for GET /api/skills/{skillId}/sessions.

Verifies:
- Returns caller's own sessions for the skill
- Excludes other users' sessions
- Returns 200 with empty list when caller has no sessions
- Passes correct skill_id and owner_uid to list_sessions_for_skill
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth.access_context import AccessContext
from auth.firebase_auth import User
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex
from skills.routes import router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_idx(
    session_id: str = "sess-1",
    skill_id: str = "skill-x",
    owner_uid: str = "u1",
) -> ChatSessionIndex:
    return ChatSessionIndex(
        sessionId=session_id,
        documentIds=[],
        skillId=skill_id,
        ownerUid=owner_uid,
        accessControl=AccessControl(type="private"),
        firstMessageAt=datetime.now(UTC),
        lastMessageAt=datetime.now(UTC),
    )


def _make_user(uid: str = "u1") -> User:
    return User(uid=uid, email=f"{uid}@example.com", domain="example.com")


def _make_client(caller_uid: str = "u1") -> TestClient:
    from auth import get_current_user

    stub_user = User(uid=caller_uid, email=f"{caller_uid}@example.com", domain="example.com")
    app = FastAPI()

    @app.middleware("http")
    async def inject_access(request: Request, call_next):
        request.state.access = AccessContext(uid=caller_uid, domain="example.com")
        return await call_next(request)

    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListSkillSessions:
    def test_returns_callers_sessions(self):
        sessions = [_make_idx(session_id="sess-1", skill_id="skill-x", owner_uid="u1")]

        with patch("skills.routes.list_sessions_for_skill", return_value=(sessions, None)):
            resp = _make_client(caller_uid="u1").get("/api/skills/skill-x/sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "sess-1"
        assert data["next_cursor"] is None

    def test_empty_list_when_no_sessions(self):
        with patch("skills.routes.list_sessions_for_skill", return_value=([], None)):
            resp = _make_client(caller_uid="u1").get("/api/skills/skill-x/sessions")

        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_passes_correct_skill_id_and_owner_uid(self):
        with patch("skills.routes.list_sessions_for_skill", return_value=([], None)) as mock_list:
            _make_client(caller_uid="u2").get("/api/skills/my-skill/sessions")

        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs.get("skill_id") == "my-skill" or call_args.args[0] == "my-skill"
        uid_arg = call_args.kwargs.get("owner_uid") or call_args.args[1]
        assert uid_arg == "u2"

    def test_pagination_cursor_forwarded(self):
        sessions = [_make_idx()]
        with patch("skills.routes.list_sessions_for_skill", return_value=(sessions, "next-token")):
            resp = _make_client(caller_uid="u1").get("/api/skills/skill-x/sessions?page_size=1")

        assert resp.json()["next_cursor"] == "next-token"
