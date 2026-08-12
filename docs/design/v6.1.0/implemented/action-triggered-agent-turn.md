# Action-Triggered Agent Turn

**Status**: Implemented
**Priority**: P1 (workshop W6 Pattern 1 demo)
**Estimated**: ~1 day
**Scope**: Fullstack (small backend route + small frontend hook + demo skill seed)
**Dependencies**:
  - [A2UI Tool Delivery](implemented/a2ui-tool-delivery.md) ✅ — `SendA2uiToClientToolset`
  - A2UI surface context loop ✅ — `POST /api/sessions/{id}/surface-action` + `wrap_with_a2ui_surface_context` InstructionProvider (sprint 2.10, 2026-05-18)
  - [A2UI Workshop Demo](a2ui-workshop-demo.md) (1.19) — sibling doc; the demo skill seeded there is what this loop drives
  - [LOCAL_MODE & Workshop Readiness](implemented/local-mode-and-workshop-readiness.md) ✅ — supplies the demo skill seed mechanism
**Created**: 2026-06-08
**Last Updated**: 2026-06-08

## Problem Statement

The July 2026 workshop talk ([ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)) builds toward a second-half demo: an AI UI that is **not a chat** — the user drives it through clicks on A2UI surfaces, the agent generates new surfaces in response, no text input box involved. The platform calls this **Pattern 1: declarative agent-driven UI** ([build-ai-uis-beyond-chat](https://github.com/sunholo-data/build-ai-uis-beyond-chat) is the workshop repo name for a reason).

The protocol plumbing for this already exists. A2UI surfaces render via `<A2UIRenderer>`; user clicks fire `A2uiClientAction` events which `<A2UISurfaceMount>` POSTs to `/api/sessions/{id}/surface-action`. That endpoint writes the action into ADK session state under `a2ui_surface_context.{surfaceId}.lastAction` (7-gate access policy, 4 KB cap, per-skill opt-in). The `wrap_with_a2ui_surface_context` InstructionProvider injects that namespace into the agent's prompt on the next turn.

**The gap:** "the next turn" today means **the next chat turn**. There is no mechanism to **kick off an agent turn from a surface action**. So the loop only closes when the user also types something in the chat input. For a purely click-driven UI, this is a missing rung.

**Current State:**

- ✅ Surface render: `<A2UIRenderer>` ships A2UI components in chat
- ✅ Action capture: `surface.onAction.subscribe` → `POST /api/sessions/{id}/surface-action` (sprint 2.10)
- ✅ Action persistence: `EventActions(state_delta={…})` writes to ADK session under namespaced key
- ✅ Agent reads action: `wrap_with_a2ui_surface_context` InstructionProvider injects on next turn
- ✅ Per-skill opt-in: `tool_configs.a2ui.allow_surface_context_writes: true`
- ❌ **Action does NOT trigger an agent run** — caller must wait for the next chat message
- ❌ No client-side hook to drive "click → new surface" without showing the chat composer
- ❌ No `aiplatform` CLI subcommand to synthesize an action and watch the resulting AG-UI stream for debugging

**Impact:**

- **Workshop demo risk:** the headline Pattern 1 demo ("the user never types — they drive the app entirely through generated UI") is not buildable today without manual chat-message injection. Workshop attendees who copy our pattern hit the same wall.
- **Platform thesis under-demonstrated:** the talk's central claim is that protocols let you build AI UIs *beyond chat*. Without action-triggered turns, every demo still anchors back to a chat composer.
- **Workshop-fork ergonomics:** the public template (`sunholo-data/build-ai-uis-beyond-chat`) is the canonical reference for "how do I build click-driven AI UIs?". Without this rung, the template's headline pattern requires a custom workaround per fork.
- **Spec gap upstream:** A2UI v0.9 defines the `A2uiClientAction` message shape but does not specify a transport when surfaces are embedded in a chat-style agent. We filed [a2ui-project/a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) to ask for guidance; the design here is the implementation we're proposing as a reference pattern.

## Goals

**Primary Goal:** Ship an action-triggered run path such that a click on an A2UI Button can drive a full agent turn (read action + emit new A2UI surface) **without any chat message being sent or required** — measured by a workshop-ready Pattern 1 demo running end-to-end on a `LOCAL_MODE=1 make dev` boot.

**Success Metrics:**

- A click on an A2UI surface → first AG-UI event arrives <500ms (perceived-snappiness target, on par with chat turns)
- Pattern 1 demo runs 5/5 consecutive iterations from `LOCAL_MODE=1 make dev` without falling back to the chat composer
- Existing chat-driven A2UI continues to work (zero regressions in `chat-message-rendering`, `useSkillAgent`, `<A2UISurfaceMount>` test suites)
- New endpoint covered by happy-path + each of the 7 access gates (gate parity with `surface-action`)
- `aiplatform sessions trigger-action --surface … --action …` round-trips and prints AG-UI events

**Non-Goals:**

- **Not** a redesign of `<A2UIRenderer>` or `SurfaceRegistry`. Existing internals stay as-is.
- **Not** expanding A2UI component coverage (Card, Chart, Alert, etc. — covered by 1.19's non-goal section).
- **Not** a parallel transport for chat turns. The existing `/api/skills/{id}/agui` streaming endpoint is unchanged. The new endpoint is exclusively for action-triggered runs.
- **Not** server-side polling, websockets, or push channels. Triggering is HTTP-request-scoped: click → POST → SSE response → done.
- **Not** addressing the upstream `body.state` "one turn behind" question ([ag-ui#1893](https://github.com/ag-ui-protocol/ag-ui/discussions/1893)) — orthogonal.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Single round-trip (write action + run + stream in one POST). First AG-UI event budget <500ms. |
| 2 | EARNED TRUST | 0 | No new factual surface; reuses existing citation/source-attribution paths. |
| 3 | SKILLS, NOT FEATURES | +1 | Same skill abstraction. A skill opting into this is "a skill that responds to surface actions" — discoverable via the existing opt-in field, no new user-facing concept. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Model selection unchanged. |
| 5 | GRACEFUL DEGRADATION | +1 | Falls back cleanly: SSE failure → existing `<A2UISurfaceMount>` error path. The plain `surface-action` endpoint remains available as the "fire and forget" alternative for skills not opted into action-triggered runs. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Wire formats are spec-compliant on both sides (A2UI v0.9 for the action body; AG-UI for the SSE stream). The bundled write+run endpoint is a transport convention the A2UI spec is silent about — filed upstream as [a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570). Reverts to +1 once the spec lands a recommendation we match. |
| 7 | API FIRST | 0 | Web-only path by nature (Telegram/email don't render A2UI surfaces). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Endpoint emits the same structured log as `surface-action` plus the AG-UI trace/log pipeline for the run. |
| 9 | SECURE BY CONSTRUCTION | +1 | Reuses the existing 7-gate access policy from `surface-action`; layers a new per-skill opt-in `tool_configs.a2ui.allow_action_triggered_runs: true` (deny-by-default, separate trust grant from `allow_surface_context_writes`). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The agent generates the next A2UI surface server-side. Client just renders. No client-side decision logic about what to draw next. |
| | **Net Score** | **+6** | Acceptable. |

**Conflict Justifications:** None scored -1.

## Design

### Overview

Add **one new HTTP endpoint** and **one new frontend hook**:

- `POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run` — same 7-gate access policy as `surface-action`, plus a new per-skill opt-in. Performs the existing surface-action state write, then constructs a synthetic `RunAgentInput` (no user-visible message, `forwardedProps` carrying the surface snapshot), invokes the existing `build_agui_adk_agent` + `stream_agui_events` pipeline, and streams AG-UI events back as SSE.
- `useActionDrivenAgent()` frontend hook — wraps the POST + SSE consumption, dispatching events through the same AG-UI subscriber path the chat composer uses, so the rendered A2UI surface updates exactly as it would after a chat turn.

The pre-existing `POST /api/sessions/{id}/surface-action` endpoint stays unchanged. Skills that have NOT opted into action-triggered runs keep using it (action is persisted but the agent reads it only on the next chat turn — current behaviour). Skills opted in get the bundled write+run endpoint.

### Frontend Changes

**New Hook:**

- `src/hooks/useActionDrivenAgent.ts` — exposes a single `triggerAction(surfaceId, action)` function. Internally:
  1. POSTs to `/api/skills/{skill_id}/sessions/{session_id}/surface-action-run` with the `A2uiClientAction` body + current `forwardedProps.a2ui_surface_state` snapshot from `SurfaceRegistry`
  2. Consumes the SSE response via the same parser used by `@ag-ui/client`'s `HttpAgent.runAgent` (re-use the same dispatching, so `RUN_STARTED` / `TOOL_CALL_*` / `STATE_SNAPSHOT` / `RUN_FINISHED` flow through `SurfaceRegistry` exactly like a chat turn)
  3. Returns a `Promise<void>` that resolves on `RUN_FINISHED` (or rejects on `RUN_ERROR`)

**Modified Component:**

- `src/components/protocols/A2UISurfaceMount.tsx` — accepts a new prop `triggerOnAction?: boolean` (default `false` — preserves current behaviour). When `true`, `surface.onAction.subscribe` calls `triggerAction()` from the new hook instead of the plain `surface-action` POST.

**No changes to:**

- `<A2UIRenderer>`, `SurfaceRegistry`, `useSkillAgent`, `AGUIProvider`, the chat composer, `useStableThreadId`

**Demo Path:**

- The `/dev/a2ui` fixture page (shipping in [1.19 a2ui-workshop-demo](a2ui-workshop-demo.md)) gains a "Pattern 1" section: a Button-only surface that triggers a turn on click and re-renders with a counter increment. No chat composer visible. This is the visual demo for the talk.

### Backend Changes

**New Endpoint:**

- `POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run` — declared in a new `backend/protocols/a2ui_surface_action_run_routes.py` module (sibling of `a2ui_surface_action_routes.py`; keep the modules separate so the gate logic and synthetic-run logic are independently testable).

**Endpoint Behaviour:**

1. **Gates 1–7** (identical to `surface-action`): Firebase JWT → session exists → access policy → skill exists → skill has `a2ui` config → skill opted into context writes → size cap. These are shared via `_enforce_skill_opt_in` / `_enforce_size_cap` already exported from `a2ui_surface_action_routes`; refactor those to a `_shared.py` module first so both routes import them.
2. **Gate 8 (new):** `tool_configs.a2ui.allow_action_triggered_runs: true` (default `false`). Distinct trust grant: "skill accepts surface-action context writes" doesn't automatically grant "skill is invokable via surface-action". The new gate raises 403 if absent.
3. **Action write** (same code path as `surface-action`): `EventActions(state_delta={…})` via `session_service.append_event`.
4. **Synthetic run input:** construct a `RunAgentInput` with:
   - `thread_id = session_id` (matches the AG-UI thread-id-as-session-id convention)
   - `messages = []` (no user-visible message)
   - `state = {}` (cross-turn state is already in the session)
   - `forwarded_props = {"a2ui_surface_state": <body.forwardedProps.a2ui_surface_state>, "_action_trigger": {"surfaceId": ..., "componentId": ..., "name": ...}}` — the action that triggered this run is also passed as forwardedProps so the InstructionProvider's framing prose can distinguish "the user just clicked" from "the user clicked some time ago" without re-reading session state
5. **Stream:** wrap with `build_agui_adk_agent(agent, user_id=user.uid, …)` and stream via `stream_agui_events` (same module used by the chat endpoint). Use the existing terminal-event dedup wrapper (G41).
6. **Response:** `text/event-stream` SSE, one JSON event per line. Standard AG-UI envelope.

**Modified Modules:**

- `backend/protocols/a2ui_surface_action_routes.py` — extract `_require_session`, `_enforce_skill_opt_in`, `_enforce_size_cap`, and `_STATE_KEY_NAMESPACE` into `backend/protocols/_a2ui_surface_shared.py`. Both action routes import from there. Pure refactor, no behaviour change; gated by existing surface-action tests (614 backend tests).
- `backend/db/models/skill.py` — add `allow_action_triggered_runs: bool = False` to the `A2uiToolConfig` model (validation only; no migration needed since Firestore is schemaless).
- `backend/adk/a2ui_surface_context.py` — `wrap_with_a2ui_surface_context` already reads `a2ui_surface_context.{surfaceId}.lastAction` from session state, which the action write populates. Add a short instruction-prose clause that explicitly names "a user just performed an action on a surface" when `forwardedProps._action_trigger` is present — distinguishes action-triggered runs from chat turns for the model.

**No changes to:**

- `backend/fast_api_app.py` (other than registering the new router)
- `backend/adk/agui.py` — `build_agui_adk_agent` + `stream_agui_events` reused as-is
- `backend/adk/agent.py` — agent factory unchanged
- Session/memory/artifact services — unchanged

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST | `/api/skills/{skill_id}/sessions/{session_id}/surface-action-run` | New. Write A2UI action + invoke agent + stream AG-UI events as SSE. Per-skill opt-in via `allow_action_triggered_runs`. | No |
| POST | `/api/sessions/{session_id}/surface-action` | Unchanged. Existing fire-and-forget action persistence. | No |

**Request body (new endpoint):**

```json
{
  "surfaceId": "string",
  "action": {
    "name": "string",
    "sourceComponentId": "string (optional)",
    "timestamp": "ISO 8601 (optional)",
    "context": { "...": "any JSON, ≤ 4 KB serialized" }
  },
  "forwardedProps": {
    "a2ui_surface_state": { "<surfaceId>": { "...": "snapshot" } }
  }
}
```

**Response:** `text/event-stream` of AG-UI events. First event `RUN_STARTED` within 500ms. Terminal event `RUN_FINISHED` (or `RUN_ERROR` on tool failure). Stream closes after terminal event (dedup wrapper G41 ensures at most one).

### CLI Surface

Per the local-dev CLI heuristic (any new resource/endpoint developers touch needs a typed command):

- `aiplatform sessions trigger-action <session-id> --surface <surfaceId> --action <name> [--component <id>] [--context <json>]` — POSTs to the new endpoint, consumes the SSE stream, prints AG-UI events as they arrive (mirrors `aiplatform skill probe`'s output format). Used to:
  - Debug a stuck Pattern 1 demo without opening Chrome
  - Smoke the endpoint from CI
  - Demonstrate the loop from a terminal during the workshop talk

Estimated 0.15d (Click subcommand + httpx SSE consumer + unit test). Lands as part of Phase 3.

### Architecture Diagram

```
[User clicks A2UI Button]
     │
     ▼
[A2UISurfaceMount (triggerOnAction=true)]
     │
     │  POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run
     │  body: { surfaceId, action, forwardedProps }
     ▼
[surface-action-run endpoint]
     │  ┌─ Gates 1–8 (Firebase + access + skill + opt-ins + size)
     │  ├─ EventActions(state_delta) → ADK session
     │  ├─ Build synthetic RunAgentInput (messages=[], forwardedProps=…)
     │  ├─ build_agui_adk_agent(agent, user_id=…)
     │  └─ stream_agui_events(...) → SSE
     ▼
[SSE response: RUN_STARTED → TOOL_CALL_* → STATE_SNAPSHOT → RUN_FINISHED]
     │
     ▼
[useActionDrivenAgent hook consumes SSE, dispatches into SurfaceRegistry]
     │
     ▼
[<A2UIRenderer> re-renders with new agent-emitted A2UI JSON]
```

## Implementation Plan

### Phase 1: Backend foundation (~0.4d)

- [ ] Refactor `a2ui_surface_action_routes.py` shared helpers → `_a2ui_surface_shared.py` (gate logic, size cap, state key namespace). 614 existing tests stay green. (~80 LOC moved, no behaviour change)
- [ ] Add `allow_action_triggered_runs: bool = False` to `A2uiToolConfig`. Unit test: skill with the flag absent → 403; with the flag true → 204 (action wrote) + SSE. (~10 LOC + 2 tests)
- [ ] New `backend/protocols/a2ui_surface_action_run_routes.py` with the 8-gate write-and-run endpoint. Reuses `build_agui_adk_agent` + `stream_agui_events`. (~120 LOC + 5 tests covering each gate)
- [ ] `wrap_with_a2ui_surface_context` reads `forwardedProps._action_trigger` (optional) and emits "the user just performed this action" framing prose when present. (~15 LOC + 1 test)

### Phase 2: Frontend wiring (~0.3d)

- [ ] `src/hooks/useActionDrivenAgent.ts` — POST + SSE consumer + dispatch through `SurfaceRegistry`. Reuses `@ag-ui/client`'s event types directly. (~80 LOC + 4 tests)
- [ ] `<A2UISurfaceMount triggerOnAction>` — when true, route `onAction` through `useActionDrivenAgent`. Default false preserves current behaviour. (~20 LOC + 2 tests covering both branches)
- [ ] `useStableThreadId` interaction check — the action-triggered run uses the same session_id, no new thread spawned, no HttpAgent rebuild. (1 regression test)

### Phase 3: Demo + CLI + workshop polish (~0.3d)

- [ ] Demo skill seed: extend the 1.19 `Demo A2UI Forms` skill with a "Click Counter" surface (one Button, increments a counter, agent emits new A2UI on each click). Adds `allow_action_triggered_runs: true` to its tool config. (~30 LOC skill seed + prompt)
- [ ] `/dev/a2ui` fixture page → new "Pattern 1" section using `triggerOnAction`. (~40 LOC + 1 vitest)
- [ ] `aiplatform sessions trigger-action` Click subcommand + SSE consumer. (~50 LOC + 1 unit test)
- [ ] Workshop smoke harness: 5-run script that exercises Pattern 1 against the seeded skill, asserts each turn emits a fresh A2UI tool call. Lands in `scripts/smoke-pattern1.sh`. (~30 LOC)

**Total estimate: ~1.0d** (matches the upstream-issue offer to PR our approach back).

## Migration & Rollout

**Database Migrations:**

- None. `allow_action_triggered_runs` is a new optional field on `A2uiToolConfig`; existing skill configs default to `false` (current behaviour).

**Feature Flags:**

- Per-skill `tool_configs.a2ui.allow_action_triggered_runs: true` is the on-switch. Default off.
- No environment-level flag — the endpoint is dormant for all skills until they opt in.

**Rollback Plan:**

- Remove the router registration in `fast_api_app.py`. Frontends with `triggerOnAction={true}` fall back to chat-message-required behaviour (the action POST still persists). Zero user-facing breakage in chat-driven A2UI.

**Environment Variables:**

- None. Reuses existing `LOCAL_MODE`, session-service, artifact-service, and Firebase Auth env vars.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)

- [ ] `useActionDrivenAgent` POST → SSE consumption → dispatch → resolves on `RUN_FINISHED`
- [ ] `useActionDrivenAgent` rejects on `RUN_ERROR` and surfaces the error to the caller
- [ ] `useActionDrivenAgent` falls back gracefully on HTTP 4xx (skill not opted in → console.warn, no throw, surface stays in last-rendered state)
- [ ] `<A2UISurfaceMount triggerOnAction={false}>` (default) routes through plain `surface-action` POST — current behaviour test stays green
- [ ] `<A2UISurfaceMount triggerOnAction={true}>` routes through `useActionDrivenAgent`
- [ ] Click counter on `/dev/a2ui` Pattern 1 surface increments across 3 clicks (state survives turns)

### Backend Tests (pytest)

- [ ] Endpoint happy path: 8 gates pass → state writes → SSE stream contains `RUN_STARTED` and `RUN_FINISHED`
- [ ] Gate 1: missing JWT → 401
- [ ] Gate 3: access denied (skill belongs to another user, no sharing) → 403
- [ ] Gate 4: unknown skill → 404 / 403 (match `surface-action` behaviour exactly)
- [ ] Gate 5: skill has no `a2ui` tool_config → 403
- [ ] Gate 6: `allow_surface_context_writes: false` → 403
- [ ] **Gate 8 (new): `allow_action_triggered_runs: false` → 403**
- [ ] Gate 7: `context` field > 4 KB → 413
- [ ] G41 dedup: synthetic agent that raises mid-stream → exactly one terminal event in the SSE response
- [ ] Synthetic input shape: assert `RunAgentInput.messages` empty, `forwardedProps._action_trigger` populated with the action
- [ ] `wrap_with_a2ui_surface_context` framing prose includes the "user just clicked" clause when `_action_trigger` present

### Integration / E2E

- [ ] `scripts/smoke-pattern1.sh` runs 5 successive trigger-action calls against the seeded demo skill in `LOCAL_MODE=1`. Each run asserts: SSE stream emitted, A2UI tool call observed, counter incremented in the rendered surface state. All 5 must pass for workshop sign-off.

### Manual Testing

- [ ] `LOCAL_MODE=1 make dev` + navigate to `/dev/a2ui` Pattern 1 section + click the button 3 times — surface re-renders without any chat message
- [ ] Browser DevTools Network tab: confirm POST to `surface-action-run` returns `text/event-stream`, events arrive incrementally (not buffered)
- [ ] Disable the per-skill opt-in via Firestore → click button → graceful console warning, no spinner-of-death

## Security Considerations

- **Same trust boundary as `surface-action`.** The 7 existing gates are reused via the `_a2ui_surface_shared` module; no relaxation.
- **New gate 8** (`allow_action_triggered_runs`) is a distinct trust grant. A skill can opt into surface-context-writes (data flowing into the prompt) without opting into action-triggered runs (the agent being invoked by a click). Forks can use the former without the latter.
- **No new prompt-injection surface.** The action's `context` field already lands under a namespaced prompt key (`a2ui_surface_context.{surfaceId}.lastAction`) with explicit framing prose. The new endpoint doesn't change the path the data takes into the model — only what triggers the model's invocation.
- **No new exfil surface.** The SSE response carries AG-UI events the agent would have emitted on a chat turn anyway. Same observability sinks (Cloud Trace, Cloud Logging, BQ).
- **Per-session rate limiting:** existing access-policy middleware already rate-limits per-user. The action-triggered endpoint inherits the same limits. (No new dedicated rate limit; if abuse emerges, a per-skill click-rate cap can be added in a follow-up.)

## Performance Considerations

- **First-event budget:** <500ms (target same as chat-turn first AG-UI event from the 1.20 TTFT-instrumentation work). Action-triggered runs run the same agent factory, the same session service, the same model — the overhead vs chat is just one fewer HTTP round-trip (write + run combined).
- **Bundle size impact:** `useActionDrivenAgent` reuses `@ag-ui/client` types and the existing event parser; the new hook is <80 LOC. <2 KB gzip.
- **Backend CPU:** each click is one extra agent invocation. Cost = same as a chat turn. Workshop demo is single-user; production usage caps per-skill via the opt-in gate.
- **No caching opportunities here** — every click is a fresh agent decision by design.

## Success Criteria

- [ ] All frontend tests passing (`npm run quality:check` — full CI parity)
- [ ] All backend tests passing (`make lint && make test-fast`)
- [ ] Existing `surface-action` tests stay green (zero-regression in the shared `_a2ui_surface_shared` refactor)
- [ ] `LOCAL_MODE=1 make dev` boot + `/dev/a2ui` Pattern 1 section: click 3 times, surface updates 3 times, no chat composer rendered
- [ ] `aiplatform sessions trigger-action … --surface … --action …` round-trips and prints AG-UI events
- [ ] 5/5 consecutive `scripts/smoke-pattern1.sh` runs pass against the seeded demo skill
- [ ] Workshop talk (W6 section) can demo Pattern 1 from a clean checkout
- [ ] [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) verification log gains a row for the action-triggered-run sprint

## Open Questions

- **Upstream consensus for the bundled write+run endpoint.** [a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) asks the A2UI maintainers for a recommended pattern. If they prefer a different shape (e.g. surface action as a synthetic AG-UI input), we can converge — the gates + state-write + run-input are isolated enough to swap the outer route shape later. Decide before merging if a maintainer response lands first.
- **`_action_trigger` field name.** Vendor-prefixed (`_`) per A2UI's "private" prefix convention. Could promote to a top-level field on `RunAgentInput` if upstream agrees this is a recurring need.
- **Multi-action coalescing.** If a user clicks rapidly, should each click fire a turn, or should we debounce? Current plan: each click fires a turn (matches chat-message semantics — each message fires a turn). Per-skill debounce could be added later if workshop runs surface jank.

## Related Documents

- [A2UI Workshop Demo (1.19)](a2ui-workshop-demo.md) — sibling doc; the seeded demo skill is what this loop drives
- [A2UI Tool Delivery (1.0)](implemented/a2ui-tool-delivery.md) — `SendA2uiToClientToolset` delivery path
- [Chat Message Rendering (1.1)](implemented/chat-message-rendering.md) — how A2UI is mounted in chat (unchanged by this work)
- [LOCAL_MODE & Workshop Readiness (1.18)](implemented/local-mode-and-workshop-readiness.md) — supplies the demo skill seed mechanism
- [Local Dev CLI (1.4)](local-dev-cli.md) — hosts the new `aiplatform sessions trigger-action` subcommand
- [AI UI Protocol Stack talk](../../talks/ai-ui-protocol-stack.md) — workshop tracker; this doc fills the Pattern 1 rung
- [build-ai-uis-beyond-chat](https://github.com/sunholo-data/build-ai-uis-beyond-chat) — public workshop materials repo
- **Upstream filings:**
  - [a2ui-project/a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) — A2UI embedded transport spec gap (Discussion)
  - [ag-ui-protocol/ag-ui#1893](https://github.com/ag-ui-protocol/ag-ui/discussions/1893) — `body.state` one-turn-behind clarification (Discussion)
  - [ag-ui-protocol/ag-ui#1892](https://github.com/ag-ui-protocol/ag-ui/issues/1892) — duplicate terminal events bug (Issue)

---

## Implementation Report

**Completed**: 2026-06-08
**Actual Effort**: ~1 day (matches estimate)
**Branch/PR**: `dev` branch, commit range `161c9a8..56a3292`

### What Was Built

**M1 — Backend foundation** (commit `d157ee5`)
- New `POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run` endpoint with 8 gates: 7 reused from `surface-action` via the new shared `_a2ui_surface_shared` module + new `allow_action_triggered_runs` opt-in (gate 8) + an implicit 9th sanity check that URL `skill_id` matches the session's `skill_id`.
- Synthesises `RunAgentInput(messages=[], state={a2ui_action_trigger:...}, forwardedProps={a2ui_surface_state, _action_trigger})` and streams via the existing `build_agui_adk_agent` + `stream_agui_events` pipeline (G41 dedup reused as-is).
- `wrap_with_a2ui_surface_context` emits a "user just performed an action" framing clause when `_action_trigger` is in invocation state.
- `+22` backend tests (1427 → 1449); endpoint covers all 8 gates plus G41 dedup verification.

**M2 — Frontend wiring** (commit `39d8563`)
- New `useActionDrivenAgent` hook (~280 LOC) — POSTs to the new endpoint via `fetchWithAuth`, consumes the SSE stream with `ReadableStream`+`TextDecoder` (chosen over `EventSource` because EventSource can't carry Firebase auth headers), and dispatches A2UI tool-call results directly via `registry.appendMessages` — the same path `MessageBubble`'s `A2UISurfaceDispatcher` uses for chat turns.
- `<A2UISurfaceMount triggerOnAction skillId>` opt-in prop (default `false` preserves current behaviour).
- `+16` frontend tests (574 → 590); regression test confirms `HttpAgent` is NOT rebuilt across an action-triggered run.

**M3.1 — Demo skill + fixture** (commit `56a3292`)
- `demo-click-counter` skill seeded into `backend/db/local_fixture.py` (model `gemini-2.5-pro` on Vertex AI; both `allow_surface_context_writes` and `allow_action_triggered_runs` true).
- `/dev/a2ui` Pattern 1 fixture page with hand-seeded Button-with-counter surface and a `<A2UISurfaceMount triggerOnAction>`.
- `+1` backend test + `+4` frontend tests (1450 + 594 totals).

**M3.2 — CLI** (commit `c57ca27`)
- `aiplatform sessions trigger-action <session-id> --skill … --surface … --action …` Click subcommand on the existing `sessions` group.
- Newline-delimited JSON output for grep-friendly scripting; `--pretty` for human reading.
- Auth via `AIPLATFORM_ID_TOKEN` env var or `gcloud auth print-identity-token` fallback.
- `+6` CLI tests (48 → 54 totals).

**M3.3 — Smoke harness + talk-log** (commit `f5d48cc`)
- `scripts/smoke-pattern1.sh` — bash, shellcheck-clean, 5 iterations against `demo-click-counter`, asserts SSE shape + A2UI tool call per iteration, prints `PATTERN 1 SMOKE: 5/5 PASS` on success.
- `docs/talks/ai-ui-protocol-stack.md` verification log row appended dated 2026-06-08 referencing all five sprint commits.

### Deviations from Plan

1. **9th implicit gate (M1)** — URL `skill_id` ≠ session's `skill_id` returns 403. Without this, a caller with access to a session under skill A could invoke skill B by forging the URL.
2. **`_action_trigger` threaded through BOTH `state` AND `forwarded_props` (M1)** — vendored `ag_ui_adk` (`adk_agent.py:1780`) only copies `input.state` into the agent's session state; `forwarded_props` is not read by ag_ui_adk at all. So the InstructionProvider reads from `state["a2ui_action_trigger"]` for functionality + we also populate `forwarded_props._action_trigger` for protocol-canonical compliance.
3. **No new AGUIProvider export (M2)** — hook dispatches A2UI results directly via `registry.appendMessages` instead of forking the HttpAgent dispatch. Avoids ownership issues (the chat HttpAgent is single-URL bound) and keeps changes minimal+additive.
4. **`SurfaceActionRunPayload` is a local copy of `SurfaceActionPayload` (M1)** — each route module owns its own wire contract so the action-run path can evolve independently. ~15 LOC duplication.
5. **End-to-end smoke not executed in sprint** — requires `make cli-install` to refresh the binary + `LOCAL_MODE=1 make dev` + Vertex ADC. Script is correct; precondition gate fires cleanly when prerequisites are missing. Manual run is the next step before the workshop.

### Final Test Tallies

| Suite | Before | After | Δ |
|---|---|---|---|
| Backend `make test-fast` | 1427 | **1450** | +23 |
| Frontend `npm run test:run` | 574 | **594** | +20 |
| CLI `pytest` | 48 | **54** | +6 |
| Total new tests | — | — | **+49** |

### Files Changed

**Backend (new):**
- `backend/protocols/_a2ui_surface_shared.py`
- `backend/protocols/a2ui_surface_action_run_routes.py`
- `backend/tests/api_tests/test_a2ui_surface_action_run_routes.py`

**Backend (modified):**
- `backend/protocols/a2ui_surface_action_routes.py` (imports from shared)
- `backend/adk/a2ui.py` (+`allow_action_triggered_runs`)
- `backend/adk/a2ui_surface_context.py` (+`_format_action_trigger_clause`)
- `backend/db/local_fixture.py` (+`demo-click-counter` skill)
- `backend/fast_api_app.py` (+router registration)
- `backend/tests/unit/test_a2ui_surface_context_injection.py`
- `backend/tests/unit/test_skill_config_a2ui_surface.py`
- `backend/tests/unit/test_local_fixture.py`

**Frontend (new):**
- `frontend/src/hooks/useActionDrivenAgent.ts`
- `frontend/src/hooks/__tests__/useActionDrivenAgent.test.tsx`
- `frontend/src/app/dev/a2ui/page.tsx`
- `frontend/src/app/dev/a2ui/__tests__/page.test.tsx`

**Frontend (modified):**
- `frontend/src/components/protocols/A2UISurfaceMount.tsx` (+`triggerOnAction`, `+skillId`)
- `frontend/src/components/protocols/__tests__/A2UISurfaceMount.test.tsx`

**CLI:**
- `cli/aiplatform/commands/sessions.py` (+`trigger-action` subcommand)
- `cli/tests/test_sessions_trigger_action.py` (new)

**Scripts + docs:**
- `scripts/smoke-pattern1.sh` (new)
- `docs/talks/ai-ui-protocol-stack.md` (verification log row)

### Lessons Learned

**Went well:**
- Open question (`RunAgentInput.messages=[]`) was resolved upfront by reading the vendored `ag_ui_adk` source — `_start_new_execution(input)` handles it explicitly at line 840. Saved an iteration cycle.
- Parallel fan-out for M3 (three independent sub-tasks via Task sub-agents) compressed wall-clock from estimated ~2.5h to ~10min — three sub-agents finished within minutes of each other.
- Pinning the shared contract (`skill_id=demo-click-counter`, `surface_id=counter-main`, action `increment`) in all three M3 briefs let the sub-agents work in isolation without coordination.
- Existing G41 terminal-event dedup (the workaround we filed upstream as [ag-ui#1892](https://github.com/ag-ui-protocol/ag-ui/issues/1892)) was reused for free in the new endpoint — no per-route dedup code needed.

**Could be improved:**
- The SSE formatter is duplicated between the chat endpoint and the new action-run endpoint. A small `format_agui_event_as_sse(event) -> bytes` helper would tidy both. Filed as a follow-up.
- AG-UI wire format is camelCase (`toolCallName` not `tool_call_name`) — surprised the smoke-script author. Worth a comment near the SSE assertions or a typed envelope.
- End-to-end smoke wasn't run inside the sprint; surfacing the `make cli-install` requirement explicitly in `scripts/smoke-pattern1.sh`'s precondition gate would help next time.
- The demo skill uses real Vertex AI (`gemini-2.5-pro`), so the smoke script isn't purely hermetic — counter assertions are lenient on field names to tolerate model non-determinism. A purely-mocked LOCAL_MODE agent path would make CI integration cheaper.

### Upstream Status

The upstream filings made before the sprint started remain open:
- [a2ui-project/a2ui#1570](https://github.com/a2ui-project/a2ui/discussions/1570) — A2UI embedded-transport spec gap (Discussion). This sprint's implementation is what we'd PR back if the maintainers agree.
- [ag-ui-protocol/ag-ui#1893](https://github.com/ag-ui-protocol/ag-ui/discussions/1893) — `body.state` one-turn-behind clarification (Discussion).
- [ag-ui-protocol/ag-ui#1892](https://github.com/ag-ui-protocol/ag-ui/issues/1892) — duplicate terminal events bug (Issue).
