"""Tell every agent how to emit inline SVG so the frontend renders it.

The chat renderer only turns SVG into a picture when it arrives inside a
fenced ```svg block (``ChatMarkdown`` lifts that fence out and hands it to
``SVGBlock``, which DOMPurify-sanitises and injects it). A bare ``<svg>…</svg>``
in the message body is silently DROPPED — react-markdown discards raw HTML and
there is an explicit ``html() { return null }`` guard. So a model that emits raw
SVG produces NOTHING on screen; the natural next step for a well-behaved model is
to stop offering SVG at all ("it never shows"), which reads to the user as a
refusal. The fix is not a safety carve-out — nothing forbids SVG — it is telling
the model the ONE wrapper the renderer recognises.

Mirrors ``wrap_with_today``: unconditional (no state gate), static block, applied
to every skill via ``compose_instruction_providers``. It rides the shared chain
so BOTH Model-A (a2ui.enabled) and Model-B skills receive it — the a2ui gate only
attaches/detaches the A2UI toolset, it does not branch the instruction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from google.adk.agents.readonly_context import ReadonlyContext

_BaseInstruction = str | Callable[[ReadonlyContext], Awaitable[str]]

_BLOCK_TEMPLATE = """
============================================================
When you want to show a diagram, chart, icon, or any vector graphic inline in
chat, emit it as SVG inside a fenced code block tagged `svg`:

```svg
<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">...</svg>
```

The fence is required — a bare <svg> tag written into your message will NOT
render (it is stripped). Only the fenced form is turned into a picture. Do not
wrap it in ```html or ```xml, and do not include a <script> tag, <use> element,
or href/xlink:href attributes — those are removed by the sanitiser. Producing
SVG this way is fully supported; there is no need to decline such a request.
============================================================
""".strip()


def render_instruction_with_output_format(base_str: str) -> str:
    """Append the output-format (SVG-fence) block to ``base_str``."""
    return base_str + "\n\n" + _BLOCK_TEMPLATE


def wrap_with_output_format(base: _BaseInstruction) -> Callable[[ReadonlyContext], Awaitable[str]]:
    """Return an ``InstructionProvider`` that teaches the SVG-fence contract."""

    async def _provider(ctx: ReadonlyContext) -> str:
        base_str = await base(ctx) if callable(base) else base
        return render_instruction_with_output_format(base_str)

    return _provider


__all__ = ["render_instruction_with_output_format", "wrap_with_output_format"]
