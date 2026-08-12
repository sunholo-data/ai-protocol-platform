"""Tests for `aitana skill probe` — the TTFT-INSTR M4 CLI command.

The CLI fires a single SSE-streaming POST and consumes the LATENCY_REPORT
Custom event at end-of-stream. We mock the streaming response with respx +
httpx.Response so the test never needs a live backend.
"""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


def _sse_body(events: list[dict]) -> str:
    """Render a list of event dicts as an SSE response body."""
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


@respx.mock
def test_probe_prints_table_when_latency_report_present() -> None:
    events = [
        {"type": "RUN_STARTED", "threadId": "t-1", "runId": "r-1"},
        {
            "type": "CUSTOM",
            "name": "STAGE_PROGRESS",
            "value": {"stage": "before_model_done", "label": "Thinking…", "elapsed_ms": 145.0},
        },
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hi"},
        {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
        {"type": "RUN_FINISHED", "threadId": "t-1", "runId": "r-1"},
        {
            "type": "CUSTOM",
            "name": "LATENCY_REPORT",
            "value": {
                "skill_id": "ttft-skill",
                "session_id": "probe-abc",
                "user_id": "test-user",
                "model_used": "gemini-2.5-flash",
                "routing_choice": "fast",
                "tools_invoked_count": 0,
                "ttft_mode": "full",
                "request_received_ms": 0.0,
                "session_index_done_ms": 12.34,
                "before_agent_done_ms": 145.0,
                "before_model_done_ms": 152.5,
                "first_model_token_ms": 487.91,
                "first_agui_event_ms": 491.0,
                "first_sse_byte_ms": 493.0,
                "total_response_ms": 2143.0,
                "event": "ttft",
            },
        },
    ]

    route = respx.post(f"{BASE}/api/skill/ttft-skill/stream").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse_body(events),
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "skill", "probe", "ttft-skill", "--message", "Hello"],
    )

    assert result.exit_code == 0, result.output
    assert route.called
    # The table prints each stage on its own line with the ms value.
    assert "first_model_token" in result.output
    assert "487.91ms" in result.output
    assert "← TTFT" in result.output  # TTFT marker
    assert "gemini-2.5-flash" in result.output
    assert "routing: fast" in result.output
    assert "mode:" in result.output and "full" in result.output

    # And the request was sent with ?probe=1.
    sent = route.calls.last.request
    assert sent.url.params.get("probe") == "1"
    body = json.loads(sent.content)
    assert body["messages"][0]["content"] == "Hello"
    assert body["threadId"].startswith("probe-")


@respx.mock
def test_probe_json_flag_outputs_raw_payload() -> None:
    payload = {
        "skill_id": "ttft-skill",
        "model_used": "gemini-2.5-flash",
        "routing_choice": "fast",
        "first_model_token_ms": 500.0,
        "tools_invoked_count": 0,
        "ttft_mode": "full",
    }
    events = [
        {"type": "RUN_STARTED", "threadId": "t-1", "runId": "r-1"},
        {"type": "RUN_FINISHED", "threadId": "t-1", "runId": "r-1"},
        {"type": "CUSTOM", "name": "LATENCY_REPORT", "value": payload},
    ]
    respx.post(f"{BASE}/api/skill/ttft-skill/stream").mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "skill", "probe", "ttft-skill", "--json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["model_used"] == "gemini-2.5-flash"
    assert parsed["first_model_token_ms"] == 500.0


@respx.mock
def test_probe_exit_code_when_no_latency_report() -> None:
    """If the backend stream finishes without a LATENCY_REPORT (e.g.
    AITANA_TTFT_MODE=off), the CLI exits non-zero with a hint."""
    events = [
        {"type": "RUN_STARTED", "threadId": "t-1", "runId": "r-1"},
        {"type": "RUN_FINISHED", "threadId": "t-1", "runId": "r-1"},
    ]
    respx.post(f"{BASE}/api/skill/ttft-skill/stream").mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "skill", "probe", "ttft-skill"])
    assert result.exit_code == 2, result.output
    assert "No LATENCY_REPORT" in result.output


@respx.mock
def test_probe_surfaces_run_error() -> None:
    """A RUN_ERROR mid-stream surfaces as a non-zero exit + red error line."""
    events = [
        {"type": "RUN_ERROR", "message": "Backend exploded", "code": "BOOM"},
    ]
    respx.post(f"{BASE}/api/skill/ttft-skill/stream").mock(
        return_value=httpx.Response(200, text=_sse_body(events)),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "skill", "probe", "ttft-skill"])
    assert result.exit_code == 1, result.output
    assert "Backend exploded" in result.output
