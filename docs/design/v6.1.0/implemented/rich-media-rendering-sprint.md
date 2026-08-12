# RICH-MEDIA Sprint

**Sprint ID:** RICH-MEDIA
**Design doc:** [rich-media-rendering.md](rich-media-rendering.md)
**Created:** 2026-04-24
**Estimated duration:** 2.5 days (coding) + 0.25 day quality/smoke
**Scope:** Frontend + one backend endpoint
**Risk level:** Low (isolated rendering components, no shared state)

**Parent sequence position:** Item 1.3 (see [SEQUENCE.md](SEQUENCE.md))

## Goal

Add SVG, image, and PDF reference rendering to `ChatMarkdown` so agent output displays visually instead of as raw XML/broken links. Port v5's `pdf_utils.py` for the lightweight PDF page-count endpoint. All three render paths degrade gracefully and have no impact on existing text/table/citation rendering.

## Why now

- `chat-message-rendering` (1.1) is ✅ implemented — `MessageBubble` already uses `ChatMarkdown`, so new renderers plug in with zero MessageBubble changes
- `a2ui-tool-delivery` (1.0) is ✅ implemented — no further fenced-block changes pending
- Skills generating SVG diagrams (flowcharts, architecture diagrams) are blocked until SVG renders
- July workshop demo needs visual parity with v5

## Context (current state)

| Area | State |
|---|---|
| `frontend/src/components/chat/ChatMarkdown.tsx` | ✅ exists — react-markdown + remarkGfm + rehypeHighlight; handles `code`, `a`, `html` (strips), standard markdown elements |
| `frontend/src/components/chat/MessageBubble.tsx` | ✅ exists — already passes `message.content` to `<ChatMarkdown>` |
| `dompurify` | ❌ not installed |
| `@radix-ui/react-dialog` | ✅ installed (for lightbox) |
| `react-markdown`, `remark-gfm` | ✅ installed |
| `frontend/src/components/chat/media/` | ❌ directory doesn't exist yet |
| `backend/tools/media_utils.py` | ❌ not ported from v5 |
| v5 `pdf_utils.py` | ✅ at `<your-v5-source>/backend/tools/pdf_utils.py` — HTTP Range approach, clean logic |

## Milestones

| # | Milestone | Scope | Est | Depends | Pause? |
|---|---|---|---|---|---|
| M1 | Install dompurify; `SVGBlock.tsx` with DOMPurify sanitization; extend ChatMarkdown `code` renderer for `language-svg` and raw `<svg` blocks; unit tests | frontend | 0.75d | — | — |
| M2 | `InlineImage.tsx` with lazy loading, error fallback, Radix Dialog lightbox; add `img` renderer to ChatMarkdown; unit tests | frontend | 0.5d | M1 | — |
| M3 | Port v5 `pdf_utils.py` → `backend/tools/media_utils.py`; `GET /api/media/pdf-info` route with GCS URL validation + auth; `PDFCard.tsx` + `usePDFInfo` hook; extend ChatMarkdown `a` renderer for `.pdf` hrefs; backend + frontend tests | fullstack | 0.75d | M2 | — |
| M4 | `npm run quality:check:fast` + `make test-fast` pass; bundle size check (`npm run build`); smoke probe addition to `scripts/smoke-deployed.sh` | quality | 0.25d | M3 | **PAUSE** — user approves before sprint closes |

**Total:** ~2.25 days

## Quality Gates

- After every milestone: `cd frontend && npm run quality:check:fast` + `npm run test:run`
- After M3: `cd backend && make lint && make test-fast`
- After M4: `npm run build` output confirms DOMPurify delta < 25 KB gzipped
- CI gate (`.github/workflows/ci.yml`) passes on push

## Key Implementation Notes

**SVGBlock SSR safety:** `dompurify` requires browser DOM. `SVGBlock` uses `useEffect` + dynamic import so it only sanitizes client-side (renders null on SSR, hydrates on client — no hydration mismatch because the initial state is `''`).

**Image lightbox:** Radix `<Dialog>` already in `@radix-ui/react-dialog` — use it instead of native `<dialog>` (handles focus trap, a11y, portal). Reference the existing Radix pattern in the codebase.

**PDF URL validation:** Backend validates URL is `https://` + GCS hostname (`storage.googleapis.com` or `storage.cloud.google.com`) before making the HTTP Range request. Rejects others with 400.

**ChatMarkdown `code` renderer:** The existing renderer checks `className?.startsWith("language-")` for block detection. Extend it: if `className` is `language-svg` or `language-xml` and content `.trimStart().startsWith('<svg')`, delegate to `SVGBlock`. Otherwise fall through to existing code block rendering.

**ChatMarkdown `img` renderer:** Currently not in the `components` object — react-markdown uses its default (which passes through). Add an explicit `img` renderer delegating to `InlineImage`. This replaces the existing test at line 80-84 (`<img src=x onerror=alert(1)>` was raw HTML, which is still stripped by the `html()` renderer — markdown `![alt](url)` is separate).

**ChatMarkdown `a` renderer:** Extend existing `a` renderer: if `href` ends in `.pdf`, render `<PDFCard url={href} />` instead of an anchor. Preserve existing aitana:// and external https/http/mailto paths.

## File Map

```
frontend/src/components/chat/
  media/
    SVGBlock.tsx           ← new M1
    InlineImage.tsx        ← new M2
    PDFCard.tsx            ← new M3
    __tests__/
      SVGBlock.test.tsx    ← new M1
      InlineImage.test.tsx ← new M2
      PDFCard.test.tsx     ← new M3
  ChatMarkdown.tsx         ← extend (M1, M2, M3)

frontend/src/hooks/
  usePDFInfo.ts            ← new M3

backend/tools/
  media_utils.py           ← new M3 (port + route)

backend/fast_api_app.py    ← extend: include media_router (M3)
```
