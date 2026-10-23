"""GET /{doc_id}/versions and POST /{doc_id}/revise — DOC-WORKBENCH P3.

The iterate loop: change something, look at it, and be able to go back.

Two properties that are easy to get wrong and expensive to notice late:

* **A revision must update IN PLACE.** It reuses the existing document's
  filename and folder so `ingest_document`'s dedup path updates the record
  rather than piling up a second copy per iteration.
* **`/generate` and `/revise` must share one implementation.** Two entrances to
  the same behaviour is exactly the drift the v6.32.0 ingest extraction exists
  to prevent — applied here one level up.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_OWNER = User(uid="user1", email="alice@example.com", domain="example.com")

_GENERATED_DOC = {
    "userId": "user1",
    "originalFilename": "Offer.docx",
    "sourceFormat": "docx",
    "folderId": "created-folder",
    "sourceMarkdown": "# Offer\n\nOriginal body.",
    "currentVersion": 2,
    "source": "generated",
}

_UPLOADED_DOC = {
    "userId": "user1",
    "originalFilename": "Contract.docx",
    "sourceFormat": "docx",
    "folderId": "uploads",
    "source": "uploaded",
}


def _saved_response():
    """A genuine ParsedDocumentResponse for the streaming route's done frame."""
    from tools.documents.ingest import ParsedDocumentResponse

    return ParsedDocumentResponse(
        docId="doc1",
        status="parsed",
        originalFilename="Offer.docx",
        blocksCount=3,
        storagePath="users/user1/docs/created-folder/Offer.docx",
        folderId="created-folder",
    )


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.generate import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _OWNER
    return TestClient(app)


class _Response:
    def __init__(self):
        self.doc_id = "doc-1"
        self.status = "parsed"
        self.original_filename = "Offer.docx"
        self.blocks_count = 4
        self.storage_path = "users/user1/docs/created-folder/Offer.docx"
        self.folder_id = "created-folder"
        self.error = None


def _patches(doc, versions=None, capture=None):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("db.firestore.get_document", return_value=doc))
    stack.enter_context(patch("db.clients.resolve_documents_bucket", return_value="client-bucket"))
    stack.enter_context(
        patch("tools.documents.versions.list_versions", return_value=versions if versions is not None else [1, 2])
    )

    async def fake_generate(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return _Response()

    stack.enter_context(patch("tools.documents.generate._generate_into_workspace", side_effect=fake_generate))
    return stack


class TestVersions:
    def test_lists_the_stored_versions(self, client: TestClient):
        with _patches(_GENERATED_DOC, versions=[1, 2, 3]):
            resp = client.get("/api/documents/doc-1/versions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["versions"] == [1, 2, 3]
        assert body["currentVersion"] == 2
        assert body["filename"] == "Offer.docx"

    def test_a_document_with_no_history_lists_empty_rather_than_erroring(self, client: TestClient):
        with _patches(_GENERATED_DOC, versions=[]):
            resp = client.get("/api/documents/doc-1/versions")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    def test_a_non_owner_gets_403(self, client: TestClient):
        with _patches({**_GENERATED_DOC, "userId": "other"}):
            resp = client.get("/api/documents/doc-1/versions")
        assert resp.status_code == 403

    def test_unknown_document_is_404(self, client: TestClient):
        with _patches(None):
            resp = client.get("/api/documents/nope/versions")
        assert resp.status_code == 404


class TestRevise:
    def test_revising_reuses_the_filename_and_folder_so_it_updates_in_place(self, client: TestClient):
        captured: dict = {}
        with _patches(_GENERATED_DOC, capture=captured):
            resp = client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# Offer\n\nNew body."})

        assert resp.status_code == 200
        assert captured["filename"] == "Offer.docx", "a new name would create a SECOND document"
        assert captured["folder_id"] == "created-folder"
        assert captured["content_markdown"] == "# Offer\n\nNew body."

    def test_omitting_content_restyles_the_existing_source(self, client: TestClient):
        """Template-only change: no new content, so reuse the stored markdown."""
        captured: dict = {}
        with _patches(_GENERATED_DOC, capture=captured):
            resp = client.post("/api/documents/doc-1/revise", json={"template": "Acme letterhead"})

        assert resp.status_code == 200
        assert captured["content_markdown"] == _GENERATED_DOC["sourceMarkdown"]
        assert captured["template"] == "Acme letterhead"

    def test_it_keeps_the_documents_own_format_by_default(self, client: TestClient):
        captured: dict = {}
        with _patches(_GENERATED_DOC, capture=captured):
            client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# x"})
        assert captured["target"] == "docx"

    def test_a_revision_is_marked_as_such_for_provenance(self, client: TestClient):
        captured: dict = {}
        with _patches(_GENERATED_DOC, capture=captured):
            client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# x"})
        assert captured["via"] == "revise"

    def test_supplied_content_is_applied_even_to_an_uploaded_document(self, client: TestClient):
        """Explicit new content replaces the document — no stored source needed."""
        captured: dict = {}
        with _patches(_UPLOADED_DOC, capture=captured):
            resp = client.post("/api/documents/doc-2/revise", json={"contentMarkdown": "# new body"})

        assert resp.status_code == 200
        assert captured["content_markdown"] == "# new body"

    def test_an_uploaded_document_CAN_still_be_restyled(self, client: TestClient):
        """Applying a template is a pure conversion of the file that already
        exists — no authored markdown needed. Refusing this made "change the
        formatting" impossible on an uploaded document, which is the common case:
        a letterhead is usually applied to something somebody sent you."""
        captured: dict = {}

        async def fake_restyle(**kwargs):
            captured.update(kwargs)
            return _Response()

        with (
            patch("db.firestore.get_document", return_value=_UPLOADED_DOC),
            patch("tools.documents.generate._restyle_from_original", side_effect=fake_restyle),
        ):
            resp = client.post("/api/documents/doc-2/revise", json={"template": "Acme letterhead"})

        assert resp.status_code == 200
        assert captured["template"] == "Acme letterhead"

    def test_a_non_owner_gets_403(self, client: TestClient):
        with _patches({**_GENERATED_DOC, "userId": "other"}):
            resp = client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# x"})
        assert resp.status_code == 403

    def test_a_non_owner_never_reaches_generation(self, client: TestClient):
        captured: dict = {}
        with _patches({**_GENERATED_DOC, "userId": "other"}, capture=captured):
            client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# x"})
        assert captured == {}, "the refusal must come before any work"

    def test_unknown_document_is_404(self, client: TestClient):
        with _patches(None):
            resp = client.post("/api/documents/nope/revise", json={"contentMarkdown": "# x"})
        assert resp.status_code == 404


class TestOneImplementation:
    def test_generate_and_revise_go_through_the_same_function(self, client: TestClient):
        """If these ever diverge, a revision stops behaving like a generation —
        different folder rules, different provenance, different dedup."""
        seen: list[str] = []

        async def fake_generate(**kwargs):
            seen.append(kwargs["via"])
            return _Response()

        with (
            patch("db.firestore.get_document", return_value=_GENERATED_DOC),
            patch("tools.documents.generate._generate_into_workspace", side_effect=fake_generate),
        ):
            client.post("/api/documents/generate", json={"contentMarkdown": "# a", "filename": "a.docx"})
            client.post("/api/documents/doc-1/revise", json={"contentMarkdown": "# b"})

        assert seen == ["api", "revise"]


class TestRevisePassageStreamsItsProgress:
    """The 16-second silence (2026-09-09).

    The backend already emitted three progress labels; on the plain JSON route
    they were enqueued onto the LatencyTracker bound to the async context, and
    nothing binds one on an ordinary POST — so `get_current_tracker()` returned
    the NULL tracker and every label was written and dropped. The work existed;
    the transport did not.
    """

    def _frames(self, raw: str) -> list[dict]:
        import json

        return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]

    def test_the_labels_the_backend_emits_reach_the_client(self, client: TestClient):
        from tools.documents.generate import _progress

        async def fake_impl(doc_id, user, body):
            _progress("Preparing the document…")
            _progress("Rewriting the passage…")
            return _saved_response()

        with patch("tools.documents.generate._revise_passage_impl", side_effect=fake_impl):
            res = client.post(
                "/api/documents/doc1/revise-passage/stream",
                json={"anchorText": "a passage", "instruction": "shorter"},
            )

        assert res.status_code == 200
        frames = self._frames(res.text)
        labels = [f["label"] for f in frames if f["type"] == "progress"]
        assert "Preparing the document…" in labels, "progress the backend already emits must reach the UI"
        assert "Rewriting the passage…" in labels
        assert frames[-1]["type"] == "done", "the stream must end in a terminal frame"

    def test_a_typed_failure_arrives_as_a_visible_error_frame(self, client: TestClient):
        from fastapi import HTTPException

        async def boom(doc_id, user, body):
            raise HTTPException(
                status_code=422,
                detail={"code": "PASSAGE_NOT_FOUND", "message": "That passage could not be located."},
            )

        with patch("tools.documents.generate._revise_passage_impl", side_effect=boom):
            res = client.post(
                "/api/documents/doc1/revise-passage/stream",
                json={"anchorText": "gone", "instruction": "shorter"},
            )

        last = self._frames(res.text)[-1]
        assert last["type"] == "error"
        assert last["code"] == "PASSAGE_NOT_FOUND"
        assert "could not be located" in last["message"]

    def test_an_unexpected_failure_still_ends_the_stream(self, client: TestClient):
        """A stream that simply stops is the silence this endpoint removes."""

        async def boom(doc_id, user, body):
            raise RuntimeError("upstream exploded")

        with patch("tools.documents.generate._revise_passage_impl", side_effect=boom):
            res = client.post(
                "/api/documents/doc1/revise-passage/stream",
                json={"anchorText": "a", "instruction": "b"},
            )

        last = self._frames(res.text)[-1]
        assert last["type"] == "error", "never end without a terminal frame"
        assert last["status"] == 500
