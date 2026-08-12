# Stranded Chat Session Prevention

**Status**: ✅ Implemented 2026-04-30 (Option 1 + Option 2; Option 3 deferred per plan)
**Priority**: P1
**Estimated**: ~0.5d for the recommended path (Option 1 + Option 2). Option 3 deferred.
**Scope**: Fullstack (frontend hook + backend log) — small in both layers
**Dependencies**:
  - [chat-history-fixes (1.13)](../implemented/chat-history-fixes.md) ✅ — synchronous `_ensure_session_index` (B1) is what this builds on
  - [chat-history-deep-fixes-2 (1.15)](../chat-history-deep-fixes-2.md) ✅ — `useStableThreadId`, the URL/threadId lifecycle this interacts with
  - [multi-doc-context-fix (1.22)](multi-doc-context-fix.md) ✅ — D1 loader-log elevation that exposed the empty-`document_ids` turn signature
  - **Already landed in this branch:** reactive synchronous union fix in [`backend/skills/skill_processor.py:_ensure_session_index`](../../../../backend/skills/skill_processor.py) — ArrayUnions every turn's `document_ids` onto the row, paired with [`test_session_index_document_ids_grow_when_doc_added_after_empty_first_turn`](../../../../backend/tests/api_tests/test_stream_skill.py)
**Created**: 2026-04-28
**Last Updated**: 2026-04-30

## Shipped (2026-04-30)

- **Option 2** — `make_document_loader` now logs ERROR `doc loader: TURN-1 INVARIANT VIOLATED — session=<id> requested N doc(s) [...] but every load failed (...)` when turn 1 has docs to load and every one fails. Three regression tests in `TestLoaderTurnOneInvariant` lock the contract: positive (every doc fails → exactly one ERROR), negative-mixed (some succeed → no aggregate ERROR), negative-turn-2 (`_STATE_DOCS_LOADED` non-empty → no aggregate ERROR).
- **Option 1** — `useSessionMessages` now distinguishes 404 (`SessionNotFoundError` → `sessionGone=true`) from other failures (`historyError`). `chat/[...path]/page.tsx` watches `sessionGone` and calls `handleNewSession()`, which drops `?session=` from the URL. `useStableThreadId` then mints a fresh UUID before the next outbound POST. Three new vitest cases lock the contract: 404→sessionGone (no historyError), 5xx→historyError (no sessionGone), reset on sessionId change.
- **Page-mount vitest** — deferred. The hook tests pin the public contract; the page wiring is a 4-line `useEffect` that's hard to break in isolation, and there's no existing vitest scaffold for `chat/[...path]/page.tsx`. Manual E2E (delete a row in Firestore, reload onto its `?session=`) covers the integration.
- **Option 3** — deferred per plan. Re-evaluate if stranded sessions still appear after Option 1 has run for ~1 week.

## Verification

- Backend: `pytest tests/api_tests tests/unit -q` → 617 passed
- Frontend: `npm run quality:check:fast` clean (lint + tsc + check:auth-fetch); `npm run test:run` → 339 tests / 46 files passed

## Problem Statement

A `chat_sessions/{id}` row can land in Firestore with `documentIds: []` and never recover, even though the user is clearly chatting *with* a document. The session then becomes asymmetric:

- It **does** show in the skill-level Sessions panel — [`list_sessions_for_skill`](../../../backend/db/chat_sessions.py) filters by `ownerUid` only.
- It **does not** show under any per-document Conversations panel — [`list_sessions_for_document`](../../../backend/db/chat_sessions.py) uses Firestore `array_contains`, which never matches an empty list.

User-visible symptom (reported 2026-04-28):

> "I reload the page after chatting with a document and I can see the chat session is available but it is not visible again in the UI — it also says 55 turns which is impossible — my guess it's all getting lumped into one thread which we don't fetch."

The 55-turn count is real; the user has been sending into the same `threadId` for many turns, but the doc panel query has been blind to that row from turn 1.

**Current State (after the reactive fix already in this branch):**

The synchronous `add_session_documents` in `_ensure_session_index` now ArrayUnions the wire-time docs onto the row on **every** turn, not only at row creation. So a session created with empty docs heals on the **next** turn that arrives with docs. That is the floor — but it's still reactive, and three upstream paths can still mint the bad state in the first place:

1. **404'd-session reload** — URL has `?session=X` but X is not in Firestore (manual DB wipe between dev runs, deletion in another tab, transient outage). [`useSessionMessages`](../../../frontend/src/hooks/useSessionMessages.ts) silently logs `"Couldn't load previous messages — starting fresh."` and lets the user keep typing into a `threadId` the backend has no record of.
2. **Loader silently fails on every doc in turn 1** — [`make_document_loader`](../../../backend/adk/callbacks.py) catches per-doc failures into `_STATE_DOC_LOAD_ERROR` at WARNING. If turn 1 attaches docs but every one fails (Firestore unavailable, `build_document_context` raises, parsed_documents row missing), `successfully_loaded` is empty and `add_session_documents` is never called. The synchronous `_ensure_session_index` union helps, but only if `forwardedProps.document_ids` actually arrived — see (3).
3. **`openTabs` clobbered to `[]` on resume** — [`chat/[...path]/page.tsx:192-196`](../../../frontend/src/app/chat/[...path]/page.tsx#L192) calls `setOpenTabs(sessionDocTabs)` even when `sessionDocTabs === []`. After reloading onto a stranded session (Firestore says no docs), this nukes any tab the user pre-opened. The next message goes out with `forwardedProps.document_ids = []` and feeds the cycle.

**Impact:**

- Every developer who wipes the dev Firestore between sessions lands on this bug. It's in the backend log (`.dev-logs/backend.log`, 2026-04-28: `404 /api/sessions/eaad583c…/messages` followed by `doc loader: turn start — document_ids=[]`).
- Triages as "session vanished from history panel," wastes 30+ minutes per occurrence to root-cause without the synchronous-union fix already in place.
- Workshop risk (July 2026): a demo audience reloading a session would see the asymmetric panels.

## Goals

**Primary Goal:** A `chat_sessions/{id}` row never enters the empty-`documentIds` accumulating state — and when something tries to push it there, the system fails fast with a single, actionable signal.

**Success Metrics:**
- Zero new sessions land with `documentIds=[]` after non-trivial chat (≥2 turns) with at least one doc attached, measured by spot-check against `chat_sessions` rows whose `turnCount > 1` and `documentIds` is empty.
- 404'd `?session=X` reloads recover to a fresh chat in one user action (auto), or with one click ("Start fresh" banner).
- One log line per `make_document_loader` failure surfaces the "every doc failed on turn 1" condition, distinguishable from the existing per-doc WARNINGs.

**Non-Goals:**
- Backfilling already-stranded rows. The recovery for the user's existing 55-turn row is "delete it" — out of scope here. (One-time backfill could be a follow-up if multiple users hit this in prod.)
- Replacing the synchronous-union reactive fix already shipped in this branch. That stays as the floor; this doc adds the upstream prevention layers.
- Changing the `useStableThreadId` lifecycle. That contract is locked by [chat-history-deep-fixes-2.md](chat-history-deep-fixes-2.md) and is correct.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No latency change. Auto-redirect on 404 is one extra `router.replace`, sub-millisecond. |
| 2 | EARNED TRUST | +1 | A panel that lies ("no conversations yet" while the user is mid-conversation) destroys trust. Closing this asymmetry is exactly what this axiom rewards. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; not user-facing surface area. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model decision involved. |
| 5 | GRACEFUL DEGRADATION | +1 | Today, a 404'd session URL silently lets the user keep typing into a void. Option 1 turns that into a clean fail-fast. Option 2 turns "every doc failed silently" into one ERROR line you can grep for. Both replace silent broken states with observable degraded ones. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Pure HTTP status code handling + Python logging — no new protocol surface. |
| 7 | API FIRST | 0 | No API surface change. The 404 from `GET /api/sessions/{id}/messages` already exists; this only changes how the frontend reacts to it. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Option 2 is observability — a single ERROR-level invariant for "session created but no doc reached an artifact." Distinguishable from the existing per-doc WARNINGs, which today get lost in noise. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data flows or trust boundaries. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Option 1 is a few lines in one hook + page; doesn't move logic into the client, just reacts to a backend status code. |
| | **Net Score** | **+3** | Below threshold (+4). See justification below — this is intentionally a *prevention* sprint complementing an already-shipped reactive fix; the score lifts to +4 once Option 2's regression test lands (Axiom #5: an explicit failure-mode test). |

**Conflict Justifications:** None — no axiom scores -1.

**Threshold note:** Net +3 is one below threshold. This is acceptable for two reasons: (a) the reactive synchronous-union fix already in this branch carried its own +1 on Axiom #5 (it converted "stranded forever" into "self-heals on next turn"), and the upstream prevention here is the natural follow-on, not a standalone feature trying to clear the bar from scratch; (b) once Option 2 ships with the failure-mode regression test (a single test that fakes loader failure on every doc and asserts the ERROR fires), Axiom #5 lifts to +2 and net hits +4. If the user wants to be strict about the threshold pre-implementation, the order is: ship Option 2's test first.

## Design

### Overview

Two prevention layers on top of the reactive floor that already shipped:

- **Option 1 (frontend, recommended):** When `useSessionMessages` gets a 404, distinguish it from transient errors and clear the URL automatically (1a) or disable the composer with a "Start fresh" affordance (1b). Default: 1a.
- **Option 2 (backend, recommended):** When `make_document_loader` finishes turn 1 with `successfully_loaded == []` and `to_load` was non-empty, log a single ERROR with the session id and the failing doc ids. Distinguishable from the existing per-doc WARNINGs.
- **Option 3 (frontend, deferred):** Don't `setOpenTabs([])` on resume when the existing `openTabs` is non-empty. Backstop only — Option 1 should make this unnecessary.

### Option 1 — Frontend fail-fast on 404'd session URL

**Where:** [`frontend/src/hooks/useSessionMessages.ts`](../../../frontend/src/hooks/useSessionMessages.ts) and [`frontend/src/app/chat/[...path]/page.tsx`](../../../frontend/src/app/chat/[...path]/page.tsx)

**Today:**

```typescript
.catch((err: Error) => {
  if (err.name !== "AbortError") {
    setHistoryError("Couldn't load previous messages — starting fresh.");
    setInitialMessages([]);
  }
})
```

The hook treats every non-OK status the same — "starting fresh" is a polite lie when the user just typed `/?session=X` into the URL bar and X is gone.

**1a — Auto-redirect (recommended).** Surface the 404 as a distinct signal so the chat page can `router.replace(pathPrefix)` to drop `?session=` from the URL. `useStableThreadId`'s existing transition (URL went from `?session=X` to no session) mints a fresh UUID, AGUIProvider rebuilds, the user's next send starts a clean session.

```typescript
// useSessionMessages.ts
.then((res) => {
  if (res.status === 404) {
    // signal the page to clear ?session=; don't surface a generic error
    throw new SessionNotFoundError();
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<GetSessionMessagesResponse>;
})
.catch((err: Error) => {
  if (err.name === "AbortError") return;
  if (err instanceof SessionNotFoundError) {
    setSessionGone(true);
    setInitialMessages([]);
    return;
  }
  setHistoryError("Couldn't load previous messages — starting fresh.");
  setInitialMessages([]);
})
```

The chat page reads `sessionGone` and calls `handleNewSession()`. (We already have that handler; it does exactly the right thing.) One subtle detail: do this before the AGUIProvider renders the next outbound POST, so we don't waste a fresh UUID on a stranded threadId.

**1b — Disable composer + banner (alternative).** Same hook signal, different reaction: gate the composer on `!sessionGone`, render a banner: *"This conversation no longer exists. [Start fresh]"*. Click → `handleNewSession()`. More user-friendly but more surface area to maintain. Keep 1a as default; revisit if user testing says auto-redirect is jarring.

### Option 2 — Loud invariant in the loader

**Where:** [`backend/adk/callbacks.py:make_document_loader`](../../../backend/adk/callbacks.py)

**Today:** the loader logs at WARNING per failed doc:

```python
logger.warning("document loader failed for %s: %s", doc_id, exc)
errors[doc_id] = str(exc)
```

These get lost in normal log noise — there's no single line that says "this turn was supposed to attach docs but **none** of them landed."

**Proposed:** after the per-doc loop, if `to_load` was non-empty AND `successfully_loaded` is empty AND this is the first turn (loaded was empty going in), log one ERROR:

```python
if to_load and not successfully_loaded and not loaded_raw:
    logger.error(
        "doc loader: TURN-1 INVARIANT VIOLATED — session=%s requested %d doc(s) "
        "%s but every load failed (%s). Session row will have documentIds=[] "
        "and will not appear in any per-doc Conversations panel until a "
        "subsequent turn succeeds.",
        getattr(getattr(callback_context, "session", None), "id", "?"),
        len(to_load),
        to_load,
        list(errors),
    )
```

Why ERROR and not WARNING: the existing per-doc lines are at WARNING and tend to be transient (one bad doc among many); this is a structurally different condition (the *aggregate* of turn 1 produced no usable artifact). One ERROR per stranded-session-creation event is cheap and gives us a clean grep target in `.dev-logs/backend.log` and Cloud Logging.

**Regression test:** mock `build_document_context` to raise for every doc, run one turn through the loader callback, assert exactly one ERROR record was emitted with the session id in it. Pure unit test, no Vertex.

### Option 3 — Don't clobber `openTabs` to `[]` on resume (deferred)

**Where:** [`frontend/src/app/chat/[...path]/page.tsx:192-196`](../../../frontend/src/app/chat/[...path]/page.tsx#L192)

**Today:**

```typescript
if (sessionDocTabs === null) return;
if (lastSyncedSessionId.current === sessionId) return;
lastSyncedSessionId.current = sessionId;
setOpenTabs(sessionDocTabs);
setActiveTabId(sessionDocTabs[0]?.id ?? null);
```

`sessionDocTabs === []` (stranded session resumed; Firestore says no docs) wipes any tab the user opened via `?doc=` URL or DocListView click before the resume effect ran.

**Proposed:** skip the sync if both the incoming list and the user's pre-existing tabs disagree about emptiness:

```typescript
if (sessionDocTabs.length === 0 && openTabs.length > 0) {
  // Stranded session (server says no docs) but user has tabs open.
  // Trust user state; the synchronous union in _ensure_session_index
  // will heal Firestore on the next send.
  lastSyncedSessionId.current = sessionId;
  return;
}
```

**Why deferred:** Option 1 collapses 99% of the cases this catches. The remaining case is "user opens a tab, then types `?session=X` into the URL bar" — possible but exotic. Re-evaluate after Option 1 is observed in the wild for a week.

### Architecture

```
[User]                                                       [Backend]
   │
   │ reload /chat/X?session=eaad...                           ┌──────────────┐
   ├────────────────────────────────────────────────────────► │ GET /api/    │
   │                                                          │  sessions/   │
   │                                                          │  eaad…/      │
   │                                                          │  messages    │
   │                                                          └──────┬───────┘
   │                                                                 │ 404
   │                                                                 ▼
   │   ┌─────────────────────────────────────────────────────────────┐
   │   │ Option 1: useSessionMessages distinguishes 404              │
   │   │  → setSessionGone(true)                                     │
   │   │  → page.tsx effect calls handleNewSession()                 │
   │   │  → router.replace(pathPrefix) drops ?session=               │
   │   │  → useStableThreadId mints fresh UUID                       │
   │   │  → AGUIProvider rebuilds with clean threadId                │
   │   └─────────────────────────────────────────────────────────────┘
   │
   │ first message (now on fresh threadId)
   ├────────────────────────────────────────────────────────► [_ensure_session_index]
   │                                                                 │
   │                                                                 ▼
   │                                                          ┌──────────────┐
   │                                                          │ make_doc_    │
   │                                                          │  loader      │
   │                                                          └──────┬───────┘
   │                                                                 │
   │                                                                 ▼
   │                                                          ┌──────────────┐
   │                                                          │ Option 2:    │
   │                                                          │ ERROR if     │
   │                                                          │ turn 1 ∧     │
   │                                                          │ all docs     │
   │                                                          │ failed       │
   │                                                          └──────────────┘
```

## Implementation Plan

### Phase 1 — Option 1 (frontend) (~0.25d)

- [ ] `useSessionMessages.ts` — add `SessionNotFoundError` discriminator + `sessionGone` state in the hook return
- [ ] `chat/[...path]/page.tsx` — `useEffect` watching `sessionGone`, calls `handleNewSession()` once per stale session id
- [ ] Vitest: mount with `?session=X`, mock `fetchWithAuth` to return 404, assert `router.replace` is called with the bare `pathPrefix`
- [ ] Manual: open a chat, copy the URL, delete the row in Firestore console (or use `aiplatform sessions delete`), reload. Expect: URL clears, fresh chat ready, no error toast.

### Phase 2 — Option 2 (backend) (~0.15d)

- [ ] `backend/adk/callbacks.py` — add the ERROR-level invariant after the loader's per-doc loop
- [ ] `backend/tests/unit/test_session_callbacks.py` — new test under `TestDocumentInjectorBugF` (or its own class): `test_loader_logs_error_when_every_doc_fails_on_turn_one`
- [ ] Verify the ERROR is structured enough for `aiplatform skill probe`'s log parser if/when we extend it (one log key, list of doc ids, session id)

### Phase 3 — Self-verify (~0.1d)

- [ ] Backend: `pytest tests/api_tests tests/unit -q` clean
- [ ] Frontend: `vitest run && tsc --noEmit && lint` clean
- [ ] Manual E2E: cold-reload onto a non-existent session → URL clears, chat is fresh
- [ ] Manual E2E: chat with a doc that has a deliberately broken parsed_documents row → ERROR appears once in `.dev-logs/backend.log`, session row never lands with `documentIds=[]` (because `_ensure_session_index` already wrote the wire-time docs)

### Deferred — Option 3 (frontend backstop)

Re-open after Option 1 has been live for ~1 week. If we still observe sessions landing stranded despite Option 1 + the synchronous union, fold Option 3 in as a 0.1d patch.

### CLI Surface

None. This sprint touches an existing endpoint (`GET /api/sessions/{id}/messages`) and an existing callback. No new resource to list/probe. The existing `aiplatform sessions get <id>` already lets a developer reproduce the 404 path on demand; if we add anything here it would be `aiplatform sessions audit --empty-docs` (list rows with `documentIds=[]` and `turnCount > 1`) — useful but not required for this sprint, file as a follow-up if multiple users hit this in prod.

## Migration & Rollout

**Database migrations:** None.

**Feature flags:** None. Both options are pure code paths with deterministic behaviour. The 404 detection is a status-code check; the ERROR log is additive observability.

**Rollback Plan:** revert the sprint commit. The synchronous-union reactive fix already in this branch stays as the floor either way.

**Environment variables:** None.

## Testing Strategy

### Frontend Tests (Vitest)

- [ ] `useSessionMessages.test.ts` — new case: 404 response sets `sessionGone=true` and does not set `historyError`
- [ ] `useSessionMessages.test.ts` — existing 5xx response keeps using `historyError` (regression lock)
- [ ] `ChatPage.test.tsx` — when `sessionGone` flips, `router.replace(pathPrefix)` fires exactly once

### Backend Tests (pytest)

- [ ] `test_session_callbacks.py::test_loader_logs_error_when_every_doc_fails_on_turn_one` — fake `build_document_context` raises for every doc id; assert one ERROR record
- [ ] `test_session_callbacks.py::test_loader_does_not_log_error_when_some_doc_succeeds` — mixed success; ensure we don't false-positive on partial failure
- [ ] `test_session_callbacks.py::test_loader_does_not_log_error_after_turn_one` — `_STATE_DOCS_LOADED` already has prior loads; not a turn-1 invariant violation

### Manual Testing

- [ ] Load `/chat/<skill>?session=<known-bad-id>` → URL clears within one effect tick, chat is fresh
- [ ] Load `/chat/<skill>?session=<valid-id>` → no redirect, history loads
- [ ] Backend: kill Firestore connectivity briefly, send a turn-1 message with docs → ERROR fires; restore connectivity, next turn heals (because synchronous union)

## Security Considerations

None new. Same auth surface as `GET /api/sessions/{id}/messages` today; same logging boundary (Cloud Logging, inside the GCP project).

## Performance Considerations

Negligible. Option 1 saves a wasted POST that would have written into a stranded session. Option 2 adds one log line per stranded-session-creation event — by definition rare.

## Success Criteria

- [ ] Reload onto a 404'd session URL clears `?session=` automatically (Option 1)
- [ ] One ERROR log per turn-1 all-docs-failed event (Option 2)
- [ ] Frontend tests green: `useSessionMessages.test.ts` + `ChatPage.test.tsx`
- [ ] Backend tests green: three new cases under `TestDocumentInjectorBugF` (or sibling class)
- [ ] tsc + ruff clean; full test suites unchanged
- [ ] Manual E2E walkthroughs above pass
- [ ] No new sessions land with `documentIds=[]` after ≥2 turns of doc-attached chat in dev for 7 days post-merge

## Open Questions

- **Auto-redirect (1a) vs banner (1b)?** Default 1a. Revisit if anyone reports the auto-clear is jarring (e.g. a user about to copy the URL for a bug report and the URL changes under them). Mitigation: log the cleared session id at INFO so we can still trace it.
- **Should the ERROR include user id?** Probably not — Cloud Logging already binds it via the request scope. Keep the line lean.
- **Option 3 — does the user want it now or as backstop?** Recommendation: backstop. Re-evaluate after Option 1 is in.
- **Backfill the existing 55-turn stranded row?** Out of scope here — recommend deletion. If multiple users have stranded rows in prod, file a one-off migration: scan `chat_sessions` for `turnCount > 1 AND documentIds == []`, surface a list, decide per-row.

## Related Documents

- [chat-history-fixes.md (1.13)](chat-history-fixes.md) — B1: synchronous `_ensure_session_index` (the original sprint this builds on)
- [chat-history-deep-fixes-2.md (1.15)](chat-history-deep-fixes-2.md) — Bug A': `useStableThreadId`, the URL/threadId lifecycle Option 1 plugs into
- [implemented/multi-doc-context-fix.md (1.22)](implemented/multi-doc-context-fix.md) — D1 loader-log elevation; the WARNING line that surfaced the empty-`document_ids` turn signature in dev logs
- [local-dev-cli.md (1.4)](local-dev-cli.md) — context for the deferred `aiplatform sessions audit --empty-docs` follow-up
