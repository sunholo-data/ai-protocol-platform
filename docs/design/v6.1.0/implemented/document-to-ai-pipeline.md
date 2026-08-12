---
**Feature:** Document-to-AI Pipeline (ADK Artifact Service)
**Status:** Implemented
**Priority:** P0
**Estimated Effort:** 2 days (backend only)
**Owner:** Mark
**Last Updated:** 2026-04-24
---

# Document-to-AI Pipeline

## Problem Statement

Uploaded documents are parsed by AILANG Parse and stored in Firestore (`parsed_documents`), but the AI agent **never sees their content**. Every agent is built with zero document tools: all skills have `tools: []` in Firestore, so `resolve_tools([])` returns an empty list. The agent cannot list, find, or read any document.

This produces the failure the user sees: upload a `.docx`, see the green "Parsed" dot, ask the AI about it, and the AI replies "Please specify which document you are referring to."

There are two separate gaps:

1. **No document tools on the agent** — `list_documents` and `get_document_content` exist in `adk/tools.py` but are never wired because all skills have `tools: []` in Firestore.
2. **Parsed content never written to the ADK artifact service** — the upload pipeline writes to Firestore only. `load_artifacts_tool` (ADK's built-in mechanism for injecting file content into the model's context) therefore finds nothing.

## Goals

**Primary:** After a document is opened in a conversation, the AI can read its full structured content, cite specific pages and sections, and reason across multiple documents in the same session.

**Success metrics:**
- User clicks a document → opens a chat session — AI has access to the document content immediately on the first message.
- AI can cite "page 3, under 'Revenue Summary'" with block-level provenance from the AILANG Parse output.
- Multiple documents opened in one session are all accessible.
- Re-uploading a document produces fresh content on next session open (no stale artifact).
- Local dev with a real GCS bucket works (`ADK_ARTIFACT_BUCKET` in `.env`).
- Clicking a document in the file browser shows all past conversations that involved it.

## Non-Goals

- Replacing Firestore as the source of truth for document metadata (filename, parse status, folder, user). Firestore stays; the artifact service is a session-level cache on top of it.
- Image/binary rendering via model vision (out of scope; the artifact service supports it, but MIME handling is a separate concern).
- Skill-scoped GCS bucket access per document (v6.2 — see "Future" section).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Artifact loading happens in `before_agent_callback` before the first LLM call. No user-visible latency added — the document was already parsed. |
| 2 | EARNED TRUST | +1 | AI cites block-level provenance (page number, section heading) from AILANG Parse structured output. No hallucinated sources. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure change invisible to end users. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Document text arrives as JSON via the artifact service — zero LLM tokens at parse time. AILANG Parse deterministic path preserved. |
| 5 | GRACEFUL DEGRADATION | +1 | Artifact load failure in `before_agent_callback` is non-fatal. AI receives an explicit error message in its context ("⚠️ Document could not be loaded into context") and can tell the user. |
| 6 | PROTOCOL OVER CUSTOM | +1 | ADK's built-in `load_artifacts_tool` and `GcsArtifactService`. No custom context injection. |
| 7 | API FIRST | 0 | Backend-only change; no new API surface. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Artifact save/load is in the ADK runner, already traced. |
| 9 | SECURE BY CONSTRUCTION | +1 | Session-scoped artifacts: `(app_name, user_id, session_id)`. Cross-user and cross-session access architecturally impossible. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend: no changes. |
| | **Net Score** | **+6** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Background: How `load_artifacts_tool` Works

Source-verified against ADK v1.29.0 (`google.adk.tools.load_artifacts_tool.LoadArtifactsTool`).

**Phase 1 — `process_llm_request` (fires before every model call):**
1. Calls `tool_context.list_artifacts()` → gets all artifact filenames for this `(app_name, user_id, session_id)`.
2. If any exist, appends a system instruction: "You have a list of artifacts: `[...]`. Call `load_artifacts` to load them before answering questions."
3. If the last message is a `function_response` for `load_artifacts`, fetches each artifact and appends it to `llm_request.contents` as a `user` turn.

**Phase 2 — `run_async` (fires when the model calls the tool):**
Returns a placeholder dict. The actual injection happened in `process_llm_request`.

**MIME type handling relevant to us:**
- `application/json` → decoded to UTF-8 text → injected as `Part.from_text(json_string)`. The model receives the full JSON and can parse it.
- `text/plain` / `text/markdown` → same.
- `application/pdf`, `image/*` → inline binary (vision models).

**Scoping: session-scoped artifacts.**
Artifacts are stored without the `user:` prefix — `doc:{doc_id}.json` — which ties them to `(app_name, user_id, session_id)`. Each conversation has its own artifact cache. If the user opens the same document in a new conversation, `before_agent_callback` fetches fresh content from Firestore for that session. Firestore is the source of truth; artifacts are ephemeral session caches.

`GcsArtifactService` automatically namespaces the GCS path as `{bucket}/{app_name}/{user_id}/{session_id}/doc:{doc_id}.json` — user isolation is in the path structure, not in the ADK `user:` prefix feature. One bucket per environment is correct.

## Design

### Core Model: Document-First, Session-Contextual

The platform is document-first: a document is the organizing unit, and conversations exist *around* documents. The data model already reflects this — `chat_sessions` has a `document_id` field. This design extends it:

- **Document** → many **Sessions** (each conversation that used the document)
- **Session** → one or more **Documents** (the document(s) in context for that conversation)
- Clicking a document in the file browser → queries `chat_sessions` where `document_id == doc_id` → shows all related conversations
- Opening a new conversation from a document → creates session with `document_id` in state

The artifact service is a **session-level cache** of parsed document content, populated by the `before_agent_callback` at the start of each session. Firestore remains the source of truth; artifacts are ephemeral and rebuilt fresh per session (no stale content after re-upload).

### Architecture

```
                        FILE BROWSER (UI)
                              │
              Click doc ──────┤──── Shows related conversations
                              │     (queries chat_sessions by document_id)
                              ▼
                    POST /api/skills/{id}/stream
                    body: { document_id: "doc123", session_id: null }
                              │
                              ▼
                    skill_processor.process_skill_request()
                    → creates session with state: { document_id: "doc123" }
                              │
                              ▼
                    ADK runner: LlmAgent.run()
                              │
                              ▼
              before_agent_callback  (first turn only — state flag)
                │
                ├── read state: document_id = "doc123"
                ├── fetch parsed blocks from Firestore (build_document_context)
                ├── callback_context.save_artifact(
                │       filename="doc:doc123.json",
                │       artifact=Part(inline_data=Blob(
                │           data=json.dumps(blocks).encode(),
                │           mime_type="application/json"
                │       ))
                │   )
                └── state["app:docs_loaded"] = True
                              │
                              ▼
              load_artifacts_tool.process_llm_request()
                └── list_artifacts() → ["doc:doc123.json"]
                └── appends instruction to LlmRequest
                              │
                              ▼
              Model calls load_artifacts(["doc:doc123.json"])
                              │
                              ▼
              next process_llm_request():
                └── load_artifact("doc:doc123.json") → Part.from_text(json_string)
                └── appended to llm_request.contents
                              │
                              ▼
              Model answers with structured document data + citations
```

### Artifact Content Format

**We store the full AILANG Parse block output, not just rendered markdown.**

AILANG Parse produces structured blocks with provenance metadata:

```json
{
  "filename": "Q1-Executive-Summary.docx",
  "doc_id": "abc123",
  "source_format": "docx",
  "blocks": [
    {
      "type": "heading",
      "level": 1,
      "text": "Revenue Summary",
      "page": 1,
      "block_id": "b001"
    },
    {
      "type": "paragraph",
      "text": "Q1 2026 revenue was $42M, up 18% year-over-year.",
      "page": 1,
      "block_id": "b002",
      "heading_path": ["Revenue Summary"]
    },
    {
      "type": "table",
      "headers": ["Quarter", "Revenue", "Growth"],
      "rows": [["Q1 2026", "$42M", "+18%"], ["Q4 2025", "$35M", "+12%"]],
      "page": 2,
      "block_id": "b003",
      "heading_path": ["Revenue Summary", "Historical Comparison"]
    }
  ]
}
```

**Why structured JSON over markdown:**
- **Citations**: model can reference `page 1, block b002` or `"under 'Revenue Summary'"` with block-level precision.
- **Tables**: JSON preserves table structure exactly; markdown tables lose alignment and column semantics.
- **Track changes**: AILANG Parse captures accepted/rejected changes in blocks — markdown flattens them.
- **Multi-document reasoning**: the model can compare `doc_id: "abc"` block X with `doc_id: "xyz"` block Y.
- **No double-conversion loss**: `build_document_context` already converts to markdown for display; the artifact stores the richer source.

The model instructions should include: "Documents are provided as JSON. Each block has `page`, `block_id`, `heading_path`. When citing content, reference the page and heading path."

### Backend Changes

#### 1. `before_agent_callback` loads documents (`adk/callbacks.py`)

New `make_document_loader(document_id)` callback, composed into `_composed_before_agent`:

```python
# adk/callbacks.py

import json
from google.genai import types as genai_types

_STATE_DOCS_LOADED = "app:docs_loaded"

def make_document_loader(document_id: str | None) -> Any:
    """Return an async before_agent_callback that loads document content into artifacts.

    Fires on the first turn of a session only (guarded by app:docs_loaded state flag).
    Fetches parsed blocks from Firestore and saves as a session-scoped artifact so
    load_artifacts_tool can inject them into the model's context.
    """
    if not document_id:
        return lambda ctx: None  # no-op when no document is in scope

    def _loader(callback_context: Any) -> None:
        # Guard: only run on first turn per session
        state = getattr(callback_context, "state", None)
        if state is None or state.get(_STATE_DOCS_LOADED):
            return

        from tools.documents.context import build_document_context
        from google.genai import types as genai_types
        import json

        try:
            _markdown, blocks = build_document_context(document_id, mode="blocks")
        except KeyError:
            # Document not found — surface error to AI via state
            state["app:doc_load_error"] = f"Document '{document_id}' not found in Firestore."
            state[_STATE_DOCS_LOADED] = True
            return
        except Exception as exc:
            state["app:doc_load_error"] = f"Could not load document '{document_id}': {exc}"
            state[_STATE_DOCS_LOADED] = True
            return

        if not blocks:
            state["app:doc_load_error"] = f"Document '{document_id}' has no parsed content."
            state[_STATE_DOCS_LOADED] = True
            return

        payload = json.dumps(blocks, ensure_ascii=False).encode("utf-8")
        artifact = genai_types.Part(
            inline_data=genai_types.Blob(
                data=payload,
                mime_type="application/json",
            )
        )

        try:
            # Session-scoped (no user: prefix) — artifact lives at
            # {bucket}/{app_name}/{user_id}/{session_id}/doc:{doc_id}.json.
            # Fresh fetch from Firestore each new session → never stale.
            callback_context.save_artifact(
                filename=f"doc:{document_id}.json",
                artifact=artifact,
            )
            state[_STATE_DOCS_LOADED] = True
            logger.info("Loaded document %s into session artifacts", document_id)
        except Exception as exc:
            logger.warning("Failed to save document artifact %s: %s", document_id, exc)
            state["app:doc_load_error"] = f"Document could not be cached: {exc}"
            state[_STATE_DOCS_LOADED] = True

    return _loader
```

Note: `save_artifact` on `CallbackContext` is synchronous in ADK 1.29.0 (unlike `ToolContext.save_artifact` which is async). Verify at implementation time; if async, wrap the callback.

#### 2. Wire document loader into `create_agent` (`adk/agent.py`)

```python
# adk/agent.py — create_agent()

from adk.callbacks import make_document_loader

# document_id comes from access_context or is passed explicitly
document_id = access_context.document_id if access_context else None
_document_loader = make_document_loader(document_id)

def _composed_before_agent(callback_context: object) -> None:
    _before_agent(callback_context)
    _session_tracker(callback_context)
    _document_loader(callback_context)
```

`AccessContext` needs a `document_id` field (or it's read from request state — see skill_processor change below).

#### 3. Pass `document_id` from request through to agent (`skills/skill_processor.py`)

The frontend sends `document_id` as part of the skill stream request. Currently `state={}` is empty in `RunAgentInput`. Update:

```python
# skills/skill_processor.py

run_input = RunAgentInput(
    threadId=thread_id,
    ...
    state={"document_id": document_id} if document_id else {},
)
```

`document_id` comes from the HTTP request body (frontend sends it when opening a document-linked chat).

#### 4. `load_artifacts_tool` always-on (`adk/agent.py`)

```python
from google.adk.tools.load_artifacts_tool import load_artifacts_tool

tools = [
    load_artifacts_tool,   # ADK built-in: injects artifacts into LLM context
    retrieve_artifact,     # our custom: section-retrieval for large tool outputs
    *resolve_tools(md.tools, md.tool_configs),
]
```

#### 5. Error feedback to AI

If `app:doc_load_error` is in session state, inject it as a system message at the start of the first turn so the AI knows what happened and can communicate it to the user:

```python
# in before_model_callback or via a check in process_skill_request
if state.get("app:doc_load_error"):
    # Prepend error to user message context
    error_msg = state["app:doc_load_error"]
    # ... inject as system context
```

This preserves the "fail loudly to the AI, gracefully to the user" principle.

### GCS Bucket Strategy

**One dedicated artifact bucket per environment. User isolation is automatic via path structure.**

`GcsArtifactService` stores artifacts at `{bucket}/{app_name}/{user_id}/{session_id}/doc:{doc_id}.json`. No cross-user or cross-session access is possible — both user_id and session_id are in the path. No separate bucket per user is needed or appropriate.

| Environment | Bucket | Set via |
|-------------|--------|---------|
| Local dev | `your-project-id-artifacts` | `ADK_ARTIFACT_BUCKET` in `backend/.env` |
| Test | `your-project-id-test-artifacts` | Cloud Run env var (Terraform) |
| Production | `your-project-id-prod-artifacts` | Cloud Run env var (Terraform) |

The raw file upload bucket holds binary originals (`.docx`, `.pdf`). The artifact bucket holds processed JSON — different retention policy (artifacts can expire; originals retained), different access pattern (artifact service reads frequently; upload bucket is write-once). Keep them separate.

**Why not LOGS_BUCKET_NAME**: That bucket holds OTEL prompt/response telemetry with log-oriented retention. Artifacts are operational data that needs different lifecycle rules.

**Future (v6.2): Skill-scoped bucket access**
If a skill is wired to a GCS bucket (via `bucket_folders` in `tool_configs`), documents from that folder could be stored in the skill's bucket rather than the global artifact bucket. Enables skill-isolated data sovereignty for enterprise customers. Out of scope for this sprint.

### Local Dev with Real GCS

Add to `backend/.env`:

```bash
# ADK artifact storage — required for documents to be visible to AI
# Local dev: use dev GCS bucket (ADC credentials work for your-project-id)
# Omit to use in-memory (lost on server restart; documents won't persist across sessions)
ADK_ARTIFACT_BUCKET=your-project-id-artifacts
```

Add to `backend/.env.example` with the same comment.

`make dev` already injects `.env` vars inline. No changes to the Makefile needed.

**Testing GCS locally:**
1. `gcloud auth application-default login` (already required for Firestore)
2. Set `ADK_ARTIFACT_BUCKET=your-project-id-artifacts` in `.env`
3. `make dev` — artifact service uses the dev GCS bucket
4. Uploads → artifacts written to GCS → visible across server restarts

For CI (no GCS credentials): omit `ADK_ARTIFACT_BUCKET` → `InMemoryArtifactService` singleton → unit tests work without GCS.

### What Stays the Same

- **Firestore `parsed_documents`** — source of truth for the file browser, parse status, folder membership, filename, access control.
- **Upload pipeline** — no changes. AILANG Parse runs as before. Artifact loading is lazy (on session start), not at upload time.
- **`list_documents` / `get_document_content`** — still in `TOOL_REGISTRY` for agent-driven document discovery and section retrieval. Add to a skill's `tools: [...]` in Firestore to enable.
- **`retrieve_artifact`** — still always-on for the `_handle_large_output` offloading pattern.
- **Document-to-conversation relationship** — `document_id` in `chat_sessions` Firestore already tracked by `make_session_tracker`. The UI queries this to show "conversations related to this document."

### Multiple Documents Per Session

A session can have more than one document in context (e.g., "Compare these two contracts"):

```python
# session state
state = {
    "document_id": "primary_doc_id",   # existing field — first document
    "document_ids": ["doc1", "doc2"],   # new field — all documents in session
}
```

`make_document_loader` iterates `document_ids` (falling back to `document_id` if only one). Each document saved as `doc:{doc_id}.json`. `load_artifacts_tool` lists all and injects on demand.

Multi-document session creation: frontend sends `document_ids: [...]` in the stream request body. Single-document path unchanged.

## API Changes

### Modified: POST `/api/skills/{skill_id}/stream`

Add optional body fields:

```typescript
{
  message: string,
  session_id?: string,
  document_id?: string,        // single document context (existing concept, now wired)
  document_ids?: string[],     // multi-document context (new)
}
```

No breaking change — both fields are optional and default to empty/no-doc session.

### New environment variable

| Variable | Required in | Default | Effect |
|----------|-------------|---------|--------|
| `ADK_ARTIFACT_BUCKET` | Production + local dev with GCS | InMemory | GCS bucket for session artifact storage |

## Migration

No database migration. Artifacts are session-ephemeral — they're rebuilt from Firestore on session start. Existing sessions have no artifacts; the first turn of any session with a `document_id` in state populates them. No backfill needed.

## Testing Strategy

### Unit tests (`tests/tool_tests/`)

```python
async def test_document_loader_saves_artifact():
    """make_document_loader saves doc:{id}.json on first turn."""
    mock_ctx = Mock()
    mock_ctx.state = {}
    with patch("tools.documents.context.build_document_context", return_value=("md", [{"type": "paragraph", "text": "hi", "page": 1}])):
        loader = make_document_loader("doc123")
        loader(mock_ctx)
    mock_ctx.save_artifact.assert_called_once()
    args = mock_ctx.save_artifact.call_args
    assert args.kwargs["filename"] == "doc:doc123.json"
    assert args.kwargs["artifact"].inline_data.mime_type == "application/json"
    assert mock_ctx.state["app:docs_loaded"] is True

async def test_document_loader_is_noop_on_subsequent_turns():
    """Document loader skips if app:docs_loaded already set."""
    mock_ctx = Mock()
    mock_ctx.state = {"app:docs_loaded": True}
    loader = make_document_loader("doc123")
    loader(mock_ctx)
    mock_ctx.save_artifact.assert_not_called()

async def test_document_loader_failure_sets_error_state():
    """Firestore failure sets app:doc_load_error, does not raise."""
    mock_ctx = Mock()
    mock_ctx.state = {}
    with patch("tools.documents.context.build_document_context", side_effect=Exception("Firestore down")):
        loader = make_document_loader("doc123")
        loader(mock_ctx)
    assert "app:doc_load_error" in mock_ctx.state
    assert mock_ctx.state["app:docs_loaded"] is True

def test_load_artifacts_tool_in_every_agent():
    """Every agent has load_artifacts in its tool list."""
    agent = create_agent(sample_skill_config, sample_user)
    assert any(t.name == "load_artifacts" for t in agent.tools)
```

### Integration tests (`tests/integration/`)

```python
@pytest.mark.integration
async def test_document_content_visible_to_agent():
    """Agent answers document question after before_agent_callback populates artifact."""
    # Pre-populate Firestore with a parsed doc
    # Create session with document_id in state
    # Run one turn
    # Assert AI response mentions content from the doc
```

### ADK eval (`tests/eval/evalsets/documents.evalset.json`)

```json
[
  {
    "name": "cite_page_from_document",
    "description": "Agent cites page number when answering from uploaded document",
    "initial_session_artifacts": {"doc:doc1.json": "<blocks JSON>"},
    "query": "What was Q1 revenue and where is that stated?",
    "expected_tool_use": [{"tool_name": "load_artifacts"}],
    "reference": "Q1 revenue was $42M (page 1, under Revenue Summary)."
  },
  {
    "name": "multi_document_comparison",
    "description": "Agent compares content across two documents",
    "initial_session_artifacts": {
      "doc:doc1.json": "<contract A blocks>",
      "doc:doc2.json": "<contract B blocks>"
    },
    "query": "What are the key differences between these two contracts?",
    "expected_tool_use": [{"tool_name": "load_artifacts"}],
    "reference": "Contract A has a 12-month term; Contract B has 24 months..."
  }
]
```

## Success Criteria

- [ ] Clicking a document in the file browser and sending the first message → AI has access to the document's full structured content (no user configuration required)
- [ ] AI can cite specific pages and sections with block-level precision
- [ ] Two documents open in one session → AI can reference both and compare
- [ ] `app:doc_load_error` in state → AI receives the error message and communicates it to the user
- [ ] Local dev with `ADK_ARTIFACT_BUCKET=your-project-id-artifacts` works end-to-end (GCS, not InMemory)
- [ ] `load_artifacts` tool present in every agent (unit test)
- [ ] Re-uploading a document → next conversation start gets fresh content (no stale artifact, because artifacts are session-ephemeral)
- [ ] `ADK_ARTIFACT_BUCKET` in `.env.example`, Cloud Run Terraform, and smoke test
- [ ] Unit tests pass: `make test-fast`
- [ ] Integration test passes: `make test`

## Related Documents

- [file-browser.md](file-browser.md) — Upload UI, parse status, folder management (implemented ✅)
- [chat-session-history.md](chat-session-history.md) — Session → document_id tracking (implemented ✅)
- [local-dev-cli.md](local-dev-cli.md) — Future `aitana docs list` command
- [ADK Artifacts documentation](https://adk.dev/artifacts/) — authoritative reference
- ADK source: `google.adk.tools.load_artifacts_tool.LoadArtifactsTool` (verified v1.29.0)
- Rockwool reference: `worklog/resources/rockadk/file_search_agent.py`

## Open Questions

1. **`CallbackContext.save_artifact` async or sync?** The ADK docs say callbacks can be async, but our current `make_before_agent` is synchronous. Need to check if `CallbackContext.save_artifact` in ADK 1.29.0 is a coroutine — if so, `make_document_loader` must be `async def` and `_composed_before_agent` must also become async.

2. **`app_name` exact value**: The artifact key includes `app_name`. This is determined by the ADK runner at startup. Add `log.info("ADK app_name=%s", ...)` at startup to confirm it matches what `make_document_loader` uses. If the runner derives it differently for the custom SSE path vs the `/run` path, we need a shared constant.

3. **Multi-document UX**: When are multiple documents added to a session? Is it on session create only, or can documents be added mid-conversation? If mid-conversation, the `before_agent_callback` guard (`app:docs_loaded`) would prevent re-loading. We may need a `load_document(doc_id)` tool that can be called by the user ("add this document to our conversation") at any turn.

4. **Large documents and context limits**: For documents >100K tokens of parsed JSON, the LLM context window fills up. The `_handle_large_output` offloading helps after tool calls, but the artifact JSON is injected before the model call. May need a `max_artifact_bytes` truncation strategy, or the `section` parameter from `get_document_content` as a fallback for targeted extraction.

5. **Artifact bucket `your-project-id-artifacts` creation**: Does this bucket already exist? Needs to be created in Terraform if not. Or can we reuse an existing dev bucket with a `adk-artifacts/` prefix?
