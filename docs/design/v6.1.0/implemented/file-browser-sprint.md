# FILE-BROWSER Sprint Plan

**Sprint ID**: FILE-BROWSER
**Design Doc**: [file-browser.md](file-browser.md)
**Status**: Implemented
**Estimated Duration**: 2.5 days
**Scope**: Fullstack
**Created**: 2026-04-24

## Sprint Summary

Build the file browser: split the chat page into a document panel + chat panel, implement user document folders backed by per-client GCS buckets (domain-scoped), wire real-time Firestore parse-status updates, and add `aitana docs` CLI commands for batch workflows.

**Key deliverables:**
- `backend/db/clients.py` — domain → GCS bucket resolution
- `backend/db/folders.py` — user-facing document folder CRUD
- `GET/POST /api/folders` + `GET /api/folders/{id}/documents` routes
- Migrate `upload.py` from `LOGS_BUCKET_NAME` → `DOCUMENTS_BUCKET` + per-client bucket
- Frontend: `DocTabsBar`, `DocListView`, `UploadDropZone`, `DocParseProgress` components
- Split chat page into doc panel + chat panel layout
- `aitana docs` CLI subgroup (5 commands)

**What already exists (reuse, don't rebuild):**
- `backend/buckets/` — storage ACL/config system (different purpose — do NOT conflate)
- `backend/tools/documents/upload.py` — document upload, needs migration
- `backend/tools/documents/ailang_parse.py` — parse pipeline, needs status write-back
- `backend/db/models/document.py` — `ParsedDocument` model, needs `folderId` + stats fields
- CLI: `aitana bucket` + `aitana folder` commands (for storage ACL, not doc folders)
- Frontend: `DocumentHistoryPanel`, `useDocumentSessions` (chat session history per doc)

## Velocity Calibration

Last 14 days: 163 commits, ~385 files changed, ~73k lines added.  
CHAT-POLISH (3 milestones): markdown + thinking panel + skill display in ~1 session.  
Estimates below are conservative (buffer included).

## Milestone 1: Backend foundation — clients + document folders (~0.5 day)

**Scope**: backend  
**Goal**: Domain→bucket resolution and user-facing folder CRUD.

### Tasks
- [ ] `backend/db/clients.py`:
  - `ClientConfig(domain, documents_bucket, display_name, created_at)` Pydantic model
  - `get_client(domain: str) -> ClientConfig | None` — Firestore `clients/{domain}` lookup
  - `resolve_documents_bucket(user: User) -> str` — domain lookup with `DOCUMENTS_BUCKET` fallback
  - (~50 lines)
- [ ] Extend `ParsedDocument` in `backend/db/models/document.py`:
  - Add: `folder_id`, `folder_name`, `parse_status` (pending/parsing/parsed/failed), `block_count`, `table_count`, `image_count`, `change_count`, `parsed_ms`
  - (~20 lines diff)
- [ ] `backend/db/folders.py`:
  - `Folder(id, name, user_id, created_at, doc_count, parsed_count)` Pydantic model
  - `create_folder`, `get_folder`, `list_folders`, `update_folder_counts` Firestore operations
  - (~80 lines)
- [ ] `backend/tools/documents/routes.py` (new) — extract folder routes from upload.py:
  - `GET /api/folders` — Firestore query filtered by `userId == uid`
  - `POST /api/folders` — create folder
  - `GET /api/folders/{folderId}/documents` — list documents in folder
  - (~80 lines)
- [ ] `backend/tests/unit/test_clients.py` — `resolve_documents_bucket` with/without mapping (~30 lines)
- [ ] `backend/tests/api_tests/test_folders.py` — CRUD + ownership isolation (~60 lines)

### Acceptance Criteria
- `resolve_documents_bucket` returns `rockwool-documents` for `@rockwool.com` user with Firestore mapping
- `resolve_documents_bucket` returns `DOCUMENTS_BUCKET` env value for unmapped domain
- `GET /api/folders` returns only the calling user's folders (not other users')
- `POST /api/folders` returns 201 with folder id
- `GET /api/folders/{folderId}/documents` returns empty list for new folder

---

## Milestone 2: Pipeline migration + session context (~0.5 day)

**Scope**: backend  
**Goal**: Migrate upload to per-client buckets, write parse stats back, wire session context.

### Tasks
- [ ] Migrate `backend/tools/documents/upload.py`:
  - Replace `LOGS_BUCKET_NAME` with `await resolve_documents_bucket(user)`
  - Change GCS path from `documents/{uid}/{doc_id}/` → `users/{uid}/docs/{folder_id}/`
  - Add `folderId` to request body; auto-create folder named after today if absent
  - Write `parseStatus: 'pending'` to Firestore on upload; flip to `parsing` when parse starts
  - (~40 lines diff)
- [ ] Extend `backend/tools/documents/ailang_parse.py` parse pipeline:
  - On success: write `parseStatus: 'parsed'`, `blockCount`, `tableCount`, `imageCount`, `changeCount`, `parsedMs`
  - On failure: write `parseStatus: 'failed'`, `parseError`
  - (~30 lines diff)
- [ ] `POST /api/sessions/{sessionId}/context` route:
  - Appends `{docId, folderName}` to session context in Firestore
  - Validates session belongs to calling user (403 otherwise)
  - (~40 lines)
- [ ] `GET /api/corpus/files` route:
  - Reads `bucket_url` from active skill's `tool_configs.list_documents`
  - Lists GCS objects under that prefix via SA credentials
  - Returns `[{name, size, contentType, prefix}]` for folder-tree rendering
  - (~50 lines)
- [ ] `backend/tests/api_tests/test_upload.py` — folderId auto-create, bucket resolution (~40 lines)
- [ ] `backend/tests/api_tests/test_session_context.py` — append + 403 on foreign session (~30 lines)

### Acceptance Criteria
- Uploading a file sets `parseStatus: 'pending'` in Firestore immediately
- After parse completes, `parseStatus` flips to `'parsed'` with non-null `blockCount`
- `POST /api/sessions/{sessionId}/context` returns 200 and updates Firestore
- `POST /api/sessions/{foreign_sessionId}/context` returns 403
- `GET /api/corpus/files` returns files from the skill's `bucket_url` (tested with a mock GCS)

---

## Milestone 3: Frontend — doc panel split + list view (~1 day)

**Scope**: frontend  
**Goal**: Split chat page into two panels; build folder tree + file list with live Firestore subscription.

### Tasks
- [ ] Split `frontend/src/app/chat/[skillId]/page.tsx`:
  - Add a collapsible left panel (240px wide) alongside the chat panel
  - Left panel renders `<DocListView>` when expanded, hidden when collapsed
  - Toggle button in chat header
  - (~30 lines diff in page.tsx)
- [ ] `frontend/src/components/doc-browser/DocListView.tsx`:
  - "My Documents" section: `onSnapshot` subscription on `users/{uid}/folders`, then active folder's docs
  - "Knowledge Base" section: calls `GET /api/corpus/files`, rendered when skill has `bucket_url`
  - Client-side search filtering across both sections
  - (~120 lines)
- [ ] `frontend/src/components/doc-browser/DocListFolder.tsx`:
  - Collapsible row with folder name + `(parsed/total)` badge
  - (~40 lines)
- [ ] `frontend/src/components/doc-browser/DocListItem.tsx`:
  - File row: format badge, parse-status dot (green=parsed, amber=pending/parsing, red=failed)
  - Click fires `loadDocumentToSession(docId)`
  - Lock icon for corpus files (no delete control)
  - (~50 lines)
- [ ] `frontend/src/components/doc-browser/DocListSearch.tsx`:
  - Controlled input, filters `DocListView` client-side on keystroke
  - (~25 lines)
- [ ] `frontend/src/components/doc-browser/DocParseProgress.tsx`:
  - Shown when `parsedCount < docCount`; progress bar + "N / total parsed" + ETA
  - Auto-hides when all parsed
  - (~40 lines)
- [ ] Update `SkillContext` (or add `DocBrowserContext`):
  - `openDocs`, `activeDocId`, `loadDocumentToSession`, `closeDoc`, `activeDocumentContext`
  - (~20 lines)
- [ ] `frontend/src/components/doc-browser/__tests__/DocListView.test.tsx`:
  - Renders 2 folders; search "summary" → 1 result; green dot for `parsed`; amber for `pending`
  - (~60 lines)
- [ ] `frontend/src/components/doc-browser/__tests__/DocParseProgress.test.tsx`:
  - Visible when incomplete; hidden when all parsed
  - (~25 lines)

### Acceptance Criteria
- Chat page shows a collapsible left panel; toggling shows/hides the doc browser
- "My Documents" section lists folders and file counts from Firestore `onSnapshot` (live)
- Parse-status dots update without page refresh when `parseStatus` changes in Firestore
- Client-side search filters list within 50ms (no backend call)
- "Knowledge Base" section hidden when skill has no `bucket_url`
- Frontend tests pass

---

## Milestone 4: Upload + tabs + CLI (~0.5 day)

**Scope**: mixed  
**Goal**: Drag-drop upload, open-document tabs, session context wiring, CLI docs subgroup.

### Tasks
- [ ] `frontend/src/components/doc-browser/UploadDropZone.tsx`:
  - Drop target in doc panel (and full-panel when list is empty)
  - Iterates `FileList`, calls `POST /api/documents/upload` per file with `folderId`
  - Upload progress via `XHR.upload.onprogress`; max 4 parallel (semaphore)
  - New docs appear via existing `onSnapshot` listener — no manual state update
  - (~80 lines)
- [ ] `frontend/src/components/doc-browser/DocTabsBar.tsx`:
  - Tab strip above doc panel: one `DocTab` per open document
  - Tabs closeable; scroll-overflow hidden; "+" button opens `UploadDropZone`
  - Toggle between `DocListView` and active doc A2UI render
  - (~70 lines)
- [ ] `frontend/src/components/doc-browser/DocTab.tsx`:
  - File icon, truncated filename, format badge, close ×
  - (~30 lines)
- [ ] `loadDocumentToSession(docId)` in `DocBrowserContext`:
  - Calls `POST /api/sessions/{sessionId}/context`
  - Adds doc to `openDocs`, sets `activeDocId`
  - Updates `activeDocumentContext` for `ContextBanner`
  - (~30 lines)
- [ ] `cli/aitana/commands/docs.py` — `aitana docs` subgroup:
  - `aitana docs folder new <name>` — POST /api/folders, print id
  - `aitana docs folder list` — GET /api/folders, tabular output
  - `aitana docs upload <file|glob> [--folder <id|name>]` — per-file POST /api/documents/upload
  - `aitana docs list [--folder <id|name>]` — list docs with parseStatus column
  - `aitana docs status <folderId>` — parse progress for a folder
  - Register `docs` on `main` in `cli.py`
  - (~120 lines)
- [ ] `frontend/src/components/doc-browser/__tests__/DocTabsBar.test.tsx`:
  - 3 tabs, clicking activates, closing removes
  - (~40 lines)
- [ ] `frontend/src/components/doc-browser/__tests__/UploadDropZone.test.tsx`:
  - `POST /api/documents/upload` called once per dropped file (mock fetch)
  - (~30 lines)
- [ ] `cli/tests/test_docs.py` — docs folder new/list, upload, list (~40 lines)

### Acceptance Criteria
- Drag-dropping 3 files calls `POST /api/documents/upload` 3 times (verified in test)
- Opening a file adds it to `DocTabsBar`; closing removes it
- `ContextBanner` in chat header reflects open doc count from `activeDocumentContext`
- `aitana docs folder list` prints folders from dev backend
- `aitana docs upload fixtures/test.docx --folder test` uploads successfully
- All frontend tests pass

---

## Day-by-Day Plan

**Day 1 (morning)**: M1 — clients.py + folders.py + routes (~4 hrs)  
**Day 1 (afternoon)**: M2 — upload migration + parse pipeline + session context (~4 hrs)  
**Day 2 (full)**: M3 — split layout + DocListView + DocListFolder + DocListItem + DocParseProgress + tests (~8 hrs)  
**Day 2.5**: M4 — UploadDropZone + DocTabsBar + loadDocumentToSession + CLI (~4 hrs)

## Quality Gates

After each milestone:
```bash
cd <local-path> && uv run pytest tests/ -m "not slow and not integration" -q
cd <local-path> && npm run quality:check:fast
```

After all milestones:
```bash
cd <local-path> && npm run test:run
cd <local-path> && uv run pytest tests/ -v --tb=short
```

## LOC Estimate

| Milestone | Impl LOC | Test LOC | Total |
|-----------|----------|----------|-------|
| M1: clients + folders | ~230 | ~90 | ~320 |
| M2: pipeline migration | ~160 | ~70 | ~230 |
| M3: frontend list view | ~295 | ~85 | ~380 |
| M4: upload + tabs + CLI | ~330 | ~110 | ~440 |
| **Total** | **~1015** | **~355** | **~1370** |

## Risks

- **Firestore `onSnapshot` unsubscribe** — must unsubscribe on folder change / component unmount; leak = excessive reads
- **GCS SA permissions** — dev backend SA needs `storage.objectAdmin` on `aitana-documents-bucket`; add to Terraform before running integration tests
- **`clients/{domain}` bootstrap** — dev `yourcompany.com` client doc must exist in Firestore or fallback path is the only test path; seed in smoke setup
- **`POST /api/sessions/{sessionId}/context`** — ADK session store may not have a stable `context` map; confirm session Firestore schema before implementing

## Related Documents

- [file-browser.md](file-browser.md) — canonical design doc
- [document-ui.md](../v6.1.0/document-ui.md) — upload flow, ParsedDocument schema
- [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md) — buckets system (storage ACL — separate from user doc folders)
- [adk-search-tools.md](../../ops/adk-search-tools.md) — search sub-agent pattern reference
