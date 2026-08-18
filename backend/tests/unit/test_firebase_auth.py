"""Unit tests for auth.firebase_auth with firebase_admin mocked.

Covers:
    - valid token → User (uid/email/domain/group_tags correct)
    - expired token → 401
    - invalid token → 401 (generic verify_id_token failure)
    - malformed header (not "Bearer ...") → 401
    - missing header → 401
    - empty bearer token → 401
    - domain extraction on @yourcompany.com / @gmail.com / no @
    - groupTags custom claim: missing / empty / populated → frozenset

We never generate real JWTs; we mock `firebase_admin.auth.verify_id_token`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from firebase_admin import auth as fb_auth
from pydantic import ValidationError
from starlette.requests import Request

from auth.firebase_auth import User, _extract_domain, _user_from_decoded_token, get_current_user


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI Request for dependency testing."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


async def test_missing_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_make_request())
    assert exc.value.status_code == 401
    assert "Missing" in exc.value.detail


async def test_malformed_header_returns_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_make_request({"Authorization": "Basic abc123"}))
    assert exc.value.status_code == 401
    assert "Malformed" in exc.value.detail


async def test_empty_bearer_returns_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_make_request({"Authorization": "Bearer "}))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# verify_id_token outcomes
# ---------------------------------------------------------------------------


def _decoded(**overrides: Any) -> dict[str, Any]:
    base = {"uid": "uid-123", "email": "owner@yourcompany.com"}
    base.update(overrides)
    return base


async def test_valid_token_returns_user() -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = _decoded(groupTags=["aitana-admin"])
        user = await get_current_user(_make_request({"Authorization": "Bearer good.jwt"}))
    assert isinstance(user, User)
    assert user.uid == "uid-123"
    assert user.email == "owner@yourcompany.com"
    assert user.domain == "yourcompany.com"
    assert user.group_tags == frozenset({"aitana-admin"})


async def test_expired_token_returns_401() -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.side_effect = fb_auth.ExpiredIdTokenError("expired", cause=Exception())
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_make_request({"Authorization": "Bearer expired.jwt"}))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token expired"


async def test_invalid_token_returns_401() -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.side_effect = fb_auth.InvalidIdTokenError("bad signature")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_make_request({"Authorization": "Bearer bad.jwt"}))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid token"


async def test_verify_raises_generic_error_returns_401() -> None:
    """firebase-admin can raise ValueError on shape errors; treat as 401."""
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.side_effect = ValueError("malformed payload")
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_make_request({"Authorization": "Bearer shape.jwt"}))
    assert exc.value.status_code == 401


async def test_pii_not_in_auth_failure_logs(caplog: pytest.LogCaptureFixture) -> None:
    """On auth failure we must not log the email or the raw token — only the error type."""
    caplog.set_level("INFO", logger="auth.firebase_auth")
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.side_effect = fb_auth.InvalidIdTokenError("oops")
        with pytest.raises(HTTPException):
            await get_current_user(_make_request({"Authorization": "Bearer sensitive.jwt"}))
    for rec in caplog.records:
        assert "sensitive.jwt" not in rec.getMessage()
        assert "mark@" not in rec.getMessage()


# ---------------------------------------------------------------------------
# Domain extraction (via helper and via end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected_domain"),
    [
        ("owner@yourcompany.com", "yourcompany.com"),
        ("someone@gmail.com", "gmail.com"),
        ("no-at-sign", ""),
        ("", ""),
        ("user@sub.example.co.uk", "sub.example.co.uk"),
    ],
)
def test_extract_domain(email: str, expected_domain: str) -> None:
    assert _extract_domain(email) == expected_domain


async def test_domain_populated_from_decoded_token() -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = _decoded(email="ceo@yourcompany.com")
        user = await get_current_user(_make_request({"Authorization": "Bearer x"}))
    assert user.domain == "yourcompany.com"


async def test_domain_empty_when_no_at() -> None:
    with patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify:
        mock_verify.return_value = _decoded(email="weird")
        user = await get_current_user(_make_request({"Authorization": "Bearer x"}))
    assert user.domain == ""


# ---------------------------------------------------------------------------
# groupTags custom claim
# ---------------------------------------------------------------------------


def test_group_tags_missing() -> None:
    user = _user_from_decoded_token({"uid": "u", "email": "m@a.com"})
    assert user.group_tags == frozenset()


def test_group_tags_empty_list() -> None:
    user = _user_from_decoded_token({"uid": "u", "email": "m@a.com", "groupTags": []})
    assert user.group_tags == frozenset()


def test_group_tags_populated() -> None:
    user = _user_from_decoded_token({"uid": "u", "email": "m@a.com", "groupTags": ["aitana-admin", "beta"]})
    assert user.group_tags == frozenset({"aitana-admin", "beta"})


def test_group_tags_is_frozen() -> None:
    """User is immutable — group_tags swap must raise Pydantic ValidationError."""
    user = _user_from_decoded_token({"uid": "u", "email": "m@a.com", "groupTags": ["x"]})
    with pytest.raises(ValidationError):
        user.group_tags = frozenset({"mutated"})  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Domain-derived group tags
# ---------------------------------------------------------------------------


async def test_derived_group_tags_unioned_with_jwt_claim() -> None:
    """acmeenergy.com user with no JWT groupTags gets ONE via clients/{domain}."""
    with (
        patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify,
        patch("db.clients.resolve_derived_group_tags", return_value=frozenset({"ONE"})),
    ):
        mock_verify.return_value = _decoded(email="alice@acmeenergy.com")
        user = await get_current_user(_make_request({"Authorization": "Bearer good.jwt"}))
    assert user.domain == "acmeenergy.com"
    assert user.group_tags == frozenset({"ONE"})


async def test_derived_tags_unioned_with_existing_jwt_tags() -> None:
    """JWT-provided tags survive — derived tags add to them, never replace."""
    with (
        patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify,
        patch("db.clients.resolve_derived_group_tags", return_value=frozenset({"ONE"})),
    ):
        mock_verify.return_value = _decoded(email="alice@acmeenergy.com", groupTags=["aitana-admin"])
        user = await get_current_user(_make_request({"Authorization": "Bearer good.jwt"}))
    assert user.group_tags == frozenset({"ONE", "aitana-admin"})


async def test_no_derived_tags_when_domain_unmapped() -> None:
    """gmail.com user gets nothing added — resolve returns empty frozenset."""
    with (
        patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify,
        patch("db.clients.resolve_derived_group_tags", return_value=frozenset()),
    ):
        mock_verify.return_value = _decoded(email="alice@gmail.com")
        user = await get_current_user(_make_request({"Authorization": "Bearer good.jwt"}))
    assert user.group_tags == frozenset()


async def test_derived_tags_lookup_failure_does_not_block_auth() -> None:
    """Firestore unavailability must not 500 the auth path — user keeps JWT tags only."""
    with (
        patch("auth.firebase_auth.fb_auth.verify_id_token") as mock_verify,
        patch("db.clients.resolve_derived_group_tags", side_effect=RuntimeError("firestore down")),
    ):
        mock_verify.return_value = _decoded(email="alice@acmeenergy.com", groupTags=["x"])
        user = await get_current_user(_make_request({"Authorization": "Bearer good.jwt"}))
    assert user.group_tags == frozenset({"x"})
