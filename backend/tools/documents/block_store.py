"""Parsed blocks too large for one Firestore record.

A ``parsed_documents`` record carries its parsed blocks inline, and Firestore
refuses any record over 1 MiB. A spreadsheet of tagging specs or a long
Markdown export parses to more than that, and the write used to fail the whole
upload with a 500 ("cannot be written because its size (3,165,212 bytes)
exceeds the maximum allowed size") — seen 15 times in one folder upload on
2026-10-29, leaving each record stuck at ``pending`` with no content.

Over the limit, the blocks move to ``parsed_documents/{id}/block_chunks/{n}``
(each chunk well under 1 MiB) and the record keeps ``blocks: []`` plus
``blockChunks: n``. Chunks are written BEFORE the record that points at them,
so a reader never follows a count to chunks that are not there yet. Inline
records carry ``blockChunks: 0``, so a merge write over a chunked record
clears the pointer.

Content stays in Firestore under the same record: the same owner checks and
the same backend-only access as before, and no bucket to resolve (a shared
library import has no per-user bucket). Readers go through ``load_blocks``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from db.firestore import delete_document, get_document, set_document

log = logging.getLogger(__name__)

_COLLECTION = "parsed_documents"
_CHUNKS = "block_chunks"

# JSON size is an estimate of Firestore's own accounting (numbers cost 8 bytes
# there, field names repeat per block), so both limits leave a wide margin
# under 1 MiB.
INLINE_MAX_BYTES = 700_000
CHUNK_MAX_BYTES = 600_000


class BlockTooLargeError(ValueError):
    """One block alone is over the record limit — it cannot be stored."""


def chunks_collection(doc_id: str) -> str:
    return f"{_COLLECTION}/{doc_id}/{_CHUNKS}"


def _size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode())


# json.dumps' default item separator is ", ": two bytes between list items.
_SEP = 2


def _split_oversized(block: Any, limit: int = CHUNK_MAX_BYTES) -> list[Any]:
    """``block`` as one or more blocks, each at most ``limit`` bytes.

    A spreadsheet parses to one section per sheet, so one sheet can be bigger
    than a chunk (five sheets of 0.6-1.7 MB on 2026-10-30). A section becomes
    consecutive sections over runs of its children; a table becomes tables over
    runs of its rows, each repeating the headers. A child is split to fit
    INSIDE its parent's wrapper, and a run is measured with the separators
    between its items: without both, a 994-row table split into pieces 1.6 KB
    over the limit, which the enclosing section then refused. Anything else
    that big cannot be split and is refused.
    """
    size = _size(block)
    if size <= limit:
        return [block]
    if isinstance(block, dict):
        for key in ("children", "rows"):
            items = block.get(key) or []
            if not items:
                continue
            shell = {k: v for k, v in block.items() if k != key}
            room = limit - _size({**shell, key: []})
            pieces = [p for item in items for p in (_split_oversized(item, room) if key == "children" else [item])]
            out: list[Any] = []
            run: list[Any] = []
            run_size = 0
            for piece in pieces:
                piece_size = _size(piece)
                if piece_size > room:
                    raise BlockTooLargeError(f"one part of this document is {piece_size:,} bytes, too large to store")
                added = piece_size + (_SEP if run else 0)
                if run and run_size + added > room:
                    out.append({**shell, key: run})
                    run, run_size, added = [], 0, piece_size
                run.append(piece)
                run_size += added
            if run:
                out.append({**shell, key: run})
            return out
    raise BlockTooLargeError(f"one part of this document is {size:,} bytes, too large to store")


def split_blocks(blocks: list[Any]) -> list[list[Any]]:
    """Consecutive runs of blocks, each under ``CHUNK_MAX_BYTES`` (an
    oversized block is first split into several — see ``_split_oversized``)."""
    parts: list[list[Any]] = []
    current: list[Any] = []
    current_size = 0
    for block in (piece for b in blocks for piece in _split_oversized(b)):
        size = _size(block)
        if current and current_size + size > CHUNK_MAX_BYTES:
            parts.append(current)
            current, current_size = [], 0
        current.append(block)
        current_size += size
    if current:
        parts.append(current)
    return parts


def prepare_record(doc_id: str, record: dict[str, Any]) -> int:
    """Move ``record["blocks"]`` into chunks when the record would be too large.

    Mutates ``record``: sets ``blockChunks`` (0 when the blocks stay inline) and
    empties ``blocks`` when they moved. Writes the chunks; the caller writes the
    record after, then calls ``drop_stale_chunks`` with the previous count.
    Returns the new chunk count.
    """
    blocks = record.get("blocks") or []
    if not blocks or _size(record) <= INLINE_MAX_BYTES:
        record["blockChunks"] = 0
        return 0
    parts = split_blocks(blocks)
    if record.get("blockCount") is not None:
        # An oversized sheet or table may have been split into several blocks.
        record["blockCount"] = sum(len(p) for p in parts)
    for i, part in enumerate(parts):
        set_document(chunks_collection(doc_id), str(i), {"index": i, "blocks": part})
    record["blocks"] = []
    record["blockChunks"] = len(parts)
    log.info("parsed document %s: %d blocks stored in %d chunks", doc_id, len(blocks), len(parts))
    return len(parts)


def drop_stale_chunks(doc_id: str, *, keep: int, had: int) -> None:
    """Delete chunks ``keep..had-1`` (a re-parse that came out smaller, or a
    deleted document). Best-effort: a leftover chunk is unreachable, not wrong."""
    for i in range(keep, had):
        try:
            delete_document(chunks_collection(doc_id), str(i))
        except Exception as exc:
            log.warning("parsed document %s: could not delete block chunk %d: %s", doc_id, i, exc)


def load_blocks(doc_id: str, record: dict[str, Any] | None) -> list[Any]:
    """A record's blocks, inline or chunked. Raises when a chunk is missing —
    half a document read as if it were the whole one is worse than an error."""
    if not record:
        return []
    blocks = record.get("blocks") or []
    count = int(record.get("blockChunks") or 0)
    if blocks or count <= 0:
        return list(blocks)
    out: list[Any] = []
    for i in range(count):
        chunk = get_document(chunks_collection(doc_id), str(i))
        if chunk is None:
            raise LookupError(f"parsed document {doc_id}: block chunk {i} of {count} is missing")
        out.extend(chunk.get("blocks") or [])
    return out
