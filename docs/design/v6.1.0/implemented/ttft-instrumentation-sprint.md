# Sprint Plan: TTFT-INSTR — TTFT Instrumentation, Perceived Snappiness & Latency Diagnostics

## Summary
Land both tracks of [ttft-instrumentation.md](ttft-instrumentation.md): backend `LatencyTracker` + structured `ttft` log + AG-UI `STAGE_PROGRESS` events for measurement; optimistic user-bubble + skeleton assistant-bubble + stage-label rendering for perceived snappiness; dev `LatencyHUD` and `aiplatform skill probe` CLI to surface the data. **Critical constraint:** the entire measurement track is gated by `AITANA_TTFT_MODE` (full | log | off) so the team can A/B-test whether instrumentation itself adds latency.

**Duration:** 3 days
**Scope:** Fullstack
**Dependencies:** chat-message-rendering ✅, chat-session-history ✅, document-to-ai-pipeline ✅
**Risk Level:** Medium — the perceived-snappiness track touches the hottest UI hook (`useSkillAgent`) and the bubble component that just shipped; risk of regressions in chat rendering. Mitigated by Phase 2 keeping the *same* `StreamingBubble` DOM node throughout (skeleton → streaming → final).
**Design Doc:** [docs/design/v6.1.0/ttft-instrumentation.md](ttft-instrumentation.md)
**Sprint ID:** `TTFT-INSTR`

## Current Status Analysis

### Recent Velocity
- **Last 14 days:** 238 commits, 67k+ insertions across 441 files. Major sprints (CHAT-HIST-FIX, CHAT-HIST-DEEP, doc onSnapshot) shipped in 1–2 days each.
- **Comparable sprint:** `sprint_CHAT-HIST-FIX.json` — 430 LOC across 3 milestones in 1 day. M1 (250 LOC backend + tests) and M2 (130 LOC frontend + tests) ran in parallel-friendly fashion.
- **Estimated capacity for this sprint:** ~1500 LOC over 3 days.

### Existing Implementation
- `get_fast_api_app(otel_to_cloud=...)` already exports OTel to Cloud Trace ([backend/fast_api_app.py:145-153](../../backend/fast_api_app.py#L145-L153)).
- `_composed_before_agent` chain wires doc loader + injector ([backend/adk/agent.py:330-333](../../backend/adk/agent.py#L330-L333)) — the right hook points for stage marks.
- `stream_agui_events` in [backend/adk/agui.py](../../backend/adk/agui.py) is where AG-UI events are yielded — STAGE_PROGRESS interleaves here.
- `_HeuristicRouter` ([backend/adk/agent.py:358-387](../../backend/adk/agent.py#L358-L387)) records `routing_choice` already.
- `useSkillAgent` ([frontend/src/hooks/useSkillAgent.ts](../../frontend/src/hooks/useSkillAgent.ts)) exposes `isLoading`, subscribes to AG-UI events — needs optimistic-append + STAGE_PROGRESS subscription.
- `StreamingBubble` renders cursor when streaming — needs to render `stageLabel` line above cursor when `status === "thinking"`.
- No `aiplatform` CLI command for skill probing exists yet (per [local-dev-cli.md](local-dev-cli.md)).

### Test surface
- Backend: 716 tests passing (per CHAT-HIST-FIX sprint notes); ruff clean.
- Frontend: 281+ tests; tsc + lint clean.
- AG-UI custom event handling already proven in test suite via `useSkillAgent.test.tsx`.

## Proposed Milestones

### Milestone 1: Backend LatencyTracker + STAGE_PROGRESS

**Scope:** backend
**Goal:** Land `LatencyTracker` with kill switch (`AITANA_TTFT_MODE`), wire all 7 marks across the request lifecycle, emit `STAGE_PROGRESS` AG-UI Custom events, emit one structured `event="ttft"` log line per request.
**Estimated:** ~350 LOC implementation + ~250 LOC tests = ~600 LOC
**Duration:** 1 day

**Tasks:**
- [ ] Create `backend/observability/timing.py` with `LatencyTracker` class. Module-level `_TTFT_MODE` constant read once from env. `LatencyTracker.mark(name, user_label=None)` is no-op when mode=off, log-only when mode=log, full when mode=full. Fail-open everywhere. (~140 LOC)
- [ ] Wire `LatencyTracker` into `stream_skill` ([backend/fast_api_app.py:302-388](../../backend/fast_api_app.py#L302-L388)) — instantiate at request entry, attach to `request.state` and to ADK session state under `_latency_tracker`, mark `request_received` and `session_index_done`, mark `first_sse_byte` on first yield in `_sse()`, `finally: tracker.emit_log()`. (~50 LOC)
- [ ] Add `mark()` calls with `user_label` in [backend/adk/callbacks.py](../../backend/adk/callbacks.py): in `_composed_before_agent` (label conditional on `_STATE_DOCS_LOADED`), in `_document_injector` (always "Thinking…"). (~30 LOC)
- [ ] Add `before_tool_callback` that marks `tool_call_started` with label `"Calling {tool.name}…"`. (~30 LOC)
- [ ] In `stream_agui_events` ([backend/adk/agui.py](../../backend/adk/agui.py)): mark `first_agui_event` on first iteration, mark `first_model_token` on first `LlmResponse` partial, increment `tools_invoked_count`. Interleave `STAGE_PROGRESS` Custom events from a per-request queue populated by `LatencyTracker`. (~80 LOC)
- [ ] Stash `model_used` from `_HeuristicRouter` decision ([backend/adk/agent.py:358-387](../../backend/adk/agent.py#L358-L387)) so tracker can read it for the log line + RunStarted metadata. (~20 LOC)
- [ ] Pytest: `test_latency_tracker.py` — mark/emit/fail-open across all three modes. `test_stream_skill_ttft.py` — integration asserts log line shape, `STAGE_PROGRESS` ordering (before any TextMessageContent), and that `mode=off` produces no log line and no STAGE_PROGRESS events. (~250 LOC)

**Files to Create/Modify:**
- `backend/observability/timing.py` (new, ~140 LOC)
- `backend/fast_api_app.py` (modify, ~50 LOC delta)
- `backend/adk/callbacks.py` (modify, ~60 LOC delta)
- `backend/adk/agui.py` (modify, ~80 LOC delta)
- `backend/adk/agent.py` (modify, ~20 LOC delta)
- `backend/tests/unit/test_latency_tracker.py` (new, ~120 LOC)
- `backend/tests/api_tests/test_stream_skill_ttft.py` (new, ~130 LOC)

**Acceptance Criteria:**
- [ ] `test_latency_tracker.py::test_marks_emit_when_mode_full` passes
- [ ] `test_latency_tracker.py::test_marks_silent_when_mode_off` passes (asserts no log, no STAGE_PROGRESS in queue)
- [ ] `test_latency_tracker.py::test_marks_log_only_when_mode_log` passes
- [ ] `test_latency_tracker.py::test_mark_failure_does_not_break_request` passes (callback raises inside `mark()` → request still completes)
- [ ] `test_stream_skill_ttft.py::test_structured_log_line_shape` passes (asserts all 7 marks + model_used + routing_choice)
- [ ] `test_stream_skill_ttft.py::test_stage_progress_arrives_before_text_content` passes
- [ ] `test_stream_skill_ttft.py::test_stage_progress_silent_when_no_docs_loaded` passes (no "Reading documents…" if loader did nothing)
- [ ] `cd backend && uv run pytest tests/ -q` passes (no other tests broken)
- [ ] `cd backend && uv run ruff check .` clean
- [ ] No new TODOs or `# noqa` introduced

**Risks:**
- **ADK partial-event behavior unknown.** ADK may not emit `LlmResponse` partials before the model completes, in which case `first_model_token` collapses into model RTT. Mitigation: still emit `first_agui_event` and `first_sse_byte`; document the finding in the implementation report. Verify via `mcp__adk-mcp__search_code` for `LlmResponse` before writing detection logic.
- **STAGE_PROGRESS interleaving.** AG-UI events come from an async iterator inside `stream_agui_events`. Need a thread-safe-ish queue (asyncio.Queue) so callback-thread marks reach the iterator-thread without blocking. Mitigation: use `asyncio.Queue` and check for STAGE_PROGRESS items between each ADK event yield.

---

### Milestone 2: Optimistic UI + Skeleton + Stage Progress Rendering

**Scope:** frontend
**Goal:** Decouple perceived TTFT from real TTFT. Optimistic user-bubble + skeleton assistant-bubble paint within one animation frame of `onSubmit`. Stage labels render in skeleton, fade out cleanly when first delta arrives. Same `StreamingBubble` DOM node throughout — no remount, no flicker.
**Estimated:** ~280 LOC implementation + ~220 LOC tests = ~500 LOC
**Duration:** 0.75 day

**Tasks:**
- [ ] Modify [useSkillAgent.ts](../../frontend/src/hooks/useSkillAgent.ts): in `sendMessage`, append optimistic user message + skeleton assistant message + clear input + capture `t_send`, all *before* `agent.runAgent()`. (~60 LOC delta)
- [ ] Subscribe to AG-UI Custom events; on `{type: "STAGE_PROGRESS"}` set `stageLabel` on the in-flight skeleton message. (~30 LOC)
- [ ] On first `TextMessageContent` delta: flip skeleton `status` `"thinking"` → `"streaming"`, clear `stageLabel`, append delta. Subsequent deltas only append. (~25 LOC)
- [ ] On `onRunError`: flip skeleton to `{status: "failed", errorMessage}`. Optimistic user bubble persists. (~20 LOC)
- [ ] Modify [StreamingBubble.tsx](../../frontend/src/components/chat/StreamingBubble.tsx): when `status === "thinking"` and `stageLabel` set, render dimmed `<span>` above the cursor. Fade out (200ms) on first delta. NEVER remount. (~40 LOC delta)
- [ ] Modify [ChatInput.tsx](../../frontend/src/components/chat/ChatInput.tsx): button spinner during in-flight, input disabled, Esc → `cancelRun()`. (~30 LOC delta)
- [ ] Vitest: `useSkillAgent.test.tsx` adds tests for optimistic append (paints in same render tick), STAGE_PROGRESS updates `stageLabel`, first delta clears `stageLabel`, error path renders. (~120 LOC)
- [ ] Vitest: `StreamingBubble.test.tsx` adds tests for dimmed stageLabel rendering and same-DOM-node persistence (using ref equality). (~60 LOC)
- [ ] Vitest: `ChatInput.test.tsx` adds tests for spinner/disabled/Esc-cancel. (~40 LOC)

**Files to Create/Modify:**
- `frontend/src/hooks/useSkillAgent.ts` (modify, ~135 LOC delta)
- `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` (modify, ~120 LOC delta)
- `frontend/src/components/chat/StreamingBubble.tsx` (modify, ~40 LOC delta)
- `frontend/src/components/chat/__tests__/StreamingBubble.test.tsx` (modify or new, ~60 LOC delta)
- `frontend/src/components/chat/ChatInput.tsx` (modify, ~30 LOC delta)
- `frontend/src/components/chat/__tests__/ChatInput.test.tsx` (modify or new, ~40 LOC delta)
- `frontend/src/types/chat.ts` (modify, ~15 LOC delta) — extend `ChatMessage` with `status` + `stageLabel`

**Acceptance Criteria:**
- [ ] `useSkillAgent.test.tsx::test_optimistic_user_bubble_appears_before_runAgent` passes
- [ ] `useSkillAgent.test.tsx::test_skeleton_assistant_bubble_appears_with_user_bubble` passes
- [ ] `useSkillAgent.test.tsx::test_stage_progress_updates_skeleton_label` passes
- [ ] `useSkillAgent.test.tsx::test_first_delta_clears_stage_label_and_appends_content` passes
- [ ] `useSkillAgent.test.tsx::test_run_error_flips_skeleton_to_failed_state_keeps_user_bubble` passes
- [ ] `StreamingBubble.test.tsx::test_renders_stage_label_when_thinking` passes
- [ ] `StreamingBubble.test.tsx::test_same_dom_node_through_skeleton_to_streaming` passes (ref equality)
- [ ] `ChatInput.test.tsx::test_esc_cancels_in_flight_run` passes
- [ ] `cd frontend && npx vitest run` all green
- [ ] `cd frontend && npx tsc --noEmit -p tsconfig.json` clean
- [ ] `cd frontend && npm run lint` clean
- [ ] **Manual:** trigger artificial 2s backend stall (sleep in `_composed_before_agent`); confirm skeleton + stage label behavior; confirm Esc cancels cleanly.

**Risks:**
- **Same-DOM-node invariant is fragile.** If React re-keys the message between skeleton and streaming states, the cursor flickers. Mitigation: use a stable optimistic id generated at submit time; use that id as React `key` throughout the lifecycle.
- **Could regress shipped chat-render fixes from CHAT-HIST-FIX.** The monotonic dedup logic in `useSkillAgent` (F1) must not be broken by optimistic append. Mitigation: M2 tests include a test that re-asserts the F1 invariant (message list never shrinks on stutter, even with optimistic append).

---

### Milestone 3: LatencyHUD + Frontend Timing

**Scope:** frontend
**Goal:** Developer-facing HUD shows real + perceived TTFT side by side, gated by `NEXT_PUBLIC_DEV_LATENCY_HUD=1`. Perceived TTFT measured honestly via `PerformanceObserver` paint timings.
**Estimated:** ~250 LOC implementation + ~120 LOC tests = ~370 LOC
**Duration:** 0.5 day

**Tasks:**
- [ ] Create `frontend/src/stores/latencyStore.ts` — minimal store, last-N marks per session, paint-timing observer hook. (~80 LOC)
- [ ] Create `frontend/src/components/dev/LatencyHUD.tsx` — fixed bottom-right, env-gated, side-by-side real (from AG-UI metadata) vs perceived (from PerformanceObserver) numbers, last 5 messages. (~140 LOC)
- [ ] Wire `LatencyHUD` into the chat layout (only mount when env flag set). (~15 LOC)
- [ ] In `useSkillAgent.ts`: capture `t_send`, `t_first_event`, `t_first_text_chunk`; push to latency store. (~25 LOC)
- [ ] Vitest: `LatencyHUD.test.tsx` — env-gate (renders only when `NEXT_PUBLIC_DEV_LATENCY_HUD === "1"`); displays mark values from store. (~80 LOC)
- [ ] Vitest: `latencyStore.test.ts` — last-N eviction; session-clear behavior. (~40 LOC)
- [ ] Manual: confirm prod bundle does NOT include the HUD when flag unset (run `npm run build`, grep chunks for `LatencyHUD`).

**Files to Create/Modify:**
- `frontend/src/stores/latencyStore.ts` (new, ~80 LOC)
- `frontend/src/components/dev/LatencyHUD.tsx` (new, ~140 LOC)
- `frontend/src/components/dev/__tests__/LatencyHUD.test.tsx` (new, ~80 LOC)
- `frontend/src/stores/__tests__/latencyStore.test.ts` (new, ~40 LOC)
- `frontend/src/hooks/useSkillAgent.ts` (modify, ~25 LOC delta)
- one chat page/layout file (modify, ~15 LOC delta) — mount HUD env-gated

**Acceptance Criteria:**
- [ ] `LatencyHUD.test.tsx::test_renders_only_when_env_flag_set` passes
- [ ] `LatencyHUD.test.tsx::test_displays_real_and_perceived_side_by_side` passes
- [ ] `latencyStore.test.ts::test_evicts_past_n_marks` passes
- [ ] `latencyStore.test.ts::test_clears_on_session_change` passes
- [ ] `cd frontend && npx vitest run` all green
- [ ] `cd frontend && npm run quality:check:fast` clean
- [ ] **Manual:** prod bundle has no `LatencyHUD` reference when flag unset

**Risks:**
- **PerformanceObserver browser support / timing accuracy.** Modern browsers fine; Safari has historical quirks. Mitigation: feature-detect, fall back to `requestAnimationFrame` after submit.

---

### Milestone 4: CLI `aiplatform skill probe` + LATENCY_REPORT event

**Scope:** fullstack
**Goal:** One-terminal command that fires a test message at a skill and prints the per-stage breakdown. Emit `LATENCY_REPORT` AG-UI Custom event at end-of-stream when `?probe=1` is set so the CLI can consume it.
**Estimated:** ~150 LOC implementation + ~80 LOC tests = ~230 LOC
**Duration:** 0.5 day

**Tasks:**
- [ ] Backend: in `stream_agui_events`, when query param `probe=1` is set, emit a final `{type: "LATENCY_REPORT", payload: {<all marks + model_used + routing_choice>}}` Custom event before stream close. (~40 LOC)
- [ ] CLI: `cli/aitana/commands/skill.py::probe(skill_id, message, --base-url, --token)` — opens SSE stream, prints per-stage breakdown when LATENCY_REPORT arrives. (~80 LOC)
- [ ] Pytest: backend test asserts `LATENCY_REPORT` only emitted when `?probe=1`. (~50 LOC)
- [ ] Pytest: CLI test (mock SSE response, assert print formatting). (~30 LOC)
- [ ] Update [local-dev-cli.md](local-dev-cli.md) — add `aiplatform skill probe` to the command tree.

**Files to Create/Modify:**
- `backend/adk/agui.py` (modify, ~40 LOC delta)
- `backend/tests/api_tests/test_stream_skill_ttft.py` (modify, ~50 LOC delta) — add LATENCY_REPORT test
- `cli/aitana/commands/skill.py` (modify or new, ~80 LOC)
- `cli/tests/test_skill_probe.py` (new, ~30 LOC)
- `docs/design/v6.1.0/local-dev-cli.md` (modify, ~15 LOC delta)

**Acceptance Criteria:**
- [ ] `test_stream_skill_ttft.py::test_latency_report_emitted_when_probe_param_set` passes
- [ ] `test_stream_skill_ttft.py::test_latency_report_silent_without_probe_param` passes
- [ ] `test_skill_probe.py::test_prints_full_breakdown` passes
- [ ] `aiplatform skill probe <local-dev-skill-id> --message "Hi"` runs end-to-end against local backend and prints the breakdown table

**Risks:**
- **Auth path for CLI.** CLI needs a Firebase token. Defer to existing local-dev-cli auth pattern (it already has `aiplatform auth` per [local-dev-cli.md](local-dev-cli.md)). Mitigation: probe command consumes the cached token like other commands.

---

### Milestone 5: Baseline + Implementation Report (full vs off A/B)

**Scope:** fullstack
**Goal:** Measure baseline real and perceived TTFT, AND prove instrumentation overhead is <5ms p50 by running the same workload with `AITANA_TTFT_MODE=full` vs `off`. File the implementation report; queue follow-up `ttft-optimization.md` for the next sprint.
**Estimated:** ~50 LOC of report + scripts (no new feature code)
**Duration:** 0.25 day

**Tasks:**
- [ ] Run `aiplatform skill probe` 50 times against local dev with `AITANA_TTFT_MODE=full`, three skills (no-tools, with-tools, with-docs). Record p50 / p95 real TTFT.
- [ ] Repeat with `AITANA_TTFT_MODE=off`. Same 50 iterations × three skills.
- [ ] Compute the delta (full minus off) at p50 and p95. **If >5ms p50, halt sprint and redesign.**
- [ ] Open the chat in dev with `NEXT_PUBLIC_DEV_LATENCY_HUD=1`; manually send 10 messages; eyeball perceived TTFT in the HUD; record p95 from store.
- [ ] Append Implementation Report to [ttft-instrumentation.md](ttft-instrumentation.md) with: per-skill p50/p95 real TTFT (full + off), delta, perceived TTFT p95, top 2 contributors to real latency.
- [ ] Open follow-up doc `ttft-optimization.md` (planned) listing the top 2 contributors and proposed fixes; register as 1.21 in [SEQUENCE.md](SEQUENCE.md).
- [ ] `move_to_implemented.sh ttft-instrumentation` if all acceptance criteria met.

**Acceptance Criteria:**
- [ ] Implementation Report appended with concrete numbers (full + off + delta + perceived)
- [ ] Instrumentation overhead <5ms p50 confirmed
- [ ] Perceived TTFT p95 <100ms confirmed
- [ ] Follow-up `ttft-optimization.md` filed
- [ ] Single conventional-commit on dev: `feat(ttft): instrument + perceived snappiness (1.20)`

---

## Day-by-Day Breakdown

### Day 1: Backend (Milestone 1)
- **Focus:** `LatencyTracker`, kill switch, all 7 marks, STAGE_PROGRESS emission, structured log
- **Tasks:** All M1 tasks
- **Checkpoint:** Backend tests green; manually confirm log line shape via `curl /api/skill/.../stream` with all three modes; `mode=off` produces nothing extra

### Day 2: Frontend (Milestones 2 + 3)
- **Focus:** Optimistic UI + skeleton + stage labels (M2), then HUD (M3)
- **Tasks:** All M2 tasks first (the actual UX payload), then M3 tasks
- **Checkpoint:** vitest + tsc + lint green; manual chat-walkthrough with HUD on; same-DOM-node invariant verified in DevTools; F1 dedup invariant from CHAT-HIST-FIX still passes

### Day 3: CLI + Baseline (Milestones 4 + 5)
- **Focus:** `aiplatform skill probe` + LATENCY_REPORT, then run the A/B baseline
- **Tasks:** All M4 tasks, then all M5 tasks
- **Checkpoint:** CLI prints breakdown end-to-end; instrumentation overhead measured (must be <5ms p50); Implementation Report filed; commit + (if all green) move-to-implemented

## Quality Gates

After each milestone:
```bash
# Backend (M1, M4)
cd backend && uv run pytest tests/ -q
cd backend && uv run ruff check .

# Frontend (M2, M3)
cd frontend && npx vitest run
cd frontend && npx tsc --noEmit -p tsconfig.json
cd frontend && npm run lint

# Combined fast gate
npm run quality:check:fast
```

After all milestones:
```bash
npm run docker:check          # Full CI simulation
```

## Success Metrics
- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/`)
- [ ] Lint and typecheck clean
- [ ] Docker build succeeds
- [ ] Real TTFT measured: p50/p95 per skill recorded in implementation report
- [ ] Perceived TTFT p95 <100ms
- [ ] Instrumentation overhead <5ms p50 (full vs off)
- [ ] STAGE_PROGRESS labels render in skeleton bubble; same DOM node throughout
- [ ] `aiplatform skill probe` works end-to-end
- [ ] Cloud Trace shows `aitana.ttft.*` attributes
- [ ] BigQuery query returns p50/p95 TTFT per skill

## Dependencies
- chat-message-rendering (1.1 ✅)
- chat-session-history (1.8 ✅)
- document-to-ai-pipeline (1.9 ✅)
- ADK `LlmResponse` partial-event behavior — must verify before Day 1 M1 starts (`mcp__adk-mcp__search_code`)

## Open Questions
- Does ADK emit `LlmResponse` partials before model completion? If not, `first_model_token` collapses into model RTT. Investigate Day 1 morning.
- Should `before_agent_ms` split per-callback (doc loader vs structured-extraction)? Defer to ttft-optimization.md unless baseline reveals >50ms p50.
- Cloud Run min-instances for cold-start: not in scope this sprint (local-dev only per user). Note in implementation report; defer.

## Notes
- **Local-only baseline.** User clarified latency seen locally — no infra in scope. Cloud Run cold-start optimization is a separate ticket if it ever surfaces.
- **Kill switch is non-negotiable.** Every commit in M1 must be runnable with `AITANA_TTFT_MODE=off` and produce identical behavior to before this sprint. M5 verifies this empirically.
- **Don't break shipped fixes.** F1 monotonic dedup (CHAT-HIST-FIX) is the closest invariant at risk; M2 tests must re-assert it.
- **Same DOM node invariant.** Optimistic skeleton → streaming → final must reuse the same React component instance. Use stable optimistic id as `key` throughout.
- **No fake progress.** Stage labels are server-authored and conditional on real backend events. If backend stalls, label says "Still working…" honestly.
