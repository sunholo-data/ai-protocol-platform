"""Tell the agent what day it is (v6.12.0).

Nothing in the instruction chain ever grounded the current date, so any
relative-time request was answered from the model's training-era guess. Measured
on deployed test 2026-07-17: asked for Danish prices "from start of year to now",
the agent queried **2024-01-01 → 2024-07-17** — right month and day, wrong YEAR —
and reported those figures as the answer. For a product whose pitch is numbers
you can check to the euro, silently answering about the wrong year is worse than
refusing: the response looks perfectly plausible.

Mirrors ``wrap_with_saved_forms`` — ``base`` may be a static string or an existing
provider, so it chains via ``compose_instruction_providers``. The date is computed
per REQUEST (inside the provider), never at import, so a long-lived container
doesn't serve a stale "today".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from google.adk.agents.readonly_context import ReadonlyContext

_BaseInstruction = str | Callable[[ReadonlyContext], Awaitable[str]]

_BLOCK_TEMPLATE = """
============================================================
Today's date is {today} (UTC). The current year is {year}.

Resolve every relative date against THIS date — "today", "now", "this year",
"start of year", "last week", "the last 30 days", "year to date". Never guess the
year: a tool that takes a date range must receive real ISO dates derived from the
date above. If a relative phrase is ambiguous, say what range you used.
============================================================
""".strip()


def render_instruction_with_today(base_str: str, now: datetime | None = None) -> str:
    """Append the current-date block to ``base_str``."""
    stamp = now or datetime.now(UTC)
    return base_str + "\n\n" + _BLOCK_TEMPLATE.format(today=stamp.strftime("%Y-%m-%d"), year=stamp.year)


def wrap_with_today(base: _BaseInstruction) -> Callable[[ReadonlyContext], Awaitable[str]]:
    """Return an ``InstructionProvider`` that grounds the agent in today's date."""

    async def _provider(ctx: ReadonlyContext) -> str:
        base_str = await base(ctx) if callable(base) else base
        return render_instruction_with_today(base_str)

    return _provider


__all__ = ["render_instruction_with_today", "wrap_with_today"]
