# MCP App render UX — snapshot history + pinned widget panel

**Status**: Planned
**Priority**: P1 (workshop W7 polish; not blocking the bare demo, but greatly raises its quality and is a credible answer to "how do you actually live with multiple MCP App tool calls in one chat")
**Estimated**: Phase A 0.4d (workshop polish, frontend-only after option-3 revision) · Phase B 1.5d (pinned panel) · Phase C 0.25d (time-travel hookup) — total 2.15d if all three land; Phase A alone unblocks the workshop story
**Scope**: Frontend only (after 2026-05-01 option-3 revision — the original draft proposed a backend snapshot endpoint that is now eliminated)
**Dependencies**: [mcp-app-integrations.md](mcp-app-integrations.md) (1.7) shipped, [mcp-app-update-model-context.md](implemented/mcp-app-update-model-context.md) (1.25) shipped — Path A piggybacks on its postMessage callback.
**Created**: 2026-04-30
**Last Updated**: 2026-05-01 (option-3 revision: snapshots stay client-side; backend untouched)

## Problem Statement

Sprint 1.7 + 1.25 wired the protocol-correct way to render MCP App tool results inline in chat bubbles, with bidirectional context flow. The host policy is "one fresh iframe per tool call, mounted in the bubble of the assistant turn that produced it." This is the simplest spec-compliant host. It surfaces three UX/perf problems live in chat (verified on the v6 chat page 2026-04-30):

**1. History fragmentation.** A multi-turn map conversation produces N maps, each in its own bubble. Scrolling up to revisit "what did Munich look like compared to Copenhagen" means scrolling through interleaved chat text + heavy iframes. By the third map, the user has lost spatial intuition that they're related — they look like three separate widgets, not a sequence of views into one logical "map" workspace.

**2. Memory + initialization cost.** Each iframe = a fresh ~15 MB CesiumJS bundle + tile-load + WebGL context. The browser keeps them all alive until the chat scrolls past memory pressure thresholds (Chrome typically tolerates 5-10 before frame-drops). For widgets like ext-apps' Three.js / Shadertoy / map-server (all heavy WebGL), three turns is enough to noticeably degrade frame rate. Reactivity dies (the latest iframe loses paint priority) and the model's "responsive widget" promise weakens visibly mid-demo.

**3. Wasted spec affordances.** AppRenderer's `sendToolInput` postMessage CAN update an existing iframe with new arguments — that's literally how the spec models "ask the agent to refine what's currently shown." Today we ignore it: every new tool call = new iframe, even for the same `(server, tool)` pair. The single most expressive iframe primitive in the spec is unused.

**Live evidence (v6 chat smoke 2026-04-30):** A three-turn conversation ("show me Munich" → "what city is centred?" → "now zoom to its old town") produces TWO live Cesium iframes in the chat bubble history (the second turn doesn't render a map; the third does). Scrolling up past the third turn shrinks the second iframe out of view but it stays mounted; the user reports losing visual association between the question and the map. The workshop W7 demo currently relies on the user staring at the latest turn and trusting the agent's prose ("I've updated the map to zoom in on Munich's Old Town") rather than seeing both views side-by-side.

**Current State:**
- One Cesium instance per tool call. Three conversation turns over an hour ⇒ three iframes alive in DOM ⇒ ~45 MB of duplicated Cesium runtime.
- No snapshot of older states. Hard refresh re-instantiates each iframe from the same tool input + result we recorded; refresh time scales linearly with how many maps the conversation produced.
- No way to compare two map views from the same conversation side-by-side.
- No way to "go back to the Munich view" without the agent re-running `geocode + show-map`.

**Impact:**
- **Workshop demo:** the W7 demo as currently scripted is single-map. Showing the multi-turn flow exposes the fragmentation. Audience question "but what if I want to see all three places at once?" has no answer in the current host.
- **Real users on workshop dev environment:** anyone who actually USES the document-analyst skill with the map for more than a few turns will hit the perf cliff and the visual-disconnect problem. The very first piece of feedback we got from internal use of v6 chat with maps was "wait, where did the earlier map go?"
- **Public template clones:** any downstream project that builds an MCP-App-using chat skill inherits this UX. The cost of leaving it as default is that every fork relearns the iframe-lifecycle problem and reinvents some variant of pinning.

## Goals

**Primary Goal:** Make it possible to (a) revisit older MCP App tool-call results without paying the full live-iframe cost AND (b) update a single persistent widget across multiple turns when the user is iterating on one logical view (e.g. zooming around a map). Both behaviors must be opt-in / opt-out per skill so no existing surface regresses.

**Success Metrics:**
- Multi-turn workshop demo: 5-turn conversation with the map (Munich → Paris → London → Berlin → "compare to Munich") fits in ≤ 1 live iframe + 4 lightweight snapshots. Total memory footprint < 80 MB (vs 75 MB+ for 5 fresh Cesium instances today).
- "Click the Munich snapshot in chat history → live map snaps back to that view" works in the workshop demo with no agent round-trip needed.
- Hard refresh recovers the latest map from the existing tool-call replay path (no regression); historical snapshots are restored from sessionStorage when present, gracefully fall back to "[Map view from 8:01 PM]" placeholder when not.
- Zero new lint or test failures. Net axiom score ≥ +4.

**Non-Goals:**
- Server-side snapshot storage. Snapshots stay client-side (sessionStorage + in-memory). Persistent storage of widget state across browser sessions is a future design decision (would need a real "artifacts" model — out of scope here).
- A general-purpose iframe lifecycle manager. We solve the MCP App case; we do NOT generalize to "all iframes everywhere in the chat" (A2UI iframes, doc viewers, etc., have different requirements and don't share the heavy-WebGL constraint).
- Cross-skill widget pinning. The pinned panel is per-(skill, server). If a skill activates two MCP servers (currently rare), each gets its own pinned slot — they don't compete for one panel.
- Modifying third-party MCP server widgets (ext-apps map-server, threejs-server, etc.) to push snapshots themselves. We build the host-side handling; widget cooperation is a separate concern (see "Open Questions").
- Replacing the inline rendering. Inline IS the spec-compliant default; this design adds optional layers on top, gated by config.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Snapshots in history are instant (one image decode); pinned-panel updates use the existing iframe (no remount cost). Net better than the "every turn re-mounts a fresh Cesium" status quo. |
| 2 | EARNED TRUST | +1 | History snapshots make the assistant's claim "I updated the map" verifiable — the user can see what the previous map looked like, side-by-side with the new one. |
| 3 | SKILLS, NOT FEATURES | 0 | Skill-local config (per-skill opt-in for the pinned panel), no new top-level concept. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing implications. |
| 5 | GRACEFUL DEGRADATION | +2 | Three explicit fallback paths: (1) snapshot push absent → fall back to "[Map view from <time>]" placeholder; (2) snapshot push corrupted → discard, render placeholder, log; (3) panel layout disabled → fall back to today's inline-only behavior. Each layer is independently optional. |
| 6 | PROTOCOL OVER CUSTOM | -1 | The MCP Apps spec doesn't define a snapshot-push wire format. We extend `ui/update-model-context`'s structuredContent with a vendor-prefixed `_aitanaSnapshotDataUrl` field rather than inventing a new method. Scope of the deviation is bounded — snapshots ride one optional field on an existing host-internal postMessage that is stripped at the host before any backend hop. Migration when upstream RFCs a canonical name = a string rename in two files. Open RFC for upstream — see Open Questions. |
| 7 | API FIRST | +1 | The pinned-panel update path uses the existing `AppRenderer.appBridge.sendToolInput` postMessage (a primitive the spec already exposes); we just stop ignoring it. Snapshots ride the existing 1.25 `onFallbackRequest` callback — no new endpoint, no new API surface. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Snapshot writes log size + dataUrl prefix in the frontend hook (visible in DevTools console); pinned-panel updates log "skipped iframe remount" so we can verify the optimization is firing in production. |
| 9 | SECURE BY CONSTRUCTION | +1 | **Revised 2026-05-01 (option 3):** snapshots stay purely client-side. The host's `onFallbackRequest` handler extracts `_aitanaSnapshotDataUrl` from the iframe's postMessage, stashes in React state via `useMcpAppSnapshots`, then strips the field BEFORE forwarding to the backend's existing 1.25 endpoint. Backend never sees snapshot bytes. No new server-side data path; no new attack surface beyond the existing iframe sandbox boundary. Frontend write-side guards (1 MB dataUrl cap, MIME allowlist, `referrerpolicy="no-referrer"` + `loading="lazy"` on the rendered `<img>`) constrain what the iframe can push into our React state. Net positive — the security story for snapshots is strictly weaker when sent server-side, so client-only IS the secure choice. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The snapshot field rides existing protocol surface (postMessage → `ui/update-model-context` callback). The pinned panel is pure client-side state. No new wire concept; no new endpoint. |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ |

**Conflict Justifications:**

- **#6 PROTOCOL OVER CUSTOM (-1):** We're adding `_aitanaSnapshotDataUrl` (vendor-prefixed underscore convention from the MCP spec extension guidance) to `structuredContent` rather than waiting for an upstream snapshot RFC. Justification: (a) the spec WILL need this — every host adopting MCP Apps will hit the same iframe-history problem — but the upstream cycle is months; (b) a vendor prefix is the spec's documented escape hatch for this exact case; (c) when an upstream method lands, our migration is a string rename in two files (the iframe widget + the host's onFallbackRequest handler). Blast radius is small because the field never crosses the host→backend boundary — only the iframe author and the host need to agree on it. Tracked as an upstream RFC opportunity in the talk verification log.

## Standards Compliance Check

- **MCP Apps spec** — defines the wire format (`_meta.ui.resourceUri`, `text/html;profile=mcp-app`) and the postMessage RPC (`ui/message`, `ui/update-model-context`, `ui/notifications/initialized`, etc.). It does NOT specify how the host should display tool results spatially, snapshot them, or pin them — those are explicitly host concerns. Our design is renderer choice on top of a stable wire.

- **AppRenderer's `sendToolInput`** (already in `@mcp-ui/client@7.0.0`) IS the spec-supported way to update an existing iframe instance with new tool args. Path B uses this primitive verbatim — no shim, no extension.

- **Vendor-prefix convention** — the MCP spec extension guidance (and the `_meta` field's `_`-leading-key convention) allows hosts to add fields without polluting the namespace. Our snapshot field uses the `_aitanaSnapshotDataUrl` prefix — when the spec lands a canonical name, our host migration is a one-line constant change.

- **No custom CSP, no parallel postMessage protocol** — all new transport rides existing channels.

## CLI Surface

This feature has a small CLI affordance for debugging:

- `aiplatform sessions inspect <id> --snapshots` — prints sizes + timestamps of any snapshot pushes the backend has logged for a session (helpful when "did this widget actually push?" is the question). Implementation: extends the existing `aiplatform sessions inspect --mcp-context` subcommand from sprint 1.25 with a parallel `--snapshots` flag that reads the structured `iframe_context: write` log lines filtered for snapshot-bearing pushes.

Backlinks to [local-dev-cli.md](local-dev-cli.md).

## Design

### Phase A — Snapshot history (the workshop polish)

**The core idea:** when an MCP App iframe finishes rendering a "stable" view, it pushes a small image snapshot back to the host alongside the existing `ui/update-model-context` payload. The host stores it (client-side, sessionStorage + React state) and renders it in chat history wherever the live iframe used to live.

**Wire shape (extends sprint 1.25):**

```
iframe → host postMessage:
  method: "ui/update-model-context"
  params:
    structuredContent:
      viewUUID: "abc-123"
      currentBounds: { west, south, east, north }
      label: "Munich"
      _aitanaSnapshotDataUrl: "data:image/png;base64,iVBORw0..."  ← NEW (optional)
```

The `_aitanaSnapshotDataUrl` field is OPTIONAL. Iframes that don't push it work exactly like today (Phase A degrades to placeholder). The MCP Apps spec extension convention permits underscore-prefixed vendor fields under `_meta`/`structuredContent`.

**How the iframe produces the snapshot:**

For widgets we author (Path B + future v6 widgets): use Cesium's `viewer.scene.canvas.toDataURL("image/jpeg", 0.6)` (or threejs's `renderer.domElement.toDataURL`) at "stable" moments — typically after `tileLoadProgressEvent` reports queue-empty for ≥ 250ms. Push at the same moment we already push `ui/update-model-context`.

For third-party widgets (ext-apps map-server, threejs-server) — short term, we **do not** ship a fork. The host renders a "[Map view from 8:03 PM]" placeholder for missing snapshots and the workshop story narrates this honestly: "this widget hasn't been updated to push snapshots yet; here's the deferred-image fallback." Long-term we open issues / PRs upstream.

**How the host receives it (revised 2026-05-01 — option 3, client-only):**

The snapshot stays purely client-side. The host's `onFallbackRequest` handler in `MCPAppToolCallRouter` (already wired by sprint 1.25 for `ui/update-model-context`) extracts the snapshot field, stashes it in React state via `useMcpAppSnapshots`, then strips it BEFORE forwarding the rest of the structuredContent to the backend's existing 1.25 endpoint.

```typescript
onFallbackRequest = async (request) => {
  if (request.method !== "ui/update-model-context") return {};
  const sc = request.params?.structuredContent ?? {};

  // Sprint 1.26 — extract + validate snapshot before backend forward
  const { _aitanaSnapshotDataUrl, ...sanitized } = sc;
  if (_aitanaSnapshotDataUrl) {
    snapshotsHook.write(toolCallId, _aitanaSnapshotDataUrl);  // 1 MB cap + MIME check inside
  }

  // Backend gets the rest verbatim (existing 1.25 path; no schema change)
  await fetchWithAuth(`/api/proxy/api/sessions/${sessionId}/iframe-context`, {
    method: "POST",
    body: JSON.stringify({ serverId, toolName, structuredContent: sanitized, content: null }),
  });
  return {};
};
```

**Backend never sees snapshot bytes.** The 1.25 endpoint, validation, and tests stay as-is. No new API. No schema change. No new server-side surface.

**Frontend write-side guards** (in `useMcpAppSnapshots.write`):
- `len(dataUrl) ≤ 1 MB` (prevents memory DoS from a malicious iframe stuffing the React state)
- `mimeType ∈ {image/png, image/jpeg, image/webp}` extracted from the data URL prefix
- `<img>` rendered with `referrerpolicy="no-referrer"` and `loading="lazy"` for defence-in-depth

**Trade-off:** snapshots are tab-local. Two browser tabs viewing the same session don't share snapshot history. Acceptable for the workshop and for typical single-tab use; multi-tab is a future design decision (would need a server-side broadcast channel + persistence model — explicitly deferred).

**How the host renders it:**

`MCPAppToolCallRouter` gains a state-keyed map `Map<toolCallId, Snapshot>`. New `<MCPAppSnapshot>` component:

```tsx
<MCPAppSnapshot
  snapshot={snap}                  // dataUrl + dims + label + timestamp
  onReopenLive={() => reopenLive(toolCallId)}
/>
```

Renders as a static `<img>` with a small overlay "View live" button. Click → swap back to the live `<AppRenderer>` for that bubble.

**Iframe lifecycle policy:**

The router decides per-tool-call whether to render live or snapshot:

| Condition | Render |
|---|---|
| Tool call is the most recent `(skill, server, tool)` AND has been visible in viewport in the last N seconds | Live `<AppRenderer>` |
| Tool call has a snapshot AND is older / out-of-viewport | `<MCPAppSnapshot>` |
| Tool call has NO snapshot AND is not the most recent | `<div>[Map view from {timestamp}]</div>` placeholder |
| User clicked "View live" on a snapshot | Live `<AppRenderer>` for that bubble until the user clicks "Back to snapshot" or another tool call lands |

Per-server "most recent live" instead of "global most recent live" so two different MCP App widgets in the same chat (rare today, plausible) don't fight over the live slot.

**Acceptance:** on hard-refresh of a chat that produced 3 maps, the user sees 1 live iframe + 2 snapshot placeholders. Memory ≤ ⅓ of today's cost. Workshop demo can show the user "look — three views, comparable side-by-side, none of which we re-rendered."

### Phase B — Pinned widget panel

**The core idea:** when a skill opts in, a persistent right-pane "Widget panel" hosts ONE live iframe per (server). New tool calls update the existing iframe via `appBridge.sendToolInput` instead of mounting a fresh one in the chat bubble.

**Skill config (extends sprint 1.25's `tool_configs.mcp`):**

```yaml
toolConfigs:
  mcp:
    servers: [ext-apps-map]
    allow_context_writes: [ext-apps-map]
    pinned_panel: [ext-apps-map]   # NEW (optional, default empty)
```

When `pinned_panel` includes a server: the FIRST tool call from that server in a chat session mounts the iframe in the pinned panel (right pane, ~60% of the chat panel's height, collapsible). Subsequent tool calls call `pinnedAppBridge.sendToolInput(newArgs)` — iframe stays mounted, just receives the new bounds via postMessage; Cesium animates the camera.

Chat bubbles for those tool calls show a compact "📍 Updated → Munich Old Town · 8:04 PM" badge with a "view in panel" button (smooth-scrolls + highlights the panel briefly).

**Layout:**

```
┌─────────────────────────────────────────────┬───────────────────┐
│ Chat                                        │ Widget panel      │
│                                             │ (pinned)          │
│   user: show me Munich                      │ ┌───────────────┐ │
│   bot: I've shown Munich on the map.        │ │  [Live Cesium │ │
│        📍 Updated → Munich · 8:01 PM        │ │   iframe with │ │
│        [view in panel]                      │ │  current view]│ │
│                                             │ │               │ │
│   user: what city is centred?               │ │               │ │
│   bot: Munich.                              │ │               │ │
│                                             │ │               │ │
│   user: now zoom to its old town            │ │               │ │
│   bot: I've updated the map.                │ │               │ │
│        📍 Updated → Munich Old Town · 8:04  │ │               │ │
│        [view in panel]                      │ └───────────────┘ │
└─────────────────────────────────────────────┴───────────────────┘
```

**Wire (no spec change):** `appBridge.sendToolInput({ arguments: parsedNewArgs })` is already the spec-supported way to update an iframe with new tool args. Path B is the host taking that primitive seriously.

**Per-skill control:** the panel only appears for skills that opt in via `pinned_panel`. Skills without it keep today's inline-only rendering — no surprise UX changes for existing chats.

**Acceptance:** for `document-analyst` with `pinned_panel: [ext-apps-map]`, a 5-turn map conversation produces ONE iframe (in the panel) that animates between views, plus 5 chat bubbles each with a compact badge.

### Phase C — Snapshot ↔ pin time-travel

The polish layer that ties A and B together. When the user clicks a snapshot in chat history (Path A), the pinned panel (Path B, if present) animates back to that view via `appBridge.sendToolInput(originalArgs)`. The current view is preserved in a "back-to-now" button; clicking it returns the panel to the latest tool input.

**Acceptance:** workshop demo "let me show you what Paris looked like before we zoomed to Munich" → click Paris snapshot → panel animates to Paris → click "back to now" → panel animates to Munich Old Town. ≤ 0.25d of additional wiring on top of A + B.

### Frontend Changes (across all three phases)

**New components:**
- `frontend/src/components/protocols/MCPAppSnapshot.tsx` — Path A snapshot tile (~50 LOC)
- `frontend/src/components/protocols/MCPAppPanel.tsx` — Path B pinned-panel container (~120 LOC)

**Modified components:**
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — gains the live-vs-snapshot decision logic + snapshot state map; reads pinned-server config from props (~80 LOC delta)
- `frontend/src/app/chat/[...path]/page.tsx` — renders the optional `<MCPAppPanel>` next to `<ChatMessageList>` when the skill has `pinned_panel` set (~30 LOC)
- `frontend/src/hooks/useSkillMeta.ts` — extracts `mcp.pinned_panel` from the API response (~5 LOC)

**State management:**
- New `useMcpAppSnapshots(sessionId)` hook — wraps a `Map<toolCallId, Snapshot>` in React state; persists to sessionStorage on write, restores on chat-page mount
- New `useMcpAppPanel(skillMcpServerIds, pinnedServerIds)` hook — owns the `Map<serverId, AppBridge>` for live pinned iframes; exposes `sendToolInputToPanel(serverId, args)`

**UI/UX:**
- Snapshot tile: ~600px wide image with "View live" overlay button bottom-right; shows a subtle border + timestamp ("Map · Munich · 8:01 PM")
- Pinned panel: collapsible right pane (default 40% of viewport width, min 320px); header shows server name + "back to now" affordance; iframe fills the rest

### Backend Changes

**None required for Path A** (revised 2026-05-01 — option 3, client-only snapshots). The host strips `_aitanaSnapshotDataUrl` from the postMessage payload before forwarding the rest to the existing 1.25 endpoint. The backend still receives the structured context (viewUUID, currentBounds, label) it needs for the agent's NEXT turn — just not the snapshot bytes.

Phase B is also frontend-only (the `pinned_panel` field rides the existing `tool_configs.mcp` dict that 1.25 already plumbs through).

### API Changes

**None.** The 1.25 `POST /api/sessions/{id}/iframe-context` endpoint serves both phases unchanged. No new endpoints, no schema changes.

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Browser                                                                   │
│                                                                           │
│   ┌───────────────────────────┐   ┌────────────────────────────────────┐ │
│   │ ChatMessageList           │   │ MCPAppPanel (Path B, opt-in)       │ │
│   │                           │   │                                    │ │
│   │ ┌───────────────────────┐ │   │  ┌──────────────────────────────┐  │ │
│   │ │ MessageBubble #1      │ │   │  │  ONE pinned <AppRenderer>    │  │ │
│   │ │  • prose              │ │   │  │  ▸ Cesium iframe (LIVE)      │  │ │
│   │ │  • <MCPAppSnapshot>   │ │   │  │  ▸ updated via                │  │ │
│   │ │    (Munich, 8:01 PM)  │◀┼───┤  │    appBridge.sendToolInput()  │  │ │
│   │ │    [View live]        │ │   │  │  ▸ NO remount per turn        │  │ │
│   │ └───────────────────────┘ │   │  └──────────────────────────────┘  │ │
│   │                           │   │                                    │ │
│   │ ┌───────────────────────┐ │   │  📍 Showing: Munich Old Town       │ │
│   │ │ MessageBubble #2      │ │   │  [back to now]                     │ │
│   │ │  • prose              │ │   │                                    │ │
│   │ │  • 📍 Updated → ...   │ │   │ Click snapshot in #1 ──────────────┘ │
│   │ │    [view in panel]    │─┼───→ panel time-travels (Path C)         │
│   │ └───────────────────────┘ │                                          │
│   │                           │                                          │
│   │ ┌───────────────────────┐ │                                          │
│   │ │ MessageBubble #3      │ │                                          │
│   │ │  • <MCPAppSnapshot>   │ │ (or, when no pinned panel:               │
│   │ │  • OR live AppRenderer│ │  the LATEST bubble shows live            │
│   │ │    if most-recent     │ │  AppRenderer, older bubbles show         │
│   │ └───────────────────────┘ │  snapshots — Path A standalone)          │
│   └───────────────────────────┘                                           │
│                                                                           │
│   Snapshot state: useMcpAppSnapshots(sessionId)                           │
│     in-memory Map<toolCallId, Snapshot> + sessionStorage mirror           │
│                                                                           │
│   Iframe → host postMessage:                                              │
│     ui/update-model-context { structuredContent: {                        │
│         viewUUID, currentBounds, label,                                   │
│         _aitanaSnapshotDataUrl: "data:image/png;base64,..."  ← stripped  │
│     } }                              before backend forward, stored ────┐ │
│                                                                          │ │
│   onFallbackRequest handler:                                             │ │
│     1. extract _aitanaSnapshotDataUrl into snapshotsHook ────────────────┘ │
│     2. validate (≤ 1 MB, MIME ∈ {png|jpeg|webp})                          │
│     3. forward sanitized structuredContent (no snapshot) to backend       │
│                                                                           │
└──────────────────────────────┬────────────────────────────────────────────┘
                               │
                               │ POST /api/sessions/{id}/iframe-context
                               │   {serverId, toolName, structuredContent}  ← 1.25 wire, unchanged
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Backend                                                                   │
│                                                                           │
│   POST /api/sessions/{id}/iframe-context  [shipped sprint 1.25]           │
│       Same 7-gate validation. Same state write path. NO snapshot          │
│       handling. Backend never sees snapshot bytes by construction.        │
└──────────────────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase A — Snapshot history (~0.4 day, ~210 LOC + ~60 test LOC) [P1, workshop polish]

(Reduced from 0.5d after the option-3 revision dropped the backend route extension and its 4 tests.)

- [ ] `frontend/src/components/protocols/MCPAppSnapshot.tsx` — new component. ~50 LOC + 2 tests.
- [ ] `frontend/src/hooks/useMcpAppSnapshots.ts` — new hook with sessionStorage persistence + write-side guards (1 MB cap, MIME allowlist). ~70 LOC + 5 tests (one per guard + happy path + persistence round-trip).
- [ ] `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — extend `onFallbackRequest` handler to extract `_aitanaSnapshotDataUrl` from `structuredContent`, hand to `useMcpAppSnapshots.write`, then forward the SANITIZED structuredContent (snapshot field stripped) to the existing 1.25 backend endpoint; thread snapshot state to the bubble; live-vs-snapshot decision per tool call. ~70 LOC delta.
- [ ] `frontend/src/components/chat/MessageBubble.tsx` — render snapshot OR live based on a per-call live-flag prop. ~20 LOC.
- [ ] **Author one v6 widget that pushes snapshots** (so the workshop demo isn't gated on a third-party PR). Smallest path: a static "Munich snapshot" fixture pushed by a tiny dev page; OR a one-line PR to `ext-apps/map-server`'s `mcp-app.ts` and rebuild locally. Decision deferred to implementation.

### Phase B — Pinned widget panel (~1.5 day, ~400 LOC + ~120 test LOC) [P2, post-workshop polish]

- [ ] `frontend/src/components/protocols/MCPAppPanel.tsx` — new pinned-panel container. ~120 LOC + 6 tests.
- [ ] `frontend/src/hooks/useMcpAppPanel.ts` — new hook owning `Map<serverId, AppBridge>` + `sendToolInputToPanel`. ~100 LOC + 5 tests.
- [ ] `frontend/src/hooks/useSkillMeta.ts` — extract `pinned_panel` from API response. ~5 LOC.
- [ ] `frontend/src/app/chat/[...path]/page.tsx` — conditionally render `<MCPAppPanel>` next to chat. Layout adjustments (split-pane). ~60 LOC.
- [ ] `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — when a tool call's server is in `pinned_panel`, dispatch the tool input to the panel hook instead of mounting a new iframe in the bubble. Bubble shows compact badge. ~50 LOC delta.
- [ ] Update `document-analyst`'s `SKILL.md` template + the migration script with `pinned_panel: [ext-apps-map]`. ~10 LOC + 1 test.

### Phase C — Snapshot ↔ pin time-travel (~0.25 day, ~80 LOC + ~30 test LOC) [P2, lands with B]

- [ ] `useMcpAppSnapshots` exposes a `getOriginalToolInput(toolCallId)` accessor. ~10 LOC.
- [ ] `MCPAppSnapshot` "View live" button: when a pinned panel exists for this server, dispatch the original tool args to the panel + temporarily highlight the panel; otherwise re-mount inline as today. ~30 LOC.
- [ ] `MCPAppPanel` gains a "back to now" button visible whenever the panel is in time-travel mode. ~20 LOC + 2 tests.
- [ ] Track time-travel state in `useMcpAppPanel`. ~20 LOC + 2 tests.

## Migration & Rollout

**Database Migrations:**
- None (snapshots are client-side; pinned-panel config rides existing `tool_configs.mcp` dict, no new field at the storage layer).
- Skill config gets a new optional `tool_configs.mcp.pinned_panel` field (Phase B). Idempotent migration via the existing `scripts/migrate_mcp_context_writes.py` pattern; defaults to empty (= today's behavior).

**Feature Flags:**
- `pinned_panel` IS the per-skill feature flag for Path B. Default off.
- Path A's snapshot rendering activates whenever a snapshot is present (no flag — falls back to placeholder when absent, which IS the current behavior).
- A global `NEXT_PUBLIC_MCP_APP_RENDER_UX_PHASE_A=on/off` env var lets us disable Path A in production if a snapshot push triggers an unexpected rendering bug.

**Rollback Plan:**
- Phase A: set `NEXT_PUBLIC_MCP_APP_RENDER_UX_PHASE_A=off` — the router stops extracting + storing snapshots; iframes that push the field have it silently ignored; older bubbles fall back to "[Map view from <time>]" placeholders. No backend rollback needed since there's nothing to roll back. Live iframes for the most-recent tool call keep working unchanged.
- Phase B: prune skill config — set `pinned_panel: []`. Tool calls fall back to inline rendering. Pinned-panel code remains shipped but inert.
- Phase C: defer time-travel button rendering. Pinned + snapshot continue to work independently.

**Environment Variables:**
- `NEXT_PUBLIC_MCP_APP_RENDER_UX_PHASE_A` — default `on`; set `off` to disable snapshot rendering even when present.
- No new server-side env vars.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)

**Phase A:**
- `MCPAppSnapshot.test.tsx`: renders an `<img>` with the supplied dataUrl + label + timestamp. "View live" button is visible. Click fires the supplied `onReopenLive` callback.
- `useMcpAppSnapshots.test.ts`: persists writes to sessionStorage; restores on hook mount; ignores corrupt sessionStorage entries; handles 100-snapshot bound (LRU eviction).
- `MCPAppToolCallRouter.snapshotHistory.test.tsx`: given a chat with three tool calls (one most recent + two older with snapshots), renders 1 live AppRenderer + 2 snapshots; given a chat with three tool calls + ONE snapshot, renders 1 live + 1 snapshot + 1 placeholder.

**Phase B:**
- `MCPAppPanel.test.tsx`: mounts an iframe when the pinned-panel hook reports a live AppBridge for the configured server; remains mounted across multiple `sendToolInputToPanel` calls; updates the displayed label as the iframe pushes new context.
- `useMcpAppPanel.test.ts`: per-server AppBridge tracking; `sendToolInputToPanel` dispatches to the right bridge; teardown on session change.
- `MCPAppToolCallRouter.pinnedRouting.test.tsx`: when a tool call's server is in `pinned_panel`, the router does NOT mount an inline AppRenderer; instead, it surfaces a compact badge AND triggers `sendToolInputToPanel`.

**Phase C:**
- `MCPAppSnapshot.timeTravel.test.tsx`: when a pinned panel exists for the snapshot's server, "View live" dispatches `getOriginalToolInput(toolCallId)` to the panel hook; "back to now" reverts the panel.

### Backend Tests (pytest)

**None required.** The 1.25 endpoint is unchanged; its existing 16 tests continue to cover the iframe-context path. The router strips the snapshot field client-side before forwarding, so the backend never receives it — and there's nothing new to test on that side. Add ONE regression assertion to the existing frontend `MCPAppToolCallRouter.iframeContext.test.tsx` that the POSTed body does NOT contain `_aitanaSnapshotDataUrl` even when the iframe push includes it (closes the "we leaked snapshot bytes server-side" failure mode).

### Manual Testing

- [ ] 5-turn map conversation in workshop dev environment. Verify ≤ 1 live iframe + 4 snapshots, total memory < 80 MB (Chrome task manager).
- [ ] Hard refresh: Phase A — sessionStorage restores snapshots; user sees same chat history.
- [ ] Disable snapshot env var: confirm fallback to today's behavior (or "[Map view from <time>]" placeholders if no snapshot was ever sent).
- [ ] Phase B: switch to a skill with `pinned_panel`, fire 5 turns, confirm one iframe; switch back to a skill without it, confirm inline rendering unchanged.
- [ ] Phase C: workshop demo flow end-to-end (Paris → Munich → click Paris snapshot → panel animates → "back to now" → panel returns).

## Security Considerations

(Revised 2026-05-01 for option 3 — snapshots stay client-side.)

- **No new server-side surface.** The backend never sees snapshot bytes by construction. The router strips `_aitanaSnapshotDataUrl` from the postMessage payload BEFORE forwarding the rest to the existing 1.25 endpoint. Asserted by a frontend regression test (POSTed body must not contain the snapshot field).
- **DoS via giant snapshots (memory):** the iframe sandbox can in principle push gigabytes of base64 if its scripts are compromised. The host's `useMcpAppSnapshots.write` enforces a 1 MB dataUrl cap before the bytes ever enter React state — oversized pushes are dropped + logged. Cap is in the trusted host context (not the sandboxed iframe), so a malicious iframe can't bypass it by editing the field.
- **MIME spoofing:** the host extracts the MIME from the data URL prefix and validates it against an allowlist (`image/png`, `image/jpeg`, `image/webp`). Anything else is dropped. Browser will refuse to render bytes that aren't actually an image regardless of the declared MIME — defence in depth.
- **Tracking pixels in snapshot images:** the `<img>` rendered in chat uses `referrerpolicy="no-referrer"` and `loading="lazy"`. The dataUrl is base64-inlined (not an external URL), so no third-party referrer leak is possible by construction.
- **Persistence boundary:** snapshots live in React state + sessionStorage only. Lost on tab close + sessionStorage cleared. NEVER persisted server-side, NEVER sent to other users, NEVER survive a hard refresh that clears storage. Out of scope for any compliance regime worried about long-term storage of generated content.
- **Path B doesn't change the security model** — it's purely a layout decision; the iframe's sandbox/referrer/postMessage gates are unchanged.

## Performance Considerations

- **Memory:** primary win. 5-turn map conversation drops from ~75 MB (5 × Cesium) to ~15-20 MB (1 × Cesium + 4 × ~50 KB JPEG). Measurable in Chrome's task manager.
- **CPU:** snapshot encoding (Cesium → JPEG dataUrl) costs ~50-100 ms once per stable view. Happens off the chat-render hot path (in the iframe's render loop).
- **Network:** **none added.** Option 3 keeps snapshots client-side — no extra POST. The existing 1.25 iframe-context POST already fires once per camera reposition; we just include one more field in the postMessage that the host strips before forwarding. Wire bytes server-side: unchanged.
- **Bundle size impact (frontend):** Path A adds ~3 KB (snapshot component + hook). Path B adds ~10 KB (panel + hook + layout). No new third-party dep.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/ -m "not slow"`)
- [ ] Lint and typecheck clean (`npm run quality:check:fast`, `cd backend && make lint`)
- [ ] Backend Docker build succeeds (`npm run docker:check`)
- [ ] Documentation updated: this design moves to `implemented/`; workshop W7 module gains a "rendering modes" subsection; protocol-stack talk verification log gains a sprint-1.26 row
- [ ] **Phase A acceptance:** 3-turn map demo produces 1 live + 2 snapshots in chat history. Hard refresh restores the snapshots from sessionStorage. Memory measurably reduced (Chrome task manager screenshot in implementation report).
- [ ] **Phase B acceptance** (if shipped): `document-analyst` with `pinned_panel: [ext-apps-map]` shows a 5-turn conversation with ONE iframe + 5 chat badges; the panel iframe animates between views without remount.
- [ ] **Phase C acceptance** (if shipped): workshop "compare Paris to Munich" flow works in <5 seconds with no agent round-trip.
- [ ] Workshop W7f gains a 7th papercut row describing this UX trap and pointing at the resolution

## Open Questions

- **(A)** ~~Snapshot transport: piggyback on `ui/update-model-context` OR add a separate `ui/notifications/snapshot` postMessage?~~ **Resolved 2026-05-01:** piggyback. One wire = one mental model for widget authors; snapshot is conceptually part of the current view's state so it belongs with the structured content; easier upstream RFC story when the spec lands a canonical name. Host strips the field client-side before forwarding to the backend, so the spec extension is host-internal — minimal downstream blast radius.

- **(B)** ~~Backend-broadcast step for multi-tab snapshot sharing?~~ **Resolved 2026-05-01 (option 3):** explicitly out of scope. Snapshots are tab-local (single-tab design). The agent's context (structuredContent) still flows through ADK session state via the existing 1.25 endpoint, so the AGENT sees the same view regardless of which tab pushed — only the visual thumbnails in chat history are tab-local. Multi-tab snapshot sharing would need a real persistence model (BlobStore? GCS?); revisit only if a workshop attendee or downstream user actually asks for it.

- **(C)** Pinned panel default size + responsive behavior. Should the panel be a sidebar (right pane) or a top-strip when the viewport is narrow? My instinct: sidebar at ≥ 1024px, full-width modal at < 1024px (mobile-friendlier). Specifics in Phase B implementation review.

- **(D)** Cross-skill widget reuse: if the user moves between two skills that both pin `ext-apps-map`, does the panel iframe persist (and just receive new tool inputs from whichever skill's session is active) or remount on skill switch? Tentative: remount per-(skill, server) — keeps session boundary clean and avoids context leakage. Worth a UX call.

- **(E)** Upstream RFC for the snapshot field: file an issue against `modelcontextprotocol/ext-apps` proposing a canonical name (`_meta.ui.snapshot` or `structuredContent.snapshot`) so vendor prefixes can converge. Worth doing once we have v6 implementation experience to point at.

- **(F)** What about widgets that genuinely SHOULDN'T be snapshotted (e.g. a live form being filled in — snapshot would capture stale state)? Maybe a `_aitanaSnapshotPolicy: "never" | "stable" | "explicit"` field that the iframe can declare. Defer until we have a real form widget; not relevant for the workshop's read-only viewers.

## Related Documents

- [mcp-app-integrations.md](mcp-app-integrations.md) — sprint 1.7, ships the inline-only host this design extends
- [mcp-sandbox-separate-origin.md](mcp-sandbox-separate-origin.md) — sprint 1.24, the sandbox proxy that carries the postMessage this design relies on
- [implemented/mcp-app-update-model-context.md](implemented/mcp-app-update-model-context.md) — sprint 1.25, the iframe-context endpoint this design extends
- [docs/talks/workshop.md](../../talks/workshop.md) — Module W7; this design adds a "rendering modes" follow-up sub-section
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — Lesson #4 papercut row #6 was resolved by 1.25; this design will earn a row #7 ("inline-only is the simplest spec-compliant host but it doesn't scale past a few WebGL widgets — here's the snapshot + pin pattern")
- [local-dev-cli.md](local-dev-cli.md) — backlinked from the new `aiplatform sessions inspect --snapshots` debug subcommand
