"""Ground the agent in today's date (v6.12.0).

Deployed test 2026-07-17: asked for prices "from start of year to now", the agent
queried 2024-01-01 → 2024-07-17 — right month/day, wrong YEAR — and presented
those numbers as the answer. Nothing ever told it what day it is.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from adk.today_context import render_instruction_with_today, wrap_with_today

_BASE = "You are a PPA expert."


def test_states_the_date_and_the_year():
    out = render_instruction_with_today(_BASE, now=datetime(2026, 7, 17, tzinfo=UTC))
    assert _BASE in out
    assert "2026-07-17" in out
    assert "current year is 2026" in out


def test_tells_the_model_to_resolve_relative_dates_against_it():
    out = render_instruction_with_today(_BASE, now=datetime(2026, 7, 17, tzinfo=UTC))
    # (substring, not phrase — the template hard-wraps at 80 cols)
    assert "start of year" in out and "Never guess" in out and "real ISO dates" in out


def test_wrapper_chains_over_a_provider():
    async def _base_provider(_ctx):
        return _BASE

    provider = wrap_with_today(_base_provider)
    # asyncio.run, not get_event_loop() — the latter depends on the deprecated
    # default loop that other tests close, so it fails on suite ordering.
    out = asyncio.run(provider(SimpleNamespace(state={})))
    assert _BASE in out
    # Computed per request, so it reflects the real current year, not import time.
    assert str(datetime.now(UTC).year) in out
