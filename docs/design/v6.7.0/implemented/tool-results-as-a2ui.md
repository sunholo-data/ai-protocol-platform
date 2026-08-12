# Tool Results as A2UI (retire bespoke workbench panels)

**Status**: Implemented — M1–M3 code shipped 2026-07-09 (commits d519701,
1a930df, 846c4c1); **browser E2E is the pending verification step**. See
[Phase 2 & 3 — Outcomes](#phase-2--3--outcomes-2026-07-09).
**Priority**: P1 (Medium)
**Estimated**: 4 days
**Scope**: Fullstack
**Dependencies**: [ppa-compare-launcher.md](../ppa-compare-launcher.md), [a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md), the shipped PPA render fixes (unwrap + offload exemption), `adk/a2ui.py` (`SurfaceAwareA2uiToolset`)
**Created**: 2026-07-09
**Last Updated**: 2026-07-09

## Problem Statement

Getting a tool's structured result to render in the workbench took an
end-to-end debugging marathon and a **bespoke React component per tool**
(`PpaWorkspacePanel` → `KeyDifferencesPanel` + `ClauseExtractionCard`), plus a
hand-written `usePpaWorkspaceArtifacts` hook that parses the tool-call stream by
tool name. That path is fragile and doesn't generalise.

**Current State (what we learned the hard way):**

- The workbench render is **bespoke React** keyed off tool name
  (`compare_ppa_contracts` / `extract_ppa_clauses`). Every new renderable tool
  needs a new component + hook branch.
- Tool results arrive **double-wrapped** on the wire: `{"result": "{\"doc_id\":
  …}"}` (a JSON string nested under `result`). Anything reading them must
  `deepUnwrap` (now centralised in `src/lib/toolResult.ts`).
- Large tool results (>50K chars) are **offloaded to an artifact** by
  `_handle_large_output`, replacing the body with a pointer string — which
  silently strands any UI that needs the data. We patched this with a
  `_RENDER_PAYLOAD_TOOLS` allow-list, but that's a per-tool escape hatch.
- The rendered panel is a **single very long, unnavigable page** (a diff panel
  stacked above two full clause tables) — no tabs, sections, or in-page nav.
- A2UI already exists as the declarative render path (`A2UISurfaceMount` on the
  workspace surface, `SurfaceAwareA2uiToolset`) but tool *results* don't use it
  — only the agent's explicit `send_a2ui_json_to_client` calls do.

**Impact:**

- Bespoke-per-tool doesn't scale: the platform's premise is skills+tools, and
  every tool that wants a workbench view currently forks a React component.
- The two wire hazards (envelope + offload) will re-bite every future
  renderable tool until the pattern is systematised.
- The flagship PPA demo's workbench is hard to read — undermines "earned trust".

## Goals

**Primary Goal:** Any tool can render its result as **declarative A2UI on the
workspace surface** (and drive user interaction as A2UI in chat) — no bespoke
React per tool — with the PPA panels refactored onto this path as the first
consumer.

**Success Metrics:**

- A new renderable tool ships a workbench view with **zero new React
  components** — it emits A2UI (or maps its typed result to A2UI catalog
  components) and the existing `A2UISurfaceMount` renders it.
- The PPA compare + extract views are produced as A2UI; `PpaWorkspacePanel`,
  `KeyDifferencesPanel`, `ClauseExtractionCard`, and `usePpaWorkspaceArtifacts`
  are deleted.
- The workbench view is **navigable**: sections/tabs (Differences · Contract A ·
  Contract B), in-page scroll, and severity/clause filters carried over.
- The wire hazards (envelope unwrap, large-output offload) are handled **once**
  in the shared path, not per tool.

**Non-Goals:**

- Replacing the Activity panel's raw tool-result inspector (stays as-is —
  already uses `toolResult.ts`).
- A visual A2UI builder / WYSIWYG.
- Changing the comparison/extraction *tools' logic* — this is about how their
  results reach the UI.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral — same data, declarative render. Navigation (tabs) improves *perceived* speed of a long page. |
| 2 | EARNED TRUST | +1 | A navigable, sectioned view with citations is far more readable than one long dump; provenance preserved. |
| 3 | SKILLS, NOT FEATURES | +1 | Tools gain UI without app-code — the whole point. A fork adds a renderable tool with no frontend PR. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Replaces bespoke React with **A2UI** (the declared v0.9 UI protocol) — the strongest possible alignment; retires custom components. |
| 7 | API FIRST | +1 | The render is data (A2UI JSON) over the wire, not compiled UI; inspectable, testable, CLI-renderable. |
| 8 | OBSERVABLE BY DEFAULT | +1 | One shared path means the envelope/offload hazards are logged + handled once; the `[ppa-workbench]` trace generalises. |
| 9 | SECURE BY CONSTRUCTION | 0 | Neutral — same data, same access gates as today's workspace surface. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client renders declarative A2UI; all shaping is server-side/protocol. Removes fat client components. |

**Net score: +6** (threshold ≥ +4 ✅). No −1 scores.

**Conflict Justifications:** None.

## Design

### Overview

**Decided (Phase 0 spike, 2026-07-09): Model B — server-side result→A2UI
mapping, kept out of the model's context.** The tool returns only its typed JSON
(unchanged); a **mapping layer** transforms that typed result into A2UI surface
messages, which are pushed to the `workspace` surface via a non-model-visible
channel and rendered by the existing `A2UISurfaceMount` / `A2UIRenderer`. No
per-tool React; the model's context is not bloated with the A2UI.

Model A (tool emits A2UI on its result, reusing the `validated_a2ui_json` +
`surface_id` augmentation) was **rejected**: it puts tens of KB of A2UI the
model doesn't need into the model-visible tool result, re-courting the >50K
`_handle_large_output` offload we just fixed, and costs tokens on every turn.

The mapping layer is a small registry keyed by result shape/`$schema`
(`PpaComparison` → the tabbed compare surface; `PpaClauses` → a clause-card
surface), each a pure `typed result → A2UI messages` function — server-side,
unit-testable, CLI-previewable.

Both must sit behind the shared **wire-hazard handling** (learned this session):
- **Unwrap** the `{result: …}` / double-encoded envelope once. Server-side the
  transform reads the typed object directly; the client keeps `toolResult.ts`
  for the Activity inspector.
- **Never offload** a render payload: generalise `_RENDER_PAYLOAD_TOOLS` into a
  first-class "this tool's output feeds a UI mapping" marker (registered
  alongside the mapping), not a hardcoded name set in `callbacks.py`.

**Emission path (to verify first in Phase 1):** push the surface messages as an
AG-UI `CUSTOM` event (`updateComponents`/`updateDataModel` for `surfaceId:
workspace`) — the same drain the delegation signals use — so the A2UI never
enters the model's context. Confirm `A2UISurfaceMount` consumes a
CUSTOM-delivered surface update (vs only tool-result-delivered).

### Navigation UX (the "long unreadable page" fix)

Render the comparison as A2UI with structure, not a stack:
- **Section tabs** (A2UI `Tabs` — confirmed in Basic catalog): `Key Differences`
  · `{Contract A}` · `{Contract B}`, one visible at a time.
- **Key Differences** stays the default landing section; carries the
  severity + clause **filters** already built (as A2UI controls driving
  surface-action → client-side filter, or A2UI data-model state).
- **Per-contract sections** are the clause tables, collapsible by clause group,
  with the `block_id` citations preserved.
- In-page scroll within the workspace pane (already `overflow-auto`).

### Frontend Changes

**Deleted:** `PpaWorkspacePanel.tsx`, `KeyDifferencesPanel.tsx`,
`ClauseExtractionCard.tsx`, `usePpaWorkspaceArtifacts`, and the
`ChatShell` `ppaArtifacts` wiring — replaced by the generic
`A2UISurfaceMount` workspace render.

**Kept/served:** `src/lib/toolResult.ts` (envelope unwrap) — now used by the
generic tool-result→A2UI path, not just PPA. The `[ppa-workbench]` diagnostic
generalises to a `[a2ui-result]` trace.

**Modified:** `ChatShell` Workspace tab — the native `ppaArtifacts` branch is
removed; the tab renders the `workspace` A2UI surface (already wired) whenever a
tool has emitted one.

### Backend Changes (Model B)

- **Result→A2UI mapping registry** — `adk/a2ui_result_render.py` (new): a
  registry of pure transforms keyed by result shape/`$schema`
  (`PpaComparison` → tabbed compare surface; `PpaClauses` → clause-card
  surface). Each `typed result → list[A2uiMessage]`. Server-side, unit-tested.
- **Emission path** — an `after_tool_callback` (or extension of the existing
  offload callback) that, when a tool's typed result matches a registered
  mapping, runs the transform and pushes the A2UI to the `workspace` surface as
  an AG-UI `CUSTOM` event (out of the model's context). Reuses the CUSTOM-event
  drain used by delegation signals.
- **Generalise `_RENDER_PAYLOAD_TOOLS`** — registering a mapping implicitly marks
  the tool's output as a UI payload; the offload exemption reads the registry
  rather than a hardcoded name set in `callbacks.py`.
- `compare_ppa_contracts` / `extract_ppa_clauses` register the first two
  mappings; their tool code is unchanged (still return typed JSON).

### API Changes

No new HTTP endpoints. A2UI surface messages ride the existing AG-UI `CUSTOM`
event stream (Model B, out of model context); interaction uses the
surface-action loop.

### CLI Surface

- `aiplatform a2ui render <mapping> --result <file.json>` — run a registered
  result→A2UI transform and print/validate the surface messages headlessly
  (no browser). ~0.25 day. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## Implementation Plan

### Phase 0: Spike — Model A vs B ✅ DONE (2026-07-09)
- Catalog enumerated (Tabs/Card/List/ChoicePicker sufficient — captured in the
  `agent-protocols` skill), bespoke panels audited (4 to retire), **Model B
  chosen** (see Overview). Only residual unknown: the CUSTOM-event emission path
  (first task of Phase 1).

### Phase 1: Shared plumbing (~1 day)
- First-class "UI payload" tool marker → offload exemption (retire the
  hardcoded `_RENDER_PAYLOAD_TOOLS`).
- Generic tool-result→workspace-A2UI path in `ChatShell` (render the workspace
  surface; drop `ppaArtifacts`). `toolResult.ts` unwrap in the shared path.

### Phase 2: PPA as A2UI + navigation (~1.5 days)
- Emit (or map) `compare_ppa_contracts` + `extract_ppa_clauses` as A2UI with
  the tabbed/sectioned navigation + severity/clause filters.
- Delete the bespoke panels + hook + tests.

### Phase 3: Interaction + CLI + polish (~1 day)
- Diff-row click / filter changes as A2UI surface-actions in chat (per the
  "user interaction → A2UI in chat" principle).
- `aiplatform a2ui render` verb + test. Browser verify (chrome-devtools).

## Migration & Rollout

**Feature flags:** Ship behind a per-skill/tool toggle so PPA moves to A2UI
first; other renderable tools follow. Bespoke panels deleted only once the A2UI
path is verified at parity.

**Rollback Plan:** Re-point the Workspace tab at the (git-history) bespoke path
if the A2UI render regresses — but prefer forward-fix since the data path
(unwrap + no-offload) is unchanged.

**Environment Variables:** None new.

## Testing Strategy

### Frontend (Vitest)
- Shared unwrap already tested (`toolResult`); add: workspace tab renders the
  A2UI surface from a tool result; navigation tabs switch sections; filters
  work.

### Backend (pytest)
- Tool emits valid A2UI v0.9 for a sample comparison/extraction (schema-valid).
- "UI payload" tools are never offloaded (generalised exemption test).

### Manual
- chrome-devtools: run a compare, confirm A2UI workbench renders, is navigable,
  filters work, citations click through.

## Security Considerations

Same data + access gates as the current workspace surface. A2UI is declarative
(no code execution). No new egress. Restricted-content rule unchanged — the
render stays behind the authed workspace.

## Performance Considerations

- Emitting A2UI adds payload vs. the raw typed JSON, but replaces a
  `load_artifacts` round-trip and bespoke hydration. Watch token cost of
  Model A (A2UI in the tool result the model sees) — a reason Model B (map
  outside the model's view) may win for large results.
- Navigation (one section visible) reduces DOM for long comparisons.

## Success Criteria

- [x] A comparison renders as **A2UI** on the workspace surface — no bespoke PPA React in the tree. *(unit-verified against the real catalog; browser E2E pending)*
- [x] Adding a new renderable tool needs **zero** new frontend components — just a registered mapping in `adk/a2ui_result_render.py`.
- [x] Workbench view is navigable (top-level Tabs + nested severity Tabs + scroll) and keeps citations. **Severity filter = nested Tabs** (see finding below); **clause filter deferred** (see below).
- [x] `PpaWorkspacePanel` / `KeyDifferencesPanel` / `ClauseExtractionCard` / `usePpaWorkspaceArtifacts` deleted (+ orphaned `types/ppa-clauses.ts` + dead `SideBySideDocViewer` cluster — 12 files / ~1,600 LOC).
- [x] Envelope-unwrap + no-offload handled once in the shared path (registry-driven `is_render_payload_tool` marker, not a name list).
- [x] `aiplatform a2ui render` previews a tool's A2UI headlessly (thin wrapper → `python -m adk.a2ui_render_preview` + `make a2ui-render`).
- [ ] **Browser E2E** (chrome-devtools): compare → progressive fill → tab nav → severity filter → diff-click → agent explains. *Pending — user-run.*

## Phase 0 Spike — outcomes (2026-07-09)

- **Catalog is sufficient** (verified against `a2ui==0.9.x`, now captured in the
  `agent-protocols` skill → `references/a2ui-v0.9-basic-catalog.md`). Basic has
  **`Tabs`** (section nav), `Card`, `List`, `Column`/`Row`, `Text`, `Button`,
  and **`ChoicePicker`** (severity + clause filters) + `CheckBox`/`Slider`. No
  `Table`/`Accordion` — tables become `Column` of `Row`s. **Open question about
  navigation resolved: it's achievable in A2UI.**
- **Bespoke-panel audit:** the retire-to-A2UI set is `PpaWorkspacePanel`,
  `KeyDifferencesPanel`, `ClauseExtractionCard` (live workspace path) +
  `SideBySideDocViewer` (only in the retired `DocCompareShell`/`WorkspaceShell`
  path — already dead on the production ChatShell). Everything else
  (`MessageBubble`, `ActivityPanel`, `MCPAppToolCallRouter`) is protocol
  plumbing, not a per-tool render.
- **Model decision → Model B (result→A2UI mapping), recommended.** Model A (tool
  emits A2UI on its result) re-bloats the model's context with tens of KB of
  A2UI it doesn't need (it already has the typed result to narrate) and
  re-courts the >50K offload. Model B keeps the transform out of the model's
  view: a server-side `PpaComparison`/`PpaClauses` → A2UI transform (testable),
  rendered by the existing `A2UISurfaceMount` + `A2UIRenderer`.

## Open Questions

1. **Emission path** — ✅ RESOLVED (M1). A CUSTOM `A2UI_SURFACE` event (value
   `{surfaceId, messages, sourceId}`) enqueued on the per-request tracker's
   pending-events queue (the drain the delegation signals already use), routed
   by `useSkillAgent.onCustomEvent` → `SurfaceRegistry.appendMessages`. Kept
   **mode-independent** (not gated by `AITANA_TTFT_MODE` — it's a product
   feature, not instrumentation). Each emit gets a unique `sourceId`
   (`{invocation_id}:{tool}:{seq}`) so progressive fill isn't deduped.
2. **Filters as A2UI** — ✅ RESOLVED (M2/M3), with a real constraint (below).

## Phase 2 & 3 — Outcomes (2026-07-09)

### Finding 1 — reactive client-side list filtering is NOT expressible in stock A2UI v0.9

Verified against `@a2ui/web_core` + `@a2ui/react` 0.9 in this repo: there is
**no per-component visibility/conditional prop**, the expression functions are
math/boolean/string only (**no `filter`/`map`**), and a List's dynamic-child
template binds a **static** data path (`{componentId, path}`) — so the only way
to change which rows show is to rewrite the underlying data-model array
(`updateDataModel`), i.e. a round-trip. The bespoke panel's zero-latency
checkbox filter has no stock-A2UI equivalent.

**Resolution:** the **severity filter is native nested `Tabs`**
(All / Material / Moderate / Cosmetic, server-partitioned; each diff Card built
once and shared-ref'd across the "All" tab and its severity tab). Tab switching
is pure-client, zero-latency, no bespoke React. The **multi-select clause
filter is deferred** — a working one needs a *retained-comparison re-render
path* (keep the comparison client-side + a filter-aware re-emit); a
fire-and-forget ChoicePicker that visibly changes but doesn't filter the list
would mislead. Severity tabs cover the primary filtering need.

### Finding 2 — interaction via per-action routing (`run:` convention)

`A2UISurfaceMount` now routes **per action**: a `run:`-prefixed action name
drives a full agent turn (`surface-action-run` via `useActionDrivenAgent`) even
on a default fire-and-forget mount, so one surface mixes client actions and
agent-run actions without being all-or-nothing. Each diff Card carries an
"Explain this difference" Button (`run:explain_diff` + clause context); clicking
it makes the agent elaborate **in chat**. Requires the skill to opt in via
`tool_configs.a2ui.allow_action_triggered_runs: true` (backend 403s otherwise,
surface stays put) — **enable this on the PPA/ONE skill before the E2E**.

### Finding 3 — a render-loop bug, averted

Adding `surfaceRegistry` to `useSkillAgent`'s subscription-effect deps caused a
`setMessages(newArray)` render loop (OOM under CI's vitest singleFork pool). The
registry must be read as a stable ref, not a dep — kept `[agent]` deps with a
documented `eslint-disable`.

### CLI / headless preview

The `aiplatform` CLI is an isolated `uv tool` (can't import the backend or
`a2ui`), so the render+validate logic lives in the backend
(`adk/a2ui_render_preview.py` + `make a2ui-render`), and `aiplatform a2ui
render <mapping> --result f.json` / `--list` is a thin monorepo-dev wrapper that
shells to it. Verified E2E.

### Bookkeeping left for the maintainer

`SEQUENCE.md`'s 7.3 row still says "Planned" and the design doc + sprint plan
have not been physically moved to `implemented/` — left undone deliberately
because `SEQUENCE.md` carried **uncommitted 7.4 (`generative-ui-surface`) WIP**
at finalization time, and moving files would break its links. Update the 7.3
status → shipped and move both docs when committing the 7.4 work.

## Related Documents

- [ppa-compare-launcher.md](../ppa-compare-launcher.md) — M2 launcher; shares the surface-action loop
- [a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — surface → agent context
- [action-triggered-agent-turn.md](../v6.1.0/implemented/action-triggered-agent-turn.md) — surface-action-run (interaction path)
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance backlink
- **Session learnings (2026-07-09):** the render was blocked by (a) large-output offload stranding the 50,672-char comparison (`_RENDER_PAYLOAD_TOOLS` exemption) and (b) the `{result:…}` double-encoded envelope hiding `doc_id`/`differences` (`src/lib/toolResult.ts`). Both are the wire hazards this doc systematises. Memory: `gotcha_large_tool_output_offload`.

---

## Implementation Report

**Completed**: 2026-07-09
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
