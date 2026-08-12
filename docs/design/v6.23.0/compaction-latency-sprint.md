# Sprint Plan: COMPACTION-LATENCY — get compaction out of the user's way

## Summary

Stop billing compaction's cost to the user's turn. Measure first, remove the
TTFT hit, and only then decide whether the residual tail is worth a riskier fix.

**Duration:** 2.25 days
**Scope:** Backend
**Dependencies:** COMPACTION-WIRE M1/M2/M4 (shipped) — compaction runs and is observable
**Risk Level:** Low for M0/M1 · **Medium for M2** (couples to a private ADK function)
**Design Doc:** [compaction-off-the-critical-path.md](compaction-off-the-critical-path.md)

## Current Status Analysis

### What is already true
- Compaction fires and is verified live (`f037b5f`).
- `HISTORY_COMPACTED` carries `summary_chars` — M0 needs no new instrumentation.
- **The two compaction paths read different config objects** (verified):
  pre-request reads `invocation_context.events_compaction_config`,
  post-invocation reads `app.events_compaction_config`. That split is what makes
  M1 a config change rather than an ADK fight.
- The post-invocation write is a single `append_event` at the end, so a
  cancelled request appends nothing — no half-applied state.

### What is not known
- **How big either cost actually is.** Nobody has measured. M0 exists because
  every failure in the predecessor sprint was a measurement failure, not an
  implementation failure.

## Milestones

### M0: Measure both costs
**Scope:** backend / ops · **Est:** ~40 LOC (harness only) · **Duration:** 0.25d

**Tasks**
- [ ] Extend the compaction probe to stamp wall-clock at: first token, last
      `TEXT_MESSAGE_CONTENT`, `RUN_FINISHED` (~40)
- [ ] Run compacting vs non-compacting turns at a forced low threshold
- [ ] Record both numbers in the design doc

**Acceptance**
- [ ] TTFT delta attributable to pre-request compaction, in ms
- [ ] last-delta → `RUN_FINISHED` gap, compacting vs not, in ms
- [ ] **A written go/no-go on M2 based on that gap** — if it is small, M2 is
      cancelled and that is a success, not a shortfall

**Risks**
- Measuring the wrong thing (the predecessor sprint's failure mode). *Mitigation:*
  assert a compaction actually fired in the measured turn — `HISTORY_COMPACTED`
  present — rather than assuming it did.

### M1: Demote the pre-request trigger to emergency-only
**Scope:** backend · **Est:** ~90 impl + ~110 tests · **Duration:** 0.75d

Routine compaction moves entirely to the post-invocation path, which already
fires at the end of a turn — i.e. while the user is reading. The pre-request
processor stays as a genuine safety net so an over-limit turn still degrades
instead of failing.

**Tasks**
- [ ] `before_agent_callback` raises `invocation_context.events_compaction_config`'s
      token threshold to the emergency value for the turn (~50)
- [ ] Emergency threshold derived from the model's window, not a second magic
      number (~20)
- [ ] Per-request `model_copy` — never mutate the shared config (~10)
- [ ] Tests: routine compaction never fires pre-request; an over-limit turn
      still does; shared config unmutated (~110)

**Acceptance**
- [ ] A routine compacting conversation shows **zero** pre-request compactions
- [ ] An over-limit turn still compacts in-request rather than failing
- [ ] TTFT on a compacting conversation matches a non-compacting one (M0's harness)
- [ ] `make test-fast`, `make adk-conformance`, lint clean

**Risks**
- Suppressing the safety net too well and letting a turn exceed the window.
  *Mitigation:* the emergency threshold is derived from the model's real context
  size; a test asserts an over-limit turn still compacts.
- The two-config-source split is load-bearing and undocumented in ADK. *Mitigation:*
  an `adk_contract` test pins it, so a `google-adk` bump that merges the sources
  fails `make adk-conformance` loudly rather than silently restoring the TTFT hit.

### M2: Take the tail off the visible turn — SPIKE FIRST
**Scope:** backend · **Est:** ~0.5d spike, then ~0.75d if green · **Duration:** ≤1.25d

**Gated twice:** on M0 showing the gap is material, and on the spike below.

**Spike (~0.5d, timeboxed)**
- [ ] Can `stream_agui_events` emit `RUN_FINISHED` and then run compaction itself?
      Needs the App, the session and the session service at that point.
- [ ] Confirm suppressing ADK's post-invocation compaction (App-level threshold)
      cleanly hands ownership over, with no double-compaction.
- [ ] **Write down the answer either way.** A "no" cancels M2 and M1 stands alone.

**If green (~0.75d)**
- [ ] Emit `RUN_FINISHED`, then compact, then close the stream (~60)
- [ ] Guard: a lingering request must not read as a hung stream (~40)
- [ ] Concurrency test: two overlapping turns on one session (~80)
- [ ] `adk_contract` test pinning the private-function coupling (~40)

**Acceptance**
- [ ] last-delta → `RUN_FINISHED` gap indistinguishable from a non-compacting turn
- [ ] No double-compaction; session state consistent under overlapping turns
- [ ] Cancelled request leaves no partial state (already true — assert it)

**Risks**
- **Couples to `_run_compaction_for_sliding_window`, a private ADK function.**
  This is the reason M2 is spiked rather than planned. *Mitigation:* an
  `adk_contract` guard so a version bump fails loudly; and M1 already banks the
  larger win if we back out.
- Double-compaction if ADK's path isn't fully suppressed. *Mitigation:* asserted
  by test; `HISTORY_COMPACTED` count makes it observable live.

## Model Assignment

| Stage | Model | Why |
|-------|-------|-----|
| M0 executor | `claude-opus-4-8`, xhigh | Judgement-heavy: deciding whether a measurement actually discriminates is exactly where this project has erred. |
| M1 executor | `claude-opus-4-8`, xhigh | Well-specified config change on a verified seam; short horizon. |
| M2 spike | `claude-fable-5`, xhigh | Highest subtlety in the sprint — streaming semantics, generator lifecycle, private-API coupling, concurrency. A wrong-but-plausible implementation would pass shallow tests, which is this repo's recurring failure mode. |
| M2 build | `claude-opus-4-8`, xhigh | Once the spike has settled the shape, the build is ordinary. |
| Evaluator | `claude-fable-5` | Cross-model diversity on the milestone that touches the stream. |

## Sequencing

```
M0 (measure) ──> M1 (the sure win, independent of M0's result)
             └─> M2 spike ──> M2 build   [both gated: M0 gap material AND spike green]
```

M1 does not depend on M0's outcome — it is worth doing regardless, because the
TTFT hit is unambiguous. M0 exists to decide M2.

## Success Metrics

- Compaction contributes ~0ms to TTFT (measured before and after)
- An over-limit turn still compacts rather than failing
- `make adk-conformance` green, including the new contract pins
- A written go/no-go on M2, whichever way it goes

## Explicit Non-Goals

- Cloud Tasks / separate compaction service (triggers for reaching for it are in
  the design doc; none are met yet)
- Instance-based billing — a real option, to be decided on its own merits
- Strategy or threshold changes — different docs (1b, 1c)
