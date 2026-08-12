"""Teach every agent the SVG-fence contract the chat renderer requires.

The frontend only turns SVG into a picture when it arrives inside a fenced
```svg block; a bare <svg> tag is stripped and renders nothing, which reads to
users as the model "refusing" to produce SVG. This block, composed into every
agent's instruction, tells the model the one wrapper the renderer recognises.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adk.output_format_context import render_instruction_with_output_format, wrap_with_output_format

_BASE = "You are a PPA expert."


def test_states_the_svg_fence_contract():
    out = render_instruction_with_output_format(_BASE)
    assert _BASE in out
    assert "```svg" in out
    # The load-bearing instruction: the fence is required, bare <svg> won't render.
    assert "bare <svg>" in out and "will NOT" in out


def test_names_the_sanitiser_forbidden_constructs():
    out = render_instruction_with_output_format(_BASE)
    assert "<script>" in out and "href" in out


def test_wrapper_chains_over_a_provider():
    async def _base_provider(_ctx):
        return _BASE

    provider = wrap_with_output_format(_base_provider)
    # asyncio.run, not get_event_loop() — mirrors test_today_context (the default
    # loop other tests close makes get_event_loop() flaky on suite ordering).
    out = asyncio.run(provider(SimpleNamespace(state={})))
    assert _BASE in out
    assert "```svg" in out
