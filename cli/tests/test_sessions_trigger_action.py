"""Tests for `aiplatform sessions trigger-action` — Sprint ACTION-TRIGGER M3.2.

The CLI POSTs to ``/api/skills/{skill_id}/sessions/{session_id}/surface-action-run``
and consumes the AG-UI SSE stream that comes back. Mocks the HTTP layer
with respx + httpx.Response (same pattern as ``test_cli_skill_probe``).
"""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
ENDPOINT = f"{BASE}/api/skills/demo-click-counter/sessions/sess-001/surface-action-run"


def _sse_body(events: list[dict]) -> str:
    """Render a list of event dicts as an SSE response body."""
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


@respx.mock
def test_trigger_action_happy_path_prints_events_one_per_line() -> None:
    """RUN_STARTED + TOOL_CALL_* + RUN_FINISHED → exit 0, one event per line."""
    events = [
        {"type": "RUN_STARTED", "thread_id": "sess-001", "run_id": "action_trigger_abc"},
        {
            "type": "TOOL_CALL_START",
            "tool_call_id": "tc-1",
            "tool_call_name": "send_a2ui_json_to_client",
        },
        {
            "type": "TOOL_CALL_ARGS",
            "tool_call_id": "tc-1",
            "delta": '{"surfaceId":"counter-main"}',
        },
        {"type": "TOOL_CALL_END", "tool_call_id": "tc-1"},
        {"type": "RUN_FINISHED", "thread_id": "sess-001", "run_id": "action_trigger_abc"},
    ]
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_body(events),
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            "--surface",
            "counter-main",
            "--action",
            "increment",
        ],
    )

    assert result.exit_code == 0, result.output
    assert route.called

    # One event per stdout line, in arrival order. Final line is RUN_FINISHED.
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == len(events), (lines, events)
    parsed = [json.loads(ln) for ln in lines]
    assert [e["type"] for e in parsed] == [
        "RUN_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "RUN_FINISHED",
    ]

    # Body shape that hit the backend.
    sent = route.calls.last.request
    body = json.loads(sent.content)
    assert body["surfaceId"] == "counter-main"
    assert body["action"]["name"] == "increment"
    assert body["action"]["sourceComponentId"] is None
    assert body["action"]["context"] is None
    assert "timestamp" in body["action"]
    assert body["forwardedProps"] == {"a2ui_surface_state": {}}


@respx.mock
def test_trigger_action_403_skill_not_opted_in_exits_2_with_body() -> None:
    """Skill missing `allow_action_triggered_runs` → 403 → exit code 2, body to stderr."""
    detail = {"detail": "Skill 'demo-click-counter' is not opted in to action-triggered runs"}
    respx.post(ENDPOINT).mock(return_value=httpx.Response(403, json=detail))

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            "--surface",
            "counter-main",
            "--action",
            "increment",
        ],
    )

    assert result.exit_code == 2, (result.output, getattr(result, "stderr", None))
    # The 403 response body lands on stderr so stdout stays clean for the
    # smoke script's event-grep. Click 8.3's CliRunner merges streams onto
    # `result.output` by default, so we check the combined output.
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "403" in combined
    assert "not opted in" in combined


@respx.mock
def test_trigger_action_run_error_exits_1() -> None:
    """Stream terminates with RUN_ERROR → exit code 1."""
    events = [
        {"type": "RUN_STARTED", "thread_id": "sess-001", "run_id": "r-1"},
        {"type": "RUN_ERROR", "message": "Tool blew up", "code": "TOOL_ERROR"},
    ]
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            "--surface",
            "counter-main",
            "--action",
            "increment",
        ],
    )

    assert result.exit_code == 1, result.output
    # RUN_ERROR event itself is still printed to stdout (final line).
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert json.loads(lines[-1])["type"] == "RUN_ERROR"


def test_trigger_action_missing_required_flag_is_click_usage_error() -> None:
    """No --surface → Click usage error (exit code 2)."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            # --surface intentionally omitted
            "--action",
            "increment",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--surface" in result.output


@respx.mock
def test_trigger_action_parses_context_and_state_json() -> None:
    """--context and --state JSON strings are parsed and end up in the request body."""
    events = [
        {"type": "RUN_STARTED", "thread_id": "sess-001", "run_id": "r-1"},
        {"type": "RUN_FINISHED", "thread_id": "sess-001", "run_id": "r-1"},
    ]
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            "--surface",
            "counter-main",
            "--action",
            "increment",
            "--component",
            "btn-increment",
            "--context",
            '{"foo":"bar","n":3}',
            "--state",
            '{"counter-main":{"count":2}}',
        ],
    )

    assert result.exit_code == 0, result.output
    sent = route.calls.last.request
    body = json.loads(sent.content)
    assert body["action"]["sourceComponentId"] == "btn-increment"
    assert body["action"]["context"] == {"foo": "bar", "n": 3}
    assert body["forwardedProps"]["a2ui_surface_state"] == {"counter-main": {"count": 2}}


@respx.mock
def test_trigger_action_pretty_flag_emits_indented_json() -> None:
    """--pretty switches event output to indent=2 (multiline) JSON."""
    events = [
        {"type": "RUN_STARTED", "thread_id": "sess-001", "run_id": "r-1"},
        {"type": "RUN_FINISHED", "thread_id": "sess-001", "run_id": "r-1"},
    ]
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--env",
            "local",
            "sessions",
            "trigger-action",
            "sess-001",
            "--skill",
            "demo-click-counter",
            "--surface",
            "counter-main",
            "--action",
            "increment",
            "--pretty",
        ],
    )

    assert result.exit_code == 0, result.output
    # Indented JSON has a newline immediately after `{` and 2-space indent.
    assert '{\n  "run_id"' in result.output or '{\n  "type"' in result.output
