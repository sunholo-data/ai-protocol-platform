"""Tailnet auth mode — identity from ``tailscale serve`` for self-hosted forks.

For a deployment that runs on one machine and is reachable ONLY over a
tailnet (``tailscale serve``, never Funnel), Tailscale has already
authenticated every user request: tailscaled adds ``Tailscale-User-Login`` /
``Tailscale-User-Name`` and overwrites any client-supplied copy. This mode
turns that header into a platform ``User`` instead of running Firebase Auth.

Enabled by ``AUTH_MODE=tailnet``. It REPLACES the other verifiers — no
Firebase, stub or group-token fallback — and it composes with or without
``LOCAL_MODE`` (in-memory vs real Firestore is a separate choice).

Two ways in, nothing else:

1. **Via the frontend proxy** — the request carries ``X-Proxy-Secret`` equal
   to ``TAILNET_PROXY_SECRET``. Only then is ``Tailscale-User-Login`` read,
   and it must be a key of ``TAILNET_PRINCIPALS``. The secret is what stops
   another process on the machine forging the header straight at the backend
   port. Any ``Authorization`` header on these requests is ignored.
2. **A machine caller** (e.g. a local agent) — ``Authorization: Bearer
   <token>`` where ``sha256(token)`` is a key of ``MACHINE_PRINCIPALS``.
   Only hashes are configured; the raw token lives in the caller's own
   0600 file. A machine may only call the routes in its ``routes`` list
   (``"METHOD /path/glob"``); without one it gets ``DEFAULT_MACHINE_ROUTES`` —
   read skills and chat with them, nothing that writes. Which skills it sees
   is the normal skill access control: give each machine its own tag.
   A machine allowed to write skills may only give them ``private`` or
   ``tagged`` access with tags it holds itself (``check_machine_access_control``)
   — it can build for itself, never publish to others. Machines get an email
   (``<name>@machine.local`` unless the principal sets ``email``) so
   ``tool_permissions/<email>`` can limit their tools in ANY skill, including
   ones they wrote.

Config (JSON env vars)::

    TAILNET_PRINCIPALS  {"ada@example.com": {"uid": "ada", "groupTags": ["staff"],
                                             "displayName": "Ada"}}
    MACHINE_PRINCIPALS  {"<sha256 hex of token>": {"uid": "machine:agent",
                                                   "groupTags": ["agent"],
                                                   "displayName": "Agent",
                                                   "routes": ["GET /api/skills"],
                                                   "supervisors": ["ada@example.com"],
                                                   "notifyInbox": "agent"}}

``supervisors`` (machines only) share the machine's chat threads with those
humans: they see them under "Shared with me" and may reply. A reply runs in the
thread with the MACHINE's identity, tools and skill access — never the human's
— so nothing can land in a thread the machine isn't allowed to read. The reply
is labelled with the human's name, and ``notifyInbox`` (an AILANG messages
inbox) is pinged so the machine knows to read it.
    TAILNET_PROXY_SECRET  random, shared with the frontend proxy

Residual risk (documented, not solved here): the header is trusted from
whatever reaches the FRONTEND port, so another OS user on the same machine
can reach that loopback port and forge the header. The boundary is "the
machine's users", enforced by the tailnet ACL for everyone else.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from fnmatch import fnmatchcase
from functools import lru_cache

from fastapi import HTTPException, Request

from auth.access_context import build_access_context
from auth.firebase_auth import User

logger = logging.getLogger(__name__)

AUTH_MODE = "tailnet"
MACHINE_AUTH_MODE = "machine"
MACHINE_EMAIL_DOMAIN = "machine.local"
PROXY_SECRET_HEADER = "X-Proxy-Secret"
LOGIN_HEADER = "Tailscale-User-Login"

# Header trust only makes sense behind tailscaled on the same machine. On a
# managed platform the header is just client input.
_DEPLOY_ENV_ADAERS: tuple[str, ...] = ("K_SERVICE", "GAE_ENV", "KUBERNETES_SERVICE_HOST")

# What a machine caller may do when its principal names no ``routes``: find out
# who it is, list/read the skills it can see, and chat with one. Deliberately no
# skill create/edit/fork/delete — an agent that could publish a skill could
# grant itself (or anyone) access, bypassing the operator's tagging.
DEFAULT_MACHINE_ROUTES: tuple[str, ...] = (
    "GET /api/auth/whoami",
    "GET /api/tools",  # the tool catalog, filtered to what this caller may use
    "GET /api/skills",
    "GET /api/skills/*",
    "POST /api/skill/*/stream",
    # Its own chat threads (owner/collaborator-checked by the routes), so it can
    # read what a supervisor replied in a shared thread.
    "GET /api/sessions",
    "GET /api/sessions/*",
)


def machine_route_allowed(principal: dict, method: str, path: str) -> bool:
    """Whether ``METHOD path`` matches one of the principal's route globs."""
    routes = principal.get("routes") or DEFAULT_MACHINE_ROUTES
    call = f"{method.upper()} {path}"
    return any(fnmatchcase(call, pattern) for pattern in routes)


def is_tailnet_mode() -> bool:
    """True when ``AUTH_MODE=tailnet``."""
    return os.environ.get("AUTH_MODE", "").strip().lower() == AUTH_MODE


def _load_principals(env_name: str) -> dict[str, dict]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{env_name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(v, dict) and v.get("uid") for v in data.values()):
        raise RuntimeError(f'{env_name} must map each key to an object with a "uid"')
    return data


@lru_cache(maxsize=1)
def _config() -> tuple[str, dict[str, dict], dict[str, dict]]:
    """(proxy secret, tailnet principals keyed by lower-cased login, machine
    principals keyed by lower-cased sha256 hex). Cached — env is read once."""
    secret = os.environ.get("TAILNET_PROXY_SECRET", "")
    tailnet = {k.strip().lower(): v for k, v in _load_principals("TAILNET_PRINCIPALS").items()}
    machine = {k.strip().lower(): v for k, v in _load_principals("MACHINE_PRINCIPALS").items()}
    return secret, tailnet, machine


def assert_safe_tailnet_mode() -> None:
    """Refuse to start a misconfigured tailnet mode.

    Raises:
        RuntimeError: on a deployment marker (header trust would be client
            input), a missing/short proxy secret, bad principal JSON, or no
            principals at all (every request would be refused).
    """
    if not is_tailnet_mode():
        return
    offenders = [name for name in _DEPLOY_ENV_ADAERS if os.environ.get(name)]
    if offenders:
        raise RuntimeError(
            f"AUTH_MODE=tailnet with deployment markers {', '.join(offenders)}: the "
            "Tailscale identity header is only trustworthy behind tailscaled on the same machine."
        )
    _config.cache_clear()
    secret, tailnet, machine = _config()
    if len(secret) < 32:
        raise RuntimeError("AUTH_MODE=tailnet needs TAILNET_PROXY_SECRET (>= 32 chars), shared with the frontend proxy")
    if not tailnet and not machine:
        raise RuntimeError("AUTH_MODE=tailnet with no TAILNET_PRINCIPALS or MACHINE_PRINCIPALS would refuse everyone")


def _user(principal: dict, *, email: str, auth_mode: str) -> User:
    return User(
        uid=str(principal["uid"]),
        email=email,
        domain=email.rsplit("@", 1)[1].lower() if "@" in email else "",
        group_tags=frozenset(principal.get("groupTags") or ()),
        auth_mode=auth_mode,
        display_name=str(principal.get("displayName") or ""),
        supervisors=tuple(str(e).strip().lower() for e in principal.get("supervisors") or () if str(e).strip()),
        notify_inbox=str(principal.get("notifyInbox") or ""),
    )


def token_hash(token: str) -> str:
    """sha256 hex of a machine token — the form MACHINE_PRINCIPALS is keyed by."""
    return hashlib.sha256(token.encode()).hexdigest()


async def get_current_user_tailnet(request: Request) -> User:
    """FastAPI auth dependency for ``AUTH_MODE=tailnet``.

    Raises:
        HTTPException(401): no proxy secret and no valid machine token, or the
            proxy vouched for a request that carries no Tailscale login (e.g. a
            tagged device, or a request that didn't come through tailscaled).
        HTTPException(403): a Tailscale login that isn't in TAILNET_PRINCIPALS,
            or a machine calling a route outside its ``routes``.
    """
    secret, tailnet, machine = _config()

    presented = request.headers.get(PROXY_SECRET_HEADER, "")
    if presented:
        if not secret or not hmac.compare_digest(presented.encode(), secret.encode()):
            logger.warning("tailnet auth: rejected bad proxy secret")
            raise HTTPException(status_code=401, detail="Invalid proxy secret")
        login = request.headers.get(LOGIN_HEADER, "").strip().lower()
        if not login:
            raise HTTPException(status_code=401, detail="No Tailscale user login on this request")
        principal = tailnet.get(login)
        if principal is None:
            logger.info("tailnet auth: rejected unknown login %s", login)
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TAILNET_LOGIN_NOT_PERMITTED",
                    "message": f"{login} is not allowed on this deployment.",
                },
            )
        user = _user(principal, email=login, auth_mode=AUTH_MODE)
    else:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[len("Bearer ") :].strip() if auth_header.startswith("Bearer ") else ""
        principal = machine.get(token_hash(token)) if token else None
        if principal is None:
            raise HTTPException(status_code=401, detail="Tailnet mode: use the tailnet URL, or a machine token")
        if not machine_route_allowed(principal, request.method, request.url.path):
            logger.info("tailnet auth: machine %s denied %s %s", principal["uid"], request.method, request.url.path)
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "MACHINE_ROUTE_NOT_PERMITTED",
                    "message": f"{principal['uid']} may not call {request.method} {request.url.path}.",
                },
            )
        email = principal.get("email") or f"{str(principal['uid']).split(':', 1)[-1]}@{MACHINE_EMAIL_DOMAIN}"
        user = _user(principal, email=email.lower(), auth_mode=MACHINE_AUTH_MODE)

    request.state.access = build_access_context(user)
    logger.debug("tailnet auth: granted uid=%s mode=%s", user.uid, user.auth_mode)
    return user


def resolve_user_by_uid_tailnet(uid: str) -> User | None:
    """Rebuild a User from a UID in tailnet mode — the counterpart of
    ``firebase_auth.resolve_user_by_uid``, which reads Firebase and therefore
    can never find a tailnet or machine principal (so tools that re-resolve the
    caller by UID, e.g. ``generate_document``, failed closed for every user).

    Searches TAILNET_PRINCIPALS then MACHINE_PRINCIPALS for a matching ``uid``.
    Same identity, tags and email as at request time. None if unknown.
    """
    if not uid:
        return None
    _secret, tailnet, machine = _config()
    for login, principal in tailnet.items():
        if str(principal.get("uid")) == uid:
            return _user(principal, email=login, auth_mode=AUTH_MODE)
    for principal in machine.values():
        if str(principal.get("uid")) == uid:
            email = principal.get("email") or f"{uid.split(':', 1)[-1]}@{MACHINE_EMAIL_DOMAIN}"
            return _user(principal, email=email.lower(), auth_mode=MACHINE_AUTH_MODE)
    return None


def check_machine_access_control(user: User, access_control: dict | None) -> None:
    """403 a machine that tries to give a skill wider access than itself.

    Allowed for machines: ``private`` (the default when omitted), or ``tagged``
    with a non-empty subset of the machine's own tags. Anything else —
    ``public``, ``domain``, ``specific``, or someone else's tag — would let an
    agent publish into other people's view, which is the operator's decision.
    Humans are unaffected.
    """
    if user.auth_mode != MACHINE_AUTH_MODE or not access_control:
        return
    kind = access_control.get("type")
    tags = set(access_control.get("tags") or ())
    if kind == "private" or (kind == "tagged" and tags and tags <= set(user.group_tags)):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "MACHINE_ACCESS_CONTROL_NOT_PERMITTED",
            "message": (
                f"{user.uid} may only create skills that are private or tagged with its own tags "
                f"({', '.join(sorted(user.group_tags)) or 'none'}). Ask an operator to widen access."
            ),
        },
    )


__all__ = [
    "AUTH_MODE",
    "DEFAULT_MACHINE_ROUTES",
    "LOGIN_HEADER",
    "MACHINE_AUTH_MODE",
    "PROXY_SECRET_HEADER",
    "assert_safe_tailnet_mode",
    "check_machine_access_control",
    "get_current_user_tailnet",
    "is_tailnet_mode",
    "machine_route_allowed",
    "resolve_user_by_uid_tailnet",
    "token_hash",
]
