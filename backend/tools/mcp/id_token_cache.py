"""Cached Google ID tokens for calling IAM-gated Cloud Run services.

**The cache is a correctness requirement, not a latency optimisation.** That is
easy to misread as premature optimisation and delete, so the reason is here
rather than in a commit message:

ADK's `MCPSessionManager` keys its pooled MCP sessions on an MD5 of the merged
request headers (`mcp_session_manager.py:279-282`). If the `Authorization`
header carries a freshly minted JWT, the header set differs on every call, the
hash differs on every call, and session pooling collapses to **one MCP session
per tool call** — a new HTTP session, a new handshake, a new browser context,
every time. Caching the token for its lifetime keeps the hash stable for an
hour, and pooling behaves as designed.

There is a second reason. ADK invokes `header_provider` **synchronously**
(`mcp_tool.py:361-364`), so a blocking `google.auth` refresh would run on the
event loop that is also serving the chat stream. With the cache warm the
provider is a dict lookup; refreshes happen off the hot path.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# Google ID tokens last an hour. Refresh early so a call never races expiry, but
# not so early that the token — and therefore the session-pool key — churns.
_REFRESH_MARGIN_SECONDS = 600.0
_DEFAULT_LIFETIME_SECONDS = 3600.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, str]] = {}


class IdTokenUnavailable(RuntimeError):
    """No ID token could be minted for this audience."""


def _mint(audience: str) -> str:
    """Mint an ID token for `audience` using ambient credentials.

    On Cloud Run this is the runtime service account via the metadata server;
    locally it is whatever ADC provides.
    """
    import google.auth.transport.requests
    from google.oauth2 import id_token as google_id_token

    request = google.auth.transport.requests.Request()
    return google_id_token.fetch_id_token(request, audience)


def get_id_token(audience: str, *, minter=None, now=None) -> str:
    """Return a cached ID token for `audience`, minting one if needed.

    Args:
        audience: the Cloud Run service base URL the token is scoped to.
        minter: override for tests.
        now: override for tests.

    Raises:
        IdTokenUnavailable: if no token could be obtained. Deliberately loud —
            a silent empty Authorization header would surface as an opaque 403
            from Cloud Run's edge, which is a much worse thing to debug.
    """
    clock = now or time.monotonic
    mint = minter or _mint
    current = clock()

    with _lock:
        entry = _cache.get(audience)
        if entry is not None and entry[0] > current:
            return entry[1]

    # Minted outside the lock: it is network I/O, and holding a global lock
    # across it would serialise every MCP call in the process.
    try:
        token = mint(audience)
    except Exception as exc:
        raise IdTokenUnavailable(f"Could not mint an ID token for {audience}: {exc}") from exc

    if not token:
        raise IdTokenUnavailable(f"Empty ID token returned for {audience}")

    expires_at = current + _DEFAULT_LIFETIME_SECONDS - _REFRESH_MARGIN_SECONDS
    with _lock:
        _cache[audience] = (expires_at, token)

    log.debug("mcp_auth: minted ID token for %s, cached for %.0fs", audience, expires_at - current)
    return token


def clear_cache() -> None:
    """Drop every cached token (tests, and after a credential change)."""
    with _lock:
        _cache.clear()
