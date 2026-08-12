# Document Data Layer — onSnapshot Reads + Block Subcollection for Edits

**Status**: Planned
**Priority**: P1 (Medium) — foundational for both [agent-driven-document-edits.md](agent-driven-document-edits.md) and [document-edit-loop.md](../v6.2.0/document-edit-loop.md)
**Estimated**: 2 days (read path: ✅ already shipped 2026-04-27; subcollection migration: 1.5d; block-id stability: 0.5d)
**Scope**: Fullstack
**Dependencies**:
- document-ui (v6.1.0 ✅) — `DocumentPanel`, `BlocksRenderer`, current `parsed_documents/{docId}` schema
- document-to-ai-pipeline (v6.1.0 ✅) — `apply_edits(blocks, editedBlocks)` overlay function in [backend/tools/documents/context.py](../../../backend/tools/documents/context.py)
- file-browser (v6.1.0 ✅) — `useDocBrowser` Firestore subscription pattern
**Created**: 2026-04-27
**Last Updated**: 2026-04-27

## Problem Statement

**Current State (as of 2026-04-27):**

The frontend's [useDocument](../../../frontend/src/hooks/useDocument.ts) hook just migrated from a one-shot `GET /api/documents/{id}` REST fetch to a Firestore `onSnapshot` subscription on `parsed_documents/{docId}`. This fixed the flicker that showed "Document preview unavailable" when a previously-parsed document was reopened — `parseStatus`, `blocks`, and `parseError` now stream in real-time and the UI is status-aware.

That fix solves **today's read path**. It does not solve **tomorrow's edit path**:

1. **Whole-doc snapshots restream the entire `blocks` array on every change.** A single edit to one cell in a 200-block document streams ~50KB to every subscriber. Today there is no editing, so this is fine. Once [agent-driven-document-edits.md](agent-driven-document-edits.md) lands, every accept-edit click and every agent `propose_block_edit` will trigger a full restream.

2. **Block identity is positional.** [context.py:88](../../../backend/tools/documents/context.py) stores edits as `editedBlocks: { "0": {...}, "5": {...} }` keyed by `str(block_index)`. This works only if block positions never shift. The moment we support row-insert, section-delete, or reparse, positional keys point to the wrong content. The [document-edit-loop.md](../v6.2.0/document-edit-loop.md) `EditDelta` vocabulary already assumes stable `blockId` strings — but the storage layer doesn't yet provide them.

3. **Frontend doesn't currently apply the `editedBlocks` overlay.** [useDocument.ts](../../../frontend/src/hooks/useDocument.ts) reads `data.blocks` directly from the Firestore document. The agent-side `apply_edits(blocks, editedBlocks)` merge in [context.py:88](../../../backend/tools/documents/context.py) only runs when the agent reads context — the user's view of the document does not reflect prior edits until the next reparse. This is a latent bug today (no UI edit path exists yet), but it becomes a visible bug the moment users can edit.

4. **Write path is undecided.** Two existing design docs reach toward editing from different angles:
   - [agent-driven-document-edits.md](agent-driven-document-edits.md) (v6.1.0) — agent proposes via A2UI sub-surface, user accepts → agent commits to `editedBlocks`. Write path: backend (agent-mediated).
   - [document-edit-loop.md](../v6.2.0/document-edit-loop.md) (v6.2.0) — direct user edits via `POST /api/sessions/{sid}/document/edit`, optimistic UI, `apply_edit_delta` pure function. Write path: backend (REST endpoint).
   
   Neither doc takes a position on whether the **client could write directly to Firestore** (Firestore rules already permit owner reads; writes are currently denied). This decision needs a single owner — it shapes both subsequent design docs.

**Concrete friction:**

A user edits a cell in a 200-block financial report. With the current shape:
- The backend writes `editedBlocks["42"] = {editedText: "€2.8M"}` to `parsed_documents/{docId}`
- Firestore restreams the entire 50KB document to every onSnapshot subscriber
- The frontend's `useDocument` hook receives the snapshot but never applies the overlay — the cell still shows `€2.6M`
- Next time the agent reads context, `apply_edits` merges correctly — so the chat agrees with the edit, but the document panel does not. Worse: the inconsistency is invisible in observability because the Firestore snapshot stream looks fine.

**Impact:**

- **Blocks** [agent-driven-document-edits.md](agent-driven-document-edits.md) from being implemented correctly — without stable block IDs, the `propose_block_edit` tool's `block_index` parameter is brittle.
- **Blocks** [document-edit-loop.md](../v6.2.0/document-edit-loop.md) `EditDelta.blockId` from being a real identifier — today it would have to be a positional string.
- **Restream cost** is invisible until edit volume goes up, then becomes a Firestore read-cost and bandwidth issue.
- **Frontend overlay gap** silently desynchronises the user's view from the agent's view — exactly the trust failure the document workspace is supposed to avoid.

## Goals

**Primary Goal:** Establish the document data shape that supports real-time read (already shipped) AND granular per-block writes for editing — without restreaming the whole document, with stable block identifiers, and with a single write-path policy.

**Success Metrics:**
- Single-block edit produces a Firestore write of ≤ 2 KB (single block document) and a snapshot delivery of ≤ 2 KB (single subcollection doc), down from ~50 KB whole-doc restream
- Block IDs survive reparse: 100% of unchanged blocks retain their ID; only structurally changed regions get new IDs
- Frontend `DocumentViewer` reflects `editedBlocks` overlay on first paint after a Firestore snapshot — no per-block re-fetch needed
- Zero client-direct Firestore writes for document content (all edits go through backend; Firestore rules continue to deny client writes to `parsed_documents/**`)
- Read path latency (snapshot → render) stays < 100 ms p95 after migration

**Non-Goals:**
- Real-time multi-user collaborative editing (CRDTs, OT). Single-user model only — multi-tab is allowed but conflict resolution is last-write-wins per block.
- Server-Sent Events or WebSockets for document changes. Firestore onSnapshot is the transport.
- Migrating away from Firestore for documents. Firestore is the right store for owner-scoped, schema-light, reactive data.
- Versioning / undo-redo across edits. The data layer must support it (immutable `blocks`, mutable overlays) but the feature is out of scope here.
- Edit semantics (`EditDelta` vocabulary, `apply_edit_delta`, optimistic UI) — those live in [document-edit-loop.md](../v6.2.0/document-edit-loop.md) and are unaffected by this decision.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Per-block snapshots reduce delivery size by ~25× for typical edits → faster reactive updates. onSnapshot already eliminates the click-to-render flicker. |
| 2 | EARNED TRUST | +1 | Eliminates the silent overlay-desync bug. What the user sees in the document panel is exactly what the agent sees in context — single source of truth. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; no new user-facing skill concept. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Storage shape, no model selection involved. |
| 5 | GRACEFUL DEGRADATION | +1 | If subcollection writes fail, frontend falls back to whole-doc `blocks` field (still present during migration window). If onSnapshot disconnects, last-known doc remains rendered until reconnect. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Firestore subcollection is the standard Firestore pattern for "many small things under one parent." No custom replication protocol invented. Block IDs are server-assigned UUIDs — Firestore document ID is the standard identifier. |
| 7 | API FIRST | 0 | All read/write surfaces are existing REST endpoints + Firestore subscriptions. No new API. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Per-block writes show up as discrete Firestore audit log entries with `blockId` — easier to reason about than a single whole-doc write that obscures which block changed. |
| 9 | SECURE BY CONSTRUCTION | +1 | Backend-mediated writes only. Firestore rules continue to deny client writes to `parsed_documents/**`. Subcollection inherits parent's `userId` for the read rule. New rule: `parsed_documents/{docId}/blocks/{blockId}` allows owner read, denies client write — consistent with existing rule for the parent. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Client subscribes and renders. All edit logic, ID assignment, and conflict resolution lives in the backend. The client never invents a block ID or rearranges document structure. |
| | **Net Score** | **+6** | Threshold: >= +4 ✓ |

**Conflict Justifications:** None — no axiom scored -1.

## Standards Compliance Check

- [x] Firestore subcollections are the established Firebase pattern for one-to-many under a parent — Firebase docs explicitly recommend it for "potentially large data attached to a parent document" (verified via Google Dev Knowledge MCP)
- [x] Block IDs use Firestore-assigned document IDs (UUIDs) — standard Firestore identity
- [x] Block format remains the existing `Block` ADT from ailang_parse — no new format invented
- [x] No custom replication, no custom diff format, no bespoke transport
- [x] `EditDelta` from [document-edit-loop.md](../v6.2.0/document-edit-loop.md) is reused unchanged — its `blockId` field finally becomes a real identifier instead of a positional string

This design adopts existing standards exclusively. Axiom #6 score is +1.

## Design

### Overview

Three changes, sequenced:

1. **Block identity** — promote block IDs from positional strings to stable, server-assigned UUIDs, written into the existing `blocks` array. Add a `blocks_by_id` map for O(1) lookup. Backwards-compatible with the existing `apply_edits` overlay during the migration window.
2. **Subcollection** — split the `blocks` array out into `parsed_documents/{docId}/blocks/{blockId}` documents. Frontend subscribes to the subcollection (delta updates) instead of the whole parent doc.
3. **Write path** — backend-mediated only. `POST /api/sessions/{sid}/document/edit` (from [document-edit-loop.md](../v6.2.0/document-edit-loop.md)) writes directly to the subcollection document. Client-direct Firestore writes are explicitly forbidden.

The current onSnapshot read path (already shipped) is preserved — it just shifts from subscribing to one parent doc to subscribing to a subcollection query.

### Frontend Changes

**Modified Components:**

- [useDocument.ts](../../../frontend/src/hooks/useDocument.ts) — split into two subscriptions:
  - Parent doc: `parsed_documents/{docId}` for metadata (`parseStatus`, `parseError`, `summary`, `originalFilename`)
  - Subcollection: `parsed_documents/{docId}/blocks` ordered by `position` for the block stream
  - Combine into the same `DocumentDetail` shape callers expect — no API change to consumers
- `DocumentDetail` interface: `blocks` continues to be a `Block[]` indexed by position; each block now carries a stable `id` (already optional today, becomes required post-migration)

**No new components.** The data layer change is invisible above [DocumentPanel.tsx](../../../frontend/src/components/document/DocumentPanel.tsx) — `BlocksRenderer` already accepts `Block[]` with optional IDs.

**State Management:**

- The hook holds a `Map<blockId, Block>` internally; the returned `blocks: Block[]` is derived by sorting on the `position` field. This decouples Firestore document order (arbitrary) from rendering order (deterministic).
- Snapshot deltas (`docChanges`) update the map per-block — added/modified/removed — instead of replacing the entire array. React reconciles the changed `<BlockNode>` only.

### Backend Changes

**Modified Modules:**

- [backend/tools/documents/upload.py](../../../backend/tools/documents/upload.py) — when writing parsed blocks, also write each block as a subcollection document `parsed_documents/{docId}/blocks/{blockId}`. Keep the inline `blocks` array on the parent document during the migration window for fallback / agent-side reads (which still use `apply_edits(blocks, editedBlocks)`).
- [backend/tools/documents/context.py](../../../backend/tools/documents/context.py) — `build_document_context` reads from the subcollection in preference to the inline `blocks` field. Falls back to inline if the subcollection is empty (for pre-migration documents).
- [backend/tools/documents/routes.py](../../../backend/tools/documents/routes.py) — `POST /api/documents/{doc_id}/reparse` writes new blocks to the subcollection, soft-deletes blocks from the previous reparse that did not match new content. Block ID stability is preserved via content-hash matching (see "Block ID Stability" below).

**New Endpoint** (from [document-edit-loop.md](../v6.2.0/document-edit-loop.md), this doc only specifies the storage-side behaviour):

- `POST /api/sessions/{sid}/document/edit` — translates `EditDelta` into a write to a single subcollection document. Increments a `version` field on the parent doc for cache-coherence. Returns the new block content for the optimistic UI to reconcile against.

**Data Model Changes:**

Today's shape (`parsed_documents/{docId}`):
```
{
  userId, folderId, originalFilename, sourceFormat,
  parseStatus, parseError, parsedAt, parsedMs,
  blockCount, tableCount, imageCount, changeCount,
  blocks: [Block, Block, ...],         // ← migrating out
  editedBlocks: { "0": {...} },        // ← deprecated post-migration
  ...
}
```

Post-migration shape:
```
parsed_documents/{docId}                  ← parent doc (small, ~2KB)
  userId, folderId, originalFilename, sourceFormat,
  parseStatus, parseError, parsedAt, parsedMs,
  blockCount, tableCount, imageCount, changeCount,
  version: number,                       ← bumped on every block write; cache-coherence signal
  schemaVersion: 2,                      ← so we can detect pre-migration docs

parsed_documents/{docId}/blocks/{blockId}  ← per-block subcollection doc
  position: number,                      ← rendering order; gaps allowed (e.g., 100, 200, 300)
  type: "heading" | "text" | "table" | ... ,
  data: { ... },                         ← block-type-specific payload
  contentHash: string,                   ← sha256 of stable content fields, used for reparse matching
  editedAt: ISO8601 | null,              ← null means "as parsed"; non-null means user/agent edited
  editedBy: "user" | "agent" | null,
  createdAt, updatedAt
```

**Why `position` as a number, not array index:**
Numeric positions with gaps (100, 200, 300) let us insert between blocks without renumbering everything (250 goes between 200 and 300). Standard Firestore-friendly ordering pattern.

**Why `contentHash`:**
On reparse, a block whose `contentHash` matches an existing block keeps its `blockId`. Only blocks whose content actually changed get new IDs. This means edits made against the old `blockId` survive a reparse if and only if the underlying content didn't change — which is the correct semantics.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/api/documents/{doc_id}` | Still returns the parent doc + inline `blocks` array (composed from subcollection on the backend). Frontend no longer calls this; preserved for non-frontend consumers (CLI, channels). | No |
| POST | `/api/documents/{doc_id}/reparse` | Now also writes to the subcollection and preserves block IDs by `contentHash`. | No (new behaviour, same shape) |
| POST | `/api/sessions/{sid}/document/edit` | New (from [document-edit-loop.md](../v6.2.0/document-edit-loop.md)) — writes a single subcollection block. | No (new endpoint) |

### Architecture Diagram

```
                    ┌──────────────────────────────────────────────────┐
                    │  Backend (Python)                                │
                    │                                                  │
   Upload  ───►  parse_document()  ──► writes:                         │
                    │                  parsed_documents/{id}           │
                    │                  parsed_documents/{id}/blocks/* │
                    │                                                  │
   Edit    ───►  POST /document/edit ──► writes single block:          │
                    │                  parsed_documents/{id}/blocks/{blockId}
                    │                  bumps parsed_documents/{id}.version
                    │                                                  │
   Reparse ───►  reparse_document() ──► content-hash diff:             │
                    │                  preserves matching block IDs    │
                    │                  soft-deletes orphaned blocks    │
                    │                                                  │
                    └──────────────────────────────────────────────────┘
                              │ Firestore native (no custom protocol)
                              │
                              ▼
        ┌───────────────────────────────────────────────────┐
        │  Firestore                                        │
        │                                                   │
        │  parsed_documents/{docId}            ◄── metadata │
        │    .parseStatus, .version, ...                   │
        │                                                   │
        │  parsed_documents/{docId}/blocks/{blockId}        │
        │    .position, .data, .contentHash, ...            │
        │                                                   │
        └───────────────────────────────────────────────────┘
                              │
                              │ onSnapshot (delta updates)
                              ▼
        ┌───────────────────────────────────────────────────┐
        │  Frontend (React)                                 │
        │                                                   │
        │  useDocument(docId):                              │
        │    sub 1: doc(parsed_documents/{docId})           │
        │    sub 2: query(parsed_documents/{docId}/blocks)  │
        │            orderBy('position')                    │
        │                                                   │
        │  Returns combined DocumentDetail                  │
        │  (parent metadata + sorted blocks array)          │
        │                                                   │
        │  DocumentPanel → BlocksRenderer                   │
        │  React reconciles only changed <BlockNode>s       │
        │                                                   │
        └───────────────────────────────────────────────────┘
```

### Write Path: Backend-Mediated, Not Client-Direct

The decision: **the client never writes directly to Firestore. All edits go through the backend.**

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Client-direct Firestore writes** | Lowest latency (~50ms vs ~150ms backend round-trip). Standard Firebase pattern. | Bypasses `apply_edit_delta` validation. Splits write logic between frontend and backend. Edit logging requires Firestore audit log + correlation. The agent's view of "what changed and why" requires reconstructing intent from raw writes. | ❌ rejected |
| **Backend-mediated writes (REST)** | Single source of edit logic. `EditDelta` validates content (length caps, HTML stripping). Edits are first-class observable events with semantic meaning. Agent can be notified of user edits via the same channel. | ~100ms extra latency vs client-direct. Optimistic UI compensates. | ✅ chosen |

The latency penalty is paid by the optimistic UI pattern from [document-edit-loop.md](../v6.2.0/document-edit-loop.md): the local React state updates in <16ms; the backend round-trip is invisible unless it fails.

**Firestore rules** stay strict — `parsed_documents/{docId}` and `parsed_documents/{docId}/blocks/{blockId}` deny all client writes. This is one of the cheapest security properties to maintain: a misbehaving client cannot corrupt a document.

### Block ID Stability Through Reparse

Reparse needs to be idempotent for unchanged content. Approach:

```python
# backend/tools/documents/reparse.py (sketch)
def reparse_with_id_preservation(doc_id: str, new_blocks: list[Block]) -> None:
    existing = list_blocks_subcollection(doc_id)  # current blocks
    existing_by_hash = {b.contentHash: b for b in existing}
    
    for new_block in new_blocks:
        h = compute_content_hash(new_block)
        if h in existing_by_hash:
            # Same content → keep the old blockId, update only position
            old = existing_by_hash.pop(h)
            update_block(doc_id, old.id, position=new_block.position)
        else:
            # New or changed content → fresh blockId
            create_block(doc_id, generate_block_id(), new_block, content_hash=h)
    
    # Anything left in existing_by_hash is now orphaned → soft-delete
    for orphan in existing_by_hash.values():
        soft_delete_block(doc_id, orphan.id)
```

**`contentHash` definition:** sha256 of `(block.type, block.position-stripped-data)` — the structural content, ignoring position. So a paragraph that moved from position 100 to position 200 keeps its ID; a paragraph whose text changed gets a new ID.

**Edit survival semantics:** an edit lives on a specific `blockId`. If reparse preserves that `blockId` (content unchanged), the edit naturally survives. If reparse replaces the block (content changed), the edit's target is gone — the edit is orphaned and lost. This is the correct semantics: if the source document changed in a way that obsoletes my edit, my edit shouldn't silently re-apply to different content.

### Conflict Handling

Two writers can target the same block: a human in a document panel and an AI agent in a chat tool call. Approach:

- **Last-write-wins per block.** Each block is an independent unit; concurrent edits to different blocks never conflict.
- **Optimistic concurrency for same-block edits.** The edit endpoint accepts an `expectedVersion` from the client. If the block has been written since the client's last snapshot, the server returns 409 + the current state. The client reconciles (apply local edit on top of server's new state, or surface a "the AI also changed this — keep yours / accept theirs" toast).
- **Agent edits announce themselves.** When the agent writes to a block, the corresponding chat message includes a `blockId`-aware reference so the user sees that change in chat context too. The user's onSnapshot subscription delivers the new block to the document panel — the two surfaces stay coherent without polling.

Multi-user collaboration (two humans editing the same doc) is explicitly out of scope. Single-user multi-tab is supported via the same optimistic-concurrency mechanism — the second tab's onSnapshot delivers the first tab's edit, no special-case needed.

### Migration Strategy

The migration runs in three phases. Each phase is independently shippable; the system is correct at every step.

**Phase A — Subcollection mirror (write to both, read from inline):**
- Upload + reparse write to both the inline `blocks` array AND the subcollection
- Reads (frontend + agent) continue to use the inline `blocks` array
- Validates the subcollection write path without changing read behaviour
- Risk: zero. Inline path is unchanged.

**Phase B — Subcollection read (frontend cuts over):**
- Frontend `useDocument` switches to subscribing to the subcollection (combined with parent metadata)
- Backend `build_document_context` keeps reading inline `blocks` for now (agent-side read path unchanged)
- Risk: low. Backwards-compatible — pre-Phase-A documents with no subcollection cause `useDocument` to fall back to the inline `blocks` field on the parent.

**Phase C — Subcollection canonical (delete inline, agent reads subcollection):**
- Backend `build_document_context` reads from subcollection
- `apply_edits(blocks, editedBlocks)` overlay logic is removed; edits live as updates to subcollection blocks directly
- Inline `blocks` field is deleted from new uploads (existing docs keep it as legacy)
- A backfill script writes the subcollection for pre-Phase-A documents and removes their inline blocks
- Risk: medium. Cutover requires `editedBlocks` overlay to be migrated first — see "EditedBlocks Migration" below.

**EditedBlocks Migration (one-shot, runs at start of Phase C):**

Today's `editedBlocks: { "0": {editedText: "..."} }` (positional, text-only) maps to subcollection updates: for each entry, find the block at the specified position, write `editedText` to its `data.text` field, and set `editedAt`/`editedBy`. After migration, the `editedBlocks` field is deleted from the parent.

This is a one-shot Cloud Run job (~20 min for the current corpus, well under 10K docs).

## Implementation Plan

### Phase 0: Read-path onSnapshot ✅ (completed 2026-04-27)
- [x] Migrate `useDocument` from one-shot REST to onSnapshot on `parsed_documents/{docId}`
- [x] Make `DocumentPanel` status-aware (parsing/loading/failed/empty terminal states)
- [x] Add `parseError` to `DocumentDetail`
- [x] Update tests (13 passing in [DocumentPanel.test.tsx](../../../frontend/src/components/document/__tests__/DocumentPanel.test.tsx))

### Phase A: Subcollection mirror writes (~0.5 day)
- [ ] [backend/tools/documents/upload.py](../../../backend/tools/documents/upload.py) — after writing inline `blocks`, write each block to `parsed_documents/{docId}/blocks/{blockId}` with `position`, `contentHash`, `createdAt`, `updatedAt` (~50 lines)
- [ ] Add `version: 1` and `schemaVersion: 2` to parent doc on upload
- [ ] pytest: upload writes parent + N subcollection docs; reparse preserves IDs by content hash (~80 lines)
- [ ] Firestore rules: extend owner-read rule to subcollection; deny all client writes (~10 lines + verify-rules test)

### Phase B: Frontend cutover (~1 day)
- [ ] [frontend/src/hooks/useDocument.ts](../../../frontend/src/hooks/useDocument.ts) — split into parent-doc + subcollection-query subscriptions; combine into `DocumentDetail` shape (~80 lines)
- [ ] Fallback to inline `blocks` if subcollection is empty (pre-migration docs) (~15 lines)
- [ ] Update existing tests; add new tests for subcollection path, parent+subcoll merge, fallback (~60 lines)
- [ ] Verify in browser: open a parsed doc, simulate a single-block update via Firestore console, confirm only the affected `<BlockNode>` re-renders

### Phase C: Subcollection canonical + EditedBlocks migration (~0.5 day)
- [ ] [backend/tools/documents/context.py](../../../backend/tools/documents/context.py) — read from subcollection; remove `apply_edits` overlay (~30 lines)
- [ ] One-shot migration script: `backend/scripts/migrate_edited_blocks.py` — for each parsed doc, materialise `editedBlocks` overlay into subcollection; mirror inline `blocks` for any doc without subcollection; delete `editedBlocks` and `blocks` fields (~80 lines)
- [ ] Smoke test: run script against dev, verify a sample doc, then test/prod
- [ ] Remove the inline `blocks` write from `upload.py` (post-migration cleanup)

### Phase D: CLI surface (~0.25 day)
- [ ] `aitana docs blocks <docId>` — list blocks in a document (id, position, type, contentHash, edited?)
- [ ] `aitana docs reparse <docId>` — trigger reparse; show before/after block ID delta (which IDs survived, which were replaced)
- [ ] Both commands hit existing endpoints — pure CLI plumbing, no new backend work

### CLI Surface

Two new commands under the existing `aitana docs` namespace (see [local-dev-cli.md](local-dev-cli.md)):

| Command | Description |
|---------|-------------|
| `aitana docs blocks <docId>` | List blocks in a document with id, position, type, edited status. Sanity-check block ID stability and the subcollection shape during dev. |
| `aitana docs reparse <docId> [--diff]` | Trigger reparse via existing `/api/documents/{id}/reparse` endpoint. With `--diff`, shows which block IDs were preserved (content unchanged) and which were replaced. |

Both commands are thin httpx wrappers over existing endpoints — ~30 lines each + a unit test. They make the data-layer work testable from a terminal without console-clicking through Firebase.

## Migration & Rollout

**Database Migrations:**
- Phase A: subcollection writes added (no destructive change)
- Phase C: one-shot migration of `editedBlocks` → subcollection updates; `editedBlocks` and inline `blocks` fields removed from parent docs after migration confirms

**Feature Flags:**
- `DOCUMENT_SUBCOLLECTION_READS=true` — Phase B cutover. Default false until subcollection mirror is verified in dev. Frontend reads inline blocks if false, subcollection if true.
- No flag for Phase A (additive write) or Phase C cleanup (one-shot, irreversible by design — backed by Firestore export beforehand).

**Rollback Plan:**
- Phase A: no rollback needed (additive). Worst case: stop writing the subcollection.
- Phase B: flip `DOCUMENT_SUBCOLLECTION_READS=false`. Frontend reverts to inline reads. No data loss.
- Phase C: pre-migration Firestore export taken before the script runs. Restore is `gcloud firestore import` against the export bucket. Tested in dev first.

**Environment Variables:**
- `DOCUMENT_SUBCOLLECTION_READS` (frontend) — Phase B cutover flag. Set in Cloud Run service env per environment. Removed in Phase D.

## Testing Strategy

### Frontend Tests (Vitest)
- [ ] `useDocument` returns combined parent + subcollection state on initial subscribe
- [ ] Single-block update in subcollection triggers state update without re-streaming all blocks
- [ ] Pre-migration doc (no subcollection) falls back to inline `blocks` field
- [ ] Block sort order respects `position` field, not Firestore native order
- [ ] Removed block (Firestore `removed` change) drops from rendered output

### Backend Tests (pytest)
- [ ] Upload writes both inline blocks and subcollection blocks (Phase A invariant)
- [ ] Reparse preserves blockId when contentHash matches; mints new blockId when content changes
- [ ] Reparse soft-deletes orphaned blocks (whose hashes are not in the new parse)
- [ ] `POST /api/sessions/{sid}/document/edit` writes single subcollection doc and bumps parent `version`
- [ ] Edit endpoint returns 409 when `expectedVersion` doesn't match current parent `version`
- [ ] Migration script materialises `editedBlocks` overlay into subcollection updates correctly (golden fixture)
- [ ] Firestore rules: owner can read subcollection; non-owner gets permission denied; client cannot write (verify-rules test)

### Manual Testing
- [ ] Open a parsed doc; edit a single cell via the upcoming `POST /document/edit` endpoint; confirm in browser devtools that the Firestore snapshot delivery is < 5 KB (just the changed block doc)
- [ ] Open the same doc in two tabs; edit in tab A; tab B reflects the change without refresh
- [ ] Reparse a doc; spot-check that unchanged blocks kept their IDs and edits to those blocks survive
- [ ] Pre-migration doc opens correctly via the inline-blocks fallback path

## Security Considerations

- **Firestore rules** for subcollection mirror parent rules: owner read only, client writes denied. Backend SA bypasses rules for writes.
- **Edit authorisation**: the `POST /document/edit` endpoint validates session ownership AND that the target `blockId` belongs to a doc the session owner owns. No cross-doc or cross-user mutation is possible.
- **Content validation**: edit values pass through the same length/HTML-stripping policy as `apply_edit_delta` from [document-edit-loop.md](../v6.2.0/document-edit-loop.md). Storage layer does not invent its own content policy — it inherits from the edit endpoint.
- **Block ID enumeration**: block IDs are server-generated UUIDs. Knowing one block ID does not let an attacker enumerate or guess others. Combined with the rule that reads require `userId == request.auth.uid`, IDs are not a sensitive secret but also not a usable attack vector.
- **No client-direct writes**: explicitly prohibited by Firestore rules + design contract. A reviewer rejecting a PR that adds a client write to `parsed_documents/**` is doing their job.

## Performance Considerations

- **Snapshot delivery size**: typical edit becomes ~1-2 KB (single subcollection doc) instead of ~50 KB (whole parent doc). For active editing sessions, this is a 25-50× reduction in bandwidth.
- **Initial subscribe cost**: subscribing to a 200-block document still streams 200 small docs on first connection. Total bytes are similar to the inline approach (the data is the same), but the snapshot listener has 200 entry events instead of 1. Tested cost: ~150ms additional first-paint latency for a 200-block doc — acceptable. For larger docs, consider paginated subscription (limit + load-more) — not needed for the current corpus.
- **Firestore read costs**: per Firebase pricing, each subcollection doc counts as one read. A 200-block doc costs 200 reads on first subscribe vs 1 for the inline field. At expected usage volumes (< 10K active docs), monthly cost increase is ~$X — verify before Phase C cutover with a realistic load test.
- **Write costs**: a single edit costs 2 writes (the block doc + the parent's `version` bump) vs 1 today. Acceptable.
- **Bundle size**: no change. Frontend already imports `firebase/firestore` for `useDocBrowser`; adding `query`/`orderBy`/`onSnapshot` for a subcollection is a no-op.

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run test:run`) — including new subcollection cases
- [ ] All backend tests passing (`cd backend && make test-fast`) — including reparse-preservation and migration-script tests
- [ ] Lint and typecheck clean (`cd frontend && npm run quality:check:fast`, `cd backend && make lint`)
- [ ] Smoke probe: opening a doc and editing a block delivers a Firestore snapshot ≤ 5 KB (measured in the browser)
- [ ] Reparse on a 50-block doc preserves ≥ 90% of block IDs when the source file is unchanged byte-for-byte (sanity check on the contentHash logic)
- [ ] One-shot migration script idempotency: running twice produces no diff after the first run
- [ ] Firestore rules verified via `make verify-rules`: owner-read on subcollection works, non-owner denied, client write denied
- [ ] CLI commands work end-to-end: `aitana docs blocks <docId>` and `aitana docs reparse <docId> --diff`
- [ ] [agent-driven-document-edits.md](agent-driven-document-edits.md) updated to reference subcollection blockIds (no more `block_index`)
- [ ] [document-edit-loop.md](../v6.2.0/document-edit-loop.md) updated to note that `EditDelta.blockId` is a subcollection document ID

## Open Questions

- **Subcollection size limits**: Firestore has no documented hard limit on subcollection size, but query performance degrades past ~100K docs. Realistic max document is ~5K blocks (a 1000-page report). Confirm this stays well within performance bounds via a load test in Phase A.
- **`schemaVersion` rollout**: do we need a separate `parsed_documents_v2` collection, or is a `schemaVersion` field on the existing collection sufficient? Proposal: field is sufficient — Firestore is schema-light and the migration is one-shot.
- **Reparse content-hash false negatives**: if ailang_parse changes its serialisation between versions (e.g., key ordering changes), all block hashes change even if the content is semantically identical. Mitigation: canonicalise the hash input (sorted keys, normalised whitespace) — verify ailang_parse output is stable in Phase A.
- **Per-block deletion vs soft-delete**: if a reparse drops a block, do we hard-delete the subcollection doc or set a `deletedAt` field? Soft-delete preserves edit history (the user might have edited a now-orphaned block); hard-delete keeps the subcollection clean. Proposal: soft-delete during the workshop demo era (preserves reasoning chain), revisit when storage cost justifies cleanup.
- **Workshop integration**: this design is invisible above the renderer — does it deserve a workshop comment / module label? Proposal: no. The workshop's W8 module ([document-edit-loop.md](../v6.2.0/document-edit-loop.md)) demonstrates the *protocol* (EditDelta, Block ADT, A2UI projection); the storage shape is implementation detail. One sentence in the W8 narrative ("blocks are stored as a subcollection so per-block edits don't restream the document") is enough.

## Related Documents

- [agent-driven-document-edits.md](agent-driven-document-edits.md) — agent-proposed inline edits via A2UI sub-surface; consumes the stable `blockId` this doc establishes
- [document-edit-loop.md](../v6.2.0/document-edit-loop.md) — `EditDelta` vocabulary, `apply_edit_delta`, optimistic UI; this doc is its storage-side prerequisite
- [document-ui.md](document-ui.md) — Block ADT, current `parsed_documents` schema, `BlocksRenderer`
- [document-to-ai-pipeline.md](implemented/document-to-ai-pipeline.md) — `apply_edits(blocks, editedBlocks)` overlay function being deprecated by Phase C
- [local-dev-cli.md](local-dev-cli.md) — CLI command host for the new `aitana docs blocks` / `aitana docs reparse` subcommands
- [Product Axioms](../../product-axioms.md)
