"""Tests for adk/mcp_observability.py — OTel attribute tagging on MCP tool calls.

Two callbacks under test:

  * ``make_mcp_before_tool_callback()`` — at tool-call entry, tags the current
    OTel span with ``mcp_app.server_id`` if the tool came from an MCP source.
    Non-MCP tools (regular FunctionTool, AgentTool) are ignored.
  * ``make_mcp_after_tool_callback()`` — at tool-call exit, inspects the
    response. If any content item is an EmbeddedResource with mimeType
    ``text/html;profile=mcp-app``, tags ``mcp_app.has_ui_resource=true``.

Detection of "MCP-source tool": presence of the ``_aitana_mcp_server_id``
attribute stamped by ``TaggedMcpToolset.get_tools`` (see
``tools/mcp/registry.py``). Falls back to ``isinstance(tool, MCPTool)`` for
toolsets created outside our registry (e.g. ad-hoc test setups).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from adk.mcp_observability import (
    make_mcp_after_tool_callback,
    make_mcp_before_tool_callback,
)
from tools.mcp.registry import SERVER_ID_ATTR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_tool(server_id: str = "ext-apps-map") -> MagicMock:
    """A mock that looks like a tagged MCPTool — has the server_id attribute
    on the instance."""
    tool = MagicMock()
    tool.name = "show_map"
    setattr(tool, SERVER_ID_ATTR, server_id)
    return tool


def _plain_tool(name: str = "list_documents") -> MagicMock:
    """A regular non-MCP FunctionTool — no server_id attribute."""
    tool = MagicMock(spec=["name", "run_async"])  # spec strips MagicMock attr-creation
    tool.name = name
    # Defensive: make hasattr(tool, SERVER_ID_ATTR) explicitly False.
    if hasattr(tool, SERVER_ID_ATTR):
        delattr(tool, SERVER_ID_ATTR)
    return tool


def _ui_response() -> dict:
    """An MCP CallToolResult shaped like one carrying a UI EmbeddedResource."""
    return {
        "content": [
            {
                "type": "resource",
                "resource": {
                    "uri": "ui://map/show",
                    "mimeType": "text/html;profile=mcp-app",
                    "text": "<html>...</html>",
                },
            }
        ],
        "isError": False,
    }


def _non_ui_response() -> dict:
    """An MCP CallToolResult with text content only — no UI resource."""
    return {
        "content": [
            {"type": "text", "text": "OK"},
        ],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# before_tool_callback: tag span with server_id
# ---------------------------------------------------------------------------


class TestBeforeToolCallback:
    def test_tags_span_with_server_id_for_mcp_tool(self):
        callback = make_mcp_before_tool_callback()
        tool = _mcp_tool("ext-apps-map")
        ctx = MagicMock()

        captured: dict[str, object] = {}

        def fake_set_attribute(key, value):
            captured[key] = value

        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = fake_set_attribute
        with pytest.MonkeyPatch.context() as mp:
            from adk import mcp_observability

            mp.setattr(mcp_observability.trace, "get_current_span", lambda: fake_span)
            result = callback(tool=tool, args={}, tool_context=ctx)

        assert result is None  # Never short-circuit
        assert captured.get("mcp_app.server_id") == "ext-apps-map"

    def test_no_attributes_for_non_mcp_tool(self):
        """A regular FunctionTool gets no mcp_app.* attributes — must not
        pollute the span for non-MCP work."""
        callback = make_mcp_before_tool_callback()
        tool = _plain_tool("list_documents")
        ctx = MagicMock()

        captured: dict[str, object] = {}

        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = lambda k, v: captured.update({k: v})
        with pytest.MonkeyPatch.context() as mp:
            from adk import mcp_observability

            mp.setattr(mcp_observability.trace, "get_current_span", lambda: fake_span)
            result = callback(tool=tool, args={}, tool_context=ctx)

        assert result is None
        assert all(not k.startswith("mcp_app.") for k in captured)


# ---------------------------------------------------------------------------
# after_tool_callback: detect UI resource in response
# ---------------------------------------------------------------------------


class TestAfterToolCallback:
    def test_marks_ui_resource_when_mcp_response_contains_one(self):
        callback = make_mcp_after_tool_callback()
        tool = _mcp_tool("ext-apps-map")
        ctx = MagicMock()
        response = _ui_response()

        captured: dict[str, object] = {}
        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = lambda k, v: captured.update({k: v})

        with pytest.MonkeyPatch.context() as mp:
            from adk import mcp_observability

            mp.setattr(mcp_observability.trace, "get_current_span", lambda: fake_span)
            result = callback(tool=tool, args={}, tool_context=ctx, tool_response=response)

        assert result is None  # never mutate the response
        assert captured.get("mcp_app.server_id") == "ext-apps-map"
        assert captured.get("mcp_app.has_ui_resource") is True

    def test_does_not_mark_ui_resource_when_mcp_response_is_text_only(self):
        callback = make_mcp_after_tool_callback()
        tool = _mcp_tool("ext-apps-map")
        ctx = MagicMock()
        response = _non_ui_response()

        captured: dict[str, object] = {}
        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = lambda k, v: captured.update({k: v})

        with pytest.MonkeyPatch.context() as mp:
            from adk import mcp_observability

            mp.setattr(mcp_observability.trace, "get_current_span", lambda: fake_span)
            result = callback(tool=tool, args={}, tool_context=ctx, tool_response=response)

        assert result is None
        assert captured.get("mcp_app.server_id") == "ext-apps-map"
        assert "mcp_app.has_ui_resource" not in captured

    def test_no_attributes_for_non_mcp_tool_response(self):
        """Non-MCP tools must not be inspected for UI resources or get
        mcp_app.* attributes — the after-callback is shared infra."""
        callback = make_mcp_after_tool_callback()
        tool = _plain_tool("list_documents")
        ctx = MagicMock()
        response = "plain text result"

        captured: dict[str, object] = {}
        fake_span = MagicMock()
        fake_span.set_attribute.side_effect = lambda k, v: captured.update({k: v})

        with pytest.MonkeyPatch.context() as mp:
            from adk import mcp_observability

            mp.setattr(mcp_observability.trace, "get_current_span", lambda: fake_span)
            result = callback(tool=tool, args={}, tool_context=ctx, tool_response=response)

        assert result is None
        assert all(not k.startswith("mcp_app.") for k in captured)

    def test_handles_unexpected_response_shape_gracefully(self):
        """If the upstream MCP server returned something we can't parse,
        the callback must not crash — observability is best-effort."""
        callback = make_mcp_after_tool_callback()
        tool = _mcp_tool("ext-apps-map")
        ctx = MagicMock()

        for weird in (None, "", [], {"unexpected": "shape"}, {"content": "not a list"}):
            captured: dict[str, object] = {}
            fake_span = MagicMock()
            # Bind the local fake_span via default args so each loop iteration
            # captures its own instance (pylint B023 prevention).
            fake_span.set_attribute.side_effect = lambda k, v, _c=captured: _c.update({k: v})

            with pytest.MonkeyPatch.context() as mp:
                from adk import mcp_observability

                mp.setattr(mcp_observability.trace, "get_current_span", lambda _s=fake_span: _s)
                # Must not raise.
                result = callback(tool=tool, args={}, tool_context=ctx, tool_response=weird)

            assert result is None
            # server_id still tagged because the tool is MCP-sourced
            assert captured.get("mcp_app.server_id") == "ext-apps-map"
            # has_ui_resource never True for unparseable shapes
            assert captured.get("mcp_app.has_ui_resource") is not True


# ---------------------------------------------------------------------------
# Composition with existing callbacks
# ---------------------------------------------------------------------------


class TestCallbackComposition:
    def test_compose_before_runs_existing_then_mcp(self):
        """``compose_before_tool_callback(existing, mcp)`` runs both. If the
        existing callback returns a dict (short-circuit/override behaviour),
        the MCP one is skipped — preserves existing semantics."""
        from adk.mcp_observability import compose_before_tool_callbacks

        existing = MagicMock(return_value=None)
        mcp = MagicMock(return_value=None)
        composed = compose_before_tool_callbacks(existing, mcp)

        composed(tool=_mcp_tool(), args={}, tool_context=MagicMock())
        existing.assert_called_once()
        mcp.assert_called_once()

    def test_compose_before_short_circuits_when_existing_overrides(self):
        from adk.mcp_observability import compose_before_tool_callbacks

        existing = MagicMock(return_value={"override": "value"})
        mcp = MagicMock()
        composed = compose_before_tool_callbacks(existing, mcp)

        result = composed(tool=_mcp_tool(), args={}, tool_context=MagicMock())
        assert result == {"override": "value"}
        existing.assert_called_once()
        mcp.assert_not_called()

    async def test_compose_after_runs_existing_then_mcp(self):
        """The after composer should let the existing callback potentially
        REWRITE the response (e.g. ``_handle_large_output`` returns a
        pointer string), then pass the rewritten value to the MCP callback.
        The composer is async (v6.10.0) so it can await an async `existing`
        like `_handle_large_output`; a sync `existing` still works."""
        from adk.mcp_observability import compose_after_tool_callbacks

        existing = MagicMock(return_value="rewritten response")
        mcp = MagicMock(return_value=None)
        composed = compose_after_tool_callbacks(existing, mcp)

        result = await composed(
            tool=_mcp_tool(),
            args={},
            tool_context=MagicMock(),
            tool_response={"original": "response"},
        )
        existing.assert_called_once()
        mcp.assert_called_once()
        # When the existing callback rewrites, that wins.
        assert result == "rewritten response"
        # The MCP callback must have been invoked with the REWRITTEN response,
        # not the original — otherwise UI detection sees stale data.
        mcp_kwargs = mcp.call_args.kwargs
        assert mcp_kwargs["tool_response"] == "rewritten response"
