"""Tier names must resolve before raw-model-string prefix checks.

v6.6.0 M4 regression guard: M1 let SkillMetadata.model hold a tier name
(`lite`/`smart`). Code that inspects the raw model string — the Gemini planner
selector and the code-execution guard — must resolve the tier to an api name
first, or a `lite` (Gemini) skill silently loses its planner.
"""

from __future__ import annotations

from adk.agent import _planner_for
from db.models import SkillConfig, SkillMetadata


def _skill(model: str, thinking_model: str | None = None) -> SkillConfig:
    return SkillConfig(
        name="tier-skill",
        description="x",
        skillMetadata=SkillMetadata(model=model, thinkingModel=thinking_model),
    )


class TestPlannerTierResolution:
    def test_lite_tier_gemini_gets_planner(self):
        # lite -> gemini-3.5-flash-lite (dev/test) -> Gemini -> planner attached.
        assert _planner_for(_skill("lite")) is not None

    def test_smart_tier_claude_gets_no_planner(self):
        # smart -> claude-opus-4-8 (dev/test) -> not Gemini -> no planner.
        assert _planner_for(_skill("smart")) is None

    def test_raw_gemini_id_still_gets_planner(self):
        assert _planner_for(_skill("gemini-2.5-flash")) is not None

    def test_thinking_model_set_returns_none(self):
        assert _planner_for(_skill("lite", thinking_model="smart")) is None


def test_gemini_planner_streams_thought_summaries():
    """MODEL-RELIABILITY M4: include_thoughts must be ON — the REASONING→
    ThinkingPanel pipeline is wired end-to-end but stays dark without it,
    and a silent thinking phase is indistinguishable from a hang."""
    planner = _planner_for(_skill("lite"))
    assert planner is not None
    assert planner.thinking_config.include_thoughts is True
