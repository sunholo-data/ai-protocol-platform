"""Auth module — Firebase JWT verification + access/permission checks.

Sprint AUTH-PERMISSIONS layering:
    M1  firebase_auth.User + firebase_auth.get_current_user
    M2  access_context.AccessContext + access_context.build_access_context
    M3  permissions.can_use_tool + permissions.ToolPermissionDenied

LOCAL_MODE (sprint LOCAL-MODE-AND-FORK):
    When ``LOCAL_MODE=1`` the ``get_current_user`` dependency dispatches to
    ``auth.local_mode_stub.get_current_user_local_mode`` which accepts only
    the well-known stub token AND the anonymous-group-id JWT (sprint 2.11).
    All Cloud-Run / GAE / GKE markers are rejected at startup so this stub
    can never be active in a deployed context.

Token-shape dispatch (sprint 2.11, M2):
    The fourth auth mode (anonymous group-ID) mints HS256 JWTs with our own
    secret, NOT Firebase tokens. The dispatcher peeks at the unverified
    claims to decide which verifier to run:
      - claims["auth_mode"] == "anonymous_group_id"  → group_id_auth verifier
      - LOCAL_MODE stub token literal               → local_mode_stub
      - everything else                              → Firebase verifier
    Bearers that fail any verifier → 401 (no fallback chain — security).

Downstream imports should go through this module (`from auth import ...`)
so later milestones can swap or extend internals without fan-out changes.
"""

import logging
import os

import jwt
from fastapi import HTTPException, Request

from auth.access_context import AccessContext, build_access_context, can_access
from auth.firebase_auth import User
from auth.firebase_auth import get_current_user as _firebase_get_current_user
from auth.group_id_auth import AUTH_MODE as _GROUP_AUTH_MODE
from auth.group_id_auth import (
    AnonymousGroupAuth,
    GroupRevoked,
    InvalidGroupToken,
)
from auth.permissions import ToolPermissionDenied, can_use_tool
from config.local_mode import is_local_mode

logger = logging.getLogger(__name__)


def _peek_token_auth_mode(token: str) -> str | None:
    """Inspect unverified JWT claims to decide which verifier to run.

    Returns the ``auth_mode`` claim string (e.g. "anonymous_group_id")
    if present, otherwise ``None`` (likely a Firebase token, or
    malformed — let the Firebase verifier handle the final decision).

    UNVERIFIED is safe HERE because:
      - We don't trust the claim — it just routes to the right
        verifier. The verifier then enforces the signature.
      - A forged claim ``auth_mode=anonymous_group_id`` will be
        rejected by group_id_auth's HS256-signature check.
      - A token shaped to look like Firebase (no auth_mode claim)
        falls through to the Firebase verifier which enforces RS256
        + Google-issued signature.
    """
    try:
        # We only read the unauthenticated `auth_mode` claim to ROUTE to
        # the right verifier. The verifier then enforces the signature.
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    mode = unverified.get("auth_mode")
    return mode if isinstance(mode, str) else None


async def _group_auth_get_current_user(request: Request, token: str) -> User:
    """Verify an anonymous-group-id token and stash AccessContext."""
    try:
        user = AnonymousGroupAuth.user_from_token(token)
    except GroupRevoked as exc:
        logger.info("auth: rejected revoked group token")
        raise HTTPException(status_code=401, detail="group revoked") from exc
    except InvalidGroupToken as exc:
        logger.info("auth: rejected invalid group token (%s)", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    request.state.access = build_access_context(user)
    logger.info("auth: group-auth uid=%s group=%s", user.uid, user.group_id)
    return user


_ALLOWLIST_TRUTHY = {"1", "true", "yes", "on"}


def _require_known_domain() -> bool:
    """Whether authentication is restricted to an email-domain allowlist (v6.18.0).

    Off by default (backward-compatible — every existing env keeps working). Set
    ``AUTH_REQUIRE_KNOWN_DOMAIN=1`` per-env to lock a deployment to its customer
    + operator domains.

    Turning it on requires ``AUTH_OPERATOR_DOMAINS`` to be set as well — that
    default is empty on purpose (see below), so the gate without it admits only
    mapped tenants.
    """
    return os.environ.get("AUTH_REQUIRE_KNOWN_DOMAIN", "").strip().lower() in _ALLOWLIST_TRUTHY


def _operator_domains() -> frozenset[str]:
    """Operator email domains that are always permitted, independent of a tenant
    mapping. Config via ``AUTH_OPERATOR_DOMAINS`` (csv).

    **Empty by default, deliberately.** An operator domain is deployment
    identity: there is no value that is correct for an unknown deployment, so
    the only safe default is none, and every deployment declares its own.

    This used to default to this deployment's own domains, with the template
    sanitizer rewriting the literal on the way out (v6.19.0, AIPLA #42 — a fork
    that switched on the domain gate would otherwise trust *our* staff domain).
    TEMPLATE-INVERT M3 removed that: publish-time rewriting cannot survive the
    upstream/downstream inversion, because the same tracked path would hold
    different bytes on each side — a permanent merge conflict on every sync.

    The real values now live in deploy config (``cloudbuild.yaml`` passes
    ``AUTH_OPERATOR_DOMAINS`` explicitly). Removing the default without that
    half would 403 every operator on dev/test, which both set
    ``AUTH_REQUIRE_KNOWN_DOMAIN=1`` — asserted by
    ``tests/unit/test_fork_safe_defaults.py``.
    """
    raw = os.environ.get("AUTH_OPERATOR_DOMAINS", "")
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


def _domain_allowed(user: User) -> bool:
    """A domain is allowed iff it is an operator domain OR a mapped tenant
    (``clients/{domain}`` exists). Fails closed: an empty domain or a Firestore
    miss/error → not allowed (operators still pass via the operator-domain check,
    which needs no Firestore)."""
    domain = (user.domain or "").strip().lower()
    if not domain:
        return False
    if domain in _operator_domains():
        return True
    from db.clients import get_client_cached

    return get_client_cached(domain) is not None


def _enforce_domain_allowlist(user: User) -> None:
    """403 an authenticated caller whose email domain isn't permitted (v6.18.0
    Gap B), when ``AUTH_REQUIRE_KNOWN_DOMAIN`` is on. Exempts LOCAL_MODE (dev/fork
    stub — never a deployed context) and anonymous group-id workshop identities
    (no email domain; Gap-A still contains what they can read)."""
    if not _require_known_domain():
        return
    if is_local_mode():
        return
    if user.auth_mode == _GROUP_AUTH_MODE:
        return
    if _domain_allowed(user):
        return
    if not _operator_domains():
        # NEVER SILENT (CLAUDE.md #8). The gate is on but no operator domain is
        # configured, so every operator is about to be denied — and the symptom
        # (a 403 for staff who have always worked) reads as an auth bug rather
        # than a missing env var. TEMPLATE-INVERT M3 removed the code default
        # that used to mask this, so the deploy must supply
        # AUTH_OPERATOR_DOMAINS. See traps.md #23: that is TWO trigger
        # substitutions, not one.
        logger.error(
            "AUTH_OPERATOR_DOMAINS_MISSING: AUTH_REQUIRE_KNOWN_DOMAIN is on but "
            "AUTH_OPERATOR_DOMAINS is empty — only mapped tenants can authenticate. "
            "Set it in this env's trigger substitutions (uid=%s domain=%s)",
            user.uid,
            user.domain or "(none)",
        )
    logger.info("auth: rejected domain uid=%s domain=%s", user.uid, user.domain or "(none)")
    raise HTTPException(
        status_code=403,
        detail={
            "code": "DOMAIN_NOT_PERMITTED",
            "message": "This account's domain isn't permitted on this deployment.",
        },
    )


async def get_current_user(request: Request) -> User:
    """FastAPI auth dependency. Dispatches to the right verifier and
    binds the tenant contextvar so every OTel span emitted during the
    request carries tenant attribution.

    The dispatcher logic lives in ``_resolve_user``; this wrapper is
    the single insertion point for sprint 2.14's
    ``set_tenant_context(user)`` — covering all three auth paths
    (Firebase, group-auth, LOCAL_MODE stub) without touching the 13
    endpoints that depend on ``get_current_user`` — and for the v6.18.0
    domain allowlist (``_enforce_domain_allowlist``).
    """
    from observability.tenant_context import set_tenant_context

    user = await _resolve_user(request)
    _enforce_domain_allowlist(user)
    set_tenant_context(user)
    return user


async def _resolve_user(request: Request) -> User:
    """Token-shape dispatcher for the three auth paths.

    Order:
      1. LOCAL_MODE: try stub token first; fall through to group-auth
         (group tokens are also valid in LOCAL_MODE so forks can demo).
      2. Cloud-mode: peek at the JWT's ``auth_mode`` claim:
           - "anonymous_group_id" → group_id_auth verifier
           - missing / other       → Firebase verifier
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        # Defer the 401-shape error to the chosen verifier so the message
        # remains consistent across paths.
        if is_local_mode():
            from auth.local_mode_stub import get_current_user_local_mode

            return await get_current_user_local_mode(request)
        return await _firebase_get_current_user(request)

    token = auth_header[len("Bearer ") :].strip()

    # LOCAL_MODE: accept stub literal OR group-auth JWT (forks may use both).
    if is_local_mode():
        from auth.local_mode_stub import STUB_TOKEN, get_current_user_local_mode

        if token == STUB_TOKEN:
            return await get_current_user_local_mode(request)
        # Fall through to group-auth in LOCAL_MODE so anonymous-group
        # forks can demo without Firebase.

    # Token-shape dispatch.
    mode = _peek_token_auth_mode(token)
    if mode == _GROUP_AUTH_MODE:
        return await _group_auth_get_current_user(request, token)

    if is_local_mode():
        # LOCAL_MODE and the token is neither the stub nor a group JWT:
        # fall through to the stub's 401 message for consistent error UX.
        from auth.local_mode_stub import get_current_user_local_mode

        return await get_current_user_local_mode(request)

    return await _firebase_get_current_user(request)


__all__ = [
    "AccessContext",
    "ToolPermissionDenied",
    "User",
    "build_access_context",
    "can_access",
    "can_use_tool",
    "get_current_user",
]
