"""The direct `browse` tool sends BROWSER_SERVICE_SECRET when set, so a
self-hosted browser service (BROWSER_SHARED_SECRET) can refuse other callers."""

from __future__ import annotations

import importlib


def _headers(monkeypatch, secret: str | None):
    monkeypatch.setenv("BROWSER_SERVICE_URL", "http://127.0.0.1:8949")
    if secret is None:
        monkeypatch.delenv("BROWSER_SERVICE_SECRET", raising=False)
    else:
        monkeypatch.setenv("BROWSER_SERVICE_SECRET", secret)
    import tools.browser as browser

    importlib.reload(browser)
    return browser._auth_headers("alice@example.com", "sess-1")


def test_secret_sent_when_configured(monkeypatch):
    headers = _headers(monkeypatch, "s" * 40)
    assert headers["X-Browser-Service-Secret"] == "s" * 40
    assert "Authorization" not in headers  # loopback http: no ID token


def test_no_secret_header_when_unset(monkeypatch):
    assert "X-Browser-Service-Secret" not in _headers(monkeypatch, None)
