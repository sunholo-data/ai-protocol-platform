"""Unit tests for the named-tier layer on top of the model registry.

v6.6.0 ONE-FORK-CONVERGENCE M1: a skill may reference a logical tier
(`lite`, `smart`) instead of a raw model id. `lite` is ONE's day-to-day
default (fast Gemini); `smart` is the deep-work tier delegated to sub-skills
(Anthropic). Raw ids and registry ids must keep resolving for back-compat.
"""

from __future__ import annotations

import pytest

from config.models import (
    TIER_NAMES,
    api_name_for,
    default_model,
    load_models_config,
    resolve_tier,
)


class TestTierDefaults:
    def test_registry_declares_tier_defaults(self):
        cfg = load_models_config()
        assert cfg.tier_defaults, "models.yaml must declare tier_defaults"

    def test_tier_defaults_reference_valid_ids(self):
        cfg = load_models_config()
        ids = {m.id for m in cfg.models}
        for tier, model_id in cfg.tier_defaults.items():
            assert model_id in ids, f"tier {tier!r} -> {model_id!r} not in models"

    def test_lite_and_smart_tiers_exist(self):
        cfg = load_models_config()
        assert "lite" in cfg.tier_defaults
        assert "smart" in cfg.tier_defaults

    def test_tier_names_set_matches_config(self):
        cfg = load_models_config()
        assert TIER_NAMES == set(cfg.tier_defaults.keys())


class TestResolveTier:
    def test_lite_resolves_to_eu_resident_under_eu_strict(self, monkeypatch):
        # THE invariant: an eu-strict deployment (prod) must get an EU-resident
        # `lite`, whatever the unrestricted variant is set to. 2026-08-13: now
        # gemini-3.5-flash-lite pinned to the "eu" jurisdictional multi-region
        # endpoint (gemini-2.5-flash-lite has no single/multi-region EU option
        # of its own generation still alive much longer — 2.5 family EOLs
        # 2026-10-16). See models.yaml gemini-3-5-flash-lite-eu note.
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        entry = resolve_tier("lite")
        assert entry.provider == "google"
        assert entry.residency == "eu"
        assert entry.api_name == "gemini-3.5-flash-lite"
        assert entry.location == "eu"

    def test_lite_resolves_to_faster_3x_under_unrestricted(self, monkeypatch):
        # dev + test run MODEL_RESIDENCY_POLICY=unrestricted and take the
        # faster global-endpoint variant (benchmarked 2026-07-21: lower TTFT,
        # higher decode rate, tighter tail than the 2.5 lite — see models.yaml).
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
        entry = resolve_tier("lite")
        assert entry.provider == "google"
        assert entry.api_name == "gemini-3.5-flash-lite"

    def test_every_tier_is_eu_resident_under_eu_strict(self, monkeypatch):
        # Generalises the invariant above to every declared tier, so adding a
        # tier with a non-EU `eu-strict` variant fails here rather than at
        # load time on prod (resolve_model_chain raises for a non-EU primary).
        monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
        for tier in TIER_NAMES:
            assert resolve_tier(tier).residency == "eu", f"tier {tier!r} is not EU-resident under eu-strict"

    def test_smart_resolves_to_anthropic_claude_entry(self):
        # Deep-work tier must route to Anthropic (Mark's decision).
        entry = resolve_tier("smart")
        assert entry.provider == "anthropic"
        assert entry.api_name.startswith("claude-")

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            resolve_tier("ultra")


class TestApiNameFor:
    def test_tier_name_maps_to_api_name(self):
        assert api_name_for("smart") == resolve_tier("smart").api_name

    def test_registry_id_maps_to_its_api_name(self):
        # gemini-2-5-flash (registry id) -> gemini-2.5-flash (api_name)
        assert api_name_for("gemini-2-5-flash") == "gemini-2.5-flash"

    def test_raw_api_name_passes_through(self):
        # Back-compat: a raw provider id we don't know about is returned as-is.
        assert api_name_for("gemini-2.5-flash") == "gemini-2.5-flash"
        assert api_name_for("claude-sonnet-4-6") == "claude-sonnet-4-6"


class TestDefaultModel:
    def test_default_model_is_platform_default_api_name(self):
        cfg = load_models_config()
        platform_entry = next(m for m in cfg.models if m.id == cfg.platform_default)
        assert default_model() == platform_entry.api_name


class TestClaudeAdaptiveThinking:
    """MODEL-RELIABILITY M4 — adaptive thinking on Claude via LiteLlm kwargs.

    Live-verified 2026-07-10: litellm forwards `thinking` to Anthropic;
    Opus/Sonnet accept adaptive and stream summarized reasoning; Haiku
    rejects it with a 400 ("adaptive thinking is not supported"). display:
    summarized is mandatory on Opus 4.7+ (default omitted = empty text).
    """

    def test_opus_4_7_uses_adaptive_thinking(self, monkeypatch):
        """2026-07-24 (0a583f5): opus-4-8/4-7 send the NEW shape, not reasoning_effort.

        Supersedes the 2026-07-16 contract. The API TOLERATES the old
        enabled+budget_tokens shape on opus-4-8 but silently does no thinking —
        the "dark ThinkingPanel" — so these models moved to
        thinking={type:adaptive, display:summarized} + output_config.effort,
        the same shape the Claude 5 family requires (f7d7914).
        """
        monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
        from adk.agent import resolve_model

        model = resolve_model("claude-opus-4-7")
        assert model._additional_args["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert model._additional_args["output_config"]["effort"] in ("high", "medium", "low")
        # reasoning_effort is the PRE-4.6 path — sending both would be ambiguous.
        assert "reasoning_effort" not in model._additional_args

    def test_pre_4_6_claude_still_uses_reasoning_effort(self, monkeypatch):
        """The old line (sonnet-4-5 / 3.x) keeps reasoning_effort — litellm maps it
        per-model. Guards the `-4-5` vs `-5` regex boundary in
        `_claude_uses_adaptive_thinking` (a `-5` suffix match would wrongly catch
        haiku-4-5 / sonnet-4-5)."""
        monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
        from adk.agent import resolve_model

        model = resolve_model("claude-sonnet-4-5")
        assert model._additional_args.get("reasoning_effort") in ("high", "medium", "low")
        assert "thinking" not in model._additional_args

    def test_claude_5_uses_adaptive_thinking(self, monkeypatch):
        """The 5 family HARD 400s on the old shape (every sonnet-5 handoff
        RUN_ERRORed live 2026-07-24) — pin the new shape."""
        monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
        from adk.agent import resolve_model

        model = resolve_model("claude-sonnet-5")
        assert model._additional_args["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert "reasoning_effort" not in model._additional_args

    def test_haiku_stays_bare(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_ADAPTIVE_THINKING", raising=False)
        from adk.agent import resolve_model

        model = resolve_model("claude-haiku-4-5")
        assert "reasoning_effort" not in model._additional_args
        assert "thinking" not in model._additional_args

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_ADAPTIVE_THINKING", "off")
        from adk.agent import resolve_model

        model = resolve_model("claude-opus-4-7")
        assert "reasoning_effort" not in model._additional_args
        assert "thinking" not in model._additional_args
        assert "output_config" not in model._additional_args
