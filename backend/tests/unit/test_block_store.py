"""Parsed blocks over the Firestore record limit — tools/documents/block_store.py.

A record over 1 MiB used to fail its upload with a 500 and stay ``pending``
forever (15 files in one folder upload, 2026-10-29). Over the limit the blocks
move to ``parsed_documents/{id}/block_chunks``; every reader goes through
``load_blocks``. Runs against the in-memory Firestore so real paths execute.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from db.firestore import get_document, set_document
from tools.documents import block_store


@pytest.fixture()
def store(monkeypatch):
    import db.firestore as fs
    from db.firestore_inmemory import InMemoryFirestoreClient

    client = InMemoryFirestoreClient()
    monkeypatch.setattr(fs, "_client", client)
    return client


def _blocks(n: int, size: int = 1000) -> list[dict]:
    return [{"type": "paragraph", "text": f"{i:06d} " + "x" * size} for i in range(n)]


def _store(doc_id: str, blocks: list[dict], status: str = "parsed") -> None:
    from tools.documents.ingest import _ParseResult, _store_document

    _store_document(
        doc_id,
        user_id="alice",
        skill_id="",
        gs_url="gs://b/users/alice/docs/f1/specs.xlsx",
        storage_path="users/alice/docs/f1/specs.xlsx",
        original_filename="specs.xlsx",
        source_format="xlsx",
        folder_id="f1",
        parse_result=_ParseResult(status, blocks, None, 10),
        now=datetime(2026, 10, 29, tzinfo=UTC),
    )


def test_small_document_stays_inline(store):
    _store("d1", _blocks(3))
    rec = get_document("parsed_documents", "d1")
    assert len(rec["blocks"]) == 3
    assert rec["blockChunks"] == 0
    assert block_store.load_blocks("d1", rec) == _blocks(3)


def test_large_document_is_chunked_and_reads_back_whole(store):
    blocks = _blocks(3000)  # ~3 MB, like the tagging-spec spreadsheets
    _store("d1", blocks)
    rec = get_document("parsed_documents", "d1")
    assert rec["blocks"] == []
    assert rec["blockChunks"] > 1
    assert rec["blockCount"] == 3000
    assert rec["parseStatus"] == "parsed"
    assert block_store._size(rec) < block_store.INLINE_MAX_BYTES
    for i in range(rec["blockChunks"]):
        chunk = get_document(block_store.chunks_collection("d1"), str(i))
        assert block_store._size(chunk) < 1_048_576
    assert block_store.load_blocks("d1", rec) == blocks


def test_reparse_that_shrinks_drops_the_old_chunks(store):
    _store("d1", _blocks(3000))
    had = get_document("parsed_documents", "d1")["blockChunks"]
    _store("d1", _blocks(3))
    rec = get_document("parsed_documents", "d1")
    assert rec["blockChunks"] == 0 and len(rec["blocks"]) == 3
    for i in range(had):
        assert get_document(block_store.chunks_collection("d1"), str(i)) is None


def test_one_block_too_large_to_store_marks_the_document_failed(store):
    _store("d1", [{"type": "table", "text": "x" * (block_store.CHUNK_MAX_BYTES + 10)}] * 2)
    rec = get_document("parsed_documents", "d1")
    assert rec["parseStatus"] == "failed"
    assert "too large to store" in rec["parseError"]
    assert rec["blocks"] == []


def test_a_missing_chunk_is_an_error_not_half_a_document(store):
    set_document("parsed_documents", "d1", {"blocks": [], "blockChunks": 2})
    set_document(block_store.chunks_collection("d1"), "0", {"index": 0, "blocks": [{"text": "a"}]})
    with pytest.raises(LookupError):
        block_store.load_blocks("d1", get_document("parsed_documents", "d1"))


def test_readers_see_chunked_blocks(store):
    from auth import User
    from tools.documents.context import build_document_context
    from tools.documents.parsed_text import parsed_text_from_record
    from tools.documents.routes import get_document as get_document_route

    blocks = _blocks(3000)
    _store("d1", blocks)
    rec = get_document("parsed_documents", "d1")

    assert "000000" in parsed_text_from_record("d1", rec, caller_uid="alice").text
    _content, got = build_document_context("d1", mode="blocks", caller_uid="alice")
    assert got is not None and len(got) == 3000
    body = get_document_route("d1", User(uid="alice", email="alice@example.com", domain="example.com"))
    assert len(body["blocks"]) == 3000


def test_deleting_the_document_deletes_its_chunks(store, monkeypatch):
    from auth import User
    from tools.documents import routes

    monkeypatch.setattr(routes, "_adjust_counts", lambda *a, **k: None)
    monkeypatch.setattr(routes, "document_event", lambda *a, **k: None)
    monkeypatch.setattr("db.projects.on_document_deleted", lambda *a, **k: None)
    _store("d1", _blocks(3000))
    rec = get_document("parsed_documents", "d1")
    rec["storagePath"] = ""  # no GCS object in this test
    routes._delete_owned_document("d1", rec, User(uid="alice", email="alice@example.com", domain="example.com"))
    assert get_document(block_store.chunks_collection("d1"), "0") is None


def test_an_oversized_sheet_is_split_into_sections_and_tables():
    """One spreadsheet sheet is one section block; five were 0.6-1.7 MB."""
    rows = [{"cells": [{"text": f"r{i}"}, {"text": "x" * 2000}]} for i in range(800)]  # ~1.7 MB
    sheet = {
        "type": "section",
        "kind": "sheet",
        "name": "Q1",
        "children": [
            {"type": "heading", "text": "Q1", "level": 1},
            {"type": "table", "headers": [{"text": "KPI"}, {"text": "Spec"}], "rows": rows},
        ],
    }
    parts = block_store.split_blocks([sheet])
    flat = [b for p in parts for b in p]
    assert len(flat) > 1 and all(b["type"] == "section" and b["name"] == "Q1" for b in flat)
    assert all(block_store._size(b) <= block_store.CHUNK_MAX_BYTES for b in flat)
    tables = [c for b in flat for c in b["children"] if c["type"] == "table"]
    assert all(t["headers"][0]["text"] == "KPI" for t in tables)
    assert [r["cells"][0]["text"] for t in tables for r in t["rows"]] == [f"r{i}" for i in range(800)]


def test_a_split_document_stores_and_reads_back_with_its_new_count(store):
    rows = [{"cells": [{"text": f"r{i}"}, {"text": "x" * 2000}]} for i in range(800)]
    sheet = {"type": "section", "name": "Q1", "children": [{"type": "table", "rows": rows}]}
    _store("d1", [sheet])
    rec = get_document("parsed_documents", "d1")
    assert rec["parseStatus"] == "parsed" and rec["blockChunks"] >= 1
    blocks = block_store.load_blocks("d1", rec)
    assert rec["blockCount"] == len(blocks) > 1
    assert sum(len(c["rows"]) for b in blocks for c in b["children"]) == 800


def test_a_sheet_just_over_the_limit_splits_inside_its_section():
    """The real failure: a 994-row table (~645 KB) in a sheet section split into
    pieces 1.6 KB over the limit (separators and the wrapper uncounted), which
    the section then refused: "one part of this document is 601,617 bytes"."""
    headers = [{"text": f"col{i}"} for i in range(37)]
    rows = [{"cells": [{"text": f"r{i}"}, {"text": "x" * 600}]} for i in range(994)]
    sheet = {
        "type": "section",
        "kind": "sheet",
        "name": "Spec",
        "children": [
            {"type": "heading", "text": "Spec", "level": 1},
            {"type": "table", "headers": headers, "rows": rows},
        ],
    }
    assert block_store._size(sheet) > block_store.CHUNK_MAX_BYTES
    flat = [b for p in block_store.split_blocks([sheet]) for b in p]
    assert all(block_store._size(b) <= block_store.CHUNK_MAX_BYTES for b in flat)
    got = [r["cells"][0]["text"] for b in flat for c in b["children"] if c["type"] == "table" for r in c["rows"]]
    assert got == [f"r{i}" for i in range(994)]
