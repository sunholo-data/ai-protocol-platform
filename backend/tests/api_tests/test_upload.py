"""Tests for POST /api/documents/upload — bucket migration + folderId + parseStatus."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

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
            patch("tools.documents.upload.resolve_documents_bucket", return_value="example-documents"),
            patch("tools.documents.upload._upload_to_gcs", side_effect=fake_gcs_upload),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.upload._store_document"),
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
            patch("tools.documents.upload.resolve_documents_bucket", return_value="example-documents"),
            patch("tools.documents.upload._upload_to_gcs", side_effect=fake_gcs_upload),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.upload._store_document"),
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
            patch("tools.documents.upload.resolve_documents_bucket", return_value="bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.upload._store_document", side_effect=fake_store),
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
            patch("tools.documents.upload.resolve_documents_bucket", return_value="bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 10, None)),
            patch("tools.documents.upload._store_document"),
            patch("db.folders.ensure_default_folder", side_effect=capture_ensure),
        ):
            resp = client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        assert calls == ["user1"]

    def test_unsupported_extension_returns_400(self, client: TestClient):
        with patch("tools.documents.upload.resolve_documents_bucket", return_value="bucket"):
            resp = client.post("/api/documents/upload", files=_file("test.exe"))
        assert resp.status_code == 400
