# Thinking Content Visibility

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 0.75 days
**Scope**: Frontend
**Dependencies**: [chat-message-rendering.md](implemented/chat-message-rendering.md) ✅
**Created**: 2026-04-24
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- When an agent is doing multi-step reasoning or calling tools, the UI shows only a `TypingIndicator` (three dots) with no indication of *what* it is doing
- The AG-UI protocol emits `REASONING_START`, `REASONING_MESSAGE_CONTENT`, `REASONING_MESSAGE_END`, and `REASONING_END` events — all currently ignored by `useSkillAgent`
- Users staring at three dots for 5–10 seconds during tool-heavy queries have no signal that anything is happening

**Impact:**
- Violates Axiom 1 (INSTANT FEEL) — the system feels unresponsive even though the agent is actively working
- Users are likely to give up, retry, or assume an error occurred
- The platform's streaming commitment is undermined if the only visual is three static dots

## Goals

**Primary Goal:** Surface AG-UI reasoning/thinking content in the UI so users see the agent's working process the moment it starts — before the first text token arrives.

**Success Metrics:**
- Reasoning text appears within 300ms of `REASONING_START` firing
- The thinking panel is visually distinct from the final answer (collapsible, muted styling)
- Auto-collapses when the final answer begins (`TEXT_MESSAGE_START`)
- Works with both Claude extended thinking (Sonnet 3.7/4+) and Gemini thinking models
- Falls back gracefully (no thinking panel shown) if the model doesn't emit reasoning events

**Non-Goals:**
- Encrypted reasoning tokens (`REASONING_ENCRYPTED_VALUE`) — not surfaced (Claude's internal tokens)
- Persisting thinking content beyond the current session
- Editable or copy-able thinking content

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | User sees activity within 300ms of reasoning start — the most direct axiom hit of this feature |
| 2 | EARNED TRUST | +1 | Showing the reasoning process makes agent behaviour transparent and verifiable |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure — no new user concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model selection changes; surfaces what the model already emits |
| 5 | GRACEFUL DEGRADATION | +1 | Panel simply doesn't appear if no reasoning events fire; no broken state |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses AG-UI `REASONING_*` event taxonomy directly — no custom streaming |
| 7 | API FIRST | 0 | Frontend-only; no API surface |
| 8 | OBSERVABLE BY DEFAULT | 0 | No new data surface; reasoning already flows through the AG-UI stream |
| 9 | SECURE BY CONSTRUCTION | 0 | Reasoning text arrives from the authenticated AG-UI stream; no new trust surface |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Pure rendering of protocol events; zero business logic in the component |
| | **Net Score** | **+5** | Threshold: >= +4 ✓ |

## Design

### Overview

Extend `useSkillAgent` to track reasoning content via `onReasoningMessageContentEvent` (which provides `reasoningMessageBuffer: string` — the accumulated text). Add a `ThinkingPanel` component that renders this buffer in a collapsible, muted panel inside `StreamingBubble`. Panel auto-collapses when `TEXT_MESSAGE_START` fires (first real token).

### AG-UI Event → State Mapping

| AG-UI Event | Hook action |
|---|---|
| `REASONING_START` | Set `isThinking: true`; clear `thinkingBuffer` |
| `REASONING_MESSAGE_CONTENT` | Update `thinkingContent` with `reasoningMessageBuffer` |
| `REASONING_MESSAGE_END` | Reasoning message complete (buffer still visible) |
| `REASONING_END` | Set `isThinking: false` |
| `TEXT_MESSAGE_START` (assistant) | Collapse panel (`thinkingExpanded: false`) |

**Note:** `REASONING_MESSAGE_CONTENT`'s `reasoningMessageBuffer` is the full accumulated string (not a delta) — no accumulation logic needed in the hook.

### Frontend Changes

**New component:** `frontend/src/components/chat/ThinkingPanel.tsx`

**Modified:**
- `frontend/src/hooks/useSkillAgent.ts` — add `thinkingContent: string`, `isThinking: boolean` to return type; subscribe to `REASONING_*` events
- `frontend/src/components/chat/StreamingBubble.tsx` — render `ThinkingPanel` when `thinkingContent` is non-empty

**`useSkillAgent` additions:**

```typescript
export interface UseSkillAgentReturn {
  // ... existing fields ...
  thinkingContent: string;   // accumulated reasoning buffer; empty string when idle
  isThinking: boolean;       // true between REASONING_START and REASONING_END
}
```

Subscribe additions:

```typescript
onReasoningStartEvent: () => {
  setThinkingContent("");
  setIsThinking(true);
},
onReasoningMessageContentEvent: ({ reasoningMessageBuffer }) => {
  setThinkingContent(reasoningMessageBuffer);
},
onReasoningEndEvent: () => {
  setIsThinking(false);
},
```

Clear `thinkingContent` and `isThinking` on `onRunStartedEvent` for a clean slate each turn.

**`ThinkingPanel.tsx`:**

```tsx
interface ThinkingPanelProps {
  content: string;      // full reasoning buffer
  isThinking: boolean;  // still streaming?
}

export function ThinkingPanel({ content, isThinking }: ThinkingPanelProps) {
  const [expanded, setExpanded] = useState(true);

  // Auto-collapse when thinking finishes and final answer begins
  useEffect(() => {
    if (!isThinking) setExpanded(false);
  }, [isThinking]);

  return (
    <div className="mb-2 rounded border border-orange-200 bg-orange-50/50 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-orange-700"
      >
        {isThinking && <SpinnerIcon />}
        <span className="font-medium">{isThinking ? "Thinking…" : "Thought process"}</span>
        <ChevronIcon expanded={expanded} />
      </button>
      {expanded && (
        <p className="whitespace-pre-wrap px-2 pb-2 text-orange-800/70">{content}</p>
      )}
    </div>
  );
}
```

**`StreamingBubble` integration:**

```tsx
// Render ThinkingPanel above the streaming text when content is present
{thinkingContent && (
  <ThinkingPanel content={thinkingContent} isThinking={isThinking} />
)}
```

`StreamingBubble` receives `thinkingContent` and `isThinking` as props passed from `ChatMessageList`, which reads them from `useSkillAgent`.

**Collapsed state after answer arrives:** Once the final `MessageBubble` renders (on `TEXT_MESSAGE_END`), the `ThinkingPanel` is no longer shown — it was part of the `StreamingBubble` which is replaced. The thinking content is ephemeral by design (Non-Goal: not persisted).

### API Changes

None — AG-UI `REASONING_*` events already flow through the stream from the backend. No backend changes required.

### CLI Surface

None — frontend rendering feature.

## Implementation Plan

### Phase 1: Hook extension (~0.25 day)
- [ ] Add `thinkingContent: string` and `isThinking: boolean` to `useSkillAgent` return type
- [ ] Subscribe to `onReasoningStartEvent`, `onReasoningMessageContentEvent`, `onReasoningEndEvent`
- [ ] Reset both on `onRunStartedEvent`
- [ ] Update existing `useSkillAgent` test: add `thinkingContent` and `isThinking` to expected keys

### Phase 2: ThinkingPanel component (~0.25 day)
- [ ] Create `ThinkingPanel.tsx` — collapsible panel, spinner when active, auto-collapse on finish
- [ ] Wire into `StreamingBubble` — pass `thinkingContent` + `isThinking` as props
- [ ] Wire `thinkingContent`/`isThinking` from `useSkillAgent` through `ChatMessageList` → `StreamingBubble`

### Phase 3: Tests + quality gate (~0.25 day)
- [ ] `ThinkingPanel` tests: shows content, shows spinner when isThinking, collapses when isThinking flips to false, expand/collapse toggle works
- [ ] `useSkillAgent` tests: `thinkingContent` updates on `REASONING_MESSAGE_CONTENT`, clears on new run
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `ThinkingPanel`: renders thinking content when provided
- [ ] `ThinkingPanel`: shows "Thinking…" label + spinner when `isThinking` is true
- [ ] `ThinkingPanel`: auto-collapses (expanded=false) when `isThinking` transitions to false
- [ ] `ThinkingPanel`: clicking the header toggles expanded/collapsed
- [ ] `ThinkingPanel`: shows "Thought process" label when not thinking (collapsed by default)
- [ ] `useSkillAgent`: `thinkingContent` accumulates via `reasoningMessageBuffer`
- [ ] `useSkillAgent`: `thinkingContent` and `isThinking` reset to `""` / `false` on `RUN_STARTED`

### Manual Testing
- [ ] Prompt a Claude thinking-model skill with a complex question → thinking panel appears before first token, text streams in while panel shows reasoning, panel auto-collapses when answer starts
- [ ] Prompt a non-thinking skill (Gemini Flash) → no thinking panel appears; three-dot indicator only
- [ ] Click thinking panel header → expands/collapses manually

## Security Considerations

- Reasoning content arrives via the authenticated AG-UI SSE stream — same trust level as message content
- No reasoning content is sent to any external service — stays within GCP project boundary (Axiom #9)
- `ThinkingPanel` renders plain text (not markdown) — no XSS surface

## Performance Considerations

- `thinkingContent` state updates are batched by React on every `REASONING_MESSAGE_CONTENT` event — same pattern as existing `messages` sync; no additional debouncing needed
- `ThinkingPanel` is not memoised (collapses and unmounts when StreamingBubble is replaced) — no leaked state

## Migration & Rollout

No migration. `thinkingContent` defaults to `""` — existing users see no change unless a thinking model is used.

## Success Criteria

- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes
- [ ] Thinking panel appears before first text token on a reasoning-capable model
- [ ] Panel auto-collapses when final answer starts streaming
- [ ] No panel appears for non-thinking models (graceful degradation)
- [ ] Manual expand/collapse works

## Open Questions

- **Which skills use thinking models?** The `ThinkingPanel` only appears when the backend actually emits `REASONING_*` events. Skills using Gemini Flash or GPT-4o will never trigger it. Document this in the skill builder UI eventually.
- **Show thinking content in finalised `MessageBubble`?** Currently thinking is ephemeral (only in `StreamingBubble`). If users want to review the reasoning after the fact, a "Show reasoning" toggle on the finalised bubble would require persisting `thinkingContent` through to `MessageBubble`. Deferred — non-goal for this doc.

## Related Documents

- [chat-message-rendering.md](implemented/chat-message-rendering.md) — StreamingBubble and useSkillAgent where this wires in
- [Product Axioms](../../product-axioms.md)

---

## Implementation Report

**Completed**: 2026-04-24
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
