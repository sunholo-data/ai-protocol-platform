"""API tests for the MCP server at /mcp (PROTOCOLS-1A5 M3).

Verifies:
  - The FastMCP server registers one tool per public skill at startup.
  - rebuild_tools() is idempotent — add/remove/sync cycle works.
  - Tool invocation routes through process_skill_request() and returns
    the concatenated TEXT_MESSAGE_CONTENT deltas.
  - Tool-name sanitisation handles hyphenated/UUID skill IDs.

We exercise the tool registry + invocation path directly via the
FastMCP instance rather than going over the wire — the wire protocol
(initialize, tools/list, tools/call) is the SDK's job; ours is the
glue from public skills -> MCP tools -> skill execution.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest

from db.models import SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _skill(
    *,
    name: str = "search",
    skill_id: str = "search-1",
    description: str = "Run a search query.",
) -> SkillConfig:
    return SkillConfig(
        name=name,
        description=description,
        instructions="Be helpful.",
        skillId=skill_id,
        ownerId="owner-uid",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl=AccessControl(type="public"),
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with a fresh MCP tool registry."""
    from protocols import mcp_server

    # Remove anything left over from another test / module init.
    for skill_id, tool_name in list(mcp_server._registered.items()):
        mcp_server.mcp.remove_tool(tool_name)
        del mcp_server._registered[skill_id]
    yield
    for skill_id, tool_name in list(mcp_server._registered.items()):
        mcp_server.mcp.remove_tool(tool_name)
        del mcp_server._registered[skill_id]


# --- Tool-name sanitisation ---


def test_tool_name_converts_hyphens_to_underscores():
    from protocols.mcp_server import _tool_name

    assert _tool_name("my-skill-1") == "skill_my_skill_1"


def test_tool_name_prepends_when_starts_with_digit():
    from protocols.mcp_server import _tool_name

    assert _tool_name("1abc").startswith("skill_")
    # Must not contain raw leading digit after the prefix.
    assert _tool_name("1abc") == "skill__1abc"


# --- Registry sync ---


@pytest.mark.asyncio
async def test_rebuild_tools_registers_one_tool_per_public_skill():
    from protocols import mcp_server

    public = [_skill(skill_id="s1", name="one"), _skill(skill_id="s2", name="two")]
    with patch("protocols.mcp_server.list_marketplace", return_value=public):
        mcp_server.rebuild_tools()

    tools = await mcp_server.mcp.list_tools()
    names = sorted(t.name for t in tools)
    assert names == ["skill_s1", "skill_s2"], f"unexpected tools: {names}"


@pytest.mark.asyncio
async def test_rebuild_tools_is_idempotent():
    """Calling rebuild twice with the same public set yields the same tools."""
    from protocols import mcp_server

    public = [_skill(skill_id="s1")]
    with patch("protocols.mcp_server.list_marketplace", return_value=public):
        mcp_server.rebuild_tools()
        mcp_server.rebuild_tools()

    tools = await mcp_server.mcp.list_tools()
    assert [t.name for t in tools] == ["skill_s1"]


@pytest.mark.asyncio
async def test_rebuild_tools_removes_stale_tools():
    """A skill that goes private/deleted disappears from the tool list."""
    from protocols import mcp_server

    initial = [_skill(skill_id="s1"), _skill(skill_id="s2")]
    with patch("protocols.mcp_server.list_marketplace", return_value=initial):
        mcp_server.rebuild_tools()

    # s2 goes away (e.g. deleted or flipped to private).
    shrunk = [_skill(skill_id="s1")]
    with patch("protocols.mcp_server.list_marketplace", return_value=shrunk):
        mcp_server.rebuild_tools()

    tools = await mcp_server.mcp.list_tools()
    assert [t.name for t in tools] == ["skill_s1"]


# --- tools/call round-trip ---


@pytest.mark.asyncio
async def test_tool_call_returns_concatenated_text_deltas():
    """Invoking a registered tool streams through process_skill_request and
    concatenates the TEXT_MESSAGE_CONTENT deltas into a single string."""
    from protocols import mcp_server

    async def _fake_process(**_kwargs) -> AsyncGenerator[dict, None]:
        yield {"type": "RUN_STARTED"}
        yield {"type": "TEXT_MESSAGE_START"}
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello "}
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "world."}
        yield {"type": "TEXT_MESSAGE_END"}
        yield {"type": "RUN_FINISHED"}

    skill = _skill(skill_id="echo-1", description="Echoes back.")
    with patch("protocols.mcp_server.list_marketplace", return_value=[skill]):
        mcp_server.rebuild_tools()

    with patch("skills.skill_processor.process_skill_request", side_effect=_fake_process):
        result = await mcp_server.mcp.call_tool("skill_echo_1", {"message": "hi"})

    # FastMCP wraps the string return in ContentBlocks. Pull out the
    # text portion — either the raw string (older versions) or a
    # TextContent entry.
    text = _extract_text(result)
    assert text == "Hello world.", f"got {text!r}"


@pytest.mark.asyncio
async def test_tool_call_ignores_non_text_events():
    """Tool/status events must not leak into the returned string."""
    from protocols import mcp_server

    async def _fake_process(**_kwargs) -> AsyncGenerator[dict, None]:
        yield {"type": "TOOL_CALL_START", "tool_call_name": "noop"}
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "answer"}
        yield {"type": "TOOL_CALL_END"}

    skill = _skill(skill_id="s1")
    with patch("protocols.mcp_server.list_marketplace", return_value=[skill]):
        mcp_server.rebuild_tools()

    with patch("skills.skill_processor.process_skill_request", side_effect=_fake_process):
        result = await mcp_server.mcp.call_tool("skill_s1", {"message": "?"})

    assert _extract_text(result) == "answer"


# --- Helpers ---


def _extract_text(result) -> str:
    """FastMCP's call_tool can return either a string, a list of
    ContentBlocks, or a (content, structured) tuple depending on
    version. Extract the textual payload in a version-tolerant way.
    """
    # Tuple form: (content_blocks, structured_content)
    if isinstance(result, tuple):
        result = result[0]
    # List of ContentBlocks (TextContent / similar)
    if isinstance(result, list):
        parts = []
        for block in result:
            txt = getattr(block, "text", None)
            if isinstance(txt, str):
                parts.append(txt)
        return "".join(parts)
    # Plain string
    if isinstance(result, str):
        return result
    # Dict (structured_output=True)
    if isinstance(result, dict):
        return str(result.get("result") or result.get("text") or result)
    return str(result)


# --- HTTP mount composition ---
#
# Regression guards for the FastMCP + FastAPI mount pattern. The
# in-process tests above exercise the FastMCP instance directly; they
# miss:
#   (a) mount path composition (FastMCP's streamable_http_path default
#       of "/mcp" combined with FastAPI's mount at "/mcp" gave /mcp/mcp),
#   (b) lifespan propagation (FastAPI doesn't call sub-app lifespans,
#       so the session_manager task group was never initialised and
#       every HTTP request 500'd with "Task group is not initialized"),
#   (c) DNS-rebinding host rejection (default allowed_hosts=[] rejected
#       every Host header including Cloud Run's).
# Exercising the mount over HTTP via TestClient catches all three.


def test_mcp_initialize_via_http_mount_returns_jsonrpc_result():
    """POST /mcp/ initialize must reach the JSON-RPC handler and return
    a serverInfo payload. Proves mount path + lifespan + DNS-rebinding
    settings are composed correctly end-to-end.
    """
    from fastapi.testclient import TestClient

    import fast_api_app as module

    with TestClient(module.app) as client:
        resp = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert resp.status_code == 200, f"MCP initialize failed: {resp.status_code} {resp.text[:200]}"
    # Streamable-HTTP returns an SSE-framed JSON-RPC response. The body
    # always includes the serverInfo we registered on the FastMCP instance.
    assert '"serverInfo"' in resp.text
    assert '"aitana-platform"' in resp.text
