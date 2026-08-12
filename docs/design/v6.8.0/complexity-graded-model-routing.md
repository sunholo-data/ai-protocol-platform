# Complexity-Graded Ascending Model Routing

**Status**: Planned (research + design; supersedes the deferred 8.5 mid-turn-escalation)
**Priority**: P2
**Estimated**: ~4d (heuristic ladder) + research spike for the learned classifier
**Scope**: Backend
**Dependencies**: 6.0.0 agent-factory ✅ (`_HeuristicRouter`/`_should_think`), 6.6.0 model tiers ✅, 7.7 model-reliability ✅ (fallback chains / never-dead-air), 8.2 elicited-handoff ✅ (inter-skill escalation), the 2026-07-14 model-registry refresh (flagship + middle tiers)
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

## Problem Statement

The platform now has a full model lineup across price/capability tiers, but
**depth is assigned crudely**: a skill either pins one model, or uses the
2-tier `_HeuristicRouter` (`backend/adk/agent.py:470`) that flips lite↔smart via
a keyword/length heuristic (`_should_think`, `:336`: >280 chars, ≥2 `?`, a
THINK_KEYWORD). That's a **binary** decision on a **string** — no true "start
cheap, escalate as the work demands it" ladder, no principled notion of
*complexity*, and no cascade that tries a cheap model and only escalates when the
answer isn't good enough.

The intent (per axiom #4 RIGHT MODEL, RIGHT MOMENT) is an **ascending skill
level**: answer the easy 80% on a super-fast, cheap model; send genuinely deeper
needs to the mid tier; reserve the top tier for the really complex. Today most of
that spectrum collapses to two rungs chosen by a regex.

**Current State:**
- `_should_think` is binary (fast vs thinking) and heuristic-only — no middle rung.
- Tiers exist (`lite`/`pro`/`smart` → now flash-lite / Gemini-Pro / Opus-4.8 etc.) but nothing routes *across* them by difficulty.
- No cascade: a cheap model's weak/uncertain answer is never detected and re-tried on a stronger model.
- No calibration: no measure of how often we escalate or whether the threshold is right.

**Impact:** We overspend (smart tier on easy turns) *and* under-serve (fast tier
on hard turns), and the just-added middle tier (Sonnet 5 / Luna / Terra) has no
routing path to be used at all.

## Goals

**Primary Goal:** A **complexity-graded ascending router** — a small, fast layer
that estimates each request's difficulty and dispatches to the cheapest tier that
can do it well, escalating (up-front or via cascade) only when the work demands
it — generalising the binary `_HeuristicRouter` into an N-rung ladder over the
current tier lineup.

**Success Metrics:**
- 3-rung ladder (fast → mid → top) live, mapped to the tier registry; the middle tier actually gets used.
- Escalation rate lands in the healthy band (roughly **5–50%** — below 5% = threshold too permissive, above 50% = too conservative — per the routing literature).
- Cost/quality: measurable drop in top-tier spend on easy turns with no quality regression on hard ones (measured via the eval suite + the 9.5 analytics rollup).

**Non-Goals:**
- Replacing per-skill model pins or the residency policy (routing chooses *within* what a skill/deploy allows; `resolve_model_chain` residency gating is unchanged).
- A bespoke fine-tuned router in v1 (start heuristic + optional cheap-classifier; a learned router is a spike, not a blocker).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Most turns resolve on the fastest tier → better perceived latency; escalation is the exception, and streams (never dead air). |
| 2 | EARNED TRUST | +1 | Cascade escalates precisely when the cheap model is *uncertain* — low-confidence answers get a stronger second opinion rather than shipping wrong-but-fast. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure under the skill; a skill author sees "it picks the right depth," not the ladder. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | This *is* the axiom, made literal across the full tier lineup — the headline win. |
| 5 | GRACEFUL DEGRADATION | +1 | Composes with the 7.7 fallback chains; an escalation target that's down falls back within its tier. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Internal routing; no protocol surface. |
| 7 | API FIRST | +1 | Routing lives in the agent factory, so every channel inherits it. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every routing/escalation decision is traced (chosen tier, complexity score, escalation reason) → the 9.5 analytics can calibrate the thresholds. |
| 9 | SECURE BY CONSTRUCTION | 0 | Routes within residency-allowed models only; no new trust surface. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All routing server-side. |
| | **Net Score** | **+7** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### The tier ladder (ascending skill level)

Three logical rungs, resolved through the existing tier registry (residency-aware
per `MODEL_RESIDENCY_POLICY`, so each rung has an EU variant):

| Rung | Purpose | Unrestricted (dev) | eu-strict (test/prod) |
|------|---------|--------------------|------------------------|
| **fast** | the easy 80%: chat, lookup, extraction, formatting | `gemini-flash-lite` (or Luna) | `gemini-flash-lite` |
| **mid** | deeper: multi-step, light analysis, ambiguity | `claude-sonnet-5` / `gpt-5.6-terra` | `gemini-2.5-pro` |
| **top** | really complex: hard reasoning, planning, verification | `claude-opus-4-8` / `gpt-5.6-sol` / `claude-fable-5` | `gemini-2.5-pro` |

A skill declares its **allowed rungs** (e.g. `fast` + `mid` + `top`, or a subset);
the router chooses within them. Existing per-skill `model`/`thinkingModel` pins
remain valid — a pin is just a one-rung ladder.

### How we assign complexity (the research question)

Two complementary mechanisms, layered (per the 2026 routing/cascade literature):

**A. Up-front ROUTE — classify difficulty, dispatch once (no wasted calls).**
Layered, cheapest-signal-first (generalises `_should_think`):
1. **Rule/heuristic pass** (near-zero cost) — the obvious cases: length, question
   count, `THINK_KEYWORDS` (analyze/compare/plan/prove…), explicit tool/RAG needs,
   attached-document size, multi-part requests. Catches the clear-fast and
   clear-hard ends.
2. **Cheap-classifier pass** (the ambiguous middle) — a small/fast model (or an
   embedding + logistic/matrix-factorisation router à la RouteLLM) scores
   difficulty → rung. Trained/calibrated on our own labelled turns (the 9.5
   capture is the training substrate). This is the **research spike**: features,
   labels, and whether a learned router beats a good heuristic for our traffic.

**B. CASCADE — try cheap, escalate on low confidence (the long tail).**
Start on the chosen rung; if the answer is **uncertain or fails a check**,
re-issue on the next rung up. Escalation signals: model-expressed uncertainty /
refusal, self-consistency divergence, a cheap verifier's thumbs-down, a tool/
extraction validation failure (e.g. the truncated-JSON class), or an explicit
"I'm not sure" from the fast model. Cascading costs an extra call on the tail but
catches what a pre-router misses.

**The production shape is A then B:** a rule pass handles the obvious, a cheap
classifier handles the ambiguous middle, and a cascade backstops the long tail —
exactly the layered pattern the literature recommends. **Calibration:** track the
escalation rate and keep it in ~5–50%; the 9.5 analytics rollup is where we watch
it and tune the thresholds per skill.

### Relationship to what exists
- **Generalises `_HeuristicRouter`** (2 rungs → N) — same "pick an agent per turn" seam, now over a ladder; `_should_think` becomes the rule-pass of mechanism A.
- **Subsumes 8.5 mid-turn-escalation** — "stream fast, then escalate within the turn" is exactly mechanism B (cascade) with the fast partial preserved; the EARNED-TRUST tension 8.5 flagged is handled here by escalating on *uncertainty*, marking the escalation visibly, and never silently overwriting the partial.
- **Complements 8.2 delegation** — intra-skill the router changes the *tier*; when the right answer is a different *specialist*, that's still a handoff. Complexity routing chooses depth; delegation chooses expertise.
- **Composes with 7.7 fallback chains** — routing picks the rung; `resolve_model_chain` picks a live model within it (residency-gated).

### Backend
- **New:** `backend/adk/complexity_router.py` — the rule pass + rung ladder + optional classifier hook + cascade controller; a `ComplexityRouter` that wraps N agents (one per allowed rung) and exposes `route(message, context) -> rung` and an escalation callback.
- **Config:** a `routing` block on `SkillMetadata` (allowed rungs, thresholds, cascade on/off) — default preserves today's behaviour (single pin or the 2-tier router).
- **Trace:** emit the chosen rung + complexity score + escalation reason (STAGE_PROGRESS + span attrs) for calibration.

## Implementation Plan

### Phase 1: N-rung ladder + rule router (~1.5d)
- [ ] `ComplexityRouter` generalising `_HeuristicRouter`; `routing` config on SkillMetadata; wire the 3 rungs to the tier registry (residency-aware). `_should_think` becomes the rule pass.

### Phase 2: Cascade / escalate-on-uncertainty (~1.5d)
- [ ] Escalation controller: detect low-confidence / validation-failure on the fast answer → re-issue on the next rung, streamed + visibly marked (subsumes 8.5). Token-budget guard.

### Phase 3: Research spike — learned classifier (~1d + ongoing)
- [ ] Evaluate a cheap-classifier route (embedding/matrix-factorisation vs a small LLM judge) on our labelled turns (9.5 capture); compare cost/quality vs the heuristic; ship only if it beats it.

### Phase 4: Calibration + observability
- [ ] Escalation-rate + cost/quality dashboards (9.5); per-skill threshold tuning; eval-suite guardrail (no quality regression on the hard set).

## Testing Strategy

- **Backend:** rung selection for representative easy/mid/hard turns; cascade fires on injected low-confidence / validation-failure; residency gating preserved per rung.
- **Eval:** a difficulty-labelled evalset — assert quality holds on hard turns while top-tier share drops on easy ones; escalation rate in band.

## Security Considerations

- Routes only within the models a skill + deployment already allow (residency by construction, `resolve_model_chain`) — routing never widens egress or the trust surface.

## Success Criteria

- [ ] 3-rung ladder live; the middle tier is actually used; per-skill allowed-rungs config.
- [ ] Cascade escalates on uncertainty/validation-failure, streamed + marked (never a silent swap of the answer).
- [ ] Escalation rate in ~5–50%; measurable easy-turn cost drop with no hard-turn quality regression.

## Open Questions

- OQ1: Heuristic-only v1 vs ship the cheap classifier immediately? (Lean: heuristic ladder + cascade first; classifier as a measured spike — don't block the ladder on ML.)
- OQ2: Is complexity global or per-skill? (A "PPA expert" turn and a "chat" turn of equal length aren't equally hard — likely per-skill thresholds, calibrated from that skill's traffic.)
- OQ3: Confidence signal for cascade — model self-report vs a cheap external verifier vs self-consistency? (Cost/latency trade-off; start with validation-failure + explicit-uncertainty, add a verifier if the tail warrants.)
- OQ4: Does the router live in the agent factory (per-turn, like `_HeuristicRouter`) or as a pre-agent step? (Lean: factory seam, to inherit callbacks/tracing.)

## Related Documents

- [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) — inter-skill escalation (depth vs expertise)
- [mid-turn-escalation.md](mid-turn-escalation.md) — **subsumed by this doc** (its cascade is mechanism B)
- [model-reliability.md](../v6.7.0/implemented/model-reliability.md) — fallback chains this composes with
- [analytics-and-reporting.md](../v6.9.0/analytics-and-reporting.md) — the calibration/observability substrate
- Research: [Cluster, Route, Escalate (arXiv 2606.27457)](https://arxiv.org/pdf/2606.27457) · [Dynamic Model Routing & Cascading survey (arXiv 2603.04445)](https://arxiv.org/pdf/2603.04445) · [Is Escalation Worth It? (arXiv 2605.06350)](https://arxiv.org/pdf/2605.06350) · [RouteLLM](https://github.com/lm-sys/RouteLLM) · [LLM Model Routing 2026 (digitalapplied)](https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide)
```
