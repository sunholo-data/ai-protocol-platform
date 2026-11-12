"""Generic render opt-in follows the actual server/tool, with chart + table fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from adk.a2ui_mcp_series_render import CONFIG_KEY
from adk.a2ui_result_render import is_render_payload_tool, render_for_emit
from tools.mcp.registry import _build_toolset


async def test_registry_opt_in_renders_only_for_the_configured_server():
    name = "example_cost_by_service"
    config = {"url": "http://localhost:9999/mcp", "render": {name: {"kind": "series", "title": "Spend by service"}}}
    toolset = _build_toolset("series-test", config)
    tool = SimpleNamespace(name=name, run_async=AsyncMock())
    with patch("google.adk.tools.mcp_tool.mcp_toolset.McpToolset.get_tools", new=AsyncMock(return_value=[tool])):
        tools = await toolset.get_tools()
    assert tools[0]._aitana_render_config["server_id"] == "series-test"
    assert is_render_payload_tool(name)
    ctx = SimpleNamespace(state={CONFIG_KEY: tools[0]._aitana_render_config})
    result = {"content": [{"type": "text", "text": '[{"service":"Compute","cost":20},{"service":"Search","cost":10}]'}]}
    rendered = render_for_emit(name, result, ctx)
    assert rendered is not None and rendered.artifact["kind"] == "prices"
    assert rendered.artifact["title"] == "Spend by service"
    model = rendered.messages[-1]["updateDataModel"]["value"]
    assert model["kind"] == "series" and model["rowCount"] == 2 and model["y"][0]["key"] == "cost"
    assert render_for_emit(name, result, SimpleNamespace(state={CONFIG_KEY: {"server_id": "other"}})) is None

    assert render_for_emit(name, result) is None
    single = {"content": [{"type": "text", "text": '{"service":"Compute","cost":20}'}]}
    fallback = render_for_emit(name, single, ctx)
    assert fallback.artifact["kind"] == "table"
    assert any("cost: 20" in c.get("text", "") for c in fallback.messages[1]["updateComponents"]["components"])
    empty = {"content": [{"type": "text", "text": "[]"}]}
    assert render_for_emit(name, empty, ctx) is None
    assert render_for_emit(name, {"isError": True, "content": result["content"]}, ctx) is None
