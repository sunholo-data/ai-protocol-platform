"""Unit tests for `channels.telegram_.TelegramChannel`.

The Telegram adapter is a `BaseChannel` subclass — these tests verify
the adapter-specific bits the framework doesn't cover:

    - `verify_webhook` — `X-Telegram-Bot-Api-Secret-Token` header
      verification (happy + reject + fail-closed when unconfigured)
    - `parse_inbound` — message text, photo, document inbound shapes;
      non-actionable updates (edits, callbacks) → None
    - `send` — HTML parse_mode + 4096-char chunking via stubbed bot

Framework integration (verify-then-parse-then-dispatch) is already
proven by `tests/channels/test_base.py` and
`test_handle_webhook_integration.py`; not re-tested here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channels.base import OutboundMessage
from channels.telegram_ import TELEGRAM_MAX_MESSAGE_LENGTH, TelegramChannel


@pytest.fixture()
def webhook_secret() -> str:
    return "telegram-webhook-secret-xyz"


@pytest.fixture()
def channel(webhook_secret: str) -> TelegramChannel:
    """Telegram channel wired with a known bot token + webhook secret."""
    return TelegramChannel(bot_token="test-bot-token", webhook_secret=webhook_secret)


# --- verify_webhook -------------------------------------------------------


class TestVerifyWebhook:
    """`X-Telegram-Bot-Api-Secret-Token` header verification."""

    @pytest.mark.asyncio
    async def test_valid_secret_accepted(self, channel: TelegramChannel, webhook_secret: str) -> None:
        headers = {"X-Telegram-Bot-Api-Secret-Token": webhook_secret}
        assert await channel.verify_webhook(headers, b"{}") is True

    @pytest.mark.asyncio
    async def test_valid_secret_accepted_lowercase_header(self, channel: TelegramChannel, webhook_secret: str) -> None:
        # FastAPI typically lowercases — make sure we tolerate both.
        headers = {"x-telegram-bot-api-secret-token": webhook_secret}
        assert await channel.verify_webhook(headers, b"{}") is True

    @pytest.mark.asyncio
    async def test_wrong_secret_rejected(self, channel: TelegramChannel) -> None:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}
        assert await channel.verify_webhook(headers, b"{}") is False

    @pytest.mark.asyncio
    async def test_missing_header_rejected(self, channel: TelegramChannel) -> None:
        assert await channel.verify_webhook({}, b"{}") is False

    @pytest.mark.asyncio
    async def test_no_configured_secret_rejects_for_safety(self) -> None:
        """Without a webhook secret configured, every inbound is refused."""
        ch = TelegramChannel(bot_token="t", webhook_secret="")
        headers = {"X-Telegram-Bot-Api-Secret-Token": "anything"}
        assert await ch.verify_webhook(headers, b"{}") is False


# --- parse_inbound --------------------------------------------------------


class TestParseInbound:
    """Telegram `Update` → `InboundMessage`; non-actionable → None."""

    @pytest.mark.asyncio
    async def test_text_message_returns_inbound(self, channel: TelegramChannel) -> None:
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "from": {"id": 1001, "username": "alice", "first_name": "Alice"},
                "chat": {"id": -100200, "type": "private"},
                "text": "Hello bot",
            },
        }
        inbound = await channel.parse_inbound(payload)
        assert inbound is not None
        assert inbound.channel_user_id == "1001"
        assert inbound.channel_chat_id == "-100200"
        assert inbound.text == "Hello bot"
        assert inbound.attachments == []
        assert inbound.metadata["telegram_user_id"] == "1001"
        assert inbound.metadata["username"] == "alice"
        assert inbound.metadata["message_id"] == 42

    @pytest.mark.asyncio
    async def test_photo_message_resolves_download_url(self, channel: TelegramChannel) -> None:
        """Photo arrives as a list; we take the largest + resolve via getFile."""
        payload = {
            "update_id": 2,
            "message": {
                "message_id": 43,
                "from": {"id": 1001},
                "chat": {"id": 1001},
                "caption": "look at this",
                "photo": [
                    {"file_id": "small-id", "file_size": 1024, "width": 90, "height": 90},
                    {"file_id": "big-id", "file_size": 50_000, "width": 1280, "height": 720},
                ],
            },
        }
        with patch.object(
            channel,
            "_get_file_url",
            AsyncMock(return_value="https://api.telegram.org/file/bot.../photos/big.jpg"),
        ) as get_url:
            inbound = await channel.parse_inbound(payload)

        assert inbound is not None
        assert inbound.text == "look at this"
        assert len(inbound.attachments) == 1
        # We took the *largest* photo (last item).
        get_url.assert_awaited_once_with("big-id")
        att = inbound.attachments[0]
        assert att.url.endswith("/big.jpg")
        # Telegram photos have no file_name; the adapter synthesises one.
        assert att.filename.endswith(".jpg") or att.filename.endswith(".bin")

    @pytest.mark.asyncio
    async def test_document_message_uses_file_name(self, channel: TelegramChannel) -> None:
        payload = {
            "update_id": 3,
            "message": {
                "message_id": 44,
                "from": {"id": 1001},
                "chat": {"id": 1001},
                "text": "",
                "document": {
                    "file_id": "doc-id-1",
                    "file_name": "contract.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 1024 * 1024,
                },
            },
        }
        with patch.object(
            channel,
            "_get_file_url",
            AsyncMock(return_value="https://api.telegram.org/file/bot.../docs/contract.pdf"),
        ):
            inbound = await channel.parse_inbound(payload)
        assert inbound is not None
        assert len(inbound.attachments) == 1
        att = inbound.attachments[0]
        assert att.filename == "contract.pdf"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 1024 * 1024

    @pytest.mark.asyncio
    async def test_edited_message_returns_none(self, channel: TelegramChannel) -> None:
        """Edits are not actionable for v1 — return None to skip the dispatch."""
        payload = {
            "update_id": 4,
            "edited_message": {
                "message_id": 42,
                "from": {"id": 1001},
                "chat": {"id": 1001},
                "text": "Edited text",
            },
        }
        assert await channel.parse_inbound(payload) is None

    @pytest.mark.asyncio
    async def test_callback_query_returns_none(self, channel: TelegramChannel) -> None:
        """Button-press callbacks — non-actionable in v1."""
        payload = {
            "update_id": 5,
            "callback_query": {"id": "cb-1", "data": "switch:doc-analyst"},
        }
        assert await channel.parse_inbound(payload) is None

    @pytest.mark.asyncio
    async def test_message_without_text_or_media_returns_none(self, channel: TelegramChannel) -> None:
        """A sticker / location with no text and no downloadable media is non-actionable."""
        payload = {
            "update_id": 6,
            "message": {
                "message_id": 47,
                "from": {"id": 1001},
                "chat": {"id": 1001},
                "sticker": {"file_id": "sticker-1"},  # not in _DOWNLOADABLE_MEDIA_KEYS
            },
        }
        assert await channel.parse_inbound(payload) is None

    @pytest.mark.asyncio
    async def test_missing_user_id_returns_none(self, channel: TelegramChannel) -> None:
        payload = {"update_id": 7, "message": {"chat": {"id": 1}, "text": "hi"}}
        assert await channel.parse_inbound(payload) is None


# --- send -----------------------------------------------------------------


class TestSend:
    """`send` chunks at 4096 chars and dispatches each chunk via the bot client."""

    @pytest.mark.asyncio
    async def test_single_chunk_sent_with_html_parse_mode(self, channel: TelegramChannel) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with patch.object(channel, "_get_bot", return_value=mock_bot):
            await channel.send("1001", OutboundMessage(text="hello <b>world</b>"))

        mock_bot.send_message.assert_awaited_once()
        kwargs = mock_bot.send_message.await_args.kwargs
        assert kwargs["chat_id"] == 1001
        assert kwargs["text"] == "hello <b>world</b>"
        assert kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_oversized_message_chunked(self, channel: TelegramChannel) -> None:
        """Long messages split at 4096-char limit."""
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        text = "X" * 10_000  # 3 chunks at 4096
        with patch.object(channel, "_get_bot", return_value=mock_bot):
            await channel.send("1001", OutboundMessage(text=text))

        assert mock_bot.send_message.await_count == 3
        total_sent = sum(len(c.kwargs["text"]) for c in mock_bot.send_message.await_args_list)
        assert total_sent == 10_000
        # All chunks under the cap.
        assert all(len(c.kwargs["text"]) <= TELEGRAM_MAX_MESSAGE_LENGTH for c in mock_bot.send_message.await_args_list)

    @pytest.mark.asyncio
    async def test_empty_message_no_send(self, channel: TelegramChannel) -> None:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with patch.object(channel, "_get_bot", return_value=mock_bot):
            await channel.send("1001", OutboundMessage(text="   "))
        mock_bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_no_op_when_bot_unconfigured(self, caplog) -> None:
        """Without a bot token, send logs an error but does not raise."""
        ch = TelegramChannel(bot_token="", webhook_secret="s")
        with caplog.at_level("ERROR"):
            await ch.send("1001", OutboundMessage(text="hi"))
        assert any("TELEGRAM_BOT_TOKEN not configured" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_string_chat_id_passed_through(self, channel: TelegramChannel) -> None:
        """Channels can use @username string IDs — must not crash on int() coerce."""
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with patch.object(channel, "_get_bot", return_value=mock_bot):
            await channel.send("@aitanachannel", OutboundMessage(text="hi"))
        kwargs = mock_bot.send_message.await_args.kwargs
        assert kwargs["chat_id"] == "@aitanachannel"


# --- channel defaults -----------------------------------------------------


class TestChannelDefaults:
    def test_name(self, channel: TelegramChannel) -> None:
        assert channel.name == "telegram"

    def test_command_prefix(self, channel: TelegramChannel) -> None:
        assert channel.command_prefix == "/"

    def test_max_attachment_size_50mb(self, channel: TelegramChannel) -> None:
        assert channel.max_attachment_size == 50 * 1024 * 1024

    def test_supports_streaming_false(self, channel: TelegramChannel) -> None:
        # Telegram doesn't support per-message live edits in v1 (would
        # need editMessageText with rate-limit budget — not in scope).
        assert channel.supports_streaming is False
