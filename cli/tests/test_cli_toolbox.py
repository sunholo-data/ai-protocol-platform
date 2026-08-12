"""Tests for `aiplatform toolbox` — validate (static gates) + probe (MCP client).

`validate`'s deep check shells out to the toolbox binary, which isn't present in
CI, so these cover the static gates (which are the security-critical part) and
the probe's HTTP behaviour. The binary path is exercised live in the sprint's
manual verification, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

_SAFE = """\
kind: source
name: s
type: bigquery
project: p
location: europe-west4
writeMode: blocked
---
kind: tool
name: t
type: bigquery-sql
source: s
description: safe
statement: SELECT 1
---
kind: toolset
name: ts
tools: [t]
"""

_UNSAFE = """\
kind: source
name: s
type: bigquery
project: p
location: europe-west4
---
kind: tool
name: t
type: bigquery-sql
source: s
description: unsafe
statement: SELECT `{{.col}}` FROM `p.d.tbl`
templateParameters:
  - name: col
    type: string
    description: injectable
---
kind: toolset
name: ts
tools: [t]
"""


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "tools.yaml"
    p.write_text(body)
    return str(p)


def test_validate_static_gates_pass_on_safe_config(tmp_path: Path) -> None:
    # A safe config passes the static gates. The deep binary check is skipped
    # when the binary is absent (CI), which is not a failure.
    result = CliRunner().invoke(main, ["toolbox", "validate", _write(tmp_path, _SAFE)])
    assert result.exit_code == 0, result.output
    assert "no templateParameters, all sources read-only" in result.output


def test_validate_flags_templateparameters(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["toolbox", "validate", _write(tmp_path, _UNSAFE)])
    assert result.exit_code != 0
    assert "templateParameters" in result.output
    assert "SQL-injection" in result.output


def test_validate_flags_missing_writemode_blocked(tmp_path: Path) -> None:
    # _UNSAFE also omits writeMode: blocked on its source.
    result = CliRunner().invoke(main, ["toolbox", "validate", _write(tmp_path, _UNSAFE)])
    assert result.exit_code != 0
    assert "writeMode: blocked" in result.output


def test_validate_does_not_false_positive_on_comment_mentions(tmp_path: Path) -> None:
    # The words appear in a comment but not in any actual tool/source mapping.
    body = "# templateParameters and writeMode are discussed here\n" + _SAFE
    result = CliRunner().invoke(main, ["toolbox", "validate", _write(tmp_path, body)])
    assert result.exit_code == 0, result.output


@respx.mock
def test_probe_lists_tools() -> None:
    url = "http://127.0.0.1:5000/mcp/example"
    respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": [{"name": "popular_baby_names", "inputSchema": {"required": ["market"]}}]},
            },
        )
    )
    result = CliRunner().invoke(main, ["toolbox", "probe", url])
    assert result.exit_code == 0, result.output
    assert "1 tool(s)" in result.output
    assert "popular_baby_names" in result.output
    assert "required: ['market']" in result.output


@respx.mock
def test_probe_sends_streamable_http_accept_header() -> None:
    # A plain application/json Accept gets a 406 from some MCP servers; the probe
    # must send both types. Guard against a regression that drops the second.
    url = "http://127.0.0.1:5000/mcp/example"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
    )
    result = CliRunner().invoke(main, ["toolbox", "probe", url])
    assert result.exit_code == 0, result.output
    accept = route.calls.last.request.headers["accept"]
    assert "application/json" in accept and "text/event-stream" in accept


@respx.mock
def test_probe_calls_tool_with_args() -> None:
    url = "http://127.0.0.1:5000/mcp/example"
    route = respx.post(url).mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "t"}]}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "42"}]}}),
        ]
    )
    result = CliRunner().invoke(main, ["toolbox", "probe", url, "--call", "t", "--args", '{"market":"sweden_4"}'])
    assert result.exit_code == 0, result.output
    assert "42" in result.output
    call_body = json.loads(route.calls[-1].request.content)
    assert call_body["params"] == {"name": "t", "arguments": {"market": "sweden_4"}}


def test_probe_reports_unreachable_server() -> None:
    result = CliRunner().invoke(main, ["toolbox", "probe", "http://127.0.0.1:5999/mcp/nope"])
    assert result.exit_code != 0
    assert "cannot reach" in result.output
