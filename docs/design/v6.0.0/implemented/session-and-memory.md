# Session & Memory

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 4 days
**Scope**: Backend
**Dependencies**: [Agent Factory](implemented/agent-factory.md), [Skills Data Model](implemented/skills-data-model.md)
**Created**: 2026-04-10
**Last Updated**: 2026-04-22
**Completed**: 2026-04-22

## Problem Statement

v5's memory system is a major limitation: 10 files x 1MB max per assistant, no semantic search, manual management. ADK provides native `SessionService` and `MemoryService` that replace this entirely, but the integration with Firestore needs design:

- How does ADK SessionService sync with Firestore for persistence?
- How does conversation history compaction work across different model context windows?
- How does MemoryService's semantic search integrate with existing Vertex AI Search?
- How does the content-limiting strategy change with ADK artifacts?

**Current State:**
- `backend/adk/session.py` exists but is empty
- v5 has `assistant_memory.py` (295 lines) — file-based, limited
- v5 has `limit_content.py` (400 lines) — custom content limiting
- ADK provides `InMemorySessionService`, Firestore-backed `SessionService`, and `MemoryService`

**Impact:**
- Blocks conversation persistence (sessions must survive server restarts)
- Blocks cross-session recall (memory)
- Blocks long conversations (context window management)

## Goals

**Primary Goal:** Implement persistent sessions and semantic memory using ADK's native services, backed by Firestore, with model-aware context compaction.

**Success Metrics:**
- Sessions persist across server restarts (Firestore-backed)
- Memory recalls relevant context from past sessions (semantic search)
- Context compaction prevents token overflow for all model sizes
- Content-limiting handles 500-page documents without Gemini summarization costs

**Non-Goals:**
- Custom memory service (use ADK's native implementation)
- Cross-skill memory sharing (each skill has independent memory)
- Memory export/import

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Context compaction prevents growing latency; artifacts keep context lean |
| 2 | EARNED TRUST | +1 | Memory provides source context from past sessions for grounded responses |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to users |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Model-aware compaction intervals (Gemini=10, Claude=5) |
| 5 | GRACEFUL DEGRADATION | +1 | InMemorySessionService as fallback if Firestore unavailable |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK SessionService, MemoryService, ArtifactService — replaces 700 lines of v5 custom code |
| 7 | API FIRST | 0 | Backend-internal, not API-facing |
| 8 | OBSERVABLE BY DEFAULT | 0 | Covered by ADK's built-in tracing |
| 9 | SECURE BY CONSTRUCTION | +1 | Sessions isolated per-user-per-skill, temp state auto-cleared |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Purely backend |
| | **Net Score** | **+6** | Threshold: >= +4 |

## Design

### Overview

Three ADK mechanisms handle persistence: (1) `SessionService` for conversation state, (2) `MemoryService` for cross-session semantic recall, and (3) Artifacts + context compaction for large content management. All backed by Firestore/GCS.

### 1. Session Service (Conversation State)

ADK's `SessionService` persists conversation history and state per user per skill.

```python
# backend/adk/session.py

import os
from google.adk.sessions import VertexAiSessionService

AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID")

def get_session_service() -> VertexAiSessionService:
    """Get Vertex AI Agent Engine session service (pay-per-use).
    
    See cloud-infrastructure.md for Agent Engine provisioning and local dev fallback.
    """
    return VertexAiSessionService(
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        agent_engine_id=AGENT_ENGINE_ID,
    )
```

**Session State Scopes:**

| Prefix | Scope | Use Case | Persistence |
|--------|-------|----------|-------------|
| (none) | Session | Current conversation state | Until session ends |
| `user:` | User | Cross-session per-user data | Permanent per user |
| `app:` | App/Skill | Shared across all users of a skill | Permanent per skill |
| `temp:` | Invocation | Temporary computation state | Cleared after each turn |

```python
# In tools or callbacks:
# Session-scoped (current conversation)
tool_context.state["current_document"] = doc_summary

# User-scoped (persists across sessions)
tool_context.state["user:preferences"] = {"model": "gemini-2.5-pro"}

# App-scoped (shared across users)
tool_context.state["app:extraction_schemas"] = schema_list

# Temp (cleared after this turn)
tool_context.state["temp:intermediate_result"] = partial_data
```

**Storage:** Agent Engine manages session storage internally — no Firestore collections or database schemas to provision. See [Cloud Infrastructure](implemented/cloud-infrastructure.md) for Agent Engine setup.

### 2. Memory Service (Cross-Session Recall)

ADK's `MemoryService` provides semantic search across past sessions. When a session ends, key information is extracted and stored for future recall.

```python
# backend/adk/session.py

from google.adk.memory import VertexAiMemoryBankService

def get_memory_service() -> VertexAiMemoryBankService:
    """Get Vertex AI Agent Engine memory service (semantic cross-session recall).
    
    Shares the same Agent Engine resource as the session service.
    See cloud-infrastructure.md for Agent Engine provisioning and local dev fallback.
    """
    return VertexAiMemoryBankService(
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ["GOOGLE_CLOUD_LOCATION"],
        agent_engine_id=AGENT_ENGINE_ID,
    )
```

**How it works:**
1. When a session ends, ADK extracts key facts/entities from the conversation
2. These are stored as memory entries with embeddings
3. At the start of a new session, relevant memories are retrieved via semantic search
4. Retrieved memories are injected into the agent's context

**Integration with agent:**

```python
from google.adk.agents import Agent

agent = Agent(
    name=skill_config.skillId,
    model=resolve_model(skill_config.agent.model),
    instruction=skill_config.agent.instruction,
    tools=tools,
    # Memory is wired at the Runner level, not agent level
)

runner = Runner(
    agent=agent,
    session_service=get_session_service(),
    memory_service=get_memory_service(),  # Enables cross-session recall
    app_name=skill_config.skillId,
)
```

### 3. Context Compaction (History Management)

ADK's `EventsCompactionConfig` automatically summarizes older conversation turns to prevent context overflow. The compaction interval should vary by model context window size.

```python
from google.adk.apps.app import EventsCompactionConfig  # Lives on App, not Agent

# Model-aware compaction intervals
# EventsCompactionConfig requires both compaction_interval and overlap_size
COMPACTION_CONFIG = {
    # Gemini: 1M context → compact less often
    "gemini-": EventsCompactionConfig(compaction_interval=10, overlap_size=3),
    # Claude: 200K context → compact more often
    "claude-": EventsCompactionConfig(compaction_interval=5, overlap_size=2),
    # OpenAI: varies (128K-1M) → conservative
    "gpt-": EventsCompactionConfig(compaction_interval=5, overlap_size=2),
    "o3": EventsCompactionConfig(compaction_interval=5, overlap_size=2),
}

def get_compaction_config(model_id: str) -> EventsCompactionConfig:
    """Get model-appropriate compaction config.
    
    NOTE: This config is set on App, not on individual Agents:
        App(agent=agent, events_compaction_config=get_compaction_config(model_id))
    """
    for prefix, config in COMPACTION_CONFIG.items():
        if model_id.startswith(prefix):
            return config
    return EventsCompactionConfig(compaction_interval=5, overlap_size=2)  # Default
```

**How compaction works:**
- Every N invocations (compaction_interval), ADK summarizes all events before the current window
- Recent events are kept in full fidelity
- Summary replaces old events in the session
- The agent sees: `[Summary of previous conversation] + [Recent full events]`

### 4. Content Management (Artifacts + Callbacks)

Replaces v5's `limit_content.py` (400 lines). Large content goes to GCS artifacts; only summaries enter the context window.

```python
# backend/adk/callbacks.py

async def handle_large_output(tool, args, tool_context, tool_response):
    """After-tool callback: save large outputs as artifacts."""
    content = str(tool_response)
    
    # Threshold: 50K chars (~12K tokens for Gemini)
    if len(content) > 50_000:
        artifact_id = f"{tool.name}_{tool_context.invocation_id}"
        
        # Save full content as GCS artifact
        await tool_context.save_artifact(
            filename=artifact_id,
            artifact=types.Part.from_text(content),
        )
        
        # Return summary reference instead
        char_count = len(content)
        preview = content[:2000]  # First 2K chars as preview
        return (
            f"[Full content saved as artifact '{artifact_id}' ({char_count} chars)]\n\n"
            f"Preview:\n{preview}\n\n"
            f"To access more content, request specific sections by topic."
        )
    
    return tool_response
```

**Artifact retrieval tool** — lets the agent request specific parts of saved artifacts:

```python
async def retrieve_artifact(
    artifact_id: str,
    section: str | None = None,
    tool_context: ToolContext = None,
) -> str:
    """Retrieve content from a previously saved artifact.
    
    Args:
        artifact_id: The artifact identifier.
        section: Optional section keyword to search within the artifact.
    
    Returns:
        The requested content from the artifact.
    """
    artifact = await tool_context.load_artifact(filename=artifact_id)
    if not artifact:
        return f"Artifact '{artifact_id}' not found."
    
    content = artifact.text
    if section:
        # Extract relevant section (simple keyword search)
        chunks = content.split("\n\n")
        relevant = [c for c in chunks if section.lower() in c.lower()]
        if relevant:
            return "\n\n".join(relevant[:5])  # Top 5 relevant chunks
    
    return content[:10_000]  # First 10K chars if no section specified
```

**GCS Artifact Service configuration:**

```python
from google.adk.artifacts import GcsArtifactService

def get_artifact_service() -> GcsArtifactService:
    return GcsArtifactService(
        bucket_name=settings.LOGS_BUCKET_NAME,  # Same bucket as v5
        prefix="artifacts/v6/",
    )
```

### Content Flow Diagram

```
[Document arrives (PDF, DOCX, etc.)]
    │
    ▼
[AILANG Parse] → markdown (if supported, <1s)
    │ or
[Gemini extraction] → text (fallback)
    │
    ▼
[after_tool_callback: handle_large_output()]
    │
    ├── Content < 50K chars → passes through to context
    │
    └── Content ≥ 50K chars
            ├── Full text → GCS Artifact (versioned)
            ├── Summary + preview → context window
            └── Metadata → session state
    │
    ▼
[Agent sees summary, can request artifact sections on demand]
    │
    ▼
[Context compaction summarizes older turns periodically]
```

### Architecture Diagram

```
[ADK Runner]
    │
    ├── VertexAiSessionService (Agent Engine)
    │       ├── events: conversation history
    │       ├── state: session/user/app/temp scopes
    │       └── compaction: automatic history summarization
    │
    ├── VertexAiMemoryBankService (Agent Engine — same resource)
    │       ├── session-end extraction
    │       ├── embedding storage
    │       └── retrieval at session start
    │
    └── GcsArtifactService (GCS bucket)
            ├── large content storage
            ├── versioning per session
            └── on-demand retrieval tool
```

## Implementation Plan

### Phase 1: Session Service (~1 day)
- [ ] Implement `backend/adk/session.py` — `get_session_service()` with Agent Engine backend
- [ ] Configure session state scopes (user:, app:, temp:)
- [ ] Wire into `process_skill_request()` in skill_processor.py
- [ ] Write tests: create session, resume session, state persistence

### Phase 2: Context Compaction (~1 day)
- [ ] Implement model-aware compaction config in `backend/adk/agent.py`
- [ ] Test compaction triggers for Gemini (interval=10) and Claude (interval=5)
- [ ] Verify compacted sessions maintain coherent conversation context

### Phase 3: Artifacts + Content Management (~1 day)
- [ ] Implement `handle_large_output()` callback
- [ ] Implement `retrieve_artifact()` tool
- [ ] Configure `GcsArtifactService` with existing GCS bucket
- [ ] Test: large document → artifact → summary in context → retrieve section

### Phase 4: Memory Service (closed 2026-04-27)
- [x] Configure `VertexAiMemoryBankService` (shares Agent Engine with sessions) — `adk/session.py:get_memory_service()`
- [x] Wire into the AG-UI middleware (`adk/agui.py:build_agui_adk_agent` + `protocols/agui.py:mount_skill_endpoint`) so the production skill stream uses the real Vertex memory bank instead of ag_ui_adk's silent `InMemoryMemoryService` fallback (root cause: `use_in_memory_services=True` with no explicit memory_service)
- [x] Add `load_memory_tool` + `preload_memory_tool` to the default per-skill tool set (`adk/agent.py`) — without these the model has no surface to query memory
- [x] Bootstrap Agent Engine resources in dev/test/prod and store IDs in Secret Manager (`docs/ops/deployed-urls.md`)
- [ ] Test: conversation 1 → end → conversation 2 → relevant recall (manual smoke for now; evalset coverage tracked separately)
- [ ] Tune `save_session_to_memory_on_cleanup` cadence (defaults to "on session timeout", may want explicit triggers)

## Migration & Rollout

**Infrastructure:**
- Agent Engine resource created per environment (see [Cloud Infrastructure](implemented/cloud-infrastructure.md))
- GCS artifacts in existing `LOGS_BUCKET_NAME` bucket under `artifacts/v6/` prefix

**What this replaces from v5:**
- `assistant_memory.py` (295 lines) → ADK MemoryService
- `limit_content.py` (400 lines) → Artifacts + after_tool_callback
- Custom history truncation → EventsCompactionConfig
- 10 files x 1MB limit → unlimited GCS artifacts

**Rollback Plan:** Fall back to `InMemorySessionService` (no persistence, but functional).

## Testing Strategy

### Backend Tests (pytest)
- [ ] Session CRUD: create, resume, list, delete
- [ ] State scopes: session, user:, app:, temp: — correct persistence behavior
- [ ] Compaction: verify history is summarized after N invocations
- [ ] Artifacts: save, load, version, retrieve section
- [ ] Large content: >50K chars → artifact created, summary in context
- [ ] Memory: facts from session 1 recalled in session 2

### Integration Tests
- [ ] Full flow: multi-turn conversation → server restart → resume
- [ ] Content management: upload large PDF → artifact → agent answers questions about it
- [ ] Cross-session: discuss topic in session 1 → reference in session 2

## Security Considerations

- Sessions isolated per user per skill (no cross-user access)
- Artifacts stored in authenticated GCS bucket (same as v5)
- Memory entries scoped per user (no cross-user recall)
- `temp:` state auto-cleared — no unintended persistence of sensitive intermediate data

## Performance Considerations

- Agent Engine session read: ~50ms (acceptable for session resume)
- Artifact save: ~100ms (GCS write)
- Artifact retrieve: ~50ms (GCS read)
- Memory search: ~200ms (Agent Engine MemoryBank)
- Compaction: runs in background, no user-facing latency

## Success Criteria

- [ ] Sessions persist across server restarts
- [ ] State scopes work correctly (user: persists, temp: clears)
- [ ] Large documents saved as artifacts, summaries in context
- [ ] Context compaction prevents token overflow
- [ ] Memory recalls relevant facts from past sessions
- [ ] All tests passing

## Open Questions

- Should artifact retention have a TTL (e.g., 90 days) or be permanent?
- Does compaction preserve tool call results or only text messages?

## Implementation Report

**Completed:** 2026-04-22
**Sprint:** SESSION-MEMORY — 4 milestones, all passing
**Actual effort:** ~45 min wall time (M1/M2/M3 parallel, M4 sequential); planned 1 day
**Commits:** `9e5cf4e` (M1–M3 parallel wave), M4 docs

### What was built

- **M1 — Compaction config** (`9e5cf4e`): `adk/session.py` exports `get_compaction_config(model_id)` returning model-tuned `EventsCompactionConfig` — Gemini gets `interval=10, overlap=3` (large context window); Claude/GPT/o1/o3 get `interval=5, overlap=2`. `app.py` `App()` now carries `events_compaction_config=get_compaction_config("gemini-2.5-flash")`. 5 new tests.
- **M2 — `retrieve_artifact` tool** (`9e5cf4e`): `adk/artifact_tools.py` exports async `retrieve_artifact(artifact_id, section, tool_context)` — no-section path returns first 10K chars; section keyword returns up to 5 matching paragraphs (case-insensitive); missing artifact → clear message, no exception. Registered on root agent (`app.py`) and in `create_agent()` (`adk/agent.py`). 7 new tests.
- **M3 — State-scope + callback tests** (`9e5cf4e`): `test_session_state_scopes.py` (17 tests) locks in `_handle_large_output` behaviour (>50K → artifact pointer, ≤50K → pass-through) and all three session state scope contracts. Key finding: `InMemorySessionService` fully implements `user:` and `app:` scopes via `_merge_state()` and `extract_state_delta()` — no mocks needed; `temp:` keys are silently dropped by `extract_state_delta` at both `create_session` and `append_event` time.
- **M4 — Docs + `.env.example` + smoke**: `backend/.env.example` created documenting all env vars (`AGENT_ENGINE_ID`, `ADK_ARTIFACT_BUCKET`, `SIGNED_URL_*`, `FIREBASE_PROJECT_ID`, `ALLOW_ORIGINS`). `smoke-deployed.sh` documents manual two-turn session continuity procedure with note explaining why it can't be a stateless HTTP probe. Design doc moved to `implemented/`.

### Deviations from the design doc

- **Service factories already shipped**: `get_session_service()`, `get_memory_service()`, `get_artifact_service()`, and URI helpers shipped in AGENT-FACTORY. This sprint only added the missing behaviour on top — compaction, retrieval tool, and test coverage. Actual LOC was ~260 (planned ~400).
- **`EventsCompactionConfig` is flagged `[EXPERIMENTAL]` by ADK**: emits a `UserWarning` at startup. Functional and live but Google may change the API. Accepted risk; documented inline.
- **Session continuity smoke is manual**: a stateless HTTP probe for two-turn session persistence would require a seeded skill ID and live Agent Engine. Documented the manual procedure in `smoke-deployed.sh` instead.

### Lessons

- **`InMemorySessionService` is surprisingly complete**: all three scope contracts (`user:`, `app:`, `temp:`) are enforced by the in-memory implementation — tests against it are meaningful, not just documentation.
- **Parallel M1/M2/M3 with non-overlapping files worked perfectly**: zero merge conflicts, all 29 new tests green on integration.
- **`retrieve_artifact` belongs next to callbacks**: co-locating the save (callbacks.py) and retrieve (artifact_tools.py) paths in `adk/` makes the full artifact lifecycle easy to find.

## Related Documents

- [Cloud Infrastructure](implemented/cloud-infrastructure.md) — Agent Engine provisioning, local dev fallback
- [Migration to v6](../v5.0.0/migration-to-v6.md) — Artifacts, sessions, content management (lines 495-596)
- [Agent Factory](implemented/agent-factory.md) — Compaction config, callback wiring
- [Tools Porting Guide](tools-porting-guide.md) — Tool outputs that trigger artifact storage
