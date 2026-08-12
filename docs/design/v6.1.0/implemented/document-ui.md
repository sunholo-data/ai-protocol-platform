# Document-Centric UI with Skills Navigation

**Status**: Partially shipped — document rendering & split-pane done; **SkillsBar** carved out for a follow-up sprint
**Priority**: P1 (Medium)
**Scope**: Fullstack (Frontend + Backend)
**Dependencies**: [Frontend Architecture](../v6.0.0/implemented/frontend-architecture.md), [Skills Data Model](../v6.0.0/implemented/skills-data-model.md), [Streaming & Protocols](../v6.0.0/implemented/streaming-and-protocols.md), [Tools Porting Guide](../v6.0.0/implemented/tools-porting-guide.md), [File Browser](implemented/file-browser.md), [DOC-AI-PIPELINE](implemented/document-to-ai-pipeline.md) ✅
**Created**: 2026-04-10
**Last Updated**: 2026-04-27

## Architectural update (2026-04-25): BlocksRenderer, not A2UI

This doc's original plan assumed "ailang-parse → A2UI → `<A2UIViewer>` renders." That was wrong. See [document-rendering-decision.md](implemented/document-rendering-decision.md) for the full reasoning, but the short version:

- **A2UI is a state-sync protocol** (DataModel + `setValue()` + `sendAction()`), not a document-rendering format. Forcing parsed documents through `A2UIViewer` loses information (no `Table` in v0_8 catalog), requires lossy conversion, and produces worse output than the canonical ailang-parse workbench demo — which doesn't use `A2UIViewer` for documents either.
- **Documents render via `BlocksRenderer`** — a custom React component that walks the ailang-parse BlockADT directly. Proper `<table>` with headers/rows, heading hierarchy, type-specific styling (equation/bibitem/abstract), change-tracking.
- **Chat still uses `A2UIRenderer`** — agent-emitted interactive UI is exactly what A2UI is for.
- **Agent-driven document edits** — planned as a hybrid: `BlocksRenderer` frames the doc, `A2UIViewer` embeds for specific editable sub-surfaces where two-way binding pays off. See [agent-driven-document-edits.md](agent-driven-document-edits.md).

## Implementation Status

### Shipped — Document Workspace (DOC-UI-IMPL sprint, 2026-04-24, + blocks-direct pivot 2026-04-25)
- Pydantic models: `Block`, `DocMetadata`, `DocSummary`, `ParsedDocument`, `EditedBlock` — `backend/db/models/document.py`
- JSON schema contract: `docs/design/v6.0.0/contracts/document.schema.json`
- `A2UIRenderer` component exists in frontend, used in chat messages only (NOT in the document panel — see decision doc)
- File browser sidebar, folder tree, tab management, upload UI — `FILE-BROWSER` sprint ✅
- Backend artifact pipeline — parsed blocks stored in Firestore + loaded into ADK session ✅
- `DocumentPanel.tsx` — header + body + footer composition, useDocument hook
- `DocumentHeader.tsx`, `DocumentFooter.tsx` — filename, format badge, source link, block summary stats
- `DocumentViewer.tsx` — renders via `BlocksRenderer` (was briefly `A2UIRenderer`; pivoted 2026-04-25)
- `BlocksRenderer.tsx` — walks BlockADT, handles heading/text/table/list/change/image/section
- Split-pane layout in chat page: document left (50%), chat right (50%) when a tab is active
- `documentId` passed in AG-UI stream request body (agent knows which doc is open)
- Retry/reparse button on failed or empty docs in the sidebar
- Delete button per doc (soft UX: click twice to confirm)
- Dedup of same-filename re-uploads per folder

Sprint plan: [implemented/document-ui-sprint.md](implemented/document-ui-sprint.md).
Architectural decision behind the rendering pivot: [implemented/document-rendering-decision.md](implemented/document-rendering-decision.md).

### Not yet shipped (carved out for follow-up sprints)
- **`SkillsBar` — horizontal skills navigation tab bar** (§1 of this doc) — ✅ shipped 2026-04-27 via [implemented/skills-bar-sprint.md](implemented/skills-bar-sprint.md). Tab-per-skill, active-skill drives chat agent, friendly URLs honoured.
- **User-driven inline edit** (simple `onChange → PATCH /api/documents/{docId}/blocks/{idx}`). Designed in §"Editable Mode" below. No sprint scheduled.
- **Agent-driven edits with live A2UI sub-surface proposals** — fully designed in [agent-driven-document-edits.md](agent-driven-document-edits.md). Target: 2026-06-15 (workshop demo).

## Problem Statement

v5 treats documents as attachments — uploaded files listed as links in a sidebar, passed as opaque blobs to the backend. Users can't see document contents without opening the original file in a separate application. The AI can read documents, but the user has no shared visual representation of what the AI sees. This creates a trust gap: users can't verify extraction accuracy, can't correct errors inline, and can't see how their document maps to the AI's understanding.

v6's document-centric model flips this: **parsed documents are the primary workspace object**, rendered as interactive A2UI components that both the user and the AI operate on together. Users see their document's structure (headings, tables, images, tracked changes) rendered in the platform, can edit content inline, and can instruct the AI to work with specific sections. After the conversation, the AI generates new documents from the conversation output.

**Current State:**
- v5 `Documents.tsx` shows file links with icons in the sidebar — no content rendering
- v5 `DocumentUpload.tsx` handles drag-drop upload to Firebase Storage — no parsing on upload
- Backend has `ailang-parse>=0.5.1` as a dependency but no ADK FunctionTool wrapper
- `@a2ui/react` v0.9.0-alpha.0 in `package.json` (A2UI v0.9 alpha, as of 2026-04-19) — not yet used. v0.9 rename: optional component set is now **"Basic"** (was "Standard"); custom catalogs via `CatalogConfig.from_path()`.
- `@ailang/parse` JS SDK exists on npm — not in `package.json`
- ailang-parse 0.9.3 includes `docparse/services/a2ui_formatter` — converts Block ADT to A2UI JSON natively
- `sunholo/a2ui` v0.1.0 package exists — A2UI component builder with flat adjacency-list JSON

**Impact:**
- Transforms the core UX from "chat with file attachments" to "collaborative document workspace"
- Enables document verification (EARNED TRUST) — users see exactly what the AI extracted
- Enables inline correction — users fix extraction errors without re-uploading
- Enables document generation — conversations produce real output documents (DOCX, PPTX, XLSX)
- Skills become document-aware — skills in the top bar are contextual to the active document

## Goals

**Primary Goal:** Build a document-first UI where parsed documents are rendered as interactive A2UI components, skills provide contextual actions via a top navigation bar, and conversations produce output documents.

**Success Metrics:**
- Document upload → parsed preview visible in <2s (deterministic formats) or <5s (AI-powered formats)
- Users can view full document structure (headings, tables, images, changes) without leaving the platform
- Users can edit text content inline and see changes reflected in chat context
- Skills bar loads user's skills in <500ms, allows one-click skill selection
- Document generation produces valid DOCX/PPTX/XLSX from conversation output
- Source link to original file always visible and accessible

**Non-Goals:**
- Real-time collaborative editing (multi-user simultaneous editing)
- Full-fidelity document rendering (pixel-perfect reproduction of original formatting)
- Offline document editing
- Document version control / diff UI (future)
- Mobile-optimized document editing (responsive viewing only)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Client-side JS parsing for instant preview; lazy backend parsing; skeleton states while AI formats parse; streaming document generation |
| 2 | EARNED TRUST | +1 | Users see exactly what the AI sees — parsed blocks with source attribution. Inline editing lets users correct errors. Link to original source always visible |
| 3 | SKILLS, NOT FEATURES | +1 | Skills in top bar — discoverable, selectable, contextual to active document. Document operations are skills, not hidden features |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Deterministic parsing (ailang-parse, 11ms, zero LLM tokens) for Office/HTML/CSV. AI models only for PDF/images/audio. No LLM waste on structured formats |
| 5 | GRACEFUL DEGRADATION | +1 | If ailang-parse unavailable: fall back to file link (v5 behavior). If A2UI render fails: fall back to markdown. If JS SDK unavailable: backend-only parsing. Each layer degrades independently |
| 6 | PROTOCOL OVER CUSTOM | +1 | A2UI for document rendering (not custom React components). AG-UI for streaming parse results. ailang-parse unified Block ADT (not format-specific parsers) |
| 7 | API FIRST | +1 | Parse API (`POST /api/documents/parse`) serves all channels. Telegram/email get markdown rendering of same blocks. Web gets A2UI. Same data, different renderers |
| 8 | OBSERVABLE BY DEFAULT | 0 | Covered by existing AG-UI tracing and backend OTEL |
| 9 | SECURE BY CONSTRUCTION | +1 | Documents scoped per-user-per-skill. A2UI renderer is pure data (JSON, no executable code). No client-side document access beyond user's own files. Signed URLs for original source access |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | JS SDK adds client-side parsing (~20KB) for instant preview — slight tension with thin client. But parsing is deterministic transformation, not business logic. Backend remains authoritative |
| | **Net Score** | **+8** | Threshold: >= +4. Strong alignment |

## Design

### Overview

The document UI has three integrated parts:

1. **Skills Navigation Bar** — horizontal top bar with the user's skills, contextual to active document
2. **Document Workspace** — split-pane layout with rendered document (A2UI) and contextual chat
3. **Document Lifecycle** — upload → parse → store → render → chat → edit → generate

All three are unified: selecting a skill from the top bar changes what the chat can do with the active document. The document panel is the shared context between user and AI.

### 1. Skills Navigation Bar

#### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ [Aitana Logo]  │ 📄 Doc Analyst │ 🔍 Researcher │ 📊 Extractor │ + │
│                │   (active)     │               │              │   │
├────────────────┴────────────────┴───────────────┴──────────────┴───┤
│                                                                     │
│  ┌─── Document Panel ──────────┐  ┌─── Chat Panel ───────────────┐ │
│  │                             │  │                               │ │
│  │  [A2UI rendered document]   │  │  [Contextual conversation]   │ │
│  │                             │  │                               │ │
│  └─────────────────────────────┘  └───────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

#### Behavior

- **Skills as tabs**: User's skills displayed as horizontal tabs in top bar
- **Active skill**: Highlighted tab determines which ADK agent handles chat messages
- **Contextual tools**: Each skill's `skillMetadata.tools` determines what operations are available on the document (e.g., "Document Analyst" has `ai_search` + `structured_extraction`, "Data Extractor" has `structured_extraction` + `file_browser`)
- **+ button**: Opens skill creation wizard (quick-create from template or full wizard)
- **Skill switching**: Changing skill preserves the document context but starts a new chat session with the new skill's agent
- **Overflow**: Skills that don't fit scroll horizontally; chevron indicators for overflow
- **Skill badges**: Small protocol icons (A2UI, MCP) below skill name indicate capabilities

#### Data Flow

```
User clicks skill tab
    → Frontend sets activeSkillId in SkillContext
    → Chat provider reconnects AG-UI stream to /api/skill/{activeSkillId}/stream
    → Document context (parsed blocks) included in session state
    → New skill's agent receives document as context automatically
```

#### Skills Top Bar Component

```typescript
// frontend/src/components/navigation/SkillsBar.tsx

interface SkillsBarProps {
  skills: SkillConfig[]         // User's skills (from GET /api/skills?mine=true)
  activeSkillId: string | null  // Currently selected skill
  onSkillSelect: (id: string) => void
  onCreateSkill: () => void     // Opens wizard
}
```

Skills are fetched from the existing `GET /api/skills` endpoint with `ownerId` filter. The bar also shows recently-used public skills (from session history) for quick access.

### 2. Document Workspace

#### Split-Pane Layout

The main workspace is a resizable split-pane:

- **Left: Document Panel** — A2UI-rendered document with source link
- **Right: Chat Panel** — AG-UI streaming chat, contextual to the document
- **Default split**: 50/50 on desktop, stacked (document above chat) on mobile
- **Resizable**: Drag handle between panes. Collapse either side to focus

#### Document Panel Architecture

```
┌── Document Panel ──────────────────────────────────────┐
│ ┌── Header Bar ──────────────────────────────────────┐ │
│ │ 📄 quarterly-report.docx  │  ↗ Open Original  │ ⋮ │ │
│ │ By: John Smith  │  Modified: 2026-04-08          │ │ │
│ └────────────────────────────────────────────────────┘ │
│                                                        │
│ ┌── A2UI Rendered Content ───────────────────────────┐ │
│ │                                                    │ │
│ │  [Heading] Q1 2026 Financial Summary               │ │
│ │                                                    │ │
│ │  [Text] Revenue grew 23% year-over-year...         │ │
│ │                                                    │ │
│ │  [Table]                                           │ │
│ │  ┌─────────┬──────────┬──────────┐                 │ │
│ │  │ Region  │ Revenue  │ Growth   │                 │ │
│ │  ├─────────┼──────────┼──────────┤                 │ │
│ │  │ EMEA    │ €2.3M    │ +18%     │                 │ │
│ │  │ APAC    │ €1.8M    │ +31%     │                 │ │
│ │  └─────────┴──────────┴──────────┘                 │ │
│ │                                                    │ │
│ │  [Change] ✏️ Track Change (Jane, 2026-04-05):      │ │
│ │  "Updated APAC figures to include Q1 actuals"      │ │
│ │                                                    │ │
│ │  [Image] 📊 Chart: Revenue by Region               │ │
│ │  (AI description shown if image not renderable)    │ │
│ │                                                    │ │
│ └────────────────────────────────────────────────────┘ │
│                                                        │
│ ┌── Footer ──────────────────────────────────────────┐ │
│ │ 42 blocks │ 3 tables │ 2 images │ 5 changes       │ │
│ └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

#### A2UI Rendering Strategy

ailang-parse's `output_format="a2ui"` returns flat adjacency-list JSON that `@a2ui/react`'s `<A2UIViewer>` can render directly:

```typescript
// frontend/src/components/document/DocumentViewer.tsx

import { A2UIViewer } from '@a2ui/react'
import { aitanaTheme } from '@/themes/aitana-a2ui'

interface DocumentViewerProps {
  a2uiRoot: string                    // Root node ID from a2ui_formatter
  a2uiComponents: ComponentInstance[] // Flat component list
  metadata: DocMetadata               // Title, author, dates
  sourceUrl: string                   // Link to original file
  onAction?: (action: A2UIActionEvent) => void  // Edit callbacks
}

function DocumentViewer({ a2uiRoot, a2uiComponents, metadata, sourceUrl, onAction }: DocumentViewerProps) {
  return (
    <div className="flex flex-col h-full">
      <DocumentHeader metadata={metadata} sourceUrl={sourceUrl} />
      <ScrollArea className="flex-1">
        <A2UIViewer
          root={a2uiRoot}
          components={a2uiComponents}
          onAction={onAction}
          theme={aitanaTheme}
        />
      </ScrollArea>
      <DocumentFooter summary={metadata.summary} />
    </div>
  )
}
```

#### Block-to-A2UI Mapping

ailang-parse's `a2ui_formatter` maps Block ADT types to A2UI component nodes:

| Block Type | A2UI Component | Behavior |
|-----------|---------------|----------|
| `Heading` | `Text` (bold, sized by level) | Display, selectable for chat reference |
| `Text` | `Text` (markdown-enabled) | Display; editable mode uses `TextField` |
| `Table` | `List` of `Row` components | Display; cells selectable |
| `Image` | `Image` (URL or base64) | Display with AI description fallback |
| `List` | `List` (ordered/unordered) | Display |
| `Section` | `Card` with `Tabs` (for slides/sheets) | Collapsible sections; tabs for multi-sheet/slide docs |
| `Change` | `Card` (styled as diff) | Shows author, date, insertion/deletion |
| `Audio` | `AudioPlayer` | Playback with transcription text |
| `Video` | `Video` | Playback with transcription |

#### Editable Mode

When the user activates editing (toggle button in header), text blocks switch from `Text` to `TextField` A2UI components with two-way data binding:

```typescript
// Edit action flows back to backend
function handleDocumentAction(action: A2UIActionEvent) {
  if (action.actionName === 'field_changed') {
    // Update local state optimistically
    updateBlock(action.context.blockId, action.context.value)
    
    // Send edit to backend for session state persistence
    sendEdit({
      documentId: activeDocument.id,
      blockIndex: action.context.blockIndex,
      newValue: action.context.value,
    })
  }
}
```

**Editable A2UI mode** requires enhancement to ailang-parse's `a2ui_formatter`:
- Current: generates read-only `Text` components
- Needed: flag to generate `TextField` components with `data` bindings for editable content
- Roadmap item for ailang-parse (see [Roadmap Requirements](#ailang-parse-roadmap-requirements))

> **A2UI v0.9 alignment (2026-04-19):** component vocabulary above (`Text`, `TextField`, `List`, `Row`, `Card`, `Tabs`, `Image`, `AudioPlayer`, `Video`) is the **Basic** catalog (renamed from "Standard" in v0.9). Resilient streaming (incremental parse/heal) and client-to-server collaborative sync are **not** adopted for v6.0.0 — single-user edit model, post-parse A2UI render. Revisit before the July 2026 workshop if the demo needs mid-stream rendering.

### 3. Document Lifecycle

#### Upload → Parse → Store → Render

```
User drops file in upload zone
    │
    ├── [Frontend — instant preview]
    │   @ailang/parse JS SDK: DocParse.parseFile(file, "a2ui")
    │   → Renders A2UI preview immediately (deterministic formats: <100ms)
    │   → Shows "Parsing..." skeleton for AI formats (PDF, images)
    │
    └── [Backend — authoritative parse + storage]
        POST /api/documents/upload
        → Upload to GCS (Firebase Storage)
        → ailang_parse.DocParse.parse_url(signed_url, "blocks")
        → Store blocks + metadata in Firestore
        → Return parsed document with A2UI representation
        → Frontend replaces preview with authoritative render
```

#### Lazy + Batched Parsing

Not all documents need to be parsed immediately. Strategy:

| Trigger | Parse When | Priority |
|---------|-----------|----------|
| **User uploads** | Immediately (foreground) | High — user is waiting |
| **Skill assigned documents** | On first view (lazy) | Medium — parse when user opens document panel |
| **Bulk import** | Background Cloud Task | Low — batch parse, notify when done |
| **Re-parse (format update)** | On-demand button | Low — user explicitly requests |

#### Firestore Schema

```
parsed_documents/{docId}
├── skillId: string              # Owning skill (scopes access)
├── userId: string               # Uploader
├── sourceUrl: string            # GCS signed URL to original file
├── sourceFormat: string         # "docx", "pdf", "xlsx", etc.
├── originalFilename: string     # User-facing name
├── storagePath: string          # GCS path
├── status: "pending" | "parsing" | "parsed" | "failed" | "edited"
├── parsedAt: timestamp | null
├── metadata: {                  # DocMetadata from ailang-parse
│     title: string
│     author: string
│     created: string
│     modified: string
│     pageCount: number
│   }
├── summary: {                   # Summary from ailang-parse
│     totalBlocks: number
│     headings: number
│     tables: number
│     images: number
│     changes: number
│   }
├── blocks: Block[]              # Full block array (for docs <500 blocks)
├── a2uiRoot: string             # Root node ID for A2UI rendering
├── a2uiComponents: object[]     # Pre-computed A2UI component tree
├── editedBlocks: {              # User edits overlay (sparse map)
│     [blockIndex: string]: {
│       originalText: string
│       editedText: string
│       editedAt: timestamp
│       editedBy: string
│     }
│   }
├── createdAt: timestamp
└── updatedAt: timestamp

# For large documents (>500 blocks), blocks stored in subcollection:
parsed_documents/{docId}/block_pages/{pageIndex}
├── startIndex: number
├── endIndex: number
├── blocks: Block[]              # Chunk of blocks (100 per page)
└── a2uiComponents: object[]     # Pre-computed A2UI for this chunk
```

#### Chat → Document Context

When a user chats with a skill while a document is active, the document blocks are included in the agent's context:

```python
# backend/skills/document_context.py

async def build_document_context(doc_id: str, user_id: str) -> str:
    """Build markdown context from parsed document for agent consumption."""
    doc = await get_parsed_document(doc_id, user_id)
    
    if not doc or doc.status != "parsed":
        return ""
    
    # Apply user edits over original blocks
    blocks = apply_edits(doc.blocks, doc.edited_blocks)
    
    # Convert to markdown for agent context (not A2UI — agent reads markdown)
    context = f"## Active Document: {doc.original_filename}\n\n"
    context += f"Source: {doc.source_url}\n"
    context += f"Format: {doc.source_format} | Author: {doc.metadata.author}\n\n"
    context += blocks_to_markdown(blocks)
    
    return context
```

The agent receives the document as markdown in its context, not as A2UI. A2UI is purely for the frontend rendering. This separation means:
- Agent works with clean text (efficient token usage)
- Frontend shows rich rendering (A2UI components)
- Same blocks serve both purposes

#### Document Selection in Chat

Users can reference specific document sections in chat:

```
┌── Chat Panel ─────────────────────────────────────────┐
│                                                       │
│  🤖 I can see this document has 3 tables and 5        │
│     tracked changes. What would you like to do?       │
│                                                       │
│  👤 Can you extract the revenue figures from the       │
│     table in section 2?                               │
│                                                       │
│  🤖 From the "Regional Performance" table:            │
│                                                       │
│  ┌────────────────────────────────────────────┐       │
│  │ ```a2ui                                    │       │
│  │ { "type": "table", ... }                   │       │
│  │ ```                                        │       │
│  │ [A2UI table rendered inline in chat]       │       │
│  └────────────────────────────────────────────┘       │
│                                                       │
│  Sources: quarterly-report.docx, Section 2, Table 1   │
│                                                       │
└───────────────────────────────────────────────────────┘
```

The agent can emit A2UI components in its responses (via AG-UI `TEXT_MESSAGE_CONTENT` with `\`\`\`a2ui` markers) to show extracted data, tables, or forms inline in the chat. This is the standard A2UI-in-AG-UI pattern from [streaming-and-protocols.md](../streaming-and-protocols.md).

#### Edit → Regenerate

After chatting about a document, the user can generate a new document from the conversation:

```
User: "Generate a summary document from our analysis"
    │
    Agent calls generate_document tool
    │
    └── POST /api/documents/generate
        → ailang_parse.generate(blocks, format="docx", prompt=conversation_summary)
        → Upload generated file to GCS
        → Return signed URL for download
        → A2UI preview of generated document shown in chat
        → Download button with format selector (DOCX, PDF, PPTX, etc.)
```

### 4. Backend Components

#### ADK FunctionTool: Document Parse

```python
# backend/tools/document_parse.py

from google.adk.tools import FunctionTool
from ailang_parse import DocParse

client = DocParse()

async def parse_document(
    url: str,
    output_format: str = "markdown+metadata",
) -> dict:
    """Parse a document and return structured content.
    
    Args:
        url: GCS signed URL or public URL to document
        output_format: One of: blocks, markdown, html, markdown+metadata, a2ui
    
    Returns:
        Parsed document with blocks, metadata, and summary
    """
    result = client.parse_url(url, output_format=output_format)
    return {
        "status": result.status,
        "filename": result.filename,
        "format": result.format,
        "blocks": [b.__dict__ for b in result.blocks] if result.blocks else [],
        "metadata": result.metadata.__dict__,
        "summary": result.summary.__dict__,
        "markdown": result.markdown,
        "sections": [s.__dict__ for s in result.sections] if result.sections else [],
    }

parse_document_tool = FunctionTool(func=parse_document)
```

#### ADK FunctionTool: Document Generate

```python
# backend/tools/document_generate.py

from google.adk.tools import FunctionTool
from ailang_parse import DocParse

client = DocParse()

async def generate_document(
    content: str,
    format: str = "docx",
    title: str = "Generated Document",
) -> dict:
    """Generate a document from content.
    
    Args:
        content: Markdown content to convert to document
        format: Output format — docx, pptx, xlsx, odt, odp, ods, html, md
        title: Document title
    
    Returns:
        URL to generated document for download
    """
    # Generate via ailang-parse
    result = client.generate(content=content, format=format, title=title)
    
    # Upload to GCS
    url = await upload_to_gcs(result.data, f"{title}.{format}")
    
    return {
        "status": "success",
        "url": url,
        "format": format,
        "filename": f"{title}.{format}",
    }

generate_document_tool = FunctionTool(func=generate_document)
```

#### Document API Endpoints

```python
# backend/routes/documents.py

@router.post("/api/documents/upload")
async def upload_document(
    file: UploadFile,
    skill_id: str,
    user: FirebaseUser = Depends(get_current_user),
) -> ParsedDocumentResponse:
    """Upload and parse a document."""
    # 1. Upload to GCS
    storage_path = f"documents/{user.uid}/{skill_id}/{file.filename}"
    url = await upload_to_gcs(file, storage_path)
    
    # 2. Parse with ailang-parse
    result = client.parse_url(url, output_format="blocks")
    
    # 3. Generate A2UI representation
    a2ui_result = client.parse_url(url, output_format="a2ui")
    
    # 4. Store in Firestore
    doc = await store_parsed_document(
        user_id=user.uid,
        skill_id=skill_id,
        source_url=url,
        storage_path=storage_path,
        original_filename=file.filename,
        blocks=result.blocks,
        metadata=result.metadata,
        summary=result.summary,
        a2ui=a2ui_result,
    )
    
    return ParsedDocumentResponse(doc=doc)

@router.get("/api/documents/{doc_id}")
async def get_document(
    doc_id: str,
    user: FirebaseUser = Depends(get_current_user),
) -> ParsedDocumentResponse:
    """Get a parsed document with A2UI representation."""
    doc = await get_parsed_document(doc_id, user.uid)
    
    # Lazy parse if status is "pending"
    if doc.status == "pending":
        doc = await parse_and_store(doc)
    
    return ParsedDocumentResponse(doc=doc)

@router.patch("/api/documents/{doc_id}/blocks/{block_index}")
async def edit_block(
    doc_id: str,
    block_index: int,
    body: EditBlockRequest,
    user: FirebaseUser = Depends(get_current_user),
) -> EditBlockResponse:
    """Edit a specific block in a parsed document."""
    await update_edited_block(
        doc_id=doc_id,
        user_id=user.uid,
        block_index=block_index,
        new_text=body.text,
    )
    return EditBlockResponse(status="ok")

@router.post("/api/documents/generate")
async def generate_document(
    body: GenerateDocumentRequest,
    user: FirebaseUser = Depends(get_current_user),
) -> GenerateDocumentResponse:
    """Generate a document from blocks or markdown content."""
    result = client.generate(
        content=body.content,
        format=body.format,
        title=body.title,
    )
    url = await upload_to_gcs(result.data, f"{body.title}.{body.format}")
    return GenerateDocumentResponse(url=url, format=body.format)
```

### 5. Frontend Components

#### Component Tree

```
frontend/src/
├── components/
│   ├── navigation/
│   │   ├── SkillsBar.tsx          # Horizontal skill tabs
│   │   ├── SkillTab.tsx           # Individual skill tab with badge
│   │   └── CreateSkillButton.tsx  # + button → wizard
│   ├── document/
│   │   ├── DocumentPanel.tsx      # Main document panel wrapper
│   │   ├── DocumentViewer.tsx     # A2UI renderer for parsed doc
│   │   ├── DocumentHeader.tsx     # Metadata bar + source link
│   │   ├── DocumentFooter.tsx     # Block summary stats
│   │   ├── DocumentUpload.tsx     # Drag-drop upload zone (port from v5)
│   │   └── DocumentList.tsx       # Multi-document tab bar
│   ├── workspace/
│   │   ├── Workspace.tsx          # Split-pane layout
│   │   └── ResizeHandle.tsx       # Drag handle between panes
│   └── chat/
│       ├── ChatPanel.tsx          # AG-UI chat (standard)
│       └── DocumentContext.tsx    # Shows active doc reference in chat
├── hooks/
│   ├── useDocuments.ts            # CRUD for parsed_documents
│   ├── useDocumentParse.ts        # Client-side parsing with @ailang/parse
│   └── useSkillsBar.ts           # Skills fetch + selection state
├── themes/
│   └── aitana-a2ui.ts            # A2UI theme with Aitana colors
└── providers/
    └── DocumentProvider.tsx       # Active document context
```

#### Aitana A2UI Theme

Custom theme mapping Aitana's brand to A2UI components:

```typescript
// frontend/src/themes/aitana-a2ui.ts

import type { Theme } from '@a2ui/react'

export const aitanaTheme: Theme = {
  colors: {
    primary: 'hsl(20, 100%, 70%)',      // Aitana orange highlight
    secondary: 'hsl(200, 65%, 45%)',     // Teal
    surface: 'hsl(0, 0%, 100%)',         // White
    onSurface: 'hsl(222.2, 84%, 4.9%)', // Near-black text
    error: 'hsl(0, 84.2%, 60.2%)',       // Red
    outline: 'hsl(214.3, 31.8%, 91.4%)', // Light gray border
  },
  typography: {
    fontFamily: '"Euclid Circular A", system-ui, sans-serif',
    serifFamily: '"Crimson Pro", Georgia, serif',
  },
  shape: {
    borderRadius: '0.5rem',
  },
}
```

#### Client-Side Parsing Hook

```typescript
// frontend/src/hooks/useDocumentParse.ts

import { DocParse } from '@ailang/parse'

const client = new DocParse({ 
  baseUrl: '/api/proxy/api/documents/parse-proxy',  // Route through backend
})

export function useDocumentParse() {
  const [preview, setPreview] = useState<ParseResult | null>(null)
  const [isParsing, setIsParsing] = useState(false)

  const parseForPreview = useCallback(async (file: File) => {
    setIsParsing(true)
    try {
      // Client-side parse for instant preview (deterministic formats only)
      const result = await client.parseFile(file, 'a2ui')
      setPreview(result)
    } catch {
      // Fall back to showing upload-in-progress state
      // Backend will provide authoritative parse
    } finally {
      setIsParsing(false)
    }
  }, [])

  return { preview, isParsing, parseForPreview }
}
```

### 6. Protocol Integration

#### AG-UI Event Flow for Documents

Documents integrate into the existing AG-UI streaming protocol:

```
[User uploads document]
    → POST /api/documents/upload (REST, not AG-UI)
    → Frontend receives ParsedDocumentResponse
    → Renders A2UI in document panel

[User sends chat message with active document]
    → AG-UI stream: POST /api/skill/{skillId}/stream
    → Backend builds document context (markdown) + user message
    → ADK agent processes with document in context
    → AG-UI events stream back:
        TEXT_MESSAGE_CONTENT → chat text
        TEXT_MESSAGE_CONTENT (```a2ui) → inline A2UI in chat
        TOOL_CALL_START/END → tool activity indicators
        STATE_SNAPSHOT → document state if modified

[User edits document block]
    → PATCH /api/documents/{docId}/blocks/{idx} (REST)
    → Firestore updated
    → Next chat message picks up edited blocks in context
```

#### A2UI Bidirectional Flow

A2UI supports two-way communication via `onAction` callbacks:

```
User edits text field in A2UI document viewer
    → A2UIViewer fires onAction({ actionName: "field_changed", ... })
    → DocumentViewer.handleAction() 
    → Optimistic UI update (local state)
    → PATCH /api/documents/{docId}/blocks/{idx}
    → Firestore records edit in editedBlocks overlay
    → Next agent invocation sees edited content
```

This is native A2UI behavior — `@a2ui/react`'s `useA2UIComponent().setValue()` and `sendAction()` handle the client side. No custom protocol needed.

#### Multi-Channel Document Rendering

Same parsed blocks, different rendering per channel:

| Channel | Document Rendering |
|---------|-------------------|
| **Web** | Full A2UI interactive rendering (document panel) |
| **Telegram** | Markdown summary (headings + first paragraph per section) |
| **Email** | HTML rendering of blocks (via ailang-parse `output_format="html"`) |
| **WhatsApp** | Plain text summary with download link |
| **CLI** | Markdown to terminal |

### 7. Visual Design Direction

#### Brand Continuity from v5

Carry forward v5's established visual identity:

- **Logo**: `animated-aitana.svg` from `/public/images/logo/` — used in skills bar left corner
- **Primary color**: Orange highlight `hsl(20, 100%, 70%)` — active skill tab, document borders, action buttons
- **Secondary color**: Teal `hsl(200, 65%, 45%)` — chat messages, secondary actions
- **Fonts**: Euclid Circular A (UI), Crimson Pro (document content headings for contrast)
- **Neutrals**: White background, sand `hsl(35, 40%, 92%)` for document panel background
- **Shadows**: `shadow-sm` default, `shadow-lg` for document panel elevation

#### Component Styling

- **Skills bar**: `h-12`, white background, `border-b`, tabs use orange underline for active state
- **Document panel**: Sand background, white card for document content, subtle `shadow-sm`
- **Chat panel**: White background (matches v5 chat styling)
- **Document blocks**: `rounded-lg` cards for tables/changes/sections, inline text for headings/paragraphs
- **Edit mode**: Orange dashed border on editable fields, "Editing" badge in header

## ailang-parse Roadmap Requirements

Features needed from ailang-parse to fully realize this design:

| Feature | Priority | Current State | Needed |
|---------|----------|--------------|--------|
| **Editable A2UI mode** | P0 | `a2ui_formatter` outputs read-only `Text` nodes | Flag to emit `TextField` components with `data` bindings for editable content |
| **Block-level IDs** | P0 | Blocks identified by array index only | Stable `blockId` field on each block for edit targeting and partial re-renders |
| **Generation from blocks** | P1 | `generate()` accepts prompts/markdown | Accept Block ADT array as input for round-trip: parse → edit → regenerate |
| **Diff/patch support** | P1 | No edit tracking at parse level | `apply_edits(original_blocks, edits) → updated_blocks` utility |
| **Streaming parse for large docs** | P2 | Synchronous full-document response | Progressive block delivery for 50+ page documents |
| **JS SDK A2UI output** | P1 | JS SDK supports `"blocks"` output | Add `output_format="a2ui"` support to `@ailang/parse` JS client |

These are all additive to the existing ailang-parse API — no breaking changes needed.

## Parse/Render Contract

This section locks the data contract between `ailang-parse`, the v6 backend, and the A2UI frontend renderer. The canonical Python types live in [`backend/db/models/document.py`](../../../../backend/db/models/document.py); the generated JSON schema sits at [`contracts/document.schema.json`](contracts/document.schema.json), companion to `skill.schema.json`.

### ailang-parse → Pydantic

- `DocParse.parse_url(url, output_format="blocks")` returns `ParseResult` whose `blocks: list[ailang_parse.Block]` (a dataclass with union-shaped fields per block type) serializes to a dict list that **maps 1:1 onto `ParsedDocument.blocks: list[Block]`**. Our `Block` model pins the discriminator (`type`) and the common `text` field, and bags the rest into `properties` (`extra="allow"`), so the ailang-parse payload round-trips losslessly.
- `DocParse.parse_url(url, output_format="a2ui")` returns the A2UI component tree in the shape `{root: str, components: list[dict]}`. These map directly onto `ParsedDocument.a2uiRoot` (string node id) and `ParsedDocument.a2uiComponents` (pre-computed component list). **No adapter layer** — the values are stored in Firestore as-is.
- `metadata` and `summary` on the `ParseResult` map directly onto `ParsedDocument.metadata` (`DocMetadata`) and `ParsedDocument.summary` (`DocSummary`).

### Pydantic → Firestore

- All field names are camelCase at the wire/storage layer via Pydantic aliases; Python uses snake_case with `populate_by_name=True`.
- `status` is locked to `Literal["pending", "parsing", "parsed", "failed", "edited"]` — enforced by the Pydantic model and reflected in `firestore.rules`.
- `editedBlocks` is a sparse `dict[str, EditedBlock]` keyed by stringified block index (Firestore does not support integer keys in maps).

### Pydantic → A2UI frontend

- `@a2ui/react`'s `<A2UIViewer>` consumes `{root, components}` directly. The backend response ships `a2uiRoot` + `a2uiComponents` and the frontend passes them through unchanged — **no transform on the client side**.

### Versioning

- `ailang-parse>=0.9.3` is required for the native `output_format="a2ui"` formatter. v6.0.0 currently pins `>=0.5.1` (no a2ui formatter yet) — Phase 1B must bump this when the formatter ships. Until then, the `a2uiRoot`/`a2uiComponents` fields are optional (`None`) and the frontend falls back to client-side rendering of `blocks`.
- `@a2ui/react>=0.9.0-alpha.0` on the frontend for the viewer component.

### Canonical references

- Python types: [`backend/db/models/document.py`](../../../../backend/db/models/document.py)
- JSON schema: [`contracts/document.schema.json`](contracts/document.schema.json)
- Firestore schema doc: this file, lines 291–336

## Implementation Plan

### Phase 1: Foundation (~2 days)

- [ ] Add `@ailang/parse` to `frontend/package.json`
- [ ] Bump `@a2ui/react` to latest (`^0.9.0-alpha.0`)
- [ ] Create `parsed_documents/` Firestore collection schema
- [ ] Implement `backend/tools/document_parse.py` as ADK FunctionTool
- [ ] Implement `backend/tools/document_generate.py` as ADK FunctionTool
- [ ] Create Aitana A2UI theme (`frontend/src/themes/aitana-a2ui.ts`)
- [ ] Build `DocumentViewer` component using `<A2UIViewer>` with test data

### Phase 2: Skills Bar (~1 day)

- [ ] Build `SkillsBar` component with tab layout
- [ ] Wire `useSkillsBar` hook to `GET /api/skills` endpoint
- [ ] Implement skill switching (updates `SkillContext.activeSkillId`)
- [ ] Add `CreateSkillButton` linking to skill creation wizard
- [ ] Style with v5 brand (orange active tab, Aitana logo)

### Phase 3: Document Upload + Parse (~2 days)

- [ ] Implement `POST /api/documents/upload` endpoint
- [ ] Implement `GET /api/documents/{docId}` with lazy parsing
- [ ] Implement `PATCH /api/documents/{docId}/blocks/{blockIndex}`
- [ ] Build `DocumentUpload` component (port v5 drag-drop, add parse flow)
- [ ] Build `useDocumentParse` hook for client-side preview
- [ ] Build `DocumentPanel` with header, viewer, footer

### Phase 4: Workspace Integration (~1.5 days)

- [ ] Build `Workspace` split-pane layout with resize handle
- [ ] Wire document context into AG-UI chat (backend `build_document_context`)
- [ ] Implement `DocumentProvider` context for active document state
- [ ] Connect skill switching to document context (preserve doc, new session)
- [ ] Add document reference indicator in chat panel

### Phase 5: Document Generation (~1 day)

- [ ] Implement `POST /api/documents/generate` endpoint
- [ ] Build download UI with format selector (DOCX, PDF, PPTX, etc.)
- [ ] Add A2UI preview of generated document in chat
- [ ] Wire `generate_document_tool` into relevant skills (Document Analyst, Data Extractor)

## Migration & Rollout

**From v5:**
- v5 `initialDocuments` (file links) continue to work — displayed as download links in document panel
- Lazy parsing: first time user views a v5 document in v6, it's parsed and cached
- No data migration needed — `parsed_documents/` is a new collection alongside v5 data

**Feature flag:**
- `ENABLE_DOCUMENT_UI=true` — enables document panel and parsing
- `ENABLE_DOCUMENT_UI=false` — falls back to v5-style file links in sidebar

**Rollout:**
1. Internal dogfood with document-heavy skills (Document Analyst, Data Extractor)
2. Enable for all users once ailang-parse editable A2UI mode ships
3. Document generation available immediately (no ailang-parse changes needed)

## Testing Strategy

### Frontend Tests (Vitest)

- [ ] `DocumentViewer` renders A2UI components from test fixture
- [ ] `SkillsBar` renders skills, handles tab selection
- [ ] `Workspace` split-pane resizes correctly
- [ ] `useDocumentParse` handles parse success and failure
- [ ] `DocumentUpload` validates file size and type

### Backend Tests (pytest)

- [ ] `parse_document` tool returns correct block structure for DOCX, PDF, XLSX
- [ ] `generate_document` tool produces valid file for each format
- [ ] `upload_document` endpoint stores and parses correctly
- [ ] `edit_block` endpoint updates Firestore overlay correctly
- [ ] `build_document_context` includes edits in markdown output
- [ ] Access control: users can only access their own documents

### Integration Tests

- [ ] Upload DOCX → parse → render A2UI → edit block → chat references edited content
- [ ] Upload PDF (AI parse) → renders with skeleton → completes when parse finishes
- [ ] Skill switch preserves document, starts new session
- [ ] Generate document from chat → download link works

### ADK Eval

- [ ] Document Analyst skill correctly extracts tables from test DOCX
- [ ] Data Extractor skill identifies all tracked changes in test document
- [ ] Agent citations reference correct document sections (EARNED TRUST)

## Security Considerations

- **Document access scoping**: `parsed_documents/` documents scoped by `userId` + `skillId`. Backend enforces ownership check on all read/write operations. Firestore security rules as defense-in-depth.
- **A2UI safety**: A2UI is pure JSON data — no executable code, no script injection. `<A2UIViewer>` renders from a component registry, not from arbitrary HTML.
- **GCS signed URLs**: Original file links use time-limited signed URLs (1 hour). No permanent public URLs for uploaded documents.
- **Client-side parsing**: `@ailang/parse` JS SDK sends file data to the parse API — no files processed purely client-side for security-sensitive content. The backend parse is authoritative.
- **Edit tracking**: All edits recorded with `editedBy` userId and timestamp for audit trail.
- **TODO (multi-user sharing)**: `ParsedDocument` currently has no `AccessControl` block — documents are scoped per `userId` only. When document sharing lands (v6.1+ workspace), add `access_control: AccessControl` + `owner_id` to `ParsedDocument` so it satisfies the `_HasAccess` protocol. At that point `GET /api/documents/{doc_id}/sessions` must add an explicit `ctx.can_access(doc)` gate; today it relies on session-level filtering as the implicit deny.

## Performance Considerations

- **Deterministic parse**: DOCX/PPTX/XLSX/HTML/CSV via ailang-parse: ~11ms (server-side). Client-side JS SDK preview may be slower (~50-100ms) but provides instant feedback.
- **AI parse**: PDF/images: 1-5s depending on document size and model. Skeleton state shown during parse.
- **A2UI render**: `<A2UIViewer>` renders from JSON: <50ms for typical documents (<100 components).
- **Large documents**: Documents >500 blocks paginated in Firestore subcollection. Frontend virtualizes scroll. A2UI components loaded per-page.
- **Firestore reads**: Single document read: ~50ms. Subcollection page read: ~50ms per page.
- **Client-side preview**: Prevents upload → wait → see pattern. User sees structure immediately while backend processes authoritatively.

## Success Criteria

- [ ] User can upload a DOCX and see its full structure (headings, tables, changes) rendered in the document panel within 2 seconds
- [ ] User can switch between skills in the top bar and the chat context changes while the document stays
- [ ] User can edit text in the document panel and the next chat message reflects the edit
- [ ] User can ask the AI to generate a new document from the conversation and download it
- [ ] Source link to original file is always visible and clickable
- [ ] Falls back to file links gracefully when parsing fails
- [ ] All document data scoped per-user — no cross-user document access

## Open Questions

1. Should the skills bar show **all** user skills or only skills tagged as document-compatible? (Recommendation: show all, but highlight document-compatible ones)
2. Should client-side JS parsing be enabled for all formats or only deterministic ones? (Recommendation: deterministic only — AI formats should go through backend for cost tracking)
3. Should edited blocks be stored as a sparse overlay (current design) or should we deep-copy the full blocks array on first edit? (Recommendation: sparse overlay — more efficient for small edits)
4. Should we support multiple simultaneous documents per skill session? (Recommendation: start with single active document, add multi-doc tabs in v6.1.0)
5. How should the skills bar interact with the existing sidebar (if any)? (Recommendation: skills bar replaces the v5 sidebar navigation — skills are the top-level concept)

## Related Documents

- [Frontend Architecture](frontend-architecture.md) — Overall frontend structure, providers, routing
- [Skills Data Model](skills-data-model.md) — Skill schema, CRUD API, marketplace
- [Streaming & Protocols](../streaming-and-protocols.md) — AG-UI events, A2UI rendering, MCP Apps
- [Tools Porting Guide](../tools-porting-guide.md) — ADK FunctionTool pattern for parse/generate tools
- [Cloud Infrastructure](cloud-infrastructure.md) — GCS buckets, Firestore collections
- [Auth & Permissions](auth-and-permissions.md) — Document access control, JWT verification

---

## Implementation Report

**Completed**: 2026-04-21
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
