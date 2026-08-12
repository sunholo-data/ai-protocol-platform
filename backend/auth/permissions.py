"""Tool-class permission enforcement.

Firestore collection: ``tool_permissions``
Document ID: user email, domain, or ``*`` (wildcard).

Document shape::

    {
        "type": "user" | "domain" | "wildcard",
        "tools": ["tool_a", "tool_b"]  or ["*"],  # "*" = all tools
        "denied": ["tool_x"],                      # optional deny list
    }

Lookup order:
    1. User-level doc (exact email match) — wins if found.
    2. Domain-level doc (e.g. ``yourcompany.com``).
    3. Wildcard doc (``*``) — global fallback.
    4. If none match → deny.

At each level, ``tools`` grants access and ``denied`` revokes it. A tool
must be in ``tools`` (or tools == ``["*"]``) AND not in ``denied``.

Cache: per ``(email, tool_name)`` pair, 60 s TTL, plain dict + timestamps.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from db import firestore as fs

logger = logging.getLogger(__name__)

COLLECTION = "tool_permissions"
_CACHE_TTL = 60  # seconds


class ToolPermissionDenied(Exception):
    """Raised when a user is not permitted to invoke a tool."""

    def __init__(self, user_email: str, tool_name: str) -> None:
        self.user_email = user_email
        self.tool_name = tool_name
        super().__init__(f"user {user_email} is not permitted to use tool {tool_name}")


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def _cache_get(email: str, tool_name: str) -> bool | None:
    """Return cached result or None on miss/expiry."""
    key = (email, tool_name)
    entry = _cache.get(key)
    if entry is None:
        return None
    allowed, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return allowed


def _cache_set(email: str, tool_name: str, allowed: bool) -> None:
    _cache[(email, tool_name)] = (allowed, time.monotonic())


def clear_cache() -> None:
    """Flush the permission cache (useful for tests)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Permission evaluation
# ---------------------------------------------------------------------------


def _doc_allows(doc: dict[str, Any], tool_name: str) -> bool:
    """Evaluate a single permission document against *tool_name*."""
    tools: list[str] = doc.get("tools", [])
    denied: list[str] = doc.get("denied", [])
    if tool_name in denied:
        return False
    return "*" in tools or tool_name in tools


def can_use_tool(user_email: str, user_domain: str, tool_name: str) -> bool:
    """Check whether *user_email* (with *user_domain*) may invoke *tool_name*.

    Hits Firestore at most 3 times per miss (user, domain, wildcard), cached
    for 60 s thereafter.
    """
    cached = _cache_get(user_email, tool_name)
    if cached is not None:
        return cached

    # 1. User-level (guard: empty string → invalid Firestore doc path)
    user_doc = fs.get_document(COLLECTION, user_email) if user_email else None
    if user_doc is not None:
        result = _doc_allows(user_doc, tool_name)
        _cache_set(user_email, tool_name, result)
        logger.debug("perm: user-level %s → %s for %s", user_email, result, tool_name)
        return result

    # 2. Domain-level
    if user_domain:
        domain_doc = fs.get_document(COLLECTION, user_domain)
        if domain_doc is not None:
            result = _doc_allows(domain_doc, tool_name)
            _cache_set(user_email, tool_name, result)
            logger.debug("perm: domain-level %s → %s for %s", user_domain, result, tool_name)
            return result

    # 3. Wildcard fallback
    wildcard_doc = fs.get_document(COLLECTION, "*")
    if wildcard_doc is not None:
        result = _doc_allows(wildcard_doc, tool_name)
        _cache_set(user_email, tool_name, result)
        logger.debug("perm: wildcard → %s for %s", result, tool_name)
        return result

    # 4. No rule → deny
    _cache_set(user_email, tool_name, False)
    logger.debug("perm: no rule → deny %s for %s", user_email, tool_name)
    return False
