"""API tests for /api/admin/users — per-user group-tag grant/revoke.

The Firebase Admin SDK is mocked via admin.users_routes._fb_auth. Tests exercise:
  - admin guard (aitana-admin) on every route
  - get: shape + tag read
  - grant: idempotent union, writes groupTags claim
  - revoke: removes the tag
  - 404 on unknown email
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_ADMIN = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_NON_ADMIN = User(
    uid="user-uid",
    email="user@example.com",
    domain="example.com",
    group_tags=frozenset(),
)


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
    """A fake firebase_admin.auth with a single known user."""
    rec = MagicMock()
    rec.uid = uid
    rec.custom_claims = {"groupTags": list(tags)} if tags else {}
    fb = MagicMock()
    fb.get_user_by_email.return_value = rec
    return fb, rec


def test_get_user_groups(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=["ONE"])
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.get("/api/admin/users/alice@one.com")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@one.com"
    assert body["uid"] == "u1"
    assert body["group_tags"] == ["ONE"]


def test_grant_group_is_idempotent_union(admin_client: TestClient) -> None:
    fb, _rec = _fake_fb(tags=["ONE"])
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "aitana-admin"})
    assert resp.status_code == 200
    assert resp.json()["group_tags"] == ["ONE", "aitana-admin"]
    # The claim was written with the union, preserving other claims.
    fb.set_custom_user_claims.assert_called_once()
    uid_arg, claims_arg = fb.set_custom_user_claims.call_args[0]
    assert uid_arg == "u1"
    assert claims_arg["groupTags"] == ["ONE", "aitana-admin"]


def test_grant_empty_tag_400(admin_client: TestClient) -> None:
    fb, _ = _fake_fb()
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.post("/api/admin/users/alice@one.com/groups", json={"tag": "  "})
    assert resp.status_code == 400


def test_revoke_group(admin_client: TestClient) -> None:
    fb, _ = _fake_fb(tags=["ONE", "aitana-admin"])
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.delete("/api/admin/users/alice@one.com/groups/aitana-admin")
    assert resp.status_code == 200
    assert resp.json()["group_tags"] == ["ONE"]


def test_get_unknown_email_404(admin_client: TestClient) -> None:
    fb = MagicMock()
    fb.get_user_by_email.side_effect = ValueError("no such user")
    with patch("admin.users_routes._fb_auth", return_value=fb):
        resp = admin_client.get("/api/admin/users/nobody@nowhere.com")
    assert resp.status_code == 404


def test_grant_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.post("/api/admin/users/x@y.com/groups", json={"tag": "ONE"})
    assert resp.status_code == 403


def test_get_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.get("/api/admin/users/x@y.com")
    assert resp.status_code == 403
