"""Per-skill thinking depth — the `thinking:` field and the two-agent path.

A front door and a chaining specialist want opposite thinking budgets, so depth
is declared per skill rather than handed out uniformly. Two behaviours are
guarded here:

  1. `thinking:` on a skill reaches its planner (default `dynamic` = the
     pre-field behaviour, so untouched skills are unaffected).
  2. A `thinkingModel` skill gets a planner on BOTH agents. It previously got
     one on NEITHER — the five deepest skills in the fleet reasoned at the bare
     API default with include_thoughts unset, i.e. silently, with a dark
     ThinkingPanel (CLAUDE.md #8).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adk.agent import _planner_at, _planner_for
from config.thinking import ThinkDepth
from db.models import SkillConfig, SkillMetadata


def _skill(model: str, thinking: str = "dynamic", thinking_model: str | None = None) -> SkillConfig:
    return SkillConfig(
        name="depth-skill",
        description="x",
        skillMetadata=SkillMetadata(model=model, thinking=thinking, thinkingModel=thinking_model),
    )


class TestSkillDeclaredDepth:
    def test_default_is_dynamic_for_back_compat(self):
        # Every Gemini skill got dynamic thinking before this field existed;
        # a skill that declares nothing must not change behaviour.
        assert _skill("lite").skill_metadata.thinking == "dynamic"
        assert _planner_for(_skill("lite")).thinking_config.thinking_budget == -1

    def test_off_reaches_the_planner(self):
        planner = _planner_for(_skill("lite", thinking="off"))
        assert planner.thinking_config.thinking_budget == 0

    def test_low_reaches_the_planner(self):
        # `lite` resolves to a 3.x model on dev/test, where LOW is expressed as
        # thinking_level rather than a budget — the seam handles that split.
        cfg = _planner_for(_skill("lite", thinking="low")).thinking_config
        assert (cfg.thinking_level is not None) or (cfg.thinking_budget == 512)

    def test_dynamic_still_streams_thought_summaries(self):
        # MODEL-RELIABILITY M4 — regression guard on the ThinkingPanel.
        assert _planner_for(_skill("lite")).thinking_config.include_thoughts is True

    def test_bare_yaml_off_is_coerced_from_boolean_false(self):
        # YAML 1.1 parses an unquoted `thinking: off` as the BOOLEAN False.
        # This shipped a real (silent) seed failure — data-extractor was the
        # only skill declaring `off` and the only one in the seed's
        # `failed: [...]`, invisible because that step is non-fatal.
        assert SkillMetadata(model="lite", thinking=False).thinking == "off"
        assert _planner_for(_skill("lite", thinking="off")).thinking_config.thinking_budget == 0

    def test_yaml_true_still_fails_loudly(self):
        # `on`/`yes` parse to True and have no sensible depth — guessing one
        # would be worse than erroring.
        with pytest.raises(ValidationError):
            SkillMetadata(model="lite", thinking=True)

    def test_invalid_depth_is_rejected_at_config_load(self):
        # A typo in SKILL.md must fail loudly at load, not silently fall back
        # to a depth nobody chose.
        with pytest.raises(ValidationError):
            SkillMetadata(model="lite", thinking="deep")


class TestPlannerAt:
    def test_returns_none_for_non_gemini(self):
        # BuiltInPlanner is Gemini-specific; Claude/OpenAI use reasoning_effort.
        assert _planner_at(ThinkDepth.DYNAMIC, "claude-opus-4-8") is None

    def test_returns_a_planner_for_gemini(self):
        assert _planner_at(ThinkDepth.DYNAMIC, "lite") is not None

    def test_never_builds_a_planner_with_an_empty_config(self):
        # A BuiltInPlanner carrying thinking_config=None would be a silent no-op
        # dressed as a planner.
        for depth in ThinkDepth:
            p = _planner_at(depth, "lite")
            assert p is not None and p.thinking_config is not None


class TestTwoAgentSkillsGetPlanners:
    def test_single_agent_path_still_declines_for_thinking_model_skills(self):
        # create_agent_with_thinking supplies both planners explicitly, so the
        # single-agent helper must stay out of the way.
        assert _planner_for(_skill("lite", thinking_model="smart")) is None

    def test_fast_agent_planner_uses_the_skills_declared_depth(self):
        md = _skill("pro", thinking="low", thinking_model="smart").skill_metadata
        cfg = _planner_at(ThinkDepth(md.thinking), md.model).thinking_config
        assert cfg.thinking_budget != -1, "fast agent should not run full dynamic"

    def test_thinking_agent_planner_is_always_dynamic(self):
        # Being the thinking one is the point — it does not inherit the fast
        # agent's reduced depth.
        cfg = _planner_at(ThinkDepth.DYNAMIC, "pro").thinking_config
        assert cfg.thinking_budget == -1
        assert cfg.include_thoughts is True

    def test_claude_thinking_model_gets_no_planner(self):
        # `smart` is Claude on dev/test — reasoning_effort handles it, and a
        # Gemini planner there would be meaningless.
        assert _planner_at(ThinkDepth.DYNAMIC, "smart") is None
