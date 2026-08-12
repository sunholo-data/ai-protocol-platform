# Sprint Plan: CHAT-HIST-FIX — Chat History Hardening

## Summary

Close four user-visible chat-history regressions discovered 2026-04-27 with paired regression tests so they cannot reappear silently.

**Duration:** 1 day (~5 focused hours)
**Scope:** Fullstack
**Dependencies:** None — design doc approved; all touched files exist; no schema/API changes.
**Risk Level:** Low — surgical edits to existing functions; six tests gate each fix.
**Design Doc:** [chat-history-fixes.md](chat-history-fixes.md)

## Current Status Analysis

### Recent Velocity
- 173 commits in last 7 days (~25/day), heavy chat/document/protocol work.
- Last comparable hardening sprint: streaming-error-surface (~4–5 hours wall clock, single session).
- Estimated capacity: well within budget for a 1-day sprint.

### Existing Implementation
- `backend/adk/callbacks.py` — `_tracker` (before-agent), `_flush_session_index` (after-agent), `generate_title_fast` invocation already wired. Just needs B1 move + B2 sync + B3 retry condition.
- `backend/db/chat_sessions.py` — `add_session_documents`, `update_session_fields`, `get_session_index`, `create_session_index` all exist. No new functions needed.
- `backend/skills/skill_processor.py` — async generator already wraps `stream_agui_events`; B1 adds one synchronous Firestore call before the loop.
- Tests:
  - `backend/tests/api_tests/test_stream_skill.py` (16 cases) — extend with `test_refresh_finds_session_index_after_first_stream_event`.
  - `backend/tests/api_tests/test_sessions_route.py` — extend with `test_list_sessions_for_document_includes_docs_added_after_turn_one`.
  - `backend/tests/unit/test_session_callbacks.py` — extend with `test_title_regenerates_when_turn_two_returns_empty` and `test_create_session_index_is_idempotent`.
  - `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` — extend with `test_message_list_never_shrinks_on_stutter`.
  - `frontend/src/components/document/__tests__/DocumentHistoryPanel.test.tsx` — new file with `test_rename_refetches_session_list_on_success`.

## Proposed Milestones

### Milestone 1: Backend fixes — B1, B2, B3 (~2.5h)

**Scope:** backend
**Goal:** Make the Firestore session index reliably reflect what the user sees: created synchronously before the first SSE event, document_ids stay in sync mid-session, title retries past turn 2 if it was empty.
**Estimated:** ~110 LOC implementation + ~140 LOC tests = ~250 LOC
**Duration:** 2.5h

**Tasks:**
- [ ] **B1** Move `create_session_index` to synchronous prelude in `process_skill_request`; make `_tracker` callback in `callbacks.py:415` idempotent (~50 LOC)
- [ ] **B1 test** `test_stream_skill.py::test_refresh_finds_session_index_after_first_stream_event` — POST stream, then `GET /api/sessions/{id}` returns 200 (~30 LOC)
- [ ] **B1 test** `test_session_callbacks.py::test_create_session_index_is_idempotent` — call twice with same id, no Firestore write on second call (~25 LOC)
- [ ] **B2** Extend `_flush_session_index` to call `add_session_documents` from session state (~20 LOC)
- [ ] **B2 test** `test_sessions_route.py::test_list_sessions_for_document_includes_docs_added_after_turn_one` — start session with doc A, simulate doc B added at turn 2, `list_sessions_for_document(B)` returns the session (~40 LOC)
- [ ] **B3** Change title-gen condition to `turn_count == 2 or (turn_count >= 4 and not state["titleSet"])`; set `state["titleSet"] = True` on success (~20 LOC)
- [ ] **B3 test** `test_session_callbacks.py::test_title_regenerates_when_turn_two_returns_empty` — mock `generate_title_fast` to return None then real value; assert title set after turn 4 flush (~45 LOC)

**Files to Create/Modify:**
- `backend/skills/skill_processor.py` (modify, +~25 LOC)
- `backend/adk/callbacks.py` (modify, +~30 LOC, refactor existing tracker + flush + title)
- `backend/tests/api_tests/test_stream_skill.py` (modify, +~30 LOC)
- `backend/tests/api_tests/test_sessions_route.py` (modify, +~40 LOC)
- `backend/tests/unit/test_session_callbacks.py` (modify, +~70 LOC)

**Acceptance Criteria:**
- [ ] All four new backend tests fail before each respective fix and pass after.
- [ ] `cd backend && uv run pytest tests/api_tests tests/unit -q` — full pass (no other tests broken).
- [ ] `cd backend && uv run ruff check .` — clean.
- [ ] B1 callback path is hit in tests via the existing AG-UI dep-override pattern; no live Firestore in tests.

**Risks:**
- B1 synchronous Firestore write could surface a previously-hidden failure mode if Firestore returns slow. Mitigation: time-box during dev; if write >200 ms locally, log it and ship anyway (Cloud Run regional Firestore is much faster).
- `_flush_session_index` may be called from multiple paths; verify B2's `add_session_documents` is safe to invoke when `document_ids` is empty (it should no-op via `ArrayUnion([])`).

---

### Milestone 2: Frontend fixes — F1, F2 (~1.5h)

**Scope:** frontend
**Goal:** Mid-stream message list never shrinks; manual rename refreshes the document history panel within 200 ms.
**Estimated:** ~70 LOC implementation + ~80 LOC tests = ~150 LOC
**Duration:** 1.5h

**Tasks:**
- [ ] **F1** Wrap `onMessagesChanged` in `useSkillAgent.ts:120-129` with monotonic-length dedup using message id as merge key; `console.warn` on suppressed shrink (~40 LOC)
- [ ] **F1 test** `useSkillAgent.test.tsx::test_message_list_never_shrinks_on_stutter` — feed sequence of message snapshots [3 msgs] → [2 msgs] → [3 msgs]; assert rendered state never drops below 3 (~40 LOC)
- [ ] **F2** Thread `refetch` callback from parent into `DocumentHistoryPanel`; call `await refetch()` after PATCH succeeds (~30 LOC including parent prop wiring)
- [ ] **F2 test** new file `DocumentHistoryPanel.test.tsx::test_rename_refetches_session_list_on_success` — mock PATCH OK; assert `refetch` invoked exactly once after await (~40 LOC)

**Files to Create/Modify:**
- `frontend/src/hooks/useSkillAgent.ts` (modify, +~40 LOC)
- `frontend/src/hooks/__tests__/useSkillAgent.test.tsx` (modify, +~40 LOC)
- `frontend/src/components/document/DocumentHistoryPanel.tsx` (modify, +~30 LOC including prop)
- Parent of `DocumentHistoryPanel` (likely `DocumentPanel.tsx` or `ChatLayout.tsx` — confirm at edit time) (modify, +~5 LOC to pass `refetch`)
- `frontend/src/components/document/__tests__/DocumentHistoryPanel.test.tsx` (new, ~50 LOC including fixtures)

**Acceptance Criteria:**
- [ ] Both new frontend tests fail pre-fix and pass post-fix.
- [ ] `cd frontend && npx vitest run` — full pass.
- [ ] `cd frontend && npx tsc --noEmit -p tsconfig.json` — clean.
- [ ] `cd frontend && npm run lint` — clean.

**Risks:**
- F1's dedup must use message id (stable across renders), not array length alone — otherwise replacing a message with another of equal length would still flicker. Verify `SkillMessage.id` is stable.
- F2 requires confirming the parent component name; the design doc notes the parent path needs verifying at edit time. Two-minute task before committing to the prop-threading approach.

---

### Milestone 3: Self-verification + manual e2e (~1h)

**Scope:** fullstack
**Goal:** Confirm every fix works against a running stack, not just in unit tests. Capture results in the design doc's Implementation Report.
**Estimated:** ~0 LOC code, ~30 LOC docs (Implementation Report stub)
**Duration:** 1h

**Tasks:**
- [ ] Restart `make dev` (so all today's env-hardening + new code load fresh).
- [ ] **E2E #1 (refresh):** Open fresh chat → send message → reload page → confirm history present with **0 retries** on the happy path.
- [ ] **E2E #2 (mid-stream):** Send 3+ rapid messages → confirm message list never visibly shrinks during streaming.
- [ ] **E2E #3 (doc-linked):** Open doc A → start chat → open doc B mid-session → reload → confirm conversation appears in B's Conversations panel.
- [ ] **E2E #4 (title):** Send 5+ messages → confirm title appears within turn 2 or turn 5.
- [ ] **E2E #5 (rename):** Manual rename in DocumentHistoryPanel → confirm list updates within 200 ms.
- [ ] Append Implementation Report to `chat-history-fixes.md` with each E2E result inline.
- [ ] Move design doc + sprint plan to `implemented/` via `move_to_implemented.sh chat-history-fixes`.

**Files to Modify:**
- `docs/design/v6.1.0/chat-history-fixes.md` (modify, append Implementation Report section)

**Acceptance Criteria:**
- [ ] All five E2E checks pass and are recorded in the Implementation Report.
- [ ] Design doc + sprint plan moved to `implemented/`.
- [ ] No new TODOs or `# noqa` introduced anywhere in the diff.

**Risks:**
- Running stack may surface an interaction we didn't catch in unit tests (e.g., F1 + B1 race). If so, **stop and re-plan** rather than patch on top — the whole point of this sprint is to break that habit.

## Day-by-Day Breakdown

### Day 1 (single-session, ~5h)

- **Hour 1–2.5: Backend (M1)** — B1 + B1 tests, B2 + B2 test, B3 + B3 test. Run `pytest tests/api_tests tests/unit -q && ruff check .` before moving on.
- **Hour 2.5–4: Frontend (M2)** — F1 + F1 test, F2 + F2 test. Run `vitest run && tsc --noEmit && lint` before moving on.
- **Hour 4–5: Verification (M3)** — restart `make dev`, run all five E2E checks, append Implementation Report, move doc to `implemented/`.

**Pause point:** end of M1. If anything in the backend tests behaves unexpectedly, pause for review before starting M2 — the frontend fixes assume the backend invariants hold.

## Quality Gates

After M1:
```bash
cd backend && uv run pytest tests/api_tests tests/unit -q && uv run ruff check .
```

After M2:
```bash
cd frontend && npx vitest run && npx tsc --noEmit -p tsconfig.json && npm run lint
```

After M3 (full sprint):
```bash
# Manual: run `make dev` and execute the five E2E scenarios.
# Code: full lint + tests already covered in M1/M2 gates.
```

## Out of Scope (do not add)

- Reworking session-id mapping (`use_thread_id_as_session_id` is correct).
- New endpoints, new schema fields, feature flags.
- Cross-channel session unification, session forking.
- Telemetry beyond the F1 `console.warn` — observable-by-default for AG-UI client errors is a separate sprint.
- Frontend banner styling for `RUN_ERROR` (the streaming-error-surface sprint covers it; F1's `console.warn` is dev-tools only).

## Definition of Done

- [ ] All six new tests committed and green.
- [ ] All quality gates pass (M1 + M2 + M3 commands above).
- [ ] All five E2E scenarios verified manually and recorded.
- [ ] Design doc + sprint plan moved to `docs/design/v6.1.0/implemented/`.
- [ ] Single sprint commit on `dev` with conventional-commit subject `feat(chat-history): close 4 history regressions w/ regression tests`.
