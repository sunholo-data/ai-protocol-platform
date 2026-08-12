# Chat Message Rendering

> **Workshop W8 — Convergence:** This is where every protocol from the workshop
> meets in one component tree. AG-UI events drive the state machine; `A2UIRenderer`
> (W6) and `MCPAppFrame` (W7) are leaf nodes; `useSkillAgent` (W5d) is the root.
> The architecture diagram below is the workshop's payoff slide.

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 2.5 days
**Scope**: Frontend
**Dependencies**: frontend-architecture (v6.0.0 ✅), streaming-and-protocols (v6.0.0 ✅), AG-UI `useSkillAgent` hook (v6.0.0 ✅)
**Created**: 2026-04-23
**Last Updated**: 2026-04-23

## Problem Statement

**Current State:**
- v6 has a working AG-UI streaming hook (`useSkillAgent`) that returns `messages[]` and `isLoading`, but no components consume it — there is no visible chat UI
- The document workspace mockup ([/frontend/public/mockups/document-workspace.html](/frontend/public/mockups/document-workspace.html)) shows a full chat panel: bot/user message bubbles, inline tables, source citation links, a context banner, streaming animation, and a compose area
- None of these components exist yet in v6

**Impact:**
- The entire chat panel of the mockup is unimplemented — the platform produces no visible output for any user interaction
- `rich-media-rendering.md` (SVG, images, PDF cards) depends on `MessageBubble` existing before it can wire in

## Goals

**Primary Goal:** Implement the chat panel rendering layer — the components that turn AG-UI `messages[]` into the visual chat UI shown in the mockup.

**Success Metrics:**
- Bot and user messages render with correct avatar, name, and timestamp styling matching the mockup
- Streaming text tokens appear incrementally as they arrive (no waiting for full message)
- Source citation links inside agent messages render as teal chip links, not plain anchors
- Inline tables inside agent messages render styled (not raw markdown)
- Tool call events show a status chip (spinner → checkmark/error)
- Auto-scroll: new messages scroll into view without forcing user back to bottom if they've scrolled up

**Non-Goals:**
- SVG / image / PDF rendering (that is [rich-media-rendering.md](rich-media-rendering.md), which builds on top of this)
- Voice/audio input or output
- Message editing or deletion
- Chat search

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Streaming tokens render incrementally; first visible output <100ms after stream starts |
| 2 | EARNED TRUST | +1 | Source citation chips anchor every claim to a specific document section; users can click through to verify |
| 3 | SKILLS, NOT FEATURES | 0 | Rendering infrastructure; no new user-facing skill concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | Tool chip falls back to plain text label if status unknown; citation falls back to plain link; no single failure breaks the whole bubble |
| 6 | PROTOCOL OVER CUSTOM | +1 | AG-UI event taxonomy drives all state transitions (TEXT_MESSAGE_START/CONTENT/END, TOOL_CALL_START/END, RUN_STARTED/FINISHED) — no custom message protocol |
| 7 | API FIRST | 0 | Frontend-only; no new API surface |
| 8 | OBSERVABLE BY DEFAULT | 0 | Existing AG-UI OTEL tracing covers the stream events |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data surface; chat content is already in-flight on the existing auth'd AG-UI stream |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Components are pure presentation; no business logic; all state from AG-UI events and `useSkillAgent` |
| | **Net Score** | **+5** | Threshold: >= +4 ✓ |

## Design

### Overview

A `ChatMessageList` component consumes the `messages[]` array and `isLoading` flag from `useSkillAgent`, rendering each message as a `MessageBubble`. A `StreamingBubble` handles live text as tokens arrive. Source citation links embedded by the agent are detected in `ChatMarkdown` (added by `rich-media-rendering.md`) and rendered as `InlineCitation` chips. Tool call events produce a `ToolCallChip` inline with the message. A `ContextBanner` at the top of the message list shows the active document context from the current skill session.

### AG-UI Event → Component Mapping

| AG-UI Event | Component action |
|---|---|
| `RUN_STARTED` | Show `TypingIndicator` (three dots) |
| `TEXT_MESSAGE_START` (role=assistant) | Create `StreamingBubble` for this `messageId` |
| `TEXT_MESSAGE_CONTENT` | Append delta to `StreamingBubble` text |
| `TEXT_MESSAGE_END` | Finalise → `MessageBubble`; run `extractA2UISegments` to split text/A2UI blocks |
| `TEXT_MESSAGE_START` (role=user) | Create user `MessageBubble` (optimistic, already shown) |
| `TOOL_CALL_START` | Append `ToolCallChip` (spinner) to current bot bubble |
| `TOOL_CALL_END` (result has `ui://`) | Replace chip with `MCPAppFrame` (sandboxed widget) |
| `TOOL_CALL_END` (no `ui://`) | Update chip to checkmark (success) or error icon |
| `RUN_FINISHED` / `RUN_FAILED` | Clear `TypingIndicator`; mark last bubble as complete |

### Frontend Changes

**New Components:**

```
frontend/src/components/chat/
  ChatMessageList.tsx     — scrollable list, auto-scroll logic, maps messages[] to bubbles
  MessageBubble.tsx       — bot/user variants; avatar, name, timestamp, bubble body
  StreamingBubble.tsx     — live partial-text bubble with blinking cursor
  TypingIndicator.tsx     — three-dot animation shown during RUN_STARTED → first token
  ToolCallChip.tsx        — inline tool execution status (spinner → check/error + tool name)
  InlineCitation.tsx      — styled chip for source reference links inside agent text
  ContextBanner.tsx       — top-of-list bar showing active document/folder context
```

**Existing components being wired in (already implemented in `streaming-and-protocols.md`):**

```
frontend/src/components/protocols/
  A2UIRenderer.tsx    — W6 workshop component; renders ```a2ui fenced blocks via A2UIViewer
  MCPAppFrame.tsx     — W7 workshop component; sandboxed iframe for ui:// tool results
```

These exist and are tested. This doc's job is to wire them into `MessageBubble` as the chat rendering layer, not to re-implement them.

**`extractA2UISegments` utility** (new, in `src/lib/chat-utils.ts`):

Splits a finalised message string on `\`\`\`a2ui ... \`\`\`` fences, returning an array of `{ type: 'text' | 'a2ui', content: string }` segments. `MessageBubble` maps segments: `text` → `ChatMarkdown`, `a2ui` → `A2UIRenderer`. This is the W6b piece referenced in the workshop doc.

**`MessageBubble.tsx`:**

Bot variant: orange left border (`border-l-[3px] border-orange`), gradient avatar with Aitana icon, skill name + timestamp in header. User variant: teal left border, user-initial avatar, display name + timestamp. Bubble body renders via `ChatMarkdown` (from `rich-media-rendering.md`) once that doc is implemented; until then falls back to `<p>` with whitespace-pre-wrap.

```tsx
// Styling matches mockup exactly
// bot:  bg-[hsl(0,0%,98%)] border-l-[3px] border-orange-400
// user: bg-[hsl(200,20%,97%)] border-l-[3px] border-teal-500
// bubble radius: rounded-[2px_8px_8px_8px] (top-left sharp, others rounded)
```

**`StreamingBubble.tsx`:**

Accumulates tokens from `TEXT_MESSAGE_CONTENT` in local state. Renders partial text with a blinking cursor (`animate-pulse`) at the end. On `TEXT_MESSAGE_END`, the parent swaps this for a finalised `MessageBubble` — no layout shift because the bubble dimensions are stable during streaming.

**`InlineCitation.tsx`:**

The agent backend embeds source refs in its text using the URI pattern `aitana://doc/{docId}/block/{blockId}` (or a GCS signed URL for the doc). `ChatMarkdown`'s `a` renderer detects these and delegates to `InlineCitation` instead of a plain `<a>`:

```tsx
// Renders as: [link-icon] "Q1 Financial Summary, Table 1"
// Teal colour, chip shape, opens document panel at that block on click
// Falls back to plain <a> if docId not in the active session's doc set
```

The citation destination is a custom `aitana://` URI. Clicking one fires a `navigateToBlock(docId, blockId)` callback (passed down via context) which — once the file browser exists — opens that document in the doc panel and scrolls to the block. Initially the callback is a no-op that opens the GCS signed URL in a new tab.

**`ToolCallChip.tsx`:**

Compact inline chip: tool name (truncated to 32 chars) + status icon. Shown inline after the last text paragraph in the bot bubble, before `TEXT_MESSAGE_END`. On completion: if the tool result contains a `ui://` URI, the chip expands into an `MCPAppFrame` (the sandboxed widget takes over). Otherwise the spinner is replaced with a green checkmark (success) or red X (error).

**`MCPAppFrame` integration:**

When `TOOL_CALL_END` carries a tool result with `content` matching `^ui://`, pass `uri` + `html` to `MCPAppFrame`. The frame renders inside the bot bubble, below the text (not in a separate pane). Height defaults to 400px; user can drag-resize (native `resize: vertical` CSS). This is the W7 workshop moment — `MCPAppFrame` already exists, this is purely the event routing.

**`ContextBanner.tsx`:**

The top-of-messages banner (matches mockup's `.chat-doc-context` element). Reads `activeDocumentContext` from `SkillContext` — set when a folder or set of documents is loaded into the session. Renders: folder icon + "Analyzing N documents from {folderName}" text. Hidden when no document context is active.

**`ChatMessageList.tsx`:**

- Renders `ContextBanner` at the top if context is present
- Maps `messages[]` from `useSkillAgent` to `MessageBubble` (finalised) or `StreamingBubble` (last message if `isLoading`)
- Auto-scroll: `useEffect` + `scrollIntoView({ behavior: 'smooth' })` on new message append, but only if `scrollTop + clientHeight >= scrollHeight - 100` (user is near the bottom). If user has scrolled up, a "↓ New message" badge appears.
- Dedup: messages are keyed by `message.id` (AG-UI `messageId`) — safe to re-render on state change

**Modified Components:**

- The root workspace layout (in `document-ui.md`) has a chat panel stub — replace the stub content with `<ChatMessageList>` + compose area

**State Management:**
- No new contexts needed — `useSkillAgent` already provides `messages[]`, `isLoading`, `sendMessage`, `stop`
- `navigateToBlock` callback added to `SkillContext` for citation click-through (no-op stub initially)
- `activeDocumentContext` (`{ folderName, docCount }`) added to `SkillContext`, set by file browser when documents are loaded

### API Changes

None — this is pure frontend, consuming the existing AG-UI stream.

### Architecture Diagram

This diagram is the key teaching moment for the workshop. It shows how the same AG-UI event stream routes to three different rendering paths — each driven by a different open protocol — with no branching in business logic.

![Chat rendering architecture — protocol convergence](../../talks/assets/chat-rendering-architecture.svg)

```
                   ┌──────────────────────────────────────────┐
                   │          AG-UI event stream              │
                   │  (SSE from /api/skill/{id}/stream)       │
                   └─────────────────┬────────────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
TEXT_MESSAGE_START            TOOL_CALL_START/END          RUN_STARTED
TEXT_MESSAGE_CONTENT                 │                     RUN_FINISHED
TEXT_MESSAGE_END                     │                          │
          │                          ▼                          ▼
          ▼                   ToolCallChip               TypingIndicator
   StreamingBubble             (spinner)
   (live tokens)                     │
          │                   on TOOL_CALL_END:
   on TEXT_MESSAGE_END               │
          │                 ┌────────┴────────────┐
          ▼                 │                     │
extractA2UISegments    has ui:// ?            no ui://
(splits on ```a2ui)         │                     │
          │                 ▼                     ▼
   ┌──────┴──────┐    MCPAppFrame         checkmark / error
   │             │    (W7 ─ already built)
text segments  a2ui    │
   │           segments│    <iframe                ← MCP Apps protocol
   ▼             │     │     sandbox="allow-scripts"   ui:// resource URI
ChatMarkdown  A2UIRenderer   no allow-same-origin       served by MCP server
(W5 ─ AG-UI   (W6 ─ already  postMessage only
 text stream)  built)
   │             │
markdown +   A2UIViewer      ← A2UI protocol
SVG/img/PDF  (@a2ui/react)     JSON component vocabulary
InlineCitation two-way bind    onAction → new user message
(aitana:// links)
```

**The three rendering paths — each triggered by a different protocol:**

| Path | Trigger | Protocol | What renders |
|---|---|---|---|
| **Markdown** | Plain text in `TEXT_MESSAGE_CONTENT` | AG-UI (W5) | `ChatMarkdown` → headings, code, tables, SVG, images, PDFs, citation chips |
| **Declarative UI** | `` ```a2ui `` fence in agent text | A2UI (W6) | `A2UIRenderer` → forms, structured tables, charts, any A2UI Basic component |
| **Sandboxed widget** | `ui://` URI in `TOOL_CALL_END` result | MCP Apps (W7) | `MCPAppFrame` → sandboxed `<iframe>`, any HTML the MCP server returns |

**Why this matters for the talk:** The agent never decides how to render — it decides what to send. The rendering path is entirely determined by the content shape and the protocol it matches. Swapping a rendering library (e.g., upgrading A2UI) requires zero changes to the agent or the event stream.

### Workshop Integration

This component set is the live code for workshop modules W5, W6, and W7. Every new file must carry a workshop comment block so attendees reading the code can immediately locate which protocol is being demonstrated and why the code is shaped the way it is.

**Required comment format — one block at the top of each relevant file:**

```tsx
// Workshop W5b — AG-UI: text events → chat bubbles
// ChatMessageList maps AG-UI messages[] from useSkillAgent to MessageBubble /
// StreamingBubble. All state transitions are driven by TEXT_MESSAGE_START /
// CONTENT / END events — no custom event types, no polling.
// See: docs/talks/workshop.md §W5
```

```tsx
// Workshop W6b — A2UI: the text-vs-component decision point
// extractA2UISegments splits a finalised message string on ```a2ui fences.
//   text segments → ChatMarkdown   (AG-UI plain text, W5)
//   a2ui segments → A2UIRenderer   (A2UI protocol, @a2ui/react, W6a)
// The split runs once on TEXT_MESSAGE_END, never during streaming, so the
// A2UI viewer always receives a complete JSON spec, never a partial one.
// See: docs/talks/workshop.md §W6, A2UIRenderer.tsx W6a
```

```tsx
// Workshop W7b — MCP Apps: tool results → sandboxed widgets
// When TOOL_CALL_END carries a ui:// URI, ToolCallChip expands into MCPAppFrame.
// sandbox="allow-scripts" without allow-same-origin means the widget has no
// access to cookies, the host DOM, or the user's session token.
// postMessage is the only communication channel — explicit and auditable.
// See: docs/talks/workshop.md §W7, MCPAppFrame.tsx W7
```

**File → workshop label mapping:**

| File | Label | Workshop moment |
|---|---|---|
| `ChatMessageList.tsx` | `W5b` | "Here's where AG-UI text events become the bubbles you see" |
| `MessageBubble.tsx` | `W5b` | "Each finalised TEXT_MESSAGE_END is one immutable bubble" |
| `StreamingBubble.tsx` | `W5b` | "TEXT_MESSAGE_CONTENT events accumulate here during streaming" |
| `src/lib/chat-utils.ts` (`extractA2UISegments`) | `W6b` | "This single function is the decision point: markdown or A2UI?" |
| `ToolCallChip.tsx` (MCPAppFrame branch) | `W7b` | "A ui:// result turns a spinner into a live sandboxed widget" |

**Add entries to `docs/talks/workshop.md`** when this doc is implemented — new sub-modules W5b, W6b, W7b bridge the abstract protocol walk-through to the concrete chat rendering code that attendees can clone and run.

### CLI Surface

No CLI commands required — this is a pure frontend rendering feature with no developer-facing resources.

## Implementation Plan

### Phase 1: Core bubbles (~0.75 day)
- [ ] `MessageBubble.tsx` — bot/user variants, avatar, name, timestamp, `<p>` body fallback (~90 lines)
- [ ] `ChatMessageList.tsx` — list, key-by-id, auto-scroll logic, TypingIndicator slot (~70 lines)
- [ ] `StreamingBubble.tsx` — accumulate tokens, blinking cursor, swap to MessageBubble on END (~50 lines)
- [ ] `TypingIndicator.tsx` — three-dot CSS animation (~20 lines)
- [ ] Wire `ChatMessageList` into workspace chat panel stub

### Phase 2: Source citations + tool chips (~0.75 day)
- [ ] `InlineCitation.tsx` — chip component, `navigateToBlock` callback (no-op stub) (~40 lines)
- [ ] `ToolCallChip.tsx` — spinner/check/error states, tool name display (~40 lines)
- [ ] `ContextBanner.tsx` — active doc context display, hidden when empty (~30 lines)
- [ ] Add `navigateToBlock` + `activeDocumentContext` to `SkillContext` type (~15 lines)
- [ ] Detect `aitana://` links in inline `<a>` renderer (in ChatMarkdown stub or `MessageBubble` body)

### Phase 3: A2UI + MCP App wiring (~0.5 day)
- [ ] `extractA2UISegments` utility — split on ```a2ui fences, return typed segment array (~30 lines)
- [ ] Wire `A2UIRenderer` into `MessageBubble` for `a2ui` segments — the W6b integration point (~15 lines)
- [ ] Wire `MCPAppFrame` into `ToolCallChip` for `ui://` tool results — the W7 integration point (~20 lines)
- [ ] Tests: `extractA2UISegments` splits correctly; mixed text/A2UI message renders both; `ui://` result renders frame (~40 lines)

### Phase 4: Workshop comments (~0.25 day)
- [ ] Add `W5b` comment block to `ChatMessageList.tsx`, `MessageBubble.tsx`, `StreamingBubble.tsx`
- [ ] Add `W6b` comment block to `src/lib/chat-utils.ts` (`extractA2UISegments`)
- [ ] Add `W7b` comment block to `ToolCallChip.tsx` at the MCPAppFrame branch
- [ ] Add W5b/W6b/W7b sub-module entries to `docs/talks/workshop.md` (code file cross-refs + what to say)

### Phase 5: Tests + polish (~0.5 day)
- [ ] `MessageBubble` tests: renders bot/user variants, shows timestamp, applies correct border colour (~40 lines)
- [ ] `ChatMessageList` tests: renders multiple messages in order, shows TypingIndicator while loading (~30 lines)
- [ ] `StreamingBubble` tests: accumulates tokens, cursor visible, finalises on END (~30 lines)
- [ ] `ToolCallChip` tests: spinner on START, checkmark on END, error icon on failure (~25 lines)
- [ ] `InlineCitation` tests: renders chip, calls navigateToBlock on click, falls back to plain link (~25 lines)

## Migration & Rollout

**No data migration** — pure frontend components, no Firestore changes.

**Rollback:** Remove `ChatMessageList` from the workspace layout; stubs remain. Zero risk to backend.

**Environment Variables:** None new.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `MessageBubble`: bot variant has orange border, user variant has teal border, avatar initials render
- [ ] `MessageBubble`: renders children body; timestamp formatted correctly
- [ ] `ChatMessageList`: maps 3 messages to 3 bubbles; `TypingIndicator` shown when `isLoading`
- [ ] `StreamingBubble`: appending tokens updates displayed text; cursor disappears after finalisation
- [ ] `ToolCallChip`: shows spinner on in-progress; shows tool name; updates to checkmark on success
- [ ] `InlineCitation`: renders chip shape for `aitana://` URI; calls `navigateToBlock` with correct args
- [ ] `ContextBanner`: renders folder name and count; hidden when context is `null`

### Manual Testing
- [ ] Send a message to a live skill → streaming tokens appear incrementally, no raw markdown visible
- [ ] Scroll up during streaming → auto-scroll paused; "↓ New message" badge appears
- [ ] Agent response with inline table → styled table rendered (once ChatMarkdown is wired)
- [ ] Agent response includes `aitana://` citation → chip appears, clicking opens new-tab fallback
- [ ] Tool call (e.g., web search) → chip appears with spinner, resolves to checkmark on tool return
- [ ] Load 14 documents folder → ContextBanner shows "Analyzing 14 documents from Q1 Financial Review"

## Security Considerations

- `InlineCitation` only opens `aitana://` or `https://storage.googleapis.com` URLs — no open redirect. The `navigateToBlock` callback validates the `docId` is in the active session before navigating.
- `TypingIndicator` and streaming state are local — no user data is logged or persisted in the component layer.

## Performance Considerations

- `ChatMessageList` uses `React.memo` on `MessageBubble` to prevent re-rendering stable messages when a new token arrives in the `StreamingBubble`.
- Auto-scroll check is debounced (16ms) to avoid thrashing on high-frequency token events.
- Avatars: Aitana bot avatar uses the existing `animated-aitana-square.svg` from `public/images/logo/`. User avatar is an initial rendered in a CSS gradient circle — zero network request.

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run test:run`)
- [ ] Lint and typecheck clean (`cd frontend && npm run quality:check:fast`)
- [ ] Streaming session: tokens appear incrementally in a styled bubble (visual confirm)
- [ ] Bot bubble: orange left border, Aitana avatar, skill name header — matches mockup
- [ ] User bubble: teal left border, user-initial avatar — matches mockup
- [ ] `TypingIndicator` visible between `RUN_STARTED` and first `TEXT_MESSAGE_CONTENT`
- [ ] `ToolCallChip` visible for tool-using skill (spinner → checkmark)
- [ ] Source citation chip renders for `aitana://` link in agent response
- [ ] A2UI block renders inside a bot bubble: `\`\`\`a2ui` fence in agent response → `A2UIRenderer` shows component (W6 demo works)
- [ ] `MCPAppFrame` renders inside a bot bubble when tool result contains `ui://` URI (W7 demo works)

## Open Questions

- **ChatMarkdown integration**: `rich-media-rendering.md` defines `ChatMarkdown` which this component uses for bubble body rendering. Until that doc is implemented, `MessageBubble` falls back to a plain `<p>` with `whitespace-pre-wrap`. The integration point is a one-line swap in `MessageBubble.tsx`.
- **`navigateToBlock` implementation**: needs file browser (`file-browser.md`) to exist before the full navigation works. The stub (open in new tab) is sufficient for launch.
- **Message timestamps**: displayed as relative time ("2:14 PM"). Use `date-fns/format` if already in deps, or native `Intl.DateTimeFormat` (zero deps). Check before adding a new dep.
- **Optimistic user message**: `useSkillAgent` already shows user messages immediately. Ensure the optimistic message's `id` matches the confirmed `messageId` from AG-UI so there's no duplicate render.

## Related Documents

- [frontend-architecture.md](../v6.0.0/implemented/frontend-architecture.md) — `useSkillAgent` hook, AG-UI provider
- [streaming-and-protocols.md](../v6.0.0/implemented/streaming-and-protocols.md) — AG-UI event taxonomy
- [document-ui.md](../v6.1.0/document-ui.md) — workspace layout where chat panel lives
- [rich-media-rendering.md](rich-media-rendering.md) — SVG/image/PDF in `ChatMarkdown` (depends on this doc)
- [file-browser.md](file-browser.md) — document panel navigation; provides `navigateToBlock` implementation
- [Product Axioms](../../product-axioms.md)
- [Mockup](../../frontend/public/mockups/document-workspace.html)
