"""Tests for `aiplatform skill compare` — PPA-COMPARE-LAUNCHER 7.2-M2 sprint (M3).

The command drives the same `start_compare` action the workbench launcher
fires: (optionally bootstrap a session, then) POST to
``/api/skills/{skill_id}/sessions/{session_id}/surface-action-run`` and consume
the AG-UI SSE stream. Mocks the HTTP layer with respx (same pattern as
``test_sessions_trigger_action`` / ``test_cli_skill_probe``).
"""

from __future__ import annotations

import json
import re

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"

# New-session ids look like `compare-<hex>`; match the dynamic segment.
_BOOTSTRAP_RE = re.compile(r"http://localhost:1956/api/sessions/compare-[0-9a-f]+/bootstrap")
_ACTION_RE = re.compile(
    r"http://localhost:1956/api/skills/one-doc-compare/sessions/compare-[0-9a-f]+/surface-action-run"
)


def _sse_body(events: list[dict]) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


_OK_EVENTS = [
    {"type": "RUN_STARTED", "thread_id": "compare-x", "run_id": "action_trigger_abc"},
    {"type": "TOOL_CALL_START", "tool_call_id": "tc-1", "tool_call_name": "send_a2ui_json_to_client"},
    {"type": "TOOL_CALL_END", "tool_call_id": "tc-1"},
    {"type": "RUN_FINISHED", "thread_id": "compare-x", "run_id": "action_trigger_abc"},
]


def _invoke(args: list[str]):
    return CliRunner().invoke(main, ["--env", "local", "skill", "compare", *args])


# --- happy path (new session) -------------------------------------------------


@respx.mock
def test_compare_new_session_bootstraps_then_streams() -> None:
    boot = respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={"created": True}))
    action = respx.post(url__regex=_ACTION_RE.pattern).mock(
        return_value=httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_sse_body(_OK_EVENTS))
    )

    result = _invoke(["one-doc-compare", "--left", "doc-a", "--right", "doc-b"])

    assert result.exit_code == 0, result.output
    assert boot.called, "a fresh session must be bootstrapped"
    assert action.called

    # One event per stdout line, in arrival order; last is RUN_FINISHED.
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
    parsed = [json.loads(ln) for ln in lines]
    assert [e["type"] for e in parsed] == ["RUN_STARTED", "TOOL_CALL_START", "TOOL_CALL_END", "RUN_FINISHED"]

    body = json.loads(action.calls.last.request.content)
    assert body["surfaceId"] == "workspace"
    assert body["action"]["name"] == "start_compare"
    assert body["action"]["context"]["left"] == {"doc_id": "doc-a"}
    assert body["action"]["context"]["right"] == {"doc_id": "doc-b"}
    # All-default scope → empty config (legacy cache keys).
    assert body["action"]["context"]["config"] == {}
    assert body["forwardedProps"] == {"a2ui_surface_state": {}}


# --- identity duality ---------------------------------------------------------


@respx.mock
def test_compare_gs_url_and_doc_id_duality() -> None:
    respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={}))
    action = respx.post(url__regex=_ACTION_RE.pattern).mock(
        return_value=httpx.Response(200, text=_sse_body(_OK_EVENTS))
    )

    result = _invoke(
        [
            "one-doc-compare",
            "--left",
            "doc-a",
            "--right",
            "gs://multivac-acme-energy-bucket/PPAs/B.pdf",
        ]
    )
    assert result.exit_code == 0, result.output
    ctx = json.loads(action.calls.last.request.content)["action"]["context"]
    assert ctx["left"] == {"doc_id": "doc-a"}
    assert ctx["right"] == {"gs_url": "gs://multivac-acme-energy-bucket/PPAs/B.pdf"}


# --- config narrowing ---------------------------------------------------------


@respx.mock
def test_compare_config_flags_reach_the_payload() -> None:
    respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={}))
    action = respx.post(url__regex=_ACTION_RE.pattern).mock(
        return_value=httpx.Response(200, text=_sse_body(_OK_EVENTS))
    )

    result = _invoke(
        [
            "one-doc-compare",
            "--left",
            "doc-a",
            "--right",
            "doc-b",
            "--clauses",
            "settlement_type, price_formula",
            "--severity",
            "moderate",
            "--max-other",
            "5",
        ]
    )
    assert result.exit_code == 0, result.output
    config = json.loads(action.calls.last.request.content)["action"]["context"]["config"]
    assert config == {
        "clauses": ["settlement_type", "price_formula"],
        "severity_floor": "moderate",
        "max_other_clauses": 5,
    }


@respx.mock
def test_compare_invalid_severity_is_click_usage_error() -> None:
    result = _invoke(["one-doc-compare", "--left", "a", "--right", "b", "--severity", "nope"])
    assert result.exit_code == 2, result.output
    assert "--severity" in result.output or "Invalid value" in result.output


# --- existing session (no bootstrap) ------------------------------------------


@respx.mock
def test_compare_reuses_session_without_bootstrap() -> None:
    boot = respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={}))
    action = respx.post(f"{BASE}/api/skills/one-doc-compare/sessions/sess-fixed/surface-action-run").mock(
        return_value=httpx.Response(200, text=_sse_body(_OK_EVENTS))
    )

    result = _invoke(["one-doc-compare", "--left", "doc-a", "--right", "doc-b", "--session", "sess-fixed"])
    assert result.exit_code == 0, result.output
    assert action.called
    assert not boot.called, "an explicit --session must NOT be bootstrapped"


# --- error paths --------------------------------------------------------------


@respx.mock
def test_compare_403_not_opted_in_exits_2() -> None:
    respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={}))
    respx.post(url__regex=_ACTION_RE.pattern).mock(
        return_value=httpx.Response(403, json={"detail": "not opted in to action-triggered runs"})
    )
    result = _invoke(["one-doc-compare", "--left", "doc-a", "--right", "doc-b"])
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "403" in combined
    assert "not opted in" in combined


@respx.mock
def test_compare_run_error_exits_1() -> None:
    respx.post(url__regex=_BOOTSTRAP_RE.pattern).mock(return_value=httpx.Response(200, json={}))
    events = [
        {"type": "RUN_STARTED", "thread_id": "compare-x", "run_id": "r-1"},
        {"type": "RUN_ERROR", "message": "compare blew up", "code": "TOOL_ERROR"},
    ]
    respx.post(url__regex=_ACTION_RE.pattern).mock(return_value=httpx.Response(200, text=_sse_body(events)))
    result = _invoke(["one-doc-compare", "--left", "doc-a", "--right", "doc-b"])
    assert result.exit_code == 1, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
    assert json.loads(lines[-1])["type"] == "RUN_ERROR"


def test_compare_missing_required_left_is_usage_error() -> None:
    result = _invoke(["one-doc-compare", "--right", "doc-b"])
    assert result.exit_code == 2, result.output
    assert "--left" in result.output
