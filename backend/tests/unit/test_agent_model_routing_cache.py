"""Claude prompt caching (adk/agent.py claude_prompt_cache_kwargs).

On Sunholo 2026-11-09 every Claude call of a project chat re-read 70K+ prompt
tokens (cached_tokens=0). Claude models now carry LiteLLM cache breakpoints on
the system message and the latest message; CLAUDE_PROMPT_CACHE=off removes them.
"""

from __future__ import annotations

from adk.agent import CLAUDE_CACHE_POINTS, resolve_model


def test_claude_models_cache_the_system_prompt_and_the_history(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROMPT_CACHE", raising=False)
    for model_id in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
        model = resolve_model(model_id)
        assert model._additional_args["cache_control_injection_points"] == CLAUDE_CACHE_POINTS, model_id


def test_the_cache_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CLAUDE_PROMPT_CACHE", "off")
    assert "cache_control_injection_points" not in resolve_model("claude-haiku-5-5")._additional_args


def test_gemini_models_are_untouched(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROMPT_CACHE", raising=False)
    model = resolve_model("gemini-3.5-flash-lite")
    assert not hasattr(model, "_additional_args") or "cache_control_injection_points" not in model._additional_args
