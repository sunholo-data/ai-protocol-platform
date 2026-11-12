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
from tools.documents.comments import comments_to_markdown, extract_comments

log = logging.getLogger(__name__)

_PARSED_DOCS_COLLECTION = "parsed_documents"


# --- Markdown renderer ---


def _runs_markdown(runs: Any) -> str | None:
    """A paragraph's text with its links kept, when any run is a link; else None.

    The parser keeps a link's target only on the run (``href``); the block's
    ``text`` is the visible words. Rendering ``text`` turned "Joined with
    [customers](/tables/customers.md)" into "Joined with customers", so a
    Markdown wiki's links could not be followed. Stored list-item runs are
    wrapped as ``{"items": [...]}`` (ailang_parse._unnest_arrays).
    """
    if isinstance(runs, dict):
        runs = runs.get("items")
    if not isinstance(runs, list) or not any(isinstance(r, dict) and r.get("href") for r in runs):
        return None
    out: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        text = str(run.get("text") or "")
        href = str(run.get("href") or "").strip()
        if text and run.get("code"):
            text = f"`{text}`"
        out.append(f"[{text}]({href})" if text and href else text)
    return "".join(out)


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
            text = _runs_markdown(block.get("runs")) or text
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
                row_texts = [_cell_text(c) for c in _row_cells(row)]
                parts.append("| " + " | ".join(row_texts) + " |")

        elif block_type == "list":
            items = block.get("items") or []
            ordered = block.get("ordered", False)
            item_runs = block.get("itemRuns") or block.get("item_runs") or []
            for i, item in enumerate(items):
                prefix = f"{i + 1}." if ordered else "-"
                linked = _runs_markdown(item_runs[i]) if i < len(item_runs) else None
                parts.append(f"{prefix} {linked or item}")

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
            # A `kind: "comment"` section holds reviewer comments, not document
            # body. Rendering them here printed the comment text as if the
            # DOCUMENT said it — author gone, anchor gone. They are surfaced
            # separately, with their anchors, by tools/documents/comments.py.
            if block.get("kind") == "comment":
                continue
            children = block.get("children") or []
            if children:
                parts.append(blocks_to_markdown(children))

        elif block_type == "comment":
            # Same reasoning: never inline, never anonymous.
            continue

        else:
            if text:
                parts.append(text)

    return "\n\n".join(p for p in parts if p)


def _row_cells(row: Any) -> list[Any]:
    """The cells of a table row, whichever shape the row is stored in.

    Firestore rejects an array whose elements are arrays, so a parsed table's
    ``rows`` (``List[List[Cell]]``) is stored as ``[{"cells": [...]}, …]`` —
    see ``_flatten_block`` in ailang_parse.py.

    This function did not exist, and the renderer iterated the row directly.
    Iterating a dict yields its KEYS, so every data row of every table reached
    the agent as the literal string ``| cells |`` — headers intact, all data
    gone. The frontend reads ``row.cells`` and rendered the table correctly, so
    the user saw the numbers on screen while the model was blind to them. On a
    PPA that is the volumes, prices and terms.

    Accepts the raw list shape too: blocks that never round-tripped through
    Firestore (a fresh parse handed straight to a tool) still carry it.
    """
    if isinstance(row, dict):
        cells = row.get("cells")
        return list(cells) if isinstance(cells, list) else []
    if isinstance(row, list):
        return row
    return []


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


def list_documents_for_user(
    user_id: str, skill_id: str | None = None, limit: int = 20, select: list[str] | None = None
) -> list[dict[str, Any]]:
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
        select=select,
    )


class DocumentAccessError(PermissionError):
    """Raised when the caller may not read the requested document.

    A distinct type so call sites can translate it into their own refusal shape
    (a tool returns structured JSON; a callback logs and skips) without matching
    on message text.
    """


# Explicit opt-out for callers that legitimately have no end user — an admin
# backfill, a seeding script. Named, greppable, and never a default: an optional
# security argument fails open the first time a call site omits it, which is
# exactly how `list_documents` came to read a state key nothing ever wrote and
# return every user's documents (M10).
UNCHECKED_INTERNAL = "__unchecked_internal__"


def resolve_caller_uid(ctx: Any) -> str:
    """Best-effort uid for a ToolContext or CallbackContext. "" when unknown.

    Prefers the ADK INVOCATION, which always carries the real uid
    (`skill_processor` passes `user_id=user.uid` through to `session.user_id`).
    The `user:id` / `user_id` state keys are only a fallback: nothing in the
    request path writes them, and trusting them alone is precisely the bug that
    let every user list every document (M10).

    Returns "" rather than raising — the caller decides how to refuse, and every
    caller here refuses.
    """
    if ctx is None:
        return ""
    inv = getattr(ctx, "_invocation_context", None)
    uid = getattr(inv, "user_id", None) if inv is not None else None
    if uid:
        return uid
    try:
        return ctx.state.get("user:id") or ctx.state.get("user_id") or ""
    except Exception:
        return ""


def assert_document_readable(raw: dict[str, Any], caller_uid: str | None, doc_id: str) -> None:
    """Allow the document's owner, and the platform's shared library. Nothing else.

    The shared-library case is load-bearing, not a loophole: the prewarmed PPA
    corpus is deliberately owned by ``PLATFORM_OWNER_UID`` so every tenant can
    read it. An owner-must-match check would have broken it on day one.
    """
    if caller_uid == UNCHECKED_INTERNAL:
        return

    from skills.platform import PLATFORM_OWNER_UID

    owner = raw.get("userId")
    if not caller_uid:
        raise DocumentAccessError(f"Document '{doc_id}' requires an identified caller.")
    if owner == PLATFORM_OWNER_UID:
        return
    # A missing owner must NOT read as "unowned, therefore public".
    if not owner or owner != caller_uid:
        raise DocumentAccessError(f"Document '{doc_id}' does not belong to the current user.")


def build_document_context(
    doc_id: str,
    mode: str = "markdown",
    section: str | None = None,
    *,
    caller_uid: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Load a parsed document from Firestore and format for LLM consumption.

    Args:
        doc_id: Firestore document ID under parsed_documents/{doc_id}.
        mode: "markdown" for chat (compact), "blocks" for extraction (structured JSON),
            "outline" for the section tree (ids, titles, printed pages, sizes) without the text.
        section: Optional section(s) to extract: outline ids ("3.2" or "3.2, 5"), or a heading
            title (exact, else a unique substring; several matches are listed, never guessed).
            With mode="outline", the outline of that section.

    Returns:
        (content_str, blocks_or_none)
        content_str — formatted content to return to the agent
        blocks_or_none — raw blocks list when mode="blocks", else None (also for "outline")

    Raises:
        KeyError: if the document is not found.
    """
    raw = get_document(_PARSED_DOCS_COLLECTION, doc_id)
    if raw is None:
        raise KeyError(f"Document '{doc_id}' not found.")

    # Before ANY content leaves this function, including the filename.
    assert_document_readable(raw, caller_uid, doc_id)

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

    from tools.documents.block_store import load_blocks

    raw_blocks: list[dict[str, Any]] = load_blocks(doc_id, raw)
    edited_blocks_raw: dict[str, Any] = raw.get("editedBlocks") or {}
    metadata: dict = raw.get("metadata") or {}

    blocks = apply_edits(raw_blocks, edited_blocks_raw)

    structure = None
    selected: list[Any] = []
    if mode == "outline" or section:
        from tools.documents.outline import build_structure, section_blocks, select_sections

        structure = build_structure(blocks)
    if section and structure is not None:
        selection = select_sections(structure, section)
        if selection.problem:
            # Say the section is missing rather than rendering an empty document.
            # An empty blocks list reads to the model as "this section exists and
            # is blank", which is the same silent-substitution failure as answering
            # about the wrong document — a plausible shape standing in for "not
            # found". Name what WAS asked for so the model can retry or refuse.
            return (
                f"In {original_filename}: {selection.problem}\n\n"
                'Call get_document_content with mode="outline" to see this document\'s sections '
                "and their ids, then ask for the section by id.",
                None,
            )
        selected = selection.nodes

    if mode == "outline" and structure is not None:
        return _outline_context(original_filename, metadata, structure, selected, _location_lines(doc_id, raw)), None

    if selected and structure is not None:
        blocks = section_blocks(structure, selected)

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
    preamble_lines += _location_lines(doc_id, raw)
    preamble = "\n".join(preamble_lines)

    body = _sections_markdown(structure, selected) if selected and structure is not None else blocks_to_markdown(blocks)
    if not body:
        body = raw.get("text", "") or "(No text content extracted.)"

    # v6.34.0 P4 — reviewer comments, each tied to the passage it points at.
    # A comment without its anchor is noise; with it, the agent can change the
    # one paragraph the reviewer meant.
    comments_md = comments_to_markdown(extract_comments(blocks))
    if comments_md:
        body = f"{body}\n\n---\n\n{comments_md}"

    # v6.34.0 P4b — the EDITABLE source, for documents the assistant generated.
    #
    # The body above is the document re-PARSED from its rendered bytes, which is
    # right for reading and wrong for editing: every regeneration would then be
    # built on the previous parse, so any fidelity the parser loses compounds on
    # each cycle (formatted list items already fragment — the upstream
    # list-grouping bug). The stored markdown is what the document was rendered
    # FROM, so revising it is lossless.
    #
    # Only shown when there is one: an uploaded document has no authored source,
    # and pretending otherwise would invite the model to "edit" a reconstruction.
    source_markdown = str(raw.get("sourceMarkdown") or "").strip()
    if source_markdown:
        body = (
            f"{body}\n\n---\n\n"
            "## Editable source\n\n"
            "This document was generated from the Markdown below, and regenerating it "
            "replaces the file in place. To change the document, edit THIS source — not "
            "the rendered text above — and pass the whole edited Markdown to "
            "`generate_document` with the SAME filename. Change only what was asked for; "
            "leave every other line exactly as it is.\n\n"
            f"```markdown\n{source_markdown}\n```"
        )

    return f"{preamble}\n\n---\n\n{body}", None


def _metadata_preamble(original_filename: str, metadata: dict[str, Any]) -> list[str]:
    lines = [f"**Document:** {original_filename}"]
    if metadata.get("title") and metadata["title"] != original_filename:
        lines.append(f"**Title:** {metadata['title']}")
    if metadata.get("pageCount"):
        lines.append(f"**Pages:** {metadata['pageCount']}")
    return lines


def _sections_markdown(structure: Any, nodes: list[Any]) -> str:
    """Each requested section under a line saying which section it is, where it
    sits and which pages it covers, so a quote can be cited without re-asking."""
    from tools.documents.outline import describe, heading_path, section_blocks

    chosen = {id(n) for n in nodes}

    def inside_another(node: Any) -> bool:
        parent = node.parent
        while parent is not None:
            if id(parent) in chosen:
                return True
            parent = parent.parent
        return False

    parts = []
    for node in nodes:
        if inside_another(node):
            continue  # already printed as part of its parent
        where = heading_path(node.parent) if node.parent else ""
        label = f"**Section {describe(node)}**" + (f" — in: {where}" if where else "")
        parts.append(f"{label}\n\n{blocks_to_markdown(section_blocks(structure, [node]))}")
    return "\n\n---\n\n".join(parts)


def _location_lines(doc_id: str, raw: dict[str, Any]) -> list[str]:
    """Where the document sits, and the YAML header of a Markdown file.

    The path is what a relative link inside the document resolves against
    (tools/documents/paths.py). The header is what the parser drops, apart from
    ``title``: in an Open Knowledge Format bundle its ``type`` and
    ``description`` say what the document IS.
    """
    from tools.documents.frontmatter import stored_frontmatter
    from tools.documents.paths import document_path

    lines: list[str] = []
    path = document_path(raw)
    if path and "/" in path:
        lines.append(f"**Path:** {path}")
    frontmatter = stored_frontmatter(doc_id, raw).strip()
    if frontmatter:
        lines.append(f"**Properties** (the file's YAML header):\n```yaml\n{frontmatter}\n```")
    return lines


def _outline_context(
    original_filename: str,
    metadata: dict[str, Any],
    structure: Any,
    within: list[Any],
    location: list[str] | None = None,
) -> str:
    """The document's outline: section ids, titles, printed pages and sizes.

    The cheap first look at a long document. A 421-page filing's outline is a
    few thousand tokens; its full text is about a hundred times that.
    """
    from tools.documents.outline import DEFAULT_OUTLINE_DEPTH, describe, render_outline, token_estimate

    head = "\n".join(_metadata_preamble(original_filename, metadata) + list(location or []))
    size = token_estimate(sum(leaf.chars for leaf in structure.leaves))
    if not structure.roots:
        return (
            f"{head}\n\n---\n\nThis document has no headings or named sections, so it has no outline. "
            f"Its full text is {size}: read it without a section, or search it for the words you need."
        )
    how = (
        'Open sections with get_document_content(doc_id, section="<id>"); several ids can be '
        'comma-separated (section="2.1, 4"). Pages are the printed page numbers, for citing. '
        'For the subsections of one section: mode="outline", section="<id>".'
    )
    outlines: list[str] = []
    for node in within or [None]:
        lines, depth = render_outline(structure, within=node, max_depth=DEFAULT_OUTLINE_DEPTH)
        if node is None:
            count = len(structure.nodes)
            title = f"Outline — {count} section{'s' if count != 1 else ''}, showing {depth} level{'s' if depth != 1 else ''}; whole document {size}."
            front = structure.front_matter_chars
            if front:
                title += f" Before the first section: {token_estimate(front)} of front matter."
        else:
            title = f"Outline of section {describe(node)}"
            if not lines:
                lines = ["- (no subsections — open it by id to read it)"]
        outlines.append(title + "\n\n" + "\n".join(lines))
    return f"{head}\n\n---\n\n" + "\n\n".join(outlines) + f"\n\n{how}"
