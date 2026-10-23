"""an unverified Firebase email must never be trusted.

Firebase self sign-up lets anyone register an email/password account for any
address (e.g. a colleague's @example.com). The token then carries
``email_verified: false``. These tests pin that such a caller is 403'd before
the domain allowlist or any email-keyed tool (Severa) sees the address.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import auth
from auth import _enforce_verified_email
from auth.firebase_auth import User, _user_from_decoded_token


def _user(*, email: str = "victim@example.com", verified: bool, auth_mode: str = "firebase") -> User:
    return User(uid="u", email=email, domain="example.com", auth_mode=auth_mode, email_verified=verified)


def test_unverified_email_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    with pytest.raises(HTTPException) as exc:
        _enforce_verified_email(_user(verified=False))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "EMAIL_NOT_VERIFIED"


def test_verified_email_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    _enforce_verified_email(_user(verified=True))


def test_emailless_firebase_user_is_not_this_gates_business(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    _enforce_verified_email(_user(email="", verified=False))


def test_non_firebase_modes_and_local_mode_are_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    # Group-id, tailnet and machine principals never carry email_verified.
    for mode in (auth._GROUP_AUTH_MODE, auth._TAILNET_AUTH_MODE, auth._MACHINE_AUTH_MODE):
        _enforce_verified_email(_user(verified=False, auth_mode=mode))
    monkeypatch.setenv("LOCAL_MODE", "1")
    _enforce_verified_email(_user(verified=False))


@pytest.mark.parametrize(("claim", "expected"), [(True, True), (False, False), (None, False), ("true", False)])
def test_claim_is_read_strictly(claim: object, expected: bool) -> None:
    decoded = {"uid": "u", "email": "a@example.com"}
    if claim is not None:
        decoded["email_verified"] = claim
    assert _user_from_decoded_token(decoded).email_verified is expected


async def test_gate_runs_even_with_the_domain_allowlist_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prod runs AUTH_REQUIRE_KNOWN_DOMAIN=0 — the gate must not hang off it."""
    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "0")
    with patch.object(auth, "_resolve_user", AsyncMock(return_value=_user(verified=False))):
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user(MagicMock())
    assert exc.value.detail["code"] == "EMAIL_NOT_VERIFIED"
