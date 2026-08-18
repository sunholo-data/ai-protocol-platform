"""AdminScope — the shared admin authority primitive (v6.16.0 / ADMIN-SCOPE M1).

This is a security gate, so the suite is written adversarially: as well as the
happy paths it actively tries to *bypass* the scope (casing, whitespace,
subdomain lookalikes, blank domains, forged-looking tags) and asserts each
attempt is denied.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from admin.scope import AdminScope, resolve_admin_scope
from auth import User


def _user(*tags: str, email: str = "a@a.com") -> User:
    return User(uid="u1", email=email, domain=email.split("@")[-1], group_tags=frozenset(tags))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestResolution:
    def test_platform_admin_gets_unbounded_scope(self):
        scope = resolve_admin_scope(_user("aitana-admin"))
        assert scope is not None
        assert scope.is_platform
        assert scope.domains is None

    def test_single_tenant_admin(self):
        scope = resolve_admin_scope(_user("tenant-admin:acmeenergy.com"))
        assert scope is not None
        assert not scope.is_platform
        assert scope.domains == frozenset({"acmeenergy.com"})

    def test_multi_tenant_admin(self):
        scope = resolve_admin_scope(_user("tenant-admin:a.com", "tenant-admin:b.com"))
        assert scope is not None
        assert scope.domains == frozenset({"a.com", "b.com"})

    def test_no_admin_tags_resolves_none(self):
        assert resolve_admin_scope(_user()) is None
        assert resolve_admin_scope(_user("ONE", "some-group")) is None

    def test_one_admin_is_not_a_platform_admin(self):
        # one-admin is a *skill* admin tag, deliberately not platform authority.
        assert resolve_admin_scope(_user("one-admin")) is None

    def test_bare_tenant_admin_prefix_grants_nothing(self):
        # "tenant-admin:" with no domain must not yield an empty-string domain
        # that could then match a blank domain somewhere downstream.
        assert resolve_admin_scope(_user("tenant-admin:")) is None


# ---------------------------------------------------------------------------
# Enforcement — adversarial
# ---------------------------------------------------------------------------


class TestAssertMay:
    @pytest.fixture
    def tenant(self):
        return AdminScope(user=_user("tenant-admin:a.com"), domains=frozenset({"a.com"}))

    @pytest.fixture
    def platform(self):
        return AdminScope(user=_user("aitana-admin"), domains=None)

    def test_in_scope_allowed(self, tenant):
        tenant.assert_may("a.com")  # no raise

    def test_out_of_scope_denied(self, tenant):
        with pytest.raises(HTTPException) as exc:
            tenant.assert_may("b.com")
        assert exc.value.status_code == 403

    def test_denial_does_not_leak_scope_contents(self, tenant):
        """A tenant admin probing for other tenants must learn nothing."""
        with pytest.raises(HTTPException) as exc:
            tenant.assert_may("b.com")
        assert "a.com" not in str(exc.value.detail)

    def test_platform_may_touch_anything(self, platform):
        platform.assert_may("a.com")
        platform.assert_may("literally-anything.com")

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_domain_is_denied_for_tenant(self, tenant, blank):
        """A blank domain must never satisfy a tenant scope — the classic
        'unset means allow' bug."""
        assert tenant.may(blank) is False
        with pytest.raises(HTTPException):
            tenant.assert_may(blank)

    def test_case_and_whitespace_are_normalised_not_bypasses(self, tenant):
        assert tenant.may("A.COM") is True
        assert tenant.may("  a.com  ") is True

    @pytest.mark.parametrize(
        "lookalike",
        ["evil-a.com", "a.com.evil.com", "sub.a.com", "aa.com", "a.co", "xa.com"],
    )
    def test_lookalike_domains_denied(self, tenant, lookalike):
        """Scope matching is exact-set membership, never substring/suffix."""
        assert tenant.may(lookalike) is False

    def test_assert_platform_denies_tenant(self, tenant, platform):
        with pytest.raises(HTTPException) as exc:
            tenant.assert_platform()
        assert exc.value.status_code == 403
        platform.assert_platform()  # no raise


class TestFilterDomains:
    def test_tenant_filters_to_scope(self):
        scope = AdminScope(user=_user(), domains=frozenset({"a.com"}))
        assert scope.filter_domains(["a.com", "b.com", "c.com"]) == ["a.com"]

    def test_platform_passes_everything(self):
        scope = AdminScope(user=_user(), domains=None)
        assert scope.filter_domains(["a.com", "b.com"]) == ["a.com", "b.com"]

    def test_empty_and_none_are_safe(self):
        scope = AdminScope(user=_user(), domains=frozenset({"a.com"}))
        assert scope.filter_domains([]) == []
        assert scope.filter_domains(None) == []

    def test_filter_is_case_insensitive(self):
        scope = AdminScope(user=_user(), domains=frozenset({"a.com"}))
        assert scope.filter_domains(["A.COM"]) == ["A.COM"]  # kept, original casing preserved
