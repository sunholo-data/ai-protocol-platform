"""Unit tests for backend/config/models.py — YAML loader + Pydantic validation."""

from __future__ import annotations

import pytest

# These imports will fail until models.py is implemented — that's the TDD red state.
from config.models import ModelEntry, ModelsConfig, load_models_config


class TestLoadModelsConfig:
    def test_loads_without_error(self):
        cfg = load_models_config()
        assert isinstance(cfg, ModelsConfig)

    def test_has_models(self):
        cfg = load_models_config()
        assert len(cfg.models) >= 6

    def test_all_three_providers_present(self):
        cfg = load_models_config()
        providers = {m.provider for m in cfg.models}
        assert "google" in providers
        assert "anthropic" in providers
        assert "openai" in providers

    def test_all_tiers_present(self):
        cfg = load_models_config()
        tiers = {m.tier for m in cfg.models}
        assert "default" in tiers
        assert "smart" in tiers
        assert "fast" in tiers

    def test_platform_default_exists_in_models(self):
        cfg = load_models_config()
        model_ids = {m.id for m in cfg.models}
        assert cfg.platform_default in model_ids

    def test_defaults_reference_valid_model_ids(self):
        cfg = load_models_config()
        model_ids = {m.id for m in cfg.models}
        for provider, model_id in cfg.defaults.items():
            assert model_id in model_ids, f"default for {provider!r} → {model_id!r} not in models"

    def test_defaults_cover_all_three_providers(self):
        cfg = load_models_config()
        assert "google" in cfg.defaults
        assert "anthropic" in cfg.defaults
        assert "openai" in cfg.defaults

    def test_each_model_has_api_name(self):
        cfg = load_models_config()
        for m in cfg.models:
            assert m.api_name, f"model {m.id!r} has empty api_name"

    def test_context_windows_are_positive(self):
        cfg = load_models_config()
        for m in cfg.models:
            assert m.context_window > 0, f"model {m.id!r} has non-positive context_window"


class TestModelEntry:
    def test_valid_entry(self):
        entry = ModelEntry(
            id="test-model",
            api_name="test-model-preview",
            provider="google",
            tier="default",
            context_window=1_000_000,
            max_output_tokens=65_536,
            description="Test model",
        )
        assert entry.id == "test-model"
        assert entry.provider == "google"

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError):
            ModelEntry(
                id="bad",
                api_name="bad",
                provider="amazon",  # not a valid provider
                tier="default",
                context_window=100_000,
                max_output_tokens=8_000,
                description="bad",
            )

    def test_invalid_tier_raises(self):
        with pytest.raises(ValueError):
            ModelEntry(
                id="bad",
                api_name="bad",
                provider="google",
                tier="turbo",  # not a valid tier
                context_window=100_000,
                max_output_tokens=8_000,
                description="bad",
            )
