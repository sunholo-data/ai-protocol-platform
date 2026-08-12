"""Tests for tools/documents/context.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

_SAMPLE_BLOCKS = [
    {"type": "heading", "text": "Introduction", "level": 1},
    {"type": "paragraph", "text": "This is a paragraph."},
    {"type": "heading", "text": "Data", "level": 2},
    {"type": "table", "headers": [{"text": "A"}, {"text": "B"}], "rows": [[{"text": "1"}, {"text": "2"}]]},
    {"type": "list", "items": ["item one", "item two"], "ordered": False},
    {"type": "change", "text": "deleted text", "change_type": "deletion"},
]


class TestBlocksToMarkdown:
    def test_heading(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "heading", "text": "Hello", "level": 2}])
        assert "## Hello" in out

    def test_paragraph(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "paragraph", "text": "Some text."}])
        assert "Some text." in out

    def test_table(self):
        from tools.documents.context import blocks_to_markdown

        block = {
            "type": "table",
            "headers": [{"text": "Col1"}, {"text": "Col2"}],
            "rows": [[{"text": "val1"}, {"text": "val2"}]],
        }
        out = blocks_to_markdown([block])
        assert "Col1" in out
        assert "val1" in out
        assert "|" in out

    def test_list_unordered(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "list", "items": ["a", "b"], "ordered": False}])
        assert "- a" in out

    def test_list_ordered(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "list", "items": ["first", "second"], "ordered": True}])
        assert "1. first" in out

    def test_deletion_strikethrough(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "change", "text": "removed", "change_type": "deletion"}])
        assert "~~removed~~" in out

    def test_insertion(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([{"type": "change", "text": "added", "change_type": "insertion"}])
        assert "added" in out
        assert "INSERTED" in out

    def test_empty_blocks(self):
        from tools.documents.context import blocks_to_markdown

        out = blocks_to_markdown([])
        assert out == ""


class TestApplyEdits:
    def test_no_edits(self):
        from tools.documents.context import apply_edits

        blocks = [{"type": "paragraph", "text": "original"}]
        result = apply_edits(blocks, {})
        assert result[0]["text"] == "original"

    def test_edit_applied(self):
        from tools.documents.context import apply_edits

        blocks = [{"type": "paragraph", "text": "original"}]
        edits = {"0": {"editedText": "modified"}}
        result = apply_edits(blocks, edits)
        assert result[0]["text"] == "modified"

    def test_edit_preserves_other_blocks(self):
        from tools.documents.context import apply_edits

        blocks = [{"type": "paragraph", "text": "first"}, {"type": "paragraph", "text": "second"}]
        edits = {"0": {"editedText": "FIRST"}}
        result = apply_edits(blocks, edits)
        assert result[0]["text"] == "FIRST"
        assert result[1]["text"] == "second"


class TestBuildDocumentContext:
    def _fake_firestore_doc(self):
        return {
            "originalFilename": "report.docx",
            "blocks": _SAMPLE_BLOCKS,
            "editedBlocks": {},
            "metadata": {"title": "Annual Report", "author": "Jane", "pageCount": 5},
            "parseStatus": "parsed",
        }

    def test_markdown_mode(self):
        from tools.documents.context import build_document_context

        with patch("tools.documents.context.get_document", return_value=self._fake_firestore_doc()):
            content, blocks = build_document_context("doc123", mode="markdown")
        assert "report.docx" in content
        assert "Introduction" in content
        assert blocks is None

    def test_blocks_mode(self):
        import json

        from tools.documents.context import build_document_context

        with patch("tools.documents.context.get_document", return_value=self._fake_firestore_doc()):
            content, blocks = build_document_context("doc123", mode="blocks")
        parsed = json.loads(content)
        assert parsed["docId"] == "doc123"
        assert isinstance(parsed["blocks"], list)
        assert blocks is not None

    def test_not_found(self):
        from tools.documents.context import build_document_context

        with patch("tools.documents.context.get_document", return_value=None):
            with pytest.raises(KeyError, match="doc999"):
                build_document_context("doc999")

    def test_section_filter(self):
        from tools.documents.context import build_document_context

        with patch("tools.documents.context.get_document", return_value=self._fake_firestore_doc()):
            content, _ = build_document_context("doc123", mode="markdown", section="Introduction")
        assert "Introduction" in content
        # Data section should not appear
        assert "Data" not in content

    def test_metadata_in_markdown(self):
        from tools.documents.context import build_document_context

        with patch("tools.documents.context.get_document", return_value=self._fake_firestore_doc()):
            content, _ = build_document_context("doc123", mode="markdown")
        assert "Jane" in content
        assert "5" in content  # page count
