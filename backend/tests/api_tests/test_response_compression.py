"""JSON responses are gzipped when the client asks; the AG-UI stream never is.

A chat history, an activity feed (350 KB) or a large document (1.8 MB) crossed
the network as raw JSON (2026-10-30). The web proxy forwards Accept-Encoding
and passes compressed bytes through untouched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware


def test_large_json_is_gzipped_on_request_and_plain_otherwise():
    import fast_api_app

    client = TestClient(fast_api_app.app)
    gz = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert gz.status_code == 200 and gz.headers.get("content-encoding") == "gzip"
    assert gz.json()["info"]["title"]  # decodes to the same document
    plain = client.get("/openapi.json", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers


def test_event_streams_stay_excluded():
    """The live AG-UI stream must never be buffered for compression: keep
    Starlette's default exclusions (text/event-stream among them)."""
    import fast_api_app

    (gzip,) = [m for m in fast_api_app.app.user_middleware if m.cls is GZipMiddleware]
    assert "exclude_content_types" not in gzip.kwargs
