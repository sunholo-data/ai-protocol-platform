"""Per-user default skill lookup for the channel framework.

A user's default skill is the skill that handles channel messages when
the user hasn't explicitly selected one (no `/skill` command, no
`[SkillName]` subject prefix). Stored at:

    user_settings/{firebase_uid}:
        default_skill_id: "general-assistant"

Returns None when the user has no preference set; `BaseChannel`
fallbacks to the General Assistant template skill in that case.
"""

from __future__ import annotations

import logging

from db.firestore import get_document, set_document

logger = logging.getLogger(__name__)

_COLLECTION = "user_settings"
_FIELD = "default_skill_id"


async def get_user_default_skill(firebase_uid: str) -> str | None:
    """Return the user's stored default skill id, or None if unset."""
    data = get_document(_COLLECTION, firebase_uid)
    if not data:
        return None
    skill_id = data.get(_FIELD)
    if not skill_id:
        return None
    return str(skill_id)


async def set_user_default_skill(firebase_uid: str, skill_id: str) -> None:
    """Persist `skill_id` as the user's default for future channel messages."""
    existing = get_document(_COLLECTION, firebase_uid) or {}
    existing[_FIELD] = skill_id
    set_document(_COLLECTION, firebase_uid, existing)
    logger.info("user_settings: set default_skill_id=%s for uid=%s", skill_id, firebase_uid)


__all__ = ["get_user_default_skill", "set_user_default_skill"]
