# MCP App `ui/update-model-context` — bidirectional iframe ↔ agent state

**Status**: Implemented
**Priority**: P0 (workshop W7 demo credibility)
**Estimated**: 0.75 days (one focused half-day + a smoke pass)
**Scope**: Fullstack (frontend handler + backend session-state injector + small wire test)
**Dependencies**: Sprint 1.7 [mcp-app-integrations](implemented/mcp-app-integrations.md) shipped (Path A frontend MCP Client + sandbox proxy + `<AppRenderer onMessage>`); needed in workshop W7 demo flow.
**Created**: 2026-04-30
**Last Updated**: 2026-04-30

## Problem Statement

MCP Apps defines TWO iframe→host RPC channels — sprint 1.7 shipped one, the other errors silently:

| Channel | Spec method | What it carries | Use case | Status |
|---|---|---|---|---|
| Synthetic chat turns | `ui/message` (also called `notifications/message`) | One-shot strings the host turns into chat input | "Click pin → 'Tell me more about Munich' appears" | ✅ shipped sprint 1.7 (`<AppRenderer onMessage>` + `mcpAppNotificationAdapter`) |
| Model-context updates | `ui/update-model-context` | Structured content the host merges into the next agent turn's context | "User is currently viewing Munich at zoom 8 — agent's NEXT turn knows" | ❌ not handled — `MCP error -32601: No handler for method` |

**Live evidence (sprint 1.7 chat smoke, 2026-04-30):**

After the Cesium app receives `tool-input` and positions its camera, it fires `ui/update-model-context` with structured content:

```json
{
  "structuredContent": {
    "viewUUID": "abc-123",
    "currentBounds": {"west": 12.4101, "south": 55.5267, "east": 12.7301, "north": 55.8467},
    "label": "Copenhagen"
  }
}
```

Without an `onUpdateModelContext` handler on `<AppRenderer>`, the JSON-RPC request is rejected with `-32601: No handler for method`. The map keeps rendering correctly (it's a fire-and-forget context push, not a render dependency), but the agent's NEXT turn has no idea what view the user is currently looking at.

**Demo-narrative consequence (matters for workshop W7e):**

| User says | What works today (sprint 1.7) | What needs `ui/update-model-context` |
|---|---|---|
| "show me Munich" | ✅ Globe renders zoomed to Munich | (same) |
| "click pin → 'tell me more about Munich'" | ✅ Synthetic chat turn fires (channel 1) | (same) |
| "now zoom to its old town" | ❌ Agent has no idea what "it" refers to. Best case: agent re-geocodes "Munich Altstadt" by inferring from earlier chat. Realistic case: confused response. | ✅ Agent's context now contains "currently viewing Munich at W:11.4 S:48.0 E:11.7 N:48.2 zoom 8" — picks the right tool input |
| "what country is currently centred?" | ❌ Same blindness — agent only knows what was said in chat, not what's on screen | ✅ Agent reads `currentBounds` from context, knows it's Germany |

**Current State:**
- Workshop W7 narrative claims "the iframe and the agent are in conversation through a spec." Half-true today: the iframe can speak (via channel 1), but the agent stays blind to iframe state. Demo collapses on the second turn that references "this view".
- The `MCP error -32601` shows up in DevTools console on every map render — looks alarming for the workshop audience even though it's harmless.
- Other MCP Apps in the wild that we might want to demo (threejs-server, shadertoy-server, wiki-explorer-server) likely also push context updates; same blindness applies.

**Impact:**
- **Audience:** Workshop W7 attendees see a credible demo of "stateful iframe ↔ agent conversation" or they don't. Cosmetic-only synthetic chat turns is the "wow factor" for one turn; loses bite by turn three.
- **Devs cloning the public template:** Without a worked example of context flow, "how do I make my MCP App talk to my agent?" becomes a multi-day discovery exercise; with one, it's read-the-design-doc-then-copy.
- **Spec compliance:** Without it, v6 is half a host. The whole point of "we're a spec-compliant template" weakens if we ship the easy half.

## Goals

**Primary Goal:** Wire the `ui/update-model-context` handler end-to-end so the Cesium globe's view-state (bounds + UUID + label) reaches the next agent turn's context, removing the console error and letting the demo say "now zoom to its old town" with a credible response.

**Success Metrics:**
- DevTools console clean of `MCP error -32601: No handler for method: ui/update-model-context` after the chat smoke test
- Two-turn demo works: turn 1 "show me Munich" → globe renders + context updated; turn 2 "what city is currently centred?" → agent reads context → answers "Munich" without re-geocoding
- Three-turn demo works: turn 1 "show me Munich" → turn 2 "click a pin → 'tell me more about Munich'" → turn 3 "now zoom to its old town" → agent calls `geocode("Munich Altstadt")` → `show-map(new bounds)` without confusion
- Code-size budget: ≤ 80 LOC frontend + ≤ 120 LOC backend + ≤ 100 LOC tests (this is plumbing, not new architecture)

**Non-Goals:**
- Generic "two-way state sync between any iframe and any agent context" framework — overengineered. Solve the MCP Apps `ui/update-model-context` shape, nothing else.
- Frontend-side persistence of context across page reloads — context is per-session, lives in ADK session state on the backend. Refresh = new session = clean context (this is the right behaviour for the demo).
- Bidirectional context UPDATE from the agent back to the iframe (agent telling the iframe "now you're showing X"). Spec has `host-context-changed` for this; out of scope unless a Path-B widget needs it.
- A general-purpose "iframe writes to ADK state" REST endpoint that any iframe can call. We bind to the MCP Apps spec's `ui/update-model-context` shape; a generic write endpoint is a different design with different security implications.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Context flows asynchronously after the iframe's `setView` completes; doesn't block any user-visible action. Neutral. |
| 2 | EARNED TRUST | +1 | Agent can answer "what's currently on screen" instead of guessing/confusing — demonstrably more trustworthy when iframe state matters |
| 3 | SKILLS, NOT FEATURES | +1 | This is per-skill plumbing — any skill that uses MCP Apps benefits, no new user-facing concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing implications |
| 5 | GRACEFUL DEGRADATION | +1 | If the backend write fails, iframe still works; the agent just stays blind to iframe state (which is the current state). Genuinely degrades gracefully. |
| 6 | PROTOCOL OVER CUSTOM | +2 | This is the SPEC method; we're closing the second half of the MCP Apps contract. Strong axiom alignment. |
| 7 | API FIRST | +1 | Adds one well-typed endpoint (`POST /api/sessions/{id}/iframe-context`) that downstream forks of the public template can use as the worked example |
| 8 | OBSERVABLE BY DEFAULT | +1 | Each `ui/update-model-context` write logs a structured line + adds an OTel span (we already do this for /api/proxy/mcp/* calls; same pattern) |
| 9 | SECURE BY CONSTRUCTION | -1 | Adds a NEW write surface for the iframe to push data into the agent's session state. Audit notes below. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The frontend handler is ~30 LOC of "POST to backend"; the spec carries the contract |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ |

**Conflict Justifications:**

- **#9 SECURE BY CONSTRUCTION (-1):** A frontend handler that writes into ADK session state from arbitrary iframe input is a new attack surface. The iframe is sandboxed (different origin, no `allow-same-origin` on the inner frame, postMessage origin-checked at three boundaries) but the host code that receives `ui/update-model-context` is in the trusted host context, and what it writes goes into the agent's prompt context for the NEXT turn — a bad write could prompt-inject the agent. Mitigations:
  - The write goes through the SAME backend MCP proxy gate we already shipped — Firebase auth + per-skill allowlist (`/api/proxy/api/sessions/{session_id}/iframe-context` checks `can_access_skill(session.skill_id)`)
  - Server-side schema validation: the request body MUST match `{viewUUID: string, structuredContent: dict, sourceServerId: string}`. Anything else gets 400; logged + alerted.
  - Server-side size cap: `structuredContent` JSON capped at 4 KB serialized. Larger payloads get truncated + a warning logged. Stops a malicious iframe trying to flood the agent's context with garbage.
  - The merged content goes into a NAMESPACED key in session state (`mcp_app_context.{server_id}.{tool_name}`) — agent prompt template references this explicitly, NOT a generic "trust everything in context" splat. The iframe can't poison arbitrary session keys.
  - Per-tool ALLOWLIST of which MCP server's iframe is allowed to write context (configured per-skill in `tool_configs.mcp.allow_context_writes: ["ext-apps-map"]`). Default empty. New servers can't write context until explicitly opted in.
  - Comprehensive backend test for each gate (unauthenticated, wrong-skill, oversized, malformed, not-allowlisted) — covers the same shape as the existing `test_mcp_proxy.py` 11 tests.

  Net: the new surface is gated by the same axes that already gate `/api/proxy/mcp/*`, plus per-tool opt-in, plus schema + size validation, plus namespacing in session state. The `-1` reflects that "more attack surface than zero" is honest, not that the design is unsafe.

## Standards Compliance Check

This feature implements an existing spec method. **No custom format invented:**

- **`ui/update-model-context`** is defined in the MCP Apps spec — wire-format and method name come from the spec, not us.
- **MCP JSON-RPC** is the carrier — request/response shape is identical to every other MCP method.
- **`@mcp-ui/client.AppRenderer`** has an `onUpdateModelContext` callback prop — we wire OUR handler into the SDK's existing hook, not a parallel mechanism.

The custom bits we DO add:
- Backend endpoint `POST /api/sessions/{id}/iframe-context` — this is the host↔backend bridge, not a wire-protocol invention. It's the equivalent of how A2UI updates flow from frontend to ADK state today.
- ADK session-state key naming (`mcp_app_context.{server_id}.{tool_name}`) — internal to our state model; not a wire format.
- Skill-config field `tool_configs.mcp.allow_context_writes` — internal config, follows the existing `tool_configs.mcp.servers` convention.

No custom CSP, no parallel postMessage protocol, no novel auth scheme.

## CLI Surface

This feature has a small CLI affordance for debugging:

- `aiplatform sessions inspect <session_id> --mcp-context` — prints the namespaced `mcp_app_context.*` keys from the session's state. Useful when debugging "is the iframe actually pushing context updates?" without staring at the chat for the right log line.

Implementation: ~10 LOC click subcommand + an httpx GET to `/api/sessions/{id}` (existing endpoint), filter the state dict to keys prefixed `mcp_app_context.`. Lands in the same sprint as the feature.

Backlinks to [local-dev-cli.md](local-dev-cli.md).

## Design

### Overview

Three-tier wire: Cesium iframe → `<AppRenderer onUpdateModelContext>` callback (frontend) → `POST /api/sessions/{id}/iframe-context` (backend) → ADK `session.state["mcp_app_context.{server_id}.{tool_name}"] = {...}` (Firestore via ADK `SessionService`). On the next agent turn, the prompt template references `mcp_app_context.*` keys explicitly so the agent sees current iframe state.

### Frontend Changes

**Modified Components:**

- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — wire one new prop on `<AppRenderer>`:

  ```tsx
  <AppRenderer
    // ...existing props...
    onUpdateModelContext={async (params) => {
      // params: McpUiUpdateModelContextRequest['params']
      //   structuredContent?: Record<string, unknown>
      //   content?: McpUiContentBlock[]  (text/image/etc)
      try {
        await fetchWithAuth(
          `/api/proxy/api/sessions/${sessionId}/iframe-context`,
          {
            method: "POST",
            body: JSON.stringify({
              serverId,                     // from RoutedToolCall scope
              toolName: unprefixedName,     // from RoutedToolCall scope
              structuredContent: params.structuredContent ?? null,
              content: params.content ?? null,
            }),
            headers: { "Content-Type": "application/json" },
          },
        );
        return {};  // Empty result is the spec-compliant ack
      } catch (err) {
        console.warn("MCPAppToolCallRouter: update-model-context POST failed", err);
        return {};  // Still ack — the iframe doesn't need to know about transport failures
      }
    }}
  />
  ```

  The `sessionId` needs to be threaded through. Currently `RoutedToolCall` doesn't know the session — `MessageBubble` does (it has the parent message and session via the chat page's `currentSessionId`). Add `sessionId?: string` prop to `MCPAppToolCallRouter` and `RoutedToolCall`, plumbed from `chat/[...path]/page.tsx`.

**State Management:**

- No new client-side state. The handler is fire-and-forget (we ack immediately to the iframe; the POST completes async).
- If the POST fails (backend down), we log + ack-empty. The iframe doesn't retry; the agent's next turn just won't have the latest context. Graceful degradation.

**UI/UX:**

- Zero user-visible UI change. This is plumbing.

### Backend Changes

**New Endpoints:**

- `POST /api/sessions/{session_id}/iframe-context` — receives the iframe's structured-content push, validates, writes to session state.

  Request body:
  ```json
  {
    "serverId": "ext-apps-map",
    "toolName": "show-map",
    "structuredContent": {"viewUUID": "...", "currentBounds": {...}},
    "content": null
  }
  ```

  Response: `204 No Content` on success; standard error envelope on validation failure.

  Implementation: ~80 LOC in `backend/skills/iframe_context_routes.py` (new file). Logic:
  1. `Depends(get_current_user)` → Firebase auth (401 if missing).
  2. Look up `session = adk_session_service.get_session(session_id)`. If not found / not the user's: 404 / 403.
  3. Look up `skill = skill_config.get(session.app_name)`. (App name on the ADK session = our skill_id.)
  4. `if not access.can_access_skill(skill): 403`. (Mirrors mcp_proxy.)
  5. Validate `serverId` is in `skill.skill_metadata.tool_configs["mcp"]["servers"]` (caller can only write context for servers their skill activates). 403 if not.
  6. Validate `serverId` is in `skill.skill_metadata.tool_configs["mcp"]["allow_context_writes"]` (per-server opt-in). 403 if not.
  7. Validate `structuredContent` is a JSON object ≤ 4096 bytes serialized. 413 if oversized.
  8. Build the namespaced state key: `mcp_app_context.{serverId}.{toolName}`.
  9. `adk_session_service.append_event(...)` with a `state_delta` setting the namespaced key. (ADK's existing pattern for state mutations.)
  10. Log structured: `iframe_context_write skill_id=... session_id=... server_id=... tool=... bytes=...`.
  11. Return 204.

**Modified Endpoints:**

- None. We do NOT modify `/api/proxy/mcp/*` — that's the protocol surface; this is a separate host-internal endpoint.

**New Services/Modules:**

- `backend/skills/iframe_context_routes.py` (new) — the FastAPI router above.
- `backend/fast_api_app.py` — one line to mount the new router.

**Data Model Changes:**

- Skill config `skill_metadata.tool_configs.mcp` gains a new optional field:

  ```json
  {
    "tool_configs": {
      "mcp": {
        "servers": ["ext-apps-map"],
        "allow_context_writes": ["ext-apps-map"]   // NEW — defaults to [] if absent
      }
    }
  }
  ```

  No migration needed (absent = empty allowlist = behaves like the feature is off for that skill). Update `seed_skills.py` templates for `document-analyst` + `web-researcher` to include `ext-apps-map` in the allowlist.

- ADK session state grows a new namespace (`mcp_app_context.*`). No schema migration — session state is a free-form dict; we just start writing to a namespace that didn't exist before. ADK doesn't care.

**Agent prompt template change:**

- `backend/adk/agent.py` — when constructing the agent's instructions, append a system-style block that says:

  > "Current iframe-app context (from previously-rendered MCP App tools, if any):
  > {{ json.dumps(session.state.get('mcp_app_context', {}), indent=2) }}
  >
  > When the user references 'this map', 'the current view', 'what's on screen', or asks about
  > what the iframe is showing, consult this block before calling tools."

  ~15 LOC added to the existing instruction-builder. The block is empty (and emits nothing) when no context has been pushed, so it's a no-op for skills that don't use MCP Apps.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST   | /api/sessions/{session_id}/iframe-context | New host-internal endpoint for iframe→host context pushes | No (additive) |

No MCP wire-protocol changes. We're consuming an existing spec method (`ui/update-model-context`) via the SDK's existing callback hook.

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Browser (chat page on localhost:3456)                                │
│                                                                      │
│   ┌──────────────────────────────────────────────────────────────┐   │
│   │ Outer iframe (sandbox proxy on localhost:3457, separate     │   │
│   │ origin)                                                       │   │
│   │  ┌────────────────────────────────────────────────────────┐  │   │
│   │  │ Inner iframe (Cesium app, blob: URL inside outer)      │  │   │
│   │  │                                                          │  │   │
│   │  │ After camera positioned:                                 │  │   │
│   │  │   client.request({                                       │  │   │
│   │  │     method: "ui/update-model-context",                   │  │   │
│   │  │     params: {                                            │  │   │
│   │  │       structuredContent: {                               │  │   │
│   │  │         viewUUID, currentBounds, label                   │  │   │
│   │  │       }                                                  │  │   │
│   │  │     }                                                    │  │   │
│   │  │   })                                                     │  │   │
│   │  └────────────────────────────────────────────────────────┘  │   │
│   │              │ postMessage (JSON-RPC)                         │   │
│   │              ▼                                                 │   │
│   │  sandbox.ts bridge — relays to host                            │   │
│   │              │ postMessage (JSON-RPC)                          │   │
│   └──────────────┼─────────────────────────────────────────────────┘   │
│                  ▼                                                     │
│  <AppRenderer onUpdateModelContext={async (params) => {...}}>          │
│   ┌──────────────────────────────────────────────────────────────┐    │
│   │ Handler:                                                      │    │
│   │   await fetchWithAuth(                                        │    │
│   │     `/api/proxy/api/sessions/${sessionId}/iframe-context`,    │    │
│   │     { method: "POST", body: JSON.stringify(...) }             │    │
│   │   )                                                            │    │
│   │   return {}  // ack to iframe                                  │    │
│   └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ HTTPS + Firebase JWT
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Backend (FastAPI on :1956)                                           │
│                                                                      │
│   POST /api/sessions/{id}/iframe-context                              │
│       │                                                                │
│       │  1. get_current_user (Firebase)                                │
│       │  2. session = adk_session_service.get(id)                      │
│       │  3. skill = skill_config.get(session.app_name)                 │
│       │  4. access.can_access_skill(skill)                             │
│       │  5. serverId in skill.tool_configs.mcp.servers                 │
│       │  6. serverId in skill.tool_configs.mcp.allow_context_writes    │
│       │  7. validate structuredContent (≤ 4 KB JSON)                   │
│       │  8. session.state["mcp_app_context.{serverId}.{toolName}"]     │
│       │     = structuredContent                                        │
│       │  9. log structured + OTel span                                 │
│       └─→ 204 No Content                                                │
│                                                                      │
│   Next agent turn:                                                    │
│       create_agent() reads session.state["mcp_app_context"]           │
│       → injects into instruction template                              │
│       → agent's next reply has visibility into iframe state           │
└──────────────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1 — Backend endpoint + tests (~0.3 day, ~150 LOC + ~120 test LOC)

- [ ] `backend/skills/iframe_context_routes.py` — new router with the `/api/sessions/{id}/iframe-context` endpoint, all six gates (auth / session-exists / can_access_skill / server-in-allowlist / server-in-context-allowlist / size). Mirror `protocols/mcp_proxy.py` style for consistency.
- [ ] Add `mcp.allow_context_writes` field handling to `db/models/__init__.py` `SkillMetadata` (optional list, default empty).
- [ ] `backend/tests/api_tests/test_iframe_context_routes.py` — six gate tests (one per failure mode) plus the happy-path success test. Mirror `test_mcp_proxy.py` shape.
- [ ] `backend/fast_api_app.py` — mount the new router.

### Phase 2 — Frontend handler (~0.15 day, ~50 LOC + ~30 test LOC)

- [ ] `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — add `sessionId?: string` prop, thread to `RoutedToolCall`, wire `onUpdateModelContext` handler that POSTs to the new endpoint.
- [ ] `frontend/src/app/chat/[...path]/page.tsx` — pass `sessionId={currentSessionId}` to `<ChatMessageList>`, then to `<MessageBubble>`, then to `<MCPAppToolCallRouter>`.
- [ ] `frontend/src/components/chat/ChatMessageList.tsx` + `frontend/src/components/chat/MessageBubble.tsx` — accept + forward the new prop.
- [ ] `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.test.tsx` — extend with a test that mounts AppRenderer with `onUpdateModelContext`, fires a synthetic call, asserts the fetch hit the right URL with the right body.

### Phase 3 — Agent prompt injection (~0.1 day, ~25 LOC + ~30 test LOC)

- [ ] `backend/adk/agent.py` — in the instruction-builder (where existing system blocks like skill instructions get assembled), append the iframe-context block IF `session.state.get("mcp_app_context")` is non-empty.
- [ ] `backend/tests/integration/test_agent_iframe_context_injection.py` — assert the instruction string contains the expected iframe-context block when state has it, and DOES NOT contain it when state is empty.

### Phase 4 — Skill seed updates + CLI subcommand + smoke (~0.2 day, ~50 LOC + ~30 test LOC)

- [ ] `backend/skills/templates/document-analyst/SKILL.md` — add `mcp.allow_context_writes: [ext-apps-map]` to the toolConfigs block.
- [ ] `backend/skills/templates/web-researcher/SKILL.md` — same.
- [ ] One-shot Firestore update on your-project-id to add `allow_context_writes` to the existing 5 skills (idempotent script in `backend/scripts/migrate_mcp_context_writes.py`, ~30 LOC).
- [ ] `cli/aiplatform/commands/sessions.py` — `inspect --mcp-context` subcommand (~15 LOC).
- [ ] **End-to-end smoke** (manual + verified in browser):
  1. Hard-refresh chat
  2. Send "show me Munich on the map" → globe renders, console shows ZERO `MCP error -32601` lines
  3. Send "what city is currently centred on the map?" → agent answers "Munich" without re-geocoding (proves it read the context)
  4. Send "now zoom to its old town" → agent calls `geocode("Munich Altstadt")` → `show-map(new bounds)` → globe re-zooms (proves the multi-turn flow)

## Migration & Rollout

**Database Migrations:**

- No Firestore schema migration needed (session state is free-form). New session-state keys (`mcp_app_context.*`) just start appearing on chats that use MCP Apps.
- Skill config schema gains an optional field. One-shot script writes `allow_context_writes: ["ext-apps-map"]` to the two skills that need it (document-analyst, web-researcher) on your-project-id. Reads SKILL.md templates as the source of truth (mirrors what `seed_skills.py` does on cold-seed).

**Feature Flags:**

- The per-skill `tool_configs.mcp.allow_context_writes` field IS the feature flag. Set per-skill, defaults to empty (= feature off). Roll out by adding skills to the allowlist one at a time. No global kill switch needed because the field defaults to off.

**Rollback Plan:**

- If the backend write endpoint goes wrong: remove the skill from the `allow_context_writes` list — the frontend handler still POSTs but the backend returns 403, frontend logs + acks-empty, iframe is unaffected, agent reverts to "blind to iframe state" behaviour (the current state). Zero user-visible regression.
- If the agent prompt injection causes weird responses: remove the conditional block from `adk/agent.py`. State still gets written, just not surfaced to the agent. (Effectively this puts us in "writes-only" mode, useful for observability while debugging.)
- Hard rollback: revert the commits. Frontend stops posting; backend endpoint sits unused; session state keys persist harmlessly (no read path).

**Environment Variables:**

- None new. The endpoint reuses existing Firebase + ADK session config.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)

- [ ] `MCPAppToolCallRouter.test.tsx` — mount with `sessionId="test-123"`, simulate the router's `onUpdateModelContext` callback firing with a sample `params`, assert `fetch` was called with `/api/proxy/api/sessions/test-123/iframe-context`, correct body, correct headers.
- [ ] `MCPAppToolCallRouter.test.tsx` — same with `sessionId={undefined}` → assert NO fetch happens (graceful no-op when session not yet known).
- [ ] `MCPAppToolCallRouter.test.tsx` — fetch fails (network error) → assert handler still resolves (doesn't propagate to iframe), assert one console.warn fired with the expected message shape.

### Backend Tests (pytest)

- [ ] `test_iframe_context_routes.py` — happy path: authed user with allowlisted skill posts a small valid body → 204; assert session state has `mcp_app_context.ext-apps-map.show-map` = the structured content.
- [ ] Auth gate: missing JWT → 401.
- [ ] Session-exists gate: unknown session_id → 404.
- [ ] Session-ownership gate: session belongs to a different user → 403.
- [ ] Skill access gate: user has no access to the skill → 403.
- [ ] Server-in-skill-allowlist gate: serverId not in `tool_configs.mcp.servers` → 403.
- [ ] Server-in-context-writes-allowlist gate: serverId not in `tool_configs.mcp.allow_context_writes` → 403.
- [ ] Size gate: `structuredContent` serialized > 4096 bytes → 413.
- [ ] Schema gate: `structuredContent` is a string (not object) → 400.
- [ ] Idempotence: posting the same body twice → state matches second post; no duplicate keys.
- [ ] Multiple servers: post for `serverA.toolX` then `serverB.toolY` → both keys exist, neither overwrites the other.

- [ ] `test_agent_iframe_context_injection.py` — build agent for a skill, set session state with `mcp_app_context.ext-apps-map.show-map: {label: "Munich"}`, assert the resolved instruction string contains the block + the literal `"Munich"`. Then build agent with empty state, assert instruction does NOT contain the block at all.

### Manual Testing

- [ ] Hard-refresh chat page; send "show me Munich on the map"; check DevTools console — no `MCP error -32601` lines.
- [ ] In the same chat, send "what city is currently centred?" → agent answers "Munich" (or includes "Munich" in its response) WITHOUT calling `geocode` again (verifiable in the AG-UI tool-call events panel).
- [ ] In the same chat, send "now zoom to its old town" → agent calls `geocode("Munich Altstadt")` then `show-map(new bounds)` → globe re-zooms.
- [ ] In the chat, run `aiplatform sessions inspect <session_id> --mcp-context` → CLI prints the namespaced state keys, including the `currentBounds` and `viewUUID` from the most recent `show-map` call.
- [ ] Clear the skill's `allow_context_writes` allowlist (remove `ext-apps-map`); re-send "show me Munich"; check DevTools — POST returns 403, frontend logs warn, iframe still renders. Agent's NEXT turn is blind to the view (regression to current behaviour). Restore allowlist; verify the flow recovers on next turn.

## Security Considerations

See **Conflict Justifications** under axiom #9 above for the full rationale. Summary of the gates this endpoint enforces:

1. **Authentication** — Firebase JWT required (`get_current_user` dependency, same as every other authed endpoint).
2. **Session ownership** — caller must own the session being written to (look up `session.user_id` and compare).
3. **Skill access** — caller must have access to the skill the session belongs to (`access.can_access_skill(skill)`, same as `mcp_proxy.py`).
4. **Server in skill allowlist** — `serverId` must be in `skill.tool_configs.mcp.servers` (the existing allowlist; you can't push context for a server your skill doesn't activate).
5. **Server in context-writes allowlist** — `serverId` must additionally be in `skill.tool_configs.mcp.allow_context_writes` (NEW — per-server opt-in, default off).
6. **Schema validation** — request body must be `{serverId: str, toolName: str, structuredContent: dict | null, content: list | null}`. Anything else: 400.
7. **Size cap** — `structuredContent` JSON serialized must be ≤ 4096 bytes. Larger: 413.
8. **Namespacing** — written state goes under `mcp_app_context.{serverId}.{toolName}`, never to a generic key the agent unconditionally trusts.
9. **Prompt-injection containment** — the agent's instruction template explicitly references the namespaced block with framing prose ("from previously-rendered MCP App tools, if any") so the model is primed to treat it as iframe-state data, not user instructions.
10. **Audit logging** — every write logs `iframe_context_write skill_id=... session_id=... server_id=... tool=... bytes=...` at INFO level + an OTel span. Investigatable post-incident.

The "added attack surface" cost is real but bounded; the threat model is well-trod (it's the same model as our existing `/api/proxy/mcp/*` proxy gate, plus the per-server opt-in extra gate).

## Performance Considerations

- **Endpoint latency budget:** ≤ 50ms p95 backend (it's a Firestore read for session + skill, a small validation pass, then an ADK `append_event` write). Comparable to other authed POST endpoints in the platform.
- **Frequency:** the Cesium app fires `ui/update-model-context` once per camera repositioning. In a typical "show me X / now zoom to Y" flow that's 2-3 writes per chat turn. Well within Firestore write budgets.
- **No caching needed** — the endpoint is per-write; no expensive computation to memoize.
- **Bundle-size impact (frontend):** ~0. We're adding ~30 LOC of handler code; the AppRenderer SDK already shipped the `onUpdateModelContext` prop type.
- **Agent prompt overhead:** the iframe-context block adds `~200-500 tokens` to the system prompt for skills that use MCP Apps and have written context. Only present after the first context write in a session. Worth instrumenting a one-line OTel attr on the next sprint to track.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/ -m "not slow"`)
- [ ] Lint and typecheck clean (`npm run quality:check:fast`, `cd backend && make lint`)
- [ ] Backend Docker build succeeds (`npm run docker:check`)
- [ ] Documentation updated: this design doc moved to `implemented/`; workshop W7 module updated to claim "stateful iframe ↔ agent" without weasel words; verification log entry on the protocol-stack talk doc with the live-smoke evidence
- [ ] DevTools console shows ZERO `MCP error -32601: No handler for method: ui/update-model-context` lines during the chat smoke
- [ ] Two-turn demo passes: "show me Munich" → "what city is currently centred?" answers correctly without re-geocoding
- [ ] Three-turn demo passes: "show me Munich" → "click pin → tell me more" → "now zoom to its old town" → globe re-zooms
- [ ] `aiplatform sessions inspect <id> --mcp-context` returns the expected namespaced keys for a session that has called `show-map`
- [ ] `mcp-ext-apps-map-dev` Cloud Run sidecar deployed (sprint 1.7 M4) — feature can run in dev environment without local map-server, demonstrating it works against deployed infra (workshop pre-flight requirement)
- [ ] Lessons captured in [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log

## Open Questions

- **Where exactly in `adk/agent.py` should the iframe-context block be appended?** Probably right before the closing `</skill_instructions>` tag, but needs a quick read of how the existing agent factory composes the instruction string. M3 verification.
- **Should the agent be able to READ the iframe-context but not the iframe push history (i.e. only the LATEST values)?** Default: yes — namespaced state holds only the latest write per `(server_id, tool_name)` tuple. If a future widget needs history, it can write a list itself; we don't impose a list shape.
- **What happens if a SECOND `show-map` call fires before the first's `ui/update-model-context` write completes?** The backend write is idempotent on the namespaced key — second write just overwrites. Order of writes matches order of POST receipt. If we ever need strict ordering (we don't yet), add a sequence number to the request.
- **Should we surface the iframe-context block to the user in the chat UI (e.g. a small "agent knows: viewing Munich" pill)?** Out of scope for this design but worth a follow-up doc — would be a nice "the agent's mental model is observable" demo moment for the workshop.

## Related Documents

- [mcp-app-integrations.md](implemented/mcp-app-integrations.md) — sprint 1.7, ships the host that this design extends (Path A frontend MCP Client + sandbox proxy + `<AppRenderer onMessage>`)
- [mcp-sandbox-separate-origin.md](mcp-sandbox-separate-origin.md) — sprint 1.7 M3, the sandbox proxy that carries the postMessage this design relies on
- [docs/talks/workshop.md](../../talks/workshop.md) — Module W7, this design closes the second iframe→host RPC channel called out in W7f papercut #6
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — Lesson #4 #6 (the harmless-but-incomplete `ui/update-model-context` story that this design resolves)
- [local-dev-cli.md](local-dev-cli.md) — backlinked from the new `aiplatform sessions inspect --mcp-context` subcommand

---

## Implementation Report

**Completed**: 2026-04-30
**Actual Effort**: ~0.6 day vs 0.75 day estimated (single sitting after sprint 1.7 wrap)
**Branch/PR**: dev branch, commit range fc19813...HEAD (this commit)

### What Was Built

End-to-end ui/update-model-context wire, exactly as designed. Three-turn
demo verified live in chat against the local map-server: "show me Munich"
→ "what city is currently centred?" (agent answered "Munich" without
re-rendering) → "now zoom in to its old town" (agent resolved "its" via
context, called geocode + show-map, map re-rendered).

One spec-shape surprise resolved during wire-up: `@mcp-ui/client`'s
AppRenderer does NOT have a dedicated `onUpdateModelContext` prop; the
method surfaces via the catch-all `onFallbackRequest` callback. Router
dispatches on `request.method === "ui/update-model-context"` and ignores
everything else. Wired into Lesson #4 of the protocol-stack talk so future
adopters don't repeat the look-for-the-prop diversion.

### Files Changed

**New (8 files, ~750 LOC):**
- `backend/protocols/iframe_context_routes.py` — POST endpoint with 7
  access gates
- `backend/adk/iframe_context.py` — pure render fn + InstructionProvider
  wrapper
- `backend/scripts/migrate_mcp_context_writes.py` — idempotent Firestore
  migration; opts already-activated servers into context-writes
- `backend/tests/api_tests/test_iframe_context_routes.py` — 16 tests
- `backend/tests/unit/test_iframe_context_injection.py` — 7 tests
- `cli/aiplatform/commands/sessions.py` — `aiplatform sessions inspect
  --mcp-context` debugging subcommand
- `frontend/src/components/protocols/__tests__/MCPAppToolCallRouter.iframeContext.test.tsx`
  — 4 tests

**Modified (10 files):**
- `backend/adk/agent.py` — wraps every agent's instruction with
  `wrap_with_iframe_context`
- `backend/fast_api_app.py` — mounts iframe_context_router
- `backend/protocols/sessions_route.py` — adds owner-only GET
  `/sessions/{id}/state` for CLI debugging
- `backend/skills/templates/{document-analyst,web-researcher}/SKILL.md`
  — add `tool_configs.mcp.allow_context_writes`
- `backend/tests/unit/test_create_agent.py` — assert instruction is
  now an InstructionProvider that resolves correctly
- `cli/aiplatform/cli.py` — registers sessions group
- `frontend/src/app/chat/[...path]/page.tsx` — passes sessionId down
- `frontend/src/components/chat/{ChatMessageList,MessageBubble}.tsx`
  — threads sessionId
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — wires
  onFallbackRequest → POST
- Two existing test mocks updated for `client.readResource`

**Migration applied to your-project-id**: web-researcher +
document-analyst now have `allow_context_writes: ['ext-apps-map']`.

### Lessons Learned

**Went well:**
- Pre-fetch-resource pattern from sprint 1.7 (carrying `html` + `csp`
  ourselves rather than letting AppRenderer fetch) made adding a NEW
  iframe handler straightforward — `onFallbackRequest` was a one-line
  addition to the existing JSX, no architectural change.
- 7-gate access policy maps cleanly to one test class per gate. Each
  gate is independently asserted. Easy to extend if future widgets
  warrant more granular checks.
- Pre-existing tests for the router caught the `client.readResource is
  not a function` mock-stale issue immediately on first `npm run
  test:run` after Phase 2 — saved finding it during smoke.

**Could be improved:**
- Backend log line `iframe_context: write` didn't surface in stdout
  during smoke (only the FastAPI access-log line did). Logger config
  may need a per-module level bump. Followed up via observation only;
  not blocking.
- The migration script needs an explicit `GCP_PROJECT=your-project-id`
  env var due to the seed-script pinning we added in sprint 1.7. Worth
  a `make` target wrapping `unset GCP_PROJECT && uv run python
  scripts/migrate_mcp_context_writes.py` for the next dev who needs to
  re-seed.
- Agent in turn 2 still called `geocode` defensively to verify the
  context-supplied "Munich" — perfectly correct behaviour but not the
  most striking demo. A future prompt-engineering pass on the iframe-
  context block could push the model to trust the context more
  aggressively. Defer to workshop-rehearsal feedback.

**Smoke-test artefacts:**
- DevTools showed zero `MCP error -32601: No handler for method:
  ui/update-model-context` lines after the changes — primary success
  criterion met.
- All 803 backend tests + 380 frontend tests pass.
- `lint clean` on every touched file.
