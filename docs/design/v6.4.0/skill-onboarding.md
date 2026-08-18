# Skill Onboarding — Welcome screens, example documents, AI greeting, bucket browser

**Status**: Planned
**Priority**: P1
**Estimated**: ~3 days (1 sprint, single dev; runs in parallel with 4.2 M4 CLI work)
**Scope**: Fullstack — backend `SkillConfig.welcome` schema + frontend SkillExamplesPicker + AI-greeting rendering + GCSFileBrowser port
**Dependencies**:
- 4.3 INTERNAL-SHELL (Sidebar/Workbench primitives) ✅ — the WorkbenchPane Workspace-tab EmptyTab is the hook point for the picker
- v6.2.0 2.9 multi-surface-rendering ✅
- v6.2.0 2.10 a2ui-surface-context ✅
- v6.3.0 3.2 client-tenant-management ✅ — gives `clients/{domain}.documents_bucket` binding
- ACTION-TRIGGER M1–M3 (shipped 2026-06-08) ✅
- Existing `SkillConfig.initialMessage` field ✅
- Existing artefact/RAG plumbing ✅
**Created**: 2026-06-09
**Last Updated**: 2026-06-09

## Problem Statement

Mark surfaced this cluster during local UX iteration on 2026-06-09 (post-4.3 INTERNAL-SHELL ship). A new visitor opens a skill (e.g. Contract Expert) and gets:

- **No introduction.** Skill frontmatter has `initialMessage: "PPA, PtX, BESS — what would you like to analyse?"` but it's never rendered. User stares at an empty chat with a "Message…" placeholder and no signal of what the skill is for.
- **Empty workspace.** Workbench Workspace tab shows the EmptyTab fallback ("The assistant's structured outputs … appear here as it works"). True but unhelpful before the user has done anything.
- **No way to demo without uploading.** User drops a random doc (a "VOLUNTEERS" doc, an insurance claim summary) and the PPA assistant correctly identifies them as non-PPAs — but the user has no obvious path to "try this skill with a pre-loaded PPA". Mark literally saw this happen.
- **No curated content library.** ONE has hundreds of indexed PPAs at `gs://multivac-acme-energy-bucket/PPAs/`. The sidebar only shows the current user's uploads (`My Documents`). No way to browse the curated library.
- **No interactive workbench surfaces.** ACTION-TRIGGER + A2UI surface-action protocols are shipped, but no Aitana skill declares interactive widgets. User sees the workspace fill with cards/tables but can't click anything to drive the next turn.

The throughline: **how does a new user get from "opens this skill" to "in a useful state" without uploading random files or staring at an empty workbench?** Pattern Mark wants: ONE PPA skill opens → AI greets (intro_message bubble) → splash in the Workspace tab offers *"Try with Example PPA A vs B"* + *"Or pick from the Contracts library"* + *"Or upload your own"* → user picks an example → workspace populates → user clicks a diff → agent explains.

**Current State** (verified 2026-06-09):
- `frontend/src/types/skill.ts:57` declares `initialMessage: string` and it round-trips through the marketplace API, but `app/chat/[...path]/page.tsx` never renders it as a synthetic first assistant bubble.
- `frontend/src/components/doc-browser/` has `DocListView`, `UploadDropZone`, `DocTabsBar`, doc-tab primitives — **no `GCSFileBrowser`**. gde-ap-agent has one (~170 LOC at `components/doc-browser/GCSFileBrowser.tsx`).
- gde-ap-agent has `SampleInvoicePicker` (`components/chat/SampleInvoicePicker.tsx`) — fresh-chat skill-home empty state with pre-loaded sample invoices. Pattern is generic; current implementation is AP-pipeline-specific.
- `SkillConfig` has no `welcome` field. ACTION-TRIGGER protocol works but the skill prompt body has no documented convention for "expect surface-action events from this widget".

**Impact:**
- **First-impression UX** — the first 15 seconds in a skill decide whether the user keeps trying. Friday demo will be Mark walking a stakeholder through Contract Expert; an empty chat + Workspace tab is a credibility problem
- **Demo without preparation** — Aitana can't easily seed a fresh deployment with "click here for example PPAs"; every demo today requires uploading docs first
- **Downstream-fork inheritance** — every fork (`tutoring-fork`, `internal-tools-fork`, future doc-comparison apps) hits the same onboarding gap. Solving it in the platform compounds
- **Demo of the ACTION-TRIGGER protocol surface** — without one skill exercising interactive workbench widgets, the protocol pitch ("agent reacts to clicks") stays abstract

## Goals

**Primary Goal:** Ship `SkillConfig.welcome` schema + AI-greeting rendering + `SkillExamplesPicker` + `GCSFileBrowser` port + a documented prompt convention for interactive workbench surfaces — so a new user opening Contract Expert sees the AI greet them, a Workspace tab offering Example PPAs they can click to import, a sidebar section browsing the Contracts library, and at least one skill (one-doc-compare) demonstrating click-to-react workbench widgets — by EOD Thursday 2026-06-11 so Friday demo runs against a polished onboarding flow.

**Success Metrics:**
- `SkillConfig.welcome` schema (Pydantic backend + TypeScript frontend) accepts `intro_message`, `example_documents: list[ExampleDocument]`, `bucket_browser: BucketBrowserConfig | None`; round-trips via `/api/skills/{id}`; older skills without `welcome` round-trip unchanged
- Fresh chat (sessionId === null AND messages.length === 0 AND !enteredViaResume) renders `welcome.intro_message` (fallback `initialMessage`) as a synthetic first assistant bubble with a small *"Assistant intro"* caption — clearly marked not persisted to session history
- WorkbenchPane Workspace tab renders `SkillExamplesPicker` in place of EmptyTab when chat is fresh AND `skill.welcome.example_documents` non-empty; clicking an example imports it via existing upload-by-reference plumbing
- Sidebar gets a 3rd `SidebarSection` (under Sessions + Documents) for "Contracts library" when `skill.welcome.bucket_browser` is set; mounted `GCSFileBrowser` lists the configured bucket+path; click → import to user's tabs
- one-ppa-expert + one-doc-compare SKILL.md frontmatter updated with `welcome.intro_message` + `welcome.example_documents` (5 example PPA contracts in `gs://aitana-examples-public/ppa/`) + `welcome.bucket_browser` pointing at `gs://multivac-acme-energy-bucket/PPAs/longform/`
- one-doc-compare SKILL.md prompt body extended with the documented ACTION-TRIGGER convention; a "Compare these clauses" interactive widget in the Workspace tab fires `surface-action` and the agent reacts
- Live URL `/chat/@aitana-platform/one-ppa-expert` post-deploy verified via curl/chrome-devtools: intro bubble present, picker visible with 5 examples, sidebar shows Contracts library section, no console errors

**Non-Goals:**
- **Full chat-page redesign** — 4.3 INTERNAL-SHELL covered the shell port; this doc only hooks into the WorkbenchPane Workspace tab EmptyTab and adds a sidebar section. No layout refactor.
- **Multi-shell support (DocCompareShell / WorkbenchShell)** — 4.4 skill-driven-shell-modes covers that. `welcome` and `shell` are sibling fields; they compose orthogonally but this doc only ships against the existing ChatShell.
- **New A2UI primitives, new MCP server, new protocol events** — zero new protocol surface; everything renders existing AG-UI/A2UI events.
- **Storing intro bubble in session history** — the intro bubble is presentation-only. Server-side history stays clean.
- **Cross-skill example-document sharing** — each skill declares its own `example_documents[]`. Shared examples = same bucket path in multiple skills.
- **Mobile-specific picker layout** — desktop-first. Mobile follows responsive primitives.
- **CDN-hosted thumbnails** — `welcome.example_documents[].thumbnail` is optional. v1 skills can omit; v6.5 designs richer artwork.
- **Backend write of new docs to a "user's bucket"** — when the user clicks an example, the platform mounts it by reference (existing doc-ref-import path), not by copying bytes.
- **Per-skill auth on the examples bucket** — public read-only bucket at `gs://aitana-examples-public/` (similar to existing `gs://aitana-public-bucket/` for fixtures). Tenant-specific examples live in tenant buckets with existing access control.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Fresh-chat affordances (greeting bubble + example picker) collapse the "what do I type" friction. User can click an example and see the workspace populate without typing or uploading. |
| 2 | EARNED TRUST | +1 | AI intro bubble tells the user up-front what the skill is for and how it's scoped ("PPA, PtX, BESS"). Small "Assistant intro" caption signals it's not stored. Examples are clearly labelled with their content so the user knows what they're clicking. |
| 3 | SKILLS, NOT FEATURES | +2 | Welcome content is per-skill config in `SKILL.md` frontmatter — not platform code. Adding example documents to a new skill is a Firestore record update, not a deploy. Downstream forks (legal, procurement) inherit the pattern automatically. Strongest hit. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing changes. |
| 5 | GRACEFUL DEGRADATION | +2 | Every welcome field optional and nullable. Missing `intro_message` → no greeting bubble. Empty `example_documents[]` → EmptyTab fallback continues. Missing `bucket_browser` → no extra sidebar section. Examples bucket unreachable → picker shows "Library unavailable" but skill still works for uploaded docs. Backwards compat: older skills round-trip unchanged. |
| 6 | PROTOCOL OVER CUSTOM | +1 | `GCSFileBrowser` uses existing artefact/RAG plumbing for doc-by-reference import — no new endpoint. AI greeting reuses existing AG-UI assistant-message rendering, just synthesised client-side. ACTION-TRIGGER convention is documented prompt-body boilerplate, not a new protocol. No new A2UI primitives. |
| 7 | API FIRST | 0 | One additive endpoint (`/api/buckets/{name}/list?prefix=…`) for the GCSFileBrowser; pure-read GCS proxy. Channels (Telegram, email, CLI) unaffected — they don't have a workbench surface to picker into. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Existing AG-UI / Cloud Trace coverage continues. Picker clicks emit a structured log line (`welcome.example_picked skill=… example=…`) so onboarding funnel is measurable. No new instrumentation surface. |
| 9 | SECURE BY CONSTRUCTION | +1 | Public examples bucket is read-only via signed-URL proxy from backend — frontend never gets bucket-list credentials. Tenant bucket access (`welcome.bucket_browser.bucket`) inherits existing SA grants from v6.3.0 client-tenant-management. No new privileges; the SA's existing `roles/storage.objectViewer` covers it. Examples bucket has CSP-friendly headers; PII-free (curated public corpus). |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Picker + GCSFileBrowser are presentation over the existing artefact-import path. No business logic moves to the client. Greeting bubble is pure client-side render (no server write). Bundle delta <20KB gzipped. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None — no axiom scores -1.

## Standards Compliance

- **Agent Skills spec** — `welcome` is additive frontmatter under existing SKILL.md format (no spec rule about which optional fields the runtime supports; standard practice is additive-with-fallback). No reinvention.
- **A2UI / AG-UI / MCP / A2A** — zero changes. The Workspace-tab Picker is plain React; the greeting bubble reuses existing AG-UI assistant-message rendering with a client-only synthetic message (clearly typed so MessageBubble can render the "Assistant intro" caption variant).
- **GCS access** — standard server-side proxy with the same SA pattern as v6.3.0 rag-document-corpus. No new auth flow.
- **No new protocols invented.**

## CLI Surface

Per design-doc-creator skill rule 5b-bis. This sprint has developer-facing surface; CLI commands ship in-sprint:

- `aiplatform examples list [--skill <slug>]` — lists what's currently declared in a skill's `welcome.example_documents[]`. ~0.1d.
- `aiplatform examples push --skill <slug> --bucket <name> --object <path> --label "..."` — appends an example document to a skill's frontmatter. Idempotent on (bucket, object). ~0.15d.
- `aiplatform bucket browse <name> [--prefix <path>]` — debug helper that lists objects under a prefix via the same backend proxy the GCSFileBrowser uses. Saves "is this bucket / prefix accessible from the SA" being a gcloud round-trip. ~0.15d.
- All three lean on existing httpx + Click patterns from v6.1.0 [local-dev-cli.md](../v6.1.0/implemented/local-dev-cli.md).

## Design

### Overview

Three additive surfaces and one documented convention, all hung off a single new `SkillConfig.welcome` block.

1. **`SkillConfig.welcome` schema** — Pydantic + TypeScript + Firestore-additive-nullable
2. **Fresh-chat AI greeting** — client-side synthetic assistant bubble in `ChatMessageList` when chat is fresh, sourced from `welcome.intro_message` (fallback `initialMessage`)
3. **`SkillExamplesPicker`** — Workspace-tab affordance when chat is fresh AND `welcome.example_documents` non-empty; replaces EmptyTab fallback
4. **`GCSFileBrowser` port** — sidebar section under Sessions + Documents when `welcome.bucket_browser` is set; reads via backend GCS proxy
5. **Interactive workbench convention** — documented `SKILL.md` prompt-body boilerplate that wires ACTION-TRIGGER `surface-action` reactivity; one-doc-compare ships an example

### Skill Config Schema

```yaml
# SKILL.md frontmatter — new optional section (compose orthogonally with 4.4 shell)
welcome:
  # First assistant bubble when chat is fresh. Falls back to skill.initialMessage.
  # Null/empty = no greeting.
  intro_message: "PPA, PtX, BESS — what would you like to analyse?"

  # Pre-loaded example documents the user can click to mount into the chat
  # without uploading. The frontend mounts SkillExamplesPicker in the
  # WorkbenchPane Workspace tab when chat is fresh AND this list is non-empty.
  example_documents:
    - bucket: aitana-examples-public
      object: ppa/example-A-fixed-pap.pdf
      label: "Example PPA — Fixed price, PaP settlement"
      thumbnail: /images/examples/ppa-fixed-pap.jpg   # optional
      summary: "10-year fixed-price PPA, Pay-as-Produced, German offtaker"   # optional one-liner
    - bucket: aitana-examples-public
      object: ppa/example-B-cpi-pan.pdf
      label: "Example PPA — CPI-indexed, PaN settlement"

  # Sidebar bucket browser. Mounts a GCSFileBrowser in the sidebar (3rd
  # SidebarSection under Sessions + Documents) when this block is set.
  bucket_browser:
    bucket: multivac-acme-energy-bucket
    root_path: PPAs/longform/
    label: "Contracts library"
    default_open: false   # collapsed by default; user expands if they need it
```

```python
# backend/skills/skill_config.py — extend SkillConfig
class ExampleDocument(BaseModel):
    bucket: str
    object: str
    label: str
    thumbnail: str | None = None
    summary: str | None = None

class BucketBrowserConfig(BaseModel):
    bucket: str
    root_path: str = ""
    label: str = ""
    default_open: bool = False

class WelcomeConfig(BaseModel):
    intro_message: str | None = None
    example_documents: list[ExampleDocument] = []
    bucket_browser: BucketBrowserConfig | None = None

class SkillConfig(BaseModel):
    # ... existing fields ...
    welcome: WelcomeConfig | None = None   # None = no onboarding affordances
```

**Backwards compatibility:** every field nullable / defaulted. Skills without `welcome` round-trip unchanged. Frontend reads `skill.welcome?.intro_message ?? skill.initialMessage ?? null` so legacy skills still get a greeting if they set `initialMessage`.

**Sibling alignment with 4.4** — `SkillConfig.shell` (from 4.4 skill-driven-shell-modes) and `SkillConfig.welcome` compose orthogonally: a `doc-compare` shell shows the picker inside the DocCompareShell's two-pane layout; a `chat-primary` shell (current) shows it in the WorkbenchPane Workspace tab. The doc decisions don't conflict.

### Examples Bucket Policy — LOCKED

Two-tier:

1. **Cross-deploy public bucket** — `gs://aitana-examples-public/` (publicly read-only). For canonical example PPAs anyone forking the platform-template can reference. Curated by Aitana team; PII-free. v1: 5 example PPA contracts under `ppa/` covering the demo narrative (fixed vs CPI, PaP vs PaN, RFNBO-eligible, etc.).
2. **Per-deploy tenant bucket** — `welcome.example_documents[].bucket` can point at any tenant bucket the SA has read access to. ONE skills (one-ppa-expert, one-doc-compare) reference `multivac-acme-energy-bucket` directly for the curated library `welcome.bucket_browser` path.

Skills mix both freely: 2 examples from public bucket (for "what is a PPA?" curiosity) + 3 from ONE's curated library (for "review my actual contracts" workflow).

**IAM:** the platform's existing `sa-platform@your-project-id.iam.gserviceaccount.com` already has cross-project read on ONE's bucket (granted 2026-06-08 during ONE-DEMO M2). Public bucket gets `roles/storage.objectViewer` on `allUsers` (one-time setup).

### AI-Greeting Render Mode — LOCKED

**Client-only synthetic message.** ChatShell renders a synthetic `Message` object with `role: "assistant"` and `content: welcome.intro_message ?? initialMessage` ONLY when:
- `messages.length === 0`
- `sessionId === null`
- `!enteredViaResume`
- the source string is non-empty

The synthetic message has `__synthetic: true` flag so:
- `MessageBubble` renders it with a small *"Assistant intro — not stored"* caption
- It does NOT get serialised into the AG-UI stream (no server-side write)
- The moment the user sends their first message, the live `messages` array starts and the synthetic bubble naturally falls off (no list mutation needed)

This is presentation-only; pure client side. No backend change.

### `SkillExamplesPicker` Component

```
WorkbenchPane Workspace tab content:
   if (workspaceSurface?.surface)         → render A2UISurfaceMount (current)
   else if (isFreshChat && welcome.example_documents.length > 0)
                                           → render SkillExamplesPicker
   else                                    → render EmptyTab (current fallback)
```

`SkillExamplesPicker` is a card grid (3-col on md, 2-col on sm) of `ExampleDocument` entries. Each card: thumbnail (or generic doc icon) + label + summary. Click → calls existing doc-import-by-reference path (POST `/api/documents/import-from-bucket?bucket=…&object=…`) which the platform already uses for v6.3.0 rag-document-corpus. On success: tab opens in `openTabs`, `setActiveTabId(newId)`, `setWorkbenchTabId("document")`.

Below the grid: "Or upload your own" link → existing upload flow.

### `GCSFileBrowser` Port

Lift `~170 LOC` from `gde-ap-agent/frontend/src/components/doc-browser/GCSFileBrowser.tsx`. Generic component; ports verbatim. Wired into the sidebar via a 3rd `SidebarSection`:

```tsx
{showDocBrowser && (
  <aside>
    <SidebarSection title="Sessions">...</SidebarSection>
    <SidebarSection title="Documents">...</SidebarSection>
    {skill.welcome?.bucket_browser && (
      <SidebarSection
        title={skill.welcome.bucket_browser.label || "Library"}
        defaultOpen={skill.welcome.bucket_browser.default_open}
      >
        <GCSFileBrowser
          bucket={skill.welcome.bucket_browser.bucket}
          rootPath={skill.welcome.bucket_browser.root_path}
          onPick={handleBucketPick}   // imports by reference, same as picker
        />
      </SidebarSection>
    )}
    <SidebarSurfaceRegion ... />
  </aside>
)}
```

`onPick` reuses the same import-by-reference path as the SkillExamplesPicker. No duplication.

### Backend: GCS-list proxy

```
GET /api/buckets/{name}/list?prefix=<path>&limit=200
   → { entries: [{name, size, content_type, updated}, ...], next_token: "..." | null }
```

Backend-only; pure GCS list-objects call via SA. Returns object metadata only (no bytes). Frontend renders folder tree + file list. Identical pattern to the v6.3.0 rag-document-corpus list path.

Document download still uses the existing artefact-render-hook proxy — no new download path.

### Interactive Workbench Convention — Boilerplate

When a skill emits A2UI surfaces with interactive widgets (buttons, clickable rows, form inputs), the SKILL.md prompt body MUST include this boilerplate so the model knows to react:

```
# Interactive workbench

When you emit a Card/Table/Button to the workspace surface, the user
may click an element. You will receive a `surface-action` event on
the next turn with structure:
  { surface_id: "workspace", action: "<your-action-name>",
    payload: { ... } }

Treat these events as user intents. Acknowledge briefly, then take
the next action (e.g. "I'll dive into clause X" → fire the
extract_clause tool with the payload's clause_id).

Do NOT echo the raw event JSON to the user. Surface-action events
are private signals from the UI; respond in natural language.
```

one-doc-compare gets a worked example: a "Compare these clauses" button on each KeyDifferencesPanel row → `surface-action: "explain_diff"` with `payload: {diff_id, left_block_id, right_block_id}` → agent explains in chat with both `block_id` citations.

### Frontend Changes

**New components:**
- `frontend/src/components/chat/SkillExamplesPicker.tsx` (~150 LOC + 5 vitest)
- `frontend/src/components/doc-browser/GCSFileBrowser.tsx` (~170 LOC + 4 vitest, port from gde-ap-agent)
- `frontend/src/components/chat/AssistantIntroBubble.tsx` (~40 LOC + 2 vitest) — extracts the "Assistant intro — not stored" presentation so ChatMessageList stays focused on streaming messages

**Modified components:**
- `frontend/src/app/chat/[...path]/page.tsx` — synthesise intro message when fresh; pass `welcome.example_documents` to WorkbenchPane; add 3rd SidebarSection when `welcome.bucket_browser` set (~40 LOC change + 3 vitest)
- `frontend/src/components/chat/ChatMessageList.tsx` — render `__synthetic` assistant message as `AssistantIntroBubble` (~10 LOC + 1 vitest)
- `frontend/src/types/skill.ts` — add `WelcomeConfig` + `ExampleDocument` + `BucketBrowserConfig` TypeScript types matching backend Pydantic (~30 LOC)

### Backend Changes

- `backend/skills/skill_config.py` — extend `SkillConfig` with `welcome: WelcomeConfig | None` (~50 LOC + 4 pytest)
- `backend/buckets/routes.py` (new) — `GET /api/buckets/{name}/list?prefix=…` proxy (~60 LOC + 4 pytest covering happy path, missing-bucket 404, access-denied 403, pagination)
- `backend/skills/templates/one-ppa-expert/SKILL.md` — extend frontmatter with `welcome` block referencing 5 example PPAs + Contracts library bucket
- `backend/skills/templates/one-doc-compare/SKILL.md` — extend frontmatter with `welcome` block + add interactive workbench convention to prompt body + extend `compare_ppa_contracts` output to include a `_a2ui_actions` array so the workbench renders a "Compare these clauses" button per diff row

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET    | /api/buckets/{name}/list | New — proxy GCS list-objects; SA does the auth, frontend never sees creds | No (new) |
| GET    | /api/skills/{id} | Response gains optional `welcome` block | No (additive nullable) |

### Architecture Diagram

```
SKILL.md frontmatter (welcome:)
   │
   └─► SkillConfig.welcome  (backend Pydantic)
         │
         ├── /api/skills/{id}          → SkillSummary.welcome  (TS type)
         │
         └─► ChatShell render:
                 │
                 ├── isFreshChat?
                 │      │
                 │      ├── synthesise AssistantIntroBubble  ← welcome.intro_message
                 │      │     (client-only, __synthetic: true)
                 │      │
                 │      └── WorkbenchPane Workspace tab:
                 │             │
                 │             ├── if A2UI surface  → A2UISurfaceMount (existing)
                 │             ├── if examples set  → SkillExamplesPicker
                 │             └── else              → EmptyTab fallback
                 │
                 └── Sidebar:
                       ├── SidebarSection "Sessions"   (existing)
                       ├── SidebarSection "Documents"  (existing)
                       └── if welcome.bucket_browser
                              → SidebarSection "<label>"
                                  └── <GCSFileBrowser>
                                        ↓ click
                                        POST /api/documents/import-from-bucket
                                        (existing artefact-import path)

Skill emits A2UI Card with action button
   ↓ user click
   POST /api/sessions/{id}/surface-action  (existing v6.2.0 2.10)
   ↓ next turn
   Agent receives surface-action event; reacts per documented convention
```

## Implementation Plan

### M1 — `SkillConfig.welcome` schema + one-ppa-expert + one-doc-compare frontmatter (~1d)
- [ ] Extend `SkillConfig` Pydantic with `WelcomeConfig` + nested models (~50 LOC + 4 pytest)
- [ ] Update `/api/skills/{id}` response serialization to include `welcome` (~5 LOC + 1 pytest)
- [ ] Frontend `WelcomeConfig` + `ExampleDocument` + `BucketBrowserConfig` TS types (~30 LOC)
- [ ] Update `backend/skills/templates/one-ppa-expert/SKILL.md` frontmatter — `welcome.intro_message`, 5 example PPAs (bucket+object+label), `welcome.bucket_browser` pointing at Contracts library
- [ ] Update `backend/skills/templates/one-doc-compare/SKILL.md` frontmatter — same welcome block; ALSO add interactive-workbench convention to prompt body + extend `compare_ppa_contracts` to emit per-diff action buttons
- [ ] Upload 5 example PPA contracts to `gs://aitana-examples-public/ppa/` (PII-free public templates: Linklaters EFET reference, AIB EECS reference, RE100 corporate PPA samples — same set used in 4.2 M2 corpus)
- [ ] `aiplatform skill push` validates the new frontmatter section (Pydantic validation already covers it; CLI just prints confirmation)

### M2 — `SkillExamplesPicker` (~0.5d)
- [ ] `SkillExamplesPicker.tsx` — card grid mounting `ExampleDocument[]` (~150 LOC + 5 vitest covering 0/1/N examples, click handler, thumbnail-or-fallback rendering)
- [ ] Wire into `WorkbenchPane` Workspace tab content: replace EmptyTab when fresh + examples non-empty (~15 LOC change + 2 vitest)
- [ ] Use existing `/api/documents/import-from-bucket` path; on success setActiveTabId + setWorkbenchTabId("document")

### M3 — AI-greeting wiring (~0.25d)
- [ ] `AssistantIntroBubble.tsx` — small variant of MessageBubble with "Assistant intro — not stored" caption (~40 LOC + 2 vitest)
- [ ] ChatShell: synthesise the bubble when fresh + intro source non-null (~15 LOC + 2 vitest)
- [ ] ChatMessageList renders `__synthetic` messages via AssistantIntroBubble (~10 LOC + 1 vitest)

### M4 — `GCSFileBrowser` port + bucket-list proxy (~1d)
- [ ] Backend `GET /api/buckets/{name}/list` route (~60 LOC + 4 pytest)
- [ ] Frontend `GCSFileBrowser.tsx` port from gde-ap-agent (~170 LOC + 4 vitest — folder tree, list, click handler)
- [ ] Wire into sidebar as 3rd `SidebarSection` when `welcome.bucket_browser` set; reuse `handleBucketPick` import-by-reference handler from M2 (~20 LOC chat-page change + 2 vitest)
- [ ] One-time IAM: confirm SA has `roles/storage.objectViewer` on `multivac-acme-energy-bucket` (already granted 2026-06-08) + set `allUsers:roles/storage.objectViewer` on `aitana-examples-public`

### M5 — Interactive workbench convention + one-doc-compare example (~0.5d)
- [ ] Document the convention in `docs/design/v6.4.0/implemented/skill-onboarding.md` post-sprint + in `backend/skills/SKILL_AUTHOR_GUIDE.md` (new short guide, ~80 LOC) — copy-pasteable boilerplate for skill authors
- [ ] one-doc-compare prompt body extended with the convention
- [ ] one-doc-compare `compare_ppa_contracts` output gains `_a2ui_actions` array; frontend KeyDifferencesPanel renders per-diff "Compare clauses" buttons; click fires `surface-action: "explain_diff"`
- [ ] Smoke: open `one-doc-compare` skill, pick 2 examples, click a diff row's "Compare clauses" button, see agent respond with cited explanation

### M6 — CLI + verify (~0.25d)
- [ ] `aiplatform examples list/push` Click subcommands (~80 LOC + 3 pytest)
- [ ] `aiplatform bucket browse` Click subcommand (~40 LOC + 2 pytest)
- [ ] chrome-devtools MCP verification on live URL `/chat/@aitana-platform/one-ppa-expert` after deploy: intro bubble visible, picker renders 5 examples, sidebar shows "Contracts library" section, click-an-example flow works end-to-end

## Migration & Rollout

**Database Migrations:** None. `SkillConfig.welcome` is additive nullable; existing Firestore skill rows round-trip unchanged.

**Feature Flags:** None. Skills opt in by adding the `welcome` block to their SKILL.md.

**Rollback Plan:**
- M1: revert SkillConfig schema → `welcome` block dropped on serialization; skills continue working.
- M2: revert SkillExamplesPicker → Workspace tab falls back to EmptyTab.
- M3: revert AssistantIntroBubble → no greeting bubble; existing chat behaviour resumes.
- M4: revert GCSFileBrowser + backend route → sidebar drops the bucket section; uploads still work via `My Documents`.
- M5: revert one-doc-compare prompt extension → interactive button still fires `surface-action` but agent doesn't react. Soft fail; not a blocker.
- Each milestone independently revert-safe.

**Environment Variables:**
- `AITANA_EXAMPLES_BUCKET` (optional, defaults to `aitana-examples-public`) — lets forks point at their own examples bucket without code change

## Testing Strategy

### Backend Tests (pytest)
- [ ] `test_skill_config_welcome.py` — `WelcomeConfig` Pydantic round-trips through Firestore; older skills without `welcome` round-trip with `welcome=None` (4 tests)
- [ ] `test_api_buckets_list.py` — happy path, missing-bucket 404, access-denied 403, pagination next_token (4 tests)
- [ ] `test_one_ppa_expert_welcome.py` + `test_one_doc_compare_welcome.py` — skill templates load with welcome config; example PPAs reference valid bucket + object paths (4 tests)

### Frontend Tests (Vitest + React Testing Library)
- [ ] `SkillExamplesPicker.test.tsx` — 0/1/N examples render, thumbnail fallback, click handler called with correct ExampleDocument (5 tests)
- [ ] `AssistantIntroBubble.test.tsx` — renders message + caption; respects optional skillName prop (2 tests)
- [ ] `GCSFileBrowser.test.tsx` — list, folder expand, click-to-import (4 tests, port from gde-ap-agent test suite)
- [ ] `page.test.tsx` (chat) — synthetic intro bubble appears when fresh + intro_message; doesn't appear on resume; picker mounts in Workspace tab when examples set; sidebar mounts GCSFileBrowser section when bucket_browser set (4 tests covering integration)
- [ ] `ChatMessageList.test.tsx` — `__synthetic: true` messages render via AssistantIntroBubble; non-synthetic via MessageBubble (1 test)

### CLI Tests (pytest)
- [ ] `aiplatform examples list/push --skill <slug>` round-trip (3 tests)
- [ ] `aiplatform bucket browse <name>` happy path + missing-bucket (2 tests)

### Manual / E2E (verified via `aiplatform demo verify` + chrome-devtools MCP)
- [ ] Fresh visit to `/chat/@aitana-platform/one-ppa-expert` → AI intro bubble renders; Workspace tab shows SkillExamplesPicker with 5 PPAs; sidebar shows Contracts library section
- [ ] Click an example → tab opens in Document; chat composer "Will process: <filename>" badge fires
- [ ] Expand Contracts library → folder tree shows; pick a doc → tab opens
- [ ] open `one-doc-compare`, pick 2 PPAs (one from examples, one from ONE library), wait for diff → click "Compare clauses" on a material diff → agent responds in chat
- [ ] `aiplatform demo verify --tenant acmeenergy.com` returns green for the new onboarding assertions

## Security Considerations

- **No new privilege expansion.** Bucket-list proxy runs as SA with existing `roles/storage.objectViewer`. Frontend never gets bucket credentials.
- **Public examples bucket is read-only.** `aitana-examples-public` is curated by Aitana team; no upload path for end users. PII-free corpus (public-domain PPA templates).
- **Tenant bucket browsing respects existing tenant isolation.** `welcome.bucket_browser.bucket` can only be set per-skill at frontmatter time; runtime users can't redirect the browser to arbitrary buckets.
- **GCS-list proxy validates bucket name format** + ALLOWLIST by tenant context — backend rejects buckets not declared in any skill's frontmatter (prevents arbitrary `/api/buckets/<random-bucket>/list` access).
- **Synthetic intro bubble is client-only** — no server-side write. PII boundary unchanged.
- **No new auth surface.**

## Performance Considerations

- **Picker bundle delta** ~15 KB gzipped (SkillExamplesPicker + thumbnail handling).
- **GCSFileBrowser bundle delta** ~12 KB gzipped (port from gde-ap-agent, no expansion).
- **List-objects call** is lazy — only fires when user expands the sidebar section. Backend SA call ~50ms typical.
- **No TTFT impact** — picker + greeting render in initial paint without blocking the AG-UI stream connection.
- **Example documents** indexed in the existing rag-document-corpus path when imported; same indexing cost as user-uploaded docs.

## Success Criteria

- [ ] All backend tests passing (`cd backend && make lint && make test-fast`)
- [ ] All frontend tests passing (`cd frontend && npm run quality:check`)
- [ ] CLI tests passing (`cd cli && uv run pytest`)
- [ ] `aiplatform demo verify` returns green
- [ ] Live URL `/chat/@aitana-platform/one-ppa-expert` renders intro bubble + picker + sidebar bucket section
- [ ] Click-an-example end-to-end: import works, Document tab opens, chat composer shows "Will process: <filename>"
- [ ] one-doc-compare interactive widget: pick 2 docs, click a diff "Compare clauses" → agent responds in chat
- [ ] Public examples bucket `gs://aitana-examples-public/ppa/` has 5 curated PPA contracts
- [ ] Zero hardcoded skill-specific copy in platform components — all comes from `SkillConfig.welcome`
- [ ] Documentation: `docs/talks/ai-ui-protocol-stack.md` verification log entry + `backend/skills/SKILL_AUTHOR_GUIDE.md` covers the interactive-workbench convention

## Open Questions

- **Q1 — Thumbnail strategy.** v1 ships generic doc-icon fallback when `thumbnail` is omitted. Production thumbnails would be auto-generated from doc first-page render (Cloud Function?). Defer to v6.5; v1 uses icons.
- **Q2 — Examples bucket lifecycle.** Who maintains `gs://aitana-examples-public/ppa/`? Recommend Aitana team curates; quarterly review for relevance. Document in `docs/ops/examples-bucket.md` (new).
- **Q3 — Cross-tenant examples.** Could a fork override the Aitana public bucket with its own (e.g. an "the internal-tools fork-examples-public")? `AITANA_EXAMPLES_BUCKET` env var lets them. Recommend default Aitana, fork overrides as needed. Sufficient for v1.
- **Q4 — Interactive widget naming convention.** Should `surface-action` action names be free-form per skill, or follow a recommended pattern (e.g. `<verb>_<entity>` like `explain_diff`, `compare_clauses`, `cite_block`)? Recommend documenting a convention in the SKILL_AUTHOR_GUIDE but not enforcing — skill authors converge naturally with the example pattern.
- **Q5 — Should the AI greeting respect tenant branding?** Currently intro_message is per-skill. Could template `{tenant_name}` placeholders so the greeting feels personal. Defer — adds complexity, current static-message pattern is clear.

## Related Documents

- [docs/design/v6.4.0/internal-app-shell-port.md](internal-app-shell-port.md) — 4.3 sister sprint; ships the SidebarSection + WorkbenchPane primitives this doc hangs onto
- [docs/design/v6.4.0/skill-driven-shell-modes.md](skill-driven-shell-modes.md) — 4.4 sibling; `SkillConfig.shell` and `SkillConfig.welcome` are sibling extensions that compose orthogonally
- the deployment's demo-readiness doc (deployment-private) — 4.2; defined the `acmeenergy-docs` bucket binding this doc reuses for `welcome.bucket_browser`
- [docs/design/v6.3.0/implemented/rag-document-corpus.md](../v6.3.0/implemented/rag-document-corpus.md) — bucket-import path the picker reuses
- [docs/design/v6.3.0/implemented/client-tenant-management.md](../v6.3.0/implemented/client-tenant-management.md) — tenant bucket binding model
- [docs/design/v6.1.0/implemented/local-dev-cli.md](../v6.1.0/implemented/local-dev-cli.md) — CLI parent doc
- gde-ap-agent source: `<local-path>` (port) + `components/chat/SampleInvoicePicker.tsx` (pattern reference)
- CPH UNI source: `<local-path>` — AI-introduction pattern reference
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — verification log
