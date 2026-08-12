# Agent-Driven Document Edits (Hybrid BlocksRenderer + A2UI)

**Status**: Design — not yet scheduled for a sprint
**Priority**: P1 for workshop demo (July 2026)
**Scope**: Fullstack (Frontend + Backend + agent tool)
**Depends on**: [document-rendering-decision.md](implemented/document-rendering-decision.md), [document-ui.md](document-ui.md) ✅, [document-data-layer.md](document-data-layer.md) (provides stable `blockId` to replace today's positional `block_index`)
**Created**: 2026-04-25

## TL;DR

The agent can already *read* the document (blocks are in its session). This design adds the ability for the agent to **propose edits** to specific blocks, with the user seeing the proposal *inline in the document* as a live A2UI sub-surface, and accepting or rejecting via a button. The accepted edit flows back into the Firestore `editedBlocks` field and the agent's session state.

**This is where A2UI's state-sync model earns its keep.** `BlocksRenderer` handles static content (read-mostly). When the agent proposes an edit, a small `<A2UIViewer>` sub-surface appears inline, sharing DataModel state between Python and React. The user's accept/reject action dispatches back to the agent. No request/response round-trip; the agent and the UI mutate one object.

## Why A2UI here (when we just decided against it for rendering)

The [document-rendering-decision.md](implemented/document-rendering-decision.md) ruled out A2UI for the document *frame* because:
- Static content needs no state sync
- No `Table` in v0_8 catalog
- BlockADT → HTML gives better rendering

None of those apply to an **edit-proposal sub-surface**:
- It's a single interactive control (TextField, Button) — exactly what A2UI's catalog has
- It *does* need state sync — the agent writes the proposed value, the user's accept mutates shared state
- It's small enough that `A2UIViewer` overhead is invisible
- Two-way binding removes an entire class of "user accepted → POST → agent receives → agent responds" round-trip logic

**This is the hybrid pattern A2UI was designed for:** one frame renderer for structure, A2UI sub-surfaces for live interactive sections within it.

## User story

1. User uploads `Q3-financial-report.docx`. It parses; `BlocksRenderer` shows headings, paragraphs, and a revenue table.
2. User types in chat: *"Change the Q3 EMEA revenue to $2.5M."*
3. Agent identifies the target block (table cell at row 2, col 2 — value "$1.2M"). Agent emits a `propose_edit` tool call with `{blockIndex: 4, path: "rows[1].cells[1].text", newValue: "$2.5M"}`.
4. Frontend renders an **inline A2UI sub-surface** *on top of that cell*:
   - Strikethrough old value, bold new value, accept/reject buttons
   - The values come from a live DataModel shared with the agent
5. User clicks ✅ Accept.
   - `sendAction({name: "accept_edit", context: {editId}})` fires
   - Agent receives the action, commits to Firestore via `editedBlocks[editId] = {...}` and removes the proposal from the DataModel
   - `BlocksRenderer` updates because `doc.blocks` (enriched with `editedBlocks`) changes
6. If user clicks ✗ Reject, same flow but agent logs the rejection and doesn't commit.

**Why this is a workshop-worthy demo:** The accept button visibly, instantly flips the cell value. No spinner, no round-trip, no "saving…" text. That immediacy IS A2UI.

## Architecture

### Data flow

```
┌─────────────────┐                    ┌─────────────────┐
│ Agent (Python)  │                    │ Frontend (React)│
│                 │                    │                 │
│ propose_edit    │── A2UI sub-surface─│ BlocksRenderer  │
│  tool call      │   with DataModel   │  + embedded     │
│                 │   {editId, old,    │    A2UIViewer   │
│                 │    new, path}      │                 │
│                 │                    │                 │
│                 │←── sendAction ─────│ User clicks     │
│ accept_edit     │    {accept, id}    │  accept/reject  │
│  handler        │                    │                 │
│                 │                    │                 │
│ writes          │── Firestore ──────→│ useDocument     │
│  editedBlocks   │    editedBlocks    │  re-reads       │
│                 │                    │                 │
└─────────────────┘                    └─────────────────┘
```

### Backend

**New agent tool: `propose_block_edit`**

```python
# backend/tools/documents/edit_tools.py
@FunctionTool
async def propose_block_edit(
    doc_id: str,
    block_index: int,
    path: str,           # JSONPath-ish: "text" | "rows[1].cells[1].text"
    new_value: str,
    reason: str,         # for the user — "because you asked for $2.5M"
) -> dict:
    """Propose an edit to a specific field within a block.

    The proposal is streamed to the frontend as an A2UI sub-surface. The user
    accepts or rejects. On accept, the edit is committed to Firestore's
    editedBlocks field keyed by a generated edit_id.
    """
    edit_id = f"edit_{uuid4().hex[:8]}"
    # Fetch current value for diff
    doc = get_document("parsed_documents", doc_id)
    old_value = _resolve_path(doc["blocks"][block_index], path)
    # Stream an A2UI component via AG-UI custom event
    await emit_a2ui_surface({
        "surface_id": f"edit-{edit_id}",
        "root": "edit-card",
        "components": [
            {"id": "edit-card", "component": {"Card": {
                "children": {"explicitList": ["old", "new", "row"]},
            }}},
            {"id": "old", "component": {"Text": {
                "text": {"path": f"/edits/{edit_id}/old"},
            }}},
            {"id": "new", "component": {"Text": {
                "text": {"path": f"/edits/{edit_id}/new"},
            }}},
            {"id": "row", "component": {"Row": {
                "children": {"explicitList": ["accept", "reject"]},
            }}},
            {"id": "accept", "component": {"Button": {
                "label": {"literalString": "Accept"},
                "action": {"literalString": "accept_edit"},
                "context": {"literalString": edit_id},
            }}},
            {"id": "reject", "component": {"Button": {
                "label": {"literalString": "Reject"},
                "action": {"literalString": "reject_edit"},
                "context": {"literalString": edit_id},
            }}},
        ],
        "data": {
            "edits": {edit_id: {"old": old_value, "new": new_value, "path": path, "blockIndex": block_index}},
        },
    })
    return {"edit_id": edit_id, "status": "proposed"}


# Agent-side action handler — receives the A2UI action
async def on_a2ui_action(event: A2UIActionEvent):
    if event.actionName == "accept_edit":
        edit_id = event.context
        # Commit to Firestore editedBlocks
        commit_edit_to_firestore(edit_id)
    elif event.actionName == "reject_edit":
        edit_id = event.context
        log_rejection(edit_id)
```

### Frontend

**New component: `EditProposalOverlay`** — positions an `<A2UIViewer>` over the target block in `BlocksRenderer`.

```tsx
// frontend/src/components/document/EditProposalOverlay.tsx
export function EditProposalOverlay({ proposal }: { proposal: A2UIEditSurface }) {
  const targetRef = useBlockRef(proposal.blockIndex);  // DOM ref to the block
  // Position absolutely over the block; fade-in animation
  return (
    <div
      className="absolute z-50 ..."
      style={{ top: targetRef.top, left: targetRef.left, width: targetRef.width }}
    >
      <A2UIViewer
        root={proposal.root}
        components={proposal.components}
        data={proposal.data}
        onAction={(evt) => sendA2UIAction(evt)}
      />
    </div>
  );
}
```

**Wire into `DocumentPanel`:**

```tsx
// frontend/src/components/document/DocumentPanel.tsx
export function DocumentPanel({ docId }: { docId: string }) {
  const { doc } = useDocument(docId);
  const proposals = useA2UIEditProposals(docId);  // subscribes to AG-UI custom events
  // ... rest as now
  return (
    <>
      <BlocksRenderer blocks={doc.blocks} />
      {proposals.map(p => <EditProposalOverlay key={p.editId} proposal={p} />)}
    </>
  );
}
```

### Why this is cleaner than rolling our own edit dispatch

Without A2UI, we'd build:
- A custom "proposal banner" React component
- A custom SSE event type for proposals
- A custom acknowledge endpoint
- Custom state management for pending proposals

With A2UI embedded:
- `A2UIViewer` renders the proposal
- `sendAction` handles acknowledgement
- `useA2UIState` subscribes to live updates
- The agent uses *the same DataModel* it uses for chat A2UI surfaces — one protocol, one codepath

**The state sync earns its keep.** This is what A2UI is for.

## Workshop demo script (2 minutes on stage)

> "We've parsed a financial report and rendered it with a custom blocks renderer — no A2UI. Why? Because rendering static content is a solved problem: HTML. Now watch what happens when the agent edits it."

*[Type in chat:]* "Change Q3 EMEA revenue to $2.5M."

> "Agent identifies the cell, and look — an A2UI sub-surface appears *inline over that cell*. The old value, the proposed value, accept and reject buttons. This little card is sharing state with the Python agent in real-time via A2UI's DataModel."

*[Click Accept]*

> "Notice: no spinner, no loading state. The cell updates instantly because there was no request/response round-trip — the agent and the UI were mutating the same object. That's A2UI's actual value proposition. Not 'JSON renders UI.' Two-way state sync between agent and human."

> "So we use A2UI where it earns its keep — chat responses, edit proposals, interactive forms. We render parsed documents with native HTML because that's a simpler, better-looking solution for static content. Same application, right tool for each surface."

## Dependencies + sequencing

- **Prerequisite:** basic AG-UI custom event channel for A2UI surfaces (exists in chat — may need extraction for doc panel)
- **Prerequisite:** agent tool registration in root agent + prompt engineering ("when user asks for an edit, call `propose_block_edit`")
- **Sprint estimate:** 3–5 days (backend tool + frontend overlay + wiring + demo polish)
- **Target:** land by 2026-06-15 to leave a month for demo rehearsal before workshop

## Open questions

- How does the A2UI sub-surface position itself absolutely over a BlocksRenderer block? Need a ref-based positioning hook, or use Portal + CSS anchoring.
- Multi-edit proposals: if the agent proposes 3 edits at once, do we show 3 overlays or a summary panel? Start with 3 overlays for demo simplicity.
- What happens if the block is scrolled off-screen? Scroll-to-block animation + pulse highlight on arrival.
- Rejections: should the agent see the reason? (Optional text field before reject → stretch goal.)

## Not in scope

- User-initiated inline editing (user clicks cell → types → saves). Simpler pattern, plain React `onChange → POST`. Tracked separately.
- Agent-drafted new sections / whole-document rewrites. These fit the existing "regenerate document" pattern via AI — no A2UI needed.
- Multi-user collaborative edits (CRDT across human users). A2UI's DataModel is per-session, not multi-user. Out of scope.
