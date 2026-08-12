# MCP App as Workbench-Tab Content Source

**Status**: Planned
**Priority**: P2 (Low — no current consumer; design-ahead-of-need)
**Estimated**: ~1.5 days (when a consumer materialises)
**Scope**: Frontend
**Dependencies**: v6.4.0 SHELL-MODES ([skill-driven-shell-modes.md](../v6.4.0/skill-driven-shell-modes.md)) ✅; v6.1.0 mcp-app-integrations (MCPAppToolCallRouter, `@mcp-ui/client` AppRenderer, sandbox proxy) ✅
**Created**: 2026-06-13
**Last Updated**: 2026-06-13

## Problem Statement

v6.4.0 SHELL-MODES gave `WorkbenchShell` a `content_source` directive per declared tab. Two of the three sources are real; one is a placeholder:

- `a2ui:<surfaceId>` → mounts `<A2UISurfaceMount>` — **fully wired**.
- `mcp_app:<server>` → **placeholder** (`workbench-tab-unsupported`).
- `fixed:<component>` → placeholder (reserved for v6.5 registry hook; out of scope here).

The `mcp_app:` source was left unwired for a structural reason discovered during the sprint, not for lack of time:

**MCP apps are tool-call-result-driven.** [`MCPAppToolCallRouter`](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx) iterates `useSkillAgent().toolCalls`, and for each tool call that carries a UI binding it parses the tool **result** and mounts `@mcp-ui/client`'s `AppRenderer`. A workbench tab is declared in `SKILL.md` frontmatter — it exists **before any tool has run**, so there is no `CallToolResult` to render. Even the `/dev/mcp-apps/passive` harness ([passive/page.tsx](../../../frontend/src/app/dev/mcp-apps/passive/page.tsx)) doesn't mount an MCP app "cold" — it feeds the router a **fixture** `show-map` result (`map-server-show-map-result.json`).

So a declared `mcp_app:ext-apps-map` tab has nothing to show until the agent (or someone) invokes a tool on that server.

**Current State:**
- `WorkbenchShell` renders an explicit "MCP App — iframe wiring is a follow-up" placeholder for any `mcp_app:` tab (honest, not a silent blank).
- **No production skill declares an `mcp_app:` workbench tab.** `document-analyst` (workbench-primary) uses `ext-apps-map`, but the map renders in the chat-drawer via the normal tool-call path, not as a tab.

**Impact:**
- Affects **skill authors** who want a *persistent* MCP-app surface (e.g. a map that stays visible in a workbench tab across turns) rather than an inline tool-call render. Today: not possible.
- Severity: **low / latent.** The schema advertises a capability the runtime doesn't honour — a documentation/contract gap more than a user-facing bug. No one is blocked yet.

## Goals

**Primary Goal:** Decide how (or whether) a declared `mcp_app:<server>` workbench tab renders a live MCP-app surface without an agent-driven tool call — and either implement the chosen path or formally retire the source from the schema.

**Success Metrics:**
- A `workbench-primary` skill can declare `content_source: mcp_app:ext-apps-map#show-map` and see the live map render in that tab on mount, with **zero agent/LLM turns** (direct MCP client call), **OR**
- the `mcp_app:` source is removed from the `content_source` enum and the schema/docs no longer advertise an unsupported capability.

**Non-Goals:**
- The `fixed:<component>` source (separate v6.5 registry-hook concern).
- Changing how MCP apps render in **chat** (the tool-call path is correct and unchanged).
- Building speculative infrastructure with no consumer — implementation is **gated on a real `workbench-primary` skill that needs a persistent MCP-app tab** (see Rollout).

## Options

### Option A — Synthesise a tool call on tab mount (direct MCP client) — RECOMMENDED

When a tab declares `mcp_app:<server>#<tool>` (with optional default args), the shell opens a **direct MCP client connection** to `<server>` on mount, calls `<tool>` with the declared args, and feeds the resulting `CallToolResult` into the existing `MCPAppToolCallRouter` / `AppRenderer`. No agent turn, no LLM cost. This is exactly the pattern [passive/page.tsx](../../../frontend/src/app/dev/mcp-apps/passive/page.tsx) already proves (`createDevDirectMcpClient` → mount router), minus the fixture.

- **Pros:** reuses 100% of the existing MCP-app render stack; it *is* a real MCP tool call (Protocol Over Custom); deterministic + cacheable; persistent surface intent met; degrades cleanly (no server / call fails → keep the placeholder).
- **Cons:** the tab config must name a tool + default args (`mcp_app:ext-apps-map#show-map?center=…`), a small schema extension; servers whose only tools need user-specific args don't fit (acceptable — they stay chat-only).

### Option B — Passive-init render contract in `@mcp-ui/client`

Extend the MCP-app render path so `AppRenderer` can mount from an empty/initial state with **no** tool result, and have the app self-initialise.

- **Pros:** no synthetic call; cleanest config (`mcp_app:<server>` with no tool).
- **Cons:** requires `@mcp-ui/client` / the MCP-UI spec to support a no-result init **and** each server's app to handle it — **unverified upstream capability** and out of our control. Speculative; highest risk for least incremental value over A.

### Option C — Retire `mcp_app:` as a tab source

Remove `mcp_app` from the `content_source` enum; MCP apps remain chat/tool-call-driven only. Document the decision.

- **Pros:** smallest, most honest; no fights with the tool-call-centric protocol; schema stops advertising an unsupported path.
- **Cons:** forecloses the persistent-MCP-app-tab use case; a skill author wanting an always-visible map must use the chat-drawer render instead.

## Recommendation

**Adopt Option A as the design, but gate implementation on a real consumer (default to Option C's honesty until then).** Concretely:

1. **Now:** keep the explicit placeholder, and **narrow the schema** so `mcp_app:` is documented as "reserved — see this doc" rather than implying it works (a one-line comment + this doc link). This removes the contract gap without building speculative infra.
2. **When a `workbench-primary` skill genuinely needs a persistent MCP-app tab:** implement Option A — direct-client init-tool call on mount, reusing the passive-render plumbing, with the `#<tool>` + default-args schema extension.

Rationale: A respects the protocol (it's a real tool call, Axiom 6) and reuses the entire existing stack, so it's cheap *when needed*; B depends on upstream capability we don't control; building either before a consumer exists is YAGNI. The placeholder + this doc is the correct resting state.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Option A uses a direct MCP client call on mount (no agent/LLM turn); result is cacheable per (server, tool, args). The tab paints as fast as the sandbox iframe loads. |
| 2 | EARNED TRUST | 0 | No new user-facing data claims; the rendered surface is the same sandboxed MCP-app iframe shown in chat today. |
| 3 | SKILLS, NOT FEATURES | +1 | A persistent MCP-app surface becomes one line of `SKILL.md` config (`content_source: mcp_app:<server>#<tool>`), not platform code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing involved; the direct-client path deliberately avoids invoking a model at all. |
| 5 | GRACEFUL DEGRADATION | +2 | Server absent / call fails / tool needs user args → fall back to the existing explicit placeholder. The chat-drawer MCP-app path is untouched. Unknown content sources already fall back. |
| 6 | PROTOCOL OVER CUSTOM | +2 | Option A is a real MCP `tools/call` against a real MCP server, rendered by the standard `@mcp-ui/client` `AppRenderer`. Option B (custom no-result init) and a bespoke tab-render path are explicitly rejected. |
| 7 | API FIRST | 0 | Frontend-only; no new backend endpoint (the MCP server already exists). Channels ignore shell/tab config. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The synthetic call can carry a span attribute (`mcp_app.tab.server` / `.tool`) so Cloud Trace shows tab-driven invocations distinctly from agent-driven ones. |
| 9 | SECURE BY CONSTRUCTION | 0 | Same sandbox + CSP + artefact-review hook as the existing MCP-app render; no new trust boundary. The tab can only call servers the skill already declares in `metadata.mcp.servers`. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The shell stays a thin dispatcher; the render contract is the existing MCP-UI protocol. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Design (Option A, when implemented)

### Overview

Extend the `ShellWorkbenchTab.content_source` grammar to `mcp_app:<serverId>[#<toolName>][?<default-args>]`. On mount, `WorkbenchShell` resolves such a tab by opening a direct MCP client to `<serverId>`, calling `<toolName>` with the parsed default args, and rendering the result through the existing `MCPAppToolCallRouter` (or `AppRenderer` directly). A loading state shows while connecting; any failure falls back to the current placeholder.

### Frontend Changes

**New components / hooks:**
- `src/components/shells/McpAppTabContent.tsx` — given `{serverId, toolName, args}`, manages the direct MCP client lifecycle (connect → call → render via `AppRenderer`; loading + error fallback). Reuses `@/lib/mcpClient` (`createDevDirectMcpClient` generalised to prod server config) and the existing result-parsing helpers from `MCPAppToolCallRouter`.

**Modified components:**
- `src/components/shells/WorkbenchShell.tsx` — `resolveTabContent()` parses the `mcp_app:` grammar and returns `<McpAppTabContent .../>` instead of the placeholder.

**State / data:**
- Server connection config (URL, transport, headers) already lives in Firestore `mcp_servers/{id}` and is surfaced to the frontend; the tab reuses it (no new fetch shape).

### Schema Changes

- `ShellWorkbenchTab.content_source` documented grammar gains the optional `#<tool>` and `?<args>` suffixes. No Pydantic change required (it's already a free `str`); validation is advisory in `aiplatform skill push` (warn if the server isn't in `metadata.mcp.servers`).

### CLI Surface

Per design-doc-creator rule 5b-bis: no new top-level command. Extend the existing `aiplatform skill push` validation to warn when a `mcp_app:<server>` tab references a server not declared in `metadata.mcp.servers` (~0.1d). Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

### Architecture Diagram

```
WorkbenchShell.resolveTabContent("mcp_app:ext-apps-map#show-map?center=Tokyo")
   │
   └── <McpAppTabContent server="ext-apps-map" tool="show-map" args={center:"Tokyo"}>
          │  (on mount — no agent turn)
          ├── direct MCP client connect (mcp_servers/{id} config)
          ├── tools/call show-map {center:"Tokyo"}  ──►  CallToolResult
          └── AppRenderer (@mcp-ui/client) in sandbox proxy iframe
                 (same sandbox + CSP + artefact-review hook as chat render)
   fail/absent ──► existing "workbench-tab-unsupported" placeholder
```

## Implementation Plan

Gated on a real consumer. When triggered:

### Phase 1 — Grammar + resolver (~0.5d)
- [ ] Parse `mcp_app:<server>[#<tool>][?<args>]` in `resolveTabContent` (~30 LOC + vitest)
- [ ] `McpAppTabContent` skeleton: direct client connect + loading/error states (~80 LOC)

### Phase 2 — Render + reuse (~0.5d)
- [ ] Wire the `CallToolResult` into `AppRenderer` reusing `MCPAppToolCallRouter`'s parse helpers (~60 LOC + vitest with a fixture result)
- [ ] Fallback-to-placeholder on connect/call failure (1 vitest)

### Phase 3 — Polish (~0.5d)
- [ ] `shell.tab.mcp_app` span attribute (1 test)
- [ ] `aiplatform skill push` advisory validation (~0.1d)
- [ ] Replace the WorkbenchShell placeholder test with a real-render assertion; live-verify against `ext-apps-map`

## Migration & Rollout

**Database Migrations:** None.
**Feature Flags:** None — skills opt in by declaring an `mcp_app:` tab.
**Rollback Plan:** Revert `resolveTabContent` to the placeholder branch; no schema/data change to undo.
**Environment Variables:** None.

**Gating:** Do **not** implement until a `workbench-primary` skill declares an `mcp_app:` tab. Until then the resting state is the explicit placeholder + this doc, and the `content_source` docs mark `mcp_app:` as "reserved — see mcp-app-workbench-tab-source.md".

## Testing Strategy

### Frontend Tests (Vitest)
- [ ] `resolveTabContent` parses `mcp_app:<server>` / `…#<tool>` / `…?<args>` correctly
- [ ] `McpAppTabContent` renders `AppRenderer` given a fixture `CallToolResult`
- [ ] Connect/call failure → falls back to the placeholder
- [ ] Backwards-compat: `a2ui:` and no-tab fallback paths unchanged

### Manual / E2E
- [ ] A test `workbench-primary` skill with `mcp_app:ext-apps-map#show-map` renders the live map in the tab on load, no agent turn (chrome-devtools via aitana-frontend-verify)

## Security Considerations

- A tab may only call servers already declared in the skill's `metadata.mcp.servers` (enforced/validated) — no arbitrary-server escalation.
- Same sandbox-iframe + CSP + artefact-review hook as the chat MCP-app render; no new trust boundary.
- Default args come from author-controlled `SKILL.md`, not user input — no injection surface beyond what the server already validates.

## Performance Considerations

- One direct MCP `tools/call` per `mcp_app:` tab on mount; result cacheable per (server, tool, args). No agent/LLM cost.
- Bundle: `McpAppTabContent` reuses already-bundled `@mcp-ui/client` + `mcpClient`; net delta is small.

## Success Criteria

- [ ] Decision recorded (Option A) and the `content_source` schema docs updated so `mcp_app:` no longer implies an unsupported runtime capability
- [ ] (On implementation) A `workbench-primary` skill renders a live MCP-app tab with no agent turn; failure degrades to the placeholder
- [ ] Frontend tests + lint + typecheck clean
- [ ] WorkbenchShell placeholder test replaced with a real-render assertion

## Open Questions

- **Q1 — Default-args grammar.** `?center=Tokyo` query-style vs a YAML `args:` map on the tab. Recommended: YAML `args:` map on `ShellWorkbenchTab` (typed, no URL-encoding) once implemented; the `#<tool>` suffix stays in the string for terseness.
- **Q2 — Caching/refresh.** Does the tab re-call on session change, or cache for the session? Recommended: cache per (server, tool, args) for the session; the existing `ui/update-model-context` push keeps the app live without a re-call.
- **Q3 — Do we ever need Option B?** Only if a server ships an app with a genuinely data-less initial view AND `@mcp-ui/client` supports no-result init. Revisit if such a server appears; verify against the MCP-UI client API at that time (per design-doc-creator rule 5c).

## Related Documents

- [skill-driven-shell-modes.md](../v6.4.0/skill-driven-shell-modes.md) — v6.4.0 SHELL-MODES; defines `content_source` and placeholders `mcp_app:` (see the as-built reconciliation note). This doc resolves that follow-up.
- [mcp-app-integrations.md](../v6.1.0/mcp-app-integrations.md) — MCPAppToolCallRouter, `@mcp-ui/client` AppRenderer, sandbox proxy.
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — `aiplatform skill push` validation surface.
