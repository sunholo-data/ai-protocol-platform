# v6.3.0 Build Sequence

**Gate:** v6.2.0 substantially complete (specifically `document-data-layer` v6.1.0 ✅ and `session-and-memory` v6.0.0 ✅ which this version builds on).

**Status as of 2026-06-02:** Two docs — both implemented ✅.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 3.1 ✅ | [rag-document-corpus.md](implemented/rag-document-corpus.md) | P1 | 3d | document-data-layer (v6.1.0 ✅), session-and-memory (v6.0.0 ✅) | Replaces ADK artifact layer for user documents with Vertex AI RAG Engine. Per-user corpus, persistent across backend restarts, semantic retrieval. Eliminates orphan probe + full-doc context injection. Adds `aiplatform docs` CLI commands. |
| 3.2 ✅ | [client-tenant-management.md](implemented/client-tenant-management.md) | P1 | 1d | rag-document-corpus (3.1 ✅) | Admin CRUD API + `aiplatform client` CLI for `clients/{domain}` Firestore config. Closes the Firestore-console-as-interface gap; enables per-client GCS bucket routing without console access. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 3.1 ✅ | [rag-document-corpus.md](implemented/rag-document-corpus.md) | Implemented 2026-06-01 |
| 3.2 ✅ | [client-tenant-management.md](implemented/client-tenant-management.md) | Implemented 2026-06-02 |

---

## What ships in v6.3.0

- **RAG Engine document corpus** — per-user Vertex AI RAG corpus for uploaded documents; semantic `search_documents` tool replaces full-doc context injection; documents persist across sessions and backend restarts; `aiplatform docs` CLI subcommand group.
- **Client/tenant management** — admin CRUD API (`/api/admin/clients`) + `aiplatform client` CLI for managing `clients/{domain}` Firestore records; enables per-client GCS bucket assignment without Firestore console access.

## Dependency Graph

```
v6.2.0 substantially complete
    │
    └──► rag-document-corpus (3.1) ✅
             │
             ├── document-data-layer (v6.1.0 ✅)  — user Firestore profile model
             ├── session-and-memory (v6.0.0 ✅)   — ADK artifact service being replaced
             └── AGENT_ENGINE_ID infra (✅)        — same SA covers Vertex RAG, no new IAM
             │
             └──► client-tenant-management (3.2)
                      │
                      └── db/clients.py read path (✅) — schema unchanged; only adds write surface
```

## Next: v6.4.0

Not yet planned. Candidates: shared/team RAG corpora (per-skill), real-time collaborative editing, voice (Gemini Live), skill marketplace.
