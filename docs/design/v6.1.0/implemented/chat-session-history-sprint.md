# Chat Session History — Sprint Plan

**Sprint ID**: CHAT-SESSION-HISTORY
**Design Doc**: [chat-session-history.md](chat-session-history.md)
**Status**: Implemented
**Duration**: 2.5 days
**Scope**: Fullstack

---

## Sprint Summary

Make chat history visible and resumable. Three problems in one sprint:

1. **Root cause fix** — `skill_processor.py` uses its own `InMemorySessionService()` instead of the shared `get_session_service()`. One-line change; makes sessions durable in prod without any other code change.
2. **Two new REST endpoints** — `GET /api/sessions/{id}/messages` (ADK event log → chat messages) and `GET /api/skills/{skillId}/sessions` (Firestore index lookup).
3. **Frontend session UI** — session list panel, `?session=` routing, history seeding into `ChatMessageList`.

**Key constraint:** The `app_name` used when reading sessions must match the one used when writing them. `build_agui_adk_agent()` in `adk/agui.py` uses `_DEFAULT_APP_NAME = "aitana_platform"`. The messages endpoint must use the same constant — no guessing.

---

## Velocity Baseline

From last 7 days: 107 commits, 292 files, ~38k insertions. Recent targeted feature sprints ran 150–250 LOC/day of implementation + tests. Total estimated LOC this sprint: ~515.

---

## Milestones

### M1 — Backend: session service fix + new endpoints (~1 day)

**Scope**: Backend only

#### Tasks

1. **Fix `skill_processor.py`** (~5 LOC)
   - Replace `_session_service = InMemorySessionService()` with `_session_service = get_session_service()`
   - Remove now-unused `InMemorySessionService` import
   - Remove the `TODO(1A.4)` comment block — it's done

2. **Export `APP_NAME` from `adk/agui.py`** (~2 LOC)
   - Rename `_DEFAULT_APP_NAME` to `APP_NAME` (make it public)
   - Update `build_agui_adk_agent()` internal reference

3. **Add `list_sessions_for_skill()` to `db/chat_sessions.py`** (~35 LOC)
   - Filter `chat_sessions` collection by `skillId + ownerUid`, newest first
   - Same cursor-based pagination as `list_sessions_for_document()`
   - Include only sessions where `archivedAt` is None

4. **Add `GET /api/sessions/{sessionId}/messages` to `protocols/sessions_route.py`** (~65 LOC)
   - Auth: `is_owner()` gate — 403 for non-owners (not 404, avoids existence leak)
   - Fetch `ChatSessionIndex` → get `owner_uid`
   - Call `session_service.get_session(APP_NAME, owner_uid, session_id)`
   - Filter + map events via `_events_to_messages()` helper
   - Return `{messages: list[ChatMessage], session_id: str}`

5. **Add `GET /api/skills/{skillId}/sessions` to `skills/routes.py`** (~40 LOC)
   - Auth: `get_current_user`
   - Call `list_sessions_for_skill(skill_id, caller_uid)`
   - Return `ListSessionsResponse` (same shape as document sessions)

6. **Tests** (~75 LOC)
   - `tests/unit/test_session_messages_route.py` — owner fetch returns messages in order, non-owner → 403, empty session → `[]`, tool events filtered, text events included
   - `tests/unit/test_skill_sessions_route.py` — returns caller's sessions, excludes other users

**Estimated LOC**: ~222 (impl ~147 + tests ~75)

#### Acceptance Criteria
- [ ] `skill_processor.py` no longer instantiates `InMemorySessionService` directly
- [ ] `APP_NAME` exported from `adk/agui.py` and used consistently
- [ ] `GET /api/sessions/{sessionId}/messages` returns `200` for owner, `403` for non-owner
- [ ] `GET /api/sessions/{sessionId}/messages` returns chronologically ordered messages
- [ ] `GET /api/skills/{skillId}/sessions` returns caller's sessions only
- [ ] All new backend tests pass

---

### M2 — Frontend: hooks + routing (~1 day)

**Scope**: Frontend only

#### Tasks

1. **`useSkillSessions.ts`** (~40 LOC)
   - SWR fetch for `GET /api/proxy/skills/{skillId}/sessions`
   - Auto-refresh on window focus (SWR `revalidateOnFocus: true`)
   - Returns `{ sessions, isLoading, error }`

2. **`useSessionMessages.ts`** (~35 LOC)
   - Fetch `GET /api/proxy/sessions/{sessionId}/messages` on `sessionId` change
   - Converts response to `SkillMessage[]` (same type as `useSkillAgent`)
   - Returns `{ initialMessages, isLoadingHistory, historyError }`
   - Skips fetch when `sessionId` is null

3. **`?session=` query param routing in chat page** (~35 LOC)
   - Read `?session=` from URL on mount via `useSearchParams`
   - `setSessionId` on session click (updates state + URL without full navigation)
   - Pass `sessionId` in the SSE stream body for `process_skill_request`

4. **Tests** (~65 LOC)
   - `useSkillSessions.test.ts` — fetches correct endpoint, returns sessions array, handles error
   - `useSessionMessages.test.ts` — fetches on sessionId change, skips when null, returns `SkillMessage[]`

**Estimated LOC**: ~175 (impl ~110 + tests ~65)

#### Acceptance Criteria
- [ ] `useSkillSessions` fetches `GET /api/proxy/skills/{skillId}/sessions`
- [ ] `useSessionMessages` fetches messages only when `sessionId` is non-null
- [ ] `useSessionMessages` returns `SkillMessage[]` in correct shape
- [ ] `?session=abc` in URL causes the correct session to be loaded on mount
- [ ] All hook tests pass

---

### M3 — Frontend: session panel + history seeding (~0.5 day)

**Scope**: Frontend only

#### Tasks

1. **`SkillSessionPanel.tsx`** (~80 LOC)
   - Scrollable list of past sessions: title + relative timestamp
   - Highlights active session
   - Click triggers `setSessionId` callback
   - Shows loading skeleton while `useSkillSessions` is loading

2. **`ChatMessageList` — `initialMessages` prop** (~25 LOC)
   - Accept optional `initialMessages?: SkillMessage[]`
   - Render them above live messages with a `"Earlier in this conversation"` section divider
   - Visually distinct: slightly muted header row, same bubble components

3. **History error handling** (~15 LOC)
   - If history fetch fails → open in fresh-session mode
   - Inline notice in `ChatMessageList`: "Couldn't load previous messages — starting fresh."

4. **Tests** (~40 LOC)
   - `SkillSessionPanel.test.tsx` — renders session list, highlights active, fires callback on click
   - `ChatMessageList` initialMessages test — section divider appears, messages above live

**Estimated LOC**: ~160 (impl ~120 + tests ~40)

#### Acceptance Criteria
- [ ] `SkillSessionPanel` renders past sessions with titles and timestamps
- [ ] Clicking a session in the panel calls `setSessionId`
- [ ] Active session is visually distinguished
- [ ] `ChatMessageList` renders `initialMessages` above live messages with a section divider
- [ ] History fetch failure shows inline notice, does not block the chat
- [ ] All component tests pass

---

## Day-by-Day Plan

| Day | Morning | Afternoon |
|-----|---------|-----------|
| Day 1 | M1 tasks 1–3: skill_processor fix, APP_NAME, list_sessions_for_skill | M1 tasks 4–6: two endpoints + tests |
| Day 2 | M2 tasks 1–3: useSkillSessions, useSessionMessages, session routing | M2 task 4: hook tests |
| Day 2.5 | M3: SkillSessionPanel + initialMessages + error handling + tests | Manual test: send 3 msgs, refresh, see history; test non-owner 403 |

---

## Quality Gates

After **M1**: `cd backend && make test-fast`
After **M2**: `cd frontend && npm run quality:check:fast && npm run test:run`
After **M3**: Full suite — `npm run docker:check` + `cd backend && make test`

---

## Manual Test Checklist

- [ ] Send 3 messages to a skill; refresh the page → chat panel shows prior messages
- [ ] Click a past session in the sidebar → panel loads its messages
- [ ] Send a follow-up after loading history → model has correct context
- [ ] Non-owner visiting session URL → 403 handled gracefully (no crash)
- [ ] With `AGENT_ENGINE_ID` unset → in-memory; history lost on server restart (expected)
- [ ] With `AGENT_ENGINE_ID` set → sessions persist across restarts

---

## Open Questions (from design doc)

1. **`app_name` consistency**: `build_agui_adk_agent()` uses `_DEFAULT_APP_NAME = "aitana_platform"`. The `GET /sessions/{id}/messages` handler must import and use the same constant. Verify in M1 with a unit test that compares what the session service was called with.

2. **Agent Engine session ID format**: Confirm that the `thread-{hex}` IDs stored in `ChatSessionIndex` match the session IDs in Agent Engine. `skill_processor.py` generates `thread_id = session_id or f"thread-{uuid.uuid4().hex[:12]}"` and passes it to `RunAgentInput.threadId`. The AG-UI adapter maps `threadId` → ADK `session_id`. Test this path in M1 integration test before building the messages endpoint on top.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `app_name` mismatch between writer and reader | Medium | Export `APP_NAME` constant; unit test proves it |
| Agent Engine session IDs differ from `thread-{hex}` format | Low | Check `build_agui_adk_agent` source; add integration test |
| `VertexAiSessionService.get_session()` slow in prod | Low | Cache at SWR layer (60s stale-while-revalidate) |
| `HttpAgent` seeding via `agent.addMessage()` breaks AG-UI continuity | Low | Deferred to post-M3 if needed — UI seeding is sufficient for blank-screen fix |

---

## Files Created / Modified

### Backend
- `backend/skills/skill_processor.py` — replace `InMemorySessionService()` with `get_session_service()`
- `backend/adk/agui.py` — rename `_DEFAULT_APP_NAME` → `APP_NAME`
- `backend/db/chat_sessions.py` — add `list_sessions_for_skill()`
- `backend/protocols/sessions_route.py` — add `GET /api/sessions/{id}/messages`
- `backend/skills/routes.py` — add `GET /api/skills/{skillId}/sessions`
- `backend/tests/unit/test_session_messages_route.py` — new
- `backend/tests/unit/test_skill_sessions_route.py` — new

### Frontend
- `frontend/src/hooks/useSkillSessions.ts` — new
- `frontend/src/hooks/useSessionMessages.ts` — new
- `frontend/src/components/chat/SkillSessionPanel.tsx` — new
- `frontend/src/components/chat/ChatMessageList.tsx` — add `initialMessages` prop
- `frontend/src/app/chat/[skillId]/page.tsx` — `?session=` param + session routing
- `frontend/src/components/chat/__tests__/SkillSessionPanel.test.tsx` — new
- `frontend/src/hooks/__tests__/useSkillSessions.test.ts` — new
- `frontend/src/hooks/__tests__/useSessionMessages.test.ts` — new

---

## Related Documents

- [chat-session-history.md](chat-session-history.md) — design doc (source of truth)
- [chat-message-rendering.md](chat-message-rendering.md) — `ChatMessageList` + `SkillMessage` type
- [local-dev-cli.md](local-dev-cli.md) — `aitana session list/get` CLI commands (separate sprint)
