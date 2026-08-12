"""Canonical access-control schema for skills, buckets, folders, chat sessions.

Promoted out of `db/models/__init__.py` during AUTH-PERMISSIONS M2 so
resource-access-control (1A.1b) can share the exact same type.

Five access types:
    - public    : anyone, no auth required on read
    - private   : owner only
    - domain    : any user whose `User.domain` matches `AccessControl.domain`
    - specific  : any user whose `User.email` is in `AccessControl.emails`
    - tagged    : any user whose `User.group_tags` intersects `AccessControl.tags`
                  — the B2B team-sharing primitive; tags come from server-signed
                  JWT custom claims (see backend/auth/firebase_auth.py).

The owner (uid == resource.ownerId) always wins regardless of type. The
evaluator lives in `backend/auth/access_context.py`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

AccessType = Literal["private", "public", "domain", "specific", "tagged"]

_ALLOWED_TYPES: frozenset[str] = frozenset({"private", "public", "domain", "specific", "tagged"})


class AccessControl(BaseModel):
    """Access policy attached to a resource (skill, bucket, folder, chat session)."""

    type: AccessType = "private"
    domain: str | None = None  # required iff type == "domain"
    emails: list[str] | None = None  # required iff type == "specific"
    tags: list[str] | None = None  # required iff type == "tagged"

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _ALLOWED_TYPES:
            raise ValueError(f"access type must be one of {sorted(_ALLOWED_TYPES)}")
        return v


__all__ = ["AccessControl", "AccessType"]
