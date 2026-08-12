"""GET /api/tools — unauthenticated tool catalog for the Skill Studio picker.

Returns the user-selectable tools from backend/adk/tools.py TOOL_CATALOG so the
Studio form can render a checklist (label + one-line description) instead of a
free-text, comma-separated list of internal tool names.

No auth required: the catalog is not sensitive (it's the same list a skill
author would otherwise type by hand).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from adk.tools import TOOL_CATALOG

router = APIRouter(prefix="/api", tags=["tools"])


class ToolInfo(BaseModel):
    name: str
    label: str
    description: str
    category: str


class ToolsResponse(BaseModel):
    tools: list[ToolInfo]


@router.get("/tools", response_model=ToolsResponse)
async def list_tools() -> ToolsResponse:
    """Return the catalog of user-selectable skill tools."""
    return ToolsResponse(tools=[ToolInfo(**entry) for entry in TOOL_CATALOG])
