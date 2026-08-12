"""Document context builder — loads parsed documents from Firestore and formats for LLM consumption.

Two modes:
  "markdown"  — compact, low token cost, good for general chat
  "blocks"    — preserves table structure, tracked changes, section hierarchy; required for extraction tasks
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.firestore import get_document, query_documents

log = logging.getLogger(__name__)

_PARSED_DOCS_COLLECTION = "parsed_documents"


# --- Markdown renderer ---


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Convert AILANG Parse blocks (as dicts) to a markdown string."""
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        text = block.get("text") or ""

        if block_type == "heading":
            level = max(1, min(6, block.get("level", 1)))
            parts.append(f"{'#' * level} {text}")

        elif block_type in ("paragraph", "text"):
            if text:
                parts.append(text)

        elif block_type == "table":
            headers = block.get("headers") or []
            rows = block.get("rows") or []
            header_texts = [_cell_text(c) for c in headers]
            if header_texts:
                parts.append("| " + " | ".join(header_texts) + " |")
                parts.append("| " + " | ".join(["---"] * len(header_texts)) + " |")
            for row in rows:
                row_texts = [_cell_text(c) for c in row]
                parts.append("| " + " | ".join(row_texts) + " |")

        elif block_type == "list":
            items = block.get("items") or []
            ordered = block.get("ordered", False)
            for i, item in enumerate(items):
                prefix = f"{i + 1}." if ordered else "-"
                parts.append(f"{prefix} {item}")

        elif block_type == "change":
            change_type = block.get("change_type", "")
            if text:
                if change_type == "deletion":
                    parts.append(f"~~{text}~~")
                elif change_type == "insertion":
                    parts.append(f"**[INSERTED]** {text}")
                else:
                    parts.append(text)

        elif block_type == "section":
            children = block.get("children") or []
            if children:
                parts.append(blocks_to_markdown(children))

        else:
            if text:
                parts.append(text)

    return "\n\n".join(p for p in parts if p)


def _cell_text(cell: Any) -> str:
    if isinstance(cell, dict):
        return cell.get("text", "")
    return str(cell)


# --- Edit overlay ---


def apply_edits(blocks: list[dict[str, Any]], edited_blocks: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply user edits overlay onto a block list.

    edited_blocks maps str(block_index) → EditedBlock dict.
    Returns a new list with edited text applied.
    """
    if not edited_blocks:
        return blocks
    result = []
    for i, block in enumerate(blocks):
        edit = edited_blocks.get(str(i))
        if edit:
            edited_text = edit.get("editedText") or edit.get("edited_text")
            if edited_text:
                block = dict(block, text=edited_text)
        result.append(block)
    return result


# --- Public API ---


def list_documents_for_user(user_id: str, skill_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List parsed documents owned by a user, optionally filtered by skill."""
    filters: list[tuple[str, str, Any]] = [
        ("userId", "==", user_id),
        ("status", "==", "parsed"),
    ]
    if skill_id:
        filters.append(("skillId", "==", skill_id))
    return query_documents(
        collection=_PARSED_DOCS_COLLECTION,
        filters=filters,
        order_by="createdAt",
        order_direction="DESCENDING",
        limit=limit,
    )


def build_document_context(
    doc_id: str,
    mode: str = "markdown",
    section: str | None = None,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Load a parsed document from Firestore and format for LLM consumption.

    Args:
        doc_id: Firestore document ID under parsed_documents/{doc_id}.
        mode: "markdown" for chat (compact), "blocks" for extraction (structured JSON).
        section: Optional section heading to extract (case-insensitive substring match).

    Returns:
        (content_str, blocks_or_none)
        content_str — formatted content to return to the agent
        blocks_or_none — raw blocks list when mode="blocks", else None

    Raises:
        KeyError: if the document is not found.
    """
    raw = get_document(_PARSED_DOCS_COLLECTION, doc_id)
    if raw is None:
        raise KeyError(f"Document '{doc_id}' not found.")

    original_filename = raw.get("originalFilename", "Unknown document")
    parse_status = raw.get("parseStatus", "pending")

    # Tell the AI explicitly when a document failed — don't silently provide empty context.
    if parse_status == "failed":
        parse_error = raw.get("parseError") or "unknown error"
        return (
            f"**Document:** {original_filename}\n\n"
            f"⚠️ This document could not be parsed: {parse_error}\n"
            "The document was uploaded but its content is unavailable. "
            "You can tell the user about this error and ask them to re-upload or try a different format."
        ), None

    if parse_status in ("pending", "pending_ai_extraction"):
        return (
            f"**Document:** {original_filename}\n\n"
            f"⏳ This document is still being processed (status: {parse_status}). "
            "Its content is not yet available. Ask the user to try again in a moment."
        ), None

    raw_blocks: list[dict[str, Any]] = raw.get("blocks") or []
    edited_blocks_raw: dict[str, Any] = raw.get("editedBlocks") or {}
    metadata: dict = raw.get("metadata") or {}

    blocks = apply_edits(raw_blocks, edited_blocks_raw)

    if section:
        blocks = _filter_by_section(blocks, section)

    if mode == "blocks":
        return json.dumps(
            {"docId": doc_id, "filename": original_filename, "blocks": blocks}, ensure_ascii=False
        ), blocks

    # markdown mode
    preamble_lines = [f"**Document:** {original_filename}"]
    if metadata.get("title") and metadata["title"] != original_filename:
        preamble_lines.append(f"**Title:** {metadata['title']}")
    if metadata.get("author"):
        preamble_lines.append(f"**Author:** {metadata['author']}")
    if metadata.get("pageCount"):
        preamble_lines.append(f"**Pages:** {metadata['pageCount']}")
    preamble = "\n".join(preamble_lines)

    body = blocks_to_markdown(blocks)
    if not body:
        body = raw.get("text", "") or "(No text content extracted.)"

    return f"{preamble}\n\n---\n\n{body}", None


def _filter_by_section(blocks: list[dict[str, Any]], section: str) -> list[dict[str, Any]]:
    """Return blocks under the heading matching section (case-insensitive).

    Returns all blocks from the matching heading up to the next same-or-higher heading.
    Falls back to keyword search in text if no heading matches.
    """
    section_lower = section.lower()
    # Find matching heading
    start_idx = None

    for i, b in enumerate(blocks):
        if b.get("type") == "heading" and section_lower in (b.get("text") or "").lower():
            start_idx = i
            break

    if start_idx is not None:
        result = [blocks[start_idx]]
        for b in blocks[start_idx + 1 :]:
            if b.get("type") == "heading":
                break
            result.append(b)
        return result

    # Keyword fallback: return blocks containing the section string
    return [b for b in blocks if section_lower in (b.get("text") or "").lower()]
