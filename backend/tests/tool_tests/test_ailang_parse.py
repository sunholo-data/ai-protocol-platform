"""Tests for tools/documents/ailang_parse.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the client singleton between tests."""
    import tools.documents.ailang_parse as ap

    ap._client_singleton = None
    ap._formats_refreshed = False
    ap._cache.clear()
    yield
    ap._client_singleton = None


def _make_parse_result(blocks=None, markdown="parsed content"):
    """Build a minimal fake ParseResult."""
    result = MagicMock()
    result.blocks = blocks or []
    result.markdown = markdown
    result.text = ""
    return result


def _make_block(block_type: str, text: str, **kwargs):
    """Build a fake ailang_parse Block dataclass."""
    try:
        from ailang_parse.types import Block

        return Block(type=block_type, text=text, **kwargs)
    except ImportError:
        m = MagicMock()
        m.type = block_type
        m.text = text
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m


class TestIsSupported:
    def test_docx_is_supported(self):
        from tools.documents.ailang_parse import is_supported

        assert is_supported("gs://bucket/path/doc.docx")

    def test_pdf_not_supported(self):
        from tools.documents.ailang_parse import is_supported

        assert not is_supported("gs://bucket/path/doc.pdf")

    def test_empty_url_not_supported(self):
        from tools.documents.ailang_parse import is_supported

        assert not is_supported("")

    def test_xlsx_is_supported(self):
        from tools.documents.ailang_parse import is_supported

        assert is_supported("gs://bucket/spreadsheet.xlsx")


class TestParseOutcome:
    def test_ok_with_blocks(self):
        from tools.documents.ailang_parse import ParseOutcome

        outcome = ParseOutcome(content=[{"type": "paragraph", "text": "hello"}], output_format="blocks")
        assert outcome.ok
        assert outcome.blocks == [{"type": "paragraph", "text": "hello"}]
        assert outcome.markdown is None

    def test_ok_with_markdown(self):
        from tools.documents.ailang_parse import ParseOutcome

        outcome = ParseOutcome(content="# Hello\n\nWorld", output_format="markdown")
        assert outcome.ok
        assert outcome.markdown == "# Hello\n\nWorld"
        assert outcome.blocks is None

    def test_error_not_ok(self):
        from tools.documents.ailang_parse import ParseOutcome

        outcome = ParseOutcome(error="API error", error_code="api", output_format="blocks")
        assert not outcome.ok


@pytest.mark.asyncio
async def test_parse_gcs_file_unsupported_format():
    """Returns None for genuinely unsupported extensions.

    PDF used to live here, but as of 2026-06-11 PDF is in PARSEABLE_EXTENSIONS
    (AILANG handles it via AI extraction). Use a clearly unsupported binary
    format to keep the original assertion meaningful.
    """
    from tools.documents.ailang_parse import parse_gcs_file

    result = await parse_gcs_file("gs://bucket/executable.exe", output_format="blocks")
    assert result is None


@pytest.mark.asyncio
async def test_parse_gcs_file_no_api_key():
    """Returns None when DOCPARSE_API_KEY is not set (client disabled)."""
    import os

    from tools.documents.ailang_parse import parse_gcs_file

    with patch.dict(os.environ, {}, clear=True):
        # Remove DOCPARSE_API_KEY if set
        os.environ.pop("DOCPARSE_API_KEY", None)
        result = await parse_gcs_file("gs://bucket/doc.docx", output_format="blocks")
    assert result is None


@pytest.mark.asyncio
async def test_parse_gcs_file_via_signed_url_success():
    """Happy path: signed URL → parse_url → blocks returned."""
    import tools.documents.ailang_parse as ap

    fake_block = _make_block("paragraph", "Hello world")
    fake_result = _make_parse_result(blocks=[fake_block])

    mock_client = MagicMock()
    mock_client.parse_url.return_value = fake_result

    with (
        patch.object(ap, "_get_client", return_value=mock_client),
        patch.object(ap, "_generate_signed_url", return_value="https://signed.example.com/doc.docx"),
    ):
        outcome = await ap.parse_gcs_file("gs://bucket/doc.docx", output_format="blocks")

    assert outcome is not None
    assert outcome.ok
    assert isinstance(outcome.blocks, list)
    assert len(outcome.blocks) == 1
    assert outcome.blocks[0]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_parse_gcs_file_markdown_mode():
    """Markdown mode returns string content."""
    import tools.documents.ailang_parse as ap

    fake_result = _make_parse_result(markdown="# Title\n\nBody text.")

    mock_client = MagicMock()
    mock_client.parse_url.return_value = fake_result

    with (
        patch.object(ap, "_get_client", return_value=mock_client),
        patch.object(ap, "_generate_signed_url", return_value="https://signed.example.com/doc.docx"),
    ):
        outcome = await ap.parse_gcs_file("gs://bucket/doc.docx", output_format="markdown")

    assert outcome is not None
    assert outcome.ok
    assert "Title" in (outcome.markdown or "")


@pytest.mark.asyncio
async def test_parse_gcs_file_cache_hit():
    """Second call for same URL uses cache, no API call."""
    import tools.documents.ailang_parse as ap

    fake_block = _make_block("paragraph", "cached")
    fake_result = _make_parse_result(blocks=[fake_block])

    mock_client = MagicMock()
    mock_client.parse_url.return_value = fake_result
    call_count = 0

    def fake_parse_url(url, output_format):
        nonlocal call_count
        call_count += 1
        return fake_result

    mock_client.parse_url.side_effect = fake_parse_url

    with (
        patch.object(ap, "_get_client", return_value=mock_client),
        patch.object(ap, "_generate_signed_url", return_value="https://signed.example.com/doc.docx"),
    ):
        await ap.parse_gcs_file("gs://bucket/doc.docx", output_format="blocks")
        await ap.parse_gcs_file("gs://bucket/doc.docx", output_format="blocks")

    assert call_count == 1  # second call used cache


@pytest.mark.asyncio
async def test_parse_gcs_file_auth_error():
    """Auth error → ParseOutcome with error_code='auth'."""
    import tools.documents.ailang_parse as ap

    try:
        from ailang_parse import AuthError
    except ImportError:
        AuthError = Exception  # type: ignore

    mock_client = MagicMock()
    mock_client.parse_url.side_effect = AuthError("Invalid key")

    with (
        patch.object(ap, "_get_client", return_value=mock_client),
        patch.object(ap, "_generate_signed_url", return_value="https://signed.example.com/doc.docx"),
    ):
        outcome = await ap.parse_gcs_file("gs://bucket/doc.docx", output_format="blocks")

    assert outcome is not None
    assert not outcome.ok
    assert outcome.error_code == "auth"
