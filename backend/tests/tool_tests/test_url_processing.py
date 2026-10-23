"""Tests for tools/url_processing.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestValidateUrl:
    def test_file_scheme_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("ftp://example.com/file")

    def test_https_allowed(self):
        from tools.url_processing import _validate_url

        _validate_url("https://example.com/page")  # should not raise

    def test_http_allowed(self):
        from tools.url_processing import _validate_url

        _validate_url("http://example.com/page")  # should not raise

    def test_rfc1918_10_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://10.0.0.1/admin")

    def test_rfc1918_172_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://172.16.5.10/secret")

    def test_rfc1918_192_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://192.168.1.1/router")

    def test_localhost_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="localhost"):
            _validate_url("http://localhost:8080/api")

    def test_loopback_ip_blocked(self):
        from tools.url_processing import _validate_url

        with pytest.raises(ValueError, match="private"):
            _validate_url("http://127.0.0.1/internal")

    def test_public_domain_allowed(self):
        from tools.url_processing import _validate_url

        _validate_url("https://docs.python.org/3/library/os.html")  # should not raise


@pytest.mark.asyncio
async def test_url_processing_delegates_to_the_fetcher():
    """Happy path: a valid URL reaches the fetch.

    Retargeted 2026-09-04 from ADK's `load_web_page` to our own
    `_fetch_and_extract`, which follows redirects and re-validates every hop.
    The assertion is unchanged in meaning: a public URL gets fetched, and the
    fetcher's output is what the tool returns.
    """
    from tools.url_processing import url_processing

    with patch("tools.url_processing._fetch_and_extract", return_value="page content") as mock_fetch:
        result = await url_processing("https://example.com")
    mock_fetch.assert_called_once_with("https://example.com")
    assert result == "page content"


@pytest.mark.asyncio
async def test_url_processing_blocks_private_ip():
    """Private IP returns error string (not exception)."""
    from tools.url_processing import url_processing

    result = await url_processing("http://192.168.1.1/")
    assert "Cannot fetch URL" in result
    assert "private" in result.lower()


@pytest.mark.asyncio
async def test_url_processing_blocks_file_scheme():
    """file:// returns error string."""
    from tools.url_processing import url_processing

    result = await url_processing("file:///etc/passwd")
    assert "Cannot fetch URL" in result


@pytest.mark.asyncio
async def test_url_processing_handles_fetch_error():
    """A transport failure returns an error string, never an exception.

    The error text reaches the model, which is the point: it must know the
    fetch failed rather than be handed nothing and answer from invention.
    """
    from tools.url_processing import url_processing

    with patch("tools.url_processing._fetch_and_extract", side_effect=Exception("connection refused")):
        result = await url_processing("https://example.com")
    assert "Failed to fetch" in result
    assert "connection refused" in result
