"""Shared machine threads: a supervisor replies in a machine principal's thread.

The reply must run as the thread's OWNER (the machine) — its skills, tools and
session — be labelled with the supervisor's name, and ping the owner's AILANG
inbox. A non-owner who is not a collaborator must not be able to post into
someone else's thread through either resumption shape (sessionId or threadId).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models.chat_session import ChatSessionIndex
from tests.api_tests.test_stream_skill import _fake_event_stream, _make_skill

MARK = User(uid="mark", email="mark@example.com", domain="example.com", display_name="Mark")
OUTSIDER = User(uid="eve", email="eve@example.com", domain="example.com", display_name="Eve")
DANEEL = User(
    uid="machine:daneel",
    email="daneel@machine.local",
    domain="machine.local",
    group_tags=frozenset({"daneel"}),
    auth_mode="machine",
    display_name="Daneel",
    supervisors=("mark@example.com",),
    notify_inbox="daneel",
)


def _thread(collaborators: list[str] | None = None, access_type: str = "private") -> ChatSessionIndex:
    now = datetime.now(UTC)
    return ChatSessionIndex(
        session_id="thread-daneel1",
        skill_id="test-skill-id",
        owner_uid="machine:daneel",
        access_control={"type": access_type},
        first_message_at=now,
        last_message_at=now,
        title="Cloud Run limits",
        collaborators=collaborators if collaborators is not None else ["mark@example.com"],
    )


@pytest.fixture()
def app():
    import fast_api_app as module

    return module.app


def _client_as(app, user: User) -> TestClient:
    async def _override(request: Request) -> User:
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides(app):
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _agui_body(text: str, thread_id: str = "thread-daneel1") -> dict:
    return {
        "threadId": thread_id,
        "runId": "run-1",
        "messages": [{"id": "m1", "role": "user", "content": text}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def test_collaborator_reply_runs_as_owner_and_is_labelled(app):
    captured = {}

    async def _capture(input_data):
        captured["message"] = input_data.messages[-1].content
        captured["state"] = dict(input_data.state or {})
        async for e in _fake_event_stream(input_data):
            yield e

    seen_users = []
    real = __import__("skills.skill_processor", fromlist=["process_skill_request"]).process_skill_request

    def _spy(**kwargs):
        seen_users.append(kwargs["user"])
        return real(**kwargs)

    skill = _make_skill(access_type="public")
    notify = AsyncMock(return_value=True)
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("db.chat_sessions.get_session_index", return_value=_thread()),
        patch("auth.firebase_auth.resolve_user_by_uid", return_value=DANEEL),
        patch("fast_api_app.process_skill_request", side_effect=_spy),
        patch("tools.ailang_messages.notify", notify),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capture),
    ):
        resp = _client_as(app, MARK).post("/api/skill/test-skill-id/stream", json=_agui_body("check the quota too"))

    assert resp.status_code == 200, resp.text
    assert seen_users and seen_users[0].uid == "machine:daneel"
    assert captured["message"] == "[Mark] check the quota too"
    assert captured["state"].get("turn_author") == "Mark"
    notify.assert_awaited_once()
    inbox, title, content = notify.await_args.args
    assert inbox == "daneel"
    assert "Mark" in title and "Cloud Run limits" in title
    assert "check the quota too" in content and "thread-daneel1" in content


def test_owner_turn_clears_turn_author_and_does_not_ping(app):
    captured = {}

    async def _capture(input_data):
        captured["message"] = input_data.messages[-1].content
        captured["state"] = dict(input_data.state or {})
        async for e in _fake_event_stream(input_data):
            yield e

    notify = AsyncMock(return_value=True)
    with (
        patch("skills.skill_processor.get_skill", return_value=_make_skill(access_type="public")),
        patch("db.chat_sessions.get_session_index", return_value=_thread()),
        patch("tools.ailang_messages.notify", notify),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capture),
    ):
        resp = _client_as(app, DANEEL).post("/api/skill/test-skill-id/stream", json=_agui_body("thanks"))

    assert resp.status_code == 200, resp.text
    assert captured["message"] == "thanks"
    assert captured["state"].get("turn_author") == ""
    notify.assert_not_awaited()


def test_non_collaborator_cannot_post_into_thread_by_thread_id(app):
    """Before this change a threadId pointing at someone else's thread ran as
    the caller. A non-collaborator gets a visible 403 now."""
    with (
        patch("skills.skill_processor.get_skill", return_value=_make_skill(access_type="public")),
        patch("db.chat_sessions.get_session_index", return_value=_thread(access_type="public")),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = _client_as(app, OUTSIDER).post("/api/skill/test-skill-id/stream", json=_agui_body("hi"))
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"]


def test_non_collaborator_without_access_is_refused(app):
    with (
        patch("skills.skill_processor.get_skill", return_value=_make_skill(access_type="public")),
        patch("db.chat_sessions.get_session_index", return_value=_thread()),
    ):
        resp = _client_as(app, OUTSIDER).post(
            "/api/skill/test-skill-id/stream", json={"message": "hi", "sessionId": "thread-daneel1"}
        )
    assert resp.status_code == 403


def test_collaborator_with_unknown_owner_gets_409(app):
    with (
        patch("skills.skill_processor.get_skill", return_value=_make_skill(access_type="public")),
        patch("db.chat_sessions.get_session_index", return_value=_thread()),
        patch("auth.firebase_auth.resolve_user_by_uid", return_value=None),
    ):
        resp = _client_as(app, MARK).post("/api/skill/test-skill-id/stream", json=_agui_body("hi"))
    assert resp.status_code == 409


def test_shared_list_returns_owner_name_and_can_reply(app):
    with (
        patch("protocols.sessions_route.list_sessions_shared_with", return_value=([_thread()], None)) as listed,
        patch("protocols.sessions_route.resolve_skill_labels", return_value={"test-skill-id": "GCP docs"}),
        patch("auth.firebase_auth.resolve_user_by_uid", return_value=DANEEL),
    ):
        resp = _client_as(app, MARK).get("/api/sessions?shared=true")
    assert resp.status_code == 200, resp.text
    listed.assert_called_once()
    assert listed.call_args.args[0] == "mark@example.com"
    (row,) = resp.json()["sessions"]
    assert row["owner_name"] == "Daneel"
    assert row["can_reply"] is True
    assert row["is_owner"] is False
    assert row["skill_label"] == "GCP docs"


def test_collaborator_can_read_messages_of_private_thread(app):
    """`collaborators` grants read even though the thread is private."""
    ctx = build_access_context(MARK)
    assert ctx.can_access(_thread())
    assert not build_access_context(OUTSIDER).can_access(_thread())
    assert ctx.is_collaborator(_thread())
    assert not ctx.is_collaborator(_thread(collaborators=[]))


def test_bootstrap_lets_collaborator_open_existing_thread(app):
    with patch("protocols.session_bootstrap_routes.get_session_index", return_value=_thread()):
        resp = _client_as(app, MARK).post("/api/sessions/thread-daneel1/bootstrap", json={"skill_id": "test-skill-id"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is False

    with patch("protocols.session_bootstrap_routes.get_session_index", return_value=_thread()):
        resp = _client_as(app, OUTSIDER).post(
            "/api/sessions/thread-daneel1/bootstrap", json={"skill_id": "test-skill-id"}
        )
    assert resp.status_code == 403


def test_new_machine_thread_is_stamped_with_supervisors():
    from skills import skill_processor

    with (
        patch("db.chat_sessions.get_session_index", return_value=None),
        patch("db.chat_sessions.create_session_index") as create,
    ):
        skill_processor._ensure_session_index(
            "thread-new", "test-skill-id", DANEEL.uid, None, DANEEL.email, collaborators=DANEEL.supervisors
        )
    assert create.call_args.kwargs["collaborators"] == ("mark@example.com",)


def test_shared_list_body_is_json(app):
    """Guard: the shared list never 500s for a user with no email."""
    nobody = User(uid="anon")
    resp = _client_as(app, nobody).get("/api/sessions?shared=true")
    assert resp.status_code == 200
    assert json.loads(resp.text)["sessions"] == []
