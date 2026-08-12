"""Tests for `aiplatform docs corpus` subcommands."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
_CORPUS_BASE = f"{BASE}/api/documents/corpus"


@respx.mock
def test_corpus_list_calls_files_endpoint() -> None:
    route = respx.get(f"{_CORPUS_BASE}/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "corpus_name": "projects/p/ragCorpora/1",
                "files": [
                    {"name": "projects/p/ragCorpora/1/ragFiles/f1", "display_name": "session-a/report.pdf"},
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "corpus", "list"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "projects/p/ragCorpora/1" in result.output
    assert "session-a/report.pdf" in result.output


@respx.mock
def test_corpus_delete_calls_delete_endpoint() -> None:
    route = respx.delete(f"{_CORPUS_BASE}/files").mock(
        return_value=httpx.Response(200, json={"deleted": "projects/p/ragCorpora/1/ragFiles/f1"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "docs", "corpus", "delete", "projects/p/ragCorpora/1/ragFiles/f1"],
    )
    assert result.exit_code == 0, result.output
    assert route.called
    assert route.calls.last.request.url.params.get("file_name") == "projects/p/ragCorpora/1/ragFiles/f1"


@respx.mock
def test_corpus_search_calls_search_endpoint() -> None:
    route = respx.post(f"{_CORPUS_BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "query": "revenue Q1",
                "results": [
                    {"text": "Revenue was €2.8M.", "source_file": "report.pdf", "score": 0.91},
                ],
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["--env", "local", "docs", "corpus", "search", "revenue Q1"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "Revenue was €2.8M" in result.output
    assert "report.pdf" in result.output


@respx.mock
def test_corpus_clear_requires_confirmation() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--env", "local", "docs", "corpus", "clear"],
        input="n\n",
    )
    assert result.exit_code != 0 or "Abort" in result.output or "aborted" in result.output.lower()
