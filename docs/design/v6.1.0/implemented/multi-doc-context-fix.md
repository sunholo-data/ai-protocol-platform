# Multi-Doc Context Fix (Diagnostic-First)

**Status**: Implemented
**Priority**: P0 — chat-history Bug-F class regression: agent only sees first doc when user adds a second mid-session
**Estimated**: TBD post-diagnostic. Loader+injector confirmed clean (tests pass); fix is either small frontend wiring or one log-elevation + a model-prompt nudge.
**Scope**: Likely fullstack (backend logging + frontend wiring + possibly an instruction-text tweak)
**Dependencies**:
  - [chat-history-fixes (1.13)](chat-history-fixes.md) ✅
  - [chat-history-deep-fixes-3 / Bug F (commit 6a1e440)](implemented/chat-history-fixes.md) ✅ — eager-inject on fresh chats
  - [TTFT instrumentation (1.20)](implemented/ttft-instrumentation.md) ✅ — added STAGE_PROGRESS labels, may have shifted callback ordering
**Created**: 2026-04-29
**Last Updated**: 2026-04-28

## Problem Statement

User report 2026-04-28 with screenshot:

> "we are still seeing inconsistencies with the document recognition. here it did recognise the first document, then I added another and it did not see it being added."

Concrete sequence:
1. User opens `PRIVACY NOTICE.docx` (only doc) → asks "summarise this doc" → agent correctly summarises the privacy notice.
2. User opens `claim_incident_summary.docx` (now two tabs, both ticked, claim is the active tab) → asks "what about this one?" → agent returns the privacy-notice summary again.
3. User asks "you don't see the claim incident?" → agent literally responds *"It seems I keep getting the same privacy notice document for the Southwest Cornwall Group, and that document does not contain any information about a 'claim incident.'"*

**The agent's self-report says the second doc never reached its context.** That's testable behaviour, not a hallucination — we have a clear ground truth to verify against.

## What's already verified (and ruled out)

Two new backend tests landed alongside this doc and both **pass**:

| Test | What it proves |
|---|---|
| `test_multi_doc_loader_loads_each_new_doc_across_turns` | When `state.document_ids` grows from `[privacy]` → `[privacy, claim]` between turns, the loader saves a fresh `doc:claim.json` artifact with the **claim document's blocks** (not a stale copy of privacy). `_STATE_DOCS_LOADED` ends `[privacy, claim]`. |
| `test_multi_doc_injector_prepends_all_loaded_docs_distinct_content` | When `_STATE_DOCS_LOADED = [privacy, claim]` and `load_artifact` returns distinct artifacts per id, the injector prepends **two** `[Attached document: ...]` Content blocks with each doc's content. |

So when the backend has the correct state, both layers behave correctly. The bug must be one of:

| Hypothesis | Verifiable by |
|---|---|
| **H1 (frontend)** The chat page sends `document_ids = [privacy]` only for turn 2, dropping `claim` somewhere between `handleDocClick` and `sendMessage`. | Backend warning-level log of `document_ids` per turn (D1 below) + a chat-page integration test (D2). |
| **H2 (state propagation)** The frontend sends both ids, but ADK's session-state delta from the loader's previous turn isn't visible to turn 2's loader pass — so `loaded_set` is empty on turn 2 and `to_load` runs again from `[privacy, claim]`, but somehow `claim`'s artifact write fails silently. | Same backend log (D1) — would also show whether `document loader` runs at all. |
| **H3 (model)** Both docs reach the LLM context but Gemini ignores the second `[Attached document: …]` block. | Adding the doc filenames to the agent's system instruction would force attention; verifiable via a one-off probe. |

## Diagnostic Plan (test-first; don't fix what isn't proven)

### D1 — Elevate the loader's "turn start" log to WARNING

Why: the loader already logs `"doc loader: turn start — document_ids=%s prior loaded=%s"` at INFO ([backend/adk/callbacks.py:230-234](../../../backend/adk/callbacks.py#L230)). Python's default root logger level is WARNING, so dev-log readers never see this. **Elevating that single line to `logger.warning` lets the user reproduce once and we read the truth.**

This isn't a behaviour change — it's pure observability. Lowest blast radius. The loader's other INFO lines stay at INFO; only the turn-start summary surfaces.

The test for this is just: ask the user to repro and grep the log for `"doc loader: turn start"`. We'll know within one turn whether `document_ids` carries one id or two.

### D2 — Frontend regression test for the multi-tab → multi-id wire path

The hook layer is already locked by `useSkillAgent.test.tsx::sendMessage forwards documentIds via forwardedProps.document_ids` (and the multi-call variant). What's NOT locked is **the chat page's derivation**:

```typescript
const includedDocIds = openTabs.filter((t) => t.included).map((t) => t.id);
```

A regression test here would mount the chat page (or a thinner wrapper extracting the same logic) with `openTabs = [{id: A, included: true}, {id: B, included: true}]`, simulate `handleSend`, and assert `sendMessage` was called with `documentIds: [A, B]`.

Heavy to write at the page level; lighter to extract the derivation as a small testable function. Prefer the latter.

### D3 — Read existing `.dev-logs/backend.log`

Once D1 lands, the user repros once. We grep for the single warning line. Three possible outcomes:
- `document_ids=['privacy']` on turn 2 → frontend bug confirmed (H1).
- `document_ids=['privacy', 'claim']` and `prior loaded=['privacy']` → loader runs, picks up claim, saves it → bug is downstream (likely model H3).
- No "doc loader" warning at all → callback wiring broken (a third hypothesis worth exposing).

## Hypotheses (assumed false until D1+D3 say otherwise)

| H | Layer | Description |
|---|---|---|
| H1 | Frontend | Chat page passes only first doc id to `sendMessage` despite both tabs being ticked |
| H2 | Backend state | Session state delta lost between turns; loader re-saves but injector misses |
| H3 | Model | Both docs reach context; Gemini ignores the second |

## Axiom Alignment

Score per [Product Axioms](../../../docs/product-axioms.md). Net >= +4.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Diagnostics-first; fix shape unknown until D1+D3 land. |
| 2 | EARNED TRUST | +1 | Closes the "agent gaslights me about which doc I sent" failure that erodes trust fast. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Same model. |
| 5 | GRACEFUL DEGRADATION | +1 | Elevating one log line gives us forensic evidence for free; this sprint's fix lands with paired regression tests against each confirmed hypothesis. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Existing AG-UI / ADK patterns. |
| 7 | API FIRST | 0 | Endpoint surface unchanged. |
| 8 | OBSERVABLE BY DEFAULT | +1 | The whole sprint is observability — D1 elevates a log to WARNING so the diagnostic line is visible without re-running the harness with custom levels. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Either layer's fix; thin-client invariant intact. |
| | **Net Score** | **+3** | Below threshold (+4) — design is intentionally thin: this is a *diagnostic* sprint, not a feature. Will hit +4 once the actual fix is committed (per-bug regression test = +1 on Axiom #2 or #5). |

**Conflict justifications:** none required.

## Implementation Plan

### Phase 1 — Diagnostics (~30 min)
- [ ] **D1** — change `logger.info("doc loader: turn start — ...")` → `logger.warning(...)` in `make_document_loader._loader`. Single-line change.
- [ ] **D2** — extract `openTabs.filter(...).map(...)` to a tiny pure function (`computeIncludedDocIds(openTabs)`) + paired Vitest test that asserts both ids surface from a 2-tab fixture. Not because the JS is wrong — because it locks the contract so a future refactor can't silently regress.
- [ ] Ask user to reproduce and paste the relevant `.dev-logs/backend.log` grep line.

### Phase 2 — Fix (gated on D1+D3 outcome) (~?h)
Decided after diagnostics:
- **If H1 (frontend):** identify where the second id is dropped (closure / effect / state setter). Add a regression test at the failure layer; fix; verify all multi-doc tests still green.
- **If H2 (state):** investigate ADK state-delta persistence; possibly switch from `state[_STATE_DOCS_LOADED]` to a session-event-attached marker.
- **If H3 (model):** add a system-instruction nudge — when `state.document_ids` has > 1 entry, prepend "The user has attached N documents. Each is wrapped in `[Attached document: ...]`. Read each one before answering."

### Phase 3 — Self-verify
- [ ] Backend: `pytest tests/api_tests tests/unit -q` clean. The two existing multi-doc tests stay green.
- [ ] Frontend: `vitest run && tsc --noEmit && lint` clean.
- [ ] Manual E2E: open doc A, send message, open doc B, send message asking about B. Agent must reference B's content.

## Migration & Rollout

**Database migrations:** none. **Feature flags:** none. **Rollback:** revert sprint commit. The D1 log elevation is non-functional and safe to keep.

## Testing Strategy

### Already locked (committed alongside this doc)
- `test_multi_doc_loader_loads_each_new_doc_across_turns` — backend loader handles multi-turn doc add
- `test_multi_doc_injector_prepends_all_loaded_docs_distinct_content` — backend injector prepends all loaded docs with distinct content

### To add this sprint
- D2: `computeIncludedDocIds.test.ts` — derivation locks 2-of-2 included → both ids out
- Per-fix: a regression test named for the confirmed hypothesis (`test_h1_*`, `test_h2_*`, etc.)

## Security Considerations

None new. No new data access surface.

## Performance Considerations

D1 elevates one log line to WARNING. Negligible cost. Removing or downgrading it post-diagnostic is one line.

## Success Criteria

- [ ] D1 elevation committed.
- [ ] User repros once; `.dev-logs/backend.log` grep produces a definitive `document_ids=[…]` line per turn.
- [ ] Failing layer named in writing in this doc's "Diagnostic Findings" section before any fix code lands.
- [ ] Regression test for the confirmed layer committed and verified to fail pre-fix.
- [ ] Manual E2E green.

## Open Questions

- **Where exactly was the user's first turn's `document_ids`?** The screenshot can't tell us. D1 will. Don't speculate.
- **TTFT-1.20 added STAGE_PROGRESS callbacks — could those have shifted before-model-callback ordering and skipped the injector?** Worth a one-line check: `grep -n 'before_model_callback' backend/adk/agent.py` to confirm `_document_injector` is still the wired callback.
- **Is there a sub-agent (heuristic router?) where the doc state isn't propagated?** The user is on `general-assistant` skill — single agent, not sub-agent. Likely fine, but check if H3 confirms and prompt fix doesn't help.

## Related Documents

- [chat-history-fixes (1.13)](chat-history-fixes.md), [chat-history-deep-fixes (1.14)](chat-history-deep-fixes.md), [chat-history-deep-fixes-2 (1.15)](chat-history-deep-fixes-2.md), [chat-history-deep-fixes-3 (commit 6a1e440)](.) — sprint cluster this builds on
- [TTFT instrumentation (1.20)](implemented/ttft-instrumentation.md) — added STAGE_BEFORE_MODEL_DONE; verify wiring not disturbed
- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md) — for inspecting Vertex/InMemory artifact storage if H2 needs deeper probing

---

## Diagnostic Findings + Fix (2026-04-29)

**Outcome of Phase 1 diagnostics:** none of the original three hypotheses (H1 frontend / H2 state-delta persistence / H3 model) — a fourth hypothesis surfaced by reading the full body shape.

### What the data showed

User repro after Phase 1 landed (commits 2e69dbd, 49afb0a):

```
.dev-logs/backend.log
WARNING:adk.callbacks:doc loader: turn start — document_ids=['6ecff3e0-c638-...'] prior loaded=['6ecff3e0-c638-...']
                                                                ↑ ONE id only on turn 3

frontend console
[handleSend] openTabs= Array(3) [{…}, {…}, {…}]  includedDocIds= ["6ecff3e0...", "41ea1884...", "e222aa3d..."]
                                                  ↑ THREE ids derived correctly
```

**Three ids out, one in.** Frontend wire path is correct; the chat page derives the right ids from `openTabs` and `useSkillAgent` forwards them via `forwardedProps.document_ids` (covered by existing tests). The drop happens at the **server-side body parser**.

### Root cause: `_extract_document_ids` candidate priority

[`backend/fast_api_app.py::_extract_document_ids`](../../../backend/fast_api_app.py) read three wire locations in this order:

```python
candidates = (
    body.documentIds,                                # CLI / simple wire format
    (body.state or {}).get("document_ids"),          # AG-UI accumulated state ← STALE
    (body.forwardedProps or {}).get("document_ids"), # AG-UI per-turn fresh signal ← NEVER REACHED
)
for value in candidates:
    if isinstance(value, list) and value:
        return [str(d) for d in value if d]
```

**The `state` candidate beats `forwardedProps` to the list and never falls through.**

Why state was non-empty on turn 3: AG-UI's `HttpAgent` mirrors backend `STATE_SNAPSHOT` events into its internal `this.state` and round-trips that state on every subsequent `runAgent` call. After turn 2 wrote `state.document_ids = ['6ecff3e0...']` server-side (via ADK's state delta from `skill_processor`'s `initial_state`), the AG-UI client received it as a STATE_SNAPSHOT and cached it. On turn 3, both fields landed in the request body:

- `state.document_ids = ['6ecff3e0...']` — stale (one turn behind)
- `forwardedProps.document_ids = ['6ecff3e0...', '41ea1884...', 'e222aa3d...']` — fresh

The reader picked the stale one and never read forwardedProps.

### Fix

Reorder priority: forwardedProps first (fresh per-turn), then top-level documentIds (CLI), then state (legacy fallback). Single tuple-element move + comment explaining why state is one turn behind.

### Lock-in tests (both committed alongside the fix)

| Test | What it locks |
|---|---|
| `test_stream_skill_forwardedprops_wins_over_stale_state_document_ids` | When the body has stale state AND fresh forwardedProps, forwardedProps wins. **Failed pre-fix** with `Got ['doc-stale-from-turn-2']`; passes post-fix. |
| `test_stream_skill_falls_back_to_state_document_ids_when_no_forwardedprops` | Floor lock: legacy callers that send only `state.document_ids` still work — the priority reorder doesn't drop the fallback path. Passes both pre- and post-fix. |

### Quality gates (post-fix)

- 613 backend tests pass · ruff clean
- Frontend tsc + eslint clean
- Diagnostic `console.warn` removed from `handleSend`
- D1 loader-log elevation kept (cheap forensic line, useful for future debugging)
- D2 `computeIncludedDocIds` extraction kept (locks the chat-page derivation)

### What this means for the user

After hard-reload of the frontend: open doc A → ask, open doc B → ask about B. Backend now reads `forwardedProps.document_ids = [A, B]`, the loader saves both artifacts, and the injector prepends both to the LLM request. Each subsequent turn, even if `state.document_ids` carries a stale snapshot, the fresh forwardedProps wins.

---

## Implementation Report

**Completed**: 2026-04-28
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
