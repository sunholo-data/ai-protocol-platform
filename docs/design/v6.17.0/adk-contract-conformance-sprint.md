# Sprint Plan: ADK-CONFORM — ADK Contract Conformance

## Summary
Make the custom layer conform to ADK's control-flow contracts so boundary bugs
stop recurring: pin ADK, add "real ADK flow" regression tests for the seams that
have bitten us, audit the divergent reimplementations, and bake the checklist
into process. Not a rewrite — hardening.

**Duration:** ~2–3 focused days (at current velocity)
**Scope:** Backend (+ small docs/process touch)
**Dependencies:** v6.10.0 unified-adk-handoff ✅, v6.8.0 elicitation ✅ (both shipped)
**Risk Level:** Low (tests, a version pin, docs) — Medium only for the M3 code audit
**Design Doc:** [adk-contract-conformance.md](adk-contract-conformance.md)

## Current Status Analysis

### Recent Velocity
- 397 commits / 14 days — very high; multiple milestones land per day.
- The design-doc "6–8 days" is calendar-conservative; real effort here is ~2–3 focused days.

### Existing Implementation (Phase 1 already largely shipped)
- Confirm-loop fix (`skip_summarization` in `make_elicitation_result`) — committed `26bdd5f`.
- `test_handoff_loop_termination.py` (the real-ADK-flow pattern) — committed.
- `handoff-e2e.sh` CONFIRM flow — committed.
- ADK currently **1.31.1**, resolved from an unpinned range `>=1.28.0,<2.0.0`.

## Proposed Milestones

### Milestone 1: Pin + guard the known seam (Phase 1 remainder)
**Scope:** backend + docs
**Goal:** ADK behavior can't drift silently; the contracts we've been bitten by are written down and runnable as a gate.
**Estimated:** ~40 LOC + ~120 doc lines
**Duration:** ~0.5 day

**Tasks:**
- [ ] Pin `google-adk==1.31.1` (both entries in `backend/pyproject.toml`); `uv lock`; full suite green (~5 LOC)
- [ ] Write `docs/design/v6.17.0/adk-contract-checklist.md` — the 6 known contracts, each: symptom → contract → guard test (~120 lines)
- [ ] Add `make adk-conformance` target grouping the flow-boundary tests (`test_*_termination.py` / `test_*_flow.py`); wire into CLAUDE.md pre-push CI-parity rows (~20 LOC)

**Files:** `backend/pyproject.toml`, `backend/uv.lock`, `Makefile`, `backend/Makefile`, `docs/design/v6.17.0/adk-contract-checklist.md` (new), `CLAUDE.md`

**Acceptance Criteria:**
- [ ] `grep google-adk backend/pyproject.toml` shows exact pins; `uv.lock` unchanged version (1.31.1)
- [ ] `make adk-conformance` runs and passes (green today with the one existing flow test)
- [ ] Checklist committed and linked from the design doc
- [ ] `make test-fast` + `make lint` clean

**Risks:** `uv lock` pulling transitive changes — Mitigation: pin only google-adk, review the lock diff.

### Milestone 2: Flow-boundary tests for bucket B
**Scope:** backend (tests only)
**Goal:** Every "rides ADK internals" seam has a hermetic test that fails on its known contract violation — the gap that let each bug ship.
**Estimated:** ~250 LOC tests
**Duration:** ~1 day

**Tasks:**
- [ ] `test_resilient_llm_flow.py` — real-flow test that `llm_request.model` is rewritten per fallback member across a simulated primary-404 → fallback (guards `resilient_llm_rewrites_model_per_member`) (~90 LOC)
- [ ] `test_document_loader_artifact_roundtrip.py` — real-flow test that the loader's `save_artifact` is awaited and the artifact is retrievable via `load_artifact` (guards the un-awaited-coroutine 404) (~90 LOC)
- [ ] `test_a2ui_emitter_tracker_binding.py` — real-flow/stream test that `A2UI_SURFACE` emits under a bound `LatencyTracker` and *visibly* no-ops without it (guards the recurring render trap) (~70 LOC)

**Files:** three new `backend/tests/unit/test_*.py`

**Acceptance Criteria:**
- [ ] Each test FAILS when its fix is reverted (proven non-tautological, like the loop test)
- [ ] All three run in `test-fast` (hermetic, no GCP) and are picked up by `make adk-conformance`
- [ ] `make lint` clean

**Risks:** the A2UI-emitter tracker ContextVar is the subtlest — Mitigation: reuse the `stream_agui_events` bind/reset pattern; if the stub harness fights the ContextVar, escalate that milestone to Fable 5.

### Milestone 3: Bucket-C conformance audit
**Scope:** backend (audit + possible small code changes)
**Goal:** The divergent reimplementations conform to ADK's contracts; the parked residual and the native-primitive decision are resolved on paper.
**Estimated:** ~80 LOC + a decision doc
**Duration:** ~0.75 day

**Tasks:**
- [ ] Audit the handoff/confirm path against the checklist beyond `skip_summarization`: `long_running_tool_ids`, terminal-event ordering vs `is_final_response`, and whether the parked parallel-transfer case needs the `after_model` all-but-first strip (implement + guard if so) (~50 LOC)
- [ ] Audit `agui.py` turn-finality (empty-run rewrite + terminal dedup) against `is_final_response`/`end_of_agent`; add a real-flow test (~30 LOC)
- [ ] Write the `ToolConfirmation` migrate-vs-conform decision record (revisit trigger: experimental → stable)

**Files:** `backend/adk/agent.py` (maybe), `backend/adk/agui.py` (maybe), new test(s), `docs/design/v6.17.0/toolconfirmation-decision.md` (new)

**Acceptance Criteria:**
- [ ] Each identified divergence is either conformed (with a guard test) or explicitly documented as intentional
- [ ] If the `after_model` strip is implemented, `handoff-e2e` confirm flow still green + a unit guard
- [ ] Decision record committed

**Risks:** the `after_model` strip regressed compound requests once before (see `handoff_multi_transfer_research`) — Mitigation: guard with the real-flow harness AND a live `make handoff-e2e` before shipping; if it re-regresses, keep parked and document.

### Milestone 4: Process integration
**Scope:** docs/process
**Goal:** The next boundary bug is caught at review, not in production.
**Estimated:** ~60 doc lines
**Duration:** ~0.25 day

**Tasks:**
- [ ] Reference `adk-contract-checklist.md` from the design-doc template (Standards-Compliance step) and the PR template
- [ ] Document the ADK-upgrade procedure (bump pin → `make adk-conformance` → live `make handoff-e2e`) in the `platform-deploy` skill

**Files:** `.claude/skills/design-doc-creator/SKILL.md` (or template), PR template, `.claude/skills/platform-deploy/SKILL.md`

**Acceptance Criteria:**
- [ ] Checklist linked from both templates
- [ ] Upgrade procedure documented

## Model Assignment

<!-- Rubric: .claude/skills/sprint-planner/resources/model-assignment.md -->

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-4-8` (high) | Decomposition of an already-detailed design doc; interactive |
| Execute M1 (pin + checklist + make target) | `claude-opus-4-8` (xhigh) | Mechanical + docs; deterministic |
| Execute M2 (bucket-B flow tests) | `claude-opus-4-8` (xhigh) | Test authoring against already-diagnosed bugs; the pattern is proven by `test_handoff_loop_termination.py`. **Escalate the A2UI-emitter test to `claude-fable-5`** only if the ContextVar harness proves subtle |
| Execute M3 (bucket-C audit) | `claude-opus-4-8` (xhigh) | Audit + judgment; **escalate the parallel-transfer `after_model` strip to `claude-fable-5`** (subtle, previously regressed) |
| Execute M4 (process docs) | `claude-opus-4-8` (high) | Docs/process |
| Evaluation | `claude-opus-4-8` + report-everything | Deterministic criteria (tests fail-on-revert, `make` gates) carry most of it |
| Sub-agents (inventory, greps) | `claude-sonnet-4-6` / `claude-haiku-4-5` | Procedural fan-out |

**Default:** run the whole sprint on **Opus 4.8 xhigh** (current session) so it executes without a model switch; escalate the two flagged sub-tasks to Fable 5 only if they prove subtle in practice.

## Day-by-Day Breakdown

### Day 1
- **Focus:** M1 (pin + guard) then start M2
- **Tasks:** pin ADK + `uv lock`; write checklist; `make adk-conformance`; begin the `ResilientLlm` flow test
- **Checkpoint:** `make adk-conformance` green; ADK pinned; ≥1 bucket-B test passing + fails-on-revert

### Day 2
- **Focus:** finish M2, do M3
- **Tasks:** the two remaining bucket-B tests; the handoff/confirm + `agui.py` audits; decision record
- **Checkpoint:** all bucket-B tests green + fail-on-revert; divergences conformed-or-documented

### Day 3 (buffer + M4)
- **Focus:** M4 process; live verification post-deploy
- **Tasks:** checklist into templates; upgrade procedure; `make handoff-e2e ENV=dev` after a deploy
- **Checkpoint:** confirm flow green on deployed dev; original spam prompt yields one card

## Quality Gates

After each milestone:
```bash
cd backend && make lint && make test-fast
make adk-conformance   # the new flow-boundary gate
```

After all milestones (post-deploy):
```bash
make handoff-e2e ENV=dev
```

## Success Metrics
- [ ] `google-adk` pinned; `make adk-conformance` exists and green
- [ ] Every bucket-B seam has a fail-on-revert real-flow test
- [ ] `adk-contract-checklist.md` written + referenced from templates
- [ ] Bucket-C divergences conformed-or-documented; `ToolConfirmation` decision recorded
- [ ] `handoff-e2e` confirm flow green on deployed dev

## Open Questions
- ADK pin target — **resolved:** pin `==1.31.1` (what we run now); upgrade is a separate deliberate PR through the new gate.
- Does the parked parallel-transfer case warrant the `after_model` strip now, or stay parked? — decide in M3 with a live repro.

## Notes
- Phase 1 of the design doc is already shipped (fix + tests + e2e), so M1 here is only the *remainder* (pin/checklist/make target).
- The non-negotiable throughout (per CLAUDE.md + `verify_with_live_stream_pass_rate`): every guard test must FAIL when its fix is reverted, and the confirm path gets a live-stream check post-deploy.
