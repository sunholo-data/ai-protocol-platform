"""WhatsApp channel adapter — Twilio WhatsApp Business API.

Ports v5 `whatsapp_service.py` into the v6 `BaseChannel` framework.
Twilio delivers WhatsApp inbound webhooks as form-encoded POSTs to the
adapter's webhook URL; the registry's form-decoder converts to a dict
before `parse_inbound` runs.

Outbound is via the Twilio REST `Messages` API (`from_=whatsapp:+...`,
`to=whatsapp:+...`). The adapter chunks at ~1600 chars (WhatsApp's
soft cap; Twilio's hard cap is 1600) using the shared helper.

What this adapter does (and does not):
    - Verifies Twilio's `X-Twilio-Signature` header against the
      configured auth token + canonical URL + form payload. Fail-closed
      when the auth token or the webhook URL aren't configured.
    - Parses Twilio form fields (`From`, `To`, `Body`, `NumMedia`,
      `MediaUrlN`, `MediaContentTypeN`).
    - Sends outbound via `twilio.rest.Client.messages.create`, chunking
      at 1600 chars. Media URLs from `message.metadata["media_urls"]`
      are passed with the first chunk.

What it does NOT carry over from v5:
    - Per-user phone→email mapping in module-level data
      (`channel_mappings.PHONE_TO_EMAIL`). Replaced by the framework's
      `channel_identities` collection + the v5→v6 migration script.
    - Twilio MessageSid Firestore deduplication. Twilio retries on 5xx,
      but the framework returns 200 on both success and non-actionable
      paths; idempotency at the skill layer is a separate concern.
    - `/list` / `/switch` / `/current` slash-style commands — replaced
      by the framework's `/skill`, `/skills`, `/help`, `/clear`.
    - LangChain / Sunholo imports — not used at all in v6.
    - Voice transcription / TTS — out of channel scope for v1.

Test seams:
    - `verify_webhook` reads `self._auth_token` + `self._webhook_url`.
    - `send` calls `self._client.messages.create(...)`. Tests stub
      `self._client` directly.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar
from urllib.parse import quote

from channels._chunk import chunk_message
from channels.attachments import Attachment
from channels.base import BaseChannel, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


# WhatsApp hard message cap is 4096 chars but Twilio enforces 1600 for
# WhatsApp `Body`. Stay under for safety margin across emoji-in-surrogate
# boundary counts.
WHATSAPP_MAX_MESSAGE_LENGTH = 1500

# Twilio caps `media_url` at 10 entries per outbound message. We never
# carry more than this — the cap is a Twilio API constraint.
_MAX_OUTBOUND_MEDIA = 10


class WhatsAppChannel(BaseChannel):
    """WhatsApp adapter over Twilio's WhatsApp Business API.

    Configuration is injected at construction time. The Twilio client
    is lazy — instantiated on first `send` — so tests exercising
    `parse_inbound` / `verify_webhook` don't import the heavy `twilio`
    SDK.

    `webhook_url` is the public URL Twilio POSTs to (e.g.,
    ``https://platform-backend-xxx.run.app/api/whatsapp/webhook``).
    Twilio's signature is computed over this exact URL — a mismatch
    (proxies, port forwarding) silently rejects every inbound, so
    operators must configure it precisely. Reading from
    ``TWILIO_WEBHOOK_URL`` in env mirrors the v5 deploy pattern.
    """

    name: ClassVar[str] = "whatsapp"
    command_prefix: ClassVar[str] = "/"
    # Twilio's WhatsApp inbound size cap. Aligning means an inbound that
    # passed Twilio won't be rejected at the pipeline boundary for size.
    max_attachment_size: ClassVar[int] = 16 * 1024 * 1024
    supports_streaming: ClassVar[bool] = False

    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        whatsapp_from: str | None = None,
        webhook_url: str | None = None,
    ) -> None:
        super().__init__()
        self._account_sid = account_sid if account_sid is not None else os.getenv("TWILIO_ACCOUNT_SID", "")
        self._auth_token = auth_token if auth_token is not None else os.getenv("TWILIO_AUTH_TOKEN", "")
        self._whatsapp_from = whatsapp_from if whatsapp_from is not None else os.getenv("TWILIO_WHATSAPP_FROM", "")
        self._webhook_url = webhook_url if webhook_url is not None else os.getenv("TWILIO_WEBHOOK_URL", "")
        # Lazy Twilio REST client — populated on first send.
        self._client: Any | None = None

    # --- BaseChannel contract --------------------------------------------

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify Twilio's `X-Twilio-Signature` header.

        Twilio's signature recipe:
            payload = url + sorted(form-encoded parameters concatenated)
            expected = base64(hmac_sha1(auth_token, payload))

        Fail-closed when auth token or webhook URL are unconfigured —
        matches v6 default posture.

        Note: the body bytes alone are insufficient. Twilio's algorithm
        operates on the form-decoded *parameter dict*. We re-decode here
        from `body` because the framework's `handle_webhook` only passes
        raw bytes — the form dict is owned by `parse_inbound`. This
        decode is cheap and signature-safe.
        """
        if not self._auth_token:
            logger.error("whatsapp channel: TWILIO_AUTH_TOKEN not configured — rejecting inbound")
            return False
        if not self._webhook_url:
            logger.error("whatsapp channel: TWILIO_WEBHOOK_URL not configured — rejecting inbound")
            return False

        h = {k.lower(): v for k, v in headers.items()}
        supplied = h.get("x-twilio-signature", "")
        if not supplied:
            return False

        # Decode form fields from the raw body for the canonical payload.
        # Twilio sends application/x-www-form-urlencoded.
        try:
            from urllib.parse import parse_qsl

            form = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        except Exception:
            logger.warning("whatsapp verify: could not decode form body")
            return False

        # Twilio's algorithm: URL + sorted(key+value) concatenated.
        payload = self._webhook_url + "".join(f"{k}{v}" for k, v in sorted(form.items()))

        import base64
        import hashlib
        import hmac as _hmac

        expected = base64.b64encode(
            _hmac.new(self._auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
        ).decode("ascii")
        return _hmac.compare_digest(expected, supplied)

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Map a Twilio WhatsApp webhook form-dict to an `InboundMessage`.

        Twilio's form fields:
            From:               'whatsapp:+34661856650'
            To:                 'whatsapp:+14155238886'
            Body:               'message text'
            MessageSid:         'SMxxxxx...'
            NumMedia:           '1' (string)
            MediaUrl0:          'https://api.twilio.com/.../Media/MEsxxxxx'
            MediaContentType0:  'image/jpeg'

        Returns None when both Body and media are empty (Twilio's own
        delivery receipts / status callbacks land here if mis-routed).
        """
        # `From` is the user's WhatsApp address, `To` is the bot's.
        sender = (payload.get("From") or "").strip()
        recipient = (payload.get("To") or "").strip()
        body_text = (payload.get("Body") or "").strip()

        if not sender:
            logger.warning("whatsapp parse_inbound: missing From")
            return None

        # Normalise: strip `whatsapp:` prefix for the channel_user_id so
        # the channel_identities lookup matches the migrated v5 data
        # (which stored phone numbers as `+34...`).
        channel_user_id = sender.removeprefix("whatsapp:")
        channel_chat_id = sender  # send replies back to the same `whatsapp:+...`

        attachments = _attachments_from_twilio_payload(payload)

        if not body_text and not attachments:
            return None

        return InboundMessage(
            channel_user_id=channel_user_id,
            channel_chat_id=channel_chat_id,
            text=body_text,
            attachments=attachments,
            metadata={
                "whatsapp_from": sender,
                "whatsapp_to": recipient,
                "message_sid": payload.get("MessageSid") or "",
                "profile_name": payload.get("ProfileName") or "",
                "wa_id": payload.get("WaId") or "",
            },
            raw=payload,
        )

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send an outbound WhatsApp message via the Twilio REST API.

        `chat_id` is the user's `whatsapp:+...` address (carried from
        `parse_inbound`). Long messages are chunked at 1500 chars.
        Optional `message.metadata["media_urls"]` carries up to 10
        attachment URLs — attached to the first chunk only (Twilio
        renders one media set per message).
        """
        client = self._get_client()
        if client is None:
            logger.error("whatsapp send: TWILIO_ACCOUNT_SID/TOKEN missing — dropping outbound to %s", chat_id)
            return
        if not self._whatsapp_from:
            logger.error("whatsapp send: TWILIO_WHATSAPP_FROM not configured — cannot send")
            return

        chunks = chunk_message(message.text, max_length=WHATSAPP_MAX_MESSAGE_LENGTH)
        if not chunks:
            return

        # Normalise both sides — accept `+...` and prepend `whatsapp:` if
        # callers forgot to. Mirror v5 tolerance.
        to_addr = chat_id if chat_id.startswith("whatsapp:") else f"whatsapp:{chat_id}"
        from_addr = (
            self._whatsapp_from if self._whatsapp_from.startswith("whatsapp:") else f"whatsapp:{self._whatsapp_from}"
        )

        media_urls = (message.metadata or {}).get("media_urls") or []
        if not isinstance(media_urls, list):
            media_urls = []
        media_urls = [str(u) for u in media_urls if u][:_MAX_OUTBOUND_MEDIA]

        for i, chunk in enumerate(chunks):
            kwargs: dict[str, Any] = {"body": chunk, "from_": from_addr, "to": to_addr}
            if i == 0 and media_urls:
                kwargs["media_url"] = media_urls
            try:
                client.messages.create(**kwargs)
            except Exception:
                logger.exception("whatsapp send: messages.create failed (chunk %d/%d)", i + 1, len(chunks))
                # Continue with subsequent chunks — partial delivery beats none.
                continue

    # --- helpers ---------------------------------------------------------

    def _get_client(self) -> Any | None:
        """Lazy-instantiate the Twilio REST client. Returns None on missing creds."""
        if not self._account_sid or not self._auth_token:
            return None
        if self._client is None:
            from twilio.rest import Client

            self._client = Client(self._account_sid, self._auth_token)
        return self._client


# --- module helpers --------------------------------------------------------


def _attachments_from_twilio_payload(payload: dict[str, Any]) -> list[Attachment]:
    """Build `Attachment` objects from Twilio's `MediaUrlN` / `MediaContentTypeN` fields.

    Twilio media URLs require authentication via the same account SID +
    auth token to download — that's a concern for the attachment
    pipeline's `_download_bytes` helper, NOT this adapter. We surface
    only the URL + content type here.

    Returns `[]` when `NumMedia` is missing, zero, or unparseable.
    """
    try:
        count = int(payload.get("NumMedia", 0))
    except (TypeError, ValueError):
        return []
    if count <= 0:
        return []

    out: list[Attachment] = []
    for i in range(count):
        url = payload.get(f"MediaUrl{i}")
        if not url:
            continue
        mime = payload.get(f"MediaContentType{i}") or None
        filename = _filename_for_media(url, mime, i)
        out.append(
            Attachment(
                url=str(url),
                filename=filename,
                mime_type=mime,
                size_bytes=None,  # Twilio doesn't supply size in webhook payload
            )
        )
    return out


def _filename_for_media(url: str, mime: str | None, index: int) -> str:
    """Synthesise a filename for a Twilio media URL.

    Twilio doesn't carry a filename in the webhook payload, only the
    Media URL (e.g., `.../Media/MEsxxxxx`) and a MIME type. The
    attachment pipeline's extension allowlist needs an extension, so we
    synthesise one from MIME.
    """
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "video/mp4": ".mp4",
        "text/plain": ".txt",
    }.get(mime or "", ".bin")
    # Use the last segment of the URL as a stable identifier; safe-quote
    # in case the URL itself contains characters that would confuse
    # downstream filename handling.
    tail = url.rsplit("/", 1)[-1] if "/" in url else f"media{index}"
    safe_tail = quote(tail, safe="")[:80] or f"media{index}"
    return f"whatsapp_{safe_tail}{ext}"


__all__ = ["WHATSAPP_MAX_MESSAGE_LENGTH", "WhatsAppChannel"]
