"""Discord channel adapter — `BaseChannel` subclass.

Discord is a hybrid channel: it splits **interactions** (slash commands,
delivered as signed webhooks) from **messages** (mentions and DMs,
delivered via a persistent gateway WebSocket). Both reach the framework's
single `handle_webhook` flow:

  - slash commands → mounted route `POST /api/discord/webhook`
    → framework `handle_webhook(payload, headers, body)`
  - mentions / DMs → gateway `on_message` handler
    → adapter synthesises an `InboundMessage` + calls a thin helper
      that re-enters the same framework path

Discord-specific concerns the framework does NOT cover:

  - Ed25519 signature verification (Discord's interaction webhook contract)
  - Discord's 2000-char message limit (handled by `chunk_message`)
  - `channel_routes/discord/{guild_id}` allowlist for per-guild access
  - The gateway connection itself (persistent WebSocket, requires
    `min_instances=1` in Cloud Run — see `discord-channel.md`)
  - Slash-command registration with Discord's Application Commands API
  - Live message-edit streaming on AG-UI events

This file is intentionally framework-stylised: each public method is one
of the abstract-or-overridable hooks from `BaseChannel`, plus a small
cluster of Discord-specific helpers grouped at the bottom.

Test seams:
  - `verify_webhook` reads `self._verify_key`, set from `public_key_hex`
    in __init__. Tests pass a known signing key's verify_key hex.
  - `send` calls `self.client.get_channel(...)` / `.fetch_channel(...)`.
    Tests stub `self.client` directly.
  - `on_unknown_user` reads Firestore via `get_document`. Tests patch the
    module-level reference.
  - `send_streaming` consumes an async iterator of AG-UI event dicts.
    Tests feed a fake iterator and assert edit calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import nacl.exceptions
import nacl.signing
from nacl.exceptions import BadSignatureError

from channels._a2ui_discord import surface_to_embed, surface_to_text
from channels._agui_render import AGUIChannelRenderer, ChannelSink
from channels._chunk import chunk_message
from channels.base import BaseChannel, InboundMessage, OutboundMessage
from channels.identity import IdentityResolver
from db.firestore import get_document, set_document

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)


# Discord caps regular messages at 2000 chars. Embeds have higher limits
# but aren't used for plain replies — citations render as separate
# messages (or embeds in a future iteration).
DISCORD_MAX_MESSAGE_LENGTH = 2000

# Streaming edits: Discord rate-limits message edits at ~5/sec/channel.
# Stay well under to leave headroom for normal sends. The streaming loop
# coalesces deltas into at-most one edit per `STREAM_EDIT_INTERVAL_SEC`.
STREAM_EDIT_INTERVAL_SEC = 1.0

# Slash-command registration lock TTL — re-registers if the doc is older
# than this. Picks up changes to the command set on redeploy.
SLASH_REGISTRATION_TTL = timedelta(days=7)


class DiscordChannel(BaseChannel):
    """Discord adapter — slash-command webhook + gateway message handler.

    Instantiated once at app startup with the bot token and Ed25519
    public key. The gateway is opened only when `start_gateway()` is
    awaited — tests skip that and exercise the adapter via direct
    method calls.

    Public-key hex and token can be supplied via constructor (for tests)
    or read from env (`DISCORD_PUBLIC_KEY`, `DISCORD_TOKEN`) when the
    constructor args are None — that's how `fast_api_app.py` wires it.
    """

    name: ClassVar[str] = "discord"
    command_prefix: ClassVar[str] = "/"
    max_attachment_size: ClassVar[int] = 8 * 1024 * 1024  # Discord 8 MB free-tier limit
    supports_streaming: ClassVar[bool] = True

    # Map slash-command names → framework command text. Adding a new
    # slash command that should route to the framework only requires
    # an entry here, not new code.
    _FRAMEWORK_SLASH_COMMANDS: ClassVar[dict[str, str]] = {
        "skill": "/skill",
        "skills": "/skills",
        "help": "/help",
        "clear": "/clear",
    }

    def __init__(
        self,
        *,
        public_key_hex: str | None = None,
        token: str | None = None,
        application_id: str | None = None,
    ) -> None:
        super().__init__()
        self._public_key_hex = public_key_hex or os.getenv("DISCORD_PUBLIC_KEY", "")
        self._token = token or os.getenv("DISCORD_TOKEN", "")
        self._application_id = application_id or os.getenv("DISCORD_APPLICATION_ID", "")

        # Build the verify key once. Empty / missing key → verify_webhook
        # always rejects. That's the right behaviour: if the key isn't
        # configured, no Discord traffic should be trusted.
        self._verify_key: nacl.signing.VerifyKey | None = None
        if self._public_key_hex:
            try:
                self._verify_key = nacl.signing.VerifyKey(bytes.fromhex(self._public_key_hex))
            except (ValueError, nacl.exceptions.TypeError):
                logger.error("DISCORD_PUBLIC_KEY is not valid hex; all webhook verifies will fail")

        # The discord.py Client is created lazily so tests don't pay the
        # cost (and don't need to mock module-level imports). Real boot
        # populates this via `start_gateway()`.
        self.client: Any | None = None
        self._gateway_task: asyncio.Task[Any] | None = None

    # --- adapter MUST implement (BaseChannel ABC) ------------------------

    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Discord interaction webhooks are signed with Ed25519.

        The signing payload is `timestamp + body` (concatenated bytes).
        Both signature and timestamp arrive as HTTP headers
        (`x-signature-ed25519`, `x-signature-timestamp`). Missing
        headers, bad hex, or signature mismatch all reject cleanly —
        never raise.
        """
        if self._verify_key is None:
            return False

        # Headers from Starlette arrive lowercased; defensive on both forms.
        signature = headers.get("x-signature-ed25519") or headers.get("X-Signature-Ed25519")
        timestamp = headers.get("x-signature-timestamp") or headers.get("X-Signature-Timestamp")
        if not signature or not timestamp:
            return False

        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            logger.debug("discord verify: signature is not hex")
            return False

        try:
            self._verify_key.verify(timestamp.encode() + body, sig_bytes)
        except BadSignatureError:
            return False
        except Exception:
            # PyNaCl can raise its own TypeError on wrong-length sig bytes.
            logger.debug("discord verify: unexpected error", exc_info=True)
            return False
        return True

    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Parse a slash-command interaction payload.

        Discord interaction payload `type` values we care about:
            1 = PING (returned by `/api/{name}/webhook` framework with
                ack-only; we treat as non-actionable here)
            2 = APPLICATION_COMMAND (slash command — the only actionable
                shape this adapter handles via webhook)
            3 = MESSAGE_COMPONENT (button click, etc. — not actionable yet)
            4 = APPLICATION_COMMAND_AUTOCOMPLETE
            5 = MODAL_SUBMIT

        Anything other than `2` returns None and the framework
        short-circuits to `{"ok": True, "skipped": True}`.
        """
        if payload.get("type") != 2:
            return None

        data = payload.get("data") or {}
        command_name = (data.get("name") or "").lower()

        # User identity: guild members arrive as `member.user`, DMs as `user`.
        user_obj = (payload.get("member") or {}).get("user") or payload.get("user") or {}
        channel_user_id = str(user_obj.get("id") or "")
        if not channel_user_id:
            logger.warning("discord parse_inbound: no user id in payload")
            return None

        channel_chat_id = str(payload.get("channel_id") or "")
        guild_id = payload.get("guild_id")  # None for DMs
        interaction_token = payload.get("token", "")

        text = self._extract_command_text(command_name, data)
        if text is None:
            logger.info("discord parse_inbound: unknown slash command %r", command_name)
            return None

        return InboundMessage(
            channel_user_id=channel_user_id,
            channel_chat_id=channel_chat_id,
            text=text,
            metadata={
                "guild_id": str(guild_id) if guild_id else None,
                "interaction_token": interaction_token,
                "command": command_name,
            },
            raw=payload,
        )

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send (chunked at Discord's 2000-char limit) via the gateway client.

        Falls back to `client.fetch_channel(...)` when the channel is not
        in the local cache — important for first-touch from a webhook
        where the gateway hasn't seen the channel yet.
        """
        chunks = chunk_message(message.text, max_length=DISCORD_MAX_MESSAGE_LENGTH)
        if not chunks:
            return

        channel = await self._resolve_channel(chat_id)
        if channel is None:
            logger.warning("discord send: could not resolve channel id=%s", chat_id)
            return

        for chunk in chunks:
            await channel.send(chunk)

    # --- adapter MAY override (BaseChannel defaults) ---------------------

    async def on_unknown_user(self, msg: InboundMessage) -> str | None:
        """Per-guild allowlist via `channel_routes/discord/{guild_id}`.

        Schema:
            channel_routes/discord/{guild_id}:
                allowed_user_ids: ["847239...", ...]   # explicit allowlist
                default_firebase_uid: "..."            # optional shared uid

        Returns:
            Firebase UID (newly auto-created) on allowlist hit; None on miss
            or when no route doc exists for the guild. DMs (no guild_id)
            always return None — DMs are admin-only territory and require
            explicit allowlisting in code, not just a Firestore flip.
        """
        guild_id = msg.metadata.get("guild_id")
        if not guild_id:
            logger.info(
                "discord on_unknown_user: rejecting DM/no-guild user=%s",
                msg.channel_user_id,
            )
            return None

        route = get_document("channel_routes", f"discord_{guild_id}")
        if not route:
            logger.info(
                "discord on_unknown_user: no route doc for guild=%s — rejecting user=%s",
                guild_id,
                msg.channel_user_id,
            )
            return None

        allowed = set(route.get("allowed_user_ids") or [])
        if msg.channel_user_id not in allowed:
            logger.info(
                "discord on_unknown_user: user=%s not in allowlist for guild=%s",
                msg.channel_user_id,
                guild_id,
            )
            return None

        # Allowlist hit — create the channel_identity so subsequent
        # webhooks short-circuit through the resolver.
        return await IdentityResolver.auto_create(self.name, msg.channel_user_id)

    # --- gateway path (mentions + DMs, not slash commands) ---------------

    async def start_gateway(self) -> None:
        """Open the persistent gateway WebSocket.

        Called from app startup. Discord's gateway demands sub-second
        keepalive responses, so this requires `min_instances >= 1` in
        Cloud Run — documented in `infrastructure/modules/cloud-run-channel`.
        """
        if not self._token:
            logger.warning("discord start_gateway: no DISCORD_TOKEN — gateway will not start")
            return

        import discord  # late import — heavy module, optional dep at startup

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True

        self.client = discord.Client(intents=intents)
        self.client.event(self.on_message)

        self._gateway_task = asyncio.create_task(self.client.start(self._token))
        logger.info("discord gateway started")

    async def on_message(self, message: discord.Message) -> None:
        """Handle a gateway-delivered message.

        Three actionable cases:
            1. DM to the bot — always react
            2. Mention in a guild channel — react
            3. Reply in a bot-created thread — react

        Everything else is dropped silently. The actionable case is
        re-shaped into an `InboundMessage` and re-entered into the
        framework via `BaseChannel._dispatch_inbound` — same downstream
        flow as the slash-command webhook path takes after parse.
        """
        import discord

        if self.client is None or message.author == self.client.user:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = bool(self.client.user) and self.client.user in (message.mentions or [])
        if not is_dm and not is_mention:
            return

        bot_mention = self.client.user.mention if self.client.user else ""
        text = (message.content or "").replace(bot_mention, "").strip()

        # Auto-thread: if a guild mention arrives in a regular channel,
        # spin up a thread so the conversation has a stable container.
        # This matches the edmonbrain UX: `f"{skill}-zzz - {preview}"`.
        chat_id = str(message.channel.id)
        if not is_dm and not isinstance(message.channel, discord.Thread):
            try:
                thread = await self._auto_thread(message, text)
                chat_id = str(thread.id)
            except Exception:
                logger.exception("discord auto-thread failed; falling back to channel id")

        inbound = InboundMessage(
            channel_user_id=str(message.author.id),
            channel_chat_id=chat_id,
            text=text,
            metadata={
                "guild_id": str(message.guild.id) if message.guild else None,
                "is_dm": is_dm,
            },
        )
        await self._dispatch_inbound(inbound)

    # --- streaming reply (adapter-specific override) ---------------------

    async def send_streaming(
        self,
        chat_id: str,
        event_stream: AsyncIterator[dict[str, Any]],
    ) -> None:
        """Live-edit a Discord message as AG-UI events arrive.

        Event semantics live in `channels._agui_render`; this method owns
        only Discord's native primitives (placeholder, edit, chunked
        follow-ups) via `_DiscordSink`.

        Discord supports message edits but rate-limits them, so the
        renderer coalesces deltas into at-most one edit per
        ``STREAM_EDIT_INTERVAL_SEC`` and the sink does one final atomic
        edit. Overflow past Discord's 2000-char limit fans out into
        follow-up messages.

        Failures during edit are logged but don't abort the stream — the
        final edit still tries to deliver a coherent reply.
        """
        channel = await self._resolve_channel(chat_id)
        if channel is None:
            logger.warning("discord send_streaming: could not resolve channel id=%s", chat_id)
            return

        renderer = AGUIChannelRenderer(
            _DiscordSink(self, channel),
            max_message_length=DISCORD_MAX_MESSAGE_LENGTH,
            edit_interval_sec=STREAM_EDIT_INTERVAL_SEC,
            strip_final=True,
            # Resolved here, not inside the renderer, so the edit-throttle
            # clock stays patchable at `channels.discord.time.monotonic` —
            # the seam the streaming tests have used since the adapter shipped.
            monotonic=time.monotonic,
        )
        await renderer.run(event_stream)

    # --- slash-command registration --------------------------------------

    async def register_slash_commands(self, guild_id: str) -> bool:
        """Register `/ask`, `/skill`, `/skills`, `/help` for `guild_id`.

        Idempotent: a Firestore flag at ``bot_state/discord_{guild_id}``
        records the last successful registration timestamp; subsequent
        calls within ``SLASH_REGISTRATION_TTL`` no-op. This lets every
        Cloud Run instance call this at startup without redoing the work
        and without racing.

        Returns True if registration was performed, False if it was
        skipped due to the lock.
        """
        if not self._application_id or not self._token:
            logger.warning("discord register_slash_commands: missing DISCORD_APPLICATION_ID or DISCORD_TOKEN; skipping")
            return False

        lock_doc_id = f"discord_{guild_id}"
        lock = get_document("bot_state", lock_doc_id)
        if lock and lock.get("registered_at"):
            try:
                registered_at = self._parse_ts(lock["registered_at"])
                if datetime.now(UTC) - registered_at < SLASH_REGISTRATION_TTL:
                    logger.info("discord register_slash_commands: lock fresh for guild=%s", guild_id)
                    return False
            except (TypeError, ValueError):
                logger.debug("discord register: bad lock timestamp, will re-register")

        url = f"https://discord.com/api/v10/applications/{self._application_id}/guilds/{guild_id}/commands"
        headers = {"Authorization": f"Bot {self._token}"}
        body = self._slash_command_spec()

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(url, headers=headers, json=body)
            resp.raise_for_status()

        set_document(
            "bot_state",
            lock_doc_id,
            {"registered_at": datetime.now(UTC).isoformat(), "guild_id": guild_id},
        )
        logger.info("discord register_slash_commands: registered %d commands for guild=%s", len(body), guild_id)
        return True

    # --- helpers ---------------------------------------------------------

    def _extract_command_text(self, command_name: str, data: dict[str, Any]) -> str | None:
        """Map a Discord slash command interaction into framework text.

        `/ask <question>` is the natural-language entry point; everything
        else (`/skill`, `/skills`, `/help`, `/clear`) maps to the
        framework's command-prefix syntax so the parser handles them
        uniformly.

        Returns None for unknown slash commands so `parse_inbound` can
        log + skip rather than dispatch garbage.
        """
        if command_name == "ask":
            options = data.get("options") or []
            for opt in options:
                if opt.get("name") in ("question", "message", "text") or len(options) == 1:
                    return str(opt.get("value", "")).strip()
            return ""

        if command_name in self._FRAMEWORK_SLASH_COMMANDS:
            framework_cmd = self._FRAMEWORK_SLASH_COMMANDS[command_name]
            options = data.get("options") or []
            args = [str(opt.get("value", "")).strip() for opt in options if opt.get("value") is not None]
            if args:
                # Quote args with spaces so CommandParser's shlex split keeps them together.
                quoted = [f'"{a}"' if " " in a else a for a in args]
                return f"{framework_cmd} {' '.join(quoted)}"
            return framework_cmd

        return None

    async def _resolve_channel(self, chat_id: str) -> Any | None:
        """Get a discord.py channel object, falling back to fetch on cache miss."""
        if self.client is None:
            return None
        try:
            channel_id_int = int(chat_id)
        except (TypeError, ValueError):
            logger.warning("discord _resolve_channel: chat_id %r is not numeric", chat_id)
            return None

        channel = self.client.get_channel(channel_id_int)
        if channel is not None:
            return channel
        # Cache miss — fetch from API. May fail with discord.NotFound, etc.;
        # callers log and move on.
        try:
            return await self.client.fetch_channel(channel_id_int)
        except Exception:
            logger.exception("discord _resolve_channel: fetch_channel failed for %s", chat_id)
            return None

    async def _auto_thread(self, message: Any, content: str) -> Any:
        """Create (or return existing) thread for a guild-channel mention."""
        preview = (content[:40] or "chat").strip() or "chat"
        thread_name = f"aitana-zzz - {preview}"
        return await message.channel.create_thread(name=thread_name, message=message)

    async def _safe_edit(self, message: Any, text: str) -> None:
        """Edit message; swallow + log Discord API errors so the stream keeps running."""
        try:
            await message.edit(content=text or "(empty)")
        except Exception:
            logger.exception("discord _safe_edit: edit failed")

    @staticmethod
    def _parse_ts(value: Any) -> datetime:
        """Robust ISO-or-datetime parse for the registration lock field."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            # Firestore returns ISO 8601; tolerate the trailing Z.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise ValueError(f"unsupported timestamp: {value!r}")

    def _slash_command_spec(self) -> list[dict[str, Any]]:
        """The set of slash commands the bot registers per guild.

        Returned as a list of Discord Application Command objects ready to
        PUT against `/applications/{id}/guilds/{gid}/commands`. The set is
        intentionally minimal — `/ask` is the workhorse; the rest mirror
        the framework's text commands.
        """
        return [
            {
                "name": "ask",
                "type": 1,
                "description": "Ask the assistant a question",
                "options": [
                    {
                        "name": "question",
                        "description": "What would you like to ask?",
                        "type": 3,  # STRING
                        "required": True,
                    }
                ],
            },
            {
                "name": "skill",
                "type": 1,
                "description": "Switch your default skill",
                "options": [
                    {
                        "name": "name",
                        "description": "Skill slug or title",
                        "type": 3,
                        "required": True,
                    }
                ],
            },
            {
                "name": "skills",
                "type": 1,
                "description": "List available skills",
            },
            {
                "name": "help",
                "type": 1,
                "description": "Show available commands",
            },
        ]


# Convenience for slash-command webhook callers: Discord requires the
# endpoint to respond with `{"type": 1}` on a PING. The framework returns
# `{"ok": True, "skipped": True}` instead, which Discord rejects. Channels
# that need the PING ack can override `BaseChannel.handle_webhook`, but
# the simplest fix is for the route to detect type=1 and answer
# accordingly — addressed in v6.1.0 sprint follow-up.
def discord_ping_response() -> dict[str, int]:
    """Build the JSON body Discord expects for a successful PING ack."""
    return {"type": 1}


class _DiscordSink(ChannelSink):
    """Discord's native presentation primitives for the shared renderer.

    Holds the placeholder message the renderer edits as deltas arrive.
    Everything here is Discord-specific; nothing here knows about AG-UI
    event names.
    """

    def __init__(self, adapter: DiscordChannel, channel: Any) -> None:
        self._adapter = adapter
        self._channel = channel
        self._placeholder: Any = None

    async def begin(self) -> None:
        """Post the placeholder the rest of the run edits in place."""
        self._placeholder = await self._channel.send("Thinking...")

    async def update_text(self, text: str) -> None:
        await self._adapter._safe_edit(self._placeholder, text)

    async def show_progress(self, label: str) -> str | None:
        """Discord has no progress affordance — splice italic text inline."""
        return f"\n_Working with {label}..._\n"

    async def render_surface(self, surface: Any) -> str | None:
        """Render an A2UI surface as a Discord embed.

        Sent as a follow-up message rather than folded into the streaming
        placeholder, so the surface survives the final text edit.

        Returns None on success (rendered natively, reply text untouched).
        On any failure returns flattened text so the renderer splices it
        into the reply — a surface degrades, it never vanishes.
        """
        try:
            embed_dict = surface_to_embed(surface)
        except Exception:
            logger.exception("discord render_surface: A2UI projection failed")
            return surface_to_text(surface)

        if embed_dict is None:
            return surface_to_text(surface)

        try:
            import discord as discord_lib

            await self._channel.send(embed=discord_lib.Embed.from_dict(embed_dict))
        except Exception:
            # Embed rejected (limits, transport) — fall back to text rather
            # than losing the surface entirely.
            logger.exception("discord render_surface: embed send failed; falling back to text")
            return surface_to_text(surface)
        return None

    async def show_error(self, text: str) -> None:
        """Replace the placeholder so a failure never reads as 'Thinking...'."""
        await self._adapter._safe_edit(self._placeholder, text)

    async def finish(self, text: str) -> None:
        """Final atomic edit; overflow past 2000 chars fans out as follow-ups."""
        first_chunk, *rest = chunk_message(text, max_length=DISCORD_MAX_MESSAGE_LENGTH) or [text]
        await self._adapter._safe_edit(self._placeholder, first_chunk)
        for chunk in rest:
            await self._channel.send(chunk)


__all__ = [
    "DISCORD_MAX_MESSAGE_LENGTH",
    "DiscordChannel",
    "discord_ping_response",
]


# `json` import retained for future enrichment (citation embeds) without
# triggering UP/I lint cycles in tests that import this module.
_ = json
