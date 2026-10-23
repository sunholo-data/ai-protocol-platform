# ADK 2 upgrade · 09 — Staged A2UI emission (one emit point)

**Status**: Proposed
**Phase**: 2, after 03b
**Estimated**: 2–3 days
**Parent**: [template-adk-2-upgrade.md](template-adk-2-upgrade.md)
**Pattern source**: `google/adk-recipes` `contrib/python/staged-ui-widgets` ("separate *deciding what to show* from *sending it*")
**Last Updated**: 2026-10-16

## Problem

"A2UI won't render in the Workspace" is our most recurring bug class (CLAUDE.md #7; contract C4). Today, `emit_a2ui_surface` is called from several places: `adk/a2ui_result_render.py`, `adk/callbacks.py` and `observability/timing.py`. Each call depends on a per-request `LatencyTracker` being bound in the current async context. Each emit path can go wrong in its own way:
- an unbound tracker on a new SSE endpoint
- emission from inside an `AgentTool` sub-agent, which has its own Runner and an unbound tracker
- an offloaded result
- a double-wrapped result

ADK 2.x adds two more variables:
- contextvars set in `before_tool_callback` now propagate into tools (changelog line 651)
- `AgentTool` runs in unary mode (912)

## Design (`staged-ui-widgets`, adapted to our stack)

1. **Tools and result mappings never emit.** A result→A2UI mapping writes a staged payload, `{surfaceId, messages, artifact metadata}`, into invocation state under one key. It returns a short text summary to the model.
2. **One `after_agent_callback` on the root agent** (or a plugin `after_run` hook) drains the staged payloads at the end of the turn and calls `emit_a2ui_surface`. That is the only call site. The tracker is always bound at that point, on every endpoint that uses `stream_agui_events`.
3. **Deduplicate by `surfaceId`; last write wins.** Two tool calls targeting the same surface ship once. A refresh that changes nothing ships nothing.
4. **Sub-agents stage into the parent's state**, so `AgentTool` children can contribute surfaces without emitting. This removes the "never emit from inside an AgentTool callback" trap.
5. **A guard test** (as `test_no_tool_renders` in the recipe) greps `backend/` for the emit call form and fails if it appears anywhere except the one emitter.

## Trade-off

Surfaces arrive at the end of a turn rather than mid-turn. For long multi-tool turns, keep an **explicit flush** at tool boundaries for progressive surfaces. It is the same drain function called early and is still the only emit site. CLAUDE.md #8 requires live progress: the Activity tab already shows tool calls, so end-of-turn surfaces don't make a run silent.

Not changed: "user interaction that feeds data back renders as A2UI in chat" and auto-focus of new workbench elements.

## Tests

- `adk_contract`: a turn with two tools mapping to the same `surfaceId` → one `A2UI_SURFACE` event. An `AgentTool` child's mapping → emitted by the parent. The `surface-action-run` path → emitted (C4).
- **Real stream** (the CLAUDE.md #7 verification rule): `aiplatform skill chat` on a mapped tool shows the `A2UI_SURFACE` CUSTOM event, and a real browser confirms `SurfaceRegistry` registers it and the Workspace focuses it. jsdom does not count.
