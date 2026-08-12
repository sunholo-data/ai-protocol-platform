"""Slash-command registration idempotency tests.

`DiscordChannel.register_slash_commands(guild_id)` is called by each
Cloud Run instance at startup. To avoid hammering Discord's API and
to make redeploys safe, the call is gated by a Firestore lock:

    bot_state/discord_{guild_id}:
        registered_at: ISO 8601 timestamp
        guild_id: "..."

Concrete behaviours covered:

    1. First call: lock missing → registration happens → lock written
    2. Second call within TTL: lock fresh → no-op
    3. Call with expired lock: re-registers
    4. Missing token / application id: skipped, returns False
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channels.discord import DiscordChannel


@pytest.fixture()
def channel() -> DiscordChannel:
    return DiscordChannel(
        public_key_hex="aa" * 32,
        token="bot-token",
        application_id="app-id-123",
    )


class TestSlashRegistration:
    @pytest.mark.asyncio
    async def test_first_call_registers_and_writes_lock(self, channel: DiscordChannel) -> None:
        with (
            patch("channels.discord.get_document", return_value=None) as get_doc,
            patch("channels.discord.set_document") as set_doc,
            patch("channels.discord.httpx.AsyncClient") as client_cls,
        ):
            put_mock = AsyncMock()
            put_mock.return_value.raise_for_status = MagicMock()
            client_cls.return_value.__aenter__.return_value.put = put_mock

            registered = await channel.register_slash_commands("guild-1")

        assert registered is True
        get_doc.assert_called_once_with("bot_state", "discord_guild-1")
        set_doc.assert_called_once()
        # Lock contains the guild + a timestamp.
        coll, doc_id, payload = set_doc.call_args.args
        assert coll == "bot_state"
        assert doc_id == "discord_guild-1"
        assert payload["guild_id"] == "guild-1"
        assert "registered_at" in payload

    @pytest.mark.asyncio
    async def test_fresh_lock_skips(self, channel: DiscordChannel) -> None:
        # Fresh registration timestamp = now.
        existing_lock = {
            "registered_at": datetime.now(UTC).isoformat(),
            "guild_id": "guild-1",
        }
        with (
            patch("channels.discord.get_document", return_value=existing_lock),
            patch("channels.discord.set_document") as set_doc,
            patch("channels.discord.httpx.AsyncClient") as client_cls,
        ):
            registered = await channel.register_slash_commands("guild-1")

        assert registered is False
        # No-op: neither HTTP nor lock-write.
        set_doc.assert_not_called()
        client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_lock_reregisters(self, channel: DiscordChannel) -> None:
        # Lock older than TTL → re-register.
        stale_lock = {
            "registered_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
            "guild_id": "guild-1",
        }
        with (
            patch("channels.discord.get_document", return_value=stale_lock),
            patch("channels.discord.set_document") as set_doc,
            patch("channels.discord.httpx.AsyncClient") as client_cls,
        ):
            put_mock = AsyncMock()
            put_mock.return_value.raise_for_status = MagicMock()
            client_cls.return_value.__aenter__.return_value.put = put_mock

            registered = await channel.register_slash_commands("guild-1")

        assert registered is True
        set_doc.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_token_or_app_id_skips(self) -> None:
        ch = DiscordChannel(public_key_hex="aa" * 32, token="", application_id="")
        registered = await ch.register_slash_commands("guild-1")
        assert registered is False

    @pytest.mark.asyncio
    async def test_command_set_contains_expected_commands(self, channel: DiscordChannel) -> None:
        spec = channel._slash_command_spec()
        names = {c["name"] for c in spec}
        # Per the design doc.
        assert {"ask", "skill", "skills", "help"}.issubset(names)
