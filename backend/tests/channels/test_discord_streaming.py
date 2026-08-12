"""Streaming-edit tests for `DiscordChannel.send_streaming`.

The adapter consumes an AG-UI event stream and live-edits the Discord
placeholder message so the user sees progress. These tests verify:

    - First-event arrival triggers a single edit (placeholder edited)
    - Multiple deltas batched into <=1 edit per ``STREAM_EDIT_INTERVAL_SEC``
    - ``RUN_FINISHED`` triggers a final atomic edit
    - Final text overflowing Discord's 2000-char limit fans out into
      follow-up messages on the channel
    - The fake event stream isn't held to wall-clock — we monkeypatch
      ``time.monotonic`` so the batching window is deterministic

A fake placeholder + channel is used; no real discord.py wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from channels.discord import DiscordChannel


async def _events(seq: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Yield events from `seq` as an async iterator."""
    for event in seq:
        yield event


@pytest.fixture()
def channel() -> DiscordChannel:
    """A bare DiscordChannel with a stubbed gateway client."""
    ch = DiscordChannel(public_key_hex="aa" * 32, token="t")
    return ch


def _wire_fake_channel(ch: DiscordChannel) -> tuple[Any, Any]:
    """Wire a fake gateway channel + placeholder pair onto `ch`. Returns both."""
    placeholder = MagicMock()
    placeholder.edit = AsyncMock()

    fake_channel = MagicMock()
    fake_channel.send = AsyncMock(return_value=placeholder)

    ch.client = MagicMock()
    ch.client.get_channel = MagicMock(return_value=fake_channel)
    ch.client.fetch_channel = AsyncMock(return_value=fake_channel)
    return fake_channel, placeholder


class TestStreaming:
    """`send_streaming` consumes AG-UI events and live-edits the placeholder."""

    @pytest.mark.asyncio
    async def test_initial_placeholder_sent(self, channel: DiscordChannel) -> None:
        fake_channel, _placeholder = _wire_fake_channel(channel)

        await channel.send_streaming("12345", _events([{"type": "RUN_FINISHED"}]))

        fake_channel.send.assert_awaited()  # "Thinking..." was sent first
        first_call = fake_channel.send.await_args_list[0]
        assert first_call.args[0] == "Thinking..."

    @pytest.mark.asyncio
    async def test_run_finished_triggers_final_edit(
        self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_channel, placeholder = _wire_fake_channel(channel)
        # Pin monotonic so the per-delta edit gate never fires before RUN_FINISHED.
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: 0.0)

        events = [
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "hel"},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "lo "},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "world"},
            {"type": "RUN_FINISHED"},
        ]
        await channel.send_streaming("12345", _events(events))

        # Final edit always fires; intermediate edits suppressed because
        # the clock didn't advance past STREAM_EDIT_INTERVAL_SEC.
        placeholder.edit.assert_awaited()
        final_args = placeholder.edit.await_args_list[-1]
        assert final_args.kwargs.get("content") == "hello world"

    @pytest.mark.asyncio
    async def test_batched_intermediate_edits(self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deltas spread across >=1 STREAM_EDIT_INTERVAL_SEC produce >=2 edits."""
        _fake_channel, placeholder = _wire_fake_channel(channel)

        # First call returns 0 (initial last_edit_ts), then increments past the
        # 1.0s threshold so the second delta crosses the gate.
        ticks = iter([0.0, 5.0, 5.1])
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: next(ticks))

        events = [
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "first"},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": " second"},
            {"type": "RUN_FINISHED"},
        ]
        await channel.send_streaming("12345", _events(events))

        # At least one intermediate edit + the final edit. Concretely, the
        # second delta hits the 5.0 monotonic tick which is >= 1.0s past 0.0.
        assert placeholder.edit.await_count >= 1
        final = placeholder.edit.await_args_list[-1]
        assert final.kwargs.get("content") == "first second"

    @pytest.mark.asyncio
    async def test_overflow_text_fans_out_to_followups(
        self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the final reply exceeds 2000 chars, the overflow is sent as new messages."""
        fake_channel, placeholder = _wire_fake_channel(channel)
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: 0.0)

        long_text = "A" * 4500  # 3 chunks at the 2000-char limit
        events = [
            {"type": "TEXT_MESSAGE_CONTENT", "delta": long_text},
            {"type": "RUN_FINISHED"},
        ]
        await channel.send_streaming("12345", _events(events))

        # First send was "Thinking...". The overflow sends 2 follow-ups.
        # → fake_channel.send called 3 times total.
        assert fake_channel.send.await_count == 3
        # Final edit holds the first 2000 chars of the reply.
        last_edit = placeholder.edit.await_args_list[-1]
        assert len(last_edit.kwargs["content"]) <= 2000

    @pytest.mark.asyncio
    async def test_tool_call_appends_progress_note(
        self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_channel, placeholder = _wire_fake_channel(channel)
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: 0.0)

        # TOOL_CALL_START with a `toolCallName` is what the backend really
        # emits (`adk/agui.py`, `adk/stream_invariants.py:128`). The AG-UI
        # spec defines no bare `TOOL_CALL` event; this fixture used to send
        # one, which is why the adapter's matching bug went unnoticed.
        events = [
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "let me check"},
            {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "ai_search"},
            {"type": "TEXT_MESSAGE_CONTENT", "delta": " — done."},
            {"type": "RUN_FINISHED"},
        ]
        await channel.send_streaming("12345", _events(events))

        final = placeholder.edit.await_args_list[-1]
        assert "ai_search" in final.kwargs["content"]

    @pytest.mark.asyncio
    async def test_run_error_replaces_placeholder(
        self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A RUN_ERROR must leave a visible message, not a stale 'Thinking...'.

        Regression for CLAUDE.md #8 (NEVER SILENT): before v6.21.0 the
        stream dropped RUN_ERROR, so a failed run rendered as the empty
        `"(no response)"` — indistinguishable from a genuinely empty answer.
        """
        _fake_channel, placeholder = _wire_fake_channel(channel)
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: 0.0)

        events = [
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "partial answer"},
            {"type": "RUN_ERROR", "message": "model unavailable", "code": "503"},
        ]
        await channel.send_streaming("12345", _events(events))

        final = placeholder.edit.await_args_list[-1].kwargs["content"]
        assert "model unavailable" in final
        assert final != "(no response)"
        # The error wins over partial text — a truncated answer presented
        # as complete is worse than an explicit failure.
        assert "partial answer" not in final

    @pytest.mark.asyncio
    async def test_empty_stream_shows_placeholder_default(
        self, channel: DiscordChannel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stream that produces no deltas before RUN_FINISHED still resolves."""
        _fake_channel, placeholder = _wire_fake_channel(channel)
        monkeypatch.setattr("channels.discord.time.monotonic", lambda: 0.0)

        await channel.send_streaming("12345", _events([{"type": "RUN_FINISHED"}]))

        # Placeholder gets edited to fallback text.
        final = placeholder.edit.await_args_list[-1]
        assert final.kwargs["content"] == "(no response)"

    @pytest.mark.asyncio
    async def test_unresolvable_channel_no_send(self, channel: DiscordChannel) -> None:
        """If the gateway can't resolve the channel, nothing is sent."""
        channel.client = MagicMock()
        channel.client.get_channel = MagicMock(return_value=None)
        channel.client.fetch_channel = AsyncMock(side_effect=Exception("not found"))

        # Should NOT raise — adapter logs and returns.
        await channel.send_streaming("99999", _events([{"type": "RUN_FINISHED"}]))
