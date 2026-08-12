"""Unit tests for the surface-aware A2UI toolset (MULTI-SURFACE-A2UI M1).

The wrapper toolset subclasses `SendA2uiToClientToolset` and augments the
validated tool result dict with two optional sibling keys: `surface_id` and
`update_mode`. The frontend (M3) reads those siblings alongside
`validated_a2ui_json` and routes the spec into the matching surface mount.

Backwards-compat contract:
  - `default_surface=None` → result dict contains ONLY `validated_a2ui_json`
    (no `surface_id`, no `update_mode`). Byte-identical to pre-M1 output.
  - `default_surface="workspace"` → result dict carries `surface_id` and
    `update_mode` siblings.

This file does NOT exercise the LLM end-to-end — we drive the tool's
`run_async` directly with a minimal A2UI v0.9 message to keep the test
fast (<50ms) and hermetic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from a2ui.adk.send_a2ui_to_client_toolset import SendA2uiToClientToolset

from adk.a2ui import (
    A2uiToolConfig,
    SurfaceAwareA2uiToolset,
    make_a2ui_toolset,
)

# A minimal v0.9 A2UI message that the basic_catalog will validate. The
# single-message `createSurface` form (just surfaceId + catalogId) is
# the lightest valid payload — no component tree needed because that
# ships in subsequent updateComponents messages. The catalogId is the
# canonical basic catalog's $id (see a2ui/assets/0.9/basic_catalog.json).
_BASIC_CATALOG_ID = "https://a2ui.org/specification/v0_9/basic_catalog.json"


def _minimal_a2ui_json() -> str:
    return '[{"version":"v0.9","createSurface":{"surfaceId":"main","catalogId":"' + _BASIC_CATALOG_ID + '"}}]'


def _stub_tool_context() -> MagicMock:
    """Build the minimum ToolContext stand-in the underlying tool needs.

    The library reads `tool_context.actions.skip_summarization` and uses
    the context as the ReadonlyContext for resolving the catalog (which
    is a concrete object in our factory, so no awaitables to chase).
    """
    ctx = MagicMock()
    ctx.actions = MagicMock()
    ctx.actions.skip_summarization = False
    return ctx


# === Backwards compatibility ===


@pytest.mark.asyncio
async def test_run_async_without_surface_omits_surface_keys():
    """surface_id=None → result has ONLY validated_a2ui_json (back-compat)."""
    toolset = make_a2ui_toolset()
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    assert len(tools) == 1
    tool = tools[0]

    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )

    assert "validated_a2ui_json" in result
    assert "surface_id" not in result
    assert "update_mode" not in result


@pytest.mark.asyncio
async def test_make_a2ui_toolset_default_returns_subclass_of_sdk_toolset():
    """The wrapper IS-A SendA2uiToClientToolset so ADK treats it identically."""
    toolset = make_a2ui_toolset()
    assert isinstance(toolset, SendA2uiToClientToolset)


@pytest.mark.asyncio
async def test_make_a2ui_toolset_without_surface_returns_wrapper_with_none():
    toolset = make_a2ui_toolset()
    assert isinstance(toolset, SurfaceAwareA2uiToolset)
    assert toolset.default_surface is None
    assert toolset.default_update_mode == "replace"


# === Surface-aware payloads ===


@pytest.mark.asyncio
async def test_run_async_with_workspace_surface_emits_surface_keys():
    toolset = make_a2ui_toolset(default_surface="workspace")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]

    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )

    assert result["surface_id"] == "workspace"
    assert result["update_mode"] == "replace"
    assert "validated_a2ui_json" in result


@pytest.mark.asyncio
async def test_run_async_with_workspace_patch_emits_patch_mode():
    toolset = make_a2ui_toolset(default_surface="workspace", default_update_mode="patch")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]

    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )

    assert result["surface_id"] == "workspace"
    assert result["update_mode"] == "patch"


@pytest.mark.asyncio
async def test_run_async_with_sidebar_surface():
    toolset = make_a2ui_toolset(default_surface="sidebar")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]
    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )
    assert result["surface_id"] == "sidebar"


@pytest.mark.asyncio
async def test_run_async_with_modal_surface():
    toolset = make_a2ui_toolset(default_surface="modal")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]
    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )
    assert result["surface_id"] == "modal"


@pytest.mark.asyncio
async def test_run_async_with_fork_custom_surface_id():
    """Forks can name surfaces freely — the wrapper does not constrain
    the surface id beyond rejecting `patch` against `chat`."""
    toolset = make_a2ui_toolset(default_surface="aipla:teacher-grid")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]
    result = await tool.run_async(
        args={"a2ui_json": _minimal_a2ui_json()},
        tool_context=_stub_tool_context(),
    )
    assert result["surface_id"] == "aipla:teacher-grid"


# === Validation at factory level ===


def test_make_a2ui_toolset_rejects_patch_without_surface():
    with pytest.raises(ValueError, match="patch"):
        make_a2ui_toolset(default_update_mode="patch")


def test_make_a2ui_toolset_rejects_patch_against_chat():
    with pytest.raises(ValueError, match="patch"):
        make_a2ui_toolset(default_surface="chat", default_update_mode="patch")


# === Error path — invalid A2UI JSON still produces the legacy error envelope ===


@pytest.mark.asyncio
async def test_run_async_error_path_preserves_legacy_envelope():
    """When the underlying tool errors, we do NOT add surface keys; the
    frontend treats the result as a failure regardless. Confirms we
    don't leak surface keys onto error payloads."""
    toolset = make_a2ui_toolset(default_surface="workspace")
    tools = await toolset.get_tools(_readonly_ctx_enabled())
    tool = tools[0]

    result = await tool.run_async(
        args={"a2ui_json": "not valid json"},
        tool_context=_stub_tool_context(),
    )
    assert "error" in result
    # surface_id MUST NOT leak onto an error envelope — the frontend
    # never tries to portal an error spec.
    assert "surface_id" not in result
    assert "update_mode" not in result


# === Factory accepts an A2uiToolConfig instance directly ===


def test_make_a2ui_toolset_from_tool_config_instance():
    """The agent factory can pass an already-validated A2uiToolConfig instead
    of unpacking it — keeps the call site short."""
    cfg = A2uiToolConfig(default_surface="workspace", default_update_mode="patch")
    toolset = make_a2ui_toolset(config=cfg)
    assert isinstance(toolset, SurfaceAwareA2uiToolset)
    assert toolset.default_surface == "workspace"
    assert toolset.default_update_mode == "patch"


# === Helpers ===


def _readonly_ctx_enabled() -> MagicMock:
    """Stand-in ReadonlyContext for `get_tools()` that returns enabled=True."""
    ctx = MagicMock()
    return ctx
