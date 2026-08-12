# A2UI surface → agent context — bidirectional surface ↔ agent state

**Status**: Proposed
**Priority**: P1 (workshop W6 demo credibility — second-turn references to workspace state)
**Estimated**: 0.5–0.75 day (one focused half-day + a smoke pass — same scope as the MCP Apps version)
**Scope**: Fullstack (frontend wire-up + backend session-state injector + small test pass)
**Dependencies**: Sprint 2.9 [multi-surface-rendering](multi-surface-rendering.md) shipped 2026-05-18 (per-surface `MessageProcessor` + `SurfaceModel` in registry; `<A2uiSurface>` direct on v0.9 native API).
**Mirror of**: [mcp-app-update-model-context.md](../../v6.1.0/implemented/mcp-app-update-model-context.md) — same design, different protocol surface.
**Created**: 2026-05-18

---

## Problem Statement

After sprint 2.9 the demo-workspace skill renders the dashboard correctly: `createSurface` + `updateComponents` + `updateDataModel` flow agent → frontend, the workspace surface materialises, "refresh" mutates the data model in place. **But the agent has zero awareness of what's currently rendered or what the user did on the surface.** It's a one-way push.

This is the exact pattern we already fixed for MCP Apps in sprint 1.25 ([ui/update-model-context](../../v6.1.0/implemented/mcp-app-update-model-context.md) shipped end-to-end). The Cesium globe pushes view state back via `ui/update-model-context` → backend `POST /api/sessions/{id}/iframe-context` → `InstructionProvider` injects `mcp_app_context.{server}.{tool}` namespace into the next agent prompt. Result: "what city is currently centred?" answers "Munich" without re-geocoding.

A2UI v0.9 has the *protocol primitives* for the same pattern; we haven't wired them.

### The two channels A2UI v0.9 provides

| Channel | Spec primitive | What it carries | Use case | Status |
|---|---|---|---|---|
| Continuous data-model | `createSurface.sendDataModel: true` flag → `A2uiClientDataModel` attached to every A2A message back to the server | Full per-surface data model, every turn | "User is looking at a dashboard showing 87 active users at $5,678 revenue" → next turn references it | ❌ flag never set, no wire path |
| Discrete user actions | `A2uiClientAction { name, surfaceId, sourceComponentId, context }` emitted on button click / form submit / etc. via `surface.onAction` | One-shot action event with structured context | "User clicked the 'Approve' button on row 47" → next turn knows | ⚠️ partial — `A2UIRenderer.onAction` is wired, but `MessageBubble.onAction` translates it into a synthetic chat-message string, not structured context |

### Live evidence (2026-05-18 dashboard smoke)

Three-turn demo against `demo-workspace`:

| Turn | User says | What works today | What needs A2UI surface context |
|---|---|---|---|
| 1 | "show me the dashboard" | ✅ Workspace renders: 42 users, $1,234 revenue | (same) |
| 2 | "what's the current revenue?" | ❌ Agent has no idea — replies "I don't know" or guesses from earlier chat | ✅ Agent reads `a2ui_surface_context.workspace.dataModel.revenue` from prompt → answers "$1,234" |
| 3 | (with interactive button in future skill) "click Approve on row 47" | ❌ User clicks → synthetic chat turn "User clicked Approve" — agent loses the structured context (which row, what data was on it) | ✅ Agent reads `a2ui_surface_context.workspace.lastAction = {name: "approve", sourceComponentId: "row-47", context: {id: 47, status: "pending"}}` |

### Demo-narrative consequence

Workshop W6 narrative says "the workspace and the chat are separate surfaces — but they're in conversation through the protocol." Half-true today: the agent can push to the surface, but the surface stays opaque to the agent. The story breaks the moment a user references workspace state in chat — same way the MCP Apps story broke before sprint 1.25.

### Current state

- The frontend's `SurfaceRegistry` has `SurfaceModel.dataModel` live and reactive for every surface. We're not reading it on the outbound path.
- `A2UIRenderer.onAction` is wired but only forwards `name` + `context` upward — `surfaceId` and `sourceComponentId` are present but our `MessageBubble.onAction` callback discards them.
- The AG-UI `runAgent({forwardedProps})` channel is already used for `document_ids` / `resumed_session` — adding `a2ui_surface_state` is the same pattern.
- Backend: no `InstructionProvider` reads surface state from session state. The MCP Apps version is the template (`wrap_with_iframe_context` in `backend/protocols/mcp_app_context.py` or wherever it lives).

### Impact

- **Audience:** Workshop W6 attendees see a demo where the workspace is genuinely *part of the conversation*, or they don't.
- **Forks (AIPLA, a tutoring fork):** AIPLA's teacher dashboard needs the agent to reason about which student panel the teacher is looking at; a tutoring fork's student dashboard needs the agent to know which exercise the student is on. Without surface context, both forks rebuild this from scratch.
- **Spec compliance:** v0.9 has `sendDataModel: true` exactly for this. Not wiring it means we're shipping the easy half of A2UI again.

---

## Goals

**Primary Goal:** Plumb A2UI surface state (per-surface `dataModel`) AND user actions (`A2uiClientAction`) from the frontend into the next agent turn's prompt context. Mirror the MCP Apps `mcp_app_context.*` namespace pattern under `a2ui_surface_context.*`.

**Success Metrics:**
- Two-turn dashboard demo works: turn 1 "show me the dashboard" renders + context injected; turn 2 "what's the current revenue?" → agent answers from `a2ui_surface_context.workspace.dataModel.revenue` without re-running the tool.
- Three-turn interactive demo works (sketch — full proof needs an interactive demo skill later): turn 1 renders → turn 2 user clicks button → turn 3 agent reads `lastAction` and reasons about which button was clicked.
- DevTools network panel: every `runAgent` POST in a session with an active workspace surface includes `forwardedProps.a2ui_surface_state` shaped like `{[surfaceId]: {dataModel: {...}, lastAction?: {...}}}`.
- Code-size budget: ≤ 80 LOC frontend (collector hook + dispatcher wiring) + ≤ 120 LOC backend (parser + InstructionProvider) + ≤ 100 LOC tests.

**Non-Goals:**
- Generic "two-way state sync between any surface and any agent context" framework. Solve the v0.9 `dataModel` + `A2uiClientAction` shapes; nothing else.
- Frontend-side persistence of context across page reloads. Surface context is per-session, lives in ADK session state on the backend. Refresh = new session = clean context (correct behaviour).
- Agent → surface state pushes outside the existing A2UI message flow (`updateDataModel` already does this).
- Cross-surface context bundling that picks "the surface most relevant to this turn." Just attach all active surface states; let the agent decide.

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Context flows asynchronously on the next outbound turn; doesn't block any user-visible action. Neutral. |
| 2 | EARNED TRUST | +1 | Agent can answer "what's on screen" instead of guessing — demonstrably more trustworthy when surface state matters. |
| 3 | SKILLS, NOT FEATURES | +1 | Per-skill plumbing. Any skill using a routed A2UI surface benefits; no new user-facing concept. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing implications. |
| 5 | GRACEFUL DEGRADATION | +1 | If `forwardedProps` are missing or malformed, the agent simply doesn't get the context — surface still renders, agent stays blind (current state). |
| 6 | PROTOCOL OVER CUSTOM | +2 | This is the SPEC primitive — `sendDataModel` + `A2uiClientAction` are v0.9 wire-format. We're closing the second half of A2UI's contract, parallel to how 1.25 closed MCP Apps' second half. |
| 7 | API FIRST | +1 | Adds one well-typed endpoint (`POST /api/sessions/{id}/surface-action`) + one well-typed `forwardedProps` slot. Forks reuse both. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every surface-state injection logs a structured line + adds an OTel span (same pattern as MCP Apps `iframe-context`). |
| 9 | SECURE BY CONSTRUCTION | -1 | Adds a new write surface (action endpoint) AND a new read-into-prompt path (data model from `forwardedProps`). Both are agent prompt-injection vectors. Mitigations below. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend collector is ~30 LOC of "read SurfaceRegistry state, append to forwardedProps"; the spec carries the contract. |
| | **Net Score** | **+6** | Threshold: >= +4 ✓ |

**Conflict justifications:**

- **#9 SECURE BY CONSTRUCTION (-1):** Two new surfaces:
  - `forwardedProps.a2ui_surface_state` is user-controllable (the frontend assembles it from SurfaceModel, but a malicious frontend could put anything there). Mitigations: server-side schema validation, size cap (4 KB total, mirror MCP Apps), namespaced injection into prompt (`a2ui_surface_context.{surfaceId}.dataModel` not a raw splat).
  - `POST /api/sessions/{id}/surface-action` is a new write surface. Mitigations: Firebase auth + session-access gate + per-skill allowlist (`tool_configs.a2ui.allow_surface_context_writes: true` opt-in, default false) + schema + size cap. Same gate shape as `/api/sessions/{id}/iframe-context`.
  - Both inject content into the agent's prompt context. The namespacing prevents "trust everything in context" splat — agent prompt template references `a2ui_surface_context.workspace.*` explicitly. The frontend cannot poison arbitrary session keys.

Net: more attack surface than zero, but gated by the same axes that already gate AG-UI `forwardedProps` (proxy auth) and `/api/sessions/{id}/iframe-context` (session+skill auth + opt-in). The `-1` reflects "more surface than zero" honestly, not that the design is unsafe.

---

## Standards Compliance Check

- **A2UI v0.9 `createSurface.sendDataModel`:** spec-defined boolean flag. We set it from the toolset config (default false, opt-in per skill via `tool_configs.a2ui.send_data_model: true`).
- **A2UI v0.9 `A2uiClientAction`:** spec-defined message shape `{name, surfaceId, sourceComponentId, timestamp, context}`. We POST this verbatim to the backend (no transformation).
- **A2UI v0.9 `A2uiClientDataModel`:** spec-defined message shape `{version: "v0.9", surfaces: {[id]: dataModel}}`. We bundle our `forwardedProps.a2ui_surface_state` to match this shape so the design generalises trivially to true A2A transport later.
- **AG-UI `forwardedProps`:** documented extension slot for per-turn signals (memory: [AG-UI state is one turn behind](../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/gotcha_agui_state_one_turn_behind.md)). Surface state is exactly a per-turn signal.

---

## Design

### Overview

Two complementary push channels:

1. **Continuous data-model push** — every outbound `runAgent` POST that has at least one active surface attaches `forwardedProps.a2ui_surface_state = {[surfaceId]: {dataModel: ..., catalogId: ...}}`. Backend parses it, an `InstructionProvider` injects under namespace `a2ui_surface_context.{surfaceId}` for the next prompt.

2. **Discrete action push** — when `surface.onAction` fires, the frontend POSTs the structured event to `/api/sessions/{id}/surface-action`. Backend writes it under namespace `a2ui_surface_context.{surfaceId}.lastAction` in session state. Next agent turn reads it.

Two channels because they have different timing characteristics: the data model is "what's on screen RIGHT NOW" (always available, sent piggyback on the next turn), the action is "what just happened" (event-driven, can fire between turns, needs out-of-band write).

### Frontend Changes

**1. `useA2uiSurfaceStateSnapshot()` hook (new, in `frontend/src/providers/SurfaceRegistry.tsx` or a sibling file).**

Reads `SurfaceRegistry.entries`, returns `{[surfaceId]: {dataModel, catalogId}}` for every surface where `state.surface !== null`. Pure read; doesn't subscribe (we read on outbound, not on every render).

```ts
export function readA2uiSurfaceState(
  registry: SurfaceRegistryAPI,
): Record<string, { dataModel: unknown; catalogId: string }> {
  // Walk entries, snapshot dataModel.get('/') for each live surface.
  // Returns {} when no active surfaces — caller omits forwardedProps slot.
}
```

**2. `useSkillAgent.sendMessage` collects the snapshot before runAgent.** Patch ~10 LOC: read the snapshot via the registry, merge into `forwardedProps` under key `a2ui_surface_state`. Skip when the snapshot is empty (avoids a noisy empty-object on every turn).

**3. `A2UISurfaceMount` subscribes to `surface.onAction` and dispatches.** New ~30 LOC: on mount, register a listener on `state.surface.onAction`; on event, POST to `/api/sessions/{id}/surface-action` with `{surfaceId, action: {name, sourceComponentId, context}}`. Uses `fetchWithAuth` (per `feedback_fetchWithAuth_always`).

### Backend Changes

**1. `POST /api/sessions/{session_id}/surface-action` (new route, in `backend/routes/sessions.py` or a sibling).**

Mirror of `/api/sessions/{id}/iframe-context` from sprint 1.25. Seven gates:
1. Firebase auth (existing decorator).
2. Session exists + caller has access (mirror existing helper).
3. Session's skill exists.
4. Skill has `tool_configs.a2ui` (else 400).
5. Skill has `tool_configs.a2ui.allow_surface_context_writes: true` (opt-in, default false).
6. Request body matches `A2uiSurfaceActionPayload { surfaceId, action: { name, sourceComponentId?, context: dict } }` (Pydantic).
7. `context` JSON size ≤ 4 KB.

On success: write `session.state["a2ui_surface_context"][surface_id]["lastAction"] = {name, sourceComponentId, context, timestamp}` (namespaced). 204 No Content.

**2. `wrap_with_a2ui_surface_context` InstructionProvider (new, in `backend/protocols/a2ui_surface_context.py`).**

Mirror of the MCP Apps `wrap_with_iframe_context`. Reads `session.state.get("a2ui_surface_context", {})` AND from `agent_input.forwardedProps.get("a2ui_surface_state", {})`. Merges (forwardedProps wins for `dataModel`; session state wins for `lastAction`). Injects into the system prompt as:

```
## Current UI surface state

Each surface the user is looking at is keyed by `surfaceId`. The dashboard surface is named "workspace".

{json.dumps(a2ui_surface_context, indent=2)}

Reference these values when the user asks "what's on screen", "what did I just click", or by surface id. Do not assume values not present in this block.
```

Same prose framing as MCP Apps. Wired into the agent factory via the existing `InstructionProvider` chain.

**3. AG-UI translator reads `forwardedProps.a2ui_surface_state`.** ~10 LOC in the translator: pluck the slot, attach to the InvocationContext so the InstructionProvider can read it. Pattern matches the existing `document_ids` handling.

### API Changes

```
POST /api/sessions/{session_id}/surface-action
Authorization: Bearer <firebase-id-token>
Content-Type: application/json

{
  "surfaceId": "workspace",
  "action": {
    "name": "approve",
    "sourceComponentId": "row-47",
    "context": {"id": 47, "status": "pending"}
  }
}

→ 204 No Content                                          (happy path)
→ 401 Unauthorized                                        (no/invalid token)
→ 403 Forbidden                                           (no session access OR allow_surface_context_writes=false)
→ 404 Not Found                                           (session/skill missing)
→ 413 Payload Too Large                                   (context > 4 KB)
→ 422 Unprocessable Entity                                (schema violation)
```

AG-UI `forwardedProps` slot:

```
forwardedProps.a2ui_surface_state = {
  "workspace": {
    "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json",
    "dataModel": { /* whatever the SurfaceModel currently holds at "/" */ }
  },
  // ... other active surfaces
}
```

### Architecture diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ FRONTEND                                                        │
│                                                                 │
│  SurfaceRegistry ──► readA2uiSurfaceState() ──► forwardedProps  │
│        │                                              │         │
│        └──► surface.onAction ──► POST /surface-action │         │
│                                                       ▼         │
└───────────────────────────────────────────────────────┼─────────┘
                                                        │
┌───────────────────────────────────────────────────────┼─────────┐
│ BACKEND                                               ▼         │
│                                                                 │
│  /api/sessions/{id}/surface-action  ──► session.state           │
│       (auth + skill opt-in + size cap)         │                │
│                                                ▼                │
│  AG-UI translator ──► InvocationContext ──► InstructionProvider │
│       (forwardedProps.a2ui_surface_state)         │             │
│                                                   ▼             │
│                                              agent prompt:      │
│                                              "## Current UI     │
│                                               surface state…"   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1 — Backend endpoint + InstructionProvider (~0.3 day)

- `backend/protocols/a2ui_surface_context.py`: `wrap_with_a2ui_surface_context` InstructionProvider + a `read_surface_state(invocation_ctx)` helper. ~80 LOC.
- `backend/routes/sessions.py` (or wherever the iframe-context endpoint lives): mirror the 7-gate `POST /api/sessions/{id}/surface-action`. ~80 LOC.
- AG-UI translator addition: ~10 LOC to pluck `forwardedProps.a2ui_surface_state`.
- Tests: gate-by-gate parametrised pytest, mirror `test_iframe_context.py`. ~120 LOC.

### Phase 2 — Frontend collector + dispatcher (~0.2 day)

- `readA2uiSurfaceState(registry)` helper in `frontend/src/providers/SurfaceRegistry.tsx`. ~20 LOC.
- `useSkillAgent.sendMessage` patch to include the snapshot in `forwardedProps`. ~10 LOC.
- `A2UISurfaceMount.tsx` subscribes to `state.surface.onAction` and POSTs via `fetchWithAuth`. ~30 LOC.
- Vitest cases: snapshot is sent on outbound; empty-state turn omits the slot; action POST fires with the right shape; auth failures don't crash. ~80 LOC.

### Phase 3 — Skill seed updates + smoke (~0.1–0.2 day)

- `backend/db/local_fixture.py` + template `SKILL.md`: opt the `demo-workspace` skill into `allow_surface_context_writes: true` and add a second triggers block ("what's the current revenue?" → "Read from a2ui_surface_context.workspace.dataModel.revenue").
- Manual smoke via chrome-devtools MCP: turn 1 renders dashboard → turn 2 "what's the current revenue?" answers from context without invoking the tool.
- Add a row to `docs/talks/ai-ui-protocol-stack.md` audit table marking the A2UI ↔ agent context loop as ✅ instead of one-way push.

---

## Migration & Rollout

- **Default-off:** `tool_configs.a2ui.allow_surface_context_writes` defaults to `false`. Existing skills don't see the new endpoint until they opt in.
- **Backwards compatible:** skills without the flag continue working as today (data-model push from `forwardedProps` still flows but the agent prompt simply doesn't reference an absent namespace).
- **No DB migration:** session state is already a freeform dict.
- **Workshop demo path:** update `demo-workspace` seed in `local_fixture.py` + template; the public template fork inherits the wiring automatically.

---

## Testing strategy

### Frontend tests (Vitest + React Testing Library)

- `useSkillAgent.sendMessage` attaches `forwardedProps.a2ui_surface_state` when SurfaceRegistry has live surfaces, omits when empty.
- `A2UISurfaceMount` dispatches `onAction` to `/api/sessions/{id}/surface-action` with the right body shape.
- A POST failure logs warn + doesn't crash the bubble.
- `readA2uiSurfaceState` handles empty / partial / disposed surfaces gracefully.

### Backend tests (pytest)

- 7-gate matrix on `POST /surface-action`: unauthenticated, wrong-session, missing-skill, no-a2ui-config, allow-flag-off, oversized, malformed.
- InstructionProvider correctly merges `forwardedProps.a2ui_surface_state` + `session.state["a2ui_surface_context"]` and produces the prompt block with the right namespace.
- Empty surface state → no prompt block injected (agent doesn't get a confusing empty section).
- Smoke: full round-trip from `forwardedProps` to agent prompt via in-process ADK Runner.

### Manual testing (chrome-devtools MCP)

- Turn 1: "show me the dashboard" → workspace renders → `Validated call send_a2ui_json_to_client` in backend log.
- Turn 2: "what's the current revenue?" → agent answers "$1,234 in revenue" without invoking the tool. Verifiable via TOOL_CALL_START count in stream response (should be 0 on turn 2).
- Refresh test: turn 3 "refresh" → updateDataModel mutates → turn 4 "and now?" → agent reads NEW revenue value.

---

## Security considerations

- **Prompt-injection vector via dataModel:** the frontend assembles `forwardedProps.a2ui_surface_state` from SurfaceModel.dataModel. A compromised frontend could put anything there. Mitigations: 4 KB total cap, namespaced injection (`a2ui_surface_context.{surfaceId}.dataModel`, NOT a raw splat), prompt prose explicitly tells the agent "do not assume values not present in this block".
- **Prompt-injection vector via action context:** POST endpoint accepts `context: dict`. Same mitigations (4 KB cap, namespaced injection). Plus per-skill opt-in flag (default off).
- **No new auth path:** uses existing Firebase auth + session-access gate + skill-allowlist axes, same as MCP Apps `iframe-context`.
- **Logging + observability:** every POST adds a `surface_action_write` OTel span; every InstructionProvider injection logs `a2ui_surface_context.applied surface_count={n} data_kb={x}`.

---

## Performance considerations

- **Forward-on-every-turn cost:** ~1–4 KB of JSON in `forwardedProps` on the outbound POST. Negligible network impact; the prompt is the more expensive surface. Cap at 4 KB enforces a known upper bound on token cost.
- **Read on send, not on render:** `readA2uiSurfaceState` runs once per outbound (in `sendMessage`), not on every React render. No re-render cost.
- **No backend write amplification:** the action POST writes one row to session.state per fire, same shape as iframe-context.

---

## Open questions

1. **Should we add a "stale data" indicator to the prompt?** If a user looked at the dashboard 10 turns ago and then asks "what's the current revenue?", the agent should answer from the current dataModel — but if the surface was *cleared* (session change), the namespace is gone and the agent correctly says "I don't know." Edge case: surface still mounted but the user is now looking at a different document. Probably acceptable to just attach the data model; staleness isn't this design's job.

2. **A2A path:** if forks later expose the surface to an A2A counterpart agent (not just the host chat agent), do we adopt the canonical `A2uiClientDataModel` wire shape end-to-end? The current design already matches that shape (`{[surfaceId]: {dataModel, catalogId}}`) so the generalisation is one wire-format rename away.

3. **`onAction` → synthetic chat vs. structured context:** today `A2UIRenderer.onAction` triggers a synthetic chat message. Should action POST REPLACE the chat-message path, or ADD to it? Recommendation: ADD — both behaviours are useful (chat message for users who want to see what they did; structured context for the agent). Confirm with first interactive demo skill.

---

## Related Documents

- [mcp-app-update-model-context.md](../../v6.1.0/implemented/mcp-app-update-model-context.md) — sibling design, MCP Apps version, shipped 2026-04-30. This doc mirrors its structure and gates.
- [multi-surface-rendering.md](multi-surface-rendering.md) — sprint 2.9, the foundation. Provides `SurfaceRegistry` + `SurfaceModel` that this design reads from.
- [ai-ui-protocol-stack.md](../../../talks/ai-ui-protocol-stack.md) — "Discipline" section flags surface→agent context as a missing audit row; this design closes the audit row.
- [a2ui-tool-delivery.md](../../v6.1.0/implemented/a2ui-tool-delivery.md) — wire-format end of the agent → surface direction. Together with this doc, completes the bidirectional contract.
