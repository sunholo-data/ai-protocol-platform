"""Unit tests for LLM cost estimation and metrics recording."""

from __future__ import annotations

from unittest.mock import patch

from observability.llm_metrics import estimate_cost, record_llm_cost


class TestEstimateCost:
    def test_gemini_flash(self):
        # 1000 input, 500 output at gemini-2.5-flash rates (0.15/1M, 0.60/1M)
        cost = estimate_cost("gemini-2.5-flash", 1000, 500)
        expected = (1000 * 0.15 + 500 * 0.60) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_gemini_pro(self):
        cost = estimate_cost("gemini-2.5-pro", 10_000, 5_000)
        expected = (10_000 * 1.25 + 5_000 * 10.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_claude_sonnet(self):
        cost = estimate_cost("claude-sonnet", 2000, 1000)
        expected = (2000 * 3.00 + 1000 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_claude_haiku(self):
        cost = estimate_cost("claude-haiku", 5000, 2000)
        expected = (5000 * 0.25 + 2000 * 1.25) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_gpt4o(self):
        cost = estimate_cost("gpt-4o", 3000, 1000)
        expected = (3000 * 2.50 + 1000 * 10.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_unknown_model_returns_zero(self):
        assert estimate_cost("unknown-model-xyz", 1000, 500) == 0.0

    def test_zero_tokens(self):
        assert estimate_cost("gemini-2.5-flash", 0, 0) == 0.0

    def test_model_name_substring_match(self):
        """Model names with version suffixes should still match."""
        cost = estimate_cost("gemini-2.5-flash-001", 1000, 500)
        assert cost > 0

    def test_vertex_ai_prefix_match(self):
        """Models accessed via Vertex AI may have prefixed names."""
        cost = estimate_cost("publishers/google/models/gemini-2.5-pro", 1000, 500)
        assert cost > 0


class TestRecordLlmCost:
    def test_calls_counters(self):
        with (
            patch("observability.llm_metrics.cost_counter") as mock_cost,
            patch("observability.llm_metrics.token_counter") as mock_tokens,
        ):
            record_llm_cost("gemini-2.5-flash", 1000, 500)
            mock_cost.add.assert_called_once()
            mock_tokens.add.assert_called_once()
            # Token counter should get total tokens
            token_args = mock_tokens.add.call_args[0]
            assert token_args[0] == 1500
