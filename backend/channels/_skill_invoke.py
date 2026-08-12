"""Bridge from channel webhook to `process_skill_request`.

Channels arrive at this layer with a Firebase UID (from
`IdentityResolver`) and a normalised `InboundMessage`. This module:

  1. Synthesises a minimal `User` + `AccessContext` (no JWT involved —
     identity already verified by the channel's webhook signature)
  2. Calls `skills.skill_processor.process_skill_request`
  3. Exposes the resulting AG-UI event stream two ways

There are two entry points over ONE underlying event stream:

  - `stream_skill_events` — yields raw AG-UI events. Streaming adapters
    (`supports_streaming = True`) consume these and render progressively.
  - `invoke_skill_collected` — folds the same stream into one string for
    collect-then-send adapters.

Both fold errors the same way, via `collect_reply`, so a `RUN_ERROR` is
user-visible on EVERY channel rather than being silently dropped (the
`"(no response)"` bug: a model outage and an empty answer were
indistinguishable). See `docs/design/v6.21.0/channels-agui-convergence.md`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from auth.access_context import build_access_context
from auth.firebase_auth import User

# User-visible text and the error mapping live in `_agui_render` (the
# presentation layer) and are re-exported here so existing call sites —
# `discord.py`, the channel tests — keep importing them from this module.
from channels._agui_render import (
    GENERIC_ERROR_TEXT,
    NO_RESPONSE_TEXT,
    AGUIChannelRenderer,
    CollectingSink,
    error_reply_text,
)

logger = logging.getLogger(__name__)


def skill_not_found_text(skill_id: str) -> str:
    """User-visible text when `skill_id` doesn't resolve."""
    return f"Skill {skill_id!r} is not available. Use /skills to list available skills."


async def stream_skill_events(
    *,
    skill_id: str,
    firebase_uid: str,
    message: str,
    attachment_ids: list[str] | None = None,
    channel_name: str,
    channel_metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the skill and yield its raw AG-UI events.

    The stream is exactly what `process_skill_request` emits —
    `RUN_STARTED`, `TEXT_MESSAGE_*`, `TOOL_CALL_START`, `CUSTOM`
    (`STAGE_PROGRESS` / `A2UI_SURFACE`), `RUN_FINISHED`, `RUN_ERROR` —
    with one addition: a missing skill is converted into a synthetic
    `RUN_ERROR` so consumers have a single failure channel to render
    instead of a Python exception plus an event stream.

    `channel_name` and `channel_metadata` are logged on every invocation
    so trace context links a channel's webhook to the skill it triggered.
    """
    # Late import to avoid circular load (skill_processor imports adk
    # which initialises Vertex AI session services on import).
    from skills.skill_processor import SkillNotFoundError, process_skill_request

    user = _build_channel_user(firebase_uid)
    access = build_access_context(user)
    logger.info(
        "channel_invoke channel=%s skill=%s uid=%s metadata_keys=%s",
        channel_name,
        skill_id,
        firebase_uid,
        sorted((channel_metadata or {}).keys()),
    )

    try:
        async for event in process_skill_request(
            skill_id=skill_id,
            user=user,
            access=access,
            session_id=session_id,
            message=message,
            document_ids=attachment_ids or None,
        ):
            yield event
    except SkillNotFoundError:
        logger.warning(
            "channel=%s skill_not_found skill_id=%s uid=%s metadata=%s",
            channel_name,
            skill_id,
            firebase_uid,
            channel_metadata,
        )
        yield {
            "type": "RUN_ERROR",
            "message": skill_not_found_text(skill_id),
            "code": "SKILL_NOT_FOUND",
        }


async def collect_reply(event_stream: AsyncIterator[dict[str, Any]]) -> str:
    """Fold an AG-UI event stream into one user-visible reply string.

    A thin wrapper over the shared renderer driving a `CollectingSink`,
    so the collect-then-send path and the streaming path classify events
    identically — including `RUN_ERROR`, which wins over partial text.

    No `edit_interval_sec` (nothing is being edited) and no
    `strip_final` (the collected path has never stripped; preserving that
    keeps the extraction behaviour-safe).
    """
    return await AGUIChannelRenderer(CollectingSink()).run(event_stream)


async def invoke_skill_collected(
    *,
    skill_id: str,
    firebase_uid: str,
    message: str,
    attachment_ids: list[str] | None = None,
    channel_name: str,
    channel_metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> str:
    """Run the skill end-to-end and return the assembled assistant text.

    The collect-then-send path used by every adapter with
    `supports_streaming = False`. Errors surface as text here, so a
    non-streaming channel gets failure reporting with no adapter work.

    `attachment_ids` are the doc IDs created by `AttachmentPipeline`;
    `process_skill_request` accepts them as `document_ids` so the agent
    can read the parsed content.
    """
    return await collect_reply(
        stream_skill_events(
            skill_id=skill_id,
            firebase_uid=firebase_uid,
            message=message,
            attachment_ids=attachment_ids,
            channel_name=channel_name,
            channel_metadata=channel_metadata,
            session_id=session_id,
        )
    )


def _enrichment_enabled() -> bool:
    """Whether channel identities resolve to their authoritative privileges.

    Read at call time, not import time, so tests and a redeploy-free flip
    both take effect. Default OFF: enabling changes an access decision.
    """
    return os.getenv("CHANNEL_IDENTITY_ENRICHMENT", "").strip().lower() in ("1", "true", "yes", "on")


def _build_channel_user(firebase_uid: str) -> User:
    """Construct a `User` for skill processing from a channel UID.

    Channels resolve their wire-format user ID to a Firebase UID via
    `IdentityResolver` (webhook signature or authenticated gateway first —
    enrichment happens strictly after that trust check). The skill
    processor then needs a richer `User` (email, domain, group_tags) for
    access checks and per-domain bucket resolution.

    **Where the tags come from matters more than that they arrive.**
    `channel_identities/{channel}_{user_id}` mirrors `group_tags`, but that
    mirror is advisory by design (`channels/identity.py`: "Channels do not
    grant privileges via group_tags") precisely so a stale or tampered
    Firestore document cannot grant access. We therefore ask
    `auth.firebase_auth.resolve_user_by_uid`, which reads the authoritative
    custom claim and applies the same derived-tag union the JWT path uses.
    Nothing here assembles tags, and nothing reads them from the mirror or
    from channel-supplied data (a Discord nickname or guild role must never
    grant access).

    Fail-closed on every unhappy path — flag off, unknown UID, Firebase
    unavailable — by returning the restricted user that matches only
    public, owner, or explicit-email-list skills.
    """
    restricted = User(uid=firebase_uid, email="", domain="", group_tags=frozenset())

    if not _enrichment_enabled():
        return restricted

    from auth.firebase_auth import resolve_user_by_uid

    resolved = resolve_user_by_uid(firebase_uid)
    if resolved is None:
        logger.info("channel identity: no authoritative record for uid=%s; staying restricted", firebase_uid)
        return restricted
    return resolved


__all__ = [
    # Re-exported from `_agui_render` for existing call sites.
    "GENERIC_ERROR_TEXT",
    "NO_RESPONSE_TEXT",
    "collect_reply",
    "error_reply_text",
    "invoke_skill_collected",
    "skill_not_found_text",
    "stream_skill_events",
]
