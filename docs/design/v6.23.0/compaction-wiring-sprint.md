# Sprint Plan: COMPACTION-WIRE — make compaction actually run

## Summary

Wire the deployment's `App` into the chat path so conversation compaction runs
at all, replace ADK's lossy default summariser, verify it live, and make
compaction observable. Today the entire compaction configuration is dead code.

**Duration:** 3.5 days
**Scope:** Backend (M4 adds a small frontend surface)
**Dependencies:** None. Spike complete; failing guard already committed (`6573058`).
**Risk Level:** **High** — the wiring change routes through `from_app`, whose
defaults are the exact ones that permanently deleted 19 of 75 conversations.
**Design Doc:** [compaction-wiring-and-observability.md](compaction-wiring-and-observability.md)

## Current Status Analysis

### Recent Velocity
- 90 commits / 14 days; 206 files, +22,871 / −2,954.
- Recent comparable backend fixes (`44ca9b6` sessions, `7bcc99a` observability)
  each landed in well under a day including tests.
- Capacity is not the constraint here. **Verification is** — this sprint's
  predecessor shipped a confidently wrong root cause because unit tests were
  green against a config nobody read.

### Existing Implementation
- `backend/app.py` — builds the `App` with `events_compaction_config` (correct, unused by chat).
- `backend/adk/session.py` — per-model tuning table; token thresholds landed in `2fa1736`.
- `backend/adk/agui.py` — `build_agui_adk_agent`, the single seam both production
  chat paths (`skills/skill_processor.py:214`, `protocols/a2ui_surface_action_run_routes.py:394`) go through.
- `tests/unit/test_compaction_reaches_chat_runner.py` — 4 assertions, 3 currently
  `xfail(strict=True)`. **These are the sprint's definition of done for M1.**
- Backend suite: 2866 passed, 3 xfailed. Lint clean.
- **Uncommitted WIP from this session:** `adk/compaction_summarizer.py` (new) and
  a `session.py` edit — M2 work started before planning. Folded into M2 below;
  to be reviewed as part of it, not assumed correct.

## Proposed Milestones

### Milestone 1: Wire the App into the chat Runner
**Scope:** backend
**Goal:** `runner.app` is non-None on the chat path, carrying the deployment's compaction config.
**Estimated:** ~60 impl + ~80 tests = ~140 LOC
**Duration:** 0.5 day

**Tasks:**
- [ ] `build_agui_adk_agent` → `ADKAgent.from_app(app, ...)`, passing every existing kwarg explicitly (~40)
- [ ] Resolve or avoid the `app.py` ↔ `adk/agui.py` import cycle (lazy import or injected param) (~20)
- [ ] Support an `app_name` override without losing the App (copy with new name if it differs) (~10)
- [ ] Remove `_NOT_YET_WIRED` from the three guard assertions (~5)
- [ ] **Session-safety guard**: assert `delete_session_on_cleanup is False` and `session_timeout_seconds == 86400` survive `from_app` (~50)

**Files:**
- `backend/adk/agui.py` (modify, ~60 delta)
- `backend/tests/unit/test_compaction_reaches_chat_runner.py` (modify, ~50)
- `backend/tests/unit/test_agui.py` (extend, ~30)

**Acceptance Criteria:**
- [ ] `make adk-conformance` green with **zero xfail** in that file
- [ ] Session-safety assertions pass (`delete_session_on_cleanup=False`, 24h timeout)
- [ ] `make test-fast` green; lint clean
- [ ] A two-turn conversation still resumes history (manual smoke, real backend)

**Risks:**
- **Reintroducing the session-deletion bug.** `from_app` defaults
  `delete_session_on_cleanup=True` / `session_timeout_seconds=1200` — the exact
  values from the `44ca9b6` incident. *Mitigation:* the session-safety guard is
  written **before** the wiring change and must fail if either regresses. This is
  a hard gate; M1 does not pass without it.
- **`use_thread_id_as_session_id` defaults to False in `from_app`.** Losing it
  mints a fresh ADK session per turn — total history loss, worse than today.
  *Mitigation:* covered by the same guard.
- Import cycle. *Mitigation:* inject the App as a parameter rather than importing.

### Milestone 2: Summarizer that keeps the findings
**Scope:** backend
**Goal:** Compaction summaries retain tool results and specifics, and never mutate shared config.
**Estimated:** ~150 impl + ~100 tests = ~250 LOC
**Duration:** 1 day

**Tasks:**
- [ ] Review the WIP `adk/compaction_summarizer.py` as new code, not as done (~120)
- [ ] `get_compaction_config` returns a `model_copy` carrying the explicit summarizer (~30)
- [ ] Test: `function_call` / `function_response` appear in the summariser's input
- [ ] Test: shared config never mutated across callers or into `app.events_compaction_config`
- [ ] Test: oversized tool payloads are capped **and labelled** as truncated
- [ ] Test: unresolvable model → returns None, ADK default used, no raise

**Files:**
- `backend/adk/compaction_summarizer.py` (new, ~150)
- `backend/adk/session.py` (modify, ~40 delta)
- `backend/tests/unit/test_compaction_summarizer.py` (new, ~100)

**Acceptance Criteria:**
- [ ] Tool calls and results present in formatted summariser input
- [ ] Shared-singleton mutation impossible (regression test for the verified leak)
- [ ] Summarizer failure degrades to ADK default, never breaks a turn
- [ ] `make test-fast` green; lint clean

**Risks:**
- Pinning `pro` adds an LLM call on a background path. *Mitigation:* fires rarely;
  measure in M3.
- A verbose summary could itself be large. *Mitigation:* payload caps; M3 measures
  real summary size.

### Milestone 3: Live verification (the milestone that matters)
**Scope:** backend / ops
**Goal:** Prove compaction fires against a real backend and that fidelity survives it.
**Estimated:** ~120 LOC (harness + CLI)
**Duration:** 1 day

**Tasks:**
- [ ] Promote the session-scratch harness into `aiplatform session compaction <id>` (~60)
- [ ] Forced-threshold run: `COMPACTION_TOKEN_THRESHOLD` low, 25 turns, assert **≥1** compaction event (~30)
- [ ] Assert the compacted summary contains planted tool-result facts, not just chat (~30)
- [ ] Confirm history persists/resumes after restart (session-deletion regression, live)
- [ ] Measure TTFT before/after the per-request `App.model_copy`

**Acceptance Criteria:**
- [ ] ≥1 compaction event on a real session — **the exact inverse of the measured zero**
- [ ] Planted turn-1 facts AND a planted tool result survive the compaction
- [ ] Chat history resumes correctly
- [ ] No TTFT regression beyond noise

**Risks:**
- **A weak probe passing vacuously.** The 2026-08-06 canary passed under both
  arms and proved nothing. *Mitigation:* the probe must plant a fact only
  reachable via a **tool result**, and must assert a compaction actually
  occurred — not merely that recall worked.
- Local env drift (wrong service on the port). *Mitigation:* assert the backend's
  identity before trusting any result.

### Milestone 4: Never-silent compaction
**Scope:** fullstack
**Goal:** A user and a triager can both see that history was summarised.
**Estimated:** ~90 impl + ~60 tests = ~150 LOC
**Duration:** 1 day

**Tasks:**
- [ ] Emit a compaction event through the existing activity path (~50)
- [ ] `ActivityPanel` marker — "History summarised — turns 1–18 at 251K tokens" (~40)
- [ ] Tests both sides (~60)

**Acceptance Criteria:**
- [ ] Compaction visible in Activity on a real run
- [ ] Event carries **metadata only** — never summary text (group-session safe)
- [ ] Frontend + backend tests green

**Risks:**
- Leaking summary text into a lower-trust session. *Mitigation:* metadata-only
  asserted by test.

## Model Assignment

| Stage | Model | Why |
|-------|-------|-----|
| sprint-planner | `claude-opus-4-8`, high | Decomposition over a complete design doc; interactive. |
| **M1 executor** | **`claude-fable-5`, xhigh** | Highest subtlety in the sprint: a session-lifecycle + protocol-boundary change where a wrong-but-plausible implementation passes shallow tests — which is *literally the failure that produced this sprint*. Spec is complete (exact kwargs enumerated), so Fable's strength applies. |
| M2 executor | `claude-opus-4-8`, xhigh | Well-specified, self-contained; subclass one method + config plumbing. |
| M3 executor | `claude-opus-4-8`, xhigh | Judgment-heavy: deciding whether a probe *actually* discriminates is exactly where this project has erred. Not a mechanical loop. |
| M4 executor | `claude-opus-4-8`, xhigh | Routine fullstack surface on a proven event path. |
| sprint-evaluator | `claude-fable-5` | **Cross-model diversity** — M1 is the risky milestone and should not be evaluated by the model that wrote it. |
| Sub-agents (greps, inventories) | `claude-haiku-4-5` | Mechanical fan-out. |

## Day-by-Day

| Day | Work |
|-----|------|
| 1 (am) | M1 — session-safety guard first, then wiring; conformance green |
| 1 (pm) | M2 — summarizer review + config copy + tests |
| 2 | M3 — live verification, forced threshold, TTFT |
| 3 | M4 — observability, both sides |
| 3 (pm) | Evaluation pass, doc updates, `move_to_implemented` |

## Success Metrics

- `make adk-conformance` green, **zero xfail** in the guard file
- `make test-fast` green; ruff + format clean
- ≥1 compaction event on a real session (inverse of measured zero)
- Tool-result facts survive a real compaction
- Chat history persists — no repeat of `44ca9b6`
- No TTFT regression

## Explicit Non-Goals

- Re-tuning thresholds (numbers stay as shipped in `2fa1736` until M3 can measure)
- Adopting plugins / resumability just because `from_app` exposes them
- Re-opening Tomas's context loss — cause identified (`44ca9b6`), awaiting his re-run
