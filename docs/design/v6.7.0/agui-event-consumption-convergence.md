# AG-UI Event Consumption Convergence — one reducer, two ingresses

**Status**: Planned
**Priority**: P1 (pays down the recurring "A2UI won't render / Activity is blind" tax)
**Estimated**: ~1 day (extract reducer + route action path + drift-guard test + delete divergent code)
**Scope**: Frontend (one shared module; two call sites) + one backend invariant (already documented)
**Created**: 2026-07-11
**Depends on**: the tactical action-run fixes landing first — backend `LatencyTracker` bind (`2973b3f`), frontend CUSTOM/`A2UI_SURFACE` dispatch (`7568e64`), and the in-flight action-run→Activity wiring. This doc GENERALIZES those patches so no future one is needed.

## Problem

The frontend has **two divergent AG-UI event consumers**:

| | Chat turn | Action-triggered run (launcher buttons) |
|---|---|---|
| Ingress | `useSkillAgent` via `@ag-ui/client` `HttpAgent` (chat URL) | `useActionDrivenAgent` hand-rolled SSE parser (`surface-action-run` URL) |
| Event handling | full: tool calls, `A2UI_SURFACE`, `STAGE_PROGRESS`, `AGENT_DELEGATION`, reasoning/thinking, `RUN_ERROR` → feeds **Activity + SurfaceRegistry** | a **subset**, re-implemented inline |

The backend already emits **one identical event stream** for both (both endpoints run `stream_agui_events`). The divergence is purely that the action path re-derives a subset of the handling the chat path already does. So every capability has to be **re-added to the action path one event type at a time** — this is the root cause of the recurring failures:
- Compare/obligation launcher rendered nothing (no `A2UI_SURFACE`/CUSTOM case) — fixed tactically in `7568e64`.
- Activity tab blind during action runs (no tool-call/stage forwarding) — fixed tactically (in flight).
- The NEXT new event type (a new tool's result surface, a new stage label, thinking on an action run) will hit the same wall.

The split's original justification is legitimate but narrow: `HttpAgent` is bolted to the chat URL and writes into a `messages` array we do NOT want for a button click (it would render a stray chat bubble). That justifies **two ingresses** — NOT two divergent reducers.

## Goal

Extract the AG-UI **event→state reducer** into ONE shared module. Both ingresses feed it:
1. the chat `HttpAgent` subscription (`useSkillAgent`), and
2. the action-run SSE parser (`useActionDrivenAgent`).

One handler, two front doors. Any event type the chat path supports then works for action-triggered runs automatically — Activity, Workspace rendering, stage progress, delegations, thinking, errors — with zero per-feature reimplementation.

## Design

### The shared reducer
A pure `(state, aguiEvent) → state` reducer (or a small event-normalizer + typed handlers) that owns ALL AG-UI event→UI-state mapping currently split across `useSkillAgent` and `useActionDrivenAgent`:
- `TOOL_CALL_START/ARGS/RESULT/END` → `ToolCallState[]`
- CUSTOM `A2UI_SURFACE` → `SurfaceRegistry.appendMessages(surfaceId, messages, sourceId, artifact)`
- CUSTOM `STAGE_PROGRESS` → transient status label
- CUSTOM `AGENT_DELEGATION` → `DelegationMarkerItem[]`
- reasoning/thinking → thinking panel
- `RUN_STARTED/FINISHED/ERROR` → run lifecycle + error surfacing

The reducer has NO transport knowledge (no fetch, no HttpAgent, no messages array). It is fed raw AG-UI events by whichever ingress.

### The two ingresses (thin)
- **Chat:** `useSkillAgent`'s `HttpAgent` subscription forwards each event into the reducer (keeps its `messages` handling for the chat transcript — that stays chat-only).
- **Action run:** `useActionDrivenAgent`'s SSE parser forwards each parsed event into the reducer. It KEEPS its separate POST (correct — no fake chat bubble) but DELETES its bespoke subset switch; the reducer does the work.

### Shared activity/surface sink
Both ingresses write tool calls / delegations / stage into the same live activity state that `ChatShell` merges into `<ActivityPanel>` — so an action-triggered run shows live in Activity exactly like a chat turn (this is what the in-flight tactical fix does; convergence makes it structural, not a second bespoke path).

### Backend invariant (already documented, keep enforced)
Every SSE endpoint that runs `stream_agui_events` MUST bind a per-request `LatencyTracker` (`set_current_tracker` / reset) or CUSTOM events silently no-op. Documented in CLAUDE.md ("Protocols first for UI"). Add/confirm a backend test asserting the action-run route binds it (already added: `TestLatencyTrackerBinding`).

## Non-Goals
- Merging the two POST endpoints or pointing `HttpAgent` at the action URL — the two ingresses are correct; only the reducer converges.
- Changing the backend event stream — already unified.
- The chat transcript `messages` handling — stays chat-only.

## Drift guard (the thing that ends "we always get it wrong")
A test that asserts the action-run ingress routes **every** AG-UI event type the chat ingress handles into the shared reducer — a table of event types both must cover, failing if the action path silently drops one. This catches a future divergence at CI time instead of in a demo.

## Migration (incremental, each step green)
1. Extract the reducer from `useSkillAgent` (no behavior change; chat path routes through it).
2. Route `useActionDrivenAgent`'s SSE events through the same reducer; delete its bespoke `TOOL_CALL_*`/CUSTOM subset switch.
3. Add the drift-guard test + a real-browser verification (compare + analyze-obligations: surface renders AND Activity streams live).
4. Retire the tactical inline handlers folded into the reducer.

## Testing
- Unit: reducer transitions per event type; both ingresses drive identical state from identical event sequences.
- Drift guard (above).
- **Real browser (non-negotiable — jsdom passing is how this class ships broken):** launcher compare + analyze-obligations on deployed dev — surface renders, Activity shows live tool calls, errors surface.

## Success Criteria
- [ ] One shared reducer; `useSkillAgent` + `useActionDrivenAgent` both feed it; action path's bespoke event switch deleted.
- [ ] A new event type added to the reducer works on BOTH paths with no action-path change.
- [ ] Drift-guard test present and green.
- [ ] Real-browser: action-triggered compare + obligations render AND stream live in Activity.
