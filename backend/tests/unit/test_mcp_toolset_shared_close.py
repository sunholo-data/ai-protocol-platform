"""A shared (agent-cached) TaggedMcpToolset must not be closed under an in-flight call.

Regression for 2026-09-30: concurrent turns share one cached agent; each turn's
Runner.close() closed the shared MCP session under another turn's call, which then
hung for the full 300s sse_read_timeout.
"""

import asyncio

import pytest
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from tools.mcp.registry import TaggedMcpToolset


@pytest.fixture
def toolset(monkeypatch):
    closes: list[str] = []

    async def fake_close(self):
        closes.append("closed")

    monkeypatch.setattr(McpToolset, "close", fake_close)
    ts = TaggedMcpToolset(server_id="s", connection_params=StreamableHTTPConnectionParams(url="http://127.0.0.1:1/mcp"))
    return ts, closes


async def test_close_is_deferred_until_inflight_call_drains(toolset):
    ts, closes = toolset
    release = asyncio.Event()

    async def slow_call(**_):
        await release.wait()
        return {"ok": True}

    call = asyncio.create_task(ts._leased(slow_call)(args={}, tool_context=None))
    await asyncio.sleep(0)
    await ts.close()  # another turn's Runner.close()
    assert closes == [], "session closed under an in-flight call"
    release.set()
    assert await call == {"ok": True}
    assert closes == ["closed"], "deferred close must run once the last call drains"


async def test_close_is_immediate_when_idle(toolset):
    ts, closes = toolset
    await ts.close()
    assert closes == ["closed"]


async def test_failed_call_still_releases_and_closes(toolset):
    ts, closes = toolset

    async def boom(**_):
        await asyncio.sleep(0)
        raise RuntimeError("x")

    call = asyncio.create_task(ts._leased(boom)())
    await asyncio.sleep(0)
    await ts.close()
    with pytest.raises(RuntimeError):
        await call
    assert ts._inflight == 0 and closes == ["closed"]
