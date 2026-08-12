"""Unit tests for the v6.18.0 email-domain allowlist (Gap B).

Covers `auth._enforce_domain_allowlist` and its helpers: the flag gate, operator
domains, mapped-tenant lookup, and the exemptions (LOCAL_MODE, anonymous group-id)
that must never break. The load-bearing case is: flag ON + unmapped Firebase
domain → 403 DOMAIN_NOT_PERMITTED.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from auth import (
    _domain_allowed,
    _enforce_domain_allowlist,
    _operator_domains,
    _require_known_domain,
)
from auth.firebase_auth import User


def _user(domain: str, *, auth_mode: str = "firebase", email: str = "x@x.com") -> User:
    return User(uid="u", email=email, domain=domain, auth_mode=auth_mode)


# --- flag + operator-domain parsing ---------------------------------------


def test_require_known_domain_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_REQUIRE_KNOWN_DOMAIN", raising=False)
    assert _require_known_domain() is False


def test_require_known_domain_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    assert _require_known_domain() is True


def test_operator_domains_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_OPERATOR_DOMAINS", raising=False)
    assert _operator_domains() == frozenset({"yourcompany.com", "yourcompany.test"})


def test_operator_domains_csv_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "Foo.com, bar.org ")
    assert _operator_domains() == frozenset({"foo.com", "bar.org"})


# --- _domain_allowed -------------------------------------------------------


def test_operator_domain_allowed_without_firestore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_OPERATOR_DOMAINS", raising=False)
    # If Firestore were consulted this would blow up; operator check must short-circuit.
    with patch("db.clients.get_client_cached", side_effect=AssertionError("must not hit Firestore")):
        assert _domain_allowed(_user("yourcompany.com")) is True


def test_mapped_tenant_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "yourcompany.com")
    with patch("db.clients.get_client_cached", return_value=object()):
        assert _domain_allowed(_user("acme-energy.example")) is True


def test_unmapped_domain_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "yourcompany.com")
    with patch("db.clients.get_client_cached", return_value=None):
        assert _domain_allowed(_user("evil.com")) is False


def test_empty_domain_denied() -> None:
    assert _domain_allowed(_user("")) is False


# --- _enforce_domain_allowlist (the gate) ----------------------------------


def test_gate_off_allows_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_REQUIRE_KNOWN_DOMAIN", raising=False)
    _enforce_domain_allowlist(_user("evil.com"))  # no raise


def test_gate_on_unmapped_domain_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "yourcompany.com")
    with patch("db.clients.get_client_cached", return_value=None), patch("auth.is_local_mode", return_value=False):
        with pytest.raises(HTTPException) as exc:
            _enforce_domain_allowlist(_user("evil.com"))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "DOMAIN_NOT_PERMITTED"


def test_gate_on_operator_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "yourcompany.com")
    with patch("auth.is_local_mode", return_value=False):
        _enforce_domain_allowlist(_user("yourcompany.com"))  # no raise


def test_gate_on_mapped_tenant_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    monkeypatch.setenv("AUTH_OPERATOR_DOMAINS", "yourcompany.com")
    with patch("db.clients.get_client_cached", return_value=object()), patch("auth.is_local_mode", return_value=False):
        _enforce_domain_allowlist(_user("acme-energy.example"))  # no raise


def test_local_mode_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployed context never runs LOCAL_MODE, but the dev/fork stub must pass."""
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    with patch("auth.is_local_mode", return_value=True):
        _enforce_domain_allowlist(_user("local"))  # no raise, no Firestore


def test_group_id_auth_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anonymous workshop identities have no email domain and are exempt."""
    monkeypatch.setenv("AUTH_REQUIRE_KNOWN_DOMAIN", "1")
    with patch("auth.is_local_mode", return_value=False):
        _enforce_domain_allowlist(_user("", auth_mode="anonymous_group_id"))  # no raise
