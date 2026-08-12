"""
Firebase JWT verification middleware for FastAPI.

Provides:
    - `User`: Pydantic model for an authenticated caller (uid, email, domain,
      group_tags as frozenset from the JWT `groupTags` custom claim).
    - `get_current_user`: FastAPI dependency that parses the `Authorization`
      header, calls `firebase_admin.auth.verify_id_token`, and returns `User`.

Assumes `firebase_admin.initialize_app()` has been called at app startup
(see `fast_api_app.py`). Uses Application Default Credentials (ADC) — on
Cloud Run the attached service account is picked up automatically; locally,
`gcloud auth application-default login` supplies creds.

No PII (email) is logged on success or failure — only `uid` on success, and
the exception type name on failure.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, Request
from firebase_admin import auth as fb_auth
from pydantic import BaseModel, ConfigDict, Field

from auth.access_context import build_access_context

logger = logging.getLogger(__name__)


class User(BaseModel):
    """Authenticated caller extracted from a verified auth token.

    Used across all four auth modes (Firebase, Identity Platform,
    LOCAL_MODE stub, anonymous-group-id — sprint 2.11).

    `group_tags` is populated from the JWT `groupTags` custom claim set
    server-side via `firebase_admin.auth.set_custom_user_claims`. Because it
    comes from a signed JWT the client cannot forge it. An absent or empty
    claim lands as an empty frozenset.

    `auth_mode` distinguishes the four modes for downstream code that
    needs to branch on it (e.g. permissions falls back to `group/<gid>`
    lookup for `anonymous_group_id`). Defaults to `"firebase"` for
    back-compat — existing call sites that construct `User` directly
    don't need to change.

    `group_id` is set ONLY when `auth_mode == "anonymous_group_id"`;
    empty string otherwise. Carrying it on User (rather than as a
    side-channel) keeps the permission system and observability hooks
    type-safe.
    """

    model_config = ConfigDict(frozen=True)

    uid: str
    email: str = ""
    domain: str = ""
    group_tags: frozenset[str] = Field(default_factory=frozenset)
    auth_mode: str = "firebase"
    group_id: str = ""


def _extract_domain(email: str) -> str:
    """Return the part of `email` after `@`, or `""` if there is no `@`."""
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1]


def _user_from_decoded_token(decoded: dict[str, Any]) -> User:
    """Build a `User` from a verified Firebase decoded-token dict."""
    email = decoded.get("email") or ""
    raw_tags = decoded.get("groupTags") or []
    group_tags = frozenset(str(t) for t in raw_tags)
    return User(
        uid=decoded["uid"],
        email=email,
        domain=_extract_domain(email),
        group_tags=group_tags,
    )


async def get_current_user(request: Request) -> User:
    """FastAPI dependency: verify `Authorization: Bearer <jwt>` → `User`.

    Raises:
        HTTPException(401): missing header, malformed header (not `Bearer `),
            empty bearer token, or `verify_id_token` rejects the token
            (expired, invalid signature, malformed, revoked).
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        decoded = fb_auth.verify_id_token(token)
    except fb_auth.ExpiredIdTokenError as exc:
        logger.info("auth: rejected expired token")
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except fb_auth.InvalidIdTokenError as exc:
        logger.info("auth: rejected invalid token")
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    except Exception as exc:
        # Last-resort guard: firebase-admin can raise ValueError on shape errors.
        logger.info("auth: rejected token (%s)", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    user = _user_from_decoded_token(decoded)
    user = _apply_derived_group_tags(user)
    # Stash a request-scoped AccessContext so route handlers can call
    # `request.state.access.can_access_skill(...)` without re-reading the JWT.
    request.state.access = build_access_context(user)
    logger.info("auth: authenticated uid=%s", user.uid)
    return user


def _apply_derived_group_tags(user: User) -> User:
    """Union per-domain derived tags from clients/{domain} into user.group_tags.

    Lets a deployment grant a tag (e.g. `ONE`) to every user from a customer's
    email domain without an admin calling `set_custom_user_claims` per signup.
    Returns the user unchanged when there is no derived-tag config (the common
    case for internal yourcompany.com users).
    """
    if not user.domain:
        return user
    # Local import avoids a module-level circular dep (db.clients ↔ auth.User
    # typing arg in resolve_documents_bucket).
    from db.clients import resolve_derived_group_tags

    try:
        derived = resolve_derived_group_tags(user.domain)
    except Exception as exc:  # Firestore unavailability must not block auth
        logger.warning("auth: derived-group-tags lookup failed (%s)", type(exc).__name__)
        return user
    if not derived:
        return user
    return user.model_copy(update={"group_tags": user.group_tags | derived})


# --- authoritative lookup for non-JWT callers (channels) -------------------
#
# A channel (Discord, Telegram, …) has no JWT — the user is identified by a
# webhook signature plus a `channel_identities` mapping to a Firebase UID.
# That mapping mirrors `email` / `domain` / `group_tags`, but the mirror is
# ADVISORY ONLY (see `channels/identity.py`): reading tags from it would let
# a stale or tampered Firestore document grant access.
#
# So privilege resolution stays HERE, on the same authority the JWT path
# uses — the Firebase custom claim — with the same derived-tag union applied
# afterwards. Channels ask this function; they never assemble tags themselves.

_USER_CACHE_TTL_SEC = 60.0
_user_cache: dict[str, tuple[float, User | None]] = {}


def clear_user_cache() -> None:
    """Drop the resolve-by-UID cache. For tests and admin claim changes."""
    _user_cache.clear()


def resolve_user_by_uid(uid: str) -> User | None:
    """Build an authoritative `User` from the Firebase record for `uid`.

    Group tags come from the account's **custom claims**, unioned with the
    per-domain derived tags — byte-identical authority to `get_current_user`,
    just reached by UID instead of by verifying a JWT.

    Returns None when the record can't be read (unknown UID, Admin SDK error,
    Firebase unavailable). Callers MUST treat None as "no privileges" and fall
    back to a restricted user — never to a permissive default.

    Results are cached for `_USER_CACHE_TTL_SEC`, since a channel would
    otherwise make one Admin SDK call per inbound message. A claim change
    therefore takes up to that long to take effect on channels; call
    `clear_user_cache()` to apply one immediately.
    """
    now = time.monotonic()
    cached = _user_cache.get(uid)
    if cached is not None and now - cached[0] < _USER_CACHE_TTL_SEC:
        return cached[1]

    user: User | None
    try:
        record = fb_auth.get_user(uid)
    except Exception as exc:
        # Unknown UID, revoked account, or Firebase unavailable. Fail closed.
        logger.warning("auth: resolve_user_by_uid(%s) failed (%s)", uid, type(exc).__name__)
        user = None
    else:
        email = getattr(record, "email", None) or ""
        claims = getattr(record, "custom_claims", None) or {}
        raw_tags = claims.get("groupTags") or []
        try:
            group_tags = frozenset(str(t) for t in raw_tags)
        except TypeError:
            # Malformed claim (not iterable) grants nothing rather than raising.
            logger.warning("auth: uid=%s has a non-iterable groupTags claim; ignoring", uid)
            group_tags = frozenset()
        user = _apply_derived_group_tags(
            User(uid=uid, email=email, domain=_extract_domain(email), group_tags=group_tags)
        )

    _user_cache[uid] = (now, user)
    return user


__all__ = ["User", "clear_user_cache", "get_current_user", "resolve_user_by_uid"]
