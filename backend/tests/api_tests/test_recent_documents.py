"""GET /api/documents/recent — the caller's documents, most recently worked with first.

Replaces a skill's shared demo documents in the workbench picker, so the
properties that matter are: ranking by real use (conversations), documents not
yet discussed still appear, and nothing the caller doesn't own ever leaks in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from db.models.chat_session import ChatSessionIndex

_USER = User(uid="user_a", email="alice@example.com", domain="example.com")


def _session(sid: str, docs: list[str], at: datetime, *, title: str | None = None, provisional: bool = False):
    return ChatSessionIndex.model_validate(
        {
            "sessionId": sid,
            "documentIds": docs,
            "skillId": "skill-1",
            "ownerUid": "user_a",
            "title": title,
            "firstMessageAt": at,
            "lastMessageAt": at,
            "provisional": provisional,
            "accessControl": {"type": "private"},
        }
    )


def _doc(doc_id: str, *, owner: str = "user_a", created: str = "2026-10-01T00:00:00+00:00", status: str = "parsed"):
    return {
        "id": doc_id,
        "userId": owner,
        "originalFilename": f"{doc_id}.pdf",
        "sourceFormat": "pdf",
        "parseStatus": status,
        "folderId": "f1",
        "createdAt": created,
        "updatedAt": created,
        "blocks": [{"type": "paragraph", "text": "confidential"}],
    }


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


def _get(client: TestClient, *, sessions, docs: dict, library: list, limit: int | None = None):
    with (
        patch("tools.documents.recent.most_recent_session_for_user", return_value=sessions) as m_sessions,
        patch("tools.documents.recent.get_document", side_effect=lambda _c, i: docs.get(i)),
        patch("tools.documents.recent.list_documents_for_user", return_value=library),
    ):
        resp = client.get("/api/documents/recent" + (f"?limit={limit}" if limit else ""))
    return resp, m_sessions


def test_recent_is_not_captured_as_a_document_id(client: TestClient):
    resp, m_sessions = _get(client, sessions=[], docs={}, library=[])
    assert resp.status_code == 200
    assert resp.json() == {"documents": []}
    assert m_sessions.call_args.args[0] == "user_a"


def test_ranks_by_latest_conversation_then_library_arrival(client: TestClient):
    sessions = [
        _session("s-new", ["doc-b"], datetime(2026, 10, 19, tzinfo=UTC), title="Compare EDP and LEAP"),
        _session("s-old", ["doc-a", "doc-b"], datetime(2026, 10, 10, tzinfo=UTC)),
    ]
    docs = {"doc-a": _doc("doc-a"), "doc-b": _doc("doc-b")}
    # doc-c arrived after s-old but was never discussed.
    library = [
        {**_doc("doc-c", created="2026-10-15T00:00:00+00:00"), "__id": "doc-c"},
        {**docs["doc-a"], "__id": "doc-a"},
    ]

    resp, _ = _get(client, sessions=sessions, docs=docs, library=library)

    rows = resp.json()["documents"]
    assert [r["id"] for r in rows] == ["doc-b", "doc-c", "doc-a"]
    assert rows[0]["lastSessionId"] == "s-new"
    assert rows[0]["lastSessionTitle"] == "Compare EDP and LEAP"
    assert rows[1]["lastSessionId"] is None


def test_drops_documents_the_caller_does_not_own(client: TestClient):
    # A shared thread can name someone else's document.
    sessions = [_session("s1", ["mine", "theirs"], datetime(2026, 10, 19, tzinfo=UTC))]
    docs = {"mine": _doc("mine"), "theirs": _doc("theirs", owner="user_b")}

    resp, _ = _get(client, sessions=sessions, docs=docs, library=[])

    assert [r["id"] for r in resp.json()["documents"]] == ["mine"]


def test_skips_deleted_failed_and_provisional(client: TestClient):
    sessions = [
        _session("s-prov", ["from-provisional"], datetime(2026, 10, 20, tzinfo=UTC), provisional=True),
        _session("s1", ["gone", "broken", "ok"], datetime(2026, 10, 19, tzinfo=UTC)),
    ]
    docs = {
        "from-provisional": _doc("from-provisional"),
        "broken": _doc("broken", status="failed"),
        "ok": _doc("ok"),
    }

    resp, _ = _get(client, sessions=sessions, docs=docs, library=[])

    assert [r["id"] for r in resp.json()["documents"]] == ["ok"]


def test_listing_never_carries_parsed_blocks(client: TestClient):
    sessions = [_session("s1", ["doc-a"], datetime(2026, 10, 19, tzinfo=UTC))]
    resp, _ = _get(client, sessions=sessions, docs={"doc-a": _doc("doc-a")}, library=[])

    row = resp.json()["documents"][0]
    assert "blocks" not in row
    assert row["originalFilename"] == "doc-a.pdf"


def test_limit_is_respected(client: TestClient):
    sessions = [_session(f"s{i}", [f"d{i}"], datetime(2026, 10, i + 1, tzinfo=UTC)) for i in range(10)]
    docs = {f"d{i}": _doc(f"d{i}") for i in range(10)}

    resp, _ = _get(
        client, sessions=sorted(sessions, key=lambda s: s.last_message_at, reverse=True), docs=docs, library=[], limit=3
    )

    assert [r["id"] for r in resp.json()["documents"]] == ["d9", "d8", "d7"]
