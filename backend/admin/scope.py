"""Resolved admin authority for one request (v6.16.0 / ADMIN-SCOPE M1).

Every ``/api/admin/*`` route gates on **one** dependency that answers "which
tenants may this caller touch?" — not on a per-route `is_platform_admin` check.

The reason it is a shared dependency rather than a helper each route calls: the
v6.9.0 admin API shipped with tenant scoping as *nobody's* parameter.
``is_tenant_admin`` existed but had exactly one call site (``admin/tenants.py``)
while every other route used the platform-only ``_Admin``. Fixing that route by
route, as client admins hit 403s, is how you end up with twenty routes and
nineteen slightly different scoping rules. So: one primitive, adopted
everywhere, and a cross-tenant matrix test that goes red when a new admin route
forgets it.

Scope shapes
------------
``domains is None``      → PLATFORM scope (``aitana-admin``): every tenant.
``domains == {"a.com"}`` → TENANT scope: exactly those domains.
(no admin authority)     → the dependency raises 403 before an AdminScope exists,
                           so a constructed AdminScope always carries authority
                           and an empty ``domains`` set is unrepresentable.

Deny-by-default is structural: :meth:`AdminScope.assert_may` denies unless the
domain is explicitly in scope, and :meth:`AdminScope.filter_domains` returns
only what is in scope. There is no "allow if unset" branch to get wrong.

No feature flag
---------------
Tenant scoping is **unconditional**. An earlier revision gated it behind
``ADMIN_TENANT_SCOPE_ENABLED`` during the rollout; that flag is deliberately
gone:

  * A flag that is always on is dead weight, and worse, a trap — the next reader
    concludes tenant scoping is optional and writes a route that assumes
    platform-only callers.
  * Runtime env vars do **not** promote with code (docs/ops/env-config-parity.md).
    A flag on in dev and forgotten in prod is a recurring bug class here, and
    the failure mode would have been "the console silently refuses every client
    admin in prod only".
  * Authority still comes from the ``tenant-admin:{domain}`` claim, which is
    itself the real gate: nobody is a tenant admin until someone is granted the
    tag, so "released" and "in use" stay separate decisions without a flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException

from auth import User, get_current_user
from auth.admin_roles import is_platform_admin, tenant_admin_domains


def _normalise_domain(domain: str | None) -> str:
    """Lower-case/strip a domain for comparison. Blank stays blank (never matches)."""
    return (domain or "").strip().lower()


def domain_of_key(key: str | None) -> str:
    """The tenant an admin identifier belongs to, or ``""`` for none.

    Admin surfaces are keyed by several shapes and all of them need the same
    question answered before scoping:

        ``user@a.com``  → ``a.com``   (an email — a user of that tenant)
        ``a.com``       → ``a.com``   (a domain — the tenant itself)
        ``*``           → ``""``      (the wildcard tool-permission doc)
        ``some-tag``    → ``""``      (a global registry id)
        malformed/blank → ``""``

    ``""`` means **belongs to no single tenant**, which callers must treat as
    platform-only — never as "unscoped, allow". Because :meth:`AdminScope.may`
    is False for a blank domain, passing this straight into ``assert_may``
    already fails closed.

    Centralised on the third occurrence: tool-permission doc ids, user emails,
    and now audit targets all derived this independently, and three copies of a
    security predicate is how they drift.
    """
    k = (key or "").strip().lower()
    if not k or k == "*":
        return ""
    if "@" in k:
        return k.rsplit("@", 1)[-1]
    # A bare token with no dot is a registry id (a tag name), not a domain.
    return k if "." in k else ""


@dataclass(frozen=True)
class AdminScope:
    """The tenants one admin caller may read or mutate.

    Attributes:
        user: The authenticated admin.
        domains: ``None`` for platform-wide authority, else the exact set of
            domains in scope. Never empty — a caller with no authority is
            rejected by the dependency before an AdminScope is built.
    """

    user: User
    domains: frozenset[str] | None

    @property
    def is_platform(self) -> bool:
        """True for ``aitana-admin`` — unrestricted across every tenant."""
        return self.domains is None

    def may(self, domain: str) -> bool:
        """True iff this scope covers ``domain``. Blank domain is always False."""
        if self.domains is None:
            return True
        norm = _normalise_domain(domain)
        return bool(norm) and norm in self.domains

    def assert_may(self, domain: str) -> None:
        """Raise 403 unless this scope covers ``domain``.

        The message deliberately does not name the domains in scope — a tenant
        admin probing for other tenants' names should learn nothing.
        """
        if not self.may(domain):
            raise HTTPException(status_code=403, detail="Outside your tenant scope")

    def assert_platform(self) -> None:
        """Raise 403 unless this is platform scope.

        For genuinely global surfaces (the platform preamble, the wildcard
        tool-permission doc) that no single tenant may own.
        """
        if not self.is_platform:
            raise HTTPException(status_code=403, detail="Platform admin required")

    def filter_domains(self, domains: object) -> list[str]:
        """Return only the in-scope entries of ``domains`` (order preserved)."""
        items = [str(d) for d in domains] if domains else []
        if self.domains is None:
            return items
        return [d for d in items if _normalise_domain(d) in self.domains]


def resolve_admin_scope(user: User) -> AdminScope | None:
    """Resolve a user's admin authority, or ``None`` if they have none.

    Pure (no HTTP) so the whoami endpoint can report "not an admin" as a 200
    while the route dependency turns the same result into a 403.
    """
    tags = user.group_tags
    if is_platform_admin(tags):
        return AdminScope(user=user, domains=None)
    domains = frozenset(_normalise_domain(d) for d in tenant_admin_domains(tags) if _normalise_domain(d))
    if not domains:
        return None
    return AdminScope(user=user, domains=domains)


def require_admin_scope(user: Annotated[User, Depends(get_current_user)]) -> AdminScope:
    """FastAPI dependency: the caller's admin scope, or 403.

    Replaces the platform-only ``_Admin`` dependency on every ``/api/admin/*``
    route. A route that takes this and never consults it is a bug the
    cross-tenant matrix test is designed to catch.
    """
    scope = resolve_admin_scope(user)
    if scope is None:
        raise HTTPException(status_code=403, detail="aitana-admin group required")
    return scope


def require_platform_scope(scope: Annotated[AdminScope, Depends(require_admin_scope)]) -> AdminScope:
    """FastAPI dependency for platform-only routes (e.g. platform config)."""
    scope.assert_platform()
    return scope


Scope = Annotated[AdminScope, Depends(require_admin_scope)]
PlatformScope = Annotated[AdminScope, Depends(require_platform_scope)]


__all__ = [
    "AdminScope",
    "PlatformScope",
    "Scope",
    "require_admin_scope",
    "require_platform_scope",
    "resolve_admin_scope",
]
