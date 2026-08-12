"""Unit tests for `channels.whatsapp.WhatsAppChannel`.

The WhatsApp adapter is a `BaseChannel` subclass — these tests verify
the adapter-specific bits the framework doesn't cover:

    - `verify_webhook` — Twilio `X-Twilio-Signature` HMAC-SHA1 over the
      canonical (URL + sorted params) payload
    - `parse_inbound` — Twilio form fields (`From`, `Body`, `NumMedia`,
      `MediaUrlN`, `MediaContentTypeN`)
    - `send` — Twilio `messages.create` call shape, including chunking
      at the 1500-char soft cap and optional `media_url` attachment

Framework integration (verify-then-parse-then-dispatch) is already
proven by `tests/channels/test_base.py` — not re-tested here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest

from channels.base import OutboundMessage
from channels.whatsapp import WHATSAPP_MAX_MESSAGE_LENGTH, WhatsAppChannel

WEBHOOK_URL = "https://example.test/api/whatsapp/webhook"
AUTH_TOKEN = "twilio-auth-token-xyz"


def _twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute the canonical Twilio signature for tests."""
    payload = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _form_body(params: dict[str, str]) -> bytes:
    """Encode params as application/x-www-form-urlencoded bytes (Twilio's wire format)."""
    from urllib.parse import urlencode

    return urlencode(params).encode("utf-8")


@pytest.fixture()
def channel() -> WhatsAppChannel:
    """WhatsAppChannel with a known auth token, webhook URL, and sandbox `From`."""
    return WhatsAppChannel(
        account_sid="ACxxxxx",
        auth_token=AUTH_TOKEN,
        whatsapp_from="+14155238886",
        webhook_url=WEBHOOK_URL,
    )


# --- verify_webhook -------------------------------------------------------


class TestVerifyWebhook:
    """Twilio HMAC-SHA1 signature verification."""

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, channel: WhatsAppChannel) -> None:
        params = {"From": "whatsapp:+34661856650", "To": "whatsapp:+14155238886", "Body": "hi"}
        body = _form_body(params)
        sig = _twilio_signature(AUTH_TOKEN, WEBHOOK_URL, params)
        headers = {"X-Twilio-Signature": sig}
        assert await channel.verify_webhook(headers, body) is True

    @pytest.mark.asyncio
    async def test_valid_signature_accepted_lowercase_header(self, channel: WhatsAppChannel) -> None:
        params = {"From": "whatsapp:+34661856650", "Body": "hi"}
        body = _form_body(params)
        sig = _twilio_signature(AUTH_TOKEN, WEBHOOK_URL, params)
        headers = {"x-twilio-signature": sig}
        assert await channel.verify_webhook(headers, body) is True

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self, channel: WhatsAppChannel) -> None:
        params = {"From": "whatsapp:+34661856650", "Body": "hi"}
        body = _form_body(params)
        headers = {"X-Twilio-Signature": "deadbeef" * 8}
        assert await channel.verify_webhook(headers, body) is False

    @pytest.mark.asyncio
    async def test_tampered_body_rejected(self, channel: WhatsAppChannel) -> None:
        """Signature for {Body: hi} but body says {Body: tampered}."""
        good_params = {"From": "whatsapp:+34661856650", "Body": "hi"}
        sig = _twilio_signature(AUTH_TOKEN, WEBHOOK_URL, good_params)
        # Tamper: ship the signature but a different body.
        bad_body = _form_body({"From": "whatsapp:+34661856650", "Body": "tampered"})
        headers = {"X-Twilio-Signature": sig}
        assert await channel.verify_webhook(headers, bad_body) is False

    @pytest.mark.asyncio
    async def test_missing_signature_header_rejected(self, channel: WhatsAppChannel) -> None:
        body = _form_body({"Body": "hi"})
        assert await channel.verify_webhook({}, body) is False

    @pytest.mark.asyncio
    async def test_no_auth_token_rejects_for_safety(self) -> None:
        """Without an auth token configured, refuse — fail-closed is the v6 default."""
        ch = WhatsAppChannel(account_sid="x", auth_token="", whatsapp_from="+14155238886", webhook_url=WEBHOOK_URL)
        params = {"Body": "hi"}
        body = _form_body(params)
        sig = _twilio_signature("anything", WEBHOOK_URL, params)
        assert await ch.verify_webhook({"X-Twilio-Signature": sig}, body) is False

    @pytest.mark.asyncio
    async def test_no_webhook_url_rejects_for_safety(self) -> None:
        ch = WhatsAppChannel(account_sid="x", auth_token=AUTH_TOKEN, whatsapp_from="+14155238886", webhook_url="")
        assert await ch.verify_webhook({"X-Twilio-Signature": "anything"}, b"") is False


# --- parse_inbound --------------------------------------------------------


class TestParseInbound:
    """Twilio form-dict → `InboundMessage`."""

    @pytest.mark.asyncio
    async def test_text_message_returns_inbound(self, channel: WhatsAppChannel) -> None:
        payload = {
            "From": "whatsapp:+34661856650",
            "To": "whatsapp:+14155238886",
            "Body": "Hello",
            "MessageSid": "SMxxx1",
            "ProfileName": "Mar",
            "WaId": "34661856650",
        }
        inbound = await channel.parse_inbound(payload)
        assert inbound is not None
        # channel_user_id strips the `whatsapp:` prefix so it matches the
        # migrated phone number in `channel_identities`.
        assert inbound.channel_user_id == "+34661856650"
        # chat_id keeps the prefix — that's what the reply send() uses verbatim.
        assert inbound.channel_chat_id == "whatsapp:+34661856650"
        assert inbound.text == "Hello"
        assert inbound.attachments == []
        assert inbound.metadata["whatsapp_from"] == "whatsapp:+34661856650"
        assert inbound.metadata["whatsapp_to"] == "whatsapp:+14155238886"
        assert inbound.metadata["message_sid"] == "SMxxx1"
        assert inbound.metadata["profile_name"] == "Mar"
        assert inbound.metadata["wa_id"] == "34661856650"

    @pytest.mark.asyncio
    async def test_media_message_returns_attachments(self, channel: WhatsAppChannel) -> None:
        payload = {
            "From": "whatsapp:+34661856650",
            "To": "whatsapp:+14155238886",
            "Body": "Look at these",
            "MessageSid": "SMxxx2",
            "NumMedia": "2",
            "MediaUrl0": "https://api.twilio.com/2010-04-01/Accounts/AC/Messages/MM/Media/ME1",
            "MediaContentType0": "image/jpeg",
            "MediaUrl1": "https://api.twilio.com/2010-04-01/Accounts/AC/Messages/MM/Media/ME2",
            "MediaContentType1": "application/pdf",
        }
        inbound = await channel.parse_inbound(payload)
        assert inbound is not None
        assert len(inbound.attachments) == 2
        first = inbound.attachments[0]
        assert first.url.endswith("/Media/ME1")
        assert first.mime_type == "image/jpeg"
        assert first.filename.endswith(".jpg")
        second = inbound.attachments[1]
        assert second.mime_type == "application/pdf"
        assert second.filename.endswith(".pdf")

    @pytest.mark.asyncio
    async def test_empty_body_no_media_returns_none(self, channel: WhatsAppChannel) -> None:
        """No Body, no media — non-actionable (Twilio status callbacks, etc.)."""
        payload = {"From": "whatsapp:+34661856650", "To": "whatsapp:+14155238886", "Body": ""}
        assert await channel.parse_inbound(payload) is None

    @pytest.mark.asyncio
    async def test_missing_from_returns_none(self, channel: WhatsAppChannel) -> None:
        payload = {"To": "whatsapp:+14155238886", "Body": "hi"}
        assert await channel.parse_inbound(payload) is None


# --- send -----------------------------------------------------------------


class TestSend:
    """`send` calls Twilio `messages.create`, chunks at 1500 chars."""

    @pytest.mark.asyncio
    async def test_single_chunk_sent(self, channel: WhatsAppChannel) -> None:
        mock_client = MagicMock()
        with patch.object(channel, "_get_client", return_value=mock_client):
            await channel.send("whatsapp:+34661856650", OutboundMessage(text="hi"))

        assert mock_client.messages.create.call_count == 1
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["body"] == "hi"
        assert kwargs["to"] == "whatsapp:+34661856650"
        assert kwargs["from_"] == "whatsapp:+14155238886"
        assert "media_url" not in kwargs

    @pytest.mark.asyncio
    async def test_oversized_message_chunked(self, channel: WhatsAppChannel) -> None:
        mock_client = MagicMock()
        text = "X" * 5000  # > 1500 → multiple chunks
        with patch.object(channel, "_get_client", return_value=mock_client):
            await channel.send("whatsapp:+34661856650", OutboundMessage(text=text))

        # 5000 / 1500 = 4 chunks (last one partial)
        assert mock_client.messages.create.call_count == 4
        total_sent = sum(len(c.kwargs["body"]) for c in mock_client.messages.create.call_args_list)
        assert total_sent == 5000
        assert all(
            len(c.kwargs["body"]) <= WHATSAPP_MAX_MESSAGE_LENGTH for c in mock_client.messages.create.call_args_list
        )

    @pytest.mark.asyncio
    async def test_media_attached_to_first_chunk_only(self, channel: WhatsAppChannel) -> None:
        mock_client = MagicMock()
        text = "X" * 3000  # 2 chunks
        media_urls = ["https://aitana-public-bucket.example/img1.jpg"]
        with patch.object(channel, "_get_client", return_value=mock_client):
            await channel.send(
                "whatsapp:+34661856650",
                OutboundMessage(text=text, metadata={"media_urls": media_urls}),
            )

        calls = mock_client.messages.create.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs.get("media_url") == media_urls
        assert "media_url" not in calls[1].kwargs

    @pytest.mark.asyncio
    async def test_recipient_prefix_normalised(self, channel: WhatsAppChannel) -> None:
        """`chat_id` without `whatsapp:` prefix gets it prepended."""
        mock_client = MagicMock()
        with patch.object(channel, "_get_client", return_value=mock_client):
            await channel.send("+34661856650", OutboundMessage(text="hi"))
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["to"] == "whatsapp:+34661856650"

    @pytest.mark.asyncio
    async def test_empty_message_no_send(self, channel: WhatsAppChannel) -> None:
        mock_client = MagicMock()
        with patch.object(channel, "_get_client", return_value=mock_client):
            await channel.send("whatsapp:+34661856650", OutboundMessage(text=""))
        mock_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_no_op_when_client_unconfigured(self, caplog) -> None:
        """No SID/token = no client = no send (logs an error)."""
        ch = WhatsAppChannel(account_sid="", auth_token="", whatsapp_from="+14155238886", webhook_url=WEBHOOK_URL)
        with caplog.at_level("ERROR"):
            await ch.send("whatsapp:+34661856650", OutboundMessage(text="hi"))
        assert any(
            "TWILIO_ACCOUNT_SID/TOKEN missing" in r.message or "TWILIO_AUTH_TOKEN" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_send_no_op_when_from_unconfigured(self, caplog) -> None:
        """Missing TWILIO_WHATSAPP_FROM aborts the send."""
        ch = WhatsAppChannel(account_sid="AC", auth_token="t", whatsapp_from="", webhook_url=WEBHOOK_URL)
        mock_client = MagicMock()
        with patch.object(ch, "_get_client", return_value=mock_client), caplog.at_level("ERROR"):
            await ch.send("whatsapp:+34661856650", OutboundMessage(text="hi"))
        mock_client.messages.create.assert_not_called()
        assert any("TWILIO_WHATSAPP_FROM" in r.message for r in caplog.records)


# --- channel defaults -----------------------------------------------------


class TestChannelDefaults:
    def test_name(self, channel: WhatsAppChannel) -> None:
        assert channel.name == "whatsapp"

    def test_command_prefix(self, channel: WhatsAppChannel) -> None:
        assert channel.command_prefix == "/"

    def test_max_attachment_size_16mb(self, channel: WhatsAppChannel) -> None:
        assert channel.max_attachment_size == 16 * 1024 * 1024

    def test_supports_streaming_false(self, channel: WhatsAppChannel) -> None:
        assert channel.supports_streaming is False
