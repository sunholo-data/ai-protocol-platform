"""Approval core: provider registry, one-use digest-bound decisions, audit.

Design: docs/design/v6.41.0/human-approvals.md ("Approval core").
"""

from __future__ import annotations

import time
from dataclasses import replace
from unittest import mock

import pytest

from adk import approvals
from adk.approvals import ApprovalError, ApprovalRequest, Approver, Receipt
from auth.approvals import Credential
from auth.firebase_auth import User

SESSION = Credential(kind="session", id="web")


class FakeProvider:
    """A provider over an in-memory store, recording every decide call."""

    kind = "fake"

    def __init__(self) -> None:
        self.rows: dict[str, ApprovalRequest] = {}
        self.calls: list[tuple[str, str, str, Approver]] = []

    def add(self, ref: str, **changes) -> ApprovalRequest:
        row = ApprovalRequest(
            id=f"fake:{ref}",
            owner_uid="owner",
            session_id="s1",
            risk="routine",
            title=f"Change {ref}",
            review=f"Review of {ref}",
            digest=f"digest-{ref}",
            expires_at=time.time() + 3600,
            state="pending",
        )
        self.rows[ref] = replace(row, **changes)
        return self.rows[ref]

    def pending(self, uid: str, session_id: str | None) -> list[ApprovalRequest]:
        return [
            r
            for r in self.rows.values()
            if r.owner_uid == uid and r.state == "pending" and session_id in (None, r.session_id)
        ]

    def get(self, ref: str, uid: str) -> ApprovalRequest | None:
        row = self.rows.get(ref)
        return row if row and row.owner_uid == uid else None

    async def decide(self, ref: str, digest: str, decision: str, approver: Approver) -> Receipt:
        self.calls.append((ref, digest, decision, approver))
        state = "completed" if decision == "approve" else "cancelled"
        self.rows[ref] = replace(self.rows[ref], state=state)
        return Receipt(state=state, message=f"{decision}d {ref}")


@pytest.fixture
def provider():
    approvals.clear_registry()
    fake = FakeProvider()
    approvals.register_provider(fake)
    yield fake
    approvals.clear_registry()


@pytest.fixture
def audit():
    with mock.patch("adk.approvals.set_document") as set_document:
        yield set_document


OWNER = User(uid="owner", email="owner@example.com", auth_mode="tailnet")


async def decide(approval_id: str, digest: str, decision: str = "approve", who: User = OWNER, **kwargs):
    return await approvals.decide(
        approval_id, digest=digest, decision=decision, user=who, channel="web", credential=SESSION, **kwargs
    )


@pytest.mark.asyncio
async def test_approve_runs_the_provider_once_with_the_approver(provider, audit):
    row = provider.add("1")
    receipt = await decide(row.id, row.digest)
    assert receipt.state == "completed"
    assert provider.calls == [("1", row.digest, "approve", Approver("owner", "web", "web", None))]
    with pytest.raises(ApprovalError) as again:
        await decide(row.id, row.digest)
    assert again.value.status == 410
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_stale_digest_is_a_conflict_and_never_reaches_the_provider(provider, audit):
    row = provider.add("1")
    with pytest.raises(ApprovalError) as stale:
        await decide(row.id, "some-other-digest")
    assert stale.value.status == 409
    assert provider.calls == []


@pytest.mark.asyncio
async def test_expired_unknown_foreign_and_refused(provider, audit):
    expired = provider.add("old", expires_at=time.time() - 1)
    with pytest.raises(ApprovalError) as gone:
        await decide(expired.id, expired.digest)
    assert gone.value.status == 410
    for missing in ("fake:nope", "other:1", "no-colon"):
        with pytest.raises(ApprovalError) as unknown:
            await decide(missing, "x")
        assert unknown.value.status == 404
    theirs = provider.add("2", owner_uid="someone-else")
    with pytest.raises(ApprovalError) as foreign:
        await decide(theirs.id, theirs.digest)
    assert foreign.value.status == 404
    row = provider.add("3")
    machine = User(uid="owner", email="owner@example.com", auth_mode="machine")
    with pytest.raises(ApprovalError) as refused:
        await decide(row.id, row.digest, who=machine)
    assert refused.value.status == 403
    assert provider.calls == []


@pytest.mark.asyncio
async def test_cancel_needs_no_fresh_sign_in(provider, audit):
    row = provider.add("1", risk="high")
    stale_session = User(uid="owner", email="o@example.com", auth_mode="firebase", auth_time=0)
    receipt = await decide(row.id, row.digest, "cancel", who=stale_session)
    assert receipt.state == "cancelled"
    with pytest.raises(ApprovalError):
        await decide(provider.add("2", risk="high").id, "digest-2", who=stale_session)


@pytest.mark.asyncio
async def test_every_decision_is_audited(provider, audit):
    row = provider.add("1")
    await decide(row.id, row.digest)
    (collection, doc_id, record), _ = audit.call_args
    assert collection == "approval_decisions" and doc_id
    assert record["approval_id"] == row.id and record["decision"] == "approve"
    assert record["channel"] == "web" and record["credential"] == "web" and record["state"] == "completed"
    assert record["digest"] == row.digest and "review" not in record


@pytest.mark.asyncio
async def test_an_audit_failure_never_hides_a_completed_decision(provider):
    row = provider.add("1")
    with mock.patch("adk.approvals.set_document", side_effect=RuntimeError("firestore down")):
        receipt = await decide(row.id, row.digest)
    assert receipt.state == "completed"


def test_pending_spans_providers_and_respects_owner_and_session(provider):
    provider.add("1")
    provider.add("2", session_id="s2")
    provider.add("3", owner_uid="someone-else")
    provider.add("4", state="completed")
    assert {r.id for r in approvals.pending_for("owner")} == {"fake:1", "fake:2"}
    assert [r.id for r in approvals.pending_for("owner", "s2")] == ["fake:2"]


def test_a_failing_provider_does_not_hide_the_others(provider):
    class Broken:
        kind = "broken"

        def pending(self, uid, session_id):
            raise RuntimeError("store unavailable")

    approvals.register_provider(Broken())
    provider.add("1")
    assert [r.id for r in approvals.pending_for("owner")] == ["fake:1"]


def test_request_view_is_what_clients_render(provider):
    row = provider.add("1")
    view = approvals.get("fake:1", "owner").view()
    assert view == {
        "id": "fake:1",
        "title": "Change 1",
        "review": "Review of 1",
        "risk": "routine",
        "digest": row.digest,
        "expires_at": row.expires_at,
        "state": "pending",
        "session_id": "s1",
    }
