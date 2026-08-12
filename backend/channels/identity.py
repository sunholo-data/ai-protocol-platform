"""Channel-to-Firebase identity resolution.

Maps channel-native user IDs (Telegram user ID, email address, Discord
snowflake) to Firebase UIDs via Firestore `channel_identities/{channel}_{user_id}`.

A separate collection (rather than per-channel subcollections of a user)
keeps adversarial cross-channel lookups inexpensive — given a Discord ID,
a single read tells you whether that user has a v6 mapping at all.

Schema:

    channel_identities/{channel}_{channel_user_id}:
        channel: "discord"
        channel_user_id: "847239..."
        firebase_uid: "abc123"
        email: "user@example.com"        # optional, populated when known
        domain: "example.com"            # derived from email
        group_tags: ["foo"]              # mirror of custom claim, advisory
        created_at: ts
        last_seen_at: ts

Group tags are stored advisory-only; the authoritative copy is the
Firebase custom claim. Channels do not grant privileges via group_tags
— only the auth.firebase_auth.get_current_user path does that.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.firestore import get_client

logger = logging.getLogger(__name__)

COLLECTION = "channel_identities"


def _doc_id(channel: str, channel_user_id: str) -> str:
    """Build the deterministic Firestore document ID.

    `channel` is restricted to lowercase ASCII (`a-z`, hyphen) by
    convention; `channel_user_id` is opaque but must not contain `/`
    (would break path semantics).
    """
    if "/" in channel_user_id:
        # Firestore document IDs cannot contain `/`. Channels with such
        # IDs (none currently known) should percent-encode before calling.
        raise ValueError(f"channel_user_id contains '/': {channel_user_id!r}")
    return f"{channel}_{channel_user_id}"


class IdentityResolver:
    """Stateless resolver. All entry points are classmethods.

    Channels never instantiate this; they call `IdentityResolver.resolve()`.
    """

    @classmethod
    async def resolve(cls, channel: str, channel_user_id: str) -> str | None:
        """Look up the Firebase UID for a channel-native user identifier.

        Returns the UID on hit, None on miss. The caller (typically
        `BaseChannel.on_unknown_user`) decides what to do on miss —
        auto-create, request allowlist approval, or reject.

        Side effect: updates `last_seen_at` on hit so admins can sweep
        cold mappings.
        """
        doc_id = _doc_id(channel, channel_user_id)
        client = get_client()
        snap = client.collection(COLLECTION).document(doc_id).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        uid = data.get("firebase_uid")
        if not uid:
            logger.warning("channel_identities/%s exists but has no firebase_uid", doc_id)
            return None

        # Best-effort last_seen_at touch — failure here must not block
        # the resolve, so we catch broadly and log.
        try:
            client.collection(COLLECTION).document(doc_id).update({"last_seen_at": datetime.now(UTC)})
        except Exception:
            logger.debug("Failed to touch last_seen_at for %s", doc_id, exc_info=True)

        return uid

    @classmethod
    async def auto_create(
        cls,
        channel: str,
        channel_user_id: str,
        *,
        email: str | None = None,
    ) -> str:
        """Create a fresh `channel_identities` mapping for an unknown user.

        Default policy used by `BaseChannel.on_unknown_user`. Channels
        that want gated onboarding (Discord guild allowlist, etc.)
        override `on_unknown_user` to return None instead.

        The Firebase UID is derived from the channel ID (`{channel}-{user_id}`)
        so subsequent webhooks resolve to the same identity without a
        re-create race. This is *not* a real Firebase Auth account — it
        is a v6-internal identity used to scope per-user state. Channels
        that need a real Firebase account should override.
        """
        doc_id = _doc_id(channel, channel_user_id)
        firebase_uid = f"channel-{doc_id}"
        domain = email.split("@", 1)[1] if email and "@" in email else ""

        now = datetime.now(UTC)
        record = {
            "channel": channel,
            "channel_user_id": channel_user_id,
            "firebase_uid": firebase_uid,
            "email": email or "",
            "domain": domain,
            "group_tags": [],
            "created_at": now,
            "last_seen_at": now,
        }

        client = get_client()
        client.collection(COLLECTION).document(doc_id).set(record)
        logger.info("auto-created channel_identity %s -> %s", doc_id, firebase_uid)
        return firebase_uid


__all__ = ["COLLECTION", "IdentityResolver"]
