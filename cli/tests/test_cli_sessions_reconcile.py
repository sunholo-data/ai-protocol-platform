"""Tests for `aiplatform sessions reconcile` (v6.23.0 B5/F5/F6 Phase 1)."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
ONE = f"{BASE}/api/admin/analytics/sessions/sess-1/reconcile"
SWEEP = f"{BASE}/api/admin/analytics/sessions-reconcile-sweep"

_BROKEN = {
    "session_id": "sess-1",
    "mirror_present": True,
    "owner_uid": "u1",
    "owner_domain": "acmeenergy.com",
    "skill_id": "one-assistant",
    "mirror_turn_count": 4,
    "provisional": False,
    "archived": False,
    "canonical_present": True,
    "event_count": 9,
    "user_event_count": 4,
    "raw_function_calls": 3,
    "raw_tool_calls": 3,
    "raw_delegation_calls": 0,
    "raw_function_responses": 3,
    "calls_missing_id": 0,
    "responses_missing_id": 2,
    "text_bearing_events": 5,
    "trace_messages": 5,
    "trace_tools": 3,
    "trace_tools_errored": 2,
    "trace_delegations": 0,
    "findings": [
        {"code": "RESPONSE_ID_MISSING", "severity": "error", "detail": "2 function_response part(s) have no id"},
        {"code": "TOOLS_RENDER_ERRORED", "severity": "warn", "detail": "2 of 3 tool calls render as failed"},
    ],
}

_CLEAN = {
    "session_id": "sess-2",
    "mirror_present": True,
    "canonical_present": True,
    "owner_uid": "u2",
    "owner_domain": "acmeenergy.com",
    "skill_id": "one-assistant",
    "mirror_turn_count": 1,
    "findings": [{"code": "OK", "severity": "info", "detail": "All three stores agree."}],
}


@respx.mock
def test_reconcile_one_session_shows_findings_most_severe_first() -> None:
    route = respx.get(ONE).mock(return_value=httpx.Response(200, json=_BROKEN))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "reconcile", "sess-1"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "RESPONSE_ID_MISSING" in result.output
    # Errors lead, warnings follow.
    assert result.output.index("RESPONSE_ID_MISSING") < result.output.index("TOOLS_RENDER_ERRORED")
    # The counts an operator needs to judge the finding are on screen.
    assert "tools=3" in result.output
    assert "errored=2" in result.output


@respx.mock
def test_reconcile_json_flag_emits_raw() -> None:
    respx.get(ONE).mock(return_value=httpx.Response(200, json=_BROKEN))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "reconcile", "sess-1", "--json"])
    assert result.exit_code == 0, result.output
    assert '"responses_missing_id": 2' in result.output


@respx.mock
def test_sweep_prints_the_tally_and_hides_healthy_sessions() -> None:
    """A clean sweep of 25 must not scroll 25 healthy reports past the operator."""
    body = {
        "scanned": 2,
        "reports": [_BROKEN, _CLEAN],
        "code_counts": {"RESPONSE_ID_MISSING": 1, "OK": 1},
    }
    respx.get(SWEEP).mock(return_value=httpx.Response(200, json=body))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "reconcile", "--all", "--limit", "2"])
    assert result.exit_code == 0, result.output
    assert "swept 2 session(s)" in result.output
    assert "RESPONSE_ID_MISSING" in result.output
    # The diverging session is detailed; the healthy one is only in the tally.
    assert "sess-1" in result.output
    assert "=== sess-2 ===" not in result.output


@respx.mock
def test_sweep_reports_percentages_so_a_systemic_defect_is_obvious() -> None:
    body = {"scanned": 4, "reports": [], "code_counts": {"RESPONSE_ID_MISSING": 3}}
    respx.get(SWEEP).mock(return_value=httpx.Response(200, json=body))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "reconcile", "--all"])
    assert result.exit_code == 0, result.output
    assert "(75%)" in result.output


def test_reconcile_without_session_id_or_all_is_a_usage_error() -> None:
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "reconcile"])
    assert result.exit_code != 0
    assert "--all" in result.output
