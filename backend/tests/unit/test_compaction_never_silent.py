"""Compaction must announce itself (COMPACTION-WIRE M4).

Compaction silently rewrites what the assistant can remember. The user keeps
seeing a full transcript while the model sees a summary, so a degraded answer
looks identical to a good one — which is precisely how the 2026-08-06 UAT issue
went undiagnosed, and why this whole sprint had to reverse-engineer an invisible
mechanism from raw session events.

CLAUDE.md #8 (NEVER SILENT) applied to context rather than to actions: every
compaction emits a CUSTOM event so the user sees it happened and a triager can
answer "was this session compacted, and when" without reading the session store.

SECURITY: the event carries METADATA ONLY — counts and timestamps, never summary
text. Summaries are derived from customer conversation content (contracts,
prices, counterparties), and `stream_invariants.redact_privileged_results`
withholds tool payloads from lower-trust group sessions. A compaction event
leaking summary text would route around that gate entirely.
"""

from __future__ import annotations

import pytest
from google.adk.events.event import Event
from google.genai import types

from adk.compaction_summarizer import COMPACTION_EVENT_NAME, FidelityEventSummarizer


class _FakeTracker:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit_reliability_event(self, name: str, value: dict) -> None:
        self.events.append((name, value))


class _StubLlm:
    model = "stub-model"

    async def generate_content_async(self, _req, stream=False):
        yield type(
            "R",
            (),
            {"content": types.Content(role="model", parts=[types.Part(text="SUMMARY BODY: strike price EUR 48.62")])},
        )()


def _events(n: int) -> list[Event]:
    return [
        Event(author="user", content=types.Content(role="user", parts=[types.Part(text=f"turn {i}")])) for i in range(n)
    ]


@pytest.fixture
def tracker(monkeypatch):
    t = _FakeTracker()
    monkeypatch.setattr("observability.timing.get_current_tracker", lambda: t)
    return t


class TestStartNoticeComesFirst:
    """COMPACTION-LATENCY M2 — the ordering IS the feature.

    `HISTORY_COMPACTED` reports a finished compaction, so it lands after the
    ~35s model call, at roughly the same moment as RUN_FINISHED. The frontend
    holds `isLoading` until the run finalises, so the composer stays disabled
    and the indicator spins for that whole stretch with the answer already on
    screen — measured at ~37s median, up to 47s.

    `COMPACTION_STARTED` fires BEFORE the call, which is the only position from
    which it can tell the client anything useful.
    """

    @pytest.mark.asyncio
    async def test_start_is_emitted_before_the_completion_event(self, tracker):
        from adk.compaction_summarizer import COMPACTION_STARTED_EVENT_NAME

        s = FidelityEventSummarizer(llm=_StubLlm())
        await s.maybe_summarize_events(events=_events(6))
        names = [n for n, _ in tracker.events]
        assert names == [COMPACTION_STARTED_EVENT_NAME, COMPACTION_EVENT_NAME], (
            f"expected start-then-finish, got {names}. If the start notice moved "
            "after the model call it is useless — that is the bug this fixes."
        )

    @pytest.mark.asyncio
    async def test_start_fires_even_if_summarisation_returns_nothing(self, tracker):
        """The client must be released regardless of the outcome.

        A summariser that declines still burned the time; leaving the UI spinning
        because nothing was produced would be the worst case.
        """
        from adk.compaction_summarizer import COMPACTION_STARTED_EVENT_NAME

        class _NoneLlm:
            model = "stub"

            async def generate_content_async(self, _req, stream=False):
                yield type("R", (), {"content": None})()

        s = FidelityEventSummarizer(llm=_NoneLlm())
        await s.maybe_summarize_events(events=_events(4))
        assert [n for n, _ in tracker.events] == [COMPACTION_STARTED_EVENT_NAME]

    @pytest.mark.asyncio
    async def test_no_start_notice_when_there_is_nothing_to_compact(self, tracker):
        s = FidelityEventSummarizer(llm=_StubLlm())
        await s.maybe_summarize_events(events=[])
        assert tracker.events == []

    @pytest.mark.asyncio
    async def test_start_notice_carries_no_conversation_content(self, tracker):
        from adk.compaction_summarizer import COMPACTION_STARTED_EVENT_NAME

        s = FidelityEventSummarizer(llm=_StubLlm())
        await s.maybe_summarize_events(events=_events(3))
        name, value = tracker.events[0]
        assert name == COMPACTION_STARTED_EVENT_NAME
        assert "turn 0" not in repr(value), "start notice leaked conversation content"

    @pytest.mark.asyncio
    async def test_a_failing_start_notice_does_not_abort_compaction(self, monkeypatch):
        calls = {"n": 0}

        class _FlakyTracker:
            def emit_reliability_event(self, *_a, **_k):
                calls["n"] += 1
                if calls["n"] == 1:  # the START notice
                    raise RuntimeError("tracker exploded")

        monkeypatch.setattr("observability.timing.get_current_tracker", lambda: _FlakyTracker())
        s = FidelityEventSummarizer(llm=_StubLlm())
        assert await s.maybe_summarize_events(events=_events(4)) is not None


@pytest.mark.asyncio
async def test_compaction_emits_an_event(tracker):
    s = FidelityEventSummarizer(llm=_StubLlm())
    await s.maybe_summarize_events(events=_events(5))
    assert COMPACTION_EVENT_NAME in [n for n, _ in tracker.events], (
        "a compaction produced no completion signal — the exact silence that made this bug class undiagnosable"
    )


@pytest.mark.asyncio
async def test_event_carries_useful_metadata(tracker):
    s = FidelityEventSummarizer(llm=_StubLlm())
    await s.maybe_summarize_events(events=_events(7))
    # events[0] is now COMPACTION_STARTED (M2); the completion event is the one
    # carrying the outcome.
    value = next(v for n, v in tracker.events if n == COMPACTION_EVENT_NAME)
    assert value["events_compacted"] == 7
    # Length, not content — enough to spot a suspiciously tiny summary (the
    # 102-char summary that made M3's first fidelity check meaningless) without
    # putting the summary itself on the wire.
    assert value["summary_chars"] > 0


@pytest.mark.asyncio
async def test_event_never_carries_summary_text(tracker):
    """The security assertion. Metadata only."""
    s = FidelityEventSummarizer(llm=_StubLlm())
    await s.maybe_summarize_events(events=_events(3))
    value = next(v for n, v in tracker.events if n == COMPACTION_EVENT_NAME)
    blob = repr(value)
    assert "SUMMARY BODY" not in blob, "compaction event leaked summary text onto the wire"
    assert "48.62" not in blob, "compaction event leaked customer content onto the wire"
    assert "turn 0" not in blob, "compaction event leaked raw conversation onto the wire"


@pytest.mark.asyncio
async def test_no_event_when_nothing_was_compacted(tracker):
    """An empty compaction is not an event. Announcing a no-op would train
    people to ignore the marker."""
    s = FidelityEventSummarizer(llm=_StubLlm())
    await s.maybe_summarize_events(events=[])
    assert tracker.events == []


@pytest.mark.asyncio
async def test_emission_failure_never_breaks_compaction(monkeypatch):
    """Fail-open. Losing the notice is bad; losing the conversation is worse.

    Compaction runs inside the request flow, so an exception here would fail
    the user's turn — trading a silent degradation for a hard error.
    """

    class _Boom:
        def emit_reliability_event(self, *_a, **_k):
            raise RuntimeError("tracker exploded")

    monkeypatch.setattr("observability.timing.get_current_tracker", lambda: _Boom())
    s = FidelityEventSummarizer(llm=_StubLlm())
    result = await s.maybe_summarize_events(events=_events(4))
    assert result is not None, "a failed notice must not abort the compaction"


@pytest.mark.asyncio
async def test_works_outside_a_request_context(monkeypatch):
    """Compaction can run where no tracker is bound (a job, a test, the A2A
    path). The module NULL tracker must absorb it silently."""

    def _raise():
        raise LookupError("no tracker bound")

    monkeypatch.setattr("observability.timing.get_current_tracker", _raise)
    s = FidelityEventSummarizer(llm=_StubLlm())
    assert await s.maybe_summarize_events(events=_events(2)) is not None
