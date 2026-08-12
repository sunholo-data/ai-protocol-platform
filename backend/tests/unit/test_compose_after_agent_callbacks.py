"""Unit tests for ``compose_after_agent_callbacks``.

G26 (template-protocol-defaults.md): the helper composes N after-agent
callbacks and forwards the first non-None return — mirrors ADK's contract
that an after-agent callback either mutates state and returns None, OR
returns a ``Content`` event that ADK appends to the response stream.

The bespoke ``_composed_after_agent`` wrapper this replaces (in
``backend/adk/agent.py``) was annotated ``-> None`` and silently dropped
each callback's return value, so a callback that wanted to surface a
follow-up Card had no path to the wire. This test file pins the new
behaviour so the regression cannot reappear.
"""

from __future__ import annotations

import pytest

from adk.callbacks import compose_after_agent_callbacks


@pytest.mark.asyncio
async def test_all_callbacks_return_none_chain_returns_none():
    """When every callback returns None the composed result is None."""
    calls: list[str] = []

    def cb_a(ctx):
        calls.append("a")
        return None

    async def cb_b(ctx):
        calls.append("b")
        return None

    composed = compose_after_agent_callbacks(cb_a, cb_b)
    result = await composed(object())

    assert result is None
    assert calls == ["a", "b"]  # both ran when neither short-circuited


@pytest.mark.asyncio
async def test_first_non_none_return_wins_and_short_circuits():
    """The first callback to return non-None short-circuits the chain."""
    calls: list[str] = []
    sentinel = object()

    def cb_a(ctx):
        calls.append("a")
        return None

    async def cb_b(ctx):
        calls.append("b")
        return sentinel

    def cb_c(ctx):  # must not run
        calls.append("c")
        return "should-not-appear"

    composed = compose_after_agent_callbacks(cb_a, cb_b, cb_c)
    result = await composed(object())

    assert result is sentinel
    assert calls == ["a", "b"]  # cb_c never ran


@pytest.mark.asyncio
async def test_mixed_sync_and_async_callbacks_both_awaited():
    """Sync and async callbacks compose without forgetting to await."""

    def cb_sync(ctx):
        return None

    async def cb_async(ctx):
        return "from-async"

    composed = compose_after_agent_callbacks(cb_sync, cb_async)
    result = await composed(object())

    assert result == "from-async"


@pytest.mark.asyncio
async def test_zero_callbacks_returns_none():
    """An empty composition is a valid no-op."""
    composed = compose_after_agent_callbacks()
    assert await composed(object()) is None


@pytest.mark.asyncio
async def test_callback_context_is_threaded_through():
    """The same callback_context object reaches every callback."""
    ctx = object()
    seen: list[object] = []

    def cb_a(received):
        seen.append(received)
        return None

    async def cb_b(received):
        seen.append(received)
        return None

    composed = compose_after_agent_callbacks(cb_a, cb_b)
    await composed(ctx)

    assert seen == [ctx, ctx]
