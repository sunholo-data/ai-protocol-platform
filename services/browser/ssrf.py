"""Resolution pre-check, so a refusal can say WHY.

Obscura has its own SSRF guard and it works — verified 2026-09-02 by serving a
canary on 127.0.0.1 and confirming Obscura would not return it while curl would.
But its resolver-level refusal surfaces as a generic "error sending request":
the guard returns Err and reqwest flattens the reason away, so "we refused to
touch your internal network" and "that site is down" are indistinguishable.

CLAUDE.md #8 says every interaction gets a visible, meaningful terminal state.
So the supervisor resolves first and produces the reason itself. Obscura's guard
stays underneath as defence in depth — this one exists to be legible, and it is
deliberately NOT the only thing standing between a page and the metadata server.

Mirrors backend/tools/url_processing.py; kept as a copy rather than an import
because this service must not depend on the backend package.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# 100.64.0.0/10 (RFC 6598 CGNAT) — Python's is_private says False, GCP uses it
# for managed-service endpoints.
_EXTRA_DENY_NETWORKS = [ipaddress.ip_network("100.64.0.0/10")]

_default_resolver = socket.getaddrinfo


class BlockedUrl(ValueError):
    """A URL we refuse to fetch, with a reason fit to show a person."""


def is_forbidden_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True when `addr` must never be an outbound target.

    v4-mapped IPv6 is unwrapped first so `::ffff:169.254.169.254` cannot slip
    past the v6 arm.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return is_forbidden_ip(mapped)

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return True

    return any(addr in net for net in _EXTRA_DENY_NETWORKS)


def assert_fetchable(url: str, resolver=None) -> None:
    """Raise BlockedUrl unless `url` is safe to fetch, with a legible reason.

    Checks scheme, then the host literal, then every address the host resolves
    to. Refuses if ANY resolved address is forbidden — a round-robin record
    mixing one internal answer among public ones would otherwise be a coin flip.

    Fails closed: an unresolvable host is refused rather than passed to Obscura,
    which would only fail to resolve it again with a worse message.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise BlockedUrl(
            f"Only http:// and https:// can be browsed — {parsed.scheme or 'that'}:// is not allowed."
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise BlockedUrl("That URL has no hostname.")

    if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
        raise BlockedUrl(f"{hostname!r} is a local address; the browser only reaches the public internet.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if is_forbidden_ip(literal):
            raise BlockedUrl(
                f"{hostname} is a private or internal address; the browser only reaches the public internet."
            )
        return  # a public IP literal needs no lookup

    resolve = resolver or _default_resolver
    try:
        infos = resolve(hostname, None, 0, socket.SOCK_STREAM)
    except Exception as exc:
        raise BlockedUrl(f"Could not resolve {hostname!r}: {exc}") from exc

    if not infos:
        raise BlockedUrl(f"Could not resolve {hostname!r} to any address.")

    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:  # pragma: no cover - getaddrinfo returns literals
            continue
        if is_forbidden_ip(addr):
            raise BlockedUrl(
                f"{hostname!r} resolves to {addr}, a private or internal address. "
                "The browser only reaches the public internet."
            )
