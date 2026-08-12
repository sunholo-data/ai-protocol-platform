"""GET /api/models — unauthenticated model list for the skill-settings UI.

Returns the structured model registry from backend/config/models.yaml.
No auth required: the model list is not sensitive.
Compaction config is internal and not included in the response.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from config.models import ModelEntry, load_models_config

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
