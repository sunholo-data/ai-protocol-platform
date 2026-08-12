"""GET /api/sessions — the caller's conversations across ALL skills.

Regression context (2026-08-05): switching agent via the top bar starts a NEW
session on the new skill (``skillHref`` carries no ``?session=``), so one
sitting that moved between agents becomes one session per skill. History was
scoped per-skill, so each fragment only showed under its own agent and a real
7-turn sitting across two agents read as "it didn't record my session".

These cover the cross-skill listing, the optional per-skill filter, and the
friendly agent label (CLAUDE.md #9 — never make a human tell conversations
apart by UUID).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


def _make_idx(session_id: str, skill_id: str, owner_uid: str = "u1", title: str | None = None):
    from db.models.access import AccessControl
    from db.models.chat_session import ChatSessionIndex

    return ChatSessionIndex(
        sessionId=session_id,
        documentIds=[],
        skillId=skill_id,
        ownerUid=owner_uid,
        accessControl=AccessControl(type="private"),
        firstMessageAt=datetime.now(UTC),
        lastMessageAt=datetime.now(UTC),
        title=title,
    )


def _client(caller_uid: str = "u1"):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from auth import get_current_user
    from auth.access_context import AccessContext
    from auth.firebase_auth import User
    from protocols.sessions_route import router

    stub_user = User(uid=caller_uid, email=f"{caller_uid}@example.com", domain="example.com")
    app = FastAPI()

    @app.middleware("http")
    async def inject_access(request: Request, call_next):
        request.state.access = AccessContext(uid=caller_uid, domain="example.com")
        return await call_next(request)

    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.include_router(router)
    return TestClient(app)


def _skill(name: str):
    s = MagicMock()
    s.display_name = name
    s.name = name
    return s


class TestCrossSkillSessionList:
    def test_lists_sessions_from_every_skill(self):
        """The whole point: a sitting split across agents comes back together."""
        rows = [
            _make_idx("s-ppa", "skill-ppa", title="Clarifying capabilities"),
            _make_idx("s-reasoner", "skill-reasoner"),
        ]
        labels = {"skill-ppa": _skill("Contract Expert"), "skill-reasoner": _skill("OpenAI Reasoner")}

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)) as lister,
            patch("skills.skill_config.get_skill", side_effect=lambda sid: labels.get(sid)),
        ):
            resp = _client().get("/api/sessions")

        assert resp.status_code == 200
        # skill_id=None is what makes it cross-skill.
        assert lister.call_args.kwargs["skill_id"] is None
        assert lister.call_args.kwargs["owner_uid"] == "u1"

        got = resp.json()["sessions"]
        assert [s["session_id"] for s in got] == ["s-ppa", "s-reasoner"]

    def test_each_row_carries_a_friendly_agent_name(self):
        rows = [_make_idx("s-ppa", "skill-ppa")]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)),
            patch("skills.skill_config.get_skill", return_value=_skill("Contract Expert")),
        ):
            resp = _client().get("/api/sessions")

        row = resp.json()["sessions"][0]
        assert row["skill_label"] == "Contract Expert"
        assert "skill-ppa" not in (row["skill_label"] or ""), "never surface the raw id as the label"

    def test_unresolvable_skill_yields_no_label_not_a_uuid(self):
        """A deleted skill must not degrade into printing its UUID at a human."""
        rows = [_make_idx("s-orphan", "skill-gone")]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)),
            patch("skills.skill_config.get_skill", return_value=None),
        ):
            resp = _client().get("/api/sessions")

        assert resp.json()["sessions"][0]["skill_label"] is None

    def test_label_lookup_failure_is_fail_soft(self):
        rows = [_make_idx("s-1", "skill-boom")]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)),
            patch("skills.skill_config.get_skill", side_effect=RuntimeError("firestore down")),
        ):
            resp = _client().get("/api/sessions")

        assert resp.status_code == 200, "a label lookup must never 500 the history list"
        assert resp.json()["sessions"][0]["skill_label"] is None

    def test_labels_resolved_once_per_distinct_skill(self):
        """Three sessions on one agent = one lookup, not three."""
        rows = [_make_idx(f"s-{i}", "skill-ppa") for i in range(3)]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)),
            patch("skills.skill_config.get_skill", return_value=_skill("Contract Expert")) as g,
        ):
            _client().get("/api/sessions")

        assert g.call_count == 1

    def test_skill_id_filter_scopes_to_one_agent(self):
        rows = [_make_idx("s-ppa", "skill-ppa")]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, None)) as lister,
            patch("skills.skill_config.get_skill", return_value=_skill("Contract Expert")),
        ):
            resp = _client().get("/api/sessions?skill_id=skill-ppa")

        assert resp.status_code == 200
        assert lister.call_args.kwargs["skill_id"] == "skill-ppa"

    def test_is_owner_scoped(self):
        """Never a cross-user view — the query is always pinned to the caller."""
        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=([], None)) as lister,
            patch("skills.skill_config.get_skill", return_value=None),
        ):
            _client(caller_uid="someone-else").get("/api/sessions")

        assert lister.call_args.kwargs["owner_uid"] == "someone-else"

    def test_pagination_cursor_round_trips(self):
        rows = [_make_idx("s-1", "skill-ppa")]

        with (
            patch("protocols.sessions_route.list_sessions_for_skill", return_value=(rows, "cur-2")) as lister,
            patch("skills.skill_config.get_skill", return_value=_skill("A")),
        ):
            resp = _client().get("/api/sessions?cursor=cur-1&page_size=1")

        assert lister.call_args.kwargs["cursor"] == "cur-1"
        assert lister.call_args.kwargs["page_size"] == 1
        assert resp.json()["next_cursor"] == "cur-2"

    def test_recent_route_is_not_shadowed(self):
        """`/sessions/recent` must not be captured by the new `/sessions` route."""
        from protocols.sessions_route import router

        paths = {getattr(r, "path", "") for r in router.routes}
        assert "/api/sessions" in paths
        assert "/api/sessions/recent" in paths
