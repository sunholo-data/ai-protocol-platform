"""Unit tests for the A2UI surface resume-stash (7.5 M3).

The result emitter stashes each rendered workbench surface into SESSION-scoped
state so a page refresh / resume can rehydrate the workbench without re-running
the tool. The security-critical property: the stash key is session-scoped (no
`app:` prefix), so a stable surface id like "ppa_comparison" never leaks one
session's artifact into another (CLAUDE.md cross-session rule).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from adk.callbacks import A2UI_SURFACE_STATE_PREFIX, _stash_surface_for_resume


def _rendered(surface_id: str):
    return SimpleNamespace(
        surface_id=surface_id,
        messages=[{"createSurface": {"surfaceId": surface_id}}],
        artifact={"kind": "comparison", "title": "Comparison"},
    )


def test_stash_writes_session_scoped_key_not_app_scoped():
    """The stash key must be session-scoped (no `app:` prefix) so a fixed surface
    id ('ppa_comparison') can't collide/leak across sessions."""
    ctx = SimpleNamespace(state={})
    _stash_surface_for_resume(ctx, _rendered("ppa_comparison"), "inv:compare:1", "compare_ppa_contracts")

    key = f"{A2UI_SURFACE_STATE_PREFIX}ppa_comparison"
    assert key in ctx.state
    assert not key.startswith("app:"), "resume stash must be session-scoped, never app-scoped"
    assert not any(k.startswith("app:") for k in ctx.state), "no app-scoped keys written"


def test_stash_payload_shape_matches_live_event():
    """Stash payload carries the same fields the live CUSTOM event does, plus a
    createdAt for index ordering — so the frontend replay path is identical."""
    ctx = SimpleNamespace(state={})
    _stash_surface_for_resume(ctx, _rendered("ppa_clauses:doc-A"), "inv:extract:2", "extract_ppa_clauses")

    payload = json.loads(ctx.state["a2ui_surface:ppa_clauses:doc-A"])
    assert payload["surfaceId"] == "ppa_clauses:doc-A"
    assert payload["sourceId"] == "inv:extract:2"
    assert payload["toolName"] == "extract_ppa_clauses"
    assert payload["messages"] and payload["artifact"]["kind"] == "comparison"
    assert isinstance(payload["createdAt"], (int, float)) and payload["createdAt"] > 0


def test_reemit_to_same_surface_overwrites():
    """The latest render of a surface wins — one key per surface_id."""
    ctx = SimpleNamespace(state={})
    _stash_surface_for_resume(ctx, _rendered("ppa_comparison"), "inv:compare:1", "compare_ppa_contracts")
    _stash_surface_for_resume(ctx, _rendered("ppa_comparison"), "inv:compare:2", "compare_ppa_contracts")

    keys = [k for k in ctx.state if k.startswith(A2UI_SURFACE_STATE_PREFIX)]
    assert len(keys) == 1
    assert json.loads(ctx.state[keys[0]])["sourceId"] == "inv:compare:2"


def test_stash_failure_is_suppressed():
    """A stash failure must never break the turn (fail-open) — the live emit
    already reached the client."""
    ctx = SimpleNamespace(state=None)  # None state → helper returns quietly
    _stash_surface_for_resume(ctx, _rendered("ppa_comparison"), "inv:compare:1", "compare_ppa_contracts")
    # no exception raised
