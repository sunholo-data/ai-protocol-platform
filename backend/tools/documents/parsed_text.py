"""Canonical text shared by search and RAG; the source bytes never leave here.

Read access is checked before rendering. Tables, headings and the user's edits
use the same renderer as get_document_content. A failed/empty parse is an
explicit error, never a reason to silently send the original PDF elsewhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from db.firestore import get_document
from tools.documents.context import apply_edits, assert_document_readable, blocks_to_markdown


@dataclass(frozen=True)
class ParsedText:
    doc_id: str
    filename: str
    blocks: list[dict[str, Any]]
    text: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def parsed_text_from_record(doc_id: str, record: dict[str, Any], *, caller_uid: str) -> ParsedText:
    assert_document_readable(record, caller_uid, doc_id)
    if (record.get("parseStatus") or record.get("status")) != "parsed":
        raise ValueError("The document is not successfully parsed yet; use its parse status to explain why.")
    blocks = apply_edits(record.get("blocks") or [], record.get("editedBlocks") or {})
    text = blocks_to_markdown(blocks)
    if not text.strip():
        raise ValueError("The parsed document has no searchable text. It needs extraction or OCR first.")
    return ParsedText(doc_id, str(record.get("originalFilename") or "Document"), blocks, text)


def load_parsed_text(doc_id: str, *, caller_uid: str) -> ParsedText:
    record = get_document("parsed_documents", doc_id)
    if record is None:
        raise KeyError("Document not found.")
    return parsed_text_from_record(doc_id, record, caller_uid=caller_uid)
