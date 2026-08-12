# Chat History Deep Fixes 2 — `user_id` Triple Inconsistency

**Status**: ✅ Implemented 2026-04-27 (Bugs A', C, E shipped; Bug B' deferred — verified 2026-04-30)
**Priority**: P0 (Critical) — resume is broken for every session; Bug C report: "I can't resume a conversation anywhere — they are all blank"
**Estimated**: TBD post-diagnostic; the failure is now pinned to a single backend ValueError, scope likely <100 LOC + tests
**Scope**: Fullstack (likely backend-heavy)
**Dependencies**:
  - [chat-history-deep-fixes (1.14)](chat-history-deep-fixes.md) ✅ — F1 agent-identity guard shipped; revealed this layer of the bug
**Created**: 2026-04-27
**Last Updated**: 2026-04-30

## Shipped

- **Bugs A'/C** (commit `e3ff7bb`, 2026-04-27) — `build_agui_adk_agent` now accepts `user_id` and `process_skill_request` passes `user.uid` so Vertex's session and Firestore's `owner_uid` agree. `GET /api/sessions/{id}/messages` returns 200 instead of 500 + ValueError.
- **Bug A' visual flicker** (commit `31ddbcd`, Phase 2 follow-up 2026-04-27) — `useStableThreadId` hook pre-allocates a UUID at chat-page mount and only updates on real navigations (different existing thread, "+ New conversation"), not URL writebacks. AGUIProvider's `sessionId` prop is now stable across the post-first-turn writeback → no HttpAgent rebuild → `agent.messages` survives turn 1. 5 paired tests in `useStableThreadId.test.ts`.
- **Bug E** (same commit `e3ff7bb`) — `get_session_messages` now uses `ctx.can_access(idx)` ([sessions_route.py:255](../../../../backend/protocols/sessions_route.py#L255)) instead of `is_owner`. PATCH/DELETE remain owner-gated. Non-owners with valid public/domain/tagged access can now read shared session messages.
- **Bug B'** ("+ New conversation closes doc preview") — DEFERRED at sprint close. D3' diagnostic test was never written; code trace couldn't explain the mechanism. Verification 2026-04-30: not refuted, not re-reported by the user since the fix wave landed. Re-open if it returns. Not blocking.

## Verification (2026-04-30)

Re-checked against current dev while triaging 1.15 status:
- `useStableThreadId.ts` exists; wired into AGUIProvider
- `build_agui_adk_agent(user_id=...)` parameter present in `backend/adk/agui.py:42`
- `sessions_route.py:255` uses `can_access` for read; `:283` uses `is_owner` for PATCH/DELETE
- Both fix commits (`e3ff7bb`, `31ddbcd`) on dev

## Problem Statement

After [1.14](chat-history-deep-fixes.md) shipped (F1 agent-identity guard), three new/refined bugs surfaced in user E2E. Direct quotes:

> "the first message then answer are there, but disappear when I do question 2 and answer 2 then are replacing the first initial pair — but then history of chat progresses normally"
> "I click new conversation under a doc and yes I do get a new conversation, but the document preview also closes which is confusing"
> "I load another skill and I see the conversation thread title, but when I click on it I do not see the conversation thread history — but I see no conversation history at all when I click away — I can't resume a conversation anywhere — they are all blank"
> "I click on an old session to the top left or in a document and I see at top of thread `Couldn't load previous messages — starting fresh`"

### Confirmed root cause for Bugs A' and C (read from running backend logs)

```
INFO:  GET /api/sessions/{id}/messages HTTP/1.1  500 Internal Server Error
File "backend/protocols/sessions_route.py", line 253, in get_session_messages
File ".../google/adk/sessions/vertex_ai_session_service.py", line 194
ValueError: Session {id} does not belong to user uG9Cjk6uMLY7n6drCLGyHrHjik42.
```

**The Firestore `chat_sessions/{id}.owner_uid` does not match the `user_id` Vertex Agent Engine has stored for the same `session_id`.** The route at [sessions_route.py:253](../../../backend/protocols/sessions_route.py#L253) reads `idx.owner_uid` from Firestore and passes it to `session_service.get_session(...)`; Vertex rejects the call because its own user_id for the session is different.

This is exactly the `(app_name, user_id, session_id)` triple inconsistency documented in the [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — known class of issue, never had a regression test.

### Why this presents as Bugs A' and C

- **Bug A' (Q1+A1 disappears when Q2 sent):**
  1. Fresh chat → user sends Q1 → backend B1 ([commit d83b1e4](../../../backend/skills/skill_processor.py)) creates Firestore index with `owner_uid = user.uid`.
  2. ag_ui_adk middleware creates the Vertex session with a *different* `user_id` (the divergence point — see H1 below).
  3. Stream completes; URL writeback rebuilds the HttpAgent (live `agent.messages` resets to `[]`).
  4. Frontend GET `/api/sessions/{id}/messages` → backend queries Vertex with `idx.owner_uid` → ValueError → **500**.
  5. Frontend shows "Couldn't load previous messages — starting fresh" and `initialMessages = []`.
  6. User sends Q2 → live messages = `[Q2, A2]`. Q1+A1 are unreachable (Vertex has them, but under the wrong user_id). Net: turn 1 is lost.

- **Bug C (resume any thread shows blank):** every existing session has the same user_id mismatch, so every GET `/messages` returns 500, every panel shows the error string. There is no path to resume because the route can't read the events.

### Bug B' (separate, frontend-only)

> "I click new conversation under a doc and yes I do get a new conversation, but the document preview also closes"

Suspect: the chat page effect at [page.tsx:175-187](../../../frontend/src/app/chat/[...path]/page.tsx#L175) wires `setOpenTabs(sessionDocTabs)` to `[sessionId, sessionDocTabs]`. When `sessionId` becomes null (handleNewSession) and `useSessionDocuments(null)` returns null/empty tabs, the effect may inadvertently clear visible tabs. Different layer from A'/C — covered by D3' below.

### Bug E (policy gap, NOT a regression — discovered 2026-04-27)

> "we do want the document conversations to be readable if they are intentionally shared via our auth system (group, domain wide, public etc)"

The session-list endpoint ([sessions_route.py:144](../../../backend/protocols/sessions_route.py#L144) `list_sessions_for_document`) returns sessions the caller `can_access` per the [resource-access-control](../v6.0.0/implemented/resource-access-control.md) model — including non-owned sessions that are `public`, `domain`, or shared with a `tagged` group the caller belongs to. So a non-owner sees the *titles* in the Conversations panel.

But the message-read endpoint ([sessions_route.py:249](../../../backend/protocols/sessions_route.py#L249)) is **owner-only**: `if not ctx.is_owner(idx): raise 403`. Result: non-owners see thread titles they cannot open. Even after Bugs A'/C are fixed, this leaves a discoverable-but-unreadable surface that breaks the access-control contract.

**Fix in this sprint:** align `get_session_messages` access policy with `get_session` — replace `is_owner` with `can_access`. The route already passes `idx.owner_uid` to Vertex (regardless of caller), so once the user_id triple is consistent (the primary bug), cross-user reads work without further plumbing. Owner-only writes (PATCH/DELETE) stay owner-gated.

**Impact:** Bugs A' and C are blocking. Bug E is a product correctness issue with the same surface — folding it into this sprint avoids a third round-trip.  **This must ship before any further v6.1.0 work.**

## Goals

**Primary goal:** Make the `(app_name, user_id, session_id)` triple consistent across the writer (`process_skill_request` synchronous index write + ag_ui_adk session creation) and the reader (`get_session_messages`). Add a regression test that locks consistency.

**Success metrics:**
- `GET /api/sessions/{id}/messages` returns 200 with the expected events for any session the caller can access, immediately after a stream completes and after page reload. No more 500s with "does not belong to user".
- Bug C: clicking any thread shows the historical messages — owner OR shared (public / domain / tagged).
- Bug A': turn 1's Q+A persist across the URL writeback / agent rebuild.
- Bug B' (frontend): "+ New conversation" leaves doc tabs untouched.
- Bug E: a non-owner with valid access (`can_access` true) can read messages of a shared session; only PATCH/DELETE remain owner-gated.
- Diagnostic tests committed before any fix; fix-locking tests verified to fail pre-fix and pass post-fix.

**Non-goals:**
- Reworking session id mapping (still 1:1 via `use_thread_id_as_session_id=True`).
- Vertex consistency-window mitigations (no longer suspect — the GET fails synchronously, not lags).
- Migrating existing already-broken sessions in dev. They will remain broken until manually re-created. Production never had this state because production has no users yet.
- Relaxing PATCH (rename) or DELETE access — those stay owner-only. Only the read path joins the access-control model.

## Diagnostic Plan (tests first)

Tests come BEFORE any code change. Each test pins one layer.

### D1' — Confirm/refute H1: writer-side `user_id` divergence

**Hypothesis:** ag_ui_adk's session_manager creates the Vertex session with a `user_id` derived from request state (e.g. extracted from `x-user-id` / `x-firebase-uid` headers, or a default like `"user"`), while my synchronous `_ensure_session_index` writes Firestore with `user.uid` from the Firebase auth dependency. The two paths use different sources.

**Test:** `backend/tests/api_tests/test_session_user_id_consistency.py::test_firestore_owner_uid_matches_vertex_user_id_after_first_turn`

```python
# Patch VertexAiSessionService.create_session to capture the user_id
# argument it receives. Run a stream end-to-end (mocked ADKAgent.run).
# Assert: the user_id Vertex was called with EQUALS the Firestore index's
# owner_uid for the same session_id.
```

If the test fails, H1 is confirmed and we have the exact divergence point.

### D2' — Confirm: GET `/messages` for an existing-but-mismatched session returns 500

**Test:** `backend/tests/api_tests/test_sessions_route.py::test_get_session_messages_returns_500_when_user_id_mismatch`

```python
# Seed a Firestore index with owner_uid="A".
# Mock VertexAiSessionService.get_session to raise the exact ValueError
# from the production log: "Session ... does not belong to user A.".
# Call GET /api/sessions/{id}/messages.
# Assert: response is 500. Lock-in this is the failure mode we're fixing.
```

This test will pass on current main (it documents the bug). The fix should add a separate "happy path" test that asserts 200 + correct events when the triple is consistent.

### D3' — Pin Bug B': handleNewSession should not clear doc tabs

**Test:** `frontend/src/app/chat/__tests__/chat-page-new-session-tabs.test.tsx::test_new_session_button_preserves_open_doc_tabs`

```typescript
// Render <ChatPage /> with ?session=existing&doc=A&doc=B.
// Wait for the doc tabs to mount.
// Click + New conversation in DocumentHistoryPanel.
// Assert: URL ?session= is gone but the tab strip still shows doc A and B.
```

If pre-fix the tabs disappear, the bug's mechanism is in either `useSessionDocuments(null)` returning empty tabs or the page-level `useEffect([sessionId, sessionDocTabs])` clearing state.

### D4' — (after D1' identifies divergence) lock the user_id consistency fix

**Test name TBD** — depends on where D1' shows the divergence:
- If ag_ui_adk uses headers: lock that Aitana sends `x-firebase-uid` matching `user.uid` and that the route reads from the same source.
- If ag_ui_adk uses a default user: lock that we override it with the authenticated uid.
- If `_ensure_session_index` reads the wrong field: align it.

### D5' — Bug E: shared-session message read

**Test:** `backend/tests/api_tests/test_sessions_route.py::test_get_session_messages_allows_non_owner_with_can_access`

```python
# Seed Firestore with a session: owner_uid="alice", access_control={"type": "public"}.
# Mock VertexAiSessionService.get_session to return events under user_id="alice"
# (assuming the user_id triple consistency fix has landed).
# Call GET /api/sessions/{id}/messages as user "bob" (a non-owner with can_access).
# Assert: 200 + the expected events (Vertex query used user_id="alice", not "bob").
# Then: same setup but access_control={"type": "private"}.
# Assert: 403 (still locked for private sessions).
```

This test fails on current main (route is `is_owner`-only → 403 for the public case). After the fix it passes.

## Hypotheses

| Id | Layer | Hypothesis | Diagnostic |
|---|---|---|---|
| H1 | backend writer | ag_ui_adk creates Vertex session with `user_id` ≠ Firestore `owner_uid` (Bug A'/C primary cause) | D1' |
| H2 | backend reader | `get_session_messages` queries Vertex with the wrong field — refuted by stack trace (it correctly uses `idx.owner_uid`); the bug is on the writer side | (refuted by log) |
| H3 | frontend | "+ New conversation" page-level effect clears `openTabs` (Bug B') | D3' |
| H4 | policy | `get_session_messages` is owner-only despite `list_sessions_for_document` returning shared sessions to non-owners (Bug E) | D5' |

## Diagnostic Findings (Phase 1, 2026-04-27)

| Test | File | Result | Conclusion |
|---|---|---|---|
| **D1'** `test_default_user_id_extractor_diverges_from_firebase_uid` | `tests/unit/test_agui.py` | **PASSES** — `wrapped._get_user_id(input)` returns `"thread_user_thread-abc-123"` | **H1 CONFIRMED**. ag_ui_adk's [`_default_user_extractor` at adk_agent.py:451-454](../../../backend/.venv/lib/python3.12/site-packages/ag_ui_adk/adk_agent.py#L451) uses `f"thread_user_{thread_id}"` because `build_agui_adk_agent` doesn't pass `user_id`. This is exactly what gets stored as Vertex's session `user_id`, while Firestore stores `user.uid` — the divergence point is `backend/adk/agui.py:74-83`. |
| **D2'** `test_d2_returns_500_when_vertex_user_id_mismatch` | `tests/api_tests/test_sessions_route.py` | **PASSES** — route returns 500 when Vertex raises the documented ValueError | Locks in current failure mode. After the fix, this test should be replaced with a positive case (200 + events). |
| **D5'** `test_d5_non_owner_with_can_access_currently_403s_bug_e` | `tests/api_tests/test_sessions_route.py` | **PASSES** — non-owner with public-access gets 403 today | **H4 CONFIRMED**. Bug E is real. After the fix, this test inverts (assert 200) and a new test asserts `private` still 403s. |
| **D3'** Bug B' | (not written) | **DEFERRED** — code trace cannot explain how `openTabs` would be cleared. `handleNewSession` only mutates URL; page-level syncing effect early-returns on null `sessionId`; `useSessionDocuments(null)` sets its OWN `tabs` to null but the page only reads `sessionDocTabs` to assign `openTabs` (no clearing on null). User report may be about the document preview pane visibility (different surface) or a misread. **Re-validate in E2E after the user_id fix lands.** |

**Root cause pinned (single fix path for Bugs A'/C):** `build_agui_adk_agent` does not thread an explicit `user_id`, so ag_ui_adk falls back to `f"thread_user_{thread_id}"`. Fix: accept `user_id` in `build_agui_adk_agent` and pass it from `process_skill_request` as `user.uid`. Vertex's session and Firestore's `owner_uid` will then agree.

**Bug E is independent** and lands in the same commit: replace `is_owner` with `can_access` on the message-read endpoint.

**Bug B' deferred** — needs more E2E signal before committing to a fix.

### Phase 2 follow-up (2026-04-27): Bug A' visual flicker

E2E after the user_id consistency fix (commit e3ff7bb) confirmed Bugs C and B' fixed, but Bug A' surfaced as a *visual flicker*:

> "I say hi - the hi disappears for a while then I see it and the reply from ai in chat. I saw whats on? and it doesn't appear, then replaces after a while with its answer. e.g. exactly as it was before."

The user_id fix made the `GET /api/sessions/{id}/messages` succeed, so the missing turn 1 now reappears via `initialMessages` after the round-trip. The flicker is the gap between "live messages cleared on agent rebuild" (F1 yields, by design) and "initialMessages populated from the GET". This is exactly H1 from sprint 1.14 ("URL writeback rebuilds the HttpAgent") which was deferred.

**Fix:** new `useStableThreadId(urlSessionId)` hook ([frontend/src/hooks/useStableThreadId.ts](../../../frontend/src/hooks/useStableThreadId.ts)). Pre-allocates a UUID at chat-page mount and only updates when the URL change is a *navigation* (different existing thread or "+ New conversation"), not a *writeback* (URL catching up to our id). AGUIProvider's `sessionId` prop is now stable across the post-first-turn writeback → no HttpAgent rebuild → `agent.messages` survives turn 1 → no flicker.

Five paired tests in `useStableThreadId.test.ts` cover initial mount (with/without session), URL writeback (stays stable — the central assertion), thread resume (adopts new id), and "+ New conversation" (mints fresh id).

Verified: 274 frontend tests pass (5 new), tsc + lint clean.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Resume becomes possible at all; no more "starting fresh" red banner. |
| 2 | EARNED TRUST | +1 | Closes the "I can't resume any conversation" trust failure. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Replaces a silent 500 with a deterministic 200 + events. Adds a regression test for the triple invariant — future writers can't quietly diverge again. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Existing patterns. |
| 7 | API FIRST | 0 | Endpoint surface unchanged. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Diagnostic tests pin the divergence point in code; the regression test prevents recurrence. |
| 9 | SECURE BY CONSTRUCTION | +1 | Aligning user_id closes a class of "wrong session served to caller" bugs. The current 500 is the safe failure mode — better that than serving someone else's events — but this fix removes the failure entirely. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend fix; frontend is largely unchanged. |
| | **Net Score** | **+5** | Threshold met. No -1s. No hard-fail risk. |

## Design (after diagnostic outcome)

The fix shape depends on D1's outcome. Most likely cases:

### If ag_ui_adk uses a header (e.g. `x-firebase-uid`)

[backend/protocols/agui.py:84](../../../backend/protocols/agui.py#L84) already declares `extract_headers=["x-user-id", "x-firebase-uid"]`. Verify Aitana actually sends `x-firebase-uid: <user.uid>` on the SSE request. The frontend AGUIProvider currently sends only `Authorization: Bearer …`. **Likely fix:** add `x-firebase-uid: <user.uid>` to AGUIProvider headers, OR override the user_id extraction in ag_ui_adk's `make_extract_headers` so the `Authorization` Bearer is decoded server-side and the Firebase uid extracted from the verified token. The latter is more secure (header is forgeable).

### If ag_ui_adk falls back to a default user

Override the default by passing `user_id=user.uid` explicitly into the ADKAgent.run() call from `process_skill_request`. Verify the API surface in `@ag-ui/server` and ADK's session-service contract.

### If `_ensure_session_index` was the wrong source

Re-read `user.uid` from the same place ag_ui_adk does, so Firestore matches Vertex.

### Bug B' fix (independent)

Investigate D3' result. Likely: `setOpenTabs(sessionDocTabs)` should not run when `sessionId` becomes null; OR `handleNewSession` should explicitly NOT touch tabs (current code doesn't, but an effect downstream might).

## Implementation Plan

### Phase 1 — Diagnostics (~1h)
- [ ] **D1'** — `test_firestore_owner_uid_matches_vertex_user_id_after_first_turn` (backend api_tests)
- [ ] **D2'** — `test_get_session_messages_returns_500_when_user_id_mismatch` (backend api_tests)
- [ ] **D3'** — `chat-page-new-session-tabs.test.tsx::test_new_session_button_preserves_open_doc_tabs` (frontend)
- [ ] **D5'** — `test_get_session_messages_allows_non_owner_with_can_access` (backend api_tests; will fail pre-fix because route is owner-only)
- [ ] **Capture findings** — append a "Diagnostic Findings" section to this doc with the exact divergence point in code.

### Phase 2 — Fix (gated by D1', D3' outcome)
- [ ] **D4'** — fix-locking test pinning the consistency invariant (writer and reader agree on user_id).
- [ ] Implement the user_id consistency fix surface identified by D1'.
- [ ] Implement Bug E fix: replace `is_owner` with `can_access` in `get_session_messages` (PATCH/DELETE stay owner-gated).
- [ ] Implement Bug B' fix from D3'.
- [ ] Re-run all of D1', D2', D3', D5' — green.
- [ ] Re-run full backend + frontend test suites + lint + typecheck.

### Phase 3 — E2E
- [ ] Restart `make dev` (so the user is on a clean process with new code).
- [ ] **Manual repro of Bug C:** click a thread → assert messages render.
- [ ] **Manual repro of Bug A':** send Q1+A1, send Q2 → assert all four messages stay visible.
- [ ] **Manual repro of Bug B':** open doc tabs, click "+ New conversation" → assert tabs persist.
- [ ] Append Implementation Report to this doc with results recorded inline.

## Migration & Rollout

**Existing dev sessions:** already mismatched, will remain broken. Acceptable — this is a fresh dev environment. **Production:** has no live users yet (v6 still in pre-cutover); no migration needed.

**Feature flags:** none. **Rollback:** revert sprint commit.

## Testing Strategy

Same diagnostic-first discipline as 1.14:
1. D1', D2', D3' run and findings are captured BEFORE any fix.
2. D4' (the fix-locking test) verified to fail pre-fix and pass post-fix.
3. Manual E2E for the three bugs is non-optional and recorded inline.

## Security Considerations

- **Read access widens from `is_owner` to `can_access`** — by design (Bug E fix). This aligns with the [resource-access-control](../v6.0.0/implemented/resource-access-control.md) model: a session's AccessControl (`private`/`public`/`domain`/`tagged`/`specific`) governs both metadata visibility (already wired) and message visibility (this sprint). Private stays private; shared stays shared.
- **Write access stays owner-only.** PATCH (rename, re-scope, archive) and DELETE retain the `ctx.is_owner(idx)` check. Only readers join the access-control model.
- **Vertex query always uses `idx.owner_uid`** regardless of caller — Vertex Agent Engine sessions are namespaced by user_id, and the session was created under the owner. A non-owner reader does NOT get the session attributed to themselves; they read the owner's events. That's the intended product behavior (sharing).
- If the user_id consistency fix path involves trusting an `x-firebase-uid` header, it MUST come from the verified Firebase token (same path `Depends(get_current_user)` uses), not from a client-supplied header value.
- The current 500 is a safe failure mode (better than serving someone else's events). The fix preserves the security invariant — once the user_id triple is consistent, callers only get sessions where Vertex's stored user_id matches the queried user_id, AND the caller passes `can_access`.

## Performance Considerations

No expected impact. The route already does the same number of Firestore + Vertex calls; we're just making them succeed.

## Success Criteria

- [ ] Three diagnostic tests committed and run; findings captured in this doc.
- [ ] Fix-locking test (D4') committed; verified to fail pre-fix and pass post-fix.
- [ ] Full backend + frontend test suites green.
- [ ] Lint + typecheck clean.
- [ ] All three E2E scenarios pass; recorded in Implementation Report.
- [ ] No more `ValueError: Session ... does not belong to user` in `.dev-logs/backend.log` after the fix.

## Open Questions

- **What value DOES ag_ui_adk pass as `user_id` to VertexAiSessionService.create_session?** D1' will tell us. Until D1' runs, all fix shapes are speculative.
- **Should pre-existing broken sessions in dev be cleaned up?** Probably yes — a one-shot script that scans Firestore for sessions whose Vertex partner has a mismatched user_id and deletes the Firestore index, so the user gets clean panels. Decide post-fix.

## Related Documents

- [chat-history-fixes (1.13)](chat-history-fixes.md) — original B1 synchronous index write (the writer side that may diverge)
- [chat-history-deep-fixes (1.14)](chat-history-deep-fixes.md) — F1 agent-identity guard; exposed this layer
- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — documents the (app_name, user_id, session_id) triple invariant
- [feedback memory: tests + self-verify](../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/feedback_test_and_self_verify.md) — the discipline this sprint operates under
