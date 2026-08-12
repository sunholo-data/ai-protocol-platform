# Workbench Home & Curated Activity

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: ~4 days
**Scope**: Fullstack (backend notability tagging + emitter, frontend Home surface)
**Dependencies**: 7.3 tool-results-as-a2ui ✅, 7.5 workbench-artifacts-model ✅, 7.1 skill-delegation Activity panel ✅, search-sources (grounding `Sources:` block) ✅
**Created**: 2026-07-16
**Last Updated**: 2026-07-16

## Problem Statement

The Workbench (right-hand pane: **Workspace · Document · Activity**) shows the
assistant's non-chat output. Today it under-delivers on "what did the assistant
actually do for me, and where is the useful stuff?":

**Current State:**
- The **Workspace** tab is empty until a skill emits a full A2UI artifact
  ([ChatShell.tsx:497-541](../../../frontend/src/components/chat/ChatShell.tsx#L497)).
  For a plain web-research turn — the exact flow in the screenshots — it stays on
  its empty placeholder even though the turn produced genuinely useful output
  (news items, and now a `Sources:` list). Nothing lands in the Workspace.
- The **Activity** tab ([ActivityPanel.tsx](../../../frontend/src/components/chat/ActivityPanel.tsx))
  is honest but *undifferentiated*: it lists **every** tool call as a row with a
  raw JSON result tree — including pure-plumbing calls the user shouldn't care
  about (`transfer_to_agent` ×2, then `web_search_agent`, per the screenshot).
  The one thing the user asked for — *"what are the sources?"* — is buried in a
  JSON blob or absent from the text entirely.
- The **Workspace-as-index** affordance
  ([WorkbenchIndex.tsx](../../../frontend/src/components/chat/WorkbenchIndex.tsx))
  only appears at **≥2 artifacts** (`showIndex`, [ChatShell.tsx:490](../../../frontend/src/components/chat/ChatShell.tsx#L490))
  and indexes *only* A2UI artifacts — not the open document, not sources, not the
  research digest. With 0–1 artifacts there is no landing/home view at all.
- Net effect: as tabs proliferate (a Result tab per artifact, a Document tab, an
  Activity tab) there is **no single "home" a user returns to** to see what's
  available and jump to it. Auto-focus throws them into the newest tab; getting
  an overview means clicking around.

**Impact:**
- **Who:** every end user (ONE analysts first). Most acute on research/summary
  turns that produce no A2UI artifact but do produce citable, useful output.
- **How significant:** major friction, not a blocker. It is the difference
  between "a chat box with a debug panel" and "a workbench that curates results."
  It also directly undercuts **EARNED TRUST** — sources exist but aren't surfaced.

## Goals

**Primary Goal:** Turn the Workbench into a **curated home**: the Workspace
becomes a persistent "Home" that shows a friendly, curated digest of the
*useful* things the assistant produced (sources, formatted tool outputs, results,
handoffs) and acts as the index to every open tab — while the Activity tab
remains the complete, time-ordered debug feed.

**Success Metrics:**
- 100% of turns that produce a citable answer surface a **Sources** card in the
  Home digest (measured against the grounding `Sources:` path).
- Home digest shows **0** pure-plumbing rows (`transfer_to_agent` and other
  internal sub-agent hops are tier `internal`, never rendered in the digest).
- From Home, every open surface (Result tabs, Document, sources) is reachable in
  **one click** (index rows → focus tab), for any artifact count ≥ 1.
- Zero new bespoke per-tool React: every digest item renders through the generic
  A2UI mount (Axiom #6/#10).

**Non-Goals:**
- Not redesigning the chat transcript or the tab shell chrome
  ([Workbench.tsx](../../../frontend/src/components/chat/Workbench.tsx) stays the
  dumb controlled shell).
- Not removing the Activity tab — it stays as the full, unfiltered feed.
- Not a new user-facing concept: "Home", "digest", "notable" are presentation,
  not a new abstraction users must learn.
- Not multi-channel rendering of the digest in this doc (the *data* is made
  channel-agnostic; Telegram/email rendering is a follow-up — see Open Questions).

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Curated live digest turns a slow tool turn into visible progress ("Searching → 4 sources found → summarising"); Home is a fast, always-present landing instead of hunting tabs. |
| 2 | EARNED TRUST | +1 | Sources become first-class: every citable answer surfaces a **Sources** card. This is the axiom the current gap most violates. |
| 3 | SKILLS, NOT FEATURES | +1 | Notability + digest are skill-agnostic; any skill's notable outputs render the same way with zero per-skill UI. No new user concept. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No change to model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Digest degrades to today's Workspace/Activity; no notable events → Home shows launcher/empty; A2UI render failure → falls to the plain text answer already in chat. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Digest items are A2UI surfaces delivered over the existing AG-UI `A2UI_SURFACE` CUSTOM event; no new format or transport invented. |
| 7 | API FIRST | +1 | Notability is decided backend-side and carried in the event/activity contract, so every channel (and the `/sessions/{id}/activity` API) gets the curated view for free. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The full Activity feed is preserved unchanged; notability is *added* metadata on spans/events — nothing is hidden from tracing, only re-tiered for the user. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access or egress: the digest re-presents content the user already receives in-gate. Confidential-content rule still applies (no new derivative artefacts served outside the auth gate). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Curation logic (what is "notable") lives in the backend; the frontend filters by tier and renders A2UI generically — no client-side business logic. |
| | **Net Score** | **+8** | Threshold: >= +4 |

**Conflict Justifications:**
- None (no axiom scored -1).

## Design

### Overview

Add a backend-owned **notability tier** to the events the frontend already
consumes, emit the *useful* ones as a curated **`digest` A2UI surface**, and
reframe the frontend **Workspace tab as "Home"** — a landing view that stacks
(1) the curated digest ribbon and (2) a broadened index of every open surface
(Results, Document, sources), with the raw **Activity** tab untouched as the
debug feed. Everything rides existing protocols: A2UI surfaces + AG-UI CUSTOM
events + the SurfaceRegistry artifact model — no new format is invented
(Standards check: A2UI is the established rendering protocol here; digest items
are A2UI Basic-catalog messages, not a bespoke schema).

### The notability tier (backend-owned, the load-bearing idea)

Every activity-producing event gets a server-assigned tier:

| Tier | Meaning | Examples | Rendered in |
|------|---------|----------|-------------|
| `internal` | plumbing the user shouldn't see | `transfer_to_agent`, sub-agent hops, memory reads | Activity only |
| `notable` | a useful result worth curating | web search + its sources, document extraction, obligation analysis, a formatted tool output | Home digest **and** Activity |
| `artifact` | a full structured surface | clause cards, comparison, chart, WASM analysis | Home index (a Result tab) **and** Activity |

Tier is decided where the event originates in the backend, never in the browser:
- Tool calls: a small **allow/deny classifier** keyed on tool name + result
  shape in the AG-UI/activity emission path. Default `internal` for the
  ADK-native control verbs (`transfer_to_agent`), `notable` for tools that
  already have a result→A2UI mapping ([a2ui_result_render.py](../../../backend/adk/a2ui_result_render.py)),
  `artifact` for tools that emit a workbench surface.
- Delegations already carry semantic meaning → `notable` (they're the "brought
  in the Contract Expert" story users *do* want).
- Sources: the grounding `Sources:` block (shipped in `search_agent.py`) is
  additionally emitted as a structured **sources item** so the digest can render
  a clean card instead of parsing prose (see below).

### Sources as a first-class digest item

The just-shipped `_append_grounding_sources` callback
([backend/tools/search_agent.py](../../../backend/tools/search_agent.py)) puts a
`Sources:` block into the sub-agent's *text*. Extend it to **also** emit a
`digest` A2UI surface item (`kind: "sources"`) built from the same
`grounding_metadata.grounding_chunks` — one deterministic list of `[title](uri)`.
This means the Home digest and the chat text stay in sync, and "what are the
sources?" is answered *visually* in Home, not re-derived by the model. Covers
both `chunk.web` (google_search) and `chunk.retrieved_context` (Vertex AI Search),
exactly like the text path.

### Frontend Changes

**Reframed component — Workspace tab → "Home":**
`WorkbenchPane` ([ChatShell.tsx:497-541](../../../frontend/src/components/chat/ChatShell.tsx#L497))
Workspace content resolution becomes:
1. A full-artifact `workspace` surface present → render it (unchanged, auto-focus
   still applies) — a single dominant result still takes the stage.
2. **Otherwise → `WorkbenchHome`** (new) whenever there is *anything* to show: a
   curated digest and/or ≥1 openable surface. Replaces the "≥2 artifacts"
   `showIndex` threshold with "≥1 openable thing OR ≥1 digest item".
3. Else launcher / picker / empty state (unchanged).

**New component — `frontend/src/components/chat/WorkbenchHome.tsx`:**
- Top: **Digest ribbon** — renders the `digest` A2UI surface via the generic
  `A2UISurfaceMount surfaceId="digest"` (zero bespoke React; same mount used for
  `workspace`/`activity`). Newest-first, friendly cards: a Sources card, a
  formatted-tool-output card, a "Delegated to X" chip.
- Bottom: **Index** — a broadened `WorkbenchIndex`
  ([WorkbenchIndex.tsx](../../../frontend/src/components/chat/WorkbenchIndex.tsx))
  that lists every openable surface: Result artifacts (`useArtifacts()`), the
  open Document (if `activeTabId` set), and a "Sources" jump. Each row's `onOpen`
  → `onWorkbenchTabChange(surfaceId)` / focus Document, as today.

**Modified — `WorkbenchPane` focus logic** ([ChatShell.tsx:379-433](../../../frontend/src/components/chat/ChatShell.tsx#L379)):
- Keep auto-focus to a *full* artifact/workspace surface (a dominant result
  should still grab focus — repo principle #7).
- Do **not** auto-focus for `notable`/digest-only events; those land in Home,
  which is the default tab, so the user sees them without being yanked.
- `hasContent` ([ChatShell.tsx:443-449](../../../frontend/src/components/chat/ChatShell.tsx#L443))
  gains "≥1 digest item" as a reason the pane opens.

**Modified — `SurfaceRegistry`**
([SurfaceRegistry.tsx](../../../frontend/src/providers/SurfaceRegistry.tsx)):
- Register `digest` as a **session-scoped** default surface (like `workspace`),
  in `DEFAULT_SURFACES` ([SurfaceRegistry.tsx:174-191](../../../frontend/src/providers/SurfaceRegistry.tsx#L174)).
  Append-only within a turn, cleared on session change.
- No change to the artifact model; the digest is a surface, not an artifact tab.

**Modified — `ActivityPanel`** ([ActivityPanel.tsx](../../../frontend/src/components/chat/ActivityPanel.tsx)):
- Consume the tier: render **all** tiers (it's the full feed) but visually
  de-emphasise `internal` rows (collapsed by default under a "N internal steps"
  disclosure) so the debug feed is scannable without losing completeness.

### Backend Changes

**Modified — activity/AG-UI emission:**
- `backend/observability/timing.py` (`LatencyTracker`) — the delegation/stage/
  A2UI emit helpers gain a `notability` field on the emitted event value; add
  `emit_digest_item(kind, surface_messages)` that emits an `A2UI_SURFACE` CUSTOM
  event bound to `surfaceId="digest"` (reuses the existing
  `set_current_tracker`/`stream_agui_events` drain — same wire path as workspace
  surfaces; **must bind the per-request tracker on every SSE endpoint**, per the
  known A2UI-render trap in CLAUDE.md).
- `backend/adk/a2ui_result_render.py` — the result→A2UI emitter tags each emitted
  surface with its tier and, for `notable` results, also emits a compact digest
  card. A **tool-notability map** (name → tier) lives here as the single source
  of truth; `transfer_to_agent` and sub-agent verbs default `internal`.
- `backend/tools/search_agent.py` — extend `_append_grounding_sources` to also
  call `emit_digest_item("sources", …)` from the same grounding chunks.
- `GET /api/sessions/{id}/activity` (`useSessionActivity` source) — include the
  `notability` tier per event so history hydration matches the live tiering.

### CLI Surface

Add to the existing `aitana` tree (per [local-dev-cli.md](../../../docs/design/v6.1.0/local-dev-cli.md)):
- `aitana session digest <session-id>` — dump the curated digest items (kind +
  tier + summary) for a session, so backend notability tagging is verifiable
  without a browser (the current gap: you must open Chrome to see the Workspace).
  ~0.25d: Click subcommand + httpx call to `/sessions/{id}/activity?view=digest`
  + a unit test.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | /api/sessions/{id}/activity | Add `notability` tier per event; optional `?view=digest` filter | No (additive) |
| — | AG-UI `A2UI_SURFACE` CUSTOM event | New reserved `surfaceId: "digest"`; existing schema | No |

### Architecture Diagram

```
Tool/delegation/search event
        │ (backend decides tier: internal | notable | artifact)
        ▼
 LatencyTracker.emit_* ──emit_digest_item()──► A2UI_SURFACE (surfaceId="digest")
        │                                             │ (AG-UI CUSTOM, existing drain)
        │                                             ▼
        │                                   WorkspaceA2uiEventRouter
        │                                             │ registry.appendMessages("digest", …)
        ▼                                             ▼
 /sessions/{id}/activity (tiered)          SurfaceRegistry("digest")
        │                                             │
        ▼                                             ▼
 ActivityPanel (full feed,             WorkbenchHome ──► Digest ribbon (A2UISurfaceMount)
 internal collapsed)                     (Workspace tab)  + Index (useArtifacts + Document + sources)
```

## Implementation Plan

### Phase 1: Notability model + sources digest (~1.5 days)
- [ ] Tool-notability map + tier assignment in `a2ui_result_render.py` (~80 LOC)
- [ ] `LatencyTracker.emit_digest_item` + `notability` on emitted event values (~90 LOC)
- [ ] `search_agent` emits a `sources` digest item from grounding chunks (~50 LOC)
- [ ] `/sessions/{id}/activity` returns tier + `?view=digest` (~60 LOC)
- [ ] Backend unit tests: tiering (transfer_to_agent=internal, web_search=notable), sources-item shape (~120 LOC)

### Phase 2: Workspace → Home + broadened index (~1.5 days)
- [ ] Register `digest` session-scoped surface in `SurfaceRegistry` (~30 LOC)
- [ ] `WorkbenchHome.tsx` — digest ribbon (`A2UISurfaceMount surfaceId="digest"`) + broadened `WorkbenchIndex` (~180 LOC)
- [ ] Rewire `WorkbenchPane` Workspace content + `hasContent` + focus rules (~80 LOC)
- [ ] Frontend tests: Home renders digest + index; ≥1 openable thing shows Home; single dominant artifact still auto-focuses (~150 LOC)

### Phase 3: Activity de-emphasis + CLI + polish (~1 day)
- [ ] ActivityPanel collapses `internal` tier under a disclosure (~60 LOC)
- [ ] `aitana session digest <id>` command + test (~50 LOC)
- [ ] Empty/degraded paths (no notable events, A2UI render failure) render visibly (never-silent) (~40 LOC)

## Migration & Rollout

**Database Migrations:** None. Digest is derived at emit time; history hydration
re-derives tiers from existing `function_call`/`function_response` events.

**Feature Flags:** None — shipped to dev unflagged (user decision 2026-07-16).
Backend tier tagging is additive; the `digest` surface simply doesn't render on
a frontend that hasn't shipped `WorkbenchHome`.

**Rollback Plan:** Git revert the frontend commit (Home is a self-contained
component + a WorkbenchPane content branch); the backend `notability` field is
harmless additive metadata that older frontends ignore.

**Environment Variables:** None.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `WorkbenchHome` renders a digest ribbon from a `digest` surface state
- [ ] Index lists artifacts + open document + sources; each row focuses its tab
- [ ] Workspace shows Home at ≥1 openable thing OR ≥1 digest item; empty otherwise
- [ ] A single dominant `workspace`/artifact surface still auto-focuses (no regression)
- [ ] ActivityPanel collapses `internal` rows; expands on disclosure

### Backend Tests (pytest)
- [ ] Tier classifier: `transfer_to_agent`→internal, web-search→notable, obligation→artifact
- [ ] `emit_digest_item` enqueues an `A2UI_SURFACE` event on `surfaceId="digest"` (per the tracker-bind trap)
- [ ] `sources` digest item built from web + retrieved_context grounding chunks
- [ ] `/sessions/{id}/activity?view=digest` returns only notable/artifact tiers

### Manual / Real-browser (non-negotiable per CLAUDE.md protocol rule)
- [ ] Re-run the Danish-PPA flow: Home shows a **Sources** card; Activity shows the two `transfer_to_agent` hops collapsed under "internal steps"
- [ ] Obligation analysis: Result artifact tab + Home index row; one-click open
- [ ] Verify via a **real AG-UI stream** that the `A2UI_SURFACE` digest event emits and `SurfaceRegistry` registers it (split backend-emit from frontend-render; jsdom green ≠ renders)

## Security Considerations

- **No new data access or egress.** The digest re-presents content the user
  already receives inside the auth gate. It is served over the same
  Firebase-authenticated SSE stream as chat.
- **Confidential-content rule (CLAUDE.md security hard-rule) holds:** the digest
  must not introduce any new *derivative* artefact (thumbnail, preview, snippet)
  served outside the gate. Source links are whatever the grounding tool returned
  (public web URLs or gated `/api/proxy` doc refs) — no new public surface.
- **Notability is not an authorization boundary** — `internal` events are hidden
  for UX, still fully traced (Axiom #8). Never rely on tier for access control.

## Performance Considerations

- Digest items reuse the existing A2UI event path — no new stream, negligible
  extra bytes (a small card per notable event).
- Bundle: `WorkbenchHome` reuses `A2UISurfaceMount` + `WorkbenchIndex`; net new
  JS target < 15KB (Axiom #10 budget).
- History hydration: tier computed from events already fetched — no extra round-trips.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/`)
- [ ] Lint and typecheck clean (`npm run quality:check:fast`, `make lint`)
- [ ] A citable answer always surfaces a **Sources** card in Home
- [ ] Pure-plumbing tool calls (`transfer_to_agent`) never appear in the Home digest
- [ ] Every open surface reachable from Home in one click, at artifact count ≥ 1
- [ ] Real-browser verification of the Danish-PPA + obligation flows
- [ ] Zero bespoke per-tool React added (all digest items via `A2UISurfaceMount`)

## Open Questions

- **OQ1 — Digest authorship:** should each `notable` tool own its digest-card
  A2UI (per-tool mapping), or should a generic "tool result → summary card"
  mapping cover the long tail with per-tool opt-in for richer cards? (Lean:
  generic default + opt-in rich, mirroring 7.3.)
- **OQ2 — Multi-channel:** the digest data is channel-agnostic; do we render it
  on Telegram/email now or defer? (Lean: defer to a follow-up; ship web first,
  keep the contract channel-ready per Axiom #7.)
- **OQ3 — Home vs. dominant result:** ✅ **DECIDED (2026-07-16):** a full
  artifact/workspace surface **auto-focuses** its tab (unchanged repo principle
  #7); Home stays one click away and accrues the digest. Digest-only/`notable`
  events do **not** steal focus.
- **OQ4 — Naming:** ✅ **DECIDED (2026-07-16):** keep the tab labelled
  **"Workspace"**; "Home" is its behaviour, not a new user-facing term.

## Related Documents

- [workbench-artifacts-model.md](../v6.7.0/implemented/workbench-artifacts-model.md) — 7.5, per-result tabs + the existing Workspace index this generalises
- [tool-results-as-a2ui.md](../v6.7.0/implemented/tool-results-as-a2ui.md) — 7.3, result→A2UI mapping the notability tier extends
- [skill-delegation.md](../v6.7.0/implemented/skill-delegation.md) — 7.1, the Activity panel + delegation markers
- [generative-ui-surface.md](../v6.7.0/generative-ui-surface.md) — 7.4, the A2UI surface model
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — the `aitana` CLI the digest command extends
- search-sources (`backend/tools/search_agent.py`) — the grounding `Sources:` block this makes first-class
