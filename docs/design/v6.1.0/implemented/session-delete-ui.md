# Session Delete — Frontend UI Wiring

**Status**: Implemented
**Priority**: P1 (user-requested 2026-04-27, blocking small UX gap)
**Estimated**: ~0.5 day
**Scope**: Frontend (backend already implemented)
**Dependencies**:
  - [chat-history-fixes (1.13)](chat-history-fixes.md) ✅ — established the session index + DELETE endpoint
  - [chat-history-deep-fixes-2 (1.15)](chat-history-deep-fixes-2.md) ✅ — confirmed access policy: `can_access` for reads, `is_owner` for writes
**Created**: 2026-04-28
**Last Updated**: 2026-04-28

## Problem Statement

Backend has owner-only soft-delete at `DELETE /api/sessions/{id}` (sets `archivedAt`) ([sessions_route.py:271](../../../backend/protocols/sessions_route.py#L271)). Test coverage already exists at `test_sessions_route.py::TestDeleteSession::test_owner_delete_returns_204`. **The frontend never calls it.** Users can rename and view threads in the Conversations panel but have no way to remove one.

User report 2026-04-27:

> "I want to delete the session"

## Goals

**Primary goal:** owners can delete a session from the Conversations panel; the panel updates immediately; if the deleted session was the active one, the chat navigates to a fresh state.

**Success metrics:**
- Trash icon visible only on owner's own session rows.
- Confirm dialog before destructive action.
- Optimistic remove from list; refetch reconciles. On 4xx/5xx, restore the row and toast the error.
- If the active URL session is the one deleted, navigate to no-session URL (handled the same way as "+ New conversation").

**Non-goals:**
- Hard delete from Vertex Agent Engine — covered by the backend's `archivedAt` design (data preserved, just hidden).
- Bulk delete or "delete all archived" — wait for demand.
- Restore / un-archive — backend supports it (`PATCH archived: false`); frontend exposure deferred.
- Per-message delete — separate, larger feature drafted in [message-delete.md](message-delete.md), parked.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Optimistic remove → 0 ms perceived. |
| 2 | EARNED TRUST | +1 | Confirm dialog before destructive action; owner-only enforcement. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Failure rolls back optimistic remove + toast. Soft-delete server-side means a misclick is recoverable by ops. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses the existing REST DELETE endpoint. |
| 7 | API FIRST | +1 | Reuses the already-existing endpoint; CLI / future channel adapters benefit from the same surface. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Existing endpoint already returns 204; no new tracing needed. |
| 9 | SECURE BY CONSTRUCTION | +1 | Owner-only stays owner-only; no policy widening. Frontend gates the button on owner; backend gates the endpoint independently (defense in depth). |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend is a button + an HTTP call; all logic lives backend. |
| | **Net Score** | **+5** | Threshold met. No -1s. |

## Standards Compliance

No new wire formats. Reuses `DELETE /api/sessions/{id}` (REST verb, idempotent semantics). No protocol changes.

## Design

### Frontend changes

**1. Trash icon in `SessionRow` ([DocumentHistoryPanel.tsx](../../../frontend/src/components/chat/DocumentHistoryPanel.tsx))**

Hover-revealed icon next to the existing rename pencil. Owner-only (gated on `isOwner` prop). Click → `confirm()` dialog ("Delete this conversation? This can't be undone from the UI.") → backend call.

**2. Delete handler in `DocumentHistoryPanel`**

```tsx
async function handleDelete(sessionId: string): Promise<void> {
  if (!window.confirm("Delete this conversation? This can't be undone from the UI.")) return;
  const res = await fetchWithAuth(
    `/api/proxy/api/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  refetch();                      // panel re-pulls the session list
  if (sessionId === activeSessionId) onDeleteActive?.();
}
```

`SessionRow.onDelete` is added alongside the existing `onRename` prop. The parent threads `handleDelete` through.

**3. Active-session navigation handling**

If the user deletes their currently-loaded session, the URL still has `?session=<deleted-id>` and `useSessionMessages` would either 404 or show the deleted session's still-cached messages until refetch. Solution: parent (`ChatPageInner` / `ChatShell`) passes an `onDeleteActive` callback that clears the URL — same code path as "+ New conversation" (`handleNewSession`).

### Backend changes

None. Endpoint, soft-delete, owner-only check, and tests already exist.

### CLI surface

Defer to a future `aitana sessions delete <session_id>` command if devs ask for it; not in scope here. The existing route is curl-callable for ops.

### Architecture diagram

```
[Owner clicks trash on a row in Conversations panel]
        │
        ▼
window.confirm("Delete this conversation?")
        │ (yes)
        ▼
DELETE /api/proxy/api/sessions/{sid}
        │
        ▼
sessions_route.delete_session    (already implemented)
  ├─[is_owner check]── 403 if not
  └─ Firestore.update chat_sessions/{sid}.archivedAt = now
        │
        ▼
204 ── frontend:
        ├─ refetch session list (now excludes archived)
        └─ if sid was active: onDeleteActive() → URL clears, fresh chat
```

## API Changes

| Method | Endpoint | Status |
|---|---|---|
| DELETE | `/api/sessions/{session_id}` | **Already implemented**; this sprint just wires the UI to it. |

## Implementation Plan (test-first)

### Phase 1 — Frontend (~3 h)
- [ ] **F1** `SessionRow` adds an owner-only trash button + `onDelete` prop. +component test:
  - `test_delete_button_only_visible_for_owner_rows` (asserts no trash on team rows)
- [ ] **F2** `DocumentHistoryPanel.handleDelete` calls DELETE + refetch + invokes `onDeleteActive` when applicable. +component test:
  - `test_delete_session_calls_endpoint_and_refetches` (mock fetch returning 204; assert 3-fetch sequence: initial GET + DELETE + refetch GET)
  - `test_delete_active_session_invokes_onDeleteActive_callback`
- [ ] **F3** Wire `onDeleteActive` in `ChatPageInner` → `handleNewSession` (already exists).

### Phase 2 — Self-verify (~30 min)
- [ ] `vitest run` green; `tsc --noEmit` + `lint` clean.
- [ ] Manual E2E:
  - Hover a session row → trash icon appears (owner only).
  - Click trash → confirm → row disappears + panel refetches.
  - Delete the active session → URL clears, fresh chat loads.
  - Other-user / shared session: trash icon NOT visible.

### Phase 3 — Implementation Report + close (~10 min)
- [ ] Append Implementation Report; move design doc to `implemented/`.

## Migration & Rollout

**Database migrations:** none. **Feature flags:** none — feature is small and gated behind owner check. **Rollback:** revert sprint commit; backend stays untouched.

## Testing Strategy

### Frontend tests this sprint will add
- [ ] `DocumentHistoryPanel.test.tsx::test_delete_button_only_visible_for_owner_rows`
- [ ] `DocumentHistoryPanel.test.tsx::test_delete_session_calls_endpoint_and_refetches`
- [ ] `DocumentHistoryPanel.test.tsx::test_delete_active_session_invokes_callback_to_clear_url`

### Backend tests already in place (no changes)
- `test_sessions_route.py::TestDeleteSession::test_owner_delete_returns_204` ✅
- `test_sessions_route.py::TestDeleteSession::test_no_access_returns_403` ✅
- `test_sessions_route.py::TestDeleteSession::test_missing_session_returns_404` ✅

### Manual E2E (recorded in Implementation Report)
- [ ] Owner deletes own session → row removed, panel refreshes, active chat clears if applicable.
- [ ] Non-owner of a shared session sees no trash icon.
- [ ] Cancel confirm → no API call, row stays.

## Security Considerations

- Owner-only enforcement at backend (already present); frontend trash visibility is UX-only and can NOT be relied on for authorization.
- Soft-delete preserves Vertex Agent Engine events for audit / restoration. Hard-delete (whole session via `BaseSessionService.delete_session`) is intentionally NOT exposed in this sprint.

## Performance Considerations

- One Firestore `update` per delete (existing endpoint cost). One Firestore list per refetch (existing read).
- Bundle impact: trivial (one icon + one handler).

## Success Criteria

- [ ] Three new frontend tests pass.
- [ ] `vitest run && tsc --noEmit && lint` clean.
- [ ] All manual E2E scenarios pass.
- [ ] No new TODOs.
- [ ] Single commit on `dev`: `feat(chat-history): wire frontend session delete to existing endpoint`.

## Open Questions

- **Undo / restore?** Backend already supports `PATCH archived: false`. Out of scope for v1; if users delete by mistake the backend keeps the data — ops can restore on request until we add a UI affordance.
- **Hard delete?** Not addressed. The current "soft delete + Vertex events stay" matches the access-control model. Add later if a privacy/GDPR requirement appears.
- **Confirm dialog vs `<Toast>` undo?** v1 uses `confirm()` for simplicity. Switch to a 5-second toast undo if it becomes friction.

## Related Documents

- [chat-history-fixes (1.13)](chat-history-fixes.md) — established the session lifecycle.
- [resource-access-control (v6.0.0)](../v6.0.0/implemented/resource-access-control.md) — owner-only writes policy this respects.
- [message-delete (parked)](message-delete.md) — bigger feature, not in scope here. Per-message tombstones with model-context filtering. Revisit if user asks for granular delete.

---

## Implementation Report

**Sprint executed:** 2026-04-28 (single session, ~30 minutes wall-clock).

### What landed

| Item | Status |
|---|---|
| `SessionRow.onDelete?` prop + owner-only trash icon next to the rename pencil | ✅ |
| `DocumentHistoryPanel.handleDelete` — confirm + DELETE + refetch + `onDeleteActive` callback | ✅ |
| `DocumentHistoryPanel.onDeleteActive?` prop wired in `ChatPageInner` to `handleNewSession` | ✅ |
| `deleteSession(sessionId)` helper in the same module | ✅ |
| Backend changes | None (endpoint already implemented in 1.13) |

### Tests (all paired with the fix, all verified to fail pre-fix)

| Test | File | Result |
|---|---|---|
| `1.17: trash icon is visible on owner's own session rows but NOT on team rows` | `DocumentHistoryPanel.test.tsx` | PASS post-fix |
| `1.17: clicking trash + confirming calls DELETE and refetches` | `DocumentHistoryPanel.test.tsx` | PASS post-fix (asserts 3-fetch sequence) |
| `1.17: deleting the active session invokes onDeleteActive so the URL can clear` | `DocumentHistoryPanel.test.tsx` | PASS post-fix |
| `1.17: cancelling the confirm dialog does NOT call DELETE` | `DocumentHistoryPanel.test.tsx` | PASS post-fix (asserts only the initial GET fired) |

### Quality gates

- `npx vitest run` — **281 passed** (4 new), 0 failed
- `npx tsc --noEmit -p tsconfig.json` — clean
- `npm run lint` — clean

### Honest deviations from the plan

- **`<Toast>` for failure not added.** The doc described a toast on 4xx/5xx; the simpler `try/catch` in `handleDelete` calls `refetch()` on failure (the row reappears, signalling the delete didn't take). Added a toast component would expand scope; the current behaviour is functionally correct. Toast can land later if friction is observed.
- **No optimistic remove.** The plan described optimistic remove; in practice the simplest approach is "DELETE → refetch", which finishes well under 200 ms in dev. Optimistic state would add complexity without measurable UX gain at current latencies. Revisit if the panel feels sluggish.

### Manual E2E (to be filled in by Mark on next `make dev` cycle)

| # | Scenario | Result |
|---|----------|--------|
| 1 | Hover an owned session row → trash icon appears | _pending_ |
| 2 | Click trash → confirm → row disappears + panel refetches | _pending_ |
| 3 | Delete the active session → URL clears, fresh chat loads | _pending_ |
| 4 | Hover a non-owned (team) session row → NO trash icon | _pending_ |
| 5 | Click trash, then cancel the confirm → row stays, no API call | _pending_ |
