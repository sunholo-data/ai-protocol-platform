"""ADK-native routes must never be reachable without an admin token.

**This is a regression test for a live exposure, not a hypothetical.** Measured
2026-08-07 against deployed dev, test and the public test.yourcompany.com domain:

    GET /api/proxy/api/skills                                     -> 401  (correct)
    GET /api/proxy/apps/aitana_platform/users/{uid}/sessions      -> 200  (!!)

`get_fast_api_app(web=True, …)` mounts ADK's own routes and none of them carry
our `Depends(get_current_user)`. The Next proxy is a deliberate catch-all that
forwards any path and relies entirely on the backend to authenticate, so those
routes were served to the public internet with no token. Behind them:

  - a session's full event list — every message, tool call and extracted clause
    (ONE's contract content: exactly what CLAUDE.md's security rule governs)
  - session artifacts (parsed documents)
  - DELETE on a session, PATCH on user memory
  - /run and /run_sse, which EXECUTE the agent and bill for it

A Firebase uid is not a secret (it appears in URLs, logs and admin surfaces), so
obscurity was never the boundary.

Why the guard is middleware and why this test asserts on PATHS rather than on a
handful of endpoints: the routes are registered by ADK, so we cannot decorate
them, and a `google-adk` bump that adds a route would silently reopen the hole.
The guard denies by path prefix; this test pins the prefix set AND spot-checks
real routes, so either half failing is caught.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auth import User
from fast_api_app import app

_ADMIN = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_PLAIN_USER = User(
    uid="user-uid",
    email="alex@acmeenergy.com",
    domain="acmeenergy.com",
    group_tags=frozenset({"ONE"}),
)

# Every ADK-registered route family, sampled. Paths are what the guard sees, so
# these are concrete instances of the patterns in the OpenAPI schema.
_ADK_NATIVE_PATHS = [
    "/apps/aitana_platform/users/u1/sessions",
    "/apps/aitana_platform/users/u1/sessions/s1",
    "/apps/aitana_platform/users/u1/sessions/s1/artifacts",
    "/apps/aitana_platform/users/u1/memory",
    "/apps/aitana_platform/app-info",
    "/apps/aitana_platform/eval_sets",
    "/list-apps",
    "/run",
    "/run_sse",
    "/debug/trace/session/s1",
    "/debug/trace/e1",
    "/builder/save",
    "/dev-ui",
    "/dev-ui/config",
    "/dev/build_graph/aitana_platform",
]

# Ours. None of these may be caught by the guard — `/api/debug/slow-stream` in
# particular is a deliberate near-miss for the `/debug/` prefix.
_OUR_PATHS = [
    "/health",
    "/version",
    "/api/local-mode-status",
    "/api/debug/slow-stream",
    "/internal/tasks/recompact",
    "/api/skills",
    "/api/admin/analytics/sessions",
    "/api/sessions/s1/messages",
    "/.well-known/agent.json",
    "/mcp/server-1",
    "/",
]


def _guard():
    from fast_api_app import _is_adk_native_path

    return _is_adk_native_path


@pytest.mark.parametrize("path", _ADK_NATIVE_PATHS)
def test_adk_native_paths_are_guarded(path: str) -> None:
    assert _guard()(path) is True, f"{path} would be served without auth"


@pytest.mark.parametrize("path", _OUR_PATHS)
def test_our_own_paths_are_not_guarded(path: str) -> None:
    # A false positive here breaks the product — /api/debug/slow-stream is ours
    # and must not be swallowed by the /debug/ prefix.
    assert _guard()(path) is False, f"{path} is one of ours and must not be guarded"


# ONE client for the module. `with TestClient(app)` would run the app lifespan,
# and our MCP mount's StreamableHTTPSessionManager refuses a second .run() in the
# same process — so a per-test context manager fails from the second test on.
# Middleware runs without the lifespan, which is all this file exercises.
_CLIENT = TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/apps/aitana_platform/users/u1/sessions", "/list-apps"])
def test_unauthenticated_request_is_rejected(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact request that returned 200 in production must not return 200.

    The guard calls `auth.get_current_user` directly (it is middleware, so
    FastAPI's dependency_overrides do not apply), hence the monkeypatch.
    """
    from fastapi import HTTPException

    async def _no_auth(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    monkeypatch.setattr("auth.get_current_user", _no_auth)
    res = _CLIENT.get(path)
    assert res.status_code == 401, f"{path} returned {res.status_code} without a token"


@pytest.mark.parametrize("path", ["/apps/aitana_platform/users/u1/sessions", "/list-apps"])
def test_authenticated_non_admin_is_rejected(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Being a valid user is not enough — these routes read ANY uid's content."""

    async def _plain(request):
        return _PLAIN_USER

    monkeypatch.setattr("auth.get_current_user", _plain)
    res = _CLIENT.get(path)
    assert res.status_code == 403, f"{path} returned {res.status_code} for a non-admin"


def test_admin_is_allowed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin still gets through — the `aitana-adk-testing` skill depends on it.

    Asserts only that the guard does not block (any non-401/403 means the guard
    passed the request to ADK's handler; what ADK then returns is its business).
    """

    async def _admin(request):
        return _ADMIN

    monkeypatch.setattr("auth.get_current_user", _admin)
    res = _CLIENT.get("/list-apps")
    assert res.status_code not in (401, 403), "admin was blocked from an ADK-native route"


def test_guard_fails_closed_when_auth_raises_unexpectedly(monkeypatch: pytest.MonkeyPatch) -> None:
    """An auth-layer bug must not fail OPEN on these routes."""

    async def _boom(request):
        raise RuntimeError("firebase exploded")

    monkeypatch.setattr("auth.get_current_user", _boom)
    res = _CLIENT.get("/apps/aitana_platform/users/u1/sessions")
    assert res.status_code == 401


def test_internal_task_route_rejects_unauthenticated() -> None:
    """`/internal/` escapes the ADK middleware by design — so its own gate must
    hold. Unconfigured or tokenless, the OIDC gate fails CLOSED (403). This is
    the companion proof for the `/internal/` entry in `our_prefixes` below."""
    res = _CLIENT.post("/internal/tasks/recompact", json={"session_id": "s", "user_id": "u"})
    assert res.status_code == 403, f"internal task route returned {res.status_code} without a task OIDC token"


def test_every_adk_route_in_the_live_schema_is_covered() -> None:
    """Nothing ADK registers may escape the guard.

    This is the part that survives a `google-adk` bump: it walks the ACTUAL
    route table rather than a hand-written list, so a newly-added ADK route that
    the prefix set doesn't cover fails here instead of shipping open.
    """
    # `/internal/` carries its own per-route auth (Cloud Tasks OIDC gate,
    # `internal_tasks.auth`) — classified as ours here, and PROVEN closed by
    # test_internal_task_route_rejects_unauthenticated below, so the
    # classification is checked rather than asserted.
    our_prefixes = ("/api/", "/mcp/", "/.well-known/", "/internal/")
    ours_exact = {"/", "/health", "/version", "/mcp"}
    # DELIBERATELY public and NOT guarded. These are FastAPI's own, not ADK's,
    # and they expose the API *shape* rather than any content — no session, no
    # document, no customer data. Guarding them would break OpenAPI tooling
    # (`curl /openapi.json | jq '.paths'` is the documented endpoint-discovery
    # step in CLAUDE.md). Tightening them is a separate, lower-stakes call than
    # the exposure this guard closes; listed explicitly so the decision is
    # visible rather than an accident of prefix matching.
    known_public = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    guard = _guard()

    escaped = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path in ours_exact or path in known_public or path.startswith(our_prefixes):
            continue
        if not guard(path):
            escaped.append(path)

    assert not escaped, (
        "these routes are neither ours nor guarded — if ADK added them, extend "
        f"_ADK_NATIVE_PREFIXES/_ADK_NATIVE_EXACT: {sorted(escaped)}"
    )
