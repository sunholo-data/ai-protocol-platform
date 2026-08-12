"""Unit tests for `channels.base.BaseChannel`.

The ABC is exercised through a `MockChannel` that captures every send
and every framework call so each branch of `handle_webhook` can be
isolated. Tests live here (rather than in the integration file) because
they validate the ABC contract itself — adapter subclasses inherit
this behaviour for free.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from channels.base import BaseChannel, InboundMessage, OutboundMessage


class MockChannel(BaseChannel):
    """Test double — captures `send` calls, lets tests stub the abstract methods."""

    name = "mock"

    def __init__(self, *, verify=True, parse_result=None) -> None:
        super().__init__()
        self._verify_ok = verify
        self._parse_result = parse_result
        self.sent: list[tuple[str, OutboundMessage]] = []

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return self._verify_ok

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return self._parse_result

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        self.sent.append((chat_id, message))


# Default stubs used by every test that doesn't care about a specific layer.
_STUB_IDENTITY_HIT = AsyncMock(return_value="firebase-uid-1")
_STUB_IDENTITY_MISS = AsyncMock(return_value=None)
_STUB_ATTACHMENTS_NONE = AsyncMock(return_value=[])
_STUB_INVOKE_RESPONSE = AsyncMock(return_value="bot reply")


class TestSubclassRequirements:
    """ABC contract: name must be set; abstract methods must be implemented."""

    def test_abstract_class_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            BaseChannel()  # type: ignore[abstract]

    def test_subclass_without_name_raises(self) -> None:
        class NoName(BaseChannel):
            async def verify_webhook(self, headers, body):
                return True

            async def parse_inbound(self, payload):
                return None

            async def send(self, chat_id, message):
                pass

        with pytest.raises(ValueError, match="must set the `name`"):
            NoName()


class TestVerifyFailure:
    """`handle_webhook` raises 401 when `verify_webhook` returns False."""

    @pytest.mark.asyncio
    async def test_verify_failure_raises_401(self) -> None:
        ch = MockChannel(verify=False)
        with pytest.raises(HTTPException) as exc:
            await ch.handle_webhook({}, {}, b"")
        assert exc.value.status_code == 401


class TestNonActionableEvent:
    """`parse_inbound` returning None short-circuits to {'ok': True, 'skipped': True}."""

    @pytest.mark.asyncio
    async def test_none_parse_skips_silently(self) -> None:
        ch = MockChannel(parse_result=None)
        result = await ch.handle_webhook({}, {}, b"")
        assert result == {"ok": True, "skipped": True}
        assert ch.sent == []


class TestUnknownUserRejection:
    """When IdentityResolver misses AND on_unknown_user returns None → rejected."""

    @pytest.mark.asyncio
    async def test_unknown_user_returns_rejected(self) -> None:
        inbound = InboundMessage(
            channel_user_id="never-seen",
            channel_chat_id="chat-1",
            text="hello",
        )
        ch = MockChannel(parse_result=inbound)
        # Override on_unknown_user to reject (Discord-style allowlist).
        ch.on_unknown_user = AsyncMock(return_value=None)  # type: ignore[method-assign]

        with patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_MISS):
            result = await ch.handle_webhook({}, {}, b"")

        assert result == {"ok": True, "rejected": "unknown_user"}
        assert ch.sent == []  # rejected user gets no reply


class TestSkillDispatchPath:
    """Full happy path: known user, no command, plain message → skill invoked."""

    @pytest.mark.asyncio
    async def test_plain_message_invokes_skill_and_sends_reply(self) -> None:
        inbound = InboundMessage(
            channel_user_id="user-1",
            channel_chat_id="chat-1",
            text="hello bot",
        )
        ch = MockChannel(parse_result=inbound)

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels.base.AttachmentPipeline.upload", _STUB_ATTACHMENTS_NONE),
            patch.object(ch, "select_skill", AsyncMock(return_value="general-assistant")),
            patch("channels._skill_invoke.invoke_skill_collected", _STUB_INVOKE_RESPONSE),
        ):
            result = await ch.handle_webhook({}, {}, b"")

        assert result == {"ok": True}
        assert len(ch.sent) == 1
        chat_id, msg = ch.sent[0]
        assert chat_id == "chat-1"
        assert msg.text == "bot reply"

    @pytest.mark.asyncio
    async def test_inbound_metadata_is_forwarded_into_outbound(self) -> None:
        """Reply-threading depends on this: subject + Message-Id from the inbound
        must reach the adapter's `send()` via `OutboundMessage.metadata` so
        channels like email can set `In-Reply-To` without the caller passing
        metadata explicitly. Regression guard for the M3 gap fix.
        """
        inbound = InboundMessage(
            channel_user_id="user-1",
            channel_chat_id="chat-1",
            text="hello",
            metadata={"subject": "Re: prev", "in_reply_to": "<msg-9@example.com>"},
        )
        ch = MockChannel(parse_result=inbound)

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels.base.AttachmentPipeline.upload", _STUB_ATTACHMENTS_NONE),
            patch.object(ch, "select_skill", AsyncMock(return_value="general-assistant")),
            patch("channels._skill_invoke.invoke_skill_collected", _STUB_INVOKE_RESPONSE),
        ):
            await ch.handle_webhook({}, {}, b"")

        _chat_id, outbound = ch.sent[0]
        assert outbound.metadata == {"subject": "Re: prev", "in_reply_to": "<msg-9@example.com>"}

    @pytest.mark.asyncio
    async def test_dispatch_inbound_can_be_called_directly_by_adapters(self) -> None:
        """Non-webhook transports (Discord gateway, Slack RTM) build an
        `InboundMessage` themselves and call `_dispatch_inbound` to share
        the downstream identity → command → skill → send path with
        `handle_webhook`. Regression guard for the M2 gap fix.
        """
        inbound = InboundMessage(
            channel_user_id="gateway-user",
            channel_chat_id="thread-1",
            text="from the gateway",
        )
        ch = MockChannel(parse_result=None)  # parse_inbound NOT called here

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels.base.AttachmentPipeline.upload", _STUB_ATTACHMENTS_NONE),
            patch.object(ch, "select_skill", AsyncMock(return_value="general-assistant")),
            patch("channels._skill_invoke.invoke_skill_collected", _STUB_INVOKE_RESPONSE),
        ):
            result = await ch._dispatch_inbound(inbound)

        assert result == {"ok": True}
        assert len(ch.sent) == 1
        assert ch.sent[0][0] == "thread-1"
        assert ch.sent[0][1].text == "bot reply"

    @pytest.mark.asyncio
    async def test_fallback_to_general_assistant_when_select_skill_returns_none(self) -> None:
        inbound = InboundMessage(channel_user_id="u", channel_chat_id="c", text="hi")
        ch = MockChannel(parse_result=inbound)
        captured_skill: list[str] = []

        async def fake_invoke(*, skill_id, **_kwargs):
            captured_skill.append(skill_id)
            return "ok"

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels.base.AttachmentPipeline.upload", _STUB_ATTACHMENTS_NONE),
            patch.object(ch, "select_skill", AsyncMock(return_value=None)),
            patch("channels._skill_invoke.invoke_skill_collected", side_effect=fake_invoke),
        ):
            await ch.handle_webhook({}, {}, b"")

        assert captured_skill == ["general-assistant"]


class TestCommandPath:
    """Commands are parsed before skill dispatch — `/skill foo` doesn't invoke a skill."""

    @pytest.mark.asyncio
    async def test_slash_skill_does_not_invoke_skill(self) -> None:
        inbound = InboundMessage(
            channel_user_id="u",
            channel_chat_id="c",
            text="/skill general-assistant",
        )
        ch = MockChannel(parse_result=inbound)
        invoke_mock = AsyncMock()

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels._commands_runtime.execute_command", AsyncMock(return_value="ack")),
            patch("channels._skill_invoke.invoke_skill_collected", invoke_mock),
        ):
            result = await ch.handle_webhook({}, {}, b"")

        assert result == {"ok": True, "command": "skill"}
        assert invoke_mock.await_count == 0  # skill not invoked
        # Command result was sent back as a reply
        assert len(ch.sent) == 1
        assert ch.sent[0][1].text == "ack"

    @pytest.mark.asyncio
    async def test_help_command_short_circuits(self) -> None:
        inbound = InboundMessage(channel_user_id="u", channel_chat_id="c", text="/help")
        ch = MockChannel(parse_result=inbound)

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels._commands_runtime.execute_command", AsyncMock(return_value="help text")),
        ):
            result = await ch.handle_webhook({}, {}, b"")

        assert result["command"] == "help"
        assert ch.sent[0][1].text == "help text"


class TestEmailBracketSelector:
    """Email's `[Skill] body` uses BRACKET_PREFIX — confirmed via override path."""

    @pytest.mark.asyncio
    async def test_bracket_prefix_routes_to_command_dispatch(self) -> None:
        class BracketChannel(MockChannel):
            command_prefix = "["

        inbound = InboundMessage(
            channel_user_id="u",
            channel_chat_id="c",
            text="[Doc Analyst] summarise",
        )
        ch = BracketChannel(parse_result=inbound)

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels._commands_runtime.execute_command", AsyncMock(return_value="switched")),
        ):
            result = await ch.handle_webhook({}, {}, b"")

        assert result["command"] == "skill"


class TestAttachmentForwarding:
    """Inbound attachments are forwarded to AttachmentPipeline and the doc IDs reach the skill."""

    @pytest.mark.asyncio
    async def test_attachments_become_document_ids(self) -> None:
        from channels.base import Attachment

        att = Attachment(url="http://x", filename="report.pdf", size_bytes=100)
        inbound = InboundMessage(
            channel_user_id="u",
            channel_chat_id="c",
            text="please read this",
            attachments=[att],
        )
        ch = MockChannel(parse_result=inbound)
        captured: dict[str, Any] = {}

        async def fake_invoke(**kwargs):
            captured.update(kwargs)
            return "ok"

        with (
            patch("channels.base.IdentityResolver.resolve", _STUB_IDENTITY_HIT),
            patch("channels.base.AttachmentPipeline.upload", AsyncMock(return_value=["doc-123"])),
            patch.object(ch, "select_skill", AsyncMock(return_value="general-assistant")),
            patch("channels._skill_invoke.invoke_skill_collected", side_effect=fake_invoke),
        ):
            await ch.handle_webhook({}, {}, b"")

        assert captured["attachment_ids"] == ["doc-123"]
