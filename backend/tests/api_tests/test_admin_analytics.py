"""API tests for /api/admin/analytics — session list + trace.

Firestore + ADK session reads are mocked. Tests exercise:
  - admin guard (aitana-admin required) on both routes
  - list: shape, newest-first sort, q filter, skill_id filter
  - trace: 404 on unknown session, reconstruction wiring for a known one
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import User, get_current_user

_ADMIN = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)
_NON_ADMIN = User(
    uid="user-uid",
    email="user@example.com",
    domain="example.com",
    group_tags=frozenset(),
)

_DOCS = [
    {
        "__id": "sess-old",
        "skillId": "one-ppa-expert",
        "ownerUid": "u1",
        "title": "PPA review",
        "turnCount": 3,
        "documentIds": ["doc-a", "doc-b"],
        "firstMessageAt": "2026-07-01T09:00:00+00:00",
        "lastMessageAt": "2026-07-01T09:30:00+00:00",
        "transcriptLost": True,
    },
    {
        "__id": "sess-new",
        "skillId": "web-researcher",
        "ownerUid": "u2",
        "title": "Market scan",
        "turnCount": 1,
        "firstMessageAt": "2026-07-10T12:00:00+00:00",
        "lastMessageAt": "2026-07-10T12:05:00+00:00",
    },
    {
        "__id": "sess-mid",
        "skillId": "one-ppa-expert",
        "ownerUid": "u1",
        "title": "Clause check",
        "turnCount": 2,
        "firstMessageAt": "2026-07-05T10:00:00+00:00",
        "lastMessageAt": "2026-07-05T10:20:00+00:00",
    },
    # Never-used husks — must be hidden from the list (and its facets) but
    # counted in hidden_empty: a provisional bootstrap row, and its pre-flag
    # ancestor (zero turns, never titled, no documents).
    {
        "__id": "sess-husk-provisional",
        "skillId": "one-ppa-expert",
        "ownerUid": "u3",
        "turnCount": 0,
        "provisional": True,
        "firstMessageAt": "2026-07-11T08:00:00+00:00",
        "lastMessageAt": "2026-07-11T08:00:00+00:00",
    },
    {
        "__id": "sess-husk-legacy",
        "skillId": "abandoned-skill",
        "ownerUid": "u3",
        "turnCount": 0,
        "firstMessageAt": "2026-05-01T08:00:00+00:00",
        "lastMessageAt": "2026-05-01T08:00:00+00:00",
    },
    # Zero turns but TITLED — someone invested in it, so it stays visible.
    {
        "__id": "sess-empty-titled",
        "skillId": "web-researcher",
        "ownerUid": "u2",
        "title": "Draft for later",
        "turnCount": 0,
        "firstMessageAt": "2026-06-01T08:00:00+00:00",
        "lastMessageAt": "2026-06-01T08:00:00+00:00",
    },
]


@pytest.fixture(autouse=True)
def _hermetic_lookups():
    """Keep API tests off Firestore/skills: friendly-label + author-map lookups
    are patched to their empty fallbacks unless a test overrides them."""
    with (
        patch("admin.analytics_routes.resolve_skill_labels", return_value={}),
        patch("admin.analytics_routes._author_agent_map", return_value={}),
    ):
        yield


def _make_app() -> FastAPI:
    from admin.analytics_routes import router

    app = FastAPI()
    app.include_router(router)
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


# --- list -----------------------------------------------------------------


def test_list_sessions_admin_newest_first(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess-new", "sess-mid", "sess-old", "sess-empty-titled"]
    assert sessions[0]["skill_id"] == "web-researcher"
    assert sessions[0]["turn_count"] == 1


def test_list_sessions_q_filter(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"q": "market"})
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess-new"


def test_list_sessions_skill_filter(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"skill_id": "one-ppa-expert"})
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess-mid", "sess-old"]


def test_list_sessions_owner_filter(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"owner_uid": "u2"})
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess-new", "sess-empty-titled"]


def test_list_sessions_facets_cover_full_population_despite_filters(admin_client: TestClient) -> None:
    """Facets describe ALL in-scope rows — filtering to one owner must not
    collapse the selectors down to that owner (or the UI could never filter
    back out)."""
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"owner_uid": "u2"})
    body = resp.json()
    owners = {o["uid"]: o for o in body["owners"]}
    # u3 owns only hidden husks — it must not appear as a selectable user.
    assert set(owners) == {"u1", "u2"}
    assert owners["u1"]["sessions"] == 2
    assert owners["u1"]["last_active"] == "2026-07-05T10:20:00+00:00"
    skills = {s["id"]: s for s in body["skills"]}
    assert set(skills) == {"one-ppa-expert", "web-researcher"}
    assert skills["one-ppa-expert"]["sessions"] == 2


def test_list_sessions_hides_never_used_husks(admin_client: TestClient) -> None:
    """Provisional bootstrap rows and their pre-flag ancestors (0 turns, no
    title, no docs) are excluded — but counted, so the UI can say so. A
    zero-turn row someone titled stays visible."""
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions")
    body = resp.json()
    ids = {s["session_id"] for s in body["sessions"]}
    assert "sess-husk-provisional" not in ids
    assert "sess-husk-legacy" not in ids
    assert "sess-empty-titled" in ids
    assert body["hidden_empty"] == 2


def test_list_sessions_rows_carry_docs_and_transcript_lost(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=_DOCS):
        resp = admin_client.get("/api/admin/analytics/sessions")
    by_id = {s["session_id"]: s for s in resp.json()["sessions"]}
    assert by_id["sess-old"]["document_count"] == 2
    assert by_id["sess-old"]["transcript_lost"] is True
    assert by_id["sess-new"]["document_count"] == 0
    assert by_id["sess-new"]["transcript_lost"] is False


def test_list_sessions_q_matches_friendly_skill_label(admin_client: TestClient) -> None:
    """CLAUDE.md #9 — an admin searches for "PPA Expert", not a skill UUID."""
    with (
        patch("admin.analytics_routes.query_documents", return_value=_DOCS),
        patch(
            "admin.analytics_routes.resolve_skill_labels",
            return_value={"one-ppa-expert": "Contract Expert"},
        ),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"q": "contract expert"})
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess-mid", "sess-old"]
    assert sessions[0]["skill_label"] == "Contract Expert"


def test_list_sessions_non_admin_403(nonadmin_client: TestClient) -> None:
    with patch("admin.analytics_routes.query_documents", return_value=[]):
        resp = nonadmin_client.get("/api/admin/analytics/sessions")
    assert resp.status_code == 403


def test_list_sessions_enriches_owner_email_and_name(admin_client: TestClient) -> None:
    directory = {"u1": ("alex@corp.com", "Alex Rivera"), "u2": ("", "")}
    with (
        patch("admin.analytics_routes.query_documents", return_value=_DOCS),
        patch("admin.analytics_routes._resolve_user_directory", return_value=directory),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions")
    assert resp.status_code == 200
    by_id = {s["session_id"]: s for s in resp.json()["sessions"]}
    assert by_id["sess-old"]["owner_email"] == "alex@corp.com"
    assert by_id["sess-old"]["owner_name"] == "Alex Rivera"
    # A directory miss leaves the fields blank (UI falls back to the uid).
    assert by_id["sess-new"]["owner_email"] == ""


def test_list_sessions_q_matches_resolved_name(admin_client: TestClient) -> None:
    # A search must hit the friendly name/email, not just the raw uid/title.
    directory = {"u1": ("alex@corp.com", "Alex Rivera"), "u2": ("bob@corp.com", "Bob")}
    with (
        patch("admin.analytics_routes.query_documents", return_value=_DOCS),
        patch("admin.analytics_routes._resolve_user_directory", return_value=directory),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions", params={"q": "alex"})
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["sess-mid", "sess-old"]


# --- trace ----------------------------------------------------------------


def test_trace_unknown_session_404(admin_client: TestClient) -> None:
    with patch("admin.analytics_routes.get_session_index", return_value=None):
        resp = admin_client.get("/api/admin/analytics/sessions/nope")
    assert resp.status_code == 404


def test_trace_known_session_reconstructs(admin_client: TestClient) -> None:
    from datetime import UTC, datetime
    from typing import ClassVar

    from protocols.sessions_route import ChatMessage

    class _Idx:
        owner_uid = "u1"
        skill_id = "one-ppa-expert"
        title = "PPA review"
        turn_count = 3
        document_ids: ClassVar[list[str]] = ["doc-a"]
        first_message_at = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        last_message_at = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)

    class _Session:
        events = ("e1", "e2")

    async def _get_session(**_kwargs):
        return _Session()

    class _Service:
        get_session = staticmethod(_get_session)

    msg = ChatMessage(role="user", content="hi", timestamp=1.0)
    with (
        patch("admin.analytics_routes.get_session_index", return_value=_Idx()),
        patch("admin.analytics_routes.get_messages_session_service", return_value=_Service()),
        patch("admin.analytics_routes._events_to_messages", return_value=[msg]),
        patch("admin.analytics_routes._events_to_tool_activity", return_value=[]),
        patch("admin.analytics_routes._events_to_delegations", return_value=[]),
        patch(
            "admin.analytics_routes.resolve_skill_labels",
            return_value={"one-ppa-expert": "Contract Expert"},
        ),
        patch(
            "admin.analytics_routes.get_document",
            return_value={"originalFilename": "demo-leap.pdf"},
        ),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions/sess-old")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_uid"] == "u1"
    assert body["skill_id"] == "one-ppa-expert"
    assert body["skill_label"] == "Contract Expert"
    assert body["title"] == "PPA review"
    assert body["turn_count"] == 3
    assert body["first_message_at"] == "2026-07-01T09:00:00+00:00"
    assert body["event_count"] == 2
    assert body["documents"] == [{"id": "doc-a", "name": "demo-leap.pdf"}]
    assert body["messages"] == [
        {"role": "user", "content": "hi", "timestamp": 1.0, "avatar": None, "agent_label": None}
    ]


def test_trace_document_name_falls_back_to_id(admin_client: TestClient) -> None:
    """An unresolvable doc keeps its id as the display name — never dropped."""

    from typing import ClassVar

    class _Idx:
        owner_uid = "u1"
        skill_id = "s"
        document_ids: ClassVar[list[str]] = ["doc-gone"]

    async def _get_session(**_kwargs):
        return None

    class _Service:
        get_session = staticmethod(_get_session)

    with (
        patch("admin.analytics_routes.get_session_index", return_value=_Idx()),
        patch("admin.analytics_routes.get_messages_session_service", return_value=_Service()),
        patch("admin.analytics_routes.get_document", return_value=None),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions/sess-x")
    assert resp.json()["documents"] == [{"id": "doc-gone", "name": "doc-gone"}]


def test_trace_transcript_unavailable_when_canonical_session_missing(admin_client: TestClient) -> None:
    """Mirror row exists (index found) but the canonical session is gone
    (get_session → None): 200 with transcript_available False, NOT a 404 and
    NOT a silent empty transcript. This is the 2026-07-23 divergence."""

    class _Idx:
        owner_uid = "u1"
        skill_id = "claude-assistant"
        turn_count = 117

    async def _get_session(**_kwargs):
        return None

    class _Service:
        get_session = staticmethod(_get_session)

    with (
        patch("admin.analytics_routes.get_session_index", return_value=_Idx()),
        patch("admin.analytics_routes.get_messages_session_service", return_value=_Service()),
    ):
        resp = admin_client.get("/api/admin/analytics/sessions/sess-orphan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript_available"] is False
    assert body["messages"] == []


def test_trace_non_admin_403(nonadmin_client: TestClient) -> None:
    resp = nonadmin_client.get("/api/admin/analytics/sessions/whatever")
    assert resp.status_code == 403
