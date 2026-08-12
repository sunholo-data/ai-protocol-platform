# Sprint Plan — Search Sources → Openable Documents

**Design doc:** [search-sources-openable-documents.md](search-sources-openable-documents.md)
**Goal of this sprint:** ship **Phase A** (openable sources) to dev, tested. Phase B
(metadata filters) is scoped but sequenced after a spike.

## Milestones

### M1 — Backend: bucket remap + source enrichment (~0.4 day)
- `remap_source_bucket(gs_uri)` in `a2ui_sources_render.py` using `AI_SEARCH_SOURCE_BUCKET_OVERRIDE`.
- `sources_from_grounding` emits `{title, uri, kind, bucket, object, filename}` and applies remap to `uri`/`bucket`/`object`.
- Tests: `test_a2ui_sources_render.py` — remap on/off, url-decoded object, web vs gcs kind.
- **Gate:** `make test-fast` green.

### M2 — Frontend: gs:// sources open the Document tab (~0.4 day)
- `SourcesArtefactTab` — gs:// source → document card → `onOpenSource(bucket, object)`; http → external link (unchanged). Pending + error states render.
- `ChatShell.tsx:525` — pass `onOpenSource={handleImportByReference}`.
- Tests: `SourcesArtefactTab.test.tsx` — gs:// card click calls onOpenSource; http renders link.
- **Gate:** `npm run quality:check` green.

### M3 — Env + deploy wiring (~0.1 day)
- `AI_SEARCH_SOURCE_BUCKET_OVERRIDE` in `.env.example` + both `cloudbuild.yaml` (dev value only).
- Doc: note the `storage.objectViewer` grant on the dev bucket in adk-search-tools.md.

### M4 — Ship + verify (~0.1 day)
- Push to dev; watch build; live-stream verify: source click → Document tab opens the parsed doc; 403 path renders a notice.

## Phase B (next sprint, not this push)
- Spike: introspect `aitana3` schema (Discovery Engine `…/schemas`) + confirm filter mechanism.
- Direct Discovery Engine search FunctionTool with `filter`; `search_filters` A2UI surface → session state.

## Acceptance
- [ ] M1–M4 gates green
- [ ] gs:// source opens the real document, added to selected, all behind auth
- [ ] dev reads only the dev bucket
