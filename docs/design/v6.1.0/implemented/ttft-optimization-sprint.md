# Sprint Plan: TTFT-OPTIMIZATION

## Summary

Cut p50 first_model_token from 9233ms → <3000ms on a no-docs-no-tools skill. Phase 1 adds three finer-grained `LatencyTracker` marks that attribute the unexplained 7.5s before-model gap; Phase 2 fixes whichever stage Phase 1 reveals as dominant; Phase 3 re-baselines and reports.

**Duration:** 1.5 days (one calendar day if Phase 2 is candidate A or B; longer if it's candidate C — escalation to ADK).
**Scope:** Backend (mostly observability/timing module + skill_processor + agent factory).
**Dependencies:** [ttft-instrumentation](implemented/ttft-instrumentation.md) ✅ — provides `LatencyTracker` infra.
**Risk Level:** Low — Phase 1 is purely additive instrumentation; Phase 2 is a code-only change behind existing tests; Phase 3 is measurement.
**Sprint ID:** `TTFT-OPTIMIZATION`
**Design Doc:** [ttft-optimization.md](ttft-optimization.md)

## Current Status Analysis

### Recent Velocity

- Last 24h: 5 sprint commits + 1 rename + 1 doc-revise = ~2400 LOC, all green tests.
- Comparable sprint shape: TTFT-INSTR was 5 milestones / ~1900 LOC in 1 day. This sprint is 3 milestones / ~600 LOC.
- Plenty of headroom — sub-day velocity.

### Existing Implementation

- `LatencyTracker` ([backend/observability/timing.py](../../../backend/observability/timing.py)) is the right surface for new marks; pattern proven across 7 existing stages.
- `_composed_before_agent` in [backend/adk/agent.py:330-348](../../../backend/adk/agent.py#L330) already marks `before_agent_done` at its tail; the new `runner_setup_done` mark goes at its head.
- `process_skill_request` in [backend/skills/skill_processor.py](../../../backend/skills/skill_processor.py) calls `create_agent_with_thinking()` then `build_agui_adk_agent()` — `agent_factory_done` mark goes between.
- `preload_memory_tool` is in the agent's tools list ([backend/adk/agent.py:40](../../../backend/adk/agent.py#L40)) — Phase 1 needs to find where ADK actually invokes it (callback hook vs first model call) before placing the `memory_load_done` mark correctly.

### Real baseline (from this session, against `web-researcher` skill, no docs)

| run | ttft (ms) | before_agent (ms) | before_model (ms) | session_idx (ms) |
|---|---|---|---|---|
| 1 | 8286 | 5564 | 7125 | 206 |
| 2 | 9549 | 5946 | 7573 | 163 |
| 3 | 9731 | 5970 | 7523 | 172 |
| 4 | 9365 | 6145 | 7760 | 221 |
| **mean** | **9233** | **5906** | **7495** | **190** |

## Proposed Milestones

### Milestone 1: Finer instrumentation marks (Phase 1)

**Scope:** backend
**Goal:** Make the 7.5s before-model gap attributable to a specific sub-stage. Add three new marks, run a 5-probe baseline, and capture the per-stage breakdown so Milestone 2 can pick the right fix.
**Estimated:** ~120 LOC implementation + ~80 LOC tests = ~200 LOC
**Duration:** ~0.25 day

**Tasks:**
- [ ] Add `STAGE_AGENT_FACTORY_DONE`, `STAGE_RUNNER_SETUP_DONE`, `STAGE_MEMORY_LOAD_DONE` constants to [backend/observability/timing.py](../../../backend/observability/timing.py) (~10 LOC).
- [ ] In [backend/skills/skill_processor.py](../../../backend/skills/skill_processor.py), mark `agent_factory_done` immediately after `create_agent_with_thinking()` returns (~10 LOC).
- [ ] In [backend/adk/agent.py](../../../backend/adk/agent.py) `_composed_before_agent`, mark `runner_setup_done` as the first line so the gap from `agent_factory_done` → `runner_setup_done` measures pure ADK runner setup (~5 LOC).
- [ ] Investigate where `preload_memory_tool` runs (search adk source via `mcp__adk-mcp__search_code` — could be `before_model_callback`, could be a synthetic tool call). Place `memory_load_done` mark accordingly. If it runs as part of `_composed_before_agent` at a known point, mark there. If it's deeper inside ADK's runner, fall back to splitting `_document_injector` into start/end marks and treating "between agent and model" as the memory window. (~30 LOC including the investigation result as inline comment).
- [ ] Update [backend/tests/unit/test_latency_tracker.py](../../../backend/tests/unit/test_latency_tracker.py) — add three constants, add to the full-mode payload assertion (~20 LOC).
- [ ] Update [backend/tests/api_tests/test_stream_skill_ttft.py](../../../backend/tests/api_tests/test_stream_skill_ttft.py) — assert the three new keys appear in the `event="ttft"` log line and in the LATENCY_REPORT payload when probe=1 (~30 LOC).
- [ ] Run `aiplatform skill probe` 5× and paste the breakdown into the M2 task block below (and into the design doc's Phase 1 result section).

**Files to Create/Modify:**
- `backend/observability/timing.py` (modify, +15 LOC)
- `backend/skills/skill_processor.py` (modify, +10 LOC)
- `backend/adk/agent.py` (modify, +10 LOC)
- `backend/adk/callbacks.py` (modify only if memory mark goes here, +20 LOC)
- `backend/tests/unit/test_latency_tracker.py` (modify, +20 LOC)
- `backend/tests/api_tests/test_stream_skill_ttft.py` (modify, +30 LOC)

**Acceptance Criteria:**
- [ ] All three new marks appear in the structured `event="ttft"` log line on every probe.
- [ ] All three new marks appear in the `LATENCY_REPORT` AG-UI Custom event when `?probe=1`.
- [ ] `cd backend && uv run pytest tests/ -q` passes (target: 736+ tests).
- [ ] `cd backend && uv run ruff check .` clean.
- [ ] 5-probe baseline run captured: per-stage means recorded in the M2 task block, with the dominant gap identified.
- [ ] Phase 1 result section appended to [ttft-optimization.md](ttft-optimization.md) with the data.

**Risks:**
- **Memory tool's actual location is uncertain.** ADK's `preload_memory_tool` may be invoked inside the model loop, not via a callback. If we can't place a clean mark there, the data still tells us "the gap exists between agent_done and model_done" — Milestone 2 can still proceed with that information.

---

### Milestone 2: Fix the dominant contributor (Phase 2)

**Scope:** backend
**Goal:** Cut the dominant gap by ≥50%. The fix is one of three pre-sized candidates; the choice is decided by M1's data.
**Estimated:** ~250 LOC implementation + ~150 LOC tests = ~400 LOC (sized for the larger of the three candidates)
**Duration:** ~1 day
**Dependencies:** M1

**Decision tree (decide based on M1 baseline):**

**Candidate A — Lazy/skip `preload_memory_tool`** (likely, sized at ~250 LOC)
- Symptom: `memory_load_done - runner_setup_done` is the largest sub-gap.
- Fix: skip when `MemoryService` is the in-memory variant (no remote round-trip to amortize); for Vertex Memory Bank, defer the load to a post-RUN_FINISHED background task whose result lands on the *next* turn's instructions.
- Files: `backend/adk/agent.py` (drop preload_memory_tool from default tools list when service is in-memory), `backend/adk/callbacks.py` (after_agent_callback that triggers async memory hydration), tests.

**Candidate B — Cache the agent factory per `(skill_id, user_id)`** (sized at ~200 LOC)
- Symptom: `agent_factory_done - session_index_done` is the largest sub-gap.
- Fix: process-lifetime LRU cache keyed on `(skill_id, user_id)`; invalidate on Firestore skill-config snapshot change (existing pattern); per-user keying preserves the permission enforcer closure.
- Files: `backend/adk/agent.py` (cache + invalidation), `backend/skills/skill_processor.py` (use cache), tests for cache hit/miss/invalidation.

**Candidate C — ADK runner setup is the bottleneck** (sized at ~50 LOC of escalation, no fix)
- Symptom: `runner_setup_done - agent_factory_done` is the largest sub-gap and not memory-attributable.
- Fix: this is in ADK framework code, not ours. Ship a written-up benchmark + a `mcp__adk-mcp__search_code` summary of the relevant ADK source, file feedback via `agents-cli` skill, document the finding in this sprint's report. Don't fork ADK.

**Candidate D — Cloud round-trip latency dominates** (laptop ↔ europe-west1, sized at ~150 LOC)
- Symptom: many sub-gaps are each ~100–300ms and they add up. The `aiplatform-cli` baseline was run from a laptop hitting `make dev` which talks to GCS (artifact service), Vertex AI Agent Engine sessions, Vertex Memory Bank, and Firestore — all in europe-west1. A typical local-to-eu-west round-trip is 50–150ms each way. If ADK makes ~20 sequential round-trips per request to set up the runner, that's 1–3s of pure network latency the *deployed* version (Cloud Run in europe-west1) would not pay.
- This is the most likely candidate given the data: production traffic in europe-west1 will be dramatically faster than local-laptop probes, and the "local feels slow" complaint is partly a measurement artifact.
- Fix: run the same probe inside a Cloud Run job in europe-west1 (or `gcloud run deploy` the backend in dev and probe from a Cloud Shell / VM in the same region) to **isolate the network-vs-code component**. If the deployed-from-region p50 is already <3000ms, the fix on local-dev side is informational — document the measurement gap, recommend that latency-sensitive probes run from europe-west1, and only treat the laptop-local p50 as a *worst case*, not a target.
- Phase 1's instrumentation needs no changes for this — the same per-stage marks make the round-trip pattern visible (e.g., if every Firestore-touching stage is +200ms compared to deployed, that's the signature). M3 in this sprint adds a "deployed-region probe" measurement to confirm.

**Most likely outcome (revised):** Phase 1 will show D + (A or B) compounding. The headline target (<3000ms) is for *deployed-region* probes; the *laptop-local* number will improve via the code-only fix (A or B) but stay above 3s due to network. We document both numbers and don't pretend the network is free.

**Tasks (will be filled by the chosen candidate's path):**
- [ ] Apply chosen candidate's diff
- [ ] Add ≥2 regression tests for the new path
- [ ] Verify the fix doesn't break any of the 736+ existing tests
- [ ] Re-run probe 5× to confirm the fix actually moves the dominant-gap metric

**Acceptance Criteria:**
- [ ] Dominant gap from M1 reduced by ≥50% (e.g., if `memory_load_done - runner_setup_done` was 4000ms, after fix should be <2000ms).
- [ ] `cd backend && uv run pytest tests/ -q` passes.
- [ ] `cd backend && uv run ruff check .` clean.
- [ ] If Candidate B (caching): tests cover cache miss, cache hit, invalidation on config change, isolation between users.
- [ ] If Candidate A (memory): tests cover skip-when-in-memory, async-hydration-on-next-turn, fallback when memory load fails.
- [ ] No regressions in existing TTFT instrumentation tests (overhead must still be <5ms p50 — re-verified per Axiom #1 budget).

**Risks:**
- **Memory caching could leak across users.** Candidate B must key on `(skill_id, user_id)` not just `skill_id` because `make_permission_enforcer` closes over `user.email`/`user.domain`. Test asserts a different user gets a different cached agent.
- **ADK behaviour might change between releases.** Memory-tool placement could move. Pin the ADK version (already in `backend/pyproject.toml`); flag this in the implementation report.

---

### Milestone 3: Re-baseline + Implementation Report (Phase 3)

**Scope:** backend (measurement only)
**Goal:** Confirm the fix actually delivered the headline metric. Document before/after numbers in the design doc.
**Estimated:** ~30 LOC of doc updates + scripted measurement
**Duration:** ~0.25 day
**Dependencies:** M2

**Tasks:**
- [ ] Run `aiplatform skill probe <skill> --json` 5× from the **laptop** → record means (the apples-to-apples comparison vs the M0 baseline).
- [ ] Run `aiplatform skill probe --env dev <skill> --json` 5× from a **europe-west1 Cloud Shell / VM** → record means (isolates the network component per Candidate D). If the dev backend isn't yet probable from the region, document the gap and skip — laptop number alone is enough to land the sprint, the regional probe can ship as a follow-up Cloud Build smoke step.
- [ ] Run `scripts/ttft-baseline-summarize.sh` (full vs off) → confirm instrumentation overhead is still <5ms p50 (the new marks added in M1 must not have moved this).
- [ ] Append "Implementation Report" section to [ttft-optimization.md](ttft-optimization.md) with: pre-fix means, post-fix means, % improvement, which candidate was chosen, what the next bottleneck is.
- [ ] If p50 first_model_token still >3000ms: file a follow-up doc `ttft-optimization-2.md` or extend this sprint with M2-bis on the new dominant gap.
- [ ] Move design doc + sprint plan to `docs/design/v6.1.0/implemented/` via `move_to_implemented.sh ttft-optimization`.
- [ ] Single conventional-commit on dev: `feat(ttft): cut first-token from 9.2s to <3s (1.21)`.

**Acceptance Criteria:**
- [ ] p50 first_model_token <3000ms **for deployed-region probes** (currently 9233ms laptop-local).
- [ ] p50 first_model_token reduced by ≥30% on **laptop-local** probes (acknowledging network is a fixed cost the laptop pays; if this floor is the ceiling, document it).
- [ ] p50 before_model_done <500ms for deployed-region probes (currently 7495ms laptop-local).
- [ ] Instrumentation overhead unchanged (<5ms p50 full-vs-off, per the M5 contract from TTFT-INSTR).
- [ ] Implementation Report appended to design doc with concrete numbers.
- [ ] Design doc moved to `implemented/`.
- [ ] If targets unmet: a clear written analysis of the new dominant gap + a follow-up doc filed (don't pretend success).

---

## Day-by-Day Breakdown

### Day 1
- **AM:** M1 (Phase 1). Add three marks, wire them in, update tests. ~0.25 day.
- **AM cont:** Run baseline, identify dominant gap. **Pause point** to adjust M2 candidate selection if reality surprises us.
- **PM:** M2 (Phase 2) — apply chosen candidate fix + tests. ~1 day.

### Day 2 (if needed)
- **AM:** M2 finishing if it spilled over. M3 re-baseline + report.

## Quality Gates

After each milestone:
```bash
cd backend && uv run pytest tests/ -q          # 736+ tests
cd backend && uv run ruff check .              # lint clean
```

After M2:
```bash
cd backend && uv run pytest tests/ -q
# Re-run probe 5× and capture per-stage breakdown into the M3 task block.
```

After M3:
```bash
./scripts/ttft-baseline-summarize.sh           # confirm overhead floor
git status                                      # only the report + move
```

## Success Metrics

- [ ] All backend tests passing (currently 736; M1 adds ~5, M2 adds ~5–10).
- [ ] Lint clean (`ruff check`).
- [ ] p50 first_model_token <3000ms (-67% from 9233ms).
- [ ] p50 before_model_done <500ms (the headline metric).
- [ ] Instrumentation overhead <5ms p50 unchanged (re-verified).
- [ ] Implementation Report with concrete before/after numbers.

## Dependencies

- [ttft-instrumentation](implemented/ttft-instrumentation.md) ✅ — `LatencyTracker` infra.
- `aiplatform skill probe` CLI ✅ — measurement surface.
- `aiplatform-cli` skill ✅ — token mint + curl fallbacks (already used to capture the baseline).

## Open Questions

- Where exactly does `preload_memory_tool` run? M1's investigation answers this. If it's not in `_composed_before_agent`, the `memory_load_done` mark may need to live in a `before_model_callback` slot or in the AG-UI translator's first-token detector.
- Will M2 actually need 1 day or will it land in 2–3 hours? The candidates are pre-sized for the worst case; reality often surprises positively.

## Notes

- **The 7.5s gap is the headline.** If M1's data shows that memory loading is 4s+ of it, that's the smoking gun and Candidate A is a near-certain ship. If memory is fine and the gap is in agent factory, Candidate B is the answer. Candidate C is the unhappy path (escalation to ADK).
- **Don't optimize speculatively.** Phase 1's data is the gating decision for Phase 2 — never apply a fix before knowing the gap it addresses is real.
- **The `aiplatform skill probe` CLI is the measurement instrument.** It already works against the live local backend with auth via `eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"`. No re-derivation needed.
