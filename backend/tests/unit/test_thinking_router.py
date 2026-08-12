"""Unit tests for the three-tier thinking strategy (AGENT-FACTORY M2).

Tier 1 — Gemini skills with no `thinking_model`: a single LlmAgent with
  `BuiltInPlanner(ThinkingConfig(thinking_budget=-1))` (Gemini 2.5 dynamic
  thinking — the model decides whether and how much to reason).

Tier 2 — Claude / OpenAI skills with no `thinking_model`: a single LlmAgent
  with no planner (these providers don't support BuiltInPlanner).

Tier 3 — Any skill with `thinking_model` set: the factory returns a
  `_HeuristicRouter(fast, thinking, picker)` that picks between two
  agents based on `_should_think(message)`.
"""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner

from adk.agent import (
    _HeuristicRouter,
    _planner_for,
    _should_think,
    create_agent_with_thinking,
)
from auth.firebase_auth import User
from db.models import SkillConfig, SkillMetadata


def _user() -> User:
    return User(uid="u1", email="alice@example.com", domain="example.com")


def _skill(
    model: str = "gemini-2.5-flash",
    thinking_model: str | None = None,
) -> SkillConfig:
    return SkillConfig(
        name="test-skill",
        description="skill under test",
        instructions="Be helpful.",
        skillId="11111111-1111-1111-1111-111111111111",
        skillMetadata=SkillMetadata(model=model, thinkingModel=thinking_model),
    )


# --- _planner_for ---


def test_planner_for_gemini_without_thinking_model_returns_builtinplanner():
    planner = _planner_for(_skill(model="gemini-2.5-flash"))
    assert isinstance(planner, BuiltInPlanner)


def test_planner_for_gemini_pro_without_thinking_model_returns_builtinplanner():
    planner = _planner_for(_skill(model="gemini-2.5-pro"))
    assert isinstance(planner, BuiltInPlanner)


def test_planner_for_claude_returns_none():
    # Claude/OpenAI don't support BuiltInPlanner — use the router instead.
    assert _planner_for(_skill(model="claude-opus-4-7")) is None


def test_planner_for_openai_returns_none():
    assert _planner_for(_skill(model="gpt-4o")) is None


def test_planner_for_gemini_with_thinking_model_returns_none():
    # When thinking_model is set, routing happens in Python via the heuristic
    # router; there is no single-agent planner.
    skill = _skill(model="gemini-2.5-flash", thinking_model="gemini-2.5-pro")
    assert _planner_for(skill) is None


# --- _should_think heuristic ---


def test_should_think_false_for_short_simple_message():
    assert _should_think("hi") is False
    assert _should_think("what time is it") is False


def test_should_think_true_for_long_message_over_280_chars():
    msg = "a" * 281
    assert _should_think(msg) is True


def test_should_think_true_for_think_keyword():
    assert _should_think("please analyze this") is True
    assert _should_think("can you reason about this") is True
    assert _should_think("compare these two options") is True


def test_should_think_true_for_multiple_question_marks():
    assert _should_think("why? how? what?") is True


def test_should_think_false_for_single_question_mark():
    assert _should_think("what is your name?") is False


# --- create_agent_with_thinking dispatch ---


def test_create_agent_with_thinking_returns_single_agent_when_no_thinking_model():
    agent = create_agent_with_thinking(_skill(model="gemini-2.5-flash"), _user())
    assert isinstance(agent, LlmAgent)
    # Gemini + no thinking_model -> planner attached
    assert isinstance(agent.planner, BuiltInPlanner)


def test_create_agent_with_thinking_claude_returns_single_agent_no_planner():
    agent = create_agent_with_thinking(_skill(model="claude-opus-4-7"), _user())
    assert isinstance(agent, LlmAgent)
    assert agent.planner is None


def test_create_agent_with_thinking_returns_router_when_thinking_model_set():
    skill = _skill(model="gemini-2.5-flash", thinking_model="gemini-2.5-pro")
    router = create_agent_with_thinking(skill, _user())
    assert isinstance(router, _HeuristicRouter)
    assert isinstance(router.fast, LlmAgent)
    assert isinstance(router.thinking, LlmAgent)
    assert callable(router.picker)


def test_heuristic_router_picks_fast_for_short_message():
    skill = _skill(model="gemini-2.5-flash", thinking_model="gemini-2.5-pro")
    router = create_agent_with_thinking(skill, _user())
    assert router.pick_agent("hi") is router.fast


def test_heuristic_router_picks_thinking_for_complex_message():
    skill = _skill(model="gemini-2.5-flash", thinking_model="gemini-2.5-pro")
    router = create_agent_with_thinking(skill, _user())
    assert router.pick_agent("analyze this carefully") is router.thinking


def test_heuristic_router_claude_thinking_model_is_supported():
    # Claude fast + Claude thinking: both agents built, no planner on either.
    skill = _skill(model="claude-haiku-4-5", thinking_model="claude-opus-4-7")
    router = create_agent_with_thinking(skill, _user())
    assert router.fast.planner is None
    assert router.thinking.planner is None


@pytest.mark.parametrize(
    "model, thinking_model, expected_type",
    [
        ("gemini-2.5-flash", None, LlmAgent),
        ("claude-opus-4-7", None, LlmAgent),
        ("gpt-4o", None, LlmAgent),
        ("gemini-2.5-flash", "gemini-2.5-pro", _HeuristicRouter),
        ("claude-haiku-4-5", "claude-opus-4-7", _HeuristicRouter),
    ],
)
def test_thinking_dispatch_matrix(model, thinking_model, expected_type):
    skill = _skill(model=model, thinking_model=thinking_model)
    result = create_agent_with_thinking(skill, _user())
    assert isinstance(result, expected_type)
