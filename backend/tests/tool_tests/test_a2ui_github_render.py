"""The GitHub workbench tab (adk/a2ui_github_render.py) and the render-before-
offload stage it relies on.

What matters:
* only a server with `workbench: github` draws, and only tools the tab knows;
* a pull request builds up (summary, then checks and reviews) and a different
  one starts afresh, with the earlier one linked;
* text from GitHub can't inject markdown or links, and only http(s) links are made;
* a large result is drawn in full AND still offloaded for the model.

Payloads are shaped like GitHub's MCP server output, with example data.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from adk import a2ui_github_render as gh
from adk.a2ui_result_render import is_render_payload_tool, render_for_emit, tool_produces_artifact
from adk.callbacks import A2UI_TOOL_ARGS_STATE_KEY, _handle_large_output, make_a2ui_result_emitter
from adk.mcp_observability import chain_mutators
from observability.timing import LatencyTracker, reset_current_tracker, set_current_tracker

REPO = {"owner": "example-org", "repo": "widgets"}
GITHUB = {"kind": "github", "server_id": "github"}


def _mcp(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _ctx(config=GITHUB) -> SimpleNamespace:
    return SimpleNamespace(state={"temp:a2ui_mcp_render": config}, invocation_id="inv-1")


def _render(ctx, tool: str, args: dict, payload):
    ctx.state[gh.TOOL_NAME_KEY] = tool
    ctx.state[A2UI_TOOL_ARGS_STATE_KEY] = args
    return render_for_emit(tool, _mcp(payload), ctx, offload=True)


def _texts(rendered) -> list[str]:
    components = rendered.messages[1]["updateComponents"]["components"]
    return [c["text"] for c in components if c["component"] == "Text"]


def _pr(number: int, title: str, **extra) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "html_url": f"https://github.com/example-org/widgets/pull/{number}",
        "user": {"login": "dev-one"},
        "head": {"ref": "feat/x"},
        "base": {"ref": "main"},
        "additions": 10,
        "deletions": 2,
        "changed_files": 3,
        "commits": 1,
        "updated_at": "2026-11-05T10:00:00Z",
        "body": "## Why\r\n\r\nBecause.\r\n\r\n![shot](https://example.com/a.png)\r\n<!-- hidden -->",
        **extra,
    }


def test_commits_render_as_linked_rows_on_one_github_tab():
    commits = [
        {
            "sha": "abcdef1234567890",
            "html_url": "https://github.com/example-org/widgets/commit/abcdef1",
            "commit": {
                "message": "Fix the *thing*\n\nlonger text",
                "author": {"name": "Dev", "date": "2026-11-05T07:10:10Z"},
            },
            "author": {"login": "dev-one"},
        }
    ]
    rendered = _render(_ctx(), "list_commits", REPO, commits)
    assert rendered.surface_id == gh.GITHUB_SURFACE
    assert rendered.artifact == {
        "kind": "github",
        "title": "GitHub",
        "description": "What was looked at on GitHub in this chat",
    }
    texts = _texts(rendered)
    assert texts[0] == "#### Commits"
    row = next(t for t in texts if "abcdef1" in t)
    assert "[`abcdef1`](https://github.com/example-org/widgets/commit/abcdef1)" in row
    assert r"Fix the \*thing\*" in row  # escaped: no emphasis from a commit message
    assert "5 Nov 2026, 07:10 UTC" in row


def test_only_a_github_workbench_server_draws():
    payload = [{"sha": "1", "commit": {"message": "m"}}]
    assert _render(_ctx(config=None), "list_commits", REPO, payload) is None
    assert _render(_ctx(config={"kind": "series", "server_id": "x"}), "list_commits", REPO, payload) is None
    ctx = _ctx()
    ctx.state[gh.TOOL_NAME_KEY] = "get_me"
    assert render_for_emit("get_me", _mcp({"login": "me"}), ctx, offload=True) is None


def test_an_error_result_draws_nothing():
    ctx = _ctx()
    ctx.state[gh.TOOL_NAME_KEY] = "list_commits"
    error = {"content": [{"type": "text", "text": "failed: 404"}], "isError": True}
    assert render_for_emit("list_commits", error, ctx, offload=True) is None


def test_a_pull_request_builds_up_and_a_new_one_starts_afresh():
    ctx = _ctx()
    pr_args = {**REPO, "pullNumber": 7}
    _render(ctx, "pull_request_read", {**pr_args, "method": "get"}, _pr(7, "Add widgets"))
    checks = {
        "total_count": 2,
        "check_runs": [
            {
                "name": "ci",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/example-org/widgets/runs/1",
            },
            {"name": "lint", "status": "in_progress", "conclusion": None, "html_url": "javascript:alert(1)"},
        ],
    }
    _render(ctx, "pull_request_read", {**pr_args, "method": "get_check_runs"}, checks)
    rendered = _render(
        ctx,
        "pull_request_read",
        {**pr_args, "method": "get_reviews"},
        [{"state": "APPROVED", "user": {"login": "rev"}}],
    )
    texts = _texts(rendered)
    assert texts[0].startswith("#### [#7](https://github.com/example-org/widgets/pull/7) Add widgets")
    assert any("Open" in t and "`feat/x` → `main`" in t for t in texts)
    body = next(t for t in texts if "Because." in t)
    assert "**Why**" in body and "png" not in body and "hidden" not in body
    assert "**● In progress** · 1 running · 1 passed" in texts
    assert texts.index("● Running — lint") < texts.index(
        "✓ Passed — [ci](https://github.com/example-org/widgets/runs/1)"
    )
    assert "● Running — lint" in texts  # an unsafe URL is not made a link
    assert "Approved — **rev**" in texts

    rendered = _render(ctx, "pull_request_read", {**REPO, "pullNumber": 8, "method": "get"}, _pr(8, "Other"))
    texts = _texts(rendered)
    assert not any("Passed" in t for t in texts)  # PR 7's checks don't follow
    assert "### Earlier in this chat" in texts
    assert "[Pull request \\#7 in example-org/widgets](https://github.com/example-org/widgets/pull/7)" in texts


def test_markdown_and_links_in_titles_are_neutralised():
    issues = {
        "issues": [
            {"number": 3, "title": "[click](https://evil.example) <b>x</b>", "state": "OPEN", "user": {"login": "a"}}
        ]
    }
    texts = _texts(_render(_ctx(), "list_issues", REPO, issues))
    row = next(t for t in texts if "#3" in t)
    assert "[click](" not in row  # the brackets are escaped, so this is text, not a link
    assert r"\[click\]" in row and r"\<b\>" in row
    # list_issues carries no html_url; the link comes from the repository asked about.
    assert row.startswith("[#3](https://github.com/example-org/widgets/issues/3)")


def test_a_file_is_shown_as_a_code_block():
    result = {
        "content": [
            {"type": "text", "text": "successfully downloaded text file"},
            {
                "type": "resource",
                "resource": {"uri": "repo://x", "mimeType": "text/plain", "text": "a = 1\n```\nb = 2\n"},
            },
        ]
    }
    ctx = _ctx()
    ctx.state[gh.TOOL_NAME_KEY] = "get_file_contents"
    ctx.state[A2UI_TOOL_ARGS_STATE_KEY] = {**REPO, "path": "src/app.py"}
    texts = _texts(render_for_emit("get_file_contents", result, ctx, offload=True))
    code = next(t for t in texts if t.startswith("```"))
    assert code.startswith("```python\na = 1") and code.count("```") == 2  # an inner fence can't break out


def test_github_tools_render_before_offload_and_are_not_exempt():
    assert not is_render_payload_tool("list_pull_requests")
    assert tool_produces_artifact("list_pull_requests")
    ctx = _ctx()
    ctx.state[gh.TOOL_NAME_KEY] = "list_pull_requests"
    # The default (post-offload) stage leaves GitHub results alone.
    assert render_for_emit("list_pull_requests", _mcp([_pr(1, "t")]), ctx, offload=False) is None


async def test_a_large_result_is_drawn_in_full_and_still_offloaded():
    prs = [_pr(n, f"PR {n} " + "x" * 2000) for n in range(1, 40)]
    response = _mcp(prs)
    assert len(json.dumps(response)) > 50_000

    ctx = MagicMock()
    ctx.state = {"temp:a2ui_mcp_render": GITHUB}
    ctx.invocation_id = "inv-1"
    ctx.save_artifact = AsyncMock()
    tool = SimpleNamespace(name="list_pull_requests", _aitana_render_config=GITHUB)

    tracker = LatencyTracker(skill_id="t", session_id="s", user_id="u")
    token = set_current_tracker(tracker)
    try:
        chain = chain_mutators(make_a2ui_result_emitter(before_offload=True), _handle_large_output)
        out = await chain(tool=tool, args=REPO, tool_context=ctx, tool_response=response)
        # The model gets the pointer...
        assert isinstance(out, str) and "large response saved as artifact" in out
        ctx.save_artifact.assert_called_once()
        # ...and the tab was drawn from the full list, before the offload.
        events = tracker.drain_stage_events()
        assert len(events) == 1
        assert events[0].value["surfaceId"] == gh.GITHUB_SURFACE
        assert events[0].value["sourceId"] == "inv-1:list_pull_requests:pre1"
        rows = [
            c for c in events[0].value["messages"][1]["updateComponents"]["components"] if "PR 30" in c.get("text", "")
        ]
        assert rows
    finally:
        reset_current_tracker(token)


def test_the_view_kept_in_session_state_stays_small():
    ctx = _ctx()
    many = [_pr(n, "t" * 300) for n in range(100)]
    _render(ctx, "list_pull_requests", REPO, many)
    assert len(ctx.state["github_pane_view"]["rows"]) == gh._MAX_ROWS
    assert len(json.dumps(ctx.state["github_pane_view"])) < 20_000


def test_every_text_is_the_markdown_variant():
    # Only `body` renders as full markdown on the workbench; a heading variant
    # showed the PR link as raw, upper-cased text (seen on the dev stack).
    rendered = _render(_ctx(), "pull_request_read", {**REPO, "pullNumber": 7, "method": "get"}, _pr(7, "Add widgets"))
    components = rendered.messages[1]["updateComponents"]["components"]
    assert {c.get("variant") for c in components if c["component"] == "Text"} == {"body"}


def test_a_long_all_green_check_list_is_summarised():
    runs = [{"name": f"job {n}", "status": "completed", "conclusion": "success"} for n in range(20)]
    runs += [{"name": "deploy", "status": "completed", "conclusion": "skipped"}]
    runs += [
        {
            "name": "e2e",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/example-org/widgets/runs/9",
        }
    ]
    ctx = _ctx()
    rendered = _render(
        ctx, "pull_request_read", {**REPO, "pullNumber": 7, "method": "get_check_runs"}, {"check_runs": runs}
    )
    texts = _texts(rendered)
    assert "**✗ Failing** · 1 failed · 20 passed · 1 skipped" in texts
    assert "✗ Failed — [e2e](https://github.com/example-org/widgets/runs/9)" in texts
    assert not any(t.startswith("✓ Passed") for t in texts)  # 20 passes: counted, not listed
