"""Unit tests for `channels.email_.EmailChannel`.

The adapter is exercised in isolation — Mailgun HTTP calls (inbound +
outbound) are mocked. Tests follow the patterns established in
`test_base.py` and `test_handle_webhook_integration.py`: AsyncMock for
the three side-effect points (`requests.post`, signature lookup,
identity resolution) plus direct calls to the adapter methods.
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channels.base import InboundMessage, OutboundMessage
from channels.email_ import EmailChannel


def _signed_headers(signing_key: str, timestamp: str = "1700000000", token: str = "tk-abc") -> dict[str, str]:
    """Build a valid Mailgun signature header trio for `signing_key`."""
    expected = hmac.new(
        key=signing_key.encode(),
        msg=f"{timestamp}{token}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return {
        "x-mailgun-timestamp": timestamp,
        "x-mailgun-token": token,
        "x-mailgun-signature": expected,
    }


@pytest.fixture()
def signing_key() -> str:
    return "test-signing-key-very-secret"


@pytest.fixture()
def channel(signing_key: str) -> EmailChannel:
    """Email channel with a known signing key + Mailgun creds injected."""
    return EmailChannel(
        signing_key=signing_key,
        api_key="api-test",
        domain="example.test",
        sender_address="bot@example.test",
        api_endpoint="https://api.eu.mailgun.test",
    )


class TestVerifyWebhook:
    """Mailgun HMAC verification: happy + reject + tampered."""

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, channel: EmailChannel, signing_key: str) -> None:
        headers = _signed_headers(signing_key)
        assert await channel.verify_webhook(headers, b"") is True

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, channel: EmailChannel) -> None:
        assert await channel.verify_webhook({}, b"") is False

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self, channel: EmailChannel) -> None:
        bad = {
            "x-mailgun-timestamp": "1700000000",
            "x-mailgun-token": "tk-abc",
            "x-mailgun-signature": "deadbeef" * 8,  # wrong hash
        }
        assert await channel.verify_webhook(bad, b"") is False

    @pytest.mark.asyncio
    async def test_tampered_token_rejected(self, channel: EmailChannel, signing_key: str) -> None:
        """Signature is right for `tk-abc` but token field was swapped."""
        headers = _signed_headers(signing_key, token="tk-abc")
        headers["x-mailgun-token"] = "tk-tampered"
        assert await channel.verify_webhook(headers, b"") is False

    @pytest.mark.asyncio
    async def test_no_signing_key_rejects_for_safety(self, signing_key: str) -> None:
        """Without a configured key, refuse — fail-closed is the v6 default."""
        ch = EmailChannel(signing_key="", api_key="k", domain="d", sender_address="bot@d")
        assert await ch.verify_webhook(_signed_headers(signing_key), b"") is False


class TestParseInbound:
    """Mailgun payload → InboundMessage mapping."""

    @pytest.mark.asyncio
    async def test_basic_payload(self, channel: EmailChannel) -> None:
        payload = {
            "sender": "alice@example.com",
            "Message-Id": "<msg-1@example.com>",
            "body-plain": "Hello, what's the weather?",
            "subject": "Quick question",
            "In-Reply-To": "",
            "References": "",
        }
        msg = await channel.parse_inbound(payload)
        assert msg is not None
        assert msg.channel_user_id == "alice@example.com"
        # channel_chat_id = the reply recipient (sender of inbound), not Message-Id.
        # See parse_inbound — the framework calls send(chat_id, message) so
        # chat_id is the SMTP destination; threading info lives in metadata.
        assert msg.channel_chat_id == "alice@example.com"
        assert msg.text == "Hello, what's the weather?"
        assert msg.metadata["subject"] == "Quick question"
        # in_reply_to in metadata is the inbound's Message-Id — that's what
        # we'll set as the OUTBOUND's `In-Reply-To` header to thread the reply.
        assert msg.metadata["in_reply_to"] == "<msg-1@example.com>"
        assert msg.metadata["message_id"] == "<msg-1@example.com>"
        # The inbound's prior In-Reply-To header (chain continuation, may be empty)
        assert msg.metadata["prior_in_reply_to"] == ""
        assert msg.metadata["to"] == "alice@example.com"
        assert msg.attachments == []

    @pytest.mark.asyncio
    async def test_with_bracket_subject(self, channel: EmailChannel) -> None:
        payload = {
            "sender": "bob@example.com",
            "Message-Id": "<msg-2@example.com>",
            "body-plain": "Please summarise this thread.",
            "subject": "[Doc Analyst] Re: meeting notes",
        }
        msg = await channel.parse_inbound(payload)
        assert msg is not None
        assert msg.metadata["subject"] == "[Doc Analyst] Re: meeting notes"

    @pytest.mark.asyncio
    async def test_missing_body_returns_none(self, channel: EmailChannel) -> None:
        """A payload with no text and no attachments is non-actionable."""
        msg = await channel.parse_inbound({"sender": "x@y", "Message-Id": "<m>"})
        assert msg is None

    @pytest.mark.asyncio
    async def test_threading_metadata_preserved(self, channel: EmailChannel) -> None:
        payload = {
            "sender": "carol@example.com",
            "Message-Id": "<msg-3@example.com>",
            "body-plain": "follow-up",
            "subject": "Re: previous",
            "In-Reply-To": "<prev-msg@example.com>",
            "References": "<root@example.com> <prev-msg@example.com>",
        }
        msg = await channel.parse_inbound(payload)
        assert msg is not None
        # The reply's `In-Reply-To` header threads to the inbound's Message-Id,
        # not to the inbound's own In-Reply-To. That's how RFC-5322 threading
        # works: each reply references the parent message's Message-Id.
        assert msg.metadata["in_reply_to"] == "<msg-3@example.com>"
        # We keep the inbound's own In-Reply-To for chain continuity in
        # case a fork needs it for richer threading.
        assert msg.metadata["prior_in_reply_to"] == "<prev-msg@example.com>"
        assert msg.metadata["references"] == "<root@example.com> <prev-msg@example.com>"


class TestSelectSkill:
    """`[SkillName] subject` overrides default skill via subject parsing."""

    @pytest.mark.asyncio
    async def test_bracket_subject_resolves_skill(self, channel: EmailChannel) -> None:
        msg = InboundMessage(
            channel_user_id="u@example.com",
            channel_chat_id="<m>",
            text="please help",
            metadata={"subject": "[Doc Analyst] summarise"},
        )
        with patch(
            "channels.email_._resolve_skill_by_name_or_id",
            AsyncMock(return_value="doc-analyst"),
        ):
            assert await channel.select_skill(msg, "uid-1") == "doc-analyst"

    @pytest.mark.asyncio
    async def test_bracket_subject_unknown_skill_falls_back_to_default(
        self,
        channel: EmailChannel,
    ) -> None:
        msg = InboundMessage(
            channel_user_id="u@example.com",
            channel_chat_id="<m>",
            text="hi",
            metadata={"subject": "[No Such Skill] anything"},
        )
        with (
            patch(
                "channels.email_._resolve_skill_by_name_or_id",
                AsyncMock(return_value=None),
            ),
            patch(
                "channels._default_skill.get_user_default_skill",
                AsyncMock(return_value="general-assistant"),
            ),
        ):
            assert await channel.select_skill(msg, "uid-1") == "general-assistant"

    @pytest.mark.asyncio
    async def test_plain_subject_falls_back_to_default(self, channel: EmailChannel) -> None:
        msg = InboundMessage(
            channel_user_id="u@example.com",
            channel_chat_id="<m>",
            text="hi",
            metadata={"subject": "no brackets here"},
        )
        with patch(
            "channels._default_skill.get_user_default_skill",
            AsyncMock(return_value="general-assistant"),
        ):
            assert await channel.select_skill(msg, "uid-1") == "general-assistant"

    @pytest.mark.asyncio
    async def test_no_subject_falls_back_to_default(self, channel: EmailChannel) -> None:
        msg = InboundMessage(
            channel_user_id="u@example.com",
            channel_chat_id="<m>",
            text="hi",
            metadata={},
        )
        with patch(
            "channels._default_skill.get_user_default_skill",
            AsyncMock(return_value="general-assistant"),
        ):
            assert await channel.select_skill(msg, "uid-1") == "general-assistant"


class TestSend:
    """Outbound Mailgun API call shape: auth, payload, reply threading."""

    @pytest.mark.asyncio
    async def test_send_posts_to_mailgun(self, channel: EmailChannel) -> None:
        mock_resp = MagicMock(status_code=200, text="queued")
        with patch("channels.email_.requests.post", return_value=mock_resp) as mock_post:
            await channel.send(
                "alice@example.com",
                OutboundMessage(
                    text="Here is your answer.",
                    metadata={"subject": "Quick question", "in_reply_to": "<msg-1@example.com>"},
                ),
            )

        assert mock_post.call_count == 1
        url = mock_post.call_args.args[0]
        assert url == "https://api.eu.mailgun.test/v3/example.test/messages"
        assert mock_post.call_args.kwargs["auth"] == ("api", "api-test")

        data = mock_post.call_args.kwargs["data"]
        assert data["to"] == "alice@example.com"
        assert data["subject"] == "Re: Quick question"
        assert data["text"] == "Here is your answer."
        # Reply threading: In-Reply-To references the original Message-Id
        assert data.get("h:In-Reply-To") == "<msg-1@example.com>"
        # Sender uses configured EMAIL_SENDER_ADDRESS
        assert "bot@example.test" in data["from"]

    @pytest.mark.asyncio
    async def test_send_handles_missing_subject_metadata(self, channel: EmailChannel) -> None:
        """Send should still work if metadata doesn't carry a subject."""
        mock_resp = MagicMock(status_code=200, text="queued")
        with patch("channels.email_.requests.post", return_value=mock_resp) as mock_post:
            await channel.send(
                "alice@example.com",
                OutboundMessage(text="reply"),
            )

        data = mock_post.call_args.kwargs["data"]
        # Default subject prefixed with Re:
        assert data["subject"].startswith("Re: ")
        # No In-Reply-To when no Message-Id known
        assert "h:In-Reply-To" not in data

    @pytest.mark.asyncio
    async def test_send_logs_on_non_200(self, channel: EmailChannel, caplog) -> None:
        """Non-200 response is logged but does not raise (channel framework
        treats send as best-effort — the inbound webhook still 200s)."""
        mock_resp = MagicMock(status_code=401, text="Unauthorized")
        with (
            patch("channels.email_.requests.post", return_value=mock_resp),
            caplog.at_level("ERROR"),
        ):
            await channel.send(
                "alice@example.com",
                OutboundMessage(text="x", metadata={"subject": "s"}),
            )
        assert any("mailgun send failed" in r.message.lower() for r in caplog.records)


class TestChannelDefaults:
    """Class-level configuration matches the framework contract."""

    def test_name(self) -> None:
        ch = EmailChannel(signing_key="k", api_key="a", domain="d", sender_address="s@d")
        assert ch.name == "email"

    def test_command_prefix_is_bracket(self) -> None:
        ch = EmailChannel(signing_key="k", api_key="a", domain="d", sender_address="s@d")
        assert ch.command_prefix == "["

    def test_max_attachment_size_25mb(self) -> None:
        ch = EmailChannel(signing_key="k", api_key="a", domain="d", sender_address="s@d")
        assert ch.max_attachment_size == 25 * 1024 * 1024
