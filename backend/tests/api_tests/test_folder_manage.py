"""Folder rename / delete-if-empty and document move (Mark, 2026-10-02)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user

OWNER = User(uid="mark", email="mark@example.com", domain="example.com")
R = "tools.documents.routes"


@pytest.fixture()
def client():
    import fast_api_app as module

    async def _override(request: Request) -> User:
        request.state.access = build_access_context(OWNER)
        return OWNER

    module.app.dependency_overrides[get_current_user] = _override
    yield TestClient(module.app)
    module.app.dependency_overrides.pop(get_current_user, None)


def _doc(**kw):
    base = {
        "userId": "mark",
        "folderId": "src",
        "originalFilename": "a.pdf",
        "storagePath": "users/mark/docs/src/a.pdf",
        "parseStatus": "parsed",
    }
    return {**base, **kw}


def test_delete_empty_folder(client):
    with (
        patch(f"{R}.folders_db.get_folder", return_value={"id": "f1", "name": "Old", "userId": "mark"}),
        patch(f"{R}.folders_db.count_folder_documents", return_value=0),
        patch(f"{R}.folders_db.delete_folder") as delete,
    ):
        resp = client.delete("/api/folders/f1")
    assert resp.status_code == 204
    delete.assert_called_once_with("mark", "f1")


def test_folder_with_documents_is_refused(client):
    with (
        patch(f"{R}.folders_db.get_folder", return_value={"id": "f1", "name": "llm", "userId": "mark"}),
        patch(f"{R}.folders_db.count_folder_documents", return_value=11),
        patch(f"{R}.folders_db.delete_folder") as delete,
    ):
        resp = client.delete("/api/folders/f1")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "FOLDER_NOT_EMPTY" and detail["count"] == 11
    assert "11 documents" in detail["message"]
    delete.assert_not_called()


def test_unknown_folder_is_404(client):
    with patch(f"{R}.folders_db.get_folder", return_value=None):
        assert client.delete("/api/folders/nope").status_code == 404
        assert client.patch("/api/folders/nope", json={"name": "x"}).status_code == 404


def test_rename_and_duplicate_name(client):
    folder = {"id": "f1", "name": "Old", "userId": "mark"}
    with (
        patch(f"{R}.folders_db.get_folder", return_value=folder),
        patch(f"{R}.folders_db.find_folder_by_name", return_value=None),
        patch(f"{R}.folders_db.rename_folder") as rename,
    ):
        resp = client.patch("/api/folders/f1", json={"name": "  Papers "})
    assert resp.status_code == 200 and resp.json()["name"] == "Papers"
    rename.assert_called_once_with("mark", "f1", "Papers")
    with (
        patch(f"{R}.folders_db.get_folder", return_value=folder),
        patch(f"{R}.folders_db.find_folder_by_name", return_value={"id": "f2"}),
    ):
        resp = client.patch("/api/folders/f1", json={"name": "papers"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "FOLDER_EXISTS"


def _gcs():
    bucket = MagicMock()
    gcs_client = MagicMock()
    gcs_client.bucket.return_value = bucket
    return gcs_client, bucket


def test_move_moves_object_record_and_counts(client):
    gcs_client, bucket = _gcs()
    with (
        patch(f"{R}._get_firestore_doc", return_value=_doc()),
        patch(f"{R}.folders_db.get_folder", return_value={"id": "dst", "name": "Papers"}),
        patch(f"{R}.folders_db.list_folder_documents", return_value=[]),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=gcs_client),
        patch("db.firestore.update_document") as update,
        patch(f"{R}.folders_db.update_folder_counts") as counts,
    ):
        resp = client.patch("/api/documents/d1", json={"folder_id": "dst"})
    assert resp.status_code == 200, resp.text
    assert bucket.copy_blob.call_args.args[2] == "users/mark/docs/dst/a.pdf"
    bucket.blob.return_value.delete.assert_called_once()
    fields = update.call_args.args[2]
    assert fields["folderId"] == "dst" and fields["sourceUrl"] == "gs://bkt/users/mark/docs/dst/a.pdf"
    assert {c.args[1]: c.kwargs for c in counts.call_args_list} == {
        "src": {"doc_delta": -1, "parsed_delta": -1},
        "dst": {"doc_delta": 1, "parsed_delta": 1},
    }


def test_move_name_clash_asks_then_replaces(client):
    gcs_client, _ = _gcs()
    other = _doc(folderId="dst", storagePath="users/mark/docs/dst/a.pdf", id="d9")
    common = [
        patch(f"{R}._get_firestore_doc", return_value=_doc()),
        patch(f"{R}.folders_db.get_folder", return_value={"id": "dst", "name": "Papers"}),
        patch(f"{R}.folders_db.list_folder_documents", return_value=[other]),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=gcs_client),
        patch("db.firestore.update_document"),
        patch(f"{R}.folders_db.update_folder_counts"),
    ]
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in common:
            stack.enter_context(p)
        deleted = stack.enter_context(patch(f"{R}._delete_firestore_doc"))
        resp = client.patch("/api/documents/d1", json={"folder_id": "dst"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "FILENAME_EXISTS"
        deleted.assert_not_called()
        resp = client.patch("/api/documents/d1", json={"folder_id": "dst", "replace": True})
        assert resp.status_code == 200
        deleted.assert_called_once()
        assert deleted.call_args.args[1] == "d9"


def test_cannot_move_someone_elses_document(client):
    with patch(f"{R}._get_firestore_doc", return_value=_doc(userId="eve")):
        assert client.patch("/api/documents/d1", json={"folder_id": "dst"}).status_code == 403


def test_delete_document_decrements_counts(client):
    with (
        patch(f"{R}._get_firestore_doc", return_value=_doc()),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=_gcs()[0]),
        patch(f"{R}._delete_firestore_doc"),
        patch(f"{R}.folders_db.update_folder_counts") as counts,
    ):
        assert client.delete("/api/documents/d1").status_code == 204
    counts.assert_called_once_with("mark", "src", doc_delta=-1, parsed_delta=-1)


@pytest.mark.parametrize(
    ("storage_path", "deletes_object"),
    [
        ("users/mark/docs/src/a.pdf", True),  # the caller's own upload
        ("users/mark/docs/channel/d1-a.pdf", True),  # a channel attachment
        ("example-datastore/PPAs/shared contract.pdf", False),  # imported by reference: a shared source
        ("users/someone-else/docs/x/a.pdf", False),  # never another user's file
    ],
)
def test_delete_removes_only_the_callers_own_stored_file(client, storage_path, deletes_object):
    """Removing an imported-by-reference document from My Documents must never
    delete its SOURCE object, which can sit in the same bucket (a shared library)."""
    gcs_client, bucket = _gcs()
    with (
        patch(f"{R}._get_firestore_doc", return_value=_doc(storagePath=storage_path, folderId=None)),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=gcs_client),
        patch(f"{R}._delete_firestore_doc") as deleted,
        patch(f"{R}.folders_db.update_folder_counts"),
    ):
        assert client.delete("/api/documents/d1").status_code == 204
    deleted.assert_called_once()  # the record always goes
    if deletes_object:
        bucket.blob.assert_called_once_with(storage_path)
        bucket.blob.return_value.delete.assert_called_once()
    else:
        bucket.blob.assert_not_called()


# --- extensions.document_event: what a searchable folder relies on ------------


def test_move_and_delete_tell_extensions(client):
    gcs_client, _ = _gcs()
    with (
        patch(f"{R}._get_firestore_doc", return_value=_doc()),
        patch(f"{R}.folders_db.get_folder", return_value={"id": "dst", "name": "Papers"}),
        patch(f"{R}.folders_db.list_folder_documents", return_value=[]),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=gcs_client),
        patch("db.firestore.update_document"),
        patch(f"{R}._delete_firestore_doc"),
        patch(f"{R}.folders_db.update_folder_counts"),
        patch(f"{R}.document_event") as event,
    ):
        client.patch("/api/documents/d1", json={"folder_id": "dst"})
        client.delete("/api/documents/d1")
    assert [c.args for c in event.call_args_list] == [
        ("moved", {"uid": "mark", "doc_id": "d1", "from_folder_id": "src", "folder_id": "dst"}),
        ("deleted", {"uid": "mark", "doc_id": "d1", "folder_id": "src"}),
    ]


def test_folder_delete_tells_extensions(client):
    with (
        patch(f"{R}.folders_db.get_folder", return_value={"id": "f1", "name": "Old", "userId": "mark"}),
        patch(f"{R}.folders_db.count_folder_documents", return_value=0),
        patch(f"{R}.folders_db.delete_folder"),
        patch(f"{R}.document_event") as event,
    ):
        assert client.delete("/api/folders/f1").status_code == 204
    event.assert_called_once_with("folder_deleted", {"uid": "mark", "folder_id": "f1"})


# --- rename a document (PATCH /api/documents/{id} {"filename": ...}) --------------


def _rename(client, doc, body, *, siblings=()):
    gcs_client, bucket = _gcs()
    with (
        patch(f"{R}._get_firestore_doc", return_value=doc),
        patch(f"{R}.folders_db.list_folder_documents", return_value=list(siblings)),
        patch(f"{R}.folders_db.get_folder", return_value={"id": "src", "name": "Papers"}),
        patch(f"{R}.resolve_documents_bucket", return_value="bkt"),
        patch("google.cloud.storage.Client", return_value=gcs_client),
        patch("db.firestore.update_document") as update,
        patch(f"{R}.folders_db.update_folder_counts") as counts,
        patch(f"{R}.document_event") as event,
    ):
        resp = client.patch("/api/documents/d1", json=body)
    return resp, update, bucket, counts, event


def test_owner_renames_record_and_its_upload_path(client):
    resp, update, bucket, counts, event = _rename(client, _doc(), {"filename": "  Q3 report.pdf "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["originalFilename"] == "Q3 report.pdf" and body["id"] == "d1"
    fields = update.call_args.args[2]
    assert fields["originalFilename"] == "Q3 report.pdf"
    # The object at the folder-derived path moves with the name, so a later
    # upload of the OLD name can't overwrite this document's bytes.
    assert bucket.copy_blob.call_args.args[2] == "users/mark/docs/src/Q3 report.pdf"
    bucket.blob.return_value.delete.assert_called_once()
    assert fields["sourceUrl"] == "gs://bkt/users/mark/docs/src/Q3 report.pdf"
    counts.assert_not_called()  # same folder, same count
    event.assert_called_once_with("upserted", {"uid": "mark", "doc_id": "d1", "folder_id": "src"})


def test_rename_keeps_the_extension_when_dropped(client):
    resp, update, *_ = _rename(client, _doc(), {"filename": "Q3 report"})
    assert resp.status_code == 200
    assert update.call_args.args[2]["originalFilename"] == "Q3 report.pdf"
    # A different ending stays part of the name; the format never changes.
    resp, update, *_ = _rename(client, _doc(), {"filename": "notes.txt"})
    assert update.call_args.args[2]["originalFilename"] == "notes.txt.pdf"
    # A case-only difference in the ending is the same extension.
    resp, update, *_ = _rename(client, _doc(), {"filename": "Scan.PDF"})
    assert update.call_args.args[2]["originalFilename"] == "Scan.PDF"


def test_rename_leaves_objects_outside_the_upload_path_alone(client):
    # Imported by reference: the object is shared, never ours to move.
    doc = _doc(folderId=None, storagePath="shared/ppa/a.pdf")
    resp, update, bucket, *_ = _rename(client, doc, {"filename": "b"})
    assert resp.status_code == 200
    bucket.copy_blob.assert_not_called()
    fields = update.call_args.args[2]
    assert fields["originalFilename"] == "b.pdf"
    assert "storagePath" not in fields and "sourceUrl" not in fields


def test_rename_to_a_name_taken_in_the_folder_is_409(client):
    other = _doc(id="d9", originalFilename="b.pdf", storagePath="users/mark/docs/src/b.pdf")
    resp, update, bucket, *_ = _rename(client, _doc(), {"filename": "b.pdf"}, siblings=[other])
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "FILENAME_EXISTS"
    assert "'Papers' already has a document called 'b.pdf'" in detail["message"]
    update.assert_not_called()
    bucket.copy_blob.assert_not_called()


@pytest.mark.parametrize("name", ["", "   ", "..", "a/b", "a\\b", "x" * 201, "tab\there"])
def test_invalid_names_are_422_with_a_message(client, name):
    resp, update, *_ = _rename(client, _doc(), {"filename": name})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and detail
    update.assert_not_called()


def test_rename_to_the_same_name_changes_nothing(client):
    resp, update, *_ = _rename(client, _doc(), {"filename": "a.pdf"})
    assert resp.status_code == 200
    update.assert_not_called()


def test_cannot_rename_someone_elses_document(client):
    resp, update, *_ = _rename(client, _doc(userId="eve"), {"filename": "mine.pdf"})
    assert resp.status_code == 403
    update.assert_not_called()


def test_unknown_document_rename_is_404(client):
    resp, *_ = _rename(client, None, {"filename": "x.pdf"})
    assert resp.status_code == 404


def test_patch_needs_exactly_one_change(client):
    assert _rename(client, _doc(), {})[0].status_code == 422
    assert _rename(client, _doc(), {"filename": "b", "folder_id": "dst"})[0].status_code == 422
