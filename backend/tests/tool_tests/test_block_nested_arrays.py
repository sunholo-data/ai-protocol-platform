"""Blocks must be storable in Firestore — regression for a LIVE bug (2026-09-01).

Firestore rejects an array whose elements are themselves arrays:

    400 Property array contains an invalid nested entity.

`_flatten_block` used to name the offending fields one at a time — it handled
`rows` and recursed `children`, and MISSED `item_runs`
(`List[List[InlineRun]]`), which the parser populates whenever a list item
carries inline formatting.

Consequence: **any .docx with a bold or italic bullet failed its Firestore
write**, losing the whole document. Present since at least SDK 0.12.0, so it had
been silently breaking uploads; document generation just hit it constantly,
because a model writing markdown emphasises bullets by habit.

Caught in the browser on test, not by any test — hence these.
"""

from __future__ import annotations

from typing import Any

from tools.documents.ailang_parse import _flatten_block


def _nested_arrays(value: Any, path: str = "") -> list[str]:
    """Every place a list sits directly inside a list — what Firestore refuses."""
    hits: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            hits += _nested_arrays(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, list):
                hits.append(f"{path}[{i}]")
            hits += _nested_arrays(item, f"{path}[{i}]")
    return hits


class TestTheBugThatShipped:
    def test_item_runs_is_flattened(self):
        """The exact shape that 400'd on test."""
        block = {
            "type": "list",
            "items": ["Bold bullet with emphasis"],
            "item_runs": [[{"text": "Bold bullet", "bold": True}, {"text": " with emphasis"}]],
        }
        assert _nested_arrays(block), "fixture must contain the bug"
        assert _nested_arrays(_flatten_block(block)) == []

    def test_a_formatted_bullet_list_is_storable(self):
        block = {
            "type": "list",
            "items": ["one", "two"],
            "item_runs": [
                [{"text": "one", "bold": True}],
                [{"text": "two", "italic": True}],
            ],
        }
        flat = _flatten_block(block)
        assert _nested_arrays(flat) == []
        # Content survives the wrapping — this is a reshape, not a drop.
        assert flat["item_runs"][0]["items"][0]["text"] == "one"
        assert flat["item_runs"][1]["items"][0]["italic"] is True


class TestExistingContractsSurvive:
    def test_table_rows_keep_their_cells_wrapper(self):
        """The frontend reads `row.cells` (BlocksRenderer, StructuredDocPreview);
        changing that shape would silently blank every table."""
        block = {"type": "table", "rows": [["a", "b"], ["c", "d"]]}
        flat = _flatten_block(block)
        assert flat["rows"] == [{"cells": ["a", "b"]}, {"cells": ["c", "d"]}]

    def test_children_are_still_walked(self):
        block = {
            "type": "section",
            "children": [{"type": "table", "rows": [["x"]]}],
        }
        flat = _flatten_block(block)
        assert flat["children"][0]["rows"] == [{"cells": ["x"]}]
        assert _nested_arrays(flat) == []

    def test_a_block_with_nothing_nested_is_unchanged(self):
        block = {"type": "heading", "text": "Title", "level": 1, "items": ["a", "b"]}
        assert _flatten_block(block) == block


class TestItIsStructuralNotFieldByField:
    """The point of the fix: the NEXT parser field cannot reintroduce this."""

    def test_an_unknown_nested_field_is_handled(self):
        block = {"type": "future", "some_new_field": [[1, 2], [3, 4]]}
        flat = _flatten_block(block)
        assert _nested_arrays(flat) == []
        assert flat["some_new_field"][0]["items"] == [1, 2]

    def test_nesting_inside_table_cells_is_handled(self):
        """`rows` cells were never recursed into before."""
        block = {"type": "table", "rows": [[{"runs": [["deep"]]}]]}
        flat = _flatten_block(block)
        assert _nested_arrays(flat) == []

    def test_triple_nesting_is_handled(self):
        block = {"type": "x", "f": [[[1]]]}
        assert _nested_arrays(_flatten_block(block)) == []


class TestIngestFailureIsVisible:
    """The second half of the incident: the Firestore 400 escaped the tool and
    killed the ADK run ("Background execution error"), so the user got a dead
    chat turn with no explanation. A save failure is still a RESULT."""

    async def _run(self, side_effect):
        from unittest.mock import patch

        from tools.documents.ailang_parse import ConvertOutcome

        ok = ConvertOutcome(content=b"PK\x03\x04", filename="x.docx", target="docx", source_markdown="# Hi")

        class _User:
            uid, email, domain = "u1", "a@b.com", "b.com"

        from contextlib import ExitStack

        from tools.documents.generate import generate_document

        with ExitStack() as stack:
            stack.enter_context(patch("tools.documents.context.resolve_caller_uid", return_value="u1"))
            stack.enter_context(patch("auth.firebase_auth.resolve_user_by_uid", return_value=_User()))
            stack.enter_context(patch("tools.documents.generate.convert_markdown", return_value=ok))
            stack.enter_context(patch("tools.documents.generate.folders_db.ensure_named_folder", return_value="f1"))
            stack.enter_context(patch("tools.documents.generate.ingest_document", side_effect=side_effect))
            return await generate_document("# Hi", filename="x.docx")

    async def test_a_firestore_rejection_returns_failed_instead_of_raising(self):
        from google.api_core.exceptions import InvalidArgument

        result = await self._run(InvalidArgument("400 Property array contains an invalid nested entity."))

        assert result["status"] == "failed"
        assert "could not be saved" in result["error"]
        assert "nested entity" in result["error"]
        assert result["sourceMarkdown"] == "# Hi", "the authored content must survive a save failure"

    async def test_any_unexpected_error_is_reported_not_raised(self):
        result = await self._run(RuntimeError("gcs exploded"))
        assert result["status"] == "failed"
        assert "gcs exploded" in result["error"]
