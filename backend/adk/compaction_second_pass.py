"""Second-pass compaction — re-derive an idle session's summary from raw events.

Live compaction is latency-bounded and incremental: each pass summarises the
previous summary plus new events, so omissions compound, and its quality is
capped by what a user will wait for. The raw events it summarised are still in
the session store (findings log §1) — so once a session goes idle we can
re-derive the whole compacted span from the originals with a model that has
time to think, and REPLACE the live summary.

Replacement rides ADK's own subsume semantics, verified against the pinned
1.31.1 source (design doc "Verified ADK seams"):

- `_is_compaction_subsumed` (apps/compaction.py:66): a compaction whose range
  is fully contained in another's is dropped at request-build time; on
  identical ranges the LATER event wins. So appending a summary whose range
  contains every prior compaction supersedes them all.
- `_latest_compaction_event` (:142) skips subsumed events, so the next LIVE
  compaction seeds from our improved summary — no double-seeding.
- The write is the public API: `session_service.append_event`, the same call
  ADK itself makes (:390). The append is atomic-ish; a crash before it leaves
  the session untouched and the Cloud Tasks retry runs the whole pass again.

This module is the CORE shared by both HTTP surfaces (the admin route the CLI
calls, and the OIDC-gated internal task route). It performs no auth and maps
nothing to HTTP; routes do that.

See docs/design/v6.23.0/compaction-second-pass.md and
docs/projects/compaction/README.md.
"""

from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field

from google.adk.events.event import Event

logger = logging.getLogger(__name__)

# RECURSION GUARD. The second pass drives the SAME `FidelityEventSummarizer`
# whose `maybe_summarize_events` enqueues second-pass tasks — so without this,
# a pass would schedule another pass, forever, appending a compaction event
# each cycle. Today that is masked by the task/admin paths having no bound
# LatencyTracker (so the enqueue finds no session id and skips), but that is an
# accident of plumbing, not a guarantee: binding a tracker on either path — an
# obvious future change for observability — would turn it into a live loop.
# Make the invariant explicit instead of depending on the accident.
_in_second_pass: contextvars.ContextVar[bool] = contextvars.ContextVar("aitana_in_second_pass", default=False)


def in_second_pass() -> bool:
    """True while a second pass is running in this async context."""
    return _in_second_pass.get()


def second_pass_enabled() -> bool:
    """Master switch: admin toggle (1b) wins, env var is the deploy default.

    Flag-off is total — no enqueue, and the task route 404s. Addressing (queue
    path, OIDC SA, target URL) stays env-only, so an env without a provisioned
    queue cannot be switched on from the admin panel by accident.
    """
    from adk.compaction_settings import second_pass_enabled as _resolved

    return _resolved()


# Stamped into the superseding event's `custom_metadata` (a plain dict on
# ADK's Event via LlmResponse — verified pinned 1.31.1). Live compactions never
# carry it, which is what lets the task handler tell "already second-passed"
# apart from "a newer live compaction needs its own pass". If a store ever
# drops the field, the cost is a wasted duplicate summarisation, not a wrong
# result.
SECOND_PASS_MARKER = "aitana_compaction_second_pass"


@dataclass
class SecondPassOutcome:
    """What the pass did, in numbers a triager can act on.

    Every terminal state is explicit — `stale`, `nothing_to_improve`,
    `declined`, `appended` — because the whole findings log is a record of
    compaction outcomes that were indistinguishable from each other in logs.
    """

    total_events: int = 0
    prior_compactions: int = 0
    selected_events: int = 0
    input_chars: int = 0
    stale: bool = False
    nothing_to_improve: bool = False
    declined: bool = False
    appended: bool = False
    dry_run: bool = False
    summary_chars: int = 0
    summary: str | None = None
    start_timestamp: float | None = None
    end_timestamp: float | None = None
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def retryable(self) -> bool:
        """True when a task delivery should be retried (non-2xx to Cloud Tasks).

        A decline means the model call was paid for nothing (§3.2) — exactly the
        class this feature exists to convert into guaranteed progress, so it
        retries. Stale / nothing-to-improve are successful no-ops.
        """
        return self.declined


@dataclass
class _Selection:
    events: list[Event]
    start_timestamp: float
    end_timestamp: float
    prior_compactions: int


def _select_range(events: list[Event], notes: list[str]) -> _Selection | None:
    """The raw events to re-summarise, and the range the new event must claim.

    Selection uses ADK's own helpers rather than a local reimplementation —
    replicating the logic would drift from ADK and quietly invalidate the
    supersede guarantee (same reasoning as the replay route).

    The claimed range is the union of every prior compaction's range (plus the
    earliest selected raw event), so the new event CONTAINS all of them and
    each is subsumed. The second pass only re-derives what live compaction has
    already claimed — events past the latest compaction end stay raw, which is
    also what keeps the live retention floor respected by construction.
    """
    from google.adk.apps.compaction import (
        _pending_function_call_ids,
        _truncate_events_before_pending_function_call,
        _valid_compactions,
    )

    compactions = _valid_compactions(events)
    if not compactions:
        notes.append("no existing compaction — the second pass improves summaries, it does not introduce them")
        return None

    start_ts = min(start for _, start, _, _ in compactions)
    end_ts = max(end for _, _, end, _ in compactions)

    raw = [e for e in events if not (e.actions and e.actions.compaction) and e.timestamp <= end_ts]
    if not raw:
        notes.append("no raw events inside the compacted span (nothing to re-derive)")
        return None

    truncated = _truncate_events_before_pending_function_call(raw, _pending_function_call_ids(events))
    if len(truncated) != len(raw):
        # A pending (never-answered) function call sits inside the compacted
        # span. Claiming the full range while summarising only part of it would
        # DROP the tail from the model's view — strictly worse than the live
        # summary we'd be replacing. Fail safe: leave the live summary alone.
        notes.append(
            f"pending function call inside the compacted span "
            f"({len(raw) - len(truncated)} events would be unsummarised) — declining to supersede"
        )
        return None

    start_ts = min(start_ts, raw[0].timestamp)
    return _Selection(
        events=raw,
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        prior_compactions=len(compactions),
    )


def _text_chars(events: list[Event]) -> int:
    return sum(len(p.text or "") for e in events if e.content and e.content.parts for p in e.content.parts)


async def run_second_pass(
    *,
    session,
    session_service,
    summarizer,
    for_compaction_end_ts: float | None = None,
    dry_run: bool = False,
) -> SecondPassOutcome:
    """Re-summarise the session's compacted span from raw events and append a
    superseding compaction event.

    Args:
        session: the ADK session (already loaded and authorised by the caller).
        session_service: where the superseding event is appended.
        summarizer: anything with ``maybe_summarize_events(events=...)`` —
            injected so tests and dry-runs never need a model.
        for_compaction_end_ts: the live compaction this task was enqueued FOR
            (task path only; None = the CLI path, which always runs). Two
            guards hang off it: if the session's latest non-subsumed compaction
            ends LATER, a newer live compaction exists and its own task will
            cover it → stale no-op; if the latest one carries
            ``SECOND_PASS_MARKER``, this span is already second-passed (a
            duplicate delivery) → no-op. New raw turns do NOT stale a task —
            they don't change the compacted span.
        dry_run: compute and summarise but never append.

    Raises: whatever the summariser raises. Routes map that to their transport
    (the task route returns 503 so Cloud Tasks retries). Nothing is appended on
    any raise — the append is the final statement.
    """
    events = list(session.events or [])
    outcome = SecondPassOutcome(total_events=len(events), dry_run=dry_run)

    if for_compaction_end_ts is not None:
        from google.adk.apps.compaction import _latest_compaction_event

        latest = _latest_compaction_event(events)
        latest_comp = latest.actions.compaction if latest is not None else None
        latest_end = getattr(latest_comp, "end_timestamp", None)
        if latest_end is not None and latest_end > for_compaction_end_ts:
            outcome.stale = True
            outcome.notes.append("a newer live compaction exists; its own task will second-pass it")
            return outcome
        if latest is not None and (latest.custom_metadata or {}).get(SECOND_PASS_MARKER):
            outcome.nothing_to_improve = True
            outcome.notes.append("this span is already second-passed (duplicate delivery)")
            return outcome

    selection = _select_range(events, outcome.notes)
    if selection is None:
        outcome.nothing_to_improve = True
        return outcome

    outcome.prior_compactions = selection.prior_compactions
    outcome.selected_events = len(selection.events)
    outcome.input_chars = _text_chars(selection.events)
    outcome.start_timestamp = selection.start_timestamp
    outcome.end_timestamp = selection.end_timestamp

    if outcome.input_chars == 0:
        # Replacing real summaries with a summary of nothing can only lose
        # information. Seen shapes: artifact-offloaded payloads, pure
        # function-call turns.
        outcome.nothing_to_improve = True
        outcome.notes.append("selected raw events contain no text — refusing to supersede with less")
        return outcome

    started = time.perf_counter()
    guard = _in_second_pass.set(True)
    try:
        event = await summarizer.maybe_summarize_events(events=selection.events)
    finally:
        _in_second_pass.reset(guard)
    outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)

    if event is None:
        outcome.declined = True
        outcome.notes.append("summariser declined (returned None) — findings log §3.2; task path retries")
        return outcome

    comp = event.actions.compaction
    # The summariser stamps the range of the events it SAW (first..last raw
    # timestamp). The superseding event must claim the UNION range computed
    # above, or a prior compaction with an earlier start would survive
    # un-subsumed and the request would carry two summaries.
    comp.start_timestamp = selection.start_timestamp
    comp.end_timestamp = selection.end_timestamp
    event.custom_metadata = {**(event.custom_metadata or {}), SECOND_PASS_MARKER: True}

    text = "".join(p.text or "" for p in (comp.compacted_content.parts or []))
    outcome.summary_chars = len(text)
    outcome.summary = text

    if dry_run:
        outcome.notes.append("dry run — nothing appended")
        return outcome

    await session_service.append_event(session=session, event=event)
    outcome.appended = True
    logger.info(
        "compaction.second_pass: session=%s events=%d/%d in=%d out=%d priors_superseded=%d %dms",
        getattr(session, "id", "?"),
        outcome.selected_events,
        outcome.total_events,
        outcome.input_chars,
        outcome.summary_chars,
        outcome.prior_compactions,
        outcome.elapsed_ms,
    )
    return outcome
