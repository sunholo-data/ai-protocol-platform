"""Pydantic models and YAML loader for the v6 model registry.

Source of truth: backend/config/models.yaml
Updated in sync with ~/.ailang/models.yml when new models release.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

_YAML_PATH = Path(__file__).parent / "models.yaml"


class ChainLink(BaseModel):
    """One fallback rung: a registry id plus an optional Vertex region
    override (tier 1a cross-region entries — Gemini only)."""

    id: str
    location: str | None = None


class ModelEntry(BaseModel):
    id: str
    api_name: str
    provider: Literal["google", "anthropic", "openai"]
    tier: Literal["default", "smart", "fast"]
    context_window: int
    max_output_tokens: int
    description: str
    # MODEL-RELIABILITY M3: where this model's inference egresses. Default
    # "us" is deliberate fail-safe — an untagged entry never passes eu-strict.
    residency: Literal["eu", "us", "global"] = "us"
    # Optional Vertex location override for the PRIMARY (fallback rungs use
    # ChainLink.location instead). Unset -> bare Gemini(), which relies on
    # GOOGLE_CLOUD_LOCATION (europe-west1) for `residency: eu` entries, or the
    # "global" special-case in adk.agent.resolve_model for `residency: global`.
    # Set this when an entry needs a DIFFERENT pin — e.g. a Gemini 3.x model
    # whose EU availability is a jurisdictional multi-region endpoint
    # (location="eu", not a specific europe-west* region) rather than the
    # classic single-region pin 2.x-era EU entries use. Verified per-model via
    # a live probe (see individual entry comments) — Vertex region/endpoint
    # availability is NOT uniform across the Gemini generations.
    location: str | None = None
    fallbacks: list[ChainLink] = []


class ModelsConfig(BaseModel):
    models: list[ModelEntry]
    defaults: dict[str, str]
    platform_default: str
    # Logical tier name -> registry id, resolved for the DEFAULT policy
    # (backward-compatible view — models_route and older callers read this).
    tier_defaults: dict[str, str] = {}
    # Full per-policy variants: {tier: {"default": id, "eu-strict": id, ...}}.
    tier_variants: dict[str, dict[str, str]] = {}
    # Fail-safe deployment default; MODEL_RESIDENCY_POLICY env overrides.
    residency_default_policy: str = "eu-strict"
    fallback_policy: dict[str, float] = {}

    @model_validator(mode="after")
    def validate_references(self) -> ModelsConfig:
        model_ids = {m.id for m in self.models}
        if self.platform_default not in model_ids:
            raise ValueError(f"platform_default {self.platform_default!r} not found in models list")
        for provider, model_id in self.defaults.items():
            if model_id not in model_ids:
                raise ValueError(f"defaults[{provider!r}] = {model_id!r} not found in models list")
        for tier, variants in self.tier_variants.items():
            for policy, model_id in variants.items():
                if model_id not in model_ids:
                    raise ValueError(f"tier_defaults[{tier!r}][{policy!r}] = {model_id!r} not found in models list")
        for entry in self.models:
            for link in entry.fallbacks:
                if link.id not in model_ids:
                    raise ValueError(f"models[{entry.id!r}].fallbacks references unknown id {link.id!r}")
        return self


@lru_cache(maxsize=1)
def load_models_config() -> ModelsConfig:
    """Load and validate models.yaml. Cached after first call.

    Raises RuntimeError with a clear message if the file is missing or malformed,
    so startup failures are diagnosable rather than crashing with a raw exception.
    """
    try:
        raw = yaml.safe_load(_YAML_PATH.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(f"models.yaml not found at {_YAML_PATH}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"models.yaml is malformed: {exc}") from exc

    models = []
    for key, entry in raw["models"].items():
        fields = {k: v for k, v in entry.items() if k != "id"}
        fields["fallbacks"] = [
            ChainLink(id=link) if isinstance(link, str) else ChainLink(**link) for link in fields.get("fallbacks", [])
        ]
        models.append(ModelEntry(id=key, **fields))

    # tier_defaults values are either a bare id (all policies) or a
    # {policy: id} mapping with a required "default" key (M3 variants).
    raw_tiers: dict = raw.get("tier_defaults", {})
    tier_variants: dict[str, dict[str, str]] = {}
    for tier, value in raw_tiers.items():
        if isinstance(value, str):
            tier_variants[tier] = {"default": value}
        else:
            if "default" not in value:
                raise RuntimeError(f"models.yaml tier_defaults[{tier!r}] variants need a 'default' key")
            tier_variants[tier] = dict(value)

    return ModelsConfig(
        models=models,
        defaults=raw["defaults"],
        platform_default=raw["platform_default"],
        tier_defaults={tier: variants["default"] for tier, variants in tier_variants.items()},
        tier_variants=tier_variants,
        residency_default_policy=(raw.get("residency") or {}).get("default_policy", "eu-strict"),
        fallback_policy=raw.get("fallback_policy", {}),
    )


# --- Tier + id resolution helpers ---------------------------------------------
# A skill's `model` field (SkillMetadata.model) may be a logical tier name
# (`lite`, `smart`), a registry id (`gemini-2-5-flash`), or a raw provider api
# name (`gemini-2.5-flash`). These helpers collapse all three to the api name
# the ADK model wrappers expect. See docs/design/v6.6.0/one-app-fork-convergence.md.


def _entry_by_id(cfg: ModelsConfig, model_id: str) -> ModelEntry | None:
    return next((m for m in cfg.models if m.id == model_id), None)


RESIDENCY_POLICIES = ("eu-strict", "unrestricted")


def active_residency_policy() -> str:
    """The deployment's residency policy (MODEL-RELIABILITY M3).

    ``MODEL_RESIDENCY_POLICY`` env > models.yaml ``residency.default_policy``.
    Read per call (not cached) so per-request/test overrides work. An
    unrecognized value fails SAFE to ``eu-strict`` with a loud log — a typo
    in a deploy env must never silently widen egress.
    """
    import logging
    import os

    value = os.environ.get("MODEL_RESIDENCY_POLICY", "").strip().lower()
    if value in RESIDENCY_POLICIES:
        return value
    if value:
        logging.getLogger(__name__).error(
            "MODEL_RESIDENCY_POLICY=%r is not one of %s — failing safe to 'eu-strict'", value, RESIDENCY_POLICIES
        )
        return "eu-strict"
    default = load_models_config().residency_default_policy
    return default if default in RESIDENCY_POLICIES else "eu-strict"


def _tier_target(cfg: ModelsConfig, name: str) -> str:
    """Registry id a tier resolves to under the ACTIVE residency policy."""
    variants = cfg.tier_variants[name]
    return variants.get(active_residency_policy(), variants["default"])


def resolve_tier(name: str) -> ModelEntry:
    """Return the ModelEntry a logical tier resolves to under the active
    residency policy (an ``eu-strict`` deployment resolves ``smart`` to its
    EU variant automatically — zero skill changes).

    Raises:
        ValueError: If the tier is not declared in models.yaml `tier_defaults`.
    """
    cfg = load_models_config()
    if name not in cfg.tier_variants:
        raise ValueError(f"Unknown model tier {name!r}; known tiers: {sorted(cfg.tier_variants)}")
    model_id = _tier_target(cfg, name)
    entry = _entry_by_id(cfg, model_id)
    if entry is None:  # pragma: no cover — guarded by ModelsConfig validator
        raise ValueError(f"tier {name!r} points at unknown model id {model_id!r}")
    return entry


def entry_for(ref: str) -> ModelEntry | None:
    """ModelEntry for a tier name or registry id (None for raw api names),
    tier resolution respecting the active residency policy."""
    cfg = load_models_config()
    if ref in cfg.tier_variants:
        ref = _tier_target(cfg, ref)
    return _entry_by_id(cfg, ref)


def api_name_for(ref: str) -> str:
    """Collapse a tier name / registry id / raw api name to a provider api name.

    - Logical tier (`lite`, `smart`) -> the tier's registry entry api name
      (variant chosen by the active residency policy).
    - Registry id (`gemini-2-5-flash`) -> that entry's api name.
    - Anything else (a raw `gemini-2.5-flash` / `claude-*` / `gpt-*`) -> unchanged.
    """
    entry = entry_for(ref)
    if entry is not None:
        return entry.api_name
    return ref


def gemini_api_name_for(ref: str) -> str:
    """`api_name_for()` that asserts the result is a Gemini model.

    For callers that use Vertex-only features — structured output via
    `response_schema` / `response_mime_type` is Gemini-only, so a tier that
    resolves to Claude/OpenAI (e.g. `smart` -> claude-opus) cannot serve them.
    Fail loudly rather than emit a broken Vertex request.

    Raises:
        ValueError: if `ref` resolves to a non-Gemini api name.
    """
    api = api_name_for(ref)
    if not api.startswith("gemini-"):
        raise ValueError(
            f"Expected a Gemini tier here — this call uses Vertex structured "
            f"output (response_schema), which only Gemini serves. {ref!r} "
            f"resolved to {api!r}. Use a Gemini tier such as 'lite' or 'pro'."
        )
    return api


def default_model() -> str:
    """Return the api name of the platform default model."""
    return resolve_tier_or_default(load_models_config().platform_default)


def resolve_tier_or_default(ref: str) -> str:
    """api_name_for() but resolves a registry id even when not a tier. Used by
    default_model(); kept separate so callers can read intent."""
    return api_name_for(ref)


def _tier_names() -> set[str]:
    return set(load_models_config().tier_defaults.keys())


# Snapshot of declared tier names for cheap membership checks / test assertions.
TIER_NAMES: set[str] = _tier_names()
