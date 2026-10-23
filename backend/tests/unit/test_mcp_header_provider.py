"""Per-call auth + identity headers for IAM-gated MCP servers.

The behaviour under test is subtle and load-bearing, so these assert the two
things that actually matter operationally:

* the calling user's identity reaches the server (per-user sessions need it), and
* the token is STABLE across calls, because ADK keys its MCP session pool on an
  MD5 of the merged headers. An unstable token silently collapses pooling to one
  session per tool call — the kind of regression that shows up as a mysterious
  slowdown months later, not as a failure.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tools.mcp import id_token_cache
from tools.mcp.id_token_cache import IdTokenUnavailable, get_id_token
from tools.mcp.registry import _build_header_provider


class _Session:
    def __init__(self, sid: str) -> None:
        self.id = sid


class _Ctx:
    """Stand-in for ADK's ReadonlyContext — only the fields we read."""

    def __init__(self, user_id: str, session_id: str = "sess-1") -> None:
        self.user_id = user_id
        self.session = _Session(session_id)


@pytest.fixture(autouse=True)
def _clear():
    id_token_cache.clear_cache()
    yield
    id_token_cache.clear_cache()


class TestTokenCache:
    def test_token_is_reused_within_its_lifetime(self):
        """The correctness requirement: a stable token means a stable
        session-pool key."""
        calls = []

        def minter(aud):
            calls.append(aud)
            return f"tok-{len(calls)}"

        t = 1000.0
        first = get_id_token("https://svc", minter=minter, now=lambda: t)
        for _ in range(20):
            again = get_id_token("https://svc", minter=minter, now=lambda: t)
            assert again == first
        assert len(calls) == 1, "token was re-minted; session pooling would collapse"

    def test_token_refreshes_after_expiry(self):
        calls = []

        def minter(aud):
            calls.append(aud)
            return f"tok-{len(calls)}"

        now = {"t": 1000.0}
        a = get_id_token("https://svc", minter=minter, now=lambda: now["t"])
        now["t"] += 4000.0  # past lifetime minus margin
        b = get_id_token("https://svc", minter=minter, now=lambda: now["t"])
        assert a != b
        assert len(calls) == 2

    def test_different_audiences_do_not_share_a_token(self):
        def minter(aud):
            return f"tok-for-{aud}"

        a = get_id_token("https://a", minter=minter)
        b = get_id_token("https://b", minter=minter)
        assert a != b

    def test_mint_failure_is_loud(self):
        """A silent empty Authorization header would surface as an opaque 403
        from Cloud Run's edge — much worse to debug than an explicit error."""

        def minter(aud):
            raise RuntimeError("no credentials")

        with pytest.raises(IdTokenUnavailable):
            get_id_token("https://svc", minter=minter)

    def test_empty_token_is_rejected(self):
        with pytest.raises(IdTokenUnavailable):
            get_id_token("https://svc", minter=lambda aud: "")


class TestHeaderProvider:
    def test_absent_unless_auth_mode_requested(self):
        assert _build_header_provider("plain", {"url": "https://x"}) is None
        assert _build_header_provider("plain", {"url": "https://x", "auth": "none"}) is None

    def test_built_when_auth_mode_requested(self):
        provider = _build_header_provider("browser", {"url": "https://x", "auth": "google_id_token"})
        assert provider is not None

    def test_carries_user_identity(self):
        provider = _build_header_provider("browser", {"url": "https://svc/mcp", "auth": "google_id_token"})
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        headers = provider(_Ctx("alice@example.com", "sess-42"))
        assert headers["X-Aitana-User"] == "alice@example.com"
        assert headers["X-Aitana-Session"] == "sess-42"
        assert headers["Authorization"] == "Bearer tok"

    def test_audience_strips_the_mcp_path(self):
        """Cloud Run validates the token against the SERVICE url, not the path."""
        captured = []
        provider = _build_header_provider("browser", {"url": "https://svc.run.app/mcp", "auth": "google_id_token"})
        id_token_cache.clear_cache()

        def minter(aud):
            captured.append(aud)
            return "tok"

        original = id_token_cache._mint
        id_token_cache._mint = minter
        try:
            provider(_Ctx("a@b.com"))
        finally:
            id_token_cache._mint = original

        assert captured == ["https://svc.run.app"]

    def test_two_users_produce_different_header_hashes(self):
        """This is what gives per-user MCP session isolation for free: ADK keys
        its session pool on an MD5 of the merged headers."""
        provider = _build_header_provider("browser", {"url": "https://svc", "auth": "google_id_token"})
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        def digest(user):
            headers = provider(_Ctx(user, "same-session"))
            return hashlib.md5(json.dumps(headers, sort_keys=True).encode()).hexdigest()

        assert digest("alice@example.com") != digest("bob@example.com")

    def test_same_user_produces_a_stable_header_hash(self):
        """The other half: one user must map to the tenant pooled session, not N."""
        provider = _build_header_provider("browser", {"url": "https://svc", "auth": "google_id_token"})
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        def digest():
            headers = provider(_Ctx("alice@example.com", "sess-1"))
            return hashlib.md5(json.dumps(headers, sort_keys=True).encode()).hexdigest()

        first = digest()
        assert all(digest() == first for _ in range(10))

    def test_missing_identity_does_not_crash_the_call(self):
        """A context without user_id still gets an authenticated header; the
        browser service refuses it explicitly, which is a better error than a
        crash inside the tool call."""
        provider = _build_header_provider("browser", {"url": "https://svc", "auth": "google_id_token"})
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        class _Bare:
            pass

        headers = provider(_Bare())
        assert headers["Authorization"] == "Bearer tok"
        assert "X-Aitana-User" not in headers


class TestAdkSeamStillPresent:
    """`header_provider` is the ADK seam this design leans on hardest.

    If a `google-adk` bump removes or renames it, the browser silently loses
    both authentication and per-user identity. Fail here, at the seam, rather
    than in production.
    """

    def test_mcptoolset_accepts_header_provider(self):
        import inspect

        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

        assert "header_provider" in inspect.signature(McpToolset.__init__).parameters

    def test_mcptool_invokes_header_provider_per_call(self):
        import inspect

        # `MCPTool` is a deprecated alias as of ADK 1.31.1; `McpTool` is the
        # live class. Assert against the live one — testing the alias would
        # keep passing after the alias is dropped.
        from google.adk.tools.mcp_tool.mcp_tool import McpTool

        init_source = inspect.getsource(McpTool.__init__)
        assert "header_provider" in init_source
        source = inspect.getsource(McpTool._run_async_impl)
        assert "_header_provider" in source, "McpTool no longer calls header_provider per invocation"

    def test_readonly_context_still_exposes_user_id(self):
        from google.adk.agents.readonly_context import ReadonlyContext

        assert isinstance(getattr(ReadonlyContext, "user_id", None), property)

    def test_session_pooling_still_keys_on_headers(self):
        """If ADK stops hashing headers for the pool key, per-user isolation
        stops being free and this design needs revisiting."""
        import inspect

        from google.adk.tools.mcp_tool.mcp_session_manager import MCPSessionManager

        source = inspect.getsource(MCPSessionManager)
        assert "merged_headers" in source


class TestIdentityIsTheEmailNotTheUid:
    """Regression: the gate is keyed on email; ADK's user_id is the Firebase UID.

    First deployed attempt failed exactly here. `_build_header_provider` read
    `ReadonlyContext.user_id` — the Firebase UID (adk/agui.py:123) — and handed
    it to an allowlist keyed on email and domain. No '@', no match, denial;
    the denial was then swallowed by a broad `except` and the call went out with
    no `X-Aitana-User` at all, so the browser service answered "could not tell
    which user this request is for". Two bugs stacked into one confusing message.
    """

    def test_email_is_used_in_preference_to_the_uid(self):
        provider = _build_header_provider(
            "browser", {"url": "https://svc", "auth": "google_id_token"}, "alice@example.com", "example.com"
        )
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        headers = provider(_Ctx("firebase-uid-abc123"))
        assert headers["X-Aitana-User"] == "alice@example.com", (
            "the Firebase UID reached the header again — every policy here is keyed on email"
        )

    def test_falls_back_to_context_when_no_email_supplied(self):
        """Older callers that pass no identity still send something rather than
        dropping the header entirely."""
        provider = _build_header_provider("browser", {"url": "https://svc", "auth": "google_id_token"})
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        headers = provider(_Ctx("firebase-uid-abc123"))
        assert headers["X-Aitana-User"] == "firebase-uid-abc123"


class TestDenialPropagates:
    """A gate denial must reach the caller, not become a log line.

    Swallowing it left the request going out WITHOUT identity, which the
    browser service reports as a configuration problem — telling the user the
    wrong thing about their own access. CLAUDE.md #8, violated by the code
    written to enforce it.
    """

    def test_denied_user_raises_rather_than_dropping_the_header(self):
        from tools import browser_allowlist
        from tools.browser_allowlist import NotAllowed

        provider = _build_header_provider(
            "browser",
            {"url": "https://svc", "auth": "google_id_token", "gate": "browser_allowlist"},
            "nobody@evil.com",
            "evil.com",
        )
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        original = browser_allowlist._load
        browser_allowlist._load = lambda now=None: {"domains": ["example.com"]}
        try:
            with pytest.raises(NotAllowed):
                provider(_Ctx("uid"))
        finally:
            browser_allowlist._load = original

    def test_allowed_user_passes_the_gate(self):
        from tools import browser_allowlist

        provider = _build_header_provider(
            "browser",
            {"url": "https://svc", "auth": "google_id_token", "gate": "browser_allowlist"},
            "alice@example.com",
            "example.com",
        )
        id_token_cache._cache["https://svc"] = (float("inf"), "tok")

        original = browser_allowlist._load
        browser_allowlist._load = lambda now=None: {"domains": ["example.com"]}
        try:
            headers = provider(_Ctx("uid"))
            assert headers["X-Aitana-User"] == "alice@example.com"
        finally:
            browser_allowlist._load = original


class TestAgentFactoryPassesIdentity:
    """The wiring, not just the function — a correct provider that is never
    given the email is the bug this whole class exists to prevent."""

    def test_agent_factory_passes_user_email_to_mcp_resolution(self):
        import inspect

        from adk import agent

        source = inspect.getsource(agent.create_agent)
        assert "resolve_mcp_tools(md.tool_configs, user.email" in source


class TestConnectTimeoutSurvivesAColdStart:
    """The MCP SDK defaults `timeout` to 5.0s, which is shorter than a Cloud Run
    cold start — so the FIRST call to any scale-to-zero MCP server fails.

    It fails in the worst way available: ADK logs "Failed to get tools from
    toolset", the toolset yields no tools, and the agent tells the user the
    capability does not exist and answers from memory. A tool that timed out is
    indistinguishable from one that was never configured.

    Observed on test 2026-09-03 with platform-browser at min_instances=0: the
    backend gave up at 5.0s and the browser finished starting 0.4s later. The
    next request took 38 ms.
    """

    def _params(self, config):
        from tools.mcp.registry import _build_toolset

        toolset = _build_toolset("browser", config, None, None)
        assert toolset is not None
        return toolset._connection_params

    def test_streamable_http_gets_more_than_the_sdk_default(self):
        params = self._params({"url": "https://svc/mcp"})
        assert params.timeout > 5.0, (
            "back on the SDK default — the first browse after a scale-to-zero "
            "idle will silently report the tool as unavailable"
        )

    def test_default_tolerates_a_measured_cold_start(self):
        """5.4s was the measured cold start; leave real headroom over it."""
        from tools.mcp.registry import MCP_CONNECT_TIMEOUT_SECONDS

        assert MCP_CONNECT_TIMEOUT_SECONDS >= 15

    def test_sse_transport_gets_it_too(self):
        params = self._params({"url": "https://svc/sse", "transport": "sse"})
        assert params.timeout > 5.0

    def test_a_server_may_opt_into_failing_fast(self):
        params = self._params({"url": "https://svc/mcp", "timeout_seconds": 2})
        assert params.timeout == 2.0

    def test_read_timeout_defaults_to_adk_300(self):
        assert self._params({"url": "https://svc/mcp"}).sse_read_timeout == 300.0

    def test_a_server_may_lower_its_read_timeout(self):
        params = self._params({"url": "https://svc/mcp", "read_timeout_seconds": 90})
        assert params.sse_read_timeout == 90.0


class TestSelfHostedLoopback:
    """A self-hosted service on http://127.0.0.1 has no IAM edge, and a user
    credential can't mint an ID token — so no token is minted, but identity
    still flows."""

    def test_loopback_http_mints_no_token(self):
        def boom(aud):
            raise AssertionError("must not mint for a loopback http server")

        original = id_token_cache._mint
        id_token_cache._mint = boom
        id_token_cache.clear_cache()
        try:
            for url in ("http://127.0.0.1:8949/mcp", "http://localhost:8949/mcp"):
                provider = _build_header_provider("browser", {"url": url, "auth": "google_id_token"})
                headers = provider(_Ctx("alice@example.com", "sess-1"))
                assert "Authorization" not in headers
                assert headers["X-Aitana-User"] == "alice@example.com"
        finally:
            id_token_cache._mint = original

    def test_https_and_non_loopback_http_still_mint(self):
        from tools.mcp.registry import _is_loopback_http

        assert not _is_loopback_http("https://127.0.0.1/mcp")
        assert not _is_loopback_http("http://10.0.0.5:8080/mcp")
        assert not _is_loopback_http("https://svc.run.app/mcp")
        assert _is_loopback_http("http://127.0.0.1:8949/mcp")
