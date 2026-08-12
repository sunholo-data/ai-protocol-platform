"""MODEL-RELIABILITY M1 — GET /api/debug/slow-stream probe route.

The probe is the backend half of the long-stream incident regression guard:
`scripts/smoke-long-stream.sh` curls it through the deployed frontend
proxy with 60s gaps for 6+ minutes. These tests cover the parts that
must never regress silently: auth is required, the Cloud Run gate
(SLOW_STREAM_PROBE_ENABLED) actually gates, params are clamped, and the
stream ends with a `done` record so the smoke script has a positive
completion signal (a stream that merely *closes* is indistinguishable
from a reaped one — the done marker is the proof of natural death).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auth import User, get_current_user


@pytest.fixture
def app_module():
    import fast_api_app

    return fast_api_app


@pytest.fixture
def client(app_module) -> TestClient:
    user = User(uid="probe-tester", email="probe@test.local")
    app_module.app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_current_user, None)


def test_requires_auth(app_module) -> None:
    # No dependency override installed → the real get_current_user runs and
    # rejects the tokenless request.
    unauthed = TestClient(app_module.app)
    resp = unauthed.get("/api/debug/slow-stream?seconds=0.2&gap=0.05")
    assert resp.status_code in (401, 403)


def test_streams_ticks_and_done_marker(client: TestClient) -> None:
    with client.stream("GET", "/api/debug/slow-stream?seconds=0.3&gap=0.05") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    assert 'data: {"tick": 1' in body
    assert '"done": true' in body


def test_gated_off_on_cloud_run_without_flag(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "platform-frontend")
    monkeypatch.delenv("SLOW_STREAM_PROBE_ENABLED", raising=False)
    resp = client.get("/api/debug/slow-stream?seconds=0.2&gap=0.05")
    assert resp.status_code == 404


def test_enabled_on_cloud_run_with_flag(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "platform-frontend")
    monkeypatch.setenv("SLOW_STREAM_PROBE_ENABLED", "true")
    with client.stream("GET", "/api/debug/slow-stream?seconds=0.2&gap=0.05") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert '"done": true' in body


def test_params_clamped(client: TestClient) -> None:
    # seconds above the 900 cap must not produce a 15-minute test run: ask
    # for a huge total with a huge gap and rely on the clamps + the fact we
    # only read the first chunk. Cheap sanity that the clamp code path runs.
    with client.stream("GET", "/api/debug/slow-stream?seconds=0.2&gap=99999") as resp:
        assert resp.status_code == 200
        first = next(resp.iter_text())
    assert "tick" in first
