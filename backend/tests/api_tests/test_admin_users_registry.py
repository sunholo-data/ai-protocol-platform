"""API tests for the v6.9.0 9.3 additions to /api/admin/users:
- grant validates the tag against the registry (422 on unknown)
- grant/revoke carry a propagation block (NEVER-SILENT staleness signal)
- POST /{email}/refresh-claims forces propagation via revoke_refresh_tokens
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_ADMIN = User(
    uid="admin-uid", email="owner@yourcompany.com", domain="yourcompany.com", group_tags=frozenset({"aitana-admin"})
)
_NON_ADMIN = User(uid="user-uid", email="user@example.com", domain="example.com", group_tags=frozenset())


def _make_app() -> FastAPI:
    from admin.users_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _install_user(app: FastAPI, user: User) -> FastAPI:
    async def _override(request: Request) -> User:
        return user

    app.dependency_overrides[get_current_user] = _override
    return app


@pytest.fixture()
def admin_client() -> TestClient:
    return TestClient(_install_user(_make_app(), _ADMIN))


@pytest.fixture()
def nonadmin_client() -> TestClient:
    return TestClient(_install_user(_make_app(), _NON_ADMIN))


def _fake_fb(uid="u1", tags=None):
    rec = MagicMock()
    rec.uid = uid
    rec.custom_claims = {"groupTags": list(tags)} if tags else {}
    fb = MagicMock()
    fb.get_user_by_email.return_value = rec
    return fb, rec


# --- registry validation --------------------------------------------------


def test_grant_unknown_tag_422_when_registry_populated(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=[])
    # Registry populated but does NOT contain "TYPO" → is_known_tag False → 422.
    with (
        patch("admin.group_tags_routes.query_documents", return_value=[{"__id": "ONE"}]),
        patch("admin.users_routes._fb_auth", return_value=fb),
    ):
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "TYPO"})
    assert resp.status_code == 422
    assert "Unknown group tag" in resp.json()["detail"]


def test_grant_known_registry_tag_ok(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=[])
    with (
        patch("admin.group_tags_routes.query_documents", return_value=[{"__id": "ONE"}]),
        patch("admin.users_routes._fb_auth", return_value=fb),
    ):
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "ONE"})
    assert resp.status_code == 200
    assert resp.json()["group_tags"] == ["ONE"]


def test_grant_bootstrap_when_registry_empty(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=[])
    # Empty registry → bootstrap escape → any tag grantable.
    with (
        patch("admin.group_tags_routes.query_documents", return_value=[]),
        patch("admin.users_routes._fb_auth", return_value=fb),
    ):
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "BRAND-NEW"})
    assert resp.status_code == 200
    assert resp.json()["group_tags"] == ["BRAND-NEW"]


# --- propagation block ----------------------------------------------------


def test_grant_carries_propagation_block(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=[])
    with patch("admin.users_routes._fb_auth", return_value=fb):
        # structural tag → no registry read needed
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "aitana-admin"})
    assert resp.status_code == 200
    prop = resp.json()["propagation"]
    assert prop["effective"] == "on_next_refresh"
    assert prop["tokenTtlSeconds"] == 3600


def test_revoke_carries_propagation_block(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=["ONE"])
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.delete("/api/admin/users/alice@one.com/groups/ONE")
    assert resp.status_code == 200
    assert resp.json()["propagation"]["effective"] == "on_next_refresh"


# --- force refresh --------------------------------------------------------


def test_refresh_claims_revokes_and_audits(admin_client: TestClient) -> None:
    fb, _rec = _fake_fb(tags=["ONE"])
    with (
        patch("admin.users_routes._fb_auth", return_value=fb),
        patch("admin.users_routes.record_admin_action") as mock_audit,
    ):
        resp = admin_client.post("/api/admin/users/alice@one.com/refresh-claims")
    assert resp.status_code == 200
    fb.revoke_refresh_tokens.assert_called_once_with("u1")
    body = resp.json()
    assert body["propagation"]["effective"] == "forced"
    assert mock_audit.call_args.kwargs["action"] == "refresh_claims"


def test_refresh_claims_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.post("/api/admin/users/x@y.com/refresh-claims")
    assert resp.status_code == 403


def test_refresh_claims_unknown_user_404(admin_client: TestClient) -> None:
    fb = MagicMock()
    fb.get_user_by_email.side_effect = ValueError("no such user")
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.post("/api/admin/users/ghost@x.com/refresh-claims")
    assert resp.status_code == 404
