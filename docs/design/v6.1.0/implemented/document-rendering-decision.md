# Document Rendering: Custom Renderer vs A2UIViewer

**Status**: Decided
**Priority**: P1
**Scope**: Frontend (small backend converter cleanup)
**Decided**: 2026-04-25
**Decision**: Render parsed documents from the **ailang-parse `blocks` BlockADT directly** with a custom React renderer; keep `A2UIViewer` for AI-emitted chat UI. Skip ailang-parse's `output_format="a2ui"` entirely.
**Supersedes**: implicit "A2UI everywhere" assumption in [document-ui.md](document-ui.md) and [ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)

## TL;DR

**A2UI is a state-sync protocol for agent-driven interactive UI, not a document rendering format.** Its unique value is `useA2UIComponent().setValue()` + `sendAction()` — two-way binding between a Python agent and React, with a shared mutable DataModel. That's powerful for chat, forms, and agent-proposed edits. It's overkill for read-mostly parsed document content, which is better served by native HTML (tables with headers, proper heading hierarchy, type-specific styling for equations/changes/bibitems) via a direct BlockADT renderer.

The ailang-parse workbench — the canonical demo of parsed-document rendering — does **not** use `A2UIViewer`. Its render path walks the BlockADT directly. That's the pattern we're copying.

**Decision:**
- **Chat surface:** `A2UIViewer` via `@a2ui/react` — agent emits interactive UI, two-way binding works as designed
- **Document rendering:** custom `BlocksRenderer` walking ailang-parse's BlockADT directly
- **Agent-driven document edits (future):** embed `<A2UIViewer>` sub-surfaces *inside* the document panel for edit-proposal UI. See [agent-driven-document-edits.md](agent-driven-document-edits.md).

Two pipelines today, with an opening for A2UI to come back into the document surface when the agent starts proposing edits — *that's* where A2UI's state-sync model earns its keep, not for static content display.

| Surface | Renderer | Source data | Why |
|---|---|---|---|
| Chat (AI-emitted UI) | `A2UIViewer` via `@a2ui/react` | A2UI ComponentInstances streamed by the agent | Interactive components, action events, two-way binding — the actual point of A2UI |
| Document panel (parsed file content) | Custom `BlocksRenderer` | ailang-parse `blocks` BlockADT (already stored in Firestore) | Read-mostly content. BlockADT gives us native `<table>` with `b.headers`+`b.rows`, heading hierarchy, type-specific styling. A2UI v0_8 has no `Table` component — forcing it through A2UI loses information. |
| Agent-driven edit surfaces (future) | `A2UIViewer` embedded inside `BlocksRenderer` | Agent emits editable A2UI sub-tree (e.g. a `TextField` over a specific cell, or a Card with inline proposals) | When the agent *proposes* or *collaboratively edits* a region of the document, shared DataModel is the right tool. The document *frame* stays BlocksRenderer; the editable sub-surface is A2UI. |

## What A2UI actually is (and why the naming confused us)

From reading `@a2ui/web_core/src/v0_8/data/model-processor.js` and `@a2ui/react/v0_8/`, A2UI has three load-bearing pieces:

1. **ComponentInstance schema** — `{id, component: {TypeName: {properties}}}`. A declarative JSON tree. This is what people see first and assume is "the protocol."
2. **DataModel** — a shared mutable state object, addressable by `/path/to/field`. Both Python and React hold references to this model. The agent mutates it; the frontend subscribes to changes.
3. **Action dispatch** — `sendAction({name, context})` from a UI component fires an event the agent receives and can respond to.

The killer feature is (2) + (3) together: **two-way binding**. A form on the frontend writes to the DataModel via `useA2UIComponent().setValue()`; the agent sees the change without a round-trip. The agent writes back; the form updates without a re-render request. It's CRDT-flavored shared state between Python and React.

**This is what A2UI is *for*.** Rendering JSON is the shallow surface. State sync is the substance.

**The naming collision that cost us a day:** ailang-parse has an `output_format="a2ui"` mode whose output is *not* A2UI ComponentInstance format — it's ailang-parse's own adjacency-list document AST that they labelled "a2ui." Separate thing. Nothing wrong with the format itself, but the name implied drop-in compatibility with `@a2ui/react` that doesn't exist. Worth raising with the ailang-parse team.

## When to use A2UI (and when not to)

| Use case | A2UI fit | Reasoning |
|---|---|---|
| Agent emits a card with buttons in chat | ✅ Perfect | Agent-authored interactive UI, action events flow back. This is the canonical A2UI use case. |
| Agent proposes a form to collect structured user input mid-conversation | ✅ Perfect | Two-way binding is the whole point. |
| Agent-driven document editing (AI says "change cell B3 to $2.5M" and the user sees it update live, can accept/reject) | ✅ Good fit | Shared DataModel means no round-trip for edit proposals. Embed an A2UI sub-surface in the doc panel for the edit affordance. |
| User-driven inline edit (user clicks cell → types → saves) | ⚠️ Overkill | Regular React `onChange + POST` is 3 lines, no protocol baggage. Unless you want the edit visible to the agent in real-time without the user saving — then A2UI pays off. |
| Static parsed document display | ❌ Wrong tool | No interaction, no state sync needed. BlockADT → HTML is simpler, gives better output (tables, headings, equations). |
| Markdown streaming from a chat response | ❌ Wrong tool | Plain text. Use AG-UI text events. |

## Problem

The original [document-ui.md](document-ui.md) sprint plan assumed: "ailang-parse → A2UI → A2UIViewer renders." That is *almost* what happens, but the seams don't line up.

### What ailang-parse actually emits for `output_format="a2ui"`

Adjacency-list document AST:

```json
[
  {"id": "doc",  "type": "container", "children": ["b_0", "b_1"], "props": {}},
  {"id": "b_0",  "type": "text",      "children": [], "props": {"text": "Title", "style": "Heading 1"}},
  {"id": "b_1",  "type": "table",     "children": [], "props": {"headers": [...], "rows": [...]}}
]
```

Lowercase types, `props` payload, children as ID strings. This is **HTML-like document structure**, not declarative interactive UI.

### What `@a2ui/react` v0_8 `A2UIViewer` requires (Zod schema)

```json
{
  "root": "doc",
  "components": [
    {"id": "doc", "component": {"Column": {"children": {"explicitList": ["b_0", "b_1"]}}}},
    {"id": "b_0", "component": {"Text":   {"text":     {"literalString": "Title"}, "usageHint": "h1"}}}
  ]
}
```

PascalCase types, ComponentInstance wrapping, `StringValueSchema` (`{literalString: ...}`), `ComponentArrayReferenceSchema` (`{explicitList: [...]}`).

### What broke when we tried to bridge them

1. **No `Table` in v0_8 standard catalog.** Available types are `Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs, Divider, Modal, Button, CheckBox, TextField, DateTimeInput, MultipleChoice, Slider`. We had to flatten table content to `Column → Row → Text` — losing headers, borders, alignment.
2. **No `Heading` type.** Mapped via `Text + usageHint: "h1"`. Workable but awkward.
3. **Schema mismatch.** Required a 60-line backend converter (`_ailang_nodes_to_a2ui` in `backend/tools/documents/ailang_parse.py`) that maps types and wraps every primitive.
4. **Firestore nested-array rejection.** `Block.rows: List[List[Cell]]` is invalid in Firestore (arrays cannot directly contain arrays). Required a separate wrapper to flatten to `[{cells: [...]}]`.

After all of that, we ship a parsed document that renders but loses table layout and isn't the demo-quality output we get from the ailang-parse workbench. We've taken on schema baggage *and* still don't get what we want.

### What the ailang-parse workbench actually does

[`docs/js/wasm-demo.js:1773`](file://<local-path>) (`buildA2UIDemo`) walks the adjacency-list nodes directly with explicit cases:

```js
case 'heading': // -> <h1>..<h6>
case 'text':    // -> <div class="dp-block-text">, with style-aware variants for equation/bibitem/abstract
case 'table':   // -> <table><thead>...<tbody> using b.headers, b.rows
case 'list':    // -> <ul>/<ol>
case 'change':  // -> <span class="dp-block-change--insert/delete">
case 'image':   // -> figure with caption
```

**No `A2UIViewer`. No converter. No schema.** Just a renderer per node type. That is what produces the "beautiful A2UI rendering" we want to match.

## Decision

### Frontend: replace `DocumentViewer` with custom `BlocksRenderer`

```tsx
// frontend/src/components/document/BlocksRenderer.tsx
interface Block {
  type: string; text?: string; level?: number; style?: string;
  headers?: Array<{text: string}>; rows?: Array<{cells: Array<{text: string}>}>;
  items?: string[]; ordered?: boolean;
  change_type?: string; author?: string;
  description?: string;
  children?: Block[];
}

function renderBlock(b: Block, key: number) {
  switch (b.type) {
    case "heading": {
      const Tag = `h${Math.min(b.level ?? 1, 6)}` as "h1";
      return <Tag key={key} className="...">{b.text}</Tag>;
    }
    case "text":
      // Style-aware: equation, bibitem, abstract get variant rendering; default → <p>
      if (b.style === "Heading 1") return <h1 key={key}>{b.text}</h1>;
      if (b.style === "Heading 2") return <h2 key={key}>{b.text}</h2>;
      if (b.style === "Heading 3") return <h3 key={key}>{b.text}</h3>;
      return <p key={key} className="...">{b.text}</p>;
    case "table":
      return (
        <table key={key} className="...">
          <thead><tr>{(b.headers ?? []).map((h, i) => <th key={i}>{h.text}</th>)}</tr></thead>
          <tbody>
            {(b.rows ?? []).map((row, i) => (
              <tr key={i}>{row.cells.map((c, j) => <td key={j}>{c.text}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
    case "list":
      const Tag = b.ordered ? "ol" : "ul";
      return <Tag key={key}>{(b.items ?? []).map((it, i) => <li key={i}>{it}</li>)}</Tag>;
    case "change":
      return (
        <span key={key} className={b.change_type === "deletion" ? "..." : "..."}>
          {b.text} {b.author && <em className="text-xs">({b.author})</em>}
        </span>
      );
    case "image":
      return <figure key={key}>[Image: {b.description ?? "embedded"}]</figure>;
    case "section":
      return <section key={key}>{(b.children ?? []).map((c, i) => renderBlock(c, i))}</section>;
    default:
      return <div key={key}>{b.text ?? JSON.stringify(b)}</div>;
  }
}

export function BlocksRenderer({ blocks }: { blocks: Block[] }) {
  return <div className="space-y-2">{blocks.map((b, i) => renderBlock(b, i))}</div>;
}
```

Style with Tailwind tokens to match shadcn theme. Reference styling from the workbench's `dp-block-*` classes.

### Backend: simplify

- **Stop calling `parse_gcs_file(gs_url, output_format="a2ui")` in `upload.py` and the reparse route.** We don't need it.
- **Delete `_ailang_nodes_to_a2ui`** from `backend/tools/documents/ailang_parse.py` (~70 lines).
- **Drop the `a2uiComponents` field** from Firestore writes in upload.py and reparse.
- **Keep the `Block.rows` flattening fix** in `_extract_content` (Firestore correctness for nested arrays — unrelated to this decision).
- **Keep the `blocks` field** — already stored, that's our source of truth now.

### Chat surface: unchanged

`A2UIRenderer` in `ChatMessageList` still renders agent-emitted A2UI through `@a2ui/react`'s `A2UIViewer`. That's the protocol used as designed.

## What we keep

- Two-way edit flow plan (block edits sent back to backend) — design unchanged. The renderer is just simpler.
- Firestore storage shape — `a2uiComponents` field stays. Existing reparse + retry buttons keep working.
- A2UI for chat — fully native, no compromise.

## What we drop

- Backend `_ailang_nodes_to_a2ui` converter (~60 lines)
- Frontend dependency on the ailang nodes ↔ A2UI ComponentInstance mapping
- The synthetic `__label`/`__value` Row expansion for `key-value`

## Risks / open questions

- **Catalog drift.** If ailang-parse adds new node types (`equation-display`, `bibitem`, `abstract` are visible in the workbench), our renderer needs explicit cases. Mitigation: default fallback for unknown types renders `<div>{p.text || JSON.stringify(p)}</div>` so nothing disappears silently.
- **Re-using A2UI primitives.** If we later want shadcn-styled cards/buttons inside the document, we *can* embed `<A2UIViewer>` for sub-trees that *are* component instances. Don't preclude this.
- **Editing.** Block edits flow back via a custom dispatch (callback prop on the renderer), not via `useA2UIComponent().setValue()`. Slightly different from the chat path but a single-purpose hook is fine.

## Workshop learnings (July 2026)

This detour is a teaching moment, and a good counterweight to "adopt protocols everywhere" dogma:

1. **Protocols have a *primary purpose* — learn it before adopting.** A2UI's primary purpose is **state-sync between agent and UI**, not JSON rendering. We spent half a day treating it as a fancy rendering format. The DataModel + `setValue()` + `sendAction()` two-way binding is the substance; the component schema is just the surface.

2. **Naming collisions cost time.** ailang-parse's `output_format="a2ui"` is not the A2UI ComponentInstance spec — it's their own document AST with a misleading name. Assumption that they'd interop cost ~1 day of work.

3. **Match the canonical demo.** The ailang-parse workbench is the best-looking public A2UI-adjacent demo we could find. Reading its source revealed it does *not* use `A2UIViewer` for documents — it walks BlockADT directly. Whenever a "but this demo works fine" reference exists, find its renderer before building yours.

4. **"One pipeline for everything" is premature abstraction.** Two purpose-built renderers — `A2UIViewer` for agent UI, `BlocksRenderer` for documents — is simpler, ships faster, and produces better output than forcing one protocol to cover both.

5. **But don't close the door.** A2UI's two-way binding IS the right tool for *agent-driven document edits* (AI proposes a change → user sees it live → accepts/rejects via action). The correct pattern is **BlocksRenderer for the frame, A2UIViewer for editable sub-surfaces**. Hybrid, not either-or. See [agent-driven-document-edits.md](agent-driven-document-edits.md) for the demo plan.

Talk slot: the protocol stack section. Sub-bullet under A2UI:
> "A2UI is not a rendering format — it's a state-sync protocol. That matters when you decide what to use it for. Use it where shared mutable state between agent and UI pays off (agent-proposed edits, live forms). Don't use it for read-mostly content. The ailang-parse workbench itself doesn't use A2UIViewer for parsed documents — it walks the BlockADT directly."

**Demo idea for the workshop:** split-pane showing a parsed document. Left pane renders via `BlocksRenderer` (static content). Right pane is the chat. When the user asks the agent to edit a specific cell, a small `A2UIViewer` sub-surface appears inline over the cell with the proposed change + accept/reject buttons — agent and user collaborating on the same DataModel. This single moment visualizes the whole "two pipelines, right tool for each job" story.

## Action items

All shipped on the `dev` branch as of 2026-04-27.

- [x] Add `frontend/src/components/document/BlocksRenderer.tsx` walking the BlockADT (~185 lines as shipped). Styling inspired by `ailang-parse/docs/js/wasm-demo.js` `renderBlocks`.
- [x] Replace `DocumentViewer.tsx` body to use `BlocksRenderer` instead of `A2UIRenderer`.
- [x] Update `useDocument` to expose `blocks: Block[]` (drop `a2uiComponents`).
- [x] Update `frontend/src/components/document/__tests__/DocumentPanel.test.tsx` fixture to BlockADT shape.
- [x] Backend: drop the a2ui parse call in `upload.py` `_run_parse` and the reparse route. Drop `a2uiComponents` field from Firestore writes.
- [x] Backend: delete `_ailang_nodes_to_a2ui` and `_A2UI_TYPE_MAP` from `ailang_parse.py`.
- [x] Update [document-ui.md](../document-ui.md) "Document panel" section to reference this decision.
- [x] Update [ai-ui-protocol-stack.md](../../../talks/ai-ui-protocol-stack.md) A2UI section.
