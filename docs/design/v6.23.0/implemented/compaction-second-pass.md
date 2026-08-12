# Compaction Second Pass — recompact idle sessions with time to think

**Status**: Implemented (infrastructure live on dev since 2026-08-10)
**Priority**: P1 — the quality ceiling on live compaction is its latency budget; this removes the ceiling
**Estimated**: ~2.5d (M1 ~1d route + handler, M2 ~0.75d enqueue + concurrency, M3 ~0.75d env wiring + live verification)
**Scope**: Backend + CLI (infra already applied; frontend follow-up noted, not blocking)
**Dependencies**: [compaction-off-the-critical-path](compaction-off-the-critical-path.md) (shipped — predicted this doc: *"reach for (b) when a second deferred job appears"*) · composes with [compaction-strategy-hooks](compaction-strategy-hooks.md) (soft — see Design)
**Created**: 2026-08-10
**Last Updated**: 2026-08-10

## Problem Statement

Live compaction runs inside the user's turn, so its quality is capped by what a
user will wait for. That cap is real and measured (findings log §2): the
post-invocation tail plateaus ~45s, and ~22% of compactions silently produce
nothing (§3.2) with no retry — context keeps growing while the logs say work
happened.

Meanwhile the input for doing better is sitting there free:

1. **The raw events survive compaction** (§1). The summary replaces them only
   in the model's request; the session store keeps everything. A better summary
   can always be re-derived from the originals — live compaction quality only
   has to be *adequate until the session goes idle*.
2. **Idle time is abundant.** ONE's expert sessions run 90 minutes and resume
   next day. Between those is an unbounded compute window nobody is waiting in.

So: keep the live pass fast (it keeps the conversation flowing), and add a
**second pass** that runs when the session goes quiet — a `pro`-tier,
specifics-preserving summarisation over the **raw events** (not the rolling
summary-of-summary seed), superseding the live summary in place. A resumed
session starts from the considered version.

This also converts §3.2's silent no-ops from a quality leak into a retried job
with guaranteed progress.

**Infrastructure is already live on dev** (2026-08-10, `multivac-aitana`
`environments/dev/compaction_tasks.tf`): queue `platform-compaction`
(europe-west1, 1 dispatch/s, 2 concurrent, 8 retries over 12h) and a dedicated
no-role OIDC SA `platform-tasks@`. Cloud Tasks over Pub/Sub because the job is
a *scheduled command with one consumer*: per-task `scheduleTime` is the idle
timer, the 30-min dispatch deadline covers a slow summarisation (Pub/Sub push
acks cap at 10 min), and queue rate limits govern model cost. A task delivery
is its own HTTP request, so it gets full CPU under request-based billing — the
constraint that killed fire-and-forget in the predecessor doc.

## Verified ADK seams (pinned 1.31.1 source, not the MCP index)

The design stands on four facts checked in `backend/.venv` — google-adk 1.31.1:

1. **Supersede is native.** `_is_compaction_subsumed`
   (`apps/compaction.py:66`): a compaction whose range is fully contained in
   another's is dropped at request-build time; **on identical ranges the
   later-appended event wins**. Appending a better summary over the same or a
   wider range replaces the old one with zero custom filtering.
2. **The next live compaction seeds from the winner.** `_latest_compaction_event`
   (`apps/compaction.py:142`) skips subsumed compactions, and candidate
   selection (`_events_to_compact_for_token_threshold`, `:205`) takes its
   rolling seed and `last_compacted_end_timestamp` from that same non-subsumed
   winner. After a second pass, subsequent live compactions build on the
   improved summary — no double-seeding, no divergence.
3. **The write is the public API.** ADK itself lands compactions via
   `session_service.append_event(session, compaction_event)`
   (`apps/compaction.py:390`). The event shape is small and stable
   (`llm_event_summarizer.py:123`):
   `Event(author='user', invocation_id=Event.new_id(), actions=EventActions(compaction=EventCompaction(start_timestamp, end_timestamp, compacted_content)))`.
   No underscore-prefixed function is needed for the write — the handler does
   its own candidate selection, which is where ADK's privates live.
4. **One selection rule must be honored:** events carrying **pending function
   calls** (a call with no matching response, `_pending_function_call_ids`,
   `:281`) must not be compacted. The handler applies the same guard.

Per the [adk-contract-checklist](../v6.17.0/adk-contract-checklist.md), this is
a new custom↔ADK seam and owes a hermetic real-ADK-flow guard under
`make adk-conformance` (see Testing).

## Goals

**Primary Goal:** An idle session's compacted history is re-derived from raw
events at full quality, invisibly and safely, before the user returns.

**Success Metrics:**
- A superseding compaction event lands within ~1h of a qualifying session going
  idle, and the next model request provably contains the new summary only.
- §3.2 class failures no longer terminal: a no-op or crashed second pass is
  retried by the queue until the append lands (or the 12h horizon expires).
- Second-pass summary beats the live one on the replay diff for the same
  session (specifics preserved, environment state and opaque ids absent).
- Zero ms added to any user-facing turn (measured, as in the predecessor doc).

**Non-Goals:**
- Changing the live pass. (A cheaper live strategy becomes *safe* once this
  lands — that decision belongs to [compaction-strategy-hooks](compaction-strategy-hooks.md).)
- Cross-session distillation into user/document/group scopes. This doc builds
  the deferred-execution rail; the distiller is a separate design.
- A frontend surface. The superseding event is visible in traces and
  `aiplatform session compaction`; an Activity "context refined" marker on
  resume is a noted follow-up.

## Design

### Trigger — enqueue when a live compaction lands, newest task wins

> **Revised during M2 (2026-08-10), superseding the drafted every-turn-end
> design.** The second pass covers exactly the span live compaction has
> claimed; new raw turns don't change that span — so a per-turn enqueue is
> noise, and its "any newer event → stale" guard had a real gap (a task staled
> by a non-compacting turn left the span unimproved until the *next* live
> compaction). Enqueue-on-compaction also removes the size-floor heuristic:
> no compaction → nothing to improve → no task.

The hook lives in `FidelityEventSummarizer.maybe_summarize_events`, at the
moment a live compaction is produced (same seam as `HISTORY_COMPACTED`); the
request-scoped `LatencyTracker` supplies `session_id`/`user_id`, so the hook
works on every SSE path that compacts (chat, `surface-action-run`) without
stream-layer surgery. One live compaction → one task:

```
task name:     recompact-{session_id}-{compaction_end_ms}
scheduleTime:  now + COMPACTION_SECOND_PASS_IDLE_SECS (default 2700 = 45 min)
payload:       {"session_id", "user_id", "compaction_end_ts"}   # ids only, no content
```

Nothing is deleted or rescheduled — task names are unique per live compaction,
so a retried turn's duplicate enqueue is a benign `AlreadyExists`, and the
design never touches the ~1h task-name tombstone. The handler's guards give
exactly-once *effect* from at-least-once delivery:

- session's latest non-subsumed compaction ends **later** than the task's
  `compaction_end_ts` → a newer live compaction exists and enqueued its own
  task → 200 no-op;
- latest non-subsumed compaction carries the **second-pass marker**
  (`custom_metadata["aitana_compaction_second_pass"]`, a field verified on
  ADK's `Event` via `LlmResponse`) → duplicate delivery after success → 200
  no-op. Live compactions never carry the marker, which is how "already done"
  stays distinguishable from "a newer live compaction deserves its own pass".

Enqueue failures are logged and dropped (fail-soft — it runs inside the
post-invocation path of a user's request); the next live compaction
re-enqueues, and the (future) nightly sweep is the backstop.

### Handler — `POST /internal/tasks/recompact`

On the existing backend service (same image, same model chains, same session
service — no new deploy unit):

1. **Verify OIDC** — the service is public, so Cloud Run IAM cannot gate this
   route; the handler verifies the token itself: issuer
   `https://accounts.google.com`, audience = this route's URL, email =
   `COMPACTION_TASKS_OIDC_SA`. Only the queue path can mint that identity —
   the SA has zero roles and only `sa-platform` may actAs it. Same gate shape
   as the SEC-1 admin-auth work; reuse, don't reinvent.
2. **Load the session** via `get_session_service()` and run the staleness
   check.
3. **Select the range**: all events from the session's first up to the latest
   existing (non-subsumed) compaction's `end_timestamp` — i.e. re-derive
   exactly what live compaction has already claimed, from the raw originals.
   Never past the retention floor, never across a pending function call. If no
   compaction exists yet, no-op: the second pass improves summaries, it does
   not introduce them early.
4. **Summarise from raw** with the second-pass strategy (below), producing
   `compacted_content`.
5. **Append the superseding event** with the selected range. By seam #1 it
   subsumes every prior compaction it covers; by seam #2 future live
   compactions seed from it. The append is the last statement before the 200 —
   a crash anywhere earlier means the task retries and nothing half-applied
   exists (the same atomicity that made the predecessor doc's M2 safe).
6. **Record it**: structured log + OTel span with `events_covered`,
   `summary_chars`, strategy + model, and the superseded event count — the
   offline sibling of `HISTORY_COMPACTED` (which is a stream event and has no
   stream here).

Concurrency needs no lock: if a user resumes mid-handler and a live compaction
lands between our read and our append, the two events simply coexist — ours
subsumes the *old* ones, the new live one covers a later span, and the contents
filter resolves both consistently. Worst case is a wasted summarisation, retried
against fresher state next time.

### The second-pass strategy

The live `FidelityEventSummarizer` compacts *incrementally* — each pass sees
the previous summary plus new events, so errors and omissions compound. The
second pass instead reads the **full raw range** in one context with a `pro`
model and the fidelity prompt's findings-vs-environment rules. When
[compaction-strategy-hooks](compaction-strategy-hooks.md) lands its registry,
this becomes registered strategy `second-pass-fidelity` selectable like any
other (and forks can override it); until then it is a direct use of
`FidelityEventSummarizer` over the raw range — the dependency is soft in both
directions.

### CLI Surface

Per [local-dev-cli.md](../v6.1.0/local-dev-cli.md), the dev loop ships with the
feature, extending the existing `aiplatform compaction` group:

```
aiplatform compaction recompact <session-id> --user-id <uid> [--dry-run]
aiplatform compaction recompact <session-id> --user-id <uid> --enqueue
```

`--dry-run` prints the proposed summary and range without appending (reuses the
replay machinery); the default performs the append via the internal route logic
directly; `--enqueue` exercises the real queue path end-to-end on dev.

### Configuration

All via env vars on both backend containers (sidecar + standalone), set in
`multivac-apps` `run_client.tfvars` **dev branch only** for the trial:

| Var | Meaning | Dev value |
|---|---|---|
| `COMPACTION_SECOND_PASS_ENABLED` | master switch; absent = off (test/prod state) | `true` |
| `COMPACTION_TASKS_QUEUE` | full queue path | `projects/your-project-id/locations/europe-west1/queues/platform-compaction` |
| `COMPACTION_TASKS_OIDC_SA` | expected token identity | `platform-tasks@your-project-id.iam.gserviceaccount.com` |
| `COMPACTION_TASKS_TARGET_URL` | route URL = OIDC audience | standalone backend URL + path |
| `COMPACTION_SECOND_PASS_IDLE_SECS` | idle timer | `2700` |

Flag-off is total: no enqueue, and the route 404s. Rollback = unset the flag;
already-appended superseding events are ordinary compaction events and remain
valid under any code version.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Adds 0ms to any turn, and makes a cheaper/faster *live* strategy safe to adopt later. |
| 2 | EARNED TRUST | +1 | The resumed session remembers what was actually said — re-derived from originals, not summaries of summaries; §3.2's silent context loss becomes a retried job. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure beneath every skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | The doc's core: `pro` where nobody waits, without paying its latency in-turn. |
| 5 | GRACEFUL DEGRADATION | +1 | Atomic append + queue retries; a failed pass leaves the live summary in place — never worse than today. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Rides ADK's own subsume semantics and public `append_event`; GCP-native queue; no parallel history mechanism. |
| 7 | API FIRST | +1 | Internal route + CLI recompact/dry-run/enqueue. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Structured log/OTel per pass; visible in `session compaction` triage; findings-log rows owed on every measurement. |
| 9 | SECURE BY CONSTRUCTION | 0 | New ingress path, gated by a dedicated no-role OIDC identity verified in-app; queue payload is ids-only; summaries stay in the session store. Scored 0, not +1: it *is* new attack surface, mitigated not absent. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Entirely server-side. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scored -1).

## Implementation Plan

### M1 — route + handler (~1d)
- [ ] OIDC verification dependency (reuse SEC-1 gate pattern) + 401/403 tests (~80)
- [ ] Handler: staleness check, range selection (subsume-all + retention floor + pending-call guard), summarise-from-raw, append, structured log (~150)
- [ ] `aiplatform compaction recompact` with `--dry-run` (~60)
- [ ] Hermetic adk-conformance guard for the supersede seam (~80)

### M2 — enqueue + concurrency (~0.75d)
- [ ] Turn-end enqueue (size floor, unique task names, fail-soft) behind the flag (~70)
- [ ] Staleness no-op + newest-task-wins tests; concurrent live-compaction interleave test (~100)
- [ ] `--enqueue` mode exercising the real queue on dev (~30)

### M3 — wiring + live verification (~0.75d)
- [ ] Env vars in `multivac-apps` `run_client.tfvars` (dev branch) (~10)
- [ ] Live: forced-low-threshold session on dev → idle → superseding event lands; replay-diff live vs second-pass summary; next turn's request contains only the new summary (~0)
- [ ] Findings-log rows (§2 numbers, §7 log) + memory update

## Testing Strategy

### Backend (pytest)
- [ ] Range selection: covers all prior compactions, respects retention floor, stops before pending function calls, no-ops when nothing to improve
- [ ] Staleness: newer session activity → 200 no-op, no append
- [ ] OIDC gate: wrong audience / issuer / SA / missing token → 401/403; flag off → 404
- [ ] Append-last ordering: a summariser failure appends nothing (task retries)

### ADK conformance (hermetic, real ADK flow)
- [ ] Build a session with events + a live compaction, append a superseding
      event, run the real contents pipeline: model request contains **only**
      the new summary; `_latest_compaction_event` selects it as the next seed.
      This is the guard that breaks loudly if an ADK bump changes the subsume
      contract (Rule 3 of the deploy skill).

### Live (non-negotiable — unit-green is not proof)
- [ ] The M3 end-to-end on dev, asserting the superseding event on the
      wire-of-record (session events), not inferring it

## Security Considerations

The queue payload carries identifiers only — session content never transits
Cloud Tasks. Summaries derive from customer conversation content and stay in
the session store behind existing auth (CLAUDE.md hard rule; same posture as
the live summarizer). The internal route is the one new surface: it is gated by
a token only mintable through the queue (no-role SA, actAs restricted to
`sa-platform`), verified in-app because the service must remain public. It
mutates nothing a live compaction doesn't already mutate, for a session it can
already read.

## Performance Considerations

Zero user-facing cost by construction (the enqueue is a fire-soft ~10ms HTTP
call at turn end; everything else runs in a task's own request). Model cost is
bounded by the queue's 1 dispatch/s + 2 concurrent, and each session costs at
most one full-range `pro` summarisation per idle period — the staleness check
prevents per-turn amplification. The 30-min dispatch deadline bounds a
pathological pass; the range is capped by the same token limits as the live
summariser's input handling.

## Success Criteria

- [ ] Backend tests passing (`cd backend && make test-fast`), lint clean (`make lint`)
- [ ] `make adk-conformance` green including the new supersede guard
- [ ] `aiplatform compaction recompact --dry-run` works against a recorded dev session
- [ ] Live M3 verification on dev: superseding event lands after idle; next
      model request contains only the new summary; replay diff shows the
      second pass beating the live summary
- [ ] Findings log updated with measured numbers (no undated claims)

## Open Questions

- **Should the second pass eventually run a different strategy per skill** (e.g.
  `facts-only` for ONE's contract work)? Deferred to the strategy-hooks registry
  landing; the seam is ready.
- **Nightly sweep backstop** (Cloud Scheduler → enqueue for sessions the
  idle-trigger missed): worth ~0.25d once the route exists. Not in scope here.
- **Resume-time "context refined" Activity marker** — NEVER SILENT arguably
  applies to an invisible context improvement. Follow-up with the frontend.
- **Does the second pass become the distillation point** for user/document/group
  scoped memory? That is the intended evolution (separate design), and is why
  the handler's structured output should stay strategy-shaped rather than
  summary-string-shaped.

## Related Documents

- [Findings log](../../projects/compaction/README.md) — mechanism map (§1), measured numbers (§2), the §3.2 no-ops this retries, infra row (§7)
- [compaction-off-the-critical-path](compaction-off-the-critical-path.md) — shipped predecessor; its option (b) trigger condition is this doc
- [compaction-strategy-hooks](compaction-strategy-hooks.md) — the registry this composes with
- [adk-contract-checklist](../v6.17.0/adk-contract-checklist.md) — the seam rules the conformance guard enforces
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI conventions

---

## Implementation Report

**Completed**: 2026-08-10
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- M1–M3 as designed (`7ed0ff1`, `eaf2dbf`, `5159506` + three infra commits across
  `multivac-aitana`/`multivac-apps`), dev-only, flag-gated, same day as the doc.
- **Deviation 1 — trigger.** The drafted every-turn-end enqueue was replaced
  during M2 by enqueue-on-compaction at the `HISTORY_COMPACTED` seam (see the
  revision note in Design). The per-turn design's "newer event → stale" guard
  had a real gap; the shipped semantics compare compaction ends and use a
  `custom_metadata` second-pass marker for duplicate deliveries.
- **Deviation 2 — auth.** The standalone backend turned out to be IAM-gated
  (SA invokers), so the task passes Cloud Run IAM *and* the in-app OIDC gate —
  strictly stronger than designed. The tasks SA got `run.invoker` in the
  dev-only tf file.

### Live verification (2026-08-10, deployed dev)
Against `m0-latency-e2e7aab5cb` — the findings-log §3.1 session (117 events,
5 live compactions): `--enqueue` drove a real zero-delay Cloud Tasks delivery
through queue → Cloud Run IAM → OIDC gate → superseding append. Result: 6th
compaction event, widest range (identical to live #5 → later-wins), marker
survives the Vertex round-trip. Read 48 raw events / 35,086 chars in 33s on
`pro`; produced **9,506 chars vs the live rolling summary's 15,378** — smaller
while reading more original material. Supersede-filter behaviour is pinned by
the `adk_contract` guard running ADK's real contents pipeline.

### Files Changed
- New: `backend/adk/compaction_second_pass.py`, `backend/internal_tasks/`
  (auth, enqueue, recompact_routes), `backend/admin/compaction_second_pass_routes.py`,
  `tests/unit/test_compaction_second_pass.py`, `tests/unit/test_second_pass_enqueue.py`,
  `tests/api_tests/test_internal_tasks_recompact.py`,
  `multivac-aitana …/dev/compaction_tasks.tf`
- Modified: `fast_api_app.py`, `adk/compaction_summarizer.py` (enqueue hook),
  `observability/timing.py` (tracker id properties), both `cloudbuild.yaml`s,
  `cli/…/compaction.py`, `test_adk_native_route_guard.py` (`/internal/` +
  companion proof), `multivac-apps run_client.tfvars` (dev)

### Lessons Learned
- The SEC-1 route-coverage guard caught the new `/internal/` prefix exactly as
  intended — extending it demanded a companion fail-closed proof, which is the
  guard working, not friction.
- Enqueue-time context came free from the request-scoped `LatencyTracker`
  (session/user ids at the summarizer seam) — no stream-layer surgery.
- Still owed (tracked in the findings log): the answer-quality delta — §4's
  fact-survival eval applied to second-pass vs live summaries — and the
  nightly sweep backstop + resume-time Activity marker from Open Questions.
