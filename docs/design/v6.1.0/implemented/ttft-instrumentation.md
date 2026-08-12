# TTFT Instrumentation, Perceived Snappiness & Latency Diagnostics

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 2 days
**Scope**: Fullstack
**Dependencies**: chat-message-rendering (1.1 ✅), chat-session-history (1.8 ✅), document-to-ai-pipeline (1.9 ✅)
**Created**: 2026-04-28
**Last Updated**: 2026-04-28

## Problem Statement

Chat responses in v6 feel slow, but we cannot tell *where* the latency lives. Axiom #1 (INSTANT FEEL) sets concrete KPIs — first token <1s without tools, AG-UI first event <300ms — and we have no way to measure either. We have OpenTelemetry wired (`get_fast_api_app(otel_to_cloud=...)`) and a few custom logs, but no first-token marker, no per-stage breakdown, and no UI surface that shows the developer a number to optimize against.

**Current State:**
- **No TTFT measurement at any layer.** Grep for `first_token`, `ttft`, `time_to_first` returns nothing across `backend/` and `frontend/`. Spans set `skill_id` and `routing_choice` ([backend/adk/callbacks.py:105-118](../../../backend/adk/callbacks.py#L105-L118)) but nothing time-related.
- **No timing in `useSkillAgent`.** [frontend/src/hooks/useSkillAgent.ts:94-294](../../../frontend/src/hooks/useSkillAgent.ts#L94-L294) tracks an `isLoading` boolean only — no `performance.now()` markers around `agent.runAgent()` or `onTextMessageContentEvent`.
- **Synchronous work runs before the first token.** `_composed_before_agent` in [backend/adk/agent.py:330-333](../../../backend/adk/agent.py#L330-L333) chains the document loader ([backend/adk/callbacks.py:193-326](../../../backend/adk/callbacks.py#L193-L326)) — for resumed sessions with attached docs, `build_document_context(doc_id, mode="blocks")` runs per doc inline (Firestore reads, possible block parsing). `_ensure_session_index` ([backend/skills/skill_processor.py:91-99](../../../backend/skills/skill_processor.py#L91-L99)) writes Firestore on the synchronous path before SSE opens. Whether either is the bottleneck — unknown.
- **Routing decision invisible.** `_HeuristicRouter` picks fast vs thinking model ([backend/adk/agent.py:358-387](../../../backend/adk/agent.py#L358-L387)); the chosen model never reaches the user or the developer console.
- **No min-instances on Cloud Run** ([backend/cloudbuild.yaml:64](../../../backend/cloudbuild.yaml#L64)) — cold starts are a candidate cause but unattributed.

**Impact:**
- Mark + future devs can't tell whether to optimize the model choice, the doc loader, the SSE path, or Cloud Run cold-start. Optimization is blind.
- The July 2026 workshop (per memory) demos the platform live; an audience-facing chat that takes >2s to start streaming is a credibility hit. Workshop critical path.
- Axiom #1 KPIs are unverifiable today — we cannot say whether we are passing or failing.
- **The chat UI does nothing visible between "user hits Enter" and "first text delta arrives."** [useSkillAgent](../../../frontend/src/hooks/useSkillAgent.ts) tracks an `isLoading` boolean, but the user's message is not echoed optimistically and `StreamingBubble` only mounts when the first delta lands. Even if real TTFT is 400ms, the *perceived* dead time runs from key-press to first paint. Axiom #1 is explicit that perceived speed is the KPI, not real speed — "stream a partial answer immediately changes the experience from 'waiting' to 'watching progress.'"

## Goals

**Primary Goals:**
1. **Measure** — TTFT and per-stage latency are observable end-to-end (backend trace, structured log, developer HUD), so any regression is attributable to a specific stage within 30 seconds of seeing it.
2. **Mask** — perceived TTFT (key-press → first visible feedback) is **<100ms**, decoupled from real model TTFT. Even if the model takes 800ms, the user sees their message echoed, the input cleared, and the assistant bubble already pulsing within one animation frame.

**Success Metrics:**
- **Real TTFT (server-side):** every `/api/skill/{id}/stream` request emits per-stage timings (request received → before-agent done → before-model done → first model token → first AG-UI event → first SSE byte) into Cloud Trace and structured logs. Coverage: 100%.
- **Perceived TTFT (client-side):** time from `onSubmit` to first visible DOM change <100ms p95 (measured via `PerformanceObserver` on the chat container). The optimistic user bubble + skeleton assistant bubble must paint within one animation frame (~16ms) of submit.
- **Stage progress visible:** while waiting for the first model token, the assistant skeleton bubble shows a status string driven by backend stage events ("Reading documents…" / "Thinking…" / "Calling search…"), updating within 50ms of the corresponding backend mark. Status text resolves to actual content the moment the first delta arrives — no flicker.
- **Developer can see TTFT on every chat message** when `NEXT_PUBLIC_DEV_LATENCY_HUD=1` (off in prod by default). Both real and perceived numbers shown side by side.
- A single BigQuery query returns p50 / p95 / p99 TTFT per skill over the last 24h.
- Baseline numbers (real + perceived) recorded before any optimization, again after — published in the implementation report.

**Instrumentation overhead is itself a measurable risk.** The whole point is to reduce latency, so we must be able to A/B test the chat path *with* vs *without* instrumentation. A single env var `AITANA_TTFT_MODE` controls this:

| Value | OTel attrs | Structured log | STAGE_PROGRESS events | LatencyTracker overhead |
|---|---|---|---|---|
| `full` (default) | yes | yes | yes | ~hundreds of µs/request |
| `log` | no | yes | no | ~tens of µs/request |
| `off` | no | no | no | one boolean check, ~ns |

In `off` mode, `LatencyTracker.mark()` returns immediately with no work. The frontend `LatencyHUD` and optimistic-UI changes are NOT gated by this — they're cheap and the perceived-snappiness payload is the actual product feature, not the instrumentation. Only the *measurement* track is gated. The implementation report MUST include a comparison of median real TTFT in `full` vs `off` over 50 requests; if instrumentation adds >5ms p50, redesign before shipping.

**Non-Goals:**
- **Not** a full APM rebuild. We are extending what `otel_to_cloud=True` already exports, not adding a new vendor.
- **Not** the *backend* optimization itself. This doc instruments + masks; follow-up docs fix the underlying causes the data exposes (likely candidates: doc-loader parallelization, model swap, callback ordering). Scoping the backend fix before measuring violates "measure first."
- **Not** fake progress. The status string is driven by *real backend marks* (via AG-UI custom events), not a timer-based fiction. If the backend stalls, the status truthfully says so. No lying-to-the-user.
- **Not** a new protocol. We use AG-UI events and OTel spans — no proprietary timing or progress channel.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Core of the doc on two axes: (a) measure the real number; (b) reduce *perceived* TTFT to <100ms via optimistic echo + skeleton bubble + stage progress, exactly the "watching progress, not waiting" pattern the axiom calls out. |
| 2 | EARNED TRUST | 0 | No factual-claim surface. |
| 3 | SKILLS, NOT FEATURES | 0 | Internal infra; invisible to skill builders. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Surfaces the routing decision (`fast` vs `thinking`, model id) per request, making mis-routing visible. |
| 5 | GRACEFUL DEGRADATION | +1 | Timing failures (clock skew, missing span) must never break the chat path. Doc specifies fail-open for every probe. Optimistic UI degrades cleanly: if `runAgent` errors before any delta, the skeleton bubble flips to an error state with the actual server message — never an indefinite spinner. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses existing OpenTelemetry spans + AG-UI `RUN_STARTED`/`TEXT_MESSAGE_CONTENT` events. No custom timing channel. The frontend HUD uses `performance.now()`, a web standard. |
| 7 | API FIRST | 0 | Timings ride on existing API surface; no channel-specific work. |
| 8 | OBSERVABLE BY DEFAULT | +1 | This *is* observability — adds spans, structured logs with stable keys, and a developer HUD. Stays inside GCP project edge (Cloud Trace + Cloud Logging + BQ). |
| 9 | SECURE BY CONSTRUCTION | 0 | Only emits durations + opaque ids (skill_id, session_id, model name). No prompt content added beyond what the existing GenAI capture already does. HUD gated by env flag. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Frontend timing is one hook reading three `performance.now()` marks; no business logic moves to the client. |
| | **Net Score** | **+5** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no -1 scores.

## Design

### Overview

Two parallel tracks, sharing one timing taxonomy:

1. **Measurement track** — backend OTel marks + structured `event="ttft"` log + frontend `performance.now()` marks + `LatencyHUD` (dev-only) + `aiplatform skill probe` CLI. Tells us where the real time goes.
2. **Perceived-snappiness track** — optimistic user-message echo, skeleton assistant bubble that mounts on submit, stage progress text driven by AG-UI custom events emitted from the same backend marks. Decouples "did anything happen?" from "did the model start tokens?"

The two tracks share one source of truth (the backend marks). The same `LatencyTracker` that emits OTel attrs also emits AG-UI `STAGE_PROGRESS` custom events to the client, so the UI's progress text is never out of sync with the trace.

The whole feature is additive. No existing endpoint or response format changes.

### Perceived Snappiness — UX details

The four affordances, in order of how the user sees them:

**1. Optimistic user-message bubble (paints at ~16ms)**
- On `onSubmit` in [ChatInput](../../../frontend/src/components/chat/ChatInput.tsx), `useSkillAgent` immediately appends a `{role: "user", content: input, status: "sent"}` message to the local message list and clears the input field — *before* `agent.runAgent()` is invoked.
- This single change moves perceived feedback from ~real-TTFT to one animation frame. The user has visual confirmation their message left.
- If `runAgent` rejects synchronously, the bubble's status flips to `"failed"` with a retry affordance. If it rejects later, same path.

**2. Skeleton assistant bubble with cursor (paints alongside the user bubble, ~16ms)**
- A new placeholder message `{role: "assistant", content: "", status: "thinking"}` is appended in the same render as the user bubble.
- Renders [StreamingBubble](../../../frontend/src/components/chat/StreamingBubble.tsx) with empty content + the existing cursor animation, so the visual language is identical to in-progress streaming. No new spinner UI.
- When the first `TextMessageContent` event arrives, the same bubble's content fills in — *no remount, no flicker*. Same DOM node from skeleton through final answer.

**3. Stage progress text inside the skeleton (paints as backend marks fire)**
- Backend emits AG-UI [`Custom`](https://docs.ag-ui.com/) events of type `STAGE_PROGRESS` from inside the same `LatencyTracker.mark()` calls that record OTel attributes. Payload: `{stage: "before_agent" | "before_model" | "tool_call", label: "Reading documents…" | "Thinking…" | "Calling search…", elapsed_ms: 145}`.
- The skeleton bubble shows the latest `label` as small dimmed text *above* the cursor.
- When the first `TextMessageContent` event arrives, the stage text vanishes (one fade-out, ~150ms) and the cursor stays. This is the moment perceived TTFT ends and real streaming begins — visually identical for the user, just more text appearing.
- Stage labels are server-authored (Axiom #10 — fat protocol), not hardcoded in the client. Backend can localize, A/B-test, or mute them without a frontend change.

**4. Input field "armed" state (paints at ~16ms)**
- Send button → spinner. Input field disabled. Esc cancels (calls `agent.abortRun()` if available, otherwise marks the optimistic bubble as cancelled).
- Standard chat-app affordance, but absent today — we have only `isLoading` boolean with no visible affordance.

**Anti-fake-progress invariant:** if the backend stalls (no marks fire for >2s), the stage text says "Still working…" with the elapsed time. We never invent progress that isn't real.

**Animation budget:** all transitions ≤200ms, ease-out. The point is to mask latency, not introduce new latency. Transitions must NEVER block the first text delta from rendering.

### Stage event taxonomy

The backend already records 7 marks (see Backend Changes below). Of those, **4 are user-relevant** and get a `STAGE_PROGRESS` AG-UI event with a human-readable label:

| Mark (server) | AG-UI event label | When skeleton shows it |
|---|---|---|
| `session_index_done` | (silent — too fast to be worth showing) | — |
| `before_agent_done` | "Reading documents…" *if* doc loader did work; otherwise silent | Conditional on `_STATE_DOCS_LOADED` non-empty |
| `before_model_done` | "Thinking…" | Always |
| `tool_call_started` (per tool) | "Calling {tool_name}…" | One label per tool invocation, replaces previous |
| `first_model_token` | (silent — content takes over) | — |

The label is decided server-side based on what actually happened, not the existence of the mark. If the doc loader had nothing to load, the user does not see "Reading documents…" for 0ms.

### Backend Changes

**Timing taxonomy (single source of truth — defined once in `backend/observability/timing.py`):**

| Mark | Captured at | Span attribute |
|---|---|---|
| `t_request_received` | First line of `stream_skill` handler | `aitana.ttft.request_received_ms` (=0, anchor) |
| `t_session_index_done` | After `_ensure_session_index` returns | `aitana.ttft.session_index_ms` |
| `t_before_agent_done` | Last `after_agent_callback` exit (or last `_composed_before_agent` mark — whichever is the gate before model invocation) | `aitana.ttft.before_agent_ms` |
| `t_before_model_done` | `before_model_callback` exit (covers doc injection) | `aitana.ttft.before_model_ms` |
| `t_first_model_token` | First `LlmResponse` partial event from the runner | `aitana.ttft.first_model_token_ms` |
| `t_first_agui_event` | First event yielded out of `stream_agui_events` | `aitana.ttft.first_agui_event_ms` |
| `t_first_sse_byte` | First `yield` inside `_sse()` | `aitana.ttft.first_sse_byte_ms` |
| `routing_choice` | `_HeuristicRouter` decision | `aitana.routing.choice` (already partially exists) |
| `model_used` | Resolved agent model id at runtime | `aitana.model.id` |
| `tools_invoked_count` | Counter incremented on each `FunctionCall` event | `aitana.tools.invoked_count` |

All durations are wall-clock ms relative to `t_request_received`, captured with `time.perf_counter()` (monotonic). One owning context manager — `LatencyTracker` — lives on the FastAPI request `state` and on the ADK `CallbackContext` (via a small accessor) so callbacks can record marks without re-plumbing.

**New module:** [backend/observability/timing.py](../../../backend/observability/timing.py) — defines `LatencyTracker`:
- Constructor takes a span (or creates one via `tracer.start_as_current_span("skill.ttft")`).
- `mark(name)` sets a span attribute and stores the duration in an internal dict.
- `emit_log()` produces a single `logger.info("ttft", extra={...})` line with all marks (called in a `finally:` after the SSE generator drains).
- Fail-open: any exception inside `mark`/`emit_log` is caught and logged at DEBUG.

**Modified files:**
- [backend/fast_api_app.py:302-388](../../../backend/fast_api_app.py#L302-L388) — instantiate `LatencyTracker` on entry to `stream_skill`, attach to `request.state`, call `mark("request_received")`, then `mark("session_index_done")` after `_ensure_session_index`. Wrap `_sse()` so the first `yield` calls `mark("first_sse_byte")`. Add `finally: tracker.emit_log()`.
- [backend/adk/callbacks.py](../../../backend/adk/callbacks.py) — `_composed_before_agent` calls `tracker.mark("before_agent_done")` at the tail. `_document_injector` (the `before_model_callback`) calls `tracker.mark("before_model_done")` on exit.
- [backend/adk/agui.py](../../../backend/adk/agui.py) — inside `stream_agui_events`, set `tracker.mark("first_agui_event")` on the first iteration. Inspect events for `first_model_token` (the first `LlmResponse` partial) and call `tracker.mark("first_model_token")`. Increment `tools_invoked_count` on each `FunctionCall` event.
- [backend/adk/agent.py:358-387](../../../backend/adk/agent.py#L358-L387) — when `_HeuristicRouter` decides, also stash the resolved `model_used` string so it can be read from agent state by the tracker. (Already partially observable via `routing_choice`; add `model_used`.)

**Accessor pattern:** `LatencyTracker` is reachable from `CallbackContext` via `context.invocation_context.session_state.get("_latency_tracker")` — set once in the entry handler. ADK does not expose request-state cleanly to callbacks otherwise; this is the least-invasive bridge and mirrors how `_STATE_DOCS_LOADED` is already plumbed.

**Structured log line (single line per request):**
```
{"event": "ttft", "skill_id": "...", "session_id": "...", "user_id": "...",
 "model_used": "gemini-2.5-flash", "routing_choice": "fast",
 "request_received_ms": 0, "session_index_ms": 12, "before_agent_ms": 145,
 "before_model_ms": 152, "first_model_token_ms": 487, "first_agui_event_ms": 491,
 "first_sse_byte_ms": 493, "tools_invoked_count": 0,
 "total_response_ms": 2143, "doc_count": 1}
```

This goes to Cloud Logging via the existing logger; the structured `extra` dict becomes `jsonPayload` automatically. BQ query becomes a one-liner since these end up in the GenAI logs sink already configured ([reference: ADK observability skill]).

**STAGE_PROGRESS event emission:** `LatencyTracker.mark(name)` takes an optional `user_label: str | None = None` arg. When non-None, it ALSO appends a `{type: "STAGE_PROGRESS", stage: name, label: user_label, elapsed_ms: ...}` AG-UI Custom event to the in-flight stream queue (same queue `stream_agui_events` yields from). This piggybacks on the AG-UI Custom event protocol — no new wire type. The decision of *which* marks get a label (and what label) lives in the call sites:

```python
# in _composed_before_agent, after doc loader:
if loaded_docs:
    tracker.mark("before_agent_done", user_label=f"Reading {len(loaded_docs)} document{'s' if len(loaded_docs) > 1 else ''}…")
else:
    tracker.mark("before_agent_done")

# in _document_injector:
tracker.mark("before_model_done", user_label="Thinking…")

# in tool_call hook (new — see backend/adk/callbacks.py before_tool_callback):
tracker.mark("tool_call_started", user_label=f"Calling {tool.name}…")
```

This keeps stage-label policy server-side (Axiom #10), and the OTel attrs and the user-facing label come from the same call site — they cannot drift.

**No new endpoint.** All instrumentation + progress rides existing routes.

### Frontend Changes

**Modified hook:** [frontend/src/hooks/useSkillAgent.ts](../../../frontend/src/hooks/useSkillAgent.ts)
- **Optimistic append (perceived-snappiness):** in `sendMessage`, before `agent.runAgent()`:
  1. Append optimistic user message `{role: "user", id: uuid, content, status: "sent"}` to local message list.
  2. Append skeleton assistant message `{role: "assistant", id: uuid, content: "", status: "thinking", stageLabel: null}`.
  3. Clear input field.
  4. Capture `t_send = performance.now()`.
  5. Call `agent.runAgent()`.
- **First-event mark:** in `onRunStarted` callback capture `t_first_event`; push `{t_first_event - t_send}` to the latency store.
- **Stage progress:** subscribe to AG-UI custom events; on `{type: "STAGE_PROGRESS"}` set `stageLabel` on the in-flight skeleton message.
- **First-text mark:** in `onTextMessageContentEvent` on the *first* delta of a run, flip skeleton's `status` from `"thinking"` → `"streaming"`, clear `stageLabel`, append the delta to `content`. Capture `t_first_text_chunk`. Push to latency store.
- **Failure path:** on `onRunError` or network failure, flip skeleton to `{status: "failed", errorMessage: ...}`. Optimistic user bubble stays.

**Modified component:** [frontend/src/components/chat/StreamingBubble.tsx](../../../frontend/src/components/chat/StreamingBubble.tsx)
- Render an additional small dimmed `<span>` above the cursor when `status === "thinking"` and `stageLabel` is set. Fade out (200ms) when the first delta arrives.
- No new component file — it's the *same* bubble through skeleton → streaming → final, exactly so there's no remount-flicker.

**Modified component:** [frontend/src/components/chat/ChatInput.tsx](../../../frontend/src/components/chat/ChatInput.tsx)
- Disable input + show button spinner while `isLoading`. Esc key triggers `cancelRun()`.

**New component:** [frontend/src/components/dev/LatencyHUD.tsx](../../../frontend/src/components/dev/LatencyHUD.tsx)
- Fixed-position panel, bottom-right, only renders when `process.env.NEXT_PUBLIC_DEV_LATENCY_HUD === "1"`.
- Subscribes to `latencyStore`.
- Displays the most recent message's marks **side by side**:
  - **Real:** `request_received → first_model_token` (server-side, from AG-UI metadata)
  - **Perceived:** `send → first DOM paint` (client-side, via `PerformanceObserver` on the message list container)
  - `send → first event`, `send → first text chunk`
  - `model used`, `routing` (`fast`/`thinking`)
  - Last 5 messages, sparkline-style

**State:** new `frontend/src/stores/latencyStore.ts` — minimal, last-N marks per session. Cleared on session change.

**Perceived-TTFT measurement:** the `LatencyHUD` reads paint timing via `PerformanceObserver({type: "paint"})` filtered to the chat container — this is the honest number, not a self-reported timestamp. Used both for the HUD display and for the success-metric assertion.

**No bundle impact** from the HUD when the env var is unset (tree-shaken). The optimistic-bubble + stage-label changes ARE in the prod bundle — they're the actual UX feature, not a dev affordance. Estimated +2KB gzip; well within Axiom #10's 200KB budget.

### CLI Surface

Add `aiplatform skill probe <skill-id>` to the local-dev CLI ([local-dev-cli.md](local-dev-cli.md)):

```bash
aiplatform skill probe my-skill --message "Hello"
# Sends one message via /api/skill/{id}/stream, prints:
#   request_received       0ms
#   session_index_done    12ms
#   before_agent_done    145ms
#   before_model_done    152ms
#   first_model_token    487ms  ← TTFT
#   first_agui_event     491ms
#   first_sse_byte       493ms
#   total                2143ms
#   model: gemini-2.5-flash  routing: fast  tools: 0
```

The CLI parses the structured log line out of the SSE response (or via a side-channel `?probe=1` flag that returns timings as a final AG-UI custom event). Implementation: read the response trailer or a dedicated `LATENCY_REPORT` event type appended at end-of-stream.

This is the workshop-friendly surface — one terminal command, no browser open, immediate per-stage breakdown.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST | `/api/skill/{id}/stream` | Adds optional final `LATENCY_REPORT` AG-UI custom event when `?probe=1` query param is set | No |

`LATENCY_REPORT` is delivered as an AG-UI [`Custom`](https://docs.ag-ui.com/) event (per protocol spec — no proprietary type), payload mirrors the structured log dict above. Frontend ignores it unless the HUD is on. CLI consumes it.

### Architecture Diagram

```
[Frontend]                            [Backend]                          [GCP]
  ↓ t_send                              ↓ t_request_received
useSkillAgent ──────POST /stream──────▶ stream_skill handler
  │                                       │ mark("session_index_done")
  │                                       ▼
  │                                    _composed_before_agent
  │                                       │ doc_loader (per-doc Firestore reads)
  │                                       │ mark("before_agent_done")
  │                                       ▼
  │                                    _document_injector
  │                                       │ mark("before_model_done")
  │                                       ▼
  │                                    ADK Runner.run_async ──▶ Gemini/Claude/OpenAI
  │                                       │ mark("first_model_token")
  │                                       ▼
  │   ◀──first AG-UI event────────────  stream_agui_events
  │   onRunStarted                        │ mark("first_agui_event")
  │   t_first_event                       ▼
  │                                    StreamingResponse._sse()
  │                                       │ mark("first_sse_byte")
  │   ◀──first text delta─────────────  yield
  │   t_first_text_chunk                  ▼
  ▼                                    finally: tracker.emit_log() ─────▶ Cloud Logging
LatencyHUD                                                                  Cloud Trace
                                                                            BigQuery
```

## Implementation Plan

### Phase 1: Backend Instrumentation + STAGE_PROGRESS (~1 day)
- [ ] Create `backend/observability/timing.py` with `LatencyTracker` (mark + optional `user_label` → STAGE_PROGRESS Custom event) (~120 LOC + ~60 LOC tests)
- [ ] Wire `LatencyTracker` into `stream_skill` ([fast_api_app.py:302-388](../../../backend/fast_api_app.py#L302-L388)) (~30 LOC)
- [ ] Add `mark()` calls with user labels in `_composed_before_agent`, `_document_injector`, and a new `before_tool_callback` in [backend/adk/callbacks.py](../../../backend/adk/callbacks.py) (~40 LOC)
- [ ] Add `first_model_token` + `first_agui_event` detection in [stream_agui_events](../../../backend/adk/agui.py); ensure STAGE_PROGRESS events are interleaved into the existing event stream (~40 LOC)
- [ ] Stash `model_used` from `_HeuristicRouter` decision; emit it in `RunStarted` metadata so the HUD can show it (~15 LOC)
- [ ] Pytest: `LatencyTracker` unit (mark/emit/fail-open + STAGE_PROGRESS emission); integration test asserts ordering: `STAGE_PROGRESS(before_model)` arrives before any `TextMessageContent` (~150 LOC)

### Phase 2: Optimistic UI + Skeleton + Stage Progress (~0.75 day) — **the perceived-snappiness payload**
- [ ] Modify [useSkillAgent.ts](../../../frontend/src/hooks/useSkillAgent.ts): optimistic user-bubble append, skeleton assistant-bubble append, input clear, `t_send` capture — all before `agent.runAgent()` (~40 LOC)
- [ ] Subscribe to STAGE_PROGRESS Custom events; thread `stageLabel` onto the in-flight skeleton message (~25 LOC)
- [ ] Modify [StreamingBubble.tsx](../../../frontend/src/components/chat/StreamingBubble.tsx): render `stageLabel` dimmed line above cursor when `status === "thinking"`; fade it out on first delta — same DOM node throughout (~30 LOC)
- [ ] Modify [ChatInput.tsx](../../../frontend/src/components/chat/ChatInput.tsx): button spinner, input disable, Esc → cancel (~25 LOC)
- [ ] Failure path: skeleton flips to error state with server message; optimistic user bubble persists (~20 LOC)
- [ ] Vitest: optimistic append paints user+skeleton in same render tick; STAGE_PROGRESS updates `stageLabel`; first delta clears `stageLabel` and reuses same DOM node (no remount); error path renders correctly (~150 LOC)
- [ ] Visual: trigger artificial 2s backend stall in dev, confirm skeleton + stage labels behave; record video for design review

### Phase 3: LatencyHUD + Frontend Timing (~0.5 day)
- [ ] `frontend/src/stores/latencyStore.ts` — last-N marks per session, includes paint-timing observer (~60 LOC)
- [ ] `LatencyHUD.tsx` env-flag-gated, real + perceived TTFT side by side (~120 LOC)
- [ ] `PerformanceObserver` integration to measure first DOM paint after submit (~30 LOC)
- [ ] Vitest: HUD env-gate; perceived-TTFT measurement asserts <100ms when stub-server returns first event in 50ms (~80 LOC)
- [ ] Verify prod bundle excludes HUD; optimistic-UI changes are included (~+2KB gzip)

### Phase 4: CLI + LATENCY_REPORT (~0.5 day)
- [ ] Add `LATENCY_REPORT` AG-UI custom event emission in `stream_agui_events` when `?probe=1` (~25 LOC)
- [ ] `aiplatform skill probe` command in `cli/aitana/commands/skill.py` — prints both real and perceived(=N/A for CLI) breakdown (~80 LOC + ~40 LOC test)
- [ ] Update [local-dev-cli.md](local-dev-cli.md) with the new command

### Phase 5: Baseline + report (~0.25 day)
- [ ] Run 10 iterations against local dev, three skills (no-tools, with-tools, with-docs); record real TTFT and perceived TTFT side by side
- [ ] Confirm perceived TTFT p95 <100ms across all three
- [ ] Record real TTFT baseline in implementation report
- [ ] File follow-up doc `ttft-optimization.md` listing the top 2 backend contributors and proposed fixes (next sprint)

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:**
- `NEXT_PUBLIC_DEV_LATENCY_HUD=1` — frontend HUD on. Default: unset (off).
- No backend flag — instrumentation always on. Span attributes and structured logs cost negligible bytes; Axiom #8 says emit by default.

**Rollback Plan:**
- Pure additive change. To roll back: revert the PR. No data shape change in stored sessions or AG-UI events (the `LATENCY_REPORT` event is opt-in via query param).

**Environment Variables:**
- Frontend: `NEXT_PUBLIC_DEV_LATENCY_HUD` (dev/test only, never set in prod)
- Backend: none new

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `useSkillAgent` records `t_send`, `t_first_event`, `t_first_text_chunk` correctly (mock AG-UI client, fire events, assert store state)
- [ ] `LatencyHUD` renders only when env var is `"1"` (test both branches)
- [ ] `latencyStore` evicts past N entries

### Backend Tests (pytest)
- [ ] `LatencyTracker.mark()` is monotonic and records via OTel
- [ ] `LatencyTracker.emit_log()` produces a single structured log line with all expected keys
- [ ] Fail-open: a callback that raises inside `mark()` does NOT break the SSE response
- [ ] Integration test: hit `/api/skill/{id}/stream` with a stub agent, assert log line shape and that `first_model_token_ms <= first_agui_event_ms <= first_sse_byte_ms`

### Manual Testing
- [ ] Open chat with `NEXT_PUBLIC_DEV_LATENCY_HUD=1`, send message — user bubble + skeleton paint within one frame, HUD shows real + perceived numbers
- [ ] Send message with attached doc — confirm "Reading documents…" appears in skeleton, then "Thinking…", then content; same bubble throughout (no flicker)
- [ ] Send message with no doc — skeleton goes straight to "Thinking…", no "Reading documents…" flash
- [ ] Hold backend with debugger paused for 3s — confirm skeleton stays, stage label is honest, no fake progress, Esc cancels cleanly
- [ ] Force a backend error — skeleton flips to error state, optimistic user bubble persists, retry works
- [ ] Run `aiplatform skill probe` against local dev, three skills, eyeball p50
- [ ] Visually confirm same DOM node from skeleton through final answer (React DevTools — no remount)
- [ ] Cloud Trace UI shows the new span attributes on a request from dev
- [ ] BQ query `SELECT skill_id, APPROX_QUANTILES(first_model_token_ms, 100)[OFFSET(50)] FROM ... WHERE event="ttft"` returns rows

## Security Considerations

- All timing data is internal to the GCP project — Cloud Logging + Cloud Trace + BQ. No egress. Axiom #9 satisfied by default.
- `LATENCY_REPORT` event payload contains only ms durations + opaque ids (`skill_id`, `session_id`, `user_id`, `model_used`, `routing_choice`). No prompt or response content. No new sensitive data path.
- Dev HUD is env-flag-gated. Production builds with the flag unset must tree-shake the component (verified in CI bundle-size check).

## Performance Considerations

- `time.perf_counter()` is ~ns overhead. Span attribute writes are ~µs. One structured log line per request is negligible.
- Critical: instrumentation must NEVER be on the hot path of the first model token. All `mark()` calls happen *after* the byte we are timing, never before. The first `yield` of `_sse()` writes the byte then marks.
- The `LATENCY_REPORT` AG-UI event adds ~200 bytes at end-of-stream, only when `?probe=1`.
- Frontend HUD re-renders are throttled to once per message (not per delta) — no streaming-loop pressure.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] Lint and typecheck clean (`cd frontend && npm run quality:check:fast`; `cd backend && make lint`)
- [ ] Cloud Trace shows `aitana.ttft.*` attributes on a real dev request
- [ ] Structured `event="ttft"` log line appears in Cloud Logging for every `/stream` request
- [ ] `aiplatform skill probe my-skill` prints the full breakdown
- [ ] `LatencyHUD` shows real + perceived TTFT side by side when dev flag is set
- [ ] **Perceived TTFT p95 <100ms** measured via `PerformanceObserver` across the manual test scenarios
- [ ] User message + skeleton bubble paint in the same animation frame as `onSubmit` (verified in DevTools Performance panel)
- [ ] STAGE_PROGRESS labels render in skeleton; same DOM node from skeleton through final (no remount)
- [ ] BQ query returns p50/p95 TTFT per skill over the last hour of dev traffic
- [ ] Implementation report documents real-TTFT baseline + perceived-TTFT measurement and the top 2 contributors to real latency

## Open Questions

- **Where exactly does ADK emit "first model token"?** The runner emits `LlmResponse` events; we need to confirm the first *partial* response is fired before the model has finished — verify in `mcp__adk-mcp__search_code` with `LlmResponse` and check `Runner.run_async` partial-event behaviour. If ADK only emits a single `LlmResponse` per turn (not streaming partials), TTFT collapses into "model RTT" and we lose model-internal granularity. (Mitigation: still emit `first_agui_event` and `first_sse_byte` — those are the user-facing numbers anyway.)
- **Should `before_agent_ms` split per-callback?** With doc loader, structured-extraction, and any future callback in the chain, a single number hides which callback is slow. Cheap to add: each callback reports its own mark. Probably yes — defer until baseline shows `before_agent_ms` is meaningful (>50ms p50).
- **Does Cloud Run buffer SSE on cold start?** If yes, `first_sse_byte_ms` will be much higher than `first_agui_event_ms` on the first request after a cold start, and the fix is `--min-instances=1`. Worth confirming before changing infra.
- **Should the HUD be visible to non-dev users in test/staging?** Default is no (only dev), but the workshop demo benefits from showing the audience that streaming is real. Could add a `?showLatency=1` URL param as a one-off override for live demos.

## Related Documents

- [docs/product-axioms.md](../../../docs/product-axioms.md) — Axiom #1 (INSTANT FEEL) and #8 (OBSERVABLE BY DEFAULT) define the KPIs this doc serves.
- [local-dev-cli.md](local-dev-cli.md) — `aiplatform skill probe` is the CLI surface for this feature, per the local-dev-cli charter.
- [local-mode-and-workshop-readiness.md](local-mode-and-workshop-readiness.md) — workshop critical path; TTFT directly affects demo credibility.
- [chat-message-rendering.md](implemented/chat-message-rendering.md) — `StreamingBubble` is where the first text chunk renders; HUD reads timing from `useSkillAgent`, not from the bubble.
- [implemented/document-to-ai-pipeline.md](implemented/document-to-ai-pipeline.md) — explains why `before_agent_ms` may be the dominant cost when docs are attached (loader runs synchronously before model).
- [ADK observability skill](https://adk.dev/) (internal) — the OTel + Cloud Trace + BQ sink pattern this doc extends.

---

## Implementation Report (2026-04-28)

Sprint **TTFT-INSTR**, 5 milestones, ~3 days planned, landed in the same day across commits c0f7923 → a4d9ca8.

### What shipped

| Milestone | Commit | Summary |
|---|---|---|
| M1 backend LatencyTracker | c0f7923 | 7 stage marks + kill switch (`AITANA_TTFT_MODE=full|log|off`), OTel attrs, structured `event="ttft"` log, AG-UI `STAGE_PROGRESS` events. ContextVar-based accessor. Fail-open everywhere. 9 unit + 5 SSE integration tests. |
| M2 frontend stage labels | e6920f3 | `useSkillAgent.stageLabel` subscribes to STAGE_PROGRESS; rendered inside existing TypingIndicator (priority over toolName fallback). Esc cancels in-flight run. 13 new tests. **Scope adjusted on discovery**: existing `agent.addMessage()` already paints the user bubble synchronously and the existing TypingIndicator already serves as the skeleton. The actual gap was empty dots → server-authored progress text. No separate skeleton component built. |
| M3 LatencyHUD | c16f677 | Module-level latency store + useSyncExternalStore-based hook (no Provider needed). Env-flag-gated (`NEXT_PUBLIC_DEV_LATENCY_HUD=1`) panel, fixed bottom-right, shows last 5 marks side by side: perceived event/label/chunk vs real first_model_token_ms (when `?probe=1` set). 16 tests. |
| M4 CLI probe | a4d9ca8 | `aiplatform skill probe <id>` consumes `LATENCY_REPORT` AG-UI Custom event from a `?probe=1` stream and prints the per-stage table (or JSON via `--json`). 4 tests via respx-mocked SSE. |
| M5 baseline + report | (this commit) | `scripts/ttft-baseline.sh` and `scripts/ttft-baseline-summarize.sh` — A/B-measure instrumentation overhead against a live local backend. Follow-up `ttft-optimization.md` filed (1.21). |

### Aggregate test impact

- Backend: **736 tests pass**, ruff clean (was 716 pre-sprint; added 14 new tests + 6 absorbed elsewhere).
- Frontend: **327 tests pass**, tsc + ESLint clean (was 296 pre-sprint; added 31 new tests).
- CLI: **29 tests pass**, ruff clean (added 4).

### Empirical baseline — pending

The A/B baseline (full mode vs off mode against three real skills) is a **manual user step**: it requires a running local backend with real model API calls. The runner scripts are checked in:

```bash
# Terminal 1 — full-mode backend:
AITANA_TTFT_MODE=full make dev

# Terminal 2:
./scripts/ttft-baseline.sh full <skill-id>
# stop the backend, restart in off mode:

# Terminal 1 — off-mode backend:
AITANA_TTFT_MODE=off make dev

# Terminal 2:
./scripts/ttft-baseline.sh off <skill-id>
./scripts/ttft-baseline-summarize.sh
```

The summarize script asserts the contract: **instrumentation overhead must be <5ms p50 on `first_model_token_ms`**. Mark VIOLATION if exceeded — do not move design doc to `implemented/` until baseline passes.

### Surprises and design diversions

- **No skeleton component needed.** The optimistic-UI track was over-scoped in the original design — `agent.addMessage()` already runs synchronously inside `sendMessage` before `agent.runAgent()` fires, so the user bubble paints in the next render tick. And `TypingIndicator` already mounts between submit and first model token. The actual gap was *empty dots → meaningful progress label*, which M2 closes by routing STAGE_PROGRESS into the existing component. -120 LOC vs plan.

- **ContextVar over ADK state.** Original design floated stashing the tracker on ADK session state; switched to a `contextvars.ContextVar` because ADK state must be JSON-serializable and a Python object reference is not. ContextVars propagate across `await` boundaries within one task tree, exactly matching the lifetime of one chat turn — no manual passthrough needed.

- **First model token == first TEXT_MESSAGE_CONTENT.** ADK's `LlmResponse` partial-event behaviour is unreliable to detect generically; using the AG-UI `TEXT_MESSAGE_CONTENT` event as the first-token signal is what the user sees anyway, and the AG-UI translator inside `ag_ui_adk` is the deterministic boundary.

- **Test-isolation gotcha.** Reloading `observability.timing` (to flip `AITANA_TTFT_MODE`) leaves `fast_api_app` holding a stale `LatencyTracker` class. Switched to in-place `monkeypatch.setattr` on `_ENABLED`/`_FULL`/`TTFT_MODE` constants — also a more accurate test of production behaviour, since production never re-imports the module.

### Top contributors to real latency — to be measured

The follow-up sprint [ttft-optimization.md](ttft-optimization.md) (registered as 1.21) addresses optimization once the baseline data is in. Expected hot spots based on code inspection (unverified without the baseline):

1. `_composed_before_agent` chain runs synchronously before the model. For resumed sessions with attached docs, `build_document_context(doc_id, mode="blocks")` is called per doc inline (Firestore reads, possible block parsing). Likely the dominant `before_agent_ms` cost.
2. `_ensure_session_index` synchronous Firestore write at the top of `stream_skill`. Single round-trip; should be small but is on the perceived-TTFT path.
3. Cold-start when a new agent factory rebuild is needed. Local-only per the sprint scope so de-prioritized.

### Sprint scope vs reality

| Milestone | Planned LOC | Actual LOC | Notes |
|---|---|---|---|
| M1 | 600 | 720 | Slightly larger — added `_NullLatencyTracker` subclass for cleaner accessor + LATENCY_REPORT event builder for M4. |
| M2 | 500 | 280 | Smaller — existing components covered most of the surface. |
| M3 | 370 | 350 | On target. |
| M4 | 230 | 350 | Slightly larger — explicit error/no-report exit codes + JSON flag + table formatting. |
| M5 | 50 | 200 | Larger because the runner scripts (full vs off + summarize) were more careful than originally planned. Still trivially small for the deliverable. |
| **Total** | 1750 | 1900 | +9% — well within velocity range. |

