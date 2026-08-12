"""Every /api/admin route is scope-gated — enforced, not documented (M5).

This is the mechanism that keeps the tenant boundary true *as routes are
added*. v6.9.0 shipped a correct tenant-admin role with exactly one call site
because nothing forced the other twenty routes to use it. A checklist in a
design doc does not survive six months of feature work; a failing test does.

The suite has two halves:

  1. **Static coverage** — enumerate every route on every admin router by
     introspecting ``router.routes``, and assert each one either depends on the
     shared scope dependency or is on a short, *reasoned* exemption list. A new
     unscoped admin route fails here on the day it is written, without anyone
     remembering to add a test for it.

  2. **Behavioural matrix** — for the routes that take a domain-ish key, drive a
     real request as ``tenant-admin:a.com`` against ``b.com`` data and assert a
     403 / empty result. Static coverage proves the dependency is wired;
     this proves it is actually consulted.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from admin.scope import PlatformScope, Scope, require_admin_scope, require_platform_scope
from auth import User, get_current_user

# ---------------------------------------------------------------------------
# Route inventory
# ---------------------------------------------------------------------------


def _admin_routers():
    """Every router that serves an /api/admin path, as (name, router).

    **Discovered, not listed.** An earlier version hardcoded the imports, which
    meant this sweep only caught a new unscoped *route on an existing router* —
    a whole new router file (as Phase 4's audit reader was) sailed straight past
    it while the suite stayed green. That is the precise failure mode this test
    exists to prevent, so the inventory now walks the ``admin`` package and picks
    up every APIRouter attribute it finds.
    """
    import importlib
    import pkgutil

    import admin as admin_pkg

    found: list[tuple[str, APIRouter]] = []
    seen: set[int] = set()
    for mod_info in pkgutil.iter_modules(admin_pkg.__path__):
        module = importlib.import_module(f"admin.{mod_info.name}")
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, APIRouter) and id(obj) not in seen:
                seen.add(id(obj))
                found.append((f"{mod_info.name}.{attr}", obj))
    return sorted(found, key=lambda pair: pair[0])


# Routes that legitimately do NOT take the shared scope dependency. Each entry
# must carry a reason — an exemption without one is how this test rots into a
# rubber stamp.
_EXEMPT: dict[str, str] = {
    "GET /api/admin/whoami": (
        "The role probe itself. Must answer for any authenticated user (200 "
        "scope:'none') so the UI can distinguish 'not an admin' from 'broken'."
    ),
    "POST /api/admin/seed-platform-skills": (
        "Service-account auth (_assert_caller_is_service_account), called by the "
        "Cloud Build deploy hook — there is no Firebase user to scope."
    ),
    "POST /api/admin/documents/prewarm-from-blocks": (
        "Service-account auth, ops runbook path; no per-tenant data returned."
    ),
    "POST /api/admin/documents/prewarm-from-blocks/precheck": (
        "Service-account auth, ops runbook path; no per-tenant data returned."
    ),
    # These two read their domain from the request BODY, not the path, so a
    # route-level dependency cannot know it in time. They call the same
    # resolve_admin_scope via admin/tenants.py::_require_tenant_admin, and the
    # behavioural probes below still exercise the boundary.
    "POST /api/admin/tenants": "Domain comes from the body; enforced via _require_tenant_admin -> resolve_admin_scope.",
    "GET /api/admin/tenants/{domain}/validate": (
        "Enforced via _require_tenant_admin -> resolve_admin_scope; covered behaviourally below."
    ),
}

_SCOPE_DEPS = {require_admin_scope, require_platform_scope}


def _route_key(route: APIRoute) -> str:
    method = sorted(route.methods - {"HEAD", "OPTIONS"})[0] if route.methods else "?"
    return f"{method} {route.path}"


def _depends_on_scope(route: APIRoute) -> bool:
    """True iff the route's dependency tree includes a scope dependency."""
    seen, stack = set(), list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        if dep.call in _SCOPE_DEPS:
            return True
        stack.extend(dep.dependencies)
    return False


def _all_admin_routes():
    out = []
    for name, router in _admin_routers():
        for route in router.routes:
            if isinstance(route, APIRoute) and route.path.startswith("/api/admin"):
                out.append((name, route))
    return out


# ---------------------------------------------------------------------------
# 1. Static coverage
# ---------------------------------------------------------------------------


class TestEveryAdminRouteIsScopeGated:
    def test_inventory_is_not_empty(self):
        """Guard against the introspection silently finding nothing — which
        would make every other assertion here vacuously true."""
        assert len(_all_admin_routes()) >= 15

    @pytest.mark.parametrize("name,route", _all_admin_routes(), ids=lambda v: getattr(v, "path", v))
    def test_route_is_scoped_or_reasoned_exempt(self, name, route):
        key = _route_key(route)
        if key in _EXEMPT:
            assert _EXEMPT[key].strip(), f"{key} is exempt with no reason"
            return
        assert _depends_on_scope(route), (
            f"{key} ({name}) does not depend on AdminScope.\n"
            "Every /api/admin route must take `Scope` or `PlatformScope`. If it "
            "genuinely cannot, add it to _EXEMPT in this file WITH a reason."
        )

    def test_exemptions_all_reference_real_routes(self):
        """A stale exemption is worse than none — it silently excuses a route
        that no longer exists while looking like coverage."""
        live = {_route_key(r) for _, r in _all_admin_routes()}
        stale = set(_EXEMPT) - live
        assert not stale, f"Exemptions for routes that no longer exist: {sorted(stale)}"


# ---------------------------------------------------------------------------
# 2. Behavioural matrix
# ---------------------------------------------------------------------------

_TENANT_A = User(
    uid="ta-uid",
    email="ops@a.com",
    domain="a.com",
    group_tags=frozenset({"tenant-admin:a.com"}),
)


def _client_for(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        return _TENANT_A

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


def _b_com_probes():
    """(label, router-import, method, path, json) tuples reaching for b.com."""
    from admin.access_routes import router as access
    from admin.clients import router as clients
    from admin.tenants import router as tenants
    from admin.tool_permissions_routes import router as tool_permissions
    from admin.users_routes import router as users

    return [
        ("clients.get", clients, "GET", "/api/admin/clients/b.com", None),
        ("clients.put", clients, "PUT", "/api/admin/clients/b.com", {"display_name": "x"}),
        ("clients.delete", clients, "DELETE", "/api/admin/clients/b.com", None),
        ("users.get", users, "GET", "/api/admin/users/someone@b.com", None),
        ("users.grant", users, "POST", "/api/admin/users/someone@b.com/groups", {"tag": "ONE"}),
        ("users.revoke", users, "DELETE", "/api/admin/users/someone@b.com/groups/ONE", None),
        ("users.refresh", users, "POST", "/api/admin/users/someone@b.com/refresh-claims", None),
        ("toolperms.get", tool_permissions, "GET", "/api/admin/tool-permissions/b.com", None),
        (
            "toolperms.put",
            tool_permissions,
            "PUT",
            "/api/admin/tool-permissions/b.com",
            {"type": "domain", "tools": ["*"], "denied": []},
        ),
        ("toolperms.delete", tool_permissions, "DELETE", "/api/admin/tool-permissions/b.com", None),
        ("toolperms.wildcard", tool_permissions, "GET", "/api/admin/tool-permissions/*", None),
        ("access.check", access, "POST", "/api/admin/access/check", {"email": "someone@b.com"}),
        # Body-domain routes: exempt from the STATIC sweep (the dependency can't
        # see a body param), so they must be covered behaviourally or the
        # exemption becomes a blind spot.
        ("tenants.onboard", tenants, "POST", "/api/admin/tenants", {"domain": "b.com", "display_name": "B"}),
        ("tenants.validate", tenants, "GET", "/api/admin/tenants/b.com/validate", None),
    ]


class TestCrossTenantMatrix:
    @pytest.mark.parametrize(
        "label,router,method,path,body", _b_com_probes(), ids=lambda v: v if isinstance(v, str) else ""
    )
    def test_tenant_a_cannot_reach_b(self, label, router, method, path, body):
        """Every domain-keyed admin route denies a reach into another tenant.

        Firestore is patched to RETURN DATA — so a route that skipped its scope
        check would return 200 with b.com's content rather than a 404 that
        accidentally looks like a pass.
        """
        with (
            patch("db.firestore.get_document", return_value={"display_name": "Beta"}),
            patch("admin.clients.get_document", return_value={"display_name": "Beta"}),
            patch(
                "admin.tool_permissions_routes.get_document",
                return_value={"type": "domain", "tools": ["*"], "denied": []},
            ),
        ):
            r = _client_for(router).request(method, path, json=body)
        assert r.status_code == 403, f"{label} returned {r.status_code}, expected 403 (cross-tenant reach)"


# Probe routers for the self-test below. Defined at MODULE level on purpose:
# this file uses `from __future__ import annotations`, so FastAPI resolves a
# route's annotations against module globals. A router built inside a test
# function has `Scope` as a local, which FastAPI cannot resolve — the detector
# would then report a genuinely-scoped route as unscoped. (That is not
# hypothetical: the first version of these probes was function-local and failed
# exactly this way, which is why the self-test exists at all.)

_probe = APIRouter(prefix="/api/admin/_probe")


@_probe.get("/rogue")
def _probe_rogue() -> dict:  # deliberately unscoped
    return {"everything": "for everyone"}


@_probe.get("/scoped")
def _probe_scoped(scope: Scope) -> dict:
    return {"ok": True}


@_probe.get("/platform")
def _probe_platform(scope: PlatformScope) -> dict:
    return {"ok": True}


def _probe_route(name: str) -> APIRoute:
    return next(r for r in _probe.routes if isinstance(r, APIRoute) and r.path.endswith(name))


class TestMatrixHasTeeth:
    """The matrix is only worth having if it actually fails on an unscoped route.

    Asserts the failure mode directly rather than trusting it — the sprint plan
    calls for verifying this, and a coverage test that cannot fail is worse than
    no test, because it reads as proof.
    """

    def test_an_unscoped_route_is_detected(self):
        assert not _depends_on_scope(_probe_route("/rogue")), "an unscoped route must be reported as unscoped"

    def test_a_scoped_route_passes(self):
        assert _depends_on_scope(_probe_route("/scoped"))

    def test_platform_scope_also_counts_as_gated(self):
        assert _depends_on_scope(_probe_route("/platform")), (
            "PlatformScope nests require_admin_scope and must be detected"
        )

    def test_probe_routes_are_not_in_the_real_inventory(self):
        """The probes must not pollute the coverage sweep they validate."""
        paths = {r.path for _, r in _all_admin_routes()}
        assert not any(p.startswith("/api/admin/_probe") for p in paths)
