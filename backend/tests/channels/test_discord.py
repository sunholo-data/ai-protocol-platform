"""Unit tests for `channels.discord.DiscordChannel`.

The Discord adapter is a `BaseChannel` subclass — these tests verify the
adapter-specific bits the framework doesn't cover:

    - `verify_webhook` — Ed25519 signature verification (happy + reject)
    - `parse_inbound` — slash command interaction shape (type=2) plus
      non-actionable interactions (ping=1, component=3) → None
    - `send` — Discord 2000-char chunking using a mock client
    - `on_unknown_user` — Firestore `channel_routes/discord/{guild_id}`
      allowlist hit + miss

The framework integration (verify-then-parse-then-dispatch) is already
proven by `tests/channels/test_base.py` and `test_handle_webhook_integration.py`.
We don't re-test that here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import nacl.signing
import pytest

from channels.base import OutboundMessage
from channels.discord import DiscordChannel

# --- shared fixtures ------------------------------------------------------


@pytest.fixture()
def signing_key() -> nacl.signing.SigningKey:
    """Stable signing key for Ed25519 verify tests."""
    return nacl.signing.SigningKey.generate()


@pytest.fixture()
def discord_channel(signing_key: nacl.signing.SigningKey) -> DiscordChannel:
    """A DiscordChannel wired to the test signing key's verify_key."""
    verify_key_hex = signing_key.verify_key.encode().hex()
    return DiscordChannel(public_key_hex=verify_key_hex, token="test-token")


# --- verify_webhook -------------------------------------------------------


class TestVerifyWebhook:
    """Ed25519 signature verification for slash-command interactions."""

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(
        self,
        discord_channel: DiscordChannel,
        signing_key: nacl.signing.SigningKey,
    ) -> None:
        body = b'{"type":1}'
        timestamp = "1700000000"
        signature = signing_key.sign(timestamp.encode() + body).signature.hex()
        headers = {
            "x-signature-ed25519": signature,
            "x-signature-timestamp": timestamp,
        }
        assert await discord_channel.verify_webhook(headers, body) is True

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self, discord_channel: DiscordChannel) -> None:
        body = b'{"type":1}'
        headers = {
            "x-signature-ed25519": "00" * 64,
            "x-signature-timestamp": "1700000000",
        }
        assert await discord_channel.verify_webhook(headers, body) is False

    @pytest.mark.asyncio
    async def test_missing_headers_rejected(self, discord_channel: DiscordChannel) -> None:
        assert await discord_channel.verify_webhook({}, b"") is False
        # Missing timestamp
        assert await discord_channel.verify_webhook({"x-signature-ed25519": "00" * 64}, b"") is False

    @pytest.mark.asyncio
    async def test_malformed_signature_hex_rejected(self, discord_channel: DiscordChannel) -> None:
        # Non-hex value must reject cleanly, not raise.
        headers = {
            "x-signature-ed25519": "not-hex-at-all",
            "x-signature-timestamp": "1700000000",
        }
        assert await discord_channel.verify_webhook(headers, b"body") is False


# --- parse_inbound --------------------------------------------------------


class TestParseInbound:
    """Slash-command interaction payload → InboundMessage; non-actionable → None."""

    @pytest.mark.asyncio
    async def test_application_command_returns_inbound(self, discord_channel: DiscordChannel) -> None:
        payload = {
            "type": 2,  # APPLICATION_COMMAND
            "id": "interaction-id",
            "token": "interaction-token-xyz",
            "channel_id": "channel-42",
            "guild_id": "guild-7",
            "member": {"user": {"id": "user-1001", "username": "alice"}},
            "data": {
                "name": "ask",
                "options": [{"name": "question", "value": "what's up?"}],
            },
        }
        inbound = await discord_channel.parse_inbound(payload)
        assert inbound is not None
        assert inbound.channel_user_id == "user-1001"
        assert inbound.channel_chat_id == "channel-42"
        assert inbound.text == "what's up?"
        assert inbound.metadata == {
            "guild_id": "guild-7",
            "interaction_token": "interaction-token-xyz",
            "command": "ask",
        }

    @pytest.mark.asyncio
    async def test_ping_type_returns_none(self, discord_channel: DiscordChannel) -> None:
        # type=1 is PING (Discord uses this to verify the endpoint at registration).
        assert await discord_channel.parse_inbound({"type": 1}) is None

    @pytest.mark.asyncio
    async def test_message_component_returns_none(self, discord_channel: DiscordChannel) -> None:
        # type=3 is MESSAGE_COMPONENT (button click etc.); not actionable here.
        assert await discord_channel.parse_inbound({"type": 3, "data": {}}) is None

    @pytest.mark.asyncio
    async def test_dm_payload_uses_user_field(self, discord_channel: DiscordChannel) -> None:
        """DM interactions have `user` instead of `member.user`."""
        payload = {
            "type": 2,
            "id": "i",
            "token": "tok",
            "channel_id": "dm-99",
            "user": {"id": "user-1002"},
            "data": {
                "name": "ask",
                "options": [{"name": "question", "value": "hello"}],
            },
        }
        inbound = await discord_channel.parse_inbound(payload)
        assert inbound is not None
        assert inbound.channel_user_id == "user-1002"
        assert inbound.metadata["guild_id"] is None

    @pytest.mark.asyncio
    async def test_slash_skill_command_maps_to_framework(self, discord_channel: DiscordChannel) -> None:
        """`/skill <slug>` slash command maps to the framework's `/skill` text."""
        payload = {
            "type": 2,
            "id": "i",
            "token": "tok",
            "channel_id": "c",
            "guild_id": "g",
            "member": {"user": {"id": "u"}},
            "data": {
                "name": "skill",
                "options": [{"name": "name", "value": "general-assistant"}],
            },
        }
        inbound = await discord_channel.parse_inbound(payload)
        assert inbound is not None
        # Maps slash `/skill general-assistant` to framework command text.
        assert inbound.text == "/skill general-assistant"

    @pytest.mark.asyncio
    async def test_slash_skills_command_maps_to_framework(self, discord_channel: DiscordChannel) -> None:
        payload = {
            "type": 2,
            "id": "i",
            "token": "t",
            "channel_id": "c",
            "guild_id": "g",
            "member": {"user": {"id": "u"}},
            "data": {"name": "skills"},
        }
        inbound = await discord_channel.parse_inbound(payload)
        assert inbound is not None
        assert inbound.text == "/skills"

    @pytest.mark.asyncio
    async def test_slash_help_command_maps_to_framework(self, discord_channel: DiscordChannel) -> None:
        payload = {
            "type": 2,
            "id": "i",
            "token": "t",
            "channel_id": "c",
            "guild_id": "g",
            "member": {"user": {"id": "u"}},
            "data": {"name": "help"},
        }
        inbound = await discord_channel.parse_inbound(payload)
        assert inbound is not None
        assert inbound.text == "/help"


# --- send -----------------------------------------------------------------


class TestSend:
    """`send` chunks at 2000 chars and dispatches each chunk via the discord client."""

    @pytest.mark.asyncio
    async def test_single_chunk_sent_unchanged(self, discord_channel: DiscordChannel) -> None:
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        discord_channel.client = MagicMock()
        discord_channel.client.get_channel = MagicMock(return_value=mock_channel)
        discord_channel.client.fetch_channel = AsyncMock(return_value=mock_channel)

        await discord_channel.send("12345", OutboundMessage(text="hi"))

        mock_channel.send.assert_awaited_once_with("hi")

    @pytest.mark.asyncio
    async def test_oversized_message_chunked(self, discord_channel: DiscordChannel) -> None:
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        discord_channel.client = MagicMock()
        discord_channel.client.get_channel = MagicMock(return_value=mock_channel)
        discord_channel.client.fetch_channel = AsyncMock(return_value=mock_channel)

        # 5000 chars of "X" → 3 chunks at 2000-char Discord limit.
        text = "X" * 5000
        await discord_channel.send("12345", OutboundMessage(text=text))

        assert mock_channel.send.await_count == 3
        sent_lengths = [c.args[0] for c in mock_channel.send.await_args_list]
        assert sum(len(s) for s in sent_lengths) == 5000

    @pytest.mark.asyncio
    async def test_empty_message_no_send(self, discord_channel: DiscordChannel) -> None:
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        discord_channel.client = MagicMock()
        discord_channel.client.get_channel = MagicMock(return_value=mock_channel)
        discord_channel.client.fetch_channel = AsyncMock(return_value=mock_channel)

        await discord_channel.send("12345", OutboundMessage(text=""))
        mock_channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_fetch_channel_when_cache_miss(self, discord_channel: DiscordChannel) -> None:
        """`get_channel` returns None for un-cached channels — must fetch."""
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        discord_channel.client = MagicMock()
        discord_channel.client.get_channel = MagicMock(return_value=None)
        discord_channel.client.fetch_channel = AsyncMock(return_value=mock_channel)

        await discord_channel.send("99999", OutboundMessage(text="hello"))

        discord_channel.client.fetch_channel.assert_awaited_once_with(99999)
        mock_channel.send.assert_awaited_once_with("hello")


# --- on_unknown_user ------------------------------------------------------


class TestOnUnknownUser:
    """Discord uses explicit allowlist via `channel_routes/discord/{guild_id}`."""

    @pytest.mark.asyncio
    async def test_allowlist_hit_returns_firebase_uid(self, discord_channel: DiscordChannel) -> None:
        from channels.base import InboundMessage

        msg = InboundMessage(
            channel_user_id="user-1",
            channel_chat_id="chat-1",
            text="hi",
            metadata={"guild_id": "guild-allowed"},
        )
        with (
            patch(
                "channels.discord.get_document",
                return_value={
                    "allowed_user_ids": ["user-1", "user-2"],
                    "default_firebase_uid": "fb-uid-shared",
                },
            ),
            patch(
                "channels.discord.IdentityResolver.auto_create",
                AsyncMock(return_value="fb-uid-auto"),
            ),
        ):
            uid = await discord_channel.on_unknown_user(msg)
        assert uid == "fb-uid-auto"

    @pytest.mark.asyncio
    async def test_allowlist_miss_returns_none(self, discord_channel: DiscordChannel) -> None:
        from channels.base import InboundMessage

        msg = InboundMessage(
            channel_user_id="hacker",
            channel_chat_id="chat-1",
            text="hi",
            metadata={"guild_id": "guild-allowed"},
        )
        with patch(
            "channels.discord.get_document",
            return_value={"allowed_user_ids": ["user-1", "user-2"]},
        ):
            uid = await discord_channel.on_unknown_user(msg)
        assert uid is None

    @pytest.mark.asyncio
    async def test_no_guild_route_returns_none(self, discord_channel: DiscordChannel) -> None:
        from channels.base import InboundMessage

        msg = InboundMessage(
            channel_user_id="user-1",
            channel_chat_id="chat-1",
            text="hi",
            metadata={"guild_id": "guild-unknown"},
        )
        with patch("channels.discord.get_document", return_value=None):
            uid = await discord_channel.on_unknown_user(msg)
        assert uid is None

    @pytest.mark.asyncio
    async def test_dm_without_guild_id_returns_none(self, discord_channel: DiscordChannel) -> None:
        """DMs have no guild_id; per design, reject by default (no public DMs)."""
        from channels.base import InboundMessage

        msg = InboundMessage(
            channel_user_id="user-1",
            channel_chat_id="dm-1",
            text="hi",
            metadata={"guild_id": None},
        )
        uid = await discord_channel.on_unknown_user(msg)
        assert uid is None
