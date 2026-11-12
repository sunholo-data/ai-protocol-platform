"""Tests for `aiplatform memory` (Your memory, headless)."""

from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

BASE = "http://localhost:1956"
LISTING = {
    "available": True,
    "truncated": False,
    "memories": [
        {
            "ref": "reasoningEngines/1/memories/a",
            "fact": "Team is Northern region",
            "updated_at": "2026-10-03T09:00:00",
        },
        {"ref": "reasoningEngines/1/memories/b", "fact": "Prefers Spanish", "updated_at": None},
    ],
}


def _run(*args: str):
    return CliRunner().invoke(main, ["--env", "local", "memory", *args])


@respx.mock
def test_list_numbers_the_memories() -> None:
    respx.get(f"{BASE}/api/me/memories").mock(return_value=httpx.Response(200, json=LISTING))
    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "  1. Team is Northern region  (2026-10-03 09:00)" in result.output
    assert "  2. Prefers Spanish" in result.output


@respx.mock
def test_delete_sends_the_numbered_ref_and_project() -> None:
    respx.get(f"{BASE}/api/me/memories").mock(return_value=httpx.Response(200, json=LISTING))
    route = respx.delete(f"{BASE}/api/me/memories/item").mock(return_value=httpx.Response(200, json={"deleted": 1}))
    result = _run("delete", "2", "--project", "p1")
    assert result.exit_code == 0, result.output
    params = route.calls.last.request.url.params
    assert params.get("ref") == "reasoningEngines/1/memories/b" and params.get("project_id") == "p1"
    assert 'Deleted: "Prefers Spanish"' in result.output


@respx.mock
def test_delete_out_of_range_deletes_nothing() -> None:
    respx.get(f"{BASE}/api/me/memories").mock(return_value=httpx.Response(200, json=LISTING))
    route = respx.delete(f"{BASE}/api/me/memories/item")
    result = _run("delete", "9")
    assert result.exit_code != 0 and "There is no memory 9" in result.output
    assert not route.called


@respx.mock
def test_clear_confirms_with_the_backend_word() -> None:
    route = respx.delete(f"{BASE}/api/me/memories").mock(return_value=httpx.Response(200, json={"deleted": 2}))
    result = _run("clear", "--yes")
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params.get("confirm") == "forget"
    assert "Deleted 2 memories." in result.output


@respx.mock
def test_unavailable_env_says_so() -> None:
    respx.get(f"{BASE}/api/me/memories").mock(
        return_value=httpx.Response(200, json={"available": False, "memories": [], "truncated": False})
    )
    assert "isn't switched on" in _run("list").output
