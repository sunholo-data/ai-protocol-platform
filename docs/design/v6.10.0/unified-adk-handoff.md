# Unified ADK-Native Handoff — one tool, one policy point, one completion path

**Status**: Planned
**Priority**: P0 (High)
**Estimated**: 2.5 days
**Scope**: Fullstack (backend-heavy; frontend already shipped the completion path)
**Dependencies**: 8.1 elicitation-in-chat primitive (shipped), 8.2 confirm→switch full-switch frontend (shipped)
**Created**: 2026-07-15
**Last Updated**: 2026-07-15

## Problem Statement

Live testing on 2026-07-15 produced three failures in one afternoon. Two of them
are **design-level** faults in the handoff architecture, not implementation slips:

**Failure 1 — the door conflates two handoff tools (dev, 16:00).** The ONE
Assistant front door (a `lite` model) carries BOTH ADK's native
`transfer_to_agent(agent_name)` (for auto-floor sub_agents) AND our custom
`request_handoff(target_skill_id, reason, level)` (for confirm-floor jobs). Asked
to analyse obligations, the model emitted `transfer_to_agent` with
`request_handoff`'s argument schema — no `agent_name` — failed, retried the same
malformed call three times, and dead-ended with an apology. A lite model cannot
reliably juggle two tools with overlapping semantics and different schemas; no
prompt tweak fixes a schema coin-flip.

**Failure 2 — session↔skill binding 403 after a switch (test, 14:01).** The
confirm→switch navigates to the specialist on the same thread. But the session
index row permanently records the skill the session was *created* on (the door).
The specialist's obligation form then POSTs
`/api/skills/{specialist}/sessions/{id}/surface-action-run`, the gate compares
URL skill vs `idx.skill_id`, sees door ≠ specialist → 403 "This action isn't
permitted for this skill". The switch semantics violated an invariant a security
gate depends on — because the switch was bolted around the session model rather
than into it.

**Failure 3 (fixed same day, root cause instructive) — `MODEL_RESOLVED` emitted
before `RUN_STARTED`** broke every chat: a bespoke emission path re-derived a
wire invariant the framework client enforces.

**Current State — three parallel handoff mechanisms:**
- ADK-native `transfer_to_agent` for auto-floor delegates (sub_agents).
- Custom `request_handoff` with a hand-rolled catalog string, alias map
  (sanitized-name/slug/display → canonical id), and AI-judged `level`.
- A backend `confirm_delegation` surface-action-run branch (already superseded by
  the frontend full switch, still deployed as drift bait).

Each mechanism has its own catalog, id format, gates, and emission path. Every
recurring bug class this repo documents (A2UI-won't-render, friendly-names,
never-silent) has hit this feature because each path re-derives the invariants.

**Impact:** the multi-agent handoff is the centrepiece of the ONE user test and
the pattern for "even more critical handoffs in the future" (user, 2026-07-15).
It is currently unreliable at the first hop.

## Goals

**Primary Goal:** exactly one handoff verb the model can utter, with the
three levels — **automatic**, **confirm**, **confirm-with-form** — all working
and covered by scripted end-to-end acceptance tests on deployed envs.

**Success Metrics:**
- The three acceptance flows (§Testing Strategy) pass on deployed dev AND test.
- `request_handoff` + its catalog/alias machinery deleted (≈150 LOC) with no
  replacement custom tool.
- Zero malformed handoff tool calls in a 20-turn scripted routing session on the
  `lite` door model.

**Non-Goals:**
- Mid-turn escalation (8.5, design-ahead) — unchanged.
- The elicitation primitive (8.1) — unchanged; this doc only removes one of its
  producers. Tool-authored forms (`map_ppa_obligations`) keep working as-is.
- AgentTool-style sub-agents (search, code) — different idiom, untouched.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Door stays `lite`; confirm-floor delegates become stubs (no factory cost); one tool shrinks the request schema vs two. |
| 2 | EARNED TRUST | +1 | Confirmation friction becomes deterministic policy (floor in config), not a lite-model judgement call. |
| 3 | SKILLS, NOT FEATURES | +1 | Handoff behaviour is entirely a property of skill config (`delegation.allow` floors); no per-skill code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Routing to deeper models becomes reliable — the whole point of the door. |
| 5 | GRACEFUL DEGRADATION | +1 | Invalid targets become *unrepresentable* (enum-constrained tool schema) instead of a runtime error loop. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Deletes a custom tool in favour of the framework's native transfer mechanism + documented callback seam. |
| 7 | API FIRST | 0 | No HTTP surface change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | One delegation marker path (`mark_delegation` in one callback) instead of two. |
| 9 | SECURE BY CONSTRUCTION | +1 | Closed agent-name enum; floors enforced in code; the session↔skill gate keeps its exact purpose and now *holds* after a switch. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Frontend unchanged (completion path already shipped). |
| | **Net Score** | **+8** | Threshold: >= +4 ✓ |

**Conflict Justifications:** none (no -1 scores).

## Design

### Overview — one tool, one policy point, one completion path

```
                       the ONLY handoff verb the model knows
                                      │
                          transfer_to_agent(agent_name)     ← ADK-native, enum-constrained
                                      │
                     before_tool_callback (ONE policy point)
                        agent_name → (skill, DelegateRule)
                                      │
              ┌───────────────────────┴───────────────────────┐
        floor = auto                                  floor = confirm / cwf
              │                                                │
     return None → ADK native                    return elicitation envelope
     in-turn transfer proceeds                   (tool skipped; NO transfer)
     (unchanged UX: chip, chains,                8.1 primitive renders the
     maxDepth guard)                             card / form in chat
                                                               │
                                                    user clicks Proceed / Run
                                                               │
                                                 full switch (shipped, 8.2):
                                                 navigate to specialist on the
                                                 SAME thread + re-issue request
```

### D1. Single tool: ADK's native `transfer_to_agent`

ALL delegates — auto, confirm, and confirm_with_fields floors — are wired as
`sub_agents`, so ADK's own transfer machinery presents **one** tool. Verified
against the installed ADK source (design-doc-creator 5c, 2026-07-15):

- `TransferToAgentTool` is a `FunctionTool` subclass whose schema **enum-constrains
  `agent_name` to the actual sub-agent names** — the framework's own fix for
  "LLMs hallucinating invalid agent names" (`google/adk/tools/transfer_to_agent_tool.py`).
  This kills the entire alias-map bug class (`s_26124699_…` vs `26124699-…`)
  at the schema level: invalid targets are unrepresentable.
- The tool body's only effect is `tool_context.actions.transfer_to_agent =
  agent_name` — nothing else happens until the flow reads that action
  (`flows/llm_flows/base_llm_flow.py`).

`request_handoff` is **deleted**: its catalog string, alias map, and `level`
parameter go with it. `discover_jobs` keeps working — discovered jobs are wired
as confirm-floor stub sub_agents through the same
`accessible_delegate_rules` resolver (unchanged).

### D2. Readable agent names (friendly-names #9, structurally)

Sub-agent names switch from `_safe_agent_name(skill_id)` (`s_26124699_f558_…`)
to the sanitized **slug** (`one_obligation_analysis`), falling back to the
uuid form only on collision. The model reasons over names that mean something,
the enum keeps them closed, and the callback maps name → skill via a
factory-built dict — the model never sees or utters a raw id.

### D3. Stub sub_agents for confirm-floor delegates

Confirm/cwf-floor delegates must be *visible* to the transfer enum but must
never *run* in-turn. They are wired as stub `LlmAgent`s — name + description
only, no tools, no instruction body — so the door's factory cost stays flat
(the real agent is built on the specialist's page after the switch, exactly as
today). The policy callback guarantees a stub is never executed (interception
happens before the transfer action is set). Auto-floor delegates remain real
sub_agents (they genuinely run in-turn).

### D4. One policy point: `before_tool_callback`

Verified semantics (`flows/llm_flows/functions.py`): a callback returning a
truthy dict **skips the tool** and uses the dict as the function response; the
callback receives `(tool, args, tool_context)`. The interception:

```python
def make_handoff_policy_callback(delegate_map: dict[str, tuple[SkillConfig, DelegateRule]]):
    def handoff_policy(tool, args, tool_context) -> dict | None:
        if tool.name != "transfer_to_agent":
            return None                      # not ours
        target = delegate_map.get(str(args.get("agent_name") or ""))
        if target is None:
            return None                      # unknown → let ADK's own validation speak
        skill, rule = target
        if rule.floor == "auto":
            return None                      # native in-turn transfer proceeds
        # confirm / confirm_with_fields → short-circuit: NO transfer happens
        # (actions.transfer_to_agent never set). Return the 8.1 elicitation
        # envelope as the tool response; the render registry draws the card.
        envelope = build_handoff_envelope(skill, rule)   # fields from rule.fields
        get_current_tracker().mark_delegation(...)
        return envelope
    return handoff_policy
```

- **Floor = policy.** The AI no longer picks a `level`; the delegate's configured
  floor decides. (Today's evidence: a lite model does not make reliable level
  judgements — it couldn't even keep two schemas apart.)
- `confirm_with_fields` fields come from `rule.fields` (skill-config-authored,
  engine-validated at submit — consistent with the production-not-demo rule;
  *runtime* AI-authored forms remain the domain of tools like
  `map_ppa_obligations`, unchanged).
- The render registry (`a2ui_elicitation_render`) registers `transfer_to_agent`
  in its `tool_names` with the existing `is_elicitation` matcher (the registry
  keys on tool name — the documented recurring trap) and offload-exemption.
- The envelope keeps `action: confirm_delegation` — the shipped frontend
  interception (`A2UISurfaceMount` → skill-switch intent → navigate + stash +
  re-issue) is the completion path, unchanged.

### D5. The session index follows the conversation (fixes the 403)

Invariant today: `chat_sessions/{id}.skill_id` = the skill the session was
*created* on, forever. The switch breaks it. Fix: the index records the skill of
the **most recent chat turn**:

- `_ensure_session_index(thread_id, skill_id, …)` currently short-circuits when
  the row exists. Change: when the row exists and `idx.skill_id != skill_id`
  (an authenticated chat turn on the same thread from a different skill —
  exactly what the switch re-issue is), **update** `skill_id` and append to a
  `skillHistory` array (observability: the Activity tab and admin analytics can
  show the chain).
- The binding gate (`a2ui_surface_action_run_routes.py:371`) is **unchanged** —
  URL skill must equal the session's *current* skill. It now holds after a
  switch because the specialist's first turn (the re-issue) updates the index
  before any form can be submitted.
- Security: not a relaxation. A user can already run any accessible skill on a
  thread they own via the chat path; the gate's purpose — blocking forged
  cross-skill surface actions — is preserved verbatim.
- Sessions list: the session now surfaces under the specialist (where the user
  actually is). Accepted; arguably the correct behaviour.

### D6. Deletions (drift-bait removal)

| Deleted | Why |
|---|---|
| `make_request_handoff_tool` + catalog/listing/alias map + `_HANDOFF_LEVEL_ORDER` clamp | Replaced by D1–D4. |
| Backend `confirm_delegation` branch in `surface-action-run` (`_resolve_confirm_delegation_target`, target-swap for gates 4–8) | Dead since the frontend full switch; unused paths drift. `CONFIRM_DELEGATION_ACTION` survives only as the envelope action name the frontend intercepts. |
| `_NON_SURFACE_TRIGGER_ACTIONS` framing suppression in `a2ui_surface_context.py` | Only existed for the deleted backend branch. |
| `request_handoff` unit tests | Replaced by callback-policy tests (same scenarios, new seam). |

### D7. Small same-class fixes riding along

- **Replayed elicitation cards render frozen.** History-replayed
  `a2uiSurfaces` must always render as submitted records (the test transcript
  showed two live-looking "Proceed" cards under "Earlier in this conversation").
- **History attribution** (bubbles from before the switch are labelled with the
  *current* skill): follow-up if time allows; cosmetic, tracked not blocking.

### CLI Surface

No new commands. New script + make target (automation principle):
`scripts/handoff-e2e.sh [dev|test]` / `make handoff-e2e` — runs the three
acceptance flows below against a deployed env via real AG-UI streams (token via
the `aiplatform-cli` mint script).

## API Changes

None to the HTTP surface. One behavioural change: `chat_sessions/{id}.skill_id`
becomes "skill of the most recent turn" (with a `skillHistory` audit trail)
rather than "skill at creation".

## Migration

- No data migration: existing rows already read as "skill of the most recent
  turn" for never-switched sessions; `skillHistory` starts accruing on first
  switch.
- Deploy note: the chat backend is the frontend service's **sidecar** — backend
  changes reach chat only via the FRONTEND trigger (2026-07-15 deploy lesson).
- Rollback: revert the commit; no schema migration to roll back.

## Testing Strategy

### Unit (backend)
- Callback policy: auto floor → `None`; confirm floor → envelope dict, tool
  skipped, `mark_delegation` fired; unknown agent_name → `None` (ADK's own
  validation answers); cwf with `rule.fields` → form envelope; cwf without
  fields → degrades to confirm.
- Naming: slug-derived agent names, collision fallback, delegate_map closure.
- Session index: existing row + different skill on chat turn → `skill_id`
  updated + `skillHistory` appended; same skill → no write.
- Render registry: `transfer_to_agent` envelope renders placement:chat
  (the tool-names trap test, mirroring
  `test_request_handoff_output_renders_via_registry`).

### Acceptance (deployed env, real streams — non-negotiable)
Two design-level bugs shipped this week because component tests passed while the
user journey was broken. These three flows ARE the definition of done ("these
are our tests to make them all work" — user, 2026-07-15), scripted in
`scripts/handoff-e2e.sh` and run on dev AND test before this doc moves to
implemented/:

1. **AUTO** — stream `"extract the clauses of the Google LEAP ppa"` on the ONE
   Assistant. Assert: `AGENT_DELEGATION` custom event + specialist
   `TEXT_MESSAGE_CONTENT` in the SAME stream; no elicitation envelope; no
   malformed tool call.
2. **CONFIRM** — stream `"analyse the demo solar ppa and extract its
   obligations"`. Assert: exactly one `transfer_to_agent` call; intercepted (no
   transfer); `placement:"chat"` confirm envelope on the wire. Then complete the
   switch (navigate + re-issue on the same thread, as the frontend does) and
   assert the specialist streams 200 AND a subsequent `surface-action-run` POST
   on the specialist's page returns **200** (the Failure-2 regression).
3. **CONFIRM_WITH_FIELDS** — a job whose rule declares fields. Assert: form
   envelope carries exactly those fields; submitting values completes the run
   with the values readable in run state.

### Browser (render layer)
`aitana-frontend-verify` chrome-devtools pass: the confirm card renders compact,
Proceed shows the working state, the page switches, the specialist answers, the
form submits without a 403, replayed cards render frozen.

## Success Criteria

- [ ] `request_handoff` deleted; the door's only handoff verb is `transfer_to_agent`
- [ ] Agent names are slug-derived and enum-constrained
- [ ] Floor policy enforced in the callback (auto passes through; confirm/cwf intercepted)
- [ ] Session index follows the most recent turn's skill; post-switch form submit returns 200
- [ ] Backend confirm_delegation branch + framing suppression deleted
- [ ] Replayed elicitation cards render frozen
- [ ] `make handoff-e2e` passes on deployed dev and test
- [ ] Browser click-through of all three levels verified

## Implementation Plan

| Milestone | Scope | Est |
|---|---|---|
| M1 — Callback unification | Slug agent names + stub wiring + policy callback + render registration + delete `request_handoff` + session-index update + unit tests | 1.5d |
| M2 — Deletions + freeze | Backend confirm branch + framing suppression removal; frozen replayed cards | 0.5d |
| M3 — Acceptance harness | `scripts/handoff-e2e.sh` + `make handoff-e2e`; run on dev; promote; run on test; browser pass | 0.5d |

**Model Assignment:** M1 on the most capable available model (callback/transfer
semantics are subtle; first-shot correctness matters); M2–M3 any tier.

## Related Documents

- [8.1 elicitation-in-chat-primitive](../v6.8.0/elicitation-in-chat-primitive.md) — unchanged consumer contract
- [8.2 first-impression-elicited-handoff](../v6.8.0/first-impression-elicited-handoff.md) — superseded in its `request_handoff` mechanism; UX levels preserved
- [8.3 jobs-and-subagents](../v6.8.0/jobs-and-subagents.md) — discovery unchanged; jobs become confirm-floor stubs
- [local-dev-cli](../v6.1.0/local-dev-cli.md) — token mint used by the e2e script
