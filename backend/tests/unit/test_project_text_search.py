"""Literal project search: bounded excerpts, table text, ownership and coverage."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from adk import project_text_search as search
from adk.a2ui_project_search_render import project_text_matches_to_a2ui
from tools.documents.context import DocumentAccessError
from tools.documents.parsed_text import parsed_text_from_record


def record(text="The cure period is 27 business days.", **fields):
    return {
        "userId": "alice",
        "projectIds": ["p"],
        "parseStatus": "parsed",
        "originalFilename": "PPA.pdf",
        "blocks": [{"type": "paragraph", "block_id": "clause-7", "page": 12, "text": text}],
        **fields,
    }


def context():
    return SimpleNamespace(_invocation_context=SimpleNamespace(user_id="alice"), state={"project_id": "p"})


@pytest.fixture
def world(monkeypatch):
    docs = {
        "a": record(),
        "b": record("Replenish security within 12 business days.", originalFilename="Security.pdf"),
        "outside": record("SECRET", projectIds=["elsewhere"]),
        "foreign": record("SECRET", userId="bob"),
    }
    monkeypatch.setattr(search, "get_document", lambda collection, doc_id: docs[doc_id])
    monkeypatch.setattr("db.projects.project_document_ids", lambda uid, pid: list(docs))
    monkeypatch.setattr(search, "current_project", lambda ctx: {"id": "p", "name": "Portfolio"})
    return docs


def test_canonical_text_applies_edits_and_excludes_comments():
    doc = record(
        blocks=[{"type": "paragraph", "text": "old value"}, {"type": "comment", "text": "private comment"}],
        editedBlocks={"0": {"editedText": "new value"}},
    )
    parsed = parsed_text_from_record("a", doc, caller_uid="alice")
    assert parsed.text == "new value"
    with pytest.raises(DocumentAccessError):
        parsed_text_from_record("a", doc, caller_uid="bob")
    with pytest.raises(ValueError, match="successfully parsed"):
        parsed_text_from_record("a", record(parseStatus="failed"), caller_uid="alice")


def test_project_scope_and_diverse_results(world):
    out = asyncio.run(search.grep_project_documents(["business days", "SECRET"], max_results=2, tool_context=context()))
    assert {r["filename"] for r in out["results"]} == {"PPA.pdf", "Security.pdf"}
    assert all("SECRET" not in r["snippet"] for r in out["results"])
    assert len(json.dumps(out)) < 5000
    assert not out["coverage"]["complete"]  # forged/stale membership rows are not counted as scanned


def test_full_scan_and_result_cap_are_explicit(world, monkeypatch):
    world.pop("outside")
    world.pop("foreign")
    out = asyncio.run(search.grep_project_documents(["business days"], max_results=1, tool_context=context()))
    assert out["coverage"]["complete"] and out["results_truncated"] and len(out["results"]) == 1
    monkeypatch.setattr(search, "MAX_SCAN_CHARS", 1)
    out = asyncio.run(search.grep_project_documents(["business days"], tool_context=context()))
    assert not out["coverage"]["complete"] and out["notice"] and not out["results"]


def test_unparsed_document_is_reported_as_incomplete(world):
    world["a"]["parseStatus"] = "pending"
    out = asyncio.run(search.grep_project_documents(["cure"], tool_context=context()))
    assert out["coverage"]["documents_unavailable"] == 1 and not out["coverage"]["complete"]


def test_scan_can_continue_after_document_limit(world, monkeypatch):
    world.pop("outside")
    world.pop("foreign")
    monkeypatch.setattr(search, "MAX_DOCUMENTS", 1)
    first = asyncio.run(search.grep_project_documents(["business days"], tool_context=context()))
    second = asyncio.run(
        search.grep_project_documents(
            ["business days"], cursor=first["coverage"]["next_cursor"], tool_context=context()
        )
    )
    assert first["coverage"]["next_cursor"] == 1 and second["coverage"]["next_cursor"] is None
    assert {first["results"][0]["filename"], second["results"][0]["filename"]} == {"PPA.pdf", "Security.pdf"}


def test_missing_input_or_project_is_visible(world, monkeypatch):
    assert asyncio.run(search.grep_project_documents(tool_context=context()))["status"] == "needs_input"
    assert (
        asyncio.run(search.grep_project_documents(["x"], max_results=100, tool_context=context()))["status"]
        == "needs_input"
    )
    monkeypatch.setattr(search, "current_project", lambda ctx: None)
    assert asyncio.run(search.grep_project_documents(["x"], tool_context=context()))["status"] == "error"


def test_search_result_and_empty_result_have_registered_ui(world):
    from adk.a2ui_result_render import is_render_payload_tool, render_for_emit
    from adk.project_tools import project_function_tools

    assert search.TOOL_NAME in {t.name for t in project_function_tools()}
    assert is_render_payload_tool(search.TOOL_NAME)
    out = asyncio.run(search.grep_project_documents(["cure"], tool_context=context()))
    surface = render_for_emit(search.TOOL_NAME, out)
    assert surface is not None and surface.artifact["kind"] == "sources"
    empty = project_text_matches_to_a2ui({"status": "ok", "message": "No matching passages", "results": []})
    assert "No matching passages" in json.dumps(empty)
