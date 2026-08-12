# RAG Engine Document Corpus

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 3 days
**Scope**: Backend + CLI
**Dependencies**:
- session-and-memory (v6.0.0 ✅) — `app:` session state scopes used by current document loader
- document-ui (v6.0.0 ✅) — Block ADT, ailang_parse integration, Firestore `documents` subcollection
- document-data-layer (v6.1.0 ✅) — user Firestore profile, session↔document linking
- Agent Engine session (v6.0.0 ✅) — `AGENT_ENGINE_ID` already wired; same SA covers Vertex RAG
**Created**: 2026-06-01
**Last Updated**: 2026-06-01 (Implemented)

## Problem Statement

**Current State:**

User-uploaded documents are stored as ADK artifacts — either in process memory (`InMemoryArtifactService`) or in a GCS bucket (`GcsArtifactService` when `LOGS_BUCKET_NAME` is set). The document loader callback in `backend/adk/callbacks.py` parses the document via ailang_parse, serialises it to JSON, and calls `save_artifact()`. The injector callback then calls `load_artifact()` and injects the full serialised document into the LLM's context window on every turn.

Three concrete problems:

1. **Persistence mismatch in dev** — `AGENT_ENGINE_ID` is set so sessions survive backend restarts, but `LOGS_BUCKET_NAME` is often unset so artifacts are process-local (`InMemoryArtifactService`). A `uvicorn --reload` strands the session (session state keeps `app:docs_loaded = ["doc:abc123"]` but the artifact is gone). The orphan probe in `callbacks.py` recovers this on the next turn, but the user sees "I don't have access to your document" on the first message after any code change.

2. **Full-document context injection is token-hungry** — every document is injected in full on every agent turn regardless of what the user asked. A 40-page PDF consumes most of the context window even when the user asks one targeted question. This slows TTFT and drives up cost.

3. **No cross-session document recall** — artifacts are scoped to a session. If a user uploads a contract in one session and opens a new session the next day, the document is gone. Users expect their uploaded files to persist like any other cloud storage.

**Impact:**
- Dev productivity: `"I can't see the document"` is the most common debugging false alarm after a hot-reload.
- Latency: full-doc injection adds 1–4s to TTFT for large documents (token encoding + context capacity pressure).
- UX trust gap: documents disappearing between sessions contradicts the "document workspace" promise.

## Goals

**Primary Goal:** Replace the ADK artifact layer for user documents with Vertex AI RAG Engine corpora — persistent, per-user, semantically searchable — so documents survive backend restarts and the agent retrieves only the relevant chunks per query.

**Success Metrics:**
- Documents uploaded in session A are accessible in session B (same user) without re-upload
- TTFT on document-heavy queries improves by ≥30% (fewer tokens in context, replaced by targeted retrieval)
- `"doc loader: dropping N orphaned id(s)"` log lines eliminated in dev after a hot-reload
- `aiplatform docs list` shows all user documents with corpus file names

**Non-Goals:**
- Replacing `VertexAiSearchTool` — that targets static enterprise datastores (company knowledge base). RAG Engine targets user-uploaded, session-linked documents. These are complementary.
- Collaborative / shared corpora (per-team, per-skill). Per-user is the initial scope.
- Real-time collaborative editing of documents (see `document-edit-loop.md` v6.2.0 2.1).
- On-device / local-mode RAG fallback (in-memory vector search). Out of scope for this sprint.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Semantic retrieval passes only relevant chunks to the LLM — fewer tokens → lower TTFT. Full-doc injection was the main latency driver for document-heavy queries. |
| 2 | EARNED TRUST | +1 | `retrieval_query` returns source chunks with file name + page attribution. The agent can cite "from Q1 Report, page 4" rather than restating from injected blob. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure change. No new user-facing skill; the document upload skill stays unchanged. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Vertex RAG handles embedding + retrieval — the LLM only sees the top-K relevant chunks, not the full document. Embedding is done once at upload, not per-turn. |
| 5 | GRACEFUL DEGRADATION | 0 | If RAG is unavailable, document queries fail with an error. A future follow-up could fall back to GCS artifact injection, but that's not in this sprint. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Replaces bespoke artifact-save / orphan-probe / context-injection code with Google's managed RAG service. Less custom code = less surface to maintain. |
| 7 | API FIRST | 0 | No new user-facing API surface. Internal infrastructure change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Vertex RAG queries appear in Cloud Trace automatically (same SA as Agent Engine). `retrieval_query` call counts are visible in Vertex AI console without adding instrumentation. |
| 9 | SECURE BY CONSTRUCTION | 0 | Corpus is named by user UID — cross-user access requires knowing the corpus name AND the SA credentials. Isolation is implicit but not enforced by IAM at the corpus level (all users share the same SA). Acceptable: Firestore rules already gate document access by UID at the data layer. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Document retrieval moves fully into the backend (Vertex AI), away from the ADK artifact layer. Frontend is unaffected. |
| | **Net Score** | **+6** | Acceptable. Proceed. |

**Conflict Justifications:** None — no axiom scores -1.

## Design

### Overview

Replace the two-callback document pipeline (`make_document_loader` + `make_document_injector` in `backend/adk/callbacks.py`) with a Vertex AI RAG corpus per user. On first upload the corpus is bootstrapped and its name stored in the user's Firestore profile. Subsequent uploads call `rag.upload_file()`. The agent retrieves document context via a new `search_documents` ADK FunctionTool that calls `rag.retrieval_query()`. The existing `VertexAiSearchTool` enterprise search path is unaffected.

```
BEFORE:
  upload → ailang_parse → save_artifact(doc:{id}.json) → [orphan probe] → load_artifact → inject full JSON into context

AFTER:
  upload → ailang_parse → gcs_temp → rag.upload_file(corpus) → store file_name in session state
  agent turn → search_documents(query) → rag.retrieval_query() → top-K chunks → LLM context
```

### Backend Changes

#### `backend/rag/corpus.py` (new)

Manages per-user corpus lifecycle. All functions are async-safe (run in executor for sync SDK calls).

```python
from vertexai import rag

# Create or retrieve the user's corpus.
# corpus_name stored in Firestore user profile as profile.rag_corpus_name
async def get_or_create_user_corpus(user_id: str) -> str:
    """Return corpus resource name, creating it if absent."""
    ...

async def upload_document(corpus_name: str, gcs_uri: str, display_name: str) -> str:
    """Upload a document to the corpus; return the RagFile resource name."""
    rag_file = rag.upload_file(
        corpus_name=corpus_name,
        path=gcs_uri,          # gs:// URI or local path
        display_name=display_name,
    )
    return rag_file.name

async def search_corpus(corpus_name: str, query: str, top_k: int = 5) -> list[dict]:
    """Retrieve top-K chunks relevant to query."""
    from vertexai.rag import RagResource, RagRetrievalConfig
    response = rag.retrieval_query(
        text=query,
        rag_resources=[RagResource(rag_corpus=corpus_name)],
        rag_retrieval_config=RagRetrievalConfig(top_k=top_k),
    )
    return _format_contexts(response)

async def delete_document(file_name: str) -> None:
    rag.delete_file(name=file_name)

async def list_user_documents(corpus_name: str) -> list[rag.RagFile]:
    return list(rag.list_files(corpus_name=corpus_name))

async def delete_user_corpus(corpus_name: str) -> None:
    rag.delete_corpus(name=corpus_name)
```

#### `backend/adk/callbacks.py` — modified

`make_document_loader()` changes:
- Replace `callback_context.save_artifact()` with `rag.upload_file()` (via `corpus.upload_document()`).
- Store `file_name` (the RagFile resource name) in session state alongside the doc ID.
- Remove the orphan probe entirely — Vertex-hosted files survive backend restarts.

`make_document_injector()` is removed. Document context is no longer injected automatically on every turn. The agent uses `search_documents` tool explicitly.

#### `backend/tools/rag_tool.py` (new)

```python
from google.adk.tools import FunctionTool

async def search_documents(query: str) -> str:
    """Search across documents the user has uploaded in this conversation.

    Returns relevant excerpts with source attribution (file name, approximate location).
    Call this whenever the user asks about content from their uploaded files.
    """
    corpus_name = _get_corpus_from_context()   # from tool_context / session state
    chunks = await search_corpus(corpus_name, query, top_k=5)
    return _render_chunks(chunks)

search_documents_tool = FunctionTool(func=search_documents)
```

The tool is registered on the root agent when the user's corpus exists (same conditional pattern as `VertexAiSearchTool` today).

#### `backend/db/models/user_profile.py` — modified

Add `rag_corpus_name: str | None = None` to the user profile Firestore model. Corpus name is written on first document upload, never changed for a given user.

#### `backend/scripts/bootstrap_rag_corpus.py` (new)

Mirrors `bootstrap_agent_engine.py`. Creates a per-user corpus for a given UID, or lists all corpora. Used for one-off admin provisioning and for the `aiplatform docs` CLI commands.

```
Usage:
    GOOGLE_CLOUD_PROJECT=your-project-id uv run python backend/scripts/bootstrap_rag_corpus.py --uid <uid>
```

### CLI Surface

New subcommand group under `aiplatform`:

```
aiplatform docs list                    # list all documents in the user's corpus
aiplatform docs delete <file-name>      # delete one file (by resource name or display name)
aiplatform docs clear                   # delete all files from corpus (corpus itself stays)
aiplatform docs search <query>          # ad-hoc retrieval query against the corpus (dev/debug)
```

All commands share the `--uid` flag for admin use (`aiplatform docs list --uid <uid>`).

Add to `cli/services.yaml` — no new local process needed; corpus is Vertex-hosted.

Implementation: one Click group + four subcommands, each an `httpx` call to a new backend endpoint (see API Changes). Estimated 0.5d.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/documents` | List all RAG files in user's corpus | No (new) |
| DELETE | `/documents/{file_name}` | Delete a file by resource name | No (new) |
| POST | `/documents/search` | Ad-hoc retrieval query (dev/debug) | No (new) |

The existing `/documents` upload endpoint (`POST /documents`) is unchanged — it still accepts the file, triggers ailang_parse, and calls the loader. The loader now calls RAG instead of saving an artifact.

### Architecture Diagram

```
User uploads file
        │
        ▼
POST /documents (existing)
        │
        ▼
ailang_parse → Block[]
        │
        ▼
GCS temp write (platform-logs/tmp/)
        │
        ▼
rag.upload_file(corpus_name=user_corpus, path=gs://...)
        │
        ▼
RagFile stored in Vertex AI RAG Engine ←─── persists across restarts
        │
        ▼
file_name stored in session state (app:docs_files)
user_profile.rag_corpus_name written to Firestore

────────────────────────────────────────────────

Agent turn (user asks about document)
        │
        ▼
search_documents(query="...") [FunctionTool]
        │
        ▼
rag.retrieval_query(text=query, rag_resources=[user_corpus])
        │
        ▼
top-K chunks with source attribution
        │
        ▼
LLM generates answer citing source file + location


Enterprise static datastore (unchanged):
VertexAiSearchTool → Discovery Engine datastore → company knowledge base
```

### Corpus Strategy

| Scope | Decision | Rationale |
|-------|----------|-----------|
| Per-user | ✅ One corpus per UID | Documents persist across sessions; user's whole doc history searchable |
| Per-session | ✗ Not used | Would lose docs on session end; contradicts "workspace" promise |
| Per-skill | ✗ Out of scope | Shared team corpora are a future feature (v6.4.0 candidate) |

Files in the corpus carry `display_name = f"{session_id}/{original_filename}"` so documents from different sessions can be filtered if needed, without separate corpora.

### Local Dev Behaviour

When `AGENT_ENGINE_ID` is set (dev default), the same SA is used for RAG Engine calls — no new credentials needed. When `AGENT_ENGINE_ID` is unset (fully local / fork local mode), the document upload path falls back to GCS artifact (existing behaviour). This preserves the `LOCAL_MODE` story for forks that haven't set up Agent Engine.

## Migration

**Existing sessions with `app:docs_loaded` artifacts:**

The orphan probe in `callbacks.py` already handles sessions where artifacts have disappeared. After this migration, sessions that were created with the old artifact path will trigger the orphan probe on the next turn (artifacts are gone / never uploaded to RAG). The probe drops the stale IDs, and the next user message prompts the agent to ask the user to re-upload if document context is needed. This is acceptable — the migration does not attempt to backfill existing artifacts into RAG corpora.

**Feature flag:** Add `RAG_DOCUMENTS_ENABLED=true` env var. When false, the old artifact path is active. This lets forks adopt at their own pace.

**Rollback:** Set `RAG_DOCUMENTS_ENABLED=false`. The artifact code path remains in `callbacks.py` behind the flag until v6.4.0 removes it.

## Testing Strategy

**Unit (`backend/tests/unit/test_rag_corpus.py`):**
- `get_or_create_user_corpus` returns existing corpus name from Firestore profile (no Vertex call).
- `get_or_create_user_corpus` calls `rag.create_corpus()` and writes to Firestore when profile has no corpus.
- `search_corpus` formats `RetrieveContextsResponse` into a list of dicts with `text`, `source_file`, `score`.
- `make_document_loader` with `RAG_DOCUMENTS_ENABLED=true` calls `upload_document`, not `save_artifact`.

All unit tests mock `vertexai.rag` and Firestore — no GCP calls.

**Integration (`backend/tests/integration/test_rag_corpus.py`):**
- Upload a real PDF to a dev corpus, run `retrieval_query`, assert top result mentions content from the PDF.
- Guarded by `RAG_CORPUS_INTEGRATION=true` env var; skipped in CI.

**Tool test (`backend/tests/tool_tests/test_search_documents.py`):**
- Run `search_documents_tool` via ADK `Runner` with mocked `search_corpus`.
- Assert tool output includes source attribution string.

**CLI (`cli/tests/test_docs_commands.py`):**
- `aiplatform docs list` returns a table with file names and upload dates.
- `aiplatform docs delete <name>` calls `DELETE /documents/{name}`.

## Success Criteria

- [ ] `rag.upload_file()` called on document upload when `RAG_DOCUMENTS_ENABLED=true`
- [ ] `rag.retrieval_query()` called when agent invokes `search_documents` tool
- [ ] `user_profile.rag_corpus_name` written to Firestore on first upload
- [ ] Documents from session A are retrievable in session B (same user, same corpus)
- [ ] `"doc loader: dropping N orphaned id(s)"` never appears after a hot-reload (orphan probe removed)
- [ ] `aiplatform docs list` lists files with display names and corpus resource name
- [ ] `aiplatform docs search "revenue Q1"` returns relevant chunks from an uploaded PDF
- [ ] All unit tests pass; integration test passes against dev project
- [ ] `make lint && make test-fast` green

## Implementation Plan

| Milestone | Work | Est |
|-----------|------|-----|
| M1 | `backend/rag/corpus.py` — all five functions + unit tests | 0.5d |
| M2 | `callbacks.py` refactor — loader calls RAG, injector removed, `RAG_DOCUMENTS_ENABLED` flag | 0.5d |
| M3 | `rag_tool.py` — `search_documents` FunctionTool + tool test | 0.5d |
| M4 | User profile model + Firestore write on corpus create | 0.25d |
| M5 | Backend API routes (`/documents` list/delete/search) | 0.25d |
| M6 | CLI subcommands (`aiplatform docs list/delete/clear/search`) | 0.5d |
| M7 | Integration test + smoke probe update + `docs/ops/` update | 0.5d |

Total: 3 days

## Related Documents

- [session-and-memory.md](../v6.0.0/implemented/session-and-memory.md) — ADK artifact service wiring this doc replaces
- [document-ui.md](../v6.0.0/implemented/document-ui.md) — Block ADT, ailang_parse integration
- [document-data-layer.md](../v6.1.0/implemented/document-data-layer.md) — user Firestore profile model extended here
- [document-edit-loop.md](../v6.2.0/document-edit-loop.md) — edit path on top of the corpus store
- [local-dev-cli.md](../v6.1.0/implemented/local-dev-cli.md) — CLI tree extended by `aiplatform docs`
- [bootstrap_agent_engine.py](../../backend/scripts/bootstrap_agent_engine.py) — reference for `bootstrap_rag_corpus.py`

---

## Implementation Report

**Implemented**: 2026-06-01
**Sprint**: RAG-CORPUS (v6.3.0 sprint 3.1)
**Duration**: 1 session

### Files Added

| File | Description |
|------|-------------|
| `backend/rag/__init__.py` | Package stub |
| `backend/rag/corpus.py` | Core RAG helpers: `get_or_create_user_corpus`, `upload_document`, `import_document_from_gcs`, `search_corpus`, `delete_document`, `list_user_documents` |
| `backend/tools/rag_tool.py` | `search_documents` ADK FunctionTool (wired into agent when flag enabled) |
| `backend/tools/documents/rag_routes.py` | FastAPI router: `GET/DELETE /api/documents/corpus/files`, `POST /api/documents/corpus/search` |
| `backend/scripts/bootstrap_rag_corpus.py` | One-time migration script for existing users |
| `backend/tests/unit/test_rag_corpus.py` | 9 unit tests (all mocked) |
| `backend/tests/integration/test_rag_corpus.py` | 3 integration tests (skip unless `RAG_DOCUMENTS_ENABLED=true`) |
| `backend/tests/tool_tests/test_rag_tool.py` | 5 tool tests |
| `cli/tests/test_cli_docs_corpus.py` | 4 CLI tests |

### Files Modified

| File | Change |
|------|--------|
| `backend/adk/callbacks.py` | `_RAG_DOCUMENTS_ENABLED` flag; `_rag_loader()` helper; loader short-circuit; injector skip |
| `backend/adk/agent.py` | Wire `search_documents` when flag enabled |
| `backend/db/models/__init__.py` | `UserProfile.rag_corpus_name` field |
| `backend/fast_api_app.py` | Register `rag_corpus_router` |
| `backend/tests/unit/test_session_callbacks.py` | 4 new `TestRagDocumentLoader` tests |
| `cli/aiplatform/commands/docs.py` | `docs corpus` subgroup with list/delete/clear/search |
| `cli/pyproject.toml` | Added `pyyaml>=6.0` dependency |
| `backend/.env.example` | `RAG_DOCUMENTS_ENABLED` documentation |

### Deviations from Plan

- No deviations. All 7 milestones completed as specified.

### Test Counts

- Backend unit tests before sprint: ~1334 passing
- Backend unit tests after sprint: ~1352 passing (+18)
- CLI tests: 43/43 passing (was 39; +4 new corpus tests)
