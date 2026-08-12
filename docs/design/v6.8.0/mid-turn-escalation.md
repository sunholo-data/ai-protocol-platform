# Mid-Turn Escalation

**Status**: Design-ahead (deferred — nothing built this push)
**Priority**: P2
**Estimated**: ~3 days (exploratory)
**Scope**: Backend
**Dependencies**: 6.0.0 agent-factory ✅ (`_HeuristicRouter`), 6.6.0 model tiers ✅, 7.7 model-reliability ✅ (stream survival / heartbeats)
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

> **Deferred by decision (2026-07-14).** Captured now so the idea and its constraints aren't
> lost. Revisit after 8.2's handoff UX proves out — inter-skill handoff may make this less urgent.

## Problem Statement

Adaptive depth today commits to a tier **once, at turn start**. `_HeuristicRouter.pick_agent`
(`backend/adk/agent.py:470-483`) runs `_should_think(message)` on the incoming message and
hands one agent (fast or thinking) to the Runner for the **whole** turn. If a request *looks*
simple but turns out to need deep reasoning (or vice-versa), the turn can't change course.

**Current State:**
- Intra-skill depth = a one-shot string heuristic (`_should_think`: >280 chars, ≥2 `?`, THINK_KEYWORDS).
- Inter-skill depth = 8.2 delegation (a *different* skill/model takes over) — but that's a
  whole handoff, not "the same skill's answer deepens."
- No "stream a fast first-impression answer, then escalate to the deep model within the same turn."

**Impact:** Occasional mis-routing (fast model on a hard turn → weak answer; smart model on a
trivial turn → slow/expensive). Low-frequency but visible on ambiguous asks.

## Goals

**Primary Goal (to validate when scheduled):** Begin streaming a fast answer immediately, and
if the fast agent detects (or a mid-turn signal indicates) the task needs more, escalate to the
deep model **within the same turn** — without dead air and without discarding the fast partial.

**Open design tension:** this trades Axiom #1 (instant partial) against Axiom #2 (don't present
a fast-but-wrong partial as final). The escalation must be visibly marked, not a silent swap of
the answer under the user.

**Non-Goals:** Replacing inter-skill delegation (8.2) — this is intra-skill only.

## Axiom Alignment (indicative — finalize when scheduled)

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Always streams a fast partial first. |
| 2 | EARNED TRUST | -1 (risk) | A fast partial later revised by the deep model risks presenting low-confidence content as answer unless escalation is clearly marked. Must mitigate. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | The purest expression of the axiom — right model chosen *as the moment reveals itself*. |
| 5 | GRACEFUL DEGRADATION | +1 | If escalation fails, the fast partial remains a useful answer. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Escalation is a traced decision (why/when). |
| (others) | | 0 | |
| | **Net Score** | **+3 (indicative)** | The -1 on EARNED TRUST must be designed out before build. |

## Design (options to explore)

- **A. Detect-and-escalate:** fast agent runs with a lightweight self-signal ("this needs deep
  reasoning") that triggers a second, deep pass appended to the same turn, visibly marked
  ("Refining with the deep model…" STAGE_PROGRESS + a marker), never overwriting silently.
- **B. Speculative two-track:** start fast; if a cheap classifier (or the planner's early
  thoughts) crosses a threshold, spin the deep model and reconcile. Higher cost; needs a token budget guard.
- **C. Punt to delegation:** treat "needs more" as a handoff to a deep sibling skill (8.2) —
  possibly making this doc unnecessary. Evaluate first.

All options must preserve never-dead-air (7.7 heartbeats) and mark the escalation (Axiom #2).

## Open Questions

- OQ1: Is intra-skill mid-turn escalation worth it once inter-skill delegation (8.2) exists, or does Option C cover the need?
- OQ2: How to reconcile a fast partial with a deep revision without eroding trust (append vs replace-with-marker)?
- OQ3: Token-budget ceiling for speculative two-track before it's disallowed.

## Related Documents

- [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) — inter-skill depth (may subsume this)
- [model-reliability.md](../v6.7.0/implemented/model-reliability.md) — heartbeats / thinking visibility this must preserve
- [ttft-optimization.md](../v6.1.0/implemented/ttft-optimization.md)
