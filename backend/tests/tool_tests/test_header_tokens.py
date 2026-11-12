"""Template letterhead tokens ({{title}}/{{customer}}/{{date}}) are filled in
the generated .docx; highlight on filled cells is removed; nothing else moves.

A fork marks the variable parts of its house-style header with these tokens;
without them a reference template stamps its literal placeholders on every file."""

from __future__ import annotations

import io
import re
import zipfile

from tools.documents import header_tokens as ht


def _docx(header: str, body: str = "<w:p><w:r><w:t>Body {{title}}</w:t></w:r></w:p>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
        z.writestr("word/header1.xml", f"<w:hdr>{header}</w:hdr>")
    return buf.getvalue()


def _texts(docx: bytes, part: str) -> list[str]:
    xml = zipfile.ZipFile(io.BytesIO(docx)).read(part).decode()
    return [t for t in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml) if t.strip()]


def test_tokens_filled_escaped_and_highlight_removed():
    header = (
        '<w:p><w:pPr><w:rPr><w:highlight w:val="yellow"/></w:rPr></w:pPr>'
        '<w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>{{customer}}</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>{{title}}</w:t></w:r><w:r><w:t>{{date}}</w:t></w:r></w:p>"
        '<w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>untouched</w:t></w:r></w:p>'
    )
    out = ht.fill_header_tokens(_docx(header), {"title": "Q3 <plan>", "customer": "Smith & Co", "date": "09.10.2026"})
    xml = zipfile.ZipFile(io.BytesIO(out)).read("word/header1.xml").decode()
    assert "Smith &amp; Co" in xml and "Q3 &lt;plan&gt;" in xml and "09.10.2026" in xml
    assert xml.count("w:highlight") == 1  # only the paragraph WITHOUT a token keeps it
    assert _texts(out, "word/document.xml") == ["Body {{title}}"]  # the body is never rewritten


def test_empty_value_removes_the_token():
    out = ht.fill_header_tokens(_docx("<w:p><w:r><w:t>{{customer}}</w:t></w:r></w:p>"), {"customer": ""})
    assert _texts(out, "word/header1.xml") == []


def test_bytes_without_tokens_are_returned_unchanged():
    raw = _docx("<w:p><w:r><w:t>Static header</w:t></w:r></w:p>")
    assert ht.fill_header_tokens(raw, {"title": "x"}) is raw
    assert ht.fill_header_tokens(b"not a zip", {"title": "x"}) == b"not a zip"


def test_title_comes_from_first_heading_else_filename():
    assert ht.title_from("intro\n\n# Kickoff **memo**\n\n## Next", "x.docx") == "Kickoff memo"
    assert ht.title_from("no heading here", "Offer - Northwind.docx") == "Offer - Northwind"
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", ht.today())


def test_page_fields_and_untokened_parts_survive():
    """A real letterhead mixes tokens with PAGE/NUMPAGES fields — only tokens change."""
    header = (
        "<w:p><w:r><w:t>{{title}}</w:t></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>"
        "<w:r><w:instrText> NUMPAGES </w:instrText></w:r></w:p>"
    )
    out = ht.fill_header_tokens(_docx(header), {"title": "Offer"})
    xml = zipfile.ZipFile(io.BytesIO(out)).read("word/header1.xml").decode()
    assert "Offer" in xml and " PAGE " in xml and " NUMPAGES " in xml and "{{" not in xml
