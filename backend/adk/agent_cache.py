"""TTL + LRU cache for built agents (cold-start / per-turn build cost, v6.14.0).

``create_agent_with_thinking`` ran on EVERY chat turn with no cache — rebuilding
the model chain, all tools, every MCP toolset object (a Firestore read per
server), and recursively building every ``auto``-floor delegate. Measured warm
that's ~1.4s of ``before_agent`` per turn; on a cold instance it compounds with
process import into the ~25s first-request cost.

The built agent is a pure function of ``(skill, user, access)`` — session id,
message, and document ids all arrive at RUN time (via the Runner / initial_state),
never at build time. So it is cacheable. The cache key captures everything that
changes the built agent:

  * ``skill.skill_id`` + ``skill.updated_at`` — a Studio edit bumps ``updated_at``
    (and invalidates the skill_config cache), so an edited skill misses and
    rebuilds.
  * the ``AccessContext`` (frozen, hashable) — the agent bakes in per-user
    permission/loader callbacks AND its delegate set is access-filtered, so a
    different user, domain, or group-tag set must get its own agent. A mid-session
    group-tag grant changes the key → rebuild.

TTL (60s, matching ``skills.skill_config``) bounds staleness for anything not in
the key; an LRU cap bounds growth across (users x skills). A build error is never
cached. Concurrency: a race can double-build (harmless) — worst case is redundant
work, never a wrong agent, because the key fully determines correctness.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from auth.access_context import AccessContext
from db.models import SkillConfig

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0  # seconds — matches skills.skill_config
_CACHE_MAX = 256  # bound growth across (users x skills)

# key -> (timestamp, built agent-or-router). OrderedDict for LRU eviction.
_cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()


def clear() -> None:
    """Empty the cache (tests / explicit invalidation)."""
    _cache.clear()


def _key(skill: SkillConfig, access: AccessContext) -> tuple:
    # AccessContext is a frozen dataclass (group_tags is a frozenset), so it is
    # hashable and safe to embed directly.
    return (skill.skill_id, skill.updated_at, access)


def get_or_build(skill: SkillConfig, access: AccessContext, builder: Callable[[], Any]) -> tuple[Any, bool]:
    """Return ``(agent, cache_hit)`` for ``(skill, access)``, building on miss.

    ``builder`` is called only on a miss and its result cached. If the key can't
    be hashed for any reason, fall back to a fresh build (never cache) so a cache
    quirk can never break a turn.
    """
    try:
        key = _key(skill, access)
        hash(key)
    except Exception as exc:  # pragma: no cover - AccessContext is hashable
        logger.debug("agent_cache: unhashable key (%s) — building uncached", type(exc).__name__)
        return builder(), False

    now = time.time()
    entry = _cache.get(key)
    if entry is not None and (now - entry[0]) < _CACHE_TTL:
        _cache.move_to_end(key)
        return entry[1], True

    built = builder()  # a raise here propagates BEFORE we cache — errors never stick
    _cache[key] = (now, built)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)  # evict least-recently-used
    return built, False


__all__ = ["clear", "get_or_build"]
