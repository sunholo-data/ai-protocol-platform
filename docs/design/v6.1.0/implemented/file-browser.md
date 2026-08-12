# File Browser

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 2.5 days
**Scope**: Fullstack
**Dependencies**: document-ui (v6.0.0 ✅), cloud-infrastructure (v6.0.0 ✅), auth-and-permissions (v6.0.0 ✅)
**Created**: 2026-04-23
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- `document-ui.md` designed the upload flow (`POST /api/documents/upload`) and A2UI rendering of a single open document — but it assumed each document is uploaded individually and has no concept of organising multiple documents into folders or projects
- Users working on a real task (e.g., Q1 financial review) upload 10–20 related files at once. There is nowhere to browse these, see which have been parsed, open multiple at once in tabs, or navigate between them
- The document workspace mockup ([/frontend/public/mockups/document-workspace.html](/frontend/public/mockups/document-workspace.html)) shows a full file browser: folder accordion, file list with format badges and parse-status indicators, search, parse-progress bar, and a multi-tab interface for open documents
- Without the file browser, the "14 documents loaded" context shown in the chat panel cannot be established — users can only work with one document at a time

**Impact:**
- Skills that operate across a document set (Doc Analyst, Data Extractor) cannot function as designed
- Demo scenario (Q1 Financial Review folder with 14 files across 3 subfolders) is impossible to set up
- `chat-message-rendering.md`'s `ContextBanner` ("Analyzing 14 documents") has no source of truth

## Goals

**Primary Goal:** Give users a folder-based file browser in the document panel that reflects their GCS-stored document collection, shows real-time parse status, and feeds the active document set into the chat context.

**Success Metrics:**
- Upload a folder of 14 files (drag-drop) → all 14 appear in the browser within 2 seconds, organised by subfolder
- Parse status indicators update live as parsing completes (no manual refresh)
- Opening a file from the browser loads it in a new doc tab and renders it via A2UI within 2 seconds (deterministic formats)
- Chat context badge ("Analyzing N documents") reflects the set of files in the active session
- Search/filter responds within 50ms for 200-file collections (client-side)

**Non-Goals:**
- Real-time collaborative file management (multi-user simultaneous uploads)
- File versioning / history (future)
- Sharing files between users (future — covered by resource-access-control.md separately)
- Folder rename / move within the browser (future)
- Preview thumbnails for images (show icon + filename only)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | File list renders from Firestore index (no GCS listing); parse status updates via Firestore real-time listener — no polling |
| 2 | EARNED TRUST | +1 | Parse status dot (green/amber/red) tells users exactly which files the AI has and hasn't processed; no silent failures |
| 3 | SKILLS, NOT FEATURES | +1 | Opening a folder sets the skill's document context automatically — user doesn't configure an "attachment list" manually |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | If Firestore listener fails → falls back to REST poll; if GCS upload fails → file shown in error state, not silently dropped |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses standard GCS multipart upload and Firestore onSnapshot — no custom protocol |
| 7 | API FIRST | +1 | `GET /api/documents` and `POST /api/documents/upload` (already defined) serve web, Telegram, and CLI equally |
| 8 | OBSERVABLE BY DEFAULT | 0 | Upload + parse events already flow through existing OTEL tracing in the backend |
| 9 | SECURE BY CONSTRUCTION | +1 | Documents scoped per-user; GCS paths include `uid` prefix; Firestore rules enforce `request.auth.uid == resource.data.userId`; no cross-user file listing |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client only renders the Firestore index — no GCS listing calls from browser, no client-side parse logic |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ |

## Design

### Overview

Documents are stored in GCS with a `documents/{uid}/{doc_id}/{filename}` key structure. A Firestore `documents` collection mirrors each file's metadata and parse status. The frontend subscribes to the user's document index via `onSnapshot` for live updates. The `DocTabsBar` manages open documents as tabs. The `DocListView` renders the folder tree, file list, status indicators, search, and progress bar. Clicking any file opens it in a new tab and triggers `loadDocumentToSession()` to include it in the chat context.

The file browser exposes **two distinct bucket modes** — user-owned (read-write) and skill corpus (read-only). Access mode is inferred from path structure, not an explicit config flag.

### Bucket Modes

The file browser shows two sections simultaneously. Access mode is inferred from path structure — no explicit `mode` flag that can be misconfigured.

| Mode | UI section | Bucket source | Path pattern | Firestore index? | Write? |
|------|-----------|--------------|-------------|-----------------|--------|
| **User uploads** | "My Documents" | Client bucket (domain-mapped) or `DOCUMENTS_BUCKET` fallback | `users/{uid}/docs/{folderId}/{filename}` | Yes — `userId == uid` | Yes |
| **Skill corpus** | "Knowledge Base" | `SkillConfig.tool_configs.list_documents.bucket_url` | Admin-managed, no uid prefix | No — listed from GCS | No |

### Client Bucket Resolution (domain → bucket)

Each client organisation has its own GCS bucket, keyed to their email domain. This enforces data separation at the infrastructure level — `@rockwool.com` files never share a bucket with `@yourcompany.com` files, regardless of access controls.

**Firestore `clients` collection** (new — one doc per client domain):
```
/clients/{domain}/           e.g. /clients/rockwool.com
  documents_bucket: string   GCS bucket name for user uploads + corpus
  display_name: string       "Rockwool"
  created_at: Timestamp
```

**Bucket resolution at upload/list time:**
```python
async def resolve_documents_bucket(user: User) -> str:
    domain = user.email.split("@")[1]
    client_doc = await get_client(domain)          # Firestore lookup
    if client_doc and client_doc.documents_bucket:
        return client_doc.documents_bucket         # e.g. "rockwool-documents"
    return settings.DOCUMENTS_BUCKET              # app-wide fallback for dev/internal
```

**Path structure within the shared client bucket:**

The client bucket is **shared across all users in the domain**. Per-user isolation is enforced by the `users/{uid}/` path prefix within the shared bucket — not by separate per-user buckets.

```
gs://{client_bucket}/            ← one bucket, all @domain.com users
  users/
    {uid_alice}/
      docs/{folderId}/{filename} ← Alice's personal uploads (read-write)
    {uid_bob}/
      docs/{folderId}/{filename} ← Bob's personal uploads (read-write)
  corpus/
    {admin-managed structure}    ← domain-wide knowledge base (read-only)
```

The `corpus/` prefix within the client bucket can also be indexed in Vertex AI Search as the client's enterprise datastore. The skill's `tool_configs.list_documents.bucket_url` points to either the corpus prefix or an entirely separate admin bucket — whichever the client uses.

**Fallback for `DOCUMENTS_BUCKET`:** When no client mapping exists (e.g., `@gmail.com` test accounts, internal `@yourcompany.com` dev users), uploads go to `DOCUMENTS_BUCKET` with the same `users/{uid}/...` path structure. The client doc for `yourcompany.com` can be created with `documents_bucket: DOCUMENTS_BUCKET` to make this explicit rather than implicit.

### Access Enforcement

Two layers — both must hold independently:

1. **IAM (infrastructure):** The Cloud Run SA has `storage.objectAdmin` on client buckets it owns and `storage.objectViewer` on corpus-only buckets. A backend bug cannot escalate privileges beyond what the SA can do.

2. **API (application):** 
   - Upload/delete: resolve bucket for the calling user's domain → only write to `users/{calling_uid}/...` within that bucket. Any path outside the uid prefix returns 403.
   - List (My Documents): Firestore query filtered by `userId == calling_uid`. Never exposes other users' Firestore records.
   - List (Knowledge Base): reads from skill's `bucket_url` via backend — never a client-supplied bucket name.
   - Cross-domain: a user cannot request another domain's client bucket. Bucket name comes from server-side Firestore lookup, not from the request.

### GCS Bucket Structure (summary)

```
gs://rockwool-documents/           ← client bucket (one per domain)
  users/{uid}/docs/{folderId}/     ← "My Documents" — read-write, uid-scoped
    q1-financial-summary.docx
  corpus/                          ← "Knowledge Base" — read-only, admin-managed
    policies/annual-report-2025.pdf
    procedures/...

gs://aitana-documents-bucket/      ← yourcompany.com + fallback (DOCUMENTS_BUCKET)
  users/{uid}/docs/{folderId}/
  corpus/

gs://any-other-admin-bucket/       ← skill corpus only (no user uploads)
  ...                              ← pointed to by tool_configs.list_documents.bucket_url
```

### Firestore Data Model

**Collection: `folders`** (subcollection under `users/{uid}/folders/{folderId}`)

```
Folder {
  id: string               # Firestore doc ID
  name: string             # Display name, e.g. "Q1 Financial Review"
  userId: string
  createdAt: Timestamp
  docCount: number         # denormalised for fast badge display
  parsedCount: number      # denormalised
}
```

**Collection: `documents`** (extends the existing schema from `document-ui.md`)

New/updated fields:
```
Document {
  ...existing fields (id, title, mimeType, storagePath, sourceUrl, createdAt)...
  folderId: string         # required — every document belongs to a folder
  folderName: string       # denormalised for display without a join
  parseStatus: 'pending' | 'parsing' | 'parsed' | 'failed'
  parseError: string?      # populated on failure, shown in tooltip
  blockCount: number?      # populated after parse: total A2UI blocks
  tableCount: number?
  imageCount: number?
  changeCount: number?     # tracked changes
  parsedMs: number?        # parse duration (shown in doc footer, e.g. "Parsed in 11ms")
}
```

**Migration note:** Existing `documents` records from `document-ui.md` have no `folderId`. The migration script creates a synthetic "Uploads" folder for each user and assigns their orphaned docs to it. This is a one-off Firestore write, not a schema break.

### Backend Changes

**New Endpoints:**

`GET /api/folders` — list all folders for the authenticated user
```json
Response: {
  "folders": [
    { "id": "abc123", "name": "Q1 Financial Review", "docCount": 14, "parsedCount": 12 }
  ]
}
```

`POST /api/folders` — create a new folder
```json
Request:  { "name": "Q1 Financial Review" }
Response: { "id": "abc123", "name": "Q1 Financial Review" }
```

`GET /api/folders/{folderId}/documents` — list documents in a folder with parse status
```json
Response: {
  "documents": [
    {
      "id": "doc1", "title": "Q1 2026 Financial Summary", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "parseStatus": "parsed", "blockCount": 42, "tableCount": 3, "imageCount": 2,
      "changeCount": 5, "parsedMs": 11, "storagePath": "users/uid/docs/abc123/q1-2026-financial-summary.docx"
    }
  ]
}
```

**Modified Endpoints:**

`POST /api/documents/upload` (from `document-ui.md`) — add required `folderId` field to request body. If `folderId` is absent, auto-create a folder named after the upload session date.

**New Module:** `backend/db/folders.py` — Firestore CRUD for `Folder` documents (~80 lines).

**Parse Status Updates:** The existing parse pipeline (ailang-parse) already runs async after upload. Extend the pipeline to write `parseStatus`, `blockCount`, `tableCount`, `imageCount`, `changeCount`, `parsedMs` back to the Firestore document record on completion. Use a `try/except` to write `parseStatus: 'failed'` + `parseError` on any exception.

### Frontend Changes

**New Components:**

```
frontend/src/components/doc-browser/
  DocTabsBar.tsx          — open-document tab strip: tab per open doc, close button, + upload, list toggle
  DocTab.tsx              — individual tab: file icon, truncated name, format badge, close ×
  DocListView.tsx         — full folder browser: folder accordion, file list, search, progress bar
  DocListFolder.tsx       — collapsible folder row with file count badge
  DocListItem.tsx         — file row: icon, name, format badge, parse-status dot
  DocListSearch.tsx       — client-side filter input
  DocParseProgress.tsx    — bottom-of-list progress bar (N/total parsed, estimated time)
  UploadDropZone.tsx      — drag-and-drop target; calls POST /api/documents/upload per file
```

**`DocTabsBar.tsx`:**

Shows one `DocTab` per open document (state: `openDocs: Document[]` + `activeDocId: string`). Tabs are closeable (× removes from `openDocs`). The "list" button (grid icon) toggles between `DocListView` and the single active doc's A2UI render. A "+" button opens the upload dialog or drag-target. Scroll-overflow: `overflow-x: auto; scrollbar-width: none` (matches mockup).

**`DocListView.tsx`:**

Renders two sections:

1. **"My Documents"** — subscribes to `onSnapshot(collection(db, 'users/{uid}/folders'))` for live folder list. For the active folder, subscribes to `onSnapshot(collection(db, 'users/{uid}/folders/{folderId}/documents'))`. Status dots update in real time. Upload and delete controls are enabled. Files show no lock badge.

2. **"Knowledge Base"** — only rendered when the skill has `tool_configs.list_documents.bucket_url` set. Calls `GET /api/corpus/files` (new endpoint — see backend) once on mount; no live subscription (admin-managed files change infrequently). Files show a lock badge (🔒) and no upload/delete controls. Folder structure mirrors GCS key prefixes.

Client-side search filters across both sections simultaneously.

**`DocParseProgress.tsx`:**

Shown when `parsedCount < docCount`. Displays "Parsing: {parsedCount} / {docCount} complete" + an animated progress bar + estimated time remaining (derived from average parse time of completed docs). Disappears when all docs are parsed.

**`UploadDropZone.tsx`:**

Drop target wired into `DocTabsBar`'s "+" button and the full panel (when `DocListView` is empty). On drop: iterate `FileList`, auto-create a folder (or add to active folder), call `POST /api/documents/upload` per file with `folderId`. Shows upload progress via native `XMLHttpRequest.upload.onprogress` (no extra dep). On success, the Firestore `onSnapshot` listener surfaces the new doc automatically — no manual state update.

**`loadDocumentToSession()` — session context wiring:**

When a user opens a document from the browser (clicks a `DocListItem` or `DocTab`), call:
```ts
loadDocumentToSession(docId)   // adds doc to the active AG-UI session's context
setActiveDocumentContext({ folderName, docCount })  // updates SkillContext for ContextBanner
```

The backend exposes `POST /api/sessions/{sessionId}/context` (new) that appends a document reference to the session's context map — the next agent turn sees it as available tool context.

**Modified Components:**

- `SkillContext` — add `openDocs`, `activeDocId`, `activeDocumentContext`, `loadDocumentToSession`, `closeDoc` to the context type
- Workspace layout (from `document-ui.md`) — replace the doc panel placeholder with `<DocTabsBar>` + conditional render of `<DocListView>` or `<A2UIDocRenderer>`

### New API Endpoints Summary

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/api/folders` | List user's folders | No (new) |
| POST | `/api/folders` | Create folder | No (new) |
| GET | `/api/folders/{folderId}/documents` | List folder documents | No (new) |
| POST | `/api/documents/upload` | Upload file (folderId now required) | Minor — folderId auto-assigned if absent |
| POST | `/api/sessions/{sessionId}/context` | Add document to session context | No (new) |
| GET | `/api/corpus/files` | List skill corpus files from `bucket_url` (read-only) | No (new) |

### Architecture Diagram

```
Browser (drag-drop or click)
      │
      ▼
UploadDropZone ──► POST /api/documents/upload
                         │
                         └──► GCS (store file)
                         └──► Firestore Document {parseStatus: 'pending'}
                         └──► ailang-parse async worker
                                    └──► Firestore Document {parseStatus: 'parsed', blockCount: N}

Firestore onSnapshot ──────────────────────────────────────────────────
      │                                                                │
      ▼                                                                ▼
DocListView (live status dots)                              DocParseProgress (live bar)

User clicks file
      │
      ▼
loadDocumentToSession(docId)
      ├──► POST /api/sessions/{sessionId}/context
      ├──► Add DocTab to DocTabsBar
      ├──► Render A2UI doc in doc panel
      └──► setActiveDocumentContext → ContextBanner updates
```

### CLI Surface

The `aitana` CLI should be able to manage document folders from the terminal — useful for batch uploads and scripted workflows.

New commands (in the `aitana docs` subgroup):

```
aitana docs folder new  <name>              # Create a new folder, print folderId
aitana docs folder list                     # List all folders with doc counts
aitana docs upload <file|glob> --folder <id|name>  # Upload file(s) to a folder
aitana docs list [--folder <id|name>]       # List documents (all or per folder) with parse status
aitana docs status <folderId>              # Show parse progress for a folder
```

Add `docs` to the existing `aitana` command tree (see [local-dev-cli.md](local-dev-cli.md)).

## Implementation Plan

### Phase 1: Backend — folders + parse status (~0.75 day)
- [ ] `backend/db/clients.py` — `ClientConfig` Pydantic model + `get_client(domain)` Firestore lookup + `resolve_documents_bucket(user)` helper (~50 lines)
- [ ] `backend/db/folders.py` — Folder CRUD (Pydantic model + Firestore operations, ~80 lines)
- [ ] `GET /api/folders`, `POST /api/folders` routes (~40 lines)
- [ ] `GET /api/folders/{folderId}/documents` route (~30 lines)
- [ ] Extend parse pipeline to write `parseStatus` + stats back to Firestore on completion/failure (~40 lines)
- [ ] Add `folderId` to upload endpoint; auto-create folder if absent (~20 lines)
- [ ] `POST /api/sessions/{sessionId}/context` — append doc ref to session context (~40 lines)
- [ ] `backend/tests/unit/test_folders.py` — CRUD + route tests (~60 lines)

### Phase 2: Frontend — tabs + list view (~1 day)
- [ ] `DocTab.tsx` + `DocTabsBar.tsx` — open/close/active-tab state (~80 lines)
- [ ] `DocListItem.tsx` + `DocListFolder.tsx` — file row, collapsible folder (~70 lines)
- [ ] `DocListView.tsx` — Firestore `onSnapshot` subscription, folder accordion, search (~120 lines)
- [ ] `DocParseProgress.tsx` — progress bar, ETA estimate (~40 lines)
- [ ] Wire `DocTabsBar` into workspace layout, replace doc panel placeholder
- [ ] Update `SkillContext` with new fields (~20 lines)

### Phase 3: Upload + session context + CLI (~0.75 day)
- [ ] `UploadDropZone.tsx` — drag-drop, progress, per-file uploads (~80 lines)
- [ ] `loadDocumentToSession()` — calls `POST /api/sessions/{sessionId}/context`, updates `SkillContext` (~30 lines)
- [ ] CLI: `aitana docs` subgroup (folder new/list, upload, list, status) — 5 Click commands (~100 lines)
- [ ] Frontend tests: `DocTabsBar` open/close, `DocListView` renders folders, search filters, `DocParseProgress` hides when all parsed (~80 lines)

## Migration & Rollout

**Firestore migration:**
- One-off script `scripts/migrate-docs-to-folders.py`: for each user with documents but no folders, create a "Uploads" folder and set `folderId` on all their existing docs. Run once against dev; run before v6.1.0 deploy against prod.

**Firestore rules additions:**
```
match /users/{uid}/folders/{folderId} {
  allow read, write: if request.auth.uid == uid;
}
```
(Document rules already handle the `folderId` field on the existing `documents` collection.)

**Feature flags:** None — the doc browser replaces the placeholder panel wholesale. No existing file-browsing feature to gate against.

**Rollback:** Re-insert the document panel placeholder from `document-ui.md`. No data loss — Firestore folder records remain.

**Environment Variables:** `DOCUMENTS_BUCKET` — dedicated GCS bucket for user document uploads (separate from `LOGS_BUCKET_NAME` which holds ADK artifacts and logs). Already added to `backend/.env.example`. The existing upload implementation in `upload.py` currently uses `LOGS_BUCKET_NAME` — this sprint migrates it to `DOCUMENTS_BUCKET`. Terraform must provision `aitana-documents-{project}` bucket with the Cloud Run SA as `storage.objectAdmin`.

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `DocTabsBar`: renders 3 tabs, clicking a tab sets it active, closing a tab removes it
- [ ] `DocListView`: renders 2 folders with correct file counts; search "summary" shows 1 result
- [ ] `DocListItem`: green dot for `parsed`, amber for `pending`; clicking fires `loadDocumentToSession`
- [ ] `DocParseProgress`: visible when `parsedCount < docCount`; hidden when all parsed
- [ ] `UploadDropZone`: `POST /api/documents/upload` called once per dropped file (mock fetch)

### Backend Tests (pytest)
- [ ] `resolve_documents_bucket`: returns domain-mapped bucket; falls back to `DOCUMENTS_BUCKET` for unmapped domain
- [ ] `POST /api/folders`: creates folder, returns 201 + id
- [ ] `GET /api/folders`: returns only caller's folders (not other users' within the same domain bucket)
- [ ] `GET /api/folders/{folderId}/documents`: returns docs with correct parseStatus
- [ ] `POST /api/documents/upload`: folderId auto-assigned when absent
- [ ] Parse pipeline: Firestore document updated to `parsed` on success, `failed` on exception
- [ ] `POST /api/sessions/{sessionId}/context`: doc ref added to session; 403 if session not owned by caller

### Manual Testing
- [ ] Drag-drop 14 files → all appear in browser within 2 seconds
- [ ] Parse status dots turn green one by one as parsing completes (no refresh)
- [ ] Progress bar reaches 100% and disappears when all docs parsed
- [ ] Click a file → opens in doc tab, A2UI content visible, ContextBanner shows correct count
- [ ] Search "revenue" → filters live as user types
- [ ] Close a tab → tab removed; switching to another tab restores its content

## Security Considerations

**User uploads (read-write):**
- **GCS paths include `uid`**: `users/{uid}/docs/{folderId}/...` within the client bucket — files cannot be accessed cross-user even with a guessed GCS path (all users in a domain share one bucket, but uid-scoped paths prevent cross-user access)
- **Firestore rules** enforce `request.auth.uid == uid` on all folder and document reads/writes
- **Upload/delete API**: backend resolves the bucket from the caller's domain (server-side Firestore lookup), then validates that the target path begins with `users/{calling_uid}/` before any write; returns 403 otherwise — no client-supplied bucket name or path prefix is trusted
- **`POST /api/sessions/{sessionId}/context`**: validates that `sessionId` belongs to the calling user before appending. A user cannot inject another user's documents into their session.
- **File upload**: backend validates MIME type against the same whitelist from `document-ui.md` (55 types). Files outside the whitelist return 415.
- **Folder names**: sanitised server-side (strip HTML/control chars) before writing to Firestore.

**Skill corpus (read-only):**
- **IAM enforced at SA level**: the Cloud Run SA has `storage.objectViewer` (not `objectAdmin`) on corpus buckets — write is physically impossible regardless of application logic
- **No uid scoping on corpus**: corpus files are shared across users of the same skill; access is controlled at the skill level (user must have access to the skill to see its corpus)
- **Corpus `bucket_url` is skill-config only**: the frontend never sends a `bucket_url` — only the backend reads it from `SkillConfig.tool_configs`. A crafted API request cannot redirect listing to an arbitrary bucket.
- **No write endpoints for corpus**: `POST /api/documents/upload` always targets `DOCUMENTS_BUCKET`; there is no API endpoint that writes to `bucket_url`

## Performance Considerations

- **No GCS listing calls from the browser**: the frontend reads from the Firestore index only. `gs.list()` would be slow (unbounded GCS API call) — the Firestore index is the source of truth for file metadata.
- **Firestore `onSnapshot` scope**: subscribe only to the active folder's documents, not all user documents. Switching folders unsubscribes the previous listener.
- **Client-side search**: filtering a `Document[]` array of 200 items is <1ms — no backend call needed for search.
- **Upload concurrency**: limit to 4 parallel uploads (browser default) to avoid saturating the backend connection pool. Use a semaphore in `UploadDropZone`.

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] Lint and typecheck clean (`cd frontend && npm run quality:check:fast` and `cd backend && make lint`)
- [ ] Drag-drop 14 files → all in browser in <2s, parse status updating live
- [ ] Clicking a file → doc tab opens, A2UI rendered, `ContextBanner` in chat panel shows correct doc count
- [ ] Firestore rules: `make verify-rules` passes with new `folders` collection rules
- [ ] CLI: `aitana docs folder list` and `aitana docs upload` work end-to-end against dev
- [ ] Smoke probe: `GET /api/folders` returns 200 (with auth) — added to `scripts/smoke-deployed.sh`

## Open Questions

- **Client onboarding**: how is a new domain→bucket mapping created? Proposal: an `aitana admin client new <domain> --bucket <bucket>` CLI command (or a Firestore console write during onboarding). The `clients/{domain}` document can be bootstrapped by Terraform when a new client bucket is provisioned, so it's never out of sync.
- **Folder creation UX**: does uploading files without a folder first auto-create one named after today's date, or does the user always name the folder first? The mockup implies named folders ("Q1 Financial Review"). Propose: first upload triggers a "Name this folder" modal; subsequent uploads to the same session add to the existing folder.
- **Multi-folder chat context**: can the user have two folders open simultaneously for cross-folder analysis? The mockup shows "14 documents loaded" from a single folder. For now: one active folder per session; loading a second folder replaces the first in context (with a confirmation prompt if the first has been analysed).
- **Images in the file list**: the mockup's "Charts" folder shows `.png` and `.jpg` files with a green "parsed" dot. For images, `parseStatus: 'parsed'` means the AI has described the image (via Gemini vision). This is more expensive than text parsing — should images be parsed eagerly or on-demand? Proposal: on-demand (parse when the image is first opened in a tab or first referenced in chat).

## Related Documents

- [document-ui.md](../v6.1.0/document-ui.md) — upload flow, A2UI rendering, `POST /api/documents/upload` definition
- [cloud-infrastructure.md](../v6.0.0/implemented/cloud-infrastructure.md) — GCS bucket config, Firestore project
- [auth-and-permissions.md](../v6.0.0/implemented/auth-and-permissions.md) — Firestore rules pattern
- [chat-message-rendering.md](chat-message-rendering.md) — `ContextBanner` that reads `activeDocumentContext` set here
- [local-dev-cli.md](local-dev-cli.md) — `aitana docs` CLI subgroup added here
- [Product Axioms](../../product-axioms.md)
- [Mockup](../../frontend/public/mockups/document-workspace.html)

---

## Implementation Report

**Completed**: 2026-04-24
**Actual Effort**: 1 session vs 2.5 days estimated (M1–M4 all delivered)
**Commits**: M1 `e0f7094`, M2 `d92513c`, M3 `af9a9a9`, M4 `0c5bc87`
**Evaluator score**: 79/100 — PASS

### What Was Built
- **M1**: `db/clients.py` (domain→bucket via Firestore `clients/{domain}`), `db/folders.py` (user-facing folder CRUD at `users/{uid}/folders/`), folder API routes
- **M2**: `upload.py` rewritten — per-client bucket, `users/{uid}/docs/{folderId}/` path, `parseStatus:pending` written immediately, `_ParseResult` dataclass, `POST /api/sessions/{id}/context` endpoint
- **M3**: `DocListView`, `DocListFolder`, `DocListItem`, `DocListSearch`, `DocParseProgress`, `useDocBrowser` Firestore onSnapshot hook — split chat page with collapsible left doc panel
- **M4**: `DocTabsBar`, `DocTab`, `UploadDropZone` (XHR with progress, 4-concurrent semaphore), tab open/close wiring in ChatShell, `aitana docs` CLI (folder new/list, upload, list, status)

### Deviations from Plan
- `test_session_context.py` not a separate file — session context tests in `TestSessionContext` class inside `test_upload.py`
- Doc folder tests named `test_doc_folders.py` (not `test_folders.py` — that file covers storage ACL)
- `DocListView.test.tsx` not created — component-level tests in `DocListFolder/DocListItem/DocListSearch/DocParseProgress` test files instead
- Knowledge Base section deferred — requires `/api/corpus/files` backend endpoint not yet built
- `ContextBanner` not wired to file-browser `activeDocumentContext` in ChatShell

### Follow-up
1. Create `cli/tests/test_docs.py` (Click runner tests for `aitana docs` commands)
2. Wire `ContextBanner` to `activeDocumentContext` when a doc tab is opened
3. Knowledge Base section in `DocListView` (requires `/api/corpus/files` endpoint)
4. `aitana docs folder list` tabular output (Rich table)
