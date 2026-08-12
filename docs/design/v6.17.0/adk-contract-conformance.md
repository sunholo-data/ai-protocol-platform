# ADK Contract Conformance — Default to ADK, Tame the Custom Layer

**Status**: Proposed
**Priority**: P1 (Medium) — systemic reliability, not a feature gate
**Estimated**: ~6–8 days (phased; each phase independently shippable)
**Scope**: Backend (with a thin frontend touch for the handoff render path)
**Dependencies**: v6.10.0 unified-adk-handoff ✅, v6.8.0 elicitation-in-chat ✅
**Created**: 2026-07-22
**Last Updated**: 2026-07-22

## Problem Statement

We keep shipping the same *class* of bug: our custom layer assumes ADK behaves
one way, ADK behaves another, and the seam breaks — silently, past unit tests,
only visible on a real ADK run or a live stream. The trigger for this doc was the
2026-07-22 **confirm-handoff spam loop**: a `gemini-3.5-flash-lite` front door
emitted the "Hand this conversation to PPA Obligation Analysis?" card 10+ times
in one turn. Root cause: our custom confirm short-circuit returned an elicitation
envelope as the `transfer_to_agent` result but never set `skip_summarization`, so
ADK's flow did not treat the turn as final (`Event.is_final_response()` is true
**iff** `actions.skip_summarization` is set — `events/event.py`) and re-invoked
the model, which re-issued the transfer. ADK's *own* "ask the user and pause"
tool (`tools/get_user_choice_tool.py`) sets exactly that flag. We reimplemented
the pattern and dropped the one line that makes it terminate.

That is not an isolated miss. It is the dominant bug shape in this codebase.

**Current State — the bugs cluster at the custom↔ADK seam:**

| Recurring bug | Where it lives | ADK contract we violated |
|---|---|---|
| Confirm-handoff spam loop (2026-07-22) | custom handoff short-circuit | `skip_summarization` → `is_final_response` ends the turn |
| Un-awaited `save_artifact` → 404 | our callback wiring | ADK `save_artifact`/`load_artifact` are coroutines |
| `ResilientLlm` fallback → publisher 404 | model layer over ADK | ADK stamps `llm_request.model` once per member |
| raw-genai global-residency 404 | raw-genai tool seam | residency pin the agent path already had |
| AG-UI `RUN_STARTED` must be first | AG-UI streaming bridge | `@ag-ui/client` event-ordering contract |
| Required params → invent/interrogate | our tool declarations | ADK marks no-default params `required` |
| A2UI "won't render in Workspace" (recurring) | A2UI emitter | per-request `LatencyTracker` ContextVar binding |
| Multi-transfer double-delegation (parked) | custom handoff | `transfer_to_agent` is a baton pass, not fan-out |

Every row is the same story. None are random.

**Two structural aggravators:**

1. **ADK is unpinned.** `backend/pyproject.toml` declares
   `google-adk>=1.28.0,<2.0.0` — a *range*. ADK changes callback signatures,
   flow behavior, and event semantics between minor versions. Our bucket-B code
   (below) rides ADK internals, so a silent minor bump can move the ground under
   us with no code change on our side.
2. **We test below the seam, not across it.** Nearly every bug above passed
   `pytest`/jsdom and failed only against the real ADK `Runner` flow or a live
   AG-UI stream. Our unit tests assert our functions in isolation; the bugs live
   in how ADK *drives* those functions.

**Impact:** Developers (repeated multi-hour debug sessions re-deriving the same
seam), and end users (each bug is a user-visible defect — a spam loop, a dead
document fetch, a 404 on a specialist). This is the single highest-leverage
reliability investment available right now.

## Goals

**Primary Goal:** Cut recurrence of custom↔ADK boundary bugs by making the custom
layer *conform to ADK's contracts* — adopt ADK's stable primitives and control-flow
rules where they exist, pin ADK so its behavior can't drift silently, and test at
the ADK-flow boundary rather than below it. **This is a conformance and hardening
effort, not a rewrite** — most of the custom layer is irreplaceable and stays.

**Success Metrics:**
- **Zero** new boundary-class regressions ship undetected: every bucket-B/C
  module has a hermetic "real ADK flow" test (like
  `tests/unit/test_handoff_loop_termination.py`) that fails when the contract is
  violated.
- ADK is **pinned to an exact version**; upgrades are a deliberate PR with the
  flow-boundary suite as the gate.
- A written **ADK-contract conformance checklist** exists and is referenced by
  the design-doc and PR templates; the confirm-loop is captured as case study #1.
- The handoff/confirm path has a **documented migrate-vs-conform decision**
  against ADK's native `ToolConfirmation`, and is conformed accordingly.

**Non-Goals:**
- Rewriting bucket-A code (A2UI rendering, AG-UI bridge, Firestore mirror). ADK
  has no equivalent; there is nothing to "default to."
- Adopting ADK **experimental** features wholesale (`ToolConfirmation`,
  `PLUGGABLE_AUTH`). Experimental ADK is its own instability; we conform to
  *stable* contracts and treat experimental adoption as a case-by-case decision.
- A general refactor of `callbacks.py` / `agent.py` for aesthetics.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Removes a live spam loop (a turn that never terminates) and cuts wasted post-card model re-invocations; conformance reduces latency-path failure modes. |
| 2 | EARNED TRUST | 0 | No change to factual-claim provenance. |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure; invisible to end users and the skill builder. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal to routing (though it hardens the `ResilientLlm` seam that *implements* routing). |
| 5 | GRACEFUL DEGRADATION | +1 | Turns silent seam failures (loops, 404s, no-ops) into terminating, testable behavior; conformance is degradation-by-construction. |
| 6 | PROTOCOL OVER CUSTOM | +1 | The axiom made literal: prefer ADK's primitives/contracts over parallel custom mechanisms; retire divergent reimplementations. |
| 7 | API FIRST | 0 | No channel/API surface change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Flow-boundary tests + a conformance checklist make the seam observable and regression-guarded; the confirm-flow addition to `handoff-e2e` makes the loop observable on a real stream. |
| 9 | SECURE BY CONSTRUCTION | +1 | Pinning ADK removes an uncontrolled dependency-drift surface; "enforced by architecture, not developer discipline" is precisely the conformance-test philosophy. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Backend-weighted; the one frontend touch (handoff render) is unchanged in responsibility. |
| | **Net Score** | **+5** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scores -1).

## Design

### The taxonomy — three buckets, three different verdicts

The custom layer is **not** uniformly "too much." `backend/adk/` is ~8,400 lines;
it splits cleanly into three categories, each with a distinct verdict. Getting
this distinction right is the whole point — "default to ADK" is wrong for bucket
A and right for bucket C.

#### Bucket A — Irreplaceable custom (~half the code). Verdict: KEEP, do not touch.

ADK provides no equivalent, so there is nothing to default to.

- **A2UI rendering** (~2,900 lines): `a2ui_ppa_render`, `a2ui_obligation_render`,
  `a2ui_sources_render`, `a2ui_entsoe_render`, `a2ui_result_render`,
  `a2ui_elicitation_render`, `a2ui_surface_context`, `a2ui.py`. ADK has no UI
  protocol.
- **AG-UI streaming bridge** (`agui.py`): ADK does not speak AG-UI.
- **Firestore session mirror** (`session.py`), **skills-as-config**, **MCP-app
  surface**.

These are the platform's differentiators (Axiom #6 is *satisfied* by them). They
are custom because the protocols they implement did not exist in ADK. No action.

#### Bucket B — Required extensions that ride ADK internals. Verdict: KEEP, but PIN + test at the flow boundary.

ADK has a primitive but it is insufficient for us, so we wrap/extend it and must
track its behavior. These break when ADK's internals shift.

- **`ResilientLlm` / `model_errors`** (~640 lines): cross-provider retry +
  fallback chain. ADK gives one model, no failover. Known breakage:
  `resilient_llm_rewrites_model_per_member` (ADK stamps `llm_request.model` once).
- **`callbacks.py`** (1,263 lines): the RAG document loader, session-index mirror,
  permission enforcer, A2UI result emitter, signed-URL population, Model-B A2UI
  stripper — real features implemented via ADK's callback slots. Known breakage:
  `adk_async_callbacks_must_be_awaited`.
- **`agent.py`** callback composition (67 callback references), the
  instruction-provider chain.

**Remediation:** these stay, but (1) ADK gets pinned (below), and (2) each grows
a hermetic "real ADK flow" test that would catch a contract drift — not just a
unit test of the function in isolation.

#### Bucket C — Reimplementations that DIVERGE from an ADK primitive. Verdict: CONFORM or MIGRATE. This is the bug factory.

Smallest bucket (~15% of the code), **most of the recurring bugs.** Here we built
a *parallel mechanism* instead of using or conforming to ADK's.

- **Confirm / handoff elicitation** vs ADK's native `ToolConfirmation` +
  `requested_tool_confirmations` + `generate_request_confirmation_event` +
  `tool_context.request_confirmation`. Our path:
  `make_handoff_policy_callback` → `_build_handoff_envelope` →
  `make_elicitation_result` (a `before_tool_callback` returning a dict). The
  confirm-loop lived here.
- **Turn-finality handling**: the empty-run→`RUN_ERROR` rewrite and terminal-event
  dedup in `agui.py` re-derive turn-finality that ADK already models
  (`is_final_response`, `end_of_agent`).

**Remediation (the case study below): conform now, decide migrate later.**

### Case study & pattern: the handoff/confirm path (bucket C)

This is the template for how we treat every bucket-C item.

**What ADK offers (verified):** `ToolConfirmation` (`tools/tool_confirmation.py`)
with `hint` / `confirmed` / `payload`; `EventActions.requested_tool_confirmations`;
`functions.generate_request_confirmation_event`; and
`tool_context.request_confirmation(...)`. This is a first-class "require human
confirmation before this tool runs, then resume with a payload" flow — conceptually
exactly our confirm-floor handoff.

**The catch:** `ToolConfirmation` is decorated `@experimental(FeatureName.TOOL_CONFIRMATION)`.
Betting the handoff path on an experimental ADK feature trades one instability for
another, and its resume semantics differ from our "switch the session to the
specialist" model (see `confirm_handoff_reissues_on_target`). So:

- **Now (conform):** keep our custom confirm path, but make it a *thin, conformant
  shim* over ADK's flow contracts rather than a parallel universe. Concretely: the
  `skip_summarization` fix (shipped 2026-07-22) is step one; audit the rest of the
  path for other contract violations (does it respect `long_running_tool_ids`?
  does the terminal-event ordering match `is_final_response`? does the parked
  parallel-transfer case need the `after_model` all-but-first strip?).
- **Later (migrate decision):** when `ToolConfirmation` graduates from
  experimental, revisit with a written migrate-vs-keep decision. Record the
  decision either way; do not let it drift.

### Cross-cutting deliverables

**1. Pin ADK.** Change `google-adk>=1.28.0,<2.0.0` to an exact pin (e.g.
`google-adk==1.28.x`), matching `[eval]` too. Upgrades become a deliberate PR
gated by the flow-boundary suite. This alone removes the "ground moved under us"
class of bucket-B surprises.

**2. The "real ADK flow" test pattern — the standard.**
`tests/unit/test_handoff_loop_termination.py` (shipped 2026-07-22) is the
template: a stub `BaseLlm` drives the **real** `Runner` + `InMemorySessionService`
+ our callbacks, and asserts on ADK's actual behavior (turn terminates, event is
`is_final_response`, model called exactly once). It reproduced the loop
deterministically (7 re-invocations with the fix disabled, 1 with it). Every
bucket-B/C module gets one. These are hermetic (no GCP), fast, and run in
`test-fast`.

**3. The ADK-contract conformance checklist.** A short, living doc
(`docs/design/v6.17.0/adk-contract-checklist.md`) enumerating the contracts we've
been bitten by, each with the symptom and the guard:
- A tool/callback that "asks and waits" must set `skip_summarization` (or use a
  `LongRunningFunctionTool`) — else the turn re-invokes the model.
- ADK artifact/session calls are coroutines — `await` them.
- A `ResilientLlm`/fallback member must have `llm_request.model` rewritten per
  member.
- Any new SSE endpoint running `stream_agui_events` must bind/reset the
  per-request `LatencyTracker` (the A2UI render trap).
- No-default tool params are `required` — make them optional + return a
  `needs_input` envelope, and tell the model to call bare.
- `transfer_to_agent` is a control baton (one at a time), not fan-out — use
  `AgentTool` for concurrency.

Referenced from the design-doc template's Standards-Compliance step and the PR
template.

**4. Close the e2e coverage gap (shipped 2026-07-22).** `scripts/handoff-e2e.sh`
tested AUTO + DOCUMENT but not the confirm flow — which is why the loop shipped. A
`confirm` flow now hard-fails on >1 delegation/transfer. Same principle applies to
every bucket-C path: the live-stream harness must exercise it.

### Tooling surface (no new user-facing CLI)

This is infrastructure, not a developer resource, so no `aitana <resource>`
command. The one tooling deliverable is a **`make` target**:
`make adk-conformance` runs the flow-boundary suite (the `test_*_termination.py` /
`test_*_flow.py` family) as a named group, so it can gate an ADK version bump.
Add it to the root `Makefile` and the pre-push CI-parity rows in `CLAUDE.md`.

## Implementation Plan

### Phase 1: Pin + guard the known seam (~1.5 days) — ship first
- [x] Confirm-loop fix (`skip_summarization` in `make_elicitation_result`) + tests + e2e confirm flow — **done 2026-07-22**
- [ ] Pin `google-adk` to an exact version in `backend/pyproject.toml` (both entries); `uv lock`; run full suite (~0.25d)
- [ ] Write `adk-contract-checklist.md` seeded with the 6 known contracts (~0.5d)
- [ ] Add `make adk-conformance` target grouping the flow-boundary tests; wire into CLAUDE.md pre-push rows (~0.25d)

### Phase 2: Flow-boundary tests for bucket B (~2.5 days)
- [ ] `ResilientLlm` fallback: hermetic test asserting `llm_request.model` is rewritten per member across a simulated 404→fallback (~0.75d)
- [ ] Async-callback contract: a real-flow test that a document-loader `save_artifact` is awaited and retrievable via `load_artifact` (the 404 regression) (~0.75d)
- [ ] A2UI result emitter: a real-flow/stream test that `A2UI_SURFACE` emits under the bound tracker, and no-ops (visibly, not silently) without it (~1d)

### Phase 3: Bucket C conformance audit (~2 days)
- [ ] Audit the full handoff/confirm path against the checklist beyond `skip_summarization` (long-running ids, terminal ordering, the parked parallel-transfer `after_model` strip) (~1d)
- [ ] Audit `agui.py` turn-finality (empty-run rewrite, terminal dedup) against `is_final_response`/`end_of_agent`; add a real-flow test (~0.5d)
- [ ] Write the `ToolConfirmation` migrate-vs-conform decision record (revisit trigger: experimental→stable) (~0.5d)

### Phase 4: Process integration (~0.5–1 day)
- [ ] Reference the checklist from the design-doc template (Standards-Compliance step) and PR template
- [ ] Document the ADK-upgrade procedure (bump pin → run `make adk-conformance` → live `make handoff-e2e`) in the deploy skill

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None — Phase 1's confirm fix is already live behavior; the rest
is tests, a version pin, and docs. No runtime behavior toggles.

**Rollback Plan:** Each phase is independent. The only runtime change is the ADK
pin; if a pinned version regresses, bump the pin back (the flow-boundary suite is
the signal). The confirm-loop fix is already shipped and independently revertable.

**Environment Variables:** None.

## Testing Strategy

### Backend Tests (pytest)
- [x] Loop-termination real-flow test (`test_handoff_loop_termination.py`) — done
- [x] Elicitation `skip_summarization` unit tests — done
- [ ] `ResilientLlm` per-member model-rewrite real-flow test
- [ ] Async-callback await/roundtrip real-flow test
- [ ] A2UI emitter tracker-binding real-flow/stream test
- [ ] `agui.py` turn-finality real-flow test

### Live-stream (non-negotiable per CLAUDE.md)
- [x] `handoff-e2e.sh` confirm flow (hard-fails on >1 delegation) — done
- [ ] `make adk-conformance` green on the pinned version, and `make handoff-e2e ENV=dev` green after deploy

### Manual Testing
- [ ] Reproduce the original spam prompt on deployed dev post-deploy: exactly one confirm card, `text_after == 0`, no loop
- [ ] Bump ADK pin by one minor in a throwaway branch; confirm the flow-boundary suite catches any contract drift (validates the guard actually guards)

## Security Considerations

- Pinning ADK **reduces** the trust surface (no silent dependency drift into
  runtime behavior) — aligns with Axiom #9 (secure by construction).
- No new data-access patterns, no egress, no new user-controlled inputs reaching
  model context. The handoff target validation (canonical-id, access-checked)
  from v6.10.0 is unchanged.

## Performance Considerations

- The confirm fix *removes* wasted work (post-card model re-invocations, and the
  runaway loop). Net positive on the latency path.
- Flow-boundary tests are hermetic and fast (~1s each); negligible CI cost.

## Success Criteria

- [ ] `google-adk` pinned to an exact version; `make adk-conformance` exists and is green
- [ ] Every bucket-B module has a real-ADK-flow regression test that fails on its known contract violation
- [ ] `adk-contract-checklist.md` written and referenced from the design-doc + PR templates
- [ ] Handoff/confirm path audited against the checklist; `ToolConfirmation` migrate-vs-conform decision recorded
- [ ] `handoff-e2e.sh` confirm flow green on deployed dev; original spam prompt yields exactly one card
- [ ] All backend tests passing (`make test-fast`); lint clean (`make lint`)

## Open Questions

- **Exact ADK pin target:** pin to the current `1.28.x` we run, or first move to
  the latest `1.x` and pin there? (Leaning: pin what we run *now* in Phase 1, then
  upgrade deliberately as a separate PR through the new gate.)
- **`ToolConfirmation` timeline:** is there signal on when it graduates from
  `@experimental`? That sets the migrate-vs-conform revisit date.
- **Scope of bucket-C audit:** stop at handoff + turn-finality, or also sweep the
  MCP tool-result offload path and the Model-B stripper for divergences?

## Related Documents

- `docs/design/v6.10.0/unified-adk-handoff.md` — the handoff mechanism this hardens
- `docs/design/v6.8.0/` — elicitation-in-chat (8.1) primitive
- `docs/design/v6.7.0/implemented/tool-results-as-a2ui.md` — bucket-A A2UI render path
- Memory: `handoff_confirm_spam_loop_skip_summarization` (the trigger), `handoff_multi_transfer_research` (parked bucket-C residual), `resilient_llm_rewrites_model_per_member`, `adk_async_callbacks_must_be_awaited`, `a2ui_workspace_render_trap`, `agui_run_started_must_be_first`
- `backend/adk/CLAUDE.md` — the A2UI render playbook (bucket-A trap catalogue)
- `CLAUDE.md` §7 (protocols-first) and §8 (never-silent) — the architectural rules this operationalizes
