"""Regression tests for the shared ingest — tools/documents/ingest.py (v6.32.0 M1).

These pin the six behaviours that made `POST /upload` correct while they lived
inside the route handler. They exist because the generation path now calls the
same function: if any of these silently stops holding, a generated document
becomes subtly not a real document (wrong bucket on one tenant, no dedup on
regeneration, counters off by the number of iterations).

Every test here calls `ingest_document()` directly — no TestClient, no HTTP
request in scope. That is the property M1 was for.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from auth import User

_USER = User(uid="user123", email="test@example.com", domain="example.com")


def _patches(parse_return=("parsed", [{"type": "paragraph", "text": "Hi"}], 10, None), existing=None):
    """The standard seam set: bucket, GCS, parse, Firestore, folders."""
    return [
        patch("tools.documents.ingest.resolve_documents_bucket", return_value="test-bucket"),
        patch("tools.documents.ingest._upload_to_gcs"),
        patch("tools.documents.ingest._run_parse", return_value=parse_return),
        patch("tools.documents.ingest.set_document"),
        patch("tools.documents.ingest.query_documents", return_value=existing or []),
        patch("tools.documents.ingest.folders_db.ensure_default_folder", return_value="folder1"),
        patch("tools.documents.ingest.folders_db.update_folder_counts"),
        # Version snapshotting talks to GCS; unit tests must not. Its own
        # behaviour is covered in tests/tool_tests/test_document_versions.py.
        patch("tools.documents.ingest._save_version", return_value=None),
    ]


async def _ingest(**overrides):
    from tools.documents.ingest import ingest_document

    kwargs = {
        "user": _USER,
        "file_bytes": b"fake content",
        "filename": "report.docx",
    }
    kwargs.update(overrides)
    return await ingest_document(**kwargs)


class TestCallableWithoutHTTP:
    """The point of the extraction."""

    @pytest.mark.asyncio
    async def test_ingest_document_is_callable_with_no_request(self):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            result = await _ingest()

        assert result.doc_id
        assert result.status == "parsed"
        assert result.original_filename == "report.docx"
        assert result.folder_id == "folder1"


class TestTenantFailsClosed:
    @pytest.mark.asyncio
    async def test_unmapped_tenant_raises_403_with_structured_code(self):
        from contextlib import ExitStack

        from db.clients import UnmappedTenantError

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "tools.documents.ingest.resolve_documents_bucket",
                    side_effect=UnmappedTenantError("no bucket for example.com"),
                )
            )
            with pytest.raises(HTTPException) as exc:
                await _ingest()

        assert exc.value.status_code == 403
        # Structured detail so the UI can branch and show a specific why
        # (NEVER-SILENT #8) rather than a generic "Upload failed (403)".
        assert exc.value.detail["code"] == "TENANT_NOT_PROVISIONED"
        assert exc.value.detail["message"]

    @pytest.mark.asyncio
    async def test_unmapped_tenant_never_writes_to_a_fallback_bucket(self):
        from contextlib import ExitStack

        from db.clients import UnmappedTenantError

        uploads: list = []
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "tools.documents.ingest.resolve_documents_bucket",
                    side_effect=UnmappedTenantError("nope"),
                )
            )
            stack.enter_context(patch("tools.documents.ingest._upload_to_gcs", side_effect=uploads.append))
            with pytest.raises(HTTPException):
                await _ingest()

        assert uploads == [], "refusing the tenant must not write bytes anywhere"


class TestBucketAndPath:
    @pytest.mark.asyncio
    async def test_uses_per_client_bucket_and_uid_scoped_path(self):
        from contextlib import ExitStack

        captured: dict = {}

        def fake_upload(bucket_name, path, data, content_type, uid, filename):
            captured.update(bucket=bucket_name, path=path, content_type=content_type)

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(patch("tools.documents.ingest._upload_to_gcs", side_effect=fake_upload))
            await _ingest()

        assert captured["bucket"] == "test-bucket"
        assert captured["path"] == f"users/{_USER.uid}/docs/folder1/report.docx"
        # Canonical content type, not the octet-stream a programmatic client sends
        assert captured["content_type"].endswith("wordprocessingml.document")


class TestDedup:
    @pytest.mark.asyncio
    async def test_same_filename_in_folder_reuses_doc_id(self):
        from contextlib import ExitStack

        stored_ids: list[str] = []
        with ExitStack() as stack:
            for p in _patches(existing=[{"__id": "existing-doc-abc", "originalFilename": "report.docx"}]):
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.set_document", side_effect=lambda _c, d, _doc: stored_ids.append(d))
            )
            result = await _ingest()

        assert stored_ids and all(s == "existing-doc-abc" for s in stored_ids)
        assert result.doc_id == "existing-doc-abc"

    @pytest.mark.asyncio
    async def test_regeneration_updates_in_place_not_a_second_record(self):
        """Regenerating under the same name must update, not pile up drafts."""
        from contextlib import ExitStack

        stored_ids: list[str] = []
        with ExitStack() as stack:
            for p in _patches(existing=[{"__id": "gen-doc-1", "originalFilename": "offer.docx"}]):
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.set_document", side_effect=lambda _c, d, _doc: stored_ids.append(d))
            )
            await _ingest(filename="offer.docx", source="generated")

        assert set(stored_ids) == {"gen-doc-1"}


class TestWriteOrdering:
    @pytest.mark.asyncio
    async def test_pending_record_is_written_before_parse_runs(self):
        """The frontend shows the file immediately; the parse fills it in."""
        from contextlib import ExitStack

        events: list[str] = []

        def capture_set(_collection, _doc_id, doc):
            events.append(f"store:{doc.get('parseStatus')}")

        async def capture_parse(_gs_url):
            events.append("parse")
            return ("parsed", [], 5, None)

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(patch("tools.documents.ingest.set_document", side_effect=capture_set))
            stack.enter_context(patch("tools.documents.ingest._run_parse", side_effect=capture_parse))
            await _ingest()

        assert events[0] == "store:pending", f"pending write must come first, got {events}"
        assert "parse" in events
        assert events.index("store:pending") < events.index("parse")
        assert events[-1] == "store:parsed"


class TestFolderCounters:
    @pytest.mark.asyncio
    async def test_new_parsed_document_increments_both_counters(self):
        from contextlib import ExitStack

        calls: list = []
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "tools.documents.ingest.folders_db.update_folder_counts",
                    side_effect=lambda *a, **k: calls.append((a, k)),
                )
            )
            await _ingest()

        assert calls == [((_USER.uid, "folder1"), {"doc_delta": 1, "parsed_delta": 1})]

    @pytest.mark.asyncio
    async def test_overwrite_does_not_touch_counters(self):
        from contextlib import ExitStack

        calls: list = []
        with ExitStack() as stack:
            for p in _patches(existing=[{"__id": "existing-id", "originalFilename": "report.docx"}]):
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.folders_db.update_folder_counts", side_effect=calls.append)
            )
            await _ingest()

        assert calls == [], "counters must not move on overwrite"

    @pytest.mark.asyncio
    async def test_failed_parse_does_not_increment_counters(self):
        from contextlib import ExitStack

        calls: list = []
        with ExitStack() as stack:
            for p in _patches(parse_return=("failed", [], 5, "boom")):
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.folders_db.update_folder_counts", side_effect=calls.append)
            )
            result = await _ingest()

        assert calls == []
        assert result.status == "failed"


class TestStorageFailure:
    @pytest.mark.asyncio
    async def test_gcs_failure_writes_failed_record_then_raises_500(self):
        """Never a silent loss: the record reaches a visible terminal state."""
        from contextlib import ExitStack

        statuses: list[str] = []
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(patch("tools.documents.ingest._upload_to_gcs", side_effect=RuntimeError("gcs down")))
            stack.enter_context(
                patch(
                    "tools.documents.ingest.set_document",
                    side_effect=lambda _c, _d, doc: statuses.append(doc.get("parseStatus")),
                )
            )
            with pytest.raises(HTTPException) as exc:
                await _ingest()

        assert exc.value.status_code == 500
        assert statuses == ["pending", "failed"], f"got {statuses}"


class TestProvenance:
    @pytest.mark.asyncio
    async def test_upload_defaults_to_source_uploaded_and_no_generated_from(self):
        from contextlib import ExitStack

        stored: dict = {}
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.set_document", side_effect=lambda _c, _d, doc: stored.update(doc))
            )
            await _ingest()

        assert stored["source"] == "uploaded"
        assert "generatedFrom" not in stored

    @pytest.mark.asyncio
    async def test_generated_document_records_its_provenance(self):
        from contextlib import ExitStack

        stored: dict = {}
        provenance = {
            "skillId": "one-offer-creator",
            "sessionId": "sess-1",
            "templateDocId": "tmpl-doc-9",
            "promptDigest": "sha256:abc",
        }
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.set_document", side_effect=lambda _c, _d, doc: stored.update(doc))
            )
            await _ingest(source="generated", generated_from=provenance)

        assert stored["source"] == "generated"
        assert stored["generatedFrom"] == provenance


class TestValidation:
    @pytest.mark.asyncio
    async def test_unsupported_extension_rejected_before_any_write(self):
        from contextlib import ExitStack

        writes: list = []
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(patch("tools.documents.ingest.set_document", side_effect=writes.append))
            with pytest.raises(HTTPException) as exc:
                await _ingest(filename="malware.exe")

        assert exc.value.status_code == 400
        assert writes == []

    @pytest.mark.asyncio
    async def test_path_separators_are_stripped_from_filename(self):
        from contextlib import ExitStack

        captured: dict = {}
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "tools.documents.ingest._upload_to_gcs",
                    side_effect=lambda b, path, *a, **k: captured.update(path=path),
                )
            )
            await _ingest(filename="../../etc/passwd.txt")

        # Traversal is impossible either way; since folder upload the directory
        # part is dropped entirely (Chrome sends relative paths as filenames),
        # so the object lands as the bare name inside the user's own folder.
        assert captured["path"] == f"users/{_USER.uid}/docs/folder1/passwd.txt"

    @pytest.mark.asyncio
    async def test_explicit_folder_id_wins_over_default(self):
        from contextlib import ExitStack

        ensure_calls: list = []
        captured: dict = {}
        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.folders_db.ensure_default_folder", side_effect=ensure_calls.append)
            )
            stack.enter_context(
                patch(
                    "tools.documents.ingest._upload_to_gcs",
                    side_effect=lambda b, path, *a, **k: captured.update(path=path),
                )
            )
            result = await _ingest(folder_id="chosen-folder")

        assert ensure_calls == [], "must not auto-create when a folder was given"
        assert "chosen-folder" in captured["path"]
        assert result.folder_id == "chosen-folder"


class TestFolderUpload:
    """Folder upload: folder_path picks/creates the library folder, and
    skip_unchanged makes a re-upload of an unchanged file a no-op."""

    @pytest.mark.asyncio
    async def test_folder_path_creates_named_folder(self):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            named = stack.enter_context(
                patch("tools.documents.ingest.folders_db.ensure_named_folder", return_value="reports-2026")
            )
            result = await _ingest(folder_path="/Reports//2026/")
        named.assert_called_once_with("user123", "Reports/2026")
        assert result.folder_id == "reports-2026"

    @pytest.mark.asyncio
    async def test_explicit_folder_id_wins_over_folder_path(self):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            named = stack.enter_context(patch("tools.documents.ingest.folders_db.ensure_named_folder"))
            result = await _ingest(folder_id="picked", folder_path="Reports")
        named.assert_not_called()
        assert result.folder_id == "picked"

    @pytest.mark.asyncio
    async def test_parent_segments_are_refused(self):
        with pytest.raises(HTTPException) as exc:
            await _ingest(folder_path="Reports/../secrets")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_identical_reupload_is_skipped(self):
        import hashlib
        from contextlib import ExitStack

        existing = [
            {
                "__id": "doc-1",
                "parseStatus": "parsed",
                "originalFilename": "report.docx",
                "blockCount": 3,
                "storagePath": "users/user123/docs/folder1/report.docx",
                "folderId": "folder1",
                "contentSha256": hashlib.sha256(b"fake content").hexdigest(),
            }
        ]
        with ExitStack() as stack:
            for p in _patches(existing=existing):
                stack.enter_context(p)
            parse = stack.enter_context(patch("tools.documents.ingest._run_parse"))
            upload = stack.enter_context(patch("tools.documents.ingest._upload_to_gcs"))
            result = await _ingest(skip_unchanged=True)
        assert result.unchanged is True
        assert result.doc_id == "doc-1"
        parse.assert_not_called()
        upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_content_is_reingested_and_hash_stored(self):
        from contextlib import ExitStack

        existing = [{"__id": "doc-1", "parseStatus": "parsed", "contentSha256": "old"}]
        stored = []
        with ExitStack() as stack:
            for p in _patches(existing=existing):
                stack.enter_context(p)
            stack.enter_context(
                patch("tools.documents.ingest.set_document", side_effect=lambda _c, _d, doc: stored.append(doc))
            )
            result = await _ingest(skip_unchanged=True)
        assert result.unchanged is False
        assert result.doc_id == "doc-1"
        import hashlib

        assert stored[-1]["contentSha256"] == hashlib.sha256(b"fake content").hexdigest()

    @pytest.mark.asyncio
    async def test_without_skip_unchanged_identical_upload_still_reingests(self):
        import hashlib
        from contextlib import ExitStack

        existing = [
            {"__id": "doc-1", "parseStatus": "parsed", "contentSha256": hashlib.sha256(b"fake content").hexdigest()}
        ]
        with ExitStack() as stack:
            for p in _patches(existing=existing):
                stack.enter_context(p)
            parse = stack.enter_context(
                patch("tools.documents.ingest._run_parse", return_value=("parsed", [], 1, None))
            )
            result = await _ingest()
        parse.assert_called_once()
        assert result.unchanged is False


class TestFolderUploadFilenames:
    """Chrome sends a directory-picker file's relative path as its filename."""

    @pytest.mark.asyncio
    async def test_relative_path_filename_keeps_only_the_name(self):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            stack.enter_context(patch("tools.documents.ingest.folders_db.ensure_named_folder", return_value="llm"))
            result = await _ingest(filename="llm/attention.pdf", folder_path="llm")
        assert result.original_filename == "attention.pdf"
        assert result.storage_path.endswith("/llm/attention.pdf")

    @pytest.mark.asyncio
    async def test_images_are_accepted(self):
        from contextlib import ExitStack

        for name in ("scan.png", "photo.JPG", "diagram.jpeg"):
            with ExitStack() as stack:
                for p in _patches():
                    stack.enter_context(p)
                result = await _ingest(filename=name)
            assert result.original_filename == name


class TestTellsExtensions:
    """A searchable folder re-indexes on every (re)parse — via extensions.document_event."""

    @pytest.mark.asyncio
    async def test_upserted_after_the_parse_is_stored(self):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _patches():
                stack.enter_context(p)
            event = stack.enter_context(patch("tools.documents.ingest.document_event"))
            result = await _ingest()
        event.assert_called_once_with("upserted", {"uid": "user123", "doc_id": result.doc_id, "folder_id": "folder1"})
