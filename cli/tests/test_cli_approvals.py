"""Tests for `aiplatform approvals` — decide human approvals from a terminal."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
ROUTINE = {
    "id": "severa:abc",
    "title": "Correct 17 time entries",
    "review": "17 entries · 19.5 h\nEvery entry's billing label becomes Externally invoiced 2026-10-30.",
    "risk": "routine",
    "digest": "d" * 64,
    "expires_at": 1_900_000_000,
    "state": "pending",
    "session_id": "s1",
}
HIGH = {**ROUTINE, "id": "billing:inv1", "title": "Send invoice 1201", "risk": "high"}


def _run(*args: str, stdin: str | None = None, env: dict | None = None):
    return CliRunner().invoke(main, ["--env", "local", "approvals", *args], input=stdin, env=env)


@respx.mock
def test_list_shows_what_waits_and_how_to_approve() -> None:
    respx.get(f"{BASE}/api/approvals").mock(return_value=httpx.Response(200, json={"approvals": [ROUTINE, HIGH]}))
    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "severa:abc  Correct 17 time entries" in result.output
    assert "billing:inv1  Send invoice 1201  (needs a fresh sign-in)" in result.output


@respx.mock
def test_list_says_so_when_nothing_waits() -> None:
    respx.get(f"{BASE}/api/approvals").mock(return_value=httpx.Response(200, json={"approvals": []}))
    assert "Nothing is waiting for your approval." in _run("list").output


@respx.mock
def test_show_prints_the_full_review() -> None:
    respx.get(f"{BASE}/api/approvals/severa:abc").mock(return_value=httpx.Response(200, json=ROUTINE))
    result = _run("show", "severa:abc")
    assert result.exit_code == 0, result.output
    assert "Externally invoiced 2026-10-30" in result.output and "dddddddddddd" in result.output


@respx.mock
def test_approve_shows_the_review_asks_and_sends_the_reviewed_digest() -> None:
    respx.get(f"{BASE}/api/approvals/severa:abc").mock(return_value=httpx.Response(200, json=ROUTINE))
    decision = respx.post(f"{BASE}/api/approvals/severa:abc/decision").mock(
        return_value=httpx.Response(200, json={"state": "completed", "message": "Saved and verified 17 entries."})
    )
    result = _run("approve", "severa:abc", stdin="y\n")
    assert result.exit_code == 0, result.output
    assert result.output.index("Externally invoiced") < result.output.index("Approve this?")
    assert json.loads(decision.calls.last.request.content) == {"decision": "approve", "digest": "d" * 64}
    assert "Saved and verified 17 entries." in result.output


@respx.mock
def test_answering_no_sends_nothing() -> None:
    respx.get(f"{BASE}/api/approvals/severa:abc").mock(return_value=httpx.Response(200, json=ROUTINE))
    decision = respx.post(f"{BASE}/api/approvals/severa:abc/decision")
    result = _run("approve", "severa:abc", stdin="n\n")
    assert result.exit_code == 0 and not decision.called
    assert "Not approved. Nothing was sent." in result.output


@respx.mock
def test_high_risk_is_never_approved_from_the_terminal() -> None:
    respx.get(f"{BASE}/api/approvals/billing:inv1").mock(return_value=httpx.Response(200, json=HIGH))
    decision = respx.post(f"{BASE}/api/approvals/billing:inv1/decision")
    result = _run("approve", "billing:inv1", stdin="y\n")
    assert result.exit_code != 0 and not decision.called
    assert "needs a fresh sign-in" in result.output


@respx.mock
def test_cancel_confirms_then_discards() -> None:
    respx.get(f"{BASE}/api/approvals/severa:abc").mock(return_value=httpx.Response(200, json=ROUTINE))
    decision = respx.post(f"{BASE}/api/approvals/severa:abc/decision").mock(
        return_value=httpx.Response(200, json={"state": "cancelled", "message": "Cancelled. Severa was not changed."})
    )
    result = _run("cancel", "severa:abc", "--yes")
    assert result.exit_code == 0, result.output
    assert json.loads(decision.calls.last.request.content)["decision"] == "cancel"


@respx.mock
def test_the_approval_token_is_used_when_set() -> None:
    route = respx.get(f"{BASE}/api/approvals").mock(return_value=httpx.Response(200, json={"approvals": []}))
    _run("list", env={"AIPLATFORM_APPROVAL_TOKEN": "aip_mcp_approver"})
    assert route.calls.last.request.headers["Authorization"] == "Bearer aip_mcp_approver"


@respx.mock
def test_server_refusals_are_shown_not_swallowed() -> None:
    respx.get(f"{BASE}/api/approvals/severa:abc").mock(return_value=httpx.Response(200, json=ROUTINE))
    respx.post(f"{BASE}/api/approvals/severa:abc/decision").mock(
        return_value=httpx.Response(403, json={"detail": "This token has no approval rights."})
    )
    result = _run("approve", "severa:abc", stdin="y\n")
    assert result.exit_code != 0 and "no approval rights" in result.output
