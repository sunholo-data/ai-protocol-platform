# Workbench Artifacts Model (per-result tabs + workspace index)

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: ~3 days (3 phases)
**Scope**: Fullstack (backend routing + frontend workbench tabs)
**Dependencies**: [tool-results-as-a2ui.md](implemented/tool-results-as-a2ui.md) (7.3 — the result→A2UI
emission path, `SurfaceRegistry`, `A2UISurfaceMount`, the `chat:send` interaction),
[a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md)
**Created**: 2026-07-09
**Last Updated**: 2026-07-09

## Problem Statement

7.3 (tool-results-as-a2ui) made any tool's typed result render as declarative
A2UI on **one** surface — `workspace` — pushed via a CUSTOM event and drawn by
the generic `A2UISurfaceMount`. That shipped and works. But live testing of the
PPA compare flow surfaced a structural limit: **every tool result targets the
same `workspace` surface, so each result overwrites the last.**

Concretely, in an `extract → extract → compare` turn:

- `extract_ppa_clauses` (doc A) renders a clause surface,
- `extract_ppa_clauses` (doc B) **replaces** it,
- `compare_ppa_contracts` **replaces** that with the comparison.

The intermediate artifacts are gone. We partially worked around this by making
the extract transform *accumulate* all active-doc extractions into a tabbed
surface (a 7.3 follow-up), but that's a per-tool hack inside one transform — it
doesn't generalise, and the comparison still clobbers the clause view. A user
who wants the extracted clauses *and* the comparison at once can't.

**Current state:**

- One session-scoped `workspace` surface; last writer wins.
- The workbench has three fixed tabs — **Workspace · Document · Activity** — and
  every A2UI tool result crowds into the single Workspace tab.
- There is no landing view: nothing tells the user "this session produced a
  clause extraction for A, one for B, and a comparison" with a way back to each.
- Accumulation logic lives inside the PPA clause transform (reads
  `app:emitted:ppa_clauses:*` from state) — bespoke, not a platform capability.

**Impact:**

- Multi-artifact skills (extract-then-compare, research-then-summarise, any
  multi-step tool chain) lose all but the last artifact.
- The "workbench" is a single scratchpad, not a durable record of what the
  session produced — undercutting EARNED TRUST for analytical workflows.
- The per-tool accumulation hack will be copy-pasted into every future
  multi-artifact skill — the exact bespoke-per-tool trap 7.3 set out to kill.

## Goals

**Primary Goal:** Each tool result is a **first-class workbench artifact** with
its own tab (like Documents/Activity are their own tabs), and the **Workspace
tab becomes a landing index** — a timeline of the session's active artifacts
(title, description, timestamp, link to the tab) — so nothing is overwritten and
the user can navigate everything the session produced.

**Success Metrics:**

- An `extract → extract → compare` turn yields **3 durable artifacts** (2 clause
  extractions + 1 comparison), each reachable from its own tab and from the
  Workspace index — none overwrites another.
- A **new** multi-artifact skill gets per-result tabs with **zero new frontend
  code** — the backend mapping declares an artifact id; the workbench renders it.
- The per-tool accumulation hack in the PPA clause transform is **deleted** —
  routing/identity is a platform capability, not per-transform logic.
- The Workspace index shows a scannable timeline of artifacts (what · when).

**Success Metrics (cont.):**

- **Artifacts survive a page refresh / session resume.** Today the workbench
  renders are transient CUSTOM events held in the in-memory `SurfaceRegistry`;
  on reload the chat history returns but the workbench is empty. Since the tool
  *results* persist (ADK session events + `app:emitted:*` state), resuming a
  session **rehydrates** the artifacts by re-running the registered transforms —
  no new persistence store, just replay from durable results.

**Non-Goals:**

- Persisting artifacts across *different* sessions (they stay session-scoped,
  like today's workspace surface). Within-session resume/refresh rehydration IS
  in scope (above); cross-session artifact history is a later doc.
- A general drag/rearrange/pin workbench layout manager.
- Changing the A2UI transforms' *content* (7.3 owns that) — this is about
  routing, identity, and the tab/index shell around them.
- Replacing the Document or Activity tabs (they stay; artifact tabs are additive).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Neutral — same render path; a tab switch is instant and cheaper than re-rendering one crowded surface. |
| 2 | EARNED TRUST | +1 | The workbench becomes a durable, navigable record of what the session produced (nothing silently overwritten) — provenance you can return to. |
| 3 | SKILLS, NOT FEATURES | +1 | A multi-artifact skill gets per-result tabs by declaring artifact ids in its mappings — no app code. Generalises the 7.3 premise. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Neutral — no model routing change. |
| 5 | GRACEFUL DEGRADATION | +1 | A skill with one artifact behaves exactly like today (one tab, no index clutter); the index only appears with ≥2 artifacts. Absent/failed artifacts just don't get a tab. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Artifacts ride the existing A2UI surfaces + AG-UI CUSTOM event; identity is a surfaceId (already the protocol's addressing unit). No new format. Deletes bespoke per-tool accumulation. |
| 7 | API FIRST | +1 | Artifact identity/description/timestamp is data over the wire (the CUSTOM event value), inspectable + CLI-previewable — not compiled UI. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The Workspace index *is* an observability surface — a timeline of every artifact the session emitted, with timestamps. |
| 9 | SECURE BY CONSTRUCTION | 0 | Neutral — same data, same authed workspace access gate as 7.3; artifacts are session-scoped and never leave the surface. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The client renders declarative A2UI per artifact + a generic index from artifact metadata; all shaping stays server-side. The tab list is derived from active surfaces, not hardcoded. |

**Net score: +7** (threshold ≥ +4 ✅). No −1 scores.

**Conflict Justifications:** None.

**Standards check (5b):** No new format. Artifact identity reuses the A2UI
`surfaceId` (the protocol's existing addressing unit); delivery reuses the AG-UI
CUSTOM event from 7.3; interaction reuses `chat:send` / the surface-action loop.
The only new wire field is optional artifact **metadata** (title, description,
kind) carried alongside the existing `{surfaceId, messages}` — an additive
extension, not a competing schema.

## Open Question — interaction & rendering model (REDESIGN)

Live testing raised a genuine model question (deferred here as a redesign task,
not resolved in this sprint): **when a user interacts with a surface, how should
the agent respond — plain chat text, an update to that surface's A2UI, or a new
artifact?** Today it's ambiguous and the two rendering models collide:

- **Model B** (this line of work): the *tool result* → A2UI, server-side, out of
  the model's context. The agent never authors UI.
- **Model A** (`send_a2ui_json_to_client`): the *agent* authors A2UI directly. On
  by default (`a2ui.enabled=True`) for every skill.

A skill that renders via Model B *and* has Model A's toolset gets both — and the
agent, seeing the surface state, tried to "update" it with a hallucinated
component when asked to explain a diff. Band-aids (a "reply in prose" prompt;
per-skill `a2ui.enabled=false`) mask it but don't answer the model question. A
proper redesign should define:

- **One declared render path per skill** (or per interaction) — Model A xor
  Model B, not both-by-default; `a2ui.enabled` should probably default *false*.
- **A typed vocabulary for "what an interaction returns"** — `reply-in-chat`
  (text), `update-artifact` (re-emit that surface), or `new-artifact` — chosen by
  the skill/mapping, not inferred by the LLM.
- Whether an agent *should* be able to author/patch a chat-area A2UI surface at
  all, and if so, behind what guardrails (the user's "why can't it update its own
  or a chat A2UI?" — the answer is "it can, but only when that's the declared
  path, with a validated component set").

Tracked as a follow-up redesign; 7.5 ships the artifact model and the interim
`a2ui.enabled=false` for Model-B skills.

## Design

### Overview

Three moves, each building on 7.3:

1. **Per-result artifact surfaces (backend routing).** A result→A2UI mapping
   declares a **target surface identity** instead of always emitting to
   `workspace`. For a stable artifact (e.g. "the comparison") the id is fixed
   (`ppa_comparison`); for a per-entity artifact (e.g. "clauses for doc X") the
   id is derived (`ppa_clauses:{doc_id}`). The emitter (7.3
   `make_a2ui_result_emitter`) pushes the A2UI + artifact metadata to that
   surface via the existing CUSTOM event.

2. **Dynamic workbench tabs (frontend).** The workbench derives its tab list
   from the **active artifact surfaces** in the `SurfaceRegistry` (plus the
   fixed Document/Activity tabs), rather than a single hardcoded Workspace tab.
   Each artifact surface with content → a tab, titled by its metadata, rendered
   by the generic `A2UISurfaceMount`. Auto-focus the newest (repo principle #7).

3. **Workspace = index/timeline (frontend).** The "Workspace" tab stops being a
   render target and becomes a **landing index**: a generic, protocol-driven
   list of the session's artifacts — title, description, kind icon, timestamp,
   and a link that activates the artifact's tab. Built from artifact metadata in
   the registry; no per-skill code.

### Artifact identity + metadata (the one new wire slot)

7.3's CUSTOM event value is `{surfaceId, messages, sourceId}`. Extend it with an
optional `artifact` block:

```jsonc
{
  "surfaceId": "ppa_clauses:doc-abc",      // artifact identity (was always "workspace")
  "sourceId":  "inv-1:extract_ppa_clauses:1",
  "messages":  [ /* A2UI v0.9 */ ],
  "artifact": {                             // NEW, optional — drives tab + index
    "kind":  "clauses",                     // icon / grouping
    "title": "2024_DemoSolar_EDP_Spain.pdf",
    "description": "12 clauses extracted",
    "createdAt": 1720531200000              // stamped by the frontend on arrival
  }
}
```

Backwards compatible: an emission with `surfaceId: "workspace"` and no
`artifact` behaves exactly as 7.3 (renders in the Workspace tab / index-less).

### Durability across resume (rehydration)

**As built (Phase 3):** rather than re-run transforms on resume, the result
emitter **stashes the already-rendered surface** into *session-scoped* state
(`a2ui_surface:{surfaceId}` — deliberately NOT the `app:`-scoped result caches,
because a stable surface id like `ppa_comparison` is not session-unique and an
app-scoped key would leak one session's artifact into another). The existing
session-history GET (`/api/sessions/{id}/messages`) returns these surfaces
(ordered by `createdAt`) in a new optional `a2ui_surfaces` field — no new
endpoint. On resume the frontend replays them into the SurfaceRegistry via the
same `appendMessages` path the live `A2UI_SURFACE` event uses; it's idempotent on
the stashed `sourceId` (the same id the live event carried, so a fresh-chat's
already-live surfaces aren't double-added), and `createdAt` is restored so the
index timeline keeps its order.

This was chosen over "re-run the registered transforms" because stashing the
rendered output is **generic** (any tool whose mapping emits a surface rehydrates
for free — no per-tool resume logic, directly answering "will new tools' tabs
scale?"), **cheaper** (no Firestore doc-name lookups or transform re-execution on
resume), and **doubles as a render cache**. Trade-off: a stashed surface can go
stale if a transform's code changes — it self-heals on the next live emit, the
same contract the `app:emitted:*` result caches use.

### Don't hand Model-B skills the direct A2UI toolset

Live testing surfaced a hazard: a skill that renders via Model B (result→A2UI
mapping, this line of work) *also* gets the agent's direct A2UI toolset
(`send_a2ui_json_to_client`) whenever it has any `tool_configs.a2ui`. Asked to
"explain this difference", the agent tried to *render* A2UI (inventing an
invalid component, reusing surface-context component ids) instead of replying in
prose — a validation failure that corrupted the turn.

**As built (Phase 3):** the gate is the existing `tool_configs.a2ui.enabled`
flag — the Model-B PPA skills (`one-doc-compare`, `one-ppa-expert`) set
`enabled: false` in **source** (durable; was a Firestore band-aid), so the
agent-factory never attaches the direct `send_a2ui_json_to_client` toolset. We
kept `enabled` rather than introducing a separate `agent_emits_a2ui` field: it
already gates exactly this toolset, and its default-`True` preserves the
backwards-compat every workshop demo relies on. Crucially the two rendering
models are already independent — Model-B render runs via the `after_tool_callback`
regardless of `enabled` — so `enabled: false` disables *only* the agent's direct
authoring, leaving the result→A2UI workbench render intact. `one-ppa-expert`'s
stale "call `send_a2ui_json_to_client`" instruction was rewritten to "the
workbench renders itself from the tool result; reply in prose."

### Backend Changes

- **Mapping declares a surface strategy** — `adk/a2ui_result_render.py`
  `register(...)` gains an optional `surface` argument: either a literal
  surfaceId, or a callable `typed_result → surfaceId` (for per-entity ids like
  `ppa_clauses:{doc_id}`), plus an optional `artifact_meta` callable
  `typed_result → {kind,title,description}`. Defaults preserve 7.3 (`workspace`,
  no metadata).
- **Emitter routes per artifact** — `adk/callbacks.py`
  `make_a2ui_result_emitter` reads the mapping's surface + metadata and emits to
  that surface (via `LatencyTracker.emit_a2ui_surface`, extended to carry the
  optional `artifact` block). One tool call → one artifact surface update.
- **PPA mappings** — `compare_ppa_contracts` → surface `ppa_comparison` (stable);
  `extract_ppa_clauses` → surface `ppa_clauses:{doc_id}` (per-doc). **Delete**
  the `_gather_extractions` accumulation hack — each extraction is now its own
  artifact surface; the workbench shows them as sibling tabs. Title resolves via
  the existing `_resolve_doc_name` (filename).
- **Scoping** stays a mapping/emitter concern: a per-entity artifact is only
  (re)emitted for the doc being processed; stale artifacts are cleared on
  session change (existing `clearByPersistence("session-scoped")`).

### Frontend Changes

- **`SurfaceRegistry`** — track optional per-surface `artifact` metadata + first-
  seen timestamp; expose `listArtifacts()` → ordered `[{surfaceId, kind, title,
  description, createdAt}]` for the index + tab derivation. (The registry already
  holds per-surface state; this is a metadata field + a selector.)
- **Workbench tabs become dynamic** — `ChatShell` derives artifact tabs from
  `listArtifacts()` (each → an `A2UISurfaceMount(surfaceId)` tab titled by
  metadata), in addition to the fixed Document/Activity tabs. Auto-focus the
  newest artifact (extends the existing workspace auto-focus). Badging reuses the
  existing per-tab badge logic.
- **Workspace index component** — a small generic `WorkbenchIndex` that renders
  `listArtifacts()` as a timeline (kind icon · title · description · relative
  time · "open" → activate the tab). No A2UI needed — it's chrome over metadata;
  or (stretch) itself an A2UI surface for full protocol-purity.
- **Interactions → chat** stays as shipped in 7.3 (`chat:send` →
  `onChatMessage` → `sendMessage`); artifact surfaces inherit it unchanged.

### API Changes

No new HTTP endpoints. The only wire change is the additive optional `artifact`
block on the existing AG-UI `A2UI_SURFACE` CUSTOM event (7.3). Interaction still
rides `chat:send` / the surface-action loop.

### CLI Surface

Extend the 7.3 `aiplatform a2ui render` verb: when a mapping declares a
per-entity surface / artifact metadata, `render` prints the resolved
`surfaceId` + `artifact` block alongside the A2UI, so the routing is previewable
headlessly (no browser). ~0.1 day. Backlink:
[local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## Implementation Plan

### Phase 1 — Artifact routing + identity (backend, ~1 day)
- `register(..., surface=..., artifact_meta=...)`; emitter routes per artifact;
  `emit_a2ui_surface` carries the optional `artifact` block; frontend
  `SurfaceRegistry` stores metadata + timestamp and exposes `listArtifacts()`.
- PPA: compare → `ppa_comparison`, extract → `ppa_clauses:{doc_id}`; delete
  `_gather_extractions`. Unit tests for routing + metadata; CLI `render` shows
  the resolved surface.

### Phase 2 — Dynamic workbench tabs (frontend, ~1 day)
- Workbench derives artifact tabs from `listArtifacts()`; generic
  `A2UISurfaceMount` per artifact; auto-focus newest; badging. Vitest for tab
  derivation + auto-focus.

### Phase 3 — Workspace index + polish (frontend, ~0.5–1 day)
- `WorkbenchIndex` timeline in the Workspace tab; relative timestamps; open →
  activate tab. chrome-devtools E2E: extract → extract → compare yields 3 tabs +
  an index; clicking an index row opens its tab; "explain this difference" still
  posts to chat.

## Migration & Rollout

**Feature flags:** Default behaviour is unchanged for single-artifact skills
(one tab, no index). Per-result tabs activate when a mapping declares a non-
`workspace` surface — opt-in per mapping, so PPA moves first.

**Rollback:** Point mappings back at `surface="workspace"` (7.3 behaviour). The
data path (emission, unwrap, no-offload) is unchanged.

**Environment Variables:** None new.

## Testing Strategy

### Backend (pytest)
- Mapping surface strategy resolves the right surfaceId (literal + per-entity).
- Emitter emits to the declared surface with the `artifact` block.
- PPA: two extractions → two distinct `ppa_clauses:{doc_id}` surfaces; compare →
  `ppa_comparison`; `_gather_extractions` deleted (no accumulation).

### Frontend (Vitest)
- `SurfaceRegistry.listArtifacts()` ordering + metadata.
- Workbench derives one tab per active artifact; auto-focuses the newest.
- `WorkbenchIndex` renders the timeline; "open" activates the tab.

### Manual (chrome-devtools)
- extract → extract → compare → 3 artifact tabs + a populated Workspace index;
  none overwritten; index links navigate; interactions still reach chat.

## Security Considerations

Same data + access gate as the 7.3 workspace surface — artifacts are
session-scoped, declarative (no code execution), never egress. The index shows
only titles/descriptions the user already sees (e.g. the filenames already in
the Document tabs), behind the authed workbench. No new data access → SECURE BY
CONSTRUCTION neutral.

## Performance Considerations

- Per-artifact surfaces mean smaller DOM per tab (one artifact each) vs. one
  crowded surface — net win for long comparisons.
- The registry holds N artifact surfaces per session (bounded by tools run);
  session-scoped clear reclaims them. Metadata is tiny.

## Success Criteria

- [ ] `extract → extract → compare` produces 3 durable artifact tabs; none overwrites another.
- [ ] The Workspace tab shows an index/timeline of the session's artifacts with working links.
- [ ] A new multi-artifact skill gets per-result tabs by declaring artifact ids — no new frontend code.
- [ ] The PPA `_gather_extractions` accumulation hack is deleted.
- [ ] `aiplatform a2ui render` shows the resolved artifact surface + metadata headlessly.
- [ ] Single-artifact skills are visually unchanged (one tab, no index).
- [ ] After a page refresh / session resume, the workbench artifacts rehydrate (tabs + index return; nothing lost).

## Related Documents

- [tool-results-as-a2ui.md](implemented/tool-results-as-a2ui.md) — 7.3, the foundation (emission path, SurfaceRegistry, chat:send)
- [ppa-compare-launcher.md](ppa-compare-launcher.md) — the PPA flow this reshapes
- [a2ui-surface-context.md](../v6.2.0/implemented/a2ui-surface-context.md) — surface → agent context
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI affordance backlink
- **Session origin (2026-07-09):** emerged from live PPA testing after 7.3
  shipped — extract results were overwritten by compare; user asked for
  per-result tabs + a workspace index/timeline, and for interactions to feed
  chat (the `chat:send` fix, already shipped).

---

## Implementation Report

**Completed**: 2026-07-09 (same day as 7.3; one continuous live-testing session)
**Actual Effort**: ~1 day across 3 milestones (est. 3d)
**Commits**: M1 `6052927`, M2 `a61333b`, tab-UX `0953578`, caching `6c3486a`, M3 `b44398c`

### What Was Built
- **M1 — per-result routing:** mappings declare a target surface (literal or
  callable) + `artifact_meta`; `render_for_emit → RenderResult` retargets each
  message's inner `surfaceId`; emitter routes one tool call → one artifact
  surface; `emit_a2ui_surface` carries the `artifact` block; `_gather_extractions`
  deleted. PPA: `compare → ppa_comparison`, `extract → ppa_clauses:{doc_id}`.
- **M2 — dynamic tabs:** `SurfaceRegistry.useArtifacts()` (reactive, ordered);
  one workbench tab per artifact (tool-named label + tooltip); auto-focus newest;
  hidden tab-strip scrollbar. Single-artifact skills unchanged.
- **M3 — index + rehydration + gate:** `WorkbenchIndex` timeline (≥2 artifacts);
  resume rehydration via emitter session-scoped stash + `a2ui_surfaces` on the
  session-history GET + `RehydrateSurfaces` replay; `a2ui.enabled: false` on the
  two Model-B PPA skills.
- **Bonus (user-requested):** PPA `extract`/`compare` now **read** the app-scoped
  result caches they already wrote — skipping redundant extraction/comparison LLM
  calls on unchanged docs ("we recalculate a lot").

### Deviations from Plan
- **Rehydration:** built as an emitter **stash of the rendered surface** (session
  state) replayed by the frontend — NOT "re-run the transforms on resume." More
  generic (any mapped tool rehydrates free), cheaper, doubles as a render cache.
  See the *Durability across resume* section.
- **Config gate:** used the existing `a2ui.enabled` flag (set `false` on Model-B
  skills) rather than adding a new `agent_emits_a2ui` field — avoids redundancy
  and preserves the default-`True` workshop-demo backwards-compat.

### Files Changed
- **New:** `frontend/src/components/chat/WorkbenchIndex.tsx` (+ test),
  `backend/tests/unit/test_a2ui_surface_stash.py`.
- **Modified (backend):** `adk/a2ui_result_render.py`, `adk/a2ui_ppa_render.py`,
  `adk/callbacks.py`, `adk/agent.py`, `observability/timing.py`,
  `protocols/sessions_route.py`, `tools/extract_ppa_clauses.py`,
  `tools/compare_ppa_contracts.py`, `skills/templates/one-doc-compare/SKILL.md`,
  `skills/templates/one-ppa-expert/SKILL.md`.
- **Modified (frontend):** `providers/SurfaceRegistry.tsx`,
  `components/chat/ChatShell.tsx`, `components/chat/Workbench.tsx`,
  `hooks/useSessionMessages.ts`, `app/globals.css`.

### Verification
- Backend: 1740 pass (incl. surface-stash + endpoint-rehydrate + PPA cache tests);
  `make lint` clean. Frontend: lint + tsc + 379 tests (incl. WorkbenchIndex).
- Live browser refresh round-trip is the one gap: no connected chrome-devtools
  page, and rehydration only stashes for runs after the reload — **to confirm:
  run a fresh extract/compare, then reload the `?session=` URL; the artifact tabs
  + Workspace index should return.**

### Lessons Learned
- **Retarget the surfaceId at emit, not in the transform** — the sharpest bug of
  the sprint (transforms hardcoded `workspace`, emission targeted a per-doc
  surface → client built the wrong SurfaceModel → no tab). Locked with a
  regression test; `make dev-restart` added for the `.next` corruption that
  masked it.
- **Two rendering models must not overlap on one skill.** Model-A
  (`send_a2ui_json_to_client`) + Model-B (result→A2UI) on the same skill made the
  agent hallucinate invalid A2UI. The clean fix was config, not prompt.
- **A write-only cache is a latent cost bug.** Both PPA tools wrote a result
  cache neither read — every turn re-paid the LLM. Adding the read side was the
  systemic fix.

### Follow-up
- **Interaction & rendering model redesign** — see the *Open Question* section
  above (one render path per skill; typed "what an interaction returns"; A2UI-in-
  chat guardrails). Tracked for a future design doc.
- **Stash-update hook (SHIPPED 2026-07-11):** the stash was written only by the
  result emitter at tool time, so client-side edits (e.g. the 7.6 obligation
  artefact's what-if scenario) survived tab re-mounts but not a hard refresh.
  `POST /api/sessions/{id}/surface-data`
  (`backend/protocols/a2ui_surface_data_routes.py`) now lets the session owner
  persist a surface's data-model root into the stash as a `clientDataModel`
  block (same gate stack as `surface-action`, owner-only, 256 KiB cap; the
  client can only update stash entries the emitter created). The messages GET
  materialises the block as one trailing `updateDataModel` message, so the
  existing replay path rehydrates the edit unchanged; `dataModel: null` clears
  it, and a fresh tool emit overwrites it (same self-heal contract as the
  stash). `ObligationArtefactTab` debounce-POSTs scenario changes to it.
