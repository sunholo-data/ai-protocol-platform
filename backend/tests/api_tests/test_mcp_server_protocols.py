"""The MCP server answers both protocol generations through the real SDK client.

2025-11-25 clients (Claude Code today) negotiate with ``initialize``; 2026-07-28
clients send their version and capabilities on every request and never
handshake. Both go through the same auth gate and caller binding.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from protocols import mcp_server
from tests.api_tests.test_mcp_server import ALICE, PRIVATE, PUBLIC, TOKEN, _as_token_owner


def _app(server: mcp_server.PlatformMCP) -> Starlette:
    gated = mcp_server.get_mcp_asgi_app(server)
    return Starlette(
        routes=[Route("/mcp", mcp_server.McpExactPath(gated), methods=["GET", "POST", "DELETE"]), Mount("/mcp", gated)]
    )


def _client(app: Starlette, mode: str, token: str | None = None, **options: Any) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test", headers=headers)
    return Client(streamable_http_client("http://test/mcp", http_client=http), mode=mode, **options)


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "negotiated"), [("legacy", "2025-11-25"), ("2026-07-28", "2026-07-28")])
async def test_token_owner_lists_and_calls_on_both_protocol_versions(mode, negotiated):
    seen: dict = {}

    async def fake(**kw) -> AsyncGenerator[dict, None]:
        seen.update(kw)
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": "31.5h."}

    server = mcp_server.new_server()
    app = _app(server)
    a, b = _as_token_owner()
    with (
        a,
        b,
        patch.object(mcp_server, "list_skills", return_value=[PUBLIC, PRIVATE]),
        patch("db.clients.resolve_enabled_skills", return_value=None),
        patch("skills.visibility.visible_configs", side_effect=lambda configs, access, _e: list(configs)),
        patch("skills.skill_processor.process_skill_request", side_effect=fake),
    ):
        async with server.session_manager.run(), _client(app, mode, TOKEN) as client:
            assert client.protocol_version == negotiated
            names = sorted(tool.name for tool in (await client.list_tools()).tools)
            result = await client.call_tool("skill_my_hours", {"message": "my hours?"})
    assert names == ["skill_helper", "skill_my_hours"]
    assert result.content[0].text == "31.5h." and not result.is_error
    assert seen["user"].uid == ALICE.uid


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_anonymous_sees_public_skills_only_on_both_protocol_versions(mode, monkeypatch):
    monkeypatch.setenv("MCP_AUTH", "optional")
    server = mcp_server.new_server()
    app = _app(server)
    with patch.object(mcp_server, "list_marketplace", return_value=[PUBLIC]):
        async with server.session_manager.run(), _client(app, mode) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == ["skill_helper"]
