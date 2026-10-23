"""Send one Web Push message. Never raises — a notification is a courtesy, and
the thing it points at (the reply, the shared thread) is already saved.

Config (env):
    VAPID_PUBLIC_KEY   base64url uncompressed P-256 point (the browser's
                       applicationServerKey)
    VAPID_PRIVATE_KEY  base64url raw 32-byte private key (or DER)
    VAPID_SUBJECT      mailto: or https: contact for the push services
Any unset → push is off. ``scripts/generate_vapid_keys.py`` prints a pair.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Literal

logger = logging.getLogger(__name__)

SendResult = Literal["ok", "gone", "error", "off"]


def push_config() -> tuple[bool, str | None]:
    """(enabled, public key) — what the browser needs to subscribe."""
    pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    enabled = bool(pub and priv and subject)
    return enabled, (pub if enabled else None)


def _send_sync(subscription: dict[str, Any], payload: dict[str, str]) -> SendResult:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": subscription["endpoint"], "keys": subscription.get("keys") or {}},
            data=json.dumps(payload),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"].strip(),
            vapid_claims={"sub": os.environ["VAPID_SUBJECT"].strip()},
            ttl=3600,
            timeout=10,
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            # The browser dropped this subscription (app removed, permission
            # revoked) — the caller prunes it.
            return "gone"
        logger.warning("web push failed (%s): %s", status, exc)
        return "error"
    except Exception:  # never let a notification fail a turn
        logger.exception("web push failed")
        return "error"


async def send(subscription: dict[str, Any], payload: dict[str, str]) -> SendResult:
    if not push_config()[0]:
        return "off"
    return await asyncio.to_thread(_send_sync, subscription, payload)
