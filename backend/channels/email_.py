"""Email channel adapter — Mailgun inbound + outbound.

Ports the v5 `email_integration.py` flow into the v6 `BaseChannel`
framework: Mailgun HMAC signature verification, form-payload parsing,
`[SkillName]`-subject routing, and outbound `requests.post` to
`/v3/{domain}/messages` with reply-thread headers.

File is named `email_.py` (trailing underscore) to avoid shadowing the
Python stdlib `email` package — `from channels.email_ import ...` is
the canonical import path.

Mailgun signing reference:
    expected = hmac_sha256(signing_key, timestamp + token)
    valid    = compare_digest(expected, supplied_signature)

v5 features intentionally dropped:
    - Quarto export ([PDF]/[DOCX] subject flags) — that's a skill concern
    - HTML formatting + signatures — plain text v1; HTML follow-up
    - Per-recipient assistant routing via `assistant-{id}@domain` —
      replaced by `[SkillName]` subject prefix (uniform across channels)
    - LangChain / Sunholo imports — not used at all in v6
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import requests

from channels.attachments import Attachment
from channels.base import BaseChannel, InboundMessage, OutboundMessage
from channels.commands import CommandParser

logger = logging.getLogger(__name__)


# 25MB — typical email service hard cap. Mailgun's own ingress cap is
# 25MB by default; aligning means an inbound that passed Mailgun won't
# be rejected at the pipeline boundary for size alone.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


class EmailChannel(BaseChannel):
    """Email adapter over Mailgun (EU or US region).

    Configuration is injected at construction time (rather than read
    from env in the adapter) so tests can build an instance with
    deterministic creds and the wiring in `fast_api_app.py` stays
    explicit about what's required.
    """

    name = "email"
    command_prefix = "["  # `[Skill Name] body` subject routing
    max_attachment_size = MAX_ATTACHMENT_BYTES

    def __init__(
        self,
        *,
        signing_key: str,
        api_key: str,
        domain: str,
        sender_address: str,
        api_endpoint: str = "https://api.eu.mailgun.net",
    ) -> None:
        super().__init__()
        self._signing_key = signing_key
        self._api_key = api_key
        self._domain = domain
        self._sender_address = sender_address
        self._api_endpoint = api_endpoint.rstrip("/")

    # --- BaseChannel contract --------------------------------------------

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify Mailgun's `timestamp + token` HMAC-SHA256 signature.

        Fail-closed when the signing key is unconfigured — v5 logged a
        warning and accepted; v6 rejects. Operators set `MAILGUN_SIGNING_KEY`
        explicitly or no inbound emails are processed.
        """
        if not self._signing_key:
            logger.error("email channel: MAILGUN_SIGNING_KEY not configured — rejecting inbound")
            return False

        # Headers come in lowercase from FastAPI; tolerate both forms.
        h = {k.lower(): v for k, v in headers.items()}
        timestamp = h.get("x-mailgun-timestamp", "")
        token = h.get("x-mailgun-token", "")
        signature = h.get("x-mailgun-signature", "")

        if not (timestamp and token and signature):
            logger.warning("email channel: missing Mailgun signature headers")
            return False

        expected = hmac.new(
            key=self._signing_key.encode(),
            msg=f"{timestamp}{token}".encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Map a Mailgun form payload to an `InboundMessage`.

        Mailgun delivers webhooks as form-encoded data with these fields:
            sender, recipient, subject, body-plain, body-html,
            Message-Id, In-Reply-To, References, attachment-count,
            attachment-N (JSON metadata blob per attachment)

        Returns None when the payload has no text and no attachments
        (Mailgun health-check pings, etc.).
        """
        sender = payload.get("sender") or payload.get("from") or ""
        message_id = payload.get("Message-Id") or payload.get("message-id") or ""
        body_plain = payload.get("body-plain") or ""
        subject = payload.get("subject") or ""
        in_reply_to = payload.get("In-Reply-To") or payload.get("in-reply-to") or ""
        references = payload.get("References") or payload.get("references") or ""

        attachments = _attachments_from_mailgun_payload(payload)

        if not body_plain and not attachments:
            # Non-actionable: no message text, no files. Mailgun's
            # internal pings + spam-quarantine acks land here.
            return None

        if not sender or not message_id:
            logger.warning(
                "email channel: missing sender or Message-Id — dropping (sender=%r, mid=%r)",
                sender,
                message_id,
            )
            return None

        return InboundMessage(
            channel_user_id=sender,
            # `channel_chat_id` for email is the recipient of the reply
            # (the sender of the inbound). The framework calls
            # `send(chat_id, message)` so chat_id is "where the reply
            # goes" — for email that's the original sender. Message-Id
            # for In-Reply-To threading lives in metadata below.
            channel_chat_id=sender,
            text=body_plain,
            attachments=attachments,
            raw=payload,
            metadata={
                "subject": subject,
                # `in_reply_to` and `message_id` are equivalent for our
                # purposes: the inbound's Message-Id becomes the reply's
                # In-Reply-To header. Mailgun's "In-Reply-To" field is
                # the inbound message's PRIOR reference, which we also
                # carry for chain continuity.
                "in_reply_to": message_id,
                "message_id": message_id,
                "prior_in_reply_to": in_reply_to,
                "references": references,
                "to": sender,
            },
        )

    async def select_skill(self, msg: InboundMessage, firebase_uid: str) -> str | None:
        """`[Skill Name]` in the subject overrides the user's default skill.

        Body text already passed through `CommandParser` in the framework
        with `command_prefix="["`, but for emails the bracketed name
        lives in the SUBJECT, not the body. So we re-parse `subject` here
        and resolve the skill — falling back to the user default on miss.
        """
        subject = msg.metadata.get("subject") or ""
        cmd = CommandParser.parse(subject, prefix=self.command_prefix)
        if cmd is not None and cmd.name == "skill" and cmd.args:
            resolved = await _resolve_skill_by_name_or_id(cmd.args[0])
            if resolved is not None:
                logger.info("email channel: subject-routed to skill_id=%s", resolved)
                return resolved
            logger.info(
                "email channel: subject %r referenced unknown skill — falling back to default",
                subject,
            )
        return await super().select_skill(msg, firebase_uid)

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send an outbound email via Mailgun's `/v3/{domain}/messages`.

        `chat_id` is the recipient address — for inbound replies that's
        the original sender (see `parse_inbound`). For direct sends from
        event-driven skill output, callers pass a real address.

        `message.metadata` carries the threading context that the
        framework forwards from `inbound.metadata`:
            - `subject`     → reply subject becomes `Re: {subject}`
            - `in_reply_to` → SMTP `In-Reply-To` header (inbound's
                              Message-Id; ties the reply to the thread)
            - `references`  → SMTP `References` chain for nested
                              threading
        """
        if not self._api_key or not self._domain:
            logger.error("email channel: MAILGUN_API_KEY/DOMAIN unset — cannot send")
            return

        recipient = message.metadata.get("to") or chat_id
        subject = message.metadata.get("subject") or ""
        in_reply_to = message.metadata.get("in_reply_to") or ""

        from_field = f"Aitana <{self._sender_address}>"

        data: dict[str, str] = {
            "from": from_field,
            "to": recipient,
            "subject": f"Re: {subject}" if subject else "Re: (no subject)",
            "text": message.text,
        }
        if in_reply_to:
            # Mailgun forwards `h:`-prefixed fields as raw SMTP headers.
            data["h:In-Reply-To"] = in_reply_to
            data["h:References"] = in_reply_to

        url = f"{self._api_endpoint}/v3/{self._domain}/messages"
        try:
            resp = requests.post(
                url,
                auth=("api", self._api_key),
                data=data,
                timeout=30,
            )
        except requests.RequestException:
            logger.exception("email channel: mailgun send failed (network)")
            return

        if resp.status_code != 200:
            logger.error(
                "email channel: mailgun send failed status=%s body=%r",
                resp.status_code,
                resp.text[:500],
            )
            return

        logger.info("email channel: sent to=%s subject=%r", recipient, data["subject"])


# --- module helpers --------------------------------------------------------


def _attachments_from_mailgun_payload(payload: dict[str, Any]) -> list[Attachment]:
    """Build `Attachment` objects from Mailgun's form-encoded attachment fields.

    Mailgun renders attachments as a count + per-index JSON blob:
        attachment-count: "2"
        attachment-1: '{"url": "...", "name": "a.pdf", "content-type": "...", "size": 1024}'
        attachment-2: '{...}'

    Robust against:
        - missing or zero count
        - malformed JSON (logged + skipped)
        - already-decoded dicts (some test fixtures hand pre-parsed objects)
    """
    try:
        count = int(payload.get("attachment-count", 0))
    except (TypeError, ValueError):
        return []
    if count <= 0:
        return []

    out: list[Attachment] = []
    for i in range(1, count + 1):
        blob = payload.get(f"attachment-{i}")
        if blob is None:
            continue

        if isinstance(blob, dict):
            meta = blob
        elif isinstance(blob, str):
            try:
                meta = json.loads(blob)
            except (ValueError, TypeError):
                logger.warning(
                    "email channel: malformed attachment-%d payload (not JSON), skipping",
                    i,
                )
                continue
            if not isinstance(meta, dict):
                logger.warning("email channel: attachment-%d JSON was not an object, skipping", i)
                continue
        else:
            logger.warning(
                "email channel: attachment-%d had unexpected type %s, skipping",
                i,
                type(blob).__name__,
            )
            continue

        url = meta.get("url") or ""
        filename = meta.get("name") or meta.get("filename") or ""
        if not url or not filename:
            logger.warning(
                "email channel: attachment-%d missing url or name (url=%r name=%r), skipping",
                i,
                url,
                filename,
            )
            continue

        size_raw = meta.get("size")
        size_bytes: int | None
        try:
            size_bytes = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size_bytes = None

        out.append(
            Attachment(
                url=url,
                filename=filename,
                mime_type=meta.get("content-type") or meta.get("contentType"),
                size_bytes=size_bytes,
            )
        )

    return out


async def _resolve_skill_by_name_or_id(needle: str) -> str | None:
    """Resolve a `[Skill Name]` subject argument to a `skill_id`.

    Mirrors the logic in `_commands_runtime._resolve_skill_by_name_or_id`
    but lives here so the email adapter can resolve skills BEFORE the
    framework's command dispatch fires (the framework only parses BODY
    text; the email subject is a separate routing surface).
    """
    from skills.skill_config import get_skill, list_marketplace

    direct = get_skill(needle)
    if direct is not None:
        return needle

    needle_lower = needle.lower()
    for skill in list_marketplace(limit=100):
        if skill.title and skill.title.lower() == needle_lower:
            return skill.skill_id
    return None


__all__ = ["EmailChannel"]
