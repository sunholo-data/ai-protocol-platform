# Chat Markdown Rendering

**Status**: Implemented
**Priority**: P0 (High)
**Estimated**: 0.5 days
**Scope**: Frontend
**Dependencies**: [chat-message-rendering.md](implemented/chat-message-rendering.md) ✅
**Created**: 2026-04-24
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- `MessageBubble` renders bot message text as `<p className="whitespace-pre-wrap">` — raw string output
- Agent responses contain markdown (`**bold**`, `*lists*`, headings, code blocks, tables) that renders as literal asterisks and hashes
- The first live test showed this clearly: every `**keyword:**` appears unstyled

**Impact:**
- Every agent response looks broken to users — the formatting the model produces is invisible
- Tables render as raw `| col | col |` pipe strings
- Code snippets appear as indented plain text rather than highlighted blocks

## Goals

**Primary Goal:** Render markdown in bot `MessageBubble` text segments using `react-markdown` + GFM + syntax highlighting — replacing the `<p>` fallback.

**Success Metrics:**
- `**bold**` renders as `<strong>`, `*italic*` as `<em>`
- GFM tables render as styled `<table>` elements
- Fenced code blocks render with syntax highlighting (`rehype-highlight`, already installed)
- `aitana://` links delegated to `InlineCitation` chip via custom `a` renderer
- No XSS: `react-markdown` does not use `dangerouslySetInnerHTML`; HTML passthrough disabled
- Zero new dependencies (all three packages already in `package.json`)

**Non-Goals:**
- SVG / image / PDF rendering (that is [rich-media-rendering.md](rich-media-rendering.md))
- LaTeX / math rendering
- User message markdown (user bubbles stay plain text)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No latency impact — rendering only |
| 2 | EARNED TRUST | +1 | Styled citations and tables make sourced data legible and verifiable |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; no new user concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | Falls back gracefully; `react-markdown` never throws on malformed input |
| 6 | PROTOCOL OVER CUSTOM | +1 | `react-markdown` + GFM is the standard React markdown path; no custom parser |
| 7 | API FIRST | 0 | Frontend-only |
| 8 | OBSERVABLE BY DEFAULT | 0 | No new data surface |
| 9 | SECURE BY CONSTRUCTION | +1 | `react-markdown` safe by default; `html()` renderer override strips raw HTML (XSS prevention) |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Pure presentation layer; zero business logic |
| | **Net Score** | **+5** | Threshold: >= +4 ✓ |

## Design

### Overview

Create a `ChatMarkdown` component wrapping `react-markdown` with: GFM tables, syntax highlighting, and a custom `a` renderer that intercepts `aitana://` links and delegates to `InlineCitation`. Wire into `MessageBubble` in place of the `<p>` fallback.

### Frontend Changes

**New component:** `frontend/src/components/chat/ChatMarkdown.tsx`

**Modified:** `frontend/src/components/chat/MessageBubble.tsx` — swap `<p>` + `renderWithCitations` for `<ChatMarkdown>`

**`ChatMarkdown.tsx`:**

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { InlineCitation } from "@/components/chat/InlineCitation";

interface ChatMarkdownProps {
  content: string;
  navigateToBlock: (docId: string, blockId: string) => void;
}

export function ChatMarkdown({ content, navigateToBlock }: ChatMarkdownProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        a({ href, children }) {
          return (
            <InlineCitation href={href ?? "#"} navigateToBlock={navigateToBlock}>
              {children}
            </InlineCitation>
          );
        },
        // Strip raw HTML passthrough — prevents XSS from agent output
        html() { return null; },
      }}
      className="prose prose-sm max-w-none"
    >
      {content}
    </ReactMarkdown>
  );
}
```

**`MessageBubble.tsx` change** (one-line swap):

```tsx
// Before:
<p className="whitespace-pre-wrap">
  {renderWithCitations(seg.text, navigateToBlock)}
</p>

// After:
<ChatMarkdown content={seg.text} navigateToBlock={navigateToBlock} />
```

`renderWithCitations` is retired — the custom `a` renderer handles `aitana://` interception directly.

**Tailwind typography:** Check if `@tailwindcss/typography` is installed (`package.json`). If yes, add plugin to `tailwind.config.ts`. If not, style markdown elements with Tailwind utility classes in `ChatMarkdown` (adds ~30 lines, avoids a dep).

**Packages already in `package.json` — no new deps required:**
- `react-markdown: ^9.0.0` ✅
- `remark-gfm: ^4.0.0` ✅
- `rehype-highlight: ^7.0.0` ✅

### API Changes

None.

### CLI Surface

None — pure rendering feature.

## Implementation Plan

### Phase 1: Component + wire-in (~0.25 day)
- [ ] Check `@tailwindcss/typography` presence; add if needed
- [ ] Create `ChatMarkdown.tsx`
- [ ] Replace `<p>` + `renderWithCitations` in `MessageBubble` with `<ChatMarkdown>`
- [ ] Remove `renderWithCitations` export from `InlineCitation.tsx` (now unused)

### Phase 2: Tests + quality gate (~0.25 day)
- [ ] `ChatMarkdown` tests: bold, GFM table, code block, aitana:// chip, XSS strip
- [ ] Update `MessageBubble` tests for markdown-rendered output
- [ ] `npm run quality:check:fast` + `npm run test:run` clean

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `**bold**` → `<strong>` in DOM
- [ ] GFM table `| a | b |` → `<table>` with correct cells
- [ ] Fenced code block → element with highlight class
- [ ] `[label](aitana://doc/d1/block/b1)` → `InlineCitation` chip, not `<a>`
- [ ] `[label](https://external.com)` → plain `<a>` (InlineCitation fallback)
- [ ] `<script>alert(1)</script>` in content → stripped from output (XSS check)

### Manual Testing
- [ ] "Summarize in a table" → GFM table renders in bot bubble
- [ ] "Give me a code example" → fenced block with syntax highlighting
- [ ] Agent response with `[Source](aitana://doc/x/block/y)` → teal chip appears
- [ ] User bubbles still render as plain text (no markdown processing)

## Security Considerations

- `react-markdown` does not use `dangerouslySetInnerHTML` — safe by default
- `html()` renderer override returns `null` — raw HTML tags in agent output stripped
- `InlineCitation` already blocks open redirects

## Migration & Rollout

No migration. Reverting to `<p>` is a one-line change in `MessageBubble` if needed.

## Success Criteria

- [ ] `npm run test:run` passes
- [ ] `npm run quality:check:fast` passes
- [ ] Bot messages render formatted markdown (bold, lists, tables, code)
- [ ] `aitana://` links appear as teal chips
- [ ] Raw HTML in agent content stripped (XSS check passes)
- [ ] User bubbles remain plain text

## Related Documents

- [chat-message-rendering.md](implemented/chat-message-rendering.md) — MessageBubble where ChatMarkdown wires in
- [rich-media-rendering.md](rich-media-rendering.md) — SVG/image/PDF on top of ChatMarkdown
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
