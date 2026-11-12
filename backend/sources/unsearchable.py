"""Files a source never indexes because they can't add anything searchable
(github-sources.md §9). One rule for project and knowledge-base sources.

* **Empty files** — nothing to search; indexing them only records a failure.
* **Parser outputs next to their original** — running a document parser locally
  leaves ``proposal.docx.json`` / ``proposal.docx.md`` beside ``proposal.docx``,
  or in a ``parsed/`` folder under it. Committed, they are the same content a
  second time (the original is indexed already), and a JSON dump can be too large
  to store at all. A file is an output only when the original is in the same
  source — ``<name>.<format>.<json|md>`` with ``<name>.<format>`` in its folder or
  in the folder above a ``parsed/`` folder — so a real document can't match by
  accident, and an output whose original isn't synced (the only text copy) stays.
* **A parser's JSON beside its Markdown twin** — when the original wasn't kept,
  ``x.docx.md`` is that copy, readable; ``x.docx.json`` is the same content as a
  structure dump (on Sunholo, one too large to store). The Markdown stays.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath

# Document formats a parser reads, and the formats it writes beside them.
_PARSED_FROM = {
    ".docx",
    ".doc",
    ".pptx",
    ".xlsx",
    ".odt",
    ".odp",
    ".ods",
    ".pdf",
    ".html",
    ".htm",
    ".md",
    ".epub",
    ".eml",
}
_PARSED_TO = {".json", ".md"}
_OUTPUT_DIRS = {"parsed"}


def parser_original(path: str, paths: Collection[str]) -> str | None:
    """The document ``path`` was parsed from, when that document is in ``paths``."""
    p = PurePosixPath(path)
    if p.suffix.lower() not in _PARSED_TO:
        return None
    original_name = p.stem  # "proposal.docx" for "proposal.docx.json"
    if PurePosixPath(original_name).suffix.lower() not in _PARSED_FROM:
        return None
    folders = [p.parent]
    if p.parent.name.lower() in _OUTPUT_DIRS:
        folders.append(p.parent.parent)
    for folder in folders:
        candidate = str(folder / original_name) if str(folder) != "." else original_name
        if candidate in paths:
            return candidate
    return None


def markdown_twin(path: str, paths: Collection[str]) -> str | None:
    """``x.docx.md`` for a parser's ``x.docx.json``, when that is in ``paths``."""
    p = PurePosixPath(path)
    if p.suffix.lower() != ".json" or PurePosixPath(p.stem).suffix.lower() not in _PARSED_FROM:
        return None
    twin = str(p.with_suffix(".md"))
    return twin if twin in paths else None


def unsearchable_reason(path: str, size: int, paths: Collection[str]) -> str | None:
    """Why ``path`` is skipped as unsearchable, or None. ``paths`` is every path
    in the same listing, so an output is recognised by its original or its twin."""
    if size == 0:
        return "empty file"
    original = parser_original(path, paths)
    if original:
        return f"parser output of {PurePosixPath(original).name}, which is synced itself"
    twin = markdown_twin(path, paths)
    if twin:
        return f"parser output; its Markdown version {PurePosixPath(twin).name} is synced"
    return None
