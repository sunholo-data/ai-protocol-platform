# MCP App Integrations (geo first, pattern for the rest)

**Status**: Planned (re-scoped 2026-04-30 after spec audit)
**Priority**: P1 — workshop W7 critical path; July 2026 demo MUST run through the actual MCP Apps wire format
**Estimated**: ~4.0 days total — Phase 1 spike (0.5d) ✅ shipped 2026-04-30 (commit 05bcd5f) + Phase 2 frontend MCP Client + passive routing + active iframe→host bridge + backend MCP proxy + UI capability declaration (2.0d) + Phase 3 dev demo page + observability + e2e smoke (0.5d) + Phase 4 sidecar Cloud Run deployment across three repos (1.0d). Path A (frontend MCP Client via backend proxy) chosen 2026-04-30 — adds ~0.75d above the minimum-scope Path B because of the new proxy endpoint + per-skill allowlist enforcement, but ships the canonical spec surface (template-grade) without future retrofit.
**Scope**: Fullstack + infra (backend `McpToolset` registration with UI capability declaration + frontend `@mcp-ui/client` adoption replacing custom `MCPAppFrame` + Cloud Run sidecar service for the MCP server itself)
**Dependencies**:
- v6.0.0 backend + frontend running (✅)
- [streaming-and-protocols.md](streaming-and-protocols.md) — AG-UI tool-call event flow (the existing `TOOL_CALL_RESULT.content` channel is the carrier; no new event type)
- [tools-porting-guide.md](tools-porting-guide.md) — `McpToolset` registration pattern proven via 1A.3
- platform-deploy skill (the deploy skill) — three-repo deploy flow for the sidecar
**Created**: 2026-04-11
**Last Updated**: 2026-04-30

## 2026-04-30 architecture decision — Path A (frontend MCP Client via backend proxy)

After M1 spike (sprint 1.7 commit 05bcd5f) surfaced that the spec is **UI-by-reference** (tool definition declares `_meta.ui.resourceUri`; host fetches HTML via `resources/read`), three paths emerged for how the frontend should obtain UI resources:

- **Path A (chosen):** Backend exposes `/api/proxy/mcp/{server_id}` that forwards MCP JSON-RPC to the registered server. Frontend instantiates an `@modelcontextprotocol/sdk/client/streamableHttp.Client` against the proxy and passes it to `<AppRenderer client={...} toolName={...} />`. AppRenderer fetches resources autonomously and forwards iframe-initiated `tools/call` requests back to the server through the same client.
- **Path B (rejected):** Backend pre-fetches HTML in an `after_tool_callback` and attaches it to the AG-UI `TOOL_CALL_RESULT` event; frontend uses `<AppRenderer html={...} />` (no Client). Workshop demo lands but iframe-initiated MCP calls don't work.
- **Path C (rejected):** Skip `<AppRenderer>` entirely; use `<AppFrame>` with manually fetched HTML and a hand-wired `AppBridge`. Smallest scope, least spec coverage.

**Why A:** v6 is the foundation for a public template (mid-to-late May 2026 fork per `project_template_split` memory) and likely several downstream Aitana Labs projects. Templates should ship with the canonical pattern, not the minimal one. The +0.5–0.75d cost-now is rounding error against the multi-year lifetime of the template; retrofitting from B → A across N forks later is strictly more expensive. Workshop demo also gains a credibility moment: iframe AND agent both invoke MCP through the same protocol surface, with no special wiring on either side.

**What Path A unlocks for the workshop:**
1. Click a map pin → `onMessage` notification → host translates → synthetic chat message (the active integration we already had)
2. Iframe asks the MCP server for more data directly (`onCallTool` auto-forwards through the client) — e.g. iframe says "geocode Lyon" while the user is panning, gets the result back, updates the globe
3. Both flows use the same MCP wire surface — "the iframe and the agent are peers under the protocol"

**Cost incurred:**
- New backend route `backend/protocols/mcp_proxy.py` (~80 LOC) — JSON-RPC proxy for `/api/proxy/mcp/{server_id}`
- Per-skill allowlist enforcement on the proxy (~40 LOC) — auth dependency + check that the calling user has access to a skill that allowlists `{server_id}`
- Frontend MCP Client wiring (~50 LOC) — `@modelcontextprotocol/sdk/client/streamableHttp` Client instance per active server, threaded via context, Firebase token attached via `fetchWithAuth` pattern
- Tests (~250 LOC across both layers) covering the negative case (logged-in user with no skill access cannot proxy)

**Sprint estimate impact:** 3.25d → ~4.0d (parallel) / ~4.5d (sequential).

## 2026-04-30 audit — placeholder vs spec gap

Audit of the current code (after stranded-session-prevention 1.23 shipped) revealed that the v6 MCP Apps surface is a **non-spec placeholder**, not a working integration:

| Layer | Today | Spec | Gap |
|---|---|---|---|
| Frontend renderer | `frontend/src/components/protocols/MCPAppFrame.tsx` — custom iframe with placeholder text "(payload not yet wired)" | `@mcp-ui/client@7.0.0` `AppRenderer` / `AppFrame` (already installed, unused) | Adopt `@mcp-ui/client`, delete custom shim |
| Result detection | `extractMCPAppURIs(text)` regex over agent prose — finds `ui://...` strings, ignores resource MIME entirely | `isUIResource(content)` type guard on `CallToolResult` content; resource has `mimeType: "text/html;profile=mcp-app"` | Replace regex with spec detector |
| HTML payload extraction | None — `MCPAppFrame` always renders the placeholder | Resource HTML in `content[i].resource.text` per MCP spec | Bridge `tc.resultContent` JSON → `isUIResource` → `AppRenderer` |
| Backend capability | `McpToolset` instances built without UI extension declaration | Client should declare `UI_EXTENSION_CAPABILITIES` (`io.modelcontextprotocol/ui` mimeTypes `["text/html;profile=mcp-app"]`) per SEP-1724 | Pass capabilities through ADK's `McpToolset` connection params |
| postMessage events | None | `AppBridge` + `PostMessageTransport` for bidirectional iframe ↔ host events | Defer (no current skill needs iframe → host events; document the deferral) |
| Skill allowlist | `tool_configs` per-skill — implicit allowlist via which skills get which configs | Same | Already correct; just needs `mcp_servers` Firestore docs seeded |
| Sidecar deploy | Not started | n/a (infra) | Three-repo deploy per `platform-deploy` skill |

The 2026-04-13 plan in this doc assumed CopilotKit's `@ag-ui/mcp-apps-middleware` would do the routing for free. **v6 does not use CopilotKit** — frontend uses `@ag-ui/client` + `@ag-ui/core` directly (per `gotcha_copilotkit_not_agui_native` memory). That entire path is moot. The replacement strategy is to adopt `@mcp-ui/client` directly, which provides the same protocol surface without the CopilotKit runtime.

**Workshop implication:** the W7 demo (currently planned for July 2026) cannot run through the placeholder. Spec compliance is the sprint goal, not a nice-to-have.

## Problem Statement

Aitana v6 chat surfaces are text-first. When the agent needs to communicate something inherently spatial, structural, or interactive — a location on a map, a 3D molecule, an interactive chart — the only available channels are:
1. Markdown text (lossy: "the offices are in Munich, São Paulo, and Singapore")
2. Inline images (static, no interaction)
3. A2UI declarative components (great for forms/tables, but can't express "render a Cesium globe")

A2UI handles 80% of structured UI but has a fundamental ceiling: it's a declarative component vocabulary, not a sandbox for arbitrary code. The remaining 20% — anything that needs a third-party rendering library, GPU, custom canvas/WebGL, or a non-React widget — has no clean home in v6 today.

**Current State:**
- Document workspace renders parsed `Block` ADT via `@copilotkit/a2ui-renderer` — covers headings, paragraphs, tables, callouts
- Chat panel renders streamed text + tool-call cards via `<CopilotSidebar>`
- No path for an agent tool result to deliver a sandboxed interactive widget
- The Q1 financial summary in the mockup has "EMEA / APAC / Americas" rows — currently a flat table; no spatial context

**Concrete first use case (geo):** Aitana parses a document mentioning regions, addresses, or coordinates → the agent should be able to surface those on an interactive map without us *building* a Cesium + tile provider + geocoding stack from scratch. We're happy to *operate* an open-source MCP server that does this; we just don't want to write or maintain the rendering code.

**Important framing — "third-party" vs "self-operated":** For this doc, "third-party MCP server" means *code we didn't write*, not *infrastructure we don't run*. The reality of the MCP App ecosystem in 2026-04 is that most reference servers (`modelcontextprotocol/ext-apps`, `microsoft/mcp-interactiveUI-samples`) are open-source examples meant to be cloned and run, not hosted SaaS endpoints. The protocol value is that **we can drop in any conformant MCP server, regardless of who ships and operates it** — and self-operating is actually a stronger story than depending on a third-party uptime SLA. Aitana will host `ext-apps/map-server` as a Cloud Run sidecar, not point at someone else's hosted endpoint.

**Impact:**
- Affects: every skill that produces inherently visual/spatial output (Doc Analyst, Web Researcher, future Data Extractor)
- Significance: medium — not a blocker for the bring-up demo, but a credibility ceiling for the talk and for any document workflow involving geographical data
- Strategic: validates the *transport-decoupled rendering* insight from [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — that MCP Apps' value is the resource format + postMessage contract, not the transport. Without this doc the insight is theory; with it, it's a shipping pattern.

## Goals

**Primary Goal:** Establish the integration pattern for open-source MCP servers that ship UI resources (MCP Apps), validated by adopting `modelcontextprotocol/ext-apps/map-server` as the first concrete integration — deployed as a sidecar Cloud Run service alongside `platform-backend` — so that Aitana skills can surface geographical data as an interactive 3D globe without owning the rendering code.

**Success Metrics:**
- Time-to-first-MCP-App-render in chat: <2s after the agent decides to call the tool
- Zero new rendering dependencies pulled into the v6 frontend bundle (Cesium etc. live inside the sandbox iframe served by `map-server`)
- Adding a *second* MCP App provider from the same `ext-apps` repo takes <1 hour (purely registry edit + redeploy of the existing sidecar image, no new Cloud Run service)
- Adding a *new third-party* MCP App provider (e.g., a Microsoft sample) takes <half a day (new sidecar Cloud Run service + registry edit)
- Demo: in the talk, show Aitana parsing a financial doc → extracting "EMEA / APAC / Americas" → calling `show_locations` → globe renders inline with the three regions highlighted

**Non-Goals:**
- Building our own MCP App server with our own widgets (that's a separate doc — the *path B* / FunctionTool-returning-UI-resource pattern; this doc is exclusively path A: third-party MCP servers)
- Building an MCP App marketplace or discovery UI inside Aitana
- Sandboxing arbitrary user-supplied HTML (security model assumes we trust the MCP server registry)
- Replacing A2UI for any case A2UI handles well — MCP Apps is the escape hatch, not the default
- Auth/permission model for MCP servers (covered in [auth-and-permissions.md](auth-and-permissions.md) once this is wired)

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Sandboxed iframe renders synchronously once the tool result arrives; no extra client round-trips. The agent's `TOOL_CALL_RESULT` event already arrives via AG-UI streaming, so the widget appears as soon as the call completes. |
| 2 | EARNED TRUST | +1 | Every widget cites the MCP server that produced it (visible in the iframe header). Sandboxed iframe means the server can't tamper with surrounding chat content — visual provenance is structurally enforced. |
| 3 | SKILLS, NOT FEATURES | +1 | MCP servers are registered per-skill (the Doc Analyst skill gets the map server, not a global toggle). Skills remain the user-facing primitive; MCP Apps are an implementation detail of what a skill can render. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Doesn't change model selection. |
| 5 | GRACEFUL DEGRADATION | +1 | If the MCP server is down or returns a malformed UI resource, `MCPAppToolCallRouter` returns null (so `MessageBubble`'s existing routing handles the tool call as a normal `ToolCallChip`). Chat keeps working; only the visual flourish is lost. |
| 6 | PROTOCOL OVER CUSTOM | +2 | This is the axiom in action — and it's the reason the doc was re-scoped on 2026-04-30. Original plan was on track to ship a custom `MCPAppFrame` + `extractMCPAppURIs` regex (a placeholder masquerading as integration) alongside an unused `@mcp-ui/client@7.0.0` install. Re-scoped to delete the custom shim and adopt `@mcp-ui/client` directly + declare `UI_EXTENSION_CAPABILITIES` (SEP-1724). Zero new file formats, zero new transport, zero custom UI plumbing. The "geo support" outcome is achieved entirely by composition with two off-the-shelf packages (`@mcp-ui/client` + ADK `McpToolset`). |
| 7 | API FIRST | 0 | No new public Aitana API surface; this is internal tool composition. |
| 8 | OBSERVABLE BY DEFAULT | +1 | MCP tool calls flow through ADK's existing tool callbacks → OpenTelemetry → Cloud Trace. We get latency, error rate, and call counts per MCP server for free. Add one custom span attribute (`mcp_app.has_ui_resource=true`) to distinguish UI-bearing calls. |
| 9 | SECURE BY CONSTRUCTION | +1 | Sandbox model is enforced by `@mcp-ui/client.AppFrame` per spec defaults (`allow-scripts allow-same-origin allow-forms`), not improvised by us. MCP server registry is allowlisted via Firestore `mcp_servers/{id}` + per-skill `tool_configs.mcp.servers` — users can't register arbitrary servers in v1. Backend declares `UI_EXTENSION_CAPABILITIES` only for servers we've allowlisted, so a skill never accidentally accepts UI resources from an un-vetted server. |
| 10 | THIN CLIENT, FAT PROTOCOL | +2 | The frontend learns *zero* domain knowledge about maps, globes, or geocoding. All of that lives behind the MCP boundary, in a sandboxed iframe served by an external process. The Aitana client stays thin; the protocol layer carries everything. |
| | **Net Score** | **+10** | Strong alignment — proceed |

**Conflict Justifications:** None — no axioms scored -1.

**Standards compliance check (refreshed 2026-04-30):** Adopting MCP (modelcontextprotocol.io) + MCP Apps spec + SEP-1724 capability extensions + ADK `McpToolset` + `@mcp-ui/client@7.0.0` (`AppRenderer`, `AppFrame`, `isUIResource`, `UI_EXTENSION_CAPABILITIES`). No custom formats invented. The project-specific artifacts are (a) the Firestore `mcp_servers/{id}` collection — config layer over standard MCP, not a new protocol — and (b) per-skill activation via `SkillConfig.tool_configs.mcp.servers`. See the **Spec Compliance** section for the explicit adopt/diverge/defer table.

## Design

### Overview

Two slim composition points: (1) `backend/tools/mcp/registry.py` already builds `McpToolset` instances per skill — extend it to declare the spec UI extension capability so servers know to advertise UI resources; (2) on the frontend, a new `MCPAppToolCallRouter` inspects each `TOOL_CALL_RESULT.content`, runs `isUIResource` from `@mcp-ui/client`, and mounts `<AppRenderer>` for matches. Active iframe → host integration is wired through `AppBridge`'s default transport plus a small notification adapter (per the 2026-04-30 decision). The first integration target is `modelcontextprotocol/ext-apps/map-server` for geographical visualisation, validating the pattern.

### Backend Changes

**New module:**
- `backend/adk/mcp_app_registry.py` (~120 LOC) — loads `backend/config/mcp_servers.yaml`, exposes `get_servers_for_skill(skill_id) -> list[McpServerConfig]`. The registry is the allowlist that prevents skills from invoking unapproved MCP servers.
- `backend/config/mcp_servers.yaml` (~40 LOC) — declarative registry of available MCP servers and which skills can use them. URLs are env-templated so the same registry works across dev/test/prod. Example:
  ```yaml
  servers:
    - id: ext-apps-map
      name: "Geo / 3D Globe"
      transport: streamable_http
      url: ${MCP_EXT_APPS_MAP_URL}   # dev: http://localhost:3001/mcp
                                     # test/prod: https://mcp-ext-apps-map-{env}-{hash}.run.app/mcp
      tags: [geo, visualization]
      tool_filter: [show_locations, show_route]
      allowed_skills: [doc-analyst, web-researcher]
      source_repo: https://github.com/modelcontextprotocol/ext-apps
      source_path: examples/map-server
      operated_by: aitana  # we run this ourselves as a Cloud Run sidecar
  ```

**New module (Path A — added 2026-04-30):**
- `backend/protocols/mcp_proxy.py` (~80 LOC) — FastAPI router exposing `POST /api/proxy/mcp/{server_id}` and `GET /api/proxy/mcp/{server_id}/sse` (or whatever StreamableHTTP needs). Forwards JSON-RPC requests to the MCP server URL from `mcp_servers/{server_id}` Firestore doc; returns the response stream verbatim. Uses ADK's `McpToolset.connection_params` plumbing under the hood (or instantiates `streamablehttp_client` directly with the server URL). Auth: depends on Firebase auth (existing `current_user` dependency) AND a per-skill allowlist check — the route must verify the calling user has access to at least one skill that includes `{server_id}` in its `tool_configs.mcp.servers` list. Otherwise rejects with 403. The check is the security boundary that prevents arbitrary logged-in users from invoking arbitrary MCP servers via the proxy.

**Modified modules (revised 2026-04-30):**
- `backend/tools/mcp/registry.py` (already exists) — extend `_build_toolset` to declare the UI extension capability per SEP-1724 so MCP servers advertise UI resources back. Add to the connection params:
  ```python
  client_info={"name": "platform", "version": "..."}
  capabilities={
      "extensions": {
          "io.modelcontextprotocol/ui": {
              "mimeTypes": ["text/html;profile=mcp-app"]
          }
      }
  }
  ```
  These map to ADK's `McpToolset` underlying MCP `Client` ctor. Without this, a spec-compliant server will not include UI resources in its `tools/call` response. Verify ADK's `StreamableHTTPConnectionParams` plumbs these through; if not, file an ADK-side gap and use `header_provider` as a temporary signal.
- `backend/adk/tools.py` — already wires `get_mcp_tools(server_ids)` per skill `tool_configs` (line 181). No change needed beyond the registry extension above.
- `backend/observability` — add a `before_tool_callback` that tags spans with `mcp_app.server_id`; add an `after_tool_callback` that tags `mcp_app.has_ui_resource=true` when the result contains a `text/html;profile=mcp-app` resource. Tags flow to Cloud Trace via the existing OTel pipeline.

**No new endpoints.** The whole integration is internal to the ADK agent loop. The chat endpoint built in M1A.5 already streams `TOOL_CALL_RESULT` events; MCP App tool results ride that exact channel without changes.

**Verified ADK API (against `google/adk-python@v1.24.1` source via `adk-mcp` MCP server, 2026-04-11):**

```python
# backend/adk/mcp_app_registry.py — usage shape
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

def build_toolset_for(server_config: McpServerConfig) -> McpToolset:
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=server_config.url,
            timeout=server_config.timeout_s or 30,
        ),
        tool_filter=server_config.tool_filter,        # list[str] or None
        tool_name_prefix=server_config.id,            # avoids name collisions across servers
        header_provider=lambda ctx: {                 # injects Aitana auth context
            "x-aitana-skill-id": ctx.session.state.get("skill_id", ""),
            "x-aitana-user-id": ctx.session.user_id,
        },
        require_confirmation=server_config.require_confirmation or False,
    )
```

Notes from the verified source:
- `McpToolset` (note casing — `MCPToolset` is the deprecated alias) lives at `google.adk.tools.mcp_tool.mcp_toolset`.
- `connection_params` accepts `StdioServerParameters | StdioConnectionParams | SseConnectionParams | StreamableHTTPConnectionParams`. For HTTP-served third-party servers (the ext-apps map server, future hosted services) use `StreamableHTTPConnectionParams`.
- `header_provider` receives a `ReadonlyContext` and returns a `dict[str, str]`. This is how we inject Aitana's user/skill context into outbound MCP calls without leaking the user's Firebase token.
- `tool_name_prefix` namespaces tools across multiple MCP servers — important once a skill has more than one server registered (e.g., map + sheet music + shadertoy).
- The toolset has `from_config(config: ToolArgsConfig, ...)` for loading from ADK YAML — we may use this in `mcp_servers.yaml` if we move to ADK's native tool config format later.

### Frontend Changes (revised 2026-04-30 — adopt `@mcp-ui/client`, delete custom shim)

**Renderer decision:** v6 had been carrying a custom `MCPAppFrame.tsx` (sandboxed iframe with placeholder rendering) alongside an unused `@mcp-ui/client@7.0.0` install. Per axiom #6 (PROTOCOL OVER CUSTOM) and the workshop spec-compliance requirement, the custom shim is deleted and `@mcp-ui/client` is adopted as the canonical renderer. Rationale:
- `@mcp-ui/client.AppFrame` already provides our exact sandbox posture plus spec-mandated `AppBridge` postMessage transport (which `MCPAppFrame` lacked entirely)
- `@mcp-ui/client.isUIResource` is the spec-correct detector (vs our regex over text)
- `@mcp-ui/client.AppRenderer` is the high-level integration that takes `CallToolResult` content and handles detection + rendering + postMessage in one component
- The package is already in `package.json` from an earlier exploration; no new dependency lands
- Pulls in `@modelcontextprotocol/sdk@^1.27.1` and `@modelcontextprotocol/ext-apps@^1.2.0` as transitive deps — both spec implementations, not new alternatives we're inventing

**New components (revised 2026-04-30 for Path A):**

After M1 captured fixtures showed real MCP servers ship UI by REFERENCE (tool definition's `_meta.ui.resourceUri`, not embedded), the router needs more than just `tc.resultContent`. Per the Path A architecture decision, the frontend gets its own MCP `Client` so `<AppRenderer>` can handle the full spec lifecycle.

- `frontend/src/lib/mcpClient.ts` (~50 LOC) — instantiates `Client` from `@modelcontextprotocol/sdk/client/index.js` connected via `StreamableHTTPClientTransport` from `@modelcontextprotocol/sdk/client/streamableHttp.js` pointing at `/api/proxy/mcp/{server_id}`. Uses `fetchWithAuth` shape so the Firebase token rides on the Authorization header (the proxy verifies it). Per-server lazy instantiation + caching so we don't reconnect on every render. Exposes `useMcpClient(serverId): Client | null` hook for components.
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` (~100 LOC) — receives `toolCalls: ToolCall[]` from MessageBubble. For each tool call:
  1. Look up the tool DEFINITION (cached from a prior `tools/list` call against the proxy at app boot, OR fetched on first sight) to detect `_meta.ui.resourceUri`
  2. If the tool has a UI binding: mount `<AppRenderer client={mcpClient} toolName={tc.toolCallName} toolInput={tc.argsJson} toolResult={parsedResultContent} onMessage={...} />`. AppRenderer fetches the resource HTML itself via `client.readResource(uri)` and handles iframe lifecycle.
  3. If no UI binding: return null so `MessageBubble`'s existing routing (A2UI / `ToolCallChip`) takes over
- `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.test.tsx` (~100 LOC) — fixture-driven, using the M1 captured artifacts (`map-server-tools-list.json` for the tool definition, `map-server-show-map-result.json` for the result, mock client returning `map-server-ui-resource.json` for `readResource`). Asserts AppRenderer mounted with the right props; non-UI tool returns null; client unavailable returns null gracefully.

**Modified components:**
- `frontend/src/components/chat/MessageBubble.tsx` — replace lines 63 (`extractMCPAppURIs`) and 85-87 (raw `MCPAppFrame` mapping) with a single `<MCPAppToolCallRouter toolCalls={mcpAppCandidates} />` block. The candidates filter is `tc => tc.resultContent && (looks-like-mcp-app)`.

**Deleted components:**
- `frontend/src/components/protocols/MCPAppFrame.tsx` — replaced wholesale by `@mcp-ui/client.AppFrame` (used internally by `AppRenderer`)
- `frontend/src/components/protocols/__tests__/MCPAppFrame.test.tsx` — its assertions transfer to the new router test
- `extractMCPAppURIs` regex helper in `MCPAppFrame.tsx` — the v6-only `ui://` text-scan shortcut. The Path B FunctionTool pattern (an Aitana-owned tool returning a `ui://` reference) will need to be re-implemented as a tool that returns a proper `CallToolResult` with an `EmbeddedResource`, which then flows through the same `MCPAppToolCallRouter`. This is the spec-correct unification of Path A and Path B.

**Path A vs Path B unification (was an asymmetry, now isn't):**
- **Path A (third-party MCP server):** `tools/call` returns `CallToolResult` with `content[i] = { type: "resource", resource: { mimeType: "text/html;profile=mcp-app", text: "<html>..." } }`. ADK delivers this to AG-UI as a `TOOL_CALL_RESULT` event; frontend router detects via `isUIResource`.
- **Path B (Aitana-owned FunctionTool):** the tool's return value goes through the same ADK serialisation. As long as the tool returns the same `CallToolResult` shape — or the ADK adapter wraps a return-value into one — the same router handles it. **No special-case code path.**

**Verified npm packages (2026-04-30 audit):**
- `@mcp-ui/client@7.0.0` — installed, currently unused; this sprint wires it
- Exports verified by reading `node_modules/@mcp-ui/client/dist/src/index.d.ts`: `AppRenderer`, `AppFrame`, `isUIResource`, `getUIResourceMetadata`, `getResourceMetadata`, `AppBridge`, `PostMessageTransport`, `UI_EXTENSION_CAPABILITIES`, `UI_EXTENSION_CONFIG` (`{mimeTypes: ["text/html;profile=mcp-app"]}`), `UI_EXTENSION_NAME` (`"io.modelcontextprotocol/ui"`)
- Transitive: `@modelcontextprotocol/sdk@^1.27.1`, `@modelcontextprotocol/ext-apps@^1.2.0` — already on disk via `@mcp-ui/client`'s install

**Spec compliance — what we adopt verbatim:**
- Resource MIME type `text/html;profile=mcp-app` (no `text/html+mcp` or other variants)
- `CallToolResult.content[].resource.text` as the canonical HTML payload location
- `isUIResource()` for detection (no homegrown MIME parsing)
- `AppFrame` sandbox defaults (`allow-scripts allow-same-origin allow-forms` per spec; this is the `@mcp-ui/client` default and stricter than what we'd need to whitelist ourselves)
- `UI_EXTENSION_CAPABILITIES` declaration on the backend MCP client (so servers know to advertise UI resources) — see Backend Changes
- `AppBridge` + `PostMessageTransport` for bidirectional events — **wired Active** (decided 2026-04-30); see "Active iframe → host integration" below

### Active iframe → host integration (decided 2026-04-30)

**Why active:** the workshop W7 demo needs to show the protocol earning its keep, not just rendering pixels. The story is "the iframe and the agent talk through a spec — neither knows about the other; we wrote zero map-specific code." A static globe doesn't tell that story; a globe that drives the next turn does.

**Demo sequence (workshop):**
1. User asks "show me the three regions on a map" → agent calls `show_locations` → `<AppRenderer>` mounts the globe in chat
2. User clicks a Munich pin in the iframe → iframe sends a postMessage notification (spec-defined; the map-server emits something like `{type:"app/notify", reason:"location-selected", payload:{location:"Munich"}}`)
3. `MCPAppToolCallRouter` receives the notification via `AppBridge`'s host-side handler, translates it into a synthetic user-message string ("Tell me more about Munich"), and calls `useSkillAgent.sendMessage(...)` so it goes into the chat as if the user typed it
4. Agent responds in the same chat thread; user sees the seamless loop

**Implementation shape (Phase 2):**
- `MCPAppToolCallRouter` accepts an `onAppMessage(notification, context) => void` prop (or uses the `requestHandler` exported by `@mcp-ui/client`'s `AppRendererHandle`)
- The default handler is a small adapter that:
  - Maps known notification shapes (e.g. `location-selected`, `route-selected`, `region-clicked`) to a templated chat message
  - Calls `agent.sendMessage(text)` via the AGUIProvider context
  - Falls through silently for unknown notifications (forward-compatible — a future server might emit events we don't model yet)
- The mapping table lives in `frontend/src/components/protocols/mcpAppNotificationAdapter.ts` so it's straightforward to extend per-server
- For unknown servers/notifications: log a `console.debug` and ignore. No exceptions propagated to the chat — degraded experience, not broken chat.

**What we explicitly do NOT do (defence-in-depth):**
- The iframe CANNOT call `useSkillAgent.sendMessage` directly. The host is the only thing that touches the chat; the iframe's reach is bounded by the spec's notification surface.
- The iframe CANNOT inject arbitrary HTML into the chat — its postMessage payloads are JSON, parsed and templated by us before any `sendMessage`.
- `AppBridge`'s spec origin checks remain the gatekeeper — verify on first integration that an iframe from origin A can't impersonate an iframe from origin B.

**Bumps Phase 2 estimate by ~0.5d.** Full sprint estimate updated to ~3.5d.

### Architecture Diagram

```
┌────────────────────── BROWSER ──────────────────────┐
│                                                     │
│  ChatPanel (CopilotSidebar)                         │
│    └── tool-call renderer routing                   │
│         ├── plain text/json → default card          │
│         └── UI resource    → <AppRenderer>          │
│                                  (from @mcp-ui/client) │
│                                  │                  │
│                                  ▼                  │
│                          ┌──────────────┐           │
│                          │ <iframe      │           │
│                          │   sandbox    │           │
│                          │   srcdoc=… > │           │
│                          │              │           │
│                          │  Cesium 3D   │           │
│                          │  globe (or   │           │
│                          │  whatever    │           │
│                          │  the server  │           │
│                          │  shipped)    │           │
│                          └──────┬───────┘           │
│                                 │ postMessage       │
└─────────────────────────────────┼───────────────────┘
                                  │ AG-UI SSE
                                  ▼
┌─────────────────── BACKEND (FastAPI + ADK) ─────────┐
│                                                     │
│  ADK LlmAgent (per skill)                           │
│    └── tools=[                                      │
│          parse_document,                            │
│          generate_document,                         │
│          McpToolset(ext-apps-map, …)  ◄── NEW       │
│        ]                                            │
│                                  │                  │
│                                  │ MCP transport    │
│                                  │ (streamable HTTP)│
└──────────────────────────────────┼──────────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────┐
                  │ ext-apps/map-server            │
                  │ (separate process or Cloud Run)│
                  │                                │
                  │  - tools: show_locations,      │
                  │           show_route           │
                  │  - returns: UI resource        │
                  │             (text/html;        │
                  │              profile=mcp-app)  │
                  └────────────────────────────────┘
```

## Spec Compliance (added 2026-04-30)

**Spec sources of truth:**
- MCP Apps blog post: https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/
- `@mcp-ui/client@7.0.0` package — verified by reading `node_modules/@mcp-ui/client/dist/src/index.d.ts` and `dist/src/capabilities.d.ts` on 2026-04-30
- SEP-1724 (capability extensions pattern): https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1724
- `@modelcontextprotocol/ext-apps` (server-side spec) and `@modelcontextprotocol/sdk` (transport contracts) — both pulled in transitively via `@mcp-ui/client`

**What we adopt verbatim (axiom #6 PROTOCOL OVER CUSTOM):**

| Spec element | Source | Aitana adoption |
|---|---|---|
| Resource MIME `text/html;profile=mcp-app` | `UI_EXTENSION_CONFIG.mimeTypes` | Backend declares it via `UI_EXTENSION_CAPABILITIES`; frontend detector `isUIResource` matches it |
| `CallToolResult.content[].resource.text` for HTML payload | MCP SDK `EmbeddedResource` type | Frontend reads via `isUIResource` type guard |
| `isUIResource(content)` detection | `@mcp-ui/client` | Frontend router uses it directly; no homegrown MIME parsing |
| `AppFrame` sandboxed iframe (allow-scripts allow-same-origin allow-forms) | `@mcp-ui/client` default | Used internally by `AppRenderer` |
| `AppRenderer` high-level component | `@mcp-ui/client` | Mounted by frontend router on UI-bearing tool results |
| `UI_EXTENSION_CAPABILITIES` declaration (`io.modelcontextprotocol/ui` extension per SEP-1724) | `@mcp-ui/client.UI_EXTENSION_CAPABILITIES` | Backend MCP client declares it via ADK `McpToolset` connection params |
| `AppBridge` + `PostMessageTransport` for iframe ↔ host events | `@modelcontextprotocol/ext-apps/app-bridge` | Used by default with `AppRenderer`; active integration deferred (see Open Questions) |
| McpToolset transport (StreamableHTTP / SSE) | ADK + MCP spec | `backend/tools/mcp/registry.py` already correct |
| Tool discovery / invocation lifecycle | MCP `tools/list` + `tools/call` | ADK `McpToolset` handles natively |

**What we explicitly diverge on (and why):**

| Element | Spec says | We do | Justification |
|---|---|---|---|
| `iframe sandbox` permissions | `allow-scripts allow-same-origin allow-forms` (per `@mcp-ui/client.AppFrame` defaults) | Use the spec default (no override) | We previously had `allow-scripts` only in the deleted `MCPAppFrame.tsx` — that was tighter than spec. The spec default is what real MCP App servers test against. Tightening unilaterally would break working apps; if a specific server is found to abuse `allow-same-origin`, we override per-server in the registry. |
| Active postMessage event handling | App can request data, navigation, etc. via postMessage | **Wired Active 2026-04-30.** `onMessage` translates iframe notifications into synthetic chat messages via `mcpAppNotificationAdapter`. With Path A's MCP Client also passed, iframe-initiated `tools/call` requests auto-forward through the same client to the MCP server (spec-default behavior; we don't need to add `onCallTool` overrides). | Workshop demo benefits; template ships with the full bidirectional surface |

**What we explicitly defer (file follow-up docs as needed):**

- **Active iframe → host actions** — see Open Questions: postMessage scope
- **Prefab for Aitana-owned tool UIs (Path B richness)** — once a real use case for our own MCP App server emerges
- **Multi-server collision policy** — `tool_name_prefix` works; strategic guidance deferred until two servers actually compete

**Compliance verification (lands as part of Phase 2 tests):**
- `test_mcp_registry_ui_capability.py::test_toolset_declares_ui_extension_capabilities` — asserts `UI_EXTENSION_CAPABILITIES` is in the McpToolset connection params
- `MCPAppToolCallRouter.test.tsx::test_renders_AppRenderer_for_canonical_mime` — fixture asserts the router mounts `<AppRenderer>` for a real captured payload
- `MCPAppToolCallRouter.test.tsx::test_passes_through_when_not_ui_resource` — locks the floor: non-UI tool results don't get hijacked

## Implementation Plan

### Phase 1 (revised 2026-04-30): Local map-server spike + capture a real `CallToolResult` (~0.5 day)

> **Strike the entire 2026-04-13 CopilotKit-native sub-section.** v6 dropped CopilotKit. The `@ag-ui/mcp-apps-middleware` route does not apply. We use `@mcp-ui/client` directly on top of the existing `@ag-ui/client` HttpAgent — same protocol, no extra runtime.

- [ ] Clone `modelcontextprotocol/ext-apps`, run `cd examples/map-server && npm install && npm run start:http` locally; record port + transport (typically `localhost:3001/mcp`) (~0.1 day)
- [ ] Configure a temporary Firestore `mcp_servers/ext-apps-map` doc pointing at `http://localhost:3001/mcp`; add it to the doc-analyst skill's `tool_configs.mcp.servers`; restart backend (~0.1 day)
- [ ] **Capture a real `CallToolResult` payload** by invoking `show_locations(["Munich","Singapore","São Paulo"])` either via `aiplatform skill probe` or a curl against the backend stream endpoint. Save as `frontend/src/components/protocols/__tests__/fixtures/map-server-show-locations.json`. This fixture is the contract for the router test in Phase 2 — if the wire format ever shifts, the test breaks first (~0.15 day)
- [ ] **Spec MIME compatibility check:** confirm the captured payload's `content[i].resource.mimeType` is `text/html;profile=mcp-app` (not `text/html+mcp` or anything else). If it diverges, the spec gap is on the `ext-apps` side, not ours; document the actual MIME and decide whether to coerce in the router or wait for upstream (~0.15 day)

### Phase 2 (revised 2026-04-30 for Path A): Adopt `@mcp-ui/client` + MCP proxy + active iframe→host (~2 days)

**Frontend — MCP Client + passive routing (~0.75d):**
- [ ] Add `frontend/src/lib/mcpClient.ts` (~50 LOC) — `Client` from `@modelcontextprotocol/sdk/client/index.js` + `StreamableHTTPClientTransport` from `@modelcontextprotocol/sdk/client/streamableHttp.js` pointing at `/api/proxy/mcp/{server_id}`. Firebase token via `fetchWithAuth` Authorization header. Per-server lazy instantiation + cache. Export `useMcpClient(serverId)` hook.
- [ ] Add `frontend/src/lib/__tests__/mcpClient.test.ts` (~80 LOC) — mocks fetch; asserts the Authorization header is attached; asserts client is cached per server id; asserts auth failure surfaces cleanly
- [ ] Add `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` (~100 LOC) — looks up tool definitions to detect `_meta.ui.resourceUri`; for UI-bearing tools mounts `<AppRenderer client={mcpClient} toolName={tc.toolCallName} toolInput={...} toolResult={...} onMessage={...} />` from `@mcp-ui/client`
- [ ] Add `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.test.tsx` (~100 LOC) — uses M1 fixtures (tools-list, show-map-result, ui-resource); mocks `Client.readResource` to return the captured UI resource; asserts `AppRenderer` is mounted with the right props; non-UI tool result returns null; missing client returns null gracefully
- [ ] Modify `frontend/src/components/chat/MessageBubble.tsx` — drop `extractMCPAppURIs` import + line 63 + lines 85-87; replace with `<MCPAppToolCallRouter toolCalls={mcpAppCandidates} />` block
- [ ] Delete `frontend/src/components/protocols/MCPAppFrame.tsx` and its test
- [ ] Update `frontend/src/components/chat/__tests__/MessageBubble.test.tsx` — replace any `MCPAppFrame` / `extractMCPAppURIs` references with the new router behaviour

**Frontend — active iframe → host integration (~0.5d):**
- [ ] Add `frontend/src/components/protocols/mcpAppNotificationAdapter.ts` (~60 LOC) — pure function `notificationToChatMessage(notification): string | null`. Maps known shapes (e.g. `location-selected`, `route-selected`) to templated strings ("Tell me more about Munich"). Returns null for unknown shapes (forward-compatible). Pure, no React, easy to unit test and extend per-server.
- [ ] Wire `MCPAppToolCallRouter` to obtain a `sendMessage` reference from the AGUIProvider context (or accept it as a prop) and pass `onMessage` to `<AppRenderer>` that translates notifications via the adapter and calls `sendMessage(...)`. Note: with Path A's `client` prop also passed, AppRenderer will auto-forward iframe-initiated `tools/call` requests through that client — no extra wiring needed for the iframe-as-MCP-peer story
- [ ] Add `frontend/src/components/protocols/__tests__/mcpAppNotificationAdapter.test.ts` (~60 LOC) — table-driven: each known notification shape maps to the expected chat message; unknown shapes return null; malformed payloads return null without throwing
- [ ] Add `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.activeBridge.test.tsx` (~80 LOC) — mounts router with a stub `AppRenderer`, fires a fake notification through the bridge handler, asserts `sendMessage` was called with the expected synthetic message; second case: unknown notification → no `sendMessage` call

**Backend — MCP proxy + UI capability + observability (~0.75d):**
- [ ] Add `backend/protocols/mcp_proxy.py` (~80 LOC) — FastAPI router for `POST /api/proxy/mcp/{server_id}` (and `GET .../sse` if StreamableHTTP needs it). Forwards JSON-RPC verbatim to the URL from `mcp_servers/{server_id}` Firestore doc using a Python MCP `Client` (or raw httpx). Auth: depends on existing `current_user` Firebase dependency. **Per-skill allowlist enforcement:** before forwarding, verify the caller has access to ≥1 skill that includes `{server_id}` in its `tool_configs.mcp.servers`. Reject 403 otherwise. (~0.3d)
- [ ] Add `backend/tests/api_tests/test_mcp_proxy.py` (~150 LOC) — happy path: authenticated user with skill access → 200 + forwarded JSON-RPC response. Negative paths: unauthenticated → 401; authenticated but no skill access → 403; unknown server_id → 404; downstream MCP server error → 502. (~0.2d)
- [ ] Extend `backend/tools/mcp/registry.py::_build_toolset` to declare `UI_EXTENSION_CAPABILITIES` (`io.modelcontextprotocol/ui` mimeTypes `["text/html;profile=mcp-app"]`) on the MCP client. Verify `StreamableHTTPConnectionParams` actually plumbs client capabilities through ADK to the MCP `Client` ctor — if not, file an ADK gap and use a temporary header signal as a workaround documented in this doc's Open Questions. (~0.15d)
- [ ] Add `backend/tests/tool_tests/test_mcp_registry_ui_capability.py` — asserts the McpToolset is configured with the UI capability declaration. Integration test marked `@pytest.mark.integration` against the local map-server that asserts `tools/call show-map` returns the `_meta.ui.resourceUri` indirection (matching M1 fixture shape). (~0.1d)
- [ ] Add `before_tool_callback` and `after_tool_callback` for `mcp_app.server_id` and `mcp_app.has_ui_resource` span attributes in the ADK callback chain (`backend/adk/callbacks.py` or similar). (~0.05d)

### Phase 3: Dev demo page + observability + end-to-end smoke (~0.5 day)

**Dev demo page (~0.25d, smoke loop):**
- [ ] Add `frontend/src/app/dev/mcp-apps/page.tsx` (~80 LOC) — hardcoded route that loads the Phase 1 captured `CallToolResult` fixture and renders it through `MCPAppToolCallRouter` with the active bridge wired to a stubbed `sendMessage` (renders to an on-page `<pre>` so you can see what the iframe is sending without standing up the full chat stack). Exists alongside `/dev/rich-media` (referenced in 1.19) as an iteration surface.
- [ ] Sub-routes if useful: `/dev/mcp-apps/passive` (no bridge — pure render) and `/dev/mcp-apps/active` (full bridge + adapter + chat-like message preview)
- [ ] Add a small "fire test notification" button on `/dev/mcp-apps/active` that synthesises common notifications (location-selected, route-selected, unknown-shape) so the adapter can be exercised without iframe interaction
- [ ] Document the page in `docs/ops/dev-routes.md` (or wherever dev-only routes are catalogued; create the file if it doesn't exist)

**Observability + skill seeds (~0.15d):**
- [ ] Seed Firestore `mcp_servers/ext-apps-map` for each env (dev/test/prod URL templates) — script in `backend/scripts/seed_mcp_servers.py` (~0.1d)
- [ ] Wire the `doc-analyst` and `web-researcher` skill definitions to include `mcp.servers: ["ext-apps-map"]` in their `tool_configs` (~0.05d)

**End-to-end smoke + verification log (~0.1d):**
- [ ] **End-to-end smoke (local):** Aitana frontend → backend → local `localhost:3001/mcp` map-server → render globe inline in chat for the Q1 fixture doc prompt "show me the three regions on a map" → click a pin → confirm a synthetic user message appears in chat → confirm agent responds. Capture screenshot for the workshop deck. (~0.05d)
- [ ] Update [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log: strike the resolved CopilotKit hypothesis (we don't use it); record the actual `@mcp-ui/client` adoption + the captured map-server MIME + the active iframe→host integration with notification adapter pattern (~0.05d)

### Phase 4 (revised 2026-04-30): Sidecar Cloud Run deployment — three-repo touch (~1.25 day)

**This phase touches three repos per the platform-deploy skill (the deploy skill). Read that skill BEFORE starting Phase 4 — the IAM cascade and per-env directory structure have specific gotchas (per `gotcha_two_deploy_projects` and `reference_promotion_pattern` memory).** Dev-only first; promote to test/prod after dev demo lands.

**Repo 1 — `sunholo-data/ai-protocol-platform` (this repo):**
- [ ] Add `infrastructure/mcp-ext-apps-map/Dockerfile` that pins a specific `modelcontextprotocol/ext-apps` commit, builds `examples/map-server`, runs on `$PORT` with `streamable_http` transport. Multi-stage build to keep the image small (~0.15d)
- [ ] Add `infrastructure/mcp-ext-apps-map/cloudbuild-mcp-ext-apps-map.yaml` — branch-based deploy step (`dev` branch → dev project; `test` → test; `prod` → prod). Mirrors the existing `cloudbuild.yaml` structure. Submit ONLY for dev branch in Phase 4; promote later (~0.15d)

**Repo 2 — `sunholo-data/multivac-aitana` (terraform):**
- [ ] Add `infrastructure/environments/dev/services/mcp-ext-apps-map.tf` — Cloud Run service definition. Region `europe-west1`, ingress `internal-and-cloud-load-balancing` (called from `platform-backend` over VPC connector with IAM auth — NOT publicly invokable), SA `platform@your-project-id.iam.gserviceaccount.com` (reuse the existing v6 SA via the bootstrap folder cascade — do NOT create a new SA per `feedback_no_manual_iam_grants`), scale 0–3, `--min-instances=0` for dev (cold starts acceptable; we'll bump to 1 for the workshop demo)
- [ ] Add the sidecar URL as a Cloud Run env var on `platform-backend-dev` — `MCP_EXT_APPS_MAP_URL=https://mcp-ext-apps-map-dev-<hash>.run.app/mcp`. Either via terraform variable interpolation or as a substitution baked at deploy-time
- [ ] Run `terraform plan -var-file=run_client.tfvars` from the `dev` env directory, confirm no IAM destroy-replace, `terraform apply` (~0.3d)

**Repo 3 — `Aitana-Labs/multivac-apps` (deploy):**
- [ ] Add Cloud Build trigger `trigger-mcp-ext-apps-map-dev` in `your-deploy-project-id` project pointing at `sunholo-data/ai-protocol-platform` repo, branch `dev`, included files `infrastructure/mcp-ext-apps-map/**`, build config `infrastructure/mcp-ext-apps-map/cloudbuild-mcp-ext-apps-map.yaml`. Use the `github-voight` connection (NOT the older `github` connection v5 used) per CLAUDE.md
- [ ] Verify the trigger fires on a no-op commit to a sidecar file before relying on it (~0.2d)

**Backend Firestore seed:**
- [ ] Update `mcp_servers/ext-apps-map` doc in dev Firestore to point at the deployed URL (override the localhost value used in Phase 1) — script in `backend/scripts/seed_mcp_servers.py` already from Phase 3 (~0.05d)

**Verification (~0.2d):**
- [ ] `./scripts/smoke-deployed.sh dev backend` returns 200 (existing target)
- [ ] Reproduce the Q1 globe prompt against deployed dev backend; confirm globe renders end-to-end across the two Cloud Run services
- [ ] Add `mcp-ext-apps-map-dev` to `docs/ops/deployed-urls.md` per `reference_v6_deployed_urls` memory
- [ ] Add a `mcp-ext-apps-map` smoke check to `scripts/smoke-deployed.sh` (probe the `/mcp` endpoint with a list-tools call)

**Promotion to test/prod is OUT OF SCOPE for this sprint** — only dev lands first. Promote via the standard two-PR flow (multivac-aitana then multivac-apps) once the dev demo is verified, ideally a week before the workshop. Per `reference_env_promotion_audit`, run the pre-promotion audit before the test/prod merge.

## Migration & Rollout

**Database Migrations:** None. Registry is config-file driven, not stored in Firestore.

**Feature Flags:** None — the registry itself is the gate. A skill can only invoke an MCP server if it's allowlisted in `mcp_servers.yaml`. To roll out a new server: add it to the YAML and redeploy the backend.

**Rollback Plan:**
- **Backend kill switch:** delete or disable the Firestore `mcp_servers/{server_id}` doc → next request to a skill with that server in `tool_configs.mcp.servers` finds no toolset, agent silently runs without the MCP App tool. No frontend change needed.
- **Per-skill kill switch:** remove the server id from the skill's `tool_configs.mcp.servers` array in Firestore → that skill loses access without affecting other skills.
- **Frontend kill switch (if `<AppRenderer>` itself misbehaves):** revert the `MessageBubble.tsx` change so the router is not invoked. Tool results display as `ToolCallChip` cards (text-only). One-line revert; chat continues to work.

**Environment Variables:** None new in this sprint. The map-server URL per-env (`MCP_EXT_APPS_MAP_URL` from Phase 4 deploy) lives in Cloud Build substitutions, not the application config — it's seeded into Firestore at deploy time, not read at request time.

**Cloud Run / infra impact (decided 2026-04-11):** `ext-apps/map-server` is deployed as a **standalone Cloud Run service** named `mcp-ext-apps-map-{env}` in each Aitana project (dev/test/prod). It is treated as a sidecar to `platform-backend`: same project, same VPC connector, same SA scope, deployed by the same Cloud Build pipeline.

Why standalone-Cloud-Run rather than a Docker stage of `platform-backend`:
- The map server is a Node process; `platform-backend` is Python. Combining them into one image would force one of them into a multi-runtime base — strictly worse for cold-starts and image size.
- Independent rollout: we can update `ext-apps` upstream and redeploy *just* the map server without rebuilding the Python image.
- Independent scaling: the map server is lightly used; it can scale to zero while `platform-backend` stays warm.
- Same blast radius: both services live in the same GCP project, so `operated_by: aitana` still holds and the privacy boundary is unchanged.

The registry URL is per-env via `MCP_EXT_APPS_MAP_URL` (set in each environment's `cloudbuild.yaml` substitutions, fed in via Secret Manager or plain env var since the URL isn't sensitive). For local dev, it points at `http://localhost:3001/mcp` running from a checked-out `ext-apps` clone.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `MCPAppToolCallRouter::test_renders_AppRenderer_for_canonical_mime` — fixture-driven (Phase 1 captured payload from real map-server); asserts `<AppRenderer>` is mounted with the captured `CallToolResult`
- [ ] `MCPAppToolCallRouter::test_passes_through_when_not_ui_resource` — non-UI tool result returns null so `MessageBubble`'s existing routing (A2UI / `ToolCallChip`) takes over
- [ ] `MCPAppToolCallRouter::test_handles_malformed_resultContent_gracefully` — JSON parse failure returns null, no crash, no rendering
- [ ] `MessageBubble::test_uses_router_for_mcp_app_tool_calls` — replaces the old `extractMCPAppURIs` test; asserts the router is invoked with candidate tool calls
- [ ] **Deleted tests:** `MCPAppFrame.test.tsx` (component is deleted), any `extractMCPAppURIs` regex tests

### Backend Tests (pytest)
- [ ] `test_mcp_registry_ui_capability::test_toolset_declares_ui_extension_capabilities` — asserts `UI_EXTENSION_CAPABILITIES` (or its serialised form) is present in the McpToolset connection params
- [ ] `test_mcp_registry::test_get_mcp_tools_returns_empty_for_missing_server` — already covered by existing test; reconfirm
- [ ] `test_mcp_registry::test_skill_tool_configs_drive_per_skill_allowlist` — asserts a skill's `tool_configs.mcp.servers` list determines which McpToolsets get attached to its agent
- [ ] Integration test (`@pytest.mark.integration`): with a local map-server running, register it via Firestore, instantiate a doc-analyst agent, invoke `show_locations(["Munich"])`, assert the response contains a `text/html;profile=mcp-app` resource

### Manual Testing (workshop demo dry-run)
- [ ] Run `examples/map-server` locally on `localhost:3001/mcp`
- [ ] Seed Firestore `mcp_servers/ext-apps-map` pointing at localhost; attach to doc-analyst skill
- [ ] Open Aitana frontend, pick Doc Analyst skill, upload the Q1 financial fixture
- [ ] Ask "show me the three regions on a map"
- [ ] Confirm: chat shows a streaming tool call → globe renders inline via `<AppRenderer>` → three pins appear → iframe is sandboxed (DevTools shows correct sandbox attribute)
- [ ] Capture network trace to confirm `tools/call` advertised + returned the `text/html;profile=mcp-app` MIME

## Security Considerations

- **Sandbox enforcement:** `@mcp-ui/client.AppFrame` (used internally by `AppRenderer`) mounts the iframe with the spec-default sandbox. We do NOT forward Aitana's auth tokens into the iframe context — the bridge only exchanges spec-defined messages.
- **Allowlist over open registry:** v1 of this feature does NOT let users register arbitrary MCP servers. The registry is Firestore `mcp_servers/{id}` documents managed by the platform team; per-skill activation is `tool_configs.mcp.servers` on the SkillConfig. A future v2 may add a curated marketplace; that's a separate doc.
- **Header injection:** if/when we add a `header_provider` callback to the McpToolset, it MUST only inject `x-aitana-skill-id` and `x-aitana-user-id` as opaque tracing context. It explicitly MUST NOT forward the user's Firebase ID token — the MCP server is treated as a third party.
- **MIME type validation:** `isUIResource` is the gatekeeper. Anything not matching the spec MIME falls through to non-UI handling (text card). Prevents a compromised MCP server from smuggling `text/html` (no spec sandbox guarantees) or `image/svg+xml` (script execution surface).
- **postMessage origin checks:** `@mcp-ui/client.AppBridge` and `PostMessageTransport` are spec-implementing. **Verify the package's origin-check behaviour as part of Phase 2 review** — if it doesn't enforce strict origin, we add a wrapper before going to test/prod.
- **Network egress:** the backend now makes outbound HTTP to MCP server URLs. For dev/test/prod sidecar deploys this is intra-VPC; if we ever register a non-Aitana hosted server, Cloud Run egress rules need updating. Document in `docs/ops/deployed-urls.md`.

## Performance Considerations

- **First-render latency:** the iframe mounts synchronously once `TOOL_CALL_RESULT` reaches the frontend and `MCPAppToolCallRouter` matches via `isUIResource`. Internal asset loading (Cesium tiles for the map server) happens inside the sandbox and is the MCP server's problem, not ours. Target: <300ms from event arrival to iframe DOM mount.
- **Bundle size:** `@mcp-ui/client` is already in `package.json` (currently dead code). Adopting it adds the actual import + `@modelcontextprotocol/sdk` + `@modelcontextprotocol/ext-apps` to the runtime bundle. Measure with `npm run build` before/after; expected delta is <50kB gzipped.
- **Backend latency:** MCP `tools/call` adds one network round-trip per invocation. For local dev (map-server on localhost) negligible. For deployed sidecar (same region, intra-VPC), expect <50ms p50.
- **Concurrent MCP servers per skill:** ADK's `McpToolset` opens a session per call, not a long-lived connection. No connection-pool tuning needed in v1.

## Success Criteria

- [ ] Backend tests passing (`cd backend && uv run pytest tests/ -m "not slow"`)
- [ ] Frontend tests passing (`cd frontend && npm run test:run`)
- [ ] Lint/typecheck clean (`cd frontend && npm run quality:check:fast`, `cd backend && make lint`)
- [ ] `MCPAppFrame.tsx` deleted; `MCPAppToolCallRouter.tsx` ships; `@mcp-ui/client` actually imported (no dead deps)
- [ ] Backend McpToolset declares `UI_EXTENSION_CAPABILITIES` (verified by test + manual capture of an `initialize` MCP frame)
- [ ] Spec MIME `text/html;profile=mcp-app` confirmed in a captured payload from the live ext-apps map-server (Phase 1 fixture committed to repo)
- [ ] Live demo (local): parse Q1 financial doc → ask for regions on a map → globe renders inline via `<AppRenderer>` → click a pin → synthetic user message appears in chat → agent responds
- [ ] Live demo (deployed dev): same scenario against deployed Cloud Run sidecar
- [ ] `/dev/mcp-apps/active` route loads the captured fixture, renders the iframe, lets the operator fire test notifications via the on-page button, and shows what the adapter translated them into — works with `make dev` alone, no backend round-trip required
- [ ] Verification log in [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) updated: strike CopilotKit hypothesis; record `@mcp-ui/client` adoption + captured MIME + active iframe→host integration
- [ ] Adding a *second* MCP server takes <1 hour: only Firestore doc + skill config edit, no code changes (verified by adding e.g. `ext-apps/threejs-server` as a smoke target if time permits)
- [ ] **Workshop W7 demo runs end-to-end with no placeholder paths anywhere in the call stack** — the iframe both *shows* and *responds to* user interaction without us writing any map-specific code

## Open Questions

- ~~**CopilotKit ↔ MCP Apps wiring**~~ **N/A 2026-04-30** — v6 dropped CopilotKit. The `@ag-ui/mcp-apps-middleware` route does not apply. We use `@mcp-ui/client` directly atop `@ag-ui/client`'s HttpAgent (the existing transport).
- ~~**MIME type allowlist / `text/html+mcp` vs `text/html;profile=mcp-app`**~~ **RESOLVED 2026-04-30 from `@mcp-ui/client@7.0.0` source:** canonical is `text/html;profile=mcp-app` per `UI_EXTENSION_CONFIG`. The `text/html+mcp` variant was an older/CopilotKit-middleware quirk and does NOT appear in the current `@mcp-ui` or `@modelcontextprotocol/ext-apps` packages. We adopt the canonical form. Phase 1 capture will reconfirm against the live map-server payload.
- **(NEW) ADK plumbs MCP client capabilities through `StreamableHTTPConnectionParams`?** — Phase 2 backend item. Need to verify the ADK `McpToolset` actually forwards `capabilities={"extensions": {...}}` to the underlying MCP `Client` ctor. If it doesn't, we file an upstream ADK gap and use a temporary header-based signal (`x-aitana-mcp-ui-supported: true`) so the map-server still advertises UI resources. Verify via `mcp__adk-mcp__search_code` for `Client(.*capabilities`.
- ~~**(NEW) postMessage scope for this sprint**~~ **DECIDED 2026-04-30: Active.** Mark wants iframe events to drive new chat turns — clicking a map pin appends a synthetic user message ("Tell me more about Munich") which the agent sees and responds to. This is the demo moment that makes the protocol story land in the workshop ("the iframe is talking back to the agent without us writing a single line of map-specific code"). Adds ~0.5d to Phase 2; full design captured in **Frontend Changes → Active iframe → host integration** below.
- **(NEW) Path B (Aitana FunctionTool returning a `ui://` reference)** — historically supported via `extractMCPAppURIs` regex; now deleted. Path B is re-implemented spec-correct: a Python FunctionTool returns a `CallToolResult` with an `EmbeddedResource` of MIME `text/html;profile=mcp-app`. Verify ADK's FunctionTool serialiser preserves the `EmbeddedResource` shape in `TOOL_CALL_RESULT.content`. If not, file an ADK gap; otherwise the SAME router handles both Path A and Path B without any branching.
- **`tool_name_prefix` collision strategy** — unchanged from original doc. ADK accepts multiple servers with `tool_name_prefix`; agent prompt confusion is a future problem.
- **Prefab for Path B tool UIs** — deferred. Prefab's `prefab-ui` produces standard MCP Apps wire format, so it would work through the same router with no Aitana-side change. Re-evaluate after Phase 4 ships and we have a real Path B use case.
- **Audit trail for tool calls returning UI resources** — OTel `before_tool_callback` + `after_tool_callback` with `mcp_app.has_ui_resource=true` lands in Phase 2. BigQuery sink TBD once we have real usage data.

## Related Documents

- [streaming-and-protocols.md](streaming-and-protocols.md) — AG-UI tool-call event flow this rides on
- [tools-porting-guide.md](tools-porting-guide.md) — general MCP tool registry (this doc adds the UI surface layer)
- [agent-factory.md](agent-factory.md) — `build_agent_from_skill` hook point
- [auth-and-permissions.md](auth-and-permissions.md) — auth context propagation
- [cloud-infrastructure.md](cloud-infrastructure.md) — Cloud Run egress rules for MCP server URLs
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — Layer 4b section + provider catalog + transport-decoupled rendering insight (this doc is the implementation of that insight)
- External: [modelcontextprotocol/ext-apps](https://github.com/modelcontextprotocol/ext-apps), [@mcp-ui/client on npm](https://www.npmjs.com/package/@mcp-ui/client), [MCP Apps blog post](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)
- External: ~~[@ag-ui/mcp-apps-middleware on npm](https://www.npmjs.com/package/@ag-ui/mcp-apps-middleware)~~ **Superseded 2026-04-30** — was investigated when v6 still had CopilotKit; v6 dropped CopilotKit, so this middleware does not apply. We use `@mcp-ui/client` directly atop `@ag-ui/client` HttpAgent.
- External: [Prefab by Prefect](https://prefab.prefect.io/docs) — generative UI framework for MCP Apps, `pip install fastmcp[apps]`. Candidate for Path B (Aitana-owned) tool UIs once a real use case emerges. Compatibility check on 2026-04-13 was via CopilotKit middleware (no longer relevant); needs re-verification against `@mcp-ui/client` directly when adopted.
- External: [SEP-1724 (capability extensions pattern)](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1724) — the spec basis for `UI_EXTENSION_CAPABILITIES` we declare on the backend MCP client
