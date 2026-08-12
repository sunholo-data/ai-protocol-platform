# v6.17.0 — Build Sequence

Systemic reliability, not a feature: the recurring bugs in this codebase cluster
at the seam between our custom layer and Google ADK. This version makes the custom
layer **conform to ADK's contracts** — pin ADK, test at the ADK-flow boundary, and
retire divergent reimplementations — rather than continuing to patch the seam one
symptom at a time. Triggered by the 2026-07-22 confirm-handoff spam loop.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [adk-contract-conformance](adk-contract-conformance.md) | P1 | ~6–8d | v6.10.0 unified-adk-handoff ✅, v6.8.0 elicitation ✅ | Phase 1 (pin + guard the known seam) ships first and is mostly done. Phases 2–4 each independently shippable. |

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 1 | Confirm-loop fix + tests + e2e confirm flow; pin `google-adk`; `adk-contract-checklist.md`; `make adk-conformance` | ~1.5d | Fix + tests ✅ (2026-07-22); pin/checklist/target Proposed |
| 2 | Flow-boundary regression tests for bucket B (`ResilientLlm` model-rewrite, async-callback await, A2UI emitter tracker-binding) | ~2.5d | Proposed |
| 3 | Bucket-C conformance audit (handoff/confirm beyond skip_summarization; `agui.py` turn-finality; `ToolConfirmation` migrate-vs-conform record) | ~2d | Proposed |
| 4 | Process integration (checklist into design-doc + PR templates; ADK-upgrade procedure in deploy skill) | ~0.5–1d | Proposed |

## What ships in v6.17.0

- **Phase 1 (mostly done):** the confirm-handoff spam loop is fixed centrally in
  `make_elicitation_result` (sets `skip_summarization`, mirroring ADK's
  `get_user_choice`), guarded by a deterministic real-ADK-flow test
  (`test_handoff_loop_termination.py`) and a new `confirm` flow in
  `handoff-e2e.sh`. Remaining: pin `google-adk` to an exact version, write the
  ADK-contract checklist, add `make adk-conformance`.
- **Phase 2:** every bucket-B module (model resilience, callbacks) gains a hermetic
  test that drives the real ADK `Runner` and fails on its known contract violation
  — closing the "passes unit tests, breaks on the real flow" gap.
- **Phase 3:** the bucket-C reimplementations (handoff/confirm, turn-finality) are
  audited against ADK's contracts and conformed; the native `ToolConfirmation`
  migrate decision is recorded with a revisit trigger.
- **Phase 4:** the checklist becomes process — referenced by the design-doc and PR
  templates so the next boundary bug is caught at review, not in production.

## Dependency graph

```
v6.10.0 unified-adk-handoff ─┐
v6.8.0 elicitation-in-chat  ─┴─► adk-contract-conformance
                                   Phase 1 (pin + guard) ─► Phase 2 (bucket-B tests)
                                                          ─► Phase 3 (bucket-C audit) ─► Phase 4 (process)
```
