"""Literal fallback over the current project's authorised parsed documents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.tools import ToolContext

from adk.project_scope import current_project
from db.firestore import get_document
from tools.documents.context import resolve_caller_uid
from tools.documents.parsed_text import parsed_text_from_record
from tools.documents.text_search import MAX_RESULTS, literal_pattern, matching_passages

log = logging.getLogger(__name__)
TOOL_NAME = "grep_project_documents"
MAX_DOCUMENTS = 250
MAX_SCAN_CHARS = 25_000_000


def _search(uid: str, project: dict[str, Any], terms: list[str], max_results: int, cursor: int) -> dict[str, Any]:
    from db.projects import project_document_ids

    pattern = literal_pattern(terms)
    ids = sorted(project_document_ids(uid, project["id"]))
    scanned = chars = 0
    next_cursor = None
    skipped: list[str] = []
    per_document: list[list[dict[str, Any]]] = []
    for offset, doc_id in enumerate(ids[cursor : cursor + MAX_DOCUMENTS], cursor):
        try:
            record = get_document("parsed_documents", doc_id) or {}
            # Membership can change between the listing and this read. Recheck
            # ownership as well; a forged/stale index row is never authority.
            if record.get("userId") != uid or project["id"] not in (record.get("projectIds") or []):
                continue
            parsed = parsed_text_from_record(doc_id, record, caller_uid=uid)
            if chars + len(parsed.text) > MAX_SCAN_CHARS:
                next_cursor = offset
                break
            chars += len(parsed.text)
            scanned += 1
            hits = matching_passages(parsed.blocks, pattern, limit=max_results + 1)
            for hit in hits:
                hit.update(filename=parsed.filename, title=parsed.filename, ref=doc_id)
            if hits:
                per_document.append(hits)
        except Exception:
            # Do not expose a filename when a document failed its access check.
            log.warning("project text search: could not scan a document", exc_info=True)
            skipped.append(doc_id)

    # Diversify before taking the cap: one repetitive contract must not crowd
    # out a second contract that answers the other half of the question.
    merged = [rows[i] for i in range(max_results + 1) for rows in per_document if i < len(rows)]
    results = merged[:max_results]
    for index, row in enumerate(results, 1):
        row["n"] = index
    if next_cursor is None and cursor + MAX_DOCUMENTS < len(ids):
        next_cursor = cursor + MAX_DOCUMENTS
    complete = cursor == 0 and scanned == len(ids) and not skipped
    return {
        "status": "ok",
        "project": str(project.get("name") or "this project"),
        "terms": terms,
        "results": results,
        "coverage": {
            "documents_scanned": scanned,
            "documents_total": len(ids),
            "characters_scanned": chars,
            "documents_unavailable": len(skipped),
            "complete": complete,
            "cursor": cursor,
            "next_cursor": next_cursor,
        },
        "results_truncated": len(merged) > max_results,
        "message": (
            "Literal, case-insensitive matches in parsed text. Cite filename and page/block; refs are internal. "
            "Only excerpts enter the chat. No matches does not mean the concept is absent: try synonyms or semantic search."
        ),
        **(
            {
                "notice": "The scan is incomplete. Some documents were unavailable or a scan limit was reached. "
                "Do not claim an exhaustive absence."
            }
            if not complete
            else {}
        ),
    }


async def grep_project_documents(
    terms: list[str] | None = None, max_results: int = 8, cursor: int = 0, tool_context: ToolContext = None
) -> dict:
    """Find literal words or phrases across this project's parsed text, like grep.

    Use alongside search_project_documents, especially when semantic search
    misses. Supply short words/phrases and synonyms, e.g. ["curtail", "interrupt",
    "drawdown", "replenish"]. Matches ANY term, ignoring case; no regex. The
    server scans the text and returns bounded cited excerpts, never whole files.
    Prefer this over repeatedly opening every document. Ask for more context
    with get_document_content only for the specific source/section you need.
    A literal miss is not proof a concept is absent. Report incomplete coverage.
    With no terms, call bare to learn what input is needed; never invent one.

    Args:
        terms: 1-12 literal words or phrases. Each can contain up to 100 characters.
        max_results: Maximum passages (1-20, default 8; at most 800 characters each).
        cursor: Continue a partial scan using coverage.next_cursor; otherwise leave at 0.
    """
    try:
        literal_pattern(terms or [])
        if not 1 <= max_results <= MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS}.")
        if cursor < 0:
            raise ValueError("cursor must be 0 or a next_cursor returned by this tool.")
    except (ValueError, TypeError) as exc:
        return {"status": "needs_input", "message": str(exc), "results": []}
    uid = resolve_caller_uid(tool_context)
    project = await asyncio.to_thread(current_project, tool_context) if uid else None
    if not uid or project is None:
        return {
            "status": "error",
            "message": "Open one of your projects before searching its documents.",
            "results": [],
        }
    try:
        return await asyncio.to_thread(_search, uid, project, terms or [], max_results, cursor)
    except Exception:
        log.exception("project text search failed")
        return {"status": "error", "message": "Project text search failed. No complete scan was made.", "results": []}
