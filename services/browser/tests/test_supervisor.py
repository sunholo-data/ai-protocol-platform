"""Tests for the browser supervisor.

The subprocess tests run against a REAL Obscura binary when one is available
(OBSCURA_BIN, or `obscura` on PATH) and skip otherwise, so the suite stays
useful on a laptop and honest in CI. Everything that can be proven without a
browser is proven without one.
"""

from __future__ import annotations

import os
import shutil
import socket

import pytest

import allowlist
from allowlist import NotAllowed, assert_user_identified
from ssrf import BlockedUrl, assert_fetchable


def _obscura_available() -> bool:
    binary = os.environ.get("OBSCURA_BIN", "obscura")
    return shutil.which(binary) is not None or os.path.exists(binary)


needs_obscura = pytest.mark.skipif(not _obscura_available(), reason="no obscura binary available")


# ---------------------------------------------------------------------------
# Tool allowlist — the credential surface
# ---------------------------------------------------------------------------


class TestToolAllowlist:
    def test_credential_tools_are_never_exposed(self):
        """The prompt-injection defence. If this fails, a visited page can ask
        the model to export session state and post it elsewhere."""
        offered = [
            "browser_navigate",
            "browser_markdown",
            "browser_storage_state",
            "browser_set_storage_state",
            "browser_get_cookies",
            "browser_set_cookie",
            "browser_clear_cookies",
        ]
        result = allowlist.filter_tools(offered)
        assert "browser_storage_state" not in result
        assert "browser_set_storage_state" not in result
        assert "browser_get_cookies" not in result
        assert "browser_set_cookie" not in result
        assert "browser_clear_cookies" not in result
        assert "browser_navigate" in result

    def test_broken_text_serialiser_is_not_exposed(self):
        """browser_snapshot mangles inline whitespace (upstream #785). Only the
        markdown path is correct, so it is the only one offered."""
        result = allowlist.filter_tools(["browser_snapshot", "browser_markdown"])
        assert result == ["browser_markdown"]

    def test_unknown_tools_are_withheld_not_passed_through(self):
        """A version bump that adds a tool must not silently widen the surface."""
        result = allowlist.filter_tools(["browser_navigate", "browser_do_something_new"])
        assert result == ["browser_navigate"]

    def test_credential_and_allowed_sets_are_disjoint(self):
        assert not (allowlist.ALLOWED_TOOLS & allowlist.EXCLUDED_CREDENTIAL_TOOLS)
        assert not (allowlist.ALLOWED_TOOLS & allowlist.EXCLUDED_BROKEN_TOOLS)


# ---------------------------------------------------------------------------
# User allowlist — deny by default
# ---------------------------------------------------------------------------


class TestIdentityOnly:
    """This service checks that a user is NAMED, never whether they are allowed.

    Membership moved to backend/tools/browser_allowlist.py after the first
    deployed revision denied everyone: this service holds zero project roles by
    design, so it cannot read Firestore, and a policy it cannot evaluate could
    only ever fail closed. See the note in allowlist.py.
    """

    def test_named_user_passes(self):
        assert_user_identified("anyone@example.com")

    def test_unnamed_user_refused(self):
        with pytest.raises(NotAllowed):
            assert_user_identified("")

    def test_whitespace_user_refused(self):
        with pytest.raises(NotAllowed):
            assert_user_identified("   ")

    def test_refusal_message_is_fit_to_show(self):
        with pytest.raises(NotAllowed) as exc:
            assert_user_identified("")
        assert "configuration problem" in str(exc.value)

    def test_this_service_owns_no_membership_policy(self):
        """Guard against re-introducing a policy check here. It would need
        Firestore, which this identity deliberately cannot reach."""
        import allowlist as mod

        assert not hasattr(mod, "assert_user_allowed")

        # Look for USE, not the word — the module explains at length why it must
        # not read Firestore, so a naive substring check trips on its own prose.
        source = open(mod.__file__).read()
        assert "from google.cloud import firestore" not in source
        assert "firestore.Client" not in source


# ---------------------------------------------------------------------------
# SSRF pre-check — the reason-giving guard
# ---------------------------------------------------------------------------


def _resolver_returning(*addresses: str):
    def _fake(host, port, *args, **kwargs):
        out = []
        for addr in addresses:
            if ":" in addr:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, port or 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 0)))
        return out

    return _fake


class TestSsrfPrecheck:
    def test_metadata_endpoint_literal_blocked(self):
        with pytest.raises(BlockedUrl):
            assert_fetchable("http://169.254.169.254/computeMetadata/v1/")

    def test_hostname_resolving_to_metadata_blocked_with_a_reason(self):
        """The whole point of this module: Obscura blocks this too, but reports
        a generic network error. Ours has to say why."""
        with pytest.raises(BlockedUrl) as exc:
            assert_fetchable("http://evil.example.com/", resolver=_resolver_returning("169.254.169.254"))
        message = str(exc.value)
        assert "169.254.169.254" in message
        assert "private or internal" in message

    def test_v4_mapped_v6_blocked(self):
        with pytest.raises(BlockedUrl):
            assert_fetchable("http://evil.example.com/", resolver=_resolver_returning("::ffff:169.254.169.254"))

    def test_mixed_answers_refuse_the_whole_host(self):
        with pytest.raises(BlockedUrl):
            assert_fetchable(
                "http://mixed.example.com/",
                resolver=_resolver_returning("93.184.216.34", "10.0.0.1"),
            )

    def test_cgnat_blocked(self):
        with pytest.raises(BlockedUrl):
            assert_fetchable("http://evil.example.com/", resolver=_resolver_returning("100.64.0.1"))

    def test_file_scheme_blocked(self):
        with pytest.raises(BlockedUrl):
            assert_fetchable("file:///etc/passwd")

    def test_unresolvable_fails_closed(self):
        def _boom(*a, **k):
            raise socket.gaierror("nope")

        with pytest.raises(BlockedUrl):
            assert_fetchable("http://nope.example.com/", resolver=_boom)

    def test_public_host_allowed(self):
        assert_fetchable("https://example.com/", resolver=_resolver_returning("93.184.216.34"))


# ---------------------------------------------------------------------------
# Subprocess lifecycle — against a real binary
# ---------------------------------------------------------------------------


@needs_obscura
class TestObscuraSubprocess:
    async def test_session_starts_and_lists_tools(self):
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            tools = await session.list_tools()
            names = {t.name for t in tools}
            assert "browser_navigate" in names
            assert "browser_markdown" in names
            # Obscura offers the credential tools; our filter is what withholds
            # them, so assert they really are on offer or the test proves nothing.
            assert "browser_storage_state" in names
        finally:
            await reg.stop()

    async def test_filter_actually_removes_what_obscura_offers(self):
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            offered = [t.name for t in await session.list_tools()]
            exposed = allowlist.filter_tools(offered)
            assert "browser_storage_state" in offered
            assert "browser_storage_state" not in exposed
            assert "browser_snapshot" in offered
            assert "browser_snapshot" not in exposed
        finally:
            await reg.stop()

    async def test_subprocess_is_reaped_on_close(self):
        from sessions import SessionRegistry

        reg = SessionRegistry()
        session = await reg.get_or_create("u1", "s1")
        assert reg.count == 1
        closed = await reg.close("u1", "s1")
        assert closed is True
        assert reg.count == 0
        await reg.stop()
        assert session._session is None

    async def test_two_users_get_two_subprocesses(self):
        """The isolation claim: two people never share a cookie jar because
        they never share a process."""
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            a = await reg.get_or_create("alice", "s1")
            b = await reg.get_or_create("bob", "s1")
            assert a is not b
            assert reg.count == 2
        finally:
            await reg.stop()

    async def test_same_user_same_session_reuses_one_subprocess(self):
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            a = await reg.get_or_create("alice", "s1")
            b = await reg.get_or_create("alice", "s1")
            assert a is b
            assert reg.count == 1
        finally:
            await reg.stop()

    async def test_global_cap_still_refuses(self):
        """The per-user cap now EVICTS (see TestPerUserCapEvictsRatherThanRefuses),
        but the INSTANCE-wide cap must still refuse: it exists to bound the
        service's own footprint, and evicting another user's session to serve
        this one would break the isolation the whole design rests on."""
        import sessions as sessions_mod
        from sessions import TooManySessions

        reg = sessions_mod.SessionRegistry()
        original = sessions_mod.MAX_CONCURRENT_SESSIONS
        sessions_mod.MAX_CONCURRENT_SESSIONS = 1
        try:
            await reg.get_or_create("alice", "s1")
            with pytest.raises(TooManySessions, match="busy with other work"):
                await reg.get_or_create("bob", "s1")
        finally:
            sessions_mod.MAX_CONCURRENT_SESSIONS = original
            await reg.stop()

    async def test_markdown_extraction_end_to_end(self):
        """A real page through the real binary. Uses example.com because it is
        stable; the SPA corpus lives in M7."""
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            await session.call_tool("browser_navigate", {"url": "https://example.com/"})
            result = await session.call_tool("browser_markdown", {})
            text = "".join(getattr(c, "text", "") for c in result.content)
            assert "Example Domain" in text
        finally:
            await reg.stop()

    async def test_child_env_cannot_enable_private_network(self):
        """OBSCURA_ALLOW_PRIVATE_NETWORK is read from the environment by
        Obscura, so an inherited value would silently disable the SSRF guard in
        every child. It must be pinned off regardless of our own environment."""
        from obscura import _child_env

        os.environ["OBSCURA_ALLOW_PRIVATE_NETWORK"] = "1"
        try:
            env = _child_env()
            assert env["OBSCURA_ALLOW_PRIVATE_NETWORK"] == "0"
        finally:
            os.environ.pop("OBSCURA_ALLOW_PRIVATE_NETWORK", None)

    async def test_obscura_still_refuses_loopback_through_our_session(self):
        """Defence in depth: even with our pre-check bypassed (we call the tool
        directly with a literal), Obscura must refuse."""
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            result = await session.call_tool("browser_navigate", {"url": "http://127.0.0.1:9/"})
            text = "".join(getattr(c, "text", "") for c in result.content).lower()
            assert "example" not in text
            assert any(w in text for w in ("not allowed", "error", "failed"))
        finally:
            await reg.stop()


@needs_obscura
class TestCrossTaskRegression:
    """Regression: the stdio pipe must survive the task that opened it.

    `stdio_client` and `ClientSession` are anyio structured-concurrency context
    managers. Entering them inside a request handler binds their task group to
    THAT request's task; when it finishes, the group unwinds and the pipe closes
    while the subprocess keeps running. Every later call then dies with
    `anyio.ClosedResourceError`.

    This shipped past the original unit tests because each of them drives one
    session from one task, start to finish — exactly the shape that hides it.
    It was caught by driving the running HTTP service, where each call really is
    a separate task. So the test drives calls from DISTINCT tasks on purpose.
    """

    async def test_calls_survive_across_separate_tasks(self):
        import asyncio

        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            # Each call in its own task, as separate HTTP requests would be.
            async def start():
                return await reg.get_or_create("u1", "s1")

            session = await asyncio.create_task(start())

            async def navigate():
                return await session.call_tool("browser_navigate", {"url": "https://example.com/"})

            async def extract():
                return await session.call_tool("browser_markdown", {})

            await asyncio.create_task(navigate())
            result = await asyncio.create_task(extract())

            text = "".join(getattr(c, "text", "") for c in result.content)
            assert "Example Domain" in text, (
                "the stdio pipe did not survive the task that opened it — "
                "the session-owning runner task in obscura.ObscuraSession._run has regressed"
            )
        finally:
            await reg.stop()

    async def test_many_sequential_calls_from_distinct_tasks(self):
        """Not just the second call — the pipe must stay open indefinitely."""
        import asyncio

        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await asyncio.create_task(reg.get_or_create("u1", "s1"))
            await asyncio.create_task(session.call_tool("browser_navigate", {"url": "https://example.com/"}))
            for i in range(5):

                async def one():
                    return await session.call_tool("browser_markdown", {})

                result = await asyncio.create_task(one())
                text = "".join(getattr(c, "text", "") for c in result.content)
                assert "Example Domain" in text, f"call {i + 2} failed"
        finally:
            await reg.stop()


@needs_obscura
class TestMarkdownTruncation:
    """Obscura's browser_markdown defaults to max_chars=4000 and truncates.

    Silently reading a quarter of a page is worse than the JS-blind fetch this
    service replaces, because truncated content LOOKS complete. Caught on
    2026-09-02 when nine different sites all returned ~4032 characters — the
    shape of a limit, not of content.
    """

    async def test_obscura_default_really_does_truncate(self):
        """Guards the premise. If upstream raises its default, this test starts
        failing and the gateway's override can be reconsidered."""
        from sessions import SessionRegistry

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            await session.call_tool("browser_navigate", {"url": "https://react.dev/"})
            result = await session.call_tool("browser_markdown", {})
            text = "".join(getattr(c, "text", "") for c in result.content)
            assert "(truncated," in text, "upstream default is no longer 4000 — revisit the gateway override"
            assert len(text) < 5000
        finally:
            await reg.stop()

    async def test_gateway_default_reads_the_whole_page(self):
        """The fix: the supervisor supplies max_chars when the caller does not."""
        from gateway import MARKDOWN_DEFAULT_MAX_CHARS
        from sessions import SessionRegistry

        assert MARKDOWN_DEFAULT_MAX_CHARS >= 40000

        reg = SessionRegistry()
        try:
            session = await reg.get_or_create("u1", "s1")
            await session.call_tool("browser_navigate", {"url": "https://react.dev/"})
            result = await session.call_tool("browser_markdown", {"max_chars": MARKDOWN_DEFAULT_MAX_CHARS})
            text = "".join(getattr(c, "text", "") for c in result.content)
            assert "(truncated," not in text
            assert len(text) > 10000
        finally:
            await reg.stop()

    async def test_ceiling_stays_clear_of_the_offload_threshold(self):
        """The backend offloads any tool result over 50K to an artifact, which
        strands the A2UI render path. The ceiling must stay below that."""
        from gateway import MARKDOWN_DEFAULT_MAX_CHARS

        assert MARKDOWN_DEFAULT_MAX_CHARS < 50000


class TestAdvertisedDefaultMatchesOurs:
    """The half of the truncation fix that was missing until 2026-09-03.

    `setdefault` in the call path only helps when the caller OMITS max_chars.
    The model does not omit it — it reads Obscura's schema, sees `default: 4000`
    and sends 4000, so the supervisor's default never fires and the page is
    truncated exactly as before.

    Observed live on dev: browser_markdown{"max_chars":4000} returned a quarter
    of solidjs.com and the model spent six further browser_search calls hunting
    for a tagline that was in the part it never got. Test, whose model happened
    to omit the argument, answered in two calls. The mitigation existed and did
    nothing, because it addressed the rarer half of the problem.

    These are pure schema tests — no subprocess — so they run in CI, unlike the
    obscura-gated ones above.
    """

    def _tool(self, default=4000):
        import mcp.types as types

        return types.Tool(
            name="browser_markdown",
            description="Get the page as markdown.",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_chars": {
                        "type": "integer",
                        "default": default,
                        "description": "Maximum characters to return.",
                    }
                },
            },
        )

    def test_advertised_default_is_raised_to_ours(self):
        from gateway import MARKDOWN_DEFAULT_MAX_CHARS, _rewrite_advertised_defaults

        rewritten = _rewrite_advertised_defaults(self._tool())
        assert rewritten.inputSchema["properties"]["max_chars"]["default"] == MARKDOWN_DEFAULT_MAX_CHARS, (
            "the model is still being told 4000, and will still send 4000"
        )

    def test_description_warns_against_lowering_it(self):
        """The number alone is not enough — a model trimming for context will
        lower it unless told what that costs."""
        from gateway import _rewrite_advertised_defaults

        desc = _rewrite_advertised_defaults(self._tool()).inputSchema["properties"]["max_chars"]["description"]
        assert "truncated" in desc.lower()

    def test_the_probed_schema_is_not_mutated_in_place(self):
        """The catalogue is cached and shared across every user; mutating the
        probe result would make the rewrite order-dependent."""
        from gateway import _rewrite_advertised_defaults

        original = self._tool()
        _rewrite_advertised_defaults(original)
        assert original.inputSchema["properties"]["max_chars"]["default"] == 4000

    def test_other_tools_are_untouched(self):
        import mcp.types as types

        from gateway import _rewrite_advertised_defaults

        tool = types.Tool(
            name="browser_navigate",
            description="Go to a url.",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
        )
        assert _rewrite_advertised_defaults(tool) is tool

    def test_survives_a_schema_without_the_argument(self):
        """Upstream may rename or drop max_chars. That should degrade to "no
        rewrite", never to a KeyError that takes out tools/list entirely."""
        import mcp.types as types

        from gateway import _rewrite_advertised_defaults

        tool = types.Tool(
            name="browser_markdown",
            description="Get the page as markdown.",
            inputSchema={"type": "object", "properties": {}},
        )
        assert _rewrite_advertised_defaults(tool).inputSchema["properties"] == {}


@needs_obscura
class TestPerUserCapEvictsRatherThanRefuses:
    """The per-user cap must recycle, not lock the user out.

    Every chat turn brings a new session id, so a conversation opens a new
    browser session per turn. With a hard cap the 4th turn hit "you already have
    3 browser sessions open" and the user was blocked for the whole idle window
    — a self-inflicted outage from a limit meant to protect the instance.

    Found 2026-09-03 by driving the skill headlessly against deployed dev: the
    first browse worked, a login flow a few turns later could not open a page.
    """

    async def test_oldest_idle_session_is_evicted_to_make_room(self):
        import sessions as sessions_mod

        reg = sessions_mod.SessionRegistry()
        original = sessions_mod.MAX_SESSIONS_PER_USER
        sessions_mod.MAX_SESSIONS_PER_USER = 2
        try:
            first = await reg.get_or_create("alice", "turn-1")
            await reg.get_or_create("alice", "turn-2")
            assert reg.count == 2

            # The third must succeed, not raise.
            third = await reg.get_or_create("alice", "turn-3")
            assert third is not None
            assert reg.count == 2, "eviction should keep the user at the cap, not exceed it"

            live = {s["session_id"] for s in reg.describe()}
            assert "turn-3" in live
            assert "turn-1" not in live, "the least-recently-used session should have gone"
            assert first._session is None, "the evicted session's subprocess must be reaped"
        finally:
            sessions_mod.MAX_SESSIONS_PER_USER = original
            await reg.stop()

    async def test_other_users_are_untouched_by_eviction(self):
        """Eviction is per-user. Alice hitting her cap must not disturb Bob —
        they are separate subprocesses precisely so they cannot affect each
        other."""
        import sessions as sessions_mod

        reg = sessions_mod.SessionRegistry()
        original = sessions_mod.MAX_SESSIONS_PER_USER
        sessions_mod.MAX_SESSIONS_PER_USER = 1
        try:
            bob = await reg.get_or_create("bob", "b1")
            await reg.get_or_create("alice", "a1")
            await reg.get_or_create("alice", "a2")  # evicts a1, not b1

            live = {(s["user_id"], s["session_id"]) for s in reg.describe()}
            assert ("bob", "b1") in live
            assert ("alice", "a2") in live
            assert ("alice", "a1") not in live
            assert bob._session is not None
        finally:
            sessions_mod.MAX_SESSIONS_PER_USER = original
            await reg.stop()

    async def test_a_session_mid_call_is_not_evicted(self):
        """Evicting under an in-flight tool call would surface as a mysterious
        mid-task failure, which is worse than the refusal it replaces."""
        import sessions as sessions_mod
        from sessions import TooManySessions

        reg = sessions_mod.SessionRegistry()
        original = sessions_mod.MAX_SESSIONS_PER_USER
        sessions_mod.MAX_SESSIONS_PER_USER = 1
        try:
            busy = await reg.get_or_create("alice", "a1")
            busy._in_use = True
            with pytest.raises(TooManySessions, match="busy"):
                await reg.get_or_create("alice", "a2")
        finally:
            sessions_mod.MAX_SESSIONS_PER_USER = original
            await reg.stop()


# ---------------------------------------------------------------------------
# A dead subprocess must be reported as gone, not as nothing
# ---------------------------------------------------------------------------


class TestDeadSubprocessIsReportedNotSilent:
    """The 2026-09-04 incident, pinned.

    A user's session made ~16 good calls, then the Obscura subprocess died —
    the remote server closed a connection abruptly mid-navigation. Every call
    after that failed with `anyio.ClosedResourceError`, which is neither
    `TimeoutError` nor `SessionGone`, so it escaped both handled paths and hit
    the gateway's generic `except Exception`.

    That mattered because **`ClosedResourceError` stringifies to an empty
    string**. The model received "The browser could not complete
    browser_navigate: " — a sentence ending in a colon — nine times in a row,
    and then guessed at an answer. A terminal state the user cannot read is the
    failure CLAUDE.md #8 is about.

    The session also stayed in the registry for 11 minutes afterwards, holding
    a slot against the session cap, because reaping is time-based and nothing
    marked it dead.
    """

    @pytest.mark.asyncio
    async def test_a_closed_stream_becomes_SessionGone_with_a_readable_reason(self):
        import anyio

        from obscura import ObscuraSession, SessionGone

        session = ObscuraSession(user_id="u", session_id="s")

        class _DeadPipe:
            async def call_tool(self, name, arguments):
                raise anyio.ClosedResourceError

        session._session = _DeadPipe()

        with pytest.raises(SessionGone) as exc:
            await session.call_tool("browser_navigate", {"url": "https://example.com"})

        message = str(exc.value)
        assert message.strip(), "SessionGone must carry a reason — an empty error is what the model saw"
        assert "browser" in message.lower()

    @pytest.mark.asyncio
    async def test_the_session_marks_itself_closed_so_it_is_reaped_promptly(self):
        """Not left holding a cap slot until the 10-minute idle sweep."""
        import anyio

        from obscura import ObscuraSession, SessionGone

        session = ObscuraSession(user_id="u", session_id="s")

        class _DeadPipe:
            async def call_tool(self, name, arguments):
                raise anyio.ClosedResourceError

        session._session = _DeadPipe()

        with pytest.raises(SessionGone):
            await session.call_tool("browser_navigate", {})

        assert session._session is None, "a dead session must not look alive to the next call"

        # And the next call fails the SAME way rather than differently.
        with pytest.raises(SessionGone):
            await session.call_tool("browser_screenshot", {})
