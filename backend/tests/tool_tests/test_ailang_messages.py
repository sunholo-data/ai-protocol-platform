"""tools/ailang_messages.py — admin-allowlisted sends to AILANG inboxes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from tools import ailang_messages as am

SETTINGS = {"enabled": True, "from": "test-platform", "allowedInboxes": ["daneel"]}


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.content = b"{}" if body is not None else b""

    def json(self):
        return self._body


def _ctx(author: str = "", uid: str = "mark"):
    return SimpleNamespace(state={"turn_author": author}, _invocation_context=SimpleNamespace(user_id=uid))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AILANG_COORDINATOR_URL", "http://127.0.0.1:8765/")
    monkeypatch.setenv("AILANG_COORDINATOR_API_KEY", "k" * 40)
    am.clear_cache()
    yield
    am.clear_cache()


def _send(inbox="daneel", title="Hi", message="Body", ctx=None):
    return asyncio.run(am.send_ailang_message(inbox, title, message, ctx or _ctx("Mark")))


def test_sends_to_an_allowed_inbox_with_bearer_and_sender():
    with (
        patch.object(am, "load_settings", return_value=SETTINGS),
        patch("httpx.post", return_value=_Resp(201, {"message_id": "m-1"})) as post,
    ):
        out = _send()
    assert out == {"status": "sent", "inbox": "daneel", "messageId": "m-1"}
    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    assert url == "http://127.0.0.1:8765/api/messages"
    assert kwargs["headers"]["Authorization"] == "Bearer " + "k" * 40
    assert kwargs["json"]["inbox"] == "daneel"
    assert kwargs["json"]["from"] == "test-platform"
    assert "on behalf of Mark" in kwargs["json"]["content"]


def test_refuses_an_inbox_that_is_not_allowed():
    with patch.object(am, "load_settings", return_value=SETTINGS), patch("httpx.post") as post:
        out = _send(inbox="ailang-core")
    assert out["status"] == "error"
    assert "daneel" in out["error"]
    post.assert_not_called()


def test_disabled_or_missing_settings_fail_closed():
    for settings in ({}, {**SETTINGS, "enabled": False}):
        with patch.object(am, "load_settings", return_value=settings), patch("httpx.post") as post:
            out = _send()
        assert out["status"] == "error"
        post.assert_not_called()


def test_unconfigured_coordinator_is_reported(monkeypatch):
    monkeypatch.delenv("AILANG_COORDINATOR_URL")
    with patch.object(am, "load_settings", return_value=SETTINGS):
        out = _send()
    assert out == {"status": "error", "error": "AILANG messaging is not configured on this platform."}


def test_coordinator_refusal_and_outage_become_readable_errors():
    with patch.object(am, "load_settings", return_value=SETTINGS):
        with patch("httpx.post", return_value=_Resp(401)):
            assert "HTTP 401" in _send()["error"]
        with patch("httpx.post", side_effect=httpx.ConnectError("down")):
            assert "unreachable" in _send()["error"]


def test_notify_never_raises_and_reports_failure():
    with patch("httpx.post", side_effect=httpx.ConnectError("down")):
        assert asyncio.run(am.notify("daneel", "t", "c")) is False
    with patch("httpx.post", return_value=_Resp(201, {})):
        assert asyncio.run(am.notify("daneel", "t", "c")) is True
    assert asyncio.run(am.notify("", "t", "c")) is False


def test_tool_is_registered_and_in_catalog():
    from adk.tools import TOOL_REGISTRY, catalog_tool_names

    assert "ailang_messages" in TOOL_REGISTRY
    assert "ailang_messages" in catalog_tool_names()
