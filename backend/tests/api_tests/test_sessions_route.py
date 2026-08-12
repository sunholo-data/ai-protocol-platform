"""API tests for chat session CRUD endpoints.

All endpoints require authentication. Tests use a minimal FastAPI app with
mocked Firestore helpers so no real GCP connection is needed.

Acceptance criteria verified:
- GET /api/documents/{id}/sessions?filter=mine returns only caller's sessions
- GET /api/documents/{id}/sessions?filter=team returns sessions with shared tag
- GET /api/documents/{id}/sessions?filter=team returns 200 empty list when viewer has no tags
- GET /api/sessions/{id} returns 403 when caller has no access
- PATCH /api/sessions/{id} with same-tag non-owner viewer returns 403
- DELETE /api/sessions/{id} with non-owner returns 403
- DELETE /api/sessions/{id} with owner returns 204 and soft-deletes
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User
from auth.access_context import AccessContext
from db.models.access import AccessControl
from db.models.chat_session import ChatSessionIndex
from protocols.sessions_route import router

# ---------------------------------------------------------------------------
# Test app + auth mock
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(router)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _make_session(
    session_id: str = "sess-1",
    owner_uid: str = "owner-uid",
    ac: AccessControl | None = None,
    doc_id: str = "doc-1",
    archived: bool = False,
) -> ChatSessionIndex:
    return ChatSessionIndex(
        sessionId=session_id,
        documentIds=[doc_id] if doc_id else [],
        skillId="skill-1",
        ownerUid=owner_uid,
        accessControl=ac or AccessControl(type="private"),
        firstMessageAt=_utcnow(),
        lastMessageAt=_utcnow(),
        archivedAt=_utcnow() if archived else None,
    )


def _inject_user(uid: str, tags: frozenset[str] = frozenset()) -> None:
    """Override get_current_user + request.state.access for a given uid."""
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain, group_tags=tags)

    from auth import firebase_auth

    original_get = (
        firebase_auth.get_current_user.__wrapped__
        if hasattr(firebase_auth.get_current_user, "__wrapped__")
        else firebase_auth.get_current_user
    )

    app.dependency_overrides[original_get] = lambda: user

    from fastapi import Request

    async def _state_middleware(request: Request, call_next):
        request.state.access = ctx
        return await call_next(request)

    app.middleware("http")(_state_middleware)


# Simpler approach: override via dependency injection at test level
from auth import get_current_user  # noqa: E402


def _make_client(uid: str, tags: frozenset[str] = frozenset()) -> TestClient:
    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    ctx = AccessContext(uid=uid, email=user.email, domain=user.domain, group_tags=tags)

    test_app = FastAPI()
    test_app.include_router(router)

    @test_app.middleware("http")
    async def _inject_access(request, call_next):
        request.state.access = ctx
        return await call_next(request)

    test_app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# GET /api/documents/{doc_id}/sessions
# ---------------------------------------------------------------------------


class TestListDocumentSessions:
    def _own_sess(self):
        return _make_session("s1", owner_uid="viewer", ac=AccessControl(type="private"))

    def _team_sess(self):
        return _make_session("s2", owner_uid="alice", ac=AccessControl(type="tagged", tags=["finance"]))

    @patch("protocols.sessions_route.list_sessions_for_document")
    def test_returns_200_with_sessions(self, mock_list):
        mock_list.return_value = ([self._own_sess()], None)
        client = _make_client("viewer")

        resp = client.get("/api/documents/doc-1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 1

    @patch("protocols.sessions_route.list_sessions_for_document")
    def test_mine_filter_passed_through(self, mock_list):
        mock_list.return_value = ([], None)
        client = _make_client("viewer")

        resp = client.get("/api/documents/doc-1/sessions?filter=mine")
        assert resp.status_code == 200
        call_kwargs = mock_list.call_args
        assert call_kwargs.kwargs.get("filter") == "mine" or "mine" in str(call_kwargs)

    @patch("protocols.sessions_route.list_sessions_for_document")
    def test_empty_list_for_viewer_with_no_tags(self, mock_list):
        mock_list.return_value = ([], None)
        client = _make_client("viewer", tags=frozenset())

        resp = client.get("/api/documents/doc-1/sessions?filter=team")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    @patch("protocols.sessions_route.list_sessions_for_document")
    def test_returns_next_cursor_when_more_pages(self, mock_list):
        mock_list.return_value = ([self._own_sess()], "s1")
        client = _make_client("viewer")

        resp = client.get("/api/documents/doc-1/sessions")
        assert resp.json()["next_cursor"] == "s1"

    def test_requires_auth(self):
        client = TestClient(app)
        resp = client.get("/api/documents/doc-1/sessions")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestGetSession:
    @patch("protocols.sessions_route.get_session_index")
    def test_owner_can_read(self, mock_get):
        mock_get.return_value = _make_session(owner_uid="viewer")
        client = _make_client("viewer")

        resp = client.get("/api/sessions/sess-1")
        assert resp.status_code == 200
        assert resp.json()["session"]["session_id"] == "sess-1"
        assert resp.json()["session"]["is_owner"] is True

    @patch("protocols.sessions_route.get_session_index")
    def test_tagged_viewer_can_read(self, mock_get):
        mock_get.return_value = _make_session(
            owner_uid="alice",
            ac=AccessControl(type="tagged", tags=["finance"]),
        )
        client = _make_client("viewer", tags=frozenset(["finance"]))

        resp = client.get("/api/sessions/sess-1")
        assert resp.status_code == 200
        assert resp.json()["session"]["is_owner"] is False

    @patch("protocols.sessions_route.get_session_index")
    def test_no_access_returns_403(self, mock_get):
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="private"))
        client = _make_client("viewer")

        resp = client.get("/api/sessions/sess-1")
        assert resp.status_code == 403

    @patch("protocols.sessions_route.get_session_index")
    def test_missing_session_returns_404(self, mock_get):
        mock_get.return_value = None
        client = _make_client("viewer")

        resp = client.get("/api/sessions/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestPatchSession:
    @patch("protocols.sessions_route.get_session_index")
    @patch("protocols.sessions_route.update_session_fields")
    def test_owner_can_rename(self, mock_update, mock_get):
        sess = _make_session(owner_uid="viewer")
        mock_get.return_value = sess
        client = _make_client("viewer")

        resp = client.patch("/api/sessions/sess-1", json={"title": "New Title"})
        assert resp.status_code == 200
        mock_update.assert_called_once()

    @patch("protocols.sessions_route.get_session_index")
    @patch("protocols.sessions_route.update_session_fields")
    def test_non_owner_with_tag_access_gets_403(self, mock_update, mock_get):
        sess = _make_session(owner_uid="alice", ac=AccessControl(type="tagged", tags=["finance"]))
        mock_get.return_value = sess
        client = _make_client("viewer", tags=frozenset(["finance"]))

        resp = client.patch("/api/sessions/sess-1", json={"title": "Hijack"})
        assert resp.status_code == 403
        mock_update.assert_not_called()

    @patch("protocols.sessions_route.get_session_index")
    def test_no_access_returns_403(self, mock_get):
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="private"))
        client = _make_client("viewer")

        resp = client.patch("/api/sessions/sess-1", json={"title": "X"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestDeleteSession:
    @patch("protocols.sessions_route.get_session_index")
    @patch("protocols.sessions_route.soft_delete_session")
    def test_owner_delete_returns_204(self, mock_delete, mock_get):
        mock_get.return_value = _make_session(owner_uid="viewer")
        client = _make_client("viewer")

        resp = client.delete("/api/sessions/sess-1")
        assert resp.status_code == 204
        mock_delete.assert_called_once_with("sess-1")

    @patch("protocols.sessions_route.get_session_index")
    @patch("protocols.sessions_route.soft_delete_session")
    def test_non_owner_returns_403(self, mock_delete, mock_get):
        mock_get.return_value = _make_session(
            owner_uid="alice",
            ac=AccessControl(type="tagged", tags=["finance"]),
        )
        client = _make_client("viewer", tags=frozenset(["finance"]))

        resp = client.delete("/api/sessions/sess-1")
        assert resp.status_code == 403
        mock_delete.assert_not_called()

    @patch("protocols.sessions_route.get_session_index")
    def test_no_access_returns_403(self, mock_get):
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="private"))
        client = _make_client("viewer")

        resp = client.delete("/api/sessions/sess-1")
        assert resp.status_code == 403

    @patch("protocols.sessions_route.get_session_index")
    def test_missing_session_returns_404(self, mock_get):
        mock_get.return_value = None
        client = _make_client("viewer")

        resp = client.delete("/api/sessions/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/sessions/{session_id}/messages — chat-history-deep-fixes-2 (1.15)
# ---------------------------------------------------------------------------


class TestGetSessionMessages:
    """D2' and D5' diagnostics for the message-read endpoint.

    D2' locks the current 500 failure mode (user_id triple inconsistency).
    D5' surfaces the access-policy gap: list endpoint shows shared sessions
    to non-owners, but message-read is owner-only — clicking a shared
    session 403s. Bug E.
    """

    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_d2_returns_500_when_vertex_user_id_mismatch(self, mock_get, mock_service_factory):
        """D2' (chat-history-deep-fixes-2 H1 lock-in): when ag_ui_adk created
        the Vertex session with user_id='thread_user_<id>' but Firestore has
        owner_uid=<firebase_uid>, the route's call to
        ``session_service.get_session(user_id=idx.owner_uid, ...)`` raises the
        ValueError documented in production logs:

            ValueError: Session ... does not belong to user uG9C...

        Unhandled, this surfaces to the real frontend client as HTTP 500.
        TestClient re-raises by default; we use raise_server_exceptions=False
        to observe what users actually see.
        """
        from unittest.mock import AsyncMock

        from auth import User, get_current_user
        from auth.access_context import AccessContext

        mock_get.return_value = _make_session(owner_uid="firebase-uid-abc")
        mock_session_service = AsyncMock()
        mock_session_service.get_session = AsyncMock(
            side_effect=ValueError("Session sess-1 does not belong to user firebase-uid-abc.")
        )
        mock_service_factory.return_value = mock_session_service

        # Build a TestClient that doesn't re-raise so we see the 500 the
        # real frontend sees in dev (which then triggers the
        # "Couldn't load previous messages" banner).
        user = User(uid="firebase-uid-abc", email="x@example.com", domain="example.com")
        ctx = AccessContext(uid="firebase-uid-abc", email=user.email, domain=user.domain, group_tags=frozenset())
        test_app = FastAPI()
        test_app.include_router(router)

        @test_app.middleware("http")
        async def _inject_access(request, call_next):
            request.state.access = ctx
            return await call_next(request)

        test_app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(test_app, raise_server_exceptions=False)

        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 500, (
            f"D2' lock-in: route surfaces an unhandled Vertex ValueError as "
            f"HTTP 500 to the client. Got {resp.status_code}: {resp.text[:200]}"
        )

    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_d5_bug_e_fix_non_owner_with_can_access_reads_messages(self, mock_get, mock_service_factory):
        """D5' Bug E fix-locking (chat-history-deep-fixes-2): the message-read
        endpoint must align with the metadata read at ``GET /api/sessions/{id}``
        — a caller who passes ``ctx.can_access(idx)`` gets messages, regardless
        of ownership. ``list_sessions_for_document`` already uses ``can_access``
        so non-owners see shared thread titles; without this fix, clicking one
        gives a 403 they don't expect.

        Pre-fix this test fails (route was ``is_owner``-only → 403). Post-fix
        passes.

        Vertex query always uses ``idx.owner_uid`` regardless of caller;
        sharing means reading the OWNER's events, not attributing them to
        the reader.
        """
        from unittest.mock import AsyncMock

        # Session owned by alice, public.
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="public"))
        mock_session_service = AsyncMock()
        mock_session_service.get_session = AsyncMock(return_value=None)
        mock_service_factory.return_value = mock_session_service

        client = _make_client("bob")  # different uid, not owner
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200, (
            f"Bug E fix: non-owner with can_access (public session) must "
            f"read messages. Got {resp.status_code}: {resp.text[:200]}"
        )

        # Verify Vertex was queried with the OWNER's uid, not the caller's.
        mock_session_service.get_session.assert_awaited_once()
        call_kwargs = mock_session_service.get_session.await_args.kwargs
        assert call_kwargs["user_id"] == "alice", (
            "Vertex query must use idx.owner_uid (the session's owner), not "
            "the caller's uid. The caller is bob; the session belongs to alice."
        )

    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_rehydrates_a2ui_surfaces_from_session_state(self, mock_get, mock_service_factory):
        """7.5 M3: the messages endpoint returns persisted A2UI workbench surfaces
        (session-scoped `a2ui_surface:*` state) ordered by createdAt, so the
        frontend can replay the workbench on resume without re-running tools."""
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        mock_get.return_value = _make_session(owner_uid="owner-uid")

        # Two stashed surfaces + one unrelated app: key (must be ignored). The
        # comparison was emitted AFTER the clauses → must sort last by createdAt.
        state = {
            "a2ui_surface:ppa_clauses:doc-A": json.dumps(
                {
                    "surfaceId": "ppa_clauses:doc-A",
                    "messages": [{"createSurface": {"surfaceId": "ppa_clauses:doc-A"}}],
                    "artifact": {"kind": "clauses", "title": "Clauses"},
                    "sourceId": "inv:extract:1",
                    "toolName": "extract_ppa_clauses",
                    "createdAt": 1000.0,
                }
            ),
            "a2ui_surface:ppa_comparison": json.dumps(
                {
                    "surfaceId": "ppa_comparison",
                    "messages": [{"createSurface": {"surfaceId": "ppa_comparison"}}],
                    "artifact": {"kind": "comparison", "title": "Comparison"},
                    "sourceId": "inv:compare:1",
                    "toolName": "compare_ppa_contracts",
                    "createdAt": 2000.0,
                }
            ),
            "app:emitted:ppa_clauses:doc-A": "{...raw cache, not a surface...}",
        }
        mock_session = SimpleNamespace(events=[], state=state)
        mock_service = AsyncMock()
        mock_service.get_session = AsyncMock(return_value=mock_session)
        mock_service_factory.return_value = mock_service

        client = _make_client("owner-uid")
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        surfaces = resp.json()["a2ui_surfaces"]
        assert [s["surfaceId"] for s in surfaces] == ["ppa_clauses:doc-A", "ppa_comparison"]
        assert surfaces[1]["artifact"]["kind"] == "comparison"  # ordered by createdAt

    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_client_data_model_materialised_as_trailing_update(self, mock_get, mock_service_factory):
        """Stash-update hook (7.6 follow-up): a `clientDataModel` block persisted
        via POST /surface-data is replayed as ONE extra updateDataModel message
        AFTER the canonical tool-emitted messages, so a hard refresh rehydrates
        the client-edited state (e.g. an obligation what-if scenario)."""
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        mock_get.return_value = _make_session(owner_uid="owner-uid")

        edited = {"payload": {"doc_id": "doc-X"}, "scenario": {"deadlineDelta": {"COD": 21}}}
        state = {
            "a2ui_surface:obligation_analysis:doc-X": json.dumps(
                {
                    "surfaceId": "obligation_analysis:doc-X",
                    "messages": [
                        {"version": "v0.9", "createSurface": {"surfaceId": "obligation_analysis:doc-X"}},
                    ],
                    "artifact": {"kind": "obligation-analysis"},
                    "sourceId": "inv:map:1",
                    "toolName": "map_ppa_obligations",
                    "createdAt": 1000.0,
                    "clientDataModel": {"value": edited, "updatedAt": 2000.0},
                }
            ),
        }
        mock_session = SimpleNamespace(events=[], state=state)
        mock_service = AsyncMock()
        mock_service.get_session = AsyncMock(return_value=mock_session)
        mock_service_factory.return_value = mock_service

        client = _make_client("owner-uid")
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        (surface,) = resp.json()["a2ui_surfaces"]
        assert len(surface["messages"]) == 2
        trailing = surface["messages"][-1]
        assert trailing["updateDataModel"]["surfaceId"] == "obligation_analysis:doc-X"
        assert trailing["updateDataModel"]["value"] == edited

    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_no_a2ui_surfaces_for_legacy_session(self, mock_get, mock_service_factory):
        """A session with no stashed surfaces returns an empty list, not an error."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        mock_get.return_value = _make_session(owner_uid="owner-uid")
        mock_session = SimpleNamespace(events=[], state={"document_ids": ["doc-A"]})
        mock_service = AsyncMock()
        mock_service.get_session = AsyncMock(return_value=mock_session)
        mock_service_factory.return_value = mock_service

        client = _make_client("owner-uid")
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["a2ui_surfaces"] == []

    @patch("protocols.sessions_route.get_session_index")
    def test_bug_e_fix_private_session_still_403s_non_owner(self, mock_get):
        """Bug E security boundary: private sessions remain owner-only —
        the fix only relaxes access for sessions that are intentionally
        shared via the AccessControl model (public, domain, tagged,
        specific-allow). Private must stay private.
        """
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="private"))
        client = _make_client("bob")  # not owner, no shared access
        resp = client.get("/api/sessions/sess-1/messages")
        assert resp.status_code == 403, (
            f"Private sessions must stay owner-only after the Bug E fix; got {resp.status_code}: {resp.text[:200]}"
        )


# ---------------------------------------------------------------------------
# GET /api/sessions/{session_id}/activity — tool-call history persistence
# ---------------------------------------------------------------------------


def _part(fc=None, fr=None):
    return SimpleNamespace(function_call=fc, function_response=fr, text=None)


def _event(parts, ts):
    return SimpleNamespace(content=SimpleNamespace(parts=parts), timestamp=ts)


class TestEventsToToolActivity:
    def test_pairs_call_with_response_and_converts_ts_to_ms(self):
        from protocols.sessions_route import _events_to_tool_activity

        call = SimpleNamespace(name="ai_search", args={"query": "ppa"}, id="c1")
        resp = SimpleNamespace(id="c1", response={"result": "3 hits"})
        items = _events_to_tool_activity(
            [_event([_part(fc=call)], 1_700_000_000.0), _event([_part(fr=resp)], 1_700_000_001.0)]
        )
        assert len(items) == 1
        it = items[0]
        assert it.name == "ai_search"
        assert it.status == "success"
        assert it.ts == 1_700_000_000.0 * 1000  # seconds → ms
        assert '"query"' in (it.argsJson or "")
        assert "3 hits" in (it.resultContent or "")

    def test_call_without_response_is_error(self):
        from protocols.sessions_route import _events_to_tool_activity

        call = SimpleNamespace(name="broken_tool", args={}, id="c9")
        items = _events_to_tool_activity([_event([_part(fc=call)], 1_700_000_000.0)])
        assert len(items) == 1
        assert items[0].status == "error"
        assert items[0].resultContent is None

    def test_skips_text_only_events(self):
        from protocols.sessions_route import _events_to_tool_activity

        assert _events_to_tool_activity([_event([_part()], 1_700_000_000.0)]) == []


class TestMessageAgentAttribution:
    """_events_to_messages attributes each assistant message to its producing
    agent (e.author) via the author_map — 6.11 resume per-delegate marks."""

    @staticmethod
    def _msg_event(author, text, ts):
        return SimpleNamespace(author=author, content=SimpleNamespace(parts=[SimpleNamespace(text=text)]), timestamp=ts)

    def test_attributes_assistant_messages_by_author(self):
        from protocols.sessions_route import _events_to_messages

        amap = {"s_ppa": {"avatar": "/ppa.svg", "label": "Contract Expert"}}
        events = [
            self._msg_event("user", "hi", 1.0),
            self._msg_event("s_ppa", "the answer", 2.0),
            self._msg_event("root_skill", "root reply", 3.0),
        ]
        msgs = _events_to_messages(events, amap)
        assert (msgs[0].avatar, msgs[0].agent_label) == (None, None)  # user
        assert (msgs[1].avatar, msgs[1].agent_label) == ("/ppa.svg", "Contract Expert")  # delegate
        assert (msgs[2].avatar, msgs[2].agent_label) == (None, None)  # unmapped author → root

    def test_no_author_map_leaves_attribution_none(self):
        from protocols.sessions_route import _events_to_messages

        msgs = _events_to_messages([self._msg_event("s_ppa", "hi", 1.0)])
        assert msgs[0].avatar is None and msgs[0].agent_label is None

    def test_tags_notability_tier(self):
        """Reconstructed tool calls carry a notability tier (6.11). An unmapped
        tool defaults to 'internal'; a registered artifact tool → 'artifact'."""
        from adk import a2ui_result_render as rr
        from protocols.sessions_route import _events_to_tool_activity

        saved = rr._registry[:]
        try:
            rr.register(
                lambda r, tc=None: [{"version": "v0.9", "createSurface": {"surfaceId": "workspace", "catalogId": "c"}}],
                tool_names=["arty_tool"],
                name="arty",
                artifact_meta=lambda r: {"kind": "k"},
            )
            plain = SimpleNamespace(name="unmapped_tool", args={}, id="c1")
            arty = SimpleNamespace(name="arty_tool", args={}, id="c2")
            items = _events_to_tool_activity(
                [
                    _event([_part(fc=plain), _part(fc=arty)], 1_700_000_000.0),
                    _event([_part(fr=SimpleNamespace(id="c1", response={}))], 1_700_000_001.0),
                    _event([_part(fr=SimpleNamespace(id="c2", response={}))], 1_700_000_001.0),
                ]
            )
            tiers = {it.name: it.notability for it in items}
            assert tiers["unmapped_tool"] == "internal"
            assert tiers["arty_tool"] == "artifact"
        finally:
            rr._registry[:] = saved


class TestGetSessionActivity:
    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_owner_gets_tool_history(self, mock_get, mock_service):
        mock_get.return_value = _make_session(owner_uid="viewer")
        call = SimpleNamespace(name="ai_search", args={"q": "x"}, id="c1")
        resp = SimpleNamespace(id="c1", response={"result": "ok"})
        session = SimpleNamespace(
            events=[_event([_part(fc=call)], 1_700_000_000.0), _event([_part(fr=resp)], 1_700_000_001.0)]
        )
        svc = AsyncMock()
        svc.get_session = AsyncMock(return_value=session)
        mock_service.return_value = svc

        resp_http = _make_client("viewer").get("/api/sessions/sess-1/activity")
        assert resp_http.status_code == 200
        body = resp_http.json()
        assert body["session_id"] == "sess-1"
        assert len(body["tool_calls"]) == 1
        assert body["tool_calls"][0]["name"] == "ai_search"
        assert body["tool_calls"][0]["status"] == "success"

    @patch("protocols.sessions_route.get_session_index")
    def test_no_access_returns_403(self, mock_get):
        mock_get.return_value = _make_session(owner_uid="alice", ac=AccessControl(type="private"))
        resp_http = _make_client("bob").get("/api/sessions/sess-1/activity")
        assert resp_http.status_code == 403


class TestEventsToDelegations:
    def test_transfer_to_agent_is_auto_delegation_not_a_tool(self):
        from protocols.sessions_route import _events_to_delegations, _events_to_tool_activity

        fc = SimpleNamespace(name="transfer_to_agent", args={"agent_name": "one_ppa_expert"}, id="t1")
        events = [_event([_part(fc=fc)], 1_700_000_000.0)]
        delegs = _events_to_delegations(events)
        assert len(delegs) == 1
        assert delegs[0].mode == "auto"
        assert delegs[0].target == "one_ppa_expert"
        assert delegs[0].targetDisplay == "One ppa expert"
        # And it must NOT show up as a tool call.
        assert _events_to_tool_activity(events) == []

    def test_request_handoff_is_suggest(self):
        from protocols.sessions_route import _events_to_delegations

        fc = SimpleNamespace(name="request_handoff", args={"target_skill_id": "abc-123"}, id="p1")
        delegs = _events_to_delegations([_event([_part(fc=fc)], 1_700_000_000.0)])
        assert len(delegs) == 1
        assert delegs[0].mode == "suggest"
        assert delegs[0].target == "abc-123"

    def test_earliest_event_ts_is_session_start(self):
        from protocols.sessions_route import _earliest_event_ts

        events = [_event([_part()], 1_700_000_005.0), _event([_part()], 1_700_000_001.0)]
        assert _earliest_event_ts(events) == 1_700_000_001.0 * 1000
        assert _earliest_event_ts([]) is None


class TestGetSessionActivityDelegations:
    @patch("protocols.sessions_route.get_messages_session_service")
    @patch("protocols.sessions_route.get_session_index")
    def test_activity_includes_delegations_and_start_ts(self, mock_get, mock_service):
        mock_get.return_value = _make_session(owner_uid="viewer")
        fc = SimpleNamespace(name="transfer_to_agent", args={"agent_name": "specialist"}, id="t1")
        session = SimpleNamespace(events=[_event([_part(fc=fc)], 1_700_000_000.0)])
        svc = AsyncMock()
        svc.get_session = AsyncMock(return_value=session)
        mock_service.return_value = svc

        body = _make_client("viewer").get("/api/sessions/sess-1/activity").json()
        assert len(body["delegations"]) == 1
        assert body["delegations"][0]["mode"] == "auto"
        assert body["session_start_ts"] == 1_700_000_000.0 * 1000
