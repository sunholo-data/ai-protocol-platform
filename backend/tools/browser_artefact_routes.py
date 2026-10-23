"""`GET /api/browser/artefacts/{artefact_id}` — the only way screenshot bytes leave.

`adk.media_results` puts browser screenshots and PDFs in the private artefact
store and hands the model a reference. This route is the other half: it streams
those bytes back to the person the artefact belongs to, and to nobody else.

WHY THIS IS A ROUTE AND NOT A URL
A screenshot of a page the browser reached while logged in is a derived
artefact of confidential content — CLAUDE.md's security rule names page
screenshots explicitly, because page 1 of a contract still leaks parties,
jurisdiction and dates. So there is no signed URL and no public object: bytes
are fetched server-side with the backend SA and streamed through this gate, the
same shape as `tools/documents/routes.py`.

WHY THERE IS NO uid COMPARISON IN HERE
The obvious implementation looks up an artefact, reads an owner off it and
compares. This one cannot get that comparison wrong because it never makes it:
the artefact is stored user-namespaced (`user:<id>`), this route re-adds that
prefix itself, and `user_id` comes from the verified token. A caller therefore
addresses only their own namespace whatever they put in the path — the check is
structural rather than remembered. `_validate_artefact_id` refuses anything
that could reach sideways out of it.

That also settles missing-vs-forbidden: another user's artefact is simply not
in the caller's namespace, so it is a 404. Answering 403 would confirm the
artefact exists — a smaller leak than the bytes, and still a leak.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from adk.agui import APP_NAME
from adk.media_results import ARTEFACT_NAMESPACE
from adk.session import get_artifact_service
from auth import User, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["browser-artefacts"])

_CurrentUser = Annotated[User, Depends(get_current_user)]

# One path segment: the shape `media_results` mints, and nothing else. No
# slashes (no climbing into another session's prefix), no colon (no choosing
# the namespace), no dot-segments.
_ARTEFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def _validate_artefact_id(artefact_id: str) -> str:
    """Reject anything that is not a bare artefact id. Raises 400."""
    if not _ARTEFACT_ID_RE.match(artefact_id) or ".." in artefact_id:
        raise HTTPException(status_code=400, detail="Invalid artefact id")
    return artefact_id


def _content_disposition(filename: str) -> str:
    """RFC 6266 inline disposition, safe for any filename.

    Mirrors `tools/documents/routes.py`: header values serialise as latin-1, so
    a non-latin-1 character in a raw `filename=` raises at send time and
    surfaces as an opaque 500. Artefact ids are ASCII by construction today —
    this keeps that from becoming load-bearing.
    """
    from urllib.parse import quote

    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii").strip()
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_") or "artefact"
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/api/browser/artefacts/{artefact_id}")
async def get_browser_artefact(artefact_id: str, user: _CurrentUser):
    """Stream one browser artefact belonging to the caller.

    Returns 400 for a malformed id, 404 when the caller's namespace holds no
    such artefact (including when it is somebody else's), 502 when the store
    cannot be read, and 200 with the bytes otherwise.
    """
    _validate_artefact_id(artefact_id)

    try:
        part = await get_artifact_service().load_artifact(
            app_name=APP_NAME,
            user_id=user.uid,
            filename=f"{ARTEFACT_NAMESPACE}{artefact_id}",
        )
    except Exception as exc:
        # Loud, not silent: a fetch that failed is not the same as one that
        # found nothing, and the caller is told which (CLAUDE.md #8).
        log.error("browser artefact: load failed for %s: %s", artefact_id, exc)
        raise HTTPException(status_code=502, detail="Could not read the artefact store") from exc

    inline = getattr(part, "inline_data", None) if part is not None else None
    data = getattr(inline, "data", None) if inline is not None else None
    if not data:
        # Missing, empty, or not a binary part — all "nothing to serve here".
        raise HTTPException(status_code=404, detail="Artefact not found")

    def _iter():
        yield data

    return StreamingResponse(
        _iter(),
        media_type=getattr(inline, "mime_type", None) or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(artefact_id),
            # Confidential and per-user: never a shared or persisted copy.
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["router"]
