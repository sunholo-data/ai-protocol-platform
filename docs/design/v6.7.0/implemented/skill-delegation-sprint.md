# Sprint Plan: SKILL-DELEGATION - Configurable, access-aware skill delegation

## Summary
Ship configurable skill→skill delegation: a skill can hand a turn to an allow-listed specialist when the parent LLM judges it would do better — access-filtered by construction (closing a live escalation gap) and signaled live over AG-UI (transient label + persistent transcript marker).

**Duration:** 5 days
**Scope:** Fullstack
**Dependencies:** agent-factory (v6.0.0 ✅), resource-access-control (v6.0.0 ✅), model tiers (v6.6.0 ✅). Accepted: Anthropic US-egress OK for dev/demo.
**Risk Level:** Medium (M1 is security-critical; ADK `transfer_to_agent` behaviour + `suggest`-mode re-issue path carry the unknowns)
**Design Doc:** [skill-delegation.md](skill-delegation.md)

## Current Status Analysis

### Recent Velocity
- Last 14 days: 18 commits, 107 files, ~10,052 insertions — v6.6.0 shipped in 4 milestones (M1–M4) plus Studio/voice.
- Cadence: comfortably ~1 milestone/day on fullstack work with tests + lint per milestone.
- Estimated capacity for this sprint: ~1,000–1,200 LOC impl + ~500 LOC tests over 5 days — within range.

### Existing Implementation (what we build on)
- `create_agent` already recurses `sub_skills` → ADK `sub_agents` with `description`-driven `transfer_to_agent` ([agent.py:381-391](../../../backend/adk/agent.py#L381-L391), [:512](../../../backend/adk/agent.py#L512)).
- `_HeuristicRouter` lite→smart thinking tier ([agent.py:183](../../../backend/adk/agent.py#L183)) — delegation composes on top, unchanged.
- `AccessContext.can_access_skill` 5-type evaluator ([auth/access_context.py](../../../backend/auth/access_context.py)) — the exact gate to reuse; already used by [skills/routes.py:226](../../../backend/skills/routes.py#L226).
- `STAGE_PROGRESS` CustomEvent pipeline ([timing.py:142-191](../../../backend/observability/timing.py#L142-L191)) → [`useSkillAgent.onCustomEvent`](../../../frontend/src/hooks/useSkillAgent.ts#L264) → [`TypingIndicator`](../../../frontend/src/components/chat/TypingIndicator.tsx) — the "Reading N documents…" pattern to reuse for the transient label.
- **Gap to close:** `get_skill(sub_id)` does no access check; `access_context` is inert and not passed from `skill_processor` into `create_agent_with_thinking`.

## Proposed Milestones

### Milestone 1: Access-gap fix + config model (security-first)
**Scope:** backend
**Goal:** `delegation` config exists on `SkillMetadata`; `access_context` is threaded end-to-end; delegate targets are access-filtered deny-by-default. **No delegation behaviour yet** — this is the safe foundation, and it stands alone as a security hardening even if nothing else lands.
**Estimated:** ~120 impl + ~140 tests = ~260 LOC
**Duration:** 1.5 days

**Tasks:**
- [ ] Add `DelegationMode` enum + `DelegationConfig` model; add `delegation` field to `SkillMetadata`; `sub_skills` read-compat alias → `delegation.allow` (~70)
- [ ] Thread `access_context` from `skill_processor` → `create_agent_with_thinking` → `create_agent` (~20)
- [ ] Replace `md.sub_skills` loop with delegation-driven, access-filtered resolution: short-circuit on `not enabled`, enforce `max_depth`, drop targets failing `can_access_skill` (deny-by-default, log at info) (~40)
- [ ] Tests: denied target dropped from `sub_agents`; transitive denial (A ok, B denied → B never built); unknown ID skipped; `enabled=false` → no sub_agents; `max_depth`/`_seen` termination; `access_context` non-None regression (~140)

**Files to Create/Modify:**
- `backend/db/models/__init__.py` (modify, ~70) — `DelegationConfig`, `DelegationMode`, `SkillMetadata.delegation`
- `backend/adk/agent.py` (modify, ~50) — thread access, filtered resolution
- `backend/skills/skill_processor.py` (modify, ~10) — pass `access`
- `backend/tests/unit/test_skill_delegation_access.py` (new, ~140)

**Acceptance Criteria:**
- [ ] A user without access to a target skill never gets it in `sub_agents` (asserted)
- [ ] `enabled=false` (default) reproduces today's exact behaviour — zero delegation
- [ ] `access_context` is non-None through the thinking factory (regression on the inert-context bug)
- [ ] `make lint` + `make test-fast` clean

**Risks:**
- Back-compat of `sub_skills` alias — Mitigation: alias maps to `delegation.allow` with `enabled=false`; seed templates migrate in this PR; unit test both shapes.

### Milestone 2: Auto + suggest modes + observability
**Scope:** backend
**Goal:** `mode=auto` delegates via native `transfer_to_agent`; `mode=suggest` proposes-then-confirms; every decision emits a span.
**Estimated:** ~180 impl + ~140 tests = ~320 LOC
**Duration:** 1.5 days

**Tasks:**
- [ ] `mode=auto` end-to-end (config-only wiring via existing `sub_agents`) + ADK eval trajectory test: PPA question → `one-ppa-expert` (~40)
- [ ] `mode=suggest`: `propose_delegation` FunctionTool (no `sub_agents` attached so transfer stays off); confirm re-issues the turn against the chosen specialist (~120)
- [ ] `mark_delegation(parent, target, mode)` helper + span attributes (allowed?, chosen?) (~40)
- [ ] Tests: auto trajectory; suggest proposes-not-transfers-until-confirm; delegate error → parent completes turn (graceful degradation) (~140)

**Files to Create/Modify:**
- `backend/adk/agent.py` (modify, ~120) — suggest tool, mode branching
- `backend/observability/timing.py` (modify, ~40) — `mark_delegation`
- `backend/tests/eval/` (new evalset case, ~40) + `backend/tests/unit/test_skill_delegation_modes.py` (new, ~120)

**Acceptance Criteria:**
- [ ] `auto`: matching turn transfers to the correct specialist (eval trajectory passes)
- [ ] `suggest`: no transfer occurs until user confirms
- [ ] Delegate failure falls back to the parent (no user-facing 500)
- [ ] `make lint` + `make test-fast` clean

**Risks:**
- `suggest` re-issue path is the biggest unknown (how the confirmed turn re-enters the runner). Mitigation: spike it Day 2 morning; if the re-issue is heavier than budget, ship `auto` first and land `suggest` behind its mode value (already gated).

### Milestone 3: AG-UI signaling + frontend render
**Scope:** fullstack
**Goal:** Both signals live — transient "Handing off to X…" during, persistent "→ Delegated to X" chip after.
**Estimated:** ~150 impl + ~90 tests = ~240 LOC
**Duration:** 1.5 days

**Tasks:**
- [ ] Emit `STAGE_PROGRESS` "Handing off…" on transfer decision + new `AGENT_DELEGATION` CustomEvent on specialist activation (in delegate `before_agent`) (~50)
- [ ] `useSkillAgent.onCustomEvent`: `AGENT_DELEGATION` branch → append persistent marker to message list (~40)
- [ ] `DelegationMarker.tsx` inline chip + wire into message thread (~60)
- [ ] `aiplatform skill probe` prints delegation events (headless verify) (~20)
- [ ] Tests: onCustomEvent appends marker; DelegationMarker renders target display; transient label shows then clears (Vitest) (~90)

**Files to Create/Modify:**
- `backend/adk/agent.py` / `backend/adk/agui.py` (modify, ~50)
- `frontend/src/hooks/useSkillAgent.ts` (modify, ~40)
- `frontend/src/components/chat/DelegationMarker.tsx` (new, ~60)
- `cli/aiplatform/...` (modify, ~20)
- `frontend/src/**/__tests__/DelegationMarker.test.tsx` + hook test (new, ~90)

**Acceptance Criteria:**
- [ ] Every delegation emits transient + persistent events, both rendered
- [ ] Persistent marker survives in transcript history
- [ ] `npm run quality:check:fast` + `make lint`/`make test-fast` clean

**Risks:**
- Detecting "specialist just became active" in `before_agent` without ADK internals. Mitigation: the sub-agent's own callback already fires on activation; carry parent/target via skill config, not ADK `author`.

### Milestone 4: Skill Studio + seeded example + polish
**Scope:** fullstack
**Goal:** `delegation` block authorable in Studio (access-scoped allow-picker); one dev example wired; docs updated.
**Estimated:** ~120 impl + ~40 tests = ~160 LOC
**Duration:** 0.5 day

**Tasks:**
- [ ] Skill Studio `delegation` editor (enable, mode, allow-picker listing only author-accessible skills) (~90)
- [ ] Seed `general-assistant` with opt-in `delegation.allow: [one-ppa-expert]` in dev fixture (~15)
- [ ] Studio test: allow-picker excludes inaccessible skills (~40); update design doc → implemented
- [ ] Manual verify in browser (aitana-frontend-verify): label → chip → specialist answered

**Files to Create/Modify:**
- `frontend/src/app/skills/studio/...` (modify, ~90)
- `backend/db/local_fixture.py` + seed templates (modify, ~15)
- Studio test (new, ~40)

**Acceptance Criteria:**
- [ ] Author can enable delegation + pick allowed skills in Studio; picker is access-scoped
- [ ] `general-assistant` delegates a PPA question to `one-ppa-expert` in dev (manual verify)
- [ ] All gates green; design doc moved to `implemented/`

**Risks:**
- Studio surface may need more than 0.5d if the allow-picker needs a new skills-list endpoint. Mitigation: reuse existing `GET /api/skills?ownerId=` list already used by Studio; filter client-side by access flag from the response.

## Day-by-Day Breakdown

### Day 1
- **Focus:** M1 — config model + access threading
- **Tasks:** `DelegationConfig`/`SkillMetadata.delegation` + alias; thread `access_context`; filtered resolution
- **Checkpoint:** `enabled=false` reproduces current behaviour; access-threading compiles; first access tests green

### Day 2
- **Focus:** Finish M1, start M2
- **Tasks:** Complete M1 security tests (deny/transitive/depth) + `make test-fast`; spike `suggest` re-issue; wire `mode=auto`
- **Checkpoint:** M1 done & lint/test clean (security foundation landed); `auto` transfers in a manual probe

### Day 3
- **Focus:** M2 — suggest mode + observability
- **Tasks:** `propose_delegation` tool + confirm path; `mark_delegation` span; mode + degradation tests; eval trajectory
- **Checkpoint:** auto + suggest both tested; delegate-error falls back to parent

### Day 4
- **Focus:** M3 — AG-UI signaling + frontend
- **Tasks:** emit both events; `useSkillAgent` marker branch; `DelegationMarker.tsx`; CLI probe; Vitest
- **Checkpoint:** end-to-end in browser — label during, chip after; frontend + backend gates clean

### Day 5
- **Focus:** M4 — Studio + example + polish, full CI parity
- **Tasks:** Studio delegation editor (access-scoped picker); seed `general-assistant` example; manual verify; move doc to implemented
- **Checkpoint:** all success metrics green; PR-ready

## Quality Gates

After each milestone:
```bash
cd backend && make lint && make test-fast
cd frontend && npm run quality:check:fast   # M3/M4 only
```

Before PR (CI parity):
```bash
cd backend && make lint && make test-fast
cd frontend && npm run quality:check        # includes tests + build
```

## Success Metrics
- [ ] Backend tests passing (`cd backend && make test-fast`)
- [ ] Frontend tests passing (`cd frontend && npm run test:run`)
- [ ] Lint/typecheck clean (both stacks)
- [ ] A user cannot trigger delegation to a skill they cannot access (asserted in tests)
- [ ] Both AG-UI signals emitted + rendered for every delegation
- [ ] `general-assistant` delegates a PPA question to `one-ppa-expert` in dev
- [ ] Design doc moved to `implemented/`

## Dependencies
- ADK `transfer_to_agent` native behaviour (present, used by existing sub_agents path)
- `ANTHROPIC_API_KEY` for `smart`-tier specialists (already mounted on backend; US-egress accepted for dev/demo)

## Open Questions (carried from design doc — none block M1/M2)
- `suggest`-mode confirm UX: inline button vs system message (leaning inline button) — settles in M3/M4
- `allow` by slug-in-authoring vs ID-at-rest (leaning slug→ID) — settles in M1
- `max_depth` hard-cap at 1 for v1 (leaning yes) — default in M1

## Notes
- **Security-first ordering is deliberate:** M1 lands the access-filter + threading fix *before* any delegation is invocable, so the escalation gap is closed the moment delegation exists. Even if the sprint stops after M1, the codebase is strictly safer.
- Delegation is default-off per skill → rollout is per-skill `enabled=true`, no global flag.
- `_HeuristicRouter` thinking tier is untouched; a delegated specialist runs its own tier.
