# Sprint: Document UI — Split-Pane Preview (1.10)

**Sprint ID:** DOC-UI-IMPL
**Design doc:** [document-ui.md](../document-ui.md)
**Scope:** Fullstack (small backend addition, majority frontend)
**Estimated:** 2 days (actual: 2 days)
**Status:** ✅ Shipped 2026-04-25 — commits 5b02d8f (M0) → 730435d (M1) → 1038056 (M2) → 8b32aef (sprint complete) + 2026-04-25 blocks-direct pivot (see [document-rendering-decision.md](document-rendering-decision.md))

---

## Sprint Goal

When a user clicks a document in the file browser, a split-pane opens showing the parsed document (rendered via A2UIRenderer) alongside the chat. The AI already has the document in context (DOC-AI-PIPELINE ✅) — this sprint makes the user see it too.

---

## What's Already Done

| Component | Status |
|-----------|--------|
| a2ui parse at upload time | ✅ `upload.py` calls `output_format="a2ui"`, stores `a2uiComponents` in Firestore |
| Pydantic model `a2uiRoot` + `a2uiComponents` fields | ✅ `db/models/document.py` |
| `A2UIRenderer` component | ✅ `frontend/src/components/protocols/A2UIRenderer.tsx` |
| `DocTabsBar` + `activeTabId` state | ✅ in `chat/[skillId]/page.tsx` — wired but goes nowhere |
| Backend `document_id` plumbing in stream | ✅ DOC-AI-PIPELINE sprint |
| Folder/list document routes | ✅ `tools/documents/routes.py` |

## What's Missing

| Gap | Milestone |
|-----|-----------|
| `GET /api/documents/{docId}` endpoint | M0 |
| `DocumentHeader`, `DocumentFooter`, `DocumentViewer`, `DocumentPanel` components | M1 |
| `useDocument(docId)` hook | M1 |
| Split-pane layout in chat page | M2 |
| `activeTabId` passed as `documentId` in AG-UI stream request | M3 |

---

## Pre-flight

Before any new code:
```bash
cd backend && make test-fast   # confirm clean baseline (should be 630+ passing)
cd frontend && npm run quality:check:fast
```

---

## Milestones

### M0 — Backend: GET /api/documents/{docId} (~0.25d)
**Scope:** backend · **Depends on:** nothing (Firestore data already there)

**Context:** `GET /api/documents/{docId}` does not exist. Only folder/list routes are in `tools/documents/routes.py`. The frontend needs this to fetch the a2ui data for a selected document.

**Tasks:**
1. Add to `backend/tools/documents/routes.py`:
   ```python
   @router.get("/api/documents/{doc_id}")
   def get_document(doc_id: str, user: _CurrentUser) -> dict:
       doc = folders_db.get_document(user_id=user.uid, doc_id=doc_id)
       if doc is None:
           raise HTTPException(status_code=404, detail="Document not found")
       if doc.get("userId") != user.uid:
           raise HTTPException(status_code=403, detail="Access denied")
       return doc
   ```
2. Check `db/folders.py` — if `get_document(user_id, doc_id)` doesn't exist, add a simple Firestore read
3. Confirm the stored `a2uiComponents` shape by reading a real uploaded doc from Firestore (or from a test fixture) — verify it's a dict with `root` + `components` keys, or a raw list

**Acceptance criteria:**
- [ ] `GET /api/proxy/api/documents/{doc_id}` returns 200 with `a2uiComponents` field for a parsed doc
- [ ] Returns 404 for unknown doc, 403 for another user's doc
- [ ] `pytest tests/api_tests/test_upload.py` — existing tests still pass
- [ ] Add 3 unit tests: happy path, not found, wrong user

---

### M1 — Frontend: DocumentPanel components + useDocument hook (~0.75d)
**Scope:** frontend · **Depends on:** M0

**Files to create:**
- `frontend/src/hooks/useDocument.ts`
- `frontend/src/components/document/DocumentHeader.tsx`
- `frontend/src/components/document/DocumentFooter.tsx`
- `frontend/src/components/document/DocumentViewer.tsx`
- `frontend/src/components/document/DocumentPanel.tsx`

**`useDocument` hook:**
```typescript
// frontend/src/hooks/useDocument.ts
export function useDocument(docId: string | null) {
  // fetchWithAuth GET /api/proxy/api/documents/{docId}
  // returns { doc: ParsedDocument | null, isLoading, error }
}
```

**`DocumentHeader`:**
- Filename (truncated), format badge (matches existing `DocListItem` badge style)
- "Open original" link (GCS signed URL from `sourceUrl`)
- Parse date from `parsedAt`

**`DocumentFooter`:**
- Summary stats from `summary` field: `{totalBlocks} blocks · {tables} tables · {images} images · {changes} changes`
- Only render stat if > 0

**`DocumentViewer`:**
```typescript
// Uses A2UIRenderer (already built at frontend/src/components/protocols/A2UIRenderer.tsx)
// a2uiComponents from Firestore is stored as the full a2ui spec dict {root, components}
// Pass directly to A2UIRenderer
// Loading skeleton while fetching
// Error state with "Document preview unavailable" fallback
```

**`DocumentPanel`:**
```typescript
interface DocumentPanelProps { docId: string }
// Composes: DocumentHeader + ScrollArea(DocumentViewer) + DocumentFooter
// Uses useDocument(docId) to fetch data
```

**Acceptance criteria:**
- [ ] `DocumentPanel` renders `A2UIRenderer` with document a2ui data when `a2uiComponents` is present
- [ ] Shows loading skeleton while `useDocument` is fetching
- [ ] Shows "Document preview unavailable" gracefully when `a2uiComponents` is null/absent
- [ ] `DocumentHeader` shows filename, format badge, and source link
- [ ] `DocumentFooter` shows block/table/image/change counts (suppresses zeros)
- [ ] Vitest: `DocumentPanel` renders header + footer from fixture data
- [ ] Vitest: loading state renders skeleton
- [ ] `npm run quality:check:fast` passes

---

### M2 — Frontend: Split-pane layout in chat page (~0.5d)
**Scope:** frontend · **Depends on:** M1

**Target file:** `frontend/src/app/chat/[skillId]/page.tsx`

**Current layout** (lines ~196–240 in ChatShell return):
```
<div className="flex min-w-0 flex-1 flex-col">
  <ChatMessageList ... />
  <footer>...</footer>
</div>
```

**New layout** — when `activeTabId` is set, split horizontally:
```tsx
<div className="flex min-h-0 flex-1 overflow-hidden">
  {activeTabId && (
    <div className="w-1/2 shrink-0 overflow-hidden border-r">
      <DocumentPanel docId={activeTabId} />
    </div>
  )}
  <div className="flex min-w-0 flex-1 flex-col">
    <ChatMessageList ... />
    <footer>...</footer>
  </div>
</div>
```

- No resizable handle in this sprint — 50/50 CSS split is fine. Resize handle is a v6.2 enhancement.
- When `activeTabId` is null (no tab open), full-width chat — exactly current behaviour.
- Import `DocumentPanel` from `@/components/document/DocumentPanel`.

**Acceptance criteria:**
- [ ] Opening a parsed document in the file browser shows split-pane: document left, chat right
- [ ] Closing the active tab (or having no tab) shows full-width chat (no regression)
- [ ] `npm run quality:check:fast` passes
- [ ] Manual smoke: upload a DOCX → green dot → click doc → split-pane appears with content

---

### M3 — Frontend: Pass document_id in AG-UI stream request (~0.25d)
**Scope:** frontend · **Depends on:** M2

**Context:** The backend (DOC-AI-PIPELINE sprint) already reads `documentId` from the HTTP request body and injects it into the session state so the AI loads the document artifact. The frontend never sends it.

**Target:** `frontend/src/hooks/useSkillAgent.ts` (or wherever `sendMessage` POSTs to the stream endpoint)

1. Find where the AG-UI stream request body is built in `useSkillAgent`
2. Accept `documentId?: string` — either as a prop or read from a shared context
3. Include `documentId` in the request body when set

**Simplest approach:** expose a `setDocumentId` setter from `useSkillAgent`, or accept it as a parameter to `sendMessage`. The `ChatShell` already has `activeTabId` — pass it through.

**Acceptance criteria:**
- [ ] When `activeTabId` is set and user sends a message, the stream request body includes `documentId`
- [ ] Verify in browser Network tab: POST to stream endpoint body has `"documentId": "<id>"`
- [ ] When no tab is active, `documentId` is absent from request (no regression)
- [ ] `npm run quality:check:fast` passes

---

## Day-by-Day Plan

| Time | Work |
|------|------|
| Day 1 AM | M0: GET endpoint + Firestore read + 3 tests |
| Day 1 PM | M1: `useDocument` hook + `DocumentHeader` + `DocumentFooter` |
| Day 2 AM | M1: `DocumentViewer` + `DocumentPanel` + Vitest tests |
| Day 2 PM | M2: split-pane layout + M3: documentId wiring + smoke test |

---

## LOC Estimates

| Milestone | Impl | Tests | Total |
|-----------|------|-------|-------|
| M0 — GET endpoint | 40 | 40 | 80 |
| M1 — DocumentPanel components | 160 | 80 | 240 |
| M2 — Split-pane layout | 50 | 20 | 70 |
| M3 — documentId wiring | 30 | 20 | 50 |
| **Total** | **280** | **160** | **~440** |

---

## Quality Gates

After each milestone:
```bash
cd backend && make test-fast   # after M0
cd frontend && npm run quality:check:fast   # after M1, M2, M3
```

Final gate:
```bash
cd backend && make test
cd frontend && npm run quality:check
```

Manual smoke test (requires `make dev`):
1. Upload a DOCX → green dot in file browser
2. Click the document → split-pane opens, document renders on left
3. Send "Summarize this document" → AI responds with content (not "please specify")
4. Close the tab → full-width chat returns

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `a2uiComponents` stored shape differs from `A2UIRenderer` expected shape | Medium | Check in M0 before building M1; if shape mismatch, add a thin adapter in `useDocument` |
| `db/folders.py` has no `get_document()` function | Medium | Add one (~10 LOC Firestore read) — straightforward |
| `useSkillAgent` doesn't expose a clean hook for adding request body fields | Low | Grep the hook; if needed, thread `documentId` through `AGUIProvider` context |
| `A2UIRenderer` expects a specific spec format and fails silently on bad data | Low | Add error boundary + "preview unavailable" fallback in `DocumentViewer` |
