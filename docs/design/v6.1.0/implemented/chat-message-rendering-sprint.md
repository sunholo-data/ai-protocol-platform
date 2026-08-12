# Sprint Plan — Chat Message Rendering (CHAT-RENDER)

**Design doc:** [chat-message-rendering.md](chat-message-rendering.md)
**Sprint ID:** CHAT-RENDER
**Created:** 2026-04-24
**Estimated duration:** 2 days (design doc said 2.5; 0.5 day saved — see Pre-built section)
**Scope:** Frontend only

---

## Pre-built (don't reimplement)

Before writing a single line, note what is **already done**:

| What | Where | Design doc expectation |
|---|---|---|
| `extractA2UISegments` | `src/lib/a2ui/extractMarkers.ts` | "new utility in chat-utils.ts" — exists, different path |
| `A2UIRenderer` | `src/components/protocols/A2UIRenderer.tsx` | "already implemented" ✅ |
| `MCPAppFrame` + `extractMCPAppURIs` | `src/components/protocols/MCPAppFrame.tsx` | "already implemented" ✅ |
| `useSkillAgent` | `src/hooks/useSkillAgent.ts` | "already implemented" ✅ |
| Chat page shell + AG-UI wiring | `src/app/chat/[skillId]/page.tsx` (207 lines) | "stub" — it's actually a working but unstyled monolith |

The chat page already renders messages, handles streaming, error, stop, A2UI, and MCP frames — all in one file with plain divs. This sprint extracts that into proper styled components matching the mockup.

---

## Milestones

### M1 — Core Bubbles + ChatMessageList (~0.75 day)

**Goal:** Replace the inline rendering in `ChatShell` with proper styled components. After M1, messages render with the correct mockup styling and smart auto-scroll — no functional regression.

**Files to create:**
- `src/components/chat/MessageBubble.tsx` (~90 LOC)
- `src/components/chat/ChatMessageList.tsx` (~80 LOC)
- `src/components/chat/StreamingBubble.tsx` (~55 LOC)
- `src/components/chat/TypingIndicator.tsx` (~25 LOC)

**Files to modify:**
- `src/app/chat/[skillId]/page.tsx` — replace inline bubble rendering with `<ChatMessageList>`

**MessageBubble styling (must match mockup exactly):**
```tsx
// bot:  bg-[hsl(0,0%,98%)] border-l-[3px] border-orange-400  rounded-[2px_8px_8px_8px]
// user: bg-[hsl(200,20%,97%)] border-l-[3px] border-teal-500  rounded-[2px_8px_8px_8px]
// Avatar: Aitana bot → animated-aitana-square.svg (already in public/images/logo/)
// Avatar: user → CSS gradient circle with initial letter (zero network req)
// Header: skill name + timestamp (Intl.DateTimeFormat — no new dep)
```

**ChatMessageList auto-scroll logic:**
- `useEffect` on `messages.length`: only scroll if `scrollTop + clientHeight >= scrollHeight - 100`
- If user has scrolled up: show a "↓ New message" badge (sticky bottom of list)
- Clear badge when user scrolls to bottom

**StreamingBubble:**
- Accumulates `TEXT_MESSAGE_CONTENT` deltas from the last `isLoading` message
- Renders partial text + blinking cursor (`animate-pulse`)
- Parent (`ChatMessageList`) decides: last message + `isLoading` → `StreamingBubble`; otherwise → `MessageBubble`
- No layout shift: bubble width stays stable during streaming

**TypingIndicator:**
- Three-dot CSS animation (`animate-bounce` with staggered delays)
- Shown when `isLoading && messages[messages.length-1]?.role !== 'assistant'` (RUN_STARTED before first token)

**Acceptance criteria:**
- [ ] Bot bubble has orange left border, Aitana SVG avatar, skill name + timestamp header
- [ ] User bubble has teal left border, initial-letter avatar, display name + timestamp header
- [ ] Streaming tokens appear incrementally with blinking cursor
- [ ] TypingIndicator visible between send and first token arrival
- [ ] Auto-scroll: follows new messages if near bottom; pauses if scrolled up; badge appears
- [ ] All existing functionality preserved (A2UI, MCP frames, error banner, stop button)
- [ ] `npm run quality:check:fast` passes

**Estimated LOC:** ~250 (implementation) + 100 (tests)

---

### M2 — InlineCitation + ToolCallChip + ContextBanner (~0.5 day)

**Goal:** The three enhancement components that don't change the core bubble structure but enrich the content.

**Files to create:**
- `src/components/chat/InlineCitation.tsx` (~45 LOC)
- `src/components/chat/ToolCallChip.tsx` (~45 LOC)
- `src/components/chat/ContextBanner.tsx` (~30 LOC)

**Files to modify:**
- `src/hooks/useSkillAgent.ts` or a new `src/contexts/SkillContext.tsx` — add `navigateToBlock` callback + `activeDocumentContext`
- `src/components/chat/MessageBubble.tsx` — wire `InlineCitation` into inline `<a>` renderer

**InlineCitation:**
- Detects `aitana://doc/{docId}/block/{blockId}` in inline `<a>` href (via custom link renderer)
- Renders as: `[link-icon] "citation text"` — teal chip, small font
- Click: calls `navigateToBlock(docId, blockId)` — initially a no-op stub that opens GCS signed URL in new tab
- Falls back to plain `<a>` if URI doesn't match `aitana://` pattern
- Security: only opens `aitana://` or `https://storage.googleapis.com` URLs (no open redirect)

**ToolCallChip:**
- Compact inline chip: tool name (truncated to 32 chars) + status icon
- States: `running` (spinner), `success` (green checkmark), `error` (red X)
- `useSkillAgent` already receives tool call events — pass them down or extend the `SkillMessage` type
- When `TOOL_CALL_END` result has `ui://` prefix: chip renders `<MCPAppFrame>` below the bubble text
- Height default 400px, `resize: vertical` CSS for drag-resize

**ContextBanner:**
- Reads `activeDocumentContext: { folderName: string; docCount: number } | null` from context
- Hidden when `null`
- Renders: folder icon + "Analyzing N documents from {folderName}"
- Matches mockup `.chat-doc-context` element

**Note on `navigateToBlock`:** Add to `SkillContext` as a no-op stub: `(docId: string, blockId: string) => void`. File browser (`file-browser.md`) implements the real navigation later — zero coupling needed now.

**Acceptance criteria:**
- [ ] `aitana://` links in agent text render as teal chip (not plain anchor)
- [ ] Clicking chip calls `navigateToBlock` (stub opens new tab to GCS URL)
- [ ] Tool call event shows spinner chip during tool execution
- [ ] Tool call resolves to checkmark (success) or red X (error)
- [ ] Tool result with `ui://` expands chip into `MCPAppFrame` (400px, resizable)
- [ ] ContextBanner renders when `activeDocumentContext` is set; hidden when null
- [ ] `navigateToBlock` and `activeDocumentContext` added to SkillContext type
- [ ] `npm run quality:check:fast` passes

**Estimated LOC:** ~140 (implementation) + 100 (tests)

---

### M3 — Tests + Polish (~0.5 day)

**Goal:** Full test coverage for all new components; polish edge cases.

**Test files:**
```
src/components/chat/__tests__/
  MessageBubble.test.tsx    — bot/user variants, avatar initials, timestamp, borders
  ChatMessageList.test.tsx  — maps N messages to N bubbles, TypingIndicator while loading
  StreamingBubble.test.tsx  — accumulates tokens, cursor visible, cursor gone after final
  ToolCallChip.test.tsx     — spinner on start, checkmark on success, error icon on failure
  InlineCitation.test.tsx   — chip shape for aitana:// URI, calls navigateToBlock, fallback
  ContextBanner.test.tsx    — renders folder name + count, hidden when null
```

**Edge cases to cover:**
- Long message: auto-scroll pauses when user is >100px from bottom
- Empty content: `MessageBubble` with no body renders without crashing
- Tool name >32 chars: truncated in `ToolCallChip`
- Malformed `aitana://` URI: `InlineCitation` falls back to plain `<a>`
- `extractA2UISegments` (already tested at `src/lib/a2ui/__tests__/`): verify integration in `MessageBubble` renders both text and A2UI segments

**Workshop comments (Phase 4 from design doc, merged here):**
Add workshop comment blocks to each new file per design doc `§Workshop Integration`:
- `ChatMessageList.tsx` → `W5b` block
- `MessageBubble.tsx` → `W5b` block
- `StreamingBubble.tsx` → `W5b` block
- `extractMarkers.ts` → `W6b` block (update existing comment)
- `ToolCallChip.tsx` → `W7b` block at the MCPAppFrame branch
- `docs/talks/workshop.md` → add W5b/W6b/W7b sub-module entries

**Acceptance criteria:**
- [ ] All 6 component test files exist with tests for all scenarios above
- [ ] `cd frontend && npm run test:run` passes (all new tests green)
- [ ] `npm run quality:check:fast` passes (lint + typecheck)
- [ ] Workshop comment blocks in all 5 target files
- [ ] W5b/W6b/W7b added to `docs/talks/workshop.md`

**Estimated LOC:** ~220 tests + 50 workshop comments

---

## Day-by-Day Schedule

| Day | Morning | Afternoon |
|---|---|---|
| **Day 1** | M1: `MessageBubble` + `TypingIndicator` + `StreamingBubble` | M1: `ChatMessageList` (auto-scroll logic) + wire into page |
| **Day 2 AM** | M2: `InlineCitation` + `ContextBanner` + `SkillContext` additions | M2: `ToolCallChip` (spinner/check/error + MCPAppFrame expansion) |
| **Day 2 PM** | M3: Write all component tests | M3: Workshop comments + `docs/talks/workshop.md` + quality gate |

---

## LOC Summary

| Milestone | Impl LOC | Test LOC | Total |
|---|---|---|---|
| M1 — Core bubbles | 250 | 100 | 350 |
| M2 — Citations, chips, banner | 140 | 100 | 240 |
| M3 — Tests + workshop comments | 50 | 220 | 270 |
| **Total** | **440** | **420** | **860** |

Velocity benchmark: recent CHAT-HISTORY sprint averaged ~150 LOC/day (implementation) across 5 milestones. At that rate 440 impl LOC = 3 days. However this is pure frontend with no backend changes, no Firestore schema work, and the core wiring (A2UI, MCP) is already done — so 2 days is realistic.

---

## Quality Gates

After each milestone:
```bash
cd <local-path>
npm run quality:check:fast    # lint + typecheck — must be clean
```

After M3:
```bash
npm run test:run              # all Vitest tests — must pass
```

Manual verify (after M1):
- Send a message to a live skill → streaming tokens appear in styled orange-bordered bot bubble
- User message shows teal-bordered user bubble with initial avatar

---

## Assumptions

1. `useSkillAgent` doesn't need to be modified to expose tool call events — if `TOOL_CALL_START/END` events aren't already in `messages[]`, `ToolCallChip` will need a parallel state channel or `useSkillAgent` will need to be extended. **Check this first before starting M2.**
2. `date-fns` is not in deps — use `Intl.DateTimeFormat` for timestamps (zero deps).
3. `animated-aitana-square.svg` exists at `public/images/logo/` — verify path before M1.
4. The design doc's `SkillContext` additions are lightweight enough to live in `useSkillAgent`'s return type rather than a new React context file.

---

## Risk

| Risk | Likelihood | Mitigation |
|---|---|---|
| Tool call events not in `useSkillAgent` messages[] | Medium | Check hook + AG-UI provider before M2; extend if needed |
| Auto-scroll debounce causes visual lag on fast token streams | Low | Start with 16ms debounce; remove if smooth |
| `InlineCitation` aitana:// detection conflicts with future ChatMarkdown | Low | Keep detection in a `src/lib/citation.ts` util function, not baked into the component |
