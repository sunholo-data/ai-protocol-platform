"""Fixed, authenticated no-model workbench launchers, including before first chat."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.adk.events import Event, EventActions
from pydantic import BaseModel, Field

from adk.a2ui_direct_launchers import LauncherContext, lookup_launcher
from adk.agui import APP_NAME
from adk.callbacks import A2UI_SURFACE_STATE_PREFIX
from adk.session import get_session_service
from auth import User, get_current_user
from auth.permissions import can_use_tool
from db.chat_sessions import get_session_index
from protocols.a2ui_surface_direct_routes import _validate_targets
from protocols.session_bootstrap_routes import BootstrapRequest, bootstrap_session
from skills import skill_config

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills", tags=["a2ui-direct-launch"])


class LaunchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    model_config = {"extra": "forbid"}


@router.post("/{skill_id}/sessions/{session_id}/surface-launch-direct")
async def launch_direct(
    skill_id: str,
    session_id: str,
    body: LaunchRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    skill = skill_config.get_skill(skill_id) or skill_config.resolve_skill_ref(skill_id, user.uid)
    if skill is None or not request.state.access.can_access_skill(skill):
        raise HTTPException(404, "Skill not found")
    canonical_id = skill.skill_id
    metadata = skill.skill_metadata
    config = (metadata.tool_configs or {}).get("a2ui") or {}
    launchers = config.get("direct_launchers") or []
    declaration = next(
        (entry for entry in launchers if isinstance(entry, dict) and entry.get("name") == body.name), None
    )
    registered = lookup_launcher(body.name)
    if not declaration or not registered:
        raise HTTPException(404, "That launcher is not available")
    if not config.get("allow_surface_context_writes"):
        raise HTTPException(403, "This skill has not enabled workbench actions")
    tool = registered.requires_tool
    tools = {str(t if isinstance(t, str) else getattr(t, "name", t)) for t in metadata.tools or []}
    if declaration.get("tool") != tool or tool not in tools or not can_use_tool(user.email, user.domain, tool):
        raise HTTPException(403, "This launcher's tool is not enabled")

    # Check an existing session BEFORE calling the bootstrap routine, which
    # intentionally allows collaborators for other purposes. This path is owner-only.
    index = get_session_index(session_id)
    if index is None:
        await bootstrap_session(
            session_id=session_id,
            body=BootstrapRequest(skill_id=canonical_id),
            request=request,
            user=user,
        )
        index = get_session_index(session_id)
    if index is None or index.owner_uid != user.uid or not request.state.access.can_access(index):
        raise HTTPException(403, "This launcher requires a session you own")
    if index.skill_id != canonical_id:
        raise HTTPException(403, "This session belongs to another skill")
    service = get_session_service()
    session = await service.get_session(app_name=APP_NAME, user_id=user.uid, session_id=session_id)
    if session is None:
        raise HTTPException(503, "The session is not ready. Please try again.")
    try:
        result = await registered.handler(LauncherContext(user, session_id, canonical_id))
        if not result.surface_id or result.surface_id.startswith("private:") or not result.messages:
            raise ValueError("A launcher must return a safe control surface")
        _validate_targets(result.surface_id, result.messages)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("a2ui direct launcher failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Could not open that board. Please try again.") from None
    stash = {
        "surfaceId": result.surface_id,
        "messages": result.messages,
        "artifact": result.artifact,
        "sourceId": f"direct-launch:{body.name}:{time.time_ns()}",
        "toolName": tool,
        "createdAt": time.time(),
    }
    await service.append_event(
        session,
        Event(
            invocation_id=stash["sourceId"],
            author="user",
            timestamp=time.time(),
            actions=EventActions(state_delta={f"{A2UI_SURFACE_STATE_PREFIX}{result.surface_id}": json.dumps(stash)}),
        ),
    )
    return {
        "status": "ok",
        "message": result.message,
        "surface_id": result.surface_id,
        "messages": result.messages,
        "artifact": result.artifact,
    }
