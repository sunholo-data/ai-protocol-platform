"""Unit tests for resolve_model() (AGENT-FACTORY M1).

Model IDs coming from SkillConfig.skill_metadata.model are dispatched to the
correct ADK wrapper. Three provider families:
  - Gemini: gemini-*  -> google.adk.models.Gemini
  - Claude: claude-*  -> LiteLlm (anthropic/ prefix; direct API, not Vertex)
  - OpenAI: gpt-* / o3*  -> google.adk.models.lite_llm.LiteLlm (openai/ prefix)
"""

from __future__ import annotations

import pytest
from google.adk.models import Gemini
from google.adk.models.lite_llm import LiteLlm

from adk.agent import resolve_model


def test_gemini_model_returns_gemini_wrapper():
    model = resolve_model("gemini-2.5-flash")
    assert isinstance(model, Gemini)
    assert model.model == "gemini-2.5-flash"


def test_gemini_pro_model_returns_gemini_wrapper():
    model = resolve_model("gemini-2.5-pro")
    assert isinstance(model, Gemini)


def test_claude_model_returns_litellm_with_anthropic_prefix():
    # Claude routes through the direct Anthropic API (LiteLLM anthropic/ prefix),
    # NOT Vertex — Anthropic Model Garden is unavailable in every Vertex region
    # for this project. Requires ANTHROPIC_API_KEY at runtime.
    model = resolve_model("claude-opus-4-7")
    assert isinstance(model, LiteLlm)
    assert "anthropic/claude-opus-4-7" in str(model.model)


def test_openai_gpt_model_returns_litellm_with_openai_prefix():
    model = resolve_model("gpt-4o")
    assert isinstance(model, LiteLlm)
    # LiteLlm stores the openai/-prefixed id
    assert "openai/gpt-4o" in str(model.model)


def test_openai_o3_model_returns_litellm():
    model = resolve_model("o3-mini")
    assert isinstance(model, LiteLlm)
    assert "openai/o3-mini" in str(model.model)


def test_openai_reasoning_model_sets_reasoning_effort_by_tier():
    """gpt-5.x reasoning models MUST pass reasoning_effort — it routes tool-using
    turns to the Responses API (else gpt-5.4+ rejects tools+reasoning on
    /v1/chat/completions) and sets the depth per tier. (2026-07-16.)"""
    for mid, expected in [("gpt-5-6-sol", "high"), ("gpt-5-6-terra", "medium"), ("gpt-5-6-luna", "low")]:
        model = resolve_model(mid)
        assert isinstance(model, LiteLlm)
        extra = getattr(model, "_additional_args", None) or {}
        assert extra.get("reasoning_effort") == expected, f"{mid} -> {extra.get('reasoning_effort')!r}"


def test_openai_non_reasoning_model_omits_reasoning_effort():
    """A non-reasoning model (gpt-4o) must NOT get reasoning_effort — it rejects it."""
    model = resolve_model("gpt-4o")
    extra = getattr(model, "_additional_args", None) or {}
    assert "reasoning_effort" not in extra


def test_openai_reasoning_requests_summary():
    """gpt-5.x reasoners request reasoning={summary:auto} so the Responses API
    returns the reasoning summary that streams to the ThinkingPanel."""
    model = resolve_model("gpt-5-6-sol")
    extra = getattr(model, "_additional_args", None) or {}
    assert extra.get("reasoning") == {"summary": "auto"}


def test_claude_5_family_uses_adaptive_thinking_not_reasoning_effort(monkeypatch):
    """Claude 5 family (sonnet-5, fable-5, …) REMOVED the old thinking API:
    `reasoning_effort` → litellm's `thinking={type:enabled, budget_tokens:N}` is
    a hard 400 on these models (every sonnet-5 handoff RUN_ERRORed, 2026-07-24).
    They must get `thinking={type:adaptive}` + `output_config.effort` instead."""
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    for mid in ("claude-sonnet-5", "claude-fable-5"):
        model = resolve_model(mid)
        extra = getattr(model, "_additional_args", None) or {}
        assert "reasoning_effort" not in extra, f"{mid} must NOT send reasoning_effort (400s)"
        assert extra.get("thinking") == {"type": "adaptive", "display": "summarized"}, mid
        assert extra.get("output_config", {}).get("effort") in ("high", "medium", "low"), mid


def test_claude_opus_48_uses_adaptive_thinking(monkeypatch):
    """opus-4-8/4-7 also use the new API. The old reasoning_effort path produced
    `thinking={type:enabled, budget_tokens:N}` — which the API TOLERATES on
    opus-4-8 but does no thinking (dark ThinkingPanel, 2026-07-16). They must send
    `thinking={type:adaptive}` + `output_config.effort` so reasoning actually
    streams; reasoning_effort must be ABSENT."""
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    for mid in ("claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6"):
        model = resolve_model(mid)
        extra = getattr(model, "_additional_args", None) or {}
        assert "reasoning_effort" not in extra, mid
        assert extra.get("thinking") == {"type": "adaptive", "display": "summarized"}, mid
        assert extra.get("output_config", {}).get("effort") in ("high", "medium", "low"), mid


def test_claude_4_5_is_not_treated_as_claude_5(monkeypatch):
    """The `-4-5` line (sonnet-4-5) still uses the old reasoning_effort path — it
    must NOT be misdetected as Claude 5 just because the id ends in `-5`."""
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    model = resolve_model("claude-sonnet-4-5")
    extra = getattr(model, "_additional_args", None) or {}
    assert "thinking" not in extra and "output_config" not in extra
    assert extra.get("reasoning_effort") in ("high", "medium", "low")


def test_claude_5_kill_switch_disables_thinking(monkeypatch):
    """CLAUDE_ADAPTIVE_THINKING=off leaves a Claude 5 model bare (no thinking /
    output_config / reasoning_effort) — the escape hatch still works."""
    monkeypatch.setenv("CLAUDE_ADAPTIVE_THINKING", "off")
    model = resolve_model("claude-sonnet-5")
    extra = getattr(model, "_additional_args", None) or {}
    assert "thinking" not in extra and "output_config" not in extra and "reasoning_effort" not in extra


def test_claude_haiku_omits_reasoning_effort(monkeypatch):
    """Haiku has no extended thinking — it must stay bare (rejects reasoning_effort)."""
    monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
    model = resolve_model("claude-haiku-4-5")
    extra = getattr(model, "_additional_args", None) or {}
    assert "reasoning_effort" not in extra


def test_unsupported_model_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported model"):
        resolve_model("mistral-large")


def test_empty_model_id_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported model"):
        resolve_model("")
