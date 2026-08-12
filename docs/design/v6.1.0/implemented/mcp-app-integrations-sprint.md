# Sprint Plan: MCP-APP-INTEGRATIONS — MCP Apps spec compliance + workshop W7 demo

## Summary

Land [docs/design/v6.1.0/mcp-app-integrations.md](mcp-app-integrations.md) in full: replace the v6 placeholder MCP App surface (custom `MCPAppFrame` + `extractMCPAppURIs` regex) with a spec-compliant integration on top of `@mcp-ui/client@7.0.0` (already installed but unused). Wire active iframe→host postMessage so clicking a map pin appends a synthetic user message and the agent responds — the workshop W7 demo moment. Deploy the `ext-apps/map-server` as a Cloud Run sidecar to dev (test/prod promotion is OUT OF SCOPE).

**Duration:** ~4.0 days sequential, **~3.5 days with M2A/M2B parallel** (revised 2026-04-30 for Path A — frontend MCP Client via backend proxy. Original 3.25d Path B target bumped by ~0.5d for the new proxy endpoint + per-skill allowlist enforcement.)
**Scope:** Fullstack + infra
**Dependencies:**
- v6.0.0 backend + frontend running (✅)
- `@mcp-ui/client@7.0.0` installed (✅ — currently dead code)
- `backend/tools/mcp/registry.py::get_mcp_tools` already wires McpToolset per skill (✅)
- `platform-deploy` skill at `.claude/skills/platform-deploy/SKILL.md` (✅ verified)
**Risk Level:** Medium-High — Phase 4 touches three repos and one external open-source server; ADK capability passthrough through `StreamableHTTPConnectionParams` is unverified.
**Design Doc:** [mcp-app-integrations.md](mcp-app-integrations.md)
**Sprint ID:** `MCP-APP-INTEGRATIONS`

## Current Status Analysis

### Recent Velocity
- **Last 14 days (per `analyze_velocity.sh 14`):** 237 commits, 67k+ insertions across 466 files. Comparable infra-touching sprints (CICD-WIRE, AUTH-SMOKE) shipped multi-repo work in ~2 calendar days each.
- **Reference sprint:** `sprint_TTFT-INSTR.json` (3-day fullstack with parallel M1/M2) — went 600 LOC backend + 280 LOC frontend in 1 calendar day across two parallel milestones; pattern reusable here for M2A/M2B.
- **Estimated capacity for this sprint:** ~1500–1800 LOC implementation + tests over 3.25 calendar days.

### Existing Implementation (verified 2026-04-30)
- **Frontend renderer:** [frontend/src/components/protocols/MCPAppFrame.tsx](../../../../frontend/src/components/protocols/MCPAppFrame.tsx) is a custom sandboxed iframe with placeholder rendering. Will be DELETED.
- **Frontend extraction:** [frontend/src/components/chat/MessageBubble.tsx:63,85-87](../../../../frontend/src/components/chat/MessageBubble.tsx#L63) uses regex `extractMCPAppURIs` over agent text. Will be REPLACED.
- **`@mcp-ui/client@7.0.0`** in [frontend/package.json](../../../../frontend/package.json) — installed but never imported. Exports verified by reading `node_modules/@mcp-ui/client/dist/src/index.d.ts`: `AppRenderer`, `AppFrame`, `isUIResource`, `getUIResourceMetadata`, `AppBridge`, `PostMessageTransport`, `UI_EXTENSION_CAPABILITIES`, `UI_EXTENSION_CONFIG`.
- **Backend MCP toolset registry:** [backend/tools/mcp/registry.py](../../../../backend/tools/mcp/registry.py) `get_mcp_tools(server_ids)` already returns `McpToolset` instances from Firestore. Wired into [backend/adk/tools.py:181](../../../../backend/adk/tools.py#L181) per skill `tool_configs.mcp.servers`. Will be EXTENDED to declare `UI_EXTENSION_CAPABILITIES`.
- **AG-UI wire path:** [frontend/src/hooks/useSkillAgent.ts:262](../../../../frontend/src/hooks/useSkillAgent.ts#L262) already populates `tc.resultContent` from `TOOL_CALL_RESULT` events. The wire pipe is correct; only the consumer needs upgrading.
- **Existing precedent:** [frontend/src/components/chat/MessageBubble.tsx:79-84](../../../../frontend/src/components/chat/MessageBubble.tsx#L79-L84) — A2UI uses `parseA2UIResult(tc.resultContent)`. The new MCP App router follows the same pattern.

### Test surface baseline (post-sprint 1.23)
- Backend: 617 tests passing, ruff clean
- Frontend: 339 tests passing across 46 files, tsc + lint clean

### Velocity assumption
- Frontend: ~250 LOC/day implementation + ~150 LOC/day tests
- Backend: ~200 LOC/day implementation + ~150 LOC/day tests
- Infra (terraform + cloudbuild): ~0.5d per cohesive change set

## Proposed Milestones

### Milestone 1: Spike + capture real `CallToolResult` fixture (M1-SPIKE-FIXTURE)

**Scope:** fullstack-light (mostly env setup + one captured artifact)
**Goal:** Stand up the local map-server, confirm the spec MIME, and commit a real captured `CallToolResult` JSON fixture that becomes the contract for M2A's router test. **This milestone gates M2A's frontend test work** — without a real fixture the router test would be testing our own assumptions, not spec-compliant payloads.
**Estimated:** ~150 LOC (mostly Firestore seed + fixture artifact) + setup notes
**Duration:** 0.5 day
**Dependencies:** none (kicks off the sprint)

**Tasks:**
- [ ] Clone `modelcontextprotocol/ext-apps` to `~/dev/ext-apps`; record exact commit hash for Phase 4 Dockerfile pinning
- [ ] `cd examples/map-server && npm install && npm run start:http`; confirm port + transport (likely `localhost:3001/mcp`); document any setup quirks in this milestone's commit message
- [ ] Add `mcp_servers/ext-apps-map` Firestore doc in dev pointing at `http://localhost:3001/mcp` with transport `http`
- [ ] Add `mcp.servers: ["ext-apps-map"]` to the doc-analyst skill's `tool_configs` in dev Firestore (or via the seed script if simpler)
- [ ] Restart backend (`make dev`); confirm via `aiplatform skill probe doc-analyst` that the map-server tools appear in the agent's tool list
- [ ] Capture a real `CallToolResult` payload by invoking `show_locations(["Munich","Singapore","São Paulo"])` end-to-end. Save the full JSON as `frontend/src/components/protocols/__tests__/fixtures/map-server-show-locations.json`
- [ ] **Spec MIME compatibility check:** confirm the captured payload's `content[i].resource.mimeType` is `text/html;profile=mcp-app`. Document the result in the commit message — if it diverges, file a follow-up issue and decide whether the router coerces or waits for upstream (~5 min decision)

**Files to Create/Modify:**
- `frontend/src/components/protocols/__tests__/fixtures/map-server-show-locations.json` (new, captured artifact)
- `backend/scripts/seed_mcp_servers.py` (new or extended, ~50 LOC) — idempotent Firestore seed for `mcp_servers/ext-apps-map` so the dev env can be re-bootstrapped from clean

**Acceptance Criteria:**
- [ ] Fixture file committed and contains a `CallToolResult` with at least one `EmbeddedResource` of MIME `text/html;profile=mcp-app`
- [ ] `aiplatform skill probe doc-analyst` lists `ext-apps-map.show_locations` (or whatever `tool_name_prefix` lands as)
- [ ] Commit message documents the captured MIME, the ext-apps commit hash, and any deviations from spec
- [ ] No regression: `cd backend && uv run pytest tests/ -m "not slow" -q` still 617 passing

**Risks:**
- **map-server might not start cleanly out of the box** — it's an open-source reference impl with light maintenance. Mitigation: pin a known-working commit; document the steps; if blocked >2h, file an upstream issue and use a hand-crafted fixture matching the published spec wire shape (less ideal but unblocks M2A).
- **Captured MIME might not match spec** — if the live ext-apps map-server still emits `text/html+mcp` (the older variant), the router needs to handle both or we coerce in M2B. Decide at capture time, document in milestone notes.

---

### Milestone 2A: Frontend — MCP Client + passive routing + active iframe→host bridge (M2A-FRONTEND)

**Scope:** frontend
**Goal:** Replace the placeholder `MCPAppFrame` with a spec-compliant `MCPAppToolCallRouter` that uses `@mcp-ui/client.AppRenderer` for rendering. Wire the active iframe→host integration via `AppBridge` + a notification adapter so clicking a map pin appends a synthetic user message via `useSkillAgent.sendMessage`. **Path A addition (2026-04-30):** instantiate an MCP `Client` (`@modelcontextprotocol/sdk/client/streamableHttp`) per server pointing at the new backend proxy `/api/proxy/mcp/{server_id}`, threaded via `useMcpClient(serverId)` hook with Firebase token via `fetchWithAuth`. Pass to `<AppRenderer client={...} />` so spec-rich features (resource fetching, iframe-initiated tools/call) work without extra wiring.
**Estimated:** ~440 LOC implementation + ~360 LOC tests = ~800 LOC (was 640; +160 for MCP Client wiring)
**Duration:** 1.25 days (was 1.0; +0.25d for MCP Client wiring)
**Dependencies:** M1-SPIKE-FIXTURE (needs the captured fixture for tests)
**Parallelizable with:** M2B-BACKEND (M2A consumes the proxy endpoint M2B builds; for tests M2A uses a mock proxy, so they parallelize cleanly. Integration smoke happens in M3.)

**Tasks:**

**Passive routing (~0.4d):**
- [ ] `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` (~80 LOC) — accepts `toolCalls: ToolCallChip[]`. For each: JSON-parse `tc.resultContent`, run `isUIResource` from `@mcp-ui/client` over each `content[i]`, mount `<AppRenderer toolResult={result} />` for matches. Returns null when no UI resource found (so `MessageBubble`'s existing routing takes over).
- [ ] `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.test.tsx` (~100 LOC) — uses M1 fixture: (a) renders `<AppRenderer>` for canonical-MIME payload; (b) returns null for non-UI tool result; (c) returns null for malformed JSON without throwing; (d) returns null for empty `tc.resultContent`
- [ ] Modify `frontend/src/components/chat/MessageBubble.tsx` — remove `extractMCPAppURIs` import + line 63 + lines 85-87; insert `<MCPAppToolCallRouter toolCalls={mcpAppCandidates} />` block where the `mcpUris.map(...)` was. The candidates filter is `(tc) => tc.resultContent` (let the router decide if it's a UI resource)
- [ ] Delete `frontend/src/components/protocols/MCPAppFrame.tsx`
- [ ] Delete `frontend/src/components/protocols/__tests__/MCPAppFrame.test.tsx`
- [ ] Update `frontend/src/components/chat/__tests__/MessageBubble.test.tsx` — drop `MCPAppFrame` references; assert the new router is invoked with candidate tool calls (use a mock router so we don't need the M1 fixture inside MessageBubble's test)

**Active iframe → host integration (~0.4d):**
- [ ] `frontend/src/components/protocols/mcpAppNotificationAdapter.ts` (~70 LOC) — pure function `notificationToChatMessage(notification: unknown): string | null`. Maps known shapes (e.g. `{type:"app/notify", reason:"location-selected", payload:{location:string}}`) to templated strings ("Tell me more about Munich"). Unknown shapes return null (forward-compatible). No React, easy to extend per-server. Type all known shapes in a discriminated union; export the union for tests.
- [ ] Wire `MCPAppToolCallRouter` to obtain a `sendMessage` reference from the AGUIProvider context (or accept it as a prop from `MessageBubble`'s parent). Pass an `onAppMessage`/`requestHandler` to `<AppRenderer>` that translates each notification via the adapter and calls `sendMessage(translatedString)` when non-null
- [ ] `frontend/src/components/protocols/__tests__/mcpAppNotificationAdapter.test.ts` (~80 LOC) — table-driven cases: each known notification shape → expected message; unknown shape → null; malformed payload (missing required field) → null; non-object input → null
- [ ] `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.activeBridge.test.tsx` (~100 LOC) — mounts router with a stub `AppRenderer`; fires fake notifications through the bridge handler; asserts `sendMessage` is called with the expected synthetic message; second case: unknown notification → no `sendMessage` call

**Quality gate (~0.05d):**
- [ ] `npm run quality:check:fast` (lint + tsc + check:auth-fetch) clean
- [ ] `npm run test:run` — at least 339 + 6 new tests passing (no regressions in the existing 339)

**Files to Create:**
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` (~80 LOC)
- `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.test.tsx` (~100 LOC)
- `frontend/src/components/protocols/mcpAppNotificationAdapter.ts` (~70 LOC)
- `frontend/src/components/protocols/__tests__/mcpAppNotificationAdapter.test.ts` (~80 LOC)
- `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.activeBridge.test.tsx` (~100 LOC)

**Files to Modify:**
- `frontend/src/components/chat/MessageBubble.tsx` (~5 LOC delta)
- `frontend/src/components/chat/__tests__/MessageBubble.test.tsx` (~30 LOC delta)

**Files to Delete:**
- `frontend/src/components/protocols/MCPAppFrame.tsx`
- `frontend/src/components/protocols/__tests__/MCPAppFrame.test.tsx`

**Acceptance Criteria:**
- [ ] All 4 new test files passing; the router test uses the M1 fixture (not a hand-crafted shape)
- [ ] `MessageBubble` test asserts the router is invoked
- [ ] `npm run quality:check:fast` clean (no `any` types, no lint warnings, tsc happy)
- [ ] `MCPAppFrame.tsx` is gone; `extractMCPAppURIs` is gone; no dead-code references in `git grep`
- [ ] `@mcp-ui/client` is now actually imported (`grep -r "@mcp-ui/client" frontend/src`)
- [ ] Bundle size delta measured via `npm run build` before/after — recorded in milestone commit message

**Risks:**
- **`@mcp-ui/client.AppRenderer` API may not match the design's mental model** — the doc was written from a reading of the type definitions, not from actual usage. Mitigation: if `AppRenderer` doesn't accept `toolResult` directly, fall back to `AppFrame` + manual resource extraction in the router. Either path is spec-compliant; the `AppRenderer` shortcut is a convenience.
- **`useSkillAgent.sendMessage` context plumbing** — may need to thread the reference through `MessageBubble` props or use a context hook. Mitigation: prefer a hook (`useChatActions()` or similar) so the router stays self-contained; if a context boundary is awkward, accept `onSendMessage` as a prop on the router and lift the wiring up to `MessageBubble`'s parent.

---

### Milestone 2B: Backend — MCP proxy + UI capability declaration + observability (M2B-BACKEND)

**Scope:** backend
**Goal:** **Path A addition (2026-04-30):** Add `backend/protocols/mcp_proxy.py` exposing `/api/proxy/mcp/{server_id}` that forwards JSON-RPC to the registered MCP server URL, gated by Firebase auth + per-skill allowlist (caller must have access to ≥1 skill that includes `{server_id}` in its `tool_configs.mcp.servers`). PLUS the original M2B work: extend `backend/tools/mcp/registry.py` to declare `UI_EXTENSION_CAPABILITIES` so spec-compliant servers advertise UI resources back, add OTel span attributes for MCP tool calls, verify ADK plumbs MCP client capabilities through `StreamableHTTPConnectionParams` (if not, file upstream gap + use header_provider workaround).
**Estimated:** ~230 LOC implementation + ~270 LOC tests = ~500 LOC (was 300; +200 for the proxy + auth tests)
**Duration:** 0.75 day (was 0.5; +0.25d for proxy)
**Dependencies:** none (parallel with M2A)
**Parallelizable with:** M2A-FRONTEND

**Tasks:**
- [ ] **Verify capability passthrough** — use `mcp__adk-mcp__search_code` for `Client(.*capabilities` and `StreamableHTTPConnectionParams` to confirm ADK forwards `capabilities={"extensions": {...}}` to the underlying MCP `Client` ctor. Document the finding (path through ADK) in the milestone commit. **If ADK does NOT plumb capabilities through:** open an issue against `google/adk-python`, link it from the design doc's Open Questions, and use a temporary workaround — pass a custom header `x-aitana-mcp-ui-supported: true` via `header_provider` and document that the map-server doesn't currently key off it but spec-compliant servers should (~0.15d)
- [ ] Extend `backend/tools/mcp/registry.py::_build_toolset` to add `UI_EXTENSION_CAPABILITIES` (`io.modelcontextprotocol/ui` mimeTypes `["text/html;profile=mcp-app"]`) to the McpToolset connection params (or via `header_provider` if the workaround path applies) (~0.1d)
- [ ] `backend/tests/tool_tests/test_mcp_registry_ui_capability.py` (~80 LOC):
  - `test_toolset_declares_ui_extension_capabilities` — asserts the connection params (or headers, depending on workaround state) include the spec capability declaration
  - `test_toolset_falls_back_when_capability_already_set` — defence against double-write if a future server config also wants to declare its own capability
- [ ] Add `before_tool_callback` and `after_tool_callback` hooks (in `backend/adk/callbacks.py` or a new `backend/adk/mcp_observability.py`) that tag tool spans with `mcp_app.server_id` and `mcp_app.has_ui_resource=true` when the tool is from an MCP source AND the result contains a `text/html;profile=mcp-app` resource. ~70 LOC
- [ ] `backend/tests/unit/test_mcp_observability.py` (~70 LOC) — uses caplog/OTel test harness to assert the span attributes are set when expected; not set for non-MCP tools; not set when MCP returns plain text
- [ ] Integration test (`@pytest.mark.integration`, OK to skip in CI without map-server): with local map-server running, register it, instantiate doc-analyst agent, invoke `show_locations(["Munich"])`, assert the response contains a `text/html;profile=mcp-app` resource

**Quality gate (~0.05d):**
- [ ] `cd backend && uv run pytest tests/api_tests tests/unit tests/tool_tests -m "not slow" -q` clean
- [ ] `cd backend && make lint` clean

**Files to Create:**
- `backend/tests/tool_tests/test_mcp_registry_ui_capability.py` (~80 LOC)
- `backend/adk/mcp_observability.py` OR additions to `backend/adk/callbacks.py` (~70 LOC)
- `backend/tests/unit/test_mcp_observability.py` (~70 LOC)

**Files to Modify:**
- `backend/tools/mcp/registry.py` (~30 LOC delta — capability declaration + import)
- `backend/adk/agent.py` or wherever `before_tool_callback`/`after_tool_callback` are wired (~10 LOC delta)

**Acceptance Criteria:**
- [ ] `test_toolset_declares_ui_extension_capabilities` passes
- [ ] `test_mcp_observability` passes (3+ cases: UI resource detected; non-UI tool; non-MCP tool)
- [ ] All backend tests still passing (617 + new ones)
- [ ] Capability passthrough investigation result documented in milestone commit (path through ADK + any upstream issue link)
- [ ] `cd backend && make lint` clean

**Risks:**
- **ADK capability passthrough may be missing** — if `StreamableHTTPConnectionParams` doesn't accept or forward `capabilities`, we can't declare UI support natively. Mitigation: workaround via `header_provider` (documented above); the workshop demo still works because the ext-apps map-server emits UI resources unconditionally in current versions. File the upstream issue so this gets fixed properly later.
- **OTel span attribute sites may be hard to find** — ADK's `before_tool_callback` and `after_tool_callback` chains have moved between ADK versions. Mitigation: search via `mcp__adk-mcp__search_code` for current callback signatures; reference how `make_document_loader` is wired in `backend/adk/agent.py` for an existing pattern.

---

### Milestone 3: Dev demo page + skill seeds + e2e smoke (M3-DEV-DEMO)

**Scope:** fullstack
**Goal:** Land the `/dev/mcp-apps` smoke page (passive + active sub-routes, fixture-driven) so we can iterate on the iframe + adapter without standing up the full chat → backend → MCP roundtrip every time. Seed Firestore for the doc-analyst + web-researcher skills so the local end-to-end smoke runs out of the box. Capture screenshot for the workshop deck. Update talk doc verification log.
**Estimated:** ~200 LOC + ~150 LOC tests = ~350 LOC + smoke artifacts
**Duration:** 0.5 day
**Dependencies:** M2A-FRONTEND (router exists), M2B-BACKEND (capability declaration)

**Tasks:**
- [ ] `frontend/src/app/dev/mcp-apps/page.tsx` (~50 LOC) — index route with links to `/passive` and `/active`
- [ ] `frontend/src/app/dev/mcp-apps/passive/page.tsx` (~60 LOC) — loads M1 fixture from disk via dynamic import, renders through `MCPAppToolCallRouter` with the bridge wired to a no-op `sendMessage`
- [ ] `frontend/src/app/dev/mcp-apps/active/page.tsx` (~80 LOC) — loads M1 fixture, renders through router with bridge wired to a stubbed `sendMessage` that appends to an on-page `<pre>` log; adds a panel with buttons that fire synthesised notifications (location-selected with Munich, route-selected, unknown-shape) so the adapter can be exercised without iframe interaction
- [ ] `frontend/src/app/dev/mcp-apps/__tests__/active-page.test.tsx` (~80 LOC) — vitest: the page renders; clicking the "fire location-selected" button results in the expected synthetic message in the log; clicking "unknown-shape" results in nothing being logged
- [ ] `docs/ops/dev-routes.md` — new file (or extension) cataloguing dev-only routes (`/dev/rich-media` from 1.19, `/dev/mcp-apps/*` from this sprint) with their purposes and how to reach them
- [ ] **Skill seeds:** seed Firestore `mcp_servers/ext-apps-map` for dev (script from M1); add `mcp.servers: ["ext-apps-map"]` to `doc-analyst` and `web-researcher` skill configs in dev Firestore. The script should be idempotent (re-runnable safely)
- [ ] **End-to-end smoke (local):** with `make dev` running the platform AND `npm run start:http` running the local map-server, open Aitana frontend, pick Doc Analyst, upload the Q1 financial fixture, ask "show me the three regions on a map". Confirm: chat shows streaming tool call → `<AppRenderer>` mounts the globe inline → click a Munich pin → synthetic user message ("Tell me more about Munich") appears in chat → agent responds. Capture full-page screenshot for the workshop deck (save as `docs/talks/assets/mcp-apps-w7-demo-screenshot.png`)
- [ ] **Verification log update:** [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — strike the resolved CopilotKit hypothesis, record `@mcp-ui/client` adoption, the captured map-server MIME, and the active iframe→host integration with notification adapter pattern

**Files to Create:**
- `frontend/src/app/dev/mcp-apps/page.tsx` (~50 LOC)
- `frontend/src/app/dev/mcp-apps/passive/page.tsx` (~60 LOC)
- `frontend/src/app/dev/mcp-apps/active/page.tsx` (~80 LOC)
- `frontend/src/app/dev/mcp-apps/__tests__/active-page.test.tsx` (~80 LOC)
- `docs/ops/dev-routes.md` (~50 LOC)
- `docs/talks/assets/mcp-apps-w7-demo-screenshot.png` (binary artifact)

**Files to Modify:**
- `docs/talks/ai-ui-protocol-stack.md` (~20 LOC delta)

**Acceptance Criteria:**
- [ ] `/dev/mcp-apps/active` reachable via `make dev` alone (no backend round-trip required for the fixture-driven path)
- [ ] On-page button panel exercises the notification adapter; logged messages match expectations
- [ ] Vitest for the active page passes (4+ cases)
- [ ] End-to-end smoke completed with full chat → globe → click → synthetic message → agent response loop; screenshot captured
- [ ] Talk doc verification log updated
- [ ] `dev-routes.md` lists at least the new `/dev/mcp-apps/*` routes; cross-links the design doc

**Risks:**
- **Dev-only route may need a guard** — if the page accidentally ships to prod and gets indexed, it leaks the fixture content. Mitigation: gate behind `NODE_ENV !== "production"` in the page component, or follow whatever pattern `/dev/rich-media` (1.19) uses.
- **End-to-end smoke depends on the ext-apps map-server staying up locally** — same risk as M1; mitigation already covered there.

---

### Milestone 4: Sidecar Cloud Run deployment (dev only) (M4-SIDECAR-DEPLOY)

**Scope:** infra (touches three repos)
**Goal:** Deploy `ext-apps/map-server` as a Cloud Run sidecar in the `your-project-id` project so the dev demo runs end-to-end across deployed services. Test/prod promotion is OUT OF SCOPE for this sprint.
**Estimated:** ~250 LOC infra (Dockerfile + cloudbuild + terraform + trigger config) + ~50 LOC scripts + manual verification
**Duration:** 1.25 day
**Dependencies:** M3-DEV-DEMO (local end-to-end works → safe to deploy)

**MUST READ before starting:** the [`platform-deploy` skill](../../../.claude/skills/platform-deploy/SKILL.md) at `.claude/skills/platform-deploy/SKILL.md`. Per the skill's Quick Start: read `resources/topology.md` (3-repo map), `resources/iam-cascade.md` (the bootstrap folder rule that prevents IAM drift), and check `resources/recipes.md` for "Add a new Cloud Run service" — there is a known recipe.

**Tasks:**

**Repo 1 — `sunholo-data/ai-protocol-platform` (this repo, ~0.3d):**
- [ ] `infrastructure/mcp-ext-apps-map/` (new directory)
- [ ] `infrastructure/mcp-ext-apps-map/Dockerfile` — multi-stage build that pins the `modelcontextprotocol/ext-apps` commit captured in M1, builds `examples/map-server`, and runs on `$PORT` with `streamable_http` transport. Keep image small (alpine base if possible)
- [ ] `infrastructure/mcp-ext-apps-map/cloudbuild-mcp-ext-apps-map.yaml` — branch-based build step. Submits ONLY for the `dev` branch in this sprint; `test` and `prod` triggers are deferred per design doc Phase 4 note
- [ ] `infrastructure/mcp-ext-apps-map/README.md` (~30 LOC) — document the local build (`docker build`), the run command, and the deploy trigger

**Repo 2 — `sunholo-data/multivac-aitana` (terraform, ~0.45d):**
- [ ] Add a new entry to `cloud_run_multiple` in `infrastructure/environments/dev/run_client.tfvars` (per the existing `cloud_run_multiple` pattern in `main.tf:206`). Service name `mcp-ext-apps-map-dev`; region `europe-west1`; ingress `internal-and-cloud-load-balancing` (called from `platform-backend` over VPC connector with IAM auth — NOT publicly invokable); SA `platform@your-project-id.iam.gserviceaccount.com` (REUSE the existing v6 SA via the bootstrap folder cascade — do NOT create a new SA per `feedback_no_manual_iam_grants`); scale 0–3; `--min-instances=0` for dev (cold starts acceptable; bump to 1 closer to the workshop)
- [ ] Add the sidecar URL as a Cloud Run env var on `platform-backend-dev` — `MCP_EXT_APPS_MAP_URL=https://mcp-ext-apps-map-dev-<hash>.run.app/mcp`. Either via terraform variable interpolation or as a substitution baked at deploy-time (look at how other sidecar URLs are wired — likely the `cloud_run_urls` local in `locals.tf:29`)
- [ ] Run `terraform plan -var-file=run_client.tfvars` from the dev env directory; **inspect for any IAM destroy-replace** (per `gotcha_apikeys_project_replace` and `gotcha_run_multiple_sidecar_ignore` memories); if clean, `terraform apply`
- [ ] Verify the new service appears in Cloud Run console for `your-project-id`; confirm Ready: True

**Repo 3 — `Aitana-Labs/multivac-apps` (deploy triggers, ~0.3d):**
- [ ] Add a new Cloud Build trigger `trigger-mcp-ext-apps-map-dev` (location of trigger config: per the deploy skill — likely terraform in `multivac-apps/deploy/`). Pointing at `sunholo-data/ai-protocol-platform` repo, branch regex matching `dev`, included files `infrastructure/mcp-ext-apps-map/**`, build config path `infrastructure/mcp-ext-apps-map/cloudbuild-mcp-ext-apps-map.yaml`. **Use the `github-voight` connection per CLAUDE.md** (NOT the older `github` connection v5 used)
- [ ] Apply terraform; verify the trigger appears in Cloud Build console (`your-deploy-project-id` project per `gotcha_two_deploy_projects` memory)
- [ ] **Verify the trigger fires** on a no-op commit to `infrastructure/mcp-ext-apps-map/README.md` before relying on it for the real build

**Backend Firestore re-seed (~0.05d):**
- [ ] Update `mcp_servers/ext-apps-map` in dev Firestore to point at the deployed URL (override the localhost value used in M1). Reuse `seed_mcp_servers.py` script from M1; takes the URL as an arg

**Verification (~0.15d):**
- [ ] `./scripts/smoke-deployed.sh dev backend` returns 200 (existing target, regression check)
- [ ] Reproduce the Q1 globe prompt against the deployed dev backend; confirm globe renders end-to-end across the two Cloud Run services
- [ ] Add `mcp-ext-apps-map-dev` to [docs/ops/deployed-urls.md](../../ops/deployed-urls.md) per `reference_v6_deployed_urls` memory
- [ ] Add a `mcp-ext-apps-map` smoke check to `scripts/smoke-deployed.sh` (probe the `/mcp` endpoint with a `tools/list` call)

**Files to Create (this repo):**
- `infrastructure/mcp-ext-apps-map/Dockerfile`
- `infrastructure/mcp-ext-apps-map/cloudbuild-mcp-ext-apps-map.yaml`
- `infrastructure/mcp-ext-apps-map/README.md`
- `scripts/smoke-deployed.sh` (~30 LOC delta to add the sidecar probe)

**Files to Modify:**
- `docs/ops/deployed-urls.md`
- (cross-repo) `multivac-aitana` and `multivac-apps` files per their conventions

**Acceptance Criteria:**
- [ ] `mcp-ext-apps-map-dev` Cloud Run service is Ready: True in `your-project-id`
- [ ] Cloud Build trigger fires successfully on a `dev`-branch commit to `infrastructure/mcp-ext-apps-map/**`
- [ ] `platform-backend-dev` has `MCP_EXT_APPS_MAP_URL` env var set to the deployed sidecar URL
- [ ] End-to-end smoke against deployed dev: Q1 prompt → globe renders → click pin → synthetic message → agent response. Screenshot captured (overwrite or supplement M3's local screenshot)
- [ ] `docs/ops/deployed-urls.md` updated; `scripts/smoke-deployed.sh` includes the sidecar probe
- [ ] No IAM drift introduced (verified via `bash .claude/skills/platform-deploy/scripts/audit-drift.sh dev test` — even though we're not promoting yet, a clean audit confirms we didn't break the audit's expectations for the next dev→test promotion)

**Risks:**
- **Cross-repo PR coordination** — the three changes need to land in a sensible order (Dockerfile/cloudbuild first → terraform service → trigger). Mitigation: open all three PRs in close succession; the trigger PR can be reviewed independently because it doesn't actually fire until a commit hits the included files. Document the order in the PR description.
- **IAM cascade pitfalls** — per `feedback_no_manual_iam_grants` and `feedback_check_memory_before_rediscovering`, never gcloud-grant SA roles directly. The bootstrap folder cascade should give `platform@` SA the roles it needs to run a Cloud Run service in the project. **If the deploy fails with a 403:** stop, audit the SA roles via the `audit-drift.sh` script, do NOT add a manual grant.
- **`run_client.tfvars` schema** — adding to `cloud_run_multiple` requires matching the existing entry shape. Mitigation: copy the existing `platform-backend` entry as a template, edit fields; run `terraform plan` to see the diff before apply.
- **VPC + ingress** — `internal-and-cloud-load-balancing` ingress means the backend needs to call the sidecar through the VPC connector. Verify the existing v6 backend already has the VPC connector wired (it does for other internal services); if so, no additional plumbing.
- **Cold-start latency** — `--min-instances=0` for dev means first call wakes the container (likely 5–15s for a Node app). For workshop demo, bump to 1 a day before. Don't bake `min-instances=1` into dev permanently — wastes budget when not in use.

---

## Dependency graph

```
M1-SPIKE-FIXTURE (0.5d)
    ├──► M2A-FRONTEND (1d)  ─┐
    └──► M2B-BACKEND (0.5d) ─┤  ── parallelizable
                             ▼
                    M3-DEV-DEMO (0.5d)
                             │
                             ▼
                    M4-SIDECAR-DEPLOY (1.25d)
```

## Timeline

| Day | Sequential | Parallel |
|---|---|---|
| Day 1 AM | M1 (spike + fixture) | M1 (spike + fixture) |
| Day 1 PM | M2B start | **M2A AND M2B in parallel** |
| Day 2 AM | M2A | M2A continues / M2B finishes |
| Day 2 PM | M2A finish + M2B finish | M3 |
| Day 3 AM | M3 | M4 starts |
| Day 3 PM | M4 starts | M4 continues |
| Day 4 | M4 finishes | M4 finishes (light afternoon) |

**Sequential total:** ~3.75 calendar days
**Parallel total:** ~3.25 calendar days (saves M2B's 0.5d via overlap with M2A)

## Total LOC estimate

| Milestone | Implementation | Tests | Infra | Total |
|---|---|---|---|---|
| M1 ✅ | 50 (seed script) | 0 | (fixture artifact, ~9 KB) | ~50 |
| M2A | 280 | 380 | — | ~660 |
| M2B | 220 | 270 | — | ~490 |
| M3 | 240 | 80 | (screenshot, dev-routes.md ~50) | ~370 |
| M4 | 50 (smoke probe) | — | 200 (Dockerfile, cloudbuild, terraform, README) | ~250 |
| **Total** | **840** | **730** | **250** | **~1820 LOC** |

**Path A delta:** +160 LOC frontend (M2A — `mcpClient.ts` + tests + `Client` wiring in router) + +200 LOC backend (M2B — `mcp_proxy.py` + per-skill allowlist + tests). Tracks the +0.5d effort delta we accepted for template-grade canonical compliance.

Test ratio: 41% — slightly above the typical 30–50% for sprint work; appropriate given the spec-compliance focus (every spec assumption needs a regression test).

## Key risks (sprint-wide)

1. **External package behaviour assumption** — the design doc was written from reading `@mcp-ui/client@7.0.0` type defs and the MCP Apps blog post. Real `AppRenderer` API may diverge (e.g. it may want `requestHandler` instead of `onAppMessage`; sandbox config shape may differ). **Mitigation:** M2A starts with a 30-min code-read of `node_modules/@mcp-ui/client/dist/src/components/AppRenderer.d.ts` before writing the router; adapt the router shape to match before writing tests.
2. **ADK MCP capability passthrough** — flagged in M2B; if ADK doesn't plumb capabilities through, the workshop demo still works (map-server emits UI resources unconditionally) but we ship a documented workaround instead of a clean implementation. Track via the upstream ADK issue.
3. **Cross-repo Phase 4 coordination** — touches three repos. Skill `platform-deploy` has the recipe and the audit script; the planner-noted IAM pitfall (no manual grants) is a hard rule. **Don't skip the audit script.**
4. **Workshop-time scaling** — `--min-instances=0` for dev = cold-start lottery. Bump to 1 the day before the workshop demo, drop back after.
5. **Bundle size** — adopting `@mcp-ui/client` pulls in `@modelcontextprotocol/sdk` + `@modelcontextprotocol/ext-apps`. Expected <50kB gzipped delta but unverified. M2A acceptance includes a measurement; if it's >100kB, decide whether to lazy-load the router (split chunk).

## Quality gates

After each milestone:
- Frontend changes: `npm run quality:check:fast` (lint + tsc + check:auth-fetch)
- Backend changes: `cd backend && uv run pytest tests/ -m "not slow" -q && make lint`
- Infra changes: `terraform plan` clean (no IAM destroy-replace); `bash .claude/skills/platform-deploy/scripts/audit-drift.sh dev test`

End of sprint:
- `npm run test:run && npm run build` (frontend full)
- `cd backend && uv run pytest tests/ -q` (backend full)
- End-to-end smoke against deployed dev (M4 acceptance)

## Out of scope for this sprint

- Test/prod promotion of the sidecar (deferred per design doc; do this in a follow-up sprint a week before the workshop with the standard two-PR flow)
- Path B (Aitana-owned FunctionTool returning a UI resource) — design doc says it'll work through the same router with no Aitana-side change once a real use case appears; not exercising it this sprint
- Prefab adoption — re-evaluate after Phase 4 ships
- A second MCP server (e.g. ext-apps/threejs-server) — listed as a "verified <1h to add" success criterion but not actually exercising it unless time permits

## Hand-off note for sprint-executor

The JSON state file at `.claude/state/sprints/sprint_MCP-APP-INTEGRATIONS.json` mirrors this plan. Five milestones (M1, M2A, M2B, M3, M4) — M2A and M2B are parallelizable per the executor's parallel-mode capability. M4 is the heaviest and most cross-repo; consider executing it sequentially even when other milestones run in parallel, since the audit-drift safety check should be a deliberate human-reviewed step.
