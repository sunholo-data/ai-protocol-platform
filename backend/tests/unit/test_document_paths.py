"""Markdown wikis in a project: paths, links and YAML headers.

A synced repository, a folder upload or an Open Knowledge Format bundle keeps
its meaning in its paths and links. Before this the agent saw ``index.md`` once
per folder with no folder, links lost their targets when rendered
("Joined with customers"), the YAML header (``type``, ``description``) was
dropped, and nothing could open a document by path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adk import project_context
from adk.tools import get_document_content
from tools.documents import paths
from tools.documents.context import blocks_to_markdown
from tools.documents.frontmatter import markdown_frontmatter, stored_frontmatter

BUNDLE = [
    ("d-root", "kb/sales/index.md"),
    ("d-tables", "kb/sales/tables/index.md"),
    ("d-orders", "kb/sales/tables/orders.md"),
    ("d-cust", "kb/sales/tables/customers.md"),
    ("d-cust2", "kb/marketing/tables/customers.md"),
    ("d-guide", "kb/sales/guide.md"),
]


# --- resolving a path or a link ---


def test_an_exact_path_opens_that_document():
    assert paths.match("kb/sales/tables/orders.md", BUNDLE).doc_id == "d-orders"


def test_a_root_relative_link_resolves_by_its_unique_ending():
    assert paths.match("/tables/orders.md", BUNDLE).doc_id == "d-orders"


def test_dot_segments_anchors_and_case_are_ignored():
    assert paths.match("./Tables/Orders.md#joins", BUNDLE).doc_id == "d-orders"
    assert paths.match("../guide.md", BUNDLE).doc_id == "d-guide"


def test_a_link_without_extension_finds_the_markdown_file():
    assert paths.match("sales/guide", BUNDLE).doc_id == "d-guide"


def test_several_matches_are_listed_never_guessed():
    found = paths.match("/tables/customers.md", BUNDLE)
    assert found.doc_id == ""
    assert found.matches == ["kb/marketing/tables/customers.md", "kb/sales/tables/customers.md"]
    message = paths.not_found_message("/tables/customers.md", found, in_project=True)
    assert "kb/sales/tables/customers.md" in message and "Several documents" in message


def test_no_match_says_so():
    found = paths.match("/nope.md", BUNDLE)
    assert found.doc_id == "" and not found.matches
    assert "No document at the path" in paths.not_found_message("/nope.md", found, in_project=True)


def test_the_top_index_files_are_the_maps():
    assert paths.wiki_maps([p for _, p in BUNDLE] + ["other/index.md", "loose.md"]) == [
        "kb/sales/index.md",
        "other/index.md",
    ]


def test_a_document_path_joins_folder_and_filename():
    names = {"f1": "kb/sales"}
    assert paths.path_of({"originalFilename": "index.md", "folderId": "f1"}, names) == "kb/sales/index.md"
    assert paths.path_of({"originalFilename": "memo.docx", "folderId": "system"}, names) == "memo.docx"


# --- links survive rendering ---


def test_a_paragraph_keeps_its_link_targets():
    block = {
        "type": "text",
        "text": "Joined with customers on customer_id.",
        "runs": [
            {"text": "Joined with "},
            {"text": "customers", "href": "/tables/customers.md"},
            {"text": " on "},
            {"text": "customer_id", "code": True},
            {"text": "."},
        ],
    }
    assert blocks_to_markdown([block]) == "Joined with [customers](/tables/customers.md) on `customer_id`."


def test_list_items_keep_links_in_the_stored_shape():
    block = {
        "type": "list",
        "items": ["Orders: one row per order", "Plain item"],
        "item_runs": [
            {"items": [{"text": "Orders", "href": "/tables/orders.md"}, {"text": ": one row per order"}]},
            {"items": [{"text": "Plain item"}]},
        ],
    }
    assert blocks_to_markdown([block]) == "- [Orders](/tables/orders.md): one row per order\n\n- Plain item"


def test_text_without_links_is_unchanged():
    block = {
        "type": "text",
        "text": "Plain *as stored*.",
        "runs": [{"text": "Plain "}, {"text": "as stored", "italic": True}],
    }
    assert blocks_to_markdown([block]) == "Plain *as stored*."


# --- the YAML header ---


def test_the_header_of_a_markdown_file_is_kept():
    text = b"---\ntype: BigQuery Table\ndescription: One row per order.\n---\n# Schema\n"
    assert markdown_frontmatter(text, ".md") == "type: BigQuery Table\ndescription: One row per order."


def test_a_leading_rule_is_not_a_header():
    assert markdown_frontmatter(b"---\n\nJust a rule\n\n---\nmore", ".md") == ""


def test_only_markdown_files_have_a_header_kept():
    assert markdown_frontmatter(b"---\ntype: x\n---\n", ".txt") == ""


def test_a_stored_header_is_read_without_touching_storage():
    with patch("google.cloud.storage.Client") as client:
        assert stored_frontmatter("d1", {"frontmatter": "type: X", "sourceFormat": "md"}) == "type: X"
        assert stored_frontmatter("d2", {"sourceFormat": "pdf", "sourceUrl": "gs://b/x.pdf"}) == ""
    client.assert_not_called()


def test_an_older_markdown_record_gets_its_header_once():
    blob = SimpleNamespace(download_as_bytes=lambda start, end: b"---\ntype: Metric\n---\nbody")
    bucket = SimpleNamespace(blob=lambda _p: blob)
    with (
        patch("google.cloud.storage.Client", return_value=SimpleNamespace(bucket=lambda _b: bucket)),
        patch("db.firestore.update_document") as update,
    ):
        assert stored_frontmatter("d1", {"sourceFormat": "md", "sourceUrl": "gs://b/x.md"}) == "type: Metric"
    update.assert_called_once_with("parsed_documents", "d1", {"frontmatter": "type: Metric"})


# --- the project prompt ---


def test_the_project_block_lists_paths_and_the_maps():
    block = project_context.project_block(
        {"name": "Sales"}, ["kb/sales/tables/orders.md"], 1, None, ["kb/sales/index.md"]
    )
    assert "- kb/sales/tables/orders.md" in block
    assert "have a map (index.md)" in block and "- kb/sales/index.md" in block
    assert 'path="/tables/customers.md"' in block


def test_no_map_note_without_an_index():
    assert "have a map" not in project_context.project_block({"name": "P"}, ["a.pdf"], 1)


# --- opening by path ---


def _ctx():
    return SimpleNamespace(state={}, _invocation_context=SimpleNamespace(session=None))


@pytest.mark.asyncio
async def test_get_document_content_opens_a_path_within_the_project():
    with (
        patch("tools.documents.context.resolve_caller_uid", return_value="alice"),
        patch("adk.project_scope.current_project", return_value={"id": "p1"}),
        patch.object(paths, "project_paths", return_value=BUNDLE) as listed,
        patch("adk.tools.build_document_context", return_value=("# Orders", None)) as build,
    ):
        out = await get_document_content(path="/tables/orders.md", tool_context=_ctx())
    assert out == "# Orders"
    listed.assert_called_once_with("alice", "p1")
    assert build.call_args.args[0] == "d-orders"
    assert build.call_args.kwargs["caller_uid"] == "alice"


@pytest.mark.asyncio
async def test_an_ambiguous_path_opens_nothing():
    with (
        patch("tools.documents.context.resolve_caller_uid", return_value="alice"),
        patch("adk.project_scope.current_project", return_value={"id": "p1"}),
        patch.object(paths, "project_paths", return_value=BUNDLE),
        patch("adk.tools.build_document_context") as build,
    ):
        out = await get_document_content(path="customers.md", tool_context=_ctx())
    assert "Several documents match" in out
    build.assert_not_called()


@pytest.mark.asyncio
async def test_no_caller_no_path_lookup():
    with (
        patch("tools.documents.context.resolve_caller_uid", return_value=""),
        patch.object(paths, "resolve") as resolve,
    ):
        out = await get_document_content(path="a/b.md", tool_context=_ctx())
    assert "Could not identify" in out
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_neither_ref_nor_path():
    assert "give doc_id" in await get_document_content(tool_context=_ctx())
