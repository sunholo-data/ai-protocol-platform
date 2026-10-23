"""Who may use the browser. The policy decision point.

Policy lives here rather than in the browser service for a reason that is
architectural, not convenient: that service runs untrusted page JavaScript, and
the whole justification for running it at all is that its service account holds
**zero** project roles. Zero roles includes no `datastore.user`, so it cannot
read this document — an allowlist implemented there could only fail closed, and
did, denying every user on the first deployed revision.

The backend already has the identity context and the Firestore access, and is
the only principal Cloud Run IAM lets near the browser. So it decides, and the
browser service trusts the caller it cannot help but trust.

Deny-by-default, and fails closed if Firestore is unreachable: an outage must
not silently open a browser to everyone.

Seeded by `scripts/seed-browser-allowlist.sh`, which grants access without a
redeploy — per docs/ops/env-config-parity.md, per-env values a code merge does
not carry are a recurring source of production drift, and per the admin-config
convention this collection survives a deploy.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_COLLECTION = "admin_config"
_DOCUMENT = "browser_allowlist"

# Short enough that a grant is felt quickly, long enough that a tool call is a
# dict lookup rather than a Firestore round trip. The MCP `header_provider` seam
# that consumes this is SYNCHRONOUS, so a blocking read there would sit on the
# event loop serving the chat stream.
_CACHE_TTL_SECONDS = 60.0

_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


class NotAllowed(PermissionError):
    """This user may not use the browser. Carries a message fit to show them."""


_DENIED_MESSAGE = (
    "The browser is not enabled for your account yet. Ask an administrator to add you to the browser allowlist."
)


def _load(now=None) -> dict:
    """Read the allowlist document, with a TTL cache. Fails closed."""
    global _cache
    clock = now or time.monotonic
    current = clock()

    with _lock:
        if _cache is not None and (current - _cache[0]) < _CACHE_TTL_SECONDS:
            return _cache[1]

    config: dict = {}
    try:
        from google.cloud import firestore

        client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None)
        snap = client.collection(_COLLECTION).document(_DOCUMENT).get()
        if snap.exists:
            config = snap.to_dict() or {}
    except Exception as exc:
        # Fail CLOSED. An unreachable Firestore denies everyone, which is the
        # safe direction, and the caller renders a visible refusal rather than
        # hanging or silently succeeding.
        log.error("browser_allowlist: could not read %s/%s, denying all: %s", _COLLECTION, _DOCUMENT, exc)
        config = {}

    with _lock:
        _cache = (current, config)
    return config


def clear_cache() -> None:
    """Drop the cached document (tests, and after an admin edit)."""
    global _cache
    with _lock:
        _cache = None


def is_allowed(user_id: str, config: dict | None = None) -> bool:
    """True if `user_id` may use the browser. Deny-by-default."""
    try:
        assert_allowed(user_id, config=config)
    except NotAllowed:
        return False
    return True


def assert_allowed(user_id: str, config: dict | None = None) -> None:
    """Raise NotAllowed unless `user_id` may use the browser.

    `enabled: false` on the document disables the browser for everyone, which is
    the fastest kill switch that does not need a deploy.
    """
    cfg = _load() if config is None else config

    if not cfg:
        raise NotAllowed(_DENIED_MESSAGE)

    if cfg.get("enabled") is False:
        raise NotAllowed(cfg.get("disabled_message") or "The browser is temporarily disabled for all users.")

    user_id = (user_id or "").strip()
    if not user_id:
        raise NotAllowed(_DENIED_MESSAGE)

    if user_id in (cfg.get("users") or []):
        return

    # Domain grants let an operator enable a whole tenant without listing people.
    if "@" in user_id:
        domain = user_id.rsplit("@", 1)[1].lower()
        if domain in {d.lower().lstrip("@") for d in (cfg.get("domains") or [])}:
            return

    raise NotAllowed(_DENIED_MESSAGE)


def allowed_identities(user_id: str, config: dict | None = None) -> list[str]:
    """Identities `user_id` may act through (Phase 2 Mode A).

    Empty means anonymous browsing only, which is all Phase 1 supports.
    """
    cfg = _load() if config is None else config
    per_user = (cfg.get("identities") or {}).get(user_id)
    if per_user is not None:
        return list(per_user)
    return list(cfg.get("default_identities") or [])
