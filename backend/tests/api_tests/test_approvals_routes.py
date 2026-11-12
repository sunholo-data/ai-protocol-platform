"""/api/approvals — list, read and decide human-approval requests over REST.

Design: docs/design/v6.41.0/human-approvals.md ("API").
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from adk import approvals
from auth import User, build_access_context
from tests.unit.test_approvals_core import FakeProvider

OWNER = User(uid="owner", email="owner@example.com", auth_mode="tailnet")


@pytest.fixture
def provider():
    approvals.clear_registry()
    fake = FakeProvider()
    approvals.register_provider(fake)
    yield fake
    approvals.clear_registry()


def client_as(user: User) -> TestClient:
    """A client whose requests verify as ``user``'s human session."""
    import fast_api_app as module

    async def verified(request: Request) -> User:
        request.state.access = build_access_context(user)
        return user

    mock.patch("protocols.approvals_routes.get_current_user", side_effect=verified).start()
    return TestClient(module.app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    import fast_api_app as module

    module.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _no_audit_writes():
    with mock.patch("adk.approvals.set_document"):
        yield


def test_list_and_read_pending(provider):
    provider.add("1")
    provider.add("2", session_id="s2")
    provider.add("3", owner_uid="someone-else")
    client = client_as(OWNER)
    listed = client.get("/api/approvals").json()["approvals"]
    assert [a["id"] for a in listed] == ["fake:1", "fake:2"]
    assert [a["id"] for a in client.get("/api/approvals", params={"session": "s2"}).json()["approvals"]] == ["fake:2"]
    one = client.get("/api/approvals/fake:1").json()
    assert one["review"] == "Review of 1" and one["digest"] == "digest-1"
    assert client.get("/api/approvals/fake:3").status_code == 404


def test_decide_approves_once_with_the_reviewed_digest(provider):
    provider.add("1")
    client = client_as(OWNER)
    stale = client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "wrong"})
    assert stale.status_code == 409
    ok = client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"})
    assert ok.status_code == 200
    assert ok.json() == {"state": "completed", "message": "approved 1", "details": {}}
    again = client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"})
    assert again.status_code == 410
    assert len(provider.calls) == 1
    assert provider.calls[0][3].channel == "link"


def test_machine_principals_can_read_but_never_decide(provider):
    provider.add("1")
    client = client_as(User(uid="owner", email="owner@example.com", auth_mode="machine"))
    assert client.get("/api/approvals/fake:1").status_code == 200
    refused = client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"})
    assert refused.status_code == 403
    assert provider.calls == []


def test_decision_body_is_validated(provider):
    provider.add("1")
    client = client_as(OWNER)
    assert (
        client.post("/api/approvals/fake:1/decision", json={"decision": "yes", "digest": "digest-1"}).status_code == 422
    )
    assert client.post("/api/approvals/fake:1/decision", json={"decision": "approve"}).status_code == 422


# --- approval-scoped personal tokens (the CLI) ------------------------------------


def _token_client(record: dict | None, user: User = OWNER) -> TestClient:
    import fast_api_app as module

    patches = [
        mock.patch("auth.mcp_tokens.resolve_token_record", return_value=record),
        mock.patch("auth.firebase_auth.resolve_user_by_uid", return_value=user),
    ]
    for p in patches:
        p.start()
    module.app.dependency_overrides.clear()
    client = TestClient(module.app, headers={"Authorization": "Bearer aip_mcp_test"})
    client.patches = patches  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _stop_token_patches():
    yield
    mock.patch.stopall()


APPROVER = {"uid": "owner", "id": "abc123abc123", "name": "Claude Code", "scopes": ["mcp", "approve"]}


def test_an_approval_token_decides_as_the_cli(provider):
    provider.add("1")
    with mock.patch("notifications.hooks.notify_token_approval") as notify:
        client = _token_client(APPROVER)
        assert [a["id"] for a in client.get("/api/approvals").json()["approvals"]] == ["fake:1"]
        ok = client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"})
    assert ok.status_code == 200, ok.text
    approver = provider.calls[0][3]
    assert approver.channel == "cli" and approver.credential == "abc123abc123"
    notify.assert_called_once_with("owner", "approve", "Claude Code")


def test_a_plain_mcp_token_cannot_use_the_approvals_api(provider):
    provider.add("1")
    client = _token_client({**APPROVER, "scopes": ["mcp"]})
    assert client.get("/api/approvals").status_code == 403
    assert (
        client.post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"}).status_code
        == 403
    )
    assert provider.calls == []


def test_unknown_tokens_are_401_and_approval_tokens_open_nothing_else(provider):
    assert _token_client(None).get("/api/approvals").status_code == 401
    assert _token_client(APPROVER).get("/api/mcp/tokens").status_code == 401


def test_a_session_decision_sends_no_token_push(provider):
    provider.add("1")
    with mock.patch("notifications.hooks.notify_token_approval") as notify:
        client_as(OWNER).post("/api/approvals/fake:1/decision", json={"decision": "approve", "digest": "digest-1"})
    notify.assert_not_called()
