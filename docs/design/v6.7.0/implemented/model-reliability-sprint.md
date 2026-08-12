# Sprint Plan: MODEL-RELIABILITY — Retries, Fallback Chains, and Stream Survival

## Summary
Implement design doc 7.7: transport hardening (long-stream-class fix), typed model
errors for all three providers, tier-preserving fallback chains with
user-visible degradation, deployment residency enforcement, and
thinking-phase visibility. First sprint run under the Model Assignment rubric.

**Duration:** 5 working days (4.75d estimated + buffer)
**Scope:** Fullstack (backend-heavy; frontend proxy + notices; cloudbuild)
**Dependencies:** none external — all design-doc dependencies already shipped
**Risk Level:** Medium (streaming semantics in M3; two spikes in M1)
**Design Doc:** [model-reliability.md](model-reliability.md)

## Current Status Analysis

### Recent Velocity
- Last 14 days: 107 commits, ~24.5K insertions (~1,750 gross LOC/day incl. docs)
- Sprint needs ~1,500 impl + ~600 test LOC over 5 days (~420/day) — well within capacity
- Recent comparable: 7.5 workbench-artifacts (~3d planned, shipped on time)

### Existing Implementation (survey 2026-07-10, in design doc)
- Retry config only on root agent; skill-level models bare; no fallback anywhere
- Only Gemini `ClientError` → RUN_ERROR; LiteLLM errors die silently
- Proxy SSE path on undici `fetch()` (300s body timeout); no Cloud Run `--timeout`; no heartbeats
- REASONING→ThinkingPanel pipeline wired end-to-end but no model emits thoughts
- **Baseline caveat:** 4 integration tests fail pre-sprint (`Mock object has no
  attribute 'token'` — mock leaking into google-auth path in
  `tests/integration/test_agent.py` + `test_search_agent.py`; fails in
  isolation too, 1.4s runtime = never reaches GCP). Pre-existing, unrelated;
  fixed or properly marked in M0 so quality gates run from a green baseline.
- **Dirty tree caveat:** `ReadAloudButton.tsx` (+test) has uncommitted user
  WIP — must be committed/stashed before execution starts.

## Proposed Milestones

### Milestone 0: Baseline hygiene
**Scope:** backend
**Goal:** Green test baseline so all sprint gates are meaningful
**Estimated:** ~30 LOC (fixture fix or marker) · **Duration:** 0.25d
**Tasks:**
- [ ] Root-cause the mock leak (`Mock` without `.token` in google-auth path); fix fixture or mark the 4 tests `integration`/`slow` so the fast gate excludes them honestly
- [ ] `validate_prerequisites.sh` passes clean

**Acceptance Criteria:**
- [ ] `pytest -m "not slow"` fully green locally

### Milestone 1: Transport + stall hotfix (design Phase 0) — independently shippable
**Scope:** fullstack
**Goal:** A >5-minute healthy stream survives deployed envs; silence never looks like a hang at transport level
**Estimated:** ~315 impl + ~130 test LOC · **Duration:** 1d
**Tasks:**
- [ ] Proxy: `node:http` streaming path for SSE responses in `route.ts` (~120 LOC) + unit test
- [ ] `cloudbuild.yaml` `--timeout=3600` + deploy-skill trap-catalogue note (~5 LOC)
- [ ] SPIKE (timeboxed 1h): `@ag-ui/client` tolerance of SSE comment lines → pick comment vs no-op CUSTOM heartbeat
- [ ] SSE heartbeats in `agui.py` every 20s of silence (~60 LOC) + test
- [ ] Frontend mid-stream inactivity watchdog, 90s, traffic-reset (~50 LOC) + vitest
- [ ] `/api/debug/slow-stream` (dev-only, auth-gated) + `scripts/smoke-long-stream.sh` (~80 LOC)

**Files:** `frontend/src/app/api/proxy/[...path]/route.ts`, `cloudbuild.yaml`, `backend/adk/agui.py`, `frontend/src/hooks/useSkillAgent.ts`, `backend/fast_api_app.py`, `scripts/`
**Acceptance Criteria:**
- [ ] Proxy unit test: SSE response streamed via node:http path; non-SSE unchanged
- [ ] Heartbeat test: silent stream yields keep-alive within 25s
- [ ] Watchdog vitest: fake timers, 90s silence → stalled error; traffic resets
- [ ] Post-deploy: `scripts/smoke-long-stream.sh dev` passes (>5min stream) — the long-stream regression guard
- [ ] All tests + lint clean; **ship to dev at end of M1**

**Risks:** heartbeat spike may force the CUSTOM-event fallback (contained by spike timebox)

### Milestone 2: Typed model errors (design Phase 1)
**Scope:** backend + small frontend
**Goal:** No model error from any provider dies silently — every failure is a typed RUN_ERROR
**Estimated:** ~250 impl + ~120 test LOC · **Duration:** 0.5d
**Tasks:**
- [ ] `backend/adk/model_errors.py`: classifier for Gemini `ClientError`/`ServerError` + LiteLLM exception families (~150 LOC)
- [ ] Recorded fixture for Anthropic 529-via-LiteLLM (verify actual exception class, don't trust mapping tables)
- [ ] `skill_processor.py` catches `ModelTurnError` → `MODEL_UNAVAILABLE`/`MODEL_RATE_LIMITED`/`MODEL_AUTH_FAILED`/`MODEL_REQUEST_INVALID` (~40 LOC)
- [ ] `classifyRunError()` new codes + copy + vitest (~60 LOC)

**Acceptance Criteria:**
- [ ] Table-driven classifier tests: every provider exception → expected class/code/retry_after
- [ ] Regression test: LiteLLM exception now yields RUN_ERROR (was silent death)
- [ ] Vitest: new codes → correct kind/copy/retryable

### Milestone 3: Retry + fallback chains + residency (design Phase 2) — the core
**Scope:** fullstack (backend-heavy)
**Goal:** Provider failure → backoff-retry → tier-preserving fallback with user-visible notice; residency enforced by construction
**Estimated:** ~690 impl + ~300 test LOC · **Duration:** 2d
**Tasks:**
- [ ] `ResilientLlm(BaseLlm)`: backoff w/ full jitter, visible-output gate, event sink, provider cooldown (~250 LOC) — build the scripted-fake-`BaseLlm` test suite FIRST (streaming contract must pass through untouched: partials, thought parts, tool-call chunks)
- [ ] `resolve_model_chain()` + `models.yaml` chains/residency tags/tier variants + `SkillMetadata.fallback` typed block (~150 LOC)
- [ ] `MODEL_RESIDENCY_POLICY` enforcement: eu-strict filtering, pinned-non-EU load error, cloudbuild env vars per env (~60 LOC)
- [ ] `RegionalGemini` location override + `make verify-regions` probe (~80 LOC)
- [ ] `agui.py` drains `MODEL_RETRY`/`MODEL_FALLBACK` (~30 LOC)
- [ ] `FallbackNotice.tsx` + `useSkillAgent` branches (~120 LOC) + vitest

**Acceptance Criteria:**
- [ ] Fake-`BaseLlm` suite: backoff schedule, retry events, fallback order, no-fallback-after-visible-partial, cooldown
- [ ] Residency: eu-strict drops non-EU fallbacks (warning), pinned non-EU primary fails at load; unrestricted resolves full chain
- [ ] Cross-region: injected 429 on europe-west1 → answer from europe-west4, events carry region
- [ ] `make verify-regions` passes for all shipped `{model, location}` pairs
- [ ] FallbackNotice renders from event and survives session-resume replay

**Risks:** ADK streaming-contract corruption (mitigation: fake-BaseLlm suite first, TDD); per-region model availability drift (mitigation: verify-regions in CI)

### Milestone 4: Thinking visibility + tooling + evals (design Phase 3)
**Scope:** fullstack
**Goal:** Thinking phases visible; failure modes testable without breaking real providers; observable counters
**Estimated:** ~265 impl + ~100 test LOC · **Duration:** 1d
**Tasks:**
- [ ] Gemini planner `include_thoughts=True` (~5 LOC) + browser verify (REASONING→ThinkingPanel lights up)
- [ ] Claude smart tier `thinking={"type":"adaptive","display":"summarized"}` via LiteLlm kwargs (~20 LOC) + probe; before/after TTFT + eval check (open question in doc)
- [ ] Silent-phase "Thinking…" STAGE_PROGRESS fallback, throttled (~40 LOC)
- [ ] `FAULT_INJECT_MODEL` (dev-only, prod-guarded) + `make probe-fallback` + `aiplatform skill probe` prints retry/fallback events (~100 LOC)
- [ ] Evalset entry: fault-injected turn still passes rubric via fallback
- [ ] OTel counters `model_retry_total`/`model_fallback_total`/`model_error_total` (~40 LOC)

**Acceptance Criteria:**
- [ ] `make probe-fallback`: injected Anthropic 429 → Gemini answer + `MODEL_FALLBACK` event end-to-end
- [ ] "Thinking…"/ThinkingPanel visible during a real thinking phase (browser-verified)
- [ ] OTel counters visible in logs for a fault-injected run
- [ ] Design doc Success Criteria checklist fully ticked

## Model Assignment

<!-- Rubric: .claude/skills/sprint-planner/resources/model-assignment.md -->

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-4-8` assigned — **executed on `claude-fable-5`** (session model; benign upward mismatch, noted per rubric) | Decomposition of an already-detailed doc |
| Execute M0 + M1 | `claude-opus-4-8` (xhigh) | Mechanical/proven patterns: v5 proxy port, config flags, timers |
| Execute M2 + M3 | `claude-fable-5` | Subtle async-generator streaming wrapper + security-critical residency gate; complete spec; first-shot correctness pays |
| Execute M4 | `claude-opus-4-8` (xhigh) | Plumbing, UI, tooling |
| Evaluation (all rounds) | `claude-opus-4-8` + report-everything | Cross-model check on the Fable-written core; deterministic criteria carry the rest |
| Sub-agents (verify-regions, browser verify, test loops) | `claude-sonnet-4-6` | Procedural |

## Day-by-Day Breakdown

### Day 1 — M0 + M1 (model: opus-4-8)
- **Focus:** Baseline green, then the long-stream-class transport fix
- **Tasks:** mock-leak fix; heartbeat spike; proxy node:http path; cloudbuild timeout; watchdog; slow-stream probe
- **Checkpoint:** M1 shipped to dev; `smoke-long-stream.sh dev` green

### Day 2 — M2, start M3 (model: fable-5)
- **Focus:** Classifier + typed errors (morning); fake-BaseLlm test suite for M3 (afternoon)
- **Checkpoint:** M2 checkpoint green; ResilientLlm test suite written and failing

### Day 3 — M3 core (model: fable-5)
- **Focus:** ResilientLlm until fake-suite green; resolve_model_chain + models.yaml + residency
- **Checkpoint:** backend M3 tests green

### Day 4 — M3 finish (model: fable-5)
- **Focus:** RegionalGemini + verify-regions; AG-UI drain; FallbackNotice frontend
- **Checkpoint:** M3 checkpoint green incl. cross-region + residency tests

### Day 5 — M4 + evaluation (model: opus-4-8)
- **Focus:** Thinking visibility, fault injection, OTel, evalset; then sprint-evaluator
- **Checkpoint:** probe-fallback E2E green; evaluator ≥70

## Success Metrics
- All design-doc Success Criteria ticked (they are the acceptance superset)
- CI green on every milestone commit (`make lint && make test-fast`; `npm run quality:check`)
- M1 deployed to dev by end of Day 1 (independent ship)
- Zero regressions in existing 1,757-passing backend suite

## Assumptions
- Sequential execution (M1↔M2 share `useSkillAgent.ts`; M2→M3 dependency) — no parallel waves
- ReadAloudButton WIP is committed/stashed by user before execution
- `verify-regions` probes run against `your-project-id` (europe-west1/west4)
- Claude-thinking enablement (M4) may be feature-flagged off if TTFT/eval check regresses — it's a quality change riding along, not a reliability requirement
