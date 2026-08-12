"""API tests for ``POST /api/sessions/{id}/surface-data`` (stash-update hook).

The endpoint lets the frontend persist a client-edited surface data model
(e.g. an obligation what-if scenario) into the 7.5 rehydration stash
(``a2ui_surface:{surfaceId}`` session state) so the session-history GET
replays the edited state after a hard refresh.

Security gates exercised (sibling of ``test_a2ui_surface_action_routes.py``):
  * Firebase auth required (401)
  * Session must exist (404)
  * Caller must be able to access the session (403)
  * Caller must be the session OWNER (403 — viewers can't rewrite the stash)
  * Skill gates: exists + a2ui config + allow_surface_context_writes (403)
  * dataModel ≤ 256 KiB serialized (413)
  * Stash entry must exist — the client can't CREATE surfaces (404)

Merge semantics exercised:
  * clientDataModel block set on write; canonical stash fields untouched
  * ``dataModel: null`` clears a previous clientDataModel block
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.access_context import AccessContext
from db.models import SkillConfig
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex
from protocols.a2ui_surface_data_routes import router

# ---------------------------------------------------------------------------
# Test app + fixtures (mirror of the surface-action test scaffolding)
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
) -> SkillConfig:
    tool_configs: dict = {}
    if a2ui_config is not None:
        tool_configs["a2ui"] = a2ui_config
    return SkillConfig(
        skillId=skill_id,
        name="test-skill",
        description="a test skill",
        ownerId=owner_uid,
        ownerEmail=f"{owner_uid}@example.com",
        accessControl=AccessControl(type="public"),
        skillMetadata={"tools": [], "toolConfigs": tool_configs},
    )


_OPTED_IN_A2UI = {
    "default_surface": "workspace",
    "allow_surface_context_writes": True,
}

_SURFACE_ID = "obligation_analysis:doc-xyz"
_STASH_KEY = f"a2ui_surface:{_SURFACE_ID}"


def _stash_payload(**extra) -> dict:
    """A canonical emitter-written stash entry for the obligation surface."""
    return {
        "surfaceId": _SURFACE_ID,
        "messages": [
            {"version": "v0.9", "createSurface": {"surfaceId": _SURFACE_ID}},
            {
                "version": "v0.9",
                "updateDataModel": {"surfaceId": _SURFACE_ID, "value": {"payload": {"doc_id": "doc-xyz"}}},
            },
        ],
        "artifact": {"kind": "obligation-analysis", "title": "Obligations"},
        "sourceId": "inv:map_ppa_obligations:1",
        "toolName": "map_ppa_obligations",
        "createdAt": 1000.0,
        **extra,
    }


def _mock_session_service(state: dict | None = None) -> MagicMock:
    session = MagicMock()
    session.state = state if state is not None else {_STASH_KEY: json.dumps(_stash_payload())}
    svc = MagicMock()
    svc.get_session = AsyncMock(return_value=session)
    svc.append_event = AsyncMock()
    return svc


_HAPPY_BODY = {
    "surfaceId": _SURFACE_ID,
    "dataModel": {
        "payload": {"doc_id": "doc-xyz"},
        "scenario": {"deadlineDelta": {"COD": 21}, "waive": {"COD-payment": True}},
    },
}


def _written_stash(svc: MagicMock) -> dict:
    event_arg = svc.append_event.await_args.args[1]
    delta = event_arg.actions.state_delta
    assert _STASH_KEY in delta
    return json.loads(delta[_STASH_KEY])


# ---------------------------------------------------------------------------
# Happy path + merge semantics
# ---------------------------------------------------------------------------


class TestHappyPath:
    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_merges_client_data_model_into_stash(self, mock_get_index, mock_skill_module, mock_get_svc):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 204, resp.text

        written = _written_stash(svc)
        # Client edit landed…
        assert written["clientDataModel"]["value"] == _HAPPY_BODY["dataModel"]
        assert "updatedAt" in written["clientDataModel"]
        # …and the canonical emitter-written fields are untouched.
        canonical = _stash_payload()
        assert written["messages"] == canonical["messages"]
        assert written["artifact"] == canonical["artifact"]
        assert written["sourceId"] == canonical["sourceId"]
        assert written["createdAt"] == canonical["createdAt"]

    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_null_data_model_clears_client_block(self, mock_get_index, mock_skill_module, mock_get_svc):
        """`dataModel: null` = "reset to extracted" — drops the client block so
        the canonical tool render rehydrates again."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        stash = _stash_payload(clientDataModel={"value": {"payload": {}, "scenario": {"x": 1}}, "updatedAt": 2000.0})
        svc = _mock_session_service(state={_STASH_KEY: json.dumps(stash)})
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-data",
            json={"surfaceId": _SURFACE_ID, "dataModel": None},
        )
        assert resp.status_code == 204, resp.text

        written = _written_stash(svc)
        assert "clientDataModel" not in written
        assert written["messages"] == _stash_payload()["messages"]

    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_repeated_writes_replace_not_accumulate(self, mock_get_index, mock_skill_module, mock_get_svc):
        """Each write REPLACES clientDataModel — messages never grow."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        stash = _stash_payload(clientDataModel={"value": {"scenario": {"old": True}}, "updatedAt": 2000.0})
        svc = _mock_session_service(state={_STASH_KEY: json.dumps(stash)})
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 204, resp.text

        written = _written_stash(svc)
        assert written["clientDataModel"]["value"] == _HAPPY_BODY["dataModel"]
        assert len(written["messages"]) == len(_stash_payload()["messages"])

    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_session_lookup_uses_canonical_app_name(self, mock_get_index, mock_skill_module, mock_get_svc):
        """ADK sessions are keyed under APP_NAME, not the skill id (the
        surface-action route shipped with this bug — guard it here too)."""
        mock_get_index.return_value = _make_index(skill_id="some-skill")
        mock_skill_module.get_skill.return_value = _make_skill(skill_id="some-skill", a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service()
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 204, resp.text

        kwargs = svc.get_session.await_args.kwargs
        assert kwargs["app_name"] == "aitana_platform"


# ---------------------------------------------------------------------------
# Auth + access boundary
# ---------------------------------------------------------------------------


class TestAuthAndAccess:
    def test_rejects_401_when_unauthenticated(self):
        client = _make_no_auth_client()
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code in (401, 403, 422)

    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_404_when_session_unknown(self, mock_get_index):
        mock_get_index.return_value = None
        client = _make_client("viewer")
        resp = client.post("/api/sessions/missing/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 404

    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_no_session_access(self, mock_get_index):
        mock_get_index.return_value = _make_index(
            owner_uid="someone-else",
            ac=AccessControl(type="private"),
        )
        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 403

    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_for_non_owner_even_with_read_access(self, mock_get_index, mock_skill_module):
        """A PUBLIC session is readable by anyone authed — but only the OWNER
        may rewrite its rehydration stash. Write ≠ read."""
        mock_get_index.return_value = _make_index(
            owner_uid="alice",
            ac=AccessControl(type="public"),
        )
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)

        client = _make_client("bob")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Skill opt-in gates (shared with surface-action)
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
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 403

    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_403_when_not_opted_into_surface_writes(self, mock_get_index, mock_skill_module):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config={"default_surface": "workspace"})
        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 403
        assert "allow_surface_context_writes" in resp.text


# ---------------------------------------------------------------------------
# Stash-existence + size gates
# ---------------------------------------------------------------------------


class TestStashAndSizeGates:
    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_404_when_no_stash_entry(self, mock_get_index, mock_skill_module, mock_get_svc):
        """The client can only UPDATE surfaces the backend emitter stashed —
        never create new ones."""
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service(state={})
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 404
        svc.append_event.assert_not_awaited()

    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_404_when_stash_entry_unparseable(self, mock_get_index, mock_skill_module, mock_get_svc):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = _mock_session_service(state={_STASH_KEY: "{not json"})
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 404

    @patch("protocols.a2ui_surface_data_routes.get_session_service")
    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_404_when_adk_session_missing(self, mock_get_index, mock_skill_module, mock_get_svc):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)
        svc = MagicMock()
        svc.get_session = AsyncMock(return_value=None)
        mock_get_svc.return_value = svc

        client = _make_client("viewer")
        resp = client.post("/api/sessions/sess-1/surface-data", json=_HAPPY_BODY)
        assert resp.status_code == 404

    @patch("protocols._a2ui_surface_shared.skill_config")
    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_returns_413_when_data_model_too_large(self, mock_get_index, mock_skill_module):
        mock_get_index.return_value = _make_index()
        mock_skill_module.get_skill.return_value = _make_skill(a2ui_config=_OPTED_IN_A2UI)

        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-data",
            json={"surfaceId": _SURFACE_ID, "dataModel": {"blob": "x" * 262_145}},
        )
        assert resp.status_code == 413

    @patch("protocols._a2ui_surface_shared.get_session_index")
    def test_rejects_non_object_data_model(self, mock_get_index):
        """dataModel must be an object or null — a bare string/array is a 422."""
        mock_get_index.return_value = _make_index()
        client = _make_client("viewer")
        resp = client.post(
            "/api/sessions/sess-1/surface-data",
            json={"surfaceId": _SURFACE_ID, "dataModel": ["not", "an", "object"]},
        )
        assert resp.status_code == 422
