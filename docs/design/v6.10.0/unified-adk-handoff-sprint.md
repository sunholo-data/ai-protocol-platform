# Sprint Plan — v6.10.0 Unified ADK-Native Handoff (HANDOFF-UNIFY)

**Design doc:** [unified-adk-handoff.md](unified-adk-handoff.md)
**Sprint id:** `HANDOFF-UNIFY`
**Duration:** ~2.5 days (3 milestones, sequential — shared files)
**Created:** 2026-07-15

## Goal

Collapse the three parallel handoff mechanisms into ONE ADK-native tool
(`transfer_to_agent`, enum-constrained) with the confirmation floor enforced as
policy in a single `before_tool_callback`. Make all three levels — automatic,
confirm, confirm-with-form — provably work via scripted end-to-end flows on
deployed envs. Fix the post-switch 403 by making the session index follow the
conversation.

**Why now:** the handoff is the ONE user-test centrepiece and it is unreliable at
the first hop (lite-model tool conflation) and breaks the security gate after a
switch (403). Both are design-level, not implementation slips.

## Locked decisions (do not re-litigate)

- ONE handoff verb = ADK-native `transfer_to_agent`; `TransferToAgentTool`
  enum-constrains `agent_name` (verified in installed ADK source). `request_handoff`
  is deleted, not shimmed.
- Floor is **policy, not AI judgement**: `auto` → native in-turn transfer;
  `confirm`/`confirm_with_fields` → `before_tool_callback` short-circuits (no
  transfer) → returns the 8.1 elicitation envelope → the shipped 8.2 full switch
  completes it. No `level` parameter for the model.
- Confirm-floor delegates are wired as **stub sub_agents** (name + description
  only) so the door's factory cost stays flat.
- Agent names are **slug-derived** (`one_obligation_analysis`), enum-constrained;
  uuid fallback only on collision.
- Session index (`chat_sessions/{id}.skill_id`) follows the **most recent turn's
  skill**; the binding gate is UNCHANGED (keeps its security purpose).
- **Verification is real AG-UI streams + a real browser.** jsdom/unit passing is
  NOT done — two design-level bugs shipped this week exactly that way. The three
  E2E flows are the definition of done.
- Chat backend is the frontend service's **sidecar** → backend changes deploy via
  the **FRONTEND** trigger (`trigger-aitana-<env>-platform-frontend`).

## Milestones

### M1 — Callback unification (backend) — ~1.5d — CRITICAL

The subtle core. Everything routes through one tool + one policy callback.

**Tasks:**
1. `_delegate_agent_name(skill)` — sanitized-slug namer + uuid-fallback on
   collision; replaces `_safe_agent_name` for delegate wiring. (~40 LOC)
2. `create_agent` delegate loop (agent.py:847-875): wire **ALL** accessible
   delegates as sub_agents — auto = real recursive `create_agent`, confirm/cwf =
   **stub** `LlmAgent` (name + description, no tools/instruction). Build a
   `delegate_map: {agent_name: (SkillConfig, DelegateRule)}` closure. (~70 LOC)
3. `make_handoff_policy_callback(delegate_map)` — `before_tool_callback`:
   non-`transfer_to_agent` → `None`; unknown name → `None` (ADK validates);
   `auto` floor → `None` (native transfer proceeds); `confirm`/`cwf` → build the
   elicitation envelope from `rule.fields`, `mark_delegation`, return the dict
   (skips the transfer). Compose into `compose_before_tool_callbacks` (agent.py:972). (~90 LOC)
4. Register `transfer_to_agent` in `a2ui_elicitation_render` `tool_names` with the
   `is_elicitation` matcher + offload-exempt (the tool-names render trap). (~15 LOC)
5. Delete `make_request_handoff_tool` + catalog/listing/alias map +
   `_HANDOFF_LEVEL_ORDER` (agent.py:492-607). (~-120 LOC)
6. `_ensure_session_index` (skill_processor.py:296): when the row exists and
   `idx.skill_id != skill_id`, UPDATE `skill_id` + append `skillHistory`. (~30 LOC)
7. Unit tests: callback policy (auto/confirm/cwf/unknown/cwf-degrade), naming +
   collision, delegate_map, session-index update, render-registry trap. (~200 LOC)

**Acceptance criteria (M1):**
- `make lint` + `make test-fast` green; `request_handoff` symbol gone from the codebase.
- Callback returns `None` for auto, an elicitation dict for confirm/cwf; a stub
  sub_agent is never executed.
- Session-index test: chat turn on an existing thread from a different skill
  updates `skill_id` + appends `skillHistory`.

**Risk:** HIGH — ADK callback/transfer semantics; stub-agent build cost; the
enum must actually constrain. Mitigate: a real backend stream probe at the end of
M1 (not just unit tests) confirming a single `transfer_to_agent` tool with the
slug enum.

### M2 — Deletions + frozen replayed cards — ~0.5d — LOW risk

**Tasks:**
1. Delete the backend `confirm_delegation` branch in `surface-action-run`
   (`_resolve_confirm_delegation_target`, the target-swap for gates 4-8). Keep
   `CONFIRM_DELEGATION_ACTION` as the frontend-intercepted action name only. (~-80 LOC)
2. Delete `_NON_SURFACE_TRIGGER_ACTIONS` framing suppression in
   `a2ui_surface_context.py` (only existed for the deleted branch). (~-10 LOC)
3. Frontend: replayed elicitation cards (`a2uiSurfaces` from history) always
   render frozen ("Submitted" record), never a live "Proceed". (~30 LOC + test)
4. Update/trim `surface-action-run` tests for the removed branch.

**Acceptance criteria (M2):** backend + frontend suites green; no `confirm_delegation`
handling left in `surface-action-run`; a history-replayed confirm card renders frozen.

### M3 — Acceptance harness + deploy + verify — ~0.5d — the definition of done

**Tasks:**
1. `scripts/handoff-e2e.sh [dev|test]` + `make handoff-e2e`: mints a token
   (aiplatform-cli), streams the three flows against a deployed env, asserts on
   the real AG-UI event sequence. (~120 LOC bash/python)
   - **AUTO:** "extract the clauses of the Google LEAP ppa" → `AGENT_DELEGATION` +
     specialist `TEXT_MESSAGE_CONTENT` same stream; no elicitation; no malformed call.
   - **CONFIRM:** "analyse the demo solar ppa and extract its obligations" →
     exactly one `transfer_to_agent`, intercepted, `placement:"chat"` confirm
     envelope; then re-issue on the same thread + a `surface-action-run` POST on
     the specialist page returns **200** (Failure-2 regression).
   - **CONFIRM_WITH_FIELDS:** a fields-declaring job → form envelope carries those
     fields; submit completes with values in run state.
2. Deploy to dev via the FRONTEND trigger; run `make handoff-e2e dev`.
3. Promote dev→test; run the test frontend trigger; run `make handoff-e2e test`.
4. Browser pass (`aitana-frontend-verify`): all three levels click through; form
   submits with NO 403; replayed cards frozen.

**Acceptance criteria (M3):** `make handoff-e2e` green on dev AND test; browser
click-through of all three levels verified; the exact user transcripts that
failed today now succeed.

## Model Assignment (MANDATORY)

| Stage | Model | Why |
|-------|-------|-----|
| Planning | `claude-opus-4-8` | This plan — architecture already fixed in the doc; synthesis. |
| **M1 execution** | `claude-fable-5` | Subtle: ADK before_tool_callback short-circuit semantics, enum-constrained transfer, stub-agent wiring, session-index invariant. First-shot correctness matters most here; two design-level bugs already shipped from this area. |
| M2 execution | `claude-opus-4-8` | Mostly deletions + one frozen-card guard; workhorse tier, snappier. |
| M3 execution | `claude-opus-4-8` | Harness authoring + deploy orchestration + browser drive; procedural, needs judgement on real-stream assertions but not deep subtlety. |
| Evaluation | `claude-opus-4-8` | Independent quality check against the E2E definition of done. |

Verify ids against `/claude-api` if switching. The executor honors this table —
**M1 runs on Fable**; do not silently downgrade.

## Day-by-Day

- **Day 1:** M1 tasks 1-6 (naming, delegate wiring, callback, render reg, delete
  request_handoff, session index). Backend stream probe.
- **Day 2 (am):** M1 task 7 (unit tests) + checkpoint. **Day 2 (pm):** M2 in full.
- **Day 3 (am):** M3 harness + dev deploy + verify; promote to test + verify;
  browser pass. Finalize.

## Success Metrics

- `request_handoff` + alias machinery deleted (~150 LOC net removed).
- `make handoff-e2e` green on deployed dev + test.
- Zero malformed handoff tool calls in a 20-turn scripted routing session on the
  lite door.
- The two transcripts that failed today (dev loop, test 403) now succeed.

## Quality Gates

- Per milestone: `cd backend && make lint && make test-fast`; frontend
  `npm run quality:check` where touched.
- M1 end: real backend stream probe (single `transfer_to_agent` w/ slug enum).
- M3: `make handoff-e2e` (real streams) + browser pass — non-negotiable.
