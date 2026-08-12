"""API tests for the MCP proxy endpoint (M2B-BACKEND, MCP-APP-INTEGRATIONS).

The proxy lives at ``POST /mcp/{server_id}``. It's the surface the
FRONTEND uses to issue arbitrary JSON-RPC against a registered MCP server (the
agent's own MCP calls go through the existing ``McpToolset`` path).

Security boundary tested here:
  * Firebase auth required (401 otherwise).
  * Per-skill allowlist: caller must have access to ≥1 SkillConfig where
    ``tool_configs.mcp.servers`` contains ``server_id`` (403 otherwise).
  * Caller's Authorization header must NOT leak to the upstream MCP server —
    the server has its own ``headers`` config; the user's Firebase JWT stays
    at the proxy boundary.
  * Unknown ``server_id`` → 404.
  * Upstream errors mapped: 5xx → 502, timeout → 504, 4xx → forwarded as-is.

Tests use a TestClient with mocked Firestore lookups + ``httpx.AsyncClient``
patched so no network is touched. Pattern mirrors ``test_sessions_route``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.access_context import AccessContext
from db.models import SkillConfig
from db.models.access import AccessControl
from protocols.mcp_proxy import router

# ---------------------------------------------------------------------------
# Test app + auth fixtures
# ---------------------------------------------------------------------------


def _make_client(uid: str = "viewer", tags: frozenset[str] = frozenset()) -> TestClient:
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain, group_tags=tags)

    test_app = FastAPI()
    test_app.include_router(router)

    @test_app.middleware("http")
    async def _inject_access(request, call_next):
        request.state.access = ctx
        return await call_next(request)

    test_app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(test_app)


def _make_no_auth_client() -> TestClient:
    """Client without auth dependency override — used to confirm 401s."""
    test_app = FastAPI()
    test_app.include_router(router)
    return TestClient(test_app)


def _make_skill(
    skill_id: str = "skill-with-server",
    owner_uid: str = "viewer",
    server_ids: list[str] | None = None,
    ac: AccessControl | None = None,
) -> SkillConfig:
    """Build a SkillConfig whose tool_configs.mcp.servers contains server_ids."""
    tool_configs: dict = {"mcp": {"servers": list(server_ids or [])}} if server_ids else {}
    return SkillConfig(
        skillId=skill_id,
        name="test-skill",
        description="a test skill",
        ownerId=owner_uid,
        ownerEmail=f"{owner_uid}@example.com",
        accessControl=ac or AccessControl(type="public"),
        skillMetadata={"tools": ["mcp"], "toolConfigs": tool_configs},
    )


_HAPPY_SERVER = {
    "name": "Test Map Server",
    "transport": "http",
    "url": "https://upstream.example.com/mcp",
    "headers": {"x-server-secret": "abc123"},
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestForwardsJsonRpc:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_forwards_json_rpc_when_authorized(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Happy path: authenticated caller with allowlisted skill → 200 + body."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        # Mock the upstream response.
        upstream_resp = MagicMock(spec=httpx.Response)
        upstream_resp.status_code = 200
        upstream_resp.content = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'
        upstream_resp.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        # Body forwarded verbatim.
        assert resp.json() == {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

        # Confirm the upstream request used the registered URL.
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["url"] == "https://upstream.example.com/mcp"

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_forwards_get_for_sse_channel(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """The MCP TS SDK's StreamableHTTP transport opens a GET alongside the
        POST channel for server-to-client SSE notifications. The proxy MUST
        forward GET too — not just POST — or the SDK aborts the connection
        even when JSON-RPC requests succeed (regression for the in-chat
        ERR_ABORTED 404 we hit during the local smoke test)."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        upstream_resp = MagicMock(spec=httpx.Response)
        upstream_resp.status_code = 200
        upstream_resp.content = b""
        upstream_resp.headers = {"content-type": "text/event-stream"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        resp = client.get("/mcp/map-server")

        assert resp.status_code == 200, resp.text
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == "https://upstream.example.com/mcp"


# ---------------------------------------------------------------------------
# Auth + access-control boundary tests
# ---------------------------------------------------------------------------


class TestAuthAndAccess:
    def test_proxy_rejects_401_when_unauthenticated(self):
        """Without a Firebase JWT the route must reject before lookup."""
        client = _make_no_auth_client()
        resp = client.post("/mcp/map-server", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp.status_code in (401, 403, 422)

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    def test_proxy_rejects_403_when_no_skill_has_server_id(self, mock_get_doc, mock_skill_module):
        """User has skills but none allowlist this server — security boundary."""
        mock_get_doc.return_value = _HAPPY_SERVER
        # User can see one skill, but its mcp.servers list does NOT include map-server.
        mock_skill_module.list_skills.return_value = [
            _make_skill(skill_id="other-skill", server_ids=["different-server"]),
        ]

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 403, resp.text

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    def test_proxy_rejects_403_when_user_has_no_skills(self, mock_get_doc, mock_skill_module):
        """User has zero accessible skills → no allowlist hit → 403."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = []

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 403

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    def test_proxy_skips_skills_user_cannot_access(self, mock_get_doc, mock_skill_module):
        """list_skills may return private skills the caller can't see —
        the allowlist must filter them out before checking servers."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [
            _make_skill(
                skill_id="private-skill",
                owner_uid="alice",
                ac=AccessControl(type="private"),
                server_ids=["map-server"],  # would allowlist if visible
            ),
        ]

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        # Caller cannot access the alice-owned private skill, so allowlist denies.
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Server lookup failures
# ---------------------------------------------------------------------------


class TestServerLookup:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    def test_proxy_returns_404_for_unknown_server_id(self, mock_get_doc, mock_skill_module):
        mock_get_doc.return_value = None
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["unknown"])]

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/unknown",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Upstream error mapping
# ---------------------------------------------------------------------------


class TestUpstreamErrors:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_surfaces_502_on_downstream_error(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Upstream 5xx → proxy returns 502 (bad gateway)."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        upstream = MagicMock(spec=httpx.Response)
        upstream.status_code = 503
        upstream.content = b"upstream is down"
        upstream.headers = {"content-type": "text/plain"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 502, resp.text

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_surfaces_504_on_downstream_timeout(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Upstream timeout → 504 (gateway timeout)."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 504

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_forwards_4xx_as_is(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Upstream 4xx is the server's error — forward the status code through."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        upstream = MagicMock(spec=httpx.Response)
        upstream.status_code = 400
        upstream.content = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"Invalid Request"}}'
        upstream.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "bogus"},
        )
        # 4xx forwarded verbatim — the JSON-RPC error body explains the failure.
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Defence-in-depth: caller's Firebase JWT must not leak upstream
# ---------------------------------------------------------------------------


class TestAuthHeaderDoesNotLeak:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_does_not_forward_caller_authorization_to_upstream(
        self, mock_client_cls, mock_get_doc, mock_skill_module
    ):
        """The caller's Firebase Authorization header MUST NOT reach the
        upstream MCP server. The server has its own ``headers`` config in
        Firestore (e.g. an x-server-secret) — the user's JWT stays at the
        boundary, mirroring the sessions_route precedent.
        """
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        upstream = MagicMock(spec=httpx.Response)
        upstream.status_code = 200
        upstream.content = b"{}"
        upstream.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        # Send a Firebase-shaped Authorization header on the inbound request —
        # it must not appear on the outbound request.
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer fake-firebase-jwt"},
        )
        assert resp.status_code == 200

        outbound_headers = mock_client.request.call_args.kwargs.get("headers") or {}
        # Case-insensitive check — httpx normalises but we treat the dict generically.
        lowered = {k.lower(): v for k, v in outbound_headers.items()}
        assert "authorization" not in lowered, f"Caller's Firebase JWT leaked to upstream: {outbound_headers!r}"
        # The server's own configured headers MUST appear.
        assert lowered.get("x-server-secret") == "abc123"


# ---------------------------------------------------------------------------
# SSE / streamable-HTTP responses pass through
# ---------------------------------------------------------------------------


class TestPassthroughContentTypes:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_proxy_passes_through_event_stream_content_type(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """StreamableHTTP MCP transport may respond with text/event-stream;
        the proxy must not coerce it back to JSON."""
        mock_get_doc.return_value = _HAPPY_SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(server_ids=["map-server"])]

        upstream = MagicMock(spec=httpx.Response)
        upstream.status_code = 200
        upstream.content = b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        upstream.headers = {"content-type": "text/event-stream"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client("viewer")
        resp = client.post(
            "/mcp/map-server",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


# Suppress noisy pytest warning about httpx mock handling
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
