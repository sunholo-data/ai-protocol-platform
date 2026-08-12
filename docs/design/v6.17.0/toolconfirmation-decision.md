# Bucket-C Audit — Decisions & Findings

**Status**: Decision record (v6.17.0 adk-contract-conformance, Milestone 3)
**Last Updated**: 2026-07-22

Bucket C is the set of custom reimplementations that *diverge from* an ADK
primitive or contract (the design doc's taxonomy). This records the audit
outcomes: what we conform now, what we migrate later, and what stays parked —
each with a revisit trigger so no decision silently rots.

---

## Decision 1 — Confirm/handoff: CONFORM now, migrate LATER (revisit when `ToolConfirmation` is stable)

**Our path today.** A confirm-floor handoff is a `before_tool_callback`
(`make_handoff_policy_callback`) that short-circuits `transfer_to_agent` by
returning an elicitation envelope (`make_elicitation_result`); the frontend
completes it as a full skill switch (`confirm_handoff_reissues_on_target`).

**What ADK offers.** A native, first-class confirmation flow:
`ToolConfirmation` (`hint` / `confirmed` / `payload`),
`EventActions.requested_tool_confirmations`,
`functions.generate_request_confirmation_event`, and
`tool_context.request_confirmation(...)` — conceptually "require human
confirmation before this tool runs, then resume with a payload," which is exactly
our confirm-floor handoff.

**Why not migrate now.** Two blockers, not one:
1. `ToolConfirmation` is decorated **`@experimental(FeatureName.TOOL_CONFIRMATION)`**.
   Betting the handoff path on an experimental ADK feature trades one instability
   for another — and ADK experimental features change without notice (we already
   pin ADK for exactly this reason).
2. Its resume model (re-run the same tool with `confirmed=True`) differs from ours
   (**switch the whole session to the specialist** and re-issue on the target —
   the deliberate UX from `confirm_handoff_reissues_on_target`). A migration is a
   UX change, not a like-for-like swap.

**Decision: CONFORM.** Keep the custom path, but make it obey ADK's flow
contracts rather than run as a parallel universe. Done this sprint: the
`skip_summarization` fix (C1) — the confirm short-circuit now ends the turn like
ADK's own `get_user_choice`, killing the spam loop. Guarded by
`test_handoff_loop_termination.py`.

**Revisit trigger:** when `ToolConfirmation` graduates from `@experimental`.
At that point, re-evaluate migrate-vs-keep with a written note — weigh the native
resume model against our session-switch UX. Until then, conform.

---

## Decision 2 — Parked parallel-transfer (multi `transfer_to_agent` in one response): STAY PARKED

**The residual.** A lite front door can emit two `transfer_to_agent` calls in ONE
model response (Gemini parallel function-calling) → two "Delegated to X" chips for
one query. The proposed fix is an `after_model_callback` that strips all-but-the-
first `transfer_to_agent` before ADK executes any (see
`handoff_multi_transfer_research`).

**Why parked, not fixed here.** Two reasons:
1. **It previously regressed.** An earlier per-call guard turned compound requests
   into a retry/timeout loop and was reverted. The `after_model` strip is a
   different (and more correct) approach, but the blast radius is the same code
   path, and it is **not** the bug that was reported (the reported bug was the
   cross-round confirm-spam loop, fixed by C1 — a *different* root cause).
2. **It needs live verification we can't complete from a dev laptop.** Per
   `verify_with_live_stream_pass_rate`, a handoff change ships only after N live
   AG-UI streams counting `AGENT_DELEGATION` markers on a *deployed* env. That
   gate can't be met in this pass.

**Decision: STAY PARKED.** It is a cosmetic double-chip ("weird UI, not broken" —
only the first specialist does real work), not a correctness or loop bug. The
distinction from C1 is now explicit in `handoff_multi_transfer_research` and
`handoff_confirm_spam_loop_skip_summarization` so it is never re-conflated.

**Revisit trigger:** if the double-chip becomes user-visible-annoying enough to
prioritize, OR if a compound-request eval is stood up (it would give the
regression a deterministic guard the earlier attempt lacked). Implement as the
`after_model` all-but-first strip, scoped to `transfer_to_agent` ONLY (never
`AgentTool`), guarded by a real-flow test AND a live `make handoff-e2e`.

---

## Finding 3 — `agui.py` turn-finality: SOUND, now GUARDED

**Audit question:** does `stream_agui_events`' empty-run handling correctly model
turn-finality, given the C1 change (confirm turns now end via `skip_summarization`)?

**Finding: sound.** The empty-run→`RUN_ERROR` rewrite keys on `produced_output`,
which counts `TEXT_MESSAGE_CONTENT`, `TOOL_CALL_START`, **and** `A2UI_SURFACE`
CUSTOM events. So:
- A confirm turn (post-C1) emits `TOOL_CALL_START` (transfer_to_agent) + an
  `A2UI_SURFACE` (the card) → `produced_output=True` → never mis-flagged empty.
- A Model-B render-only turn (no chat text) → `produced_output=True` via the
  `A2UI_SURFACE` branch. This exemption is **load-bearing**: dropping it would
  404 every workbench-render turn as an empty run.

**Gap closed:** this logic was previously untested (`test_agui.py` covers only
mounting). Added `test_agui_turn_finality.py` — locks both branches (empty →
visible `RUN_ERROR`; A2UI-surface-only → passes through as `RUN_FINISHED`).
Fail-on-revert verified: disabling the A2UI_SURFACE branch turns a render-only run
into a spurious `RUN_ERROR`.

No code change needed — the invariant was already correct; it just wasn't guarded.
