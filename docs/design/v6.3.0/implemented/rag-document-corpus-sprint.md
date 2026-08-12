# RAG Document Corpus — Sprint Plan

**Design doc:** [rag-document-corpus.md](rag-document-corpus.md)
**Sprint ID:** RAG-CORPUS
**Duration:** 3 days
**Primary driver:** TTFT improvement — replace full-doc context injection with targeted RAG retrieval
**Opt-in gate:** `RAG_DOCUMENTS_ENABLED=true` (default `false` — old artifact path stays active; template forks unaffected)
**Template scope:** Code ships in template; env var default keeps downstream safe

---

## Velocity Baseline

- 64 commits / 14 days; recent feature sprints: ~200–400 LOC/day implementation + tests
- `vertexai.rag` already importable from existing `google-cloud-aiplatform[evaluation]` dep — no new package needed
- No frontend changes required; template publish is a post-sprint step

---

## Milestones

### M1 — RAG corpus module (~0.5d)
**File:** `backend/rag/corpus.py` (new)
**LOC:** ~120 impl + ~80 tests

Five async-wrapped functions over the sync `vertexai.rag` SDK:
- `get_or_create_user_corpus(user_id)` — reads `profile.rag_corpus_name` from Firestore; calls `rag.create_corpus()` on miss, writes name back
- `upload_document(corpus_name, gcs_uri, display_name)` → `RagFile.name`
- `search_corpus(corpus_name, query, top_k=5)` → `list[dict]` with `text`, `source_file`, `score`
- `delete_document(file_name)`
- `list_user_documents(corpus_name)` → `list[rag.RagFile]`

Unit tests (`tests/unit/test_rag_corpus.py`): mock `vertexai.rag` + Firestore; 8 test cases covering happy path, corpus-already-exists, no-blocks fallback.

**Acceptance:** `make test-fast` green; `from rag.corpus import get_or_create_user_corpus` works.

---

### M2 — Callback refactor (~0.5d)
**File:** `backend/adk/callbacks.py` (modify)
**LOC:** ~40 impl delta + ~20 tests

Add `RAG_DOCUMENTS_ENABLED` check at module top. When enabled:
- `make_document_loader()` inner `_loader`: replace `save_artifact()` block with `upload_document()` call; store `file_name` in `app:docs_files` session state; remove orphan probe (no longer needed)
- `make_document_injector()` inner `_injector`: short-circuit and return `None` when RAG enabled (agent uses search tool instead)

When disabled: existing paths run unchanged — zero regression risk.

Unit tests: existing tests must still pass (flag=false path); add 4 new tests for flag=true path (mock `rag.corpus`).

**Acceptance:** `make test-fast` green; `grep "orphan probe"` log line absent in RAG mode.

---

### M3 — search_documents FunctionTool (~0.5d)
**File:** `backend/tools/rag_tool.py` (new)
**LOC:** ~60 impl + ~40 tests

```python
async def search_documents(query: str) -> str:
    """Search across documents the user has uploaded in this conversation."""
```

Reads `app:docs_files` from tool context state to get corpus name. Calls `search_corpus(corpus_name, query)`. Returns formatted string: chunks with source file attribution.

Registers on agent only when `RAG_DOCUMENTS_ENABLED=true` AND user corpus exists (same conditional pattern as `VertexAiSearchTool`).

Tool test (`tests/tool_tests/test_rag_tool.py`): run via ADK Runner with mocked `search_corpus`; assert output contains source attribution.

**Acceptance:** Tool test passes; `search_documents` appears in agent tools list when flag enabled.

---

### M4 — User profile + Firestore write (~0.25d)
**File:** `backend/db/models/access.py` or nearest user profile model (modify)
**LOC:** ~20 impl + ~10 tests

Add `rag_corpus_name: str | None = None` field. Written in `get_or_create_user_corpus()` (M1). Read by M3 tool to resolve corpus name.

**Acceptance:** Firestore model round-trips with `rag_corpus_name`; existing access tests pass.

---

### M5 — Backend API routes (~0.25d)
**File:** `backend/tools/documents/routes.py` (modify) or new `backend/protocols/rag_routes.py`
**LOC:** ~60 impl + ~30 tests

Three new endpoints:
- `GET /documents` — list RAG files for current user
- `DELETE /documents/{file_name}` — delete file (URL-encoded resource name)
- `POST /documents/search` — ad-hoc retrieval query (dev/debug; requires auth)

API tests: happy path + 401 unauthenticated.

**Acceptance:** `curl -H "Authorization: Bearer $TOKEN" http://localhost:1956/documents` returns file list.

---

### M6 — CLI subcommands (~0.5d)
**File:** `cli/` (modify — add `docs` group)
**LOC:** ~100 impl + ~40 tests

```
aiplatform docs list
aiplatform docs delete <file-name>
aiplatform docs clear
aiplatform docs search <query>
```

Each: Click subcommand + `httpx` call to M5 routes + formatted output.

Unit test: mock httpx; assert correct endpoint + payload for each command.

**Acceptance:** `aiplatform docs list` prints a table; `aiplatform docs search "revenue"` returns chunks.

---

### M7 — Integration test + docs + template prep (~0.5d)

- `backend/scripts/bootstrap_rag_corpus.py` — one-shot admin script (mirrors `bootstrap_agent_engine.py`)
- `backend/tests/integration/test_rag_corpus.py` — real corpus round-trip (guarded by `RAG_CORPUS_INTEGRATION=true`)
- Update `backend/.env.example` — document `RAG_DOCUMENTS_ENABLED=false` with comment explaining opt-in
- Update `docs/ops/deployed-urls.md` — note RAG_DOCUMENTS_ENABLED requirement for document TTFT improvement
- Update SEQUENCE.md to mark 3.1 as ✅
- Template: confirm no Aitana-internal strings leak into `rag/corpus.py` or `tools/rag_tool.py` (sanitize-ready)

**Acceptance:** Integration test passes against dev project; `make lint && make test-fast` green; design doc moved to `implemented/`.

---

## Day-by-Day

| Day | Milestones | Gate |
|-----|-----------|------|
| 1 AM | M1 (corpus module) | `make test-fast` green |
| 1 PM | M2 (callback refactor) | existing tests still pass + new flag=true tests |
| 2 AM | M3 (search tool) | tool test passes |
| 2 PM | M4 + M5 (profile + routes) | curl /documents works |
| 3 AM | M6 (CLI) | `aiplatform docs list` works |
| 3 PM | M7 (integration + docs) | `make lint && make test-fast` green; sprint closed |

---

## Risks

| Risk | Mitigation |
|------|-----------|
| `rag.upload_file()` requires GCS URI, not local path | Write blocks to `LOGS_BUCKET_NAME/tmp/` first; fall back to local path if LOCAL_MODE |
| Corpus creation is synchronous and slow (~5s first time) | Bootstrap corpus on user's first login via `get_or_create_user_corpus()` — not on upload |
| `tool_context` doesn't expose session state in same way as `callback_context` | Read corpus name via `tool_context.state` (ADK InvocationContext pattern, same as budget tool) |
| Template sanitize strips `rag/` module as Aitana-internal | Module has no Aitana-internal strings; confirm by running sanitize dry-run in M7 |

---

## Success Criteria (from design doc)

- [ ] `rag.upload_file()` called on document upload when `RAG_DOCUMENTS_ENABLED=true`
- [ ] `rag.retrieval_query()` called when agent invokes `search_documents` tool
- [ ] `user_profile.rag_corpus_name` written to Firestore on first upload
- [ ] Documents from session A retrievable in session B (same user, same corpus)
- [ ] `"doc loader: dropping N orphaned id(s)"` never appears after hot-reload in RAG mode
- [ ] `aiplatform docs list` lists files with display names and corpus resource name
- [ ] `aiplatform docs search "revenue Q1"` returns chunks from an uploaded PDF
- [ ] All unit tests pass; integration test passes against dev project
- [ ] `make lint && make test-fast` green
- [ ] `RAG_DOCUMENTS_ENABLED=false` default confirmed in `.env.example`
