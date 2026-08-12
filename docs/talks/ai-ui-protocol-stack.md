# The AI UI Protocol Stack

> **Purpose:** Talk + workshop reference for the open AI agent protocol stack as it lands in production. Uses Aitana Platform v6 as the worked example.
>
> **Status:** Living document — updated as v6 implementation reveals real-world gotchas. Every claim is tagged with verification status so I never give a workshop slide a confident answer I haven't actually run.
>
> **Maintainer note:** Update this any time we (a) verify a new assumption during the v6 build, (b) find a gotcha that contradicts the docs, or (c) discover a new protocol or version bump that changes the picture. The talk's value comes from being the only deck in the room with field notes from a real bring-up.

**Last updated:** 2026-05-01 (Layer 4 gains a "Static / Declarative / Open" intro borrowed from CopilotKit's taxonomy — useful audience framing for why A2UI and MCP Apps both exist. Verification-log row added for the OpenGenerativeUI assessment.)
**Workshop guide:** [workshop.md](workshop.md) — module-by-module presenter notes, code file index, live demo scripts, pre-talk checklist
**Related design docs:** [streaming-and-protocols.md](../design/v6.0.0/streaming-and-protocols.md), [v6-implementation-roadmap.md](../design/v6.0.0/v6-implementation-roadmap.md)

---

## TL;DR — The Pitch

For most of 2024–25, every AI agent product reinvented the same four wheels: streaming, declarative UI, tool discovery, agent discovery. By 2026, all four have **open protocols** with multiple implementations. If you build a new agent product today and you're still writing custom SSE framing or hand-rolling JSON-to-React glue, you're paying for technical debt the rest of the ecosystem already retired.

The pitch in one sentence: **"You can build a complete agent product today by composing six published protocols, and the only code you write is your business logic."**

The six protocols, from bottom to top:

| # | Protocol | Layer | What it does | Status as of 2026-04 |
|---|----------|-------|--------------|----------------------|
| 1 | **ADK** (Agent Development Kit) | Framework | Agent loop, sessions, memory, callbacks | Google, stable |
| 2 | **MCP** (Model Context Protocol) | Tools | Discover + invoke external tools | Anthropic, stable |
| 3 | **A2A** (Agent-to-Agent) | Discovery | Agents publish capability cards, find each other | Linux Foundation, stable |
| 4 | **AG-UI** | Transport | SSE event protocol for agent → UI streams | CopilotKit + community, stable |
| 5 | **A2UI** | UI description | Declarative JSON that renders to React with two-way binding | Open standard, alpha |
| 6 | **MCP Apps** | UI extension | Sandboxed iframe UIs that tools can serve | Anthropic, draft |

---

## Discipline — protocol-native or it's a wrapper

The pitch above only holds if our code consumes the wire format directly. The moment we put a "friendly wrapper" between our code and the canonical protocol — a function that takes nicer-looking arguments and emits the wire format on your behalf — we're not testing the protocol any more. We're testing the wrapper.

**Discovered the hard way 2026-05-18** (A2UI v0.8 → v0.9). The frontend imported `@a2ui/react`'s default entry (v0.8) and used `A2UIViewer({root, components, data})` — a convenience component that LOOKS like a free-form spec but internally synthesizes v0.8 wire messages. The backend Python SDK validated against v0.9. Both halves "worked" because they spoke their own dialects to each other, never to a canonical validator. The mismatch only fired when the SDK injected the real v0.9 schema into the LLM prompt, the LLM emitted spec-compliant v0.9 — and the frontend wrapper couldn't parse it. We were testing our wrapper, not the protocol. See verification log 2026-05-18.

**The rule:** for every protocol on the stack, the line between "our code" and "the wire" must be the schema, not a friendlier shape. If the wrapper is there for ergonomics, the integration test must feed it raw wire-format messages and verify the rendering — not feed it the wrapper's nicer shape.

### Audit — protocol surfaces and their canonical/wrapper status

Updated as we verify each row. Anywhere this table has a wrapper without an integration test, that's a `[discipline-audit]` open issue.

| Layer | Wire format | What we consume | Wrapper between us and the wire? | Spec-validator in the loop? | Status |
|---|---|---|---|---|---|
| **A2UI v0.9 (agent → surface)** | array of `{version, createSurface \| updateComponents \| updateDataModel \| deleteSurface}` | `MessageProcessor` + `<A2uiSurface surface>` from `@a2ui/react/v0_9` (native) | None — `processMessages([…])` directly | Backend: `a2ui-agent-sdk` `parse_and_fix + validator.validate` against v0.9 catalog. Frontend: SDK SurfaceModel throws on bad messages. | ✅ post-2026-05-18 rewrite |
| **A2UI v0.9 (surface → agent)** | `forwardedProps.a2ui_surface_state = {[surfaceId]: {catalogId, dataModel}}` per turn + `A2uiClientAction` POST per user gesture | `SurfaceRegistry.readA2uiSurfaceState()` at `useSkillAgent.sendMessage`; `surface.onAction.subscribe` → `POST /api/sessions/{id}/surface-action` | None — frontend reads `SurfaceModel.dataModel.get('/')` directly; backend `wrap_with_a2ui_surface_context` InstructionProvider injects the `a2ui_surface_context.{surfaceId}` namespace into the next prompt | Backend: Pydantic schema on the action POST + 4 KB cap + per-skill `allow_surface_context_writes` opt-in gate. Frontend: TS type on the `forwardedProps` slot. | ✅ sprint 2.10 (2026-05-18) |
| **AG-UI events** | 16 canonical event types in 6 categories (RUN_*, TEXT_MESSAGE_*, TOOL_CALL_*, STATE_*, CUSTOM, REASONING_*) | `@ag-ui/client` `HttpAgent` directly | None at agent layer. But [AG-UI protocol boundary mismatch](../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/gotcha_agui_protocol_boundary.md) notes the HTTP ingress (`/api/skill/{id}/stream`) accepts `RunAgentInput` but our adapter shapes it to ADK input internally — adapter is the seam. | Pydantic models in `ag-ui-protocol` validate event shapes on encode. No round-trip test asserts that EVERY event we emit/consume matches a published schema. | ⚠️ partial — adapter at HTTP layer needs a wire-shape contract test |
| **AG-UI ↔ SkillMessage projection** | AG-UI `AssistantMessage { content?: string, toolCalls?: ToolCall[] }` | `toSkillMessage` projects to `{id, role, content: string}` | Yes — lossy projection. Just bit us 2026-05-18 (dropped `content: undefined` tool-only turns; bubble never rendered → dispatcher never fired). | None. Has a regression test now for the tool-only case. | ⚠️ wrapper exists — should be replaced with a non-lossy adapter or constrained to text-only paths |
| **MCP tools** | `tools/list` with `_meta.ui.resourceUri` for UI-bearing tools; `tools/call` returns data result content | `@modelcontextprotocol/sdk` Client directly; `@mcp-ui/client` `AppRenderer` for the iframe surface | None at the protocol layer — `AppRenderer` is a renderer for `text/html;profile=mcp-app` resources, not a wrapper that emits MCP messages | MCP SDK validates JSON-RPC shapes. **Path B (our own FunctionTool returning a UI resource) is unverified** (pending row in log). | ⚠️ partial — Path A shipped 2026-04-30, Path B never integration-tested |
| **MCP tools — `ui/update-model-context`** | Iframe→host RPC: `{method: "ui/update-model-context", params: {structuredContent: {…}}}` | `<AppRenderer onFallbackRequest>` — dispatch on `request.method`. NO dedicated prop in v7. | Yes — our `onFallbackRequest` switch IS the wrapper. If MCP SDK adds a typed `onUpdateModelContext` prop, our switch will drift. | None on the message shape — we read `request.params.structuredContent` raw. | ⚠️ adapter is fragile to spec evolution; pin a contract test |
| **A2A** | `/.well-known/agent.json` agent card; agent-to-agent JSON-RPC | `backend/protocols/a2a.py` — discovery endpoint shipped. Card built from `list_marketplace()` with a 60s lru_cache rotated via a time-bucket key (no scheduler, no background thread). Capabilities: streaming=true, pushNotifications=false, stateTransitionHistory=false. Card identity (`name`, `description`) is env-driven via `A2A_AGENT_NAME` + `A2A_AGENT_DESCRIPTION` with Sunholo defaults — fork overrides via env. ADK ships full A2A converters (`google.adk.a2a.*`) but the **task-handler side** (consuming A2A from other agents via `RemoteA2aAgent`) is not yet integrated. | Pydantic-derived shape on `_skill_to_a2a` + `_build_card`. `backend/tests/api_tests/test_a2a.py` covers: minimum-spec field presence, public-only filtering, no-auth requirement, cache invalidation on skill mutate. **NOT YET validated against the canonical A2A 1.0 JSON Schema from a2a-protocol.org** — small follow-up. | ✅ discovery shipped + tested; ⚠️ schema-conformance contract test is the remaining gap; ⚠️ inbound task-handler is post-workshop |
| **ADK FunctionTool** | Python function `(**kwargs) -> Any` registered via ADK FunctionTool wrapper | ADK's own factory — wire-format is the LLM tool-call args JSON | ADK is the framework; no protocol underneath | ADK validates the FunctionDeclaration on registration. LLM tool-call args go through a Pydantic-derived schema. | ✅ native |
| **Auth — anonymous group ID** | Short code (8-char `XXXX-XXXX`) + signed JWT (HS256, server secret) carrying synthetic `uid` + `group_id` + `auth_mode` + `exp` | `backend/auth/group_id_auth.py` (sprint 2.11, shipped) + 4 endpoints | None at the auth layer; bearer parses straight into `User` via the token-shape dispatcher in `auth/__init__.py` | Pydantic schema on token claims; HS256 signature; per-IP rate limit (10/min default); per-group session cap (100/day default); per-creator revocation; no-PII contract enforced in `User` type. | ✅ sprint 2.11 (shipped 2026-05-19, AIPLA fork v0.1 unblocked) |
| **Budget enforcement** | `BudgetConsultation`/`BudgetDecision` frozen dataclasses; `BudgetExceededError` → AG-UI `RUN_ERROR{code:"BUDGET_EXCEEDED", message, retry_after_seconds}` (passthrough field on the RunErrorEvent schema) | `BudgetEnforcer` runtime-checkable Protocol consulted in ADK `before_model_callback`; `InMemoryBudgetEnforcer` reference impl with period-keyed spend, 60s replay dedup, asyncio lock (sprint 2.12, shipped) | None at the gate layer — fork plugs its impl directly via `register_budget_enforcer()` | Protocol contract enforces shape (runtime_checkable isinstance check against duck-typed impls); 8-gate matrix at the function layer + 1 SSE-level test at the HTTP layer covers allow/warn/block/period rollover/multi-identity isolation/replay dedup/record reconciliation/fail-loud-but-allow on unconfigured cap. | ✅ sprint 2.12 (shipped 2026-05-19) |
| **Artefact review** | `ArtefactReview`/`ArtefactDecision` TS + Python types (mirror shapes; camelCase ⟷ snake_case) | `ArtefactReviewer` Protocol consulted in `MCPAppToolCallRouter` before iframe render (frontend, sprint 2.13 shipped) + optional `mcp_proxy._forward` interception for server-side review (backend, sprint 2.13 shipped). `PermissiveArtefactReviewer` is the shipped default; existing demos (Cesium map) unaffected. | None at the policy layer — permissive default; forks plug stricter impls in either layer | Protocol contract enforces shape (runtime_checkable + explicit async-check on register). Defence-in-depth ABOVE existing sandbox + CSP — reviewer crash + slow-reviewer + malformed body all fail open to the sandbox. Block path emits typed AG-UI-style 403 (server-side) or in-place refusal panel (client-side); audit POST on mount records every block. | ✅ sprint 2.13 (shipped 2026-05-19) |
| **OTel tenant attribution** | `tenant.uid`, `tenant.auth_mode`, `tenant.group_id` (when present), `tenant.uid_hash` (SHA256 of email, when present) as OTel span attributes via the standard `SpanProcessor` interface | `TenantAttributeSpanProcessor` registered in `telemetry.py`; contextvar bound at the single insertion point in `auth.get_current_user` (covers all 13 endpoints via dispatcher patch); fork extension via `register_tenant_enricher(fn)` (sprint 2.14, shipped) | None at the policy layer — platform defaults are non-PII; forks plug enrichers (e.g. AIPLA's `class_id` lookup) | Protocol contract via standard OTel `SpanProcessor`; runtime_checkable + explicit async/callable validation on register. PII rule: explicit no-reflection guarantee in `set_tenant_context` (reads User fields by name, never `__dict__` walk); raw email + display_name MUST NEVER land on a span; golden SHA256 test pins the hash function. | ✅ sprint 2.14 (shipped 2026-05-19) |

### How to use this table

When you add a new feature that touches any of these protocols:

1. **Find the row.** If it's missing, add it.
2. **Check the "Wrapper" column.** If your code only consumes the protocol through the wrapper, you cannot claim "protocol-native" — your test surface is the wrapper.
3. **Check the "Spec-validator in the loop" column.** If there's no validator on the path your code traverses, write the test that would catch a wire-format drift.
4. **If you tighten or remove a wrapper, log it in the verification log.** That's the field-note pipeline.

---

## The Stack — Visual

```
┌──────────────────────────────────────────────────────────────────┐
│ LAYER 4 — UI DESCRIPTION                                         │
│                                                                  │
│   A2UI (declarative JSON)        MCP Apps (sandboxed iframes)    │
│   • Forms, tables, charts        • Custom interactive UIs        │
│   • Two-way data binding         • postMessage to host           │
│   • Renders in React             • Tool-served HTML              │
└────────────────────────────────────┬─────────────────────────────┘
                                     │ ride on
                                     ▼
┌──────────────────────────────────────────────────────────────────┐
│ LAYER 3 — TRANSPORT                                              │
│                                                                  │
│   AG-UI                                                          │
│   • SSE event stream                                             │
│   • 16 canonical event types                                     │
│   • Lifecycle, text, tools, state, reasoning                     │
└────────────────────────────────────┬─────────────────────────────┘
                                     │ emitted by
                                     ▼
┌──────────────────────────────────────────────────────────────────┐
│ LAYER 2 — COORDINATION                                           │
│                                                                  │
│   A2A                            MCP                             │
│   • /.well-known/agent.json      • Tool discovery + invocation   │
│   • Capability cards             • stdio / SSE / HTTP transports │
│   • Cross-org agent discovery    • Server registries             │
└────────────────────────────────────┬─────────────────────────────┘
                                     │ used by
                                     ▼
┌──────────────────────────────────────────────────────────────────┐
│ LAYER 1 — FRAMEWORK                                              │
│                                                                  │
│   ADK (Google Agent Development Kit)                             │
│   • Agent loop, model abstraction                                │
│   • Sessions, memory, artifacts                                  │
│   • FunctionTool, callbacks, events                              │
└──────────────────────────────────────────────────────────────────┘
```

**Key insight for the talk:** the layers aren't an accident — they're a separation of concerns that AI products kept rediscovering the hard way. Each layer is independently swappable. You could keep ADK + MCP + A2A and swap AG-UI for some hypothetical AG-UI v2; you could keep AG-UI and swap ADK for LangGraph; the protocols are the contracts that make this possible.

---

## Layer 1 — ADK (Framework)

**What it is:** Google's open-source Agent Development Kit. The framework primitive — agent loop, model abstraction, sessions, memory, callbacks, events.

**When to use it:** Whenever you'd otherwise be writing your own agent loop. It replaces hand-rolled `while not done: model.call(...)` patterns with a battle-tested orchestrator that already handles streaming, tool calls, multi-turn, sub-agents, and observability.

**Where it sits in v6:**
- `backend/app.py` — root agent definition
- `backend/adk/agent_factory.py` — builds an `Agent` from a stored `Skill`
- `backend/fast_api_app.py` — wraps it in `get_fast_api_app()` for HTTP

**Install:**
```bash
uv add google-adk
```

**Minimal example (v6 pattern):**
```python
from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools import FunctionTool

def get_weather(city: str) -> str:
    return f"Sunny in {city}."

agent = Agent(
    name="weather_bot",
    model=Gemini(model_name="gemini-2.0-flash"),
    instruction="You are a helpful weather assistant.",
    tools=[FunctionTool(get_weather)],
)
```

**Verified:** ✅ Used in v6 today. ADK MCP server (`mcp__adk-mcp__search_code`) gives authoritative answers about the API surface.

**Gotchas:**
- ADK API moves fast. Callback signatures, `Runner` API, and `SessionService` have all changed across recent versions. Pin a version and use the ADK MCP server to verify before recalling from memory.
- `SkillToolset` (the way to compose skills as toolsets) is experimental — v1.25.0+.

**Links:**
- Docs: https://adk.dev/
- Skills spec extension: https://adk.dev/skills/
- Source: https://github.com/google/adk-python

---

## Layer 2a — MCP (Model Context Protocol)

**What it is:** Anthropic's open protocol for tool discovery and invocation. Lets an agent talk to external tool servers in a uniform way.

**When to use it:**
- You have tools that should be reusable across multiple agents/products
- You want third parties to plug their tools into your agent without custom adapters
- You need to keep tool implementation out of your agent's process (security, language boundary)

**When NOT to use it:** Internal tools that only your agent will ever call. ADK `FunctionTool` wrapping a Python function is faster and simpler. v6 reserves MCP for the "marketplace tools / customer integrations / third-party services" category, not for our own AI search and parsing.

**Where it sits in v6:**
- `backend/tools/mcp/registry.py` — registry of MCP servers
- ADK provides `McpToolset` for consuming MCP servers in an Agent

**Install:**
```bash
# Server side (Python)
uv add mcp
# Client side — comes with ADK
```

**Minimal example (consuming an MCP server from ADK):**
```python
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

mcp_tools = McpToolset(
    connection_params=SseConnectionParams(url="https://example.com/mcp"),
)

agent = Agent(
    name="research_bot",
    model=Gemini(...),
    tools=[mcp_tools],
)
```

**Verified:** ✅ MCP is stable. ADK ships first-class MCP support.

**Gotchas:**
- Three transport variants: stdio (local subprocess), SSE (HTTP server), HTTP (request/response). They have different deployment characteristics.
- Auth on remote MCP servers is an emerging story — mostly bearer tokens today.

**Links:**
- Spec: https://modelcontextprotocol.io/docs/
- Server registry pattern (community): various

---

## Layer 2b — A2A (Agent-to-Agent)

**What it is:** A protocol for agents to *advertise* themselves and *discover* each other. Each agent publishes a JSON "agent card" at a well-known URL.

**When to use it:**
- You're building a marketplace or directory of agents
- You want other AI products to programmatically find your skills
- You're building a multi-agent system where agents need to delegate to each other

**When NOT to use it:** Single-product, single-org. If you're not exposing capabilities outside your own process, A2A adds complexity for no payoff yet.

**Where it sits in v6:**
- `backend/protocols/a2a.py` (planned)
- Serves `/.well-known/agent.json` listing all public skills

**Minimal example (agent card):**
```json
{
  "schemaVersion": "0.1.0",
  "name": "Aitana Doc Analyst",
  "description": "Analyzes uploaded documents and answers questions about them",
  "endpoints": {
    "chat": "https://aitana.example.com/api/chat/doc-analyst"
  },
  "capabilities": ["document_qa", "extraction", "summarization"]
}
```

**Verified:** ⚠️ Spec exists, v6 will publish a card but consumption-side discovery is post-Phase-2 work.

**Links:**
- Spec: https://a2a-protocol.org/latest/specification/

---

## Layer 3 — AG-UI (Streaming Transport)

**What it is:** An SSE event protocol for streaming agent activity to a UI. 16 canonical event types covering lifecycle, text streaming, tool calls, state sync, and reasoning.

**When to use it:** Whenever an agent needs to talk to a frontend in real time. AG-UI is the open replacement for "we wrote our own SSE format and CopilotKit's `useChat` to consume it."

**When NOT to use it:** Backend-to-backend agent calls (use HTTP/JSON or A2A). Batch jobs (use a queue).

**Where it sits in v6:**
- `backend/protocols/agui.py` — wraps each `Skill` as an AG-UI endpoint
- `backend/api/chat.py` — mounts the endpoints
- Frontend: CopilotKit's AG-UI client consumes the stream

**Critical finding (verified 2026-04-10):**

ADK does **not** natively emit AG-UI events. Use [`ag-ui-adk`](https://pypi.org/project/ag-ui-adk/) — an official CopilotKit-maintained middleware that wraps an ADK Agent as an AG-UI endpoint.

**Install:**
```bash
uv add ag-ui-adk ag-ui-protocol
# ag-ui-adk: middleware (v0.6.0 as of 2026-04-06)
# ag-ui-protocol: lower-level Pydantic models + EventEncoder (v0.1.15)
```

**Minimal example (the entire backend integration):**
```python
from fastapi import FastAPI
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.agents import Agent

app = FastAPI()

my_agent = Agent(name="assistant", instruction="...", tools=[...])
adk_agent = ADKAgent(adk_agent=my_agent, app_name="aitana", user_id="...")

add_adk_fastapi_endpoint(
    app, adk_agent, path="/chat",
    extract_headers=["x-user-id", "x-tenant-id"],
)
```

That's it. No custom SSE framing, no event translation, no manual buffering.

**The 16 canonical events:**

| Category | Events |
|----------|--------|
| Lifecycle | `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR`, `STEP_STARTED`, `STEP_FINISHED` |
| Text | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `TEXT_MESSAGE_CHUNK` |
| Tools | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT`, `TOOL_CALL_CHUNK` |
| State | `STATE_SNAPSHOT`, `STATE_DELTA`, `MESSAGES_SNAPSHOT` |
| Reasoning | `REASONING_START`, `REASONING_MESSAGE_START/CONTENT/END`, `REASONING_END`, `REASONING_ENCRYPTED_VALUE` |
| Special | `RAW`, `CUSTOM`, `META_EVENT` |

**Verified:** ✅ Package exists, install verified, full integration pattern verified by scaffolding the official starter (`npx copilotkit@latest create -f adk`) on 2026-04-10. The scaffold has working examples of: shared state, frontend tools, backend tools rendered as React via `useRenderToolCall`, and the prebuilt `<CopilotSidebar>`. ⚠️ Phase 0.4 spike still pending for: custom session service injection (scaffold uses `use_in_memory_services=True`), Firebase auth ordering, our specific edit-in-place flow.

**Frontend side — the *real* three-tier architecture (verified 2026-04-10 by scaffolding `npx copilotkit@latest create -f adk`):**

The picture is more nuanced than "React talks to ag-ui-adk". There's a **Next.js API route in the middle** running `CopilotRuntime` + `HttpAgent` from `@ag-ui/client`, which is what actually translates between CopilotKit's React provider and the AG-UI SSE stream:

```
[React <CopilotKit> provider]
    │ CopilotKit hooks (useCoAgent, useFrontendTool, useRenderToolCall)
    ▼
[Next.js /api/copilotkit route]
    │ CopilotRuntime + HttpAgent (from @ag-ui/client)
    ▼
[Python FastAPI + ag-ui-adk]
    │ add_adk_fastapi_endpoint
    ▼
[ADK Agent]
```

```bash
npm install @copilotkit/react-core @copilotkit/react-ui @copilotkit/runtime @ag-ui/client
```

**The Next.js bridge (~30 LOC, the entire thing):**

```typescript
// src/app/api/copilotkit/route.ts
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";

const runtime = new CopilotRuntime({
  agents: {
    my_agent: new HttpAgent({
      url: process.env.AGENT_URL || "http://localhost:8000/",
    }),
  },
});

export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
```

**The React provider (in `app/layout.tsx`):**

```tsx
import { CopilotKit } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";  // ← important: ships its own stylesheet

<CopilotKit runtimeUrl="/api/copilotkit" agent="my_agent">
  {children}
</CopilotKit>
```

**The hook vocabulary (this is where most of the leverage lives):**

| Hook | What it does |
|------|--------------|
| `useCoAgent<T>` | **Bidirectional shared state** with the Python agent — `{ state, setState }`. The agent's Pydantic state class is mirrored in TypeScript. State changes in either direction sync via AG-UI events. This is the killer feature. |
| `useFrontendTool` | Define a tool **in TypeScript** that the agent can call (e.g. `setThemeColor`, `navigateTo`). Agent doesn't know it's frontend; it just calls a tool. |
| `useRenderToolCall` | Define how a backend tool's call/result should **render in React** (with streaming lifecycle states). This is generative UI. |
| `useDefaultTool` | Default fallback tool handling. |
| `useHumanInTheLoop` | HITL approval flows for tool calls before execution. |

**Generative UI in practice (~10 lines per tool):**

```tsx
useRenderToolCall(
  {
    name: "get_weather",
    parameters: [{ name: "location", type: "string", required: true }],
    render: ({ args, result }) => (
      <WeatherCard location={args.location} />
    ),
  },
  [/* deps */],
);
```

The `render` function is called with the streaming tool-call arguments as they arrive — `args` populates incrementally, then `result` lands when the tool returns. The lifecycle is handled for you.

**Shared state in practice (the killer feature):**

```python
# Python side — Pydantic state class
class ProverbsState(BaseModel):
    proverbs: list[str] = Field(default_factory=list)

# Agent has callbacks that read/write tool_context.state["proverbs"]
```

```tsx
// TypeScript side — mirror the shape
type AgentState = { proverbs: string[] };

const { state, setState } = useCoAgent<AgentState>({
  name: "my_agent",
  initialState: { proverbs: [] },
});

// state.proverbs is live-synced from the agent
// setState updates locally AND sends to the agent via AG-UI events
```

The agent and the React component now share a state object **as if they were in the same process**. Edits in either direction propagate. This is the pattern that makes Aitana v6's "edit a document block, agent sees the edit" flow trivial — no custom websocket, no custom event types.

**Drop-in chat surface (`<CopilotSidebar>` example from the scaffold):**

```tsx
import { CopilotSidebar, CopilotKitCSSProperties } from "@copilotkit/react-ui";

<main style={{ "--copilot-kit-primary-color": "#6366f1" } as CopilotKitCSSProperties}>
  <CopilotSidebar
    defaultOpen={true}
    labels={{ title: "Assistant", initial: "👋 How can I help?" }}
    suggestions={[
      { title: "Generative UI", message: "Get the weather in San Francisco." },
      { title: "Frontend Tools", message: "Set the theme to green." },
    ]}
  >
    <YourMainContent />
  </CopilotSidebar>
</main>
```

That replaces every line of "build a chat panel from scratch" work. Theming via CSS var `--copilot-kit-primary-color` is exposed through the typed `CopilotKitCSSProperties`. Sister components: `<CopilotChat>`, `<CopilotPopup>`.

**Gotchas confirmed by scaffold (2026-04-10):**
- The scaffold uses `LlmAgent` (not bare `Agent`) and uses ADK callbacks (`before_agent_callback`, `before_model_callback`, `after_model_callback`) as the customisation points — that's the idiomatic ADK pattern for state injection.
- Model is a string literal (`"gemini-2.5-flash"`) on the `LlmAgent` constructor, **not** wrapped in a `Gemini(model_name=...)` object as some older docs show.
- `ADKAgent` constructor takes `use_in_memory_services=True` as the easy mode. The scaffold doesn't show how to inject a real session service — that's the next thing to discover in the spike.
- The Next.js bridge route uses `ExperimentalEmptyAdapter` (note "Experimental" in the name) — flag for production hardening.
- The `<CopilotKit>` provider must wrap content at the layout level, and `@copilotkit/react-ui/styles.css` must be imported in the layout file or styling breaks silently.
- Single `npm install` bootstraps both halves via a `postinstall` script that runs `setup-agent.sh` to create `agent/.venv` and `uv sync`. Worth borrowing for v6 dev ergonomics.

**Gotchas still to confirm in spike:**
- Does `ADKAgent` accept a custom `SessionService` (e.g. Vertex AI Agent Engine) when `use_in_memory_services=False`? The scaffold doesn't show this.
- Does Firebase auth middleware compose correctly with `add_adk_fastapi_endpoint` (FastAPI middleware ordering)?
- `ResumabilityConfig` — does it support per-`(user, document)` sessions?

**Links:**
- AG-UI: https://docs.ag-ui.com/
- Events spec: https://docs.ag-ui.com/concepts/events
- Python middleware: https://pypi.org/project/ag-ui-adk/
- Python types: https://pypi.org/project/ag-ui-protocol/
- React client: https://docs.copilotkit.ai/

---

## Layer 4 — UI Description: two protocols on one spectrum

A2UI (4a) and MCP Apps (4b) aren't redundant — they sit at different points on a "frontend in control ↔ agent in control" axis. Naming the axis up front saves the audience the "wait, why both?" question.

CopilotKit's taxonomy (Static / Declarative / Open) is a useful frame here:

| Pattern | Agent ships | Frontend renders | v6 protocol |
|---------|-------------|------------------|-------------|
| **Static** | Tool call + args | Pre-built React, agent picks one | Plain AG-UI `TOOL_CALL_*` — no UI protocol needed |
| **Declarative** | Structured UI spec (JSON tree) | House-styled React from spec | **A2UI** (Layer 4a) |
| **Open** | Full UI surface (HTML/SVG/iframe) | Sandboxed iframe; no spec mediates | **MCP Apps** (Layer 4b) |

Pick the lowest-openness pattern that does the job: A2UI for forms, tables, charts (consistent house style, two-way binding); MCP Apps when the widget is bigger than what A2UI can express (3D globe, Three.js scene, custom WebGL). Static doesn't need its own protocol — it's just tool calls.

CopilotKit's [OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI) is the "Open" pattern wrapped in their CopilotKit v2 + LangChain Deep Agents stack. v6 reaches the same ceiling via canonical `@mcp-ui/client.AppRenderer` directly atop AG-UI, so we don't adopt their framework — see verification log 2026-05-01.

---

## Layer 4a — A2UI (Declarative UI)

**What it is:** A JSON description language for interactive UI components. Agents emit A2UI JSON; the frontend renders it as React with two-way data binding back to the agent.

**When to use it:**
- The agent needs to surface forms, tables, charts, or inputs
- The user should be able to edit values and have them flow back to the agent
- You want consistent styling across UI agents emit (rather than each agent inventing its own HTML)

**When NOT to use it:** Pure conversational text. Plain markdown with the AG-UI text events is simpler.

**A2UI's actual value prop (what we learned the hard way):** A2UI has three load-bearing pieces:

1. **ComponentInstance schema** — `{id, component: {TypeName: {properties}}}`. The JSON tree. This is what people see first and assume is the whole protocol.
2. **DataModel** — a shared mutable state object addressable by `/path/to/field`. Both Python and React reference it. The agent mutates it; the frontend subscribes.
3. **Action dispatch** — `sendAction({name, context})` from a UI component → agent receives event → responds.

**The killer feature is (2) + (3): two-way binding.** A form on the frontend writes to the DataModel via `useA2UIComponent().setValue()`; the agent sees the change without a request/response round-trip. The agent writes back; the form updates. CRDT-flavored shared state between Python and React.

**JSON rendering is the shallow surface. State sync is the substance.** This is easy to miss and we missed it initially.

**Where it sits in v6:**
- **Chat surface (correct A2UI use):** Agent emits A2UI components inside chat responses; `A2UIViewer` from `@a2ui/react` renders them with two-way binding. Forms, action buttons, sliders.
- **Multi-surface routing (v6.2.0 sprint 2.9 ✅ shipped 2026-05-18):** A2UI's `surface_id` is now first-class. Skills declare `tool_configs.a2ui.default_surface` (chat / workspace / sidebar / modal / custom); the frontend `SurfaceRegistry` routes specs to named mounts via portal-free dispatch. Workspace + sidebar surfaces persist across chat turns; `update_mode: patch` preserves component identity for live data updates (map zoom without WebGL remount). See [multi-surface-rendering design](../design/v6.2.0/implemented/multi-surface-rendering.md) + [skill-author howto](../integrations/multi-surface-rendering.md). **Workshop W6 demo (post-2026-05-18):** "show me Munich → workspace renders map; now zoom to old town → workspace patches in place without remount" — demonstrates the protocol, not just the renderer.
- **Document panel (NOT A2UI for rendering):** Parsed documents render via a custom `BlocksRenderer` that walks ailang-parse's BlockADT directly. Decided 2026-04-25 — see [document-rendering-decision.md](../design/v6.1.0/implemented/document-rendering-decision.md). A2UI v0_8 has no `Table` component; forcing documents through `A2UIViewer` requires a lossy converter and produces worse output than the canonical workbench demo (which walks BlockADT directly too).
- **Agent-driven document edits (future, hybrid A2UI):** When the agent proposes an edit to a document region, embed an `<A2UIViewer>` *sub-surface* inside the document panel for that specific editable control. DataModel syncs the proposed edit between Python and React live; user accepts/rejects. Frame stays `BlocksRenderer`; edit affordance is A2UI. See [agent-driven-document-edits.md](../design/v6.1.0/agent-driven-document-edits.md).

**Naming collision worth calling out:** ailang-parse has an `output_format="a2ui"` mode whose output is *not* the A2UI ComponentInstance spec — it's their own document AST. The naming implied drop-in compatibility with `@a2ui/react` that doesn't exist. Feedback sent to docparse inbox (msg_20260425_000909).

**Workshop talking points:**

1. **A2UI is a state-sync protocol, not a rendering format.** The DataModel + `setValue()` + `sendAction()` two-way binding is what A2UI is *for*. Rendering JSON is incidental.
2. **Protocol over-application is a real failure mode.** "We have an A2UI renderer, render documents through it" sounded clean. Turned into a Zod-schema fight, two Firestore errors, and worse-looking output than the public ailang-parse workbench.
3. **Match the canonical demo.** The workbench doesn't use `A2UIViewer` for documents — it walks BlockADT directly. That was hiding in plain sight.
4. **Don't close the door, though.** A2UI's state-sync model IS the right tool for *agent-driven document edits*. Hybrid pattern: BlocksRenderer frames the doc, A2UIViewer embeds for editable sub-surfaces. Not either/or.

**Demo idea for July:** split-pane parsed document. Left: `BlocksRenderer` shows static content. Right: chat. User asks agent to "change the Q3 revenue to $2.5M." A small `<A2UIViewer>` sub-surface appears *inline over the cell* with the proposed value + accept/reject buttons. Agent and user co-editing the same DataModel live. Single on-stage moment that visualizes "two pipelines, right tool for each job."

**The ecosystem has multiple A2UI renderers — pick deliberately:**

| Package | Version (verified 2026-04-19) | Style | Notes |
|---------|-------------------------------|-------|-------|
| `@a2ui/react` | 0.9.0-alpha.0 | Custom theme | The reference renderer. Currently in v6 `package.json`. A2UI v0.9 rename: optional component set is now **"Basic"** (was "Standard"). |
| `@copilotkit/a2ui-renderer` | 1.55.1 | CopilotKit theme | Plugs into `<CopilotChat>` via `createA2UIMessageRenderer({ theme })` so A2UI surfaces emitted by the agent **auto-render inside the chat** without separate routing. |
| `@a2ui-sdk/react` | 0.4.0 | **shadcn / Tailwind** | Community renderer (easyops-cn) built on shadcn primitives. Matches v6's stack exactly. Supports A2UI v0.8 stable + v0.9 draft. |

**For v6, the practical choice is:**
- Use `@copilotkit/a2ui-renderer` for A2UI surfaces that arrive *inside the chat stream* (e.g. extracted tables the agent shows in response to a question).
- Use `@a2ui-sdk/react` for the *document panel* itself, where we want shadcn tokens and tight integration with our existing Tailwind components.

Two renderers in one app is fine — they both consume the same A2UI JSON, they just produce different React trees.

**Install (the CopilotKit path):**
```bash
npm install @copilotkit/a2ui-renderer @copilotkit/react-core @copilotkit/react-ui
```

```tsx
import { createA2UIMessageRenderer } from "@copilotkit/a2ui-renderer";

const a2uiRenderer = createA2UIMessageRenderer({ theme: "light" });

<CopilotKit
  runtimeUrl="/api/copilotkit"
  agent="my_agent"
  renderActivityMessages={a2uiRenderer}
>
  {children}
</CopilotKit>
```

**Install (the shadcn path):**
```bash
npm install @a2ui-sdk/react
```

**Two-way binding (works the same regardless of renderer):**
```typescript
function MyTextField({ id }) {
  const { value, setValue } = useA2UIComponent(id);
  return <input value={value} onChange={e => setValue(e.target.value)} />;
}
```

`setValue` updates local state and emits an event back through AG-UI to the agent, so the agent's view of the document stays in sync with what the user is editing. This is the foundation of v6's "edit a block in the document panel, the agent sees the edit before the next message" pattern.

**Verified 2026-04-10:** ✅ All three packages exist on npm at the versions above. The CopilotKit `a2ui-renderer` integration pattern is documented in their generative-ui repo. Editable mode in `@a2ui/react` v0.9.0-alpha.0 still needs hands-on verification before we commit to it for v6's document editing flow.

**What A2UI v0.9 adds (as of 2026-04-19, alpha):**
- **Python Agent SDK** — `pip install a2ui-agent-sdk` with `schema_manager.generate_system_prompt()` and `parse_response_to_parts()`. v6 doesn't use it yet; we're still hand-rolling the A2UI prompt inside ADK instructions. Candidate swap-in for Phase 1B when the backend starts authoring A2UI directly.
- **Basic (formerly Standard) components + custom catalogs** via `CatalogConfig.from_path()` — lets us ship an Aitana-branded component set without forking the renderer. Not on the v6.0.0 critical path; revisit once we start designing skill-specific widgets.
- **Resilient streaming** — incremental parse/heal of LLM output so components can render mid-stream. v6's current plan is plain-text fallback + post-message A2UI; resilient streaming would let us show partial tables/forms while the model is still writing them. Demo-worthy, but worth measuring cost before committing.
- **Client-defined validation functions** and **client-to-server collab sync** — out of scope for v6.0.0 (single-user edit model).
- **Simplified transports** (MCP / WebSockets / REST / AG-UI / A2A). We're on AG-UI + REST; A2A `/.well-known/agent.json` is still a TODO.

**Gotchas:**
- A2UI is new and the spec is still evolving. Pin versions and budget for breakage.
- Read-only rendering is mature; editable mode is alpha across all three renderers.
- If you're using `@copilotkit/a2ui-renderer`, make sure your A2UI JSON is emitted through the AG-UI stream as activity messages — it doesn't pick up A2UI from arbitrary channels.
- A2UI v0.9 is alpha — "Standard" → "Basic" rename and `CatalogConfig.from_path()` shape may still shift before stable. Re-check the blog/release notes monthly until July.

**Links:**
- Spec: https://a2ui.org/
- CopilotKit A2UI: https://github.com/CopilotKit/generative-ui
- shadcn A2UI: https://github.com/easyops-cn/a2ui-sdk

---

## Layer 4b — MCP Apps (Sandboxed Tool UIs)

**What it is:** Anthropic's draft extension to MCP that lets a tool serve a sandboxed HTML UI alongside its data. The host renders it in an iframe with `postMessage` for bidirectional communication.

**When to use it:**
- A tool needs custom interactive UI that A2UI can't express (e.g., a 3D viewer, a custom chart library, a video editor)
- The tool is provided by a third party who shouldn't be able to inject arbitrary code into your host page
- You want the security of a sandbox without losing interactivity

**When NOT to use it:** Anything A2UI can render. The iframe sandbox adds complexity and isolation overhead.

**Where it sits in v6:**
- Reserved for third-party tool integrations + custom Aitana widgets that exceed A2UI's expressivity
- Not in Phase 1 scope; Phase 0.3 contracts leave room for it

**Sandbox model:** `<iframe sandbox="allow-scripts">` (deliberately **not** `allow-same-origin`). Communication via `window.postMessage`.

**Off-the-shelf renderer (verified 2026-04-10):**

```bash
npm install @mcp-ui/client
# v7.0.0 confirmed on npm
```

`@mcp-ui/client@7.0.0` ships an `<AppRenderer>` (high-level — fetches the UI resource for you given a tool name + an MCP `Client`) and `<AppFrame>` (low-level — takes pre-fetched HTML + your own `AppBridge`) React component that handles the iframe sandbox + postMessage plumbing. The `<AppRenderer>` API expects EITHER a connected MCP `Client` OR a pre-fetched `html` string — it does NOT introspect a CallToolResult to find the UI by itself, because real MCP servers (verified 2026-04-30 against `ext-apps/map-server`) ship the UI binding on the tool DEFINITION (`_meta.ui.resourceUri`), not on the tool RESULT. See verification log 2026-04-30 for the AppRenderer prop surface and the architectural choice points (frontend Client vs backend pre-fetch).

**Note (corrected 2026-04-30):** the older `<UIResourceRenderer>` name was the v6.x export; v7 renamed to `<AppRenderer>`. v6 platform pins `@mcp-ui/client@7.0.0`.

**Note (corrected 2026-04-30):** the `npx copilotkit@latest create -f mcp-apps` scaffolder remains the cleanest *CopilotKit-based* on-ramp for MCP Apps, but **v6 dropped CopilotKit** in favour of `@ag-ui/client` directly (per `gotcha_copilotkit_not_agui_native`). For our stack the right adoption path is `@mcp-ui/client` directly atop AG-UI's existing `TOOL_CALL_RESULT` event, not `CopilotRuntime.mcpApps.servers`.

### The transport-decoupled insight (workshop "aha")

**Most writeups treat MCP as load-bearing for MCP Apps. It isn't.**

What MCP Apps actually defines:
1. **A UI resource format** — HTML content (or a URL) with a MIME profile like `text/html;profile=mcp-app`, plus metadata describing sandboxing and a `postMessage` RPC contract for the iframe ↔ host communication.
2. **A rendering host** — `<UIResourceRenderer>` accepts a `resource` prop, mounts it in a sandboxed iframe, and brokers postMessage calls back to the agent.

The renderer takes a JS object. It does **not** introspect whether that object arrived via:

| Path | What runs | Honest to spec? | Effort |
|------|-----------|-----------------|--------|
| **A. Real MCP server** (e.g. `ext-apps/map-server`) | Separate process exposing MCP `tools/call` + `resources/read`; ADK `McpToolset` connects. Real servers (verified 2026-04-30) put UI binding on the TOOL DEFINITION (`_meta.ui.resourceUri`), not on the tool result. Host fetches HTML separately. | Yes, canonical (UI-by-reference is the spec pattern) | Medium — extra process + need to wire resource-fetch on the host |
| **B. ADK FunctionTool returning UI by REFERENCE** | One Python function in `backend/tools/` constructs `{_meta: {ui: {resourceUri: "ui://aitana/foo.html"}}}` on the tool definition, plus an ADK Resource-handler that serves the HTML at that URI. Mirrors what real MCP servers do. AG-UI carries the same wire shape. | Spec-compliant, both transport AND payload | Medium — needs an ADK resource-serving primitive (verify ADK supports it) |
| **B′. ADK FunctionTool returning UI EMBEDDED** | One Python function returns `{type: "resource", resource: {mimeType: "text/html;profile=mcp-app", text: "<html>..."}}` inline in the result. Spec allows this shape but real reference servers don't use it. | Spec-compliant payload, non-canonical pattern | Trivial |
| **C. Hybrid** | FunctionTool fetches HTML from GCS / CDN, wraps it in a UI resource (Path B′ shape) | Same as B′ | Easy |
| **D. Static fixture** | Pre-baked JSON in `frontend/src/fixtures/`, fed to `<AppFrame>` directly | Renderer-only, no agent | Trivial — for tests/storybooks |

**Why this matters:** MCP Apps' real value is the **resource format + postMessage contract**, not the transport. That's why it composes with anything — MCP, AG-UI, plain REST. For Aitana v6 it means we can ship Aitana-themed document widgets via our own ADK FunctionTools (path B) without standing up an MCP server, while still being able to consume third-party MCP Apps (path A via `McpToolset`) when they're useful.

**Verified:** ✅ Package + scaffolder exist. ✅ Path A via real MCP server validated against multiple working examples (see catalog below). ⚠️ Path B (FunctionTool returning UI resource directly through AG-UI) needs a v6 spike — the resource shape is in the MCP Apps spec but the AG-UI `TOOL_CALL_RESULT` round-trip hasn't been smoke-tested end-to-end.

### Catalogue of real MCP App providers (researched 2026-04-11)

For the workshop demo we want servers that are visual, anonymous, and run in <1 minute on a laptop. Findings:

| # | Provider | What it shows | Wow score | Setup | License |
|---|----------|---------------|-----------|-------|---------|
| 1 | [`modelcontextprotocol/ext-apps`](https://github.com/modelcontextprotocol/ext-apps) `map-server` | Cesium 3D globe, geocodes via Nominatim, OSM tiles | ⭐⭐⭐⭐⭐ | `npm install && npm run start:http` | MIT (MCP org) |
| 2 | `ext-apps/threejs-server` (same repo) | Interactive Three.js 3D scene | ⭐⭐⭐⭐⭐ | Same | MIT |
| 3 | `ext-apps/shadertoy-server` (same repo) | Live GLSL shader rendered in chat bubble | ⭐⭐⭐⭐⭐ | Same | MIT |
| 4 | `ext-apps/wiki-explorer-server` | Wikipedia link graph as network diagram | ⭐⭐⭐⭐ | Same | MIT |
| 5 | `ext-apps/sheet-music-server` | ABC notation → rendered staff | ⭐⭐⭐⭐ | Same | MIT |
| 6 | [`microsoft/mcp-interactiveUI-samples`](https://github.com/microsoft/mcp-interactiveUI-samples) Field Service Dispatch | List + interactive Mapbox map + routing | ⭐⭐⭐⭐ | Per-sample README; needs free Mapbox token | MIT |
| 7 | `microsoft/...` Trey Research HR / Zava Insurance | Business dashboards, consultant cards, claims | ⭐⭐⭐ | Same | MIT |
| 8 | [`idosal/mcp-ui` hosted Worker](https://remote-mcp-server-authless.idosalomon.workers.dev/mcp) | Task / user status widgets | ⭐⭐⭐ | None — already live, no key | Apache-2.0 |
| 9 | [`mcp-widgets/examples`](https://github.com/mcp-widgets/examples) | Weather card, e-commerce product grid | ⭐⭐ | `npm install && npm run dev` | Community |

**Top picks for the talk:**

1. **`ext-apps/map-server` (PRIMARY).** A 3D Cesium globe rendering inside a chat bubble from an MCP tool result is the strongest possible "wait, that's in my chat??" moment. Zero keys, open data, official repo so the `@mcp-ui/client` payload shape is canonical.
2. **`ext-apps/shadertoy-server` (BACKUP / FLOURISH).** Same wiring, even more surprising. Use as the closing reveal after the map.
3. **`microsoft/mcp-interactiveUI-samples` Field Service Dispatch (ENTERPRISE NARRATIVE).** If the audience leans business, this tells a fuller story (map + list + routing in one flow). Needs a free Mapbox token — get it the morning of, not live on stage.

**Rejected / not what we need:**
- **Shopify Storefront MCP UI Server.** Repeatedly cited in blogs (Shopify Engineering, WorkOS) as the canonical showcase, but no public repo, npm package, or hosted endpoint found. Shopify calls it a "prototype." **Don't promise the audience a live Shopify demo.**
- **Stripe / Linear / Notion MCP servers.** Plain MCP — no UI resources. Audience already knows what these look like.
- **Smithery.ai / Composio catalogs.** No "UI" filter as of 2026-04; their listings are overwhelmingly plain MCP.
- **Goose / LibreChat / ui-inspector.** These are *hosts/clients* that render mcp-ui, not servers. Useful as cross-reference clients, not demo targets.

### Workshop demo flow (proposed narrative)

1. **Show path A — real MCP server, deployed.** Aitana hosts `ext-apps/map-server` as a Cloud Run sidecar (`mcp-ext-apps-map-dev`) alongside `platform-backend` — same project, same VPC, scaled to zero when idle. Two-tool flow: (a) ask "where's Munich?" → agent calls `geocode("Munich")` → gets bounding box; (b) agent calls `show-map(west, south, east, north, label="Munich")` → Cesium 3D globe renders inline in chat (UI fetched via `resources/read("ui://cesium-map/mcp-app.html")` per spec UI-by-reference pattern). End-to-end across two real Cloud Run services. *"That's an interactive 3D widget. From a third-party MCP server. Running in our prod-shaped infra. Rendered in our chat."*
2. **The active moment (decided 2026-04-30).** Click a pin on the globe inside the iframe → iframe sends a postMessage notification through `AppBridge` → host translates via the notification adapter → "Tell me more about Munich" appears in chat as a synthetic user message → agent responds with details. *"The iframe and the agent are now talking through a spec — neither knows about the other; we wrote zero map-specific code."*
3. **Reveal path B.** *"Now I'm going to show you the same kind of widget rendering — but there's no MCP server running."* Switch to a v6 ADK FunctionTool that mirrors the same UI-by-reference shape (or returns the UI embedded for simplicity). Same `<AppRenderer>`, same iframe sandbox, no MCP transport.
4. **Punchline.** *"MCP Apps' real value is the resource format and the postMessage contract, not the transport. That's why it composes with anything — MCP, AG-UI, plain REST. You don't need to run an MCP server to ship sandboxed widgets, but if you want third-party widgets you just stand one up — it's a Cloud Run service, not a SaaS contract."*
5. **Tie back to v6.** Aitana ships document widgets via path B (no extra process), composes path A by *self-hosting* open-source MCP servers as sidecars when the widget is too domain-specific to build ourselves. "Third-party code, first-party infrastructure" keeps the privacy boundary at the GCP project edge.

**Lesson #1 from the M1 spike (worth a slide on its own):**
> *"The spec calls these 'UI Resources'. The default mental model — 'tool returns HTML inline in its result' — is wrong. The default pattern is 'tool DEFINITION declares a UI resource URI; host fetches it on demand'. Same MIME, different choreography. If you build the host assuming embedded payloads, you'll wire the wrong thing and the iframe will be empty."*

**Lesson #2 from the M1 spike (workshop teaching moment) — DECIDED & SHIPPED 2026-04-30:**
> *"`@mcp-ui/client.AppRenderer` won't accept a CallToolResult and figure the UI out for you. It needs either an MCP Client (so it can `resources/read` itself) OR pre-fetched HTML. Picking which side fetches — frontend or backend — is the architectural decision the spec hands to you. **We chose frontend Client via a backend proxy** because v6 forks into a public template that downstream projects clone — getting the canonical spec surface in once is cheaper than retrofitting N times later. The +0.5 days now is rounding error against the multi-year template lifetime, and it gives the demo a credibility moment: the iframe and the agent both invoke MCP through the same wire surface, with no special wiring on either side. Caveat for the talk audience: ADK v1.24.1 doesn't plumb MCP client capabilities through `StreamableHTTPConnectionParams`, so we ship a documented header workaround (`x-aitana-mcp-ui-supported`) until the upstream gap closes."*

**Lesson #3 — the security boundary the proxy implies:**
> *"A frontend MCP Client means the browser can call `tools/call` against any MCP server it knows about. That's a vector if your proxy doesn't enforce who can call what. We gate `/api/proxy/mcp/{server_id}` on Firebase auth AND a per-skill allowlist — you can only proxy a server if you have access to a skill that activates it. This is the same access-control axis the agent already uses to decide which McpToolset to attach. Reusing that policy keeps the gate consistent across both invocation paths (agent-initiated + iframe-initiated)."*

**Lesson #4 — the six rough edges only a real wire-up surfaces (sprint 1.7 local smoke, 2026-04-30):**

> *"The spec reads clean. The wire-up has six papercuts you only find by sending one real chat turn through the whole stack. Here they are in order — each one is a 5-minute slide for the workshop because each one cost me 30 minutes to find."*

1. **Backend router prefix overlap with the frontend reverse-proxy.** Aitana's Next.js catch-all at `/api/proxy/[...path]` strips the `/api/proxy/` prefix and forwards `[...path]` to the backend. So a backend route declared at `prefix="/api/proxy/mcp"` becomes unreachable — the frontend hits `/api/proxy/mcp/X` → Next strips → backend sees `/mcp/X`. Fix: backend prefix is just `/mcp`. Surprise factor: the curl-from-laptop test still worked (it bypassed Next.js); only the in-browser path was broken.

2. **Reference-server runtime version drift.** `ext-apps@1.7.1`'s `map-server` example imports `App.registerTool()` at runtime — but the published `@modelcontextprotocol/ext-apps@1.0.0` it depends on doesn't have that method on the `App` class (it's a server-side helper, not browser-side). The example's `dist/mcp-app.html` was built against a newer monorepo internal version. Fix: build `@modelcontextprotocol/ext-apps` from source via `bun run build && node scripts/link-self.mjs` then rebuild the map-server's `vite build` against the local v1.7.1. Workshop point: *with rapidly-evolving young protocol packages, version mismatches between published packages and reference examples are the new normal — always check the example's `dist/` is fresh against the installed deps.*

3. **`AppRenderer` does NOT auto-forward resource `_meta.ui.csp` to the sandbox.** This is the longest one. The MCP server declares CSP requirements on the resource (e.g. `_meta.ui.csp.connectDomains: ["https://cesium.com"]`) — that's the spec. `AppRenderer` reads the resource HTML via `client.readResource()` and renders it. But the `csp` field on `SandboxConfig` is a **caller-supplied prop** — `AppRenderer` does NOT extract `_meta.ui.csp` from the response and propagate it. So if you don't pre-fetch the resource yourself, the sandbox iframe gets a default-restrictive CSP and Cesium fails to load from `cesium.com`. Fix: in our router, do `client.readResource(uri)` ourselves to extract both `html` and `_meta.ui.csp`, then pass `<AppRenderer html={html} sandbox={{url, csp}}>` (the `html` prop tells AppRenderer to skip its own fetch — no double request). Workshop talking point: *the spec puts the CSP on the resource because the server is the authority on what its UI needs; the client SDK could close the loop automatically but doesn't, so hosts adopting MCP Apps today own that wiring step.*

4. **Streamable HTTP needs both POST and GET — not just POST.** The MCP TS SDK's `StreamableHTTPClientTransport` opens a `GET` to the same URL as the `POST` channel, to receive server-to-client SSE notifications. A gateway-style proxy that only declares `POST /mcp/{server_id}` returns 404 on the GET; the SDK aborts the connection (`net::ERR_ABORTED 404`) even when JSON-RPC POSTs succeed. Fix: declare `POST`, `GET`, and `DELETE` (the latter for explicit session teardown). Same auth+allowlist gate on all three. Regression test added.

5. **Sandbox `SandboxConfig` prop instability triggers iframe remount races.** Naive code: `<AppRenderer sandbox={{url: new URL(SANDBOX_PROXY_URL)}}>` — fresh `URL` object on every render. AppFrame's effect deps include the sandbox config; identity change → effect re-runs → tear down iframe + create new one. The teardown races with the OLD iframe's `proxy-ready` postMessage from our sandbox.ts. Symptom: `Error: Timed out waiting for sandbox proxy iframe to be ready` AFTER the bridge logs say "sending proxy-ready". Fix: `useMemo` the sandbox config keyed on `resource.csp` identity. Workshop point: *iframes have lifecycle and identity that React's "everything is just a render" model fights with — when you bridge React state to an iframe via postMessage, prop stability stops being a perf concern and becomes a correctness concern.*

6. **`ui/update-model-context` is the bidirectional bonus you have to opt into.** When the iframe asks the host to do something, it goes via `AppBridge.sendMessage` (handled by `AppRenderer.onMessage` — that's how our pin-click-to-chat adapter works). But there's a SECOND iframe → host RPC: `ui/update-model-context` — the iframe pushes structured content (e.g. "the user is now looking at view-UUID `abc-123`, viewport bounds W/S/E/N") into the **agent's model context** for subsequent turns. The Cesium app uses this to record what view the user is looking at, so a follow-up "fly to its old town" knows what "it" is. **Plot twist (sprint 1.25):** there is NO dedicated `onUpdateModelContext` prop on `AppRenderer` — the method surfaces via the catch-all `onFallbackRequest` callback. Dispatch on `request.method === "ui/update-model-context"`. Pair with a backend endpoint that writes to ADK session state under a namespaced key, plus an InstructionProvider that injects the namespace into the next agent turn's prompt. **v6 has now wired both channels** — the three-turn workshop demo ("show me Munich" → "what city is currently centred?" → "now zoom in to its old town") works end-to-end. Workshop point: *MCP Apps' "active integration" story has two halves — synthetic chat turns (cheap, cosmetic) and model-context updates (subtle, makes the agent stateful about the iframe). The SDK's wiring for the second channel is non-obvious — onFallbackRequest is the route. Wire both for a credible "the agent and the UI are in conversation" demo.*

**The meta-lesson:** *every one of these is a 5-line fix once you know it. None of them are findable from reading the spec — they all surface only when you push one real turn through the wire end-to-end. Budget a half-day "hostile-environment smoke" before any workshop demo of a young protocol stack; the spec confidence and the production confidence are not the same number.*

**Update 2026-04-30 (sprint 1.25):** all six papercuts resolved; the harmless-but-annoying #6 (`ui/update-model-context`) shipped end-to-end the same day with a backend endpoint + per-skill opt-in + InstructionProvider that injects iframe state into the next agent turn's prompt. Three-turn workshop demo now works credibly: "show me Munich" → "what city is currently centred?" (answered "Munich" without re-rendering) → "now zoom in to its old town" (resolved "its" via context, called geocode + show-map, map re-rendered). See the verification log entry for the wire details and the `onFallbackRequest` plot twist.

**Pre-talk checklist:**
- [x] ~~Clone `modelcontextprotocol/ext-apps`, run `map-server` locally, verify port + transport~~ **DONE 2026-04-30** — port `localhost:3001/mcp`, transport `streamable_http`, fixtures captured (sprint 1.7 M1, commit 05bcd5f). One upstream tsc bug worked around via `bun --watch main.ts`.
- [ ] Build + deploy `mcp-ext-apps-map-dev` Cloud Run sidecar; smoke from deployed `platform-backend` — **sprint 1.7 M4**
- [x] ~~Verify CopilotKit's MCP client can consume the tool result shape~~ **N/A 2026-04-30** — v6 dropped CopilotKit; replacement is `@mcp-ui/client.AppRenderer` directly (sprint 1.7 M2A).
- [ ] Build the v6 Aitana FunctionTool returning a UI resource — smoke through AG-UI `TOOL_CALL_RESULT` (Path B verification; deferred per design doc until a real Path B use case emerges)
- [ ] Have a *local* `ext-apps/map-server` as the on-stage fallback if the deployed sidecar misbehaves (and `idosal/mcp-ui` hosted Worker as the fallback-of-the-fallback)
- [ ] Screenshot the working demo as the deck fallback if everything breaks (sprint 1.7 M3 captures this)
- [ ] **Bump `mcp-ext-apps-map-dev` to `--min-instances=1` 24h before workshop** to defeat cold-start lottery; drop back after.

### Open questions / unverified

- ~~**CopilotKit ↔ MCP Apps wiring.**~~ **N/A 2026-04-30** — v6 dropped CopilotKit (per `gotcha_copilotkit_not_agui_native`). The right wiring for v6 is `@mcp-ui/client.AppRenderer` directly atop AG-UI's `TOOL_CALL_RESULT`, not `CopilotRuntime.mcpApps.servers`. See verification log 2026-04-30.
- **AG-UI carrying UI resources.** AG-UI's `TOOL_CALL_RESULT.content` is the right channel — verified 2026-04-30. The wire question that REMAINS open: does the *full UI binding* (tool definition `_meta.ui.resourceUri` + tool input + tool result) cleanly round-trip through ADK's tool callback chain into AG-UI events without losing the `_meta` annotations? (ADK strips some metadata.) Phase 2 spike will pin this.
- **`ext-apps` license text.** Repo has a LICENSE file; under the MCP org it should be MIT — confirm before redistributing in a workshop kit.
- ~~**Exact ports + transports** for each `ext-apps` example~~ **VERIFIED 2026-04-30** — `examples/map-server` defaults to `localhost:3001/mcp` via `streamable_http`. Per-tool start commands in each example's `package.json` (typically `npm run start:http`). See verification log 2026-04-30.
- **(NEW) Architectural choice point — frontend MCP Client vs backend pre-fetch.** Surfaced 2026-04-30 from M1 spike. `<AppRenderer>` requires either `client?: Client` (an MCP SDK Client, which v6 frontend doesn't have today — backend has it via ADK) OR pre-fetched `html?: string`. Three paths: (A) backend exposes `/api/proxy/mcp/{server_id}` so frontend can hold its own MCP Client; (B) backend pre-fetches HTML and attaches to AG-UI `TOOL_CALL_RESULT` event; (C) skip `<AppRenderer>`, use `<AppFrame>` directly with manually fetched HTML. Decision deferred to user; design doc captures all three.
- **(NEW) ADK MCP capability passthrough.** Does `StreamableHTTPConnectionParams` plumb client `capabilities` (e.g. `UI_EXTENSION_CAPABILITIES`) through to the underlying MCP `Client` ctor? If not, file an upstream ADK gap and use `header_provider` workaround. Phase 2 verifies via `mcp__adk-mcp__search_code`.
- **(NEW) ext-apps build bug.** `ext-apps@1.7.1` map-server's `npm run build` fails on a real upstream tsc error (`Property 'registerTool' does not exist on type 'App'`). Workaround: skip the build, run via `bun --watch main.ts`. Phase 4 Cloud Run sidecar Dockerfile needs to handle this — pin a different commit or patch the type. File upstream issue.

**Links:**
- `@mcp-ui/client`: https://www.npmjs.com/package/@mcp-ui/client
- mcpui.dev (project site)
- `modelcontextprotocol/ext-apps`: https://github.com/modelcontextprotocol/ext-apps
- `microsoft/mcp-interactiveUI-samples`: https://github.com/microsoft/mcp-interactiveUI-samples
- `idosal/mcp-ui`: https://github.com/idosal/mcp-ui
- MCP Apps blog post (2026-01-26): https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/

---

## When to reach for what — decision matrix

| Need | Use | Don't use |
|------|-----|-----------|
| Stream tokens to a chat UI | **AG-UI** | Custom SSE |
| Render a form the agent generated | **A2UI** | Hand-rolled HTML strings |
| Let user edit data and send it back | **A2UI** with two-way binding | Custom websocket events |
| Sandbox a third-party tool's UI | **MCP Apps** | An unprotected iframe |
| Make your agent discoverable by other AI products | **A2A** | Custom directory API |
| Plug an external tool into your agent | **MCP** | Custom tool adapter |
| Internal Python function as a tool | **ADK FunctionTool** | MCP (overkill for in-process) |
| Build the agent loop itself | **ADK** | Hand-rolled while loop |

---

## The "five minutes to a working agent" demo (workshop opener)

There's a CopilotKit CLI scaffolder that builds the entire stack in one command. Verified live on 2026-04-10.

```bash
npx copilotkit@latest create my-app -f adk
cd my-app
export GOOGLE_API_KEY=...
npm install   # postinstall also bootstraps agent/.venv
npm run dev   # concurrently runs Next.js (3000) + FastAPI agent (8000)
```

**What you get:**

```
my-app/
├── agent/
│   ├── main.py            ← LlmAgent + ag-ui-adk + add_adk_fastapi_endpoint
│   └── pyproject.toml     ← google-adk, ag-ui-adk, fastapi, uvicorn
├── src/
│   ├── app/
│   │   ├── layout.tsx     ← <CopilotKit> provider
│   │   ├── page.tsx       ← <CopilotSidebar> + useCoAgent + useRenderToolCall
│   │   └── api/copilotkit/route.ts  ← CopilotRuntime + HttpAgent bridge
│   ├── components/
│   │   ├── proverbs.tsx   ← shared-state demo (useCoAgent)
│   │   └── weather.tsx    ← generative-UI demo (useRenderToolCall)
│   └── lib/types.ts       ← AgentState mirror of Pydantic ProverbsState
├── docker/
└── package.json           ← concurrently runs both halves with one `npm run dev`
```

The scaffold demonstrates **all three killer patterns** in one app:
1. **Backend tool → React component** (`useRenderToolCall` → `WeatherCard` for the Python `get_weather` tool)
2. **Frontend tool → backend agent** (`useFrontendTool` for `setThemeColor`, agent learns it can call this and does)
3. **Bidirectional shared state** (`useCoAgent<{proverbs: string[]}>` mirrors the Python `ProverbsState` Pydantic class — edits in either direction sync)

**Available framework targets** (verified from `copilotkit create --help`):
`langgraph-py | langgraph-js | mastra | flows | llamaindex | agno | pydantic-ai | ag2 | adk | aws-strands-py | a2a | microsoft-agent-framework-dotnet | microsoft-agent-framework-py | mcp-apps`

That list alone is a great talk slide: it's a one-line proof that the protocol stack has real ecosystem traction across at least 14 backend frameworks, all reachable from the same React frontend via AG-UI.

**Workshop value:** participants type one command, get a working agent in under 5 minutes, and now have a runnable surface to mod. From there, every protocol concept (tools, state, generative UI, HITL) is a hook call away.

---

## Anti-patterns I'd call out in the talk

1. **"We'll just write custom SSE."** Done that, lost a quarter to it. AG-UI gives you the same wire format that every CopilotKit-using product uses, with a maintained Python ↔ TypeScript pair.
2. **"We'll render markdown for everything."** Markdown can't do two-way data binding. The moment a user wants to edit a table the agent showed them, you're rebuilding A2UI badly.
3. **"We'll wrap our internal Python functions as MCP servers."** No. ADK FunctionTool is in-process, faster, and doesn't need a separate transport. MCP is for *external* tools.
4. **"AG-UI events are too granular, we'll batch them."** They already are batched at the SSE flush boundary. Don't second-guess the framing.
5. **"We'll skip A2A because nobody discovers our agent yet."** Publishing the card is ~50 lines and it's how the next product on the stack will find you.
6. **"ADK's `get_fast_api_app()` probably handles AG-UI."** It doesn't. (I assumed this in our first design doc and was wrong — see verification log.)
7. **"We'll build the chat UI from scratch because we want our brand."** `<CopilotSidebar>` themes via a single CSS variable (`--copilot-kit-primary-color`) and slot overrides. Building from scratch is 2 days; theming the off-the-shelf one is half a day. (I made this mistake in our first sprint plan — saved 4 days when caught.)
8. **"AG-UI is just SSE, we'll write the React side ourselves with `EventSource`."** You'll re-derive `useCoAgent`, `useRenderToolCall`, and `useFrontendTool` over six weeks. CopilotKit already wrote them.
9. **"We'll just emit JSON in our chat messages and parse it client-side."** Then you've reinvented A2UI badly and you can't share renderers with anyone else's product. Just emit A2UI.
10. **"We adopted AG-UI — we use `ag_ui_adk` on the backend."** Half-adopting a protocol breaks silently. `ag_ui_adk` handles the agent execution layer, but the *HTTP ingress* must also accept the protocol's wire format (`RunAgentInput`). If your FastAPI endpoint uses a custom `{message: str}` body instead, `HttpAgent` sends `{messages:[...], threadId}` which Pydantic silently drops (extra fields ignored), `message` stays `""`, and ADK errors on every call. The failure manifests as `RUN_ERROR` in the frontend, not a 4xx — no stack trace, no obvious cause. **The rule: own the full boundary.** Accept `RunAgentInput` at the HTTP layer; let `ag_ui_adk` consume it directly. *(Hit in v6 bring-up, 2026-04-23 — cost half a day to diagnose because 200 OK masked the streaming failure.)*
11. **"We'll read per-turn signals from `RunAgentInput.state`."** AG-UI's `state` channel is a *backend output*, not a per-turn input from the user. The `HttpAgent` client mirrors every `STATE_SNAPSHOT` event from the agent into `agent.state` and round-trips it back on the next `runAgent()` call. That makes wire `state` always **one turn behind** — perfect for resumption hints, lethal for "what did the user just do." The right channel for fresh per-turn signals is `forwardedProps` (or for a bare CLI, top-level body fields). *(Hit 2026-04-28: user opens doc B mid-session, frontend sends `forwardedProps.document_ids=[A,B]`, backend reads `state.document_ids=[A]` from the prior turn's STATE_SNAPSHOT and ignores the fresh signal. Symptom: agent denies the second doc exists. Fix: read `forwardedProps` first, fall back to `state` only as a last resort. See [docs/design/v6.1.0/implemented/multi-doc-context-fix.md](../design/v6.1.0/implemented/multi-doc-context-fix.md).)*

    **Sub-trap — `temp:` prefix won't save you here.** Naïve reflex: "the bare-key write at the backend echoes into the wire snapshot, so prefix it with `temp:` to suppress the round-trip." It doesn't work. ADK's `temp:` semantics are *in-invocation only* — `base_session_service._trim_temp_delta_state` strips temp keys from `event.actions.state_delta` *before* persistence. But `ag_ui_adk` applies wire state via `update_session_state` → `append_event`, then immediately re-fetches the session via `get_session` (line ~1804 of `adk_agent.py`, "refresh session to get updated last_update_time"). The temp value lives on a transient session copy that gets garbage-collected; the runner sees a fresh storage copy with the temp key already gone. Net: callbacks downstream of the wire never see `temp:document_ids`. Verified by running the E2E suite after migrating — three tests broke at the loader because the temp key was stripped before the runner started. **Lesson:** the temp prefix is for state YOU write inside callbacks during an invocation, not for state crossing the AG-UI wire boundary. For wire-borne per-turn signals, the parser priority (forwardedProps > body > state) is the fix. The bare-key write keeps round-tripping; the parser just refuses to read the round-trip.

---

## Verification log — things I've actually run / read

| Date | Claim | Verified by |
|------|-------|-------------|
| 2026-04-10 | `ag-ui-adk` v0.6.0 exists on PyPI, MIT, Python 3.10–3.14 | WebFetch on pypi.org/project/ag-ui-adk |
| 2026-04-10 | `ag-ui-protocol` v0.1.15 ships Pydantic models + EventEncoder | WebFetch on pypi.org |
| 2026-04-10 | AG-UI has 16 canonical event types in 6 categories | WebFetch on docs.ag-ui.com/concepts/events |
| 2026-04-10 | `ADK get_fast_api_app()` does **not** natively emit AG-UI | ADK MCP search_code (no AG-UI emitter found) |
| 2026-04-10 | `add_adk_fastapi_endpoint()` is the Python integration pattern | WebFetch on pypi.org/project/ag-ui-adk |
| 2026-04-10 | `@copilotkit/react-ui` v1.55.1 exists, ships `<CopilotSidebar>`, `<CopilotChat>`, `<CopilotPopup>` | `npm view @copilotkit/react-ui` |
| 2026-04-10 | `@copilotkit/a2ui-renderer` v1.55.1 exists ("A2UI Renderer for CopilotKit - render A2UI surfaces in React applications") | `npm view @copilotkit/a2ui-renderer` |
| 2026-04-10 | `@mcp-ui/client` v7.0.0 exists on npm | `npm view @mcp-ui/client` |
| 2026-04-10 | `@a2ui-sdk/react` v0.4.0 (community shadcn-flavored A2UI renderer) exists | `npm view @a2ui-sdk/react` |
| 2026-04-10 | `npx copilotkit@latest create -f adk` is real and produces a working scaffold | Ran the command, inspected `/tmp/copilotkit-adk-scaffold/` |
| 2026-04-10 | The CopilotKit CLI supports 14 framework targets including `adk`, `a2a`, `mcp-apps`, `langgraph-py/js`, `mastra`, `pydantic-ai`, etc. | `npx copilotkit create --help` |
| 2026-04-10 | Three-tier architecture: React → Next.js `/api/copilotkit` (CopilotRuntime + HttpAgent) → Python FastAPI (ag-ui-adk) | Read `src/app/api/copilotkit/route.ts` from scaffold |
| 2026-04-10 | `useCoAgent<T>` provides bidirectional shared state via Pydantic ↔ TS mirror | Read `src/app/page.tsx` + `agent/main.py` from scaffold |
| 2026-04-10 | `useRenderToolCall` provides streaming generative UI for backend tool calls | Read scaffold's weather component |
| 2026-04-10 | `useFrontendTool` lets agent call TS-defined tools (e.g. `setThemeColor`) | Read scaffold's `page.tsx` |
| 2026-04-10 | Scaffold uses `LlmAgent` with string model name and ADK callbacks (`before_model_callback`) for state injection | Read scaffold's `agent/main.py` |
| 2026-04-10 | Scaffold uses `use_in_memory_services=True` and does NOT show custom session service injection | Read scaffold's `agent/main.py` |
| 2026-04-10 | `<CopilotKit>` provider needs both `runtimeUrl` and `agent` props, and `@copilotkit/react-ui/styles.css` must be imported in layout | Read scaffold's `layout.tsx` |
| 2026-04-10 | Theming via `--copilot-kit-primary-color` CSS var with typed `CopilotKitCSSProperties` | Read scaffold's `page.tsx` |
| 2026-04-11 | `modelcontextprotocol/ext-apps` is the official MCP Apps reference repo with ~15 working server examples (map, threejs, shadertoy, wiki-explorer, sheet-music, pdf, qr-code, business dashboards) | Research agent — github.com/modelcontextprotocol/ext-apps |
| 2026-04-11 | `ext-apps/map-server` ships a Cesium 3D globe rendered in-iframe, geocodes via Nominatim, no API keys required | Research — repo README + examples folder |
| 2026-04-11 | `microsoft/mcp-interactiveUI-samples` ships polished business MCP App samples (Field Service Dispatch, Trey Research HR, Zava Insurance) under MIT | Research — github.com/microsoft/mcp-interactiveUI-samples |
| 2026-04-11 | `idosal/mcp-ui` runs a public anonymous Cloudflare Worker MCP server at `remote-mcp-server-authless.idosalomon.workers.dev/mcp` with task/user widget tools | Research — idosal/mcp-ui repo |
| 2026-04-11 | Shopify Storefront MCP UI Server is referenced in blogs but has no public repo, npm package, or hosted endpoint — treat as vaporware for live demos | Research — couldn't find a runnable artifact |
| 2026-04-11 | MCP Apps' value is the **resource format + postMessage contract**, not the transport — `<UIResourceRenderer>` accepts the resource shape regardless of whether it arrived via MCP `tools/call`, AG-UI `TOOL_CALL_RESULT`, plain REST, or a static fixture | Spec inspection: renderer takes a JS object, doesn't introspect provenance |
| 2026-04-11 | "Third-party MCP server" in 2026-04 means *code we didn't write*, not *infrastructure we don't run* — `ext-apps` and `microsoft/mcp-interactiveUI-samples` are reference repos meant to be cloned and operated, not hosted SaaS endpoints. Only `idosal/mcp-ui` ships a truly public hosted Worker. | Catalog research, repo READMEs |
| 2026-04-11 | **Decision:** Aitana hosts `ext-apps/map-server` as a standalone Cloud Run sidecar `mcp-ext-apps-map-{env}` (Node runtime, separate image, scale-to-zero, called from `platform-backend` over the VPC connector). Captured in [docs/design/v6.1.0/mcp-app-integrations.md](../design/v6.1.0/mcp-app-integrations.md) Phase 4. | Architecture decision — keeps Python and Node runtimes separate, preserves privacy boundary at GCP project edge |
| (pending) | Custom session service injection through `ADKAgent` (when `use_in_memory_services=False`) | Phase 0.4 spike |
| (pending) | Firebase auth + `add_adk_fastapi_endpoint` middleware ordering | Phase 0.4 spike |
| (pending) | `@a2ui/react` editable mode at v0.9.0-alpha.0 stability | Phase 1B.4 |
| (pending) | `@copilotkit/a2ui-renderer` `createA2UIMessageRenderer` exact API | Phase 1B spike |
| (pending) | Bundle-size impact of CopilotKit + AG-UI client + A2UI renderer combined | Phase 1B measure |
| 2026-04-13 | **CopilotKit ↔ MCP Apps wiring — RESOLVED.** CopilotKit does NOT use `@mcp-ui/client`. It has its own parallel implementation: `@ag-ui/mcp-apps-middleware` (v0.0.3) discovers UI-enabled tools via `_meta["ui/resourceUri"]`, executes them, emits `ACTIVITY_SNAPSHOT` events with `activityType: "mcp-apps"`. Frontend `MCPAppsActivityRenderer` (built into CopilotKit) receives these, fetches renderer HTML via proxied `resources/read`, renders in sandboxed iframe. **Zero custom frontend routing code needed** — configure MCP servers on `CopilotRuntime.mcpApps.servers` and it works. This means the planned `MCPAppToolCallRenderer.tsx` in the mcp-app-integrations doc is likely unnecessary. | Research: CopilotKit `mcp-apps` scaffold (`npx copilotkit create -f mcp-apps`), CopilotKit docs `/docs/snippets/shared/generative-ui/mcp-apps.mdx`, `@ag-ui/mcp-apps-middleware` source |
| 2026-04-13 | **Prefab (Prefect) works with CopilotKit without any adapter.** Prefab uses standard MCP Apps wire format: tools declare `_meta.ui.resourceUri = "ui://prefab/tool/<hash>/renderer.html"`, tool results carry Prefab JSON in `structuredContent`, renderer HTML is served as a `text/html;profile=mcp-app` resource. CopilotKit middleware sees a standard MCP App — never knows it's Prefab. The Prefab renderer (CDN stub or 6.6MB self-contained bundle) runs inside the sandbox iframe and interprets the JSON component tree. 80+ built-in components (charts, tables, forms, dialogs), reactive state via `Rx()` expressions, all authored in Python. Strong candidate for Path B (our own tool UIs) — write Python instead of hand-crafting iframe HTML. Pre-release (0.x), pin exact versions. | Research: `prefab.prefect.io/docs`, FastMCP source (`fastmcp/server/providers/prefab_synthesis.py`), `prefab_ui` wire format |
| 2026-04-13 | **MIME type flag:** CopilotKit middleware (`@ag-ui/mcp-apps-middleware` v0.0.3) advertises `mimeTypes: ["text/html+mcp"]` in MCP client capabilities, while the current spec uses `text/html;profile=mcp-app`. Likely a version lag. **Must test explicitly in Phase 1 spike** — if the mismatch causes servers to not advertise UI resources, need to pin a compatible middleware version or override. | `@ag-ui/mcp-apps-middleware` source inspection |
| 2026-04-28 | **AG-UI `state` is a backend output, not a per-turn input.** `HttpAgent.prepareRunAgentInput` puts `state: this.state` in the body, where `this.state` is updated by `STATE_SNAPSHOT` events the backend emits. Round-trip = one turn behind. Per-turn signals belong on `forwardedProps`; bare-key state writes round-trip via `event_translator._create_state_snapshot_event` (which passes prefixes through unchanged). Fix is parser-side priority (forwardedProps > body > state), not state-side prefixing — see next row. | Read `ag_ui_adk/event_translator.py:1162` + reproduced multi-doc bug end-to-end; fix in commit 559b9e3 |
| 2026-04-28 | **`temp:` prefix doesn't suppress round-trip when the value crosses the AG-UI wire.** Tried migrating `initial_state["document_ids"]` to `temp:document_ids` in skill_processor to make the bare-key round-trip go away. ADK's `base_session_service._trim_temp_delta_state` strips temp keys from event state_delta before persistence; `ag_ui_adk` then re-fetches the session via `get_session` after `update_session_state`, so the temp value (only on a transient copy) is gone before the runner starts. Three E2E tests broke at the loader. `temp:` is for in-invocation callback writes, not wire inputs. | Migrated forward + ran `pytest tests/api_tests/test_documents_reach_agent_e2e.py` — three failures at the loader assertion; reverted |
| 2026-04-21 | **Public template decision.** `sunholo-data/ai-protocol-platform` forks into a public `platform-template` after 1A.5 streaming + 1A.6 chat-history + 1B.1 frontend-architecture land. Target mid-to-late May 2026 → ~6 weeks of buffer before July workshop. Terraform stays private (too much GCP-org specificity to sanitize for little gain); ops *patterns* travel as prose in `docs/gotchas/`. Upstream/downstream topology — public is canonical for platform core, private merges public→private weekly. | [docs/design/v6.0.0/template-split-strategy.md](../design/v6.0.0/template-split-strategy.md) |
| 2026-04-30 | **MCP Apps spec is UI-by-REFERENCE, not embedded.** Captured live from `ext-apps@1.7.1` map-server: `tools/list` includes `_meta.ui.resourceUri = "ui://cesium-map/mcp-app.html"` AND `_meta["ui/resourceUri"]` (both forms — backwards-compat shim) on each UI-bearing tool. `tools/call` returns ONLY data result content (plain text + `_meta.viewUUID`); NO embedded UI resource. Host fetches HTML via `resources/read(uri)` separately. Resource MIME is canonical `text/html;profile=mcp-app` (383KB inlined CesiumJS via vite singlefile). The earlier mental model — "router inspects tool result for embedded resource" — does NOT match reality. | Captured fixtures committed to `frontend/src/components/protocols/__tests__/fixtures/map-server-{tools-list,show-map-result,ui-resource}.json` (commit 05bcd5f) |
| 2026-04-30 | **`@mcp-ui/client@7.0.0` is `AppRenderer` (not `UIResourceRenderer`).** v7 rename. Reading `node_modules/@mcp-ui/client/dist/src/components/AppRenderer.d.ts`: requires `client?: Client` (MCP SDK Client instance) OR pre-fetched `html?: string`. Will NOT figure out the UI from a CallToolResult alone — needs the tool DEFINITION (with `_meta.ui.resourceUri`) and either an MCP Client (to fetch the resource) or pre-fetched HTML. Provides `onMessage`, `onCallTool`, `onListResources`, `onReadResource` callbacks for iframe → host requests via `AppBridge` + `PostMessageTransport`. The `onMessage` callback is the right hook for "iframe sends a notification → translate → append synthetic chat message" (the workshop W7 active-integration moment). | `node_modules/@mcp-ui/client/dist/src/components/AppRenderer.d.ts` (commit 05bcd5f docs) |
| 2026-04-30 | **`UI_EXTENSION_CONFIG.mimeTypes` is `["text/html;profile=mcp-app"]`.** Older `text/html+mcp` variant (cited in CopilotKit middleware 2026-04-13 row above) does NOT appear in current `@mcp-ui/client` or `@modelcontextprotocol/ext-apps` packages. We adopt the canonical form. SEP-1724 `UI_EXTENSION_CAPABILITIES` is the right declaration shape: `{"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}`. | `node_modules/@mcp-ui/client/dist/src/capabilities.d.ts` |
| 2026-04-30 | **ext-apps@1.7.1 map-server has an upstream tsc bug** at `src/mcp-app.ts:876` — `Property 'registerTool' does not exist on type 'App'`. Blocks `npm run build`. Workaround: skip the build, run via `bun --watch main.ts` directly (vite still needs to build the `dist/mcp-app.html` resource). For Phase 4 Cloud Run sidecar Dockerfile: either pin a different commit, patch the type, or run the bun-direct path in the container. File upstream issue against `modelcontextprotocol/ext-apps`. | Tried `npm run --workspace examples/map-server build`; failed at `tsc --noEmit`; bun-direct path works |
| 2026-04-30 | **v6 dropped CopilotKit; the 2026-04-13 `@ag-ui/mcp-apps-middleware` plan is moot.** `frontend/package.json` only has `@ag-ui/client` + `@ag-ui/core` directly (per `gotcha_copilotkit_not_agui_native` memory). `CopilotRuntime.mcpApps.servers` config doesn't exist in v6. Replacement: adopt `@mcp-ui/client.AppRenderer` directly atop `@ag-ui/client` HttpAgent. Same protocol surface, no extra runtime. Plan captured in [docs/design/v6.1.0/mcp-app-integrations.md](../design/v6.1.0/mcp-app-integrations.md) (commit 265d086). | Audit + design doc rescope |
| (pending) | Path B verification: ADK FunctionTool returning a UI resource → AG-UI `TOOL_CALL_RESULT` (or attached `_meta.ui.html`) → frontend `<AppRenderer>` renders in iframe end-to-end. After 2026-04-30 spec audit: route is via `TOOL_CALL_RESULT.content[].resource` for embedded payload OR via `_meta.ui.resourceUri` reference like real MCP servers do. **Architectural choice point:** frontend MCP Client (Path A) vs backend pre-fetch (Path B) vs raw AppFrame (Path C) — see mcp-app-integrations design doc. | M2A pre-work resolved the renderer-API mismatch; user decision needed before router code lands |
| 2026-04-30 | **Path A SHIPPED end-to-end (sprint 1.7 M2A + M2B + M3).** Frontend `mcpClient.ts` instantiates `Client` from `@modelcontextprotocol/sdk/client/streamableHttp` against backend proxy `/api/proxy/mcp/{server_id}` (auth: Firebase + per-skill allowlist on the proxy — caller must have access to ≥1 skill that includes server_id in `tool_configs.mcp.servers`). `<AppRenderer client={...} toolName={...} toolInput={...} toolResult={...} onMessage={...}>` mounts the iframe via the spec-compliant separate-origin sandbox proxy. Active iframe → host integration translates spec-defined `notifications/message` shapes (location-selected, route-selected) into synthetic chat turns via `mcpAppNotificationAdapter`. Wire-format facts: ADK does NOT plumb client capabilities through `StreamableHTTPConnectionParams`; M2B used `header_provider` workaround (`x-aitana-mcp-ui-supported`) and filed as upstream gap against `google/adk-python`. | Sprint 1.7 commits 05bcd5f → 9cc9b27 → c41e8c6 → (this commit) |
| 2026-04-30 | **Sandbox proxy SHIPPED as separate-origin Node service** (sprint 1.7 M3, doc `mcp-sandbox-separate-origin.md`). `infrastructure/mcp-sandbox/` is a tiny Express server (~120 LOC serve + ~140 LOC bridge → 3.5 KB browser bundle) on port 3457 in dev, separate Cloud Run service `mcp-sandbox-{env}` in deployed envs (M4). Adapted from `modelcontextprotocol/ext-apps/examples/basic-host` commit 0008d3b7. CSP via HTTP headers (tamper-proof; meta-tag CSP can be modified by served HTML). Referrer + origin validation on every postMessage. 17 vitest cases cover the CSP builder + sanitizer (rejects `;`, newlines, quotes, spaces — CSP injection vector closed). The double-iframe security architecture is now correct in v6, not a placeholder. | Adopted reference impl wholesale; smoke-tested via `make dev` + curl |
| 2026-04-30 | **Local end-to-end smoke surfaced six wiring papercuts** (sprint 1.7 follow-up, commits 4c7b826 + 1fdf941). All written up as Lesson #4 above. (1) Backend MCP proxy router prefix `/api/proxy/mcp` overlapped with Next.js's `/api/proxy/[...path]` strip — route unreachable through the browser path; fixed by changing backend prefix to `/mcp`. (2) `ext-apps@1.7.1` map-server's `dist/mcp-app.html` was built against a newer internal version of `@modelcontextprotocol/ext-apps` than the published v1.0.0 in `node_modules`; runtime `pe.registerTool is not a function`; fixed by `bun run build && node scripts/link-self.mjs` in the ext-apps monorepo, then `vite build` the map-server. (3) `AppRenderer` does NOT auto-propagate the resource `_meta.ui.csp` to the sandbox URL — the `csp` field on `SandboxConfig` is a CALLER-supplied prop. Hosts must `client.readResource(uri)` themselves, extract `_meta.ui.csp`, and pass `<AppRenderer html={html} sandbox={{url, csp}}>` (the `html` prop also tells AppRenderer to skip its own fetch, so no double request). (4) MCP TS SDK's `StreamableHTTPClientTransport` opens GET (SSE channel) alongside POST; backend proxy that only declared POST returned 404 on the GET → SDK aborts with `net::ERR_ABORTED`; fixed by adding GET + DELETE handlers, all gated on the same auth+allowlist. (5) `<AppRenderer sandbox={{url: new URL(...)}}>` recreated the URL on every render → AppFrame iframe-remount race vs `proxy-ready` postMessage → "Timed out waiting for sandbox proxy iframe to be ready"; fixed by `useMemo` the sandbox config. (6) `ui/update-model-context` from the iframe was unhandled — non-fatal but the agent stays blind to iframe state. | Reproduced live in chat with a real Firebase login, fixed all six, Cesium globe now renders Munich/Copenhagen correctly with bounds from the agent's `show-map` tool input. |
| 2026-04-30 | **`ui/update-model-context` is a separate iframe→host RPC from `ui/message`.** Discovered via the harmless backend error during the local smoke. The Cesium app fires it AFTER successful camera positioning, with `{structuredContent: {viewUUID: "...", currentBounds: {w, s, e, n}, ...}}`. The host is supposed to merge that structured content into the next agent turn's context. Without an `onUpdateModelContext` handler on `AppRenderer`, the iframe gets `MCP error -32601: No handler for method` (rejected RPC), the map still renders, but the agent doesn't know what's on screen — so a follow-up "now show me the city centre" can't reference the current view. **Two iframe→host RPC channels to wire, not one:** `onMessage` (synthetic chat turns — cosmetic) AND `onUpdateModelContext` (model context updates — makes the agent stateful about the iframe). Track as a follow-up; not blocking for July workshop demo, but the "stateful UI in conversation with the agent" narrative needs it. | Live console error during sprint 1.7 chat smoke, traced to `index.mjs:5722` calling `sendToolInput` then iframe app calling `updateModelContext` |
| 2026-04-30 | **`ui/update-model-context` SHIPPED end-to-end (sprint 1.25).** Three-turn live smoke verified: "show me Munich" → globe renders + iframe-context POST 204; "what city is currently centred?" → agent answered "**The city currently centered on the map is Munich.**" (read namespaced state from agent prompt, no map re-render); "now zoom in to its old town" → agent answered "**I've updated the map to zoom in on Munich's Old Town.**" (resolved "its" via context, called geocode + show-map, map re-rendered). Surprise during wire-up: AppRenderer has NO dedicated `onUpdateModelContext` prop — the spec method surfaces via the catch-all `onFallbackRequest` callback (dispatch on `request.method === "ui/update-model-context"`). Backend wire: new `POST /api/sessions/{id}/iframe-context` with seven access gates (Firebase + session-access + skill-exists + server-in-`tool_configs.mcp.servers` + server-in-NEW-`tool_configs.mcp.allow_context_writes`-opt-in + schema + 4 KB size cap); `wrap_with_iframe_context` InstructionProvider injects `mcp_app_context.{server}.{tool}` namespace into agent prompt with explicit framing prose. DevTools clean of `-32601` after deploy. | Live three-turn chat against ext-apps map-server through full prod-shaped wire (frontend → /api/proxy → backend → /api/proxy/mcp + /api/sessions/{id}/iframe-context → Vertex Agent Engine session state); backend access log + frontend snapshot screenshots captured |
| (pending) | Dry-run clone-and-deploy of the public `platform-template` by a fresh pair of hands, before workshop. Confirms README walks a newcomer from `git clone` to a live AG-UI chat in <10 minutes without Aitana infra. | Post-fork, pre-workshop |
| (pending) | `ext-apps` map-server runs locally with confirmed port + transport, payload shape works with CopilotKit's `@ag-ui/mcp-apps-middleware` | Pre-talk dry-run |
| (pending) | MIME type compatibility: verify `text/html+mcp` (CopilotKit middleware) vs `text/html;profile=mcp-app` (current spec) doesn't break tool discovery | Phase 1 spike |
| 2026-04-19 | **A2UI v0.9 (alpha) landed.** Key deltas vs our design: (1) Python Agent SDK `a2ui-agent-sdk` with `schema_manager.generate_system_prompt()` / `parse_response_to_parts()` — v6 does NOT use it yet (prompts hand-rolled in ADK instructions); (2) "Standard" component set renamed to **"Basic"**; custom catalogs via `CatalogConfig.from_path()`; (3) resilient streaming (incremental parse/heal); (4) client-defined validation functions; (5) client-to-server collab sync. `@a2ui/react` in v6 `package.json` already on `0.9.0-alpha.0`. | Research — [A2UI v0.9 Generative UI blog post](https://developers.googleblog.com/a2ui-v0-9-generative-ui/) |
| (pending) | **Decide: adopt `a2ui-agent-sdk` (Python) before workshop?** Swap hand-rolled A2UI prompt scaffolding for `schema_manager` in Phase 1B. Blocker risk: alpha API could shift before 2026-07. | Phase 1B decision point |
| (pending) | **A2A 1.0 `/.well-known/agent.json` endpoint** — design-frozen, TODO at `backend/fast_api_app.py:86`. Needed before workshop narrative can credibly claim "protocol-first discovery." | Phase 1A |
| (pending) | **Resilient streaming spike** — measure cost vs payoff of incremental A2UI rendering mid-stream. Only promise this on stage if verified. | Pre-talk spike |
| (pending) | **Custom `CatalogConfig.from_path()` for Aitana components** — not v6.0.0 critical, but the workshop "bring your own design system" narrative leans on it. | Phase 1B+ |
| 2026-05-19 | **Sprint 2.14 tenant-id-span-attribute shipped end-to-end — AIPLA template-extension sequence COMPLETE (4-of-4).** Universal per-tenant OTel attribution via a contextvar + SpanProcessor pair. Single insertion point in `auth.get_current_user` covers all 13 endpoints via dispatcher patch (helper extraction: `_resolve_user` does the existing dispatch; `get_current_user` wrapper calls `set_tenant_context(user)` at the exit, before returning). M1: `backend/observability/tenant_context.py` (153 LOC; contextvar + `set_tenant_context` reads User fields EXPLICITLY/no reflection so display_name etc. cannot leak; `register_tenant_enricher` validates callable; `_hash_email` is SHA256) + `tenant_span_processor.py` (50 LOC; standard OTel SpanProcessor; on_start stamps attrs) — 15 unit tests including concurrent-task isolation under asyncio.gather + barriers proving zero cross-tenant leakage. M2: `auth/__init__.py` dispatcher refactor (single insertion point covers all three auth paths) + `telemetry.py` best-effort SpanProcessor registration (gracefully no-ops on the default ProxyTracerProvider with INFO log) + integration test (5 cases for Firebase + group-auth + LOCAL_MODE paths + concurrent-tenant isolation + dispatcher-sets-contextvar regression). M3: privacy hardening — golden SHA256 verification test, UserWithDisplayName duck-type test proving the impl is not reflection-based, scan-every-value defence-in-depth, extra-kwarg-override documented (4 new tests). M4: fork-adoption howto. **Backend tests: 1293 passed (was 1269 at sprint start, +24 across M1+M2+M3). make lint + make test-fast green at every milestone close.** The four non-PII platform defaults: `tenant.uid` (synthetic), `tenant.auth_mode` (enum), `tenant.group_id` (synthetic short code from sprint 2.11), `tenant.uid_hash` (SHA256 of email, one-way irreversible). Raw email + display_name MUST NEVER land on a span — verified by 6 PII tests across M1 + M3. **AIPLA template-extension sequence (2.11–2.14) complete in a single day — four sprints shipped 2026-05-19 from one morning of feature-request triage.** The four sprints share the same tenant identity: 2.11 mints `group_id` → 2.12 keys budget on `identity_value` → 2.13 audit-correlates blocks by `group_id` → 2.14 stamps `tenant.group_id` on every span. A future Cloud Trace query `tenant.group_id = "PHYS-7K2N" AND span.name = "llm_call"` filters every LLM call from that cohort across all four mechanisms. | Concurrent-task isolation test in M1 + M2 (asyncio.gather + Event barriers, two tenants, zero cross-leakage); 6 PII tests across M1 + M3 (hash determinism, hash length, hash-not-raw-email, empty-email-no-uid_hash, display_name absence, scan-every-value); 5 integration tests in M2 (one per auth path + concurrent + direct dispatcher); golden SHA256 test pins the hash function. |
| 2026-05-19 | **Sprint 2.13 artefact-render-hook shipped end-to-end (~6 weeks ahead of AIPLA's early-July need-by).** Pluggable `ArtefactReviewer` Protocol consulted in `MCPAppToolCallRouter` (frontend) AND optionally in `mcp_proxy._forward` (backend). Frontend M1: `frontend/src/components/protocols/ArtefactReviewer.ts` (108 LOC, TS interface + ArtefactReview + ArtefactDecision discriminated union + registry + PermissiveArtefactReviewer shipped default) — 10 Vitest cases. M2: `MCPAppToolCallRouter.tsx` patch (118 LOC; consultArtefactReviewer with 500ms soft-budget via Promise.race + setTimeout; decision cached in component state) + `ArtefactRefused.tsx` (106 LOC; role='alert' + aria-live='assertive'; mount fires audit POST `/api/proxy/api/sessions/{id}/artefact-blocked`; idempotent via ref guard under React 18 Strict-Mode) + `ArtefactWarningStripe.tsx` (43 LOC; role='status' + aria-live='polite') — 17 Vitest cases covering all 4 paths (approve / warn / block / reviewer-crash + slow-reviewer degradation). Backend M3: `backend/protocols/artefact_review.py` (137 LOC, runtime_checkable typing.Protocol + frozen dataclasses + BlockedArtefactError + registry; registration enforces async via asyncio.iscoroutinefunction since @runtime_checkable only checks attribute presence) + `mcp_proxy._maybe_review_artefact` (~150 LOC; scope guards consult ONLY when reviewer registered AND method == resources/read AND response has text/html content; everything else passes through unchanged; reviewer crash + non-JSON body short-circuit to pass-through; soft 100ms warn log on duration; block returns 403 with `{type, message, reason_code, appeal_url}`) — 19 pytest cases including 6 back-compat regressions against existing mcp_proxy fixtures. M4: fork-adoption howto (`docs/integrations/artefact-review-hooks.md`) covers TS+Python interface mirror, registering a reviewer at bootstrap, AIPLA-style static-analysis sketch, client-vs-server trade-off, audit log shape, performance budget. **Backend tests: 1269 passed (was 1250, +19 new in M3). Frontend tests: 506 passed (was 489, +17 new across M1+M2). make lint + make test-fast + npm run quality:check all green at every milestone close.** Defence-in-depth posture confirmed: the hook lives ABOVE the existing iframe sandbox + CSP layer; a reviewer that crashes or is bypassed leaves the sandbox boundary intact. Fail-open is the chosen posture on both layers (reviewer crash → log + approve; slow reviewer → degrade to approve). The existing Cesium-map MCP-app demo renders unchanged under the permissive default. | Frontend: 4-path matrix in `MCPAppToolCallRouter.review.test.tsx` (approve / warn / block / reviewer-crash + slow-reviewer); backend: 8 mcp_proxy interception tests (6 back-compat regressions, 1 block→403, 1 approve forwards unchanged, 2 scope guards for non-resources/read + non-HTML mime, 1 fail-open on reviewer crash, 1 malformed body short-circuit). Both ArtefactRefused + ArtefactWarningStripe have aria-live attrs verified. |
| 2026-05-19 | **Sprint 2.12 budget-enforcement shipped end-to-end (~4 weeks ahead of the AIPLA mid-June pre-pilot).** Pluggable `BudgetEnforcer` Protocol consulted in ADK `before_model_callback`; forks plug their own backend (Firestore, BigQuery, Redis) without touching the platform. Closes the audit-table row flagged in the same week's AIPLA triage. M1: `backend/budget/enforcer.py` (146 LOC, `BudgetConsultation`/`BudgetDecision` frozen dataclasses, `BudgetEnforcer` runtime-checkable Protocol, `BudgetExceededError`, registry) + `in_memory_enforcer.py` (240 LOC, period-keyed dict with daily/weekly/monthly rollover, 60s replay dedup, asyncio lock, time_provider on the class) — 24 unit tests including all 8 gates (allow under cap / warn at 80% / block at 100% / period rollover / multi-identity isolation / replay dedup / record reconciliation / fail-loud-but-allow on unconfigured cap). M2: `backend/budget/callback.py` (214 LOC, `make_budget_callbacks` returns the before/after pair with closure-shared `pending` dict keyed by invocation_id; identity extraction reads any User field; cost projection reuses `observability.llm_metrics.estimate_cost` — single source of truth for projection AND realised cost) + `backend/adk/budget_config.py` (Pydantic model mirroring `A2uiToolConfig.from_tool_configs`) + `backend/adk/agent.py` introduces `_composed_before_model` + `_composed_after_model` mirroring the existing `_composed_before_agent` pattern (document_injector runs FIRST in the chain) — 23 integration tests. M3: `backend/skills/skill_processor.py` catches `BudgetExceededError` and emits AG-UI `RUN_ERROR{code:"BUDGET_EXCEEDED", message, retry_after_seconds}` (passthrough field on the RunErrorEvent schema) + `frontend/src/components/budget/BudgetBanner.tsx` (107 LOC, `role="alert"` + `aria-live="assertive"`, live retry-after countdown formatted days/hours/minutes/seconds, dismiss button) + `frontend/src/hooks/useSkillAgent.ts` extended StreamError union with `kind:"budget_exceeded"` + retryAfterSeconds field. **Backend tests: 1250 passed (was 1202, +48 new across M1+M2+M3). Frontend tests: 479 passed (was 465, +14 new in M3). make lint + make test-fast + npm run quality:check all green at every milestone close.** The warn surface (frontend banner reading `state["budget:warn_message"]`) is intentionally deferred — block was the AIPLA pre-pilot blocker; warn is a small follow-up that doesn't need its own sprint. | Backend: 8-gate matrix at the function layer + 1 SSE-level test (BudgetExceededError → typed RUN_ERROR with retry-after); frontend: 10 BudgetBanner tests + 3 useSkillAgent classifier tests; composition refactor regression test asserts both new composed callbacks are wired AND that document_injector still runs in the chain. |
| 2026-05-19 | **Sprint 2.11 anonymous-group-id-auth shipped end-to-end (8 days ahead of the AIPLA 27 May deadline).** Fourth auth mode: short-code session join, no PII, signed HS256 JWT. Closes the audit-table row flagged in the same day's AIPLA triage. M1: `backend/auth/group_id_auth.py` (318 LOC) + `group_rate_limit.py` (121 LOC) — 30 unit tests including all 7 gates (rate-limit, expiry, revoke, session-cap, malformed, unknown, happy). M2: 4 routes (create/join/delete/get) + token-shape dispatcher peeking at unverified `auth_mode` claim then routing to the right verifier (Sonar S5659 suppressed with inline justification) — 21 API tests. M3: frontend `AnonymousGroupAuthProvider` state machine + `/group` join page + `AuthContext` chooser branch + `fetchWithAuth` reads sessionStorage in group mode — 20 tests covering state transitions, sessionStorage persistence + stale-token rejection, typed error rendering, mode-gating fallback. M4: `dev-local.sh` exports a dev signing secret; fork adoption howto at `docs/integrations/anonymous-group-id-auth.md` documents Cloud Run rollout + secret rotation + multi-instance scale-out caveat. **Live E2E curl smoke verified:** teacher creates group SK6B-5J3D via Firebase auth → student joins with no auth → student calls `/api/skills` with the group JWT → backend's token-shape dispatcher accepts it → returns 5 skills. Frontend `/group` page mode-gate verified live (LOCAL_MODE shows the friendly "not available" fallback; tests prove the form path). | Curl smoke commands captured in `.dev-logs/sprint-2.11-smoke.txt`; mode-gate screenshot at `.dev-logs/anon-group-mode-gate.png`; tests: 1202 backend (+51 new across M1+M2) + 465 frontend (+20 new in M3). |
| 2026-05-19 | **AIPLA fork feature-request triage — four sprints scoped (2.11–2.14).** AIPLA's M-to-platform requests (anonymous group-ID auth, per-cohort budget enforcement, artefact content-review pipeline, per-cohort log attribution) all have a platform-level component. Triaged: 2.11 anonymous-group-id-auth (full feature, P1, AIPLA-blocker, 27 May deadline); 2.12 budget-enforcement (interface + ref impl in platform, policy in fork, P1); 2.13 artefact-render-hook (hook in platform, ruleset in fork, P2); 2.14 tenant-id-span-attribute (small contextvar + SpanProcessor, P2). Four new audit table rows added above. Continues the pattern from 2.6–2.10: fork requests surface template gaps; ship the seams upstream so the next fork doesn't reinvent. None executed yet — design docs only. | Triage doc walked through with the design-doc-creator + sprint-planner mental models; AIPLA's [feature-requests note 2026-05-18](https://www.sunholo.com/aipla/) is the source-of-truth document. |
| 2026-05-18 | **A2UI surface → agent context loop shipped end-to-end (sprint 2.10).** Closes the workspace → agent direction flagged on the discipline-section audit row earlier the same day. Wire: `SurfaceRegistry.readA2uiSurfaceState()` snapshots every live SurfaceModel's `dataModel.get('/')` at `useSkillAgent.sendMessage`; rides back on `forwardedProps.a2ui_surface_state`. User actions: `surface.onAction.subscribe` → `POST /api/sessions/{id}/surface-action` (7 gates mirror MCP Apps' `iframe-context`). Backend `wrap_with_a2ui_surface_context` InstructionProvider merges per-turn snapshot + persisted action writes under the `a2ui_surface_context.{surfaceId}` prompt namespace. Per-skill opt-in via `tool_configs.a2ui.allow_surface_context_writes: true`. **Four-turn live smoke (Gemini 2.5 Pro against demo-workspace skill):** turn 1 "show me the dashboard" → tool call → 42 users / $1,234; turn 2 "what's the current revenue?" → **0 tool calls**, agent answered "The current revenue is $1,234 in revenue."; turn 3 "refresh" → 1 tool call (updateDataModel only) → 87 users / $5,678; turn 4 "how many users are online now?" → **0 tool calls**, agent answered "There are 87 users online." Turn 4's answer reflects the post-refresh value, proving the snapshot is fresh on every turn (no caching). Code: backend 1139 tests + frontend 445 tests + lint + build green. | Live three-turn chat captured in `.dev-logs/dashboard-v2.10-context-loop.png`; backend log `grep -c "Validated call" = 2` (turns 1 + 3 only) |
| 2026-05-18 | **A2UI v0.8/v0.9 dialect mismatch resolved end-to-end.** Backend `BasicCatalog.get_config("0.9")` was validating against v0.9 from day one; frontend imported `@a2ui/react`'s default entry (v0.8) and used `A2UIViewer({root, components, data})`, a wrapper that synthesizes v0.8 `{beginRendering, surfaceUpdate, dataModelUpdate}` messages internally. Skill prompts taught the LLM a THIRD shape that wasn't valid in either version. The mismatch was invisible until the SDK's `render_as_llm_instructions()` injected the real v0.9 schema and the LLM emitted spec-compliant `{version, createSurface, updateComponents, updateDataModel}` — frontend wrapper's `isStaticSpec` returned false, dispatcher silently dropped the spec, workspace pane stayed empty. **Fix (commit `40fdc8e`):** rewrote `A2UIRenderer`/`SurfaceRegistry`/`A2UISurfaceMount`/`MessageBubble` onto `@a2ui/react/v0_9` + `@a2ui/web_core/v0_9` (`MessageProcessor` + `<A2uiSurface surface>`) with one MessageProcessor per surface, auto-`createSurface` for LLM message-ordering drift, idempotent on tool-call id. Then a follow-up (commit `fb0c78e`): `useSkillAgent.toSkillMessage` was dropping tool-only assistant turns (`content: undefined`), so the bubble that hosted the dispatcher never rendered. Treat `content: undefined` as `""` for assistant messages. **Lesson driving the new "Discipline" section above:** the protocol-native axiom requires consuming the wire format directly. The 2026-04-19 row noted A2UI v0.9 had landed; we left `@a2ui/react` on the default v0.8 wrapper anyway. Verified end-to-end in Chrome via chrome-devtools MCP — three round-trips: createSurface + updateComponents + updateDataModel render the dashboard; "refresh" mutates the SurfaceModel's dataModel in place (same model reference, no remount). | Live three-turn chat against demo-workspace skill in `make dev-local`; backend `Validated call send_a2ui_json_to_client` 3× in `.dev-logs/backend.log`; screenshots `.dev-logs/dashboard-v09-working.png` + `.dev-logs/dashboard-v09-refreshed.png` |
| 2026-05-01 | **CopilotKit OpenGenerativeUI framework assessed.** Their three-pattern taxonomy (Static / Declarative / Open) is good audience framing — adopted as Layer 4 intro above. The framework itself (CopilotKit v2 + LangChain Deep Agents + `useComponent` hook + an `assemble_document` tool that wraps HTML fragments with design-system CSS + bridge JS) is the "Open" pattern with their wrapper. v6 reaches the same ceiling via canonical `@mcp-ui/client.AppRenderer` atop AG-UI; adopting their framework would require swapping ADK → LangChain Deep Agents, `@ag-ui/client` → CopilotKit React runtime, and `@mcp-ui/client` → their `MCPAppsMiddleware` (less spec-compliant). One inspiration on watchlist only: the `assemble_document` tool pattern as a v6 backend tool for skill authors who want a one-off widget without standing up a separate MCP server. Don't pre-build — wait for the friction to surface. | WebFetch on [CopilotKit/OpenGenerativeUI](https://github.com/CopilotKit/OpenGenerativeUI), [CopilotKit/generative-ui](https://github.com/CopilotKit/generative-ui), [docs.copilotkit.ai/llms.txt](https://docs.copilotkit.ai/llms.txt) |
| 2026-06-08 | **Action-triggered A2UI run shipped (sprint ACTION-TRIGGER).** New `POST /api/skills/{skill}/sessions/{id}/surface-action-run` endpoint bundles the existing A2UI surface-action state write together with a synthetic `RunAgentInput(messages=[], state={a2ui_action_trigger}, forwardedProps={a2ui_surface_state, _action_trigger})` and the existing `build_agui_adk_agent` + `stream_agui_events` pipeline — closes the Pattern 1 loop ("non-chat AI UI driven entirely by surface clicks") that the original `surface-action` endpoint left half-open (the action persisted but waited for the next chat message to trigger a turn). Gates 1–7 reused from `surface-action` via the new `_a2ui_surface_shared` module; gate 8 is a new per-skill opt-in `tool_configs.a2ui.allow_action_triggered_runs` (deny-by-default, separate trust grant from `allow_surface_context_writes`). Five-run smoke contract against the LOCAL_MODE `demo-click-counter` skill: each iteration emits `RUN_STARTED` → `TOOL_CALL_START` (`toolCallName=send_a2ui_json_to_client`) → `TOOL_CALL_ARGS` deltas referencing `surfaceId="counter-main"` and an incrementing counter → `RUN_FINISHED`; counter goes 1→5 across iterations on a shared session. Code: backend M1 (commit `d157ee5`, +22 tests = 1449 total), frontend M2 (commit `39d8563`, +16 tests = 590 total), M3.1 demo skill + M3.2 `aiplatform sessions trigger-action` + M3.3 `scripts/smoke-pattern1.sh` parallel-shipped. Filed upstream: [a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) (transport spec gap for embedded surfaces), [ag-ui#1892](https://github.com/ag-ui-protocol/ag-ui/issues/1892) (duplicate terminal event G41 dedup), [ag-ui#1893](https://github.com/ag-ui-protocol/ag-ui/discussions/1893) (`body.state` one-turn-behind semantics). | Backend test suite (5 gate tests + happy-path SSE test + G41 dedup test in `test_a2ui_surface_action_run_routes.py`); frontend hook tests in `useActionDrivenAgent.test.tsx` covering POST + SSE consumption + `RUN_FINISHED`/`RUN_ERROR` resolution; `scripts/smoke-pattern1.sh` lands the repeatable 5/5 contract for `LOCAL_MODE=1 make dev`. End-to-end smoke not yet run live — pending M3.1 (demo skill seed) and M3.2 (CLI subcommand) parallel agents completing; the script's preconditions fail cleanly when either is missing (exit 2). |
| 2026-07-08 | **ChatGPT ignores the SEP-1865 `ui/*` postMessage bridge — it uses `window.openai`.** A widget that only posts `ui/update-model-context` / `ui/message` to `window.parent` (traps #7–9 in `protocol-gotchas.md`) renders in ChatGPT dev mode but the model stays blind to every interaction — a deployed sim widget hit exactly this (displayed fine, broadcast nothing). ChatGPT's OpenAI Apps SDK injects a `window.openai` object instead; **M365 Copilot honours BOTH** bridges (`window.openai` + postMessage); Claude Desktop / MCP Inspector are postMessage-only. Fix = **dual-bridge widget**: feature-detect `window.openai` at call time, else fall back to `ui/*`, guarding every `window.openai.*` call (unsupported APIs are `undefined`). Channel map — push state: `setWidgetState` vs `ui/update-model-context`; chat turn: `sendFollowUpMessage` vs `ui/message`; **mutation round-trip: `window.openai.callTool` (tool needs `_meta["openai/widgetAccessible"]`) vs `app.callServerTool`**. Tool→UI metadata splits too: `_meta["openai/outputTemplate"]` (ChatGPT) vs `_meta.ui.resourceUri` (standard) — set **both**. `callTool` confirmed as the plain method — **no `mcp.*` namespace**. Distilled as new gotcha #12 in `docs/workshop/protocol-gotchas.md`. | Live in ChatGPT dev mode against a local MCP server exposed via a `cloudflared` quick tunnel: `show-demo` widget rendered; its **+1** button called the `increment-counter` data tool; the server-held counter incremented and BOTH the widget readout AND the model reported the new value on the next turn. Also confirmed two host sub-traps: the `*.trycloudflare.com` URL is ephemeral (dead after a multi-day laptop sleep → re-import), and hosts cache `tools/list` at connect (a newly added tool is invisible until the connector is refreshed). |

**Add to this table** every time we run a real spike, find a real gotcha, or hit a version bump. The talk's credibility comes from this column.

---

## Demo plan (using v6 as the worked example)

The talk should culminate in a live demo or recorded walkthrough showing:

1. **A user uploads a `.docx`** — shows the file going from binary blob to parsed `Block` ADT (ailang-parse) to A2UI JSON in <1s.
2. **The document renders** — A2UI components in the left panel, with editable fields wired through `useA2UIComponent`.
3. **User asks a question** — chat panel emits AG-UI `RUN_STARTED` → `TEXT_MESSAGE_*` → `TOOL_CALL_*` (the agent calls `parse_document` against the uploaded doc) → `RUN_FINISHED`.
4. **User edits a heading inline** — the A2UI `setValue` flows back through AG-UI as a custom event, the agent's session state updates, the next message references the edited value.
5. **User asks for an output document** — agent calls `generate_document` tool, AG-UI `TOOL_CALL_RESULT` carries a download link.

**Workshop variant:** participants clone the public **`platform-template`** repo (forked from `sunholo-data/ai-protocol-platform` in mid-to-late May 2026, see [template-split-strategy.md](../design/v6.0.0/template-split-strategy.md)), walk through Phase 0.4 spike themselves (`uv add ag-ui-adk`, write 50 lines, curl the endpoint, see SSE), then add a single FunctionTool of their own and watch it surface as `TOOL_CALL_*` in CopilotKit.

### Workshop repo — the public template

The workshop doesn't share `sunholo-data/ai-protocol-platform` directly. That private repo forks into a public `platform-template` once 1A.5 streaming + 1A.6 chat-history + 1B.1 frontend-architecture land — target mid-to-late May 2026, giving ~6 weeks between fork and workshop for a dry-run clone-and-deploy, README polish, and any bugs surfaced by internal testers.

**What the public template contains:**
- Full protocol stack (AG-UI + A2UI + MCP Apps + A2A), wired end-to-end
- Firebase auth, skills CRUD, agent factory (Gemini/Claude/OpenAI), observability-to-Cloud
- Aitana branding (workshop narrative uses it as the worked example; attendees swap for their own)
- CI/CD patterns, smoke-probe tooling, `docs/gotchas/` with every trap we hit during v6 bring-up

**What it explicitly does not contain:**
- Aitana's real platform skill YAMLs (template ships a `hello-skill` stub instead)
- Terraform (stays private — workshop README points at public Google terraform modules)
- Ops runbooks with live URLs, credentials, incident logs
- `.claude/state/` sprint/memory artifacts

This is the shape because the hardest-won repo content is the *patterns* — env promotion, Firebase traps, sidecar port gotchas, env-var shadows — and those travel as prose. Aitana's IaC doesn't need to be public for workshop attendees to understand the wiring.

---

## Open questions for the talk

These are the questions I'd want answers to *before* presenting, because someone in the audience will ask:

- Latency floor with AG-UI: what's the practical first-event time? (Roadmap target: <300ms.)
- Token-cost overhead of A2UI vs plain markdown: how much extra context does a typical A2UI document add?
- MCP Apps vs Streamlit/Gradio/Plotly Dash: is there an ecosystem story or is it a green field? (Partially answered: see Layer 4b provider catalog — there's a real ecosystem now, ~15+ working examples across MCP org, Microsoft, and community.)
- Where does the protocol stack break down? I want one slide of honest "here's where we still write custom code."

---

## TODO — sections still to write

- [ ] Per-protocol "what changed in 2026" mini-history (the protocols all moved fast in late 2025, would help the audience understand why now)
- [ ] Concrete code-size comparison: lines of custom code v5 → v6 for each layer (the "you can delete this much code" slide)
- [ ] Error / observability story: how OpenTelemetry traces compose with AG-UI events
- [ ] Multi-agent: how A2A + AG-UI compose when agents talk to other agents
- [ ] A "stack adoption guide" — if a team has *one* afternoon, what do they adopt first? (Probably AG-UI, because it's the highest-impact for the lowest install cost.)
