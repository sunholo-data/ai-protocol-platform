"""Tests for `aiplatform models` subcommands."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"

_MODELS_PAYLOAD = {
    "models": [
        {
            "id": "gemini-flash-lite",
            "api_name": "gemini-2.0-flash-lite",
            "provider": "gemini",
            "tier": "lite",
            "context_window": 1000000,
        },
        {
            "id": "claude-opus-4-7",
            "api_name": "claude-opus-4-7-20260101",
            "provider": "anthropic",
            "tier": "smart",
            "context_window": 200000,
        },
    ],
    "defaults": {},
    "platform_default": "gemini-flash-lite",
    "tier_defaults": {"lite": "gemini-flash-lite", "smart": "claude-opus-4-7"},
}


@respx.mock
def test_models_tiers_renders_tier_table() -> None:
    respx.get(f"{BASE}/api/models").mock(return_value=httpx.Response(200, json=_MODELS_PAYLOAD))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "models", "tiers"])
    assert result.exit_code == 0, result.output
    # tier names
    assert "lite" in result.output
    assert "smart" in result.output
    # api_names resolved via the join on tier_defaults value -> models[] entry
    assert "gemini-2.0-flash-lite" in result.output
    assert "claude-opus-4-7-20260101" in result.output
    # platform default
    assert "gemini-flash-lite" in result.output


@respx.mock
def test_models_list_renders_all_models() -> None:
    respx.get(f"{BASE}/api/models").mock(return_value=httpx.Response(200, json=_MODELS_PAYLOAD))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "models", "list"])
    assert result.exit_code == 0, result.output
    assert "gemini-flash-lite" in result.output
    assert "claude-opus-4-7" in result.output
    assert "anthropic" in result.output
