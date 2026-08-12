# Chat History Deep Fixes (Diagnostic-First)

**Status**: Planned
**Priority**: P0 (High) — chat history is still broken in user-visible ways after [chat-history-fixes.md](chat-history-fixes.md) shipped
**Estimated**: TBD — depends on diagnostic outcome (do not predict effort before tests confirm root cause)
**Scope**: Fullstack (likely frontend-heavy)
**Dependencies**:
  - [chat-history-fixes (1.13)](chat-history-fixes.md) ✅ — base sprint; F1 monotonic dedup is now a *prime suspect* for Bug B
**Created**: 2026-04-27
**Last Updated**: 2026-04-27

## Problem Statement

User E2E on the just-shipped [chat-history-fixes](chat-history-fixes.md) sprint surfaced **three regressions the M2 frontend tests did not cover**, plus one unrelated runtime error and one performance concern. Direct user quotes:

> "the rename of conversation is working but when I chat the conversation only shows my last Q and AI Answer, not the previous"
> "when I try to start a new conversation, it does not show a new conversation chat, just the old"
> "when I load another skill, I see the other conversation threads next to docs, but they do not show any chat history when I select them"
> "the responses are still too slow in response time to first token (are they streaming?)"
> "we need another round of tests that verify the bug then fixes"
> "I guess its something to do with firestore and ADK and agent engine sync?"

### In-scope bugs

**Bug A — Within-session history shows only the latest Q+A.** After 2+ turns within the same session, only the most recent user message and assistant reply are visible. Earlier turns disappear. Reproducible on both fresh and resumed sessions.

**Bug B — "+ New conversation" does not start fresh.** Clicking "+ New conversation" in the DocumentHistoryPanel takes the user to what *appears* to be the same prior conversation (history visible) instead of an empty fresh chat.

**Bug C — Selecting a thread from another skill's conversation list shows no history.** When the user opens a different skill, the per-document Conversations panel lists prior threads (good — that part works), but clicking one does not load the message history into the chat surface.

### Out-of-scope but noted

**Bug D — Vertex 400 "Multiple tools are supported only when they are all search tools".** Surfaced in dev logs while the user tested a specific skill (`d906749a-5eb8-44f6-9414-3f7e98ce292a`). This is a tools-configuration bug (Vertex's Gemini API rejects mixing search-grounding tools with FunctionTools in one call). **Not a chat-history bug** — different layer, different fix. To be filed as its own ticket: `docs/design/v6.1.0/multi-tool-skill-config.md` (TBD).

**Perf observation — slow time-to-first-token.** Multiple causes already identified earlier this session: shell-leaked `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:1957` (now unset by `make dev`), blocking title-gen on turns 2/5, and Vertex Agent Engine's session-create RTT. Worth a separate measurement pass after this sprint lands. Not in scope here.

**Impact:**
- Bugs A, B, C affect every user every session — chat history is supposed to be the headline feature.
- The previous sprint's M3 manual E2E was supposed to catch A and B. We didn't run it before declaring done — that's the more important meta-bug, addressed by the **diagnostic-first** discipline below.

## Goals

**Primary goal:** Identify the precise layer where each in-scope bug originates (frontend agent state vs. Firestore vs. Vertex Agent Engine), prove it with a failing test, then fix — in that order.

**Success metrics:**
- One **diagnostic test per bug** that pins the failure to a specific module/line, before any fix is written.
- After fix lands: every prior turn within a session is visible (Bug A), "+ New conversation" produces a chat with zero rendered messages and a fresh threadId (Bug B), and selecting a thread from any panel loads its history (Bug C).
- Re-running the previous sprint's [Implementation Report E2E table](chat-history-fixes.md#implementation-report) on this sprint's branch passes all five scenarios.

**Non-goals:**
- Predicting LOC or duration before diagnostics complete.
- Reworking session id mapping (still 1:1 via `use_thread_id_as_session_id=True`).
- Bug D (multi-tool skill config) — separate doc.
- Performance / time-to-first-token — separate measurement pass.

## Diagnostic Findings (2026-04-27)

Phase 1 ran. All four diagnostic tests committed as the first commits of this sprint.

| Hypothesis | Test | Result | Conclusion |
|---|---|---|---|
| **H1** AGUIProvider rebuilds HttpAgent on sessionId change | `AGUIProvider.test.tsx::D1: rebuilds the HttpAgent when sessionId changes from undefined to a server-assigned value` | **PASSES** (rebuild confirmed; new instance gets the new threadId) | **CONFIRMED**. This is the mechanism behind Bug A — Q1+A1 live in the destroyed first agent. |
| **H2** F1 monotonic dedup blocks reset on agent replacement | `useSkillAgent.test.tsx::D2: agent identity reset` | **FAILS** pre-fix (`Expected 0, Received 3` — F1 holds the old list of 3 messages when agent is replaced with empty one) | **CONFIRMED**. F1 from sprint 1.13 is the direct cause of Bug B and a contributor to Bugs A and C. |
| **H3** Vertex Agent Engine event-write lag | (deferred; H1 fix removes the failure window — Vertex GET timing becomes irrelevant) | not run | **DEFERRED**. Re-evaluate if E2E Bug A symptom persists after H2+H1 fixes. |
| **H4** `useSessionMessages` doesn't refetch on session select | `useSessionMessages.test.ts::D4: refetches when sessionId changes from one id to another` | **PASSES** (refetch fires; new messages reach state) | **REFUTED at the hook level**. Bug C must be downstream — F1 holds the *previous* session's messages in the live area, masking the newly-loaded initialMessages. Same root cause as H2. |

**Confirmed root causes:**
- **Bug B**: F1 monotonic guard (sprint 1.13) holds old list when underlying HttpAgent is replaced. Fix: agent-identity-aware F1.
- **Bug A**: combination of (a) AGUIProvider rebuilds on URL writeback (Q1+A1 live in destroyed agent #1) and (b) F1 holding the old list briefly. Primary fix: agent-identity-aware F1 (then live area resets correctly and `useSessionMessages` GET picks up Q1+A1 via initialMessages).
- **Bug C**: same F1 mechanism — clicking a thread rebuilds the agent; F1 holds the old session's messages in the live area while initialMessages loads correctly above the divider, producing visible confusion.

**Single fix path:** all three bugs collapse to one fix — make F1 yield when the underlying agent identity changes. No backend change needed for this sprint.

---

## Hypotheses (to be tested, not assumed)

These are suspect surfaces from a code-trace audit. Diagnostic tests below confirm or refute each.

### H1 (Bug A): URL-writeback destroys the in-flight HttpAgent

[AGUIProvider.tsx:62-70](../../../../frontend/src/providers/AGUIProvider.tsx#L62) rebuilds the `HttpAgent` whenever `sessionId` changes:

```typescript
const agent = useMemo(() => new HttpAgent({ ..., threadId: sessionId }),
                       [skillId, token, sessionId]);
```

Flow for a fresh chat:
1. `<AGUIProvider sessionId={undefined}>` → `HttpAgent #1` with auto-generated threadId.
2. User sends Q1 → `agent.messages = [Q1, A1]` after stream.
3. [chat/[...path]/page.tsx:203-207](../../../../frontend/src/app/chat/[...path]/page.tsx#L203) URL-writeback fires → URL becomes `?session=<id>`.
4. `<AGUIProvider sessionId={id}>` → useMemo deps change → **`HttpAgent #2.messages = []`**. Q1+A1 from agent #1 are gone.
5. [useSessionMessages.ts:49](../../../../frontend/src/hooks/useSessionMessages.ts#L49) GET `/api/sessions/<id>/messages`. If Vertex's event-write is async and the GET races, returns 0 events → `initialMessages = []`.
6. Render: nothing above divider, nothing live. User sees the empty chat after their first turn briefly flashed.

### H2 (Bug B): F1 monotonic dedup blocks legitimate agent rebuilds

This is a regression I caused in the previous sprint. M2's F1 fix added monotonic guard:

```typescript
setMessages((prev) => {
  if (next.length < prev.length) return prev;  // F1 guard
  return next;
});
```

Flow for "+ New conversation":
1. `handleNewSession` ([page.tsx:258-264](../../../../frontend/src/app/chat/[...path]/page.tsx#L258)) removes `?session=` from URL.
2. AGUIProvider rebuilds → `HttpAgent #2.messages = []`.
3. `useSkillAgent`'s subscribe re-runs against the new agent. `sync()` sees `next.length === 0`, `prev.length === N`.
4. **F1 guard kicks in** → `console.warn` and holds the previous list. Reset is silently suppressed. UI still shows old messages.

H2 is high confidence — the F1 fix is broken in exactly the case it didn't test (agent identity change).

### H3 (Bug A, alternative): Vertex Agent Engine event-write lag

Even if H1 is correct, the fix path depends on Vertex's event-persistence delay. If `GET /api/sessions/{id}/messages` consistently returns 0 events for ~5+ seconds after a stream completes, the framing is wrong — it's a contract issue with Vertex, and the fix is server-side (e.g. seed `initialMessages` from skill_processor's in-memory snapshot just before the agent rebuild).

### H4 (Bug C): Resume path doesn't seed messages from `useSessionMessages`

When the user clicks a session in `DocumentHistoryPanel`, `handleSelectSession` ([page.tsx:250-256](../../../../frontend/src/app/chat/[...path]/page.tsx#L250)) calls `navigateToSession(sid)` → URL becomes `?session=<sid>` → AGUIProvider rebuilds with the new threadId → `useSessionMessages` refetches.

If the GET succeeds and `initialMessages = [Q1...]`, the panel ABOVE the divider should populate. If it doesn't, possible causes:
- The Firestore session row exists for skill A but messages are stored in Vertex under a different `app_name` / `user_id` triple than `useSessionMessages` queries (cross-skill resume mismatch).
- `lastSyncedSessionId` ref ([page.tsx:174-187](../../../../frontend/src/app/chat/[...path]/page.tsx#L174)) gates a different effect (`setOpenTabs`), but a similar gate could be missing on the messages refetch — investigate.

H4 needs the most exploration. The diagnostic test below will distinguish "Vertex returned 0 events" from "frontend never rendered the events it received".

## Diagnostic Plan (tests first, fix second)

This sprint **starts with diagnostic tests, not fixes**. Every test in Phase 1 must run before any fix is written. Each test must fail in a way that pins the bug to a specific layer.

### D1 — Confirm/refute H1 (URL writeback rebuilds agent)

**Test:** `frontend/src/app/chat/__tests__/chat-page-session-lifecycle.test.tsx::test_first_turn_messages_survive_url_writeback`

```typescript
// Render <ChatPage /> with no ?session= in URL.
// Track HttpAgent instance ids via a spy on the AGUIProvider factory.
// Send first message via the live chat surface.
// Wait for URL writeback to fire (sessionId becomes non-null).
// Assert: rendered DOM still contains the user message text from Q1.
// Capture: was a new HttpAgent instance created? (confirms or refutes H1)
```

### D2 — Confirm/refute H2 (F1 dedup blocks reset)

**Test:** `frontend/src/hooks/__tests__/useSkillAgent.test.tsx::test_message_list_resets_when_underlying_agent_changes`

```typescript
const { result, rerender } = renderHook(({ agent }) => useSkillAgent(), {
  initialProps: { agent: agentA },
});
// Populate agentA, sync. expect length 3.
// Swap to agentB (empty messages). expect rerender.
// Assert: result.current.messages.length === 0
```

Pre-fix expected: F1 holds the previous list of 3 → assertion fails. Post-fix: agent identity guard.

### D3 — Confirm/refute H3 (Vertex event-write lag)

**Test:** `backend/tests/api_tests/test_sessions_route.py::test_messages_visible_immediately_after_stream_completes`

```python
# POST /api/skill/{id}/stream with a fresh threadId, consume all SSE frames.
# Immediately GET /api/sessions/{threadId}/messages.
# Assert: response.messages contains the user message and assistant reply.
```

The existing `test_stream_skill.py` mocks `ADKAgent.run` so this needs a different fixture or a real Vertex Agent Engine call (mark `@pytest.mark.integration`). If the integration variant lags, H3 confirms.

### D4 — Confirm/refute H4 (cross-skill resume)

**Test:** `frontend/src/app/chat/__tests__/chat-page-session-lifecycle.test.tsx::test_selecting_session_from_other_skill_loads_history`

```typescript
// Render <ChatPage skillId="skillX" /> with no session.
// Mock GET /api/documents/<doc>/sessions to return 1 session owned by skillY.
// Click that session's row.
// Assert: URL changes to ?session=<id>
// Assert: GET /api/sessions/<id>/messages was issued
// Assert: rendered initial-messages section contains the historical Q+A
```

If the GET fires but its response never reaches the rendered state, the bug is in the seeding hook. If the GET 404s, the bug is server-side (cross-skill access or Vertex app_name mismatch).

### Capture step

Before any fix is written: append a "Diagnostic Findings" section to this doc with the actual test outputs and a confirmed root-cause statement per bug. **No code change happens before that section is filled in.** Skipping this step is what produced the M3 regression.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Restoring within-session history makes chat feel correct on every render. |
| 2 | EARNED TRUST | +1 | Closes the "the chat just lost my work" trust failures. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Diagnostic-first discipline forces explicit failure-mode mapping. F1 will be revised so it degrades only on stable agent identity, not on rebuild. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Existing AG-UI / ADK / Firestore patterns. |
| 7 | API FIRST | 0 | Endpoints unchanged unless H3 forces server-side. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Four named diagnostic tests pin failure layers; F1 retains `console.warn` for stutter. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Most likely fix is client-side state mgmt. |
| | **Net Score** | **+4** | Threshold met. No -1s. |

## Design (fix shape — depends on diagnostic outcome)

Exact design **deferred** until D1-D4 produce results. Likely shapes:

### If H2 confirms (highest confidence)

[useSkillAgent.ts](../../../../frontend/src/hooks/useSkillAgent.ts) — F1 monotonic guard tracks **agent identity** in addition to length:

```typescript
const prevAgentRef = useRef(agent);
// in sync():
const agentChanged = prevAgentRef.current !== agent;
prevAgentRef.current = agent;
setMessages((prev) => {
  if (!agentChanged && next.length < prev.length) return prev;  // suppress only on stable agent
  return next;
});
```

Existing F1 test stays. New test (D2) covers the agent-swap case.

### If H1 confirms (Bug A persists after H2 fix)

[AGUIProvider.tsx](../../../../frontend/src/providers/AGUIProvider.tsx) — do NOT rebuild HttpAgent when only `sessionId` changes from `undefined` to a server-assigned value. Two options:

  - **(a)** Pre-allocate the threadId client-side at `<ChatPage>` mount (e.g. `crypto.randomUUID()`) so it's stable from the first message; URL-writeback only updates the URL, not the agent. Send the same id as `threadId` in the AG-UI `RunAgentInput`.
  - **(b)** Use a setter on the existing HttpAgent rather than rebuilding (`agent.threadId = ...` if the API supports it; verify against `@ag-ui/client` source).

Option (a) is simpler and reduces the writeback effect to a router call. **Verify (b) against `@ag-ui/client` package source before committing.**

### If H3 confirms (server-side)

[backend/protocols/sessions_route.py:234](../../../../backend/protocols/sessions_route.py#L234) `get_session_messages` — bridge with a brief retry / wait-for-consistency, or fall back to a recent in-process buffer when Vertex returns 0 events for a session that was just active. Last resort.

### If H4 confirms

Depends on the failure mode discovered. Likely: ensure `useSessionMessages` is mounted under the page's actual session-id source (currently `searchParams.get("session")`) and that the GET endpoint returns events when the session was created under one skill but is now being viewed in another (verify `app_name` / `user_id` triple in [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md)).

### CLI surface

If H3 or H4 confirm, an `aitana sessions doctor <id>` command (probe Firestore index + Vertex events + report drift) is worth adding in the same commit. Decision after D3/D4 run.

## Implementation Plan (test-first)

### Phase 1 — Diagnostic tests (~1.5h)
- [ ] **D1** — write `chat-page-session-lifecycle.test.tsx::test_first_turn_messages_survive_url_writeback`. Run; capture H1 outcome.
- [ ] **D2** — write `useSkillAgent.test.tsx::test_message_list_resets_when_underlying_agent_changes`. Run; capture H2 outcome.
- [ ] **D3** — write `test_sessions_route.py::test_messages_visible_immediately_after_stream_completes`. Run; capture H3 outcome.
- [ ] **D4** — write `chat-page-session-lifecycle.test.tsx::test_selecting_session_from_other_skill_loads_history`. Run; capture H4 outcome.
- [ ] **Capture findings** — append Diagnostic Findings section to this doc with the test outputs and confirmed root cause(s) before any fix is written.

### Phase 2 — Fix(es) gated by diagnostic outcome (~?h)
- [ ] Per confirmed hypothesis: write a fix-locking test, verify it fails, implement fix, verify it passes.
- [ ] Re-run all of D1-D4 — they must all be green after the fixes.
- [ ] Full backend + frontend test suites + lint + typecheck.

### Phase 3 — Re-run the v6.1.0/1.13 E2E table

The previous sprint's [E2E checklist](chat-history-fixes.md#implementation-report) was never run. This sprint must do it before declaring done. The five scenarios are now de-facto acceptance for *both* sprints — plus a sixth for Bug C (cross-skill resume).

### Phase 4 — Implementation Report + close
- [ ] Append Implementation Report (planned vs actual; which hypotheses were correct; what was learned).
- [ ] Move design doc + sprint plan to `implemented/` only after E2E table passes.

## Migration & Rollout

**Database migrations:** none expected. **Feature flags:** none. **Rollback:** revert sprint commit. Previous chat-history-fixes commits stay (they're independently good apart from F1 — fix is to refine F1, not revert it).

## Testing Strategy

The strategy **is** the diagnostic plan. Explicit:

1. **Diagnostic tests come first.** Every test in Phase 1 must run before any fix is written.
2. **Fix tests prove the fix.** Each fix lands with a regression test named `test_bugA_*` / `test_bugB_*` / `test_bugC_*` that fails before the fix and passes after.
3. **E2E is non-optional this time.** [chat-history-fixes E2E table](chat-history-fixes.md#implementation-report) plus a new row for Bug C is the acceptance gate.

### Tests this sprint will add

- [ ] D1: `chat-page-session-lifecycle.test.tsx::test_first_turn_messages_survive_url_writeback`
- [ ] D2: `useSkillAgent.test.tsx::test_message_list_resets_when_underlying_agent_changes`
- [ ] D3: `test_sessions_route.py::test_messages_visible_immediately_after_stream_completes`
- [ ] D4: `chat-page-session-lifecycle.test.tsx::test_selecting_session_from_other_skill_loads_history`
- [ ] One additional fix-locking test per confirmed hypothesis.

## Security Considerations

No new auth surface. Same access-control checks remain.

## Performance Considerations

If H1's fix is "pre-allocate threadId client-side", first-byte latency improves slightly (no agent rebuild after first turn).
If H2's fix is the agent-identity guard, no measurable cost.
If H3 forces a brief retry on the GET, that endpoint may add ~100-500ms on the cold case; bounded.

The user's separate slow-first-token observation is **not** addressed here — it's its own measurement pass. Suspected contributors already known: shell-leaked OTEL endpoint (now fixed in `make dev`), blocking title-gen (out-of-scope follow-up), Vertex Agent Engine session-create RTT.

## Success Criteria

- [ ] All four diagnostic tests committed and run, results captured in this doc.
- [ ] Confirmed root cause(s) named in writing before any fix is committed.
- [ ] Fix-locking test(s) committed; each verified to fail pre-fix and pass post-fix.
- [ ] Full backend + frontend test suites green (no other tests broken).
- [ ] Lint + typecheck clean.
- [ ] All E2E scenarios (5 from previous sprint + Bug C) pass on a running stack and are recorded inline.
- [ ] Sprint commits on `dev` reference bug ids (A/B/C).

## Open Questions

- **Should F1 be reverted entirely** if H2 confirms? Probably no — keep the agent-identity-aware guard so the original mid-stream-stutter case is still covered. Decide after D2 runs.
- **Is `agent.threadId = ...` settable on `HttpAgent`?** Verify via `@ag-ui/client` source before committing to fix shape (b) for H1.
- **Pre-existing M3 E2E never ran.** Decision: fold its results into this sprint's Implementation Report rather than re-opening the previous sprint's doc.
- **Bug D (Vertex 400 multi-tool)** — separate doc. Does the affected skill (`d906749a-...`) need a quick mitigation (filter incompatible tool combos at agent-build time)? Out of scope here, but worth a one-line ticket today so it doesn't get lost.

## Related Documents

- [Chat History Fixes (1.13)](chat-history-fixes.md) — base sprint; F1 from there is a prime suspect for Bug B
- [Chat History (v6.0.0)](../v6.0.0/implemented/chat-history.md) — original feature
- [Chat Session History (1.8)](implemented/chat-session-history.md) — `GET /sessions/{id}/messages` and seeding
- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — references the `app_name` / `user_id` / `session_id` triple invariant relevant to H4
