"""Tests for GET /api/auth/whoami.

Mocks `firebase_admin.auth.verify_id_token` — no real JWTs are minted.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.routes import router as auth_router


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_router)
    return TestClient(app)


def test_whoami_no_auth_401(client: TestClient) -> None:
    resp = client.get("/api/auth/whoami")
    assert resp.status_code == 401


def test_whoami_valid_token_echoes_identity(client: TestClient) -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = {
            "uid": "mark-uid",
            "email": "owner@yourcompany.com",
            "email_verified": True,
            "groupTags": ["aitana-admin", "ops"],
        }
        resp = client.get("/api/auth/whoami", headers={"Authorization": "Bearer good.jwt"})
    assert resp.status_code == 200
    assert resp.json() == {
        "uid": "mark-uid",
        "email": "owner@yourcompany.com",
        "domain": "yourcompany.com",
        "groupTags": ["aitana-admin", "ops"],
        "displayName": None,
    }


def test_whoami_missing_group_tags_returns_empty_list(client: TestClient) -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = {"uid": "other-uid", "email": "other@example.com", "email_verified": True}
        resp = client.get("/api/auth/whoami", headers={"Authorization": "Bearer good.jwt"})
    assert resp.status_code == 200
    assert resp.json() == {
        "uid": "other-uid",
        "email": "other@example.com",
        "domain": "example.com",
        "groupTags": [],
        "displayName": None,
    }


def test_whoami_unverified_email_is_403(client: TestClient) -> None:
    """a self-registered account for someone else's address never authenticates."""
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = {"uid": "squat", "email": "victim@example.com", "email_verified": False}
        resp = client.get("/api/auth/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"
