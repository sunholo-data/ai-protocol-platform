"""FastAPI router for /api/admin/* endpoints.

The seed endpoint is gated by `_assert_caller_is_service_account` (Google
ID token + SA email allowlist) — it exists to support Cloud Build deploy hooks
and ops runbooks, never end users.

`GET /whoami` is different by design: it is the *role probe* the frontend uses
to decide whether to show the Admin surface at all, so it answers for any
authenticated user rather than 403-ing.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request  # Depends used inside Annotated[]
from pydantic import BaseModel

from admin import platform_seed
from admin.auth import _assert_caller_is_service_account
from admin.scope import resolve_admin_scope
from auth import User, get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminWhoAmI(BaseModel):
    """The caller's resolved admin authority.

    `scope` is one of "platform" | "tenant" | "none". "none" is returned with a
    **200**, not a 403 — the frontend needs to distinguish "you are not an
    admin" (render nothing, quietly) from "something broke" (render an error).
    Probing a data endpoint to infer a role is what made the Admin link
    invisible to tenant admins in the first place; this endpoint exists so the
    role question is asked directly.
    """

    scope: str
    domains: list[str] = []
    email: str = ""


@router.get("/whoami", response_model=AdminWhoAmI)
def admin_whoami(user: Annotated[User, Depends(get_current_user)]) -> AdminWhoAmI:
    """Report the caller's admin scope. Any authenticated user; never 403."""
    resolved = resolve_admin_scope(user)
    if resolved is None:
        return AdminWhoAmI(scope="none", domains=[], email=user.email or "")
    if resolved.is_platform:
        return AdminWhoAmI(scope="platform", domains=[], email=user.email or "")
    return AdminWhoAmI(scope="tenant", domains=sorted(resolved.domains or ()), email=user.email or "")


@router.post("/seed-platform-skills")
def seed_platform_skills(request: Request) -> dict[str, Any]:
    """Idempotently seed the default platform-owned skills.

    Hit once per deploy by the Cloud Build seed step. Returns a JSON
    SeedSummary so Cloud Build logs capture what happened.
    """
    _assert_caller_is_service_account(request)
    summary = platform_seed.seed()
    return summary.as_dict()
