"""API tests for /api/admin/tool-permissions/{docId} — CRUD over the second
access plane. Firestore + the perm cache are mocked. Exercises:
  - admin guard (aitana-admin)
  - list / get / upsert / delete happy paths + 404s
  - type validation (422 on a bad type)
  - permissions.clear_cache() flushed on every write (stale-allow hazard)
  - audit row per mutation
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_ADMIN = User(
    uid="admin-uid", email="owner@yourcompany.com", domain="yourcompany.com", group_tags=frozenset({"aitana-admin"})
)
_NON_ADMIN = User(uid="user-uid", email="user@example.com", domain="example.com", group_tags=frozenset())


def _make_app() -> FastAPI:
    from admin.tool_permissions_routes import router

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


def test_list_tool_permissions(admin_client: TestClient) -> None:
    docs = [
        {"__id": "alice@one.com", "type": "user", "tools": ["search"]},
        {"__id": "*", "type": "wildcard", "tools": ["*"]},
    ]
    with patch("admin.tool_permissions_routes.query_documents", return_value=docs):
        resp = admin_client.get("/api/admin/tool-permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert {d["doc_id"] for d in body} == {"alice@one.com", "*"}


def test_get_tool_permission_404(admin_client: TestClient) -> None:
    with patch("admin.tool_permissions_routes.get_document", return_value=None):
        resp = admin_client.get("/api/admin/tool-permissions/nobody@x.com")
    assert resp.status_code == 404


def test_get_tool_permission_ok(admin_client: TestClient) -> None:
    with patch("admin.tool_permissions_routes.get_document", return_value={"type": "user", "tools": ["search"]}):
        resp = admin_client.get("/api/admin/tool-permissions/alice@one.com")
    assert resp.status_code == 200
    assert resp.json()["doc_id"] == "alice@one.com"
    assert resp.json()["tools"] == ["search"]


def test_upsert_tool_permission_flushes_cache_and_audits(admin_client: TestClient) -> None:
    with (
        patch("admin.tool_permissions_routes.get_document", return_value=None),
        patch("admin.tool_permissions_routes.set_document") as mock_set,
        patch("admin.tool_permissions_routes.perms.clear_cache") as mock_clear,
        patch("admin.tool_permissions_routes.record_admin_action") as mock_audit,
    ):
        resp = admin_client.put(
            "/api/admin/tool-permissions/alice@one.com",
            json={"type": "user", "tools": ["search"], "denied": ["code_exec"]},
        )
    assert resp.status_code == 200
    mock_set.assert_called_once()
    mock_clear.assert_called_once()
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "upsert_tool_permission"


def test_upsert_tool_permission_bad_type_422(admin_client: TestClient) -> None:
    resp = admin_client.put("/api/admin/tool-permissions/alice@one.com", json={"type": "bogus", "tools": []})
    assert resp.status_code == 422


def test_delete_tool_permission_flushes_cache_and_audits(admin_client: TestClient) -> None:
    with (
        patch("admin.tool_permissions_routes.get_document", return_value={"type": "user", "tools": ["search"]}),
        patch("admin.tool_permissions_routes.delete_document") as mock_del,
        patch("admin.tool_permissions_routes.perms.clear_cache") as mock_clear,
        patch("admin.tool_permissions_routes.record_admin_action") as mock_audit,
    ):
        resp = admin_client.delete("/api/admin/tool-permissions/alice@one.com")
    assert resp.status_code == 200
    mock_del.assert_called_once()
    mock_clear.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "delete_tool_permission"


def test_delete_tool_permission_404(admin_client: TestClient) -> None:
    with patch("admin.tool_permissions_routes.get_document", return_value=None):
        resp = admin_client.delete("/api/admin/tool-permissions/nobody@x.com")
    assert resp.status_code == 404


def test_tool_permissions_non_admin_403(nonadmin_client: TestClient) -> None:
    with patch("admin.tool_permissions_routes.query_documents", return_value=[]):
        resp = nonadmin_client.get("/api/admin/tool-permissions")
    assert resp.status_code == 403
