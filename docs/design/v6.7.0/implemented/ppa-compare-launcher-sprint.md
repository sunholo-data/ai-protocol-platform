# Sprint Plan: PPA-COMPARE-LAUNCHER — Compare launcher & pre-run config (7.2 M2)

## Summary
Give `one-doc-compare` a zero-typing start path: pick two contracts in the
workbench, optionally narrow clauses/severity, click "Compare" — fired through
the existing surface-action-run loop. Closes the remaining scope of design doc
7.2 (M1 shipped 2026-07-09).

**Duration:** 2 days
**Scope:** Fullstack (frontend-heavy)
**Dependencies:** surface-action-run ✅, a2ui-surface-context ✅, 7.3/7.5 A2UI
result rendering ✅ (supersedes the doc's `PpaWorkspacePanel` references)
**Risk Level:** Low–Medium
**Design Doc:** [ppa-compare-launcher.md](ppa-compare-launcher.md)

## Current Status Analysis

### Recent Velocity
- Last 7 days: 119 commits, ~26k insertions / ~2.2k deletions
- Recent milestone completion rate: ~1 milestone/day sustained (7.3, 7.5, 7.7)
- Estimated capacity: comfortably covers ~1,000 LOC total for this sprint

### Existing Implementation
- `A2UISurfaceMount` already supports `triggerOnAction` (sprint 1.21) — wired
  and tested.
- `POST /api/skills/{id}/sessions/{sid}/surface-action-run` + 8-gate suite
  exists ([a2ui_surface_action_run_routes.py](../../../backend/protocols/a2ui_surface_action_run_routes.py)).
- `extract_ppa_clauses` / `compare_ppa_contracts` do **not** yet accept a
  `clauses` subset or per-call `max_other_clauses` — M1 work is real.
- ChatShell Workspace tab has an empty-state slot (currently
  `SkillExamplesPicker`) to host the launcher.
- `aiplatform skill` command group exists (`probe`, `push`, `diff`, `pull`,
  `compare` slots in cleanly).

### Design drift reconciled (doc predates 7.3/7.5)
1. **No bespoke result panels anymore.** On run completion, A2UI artifact tabs
   appear via the result-render registry; the launcher simply stops rendering
   once `a2ui_surfaces`/artifacts exist for the session. Do NOT resurrect
   `PpaWorkspacePanel` (deleted in 7.3). Stale comment in
   `DocCompareShell.tsx:31` can be fixed in passing.
2. **Model-B toolset gate.** `one-doc-compare` has `a2ui.enabled: false`
   (7.5) — the skill's model cannot emit A2UI itself. The **launcher** is
   bespoke React (as designed). The **config form** therefore defaults to
   bespoke React too (`CompareConfigForm`), unless the out-of-model emitter
   path can host it cheaply — decide in M3, do not re-enable the direct A2UI
   toolset for this skill.

## Proposed Milestones

### Milestone 1: Skill config + tool clause-subset args
**Scope:** backend
**Goal:** The comparison pipeline accepts a typed pre-run config; the skill is
opted into action-triggered runs.
**Estimated:** ~160 LOC implementation + ~140 LOC tests = ~300 LOC
**Duration:** 0.5 day

**Tasks:**
- [ ] Add `clauses: list[str] | None` + `max_other_clauses: int | None` args to
  `extract_ppa_clauses` (subset shrinks the extraction prompt) (~60 LOC)
- [ ] Thread the same subset through `compare_ppa_contracts` →
  `_resolve_clauses` / `_run_comparison` (diff respects subset) (~50 LOC)
- [ ] `one-doc-compare` SKILL.md: `allow_action_triggered_runs: true`,
  `allow_surface_context_writes: true`; document `CompareConfig` +
  `start_compare` payload→tool-arg mapping in instructions (~50 LOC)
- [ ] Re-seed via `platform_seed` (dry-run first)
- [ ] pytest: subset restricts extraction/diff; cap override transparent;
  invalid clause names rejected loudly (~140 LOC)

**Files to Create/Modify:**
- `backend/tools/extract_ppa_clauses.py` (modify)
- `backend/tools/compare_ppa_contracts.py` (modify)
- `backend/skills/templates/one-doc-compare/SKILL.md` (modify)
- `backend/tests/tool_tests/test_extract_ppa_clauses.py`,
  `test_compare_ppa_contracts.py` (extend)

**Acceptance Criteria:**
- [ ] A `clauses=["settlement_type","price_formula"]` call provably extracts /
  diffs only those clauses (assert on prompt content + output shape)
- [ ] `max_other_clauses` per-call override respected, `other_clauses_total`
  still transparent
- [ ] surface-action-run gate suite passes for `one-doc-compare` (gate 8 now
  open)
- [ ] `make lint && make test-fast` clean

**Risks:**
- Clause-subset extraction changes the cached-extraction key space (7.5 added
  result caching) — Mitigation: include the clause subset in the cache key or
  bypass cache for subset runs; add a test either way.

### Milestone 2: Workbench launcher
**Scope:** frontend
**Goal:** Zero-typing path — two checkboxes + "Compare contracts" fires
`start_compare` through surface-action-run.
**Estimated:** ~250 LOC implementation + ~160 LOC tests = ~410 LOC
**Duration:** 0.75 day

**Tasks:**
- [ ] `CompareLauncher.tsx`: doc list (seeds from doc-tabs selection), max-2
  checkbox gating, disabled-until-two button, "Configure…" link (~180 LOC)
- [ ] ChatShell Workspace empty-state slot: launcher renders for opted-in skill
  when no artifacts exist yet; disappears once artifact tabs land (~40 LOC)
- [ ] Fallback: skill not opted in → button composes today's chat intent (~30 LOC)
- [ ] Vitest: two-select gating, payload shape (`{left, right, config}` with
  `doc_id`/`gs_url` duality), doc-tabs seeding, fallback path, launcher hides
  when artifacts exist (~160 LOC)

**Files to Create/Modify:**
- `frontend/src/components/workspace/CompareLauncher.tsx` (new)
- `frontend/src/components/chat/ChatShell.tsx` (modify, empty-state slot)
- `frontend/src/components/shells/DocCompareShell.tsx` (stale comment fix)

**Acceptance Criteria:**
- [ ] Selecting two docs + click → `surface-action-run` POST with both
  identities, **no chat message**
- [ ] Launcher only renders for the opted-in skill + empty workspace
- [ ] Doc list uses the authed `/api/proxy` bucket/doc listing — no public URLs
- [ ] `npm run quality:check:fast` + Vitest clean

**Risks:**
- Empty-state detection now means "no artifacts for this session" (7.5 model),
  not the old `ppaArtifacts.hasContent` — Mitigation: derive from the
  SurfaceRegistry/artifact state that 7.5 introduced; test rehydration case
  (resume with existing artifacts must NOT show the launcher).

### Milestone 3: Config form + CLI + E2E verify
**Scope:** fullstack
**Goal:** Pre-run scoping UI + headless drive path; browser-verified end-to-end.
**Estimated:** ~280 LOC implementation + ~140 LOC tests = ~420 LOC
**Duration:** 0.75 day

**Tasks:**
- [ ] `CompareConfigForm` (clause checklist, severity segmented control, depth
  stepper; defaults all/all/20); submit → `start_compare` with config
  (~150 LOC). Decision point: bespoke React default; A2UI-in-chat only if the
  out-of-model emitter hosts it without re-enabling the Model-B toolset.
- [ ] `aiplatform skill compare --left … --right … [--clauses …] [--severity …]
  [--max-other N]` — token mint, session, POST action, stream AG-UI (~130 LOC)
- [ ] CLI unit test (mocked SSE) + form Vitest (~140 LOC)
- [ ] Browser E2E via `aitana-frontend-verify`: pick two → Compare →
  artifact tabs fill progressively; Configure → material-only → narrowed run;
  non-opted-in skill falls back to chat intent

**Files to Create/Modify:**
- `frontend/src/components/workspace/CompareConfigForm.tsx` (new)
- `cli/aiplatform/commands/skill.py` (extend)
- `cli/tests/test_cli_skill_compare.py` (new)

**Acceptance Criteria:**
- [ ] Submitting a 2-clause subset provably narrows the run (subset visible in
  tool args / trace)
- [ ] `aiplatform skill compare` drives the full path headlessly
- [ ] Browser E2E passes all three scenarios above
- [ ] All design-doc Success Criteria checked off

**Risks:**
- E2E depends on local dev stack + seeded ONE docs — Mitigation: run against
  dev env with the whoami-test user (aiplatform-cli skill token recipe).

## Model Assignment

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-4-8` (high) | Decomposition of a detailed doc + drift reconciliation; interactive |
| Execute M1 (tool args + skill config) | `claude-opus-4-8` (xhigh) | Mechanical, well-specified backend args; cache-key edge is the only subtlety |
| Execute M2 (launcher UI) | `claude-opus-4-8` (xhigh) | UI + wiring on proven `triggerOnAction` path |
| Execute M3 (form + CLI + E2E) | `claude-opus-4-8` (xhigh) | Plumbing + procedural verification |
| Evaluation | `claude-fable-5` + report-everything | Cross-model diversity over Opus-written milestones; judgment criterion: launcher payload cannot widen access beyond the 8 gates |
| Sub-agents (browser verify, test loops) | `claude-sonnet-4-6` | Procedural |

## Day-by-Day Breakdown

### Day 1
- **Focus:** M1 complete + M2 started
- **Checkpoint:** clause-subset args merged with green backend tests; skill
  re-seeded on dev; `CompareLauncher` renders with selection gating

### Day 2
- **Focus:** M2 finish + M3
- **Checkpoint:** browser E2E passes; CLI verb lands; design-doc success
  criteria all checked

## Quality Gates
After each milestone:
```bash
cd frontend && npm run quality:check:fast
cd backend && make lint && make test-fast
```
Pre-push (CI parity): `cd frontend && npm run quality:check` +
`cd backend && make lint && make test-fast`.

## Success Metrics
- [ ] All design-doc Success Criteria (6 boxes) checked
- [ ] No new endpoint, no public exposure of restricted content (launcher
  passes identifiers only)
- [ ] Frontend + backend suites and lint clean

## Open Questions
1. Config form: bespoke React (default, Model-B safe) vs A2UI via out-of-model
   emitter — decide in M3 with a 30-min spike, don't block M1/M2.
2. Launcher doc source: MVP = doc-tabs selection (per doc); library-embed is
   stretch, not planned here.

## Notes
- Design doc sections referencing `PpaWorkspacePanel` / `KeyDifferencesPanel`
  are superseded by 7.3/7.5 — update the doc when moving it to `implemented/`.
- Config persistence (remember last scope) stays deferred per the doc.
