"""The project a chat is scoped to — one helper everything reads.

Design: docs/design/private/projects.md §6.3 (scoped chats) and §6.6 (project
memory).

The frontend sends ``forwardedProps.project_id`` when a chat is started from a
project's home (or continues one). ``skills.skill_processor`` checks the
project is the caller's, stamps ``chat_sessions.projectId`` and writes the
SESSION-scoped state key :data:`SESSION_KEY` (never ``app:`` — that would be one
value for every session of the app). A resumed chat that sends nothing gets its
project back from the row.

Everything downstream — the instruction block, memory scope, ``list_documents``
and ``knowledge_search`` — asks :func:`current_project`, which re-checks
ownership: the project must still exist under the caller's own
``users/{uid}/projects``. A project id the caller does not own, or one deleted
since, is dropped and the chat runs unscoped, so a stale or forged id can never
widen what a chat reaches or tell the user a scope is active when it is not.

The ownership read is cached per (uid, project) for 30 seconds so one turn's
several model calls cost one read; :func:`clear_cache` drops it when a project is
renamed or deleted on this instance.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

SESSION_KEY = "project_id"
_CACHE_TTL_SECONDS = 30
_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}


def owned_project(user_id: str, project_id: str | None, *, fresh: bool = False) -> dict[str, Any] | None:
    """The caller's project ``project_id``, or None when it is not theirs / gone.
    Never raises: an unreadable project is treated as no project (logged)."""
    project_id = (project_id or "").strip()
    if not user_id or not project_id:
        return None
    key = (user_id, project_id)
    now = time.monotonic()
    if not fresh:
        with _lock:
            hit = _cache.get(key)
            if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
    try:
        from db.projects import get_project

        project = get_project(user_id, project_id)
    except Exception as exc:
        log.warning("project scope: could not read project %s for %s: %s", project_id, user_id, exc)
        project = None
    with _lock:
        _cache[key] = (now, project)
    return project


def clear_cache(user_id: str | None = None, project_id: str | None = None) -> None:
    with _lock:
        if user_id is None:
            _cache.clear()
            return
        for key in [k for k in _cache if k[0] == user_id and (project_id is None or k[1] == project_id)]:
            _cache.pop(key, None)


def _caller_uid(ctx: Any) -> str:
    from tools.documents.context import resolve_caller_uid

    return resolve_caller_uid(ctx)


def scoped_project_id(ctx: Any) -> str:
    """The raw project id in session state ("" when unscoped). Not ownership-
    checked — use :func:`current_project` for anything that acts on it."""
    try:
        value = ctx.state.get(SESSION_KEY)
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def current_project(ctx: Any) -> dict[str, Any] | None:
    """The project this chat is scoped to, re-checked against the caller.

    ``ctx`` is any ADK context with ``.state`` and an invocation (ToolContext,
    CallbackContext, ReadonlyContext). Blocking (a cached Firestore read): call it
    via ``asyncio.to_thread`` from async code.
    """
    project_id = scoped_project_id(ctx)
    if not project_id:
        return None
    uid = _caller_uid(ctx)
    project = owned_project(uid, project_id)
    if project is None:
        log.warning(
            "project scope: dropping project %s — not owned by %s (or deleted); running unscoped", project_id, uid
        )
    return project


def resolve_turn_project(user_id: str, requested: str | None, stored: str | None) -> str:
    """The project id one turn runs in: the one the frontend sent, else the one on
    the chat's row — each only if the caller owns it. "" when unscoped."""
    for source, candidate in (("request", requested), ("session row", stored)):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        if owned_project(user_id, candidate, fresh=True) is not None:
            return candidate
        log.warning(
            "project scope: %s project %s is not %s's (or was deleted); ignoring it", source, candidate, user_id
        )
    return ""


__all__ = [
    "SESSION_KEY",
    "clear_cache",
    "current_project",
    "owned_project",
    "resolve_turn_project",
    "scoped_project_id",
]
