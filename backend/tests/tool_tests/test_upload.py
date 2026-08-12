"""Tests for tools/documents/upload.py."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_USER = User(uid="user123", email="test@example.com", domain="example.com")


# --- Plain-text parse path (v6.14.0): .txt no longer strands on "Parsing…" ---


def test_is_plain_text_routes_txt_not_pdf():
    from tools.documents.upload import _is_plain_text

    assert _is_plain_text("gs://b/notes.txt")
    assert _is_plain_text("gs://b/server.LOG")
    assert not _is_plain_text("gs://b/contract.pdf")
    assert not _is_plain_text("gs://b/deck.docx")


@pytest.mark.asyncio
async def test_run_parse_txt_emits_paragraph_blocks_and_parsed_status():
    from tools.documents.upload import _run_parse

    text = b"First paragraph.\n\nSecond paragraph.\n\nThird."
    blob = MagicMock()
    blob.download_as_bytes.return_value = text
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    with patch("google.cloud.storage.Client", return_value=client):
        status, blocks, _ms, err = await _run_parse("gs://bucket/users/u/docs/notes.txt")

    assert status == "parsed" and err is None
    assert [b["text"] for b in blocks] == ["First paragraph.", "Second paragraph.", "Third."]
    assert all(b["type"] == "paragraph" and b.get("block_id") for b in blocks)


@pytest.mark.asyncio
async def test_run_parse_txt_read_failure_is_failed_not_stranded():
    from tools.documents.upload import _run_parse

    with patch("google.cloud.storage.Client", side_effect=RuntimeError("no creds")):
        status, blocks, _ms, err = await _run_parse("gs://bucket/x.txt")
    # A read failure is a clear terminal state — NEVER the never-resolving
    # pending_ai_extraction that stranded the viewer.
    assert status == "failed" and blocks == [] and err


@pytest.fixture
def upload_client():
    from tools.documents.upload import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _USER
    return TestClient(app)


def _file(name: str = "report.docx") -> dict:
    return {"file": (name, io.BytesIO(b"fake content"), "application/octet-stream")}


def _common_patches(parse_return=("parsed", [{"type": "paragraph", "text": "Hi"}], 10, None)):
    return [
        patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
        patch("tools.documents.upload._upload_to_gcs"),
        patch("tools.documents.upload._run_parse", return_value=parse_return),
        patch("tools.documents.upload.set_document"),
        patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="folder1"),
        patch("tools.documents.upload.folders_db.update_folder_counts"),
    ]


class TestUploadEndpoint:
    def test_unsupported_extension(self, upload_client):
        resp = upload_client.post(
            "/api/documents/upload",
            files={"file": ("script.exe", io.BytesIO(b"binary"), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "not supported" in resp.json()["detail"]

    def test_docx_upload_success(self, upload_client):
        fake_blocks = [{"type": "paragraph", "text": "Hello"}]
        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("parsed", fake_blocks, 11, None)),
            patch("tools.documents.upload.set_document") as mock_set,
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="folder1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            resp = upload_client.post("/api/documents/upload", files=_file(), data={"skill_id": "my-skill"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "parsed"
        assert data["originalFilename"] == "report.docx"
        assert data["blocksCount"] == 1
        assert data["folderId"] == "folder1"
        mock_set.assert_called()

    def test_blocks_stored_in_firestore_on_success(self, upload_client):
        """Regression: blocks must be persisted to Firestore so build_document_context can load them."""
        fake_blocks = [{"type": "heading", "text": "Intro"}, {"type": "paragraph", "text": "Body"}]
        stored: dict = {}

        def capture_set(collection, doc_id, doc):
            stored.update(doc)

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("parsed", fake_blocks, 20, None)),
            patch("tools.documents.upload.set_document", side_effect=capture_set),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            upload_client.post("/api/documents/upload", files=_file())

        assert "blocks" in stored, "blocks field missing from Firestore document"
        assert stored["blocks"] == fake_blocks, "blocks content mismatch"

    def test_failed_parse_stores_empty_blocks(self, upload_client):
        """Failed parse should store [] not the parsed blocks (there are none)."""
        stored: dict = {}

        def capture_set(collection, doc_id, doc):
            stored.update(doc)

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("failed", [], 5, "timeout")),
            patch("tools.documents.upload.set_document", side_effect=capture_set),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            upload_client.post("/api/documents/upload", files=_file())

        assert stored.get("blocks") == []
        assert stored.get("parseStatus") == "failed"

    def test_ailang_parse_failure_returns_failed_status(self, upload_client):
        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("failed", [], 5, "parse error")),
            patch("tools.documents.upload.set_document"),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="folder1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            resp = upload_client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_unparseable_format_gets_pending_ai_extraction_status(self, upload_client):
        """When AILANG can't parse a format at all, the upload returns the
        pending_ai_extraction status so the frontend / agent can surface the
        in-progress state. PDFs USED to take this path because PDF was excluded
        from the original DETERMINISTIC_EXTENSIONS gate — but as of 2026-06-11
        PDFs (and images) are in PARSEABLE_EXTENSIONS, so they hit the real
        parse path. Force the gate closed here to exercise the legacy fallback.
        """
        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.ailang_parse.is_parseable", return_value=False),
            patch("tools.documents.upload.set_document"),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="folder1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            resp = upload_client.post(
                "/api/documents/upload",
                files={"file": ("document.pdf", io.BytesIO(b"pdf bytes"), "application/pdf")},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending_ai_extraction"

    def test_bucket_comes_from_resolve_documents_bucket(self, upload_client):
        calls = []

        def capture_upload(bucket_name, *args, **kwargs):
            calls.append(bucket_name)

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="client-specific-bucket"),
            patch("tools.documents.upload._upload_to_gcs", side_effect=capture_upload),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 0, None)),
            patch("tools.documents.upload.set_document"),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
        ):
            upload_client.post("/api/documents/upload", files=_file())

        assert calls == ["client-specific-bucket"]

    def test_reupload_same_filename_reuses_doc_id(self, upload_client):
        """Re-uploading the same filename in the same folder must reuse the existing doc_id."""
        existing_doc_id = "existing-doc-abc"
        stored_ids: list[str] = []

        def capture_set(collection, doc_id, doc):
            stored_ids.append(doc_id)

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch(
                "tools.documents.upload._run_parse",
                return_value=("parsed", [{"type": "paragraph", "text": "v2"}], 10, None),
            ),
            patch("tools.documents.upload.set_document", side_effect=capture_set),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
            patch(
                "tools.documents.upload.query_documents",
                return_value=[{"__id": existing_doc_id, "originalFilename": "report.docx"}],
            ),
        ):
            resp = upload_client.post("/api/documents/upload", files=_file())

        assert resp.status_code == 200
        assert all(sid == existing_doc_id for sid in stored_ids), f"Expected {existing_doc_id}, got {stored_ids}"

    def test_reupload_does_not_increment_folder_count(self, upload_client):
        """Folder counts must not change when overwriting an existing document."""
        count_calls: list = []

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch(
                "tools.documents.upload._run_parse",
                return_value=("parsed", [{"type": "paragraph", "text": "v2"}], 10, None),
            ),
            patch("tools.documents.upload.set_document"),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts", side_effect=count_calls.append),
            patch(
                "tools.documents.upload.query_documents",
                return_value=[{"__id": "existing-id", "originalFilename": "report.docx"}],
            ),
        ):
            upload_client.post("/api/documents/upload", files=_file())

        assert count_calls == [], "update_folder_counts must not be called on overwrite"

    def test_new_upload_does_create_fresh_doc_id(self, upload_client):
        """When no existing doc matches, a new UUID must be generated."""
        stored_ids: list[str] = []

        with (
            patch("tools.documents.upload.resolve_documents_bucket", return_value="test-bucket"),
            patch("tools.documents.upload._upload_to_gcs"),
            patch("tools.documents.upload._run_parse", return_value=("parsed", [], 0, None)),
            patch("tools.documents.upload.set_document", side_effect=lambda _c, doc_id, _d: stored_ids.append(doc_id)),
            patch("tools.documents.upload.folders_db.ensure_default_folder", return_value="f1"),
            patch("tools.documents.upload.folders_db.update_folder_counts"),
            patch("tools.documents.upload.query_documents", return_value=[]),
        ):
            upload_client.post("/api/documents/upload", files=_file())

        assert len(stored_ids) > 0
        assert stored_ids[0] != "existing-id"
