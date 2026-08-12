"""Sprint 2.13 M3 — mcp_proxy server-side ArtefactReviewer interception.

The proxy is a dumb byte-forwarder by default. With a registered
``ArtefactReviewer``, ``resources/read`` responses carrying
``text/html`` artefacts get consulted before being forwarded; a block
returns a structured 403 the frontend's ArtefactRefused mount handler
understands.

Tests verify:
  * Back-compat: with NO reviewer registered, _forward is
    byte-identical to current behaviour (regression).
  * Approve path: registered reviewer approves → upstream bytes
    forwarded unchanged.
  * Block path: registered reviewer blocks → 403 with structured body
    {type:'artefact_blocked', message, reason_code, appeal_url?}.
  * Scope guard: only requests with method='resources/read' AND
    text/html responses trigger the reviewer; tool calls / prompts /
    etc. pass through untouched.
  * Reviewer crash fails open (forwards original response).
  * Slow-reviewer warn log (no test for the time threshold itself —
    that's a behavioural detail not a contract).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.access_context import AccessContext
from db.models import SkillConfig
from db.models.access import AccessControl
from protocols.artefact_review import (
    ArtefactDecision,
    clear_registered_artefact_reviewer,
    register_artefact_reviewer,
)
from protocols.mcp_proxy import router


def _make_client(uid: str = "viewer") -> TestClient:
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain, group_tags=frozenset())

    test_app = FastAPI()
    test_app.include_router(router)

    @test_app.middleware("http")
    async def _inject_access(request, call_next):
        request.state.access = ctx
        return await call_next(request)

    test_app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(test_app)


def _make_skill(server_ids: list[str]) -> SkillConfig:
    return SkillConfig(
        skillId="skill",
        name="test-skill",
        description="d",
        ownerId="viewer",
        ownerEmail="viewer@example.com",
        accessControl=AccessControl(type="public"),
        skillMetadata={"tools": ["mcp"], "toolConfigs": {"mcp": {"servers": server_ids}}},
    )


_SERVER = {
    "name": "test mcp",
    "transport": "http",
    "url": "https://upstream.example.com/mcp",
    "headers": {},
}


def _upstream_resources_read_response(html: str, mime: str = "text/html") -> MagicMock:
    """Build a mocked upstream resources/read JSON-RPC response."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "contents": [
                {
                    "uri": "ui://render/abc",
                    "mimeType": mime,
                    "text": html,
                }
            ]
        },
    }
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.content = json.dumps(payload).encode()
    resp.headers = {"content-type": "application/json"}
    return resp


def _upstream_opaque_response(body: bytes = b'{"jsonrpc":"2.0","id":1,"result":{}}') -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.content = body
    resp.headers = {"content-type": "application/json"}
    return resp


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_registered_artefact_reviewer()
    yield
    clear_registered_artefact_reviewer()


# ─── Back-compat: NO reviewer registered ─────────────────────────────────────


class TestBackCompatNoReviewer:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_no_reviewer_resources_read_passes_through_unchanged(
        self, mock_client_cls, mock_get_doc, mock_skill_module
    ):
        """With NO reviewer registered, resources/read responses are
        byte-identical to upstream — regression for the dumb-forwarder
        contract."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        upstream = _upstream_resources_read_response("<html><body>safe</body></html>")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://render/abc"}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        # Body identical to upstream — no reviewer = no inspection.
        assert resp.content == upstream.content

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_no_reviewer_tools_list_passes_through_unchanged(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Non-resources/read methods also pass through unchanged."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        upstream = _upstream_opaque_response(b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.content == upstream.content


# ─── Approve path: reviewer registered, returns approve ──────────────────────


class TestApprovePath:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_approve_forwards_response_unchanged(self, mock_client_cls, mock_get_doc, mock_skill_module):
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        class ApproveReviewer:
            async def review(self, request):
                return ArtefactDecision(action="approve", message=None, reason_code=None, appeal_url=None)

        register_artefact_reviewer(ApproveReviewer())

        upstream = _upstream_resources_read_response("<html><body>safe</body></html>")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://render/abc"}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.content == upstream.content


# ─── Block path: reviewer registered, returns block ──────────────────────────


class TestBlockPath:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_block_returns_403_with_structured_decision(self, mock_client_cls, mock_get_doc, mock_skill_module):
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        class BlockReviewer:
            async def review(self, request):
                return ArtefactDecision(
                    action="block",
                    message="Contains forbidden <script> tag",
                    reason_code="FORBIDDEN_TAG",
                    appeal_url="https://example.com/appeal",
                )

        register_artefact_reviewer(BlockReviewer())

        upstream = _upstream_resources_read_response("<script>alert(1)</script>")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://render/abc"}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 403, resp.text
        payload = resp.json()
        assert payload["type"] == "artefact_blocked"
        assert payload["message"] == "Contains forbidden <script> tag"
        assert payload["reason_code"] == "FORBIDDEN_TAG"
        assert payload["appeal_url"] == "https://example.com/appeal"


# ─── Scope guard: only resources/read with text/html triggers the reviewer ──


class TestScopeGuard:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_tools_list_response_is_NOT_inspected(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Even with a block-everything reviewer registered, tools/list
        passes through — the reviewer is only consulted on resources/read."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        review_calls = []

        class WatchingBlocker:
            async def review(self, request):
                review_calls.append(request)
                return ArtefactDecision(action="block", message="should not see this", reason_code="X", appeal_url=None)

        register_artefact_reviewer(WatchingBlocker())

        upstream = _upstream_opaque_response(b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        assert review_calls == [], "reviewer must not be consulted for non-resources/read methods"

    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_non_html_mime_is_NOT_inspected(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """A resources/read response carrying application/json (not HTML)
        passes through — the reviewer is for HTML artefacts only."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        review_calls = []

        class WatchingBlocker:
            async def review(self, request):
                review_calls.append(request)
                return ArtefactDecision(action="block", message="x", reason_code="X", appeal_url=None)

        register_artefact_reviewer(WatchingBlocker())

        upstream = _upstream_resources_read_response('{"data": "json"}', mime="application/json")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://render/abc"}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        assert review_calls == [], "reviewer must not be consulted for non-HTML resources"


# ─── Fail-open: reviewer crash forwards the original response ────────────────


class TestFailOpen:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_reviewer_crash_forwards_original_response(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """If reviewer.review() raises, the proxy logs + forwards the
        upstream response — the iframe sandbox is the safety net."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        class CrashingReviewer:
            async def review(self, request):
                raise RuntimeError("boom")

        register_artefact_reviewer(CrashingReviewer())

        upstream = _upstream_resources_read_response("<html/>")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        body = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://render/abc"}}
        resp = client.post("/mcp/map-server", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.content == upstream.content


# ─── Malformed bodies short-circuit to pass-through ──────────────────────────


class TestMalformedBodyHandling:
    @patch("protocols.mcp_proxy.skill_config")
    @patch("protocols.mcp_proxy.get_document")
    @patch("protocols.mcp_proxy.httpx.AsyncClient")
    def test_non_json_request_body_passes_through(self, mock_client_cls, mock_get_doc, mock_skill_module):
        """Non-JSON request body — proxy can't read the method, falls
        back to pass-through (back-compat preserved for non-JSON-RPC)."""
        mock_get_doc.return_value = _SERVER
        mock_skill_module.list_skills.return_value = [_make_skill(["map-server"])]

        review_calls = []

        class WatchingBlocker:
            async def review(self, request):
                review_calls.append(request)
                return ArtefactDecision(action="block", message="x", reason_code="X", appeal_url=None)

        register_artefact_reviewer(WatchingBlocker())

        upstream = _upstream_resources_read_response("<html/>")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=upstream)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = _make_client()
        # Body is raw bytes, not JSON.
        resp = client.post(
            "/mcp/map-server",
            content=b"not valid json {{{",
            headers={"content-type": "application/octet-stream"},
        )

        assert resp.status_code == 200, resp.text
        assert review_calls == [], "non-JSON body must short-circuit to pass-through"
