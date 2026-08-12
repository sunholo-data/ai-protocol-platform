"""API tests for /api/folders and /api/documents (document-folder CRUD + document fetch).

These test the user-facing document-folder system (db/folders.py + tools/documents/routes.py),
distinct from the storage-ACL bucket/folder system in backend/buckets/.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_USER_A = User(uid="user_a", email="alice@example.com", domain="example.com")
_USER_B = User(uid="user_b", email="bob@example.com", domain="example.com")


@pytest.fixture()
def app_a() -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER_A
    return TestClient(app)


@pytest.fixture()
def app_b() -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER_B
    return TestClient(app)


@pytest.fixture()
def app_anon() -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestPostFolders:
    def test_create_folder_returns_201(self, app_a: TestClient):
        with patch("db.folders.create_folder") as mock_create:
            mock_create.return_value = {
                "id": "folder1",
                "name": "Q1 Review",
                "userId": "user_a",
                "docCount": 0,
                "parsedCount": 0,
            }
            resp = app_a.post("/api/folders", json={"name": "Q1 Review"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "folder1"
        assert data["name"] == "Q1 Review"

    def test_create_folder_passes_uid_not_client_supplied(self, app_a: TestClient):
        calls = []

        def capturing_create(user_id: str, name: str) -> dict:
            calls.append((user_id, name))
            return {"id": "f1", "name": name, "userId": user_id, "docCount": 0, "parsedCount": 0}

        with patch("db.folders.create_folder", side_effect=capturing_create):
            app_a.post("/api/folders", json={"name": "My Folder"})

        assert calls[0][0] == "user_a"

    def test_create_folder_requires_name(self, app_a: TestClient):
        resp = app_a.post("/api/folders", json={})
        assert resp.status_code == 422

    def test_create_folder_requires_auth(self, app_anon: TestClient):
        resp = app_anon.post("/api/folders", json={"name": "test"})
        assert resp.status_code == 401


class TestGetFolders:
    def test_returns_caller_folders_only(self, app_a: TestClient):
        folders_a = [{"id": "f1", "name": "My Docs", "userId": "user_a", "docCount": 3, "parsedCount": 3}]
        with patch("db.folders.list_folders", return_value=folders_a):
            resp = app_a.get("/api/folders")
        assert resp.status_code == 200
        assert len(resp.json()["folders"]) == 1
        assert resp.json()["folders"][0]["id"] == "f1"

    def test_returns_empty_list_when_no_folders(self, app_a: TestClient):
        with patch("db.folders.list_folders", return_value=[]):
            resp = app_a.get("/api/folders")
        assert resp.status_code == 200
        assert resp.json()["folders"] == []

    def test_list_queries_by_caller_uid(self, app_a: TestClient):
        calls = []

        def capturing_list(user_id: str) -> list:
            calls.append(user_id)
            return []

        with patch("db.folders.list_folders", side_effect=capturing_list):
            app_a.get("/api/folders")

        assert calls == ["user_a"]

    def test_requires_auth(self, app_anon: TestClient):
        resp = app_anon.get("/api/folders")
        assert resp.status_code == 401


class TestGetFolderDocuments:
    def test_returns_documents_for_own_folder(self, app_a: TestClient):
        docs = [{"id": "doc1", "title": "Report.docx", "parseStatus": "parsed", "blockCount": 42}]
        with (
            patch("db.folders.get_folder", return_value={"id": "f1", "userId": "user_a"}),
            patch("db.folders.list_folder_documents", return_value=docs),
        ):
            resp = app_a.get("/api/folders/f1/documents")
        assert resp.status_code == 200
        assert len(resp.json()["documents"]) == 1
        assert resp.json()["documents"][0]["parseStatus"] == "parsed"

    def test_returns_empty_for_new_folder(self, app_a: TestClient):
        with (
            patch("db.folders.get_folder", return_value={"id": "f1", "userId": "user_a"}),
            patch("db.folders.list_folder_documents", return_value=[]),
        ):
            resp = app_a.get("/api/folders/f1/documents")
        assert resp.status_code == 200
        assert resp.json()["documents"] == []

    def test_returns_404_for_missing_folder(self, app_a: TestClient):
        with patch("db.folders.get_folder", return_value=None):
            resp = app_a.get("/api/folders/missing/documents")
        assert resp.status_code == 404

    def test_returns_403_for_other_users_folder(self, app_a: TestClient):
        with patch("db.folders.get_folder", return_value={"id": "f1", "userId": "user_b"}):
            resp = app_a.get("/api/folders/f1/documents")
        assert resp.status_code == 403

    def test_requires_auth(self, app_anon: TestClient):
        resp = app_anon.get("/api/folders/f1/documents")
        assert resp.status_code == 401


_PARSED_DOC = {
    "id": "doc123",
    "userId": "user_a",
    "originalFilename": "report.docx",
    "sourceFormat": "docx",
    "parseStatus": "parsed",
    "sourceUrl": "gs://bucket/report.docx",
    "summary": {"totalBlocks": 10, "headings": 2, "tables": 1, "images": 0, "changes": 0},
    "a2uiComponents": {
        "root": "root-1",
        "components": [{"id": "root-1", "component": {"type": "Text", "value": "Hello"}}],
    },
}


class TestGetDocument:
    def test_returns_document_for_owner(self, app_a: TestClient):
        with patch("tools.documents.routes._get_firestore_doc", return_value=dict(_PARSED_DOC)):
            resp = app_a.get("/api/documents/doc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["originalFilename"] == "report.docx"
        assert data["a2uiComponents"]["root"] == "root-1"
        assert data["id"] == "doc123"

    def test_returns_404_for_missing_doc(self, app_a: TestClient):
        with patch("tools.documents.routes._get_firestore_doc", return_value=None):
            resp = app_a.get("/api/documents/missing")
        assert resp.status_code == 404

    def test_returns_403_for_other_users_doc(self, app_b: TestClient):
        with patch("tools.documents.routes._get_firestore_doc", return_value=dict(_PARSED_DOC)):
            resp = app_b.get("/api/documents/doc123")
        assert resp.status_code == 403

    def test_requires_auth(self, app_anon: TestClient):
        resp = app_anon.get("/api/documents/doc123")
        assert resp.status_code == 401


# A doc whose filename carries a non-latin-1 character (en-dash U+2013), the
# exact shape that 500'd the preview route (Content-Disposition header could not
# latin-1-encode). See tools.documents.routes._content_disposition.
_UNICODE_DOC = {
    "id": "docU",
    "userId": "user_a",
    # en-dash + ae written as \u escapes so the ASCII source doesn't trip RUF001
    # (ambiguous char); \u2013 IS the char that crashed the header.
    "originalFilename": "Din SAS-booking bekr\u00e6ftet, 1 Jul \u2013 8 Jul.pdf",
    "sourceFormat": "pdf",
    "parseStatus": "parsed",
    "sourceUrl": "gs://bucket/users/user_a/docs/f/booking.pdf",
}


class TestPreviewDocument:
    def _client(self):
        from unittest.mock import MagicMock

        blob = MagicMock()
        blob.download_as_bytes.return_value = b"%PDF-1.4 fake"
        client = MagicMock()
        client.bucket.return_value.blob.return_value = blob
        return client

    def test_unicode_filename_previews_without_500(self, app_a: TestClient):
        """A non-latin-1 filename must NOT crash header serialisation (regression)."""
        with (
            patch("tools.documents.routes._get_firestore_doc", return_value=dict(_UNICODE_DOC)),
            patch("google.cloud.storage.Client", return_value=self._client()),
        ):
            resp = app_a.get("/api/documents/docU/preview")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 fake"
        cd = resp.headers["content-disposition"]
        cd.encode("latin-1")  # would raise if the bug regressed
        # RFC 5987 ext-param carries the true (percent-encoded) name; en-dash → %E2%80%93.
        assert "filename*=UTF-8''" in cd
        assert "%E2%80%93" in cd

    def test_missing_source_url_is_404_not_500(self, app_a: TestClient):
        doc = dict(_UNICODE_DOC, sourceUrl="")
        with patch("tools.documents.routes._get_firestore_doc", return_value=doc):
            resp = app_a.get("/api/documents/docU/preview")
        assert resp.status_code == 404

    def test_other_users_doc_is_403(self, app_b: TestClient):
        with patch("tools.documents.routes._get_firestore_doc", return_value=dict(_UNICODE_DOC)):
            resp = app_b.get("/api/documents/docU/preview")
        assert resp.status_code == 403
