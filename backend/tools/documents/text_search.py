"""Bounded literal search of parsed blocks, including tables and nested sections.

This module never calls a model or a semantic index. Its input is already
authorised text; only small matching windows are returned to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from tools.documents.context import blocks_to_markdown

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


def text_blocks(
    blocks: list[dict[str, Any]], *, parent_page: Any = None, prefix: str = ""
) -> Iterator[tuple[str, Any, str]]:
    for index, block in enumerate(blocks):
        block_id = str(block.get("block_id") or block.get("id") or f"{prefix}{index}")
        page = block.get("page") or block.get("page_number") or block.get("pageNumber") or parent_page
        if block.get("type") == "comment" or block.get("kind") == "comment":
            continue
        if block.get("type") == "section" and block.get("children"):
            yield from text_blocks(block["children"], parent_page=page, prefix=f"{block_id}.")
        else:
            text = blocks_to_markdown([block])
            if text:
                yield block_id, page, text


def matching_passages(
    blocks: list[dict[str, Any]], pattern: re.Pattern[str], *, limit: int = MAX_RESULTS
) -> list[dict[str, Any]]:
    """One context window per matching block; never return a whole large block."""
    results = []
    for block_id, page, text in text_blocks(blocks):
        position = 0
        while match := pattern.search(text, position):
            start = max(0, match.start() - 200)
            end = min(len(text), start + MAX_SNIPPET_CHARS - 2)
            snippet = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
            result: dict[str, Any] = {"block_id": block_id, "snippet": snippet, "matched": match.group()}
            if page is not None:
                result["page"] = page
            results.append(result)
            if len(results) >= limit:
                return results
            position = end
            if position >= len(text):
                break
    return results
