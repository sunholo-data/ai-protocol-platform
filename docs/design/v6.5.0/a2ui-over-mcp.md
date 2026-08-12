# A2UI over MCP (native-rendered tool UIs)

**Status**: Proposed — considered future extension, not scheduled for the current v6.5.0 sprint
**Priority**: P2 (Low)
**Estimated**: ~1.5d (frontend detection branch + host-side catalog validation + tests)
**Scope**: Fullstack (mostly frontend; small backend validation helper)
**Dependencies**: [a2ui-tool-delivery.md](../v6.1.0/implemented/a2ui-tool-delivery.md) ✅, [mcp-app-integrations.md](../v6.1.0/implemented/mcp-app-integrations.md) ✅, [mcp-app-update-model-context.md](../v6.1.0/implemented/mcp-app-update-model-context.md) ✅, [multi-surface-rendering.md](../v6.2.0/implemented/multi-surface-rendering.md) ✅
**Created**: 2026-06-23
**Last Updated**: 2026-06-23

> **Origin:** This doc was written after reviewing Google's 2026 blog post
> [*A2UI and MCP Apps: Integration Architectures for Agentic UIs*](https://developers.googleblog.com/a2ui-and-mcp-apps/).
> The post names three integration patterns. v6 already implements the
> rendering primitives for two of them; this doc scopes the one we do **not**
> yet do — the blog's **Pattern 1, "A2UI over MCP"** — as a considered future
> extension. It is design-ahead, gated on a real consumer (a remote MCP tool
> server that wants to contribute *structured* UI without shipping HTML).

## Problem Statement

Today, **only the in-process ADK root agent can emit native A2UI.** It does so
via the `send_a2ui_json_to_client` toolset
([a2ui-tool-delivery.md](../v6.1.0/implemented/a2ui-tool-delivery.md)), whose
result rides AG-UI `TOOL_CALL_*` events and is routed to the A2UI renderer by
tool name.

A **remote MCP tool server** has no equivalent path. Its only way to contribute
UI is the MCP Apps route: declare `_meta.ui.resourceUri` on the tool definition,
serve `text/html;profile=mcp-app`, and have the host mount it in a cross-origin
sandboxed iframe via `<AppRenderer>`
([MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx)).

That iframe path is correct for **complex, state-intensive modules** (the
CesiumJS globe fixture is the canonical example — exactly the case the blog says
belongs in an iframe). But it is the *wrong* tool for **structured UI** (a form,
a table, a confirmation card, a config panel) coming from a remote tool:

**Current State / pain points:**

- **Design-system fragmentation.** A third-party MCP server's iframe ships its
  own CSS. The blog calls out the failure mode directly: "clashing design
  systems or redundant scrollbars." This collides head-on with our
  per-deploy-branding stance (each deployment *is* the brand — there is no
  runtime tenant override). An iframe form will never match the host chrome; a
  native A2UI form always will.
- **Iframe overhead for trivial UI.** A sandbox-proxy handshake, a separate
  origin, a CSP negotiation, and an `<iframe>` boot to render what is
  structurally three text fields and a button. The iframe machinery exists for
  the Cesium case; paying it for a form is waste.
- **Larger trust surface than necessary.** Arbitrary HTML + JS in a sandbox is
  more dangerous than a declarative A2UI payload that can only express
  components from a trusted catalog and cannot execute scripts. For *structured*
  UI we are accepting more risk than the content requires.
- **No "write-once, render-natively" for tool authors.** A tool author who wants
  a clean native form must hand-author HTML/CSS that mimics our look, per host.
  The blog's pitch — "expertise in crafting MCP Tools now translates directly
  into the ability to generate sophisticated UIs" — is unavailable to our tool
  authors today.

**Impact:** Developers integrating remote MCP tools (and future template/fork
consumers who add their own MCP servers). No end-user-visible bug exists today —
this is a capability gap, which is why it is **design-ahead, gated on a
consumer**, not scheduled work.

## Where v6 already aligns with the blog

This is the useful framing: the blog mostly validates choices we already made.

| Blog concept | v6 status | Evidence |
|---|---|---|
| **A2UI = declarative JSON → predefined component catalog ("capability-based security")** | ✅ Implemented | `basicCatalog` from `@a2ui/react/v0_9`; [SurfaceRegistry.tsx](../../../frontend/src/providers/SurfaceRegistry.tsx); schema validation in the backend toolset [backend/adk/a2ui.py](../../../backend/adk/a2ui.py). Same A2UI **v0.9** the blog targets. |
| **MCP Apps = sandboxed iframe for creative freedom** | ✅ Implemented | `@mcp-ui/client` `<AppRenderer>` on a **separate-origin** sandbox proxy; [MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx), [mcp-sandbox-separate-origin.md](../v6.1.0/implemented/mcp-sandbox-separate-origin.md). |
| **Separation of concerns: MCP = tools/data, A2UI = rendering** | ✅ Implemented | A2UI lives in a toolset; MCP is the tool/data layer. Agent logic stays on reasoning. |
| **Decision tree: structured UI → A2UI; complex stateful module → iframe** | ✅ Applied correctly | Forms/cards = A2UI; CesiumJS globe / location picker = iframe. We already partitioned along the line Google recommends. |
| **Pattern 2 state-sync: agent tracks only "macro key-states," not micro-state** | ✅ Partial / spiritually aligned | `ui/update-model-context` → 4 KB cap → namespaced `mcp_app_context.{server}.{tool}` write ([mcp-app-update-model-context.md](../v6.1.0/implemented/mcp-app-update-model-context.md), [MCPAppToolCallRouter.tsx:404-451](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx#L404-L451)). This *is* the macro-key-state discipline; we just hadn't named it. |
| **Pattern 1: A2UI served *through* MCP (`application/a2ui+json`, `a2ui://`)** | ❌ Not implemented | This doc. |
| **Pattern 3: A2UI renderer bundled *inside* an MCP App (for non-A2UI hosts)** | N/A internally | v6 *is* an A2UI-native host. Relevant only to the public template / forks embedded in non-A2UI hosts — noted under Non-Goals. |

### The conscious divergence worth recording

The blog frames two A2UI delivery channels: **A2UI-over-MCP** and
**A2UI-over-A2A**. v6's primary path is *neither* — it is
**local-ADK-toolset-over-AG-UI**. That is a legitimate (and simpler) fourth
channel: the UI-emitting logic lives in the root agent we control, not in a
remote MCP server or a remote agent. This is a deliberate choice, not an
oversight. The cost of that choice is exactly the gap this doc addresses:
*remote* code cannot contribute native UI. Recording it here so a future reader
of the blog does not conclude we "missed Pattern 1" by accident.

## Goals

**Primary Goal:** Let a remote MCP tool return a native A2UI payload
(MIME `application/a2ui+json`) — via either a static `resources/read`
(`a2ui://` URI) or a dynamic `tools/call` result — and have the host render it
through the **existing** A2UI renderer + catalog instead of a sandboxed
`text/html` iframe.

**Success Metrics:**
- A remote MCP tool whose `_meta.ui.resourceUri` resolves to
  `application/a2ui+json` renders via `A2UIRenderer`/`SurfaceRegistry`, **not**
  `<AppRenderer>` — visually indistinguishable from agent-emitted A2UI.
- The host **validates** the inbound A2UI payload against the trusted catalog
  *before* render; an unknown-component payload degrades gracefully (does not
  render arbitrary content), matching the capability-based security model.
- The iframe path is unchanged for `text/html;profile=mcp-app` resources — zero
  regression to the Cesium-class case.
- Actions emitted by a native A2UI surface from an MCP tool route through the
  same `surface-action` gate as agent-emitted A2UI (no new action path).

**Non-Goals:**
- **Pattern 3** (bundling the A2UI renderer inside an MCP App for non-A2UI
  hosts). v6 is A2UI-native, so this is unnecessary internally. It may matter to
  downstream template forks embedded in a non-A2UI host — captured as a one-line
  note in the `agent-protocols` skill, not built here.
- Authoring new component catalogs — `basicCatalog` only, as today.
- A2UI-over-A2A (the other blog delivery channel) — separate concern, not in
  scope.
- Replacing the local `send_a2ui_json_to_client` path — this *adds* a remote
  source, it does not change the local one.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Native A2UI renders without the sandbox-proxy handshake + iframe boot the HTML path pays. For structured UI, first-paint is strictly faster than an iframe. |
| 2 | EARNED TRUST | 0 | No change to factual claims, citations, or provenance. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure invisible to end users; benefits MCP *tool authors* (a developer surface), not skill-builder users. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | The static `resources/read` (`a2ui://`) variant renders a fixed UI **without LLM synthesis** — a deterministic path where no model call is needed (the blog: "cached efficiently without LLM synthesis"). |
| 5 | GRACEFUL DEGRADATION | +1 | Unknown MIME → existing iframe/`ToolCallChip` path. Invalid/unknown-component A2UI → catalog renderer ignores it; never renders arbitrary content. Fail-open to the path we already ship. |
| 6 | PROTOCOL OVER CUSTOM | +2 | Adopts the published A2UI-over-MCP wire contract verbatim (`application/a2ui+json`, `a2ui://` URI, `resources/read` + `tools/call`). Extends protocol compliance to the MCP tool boundary instead of leaving native UI as a local-only capability. Pure protocol adoption. |
| 7 | API FIRST | 0 | No new HTTP surface; rides existing MCP + AG-UI transport. Web-rendering-specific (other channels render A2UI their own way already). |
| 8 | OBSERVABLE BY DEFAULT | 0 | A2UI-over-MCP results appear as the same named MCP tool calls already in traces; no new opacity, no new instrumentation. |
| 9 | SECURE BY CONSTRUCTION | +1 | A declarative, script-free payload constrained to a trusted catalog is a **smaller** trust surface than the arbitrary-HTML-in-sandbox it replaces for structured UI. Net reduction — *provided* the host validates the payload against the catalog at ingress (see Security Considerations). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Reuses the existing A2UI renderer; the change is a small MIME-detection branch + a validation call. No new client business logic. |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ — strong alignment |

**Conflict Justifications:** None (no axiom scored -1).

## Design

### Overview

The transport already exists. Remote MCP tool results already reach the frontend
as `toolCall.resultContent`, and the router already calls
`client.readResource({uri})` for the HTML path. The change is purely a
**content-type branch**: when the resolved resource (or tool-result content)
carries `mimeType: "application/a2ui+json"`, hand the parsed + catalog-validated
payload to `A2UIRenderer`/`SurfaceRegistry` instead of `<AppRenderer>`.

```
Remote MCP tool call (TOOL_CALL_END)
         │
   MCPAppToolCallRouter resolves the UI binding:
     · _meta.ui.resourceUri  → client.readResource({uri})
     · OR inline tool-result content
         │
   ┌─────┴───────────────────────────────────────────┐
   │ mimeType switch (NEW)                            │
   │  "text/html;profile=mcp-app"  → <AppRenderer>    │  (unchanged — Cesium-class)
   │  "application/a2ui+json"      → A2UIRenderer     │  (NEW — structured UI)
   └──────────────────────────────────────────────────┘
                              │
              host validates payload vs basicCatalog
                              │
              A2UIRenderer mounts native surface
                              │
              surface.onAction → existing surface-action gate
```

### Frontend Changes

**Modified: [MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx)**

- `readResource` already extracts the first content item
  ([line ~300](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx#L300)).
  Today it only reads `.text` and assumes HTML. Add the `mimeType` to the
  parsed `ResourceResult` and branch on it.
- For `application/a2ui+json`: `JSON.parse` the text into the A2UI message array
  (`[{version, createSurface|updateComponents|...}]`), run it through the
  host-side catalog validator (below), and render `A2UIRenderer` instead of
  building the `sandboxConfig` / `<AppRenderer>` subtree.
- Likewise branch in `parseToolResult` for the **dynamic** path — a `tools/call`
  result whose `content[]` includes a `{type:"resource", resource:{mimeType:"application/a2ui+json", text}}` item (the blog's dynamic delivery shape).
- The `_meta.ui` binding lookup (`extractUiResourceUri`) is unchanged — only the
  *content type of what the URI resolves to* changes the render path.

**Reused (no change):**
- `A2UIRenderer` / `SurfaceRegistry` — already render validated A2UI message
  arrays against `basicCatalog`.
- The `surface-action` POST path — A2UI actions from an MCP-sourced surface use
  the identical action route as agent-emitted A2UI.

**New (small): host-side A2UI catalog validator**
- A pure function `validateA2uiAgainstCatalog(messages, catalog): boolean` (or a
  thin wrapper over whatever validation the `@a2ui/react` SDK already exposes —
  **verify against installed `@a2ui/react@0.9` during the spike**; prefer the
  SDK's validator if present rather than reimplementing). This is the security
  linchpin: agent-emitted A2UI is validated **backend-side** by
  `SendA2uiToClientToolset`; a remote MCP server's payload is **not**, so the
  host must validate at ingress before render.

### Backend Changes

Minimal. The remote MCP server is responsible for emitting `application/a2ui+json`;
ADK already relays MCP tool results to the AG-UI stream unchanged. Two small
considerations:

- **Capability advertisement.** Today the host advertises MCP-App UI support via
  the `x-aitana-mcp-ui-supported: text/html;profile=mcp-app` header
  ([backend/tools/mcp/registry.py:101](../../../backend/tools/mcp/registry.py#L101)).
  Extend that header (or add a sibling) to also advertise
  `application/a2ui+json` so a capability-aware MCP server knows it may return
  native A2UI to this host.
- **No new validation backend-side** for the dynamic path — validation is a host
  (frontend) responsibility because that is where the trusted catalog renders.
  (If we later want a backend guard too, it belongs in the MCP toolset relay,
  not a new endpoint.)

### CLI Surface (optional, gated)

Per the design-doc CLI-affordance rule: a small, optional debug aid — extend the
MCP probe path so `aiplatform` can report, per tool, whether its `_meta.ui`
resource resolves to `text/html;profile=mcp-app` (iframe) or
`application/a2ui+json` (native). This answers "why did this tool render native
vs in an iframe?" without opening the browser. **Gated on the feature shipping**;
not built ahead of a consumer. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

### API Changes

None. No new or modified HTTP endpoints. The change is a content-type branch on
data already flowing through the existing MCP + AG-UI transport.

## Implementation Plan

### Phase 0 — Spike (~0.25d) — do not start without a consumer
- [ ] Confirm `@a2ui/react@0.9` exposes a catalog validator the host can call at
      ingress; if not, scope the minimal validator. (Verify against installed
      package, not memory.)
- [ ] Stand up a throwaway MCP server returning `application/a2ui+json` from
      both `resources/read` (static `a2ui://`) and a `tools/call` result.

### Phase 1 — Frontend detection branch (~0.75d)
- [ ] `ResourceResult` carries `mimeType`; branch in the `readResource` effect.
- [ ] Branch in `parseToolResult` for the dynamic `tools/call` A2UI-resource shape.
- [ ] Route validated `application/a2ui+json` → `A2UIRenderer`; leave HTML → `<AppRenderer>`.
- [ ] Wire MCP-sourced surface actions to the existing `surface-action` path.

### Phase 2 — Host-side validation + degradation (~0.25d)
- [ ] Catalog-validate before render; unknown components → degrade (no render),
      log, fall through to `ToolCallChip`.

### Phase 3 — Backend capability + tests (~0.25d)
- [ ] Advertise `application/a2ui+json` in the MCP UI-capability header.
- [ ] Tests (below).

## Migration & Rollout

**No data migration.** Pure additive render path. **Feature flag:** gate the new
branch behind an env/config flag (default off) until a consumer exists, so the
iframe path remains the only behaviour in production until deliberately enabled.
**Rollback:** remove the branch — HTML path is untouched.

## Testing Strategy

### Frontend Tests (Vitest)
- [ ] `readResource` returning `application/a2ui+json` → renders `A2UIRenderer`, not `<AppRenderer>`.
- [ ] `readResource` returning `text/html;profile=mcp-app` → still renders `<AppRenderer>` (regression guard).
- [ ] Dynamic `tools/call` result carrying an A2UI resource item → native render.
- [ ] A2UI payload with an unknown component → does not render arbitrary content; degrades.
- [ ] An action from an MCP-sourced A2UI surface POSTs to the `surface-action` path.

### Backend Tests (pytest)
- [ ] UI-capability header advertises both `text/html;profile=mcp-app` and `application/a2ui+json`.

### Manual
- [ ] Side-by-side: same form rendered (a) native via A2UI-over-MCP and (b) as an HTML iframe — confirm the native one matches host chrome and the iframe does not.

## Security Considerations

- **Host-side catalog validation is mandatory.** Agent-emitted A2UI is validated
  backend-side by `SendA2uiToClientToolset`; a remote MCP server's payload is
  **untrusted** and must be validated against `basicCatalog` at the host ingress
  *before* render. This is the capability-based security model the blog
  describes, and it is what makes accepting A2UI from a third party *safer* than
  accepting arbitrary HTML: a declarative payload constrained to a known catalog
  cannot execute scripts and cannot express anything outside the catalog.
- **Actions reuse the existing gate.** A2UI actions from an MCP-sourced surface
  route through the same `surface-action` endpoint (Firebase-auth + session +
  skill-allowlist gated) as agent-emitted A2UI. No new action path, no new
  trust relationship at the action layer.
- **Residual risk — UI spoofing.** Legitimate catalog components could still be
  arranged to mislead (e.g. a fake "confirm payment" card). This risk is **not
  worse** than today's iframe (which can render anything), and is mitigated by
  per-skill MCP-server allowlisting — a server must already be explicitly
  enabled for a skill. Worth a note in the eventual implementation, not a
  blocker.
- **No new data egress.** All processing is host-side within the GCP project
  edge; remote MCP calls already go through the auth-gated
  [mcp_proxy](../../../backend/protocols/mcp_proxy.py).

## Performance Considerations

- Native A2UI avoids the iframe sandbox-proxy handshake + CSP negotiation for
  structured UI — lower first-paint latency than the HTML path.
- The static `a2ui://` resource is cacheable and renders without an LLM call.
- One extra MIME branch + a validation pass per UI-bearing tool call —
  negligible.

## Success Criteria

- [ ] A remote MCP tool returning `application/a2ui+json` renders natively via the existing A2UI renderer, matching host chrome.
- [ ] Static (`resources/read` / `a2ui://`) and dynamic (`tools/call` result) delivery both work.
- [ ] Host validates against `basicCatalog` before render; unknown components degrade gracefully.
- [ ] Zero regression to the `text/html;profile=mcp-app` iframe path (Cesium fixture still renders in a sandbox).
- [ ] MCP-sourced A2UI actions route through the existing `surface-action` gate.
- [ ] Feature is flag-gated and default-off until a consumer is wired.

## Open Questions

- **SDK validator availability** — does `@a2ui/react@0.9` expose a catalog
  validator the host can call standalone, or must we extract one? (Spike.)
- **One router or two?** Extend `MCPAppToolCallRouter` with a MIME branch, or add
  a sibling `MCPNativeUIRouter` and let `MCPAppToolCallRouter` stay
  HTML-only? Leaning **extend** (shared `_meta.ui` resolution, `listTools`
  cache, and resource fetch — splitting would duplicate all of it).
- **First real consumer?** This is gated. Candidate triggers: a remote MCP tool
  in the doc-compare / web-researcher skill set that wants a native confirmation
  or config card; or a template-fork MCP server. Until one exists, the resting
  state is this doc + the schema note.

## Related Documents

- [a2ui-tool-delivery.md](../v6.1.0/implemented/a2ui-tool-delivery.md) — the local `send_a2ui_json_to_client` path this extends to remote MCP tools
- [mcp-app-integrations.md](../v6.1.0/implemented/mcp-app-integrations.md) — the iframe (`<AppRenderer>`) path this sits alongside
- [mcp-app-update-model-context.md](../v6.1.0/implemented/mcp-app-update-model-context.md) — the "macro key-state" sync the blog's Pattern 2 describes
- [mcp-app-workbench-tab-source.md](mcp-app-workbench-tab-source.md) — sibling "MCP-app render contract" design-ahead doc (v6.5.0 5.3), same gated-on-a-consumer posture
- [multi-surface-rendering.md](../v6.2.0/implemented/multi-surface-rendering.md) — `surface_id` / `update_mode` targeting reused by MCP-sourced surfaces
- [agent-protocols skill](../../../.claude/skills/agent-protocols/) — protocol-stack reference; record the Pattern-3 note here
- [Product Axioms](../../product-axioms.md)

## Sources

- [Google Developers Blog — *A2UI and MCP Apps: Integration Architectures for Agentic UIs*](https://developers.googleblog.com/a2ui-and-mcp-apps/) — the three integration patterns; Pattern 1 (`application/a2ui+json` MIME, `a2ui://` URI scheme, static `resources/read` vs dynamic `tools/call`, "A2UI over MCP vs over A2A"), Pattern 2 (macro key-state sync), Pattern 3 (renderer-in-app for non-A2UI hosts).
- [A2UI v0.9 — a2ui.org](https://a2ui.org/) — `basicCatalog`, declarative message schema (`createSurface`/`updateComponents`/`updateDataModel`/`deleteSurface`).
- [Model Context Protocol — modelcontextprotocol.io](https://modelcontextprotocol.io/) — `resources/read`, `tools/call`, `_meta` on tool definitions.
- Codebase, verified 2026-06-23: [MCPAppToolCallRouter.tsx](../../../frontend/src/components/protocols/MCPAppToolCallRouter.tsx) (HTML-only render path, `readResource`/`parseToolResult` hook points), [backend/adk/a2ui.py](../../../backend/adk/a2ui.py) (local toolset validation), [backend/tools/mcp/registry.py](../../../backend/tools/mcp/registry.py) (UI-capability header). Grep for `a2ui+json` / `a2ui://` across the repo: **zero hits** — Pattern 1 is genuinely unimplemented.
