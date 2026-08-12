"""Tests for `aiplatform skill list [--jobs]` (v6.8.0 8.3 JOBS)."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"

_SKILLS = [
    {"slug": "one-assistant", "displayName": "ONE Assistant", "skillMetadata": {"model": "lite"}},
    {
        "slug": "one-obligation-analysis",
        "displayName": "PPA Obligation Analysis",
        "skillMetadata": {"model": "lite", "job": True, "jobFloor": "confirm"},
    },
]


@respx.mock
def test_skill_list_shows_all() -> None:
    respx.get(f"{BASE}/api/skills").mock(return_value=httpx.Response(200, json=_SKILLS))
    result = CliRunner().invoke(main, ["--env", "local", "skill", "list"])
    assert result.exit_code == 0, result.output
    assert "one-assistant" in result.output
    assert "one-obligation-analysis" in result.output


@respx.mock
def test_skill_list_jobs_filters_to_jobs() -> None:
    respx.get(f"{BASE}/api/skills").mock(return_value=httpx.Response(200, json=_SKILLS))
    result = CliRunner().invoke(main, ["--env", "local", "skill", "list", "--jobs"])
    assert result.exit_code == 0, result.output
    assert "one-obligation-analysis" in result.output
    assert "job:confirm" in result.output
    assert "one-assistant" not in result.output  # non-job filtered out


@respx.mock
def test_skill_list_jobs_empty() -> None:
    respx.get(f"{BASE}/api/skills").mock(
        return_value=httpx.Response(200, json=[_SKILLS[0]])  # no jobs
    )
    result = CliRunner().invoke(main, ["--env", "local", "skill", "list", "--jobs"])
    assert result.exit_code == 0, result.output
    assert "No job skills found." in result.output
