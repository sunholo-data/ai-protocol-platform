"""Tests for `aiplatform skill diff | pull | seed`."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"

_SKILL_MD = """---
display_name: Test Skill
description: A description.
metadata:
  model: lite
---

New instruction body.
"""


def _template(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(_SKILL_MD)
    return p


# --- diff ---------------------------------------------------------------------


@respx.mock
def test_skill_diff_reports_drift(tmp_path) -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(
            200, json={"skillId": "sk1", "name": "test-skill", "instructions": "old body", "skillMetadata": {}}
        )
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "diff", "sk1", "--file", str(_template(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "instructions: changed" in result.output
    assert "New instruction body." in result.output  # + side of the diff


@respx.mock
def test_skill_diff_in_sync(tmp_path) -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "sk1",
                "name": "test-skill",
                "instructions": "New instruction body.",
                "description": "A description.",
                "displayName": "Test Skill",
                "skillMetadata": {"model": "lite"},
            },
        )
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "diff", "sk1", "--file", str(_template(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "in sync" in result.output


# --- pull ---------------------------------------------------------------------


@respx.mock
def test_skill_pull_prints_full_json() -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(200, json={"skillId": "sk1", "name": "test-skill", "instructions": "body"})
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "pull", "sk1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["skillId"] == "sk1"


@respx.mock
def test_skill_pull_single_field() -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(200, json={"skillId": "sk1", "instructions": "just the body"})
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "pull", "sk1", "--field", "instructions"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "just the body"


@respx.mock
def test_skill_pull_out_file(tmp_path) -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(
        return_value=httpx.Response(200, json={"skillId": "sk1", "name": "test-skill"})
    )
    out = tmp_path / "skill.json"
    result = CliRunner().invoke(main, ["--env", "local", "skill", "pull", "sk1", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["skillId"] == "sk1"
    assert "wrote" in result.output


@respx.mock
def test_skill_pull_unknown_field_errors() -> None:
    respx.get(f"{BASE}/api/skills/sk1").mock(return_value=httpx.Response(200, json={"skillId": "sk1"}))
    result = CliRunner().invoke(main, ["--env", "local", "skill", "pull", "sk1", "--field", "nope"])
    assert result.exit_code != 0
    assert "not present" in result.output


# --- seed ---------------------------------------------------------------------


@respx.mock
def test_skill_seed_posts_admin_endpoint() -> None:
    route = respx.post(f"{BASE}/api/admin/seed-platform-skills").mock(
        return_value=httpx.Response(200, json={"created": 0, "refreshed": 3, "failed": []})
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "seed"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "seed complete" in result.output
    assert '"refreshed": 3' in result.output
