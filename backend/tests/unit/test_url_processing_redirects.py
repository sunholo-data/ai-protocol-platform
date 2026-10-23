"""Redirects, followed safely — the other half of the 2026-09-04 bs4 fix.

ADK's `load_web_page` sets `allow_redirects=False`, and that is not an
oversight: following a redirect blindly is an SSRF hole, because the guard
validated the URL the model supplied and not wherever the 301 points. The cost
is that any host which redirects returns "Failed to fetch url" — and plenty do
(`www.example.org` -> `example.org` is the ordinary case, not the exotic one).

So we follow them and re-validate EVERY hop, which is the trade the module's
DNS-rebinding guard already makes for the first one. These tests pin both
halves: that a redirect is followed at all, and that a redirect into private
space is refused at the hop where it happens rather than at the start.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from tools import url_processing as mod


def _resolver_returning(*addresses: str):
    def _fake(host, port, *args, **kwargs):
        out = []
        for addr in addresses:
            if ":" in addr:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addr, port or 0, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 0)))
        return out

    return _fake


def _per_host_resolver(mapping: dict[str, str], default: str = "93.184.216.34"):
    """Resolve each hostname to its own address, so a redirect can change fate."""

    def _fake(host, port, *args, **kwargs):
        return _resolver_returning(mapping.get(host, default))(host, port, *args, **kwargs)

    return _fake


def _response(status: int, *, body: bytes = b"", location: str | None = None, ctype: str = "text/html"):
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": ctype}
    if location:
        r.headers["Location"] = location
    r.is_redirect = status in (301, 302, 303, 307, 308)
    r.is_permanent_redirect = status in (301, 308)
    r.content = body
    r.raw.read.return_value = body
    return r


_PAGE = b"<html><body><h1>The Price Intelligence Platform</h1><p>Some real sentence with several words in it.</p></body></html>"


@pytest.mark.asyncio
class TestRedirectsAreFollowed:
    async def test_a_single_redirect_is_followed(self):
        """The ordinary case ADK's allow_redirects=False turned into a failure."""
        responses = [
            _response(301, location="https://example.com/"),
            _response(200, body=_PAGE),
        ]
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.side_effect = responses
            result = await mod.url_processing("https://www.example.com")

        assert "Price Intelligence Platform" in result
        assert req.get.call_count == 2

    async def test_a_relative_location_is_resolved_against_the_current_url(self):
        responses = [
            _response(302, location="/landing"),
            _response(200, body=_PAGE),
        ]
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.side_effect = responses
            await mod.url_processing("https://example.com/start")

        assert req.get.call_args_list[1].args[0] == "https://example.com/landing"

    async def test_a_redirect_loop_is_refused_rather_than_followed_forever(self):
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.return_value = _response(301, location="https://example.com/again")
            result = await mod.url_processing("https://example.com/")

        assert "redirect" in result.lower()
        assert req.get.call_count <= mod._MAX_REDIRECTS + 1

    async def test_a_redirect_with_no_location_is_reported(self):
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.return_value = _response(301)
            result = await mod.url_processing("https://example.com/")
        assert "Location" in result or "location" in result


@pytest.mark.asyncio
class TestEveryHopIsRevalidated:
    """The reason ADK refuses redirects at all. Do not weaken these."""

    async def test_a_redirect_to_the_metadata_server_is_refused(self):
        """The Cloud Run payoff: 169.254.169.254 hands back an SA token."""
        with (
            patch.object(
                mod,
                "_default_resolver",
                _per_host_resolver({"metadata.evil.example": "169.254.169.254"}),
            ),
            patch.object(mod, "requests") as req,
        ):
            req.get.side_effect = [
                _response(302, location="http://metadata.evil.example/computeMetadata/v1/"),
                _response(200, body=b"SHOULD NOT BE REACHED"),
            ]
            result = await mod.url_processing("https://example.com/")

        assert "Cannot fetch URL" in result or "private" in result
        assert req.get.call_count == 1, "the second hop must never be requested"

    async def test_a_redirect_to_rfc1918_is_refused(self):
        with (
            patch.object(mod, "_default_resolver", _per_host_resolver({"internal.evil.example": "10.0.0.5"})),
            patch.object(mod, "requests") as req,
        ):
            req.get.side_effect = [
                _response(301, location="http://internal.evil.example/admin"),
                _response(200, body=b"SHOULD NOT BE REACHED"),
            ]
            result = await mod.url_processing("https://example.com/")

        assert req.get.call_count == 1, "the second hop must never be requested"
        assert "Cannot fetch URL" in result or "private" in result

    async def test_a_redirect_to_a_non_http_scheme_is_refused(self):
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.side_effect = [_response(302, location="file:///etc/passwd")]
            result = await mod.url_processing("https://example.com/")

        assert "scheme" in result.lower() or "Cannot fetch" in result
        assert req.get.call_count == 1


@pytest.mark.asyncio
class TestFetchHygiene:
    async def test_a_real_user_agent_and_a_timeout_are_sent(self):
        """No timeout parks a thread on the chat loop's executor indefinitely."""
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.return_value = _response(200, body=_PAGE)
            await mod.url_processing("https://example.com/")

        kwargs = req.get.call_args.kwargs
        assert kwargs["timeout"] > 0
        assert "python-requests" not in kwargs["headers"]["User-Agent"]
        assert kwargs["allow_redirects"] is False, "we follow hops ourselves, after validating each"

    async def test_a_non_200_is_reported_with_its_status(self):
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.return_value = _response(404)
            result = await mod.url_processing("https://example.com/")
        assert "404" in result

    async def test_extraction_returns_page_text(self):
        with (
            patch.object(mod, "_default_resolver", _resolver_returning("93.184.216.34")),
            patch.object(mod, "requests") as req,
        ):
            req.get.return_value = _response(200, body=_PAGE)
            result = await mod.url_processing("https://example.com/")
        assert "several words" in result
