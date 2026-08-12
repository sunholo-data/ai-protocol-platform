"""Tenant scoping on the domain-keyed admin routes (v6.16.0 / ADMIN-SCOPE M2).

Covers clients, tool-permissions and access-check. The generic
"no route leaks across tenants" sweep lives in the M5 matrix test; this file
pins the per-route *semantics* the matrix can't express — filter-vs-403, the
wildcard rule, and scope-before-existence ordering.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_PLATFORM = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_TENANT_A = User(
    uid="ta-uid",
    email="ops@a.com",
    domain="a.com",
    group_tags=frozenset({"tenant-admin:a.com"}),
)


def _client(router, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        return user

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------


_CLIENT_DOCS = [
    {"__id": "a.com", "display_name": "Alpha"},
    {"__id": "b.com", "display_name": "Beta"},
]


class TestClientsScoping:
    def _app(self, user):
        from admin.clients import router

        return _client(router, user)

    def test_platform_lists_every_tenant(self):
        with patch("admin.clients.query_documents", return_value=[dict(d) for d in _CLIENT_DOCS]):
            r = self._app(_PLATFORM).get("/api/admin/clients")
        assert r.status_code == 200
        assert {c["domain"] for c in r.json()} == {"a.com", "b.com"}

    def test_tenant_list_is_filtered_not_forbidden(self):
        """A tenant admin listing tenants gets their own back, not a 403 —
        an error here would be a dead-end for a legitimate action."""
        with patch("admin.clients.query_documents", return_value=[dict(d) for d in _CLIENT_DOCS]):
            r = self._app(_TENANT_A).get("/api/admin/clients")
        assert r.status_code == 200
        assert [c["domain"] for c in r.json()] == ["a.com"]

    def test_tenant_can_read_own(self):
        with patch("admin.clients.get_document", return_value={"display_name": "Alpha"}):
            r = self._app(_TENANT_A).get("/api/admin/clients/a.com")
        assert r.status_code == 200

    def test_tenant_cannot_read_other(self):
        with patch("admin.clients.get_document", return_value={"display_name": "Beta"}):
            r = self._app(_TENANT_A).get("/api/admin/clients/b.com")
        assert r.status_code == 403

    def test_scope_checked_before_existence(self):
        """An out-of-scope domain must 403 even when absent — a 404 would
        confirm which tenants do and don't exist."""
        with patch("admin.clients.get_document", return_value=None) as gd:
            r = self._app(_TENANT_A).get("/api/admin/clients/nonexistent-b.com")
        assert r.status_code == 403
        gd.assert_not_called()  # we never even looked

    def test_tenant_cannot_write_other(self):
        r = self._app(_TENANT_A).put("/api/admin/clients/b.com", json={"display_name": "pwned"})
        assert r.status_code == 403

    def test_tenant_cannot_delete_other(self):
        with patch("admin.clients.get_document", return_value={"display_name": "Beta"}):
            r = self._app(_TENANT_A).delete("/api/admin/clients/b.com")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# tool-permissions
# ---------------------------------------------------------------------------


_PERM_DOCS = [
    {"__id": "user@a.com", "type": "user", "tools": ["x"], "denied": []},
    {"__id": "b.com", "type": "domain", "tools": ["y"], "denied": []},
    {"__id": "*", "type": "wildcard", "tools": ["z"], "denied": []},
]


class TestToolPermissionScoping:
    def _app(self, user):
        from admin.tool_permissions_routes import router

        return _client(router, user)

    def test_doc_domain_parsing(self):
        from admin.tool_permissions_routes import _doc_domain

        assert _doc_domain("user@a.com") == "a.com"
        assert _doc_domain("a.com") == "a.com"
        assert _doc_domain("USER@A.COM") == "a.com"
        # The wildcard belongs to no single tenant.
        assert _doc_domain("*") == ""
        assert _doc_domain("") == ""

    def test_tenant_list_excludes_wildcard_and_other_domains(self):
        with patch("admin.tool_permissions_routes.query_documents", return_value=[dict(d) for d in _PERM_DOCS]):
            r = self._app(_TENANT_A).get("/api/admin/tool-permissions")
        assert r.status_code == 200
        assert [e["doc_id"] for e in r.json()] == ["user@a.com"]

    def test_platform_list_includes_wildcard(self):
        with patch("admin.tool_permissions_routes.query_documents", return_value=[dict(d) for d in _PERM_DOCS]):
            r = self._app(_PLATFORM).get("/api/admin/tool-permissions")
        assert {e["doc_id"] for e in r.json()} == {"user@a.com", "b.com", "*"}

    def test_tenant_cannot_edit_wildcard(self):
        """The wildcard doc changes every tenant's permissions at once, so a
        tenant admin editing it would reach other tenants without naming one."""
        r = self._app(_TENANT_A).put(
            "/api/admin/tool-permissions/*",
            json={"type": "wildcard", "tools": ["*"], "denied": []},
        )
        assert r.status_code == 403

    def test_tenant_cannot_edit_other_domains_doc(self):
        r = self._app(_TENANT_A).put(
            "/api/admin/tool-permissions/b.com",
            json={"type": "domain", "tools": ["*"], "denied": []},
        )
        assert r.status_code == 403

    def test_tenant_can_edit_own_users_doc(self):
        with (
            patch("admin.tool_permissions_routes.get_document", return_value=None),
            patch("admin.tool_permissions_routes.set_document"),
            patch("admin.tool_permissions_routes.perms.clear_cache"),
            patch("admin.tool_permissions_routes.record_admin_action"),
        ):
            r = self._app(_TENANT_A).put(
                "/api/admin/tool-permissions/user@a.com",
                json={"type": "user", "tools": ["search"], "denied": []},
            )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# access-check
# ---------------------------------------------------------------------------


class TestAccessCheckScoping:
    def _app(self, user):
        from admin.access_routes import router

        return _client(router, user)

    def test_tenant_cannot_inspect_other_tenants_user(self):
        """access/check reports someone else's tags and skill visibility, so an
        unscoped version would enumerate other tenants' users."""
        r = self._app(_TENANT_A).post("/api/admin/access/check", json={"email": "someone@b.com"})
        assert r.status_code == 403

    def test_tenant_can_inspect_own_user(self):
        with (
            patch("admin.access_routes._direct_tags", return_value=("u1", ["ONE"], True)),
            patch("admin.access_routes.resolve_derived_group_tags", return_value=[]),
        ):
            r = self._app(_TENANT_A).post("/api/admin/access/check", json={"email": "someone@a.com"})
        assert r.status_code == 200
        assert r.json()["domain"] == "a.com"
