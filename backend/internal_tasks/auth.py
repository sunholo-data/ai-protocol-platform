"""OIDC gate for /internal/tasks/* — only the Cloud Tasks queue gets in.

Trust chain (dev infra live 2026-08-10, `compaction_tasks.tf` in
multivac-aitana): the queue stamps each delivery with an ID token minted as
`platform-tasks@…` — a dedicated SA with ZERO project roles whose only power
is being impersonable by the backend runtime SA at enqueue time. So a
Google-signed token with that email and this route's audience can only have
come through the queue; nothing else may actAs it.

Same gate shape as ``admin.auth`` (SEC-1), with two differences: the audience
is enforced (Cloud Tasks sets it to the target URL), and there is no Firebase
fallback — humans use the admin route, tasks use this one.

Fail-closed: missing configuration (either env var) rejects everything. The
403-for-everything policy mirrors admin.auth — don't reveal which principal or
audience would have been accepted.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

logger = logging.getLogger(__name__)

_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


def _expected_sa() -> str:
    return os.environ.get("COMPACTION_TASKS_OIDC_SA", "").strip()


def _expected_audience() -> str:
    return os.environ.get("COMPACTION_TASKS_TARGET_URL", "").strip()


def assert_caller_is_task_queue(request: Request) -> str:
    """Verify the bearer token is the queue's OIDC identity. Returns the email.

    Raises HTTPException(403) on every failure path, uniformly.
    """
    expected_sa = _expected_sa()
    expected_aud = _expected_audience()
    if not expected_sa or not expected_aud:
        # Unconfigured = closed. Never fall back to "any Google-signed token".
        logger.warning("internal_tasks: OIDC gate unconfigured (SA or audience env missing); rejecting")
        raise HTTPException(status_code=403, detail="Not authorized")

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Not authorized")
    token = header[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # audience= makes verify check the `aud` claim — without it a token
        # minted for ANY audience would pass, so it is not optional here.
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience=expected_aud)
    except Exception:
        raise HTTPException(status_code=403, detail="Not authorized") from None

    if claims.get("iss") in _GOOGLE_ISSUERS and claims.get("email_verified") and claims.get("email", "") == expected_sa:
        return claims["email"]

    raise HTTPException(status_code=403, detail="Not authorized")
