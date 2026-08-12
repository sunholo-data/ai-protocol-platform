"""Overlapping turns on one session (COMPACTION-LATENCY M2).

M2 releases the composer the moment the ANSWER is done, while the compaction is
still running. That is the point — the user stops waiting ~37s for housekeeping.
It also means the user can send turn N+1 **while turn N's compaction is still in
flight**, which was impossible before: the disabled composer serialised them.

So M2 traded a latency bug for a concurrency question, and this file answers it.
The same risk applies to the rejected "own the compaction" route, so it needed
answering either way.

ADK's defence is `_events_to_compact_for_token_threshold`: candidates are
filtered to events *after* `_latest_compaction_end_timestamp`, so a compaction
that lands mid-flight shrinks the next one's candidate set rather than
duplicating it. That is the invariant asserted here — it has never been
exercised concurrently in this repo, and "should be fine" is not a test.
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.apps.compaction import _events_to_compact_for_token_threshold
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from adk.compaction_summarizer import FidelityEventSummarizer

pytestmark = pytest.mark.adk_contract


def _event(i: int, ts: float) -> Event:
    e = Event(author="user", content=types.Content(role="user", parts=[types.Part(text=f"turn {i}")]))
    e.timestamp = ts
    return e


def _compaction_event(start: float, end: float) -> Event:
    return Event(
        author="user",
        timestamp=end,
        actions=EventActions(
            compaction=EventCompaction(
                start_timestamp=start,
                end_timestamp=end,
                compacted_content=types.Content(role="model", parts=[types.Part(text="summary")]),
            )
        ),
    )


class TestOverlappingCompactions:
    def test_a_landed_compaction_excludes_its_events_from_the_next(self):
        """The invariant that makes overlap safe.

        Turn N compacts events 0-9 and appends its summary. Turn N+1 — sent by a
        user M2 has just released — must NOT re-compact 0-9, or the same history
        is summarised twice and the second summary is built from the first.
        """
        events = [_event(i, float(i)) for i in range(20)]
        events.append(_compaction_event(0.0, 9.0))
        events.extend(_event(i, float(i)) for i in range(20, 30))

        candidates = _events_to_compact_for_token_threshold(events=events, event_retention_size=5)

        # NOTE the one thing that IS legitimately re-offered: ADK seeds the
        # candidate list with the previous compaction's summary content, as a
        # plain event, "so the next summary can supersede it". That is a rolling
        # summary, not double-work — and it is also why summaries grow turn over
        # turn, which is the mechanism behind the cost curve M1 measured
        # (11s → 27s → … → plateau ~45s).
        original_turns = [
            e
            for e in candidates
            if e.content and e.content.parts and (e.content.parts[0].text or "").startswith("turn ")
        ]
        stale = [e for e in original_turns if e.timestamp <= 9.0]
        assert not stale, (
            f"{len(stale)} ORIGINAL turns already covered by a landed compaction were offered "
            "again — overlapping turns would double-summarise the same history"
        )
        assert original_turns, "nothing offered at all — the fixture proves nothing"

    def test_no_candidates_when_everything_is_already_compacted(self):
        """Turn N+1 arriving right behind turn N's compaction must find nothing
        to do, rather than re-summarising the tail."""
        events = [_event(i, float(i)) for i in range(10)]
        events.append(_compaction_event(0.0, 9.0))
        assert _events_to_compact_for_token_threshold(events=events, event_retention_size=5) == []

    def test_retention_still_applies_after_a_compaction(self):
        events = [_event(i, float(i)) for i in range(10)]
        events.append(_compaction_event(0.0, 4.0))
        events.extend(_event(i, float(i)) for i in range(10, 20))
        candidates = _events_to_compact_for_token_threshold(events=events, event_retention_size=100)
        assert candidates == [], "retention floor ignored — recent turns would be compacted away"


class TestConcurrentSummarisers:
    """Two summarisers running at once must not corrupt each other.

    Ours is stateless by design (it holds only an llm and a prompt), but that is
    an assumption worth pinning: adding per-instance mutable state later would
    make overlapping turns interfere in a way no single-turn test would catch.
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_summarisations_both_complete(self):
        class _SlowLlm:
            model = "stub"

            async def generate_content_async(self, _req, stream=False):
                await asyncio.sleep(0.05)  # overlap the two calls
                yield type(
                    "R",
                    (),
                    {"content": types.Content(role="model", parts=[types.Part(text="summary")])},
                )()

        s = FidelityEventSummarizer(llm=_SlowLlm())
        a, b = await asyncio.gather(
            s.maybe_summarize_events(events=[_event(i, float(i)) for i in range(5)]),
            s.maybe_summarize_events(events=[_event(i, float(i)) for i in range(5, 10)]),
        )
        assert a is not None and b is not None
        # Each compaction must describe its OWN range — shared state would smear
        # one turn's boundaries onto the other's summary event.
        assert a.actions.compaction.start_timestamp == 0.0
        assert b.actions.compaction.start_timestamp == 5.0
