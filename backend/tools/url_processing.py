"""URL processing tool — fetch and extract text from a URL.

Used by non-Gemini skill agents (Claude, OpenAI) as a FunctionTool.
Gemini skill agents use the ADK built-in url_context grounding tool instead.

Security: blocks file://, RFC-1918 private IPs, and localhost before fetching.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from urllib.parse import urlparse

from google.adk.tools.load_web_page import load_web_page

log = logging.getLogger(__name__)

# RFC-1918 + loopback + link-local ranges
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

_LOCALHOST_RE = re.compile(r"^(localhost|.*\.local)$", re.IGNORECASE)


def _validate_url(url: str) -> None:
    """Raise ValueError if the URL is not safe to fetch.

    Blocks:
      - Non-HTTP(S) schemes (file://, ftp://, etc.)
      - RFC-1918 private IP ranges
      - Loopback and link-local addresses
      - localhost / .local hostnames
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed. Only http:// and https:// are permitted.")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("URL has no hostname.")

    if _LOCALHOST_RE.match(hostname):
        raise ValueError(f"URL hostname {hostname!r} is not allowed (localhost/local).")

    # Try parsing as IP address — if it fails, it's a domain name (allowed)
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _PRIVATE_NETWORKS:
            if addr in network:
                raise ValueError(
                    f"URL {url!r} resolves to a private/internal IP address ({addr}) which is not allowed."
                )
    except ValueError as exc:
        # Re-raise if it's our own error
        if "private" in str(exc) or "not allowed" in str(exc) or "localhost" in str(exc):
            raise
        # Otherwise it's an ipaddress.ip_address parse error — hostname is a domain, fine


async def url_processing(url: str) -> str:
    """Fetch and extract text content from a URL.

    Args:
        url: The HTTPS URL to fetch. Must be a public URL — internal IPs,
             localhost, and file:// URLs are blocked for security.

    Returns:
        Extracted text content from the URL, or an error description.
    """
    try:
        _validate_url(url)
    except ValueError as exc:
        return f"Cannot fetch URL: {exc}"

    log.info("url_processing: fetching %s", url)
    try:
        result = await asyncio.to_thread(load_web_page, url)
        return result
    except Exception as exc:
        log.warning("url_processing: fetch failed for %s: %s", url, exc)
        return f"Failed to fetch {url}: {exc}"
