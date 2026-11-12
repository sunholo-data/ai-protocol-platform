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


# --- compact blocks (2026-10-30) -------------------------------------------------


def test_extracted_blocks_keep_only_non_default_fields():
    """asdict wrote all ~28 Block fields on every child and cell: a 28 KB
    spreadsheet became a 1.7 MB section, over Firestore's record limit."""
    from ailang_parse.types import Block, Cell

    from tools.documents.ailang_parse import _extract_content

    table = Block(type="table", headers=[Cell(text="KPI")], rows=[[Cell(text="Visits"), Cell(text="")]])
    section = Block(
        type="section", kind="sheet", name="Q1", children=[Block(type="heading", text="Q1", level=0), table]
    )
    result = MagicMock(blocks=[section])
    (out,) = _extract_content(result, "blocks")

    assert set(out) == {"type", "kind", "name", "children", "level", "anchor_block_index"}
    heading, tbl = out["children"]
    assert heading["level"] == 0  # readers default a MISSING level to 1
    assert "description" not in tbl and "items" not in tbl
    # Rows keep their {"cells": [...]} wrapper and every cell position, even an empty cell.
    assert [len(r["cells"]) for r in tbl["rows"]] == [2]
    assert tbl["rows"][0]["cells"][0]["text"] == "Visits"
    assert "text" not in tbl["rows"][0]["cells"][1]
    assert tbl["rows"][0]["cells"][1].get("col_span", 1) == 1


def test_client_timeout_allows_slow_spreadsheets(monkeypatch):
    import tools.documents.ailang_parse as ap

    monkeypatch.setenv("DOCPARSE_API_KEY", "k")
    monkeypatch.delenv("DOCPARSE_TIMEOUT_S", raising=False)
    with patch.object(ap, "DocParse") as cls, patch.object(ap, "_refresh_deterministic_extensions"):
        ap._get_client()
    assert cls.call_args.kwargs["timeout"] == 300


@pytest.mark.parametrize("name", ["meeting #3 notes.docx", "what? notes.docx", "plain.docx"])
def test_object_names_with_url_characters_keep_their_extension(name):
    """urlparse read a gs:// object name up to its '#' (a fragment), so the
    extension came out '' and the parse was skipped — the file stranded in
    pending_ai_extraction."""
    from tools.documents.ailang_parse import is_parseable, is_supported, split_gs_url

    url = f"gs://bucket/users/u/docs/f1/{name}"
    assert split_gs_url(url) == ("bucket", f"users/u/docs/f1/{name}")
    assert is_supported(url) and is_parseable(url)


# --- host-local fallback for PDFs with no text layer (Sunholo, 2026-10-30) -------

_NO_TEXT = 'AILANG Parse API error: 502 {"error":{"code":"PDF_BACKEND_FAILED","message":"no text layer"}}'


def _fake_cli(seen: dict):
    import json as _json
    import os as _os

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        out_dir = cmd[cmd.index("--output-dir") + 1]
        doc = {
            "document": {"status": "success", "blocks": [{"type": "heading", "text": "Slide 1", "level": 2}]},
            "aiCallsUsed": 0,
        }
        with open(_os.path.join(out_dir, _os.path.basename(cmd[1]) + ".json"), "w") as f:
            _json.dump(doc, f)
        return MagicMock(returncode=0, stdout="", stderr="")

    return run


@pytest.mark.asyncio
async def test_no_text_layer_is_parsed_by_the_local_cli_when_configured(tmp_path, monkeypatch):
    import tools.documents.ailang_parse as ap

    pdf = tmp_path / "slides.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setenv("DOCPARSE_LOCAL_CLI", "/usr/local/bin/docparse")
    monkeypatch.setenv("DOCPARSE_PDF_BACKEND", "pdftotext")
    seen: dict = {}
    hosted = ap.ParseOutcome(error=_NO_TEXT, error_code="api")
    with patch.object(ap.subprocess, "run", side_effect=_fake_cli(seen)):
        out = await ap._local_fallback("gs://b/slides.pdf", str(pdf), "blocks", hosted)
    assert out.ok and out.blocks[0]["text"] == "Slide 1"
    assert seen["cmd"][:2] == ["/usr/local/bin/docparse", str(pdf)]
    # The default ladder (pdftotext -> docling) runs only with no backend forced.
    assert "DOCPARSE_PDF_BACKEND" not in seen["env"]


@pytest.mark.asyncio
async def test_without_the_cli_the_hosted_error_stands(monkeypatch):
    import tools.documents.ailang_parse as ap

    monkeypatch.delenv("DOCPARSE_LOCAL_CLI", raising=False)
    hosted = ap.ParseOutcome(error=_NO_TEXT, error_code="api")
    with patch.object(ap.subprocess, "run") as run:
        out = await ap._local_fallback("gs://b/slides.pdf", "/tmp/x.pdf", "blocks", hosted)
    assert out is hosted
    run.assert_not_called()


@pytest.mark.asyncio
async def test_other_failures_never_go_local(monkeypatch):
    import tools.documents.ailang_parse as ap

    monkeypatch.setenv("DOCPARSE_LOCAL_CLI", "/usr/local/bin/docparse")
    hosted = ap.ParseOutcome(error="AILANG Parse API error: 502 bad gateway", error_code="api")
    with patch.object(ap.subprocess, "run") as run:
        assert await ap._local_fallback("gs://b/x.pdf", "/tmp/x.pdf", "blocks", hosted) is hosted
    run.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_local_parse_keeps_both_reasons(tmp_path, monkeypatch):
    import tools.documents.ailang_parse as ap

    pdf = tmp_path / "slides.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setenv("DOCPARSE_LOCAL_CLI", "/usr/local/bin/docparse")
    hosted = ap.ParseOutcome(error=_NO_TEXT, error_code="api")
    with patch.object(ap.subprocess, "run", return_value=MagicMock(returncode=2, stdout="", stderr="docling missing")):
        out = await ap._local_fallback("gs://b/slides.pdf", str(pdf), "blocks", hosted)
    assert not out.ok and "PDF_BACKEND_FAILED" in out.error and "docling missing" in out.error
