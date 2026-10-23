"""Live templating against the real convert API — v6.32.0 document-creation M4.

The design doc's M4 acceptance is *"asserted by comparing parts, not by eye"*, so
this converts through the production `convert_markdown()` wrapper and reads the
resulting Word XML directly.

Guarded by an appendix rule from the design doc, learned the hard way:

  > when a document tool reports a surprising result, read the file's XML before
  > believing the tool's summary of it.

`reference_doc_applied: true` with `template_parts_carried: 0` is a *correct*
result for a reference document that has nothing to carry — so trusting the flag
alone would let a real regression through. The assertions below compare bytes.

Requires DOCPARSE_API_KEY and network:

    DOCPARSE_API_KEY=$(gcloud secrets versions access latest \\
        --secret=DOCPARSE_API_KEY --project=multivac-internal-dev) \\
        uv run pytest tests/integration/test_convert_templating_live.py -v
"""

from __future__ import annotations

import os
import re
import zipfile

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DOCPARSE_API_KEY"), reason="DOCPARSE_API_KEY not set"),
]

_MARKDOWN = """# Commercial Offer

## Summary

Exclusive advisor on long-term fixed price PPAs for unsubsidised renewables.

| Item | Value |
|---|---|
| Capacity | 5.0 GW |
| Term | 10 years |

- First obligation
- Second obligation
"""

_TABLE_STYLE = "Light Grid Accent 1"


@pytest.fixture(scope="module")
def template_docx(tmp_path_factory) -> str:
    """A real Word-shaped template with a theme, header and footer to carry."""
    docx = pytest.importorskip("docx", reason="python-docx needed to build the template")
    path = tmp_path_factory.mktemp("tmpl") / "letterhead.docx"
    doc = docx.Document()
    doc.add_heading("Letterhead", 0)
    doc.add_paragraph("Template body text.")
    doc.add_table(rows=2, cols=2).style = _TABLE_STYLE
    doc.sections[0].header.paragraphs[0].text = "ACME LETTERHEAD"
    doc.sections[0].footer.paragraphs[0].text = "confidential footer"
    doc.save(str(path))
    return str(path)


def _style_ids(z: zipfile.ZipFile) -> set[bytes]:
    return set(re.findall(rb'w:styleId="([^"]+)"', z.read("word/styles.xml")))


@pytest.mark.asyncio
async def test_templated_docx_carries_the_reference_document_parts(template_docx, tmp_path):
    from tools.documents.ailang_parse import convert_markdown

    outcome = await convert_markdown(
        _MARKDOWN,
        target="docx",
        filename="offer.docx",
        reference_doc_path=template_docx,
        table_style=_TABLE_STYLE,
    )

    assert outcome.ok, outcome.error
    assert outcome.reference_doc_applied
    assert outcome.template_parts_carried > 0, "the flag alone is not evidence — nothing was carried"

    out_path = tmp_path / "offer.docx"
    out_path.write_bytes(outcome.content)

    with zipfile.ZipFile(template_docx) as zt, zipfile.ZipFile(out_path) as zb:
        for part in ("word/theme/theme1.xml", "word/header1.xml", "word/footer1.xml"):
            assert part in zb.namelist(), f"{part} missing from the generated document"
            assert zt.read(part) == zb.read(part), f"{part} is not byte-identical to the template's"

        template_styles, output_styles = _style_ids(zt), _style_ids(zb)
        assert template_styles <= output_styles, "the template's styles were not all carried"

        document_xml = zb.read("word/document.xml")
        assert b"Commercial Offer" in document_xml, "the authored content is missing"
        assert b"Light Grid Accent 1" in document_xml or b"LightGrid-Accent1" in document_xml


@pytest.mark.asyncio
async def test_untemplated_conversion_still_produces_a_valid_document(tmp_path):
    """Graceful degradation: no template → unstyled but valid docx."""
    from tools.documents.ailang_parse import convert_markdown

    outcome = await convert_markdown(_MARKDOWN, target="docx", filename="offer.docx")

    assert outcome.ok, outcome.error
    assert not outcome.reference_doc_applied
    out_path = tmp_path / "plain.docx"
    out_path.write_bytes(outcome.content)
    with zipfile.ZipFile(out_path) as z:
        assert "word/document.xml" in z.namelist()
        assert b"Commercial Offer" in z.read("word/document.xml")


@pytest.mark.asyncio
async def test_no_raw_markup_reaches_the_delivered_document(template_docx, tmp_path):
    """A delivered artefact must contain zero raw markup (design-doc success metric)."""
    from tools.documents.ailang_parse import convert_markdown

    outcome = await convert_markdown(_MARKDOWN, target="docx", filename="offer.docx", reference_doc_path=template_docx)
    assert outcome.ok, outcome.error

    out_path = tmp_path / "offer.docx"
    out_path.write_bytes(outcome.content)
    with zipfile.ZipFile(out_path) as z:
        body = z.read("word/document.xml")

    for marker in (rb"\node", rb"\includegraphics", rb"\fontsize", b"```"):
        assert marker not in body, f"raw markup {marker!r} leaked into the document"
