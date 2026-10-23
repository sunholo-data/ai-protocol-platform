"""Anchored comments + the table-row regression — v6.34.0 DOC-WORKBENCH P4.

Two things, both about what the MODEL actually sees.

**Comments.** From the 2026-08-06 UAT: *"Not only the comment is important but
also that it is able to read which piece of text is attached. If a comment says
'this is nonsense', it's important which part of the text is nonsense."* The
fixtures below use the real shape returned by `sample_docx_comments` — comments
nested as `children` of a `kind: "comment"` section, replies threaded by
`parent_id`.

**Tables.** A live bug this work uncovered: Firestore forbids nested arrays, so
a table's rows are stored as `[{"cells": [...]}]`, and the markdown renderer
iterated the row directly. Iterating a dict yields its KEYS, so every data row
of every document reached the agent as the literal string ``| cells |`` —
headers intact, all data gone. The frontend reads `row.cells` and drew the table
correctly, so the user saw the numbers while the model was blind to them.
"""

from __future__ import annotations

from tools.documents.ailang_parse import _flatten_block
from tools.documents.comments import comments_to_markdown, extract_comments, find_anchor
from tools.documents.context import blocks_to_markdown

# The shape the live API returns (verified 2026-09-01): comments nested under a
# section whose `kind` is "comment", with a threaded reply.
_COMMENTED_DOC = [
    {"type": "heading", "text": "Commercial Offer", "level": 1},
    {"type": "text", "text": "One more. And this is one with a comment in a comment."},
    {
        "type": "section",
        "kind": "comment",
        "children": [
            {
                "type": "comment",
                "text": "Do something.",
                "author": "Jesse Rosenthal",
                "date": "2016-06-22T14:35:00Z",
                "id": "3",
                "anchor_text": "comment in a comment",
                "anchor_kind": "range",
                "anchored": True,
                "anchor_block_index": 3,
            }
        ],
    },
    {
        "type": "section",
        "kind": "comment",
        "children": [
            {
                "type": "comment",
                "text": "Do something else.",
                "author": "Jesse Rosenthal",
                "id": "4",
                "anchor_text": "comment in a comment",
                "anchored": True,
                "parent_id": "3",
            }
        ],
    },
]


class TestExtraction:
    def test_finds_comments_nested_under_a_section(self):
        """A flat scan finds none — that is how this feature was first, wrongly,
        judged not to work."""
        comments = extract_comments(_COMMENTED_DOC)
        assert len(comments) == 2
        assert [c["text"] for c in comments] == ["Do something.", "Do something else."]

    def test_keeps_the_anchor_text(self):
        """The whole feature: WHICH passage the comment is about."""
        assert extract_comments(_COMMENTED_DOC)[0]["anchorText"] == "comment in a comment"

    def test_keeps_the_author(self):
        assert extract_comments(_COMMENTED_DOC)[0]["author"] == "Jesse Rosenthal"

    def test_threads_replies(self):
        assert extract_comments(_COMMENTED_DOC)[1]["parentId"] == "3"

    def test_a_document_with_no_comments_yields_none(self):
        assert extract_comments([{"type": "heading", "text": "x"}]) == []

    def test_survives_junk_in_the_block_list(self):
        assert extract_comments([None, "nonsense", {"type": "comment", "text": "ok"}]) == [
            {
                "id": "",
                "text": "ok",
                "author": "",
                "date": "",
                "anchorText": "",
                "anchored": False,
                "anchorBlockIndex": None,
                "parentId": "",
                "resolved": False,
            }
        ]


class TestRendering:
    def test_each_comment_names_the_passage_it_refers_to(self):
        md = comments_to_markdown(extract_comments(_COMMENTED_DOC))
        assert "Jesse Rosenthal" in md
        assert "Do something." in md
        assert 'refers to: "comment in a comment"' in md

    def test_it_tells_the_agent_to_change_only_that_passage(self):
        md = comments_to_markdown(extract_comments(_COMMENTED_DOC))
        assert "ONLY that passage" in md

    def test_a_reply_is_marked_as_one(self):
        md = comments_to_markdown(extract_comments(_COMMENTED_DOC))
        assert "reply to Jesse Rosenthal" in md

    def test_an_unanchored_comment_is_shown_and_flagged_not_dropped(self):
        """Dropping it hides feedback; presenting it as anchored lies about
        which passage the reviewer meant."""
        md = comments_to_markdown(extract_comments([{"type": "comment", "text": "vague", "author": "A"}]))
        assert "vague" in md
        assert "no anchor" in md

    def test_unresolved_comments_come_first(self):
        blocks = [
            {"type": "comment", "text": "done", "author": "A", "id": "1", "resolved": True},
            {"type": "comment", "text": "todo", "author": "B", "id": "2"},
        ]
        md = comments_to_markdown(extract_comments(blocks))
        assert md.index("todo") < md.index("done")

    def test_no_comments_renders_nothing(self):
        assert comments_to_markdown([]) == ""


class TestCommentsNeverRenderAsBodyText:
    """Before P4 the renderer's fallback printed comment text as a paragraph —
    author gone, anchor gone — so the model read a reviewer's note as if the
    DOCUMENT said it."""

    def test_a_comment_section_is_not_part_of_the_body(self):
        body = blocks_to_markdown(_COMMENTED_DOC)
        assert "Commercial Offer" in body
        assert "Do something." not in body

    def test_a_bare_comment_block_is_not_part_of_the_body(self):
        assert blocks_to_markdown([{"type": "comment", "text": "Do something."}]) == ""

    def test_an_ordinary_section_still_renders(self):
        body = blocks_to_markdown([{"type": "section", "children": [{"type": "text", "text": "kept"}]}])
        assert "kept" in body


class TestAnchorByText:
    def test_finds_the_block_containing_the_anchor(self):
        assert find_anchor(_COMMENTED_DOC, "comment in a comment") == 1

    def test_match_is_case_insensitive(self):
        assert find_anchor(_COMMENTED_DOC, "COMMENT IN A COMMENT") == 1

    def test_a_missing_anchor_returns_none(self):
        assert find_anchor(_COMMENTED_DOC, "nowhere in this document") is None

    def test_an_empty_anchor_returns_none(self):
        assert find_anchor(_COMMENTED_DOC, "   ") is None


class TestTableRowsReachTheModel:
    """The live bug: every data row rendered as `| cells |`."""

    def test_a_stored_table_renders_its_data(self):
        stored = _flatten_block(
            {
                "type": "table",
                "headers": ["Item", "Value"],
                "rows": [["Capacity", "5 GW"], ["Term", "10 years"]],
            }
        )
        md = blocks_to_markdown([stored])

        assert "| Capacity | 5 GW |" in md
        assert "| Term | 10 years |" in md
        assert "cells" not in md, "the row dict key leaked in place of the data"

    def test_a_table_that_never_round_tripped_still_renders(self):
        """A fresh parse handed straight to a tool carries the raw list shape."""
        md = blocks_to_markdown([{"type": "table", "headers": ["A"], "rows": [["x"]]}])
        assert "| x |" in md

    def test_cells_carrying_dicts_render_their_text(self):
        stored = _flatten_block({"type": "table", "headers": [], "rows": [[{"text": "hello"}]]})
        assert "| hello |" in blocks_to_markdown([stored])

    def test_a_malformed_row_does_not_crash_the_render(self):
        md = blocks_to_markdown([{"type": "table", "headers": ["A"], "rows": ["not-a-row", None]}])
        assert "| A |" in md
