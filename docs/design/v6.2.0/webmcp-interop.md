# WebMCP Interop

**Status**: Planned (Exploratory — tracking, not scheduled)
**Priority**: P2 (Low — opportunistic)
**Estimated**: TBD (spike: ~2 days; full implementation: ~5–10 days per direction)
**Scope**: Fullstack
**Dependencies**: Chrome WebMCP API stable beyond EPP, OR W3C draft with 2+ browser implementations
**Created**: 2026-05-15
**Last Updated**: 2026-05-15

## Problem Statement

The "agentic web" is forming around a small number of in-browser standards that let pages declare tools an AI agent can invoke. Three relevant artifacts exist today:

1. **W3C WebMCP draft** — Web Machine Learning CG, first published 2025-08-13. Authors from Microsoft and Google. Defines a browser-side JS API for pages to expose tools (declarative-via-HTML and imperative-via-JS) to whatever agent is driving the user's browser. Not yet a Working Group spec; no browser ships it.
2. **Chrome WebMCP Early Preview Program (EPP)** — announced 2026-02-10 on developer.chrome.com. Two APIs: a Declarative API ("standard actions defined directly in HTML forms") and an Imperative API ("complex interactions requiring JavaScript"). EPP-only; full surface not yet public.
3. **Reference implementation** — [PierrickVoulet/meet-live-agent-webmcp](https://github.com/PierrickVoulet/meet-media-api-samples/tree/meet-live-agent-webmcp/webmcp-live-agent) demos `webmcp.init({manifest})` + `webmcp.registerTool({name, description, parameters, execute})` shimmed in JS, driven by a **headless Puppeteer agent in Cloud Run** that consumes Gemini Live's WebRTC audio + Meet Media API speaker tracks and emits function calls into `webmcp.executeTool()`. End-to-end: voice in a Meet call → AI updates a Kanban board.

**Current state of v6 vs. WebMCP:**
- v6 already speaks MCP server-side (FunctionTool, McpToolset) and renders MCP App iframes for tool UIs. WebMCP is the **inverse polarity**: pages advertise tools to agents, instead of agents discovering tool servers.
- v6 has **no path** for a third-party agent to drive the v6 frontend, and no path for an Aitana skill to drive a third-party SaaS that exposes WebMCP tools.
- Neither gap is causing user pain today. The opportunity is *positioning* — being a participant in the emerging open agentic web, and unlocking the "voice → action on any web app" pattern Pierrick's demo proves out.

**Pain points (latent, not active):**
- Every new third-party SaaS integration today requires building an MCP server (or a custom tool wrapper). WebMCP would let Aitana drive any WebMCP-enabled page directly.
- v6's UI is invisible to external agents. As agentic browsers ship, Aitana skills won't be drivable from "the rest of the agent ecosystem" without a WebMCP surface.

**Impact:**
- Affects: future product positioning, workshop narrative (July 2026), interop story for partners
- Significance: nice-to-have today; potentially structural in 12 months if Chrome's API ships broadly

## Goals

**Primary Goal:** Track WebMCP maturity and have a ready-to-execute design (this doc + a follow-up sprint plan) so v6 can adopt the protocol within ~2 weeks of it stabilizing.

**Success Metrics (when implemented):**
- v6 frontend exposes ≥10 tools via `window.webmcp` (open document, switch skill, send message, list documents, etc.)
- An external WebMCP-aware client (Chrome agent, Claude Desktop with WebMCP bridge, or a second Aitana instance) can drive a v6 session end-to-end
- An Aitana skill can drive ≥1 third-party WebMCP-enabled page (e.g., a Kanban demo) headlessly via Puppeteer/CDP
- Voice → action demo working: Gemini Live transcript → Aitana skill → `webmcp.executeTool` on a target page

**Non-Goals:**
- Replacing v6's existing MCP/AG-UI/A2UI stack — WebMCP is *additive*, not a replacement
- Building a general-purpose web automation platform (no headless-browser-as-a-service ambitions)
- Supporting jasonjmcghee/WebMCP's localhost-WebSocket model (security posture incompatible with v6 auth)
- Shipping before Chrome's API leaves EPP or W3C draft has multiple implementations

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Does not affect first-token latency or streaming path |
| 2 | EARNED TRUST | 0 | Interop layer; doesn't change citation/source story directly |
| 3 | SKILLS, NOT FEATURES | +1 | Consume direction makes "drive any WebMCP page" a generic capability without per-app custom code; expose direction makes v6 skills portable to external agents |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal to model routing |
| 5 | GRACEFUL DEGRADATION | 0 | Would require explicit fallback design (page changes, tool unavailable) before scoring +1 |
| 6 | PROTOCOL OVER CUSTOM | +1 | Adopts emerging W3C standard rather than building bespoke browser automation; aligns directly with v6's protocol-stack thesis |
| 7 | API FIRST | 0 | Mixed — consume direction is a new transport (browser); expose direction makes the web frontend a tool surface, which is *almost* a new channel |
| 8 | OBSERVABLE BY DEFAULT | 0 | Tracing for `window.webmcp` tool calls and headless-browser sessions would need explicit design; not free |
| 9 | SECURE BY CONSTRUCTION | -1 | Both directions widen trust surface: consume = headless browser driving third-party SaaS with delegated user creds; expose = external agents invoking tools on the v6 frontend. See justification below. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | The `window.webmcp` manifest lives in the frontend but is declarative wiring (tool name → backend call), not business logic. Implementation must hold the line. |
| | **Net Score** | **+1** | **Below +4 threshold — appropriately so. Do not proceed to implementation in this state.** |

**Conflict Justifications:**

- **Axiom 9 (SECURE BY CONSTRUCTION, -1):** Both directions of WebMCP interop expand the trust surface in ways that need careful design before implementation:
  - **Consume direction:** A headless Chrome driven by an Aitana skill, logged in as the user, can take any action the user can take on the target SaaS. This is more powerful than read-only AI search and demands explicit per-skill scoping, audit logging, and probably a "review before execute" mode for destructive actions. Mitigations exist (sandboxed Cloud Run, short-lived OAuth tokens, action allowlists per page) but need to be designed in, not bolted on.
  - **Expose direction:** Exposing `window.webmcp` on the v6 frontend means any agent that can reach the page (extension, in-browser AI, headless puppet) can invoke registered tools. Each registered tool must already be authenticated (the existing `/api/proxy` + Firebase token path handles this), but tool *availability* becomes discoverable in a way it isn't today. Lower risk than consume direction, but still a new attack surface.
  - **Why the tradeoff is acceptable (when we proceed):** WebMCP's design intent is page-opts-in, agent-page communication only — not ambient cross-tab access. Both directions stay within the GCP project edge (Aitana backend + Aitana frontend); the "external" thing is the *agent driving them*, which the user explicitly authorizes (by installing/using a WebMCP client). The egress concern (Axiom 9 privacy boundary) is symmetric to current /api/proxy usage. The expansion is permission-surface, not data-egress.
  - **Why we hold this -1 today:** the security design is not written. When this doc moves to "proceed," it must include a concrete threat model and mitigation list, and re-score Axiom 9 to 0 or +1.

**Net score commentary:** +1 is honest signal. This doc's purpose is to *track*, not to *build*. The score will rise once (a) the protocol is stable enough to commit to and (b) we add explicit security/observability design. Re-score before any sprint plan.

## Design

### Overview

Two independent interop directions, either adoptable on its own:

1. **Expose direction** — v6 frontend declares `window.webmcp` manifest + tool registrations. External WebMCP-aware agents can discover and invoke v6 capabilities (open doc, switch skill, send message, list sessions, etc.). Backend logic unchanged; new code is a thin frontend manifest layer that maps tool calls to existing `/api/proxy` endpoints.
2. **Consume direction** — A new ADK skill (`browser_drive` or similar) opens a target URL in headless Chrome, waits for `window.webmcp` to be available, reads the manifest, and exposes the page's tools as ADK FunctionTools the root agent can call. Implementation is server-side Puppeteer or CDP, packaged as a sidecar Cloud Run service (similar pattern to v5's bigquery sidecar).

Either direction can ship without the other.

### Frontend Changes (Expose direction)

**New Components:**
- `frontend/src/lib/webmcp/manifest.ts` — declares the `webmcp.init({manifest: {appName, systemInstruction}})` call on app mount
- `frontend/src/lib/webmcp/tools.ts` — registers tools via `webmcp.registerTool(...)`, each tool wrapping an existing `/api/proxy` call with `fetchWithAuth`
- `frontend/src/lib/webmcp/index.ts` — entry point, gated behind a feature flag (env var or settings toggle)

**Modified Components:**
- `frontend/src/app/layout.tsx` (or equivalent provider) — initialize WebMCP manifest on mount when feature flag is on

**State Management:**
- No new state. Tool implementations read existing contexts (auth, current session, current document) and dispatch through existing hooks.

**UI/UX:**
- No visible UI change in expose direction. The page silently advertises tools; an agent driving the browser can discover them via the protocol.
- Optional: a status badge ("WebMCP active — N tools available") in the developer settings panel, for debugging.

### Backend Changes (Consume direction)

**New Endpoints:**
- None on the main backend. The headless-browser sidecar exposes its own internal API (called only by the ADK skill).

**Modified Endpoints:**
- None.

**New Services/Modules:**
- `backend/tools/browser_drive/` — ADK skill that orchestrates the headless-browser sidecar
  - `service.py` — calls the sidecar's `/open`, `/list-tools`, `/invoke-tool` endpoints
  - `tools.py` — wraps each discovered page tool as a dynamic ADK FunctionTool
- New Cloud Run service `platform-browser-sidecar` (or similar) running Puppeteer + Chrome, exposing an internal HTTP API. Pattern: Pierrick's `agent-client` Cloud Run service.

**Data Model Changes:**
- Optional: `browser_sessions` Firestore collection to track active headless sessions (URL, expires_at, owner_user_id) for audit + cleanup. Not required for v1.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| — | — | No public API changes for either direction. Expose-direction tools reuse existing `/api/proxy` endpoints. Consume-direction sidecar API is internal. | No |

### Architecture Diagram

```
EXPOSE DIRECTION (external agent → v6)

  [External agent: Chrome WebMCP / Claude Desktop / extension]
                          │ webmcp.executeTool(...)
                          ▼
  [v6 frontend: window.webmcp manifest + tools]
                          │ fetchWithAuth → /api/proxy
                          ▼
  [v6 backend: existing endpoints, no change]


CONSUME DIRECTION (v6 skill → third-party page)

  [User] → [v6 chat] → [Root agent] → [browser_drive skill]
                                              │ HTTP
                                              ▼
                                   [Browser Sidecar (Cloud Run)]
                                              │ Puppeteer / CDP
                                              ▼
                                  [Headless Chrome → target URL]
                                              │ window.webmcp
                                              ▼
                                  [Third-party WebMCP page]
```

### CLI Surface

When implemented, add to the `aiplatform` CLI:

- `aiplatform webmcp inspect <url>` — open a URL in the sidecar, dump the discovered manifest + tool list. Useful for verifying a page exposes WebMCP correctly before wiring it into a skill.
- `aiplatform webmcp drive <url> --tool <name> --args <json>` — one-shot tool invocation against a remote WebMCP page. Smoke test for the sidecar.
- `aiplatform webmcp manifest` — dump the v6 frontend's own WebMCP manifest (expose direction). Hits a debug endpoint that returns the registered tool list.

Each command is ~0.25 day of CLI work. Backlinks to [local-dev-cli](../v6.1.0/local-dev-cli.md) once that doc lands. **Defer CLI work to the implementation sprint** — no value building it before the underlying feature.

## Implementation Plan

This is a tracking doc, not a sprint plan. When it's time to proceed, expect roughly:

### Phase 0: Spike (~2 days, when re-evaluating)
- [ ] Build minimal expose-direction proof on a throwaway branch (~5 tools, no auth wiring)
- [ ] Drive it from a real Chrome WebMCP agent OR Pierrick's `webmcp.js` shim
- [ ] Verify tracing/observability story (do tool calls show up in Cloud Trace?)
- [ ] Update this doc with findings, re-score Axiom 9, decide go/no-go

### Phase 1: Expose direction (~3 days, if go)
- [ ] Implement `frontend/src/lib/webmcp/` with feature flag
- [ ] Register first batch of tools (open document, switch skill, send message, list sessions, list skills)
- [ ] Add observability: emit a span per tool invocation, tag with `webmcp.tool.name`
- [ ] Add Vitest tests for manifest registration and per-tool dispatch
- [ ] Document for workshop (July 2026)

### Phase 2: Consume direction sidecar (~5 days)
- [ ] Stand up `platform-browser-sidecar` Cloud Run service (Puppeteer + Chrome)
- [ ] Internal API: `/open`, `/list-tools`, `/invoke-tool`, `/close`
- [ ] ADK skill `browser_drive` that wraps it as dynamic FunctionTools
- [ ] Threat model + mitigations: action allowlists, audit logging, session timeouts
- [ ] Integration test: drive a known WebMCP-enabled fixture page

### Phase 3: Voice → action demo (~2 days, optional)
- [ ] Wire Gemini Live transcript stream into `browser_drive` skill
- [ ] Workshop demo: voice → headless Kanban update (Pierrick-style, but Aitana-native)

## Migration & Rollout

**Feature Flags:**
- `NEXT_PUBLIC_WEBMCP_ENABLED` (frontend) — gates expose direction
- `AITANA_BROWSER_DRIVE_ENABLED` (backend) — gates consume direction
- Both default off until protocol stabilizes

**Rollback Plan:**
- Expose: turn off feature flag; tools disappear from the page's WebMCP manifest. No state to clean up.
- Consume: turn off feature flag; skill becomes unavailable. Tear down sidecar Cloud Run service.

**Environment Variables:**
- `WEBMCP_BROWSER_SIDECAR_URL` — internal URL of the headless-browser service
- `WEBMCP_AUDIT_BUCKET` — GCS bucket for headless-session audit logs (consume direction)

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `manifest.test.ts` — verify `webmcp.init()` is called once on mount with correct app metadata
- [ ] `tools.test.ts` — for each registered tool, verify the wrapped `fetchWithAuth` call is dispatched correctly
- [ ] Integration test: mock `window.webmcp`, register tools, simulate `executeTool` calls, assert backend call shape

### Backend Tests (pytest)
- [ ] `test_browser_drive_skill.py` — mock the sidecar, verify the skill discovers tools and exposes them as FunctionTools
- [ ] `test_browser_sidecar.py` — integration test against a fixture WebMCP page (served from test fixtures)
- [ ] Eval: `webmcp_drive_evalset` — given a known page, can the agent complete a task using the discovered tools?

### Manual Testing
- [ ] Expose: install Chrome WebMCP EPP, navigate to v6 frontend, verify tools appear and one tool succeeds end-to-end
- [ ] Consume: drive `https://demo-kanban.example/` (or Pierrick's demo) from a chat session
- [ ] Edge case: page changes its tool list mid-session (expose); page never loads `window.webmcp` (consume)

## Security Considerations

See **Axiom 9 conflict justification** above for the trust-surface analysis. Summary of what must be designed before proceeding:

**Expose direction:**
- Confirm the existing `/api/proxy` + Firebase auth posture is sufficient — every WebMCP tool must already enforce auth on the backend it calls
- Decide whether `window.webmcp` should be enabled for unauthenticated visitors (default: no — gate behind authenticated session)
- Audit: log every `executeTool` invocation with user_id, tool_name, args, result_status

**Consume direction:**
- Strict per-skill action allowlist: a skill declares "I will use tools X, Y, Z on domain D" and the sidecar refuses anything else
- Headless browser session audit log to GCS (URL, tool calls, screenshots at decision points)
- OAuth token scoping: never reuse the user's primary OAuth tokens; mint scoped tokens per session where possible
- Rate limiting + per-user quotas to bound damage from a misbehaving skill or a prompt injection
- Sandboxed Cloud Run with deny-by-default egress (only the target domain reachable)

**Both directions stay inside the GCP project edge** — no third-party SaaS in the data path, no prompts/responses egress. The "external" thing is the agent identity driving the page, which the user explicitly authorized.

## Performance Considerations

- **Expose:** negligible — `window.webmcp` registration is one-time on mount, ~kB of JS, no runtime cost when no agent is connected
- **Consume:** headless Chrome is heavy — ~500MB RAM per session, ~1–3s page-load + manifest-discovery overhead before first tool call. Sidecar must be sized for concurrent sessions (start with 1 session per Cloud Run instance, scale horizontally)
- Bundle impact (expose direction): target <10KB gzipped for the WebMCP shim + manifest

## Success Criteria

**For this tracking doc (now):**
- [x] Doc exists with axiom alignment and security analysis
- [x] Listed in v6.2.0 SEQUENCE.md as P2 / exploratory
- [ ] Reassessment trigger documented (see below)

**Reassessment triggers (when to revisit this doc):**
- Chrome ships WebMCP API beyond EPP (general availability), OR
- W3C WebMCP draft has 2+ independent browser implementations, OR
- A workshop or partner conversation creates a concrete use case requiring this within 6 weeks, OR
- Quarterly review (every 3 months from Created date)

**For the eventual implementation (when proceeding):**
- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`make test-fast`)
- [ ] Lint and typecheck clean
- [ ] Threat model written and reviewed; Axiom 9 re-scored to ≥ 0
- [ ] Cloud Trace shows spans for WebMCP tool calls (both directions)
- [ ] At least one external WebMCP client successfully drives v6 (expose)
- [ ] At least one third-party WebMCP page driven from an Aitana skill (consume)
- [ ] CLI commands work end-to-end
- [ ] Workshop talk updated with verification log entry (see [protocol stack talk](../../talks/ai-ui-protocol-stack.md))

## Open Questions

- **Which W3C draft revision do we target?** The proposal is moving. Pin to a specific commit hash when proceeding.
- **Does Chrome's EPP API match the W3C draft, or diverge?** The dev.chrome blog hints at "two APIs" (declarative + imperative); W3C draft surface is less documented. Spike must clarify.
- **Sidecar build vs. buy:** is there an existing Puppeteer-as-a-service we'd pay for vs. running our own? Browserbase, Browserless.io exist. Buy vs. build decision in Phase 2 design.
- **Voice integration:** does the voice → action demo need Gemini Live (WebRTC) or can we get away with Web Speech API + an ADK skill on transcripts? Affects scope significantly.
- **Telegram/email parity:** WebMCP is browser-only by definition. How does this fit Axiom 7 (API FIRST)? Probably: expose-direction tools also exist as MCP tools on the backend, so non-browser channels still get them. Consume-direction is browser-only and that's fine — it's a new capability, not a new channel.
- **Relationship to MCP Apps:** MCP Apps render *our* tool UIs in sandboxed iframes. WebMCP exposes *page* tools to external agents. They don't conflict. But: should an MCP App also be able to register WebMCP tools that bubble up to the parent page's manifest? Defer.

## Related Documents

- [AI UI Protocol Stack talk](../../talks/ai-ui-protocol-stack.md) — workshop tracker; add a "Layer 0: open-web → agent" entry referencing this doc
- [Migration to v6](../v5.0.0/migration-to-v6.md) — broader protocol stack thesis (AG-UI / A2UI / MCP / A2A)
- [SEQUENCE.md](SEQUENCE.md) — v6.2.0 build order
- [Local Dev CLI](../v6.1.0/local-dev-cli.md) — host for the eventual `aiplatform webmcp ...` commands
- External:
  - [W3C WebMCP draft](https://github.com/webmachinelearning/webmcp) — Aug 2025
  - [Chrome WebMCP EPP announcement](https://developer.chrome.com/blog/webmcp-epp) — Feb 2026
  - [PierrickVoulet/meet-live-agent-webmcp](https://github.com/PierrickVoulet/meet-media-api-samples/tree/meet-live-agent-webmcp/webmcp-live-agent) — reference implementation
  - [jasonjmcghee/WebMCP](https://github.com/jasonjmcghee/WebMCP) — early prototype, superseded by W3C work
