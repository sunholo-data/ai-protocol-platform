"""``/api/client-logs`` is wired into the real app, and wired BEHIND auth.

``tests/unit/test_client_logs.py`` exercises the router on a bare FastAPI with
the auth dependency overridden. That proves the handler; it cannot prove the
registration — a router included on the real app without its dependency, or
not included at all, passes every unit test and fails only for a customer.
This is the one assertion that has to be made against ``fast_api_app.app``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fast_api_app import app


def test_client_logs_is_registered_and_refuses_anonymous_callers():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/client-logs", json={"events": []})
    # 404 would mean not registered; 202 would mean registered without auth.
    assert r.status_code == 401, r.text


def test_client_logs_is_post_only():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/client-logs").status_code in (404, 405)
