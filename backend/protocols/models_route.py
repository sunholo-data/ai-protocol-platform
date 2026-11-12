"""GET /api/models — unauthenticated model list for the skill-settings UI.

Returns the structured model registry from backend/config/models.yaml.
No auth required: the model list is not sensitive.
Compaction config is internal and not included in the response.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth import User, get_current_user
from config.models import ModelEntry, entry_for, load_models_config

router = APIRouter(prefix="/api", tags=["models"])


class ModelsResponse(BaseModel):
    models: list[ModelEntry]
    defaults: dict[str, str]
    platform_default: str
    # v6.6.0: logical tier name -> registry id (e.g. {"lite": "gemini-flash-lite"}).
    tier_defaults: dict[str, str] = {}


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """Return all supported models grouped by provider."""
    cfg = load_models_config()
    return ModelsResponse(
        models=cfg.models,
        defaults=cfg.defaults,
        platform_default=cfg.platform_default,
        tier_defaults=cfg.tier_defaults,
    )


# --- The composer's Model menu (adk.model_choice) ------------------------------


class ChoiceLevel(BaseModel):
    model: str
    label: str


class ChoiceModel(BaseModel):
    id: str
    label: str
    note: str = ""
    provider: str = ""


class ModelChoiceResponse(BaseModel):
    # off: no Model row; tiers: Auto/Fast/Thorough; all: also specific models
    # for people who opted in on the Settings page.
    offered: str
    levels: dict[str, ChoiceLevel]
    models: list[ChoiceModel]
    # Friendly name for any tier or registry id, e.g. a skill's own `model`.
    labels: dict[str, str]
    # The registry id each tier or id runs as under this deployment's policy,
    # so the menu can hide a level that would run exactly like Auto.
    resolved: dict[str, str]


def label_for(ref: str) -> str:
    """A person-facing model name: the pickable label, else the registry
    description's name part ("Gemini 3.8 Flash — …" → "Gemini 3.8 Flash")."""
    cfg = load_models_config()
    entry = entry_for(ref)
    model_id = entry.id if entry else ref
    for pick in cfg.choice.pickable:
        if pick.id == model_id:
            return pick.label
    if entry is None:
        return ref
    return entry.description.split(" — ")[0].split(",")[0].strip() or entry.id


@router.get("/models/choice", response_model=ModelChoiceResponse)
async def model_choice_menu(user: User = Depends(get_current_user)) -> ModelChoiceResponse:  # noqa: B008
    """What this deployment's Model menu offers, resolved for its residency
    policy and mounted provider keys. Signed-in only: which providers are
    mounted is deployment detail."""
    from adk import model_choice

    cfg = load_models_config()
    offered = model_choice.offered()
    levels = {}
    if offered != "off":
        for level in model_choice.LEVELS:
            model_id = model_choice.level_model(level)
            levels[level] = ChoiceLevel(model=model_id, label=label_for(model_id))
    refs = [m.id for m in cfg.models] + list(cfg.tier_variants)
    return ModelChoiceResponse(
        offered=offered,
        levels=levels,
        models=[ChoiceModel(**vars(m)) for m in model_choice.available_models()],
        labels={ref: label_for(ref) for ref in refs},
        resolved={ref: (entry_for(ref).id if entry_for(ref) else ref) for ref in refs},
    )
