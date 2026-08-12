# v6.15.0 — Build Sequence

Search-experience polish on top of the `ai_search` (Vertex AI Search) wiring
that landed post-v6.14.0.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [search-sources-openable-documents](search-sources-openable-documents.md) | P1 | A ~1d / B ~2–3d | ai_search wiring (docs/ops/adk-search-tools.md); per-env llmops bucket + SA grant | Phase A (openable sources) ships first; Phase B (metadata filters) after a schema spike. |

## Timeline estimate

| Phase | Doc | Est. | Status |
|-------|-----|------|--------|
| A | search-sources-openable-documents (openable sources) | ~1 day | ✅ implemented |
| B | search-sources-openable-documents (metadata filters) | ~2–3 days | Planned (spike first) |

## What ships in v6.15.0

- **Phase A:** enterprise-search sources become openable documents — click a source
  → opens the real doc in the Document tab + adds to selected (reusing
  import-by-reference), all behind the auth gate. Env-driven bucket remap keeps dev
  reading the dev llmops bucket.
- **Phase B (next):** Vertex document metadata exposed as search-refinement filters.

## Dependency graph

```
ai_search wiring (v6.14.x) ─► search-sources-openable-documents (A) ─► (B) metadata filters
per-env llmops bucket + SA grant ─┘
```
