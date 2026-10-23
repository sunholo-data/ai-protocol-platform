"""The generated-document surface actually reaches the wire — v6.32.0 M2.

`backend/adk/CLAUDE.md` calls "A2UI won't render in the Workspace" a RECURRING
bug, and the reason it recurs is that every piece can look correct in isolation
while the result never becomes a workbench artifact. Testing the transform alone
proves nothing: the transform is the one part that is almost never broken.

So this exercises the REAL chain, not the transform —

    make_a2ui_result_emitter()  →  registry lookup  →  transform
                                →  LatencyTracker.emit_a2ui_surface
                                →  drain_stage_events

— with a per-request tracker bound the way an SSE endpoint binds one, and
asserts an `A2UI_SURFACE` event is genuinely on the queue with artifact metadata
attached. It also locks the unbound branch, which is the actual trap: without
`set_current_tracker`, every emit silently no-ops and the tab never appears.

This is still not a browser. The live-stream check remains the bar for calling
the feature done; this is what makes a regression fail in CI instead of in a demo.
"""

from __future__ import annotations

from typing import Any

import pytest

import adk.a2ui_document_render  # noqa: F401 — registers the mapping
from observability.timing import (
    A2UI_SURFACE_EVENT_NAME,
    LatencyTracker,
    reset_current_tracker,
    set_current_tracker,
)

_RESULT = {
    "status": "created",
    "docId": "doc-42",
    "filename": "Offer - Northwind.docx",
    "folderId": "created-folder",
    "folder": "Created",
    "target": "docx",
    "templateUsed": "Acme letterhead.docx",
    "referenceDocApplied": True,
    "templatePartsCarried": 12,
    "blockCount": 9,
    "sizeBytes": 42330,
}


class _Tool:
    name = "generate_document"


class _ToolContext:
    """Minimal stand-in with the attributes the emitter reads."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.actions = type("A", (), {"skip_summarization": False})()
        self._invocation_context = type("I", (), {"invocation_id": "inv-1", "user_id": "user123"})()


def _drain(tracker: LatencyTracker) -> list[dict[str, Any]]:
    """Return the A2UI_SURFACE payloads on the queue.

    `drain_stage_events` yields AG-UI `CustomEvent` pydantic models, not dicts —
    the payload is `.value`. Reading them as dicts is a silent no-match.
    """
    return [e.value for e in tracker.drain_stage_events() if getattr(e, "name", "") == A2UI_SURFACE_EVENT_NAME]


@pytest.fixture()
def bound_tracker():
    """Bind a per-request tracker the way an SSE endpoint must."""
    tracker = LatencyTracker()
    token = set_current_tracker(tracker)
    try:
        yield tracker
    finally:
        reset_current_tracker(token)


class TestSurfaceReachesTheWire:
    def test_a_created_document_emits_an_a2ui_surface_event(self, bound_tracker):
        from adk.callbacks import make_a2ui_result_emitter

        emitter = make_a2ui_result_emitter()
        emitter(_Tool(), {}, _ToolContext(), _RESULT)

        surfaces = _drain(bound_tracker)
        assert surfaces, "no A2UI_SURFACE event reached the queue — the tab would never appear"

    def test_the_event_carries_artifact_metadata_so_it_is_counted(self, bound_tracker):
        """Without artifact metadata, SurfaceRegistry never counts it and any
        launcher / empty state never yields (artifactCount stays 0)."""
        from adk.callbacks import make_a2ui_result_emitter

        make_a2ui_result_emitter()(_Tool(), {}, _ToolContext(), _RESULT)
        payload = _drain(bound_tracker)[0]

        artifact = payload.get("artifact")
        assert artifact, f"no artifact metadata on the surface event: {payload}"
        assert artifact["kind"] == "document"
        assert artifact["title"] == "Offer - Northwind.docx"
        assert "Acme letterhead.docx" in artifact["description"]

    def test_it_targets_a_per_document_surface(self, bound_tracker):
        from adk.callbacks import make_a2ui_result_emitter

        make_a2ui_result_emitter()(_Tool(), {}, _ToolContext(), _RESULT)
        payload = _drain(bound_tracker)[0]

        assert payload["surfaceId"] == "created_document:doc-42"
        # The surface the messages target must MATCH the emission surface, or the
        # client processor creates the wrong surface and the tab never appears.
        assert payload["messages"][0]["createSurface"]["surfaceId"] == "created_document:doc-42"

    def test_the_double_wrapped_result_envelope_is_unwrapped(self, bound_tracker):
        """Wire hazard 1 (CLAUDE.md): tool results arrive as {"result": "<json>"}."""
        import json

        from adk.callbacks import make_a2ui_result_emitter

        wrapped = {"result": json.dumps(_RESULT)}
        make_a2ui_result_emitter()(_Tool(), {}, _ToolContext(), wrapped)

        assert _drain(bound_tracker), "a double-wrapped result must still render"

    def test_a_failed_generation_also_surfaces(self, bound_tracker):
        """NEVER-SILENT #8: the error path must render, not vanish."""
        from adk.callbacks import make_a2ui_result_emitter

        failed = {"status": "failed", "filename": "Offer.docx", "error": "Conversion failed: bad table"}
        make_a2ui_result_emitter()(_Tool(), {}, _ToolContext(), failed)

        surfaces = _drain(bound_tracker)
        assert surfaces, "a failed generation must still tell the user something"
        assert "bad table" in str(surfaces[0])


class TestTheTrapItself:
    def test_without_a_bound_tracker_the_emit_silently_no_ops(self):
        """The recurring bug, locked as a test.

        Any SSE endpoint running `stream_agui_events` MUST bind a per-request
        tracker. This asserts what happens when one does not — so the failure is
        understood as a missing bind, not a broken mapping.
        """
        from adk.callbacks import make_a2ui_result_emitter
        from observability.timing import _NULL_TRACKER, get_current_tracker

        assert get_current_tracker() is _NULL_TRACKER, "expected an unbound context"

        # Runs cleanly, returns None, and nothing reaches the wire.
        result = make_a2ui_result_emitter()(_Tool(), {}, _ToolContext(), _RESULT)
        assert result is None

        tracker = LatencyTracker()
        token = set_current_tracker(tracker)
        try:
            assert _drain(tracker) == [], "the unbound emit must not have queued anything"
        finally:
            reset_current_tracker(token)
