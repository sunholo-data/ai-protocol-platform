# Sprint Plan — Tool Results as A2UI (7.3)

**Design doc:** [tool-results-as-a2ui.md](tool-results-as-a2ui.md)
**Sprint key:** `TOOL-RESULTS-A2UI`
**Duration:** ~3.5 days (Phase 0 spike already done)
**Scope:** Fullstack (backend-led)
**Model:** B — server-side `result → A2UI` mapping registry, pushed to the
`workspace` surface via an AG-UI `CUSTOM` event (out of the model's context).

## Sprint Summary

**Goal:** Render tool results as declarative **A2UI** on the workspace surface —
no bespoke React per tool — with the PPA compare/extract views as the first
consumers and the 4 bespoke panels deleted. Navigable (Tabs), filterable
(ChoicePicker), with the session's wire hazards (envelope, offload) handled once.

**Foundation already shipped this session:** the two wire-hazard fixes
(`_RENDER_PAYLOAD_TOOLS` offload exemption + `src/lib/toolResult.ts` envelope
unwrap) and the working (bespoke) render prove the data flows end-to-end — this
sprint swaps the *render* from React to A2UI.

**Velocity check:** recent history is ~56 commits / 2 days; not a constraint.
Risk is concentrated in M1's emission path (below), which the spike showed is
mostly pre-wired.

---

## Milestone M1 — Emission path + mapping registry (backend + thin frontend, ~1 day)

**Scope:** backend + small frontend wiring. **Critical path** (M2/M3 depend on it).

**Tasks:**
1. **Verify + wire the CUSTOM-event surface push (de-risk first, ~0.25d).**
   `useSkillAgent.onCustomEvent` and `SurfaceRegistry.appendMessages` both
   exist. Route a CUSTOM event `name: "a2ui_surface"` (value = `{surfaceId,
   messages}`) → `surfaceRegistry.appendMessages(surfaceId, messages)`. Confirm
   `A2UISurfaceMount("workspace")` re-renders. (~30 LOC + 1 test.)
2. **Mapping registry** `backend/adk/a2ui_result_render.py` (~90 LOC + tests):
   `register(result_matcher, transform)`; `render_for(tool_name, typed_result)
   -> list[A2uiMessage] | None`. Matchers key on tool name / result shape.
3. **Emission callback** — extend/adjacent to `_handle_large_output`: when a
   tool's result has a registered mapping, run the transform and emit the
   `a2ui_surface` CUSTOM event to `workspace` (~50 LOC + test). Out of model view.
4. **Generalise `_RENDER_PAYLOAD_TOOLS`** → "has a registered mapping ⇒ never
   offload" (registry-driven; delete the hardcoded set). (~15 LOC + test.)

**Acceptance:**
- A registered dummy mapping emits a CUSTOM `a2ui_surface` event whose A2UI
  renders on the workspace surface in a test harness.
- A tool with a mapping is never offloaded regardless of size (test).
- `make lint` + backend tests green.

**Risk:** emission path is the one unknown — front-loaded as task 1. Fallback if
CUSTOM→surface can't be wired cleanly: reuse the `validated_a2ui_json` result
augmentation (Model A mechanics) for M1 only, revisit. **Est LOC:** ~185 + ~120 test.

---

## Milestone M2 — PPA as A2UI + navigation; delete bespoke (fullstack, ~1.5 days)

**Scope:** fullstack. **Depends on M1.**

**Tasks:**
1. **`PpaComparison → A2UI`** transform (~140 LOC + tests): `Tabs` [Key
   Differences · {left} · {right}]; Key Differences = `List` of severity-badged
   rows (Text + Icon); each contract tab = `Card`+`List` clause table with
   `block_id` citations. `ChoicePicker` (severity) + `ChoicePicker`
   (clause, `filterable`) bound to a `/filter` data-model path.
2. **`PpaClauses → A2UI`** transform (~70 LOC + tests): clause-card surface (so
   extract-only skills like one-ppa-expert get it too). Register both mappings.
3. **Progressive fill:** each `extract`→`extract`→`compare` result emits its
   surface update (append/replace), so the workspace fills step by step.
4. **Delete bespoke** (~ -900 LOC): `PpaWorkspacePanel`, `KeyDifferencesPanel`,
   `ClauseExtractionCard`, `SideBySideDocViewer` + their `__tests__` +
   `ChatShell` `ppaArtifacts`/`usePpaWorkspaceArtifacts` wiring; the Workspace
   tab renders the `workspace` A2UI surface unconditionally. Keep
   `src/lib/toolResult.ts` (Activity inspector) + `types/ppa-clauses.ts` (used
   by the transform).

**Acceptance:**
- Transforms emit schema-valid A2UI v0.9 (validated in tests via the catalog).
- A real compare renders in the workbench as tabbed A2UI (chrome-devtools),
  navigable, with severity/clause filters working; citations present.
- Bespoke panels + `usePpaWorkspaceArtifacts` gone; `npm run quality:check`
  green; no dead imports.

**Risk:** table-as-Column/Row verbosity; ChoicePicker filter semantics. **Est
LOC:** ~+220 / −900 + ~150 test.

---

## Milestone M3 — Interaction + CLI + verify (fullstack, ~1 day)

**Scope:** fullstack. **Depends on M2.**

**Tasks:**
1. **Interaction as A2UI in chat:** diff-row `Button.action` / filter change →
   `surface-action` (fire-and-forget for client filter; `surface-action-run`
   for "explain this diff" → agent elaborates in chat). Reuse the existing loop.
   (~80 LOC + test.)
2. **CLI:** `aiplatform a2ui render <mapping> --result <file.json>` — run a
   mapping, print + schema-validate the A2UI messages headlessly. (~60 LOC + 3
   tests.)
3. **E2E verify** (chrome-devtools skill): compare → progressive A2UI fill →
   tab nav → filter → click a diff → agent explains. Screenshot for the doc.
4. Move design doc + sprint to `implemented/`; update SEQUENCE.md ✅.

**Acceptance:**
- Clicking a diff / changing a filter drives an A2UI surface-action (verified).
- `aiplatform a2ui render` previews the compare A2UI headlessly.
- Full E2E passes in the browser; CI green.

**Est LOC:** ~140 + ~90 test.

---

## Day-by-Day

| Day | Focus |
|-----|-------|
| 1 | M1: emission-path wire + de-risk (task 1) → registry + emission callback + marker |
| 2 | M2: `PpaComparison`/`PpaClauses` transforms + Tabs/filters |
| 3 | M2: progressive fill + delete bespoke panels + quality:check; start M3 interaction |
| 3.5 | M3: CLI verb + chrome-devtools E2E + move-to-implemented |

## Quality Gates

- Per milestone: `cd backend && make lint && make test-fast`, `npm run quality:check:fast`.
- End: `npm run quality:check` (tests + build) + chrome-devtools E2E.
- A2UI outputs validated against the v0.9 catalog in unit tests (no hand-waving).

## Success Metrics

- 0 bespoke PPA render components remain; a new renderable tool needs only a
  registered mapping (no frontend PR).
- Workbench renders A2UI, navigable + filterable, progressive fill intact.
- Wire hazards handled once (registry-driven marker; server-side transform).

## Open / Assumptions

- **Assumes** CUSTOM→surface wiring is clean (M1 task 1 confirms; fallback noted).
- Filters kept client-side via data-model `/filter` binding if A2UI supports it;
  else surface-action round-trip.
- A2UI Basic catalog vocabulary: `.claude/skills/agent-protocols/references/a2ui-v0.9-basic-catalog.md`.
