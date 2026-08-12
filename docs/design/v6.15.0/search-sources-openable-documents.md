# Search Sources → Openable Documents (+ Metadata Filters)

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: Phase A ~1 day, Phase B ~2–3 days
**Scope**: Fullstack
**Dependencies**: `ai_search` enterprise search wiring (docs/ops/adk-search-tools.md); per-env llmops bucket + dev datastore infra
**Created**: 2026-07-18
**Last Updated**: 2026-07-18

## Problem Statement

`ai_search` (Vertex AI Search) now works and cites sources, but the sources are
useless as links. The workbench **Sources** tab renders each grounding source as
`<a href="gs://…" target="_blank">` ([SourcesArtefactTab.tsx:60](../../../frontend/src/components/workspace/SourcesArtefactTab.tsx)),
and a browser cannot open a `gs://` URI — clicking does nothing (or, if it were
ever an `https://storage.googleapis.com/...` URL, it would leak confidential
content on the public internet, which the CLAUDE.md security rule forbids).

**Current State:**
- Enterprise-search answers cite sources, but the citations are dead `gs://` links.
- The user cannot open, preview, or work with the actual source document.
- The rich document metadata Vertex holds (case, jurisdiction, doc type, date…)
  is not surfaced, so searches can't be refined.

**Impact:**
- Affects every user of an `ai_search`-backed skill (e.g. ONE Knowledge Search).
- Major friction: "the answer cited a document — let me open it" is a dead end.

## Goals

**Primary Goal (Phase A):** Every enterprise-search source opens the real
document in the existing **Document tab** and can be added to selected documents
— reusing the bucket-menu import path — with all bytes served behind the auth gate.

**Secondary Goal (Phase B):** Expose Vertex document metadata as user-facing
search-refinement filters.

**Success Metrics:**
- Clicking a source opens a parsed preview in the Document tab in <2s (warm cache).
- Zero public-URL exposure of confidential content (all reads via the backend SA).
- Source filename shown (not the raw `gs://` path).

**Non-Goals:**
- Re-indexing the datastore or building an ingestion pipeline (infra, out of scope).
- A generic "browse the whole datastore" UI (this is source-driven only).

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Reuses import-by-reference L2/L4 cache cascade → sub-second re-open. |
| 2 | EARNED TRUST | +1 | Citations become verifiable — user opens the actual source, not a dead link. |
| 3 | SKILLS, NOT FEATURES | +1 | Any `ai_search` skill inherits this; no per-skill code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model change. |
| 5 | GRACEFUL DEGRADATION | +1 | Web (http) sources keep external-link behaviour; missing bucket/doc renders a visible notice, not a hang. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Sources stay A2UI surface data; reuses import-by-reference + thumbnail routes; no new bespoke rendering. |
| 7 | API FIRST | +1 | Reuses existing `POST /api/documents/import-by-reference` + thumbnail route; no new one-off endpoint for Phase A. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Uses existing import/parse logging. |
| 9 | SECURE BY CONSTRUCTION | +1 | Bytes served only via the backend SA behind Firebase auth; env-gated bucket override keeps dev off the prod bucket; no public URLs. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend only calls existing endpoints; bucket remap + resolution are server-side. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no -1 scores).

## Design

### Overview

Enterprise-search grounding chunks carry a `gs://` URI. We (1) **remap the bucket
server-side** to the current env's llmops bucket (docs are duplicated at identical
object paths), (2) **enrich each source** with `{bucket, object, filename}`, and
(3) make the Sources tab render `gs://` sources as **document cards** that call the
existing `ChatShell.handleImportByReference` → parse + open in the **Document tab**
+ add to selected. Phase B adds metadata filters.

### Frontend Changes

**Modified Components:**
- `src/components/workspace/SourcesArtefactTab.tsx` — for a `gs://` source, render a
  document card (filename + doc glyph/thumbnail) that on click calls a new
  `onOpenSource(bucket, object)` prop. Keep `http(s)` sources as external links.
- `src/components/chat/ChatShell.tsx:525` — pass `onOpenSource={handleImportByReference}`
  into `<SourcesArtefactTab>` (the handler already parses + opens the Document tab
  and adds to selected — [ChatShell.tsx:1332](../../../frontend/src/components/chat/ChatShell.tsx)).

**State Management:** none new — reuses `handleImportByReference` → `handleDocClick`.

**UI/UX:** Source row → click → spinner (pending) → Document tab opens with the
parsed preview, focused; a visible error toast/notice on 403/parse-fail (NEVER-SILENT).

### Backend Changes

**Modified Modules:**
- `backend/adk/a2ui_sources_render.py` — `sources_from_grounding` gains, per source:
  `bucket`, `object` (URL-decoded), `filename` (basename), and `kind` (`"gcs"` vs
  `"web"`). Apply the **bucket override** (below) so dev sources point at the dev bucket.
- `backend/tools/documents/import_by_reference.py` — no change (already does the work);
  optionally accept an already-URL-decoded object (it uses `PurePosixPath`, fine).

**New helper (server-side, unit-tested):**
- `remap_source_bucket(gs_uri: str) -> str` — if `AI_SEARCH_SOURCE_BUCKET_OVERRIDE`
  is set, replace the bucket component of a `gs://` URI with the override, preserving
  the object path. Idempotent; no-op when unset (prod).

**Data Model Changes:** the `web_sources` surface `dataModel./sources` items gain
`{kind, bucket, object, filename}` alongside the existing `{title, uri}`.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST | /api/documents/import-by-reference | Reused as-is for "open/add source" | No |
| GET | /api/documents/{id}/thumbnail | Reused for gated preview | No |

Phase A adds **no** new endpoint (reuse). Phase B may add a search-with-filter tool (below).

### Architecture Diagram

```
Vertex grounding (gs://prod-bucket/…)                     [aitana3 datastore]
        │
        ▼  sources_from_grounding + remap_source_bucket (env override → dev bucket)
[A2UI web_sources surface: /sources = [{kind,bucket,object,filename,title,uri}]]
        │  (AG-UI CUSTOM → SurfaceRegistry)
        ▼
SourcesArtefactTab (gs:// card) ──click──► ChatShell.handleImportByReference(bucket,object)
        │
        ▼  POST /api/proxy/api/documents/import-by-reference  (Firebase Bearer)
[backend: import_by_reference] ── SA reads gs://dev-bucket ──► AILANG Parse → parsed_documents
        │
        ▼  handleDocClick(doc) → Document tab (focused) + selected docs
```

### Phase B — Metadata filters (design, sequenced after A)

**Investigation needed first** (spike, ~0.5 day):
1. Introspect the `aitana3` datastore `structData` / schema (Discovery Engine REST
   `…/dataStores/aitana3/schemas`) to enumerate filterable fields (case, jurisdiction,
   doc type, year…).
2. Confirm the mechanism: the ADK grounding `VertexAiSearchTool` is model-driven and
   does **not** take user filter expressions. User-driven filtering needs a **direct
   Discovery Engine `servingConfigs.search` FunctionTool** with a `filter` param, OR a
   session-state filter that the sub-agent's tool call honours.

**Proposed approach:** a `search_filters` A2UI surface (chips derived from the schema)
whose selections write `app:ai_search_filter` session state; a thin `ai_search`
FunctionTool variant reads it and passes a Discovery Engine `filter` expression.
Detailed design deferred to the Phase-B section of the sprint plan pending the spike.

## Implementation Plan

### Phase A: Openable sources (~1 day)
- [ ] `remap_source_bucket` helper + `AI_SEARCH_SOURCE_BUCKET_OVERRIDE` env (~30 LOC + tests)
- [ ] `sources_from_grounding` → emit `{kind,bucket,object,filename}` + apply remap (~40 LOC + tests)
- [ ] `SourcesArtefactTab` → gs:// document card + `onOpenSource` prop (~60 LOC + tests)
- [ ] `ChatShell` → wire `onOpenSource={handleImportByReference}` (~5 LOC)
- [ ] Env wiring: `.env.example` + both cloudbuild.yaml (dev override only)
- [ ] Error/empty paths render (403/parse-fail notice)

### Phase B: Metadata filters (~2–3 days, after spike)
- [ ] Spike: datastore schema introspection + filter mechanism (~0.5 day)
- [ ] Direct Discovery Engine search FunctionTool with `filter` param (~1 day)
- [ ] `search_filters` A2UI surface + session-state wiring (~1 day)

## Migration & Rollout

**Environment Variables:**
- `AI_SEARCH_SOURCE_BUCKET_OVERRIDE` — derived from `BRANCH_NAME` in the **frontend** `cloudbuild.yaml` (the only service serving the workbench Sources tab + import-by-reference): `dev`/`test` → that env's `-llmops-bucket`, prod → empty (reads the real prod bucket). Local: `.env.example` (dev value).

**IAM (per env):**
- `sa-platform@your-project-id-<env>` needs `roles/storage.objectViewer` on that env's llmops bucket (same-project on dev — no cross-env prod read).

**Rollback Plan:** revert the frontend commit — sources fall back to the current
(dead-link) render; no data migration.

## Testing Strategy

### Frontend Tests (Vitest)
- [ ] SourcesArtefactTab renders a gs:// source as a doc card and calls `onOpenSource(bucket,object)` on click
- [ ] http sources still render as external links
- [ ] empty/undefined sources → "No sources" (existing)

### Backend Tests (pytest)
- [ ] `remap_source_bucket` swaps bucket when override set, no-op when unset, preserves object path (incl. URL-encoded)
- [ ] `sources_from_grounding` emits kind/bucket/object/filename for retrieved_context chunks; web chunks stay kind=web

### Manual / Live
- [ ] ONE Knowledge Search → ask a corpus question → click a source → Document tab opens the parsed doc, added to selected
- [ ] 403 (missing bucket grant) renders a visible notice, not a silent no-op
- [ ] Verify via a real stream, not jsdom (repo rule)

## Security Considerations

- **Confidential content stays behind auth.** Bytes only ever flow via the backend
  SA + Firebase-authenticated proxy. No public `gs://`/`storage.googleapis.com` URL is
  ever handed to the client. (CLAUDE.md hard rule.)
- **Env isolation.** `AI_SEARCH_SOURCE_BUCKET_OVERRIDE` keeps dev reading the dev
  bucket; dev never gets `objectViewer` on the prod bucket.
- **Access parity.** Import-by-reference already runs under the caller's Firebase
  identity; the parsed doc is stored per-user (`userId`).

## Performance Considerations

- Import-by-reference L2 (self) / L4 (sentinel) cache → re-opening a source is ~200–300ms, no re-parse.
- First open of an unparsed doc pays one AILANG Parse (seconds) — show pending state.

## Success Criteria

- [ ] Frontend tests passing (`npm run test:run`)
- [ ] Backend tests passing (`make test-fast`)
- [ ] Lint/typecheck clean
- [ ] A gs:// source opens the real document in the Document tab and adds to selected
- [ ] No public URL for confidential content anywhere in the path
- [ ] dev reads only the dev bucket (override verified)

## Open Questions

- Exact dev bucket name (`your-project-id-dev-llmops-bucket`?) — confirm for env wiring.
- Phase B: does `aitana3` carry structured `structData` metadata, and is a direct
  search-with-filter tool acceptable alongside the grounding tool? (Resolved by the spike.)

## Related Documents

- [ADK Search Tool Compatibility](../../../docs/ops/adk-search-tools.md)
- [Document Import by Reference](document-import-by-reference.md) (v6.4.0)
- `backend/adk/a2ui_sources_render.py`, `backend/tools/documents/import_by_reference.py`, `frontend/src/components/workspace/SourcesArtefactTab.tsx`, `frontend/src/components/chat/ChatShell.tsx`
