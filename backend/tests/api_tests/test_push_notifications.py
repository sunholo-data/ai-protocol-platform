"""Web Push notifications (docs/design/v6.40.0/web-push-notifications.md).

- the payload carries names and a link, never message content
- subscriptions are stored per uid, found by email, pruned when gone
- /api/push routes: off without VAPID keys, NEVER SILENT errors
- AGUI_DETACH_ON_DISCONNECT: a turn whose client disconnects keeps running to
  its terminal event, then notifies; without the flag nothing changes
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from ag_ui.core import (
    EventType,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models import SkillConfig, SkillMetadata
from notifications import hooks, store

VAPID = {
    "VAPID_PUBLIC_KEY": "BPublicKeyForTests",
    "VAPID_PRIVATE_KEY": "private-key-for-tests",
    "VAPID_SUBJECT": "mailto:test@example.com",
}


@pytest.fixture()
def vapid(monkeypatch):
    for k, v in VAPID.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def fake_store(monkeypatch):
    """An in-memory stand-in for the three Firestore helpers store.py uses."""
    docs: dict[str, dict] = {}

    def get_document(_c, doc_id):
        return json.loads(json.dumps(docs[doc_id])) if doc_id in docs else None

    def set_document(_c, doc_id, data, merge=False):
        docs[doc_id] = json.loads(json.dumps(data))

    def query_documents(_c, filters=None, **_kw):
        (field, _op, value), *_ = filters
        return [{**d, "__id": k} for k, d in docs.items() if d.get(field) == value]

    monkeypatch.setattr(store, "get_document", get_document)
    monkeypatch.setattr(store, "set_document", set_document)
    monkeypatch.setattr(store, "query_documents", query_documents)
    return docs


SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}


# --- store ---


def test_store_adds_dedupes_finds_by_email_and_removes(fake_store):
    store.add("u1", "Mark@Example.com", SUB, "Safari")
    store.add("u1", "mark@example.com", SUB, "Safari")  # same endpoint: replaced, not duplicated
    assert len(store.for_uid("u1")) == 1
    assert [uid for uid, _ in store.for_email("mark@example.com")] == ["u1"]
    store.remove("u1", SUB["endpoint"])
    assert store.for_uid("u1") == []


# --- hooks: privacy + delivery ---


async def _drain_hooks():
    while hooks._tasks:
        await asyncio.gather(*list(hooks._tasks))


@pytest.mark.asyncio
async def test_reply_ready_payload_is_names_and_a_link_only(vapid, fake_store):
    store.add("u1", "m@example.com", SUB)
    sent: list[dict] = []

    async def fake_send(_sub, message):
        sent.append(message)
        return "ok"

    with (
        patch.object(hooks, "send", fake_send),
        patch.object(hooks, "_skill_name", return_value=("Web Researcher", "skill-uuid")),
    ):
        hooks.notify_reply_ready("u1", "web-researcher", "sess-1")
        await _drain_hooks()

    assert sent == [
        {
            "title": "Reply ready",
            "body": "in Web Researcher",
            "url": "/chat/skill-uuid?session=sess-1",
            "tag": "session-sess-1",
        }
    ]


@pytest.mark.asyncio
async def test_gone_subscription_is_pruned(vapid, fake_store):
    store.add("u1", "m@example.com", SUB)

    async def gone(_sub, _message):
        return "gone"

    with patch.object(hooks, "send", gone), patch.object(hooks, "_skill_name", return_value=("S", "s")):
        hooks.notify_reply_ready("u1", "s", "sess-1")
        await _drain_hooks()
    assert store.for_uid("u1") == []


@pytest.mark.asyncio
async def test_thread_collaborators_are_notified_by_email(vapid, fake_store):
    store.add("u-mark", "m@example.com", SUB)

    from types import SimpleNamespace

    index = SimpleNamespace(collaborators=["m@example.com"])

    sent: list[dict] = []

    async def fake_send(_sub, message):
        sent.append(message)
        return "ok"

    with (
        patch("db.chat_sessions.get_session_index", return_value=index),
        patch.object(hooks, "send", fake_send),
        patch.object(hooks, "_skill_name", return_value=("Sunholo", "greeter-uuid")),
    ):
        hooks.notify_thread_collaborators("Daneel", "sunholo-greeter", "sess-9")
        await _drain_hooks()
    assert sent and sent[0]["title"] == "Daneel posted" and sent[0]["body"] == "in Sunholo"


def test_hooks_do_nothing_without_vapid_keys(monkeypatch, fake_store):
    for k in VAPID:
        monkeypatch.delenv(k, raising=False)
    store.add("u1", "m@example.com", SUB)
    hooks.notify_reply_ready("u1", "s", "sess")  # no running loop needed: returns before scheduling
    assert not hooks._tasks


# --- routes ---


def _user() -> User:
    return User(uid="caller-uid", email="caller@yourcompany.com", domain="yourcompany.com")


@pytest.fixture()
def client():
    import fast_api_app as module

    async def _override(request: Request) -> User:
        user = _user()
        request.state.access = build_access_context(user)
        return user

    module.app.dependency_overrides[get_current_user] = _override
    yield TestClient(module.app)
    module.app.dependency_overrides.pop(get_current_user, None)


def test_config_is_off_without_keys(client, monkeypatch):
    for k in VAPID:
        monkeypatch.delenv(k, raising=False)
    assert client.get("/api/push/config").json() == {"enabled": False, "publicKey": None}
    assert client.post("/api/push/subscribe", json={"subscription": SUB}).status_code == 409


def test_subscribe_then_test_push(client, vapid, fake_store):
    assert client.get("/api/push/config").json() == {"enabled": True, "publicKey": VAPID["VAPID_PUBLIC_KEY"]}
    # NEVER SILENT: testing before subscribing says why it can't.
    assert client.post("/api/push/test").status_code == 404
    assert client.post("/api/push/subscribe", json={"subscription": SUB}).status_code == 204
    assert store.for_uid("caller-uid")[0]["endpoint"] == SUB["endpoint"]

    async def ok(_sub, _message):
        return "ok"

    with patch("notifications.routes.send", ok):
        assert client.post("/api/push/test").status_code == 204
    assert client.request("DELETE", "/api/push/subscribe", json={"endpoint": SUB["endpoint"]}).status_code == 204
    assert store.for_uid("caller-uid") == []


# --- detached runs ---


def _skill() -> SkillConfig:
    return SkillConfig(
        name="test-skill",
        description="Under test.",
        instructions="Be helpful.",
        skillId="test-skill-id",
        ownerId="someone-else",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl={"type": "public"},
    )


def _slow_stream(gate: asyncio.Event):
    async def _run(input_data) -> AsyncGenerator:
        yield RunStartedEvent(type=EventType.RUN_STARTED, thread_id=input_data.thread_id, run_id=input_data.run_id)
        yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id="m1", role="assistant")
        await gate.wait()  # the client "suspends" while the model is still working
        yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="done")
        yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")
        yield RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=input_data.thread_id, run_id=input_data.run_id)

    return _run


async def _open_stream(module):
    user = _user()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/skill/test-skill-id/stream",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    request.state.access = build_access_context(user)
    body = module._StreamSkillRequest(message="hi", threadId="sess-42")
    return await module.stream_skill("test-skill-id", body, request, user)


@pytest.mark.asyncio
async def test_detached_run_finishes_after_disconnect_and_notifies(monkeypatch):
    import fast_api_app as module

    monkeypatch.setenv("AGUI_DETACH_ON_DISCONNECT", "1")
    gate = asyncio.Event()
    calls: list[tuple] = []
    with (
        patch("skills.skill_processor.get_skill", return_value=_skill()),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_slow_stream(gate)),
        patch("notifications.hooks.notify_reply_ready", side_effect=lambda *a, **k: calls.append((a, k))),
    ):
        resp = await _open_stream(module)
        it = resp.body_iterator
        first = await it.__anext__()
        assert "RUN_STARTED" in first
        await it.aclose()  # the phone suspends the app: the client is gone
        assert calls == []  # nothing yet — the run is still going
        gate.set()
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
    assert calls, "the run did not finish (or notify) after the client disconnected"
    (uid, skill_id, session_id), kwargs = calls[0]
    assert (uid, skill_id, session_id) == ("caller-uid", "test-skill-id", "sess-42")
    assert kwargs == {"failed": False}


@pytest.mark.asyncio
async def test_detached_run_that_the_client_watched_sends_no_push(monkeypatch):
    import fast_api_app as module

    monkeypatch.setenv("AGUI_DETACH_ON_DISCONNECT", "1")
    gate = asyncio.Event()
    gate.set()
    calls: list[tuple] = []
    with (
        patch("skills.skill_processor.get_skill", return_value=_skill()),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_slow_stream(gate)),
        patch("notifications.hooks.notify_reply_ready", side_effect=lambda *a, **k: calls.append((a, k))),
    ):
        resp = await _open_stream(module)
        frames = [f async for f in resp.body_iterator]
    assert any("RUN_FINISHED" in f for f in frames)
    await asyncio.sleep(0.05)
    assert calls == []
