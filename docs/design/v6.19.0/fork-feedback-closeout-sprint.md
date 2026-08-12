# Sprint Plan: FORK-FEEDBACK-CLOSEOUT — v6.19.0

## Summary

**Goal:** Close all 11 open items from the CPH Uni AIPLA fork's upstream-feedback
log, then publish the public template refresh.

**Duration:** ~6.25 days of work across 5 milestones.
**Blocking:** The template refresh does not ship until all 5 are in (decided 2026-07-29).

**Key deliverables:**
- A privileged-by-default tool-result boundary
- A fork that can deploy: CI-gated, fail-loud project guard, no silent build-arg drops, serverless-safe anon groups
- An explicit auth audience at every call site
- Test doubles that model production session-ownership and state-scoping semantics
- Three client-surface fixes (viewport, render stability, artefact host portability)

## Current Status Analysis

### Recent Velocity

Last 14 days: **207 commits, 401 files, +36,918 / -2,464 LOC**. That is ~15
commits and ~2.6k insertions per day — AI-assisted throughput, sustained.

Estimate posture: the design docs' ~6.25d is **effort**, not elapsed calendar
time at that velocity. The realistic constraint is not typing speed but
verification — three milestones need something other than a green unit suite
(a real event stream, a real external MCP host, a deliberately-red CI run). The
plan below buffers for that rather than for LOC.

### Existing Implementation

- All 5 design docs exist under `docs/design/v6.19.0/` with SEQUENCE.md.
- Baseline is green: backend 2591 passed / 16 skipped, frontend 1142 passed,
  `make lint` clean, sanitized-tree gates 3/3 PASS.
- No milestone has a hard dependency on another; ordering below is by severity.

## Proposed Milestones

### Milestone 1: Stream boundary invariants (P0, ~1d, backend)

**Design:** [stream-boundary-invariants.md](stream-boundary-invariants.md) — AIPLA #39

**Descoped 2026-07-29:** #32 (RUN_ERROR terminality) is already implemented in `backend/adk/agui.py` — found at implementation start. M1 is now the privilege gate only.

**Tasks:**
- `backend/adk/stream_invariants.py`: `redact_privileged_results` (~90 LOC) + `CLIENT_VISIBLE_TOOLS`
- Wire into `fast_api_app.py::stream_skill` by **merging the prelude and the main loop into one generator**, so the two branches cannot diverge (the prelude split is exactly what AIPLA warned about)
- Structured log line per drop/redaction
- Tests (~150 LOC): redaction for group vs owner session; **fail-closed on unmatched result id**; allowlisted A2UI / `ui://` passthrough; offloaded-artifact pointer respects the gate

**Acceptance:**
- Privileged results absent from a group-token stream; unmatched ids redacted
- A2UI workbench render still works end-to-end (regression risk is real here)

**Risks:** The allowlist is the whole design. Too broad and #39 is not fixed;
too narrow and we break A2UI/workbench rendering — which is this repo's most
frequently re-broken subsystem. Mitigate by diffing against a known-good render
path before touching the allowlist.

### Milestone 2: Fork-ready defaults (P0, ~2.5d, fullstack)

**Design:** [fork-ready-defaults.md](fork-ready-defaults.md) — AIPLA #36, #42, #18, #16, #17

**Order within the milestone matters** — #36 first, so everything after it
deploys behind a gate.

**Tasks:**
- **#36** CI gate steps in `cloudbuild.yaml` + `_SKIP_CI_GATE` (manual-run only)
- **#42** derive project guard from ADC / `PLATFORM_DEFAULT_PROJECT`, fail-loud in non-LOCAL_MODE
- **#18** single source of truth for `NEXT_PUBLIC_*` + build fails on undeclared arg
- **#16** anon-group Firestore persistence (port the AIPLA implementation) + cold-cache path
- **#17** delete the dead `/gcs_config` plumbing

**Acceptance:** group code survives a restart at `min-instances=0`; undeclared
build arg fails the build; wrong project exits non-zero; red CI produces no
revision; `/gcs_config` gone.

**Risks:** #42's fail-loud boot can break a live env if any deployed service has
a project mismatch we don't know about. Land it after confirming env parity, and
verify on dev before test/prod.

### Milestone 3: Multi-audience auth (P1, ~1d, fullstack)

**Design:** [multi-audience-auth.md](multi-audience-auth.md) — AIPLA #33, #34

**Tasks:**
- `backend/auth/guards.py::assert_audience` + adopt in role-checking routes
- `fetchWithAuth(path, { audience })`; old helpers become deprecated wrappers
- `AGUIProvider` takes an explicit token source
- eslint path-scoped `no-restricted-imports` fence

**Acceptance:** cross-role token selection asserted both ways; wrong-surface
import fails lint; provider carries a bearer for an audience the global context
does not describe.

**Risks:** Prevention work on a symptom we cannot reproduce (no role split
upstream). Keep it a pure seam change — resist inventing roles.

### Milestone 4: Production-semantics test doubles (P1, ~1d, backend)

**Design:** [production-semantics-in-tests.md](production-semantics-in-tests.md) — AIPLA #35, #37b/c

**Tasks:**
- `tests/support/session_doubles.py::OwnershipEnforcingSessionService`
- `tests/support/state_doubles.py::ScopedState`
- Static tripwire: no `app:`/`user:` prefixed writes in per-session callbacks
- Fix stale `app:chat_session_initialized` docstring at `callbacks.py:788`

**Acceptance:** each double **fails against pre-fix code** (write red first,
then green — otherwise we ship a double that asserts nothing); both run in
`make test-fast` with no GCP credentials.

**Risks:** Low. Test-only, no runtime impact.

### Milestone 5: Client surface correctness (P2, ~0.75d, frontend + artefacts)

**Design:** [client-surface-correctness.md](client-surface-correctness.md) — AIPLA #23, #44, #38

**Tasks:**
- #23 body-owns-viewport layout shape + `min-h-0` on the chat column
- #44 `useMemo` the react-markdown `components` map, memo boundaries, stable empty array
- #38 artefacts emit `content` + `structuredContent` single-sourced; host detection via `ui/initialize` handshake, not `window.openai`

**Acceptance:** input visible at 700px with and without a banner; markdown DOM
nodes survive a parent re-render; an external MCP host's model receives artefact
interaction data.

**Risks:** #38 cannot be proven in jsdom. Needs a real host (MCP Inspector is
cheapest). If that verification slips, ship M5 without #38 rather than claiming it.

## Model Assignment

Scored per [resources/model-assignment.md](../../../.claude/skills/sprint-planner/resources/model-assignment.md).
Model ids verified against the current lineup.

| Stage | Model | Why |
|-------|-------|-----|
| Planning (this doc) | `claude-opus-5` | Interactive, spec already written |
| **M1 stream boundary** | `claude-fable-5` | Highest subtlety in the sprint: streaming semantics **and** a security gate, where a wrong-but-plausible filter passes shallow tests. Spec is complete. Exactly Fable's case |
| **M2 fork-ready defaults** | `claude-opus-5` | Five mostly-mechanical changes (config, flags, a proven port) but each needs judgement about blast radius on live envs. Interactive iteration beats long autonomous turns |
| **M3 multi-audience auth** | `claude-opus-5` | Mechanical seam refactor; the risk is scope creep, not subtlety |
| **M4 test doubles** | `claude-fable-5` | Must replicate Vertex ownership + ADK prefix scoping *exactly*; a double that is subtly wrong is worse than none. Well-specified, long-horizon |
| **M5 client surface** | `claude-sonnet-5` | UI + artefact edits with browser/host verification loops — procedural multi-step work |
| Evaluation | `claude-opus-5` | Judgement-heavy acceptance ("is the gate un-bypassable", "is the allowlist right") |

## Day-by-Day Breakdown

### Day 1 — M1
Stream invariants module + wiring + full test matrix. End of day: A2UI workbench
render verified unbroken against a known-good path.

### Day 2 — M2 (part 1)
#36 CI gate, #42 project guard, #18 build args. Verify the gate by pushing a
deliberately-red commit to a scratch branch.

### Day 3 — M2 (part 2)
#16 anon-group persistence + cold-cache tests; #17 removal. Dev deploy + smoke.

### Day 4 — M3 + M4
Auth seam + lint fence (morning); test doubles + tripwire, red-then-green (afternoon).

### Day 5 — M5 + closeout
Client surface fixes; external-host verification for #38; full CI parity both
stacks; regenerate the sanitized tree and re-run all three gates + manual sweep.

### Day 6 (buffer)
Verification overflow — the external MCP host and the live-stream checks are the
two most likely to slip.

## Quality Gates

After **each** milestone:
- Backend: `cd backend && make lint && make test-fast`
- Frontend: `cd frontend && npm run quality:check`

Before declaring the sprint done (non-negotiable, per the repo's standing rules):
- Sanitized tree regenerated: all 3 gates PASS **plus** the manual sweep
- Sanitized tree's own suites green (this is where 4 bugs only manifest)
- A real AG-UI event stream inspected for M1 — jsdom/unit green is not proof
- A real external MCP host for M5/#38

## Success Metrics

- All 11 AIPLA open items verifiably closed (re-run the triage greps)
- No regression: backend ≥2591 passed, frontend ≥1142 passed
- Sanitized tree: gates 3/3, suites green
- AIPLA's `upstream-feedback.md` re-annotated with the new statuses
- Template published

## Dependencies

None between milestones — all 5 are independent. M2's #36 should land first
*within* M2 so later work deploys behind a gate.

## Open Questions

1. **#17** — proceeding with delete (my recommendation, unopposed). Say so if
   you'd rather wire it to runtime-swappable skills; that becomes its own doc.
2. **M2/#42 fail-loud boot** — confirm no deployed service currently runs with a
   project mismatch before this lands, or it will refuse to boot on deploy.

## Notes

- Sprint state: `.claude/state/sprints/sprint_FORK-FEEDBACK-CLOSEOUT.json`
- On completion, re-run the downstream triage and update
  `cphu-aipla-app/docs/upstream-feedback.md` — the standing pre-refresh step now
  documented in the `aitana-template-publish` skill.
