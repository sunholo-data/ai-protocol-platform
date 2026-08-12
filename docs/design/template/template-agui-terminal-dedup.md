# Template AG-UI Terminal-Event Dedup

**Status**: ✅ Implemented in platform (2026-06-06) — **template sync pending**.  
**Priority**: P1 (universal trap — every fork using `ag_ui_adk` hits this the first time a tool throws)  
**Estimated**: 0.5d planned; ~1h actual  
**Scope**: Backend (`adk/agui.py`, `tests/unit/test_agui_terminal_dedup.py`)  
**Dependencies**: None  
**Created**: 2026-06-06  
**Last Updated**: 2026-06-06  
**Source items**: G41 / Friction 22 — gde-ap-agent fork (2026-06-06), Cloud Run logs showed a tool call raised inside the agent loop and the next `@ag-ui/client` event landed with: *"Cannot send event type 'RUN_FINISHED': The run has already errored with 'RUN_ERROR'."*

## Problem Statement

The AG-UI protocol specifies that every run terminates with **exactly one** of `RUN_ERROR` or `RUN_FINISHED`. The `@ag-ui/client` state machine on the frontend enforces this invariant — a second terminal event after the first is rejected with:

```
Cannot send event type 'RUN_FINISHED': The run has already errored with 'RUN_ERROR'.
```

The vendored `ag_ui_adk` library (`adk_agent.py` in the SDK) violates this invariant when a tool call raises an exception during streaming. Two emission paths fire:

1. **Queue path (the error):** `ag_ui_adk` runs the ADK loop in a background task and routes events through an asyncio queue. When the ADK loop raises, the background `except` block at `adk_agent.py:2377` pushes a `RunErrorEvent` onto the queue. The consumer (`_stream_events`) yields it normally — **no Python exception propagates** because the queue carrier is a pydantic event object, not an exception.
2. **Normal completion path (the spurious finish):** Control returns to the outer try-block in `_start_new_execution` at line 1515-1530. That code has no idea a RUN_ERROR was already yielded from the queue — it sees the queue-iteration ended without an exception, falls through to line 1525, and emits `RunFinishedEvent`.

Both terminal events reach the frontend. The first one closes the AG-UI state machine; the second one trips the assert.

**Where the user sees it:** the chat shows the tool-error chip, then ~50ms later the agent "appears to have crashed" — but the failure was actually the assert on the SECOND event. Console shows `Cannot send event type 'RUN_FINISHED'…`. The session is now in a broken state on the client; the user has no way to recover except a page refresh.

**Universal across forks:** every fork using `ag_ui_adk` + a backend that defines any tool that can raise hits this. The trigger pattern (tool throws) is common enough that any non-trivial demo surfaces it.

## Goals

**Primary Goal:** The frontend's AG-UI state machine never sees a duplicate terminal event from a platform-shipped backend — independent of upstream `ag_ui_adk` bugs.

**Success Metrics:**
- A tool-throw mid-stream produces exactly ONE terminal event on the SSE wire (the `RUN_ERROR`).
- The frontend's "Cannot send event type 'RUN_FINISHED'" assert never fires from a platform-shipped backend.
- Symmetric defence: a hypothetical RUN_FINISHED followed by RUN_ERROR also collapses to one event.
- Observable: each suppression logs a warning with the run's `thread_id` so on-call can correlate with upstream-bug frequency.

**Non-Goals:**
- Filing the fix upstream to `ag_ui_adk`. Worth doing as a parallel track, but this design doc covers the platform-side dedup that protects every fork independent of upstream's release cadence.
- Recovering from the error case (re-running the failed tool, resuming the conversation). Out of scope — the agent legitimately failed; we only ensure the failure is delivered cleanly.
- Removing the warning log when the upstream bug is fixed. The dedup is cheap (one string comparison per event) and is a useful invariant guard against future regressions.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Removes the "agent appears to have crashed" hard-stop after a tool error — the user sees the real error chip, not a state-machine throw |
| 2 | EARNED TRUST | +1 | Users see a clean error message instead of a frontend crash; trust the system to report failures honestly |
| 3 | SKILLS, NOT FEATURES | 0 | Plumbing fix; no skill-shape change |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Not model-routing |
| 5 | GRACEFUL DEGRADATION | +2 | Tool error stays a tool error — the wire-protocol stays well-formed even when the upstream library misbehaves; classic "be conservative in what you send" |
| 6 | PROTOCOL OVER CUSTOM | +1 | Enforces the AG-UI protocol invariant (one terminal per run) against an upstream-library violation. Uses string event-type comparison so the module stays import-light |
| 7 | API FIRST | 0 | No API changes |
| 8 | OBSERVABLE BY DEFAULT | +1 | Each suppression logs `agui_terminal_dedup: dropped duplicate terminal event (first=…, dropped=…, thread_id=…)` so on-call can grep for it |
| 9 | SECURE BY CONSTRUCTION | 0 | No security surface |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend correctness fix |
| | **Net Score** | **+6** | Acceptable — proceed |

**Conflict Justifications:** None.

## Design

### Where the fix lives

`backend/adk/agui.py::stream_agui_events` already wraps `ADKAgent.run()`
in an async generator that adds latency-tracking and JSON-serializes
each event for SSE. It's the natural choke point — every AG-UI event
leaving the backend passes through this function.

### The filter

```python
_TERMINAL_EVENT_TYPES = frozenset({"RUN_ERROR", "RUN_FINISHED"})

# inside stream_agui_events:
terminal_event_yielded: str | None = None

async for event in agui_agent.run(run_input):
    event_type = getattr(event, "type", None)
    type_value = getattr(event_type, "value", str(event_type)) if event_type else None

    if type_value in _TERMINAL_EVENT_TYPES:
        if terminal_event_yielded is None:
            terminal_event_yielded = type_value
        else:
            logger.warning(
                "agui_terminal_dedup: dropped duplicate terminal event "
                "(first=%s, dropped=%s, thread_id=%s); see "
                "docs/design/template/template-agui-terminal-dedup.md",
                terminal_event_yielded, type_value,
                getattr(run_input, "thread_id", "<unknown>"),
            )
            continue

    yield event.model_dump(by_alias=True, exclude_none=True)
```

Three properties this enforces:

1. **First-write-wins.** The FIRST terminal event reaches the wire. Subsequent terminals (of either type) are dropped.
2. **Non-terminal events still pass.** The filter only acts on `RUN_ERROR`/`RUN_FINISHED`; STAGE_PROGRESS, TEXT_MESSAGE_*, TOOL_CALL_* etc. flow unchanged.
3. **Symmetric.** Both `ERROR-then-FINISHED` (the observed bug) and `FINISHED-then-ERROR` (hypothetical) collapse to one event. Hardens against a future upstream off-by-one in the opposite direction.

### Why string comparison, not `EventType` enum

`adk/agui.py` is intentionally import-light — many tests import it without touching the full `ag_ui` SDK. Comparing against `_TERMINAL_EVENT_TYPES = frozenset({"RUN_ERROR", "RUN_FINISHED"})` avoids dragging in `from ag_ui.core import EventType` at module level. The `getattr(event_type, "value", str(event_type))` pattern handles both real enum events and any future change where `.type` might be a plain string.

### Observability

Each suppression logs at WARNING level so the upstream-bug frequency can be tracked. Inclusion of the `thread_id` lets on-call correlate a chat session reporting a hard-stop with the backend's drop. Sample log line:

```
WARNING adk.agui:agui_terminal_dedup: dropped duplicate terminal event
  (first=RUN_ERROR, dropped=RUN_FINISHED, thread_id=2899c800-…); see
  docs/design/template/template-agui-terminal-dedup.md
```

Forks that want a counter can scrape this log line for `agui_terminal_dedup` matches.

### Upstream PR (parallel track, not blocking)

The right ag_ui_adk fix is at `adk_agent.py:1510-1530`: track a `has_yielded_terminal` flag during stream consumption and skip the `RUN_FINISHED` emission at line 1525 when it's set. Worth filing — but the platform-side dedup is independent of upstream's release cadence, so we don't wait.

## Implementation Plan

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add `_TERMINAL_EVENT_TYPES` constant + `terminal_event_yielded` state + dedup branch in `stream_agui_events` | 20min |
| 2 | Update module docstring with the G41 context + cross-reference to design doc | 10min |
| 3 | 6 unit tests (bug case, symmetric case, 2 happy paths, non-terminal flow, thread-id in log) | 30min |
| 4 | New design doc + SEQUENCE.md row | 15min |

**Total: ~75 min ≈ 1h** (actual run-time).

## Testing Strategy

**`backend/tests/unit/test_agui_terminal_dedup.py`** (6 cases):

1. **`test_drops_run_finished_after_run_error`** — the gde-ap-agent bug case: scripted sequence `RUN_STARTED, TOOL_CALL_START, RUN_ERROR, RUN_FINISHED`. Asserts only the first three reach the consumer; warning log contains `agui_terminal_dedup` + `first=RUN_ERROR`.
2. **`test_drops_run_error_after_run_finished`** — symmetric: `RUN_FINISHED` before a stray `RUN_ERROR`. Same assertion shape with `first=RUN_FINISHED`.
3. **`test_lone_run_error_passes_through`** — happy error path. No warning logged.
4. **`test_lone_run_finished_passes_through`** — happy completion path. Full event sequence including TEXT_MESSAGE_* passes through; no warning logged.
5. **`test_non_terminal_events_after_first_terminal_still_drop`** — invariant pin: filter scope is `RUN_ERROR`/`RUN_FINISHED` only. A hypothetical `CUSTOM_TELEMETRY` after a terminal flows through. Pins against a future widening of the filter.
6. **`test_warning_log_includes_thread_id_for_observability`** — explicit assertion that the suppression log carries the thread_id.

Mocking strategy: tiny `_FakeEvent` class with a `.type.value` string and a `.model_dump()` method; `_FakeAguiAgent.run()` async-yields a scripted list. `observability.timing.get_current_tracker` patched to a no-op MagicMock so the function under test doesn't touch real instrumentation. No `ag_ui` SDK import needed in tests.

## Success Criteria

- [x] `stream_agui_events` keeps the FIRST terminal event seen and drops any subsequent terminal events.
- [x] Each suppression logs a WARNING line tagged `agui_terminal_dedup` with `first=`, `dropped=`, and `thread_id=` fields.
- [x] 6/6 unit tests pass covering ERROR→FINISHED, FINISHED→ERROR, lone-ERROR, lone-FINISHED, non-terminal-pass-through, thread-id-in-log.
- [x] Existing `test_agui.py` (7 cases) still passes — no regression in mount-helper / app-name / user-id contracts.
- [x] Module docstring + `stream_agui_events` docstring reference the G41 design doc.
- [ ] **Template sync pending**: next `aitana-template-publish` run propagates to `sunholo-data/ai-protocol-platform`.
- [ ] **Upstream PR** to `ag_ui_adk` filing the same fix at the library boundary — parallel track, not blocking.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — G41 row
- [template-auth-token-refresh.md](./template-auth-token-refresh.md) — sibling "universal frontend trap" doc (G40) — both are template-shipped code where the failure is invisible until a long demo surfaces it
- [adk/agui.py](../../../backend/adk/agui.py) — the file under test
- [ag_ui_adk SDK source](../../../backend/.venv/lib/python3.12/site-packages/ag_ui_adk/adk_agent.py) lines 1510-1530 (the bug) and 2370-2380 (the queue-based error emission)
