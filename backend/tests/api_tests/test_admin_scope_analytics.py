"""Tenant-scoped analytics + argsJson redaction (v6.16.0 / ADMIN-SCOPE M4).

Two properties matter most here and both are easy to get subtly wrong:

  1. **Fail closed on unknown ownership.** Rows written before the backfill have
     a blank ownerDomain. The tempting default ("show it, we don't know whose it
     is") is precisely the leak.
  2. **argsJson never reaches a non-platform reader.** Tool arguments carry
     document ids and extracted clause text — derivatives of private content.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

_ROWS = [
    {"__id": "s-a1", "ownerUid": "u1", "ownerDomain": "a.com", "skillId": "one", "title": "Alpha chat"},
    {"__id": "s-b1", "ownerUid": "u2", "ownerDomain": "b.com", "skillId": "one", "title": "Beta chat"},
    # Pre-backfill row: owner unknown.
    {"__id": "s-legacy", "ownerUid": "u3", "skillId": "one", "title": "Legacy chat"},
]


def _app(user: User) -> TestClient:
    from admin.analytics_routes import router

    app = FastAPI()
    app.include_router(router)

    async def _override(request: Request) -> User:
        return user

    app.dependency_overrides[get_current_user] = _override
    return TestClient(app)


class TestSessionListScoping:
    def test_platform_sees_every_session(self):
        with patch("admin.analytics_routes.query_documents", return_value=[dict(r) for r in _ROWS]):
            r = _app(_PLATFORM).get("/api/admin/analytics/sessions")
        assert r.status_code == 200
        assert {s["session_id"] for s in r.json()["sessions"]} == {"s-a1", "s-b1", "s-legacy"}

    def test_tenant_sees_only_own_domain(self):
        with patch("admin.analytics_routes.query_documents", return_value=[dict(r) for r in _ROWS]):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions")
        assert r.status_code == 200
        assert [s["session_id"] for s in r.json()["sessions"]] == ["s-a1"]

    def test_blank_owner_domain_fails_closed(self):
        """A pre-backfill row must NOT surface to a tenant admin just because
        its owner is unknown — unattributable is not the same as mine."""
        with patch("admin.analytics_routes.query_documents", return_value=[dict(r) for r in _ROWS]):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions")
        assert "s-legacy" not in {s["session_id"] for s in r.json()["sessions"]}

    def test_scope_filter_survives_other_filters(self):
        """A skill/q filter must not be able to re-admit an out-of-scope row."""
        with patch("admin.analytics_routes.query_documents", return_value=[dict(r) for r in _ROWS]):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions?skill_id=one&q=chat")
        assert [s["session_id"] for s in r.json()["sessions"]] == ["s-a1"]

    def test_query_cannot_reach_another_tenant(self):
        with patch("admin.analytics_routes.query_documents", return_value=[dict(r) for r in _ROWS]):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions?q=Beta")
        assert r.json()["sessions"] == []


class TestTracePolicy:
    """Transcript access, per the 2026-07-21 CHAT-TRANSCRIPT-ACCESS ruling.

    A tenant admin may read full conversations for their OWN domain (the tenant
    is the data controller). The tenant boundary around that is unchanged.
    """

    def _idx(self, domain: str):
        return SimpleNamespace(owner_uid="u1", owner_domain=domain, skill_id="one")

    def test_tenant_may_read_own_transcript(self):
        with (
            patch("admin.analytics_routes.get_session_index", return_value=self._idx("a.com")),
            patch("admin.analytics_routes.get_messages_session_service") as svc,
        ):
            svc.return_value.get_session = AsyncMock(return_value=None)
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions/s-a1")
        assert r.status_code == 200

    def test_tenant_cannot_read_another_domains_transcript(self):
        with patch("admin.analytics_routes.get_session_index", return_value=self._idx("b.com")):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions/s-b1")
        assert r.status_code == 403

    def test_unattributable_session_fails_closed_for_tenant(self):
        """A pre-backfill row has a blank ownerDomain. Serving a whole
        conversation because we cannot say whose it is would be the worst
        version of this endpoint."""
        with patch("admin.analytics_routes.get_session_index", return_value=self._idx("")):
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions/s-legacy")
        assert r.status_code == 403

    def test_platform_may_read_an_unattributable_session(self):
        with (
            patch("admin.analytics_routes.get_session_index", return_value=self._idx("")),
            patch("admin.analytics_routes.get_messages_session_service") as svc,
        ):
            svc.return_value.get_session = AsyncMock(return_value=None)
            r = _app(_PLATFORM).get("/api/admin/analytics/sessions/s-legacy")
        assert r.status_code == 200

    def test_args_json_is_no_longer_redacted_for_an_in_scope_tenant_admin(self):
        """Follows from the ruling: redacting the arguments while serving the
        conversation they came from protected nothing."""
        events: list = []
        with (
            patch("admin.analytics_routes.get_session_index", return_value=self._idx("a.com")),
            patch("admin.analytics_routes.get_messages_session_service") as svc,
            patch(
                "admin.analytics_routes._events_to_tool_activity",
                return_value=[{"name": "search", "argsJson": '{"q":"contract terms"}'}],
            ),
            patch("admin.analytics_routes._events_to_messages", return_value=[]),
            patch("admin.analytics_routes._events_to_delegations", return_value=[]),
        ):
            svc.return_value.get_session = AsyncMock(return_value=SimpleNamespace(events=events))
            r = _app(_TENANT_A).get("/api/admin/analytics/sessions/s-a1")
        assert r.status_code == 200
        assert "contract terms" in str(r.json()["tools"])


class TestOwnerDomainDerivation:
    def test_owner_domain_of(self):
        from db.chat_sessions import owner_domain_of

        assert owner_domain_of("Ops@A.COM") == "a.com"
        assert owner_domain_of("plain") == ""
        assert owner_domain_of("") == ""
        assert owner_domain_of(None) == ""
