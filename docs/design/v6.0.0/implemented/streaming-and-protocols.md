# Streaming & Protocols

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 1 week
**Scope**: Fullstack
**Dependencies**: [Agent Factory](implemented/agent-factory.md), [Skills Data Model](implemented/skills-data-model.md)
**Created**: 2026-04-10
**Last Updated**: 2026-04-22

## Problem Statement

v6's biggest architectural differentiator is its protocol stack: AG-UI for streaming, A2UI for declarative UI, MCP Apps for tool UIs, and A2A for agent discovery. v5 uses custom SSE streaming and bespoke rendering. The migration doc describes the protocol stack conceptually but doesn't specify:

- The exact AG-UI event flow from ADK through FastAPI to React
- How A2UI JSON is emitted by agents and rendered as React components
- How MCP Apps iframes are sandboxed and communicate with the host
- How these protocols interact (e.g., an AG-UI stream containing A2UI events)

**Current State:**
- `backend/protocols/` has empty `__init__.py` files
- `frontend/src/components/protocols/` directory exists but is empty
- Dependencies declared in `package.json` (AG-UI, CopilotKit, A2UI)
- No protocol implementation in v6

**Impact:**
- Blocks frontend chat interface (needs AG-UI streaming)
- Blocks rich UI rendering (needs A2UI + MCP Apps)
- Blocks agent discovery (needs A2A)

## Goals

**Primary Goal:** Implement the four-protocol stack so agents can stream text, render interactive UI components, display tool UIs, and discover each other.

**Success Metrics:**
- AG-UI: first token streams to frontend in <300ms after agent starts
- A2UI: agent returns a form → frontend renders interactive form
- MCP Apps: tool returns a chart → frontend renders in sandboxed iframe
- A2A: `/.well-known/agent.json` returns valid agent card with all skills

**Non-Goals:**
- Custom protocol extensions
- Protocol versioning/negotiation
- Cross-org A2A discovery (single-org)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | AG-UI streams first token in <300ms — this is the latency feature |
| 2 | EARNED TRUST | 0 | Transport layer — trust is in agent responses, not protocol |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to end users |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Doesn't affect model selection |
| 5 | GRACEFUL DEGRADATION | +1 | Plain text fallback when A2UI/MCP Apps unavailable |
| 6 | PROTOCOL OVER CUSTOM | +1 | **Entire purpose**: adopt AG-UI, A2UI, MCP, A2A over bespoke code |
| 7 | API FIRST | +1 | AG-UI is the universal transport for all channels |
| 8 | OBSERVABLE BY DEFAULT | 0 | Covered by existing ADK instrumentation |
| 9 | SECURE BY CONSTRUCTION | +1 | MCP Apps sandboxed (allow-scripts, no allow-same-origin) |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | CopilotKit renders events, backend decides content |
| | **Net Score** | **+6** | Threshold: >= +4 |

## Design

### Overview

The four protocols serve distinct layers:

```
Layer 4 — UI Description:  A2UI (declarative JSON) + MCP Apps (sandboxed HTML)
Layer 3 — Transport:       AG-UI (SSE event stream)
Layer 2 — Coordination:    A2A (discovery) + MCP (tools)
Layer 1 — Framework:       ADK (orchestration)
```

AG-UI is the transport — all other protocols ride on top of it. A2UI and MCP Apps events are embedded within the AG-UI event stream.

### Protocol 1: AG-UI (Streaming Transport)

AG-UI defines 16 canonical event types for agent-to-user communication. **ADK does not natively emit AG-UI events** — that translation is handled by [`ag-ui-adk`](https://pypi.org/project/ag-ui-adk/) (CopilotKit, v0.6.0 released 2026-04-06), an official middleware that wraps an ADK `Agent` and exposes it as an AG-UI-compatible FastAPI endpoint.

> **Verified 2026-04-10** during Phase 0.4 spike research for [v6-implementation-roadmap.md](v6-implementation-roadmap.md). The earlier note here speculating that `get_fast_api_app()` might handle AG-UI natively is **wrong** — the public ADK API has no AG-UI emitter. We use `ag-ui-adk` instead. The lower-level [`ag-ui-protocol`](https://pypi.org/project/ag-ui-protocol/) Pydantic models + `EventEncoder` are also available if we ever need to construct events ourselves.

#### Backend: Integration Pattern

```python
# backend/protocols/agui.py

from fastapi import FastAPI
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.agents import Agent

def mount_skill_endpoint(app: FastAPI, skill: Skill, agent: Agent) -> None:
    """Wrap an ADK Agent in ag-ui-adk and mount it as an AG-UI SSE endpoint."""
    adk_agent = ADKAgent(
        adk_agent=agent,
        app_name="aitana",
        # session_service injection — verify in spike whether this is supported
    )
    add_adk_fastapi_endpoint(
        app,
        adk_agent,
        path=f"/api/chat/{skill.id}",
        extract_headers=["x-user-id", "x-firebase-uid"],
    )
```

`ag-ui-adk` translates ADK Runner events into AG-UI events automatically. We do not write our own translator.

#### Backend: Event Flow

```
[ADK Agent.run()]
    │ yields ADK events
    ▼
[ag-ui-adk middleware]
    │ converts to AG-UI events + SSE framing
    ▼
[FastAPI SSE endpoint, mounted by add_adk_fastapi_endpoint]
    ▼
[Frontend CopilotKit AG-UI client]
    ▼
[React components update]
```

**Canonical AG-UI Events (all 16, emitted by `ag-ui-adk`):**

| Category | Event | When |
|----------|-------|------|
| Lifecycle | `RUN_STARTED` | Agent begins processing |
| Lifecycle | `RUN_FINISHED` | Agent completes successfully |
| Lifecycle | `RUN_ERROR` | Error occurred |
| Lifecycle | `STEP_STARTED` / `STEP_FINISHED` | Step within a run |
| Text | `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` | Streamed text message lifecycle |
| Text | `TEXT_MESSAGE_CHUNK` | Convenience event auto-expanding to start→content→end |
| Tools | `TOOL_CALL_START` / `TOOL_CALL_ARGS` / `TOOL_CALL_END` / `TOOL_CALL_RESULT` | Tool invocation lifecycle |
| Tools | `TOOL_CALL_CHUNK` | Convenience event |
| State | `STATE_SNAPSHOT` / `STATE_DELTA` / `MESSAGES_SNAPSHOT` | State synchronization |
| Reasoning | `REASONING_*` (start/content/end/encrypted) | For Claude/Gemini thinking modes |
| Special | `RAW`, `CUSTOM`, `META_EVENT` | Pass-through and side-band annotations |

**Spike-validated assumptions** (verify in [v6-implementation-roadmap.md M0.4](v6-implementation-roadmap.md#04-ag-ui-event-taxonomy--spike-05-day--de-risked-2026-04-10)):
- Custom session service injection works (or has a documented workaround)
- Firebase auth middleware composes correctly with `add_adk_fastapi_endpoint`
- ADK `FunctionTool` calls surface as `TOOL_CALL_*` events automatically
- `ResumabilityConfig` supports our per-document chat session model

### Verified Event Flow

**Date:** 2026-04-15
**Commit:** `83f53603` build → dev Cloud Run revision `platform-backend-*`
**Environment used:** live `platform-backend` Cloud Run service on `your-project-id`,
exercised via `backend/scripts/smoke_test_infra.py --only agui-spike` with an `--no-allow-unauthenticated`
ID-token bearer. Agent Engine telemetry + Vertex AI backend. Probe POSTs a minimal AG-UI
`RunAgentInput` ("say ok") to `/api/chat/spike` and reads the SSE stream.

#### Assumption verification

| # | Assumption | Verdict | Notes |
|---|------------|---------|-------|
| 1 | `ADKAgent(session_service=VertexAiSessionService(...))` is supported | **PASS (by source inspection)** | `ag_ui_adk/adk_agent.py` `__init__` in v0.6.0 exposes `session_service: Optional[BaseSessionService] = None`. `VertexAiSessionService` extends `BaseSessionService`, so the injection is type-compatible. `mount_skill_endpoint(..., session_service=...)` passes it through. Phase 1A.5 must smoke-test with a real `AGENT_ENGINE_ID` before depending on it. |
| 2 | Firebase auth middleware composes with `add_adk_fastapi_endpoint` | **DEFERRED to Phase 1A.1** | Not exercised in M2. `add_adk_fastapi_endpoint` registers a POST route that takes a raw `RunAgentInput` — FastAPI dependency-injection-based auth (`Depends(verify_firebase_token)`) should compose normally, but this is untested. Spike endpoint is explicitly unauthenticated (see `spikes/agui_harness/README.md`). |
| 3 | ADK `FunctionTool` calls surface as `TOOL_CALL_*` events automatically | **PENDING LIVE RUN (text path PASS)** | Plain-text path verified live (see Observed event sequence below — 6 events, RUN_STARTED…RUN_FINISHED). Tool-call path still pending a dedicated live run with a tool-invoking prompt; deferred to Phase 1A.5. Source inspection (`ag_ui_adk/event_translator.py`) confirms `TOOL_CALL_START / _ARGS / _END / _RESULT` mapping. |
| 4 | `ResumabilityConfig` supports per-document chat session model | **DEFERRED to Phase 1A.4** | Not trivially observable in a single-shot probe. `use_thread_id_as_session_id=True` on `ADKAgent` is the most promising knob — it makes the AG-UI `threadId` the ADK `session_id` directly, which aligns with document-centric sessions. Phase 1A.4 will verify this end-to-end with VertexAiSessionService + browser-driven reloads. |

#### Observed event sequence

Live probe against `https://your-service-url.example/api/chat/spike`
with prompt `"say ok"` (no tool use path — tool-call sequence still pending, see below):

```
RUN_STARTED
  TEXT_MESSAGE_START
  TEXT_MESSAGE_CONTENT
  TEXT_MESSAGE_END
  STATE_SNAPSHOT
RUN_FINISHED
```

6 events, round-trip 1.57s. Headers `Content-Type: text/event-stream` confirmed.

**Not yet verified in live run:** tool-call sub-sequence (`TOOL_CALL_START / _ARGS / _END
/ _RESULT`). The `"say ok"` prompt took the plain-text path. The offline evalset
`tests/eval/evalsets/spike_agent.evalset.json` has a `tool_call_get_current_time` case
but ADK evals don't assert on AG-UI event types. A dedicated live-run check with prompt
"What time is it?" is tracked for Phase 1A.5 rather than gating PHASE0-CLOSE.

**Surprise:** `STATE_SNAPSHOT` lands between `TEXT_MESSAGE_END` and `RUN_FINISHED`. Not in
our predicted sequence — `ag-ui-adk` emits it whenever ADK session state changes, even
without an explicit state tool call. Worth remembering for Phase 1A.4 (resumability).

#### Surprises

- `add_adk_fastapi_endpoint` registers a companion POST `/agents/state` route
  (see `ag_ui_adk/endpoint.py`) alongside the SSE endpoint. This is "EXPERIMENTAL"
  per the source but may help us skip implementing our own history-replay route for
  per-document sessions. Flag for Phase 1A.4.
- The middleware also exposes `capabilities: dict` on `ADKAgent.__init__`, which
  powers a `GET /capabilities` route. We can populate this from skill config rather
  than building our own introspection endpoint.
- `use_thread_id_as_session_id=False` by default — at scale this triggers an O(n)
  `list_sessions` scan on every request. Phase 1A.4 should enable it explicitly.
- `emit_messages_snapshot` defaults to `False`. CopilotKit's `/agents/state` endpoint
  retrieves history on demand instead; if we rely on a different AG-UI client we'll
  need to flip this.

#### Recommendations for Phase 1A.5

- Enable `use_thread_id_as_session_id=True` from the start — retrofitting it later
  is a behavior-change for existing threads.
- Test the Firebase-auth + `add_adk_fastapi_endpoint` combination *before* building
  skill-driven dynamic mounting, not after. A FastAPI `Depends(...)` on the SSE
  handler is the happy path but untested.
- The `extract_headers=["x-user-id", "x-firebase-uid"]` pattern maps headers into
  session state — align this with whatever middleware sets those headers post-auth
  so the ADK agent can read `session.state["user_id"]` without extra plumbing.
- Delete the `/api/chat/spike` mount and `spike_agent` before Phase 1A.5 ships.
  The spike endpoint is unauthenticated by design and is a deploy-footgun if left
  in place.

#### Frontend: CopilotKit Integration

```typescript
// frontend/src/providers/AGUIProvider.tsx

import { CopilotKit } from "@copilotkit/react-core";

export function AGUIProvider({ children }: { children: React.ReactNode }) {
  return (
    <CopilotKit
      runtimeUrl="/api/proxy/api/skill"
      agent="current-skill"
    >
      {children}
    </CopilotKit>
  );
}
```

```typescript
// frontend/src/hooks/useSkillAgent.ts

import { useCopilotChat } from "@copilotkit/react-ui";

export function useSkillAgent(skillId: string) {
  const { messages, append, isLoading, stop } = useCopilotChat({
    // AG-UI events are handled automatically by CopilotKit
  });

  const sendMessage = async (content: string) => {
    await append({ role: "user", content });
  };

  return { messages, sendMessage, isLoading, stop };
}
```

### Protocol 2: A2UI (Declarative UI)

A2UI lets agents describe UI components as JSON. The frontend renders them as React components.

v6 tracks **A2UI v0.9** (`@a2ui/react@0.9.0-alpha.0`, as of 2026-04-19). v0.9 renamed the optional component set from "Standard" to **"Basic"** and added a Python SDK — see the two authoring paths below.

#### Backend: Emitting A2UI

Agents include A2UI JSON in their responses. v6 has two authoring paths:

**Path A — hand-rolled prompt (current v6.0.0 approach).** Agent instruction tells the model to emit A2UI JSON between markers. Cheap to implement, fragile to schema drift.

**Path B — `a2ui-agent-sdk` (Python, new in A2UI v0.9).** `schema_manager.generate_system_prompt()` produces a schema-aware system prompt from a chosen catalog; `parse_response_to_parts()` extracts A2UI parts from the model response. Preferred once we start authoring A2UI from the backend in Phase 1B — it removes the hand-maintained JSON examples below and keeps the component vocabulary in lockstep with the renderer's version.

A2UI can be delivered via any of:
- Inline in text (between markers)
- As a separate content part
- Via a dedicated A2UI tool

```python
# Path A: A2UI via agent instruction (current v6.0.0)
instruction = """
When showing structured data, use A2UI components:

For forms:
```a2ui
{
  "type": "form",
  "fields": [
    {"name": "company", "type": "text", "label": "Company Name"},
    {"name": "amount", "type": "number", "label": "Invoice Amount"}
  ],
  "submitLabel": "Extract"
}
```

For data tables:
```a2ui
{
  "type": "table",
  "columns": ["Name", "Value", "Confidence"],
  "rows": [...]
}
```
"""
```

```python
# Path B: a2ui-agent-sdk (A2UI v0.9, Phase 1B candidate)
# pip install a2ui-agent-sdk
from a2ui_agent_sdk import SchemaManager, CatalogConfig

schema_manager = SchemaManager(catalog=CatalogConfig.from_path("basic"))
system_prompt = schema_manager.generate_system_prompt()  # schema-aware prompt
# ... run the model ...
parts = schema_manager.parse_response_to_parts(model_response)  # extract A2UI parts
```

> **Decision deferred:** adopt `a2ui-agent-sdk` when the backend starts authoring A2UI directly (Phase 1B). Alpha API — pin exact versions and re-verify before the July 2026 workshop.

#### Frontend: A2UI Renderer

```typescript
// frontend/src/components/protocols/A2UIRenderer.tsx

import { A2UIComponent } from "@a2ui/react";

interface A2UIRendererProps {
  json: A2UIPayload;
  onAction?: (action: string, data: unknown) => void;
}

export function A2UIRenderer({ json, onAction }: A2UIRendererProps) {
  return (
    <A2UIComponent
      spec={json}
      onAction={onAction}
      theme="aitana"
    />
  );
}
```

**Supported A2UI Components:**

| Component | Use Case | Example |
|-----------|----------|---------|
| `form` | User input collection | Extraction schema fields |
| `table` | Structured data display | Search results, extracted data |
| `chart` | Data visualization | Usage stats, financial data |
| `card` | Information display | Document summary, skill card |
| `button_group` | Action selection | Confirm/reject, format choice |
| `alert` | Notifications | Warnings, compliance flags |

### Protocol 3: MCP Apps (Tool UIs)

MCP Apps render interactive HTML UIs from tool outputs in sandboxed iframes.

#### Backend: MCP Apps Support

Tools can return `ui://` resource URIs that reference interactive UIs.

```python
# In a tool response:
async def search_with_ui(query: str, tool_context: ToolContext) -> str:
    results = await do_search(query)
    
    # Store interactive UI as MCP resource
    ui_html = render_search_dashboard(results)
    await tool_context.save_artifact(
        filename=f"search_ui_{tool_context.invocation_id}.html",
        artifact=types.Part.from_text(ui_html),
    )
    
    return f"Found {len(results)} results. [Interactive dashboard available]"
```

#### Frontend: MCP App Frame

```typescript
// frontend/src/components/protocols/MCPAppFrame.tsx

import { AppFrame } from "@mcp-ui/client";

interface MCPAppFrameProps {
  resourceUri: string;
  height?: number;
}

export function MCPAppFrame({ resourceUri, height = 400 }: MCPAppFrameProps) {
  return (
    <AppFrame
      src={resourceUri}
      sandbox="allow-scripts"
      style={{ width: "100%", height, border: "none", borderRadius: 8 }}
    />
  );
}
```

**Security:**
- Iframes use `sandbox="allow-scripts"` (no `allow-same-origin`)
- Communication via `postMessage` following MCP JSON-RPC spec
- No access to parent DOM, cookies, or localStorage
- Content-Security-Policy headers on iframe content

### Protocol 4: A2A (Agent Discovery)

A2A exposes skills as discoverable agents via a standard agent card.

#### Backend: Agent Card

```python
# backend/protocols/a2a.py

from fastapi import APIRouter

router = APIRouter()

@router.get("/.well-known/agent.json")
async def agent_card():
    """Serve A2A agent card listing all public skills."""
    skills = await list_skills(access_type="public")
    
    return {
        "name": "Aitana",
        "description": "AI skills platform",
        "url": settings.BASE_URL,
        "version": "6.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
        "skills": [
            {
                "id": skill.skillId,
                "name": skill.name,
                "description": skill.description,
                "tags": skill.tags,
                "inputModes": ["text"],
                "outputModes": ["text", "a2ui"],
            }
            for skill in skills
        ],
    }
```

### Protocol Interaction

All protocols flow through the AG-UI event stream:

```
[AG-UI SSE Stream]
    │
    ├── TEXT_MESSAGE_CONTENT → Plain text rendering
    │
    ├── TEXT_MESSAGE_CONTENT (with ```a2ui block) → A2UI renderer
    │       Frontend detects A2UI markers, extracts JSON, renders component
    │
    ├── TOOL_CALL_END (with ui:// in result) → MCP App frame
    │       Frontend detects ui:// URI, renders iframe
    │
    └── STATE_SNAPSHOT → State updates (e.g., progress indicators)
```

### Architecture Diagram

```
[ADK Agent]
    │ ADK events
    ▼
[AG-UI Translator] ─── backend/protocols/agui.py
    │ SSE events
    ▼
[FastAPI SSE Endpoint] ─── POST /api/skill/{id}/stream
    │
    ▼ (via /api/proxy)
[CopilotKit Provider] ─── frontend/src/providers/AGUIProvider.tsx
    │
    ├── [ChatMessages] ─── Plain text
    │       │
    │       ├── [A2UIRenderer] ─── Inline declarative UI
    │       │       └── Forms, tables, charts, alerts
    │       │
    │       └── [MCPAppFrame] ─── Sandboxed tool UIs
    │               └── Interactive HTML in iframe
    │
    └── [Tool indicators] ─── Tool execution status

[A2A Agent Card] ─── GET /.well-known/agent.json
    └── Lists all public skills for external discovery
```

## Implementation Plan

### Phase 1: AG-UI Streaming (~2 days)
- [ ] Determine if ADK `get_fast_api_app()` provides AG-UI natively
- [ ] If not: implement `backend/protocols/agui.py` (ADK → AG-UI translation)
- [ ] Implement SSE endpoint in `fast_api_app.py`
- [ ] Implement `frontend/src/providers/AGUIProvider.tsx`
- [ ] Implement `frontend/src/hooks/useSkillAgent.ts`
- [ ] Verify: message sent → tokens stream to frontend

### Phase 2: A2UI Rendering (~2 days)
- [ ] Define A2UI marker format in agent instructions
- [ ] Implement `frontend/src/components/protocols/A2UIRenderer.tsx`
- [ ] Add A2UI detection/extraction in message rendering
- [ ] Test with form, table, and chart components
- [ ] Verify: agent returns A2UI JSON → frontend renders component

### Phase 3: MCP Apps + A2A (~3 days)
- [ ] Implement `frontend/src/components/protocols/MCPAppFrame.tsx`
- [ ] Add `ui://` detection in tool results
- [ ] Implement `backend/protocols/a2a.py` (agent card endpoint)
- [ ] Implement `backend/protocols/mcp_server.py` (expose skills as MCP tools)
- [ ] Verify: `/.well-known/agent.json` returns valid card

## Migration & Rollout

**No database migration.** Protocols are stateless.

**Rollback Plan:** Fall back to plain text rendering (AG-UI text events only, no A2UI/MCP Apps).

## Testing Strategy

### Backend Tests (pytest)
- [ ] AG-UI event translation: ADK event → correct AG-UI event type
- [ ] A2A agent card: returns valid JSON with all public skills
- [ ] MCP server: skills exposed as MCP tools

### Frontend Tests (Vitest)
- [ ] CopilotKit provider renders without errors
- [ ] A2UI renderer handles all component types
- [ ] MCP App frame renders with correct sandbox attributes
- [ ] AG-UI events update message state correctly

### Integration Tests
- [ ] Full flow: agent responds → AG-UI stream → text renders in chat
- [ ] A2UI flow: agent returns form JSON → form renders → user submits → agent processes
- [ ] MCP Apps: tool returns ui:// → iframe renders

## Security Considerations

- **A2UI**: UI-as-data (JSON), not code — no script injection risk
- **MCP Apps**: Sandboxed iframes with restricted permissions
- **AG-UI**: SSE over HTTPS, authenticated endpoints
- **A2A**: Public agent card contains no secrets or internal state

## Performance Considerations

- AG-UI SSE: ~0 overhead over raw SSE (standard event format)
- A2UI rendering: <50ms (JSON parse + React render)
- MCP Apps iframe: ~200ms load time (small HTML documents)
- CopilotKit bundle: ~15KB gzipped
- A2A agent card: cached, <10ms response

## Success Criteria

- [ ] AG-UI streaming works end-to-end (backend → frontend)
- [ ] First token appears in <300ms after agent starts
- [ ] A2UI form component renders and accepts user input
- [ ] MCP App iframe renders tool UI securely
- [ ] A2A agent card is valid and lists all public skills
- [ ] All protocol tests passing

## Open Questions

- Does ADK's `get_fast_api_app()` handle AG-UI translation natively, or do we need custom translation?
- What's the exact `@a2ui/react` npm package name? (verify on npm registry)
- Should A2UI components be embedded in text (markdown markers) or as separate AG-UI events?
- Should MCP App iframes communicate bidirectionally (e.g., user clicks in iframe → agent receives action)?

## Related Documents

- [Migration to v6](../v5.0.0/migration-to-v6.md) — Protocol stack (lines 340-393), frontend architecture (lines 597-626)
- [Agent Factory](implemented/agent-factory.md) — How agents emit events
- [Frontend Architecture](implemented/frontend-architecture.md) — Component hierarchy

---

## Implementation Report

**Completed**: 2026-04-22
**Actual Effort**: ~1 day (6 milestones across one session; planned 3.25d)
**Branch/PR**: `dev` — commits `6195717..4911a95` (sprint PROTOCOLS-1A5)
**Evaluation**: 97/100 pass, round 1 — see `.claude/state/evaluations/eval_PROTOCOLS-1A5_round_1.json`

### What Was Built
- **M1 AG-UI hardening**: deleted `/api/chat/spike` + `spike_agent`; enabled `use_thread_id_as_session_id=True` in `backend/protocols/agui.py`; live tool-call SSE test; A2UI marker suffix in default agent instruction.
- **M2 A2A card**: `backend/protocols/a2a.py` serves `GET /.well-known/agent.json` (public skills only, 60s time-bucketed cache, unauthenticated).
- **M3 MCP server**: `backend/protocols/mcp_server.py` exposes each public skill as one MCP tool via the official `mcp` SDK's FastMCP (no separate `fastmcp` pkg needed); tool invocation streams through `process_skill_request()` and returns concatenated `TEXT_MESSAGE_CONTENT` deltas.
- **M4 Frontend AG-UI chat**: `AGUIProvider` (pure React Context + `@ag-ui/client` HttpAgent — **pivoted away from CopilotKit**), `useSkillAgent` hook, `/chat/[skillId]` page with auth-guarded custom chat UI.
- **M5 A2UI renderer**: `A2UIRenderer` delegates to `@a2ui/react` `A2UIViewer`; `extractA2UISegments` splits text on ` ```a2ui ` fences; onAction flows back as new user message.
- **M6 MCP Apps frame + deployed smoke**: sandboxed `MCPAppFrame` (allow-scripts only), `ui://` detection in tool results, deployed smoke with 11/11 probes green on dev including POST `/mcp/` JSON-RPC initialize round-trip.

### Deviations from Design
- **CopilotKit dropped**: design doc showed `<CopilotKit runtimeUrl=... agent=...>` but CopilotKit 1.55 `runtimeUrl` expects a GraphQL runtime, not AG-UI SSE, and `agent` is a string name not an AbstractAgent. Rewrote the provider on top of `@ag-ui/client` HttpAgent directly. Captured in memory `gotcha_copilotkit_not_agui_native.md`.
- **A2UI SDK Path B deferred**: design doc proposed `a2ui-agent-sdk` as the Phase 1B authoring path. Kept Path A (instruction markers) for this sprint; Path B deferred to Phase 1B as planned.
- **FastMCP mount trio**: the design doc didn't anticipate the three interacting footguns for mounting FastMCP on FastAPI. Captured in memory `gotcha_fastmcp_mount_path.md` with regression test `test_mcp_initialize_via_http_mount_returns_jsonrpc_result`.

### Files Created
- `backend/protocols/a2a.py`, `backend/protocols/mcp_server.py`
- `backend/tests/api_tests/test_a2a.py`, `backend/tests/api_tests/test_mcp_server.py`
- `frontend/src/providers/AGUIProvider.tsx` (+ tests)
- `frontend/src/hooks/useSkillAgent.ts` (+ tests)
- `frontend/src/app/chat/[skillId]/page.tsx`
- `frontend/src/components/protocols/{A2UIRenderer,MCPAppFrame}.tsx` (+ tests)
- `frontend/src/lib/a2ui/extractMarkers.ts` (+ tests)

### Files Modified
- `backend/fast_api_app.py` (mounts A2A router + `/mcp` ASGI app + composed lifespan)
- `backend/protocols/agui.py` (`use_thread_id_as_session_id=True` when session service provided)
- `backend/adk/agent.py` (A2UI instruction suffix)
- `frontend/src/app/api/proxy/[...path]/route.ts` (Firebase bearer forwarding)
- `frontend/src/app/layout.tsx` (AGUIProvider wiring)
- `frontend/src/test/setup.ts` (`afterEach(cleanup)` for CI singleFork mode)
- `scripts/smoke-deployed.sh` (A2A card + MCP initialize probes)

### Lessons Learned
- **Mount deep before you commit**: in-process FastMCP tests pass even when the HTTP mount is broken three different ways. The TestClient probe is the only reliable guard — ship it with any ASGI sub-app mount.
- **Pivot early on framework misfits**: CopilotKit wasn't a "small adjustment" — recognising the GraphQL/SSE mismatch within the milestone (rather than bending it) saved days.
- **CI-mode test setup is not local-mode test setup**: `vitest.config.ts`'s `pool: 'forks'` + `singleFork: true` under `CI=true` reuses one jsdom for all test files, so leaked renders silently pass locally and fail in GH Actions. Always reproduce with `CI=true npm run test:run`.
- **Design-doc code samples age fast**: the A2UI and CopilotKit snippets were already stale at implementation time. Treat design-doc code as sketches, verify against current package docs before copying.
