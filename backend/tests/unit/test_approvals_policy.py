"""Approver policy: who may decide a human-approval request, by risk and channel.

Design: docs/design/v6.41.0/human-approvals.md ("Approver policy").
"""

from __future__ import annotations

import time

import pytest

from adk.approvals import ApprovalRequest
from auth.approvals import Credential, can_approve
from auth.firebase_auth import User

NOW = 1_800_000_000.0
SESSION = Credential(kind="session", id="web")
APPROVE_TOKEN = Credential(kind="token", id="tok1", scopes=frozenset({"mcp", "approve"}))
PLAIN_TOKEN = Credential(kind="token", id="tok2", scopes=frozenset({"mcp"}))


def request(risk: str = "routine", owner: str = "owner") -> ApprovalRequest:
    return ApprovalRequest(
        id="fake:1",
        owner_uid=owner,
        session_id="s1",
        risk=risk,
        title="Correct 1 time entry",
        review="Hours: 1 h -> 2 h",
        digest="d" * 64,
        expires_at=NOW + 3600,
        state="pending",
    )


def user(mode: str = "firebase", uid: str = "owner", auth_time: float | None = NOW - 60) -> User:
    return User(uid=uid, email=f"{uid}@example.com", auth_mode=mode, auth_time=auth_time)


@pytest.mark.parametrize(
    ("who", "credential", "channel", "risk", "allowed"),
    [
        # Human web sessions: routine anywhere a session can come from, high only fresh.
        (user("firebase"), SESSION, "web", "routine", True),
        (user("firebase"), SESSION, "link", "routine", True),
        (user("tailnet", auth_time=None), SESSION, "web", "routine", True),
        (user("firebase"), SESSION, "link", "high", True),
        (user("tailnet", auth_time=None), SESSION, "link", "high", True),
        (user("firebase", auth_time=NOW - 601), SESSION, "link", "high", False),
        (user("firebase", auth_time=None), SESSION, "link", "high", False),
        # Approve-scoped personal tokens: routine via MCP or the CLI, never high.
        (user("firebase", auth_time=None), APPROVE_TOKEN, "mcp", "routine", True),
        (user("tailnet", auth_time=None), APPROVE_TOKEN, "cli", "routine", True),
        (user("firebase", auth_time=None), APPROVE_TOKEN, "mcp", "high", False),
        (user("firebase", auth_time=None), APPROVE_TOKEN, "web", "routine", False),
        (user("firebase", auth_time=None), PLAIN_TOKEN, "mcp", "routine", False),
        # Never approvers, whatever the channel or risk.
        (user("machine", auth_time=None), SESSION, "web", "routine", False),
        (user("machine", auth_time=None), APPROVE_TOKEN, "mcp", "routine", False),
        (user("anonymous_group_id"), SESSION, "web", "routine", False),
        (user("firebase", uid="someone-else"), SESSION, "web", "routine", False),
        # A session credential can't claim a token channel, and vice versa.
        (user("firebase"), SESSION, "mcp", "routine", False),
    ],
)
def test_policy_table(who, credential, channel, risk, allowed):
    refusal = can_approve(who, request(risk), channel=channel, credential=credential, now=NOW)
    assert (refusal is None) == allowed, refusal


def test_refusals_say_why():
    assert "not yours" in can_approve(user(uid="x"), request(), channel="web", credential=SESSION, now=NOW)
    assert "sign in again" in can_approve(
        user(auth_time=NOW - 3600), request("high"), channel="link", credential=SESSION, now=NOW
    )
    assert "approval rights" in can_approve(user(), request(), channel="mcp", credential=PLAIN_TOKEN, now=NOW)


def test_fresh_auth_window_is_configurable(monkeypatch):
    monkeypatch.setenv("APPROVAL_FRESH_AUTH_SECONDS", "60")
    assert can_approve(user(auth_time=NOW - 120), request("high"), channel="link", credential=SESSION, now=NOW)
    assert can_approve(user(auth_time=NOW - 30), request("high"), channel="link", credential=SESSION, now=NOW) is None


def test_default_now_is_wall_clock():
    fresh = user(auth_time=time.time())
    assert can_approve(fresh, request("high"), channel="link", credential=SESSION) is None


def test_firebase_user_carries_auth_time():
    from auth.firebase_auth import _user_from_decoded_token

    decoded = {"uid": "u", "email": "u@example.com", "auth_time": 1_700_000_000}
    assert _user_from_decoded_token(decoded).auth_time == 1_700_000_000
    assert _user_from_decoded_token({"uid": "u"}).auth_time is None
