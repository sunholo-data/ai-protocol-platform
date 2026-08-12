"""Unit tests for auth.permissions — tool-class permission enforcement.

The Firestore layer (db.firestore.get_document) is mocked so these are pure
unit tests with no network I/O. Tests cover the lookup-order rule table:

    user-level wins over domain
    domain-only grants
    wildcard + denied-list
    cache-hit skips Firestore
    cache-expiry re-queries Firestore
    no-rule → deny
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth.permissions import (
    _CACHE_TTL,
    COLLECTION,
    ToolPermissionDenied,
    can_use_tool,
    clear_cache,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Flush the permission cache before every test."""
    clear_cache()
    yield
    clear_cache()


def _mock_docs(docs: dict[str, dict | None]):
    """Patch get_document to return docs keyed by document ID."""

    def _get(collection: str, doc_id: str):
        assert collection == COLLECTION
        return docs.get(doc_id)

    return patch("auth.permissions.fs.get_document", side_effect=_get)


# ---------------------------------------------------------------------------
# Lookup order
# ---------------------------------------------------------------------------


def test_user_wins_over_domain():
    """User-level doc says yes, domain says no — user wins."""
    docs = {
        "owner@yourcompany.com": {"type": "user", "tools": ["search"], "denied": []},
        "yourcompany.com": {"type": "domain", "tools": [], "denied": ["search"]},
    }
    with _mock_docs(docs):
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "search") is True


def test_user_deny_wins_over_domain_allow():
    """User-level deny overrides domain-level allow."""
    docs = {
        "owner@yourcompany.com": {"type": "user", "tools": ["*"], "denied": ["dangerous_tool"]},
        "yourcompany.com": {"type": "domain", "tools": ["dangerous_tool"], "denied": []},
    }
    with _mock_docs(docs):
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "dangerous_tool") is False


def test_domain_only():
    """No user doc — falls through to domain."""
    docs = {
        "yourcompany.com": {"type": "domain", "tools": ["search", "code_exec"], "denied": []},
    }
    with _mock_docs(docs):
        assert can_use_tool("alice@yourcompany.com", "yourcompany.com", "search") is True
        clear_cache()
        assert can_use_tool("alice@yourcompany.com", "yourcompany.com", "email") is False


def test_wildcard_grants():
    """No user or domain doc — falls through to wildcard."""
    docs = {
        "*": {"type": "wildcard", "tools": ["*"], "denied": []},
    }
    with _mock_docs(docs):
        assert can_use_tool("stranger@example.com", "example.com", "any_tool") is True


def test_wildcard_with_denied_list():
    """Wildcard grants all except denied tools."""
    docs = {
        "*": {"type": "wildcard", "tools": ["*"], "denied": ["admin_tool"]},
    }
    with _mock_docs(docs):
        assert can_use_tool("stranger@example.com", "example.com", "search") is True
        clear_cache()
        assert can_use_tool("stranger@example.com", "example.com", "admin_tool") is False


def test_no_rule_denies():
    """No user, domain, or wildcard doc → deny."""
    with _mock_docs({}):
        assert can_use_tool("nobody@nowhere.com", "nowhere.com", "search") is False


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_no_firestore():
    """Second call with same (email, tool) should not hit Firestore."""
    docs = {
        "owner@yourcompany.com": {"type": "user", "tools": ["search"], "denied": []},
    }
    with _mock_docs(docs) as mock_get:
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "search") is True
        assert mock_get.call_count == 1

        # Second call — cached.
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "search") is True
        assert mock_get.call_count == 1  # still 1


def test_cache_expiry(monkeypatch):
    """After TTL expires, cache entry should be evicted and Firestore re-queried."""
    import time as time_mod

    fake_now = [1000.0]

    def _monotonic():
        return fake_now[0]

    monkeypatch.setattr(time_mod, "monotonic", _monotonic)

    docs = {
        "owner@yourcompany.com": {"type": "user", "tools": ["search"], "denied": []},
    }
    with _mock_docs(docs) as mock_get:
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "search") is True
        assert mock_get.call_count == 1

        # Advance time past TTL.
        fake_now[0] += _CACHE_TTL + 1

        # Should re-query Firestore.
        assert can_use_tool("owner@yourcompany.com", "yourcompany.com", "search") is True
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Specific tool list (not wildcard)
# ---------------------------------------------------------------------------


def test_specific_tools_list():
    """tools contains specific names, not '*'."""
    docs = {
        "yourcompany.com": {"type": "domain", "tools": ["search", "code_exec"], "denied": []},
    }
    with _mock_docs(docs):
        assert can_use_tool("a@yourcompany.com", "yourcompany.com", "search") is True
        clear_cache()
        assert can_use_tool("a@yourcompany.com", "yourcompany.com", "code_exec") is True
        clear_cache()
        assert can_use_tool("a@yourcompany.com", "yourcompany.com", "other") is False


def test_empty_domain_skips_domain_lookup():
    """If user_domain is empty, skip domain lookup and go to wildcard."""
    docs = {
        "*": {"type": "wildcard", "tools": ["search"], "denied": []},
    }
    with _mock_docs(docs) as mock_get:
        assert can_use_tool("anon@", "", "search") is True
        # Should skip domain lookup → only user + wildcard = 2 calls.
        assert mock_get.call_count == 2


def test_empty_email_skips_user_lookup_and_falls_to_wildcard():
    """Empty user_email must not trigger a Firestore lookup — a bare empty
    string produces an invalid document path ('tool_permissions/') and Cloud
    Firestore returns 400 InvalidArgument instead of 404. The guard in
    can_use_tool() must short-circuit to domain / wildcard instead."""
    docs = {
        "*": {"type": "wildcard", "tools": ["*"], "denied": []},
    }
    with _mock_docs(docs) as mock_get:
        result = can_use_tool("", "example.com", "search")
        assert result is True
        # First call must be the domain lookup (email="" → no user lookup)
        first_call_doc_id = mock_get.call_args_list[0].args[1]
        assert first_call_doc_id != ""


def test_empty_email_no_docs_denies():
    """Empty user_email + no domain/wildcard docs → deny (not crash)."""
    with _mock_docs({}):
        assert can_use_tool("", "example.com", "search") is False


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


def test_exception_fields():
    exc = ToolPermissionDenied("mark@x.com", "dangerous")
    assert exc.user_email == "mark@x.com"
    assert exc.tool_name == "dangerous"
    assert "not permitted" in str(exc)
