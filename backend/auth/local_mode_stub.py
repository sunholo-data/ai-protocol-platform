"""LOCAL_MODE auth stub — drop-in replacement for ``get_current_user``.

Only activates when ``LOCAL_MODE=1``. Returns a fixed workshop identity
when the request carries the exact stub token, rejects everything else.

Security mitigations layered around this stub:
1. The token must equal ``local-mode-stub-token`` exactly — any other
   bearer is rejected. Prevents a misconfigured LOCAL_MODE backend from
   accidentally accepting real Firebase tokens.
2. ``config/local_mode.py:assert_safe_local_mode()`` refuses to start the
   backend if LOCAL_MODE is paired with K_SERVICE / GAE_ENV / KUBERNETES
   markers (Cloud Run / App Engine / GKE).
3. The visible banner mounted by the frontend in LOCAL_MODE makes the
   stubbed state obvious to anyone using the system.

See ``docs/design/v6.1.0/local-mode-and-workshop-readiness.md`` §313
"Security Considerations" for the full rationale.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from auth.access_context import build_access_context
from auth.firebase_auth import User

logger = logging.getLogger(__name__)

STUB_TOKEN = "local-mode-stub-token"

WORKSHOP_USER_UID = "workshop-user"
WORKSHOP_USER_EMAIL = "workshop@local"
WORKSHOP_USER_DOMAIN = "local"
WORKSHOP_USER_GROUP_TAGS = frozenset({"workshop-attendee"})


def build_workshop_user() -> User:
    """Return the deterministic workshop user. Kept as a function so callers
    that mock it for tests have a single seam.
    """
    return User(
        uid=WORKSHOP_USER_UID,
        email=WORKSHOP_USER_EMAIL,
        domain=WORKSHOP_USER_DOMAIN,
        group_tags=WORKSHOP_USER_GROUP_TAGS,
    )


async def get_current_user_local_mode(request: Request) -> User:
    """FastAPI dependency: LOCAL_MODE equivalent of ``get_current_user``.

    Accepts only the stub token. The shape of the parsing is identical to
    the production dep so callers can swap one for the other transparently.

    Raises:
        HTTPException(401): missing header, malformed header, or token does
            not match the stub.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if token != STUB_TOKEN:
        logger.info("local_mode auth: rejected non-stub token")
        raise HTTPException(status_code=401, detail="LOCAL_MODE requires stub token")

    user = build_workshop_user()
    request.state.access = build_access_context(user)
    logger.debug("local_mode auth: granted uid=%s", user.uid)
    return user


__all__ = [
    "STUB_TOKEN",
    "WORKSHOP_USER_EMAIL",
    "WORKSHOP_USER_UID",
    "build_workshop_user",
    "get_current_user_local_mode",
]
