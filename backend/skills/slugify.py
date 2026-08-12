"""Slug generation for skill-friendly URLs.

`/chat/@{owner_id}/{slug}` URLs need a stable, owner-scoped slug per skill.
Slugs are auto-generated from the skill's name on create and surfaced in
settings as a (later) editable field.
"""

from __future__ import annotations

import re
import unicodedata

from db import firestore as fs
from db.models import _SLUG_PATTERN, RESERVED_SLUGS

_COLLECTION = "skills"
_MAX_LEN = 60
_MIN_LEN = 3
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TRIM_HYPHENS = re.compile(r"^-+|-+$")


def slugify(name: str) -> str:
    """Convert a free-form name to a URL-safe kebab-case slug.

    Falls back to ``"skill"`` if the name contains nothing slugifiable
    (e.g. only emoji or punctuation).
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower()
    kebab = _NON_ALNUM.sub("-", lowered)
    trimmed = _TRIM_HYPHENS.sub("", kebab)
    if len(trimmed) > _MAX_LEN:
        trimmed = _TRIM_HYPHENS.sub("", trimmed[:_MAX_LEN])
    # Reserved-word suffix runs before the short-input fallback so that empty /
    # punctuation-only input falls back to a plain "skill", not "skill-skill".
    if trimmed in RESERVED_SLUGS:
        trimmed = f"{trimmed}-skill"
    if len(trimmed) < _MIN_LEN:
        return "skill"
    if not _SLUG_PATTERN.match(trimmed):
        return "skill"
    return trimmed


def unique_slug(owner_id: str, base: str, exclude_skill_id: str | None = None) -> str:
    """Return ``base`` if free in ``owner_id``'s namespace, else append ``-2``, ``-3``, ...

    ``exclude_skill_id`` lets the owner re-save their own slug on PUT without
    colliding with themselves.
    """
    if not _slug_taken(owner_id, base, exclude_skill_id):
        return base
    n = 2
    while True:
        candidate = f"{base[: _MAX_LEN - len(str(n)) - 1]}-{n}"
        if not _slug_taken(owner_id, candidate, exclude_skill_id):
            return candidate
        n += 1


def _slug_taken(owner_id: str, slug: str, exclude_skill_id: str | None) -> bool:
    docs = fs.query_documents(
        _COLLECTION,
        filters=[("ownerId", "==", owner_id), ("slug", "==", slug)],
        limit=2,
    )
    for doc in docs:
        if exclude_skill_id and doc.get("skillId") == exclude_skill_id:
            continue
        return True
    return False
