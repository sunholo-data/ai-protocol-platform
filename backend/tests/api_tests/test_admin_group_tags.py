"""API tests for /api/admin/group-tags + /api/admin/groups/{tag}/members.

Firestore + the Firebase Admin SDK are mocked. Exercises:
  - admin guard (aitana-admin) on every route
  - registry list / upsert (audited)
  - grant-validation helper is_known_tag (structural / bootstrap / registry)
  - tag-holders reverse lookup filters by the groupTags claim
  - members scan reports truncation instead of silently dropping holders
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
    from admin.group_tags_routes import members_router, router

    app = FastAPI()
    app.include_router(router)
    app.include_router(members_router)
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


# --- registry list --------------------------------------------------------


def test_list_group_tags(admin_client: TestClient) -> None:
    docs = [
        {"__id": "ONE", "label": "Acme Energy", "grants": ["one-ppa-expert"]},
        {"__id": "aitana-admin", "label": "Platform admin"},
    ]
    with patch("admin.group_tags_routes.query_documents", return_value=docs):
        resp = admin_client.get("/api/admin/group-tags")
    assert resp.status_code == 200
    body = resp.json()
    assert {t["id"] for t in body} == {"ONE", "aitana-admin"}
    one = next(t for t in body if t["id"] == "ONE")
    assert one["label"] == "Acme Energy"
    assert one["grants"] == ["one-ppa-expert"]


def test_list_group_tags_non_admin_403(nonadmin_client: TestClient) -> None:
    with patch("admin.group_tags_routes.query_documents", return_value=[]):
        resp = nonadmin_client.get("/api/admin/group-tags")
    assert resp.status_code == 403


# --- registry upsert ------------------------------------------------------


def test_upsert_group_tag_creates_and_audits(admin_client: TestClient) -> None:
    with (
        patch("admin.group_tags_routes.get_document", return_value=None),
        patch("admin.group_tags_routes.set_document") as mock_set,
        patch("admin.group_tags_routes.record_admin_action") as mock_audit,
    ):
        resp = admin_client.put(
            "/api/admin/group-tags/ONE",
            json={"label": "Acme Energy", "description": "ONE tenant", "grants": ["one-ppa-expert"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "ONE"
    assert body["label"] == "Acme Energy"
    # created_by stamped from the admin uid on first create.
    assert body["createdBy"] == "admin-uid"
    mock_set.assert_called_once()
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["action"] == "upsert_group_tag"
    assert mock_audit.call_args.kwargs["target"] == "ONE"


def test_upsert_group_tag_preserves_created_by_on_update(admin_client: TestClient) -> None:
    existing = {"label": "old", "created_by": "someone-else", "created_at": 123.0}
    with (
        patch("admin.group_tags_routes.get_document", return_value=existing),
        patch("admin.group_tags_routes.set_document"),
        patch("admin.group_tags_routes.record_admin_action"),
    ):
        resp = admin_client.put("/api/admin/group-tags/ONE", json={"label": "new"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdBy"] == "someone-else"
    assert body["createdAt"] == 123.0


def test_upsert_group_tag_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.put("/api/admin/group-tags/ONE", json={"label": "x"})
    assert resp.status_code == 403


# --- is_known_tag helper --------------------------------------------------


def test_is_known_tag_structural_always_true() -> None:
    from admin.group_tags_routes import is_known_tag

    assert is_known_tag("aitana-admin") is True
    assert is_known_tag("tenant-admin:acme.com") is True


def test_is_known_tag_bootstrap_when_registry_empty() -> None:
    from admin.group_tags_routes import is_known_tag

    with patch("admin.group_tags_routes.query_documents", return_value=[]):
        assert is_known_tag("anything") is True


def test_is_known_tag_rejects_unknown_when_registry_populated() -> None:
    from admin.group_tags_routes import is_known_tag

    with patch("admin.group_tags_routes.query_documents", return_value=[{"__id": "ONE"}]):
        assert is_known_tag("ONE") is True
        assert is_known_tag("TYPO") is False


# --- tag-holders reverse lookup -------------------------------------------


def _fake_fb_users(users):
    """users: list of (email, uid, tags). Returns a fake firebase auth."""
    recs = []
    for email, uid, tags in users:
        r = MagicMock()
        r.email = email
        r.uid = uid
        r.custom_claims = {"groupTags": list(tags)} if tags else {}
        recs.append(r)
    page = MagicMock()
    page.iterate_all.return_value = iter(recs)
    fb = MagicMock()
    fb.list_users.return_value = page
    return fb


def test_list_tag_members_filters_by_claim(admin_client: TestClient) -> None:
    fb = _fake_fb_users(
        [
            ("alice@one.com", "u1", ["ONE"]),
            ("bob@x.com", "u2", ["OTHER"]),
            ("carol@one.com", "u3", ["ONE", "aitana-admin"]),
        ]
    )
    with patch("admin.group_tags_routes._fb_auth", return_value=fb):
        resp = admin_client.get("/api/admin/groups/ONE/members")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "ONE"
    assert {m["email"] for m in body["members"]} == {"alice@one.com", "carol@one.com"}
    assert body["scanned"] == 3
    assert body["truncated"] is False


def test_list_tag_members_truncation_reported(admin_client: TestClient) -> None:
    fb = _fake_fb_users([("a@one.com", "u1", ["ONE"]), ("b@one.com", "u2", ["ONE"])])
    with (
        patch("admin.group_tags_routes._MEMBERS_SCAN_CAP", 1),
        patch("admin.group_tags_routes._fb_auth", return_value=fb),
    ):
        resp = admin_client.get("/api/admin/groups/ONE/members")
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert "TRUNCATED" in body["note"]


def test_list_tag_members_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.get("/api/admin/groups/ONE/members")
    assert resp.status_code == 403
