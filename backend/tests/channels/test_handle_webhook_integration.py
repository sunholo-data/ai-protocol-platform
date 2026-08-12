"""End-to-end integration test for the channel framework.

This is the test that proves the framework's contract: register a
channel, mount the registry on a FastAPI app, POST to the resulting
`/api/{name}/webhook` route, and verify the request flows through every
layer (verify → parse → identity → command/skill → send) without any
adapter-specific glue.

If this test passes, M1 has met its primary acceptance criterion. The
contract is the test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels.base import BaseChannel, InboundMessage, OutboundMessage
from channels.registry import ChannelRegistry


class IntegrationMockChannel(BaseChannel):
    """A channel that records every framework call it sees."""

    name = "imock"

    def __init__(self) -> None:
        super().__init__()
        self.verify_calls: list[tuple[dict, bytes]] = []
        self.parse_calls: list[dict] = []
        self.sent: list[tuple[str, OutboundMessage]] = []

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        self.verify_calls.append((headers, body))
        # Reject if `X-Test-Reject: 1` header is set — used by one test below
        return headers.get("x-test-reject") != "1"

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        self.parse_calls.append(payload)
        # Empty payload short-circuits (non-actionable event)
        if not payload.get("text"):
            return None
        return InboundMessage(
            channel_user_id=payload.get("user", "anon"),
            channel_chat_id=payload.get("chat", "default-chat"),
            text=payload["text"],
        )

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        self.sent.append((chat_id, message))


@pytest.fixture(autouse=True)
def _isolate_registry():
    ChannelRegistry._clear_for_tests()
    yield
    ChannelRegistry._clear_for_tests()


@pytest.fixture()
def app_with_channel():
    """Mount IntegrationMockChannel onto a fresh FastAPI app + return both."""
    channel = IntegrationMockChannel()
    ChannelRegistry.register(channel)
    app = FastAPI()
    ChannelRegistry.mount_webhooks(app)
    return app, channel


class TestEndToEndHappyPath:
    """The contract test: full skill-dispatch round-trip through a registered channel."""

    def test_plain_message_round_trip(self, app_with_channel) -> None:
        app, channel = app_with_channel
        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value="firebase-uid-1")),
            patch("channels.base.AttachmentPipeline.upload", AsyncMock(return_value=[])),
            patch(
                "channels._default_skill.get_user_default_skill",
                AsyncMock(return_value="general-assistant"),
            ),
            patch(
                "channels._skill_invoke.invoke_skill_collected",
                AsyncMock(return_value="hello back!"),
            ),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/imock/webhook",
                    json={"user": "user-1", "chat": "chat-1", "text": "hi bot"},
                )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Framework called every layer in the right order.
        assert len(channel.verify_calls) == 1
        assert len(channel.parse_calls) == 1
        assert channel.parse_calls[0]["text"] == "hi bot"

        # Reply was sent through the adapter's `send`.
        assert len(channel.sent) == 1
        chat_id, msg = channel.sent[0]
        assert chat_id == "chat-1"
        assert msg.text == "hello back!"

    def test_unknown_user_auto_create_then_invoke(self, app_with_channel) -> None:
        """Default `on_unknown_user` auto-creates a mapping and the message proceeds."""
        app, channel = app_with_channel
        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value=None)),
            patch(
                "channels.base.IdentityResolver.auto_create",
                AsyncMock(return_value="firebase-uid-auto"),
            ),
            patch("channels.base.AttachmentPipeline.upload", AsyncMock(return_value=[])),
            patch(
                "channels._default_skill.get_user_default_skill",
                AsyncMock(return_value="general-assistant"),
            ),
            patch(
                "channels._skill_invoke.invoke_skill_collected",
                AsyncMock(return_value="welcome!"),
            ),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/imock/webhook",
                    json={"user": "first-time", "chat": "c", "text": "hi"},
                )

        assert resp.status_code == 200
        assert channel.sent[0][1].text == "welcome!"


class TestEndToEndCommands:
    def test_skill_command_sends_ack_no_skill_invoke(self, app_with_channel) -> None:
        app, channel = app_with_channel
        invoke_mock = AsyncMock()
        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value="uid")),
            patch(
                "channels._commands_runtime.execute_command",
                AsyncMock(return_value="Switched to skill: foo"),
            ),
            patch("channels._skill_invoke.invoke_skill_collected", invoke_mock),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/imock/webhook",
                    json={"user": "u", "chat": "c", "text": "/skill foo"},
                )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "command": "skill"}
        assert invoke_mock.await_count == 0  # skill was NOT invoked
        assert channel.sent[0][1].text == "Switched to skill: foo"


class TestEndToEndErrorPaths:
    def test_verify_failure_returns_401(self, app_with_channel) -> None:
        app, _channel = app_with_channel
        with TestClient(app) as client:
            resp = client.post(
                "/api/imock/webhook",
                json={"user": "u", "chat": "c", "text": "hi"},
                headers={"X-Test-Reject": "1"},
            )
        assert resp.status_code == 401

    def test_non_actionable_event_skips(self, app_with_channel) -> None:
        app, channel = app_with_channel
        # Empty text → parse_inbound returns None → framework short-circuits.
        with TestClient(app) as client:
            resp = client.post("/api/imock/webhook", json={"text": ""})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "skipped": True}
        assert channel.sent == []

    def test_unknown_user_rejection(self, app_with_channel) -> None:
        """When `on_unknown_user` returns None (rejection), framework reports rejected."""
        app, channel = app_with_channel
        channel.on_unknown_user = AsyncMock(return_value=None)  # type: ignore[method-assign]
        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value=None)),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/imock/webhook",
                    json={"user": "blocked", "chat": "c", "text": "hi"},
                )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "rejected": "unknown_user"}
        assert channel.sent == []  # no reply to rejected users


class TestMultipleChannels:
    """Registry routes correctly when multiple channels are mounted."""

    def test_two_channels_routed_independently(self) -> None:
        ch_a = IntegrationMockChannel()
        # Make second channel under a different name (subclass to avoid name collision)

        class SecondChannel(IntegrationMockChannel):
            name = "imock2"

        ch_b = SecondChannel()

        ChannelRegistry.register(ch_a)
        ChannelRegistry.register(ch_b)
        app = FastAPI()
        ChannelRegistry.mount_webhooks(app)

        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value="uid")),
            patch("channels.base.AttachmentPipeline.upload", AsyncMock(return_value=[])),
            patch(
                "channels._default_skill.get_user_default_skill",
                AsyncMock(return_value="general-assistant"),
            ),
            patch("channels._skill_invoke.invoke_skill_collected", AsyncMock(return_value="r")),
        ):
            with TestClient(app) as client:
                client.post("/api/imock/webhook", json={"user": "u", "chat": "c", "text": "x"})
                client.post("/api/imock2/webhook", json={"user": "u", "chat": "c", "text": "y"})

        # Each channel saw exactly its own request — no cross-routing.
        assert len(ch_a.parse_calls) == 1
        assert ch_a.parse_calls[0]["text"] == "x"
        assert len(ch_b.parse_calls) == 1
        assert ch_b.parse_calls[0]["text"] == "y"
