"""Session bootstrap — pre-create ChatSessionIndex + ADK session.

Called fire-and-forget by the frontend when the AG-UI HttpAgent first sees a
session_id, before the first agent turn. Without this, iframe context pushes
(ui/update-model-context) that arrive before the first turn 404 because the
ChatSessionIndex row does not exist yet.

Endpoint:
    POST /api/sessions/{sessionId}/bootstrap

Idempotent: if the Firestore index already exists, the call is a no-op (200).
The ADK session_service.create_session() is also idempotent for the same id.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from adk.agui import APP_NAME
from adk.session import get_session_service
from auth import User, get_current_user
from db.chat_sessions import create_session_index, get_session_index, owner_domain_of
from db.models.access import AccessControl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])


class BootstrapRequest(BaseModel):
    skill_id: str
    document_ids: list[str] = []


class BootstrapResponse(BaseModel):
    session_id: str
    created: bool


@router.post("/sessions/{session_id}/bootstrap", response_model=BootstrapResponse)
async def bootstrap_session(
    session_id: str,
    body: BootstrapRequest,
    request: Request,
    user: User = Depends(get_current_user),  # noqa: B008
) -> BootstrapResponse:
    """Pre-create a ChatSessionIndex + ADK session before the first agent turn.

    Idempotent: returns 200 whether or not the index already existed.
    The ``created`` field in the response distinguishes new from existing.

    Access: any authenticated user may bootstrap a session they own — the
    caller's uid becomes owner_uid. If the session already exists and was
    created by a different owner, returns 403.
    """
    existing = get_session_index(session_id)
    if existing is not None:
        if existing.owner_uid != user.uid:
            raise HTTPException(status_code=403, detail="Session owned by another user")
        return BootstrapResponse(session_id=session_id, created=False)

    create_session_index(
        session_id=session_id,
        skill_id=body.skill_id,
        owner_uid=user.uid,
        owner_domain=owner_domain_of(user.email),
        access_control=AccessControl(type="private"),
        document_ids=body.document_ids,
        # PROVISIONAL until the first real turn: this fires when the chat page
        # MOUNTS, so without the flag every opened-and-abandoned chat became a
        # history entry (22 of 40 rows on test were these). The row still has to
        # exist now — iframe context pushes 404 without it — it just stays
        # hidden from the session lists until `_ensure_session_index` promotes
        # it on the first turn.
        provisional=True,
    )

    session_service = get_session_service()
    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=user.uid,
            session_id=session_id,
        )
    except Exception:
        logger.warning(
            "session_bootstrap: ADK create_session failed for %s (index still created)",
            session_id,
        )

    logger.info("session_bootstrap: pre-created session %s for skill %s", session_id, body.skill_id)
    return BootstrapResponse(session_id=session_id, created=True)
