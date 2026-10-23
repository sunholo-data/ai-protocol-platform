"""`/api/admin/residents` — the three questions (v6.40.0 M14).

The assertions that earn their place are the scoping ones. Records are keyed by
a ONE-WAY HASH, so a tenant admin cannot be filtered by the key — which makes
"show everything" the tempting shortcut and exactly the wrong answer.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from admin import residents_routes as R
from admin.scope import AdminScope
from auth.firebase_auth import User
from db import resident_agents as records


@pytest.fixture(autouse=True)
def _resolvable_email(monkeypatch):
    """Every address resolves to a uid unless a test says otherwise.

    The routes take an EMAIL and the record is keyed by ``user_hash(uid)``, so
    a lookup that never resolves the address would 404 on a record that exists.
    """
    monkeypatch.setattr(R, "_uid_for", lambda email: f"uid-of-{email}")


def _scope(domains):
    return AdminScope(
        user=User(uid="admin-uid", email="admin@example.com"),
        domains=None if domains is None else frozenset(domains),
    )


def _rec(user_hash="abc123", domain="example.com", state="ready", budget=10.0, url=None):
    return records.ResidentAgent(userHash=user_hash, domain=domain, state=state, budgetUsd=budget, url=url)


def test_platform_admin_sees_every_record(monkeypatch):
    monkeypatch.setattr(records, "list_all", lambda: [_rec(domain="a.com"), _rec(domain="b.com")])
    assert len(R.list_residents(_scope(None))) == 2


def test_tenant_admin_sees_only_their_own(monkeypatch):
    monkeypatch.setattr(records, "list_all", lambda: [_rec(domain="a.com"), _rec(domain="b.com")])
    out = R.list_residents(_scope({"a.com"}))
    assert len(out) == 1


def test_a_record_with_no_domain_is_WITHHELD_from_a_tenant_admin(monkeypatch):
    """Deny by default. 'We do not know whose this is' must not resolve to
    'show it to everyone' — the key is a hash, so there is no second chance to
    work out ownership later."""
    monkeypatch.setattr(records, "list_all", lambda: [_rec(domain=None)])
    assert R.list_residents(_scope({"a.com"})) == []
    assert len(R.list_residents(_scope(None))) == 1


def test_missing_record_is_404_not_an_empty_200(monkeypatch):
    """'No resident agent' and 'a resident agent with nothing in it' are
    different facts and must not render identically."""
    monkeypatch.setattr(records, "get", lambda _e: None)
    with pytest.raises(HTTPException) as exc:
        R.get_resident("someone@example.com", _scope(None))
    assert exc.value.status_code == 404
    assert "No resident agent record" in exc.value.detail


def test_an_unknown_address_says_SO_rather_than_no_record(monkeypatch):
    """Two different 404s. "Never signed in" is fixed by the user logging in;
    "no record" is fixed by an administrator. One message for both sends the
    admin to the wrong place."""
    monkeypatch.setattr(R, "_uid_for", lambda _e: "")
    with pytest.raises(HTTPException) as exc:
        R.get_resident("ghost@example.com", _scope(None))
    assert exc.value.status_code == 404
    assert "never signed in" in exc.value.detail


def test_the_record_is_read_by_UID_not_by_the_email_in_the_path(monkeypatch):
    """The KEY-MISMATCH REGRESSION (found 2026-09-04, before per-user mode had
    a real record to disagree about).

    `ask_assistant` looks the record up by the ADK invocation's ``user.uid``.
    An admin route that hashed the email instead would 404 on every live
    record — and its budget PATCH would `upsert` a SECOND, orphan record under
    a key nothing reads, so the cap would appear set and cap nothing."""
    seen = []
    monkeypatch.setattr(records, "get", lambda key: seen.append(key) or _rec())
    R.get_resident("u@example.com", _scope(None))
    assert seen == ["uid-of-u@example.com"]


def test_a_budget_write_lands_on_the_same_key_it_was_read_from(monkeypatch):
    """`upsert` CREATES when absent, so a write to the wrong key does not fail
    — it silently mints the orphan the test above describes."""
    written = []
    monkeypatch.setattr(records, "get", lambda _k: _rec(budget=10.0))
    monkeypatch.setattr(records, "upsert", lambda key, **kw: written.append(key) or _rec(budget=kw["budgetUsd"]))
    monkeypatch.setattr(R, "record_admin_action", lambda **kw: None)
    R.set_budget("u@example.com", R.BudgetPatch(budgetUsd=25.0), _scope(None))
    assert written == ["uid-of-u@example.com"]


def test_budget_is_bounded_on_both_sides(monkeypatch):
    """Zero would read as 'unlimited' to some future caller, and an unbounded
    upper end turns a typo into a five-figure month."""
    monkeypatch.setattr(records, "get", lambda _e: _rec())
    for bad in (0.0, -5.0, 10_000.0):
        with pytest.raises(HTTPException) as exc:
            R.set_budget("u@example.com", R.BudgetPatch(budgetUsd=bad), _scope(None))
        assert exc.value.status_code == 422


def test_a_budget_change_is_audited(monkeypatch):
    """Raising a budget is the same class of act as granting the role, so it
    leaves the same trail."""
    seen = {}
    monkeypatch.setattr(records, "get", lambda _e: _rec(budget=10.0))
    monkeypatch.setattr(records, "upsert", lambda _e, **kw: _rec(budget=kw["budgetUsd"]))
    monkeypatch.setattr(R, "record_admin_action", lambda **kw: seen.update(kw))
    R.set_budget("u@example.com", R.BudgetPatch(budgetUsd=25.0), _scope(None))
    assert seen["action"] == "set_resident_budget"
    assert seen["before"] == {"budget_usd": 10.0}
    assert seen["after"] == {"budget_usd": 25.0}


def test_a_tenant_admin_cannot_touch_another_domains_budget(monkeypatch):
    monkeypatch.setattr(records, "get", lambda _e: _rec(domain="b.com"))
    with pytest.raises(HTTPException) as exc:
        R.set_budget("u@b.com", R.BudgetPatch(budgetUsd=25.0), _scope({"a.com"}))
    assert exc.value.status_code == 403


def test_a_uid_is_not_an_address_so_domain_must_be_passed_in(monkeypatch):
    """REGRESSION (2026-09-04). `upsert` used to derive `domain` from its
    `user_id` argument via `"@" in user_id` — but that argument is a Firebase
    UID, so the field was permanently None and every record was withheld from
    every tenant admin. Deny-by-default was working; the field it denied on was
    never filled."""
    written = {}
    monkeypatch.setattr(records, "get_client", lambda: _FakeClient(written))
    records.upsert("uG9Cjk6uMLY7n6drCLGyHrHjik42", state="ready")
    assert written["data"]["domain"] is None, "a uid must not be mined for a domain"

    written.clear()
    records.upsert("uG9Cjk6uMLY7n6drCLGyHrHjik42", state="ready", domain="example.com")
    assert written["data"]["domain"] == "example.com"


class _FakeDoc:
    exists = False

    def to_dict(self):
        return {}


class _FakeRef:
    def __init__(self, sink):
        self._sink = sink

    def get(self):
        return _FakeDoc()

    def set(self, data):
        self._sink["data"] = data


class _FakeCollection:
    def __init__(self, sink):
        self._sink = sink

    def document(self, key):
        self._sink["key"] = key
        return _FakeRef(self._sink)


class _FakeClient:
    def __init__(self, sink):
        self._sink = sink

    def collection(self, name):
        self._sink["collection"] = name
        return _FakeCollection(self._sink)
