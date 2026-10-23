"""Tests for `aiplatform docs generate` and friendly folder names — DOC-CREATE M6."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
_GENERATE = f"{BASE}/api/documents/generate"
_FOLDERS = f"{BASE}/api/folders"

_CREATED = {
    "docId": "doc-1",
    "status": "parsed",
    "originalFilename": "offer.docx",
    "blocksCount": 9,
    "storagePath": "users/u1/docs/created/offer.docx",
    "folderId": "created-folder",
}


@respx.mock
def test_generate_from_a_file_posts_the_markdown() -> None:
    route = respx.post(_GENERATE).mock(return_value=httpx.Response(200, json=_CREATED))
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("brief.md", "w", encoding="utf-8") as fh:
            fh.write("# Offer\n\nBody text.\n")
        result = runner.invoke(
            main,
            ["--env", "local", "docs", "generate", "--file", "brief.md", "--name", "offer.docx"],
        )

    assert result.exit_code == 0, result.output
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["contentMarkdown"].startswith("# Offer")
    assert sent["filename"] == "offer.docx"
    assert sent["target"] == "docx"
    assert sent["folder"] == "Created"
    assert "doc-1" in result.output


@respx.mock
def test_generate_passes_the_template_by_friendly_name() -> None:
    """CLAUDE.md #9: the operator types a name; the backend resolves the id."""
    route = respx.post(_GENERATE).mock(return_value=httpx.Response(200, json=_CREATED))
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("brief.md", "w", encoding="utf-8") as fh:
            fh.write("# Offer\n")
        result = runner.invoke(
            main,
            [
                "--env",
                "local",
                "docs",
                "generate",
                "--file",
                "brief.md",
                "--name",
                "offer.docx",
                "--template",
                "Acme letterhead",
            ],
        )

    assert result.exit_code == 0, result.output
    sent = json.loads(route.calls[0].request.content)
    assert sent["template"] == "Acme letterhead"


@respx.mock
def test_generate_reads_stdin_when_no_file_given() -> None:
    route = respx.post(_GENERATE).mock(return_value=httpx.Response(200, json=_CREATED))
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "generate", "--name", "x.docx"], input="# From stdin\n")

    assert result.exit_code == 0, result.output
    sent = json.loads(route.calls[0].request.content)
    assert "From stdin" in sent["contentMarkdown"]


def test_empty_source_is_refused_before_any_request() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "generate", "--name", "x.docx"], input="   \n")
    assert result.exit_code != 0
    assert "empty" in result.output.lower()


@respx.mock
def test_docs_list_resolves_a_folder_name_to_its_id() -> None:
    respx.get(_FOLDERS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "uploads-id", "name": "Uploads 2026-01-01"},
                {"id": "created-id", "name": "Created"},
            ],
        )
    )
    docs_route = respx.get(f"{_FOLDERS}/created-id/documents").mock(return_value=httpx.Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "list", "--folder", "Created"])

    assert result.exit_code == 0, result.output
    assert docs_route.called, "a folder NAME must resolve to its id"


@respx.mock
def test_docs_list_still_accepts_a_raw_folder_id() -> None:
    respx.get(_FOLDERS).mock(return_value=httpx.Response(200, json=[{"id": "created-id", "name": "Created"}]))
    docs_route = respx.get(f"{_FOLDERS}/some-raw-id/documents").mock(return_value=httpx.Response(200, json=[]))

    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "list", "--folder", "some-raw-id"])

    assert result.exit_code == 0, result.output
    assert docs_route.called, "an unmatched value must pass through as an id"
