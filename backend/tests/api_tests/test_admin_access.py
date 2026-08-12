"""API tests for POST /api/admin/access/check — effective-access dry-run.

Firebase + Firestore mocked. Exercises:
  - admin guard (aitana-admin)
  - tag union with provenance (direct / domain-derived / both)
  - tool-permission decision mirrors permissions.can_use_tool lookup order
  - graceful degrade when the email isn't a Firebase user (user_found=False)
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
    from admin.access_routes import router

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
    return fb


def test_access_check_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.post("/api/admin/access/check", json={"email": "x@y.com"})
    assert resp.status_code == 403


def test_access_check_tag_union_provenance(admin_client: TestClient) -> None:
    fb = _fake_fb(tags=["DIRECT", "SHARED"])
    with (
        patch("admin.access_routes._fb_auth", return_value=fb),
        patch("admin.access_routes.resolve_derived_group_tags", return_value=frozenset({"DERIVED", "SHARED"})),
    ):
        resp = admin_client.post("/api/admin/access/check", json={"email": "alice@one.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_found"] is True
    assert body["uid"] == "u1"
    assert body["domain"] == "one.com"
    by_tag = {t["tag"]: t["provenances"] for t in body["tags"]}
    assert by_tag["DIRECT"] == ["direct"]
    assert by_tag["DERIVED"] == ["domain-derived"]
    assert sorted(by_tag["SHARED"]) == ["direct", "domain-derived"]


def test_access_check_tool_permission_user_level(admin_client: TestClient) -> None:
    fb = _fake_fb(tags=[])

    def _get_doc(collection, doc_id):
        # user-level allow doc wins (mirrors can_use_tool order).
        if doc_id == "alice@one.com":
            return {"type": "user", "tools": ["search"]}
        return None

    with (
        patch("admin.access_routes._fb_auth", return_value=fb),
        patch("admin.access_routes.resolve_derived_group_tags", return_value=frozenset()),
        patch("admin.access_routes.fs.get_document", side_effect=_get_doc),
    ):
        resp = admin_client.post("/api/admin/access/check", json={"email": "alice@one.com", "toolName": "search"})
    assert resp.status_code == 200
    tp = resp.json()["tool_permission"]
    assert tp["tool"] == "search"
    assert tp["allowed"] is True
    assert tp["provenance"] == "tool-perm"
    assert "user-level" in tp["reason"]


def test_access_check_tool_permission_default_deny(admin_client: TestClient) -> None:
    fb = _fake_fb(tags=[])
    with (
        patch("admin.access_routes._fb_auth", return_value=fb),
        patch("admin.access_routes.resolve_derived_group_tags", return_value=frozenset()),
        patch("admin.access_routes.fs.get_document", return_value=None),
    ):
        resp = admin_client.post("/api/admin/access/check", json={"email": "alice@one.com", "toolName": "search"})
    assert resp.status_code == 200
    tp = resp.json()["tool_permission"]
    assert tp["allowed"] is False
    assert "deny" in tp["reason"]


def test_access_check_unknown_user_degrades(admin_client: TestClient) -> None:
    fb = MagicMock()
    fb.get_user_by_email.side_effect = ValueError("no such user")
    with (
        patch("admin.access_routes._fb_auth", return_value=fb),
        patch("admin.access_routes.resolve_derived_group_tags", return_value=frozenset({"DERIVED"})),
    ):
        resp = admin_client.post("/api/admin/access/check", json={"email": "ghost@one.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_found"] is False
    assert body["uid"] == ""
    # domain-derived still resolves for an address that hasn't signed in.
    by_tag = {t["tag"]: t["provenances"] for t in body["tags"]}
    assert by_tag["DERIVED"] == ["domain-derived"]
