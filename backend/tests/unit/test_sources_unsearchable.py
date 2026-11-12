"""What a source leaves out as unsearchable (github-sources.md §9): empty files,
and parser outputs whose original is in the same source."""

from __future__ import annotations

import pytest

from sources.engine import plan
from sources.github import TreeEntry
from sources.unsearchable import parser_original, unsearchable_reason

TREE = {
    "sessions/03/agenda.md",
    "sessions/03/parsed/agenda.md.md",
    "sessions/03/parsed/agenda.md.json",
    "returned/Notes by Gemini.docx",
    "returned/parsed/Notes by Gemini.docx.json",
    "proposal.docx",
    "proposal.docx.md",
    "intake/only-copy.docx.json",
    "returned/parsed/Notes only.docx.json",
    "returned/parsed/Notes only.docx.md",
    "release-v1.2.md",
    "data.json",
    "README.md",
}


@pytest.mark.parametrize(
    ("path", "original"),
    [
        ("sessions/03/parsed/agenda.md.md", "sessions/03/agenda.md"),
        ("sessions/03/parsed/agenda.md.json", "sessions/03/agenda.md"),
        ("returned/parsed/Notes by Gemini.docx.json", "returned/Notes by Gemini.docx"),
        ("proposal.docx.md", "proposal.docx"),  # beside the original, no parsed/ folder
        ("intake/only-copy.docx.json", None),  # its original isn't synced: the only text copy stays
        ("release-v1.2.md", None),  # a real document that merely has dots in its name
        ("data.json", None),
        ("README.md", None),
    ],
)
def test_parser_outputs_are_recognised_by_their_original(path, original):
    assert parser_original(path, TREE) == original


def test_empty_files_are_unsearchable():
    assert unsearchable_reason("notes/final.txt", 0, TREE) == "empty file"
    assert unsearchable_reason("README.md", 120, TREE) is None


def test_the_planner_skips_them_with_a_reason_and_removes_ones_it_had():
    # Each file its own blob: equal shas would mean equal bytes, i.e. a move.
    entries = [TreeEntry(path=p, sha=f"s-{p}", size=0 if p == "empty.md" else 10) for p in [*TREE, "empty.md"]]
    owned = {"proposal.docx.md": {"path": "proposal.docx.md", "blobSha": "s-proposal.docx.md", "docId": "d1"}}
    decided = plan(entries, owned, include=[], exclude=[], accepts=lambda p: None)
    skipped = {s["path"]: s["reason"] for s in decided.skipped}
    assert skipped["empty.md"] == "empty file"
    assert skipped["proposal.docx.md"] == "parser output of proposal.docx, which is synced itself"
    assert skipped["returned/parsed/Notes by Gemini.docx.json"].startswith("parser output of Notes by Gemini.docx")
    assert "intake/only-copy.docx.json" in {e.path for e in decided.add}
    # Synced before the rule existed: removed like a deleted file.
    assert [r["path"] for r in decided.remove] == ["proposal.docx.md"]


def test_a_json_dump_beside_its_markdown_twin_is_skipped_and_the_markdown_kept():
    # The original .docx was never committed: the Markdown is the readable copy.
    assert unsearchable_reason("returned/parsed/Notes only.docx.json", 664_122, TREE) == (
        "parser output; its Markdown version Notes only.docx.md is synced"
    )
    assert unsearchable_reason("returned/parsed/Notes only.docx.md", 235_568, TREE) is None
    assert unsearchable_reason("intake/only-copy.docx.json", 10, TREE) is None  # no twin: kept
