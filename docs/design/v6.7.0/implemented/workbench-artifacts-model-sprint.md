# Sprint Plan — Workbench Artifacts Model (7.5)

**Design doc:** [workbench-artifacts-model.md](workbench-artifacts-model.md)
**Sprint key:** `WORKBENCH-ARTIFACTS`
**Duration:** ~3 days
**Scope:** Fullstack (backend routing + frontend workbench tabs/index)
**Builds on:** 7.3 tool-results-as-a2ui ✅ (result→A2UI registry,
`make_a2ui_result_emitter`, `emit_a2ui_surface`, `SurfaceRegistry`,
`A2UISurfaceMount`, `WorkspaceA2uiEventRouter`, `chat:send`).

## Sprint Summary

**Goal:** Make each tool result a first-class **workbench artifact** with its own
tab (like Documents/Activity) instead of overwriting the single `workspace`
surface; turn the Workspace tab into a **landing index/timeline**; and
**rehydrate** artifacts on session resume (fixing "workbench empty after
refresh"). Reuses 7.3's emission path — the only new wire slot is an optional
`artifact` metadata block on the `A2UI_SURFACE` event.

**Risk** is concentrated in M3's resume-rehydration (re-emitting surfaces from
persisted tool results, outside a live tool call) — front-loaded as its own task.

---

## Milestone M1 — Artifact routing + identity (backend + thin frontend, ~1 day)

**Scope:** backend + small frontend registry wiring. **Critical path** (M2/M3 depend on it).

**Tasks:**
1. **Registry surface strategy** — `adk/a2ui_result_render.py` `register(...)`
   gains `surface` (literal surfaceId OR callable `typed_result → surfaceId`)
   and `artifact_meta` (callable `typed_result → {kind,title,description}`).
   Add `surface_for(tool_name, typed_result)` + `artifact_for(...)`; defaults
   preserve 7.3 (`workspace`, no metadata). (~60 LOC + tests.)
2. **Emitter routes per artifact** — `adk/callbacks.py`
   `make_a2ui_result_emitter` emits to the resolved surface with the artifact
   block; `observability/timing.py` `emit_a2ui_surface` carries optional
   `artifact` in the event value. (~30 LOC + test.)
3. **Frontend registry metadata** — `SurfaceRegistry` stores per-surface
   `artifact` + first-seen `createdAt`; `listArtifacts()` selector (ordered);
   `WorkspaceA2uiEventRouter` passes the `artifact` block through
   `appendMessages`. (~50 LOC + test.)
4. **PPA mappings** — `adk/a2ui_ppa_render.py`: compare → surface
   `ppa_comparison` (+ artifact `{kind:"comparison", title:"Contract Comparison"}`);
   extract → surface `ppa_clauses:{doc_id}` (+ artifact `{kind:"clauses",
   title:_resolve_doc_name}`); **delete `_gather_extractions`** (each extraction
   is now its own artifact surface). (~40 LOC net − ~50 deleted + tests.)

**Acceptance:**
- Registry resolves literal + callable surfaceId + artifact metadata (test).
- Two `extract_ppa_clauses` calls → two distinct `ppa_clauses:{doc_id}` surfaces
  (no accumulation); compare → `ppa_comparison` (test).
- `emit_a2ui_surface` carries the `artifact` block; `listArtifacts()` returns
  ordered metadata (tests).
- backend `make lint` + `test-fast` green; frontend `quality:check:fast` green.

**Est LOC:** ~180 + ~140 test.

---

## Milestone M2 — Dynamic workbench tabs (frontend, ~1 day)

**Scope:** frontend. **Depends on M1.**

**Tasks:**
1. **Derive artifact tabs** — `ChatShell`/`WorkbenchPane` build one tab per
   `listArtifacts()` entry (each → `A2UISurfaceMount(surfaceId)` titled by
   metadata + kind icon), in addition to the fixed Document/Activity tabs.
   (~90 LOC.)
2. **Auto-focus newest + badging** — when a new artifact arrives, focus its tab
   (extends the existing workspace auto-focus); badge others. Reuse the existing
   per-tab badge logic. (~40 LOC.)
3. **Single-artifact parity** — a skill with one artifact shows one tab, no
   index clutter (graceful degradation). (guard + test.)

**Acceptance:**
- `extract → extract → compare` → 3 artifact tabs, none overwritten (Vitest +
  chrome-devtools).
- Newest artifact auto-focuses; others badge.
- Single-artifact skills unchanged; `npm run quality:check` green.

**Est LOC:** ~130 + ~90 test.

---

## Milestone M3 — Workspace index + rehydration + config fix (fullstack, ~1 day)

**Scope:** fullstack. **Depends on M2.**

**Tasks:**
1. **Resume rehydration (de-risk first, ~0.4d)** — on session resume, re-emit
   the artifacts from persisted tool results. Backend: a rehydrate path that
   reads persisted results (`app:emitted:*` / session events) and re-runs the
   registered transforms → `A2UI_SURFACE` events marked `replay:true` (no side
   effects); frontend triggers it alongside the existing session-history GET.
   (~80 LOC + test.)
2. **Workspace index/timeline** — a generic `WorkbenchIndex` in the Workspace
   tab: `listArtifacts()` → rows (kind icon · title · description · relative
   time · "open" → activate the tab). (~90 LOC + test.)
3. **Config fix** — Model-B skills don't get the direct A2UI toolset:
   `adk/agent.py` gates `make_a2ui_toolset` behind an explicit
   `tool_configs.a2ui.agent_emits_a2ui: true` (separate from the Model-B render
   config), so the agent can't misuse `send_a2ui_json_to_client`. Remove the
   PPA explain-prompt "don't render UI" band-aid. (~25 LOC + test.)
4. **E2E + finalize** — chrome-devtools: extract → extract → compare → 3 tabs +
   populated index; refresh → rehydrates; "explain" → chat. Move design doc +
   sprint to `implemented/`; mark SEQUENCE ✅.

**Acceptance:**
- After a page refresh, the workbench artifacts rehydrate (tabs + index return).
- Workspace index lists artifacts with working "open" links.
- A Model-B skill's agent cannot call `send_a2ui_json_to_client` (config-gated);
  explain still replies in chat without the prompt band-aid.
- Full E2E passes; CI green.

**Est LOC:** ~195 + ~110 test.

---

## Day-by-Day

| Day | Focus |
|-----|-------|
| 1 | M1: registry surface strategy + emitter routing + registry metadata + PPA per-artifact mappings (delete `_gather_extractions`) |
| 2 | M2: dynamic artifact tabs + auto-focus/badging + single-artifact parity |
| 3 | M3: rehydration (de-risk first) + workspace index + toolset config gate + chrome-devtools E2E + move-to-implemented |

## Quality Gates

- Per milestone: `cd backend && make lint && make test-fast`, `npm run quality:check:fast`.
- End: `npm run quality:check` (tests + build) + chrome-devtools E2E.
- A2UI outputs stay schema-valid (existing catalog validation in tests).

## Success Metrics

- 3 durable artifact tabs from extract→extract→compare; nothing overwritten.
- Workspace = navigable index/timeline of the session's artifacts.
- Artifacts survive a refresh (rehydration).
- A new multi-artifact skill gets per-result tabs with zero new frontend code.
- `_gather_extractions` deleted; Model-B skills no longer granted the A2UI toolset.

## Open / Assumptions

- **Rehydration trigger** — resume-time GET replay vs first-turn `before_agent`
  replay; M3 task 1 picks the cleaner (both read the same persisted results).
- Artifact tab ordering: by `createdAt` (arrival). Workspace index same order.
- Per-entity surface ids (`ppa_clauses:{doc_id}`) are session-scoped; cleared on
  session change via the existing `clearByPersistence("session-scoped")`.
