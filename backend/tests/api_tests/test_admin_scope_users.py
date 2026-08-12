"""Tenant scoping + privilege-escalation guards on user/tag admin (M3).

Tag grants are the one admin operation that can change *who is an admin*.
Every other boundary in this sprint is decided by group tags, so if a tenant
admin can mint one, the whole scope model is decorative. These tests are the
proof that they cannot.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.admin_roles import is_admin_conferring_tag

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


def _users_app(user: User) -> TestClient:
    from admin.users_routes import router

    return _client(router, user)


class TestAdminConferringTags:
    @pytest.mark.parametrize(
        "tag",
        ["aitana-admin", "one-admin", "tenant-admin:a.com", "tenant-admin:evil.com"],
    )
    def test_conferring_tags_identified(self, tag):
        assert is_admin_conferring_tag(tag) is True

    @pytest.mark.parametrize("tag", ["ONE", "beta-tester", "", "  ", "tenant-admin"])
    def test_ordinary_tags_are_not_conferring(self, tag):
        # bare "tenant-admin" (no colon) is not the claim shape, so not conferring
        assert is_admin_conferring_tag(tag) is False


class TestUserScoping:
    def test_tenant_cannot_read_user_in_other_domain(self):
        r = _users_app(_TENANT_A).get("/api/admin/users/someone@b.com")
        assert r.status_code == 403

    def test_tenant_cannot_refresh_claims_for_other_domain(self):
        r = _users_app(_TENANT_A).post("/api/admin/users/someone@b.com/refresh-claims")
        assert r.status_code == 403

    def test_malformed_email_is_denied_for_tenant(self):
        """No '@' → blank domain → must not satisfy a tenant scope."""
        r = _users_app(_TENANT_A).get("/api/admin/users/not-an-email")
        assert r.status_code == 403


class TestPrivilegeEscalation:
    """The attacks a tenant admin would actually try."""

    def test_cannot_grant_platform_admin_to_self(self):
        r = _users_app(_TENANT_A).post(
            "/api/admin/users/ops@a.com/groups",
            json={"tag": "aitana-admin"},
        )
        assert r.status_code == 403

    def test_cannot_grant_tenant_admin_for_another_domain(self):
        r = _users_app(_TENANT_A).post(
            "/api/admin/users/ops@a.com/groups",
            json={"tag": "tenant-admin:b.com"},
        )
        assert r.status_code == 403

    def test_cannot_grant_tenant_admin_even_for_own_domain(self):
        """Appointing co-admins is a platform action in Phase 1 (design doc
        open question #2). Conservative default: deny, easy to relax later."""
        r = _users_app(_TENANT_A).post(
            "/api/admin/users/colleague@a.com/groups",
            json={"tag": "tenant-admin:a.com"},
        )
        assert r.status_code == 403

    def test_cannot_grant_skill_admin(self):
        r = _users_app(_TENANT_A).post(
            "/api/admin/users/ops@a.com/groups",
            json={"tag": "one-admin"},
        )
        assert r.status_code == 403

    def test_cannot_revoke_admin_tag_from_another_admin(self):
        """Revoke is an authority change too — a tenant admin stripping a
        platform admin's tag would be a denial-of-service on the platform."""
        r = _users_app(_TENANT_A).delete("/api/admin/users/ops@a.com/groups/aitana-admin")
        assert r.status_code == 403

    def test_escalation_is_blocked_before_any_firebase_write(self):
        """The guard must run before _lookup/_write, not after — a partial
        write followed by a 403 would still have granted the tag."""
        with patch("admin.users_routes._fb_auth") as fb:
            r = _users_app(_TENANT_A).post(
                "/api/admin/users/ops@a.com/groups",
                json={"tag": "aitana-admin"},
            )
        assert r.status_code == 403
        fb.assert_not_called()

    def test_platform_admin_can_still_grant_admin_tags(self):
        """The guard must not lock the platform admin out of its own job."""
        rec = SimpleNamespace(uid="u1", custom_claims={"groupTags": []})
        fb = MagicMock()
        fb.get_user_by_email.return_value = rec
        with (
            patch("admin.users_routes._fb_auth", return_value=fb),
            patch("admin.users_routes.is_known_tag", return_value=True),
            patch("admin.users_routes.record_admin_action"),
        ):
            r = _users_app(_PLATFORM).post(
                "/api/admin/users/someone@b.com/groups",
                json={"tag": "aitana-admin"},
            )
        assert r.status_code == 200
        assert "aitana-admin" in r.json()["group_tags"]

    def test_tenant_admin_can_grant_an_ordinary_tag_in_own_domain(self):
        rec = SimpleNamespace(uid="u2", custom_claims={"groupTags": []})
        fb = MagicMock()
        fb.get_user_by_email.return_value = rec
        with (
            patch("admin.users_routes._fb_auth", return_value=fb),
            patch("admin.users_routes.is_known_tag", return_value=True),
            patch("admin.users_routes.record_admin_action"),
        ):
            r = _users_app(_TENANT_A).post(
                "/api/admin/users/colleague@a.com/groups",
                json={"tag": "beta-tester"},
            )
        assert r.status_code == 200


class TestTagMembersScoping:
    def _members_app(self, user: User) -> TestClient:
        from admin.group_tags_routes import members_router

        return _client(members_router, user)

    def _fb_with_users(self, *emails):
        recs = [
            SimpleNamespace(uid=f"u{i}", email=e, custom_claims={"groupTags": ["ONE"]}) for i, e in enumerate(emails)
        ]
        fb = MagicMock()
        fb.list_users.return_value = SimpleNamespace(iterate_all=lambda: iter(recs))
        return fb

    def test_tenant_sees_only_own_domain_holders(self):
        """Unscoped, this endpoint is a directory of every tenant's users."""
        fb = self._fb_with_users("a1@a.com", "b1@b.com", "a2@a.com")
        with patch("admin.group_tags_routes._fb_auth", return_value=fb):
            r = self._members_app(_TENANT_A).get("/api/admin/groups/ONE/members")
        assert r.status_code == 200
        assert [m["email"] for m in r.json()["members"]] == ["a1@a.com", "a2@a.com"]

    def test_platform_sees_all_holders(self):
        fb = self._fb_with_users("a1@a.com", "b1@b.com")
        with patch("admin.group_tags_routes._fb_auth", return_value=fb):
            r = self._members_app(_PLATFORM).get("/api/admin/groups/ONE/members")
        assert {m["email"] for m in r.json()["members"]} == {"a1@a.com", "b1@b.com"}

    def test_scanned_count_stays_truthful_under_filtering(self):
        """`scanned` drives the truncation warning; reporting the filtered
        count instead would make that warning wrong."""
        fb = self._fb_with_users("a1@a.com", "b1@b.com", "c1@c.com")
        with patch("admin.group_tags_routes._fb_auth", return_value=fb):
            r = self._members_app(_TENANT_A).get("/api/admin/groups/ONE/members")
        body = r.json()
        assert body["scanned"] == 3
        assert len(body["members"]) == 1
