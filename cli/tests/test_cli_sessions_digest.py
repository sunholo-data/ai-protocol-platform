"""Tests for `aiplatform sessions digest` (v6.11.0 workbench-home)."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
ENDPOINT = f"{BASE}/api/sessions/sess-1/activity"

_DIGEST = {
    "session_id": "sess-1",
    "tool_calls": [{"id": "t1", "name": "map_ppa_obligations", "status": "success", "ts": 1.0, "notability": "artifact"}],
    "delegations": [{"id": "d1", "target": "one-ppa-expert", "targetDisplay": "Contract Expert", "mode": "auto", "ts": 1.0, "notability": "notable"}],
    "session_start_ts": 1.0,
}


@respx.mock
def test_digest_summarises_notable_items() -> None:
    route = respx.get(ENDPOINT, params={"view": "digest"}).mock(return_value=httpx.Response(200, json=_DIGEST))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "digest", "sess-1"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Contract Expert" in result.output
    assert "map_ppa_obligations" in result.output
    assert "[artifact]" in result.output


@respx.mock
def test_digest_json_flag_emits_raw() -> None:
    respx.get(ENDPOINT, params={"view": "digest"}).mock(return_value=httpx.Response(200, json=_DIGEST))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "digest", "sess-1", "--json"])
    assert result.exit_code == 0, result.output
    assert '"view"' not in result.output  # it's the body, not the request
    assert '"tool_calls"' in result.output


@respx.mock
def test_digest_empty_prints_friendly_notice() -> None:
    empty = {"session_id": "sess-1", "tool_calls": [], "delegations": [], "session_start_ts": None}
    respx.get(ENDPOINT, params={"view": "digest"}).mock(return_value=httpx.Response(200, json=empty))
    result = CliRunner().invoke(main, ["--env", "local", "sessions", "digest", "sess-1"])
    assert result.exit_code == 0, result.output
    assert "No curated digest items" in result.output
