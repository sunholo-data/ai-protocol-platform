"""Auth for /api/admin/* endpoints.

Two ways to authenticate:

1. **Service-account ID token** — Google-signed, email in the
   ADMIN_SEED_ALLOWED_SAS env-var allowlist. This is how Cloud Build
   deploy hooks call /api/admin/seed-platform-skills.

2. **Firebase user token with the ``aitana-admin`` group tag** — for
   admin operators running ops scripts (e.g. ``scripts/prewarm_one_ppas.py``)
   from their laptop. The group tag is checked against the JWT
   ``groupTags`` custom claim.

The allowlist + group-tag name live in env / claims (not code) so
rotating either is a Cloud Run env-var update, not a deploy.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from auth.admin_roles import PLATFORM_ADMIN_TAG, is_platform_admin

logger = logging.getLogger(__name__)

# Back-compat alias; the canonical name lives in auth.admin_roles.
ADMIN_GROUP_TAG = PLATFORM_ADMIN_TAG


def _allowed_emails() -> set[str]:
    raw = os.environ.get("ADMIN_SEED_ALLOWED_SAS", "")
    return {email.strip() for email in raw.split(",") if email.strip()}


def _assert_caller_is_service_account(request: Request) -> str:
    """Verify the bearer token belongs to a trusted admin caller.

    Accepts EITHER:
    - A Google-signed ID token whose ``email`` claim is in
      ``ADMIN_SEED_ALLOWED_SAS`` (used by Cloud Build deploy hooks).
    - A Firebase ID token whose ``groupTags`` claim contains
      ``aitana-admin`` (used by ops operators running scripts locally).

    Returns the verified email on success. Raises HTTPException(403)
    on every failure path — we deliberately don't distinguish "bad
    token" from "valid token, wrong principal" to avoid revealing
    which SAs / users are admins.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Not authorized")

    token = header[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Path 1 — Google-signed ID token (SA allowlist)
    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request())
    except Exception:
        claims = None

    if claims is not None:
        email = claims.get("email", "")
        if email and claims.get("email_verified"):
            if email in _allowed_emails():
                return email

    # Path 2 — Firebase user token with aitana-admin group tag
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app()

        decoded = firebase_auth.verify_id_token(token)
        group_tags = decoded.get("groupTags") or []
        if is_platform_admin(group_tags):
            user_email = decoded.get("email", decoded.get("uid", "(unknown)"))
            logger.info("admin_auth: firebase user %s (group %s)", user_email, ADMIN_GROUP_TAG)
            return user_email
    except Exception as exc:
        logger.debug("admin_auth: firebase verify failed: %s", exc)

    raise HTTPException(status_code=403, detail="Not authorized")
