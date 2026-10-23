"""Tests for `aiplatform kb` subcommands."""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"


def run(*args: str):
    return CliRunner().invoke(main, ["--env", "local", "kb", *args])


@respx.mock
def test_list_prints_friendly_rows() -> None:
    respx.get(f"{BASE}/api/knowledge-bases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "kbId": "llm-1",
                    "name": "llm",
                    "documentCount": 11,
                    "audience": "only you",
                    "sources": [{"type": "library_folder", "name": "library"}],
                }
            ],
        )
    )
    result = run("list")
    assert result.exit_code == 0, result.output
    assert "llm-1" in result.output and "11 docs" in result.output and "only you" in result.output


@respx.mock
def test_list_404_means_no_knowledge_bases() -> None:
    respx.get(f"{BASE}/api/knowledge-bases").mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))
    result = run("list")
    assert result.exit_code != 0 and "no knowledge bases" in result.output


@respx.mock
def test_status_shows_source_errors_and_failures() -> None:
    respx.get(f"{BASE}/api/knowledge-bases/shared/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "kbId": "shared",
                "name": "Shared",
                "documentCount": 3,
                "failed": [{"path": "a/x.pdf", "error": "encrypted"}],
                "lastSyncSummary": {
                    "state": "done",
                    "indexed": 3,
                    "sources": {"blog": {"files": 3}, "notes": {"error": "does not exist"}},
                },
            },
        )
    )
    out = run("status", "shared").output
    assert (
        "3 documents" in out
        and "done, indexed 3" in out
        and "notes: does not exist" in out
        and "a/x.pdf: encrypted" in out
    )


@respx.mock
def test_sync_waits_and_fails_on_source_error(monkeypatch) -> None:
    monkeypatch.setattr("aiplatform.commands.kb.time.sleep", lambda s: None)
    respx.post(f"{BASE}/api/knowledge-bases/shared/sync").mock(
        return_value=httpx.Response(202, json={"kbId": "shared", "state": "queued"})
    )
    respx.get(f"{BASE}/api/knowledge-bases/shared/status").mock(
        side_effect=[
            httpx.Response(200, json={"lastSyncSummary": {"state": "indexing", "indexed": 1}}),
            httpx.Response(
                200, json={"lastSyncSummary": {"state": "done", "indexed": 2, "sources": {"notes": {"error": "gone"}}}}
            ),
        ]
    )
    result = run("sync", "shared")
    assert (
        result.exit_code == 1
        and "indexing, indexed 1" in result.output
        and "source problem — notes: gone" in result.output
    )


@respx.mock
def test_search_sends_filters() -> None:
    route = respx.post(f"{BASE}/api/knowledge-bases/shared/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "knowledgeBase": "Shared",
                "results": [{"title": "Retries", "snippet": "seven  times", "metadata": {"path": "notes/r.md"}}],
            },
        )
    )
    result = run("search", "shared", "upload retries", "--filter", "status=implemented", "--sort", "newest")
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body == {"query": "upload retries", "filters": {"status": "implemented"}, "sort": "newest", "pageSize": 8}
    assert "1. Retries  [notes/r.md]" in result.output and "seven times" in result.output
    assert run("search", "shared", "q", "--filter", "nope").exit_code != 0
