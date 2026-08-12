# Sprint Model Assignment Rubric

Shared reference for the sprint trio (sprint-planner, sprint-executor,
sprint-evaluator). The planner runs this assessment and records the result as
a **Model Assignment** section in the sprint plan; executor and evaluator
honor it. First recorded use: the MODEL-RELIABILITY sprint (2026-07-10) —
kept below as the worked example.

## Why this exists

Which Claude model runs each sprint stage is a real lever, and the deciding
factors are **behavior fit and turn latency, not cost**: across a multi-day
sprint the total spend difference between Opus-tier and Fable-tier is
typically tens of dollars — noise against one avoided debug marathon. Without
a recorded assignment, every sprint session re-derives (or ignores) this
choice; the executor may run a subtle streaming milestone on a fast model, or
burn latency running mechanical file moves on the most deliberate model.

## Current lineup (traits, not prices)

Verify ids/pricing against the `/claude-api` skill before relying on them —
models change; this table records *roles*, which are more stable.

| Model | Id | Role in sprints |
|-------|----|-----------------|
| Claude Fable 5 | `claude-fable-5` | Maximum first-shot correctness on long-horizon, well-specified implementation. Turns can run minutes — fine for autonomous execution, sluggish for interactive iteration. ~2× Opus token cost. |
| Claude Opus 4.8 | `claude-opus-4-8` | Default workhorse: strong long-horizon coding (xhigh effort), strong code review, snappier turns. The single-model value pick when not splitting. |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | Procedural multi-step work: browser verification, test loops, scaffolding. |
| Claude Haiku 4.5 | `claude-haiku-4-5` | Pure mechanical fan-out: greps, file inventories. |

## Assessment criteria (score each milestone)

1. **Subtlety** — does the milestone touch streaming/concurrency semantics,
   security-critical gates, or protocol boundaries where a wrong-but-plausible
   implementation passes shallow tests? High subtlety → Fable 5. Porting a
   proven fix, config flags, UI components → Opus 4.8 (or Sonnet).
2. **Horizon** — will it run autonomously for hours from a complete spec?
   Long + well-specified favors Fable 5 (its strength is precisely
   "full spec up front, high effort"). Short interactive loops favor Opus.
3. **Spec completeness** — Fable's advantage collapses when the spec is vague
   (it will ask or over-plan). Under-specified milestones: stay on Opus and
   iterate with the user.
4. **Review load** — judgment-heavy acceptance criteria ("is this gate
   *un-bypassable*") raise the evaluator stakes; deterministic criteria
   (CI, smoke scripts) lower them.

## Default assignment (start here, override per milestone)

| Stage | Default | Notes |
|-------|---------|-------|
| sprint-planner | `claude-opus-4-8`, effort high | Planning is decomposition + interactive iteration; the design doc already holds the hard thinking. Use Fable 5 only when the planner should stress-test the design itself. |
| sprint-executor | `claude-opus-4-8`, effort xhigh; **`claude-fable-5` for milestones scoring high on subtlety + horizon** | Record per-milestone overrides in the table. |
| sprint-evaluator | `claude-opus-4-8` + report-everything prompt | **Cross-model diversity:** where feasible, evaluate with a *different* model than the one that wrote the milestone — same adversarial-verify logic as everywhere else. |
| Task sub-agents (Explore, test loops, browser verify) | `claude-sonnet-4-6` / `claude-haiku-4-5` | Set via the Agent/Task `model` param. |

**Evaluator gotcha (Opus 4.8):** it follows severity filters literally — a
prompt saying "only report high-severity issues" makes it silently withhold
findings. Always instruct: *"Report every issue you find, including
low-confidence and low-severity ones, with a confidence tag — filtering
happens downstream."*

## How assignments are applied

- **Main session model:** the executor/evaluator checks the plan's Model
  Assignment at session start; if the current session model differs from the
  assignment for the upcoming milestone, say so and ask the user to switch
  (`/model <id>`) before proceeding — don't silently run a
  Fable-assigned milestone on a lighter model.
- **Sub-agents:** pass `model:` on the Task/Agent call per the assignment.
- **Recording:** the assignment lives in the sprint plan markdown (source of
  truth) and optionally as a `model` field per milestone in the sprint JSON.

## Worked example — MODEL-RELIABILITY (7.7, assessed 2026-07-10)

Design doc: `docs/design/v6.7.0/model-reliability.md`. Assessment rationale:
the riskiest artifact is `ResilientLlm` — an async-generator wrapper on ADK's
`BaseLlm` streaming seam with a mid-stream visible-output gate and a
three-provider error classifier: maximum subtlety, long-horizon, fully
specified → Fable 5. Phase 0 ports a proven v5 fix + config flags; Phase 3 is
UI + plumbing → Opus 4.8 xhigh. Evaluation is mostly deterministic
(fault-injection probes, smoke scripts) with one judgment-heavy criterion
(residency gate un-bypassable) → Opus 4.8 with report-everything, giving
cross-model diversity over the Fable-written core.

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-4-8` (high) | Decomposition of an already-detailed doc; interactive |
| Execute Phase 0 (proxy port, cloudbuild, heartbeats, watchdog) | `claude-opus-4-8` (xhigh) | Mechanical/proven patterns |
| Execute Phases 1–2 (`model_errors.py`, `ResilientLlm`, residency gate, `RegionalGemini`) | `claude-fable-5` | Subtle streaming/concurrency + security-critical gate; complete spec |
| Execute Phase 3 (thinking visibility, fault injection, OTel) | `claude-opus-4-8` (xhigh) | Plumbing + UI |
| Evaluation (all rounds) | `claude-opus-4-8` + report-everything | Cross-model check on Fable-written core; deterministic criteria carry the rest |
| Sub-agents (verify-regions probes, browser verify, test loops) | `claude-sonnet-4-6` | Procedural |
