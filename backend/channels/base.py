"""Channel framework — `BaseChannel` ABC + Pydantic message models.

Every messaging-channel adapter subclasses `BaseChannel` and implements
three abstract methods (`verify_webhook`, `parse_inbound`, `send`). The
framework provides the shared plumbing: identity resolution, command
parsing, attachment handling, skill dispatch, audit-log metadata.

The flow inside `handle_webhook`:

    verify_webhook  →  parse_inbound  →  IdentityResolver  →
    CommandParser   →  select_skill   →  AttachmentPipeline →
    process_skill_request → send

A new adapter is ~80-120 LOC because identity / commands / attachments /
webhook plumbing are all inherited. See `docs/design/v6.1.0/channels.md`
for the design and `docs/integrations/channels-adapter-howto.md` (M5)
for a walkthrough.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, ClassVar, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from channels.attachments import Attachment, AttachmentPipeline
from channels.commands import Command, CommandParser
from channels.identity import IdentityResolver

logger = logging.getLogger(__name__)


class InboundMessage(BaseModel):
    """A normalised inbound message from any channel.

    Adapters' `parse_inbound` returns one of these per actionable webhook
    event. Non-actionable events (typing indicators, message-edited acks,
    delivery receipts) return `None` and the framework short-circuits.
    """

    model_config = ConfigDict(frozen=True)

    channel_user_id: str = Field(
        description="Channel-native user identifier (e.g., Telegram user ID, email address, Discord snowflake)"
    )
    channel_chat_id: str = Field(description="Channel-native chat/thread identifier where the reply should go")
    text: str = Field(description="The user's message text, prefix-stripped if applicable")
    attachments: list[Attachment] = Field(default_factory=list)
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Original webhook payload, retained for debugging only"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Channel-specific fields (guild_id, subject, in_reply_to, ...)"
    )


class OutboundMessage(BaseModel):
    """A message ready to send via a channel's `send()` method.

    `format` is a hint to the adapter; not every channel honours every
    format. Plain text is always safe.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    format: Literal["plain", "html", "markdown"] = "plain"
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- BaseChannel ABC -------------------------------------------------------


class BaseChannel(ABC):
    """Abstract base for messaging-channel adapters.

    Subclasses MUST set the class variable `name` (used as the URL segment
    in `/api/{name}/webhook` and as the audit-log channel tag) and MUST
    implement `verify_webhook`, `parse_inbound`, and `send`.

    Subclasses MAY override `select_skill`, `on_unknown_user`,
    `command_prefix`, `max_attachment_size`, `supports_streaming`, and
    `send_streaming` (the last two together — setting the flag without
    overriding the method degrades to collect-then-send).

    Adapters MUST NOT call `process_skill_request` directly — the
    framework's `handle_webhook` is the single integration point so the
    API boundary stays clean. This is enforced by convention, not code,
    because Python ABCs cannot forbid method calls.
    """

    # Class-level configuration. Subclasses override as needed.
    name: ClassVar[str] = ""
    command_prefix: ClassVar[str] = "/"
    max_attachment_size: ClassVar[int] = 1024 * 1024  # 1 MB default
    supports_streaming: ClassVar[bool] = False

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set the `name` class attribute")

    # --- adapter MUST implement -------------------------------------------

    @abstractmethod
    async def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Verify the webhook came from the expected sender.

        Implementations check signature headers against the channel's
        signing secret. Return True to allow, False to reject (the
        framework will respond 401).
        """

    @abstractmethod
    async def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        """Parse a channel-native webhook payload into an `InboundMessage`.

        Return None for non-actionable events (typing indicators, edit
        acks, delivery receipts) — the framework will skip them silently.
        """

    @abstractmethod
    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send `message` to `chat_id` via the channel's API.

        Implementations handle channel-specific length limits (e.g.,
        Telegram 4096 chars, Discord 2000) by chunking internally.
        """

    # --- adapter MAY override --------------------------------------------

    async def select_skill(self, msg: InboundMessage, firebase_uid: str) -> str | None:
        """Choose which skill handles `msg`.

        Default: look up the user's stored default skill. Channels with
        richer per-message routing (e.g., email's `[SkillName]` subject
        prefix) override this. Return None to fall back to the general
        assistant.
        """
        # Late import keeps base.py importable without a configured
        # Firestore client (e.g., during type-checking or schema dumps).
        from channels._default_skill import get_user_default_skill

        return await get_user_default_skill(firebase_uid)

    async def on_unknown_user(self, msg: InboundMessage) -> str | None:
        """Decide what to do when the inbound user has no mapping.

        Default: auto-create a `channel_identities` mapping with a fresh
        Firebase UID derived from the channel user id. Override to
        require an explicit allowlist check (Discord guild membership,
        SAML group, etc.) — return None to reject.
        """
        return await IdentityResolver.auto_create(self.name, msg.channel_user_id)

    # --- framework provides; subclasses do not override ------------------

    async def handle_webhook(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Run the full inbound flow for one webhook delivery.

        The single integration point between any channel and the v6
        skill system. Subclasses get this for free by inheriting from
        `BaseChannel`. Adapters with non-webhook transports (Discord
        gateway, Slack RTM) build an `InboundMessage` themselves and
        call `_dispatch_inbound` instead — same downstream flow.

        Returns a JSON-serialisable dict for the webhook HTTP response
        body. Common shapes:
            {"ok": true}                          # processed normally
            {"ok": true, "skipped": true}         # non-actionable event
            {"ok": true, "command": "skill"}      # framework command
            {"ok": true, "rejected": "unknown_user"}
        """
        if not await self.verify_webhook(headers, body):
            raise HTTPException(status_code=401, detail=f"{self.name} webhook verification failed")

        inbound = await self.parse_inbound(payload)
        if inbound is None:
            return {"ok": True, "skipped": True}

        return await self._dispatch_inbound(inbound)

    async def _dispatch_inbound(self, inbound: InboundMessage) -> dict[str, Any]:
        """Run the post-parse flow for a normalised `InboundMessage`.

        Shared by `handle_webhook` (webhook arrival) and adapter gateway
        handlers (Discord's `on_message`, Slack RTM, etc.). The single
        source of truth for: identity resolution → command parsing →
        attachment upload → skill selection → invoke → send.

        Verify + parse are NOT done here — webhooks do those before
        calling; gateway-style transports do their own trust check
        (e.g., the gateway's TLS auth) and build the InboundMessage
        directly.

        `inbound.metadata` is forwarded into the `OutboundMessage` the
        framework constructs, so adapters with reply-threading
        semantics (email's `In-Reply-To`, etc.) can read it from
        `OutboundMessage.metadata` inside `send()`.
        """
        firebase_uid = await IdentityResolver.resolve(self.name, inbound.channel_user_id)
        if firebase_uid is None:
            firebase_uid = await self.on_unknown_user(inbound)
            if firebase_uid is None:
                logger.info("channel=%s rejected unknown user channel_user_id=%s", self.name, inbound.channel_user_id)
                return {"ok": True, "rejected": "unknown_user"}

        # Command parsing comes BEFORE skill selection so that `/skill foo`
        # always works regardless of what `select_skill()` would otherwise
        # return.
        if cmd := CommandParser.parse(inbound.text, prefix=self.command_prefix):
            return await self._handle_command(cmd, firebase_uid, inbound)

        artifact_ids = await AttachmentPipeline.upload(
            inbound.attachments,
            firebase_uid,
            max_size=self.max_attachment_size,
        )

        skill_id = await self.select_skill(inbound, firebase_uid)
        if skill_id is None:
            skill_id = "general-assistant"

        if self.supports_streaming:
            # The adapter renders the AG-UI stream itself (live message
            # edits, progress, errors). It owns delivery — the framework
            # does NOT also call `send`, or the reply would double-post.
            await self.send_streaming(
                inbound.channel_chat_id,
                self._stream_skill(
                    skill_id=skill_id,
                    firebase_uid=firebase_uid,
                    inbound=inbound,
                    attachment_ids=artifact_ids,
                ),
            )
            return {"ok": True, "streamed": True}

        response_text = await self._invoke_skill(
            skill_id=skill_id,
            firebase_uid=firebase_uid,
            inbound=inbound,
            attachment_ids=artifact_ids,
        )
        await self.send(
            inbound.channel_chat_id,
            OutboundMessage(text=response_text, metadata=inbound.metadata),
        )
        return {"ok": True}

    async def _handle_command(
        self,
        cmd: Command,
        firebase_uid: str,
        inbound: InboundMessage,
    ) -> dict[str, Any]:
        """Execute a framework-level command (`/skill`, `/skills`, `/help`, `/clear`).

        Replies via `self.send` so the user sees command output in-channel.
        Override to extend with channel-specific commands.
        """
        from channels._commands_runtime import execute_command

        reply = await execute_command(cmd, firebase_uid, channel_name=self.name)
        await self.send(
            inbound.channel_chat_id,
            OutboundMessage(text=reply, metadata=inbound.metadata),
        )
        return {"ok": True, "command": cmd.name}

    async def _invoke_skill(
        self,
        *,
        skill_id: str,
        firebase_uid: str,
        inbound: InboundMessage,
        attachment_ids: list[str],
    ) -> str:
        """Run the skill and collect a complete response text.

        The collect-then-send path, used when `supports_streaming` is
        False (the default). `RUN_ERROR` is folded into user-visible text
        by `collect_reply`, so every non-streaming channel reports
        failures without adapter-side work.

        The skill processor handles its own access checks and audit log.
        Here we just bridge the channel's user identity to the agent loop.
        """
        from channels._skill_invoke import invoke_skill_collected

        return await invoke_skill_collected(
            skill_id=skill_id,
            firebase_uid=firebase_uid,
            message=inbound.text,
            attachment_ids=attachment_ids,
            channel_name=self.name,
            channel_metadata=inbound.metadata,
        )

    def _stream_skill(
        self,
        *,
        skill_id: str,
        firebase_uid: str,
        inbound: InboundMessage,
        attachment_ids: list[str],
    ) -> AsyncIterator[dict[str, Any]]:
        """Run the skill and expose its raw AG-UI event stream.

        The streaming counterpart of `_invoke_skill`, fed to
        `send_streaming` when the adapter sets `supports_streaming = True`.
        Not a coroutine — it returns the async generator directly so the
        adapter controls when iteration starts.
        """
        from channels._skill_invoke import stream_skill_events

        return stream_skill_events(
            skill_id=skill_id,
            firebase_uid=firebase_uid,
            message=inbound.text,
            attachment_ids=attachment_ids,
            channel_name=self.name,
            channel_metadata=inbound.metadata,
        )

    async def send_streaming(
        self,
        chat_id: str,
        event_stream: AsyncIterator[dict[str, Any]],
    ) -> None:
        """Deliver an AG-UI event stream to `chat_id`.

        Called by `_dispatch_inbound` only when `supports_streaming` is
        True. The default implementation collects the stream and sends
        one message — so declaring `supports_streaming = True` without
        overriding this degrades to the non-streaming behaviour rather
        than dropping the reply.

        Adapters override this to render progressively (Discord edits a
        placeholder message as deltas arrive). An override OWNS delivery:
        the framework will not call `send` afterwards.
        """
        from channels._skill_invoke import collect_reply

        text = await collect_reply(event_stream)
        await self.send(chat_id, OutboundMessage(text=text))


__all__ = [
    "Attachment",
    "BaseChannel",
    "InboundMessage",
    "OutboundMessage",
]
