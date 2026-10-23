"""The ONE place this codebase touches ADK's private compaction helpers.

Every `google.adk.apps.compaction._*` name we depend on goes through here, so
an ADK bump has exactly one file to re-check (ADK 2 upgrade sub-doc 04,
docs/design/template/template-adk-2-compaction-port.md). Two kinds of entry:

- OWNED: `pending_function_call_ids` and
  `truncate_events_before_pending_function_call` were removed in ADK 2.x
  (folded into `_longest_self_contained_prefix`, whose "provably dead call"
  pass deliberately compacts PAST some unanswered calls). The second pass needs
  the strict 1.x semantics — any unanswered call inside the span means decline
  — so we keep our own copy rather than track ADK's replacement. Parity with
  the 1.39.1 originals is pinned by a golden file
  (tests/unit/test_compaction_compat_parity.py).

- RE-EXPORTED: helpers that still exist in every version we run. Thin wrappers
  with a deferred import, matching how the callers imported them before: the
  `google.adk.apps` package is heavy and these paths are cold (admin routes,
  the idle-session task), so nothing is paid at module import. The parity test
  calls each wrapper on every venv, so a removal fails CI rather than the first
  production second pass.
"""

from __future__ import annotations

from google.adk.events.event import Event

# ---------------------------------------------------------------------------
# OWNED — ported from google-adk 1.39.1, google/adk/apps/compaction.py
# (identical in 1.31.1; removed in 2.x).
#
# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this code except in compliance with the License. You may obtain a copy
# of the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required
# by applicable law or agreed to in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
#
# Modifications: renamed to public names, reformatted to this codebase's style.
# Behaviour is unchanged — the parity golden holds us to that.
# ---------------------------------------------------------------------------


def _event_function_call_ids(event: Event) -> set[str]:
    """Returns function call ids found in an event."""
    return {fc.id for fc in event.get_function_calls() if fc.id}


def _event_function_response_ids(event: Event) -> set[str]:
    """Returns function response ids found in an event."""
    return {fr.id for fr in event.get_function_responses() if fr.id}


def pending_function_call_ids(events: list[Event]) -> set[str]:
    """Returns function call IDs that have no matching response in the session.

    Scans the session once, collecting function call IDs and response IDs, then
    returns the call IDs that are not covered by any response. Events containing
    these IDs represent pending (unanswered) function calls that must not be
    compacted.
    """
    all_call_ids: set[str] = set()
    all_response_ids: set[str] = set()
    for event in events:
        all_call_ids.update(_event_function_call_ids(event))
        all_response_ids.update(_event_function_response_ids(event))
    return all_call_ids - all_response_ids


def _has_pending_function_call(event: Event, pending_ids: set[str]) -> bool:
    """Returns True if the event contains any pending function call."""
    call_ids = _event_function_call_ids(event)
    return bool(call_ids and not call_ids.isdisjoint(pending_ids))


def truncate_events_before_pending_function_call(events: list[Event], pending_ids: set[str]) -> list[Event]:
    """Returns the leading contiguous events that avoid pending function calls."""
    for index, event in enumerate(events):
        if _has_pending_function_call(event, pending_ids):
            return events[:index]
    return events


# ---------------------------------------------------------------------------
# RE-EXPORTED — private ADK helpers still present in 1.31.1, 1.39.1 and 2.12.0
# (checked against the installed sources, 2026-10-17). Signatures and return
# shapes are unchanged across those versions.
# ---------------------------------------------------------------------------


def valid_compactions(events: list[Event]) -> list[tuple[int, float, float, Event]]:
    """`(index, start_ts, end_ts, event)` for every compaction with a full range."""
    from google.adk.apps.compaction import _valid_compactions

    return _valid_compactions(events)


def latest_compaction_event(events: list[Event]) -> Event | None:
    """The latest non-subsumed compaction event — the one the next live
    compaction seeds from."""
    from google.adk.apps.compaction import _latest_compaction_event

    return _latest_compaction_event(events)


def events_to_compact_for_token_threshold(*, events: list[Event], event_retention_size: int) -> list[Event]:
    """ADK's own token-threshold candidate selection (seed summary first).

    NOTE: in 2.x this applies `_longest_self_contained_prefix`, not the strict
    pending-call truncation above, so the replay route reflects whichever ADK
    is installed — which is what the replay is for.
    """
    from google.adk.apps.compaction import _events_to_compact_for_token_threshold

    return _events_to_compact_for_token_threshold(events=events, event_retention_size=event_retention_size)
