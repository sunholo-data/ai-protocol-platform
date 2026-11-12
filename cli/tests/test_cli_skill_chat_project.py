"""`aiplatform skill chat --project` runs the turn inside a project.

The browser scopes a chat by sending ``forwardedProps.project_id`` on each turn
(projects.md §6.3); the CLI must send the same thing, or a headless check of a
project chat silently tests an unscoped one.
"""

from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aiplatform.cli import main

_URL = "http://localhost:1956/api/skill/general-assistant/stream"
_EVENTS = [
    {"type": "RUN_STARTED", "threadId": "t", "runId": "r"},
    {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"},
]


def _stream() -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in _EVENTS)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


def _invoke(args: list[str]):
    return CliRunner().invoke(main, ["--env", "local", "skill", "chat", "general-assistant", "-m", "hi", *args])


@respx.mock
def test_project_goes_in_forwarded_props() -> None:
    route = respx.post(_URL).mock(return_value=_stream())
    result = _invoke(["--project", "p-123"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["forwardedProps"] == {"project_id": "p-123"}


@respx.mock
def test_no_project_sends_no_scope() -> None:
    route = respx.post(_URL).mock(return_value=_stream())
    result = _invoke([])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["forwardedProps"] == {}


@respx.mock
def test_doc_attaches_like_a_browser_tab() -> None:
    route = respx.post(_URL).mock(return_value=_stream())
    result = _invoke(["--project", "p-1", "--doc", "d-1", "--doc", "d-2"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["forwardedProps"] == {
        "project_id": "p-1",
        "document_ids": ["d-1", "d-2"],
    }


@respx.mock
def test_a_turn_that_leaves_an_approval_says_how_to_decide_it() -> None:
    respx.post(_URL).mock(return_value=_stream())
    pending = {"approvals": [{"id": "severa:abc", "title": "Correct 17 time entries", "risk": "routine"}]}
    lookup = respx.get("http://localhost:1956/api/approvals").mock(return_value=httpx.Response(200, json=pending))
    result = _invoke(["--session", "chat-1"])
    assert result.exit_code == 0, result.output
    assert lookup.calls.last.request.url.params["session"] == "chat-1"
    assert "WAITING FOR YOUR APPROVAL" in result.output
    assert "Correct 17 time entries — aiplatform approvals approve severa:abc" in result.output
