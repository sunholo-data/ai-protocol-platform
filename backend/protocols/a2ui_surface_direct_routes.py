"""``POST /api/sessions/{id}/surface-action-direct`` — run a direct surface action.

Two-way workbench mode C (the two-way workbench design
§4.1). A sibling of ``surface-action`` (record only) and ``surface-action-run``
(record + model turn): this one records + runs a deterministic server handler
(:mod:`adk.a2ui_direct_actions`) and returns its outcome. No model call.

Gates — the surface-action stack (shared via ``_a2ui_surface_shared``) plus
two of its own:
  1. Authenticated caller.            2. Session exists (404).
  3. Caller can access the session.   4. Caller OWNS the session (403) — a
     viewer of a shared thread must not act on the owner's inbox.
  5. Skill has an ``a2ui`` tool_config and ``allow_surface_context_writes``.
  6. ``action.context`` ≤ 4 KB.
  7. The surface is in the session's stash (404) and a handler is registered
     for its artifact kind + the action name (404).
  8. The skill declares the handler's ``requires_tool`` (403) — a click never
     grants more than the skill already has.
Then the handler runs against the STASHED surface (never the client's copy).

Response (200, always JSON so the click shows something — NEVER SILENT):
``{status, message, messages}``; ``messages`` are A2UI v0.9 patches for the
surface, also appended to the stash so a reload shows them.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from google.adk.events import Event, EventActions

from adk.a2ui_direct_actions import DirectActionContext, lookup
from adk.agui import APP_NAME
from adk.callbacks import A2UI_SURFACE_STATE_PREFIX
from adk.session import get_session_service
from auth import User, get_current_user
from auth.permissions import can_use_tool
from protocols._a2ui_surface_shared import (
    _STATE_KEY_NAMESPACE,
    _enforce_size_cap,
    _enforce_skill_opt_in,
    _require_session,
)
from protocols.a2ui_surface_action_routes import SurfaceActionRequest
from skills import skill_config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["a2ui-surface-action"])

# Handlers' patches accumulate in the stash; keep it bounded.
_MAX_STASHED_MESSAGES = 400
# Direct-action outcomes kept for the AI per surface.
_RECENT_ACTIONS = 5


def _skill_tools(skill_id: str, user: User) -> set[str]:
    skill = skill_config.get_skill(skill_id) or skill_config.resolve_skill_ref(skill_id, getattr(user, "uid", None))
    tools = getattr(getattr(skill, "skill_metadata", None), "tools", None) or []
    return {str(t if isinstance(t, str) else getattr(t, "name", t)) for t in tools}


@router.post("/{session_id}/surface-action-direct")
async def post_surface_action_direct(
    session_id: str,
    body: SurfaceActionRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    idx = _require_session(session_id)
    if not request.state.access.can_access(idx) or idx.owner_uid != user.uid:
        log.info("surface_direct: access denied uid=%s session=%s", user.uid, session_id)
        raise HTTPException(status_code=403, detail="Access denied")
    _enforce_skill_opt_in(idx.skill_id, user)
    _enforce_size_cap(body.action.context)

    session_service = get_session_service()
    session = await session_service.get_session(app_name=APP_NAME, user_id=user.uid, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session backend not found")
    state = dict(session.state or {})
    stash_key = f"{A2UI_SURFACE_STATE_PREFIX}{body.surface_id}"
    raw = state.get(stash_key)
    try:
        stash = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        stash = None
    if not isinstance(stash, dict):
        raise HTTPException(status_code=404, detail="That surface isn't on this session's workbench any more")
    kind = str((stash.get("artifact") or {}).get("kind") or "")
    action = lookup(kind, body.action.name)
    if action is None:
        raise HTTPException(status_code=404, detail=f"No action {body.action.name!r} for this surface")
    if action.requires_tool and action.requires_tool not in _skill_tools(idx.skill_id, user):
        log.info(
            "surface_direct: skill lacks tool uid=%s skill=%s tool=%s", user.uid, idx.skill_id, action.requires_tool
        )
        raise HTTPException(status_code=403, detail="This skill can't do that")
    if action.requires_tool and not can_use_tool(user.email, user.domain, action.requires_tool):
        raise HTTPException(status_code=403, detail="This action's tool isn't enabled for your account")

    ctx = DirectActionContext(
        user=user,
        session_id=session_id,
        skill_id=idx.skill_id,
        surface_id=body.surface_id,
        stash=stash,
        action_context=body.action.context or {},
        state=state,
    )
    started = time.monotonic()
    try:
        result = await action.handler(ctx)
    except Exception as exc:  # a handler bug must still answer the click
        log.warning("surface_direct: handler %s/%s raised: %s", kind, action.name, exc)
        return {"status": "error", "message": "That didn't work — the action failed on the server.", "messages": []}

    delta: dict[str, Any] = {}
    if result.messages:
        stash["messages"] = [*(stash.get("messages") or []), *result.messages]
        if len(stash["messages"]) > _MAX_STASHED_MESSAGES:
            # Dropping the first messages discards the board's original items
            # and permissions scope (a bulk ack can produce 600 patches).
            # Compact to an equivalent snapshot, retaining all live components.
            creates = [m for m in stash["messages"] if "createSurface" in m]
            components = {}
            for message in stash["messages"]:
                for component in message.get("updateComponents", {}).get("components", []):
                    components[component["id"]] = component
            model = ctx.data_model()
            stash["messages"] = [
                *creates[-1:],
                {
                    "version": "v0.9",
                    "updateComponents": {"surfaceId": body.surface_id, "components": list(components.values())},
                },
                {"version": "v0.9", "updateDataModel": {"surfaceId": body.surface_id, "value": model}},
            ]
        delta[stash_key] = json.dumps(stash)
    if action.tell_ai:
        entry = {
            "name": body.action.name,
            "direct": True,
            "status": result.status,
            "result": result.record or result.message,
            "_pushedAt": time.time(),
        }
        ns = f"{_STATE_KEY_NAMESPACE}.{body.surface_id}"
        delta[f"{ns}.lastAction"] = entry
        # lastAction alone lets one refused click hide the successful one
        # before it; the AI reads the short history too.
        prior = state.get(f"{ns}.recentActions")
        delta[f"{ns}.recentActions"] = [*(prior if isinstance(prior, list) else []), entry][-_RECENT_ACTIONS:]
    if delta:
        await session_service.append_event(
            session,
            Event(
                invocation_id=f"surface_direct_{int(time.time() * 1000)}",
                author="user",
                actions=EventActions(state_delta=delta),
                timestamp=time.time(),
            ),
        )
    log.info(
        "surface_direct: uid=%s session=%s skill=%s surface=%s action=%s status=%s ms=%d",
        user.uid,
        session_id,
        idx.skill_id,
        body.surface_id,
        body.action.name,
        result.status,
        int((time.monotonic() - started) * 1000),
    )
    return {"status": result.status, "message": result.message, "messages": result.messages}


__all__ = ["router"]
