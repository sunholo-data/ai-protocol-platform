"""Tests for POST /api/documents/upload — bucket migration + folderId + parseStatus."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user


@pytest.fixture(autouse=True)
def _no_version_store():
    """Keep the version store (GCS) out of unit tests."""
    with patch("tools.documents.ingest._save_version", return_value=None):
        yield


_USER = User(uid="user1", email="alice@example.com", domain="example.com")


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.upload import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


def _file(name: str = "test.docx") -> dict:
    return {"file": (name, BytesIO(b"fake content"), "application/octet-stream")}


class TestUploadBucketResolution:
    def test_uses_per_client_bucket_not_logs_bucket(self, client: TestClient):
        captured = {}

        def fake_gcs_upload(bucket_name, path, data, content_type, uid, filename):
            captured["bucket"] = bucket_name

        with (
            patch("tools.documents.ingest.resolve_documents_bucket", return_value="example-documents"),
            patch("tools.documents.ingest._upload_to_gcs", side_effect=fake_gcs_upload),
            patch("tools.documents.ingest._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.ingest._store_document"),
            patch("db.folders.ensure_default_folder", return_value="folder1"),
        ):
            resp = client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        assert captured.get("bucket") == "example-documents"

    def test_gcs_path_uses_uid_docs_folder_pattern(self, client: TestClient):
        captured = {}

        def fake_gcs_upload(bucket_name, path, data, content_type, uid, filename):
            captured["path"] = path

        with (
            patch("tools.documents.ingest.resolve_documents_bucket", return_value="example-documents"),
            patch("tools.documents.ingest._upload_to_gcs", side_effect=fake_gcs_upload),
            patch("tools.documents.ingest._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.ingest._store_document"),
            patch("db.folders.ensure_default_folder", return_value="folder1"),
        ):
            resp = client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        path = captured.get("path", "")
        assert path.startswith(f"users/{_USER.uid}/docs/")

    def test_parse_status_pending_written_immediately(self, client: TestClient):
        immediate_writes = []

        def fake_store(doc_id, *, parse_result, **kwargs):
            if parse_result.status == "pending":
                immediate_writes.append(doc_id)

        with (
            patch("tools.documents.ingest.resolve_documents_bucket", return_value="bucket"),
            patch("tools.documents.ingest._upload_to_gcs"),
            patch("tools.documents.ingest._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.ingest._store_document", side_effect=fake_store),
            patch("db.folders.ensure_default_folder", return_value="folder1"),
        ):
            client.post("/api/documents/upload", files=_file())

        assert len(immediate_writes) >= 1

    def test_auto_creates_folder_when_none_provided(self, client: TestClient):
        calls = []

        def capture_ensure(uid: str) -> str:
            calls.append(uid)
            return "auto-folder"

        with (
            patch("tools.documents.ingest.resolve_documents_bucket", return_value="bucket"),
            patch("tools.documents.ingest._upload_to_gcs"),
            patch("tools.documents.ingest._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.ingest._store_document"),
            patch("db.folders.ensure_default_folder", side_effect=capture_ensure),
        ):
            resp = client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        assert calls == ["user1"]

    def test_unsupported_extension_returns_400(self, client: TestClient):
        with patch("tools.documents.ingest.resolve_documents_bucket", return_value="bucket"):
            resp = client.post("/api/documents/upload", files=_file("test.exe"))
        assert resp.status_code == 400


class TestUploadIntoProject:
    def test_unknown_project_is_404_before_anything_is_stored(self, client: TestClient):
        with (
            patch("tools.documents.upload.projects_db.get_project", return_value=None),
            patch("tools.documents.upload.ingest_document") as ingest,
        ):
            resp = client.post("/api/documents/upload", files=_file(), data={"project_id": "p-gone"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
        ingest.assert_not_called()

    def test_project_id_is_a_form_field_passed_to_ingest(self, client: TestClient):
        from tools.documents.ingest import ParsedDocumentResponse

        done = ParsedDocumentResponse(docId="d1", status="parsed", originalFilename="test.docx", storagePath="x")
        with (
            patch("tools.documents.upload.projects_db.get_project", return_value={"id": "p1"}),
            patch("tools.documents.upload.ingest_document", return_value=done) as ingest,
        ):
            resp = client.post("/api/documents/upload", files=_file(), data={"project_id": "p1"})
        assert resp.status_code == 200
        assert ingest.call_args.kwargs["project_id"] == "p1"
