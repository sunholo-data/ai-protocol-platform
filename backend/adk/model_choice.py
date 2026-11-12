"""The model a person picked in the chat (composer + → Model).

Three levels (docs/design/v6.42.0/model-choice.md):

* ``auto`` — the default. Every skill runs on its own ``model``; nothing changes.
* ``fast`` / ``thorough`` — a tier from models.yaml ``choice``. Thorough also
  asks for ``ThinkDepth.HIGH``, which deepens a skill set to ``low``/``off``;
  on Gemini 3.x it measured the same as ``dynamic``, so where the smart and pro
  tiers are one model (eu-strict today) Thorough runs like Auto and the menu
  hides it (GET /api/models/choice gives the frontend what it needs to tell).
* ``model:<registry id>`` — a specific model from ``choice.pickable``. The UI
  only offers these to people who opted in on the Settings page; the backend
  accepts any id on the list the deployment allows, since the opt-in is a
  presentation preference, not a permission.

The choice is per chat. The stream route reads it from
``forwardedProps.model_choice``, the processor stamps it on the chat's row, and
binds it to ``ACTIVE_CHOICE`` while the agent tree is built. ``create_agent``
asks ``resolve_for(md)`` for every agent in the tree, delegates included, so
the specialist a front door hands to uses the choice too. A skill with
``modelChoice: false`` keeps its own model (a front door that routes, a
model-branded assistant, a skill whose A2UI output depends on one model).

Residency and fallbacks are untouched: the chosen ref goes through
``resolve_model_chain`` like any skill model, and ``available_models`` never
lists a model the residency policy or the mounted provider keys rule out.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from config.models import active_residency_policy, entry_for, load_models_config
from config.thinking import ThinkDepth

logger = logging.getLogger(__name__)

AUTO = "auto"
LEVELS = ("fast", "thorough")
MODEL_PREFIX = "model:"

# The validated choice for the turn being built. Part of the agent cache key.
ACTIVE_CHOICE: ContextVar[str] = ContextVar("model_choice", default=AUTO)

Offer = Literal["off", "tiers", "all"]

_PROVIDER_KEY_ENVS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


def offered() -> Offer:
    """What this deployment offers: ``MODEL_CHOICE`` = off | tiers | all.

    Default ``all`` (Auto, Fast, Thorough, and specific models for people who
    opt in). An unknown value falls back to ``tiers`` with a log line — the
    narrower menu, never a wider one.
    """
    raw = os.environ.get("MODEL_CHOICE", "").strip().lower()
    if raw in ("off", "tiers", "all"):
        return raw  # type: ignore[return-value]
    if raw:
        logger.error("MODEL_CHOICE=%r is not off|tiers|all — offering tiers only", raw)
        return "tiers"
    return "all"


def _allowed(model_id: str) -> bool:
    """A registry model this deployment may run: residency + provider key."""
    entry = entry_for(model_id)
    if entry is None:
        return False
    if active_residency_policy() == "eu-strict" and entry.residency != "eu":
        return False
    key = _PROVIDER_KEY_ENVS.get(entry.provider)
    return not key or bool(os.environ.get(key))


@dataclass(frozen=True)
class PickableView:
    id: str
    label: str
    note: str
    provider: str


def available_models() -> list[PickableView]:
    """The "Specific model" list for this deployment, in display order."""
    if offered() != "all":
        return []
    out: list[PickableView] = []
    seen: set[str] = set()
    for pick in load_models_config().choice.pickable:
        if pick.label in seen or not _allowed(pick.id):
            continue
        seen.add(pick.label)
        entry = entry_for(pick.id)
        out.append(PickableView(pick.id, pick.label, pick.note, entry.provider if entry else ""))
    return out


def level_model(level: str) -> str:
    """Registry id a level (``fast``/``thorough``) runs on under this policy."""
    ref = getattr(load_models_config().choice, level)
    entry = entry_for(ref)
    return entry.id if entry else ref


def normalize(raw: object) -> str:
    """A safe choice string from whatever the client sent. Unknown → Auto."""
    if not isinstance(raw, str):
        return AUTO
    value = raw.strip()
    if not value or value == AUTO:
        return AUTO
    allowed = offered()
    if allowed == "off":
        return AUTO
    if value in LEVELS:
        return value
    if value.startswith(MODEL_PREFIX) and allowed == "all":
        model_id = value[len(MODEL_PREFIX) :]
        if any(p.id == model_id for p in available_models()):
            return value
    logger.info("model choice %r is not offered here; using Auto", value)
    return AUTO


@dataclass(frozen=True)
class Resolved:
    model: str  # tier name or registry id, for resolve_model_chain
    depth: ThinkDepth | None  # None = keep the skill's own thinking depth


def resolve_for(skill_metadata) -> Resolved | None:
    """What the active choice means for one skill, or None to leave it alone."""
    choice = ACTIVE_CHOICE.get()
    if choice == AUTO or not getattr(skill_metadata, "model_choice", True):
        return None
    if choice == "fast":
        return Resolved(load_models_config().choice.fast, None)
    if choice == "thorough":
        return Resolved(load_models_config().choice.thorough, ThinkDepth.HIGH)
    if choice.startswith(MODEL_PREFIX):
        return Resolved(choice[len(MODEL_PREFIX) :], None)
    return None


__all__ = [
    "ACTIVE_CHOICE",
    "AUTO",
    "PickableView",
    "Resolved",
    "available_models",
    "level_model",
    "normalize",
    "offered",
    "resolve_for",
]
