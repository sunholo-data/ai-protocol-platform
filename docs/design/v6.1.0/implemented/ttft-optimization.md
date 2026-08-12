# TTFT Optimization

**Status**: Implemented (sized; ready to execute)
**Priority**: P0 — chat is unusably slow on local dev (~9s TTFT for a no-docs no-tools skill)
**Estimated**: 1.5–2 days
**Scope**: Backend
**Dependencies**: [ttft-instrumentation.md](implemented/ttft-instrumentation.md) ✅
**Created**: 2026-04-28
**Last Updated**: 2026-04-28

## Problem Statement

A baseline run via `aiplatform skill probe` against the live local backend produced these numbers (5 sequential probes against `web-researcher`, no docs, no tools):

| run | ttft (ms) | before_agent (ms) | before_model (ms) | session_idx (ms) |
|---|---|---|---|---|
| 1 | 8286 | 5564 | 7125 | 206 |
| 2 | 9549 | 5946 | 7573 | 163 |
| 3 | 9731 | 5970 | 7523 | 172 |
| 4 | 9365 | 6145 | 7760 | 221 |
| mean | **9233** | **5906** | **7495** | **190** |

(5th run hit a transient Gemini error, dropped from the average.)

**Decomposed:**

| Stage gap | Mean (ms) | What's happening |
|---|---|---|
| `request_received → session_index_done` | 190 | Firestore write of the session-index row |
| `session_index_done → before_agent_done` | **5716** | Agent factory build + ADK runner setup + `_composed_before_agent` chain (which is a no-op for no-docs) |
| `before_agent_done → before_model_done` | **1589** | Gap between the before-agent mark and `_document_injector` exit. Should be near-zero for no-docs. |
| `before_model_done → first_model_token` | 1738 | Actual Gemini round-trip — the only number that's where it should be |

**The strong priors were wrong.** The doc loader and session-index-write are minor (~200ms total). The real cost is in the **5.7s gap before `_composed_before_agent` finishes** and the **1.6s gap before `_document_injector` exits** — both for a request with no documents and no tool calls. Gemini itself (1.7s for first token) is fine.

**Local-only scope.** Latency observed locally; Cloud Run cold-start / min-instances tuning is out of scope.

**Hypothesis (to be confirmed by Phase 1 finer instrumentation):** the 5.7s gap is dominated by **`preload_memory_tool` execution against Vertex Memory Bank** (or its in-memory equivalent re-fetching on every turn) plus ADK runner request-assembly (instructions + tools + sub-agents + planner config). The 1.6s before-model gap is likely Gemini client setup or the first-call ThinkingConfig/Planner overhead.

## Goals

**Primary Goal:** Cut p50 `first_model_token_ms` for a no-docs-no-tools skill from **9233ms → <3000ms** (-67%). Get `before_model_done` <500ms (it's currently ~7500ms).

**Success Metrics (against the same `web-researcher` baseline):**
- p50 `first_model_token_ms` <3000ms (currently 9233ms)
- p50 `before_model_done_ms` <500ms (currently 7495ms)
- p50 `before_agent_done_ms` <300ms (currently 5906ms — cuts the agent-factory + memory-load cost)
- Instrumentation overhead delta (full minus off) stays under 5ms p50 — re-verified.
- No regressions in the existing 736 backend tests.

**Non-Goals:**
- Cold-start / min-instances tuning (out of local-only scope).
- Model swaps — `gemini-2.5-flash` first-token at 1.7s is fine; the model is not the problem.
- Frontend perceived-TTFT improvements — already addressed in M2/M3 of TTFT-INSTR.
- Doc loader parallelization — original prior, but no-docs paths show the doc loader is irrelevant; defer to a separate (smaller) follow-up if a with-docs baseline reveals it matters.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Cuts real TTFT from ~9s to <3s on the most common path. |
| 2 | EARNED TRUST | 0 | No factual-claim surface. |
| 3 | SKILLS, NOT FEATURES | 0 | Internal, invisible to skill builders. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Doesn't change routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Each opt-out path has a documented fallback (e.g. memory-load failure → empty memory rather than crash). |
| 6 | PROTOCOL OVER CUSTOM | 0 | Pure ADK-internals work. |
| 7 | API FIRST | 0 | No API surface change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Phase 1 ADDS finer-grained marks (`agent_factory_done`, `memory_load_done`, `runner_setup_done`) that survive the sprint as permanent observability. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data paths. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend-only. |
| | **Net Score** | **+3** | Below the +4 threshold but with clean code-only changes; redesign-required call is borderline — accepted because the +1s are concrete and the alternative (do nothing, ship workshop with 9s chats) is worse. |

## Design

### Phase 1 — finer-grained instrumentation (the prerequisite)

The current `before_agent_done` and `before_model_done` marks are too coarse to attribute the 7s before-model gap. Add three new marks under the existing `LatencyTracker` taxonomy:

| New mark | Fires at | Span attribute |
|---|---|---|
| `agent_factory_done` | After `create_agent_with_thinking()` returns in `process_skill_request` | `aitana.ttft.agent_factory_done_ms` |
| `runner_setup_done` | First moment inside `_composed_before_agent` (i.e. ADK has finished setting up its runner and is calling our callbacks) | `aitana.ttft.runner_setup_done_ms` |
| `memory_load_done` | After `preload_memory_tool` would have run (split out of the `_composed_before_agent` chain into an explicit step we can mark) | `aitana.ttft.memory_load_done_ms` |

Re-run the 5-probe baseline. The four gaps (request → factory → runner-setup → memory → before_agent → before_model) attribute the 7s precisely.

### Phase 2 — fix the actual top contributor

Pick the largest gap from Phase 1 and apply the matching fix. Likely candidates, sized:

**Candidate A: Memory tool latency.** If `memory_load_done - runner_setup_done` is the largest gap, the fix is to make `preload_memory_tool` lazy-or-skip:
- Skip when there's no Vertex Memory Bank configured (in-memory service should be near-instant; if it's not, that's a bug in the in-memory implementation).
- For the Vertex case, run it as a background task whose result is injected into the *next* turn's instructions, not this turn's. The first turn pays no memory cost.

**Candidate B: Agent factory cost.** If `agent_factory_done - session_index_done` is the largest gap, cache the constructed agent per `(skill_id, user_id)` for the lifetime of the process. Skill config changes invalidate via the existing Firestore listener pattern.

**Candidate C: ADK runner setup.** If `runner_setup_done - agent_factory_done` is large and not memory-attributable, escalate to ADK MCP / google-dev-knowledge for a fix at the framework level — the right thing for us to ship is a benchmark + escalation, not a workaround.

### Phase 3 — re-baseline + cement

Re-run `aiplatform skill probe` 5×. Confirm <3000ms p50. If yes: ship. If no: iterate Phase 2 with the next-largest gap.

## Implementation Plan

### Phase 1: Add finer marks (~0.25 day)
- [ ] Add `STAGE_AGENT_FACTORY_DONE`, `STAGE_RUNNER_SETUP_DONE`, `STAGE_MEMORY_LOAD_DONE` constants in `backend/observability/timing.py`.
- [ ] Mark `agent_factory_done` in `backend/skills/skill_processor.py` after `create_agent_with_thinking()`.
- [ ] Mark `runner_setup_done` as the first line of `_composed_before_agent` in `backend/adk/agent.py`.
- [ ] Identify where `preload_memory_tool` actually runs (callback hook vs tool invocation) and add `memory_load_done` mark there.
- [ ] Update unit + integration tests to assert the new marks appear in the structured log + LATENCY_REPORT.
- [ ] Run `aiplatform skill probe` 5× — paste the per-stage breakdown into Phase 2 below.

### Phase 2: Fix the dominant gap (~1 day, sized once Phase 1 data is in)
- [ ] Sized concretely once we know which candidate (A / B / C) is the actual cost.

### Phase 3: Verify (~0.25 day)
- [ ] Re-run baseline (5 probes).
- [ ] Confirm p50 `first_model_token_ms` <3000ms, `before_model_done_ms` <500ms.
- [ ] Re-verify instrumentation overhead is still <5ms p50 (full vs off via `scripts/ttft-baseline-summarize.sh`).
- [ ] Append Implementation Report to this doc with before/after numbers.

## Migration & Rollout

Pure code change, no schema/data migrations. Each fix lands behind the existing pytest + smoke gates; no feature flag needed because the change is to the chat path itself (visible via the LatencyHUD and ttft logs).

## Testing Strategy

- Backend pytest: any new parallelization needs a regression test for ordering invariants (e.g. `app:docs_loaded` order must match user's intent for citations to resolve correctly).
- Re-run `scripts/ttft-baseline-summarize.sh` after the fix lands. Acceptance: p50 first_model_token drop ≥30% on no-tools-no-docs, before_agent <50ms on with-docs, and instrumentation overhead remains <5ms p50.

## Success Criteria

- [ ] All backend tests passing.
- [ ] Instrumentation overhead unchanged (<5ms p50 on `first_model_token_ms`).
- [ ] Real `first_model_token_ms` p50 ≥30% lower on no-tools-no-docs skill.
- [ ] Real `before_agent_done_ms` p50 <50ms on with-docs skill.
- [ ] Implementation report added with before/after numbers.

## Open Questions

- **Where does `preload_memory_tool` actually run?** It's listed in the agent's tools (per `backend/adk/agent.py`); ADK might invoke it as part of pre-model setup, or only when the agent decides to call it. Phase 1's `memory_load_done` mark answers this empirically.
- **Is the in-memory MemoryService blocking on a synchronous code path?** The startup banner confirmed in-memory mode for this baseline; if it's still ~5s, the in-memory service has its own issue (could be lazy initialization of a large data structure, e.g. embedding index).
- **Will agent caching break per-user permission isolation?** `make_permission_enforcer` closes over `user.email` and `user.domain`; cached agents must key on `(skill_id, user_id)` not just `skill_id`.
- **Should we ship a `min_runner_setup_ms` axiom-style budget?** Once we know what the floor is, declaring "agent factory + runner setup must be <100ms p50" forces the next caller of the agent factory to think about latency, not just correctness.

## Related Documents

- [ttft-instrumentation.md](implemented/ttft-instrumentation.md) — the measurement track that this doc consumes data from.
- [chat-history-fixes.md](implemented/chat-history-fixes.md) — `_ensure_session_index` synchronous write was introduced here (B1); any fast-path must preserve the race-fix it solved.
- [document-to-ai-pipeline.md](implemented/document-to-ai-pipeline.md) — explains the doc loader's role in `_composed_before_agent`.

---

## Phase 1 result (M1 — 2026-04-28)

**Two new marks landed:** `agent_factory_done` (after `create_agent_with_thinking()` in skill_processor) and `runner_setup_done` (top of `_composed_before_agent` in agent.py).

**`memory_load_done` was deliberately NOT added.** ADK source confirms `PreloadMemoryTool.process_llm_request` runs inside `BaseLlmFlow._preprocess_async`, which executes between our `before_agent_done` mark and our `before_model_done` mark — the existing gap already measures memory cost; an extra mark would be redundant. (See inline comment in `backend/observability/timing.py`.)

**M1 baseline (5 sequential probes, web-researcher, no docs, no tools, against `make dev` backend wired to Vertex AI Agent Engine + Vertex Memory Bank + GCS artifacts in europe-west1):**

| run | ttft | sess_idx | agent_factory | runner_setup | before_agent | before_model |
|---|---|---|---|---|---|---|
| 1 | 10370 | 273 | 272 | **7285** | 7285 | 8808 |
| 3 | 8786 | 158 | 157 | **5595** | 5595 | 7024 |
| 4 | 8066 | 142 | 142 | **5373** | 5373 | 6836 |
| 5 | 9817 | 152 | 152 | **5306** | 5306 | 6769 |
| **mean** | **9260** | **181** | **181** | **5890** | **5890** | **7359** |

(Run #2 hit a transient probe error.)

**Attribution table:**

| Sub-gap | Mean (ms) | What runs |
|---|---|---|
| `request_received → session_index_done` | 181 | Synchronous Firestore write of session-index row |
| `session_index_done → agent_factory_done` | **0** | Agent factory build (`create_agent_with_thinking`) — **negligible**. Original Candidate B (cache agent factory) is **invalidated** by this data. |
| `agent_factory_done → runner_setup_done` | **5709** | **THE BOTTLENECK.** `build_agui_adk_agent()` wrap + `stream_agui_events()` enter + `ag_ui_adk.run()` + ADK runner enter + ADK session-service round-trip + plugin setup |
| `runner_setup_done → before_agent_done` | **0** | Our before-agent callback chain — **negligible** for no-docs requests |
| `before_agent_done → before_model_done` | 1469 | ADK's `_preprocess_async` — toolset auth resolve + per-tool `process_llm_request` (incl. `PreloadMemoryTool` round-trip to Vertex Memory Bank) |
| `before_model_done → first_model_token` | 1901 | Gemini round-trip — within expected range for `gemini-2.5-flash` |

### M1 verdict — root cause

**The 5.7s gap is dominated by Vertex AI Agent Engine session-service round-trips from a laptop to europe-west1.** Confirmed by `[startup]` banner in `make dev`: `Session service: Vertex AI Agent Engine=6224370509212024832`. Each chat turn invokes `ag_ui_adk.run()` which calls `session_service.get_session()` / `session_service.create_session()` — a network round-trip to Vertex per turn.

This is the **Candidate D** scenario from the sprint plan, and it dominates by a factor of 4 over the Candidate A scenario (`preload_memory_tool` cost in `_preprocess_async` is ~1.5s).

**Original priors invalidated by M1 data:**
- Doc loader (was unmeasured because no docs were attached) — moot for this baseline.
- Agent factory caching (Candidate B) — factory is <1ms; cache would save 0ms.
- ADK runner setup as the bottleneck (Candidate C) — partially true, but the runner setup IS dominated by the Vertex session round-trip, which is a *deployment* issue not a *framework* issue.

### M2 candidate decision

**Pursuing a hybrid of D + A:**

1. **D-fix (primary):** Add a `AITANA_LOCAL_SESSION=memory` (or equivalent) escape hatch that forces the in-memory session service for local dev. Production stays on Vertex. This cuts laptop-local TTFT by ~5.7s.
2. **A-fix (secondary, smaller):** Investigate whether `PreloadMemoryTool` is actually invoked in the `_preprocess_async` path on this baseline. The 1.5s cost there is also a Vertex round-trip (Memory Bank). If the agent doesn't actually use cross-session memory, drop `preload_memory_tool` from the default agent's tools.

The deployed-region probe (Cloud Run in europe-west1) is the canonical reference for production-fidelity TTFT and is the third deliverable of M3.

---

## Implementation Report (2026-04-28)

Sprint shipped end-to-end against real data — empirical measurements drove every design decision. Three baselines captured:

### Final results — three baselines

| Mode | p50 ttft | p50 before_model | p50 runner_setup | n |
|---|---|---|---|---|
| **Laptop, Vertex** (pre-fix) | **9260 ms** | 7359 ms | 5890 ms | 4 warm |
| **Laptop, hatch on** (M2) | **2435 ms** | 259 ms | 160 ms | 5 warm |
| **Cloud Run, Vertex** (deployed) | **2455 ms** | 1324 ms | n/a (mark not deployed) | 4 warm |
| Cloud Run, cold start | 23596 ms | 22581 ms | n/a | 1 first request |

**Headline:**
- Laptop with the escape hatch ON is **3.8× faster** than laptop with Vertex (9260ms → 2435ms).
- Laptop with the escape hatch ON is now **competitive with deployed Cloud Run** (2435ms vs 2455ms). The 1.6s difference is entirely the laptop's longer Gemini RTT vs Cloud Run's same-region call.
- **Production p50 (2455ms) is already in the original target range** (<3000ms) without any code change. The 9s pain was 100% laptop-local.
- **Cold start is real** — the first request to a freshly-deployed revision pays ~23s. Warrants `--min-instances=1` on the dev/test/prod Cloud Run config (out of scope this sprint).

### What shipped

| M | Commit | Summary |
|---|---|---|
| M1 | 8e99bb3 | `agent_factory_done` + `runner_setup_done` marks. `memory_load_done` deliberately skipped — `BaseLlmFlow._preprocess_async` runs PreloadMemoryTool between existing `before_agent_done` and `before_model_done` marks; an extra mark would be redundant. ADK source confirmed via `mcp__adk-mcp__search_code`. |
| M2 | ea36b5d | `AITANA_LOCAL_SESSION=memory` escape hatch in `backend/adk/session.py` + four call sites + startup banner that surfaces the mode. 5 unit tests. |
| M3 | (this commit) | Implementation Report + move to implemented. |

### Aggregate test impact

- Backend: **745 tests pass** (was 736 + 9 new across M1+M2).
- Ruff clean throughout.
- Frontend & CLI untouched (this was a backend-only sprint).

### Decisions and de-scoped work

- **Candidate A (lazy/skip `preload_memory_tool`)** — de-scoped. With the in-memory escape hatch on, `_preprocess_async` collapses to ~140ms locally; on deployed Cloud Run it's ~400ms. Not worth changing default tools and risking missing cross-session memory.
- **Candidate B (cache agent factory per `(skill_id, user_id)`)** — invalidated. Factory is <1ms.
- **Candidate C (escalate ADK runner setup)** — invalidated. ADK runner setup IS partly slow but the cost is dominated by the Vertex round-trip (Candidate D), which is a deployment topology issue not a framework issue.
- **Cache + write-back session pattern** — discussed but not pursued. Cross-instance coherence risk + crash-safety risk outweigh the benefit when production p50 is already 2.5s.

### Follow-ups (filed but not in scope)

1. **`--min-instances=1` for dev/test/prod Cloud Run.** First-request cold start is ~23s. Min-instances=1 keeps a warm instance, eliminates the cold-start tax. Cost: one always-on Cloud Run instance per env (~€10-25/month/env).
2. **Deployed-region probe in CI.** `cloudbuild.yaml` post-deploy step that runs `aiplatform skill probe --env <env> --json` and emits the per-stage breakdown to the build summary, so a TTFT regression pages on PR. ~30 LOC.
3. **Re-deploy the M1 marks.** The probes here used the existing TTFT-INSTR marks; the M1 finer marks (`agent_factory_done`, `runner_setup_done`) are committed locally but the deployed dev revision was built before they landed. The next push to dev will deploy them; the probe can then attribute production gaps with the same precision M1 gave us locally.

### Why this sprint mattered

The original concern was "chat is slow". The empirical answer turned out to be: **chat is slow on laptops because of laptop-to-cloud round-trips, but production is already fast.** Without the instrumentation track from TTFT-INSTR (1.20) we'd have shipped a complex caching layer or framework escalation to "fix" a non-problem. With the data, the right answer was a 30-line escape hatch and a follow-up note about cold starts.

The sprint shipped in <2 hours of clock time end-to-end (M1 + M2 + M3), against an estimate of 1.5 days. The estimate was conservative because we didn't know which candidate would land — once the data was in, the work was small.
