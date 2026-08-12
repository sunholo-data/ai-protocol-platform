"""Integration test for the `get_current_user` FastAPI dependency.

We build a minimal FastAPI app with a public endpoint (no dependency) and a
protected endpoint (Depends(get_current_user)) and drive them with TestClient.

`firebase_admin.auth.verify_id_token` is mocked — no real JWTs are created.
M1 ships only the dependency; no real endpoint in `fast_api_app.py` is yet
protected, so this test imports `get_current_user` directly to exercise it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.firebase_auth import User, get_current_user


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()

    @app.get("/public")
    def public_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/protected")
    def protected_endpoint(user: User = Depends(get_current_user)) -> dict[str, str]:  # noqa: B008
        return {"uid": user.uid, "domain": user.domain}

    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_public_endpoint_no_auth_200(client: TestClient) -> None:
    """Endpoints without Depends(get_current_user) remain reachable without a token."""
    resp = client.get("/public")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_protected_endpoint_no_auth_401(client: TestClient) -> None:
    """Protected endpoint 401s when no Authorization header is sent."""
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_protected_endpoint_invalid_auth_401(client: TestClient) -> None:
    """Protected endpoint 401s when the token is rejected."""
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        from firebase_admin import auth as fb_auth  # local import to build exception

        mock_verify.side_effect = fb_auth.InvalidIdTokenError("bad")
        resp = client.get("/protected", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_protected_endpoint_valid_token_200(client: TestClient) -> None:
    """Mock-verified token → 200 and uid/domain land on the User."""
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = {
            "uid": "mark-uid",
            "email": "owner@yourcompany.com",
            "groupTags": ["aitana-admin"],
        }
        resp = client.get("/protected", headers={"Authorization": "Bearer good.jwt"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"uid": "mark-uid", "domain": "yourcompany.com"}
