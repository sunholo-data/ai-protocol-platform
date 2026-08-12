# Document Edit Loop

> **Workshop W8 — Shared State:** This is the doc where Python and React share the
> same data model. The Block ADT lives in ADK session state on the backend and is
> projected to A2UI JSON on the frontend. Edits flow back as structured deltas —
> never as natural language — so the agent never has to parse what the user typed.
> The architecture diagram below is the workshop's shared-state payoff slide.

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: 4 days (+ external dependency on ailang_parse write-path features)
**Scope**: Fullstack
**Dependencies**:
- document-ui (v6.0.0 ✅) — Block ADT, `a2ui_formatter`, document Firestore model
- session-and-memory (v6.0.0 ✅) — ADK session state scopes (`app:`, `user:`)
- chat-message-rendering (v6.1.0) — `A2UIRenderer` wired into `MessageBubble`
- file-browser (v6.1.0) — document panel, open tabs
- [document-data-layer.md](../v6.1.0/document-data-layer.md) (v6.1.0) — provides stable subcollection-document `blockId` referenced by `EditDelta`, plus the per-block snapshot transport edits depend on
- **ailang_parse ≥ 0.10.0** — editable formatter flag + `apply_edit_delta` + write-path generate (see External Dependencies below)
**Created**: 2026-04-23
**Last Updated**: 2026-04-23

## Problem Statement

**Current State:**

Documents in v6 are read-only once rendered. The `document-ui.md` design specifies inline editing as a goal, but the implementation path was deferred — specifically:

- `a2ui_formatter` in ailang_parse 0.9.3 emits read-only `Text` nodes only; there is no flag to emit editable `TextField` / `EditableTable` components
- `onAction` in `A2UIRenderer` currently converts user interactions to a new chat message string — e.g., "the user changed APAC revenue to €2.8M" — which the LLM then has to re-interpret before making any change. This is slow (one extra agent turn), lossy (the LLM might misread the intent), and wrong (document edits should be deterministic, not probabilistic)
- There is no write path from Block ADT back to DOCX/PPTX/XLSX. The `generate_document` tool in `document-ui.md` calls `ailang_parse.generate()` but this generates a new document from scratch using a conversation summary — it cannot apply a set of discrete user edits to the original document structure

**Concrete friction today:**

A user opens a Q1 Financial Summary (DOCX), spots that the APAC revenue figure is wrong, changes `€2.6M` to `€2.8M` in the rendered table, and expects to download the corrected DOCX. Today: impossible — the table is read-only, and even if an edit were captured, there's no path back to DOCX.

**Impact:**

- Blocks the "collaborative document workspace" promise from `document-ui.md` — users can see their document but not correct it
- Every edit requires a chat message → agent turn → re-render, adding 2–5 seconds of latency and LLM cost for what should be a deterministic local operation
- Without a write path, Aitana is a document reader, not a document editor

## Goals

**Primary Goal:** Close the edit loop — users can edit document content directly in the A2UI panel, all edits persist to the backend's Block ADT in session state, and the corrected document can be exported back to its original format.

**Success Metrics:**
- Direct table-cell edit → UI update in <100ms (no network round-trip for the visual change; sync to backend in background)
- Download corrected DOCX within 3 seconds of clicking export
- Chat-driven edits ("change the APAC figure to €2.8M") update the document panel without a separate user action
- Zero LLM tokens spent interpreting a direct-edit `onAction` event — those are processed deterministically by `apply_edit_delta`
- Agent-assisted edits (user asks AI to revise a section) appear as A2UI blocks in the chat bubble before being merged into the document panel

**Non-Goals:**
- Real-time collaborative editing (multi-user concurrent edits) — single-user edit model only
- Pixel-perfect style preservation in exports (best-effort; DOCX styles are preserved from the source template, but complex formatting like custom fonts may degrade)
- Undo/redo history (future)
- Version diffing between original and edited document (future)
- Editing images or embedded objects inline

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Optimistic UI: edits apply locally in <16ms; backend sync is async. No waiting for a model turn on direct edits |
| 2 | EARNED TRUST | +1 | Users see exactly what they changed; deterministic `apply_edit_delta` means no LLM-introduced drift; export is the same data the user reviewed |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; no new skill concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Direct edits use zero LLM tokens. LLM is only invoked for AI-assisted edits (e.g., "rewrite this paragraph") — right model, right moment |
| 5 | GRACEFUL DEGRADATION | +1 | If `apply_edit_delta` fails, the edit is rejected with an error message; the document is never silently corrupted. Export falls back to original file link if write-path fails |
| 6 | PROTOCOL OVER CUSTOM | +1 | A2UI `onAction` is the standard edit event channel. Block ADT is ailang_parse's open format. No custom binary protocol or custom diff format |
| 7 | API FIRST | +1 | `POST /api/sessions/{sid}/document/edit` and `GET /api/sessions/{sid}/document/export` serve web, CLI, and channels equally |
| 8 | OBSERVABLE BY DEFAULT | 0 | Edit events logged via existing OTEL tracing |
| 9 | SECURE BY CONSTRUCTION | +1 | Edit endpoint validates: session owned by caller, `blockId` exists in session's active document, value passes content policy (length, no script injection). No cross-user document mutation possible |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend applies optimistic UI update locally; all edit logic (validation, delta application, re-serialisation) runs on the backend. Frontend never mutates the canonical Block ADT |
| | **Net Score** | **+8** | Threshold: >= +4 ✓ |

## Design

### The Shared State Model

This is the central concept of this feature and the workshop's W8 teaching moment:

**The Block ADT is the single source of truth, stored in Python session state. React never owns the document — it only renders a projection of it.**

```
Python (backend)                      React (frontend)
────────────────────────────────      ──────────────────────────────────
                                      
session.state["app:active_document"]  A2UI JSON spec
= List[Block]                         (derived from Block ADT)
                                      
  Block(                                {
    id="b1",                              "root": "b1",
    type=BlockType.TABLE,                 "components": [
    data=TableData(rows=[...])    ◄──       { "id":"b1",
  )                                           "component": {
                                               "type": "EditableTable",
                                               "data": { "rows": [...] }
                                             }
                                           }
                                        ]
                                      }
```

The Python side holds `List[Block]`. The React side holds the A2UI JSON derived from it. They are **the same data in two different representations**. The `a2ui_formatter` in ailang_parse is the serialiser; `apply_edit_delta` is the deserialiser for edit events. Neither side invents its own document model.

### Architecture Diagram

The edit loop has three modes. All three share the same session state as the source of truth.

```
 ┌───────────────────────────────────────────────────────────────────┐
 │  DOCUMENT PANEL (React)                                           │
 │                                                                   │
 │  A2UIViewer                                                       │
 │  ┌─────────────────────────────────────────────────────────┐     │
 │  │  EditableTable  │  TextField  │  Text (read-only)  │... │     │
 │  └──────────────────────┬──────────────────────────────────┘     │
 │                         │ onAction(EditDelta)                     │
 │         [optimistic UI update applied locally, <16ms]            │
 └─────────────────────────┼─────────────────────────────────────────┘
                           │
           MODE A: Direct edit (deterministic, zero LLM tokens)
                           │
           POST /api/sessions/{sid}/document/edit
                           │
 ┌─────────────────────────▼─────────────────────────────────────────┐
 │  BACKEND (Python / ADK)                                           │
 │                                                                   │
 │  session.state["app:active_document"] = List[Block]               │
 │                      │                                            │
 │       apply_edit_delta(blocks, delta)  ◄── ailang_parse ≥0.10    │
 │                      │  (pure function, no LLM)                   │
 │                      ▼                                            │
 │       updated List[Block]  ──► Firestore document record          │
 │                      │         (persists edit across sessions)    │
 │       a2ui_formatter(blocks, editable=True)                       │
 │                      │  ◄── ailang_parse ≥0.10 new flag           │
 │                      ▼                                            │
 │       A2UI diff  ──────────────────────────────────────────────── ┼──► doc panel re-renders changed nodes
 │                                                                   │
 └───────────────────────────────────────────────────────────────────┘
           │                              │
           │ MODE B: Chat-driven edit     │ MODE C: AI-generated content
           │                              │
           ▼                              ▼
   User: "change APAC        User: "write a summary of section 2"
   to €2.8M" in chat                      │
           │                       agent generates new Block(s)
   AG-UI stream → ADK               appends to session.state
   agent calls                             │
   apply_structured_edit_tool              ▼
           │                     agent emits ```a2ui block
   session.state updated         in TEXT_MESSAGE_CONTENT
           │                             │
           ▼                     chat-message-rendering (v6.1.0)
   agent sends updated              routes to A2UIRenderer
   A2UI as ```a2ui block            in the chat bubble
   in its response                         │
           │                      user approves → "merge" action
           │                             │
           └─────────────────────────────┘
                                         │
                       POST /api/sessions/{sid}/document/merge
                                         │
                              append new blocks to session.state
                              → document panel refreshes

 ─────────────────────────────────────────────────────────────────────

 EXPORT (all modes converge here)

 User clicks "Download DOCX"
           │
 GET /api/sessions/{sid}/document/export?format=docx
           │
 ailang_parse.generate(                       ◄── ailang_parse ≥0.10
   blocks=session.state["app:active_document"],
   format="docx",
   source_template=doc.storage_path  # preserves original styles
 )
           │
 → streams DOCX bytes → browser download
```

### Connection to `chat-message-rendering.md`

`chat-message-rendering.md` wires `A2UIRenderer` into chat bubbles for **read-only display** of agent-generated A2UI. This doc adds the **editable** A2UI surface in the document panel, and defines the **merge** flow by which AI-generated content in a chat bubble can be pulled into the canonical document.

The two surfaces are intentionally separate:

| Surface | Component | `onAction` behaviour |
|---|---|---|
| **Chat bubble** | `A2UIRenderer` (from `chat-message-rendering.md`) | Form submissions → new user chat message (e.g., user fills in a form the agent presented) |
| **Document panel** | `DocumentViewer` → `A2UIViewer` (editable mode) | Direct edits → `POST /api/sessions/{sid}/document/edit` (deterministic, zero LLM) |

This distinction is the key insight: `onAction` means different things in different contexts. In the chat bubble, an action is an input to the conversation. In the document panel, an action is a structured mutation of the canonical document state. Same protocol, different semantics.

### Structured Edit Event Protocol

`onAction` currently passes a free-form string. We replace this with a typed `EditDelta` vocabulary. The frontend sends these to the dedicated edit endpoint; they never go through the chat stream.

```typescript
// src/lib/document-edit-types.ts
// Workshop W8a — the edit delta vocabulary
// These are the only ways the user can mutate a document directly.
// Everything else (AI revisions, new sections) goes through the chat + merge flow.

export type EditDelta =
  | { type: 'cell_changed';    blockId: string; rowIdx: number; colIdx: number; value: string }
  | { type: 'paragraph_edited'; blockId: string; value: string }
  | { type: 'row_inserted';    blockId: string; afterRowIdx: number }
  | { type: 'row_deleted';     blockId: string; rowIdx: number }
  | { type: 'heading_edited';  blockId: string; value: string; level: 1|2|3 }
```

The backend `apply_edit_delta(blocks, delta)` is a **pure function** — same input always produces same output, no randomness, no model call. This is what lets the UI apply changes optimistically at <16ms.

### Optimistic UI Updates

The document panel applies edits locally before the backend confirms, then reconciles:

```
User edits cell
  → dispatch optimistic update to local A2UI state (immediate re-render)
  → POST /api/sessions/{sid}/document/edit (async)
      ├── success: backend returns confirmed A2UI diff; reconcile if different
      └── failure: roll back optimistic update; show error toast
```

This is standard optimistic mutation (same pattern as Firestore's `set()` with local cache). The user never waits for the network on a direct edit.

### ailang_parse External Dependencies

This feature requires three new capabilities from ailang_parse, not present in 0.9.x. These must be requested from the ailang_parse team before this sprint starts.

**Request 1 — `editable=True` flag on `a2ui_formatter`:**

```python
# Current (0.9.x):
spec = a2ui_formatter(blocks)  # always emits Text nodes

# Needed (0.10+):
spec = a2ui_formatter(blocks, editable=True)
# Text  → TextField (with value binding)
# Table rows → EditableTable cells
# Heading → HeadingField (editable heading)
# Images, TrackChanges → remain read-only (no editable counterpart)
```

**Request 2 — `apply_edit_delta` pure function:**

```python
# Needed (0.10+):
from ailang_parse import apply_edit_delta, EditDelta

new_blocks = apply_edit_delta(
    blocks=current_blocks,
    delta=EditDelta(type="cell_changed", block_id="b1", row=2, col=1, value="€2.8M")
)
# Pure: no side effects, no I/O, no model calls
# Raises EditValidationError if blockId not found or value fails content policy
```

**Request 3 — `generate()` with `source_template` for style preservation:**

```python
# Current (0.9.x):
docx_bytes = DocParse.generate(blocks, format="docx")
# → generates a clean DOCX with default styles (no original formatting)

# Needed (0.10+):
docx_bytes = DocParse.generate(
    blocks=blocks,
    format="docx",
    source_template=original_docx_bytes  # carries over fonts, margins, styles
)
# → generates DOCX that looks like the original but with the edited content
```

**Action required:** File these as a single feature request to the ailang_parse team with this design doc as context. Target: ailang_parse 0.10.0. Do not start the v6 sprint until Request 2 (`apply_edit_delta`) is available — it is on the critical path.

### Frontend Changes

**Modified Components:**

- `DocumentViewer` (from `document-ui.md`) — add editable mode toggle; switch `A2UIViewer` from `a2ui_formatter(blocks)` spec to `a2ui_formatter(blocks, editable=True)` spec when edit mode is active; wire `onAction` to `useDocumentEdit` hook instead of sending a chat message

**New Components:**

```
frontend/src/components/doc-editor/
  MergeBar.tsx          — "merge AI suggestions into document" bar shown when chat
                          contains a ```a2ui block the user hasn't merged yet
  ExportButton.tsx      — "Download DOCX/PPTX/XLSX" button with format picker
  EditConflictToast.tsx — shown when backend rejects an optimistic edit
```

**New Hook:**

```typescript
// src/hooks/useDocumentEdit.ts
// Workshop W8b — optimistic edit dispatch + backend sync
// Called by DocumentViewer's onAction. Applies the edit locally,
// fires the backend endpoint, and reconciles on response.
// This is the React side of the Python/React shared state model.

function useDocumentEdit(sessionId: string) {
  return {
    applyEdit: (delta: EditDelta) => { ... },  // optimistic + sync
    mergeAISuggestion: (a2uiSpec: A2UISpec) => { ... },
    exportDocument: (format: 'docx' | 'pptx' | 'xlsx') => { ... },
  }
}
```

### Backend Changes

**New Endpoints:**

`POST /api/sessions/{sessionId}/document/edit`
```
Request:  { delta: EditDelta }
Response: { diff: A2UIDiff, blockCount: number }
Auth:     Firebase ID token; session must belong to caller
```

`GET /api/sessions/{sessionId}/document/export`
```
Query:    format = docx | pptx | xlsx
Response: binary stream with Content-Disposition: attachment
Auth:     Firebase ID token; session must belong to caller
```

`POST /api/sessions/{sessionId}/document/merge`
```
Request:  { a2uiSpec: A2UISpec }  — the spec from a chat bubble the user approved
Response: { ok: true, newBlockCount: number }
```

**New ADK Tool** (for Mode B — chat-driven edits):

```python
# backend/tools/document_edit.py
# Workshop W8c — the agent's edit tool
# When the user asks the agent to make a specific change, the agent calls
# this tool rather than returning text. The tool mutates session state directly
# (deterministic) and returns an A2UI diff that becomes a ```a2ui block in
# the agent's response — visible in the chat bubble before merging.

async def apply_structured_edit(
    tool_context: ToolContext,
    block_id: str,
    edit_type: str,   # "cell_changed" | "paragraph_edited" | ...
    **kwargs,
) -> dict:
    blocks = tool_context.state["app:active_document"]
    delta = EditDelta(type=edit_type, block_id=block_id, **kwargs)
    new_blocks = apply_edit_delta(blocks, delta)
    tool_context.state["app:active_document"] = new_blocks
    return {
        "a2ui_spec": a2ui_formatter(new_blocks, editable=True),
        "changed_block_id": block_id,
    }
```

**New Module:** `backend/tools/document_edit.py` (~120 lines)
**Modified Module:** `backend/adk/agent_factory.py` — register `apply_structured_edit_tool` for document-aware skills (Doc Analyst, Data Extractor)

### Workshop Integration

**Comment specification:**

```python
# Workshop W8a — EditDelta: structured edits, not natural language
# apply_edit_delta is a pure function — same input, same output, no model call.
# This is the correct abstraction for user-initiated document mutations.
# The agent uses the same delta type when editing on behalf of the user (Mode B),
# so there's one code path for both human and AI edits.
# See: docs/talks/workshop.md §W8
```

```typescript
// Workshop W8b — useDocumentEdit: the React side of shared state
// onAction from A2UIViewer fires here. The delta goes to the backend;
// apply_edit_delta (Python) updates session.state["app:active_document"].
// React holds A2UI JSON; Python holds List[Block]. Same data, two views.
// The key principle: React never owns the document. It only renders a projection.
// See: docs/talks/workshop.md §W8
```

```python
# Workshop W8c — apply_structured_edit_tool: agent-initiated document edits
# When the user asks the agent to change something, the agent calls this tool
# instead of returning a text description of the change. The tool mutates
# session state (deterministic), returns an A2UI diff, and the diff appears
# as a ```a2ui block in the agent's response — in the chat bubble.
# User approves → MergeBar → POST /document/merge → document panel updates.
# See: docs/talks/workshop.md §W8
```

**File → workshop label mapping:**

| File | Label | Workshop moment |
|---|---|---|
| `src/lib/document-edit-types.ts` | `W8a` | "EditDelta: the structured edit vocabulary — no LLM needed to interpret these" |
| `src/hooks/useDocumentEdit.ts` | `W8b` | "React holds A2UI JSON; Python holds List[Block] — same data, two representations" |
| `backend/tools/document_edit.py` | `W8c` | "Agent edits use the same EditDelta as direct edits — one code path, two callers" |
| `frontend/src/components/doc-editor/MergeBar.tsx` | `W8d` | "The merge bar is the handoff from chat context to document state" |

## Implementation Plan

### Phase 0: External dependency (before sprint starts)
- [ ] File ailang_parse feature request with this doc attached — target 0.10.0
- [ ] Confirm `apply_edit_delta` API shape with ailang_parse team
- [ ] Confirm `editable=True` formatter flag emits `TextField` + `EditableTable` in Basic catalog
- [ ] Unblock sprint only when `apply_edit_delta` is available (pip-installable)

### Phase 1: Backend edit endpoint (~1 day)
- [ ] `EditDelta` Pydantic model + `apply_edit_delta` wrapper in `backend/tools/document_edit.py` (~50 lines)
- [ ] `POST /api/sessions/{sid}/document/edit` — validate session ownership, call `apply_edit_delta`, update session state, return A2UI diff (~60 lines)
- [ ] `apply_structured_edit_tool` ADK FunctionTool + register in `agent_factory.py` for doc skills (~50 lines)
- [ ] pytest: pure `apply_edit_delta` for all 5 delta types; edit endpoint 200/403/422; agent tool updates session state (~80 lines)

### Phase 2: Export endpoint (~0.5 day)
- [ ] `GET /api/sessions/{sid}/document/export` — call `ailang_parse.generate(blocks, format, source_template)`, stream bytes (~40 lines)
- [ ] `ExportButton.tsx` — format picker (DOCX/PPTX/XLSX), triggers fetch + download (~50 lines)
- [ ] pytest: export returns bytes with correct Content-Type; 403 for wrong session (~30 lines)

### Phase 3: Frontend edit mode (~1.5 days)
- [ ] `EditDelta` TypeScript type in `src/lib/document-edit-types.ts` (~30 lines)
- [ ] `useDocumentEdit` hook — optimistic dispatch, backend sync, rollback on error (~80 lines)
- [ ] Switch `DocumentViewer` `onAction` from chat-message path to `useDocumentEdit` (~20 lines)
- [ ] `EditConflictToast.tsx` — error state display (~30 lines)
- [ ] `MergeBar.tsx` — shown when active chat has unmerged `a2ui` suggestion; "Merge into document" button calls `POST /document/merge` (~60 lines)
- [ ] Frontend tests: optimistic update applies immediately; error rolls back; MergeBar visible when chat has unmerged A2UI (~60 lines)

### Phase 4: Workshop comments (~0.25 day)
- [ ] Add `W8a` comment to `document-edit-types.ts`
- [ ] Add `W8b` comment to `useDocumentEdit.ts`
- [ ] Add `W8c` comment to `document_edit.py`
- [ ] Add `W8d` comment to `MergeBar.tsx`
- [ ] Add W8 sub-module entries to `docs/talks/workshop.md`

## Migration & Rollout

**Firestore changes:** Add `edited_blocks` field to the `Document` collection (array of Block JSON, populated by the edit endpoint). Existing documents have `edited_blocks: []` — no data migration needed, field is optional.

**Feature flag:** Ship behind a `DOCUMENT_EDIT_ENABLED` env var defaulting to `false` until ailang_parse 0.10.0 is available. The edit toggle button in `DocumentViewer` is hidden when the flag is off — no visible degradation.

**Rollback:** Set `DOCUMENT_EDIT_ENABLED=false`. The document panel reverts to read-only; export button hidden. Zero data loss — `edited_blocks` remains in Firestore but is ignored.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `apply_edit_delta`: all 5 delta types produce correct updated blocks
- [ ] `apply_edit_delta`: raises `EditValidationError` for unknown `blockId`
- [ ] `POST /document/edit`: 200 + A2UI diff for valid edit
- [ ] `POST /document/edit`: 403 for session not owned by caller
- [ ] `POST /document/edit`: 422 for malformed delta
- [ ] `GET /document/export`: returns bytes, `Content-Type: application/vnd.openxmlformats...`
- [ ] `GET /document/export`: 403 for wrong session
- [ ] `apply_structured_edit_tool`: session state updated after agent calls it

### Frontend Tests (Vitest + RTL)
- [ ] `useDocumentEdit.applyEdit`: optimistic state update visible before fetch resolves
- [ ] `useDocumentEdit.applyEdit`: rolls back local state when fetch returns 422
- [ ] `MergeBar`: visible when `messages` contains an unmerged `a2ui` block; hidden otherwise
- [ ] `ExportButton`: calls `/document/export?format=docx` on click; triggers download

### Manual Testing
- [ ] Edit a table cell → change reflects instantly; backend confirmed within 500ms; download DOCX shows updated value
- [ ] Ask agent "change APAC to €2.8M" → `MergeBar` appears in chat; click Merge → document panel updates
- [ ] Edit a cell → simulate network error → edit rolls back with toast
- [ ] Export a document that has 10 edits → DOCX reflects all changes

## Security Considerations

- **Session ownership**: edit and export endpoints validate `session.userId == request.auth.uid` before any operation. A user cannot edit another user's document by guessing a `sessionId`.
- **Content validation**: `apply_edit_delta` validates `value` length (max 10,000 chars) and strips HTML tags before storing in Block ADT. No script injection via edited cell values.
- **Export data**: the exported DOCX contains the user's own data, served with `Content-Disposition: attachment`. No signed URL or persistent storage of the export — generated on demand and streamed directly.
- **Block ADT integrity**: `apply_edit_delta` is a pure function that only modifies the specific block addressed by `blockId`. It cannot rearrange document structure or inject new blocks — only the `merge` endpoint can add blocks, and only with content from the trusted AG-UI stream.

## Performance Considerations

- **Optimistic UI**: direct edits render in <16ms (synchronous local state update). Network round-trip to backend is ~100–200ms; reconciliation is invisible if the diff matches the optimistic result (which it always will for deterministic edits).
- **A2UI diff**: backend returns only changed nodes, not the full spec. For a single cell edit in a 200-row table, the diff is ~1KB, not ~50KB.
- **Export**: `ailang_parse.generate()` for a typical 50-block document takes ~500ms. Streamed as bytes — no temp file written to disk. Acceptable for a deliberate user action (clicking Download).
- **Session state size**: `List[Block]` for a 50-page document is typically 200–500 KB JSON. ADK session state has no documented size limit for `InMemorySessionService`; Firestore has a 1MB document limit. Store `edited_blocks` as a Firestore array of block diffs (not full blocks) to stay well within limits.

## External Dependencies

| Dependency | Version needed | Status | Action |
|---|---|---|---|
| ailang_parse | ≥ 0.10.0 | Not yet released | File feature request (this doc) before sprint starts |
| `apply_edit_delta` | in 0.10.0 | Not yet implemented | Critical path — do not start Phase 1 without this |
| `a2ui_formatter(editable=True)` | in 0.10.0 | Not yet implemented | Required for Phase 3 |
| `generate(source_template=...)` | in 0.10.0 | Partially in 0.9.x | Nice to have for Phase 2; fall back to unstyled generation |

## Success Criteria

- [ ] All frontend tests passing (`cd frontend && npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] Lint and typecheck clean
- [ ] Edit a table cell → download DOCX → cell shows updated value (end-to-end manual test)
- [ ] Chat-driven edit via `apply_structured_edit_tool` → `MergeBar` appears → merge → document panel updates
- [ ] `apply_edit_delta` handles all 5 delta types without exceptions
- [ ] Export 403s for wrong session (security test)
- [ ] Workshop comments (W8a–W8d) present and accurate
- [ ] W8 sub-module added to `docs/talks/workshop.md`

## Open Questions

- **ailang_parse `EditableTable` component**: does A2UI v0.9 Basic catalog include an `EditableTable` component, or does the ailang_parse team need to define a custom one? Confirm with both ailang_parse and A2UI maintainers before the sprint.
- **Merge conflict handling**: what if the user edits cell (r=2, c=1) directly, and the agent also edits the same cell in Mode B at the same time? Proposal: last-write-wins on the `blockId` level; show a "AI also changed this cell" toast. Full conflict resolution (CRDTs, OT) is deferred.
- **Track Changes**: the original DOCX has tracked changes (shown in the mockup). Should user edits generate new tracked changes in the exported DOCX, or are they applied as accepted edits? Proposal: apply as accepted edits for now; tracked-change generation requires deeper ailang_parse integration.
- **Firestore persistence of edits**: the `edited_blocks` array grows with every edit. Should we store a full snapshot after each edit or a compact delta log? Proposal: full snapshot after each save (not every keystroke) — simpler, and documents are small enough.

## Related Documents

- [document-ui.md](../v6.1.0/document-ui.md) — Block ADT, `a2ui_formatter`, `generate_document` tool, original editable mode note
- [session-and-memory.md](../v6.0.0/implemented/session-and-memory.md) — ADK session state scopes; `app:active_document` storage
- [chat-message-rendering.md](../v6.1.0/chat-message-rendering.md) — `A2UIRenderer` in chat bubbles; `onAction` in chat context vs. document context; `MergeBar` integration point
- [file-browser.md](../v6.1.0/file-browser.md) — document panel; `DocumentViewer` component extended here
- [streaming-and-protocols.md](../v6.0.0/implemented/streaming-and-protocols.md) — `A2UIRenderer` and `onAction` original spec
- [Product Axioms](../../product-axioms.md)
- [Mockup](../../frontend/public/mockups/document-workspace.html)
- [Workshop talk](../../talks/ai-ui-protocol-stack.md)
