"""MCP registry caches: config-doc TTL cache + per-toolset tools/list memo (v6.14.0).

Removes the per-turn Firestore read for every declared MCP server and the
per-turn `tools/list` handshake (once the agent cache keeps the toolset instance
alive). The conftest autouse fixture clears the config cache between tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from tools.mcp import registry


def test_config_cache_avoids_repeated_firestore_reads():
    calls = {"n": 0}

    def fake_get(_coll, _sid):
        calls["n"] += 1
        return {"url": "http://127.0.0.1:5000/mcp/x"}

    with patch("tools.mcp.registry.get_document", side_effect=fake_get):
        registry.get_mcp_tools_with_status(["srv"])
        registry.get_mcp_tools_with_status(["srv"])
    assert calls["n"] == 1  # second resolve served from cache


def test_config_cache_caches_a_none_miss():
    calls = {"n": 0}

    def fake_get(_coll, _sid):
        calls["n"] += 1
        return None  # not seeded

    with patch("tools.mcp.registry.get_document", side_effect=fake_get):
        _, missing1 = registry.get_mcp_tools_with_status(["srv"])
        _, missing2 = registry.get_mcp_tools_with_status(["srv"])
    assert missing1 == ["srv"] and missing2 == ["srv"]
    assert calls["n"] == 1  # a not-found is cached too, not re-read every turn


def test_clear_registry_cache_forces_reread():
    calls = {"n": 0}

    def fake_get(_coll, _sid):
        calls["n"] += 1
        return {"url": "http://x"}

    with patch("tools.mcp.registry.get_document", side_effect=fake_get):
        registry.get_mcp_tools_with_status(["srv"])
        registry.clear_registry_cache()  # e.g. after a re-seed
        registry.get_mcp_tools_with_status(["srv"])
    assert calls["n"] == 2


def test_config_cache_does_not_swallow_firestore_errors():
    def boom(_coll, _sid):
        raise RuntimeError("firestore down")

    with patch("tools.mcp.registry.get_document", side_effect=boom):
        resolved, missing = registry.get_mcp_tools_with_status(["srv"])
    # Error path unchanged: server counted missing, not cached as a success.
    assert resolved == [] and missing == ["srv"]


@pytest.mark.asyncio
async def test_toolset_memoises_tools_list():
    # Build the instance without McpToolset.__init__ (which needs a live
    # connection); set only the attrs get_tools touches.
    ts = TaggedMcpToolsetStub()
    calls = {"n": 0}

    async def fake_super(readonly_context=None):
        calls["n"] += 1
        return [MagicMock()]

    with patch.object(McpToolset, "get_tools", side_effect=fake_super):
        t1 = await ts.get_tools()
        t2 = await ts.get_tools()
    assert calls["n"] == 1  # tools/list handshake happened once
    assert t1 is t2  # same cached list


class TaggedMcpToolsetStub(registry.TaggedMcpToolset):
    def __init__(self):  # bypass McpToolset.__init__ (no live connection in tests)
        self._aitana_server_id = "srv"
        self._tools_cache = None
