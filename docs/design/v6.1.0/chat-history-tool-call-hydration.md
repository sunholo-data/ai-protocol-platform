# Chat History Tool-Call Hydration (F2b — persistent reload story)

**Status**: Planned (post-workshop)
**Priority**: P2 — affects every cross-session reload of a chat that contains an MCP-App tool call. Workshop demo runs live, so this doesn't block the talk; it does block "open a chat I made yesterday and see what the map looked like."
**Estimated**: 1 day
**Scope**: Fullstack (backend message-history endpoint extension + frontend hydration)
**Dependencies**:
  - [mcp-app-integrations.md](mcp-app-integrations.md) ✅ shipped
  - F2a (live-session parentMessageId snapshot) ✅ shipped 2026-05-01
**Related**: [mcp-app-render-ux.md](mcp-app-render-ux.md) (1.26) — covers the **in-session** multi-iframe UX (snapshot history + pinned panel). 1.26's Phase A keeps history *within* a browser session via sessionStorage; this doc covers *across* browser sessions via the backend session-messages endpoint. They are complementary, not overlapping.
**Parent**: sprint MCP-APP-RUNTIME-FIXES (`.claude/state/sprints/sprint_MCP-APP-RUNTIME-FIXES.json`)
**Created**: 2026-05-01

## Problem Statement

When a user reloads a chat session that contains MCP-App tool calls (e.g. a map), the historical iframes are gone. Only the assistant's *text* remains; the globe / declarative widget that was part of the original turn is missing. F2a fixed this for *live* multi-turn sessions; **F2b is the cross-session reload path**.

1.26's snapshot system stashes thumbnails in `sessionStorage` so they survive an in-tab reload — but `sessionStorage` is per-tab and dies with the browser session. For the genuine "open a saved chat from another day" case, the snapshot has to live on the server *or* the iframe has to re-mount from recorded tool input. This doc handles the server side.

Concrete repro (deployed dev, 2026-05-01):

1. Open `document-analyst`, prompt "show me Reykjavik on the map" → globe renders ✅
2. Send "zoom in close on the harbour" → second globe renders, first disappears (F2a fixes this in live session)
3. Reload the page → both turns' globes are missing; only text bubbles remain

Root cause: [`ChatMessageList.tsx:177`](../../frontend/src/components/chat/ChatMessageList.tsx#L177) hardcodes `toolCalls={[]}` when rendering `initialMessages` (the messages hydrated from the session-history endpoint). The endpoint itself doesn't return tool-call data either — the hydration path is built around plain text.

## Design Questions

### Q1 — What does the backend send?

`GET /api/proxy/api/sessions/{id}/messages` currently returns `{ id, role, content }` per message. We need to extend it with each assistant message's tool calls:

```ts
interface SessionMessageHistory {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallSummary[];  // assistant only
}

interface ToolCallSummary {
  id: string;
  name: string;             // unprefixed or ADK-prefixed; same as live AG-UI
  parentMessageId: string;  // = id of the assistant message it belongs to
  argsJson?: string;        // JSON-serialised tool input (from agent's call)
  resultContent?: string;   // tool result (truncated if very large)
  status: "success" | "error";
}
```

**Source of truth**: ADK Session events. Each `tools/call` invocation is recorded as an event on the session; the response is recorded as a separate event. The mapping is straightforward but needs care around (a) tool-result truncation (some results are large — e.g. document content) and (b) MCP server attribution (tool name → server id mapping).

### Q2 — What does the frontend render?

Two options for re-rendering the iframe:

**Option A — Render-with-live-MCP**: The hydrated `MCPAppToolCallRouter` reuses the existing path: `client.listTools()` + `client.readResource(uri)`. The MCP server is hit, the resource is fetched (cached at the resource URI level), then `<AppRenderer>` mounts with the past `toolInput` + `toolResult`. Each historical turn that has a UI tool call gets its own iframe.

- **Pro**: Always consistent with current widget code; no snapshot drift.
- **Con**: N historical turns = N iframes loaded simultaneously = N × (bundle + texture + WebGL context). Cesium especially is heavy.
- **Con**: If the MCP server is down at reload time, no iframe renders. (Today's behaviour for live calls is the same — graceful degradation: text remains.)

**Option B — Static snapshot**: Capture a screenshot or a serialised state at the time of the original turn; render the snapshot inline. Iframe re-mounts on demand (e.g. user clicks "open map").

- **Pro**: Very cheap to render N turns.
- **Con**: Snapshots go stale (an embedded base64 thumbnail isn't interactive).
- **Con**: Adds backend complexity (snapshot capture path; storage; lifecycle).

**Recommendation: Option A with a soft cap.** Render up to **3 iframes** from history at any time — the most-recent assistant turns that have UI tool calls. Older ones render a placeholder ("Map (1d ago) — click to view") that materialises the iframe on demand. This bounds the worst case (3 Cesium globes, ~150 MB GPU memory) while keeping the dominant case (just-reloaded session, last 1–2 turns) snappy.

### Q3 — Per-server caching across turns

`useMcpClient(serverId)` already caches the MCP `Client` per server id. `<AppRenderer>` resolves `html` per `resourceUri`, so two historical turns of `show-map` from `ext-apps-map` share the same widget HTML — only the postMessage `toolInput` differs. That's already what we want; no changes needed.

### Q4 — Scope of v6.1.x vs deferring

The hydration gap doesn't block the workshop demo (the demo runs live, never reloads). But it makes the product feel half-finished after the second turn — particularly painful for a future case where a user opens a saved chat to "show their colleague the map I made yesterday." Recommend shipping in MCP-APP-RUNTIME-FIXES alongside F1 + F2a + F3.

## Implementation Plan

### Backend

1. **Inspect ADK session-event shape for tool calls.** Identify the canonical event type and field names. Sanity-check against `aitana-adk-testing` skill recipes.
2. **Extend `protocols/session_routes.py::get_session_messages`** (or wherever the messages endpoint lives) to assemble `ToolCallSummary[]` per assistant message, parented by message id.
3. **Truncate large `resultContent`** (e.g. > 64 KB) with a marker; we don't need the full result for replay — the iframe state is restored from `argsJson` + a reasonable `resultContent` summary.
4. **API test**: assert `/api/sessions/{id}/messages` returns toolCalls[] with `parentMessageId` matching the assistant message id.

### Frontend

1. **Extend `useSessionMessages`** (or whichever hook produces `initialMessages`) to surface `toolCalls?: ToolCallSummary[]` per `SkillMessage`.
2. **Update `ChatMessageList.tsx:177`**: instead of hardcoding `[]`, build the `toolCalls` array per initial message from the new payload, using the same `ToolCallState` shape live messages produce.
3. **Implement the soft cap on concurrent iframes** in `MCPAppToolCallRouter` (or its caller): if N visible iframes ≥ 3, render a placeholder bubble for the oldest with an "Open map" affordance that swaps to the live `<AppRenderer>` on click.
4. **Vitest cases**:
   - "reloaded session re-renders iframe for historical map turn"
   - "more than 3 historical iframes are capped; rest render placeholder"
   - "placeholder click materialises the iframe"

### Acceptance criteria

- Reloading a chat that had a `show-map` call re-renders the globe inline at that turn.
- Two-turn session with two `show-map` calls renders two iframes (still under the cap).
- Synthetic 5-turn session with 5 `show-map` calls renders 3 iframes + 2 placeholders; clicking a placeholder mounts its iframe.
- No console errors on reload; no 502 on `/api/sessions/{id}/messages`.

## Out of Scope

- Snapshot/thumbnail capture path (deferred unless soft-cap proves insufficient in practice).
- `agent-driven-document-edits.md` / A2UI replay — A2UI rendering is purely declarative from `validated_a2ui_json`, so the hydration path is the same shape but simpler. Tracked separately if needed; this doc focuses on MCP-App iframes.

## Risks

- **ADK event-shape drift**: ADK 1.x's tool-call event schema may evolve. Snapshot the field names in a unit test that exercises `aitana_platform`'s recorded events so a v1.25 upgrade fails loudly rather than silently dropping fields.
- **Cesium memory ceiling**: 3 simultaneous Cesium contexts is roughly the iPhone-Pro-on-Safari budget. If telemetry shows OOMs, consider tightening cap to 2 or moving to a snapshot fallback for older turns.
- **Server-side scaling**: A 50-turn session could ship a large messages payload. Truncate large `resultContent` and consider pagination if turns exceed ~30.
