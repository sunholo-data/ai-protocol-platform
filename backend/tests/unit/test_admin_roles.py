"""Tests for the unified admin-role model (auth/admin_roles.py, v6.9.0 / 9.1)."""

from __future__ import annotations

from auth import admin_roles


class TestPlatformAdmin:
    def test_aitana_admin_is_platform_admin(self):
        assert admin_roles.is_platform_admin(["aitana-admin"]) is True

    def test_other_tags_are_not_platform_admin(self):
        assert admin_roles.is_platform_admin(["one-admin", "ONE"]) is False

    def test_empty_and_none(self):
        assert admin_roles.is_platform_admin(frozenset()) is False
        assert admin_roles.is_platform_admin(None) is False


class TestTenantAdmin:
    def test_tenant_admin_of_own_domain(self):
        assert admin_roles.is_tenant_admin(["tenant-admin:acmeenergy.com"], "acmeenergy.com") is True

    def test_not_tenant_admin_of_other_domain(self):
        assert admin_roles.is_tenant_admin(["tenant-admin:acmeenergy.com"], "rockwool.com") is False

    def test_platform_admin_administers_every_domain(self):
        assert admin_roles.is_tenant_admin(["aitana-admin"], "anything.com") is True

    def test_blank_domain_denied_for_tenant_admin(self):
        assert admin_roles.is_tenant_admin(["tenant-admin:x.com"], "") is False

    def test_tenant_admin_is_not_platform_admin(self):
        assert admin_roles.is_platform_admin(["tenant-admin:a.com"]) is False

    def test_domains_parsing_ignores_bare_prefix(self):
        tags = ["tenant-admin:a.com", "tenant-admin:b.com", "ONE", "tenant-admin:"]
        assert admin_roles.tenant_admin_domains(tags) == frozenset({"a.com", "b.com"})


class TestSkillAdmin:
    def test_one_admin_is_skill_admin(self):
        assert admin_roles.is_skill_admin(["one-admin"]) is True

    def test_platform_admin_is_skill_admin(self):
        assert admin_roles.is_skill_admin(["aitana-admin"]) is True

    def test_tenant_admin_is_not_skill_admin(self):
        assert admin_roles.is_skill_admin(["tenant-admin:a.com"]) is False

    def test_plain_user_is_not_skill_admin(self):
        assert admin_roles.is_skill_admin(["ONE"]) is False
