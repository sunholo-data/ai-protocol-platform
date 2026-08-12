# Client Surface Correctness

**Status**: Planned
**Priority**: P2 (Low)
**Estimated**: 0.75 day
**Scope**: Frontend + MCP App artefacts
**Dependencies**: None
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

Three inherited client-surface defects. Each is small, each is visible to a
first-time user, and all three are inherited verbatim by every fork.

**Current State:**

- **#23 — the chat input sits below the fold.** The chat column is a flex child
  with no `min-h-0`, so it refuses to shrink below content height and pushes the
  input footer past the viewport. The AIPLA user hit it on their first
  end-to-end test: *"you need to scroll down a bit to see it — very bad UX
  initially as you can't see where you input to get started."* Their follow-up
  found `min-h-0` **necessary but not sufficient**: `app/layout.tsx` still gives
  `<body>` `min-h-screen` while the chat page claims `h-screen`, so any banner
  sibling (e.g. `LocalModeBanner`) steals visible height and pushes the input
  below the fold again.
- **#44 — the whole rendered markdown subtree remounts.** `ChatMarkdown` builds
  its react-markdown `components` object inline on every render. react-markdown
  treats each override as an element *type*, so a fresh object identity makes
  React **remount** rather than re-render the tree. Compounded by `MessageBubble`'s
  `React.memo` being defeated by unstable props. Surfaced as continuous SVG
  flicker, but the remount cost is generic.
- **#38 — artefacts are unusable in any MCP host but ours.** Reference artefacts
  emit `ui/update-model-context` with `structuredContent` only. Per SEP-1624,
  `content` is the model-facing field and `structuredContent` is machine-oriented.
  Our closed loop works *only because we are both the MCP server and the host*: a
  conformant external host feeds `content` to its model, so with none present the
  model sees nothing. Verified downstream against ChatGPT developer mode — the sim
  rendered, the model never saw the interactions.

**Impact:** #23 is the first thing a new user sees. #38 means every artefact a
fork builds is silently non-portable — invisible until a second host renders it.

## Goals

**Primary Goal:** The chat input is always visible, the message tree does not
remount on unrelated re-renders, and reference artefacts work in a host we did
not write.

**Success Metrics:**

- Chat input visible on first paint at common viewport heights, **with and
  without** a banner rendered above the app shell.
- A parent re-render does not remount rendered markdown (assert stable DOM node identity).
- Reference artefacts emit both `content` and `structuredContent`, single-sourced.

**Non-Goals:**

- A general chat-layout refactor. #23's own note proposes a `FlexCol` utility;
  that is a larger change than the fix warrants.
- Reworking our own iframe bridge to *prefer* `content`. We consume
  `structuredContent` programmatically, which is its intended use; the gap is
  only the missing `content` for model-facing hosts.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | #44 removes a full subtree remount from every parent re-render |
| 2 | EARNED TRUST | +1 | An input below the fold and flickering diagrams both read as "broken" |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | 0 | — |
| 6 | PROTOCOL OVER CUSTOM | +1 | #38 brings artefacts into line with SEP-1624's audience split |
| 7 | API FIRST | 0 | — |
| 8 | OBSERVABLE BY DEFAULT | 0 | — |
| 9 | SECURE BY CONSTRUCTION | 0 | — |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | A conformant host needs no bespoke knowledge of our field choice |
| | **Net Score** | **+4** | Threshold: >= +4 |

**Conflict Justifications:** None — no axiom scores -1.

## Design

### #23 — viewport ownership

Adopt the structural fix, not just the one-character one. The body owns the
viewport; pages fill their parent:

```tsx
// app/layout.tsx
- <body className="... min-h-screen ...">
+ <body className="... h-screen flex flex-col ...">
    <LocalModeBanner />
-   <AppProviders>{children}</AppProviders>
+   <div className="flex-1 min-h-0 flex flex-col overflow-auto">
+     <AppProviders>{children}</AppProviders>
+   </div>
  </body>

// app/chat/[...path]/page.tsx
- <main className="flex h-screen flex-col">
+ <main className="flex h-full min-h-0 flex-col">
```

…plus the missing `min-h-0` on the chat column itself. The rule worth writing
down: **any full-viewport page sibling-coupled with a banner in the root layout
hits this**; "body owns the viewport, children get `flex-1`" is the robust shape.

### #44 — stable identity

`useMemo` the `components` object on its real dependencies, memo `ChatMarkdown`,
hoist per-message callbacks to `useCallback`, and share a stable empty-array
constant so `toolCallsByParent[m.id] ?? []` stops minting a new array per render.

Worth a comment at the definition: *a react-markdown `components` map must have
stable identity or it remounts the tree* — an easy and expensive mistake to copy.

### #38 — emit both fields

Reference artefacts (`frontend/src/_sim-template/`, the sandbox artefact
scaffold, and the `mcp-app-artefact` skill's template) emit a `content` text
block **derived from the same label/state** as `structuredContent`, so the two
cannot semantically diverge. Additive: our frontend keeps reading
`structuredContent`, so there is no in-app behaviour change.

Also fold in the July addendum: host detection must key on the `ui/initialize`
handshake's `serverInfo.name`, **not** `window.openai`. That global is a
ChatGPT-ism; Copilot injects it only as a compat shim and standards-conformant
hosts do not inject it at all, so branching on it mis-detects. Deny-by-default
when there is no signal.

## Implementation Plan

### Phase 1: Layout (~0.25 day)
- Layout + chat page changes; test at short viewport heights, with and without a banner.

### Phase 2: Render stability (~0.25 day)
- `ChatMarkdown` memoisation + stable callbacks; test asserting no remount across a parent re-render.

### Phase 3: Artefact portability (~0.25 day)
- Dual-field emission in the reference artefacts + handshake-based host detection.

## Migration & Rollout

**Feature Flags:** None.

**Rollback Plan:** All three are self-contained reverts.

**Environment Variables:** None.

## Testing Strategy

Vitest for #23 and #44. For #44 the assertion must be **node identity across a
re-render** — a "renders correctly" test passes against the broken version.

#38 cannot be proven by jsdom: per the repo's standing rule, an artefact change
is not done until a real host confirms it. Verify in at least one external host
(MCP Inspector is the cheapest) that the model receives the interaction text.

## Success Criteria

- [ ] Chat input visible on first paint at 700px viewport height, banner present and absent
- [ ] Rendered markdown DOM nodes survive a parent re-render (no remount)
- [ ] Reference artefacts emit both `content` and `structuredContent` from one source
- [ ] Host detection uses `serverInfo.name`, not `window.openai`
- [ ] An external MCP host's model receives artefact interaction data

## Related Documents

- AIPLA upstream feedback #23, #38, #44
- [mcp-app-artefact skill](../../../.claude/skills/mcp-app-artefact/SKILL.md)
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md)
