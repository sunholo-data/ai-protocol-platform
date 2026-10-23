"""Document-folder routes under LOCAL_MODE (in-memory Firestore, no mocks).

`GET /api/folders` 500'd in LOCAL_MODE with ``'_DocumentRef' object has no
attribute 'collection'``: folders live in the subcollection
``users/{uid}/folders`` and the in-memory stand-in had no subcollections.
These tests drive every folder route end-to-end against the real
``InMemoryFirestoreClient`` — nothing in ``db.folders`` is patched — so any
other Firestore feature the folder code path needs but the stand-in lacks
fails here, not on a laptop.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user
from auth.local_mode_stub import build_workshop_user

_OTHER = User(uid="someone-else", email="else@local", domain="local")


@pytest.fixture()
def local_store(monkeypatch):
    import db.firestore as fs

    monkeypatch.setenv("LOCAL_MODE", "1")
    fs._reset_client_for_testing()
    client = fs.get_client()
    from db.firestore_inmemory import InMemoryFirestoreClient

    assert isinstance(client, InMemoryFirestoreClient)
    yield client
    fs._reset_client_for_testing()


@pytest.fixture()
def user() -> User:
    return build_workshop_user()


def _app(as_user: User) -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: as_user
    return TestClient(app)


@pytest.fixture()
def api(local_store, user) -> TestClient:
    return _app(user)


def test_list_folders_empty_is_200_not_500(api):
    resp = api.get("/api/folders")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"folders": []}


def test_create_then_list(api):
    created = api.post("/api/folders", json={"name": "Contracts"})
    assert created.status_code == 201, created.text
    folder_id = created.json()["id"]

    listed = api.get("/api/folders").json()["folders"]
    assert [(f["id"], f["name"]) for f in listed] == [(folder_id, "Contracts")]


def test_folders_are_per_user(api, local_store):
    api.post("/api/folders", json={"name": "Mine"})
    assert _app(_OTHER).get("/api/folders").json() == {"folders": []}


def test_rename_and_duplicate_name(api):
    a = api.post("/api/folders", json={"name": "A"}).json()["id"]
    api.post("/api/folders", json={"name": "B"})

    resp = api.patch(f"/api/folders/{a}", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    assert {f["name"] for f in api.get("/api/folders").json()["folders"]} == {"Renamed", "B"}

    clash = api.patch(f"/api/folders/{a}", json={"name": "b"})
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "FOLDER_EXISTS"


def test_unknown_folder_is_404_everywhere(api):
    assert api.patch("/api/folders/nope", json={"name": "x"}).status_code == 404
    assert api.delete("/api/folders/nope").status_code == 404
    assert api.get("/api/folders/nope/documents").status_code == 404


def _seed_doc(local_store, user: User, folder_id: str, doc_id: str = "doc-1", filename: str = "a.pdf") -> None:
    # No storagePath: the move route then has no GCS object to relocate, so the
    # test stays on Firestore — which is what LOCAL_MODE stands in for.
    local_store.collection("parsed_documents").document(doc_id).set(
        {
            "userId": user.uid,
            "folderId": folder_id,
            "originalFilename": filename,
            "storagePath": "",
            "parseStatus": "parsed",
            "createdAt": "2026-10-19T00:00:00+00:00",
        }
    )
    local_store.collection("users").document(user.uid).collection("folders").document(folder_id).update(
        {"docCount": 1, "parsedCount": 1}
    )


def test_folder_documents_move_and_delete(api, local_store, user):
    src = api.post("/api/folders", json={"name": "Inbox"}).json()["id"]
    dst = api.post("/api/folders", json={"name": "Archive"}).json()["id"]
    _seed_doc(local_store, user, src)

    listed = api.get(f"/api/folders/{src}/documents")
    assert listed.status_code == 200, listed.text
    assert [d["id"] for d in listed.json()["documents"]] == ["doc-1"]

    # A folder with documents cannot be deleted.
    refused = api.delete(f"/api/folders/{src}")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "FOLDER_NOT_EMPTY"

    moved = api.patch("/api/documents/doc-1", json={"folder_id": dst})
    assert moved.status_code == 200, moved.text
    assert moved.json()["folderId"] == dst
    assert api.get(f"/api/folders/{src}/documents").json()["documents"] == []
    assert [d["id"] for d in api.get(f"/api/folders/{dst}/documents").json()["documents"]] == ["doc-1"]

    counts = {f["name"]: f["docCount"] for f in api.get("/api/folders").json()["folders"]}
    assert counts == {"Inbox": 0, "Archive": 1}

    # Now empty: delete succeeds and the folder is gone.
    assert api.delete(f"/api/folders/{src}").status_code == 204
    assert [f["name"] for f in api.get("/api/folders").json()["folders"]] == ["Archive"]


def test_move_to_unknown_folder_is_404_and_leaves_document(api, local_store, user):
    src = api.post("/api/folders", json={"name": "Inbox"}).json()["id"]
    _seed_doc(local_store, user, src)

    resp = api.patch("/api/documents/doc-1", json={"folder_id": "does-not-exist"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FOLDER_NOT_FOUND"
    doc = local_store.collection("parsed_documents").document("doc-1").get().to_dict()
    assert doc["folderId"] == src


def test_move_to_another_users_folder_is_404(api, local_store, user):
    src = api.post("/api/folders", json={"name": "Inbox"}).json()["id"]
    theirs = _app(_OTHER).post("/api/folders", json={"name": "Theirs"}).json()["id"]
    _seed_doc(local_store, user, src)

    resp = api.patch("/api/documents/doc-1", json={"folder_id": theirs})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "FOLDER_NOT_FOUND"
