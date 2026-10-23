"""Tests for auth/tailnet_auth.py — identity from ``tailscale serve``.

The mode must:
- Trust ``Tailscale-User-Login`` ONLY when the proxy secret is presented
- 403 a login that isn't in TAILNET_PRINCIPALS, 401 a vouched request with no login
- Accept machine callers by sha256(token) in MACHINE_PRINCIPALS, nothing else,
  and only on their allowed routes (default: read + chat, never skill writes)
- Never fall back to the stub / Firebase verifiers when AUTH_MODE=tailnet
- Let a machine give its skills only private / own-tag access (never publish)
- Give machines an email so tool_permissions can target them
- Refuse to start misconfigured (deploy marker, weak secret, no principals)
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import auth
from auth import tailnet_auth
from auth.firebase_auth import User
from auth.local_mode_stub import STUB_TOKEN
from auth.tailnet_auth import (
    assert_safe_tailnet_mode,
    check_machine_access_control,
    get_current_user_tailnet,
    token_hash,
)

SECRET = "s" * 40
MACHINE_TOKEN = "agent-machine-token-for-tests"
SCOPED_TOKEN = "scoped-machine-token-for-tests"


class _FakeState:
    def __init__(self):
        self.access = None


class _FakeURL:
    def __init__(self, path: str):
        self.path = path


class _FakeRequest:
    def __init__(self, headers: dict[str, str], method: str = "GET", path: str = "/api/auth/whoami"):
        self.headers = Headers(headers)  # case-insensitive, like the real thing
        self.state = _FakeState()
        self.method = method
        self.url = _FakeURL(path)


@pytest.fixture(autouse=True)
def tailnet_env(monkeypatch):
    for name in ("K_SERVICE", "GAE_ENV", "KUBERNETES_SERVICE_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_MODE", "tailnet")
    monkeypatch.setenv("TAILNET_PROXY_SECRET", SECRET)
    monkeypatch.setenv(
        "TAILNET_PRINCIPALS",
        json.dumps({"Ada@Example.com": {"uid": "ada", "groupTags": ["staff", "aitana-admin"]}}),
    )
    monkeypatch.setenv(
        "MACHINE_PRINCIPALS",
        json.dumps(
            {
                token_hash(MACHINE_TOKEN): {"uid": "machine:agent", "groupTags": ["agent"]},
                token_hash(SCOPED_TOKEN): {"uid": "machine:scoped", "groupTags": [], "routes": ["GET /api/health*"]},
            }
        ),
    )
    tailnet_auth._config.cache_clear()
    yield
    tailnet_auth._config.cache_clear()


# ─── via the proxy ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_known_login_with_secret_is_that_user():
    req = _FakeRequest({"x-proxy-secret": SECRET, "tailscale-user-login": "ada@example.com"})
    user = await get_current_user_tailnet(req)
    assert user.uid == "ada"
    assert user.email == "ada@example.com"
    assert user.domain == "example.com"
    assert user.group_tags == frozenset({"staff", "aitana-admin"})
    assert user.auth_mode == "tailnet"
    assert req.state.access is not None


@pytest.mark.asyncio
async def test_login_match_is_case_insensitive():
    req = _FakeRequest({"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "ADA@example.com"})
    assert (await get_current_user_tailnet(req)).uid == "ada"


@pytest.mark.asyncio
async def test_unknown_login_is_403():
    req = _FakeRequest({"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "someone@else.com"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "TAILNET_LOGIN_NOT_PERMITTED"


@pytest.mark.asyncio
async def test_forged_login_without_secret_is_401():
    """A local process hitting the backend port directly can't claim a login."""
    req = _FakeRequest({"Tailscale-User-Login": "ada@example.com"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_secret_is_401():
    req = _FakeRequest({"X-Proxy-Secret": "x" * 40, "Tailscale-User-Login": "ada@example.com"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_secret_without_login_is_401():
    """Tagged devices (and anything not via tailscaled) carry no user login."""
    req = _FakeRequest({"X-Proxy-Secret": SECRET})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_is_ignored_when_proxy_vouches():
    """The frontend may still send the LOCAL_MODE stub bearer; identity comes from Tailscale."""
    req = _FakeRequest(
        {"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "ada@example.com", "Authorization": f"Bearer {STUB_TOKEN}"}
    )
    assert (await get_current_user_tailnet(req)).uid == "ada"


# ─── machine callers ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_machine_token_is_the_machine_user():
    req = _FakeRequest({"Authorization": f"Bearer {MACHINE_TOKEN}"})
    user = await get_current_user_tailnet(req)
    assert user.uid == "machine:agent"
    assert user.auth_mode == "machine"
    assert user.group_tags == frozenset({"agent"})


@pytest.mark.asyncio
async def test_machine_cannot_claim_a_login_without_secret():
    req = _FakeRequest({"Authorization": f"Bearer {MACHINE_TOKEN}", "Tailscale-User-Login": "ada@example.com"})
    assert (await get_current_user_tailnet(req)).uid == "machine:agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [f"Bearer {STUB_TOKEN}", "Bearer wrong", "Bearer ", "", "Token x"])
async def test_other_bearers_are_401(header):
    req = _FakeRequest({"Authorization": header} if header else {})
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/auth/whoami"),
        ("GET", "/api/tools"),
        ("GET", "/api/skills"),
        ("GET", "/api/skills/abc-123"),
        ("POST", "/api/skill/abc-123/stream"),
    ],
)
async def test_machine_default_routes_allowed(method, path):
    req = _FakeRequest({"Authorization": f"Bearer {MACHINE_TOKEN}"}, method=method, path=path)
    assert (await get_current_user_tailnet(req)).uid == "machine:agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/skills"),  # create — could publish a public skill
        ("PUT", "/api/skills/abc-123"),
        ("DELETE", "/api/skills/abc-123"),
        ("POST", "/api/skills/abc-123/fork"),
        ("GET", "/api/admin/users"),
    ],
)
async def test_machine_cannot_write_skills_or_reach_admin(method, path):
    req = _FakeRequest({"Authorization": f"Bearer {MACHINE_TOKEN}"}, method=method, path=path)
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(req)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MACHINE_ROUTE_NOT_PERMITTED"


@pytest.mark.asyncio
async def test_machine_explicit_routes_replace_defaults():
    ok = _FakeRequest({"Authorization": f"Bearer {SCOPED_TOKEN}"}, path="/api/health")
    assert (await get_current_user_tailnet(ok)).uid == "machine:scoped"
    denied = _FakeRequest({"Authorization": f"Bearer {SCOPED_TOKEN}"}, path="/api/skills")
    with pytest.raises(HTTPException) as exc:
        await get_current_user_tailnet(denied)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_route_limits_do_not_apply_to_tailnet_users():
    req = _FakeRequest(
        {"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "ada@example.com"}, method="POST", path="/api/skills"
    )
    assert (await get_current_user_tailnet(req)).uid == "ada"


# ─── dispatcher ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_never_falls_back_to_stub(monkeypatch):
    """AUTH_MODE=tailnet + LOCAL_MODE: the stub token alone must NOT authenticate."""
    monkeypatch.setenv("LOCAL_MODE", "1")
    req = _FakeRequest({"Authorization": f"Bearer {STUB_TOKEN}"})
    with pytest.raises(HTTPException) as exc:
        await auth._resolve_user(req)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_dispatcher_routes_to_tailnet():
    req = _FakeRequest({"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "ada@example.com"})
    assert (await auth._resolve_user(req)).uid == "ada"


# ─── startup guard ───────────────────────────────────────────────────────────


def test_guard_passes_when_configured():
    assert_safe_tailnet_mode()


def test_guard_is_noop_when_mode_off(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "")
    monkeypatch.setenv("TAILNET_PROXY_SECRET", "")
    assert_safe_tailnet_mode()


def test_guard_refuses_deploy_marker(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "platform-backend")
    with pytest.raises(RuntimeError, match="K_SERVICE"):
        assert_safe_tailnet_mode()


def test_guard_refuses_short_secret(monkeypatch):
    monkeypatch.setenv("TAILNET_PROXY_SECRET", "short")
    with pytest.raises(RuntimeError, match="TAILNET_PROXY_SECRET"):
        assert_safe_tailnet_mode()


def test_guard_refuses_no_principals(monkeypatch):
    monkeypatch.delenv("TAILNET_PRINCIPALS")
    monkeypatch.delenv("MACHINE_PRINCIPALS")
    with pytest.raises(RuntimeError, match="refuse everyone"):
        assert_safe_tailnet_mode()


def test_guard_refuses_bad_json(monkeypatch):
    monkeypatch.setenv("TAILNET_PRINCIPALS", "{not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        assert_safe_tailnet_mode()


def test_guard_refuses_principal_without_uid(monkeypatch):
    monkeypatch.setenv("TAILNET_PRINCIPALS", json.dumps({"a@b.c": {"groupTags": []}}))
    with pytest.raises(RuntimeError, match="uid"):
        assert_safe_tailnet_mode()


# ─── machine-created skills: access control ──────────────────────────────────

_AGENT = User(uid="machine:agent", email="agent@machine.local", group_tags=frozenset({"agent"}), auth_mode="machine")
_ADA = User(uid="ada", email="ada@example.com", domain="example.com", group_tags=frozenset({"staff"}))


@pytest.mark.parametrize(
    "ac",
    [None, {}, {"type": "private"}, {"type": "tagged", "tags": ["agent"]}],
)
def test_machine_may_keep_skills_to_itself(ac):
    check_machine_access_control(_AGENT, ac)


@pytest.mark.parametrize(
    "ac",
    [
        {"type": "public"},
        {"type": "domain", "domain": "example.com"},
        {"type": "specific", "emails": ["ada@example.com"]},
        {"type": "tagged", "tags": ["staff"]},
        {"type": "tagged", "tags": ["agent", "staff"]},
        {"type": "tagged", "tags": []},
        {"type": "bogus"},
    ],
)
def test_machine_may_not_publish(ac):
    with pytest.raises(HTTPException) as exc:
        check_machine_access_control(_AGENT, ac)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "MACHINE_ACCESS_CONTROL_NOT_PERMITTED"


def test_humans_are_unaffected():
    check_machine_access_control(_ADA, {"type": "public"})


@pytest.mark.asyncio
async def test_machine_gets_an_email_for_tool_permissions():
    req = _FakeRequest({"Authorization": f"Bearer {MACHINE_TOKEN}"})
    user = await get_current_user_tailnet(req)
    assert user.email == "agent@machine.local"
    assert user.domain == "machine.local"


@pytest.mark.asyncio
async def test_display_name_comes_from_the_principal(monkeypatch):
    monkeypatch.setenv(
        "TAILNET_PRINCIPALS", json.dumps({"ada@example.com": {"uid": "ada", "groupTags": [], "displayName": "Ada"}})
    )
    tailnet_auth._config.cache_clear()
    req = _FakeRequest({"X-Proxy-Secret": SECRET, "Tailscale-User-Login": "ada@example.com"})
    assert (await get_current_user_tailnet(req)).display_name == "Ada"


# ─── resolve by UID (tools that re-resolve the caller, e.g. generate_document) ──


def test_resolve_by_uid_finds_tailnet_and_machine_principals():
    from auth.firebase_auth import resolve_user_by_uid

    mark = resolve_user_by_uid("ada")
    assert mark is not None and mark.email == "ada@example.com" and mark.auth_mode == "tailnet"
    agent = resolve_user_by_uid("machine:agent")
    assert agent is not None and agent.email == "agent@machine.local" and agent.auth_mode == "machine"
    assert resolve_user_by_uid("someone-else") is None


def test_resolve_by_uid_never_calls_firebase_in_tailnet_mode(monkeypatch):
    from auth import firebase_auth

    def boom(uid):
        raise AssertionError("tailnet mode must not read Firebase")

    monkeypatch.setattr(firebase_auth.fb_auth, "get_user", boom)
    assert firebase_auth.resolve_user_by_uid("ada") is not None


# ─── shared machine threads ──────────────────────────────────────────────────


def test_machine_supervisors_and_notify_inbox_are_parsed(monkeypatch):
    monkeypatch.setenv(
        "MACHINE_PRINCIPALS",
        json.dumps(
            {
                token_hash(MACHINE_TOKEN): {
                    "uid": "machine:agent",
                    "supervisors": [" Ada@Example.com ", ""],
                    "notifyInbox": "agent",
                }
            }
        ),
    )
    tailnet_auth._config.cache_clear()
    user = tailnet_auth.resolve_user_by_uid_tailnet("machine:agent")
    assert user is not None
    assert user.supervisors == ("ada@example.com",)
    assert user.notify_inbox == "agent"


def test_principal_without_supervisors_shares_nothing():
    user = tailnet_auth.resolve_user_by_uid_tailnet("ada")
    assert user is not None
    assert user.supervisors == ()
    assert user.notify_inbox == ""


def test_default_machine_routes_can_read_own_threads():
    principal = {"uid": "machine:agent"}
    assert tailnet_auth.machine_route_allowed(principal, "GET", "/api/sessions")
    assert tailnet_auth.machine_route_allowed(principal, "GET", "/api/sessions/thread-1/messages")
    assert not tailnet_auth.machine_route_allowed(principal, "DELETE", "/api/sessions/thread-1")
