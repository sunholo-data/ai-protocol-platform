"""Tests for `aiplatform skill push`."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"

_SKILL_MD = """---
display_name: Test Skill
description: A pushed description.
initial_message: hello
tags:
  - alpha
  - beta
metadata:
  model: lite
  tools:
    - a
    - b
---

This is the new instruction body.
Second line.
"""


def _write_template(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(_SKILL_MD)
    return p


@respx.mock
def test_skill_push_puts_parsed_template(tmp_path) -> None:
    """`skill push X --file SKILL.md` parses frontmatter+body and PUTs the
    instructions + skillMetadata (+ description/displayName/tags/initialMessage)."""
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "sk1",
                "name": "test-skill",
                "instructions": "old body",
                "skillMetadata": {"model": "smart"},
            },
        )
    )
    put_route = respx.put(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(200, json={"skillId": "sk1", "name": "test-skill"})
    )
    template = _write_template(tmp_path)

    result = CliRunner().invoke(main, ["--env", "local", "skill", "push", "sk1", "--file", str(template)])
    assert result.exit_code == 0, result.output
    assert put_route.called
    body = json.loads(put_route.calls.last.request.content)
    assert body["instructions"].startswith("This is the new instruction body.")
    assert body["skillMetadata"] == {"model": "lite", "tools": ["a", "b"]}
    assert body["description"] == "A pushed description."
    assert body["displayName"] == "Test Skill"
    assert body["initialMessage"] == "hello"
    assert body["tags"] == ["alpha", "beta"]
    assert "✓ pushed" in result.output


@respx.mock
def test_skill_push_dry_run_does_not_write(tmp_path) -> None:
    """`--dry-run` shows the diff but never PUTs."""
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(200, json={"skillId": "sk1", "name": "test-skill", "instructions": "old body"})
    )
    put_route = respx.put(f"{BASE}/api/skills/sk1").mock(return_value=httpx.Response(200, json={}))
    template = _write_template(tmp_path)

    result = CliRunner().invoke(main, ["--env", "local", "skill", "push", "sk1", "--file", str(template), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not put_route.called
    assert "dry-run" in result.output
    # unified diff shows the new body arriving
    assert "This is the new instruction body." in result.output


@respx.mock
def test_skill_push_noop_when_identical(tmp_path) -> None:
    """When the live skill already matches the template, nothing is pushed."""
    frontmatter_instr = "This is the new instruction body.\nSecond line."
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "sk1",
                "name": "test-skill",
                "instructions": frontmatter_instr,
                "description": "A pushed description.",
                "displayName": "Test Skill",
                "initialMessage": "hello",
                "tags": ["alpha", "beta"],
                "skillMetadata": {"model": "lite", "tools": ["a", "b"]},
            },
        )
    )
    put_route = respx.put(f"{BASE}/api/skills/sk1").mock(return_value=httpx.Response(200, json={}))
    template = _write_template(tmp_path)

    result = CliRunner().invoke(main, ["--env", "local", "skill", "push", "sk1", "--file", str(template)])
    assert result.exit_code == 0, result.output
    assert not put_route.called
    assert "already up to date" in result.output
