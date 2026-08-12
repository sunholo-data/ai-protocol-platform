# Production (Online) Evaluation for ADK Agents

**Status**: Planned
**Priority**: P2 (Low)
**Estimated**: ~1 day (spike) + ~2-3 days (build, if spike succeeds) — see phased plan
**Scope**: Backend + Ops
**Dependencies**: None (spike is standalone); build phase depends on spike outcome
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

Today the platform has exactly one evaluation mechanism: **offline `adk eval`**
(`make eval`, `backend/tests/eval/evalsets/basic.evalset.json`), run manually
or in CI against a fixed evalset. It answers "did this change regress a known
scenario?" It cannot answer "is the agent's quality drifting on *real* traffic
right now?" — a hallucination rate creeping up, a tool-selection pattern
degrading after a model or prompt change, a skill silently getting worse for
one customer's document shapes. Nothing currently samples live production
traces and scores them.

Google's Gemini Enterprise Agent Platform ships a feature for exactly this gap
— **online evaluation**: an `OnlineEvaluator` that asynchronously scores
sampled live traces on a ~10-minute cycle and surfaces drift as a
Cloud-Monitoring time series (see
[the online-evaluation docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize/evaluation/evaluate-online)).
This doc evaluates whether/how to adopt it — it is a roadmap proposal, not a
committed build, because two load-bearing assumptions need verification
before committing effort (see Open Questions).

**Current State:**
- One evalset (`basic.evalset.json`), run via `make eval`, manual/CI-triggered only.
- No continuous scoring of production traffic anywhere in the stack.
- `backend/observability/telemetry.py` already sets
  `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` (one of the two
  OTel prerequisites for online eval) whenever `LOGS_BUCKET_NAME` is set.
- The other prerequisite — `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`
  — is present as a toggle but the code **unconditionally overrides whatever
  value is requested to `"NO_CONTENT"`** (`telemetry.py:38-40`). This is a
  discrepancy worth flagging on its own: [Axiom #8](../../product-axioms.md)
  states full prompt/response capture (`=true`/`EVENT_ONLY`) is the *default*
  policy for telemetry inside the GCP trust boundary, and Axiom #9's privacy
  table explicitly lists Cloud Trace as a trusted-zone sink needing no special
  justification. The current hardcoded `NO_CONTENT` override either predates
  that axiom decision or was a deliberate conservative choice that was never
  reconciled with it — either way it should be resolved explicitly (see Open
  Questions), not left as an accidental gap.
- Agents are served on **Cloud Run** (`platform-backend`), not on **Vertex AI
  Agent Runtime** (Reasoning Engine) as the execution environment. Agent
  Engine is used only as a session/memory backing store
  (`AGENT_ENGINE_ID`, `backend/scripts/bootstrap_agent_engine.py`,
  `backend/adk/session.py`) — the agent code itself never runs there.

**Impact:**
- Engineering/ops: no automated signal today for quality drift between
  releases on real customer traffic — regressions are currently caught only
  by manual observation or a customer complaint.
- Customers (ONE / Acme Energy): a silent drop in extraction accuracy or a
  rise in hallucinated answers on their PPA documents would go undetected
  until reported.

## Goals

**Primary Goal:** Determine whether Google's online-evaluation feature is
usable against this platform's actual serving architecture, and if so, adopt
it narrowly (one metric, low sampling) as a drift-detection signal that
complements — not replaces — offline `adk eval`.

**Success Metrics:**
- Spike answers definitively: can an `OnlineEvaluator` attach to a
  Cloud-Run-served agent that only uses Agent Engine for sessions? (yes/no,
  with evidence)
- If yes: a dashboard exists showing at least one quality metric (Safety)
  trending over time for `dev`, with alerting wired per the "NEVER SILENT"
  principle (CLAUDE.md #8) so drift doesn't sit unnoticed.
- Zero incidents of confidential customer content (ONE's PPA data) reaching
  an unintended audience as a side effect of enabling message-content capture.

**Non-Goals:**
- Replacing offline `adk eval` — pre-merge regression testing on fixed
  evalsets stays the deterministic gate; online eval is a live-traffic
  drift signal, a different failure mode.
- Full-coverage evaluation of all production traffic — this is a monitoring
  signal, not an audit trail; start at low sampling %.
- Migrating the serving runtime from Cloud Run to Agent Runtime — that would
  be a separate, much larger architectural decision this doc does not
  propose making just to unlock online eval.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Async, out-of-band scoring; no impact on request-path latency. |
| 2 | EARNED TRUST | +1 | Direct instrument for the hallucination-rate KPI Axiom #2 already names; gives a live, continuous measurement instead of only CI-time snapshots. |
| 3 | SKILLS, NOT FEATURES | 0 | Ops/observability tooling, invisible to skill users. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Does not change model routing. |
| 5 | GRACEFUL DEGRADATION | 0 | Evaluator failure does not affect the serving path (fully decoupled, async). |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses Google's managed Evaluation Service + standard OTel `gen_ai.*` semconv instead of building a custom trace-sampling/scoring pipeline. |
| 7 | API FIRST | 0 | No channel-specific surface; ops-only. |
| 8 | OBSERVABLE BY DEFAULT | +1 | This *is* observability: closes the "drift detection" gap the axiom's KPIs call for (trace coverage, content capture) but that nothing currently measures continuously. |
| 9 | SECURE BY CONSTRUCTION | 0 | Per Axiom #9's own privacy table, Cloud Trace is inside the GCP trust boundary — full content capture there is the *stated default*, not a new egress. Scored 0 rather than +1 because the IAM tightening this doc requires (restricting who can create/attach `OnlineEvaluator`s, per the Google doc's own warning) is a necessary safeguard being added *because of* this feature, not an independent hardening it contributes elsewhere. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | No frontend surface. |
| | **Net Score** | **+3** | Below the +4 threshold — see note below. |

**Conflict Justifications:** No axiom scored -1; there is nothing to justify
as a conflict. The net score sits at +3 (below the nominal +4 "proceed"
threshold) purely because this is an ops/backend-internal feature with no
frontend, skill, or routing surface — most axioms are legitimately neutral by
nature (compare Axiom #7's own scoring guide: "Feature is backend-only and
channel-agnostic by nature" scores 0, not a penalty). Given the low blast
radius (Non-Goals above; async, decoupled, gated behind an unresolved spike),
this is treated as an intentionally low-priority (P2) roadmap item rather than
redesigned to force a higher score — flagged here for team sign-off, since the
score derives from axiom-neutrality rather than an actual weakness.

## Design

### Overview

Two-phase approach. **Phase 0 (spike, ~1 day)** answers the open question of
whether Google's `OnlineEvaluator` can target our Cloud-Run + Agent-Engine-
sessions hybrid at all, in the `your-project-id` project, with zero
production impact. **Phase 1 (build, ~2-3 days, only if the spike succeeds)**
wires a narrow, low-sampling monitor with an explicit confidentiality/IAM
decision made *before* any message-content capture changes, plus dashboard
and alert visibility.

### Phase 0 — Spike (verify feasibility before committing)

1. In `your-project-id`, confirm Cloud Trace is enabled and check whether
   `gen_ai.*`-attributed spans from the existing (metadata-only) telemetry
   setup are visible in Cloud Trace at all today.
2. In the Agent Platform console, attempt to create an `OnlineEvaluator` and
   see whether `platform-backend` (or any agent identity backed by our
   `AGENT_ENGINE_ID`) appears as a selectable target. Google Dev Knowledge MCP
   (`mcp__google-dev-knowledge__search_documents`) and/or a support channel
   should be used to confirm whether Agent Runtime deployment is a hard
   requirement or whether trace-based discovery (any project emitting
   `gen_ai.*` spans to Cloud Trace) is sufficient.
3. Document the answer plainly: **can we do this without migrating off Cloud
   Run, yes or no** — this determines whether Phase 1 exists at all.

### Phase 1 — Build (contingent on Phase 0 success)

**Confidentiality/IAM decision (must happen before any code change):**
- Reconcile the `telemetry.py:38-40` hardcoded `NO_CONTENT` override against
  Axiom #8/#9's stated default (full capture inside the GCP trust boundary
  needs no special justification). Get explicit written sign-off — this repo's
  CLAUDE.md hard rule on customer-confidential content means "the axiom says
  it's fine" is necessary but not sufficient; a human decision is required
  given ONE's PPA data will be the first real customer content this touches.
- Audit and restrict IAM: per the Google doc's own security note, "any user
  who can create an `OnlineEvaluator` can attach it to any agent in the
  project" — creation permission must be limited to platform admins, not the
  default authenticated-developer role.

**Rollout scope, narrow by design:**
- Start in `dev` only. Metric set: **Safety** (predefined) + one custom
  metric tied to a concrete KPI already in Axiom #2 (e.g. citation presence /
  groundedness on `one-ppa-expert` skill responses). Do not start with the
  full metric catalog.
- Sampling: low percentage (e.g. 5-10%) and a conservative max-samples-per-cycle
  cap — this is a monitoring signal, not full-coverage audit.
- Filter traces to exclude trivial/short interactions (per the doc's
  duration/token filtering) to concentrate sampling on substantive turns.

**Visibility (CLAUDE.md principle #8, NEVER SILENT):**
- Wire the Cloud Monitoring time series into whatever dashboard the team
  already watches for deploys (see [docs/ops/deployed-urls.md](../../ops/deployed-urls.md)
  and the smoke-test tooling) rather than leaving it as a console-only view
  nobody checks.
- Define an explicit alert threshold (e.g. Safety score drop past N over M
  cycles) — a metric nobody is alerted on is equivalent to not collecting it.

### CLI Surface

Deferred until after Phase 0. If the spike confirms feasibility, add to the
`aiplatform` CLI (see
[local-dev-cli.md](../v6.1.0/local-dev-cli.md)) a thin read-only surface —
`aiplatform eval online status [--env dev|test|prod]` — that queries the
Cloud Monitoring time series for the configured metric(s) and prints the
latest score + trend, so checking drift doesn't require opening the GCP
console. Not scoped further here since it depends entirely on Phase 0's
outcome (there may be nothing to query if the spike fails).

### Architecture Diagram (if Phase 0 succeeds)

```
[Live agent traffic] → [Cloud Trace / Cloud Logging, gen_ai.* spans]
                              ↓ (sampled, ~10min cycle)
                     [OnlineEvaluator / Evaluation Service]
                              ↓
                     [Cloud Monitoring time series]
                              ↓
              [Dashboard + alert]        [aiplatform eval online status]
```

## Implementation Plan

### Phase 0: Spike (~1 day)
- [ ] Confirm Cloud Trace + `gen_ai.*` span visibility in `your-project-id` today
- [ ] Attempt `OnlineEvaluator` creation against our Cloud-Run/Agent-Engine hybrid; record whether the agent is selectable
- [ ] Write up the yes/no answer and evidence in this doc's Open Questions

### Phase 1: Build (~2-3 days, only if Phase 0 succeeds)
- [ ] Confidentiality/IAM sign-off: reconcile `telemetry.py` NO_CONTENT override vs. Axiom #8/#9 default, restrict `OnlineEvaluator`-creation IAM (~0.5 day, decision + IAM change)
- [ ] Enable `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY` in `dev` only, behind the env var already present (~0.25 day)
- [ ] Create the narrow monitor (Safety + 1 custom metric, low sampling %) in `dev` (~0.5 day)
- [ ] Wire dashboard/alert visibility (~0.5 day)
- [ ] `aiplatform eval online status` CLI command (~0.25 day)
- [ ] Smoke-test: confirm a deliberately bad response gets flagged within one ~10min cycle

## Migration & Rollout

**Database Migrations:** None — Google-managed service, no Firestore/GCS schema changes.

**Feature Flags:** The existing `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`
env var is the gate; keep it `false`/`NO_CONTENT` everywhere except `dev` until
the confidentiality decision explicitly extends it further.

**Rollback Plan:** Delete the `OnlineEvaluator` monitor (no data-path dependency
on it — purely additive/observational) and revert the env var. No agent code
depends on this existing.

**Environment Variables:**
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY` (dev only, pending sign-off)
- No new env vars beyond what `telemetry.py` already reads.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Unit test pinning `telemetry.py`'s content-capture behavior. Currently
      the requested value is unconditionally overridden to `NO_CONTENT`, so
      this is a behavior change requiring a test either way — whether the
      resolution is "keep it hardcoded, document why" or "make it
      env-configurable, default closed"

### Manual Testing
- [ ] Phase 0 spike itself is the primary manual verification step
- [ ] If Phase 1 proceeds: send a deliberately low-quality query, confirm it
      surfaces in the dashboard within one evaluation cycle (~10 min)

## Security Considerations

- **Customer-confidential content**: ONE's PPA contract content is the first
  real customer data this would expose to a new internal sink (Cloud Trace
  spans, if content capture is enabled). Per Axiom #9's privacy table this is
  inside the trust boundary and needs no egress justification — but per this
  repo's CLAUDE.md hard rule, any doubt about "OK to make visible" must stop
  and ask rather than proceed on axiom-reading alone. Get explicit sign-off
  before flipping the capture flag anywhere touching ONE's data.
- **IAM**: restrict `OnlineEvaluator` creation to admins — the Google doc
  states any user who can create one can attach it to *any* agent in the
  project, which is a broader blast radius than the feature's own UI implies.
- **No public exposure**: this feature only writes to Cloud Trace / Cloud
  Monitoring / Cloud Logging, all inside the GCP project edge — no public GCS
  buckets, no unauthenticated Cloud Run endpoints — so it does not touch the
  "public artefact" hard rule directly. The "if in doubt, ask" clause still
  applies to the content-capture flag change.

## Performance Considerations

- Fully async/out-of-band (Google-managed ~10min cycle) — no impact on
  request-path latency, no impact on Axiom #1 (INSTANT FEEL) KPIs.
- Sampling percentage and max-samples-per-cycle bound cost; start low.

## Success Criteria

- [ ] Phase 0 spike completed with a documented yes/no answer on feasibility
- [ ] If yes: `dev` has a working `OnlineEvaluator` monitor with Safety + 1 custom metric
- [ ] Confidentiality/IAM decision explicitly made and recorded (not defaulted silently)
- [ ] Dashboard + alert wired (no metric collected without someone watching it)
- [ ] `aiplatform eval online status` works end-to-end (if Phase 1 ships)

## Open Questions

- **[BLOCKING Phase 1]** Can an `OnlineEvaluator` target an agent served on
  Cloud Run that only uses Agent Engine for session storage, or does it
  strictly require Agent Runtime as the serving environment? Unverified —
  this is Phase 0's entire purpose.
- Is the `telemetry.py:38-40` hardcoded `NO_CONTENT` override intentional
  (a conservative decision made after Axiom #8/#9 were written but never
  reconciled with them) or an oversight? Needs an answer independent of
  whether this feature ships at all.
- If Agent Runtime deployment turns out to be a hard requirement, is a
  parallel Agent-Runtime-hosted deployment (purely for eval purposes, not
  serving real traffic) worth the added infrastructure surface, or does this
  proposal simply not ship until/unless the serving architecture changes for
  other reasons?
- What's the right custom metric for `one-ppa-expert` specifically —
  groundedness/citation-presence (ties to Axiom #2's citation-rate KPI) is
  the working assumption above but hasn't been validated against what the
  Evaluation Service's custom-metric API actually supports.

## Related Documents

- [Product Axioms](../../product-axioms.md) — Axiom #2 (EARNED TRUST,
  hallucination-rate KPI), #8 (OBSERVABLE BY DEFAULT, content-capture
  default), #9 (SECURE BY CONSTRUCTION, privacy boundary table)
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface convention
- [docs/ops/deployed-urls.md](../../ops/deployed-urls.md) — deployed services + smoke tooling
- `backend/tests/eval/evalsets/basic.evalset.json` — existing offline evalset
- `backend/observability/telemetry.py` — current OTel/GenAI telemetry setup
- `backend/scripts/bootstrap_agent_engine.py` — Agent Engine session-store bootstrap
- CLAUDE.md — "NEVER make confidential customer content publicly accessible" hard rule
