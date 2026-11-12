"""Tests for tools/documents/vision_parse.py and its wiring into parse_gcs_file.

The shape under test is the one that failed on 2026-11-02: a scanned PDF whose
text layer is one short stamp per page, padded with layout whitespace. The
hosted parser called that a success, so the clause extractor saw nothing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tools.documents.ailang_parse as ap
import tools.documents.vision_parse as vp


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ap._client_singleton = None
    ap._cache.clear()
    monkeypatch.delenv("DOCUMENT_VISION_FALLBACK", raising=False)
    yield
    ap._cache.clear()


def _scan_layer(pages: int = 78) -> list[dict]:
    """Two blocks per page: a repeated stamp, then layout padding."""
    stamp = "COPIA AUTENTICA Registro 0-00-000 hoja de firmas verificada"
    blocks = []
    for _ in range(pages):
        blocks.append({"type": "text", "text": stamp + " " * 900})
        blocks.append({"type": "text", "text": " " * 700 + "\n" * 20})
    return blocks


def _real_text(paragraphs: int = 40) -> list[dict]:
    words = (
        "seller shall deliver contracted energy buyer pays fixed price per megawatt hour "
        "delivery point settlement period term commences effective date guarantee "
        "force majeure termination governing law arbitration curtailment balancing"
    ).split()
    return [
        {"type": "text", "text": f"Clause {i}. " + " ".join(words[(i * 7 + j) % len(words)] for j in range(60))}
        for i in range(paragraphs)
    ]


def test_a_price_table_with_few_words_is_not_sparse():
    rows = [
        {"type": "text", "text": f"Q{q} {y} | {40 + q * 1.5 + y % 7:.2f} EUR/MWh"}
        for y in range(2027, 2047)
        for q in range(1, 5)
    ]
    assert not vp.text_layer_is_sparse(rows)


def _hosted_ok(blocks):
    return ap.ParseOutcome(content=blocks, output_format="blocks")


# --- text_layer_is_sparse ------------------------------------------------------


def test_a_scans_stamp_layer_is_sparse():
    assert vp.text_layer_is_sparse(_scan_layer())


def test_real_text_is_not_sparse():
    assert not vp.text_layer_is_sparse(_real_text())


def test_nothing_at_all_is_sparse():
    assert vp.text_layer_is_sparse([])
    assert vp.text_layer_is_sparse([{"type": "text", "text": "Page 1"}])


def test_text_inside_tables_and_children_counts():
    table = {"type": "table", "rows": [{"cells": [{"text": b["text"]}]} for b in _real_text(10)]}
    assert not vp.text_layer_is_sparse([{"type": "section", "children": [table]}])


# --- fallback_reason ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reason_for_a_sparse_pdf():
    assert "scanned" in await vp.fallback_reason("gs://b/c.pdf", _hosted_ok(_scan_layer()), "blocks")


@pytest.mark.asyncio
async def test_no_reason_for_a_good_pdf():
    assert await vp.fallback_reason("gs://b/c.pdf", _hosted_ok(_real_text()), "blocks") is None


@pytest.mark.asyncio
async def test_reason_when_the_hosted_parse_failed_or_is_unavailable():
    failed = ap.ParseOutcome(error="413 Request Entity Too Large", error_code="api")
    assert "failed" in await vp.fallback_reason("gs://b/c.pdf", failed, "blocks")
    assert "unavailable" in await vp.fallback_reason("gs://b/photo.jpg", None, "blocks")


@pytest.mark.asyncio
async def test_never_for_office_files_markdown_or_when_disabled(monkeypatch):
    failed = ap.ParseOutcome(error="413", error_code="api")
    assert await vp.fallback_reason("gs://b/c.docx", failed, "blocks") is None
    assert await vp.fallback_reason("gs://b/c.pdf", failed, "markdown") is None
    monkeypatch.setenv("DOCUMENT_VISION_FALLBACK", "0")
    assert await vp.fallback_reason("gs://b/c.pdf", failed, "blocks") is None


@pytest.mark.asyncio
async def test_a_transcript_is_never_re_read_even_when_short():
    short = [{"type": "text", "text": "Signature page", "page": 1, "origin": vp.ORIGIN}]
    assert vp.text_layer_is_sparse(short)
    assert not await vp.stored_scan_needs_reparse("gs://b/c.pdf", short)
    assert await vp.stored_scan_needs_reparse("gs://b/c.pdf", _scan_layer())
    assert not await vp.stored_scan_needs_reparse("gs://b/c.docx", _scan_layer())


# --- whitespace alone never decides: per-page density settles a suspect ------


def _padded(text: str, pad: int) -> dict:
    return {"type": "text", "text": text + " " * pad}


def _numbered_stamps(pages: int) -> list[dict]:
    """A stamp that carries its page number: every block differs, mostly padding."""
    return [
        _padded(f"Registro 0-00-000 copia autentica hoja {i} de {pages} verificada notaria", 1400) for i in range(pages)
    ]


def _padded_word_export() -> list[dict]:
    """One real page whose layout padding outweighs its text (seen on prod)."""
    words = "the parties agree that delivery shall commence on the effective date and continue".split()
    return [_padded(" ".join(words[(i + j) % len(words)] for j in range(18)), 450) for i in range(34)]


def test_whitespace_makes_a_suspect_not_a_verdict():
    assert vp.sparse_verdict(_numbered_stamps(78)) == "suspect"
    assert vp.sparse_verdict(_padded_word_export()) == "suspect"
    assert vp.sparse_verdict(_real_text()) == "ok"
    assert vp.sparse_verdict(_scan_layer()) == "sparse"


def test_characters_per_page_settle_a_suspect():
    assert vp.text_layer_is_sparse(_numbered_stamps(78), page_count=78)
    assert not vp.text_layer_is_sparse(_padded_word_export(), page_count=1)


@pytest.mark.asyncio
async def test_needs_vision_reads_the_page_count_only_for_a_suspect():
    with patch.object(vp, "_pdf_page_count_gcs", return_value=1) as count:
        assert not await vp.needs_vision("gs://b/export.pdf", _padded_word_export())
        assert not await vp.needs_vision("gs://b/text.pdf", _real_text())
        assert await vp.needs_vision("gs://b/scan.pdf", _scan_layer())
    assert count.call_count == 1
    with patch.object(vp, "_pdf_page_count_gcs", return_value=78):
        assert await vp.needs_vision("gs://b/scan.pdf", _numbered_stamps(78))
    # An unreadable file keeps its parse.
    with patch.object(vp, "_pdf_page_count_gcs", return_value=None):
        assert not await vp.needs_vision("gs://b/scan.pdf", _numbered_stamps(78))


# --- parse_gcs_file wiring ----------------------------------------------------

_VISION_BLOCKS = [{"type": "text", "text": "Clause 1. Term.", "page": 1, "origin": "vision"}]


def _vision_ok():
    return ap.ParseOutcome(content=list(_VISION_BLOCKS), output_format="blocks", parser=vp.PARSER_NAME)


@pytest.mark.asyncio
async def test_a_scanned_pdf_is_read_by_vision():
    with (
        patch.object(ap, "_get_client", return_value=MagicMock()),
        patch.object(ap, "_parse_hosted", new=AsyncMock(return_value=_hosted_ok(_scan_layer()))),
        patch.object(vp, "transcribe_gcs_file", new=AsyncMock(return_value=_vision_ok())) as vision,
    ):
        out = await ap.parse_gcs_file("gs://b/scan.pdf")
    assert out.ok and out.parser == vp.PARSER_NAME and out.blocks == _VISION_BLOCKS
    assert "scanned" in vision.await_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_a_good_pdf_never_calls_vision():
    good = _hosted_ok(_real_text())
    with (
        patch.object(ap, "_get_client", return_value=MagicMock()),
        patch.object(ap, "_parse_hosted", new=AsyncMock(return_value=good)),
        patch.object(vp, "transcribe_gcs_file", new=AsyncMock()) as vision,
    ):
        out = await ap.parse_gcs_file("gs://b/text.pdf")
    assert out is good
    vision.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_too_large_pdf_is_read_by_vision():
    too_big = ap.ParseOutcome(error="AILANG Parse API error: API error: 413", error_code="api")
    with (
        patch.object(ap, "_get_client", return_value=MagicMock()),
        patch.object(ap, "_parse_hosted", new=AsyncMock(return_value=too_big)),
        patch.object(vp, "transcribe_gcs_file", new=AsyncMock(return_value=_vision_ok())),
    ):
        out = await ap.parse_gcs_file("gs://b/big.pdf")
    assert out.ok and out.parser == vp.PARSER_NAME


@pytest.mark.asyncio
async def test_without_a_parser_key_a_pdf_still_gets_read():
    with (
        patch.object(ap, "_get_client", return_value=None),
        patch.object(vp, "transcribe_gcs_file", new=AsyncMock(return_value=_vision_ok())),
    ):
        out = await ap.parse_gcs_file("gs://b/scan.pdf")
    assert out.ok


@pytest.mark.asyncio
async def test_when_both_routes_fail_the_error_names_both():
    failed_vision = ap.ParseOutcome(error="Vision transcription failed: 429", error_code="api")
    with (
        patch.object(ap, "_get_client", return_value=MagicMock()),
        patch.object(ap, "_parse_hosted", new=AsyncMock(return_value=_hosted_ok(_scan_layer()))),
        patch.object(vp, "transcribe_gcs_file", new=AsyncMock(return_value=failed_vision)),
    ):
        out = await ap.parse_gcs_file("gs://b/scan.pdf")
    # Never the scan's empty layer stored as if it were the document.
    assert not out.ok
    assert "scanned" in out.error and "429" in out.error


# --- transcribe_gcs_file ------------------------------------------------------


def _write_pdf(path, pages: int) -> None:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(595, 842)
    doc.save(str(path))
    doc.close()


def _response(pages: int, finish: str = "STOP"):
    body = {
        "pages": [{"page": i + 1, "blocks": [{"kind": "paragraph", "text": f"line {i + 1}"}]} for i in range(pages)]
    }
    r = MagicMock()
    r.text = json.dumps(body)
    r.candidates = [MagicMock(finish_reason=finish)]
    return r


def _pages_in(call) -> int:
    import io

    import pypdfium2 as pdfium

    part = call.kwargs["prompt"][0]
    doc = pdfium.PdfDocument(io.BytesIO(part.inline_data.data))
    try:
        return len(doc)
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_a_pdf_is_read_in_page_ranges_with_true_page_numbers(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_VISION_PAGES_PER_CALL", "2")
    work = tmp_path / "dl"
    work.mkdir()
    _write_pdf(work / "scan.pdf", 5)

    async def fake(**kw):
        return _response(_pages_in(MagicMock(kwargs=kw)))

    with (
        patch.object(ap, "_download_gcs_to_tempfile", return_value=str(work / "scan.pdf")),
        patch("tools.resilient_genai.generate_content_resilient", new=AsyncMock(side_effect=fake)) as gen,
    ):
        out = await vp.transcribe_gcs_file("gs://b/scan.pdf", reason="test")

    assert out.ok and out.parser == vp.PARSER_NAME
    assert sorted(_pages_in(c) for c in gen.await_args_list) == [1, 2, 2]
    assert [b["page"] for b in out.blocks] == [1, 2, 3, 4, 5]
    assert all(b["origin"] == vp.ORIGIN for b in out.blocks)
    assert gen.await_args.kwargs["model_ref"] == "pro"
    # The download is cleaned up.
    assert not work.exists()


@pytest.mark.asyncio
async def test_a_range_that_hits_the_output_cap_is_split(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_VISION_PAGES_PER_CALL", "4")
    work = tmp_path / "dl"
    work.mkdir()
    _write_pdf(work / "scan.pdf", 4)

    async def fake(**kw):
        n = _pages_in(MagicMock(kwargs=kw))
        return _response(n, finish="MAX_TOKENS" if n == 4 else "STOP")

    with (
        patch.object(ap, "_download_gcs_to_tempfile", return_value=str(work / "scan.pdf")),
        patch("tools.resilient_genai.generate_content_resilient", new=AsyncMock(side_effect=fake)) as gen,
    ):
        out = await vp.transcribe_gcs_file("gs://b/scan.pdf")

    assert out.ok
    assert [_pages_in(c) for c in gen.await_args_list] == [4, 2, 2]
    assert [b["page"] for b in out.blocks] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_a_failed_model_call_is_a_visible_error(tmp_path):
    work = tmp_path / "dl"
    work.mkdir()
    _write_pdf(work / "scan.pdf", 1)
    with (
        patch.object(ap, "_download_gcs_to_tempfile", return_value=str(work / "scan.pdf")),
        patch("tools.resilient_genai.generate_content_resilient", new=AsyncMock(side_effect=RuntimeError("quota"))),
    ):
        out = await vp.transcribe_gcs_file("gs://b/scan.pdf")
    assert not out.ok and "quota" in out.error


@pytest.mark.asyncio
async def test_an_image_is_one_call():
    import tempfile

    work = tempfile.mkdtemp()
    with open(f"{work}/photo.png", "wb") as fh:
        fh.write(b"\x89PNG fake")
    with (
        patch.object(ap, "_download_gcs_to_tempfile", return_value=f"{work}/photo.png"),
        patch("tools.resilient_genai.generate_content_resilient", new=AsyncMock(return_value=_response(1))) as gen,
    ):
        out = await vp.transcribe_gcs_file("gs://b/photo.png")
    assert out.ok and gen.await_count == 1
    assert gen.await_args.kwargs["prompt"][0].inline_data.mime_type == "image/png"


# --- hedging the model's latency tail -----------------------------------------


@pytest.mark.asyncio
async def test_a_slow_call_is_hedged_and_the_first_answer_wins(monkeypatch):
    """The same call took 9 s and 183 s on deployed dev: a hedge request goes
    out once the first is slow, the faster answer is used, the other cancelled."""
    import asyncio

    monkeypatch.setenv("DOCUMENT_VISION_HEDGE_AFTER_S", "0.05")
    calls = {"n": 0, "cancelled": 0}

    async def part(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                calls["cancelled"] += 1
                raise
        return [{"type": "text", "text": "fast", "page": 1, "origin": vp.ORIGIN}]

    with patch.object(vp, "_transcribe_part", side_effect=part):
        blocks = await vp._transcribe_hedged(b"%PDF", "application/pdf", 1, 1, "p1-1")
    assert blocks[0]["text"] == "fast"
    assert calls["n"] == 2
    await asyncio.sleep(0)
    assert calls["cancelled"] == 1


@pytest.mark.asyncio
async def test_a_fast_call_is_never_hedged(monkeypatch):
    monkeypatch.setenv("DOCUMENT_VISION_HEDGE_AFTER_S", "5")
    part = AsyncMock(return_value=[{"type": "text", "text": "ok"}])
    with patch.object(vp, "_transcribe_part", new=part):
        await vp._transcribe_hedged(b"%PDF", "application/pdf", 1, 1, "p1-1")
    assert part.await_count == 1


@pytest.mark.asyncio
async def test_a_range_that_never_answers_times_out_visibly(monkeypatch):
    import asyncio

    monkeypatch.setenv("DOCUMENT_VISION_HEDGE_AFTER_S", "0.02")
    monkeypatch.setenv("DOCUMENT_VISION_CALL_TIMEOUT_S", "0.1")

    async def hang(*_a, **_k):
        await asyncio.sleep(10)

    with patch.object(vp, "_transcribe_part", side_effect=hang), pytest.raises(TimeoutError):
        await vp._transcribe_hedged(b"%PDF", "application/pdf", 1, 1, "p1-1")


@pytest.mark.asyncio
async def test_a_failure_before_the_hedge_is_raised_not_swallowed():
    part = AsyncMock(side_effect=RuntimeError("quota"))
    with patch.object(vp, "_transcribe_part", new=part), pytest.raises(RuntimeError, match="quota"):
        await vp._transcribe_hedged(b"%PDF", "application/pdf", 1, 1, "p1-1")


@pytest.mark.asyncio
async def test_truncation_is_not_hedged_but_passed_up_to_split():
    part = AsyncMock(side_effect=vp._Truncated("p1-4"))
    with patch.object(vp, "_transcribe_part", new=part), pytest.raises(vp._Truncated):
        await vp._transcribe_hedged(b"%PDF", "application/pdf", 1, 4, "p1-4")
    assert part.await_count == 1
