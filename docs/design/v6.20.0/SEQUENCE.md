# v6.20.0 — Build Sequence

Evaluation and observability follow-ups. Opened 2026-07-29 to hold work that
isn't fork-readiness (v6.19.0) and isn't a user-facing feature.

**✅ SHIPPED 2026-07-29: [first-run-path-conformance.md](implemented/first-run-path-conformance.md)** —
P0, ~1.5d, verified live. A downstream fork seeded from the public template, followed
WORKSHOP.md verbatim, and could not complete a single chat turn. Five findings,
one cause: our dev loop is Vertex + real GCP, the documented tier-1 path is
LOCAL_MODE + a Gemini Express key, and every defect is invisible from the first
and fatal on the second. Shipped in `b3b1644`, published as template `27b80e1`,
and confirmed by a REAL Express-mode chat turn on the reporting fork — not just
by the conformance test, because schema-side proof is the same class of evidence
that let the bug ship.

The one finding not fixed is the fork's #5 (210 inherited Dependabot alerts):
a dependency-upgrade programme, deferred with a stated reason rather than
silently skipped.

The evaluation doc below is unaffected and unchanged.

Two docs. The evaluation one is deliberately a **spike-first proposal** rather
than a committed build — the feasibility question it opens with (can Google's
online evaluator target a Cloud-Run-served agent at all?) determines whether
there is anything to build. The promotion one is a committed build, opened
2026-07-31 from the AIPLA fork's two still-open upstream-feedback entries.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [build-once-artifact-promotion](build-once-artifact-promotion.md) | P1 (Ph0+1), P2 (Ph2) | ~0.5d + ~1.5d + ~2–3d | CI gate (#36, shipped); infra repo owns trigger provisioning | **Source items #46 #47** — the only two AIPLA feedback entries not closed by FORK-FEEDBACK-CLOSEOUT. Prod today is the same commit *rebuilt*, not the tested bytes; images use mutable `:${BRANCH_NAME}` tags. Additive and non-breaking: the branch flow keeps working, the tag→test / copy-promote→prod path is added beside it as the template default for new forks. Net axiom score +3 (below the +4 band, same as doc 2 — deploy infra can't score higher); see the doc's threshold note. Acceptance requires one **real** promotion run, per #47's "a pipeline that has never run is not a pipeline". |
| 2 | [production-online-evaluation](production-online-evaluation.md) | P2 | ~1d spike + ~2–3d build | None (spike); build depends on spike outcome | **Phase 0 is a feasibility spike, not implementation.** Serving-runtime mismatch (we're on Cloud Run; the feature assumes Agent Runtime) is unverified and blocking. Also surfaces an unrelated question worth answering on its own: `telemetry.py`'s hardcoded `NO_CONTENT` override vs. Axiom #8's stated full-capture default. Net axiom score +3 (below the +4 band) — needs a human call; see the doc's threshold note. |

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 0 | Promotion Ph0: immutable `:${SHORT_SHA}`/`:${TAG_NAME}` image tags + deploy-by-digest on the existing branch flow | ~0.5d | Planned |
| 1 | Promotion Ph1: `cloudbuild.promote.yaml` (crane copy backend+toolbox by digest, rebuild UI from tag), `scripts/promote-env.sh`, `aiplatform deploy` CLI group, docs — **validated by one real dev→test run before the template refresh** | ~1.5d | Planned |
| 2 | Promotion Ph2: runtime frontend config (kills compile-time `NEXT_PUBLIC_*` and item #18's cause) — **spike first** | ~2–3d | Proposed |
| 3 | Online-eval feasibility spike (can an `OnlineEvaluator` attach to Cloud Run + Agent-Engine-sessions?) | ~1d | Proposed |
| 4 | Narrow monitor in `dev`: confidentiality/IAM sign-off, Safety + 1 custom metric, low sampling, dashboard + alert, CLI status command | ~2–3d | Proposed — **contingent on Phase 3** |

## What ships in v6.20.0

- **A release identity.** Every deployed revision references its backend and
  toolbox containers by digest, and an exact build stays addressable in Artifact
  Registry forever — so "what is prod running?" and "roll back to that one" both
  become one-command answers instead of inferences from a branch tip.
- **A promotion path new forks can take** without rediscovering that the Next.js
  frontend cannot be copied between environments (silent failure: prod serving
  test's Firebase project). Existing forks and this repo's branch flow are
  untouched — the path is added beside them, not in place of them.
- **Nothing from the evaluation doc, unless the spike says yes.** Phase 3's deliverable is a documented
  yes/no on whether online evaluation is reachable from our serving
  architecture without migrating off Cloud Run.
- **If yes:** continuous drift detection on live traffic in `dev` — sampled
  traces scored on a ~10min cycle, surfaced as a Cloud Monitoring time series
  with an alert threshold (per CLAUDE.md #8, NEVER SILENT: a metric nobody is
  alerted on is equivalent to not collecting it). Additive to, not a
  replacement for, offline `adk eval` — different failure modes.
- **Either way:** an explicit decision on the `telemetry.py` content-capture
  override, which is currently an unreconciled gap against Axiom #8/#9
  regardless of whether this feature ships.

## Dependency graph

```
build-once-artifact-promotion (independent of the eval track)
  Ph0 immutable tags + deploy-by-digest   ─► useful alone; no prerequisite
        │
        └─► Ph1 promote pipeline + CLI
                  │
                  ├─► first REAL dev→test run  ──► gates the template refresh
                  │
                  └─► (separate decision) flip platform prod to promote-only
        Ph2 runtime frontend config — spike; if yes, promotion becomes a pure copy
            and item #18's compile-time ARG cause disappears

production-online-evaluation
  Ph3 — feasibility spike
        │
        ├── NO  → doc stays Planned; revisit if serving architecture changes
        │
        └── YES → confidentiality/IAM sign-off ─► Ph4 build (dev only)
```
