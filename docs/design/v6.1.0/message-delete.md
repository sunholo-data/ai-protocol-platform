# Message Delete (Tombstone-Based Soft Delete)

**Status**: Planned
**Priority**: P2 (feature; nice-to-have, not unblocking the demo)
**Estimated**: ~1.5 days (post-design)
**Scope**: Fullstack
**Dependencies**:
  - [chat-history-fixes (1.13)](chat-history-fixes.md) ✅ — session index + write paths
  - [chat-history-deep-fixes (1.14)](chat-history-deep-fixes.md) ✅ — agent-identity guard, session resume
  - [chat-history-deep-fixes-2 (1.15)](chat-history-deep-fixes-2.md) ✅ — `user_id` triple consistency, `can_access` on read
  - [resource-access-control (v6.0.0)](../v6.0.0/implemented/resource-access-control.md) ✅ — owner-only writes
**Created**: 2026-04-27
**Last Updated**: 2026-04-27

## Problem Statement

Users can't delete messages from a chat thread. Reported by Mark on 2026-04-27 mid-sprint after the chat-history bug cluster:

> "we could do with being able to delete messages"

**Current state:**
- AG-UI's `agent.messages` accumulates every turn with no removal API.
- Vertex Agent Engine session events are the canonical store. Messages only leave when the whole session is deleted (via `BaseSessionService.delete_session`).
- Frontend `MessageBubble` has no delete control.
- Owner-only PATCH/DELETE on `chat_sessions/{id}` exist; no per-message endpoint.

**Impact (today):**
- Test mistakes (typos, mis-routed prompts) live forever in the thread.
- Sensitive content the user pasted by accident can't be redacted without nuking the whole session.
- Shared sessions can't have a member retract a message they regret.

**Impact (deferred — why P2):** chat threads are short-lived; daily users typically start fresh sessions rather than curate long ones. Delete is quality-of-life, not a blocker for the July 2026 demo.

## Goals

**Primary goal:** owners can hide individual messages from their session view AND from the LLM's context on subsequent turns, with the original events preserved in Vertex for forensic and shared-session audit.

**Success metrics:**
- Owner deletes a message → it disappears from the chat UI within 200 ms.
- Next turn's LLM request to the model excludes the deleted message and any tool-call follow-ups it generated.
- Shared session viewers (non-owners with `can_access`) see a tombstone marker ("Message deleted by author") in place of the message — preserves audit visibility without leaking content.
- Diagnostic + fix-locking tests for each layer (Firestore tombstone write, Vertex events filter at GET, model-request filter via callback, frontend hide).

**Non-goals:**
- **Hard delete from Vertex Agent Engine.** ADK's `BaseSessionService` exposes `append_event` and `delete_session` (whole session only), not `delete_event`. Verified against `google.adk.sessions.base_session_service.BaseSessionService` in `google/adk-python@v1.24.1`.
- Editing message content (separate feature).
- Bulk delete or "delete from this point onward" (do single-message first; add bulk if asked for).
- Restoring deleted messages (no undo in v1; users are warned at delete time).

## Verified API Constraints

These shape the design and are NOT speculation.

| Layer | API surface | Verdict |
|---|---|---|
| **ADK Sessions** | `BaseSessionService` is append-only: `create_session`, `get_session`, `list_sessions`, `delete_session` (whole session), `append_event`. **No `delete_event` or `update_event`.** | Verified via `mcp__adk-mcp__search_code` against `google/adk-python@v1.24.1`. |
| **Vertex Agent Engine sessions** | Implements the same `BaseSessionService` abstraction; no public per-event mutation API documented. | Per Vertex docs and the ADK abstraction the platform uses. |
| **AG-UI protocol** | No standard `MESSAGE_DELETE` event. `MessagesSnapshot` carries full state; `Custom` event type can carry app-specific signals. | Verified via `https://docs.ag-ui.com/concepts/events`. |

**Implication:** "Delete" in this design = **tombstone in Firestore + filter at every read path**. The Vertex events stay; we curate what callers see. This is the only path consistent with the platform's APIs today.

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net >= +4.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Optimistic frontend hide → 0 ms perceived. Backend tombstone write is ~30-80 ms but off the critical streaming path. |
| 2 | EARNED TRUST | +1 | "Delete actually means delete-from-view-and-context" matches user expectation. Tombstone marker for shared-session viewers is visible accountability rather than silent rewrite. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; skill authors don't see this. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing changes. |
| 5 | GRACEFUL DEGRADATION | +1 | Tombstone write fails → frontend rolls back the optimistic hide and shows a "couldn't delete" toast. The Vertex events were never touched, so failure is fully recoverable. |
| 6 | PROTOCOL OVER CUSTOM | -1 | Introduces a Firestore `chat_sessions/{id}/tombstones` subcollection (custom). Justified below — the platform standards we use (ADK Sessions, AG-UI) explicitly do not support per-event delete, so a sidecar store is the only path. |
| 7 | API FIRST | +1 | New `DELETE /api/sessions/{id}/messages/{message_id}` endpoint reusable by CLI, future channel adapters, and a future "redact" admin tool. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Tombstones carry `(message_id, deleted_by_uid, deleted_at)` — every redaction is auditable in Firestore + Cloud Trace. The audit log is part of the data model, not an afterthought. |
| 9 | SECURE BY CONSTRUCTION | +1 | Owner-only DELETE (per [resource-access-control](../v6.0.0/implemented/resource-access-control.md)). The 1.15 read-policy relaxation is read-only; this sprint does NOT widen writes. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend just hides a message id; all filtering, tombstone storage, and model-context curation lives backend. |
| | **Net Score** | **+5** | Threshold met. One -1 (Axiom #6) with justification below. |

**Conflict justifications:**
- **Axiom #6 (-1):** ADK and AG-UI both lack a per-event delete primitive (verified). Introducing a Firestore tombstone subcollection is the only way to express "this message is hidden" within the platform's actual capabilities. The alternative (rebuild-session-without-event via `delete_session` + replay) is slower, fragile (Vertex doesn't guarantee event ordering on replay), and loses fidelity (timestamps drift). Tombstone path is the standards-respecting choice given the constraint.

## Standards Compliance

- **No new wire formats.** The DELETE endpoint takes path params + an empty body; the GET `/api/sessions/{id}/messages` filter is a server-side curation, not a wire-format change.
- **AG-UI:** the frontend filter on receive is purely client state; no new event types proposed. (If we later need other clients to see tombstones, we'll add a `Custom` event with `name: "message_deleted"` per the AG-UI standard — out of scope for v1.)
- **ADK:** uses the existing append-only contract; tombstones are sidecar metadata.

## Design

### Data model

New Firestore subcollection: `chat_sessions/{session_id}/tombstones/{message_id}`.

```python
class MessageTombstone(BaseModel):
    """Marker that a message in a session has been deleted by its owner.

    Stored at chat_sessions/{session_id}/tombstones/{message_id}.
    The doc id IS the deleted message id (Vertex event id) — guarantees
    one tombstone per message and trivial point reads.
    """

    deleted_by_uid: str  # Firebase uid of the actor (always the session owner per access policy)
    deleted_at: datetime
    reason: Literal["user_request", "admin_redact"] = "user_request"
```

**Reasoning for subcollection vs. inline list:**
- Subcollection scales to many tombstones without rewriting the parent document.
- Per-tombstone audit history is a natural fit (`actor`, `timestamp`).
- Simple existence check at read time: `tombstones.where(__name__, in=event_ids)` returns hidden ids.

### Backend

**1. New endpoint — `DELETE /api/sessions/{session_id}/messages/{message_id}`**

In [backend/protocols/sessions_route.py](../../../backend/protocols/sessions_route.py):
- `Depends(get_current_user)` for auth.
- `_require_session(session_id)` for existence.
- `ctx.is_owner(idx)` — **stays owner-only**. The 1.15 `can_access` widening was for reads only; deleting another user's message would violate the access-control contract.
- Verifies the `message_id` corresponds to a real event in the session (one Vertex GET via `session_service.get_session`); returns 404 if not. Prevents tombstone-stuffing.
- Writes `chat_sessions/{session_id}/tombstones/{message_id}` with `actor=user.uid`, `timestamp=now`.
- Returns 204.

**2. Read filter — extend `get_session_messages`**

In the same route at [sessions_route.py:234](../../../backend/protocols/sessions_route.py#L234), after fetching events from Vertex:
- Read all docs in `chat_sessions/{session_id}/tombstones/` (point read, expected count <50 per session).
- For each event, if its id is in the tombstone set:
  - **Owner viewer:** drop entirely (they hid it; no value re-showing).
  - **Non-owner viewer (per `can_access`):** replace with `{role, content: null, deleted: true, deleted_at}` so the UI can render a tombstone marker.
- Tool-call result events orphaned by a deleted user message: drop too (no parent context).

**3. Model-context filter — new `before_model_callback`**

In [backend/adk/callbacks.py](../../../backend/adk/callbacks.py): a new `make_tombstone_filter()` callback that runs alongside `make_document_injector` in `before_model_callback`. It:
- Reads the tombstone set for the current session (Firestore lookup; cache per-turn in `state["_tombstones_loaded"]`).
- Walks `llm_request.contents` and removes contents whose event id matches a tombstone.
- Drops corresponding tool-call follow-ups (function_response Parts that reference a removed function_call).

The model never sees deleted content on subsequent turns — this is the substantive part of the feature. Without it, "delete" is just visual.

### Frontend

**1. Per-message control in `MessageBubble`**

In [frontend/src/components/chat/MessageBubble.tsx](../../../frontend/src/components/chat/MessageBubble.tsx):
- Hover-revealed icon button (trash) on owner's own bubbles, mirrored on the rename pencil already added in 1.13.
- Click → `confirm()` dialog ("Delete this message? This will be hidden for everyone with access to this conversation.")
- On confirm: optimistic hide via local state (filter the message id out of the rendered list) + `DELETE /api/proxy/api/sessions/{id}/messages/{message_id}` with `fetchWithAuth`.
- On 4xx/5xx: rollback the optimistic hide, show a `<Toast>` with the error.

**2. Tombstone rendering for shared-session viewers**

When the GET response includes a message with `{deleted: true}`, render a muted line-height marker:

> *Message deleted by author at 14:23*

Same component, gated on the response shape — no new wire enum.

**3. Hide button visibility rule**

Show the trash icon only when:
- `user.uid === session.owner_uid` (owner)
- AND the message role is `user` OR `assistant` (no deleting system messages or tool results directly — those follow the parent message)

### CLI surface

Add to [aitana CLI](local-dev-cli.md):

```
aitana sessions message delete <session_id> <message_id>
  --reason user_request|admin_redact   (default: user_request)
  --confirm  (skip the interactive prompt)
```

Wires to the same DELETE endpoint with the dev's Firebase token. Useful for:
- Cleaning up evalset-noise messages without going through the UI.
- Admin redaction during incident response.
- Smoke-testing the endpoint from CI.

(One Click subcommand + httpx call + unit test ≈ 0.15 day.)

### API changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| DELETE | `/api/sessions/{session_id}/messages/{message_id}` | Soft-delete a message via tombstone. Owner-only. Returns 204. | No (new) |
| GET    | `/api/sessions/{session_id}/messages` (existing) | Now filters tombstones; non-owner viewers see deleted-marker placeholders. Owners see nothing where the message was. | Yes — response shape gains optional `deleted: true` field on tombstoned entries. Breaking only for clients that don't ignore unknown fields; the frontend will be updated in the same PR. |

### Architecture diagram

```
[Owner clicks trash on a bubble]
        │
        ▼
DELETE /api/proxy/api/sessions/{sid}/messages/{mid}
        │
        ▼
sessions_route.delete_message
  ├─[is_owner check]── 403 if not
  ├─[verify event exists in Vertex]── 404 if not
  └─ Firestore.set chat_sessions/{sid}/tombstones/{mid}
        │
        ▼
204 ── frontend confirms optimistic hide

— next time anyone (including the model) reads this session —

GET /api/sessions/{sid}/messages
  ├─ Vertex.get_session(...)            ← raw events
  ├─ Firestore.list(chat_sessions/{sid}/tombstones)
  └─ filter: drop for owners, mark for non-owner viewers
        │
        ▼
[chat UI renders curated history]


— next agent turn —

before_model_callback chain:
  ├─ make_document_injector
  └─ make_tombstone_filter   ← NEW
        ├─ load tombstones (cache per turn)
        └─ remove matching contents from llm_request.contents

[model sees curated context]
```

## Implementation Plan (test-first)

### Phase 1 — Diagnostics (~30 min)
- [ ] Diagnostic test: `test_delete_message_endpoint_does_not_exist_yet` (assert 404 on `DELETE /api/sessions/{id}/messages/{mid}` against current code; locks the absence so the implementation is the only thing that turns it green).

### Phase 2 — Backend (~4 h)
- [ ] **B1** Tombstone model + `db/chat_sessions.py` helpers (`create_tombstone`, `list_tombstones_for_session`). +tests.
- [ ] **B2** `DELETE /api/sessions/{session_id}/messages/{message_id}` endpoint. Owner-only; verifies event existence; idempotent. +tests covering 200/403/404/double-delete.
- [ ] **B3** Extend `get_session_messages` to filter tombstones for owners + render markers for non-owner viewers. +tests for both roles.
- [ ] **B4** New `make_tombstone_filter` callback in `adk/callbacks.py`; wire into `before_model_callback` after `make_document_injector`. +unit test that asserts deleted contents are removed before model invocation.

### Phase 3 — Frontend (~2.5 h)
- [ ] **F1** `MessageBubble` hover-reveal trash button (owner-only); confirm dialog. +component test.
- [ ] **F2** `useSkillAgent` exposes `deleteMessage(messageId)` that calls the DELETE endpoint and updates local state. +hook test for happy + failure rollback.
- [ ] **F3** Tombstone marker rendering for non-owner viewers. +visual test (`{deleted: true}` payload renders the muted marker).

### Phase 4 — CLI (~0.15 day)
- [ ] **C1** `aitana sessions message delete <session_id> <message_id> [--reason] [--confirm]`. +smoke test that hits the dev backend.

### Phase 5 — Self-verify (~1 h)
- [ ] Backend: `pytest tests/api_tests tests/unit -q` green.
- [ ] Backend: `ruff check .` clean.
- [ ] Frontend: `vitest run && tsc --noEmit && lint` green.
- [ ] Manual E2E:
  - Delete own message in fresh session → disappears, next turn doesn't reference it.
  - Open same session as a different user with `can_access` → tombstone marker visible.
  - Delete same message twice → second call is 204 (idempotent), no double-tombstone in Firestore.
  - CLI delete works against dev.

## Migration & Rollout

**Database migrations:** none — new subcollection appears on first write. Existing sessions trivially have empty tombstone sets.
**Feature flags:** `CHAT_HISTORY_DELETE_ENABLED` (default: true in dev/test, true in prod once UX is verified). Allows quick disable if abuse patterns emerge.
**Rollback:** revert sprint commit. Tombstones written before rollback stay in Firestore but become invisible (the read filter goes away); users see deleted content reappear. That's intentional safety — no destructive on-disk operation.
**Environment variables:** none new.

## Testing Strategy

### Backend (pytest)
- [ ] `test_delete_message_endpoint_does_not_exist_yet` — diagnostic, fails post-fix.
- [ ] `test_owner_can_delete_message_returns_204`
- [ ] `test_non_owner_cannot_delete_returns_403` (even with `can_access`)
- [ ] `test_delete_unknown_message_returns_404`
- [ ] `test_delete_is_idempotent` (second call returns 204, single tombstone)
- [ ] `test_get_messages_filters_tombstones_for_owner`
- [ ] `test_get_messages_renders_tombstone_marker_for_non_owner_with_can_access`
- [ ] `test_tombstone_filter_callback_removes_deleted_contents_from_llm_request`
- [ ] `test_tombstone_filter_drops_orphaned_tool_call_followups`

### Frontend (Vitest)
- [ ] `test_message_bubble_shows_trash_icon_for_owner_only`
- [ ] `test_delete_optimistically_hides_then_calls_endpoint`
- [ ] `test_delete_failure_rolls_back_optimistic_hide_and_shows_toast`
- [ ] `test_tombstone_marker_renders_for_non_owner_viewer`

### CLI
- [ ] `test_aitana_sessions_message_delete_calls_endpoint_with_confirm_flag`

### Manual E2E (recorded in Implementation Report)
- [ ] Owner deletes own message → gone from view; next turn's model context excludes it.
- [ ] Shared-session viewer sees tombstone marker.
- [ ] Idempotent: delete twice → no error.
- [ ] CLI invocation works.

## Security Considerations

- **Auth:** `Depends(get_current_user)` (existing).
- **Authorization:** `ctx.is_owner(idx)` only — non-owners 403 even with `can_access`. Aligned with [resource-access-control](../v6.0.0/implemented/resource-access-control.md): "writes (modify/delete) require ownership; reads can be widened by access-control type".
- **Audit trail:** every tombstone records `deleted_by_uid` + `deleted_at`. For `admin_redact` reason values (future), the actor is captured even when not the session owner.
- **No content-leakage on tombstones:** the deleted content is NOT copied into the tombstone doc. The original event still lives in Vertex (only Vertex-admins can read it via the platform's GCP project boundary, per [project_privacy_boundary.md](../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/project_privacy_boundary.md)).
- **Rate limiting:** not in v1 (delete is human-paced); revisit if abuse patterns emerge.

## Performance Considerations

- **Read path overhead:** one Firestore subcollection list per `GET /messages`. Bounded by tombstone count per session (expected <50). Co-located with the existing index lookup; ~5-15 ms.
- **Write path:** one Firestore set per delete. ~30-80 ms.
- **Model-context filter:** runs in `before_model_callback`, every turn. Cache tombstone set per-turn in `state["_tombstones_loaded"]` to avoid re-fetching during multi-step tool loops.
- **Frontend bundle:** trivial — confirmation dialog + a few lines in `MessageBubble`. <1 KB.

## Success Criteria

- [ ] All listed pytest + vitest tests pass; each new test verified to fail pre-fix where applicable.
- [ ] `pytest tests/ -m "not slow"` and `vitest run && tsc --noEmit && lint` clean.
- [ ] Manual E2E checklist all green; recorded in Implementation Report.
- [ ] No new TODOs or `# noqa` introduced.
- [ ] CLI command works against dev backend.
- [ ] Single (or sequential) commit on `dev` with conventional-commit subject `feat(chat-history): message-delete with tombstones`.

## Open Questions

- **Confirm dialog or one-click?** Going with `confirm()` for v1; revisit if users complain about friction.
- **Rebuild-session escape hatch?** If a user wants HARD deletion (e.g. for GDPR), the current "Delete session" already exists and removes everything. Worth surfacing in the UI alongside delete-message so users have the right tool when they need it.
- **Bulk delete?** Out of scope for v1. If the demand is "delete everything I just sent", "Delete session" already covers it.
- **Tombstones in shared sessions: is the tombstone marker too visible?** A user might want to retract a message *quietly*. Counterargument: silent rewrite of shared history is a trust-eroding pattern. Default to visible markers; consider a `Quiet delete` toggle later if requested.

## Related Documents

- [chat-history-fixes (1.13)](chat-history-fixes.md), [chat-history-deep-fixes (1.14)](chat-history-deep-fixes.md), [chat-history-deep-fixes-2 (1.15)](chat-history-deep-fixes-2.md) — sprint cluster this builds on.
- [resource-access-control (v6.0.0)](../v6.0.0/implemented/resource-access-control.md) — the owner-only-writes policy this sprint upholds.
- [chat-history (v6.0.0)](../v6.0.0/implemented/chat-history.md) — base feature.
- [Local Dev CLI (v6.1.0)](local-dev-cli.md) — host of the new CLI command.
- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — references the (app_name, user_id, session_id) triple invariant.
- [feedback memory: tests + self-verify](../../../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/feedback_test_and_self_verify.md) — discipline this sprint operates under.
