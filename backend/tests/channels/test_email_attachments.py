"""Integration tests: Mailgun attachment payloads → `Attachment` → pipeline.

These tests cover the boundary where Mailgun's form payload (with
`attachment-count` and `attachment-N` keys) becomes a list of
`Attachment` objects that `AttachmentPipeline.upload` can process. The
pipeline itself is unit-tested in `test_attachments.py`; here we only
check that the email adapter constructs the right `Attachment` objects
and threads them through.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from channels.base import Attachment
from channels.email_ import EmailChannel, _attachments_from_mailgun_payload


@pytest.fixture()
def channel() -> EmailChannel:
    return EmailChannel(signing_key="k", api_key="a", domain="d.test", sender_address="bot@d.test")


class TestAttachmentsFromMailgun:
    """Build `Attachment` objects from Mailgun's form-encoded attachment fields."""

    def test_no_attachments_returns_empty(self) -> None:
        result = _attachments_from_mailgun_payload({"sender": "a@b"})
        assert result == []

    def test_attachment_count_zero(self) -> None:
        result = _attachments_from_mailgun_payload({"attachment-count": "0"})
        assert result == []

    def test_single_attachment(self) -> None:
        payload = {
            "attachment-count": "1",
            "attachment-1": json.dumps(
                {
                    "url": "https://api.mailgun.net/v3/domains/d/messages/abc/attachments/0",
                    "name": "report.pdf",
                    "content-type": "application/pdf",
                    "size": 1024,
                }
            ),
        }
        result = _attachments_from_mailgun_payload(payload)
        assert len(result) == 1
        att = result[0]
        assert isinstance(att, Attachment)
        assert att.filename == "report.pdf"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 1024
        assert att.url.endswith("/attachments/0")

    def test_multiple_attachments(self) -> None:
        payload = {
            "attachment-count": "3",
            "attachment-1": json.dumps(
                {"url": "https://x/1", "name": "a.pdf", "content-type": "application/pdf", "size": 10}
            ),
            "attachment-2": json.dumps(
                {"url": "https://x/2", "name": "b.docx", "content-type": "application/x", "size": 20}
            ),
            "attachment-3": json.dumps(
                {"url": "https://x/3", "name": "c.png", "content-type": "image/png", "size": 30}
            ),
        }
        result = _attachments_from_mailgun_payload(payload)
        assert [a.filename for a in result] == ["a.pdf", "b.docx", "c.png"]
        assert [a.size_bytes for a in result] == [10, 20, 30]

    def test_malformed_attachment_skipped(self) -> None:
        """A bad JSON blob is skipped, not fatal — Mailgun has been known
        to send `[object Object]` strings under partial misconfiguration."""
        payload = {
            "attachment-count": "2",
            "attachment-1": "[object Object]",  # malformed
            "attachment-2": json.dumps(
                {"url": "https://x/2", "name": "ok.pdf", "content-type": "application/pdf", "size": 10}
            ),
        }
        result = _attachments_from_mailgun_payload(payload)
        assert len(result) == 1
        assert result[0].filename == "ok.pdf"

    def test_dict_form_not_just_json_strings(self) -> None:
        """Mailgun sometimes hands attachment metadata as a dict directly
        (e.g., when the framework already JSON-decoded). Accept both."""
        payload = {
            "attachment-count": "1",
            "attachment-1": {
                "url": "https://x/1",
                "name": "doc.pdf",
                "content-type": "application/pdf",
                "size": 100,
            },
        }
        result = _attachments_from_mailgun_payload(payload)
        assert len(result) == 1
        assert result[0].filename == "doc.pdf"


class TestParseInboundWithAttachments:
    """parse_inbound() pipes attachments through to InboundMessage."""

    @pytest.mark.asyncio
    async def test_inbound_carries_attachments(self, channel: EmailChannel) -> None:
        payload = {
            "sender": "alice@x.com",
            "Message-Id": "<m>",
            "body-plain": "see attached",
            "subject": "files",
            "attachment-count": "2",
            "attachment-1": json.dumps(
                {"url": "https://x/1", "name": "a.pdf", "content-type": "application/pdf", "size": 10}
            ),
            "attachment-2": json.dumps(
                {"url": "https://x/2", "name": "b.png", "content-type": "image/png", "size": 20}
            ),
        }
        msg = await channel.parse_inbound(payload)
        assert msg is not None
        assert len(msg.attachments) == 2
        assert msg.attachments[0].filename == "a.pdf"
        assert msg.attachments[1].filename == "b.png"

    @pytest.mark.asyncio
    async def test_attachment_only_message_is_actionable(self, channel: EmailChannel) -> None:
        """Empty body with attachments should still be processed (user
        forwarded a file with no commentary)."""
        payload = {
            "sender": "alice@x.com",
            "Message-Id": "<m>",
            "body-plain": "",
            "subject": "",
            "attachment-count": "1",
            "attachment-1": json.dumps(
                {"url": "https://x/1", "name": "a.pdf", "content-type": "application/pdf", "size": 10}
            ),
        }
        msg = await channel.parse_inbound(payload)
        assert msg is not None
        assert len(msg.attachments) == 1


class TestPipelineIntegration:
    """End-to-end: the BaseChannel pipeline picks up our attachments and uploads."""

    @pytest.mark.asyncio
    async def test_attachments_reach_pipeline(self, channel: EmailChannel) -> None:
        """When handle_webhook runs, AttachmentPipeline.upload sees our attachments."""
        import hashlib
        import hmac

        signing_key = "k"
        timestamp = "1700000000"
        token = "tk"
        expected = hmac.new(
            key=signing_key.encode(),
            msg=f"{timestamp}{token}".encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()
        headers = {
            "x-mailgun-timestamp": timestamp,
            "x-mailgun-token": token,
            "x-mailgun-signature": expected,
        }

        payload = {
            "sender": "alice@x.com",
            "Message-Id": "<m>",
            "body-plain": "please summarise",
            "subject": "files",
            "attachment-count": "1",
            "attachment-1": json.dumps(
                {"url": "https://x/1", "name": "a.pdf", "content-type": "application/pdf", "size": 10}
            ),
        }

        captured: dict = {}

        async def fake_upload(attachments, firebase_uid, *, max_size):
            captured["attachments"] = list(attachments)
            captured["max_size"] = max_size
            return ["doc-1"]

        async def fake_invoke(**kwargs):
            captured["attachment_ids"] = kwargs["attachment_ids"]
            return "ok"

        with (
            patch("channels.base.IdentityResolver.resolve", AsyncMock(return_value="uid-1")),
            patch("channels.base.AttachmentPipeline.upload", side_effect=fake_upload),
            patch("channels.email_._resolve_skill_by_name_or_id", AsyncMock(return_value=None)),
            patch(
                "channels._default_skill.get_user_default_skill",
                AsyncMock(return_value="general-assistant"),
            ),
            patch("channels._skill_invoke.invoke_skill_collected", side_effect=fake_invoke),
            patch("channels.email_.requests.post"),
        ):
            await channel.handle_webhook(payload, headers, b"")

        assert len(captured["attachments"]) == 1
        assert captured["attachments"][0].filename == "a.pdf"
        assert captured["max_size"] == 25 * 1024 * 1024
        assert captured["attachment_ids"] == ["doc-1"]
