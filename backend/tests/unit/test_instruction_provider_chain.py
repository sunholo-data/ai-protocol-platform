"""Unit tests for ``adk.instruction_provider_chain``.

The helper is pure structural composition — no I/O, no ADK runtime
needed. Verifies:
  * Empty wrapper list returns a provider that yields ``base`` as-is.
  * Single wrapper produces the wrapper's provider, with base passed
    as the first wrapper's input.
  * Multiple wrappers apply left-to-right: each subsequent wrapper
    receives the previous wrapper's PROVIDER (callable), not the
    base string.
  * Output is byte-equivalent to the hand-written nested-call form
    that lived in ``adk/agent.py`` before this helper landed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from google.adk.agents.readonly_context import ReadonlyContext

from adk.instruction_provider_chain import (
    InstructionProvider,
    compose_instruction_providers,
)


class _FakeReadonlyContext:
    """Minimal ReadonlyContext stand-in (only ``state`` is read)."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state


# A tiny wrapper-builder used to build deterministic test wrappers:
# each takes a base (str or provider) and returns a provider that
# appends its tag to the rendered output.
def _make_appender(tag: str) -> Callable:
    def _wrapper(base):
        async def _provider(ctx: ReadonlyContext) -> str:
            if callable(base):
                upstream = await base(ctx)
            else:
                upstream = base
            return f"{upstream}\n[{tag}]"

        return _provider

    return _wrapper


# ─── compose_instruction_providers ─────────────────────────────────────────


def test_no_wrappers_returns_base_verbatim():
    """When wrappers list is empty, the helper still produces a
    provider — yielding the base string unchanged."""
    provider = compose_instruction_providers("just the base")
    out = asyncio.run(provider(_FakeReadonlyContext()))
    assert out == "just the base"


def test_no_wrappers_returns_awaitable():
    """Type contract: even the no-wrapper case returns an async
    callable so the agent factory can always pass the result to
    ``LlmAgent.instruction``."""
    provider = compose_instruction_providers("base")
    result = provider(_FakeReadonlyContext())
    assert isinstance(result, Awaitable)
    asyncio.run(result)


def test_single_wrapper_applied_to_base():
    """One wrapper sees the base string and produces a single
    appender in the output."""
    chain = compose_instruction_providers("BASE", _make_appender("A"))
    out = asyncio.run(chain(_FakeReadonlyContext()))
    assert out == "BASE\n[A]"


def test_two_wrappers_apply_left_to_right():
    """Left-to-right semantics: A's block appears BEFORE B's in the
    rendered output. This is the order-of-append contract the
    agent factory depends on (iframe block before A2UI block)."""
    chain = compose_instruction_providers("BASE", _make_appender("A"), _make_appender("B"))
    out = asyncio.run(chain(_FakeReadonlyContext()))
    assert out == "BASE\n[A]\n[B]"
    # B's tag is later in the string than A's — the order-of-append
    # is structurally documented.
    assert out.index("[A]") < out.index("[B]")


def test_three_wrappers_chain_correctly():
    """Three-wrapper chain — proves the reduce fold doesn't lose
    intermediate providers' output (forks adding a third context
    source must see all three blocks)."""
    chain = compose_instruction_providers(
        "BASE",
        _make_appender("first"),
        _make_appender("second"),
        _make_appender("third"),
    )
    out = asyncio.run(chain(_FakeReadonlyContext()))
    assert out == "BASE\n[first]\n[second]\n[third]"


def test_byte_equivalent_to_hand_nested_form():
    """Regression: the helper output MUST equal the hand-written
    ``b(a(base))`` form. If anyone refactors the helper, this is the
    contract that proves they didn't accidentally swap inner/outer."""
    a = _make_appender("alpha")
    b = _make_appender("beta")

    hand_chain = b(a("BASE"))
    helper_chain = compose_instruction_providers("BASE", a, b)

    ctx = _FakeReadonlyContext()
    assert asyncio.run(hand_chain(ctx)) == asyncio.run(helper_chain(ctx))


def test_wrapper_receives_provider_not_string_after_first():
    """The second wrapper MUST receive a callable, not a string —
    proves the helper threads providers through, not re-stringifying.
    Wrappers that depend on ``await base(ctx)`` (the chain idiom)
    would break if we passed strings."""
    seen_types: list[type] = []

    def _capture_type_wrapper(base) -> InstructionProvider:
        seen_types.append(type(base))

        async def _provider(_ctx: ReadonlyContext) -> str:
            return "irrelevant"

        return _provider

    compose_instruction_providers("BASE", _capture_type_wrapper, _capture_type_wrapper)

    assert seen_types[0] is str
    # The second wrapper sees a callable (the first wrapper's output).
    assert callable(seen_types[1]) or seen_types[1] is type(lambda: None)
    # More precise: it's a function, not a str
    assert seen_types[1] is not str


def test_state_threads_through_to_innermost_wrapper():
    """The ReadonlyContext provided at call time reaches every
    wrapper that reads ctx.state — so agent prompts can mix
    state-driven blocks regardless of position in the chain."""
    seen_states: list[dict] = []

    def _state_observing_wrapper(base):
        async def _provider(ctx: ReadonlyContext) -> str:
            seen_states.append(ctx.state)
            if callable(base):
                return f"{await base(ctx)}|x"
            return f"{base}|x"

        return _provider

    chain = compose_instruction_providers("BASE", _state_observing_wrapper, _state_observing_wrapper)
    asyncio.run(chain(_FakeReadonlyContext(state={"key": "value"})))

    # Both wrappers saw the SAME state dict (same context).
    assert len(seen_states) == 2
    assert seen_states[0] == {"key": "value"}
    assert seen_states[1] == {"key": "value"}


# ─── Integration with the real wrappers ────────────────────────────────────


def test_integration_with_real_wrappers():
    """Sanity check that the helper composes with the real
    ``wrap_with_iframe_context`` + ``wrap_with_a2ui_surface_context``
    wrappers. This is the precise chain used in
    ``adk.agent.create_agent``. If those wrappers' signatures drift
    (e.g. they stop accepting str | callable), this test breaks
    first."""
    from adk.a2ui_surface_context import wrap_with_a2ui_surface_context
    from adk.iframe_context import wrap_with_iframe_context

    chain = compose_instruction_providers(
        "Do the thing.",
        wrap_with_iframe_context,
        wrap_with_a2ui_surface_context,
    )

    # Empty state → both wrappers are no-ops → base passes through.
    out = asyncio.run(chain(_FakeReadonlyContext(state={})))
    assert out == "Do the thing."

    # State with BOTH namespaces → both blocks appear, iframe first.
    state = {
        "mcp_app_context.foo.bar": {"structuredContent": {"x": 1}},
        "a2ui_surface_state": {
            "workspace": {"dataModel": {"revenue": "$1,234"}},
        },
    }
    out2 = asyncio.run(chain(_FakeReadonlyContext(state=state)))
    assert out2.startswith("Do the thing.")
    assert "Current iframe-app context" in out2
    assert "A2UI surface state" in out2
    # Iframe block appended first (leftmost wrapper).
    assert out2.index("iframe-app context") < out2.index("A2UI surface state")
