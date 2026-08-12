"""Tests for `aiplatform skill set`."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


@respx.mock
def test_skill_set_merges_model_into_metadata_and_puts() -> None:
    """`skill set X --model-tier smart` GETs then PUTs skillMetadata.model=smart,
    preserving other skillMetadata fields, and prints the resulting model."""
    respx.get(f"{BASE}/api/skills/one-ppa-expert").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "one-ppa-expert",
                "skillMetadata": {"model": "lite", "temperature": 0.2},
            },
        )
    )
    put_route = respx.put(f"{BASE}/api/skills/one-ppa-expert").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "one-ppa-expert",
                "skillMetadata": {"model": "smart", "temperature": 0.2},
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "skill", "set", "one-ppa-expert", "--model-tier", "smart"],
    )
    assert result.exit_code == 0, result.output
    assert put_route.called
    body = json.loads(put_route.calls.last.request.content)
    assert body["skillMetadata"]["model"] == "smart"
    # Other skillMetadata fields preserved through the merge.
    assert body["skillMetadata"]["temperature"] == 0.2
    assert "smart" in result.output


@respx.mock
def test_skill_set_accepts_raw_model_id() -> None:
    """A raw registry id (not a logical tier) is passed through unvalidated."""
    respx.get(f"{BASE}/api/skills/general-assistant").mock(
        return_value=httpx.Response(200, json={"id": "general-assistant", "skillMetadata": {}})
    )
    put_route = respx.put(f"{BASE}/api/skills/general-assistant").mock(
        return_value=httpx.Response(
            200,
            json={"id": "general-assistant", "skillMetadata": {"model": "claude-opus-4-7"}},
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "skill", "set", "general-assistant", "--model-tier", "claude-opus-4-7"],
    )
    assert result.exit_code == 0, result.output
    assert put_route.called
    body = json.loads(put_route.calls.last.request.content)
    assert body["skillMetadata"]["model"] == "claude-opus-4-7"
    assert "claude-opus-4-7" in result.output
