"""Fill a template's header/footer tokens in a generated .docx.

A reference template carries its header and footer into every generated
document UNCHANGED, so a letterhead with literal placeholders ("DOCUMENT
TITLE", "dd.mm.yyyy") stamped those placeholders on every file. Templates now
mark the variable parts with tokens — ``{{title}}``, ``{{customer}}``,
``{{date}}`` — and this fills them after conversion.

Rules:
  * Only ``word/header*.xml`` and ``word/footer*.xml`` are touched; the body
    is the model's markdown, never rewritten here.
  * A token must sit inside ONE run (``<w:t>``) in the template — templates are
    authored that way; a split token is left as-is rather than guessed at.
  * A paragraph that held a token loses ALL its highlight (runs and the
    paragraph mark): templates highlight the to-be-filled cells, which must
    not survive into a finished document.
  * Values are XML-escaped; an empty value removes the token.
  * Bytes without any token come back unchanged (byte-for-byte).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

TOKENS = ("title", "customer", "date")
_PART = re.compile(r"^word/(header|footer)\d*\.xml$")
_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_HIGHLIGHT = re.compile(r"<w:highlight\b[^>]*/>")
_HEADING = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", re.M)
_DATE_TZ = ZoneInfo("Europe/Helsinki")


def title_from(markdown: str, filename: str) -> str:
    """The document's first `#` heading, else its file name without extension."""
    m = _HEADING.search(markdown or "")
    if m:
        return re.sub(r"[*_`]", "", m.group(1)).strip()
    return PurePosixPath(filename or "").stem


def today() -> str:
    return datetime.now(_DATE_TZ).strftime("%d.%m.%Y")


def _fill_part(xml: str, values: dict[str, str]) -> str:
    """Fill tokens; strip ALL highlight from any paragraph that held one.

    Templates highlight the cells to be filled — on the visible runs AND on the
    paragraph mark (pPr/rPr) and neighbouring runs — so clearing only the
    token's own run left invisible-but-real highlight behind (it reappears with
    formatting marks on, and spreads to anything typed at the end of the cell).
    """

    def fix_paragraph(match: re.Match[str]) -> str:
        para = match.group(0)
        if "{{" not in para:
            return para
        filled = para
        for name in TOKENS:
            filled = filled.replace("{{" + name + "}}", escape(values.get(name, "") or ""))
        return _HIGHLIGHT.sub("", filled) if filled != para else para

    return _PARA.sub(fix_paragraph, xml)


def fill_header_tokens(docx: bytes, values: dict[str, str]) -> bytes:
    """Return ``docx`` with header/footer tokens filled from ``values``."""
    if not docx or not _has_token_part(docx):
        return docx
    src = zipfile.ZipFile(io.BytesIO(docx))
    parts = {
        info.filename: src.read(info.filename).decode("utf-8") for info in src.infolist() if _PART.match(info.filename)
    }
    if not any("{{" in xml for xml in parts.values()):
        return docx
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename in parts and "{{" in parts[info.filename]:
                data = _fill_part(parts[info.filename], values).encode("utf-8")
            dst.writestr(info, data)
    return out.getvalue()


def _has_token_part(docx: bytes) -> bool:
    # Parts are deflated, so a raw byte search can miss a token; check properly.
    try:
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            return any(_PART.match(n) and b"{{" in z.read(n) for n in z.namelist())
    except zipfile.BadZipFile:
        return False
