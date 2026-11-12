"""/api/projects, /api/me/instructions and PATCH /api/sessions project_id.

Owner-only throughout: another user's project id is a 404 on every route (never
a 403 — ids cannot be probed), and errors use the ``{code, message}`` shape.
Runs against the in-memory Firestore so the real query paths execute.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.access_context import AccessContext
from db.firestore import get_document


@pytest.fixture()
def store(monkeypatch):
    import db.firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    client = InMemoryFirestoreClient()
    monkeypatch.setattr(fs, "_client", client)
    # Indexing has its own integration tests; no worker may outlive this store.
    monkeypatch.setattr("extensions.document_event", lambda *_args, **_kwargs: None)
    return client


def _client(uid: str) -> TestClient:
    from projects.routes import router
    from protocols.sessions_route import router as sessions_router

    user = User(uid=uid, email=f"{uid}@example.com", domain="example.com")
    app = FastAPI()
    app.include_router(router)
    app.include_router(sessions_router)

    @app.middleware("http")
    async def _inject_access(request, call_next):
        request.state.access = AccessContext(uid=uid, email=user.email, domain=user.domain)
        return await call_next(request)

    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _doc(store, doc_id: str, *, owner: str = "alice", folder: str = "f1"):
    store.collection("parsed_documents").document(doc_id).set(
        {
            "userId": owner,
            "folderId": folder,
            "originalFilename": f"{doc_id}.pdf",
            "status": "parsed",
            "parseStatus": "parsed",
            "createdAt": "2026-10-01T00:00:00+00:00",
            "blocks": [{"type": "paragraph", "text": "confidential"}],
        }
    )


def _session(store, sid: str, docs: list[str], owner: str = "alice"):
    at = datetime(2026, 10, 20, tzinfo=UTC).isoformat()
    store.collection("chat_sessions").document(sid).set(
        {
            "sessionId": sid,
            "skillId": "skill-1",
            "ownerUid": owner,
            "documentIds": docs,
            "accessControl": {"type": "private"},
            "firstMessageAt": at,
            "lastMessageAt": at,
            "archivedAt": None,
            "title": "EDP pricing questions",
        }
    )


def test_create_list_get(store):
    alice = _client("alice")
    resp = alice.post("/api/projects", json={"name": "Iberia review", "instructions": "Answer in Spanish."})
    assert resp.status_code == 201
    pid = resp.json()["id"]
    listed = alice.get("/api/projects").json()["projects"]
    assert [(p["name"], p["docCount"], p["chatCount"]) for p in listed] == [("Iberia review", 0, 0)]
    got = alice.get(f"/api/projects/{pid}").json()
    assert got["instructions"] == "Answer in Spanish."


def test_duplicate_name_is_409_with_code(store):
    alice = _client("alice")
    alice.post("/api/projects", json={"name": "Iberia"})
    resp = alice.post("/api/projects", json={"name": "iberia"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PROJECT_EXISTS"
    assert alice.post("/api/projects", json={"name": "Iberia", "uniqueName": True}).json()["name"] == "Iberia (2)"


def test_blank_name_is_422(store):
    resp = _client("alice").post("/api/projects", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "PROJECT_NAME_INVALID"


def test_another_users_project_is_404_everywhere(store):
    pid = _client("alice").post("/api/projects", json={"name": "Mine"}).json()["id"]
    bob = _client("bob")
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert bob.patch(f"/api/projects/{pid}", json={"name": "x"}).status_code == 404
    assert bob.delete(f"/api/projects/{pid}").status_code == 404
    assert bob.post(f"/api/projects/{pid}/documents", json={"doc_ids": ["d"]}).status_code == 404
    assert bob.get(f"/api/projects/{pid}/documents").status_code == 404
    assert bob.get(f"/api/projects/{pid}/sessions").status_code == 404
    assert bob.delete(f"/api/projects/{pid}/documents/d").status_code == 404


def test_add_documents_reports_each(store):
    _doc(store, "mine")
    _doc(store, "theirs", owner="bob")
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    resp = alice.post(f"/api/projects/{pid}/documents", json={"doc_ids": ["mine", "theirs", "gone"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == ["mine"] and body["already"] == []
    assert {f["id"] for f in body["failed"]} == {"theirs", "gone"}
    docs = alice.get(f"/api/projects/{pid}/documents").json()
    assert [d["id"] for d in docs["documents"]] == ["mine"]
    assert "blocks" not in docs["documents"][0]


def test_too_many_documents_is_422(store):
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    resp = alice.post(f"/api/projects/{pid}/documents", json={"doc_ids": [f"d{i}" for i in range(101)]})
    assert resp.status_code == 422


def test_from_folder_and_from_session(store):
    store.collection("users").document("alice").collection("folders").document("f1").set(
        {"id": "f1", "name": "PPAs", "userId": "alice"}
    )
    _doc(store, "edp", folder="f1")
    _doc(store, "leap", folder="f2")
    _session(store, "s1", ["leap"])
    alice = _client("alice")
    from_folder = alice.post("/api/projects", json={"name": "PPAs", "fromFolderId": "f1"}).json()
    assert from_folder["documents"]["added"] == ["edp"]
    from_chat = alice.post("/api/projects", json={"name": "Chat", "fromSessionId": "s1"}).json()
    assert from_chat["documents"]["added"] == ["leap"] and from_chat["chatAdded"] is True
    assert get_document("chat_sessions", "s1")["projectId"] == from_chat["id"]
    # Someone else's chat or folder is not found.
    bob = _client("bob")
    assert bob.post("/api/projects", json={"name": "X", "fromSessionId": "s1"}).status_code == 404
    assert bob.post("/api/projects", json={"name": "Y", "fromFolderId": "f1"}).status_code == 404


def test_sessions_route_marks_membership_and_linking_document(store):
    _doc(store, "edp")
    _session(store, "s1", ["edp"])
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia", "docIds": ["edp"]}).json()["id"]
    rows = alice.get(f"/api/projects/{pid}/sessions").json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"
    assert rows[0]["membership"] == "derived"
    assert rows[0]["linkedDocument"] == {"id": "edp", "filename": "edp.pdf"}


def test_remove_document(store):
    _doc(store, "edp")
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia", "docIds": ["edp"]}).json()["id"]
    assert alice.delete(f"/api/projects/{pid}/documents/edp").status_code == 204
    assert alice.delete(f"/api/projects/{pid}/documents/edp").status_code == 404
    assert get_document("parsed_documents", "edp") is not None


def test_patch_and_clear_default_skill(store):
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    assert alice.patch(f"/api/projects/{pid}", json={"defaultSkillId": "demo-ppa-expert"}).json()["defaultSkillId"]
    renamed = alice.patch(f"/api/projects/{pid}", json={"name": "Iberia 2026"}).json()
    assert renamed["name"] == "Iberia 2026" and renamed["defaultSkillId"] == "demo-ppa-expert"
    assert alice.patch(f"/api/projects/{pid}", json={"defaultSkillId": None}).json()["defaultSkillId"] is None


def test_delete_keeps_documents(store):
    _doc(store, "edp")
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia", "docIds": ["edp"]}).json()["id"]
    assert alice.delete(f"/api/projects/{pid}").status_code == 204
    assert alice.get(f"/api/projects/{pid}").status_code == 404
    assert get_document("parsed_documents", "edp")["projectIds"] == []


def test_patch_session_project(store):
    _session(store, "s1", [])
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    resp = alice.patch("/api/sessions/s1", json={"project_id": pid})
    assert resp.status_code == 200
    assert resp.json()["session"]["project_id"] == pid
    # A title-only PATCH leaves it alone; null clears it.
    alice.patch("/api/sessions/s1", json={"title": "Renamed"})
    assert get_document("chat_sessions", "s1")["projectId"] == pid
    alice.patch("/api/sessions/s1", json={"project_id": None})
    assert get_document("chat_sessions", "s1")["projectId"] is None
    # Another user's project is not found.
    bob_pid = _client("bob").post("/api/projects", json={"name": "Bob's"}).json()["id"]
    assert alice.patch("/api/sessions/s1", json={"project_id": bob_pid}).status_code == 404


def test_my_instructions(store):
    alice = _client("alice")
    assert alice.get("/api/me/instructions").json() == {"instructions": ""}
    assert alice.put("/api/me/instructions", json={"instructions": " Spanish please "}).json() == {
        "instructions": "Spanish please"
    }
    assert alice.get("/api/me/instructions").json() == {"instructions": "Spanish please"}
    assert _client("bob").get("/api/me/instructions").json() == {"instructions": ""}
    resp = alice.put("/api/me/instructions", json={"instructions": "x" * 4001})
    assert resp.status_code == 422


def test_get_project_carries_search_status_and_queues_a_backfill(store, monkeypatch):
    """D4: opening a project asks the search index to catch up (in the background)
    and reports how far it has got. Both come through extensions, so the public
    template simply has no ``search``."""
    import extensions

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(extensions, "document_event", lambda e, p: events.append((e, dict(p))))
    monkeypatch.setattr(
        extensions,
        "project_search_status",
        lambda uid, pid: {"indexed": 2, "failed": 0, "total": 3, "status": "indexing"},
    )
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    got = alice.get(f"/api/projects/{pid}").json()
    assert got["search"] == {"indexed": 2, "failed": 0, "total": 3, "status": "indexing"}
    assert ("project_opened", {"uid": "alice", "project_id": pid}) in events

    monkeypatch.setattr(extensions, "project_search_status", lambda uid, pid: None)
    assert alice.get(f"/api/projects/{pid}").json()["search"] is None


def test_add_whole_folder_includes_subfolders(store):
    import db.folders as folders_db

    root = folders_db.create_folder("alice", "client")["id"]
    sub = folders_db.create_folder("alice", "client/sessions/02")["id"]
    other = folders_db.create_folder("alice", "client-archive")["id"]  # a prefix, not a subfolder
    _doc(store, "top", folder=root)
    _doc(store, "deep", folder=sub)
    _doc(store, "unrelated", folder=other)
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Client"}).json()["id"]

    body = alice.post(f"/api/projects/{pid}/documents", json={"folder_id": root}).json()
    assert sorted(body["added"]) == ["deep", "top"]
    # Running it again (after an aborted upload, say) only reports what's there.
    again = alice.post(f"/api/projects/{pid}/documents", json={"folder_id": root}).json()
    assert again["added"] == [] and sorted(again["already"]) == ["deep", "top"]


def test_add_folder_without_subfolders(store):
    import db.folders as folders_db

    root = folders_db.create_folder("alice", "client")["id"]
    sub = folders_db.create_folder("alice", "client/deep")["id"]
    _doc(store, "top", folder=root)
    _doc(store, "deep", folder=sub)
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Client"}).json()["id"]
    body = alice.post(f"/api/projects/{pid}/documents", json={"folder_id": root, "include_subfolders": False}).json()
    assert body["added"] == ["top"]


def test_add_another_users_folder_is_404(store):
    import db.folders as folders_db

    theirs = folders_db.create_folder("bob", "private")["id"]
    _doc(store, "secret", owner="bob", folder=theirs)
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Client"}).json()["id"]
    resp = alice.post(f"/api/projects/{pid}/documents", json={"folder_id": theirs})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FOLDER_NOT_FOUND"


def test_project_queries_never_stream_parsed_blocks(store, monkeypatch):
    """Ids and listings use a field projection: streaming whole records (blocks
    up to ~700 KB each) made every project page take 8-11 s at 349 documents."""
    from db import firestore_inmemory

    projections: list[tuple[str, list[str] | None]] = []
    real_stream = firestore_inmemory._Query.stream

    def spy(self):
        projections.append((self._collection, self._select))
        return real_stream(self)

    monkeypatch.setattr(firestore_inmemory._Query, "stream", spy)
    _doc(store, "d1")
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    alice.post(f"/api/projects/{pid}/documents", json={"doc_ids": ["d1"]})
    projections.clear()
    alice.get(f"/api/projects/{pid}")
    alice.get(f"/api/projects/{pid}/documents")
    alice.get(f"/api/projects/{pid}/sessions")
    doc_queries = [sel for col, sel in projections if col == "parsed_documents"]
    assert doc_queries, "expected the project routes to query parsed_documents"
    assert all(sel is not None and "blocks" not in sel for sel in doc_queries), doc_queries


# --- attached knowledge bases (github-sources.md D9) -------------------------------


def test_attaching_needs_knowledge_bases_in_the_deployment(store, monkeypatch):
    import extensions

    monkeypatch.setattr(extensions, "attachable_knowledge_bases", lambda access: None)
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    resp = alice.patch(f"/api/projects/{pid}", json={"attachedKbIds": ["method"]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "KNOWLEDGE_BASES_UNAVAILABLE"


def test_only_searchable_knowledge_bases_can_be_attached(store, monkeypatch):
    import extensions

    monkeypatch.setattr(extensions, "attachable_knowledge_bases", lambda access: [{"id": "method", "name": "Method"}])
    alice = _client("alice")
    pid = alice.post("/api/projects", json={"name": "Iberia"}).json()["id"]
    refused = alice.patch(f"/api/projects/{pid}", json={"attachedKbIds": ["method", "secret"]})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "KNOWLEDGE_BASE_NOT_ATTACHABLE"
    assert alice.get(f"/api/projects/{pid}").json()["attachedKbIds"] == []

    ok = alice.patch(f"/api/projects/{pid}", json={"attachedKbIds": ["method", "method"]})
    assert ok.status_code == 200 and ok.json()["attachedKbIds"] == ["method"]
    # Other edits leave the attachments alone; an empty list detaches.
    assert alice.patch(f"/api/projects/{pid}", json={"name": "Iberia 2"}).json()["attachedKbIds"] == ["method"]
    assert alice.patch(f"/api/projects/{pid}", json={"attachedKbIds": []}).json()["attachedKbIds"] == []
    assert _client("bob").patch(f"/api/projects/{pid}", json={"attachedKbIds": []}).status_code == 404
