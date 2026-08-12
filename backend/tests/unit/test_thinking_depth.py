"""Unit tests for the per-workload thinking seam (config/thinking.py).

The parameter split is the load-bearing part: `thinking_level` is 3.x-only and
a 2.5 model REJECTS it with a hard 400 ("thinking_level is not supported by
..."), so a family regression here is an outage, not a slowdown. Values below
were verified against live Vertex on 2026-07-21.
"""

from __future__ import annotations

import pytest
from google.genai.types import ThinkingLevel

from config.thinking import ThinkDepth, thinking_config_dict_for, thinking_config_for

# Registry ids, resolved through api_name_for so tier/residency changes to
# models.yaml can't silently break the family mapping.
GEMINI_2_5 = "gemini-flash-lite"  # -> gemini-2.5-flash-lite
GEMINI_3_X = "gemini-3-6-flash"  # -> gemini-3.6-flash


class TestOff:
    def test_off_uses_zero_budget_on_both_families(self):
        # thinking_budget=0 is the ONE setting accepted by both families
        # (verified live), so OFF needs no family branch.
        for ref in (GEMINI_2_5, GEMINI_3_X):
            cfg = thinking_config_for(ThinkDepth.OFF, ref)
            assert cfg.thinking_budget == 0, ref
            assert cfg.thinking_level is None, f"{ref}: level would 400 on 2.5"

    def test_off_does_not_request_thought_summaries(self):
        # Nothing to summarise when nothing is thought; asking for thoughts
        # here would just add wire noise.
        assert not thinking_config_for(ThinkDepth.OFF, GEMINI_2_5).include_thoughts


class TestFamilySplit:
    def test_low_uses_thinking_level_on_3x_only(self):
        cfg = thinking_config_for(ThinkDepth.LOW, GEMINI_3_X)
        assert cfg.thinking_level == ThinkingLevel.LOW
        assert cfg.thinking_budget is None

    def test_low_uses_a_numeric_budget_on_2_5(self):
        # 2.5 rejects thinking_level outright, so LOW must degrade to a budget.
        cfg = thinking_config_for(ThinkDepth.LOW, GEMINI_2_5)
        assert cfg.thinking_level is None, "thinking_level 400s on the 2.5 family"
        assert cfg.thinking_budget == 512

    def test_2_5_low_budget_respects_the_api_minimum(self):
        # Live-verified: budget=128 is REJECTED with a 400, budget=512 is
        # accepted. A future tweak below the floor must fail here, not in prod.
        assert thinking_config_for(ThinkDepth.LOW, GEMINI_2_5).thinking_budget >= 512

    def test_no_2_5_depth_ever_emits_thinking_level(self):
        # The regression that would be a hard outage rather than a slowdown.
        for depth in ThinkDepth:
            cfg = thinking_config_for(depth, GEMINI_2_5)
            assert cfg.thinking_level is None, f"{depth} would 400 on a 2.5 model"


class TestDynamic:
    def test_dynamic_is_budget_minus_one_on_both_families(self):
        # Accepted by both (verified live), so DYNAMIC is a true no-op for
        # callers that previously hardcoded thinking_budget=-1.
        for ref in (GEMINI_2_5, GEMINI_3_X):
            assert thinking_config_for(ThinkDepth.DYNAMIC, ref).thinking_budget == -1, ref

    def test_dynamic_requests_thought_summaries(self):
        # MODEL-RELIABILITY M4: without include_thoughts the ThinkingPanel goes
        # dark and a long thinking phase reads to the user as a hang.
        for ref in (GEMINI_2_5, GEMINI_3_X):
            assert thinking_config_for(ThinkDepth.DYNAMIC, ref).include_thoughts is True, ref


class TestNonGemini:
    @pytest.mark.parametrize("ref", ["claude-opus-4-8", "gpt-5-6-luna"])
    def test_non_gemini_returns_none(self, ref):
        # Claude/OpenAI carry their own reasoning_effort wiring in
        # adk.agent.resolve_model — a Gemini ThinkingConfig is meaningless there.
        for depth in ThinkDepth:
            assert thinking_config_for(depth, ref) is None, f"{ref} / {depth}"

    def test_dict_helper_returns_none_for_non_gemini(self):
        assert thinking_config_dict_for(ThinkDepth.OFF, "claude-opus-4-8") is None


class TestDictHelper:
    def test_dict_helper_is_splattable_into_a_genai_config(self):
        # The raw-genai callers pass a config DICT, not a ThinkingConfig.
        d = thinking_config_dict_for(ThinkDepth.OFF, GEMINI_2_5)
        assert d == {"thinking_budget": 0}

    def test_dict_helper_drops_none_fields(self):
        # exclude_none keeps thinking_level out of 2.5 payloads entirely,
        # rather than sending an explicit null.
        d = thinking_config_dict_for(ThinkDepth.DYNAMIC, GEMINI_2_5)
        assert "thinking_level" not in d


class TestTierRefsResolve:
    def test_tier_name_resolves_to_the_right_family(self, monkeypatch):
        # `pro` is a 3.x model on dev/test and a 2.5 model on prod, so the SAME
        # tier ref must produce different parameters per residency policy.
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
        assert thinking_config_for(ThinkDepth.LOW, "pro").thinking_level == ThinkingLevel.LOW

        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        eu = thinking_config_for(ThinkDepth.LOW, "pro")
        assert eu.thinking_level is None
        assert eu.thinking_budget == 512
