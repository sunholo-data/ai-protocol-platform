# Skill Delegation — configurable, access-aware handoff to specialist skills

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: 4–5 days
**Scope**: Fullstack
**Dependencies**: `agent-factory` (v6.0.0, implemented), `resource-access-control` (v6.0.0, implemented), `fork-convergence` model tiers (v6.6.0, implemented)
**Created**: 2026-07-08
**Last Updated**: 2026-07-09

## Problem Statement

A skill answers every turn itself, even when a *different* skill is better suited.
The `general-assistant` (day-to-day `lite` Gemini) has no way to hand a
PPA-contract question to `one-ppa-expert`, or a coding question to
`code-assistant`, even though those specialists exist, have their own tools,
instructions, and `smart`-tier reasoning. The user gets a generalist answer
where a specialist answer was available.

**Current State:**

- The agent factory *already* wires a skill's `subSkills` into ADK `sub_agents`
  with `description`-driven `transfer_to_agent` ([backend/adk/agent.py:381-391](../../../backend/adk/agent.py#L381-L391),
  [:512](../../../backend/adk/agent.py#L512)). The mechanism for LLM-judged
  handoff exists but is **unused** — every seeded skill ships `subSkills=[]`.
- There is **no per-skill control** over delegation: it's all-or-nothing on a raw
  ID list, with no enable flag, no "ask before handing off" mode, and no way to
  surface it in Skill Studio.
- **Security gap (must-fix):** sub-skill resolution calls
  [`get_skill(sub_id)`](../../../backend/skills/skill_config.py#L97) which does
  **zero access checks**, and the `access_context` threaded into `create_agent`
  is **inert** — never evaluated against sub-skills, and not even passed from the
  skill processor into `create_agent_with_thinking`. A parent skill listing a
  restricted skill would hand that specialist to *any* user who can reach the
  parent, bypassing the same 5-type access policy the direct skill route enforces.
- The handoff is **invisible** to the user: ADK's `author`/`transfer_to_agent`
  is never surfaced to the AG-UI wire, so a user would see the answer silently
  change character with no indication a specialist produced it.

**Impact:**

- **Users** get generalist answers where specialists exist; and if delegation is
  naively enabled, could receive answers from skills they aren't authorized for.
- **Skill authors** can't compose skills without editing raw config or code.
- **Trust**: an answer that silently switches specialist has no provenance.

## Goals

**Primary Goal:** Let any skill be configured to delegate a turn to an
allow-listed specialist skill when the parent LLM judges it would do better —
enforced against the requesting user's access level by construction, and signaled
live in the UI.

**Success Metrics:**

- A skill with `delegation.enabled=true` and an `allow` list transfers matching
  turns to the correct specialist (verified in eval trajectory tests).
- **Zero** delegation to a skill the requesting user cannot access (unit +
  integration test asserts a denied target is dropped from the agent's
  `sub_agents`, never invocable).
- 100% of delegations emit both a transient status label (during) and a
  persistent transcript marker (after) over AG-UI.
- No regression to first-token latency for non-delegating skills (default off).

**Non-Goals:**

- Cross-*platform* delegation via A2A to external agents (this doc is
  intra-platform, in-process ADK `sub_agents` only; A2A task-handling is a
  separate follow-up).
- Arbitrary-depth delegation chains. We cap depth (see Design) and rely on the
  existing `_seen` cycle guard.
- Replacing the intra-skill `_HeuristicRouter` lite→smart thinking tier — that
  stays; delegation composes *on top* of it (a delegated specialist runs its own
  thinking tier).

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Reuses the `STAGE_PROGRESS` pipeline to show "Handing off to X…" the moment a transfer is decided — turns a silent pause into visible progress. `suggest` mode adds a confirm step but is opt-in. |
| 2 | EARNED TRUST | +1 | The persistent transcript marker ("→ Delegated to PPA specialist") is provenance for *who produced the answer* — a specialist attribution, calibrating user trust in the response. |
| 3 | SKILLS, NOT FEATURES | +1 | Delegation is expressed purely as skill config (a `delegation` block), authored in Skill Studio; no user needs to understand sub-agents. It makes skills *composable* without code. Orchestration internals stay hidden. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | This is the axiom made literal: route the turn to the best-suited specialist (and its `smart` tier) instead of answering everything on generalist `lite`. |
| 5 | GRACEFUL DEGRADATION | +1 | Explicit fallbacks: access-denied target → silently dropped, parent answers; delegate errors → parent handles the turn; unknown ID → skip + log. Never a broken state. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Uses ADK-native `transfer_to_agent` and the AG-UI `CUSTOM` event type (the protocol's sanctioned extension point) — no new wire format. |
| 7 | API FIRST | +1 | Delegation is backend routing; the signal is emitted as AG-UI events so every channel (Telegram/CLI) can render it. Persistent marker is a rendering choice over the same event. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Every delegation decision emits a span + structured event (parent, target, mode, allowed?, chosen?) into the existing trace/timing pipeline. |
| 9 | SECURE BY CONSTRUCTION | +1 | **Closes an existing escalation gap.** Delegate targets are access-filtered at agent-build time via the same `AccessContext.can_access_skill` evaluator the API route uses — deny-by-default, enforced by architecture, not author discipline. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend only renders two new event payloads into existing components; all routing/decision logic stays server-side. |
| | **Net Score** | **+10** | Threshold: >= +4 |

**Conflict Justifications:**

- None (no axiom scored -1).

**Note on Axiom 9 residual risk (not a -1):** a delegated `smart`-tier specialist
runs on the Anthropic **direct API (US egress)** per the model registry. This is
the *existing* privacy-boundary property of the `smart` tier, not introduced by
this feature. The design does not change what content the smart tier sees; it
only changes *when* a specialist is invoked. The Security section restates the
[CLAUDE.md](../../../CLAUDE.md) rule: skills that handle restricted customer
content must not delegate to a US-egress specialist unless the customer DPA
covers it. Enforcement hook: the `delegation.allow` list is authored per skill,
so a confidential skill simply doesn't list US-egress targets.

## Design

### Overview

Introduce a typed `delegation` block on `SkillMetadata`. At agent-build time,
resolve `delegation.allow` into ADK `sub_agents` **filtered by the requesting
user's `AccessContext`**, gated by `delegation.enabled` and bounded by a depth
cap. `mode: auto` uses ADK's native `transfer_to_agent`; `mode: suggest` swaps
the transfer tool for a propose-and-confirm tool. Emit two AG-UI `CUSTOM` events
on handoff — a transient `STAGE_PROGRESS` label during, and a persistent
`AGENT_DELEGATION` marker after — rendered by the existing `TypingIndicator` and
a new inline transcript chip.

### Data Model Changes

New nested model on `SkillMetadata` ([backend/db/models/__init__.py:35](../../../backend/db/models/__init__.py#L35)).
Replaces the flat `sub_skills` list as the delegation surface (see Migration —
`sub_skills` is kept as a read-compat alias that maps to `delegation.allow`).

```python
class DelegationMode(str, Enum):
    AUTO = "auto"        # LLM transfers autonomously via transfer_to_agent
    SUGGEST = "suggest"  # LLM proposes; user confirms before handoff

class DelegationConfig(BaseModel):
    enabled: bool = False
    mode: DelegationMode = DelegationMode.AUTO
    allow: list[str] = Field(default_factory=list)  # skill IDs or slugs
    max_depth: int = 1                               # hops from the root skill

class SkillMetadata(BaseModel):
    ...
    delegation: DelegationConfig = Field(default_factory=DelegationConfig)
```

SKILL.md frontmatter (authoring surface):

```yaml
metadata:
  model: lite
  delegation:
    enabled: true
    mode: auto
    allow:
      - one-ppa-expert
      - code-assistant
```

### Backend Changes

**Modified — `backend/adk/agent.py` (the core):**

1. **Thread access through the thinking factory.** `create_agent_with_thinking`
   currently doesn't receive `access_context`; add it and pass through to
   `create_agent` (fixes the inert-context bug at the source in
   `skill_processor`).
2. **Access-filtered delegate resolution.** Replace the `md.sub_skills` loop with
   a `delegation`-driven one that (a) short-circuits when
   `not md.delegation.enabled`, (b) enforces `max_depth`, (c) drops any target
   failing `access_context.can_access_skill(sub)`:

   ```python
   sub_agents: list[LlmAgent] = []
   deleg = md.delegation
   if deleg.enabled and _depth(_seen) < deleg.max_depth:
       for sub_id in deleg.allow:
           sub = get_skill(sub_id)
           if sub is None:
               logger.warning("delegate %r of %r not found; skipping", sub_id, skill_config.skill_id)
               continue
           if access_context is not None and not access_context.can_access_skill(sub):
               logger.info("delegate %r not accessible to user; dropping", sub_id)  # deny-by-default
               continue
           sub_agents.append(create_agent(sub, user, access_context=access_context, _seen=seen))
   ```

3. **`suggest` mode.** For `mode=suggest`, do not attach `sub_agents` (which
   auto-enables `transfer_to_agent`). Instead register a `propose_delegation`
   FunctionTool that emits the suggestion event and returns control to the user;
   confirmation re-issues the turn against the chosen specialist. (Auto mode uses
   ADK's built-in transfer unchanged.)

4. **Handoff signaling.** In the delegate sub-agent's `before_agent` callback
   (built by `make_before_agent`), detect "I am a delegated sub-agent, now
   active" and emit the transient + persistent events (see AG-UI Changes). The
   parent/target names come from the skill configs, not ADK internals.

**Modified — `backend/skills/skill_processor.py`:** pass `access` (the
`request.state.access` `AccessContext`) into `create_agent_with_thinking`.

**Modified — `backend/adk/agui.py` / `backend/observability/timing.py`:** add an
`AGENT_DELEGATION` custom-event constant + a `mark_delegation(parent, target,
mode)` helper alongside the existing `STAGE_PROGRESS` drain path
([timing.py:142-191](../../../backend/observability/timing.py#L142-L191)). No new
transport — reuses `CustomEvent(type=EventType.CUSTOM, name=..., value=...)` and
the existing `drain_stage_events()` interleave.

### AG-UI Changes (protocol events)

Two `CUSTOM` events (protocol-compliant; no new event type):

| Event `name` | When | `value` | Render |
|---|---|---|---|
| `STAGE_PROGRESS` (existing) | transfer decided, before specialist runs | `{stage, label: "Handing off to PPA specialist…", elapsed_ms}` | transient label in `TypingIndicator` |
| `AGENT_DELEGATION` (new) | specialist becomes active | `{parent_skill, target_skill, target_display, mode}` | persistent inline chip in transcript |

### Frontend Changes

**Modified — [`src/hooks/useSkillAgent.ts`](../../../frontend/src/hooks/useSkillAgent.ts#L264):**
extend `onCustomEvent` with an `AGENT_DELEGATION` branch that appends a
delegation marker to the message list (persistent) — mirroring the existing
`STAGE_PROGRESS`→`setStageLabel` handling (transient).

**Modified — [`src/components/chat/TypingIndicator.tsx`](../../../frontend/src/components/chat/TypingIndicator.tsx):**
already renders `stageLabel`; no change needed for the transient label (it flows
through the existing prop). Confirm the "Handing off…" label reads well in the
indicator's priority order.

**New — `src/components/chat/DelegationMarker.tsx`:** a small inline chip ("→
Delegated to PPA specialist") rendered in the message thread where the
`AGENT_DELEGATION` marker sits. <2KB, presentational only.

### Activity Transparency Surface (expanded M3 — general, not delegation-only)

Delegation is the first consumer of a broader principle: **surface all agent
activity so a turn never looks stuck — a minimal ambient indicator that lights
up then fades, with deeper detail on demand in the workbench.** Most of this
already exists ([ThinkingPanel](../../../frontend/src/components/chat/ThinkingPanel.tsx)
auto-collapsing reasoning, [TypingIndicator](../../../frontend/src/components/chat/TypingIndicator.tsx)
3-tier line, [ToolCallChip](../../../frontend/src/components/chat/ToolCallChip.tsx));
delegation plugs in rather than adding a one-off.

New in this milestone: an **Activity panel** as a `Workbench` tab that aggregates
the activity events the frontend already receives (delegation handoffs, tool
calls, thinking summaries, stage progress) into a recede-able timeline, using the
workbench's existing **tab badging** (pulses on new activity, fades when idle).
It is frontend-native (thin client: it renders events it already has — no new
hot-path backend emission), and it also mounts an A2UI surface (`surfaceId:
"activity"`) so a skill/tool can push **structured** rich detail (a table, a
reasoning breakdown) into the same tab via the existing A2UI mechanism when the
data is deeper than a timeline line. Split for delivery: **M3a** = delegation
transient label + persistent chip + backend `mark_delegation` wiring; **M3b** =
the Activity workbench panel. See [[activity-transparency]] principle.

### CLI Surface

Extend the existing `aiplatform` CLI (per [local-dev-cli.md](../../../docs/design/v6.1.0/local-dev-cli.md)):

- `aiplatform skill probe <id>` already exists — extend its `LATENCY_REPORT`/
  event capture to print any `AGENT_DELEGATION` events so a developer can confirm
  a delegation fired without a browser (0.1 day).
- `aiplatform skill show <id>` (if/when it lands) surfaces the `delegation` block.

### Architecture Diagram

```
[User turn] → /api/skill/{parent}/stream
   → create_agent_with_thinking(parent, user, access_context)   # access now threaded
        → create_agent(parent)
             delegation.enabled? depth<max? ──no─→ single agent (today's behaviour)
                       │ yes
                       ▼
             for target in delegation.allow:
                 access_context.can_access_skill(target)? ──no─→ drop (deny-by-default)
                       │ yes
                       ▼
                 sub_agents += create_agent(target, user, access_context)  # recurse, _seen guard
   → Runner streams AG-UI events
        transfer_to_agent(target)  ──→  STAGE_PROGRESS "Handing off…"  (transient)
        specialist before_agent    ──→  AGENT_DELEGATION marker        (persistent)
   → Frontend: TypingIndicator (label) + DelegationMarker (chip)
```

## Implementation Plan

### Phase 1: Access-gap fix + config model (~1.5 days)
- [ ] Add `DelegationConfig`/`DelegationMode` to `SkillMetadata`; `sub_skills` read-compat alias (~60 LOC)
- [ ] Thread `access_context` from `skill_processor` → `create_agent_with_thinking` → `create_agent` (~20 LOC)
- [ ] Access-filter delegate resolution in `create_agent` (deny-by-default) + depth cap (~40 LOC)
- [ ] Unit tests: denied target dropped; unknown skipped; depth cap; disabled = no sub_agents (~120 LOC) **[security-critical]**

### Phase 2: Auto + suggest modes (~1.5 days)
- [ ] `mode=auto` end-to-end via native `transfer_to_agent` (config only) + eval trajectory test
- [ ] `mode=suggest` `propose_delegation` tool + confirm re-issue path (~120 LOC)
- [ ] Observability: `mark_delegation` helper + span attributes (~40 LOC)

### Phase 3: AG-UI signaling + frontend (~1.5 days)
- [ ] Emit `STAGE_PROGRESS` "Handing off…" on transfer + `AGENT_DELEGATION` on specialist activation (~50 LOC)
- [ ] `useSkillAgent` `AGENT_DELEGATION` branch → persistent marker (~40 LOC)
- [ ] `DelegationMarker.tsx` + wire into message thread (~60 LOC)
- [ ] `aiplatform skill probe` prints delegation events (~20 LOC)

### Phase 4: Studio + polish (~0.5 day)
- [ ] Skill Studio: expose `delegation` block (enable, mode, allow-picker) — allow-picker lists only skills the author can access
- [ ] Docs + one seeded example (`general-assistant` gains an opt-in `delegation.allow`)

## Migration & Rollout

**Data Model Migration:**
- `delegation` defaults to `enabled=false` → **every existing skill is unchanged**
  (no delegation until explicitly configured). No backfill required.
- `sub_skills` (currently `[]` everywhere) is read as a compat alias into
  `delegation.allow` with `enabled=false` unless a `delegation` block is present.
  Seed templates migrate `sub_skills` → `delegation` in the same PR.

**Feature Flags:**
- Per-skill `delegation.enabled` *is* the flag (deny-by-default). No global flag
  needed; roll out by enabling on one skill at a time.

**Rollback Plan:**
- Set `delegation.enabled=false` on any skill (or revert the seed) → instant
  return to single-agent behaviour. The access-filter + threading fix is safe to
  keep (pure hardening).

**Environment Variables:** none.

## Testing Strategy

### Backend Tests (pytest)
- [ ] **[security]** user without access to target → target absent from `sub_agents` (unit, on `create_agent`)
- [ ] **[security]** transitive: parent A accessible, delegate B not → B never built even via A
- [ ] `enabled=false` → no `sub_agents`; `max_depth` respected; cycle via `_seen` terminates
- [ ] `mode=auto` trajectory: PPA question routes to `one-ppa-expert` (ADK eval)
- [ ] `mode=suggest`: proposes, does not transfer until confirm
- [ ] delegate error → parent completes the turn (graceful degradation)
- [ ] `access_context` now non-None through `create_agent_with_thinking` (regression on the inert-context bug)

### Frontend Tests (Vitest + RTL)
- [ ] `onCustomEvent` appends a persistent marker on `AGENT_DELEGATION`
- [ ] `DelegationMarker` renders target display name
- [ ] transient "Handing off…" label shows via `TypingIndicator` then clears

### Manual Testing
- [ ] Enable delegation on `general-assistant` (allow `one-ppa-expert`); ask a PPA question; watch label → chip; verify specialist answered
- [ ] As a user without ONE access, same question → no delegation, generalist answers, no marker
- [ ] `suggest` mode → confirm prompt appears before handoff

## Security Considerations

- **Deny-by-default delegate access:** targets filtered by
  `AccessContext.can_access_skill` — the *same* evaluator as the direct skill
  route ([skills/routes.py:226](../../../backend/skills/routes.py#L226)), so
  delegation cannot become a privilege-escalation side channel. This closes a
  live gap where `get_skill` performed no checks.
- **Existence non-leak:** a denied target is silently dropped (logged
  server-side at `info`), never surfaced to the user — consistent with the
  route's 404-not-403 policy.
- **Egress boundary:** delegating to a `smart`-tier specialist invokes the
  Anthropic direct API (US egress). Per [CLAUDE.md](../../../CLAUDE.md), skills
  handling restricted customer content must not list US-egress specialists in
  `delegation.allow` absent DPA coverage. Enforced by author-controlled allow
  lists; flagged in Studio when a confidential skill adds a US-egress target.
  **Accepted interim decision (2026-07-08):** US-egress Anthropic is permitted
  for the **dev/demo environment only** until Anthropic-on-Vertex is enabled in
  an EU region (europe-west*) — currently 404 in all Vertex regions for this
  project. Target end-state: `smart` tier routes to EU Vertex Anthropic, keeping
  the `smart` path inside the EU edge. Until then, do not enable delegation to a
  `smart` specialist on any skill that touches restricted customer content in
  test/prod.
- **Prompt-injection:** a delegated specialist runs its own hardened
  instructions; the transfer decision is the parent LLM's, over trusted skill
  `description`s (not user-controlled free text).

## Performance Considerations

- **Default off** → zero latency impact on non-delegating skills.
- Enabling delegation builds N extra `LlmAgent`s at agent-construction time
  (cheap, in-process; the recursion already exists for `sub_skills`). Access
  checks are pure functions (no I/O) per
  [access_context.py](../../../backend/auth/access_context.py).
- A handoff adds one extra model round-trip (the transfer decision) — but routes
  to a better answer; mitigated perceptually by the transient label (INSTANT
  FEEL). Depth cap (default 1) bounds worst case.
- Frontend: `DelegationMarker` <2KB, no bundle-budget concern.

## Success Criteria

- [ ] All backend tests passing (`cd backend && make test-fast`)
- [ ] All frontend tests passing (`cd frontend && npm run test:run`)
- [ ] Lint/typecheck clean (`make lint`; `npm run quality:check:fast`)
- [ ] A user cannot trigger delegation to a skill they cannot access (asserted in tests)
- [ ] Both AG-UI signals (transient + persistent) emitted and rendered for every delegation
- [ ] `general-assistant` seeded example delegates a PPA question to `one-ppa-expert` in dev
- [ ] `delegation` block authorable in Skill Studio with an access-scoped allow-picker

## Open Questions

- **`suggest` UX**: is the confirm an inline button in the transcript, or a
  system message the user replies to? (Leaning inline button; needs Studio/UX
  pass.)
- **Allow by slug vs ID**: `allow` accepts skill IDs today; should authoring use
  human-readable slugs resolved at seed/save time? (Leaning slug-in-authoring,
  ID-at-rest.)
- **Depth > 1**: any real use case for multi-hop delegation, or keep `max_depth`
  hard-capped at 1 for v1? (Leaning cap at 1; revisit on demand.)

## Related Documents

- [agent-factory.md](../v6.0.0/implemented/agent-factory.md) — `_HeuristicRouter`, `sub_agents`, `resolve_model`
- [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md) — the 5-type `AccessContext` evaluator reused here
- [fork-convergence.md](../../v6.6.0/fork-convergence.md) — model tiers (`lite`/`smart`) that delegation routes between
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — `aiplatform` CLI surface
- [CLAUDE.md](../../../CLAUDE.md) — privacy boundary / US-egress rule for `smart`-tier delegation

---

## Implementation Report

**Completed**: 2026-07-09
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
