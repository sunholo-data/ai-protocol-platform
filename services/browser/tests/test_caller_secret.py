"""Self-hosted caller check: with BROWSER_SHARED_SECRET set, a request must carry
it in X-Browser-Service-Secret — otherwise any local process could claim any user."""

from __future__ import annotations

import pytest

import gateway
from allowlist import NotAllowed


def test_no_secret_configured_needs_only_a_user(monkeypatch):
    monkeypatch.delenv("BROWSER_SHARED_SECRET", raising=False)
    ident = gateway._identity_from_headers({"X-Aitana-User": "mark"})
    assert ident.user_id == "mark"


def test_secret_configured_and_presented(monkeypatch):
    monkeypatch.setenv("BROWSER_SHARED_SECRET", "s" * 40)
    ident = gateway._identity_from_headers({"X-Aitana-User": "mark", "X-Browser-Service-Secret": "s" * 40})
    assert ident.user_id == "mark"


@pytest.mark.parametrize("presented", [None, "", "wrong"])
def test_secret_configured_but_missing_or_wrong_is_refused(monkeypatch, presented):
    monkeypatch.setenv("BROWSER_SHARED_SECRET", "s" * 40)
    headers = {"X-Aitana-User": "mark"}
    if presented is not None:
        headers["X-Browser-Service-Secret"] = presented
    with pytest.raises(NotAllowed, match="service secret"):
        gateway._identity_from_headers(headers)
