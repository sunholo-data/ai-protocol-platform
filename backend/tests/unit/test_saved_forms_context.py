"""Tests for saved-forms instruction injection (6.11 — closes the preferences loop)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adk.saved_forms_context import render_instruction_with_saved_forms, wrap_with_saved_forms

_BASE = "You are a helpful assistant."


def test_injects_saved_forms_block():
    out = render_instruction_with_saved_forms(
        _BASE, {"a2ui_forms": {"savePreferences": {"tone": ["neutral"], "verbosity": ["short"]}}}
    )
    assert _BASE in out
    assert "Saved form inputs" in out
    assert "savePreferences" in out and "neutral" in out


def test_noop_without_forms():
    assert render_instruction_with_saved_forms(_BASE, {}) == _BASE
    assert render_instruction_with_saved_forms(_BASE, {"a2ui_forms": {}}) == _BASE
    assert render_instruction_with_saved_forms(_BASE, {"a2ui_forms": "bad"}) == _BASE


def test_wrapper_chains_over_a_provider():
    async def _base_provider(_ctx):
        return _BASE

    provider = wrap_with_saved_forms(_base_provider)
    ctx = SimpleNamespace(state={"a2ui_forms": {"x": {"a": 1}}})
    # asyncio.run() creates+closes a fresh loop, so this is order-independent —
    # get_event_loop().run_until_complete() breaks when an earlier test in the
    # suite has already closed the default loop.
    out = asyncio.run(provider(ctx))
    assert _BASE in out and "Saved form inputs" in out
