# Sprint Plan — Workbench Home & Curated Activity (11.1)

**Design doc:** [workbench-home-and-curated-activity.md](workbench-home-and-curated-activity.md)
**Sprint ID:** WORKBENCH-HOME
**Duration:** ~4 days (3 milestones)
**Created:** 2026-07-16
**Flag:** `NEXT_PUBLIC_ENABLE_WORKBENCH_HOME` (frontend), backend tier tagging ships dark

## Sprint Summary

**Goal:** The Workspace tab becomes a curated **Home** — a digest ribbon of the
useful things the assistant produced (Sources card, formatted tool outputs,
handoffs) over a one-click index of every open surface — while the Activity tab
stays the full debug feed with `internal` plumbing collapsed. Curation is a
**backend-owned notability tier** (`internal`/`notable`/`artifact`); the frontend
renders by tier via existing A2UI protocols (no bespoke per-tool React).

**Locked decisions (2026-07-16):** OQ3 — a full artifact/workspace surface
**auto-focuses** its tab; digest-only events never steal focus. OQ4 — the tab
stays labelled **"Workspace"** ("Home" is behaviour, not a term).

## Model Assignment

| Stage | Model | Effort | Why |
|-------|-------|--------|-----|
| sprint-planner | `claude-opus-4-8` | high | Decomposition; the design doc holds the hard thinking. |
| M1 execution (backend notability + digest emit) | `claude-opus-4-8` | xhigh | Extends the *proven* A2UI-surface/`LatencyTracker` emission path (7.3/7.5). Subtle in one spot — the per-request tracker-bind trap — but well-understood and covered by an explicit acceptance test, not novel streaming semantics → Opus, not Fable. |
| M2 execution (Workspace→Home frontend) | `claude-opus-4-8` | xhigh | React components + reactivity + focus rules; moderate subtlety (auto-focus regression risk), fully specified. |
| M3 execution (Activity collapse + CLI + polish) | `claude-opus-4-8` | xhigh | Mostly mechanical (disclosure UI, Click subcommand, degraded-path rendering). |
| sprint-evaluator | `claude-opus-4-8` | — | Report-everything prompt; deterministic criteria (tests/lint) dominate. |
| Task sub-agents (browser verify, test loops) | `claude-sonnet-4-6` | — | Procedural verification. |

Current session model is Opus 4.8 → matches the executor assignment; no `/model` switch needed.

## Milestone Breakdown

### M1 — Notability model + sources digest (backend, ~1.5d)

**Scope:** backend. **Depends on:** search-sources ✅, 7.3 a2ui_result_render ✅.

Tasks:
- Tool-notability map (name → tier) + tier assignment in `backend/adk/a2ui_result_render.py`; `transfer_to_agent`/sub-agent verbs default `internal`, result→A2UI-mapped tools `notable`, workbench-surface tools `artifact`. (~80 LOC)
- `LatencyTracker.emit_digest_item(kind, surface_messages)` in `backend/observability/timing.py`, emitting an `A2UI_SURFACE` CUSTOM event on `surfaceId="digest"` via the existing drain; add `notability` to emitted event values. (~90 LOC)
- Extend `_append_grounding_sources` in `backend/tools/search_agent.py` to also emit a `sources` digest item from the same `grounding_chunks`. (~50 LOC)
- `GET /api/sessions/{id}/activity`: add `notability` per event + `?view=digest` filter. (~60 LOC)
- Tests: tier classifier (`transfer_to_agent`=internal, web-search=notable, obligation=artifact); `emit_digest_item` enqueues on `digest` surface (asserts against the tracker-bind trap); sources-item shape from web + retrieved_context chunks; `?view=digest` filtering. (~120 LOC)

**Acceptance:**
- `pytest tests/` green; new tests cover all three tiers + the sources item.
- A scripted run (`aiplatform skill`) of a web-search turn shows an `A2UI_SURFACE` event on `surfaceId="digest"` in the real event stream (backend-emit verified independent of frontend).

**Risks:** the per-request `LatencyTracker` bind on the SSE endpoint (known A2UI-render trap) — mitigated by an explicit test asserting the event reaches the drain, and by reusing the exact bind the chat endpoint already does.

### M2 — Workspace → Home + broadened index (frontend, ~1.5d)

**Scope:** frontend. **Depends on:** M1 (digest surface emitted), 7.5 SurfaceRegistry ✅.

Tasks:
- Register `digest` as a **session-scoped** default surface in `SurfaceRegistry` `DEFAULT_SURFACES`. (~30 LOC)
- `frontend/src/components/chat/WorkbenchHome.tsx`: digest ribbon (`A2UISurfaceMount surfaceId="digest"`) + broadened `WorkbenchIndex` (artifacts + open Document + sources jump). (~180 LOC)
- Rewire `WorkbenchPane` Workspace-content resolution (full artifact → auto-focus, else Home when ≥1 openable thing OR ≥1 digest item, else launcher/picker/empty), `hasContent`, and focus rules (digest-only never steals focus). Keep tab label "Workspace". (~80 LOC)
- Tests: Home renders digest ribbon + index; index rows focus their tab/Document; Home shows at ≥1 openable-or-digest; dominant artifact still auto-focuses (no regression); empty state unchanged. (~150 LOC)

**Acceptance:**
- `npm run test:run` + `npm run quality:check:fast` green.
- Behind `NEXT_PUBLIC_ENABLE_WORKBENCH_HOME`; flag off → today's behaviour exactly.

**Risks:** auto-focus regression — covered by an explicit "dominant artifact auto-focuses" test.

### M3 — Activity de-emphasis + CLI + polish (fullstack, ~1d)

**Scope:** fullstack. **Depends on:** M1, M2.

Tasks:
- `ActivityPanel`: collapse `internal`-tier rows under an "N internal steps" disclosure; expand on click. (~60 LOC + tests)
- `aitana session digest <session-id>` CLI command → `/sessions/{id}/activity?view=digest`. (~50 LOC + test)
- Degraded/empty paths render visibly (no notable events, A2UI render failure) — never-silent. (~40 LOC)

**Acceptance:**
- Activity shows the two `transfer_to_agent` hops collapsed; expands to full detail.
- `aitana session digest <id>` prints the curated items for a live session.
- **Real-browser verification** (per CLAUDE.md): Danish-PPA flow → Home shows a Sources card, Activity collapses plumbing; obligation flow → Result tab + Home index one-click open.

## Success Metrics

- Backend: `make lint` + `pytest tests/` green; tier + digest + sources tests added.
- Frontend: `npm run quality:check` green; Home + index + focus-regression tests added.
- Real-browser: Sources card appears on a citable answer; zero `transfer_to_agent` rows in the Home digest; every open surface one-click from Home.
- Net axiom +8 preserved (no bespoke per-tool React; digest via `A2UISurfaceMount`).

## Quality Gates (per milestone)

- Backend: `cd backend && make lint && make test-fast`
- Frontend: `cd frontend && npm run quality:check`
- After M3: real-browser verification via `aitana-frontend-verify` (chrome-devtools MCP).
