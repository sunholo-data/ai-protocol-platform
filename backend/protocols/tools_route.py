"""GET /api/tools — unauthenticated tool catalog for the Skill Studio picker.

Returns the user-selectable tools from backend/adk/tools.py TOOL_CATALOG so the
Studio form can render a checklist (label + one-line description) instead of a
free-text, comma-separated list of internal tool names.

No auth required: the catalog is not sensitive (it's the same list a skill
author would otherwise type by hand).

When the caller IS authenticated, the catalog is filtered by that caller's
tool_permissions (``auth.permissions.can_use_tool``) — the same rule enforced
on every tool call. Otherwise an agent reading this list (a machine principal
authoring skills) is told it has tools that would be refused at call time.
Anonymous callers keep the full catalog.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
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
async def list_tools(request: Request) -> ToolsResponse:
    """Return the catalog of user-selectable skill tools — the caller's, when known."""
    from auth import get_current_user
    from auth.permissions import can_use_tool

    has_credentials = any(request.headers.get(h) for h in ("Authorization", "X-Proxy-Secret", "Tailscale-User-Login"))
    user = None
    if has_credentials:
        try:
            user = await get_current_user(request)
        except HTTPException:
            user = None  # bad/foreign credentials: fall back to the public catalog
    entries = TOOL_CATALOG
    if user is not None:
        entries = [e for e in TOOL_CATALOG if can_use_tool(user.email, user.domain, e["name"])]
    return ToolsResponse(tools=[ToolInfo(**entry) for entry in entries])
