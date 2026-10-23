"""URL processing tool — fetch and extract text from a URL.

Used by non-Gemini skill agents (Claude, OpenAI) as a FunctionTool.
Gemini skill agents use the ADK built-in url_context grounding tool instead.

Security: blocks file://, private/internal IPs, and localhost before fetching —
checking BOTH the URL literal and every address the hostname resolves to.

Why the resolution check exists (v6.39.0). The literal check alone is a
DNS-rebinding hole: `_validate_url` saw a domain name, said "not an IP, fine",
and the fetch went wherever DNS pointed. On Cloud Run the payoff is
`169.254.169.254` — the metadata server hands back a service-account token in
the response body, which this tool then returns to the model as page text.
Obscura's own guard (obscura-net/src/client.rs) resolves and re-checks for
exactly this reason; this brings the incumbent tool to the same bar.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

# Ranges Python's own address flags do NOT cover but we still refuse.
# 100.64.0.0/10 is RFC 6598 carrier-grade NAT — `is_private` returns False for
# it, and GCP uses it for managed-service endpoints, so it is reachable and
# interesting from inside a VPC.
_EXTRA_DENY_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),
]

_LOCALHOST_RE = re.compile(r"^(localhost|.*\.local)$", re.IGNORECASE)

# Module-level so tests can patch it without monkeypatching the socket module.
_default_resolver = socket.getaddrinfo

# We follow redirects ourselves rather than letting requests do it, so each hop
# can be re-validated. Five is generous for legitimate chains (http->https->www
# ->locale is four) and short enough that a loop ends quickly.
_MAX_REDIRECTS = 5
_FETCH_TIMEOUT_SECONDS = 20.0
# Enough for any article; small enough that a hostile response cannot exhaust
# memory. Cheaper than trusting Content-Length, which is attacker-controlled.
_MAX_BYTES = 2_000_000
# A bare "python-requests/2.x" is 403'd by a good number of sites, which reads
# as "the page is empty" rather than "we were refused".
_USER_AGENT = "Mozilla/5.0 (compatible; AitanaBot/1.0; +https://assistant.example.com/bot)"


def _is_forbidden_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when `addr` must never be the target of an outbound fetch.

    Leans on Python's address flags rather than a hand-maintained CIDR list:
    `is_private` alone already covers RFC-1918, loopback, link-local (including
    169.254.169.254), 0.0.0.0/8, 192.0.0.0/24, 198.18.0.0/15, IPv6 ULA
    (fc00::/7) and IPv6 link-local (fe80::/10) — several of which the previous
    hand-rolled list missed.

    v4-mapped IPv6 (`::ffff:a.b.c.d`) is unwrapped and re-checked so
    `[::ffff:169.254.169.254]` cannot slip past the v6 arm.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return _is_forbidden_ip(mapped)

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True

    return any(addr in network for network in _EXTRA_DENY_NETWORKS)


def _validate_url(url: str) -> None:
    """Raise ValueError if the URL *literal* is not safe to fetch.

    Blocks:
      - Non-HTTP(S) schemes (file://, ftp://, etc.)
      - Private, loopback, link-local, reserved and CGNAT IP literals
      - localhost / .local hostnames

    This is the cheap check and does no I/O. A hostname that is not an IP
    literal passes here and is caught by `_assert_resolved_public`.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme {parsed.scheme!r} is not allowed. Only http:// and https:// are permitted.")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("URL has no hostname.")

    if _LOCALHOST_RE.match(hostname):
        raise ValueError(f"URL hostname {hostname!r} is not allowed (localhost/local).")

    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # Not an IP literal — it is a domain name. The resolution check owns it.
        return

    if _is_forbidden_ip(addr):
        raise ValueError(f"URL {url!r} points at a private/internal IP address ({addr}) which is not allowed.")


def _assert_resolved_public(hostname: str, resolver=None) -> None:
    """Raise ValueError unless EVERY address `hostname` resolves to is public.

    This is the DNS-rebinding guard. Refusing on *any* forbidden answer (rather
    than the first, or a random one) is the point: a round-robin record that
    mixes one internal address among public ones would otherwise be a coin flip
    between a safe fetch and a metadata-server read.

    Fails closed — a name that cannot be resolved is refused rather than passed
    through to the fetch, which would only resolve it again anyway.

    Args:
        hostname: the host component of the URL.
        resolver: `socket.getaddrinfo`-compatible callable. Injected by tests so
            the suite never touches real DNS.
    """
    resolve = resolver or _default_resolver
    try:
        infos = resolve(hostname, None, 0, socket.SOCK_STREAM)
    except Exception as exc:
        raise ValueError(f"URL hostname {hostname!r} could not be resolved: {exc}") from exc

    if not infos:
        raise ValueError(f"URL hostname {hostname!r} could not be resolved to any address.")

    for info in infos:
        sockaddr = info[4]
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:  # pragma: no cover - defensive; getaddrinfo returns literals
            continue
        if _is_forbidden_ip(addr):
            raise ValueError(
                f"URL hostname {hostname!r} resolves to a private/internal address ({addr}) which is not allowed."
            )


def _validate_url_with_resolution(url: str) -> None:
    """Literal check, then the resolution check for non-literal hostnames.

    Blocking DNS lives here so callers can push the whole thing into a thread.
    """
    _validate_url(url)

    hostname = urlparse(url).hostname or ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        # A domain name — resolve and re-check.
        _assert_resolved_public(hostname)
    else:
        # An IP literal already cleared by `_validate_url`; no lookup needed.
        return


def _fetch_and_extract(url: str) -> str:
    """Fetch `url`, following redirects safely, and return its visible text.

    Replaces ADK's ``load_web_page``, which sets ``allow_redirects=False`` and so
    returns "Failed to fetch url" for any host that redirects — which is a great
    many of them, ``www.`` prefixes and http->https upgrades included. ADK is
    right to refuse a blind redirect: the guard validated the URL the model
    supplied, not wherever the 301 points, and on Cloud Run the payoff is a
    302 to 169.254.169.254 returning a service-account token as page text.

    So we follow hops ourselves and run the SAME validation on every one. That
    is the trade this module already makes for the first hop; a redirect is
    simply another hop.

    Raises:
        ValueError: a blocked hop, too many redirects, or a non-200 response.
    """
    from bs4 import BeautifulSoup

    current = url
    response = None
    for _ in range(_MAX_REDIRECTS + 1):
        # EVERY hop, not just the first. Removing this line reopens the hole.
        _validate_url_with_resolution(current)
        response = requests.get(
            current,
            allow_redirects=False,
            timeout=_FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
            stream=True,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                raise ValueError(f"{current!r} returned {response.status_code} with no Location header.")
            # A relative Location is legal and common.
            current = urljoin(current, location)
            continue
        break
    else:
        raise ValueError(f"Too many redirects (more than {_MAX_REDIRECTS}) starting at {url!r}.")

    if response.status_code != 200:
        raise ValueError(f"{current!r} returned HTTP {response.status_code}.")

    body = response.raw.read(_MAX_BYTES, decode_content=True)
    soup = BeautifulSoup(body, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    # ADK dropped every line of three words or fewer, which silently discards
    # headlines, table cells and dates — the parts of a page most likely to be
    # the answer. Keep anything with content; drop only blank lines.
    return "\n".join(line for line in text.splitlines() if line.strip())


async def url_processing(url: str) -> str:
    """Fetch and extract text content from a URL.

    Args:
        url: The HTTPS URL to fetch. Must be a public URL — internal IPs,
             localhost, and file:// URLs are blocked for security.

    Returns:
        Extracted text content from the URL, or an error description.
    """
    try:
        # In a thread: the resolution step is blocking I/O and must not sit on
        # the event loop, which is also the chat stream's loop.
        await asyncio.to_thread(_validate_url_with_resolution, url)
    except ValueError as exc:
        return f"Cannot fetch URL: {exc}"

    log.info("url_processing: fetching %s", url)
    try:
        return await asyncio.to_thread(_fetch_and_extract, url)
    except ValueError as exc:
        # Refused for a stated reason — a blocked hop, a redirect loop, a non-200.
        log.info("url_processing: refused %s: %s", url, exc)
        return f"Cannot fetch URL: {exc}"
    except Exception as exc:
        log.warning("url_processing: fetch failed for %s: %s", url, exc)
        return f"Failed to fetch {url}: {exc}"
