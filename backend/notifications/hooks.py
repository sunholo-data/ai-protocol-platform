"""When to notify, and what the notification may say.

PRIVACY (CLAUDE.md hard rule): pushes travel through Apple's/Google's push
services and end up on a lock screen. Web Push encrypts the payload end to end,
but the payload still carries ONLY a skill's display name and a link — never
the message, the answer or a document name. The app loads the content over the
authenticated connection when the notification is tapped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from notifications import store
from notifications.webpush import push_config, send

logger = logging.getLogger(__name__)

# Fire-and-forget tasks are kept here so they aren't garbage-collected
# mid-flight (asyncio holds only weak references to tasks).
_tasks: set[asyncio.Task] = set()


def payload(title: str, body: str, url: str, tag: str) -> dict[str, str]:
    """The ENTIRE push payload. Keep it to names and a link."""
    return {"title": title[:80], "body": body[:120], "url": url, "tag": tag[:64]}


def chat_url(skill_id: str, session_id: str) -> str:
    return f"/chat/{skill_id}?session={session_id}" if session_id else f"/chat/{skill_id}"


async def _deliver(targets: list[tuple[str, dict[str, Any]]], message: dict[str, str]) -> int:
    sent = 0
    for uid, sub in targets:
        result = await send(sub, message)
        if result == "ok":
            sent += 1
        elif result == "gone" and uid:
            await asyncio.to_thread(store.remove, uid, sub.get("endpoint", ""))
    return sent


def _schedule(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _skill_name(skill_id: str) -> tuple[str, str]:
    """(display name, canonical id) — the link uses the canonical id."""
    from skills.skill_config import get_skill, resolve_skill_ref

    def _lookup():
        return get_skill(skill_id) or resolve_skill_ref(skill_id)

    try:
        cfg = await asyncio.to_thread(_lookup)
    except Exception:
        cfg = None
    if cfg is None:
        return "your assistant", skill_id
    name = getattr(cfg, "display_name", None) or getattr(cfg, "name", None) or "your assistant"
    return str(name), str(getattr(cfg, "skill_id", None) or skill_id)


def notify_reply_ready(uid: str, skill_id: str, session_id: str, *, failed: bool = False) -> None:
    """The user's reply finished after their app disconnected (e.g. iOS
    suspended it). Scheduled; returns immediately; never raises."""
    if not push_config()[0] or not uid:
        return

    async def _run():
        targets = [(uid, s) for s in await asyncio.to_thread(store.for_uid, uid)]
        if not targets:
            return
        name, canonical = await _skill_name(skill_id)
        message = payload(
            "Reply failed" if failed else "Reply ready",
            f"in {name}",
            chat_url(canonical, session_id),
            f"session-{session_id}",
        )
        await _deliver(targets, message)

    _schedule(_run())


def notify_thread_collaborators(poster: str, skill_id: str, session_id: str) -> None:
    """A machine principal posted in a thread shared with people: tell each
    collaborator (the chat_sessions row's ``collaborators`` — what grants them
    access — read AFTER the run, so a thread's first turn is covered too)."""
    if not push_config()[0] or not session_id:
        return

    async def _run():
        from db.chat_sessions import get_session_index

        index = await asyncio.to_thread(get_session_index, session_id)
        emails = list(getattr(index, "collaborators", None) or [])
        if not emails:
            return
        targets: list[tuple[str, dict[str, Any]]] = []
        for email in emails:
            targets.extend(await asyncio.to_thread(store.for_email, email))
        if not targets:
            return
        name, canonical = await _skill_name(skill_id)
        message = payload(f"{poster} posted", f"in {name}", chat_url(canonical, session_id), f"session-{session_id}")
        await _deliver(targets, message)

    _schedule(_run())
