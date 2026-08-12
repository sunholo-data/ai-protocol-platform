# Rich Media Rendering in Chat

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 3 days
**Scope**: Frontend
**Dependencies**: frontend-architecture (v6.0.0), streaming-and-protocols (v6.0.0)
**Created**: 2026-04-23
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- v6 chat renders plain text only — no SVG, no inline images, no document reference cards
- v5's SVG rendering was a well-used feature: agents generated diagrams, flowcharts, and graphs as raw `<svg>` blocks in their response text and they displayed inline in the chat
- v5's image component (`Image.tsx`) and SVG component (`SVG.tsx`) are not ported — the v6 `ChatMarkdown` component (not yet written) has no equivalents
- v5 used `dangerouslySetInnerHTML` for SVG with no sanitization — a known XSS risk that must not be carried forward

**Use cases not yet served:**
1. Agent returns an SVG diagram (flowchart, architecture, graph) as text — currently displayed as raw XML text
2. Agent references an image by URL in its response — currently displayed as a broken markdown link
3. Agent references a PDF document by URL (e.g., a retrieved document) — currently displayed as a plain anchor link
4. User uploads an image alongside a message — no inline preview

**Impact:**
- Skill authors — blocked from building diagram/chart generation skills until SVG rendering exists
- End users — degraded experience vs. v5 for any visual output
- Talk demo (July 2026) — v6 must visually match or exceed v5's capability

## Goals

**Primary Goal:** Render SVG, images, and PDF references inline in the AG-UI chat stream, with better security than v5 and no meaningful bundle-size regression.

**Success Metrics:**
- SVG output from agents renders visually within the chat bubble (no raw XML text visible)
- Images referenced by URL in AI responses display inline with error fallback
- PDF references display a card showing filename and page count (if available), not a plain link
- No XSS vector introduced (DOMPurify sanitization passes a manual review)
- Bundle size delta < 25 KB gzipped from adding DOMPurify

**Non-Goals:**
- Full PDF viewer / in-chat PDF editing (handled by document workspace — see [document-ui.md](../../v6.1.0/document-ui.md))
- Video/audio playback (separate feature if needed)
- Client-side SVG editing or manipulation
- Rendering A2UI blocks inside chat messages (A2UI is for the document workspace pane; chat is text + rich media)
- User file upload UI (handled separately; this doc covers rendering only)

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Images/SVGs render as they stream in; lazy loading prevents layout jank |
| 2 | EARNED TRUST | +1 | Users can see exactly what the agent produced (diagram, image) rather than guessing from text description |
| 3 | SKILLS, NOT FEATURES | 0 | No new skill concept introduced; this is rendering infrastructure |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | All render paths have fallbacks (broken image → icon + alt text; SVG parse failure → code block; PDF → plain link) |
| 6 | PROTOCOL OVER CUSTOM | +1 | SVG is W3C standard; images use standard markdown `![alt](url)`; PDF card uses standard `<a>` links |
| 7 | API FIRST | 0 | Frontend-only; one optional lightweight backend endpoint for PDF metadata |
| 8 | OBSERVABLE BY DEFAULT | 0 | No new data capture surface |
| 9 | SECURE BY CONSTRUCTION | +1 | DOMPurify replaces v5's unsanitized `dangerouslySetInnerHTML` for SVG; images served via Next.js `<Image>` or constrained `<img>` |
| 10 | THIN CLIENT, FAT PROTOCOL | -1 | DOMPurify adds ~25 KB gzipped; no client-side PDF.js (too heavy); tradeoff is acceptable — see justification |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ |

**Conflict Justifications:**
- **THIN CLIENT (−1):** DOMPurify (~25 KB gzipped) is the minimum safe approach to rendering user-visible SVG from agent output. The alternative — stripping SVG support entirely — is worse for the July demo and skill authors. A custom sanitizer would be heavier and less trusted. We accept this cost once, not repeatedly: DOMPurify is a single shared dependency, not per-feature bloat.

## Design

### Overview

Add a `ChatMarkdown` component that renders AG-UI text messages with react-markdown, registering custom renderers for SVG blocks, images, and PDF links. SVG is sanitized client-side with DOMPurify before rendering. PDF references become lightweight `PDFCard` components that show filename + page count fetched from a backend endpoint. All components degrade gracefully to plain links or text if rendering fails.

The design avoids a full PDF.js integration (too heavy) and any server-side HTML rendering (would break streaming). Media is always rendered where it naturally appears in the text stream — no separate "attachments" panel needed for this feature.

### Frontend Changes

**New Components:**

```
frontend/src/components/chat/
  ChatMarkdown.tsx         — react-markdown wrapper with custom renderers (SVG, img, PDF links)
  media/
    SVGBlock.tsx           — DOMPurify-sanitized inline SVG renderer
    InlineImage.tsx        — <img> with lazy loading, error boundary, lightbox toggle
    PDFCard.tsx            — PDF reference card (filename, page count, download link)
```

**`ChatMarkdown.tsx`** — central component wired into the chat message bubble:
- Uses `react-markdown` + `remark-gfm` (already in v6 dependencies from v5 port research)
- Tokenises text with `marked.lexer()` for block-level memoisation (same pattern as v5 `markdown.tsx`)
- Registers custom renderers:
  - `code` block renderer: if language is `svg` or content starts with `<svg`, delegates to `SVGBlock`
  - `img` renderer: delegates to `InlineImage`
  - `a` renderer: if `href` ends in `.pdf`, renders `PDFCard`; otherwise standard anchor

**SVG detection — two entry points:**
1. Fenced code block with language `svg` or `xml` where content starts with `<svg` — explicit, preferred by skill authors
2. Raw inline `<svg ...>` tag in the markdown text — detected by `text.trimStart().startsWith('<svg')` check at the block tokenisation level, same heuristic as v5

**`SVGBlock.tsx`:**
```tsx
import DOMPurify from 'dompurify'

// Config: allow SVG elements and presentation attributes, strip scripts/handlers
const PURIFY_CONFIG = {
  USE_PROFILES: { svg: true, svgFilters: true },
  FORBID_TAGS: ['script', 'use'],
  FORBID_ATTR: ['xlink:href', 'href'],   // prevent SSRF via SVG references
}

export function SVGBlock({ svgString }: { svgString: string }) {
  const clean = DOMPurify.sanitize(svgString, PURIFY_CONFIG)
  if (!clean) return <CodeFallback code={svgString} language="svg" />
  return (
    <div
      className="svg-container my-4 overflow-x-auto rounded border border-border p-2"
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  )
}
```

**`InlineImage.tsx`:**
- `<img>` (not `next/image` — dynamic URLs from agents are not known at build time, cannot use `domains` allowlist)
- `loading="lazy"`, `decoding="async"` for performance
- `onError`: replace with a broken-image icon + alt text chip
- Lightbox: click → modal with `<dialog>` (native, zero dependency)
- Width constrained to `max-w-full` within chat bubble

**`PDFCard.tsx`:**
- Renders a card chip: PDF icon + filename + page count badge + download arrow
- Page count: fetched from `GET /api/media/pdf-info?url=<encoded>` (see backend)
- Falls back gracefully to just the filename if endpoint returns error
- Opens PDF in new tab on click (no inline viewer)

**Modified Components:**
- `src/components/chat/MessageBubble.tsx` (to be created in chat rendering phase) — swap plain `<p>` text rendering for `<ChatMarkdown>`

**State Management:**
- No new contexts or global state
- `PDFCard` uses a local `usePDFInfo(url)` hook with an in-memory cache (Map) to avoid duplicate requests per session

### Backend Changes

**New Endpoint:**
- `GET /api/media/pdf-info` — lightweight PDF metadata fetch
  - Query param: `url` (URL-encoded GCS signed URL or public URL)
  - Returns: `{ pages: number | null, filename: string }`
  - Implementation: port `count_pdf_pages_from_signed_uri()` from v5's `backend/tools/pdf_utils.py` (HTTP Range request, reads first 2 KB, regex on `/Count` or `/N` trailer)
  - Auth: requires valid Firebase ID token (standard middleware)
  - Cached in Firestore `media_metadata/{url_hash}` with 24h TTL to avoid repeated HTTP Range requests

**New Module:**
- `backend/tools/media_utils.py` — port of v5 `pdf_utils.py` + route handler

**No data model changes** — the Firestore `media_metadata` cache collection is created on first write, no migration needed.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/api/media/pdf-info` | Return PDF page count for a URL | No (new) |

### Architecture Diagram

```
AG-UI text stream
      │
      ▼
ChatMarkdown (react-markdown + remark-gfm)
      │
      ├─ SVG block/inline ──► SVGBlock ──► DOMPurify.sanitize() ──► dangerouslySetInnerHTML
      │
      ├─ ![alt](url) ────────► InlineImage ──► <img loading="lazy"> + error fallback
      │
      └─ [text](*.pdf) ──────► PDFCard ──► usePDFInfo(url)
                                                    │
                                                    └─► GET /api/media/pdf-info
                                                              │
                                                              └─► PDF HTTP Range req (2 KB)
                                                              └─► Firestore cache (24h TTL)
```

### CLI Surface

This is a rendering-only frontend feature; no new developer-facing resources or processes. No CLI commands required.

## Implementation Plan

### Phase 1: SVG rendering (~0.75 day)
- [ ] Add `dompurify` + `@types/dompurify` to `frontend/package.json` (~5 lines)
- [ ] Write `SVGBlock.tsx` with DOMPurify config (~60 lines)
- [ ] Write `ChatMarkdown.tsx` skeleton with SVG detection in `code` and block renderers (~80 lines)
- [ ] Unit tests: sanitize benign SVG, strip `<script>`, strip `xlink:href`, fallback on empty output (~50 lines)

### Phase 2: Image rendering (~0.5 day)
- [ ] Write `InlineImage.tsx` with lazy loading, error state, lightbox dialog (~70 lines)
- [ ] Register `img` renderer in `ChatMarkdown.tsx` (~10 lines)
- [ ] Unit tests: renders src, shows fallback on error, lightbox opens (~40 lines)

### Phase 3: PDF references (~0.75 day)
- [ ] Port `pdf_utils.py` → `backend/tools/media_utils.py`, add FastAPI route `GET /api/media/pdf-info` (~80 lines)
- [ ] Write `PDFCard.tsx` + `usePDFInfo` hook (~90 lines)
- [ ] Register `a` renderer in `ChatMarkdown.tsx` for `.pdf` hrefs (~15 lines)
- [ ] Backend pytest for PDF range parsing + route (~40 lines)
- [ ] Frontend unit tests: card renders filename, page count shows after fetch, falls back on error (~40 lines)

### Phase 4: Integration (~1 day)
- [ ] Wire `ChatMarkdown` into `MessageBubble` (or equivalent chat rendering component)
- [ ] Manual testing against live streaming session with SVG-generating skill
- [ ] Bundle size check: `npm run build` → confirm delta < 25 KB gzipped
- [ ] Smoke probe addition: `scripts/smoke-deployed.sh` curl for `/api/media/pdf-info` 200

## Migration & Rollout

**Database Migrations:**
- `media_metadata` Firestore collection created on first write — no migration required
- Firestore rules: allow read/write only if `request.auth != null` (reuse existing auth pattern)

**Feature Flags:**
- None required — SVG and image rendering are purely additive; existing text messages are unaffected

**Rollback Plan:**
- Remove `ChatMarkdown` registration from `MessageBubble` (one line) → falls back to plain text rendering
- Backend route is isolated in `media_utils.py`; removing it does not affect any other route

**Environment Variables:**
- None new

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `SVGBlock`: clean SVG renders, XSS payload stripped, empty result renders CodeFallback
- [ ] `SVGBlock`: `<use>` tags removed, `xlink:href` attributes stripped
- [ ] `InlineImage`: renders src, alt text, lazy loading attribute
- [ ] `InlineImage`: onError replaces with icon + alt text
- [ ] `PDFCard`: renders filename, shows spinner while fetching, shows page count on success
- [ ] `PDFCard`: shows filename only on 404 from `/api/media/pdf-info`
- [ ] `ChatMarkdown`: SVG code block → `SVGBlock`, `![alt](url)` → `InlineImage`, `[text](x.pdf)` → `PDFCard`

### Backend Tests (pytest)
- [ ] `count_pdf_pages_from_uri`: valid PDF header returns integer
- [ ] `count_pdf_pages_from_uri`: non-PDF URL returns `None` without exception
- [ ] `GET /api/media/pdf-info`: returns 200 + JSON for valid URL
- [ ] `GET /api/media/pdf-info`: returns 401 for missing auth token
- [ ] `GET /api/media/pdf-info`: returns `{ pages: null }` on fetch error (not 500)

### Manual Testing
- [ ] Trigger a skill that generates an SVG diagram; verify it renders (not raw XML) in the chat
- [ ] Verify SVG with embedded `<script>` is stripped (use a test payload)
- [ ] Post a message with a GCS-signed PDF URL; verify PDFCard shows filename + page count
- [ ] Post a message with an external image URL; verify InlineImage renders
- [ ] Break an image URL; verify error fallback shows alt text
- [ ] Test on mobile viewport — SVG and images should not overflow the chat bubble

## Security Considerations

- **SVG XSS**: DOMPurify with `USE_PROFILES: { svg: true }` is the established defence. We additionally strip `<use>` (can reference external SVGs) and `href`/`xlink:href` attributes (SSRF vector). Config is a named constant, not inline, so it can be audited and updated centrally.
- **Image SSRF**: `InlineImage` renders whatever URL the agent provides. This is acceptable — the agent is a trusted internal service (not user-controlled content); images are fetched by the user's browser, not the backend. We do not proxy images through the backend.
- **PDF metadata fetch**: backend endpoint makes an outbound HTTP Range request. URL is provided by the frontend. Mitigate URL-injection risk: validate URL is `https://` prefixed and matches GCS hostname pattern (`storage.googleapis.com` or `storage.cloud.google.com`) before fetching; reject others with 400. This prevents the endpoint being used as an open HTTP proxy.
- **Auth**: `/api/media/pdf-info` requires a valid Firebase ID token — standard middleware, no new auth surface.

## Performance Considerations

- **Bundle size**: DOMPurify ~25 KB gzipped. Acceptable per axiom conflict justification. No PDF.js — the PDF viewer that would add ~300 KB is explicitly excluded.
- **SVG rendering**: synchronous DOMPurify call; benchmarks show <1 ms for typical agent-generated SVGs (<50 KB). No async rendering path needed.
- **Image lazy loading**: `loading="lazy"` prevents off-screen images from blocking the initial paint.
- **PDF info caching**: in-memory Map in `usePDFInfo` prevents duplicate requests within a session. Firestore TTL cache (24h) prevents repeated backend HTTP Range requests across sessions. No Redis or additional infra needed.
- **Streaming**: `ChatMarkdown` uses the same block-level memoisation pattern as v5 `markdown.tsx` — only the changed block re-renders as tokens stream in.

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] Lint and typecheck clean (`cd frontend && npm run quality:check:fast`)
- [ ] Manual SVG render test: skill-generated SVG displays as rendered diagram (not raw XML)
- [ ] Security: SVG with `<script>alert(1)</script>` injected is stripped before render
- [ ] PDF reference card shows filename and page count for a known GCS PDF URL
- [ ] Image from agent response renders inline with lazy loading (`loading="lazy"` in DOM)
- [ ] Bundle size delta < 25 KB gzipped confirmed via `npm run build` output
- [ ] Smoke probe: `scripts/smoke-deployed.sh dev backend` includes `/api/media/pdf-info` 401 check (no-auth → 401 expected)

## Open Questions

- **Lightbox library**: native `<dialog>` is zero-dependency but requires manual focus-trap polyfill for a11y. If we have a Radix `<Dialog>` already wired in the frontend, use it instead. Check when `MessageBubble` is being built.
- **Image domains allowlist**: if we later switch to `next/image` for agent-generated images, we need a known set of allowed domains (GCS, potentially model provider image endpoints). Defer until the domain set is known.
- **SVG max size**: should we cap SVG size (e.g., 500 KB) before passing to DOMPurify to prevent deliberate DoS via massive SVG? Add a `MAX_SVG_BYTES = 512_000` guard at the `ChatMarkdown` block renderer level.
- **Mermaid / D2 diagrams**: agents could output Mermaid syntax in a `mermaid` fenced code block. Mermaid.js rendering is a natural follow-on to SVG rendering (Mermaid compiles to SVG). Keep in scope for a follow-on doc if skill authors request it.

## Related Documents

- [document-ui.md](../../v6.1.0/document-ui.md) — A2UI document workspace (separate from chat rendering)
- [frontend-architecture.md](implemented/frontend-architecture.md) — AG-UI provider, `useSkillAgent` hook
- [streaming-and-protocols.md](implemented/streaming-and-protocols.md) — AG-UI event types
- [local-dev-cli.md](local-dev-cli.md) — CLI reference (no new commands for this feature)
- [Product Axioms](../../product-axioms.md)
- [Mockup](../../frontend/public/mockups/document-workspace.html)

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
