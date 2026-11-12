"""Operator opt-in for dataset-shaped MCP tools; reuse the declared series renderer."""

from __future__ import annotations

from typing import Any

from adk.a2ui_bigquery_render import _bigquery_artifact, _bigquery_surface, bigquery_rows_to_a2ui
from adk.a2ui_result_render import register

CONFIG_KEY = "temp:a2ui_mcp_render"
_registered: set[tuple[str, str]] = set()


def register_series_tools(server_id: str, configs: dict) -> None:
    for tool_name, config in configs.items():
        if not isinstance(config, dict) or config.get("kind") != "series":
            continue
        key = (server_id, tool_name)
        if key in _registered:
            continue
        _registered.add(key)
        register(
            series_to_a2ui,
            tool_names=[tool_name],
            name=f"mcp-series:{server_id}:{tool_name}",
            context_matcher=lambda ctx, server=server_id: bool(
                ctx and (ctx.state.get(CONFIG_KEY) or {}).get("server_id") == server
            ),
            surface=_bigquery_surface,
            artifact_meta=series_artifact,
        )


def series_to_a2ui(result: Any, tool_context: Any = None) -> list[dict] | None:
    config = tool_context.state.get(CONFIG_KEY) if tool_context else None
    if not config or config.get("kind") != "series":
        return None
    return bigquery_rows_to_a2ui(result, tool_context, display=config)


def series_artifact(result: Any, tool_context: Any = None) -> dict:
    artifact = _bigquery_artifact(result, tool_context)
    config = tool_context.state.get(CONFIG_KEY) if tool_context else {}
    artifact["title"] = (config or {}).get("title") or artifact["title"]
    artifact["description"] = "Dataset result"
    return artifact
