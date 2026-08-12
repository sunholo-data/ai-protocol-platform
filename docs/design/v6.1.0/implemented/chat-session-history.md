# Chat Session History

**Status**: Implemented
**Priority**: P1
**Estimated**: 2.5 days
**Scope**: Fullstack
**Dependencies**: chat-history (v6.0.0 ✅), agent-factory (v6.0.0 ✅), streaming-and-protocols (v6.0.0 ✅)
**Created**: 2026-04-24
**Last Updated**: 2026-04-24

## Problem Statement

**Current State:**
- `skill_processor.py` holds a module-level `_session_service = InMemorySessionService()` — a different instance from the `VertexAiSessionService` wired via `session_service_uri` in `get_fast_api_app()`. Even with `AGENT_ENGINE_ID` set in production, streaming sessions go to the per-process in-memory store and are lost on restart.
- The v6.0.0 CHAT-HISTORY sprint tracks session *metadata* in Firestore (`ChatSessionIndex` — turn count, title, timestamps). But the actual message content lives in ADK's session service, which is always in-memory in the current code path.
- `HttpAgent` on the frontend starts with `messages: []` on every page load. The model *does* see history (ADK sends prior events to the LLM as context), but the **chat panel shows a blank screen** on every visit.
- There is no endpoint to fetch prior messages for a session and no skill-level session list. The only session listing is document-scoped (`GET /api/documents/{docId}/sessions`).

**Impact:**
- Every browser refresh wipes the visible conversation, breaking user trust
- Users cannot resume or browse past chats with a skill
- In production, the ADK session service is the wrong one — sessions are silently lost across Cloud Run revision rollouts

## Goals

**Primary Goal:** Make chat history visible and resumable — when a user opens a skill chat, they see their last session's messages and can browse and resume prior sessions.

**Success Metrics:**
- Opening `/chat/{skillId}` loads the most recent session and pre-populates the chat panel with its messages (no blank screen)
- A session list sidebar lets the user select any prior session
- `GET /api/sessions/{sessionId}/messages` returns the full turn history from ADK
- `GET /api/skills/{skillId}/sessions` lists the user's sessions for a skill
- In local dev without `AGENT_ENGINE_ID`, in-memory service works for quick iteration (messages survive the page refresh within the same server process)
- With `AGENT_ENGINE_ID` set, sessions persist to Vertex AI Agent Engine across restarts — same pattern as the Firestore skills service

**Non-Goals:**
- Session forking (deferred in CHAT-HISTORY, still deferred)
- Cross-session search or full-text indexing
- Shared/team session message replay (access-controlled metadata already exists; message replay is owner-only for now)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | History loads before first user interaction; no blank-screen flash |
| 2 | EARNED TRUST | +1 | User can verify what was said in prior turns; model doesn't silently lose context |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; no new user-facing skill concept |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model changes |
| 5 | GRACEFUL DEGRADATION | +1 | If message fetch fails, open a fresh session rather than blocking the user |
| 6 | PROTOCOL OVER CUSTOM | +1 | ADK `session_service.get_session()` is the standard ADK API; no custom event format |
| 7 | API FIRST | +1 | Two new REST endpoints; `GET /api/sessions/{id}/messages` is clean and cacheable |
| 8 | OBSERVABLE BY DEFAULT | 0 | Existing OTEL tracing covers the fetch path |
| 9 | SECURE BY CONSTRUCTION | +1 | Message fetch is owner-only; uses the same `can_access()` + `is_owner()` gates from CHAT-HISTORY |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend seeding is a one-time fetch; session state lives in ADK |
| | **Net Score** | **+7** | Threshold: >= +4 ✓ |

## Design

### Root Cause Fix: Shared Session Service

`skill_processor.py` must use the same session service instance as `get_fast_api_app()`. The module-level `InMemorySessionService()` is replaced with a call to `get_session_service()` from `adk/session.py`, which returns `VertexAiSessionService` when `AGENT_ENGINE_ID` is set and `InMemorySessionService` otherwise.

```python
# backend/skills/skill_processor.py — before
_session_service = InMemorySessionService()

# backend/skills/skill_processor.py — after
from adk.session import get_session_service
_session_service = get_session_service()
```

This one-line fix makes streaming sessions durable in prod without changing any other code path.

### New Backend Endpoints

#### `GET /api/sessions/{sessionId}/messages`

Returns the full turn history for a session as a list of `{role, content, timestamp}` objects.

```
Response 200:
{
  "messages": [
    {"role": "user",      "content": "Hello", "timestamp": 1714000000.0},
    {"role": "assistant", "content": "Hi! How can I help?", "timestamp": 1714000001.2}
  ],
  "session_id": "thread-abc123"
}
```

**Implementation:**
1. Auth gate: caller must be the session owner (non-owners can see metadata but not content; same model as the `is_owner()` check in PATCH/DELETE).
2. Call `session_service.get_session(app_name=APP_NAME, user_id=owner_uid, session_id=session_id)`. The `owner_uid` is read from the `ChatSessionIndex` in Firestore (already stored there from CHAT-HISTORY).
3. Filter events: keep events where `event.content` is non-empty text. Exclude tool call events, system events, and empty model turns.
4. Map: `author == "user"` → `role="user"`, any other author (skill agent name) → `role="assistant"`.
5. Return chronologically ordered list.

**ADK Event filtering:**
```python
def _events_to_messages(events: list[Event]) -> list[dict]:
    messages = []
    for e in events:
        if not e.content or not e.content.parts:
            continue
        text = " ".join(p.text for p in e.content.parts if p.text).strip()
        if not text:
            continue
        role = "user" if e.author == "user" else "assistant"
        messages.append({"role": role, "content": text, "timestamp": e.timestamp})
    return messages
```

#### `GET /api/skills/{skillId}/sessions`

Lists the caller's sessions for a skill, newest first. Backed by Firestore `ChatSessionIndex` (CHAT-HISTORY already writes these rows), not by ADK session service directly (Firestore is faster to query and already indexed).

```
Query params: page_size (default 20, max 50), cursor (opaque pagination token)
Response 200: same shape as GET /api/documents/{docId}/sessions but filtered to skillId
```

**Note:** The existing `GET /api/documents/{docId}/sessions` already covers document-scoped sessions. This endpoint adds the skill-scoped (no document) variant by filtering `ChatSessionIndex` rows on `skillId + ownerUid`.

### `APP_NAME` constant

ADK session service keys sessions by `(app_name, user_id, session_id)`. The `app_name` must be consistent between the writer (skill processor) and the reader (messages endpoint). Define a module-level constant:

```python
# backend/adk/session.py
APP_NAME = "aitana-platform"
```

All session operations — create, append, get — use this constant. The `get_fast_api_app()` call in `fast_api_app.py` also uses it (check ADK's built-in route to confirm it uses the same app name, or override explicitly).

### Frontend Changes

#### Session history sidebar

Add a `SkillSessionPanel` component alongside `DocumentHistoryPanel`. Displays the user's recent sessions for the current skill, fetched via `GET /api/skills/{skillId}/sessions`. Clicking a session loads its messages.

```
frontend/src/components/chat/
  SkillSessionPanel.tsx        — session list sidebar for skill chats
  __tests__/SkillSessionPanel.test.tsx
frontend/src/hooks/
  useSkillSessions.ts          — SWR fetch for session list
  useSessionMessages.ts        — fetch prior messages for a session
```

#### Chat page session routing

`/chat/{skillId}` becomes `/chat/{skillId}` (no required sessionId) but the page tracks the active session in state and URL:

```
/chat/{skillId}              → start fresh or load most recent session
/chat/{skillId}?session=abc  → load session abc (shareable link)
```

The `?session=` query param is used (not a path segment) so the existing `[skillId]` route structure is unchanged.

#### Session message seeding

When loading a session, `useSessionMessages` fetches `GET /api/sessions/{sessionId}/messages` and converts the result to `SkillMessage[]`. These are passed to `ChatMessageList` as `initialMessages` — displayed above the live stream, visually distinct (slightly greyed header "Earlier in this conversation"). The `HttpAgent` is also seeded via `agent.addMessage()` calls so the AG-UI state machine has the prior context for continuity UI.

```tsx
// ChatPage — simplified
const { sessionId, setSessionId } = useSessionState(skillId);
const { initialMessages, isLoadingHistory } = useSessionMessages(sessionId);

// ChatMessageList receives both historical and live messages
<ChatMessageList
  initialMessages={initialMessages}
  messages={liveMessages}   // from useSkillAgent
  ...
/>
```

#### Graceful degradation

If `GET /api/sessions/{sessionId}/messages` returns 404 or 500, the chat opens in fresh-session mode with an inline notice: "Couldn't load previous messages — starting fresh." This never blocks the user.

### CLI Surface

No new CLI commands for this sprint. The existing `aitana session list` and `aitana session get` commands planned in `local-dev-cli.md` cover the developer-facing session inspection need.

### Session Service in Local Dev

Use `AGENT_ENGINE_ID` to opt into persistence (same as Firestore skills config):

```bash
# .env.local — without Agent Engine (fast dev, no persistence across restarts)
# AGENT_ENGINE_ID=            # unset → InMemorySessionService

# .env.local — with Agent Engine (persistent, matches prod behaviour)
AGENT_ENGINE_ID=123456789
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

The `get_session_service()` function already handles this switch. No new env vars are needed.

## Implementation Plan

### Phase 1: Root cause fix + backend endpoints (~1 day)

- [ ] Change `skill_processor.py`: replace `InMemorySessionService()` with `get_session_service()` (~5 lines)
- [ ] Define `APP_NAME = "aitana-platform"` in `adk/session.py`; thread it through `build_agui_adk_agent()` and `skill_processor.py` consistently
- [ ] Add `GET /api/sessions/{sessionId}/messages` to `protocols/sessions_route.py` (~60 lines):
  - Auth: require `is_owner()` (403 for non-owners)
  - Fetch `ChatSessionIndex` from Firestore → get `owner_uid`
  - Call `session_service.get_session(APP_NAME, owner_uid, session_id)` 
  - Filter + map events via `_events_to_messages()`
- [ ] Add `GET /api/skills/{skillId}/sessions` to `skills/routes.py` (~40 lines):
  - Auth: `get_current_user`
  - Query Firestore `chat_sessions` where `skillId == skill_id AND ownerUid == caller_uid`
  - Return same `ListSessionsResponse` shape as document sessions
- [ ] Tests: `test_session_messages_route.py` — owner fetch, non-owner 403, empty session, events filtering (~40 lines)
- [ ] Tests: `test_skill_sessions_route.py` — returns caller's sessions, excludes other users (~20 lines)

### Phase 2: Frontend session list + routing (~1 day)

- [ ] `useSkillSessions.ts` — SWR fetch for `GET /api/skills/{skillId}/sessions`, auto-refresh on focus (~40 lines)
- [ ] `useSessionMessages.ts` — fetch `GET /api/sessions/{sessionId}/messages`, returns `SkillMessage[]` (~35 lines)
- [ ] `SkillSessionPanel.tsx` — scrollable list of past sessions, title + relative time, click to load (~80 lines)
- [ ] Chat page: `?session=` query param handling, `setSessionId` on session click, pass `sessionId` in stream body (~30 lines)
- [ ] Tests: `SkillSessionPanel.test.tsx`, `useSkillSessions.test.ts`, `useSessionMessages.test.ts` (~60 lines total)

### Phase 3: History seeding + polish (~0.5 day)

- [ ] `ChatMessageList`: accept `initialMessages` prop; render them with a section divider "Earlier in this conversation" above live messages (~20 lines)
- [ ] Seed `HttpAgent` with prior messages via `agent.addMessage()` before first `runAgent()` call so AG-UI continuity UI is correct
- [ ] Error handling: history fetch failure → fresh session with inline notice
- [ ] Manual test: open skill, send 3 messages, refresh page → messages restored, 4th message sends correctly with model context intact

## API Changes

**New endpoints:**

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/api/sessions/{sessionId}/messages` | Owner only | Returns `{messages: ChatMessage[], session_id: str}` |
| `GET` | `/api/skills/{skillId}/sessions` | Caller's own sessions | Returns `ListSessionsResponse` (same shape as document sessions) |

**Modified:**
- `skill_processor.py`: `_session_service` now from `get_session_service()` — no API change, just durability

## Testing Strategy

### Backend
- `test_session_messages_route.py`: owner fetch returns messages in order, non-owner returns 403, empty session returns `[]`, tool events are filtered out, text events are included
- `test_skill_sessions_route.py`: returns sessions for the calling user's skill, excludes other users' sessions
- `test_skill_processor_session_service.py`: verify `_session_service` is not `InMemorySessionService` when `AGENT_ENGINE_ID` is set

### Frontend
- `SkillSessionPanel`: renders session list, highlights active, triggers callback on click
- `useSkillSessions`: fetches correct endpoint, returns sessions array
- `useSessionMessages`: fetches messages on sessionId change, returns `SkillMessage[]`
- `ChatMessageList`: renders `initialMessages` above live messages with divider

### Manual
- [ ] Send 3 messages; refresh the page → chat panel shows prior messages
- [ ] Click a past session in the sidebar → panel loads its messages
- [ ] Send a follow-up message after loading history → model has correct context (references prior turn)
- [ ] Non-owner visiting session URL → 403 handled gracefully (no crash)
- [ ] With `AGENT_ENGINE_ID` unset (local dev) → in-memory, history lost on server restart as expected

## Migration & Rollout

**No Firestore schema changes** — `ChatSessionIndex` already exists. The new `GET /api/skills/{skillId}/sessions` just queries it with a different filter.

**Session service fix is non-breaking** — existing session IDs (`thread-{hex}`) continue to work. Sessions created before this fix (stored in the old in-memory service) are gone; sessions created after are durable.

**Rollback:** revert `skill_processor.py` one-liner; existing in-memory behaviour is restored. No data loss — in-memory sessions were already ephemeral.

## Security Considerations

- `GET /api/sessions/{sessionId}/messages` is owner-only (`is_owner()` gate). Non-owners see session metadata (PATCH/GET session) but never message content. This is stricter than document access because chat content is more personal.
- Session IDs are random UUIDs (not guessable). Returning 404 for sessions the caller can't access would leak existence — return 403 consistently, matching the CHAT-HISTORY design.
- ADK session `user_id` is the Firebase `uid` — no cross-user session access is possible at the ADK layer either.

## Open Questions

- **`APP_NAME` in `get_fast_api_app()`**: Need to verify what `app_name` ADK uses internally for its built-in routes and whether `build_agui_adk_agent()` already threads it correctly. If the ADK-managed session service uses a different `app_name`, the `get_session()` call in our endpoint will 404. Resolve in Phase 1 with a unit test.
- **`VertexAiSessionService.list_sessions()` behaviour**: The Firestore-backed `ChatSessionIndex` is the source of truth for the sessions list (it's already indexed). But `GET /api/sessions/{sessionId}/messages` calls `session_service.get_session()` directly on Agent Engine. Verify Agent Engine session IDs match the `sessionId` stored in `ChatSessionIndex`.

## Related Documents

- [chat-history.md](../v6.0.0/implemented/chat-history.md) — session metadata, Firestore model, CRUD API (v6.0.0 ✅)
- [chat-message-rendering.md](chat-message-rendering.md) — `ChatMessageList` + `SkillMessage` type
- [skill-friendly-urls.md](skill-friendly-urls.md) — `?session=` param interacts with slug routing
- [local-dev-cli.md](local-dev-cli.md) — `aitana session list/get` commands (CLI surface for developers)
- [Product Axioms](../../product-axioms.md)

---

## Implementation Report

**Completed**: 2026-04-24
**Actual Effort**: 2.5 days vs 2.5 estimated (on target)
**Evaluation**: 92/100 (PASS, round 1)

### What Was Built
- **Root cause fix**: `skill_processor.py` was instantiating its own `InMemorySessionService` at module level, separate from `get_fast_api_app()`'s session service. Sessions never persisted across requests. Fixed by making `get_session_service()` a singleton so all callers share one store.
- **`APP_NAME` constant**: Exported from `adk/agui.py` (was private `_DEFAULT_APP_NAME`) so the sessions endpoint imports the same value as the agent infrastructure.
- **`GET /api/sessions/{sessionId}/messages`**: Reads ADK session events, filters to text-only (strips tool events), returns chronological `ChatMessage[]`. Owner-only auth via `AccessContext`.
- **`GET /api/skills/{skillId}/sessions`**: Queries `ChatSessionIndex` in Firestore for the caller's sessions. Paginates with cursor.
- **`useSkillSessions`**: React hook fetching the sessions list, with focus refetch.
- **`useSessionMessages`**: React hook that fetches history only when `sessionId` is non-null, returns `SkillMessage[]`.
- **Session routing**: `?session=abc` in the URL drives both history load and active session highlight without full page reload.
- **`SkillSessionPanel`**: Session list sidebar with `aria-current`, loading skeleton, empty state, and manual relative time formatting (date-fns not available).
- **`ChatMessageList` updates**: Renders `initialMessages` above live messages with "Earlier in this conversation" section divider. History fetch failure shows an inline notice without blocking chat.

### Files Changed
- **New**: `backend/protocols/sessions_route.py`, `backend/tests/unit/test_session_messages_route.py`, `backend/tests/unit/test_skill_sessions_route.py`
- **New**: `frontend/src/hooks/useSkillSessions.ts`, `frontend/src/hooks/useSessionMessages.ts`
- **New**: `frontend/src/hooks/__tests__/useSkillSessions.test.ts`, `frontend/src/hooks/__tests__/useSessionMessages.test.ts`
- **New**: `frontend/src/components/chat/SkillSessionPanel.tsx`, `frontend/src/components/chat/__tests__/SkillSessionPanel.test.tsx`
- **Modified**: `backend/adk/agui.py` (APP_NAME export), `backend/adk/session.py` (singleton + test reset), `backend/skills/skill_processor.py` (use shared singleton), `backend/skills/routes.py` (sessions endpoint), `backend/db/chat_sessions.py` (list_sessions_for_skill), `backend/tests/unit/test_session_factories.py` (reset in teardown)
- **Modified**: `frontend/src/app/chat/[skillId]/page.tsx` (session routing + panel), `frontend/src/components/chat/ChatMessageList.tsx` (history rendering)

### Lessons Learned
- FastAPI `Depends()` captures the dependency reference at route registration; `patch()` won't intercept it. Must use `app.dependency_overrides[dep_fn] = lambda: stub` in tests.
- Module-level singleton services need explicit reset hooks (`_reset_service_for_tests()`) to avoid cross-test contamination when the module is shared across test cases.
- `date-fns` is not installed in the frontend; a small manual relative time helper is sufficient and avoids adding a dependency for a single use case.
