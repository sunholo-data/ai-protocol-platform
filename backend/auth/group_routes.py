"""FastAPI routes for the anonymous group-ID auth flow (sprint 2.11, M2).

Four endpoints:
  - POST   /api/auth/group/create     teacher-only (Firebase auth required)
  - POST   /api/auth/group/join       anonymous; rate-limited per IP
  - DELETE /api/auth/group/{id}       teacher-only; creator-match required
  - GET    /api/auth/group/{id}       metadata only (no member list)

Status-code map (mirrors the M1 seven-gate matrix):
  gate 1: 422  Pydantic body schema rejection
  gate 2: 401  unknown group_id
  gate 3: 401  expired group
  gate 4: 401  revoked group
  gate 5: 429  rate-limit exceeded (Retry-After header included)
  gate 6: 503  per-group concurrent-session cap exceeded
  gate 7: 200  happy path with token + uid + expires_at

DELETE produces:
  204  on success
  403  caller is not the group's creator
  404  group never existed (or was already deleted)

GET produces:
  200  with {group_id, title, expires_at, max_concurrent_sessions}
  404  unknown group_id (revoked OR never existed — privacy: don't leak the difference)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth.group_id_auth import (
    GroupExpired,
    GroupNotFound,
    GroupRecord,
    GroupRevoked,
    GroupSessionCapExceeded,
    create_group,
    delete_group,
    get_group,
    join_group,
)
from auth.group_rate_limit import RateLimitExceeded

if TYPE_CHECKING:
    from auth.firebase_auth import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/group", tags=["anonymous-group-auth"])


# ─── Wire models ────────────────────────────────────────────────────────────


class CreateGroupRequest(BaseModel):
    """Body of POST /api/auth/group/create."""

    title: str = Field(min_length=1, max_length=200)
    skill_ids: list[str] = Field(min_length=1, max_length=50)
    ttl_days: int = Field(default=30, ge=1, le=365)
    max_concurrent_sessions: int = Field(default=100, ge=1, le=10_000)

    model_config = {"extra": "forbid"}


class CreateGroupResponse(BaseModel):
    group_id: str
    expires_at: float
    join_url: str


class JoinGroupRequest(BaseModel):
    group_id: str = Field(min_length=1, max_length=64)

    model_config = {"extra": "forbid"}


class JoinGroupResponse(BaseModel):
    token: str
    uid: str
    expires_at: float


class GroupMetadataResponse(BaseModel):
    group_id: str
    title: str
    expires_at: float
    max_concurrent_sessions: int


# ─── Helpers ────────────────────────────────────────────────────────────────


def _firebase_user() -> User:
    """Resolver for endpoints that require Firebase auth (teacher path).

    Imported lazily so this module can be loaded before the rest of the
    auth dispatcher wiring; the actual ``get_current_user`` is set on
    the route as a Depends below.
    """
    raise RuntimeError("placeholder — replaced at route registration time")


def _build_join_url(group_id: str, request: Request) -> str:
    """Produce a teacher-shareable join link. Trusts the X-Forwarded-Host
    if present (Cloud Run); falls back to the request URL's base."""
    base = request.headers.get("x-forwarded-host") or request.url.netloc
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{scheme}://{base}/group?code={group_id}"


def _client_ip(request: Request) -> str:
    """Best-effort caller IP. Cloud Run / load balancer set X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First entry is the originating client per RFC 7239.
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── Endpoints ──────────────────────────────────────────────────────────────


def _resolve_firebase_user_dep():
    """Yield the configured Firebase-auth dependency.

    Indirected so tests can dependency_override(get_current_user) and
    have BOTH the routes module AND the main app pick up the override.
    """
    from auth import get_current_user

    return get_current_user


@router.post("/create", status_code=201, response_model=CreateGroupResponse)
async def create_group_endpoint(
    body: CreateGroupRequest,
    request: Request,
    user: User = Depends(_resolve_firebase_user_dep()),  # noqa: B008
) -> CreateGroupResponse:
    """Teacher creates a group. Requires Firebase auth."""
    rec = create_group(
        title=body.title,
        skill_ids=body.skill_ids,
        creator_uid=user.uid,
        ttl_days=body.ttl_days,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )
    logger.info(
        "group_routes: created group=%s creator=%s ttl=%dd skills=%d",
        rec.group_id,
        user.uid,
        body.ttl_days,
        len(body.skill_ids),
    )
    return CreateGroupResponse(
        group_id=rec.group_id,
        expires_at=rec.expires_at,
        join_url=_build_join_url(rec.group_id, request),
    )


@router.post("/join", status_code=200, response_model=JoinGroupResponse)
async def join_group_endpoint(
    body: JoinGroupRequest,
    request: Request,
) -> JoinGroupResponse:
    """Anonymous join. No auth required; rate-limited per IP."""
    ip = _client_ip(request)
    try:
        result = join_group(body.group_id, client_ip=ip)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded; retry after {exc.retry_after_seconds}s",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except GroupSessionCapExceeded as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GroupExpired as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GroupRevoked as exc:
        # Privacy: don't distinguish revoked from unknown in client message
        raise HTTPException(status_code=401, detail="group not found or no longer active") from exc
    except GroupNotFound as exc:
        raise HTTPException(status_code=401, detail="group not found or no longer active") from exc
    except ValueError as exc:
        # Gate 1 fallback if Pydantic didn't catch — defensive
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JoinGroupResponse(
        token=result.token,
        uid=result.uid,
        expires_at=result.expires_at,
    )


@router.delete("/{group_id}", status_code=204)
async def delete_group_endpoint(
    group_id: str,
    user: User = Depends(_resolve_firebase_user_dep()),  # noqa: B008
) -> None:
    """Revoke a group. Only the creator may delete."""
    try:
        delete_group(group_id, requesting_uid=user.uid)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GroupNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return None


@router.get("/{group_id}", response_model=GroupMetadataResponse)
async def get_group_endpoint(
    group_id: str,
    user: User = Depends(_resolve_firebase_user_dep()),  # noqa: B008
) -> GroupMetadataResponse:
    """Return group metadata. NO member list returned (privacy)."""
    rec: GroupRecord | None = get_group(group_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="group not found")
    return GroupMetadataResponse(
        group_id=rec.group_id,
        title=rec.title,
        expires_at=rec.expires_at,
        max_concurrent_sessions=rec.max_concurrent_sessions,
    )


__all__ = ["router"]
