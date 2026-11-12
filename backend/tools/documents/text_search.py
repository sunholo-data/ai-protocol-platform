"""Bounded literal search of parsed blocks, including tables and nested sections.

This module never calls a model or a semantic index. Its input is already
authorised text; only small matching windows are returned to the caller.
"""

from __future__ import annotations

import re
from typing import Any

from tools.documents.outline import build_structure, heading_path

MAX_TERMS = 12
MAX_TERM_CHARS = 100
MAX_SNIPPET_CHARS = 800
MAX_RESULTS = 20


def literal_pattern(terms: list[str]) -> re.Pattern[str]:
    if not isinstance(terms, list) or not terms or len(terms) > MAX_TERMS or any(not isinstance(t, str) for t in terms):
        raise ValueError(f"Supply 1-{MAX_TERMS} literal words or phrases, not a whole question.")
    cleaned = list(dict.fromkeys(t.strip() for t in terms))
    if any(not t or len(t) > MAX_TERM_CHARS for t in cleaned):
        raise ValueError(f"Each search term must contain 1-{MAX_TERM_CHARS} characters.")
    # Literal strings only. Punctuation is never interpreted as a regex.
    return re.compile("|".join(re.escape(t) for t in cleaned), re.IGNORECASE)


def matching_passages(
    blocks: list[dict[str, Any]], pattern: re.Pattern[str], *, limit: int = MAX_RESULTS
) -> list[dict[str, Any]]:
    """One context window per match; never return a whole large block.

    Each hit says where it is: the outline section id and heading path it falls
    under, and the printed page when the document has page markers, so the agent
    can cite it and open the surrounding section with
    get_document_content(section=<id>) instead of the whole document. Long
    headings are clipped in the path; the section id is exact.
    """
    structure = build_structure(blocks)
    results = []
    for leaf in structure.leaves:
        text = leaf.text
        if not text:
            continue
        position = 0
        while match := pattern.search(text, position):
            start = max(0, match.start() - 200)
            end = min(len(text), start + MAX_SNIPPET_CHARS - 2)
            snippet = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
            result: dict[str, Any] = {"block_id": leaf.block_id, "snippet": snippet, "matched": match.group()}
            if leaf.page is not None:
                result["page"] = leaf.page
            if leaf.node is not None:
                result["section"] = leaf.node.id
                result["heading_path"] = heading_path(leaf.node)
            results.append(result)
            if len(results) >= limit:
                return results
            position = end
            if position >= len(text):
                break
    return results
