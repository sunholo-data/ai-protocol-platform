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
async def test_url_processing_calls_load_web_page():
    """Happy path: valid URL delegates to load_web_page."""
    from tools.url_processing import url_processing

    with patch("tools.url_processing.load_web_page", return_value="page content") as mock_lwp:
        result = await url_processing("https://example.com")
    mock_lwp.assert_called_once_with("https://example.com")
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
    """load_web_page failure returns error string (not exception)."""
    from tools.url_processing import url_processing

    with patch("tools.url_processing.load_web_page", side_effect=Exception("connection refused")):
        result = await url_processing("https://example.com")
    assert "Failed to fetch" in result
    assert "connection refused" in result
