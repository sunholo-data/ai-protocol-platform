# Sprint Plan: ACTION-TRIGGER — Action-Triggered Agent Turn

## Summary

Ship the bundled write+run endpoint + frontend hook + per-skill opt-in + demo + CLI that lets an A2UI surface click drive a full agent turn without a chat message — closing the Pattern 1 loop for the July 2026 workshop.

**Duration:** 1 day (3 milestones, M3 parallelizable)
**Scope:** Fullstack
**Dependencies:** A2UI surface-action endpoint (sprint 2.10 ✅), `build_agui_adk_agent` + `stream_agui_events` (✅), G41 terminal-event dedup (✅), [a2ui-workshop-demo.md](a2ui-workshop-demo.md) (1.19, demo skill seed) — ship M3 demo on top of 1.19's skill or stand-alone
**Risk Level:** Low — every primitive already exists; new code is glue + one opt-in field + one new route
**Design Doc:** [action-triggered-agent-turn.md](action-triggered-agent-turn.md)

## Current Status Analysis

### Recent Velocity (last 14 days)

- **43 commits, 134 files changed, +15,444 / -609 lines** — high cadence, mostly A2A protocol + template hardening
- Recent sprint comparators:
  - **G41 dedup** (commit `9256007`, the workaround this sprint reuses): ~50 LOC + 4 tests, single commit, <0.25d
  - **G45 A2A-INVOKE M1-M3**: 3-milestone bridge sprint, similar shape (new route + auth gate + tests), ~1d
  - **A2A-DOCS** (just shipped): 4-milestone sprint, fullstack, ~2d
- **Estimated capacity for this sprint:** ~500-800 LOC over 1 day (well within recent demonstrated rate)

### Existing Implementation We Build On

- ✅ `backend/protocols/a2ui_surface_action_routes.py` — 7-gate write endpoint (refactor target)
- ✅ `backend/adk/agui.py` — `build_agui_adk_agent` + `stream_agui_events` (G41-deduped, reused as-is)
- ✅ `backend/adk/a2ui_surface_context.py` — `wrap_with_a2ui_surface_context` InstructionProvider (small clause addition)
- ✅ `frontend/src/providers/AGUIProvider.tsx` — `HttpAgent` factory (unchanged, hook reuses parser)
- ✅ `frontend/src/providers/SurfaceRegistry.tsx` — surface state snapshot path (read for `forwardedProps`)
- ✅ `frontend/src/components/protocols/A2UISurfaceMount.tsx` — current `onAction` POST target (add prop)
- ✅ `cli/` `aiplatform` CLI tree (add subcommand)
- ✅ `tests/api_tests/test_a2ui_surface_action_routes.py` — gate test patterns to mirror

## Proposed Milestones

### Milestone 1 — Backend foundation

**Scope:** backend
**Goal:** New `surface-action-run` endpoint live behind a per-skill opt-in, reusing all existing gates via a shared module. Existing `surface-action` regression-free.
**Estimated:** ~250 LOC impl + ~150 LOC tests = ~400 LOC
**Duration:** ~0.4d (~3h)

**Tasks:**
- [ ] M1.1 — Refactor shared helpers from `a2ui_surface_action_routes.py` → `_a2ui_surface_shared.py` (`_require_session`, `_enforce_skill_opt_in`, `_enforce_size_cap`, `_STATE_KEY_NAMESPACE`). Both routes import from there. Pure refactor — 614 existing tests stay green. (~80 LOC moved)
- [ ] M1.2 — Add `allow_action_triggered_runs: bool = False` to `A2uiToolConfig` in `backend/db/models/skill.py`. (~10 LOC + 2 unit tests)
- [ ] M1.3 — Add `forwardedProps._action_trigger` framing clause to `wrap_with_a2ui_surface_context` — when present, prefix the surface namespace block with "the user just performed this action: ..." (~15 LOC + 1 unit test)
- [ ] M1.4 — New `backend/protocols/a2ui_surface_action_run_routes.py` — 8-gate write-and-run endpoint, synthesizes `RunAgentInput(messages=[], forwarded_props={a2ui_surface_state, _action_trigger})`, streams via `stream_agui_events`. (~120 LOC + 5 gate tests + 1 happy-path SSE assertion test)
- [ ] M1.5 — Register router in `backend/fast_api_app.py`. (~3 LOC)

**Files to Create/Modify:**
- `backend/protocols/_a2ui_surface_shared.py` (new, ~80 LOC)
- `backend/protocols/a2ui_surface_action_routes.py` (modify — re-export from shared, no behaviour change)
- `backend/protocols/a2ui_surface_action_run_routes.py` (new, ~120 LOC)
- `backend/db/models/skill.py` (modify, +10 LOC)
- `backend/adk/a2ui_surface_context.py` (modify, +15 LOC)
- `backend/fast_api_app.py` (modify, +3 LOC)
- `backend/tests/api_tests/test_a2ui_surface_action_run_routes.py` (new, ~120 LOC)
- `backend/tests/unit/test_a2ui_surface_context_injection.py` (modify, +30 LOC for the `_action_trigger` case)

**Acceptance Criteria:**
- [ ] `make lint` clean
- [ ] `make test-fast` green — all new tests + 614 existing surface-action tests pass
- [ ] All 8 gates exercised: 200/204 happy, 401 missing JWT, 403 access denied, 403 unknown skill, 403 no a2ui config, 403 `allow_surface_context_writes=false`, **403 `allow_action_triggered_runs=false` (new)**, 413 size cap
- [ ] G41 dedup: synthetic agent raising mid-stream → exactly one terminal event in SSE response

**Risks:**
- `ADKAgent.run()` may reject `RunAgentInput.messages=[]` (empty message list). Mitigation: if it does, fall back to a single synthetic `SystemMessage` with empty content; the InstructionProvider already provides the real prompt context. Verify in M1.4 by running once locally.
- Refactor breaks existing surface-action import path. Mitigation: import-test in M1.1 (run `make test-fast` after the move, before any new code lands).

### Milestone 2 — Frontend wiring

**Scope:** frontend
**Goal:** `useActionDrivenAgent` hook + `<A2UISurfaceMount triggerOnAction>` opt-in, default behaviour unchanged. Existing A2UI mount tests stay green.
**Estimated:** ~140 LOC impl + ~100 LOC tests = ~240 LOC
**Duration:** ~0.3d (~2.5h)

**Tasks:**
- [ ] M2.1 — `src/hooks/useActionDrivenAgent.ts` — exposes `triggerAction(surfaceId, action)`. POSTs to `surface-action-run`, consumes SSE via `@ag-ui/client`'s event parser, dispatches into `SurfaceRegistry`. Returns `Promise<void>`, resolves on `RUN_FINISHED`, rejects on `RUN_ERROR`. (~80 LOC + 4 tests)
- [ ] M2.2 — `<A2UISurfaceMount triggerOnAction?: boolean>` prop. When `true`, route `onAction` through `useActionDrivenAgent`; when `false` (default), preserve current `surface-action` POST. (~20 LOC + 2 branch tests)
- [ ] M2.3 — Regression test: `useStableThreadId` does not rebuild `HttpAgent` when action-triggered run fires (same session_id). (~30 LOC test)
- [ ] M2.4 — Gracefully handle HTTP 4xx from `surface-action-run` (skill not opted in) — console.warn, no throw, surface stays in last-rendered state. (~10 LOC)

**Files to Create/Modify:**
- `frontend/src/hooks/useActionDrivenAgent.ts` (new, ~80 LOC)
- `frontend/src/hooks/__tests__/useActionDrivenAgent.test.tsx` (new, ~100 LOC)
- `frontend/src/components/protocols/A2UISurfaceMount.tsx` (modify, +20 LOC)
- `frontend/src/components/protocols/__tests__/A2UISurfaceMount.test.tsx` (modify, +30 LOC for new prop branch)

**Acceptance Criteria:**
- [ ] `npm run quality:check:fast` green (lint + tsc + auth-fetch)
- [ ] `npm run test:run` green — all new tests + existing A2UI mount tests pass
- [ ] No regression in `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` (chat-driven A2UI still works)

**Risks:**
- SSE consumer in browser may need polyfill for older targets. Mitigation: reuse the same EventSource/fetch+stream parser that `@ag-ui/client`'s `HttpAgent.runAgent` uses — proven to work in dev + prod for chat turns.
- `SurfaceRegistry` dispatch may need a new method for "this update came from an action-triggered run, not a chat turn". Mitigation: same dispatch path is fine — `STATE_SNAPSHOT` / tool calls update the registry regardless of who triggered the run. Verify in M2.1.

### Milestone 3 — Demo + CLI + smoke (parallelizable)

**Scope:** fullstack (3 independent sub-tasks)
**Goal:** Workshop-grade Pattern 1 demo path, CLI debug command, repeatable smoke test.
**Estimated:** ~180 LOC + 5 manual run iterations = ~200 LOC total
**Duration:** ~0.3d (~2.5h, parallelizable — wall-clock could be ~1h with concurrent agents)

**Tasks (independent — can run in parallel after M2 lands):**
- [ ] M3.1 (demo) — Extend 1.19's demo skill (or a new sibling skill) with a "Click Counter" surface. One Button, agent emits new A2UI on each click incrementing a counter. Skill opts in via `allow_action_triggered_runs: true`. Add Pattern 1 section to `/dev/a2ui` fixture page. (~70 LOC seed + prompt + ~40 LOC fixture page section + 1 vitest)
- [ ] M3.2 (CLI) — `aiplatform sessions trigger-action <session-id> --surface <id> --action <name> [--component <id>] [--context <json>]`. POSTs to new endpoint, consumes SSE, prints AG-UI events. (~50 LOC + 1 unit test)
- [ ] M3.3 (smoke) — `scripts/smoke-pattern1.sh` — 5 successive `trigger-action` calls against the demo skill in `LOCAL_MODE=1`, asserts each turn emits an A2UI tool call and counter increments. (~30 LOC shell + log assertions)

**Files to Create/Modify:**
- `backend/db/local_fixture.py` (modify, +70 LOC to seed the click-counter skill) — OR a new sibling fixture
- `frontend/src/app/dev/a2ui/page.tsx` (modify, +40 LOC for Pattern 1 section)
- `frontend/src/app/dev/a2ui/__tests__/page.test.tsx` (modify, +20 LOC)
- `cli/commands/sessions.py` (modify, +50 LOC for `trigger-action` subcommand)
- `cli/tests/test_sessions_trigger_action.py` (new, ~30 LOC)
- `scripts/smoke-pattern1.sh` (new, ~30 LOC)

**Acceptance Criteria:**
- [ ] `LOCAL_MODE=1 make dev` boot → navigate to `/dev/a2ui` Pattern 1 section → click 3 times → surface re-renders 3 times, counter increments, no chat composer visible
- [ ] `aiplatform sessions trigger-action <id> --surface … --action …` round-trips and prints AG-UI events
- [ ] `scripts/smoke-pattern1.sh` passes 5/5 consecutive runs
- [ ] [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log gains a row dated 2026-06-08+

**Risks:**
- LOCAL_MODE fixture seeding may collide with 1.19's pending demo skill. Mitigation: add a distinct skill `Demo Click Counter` rather than extending `Demo A2UI Forms`. Coordinate with whoever ships 1.19 if it lands first (likely separate sprint).
- Workshop talk verification log update can be deferred to a follow-up commit if 1.19 hasn't merged yet.

## Day-by-Day Breakdown

This sprint is ~1 day. Single-day execution:

### Day 1 (single day)

**Morning (M1, ~3h):**
- M1.1 refactor → run `make test-fast` (must stay 614/614 green)
- M1.2 + M1.3 model field + framing clause
- M1.4 new endpoint + 5 gate tests + happy-path SSE test
- M1.5 router register
- **Checkpoint:** `make lint && make test-fast` green; manual `curl` to new endpoint returns SSE stream

**Afternoon block A (M2, ~2.5h):**
- M2.1 hook + 4 tests
- M2.2 prop + 2 branch tests
- M2.3 regression test
- M2.4 graceful fallback
- **Checkpoint:** `npm run quality:check:fast && npm run test:run` green; `<A2UISurfaceMount>` test file fully covers both branches

**Afternoon block B (M3, ~2.5h — parallelizable via Task sub-agents):**
- M3.1 (demo skill + fixture page section) — agent A
- M3.2 (CLI subcommand) — agent B
- M3.3 (smoke script) — agent C
- **Checkpoint:** Pattern 1 demo runs 5/5; `smoke-pattern1.sh` passes; CLI command prints events
- **End of day:** All success criteria from the design doc verified; ready for sprint-evaluator

## Quality Gates

After each milestone:
```bash
# Backend (M1)
cd backend && make lint && make test-fast

# Frontend (M2, M3 demo)
cd frontend && npm run quality:check:fast && npm run test:run
```

After all milestones (pre-push CI parity):
```bash
cd backend && make lint && make test-fast
cd frontend && npm run quality:check  # tests + build
```

## Success Metrics

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`make test-fast`)
- [ ] Lint and typecheck clean (`make lint`, `npm run quality:check:fast`)
- [ ] 614+ existing backend tests stay green (no regression in shared refactor)
- [ ] Pattern 1 demo runs 5/5 consecutive iterations via `scripts/smoke-pattern1.sh`
- [ ] `aiplatform sessions trigger-action` round-trips successfully
- [ ] First-event budget: AG-UI `RUN_STARTED` arrives <500ms after click in dev (manual measurement; instrument via existing `LatencyHUD`)
- [ ] Sprint-evaluator passes (target ≥70/100; aim ≥85)

## Dependencies

- A2UI surface-action endpoint (sprint 2.10 ✅) — refactor target
- G41 terminal-event dedup (commit `9256007` ✅) — reused in new endpoint
- `build_agui_adk_agent` / `stream_agui_events` (✅) — reused as-is
- 1.19 a2ui-workshop-demo — M3 demo can share its skill seed OR run independently with its own `Demo Click Counter` skill

## Open Questions

- **Synthetic `RunAgentInput.messages=[]`** — does `ADKAgent.run()` accept this? If not, M1.4 falls back to a single synthetic `SystemMessage` with empty content. Verify within first hour of M1.4.
- **Upstream pattern alignment** — [a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) is open; if a maintainer responds with a different recommended shape before this sprint ships, the outer route signature may need to change (gates + write + run pipeline are isolated enough to swap).
- **Demo skill coordination with 1.19** — if 1.19 ships first, M3.1 extends its skill; if not, M3.1 ships a new sibling skill. Decided at sprint start based on 1.19 status.

## Notes

- Sprint is small and tight because every primitive already exists. The work is glue + one opt-in field + one new route + one frontend hook.
- M3 sub-tasks (M3.1, M3.2, M3.3) are independent — execute in parallel via Task sub-agents to compress wall-clock from ~2.5h to ~1h.
- After ship, [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log gets a row referencing this sprint + commit SHAs.
- If a2ui#1570 lands a different recommendation post-ship, the route shape is small enough to flip in a follow-up sprint without disturbing the gates or the InstructionProvider.
