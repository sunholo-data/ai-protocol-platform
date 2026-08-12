# Tools Porting Guide

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 1 week
**Scope**: Backend
**Dependencies**:
  - [Agent Factory](implemented/agent-factory.md) ✅ — stub registry + `artifact_tools.py` already wired
  - [Session & Memory](implemented/session-and-memory.md) ✅ — `GcsArtifactService` + `retrieve_artifact` already implemented
  - [Document UI](../v6.1.0/document-ui.md) ✅ — document lifecycle, Firestore schema, AILANG Parse pipeline
  - [Streaming & Protocols](implemented/streaming-and-protocols.md) ✅ — AG-UI tool-call event flow
**Created**: 2026-04-10
**Last Updated**: 2026-04-23

## Problem Statement

v6 needs the 8 tool stubs in `backend/adk/tools.py` replaced with real implementations, and the document processing pipeline (`POST /api/documents/upload` + AILANG Parse + Firestore) built out. The agent factory and artifact service are already wired; tools are the missing last layer.

**Current State:**
- `backend/adk/tools.py` — all 8 tools are stubs returning mock strings
- `backend/adk/artifact_tools.py` — `retrieve_artifact` **already implemented** (large content offload)
- `backend/adk/callbacks.py` — `_handle_large_output` **already implemented** (artifact save trigger)
- `backend/tools/` — empty `__init__.py` files only
- `backend/tools/code_execution/` — empty stub
- `backend/tools/mcp/` — empty stub
- No `POST /api/documents/upload` endpoint exists yet
- AILANG Parse is a dependency in `pyproject.toml` but has no v6 wrapper

**Impact:**
- Agent returns mock data for every tool call — unusable in production
- Document workspace is blocked: no upload → parse → store pipeline
- Code execution works for Gemini only (ADK built-in), but not wired

## Goals

**Primary Goal:** Replace all stubs with real implementations, with AILANG Parse as the backbone of document processing and ADK native tools used wherever available.

**Success Metrics:**
- All 8 tools callable via skill config `tools:` list
- Document upload → parsed blocks in Firestore in <3s (deterministic formats) / <10s (AI formats)
- Agent receives document content in the right format for each task type (markdown for chat, blocks for extraction)
- Zero Sunholo/LangChain imports in `backend/tools/`
- All tool tests pass (`cd backend && pytest tests/tool_tests/`)

**Non-Goals:**
- New tools not in v5 (future)
- Tool UI rendering (handled by streaming-and-protocols.md / A2UI)
- MCP tool federation — handled separately in protocols sprint (MCP server already ships)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Tool execution dominated by external service latency |
| 2 | EARNED TRUST | +1 | AILANG Parse extracts structure deterministically — agent sees exactly what the document contains |
| 3 | SKILLS, NOT FEATURES | 0 | Tools are infrastructure behind skills |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | ADK native tools for Gemini; specialist sub-agents for Claude/OpenAI; AILANG for deterministic formats, Gemini multimodal fallback for AI formats |
| 5 | GRACEFUL DEGRADATION | +1 | AILANG unavailable → Gemini fallback; large content → artifact offload; tool error → error string not exception |
| 6 | PROTOCOL OVER CUSTOM | +1 | ADK built-ins (`VertexAiSearchTool`, `GoogleSearchTool`, `BuiltInCodeExecutor`) where available; `AgentTool` sub-agent pattern for non-Gemini |
| 7 | API FIRST | 0 | Backend-internal tools; `POST /api/documents/upload` is the API surface |
| 8 | OBSERVABLE BY DEFAULT | 0 | ADK callbacks already trace tool calls |
| 9 | SECURE BY CONSTRUCTION | +1 | URL validation (no RFC-1918, no file://); sandbox execution; documents scoped per-user-per-skill |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Purely backend |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ |

---

## Architecture

### What Already Exists (Do Not Re-Implement)

| Component | File | What it does |
|-----------|------|-------------|
| Artifact retrieval tool | `backend/adk/artifact_tools.py` | `retrieve_artifact(artifact_id, section?)` — loads content from `GcsArtifactService` |
| Large output offload | `backend/adk/callbacks.py` | `_handle_large_output` — saves tool responses >50K chars as artifacts automatically |
| GCS artifact service | `fast_api_app.py` | `GcsArtifactService(bucket=LOGS_BUCKET, prefix="artifacts/v6/")` — wired into ADK runner |
| Stub tool registry | `backend/adk/tools.py` | `TOOL_REGISTRY` dict + `resolve_tools()` — shape is correct, callables are stubs |
| Agent factory | `backend/adk/agent.py` | `create_agent()` wires tools, `retrieve_artifact` already registered |

### Storage Layers

| What | Where | Why |
|------|-------|-----|
| Original uploaded files | GCS (`LOGS_BUCKET_NAME`) | Source of truth, immutable |
| Parsed document blocks + A2UI | Firestore `parsed_documents/{docId}` | Indexed, queryable, editable |
| Large tool output cache | GCS via `GcsArtifactService` (ADK) | Keeps context window lean; `retrieve_artifact` fetches sections on demand |
| Session state | ADK `VertexAiSessionService` | Agent Engine managed |

**Critical distinction:** Firestore `parsed_documents` is NOT the same as the ADK artifact service. Documents are stored in Firestore after AILANG Parse; large tool outputs are cached in GCS via ADK artifacts. These are separate concerns.

### Native vs. Custom Tool Strategy

| Tool | Gemini | Claude / OpenAI |
|------|--------|-----------------|
| AI Search | `VertexAiSearchTool` (model built-in) | `AgentTool(SearchAgent)` — Gemini sub-agent |
| Google Search | `GoogleSearchTool` (model built-in) | `AgentTool(SearchAgent)` — same sub-agent |
| URL fetch | `UrlContextTool` (model built-in) | ADK `load_web_page` FunctionTool |
| Code execution | `BuiltInCodeExecutor` via `code_executor=` param | `AgentTool(CodeAgent)` — Gemini sub-agent |
| List documents | Custom FunctionTool (Firestore query) | Same |
| Get document content | Custom FunctionTool (Firestore + blocks) | Same |
| Structured extraction | `after_agent` callback | Same |
| MCP tools | ADK `McpToolset` | Same |

The `SearchAgent` sub-agent hosts `VertexAiSearchTool` + `GoogleSearchTool` + `UrlContextTool` — all three native Gemini tools in one agent. Non-Gemini skill agents call it via `AgentTool(SearchAgent)`.

### AILANG Parse — Context Format Strategy

AILANG Parse supports multiple output formats. The right format depends on the use case:

| Use case | Format | Rationale |
|----------|--------|-----------|
| Agent general chat | `"markdown"` via `blocks_to_markdown(blocks)` | Compact, readable, low token cost |
| Extraction tool input | `"blocks"` JSON directly | Preserves table structure, tracked changes as typed objects, section hierarchy — lossy in markdown |
| Frontend rendering | `"a2ui"` | `A2UIViewer` renders directly; stored in `parsed_documents.a2uiComponents` |
| Large doc summary | `"markdown+metadata"` | YAML frontmatter with stats + markdown body |

**Key insight:** `blocks_to_markdown()` flattens tables to markdown table syntax and interspeses tracked changes as annotations. For extraction tasks where the agent needs to reason over table cells, address specific sections, or distinguish original content from tracked changes, pass `blocks` JSON directly. `build_document_context()` in `skills/document_context.py` should support a `mode=` parameter.

---

## Tool-by-Tool Design

### 1. Document Pipeline — `tools/documents/`

This is the most critical piece. The entire document workspace depends on it.

**Files:**
- `backend/tools/documents/ailang_parse.py` — AILANG Parse client (from v5 `ailang_parse_client.py`)
- `backend/tools/documents/upload.py` — upload handler backing `POST /api/documents/upload`
- `backend/tools/documents/context.py` — `build_document_context(doc_id, mode="markdown"|"blocks")`
- Agent tools: `list_documents` + `get_document_content` FunctionTools in `backend/adk/tools.py`

**AILANG Parse port** (from `v5/backend/tools/ailang_parse_client.py`, 485 LOC):
- Replace `from my_log import log` → `import logging; log = logging.getLogger(__name__)`
- Replace `from tools.tool_cache import file_cache, get_cache_key` → simple `functools.lru_cache` or TTL dict
- Everything else (`DocParse`, `ParseOutcome`, signed URL generation, GCS download fallback) copies verbatim
- Two strategies: `parse_url()` (signed HTTPS URL, Cloud Run) | `parse_file()` (local download fallback)

**Upload endpoint (`POST /api/documents/upload`):**
```
Receive file
    → Upload to GCS (Firebase Storage)
    → AILANG Parse: parse_url(signed_url, "blocks") for deterministic formats
    → Gemini multimodal fallback for PDFs, images (pass bytes as types.Part)
    → Store in Firestore parsed_documents/{docId}:
        blocks, a2uiComponents (from a2ui_formatter), metadata, summary
    → Return ParsedDocumentResponse
```

**Agent tools:**
```python
async def list_documents(skill_id: str | None = None, tool_context: ToolContext = None) -> str:
    """List parsed documents available in the workspace."""
    # Query Firestore parsed_documents for user's docs

async def get_document_content(
    doc_id: str,
    section: str | None = None,
    mode: str = "markdown",  # "markdown" | "blocks"
    tool_context: ToolContext = None,
) -> str:
    """Get content of a parsed document. Use mode='blocks' for extraction tasks."""
    # Load from Firestore; apply user edits via apply_edits(); 
    # return blocks_to_markdown() or JSON blocks per mode
```

### 2. Search Sub-Agent — `tools/search_agent.py`

```python
from google.adk.agents import LlmAgent
from google.adk.tools import GoogleSearchTool, VertexAiSearchTool
from google.adk.tools.url_context_tool import UrlContextTool

def create_search_agent(datastore_id: str | None = None) -> LlmAgent:
    tools = [GoogleSearchTool(), UrlContextTool()]
    if datastore_id:
        tools.append(VertexAiSearchTool(data_store_id=datastore_id))
    return LlmAgent(
        name="search_agent",
        model="gemini-2.5-flash",
        description="Searches the web and knowledge base using native Gemini tools.",
        instruction="Use the available search tools to find relevant information.",
        tools=tools,
    )
```

Registered in `TOOL_REGISTRY` as an `AgentTool(create_search_agent(...))`. For Gemini skill agents, `VertexAiSearchTool` + `GoogleSearchTool` are added directly to the agent instead (model built-ins can't go through `AgentTool`).

### 3. URL Processing — `tools/url_processing.py`

Thin wrapper around ADK's existing `load_web_page` FunctionTool + URL validation:

```python
from google.adk.tools.load_web_page import load_web_page

async def url_processing(url: str, tool_context: ToolContext = None) -> str:
    """Fetch and extract text content from a URL."""
    # Validate: block file://, RFC-1918 IPs, localhost
    _validate_url(url)
    return load_web_page(url)
```

### 4. Structured Extraction — `tools/structured_extraction.py`

Runs as `after_agent_callback`, not a FunctionTool. Triggered when `app:extraction_schema` is set in session state:

```python
async def structured_extraction_callback(callback_context: CallbackContext) -> None:
    schema = callback_context.state.get("app:extraction_schema")
    if not schema:
        return
    # Get document blocks from session state (set by get_document_content in blocks mode)
    blocks_json = callback_context.state.get("temp:document_blocks")
    # Run extraction against schema using blocks (not markdown) for fidelity
    # Save result to artifact if large; return structured JSON
```

**Key:** extraction operates on `blocks` JSON, not markdown. The `get_document_content(mode="blocks")` tool call populates `temp:document_blocks` in session state for the extraction callback to consume.

### 5. Code Execution — `tools/code_execution/`

- **Gemini**: `BuiltInCodeExecutor()` set via `code_executor=` on the agent (not in `tools=` list)
- **Claude/OpenAI**: `AgentTool(CodeAgent)` where `CodeAgent` is a Gemini 2.5 Flash agent with `code_executor=BuiltInCodeExecutor()`

```python
from google.adk.code_executors import BuiltInCodeExecutor

def create_code_agent() -> LlmAgent:
    return LlmAgent(
        name="code_agent",
        model="gemini-2.5-flash",
        description="Executes Python code in a sandbox.",
        instruction="Execute the requested code and return the output.",
        code_executor=BuiltInCodeExecutor(),
    )
```

### 6. MCP Registry — `tools/mcp/registry.py`

From v5 `mcp_servers.py` (no Sunholo deps). Returns `McpToolset` instances for dynamic tool discovery:

```python
async def get_mcp_tools(server_names: list[str]) -> list[MCPToolset]:
    """Load named MCP servers from Firestore config, return McpToolset instances."""
```

---

## Implementation Plan

### Phase 1: Document Pipeline (~2 days) — HIGHEST PRIORITY

- [ ] Port `ailang_parse_client.py` → `backend/tools/documents/ailang_parse.py` (strip `my_log` + `tool_cache`, keep everything else)
- [ ] `backend/tools/documents/upload.py` — `POST /api/documents/upload` handler: GCS upload → AILANG Parse → Firestore
- [ ] `backend/tools/documents/context.py` — `build_document_context(doc_id, mode="markdown"|"blocks")`
- [ ] Replace `_stub_file_browser` → `list_documents` + `get_document_content` FunctionTools in `backend/adk/tools.py`
- [ ] Wire upload route into `fast_api_app.py`
- [ ] Tests: `test_ailang_parse.py`, `test_upload.py`, `test_document_context.py`

### Phase 2: Search + URL (~1 day)

- [ ] `backend/tools/search_agent.py` — `create_search_agent()` with `VertexAiSearchTool` + `GoogleSearchTool` + `UrlContextTool`
- [ ] `backend/tools/url_processing.py` — `load_web_page` wrapper + URL validation
- [ ] Update `resolve_tools()` in `backend/adk/tools.py`: Gemini → add native tools directly; Claude/OpenAI → `AgentTool(create_search_agent())`
- [ ] Tests: `test_search_agent.py`, `test_url_processing.py`

### Phase 3: Structured Extraction (~1 day)

- [ ] `backend/tools/structured_extraction.py` — `after_agent` callback; operates on `blocks` JSON not markdown
- [ ] Update `build_document_context()` to populate `temp:document_blocks` in session state when `mode="blocks"`
- [ ] Copy extraction schemas from v5 `backend/tools/schemas/` if present
- [ ] Tests: `test_structured_extraction.py`

### Phase 4: Code Execution (~1 day)

- [ ] `backend/tools/code_execution/gemini_executor.py` — `BuiltInCodeExecutor` wiring helper
- [ ] `backend/tools/code_execution/code_agent.py` — `create_code_agent()` Gemini sub-agent
- [ ] Update `create_agent()` in `adk/agent.py`: if `code_execution` in tools and model is Gemini → set `code_executor=`; otherwise → add `AgentTool(create_code_agent())`
- [ ] Tests: `test_code_execution.py`

### Phase 5: MCP Registry + Final Wiring (~1 day)

- [ ] `backend/tools/mcp/registry.py` — from v5 `mcp_servers.py` (no Sunholo)
- [ ] Final `TOOL_REGISTRY` in `backend/adk/tools.py` — all stubs replaced, model-aware routing in `resolve_tools()`
- [ ] Integration test: agent with `list_documents` + `ai_search` + `get_document_content` tools end-to-end
- [ ] `cd backend && pytest tests/ -v --tb=short` — full suite green

---

## Testing Strategy

### Per-tool tests (`tests/tool_tests/`)
- Mock AILANG Parse API (`responses` or `pytest-httpx`)
- Mock Firestore (`google.cloud.firestore` with `unittest.mock`)
- Mock GCS client for upload/signed URL
- Mock ADK `VertexAiSearchTool` responses
- Test happy path + error cases for each tool

### Integration test (`tests/integration/`)
- `test_agent_with_tools.py` — ADK `Runner` with `InMemorySessionService` + `InMemoryArtifactService`; mock external services; verify tool calls appear in agent events

---

## Security Considerations

- URL validation: block `file://`, RFC-1918 IPs (10.x, 172.16-31.x, 192.168.x), localhost
- Documents scoped per-user-per-skill via `AccessContext` — same check as skills/buckets
- AILANG Parse: uses signed URLs (15-min expiry); no raw GCS credentials in tool layer
- Code execution: ADK sandbox only — no host filesystem access
- MCP: server config from Firestore only; no user-supplied server URLs in production

## Performance Targets

| Tool | Target | Notes |
|------|--------|-------|
| AILANG Parse (deterministic) | <2s | Signed URL → API → blocks |
| AILANG Parse (fallback download) | <5s | GCS download + upload |
| Gemini multimodal (PDF/image) | <10s | Gemini API latency |
| AI Search | <5s | Vertex Search API |
| `list_documents` | <200ms | Firestore index read |
| `get_document_content` | <300ms | Firestore doc read + blocks assembly |
| Code execution | 3-10s | ADK sandbox |

## Open Questions (Resolved)

- **AILANG Parse as standalone agent tool?** No — runs in upload pipeline. Agent accesses results via `list_documents` / `get_document_content`. AILANG Parse is infrastructure, not a tool the LLM calls directly.
- **Markdown vs. blocks for agent context?** Both — `mode="markdown"` for chat, `mode="blocks"` for extraction. `structured_extraction` always uses blocks.
- **Iterative search sub-agent?** Deferred. `SearchAgent` provides single-hop search; iterative refinement is a future sub-agent built on top.
- **Extraction schemas?** Copy all schemas from `v5/backend/tools/schemas/` if directory exists. Seed empty if not — schemas are skill-config driven.

## Related Documents

- [Session & Memory](implemented/session-and-memory.md) — `GcsArtifactService`, `retrieve_artifact`, `_handle_large_output` (all already implemented)
- [Document UI](../v6.1.0/document-ui.md) — `parsed_documents` Firestore schema, AILANG Parse pipeline, A2UI rendering, `build_document_context()`
- [Agent Factory](implemented/agent-factory.md) — stub registry shape, `create_agent()`, `retrieve_artifact` already wired
- [Streaming & Protocols](implemented/streaming-and-protocols.md) — AG-UI tool-call event flow, A2UI-in-AG-UI pattern
- [Migration to v6](../v5.0.0/migration-to-v6.md) — v5 tool feature map (lines 140-206)

---

## Implementation Report

**Completed**: 2026-04-23
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
