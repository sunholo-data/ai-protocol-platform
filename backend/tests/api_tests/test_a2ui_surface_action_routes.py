"""API tests for ``POST /api/sessions/{id}/surface-action`` (sprint 2.10).

Sibling of ``test_iframe_context_routes.py``: same test pattern,
different surface. The endpoint receives ``A2uiClientAction`` events
emitted by ``SurfaceModel.onAction`` on user clicks / form submits etc.

Security gates exercised:
  * Firebase auth required (401)
  * Session must exist (404)
  * Caller must be able to access the session (403)
  * Skill backing the session must exist (403)
  * Skill must have ``tool_configs.a2ui`` (403 — A2UI-enabled skills only)
  * Skill must opt in via
    ``tool_configs.a2ui.allow_surface_context_writes: true`` (403 default-deny)
  * ``action.context`` must be ≤ 4 KB serialized (413), valid object (422)
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
from protocols.a2ui_surface_action_routes import router

# ---------------------------------------------------------------------------
# Test app + auth fixtures (mirror of iframe_context test scaffolding)
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
    test_app = FastAPI()
    test_app.include_router(router)
    return TestClient(test_app)


def _make_index(
    session_id: str = "sess-1",
    skill_id: str = "skill-1",
    owner_uid: str = "viewer",
    ac: AccessControl | None = None,
) -> ChatSessionIndex:
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
    a2ui_config: dict | None = None,
    ac: AccessControl | None = None,
) -> SkillConfig:
    """Build a SkillConfig with ``tool_configs.a2ui`` set."""
    tool_configs: dict = {}
    if a2ui_config is not None:
        tool_configs["a2ui"] = a2ui_config
    return SkillConfig(
        skillId=skill_id,
        name="test-skill",
        description="a test skill",
        ownerId=owner_uid,
        ownerEmail=f"{owner_uid}@example.com",
        accessControl=ac or AccessControl(type="public"),
        skillMetadata={"tools": [], "toolConfigs": tool_configs},
    )


def _mock_session_service() -> MagicMock:
    session = MagicMock()
    session.state = {}
    svc = MagicMock()
    svc.get_session = AsyncMock(return_value=session)
    svc.append_event = AsyncMock()
    return svc


_HAPPY_BODY = {
    "surfaceId": "workspace",
    "action": {
        "name": "approve",
        "sourceComponentId": "row-47",
        "context": {"id": 47, "status": "pending"},
    },
}

_OPTED_IN_A2UI = {
    "default_surface": "workspace",
    "allow_surface_context_writes": True,
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @patch("protocols.a2ui_surface_action_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_writes_namespaced_state_when_authorized(self, mock_get_index, mock_skill_module, mock_get_svc):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)

        assert resp.status_code == 204, resp.text

        svc.append_event.assert_awaited_once()
        event_arg = svc.append_event.await_args.args[1]
        delta = event_arg.actions.state_delta
        assert "a2ui_surface_context.workspace.lastAction" in delta
        written = delta["a2ui_surface_context.workspace.lastAction"]
        assert written["name"] == "approve"
        assert written["sourceComponentId"] == "row-47"
        assert written["context"] == {"id": 47, "status": "pending"}
        assert "_pushedAt" in written

    @patch("protocols.a2ui_surface_action_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_session_lookup_uses_canonical_app_name_not_skill_id(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Regression for the bug found during sprint 2.10 follow-up live
        smoke: the route was passing `app_name=idx.skill_id` to
        `session_service.get_session`, but ADK keys sessions under the
        canonical `APP_NAME = "aitana_platform"` (set by
        build_agui_adk_agent). Wrong key → 404 every time → action POST
        silently broken. Mock check guards the storage key shape."""
        mock_get_index.return_value = _make_index(skill_id="some-skill")
        mock_skill_module.get_skill.return_value = _make_skill(skill_id="some-skill", a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 204, resp.text

        # The session lookup MUST use APP_NAME, not the skill id.
        svc.get_session.assert_awaited_once()
        kwargs = svc.get_session.await_args.kwargs
        assert kwargs["app_name"] == "aitana_platform"
        assert kwargs["app_name"] != "some-skill"

    @patch("protocols.a2ui_surface_action_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_action_without_optional_fields(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Minimal action — just ``name``, no sourceComponentId or context.
        Still writes; the namespaced entry just has fewer fields."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={"surfaceId": "workspace", "action": {"name": "refresh"}},
        )
        assert resp.status_code == 204, resp.text

        delta = svc.append_event.await_args.args[1].actions.state_delta
        written = delta["a2ui_surface_context.workspace.lastAction"]
        assert written["name"] == "refresh"
        assert "sourceComponentId" not in written
        assert "context" not in written

    @patch("protocols.a2ui_surface_action_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_multiple_surfaces_get_separate_namespaces(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Two surfaces (workspace + sidebar) each have their own
        namespaced lastAction — neither overwrites the other."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        for sid in ("workspace", "sidebar"):
            resp = client.post(
                "/api/sessions/sess-1/surface-action",
                json={"surfaceId": sid, "action": {"name": "click"}},
            )
            assert resp.status_code == 204, resp.text

        keys = [next(iter(call.args[1].actions.state_delta.keys())) for call in svc.append_event.await_args_list]
        assert "a2ui_surface_context.workspace.lastAction" in keys
        assert "a2ui_surface_context.sidebar.lastAction" in keys

    @patch("protocols.a2ui_surface_action_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_overwrite_on_repeated_action(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Posting two actions on the same surface produces two writes
        under the SAME key — second overwrites the first (agent reads
        most recent only)."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        for name in ("click-a", "click-b"):
            resp = client.post(
                "/api/sessions/sess-1/surface-action",
                json={"surfaceId": "workspace", "action": {"name": name}},
            )
            assert resp.status_code == 204, resp.text

        assert svc.append_event.await_count == 2
        deltas = [c.args[1].actions.state_delta for c in svc.append_event.await_args_list]
        for d in deltas:
            assert "a2ui_surface_context.workspace.lastAction" in d


# ---------------------------------------------------------------------------
# Auth + access boundary
# ---------------------------------------------------------------------------


class TestAuthAndAccess:
    def test_rejects_401_when_unauthenticated(self):
        client = _make_no_auth_client()
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code in (401, 403, 422)

    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_404_when_session_unknown(self, mock_get_index):
        mock_get_index.return_value = None
        client = _make_client("viewer")
        resp = client.post("/api/sessions/missing/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 404

    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_no_session_access(self, mock_get_index):
        mock_get_index.return_value = _make_index(
            owner_uid="someone-else",
            ac=AccessControl(type="private"),
        )
        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Skill-existence + a2ui-config gates
# ---------------------------------------------------------------------------


class TestSkillGate:
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_skill_deleted(self, mock_get_index, mock_skill_module):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = None
        # The gate is alias-tolerant now (CLAUDE.md #9): a miss on the canonical
        # id falls back to a slug lookup, so a genuinely deleted skill must miss
        # BOTH before the gate denies.
        mock_skill_module.resolve_skill_ref.return_value = None

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 403

    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_skill_has_no_a2ui_config(self, mock_get_index, mock_skill_module):
        """Skill exists but has no ``tool_configs.a2ui`` at all."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill()  # no a2ui config

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 403
        assert "A2UI tool_config" in resp.text


# ---------------------------------------------------------------------------
# Per-skill context-write opt-in gate (the central new gate)
# ---------------------------------------------------------------------------


class TestContextWriteOptInGate:
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_a2ui_config_present_but_opt_in_false(self, mock_get_index, mock_skill_module):
        """Skill renders A2UI surfaces (has default_surface) but did NOT
        flip allow_surface_context_writes — push must be rejected.
        Distinct trust grants: rendering ≠ writing back to context."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            a2ui_config={"default_surface": "workspace"}  # no allow flag
        )

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 403
        assert "allow_surface_context_writes" in resp.text

    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_opt_in_false_explicit(self, mock_get_index, mock_skill_module):
        """Explicit `false` (not just absent) also rejected."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(
            a2ui_config={"default_surface": "workspace", "allow_surface_context_writes": False}
        )

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-action", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Schema + size validation
# ---------------------------------------------------------------------------


class TestSchemaAndSize:
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_413_when_context_oversized(self, mock_get_index, mock_skill_module):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)

        big_payload = {"data": "x" * 5000}  # ~5 KB serialized
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={
                "surfaceId": "workspace",
                "action": {"name": "click", "context": big_payload},
            },
        )
        assert resp.status_code == 413

    def test_returns_422_when_action_missing(self):
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={"surfaceId": "workspace"},
        )
        assert resp.status_code == 422

    def test_returns_422_when_surface_id_missing(self):
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={"action": {"name": "click"}},
        )
        assert resp.status_code == 422

    def test_returns_422_when_action_name_missing(self):
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={"surfaceId": "workspace", "action": {}},
        )
        assert resp.status_code == 422

    def test_returns_422_when_unknown_field_present(self):
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={
                "surfaceId": "workspace",
                "action": {"name": "click"},
                "evilExtraField": "anything",
            },
        )
        assert resp.status_code == 422

    def test_returns_422_when_action_context_is_array(self):
        """Schema violation: action.context must be a JSON object, not an array."""
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-action",
            json={
                "surfaceId": "workspace",
                "action": {"name": "click", "context": [1, 2, 3]},
            },
        )
        assert resp.status_code == 422
