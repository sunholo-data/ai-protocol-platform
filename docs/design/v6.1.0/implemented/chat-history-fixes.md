# Chat History Fixes (Hardening Sprint)

**Status**: Planned
**Priority**: P0 (High) — history is currently broken in user-visible ways
**Estimated**: 1 day (4–6 focused hours of work + tests)
**Scope**: Fullstack
**Dependencies**:
  - [chat-history (v6.0.0)](../v6.0.0/implemented/chat-history.md) ✅ — base feature being hardened
  - [chat-session-history (v6.1.0)](implemented/chat-session-history.md) ✅ — `GET /api/sessions/{id}/messages` already wired
**Created**: 2026-04-27
**Last Updated**: 2026-04-27

## Problem Statement

The chat-history feature shipped in v6.0.0 + v6.1.0 (1.8) but has **four real failure modes** discovered in live dev:

1. **Refresh loses history.** A user who reloads the page right after sending the first message of a new session sees an empty chat. Backend logs show `GET /api/sessions/<id>` and `GET /api/sessions/<id>/messages` returning 404.
2. **Within-thread message list flaky.** Messages occasionally disappear mid-stream and reappear after the next render — visible jitter while the agent is still talking.
3. **Document-linked history broken.** Sessions that opened additional documents *after* turn 1 do not show up in those documents' Conversations panel.
4. **Title rename broken.** Auto-generated titles sometimes never appear; manual rename succeeds at the API level but the UI list does not refresh.

**Impact:**
- Affects every user every session — chat history is supposed to be the first feature anyone notices works.
- Tickling against a July 2026 demo: a frozen chat or empty-on-refresh surface is exactly the impression we cannot leave.
- Erodes confidence in ADK as the orchestration layer ("do callbacks even fire?").

**Root causes** (verified by end-to-end trace, see [Diagnostic Findings](#diagnostic-findings)):

| # | Failure | Root cause | File:Line |
|---|---------|-----------|-----------|
| 1 | Refresh 404 | `create_session_index` runs in `before_agent_callback` *during* turn 1, asynchronously after `POST /stream` returns. Refresh races the write. | [backend/adk/callbacks.py:415](../../../../backend/adk/callbacks.py) |
| 2 | Within-thread flicker | `onMessagesChanged` fires per delta; on stream stutter the in-memory list can transiently shrink and the React re-render shows fewer messages. | [frontend/src/hooks/useSkillAgent.ts:120-129](../../../../frontend/src/hooks/useSkillAgent.ts) |
| 3 | Doc-linked history | `_flush_session_index` syncs `turnCount`, `lastMessageAt`, `title` but **not** `document_ids`. Docs added mid-session never reach the index, so `list_sessions_for_document` `array_contains` query misses them. | [backend/adk/callbacks.py:464-477](../../../../backend/adk/callbacks.py) |
| 4a | Title never set | Auto-title fires once at `turn_count == 2`; if generation returns empty/None it is never retried. | [backend/adk/callbacks.py:503](../../../../backend/adk/callbacks.py) |
| 4b | Rename UI stale | PATCH succeeds; parent does not call `useDocumentSessions().refetch()` afterwards. | [frontend/src/components/document/DocumentHistoryPanel.tsx:131-140](../../../../frontend/src/components/document/DocumentHistoryPanel.tsx) |

**Cross-cutting:** None of these failure modes had a regression test. Every fix in this sprint must ship with one.

## Goals

**Primary goal:** Make chat history bulletproof on the four observed failure modes, with a regression test for each so the sprint cannot quietly unship.

**Success metrics:**
- Refresh-after-first-message returns full history with **zero** 404 retries on the happy path. Up to 1 retry tolerated on a slow Firestore write (defense in depth).
- Within-thread message count is **monotonic non-decreasing** during streaming (the rendered list never shrinks).
- Sessions opened across N documents appear in the Conversations panel for **all N**.
- Auto-title fires on turn 2 *or* turn 5 if turn 2 produced empty; manual rename refreshes the panel within 200 ms.
- New tests: 1 per failure mode, all named `test_<failure_id>_*` for grep-ability. All pass on first run after fix.

**Non-goals:**
- Reworking session-id mapping. The diagnostic confirmed `use_thread_id_as_session_id=True` in [protocols/agui.py:77](../../../../backend/protocols/agui.py) works correctly; URL `?session=<id>` IS the ADK session id. No change needed there.
- Cross-channel session unification (deferred per v6.0.0 chat-history scope notes).
- Forking sessions / `POST /api/sessions/{id}/fork` (still deferred).
- Changing the title model (already changed to `gemini-2.5-flash-lite` in env hardening on 2026-04-27).
- Frontend stream-protocol redesign — bug 2 is patched defensively, the underlying AG-UI client behaviour is upstream.

## Diagnostic Findings

Captured here to prevent the next person from re-deriving them. Full trace in conversation context for the sprint.

- **Session id mapping is correct.** Frontend URL `?session=<uuid>` → AG-UI `RunAgentInput.threadId` → ADK session id (because `use_thread_id_as_session_id=True`). Same id is what `create_session_index` writes. The 404 is a *timing* gap, not an id mismatch.
- **Firestore index *is* the source of truth** for session metadata; the messages live in Vertex Agent Engine sessions and are fetched on-demand via `GET /sessions/{id}/messages`.
- **`add_session_documents` already exists** at [backend/db/chat_sessions.py:71](../../../../backend/db/chat_sessions.py) and uses `firestore.ArrayUnion`. The bug is that it's only called by `make_document_loader` mid-turn; the index *flush* path doesn't invoke it. Fix is wiring, not new code.
- **Title generation already retries** at every turn that's a multiple of `_TURN_FLUSH_INTERVAL` for non-title fields, but the title condition `turn_count == 2` exact-equals — so if turn 2 produces empty, no later turn fixes it.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Synchronous index write adds ~50ms before first SSE event. Net neutral — losing history feels worse than the negligible startup hit, but it's not a perceived speed win either. |
| 2 | EARNED TRUST | +1 | Fixes a class of "did the chat just lose my work" trust failures. Tests lock the fix. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure, invisible to skill authors. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing changes. |
| 5 | GRACEFUL DEGRADATION | +1 | Adds explicit retry on transient 404 in `useSessionMessages`; adds dedup in `useSkillAgent` so transient stream stutter cannot shrink the list. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses existing AG-UI + Firestore patterns; no new wire shapes. |
| 7 | API FIRST | 0 | `GET/PATCH /api/sessions/{id}` and `GET /api/documents/{id}/sessions` already API-first. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The sprint's defining requirement is one regression test per failure mode. Tests are observability. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access; same access-control checks remain. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend dedup + refetch are thin-client moves; the heavy logic (synchronous index write, document_ids sync, title retry) all moves *into* the backend. |
| | **Net Score** | **+4** | Threshold met. No -1 scores; no hard-fail risk. |

**Conflict justifications:** none required.

## Design

### Overview

Five surgical changes — three backend, two frontend — each with a paired regression test. No new modules, no schema changes. The chat-history architecture is sound; this sprint hardens its existing surfaces.

### Backend changes

**B1. Synchronous session-index write at stream open** (fixes failure #1)

Move the `create_session_index` call out of the ADK `before_agent_callback` and into [backend/skills/skill_processor.py](../../../../backend/skills/skill_processor.py) — write the row *before* the async generator yields its first event. The session id at that point is `run_input.threadId` (which equals the ADK session id, by the `use_thread_id_as_session_id=True` invariant).

The before-agent callback at [backend/adk/callbacks.py:415](../../../../backend/adk/callbacks.py) becomes idempotent: if the index already exists with a matching `session_id`, skip. This keeps the callback as a fallback for non-`/api/skill/{id}/stream` entry points (CLI, future channels).

**B2. Sync `document_ids` in `_flush_session_index`** (fixes failure #3)

In [backend/adk/callbacks.py:464-477](../../../../backend/adk/callbacks.py), extend the flush to read `state.get("document_ids")` and pass it to `update_session_fields` alongside `turnCount` / `lastMessageAt` / `title`. Use `firestore.ArrayUnion` semantics (already in [backend/db/chat_sessions.py:71](../../../../backend/db/chat_sessions.py) `add_session_documents`) to avoid clobbering docs added by `make_document_loader` between flushes — call `add_session_documents(session_id, document_ids)` from inside `_flush_session_index` rather than including the array in the same `update_session_fields` write.

**B3. Title retry at later turns** (fixes failure #4a)

In [backend/adk/callbacks.py:503](../../../../backend/adk/callbacks.py), change the condition from `turn_count == 2` to `turn_count == 2 or (turn_count >= 4 and not state.get("titleSet"))`. After a successful generation, set `state["titleSet"] = True`. This gives the title generator a richer context window when turn 2 was thin (e.g., one-word user message), without retrying on every flush.

### Frontend changes

**F1. Monotonic message-list dedup** (fixes failure #2)

In [frontend/src/hooks/useSkillAgent.ts:120-129](../../../../frontend/src/hooks/useSkillAgent.ts), wrap the `onMessagesChanged` handler so it only commits a new list when its length is `>=` the previous render's length, using message id as the merge key. If a new render would shrink the list, keep the prior list and log a console warning (defensive — protocol violations should be visible in dev tools, not invisible UI corruption).

**F2. Refetch on rename** (fixes failure #4b)

Thread a `refetch` callback from the parent panel through to [frontend/src/components/document/DocumentHistoryPanel.tsx:131-140](../../../../frontend/src/components/document/DocumentHistoryPanel.tsx). After the PATCH `await fetchWithAuth(...)` resolves, call `refetch()` so `useDocumentSessions` re-pulls the list and the new title appears immediately. Keep the optimistic local-state update too so the rename feels instant — `refetch` is the eventual-consistency reconcile.

### Architecture diagram

```
POST /api/skill/{id}/stream
        │
        ├─[B1: synchronous]── create_session_index(threadId)  → Firestore  ────┐
        │                                                                       │
        ▼                                                                       │
   stream_agui_events ────────────► AG-UI events ──► SSE ──► Frontend          │
        │                                                       │               │
        │                                                       └─[F1: dedup]   │
        │                                                                       │
   on after_agent_callback (every turn):                                        │
        ├─[B2]── add_session_documents(state.document_ids)  ► Firestore  ──────┤
        ├─[B3]── if turn==2 or (turn>=4 and not titleSet):                     │
        │              generate_title_fast() → update_session_fields ─────────┤
        │                                                                      ▼
        └─ update_session_fields(turnCount, lastMessageAt) ─────────► chat_sessions/{id}
                                                                              ▲
GET /api/sessions/{id}/messages ──► Firestore + Vertex Agent Engine sessions ─┤
PATCH /api/sessions/{id}        ──► Firestore (title) ──[F2: refetch]─────────┘
```

### API changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| (none) | — | All four fixes are internal; HTTP surface unchanged. | No |

### Standards compliance

No new schemas, formats, or protocols. All changes operate inside existing AG-UI / ADK / Firestore boundaries. Axiom #6 score: 0 — no opportunity to either align or conflict.

### CLI surface

No new CLI commands. The relevant developer ergonomics (listing sessions, fetching messages) are already covered by the v6.1.0 [local-dev-cli](local-dev-cli.md) `aitana sessions` family. Not in scope here.

## Implementation Plan

### Phase 1 — Backend fixes (~2 hours)
- [ ] **B1 + test_refresh_*** — Move `create_session_index` to synchronous start of `process_skill_request`. Make callback idempotent. Add `tests/api_tests/test_stream_skill.py::test_refresh_finds_session_index_after_first_stream_event` — assert that immediately after the first SSE event yields, `GET /api/sessions/{id}` returns 200 with the expected metadata. (~50 LOC + test)
- [ ] **B2 + test_doc_linked_*** — Extend `_flush_session_index` to call `add_session_documents`. Add `tests/api_tests/test_sessions_route.py::test_list_sessions_for_document_includes_docs_added_after_turn_one` — start a session with doc A, simulate `make_document_loader` adding doc B at turn 2, assert `list_sessions_for_document(B)` returns the session. (~30 LOC + test)
- [ ] **B3 + test_title_retry_*** — Add `turn_count >= 4 and not titleSet` retry path. Add `tests/unit/test_callbacks.py::test_title_regenerates_when_turn_two_returns_empty` — mock `generate_title_fast` to return `None` on first call, real value on second; assert title is set after turn 4 flush. (~20 LOC + test)

### Phase 2 — Frontend fixes (~2 hours)
- [ ] **F1 + test_monotonic_*** — Wrap `onMessagesChanged` with monotonic dedup. Add `frontend/src/hooks/__tests__/useSkillAgent.test.ts::test_message_list_never_shrinks_on_stutter` — feed a sequence of message-array snapshots where one frame has fewer entries than its predecessor; assert the rendered state holds the longer one. (~40 LOC + test)
- [ ] **F2 + test_rename_refetch_*** — Thread `refetch` through `DocumentHistoryPanel`. Add `frontend/src/components/document/__tests__/DocumentHistoryPanel.test.tsx::test_rename_refetches_session_list_on_success` — mock PATCH success; assert `refetch` is invoked exactly once after the await resolves. (~30 LOC + test)

### Phase 3 — Self-verification (~1 hour)
- [ ] Run `cd backend && uv run pytest tests/api_tests tests/unit -q` — full pass.
- [ ] Run `cd frontend && npx vitest run` — full pass.
- [ ] Run `cd backend && uv run ruff check .` — clean.
- [ ] Run `cd frontend && npx tsc --noEmit -p tsconfig.json && npm run lint` — clean.
- [ ] **Manual end-to-end** — start `make dev`, then for each of the four failure modes, reproduce the exact scenario from [Problem Statement](#problem-statement) and confirm it now works. Record the result inline in the Implementation Report after the sprint.

## Migration & Rollout

**Database migrations:** none. All changes write to existing fields/collections. Existing sessions without titles or with stale `documentIds` arrays continue to work; the next turn's flush will reconcile.

**Feature flags:** none. Risk is low enough to ship straight to dev.

**Rollback plan:** revert the sprint commit. No data migrations to undo. Existing sessions remain valid because the schema is unchanged.

**Environment variables:** none new.

## Testing Strategy

The sprint **is** the testing strategy. Every fix above is paired with a regression test. To make this enforceable:

1. **Per-fix tests are the only acceptance gate.** A fix is not done until its test fails before the change and passes after.
2. **Test names match failure ids** — `test_refresh_*`, `test_within_thread_*`, `test_doc_linked_*`, `test_title_*`, `test_rename_*` — so anyone reading the test file can map back to this doc.
3. **Self-verification** is a checklist item in [Phase 3](#phase-3--self-verification-1-hour), not an afterthought. The implementer runs the full test suite and lint before declaring done.

### Backend tests (pytest)
- [ ] `tests/api_tests/test_stream_skill.py::test_refresh_finds_session_index_after_first_stream_event`
- [ ] `tests/api_tests/test_sessions_route.py::test_list_sessions_for_document_includes_docs_added_after_turn_one`
- [ ] `tests/unit/test_callbacks.py::test_title_regenerates_when_turn_two_returns_empty`
- [ ] `tests/unit/test_callbacks.py::test_create_session_index_is_idempotent`

### Frontend tests (Vitest + React Testing Library)
- [ ] `frontend/src/hooks/__tests__/useSkillAgent.test.ts::test_message_list_never_shrinks_on_stutter`
- [ ] `frontend/src/components/document/__tests__/DocumentHistoryPanel.test.tsx::test_rename_refetches_session_list_on_success`

### Manual verification (end-to-end, captured inline in Implementation Report)
- [ ] Open a fresh chat → send one message → reload page → history present.
- [ ] Send 5+ messages rapidly → message list never shrinks visually during streaming.
- [ ] Open doc A → start chat → open doc B mid-session → reload → conversation appears in B's Conversations panel.
- [ ] Send 5+ messages → title appears within turn 2 or turn 5; manual rename in DocumentHistoryPanel updates list within 200 ms.

## Security Considerations

- Synchronous `create_session_index` is gated by the same `Depends(get_current_user)` that already protects `/api/skill/{id}/stream`. The session row is owned by `user.uid` from the auth context — no new trust surface.
- No new fields are exposed to the wire format; access-control checks in `_require_session` and `get_session_messages` are unchanged.

## Performance Considerations

- B1 adds **one Firestore write** before the first SSE event. Measured cost is ~30–80 ms in dev/Cloud Run regional Firestore. The previous async write was the same cost, just deferred — the work isn't new, only the timing.
- B2 adds **one extra Firestore write per flush** (`add_session_documents`) when `document_ids` is non-empty. `_TURN_FLUSH_INTERVAL` is 5 today, so this is at most one extra write every five turns — negligible.
- F1 dedup is O(n) per render where n is the message count for the active thread. Threads >100 messages would still cost <1 ms.
- F2 refetch is one extra HTTP call after a manual rename — invoked at human-action rate.

## Success Criteria

- [ ] All six new tests pass on first `pytest` / `vitest` run after the sprint commit.
- [ ] `cd backend && uv run pytest tests/api_tests tests/unit -q && uv run ruff check .` — clean.
- [ ] `cd frontend && npx vitest run && npx tsc --noEmit -p tsconfig.json && npm run lint` — clean.
- [ ] All four manual end-to-end checks pass and are recorded in the Implementation Report.
- [ ] No new TODOs or `# noqa` introduced.
- [ ] CHANGELOG entry under v6.1.0 noting the four fixes (or sprint-doc Implementation Report — choose one).

## Open Questions

- **Should B1 hard-fail the stream if the Firestore write errors?** Initial answer: yes — fail closed. Better to surface a clean `RUN_ERROR` (the path now exists from the streaming-error-surface sprint) than to silently leave a session unindexed and reproduce the original bug. Confirm during implementation.
- **Should F1 also emit a telemetry event when it suppresses a shrink?** Probably yes once observability for AG-UI client errors is wired (separate sprint). For now, `console.warn` is enough.

## Related Documents

- [Chat History (v6.0.0)](../v6.0.0/implemented/chat-history.md) — base feature
- [Chat Session History (v6.1.0/1.8)](implemented/chat-session-history.md) — `GET /sessions/{id}/messages` + frontend seeding
- [Streaming Error Surface (v6.0.0)](../v6.0.0/implemented/streaming-error-surface.md) — the `RUN_ERROR` event channel B1 will use on Firestore failure
- [Local Dev CLI (v6.1.0)](local-dev-cli.md) — where future `aitana sessions doctor` would live (out of scope here)

---

## Implementation Report

**Sprint executed:** 2026-04-27 (single session, ~2 hours wall clock)
**Commits:**
- M1 backend: `d83b1e4` feat(chat-history): B1+B2+B3 + paired regression tests
- M2 frontend: `6411d1f` feat(chat-history): F1 monotonic message dedup + F2 rename-refetch lock-in

### What landed vs the plan

| Fix | Status | Notes |
|---|---|---|
| **B1** Synchronous index write + idempotent callback | ✅ Implemented | `process_skill_request` writes the row before the SSE stream opens; `_tracker` short-circuits when row already exists. |
| **B2** `documentIds` synced on flush | ✅ Implemented | `_after_response` calls `add_session_documents` on every flush. ArrayUnion semantics handle concurrent doc adds. |
| **B3** Title regenerates if turn 2 was empty | ✅ Implemented | Condition: `turn_count == 2 or (turn_count >= 4 and not state["titleSet"])`. Successful generation sets `titleSet=True`. |
| **F1** Monotonic message-list dedup | ✅ Implemented | `useSkillAgent` holds previous list when `agent.messages` shrinks; `console.warn` makes the protocol stutter visible in dev tools. |
| **F2** Refetch on rename | ✅ Already implemented (test added) | Diagnostic mis-flagged this — `handleRename` already calls `refetch()`. Added regression-locking test. |

### Tests

All six tests verified to behave as specified. Five new (one was a lock-in for already-correct code):

| Test | File | Pre-fix | Post-fix |
|---|---|---|---|
| `test_refresh_finds_session_index_after_first_stream_event` | `tests/api_tests/test_stream_skill.py` | FAIL | PASS |
| `test_create_session_index_is_idempotent` | `tests/unit/test_session_callbacks.py` | FAIL | PASS |
| `test_after_agent_callback_syncs_document_ids_to_firestore_index` | `tests/unit/test_session_callbacks.py` | FAIL | PASS |
| `test_title_regenerates_when_turn_two_returns_empty` | `tests/unit/test_session_callbacks.py` | FAIL | PASS |
| `useSkillAgent — F1: monotonic message list` | `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` | FAIL | PASS |
| `DocumentHistoryPanel — F2: rename refetches on success` | `frontend/src/components/chat/__tests__/DocumentHistoryPanel.test.tsx` | PASS | PASS (lock-in) |

### Quality gates

- Backend: `pytest tests/ -m "not slow"` → **716 passed, 3 skipped, 0 failed** (4 new passes)
- Backend: `ruff check .` → clean
- Frontend: `vitest run` → **266 passed, 0 failed** (2 new passes)
- Frontend: `tsc --noEmit -p tsconfig.json` → clean
- Frontend: `npm run lint` → clean

### Manual end-to-end (to be filled in by Mark on next `make dev` cycle)

| # | Scenario | Result |
|---|----------|--------|
| 1 | Open fresh chat → send 1 message → reload page → history present, 0 retries on happy path | _pending_ |
| 2 | Send 3+ rapid messages → message list never visibly shrinks during streaming | _pending_ |
| 3 | Open doc A → start chat → open doc B mid-session → reload → conversation appears in B's panel | _pending_ |
| 4 | Send 5+ messages → title appears within turn 2 or turn 5 | _pending_ |
| 5 | Manual rename in DocumentHistoryPanel → list updates within 200 ms | _pending_ |

### Out-of-scope follow-ups discovered

- **Title generation is currently blocking** in `after_agent_callback` (~200-500ms gemini-2.5-flash-lite roundtrip on turns 2 and 5). The user has already seen the agent's reply by then but the SSE connection stays open until the callback returns. Making this non-blocking (background task / threadpool) is a separate sprint — not in scope here.
- **`backend/tools/documents/{ailang_parse.py, upload.py}`** had unstaged local changes at sprint start; these were left untouched and are unrelated to chat-history.
- **Today's env/auth/title-model hardening** (`backend/.env.example`, `backend/config/gcp.py`, `backend/db/title_generator.py`, `backend/fast_api_app.py`, `scripts/dev.sh`) remain unstaged at end of sprint — to be committed separately as their own infra-hardening commit.

### Honest deviations from the plan

- **F2 was a false positive.** The original diagnostic claimed the parent didn't call `refetch()` after PATCH. Reading the code showed it *does*. The sprint still gained value here: a regression-locking test that pins the behaviour. No code change.
- **No changes to `tests/api_tests/test_sessions_route.py`.** The B2 test landed in `test_session_callbacks.py` instead — the bug is in the callback flush logic, not at the route boundary, so a unit test there is a more direct lock than a route-level integration test (and avoids needing a Firestore emulator). Sprint plan path was an estimate, not a contract.
