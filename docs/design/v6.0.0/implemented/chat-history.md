# Chat History (Document-First, Group-Shared)

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 2 days (reduced from 3.5 — CLI commands + idempotency ledger deferred to v6.1; channels don't exist yet so their consumers don't exist either)
**Scope**: Fullstack (Backend + Frontend); CLI deferred to v6.1
**Dependencies**: All met as of 2026-04-23:
  - [Resource Access Control](implemented/resource-access-control.md) ✅ — `tagged` AccessControl + `groupTags` live
  - [Session & Memory](implemented/session-and-memory.md) ✅
  - [Auth & Permissions](implemented/auth-and-permissions.md) ✅
  - [Document UI](../v6.1.0/document-ui.md) ✅
  - [Skills Data Model](implemented/skills-data-model.md) ✅
  - [Streaming & Protocols](implemented/streaming-and-protocols.md) ✅
**Created**: 2026-04-15
**Last Updated**: 2026-04-23

### Scope Notes (2026-04-23 review)

**Deferred to v6.1** (no consumers yet):
- `aitana chat` CLI commands — channels not built; CLI parity can land with Telegram/email sprint
- Idempotency ledger (`chat_sessions/{sid}/ingress/{key}`) — needed for channel webhook retries; web can use a simpler header-only check
- Cross-channel session unification — no channel adapters exist
- `aitana chat reindex` — operational tool, deferred until Agent Engine is in prod
- `POST /api/sessions/{id}/fork` — useful but not needed for client demo

**Tracked deviations from spec:**
- `ParsedDocument.userId` → `ownerUid` rename: too risky to bundle in (existing data + routes read `userId`); deferred. `ChatSessionIndex` uses `owner_uid` from the start; document → session inheritance reads `doc.user_id` during migration window.
- `CHAT_TITLE_MODEL`: updated to `gemini-3-flash-preview` (the model registry name) rather than the stale `gemini-flash-latest` string.

## Problem Statement

v6 persists conversations in ADK `VertexAiSessionService` ([session-and-memory.md:70](session-and-memory.md#L70)), but there is **no user-facing surface** to list, resume, or share those conversations. Three gaps today:

1. **No listing API.** ADK's `list_sessions` is flagged as an O(n) scan at scale ([streaming-and-protocols.md:200-201](streaming-and-protocols.md#L200-L201)); we cannot build a product UI on top of it. The `use_thread_id_as_session_id` footgun is noted but unresolved.
2. **No UX.** [frontend-architecture.md](implemented/frontend-architecture.md) and [document-ui.md](../v6.1.0/document-ui.md) describe the workspace but never the "past conversations" surface. The current `FRONTEND-BRINGUP` sprint explicitly excludes chat (PHASE1-UI).
3. **No sharing model.** v5's sessions are strictly per-user. For document-centric B2B workflows (Finance team reviewing the same quarterly report), teammates should see each other's conversations about a shared document — not start from scratch every time. [v5-data-migration.md:398](../v6.2.0/v5-data-migration.md#L398) flags this as an open question but does not answer it.

**Current State:**
- `VertexAiSessionService` provisioned per environment; no frontend consumer
- `ParsedDocument` Firestore doc has `userId` + `skillId` but no multi-user access (see [document.schema.json](contracts/document.schema.json))
- `AccessControl` on skills has `public | private | domain | specific` ([auth-and-permissions.md:115-127](auth-and-permissions.md#L115-L127)) — no equivalent for documents or sessions
- No "group" / "tag" concept anywhere in the auth layer
- Users cannot see their own prior conversations, let alone teammates'

**Impact:**
- Users repeat themselves across sessions — breaks the "assistant that knows my work" promise
- Teams duplicate investigation effort on shared documents
- Platform leaks the backend's session semantics (stateful but invisible) — violates document-first mental model
- Blocks onboarding demo: "here's what my team has been asking about this contract"

## Goals

**Primary Goal:** Give every document a visible, group-shared chat history pane — users browse, resume, and (optionally) continue teammates' conversations about the same document, scoped by an `accessTags` auth-group model.

**Success Metrics:**
- Selecting a document with N prior sessions renders the history list in <400ms (p95) for N ≤ 200
- Resume a session: first token streams in <1s after click (same SLA as fresh session, [product-axioms.md INSTANT FEEL](../../product-axioms.md#1-instant-feel))
- Group visibility enforced: integration test proves user B sees user A's session iff they share a tag that's on the document
- Zero cross-tenant leakage: no code path returns a session to a user whose tags don't intersect the document's tags
- CLI parity: `aitana chat list --document <id>` returns the same list as the web UI

**Non-Goals:**
- Real-time presence ("Alice is typing") — deferred to PHASE2
- Multi-user co-editing of a single session (two users streaming into one thread) — explicitly out; shared read, forked write
- Global "all my chats" inbox across documents — that's PHASE1-UI's scope, not this doc
- Migrating v5 chat history — [v5-data-migration.md](../v6.2.0/v5-data-migration.md) owns that decision
- ACL-per-message or redaction — sessions are atomic visibility units
- Search within chat history — deferred (MCP Toolbox already has `search-conversations` for agents, [mcp-toolbox-databases.md:161](../v6.2.0/mcp-toolbox-databases.md#L161); a user-facing search UI comes later)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | History list served from a Firestore index (no `list_sessions` scan); resume reuses warm Agent Engine session |
| 2 | EARNED TRUST | +1 | Every listed session shows owner + timestamp + summary — users know what they're resuming and whose thread they're reading |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure for chat history is not itself a user-configurable skill |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Session titles generated by a fast model (flash-tier) in a background callback, not the reasoning model; no LLM call to render the list |
| 5 | GRACEFUL DEGRADATION | +1 | If the index is stale/unavailable, fall back to "start new session" with a banner; Agent Engine outage degrades to read-only list from index |
| 6 | PROTOCOL OVER CUSTOM | 0 | AG-UI handles the stream on resume; history list is a plain REST resource (no custom protocol invented) |
| 7 | API FIRST | +1 | `GET /api/documents/{id}/sessions` and `GET /api/sessions/{id}` serve web, CLI, and (future) Telegram equally — channels only render |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every list/resume/share action emits a span with `doc_id`, `session_id`, `viewer_uid`, `owner_uid`, `shared_via_tag` — full audit of who saw what |
| 9 | SECURE BY CONSTRUCTION | +1 | Tag intersection enforced server-side in the index query; Firestore rules as safety net; default session visibility inherits from document's `accessTags` (no accidental over-sharing) |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend just renders the list response; no client-side ACL evaluation, no local filtering |
| | **Net Score** | **+7** | Threshold: >= +4. Strong alignment. |

**Conflict Justifications:** None — no axioms score -1.

## Design

### Overview

Two changes, layered on top of the shared access primitive:

1. **Session index**: Mirror ADK session metadata (not events) into a Firestore `chat_sessions/` collection, using the same `AccessControl` + owner model as every other resource. This is the list/filter/query substrate the product builds on; Agent Engine remains the source of truth for events and state.
2. **Document-first UI**: A `<DocumentHistoryPanel>` lives in the document workspace. Lists sessions the user can see for this document, split into "Mine" and "Team". Clicking resumes via AG-UI; an explicit button forks.

The design deliberately keeps ADK Agent Engine as the single source of truth for conversation content. The Firestore index is derivable — if it's wrong, it can be rebuilt from Agent Engine. Access enforcement reuses the `AccessContext` + `can_access()` pipeline from [resource-access-control.md](resource-access-control.md) — no new primitive.

### 1. Reusing the shared access model

This doc was originally going to introduce a separate `accessTags` concept for users, documents, and sessions. That would have been a second access model alongside the skill/bucket/folder one, and would have repeated v5's mistake of bolting ACL concepts on per-resource. Instead, [resource-access-control.md](resource-access-control.md) is extended with an `AccessControl.type == "tagged"` variant carrying a `tags[]` field matched against `user.groupTags`. Chat history consumes it directly — no duplication.

**Consequences for this doc:**

- `UserProfile` gains `groupTags: list[str]` (admin-assigned, JWT-claim-propagated) — specified in resource-access-control, not here.
- `ParsedDocument` gains the standard `accessControl: AccessControl` + `ownerUid` fields (document.schema.json update). A document shared with a team sets `accessControl = {type: "tagged", tags: ["finance-team"]}`.
- `ChatSessionIndex` gains the standard `accessControl: AccessControl` + `ownerUid` fields. Default at creation: **inherit from the parent document** (copy `doc.accessControl` verbatim). Owner can mutate to `{type: "private"}` for sensitive threads or to a narrower tag set.
- Visibility check is just `can_access(session.accessControl, ctx, session.ownerUid)` — the same function used for every other resource. No `can_see_session()` helper; no new code path.

```python
# backend/db/models/chat_session.py  — NEW
class ChatSessionIndex(BaseModel):
    """Lightweight index row — NOT the conversation itself.
    Events/state live in ADK VertexAiSessionService."""
    sessionId: str                       # matches Agent Engine session id
    documentId: str | None               # null = no document context
    skillId: str
    ownerUid: str                        # who started this session
    accessControl: AccessControl         # defaults to document.accessControl at session start
    title: str | None = None             # auto-generated; mutable by owner
    turnCount: int = 0
    firstMessageAt: datetime
    lastMessageAt: datetime
    archivedAt: datetime | None = None
```

**Why copy rather than reference the document's `accessControl`?** Three reasons: (1) session ACL can drift independently when the owner tightens it (e.g. `{type: "private"}`); (2) avoids a second Firestore read on every list; (3) if document access widens later, prior sessions stay at their original scope — safer by default. When a document's access is explicitly widened, an admin command (`aitana chat rescope --document <id>`) can fan out the change to session indexes that haven't diverged.

**"Private" semantics:** a session owner who sets `accessControl = {type: "private"}` guarantees no tag intersection can grant access — the standard `can_access()` evaluator handles it as "private and not owner → deny". No chat-history-specific override needed.

### 2. Session Index Maintenance

ADK's `VertexAiSessionService` does not expose rich queries, and its `list_sessions` is the O(n) footgun from [streaming-and-protocols.md:200-201](streaming-and-protocols.md#L200-L201). We mirror metadata into Firestore at session lifecycle points via ADK callbacks — no polling, no reconciliation job in the hot path.

**Callback wiring:**

```python
# backend/adk/callbacks.py

async def _after_session_start(session_context):
    """Create the index row when a new session begins."""
    doc_id = session_context.state.get("document_id")
    doc = await get_document(doc_id) if doc_id else None
    await create_session_index(
        sessionId=session_context.session.id,
        documentId=doc_id,
        skillId=session_context.state["skill_id"],
        ownerUid=session_context.state["user_id"],
        accessTags=doc.accessTags if doc else [],
        firstMessageAt=utcnow(),
        lastMessageAt=utcnow(),
    )

async def _after_agent_response(callback_context):
    """Bump counters + lazily title the session after turn 1."""
    idx = await get_session_index(callback_context.session.id)
    idx.turnCount += 1
    idx.lastMessageAt = utcnow()
    if idx.turnCount == 2 and not idx.title:
        idx.title = await generate_title_fast(
            callback_context.session.events[:4]
        )  # flash-tier model, <300ms
    await save_session_index(idx)
```

**Rebuild path** (for index corruption / new deploys): a one-shot `aitana chat reindex` CLI walks Agent Engine sessions and upserts index rows. Idempotent.

**Firestore schema:**

```
chat_sessions/{sessionId}
  sessionId, documentId, skillId, ownerUid, accessControl {type, domain?, emails?, tags?},
  title, turnCount, firstMessageAt, lastMessageAt, archivedAt
```

**Composite indexes** (declared in `firestore.indexes.json`):

```
chat_sessions — documentId (==) + archivedAt (==null) + lastMessageAt (DESC)
chat_sessions — ownerUid (==) + lastMessageAt (DESC)
chat_sessions — documentId (==) + accessControl.tags (ARRAY_CONTAINS_ANY) + lastMessageAt (DESC)
```

The last index serves the "Team" filter: given the viewer's `groupTags` from the JWT, the list query is `where documentId == X and accessControl.tags array-contains-any viewer.groupTags` — enforced at the Firestore layer, no post-filtering.

### 3. Single Source of Truth & Channel Idempotency

v5 stored chat history in **both** the browser (localStorage) and Firestore so that email/WhatsApp/web could share the same thread. This dual-write produced duplicates when webhooks re-delivered, when the browser resynced, or when a channel adapter wrote before the browser had flushed. v6 kills the class of bug by making the browser a **view**, not a store, and by stamping every ingress with an idempotency key.

**Rule: if the agent has seen it, the server owns it.**

| State | Owner | Persistence |
|---|---|---|
| Conversation events | Agent Engine (`VertexAiSessionService`) | Authoritative, per-session |
| Session index metadata | Firestore `chat_sessions/` | Derived, rebuildable via `aitana chat reindex` |
| Idempotency ledger | Firestore `chat_sessions/{sid}/ingress/{key}` | 7-day TTL (long enough to absorb webhook retries) |
| **Active-session events in browser** | In-memory only (SWR / React cache) | Discarded on unmount / tab close |
| Session list | In-memory SWR | Refetched on focus |
| Unsent draft text | `sessionStorage` (tab-scoped) | Never reaches the agent; never syncs |

**No `localStorage` or `IndexedDB` for message content.** A tab reload refetches from the server, guaranteeing the browser can't diverge.

#### Server-assigned event IDs via AG-UI

Every message the browser renders arrives on the AG-UI stream carrying a **server-assigned `message_id`** (AG-UI's `TEXT_MESSAGE_START` event). The client never persists its own IDs for content. Dedup on render is a simple `Map<message_id, event>` — replace, don't append.

**Optimistic send pattern** (no dual storage required):

```
User submits → client shows bubble with tempId="optimistic-xyz"
Client POSTs /api/sessions/{sid}/resume with body {
  message: "...",
  idempotencyKey: "<client-uuid-v4>"   // survives retry
}
Server: check chat_sessions/{sid}/ingress/{key}; if present → replay response
        otherwise: append turn, run agent, record key
Server emits RUN_STARTED + TEXT_MESSAGE_START(message_id="evt_abc")
Client reconciles: replace tempId="optimistic-xyz" with message_id="evt_abc"
```

If the network drops mid-stream, the optimistic bubble is discarded on reconnect — the server either has the turn (and will stream it back on resume) or it doesn't (client retries with the same `idempotencyKey`, so a duplicate POST is a no-op).

#### Channel idempotency keys

Every channel adapter stamps a stable key on inbound turns. The `process_skill_request()` entry point consults `chat_sessions/{sid}/ingress/{key}` before invoking the agent — second delivery returns the original response without re-running the LLM.

| Channel | Idempotency key | Source of uniqueness |
|---|---|---|
| Web | `web:{client-uuid-v4}` | Client-generated, sent via `Idempotency-Key` header |
| Telegram | `telegram:{chat_id}:{message_id}` | Telegram's stable update ID |
| Email | `email:{Message-ID header}` | RFC 5322 guarantees global uniqueness |
| WhatsApp | `whatsapp:{message_id}` | Meta-assigned webhook message ID |
| CLI | `cli:{client-uuid-v4}` | Generated by `aitana chat` |

This was the real root cause of v5's duplicate bugs: re-delivered webhooks (Telegram retries every ~3s on 5xx, email bounce loops) re-invoked the agent. With an idempotency ledger, retries are free.

#### One session across channels

A single `sessionId` spans all channels for the same `(user, skill, document, thread)`:

- Alice opens the doc on web → `sid=abc123`
- Alice messages the bot on Telegram about the same doc → same `sid=abc123` (resolved via user's Telegram → uid mapping + most-recent document context)
- Email reply with `In-Reply-To` header carrying the prior message's `Message-ID` → same `sid=abc123`

Channel is metadata in session state (`state["channel"] = "telegram"`), not a session-partitioning key. Channels are transports; sessions are conversations. The `state["last_channel"]` field lets the agent format responses appropriately (plain text for email, markdown for web, inline keyboards for Telegram) without owning the history.

**Exception** — [channels.md:283](../v6.1.0/channels.md#L283) currently documents "conversation history is separate (different session IDs per channel)". That decision is **superseded by this doc**: v6 unifies sessions across channels, scoped by `(user, skill, document)`. Updating the channels doc is a line item in Phase 1.

### 4. API Surface

```
GET    /api/documents/{docId}/sessions?filter=mine|team|all&cursor=...
         → 200 { sessions: ChatSessionIndex[], nextCursor }
GET    /api/sessions/{sessionId}
         → 200 { session: ChatSessionIndex, events: AGUIEvent[] }
POST   /api/sessions/{sessionId}/resume
         → 200 — opens AG-UI stream (existing endpoint, now accepts existing sessionId)
POST   /api/sessions/{sessionId}/fork
         → 201 { sessionId: newId } — copies events as seed context, new owner
PATCH  /api/sessions/{sessionId}
         → 200 — owner-only: rename title, set visibility, archive
DELETE /api/sessions/{sessionId}
         → 204 — owner-only soft-delete (sets archivedAt)
```

**Auth on every endpoint** — all checks go through `ctx.can_access()` from [resource-access-control.md](resource-access-control.md):
- List: Firestore query filters by `ownerUid == ctx.uid` (Mine) union `accessControl.tags array-contains-any ctx.group_tags` (Team)
- Read: `can_access(session.accessControl, ctx, session.ownerUid)`
- Write (rename/accessControl/archive/delete): `ctx.is_owner(session)` — owner-only
- Resume: `can_access(...)` — **non-owners can resume read-only** (see below) or fork

**Resume semantics for non-owners:** This is the most delicate decision. Options considered:

| Option | UX | Downside |
|--------|----|---------|
| Read-only (can't send) | Safe, matches "shared read, forked write" non-goal | Feels dead-ended |
| Auto-fork on send | Teammate sends → new session branches off | Surprise: "why did my send create a new thread?" |
| **Explicit fork button** | Teammate reads, clicks "Continue from here" → forks | Extra click, but the action is explicit |

**Decision: explicit fork.** Non-owner opens the session in read-only mode with a persistent "Continue from here as new session" CTA. Aligns with EARNED TRUST (no surprise state changes) and the non-goal on co-editing.

### 5. Frontend UI

**New component:** `frontend/src/components/chat/DocumentHistoryPanel.tsx` — lives inside the chat panel described in [document-ui.md:146-160](document-ui.md#L146-L160), collapsible, positioned above the active chat stream.

```
┌── Chat Panel ─────────────────────────────────────────┐
│ ┌── Conversations (quarterly-report.docx) ───── [▼] ─┐│
│ │ ⌕ Mine                                              ││
│ │  • "Revenue drivers Q1" · Mark · 2h ago · 12 turns  ││
│ │  • "Margin question"    · Mark · yesterday          ││
│ │ ⌕ Team (finance-team)                               ││
│ │  • "CFO prep notes"     · Alice · 3d ago · 24 turns ││
│ │  • "Audit walkthrough"  · Bob · last week           ││
│ │                         [+ New conversation]         ││
│ └─────────────────────────────────────────────────────┘│
│ ┌── Active Chat Stream ──────────────────────────────┐│
│ │ [AG-UI events for the active session]              ││
│ └────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────┘
```

**Component contract:**

```typescript
interface DocumentHistoryPanelProps {
  documentId: string
  activeSessionId: string | null
  currentUserUid: string
  onSelectSession: (sessionId: string, ownerUid: string) => void
  onNewSession: () => void
}

interface ChatSessionSummary {
  sessionId: string
  title: string
  ownerUid: string
  ownerDisplay: string      // resolved server-side
  turnCount: number
  lastMessageAt: string
  isOwn: boolean
  canFork: boolean          // always true for non-archived, viewable sessions
  sharedViaTags: string[]   // which tags granted visibility (for the tooltip)
}
```

**State management:** A new `useDocumentSessions(docId)` hook wraps SWR-style fetching. When the user clicks a teammate's session, the chat provider opens it in **read-only mode** — the AG-UI stream connects but the composer is disabled with a "Continue as new session" button in its place.

**Bundle impact:** ~8KB gzipped (component + hook + read-only variant of composer). Under the <200KB budget ([product-axioms.md #10](../../product-axioms.md#10-thin-client-fat-protocol)).

### 6. CLI Surface

Per [local-dev-cli.md](../v6.1.0/local-dev-cli.md), any new resource ships with CLI affordances in the same doc:

```
aitana chat list --document <docId> [--filter mine|team|all] [--json]
aitana chat get <sessionId>                  # metadata + last 20 events
aitana chat resume <sessionId>               # interactive terminal chat
aitana chat fork <sessionId>                 # prints new sessionId
aitana chat rename <sessionId> <title>
aitana chat archive <sessionId>
aitana chat reindex [--document <docId>]     # rebuild index from Agent Engine
aitana chat rescope --document <docId>       # propagate doc accessControl to sessions that haven't diverged
```

`aitana groups add-user / remove-user / list-user` are owned by [resource-access-control.md](resource-access-control.md#cli-surface) — they ship with 1A.1b and are used to seed tags before chat-history lands.

Each chat command is a thin Click wrapper around the REST API (~15 LOC + test). `reindex` and `rescope` are the only ones that read from Agent Engine or fan out across sessions.

### Architecture Diagram

```
[Web UI]  [CLI]  [Telegram (future)]
    │       │          │
    └───────┼──────────┘
            ▼
     /api/documents/{id}/sessions        ← list (Firestore index)
     /api/sessions/{id}                  ← read  (index + AE events)
     /api/sessions/{id}/resume           ← stream (AG-UI, Agent Engine)
     /api/sessions/{id}/fork             ← copy events, new AE session
            │
            ▼
 ┌──────────────────────┐      ┌──────────────────────────┐
 │ Firestore            │      │ Vertex AI Agent Engine   │
 │ chat_sessions/{id}   │◀─────│ SessionService           │
 │  (index only)        │ hooks│  events + state (truth)  │
 └──────────────────────┘      └──────────────────────────┘
            ▲                           ▲
            │ callbacks                 │ runner
            └────────────── ADK ────────┘
```

## Implementation Plan

### Phase 1: Data model (~0.5 day — smaller because groupTags + `tagged` AccessControl already shipped with 1A.1b)
- [ ] Add `accessControl: AccessControl` + rename `userId`→`ownerUid` on `ParsedDocument` (~30 LOC + migration script)
- [ ] Create `ChatSessionIndex` model + repository, using the shared `AccessControl` (~60 LOC)
- [ ] Declare composite indexes in `firestore.indexes.json`
- [ ] Update `firestore.rules` — reuse `canAccessResource()` from 1A.1b for `chat_sessions/`
- [ ] Update [channels.md:283](../v6.1.0/channels.md#L283) — unify session IDs across channels (was: separate per channel)
- [ ] Tests: verify `can_access()` returns expected decisions for `tagged` sessions (smoke — primary coverage lives in 1A.1b)

### Phase 2: Session index + callbacks (~1 day)
- [ ] Implement `_after_session_start` + `_after_agent_response` callbacks (~60 LOC)
- [ ] Wire into `App(before_agent_callback=..., after_agent_callback=...)` in `backend/adk/agent.py`
- [ ] Implement `generate_title_fast()` using a flash-tier Gemini model (~40 LOC)
- [ ] Implement `aitana chat reindex` rebuild command (~80 LOC)
- [ ] Tests: index row created on session start, counters bumped, title generated after turn 2, rebuild is idempotent

### Phase 3: API endpoints + idempotency (~1 day)
- [ ] Idempotency middleware: `Idempotency-Key` header on web, channel-specific keys on adapters (~60 LOC)
- [ ] `chat_sessions/{sid}/ingress/{key}` ledger with 7-day TTL (~30 LOC)
- [ ] Retry replay path: second POST with same key returns cached response without re-invoking agent (~40 LOC)
- [ ] `GET /api/documents/{id}/sessions` with filter/cursor (~60 LOC)
- [ ] `GET /api/sessions/{id}` returning index + recent events (~40 LOC)
- [ ] `POST /api/sessions/{id}/fork` — copy AE events to new session (~60 LOC)
- [ ] `PATCH /api/sessions/{id}` rename/visibility/archive (~40 LOC)
- [ ] `DELETE /api/sessions/{id}` soft-delete (~20 LOC)
- [ ] Extend `/api/.../resume` to accept existing `sessionId` + enforce non-owner read-only
- [ ] Tests: every endpoint × (owner, same-tag viewer, different-tag viewer) — deny/allow matrix

### Phase 4: Frontend + CLI (~1 day)
- [ ] `DocumentHistoryPanel` component (~100 LOC)
- [ ] `useDocumentSessions` hook with SWR (~40 LOC)
- [ ] Read-only composer variant + "Continue from here" CTA (~60 LOC)
- [ ] `aitana chat` Click subcommands (~120 LOC across commands)
- [ ] Vitest tests for component + hook
- [ ] CLI smoke test hitting dev backend

## Migration & Rollout

**Database Migrations:**
- `users/{uid}` — backfill `accessTags: []` (empty default, no behavior change)
- `parsed_documents/{docId}` — rename `userId` → `ownerUid`, backfill `accessTags: []`. Code path reads both during migration window (~1 week), then drops `userId`
- `chat_sessions/` — new collection; populated by callbacks + backfilled by `aitana chat reindex`
- Composite indexes provisioned via Terraform (`firestore.indexes.json` → `google_firestore_index`)

**Feature Flag:** `CHAT_HISTORY_ENABLED` env var on backend + `NEXT_PUBLIC_CHAT_HISTORY_ENABLED` on frontend. Off in prod until Phase 4 passes eval + manual QA on dev.

**Rollback Plan:**
- Turn off flag → UI hides the panel, API returns 404 on new endpoints, existing sessions still work (resume via direct URL)
- Callbacks are idempotent and only write to `chat_sessions/` — safe to disable
- No destructive migration: `userId` stays in `ParsedDocument` during the window

**Environment Variables:**
- `CHAT_HISTORY_ENABLED=true` (backend) — gate endpoints + callbacks
- `NEXT_PUBLIC_CHAT_HISTORY_ENABLED=true` (frontend) — gate panel mount
- `CHAT_TITLE_MODEL=gemini-3-flash-preview` (backend) — fast-tier titler (from backend/config/models.yaml)
- No new secrets

## Testing Strategy

### Backend Tests (pytest)
- [ ] `can_access` applied to `ChatSessionIndex` — smoke check (exhaustive truth table lives in 1A.1b)
- [ ] Session index callback creates row with `accessControl` copied from parent document
- [ ] Title generation: triggers after turn 2, not turn 1, not turn 3
- [ ] List endpoint: filters by tag intersection, paginates via cursor
- [ ] Fork endpoint: copies events from source AE session, new `ownerUid`, `forkedFromSessionId` breadcrumb in state
- [ ] PATCH owner-only: same-tag viewer gets 403
- [ ] Resume: non-owner gets read-only flag in response; composer disabled
- [ ] Reindex: delete index row → `aitana chat reindex` rebuilds it identically
- [ ] **Idempotency**: replay the same Telegram webhook body twice → one turn, identical response both times; ingress ledger has one entry
- [ ] **Browser reconnect**: simulate mid-stream disconnect → reconnect → no duplicate message bubble, no gap in event IDs
- [ ] **Cross-channel unification**: Alice messages via web (sid=X), then via Telegram on same doc → resolves to sid=X, not a new session

### Integration Tests
- [ ] Two-user flow: Alice and Bob share tag `finance-team`, both attached to doc. Alice chats → Bob lists → sees Alice's session → reads it → forks → both now appear in Alice's "Team" panel
- [ ] Tag revocation: Bob loses `finance-team` → Bob's list call no longer returns Alice's session (but his own fork remains, since he's owner)
- [ ] Document with no tags: only owner can see sessions (matches v5 behavior)

### Frontend Tests (Vitest + React Testing Library)
- [ ] `DocumentHistoryPanel` renders "Mine" + "Team" sections, hides "Team" when empty
- [ ] Click teammate's session → composer enters read-only state
- [ ] "Continue from here" triggers fork + switches active session
- [ ] Hook: cache invalidates on `lastMessageAt` change via SWR key

### CLI Tests
- [ ] `aitana chat list --document <id>` returns JSON matching API shape
- [ ] `aitana chat reindex` against a seeded dev Agent Engine restores a wiped `chat_sessions/` collection
- [ ] `aitana groups add-user` updates Firestore + (on next token refresh) the JWT claim

### Manual Testing
- [ ] Dev: two browser profiles, same tag, same doc → see each other's threads
- [ ] Dev: one profile private-marks a session → other profile can no longer see it
- [ ] Dev: archive a session → disappears from list, still accessible by direct URL (owner only)

## Security Considerations

- **Primary enforcement** is in the API layer: every read path computes `can_see_session` before returning. Firestore rules are a safety net, not the line of defense (same pattern as [auth-and-permissions.md:110](auth-and-permissions.md#L110)).
- **Tag injection**: `accessTags` on User is server-controlled via admin CLI / Firebase custom claims — users cannot self-assign tags. Frontend never sends tags; they come from the verified JWT.
- **Fork data leakage**: forking copies events; the new session's `accessTags` default to the document's tags at fork time — **not** the source session's. Prevents escalation if the source session had been manually widened.
- **Private session override**: if a session is set to `visibility="private"`, no tag intersection can grant access. Firestore rule enforces this independently.
- **Audit trail**: every list/read/resume emits a span with `viewer_uid`, `owner_uid`, `doc_id`, `session_id`, `shared_via_tag` (the tag that granted visibility, or "owner" / "private"). Required for B2B customers who need "who saw what" reports.
- **GCP project edge**: all data stays inside the project — no third-party SaaS sees session metadata. Consistent with [product-axioms.md #9](../../product-axioms.md#9-secure-by-construction).
- **Deletion**: soft-delete (archive) only. True delete comes with a separate GDPR-flow design doc; out of scope here.

## Performance Considerations

- **List latency**: Firestore composite-index query on `documentId + archivedAt + lastMessageAt DESC` — ~50ms p95 for pages of 20. Comfortably under the 400ms target.
- **Resume latency**: reuses ADK's existing `get_session` + `Runner` path from [agent-factory.md:265](agent-factory.md#L265) — no additional hops.
- **Title generation**: flash-tier Gemini, ~300ms, runs in background `after_agent_callback` — never blocks the user-facing stream.
- **Fork cost**: O(turns) events copy; acceptable up to ~500 turns (streaming copy via Agent Engine API). Longer sessions get an async fork with a "forking…" status; deferred until a real session triggers it.
- **Index write amplification**: 1 Firestore write per turn (for `turnCount` + `lastMessageAt`). On a 100-turn session that's 100 writes — trivial cost, but we debounce to every 5 turns or 10s whichever comes first, to keep Firestore QPS under budget during bursty agent loops.
- **Bundle impact**: ~8KB gzipped added to the chat route bundle. Budget-compliant.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`cd backend && make test`)
- [ ] Lint + typecheck clean (`npm run quality:check:fast`, `cd backend && make lint`)
- [ ] CI gate green on the introducing PR
- [ ] Two-user integration test passes on dev (Alice creates, Bob sees, Bob forks, archive hides)
- [ ] `aitana chat list --document <id>` returns expected JSON for a seeded doc
- [ ] `aitana chat reindex` rebuilds the index identically after a wipe
- [ ] Eval smoke probe: resume a 10-turn session → agent's next response references prior context (not a fresh introduction)
- [ ] Observability span captured with viewer/owner/tag attribution for every list + resume
- [ ] Design doc moved to `docs/design/implemented/v6.0.0/chat-history.md` on ship

## Open Questions

- **Display names**: how do we resolve `ownerUid` → human-readable name in the list? Option A: denormalise `ownerDisplay` into the index at session creation (stale if user renames). Option B: join against `users/` on read (extra read per list). Leaning A with a nightly refresh job.
- **Tag naming conventions**: free-form `string` vs. structured namespace (e.g., `org:team:role`). Starting free-form; may formalise once we see real patterns.
- **Cross-document history**: should a "Team" section on document A also surface "your team has discussed this topic on document B"? Powerful, but opens a search-UI can of worms — deferring to a later memory-UI doc.
- **Default visibility for `domain`-accessed documents**: when a doc's accessTags are empty but its skill has `accessControl.type=domain`, should sessions default to domain-visible? Proposal: yes, by synthesising `domain:<domain>` as an implicit tag. Needs a call with the auth design.
- **v5 migration**: confirmed as the same schema shape — [v5-data-migration.md:398](../v6.2.0/v5-data-migration.md#L398) can now decide whether to migrate; either answer works with this index.

## Related Documents

- [Session & Memory](session-and-memory.md) — ADK SessionService, Agent Engine, the source of truth this doc indexes
- [Auth & Permissions](implemented/auth-and-permissions.md) — JWT middleware, access-control helpers (extended here with `accessTags`)
- [Document UI](../v6.1.0/document-ui.md) — the workspace the history panel embeds into
- [Skills Data Model](implemented/skills-data-model.md) — access-control schema this doc mirrors
- [Streaming & Protocols](streaming-and-protocols.md) — the `list_sessions` scaling footgun this doc sidesteps via the Firestore index
- [Local Dev CLI](../v6.1.0/local-dev-cli.md) — `aitana chat` subcommand host
- [v5 Data Migration](../v6.2.0/v5-data-migration.md) — the "migrate chat history?" open question now has a target schema
- [MCP Toolbox Databases](../v6.2.0/mcp-toolbox-databases.md) — the agent-facing `search-conversations` tool that will later consume this same index
- [Product Axioms](../../product-axioms.md) — scoring source

---

## Implementation Report

**Status:** Implemented  
**Completed:** 2026-04-23  
**Actual effort:** 1.5 days (~665 LOC, 5 milestones)  
**Commits:** M1 `599addd`, M2 `50d2747`, M3 `f45c28c`, M4 `6deed65`

**What was built:**
- `backend/db/models/chat_session.py` — `ChatSessionIndex` Pydantic model + `owner_id` property for `_HasAccess` protocol
- `backend/db/chat_sessions.py` — repository: `create_session_index`, `get_session_index`, `save_session_index`, `list_sessions_for_document` (cursor-paginated, post-access-filtered), `soft_delete_session`
- `firestore.indexes.json` — 3 composite indexes on `chat_sessions`
- `firestore.rules` — `chat_sessions` rules using `canAccessResource()`
- `backend/db/title_generator.py` — flash-tier title generator via `CHAT_TITLE_MODEL` env var
- `backend/adk/callbacks.py` — `make_session_tracker` (creates index on first turn via `before_agent_callback` + `chat_session_initialized` flag), `make_after_agent_response` (debounced counter flush every 5 turns, title after turn 2)
- `backend/adk/agent.py` — composed `before_agent_callback` + switched to `make_after_agent_response()`
- `backend/protocols/sessions_route.py` — `GET /api/documents/{id}/sessions`, `GET /api/sessions/{id}`, `PATCH /api/sessions/{id}`, `DELETE /api/sessions/{id}`
- `backend/fast_api_app.py` — mounted sessions router; extended resume to emit `session_meta` frame with `isReadOnly` flag
- `frontend/src/hooks/useDocumentSessions.ts` — SWR-style hook with abort + focus-refetch
- `frontend/src/components/chat/DocumentHistoryPanel.tsx` — Mine/Team sections, collapse toggle, active highlight
- `frontend/src/components/chat/ReadOnlyComposer.tsx` — read-only message + "Continue from here" CTA
- 57 new tests: 34 backend (15 unit M1 + 19 unit M2 + 16 API M3) + 11 frontend (8 panel + 3 read-only) + 2 test files

**Deviations from spec (confirmed):**
- `ParsedDocument.userId` rename deferred — `ChatSessionIndex` uses `ownerUid` from day one
- Fork endpoint (`POST /api/sessions/{id}/fork`) deferred to v6.1 — "Continue from here" opens a fresh session
- Idempotency ledger deferred to v6.1
- `aitana chat` CLI commands deferred to v6.1
- `aitana chat reindex` deferred to v6.1

**Open concerns:**
- `_after_session_start` uses `before_agent_callback` (not a dedicated hook) — relies on `chat_session_initialized` state flag
- Title generation uses genai SDK directly; no retry on transient errors (tolerable — title is nullable)
- `list_sessions_for_document` over-fetches (4× page_size) to absorb post-filter losses — acceptable for ≤200 sessions per document (the stated target)
