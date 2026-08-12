"""Tests for GET /api/documents/{doc_id}/thumbnail (v6.6.0).

Standardised page-1 / image thumbnail for imported docs — same renderer + cache
as the bucket thumbnail route, gated by document ownership.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_USER_A = User(uid="user_a", email="alice@example.com", domain="example.com")
_USER_B = User(uid="user_b", email="bob@example.com", domain="example.com")

_DOC = {
    "id": "doc_1",
    "userId": "user_a",
    "originalFilename": "sample.pdf",
    "sourceFormat": "pdf",
    "sourceUrl": "gs://my-bucket/uploads/sample.pdf",
    "parseStatus": "parsed",
}


def _app_for(user: User) -> TestClient:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _one_page_pdf() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (420, 594), "white").save(buf, "PDF")
    return buf.getvalue()


def _png_image() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "steelblue").save(buf, "PNG")
    return buf.getvalue()


def _mock_storage(data: bytes) -> MagicMock:
    blob = MagicMock()
    blob.download_as_bytes.return_value = data
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    return MagicMock(return_value=client)


def test_thumbnail_anon_401() -> None:
    from tools.documents.routes import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/documents/doc_1/thumbnail").status_code == 401


def test_thumbnail_owner_renders_png() -> None:
    client = _app_for(_USER_A)
    with (
        patch("tools.documents.routes._get_firestore_doc", return_value=dict(_DOC)),
        patch("google.cloud.storage.Client", _mock_storage(_one_page_pdf())),
    ):
        resp = client.get("/api/documents/doc_1/thumbnail?width=300")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_image_doc_renders_png() -> None:
    client = _app_for(_USER_A)
    doc = {**_DOC, "sourceFormat": "png", "sourceUrl": "gs://my-bucket/logo.png"}
    with (
        patch("tools.documents.routes._get_firestore_doc", return_value=doc),
        patch("google.cloud.storage.Client", _mock_storage(_png_image())),
    ):
        resp = client.get("/api/documents/doc_1/thumbnail?width=200")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_non_owner_403() -> None:
    client = _app_for(_USER_B)
    with patch("tools.documents.routes._get_firestore_doc", return_value=dict(_DOC)):
        resp = client.get("/api/documents/doc_1/thumbnail")
    assert resp.status_code == 403


def test_thumbnail_missing_doc_404() -> None:
    client = _app_for(_USER_A)
    with patch("tools.documents.routes._get_firestore_doc", return_value=None):
        resp = client.get("/api/documents/missing/thumbnail")
    assert resp.status_code == 404


def test_thumbnail_unsupported_format_415() -> None:
    client = _app_for(_USER_A)
    doc = {**_DOC, "sourceFormat": "txt", "sourceUrl": "gs://my-bucket/notes.txt"}
    with patch("tools.documents.routes._get_firestore_doc", return_value=doc):
        resp = client.get("/api/documents/doc_1/thumbnail")
    assert resp.status_code == 415


@pytest.mark.parametrize("bad_url", [None, "", "https://not-gcs/x.pdf"])
def test_thumbnail_requires_gcs_source(bad_url) -> None:
    client = _app_for(_USER_A)
    doc = {**_DOC, "sourceUrl": bad_url}
    with patch("tools.documents.routes._get_firestore_doc", return_value=doc):
        resp = client.get("/api/documents/doc_1/thumbnail")
    assert resp.status_code == 404
