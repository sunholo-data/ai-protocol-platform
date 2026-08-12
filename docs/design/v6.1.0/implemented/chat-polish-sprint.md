# Sprint Plan: CHAT-POLISH — Markdown, Thinking Panel, Skill Display Name

## Summary

Three frontend polish features that complete the chat UI: markdown rendering in bot bubbles, surfacing reasoning/thinking content from AG-UI events, and showing the skill's human-readable name instead of its UUID in the header.

**Duration:** 1–1.5 days  
**Scope:** Frontend (skill-display-name consumes an existing backend endpoint)  
**Dependencies:** CHAT-RENDER ✅ (implemented)  
**Risk Level:** Low  
**Design Docs:**
- [chat-markdown.md](chat-markdown.md)
- [thinking-content.md](thinking-content.md)
- [skill-display-name.md](skill-display-name.md)

## Current Status Analysis

### Recent Velocity
- CHAT-RENDER sprint: 970 LOC in ~0.5 days (high velocity, focused frontend work)
- STREAM-ERR sprint: similar pace, clean TDD cycle
- Estimated capacity: 500–700 LOC/day for frontend-only work

### Existing Implementation
- `MessageBubble.tsx` renders bot text as plain `<p>` — target for ChatMarkdown swap
- `useSkillAgent.ts` exposes AG-UI subscriber — target for `REASONING_*` event wiring
- `StreamingBubble.tsx` is the in-flight bubble — target for ThinkingPanel rendering
- `ChatShell` in `chat/[skillId]/page.tsx` — target for useSkillMeta + display name
- `GET /api/skills/{skill_id}` already exists at `backend/skills/routes.py:151`
- All three packages already in `package.json`: `react-markdown ^9`, `remark-gfm ^4`, `rehype-highlight ^7`

## Proposed Milestones

### Milestone 1: ChatMarkdown — bot message rendering
**Scope:** frontend  
**Goal:** Replace the plain `<p>` fallback in `MessageBubble` with `react-markdown` + GFM + syntax highlighting. `aitana://` links delegated to `InlineCitation`. Raw HTML stripped (XSS prevention).  
**Estimated:** ~120 LOC implementation + ~130 LOC tests = ~250 LOC  
**Duration:** 0.35 days

**Tasks:**
- [ ] Check `@tailwindcss/typography` in `package.json`; add to `tailwind.config.ts` if present
- [ ] Create `frontend/src/components/chat/ChatMarkdown.tsx` (~70 LOC)
  - `ReactMarkdown` + `remarkGfm` + `rehypeHighlight`
  - Custom `a` renderer → `InlineCitation`
  - `html()` renderer returns `null` (XSS guard)
  - `className="prose prose-sm max-w-none"`
- [ ] Swap `<p>` + `renderWithCitations` in `MessageBubble.tsx` → `<ChatMarkdown>` (~5 LOC delta)
- [ ] Remove `renderWithCitations` export from `InlineCitation.tsx` if unused elsewhere (~5 LOC removed)
- [ ] Write `ChatMarkdown.test.tsx` — bold, GFM table, code block, aitana:// chip, external link, XSS strip (~130 LOC)
- [ ] Update `MessageBubble.test.tsx` for markdown output
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

**Files to Create/Modify:**
- `frontend/src/components/chat/ChatMarkdown.tsx` (new, ~70 LOC)
- `frontend/src/components/chat/MessageBubble.tsx` (modify, ~5 LOC delta)
- `frontend/src/components/chat/InlineCitation.tsx` (modify, remove unused export)
- `frontend/src/components/chat/__tests__/ChatMarkdown.test.tsx` (new, ~130 LOC)
- `frontend/src/components/chat/__tests__/MessageBubble.test.tsx` (modify)

**Acceptance Criteria:**
- [ ] `**bold**` renders as `<strong>` in DOM
- [ ] GFM table `| a | b |` renders as `<table>` with correct cells
- [ ] Fenced code block renders with highlight class
- [ ] `[label](aitana://doc/d1/block/b1)` → `InlineCitation` chip (not plain `<a>`)
- [ ] `[label](https://external.com)` → renders as a link (InlineCitation fallback)
- [ ] `<script>alert(1)</script>` in content → stripped from DOM output
- [ ] User bubbles still render as plain text (no markdown processing)
- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes

**Risks:**
- `@tailwindcss/typography` may not be installed — fallback is Tailwind utilities in `ChatMarkdown` (adds ~30 LOC, avoids a dep). Check before coding.

---

### Milestone 2: ThinkingPanel — surface AG-UI reasoning events
**Scope:** frontend  
**Goal:** Show live reasoning/thinking content from `REASONING_MESSAGE_CONTENT` events in a collapsible panel inside `StreamingBubble` before the first text token arrives. Auto-collapses when the final answer begins.  
**Estimated:** ~180 LOC implementation + ~120 LOC tests = ~300 LOC  
**Duration:** 0.45 days

**Tasks:**
- [ ] Extend `useSkillAgent.ts`:
  - Add `thinkingContent: string` and `isThinking: boolean` to `UseSkillAgentReturn`
  - Subscribe `onReasoningStartEvent` → clear `thinkingContent`, set `isThinking: true`
  - Subscribe `onReasoningMessageContentEvent` → update `thinkingContent` from `reasoningMessageBuffer`
  - Subscribe `onReasoningEndEvent` → set `isThinking: false`
  - Reset both on `onRunStartedEvent`
- [ ] Create `frontend/src/components/chat/ThinkingPanel.tsx` (~80 LOC)
  - Props: `content: string`, `isThinking: boolean`
  - Local `expanded` state (init `true`)
  - `useEffect` → `setExpanded(false)` when `isThinking` flips to false
  - Collapsible button with spinner (when active) or chevron; label "Thinking…" / "Thought process"
  - Body: `whitespace-pre-wrap` text in orange-tinted panel
- [ ] Wire through `ChatMessageList.tsx` → `StreamingBubble.tsx`:
  - Pass `thinkingContent` + `isThinking` from `useSkillAgent` through `ChatMessageList` to `StreamingBubble`
  - Render `<ThinkingPanel>` above streaming text when `thinkingContent` is non-empty
- [ ] Update `useSkillAgent` test — add `thinkingContent`/`isThinking` to expected return keys
- [ ] Write `ThinkingPanel.test.tsx` (~80 LOC): renders content, spinner when active, auto-collapses, toggle works
- [ ] Write `useSkillAgent` thinking tests (~40 LOC): content updates, resets on new run
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

**Files to Create/Modify:**
- `frontend/src/hooks/useSkillAgent.ts` (modify, ~30 LOC delta)
- `frontend/src/components/chat/ThinkingPanel.tsx` (new, ~80 LOC)
- `frontend/src/components/chat/StreamingBubble.tsx` (modify, ~15 LOC delta)
- `frontend/src/components/chat/ChatMessageList.tsx` (modify, ~10 LOC delta — pass props)
- `frontend/src/components/chat/__tests__/ThinkingPanel.test.tsx` (new, ~80 LOC)
- `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` (modify, ~40 LOC)

**Acceptance Criteria:**
- [ ] `useSkillAgent` returns `thinkingContent: string` and `isThinking: boolean`
- [ ] `thinkingContent` updates on `REASONING_MESSAGE_CONTENT` events (via `reasoningMessageBuffer`)
- [ ] `thinkingContent` and `isThinking` reset to `""` / `false` on `RUN_STARTED`
- [ ] `ThinkingPanel` renders content and shows "Thinking…" + spinner when `isThinking: true`
- [ ] `ThinkingPanel` auto-collapses when `isThinking` transitions to `false`
- [ ] `ThinkingPanel` clicking header toggles expanded/collapsed
- [ ] No panel rendered when `thinkingContent` is empty (non-thinking models)
- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes

**Risks:**
- `onReasoningMessageContentEvent` callback signature — verify `reasoningMessageBuffer` is the correct field name against the AG-UI subscriber interface. Low risk (well-documented).

---

### Milestone 3: useSkillMeta — skill display name in header
**Scope:** frontend (consuming existing backend endpoint)  
**Goal:** Fetch `display_name` from `GET /api/proxy/api/skills/{skillId}` on mount and show it in the chat header and `MessageBubble` skill label instead of the raw UUID. Fallback: truncated UUID if fetch fails.  
**Estimated:** ~80 LOC implementation + ~80 LOC tests = ~160 LOC  
**Duration:** 0.20 days

**Tasks:**
- [ ] Create `frontend/src/hooks/useSkillMeta.ts` (~50 LOC)
  - `useState(skillId.slice(0, 8))` as initial `displayName`
  - `useEffect` → `apiClient.get(\`/api/skills/${skillId}\`)` → `display_name || name || skillId.slice(0, 8)`
  - Cancel in-flight on unmount
- [ ] Wire into `ChatShell` in `frontend/src/app/chat/[skillId]/page.tsx`:
  - Call `useSkillMeta(skillId)` → `{ displayName }`
  - Use `displayName` in `<h1>` header
  - Pass `displayName` as `skillId` prop to `ChatMessageList` (MessageBubble uses it as the skill label)
- [ ] Write `useSkillMeta.test.tsx` (~80 LOC):
  - Initial value is first 8 chars of skillId
  - Resolves to `display_name` from API response
  - Falls back to `name` if `display_name` absent
  - Stays as truncated UUID on fetch error
  - Cancels in-flight fetch on unmount
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

**Files to Create/Modify:**
- `frontend/src/hooks/useSkillMeta.ts` (new, ~50 LOC)
- `frontend/src/app/chat/[skillId]/page.tsx` (modify, ~10 LOC delta)
- `frontend/src/hooks/__tests__/useSkillMeta.test.tsx` (new, ~80 LOC)

**Acceptance Criteria:**
- [ ] Chat header shows `display_name` (e.g., "Research Assistant") not UUID after fetch
- [ ] Initial render shows truncated UUID (8 chars) — no flash of blank
- [ ] `MessageBubble` skill label shows display name after fetch
- [ ] Fetch failure leaves header as truncated UUID (no blank, no crash)
- [ ] `useSkillMeta` cancels in-flight request on unmount (no state update after unmount)
- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes

**Risks:**
- `apiClient.get()` return type — verify it returns the response body directly (not `Response`). The existing `useSkillAgent` and page.tsx both use `apiClient`, so pattern is established.

---

## Day-by-Day Breakdown

### Day 1 (full day)
- **Morning — M1 ChatMarkdown (0.35d)**
  - Check tailwind/typography, create `ChatMarkdown.tsx`, swap MessageBubble, write tests
  - Checkpoint: `npm run test:run` clean, bot bubbles render markdown in dev
- **Afternoon — M2 ThinkingPanel (0.45d)**
  - Extend `useSkillAgent`, create `ThinkingPanel.tsx`, wire through ChatMessageList → StreamingBubble
  - Write tests for hook additions + ThinkingPanel component
  - Checkpoint: `npm run test:run` clean

### Day 2 (half day)
- **Morning — M3 useSkillMeta (0.20d)**
  - Create `useSkillMeta.ts`, wire into ChatShell, write tests
  - Checkpoint: `npm run test:run` + `npm run quality:check:fast` clean
- **Final gate:** `npm run test:run` full suite + typecheck

## Success Metrics

- [ ] All 3 milestones pass their acceptance criteria
- [ ] `npm run test:run` clean (all tests passing)
- [ ] `npm run quality:check:fast` clean (lint + typecheck)
- [ ] Bot messages render formatted markdown visible in dev
- [ ] Thinking panel appears for reasoning-capable models
- [ ] Chat header shows skill name not UUID

## Total Estimate

| Milestone | Impl LOC | Test LOC | Duration |
|-----------|----------|----------|----------|
| M1: ChatMarkdown | ~120 | ~130 | 0.35d |
| M2: ThinkingPanel | ~180 | ~120 | 0.45d |
| M3: useSkillMeta | ~80 | ~80 | 0.20d |
| **Total** | **~380** | **~330** | **~1.0d** |
