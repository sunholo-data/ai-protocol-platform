"""Host endpoint for A2UI ``A2uiClientAction`` pushes (sprint 2.10).

Sibling of ``iframe_context_routes.py``: same shape, different protocol
surface. The A2UI v0.9 spec defines a ``client_to_server`` action
message (``A2uiClientAction { name, surfaceId, sourceComponentId,
timestamp, context }``) emitted when the user clicks a Button, submits
a form, etc. The frontend's per-surface ``SurfaceModel.onAction``
forwards these; the chat UI's ``<A2UISurfaceMount>`` POSTs the
structured event here. We validate, gate, and write the action into
ADK session state under a namespaced key
``a2ui_surface_context.{surfaceId}.lastAction`` so the agent's NEXT
turn can read it.

Without this endpoint, user surface actions flow through
``A2UIRenderer.onAction`` → synthetic chat message, which loses the
structured context (the agent sees "User clicked submit" as a string
rather than a typed event). Half-loop until this lands.

Auth + access boundary (seven gates):
  1. Firebase JWT required (``get_current_user``).
  2. Session must exist (``_require_session`` → 404).
  3. Caller must be able to access the session (the existing 5-type
     access policy ``request.state.access.can_access``).
  4. Skill must exist (the session points at one).
  5. Skill must have an ``a2ui`` tool_config (else 403 — the skill
     hasn't opted into A2UI surface rendering at all).
  6. Skill must explicitly opt in via
     ``tool_configs.a2ui.allow_surface_context_writes: true``
     (default false). Mirrors the per-server opt-in gate
     ``tool_configs.mcp.allow_context_writes`` from the MCP Apps
     version. Distinct trust grants: "skill renders A2UI surfaces"
     doesn't automatically grant "surface actions write into agent
     context".
  7. The action ``context`` field must be a JSON object ≤ 4096 bytes
     serialized. Larger or wrong-typed: 413/400.

Threat model: actions are user-driven (only fire on a click/submit
the user actually performed). The frontend mounts the surface in a
trusted React context (not in an iframe sandbox), so origin checks
don't apply. But the action context dict could contain arbitrary
JSON the surface author chose to attach — could in theory be shaped
to prompt-inject the agent. Mitigations:
  * 4 KB cap on serialized context prevents flooding.
  * Action lands under a NAMESPACED key
    (``a2ui_surface_context.{surfaceId}.lastAction``); the agent
    prompt template references this namespace explicitly with
    framing prose so the model treats it as event-data, not
    instructions.
  * Per-skill opt-in keeps the new write surface closed by default
    for all skills.

Errors:
  * 400 — schema mismatch
  * 401 — missing/invalid Firebase JWT (handled upstream)
  * 403 — access / skill / opt-in gate failed
  * 404 — session_id not in the index
  * 413 — context serialized > 4 KB
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from google.adk.events import Event, EventActions
from pydantic import BaseModel, Field

from adk.agui import APP_NAME
from adk.session import get_session_service
from auth import User, get_current_user

# Gate helpers live in the shared module so the bundled write-and-run
# endpoint (ACTION-TRIGGER M1) honours the same access policy without
# importing this route module. Re-exported below so existing test
# patches against ``protocols.a2ui_surface_action_routes.<helper>``
# continue to work — pure refactor, zero behaviour change.
from protocols._a2ui_surface_shared import (
    _STATE_KEY_NAMESPACE,
    _enforce_size_cap,
    _enforce_skill_opt_in,
    _require_session,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["a2ui-surface-action"])


class SurfaceActionPayload(BaseModel):
    """One A2uiClientAction event."""

    name: str = Field(min_length=1, max_length=128)
    source_component_id: str | None = Field(default=None, alias="sourceComponentId", max_length=256)
    timestamp: str | None = Field(default=None, max_length=64)
    # Free-form structured context the surface author attached. Capped
    # at serialization time, validated by Pydantic for type only.
    context: dict[str, Any] | None = Field(default=None)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class SurfaceActionRequest(BaseModel):
    """The body of ``POST /api/sessions/{id}/surface-action``."""

    surface_id: str = Field(alias="surfaceId", min_length=1, max_length=128)
    action: SurfaceActionPayload

    model_config = {"populate_by_name": True, "extra": "forbid"}


@router.post("/{session_id}/surface-action", status_code=204)
async def post_surface_action(
    session_id: str,
    body: SurfaceActionRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Receive an A2UI surface action and write it into the session's
    ADK state under ``a2ui_surface_context.{surfaceId}.lastAction``.

    See module docstring for the auth + opt-in + size contract.
    """
    # Gate 2: session exists
    idx = _require_session(session_id)

    # Gate 3: caller can access the session
    ctx = request.state.access
    if not ctx.can_access(idx):
        log.info(
            "surface_action: access denied uid=%s session_id=%s skill_id=%s",
            user.uid,
            session_id,
            idx.skill_id,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    # Gates 4 + 5 + 6: skill exists + has a2ui config + opted in
    _enforce_skill_opt_in(idx.skill_id, user)

    # Gate 7: size cap
    size_bytes = _enforce_size_cap(body.action.context)

    # Build the namespaced state key. Two levels:
    # `a2ui_surface_context.{surfaceId}.lastAction`. Storing under
    # `lastAction` (singular) means each new click overwrites the
    # previous one — the agent reads the MOST RECENT action only.
    # Forks needing action history can extend with `actions: [...]`
    # later without breaking the read side (InstructionProvider walks
    # whatever fields are under the surface).
    state_key = f"{_STATE_KEY_NAMESPACE}.{body.surface_id}.lastAction"
    state_value: dict[str, Any] = {
        "name": body.action.name,
        "_pushedAt": time.time(),
    }
    if body.action.source_component_id:
        state_value["sourceComponentId"] = body.action.source_component_id
    if body.action.context is not None:
        state_value["context"] = body.action.context
    if body.action.timestamp:
        state_value["timestamp"] = body.action.timestamp

    # Write via ADK's append_event(state_delta) pattern. Author "user"
    # so ADK doesn't warn about "system" author events (same as
    # iframe-context).
    # ADK sessions are keyed by ("aitana_platform", user_id, session_id) —
    # build_agui_adk_agent passes the canonical APP_NAME, NOT the skill_id,
    # so we must look up under the same key. The 7-gate session-index check
    # above already proves this caller can access this session under this
    # skill; the app_name here is purely the ADK storage key.
    session_service = get_session_service()
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user.uid,
        session_id=session_id,
    )
    if session is None:
        log.info(
            "surface_action: ADK session not found uid=%s session_id=%s "
            "skill_id=%s (index exists, ADK session missing)",
            user.uid,
            session_id,
            idx.skill_id,
        )
        raise HTTPException(status_code=404, detail="Session backend not found")

    actions = EventActions(state_delta={state_key: state_value})
    event = Event(
        invocation_id=f"surface_action_{int(time.time() * 1000)}",
        author="user",
        actions=actions,
        timestamp=time.time(),
    )
    await session_service.append_event(session, event)

    log.info(
        "surface_action: write uid=%s session=%s skill=%s surface=%s action=%s bytes=%s",
        user.uid,
        session_id,
        idx.skill_id,
        body.surface_id,
        body.action.name,
        size_bytes,
    )
    return None


__all__ = ["router"]
