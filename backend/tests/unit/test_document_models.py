"""Unit tests for db.models.document — the ParsedDocument contract.

Contract lock for Phase 1B document-ui. If these tests break, the Firestore
schema at docs/design/v6.0.0/document-ui.md:291-336 and the generated
contracts/document.schema.json need to be reviewed together.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from db.models.document import (
    Block,
    DocMetadata,
    DocSummary,
    EditedBlock,
    ParsedDocument,
)

_AILANG_PARSE_AVAILABLE = importlib.util.find_spec("ailang_parse") is not None


def _make_minimal_doc(**overrides) -> ParsedDocument:
    defaults: dict = {
        "skillId": "skill-abc",
        "userId": "user-123",
        "sourceUrl": "gs://bucket/doc.docx",
        "sourceFormat": "docx",
        "originalFilename": "doc.docx",
        "storagePath": "users/u/docs/doc.docx",
    }
    defaults.update(overrides)
    return ParsedDocument.model_validate(defaults)


def test_block_type_validation():
    """Valid block types accepted; unknown types rejected."""
    b = Block(type="heading", text="Title")
    assert b.type == "heading"
    assert b.text == "Title"

    b2 = Block(type="table", properties={"headers": ["A", "B"], "rows": [["1", "2"]]})
    assert b2.properties["headers"] == ["A", "B"]

    with pytest.raises(ValidationError):
        Block(type="not-a-real-block-type")


def test_doc_metadata_all_optional():
    """An empty DocMetadata is valid — all fields default to None."""
    meta = DocMetadata()
    assert meta.title is None
    assert meta.author is None
    assert meta.page_count is None

    # camelCase alias accepted
    meta2 = DocMetadata.model_validate({"pageCount": 42, "author": "Ada"})
    assert meta2.page_count == 42
    assert meta2.author == "Ada"


def test_parsed_document_status_literal():
    """Only the five documented status values are accepted."""
    for status in ("pending", "parsing", "parsed", "failed", "edited"):
        doc = _make_minimal_doc(status=status)
        assert doc.status == status

    with pytest.raises(ValidationError):
        _make_minimal_doc(status="done")


def test_parsed_document_round_trip_camelcase():
    """Dump with by_alias=True → reload preserves all fields."""
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    doc = _make_minimal_doc(
        status="parsed",
        parsedAt=now,
        metadata=DocMetadata(title="Quarterly Report", pageCount=10),
        summary=DocSummary(totalBlocks=42, headings=5, tables=2),
        blocks=[Block(type="heading", text="Intro"), Block(type="paragraph", text="Body")],
        a2uiRoot="root-1",
        a2uiComponents=[{"id": "root-1", "type": "Column"}],
        createdAt=now,
        updatedAt=now,
    )

    payload = doc.model_dump(by_alias=True, mode="json")
    assert "skillId" in payload
    assert "a2uiRoot" in payload
    assert "pageCount" in payload["metadata"]
    assert "totalBlocks" in payload["summary"]

    reloaded = ParsedDocument.model_validate(payload)
    assert reloaded.status == "parsed"
    assert reloaded.metadata.page_count == 10
    assert reloaded.summary.total_blocks == 42
    assert reloaded.a2ui_root == "root-1"
    assert len(reloaded.blocks) == 2
    assert reloaded.blocks[0].type == "heading"


def test_edited_blocks_sparse_map():
    """editedBlocks is a sparse dict keyed by stringified block index."""
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    edits = {
        "3": EditedBlock(
            originalText="old",
            editedText="new",
            editedAt=now,
            editedBy="user-123",
        ),
        "17": EditedBlock(
            originalText="foo",
            editedText="bar",
            editedAt=now,
            editedBy="user-123",
        ),
    }
    doc = _make_minimal_doc(status="edited", editedBlocks=edits)

    assert set(doc.edited_blocks.keys()) == {"3", "17"}
    assert doc.edited_blocks["3"].edited_text == "new"

    # Round-trip preserves camelCase alias
    payload = doc.model_dump(by_alias=True, mode="json")
    assert payload["editedBlocks"]["3"]["originalText"] == "old"
    assert payload["editedBlocks"]["3"]["editedBy"] == "user-123"


@pytest.mark.skipif(not _AILANG_PARSE_AVAILABLE, reason="ailang-parse not installed")
def test_fixture_from_ailang_parse():
    """ailang-parse Block dataclass shapes validate against our Block model.

    We don't hit the network — we build an ailang_parse.Block directly from a
    dict (mirroring what the parse_url("blocks") response would contain) and
    confirm our Pydantic Block accepts the same dict payload.
    """
    from ailang_parse import Block as AilangBlock

    raw_payloads = [
        {"type": "heading", "text": "Section 1", "level": 1},
        {"type": "paragraph", "text": "Hello world."},
        {
            "type": "table",
            "headers": [{"text": "Col A"}, {"text": "Col B"}],
            "rows": [[{"text": "1"}, {"text": "2"}]],
        },
    ]

    for payload in raw_payloads:
        # ailang-parse accepts the raw dict
        ab = AilangBlock.from_dict(payload)
        assert ab.type == payload["type"]

        # Our permissive Block also accepts the same dict (extras bagged via extra="allow")
        pb = Block.model_validate(payload)
        assert pb.type == payload["type"]
