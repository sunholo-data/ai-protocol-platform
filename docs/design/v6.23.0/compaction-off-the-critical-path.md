# Compaction Off The Critical Path — stop making the user wait for it

**Status**: M0 ✅ · M1 ✅ · M2 ✅ — all verified live 2026-08-06
**Priority**: P1 — compaction now runs on every long conversation, and today it bills its cost to the user's turn
**Estimated**: ~2 days (M0 ~0.25d measure, M1 ~0.75d, M2 ~1d)
**Scope**: Backend (+ infrastructure decision)
**Dependencies**: [compaction-wiring-and-observability](compaction-wiring-and-observability.md) (shipped — this is the cost of that success)
**Created**: 2026-08-06
**Last Updated**: 2026-08-06

## Problem Statement

Compaction is a model call — a `pro` summarisation over a long conversation —
and both of ADK's compaction paths execute **inside the user's request**. Now
that the wiring is fixed, that cost is real and recurring on exactly the long
expert conversations ONE values most.

The two paths are not equally bad:

| Path | Where | User sees |
|---|---|---|
| **Pre-request** — `CompactionRequestProcessor` (`flows/llm_flows/compaction.py:39`) | before the model call | **added directly to TTFT** — a blank pause before the answer starts |
| **Post-invocation** — Runner (`runners.py:618-628`) | after all events yielded | answer is already rendered, but the run isn't finished |

The pre-request path is the harmful one: the user waits, with nothing on screen,
for a summarisation of a conversation they can already see.

The post-invocation path is subtler and still real. It is `await`ed *inside the
runner's async generator*, so the generator doesn't complete until compaction
does — which means `RUN_FINISHED` is delayed. The frontend keeps `isLoading`
true until `RUN_FINISHED`, so **the composer stays disabled and the typing
indicator keeps spinning after the answer has fully rendered**. The user sees a
finished answer and a UI that still says it's working.

Both were observed firing in the same turn during M4 verification (two
`HISTORY_COMPACTED` events on one turn).

**Impact:** every user on a conversation past the threshold, on the workflow the
customer values most. It also sits directly against Axiom #1 (INSTANT FEEL) and
the sub-1s first-token target.

## The reframing that makes this cheap

The scenario worth optimising for is the real one:

> A user is deep in a long conversation. They get an answer and spend ten
> minutes reading it. Then they ask a follow-up — **and wait**, while we
> summarise a conversation we have had ten idle minutes to summarise.

Stated that way, the fix looks like "compact during the idle time", which sounds
like it needs background infrastructure. It doesn't, because of an accident of
ADK's design that works in our favour:

**Post-invocation compaction is already anticipatory.** It fires at the *end* of
turn N — the instant the answer finishes, exactly when the user starts reading.
The work is already scheduled at the right moment. The only defect is that we
**block `RUN_FINISHED` on it**, so instead of compacting invisibly while they
read, we hold the UI in a working state.

So the ten-minute-read scenario is not a scheduling problem. It is:

1. **the pre-request path making them wait on the follow-up** → M1 demotes it to
   emergency-only, so routine compaction never precedes a model call;
2. **the post-invocation path making the UI look busy** → M2(a) emits
   `RUN_FINISHED` first and compacts while the connection stays open.

Together those give exactly the desired behaviour — compaction completes seconds
into a ten-minute read, and the follow-up is fast — **with no new service, no
queue, and no billing change.** The request lingers for the length of one
summarisation (seconds), not for the length of the read.

**And it fails safe.** The compaction write is a single `append_event` at the
very end of `_run_compaction_for_token_threshold_config`. If the user closes the
tab mid-summarisation and the request is cancelled, nothing is appended — the
compaction simply didn't happen and is retried next turn. There is no
half-applied state to reconcile, which is what made the fire-and-forget and
sidecar routes look risky and this one not.

This is the low-hanging fruit. The separate-service option below stays on the
table for when compaction gets expensive enough that holding a connection for
its duration is itself the cost — but it is not needed for the scenario above.

## The constraint that shapes the design

**Checked, not assumed** — `platform-frontend`, dev:

```
run.googleapis.com/cpu-throttling : unset → default TRUE (CPU throttled outside requests)
autoscaling.knative.dev/minScale  : unset → 0
timeoutSeconds                    : 3600
```

So the obvious fix — `asyncio.create_task(...)` and let it run after the
response — **is unsafe here**. Outside an active request Cloud Run throttles CPU
to near zero and may scale the instance to zero. A fire-and-forget compaction
would stall mid-flight or never complete, and it *mutates conversation history*:
a half-applied compaction (summary appended, or not) with no error path is worse
than a slow one.

This is the reason to write a doc rather than just move a line of code.

## Goals

**Primary Goal:** A compaction never delays the first token, and never leaves the
UI in a working state after the answer is complete.

**Success Metrics:**
- Compaction contributes **0ms** to TTFT on a normal turn.
- Time between last text delta and `RUN_FINISHED` on a compacting turn is
  indistinguishable from a non-compacting one.
- No compaction is ever left half-applied.
- Measured before and after — no "should be faster".

**Non-Goals:**
- Removing compaction's cost. It is a model call; the goal is to move it, not
  to eliminate it.
- Turning on always-allocated CPU as a first resort. That has a standing cost
  and should be a considered decision, not a side effect of this work.

## Design

### M0 — measure first ✅ DONE 2026-08-06

18 turns, `general-assistant`, forced `COMPACTION_TOKEN_THRESHOLD=3000`, real
stream. Every measured turn checks `HISTORY_COMPACTED` on the wire rather than
assuming a compaction happened.

| | TTFT (median) | tail (median) |
|---|---|---|
| **No compaction** (n=15) | 14,682 ms | 3,141 ms |
| **Compacted** (n=3) | 28,326 ms | 31,330 ms |
| **Cost of compaction** | **+13,644 ms** | **+28,189 ms** |

**Verdict: GO on both M1 and M2**, and the tail — the part I expected to be
negligible — is the *larger* cost.

Three findings that change the plan:

**1. The cost is severe and it is not hypothetical.** Worst turn: TTFT 45s, tail
38s, **90s total for one turn**. The "user asks a follow-up and waits two
minutes" scenario is real, and this is on a `lite` skill.

**2. It grows per compaction, sharply.** Tails across the three compacting turns
were 10.1s → 31.3s → 38.2s. Compaction is not a fixed cost; it degrades as the
conversation lengthens, so the longest conversations — the ones this whole
feature exists for — are punished hardest. Cause not yet established; the
compaction input grows as the conversation does, and the rolling summary seeds
the next one. **Worth its own investigation**, and it strengthens the case for
cheaper strategies ([compaction-strategy-hooks](compaction-strategy-hooks.md))
independently of where the work runs.

**3. Every compacting turn compacted TWICE** (`compacted` column = 2 on all three).
Both paths fire on the same turn: pre-request *and* post-invocation. So we are
paying the cost twice per turn. **M1 does not merely move the cost — it should
roughly halve it**, by leaving only the post-invocation path to do the work.

One thing M0 does **not** explain: a **~3.1s baseline tail with zero
compactions**. Something else holds the stream open for ~3s after the last text
delta on every turn. Small next to 31s, but it is unexplained and pre-existing —
tracked as an open question, not part of this sprint.

### M1 — get it off the pre-request path ✅ DONE 2026-08-06

20 turns, same harness, same forced threshold. Compare against M0's baseline:

| | TTFT cost | tail cost | compactions/turn |
|---|---|---|---|
| **M0 (before)** | **+13,644 ms** | +28,189 ms | **2** |
| **M1 (after)** | **−442 ms** | +33,590 ms | **1** |

**Both M1 goals met.** Compaction no longer touches TTFT at all (−442ms is
noise: 16,672 → 16,230 median), and the double-compaction is gone — `compacted`
held at **1 across eight consecutive compacting turns**, where v1 gave 1 then 2.

**The growth curve, answered.** Tails across the eight compacting turns:

```
11.4s → 27.2s → 36.4s → 34.2s → 37.9s → 44.9s → 46.9s → 46.0s
```

It rises and then **plateaus around 45s** — it is bounded, not runaway. That
matters for what to build next: the earlier worry was that superlinear growth
would make scheduling pointless because a 38s tail becomes 90s at turn 30.
It doesn't. Each compaction processes a bounded slice (new events since the last
one, plus the rolling summary seed), so the per-turn cost stabilises.

**Conclusion: M2 is sufficient, and now carries the entire remaining problem.**
The tail is ~37s median, up to 47s, and it is 100% of what the user still feels.
A cheaper strategy ([compaction-strategy-hooks](compaction-strategy-hooks.md))
would shrink that number, but it is no longer required to prevent unbounded
degradation.

#### What it took (two bugs, both instructive)

**v1 used `routine × 3`** — the exact "second magic number" this sprint's plan
said to avoid. A *relative* threshold rises with the routine one, so a large
conversation crosses both. Measured: turn 15 fixed, turn 16 defeated. Twelve
unit tests passed throughout, because they asserted `emergency > routine` —
true, and meaningless. Only the live run found it.

**v2's first cut then silently no-op'd in production.** The threshold is derived
from the model's registry `context_window`, but `entry_for()` returns `None` for
raw **api names** by design — and the callback reads `agent.model.model`, which
*is* an api name. Every production lookup would have taken the 200K fallback and
quietly disabled the optimisation. Caught by a unit test written *after* the
live run taught it what to assert.

The pattern worth keeping: the tests only became good once measurement told them
what mattered.

### M1 — original plan (~0.75d)

Routine compaction should never precede a model call. Two levers, no new
machinery:

1. **Raise the pre-request trigger to an emergency-only level.** The
   pre-request processor exists to stop a turn exceeding the context window —
   that is a real job, but it is a *safety net*, not routine housekeeping. Set
   the routine threshold so the **post-invocation** path does the work, and keep
   a much higher pre-request threshold as a genuine last resort.
2. **Since `token_threshold` is per-invocation settable**
   (`invocation_context.events_compaction_config` — the seam found in the wiring
   work), the before-agent callback can raise it for the current turn and let the
   post-invocation path use the routine value.

Net effect: the common case compacts *after* answering, and the user only ever
waits when the alternative is a failed turn.

### M2 SPIKE VERDICT ✅ 2026-08-06 — feasible, but there is a cheaper option

**Q: can `stream_agui_events` emit `RUN_FINISHED` and then own the compaction?**
**A: yes, mechanically — but it is the expensive way, and it is not necessary.**

Confirmed ordering: `ag_ui_adk` yields `RunFinishedEvent` only after
`_stream_events` is exhausted, and that generator wraps `runner.run_async`,
which awaits compaction internally. So `RUN_FINISHED` genuinely cannot arrive
first without taking the work off ADK.

Taking it over is possible — `agui_agent._app` and
`_session_manager._session_service` are reachable, and the session id is
`run_input.thread_id` — but it costs: suppressing ADK's post-invocation path
(App config → `None`, with the emergency config injected into
`invocation_context` by our callback instead), then calling
**`_run_compaction_for_sliding_window`, a private function** — confirmed there
is *no* public entry, every function in `google.adk.apps.compaction` is
underscore-prefixed — plus re-binding the tracker, cancellation handling, and
owning concurrency.

#### The cheaper option the spike surfaced

**The dead time is not caused by where `RUN_FINISHED` sits. It is caused by the
frontend having no signal that the answer is complete.** `isLoading` clears on
`onRunFinalized`, so the composer stays disabled through the whole compaction.

We already emit `HISTORY_COMPACTED` — but *after* `super().maybe_summarize_events()`,
i.e. after the ~35s model call, so it lands at roughly the same moment as
`RUN_FINISHED` and is useless as an early signal.

Emitting a **`COMPACTION_STARTED`** event *before* that call is ~10 lines in code
we already own (`FidelityEventSummarizer`). The frontend then knows precisely
when the answer is done and the system is only tidying up:

- re-enable the composer
- swap the typing indicator for a quiet "tidying up history…" notice
- `RUN_FINISHED` stays honest and arrives when the run genuinely ends

| | ownership route (a) | `COMPACTION_STARTED` |
|---|---|---|
| Backend | suppress ADK path + private API + tracker + cancellation | ~10 lines in our own summarizer |
| Frontend | none | ~20 lines |
| Private ADK coupling | **yes** | none |
| `RUN_FINISHED` semantics | emitted early, before the run ends | unchanged |
| Fixes the user-visible problem | yes | yes |

**Recommendation: `COMPACTION_STARTED`.** It addresses the actual complaint —
the UI claiming to work after the answer is finished — without taking ownership
of ADK's compaction scheduling. It also composes with NEVER-SILENT better: the
user is *told* what is happening rather than shown a run that silently ended.

Route (a) remains correct if we later need the request itself to end sooner
(e.g. connection-holding becomes a cost), and the Cloud Tasks route remains the
answer if we need guaranteed progress.

**One risk is shared by both** and must be tested either way: re-enabling the
composer means a user can send turn N+1 while turn N's compaction is still
running — two requests on one session. ADK's candidate filtering by
`_latest_compaction_end_timestamp` should handle it, but it has never been
exercised concurrently here.

### M2 — RESULT ✅ verified live 2026-08-06

18 turns, same harness, now also timing **last text delta → `COMPACTION_STARTED`**
— the moment the composer is released, i.e. what the user actually waits.

| | before (M0/M1) | after (M2) |
|---|---|---|
| User waits after their answer completes | **41,659 ms** | **2,336 ms** |

**A 94% reduction.** The `tail` is still ~41.7s and that is correct by design —
the request runs to completion, the user simply stops waiting for it. TTFT cost
stays at noise (+943 ms).

#### Two findings from the run

**1. The release is ~2.3s, not ~0.** `COMPACTION_STARTED` is enqueued on the
tracker and drains on the next event or heartbeat tick, so there is up to a
couple of seconds before it reaches the client. Fine against 41.7s, but it is
drain latency rather than anything fundamental, and could be tightened by
flushing on emit.

**2. Some compactions produce nothing — NEW, unrelated to M2.** Turns 12, 13, 16
and 18 emitted `COMPACTION_STARTED` but never `HISTORY_COMPACTED`, meaning
`maybe_summarize_events` returned `None`: the summariser ran, took ~5s, and
produced no summary. History was therefore **not** compacted and the cost was
paid for nothing. That is the same empty-response class as the `EMPTY_RUN`
handling elsewhere in the stack, and it is worth its own investigation — a
compaction that silently no-ops means context keeps growing while the logs say
work happened. Not an M2 regression; M2 is what made it visible.

### M2 — original options (~1d)

Post-invocation compaction still delays `RUN_FINISHED`. The options split on one
fact about Cloud Run: **CPU is allocated per REQUEST, and the unit of throttling
is the INSTANCE, not the container.**

#### Why a sidecar does not solve this (checked, 2026-08-06)

The natural instinct — "make it another sidecar, like toolbox" — does not work
here, and it is worth writing down so it is not re-proposed. Per Cloud Run's
container contract, under **request-based billing** (our config:
`cpu-throttling` unset → default) sidecar containers are throttled exactly like
the ingress container. CPU is allocated only when:

- the instance is processing at least one request, **or**
- the ingress container is starting up, **or**
- the instance is in its ~10s post-SIGTERM shutdown window.

Sidecars share the instance's lifecycle. The existing `toolbox` sidecar works
precisely because it serves requests *during* a main request — it is synchronous
to the request lifecycle, which is the opposite of what compaction needs.

So a compaction sidecar would be throttled at exactly the moment it was supposed
to work. It buys process isolation, which is not the problem.

**The work needs its own request somewhere.** That is the whole design
constraint, and it leaves three real options:

**(a) Emit `RUN_FINISHED` before awaiting compaction.** Keep the work in the
current request, but stop making the UI wait for it. We own
`stream_agui_events`, so the client is told the turn is done while the request
stays open — CPU stays allocated because we are still inside a request. No
infrastructure change, no new service, no queue.
*Risk:* the SSE connection lingers after the client considers the run finished;
that must not read as a hung stream.

**(b) A separate compaction service, fed by Cloud Tasks.** The genuinely
asynchronous answer: enqueue `(session_id, user_id)` and let a **separate Cloud
Run service** compact it. That service gets its own request, so its own CPU
allocation, entirely independent of whether the chat instance is idle or scaled
to zero. Cloud Tasks brings retries, backoff and dead-lettering — which matter
because compaction mutates conversation history and a silent half-failure is the
worst outcome. Costs: a new service, its IAM, an auth path, and idempotency.

**(c) Switch the existing service to instance-based billing.** Makes CPU
always-allocated, at which point in-process background work becomes legitimate.
One annotation, but a standing cost for every instance-hour, and it changes the
cost model of the whole service to solve one feature's problem.

**Recommendation: M1 + (a) now — that IS the anticipatory fix.** Per the
reframing above, compaction is already scheduled at the right moment; we simply
stop blocking the UI on it, and stop letting the pre-request path ambush the
follow-up. No new service, no queue, no billing change, and it fails safe under
cancellation.

Reach for **(b)** when one of these becomes true, not before:
- compaction routinely takes long enough that holding a connection for its
  duration is itself a cost (M0 tells us);
- a second deferred job appears and a queue starts paying for itself;
- we want retries — today a cancelled compaction is simply retried next turn,
  which is adequate but not guaranteed progress on a session the user abandons.

**(c)** is a real option but should be chosen for its own reasons, not as a side
effect of this.

**Rejected: fire-and-forget `create_task`** — unsafe under request-based
billing, per the constraint above. **Rejected: a compaction sidecar** — throttled
with the instance, per this section. Both are worth a note in the code so they
are re-evaluated deliberately rather than rediscovered.

### Idempotency, whichever option

Compaction appends an event to the session. Any deferral widens the window for a
second turn to start before the first's compaction lands. ADK's own
`_latest_compaction_end_timestamp` / candidate filtering handles overlapping
ranges, but that has never been exercised concurrently here. M2 owes a test for
two overlapping turns on one session.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | The entire point: removes an LLM call from the pre-token path. |
| 2 | EARNED TRUST | 0 | No change to answer quality. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Makes a `pro` summariser affordable, because its latency stops being user-visible. |
| 5 | GRACEFUL DEGRADATION | +1 | Keeps an emergency in-turn compaction so an over-limit turn degrades instead of failing. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK's existing two-path structure and the per-invocation config seam rather than new scheduling. |
| 7 | API FIRST | 0 | No new surface (unless (c)). |
| 8 | OBSERVABLE BY DEFAULT | +1 | M0's measurements become a recorded baseline, not a one-off. |
| 9 | SECURE BY CONSTRUCTION | 0 | No new data access. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Server-side. |
| | **Net Score** | **+5** | Threshold: >= +4 ✅ |

## Implementation Plan

### M0 — measure (~0.25d)
- [ ] TTFT with/without pre-request compaction, on a real stream
- [ ] last-delta → `RUN_FINISHED` gap, compacting vs not
- [ ] Record the numbers here; decide whether M2 is worth building

### M1 — off the pre-request path (~0.75d)
- [ ] Emergency-only pre-request threshold; routine work post-invocation (~50)
- [ ] Per-invocation threshold override in the before-agent callback (~40)
- [ ] Test: routine compaction never fires pre-request; over-limit still does (~80)

### M2 — off the visible tail (~1d, gated on M0)
- [ ] `RUN_FINISHED` emitted before awaiting compaction (~60)
- [ ] Guard: a lingering request is not mistaken for a hung stream (~40)
- [ ] Concurrency test: two overlapping turns on one session (~80)

## Testing Strategy

- [ ] Routine compaction contributes nothing to TTFT (measured, not asserted)
- [ ] `RUN_FINISHED` timing unaffected by compaction
- [ ] An over-limit turn still compacts in-request rather than failing
- [ ] Two overlapping turns produce consistent session state
- [ ] **Live**: re-run the M0 measurements and compare — the acceptance test

## Open Questions

- ~~**Is the post-invocation delay actually material?**~~ **Answered: it is the
  LARGER cost** — +28.2s median vs +13.6s on TTFT. M2 is firmly justified.
- ~~**Why does compaction cost grow per turn?**~~ **Answered by M1's 20-turn run:**
  it rises (11s → 27s → 36s …) and **plateaus around 45s**. Bounded, not
  runaway — each compaction handles a bounded slice plus the rolling summary
  seed. So scheduling IS sufficient; a cheaper strategy shrinks the number but
  is not needed to prevent unbounded degradation.
- **What causes the ~3.1s baseline tail with NO compaction?** Pre-existing,
  unexplained, on every turn. Small next to 31s but it is pure dead time in the
  UI and nobody has looked at it.
- **Instance-based billing, or a separate service?** The two ways to get real
  background CPU. Billing is one annotation and no new code, but a standing cost
  on every instance-hour to solve one feature's problem. A separate service is
  more work and more moving parts, but its cost is proportional to use and it
  generalises to the next deferred job. Worth deciding once, with M0's numbers,
  rather than per-feature.
- **Does `minScale=0` interact with (a)?** An instance holding a request open
  will not be reclaimed, so no — but worth confirming under maxScale=3.
- **If (b), does the compaction service need the full agent stack?** It needs the
  summarizer, the model chain and the session service — not the tool tree or the
  A2UI layer. If that subset is small, the service is small; if it drags in the
  whole backend image, that is an argument for (a) or (c).
- **Does compaction latency scale with conversation length** enough that a long
  session eventually feels slow regardless? If so, strategy choice
  ([compaction-strategy-hooks](compaction-strategy-hooks.md)) matters more than
  placement — a `lite` structural strategy may beat any amount of scheduling.

## Related Documents

- [compaction-wiring-and-observability](compaction-wiring-and-observability.md) — made compaction run; this pays the bill
- [compaction-strategy-hooks](compaction-strategy-hooks.md) — a cheaper strategy is the other way to reduce this cost
- [compaction-tuning-console](compaction-tuning-console.md) — thresholds this doc splits into routine vs emergency
