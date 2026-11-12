"""The YAML header of a Markdown file, kept for the agent.

AILANG Parse reads a Markdown file's header for ``title`` and drops the rest.
In an Open Knowledge Format bundle (and most docs sites and wikis) the header
says what the document is: ``type``, ``description``, ``tags``, ``resource``.
Ingest keeps the header text on the record (``frontmatter``) and
``get_document_content`` shows it above the body (tools/documents/context.py).
"""

from __future__ import annotations

import re

import yaml

# Formats AILANG Parse reads as Markdown. The plain-text source formats (.mdx,
# .qmd) already reach the agent verbatim, header included.
MARKDOWN_EXTENSIONS = {".md"}
MAX_CHARS = 4000

_HEADER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def markdown_frontmatter(file_bytes: bytes, ext: str) -> str:
    """The header's YAML text, or "" when there is none (or it is not a mapping:
    a file that merely opens with a horizontal rule has no header)."""
    if ext.lower() not in MARKDOWN_EXTENSIONS:
        return ""
    try:
        text = file_bytes[: MAX_CHARS * 4].decode("utf-8")
    except UnicodeDecodeError:
        return ""
    match = _HEADER.match(text)
    if not match:
        return ""
    header = match.group(1).strip()
    try:
        parsed = yaml.safe_load(header)
    except yaml.YAMLError:
        return ""
    if not isinstance(parsed, dict) or not parsed:
        return ""
    if len(header) > MAX_CHARS:
        header = header[:MAX_CHARS].rsplit("\n", 1)[0] + "\n# … (header truncated)"
    return header


def stored_frontmatter(doc_id: str, raw: dict) -> str:
    """The record's header. A Markdown record stored before headers were kept
    has no ``frontmatter`` field: read the original's first bytes once, save
    the result ("" included, so it is never read again) and return it. A sync
    leaves byte-identical files alone, forced or not, so without this the
    header of every already-synced file would stay missing."""
    if "frontmatter" in raw:
        return str(raw.get("frontmatter") or "")
    if f".{str(raw.get('sourceFormat') or '').lower()}" not in MARKDOWN_EXTENSIONS:
        return ""
    gs_url = str(raw.get("sourceUrl") or "")
    if not gs_url.startswith("gs://"):
        return ""
    import logging

    from google.cloud import storage as gcs

    from db.firestore import update_document
    from tools.documents.ailang_parse import split_gs_url

    log = logging.getLogger(__name__)
    try:
        bucket, path = split_gs_url(gs_url)
        data = gcs.Client().bucket(bucket).blob(path).download_as_bytes(start=0, end=MAX_CHARS * 4 - 1)
    except Exception as exc:
        log.warning("frontmatter: could not read %s for %s: %s", gs_url, doc_id, exc)
        return ""
    header = markdown_frontmatter(data, ".md")
    try:
        update_document("parsed_documents", doc_id, {"frontmatter": header})
    except Exception as exc:
        log.warning("frontmatter: could not save the header of %s: %s", doc_id, exc)
    return header
