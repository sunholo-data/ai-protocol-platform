# Trace Completeness & Access — finish replacing Langfuse before we switch it off

**Status**: Planned
**Priority**: P1 (Medium) — gating a commitment already made to the customer; blocks Dana's day-to-day support role
**Estimated**: ~1.5 days (Phase 1 investigation ~0.5d, Phase 2 ~0.5d, Phase 3 ~0.5d)
**Scope**: Fullstack
**Dependencies**: None. Touches `frontend/src/app/admin/analytics/page.tsx` and the backend `/sessions` trace endpoints.
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

The v6 admin analytics view is the declared replacement for Langfuse. At the
2026-08-06 UAT, Mark demoed it and stated the plan plainly — "I'm thinking
replace Langfuse with this" — then immediately qualified it:

> "You can see the tool calls and all this... **some of them are not populating
> correctly yet. So that's why I'm still working on that.** That's why I'm not
> giving it big access."

Two commitments were made in that conversation that this doc has to make true.

**1. Dana needs access, and she is blocked without it.** Tomas explained why
it matters operationally:

> "I had this issue with my non-access to my prompt, and I asked Dana if she
> could check something I couldn't... Once you see if she's more or less
> working, we can also give her access to help us with the traces, because she's
> really useful for us — she can tell me immediately when it's been done."

Dana closed the meeting on the same point: *"Can you just notify me when I have
access to the traces?"* Support triage at ONE currently routes through Mark
because nobody else can see a trace.

**2. Errors must be visible in the trace, not in Cloud Logging.** Dana's
strongest complaint about the *old* tooling is a requirement for the new one:

> "Sometimes in Langfuse we had an error and I cannot see the error in there. So
> I had to go to Cloud Logging and for the messaging ID and look there for the
> error. **I think it will be easier if everything is in there, if it's possible.**"

Mark agreed with a caveat worth preserving honestly — some backend errors
genuinely cannot be surfaced client-side — and set the bar at *"enough that you
can debug 90% of what you need on a day-to-day basis."*

### Current state

`frontend/src/app/admin/analytics/page.tsx` (v6.9.0) already does the hard
structural work: it lists sessions from the `chat_sessions` mirror, opens a
trace of messages/tool calls/delegations, and — to its credit — already renders
loading, empty, forbidden and error states (principle #8). What it does not yet
do is populate reliably, and Mark has seen it fail on his own sessions.

**The root cause is not yet known.** This doc deliberately does not guess one.
Candidate hypotheses, to be discriminated in Phase 1:

- the Firestore `chat_sessions` mirror diverging from ADK's canonical session
  store (a known-live hazard — see `gotcha_session_artifact_persistence_mismatch`)
- tool calls written under a different session id than the one the trace queries
- events lost when a run ends abnormally (`RUN_ERROR`, cancellation, timeout)
- a per-env difference in what gets mirrored

**Impact:**
- **Who:** Mark (today, debugging blind spots), Dana and Tomas (blocked from
  self-service triage entirely).
- **How significant:** blocker for switching Langfuse off, which is a decision
  already communicated to the customer. Shipping the cutover with a half-populated
  replacement would be a visible regression in their support workflow.

## Goals

**Primary Goal:** A ONE super-user can diagnose 90% of day-to-day issues from
the admin trace view alone, without Cloud Logging and without asking Mark.

**Success Metrics:**
- 100% of tool calls in a session appear in its trace (today: unquantified, and
  observed missing — quantifying this is Phase 1's first deliverable).
- Every user-visible error has a corresponding, readable trace entry.
- Dana has scoped access and completes a real triage unaided.
- Langfuse can be switched off with no loss of day-to-day capability.

**Non-Goals:**
- Feature parity with Langfuse. Mark set the bar at 90% of daily need, not parity.
- Surfacing every backend error client-side — some legitimately cannot be, and
  the honest answer is a trace entry saying an error occurred with a correlation
  id, not silence.
- Replacing Cloud Trace/Logging. They remain the deep-dive tier.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Admin surface; not on the chat hot path. |
| 2 | EARNED TRUST | +1 | A trace that silently omits tool calls is worse than no trace — it invites false conclusions during triage. |
| 3 | SKILLS, NOT FEATURES | 0 | Cross-cutting operational surface. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model impact. |
| 5 | GRACEFUL DEGRADATION | +1 | An unavailable trace segment must say so, with a correlation id, rather than render as an empty list. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Uses existing session/event stores; no new protocol. |
| 7 | API FIRST | +1 | Trace data served by real endpoints the CLI can also consume. |
| 8 | OBSERVABLE BY DEFAULT | +1 | This is the axiom, directly. |
| 9 | SECURE BY CONSTRUCTION | +1 | Forces the access question to be answered with a real scoped grant instead of "not giving it big access" as a holding position. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Client renders what the endpoint returns. |
| | **Net Score** | **+5** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Phase 1 is **investigation, not implementation** — measure what is actually
missing before designing a fix. Phase 2 closes the completeness gap that
investigation finds. Phase 3 grants scoped access so ONE's super-users can
triage themselves.

### Phase 1 — quantify the gap (no code changes)

Write a reconciliation check that, for a set of known sessions, compares:

- ADK's canonical session events (`/apps/{app}/users/{u}/sessions/{s}`) — the
  ground truth, per the `aitana-adk-testing` skill
- the Firestore `chat_sessions` mirror
- what the admin trace endpoint returns

and reports what is present in one and absent from another. Ship it as
`aiplatform session reconcile <session-id>` so it stays useful for the *next*
report of this shape rather than being a throwaway script. **The output of
Phase 1 determines Phase 2's design** — do not pre-commit to a fix.

### Phase 1 FINDINGS — measured 2026-08-10 (dev, 100 most recent sessions)

Run with `aiplatform --env local sessions reconcile --all --limit 100` against
dev's real Firestore + Vertex Agent Engine. **This changes what Phase 2 should
be**, so read it before designing anything.

| Finding | Count | Verdict |
|---|---|---|
| `OK` | 45% | — |
| `TURN_COUNT_DRIFT` | 54% | **Historical residue, not a live bug** — see below |
| `TOOLS_RENDER_ERRORED` | 1 session (1 of 189 tool calls, 0.5%) | Isolated |
| `TOOL_CALLS_DROPPED` | **0** | Reconstruction is not losing tool calls |
| `RESPONSE_ID_MISSING` | **0** | Leading hypothesis DISPROVED |
| `CANONICAL_MISSING` | **0** | No mirror-without-transcript in the recent window |

**The leading hypothesis was wrong.** Reading `_events_to_tool_activity` showed
a real defect — a `function_response` whose `id` is `None` is dropped from the
response index, so its call can never pair and renders as **failed** in the
admin trace even though the tool succeeded. The code path is real and is pinned
by a test. But it fires **zero times** in 100 real sessions. It is a latent
trap, not the cause of what Mark saw.

**What the drift actually is.** 54% looked alarming and is almost entirely
historical. The flagged sessions carry `turnCount` values of **367–380 on
sessions with 5–12 total events**, and the values are *sequential across
different sessions* — the signature of issue #38, where the counter lived under
an `app:` prefix and ran as one global odometer for every session on the
deployment. Every one of those rows was last written **2026-07-22/23**. Sessions
from 2026-08-07 carry sane counts (0–2). **The bug is fixed; the rows it
corrupted were never repaired.**

That residue is itself a genuine contributor to the reported symptom: the admin
session list renders `turn_count` per row, so ~50 sessions on dev display a
turn count that is pure nonsense. An operator reading that list sees numbers
that cannot be right, which is exactly "not populating correctly".

**Consequences for Phase 2 — the scope is smaller and different than assumed:**

1. **Backfill the corrupted `turnCount` rows** (recompute from canonical user
   events, or null them). This is the only *measured* defect with real reach.
2. **Do not** rebuild the trace reconstruction. It is not dropping anything.
3. **Still do the false-empty work.** `CANONICAL_MISSING` measured 0 here, but
   19 of 75 sessions on **test** had no Vertex session before the B3 fix
   (2026-08-05) and those transcripts are unrecoverable. Distinguishing
   "no tool calls" from "could not load" remains correct and cheap.
4. **Fix the id-less-response trap anyway** — it is two lines, it silently
   reports success as failure, and it will eventually fire.

### Phase 1 FINDINGS — TEST, 100 most recent sessions (2026-08-10)

Run after `v6.23.2` put the endpoint on test. This is the env that matters:
26 of the 100 belong to **acmeenergy.com**.

| Finding | All (100) | ONE only (26) |
|---|---|---|
| `OK` | 79% | 73% |
| `CANONICAL_MISSING` | **14%** | **23%** |
| `TURN_COUNT_DRIFT` | 7% | 4% |
| tool calls rendered failed | **0 of 64** | — |
| blank `ownerDomain` | **0** | — |

**This is B5's answer.** 14 sessions have a mirror row and turns but no
transcript — the admin trace renders "unavailable" and the conversation is
unrecoverable. Six of them are ONE's. That is what "not populating correctly
for some sessions" was.

**And the bleeding has stopped.** Dating every casualty against the B3 fix
(`44ca9b6`, deployed 2026-08-05 13:41):

```
2026-07-21 … 2026-07-27   12 sessions   turns 95 → 131
2026-08-05 10:34            1 session   turns 5      } both BEFORE the fix landed
2026-08-05 11:43            1 session   turns 2      } (13:41 that day)
--------------------------------------------------------------
lost transcripts last active AFTER the fix:   0
newest session on test:                        2026-08-10
```

**Zero losses across five days of post-fix traffic.** The same
`SessionManager` sweep that caused B2 and B3 caused this; one root cause, three
reported symptoms, already fixed. The turn counts on the casualties (95→131,
sequential across sessions) also place them in the issue-#38 odometer era, so
both historical defects cluster in the same window.

**What this leaves for Phase 2:**

1. **Repair or retire the 14 dead rows.** They still list in the sidebar and
   the admin view, and open to nothing. Unrecoverable, so the honest options are
   a clear "transcript unavailable — session predates the 2026-08-05 fix" marker
   or archiving them. **Do not** let them keep reading as ordinary sessions.
2. **The false-empty guard is now proven necessary, not speculative** — 14 real
   sessions on test need it to say something truthful.
3. **Backfill the corrupted `turnCount`s** (dev residue, same era).
4. ~~Fix the id-less-response trap~~ — **done**, `_events_to_tool_activity` now
   falls back to name-based FIFO pairing.

**Confidence note:** dev's sample was 65% our own `.test` fixtures and showed a
very different picture (54% turn-count drift, 0 canonical-missing). Test is the
representative env. Measure there.

### Phase 1 FINDINGS — dev, for comparison

**Note on the tool itself:** the first pass used a ±1 tolerance for turn-count
drift and flagged 58% of sessions. A measurement tool at 58% false positives
buries the signal it exists to find, so the tolerance is now pinned to
`adk.callbacks._TURN_FLUSH_INTERVAL` — the writer's own debounce — rather than
a guessed constant.

### Phase 2 — close the gap

Driven by Phase 1's findings. Whatever the cause, two invariants hold:

- **Never render a false empty.** A trace segment that could not be loaded must
  say "could not load tool calls for this run — correlation id X", never an
  empty list that reads as "no tool calls happened". This is principle #8
  applied to the admin surface, and it is the single highest-value change
  regardless of root cause.
- **Errors get a first-class trace entry** — code, message, stage, correlation
  id. Where the detail is genuinely backend-only, the entry carries the id
  needed to find it in Cloud Logging, so Dana's hop becomes one click with a
  known key rather than a search.

### Phase 3 — scoped access

Grant Dana (and ONE's designated super-users) access to traces **for their own
tenant's sessions only**. Group-tag scoping already exists — `derived_group_tags`
maps `acmeenergy.com` → `["ONE"]` — so this is a policy application, not new
machinery. The list endpoint filters by the caller's group tags; the detail
endpoint re-checks on read. No cross-tenant visibility, ever.

Mark's caution in the meeting was about *quality*, not permissions — but the
access design must still be explicit, because a trace contains full conversation
content and is exactly the confidential material CLAUDE.md's security rule
governs.

### CLI Surface

```
aiplatform session reconcile <session-id>   # ADK vs mirror vs trace endpoint — the Phase 1 deliverable
aiplatform session trace <session-id>       # same trace the admin UI shows, from a terminal
```

## Implementation Plan

### Phase 1: Quantify (~0.5 day)
- [ ] `aiplatform session reconcile` comparing all three stores (~120 LOC)
- [ ] Run against ≥10 real dev sessions incl. ones with errors/cancellations
- [ ] Write findings into this doc before starting Phase 2

### Phase 2: Close the gap (~0.5 day, scope confirmed by Phase 1)
- [ ] Fix the mirroring/query defect Phase 1 identifies
- [ ] Never render a false empty — distinguish "none" from "unavailable" (~60 LOC)
- [ ] First-class error entries with correlation ids (~80 LOC)
- [ ] Regression test reproducing the Phase 1 gap (~80 LOC)

### Phase 3: Access (~0.5 day)
- [ ] Group-tag scoping on list + detail endpoints (~60 LOC)
- [ ] Test: a ONE user sees ONE sessions and cannot read another tenant's (~80 LOC)
- [ ] Grant Dana access and notify her — an explicit promise made in the meeting

## Migration & Rollout

**Database Migrations:** Possible backfill of the mirror, pending Phase 1.
**Feature Flags:** None. Access is granted per group tag, which is itself the control.
**Rollback Plan:** Revoke the group tag to remove access; UI changes revert cleanly.
**Environment Variables:** None expected.

## Testing Strategy

### Backend Tests (pytest)
- [ ] A session with N tool calls returns N in its trace (the completeness guard)
- [ ] A run ending in `RUN_ERROR` yields a readable error entry with a correlation id
- [ ] A user cannot read a session outside their group tags
- [ ] A trace-load failure returns a distinguishable "unavailable", not an empty list

### Frontend Tests (Vitest)
- [ ] Error entries render with their correlation id
- [ ] "Unavailable" renders differently from "none" — the false-empty guard
- [ ] Forbidden renders a clear message, not a blank page

### Manual Testing
- [ ] Reproduce a real failure and diagnose it from the trace view alone
- [ ] Dana completes an unaided triage on a real ONE session
- [ ] Confirm a ONE user cannot see another tenant's sessions

## Security Considerations

A trace contains **full conversation content** — customer contracts, extracted
clauses, financial terms. It is exactly the material CLAUDE.md's security rule
governs. Access is group-tag scoped and re-checked on read, never inferred from
the client. Traces are never rendered into any public artefact and never egress
the GCP project edge. Widening trace access is a deliberate, auditable act —
Phase 3 must not be shortcut by loosening a rule.

## Performance Considerations

Admin-only, low traffic. The reconciliation check reads across three stores and
is a manual/CI tool, not a request-path feature — it must not be wired into
session load.

## Success Criteria

- [ ] Backend and frontend tests passing
- [ ] Phase 1 findings recorded in this doc
- [ ] 100% of tool calls appear in a reconciled session's trace
- [ ] Errors readable in the trace with correlation ids
- [ ] Dana has access and has triaged an issue unaided
- [ ] Langfuse can be switched off without loss of daily capability

## Open Questions

- **Root cause of the missing entries** — Phase 1's entire purpose. Do not
  design Phase 2 before it lands.
- **How far do we chase the last 10%?** Mark's 90% bar is the right one; the
  remainder should be a documented "go to Cloud Logging with this id" path
  rather than an open-ended chase.
- **Retention?** Traces are conversation content; a retention policy is owed but
  is arguably its own doc.
- **Should ONE see per-user traces or only their own?** Tomas's request implies
  Dana views *his* sessions. That is intra-tenant, so group scoping covers it —
  but it should be a stated, agreed policy, not an accident of implementation.

## Related Documents

- UAT source record (internal notes)
- [`.claude/skills/aitana-adk-testing/SKILL.md`](../../../.claude/skills/aitana-adk-testing/SKILL.md) — ADK canonical store vs Firestore mirror
- [conversation-context-fidelity.md](conversation-context-fidelity.md) — its compaction event lands in this trace view
