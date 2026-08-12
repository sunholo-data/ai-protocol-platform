"""FastAPI routes for the auth module — /api/auth/* endpoints.

Currently exposes `GET /api/auth/whoami`, which echoes the caller's verified
Firebase identity. Useful during bring-up to confirm a freshly-minted ID token
carries the expected `groupTags` custom claim without needing a UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import User, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/whoami")
def whoami(user: User = Depends(get_current_user)) -> dict:  # noqa: B008
    return {
        "uid": user.uid,
        "email": user.email,
        "domain": user.domain,
        "groupTags": sorted(user.group_tags),
    }
