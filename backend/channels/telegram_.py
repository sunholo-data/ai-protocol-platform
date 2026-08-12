"""Telegram channel adapter — Bot API webhook + outbound messaging.

Ports v5 `telegram_service.py` into the v6 `BaseChannel` framework.
Telegram delivers `Update` payloads to the webhook URL set by the
operator with `setWebhook`; the framework's mounted route at
``POST /api/telegram/webhook`` is the receiver.

File is named `telegram_.py` (trailing underscore) to mirror the
`email_.py` convention and avoid any future shadowing of a third-party
`telegram` package on the path.

What this adapter does (and does not):
    - Verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` header
      against the configured webhook secret (fail-closed if no secret
      is configured, mirroring v6 default).
    - Parses message text + photo + document inbound shapes. Photos
      arrive as a sorted list of file objects; we take the largest.
    - For media, resolves the download URL via `getFile` so the framework's
      attachment pipeline can fetch bytes uniformly. The URL embeds the
      bot token — that's Telegram's design.
    - Sends outbound via the Bot API with HTML parse_mode and 4096-char
      chunking via the shared `chunk_message` helper.

What it does NOT carry over from v5:
    - `first_impression` routing / per-channel assistant selection —
      replaced by the framework's `select_skill` default (user default).
    - Markdown→HTML response formatting — out of scope for the v6 v1.
      The agent already emits HTML when running through a channel.
    - Per-update Firestore deduplication (`is_update_already_processed`)
      — Telegram retries on 5xx, but framework returns 200 on
      non-actionable and on success; idempotency at the skill layer is
      a separate concern (M2/M3 didn't ship it either).
    - `/list` / `/switch` / `/current` slash-style commands — replaced by
      the framework's `/skill`, `/skills`, `/help`, `/clear` (which run
      via `CommandParser` for free).
    - Voice/audio attachments — out of channel scope for v1.

Test seams:
    - `verify_webhook` reads `self._webhook_secret`.
    - `parse_inbound` calls `self._get_file_url(...)`. Tests patch that
      method directly (no real Bot API call required).
    - `send` calls `self._bot.send_message(...)`. Tests stub `self._bot`.
"""

from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath
from typing import Any, ClassVar

import httpx

from channels._chunk import chunk_message
from channels.attachments import Attachment
from channels.base import BaseChannel, InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


# Telegram caps message text at 4096 chars. We chunk under that to stay
# safe across emoji surrogate-pair counts and parse_mode escaping.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Telegram Bot API base. The URL embeds the bot token by design — every
# request is authenticated solely by URL path. We never log this URL.
TELEGRAM_API_BASE = "https://api.telegram.org"

# Media types we know how to download via `getFile`. Photo arrives as a
# *list* of file objects (one per resolution); document/voice/etc. arrive
# as single file objects. Order in this map mirrors v5 prioritisation.
_DOWNLOADABLE_MEDIA_KEYS: tuple[str, ...] = ("document", "photo", "voice", "audio", "video")


class TelegramChannel(BaseChannel):
    """Telegram adapter over the Bot API HTTPS webhook transport.

    Configuration is injected at construction time so tests build a
    deterministic instance and `fast_api_app.py` stays explicit about
    required env. A missing bot token does not raise at construction
    — `send` becomes a no-op (logged) so the webhook still 200s; the
    integrator notices on the first inbound that no reply landed.
    """

    name: ClassVar[str] = "telegram"
    command_prefix: ClassVar[str] = "/"
    # Telegram's hard cap is 50 MB for bots downloading via getFile.
    max_attachment_size: ClassVar[int] = 50 * 1024 * 1024
    supports_streaming: ClassVar[bool] = False

    def __init__(
        self,
        *,
        bot_token: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        super().__init__()
        self._bot_token = bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._webhook_secret = (
            webhook_secret if webhook_secret is not None else os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        )
        # Lazy Bot client — instantiated on first send so tests that exercise
        # `parse_inbound` / `verify_webhook` don't import `telegram` at all.
        # `python-telegram-bot` is heavy; importing it eagerly slows test
        # collection and the channel may not be registered in CI.
        self._bot: Any | None = None

    # --- BaseChannel contract --------------------------------------------

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify the `X-Telegram-Bot-Api-Secret-Token` header.

        Telegram doesn't sign payloads — instead, the operator sets a
        secret token via `setWebhook(secret_token=...)` and Telegram
        echoes it on every webhook delivery. The header name is
        case-insensitive per HTTP spec; FastAPI lowercases.

        Fail-closed: if no secret is configured locally, reject all
        inbound. Matches v6's email/Discord posture — explicitly
        configure or no traffic gets through.
        """
        if not self._webhook_secret:
            logger.error("telegram channel: TELEGRAM_WEBHOOK_SECRET not configured — rejecting inbound")
            return False

        h = {k.lower(): v for k, v in headers.items()}
        supplied = h.get("x-telegram-bot-api-secret-token", "")
        return supplied == self._webhook_secret

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Map a Telegram `Update` to an `InboundMessage`.

        Telegram delivers many event types via the same webhook URL:
            message, edited_message, channel_post, callback_query,
            inline_query, chosen_inline_result, poll, poll_answer, ...

        We treat only `message` as actionable for v1. Edits, callbacks,
        and inline queries return None and the framework short-circuits.
        Future work can extend this for button-press routing.
        """
        message = payload.get("message")
        if not isinstance(message, dict):
            # Non-actionable: edits, callback queries, etc.
            return None

        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        channel_user_id = str(from_user.get("id") or "")
        channel_chat_id = str(chat.get("id") or "")
        if not channel_user_id or not channel_chat_id:
            logger.warning("telegram parse_inbound: missing user.id or chat.id")
            return None

        # Telegram lets a message carry text OR a caption (on photos /
        # documents). Both surfaces are user intent; coalesce here.
        text = (message.get("text") or message.get("caption") or "").strip()

        attachments = await self._extract_attachments(message)

        if not text and not attachments:
            # No text and no media — usually a sticker / location / contact;
            # not actionable in v1 channel scope.
            return None

        metadata = {
            "telegram_user_id": channel_user_id,
            "telegram_chat_id": channel_chat_id,
            "message_id": message.get("message_id"),
            "username": from_user.get("username") or "",
            "first_name": from_user.get("first_name") or "",
            "language_code": from_user.get("language_code") or "",
        }

        return InboundMessage(
            channel_user_id=channel_user_id,
            channel_chat_id=channel_chat_id,
            text=text,
            attachments=attachments,
            metadata=metadata,
            raw=payload,
        )

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send an outbound message via the Bot API.

        Uses HTML parse mode by default — matches v5's output shape and
        agent prompts that emit `<b>`, `<i>`, `<code>`. Long messages
        are chunked at 4096 characters via the shared helper.
        """
        chunks = chunk_message(message.text, max_length=TELEGRAM_MAX_MESSAGE_LENGTH)
        if not chunks:
            return

        bot = self._get_bot()
        if bot is None:
            logger.error("telegram send: TELEGRAM_BOT_TOKEN not configured — dropping outbound to chat=%s", chat_id)
            return

        try:
            numeric_chat_id: int | str = int(chat_id)
        except (TypeError, ValueError):
            # Some channels use string IDs (channels with @username). Pass through.
            numeric_chat_id = chat_id

        # parse_mode hint: respect `message.format` if explicitly set;
        # otherwise default to HTML to preserve v5 visual style.
        parse_mode = "HTML" if message.format in ("plain", "html") else "Markdown"

        for chunk in chunks:
            try:
                await bot.send_message(chat_id=numeric_chat_id, text=chunk, parse_mode=parse_mode)
            except Exception:
                logger.exception("telegram send: bot.send_message failed for chat=%s", chat_id)
                # Continue with subsequent chunks — partial delivery beats none.
                continue

    # --- helpers ---------------------------------------------------------

    def _get_bot(self) -> Any | None:
        """Lazy-instantiate the `telegram.Bot` client.

        Imports `python-telegram-bot` only on first send so the channel
        adapter is testable without the dep installed. Returns None when
        the bot token is unconfigured.
        """
        if not self._bot_token:
            return None
        if self._bot is None:
            from telegram import Bot  # late import — heavy module

            self._bot = Bot(token=self._bot_token)
        return self._bot

    async def _extract_attachments(self, message: dict[str, Any]) -> list[Attachment]:
        """Pull downloadable media references from a Telegram `message` dict.

        For photos, Telegram delivers a sorted ascending list of
        resolutions — we take the largest. For documents, the object is
        a single dict. We resolve the download URL via `getFile` so the
        framework's attachment pipeline can fetch bytes with no
        adapter-specific download path.
        """
        out: list[Attachment] = []

        for key in _DOWNLOADABLE_MEDIA_KEYS:
            media = message.get(key)
            if media is None:
                continue
            file_obj = media[-1] if isinstance(media, list) and media else media
            if not isinstance(file_obj, dict):
                continue

            file_id = file_obj.get("file_id")
            if not file_id:
                continue

            url = await self._get_file_url(file_id)
            if not url:
                continue

            filename = (
                file_obj.get("file_name")
                or _filename_from_path(url)
                or f"telegram_{key}_{file_id}{_extension_from_mime(file_obj.get('mime_type'))}"
            )
            out.append(
                Attachment(
                    url=url,
                    filename=filename,
                    mime_type=file_obj.get("mime_type"),
                    size_bytes=file_obj.get("file_size"),
                )
            )
        return out

    async def _get_file_url(self, file_id: str) -> str | None:
        """Resolve a Telegram `file_id` to a downloadable HTTPS URL.

        Calls `getFile` against the Bot API and assembles the download
        URL: ``{api}/file/bot{token}/{file_path}``. Tests patch this
        method directly to avoid real HTTP.
        """
        if not self._bot_token:
            logger.warning("telegram _get_file_url: TELEGRAM_BOT_TOKEN not configured")
            return None
        api_url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/getFile"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(api_url, json={"file_id": file_id})
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("telegram _get_file_url: getFile failed for file_id=%s", file_id)
            return None

        if not data.get("ok"):
            logger.warning("telegram _get_file_url: getFile not ok: %r", data.get("description"))
            return None
        file_path = (data.get("result") or {}).get("file_path")
        if not file_path:
            return None
        return f"{TELEGRAM_API_BASE}/file/bot{self._bot_token}/{file_path}"


# --- module helpers --------------------------------------------------------


def _filename_from_path(url: str) -> str:
    """Extract a filename from a Telegram file URL's path segment."""
    try:
        return PurePosixPath(httpx.URL(url).path).name
    except Exception:
        return ""


def _extension_from_mime(mime: str | None) -> str:
    """Best-effort extension for media that arrives without `file_name`.

    Telegram photos lack a `file_name`. We synthesise one with a sensible
    extension so the attachment pipeline's extension allowlist accepts
    the file (otherwise the photo would be rejected as 'no extension').
    """
    if not mime:
        return ".bin"
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "video/mp4": ".mp4",
    }.get(mime, ".bin")


__all__ = ["TELEGRAM_MAX_MESSAGE_LENGTH", "TelegramChannel"]
