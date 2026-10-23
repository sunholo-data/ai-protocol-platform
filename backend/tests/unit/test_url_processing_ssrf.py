"""SSRF regression tests for tools/url_processing.py — the DNS-rebinding gap.

`_validate_url` only ever inspected the URL *literal*, so a hostname that
resolves to a private address passed it. The classic consequence on Cloud Run is
a name pointing at 169.254.169.254: the guard says "that's a domain, fine", the
fetch goes to the metadata server, and the service-account token comes back in
the page body.

These tests pin the resolved-address check. The resolver is injected so the
suite stays hermetic — no test here touches real DNS.

Companion to tests/tool_tests/test_url_processing.py, which covers the literal
checks and is deliberately left untouched: `_validate_url` still does exactly
what it did, and the new resolution check is a separate function layered on top.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest


def _resolver_returning(*addresses: str):
    """Build a getaddrinfo stub that resolves any host to `addresses`.

    Mirrors the real 5-tuple shape: (family, type, proto, canonname, sockaddr),
    where sockaddr is (ip, port) for v4 and (ip, port, flow, scope) for v6.
    """

    def _fake(host, port, *args, **kwargs):
        out = []
        for addr in addresses:
            if ":" in addr:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, port or 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 0)))
        return out

    return _fake


class TestResolvedAddressGuard:
    """The rebinding fix: every resolved address is checked, not just the literal."""

    def test_hostname_resolving_to_metadata_endpoint_is_refused(self):
        """The one that matters on Cloud Run: a public name pointing at the
        metadata server must not be fetched."""
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public("evil.example.com", resolver=_resolver_returning("169.254.169.254"))

    def test_hostname_resolving_to_loopback_is_refused(self):
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public("evil.example.com", resolver=_resolver_returning("127.0.0.1"))

    def test_hostname_resolving_to_rfc1918_is_refused(self):
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public("evil.example.com", resolver=_resolver_returning("10.1.2.3"))

    def test_v4_mapped_v6_metadata_address_is_refused(self):
        """`::ffff:169.254.169.254` must not slip past the v6 arm — the bypass
        Obscura's own guard calls out explicitly."""
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public("evil.example.com", resolver=_resolver_returning("::ffff:169.254.169.254"))

    def test_ipv6_link_local_is_refused(self):
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public("evil.example.com", resolver=_resolver_returning("fe80::1"))

    def test_any_private_address_in_the_set_refuses_the_whole_host(self):
        """A round-robin record that mixes one private answer among public ones
        must refuse — checking only the first answer is the bug, not the fix."""
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="resolves to"):
            _assert_resolved_public(
                "mixed.example.com",
                resolver=_resolver_returning("93.184.216.34", "169.254.169.254"),
            )

    def test_public_addresses_pass(self):
        from tools.url_processing import _assert_resolved_public

        _assert_resolved_public("example.com", resolver=_resolver_returning("93.184.216.34", "2606:2800:220:1::1"))

    def test_resolution_failure_fails_closed(self):
        """A host we cannot resolve is refused, not waved through."""
        from tools.url_processing import _assert_resolved_public

        def _boom(*args, **kwargs):
            raise socket.gaierror("Name or service not known")

        with pytest.raises(ValueError, match="could not be resolved"):
            _assert_resolved_public("nope.example.com", resolver=_boom)

    def test_empty_resolution_fails_closed(self):
        from tools.url_processing import _assert_resolved_public

        with pytest.raises(ValueError, match="could not be resolved"):
            _assert_resolved_public("nope.example.com", resolver=lambda *a, **k: [])


class TestLiteralDenySetGaps:
    """Ranges the hand-rolled `_PRIVATE_NETWORKS` list missed."""

    def test_cgnat_range_blocked(self):
        """100.64.0.0/10 is not RFC-1918 and Python's `is_private` says False,
        but GCP uses it for managed service endpoints."""
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://100.64.0.1/")

    def test_ipv6_link_local_literal_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://[fe80::1]/")

    def test_v4_mapped_v6_literal_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://[::ffff:169.254.169.254]/")

    def test_unspecified_address_blocked(self):
        """0.0.0.0 is routed to localhost by the OS."""
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://0.0.0.0/")


@pytest.mark.asyncio
class TestIntegratedPath:
    """`url_processing` must run the resolution check before fetching."""

    async def test_rebinding_host_never_reaches_the_fetch(self):
        from tools import url_processing as mod

        with (
            patch.object(mod, "_default_resolver", _resolver_returning("169.254.169.254")),
            patch.object(mod, "_fetch_and_extract", return_value="SHOULD NOT HAPPEN") as mock_fetch,
        ):
            result = await mod.url_processing("https://evil.example.com/")

        mock_fetch.assert_not_called()
        assert "Cannot fetch URL" in result
        assert "resolves to" in result

    async def test_public_host_still_fetches(self):
        from tools import url_processing as mod

        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "_fetch_and_extract", return_value="page content") as mock_fetch,
        ):
            result = await mod.url_processing("https://example.com/")

        mock_fetch.assert_called_once_with("https://example.com/")
        assert result == "page content"

    async def test_ip_literal_skips_resolution(self):
        """A public IP literal needs no DNS round trip."""
        from tools import url_processing as mod

        called = {"n": 0}

        def _counting(*args, **kwargs):
            called["n"] += 1
            return _resolver_returning("93.184.216.34")(*args, **kwargs)

        with (
            patch.object(mod, "_default_resolver", _counting),
            patch.object(mod, "_fetch_and_extract", return_value="ok"),
        ):
            result = await mod.url_processing("https://93.184.216.34/")

        assert result == "ok"
        assert called["n"] == 0
