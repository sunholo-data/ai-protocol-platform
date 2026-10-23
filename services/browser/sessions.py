"""Session registry: one Obscura subprocess per (user, session), plus the reaper.

Sessions are keyed by user first. That is not bookkeeping — it is the isolation
boundary. Two people browsing at once must never share a cookie jar, and the
cheapest way to guarantee that is for them never to share a process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

from obscura import ObscuraSession, SessionGone

log = logging.getLogger(__name__)

# Bounds the service's own footprint. Each session is a ~20-40 MB subprocess, so
# this is the knob that decides how much of the instance we are willing to spend.
MAX_CONCURRENT_SESSIONS = int(os.environ.get("BROWSER_MAX_SESSIONS", "12"))
MAX_SESSIONS_PER_USER = int(os.environ.get("BROWSER_MAX_SESSIONS_PER_USER", "3"))

REAP_INTERVAL_SECONDS = float(os.environ.get("BROWSER_REAP_INTERVAL_SECONDS", "15"))


class TooManySessions(RuntimeError):
    """At capacity. Carries a message fit to show a person."""


class SessionRegistry:
    """Owns every live subprocess. One instance per process."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], ObscuraSession] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_loop())

    async def stop(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.close(reason="the service shut down")

    # -- acquisition -------------------------------------------------------

    async def get_or_create(self, user_id: str, session_id: str) -> ObscuraSession:
        """Return the session for this (user, session), starting one if needed."""
        key = (user_id, session_id)

        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None:
                existing.last_used_at = time.monotonic()
                return existing

            if len(self._sessions) >= MAX_CONCURRENT_SESSIONS:
                raise TooManySessions(
                    "The browser is busy with other work right now. Please try again in a moment."
                )
            # At the per-user cap, EVICT the least-recently-used session rather
            # than refuse. A browser session is a cache, not a reservation: the
            # user did not ask for one, the agent opened it, and every chat turn
            # brings a new session id. Refusing meant the 4th turn in a
            # conversation hit "you already have 3 browser sessions open" and the
            # user was locked out for the whole idle window — a self-inflicted
            # outage from a limit meant to protect the instance.
            #
            # Found 2026-09-03 by driving the skill headlessly: turn one browsed
            # fine, the login flow a few turns later could not open a page at all.
            mine = sorted(
                ((key, s) for key, s in self._sessions.items() if key[0] == user_id),
                key=lambda kv: kv[1].last_used_at,
            )
            evicted: ObscuraSession | None = None
            if len(mine) >= MAX_SESSIONS_PER_USER:
                oldest_key, oldest = mine[0]
                if oldest.in_use:
                    # Every one of their sessions is mid-call. Refusing here is
                    # correct — evicting a session under an in-flight tool call
                    # would surface as a mysterious mid-task failure.
                    raise TooManySessions(
                        "The browser is busy with your other requests right now. Please try again in a moment."
                    )
                self._sessions.pop(oldest_key, None)
                evicted = oldest

            # Reserve the slot before the (slow) spawn so two concurrent
            # requests for the same key cannot both start a subprocess.
            session = ObscuraSession(session_id=session_id, user_id=user_id)
            self._sessions[key] = session

        if evicted is not None:
            log.info(
                "browser: evicted LRU session=%s user=%s to make room (idle %.0fs)",
                evicted.session_id,
                evicted.user_id,
                time.monotonic() - evicted.last_used_at,
            )
            await evicted.close(reason="a newer browser session replaced this one")

        try:
            await session.start()
        except Exception:
            async with self._lock:
                self._sessions.pop(key, None)
            raise

        return session

    async def close(self, user_id: str, session_id: str, reason: str = "the browser session was closed") -> bool:
        async with self._lock:
            session = self._sessions.pop((user_id, session_id), None)
        if session is None:
            return False
        await session.close(reason=reason)
        return True

    # -- introspection -----------------------------------------------------

    def describe(self) -> list[dict]:
        """Live sessions, for `aiplatform browse session list` and /status."""
        now = time.monotonic()
        return [
            {
                "user_id": s.user_id,
                "session_id": s.session_id,
                "age_seconds": round(now - s.created_at, 1),
                "idle_seconds": round(now - s.last_used_at, 1),
                "peak_rss_mb": round(s.peak_rss_mb, 1),
            }
            for s in self._sessions.values()
        ]

    @property
    def count(self) -> int:
        return len(self._sessions)

    # -- reaping -----------------------------------------------------------

    async def _reap_loop(self) -> None:
        """Close sessions that are over TTL, idle, or over the RSS ceiling.

        This loop is the standing answer to upstream #783. It should almost
        never fire on memory — but "almost never" is a claim about someone
        else's unfixed bug, so the bound is enforced rather than assumed.
        """
        while True:
            try:
                await asyncio.sleep(REAP_INTERVAL_SECONDS)
                await self._reap_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - the loop must not die
                log.exception("browser: reaper iteration failed: %s", exc)

    async def _reap_once(self) -> None:
        async with self._lock:
            doomed = [(key, s, reason) for key, s in self._sessions.items() if (reason := s.over_budget())]
            for key, _, _ in doomed:
                self._sessions.pop(key, None)

        for _, session, reason in doomed:
            log.info(
                "browser: reaping session=%s user=%s reason=%s peak_rss_mb=%.0f",
                session.session_id,
                session.user_id,
                reason,
                session.peak_rss_mb,
            )
            await session.close(reason=reason)


__all__ = ["SessionRegistry", "TooManySessions", "SessionGone"]
