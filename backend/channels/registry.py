"""Channel registry — single mounting point for all channel webhooks.

Channels self-register at app startup via `ChannelRegistry.register(...)`.
A single `mount_webhooks(app)` call then auto-mounts `POST /api/{name}/webhook`
for every registered channel — adapters never touch FastAPI routing.

Usage in `fast_api_app.py`:

    ChannelRegistry.register(DiscordChannel())
    ChannelRegistry.register(EmailChannel())
    ChannelRegistry.register(TelegramChannel())
    ChannelRegistry.register(WhatsAppChannel())
    ChannelRegistry.mount_webhooks(app)

The registry is also queried by the event-driven skill output router
(`ChannelRegistry.get("discord").send(...)`) — see v6.2.0
event-driven-skills.md.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from fastapi import FastAPI, Request

from channels.base import BaseChannel

logger = logging.getLogger(__name__)


class ChannelRegistryError(Exception):
    """Raised on invalid registry operations (double-register, unknown lookup)."""


class ChannelRegistry:
    """Process-singleton registry. Class methods only; no instances."""

    _channels: ClassVar[dict[str, BaseChannel]] = {}

    @classmethod
    def register(cls, channel: BaseChannel) -> None:
        """Register a channel by its `name`. Idempotent on re-register
        with the *same* instance; refuses to overwrite with a *different*
        instance to catch double-import bugs early.
        """
        existing = cls._channels.get(channel.name)
        if existing is not None and existing is not channel:
            raise ChannelRegistryError(
                f"Channel {channel.name!r} already registered with a different instance — "
                f"check for duplicate imports or registrations"
            )
        cls._channels[channel.name] = channel
        logger.info("registered channel: %s", channel.name)

    @classmethod
    def get(cls, name: str) -> BaseChannel:
        """Look up a registered channel by name. Raises if not found.

        Used by trigger systems (event-driven skills) that need to send
        a message via a specific channel without knowing which adapter.
        """
        channel = cls._channels.get(name)
        if channel is None:
            raise ChannelRegistryError(f"No channel registered with name {name!r}")
        return channel

    @classmethod
    def names(cls) -> list[str]:
        """List the registered channel names. Stable order."""
        return sorted(cls._channels.keys())

    @classmethod
    def mount_webhooks(cls, app: FastAPI) -> None:
        """Mount `POST /api/{name}/webhook` for every registered channel.

        Must be called *after* all channels are registered. Mounting is
        a one-shot operation — calling twice in the same process raises.
        """
        if getattr(app.state, "_channel_webhooks_mounted", False):
            raise ChannelRegistryError("mount_webhooks was already called on this app")
        for name, channel in cls._channels.items():
            cls._mount_one(app, name, channel)
            logger.info("mounted webhook: POST /api/%s/webhook", name)
        app.state._channel_webhooks_mounted = True

    @classmethod
    def _mount_one(cls, app: FastAPI, name: str, channel: BaseChannel) -> None:
        """Mount a single channel's webhook route.

        The closure binds `channel` so per-channel state stays distinct
        — important when the registry has Telegram + Discord + Email
        all live in the same process.
        """

        async def webhook_handler(request: Request) -> dict:
            body = await request.body()
            # Parse JSON if present; some channels (Mailgun form-encoded)
            # will set Content-Type to `application/x-www-form-urlencoded`
            # — adapters handle their own decoding from `raw` if needed.
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                payload = await request.json()
            elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                form = await request.form()
                payload = dict(form)
            else:
                # Best-effort JSON parse; if that fails, hand raw bytes
                # to the adapter as `_raw_body`.
                try:
                    payload = await request.json()
                except Exception:
                    payload = {"_raw_body": body.decode("utf-8", errors="replace")}

            return await channel.handle_webhook(payload, dict(request.headers), body)

        # Use a per-channel endpoint name so FastAPI's OpenAPI shows distinct
        # operationIds; avoids accidental "duplicate operation" warnings.
        app.post(
            f"/api/{name}/webhook",
            name=f"channel_webhook_{name}",
            tags=["channels"],
            summary=f"Webhook endpoint for the {name} channel",
        )(webhook_handler)

    # --- test affordance --------------------------------------------------

    @classmethod
    def _clear_for_tests(cls) -> None:
        """Reset the registry between tests. Not for production use."""
        cls._channels.clear()


__all__ = ["ChannelRegistry", "ChannelRegistryError"]
