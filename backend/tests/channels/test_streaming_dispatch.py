"""Reachability tests for the streaming dispatch path (v6.21.0 Phase 1).

These exist because `DiscordChannel.send_streaming` shipped in May 2026
with 7 passing unit tests and **was never called by anything**:
`_dispatch_inbound` routed unconditionally through the collect-then-send
path, and `supports_streaming` was declared by four adapters and read by
zero. Unit-testing a method proves it works; it does not prove the
framework reaches it.

So every test here drives `_dispatch_inbound` — the real entry point —
rather than calling `send_streaming` directly.

See `docs/design/v6.21.0/channels-agui-convergence.md`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from channels._skill_invoke import GENERIC_ERROR_TEXT, NO_RESPONSE_TEXT
from channels.base import BaseChannel, InboundMessage, OutboundMessage

_INBOUND = InboundMessage(
    channel_user_id="u1",
    channel_chat_id="c1",
    text="hello",
)


class _CollectingChannel(BaseChannel):
    """Non-streaming adapter (the default) — captures `send` calls."""

    name = "collecting"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, OutboundMessage]] = []

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return True

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return _INBOUND

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        self.sent.append((chat_id, message))


class _StreamingChannel(_CollectingChannel):
    """Adapter that declares streaming AND overrides `send_streaming`."""

    name = "streaming"
    supports_streaming = True

    def __init__(self) -> None:
        super().__init__()
        self.streamed: list[dict[str, Any]] = []
        self.stream_chat_ids: list[str] = []

    async def send_streaming(
        self,
        chat_id: str,
        event_stream: AsyncIterator[dict[str, Any]],
    ) -> None:
        self.stream_chat_ids.append(chat_id)
        async for event in event_stream:
            self.streamed.append(event)


class _FlagOnlyChannel(_CollectingChannel):
    """Declares streaming but does NOT override `send_streaming`.

    Must degrade to collect-then-send rather than dropping the reply.
    """

    name = "flagonly"
    supports_streaming = True


def _events(seq: list[dict[str, Any]]):
    """Build a zero-arg factory returning an async iterator over `seq`."""

    async def _gen(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        for event in seq:
            yield event

    return _gen


def _patch_stream(seq: list[dict[str, Any]]):
    """Patch the skill event source with a canned AG-UI stream."""
    return patch("channels._skill_invoke.stream_skill_events", _events(seq))


def _patch_identity():
    """Bypass Firestore identity + skill selection in these unit tests."""
    return (
        patch("channels.identity.IdentityResolver.resolve", return_value="uid-1"),
        patch("channels.attachments.AttachmentPipeline.upload", return_value=[]),
        patch("channels.base.BaseChannel.select_skill", return_value="a-skill"),
    )


async def _dispatch(channel: BaseChannel, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Run `_dispatch_inbound` against a canned event stream."""
    ident, upload, select = _patch_identity()
    with _patch_stream(events), ident, upload, select:
        return await channel._dispatch_inbound(_INBOUND)


class TestStreamingReachability:
    """`supports_streaming` must actually route to `send_streaming`."""

    @pytest.mark.asyncio
    async def test_streaming_adapter_receives_raw_events(self) -> None:
        """THE regression: the flag routes dispatch to the streaming path."""
        channel = _StreamingChannel()
        result = await _dispatch(
            channel,
            [
                {"type": "RUN_STARTED"},
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"},
                {"type": "RUN_FINISHED"},
            ],
        )

        assert result == {"ok": True, "streamed": True}
        assert [e["type"] for e in channel.streamed] == [
            "RUN_STARTED",
            "TEXT_MESSAGE_CONTENT",
            "RUN_FINISHED",
        ]
        assert channel.stream_chat_ids == ["c1"]

    @pytest.mark.asyncio
    async def test_streaming_adapter_owns_delivery(self) -> None:
        """The framework must not also `send` — that would double-post."""
        channel = _StreamingChannel()
        await _dispatch(channel, [{"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}])

        assert channel.sent == []

    @pytest.mark.asyncio
    async def test_non_streaming_adapter_unchanged(self) -> None:
        """The default path still collects and sends exactly one message."""
        channel = _CollectingChannel()
        result = await _dispatch(
            channel,
            [
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "one "},
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "two"},
                {"type": "RUN_FINISHED"},
            ],
        )

        assert result == {"ok": True}
        assert len(channel.sent) == 1
        assert channel.sent[0][1].text == "one two"

    @pytest.mark.asyncio
    async def test_flag_without_override_degrades_to_send(self) -> None:
        """Declaring the flag without the method must not drop the reply."""
        channel = _FlagOnlyChannel()
        await _dispatch(channel, [{"type": "TEXT_MESSAGE_CONTENT", "delta": "still delivered"}])

        assert len(channel.sent) == 1
        assert channel.sent[0][1].text == "still delivered"


class TestErrorSurfacing:
    """CLAUDE.md #8 — a failed run is never silent, on any channel."""

    @pytest.mark.asyncio
    async def test_run_error_reaches_non_streaming_channel(self) -> None:
        """Telegram/WhatsApp/email inherit this with no adapter change."""
        channel = _CollectingChannel()
        await _dispatch(
            channel,
            [
                {"type": "RUN_STARTED"},
                {"type": "RUN_ERROR", "message": "model unavailable", "code": "503"},
            ],
        )

        text = channel.sent[0][1].text
        assert "model unavailable" in text
        assert text != NO_RESPONSE_TEXT

    @pytest.mark.asyncio
    async def test_error_is_distinguishable_from_empty(self) -> None:
        """The bug: both used to render as '(no response)'."""
        empty_channel = _CollectingChannel()
        await _dispatch(empty_channel, [{"type": "RUN_FINISHED"}])

        error_channel = _CollectingChannel()
        await _dispatch(error_channel, [{"type": "RUN_ERROR", "message": "boom"}])

        assert empty_channel.sent[0][1].text == NO_RESPONSE_TEXT
        assert error_channel.sent[0][1].text != empty_channel.sent[0][1].text

    @pytest.mark.asyncio
    async def test_error_without_message_still_visible(self) -> None:
        """A RUN_ERROR carrying no message must not degrade to silence."""
        channel = _CollectingChannel()
        await _dispatch(channel, [{"type": "RUN_ERROR"}])

        assert channel.sent[0][1].text == GENERIC_ERROR_TEXT

    @pytest.mark.asyncio
    async def test_error_wins_over_partial_text(self) -> None:
        """A truncated answer shown as complete is worse than a stated failure."""
        channel = _CollectingChannel()
        await _dispatch(
            channel,
            [
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "here is half an ans"},
                {"type": "RUN_ERROR", "message": "stream aborted"},
            ],
        )

        text = channel.sent[0][1].text
        assert "stream aborted" in text
        assert "here is half an ans" not in text


class TestSkillNotFound:
    """A missing skill arrives as a RUN_ERROR, not an exception."""

    @pytest.mark.asyncio
    async def test_missing_skill_renders_as_visible_text(self) -> None:
        from skills.skill_processor import SkillNotFoundError

        async def _raises(**_kwargs: Any):
            raise SkillNotFoundError("nope")
            yield  # pragma: no cover — makes this an async generator

        channel = _CollectingChannel()
        ident, upload, select = _patch_identity()
        with patch("skills.skill_processor.process_skill_request", _raises), ident, upload, select:
            await channel._dispatch_inbound(_INBOUND)

        text = channel.sent[0][1].text
        assert "not available" in text
        assert "/skills" in text
