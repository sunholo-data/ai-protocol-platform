"""API tests for the iframe-context endpoint (sprint 1.25).

The endpoint lives at ``POST /api/sessions/{session_id}/iframe-context``.
It receives ``ui/update-model-context`` pushes from MCP App iframes (via
the host's ``<AppRenderer onUpdateModelContext>`` callback) and writes
the structured content into the ADK session state under a namespaced key
(``mcp_app_context.{server_id}.{tool_name}``) for the agent's NEXT turn
to reference.

Security boundary tested here (seven gates from the design doc, each
its own test class for clarity):
  * Firebase auth required (401 if missing).
  * Session must exist (404 if not).
  * Caller must be able to access the session (403 via the existing
    5-type access policy).
  * Skill backing the session must exist (403 if deleted).
  * ``serverId`` must be in ``skill.tool_configs.mcp.servers`` (403).
  * ``serverId`` must additionally be in
    ``skill.tool_configs.mcp.allow_context_writes`` (403). This is the
    NEW per-server opt-in gate — the design's central additional
    safeguard so "skill activates server" doesn't auto-grant
    "iframe writes context".
  * ``structuredContent`` ≤ 4 KB serialized (413), valid object (400).

Tests use a TestClient with mocked Firestore lookups + ADK session
service patched. No network is touched. Pattern mirrors
``test_mcp_proxy``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.access_context import AccessContext
from db.models import SkillConfig
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex
from protocols.iframe_context_routes import router

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


def _make_index(
    session_id: str = "sess-1",
    skill_id: str = "skill-1",
    owner_uid: str = "viewer",
    ac: AccessControl | None = None,
) -> ChatSessionIndex:
    """Build a ChatSessionIndex matching the Firestore document shape."""
    now = datetime.now(UTC)
    return ChatSessionIndex(
        sessionId=session_id,
        documentIds=[],
        skillId=skill_id,
        ownerUid=owner_uid,
        accessControl=ac or AccessControl(type="public"),
        title=None,
        turnCount=0,
        firstMessageAt=now,
        lastMessageAt=now,
        archivedAt=None,
    )


def _make_skill(
    skill_id: str = "skill-1",
    owner_uid: str = "viewer",
    activated_servers: list[str] | None = None,
    context_write_servers: list[str] | None = None,
    ac: AccessControl | None = None,
) -> SkillConfig:
    """Build a SkillConfig with the relevant tool_configs.mcp branches set."""
    mcp_config: dict = {}
    if activated_servers is not None:
        mcp_config["servers"] = list(activated_servers)
    if context_write_servers is not None:
        mcp_config["allow_context_writes"] = list(context_write_servers)
    tool_configs: dict = {"mcp": mcp_config} if mcp_config else {}
    return SkillConfig(
        skillId=skill_id,
        name="test-skill",
        description="a test skill",
        ownerId=owner_uid,
        ownerEmail=f"{owner_uid}@example.com",
        accessControl=ac or AccessControl(type="public"),
        skillMetadata={"tools": ["mcp"], "toolConfigs": tool_configs},
    )


def _mock_session_service(state_after_write: dict | None = None) -> MagicMock:
    """Return a MagicMock standing in for the ADK session service.

    ``get_session`` returns a session-like object; ``append_event``
    captures the EventActions.state_delta so tests can assert on it.
    """
    session = MagicMock()
    session.state = state_after_write or {}

    svc = MagicMock()
    svc.get_session = AsyncMock(return_value=session)
    svc.append_event = AsyncMock()
    return svc


_HAPPY_BODY = {
    "serverId": "ext-apps-map",
    "toolName": "show-map",
    "structuredContent": {
        "viewUUID": "abc-123",
        "currentBounds": {"west": 11.4, "south": 48.0, "east": 11.7, "north": 48.2},
        "label": "Munich",
    },
}


# ---------------------------------------------------------------------------
# Happy path — gate 7 (size cap) inclusive
# ---------------------------------------------------------------------------


class TestHappyPath:
    @patch("protocols.iframe_context_routes.get_session_service")
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_session_lookup_uses_canonical_app_name_not_skill_id(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Regression for the latent bug found during sprint 2.10
        follow-up live smoke. The route was passing
        `app_name=idx.skill_id` to `session_service.get_session`, but
        ADK keys sessions under canonical `APP_NAME = "aitana_platform"`
        (set by build_agui_adk_agent). Wrong key → 404 every time →
        iframe-context POST silently broken in production. Earlier mocked
        tests passed because the MagicMock returned a session regardless
        of args. Sibling fix in a2ui_surface_action_routes.py."""
        mock_get_index.return_value = _make_index(skill_id="some-skill")
        mock_skill_module.get_skill.return_value = _make_skill(
            skill_id="some-skill",
            activated_servers=["ext-apps-map"],
            context_write_servers=["ext-apps-map"],
        )
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 204, resp.text

        svc.get_session.assert_awaited_once()
        kwargs = svc.get_session.await_args.kwargs
        assert kwargs["app_name"] == "aitana_platform"
        assert kwargs["app_name"] != "some-skill"

    @patch("protocols.iframe_context_routes.get_session_service")
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_writes_namespaced_state_when_authorized(self, mock_get_index, mock_skill_module, mock_get_svc):
        """End-to-end happy path: authed user, accessible session, server
        activated AND opted-in, payload within size cap → 204 + state
        write to ``mcp_app_context.{server_id}.{tool_name}``."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            context_write_servers=["ext-apps-map"],
        )
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)

        assert resp.status_code == 204, resp.text

        # Assert ADK got the state-delta write
        svc.append_event.assert_awaited_once()
        event_arg = svc.append_event.await_args.args[1]
        delta = event_arg.actions.state_delta
        assert "mcp_app_context.ext-apps-map.show-map" in delta
        written = delta["mcp_app_context.ext-apps-map.show-map"]
        assert written["structuredContent"] == _HAPPY_BODY["structuredContent"]
        assert "_pushedAt" in written  # timestamp stamped server-side

    @patch("protocols.iframe_context_routes.get_session_service")
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_empty_payload_is_no_op(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Iframes that fire ``ui/update-model-context`` with no
        structured content (e.g. a bug or a no-op clear) should NOT
        produce empty state writes — keeps the namespaced state tidy."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            context_write_servers=["ext-apps-map"],
        )
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/iframe-context",
            json={
                "serverId": "ext-apps-map",
                "toolName": "show-map",
                "structuredContent": None,
                "content": None,
            },
        )

        assert resp.status_code == 204, resp.text
        # No append_event fired because the payload was empty.
        svc.append_event.assert_not_awaited()

    @patch("protocols.iframe_context_routes.get_session_service")
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_idempotent_overwrite(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Posting twice for the same (server, tool) should produce two
        writes — second overwrites the first under the same namespaced
        key. We assert the namespaced key stays the same so the agent
        always sees the latest iframe state, not history."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            context_write_servers=["ext-apps-map"],
        )
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        for _ in range(2):
            resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
            assert resp.status_code == 204, resp.text

        assert svc.append_event.await_count == 2
        deltas = [call.args[1].actions.state_delta for call in svc.append_event.await_args_list]
        assert all("mcp_app_context.ext-apps-map.show-map" in d for d in deltas)

    @patch("protocols.iframe_context_routes.get_session_service")
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_multiple_servers_get_separate_namespaces(self, mock_get_index, mock_skill_module, mock_get_svc):
        """If two different servers are activated AND opted-in, each one's
        push lands under its own namespace and neither overwrites the
        other (keys differ on the server_id segment)."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["server-a", "server-b"],
            context_write_servers=["server-a", "server-b"],
        )
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        for srv in ("server-a", "server-b"):
            resp = client.post(
                "/api/sessions/sess-1/iframe-context",
                json={
                    "serverId": srv,
                    "toolName": "tool-x",
                    "structuredContent": {"who": srv},
                },
            )
            assert resp.status_code == 204, resp.text

        keys_written = [
            next(iter(call.args[1].actions.state_delta.keys())) for call in svc.append_event.await_args_list
        ]
        assert "mcp_app_context.server-a.tool-x" in keys_written
        assert "mcp_app_context.server-b.tool-x" in keys_written


# ---------------------------------------------------------------------------
# Auth + access boundary
# ---------------------------------------------------------------------------


class TestAuthAndAccess:
    def test_rejects_401_when_unauthenticated(self):
        """Without a Firebase JWT the route must reject before lookup."""
        client = _make_no_auth_client()
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code in (401, 403, 422)

    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_404_when_session_unknown(self, mock_get_index):
        mock_get_index.return_value = None
        client = _make_client("viewer")
        resp = client.post("/api/sessions/missing/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 404

    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_no_session_access(self, mock_get_index):
        """Caller is not the owner AND access policy denies (private
        session, public skill not enough — session-level access policy
        gates the write)."""
        mock_get_index.return_value = _make_index(
            owner_uid="someone-else",
            ac=AccessControl(type="private"),
        )
        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Skill activation gate (5)
# ---------------------------------------------------------------------------


class TestSkillActivationGate:
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_skill_deleted(self, mock_get_index, mock_skill_module):
        """Session points at a skill_id that no longer exists in
        Firestore (deleted between session creation and this push)."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = None

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403

    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_server_not_activated(self, mock_get_index, mock_skill_module):
        """Skill exists but doesn't activate the server the iframe
        claims to be from. Closes the "iframe pretends to be from a
        server the skill doesn't use" attack."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["other-server"],
            context_write_servers=["other-server"],
        )

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403
        assert "ext-apps-map" in resp.text

    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_skill_has_no_mcp_config(self, mock_get_index, mock_skill_module):
        """Skill doesn't even use MCP — empty tool_configs.mcp."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill()  # no MCP config

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Per-server context-write opt-in gate (6) — the NEW gate this design adds
# ---------------------------------------------------------------------------


class TestContextWriteOptInGate:
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_server_activated_but_not_opted_in(self, mock_get_index, mock_skill_module):
        """The crucial gate: skill DOES activate the server (so the
        agent can call its tools), but does NOT opt the server into
        context-writes. Push must be rejected — distinct trust grants."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            context_write_servers=[],  # explicit empty allowlist
        )

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403
        assert "allow_context_writes" in resp.text

    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_403_when_allowlist_field_absent(self, mock_get_index, mock_skill_module):
        """Default behaviour: no ``allow_context_writes`` field at all
        means feature is OFF for this server (default-deny). Skills
        must explicitly opt in."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            # context_write_servers omitted entirely
        )

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/iframe-context", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Schema + size validation (7)
# ---------------------------------------------------------------------------


class TestSchemaAndSize:
    @patch("protocols.iframe_context_routes.skill_config")
    @patch("protocols.iframe_context_routes.get_session_index")
    def test_returns_413_when_structured_content_oversized(self, mock_get_index, mock_skill_module):
        """A 5 KB structuredContent must be rejected (cap is 4 KB)."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            activated_servers=["ext-apps-map"],
            context_write_servers=["ext-apps-map"],
        )

        big_payload = {"data": "x" * 5000}  # ~5 KB serialized
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/iframe-context",
            json={
                "serverId": "ext-apps-map",
                "toolName": "show-map",
                "structuredContent": big_payload,
            },
        )
        assert resp.status_code == 413

    def test_returns_422_when_structured_content_is_string(self):
        """Schema violation: structuredContent must be an object/null,
        not a string (Pydantic's job; FastAPI returns 422)."""
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/iframe-context",
            json={
                "serverId": "ext-apps-map",
                "toolName": "show-map",
                "structuredContent": "not an object",
            },
        )
        assert resp.status_code == 422

    def test_returns_422_when_server_id_missing(self):
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/iframe-context",
            json={"toolName": "show-map", "structuredContent": {}},
        )
        assert resp.status_code == 422

    def test_returns_422_when_unknown_field_present(self):
        """``extra: forbid`` on the Pydantic model should reject
        unexpected fields (defends against client typos + future
        forward-compat surprises that the host hasn't audited)."""
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/iframe-context",
            json={
                "serverId": "ext-apps-map",
                "toolName": "show-map",
                "structuredContent": {},
                "evilExtraField": "anything",
            },
        )
        assert resp.status_code == 422
