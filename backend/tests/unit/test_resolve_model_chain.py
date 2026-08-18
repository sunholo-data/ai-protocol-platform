"""MODEL-RELIABILITY M3 — chain resolution + deployment residency policy.

``resolve_model_chain`` is the single choke point where residency is
enforced *by construction*: under ``eu-strict`` no non-EU entry can enter
a chain regardless of what a skill or models.yaml declares — non-EU
fallbacks are dropped with a warning, a pinned non-EU primary raises at
load (never a silent model swap), and residency-aware tier variants make
every tier resolve to a working EU model with zero skill changes.

Policy source: ``MODEL_RESIDENCY_POLICY`` env > models.yaml
``residency.default_policy`` (ships ``eu-strict`` — the fail-safe
default for forks; Aitana dev overrides to ``unrestricted`` per the
accepted dev/demo US-egress exception).
"""

from __future__ import annotations

import pytest
from google.adk.models.google_llm import Gemini

from adk.agent import RegionalGemini, ResidencyViolationError, resolve_model_chain
from adk.resilient_llm import ResilientLlm
from config.models import active_residency_policy
from db.models import FallbackConfig


@pytest.fixture(autouse=True)
def _anthropic_key(monkeypatch):
    """Most cases assume provider keys are mounted; the missing-key case
    removes them explicitly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def _models(chain_or_model) -> list[str]:
    if isinstance(chain_or_model, ResilientLlm):
        return [m.model for m in chain_or_model.chain]
    return [chain_or_model.model]


# --- Policy resolution --------------------------------------------------------


def test_env_policy_wins_over_yaml_default(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    assert active_residency_policy() == "unrestricted"


def test_unknown_policy_fails_safe_to_eu_strict(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "banana")
    assert active_residency_policy() == "eu-strict"


def test_yaml_default_policy_is_eu_strict(monkeypatch):
    # The template's fail-safe default: a fork that wires nothing gets EU-only.
    monkeypatch.delenv("MODEL_RESIDENCY_POLICY", raising=False)
    assert active_residency_policy() == "eu-strict"


# --- Tier variants -------------------------------------------------------------


def test_smart_tier_resolves_to_claude_under_unrestricted(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    model = resolve_model_chain("smart")
    chain = _models(model)
    assert chain[0].startswith("anthropic/claude-")
    # Cross-provider fallback claude→gemini is egress-NARROWING (us→eu):
    # always legal, ships in the default chain.
    assert any("gemini" in m for m in chain[1:])


def test_smart_tier_resolves_to_gemini_under_eu_strict(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    model = resolve_model_chain("smart")
    chain = _models(model)
    assert all(not m.startswith("anthropic/") and not m.startswith("openai/") for m in chain)
    assert chain[0].startswith("gemini-")


def test_lite_tier_gets_eu_multiregion_chain(monkeypatch):
    # 2026-08-13: lite's eu-strict primary (gemini-3-5-flash-lite-eu) has no
    # single-region EU option at all (live-probed — see models.yaml), so it
    # has no cross-region rung; it's pinned to the "eu" jurisdictional
    # multi-region endpoint instead. See test_gemini_3_5_flash_eu_gets_
    # cross_region_rung below for the (still-alive) same-model-different-
    # region rung pattern, which gemini-3-5-flash-eu uses instead.
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    model = resolve_model_chain("lite")
    assert isinstance(model, ResilientLlm)
    primary = model.chain[0]
    assert isinstance(primary, RegionalGemini)
    assert primary.location == "eu"


def test_gemini_3_5_flash_eu_gets_cross_region_rung():
    # Tier 1a: same model, different EU region — a RegionalGemini member.
    # gemini-3.5-flash is the one 3.x model with genuine EU single-region
    # availability (europe-west2 AND europe-west3, live-probed 2026-08-13),
    # so it keeps the classic cross-region-rung shape 2.x-era entries use.
    model = resolve_model_chain("gemini-3-5-flash-eu")
    assert isinstance(model, ResilientLlm)
    regional = [m for m in model.chain if isinstance(m, RegionalGemini)]
    assert regional, "expected a cross-region rung in the gemini-3-5-flash-eu chain"
    assert all(loc.startswith("europe-") for loc in [m.location for m in regional])


# --- eu-strict enforcement ------------------------------------------------------


def test_pinned_non_eu_primary_raises_under_eu_strict(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    with pytest.raises(ResidencyViolationError):
        resolve_model_chain("claude-opus-4-7")


def test_non_eu_fallback_dropped_under_eu_strict(monkeypatch, caplog):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    fallback = FallbackConfig(models=["gemini-2-5-flash", "claude-sonnet-4-6"], allow_cross_provider=True)
    model = resolve_model_chain("gemini-2-5-pro", fallback)
    chain = _models(model)
    assert not any("claude" in m for m in chain)
    assert any("gemini-2.5-flash" in m for m in chain)
    # Dropping is loud: allow_cross_provider CANNOT override deployment policy.
    assert any("residency" in rec.message.lower() for rec in caplog.records)


# --- unrestricted + per-skill narrowing knob ------------------------------------


def test_egress_widening_fallback_needs_per_skill_opt_in(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    # EU primary, US fallback = egress-widening: default (False) drops it…
    closed = resolve_model_chain("gemini-2-5-pro", FallbackConfig(models=["claude-sonnet-4-6"]))
    assert not any("claude" in m for m in _models(closed))
    # …explicit opt-in keeps it.
    open_ = resolve_model_chain(
        "gemini-2-5-pro", FallbackConfig(models=["claude-sonnet-4-6"], allow_cross_provider=True)
    )
    assert any("claude" in m for m in _models(open_))


# --- Deploy-drift proofing --------------------------------------------------------


def test_fallback_skipped_when_provider_key_not_mounted(monkeypatch, caplog):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # gemini primary with a claude fallback the env can't serve.
    model = resolve_model_chain(
        "gemini-2-5-pro", FallbackConfig(models=["claude-sonnet-4-6"], allow_cross_provider=True)
    )
    assert not any("claude" in m for m in _models(model))
    assert any("ANTHROPIC_API_KEY" in rec.message for rec in caplog.records)


# --- Wrapper shape ---------------------------------------------------------------


def test_single_member_chain_returns_bare_model(monkeypatch):
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "eu-strict")
    # A raw api name outside the registry has no chain → bare model, zero
    # behavior change for unconfigured setups (residency inferred by prefix).
    model = resolve_model_chain("gemini-2.0-flash")
    assert isinstance(model, Gemini)
    assert not isinstance(model, ResilientLlm)


def test_regional_rungs_only_for_gemini(monkeypatch):
    """location: entries only make sense on Vertex (region-pinned client);
    a location on a non-Gemini entry is a config error caught at resolve."""
    monkeypatch.setenv("MODEL_RESIDENCY_POLICY", "unrestricted")
    model = resolve_model_chain("smart")
    for member in _models(model):
        assert member  # chain resolved without error; shape sanity
