"""Unit tests for GET /api/sessions/{sessionId}/messages.

Verifies:
- Owner gets 200 with chronologically ordered messages
- Non-owner gets 403 (never 404 — avoids existence leak for sessions)
- Empty / no-text events → empty messages list
- Tool call events (no text content) are filtered out
- Text events from user and assistant are both included
- Missing session → 404
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

from protocols.sessions_route import _events_to_messages

# ---------------------------------------------------------------------------
# _events_to_messages unit tests (pure function, no HTTP)
# ---------------------------------------------------------------------------


def _make_event(author: str, text: str | None, timestamp: float = 1.0):
    """Build a minimal ADK-shaped event mock."""
    event = MagicMock()
    event.author = author
    event.timestamp = timestamp
    if text is not None:
        part = MagicMock()
        part.text = text
        event.content = MagicMock()
        event.content.parts = [part]
    else:
        event.content = None
    return event


def _make_multi_part_event(author: str, parts: list[str], timestamp: float = 1.0):
    """Build an event with multiple content parts."""
    event = MagicMock()
    event.author = author
    event.timestamp = timestamp
    part_mocks = []
    for t in parts:
        p = MagicMock()
        p.text = t
        part_mocks.append(p)
    event.content = MagicMock()
    event.content.parts = part_mocks
    return event


class TestEventsToMessages:
    def test_user_event_maps_to_user_role(self):
        events = [_make_event("user", "Hello")]
        msgs = _events_to_messages(events)
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == "Hello"

    def test_non_user_author_maps_to_assistant(self):
        events = [_make_event("some_skill_agent", "Hi there")]
        msgs = _events_to_messages(events)
        assert msgs[0].role == "assistant"

    def test_event_with_no_content_is_skipped(self):
        events = [_make_event("user", None)]
        msgs = _events_to_messages(events)
        assert msgs == []

    def test_event_with_empty_parts_is_skipped(self):
        event = MagicMock()
        event.author = "user"
        event.timestamp = 1.0
        event.content = MagicMock()
        event.content.parts = []
        msgs = _events_to_messages([event])
        assert msgs == []

    def test_event_with_only_none_text_parts_is_skipped(self):
        event = MagicMock()
        event.author = "user"
        event.timestamp = 1.0
        part = MagicMock()
        part.text = None
        event.content = MagicMock()
        event.content.parts = [part]
        msgs = _events_to_messages([event])
        assert msgs == []

    def test_multi_part_text_is_joined(self):
        events = [_make_multi_part_event("user", ["Hello", " world"], 1.0)]
        msgs = _events_to_messages(events)
        assert msgs[0].content == "Hello  world"

    def test_preserves_timestamp(self):
        # d68bd33: ADK events are unix SECONDS; the route now emits epoch MS
        # (_to_epoch_ms) so the frontend renders each history bubble at its
        # ORIGINAL send time (matches session_start_ms units).
        events = [_make_event("user", "Hi", timestamp=1714000042)]
        msgs = _events_to_messages(events)
        assert msgs[0].timestamp == 1714000042000

    def test_chronological_order_preserved(self):
        events = [
            _make_event("user", "first", 1.0),
            _make_event("assistant_agent", "second", 2.0),
            _make_event("user", "third", 3.0),
        ]
        msgs = _events_to_messages(events)
        assert len(msgs) == 3
        assert msgs[0].content == "first"
        assert msgs[1].content == "second"
        assert msgs[2].content == "third"

    def test_empty_event_list(self):
        assert _events_to_messages([]) == []


# ---------------------------------------------------------------------------
# HTTP endpoint tests (FastAPI TestClient)
# ---------------------------------------------------------------------------


def _make_idx(session_id: str = "sess-1", owner_uid: str = "u1"):
    from datetime import datetime

    from db.models.access import AccessControl
    from db.models.chat_session import ChatSessionIndex

    return ChatSessionIndex(
        sessionId=session_id,
        documentIds=[],
        skillId="skill-x",
        ownerUid=owner_uid,
        accessControl=AccessControl(type="private"),
        firstMessageAt=datetime.now(UTC),
        lastMessageAt=datetime.now(UTC),
    )


def _make_test_client(caller_uid: str = "u1"):
    """Build a FastAPI TestClient with auth + access middleware stubbed."""
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


class TestGetSessionMessages:
    def test_owner_gets_200_with_messages(self):
        idx = _make_idx(owner_uid="u1")
        session_mock = MagicMock()
        session_mock.events = [_make_event("user", "hello", 1.0)]

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=session_mock)
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-1"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "hello"

    def test_non_owner_gets_403(self):
        idx = _make_idx(owner_uid="u1")

        with patch("protocols.sessions_route.get_session_index", return_value=idx):
            resp = _make_test_client(caller_uid="u2").get("/api/sessions/sess-1/messages")

        assert resp.status_code == 403

    def test_missing_session_gets_404(self):
        with patch("protocols.sessions_route.get_session_index", return_value=None):
            resp = _make_test_client(caller_uid="u1").get("/api/sessions/nonexistent/messages")

        assert resp.status_code == 404

    def test_empty_session_returns_empty_list(self):
        idx = _make_idx(owner_uid="u1")
        session_mock = MagicMock()
        session_mock.events = []

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=session_mock)
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_tool_events_filtered_only_text_included(self):
        idx = _make_idx(owner_uid="u1")
        session_mock = MagicMock()
        session_mock.events = [
            _make_event("user", "query", 1.0),
            _make_event("agent", None, 2.0),  # tool call — no text content
            _make_event("agent", "result", 3.0),
        ]

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=session_mock)
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        messages = resp.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "query"
        assert messages[1]["content"] == "result"


class TestTranscriptUnavailable:
    """Regression (2026-08-05): a listed conversation whose ADK session is gone.

    ag_ui_adk's SessionManager sweep was deleting idle sessions straight out of
    Vertex Agent Engine while the Firestore index row (metadata only) survived.
    The route then found no ADK session and returned ``200 {"messages": []}`` —
    indistinguishable from an empty chat, so resuming yesterday's conversation
    rendered a blank thread with no explanation (CLAUDE.md #8, NEVER SILENT).

    The deletion itself is fixed in ``adk/agui.py``; this covers the reporting
    half, which also matters for the conversations already destroyed.
    """

    def test_missing_adk_session_with_prior_turns_flags_unavailable(self):
        idx = _make_idx(owner_uid="u1")
        idx.turn_count = 12

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=None)  # deleted from Vertex
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["transcript_unavailable"] is True, (
            "a destroyed transcript must be distinguishable from an empty chat, "
            "or the UI renders an unexplained blank thread"
        )

    def test_fresh_session_with_no_turns_is_not_flagged(self):
        """A genuinely new/empty conversation is NOT an error — no notice."""
        idx = _make_idx(owner_uid="u1")
        idx.turn_count = 0

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=None)
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        assert resp.status_code == 200
        assert resp.json()["transcript_unavailable"] is False

    def test_healthy_session_is_not_flagged(self):
        idx = _make_idx(owner_uid="u1")
        idx.turn_count = 3
        session_mock = MagicMock()
        session_mock.events = [_make_event("user", "hello", 1.0)]

        with (
            patch("protocols.sessions_route.get_session_index", return_value=idx),
            patch("protocols.sessions_route.get_messages_session_service") as svc_fn,
        ):
            svc = MagicMock()
            svc.get_session = AsyncMock(return_value=session_mock)
            svc_fn.return_value = svc

            resp = _make_test_client(caller_uid="u1").get("/api/sessions/sess-1/messages")

        assert resp.json()["transcript_unavailable"] is False
        assert len(resp.json()["messages"]) == 1
