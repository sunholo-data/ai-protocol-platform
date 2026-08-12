"""Unit tests for admin.auth._assert_caller_is_service_account.

Covers the token-verification and allowlist logic. google.oauth2.id_token is
mocked so no network I/O or real GCP credentials are required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from admin.auth import _assert_caller_is_service_account


def _make_request(token: str = "tok") -> object:
    """Minimal request stub with an Authorization header."""
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/admin/seed-platform-skills",
        "headers": Headers({"authorization": f"Bearer {token}"}).raw,
    }
    return Request(scope)


def _patch_token(claims: dict):
    return patch("admin.auth.id_token.verify_oauth2_token", return_value=claims)


def _patch_allowed(emails: set[str]):
    return patch("admin.auth._allowed_emails", return_value=emails)


# --- missing / empty auth header ---


def test_missing_auth_header_raises_403():
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": Headers({}).raw,
    }
    req = Request(scope)
    with pytest.raises(HTTPException) as exc:
        _assert_caller_is_service_account(req)
    assert exc.value.status_code == 403


# --- email claim absent → diagnostic log + 403 ---


def test_missing_email_claim_falls_through_to_firebase_and_403s():
    """When the token verifies as a Google ID token but has no 'email' claim,
    the SA path bails and we try the Firebase path. With no Firebase user
    behind the token, that also fails → 403."""
    claims = {"sub": "some-sa@iam.gserviceaccount.com", "iss": "accounts.google.com"}

    with (
        _patch_token(claims),
        _patch_allowed({"some-sa@iam.gserviceaccount.com"}),
        patch("firebase_admin.auth.verify_id_token", side_effect=Exception("not firebase")),
    ):
        with pytest.raises(HTTPException) as exc:
            _assert_caller_is_service_account(_make_request())

    assert exc.value.status_code == 403


def test_unverified_email_falls_through_to_firebase_and_403s():
    claims = {"email": "sa@example.com", "email_verified": False}
    with (
        _patch_token(claims),
        _patch_allowed({"sa@example.com"}),
        patch("firebase_admin.auth.verify_id_token", side_effect=Exception("not firebase")),
    ):
        with pytest.raises(HTTPException) as exc:
            _assert_caller_is_service_account(_make_request())
    assert exc.value.status_code == 403


# --- happy path ---


def test_verified_email_in_allowlist_returns_email():
    claims = {"email": "cb-sa@my-project.iam.gserviceaccount.com", "email_verified": True}
    with _patch_token(claims), _patch_allowed({"cb-sa@my-project.iam.gserviceaccount.com"}):
        result = _assert_caller_is_service_account(_make_request())
    assert result == "cb-sa@my-project.iam.gserviceaccount.com"


# --- email not in allowlist ---


def test_email_not_in_allowlist_falls_through_to_firebase_and_403s():
    claims = {"email": "intruder@evil.com", "email_verified": True}
    with (
        _patch_token(claims),
        _patch_allowed({"legit-sa@project.iam.gserviceaccount.com"}),
        patch("firebase_admin.auth.verify_id_token", side_effect=Exception("not firebase")),
    ):
        with pytest.raises(HTTPException) as exc:
            _assert_caller_is_service_account(_make_request())
    assert exc.value.status_code == 403


# --- Firebase user token with aitana-admin group tag ---


def test_firebase_user_with_aitana_admin_returns_email():
    """An ops operator signed in via Firebase whose JWT carries the
    aitana-admin group tag passes the admin gate without needing to be in
    ADMIN_SEED_ALLOWED_SAS."""
    decoded = {
        "uid": "mark-uid",
        "email": "owner@yourcompany.com",
        "groupTags": ["aitana-admin", "engineer"],
    }
    with (
        _patch_token({"email": "irrelevant@x.com", "email_verified": True}),
        _patch_allowed(set()),  # no SAs allowlisted
        patch("firebase_admin.get_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        result = _assert_caller_is_service_account(_make_request())
    assert result == "owner@yourcompany.com"


def test_firebase_user_without_admin_tag_403s():
    """Signed-in Firebase users WITHOUT aitana-admin in groupTags can't
    call admin endpoints, even if their token is otherwise valid."""
    decoded = {
        "uid": "alice-uid",
        "email": "alice@example.com",
        "groupTags": ["workshop-attendee"],
    }
    with (
        _patch_token({"email": "irrelevant@x.com", "email_verified": False}),
        _patch_allowed(set()),
        patch("firebase_admin.get_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        with pytest.raises(HTTPException) as exc:
            _assert_caller_is_service_account(_make_request())
    assert exc.value.status_code == 403
