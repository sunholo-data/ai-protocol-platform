"""One Obscura subprocess, its lifetime, and the JSON-RPC link to it.

The whole security and stability argument of this service rests on two choices
made here, so they are worth restating where the code is:

1. **stdio, never `serve`.** `obscura mcp` reads JSON-RPC on stdin and writes it
   on stdout. It opens NO listening port, which is why a binary with no
   authentication of its own is safe to run: there is nothing to authenticate.
   It also means upstream #783 — `obscura serve` growing to 2.3 GB over hours —
   cannot reach us, because a process that lives for one task never gets there.

2. **Hard bounds, enforced from outside.** A TTL and an RSS ceiling, checked by
   a reaper. Not because we expect to need them today, but because the leak is
   real, unfixed upstream, and the honest mitigation for "a dependency leaks" is
   a bound you own rather than a version you hope about.

Flags are pinned here rather than passed in. `--allow-private-network` and
`--allow-file-access` are the two that would undo the isolation story, so they
are not parameters — there is no code path that can set them.
"""

from __future__ import annotations

import anyio
import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger(__name__)

OBSCURA_BIN = os.environ.get("OBSCURA_BIN", "obscura")

# A session is a task, not a workday. Half an hour is long enough for a person
# to work through a multi-step flow in chat, and far short of the HOURS upstream
# #783 needs to leak — the RSS cap below is the real bound either way.
SESSION_TTL_SECONDS = float(os.environ.get("BROWSER_SESSION_TTL_SECONDS", "1800"))

# Idle reap. Ten minutes, not two: a person reading a page and typing a
# follow-up routinely takes longer than two minutes, and reaping under them
# drops the cookie jar mid-task — which looks like "it forgot I logged in".
# Sized for a human in a chat, not for a batch job.
SESSION_IDLE_SECONDS = float(os.environ.get("BROWSER_SESSION_IDLE_SECONDS", "600"))

# Measured idle RSS is ~20 MB; a heavy page is well under 200 MB. 600 MB is
# "something is wrong", not "a big page".
MAX_RSS_MB = float(os.environ.get("BROWSER_MAX_RSS_MB", "600"))

# A single tool call that has not returned by now is not going to.
CALL_TIMEOUT_SECONDS = float(os.environ.get("BROWSER_CALL_TIMEOUT_SECONDS", "60"))

# How long to wait for the subprocess to come up and complete the MCP handshake.
STARTUP_TIMEOUT_SECONDS = float(os.environ.get("BROWSER_STARTUP_TIMEOUT_SECONDS", "30"))


class SessionGone(RuntimeError):
    """The subprocess was reaped or died. Carries a reason fit to show a person."""


def _obscura_args() -> list[str]:
    """Pinned command line.

    `--obey-robots` is on by default: we browse on behalf of an authenticated
    user, not as a scraping product.

    Deliberately absent, and not parameterised:
      --allow-private-network : would let a page reach the metadata server and
                                undo the entire isolation argument.
      --allow-file-access     : would let a page read the container filesystem.
      --workers               : `serve` only, and we do not run `serve`.
    """
    args = ["mcp"]
    if os.environ.get("BROWSER_OBEY_ROBOTS", "1") not in ("0", "false", "no"):
        args.append("--obey-robots")
    if os.environ.get("BROWSER_STEALTH", "1") not in ("0", "false", "no"):
        args.append("--stealth")
    return args


@dataclass
class ObscuraSession:
    """A live subprocess plus the MCP client session talking to it."""

    session_id: str
    user_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    peak_rss_mb: float = 0.0

    _session: ClientSession | None = field(default=None, repr=False)
    _runner: asyncio.Task | None = field(default=None, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _pid: int | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _closed_reason: str | None = field(default=None, repr=False)
    _in_use: bool = field(default=False, repr=False)

    @property
    def in_use(self) -> bool:
        """True while a tool call is in flight.

        The registry checks this before evicting: dropping a session under a
        running call would surface as a mysterious mid-task failure rather than
        the invisible recycle eviction is meant to be.
        """
        return self._in_use

    async def _run(self, ready: asyncio.Event, failure: list) -> None:
        """Own the subprocess context for the session's whole life.

        THIS TASK MUST EXIST. `stdio_client` and `ClientSession` are anyio
        structured-concurrency context managers: entering them inside a request
        handler binds their task group to THAT request's task, and the moment it
        finishes the group unwinds and closes the streams. The next call from a
        different request task then dies with `anyio.ClosedResourceError` — the
        subprocess is still running, the pipe is not.

        (Found on 2026-09-02 by driving the running service: session started,
        RSS climbed to 30 MB, and every subsequent tool call failed. Unit tests
        did not catch it because each test drives one session from one task.)

        So the context is entered here, in a task that lives as long as the
        session, and held open on `self._stop`. Sending on the memory stream
        from other tasks is fine; the stream being torn down was the problem.
        """
        params = StdioServerParameters(command=OBSCURA_BIN, args=_obscura_args(), env=_child_env())
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=STARTUP_TIMEOUT_SECONDS)
                    self._session = session
                    self._pid = _find_child_pid()
                    ready.set()
                    await self._stop.wait()
        except Exception as exc:  # surfaced to start() via `failure`
            failure.append(exc)
            ready.set()
        finally:
            self._session = None

    async def start(self) -> None:
        """Spawn the subprocess and complete the MCP handshake."""
        ready: asyncio.Event = asyncio.Event()
        failure: list = []
        self._runner = asyncio.create_task(self._run(ready, failure))

        try:
            await asyncio.wait_for(ready.wait(), timeout=STARTUP_TIMEOUT_SECONDS)
        except TimeoutError:
            await self.close(reason="the browser did not start in time")
            raise

        if failure:
            await self.close(reason="the browser failed to start")
            raise failure[0]

        log.info("browser: started obscura session=%s user=%s pid=%s", self.session_id, self.user_id, self._pid)

    async def list_tools(self) -> list[Any]:
        session = self._require_session()
        result = await asyncio.wait_for(session.list_tools(), timeout=CALL_TIMEOUT_SECONDS)
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Run one tool call.

        Serialised per session: one subprocess, one stdio pipe, so concurrent
        calls on the same session would interleave frames.
        """
        session = self._require_session()
        async with self._lock:
            self.last_used_at = time.monotonic()
            self._in_use = True
            try:
                result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=CALL_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                # Never a silent hang (CLAUDE.md #8): reap and say so.
                await self.close(reason=f"the browser took longer than {CALL_TIMEOUT_SECONDS:.0f}s and was stopped")
                raise SessionGone(
                    f"The page did not finish loading within {CALL_TIMEOUT_SECONDS:.0f} seconds, "
                    "so the browser was stopped."
                ) from exc
            except (anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
                # The subprocess DIED — the pipe is gone while `_run` is still
                # parked on `self._stop.wait()`, so `_session` is still set and
                # `_require_session` waves the call through. Every subsequent
                # call then fails here.
                #
                # This is the third death mode, and until 2026-09-04 it was the
                # only unhandled one. It escaped to the gateway's generic
                # handler, and `ClosedResourceError` STRINGIFIES TO THE EMPTY
                # STRING — so the model was told "The browser could not complete
                # browser_navigate: " nine times running and then answered from
                # invention. An error the model cannot read is not a terminal
                # state (CLAUDE.md #8).
                #
                # Closing here also releases the session-cap slot now, instead of
                # at the 10-minute idle sweep. In the incident the dead session
                # held a slot for 11 minutes.
                await self.close(reason="the browser stopped unexpectedly")
                raise SessionGone(
                    "The browser stopped unexpectedly — this usually means the page closed the "
                    "connection or crashed the browser. Nothing was captured. Try again to start "
                    "a fresh browser session."
                ) from exc
            finally:
                self._in_use = False
            self.last_used_at = time.monotonic()
            self._sample_rss()
            return result

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise SessionGone(self._closed_reason or "The browser session is no longer running.")
        return self._session

    def _sample_rss(self) -> None:
        """Record peak RSS. Best-effort — this is telemetry, not control flow."""
        if self._pid is None:
            return
        try:
            import psutil

            rss_mb = psutil.Process(self._pid).memory_info().rss / (1024 * 1024)
            self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
        except Exception:
            pass

    def over_budget(self) -> str | None:
        """Return a reason if this session should be reaped, else None."""
        now = time.monotonic()
        if now - self.created_at > SESSION_TTL_SECONDS:
            return f"the browser session reached its {SESSION_TTL_SECONDS / 60:.0f} minute limit"
        if now - self.last_used_at > SESSION_IDLE_SECONDS:
            return "the browser session was idle and closed"
        self._sample_rss()
        if self.peak_rss_mb > MAX_RSS_MB:
            # This is the #783 backstop. If it ever fires in production, the
            # OTel metric is the evidence to take upstream.
            return f"the browser used more memory than allowed ({self.peak_rss_mb:.0f} MB) and was stopped"
        return None

    async def close(self, reason: str = "the browser session was closed") -> None:
        self._closed_reason = reason
        self._session = None
        self._stop.set()
        runner, self._runner = self._runner, None
        if runner is not None:
            try:
                await asyncio.wait_for(runner, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                runner.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await runner
            except Exception:
                pass
        log.info(
            "browser: closed session=%s user=%s peak_rss_mb=%.0f reason=%s",
            self.session_id,
            self.user_id,
            self.peak_rss_mb,
            reason,
        )


def _child_env() -> dict[str, str]:
    """Environment for the subprocess.

    Passed explicitly rather than inherited. The child runs untrusted
    JavaScript; it has no business seeing the service's own configuration, and
    OBSCURA_ALLOW_PRIVATE_NETWORK in particular must never be inheritable —
    Obscura reads it from the environment, so an env var set anywhere in this
    container would silently disable the SSRF guard in every child.
    """
    keep = {"PATH", "HOME", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    # Belt and braces: explicitly off, so an inherited value cannot win.
    env["OBSCURA_ALLOW_PRIVATE_NETWORK"] = "0"
    return env


def _find_child_pid() -> int | None:
    """Best-effort pid of the obscura child, for RSS sampling."""
    try:
        import psutil

        me = psutil.Process()
        for child in me.children(recursive=False):
            with contextlib.suppress(Exception):
                if "obscura" in child.name() or "obscura" in " ".join(child.cmdline()[:1]):
                    return child.pid
    except Exception:
        pass
    return None
