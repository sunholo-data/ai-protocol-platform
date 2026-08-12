"""Unit tests for the result → A2UI mapping registry + emission path
(tool-results-as-a2ui / 7.3, Model B — M1).

Covers:
  * ``adk.a2ui_result_render`` — register / render_for / render_by_name /
    is_render_payload_tool, first-match-wins, and fail-open on a bad transform.
  * ``adk.callbacks.make_a2ui_result_emitter`` — the after_tool_callback runs a
    registered transform and pushes an ``A2UI_SURFACE`` CUSTOM event onto the
    per-request LatencyTracker's drain (out of the model's context), with a
    unique idempotency key per emit (progressive fill).
  * ``adk.callbacks._handle_large_output`` — the offload exemption is now
    registry-driven (a registered tool is never offloaded; an unregistered one
    still is).

Hermetic: the ``isolated_registry`` fixture snapshots the module registry,
hands the test a clean one, and restores it afterward.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import adk.a2ui_result_render as reg
from adk.a2ui_result_render import (
    BASIC_CATALOG_ID,
    WORKSPACE_SURFACE_ID,
    is_render_payload_tool,
    register,
    registered_mapping_names,
    render_by_name,
    render_for,
)
from adk.callbacks import _handle_large_output, make_a2ui_result_emitter
from observability.timing import (
    A2UI_SURFACE_EVENT_NAME,
    LatencyTracker,
    reset_current_tracker,
    set_current_tracker,
)


@pytest.fixture
def isolated_registry():
    """Give each test a clean registry; restore the real one afterward.

    Mutates the module list in place (clear/extend) so the functions that
    closed over ``_registry`` at import time still see the changes.
    """
    saved = list(reg._registry)
    reg._registry.clear()
    yield
    reg._registry.clear()
    reg._registry.extend(saved)


def _createSurface_msg(surface_id: str = WORKSPACE_SURFACE_ID) -> dict:
    return {"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": BASIC_CATALOG_ID}}


# --- registry ---


def test_render_for_runs_matching_transform(isolated_registry):
    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["dummy_tool"], name="dummy")
    out = render_for("dummy_tool", {"doc_id": "x"})
    assert out is not None
    assert out[0]["createSurface"]["surfaceId"] == WORKSPACE_SURFACE_ID


def test_render_for_returns_none_when_no_mapping(isolated_registry):
    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["dummy_tool"], name="dummy")
    assert render_for("some_other_tool", {"x": 1}) is None


def test_render_for_first_match_wins(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["t"], name="first", surface="first")
    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["t"], name="second", surface="second")
    # First matching mapping wins → its resolved surface is used.
    assert render_for_emit("t", {}).surface_id == "first"


def test_render_for_result_matcher_narrows(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    register(
        lambda r, ctx=None: [_createSurface_msg()],
        tool_names=["poly_tool"],
        result_matcher=lambda r: isinstance(r, dict) and "differences" in r,
        name="comparison",
        surface="comparison",
    )
    register(
        lambda r, ctx=None: [_createSurface_msg()],
        tool_names=["poly_tool"],
        result_matcher=lambda r: isinstance(r, dict) and "clauses" in r,
        name="clauses",
        surface="clauses",
    )
    assert render_for_emit("poly_tool", {"differences": []}).surface_id == "comparison"
    assert render_for_emit("poly_tool", {"clauses": []}).surface_id == "clauses"


def test_render_for_emit_retargets_message_surface_ids(isolated_registry):
    """REGRESSION GUARD (7.5): the emitted messages' inner surfaceId MUST equal
    the resolved emission surface. A transform builds A2UI with a `workspace`
    placeholder; per-artifact routing sends it elsewhere — if the two don't match,
    the client builds the wrong SurfaceModel and the artifact tab never renders
    (the exact bug that shipped no tabs). This test is the fixable-once tripwire."""
    from adk.a2ui_result_render import render_for_emit

    register(
        lambda r, ctx=None: [
            {"version": "v0.9", "createSurface": {"surfaceId": "workspace", "catalogId": BASIC_CATALOG_ID}},
            {"version": "v0.9", "updateComponents": {"surfaceId": "workspace", "components": []}},
            {"version": "v0.9", "updateDataModel": {"surfaceId": "workspace", "path": "/", "value": {}}},
        ],
        tool_names=["t"],
        name="m",
        surface="artifact:xyz",
    )
    r = render_for_emit("t", {})
    assert r.surface_id == "artifact:xyz"
    # Every message that carries a surfaceId is retargeted to match.
    assert r.messages[0]["createSurface"]["surfaceId"] == "artifact:xyz"
    assert r.messages[1]["updateComponents"]["surfaceId"] == "artifact:xyz"
    assert r.messages[2]["updateDataModel"]["surfaceId"] == "artifact:xyz"


def test_render_for_fail_open_on_transform_error(isolated_registry):
    def _boom(_r, _ctx=None):
        raise ValueError("transform bug")

    register(_boom, tool_names=["dummy_tool"], name="boom")
    # A raising transform must not propagate — render_for returns None.
    assert render_for("dummy_tool", {}) is None


def test_render_for_returns_none_when_transform_declines(isolated_registry):
    register(lambda r, ctx=None: None, tool_names=["dummy_tool"], name="declines")
    assert render_for("dummy_tool", {}) is None


def test_render_by_name_ignores_matchers(isolated_registry):
    register(
        lambda r, ctx=None: [_createSurface_msg("by-name")],
        tool_names=["only_this_tool"],
        name="named_map",
    )
    # render_by_name bypasses tool-name matching (used by the CLI preview).
    out = render_by_name("named_map", {"any": "result"})
    assert out[0]["createSurface"]["surfaceId"] == "by-name"


def test_render_by_name_unknown_raises_keyerror(isolated_registry):
    with pytest.raises(KeyError):
        render_by_name("nonexistent", {})


def test_is_render_payload_tool_tracks_registered_names(isolated_registry):
    assert is_render_payload_tool("dummy_tool") is False
    register(lambda r, ctx=None: None, tool_names=["dummy_tool"], name="dummy")
    assert is_render_payload_tool("dummy_tool") is True
    assert is_render_payload_tool("unregistered") is False


def test_registered_mapping_names_in_order(isolated_registry):
    register(lambda r, ctx=None: None, tool_names=["a"], name="alpha")
    register(lambda r, ctx=None: None, tool_names=["b"], name="beta")
    assert registered_mapping_names() == ["alpha", "beta"]


def test_ppa_mappings_registered_on_import():
    """Importing adk.a2ui_ppa_render registers the two PPA tools — so the
    offload exemption survives the retiral of the hardcoded
    _RENDER_PAYLOAD_TOOLS set. The composition root (adk.agent) imports this
    module for its side effect at startup."""
    import adk.a2ui_ppa_render  # noqa: F401 — triggers the side-effect registration

    assert is_render_payload_tool("compare_ppa_contracts") is True
    assert is_render_payload_tool("extract_ppa_clauses") is True


# --- artifact routing (7.5): surface strategy + metadata ---------------------


def test_render_for_emit_default_surface_is_workspace(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["t"], name="m")
    rendered = render_for_emit("t", {"x": 1})
    assert rendered is not None
    assert rendered.surface_id == "workspace"
    assert rendered.artifact is None  # no artifact_meta declared


def test_render_for_emit_literal_surface_and_artifact(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    register(
        lambda r, ctx=None: [_createSurface_msg()],
        tool_names=["t"],
        name="m",
        surface="my_artifact",
        artifact_meta=lambda r: {"kind": "k", "title": "T", "description": "D"},
    )
    rendered = render_for_emit("t", {"x": 1})
    assert rendered.surface_id == "my_artifact"
    assert rendered.artifact == {"kind": "k", "title": "T", "description": "D"}


def test_render_for_emit_callable_surface_per_entity(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    register(
        lambda r, ctx=None: [_createSurface_msg()],
        tool_names=["t"],
        name="m",
        surface=lambda r: f"item:{r['id']}",
    )
    assert render_for_emit("t", {"id": "a"}).surface_id == "item:a"
    assert render_for_emit("t", {"id": "b"}).surface_id == "item:b"


def test_render_for_emit_surface_strategy_fail_safe(isolated_registry):
    from adk.a2ui_result_render import render_for_emit

    def _boom_surface(_r):
        raise ValueError("bad")

    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["t"], name="m", surface=_boom_surface)
    # A raising surface strategy falls back to the workspace surface.
    assert render_for_emit("t", {}).surface_id == "workspace"


# --- emission callback ---


def _bind_tracker() -> tuple[LatencyTracker, object]:
    tracker = LatencyTracker(skill_id="t", session_id="s", user_id="u")
    token = set_current_tracker(tracker)
    return tracker, token


def test_emitter_pushes_a2ui_surface_event(isolated_registry):
    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["dummy_tool"], name="dummy")
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        out = emitter(
            tool=SimpleNamespace(name="dummy_tool"),
            args={},
            tool_context=SimpleNamespace(invocation_id="inv-1"),
            tool_response='{"doc_id": "x"}',  # tools return a JSON string
        )
        assert out is None  # purely observational — never rewrites the response
        events = tracker.drain_stage_events()
        assert len(events) == 1
        ev = events[0]
        assert ev.name == A2UI_SURFACE_EVENT_NAME
        assert ev.value["surfaceId"] == WORKSPACE_SURFACE_ID
        assert ev.value["messages"][0]["createSurface"]["surfaceId"] == WORKSPACE_SURFACE_ID
        assert ev.value["sourceId"] == "inv-1:dummy_tool:1"
    finally:
        reset_current_tracker(token)


def test_emitter_source_ids_unique_for_progressive_fill(isolated_registry):
    register(lambda r, ctx=None: [_createSurface_msg()], tool_names=["dummy_tool"], name="dummy")
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        ctx = SimpleNamespace(invocation_id="inv-9")
        emitter(tool=SimpleNamespace(name="dummy_tool"), args={}, tool_context=ctx, tool_response="{}")
        emitter(tool=SimpleNamespace(name="dummy_tool"), args={}, tool_context=ctx, tool_response="{}")
        source_ids = [ev.value["sourceId"] for ev in tracker.drain_stage_events()]
        assert source_ids == ["inv-9:dummy_tool:1", "inv-9:dummy_tool:2"]
    finally:
        reset_current_tracker(token)


def test_emitter_no_event_when_tool_unregistered(isolated_registry):
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        emitter(
            tool=SimpleNamespace(name="not_registered"),
            args={},
            tool_context=SimpleNamespace(invocation_id="inv-1"),
            tool_response='{"x": 1}',
        )
        assert tracker.drain_stage_events() == []
    finally:
        reset_current_tracker(token)


def test_emitter_renders_mapped_tool_with_freetext_result_from_context(isolated_registry):
    """6.11: a mapped tool that returns free TEXT (the search AgentTools) still
    renders — the transform reads tool_context, not the body. Non-JSON results of
    UNmapped tools still short-circuit (test above)."""
    seen = {}

    def _transform(typed_result, ctx=None):
        seen["typed"] = typed_result
        return [_createSurface_msg()]

    register(_transform, tool_names=["free_text_tool"], name="freetext")
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        emitter(
            tool=SimpleNamespace(name="free_text_tool"),
            args={},
            tool_context=SimpleNamespace(invocation_id="inv-2", state={}),
            tool_response="I found some recent news from Denmark: ...",  # plain text, not JSON
        )
        events = tracker.drain_stage_events()
        assert len(events) == 1 and events[0].name == A2UI_SURFACE_EVENT_NAME
        assert seen["typed"] == {}  # transform gets an empty body, reads context instead
    finally:
        reset_current_tracker(token)


def test_emitter_no_event_when_result_not_json_and_transform_declines(isolated_registry):
    """6.11: a mapped tool with a non-JSON result renders FROM CONTEXT (typed={}),
    but a transform that declines the empty body still emits nothing. (An
    always-rendering transform is covered by
    test_emitter_renders_mapped_tool_with_freetext_result_from_context.)"""
    register(
        lambda r, ctx=None: [_createSurface_msg()] if r else None,  # declines empty body
        tool_names=["dummy_tool"],
        name="dummy",
    )
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        emitter(
            tool=SimpleNamespace(name="dummy_tool"),
            args={},
            tool_context=SimpleNamespace(invocation_id="inv-1", state={}),
            tool_response="just a plain sentence, not json",
        )
        assert tracker.drain_stage_events() == []
    finally:
        reset_current_tracker(token)


def test_emitter_unwraps_result_envelope(isolated_registry):
    """A {"result": "{...}"} double-encoded envelope is peeled server-side."""
    captured = {}

    def _capture(typed, _ctx=None):
        captured["typed"] = typed
        return [_createSurface_msg()]

    register(_capture, tool_names=["dummy_tool"], name="dummy")
    tracker, token = _bind_tracker()
    try:
        emitter = make_a2ui_result_emitter()
        emitter(
            tool=SimpleNamespace(name="dummy_tool"),
            args={},
            tool_context=SimpleNamespace(invocation_id="inv-1"),
            tool_response={"result": '{"doc_id": "abc"}'},
        )
        assert captured["typed"] == {"doc_id": "abc"}
        assert len(tracker.drain_stage_events()) == 1
    finally:
        reset_current_tracker(token)


# --- offload exemption (registry-driven) ---


async def test_registered_render_tool_never_offloaded(isolated_registry):
    register(lambda r, ctx=None: None, tool_names=["render_tool"], name="rt")
    ctx = MagicMock()
    ctx.save_artifact = AsyncMock()
    big = "x" * 60_000  # well over the 50K offload threshold
    out = await _handle_large_output(
        tool=SimpleNamespace(name="render_tool"), args={}, tool_context=ctx, tool_response=big
    )
    assert out is big  # returned unchanged — not offloaded
    ctx.save_artifact.assert_not_called()


async def test_unregistered_tool_still_offloaded(isolated_registry):
    ctx = MagicMock()
    ctx.save_artifact = AsyncMock()
    ctx.invocation_id = "inv-1"
    big = "y" * 60_000
    out = await _handle_large_output(
        tool=SimpleNamespace(name="plain_tool"), args={}, tool_context=ctx, tool_response=big
    )
    assert isinstance(out, str)
    assert out is not big  # replaced with an artifact pointer
    ctx.save_artifact.assert_called_once()
