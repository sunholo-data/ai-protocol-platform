"""Tests for backend/tools/media_utils.py — PDF info endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


def test_is_allowed_url_accepts_gcs_https():
    from tools.media_utils import _is_allowed_url

    assert _is_allowed_url("https://storage.googleapis.com/bucket/file.pdf")
    assert _is_allowed_url("https://storage.cloud.google.com/bucket/file.pdf")


def test_is_allowed_url_rejects_non_gcs():
    from tools.media_utils import _is_allowed_url

    assert not _is_allowed_url("https://example.com/file.pdf")
    assert not _is_allowed_url("http://storage.googleapis.com/bucket/file.pdf")  # http not https
    assert not _is_allowed_url("javascript:alert(1)")
    assert not _is_allowed_url("")


def test_extract_filename_from_gcs_url():
    from tools.media_utils import _extract_filename

    url = "https://storage.googleapis.com/bucket/users/uid/docs/Q1-Report.pdf"
    assert _extract_filename(url) == "Q1-Report.pdf"


def test_extract_filename_falls_back_on_empty_path():
    from tools.media_utils import _extract_filename

    assert _extract_filename("https://storage.googleapis.com/") == "document.pdf"


def test_count_pdf_pages_from_url_parses_count_field():
    from tools.media_utils import count_pdf_pages_from_url

    pdf_bytes = b"%PDF-1.4\n/Count 27\nrest of file"
    mock_response = MagicMock()
    mock_response.status_code = 206
    mock_response.content = pdf_bytes

    with patch("tools.media_utils.httpx.get", return_value=mock_response):
        assert count_pdf_pages_from_url("https://storage.googleapis.com/b/f.pdf") == 27


def test_count_pdf_pages_from_url_parses_n_field():
    from tools.media_utils import count_pdf_pages_from_url

    pdf_bytes = b"%PDF-1.4\n/N 5\nsome data"
    mock_response = MagicMock()
    mock_response.status_code = 206
    mock_response.content = pdf_bytes

    with patch("tools.media_utils.httpx.get", return_value=mock_response):
        assert count_pdf_pages_from_url("https://storage.googleapis.com/b/f.pdf") == 5


def test_count_pdf_pages_returns_none_on_non_pdf():
    from tools.media_utils import count_pdf_pages_from_url

    mock_response = MagicMock()
    mock_response.status_code = 200  # Not 206
    mock_response.content = b"<html>Not a PDF</html>"

    with patch("tools.media_utils.httpx.get", return_value=mock_response):
        assert count_pdf_pages_from_url("https://storage.googleapis.com/b/f.pdf") is None


def test_count_pdf_pages_returns_none_on_exception():
    from tools.media_utils import count_pdf_pages_from_url

    with patch("tools.media_utils.httpx.get", side_effect=Exception("timeout")):
        assert count_pdf_pages_from_url("https://storage.googleapis.com/b/f.pdf") is None


# ---------------------------------------------------------------------------
# Route tests — FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with auth dependency overridden."""
    from fastapi import FastAPI

    from auth import User
    from tools.media_utils import router

    app = FastAPI()
    app.include_router(router)

    # Override auth dependency so tests don't need Firebase
    from auth import get_current_user

    fake_user = User(uid="test-uid", email="test@example.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user

    return TestClient(app)


def test_pdf_info_route_returns_200_with_pages(client: TestClient):
    pdf_bytes = b"%PDF-1.4\n/Count 10\ndata"
    mock_response = MagicMock()
    mock_response.status_code = 206
    mock_response.content = pdf_bytes

    with patch("tools.media_utils.httpx.get", return_value=mock_response):
        resp = client.get(
            "/api/media/pdf-info",
            params={"url": "https://storage.googleapis.com/bucket/file.pdf"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "file.pdf"
    assert data["pages"] == 10


def test_pdf_info_route_returns_pages_null_on_unreadable(client: TestClient):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"not a pdf"

    with patch("tools.media_utils.httpx.get", return_value=mock_response):
        resp = client.get(
            "/api/media/pdf-info",
            params={"url": "https://storage.googleapis.com/bucket/file.pdf"},
        )
    assert resp.status_code == 200
    assert resp.json()["pages"] is None


def test_pdf_info_route_rejects_non_gcs_url(client: TestClient):
    resp = client.get(
        "/api/media/pdf-info",
        params={"url": "https://evil.com/file.pdf"},
    )
    assert resp.status_code == 400


def test_pdf_info_route_requires_auth():
    """Without auth override, the route should return 401."""
    from fastapi import FastAPI

    from tools.media_utils import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.get(
        "/api/media/pdf-info",
        params={"url": "https://storage.googleapis.com/bucket/file.pdf"},
    )
    assert resp.status_code == 401
