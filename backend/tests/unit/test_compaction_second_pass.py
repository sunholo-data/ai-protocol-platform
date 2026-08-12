"""Second-pass compaction core (design 1e) — selection, staleness, supersede.

Two layers of test:

- Plain unit tests over `run_second_pass`: every terminal state is explicit and
  reachable (stale / nothing_to_improve / declined / appended), the append is
  the final act, and dry-run never writes.
- An `adk_contract` guard proving the SUPERSEDE SEAM against the real ADK
  pipeline: after our append, `_latest_compaction_event` picks the new summary
  (so the next live compaction seeds from it) and the real contents pipeline
  serves ONLY the new summary to the model. This is the guard that breaks
  loudly if an ADK bump changes the subsume contract.
"""

from __future__ import annotations

import pytest
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types

from adk.compaction_second_pass import run_second_pass


def _event(i: int, ts: float, text: str | None = None) -> Event:
    e = Event(
        author="user",
        content=types.Content(role="user", parts=[types.Part(text=text if text is not None else f"turn {i}")]),
    )
    e.timestamp = ts
    return e


def _compaction_event(start: float, end: float, text: str = "live summary") -> Event:
    e = Event(
        author="user",
        actions=EventActions(
            compaction=EventCompaction(
                start_timestamp=start,
                end_timestamp=end,
                compacted_content=types.Content(role="model", parts=[types.Part(text=text)]),
            )
        ),
    )
    e.timestamp = end
    return e


def _pending_call_event(ts: float) -> Event:
    e = Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="slow_tool", id="never-answered", args={}))],
        ),
    )
    e.timestamp = ts
    return e


class _Session:
    def __init__(self, events):
        self.id = "s-1"
        self.events = events


class _Service:
    def __init__(self):
        self.appended: list[Event] = []

    async def append_event(self, *, session, event):
        self.appended.append(event)


class _StubSummarizer:
    """Returns a summary event the way ADK's does — stamped with the range of
    the events it SAW, which the core must then widen to the union range."""

    def __init__(self, text: str | None = "second-pass summary"):
        self.text = text
        self.calls: list[list[Event]] = []

    async def maybe_summarize_events(self, *, events):
        self.calls.append(list(events))
        if self.text is None:
            return None
        return Event(
            author="user",
            actions=EventActions(
                compaction=EventCompaction(
                    start_timestamp=events[0].timestamp,
                    end_timestamp=events[-1].timestamp,
                    compacted_content=types.Content(role="model", parts=[types.Part(text=self.text)]),
                )
            ),
        )


@pytest.mark.asyncio
async def test_no_existing_compaction_is_a_no_op():
    """The second pass improves summaries; it never introduces them early."""
    service = _Service()
    stub = _StubSummarizer()
    outcome = await run_second_pass(
        session=_Session([_event(i, float(i)) for i in range(6)]),
        session_service=service,
        summarizer=stub,
    )
    assert outcome.nothing_to_improve
    assert not outcome.appended and not service.appended
    assert stub.calls == []  # no model cost on a no-op


@pytest.mark.asyncio
async def test_newer_live_compaction_means_stale_and_untouched():
    """Newest task wins: a task enqueued for compaction@2.0 must yield when the
    session has since compacted again (end 4.0) — that compaction enqueued its
    own task, which covers the wider span."""
    events = [
        _event(0, 1.0),
        _event(1, 2.0),
        _compaction_event(1.0, 2.0),
        _event(2, 4.0),
        _compaction_event(1.0, 4.0),
    ]
    service = _Service()
    stub = _StubSummarizer()
    outcome = await run_second_pass(
        session=_Session(events),
        session_service=service,
        summarizer=stub,
        for_compaction_end_ts=2.0,
    )
    assert outcome.stale
    assert not service.appended and stub.calls == []


@pytest.mark.asyncio
async def test_new_raw_turns_do_not_stale_a_task():
    """The span the pass covers is the COMPACTED one — later raw turns don't
    change it, so they must not defeat the task (the gap that killed the
    every-turn-enqueue design)."""
    events = [_event(0, 1.0), _compaction_event(1.0, 1.0), _event(1, 9.0)]
    service = _Service()
    outcome = await run_second_pass(
        session=_Session(events),
        session_service=service,
        summarizer=_StubSummarizer(),
        for_compaction_end_ts=1.0,
    )
    assert outcome.appended and not outcome.stale


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent_via_the_marker():
    """At-least-once delivery: a second delivery after success finds the
    marker on the latest non-subsumed compaction and no-ops without a model
    call."""
    events = [_event(0, 1.0), _compaction_event(1.0, 1.0)]
    service = _Service()
    outcome1 = await run_second_pass(
        session=_Session(events),
        session_service=service,
        summarizer=_StubSummarizer(),
        for_compaction_end_ts=1.0,
    )
    assert outcome1.appended

    stub2 = _StubSummarizer()
    outcome2 = await run_second_pass(
        session=_Session([*events, *service.appended]),
        session_service=service,
        summarizer=stub2,
        for_compaction_end_ts=1.0,
    )
    assert outcome2.nothing_to_improve and stub2.calls == []
    assert len(service.appended) == 1


@pytest.mark.asyncio
async def test_superseding_event_claims_the_union_of_all_prior_ranges():
    """The stub stamps the range it saw; the core must widen it to contain
    EVERY prior compaction, or an early compaction would survive un-subsumed
    and the model request would carry two summaries."""
    events = [
        _event(0, 1.0),
        _event(1, 2.0),
        _compaction_event(1.0, 2.0),
        _event(2, 3.0),
        _event(3, 4.0),
        _compaction_event(1.0, 4.0),
        _event(4, 9.0),  # after the compacted span — must stay raw
    ]
    service = _Service()
    outcome = await run_second_pass(session=_Session(events), session_service=service, summarizer=_StubSummarizer())
    assert outcome.appended and len(service.appended) == 1
    comp = service.appended[0].actions.compaction
    assert comp.start_timestamp == 1.0
    assert comp.end_timestamp == 4.0
    assert outcome.selected_events == 4  # raw events inside the span only
    assert outcome.prior_compactions == 2
    # The idempotency marker rides the event — duplicate deliveries no-op on it.
    from adk.compaction_second_pass import SECOND_PASS_MARKER

    assert (service.appended[0].custom_metadata or {}).get(SECOND_PASS_MARKER) is True


@pytest.mark.asyncio
async def test_the_guard_is_set_during_summarisation_and_released_after():
    """Paired with test_a_second_pass_never_enqueues_another: the enqueue hook
    fires from inside `maybe_summarize_events`, so the flag must be visible
    THERE, not merely around the call."""
    from adk.compaction_second_pass import in_second_pass

    seen: list[bool] = []

    class _Observing(_StubSummarizer):
        async def maybe_summarize_events(self, *, events):
            seen.append(in_second_pass())
            return await super().maybe_summarize_events(events=events)

    events = [_event(0, 1.0), _compaction_event(1.0, 1.0)]
    await run_second_pass(session=_Session(events), session_service=_Service(), summarizer=_Observing())
    assert seen == [True], "guard must be set while the summariser runs"
    assert in_second_pass() is False, "guard must be released afterwards"


@pytest.mark.asyncio
async def test_dry_run_summarises_but_never_appends():
    events = [_event(0, 1.0), _compaction_event(1.0, 1.0)]
    service = _Service()
    outcome = await run_second_pass(
        session=_Session(events), session_service=service, summarizer=_StubSummarizer(), dry_run=True
    )
    assert outcome.dry_run and outcome.summary_chars > 0
    assert not outcome.appended and not service.appended


@pytest.mark.asyncio
async def test_pending_function_call_inside_span_declines_to_supersede():
    """Claiming a range while summarising only part of it would DROP the tail
    from the model's view — strictly worse than the live summary. Fail safe."""
    events = [
        _event(0, 1.0),
        _pending_call_event(2.0),
        _event(1, 3.0),
        _compaction_event(1.0, 3.0),
    ]
    service = _Service()
    stub = _StubSummarizer()
    outcome = await run_second_pass(session=_Session(events), session_service=service, summarizer=stub)
    assert outcome.nothing_to_improve
    assert not service.appended and stub.calls == []
    assert any("pending function call" in n for n in outcome.notes)


@pytest.mark.asyncio
async def test_textless_span_refuses_to_supersede_with_less():
    events = [_event(0, 1.0, text=""), _compaction_event(1.0, 1.0)]
    service = _Service()
    outcome = await run_second_pass(session=_Session(events), session_service=service, summarizer=_StubSummarizer())
    assert outcome.nothing_to_improve and not service.appended


@pytest.mark.asyncio
async def test_a_decline_is_reported_and_retryable():
    """§3.2's class: the model call was paid for nothing. The task route turns
    `retryable` into a 503 so Cloud Tasks retries — that conversion is the
    point of the feature."""
    events = [_event(0, 1.0), _compaction_event(1.0, 1.0)]
    service = _Service()
    outcome = await run_second_pass(
        session=_Session(events), session_service=service, summarizer=_StubSummarizer(text=None)
    )
    assert outcome.declined and outcome.retryable
    assert not service.appended


@pytest.mark.asyncio
async def test_a_raising_summariser_appends_nothing():
    """The append is the final statement — a crash mid-pass leaves the session
    exactly as it was, which is what makes the Cloud Tasks retry safe."""

    class _Boom:
        async def maybe_summarize_events(self, *, events):
            raise RuntimeError("model fell over")

    events = [_event(0, 1.0), _compaction_event(1.0, 1.0)]
    service = _Service()
    with pytest.raises(RuntimeError):
        await run_second_pass(session=_Session(events), session_service=service, summarizer=_Boom())
    assert not service.appended


@pytest.mark.adk_contract
class TestSupersedeSeam:
    """Hermetic real-ADK-flow guard (adk-contract-checklist): the supersede
    behaviour this feature stands on, exercised through ADK's own code."""

    def _session_after_second_pass(self):
        events = [
            _event(0, 1.0, text="strike price EUR 48.62"),
            _event(1, 2.0, text="indexation annual CPI"),
            _compaction_event(1.0, 2.0, text="live summary"),
            _event(2, 5.0, text="post-span raw turn"),
        ]
        return events

    @pytest.mark.asyncio
    async def test_next_live_compaction_seeds_from_our_summary(self):
        from google.adk.apps.compaction import _latest_compaction_event

        events = self._session_after_second_pass()
        service = _Service()
        outcome = await run_second_pass(
            session=_Session(events), session_service=service, summarizer=_StubSummarizer(text="SECOND PASS")
        )
        assert outcome.appended
        all_events = events + service.appended

        winner = _latest_compaction_event(all_events)
        text = "".join(p.text or "" for p in winner.actions.compaction.compacted_content.parts)
        assert text == "SECOND PASS"

    @pytest.mark.asyncio
    async def test_model_request_contains_only_the_new_summary(self):
        from google.adk.flows.llm_flows import contents

        events = self._session_after_second_pass()
        service = _Service()
        await run_second_pass(
            session=_Session(events), session_service=service, summarizer=_StubSummarizer(text="SECOND PASS")
        )
        all_events = events + service.appended

        effective = contents._get_contents(current_branch=None, events=all_events, agent_name="")
        texts = [p.text for c in effective for p in (c.parts or []) if p.text]

        assert any("SECOND PASS" in t for t in texts), "superseding summary must reach the model"
        assert not any("live summary" in t for t in texts), "old summary must be subsumed"
        assert not any("strike price" in t for t in texts), "raw events in the claimed range must be filtered"
        assert any("post-span raw turn" in t for t in texts), "events past the span must stay raw"

    @pytest.mark.asyncio
    async def test_interleaved_live_compaction_stays_consistent(self):
        """The doc's owed concurrency case: a user resumes mid-pass and a NEW
        live compaction lands between our read and our append. No lock exists,
        and none is needed — the wider live range strictly contains ours, so
        ours is subsumed and the model sees exactly one summary. Worst case is
        a wasted summarisation, never a corrupted request."""
        from google.adk.flows.llm_flows import contents

        events = [
            _event(0, 1.0, text="early raw"),
            _event(1, 2.0, text="mid raw"),
            _compaction_event(1.0, 2.0, text="live one"),
        ]
        session = _Session(events)
        service = _Service()

        class _RacingStub(_StubSummarizer):
            """Simulates the race: while summarising, the live path appends a
            new turn and a wider live compaction to the same session."""

            async def maybe_summarize_events(self, *, events):
                newer_turn = _event(9, 5.0, text="raced-in raw turn")
                session.events = [*session.events, newer_turn, _compaction_event(1.0, 5.0, text="live two")]
                return await super().maybe_summarize_events(events=events)

        outcome = await run_second_pass(
            session=session, session_service=service, summarizer=_RacingStub(text="SECOND PASS")
        )
        assert outcome.appended  # the pass itself does not fail

        all_events = [*session.events, *service.appended]
        effective = contents._get_contents(current_branch=None, events=all_events, agent_name="")
        texts = [p.text for c in effective for p in (c.parts or []) if p.text]

        summaries = [t for t in texts if t in ("live one", "live two", "SECOND PASS")]
        assert summaries == ["live two"], f"exactly the widest live summary must survive, got {summaries}"
        assert not any("early raw" in t for t in texts), "compacted raw turns must not leak"
