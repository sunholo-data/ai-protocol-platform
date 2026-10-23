"""GET /api/documents/{doc_id}/download — DOC-WORKBENCH P2.

Users create documents to use offline, so the file has to leave the browser as a
file. Two things this pins that have bitten before:

* **The filename header.** HTTP headers are latin-1, so a filename containing an
  en-dash or an umlaut 500s a naive ``Content-Disposition``. The route goes
  through ``_content_disposition`` (RFC 6266), which emits ``filename*=UTF-8``.
  The fixture below writes those characters as escapes on purpose: the literal
  glyphs are what ruff flags as ambiguous, and they are exactly what is under
  test, so escaping keeps both the intent and the linter honest.
* **Never a public URL.** A generated offer is customer-commercial content
  derived from customer context. It is streamed through an authenticated route
  that re-checks ownership, never a signed or public GCS URL — the leak
  CLAUDE.md's security rule exists to prevent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_OWNER = User(uid="user1", email="alice@example.com", domain="example.com")

_DOC = {
    "userId": "user1",
    "sourceUrl": "gs://client-bucket/users/user1/docs/created/Offer.docx",
    "sourceFormat": "docx",
    "originalFilename": "Offer.docx",
}

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture()
def client() -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _OWNER
    return TestClient(app)


def _gcs(data: bytes = b"PK\x03\x04docx-bytes"):
    blob = MagicMock()
    blob.download_as_bytes.return_value = data
    storage = MagicMock()
    storage.Client.return_value.bucket.return_value.blob.return_value = blob
    return storage


def _with_doc(doc: dict | None, data: bytes = b"PK\x03\x04docx-bytes"):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("tools.documents.routes._get_firestore_doc", return_value=doc))
    stack.enter_context(patch("google.cloud.storage", _gcs(data)))
    return stack


class TestDownload:
    def test_streams_the_bytes_as_an_attachment(self, client: TestClient):
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/download")

        assert resp.status_code == 200
        assert resp.content == b"PK\x03\x04docx-bytes"
        assert resp.headers["content-type"].startswith(_DOCX_MIME)
        assert resp.headers["content-disposition"].startswith("attachment")

    def test_a_non_latin1_filename_does_not_500(self, client: TestClient):
        """The recurring Content-Disposition trap: headers are latin-1."""
        # U+2013 EN DASH + umlauts — none of which encode as latin-1.
        doc = {**_DOC, "originalFilename": "Angebot \u2013 Gr\u00f6\u00dfe & R\u00f6hren.docx"}
        with _with_doc(doc):
            resp = client.get("/api/documents/doc1/download")

        assert resp.status_code == 200
        cd = resp.headers["content-disposition"]
        assert "filename*=UTF-8''" in cd
        # The ascii fallback must still be a usable latin-1 header value.
        cd.encode("latin-1")

    def test_never_hands_out_a_public_or_signed_url(self, client: TestClient):
        """Bytes are proxied; no storage.googleapis.com anywhere in the response."""
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/download")

        blob = str(resp.headers)
        assert "storage.googleapis.com" not in blob
        assert "gs://" not in blob
        assert "X-Goog-Signature" not in blob

    def test_download_is_not_cached(self, client: TestClient):
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/download")
        assert "no-store" in resp.headers.get("cache-control", "")


class TestAccessControl:
    def test_a_non_owner_gets_403(self, client: TestClient):
        with _with_doc({**_DOC, "userId": "someone-else"}):
            resp = client.get("/api/documents/doc1/download")
        assert resp.status_code == 403

    def test_a_non_owner_never_reaches_gcs(self, client: TestClient):
        """The refusal must come BEFORE any byte is read."""
        storage = _gcs()
        with (
            patch("tools.documents.routes._get_firestore_doc", return_value={**_DOC, "userId": "other"}),
            patch("google.cloud.storage", storage),
        ):
            resp = client.get("/api/documents/doc1/download")

        assert resp.status_code == 403
        assert not storage.Client.called

    def test_unknown_document_is_404(self, client: TestClient):
        with _with_doc(None):
            resp = client.get("/api/documents/nope/download")
        assert resp.status_code == 404

    def test_a_document_with_no_gcs_source_is_404(self, client: TestClient):
        with _with_doc({**_DOC, "sourceUrl": ""}):
            resp = client.get("/api/documents/doc1/download")
        assert resp.status_code == 404


class TestSharedWithPreview:
    """Both routes go through one helper so their access gate cannot drift."""

    def test_preview_still_serves_inline(self, client: TestClient):
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/preview")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"].startswith("inline")

    def test_both_routes_refuse_the_same_non_owner(self, client: TestClient):
        for route in ("preview", "download"):
            with _with_doc({**_DOC, "userId": "other"}):
                assert client.get(f"/api/documents/doc1/{route}").status_code == 403

    def test_unknown_format_falls_back_to_octet_stream(self, client: TestClient):
        with _with_doc({**_DOC, "sourceFormat": "wat", "originalFilename": "x.wat"}):
            resp = client.get("/api/documents/doc1/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/octet-stream")


class TestPreviewFreshness:
    """A restyle re-ingests IN PLACE, so the preview URL outlives its bytes.

    Reported 2026-09-04: applying a template changed nothing on screen and the
    Formatting panel reset to "Plain (no template)", so the whole feature read
    as broken. It was not — the theme, header, footer and page setup were all
    carried into the stored .docx every time. The browser was answering the
    post-restyle refetch out of its own cache, because the route served
    ``max-age=300`` on a URL that never varies.

    Both halves are pinned here: an un-versioned request must not be cacheable,
    and a versioned one must be (otherwise the fix is a permanent refetch of
    every document on every render).
    """

    def test_an_unversioned_preview_is_never_cached(self, client: TestClient):
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/preview")
        assert resp.status_code == 200
        cache = resp.headers["cache-control"]
        assert "no-cache" in cache and "must-revalidate" in cache
        assert "max-age=300" not in cache, "a stale preview is how a working restyle looks broken"

    def test_a_versioned_preview_may_be_cached(self, client: TestClient):
        with _with_doc(_DOC):
            resp = client.get("/api/documents/doc1/preview?v=2026-09-04T10%3A11%3A12")
        assert resp.status_code == 200
        assert "max-age=300" in resp.headers["cache-control"]

    def test_the_version_token_does_not_change_the_bytes(self, client: TestClient):
        """`v` is a cache key, not an argument — it must never select content."""
        with _with_doc(_DOC):
            bare = client.get("/api/documents/doc1/preview")
        with _with_doc(_DOC):
            versioned = client.get("/api/documents/doc1/preview?v=anything")
        assert bare.content == versioned.content

    def test_the_ownership_gate_still_applies_to_a_versioned_request(self, client: TestClient):
        with _with_doc({**_DOC, "userId": "other"}):
            assert client.get("/api/documents/doc1/preview?v=x").status_code == 403
