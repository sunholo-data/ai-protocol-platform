"""GET /api/sessions/recent — the authenticated-landing resolver (v6.5.0 AUTH-LANDING).

Returns the caller's most-recent session whose skill is still visible + enabled,
or 204 when none qualify. Sessions whose skill was since hidden or dropped from
the tenant's enabled_skills are skipped so we never route a user into a chat
they can't open.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models import SkillConfig
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex


def _make_user() -> User:
    return User(uid="caller-uid", email="caller@yourcompany.com", domain="yourcompany.com")


def _session(session_id: str, skill_id: str, minutes_ago: int) -> ChatSessionIndex:
    ts = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC).replace(minute=minutes_ago)
    return ChatSessionIndex(
        sessionId=session_id,
        skillId=skill_id,
        ownerUid="caller-uid",
        accessControl=AccessControl(type="public"),
        firstMessageAt=ts,
        lastMessageAt=ts,
    )


def _skill(skill_id: str, slug: str) -> SkillConfig:
    return SkillConfig(
        skillId=skill_id,
        name=slug,
        description="A skill.",
        slug=slug,
        accessControl=AccessControl(type="public"),
    )


@pytest.fixture()
def app():
    import fast_api_app as module

    return module.app


@pytest.fixture()
def client(app):
    async def _override(request: Request) -> User:
        user = _make_user()
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_returns_newest_qualifying_session(client):
    with (
        mock.patch(
            "protocols.sessions_route.most_recent_session_for_user",
            return_value=[_session("s-new", "skill-a", 30)],
        ),
        mock.patch("skills.skill_config.get_skill", return_value=_skill("skill-a", "ppa-expert")),
        mock.patch("db.clients.resolve_enabled_skills", return_value=None),
    ):
        resp = client.get("/api/sessions/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s-new"
    assert body["skill_id"] == "skill-a"
    assert body["slug"] == "ppa-expert"


def test_204_when_no_sessions(client):
    with mock.patch("protocols.sessions_route.most_recent_session_for_user", return_value=[]):
        resp = client.get("/api/sessions/recent")
    assert resp.status_code == 204


def test_skips_session_whose_skill_is_not_enabled(client):
    # Newest session is for a skill no longer in enabled_skills; the next one is.
    sessions = [_session("s-disabled", "skill-x", 40), _session("s-ok", "skill-a", 20)]

    def _get_skill(sid):
        return {"skill-x": _skill("skill-x", "old-skill"), "skill-a": _skill("skill-a", "ppa-expert")}[sid]

    with (
        mock.patch("protocols.sessions_route.most_recent_session_for_user", return_value=sessions),
        mock.patch("skills.skill_config.get_skill", side_effect=_get_skill),
        mock.patch("db.clients.resolve_enabled_skills", return_value=["ppa-expert"]),
    ):
        resp = client.get("/api/sessions/recent")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s-ok"


def test_skips_session_whose_skill_was_deleted(client):
    with (
        mock.patch(
            "protocols.sessions_route.most_recent_session_for_user",
            return_value=[_session("s-gone", "skill-deleted", 10)],
        ),
        mock.patch("skills.skill_config.get_skill", return_value=None),
        mock.patch("db.clients.resolve_enabled_skills", return_value=None),
    ):
        resp = client.get("/api/sessions/recent")
    assert resp.status_code == 204
