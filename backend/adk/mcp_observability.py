"""OTel span tagging for MCP tool calls (M2B-BACKEND, MCP-APP-INTEGRATIONS).

Two callback factories that the agent factory composes into the existing
``before_tool_callback`` / ``after_tool_callback`` chain:

  * ``make_mcp_before_tool_callback()`` — at tool-call entry, tags the
    current OTel span with ``mcp_app.server_id`` if the tool came from an
    MCP source. Non-MCP tools (regular FunctionTool, AgentTool, etc.) are
    ignored.

  * ``make_mcp_after_tool_callback()`` — at tool-call exit, inspects the
    tool response. If any content item is an EmbeddedResource with mimeType
    ``text/html;profile=mcp-app``, tags ``mcp_app.has_ui_resource=true``.
    Useful for filtering Cloud Trace queries down to "show every turn that
    surfaced an MCP App UI".

Detection of "MCP-source tool": presence of the ``_aitana_mcp_server_id``
attribute stamped by ``TaggedMcpToolset.get_tools`` (see
``tools/mcp/registry.py``). Falls back to ``isinstance(tool, MCPTool)`` for
tools created outside our registry — they get the generic ``unknown``
server_id since we have no provenance.

These callbacks NEVER short-circuit and NEVER mutate the response —
observability must be invisible to control flow. They also NEVER raise; an
unparseable response just leaves ``has_ui_resource`` unset.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from google.adk.tools.base_tool import BaseTool
from opentelemetry import trace

from tools.mcp.registry import SERVER_ID_ATTR

logger = logging.getLogger(__name__)

# MCP App UI mimeType — must match ``UI_CAPABILITY_MIME_TYPE`` in
# tools/mcp/registry.py. Defined locally too so the observability module
# doesn't depend on the registry's spec mimeType evolving.
_UI_RESOURCE_MIME_PREFIX = "text/html"
_UI_RESOURCE_PROFILE = "mcp-app"

# Span attribute keys (kept short and lowercase per OTel conventions).
_ATTR_SERVER_ID = "mcp_app.server_id"
_ATTR_HAS_UI = "mcp_app.has_ui_resource"


def _server_id_for(tool: Any) -> str | None:
    """Return the server_id stamped by TaggedMcpToolset, or 'unknown' for
    MCPTool instances built outside our registry, or None for non-MCP tools.
    """
    server_id = getattr(tool, SERVER_ID_ATTR, None)
    if server_id is not None:
        return str(server_id)

    # Fallback: an MCPTool we didn't build — still "MCP-source" but we can't
    # name the server. Avoid importing MCPTool at module level to keep this
    # callback registration cheap when MCP tools aren't in use.
    try:
        from google.adk.tools.mcp_tool.mcp_tool import McpTool

        if isinstance(tool, McpTool):
            return "unknown"
    except ImportError:  # pragma: no cover - ADK is a hard dep
        pass
    return None


def _safe_set_attribute(key: str, value: Any) -> None:
    """Set an OTel span attribute, swallowing exceptions. The OTel SDK can
    raise during shutdown windows or when no recording span is active."""
    try:
        trace.get_current_span().set_attribute(key, value)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("mcp_observability: span attribute set failed: %s", exc)


def _response_has_ui_resource(response: Any) -> bool:
    """True iff ``response.content`` includes an EmbeddedResource whose
    ``mimeType`` indicates a UI (``text/html;profile=mcp-app``).

    Tolerant of unexpected shapes — returns False rather than raising. The
    response shape is whatever ADK's MCPTool serialised, typically a dict
    from ``CallToolResult.model_dump(mode="json")`` but tests may pass the
    Pydantic model itself.
    """
    if not response:
        return False

    content = None
    if isinstance(response, dict):
        content = response.get("content")
    else:
        content = getattr(response, "content", None)
    if not isinstance(content, list):
        return False

    for item in content:
        # Two flavours: dict (after model_dump) or pydantic model.
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type != "resource":
            continue
        resource = item.get("resource") if isinstance(item, dict) else getattr(item, "resource", None)
        if resource is None:
            continue
        mime = resource.get("mimeType") if isinstance(resource, dict) else getattr(resource, "mimeType", None)
        if not isinstance(mime, str):
            continue
        if _UI_RESOURCE_MIME_PREFIX in mime and _UI_RESOURCE_PROFILE in mime:
            return True
    return False


def make_mcp_before_tool_callback() -> Callable[..., Any]:
    """Return a ``before_tool_callback`` that tags the OTel span with
    ``mcp_app.server_id`` for MCP-source tools.
    """

    def _callback(tool: Any, args: dict, tool_context: Any) -> None:
        server_id = _server_id_for(tool)
        if server_id is None:
            return None  # non-MCP tool — leave the span alone
        _safe_set_attribute(_ATTR_SERVER_ID, server_id)
        return None

    return _callback


def make_mcp_after_tool_callback() -> Callable[..., Any]:
    """Return an ``after_tool_callback`` that tags the OTel span with
    ``mcp_app.has_ui_resource=true`` when the response carries a UI resource.
    Also re-tags ``mcp_app.server_id`` on the same span (safe — same value;
    ensures the attribute is set even if before_tool_callback didn't fire).
    """

    def _callback(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> None:
        server_id = _server_id_for(tool)
        if server_id is None:
            return None
        _safe_set_attribute(_ATTR_SERVER_ID, server_id)
        try:
            if _response_has_ui_resource(tool_response):
                _safe_set_attribute(_ATTR_HAS_UI, True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("mcp_observability: UI resource detection failed: %s", exc)
        return None

    return _callback


# ---------------------------------------------------------------------------
# Composers — wire MCP callbacks into existing chains without losing them
# ---------------------------------------------------------------------------


def compose_before_tool_callbacks(
    existing: Callable[..., Any] | None,
    mcp: Callable[..., Any],
) -> Callable[..., Any]:
    """Run ``existing`` first; if it returns a non-None result (the ADK
    short-circuit pattern — override the tool call), respect that and skip
    the MCP callback. Otherwise run ``mcp`` for its side effects only.

    ADK 2 supplies a bare BaseTool placeholder for unresolved names, runs the
    before-chain, then skips the after-chain. Reject that exact placeholder
    before side effects: callbacks stay balanced and the user gets a named
    RUN_ERROR, preserving our fail-fast policy instead of relying on the model
    to explain its own hallucinated tool. Concrete tools/subclasses are allowed.
    """

    def _composed(tool: Any, args: dict, tool_context: Any) -> Any:
        if type(tool) is BaseTool and tool.description == "Tool not found":
            raise ValueError(f"Tool '{tool.name}' not found.")
        result = existing(tool=tool, args=args, tool_context=tool_context) if existing else None
        if result is not None:
            return result
        return mcp(tool=tool, args=args, tool_context=tool_context)

    return _composed


def chain_mutators(*mutators: Callable[..., Any]) -> Callable[..., Any]:
    """Compose after-tool callbacks that MAY REWRITE the response, in order.

    ``compose_after_tool_callbacks`` takes exactly one mutating callback (its
    ``existing`` slot) and treats everything else as an observer whose return
    value is discarded. That is the right shape for OTel tagging and A2UI
    emission, and the wrong shape the moment a second callback needs to rewrite
    — which is what converting MCP image blocks into artefact references needs.

    Order is load-bearing, not stylistic. ``_convert_media_results`` must run
    BEFORE ``_handle_large_output``: composed the other way round the offload
    has already replaced a screenshot with a pointer string, the converter finds
    no image, and the failure is silent — no exception, nothing logged, and a
    screenshot the user is never shown. `tests/unit/test_media_results.py` pins
    both orders so the wrong one cannot return unnoticed.

    Each mutator sees the previous one's rewrite. Returns ``None`` when no
    mutator rewrote anything, preserving ADK's "is not None means use this"
    contract so a chain of decliners stays a decline.
    """

    async def _chained(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> Any:
        effective = tool_response
        rewritten = False
        for mutator in mutators:
            result = mutator(tool=tool, args=args, tool_context=tool_context, tool_response=effective)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                effective = result
                rewritten = True
        return effective if rewritten else None

    return _chained


def compose_after_tool_callbacks(
    existing: Callable[..., Any] | None,
    *observers: Callable[..., Any],
) -> Callable[..., Any]:
    """Run ``existing`` first; if it returns a value, use that as the new
    response (existing semantics — e.g. ``_handle_large_output`` returning
    a pointer string). Then run each of ``observers`` against the (possibly
    rewritten) response. Observers' return values are ignored — observation
    (OTel tagging, A2UI surface emission) must not mutate the response.

    ``observers`` is variadic so additional purely-observational after-tool
    hooks (the MCP span tagger, the result→A2UI emitter) can be composed
    without nesting. They run in the order given, each seeing the effective
    (post-``existing``) response.
    """
    if existing is None:
        # Adapt the observers' "return None always" to also return the original
        # response so the caller's contract is preserved.
        def _just_observe(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> Any:
            for observer in observers:
                observer(tool=tool, args=args, tool_context=tool_context, tool_response=tool_response)
            return tool_response

        return _just_observe

    # ASYNC (v6.10.0): `existing` may be a coroutine (e.g. `_handle_large_output`
    # awaits `save_artifact`). The wrapper awaits it if awaitable, so the artifact
    # offload actually runs; observers are sync (OTel tagging, A2UI emission).
    async def _composed(tool: Any, args: dict, tool_context: Any, tool_response: Any) -> Any:
        rewritten = existing(tool=tool, args=args, tool_context=tool_context, tool_response=tool_response)
        if inspect.isawaitable(rewritten):
            rewritten = await rewritten
        # Existing callbacks return either the new response or the original;
        # ADK treats "is not None" as "use this". Mirror that contract.
        effective = rewritten if rewritten is not None else tool_response
        for observer in observers:
            result = observer(tool=tool, args=args, tool_context=tool_context, tool_response=effective)
            if inspect.isawaitable(result):
                await result
        return rewritten

    return _composed


__all__ = [
    "chain_mutators",
    "compose_after_tool_callbacks",
    "compose_before_tool_callbacks",
    "make_mcp_after_tool_callback",
    "make_mcp_before_tool_callback",
]
