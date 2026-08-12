"""Stash-update endpoint for client-side A2UI surface data-model edits (7.6 follow-up).

Sibling of ``a2ui_surface_action_routes`` — same gate stack (reused via
``_a2ui_surface_shared``), different write target. The 7.5 workbench-artifacts
model rehydrates the workbench from a session-scoped stash
(``a2ui_surface:{surfaceId}``) written by the result emitter at tool time. That
makes CLIENT-side edits — e.g. what-if scenario changes inside the
ppa-obligation-analysis artefact — survive a tab re-mount (module store) but
NOT a hard refresh: nothing wrote them back to the stash. Both the 7.5 and 7.6
sprints deferred that hook; this endpoint closes it.

``POST /api/sessions/{session_id}/surface-data`` accepts a full surface
data-model root value and merges it into the existing stash entry as a
``clientDataModel`` block. The session-history GET
(``/api/sessions/{id}/messages``) materialises that block as one extra
``updateDataModel`` message appended AFTER the canonical tool-emitted messages,
so the ordinary replay path (``RehydrateSurfaces`` → ``appendMessages``)
restores the edited state with zero frontend replay changes. Design choices:

  * **Canonical messages stay untouched.** The client edit lives in its own
    stash field; repeated writes replace it (no unbounded message growth), and
    a ``dataModel: null`` write clears it ("reset to extracted").
  * **The client cannot CREATE stash entries** — only update ones the backend
    emitter stashed (404 otherwise). Which surfaces exist, their component
    trees, and their artifact metadata remain server-authored.
  * **Self-heal on re-emit** — a new tool run overwrites the whole stash entry
    (``_stash_surface_for_resume``), dropping the client edit in favour of the
    fresh canonical render. Same staleness contract as the 7.5 stash itself.

Auth + access boundary (six gates — the surface-action stack minus the
action-context cap, plus owner-only and a data-model cap):
  1. Firebase JWT required (``get_current_user``).
  2. Session must exist in the index (404).
  3. Caller must be able to access the session (403).
  4. Caller must be the session OWNER (403). Stricter than the read-side
     ``can_access``: a shared (public/group) session is readable by others,
     but letting any viewer rewrite the owner's rehydrated artifact state is
     a write grant nobody asked for. Relax deliberately if collaborative
     what-if editing ever becomes a feature.
  5. Skill exists + has an ``a2ui`` tool_config + opted in via
     ``tool_configs.a2ui.allow_surface_context_writes: true`` (shared gate —
     the same trust grant: "client surface interactions may write into this
     skill's session state").
  6. The data model must serialize to ≤ 256 KiB (413). Far above any
     scenario edit, far below the Firestore/state-blob danger zone; the
     client sends the full root value (payload + scenario) because A2UI
     ``updateDataModel`` replaces the root.

Errors: 400 schema/serialization, 401 unauthenticated, 403 access/owner/opt-in,
404 session or stash entry missing, 413 data model too large.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from google.adk.events import Event, EventActions
from pydantic import BaseModel, Field

from adk.agui import APP_NAME
from adk.callbacks import A2UI_SURFACE_STATE_PREFIX
from adk.session import get_session_service
from auth import User, get_current_user
from protocols._a2ui_surface_shared import _enforce_skill_opt_in, _require_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["a2ui-surface-data"])

# Cap on the serialized data-model root value. Deliberately much larger than
# the 4 KB action-context cap: the client must send the FULL root value
# (canonical payload + scenario) because A2UI `updateDataModel` replaces the
# root — an obligation wire payload alone runs tens of KB.
_MAX_DATA_MODEL_BYTES = 262_144


class SurfaceDataRequest(BaseModel):
    """Body of ``POST /api/sessions/{session_id}/surface-data``.

    ``dataModel`` is the surface's new data-model ROOT value (what
    ``SurfaceModel.dataModel.get('/')`` should return after rehydration), or
    ``null`` to clear a previously persisted client edit so the canonical
    tool-emitted state rehydrates again.
    """

    surface_id: str = Field(alias="surfaceId", min_length=1, max_length=128)
    data_model: dict[str, Any] | None = Field(default=None, alias="dataModel")

    model_config = {"populate_by_name": True, "extra": "forbid"}


def _enforce_data_model_size(data_model: dict[str, Any] | None) -> str:
    """Gate 6: serialized size cap. Returns the byte count for logging."""
    if data_model is None:
        return "0"
    try:
        serialized = json.dumps(data_model, default=str)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"dataModel is not JSON-serializable: {exc}",
        ) from exc
    size = len(serialized.encode("utf-8"))
    if size > _MAX_DATA_MODEL_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"dataModel is {size} bytes; max is {_MAX_DATA_MODEL_BYTES}",
        )
    return str(size)


@router.post("/{session_id}/surface-data", status_code=204)
async def post_surface_data(
    session_id: str,
    body: SurfaceDataRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> None:
    """Persist a client-edited surface data model into the 7.5 rehydration
    stash so the session-history GET replays the edited state after a hard
    refresh. See module docstring for the gate matrix and merge semantics.
    """
    # Gate 2: session exists
    idx = _require_session(session_id)

    # Gate 3: caller can access the session
    ctx = request.state.access
    if not ctx.can_access(idx):
        log.info(
            "surface_data: access denied uid=%s session_id=%s skill_id=%s",
            user.uid,
            session_id,
            idx.skill_id,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    # Gate 4: owner-only — viewers of a shared session must not rewrite the
    # owner's rehydrated artifact state.
    if idx.owner_uid != user.uid:
        log.info(
            "surface_data: non-owner write refused uid=%s owner=%s session_id=%s",
            user.uid,
            idx.owner_uid,
            session_id,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    # Gate 5: skill exists + has a2ui config + opted into surface writes
    _enforce_skill_opt_in(idx.skill_id, user)

    # Gate 6: data-model size cap
    size_bytes = _enforce_data_model_size(body.data_model)

    # ADK sessions are keyed by (APP_NAME, user_id, session_id); the owner-only
    # gate above makes user.uid the owner's uid — the same key the emitter
    # stashed under and the messages GET reads from.
    session_service = get_session_service()
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user.uid,
        session_id=session_id,
    )
    if session is None:
        log.info(
            "surface_data: ADK session not found uid=%s session_id=%s skill_id=%s",
            user.uid,
            session_id,
            idx.skill_id,
        )
        raise HTTPException(status_code=404, detail="Session backend not found")

    # The stash entry must already exist — the emitter is the only author of
    # surfaces; the client may only layer a data-model edit on top of one.
    state_key = f"{A2UI_SURFACE_STATE_PREFIX}{body.surface_id}"
    raw = dict(session.state or {}).get(state_key)
    payload: Any = None
    if raw is not None:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            payload = None
    if not isinstance(payload, dict):
        log.info(
            "surface_data: no stashed surface uid=%s session_id=%s surface=%s",
            user.uid,
            session_id,
            body.surface_id,
        )
        raise HTTPException(status_code=404, detail="No rehydratable surface to update")

    if body.data_model is None:
        payload.pop("clientDataModel", None)
    else:
        payload["clientDataModel"] = {
            "value": body.data_model,
            # Epoch ms, matching the stash's createdAt convention.
            "updatedAt": time.time() * 1000,
        }

    event = Event(
        invocation_id=f"surface_data_{int(time.time() * 1000)}",
        author="user",
        actions=EventActions(state_delta={state_key: json.dumps(payload)}),
        timestamp=time.time(),
    )
    await session_service.append_event(session, event)

    log.info(
        "surface_data: write uid=%s session=%s skill=%s surface=%s bytes=%s cleared=%s",
        user.uid,
        session_id,
        idx.skill_id,
        body.surface_id,
        size_bytes,
        body.data_model is None,
    )
    return None


__all__ = ["router"]
