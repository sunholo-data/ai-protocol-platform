# Sprint Plan: FIRST-IMPRESSION — Front Door + Elicited Handoff (v6.8.0)

## Summary

Turn the shipped-but-dormant primitives (7.1 delegation, 7.8 elicitation) into the
product's intended shape: a fast "ONE Assistant" front door that hands down to deep
specialists on demand, with a runtime AI judgement over three confirmation levels
(auto / confirm / confirm+info) rendered as A2UI in the chat area. Spans docs 8.1 → 8.2 → 8.3.

**Duration:** ~4 milestones (~9–10 estimated engineering-days; compressed given current autonomous velocity)
**Scope:** Backend-heavy fullstack (+ config/tenant)
**Dependencies:** 7.1 skill-delegation ✅, 7.8 obligation-elicitation ✅, 7.3 result→A2UI ✅, 6.5.0 landing ✅, 6.6.0 tiers ✅
**Risk Level:** Medium-High (refactors a shipped flow; touches the recurring A2UI-workspace-render + LatencyTracker-binding traps; live dev-tenant cutover)
**Design Docs:** [8.1](elicitation-in-chat-primitive.md) · [8.2](first-impression-elicited-handoff.md) · [8.3](jobs-and-subagents.md) · [SEQUENCE](SEQUENCE.md)

## Current Status Analysis

### Recent Velocity
- Last 14 days: 194 commits, 353 files, +44.6K/−2.4K (includes docs + vendored specs; obligation/elicitation/mcp-app work dominated).
- Cadence supports multi-milestone sprints; estimates below are conservative engineering-days, not wall-clock.

### Existing Implementation (build-on, do not reinvent)
- **Delegation engine** — `backend/adk/agent.py:681-724` (`transfer_to_agent`, access filter, cycle/depth guards, `AGENT_DELEGATION`).
- **Elicitation M1** — `backend/tools/schemas/ppa_obligations.py:474-512` (envelope), `backend/adk/a2ui_obligation_render.py:180-312` (transform), `backend/tools/map_ppa_obligations.py:614-643` (read-back). Field types `date|number` only.
- **Generic chat-form render (already generic, no FE change)** — `frontend/src/components/chat/ChatPlacementForms.tsx`, `A2UISurfaceMount.tsx`, `useActionDrivenAgent.ts:210-224`, `SurfaceRegistry.readA2uiSurfaceState:412-433`, `surface-action-run` route.
- **Landing** — `backend/db/clients.py:98` (`resolve_default_skill`), `frontend/src/hooks/useLandingTarget.ts`.

## Proposed Milestones

### Milestone 1: Elicitation-in-chat primitive (8.1 foundation)
**Scope:** backend (zero frontend)
**Goal:** Reusable `confirm`/`confirm_with_fields` primitive any tool/skill/handoff can call; obligation flow re-pointed at it with no regression.
**Estimated:** ~350 impl + ~150 tests = ~500 LOC
**Duration:** ~2.5d

**Tasks:**
- [ ] `backend/adk/elicitation.py`: move `ElicitationEnvelope`/`ElicitationField`; add `kind`/`message`/`options`/`context`; widen types to `date|text|number|select|bool` (~130)
- [ ] `backend/adk/a2ui_elicitation_render.py`: generic `elicitation_form_to_a2ui` + generic matcher + `placement:"chat"`; `confirm` kind = message + button(s) (~180)
- [ ] `dropdown`/`checkbox` `_Tree` builders — verify ids vs `agent-protocols` a2ui-v0.9-basic-catalog (~50)
- [ ] `request_confirmation` FunctionTool + generic `read_submitted_values` helper (~80)
- [ ] Re-point obligation flow at the primitive; keep `build_payload_from_assumptions` validators (~40 delta)
- [ ] Unit tests: transform per field type + `confirm`; read-back; obligation regression (~150)

**Files:** `backend/adk/elicitation.py` (new), `backend/adk/a2ui_elicitation_render.py` (new), `backend/adk/a2ui_ppa_render.py` (mod), `backend/tools/schemas/ppa_obligations.py` (mod), `backend/tools/map_ppa_obligations.py` (mod), `backend/adk/a2ui_obligation_render.py` (mod/retire renderer)

**Acceptance Criteria:**
- [ ] A scratch non-obligation tool returning a `confirm` envelope renders a chat card in a **real AG-UI stream** (not jsdom)
- [ ] `select`/`bool` render; obligation "build-from-assumptions" still renders + validates + re-elicits (real browser)
- [ ] `make lint && make test-fast` clean

**Risks:**
- Regressing the shipped obligation flow — Mitigation: refactor behind identical envelope shape; obligation E2E is an acceptance gate before M2.
- A2UI-render trap (result never registers as artifact) — Mitigation: diff against 7.3/7.5 known-good path; verify `A2UI_SURFACE` emits on the wire.

### Milestone 2: Delegation floor + request_handoff + front door (8.2 part A)
**Scope:** backend + config
**Goal:** Per-delegate confirmation floor; `request_handoff` (AI picks level, clamped to floor, access-validated) replacing `propose_delegation`; the `one-assistant` skill.
**Estimated:** ~280 impl + ~140 tests = ~420 LOC
**Duration:** ~2d

**Tasks:**
- [ ] `DelegationConfig` per-delegate `floor` + optional `fields` (back-compat: string `allow` + `mode`; `suggest`→`confirm`) (~90)
- [ ] `request_handoff(target, reason, level, fields?)`; **remove `make_propose_delegation_tool`** (decided: no shim); clamp `max(level, floor)`; access-validate vs catalog (~120)
- [ ] Wire into `create_agent` delegation seam (`agent.py:692-724`): auto→sub_agent + transfer signal; confirm/confirm_with_fields→elicitation envelope w/ reserved `confirm_delegation` action (~70)
- [ ] `backend/skills/templates/one-assistant/SKILL.md` + `local_fixture.py` mirror
- [ ] Unit tests: floor clamping, access rejection, envelope shape per level (~140)

**Files:** `backend/db/models/__init__.py` (mod), `backend/adk/agent.py` (mod), `backend/skills/templates/one-assistant/SKILL.md` (new), `backend/db/local_fixture.py` (mod)

**Acceptance Criteria:**
- [ ] `aiplatform skill probe one-assistant`: fast TTFT; PPA read → auto handoff events; compare → `confirm` envelope (not auto transfer)
- [ ] Floor clamps AI level up; target outside access catalog rejected
- [ ] `make lint && make test-fast` clean

**Risks:**
- Access-filter bypass via floor — Mitigation: floor is a ceiling only; `_resolve_accessible_delegates` stays the hard gate; test deny-by-default for a non-tagged user.

### Milestone 3: Confirm→switch loop + frontend + tenant cutover (8.2 part B)
**Scope:** fullstack
**Goal:** A confirmed handoff actually switches skills on the same thread; every transition is never-silent; ONE lands on the door on dev.
**Estimated:** ~230 impl + ~90 tests = ~320 LOC
**Duration:** ~2d

**Tasks:**
- [ ] `confirm_delegation` handling in `surface-action-run`: read collected values, re-issue turn on target skill via `create_agent_with_thinking`, same `thread_id`, seed values; keep the per-request `LatencyTracker` bind; `AGENT_DELEGATION` on switch (~130)
- [ ] Frontend: retire passive "Suggested X" chip; confirm card via existing chat-form path; **fix silent import-by-reference** (`ChatShell.tsx:1145-1148`) (~50)
- [ ] Tenant cutover: `aiplatform client set` / REST PATCH `clients/acmeenergy.com` → `default_skill=one-assistant` + `enabled_skills` (**local fixture AND deployed dev**, decided)
- [ ] Tests: re-issue on target/same-thread/seeded values; never-silent paths (~90)

**Files:** `backend/protocols/a2ui_surface_action_run_routes.py` (mod), `frontend/src/components/chat/DelegationMarker.tsx` (mod/retire suggest), `frontend/src/components/chat/ChatShell.tsx` (mod), `frontend/src/hooks/useSkillAgent.ts` (mod), deployed Firestore (PATCH)

**Acceptance Criteria:**
- [ ] **Real browser** as ONE user: land on `one-assistant`; general Q fast; auto handoff → `one-ppa-expert` on same thread; compare → confirm card → Proceed → switches to `one-doc-compare`; L2 form → submit → completes. No dead air; error/empty render.
- [ ] `npm run quality:check` + `make lint && make test-fast` clean

**Risks:**
- **The recurring "A2UI won't render / action-run silently no-ops" trap** — Mitigation: this path MUST bind the tracker via `set_current_tracker`; diff against the compare-launcher fix (2026-07-11); split backend-emit verification from FE-render.
- Live dev-tenant change affecting real ONE users on dev — Mitigation: reversible; announce; verify immediately post-PATCH.

### Milestone 4: Jobs & subagents (8.3)
**Scope:** backend + config
**Goal:** Obligation-analysis as a delegatable L2 job; access-scoped `job:true` discovery; subagent-assignment pattern documented + one example.
**Estimated:** ~200 impl + ~100 tests = ~300 LOC
**Duration:** ~1.5d

**Tasks:**
- [ ] `one-obligation-analysis` job skill (move `map_ppa_obligations` + launcher); `one-ppa-expert` + front door delegate to it (floor `confirm_with_fields`)
- [ ] Access-scoped `job:true` discovery in `_resolve_accessible_delegates` (door opt-in); `aiplatform skill list --jobs`
- [ ] Subagent pattern doc + worked example (inline `sub_agent` vs `AgentTool`)
- [ ] Tests: obligation-as-delegated-job E2E; discovery deny-by-default; subagent session continuity (~100)

**Files:** `backend/skills/templates/one-obligation-analysis/SKILL.md` (new), `backend/adk/agent.py` (mod discovery), `backend/skills/templates/one-ppa-expert/SKILL.md` (mod), `cli/aiplatform/commands/skill.py` (mod)

**Acceptance Criteria:**
- [ ] Front door → "analyze obligations for X" → L2 form → submit → verified settlement (real browser)
- [ ] Non-tagged user cannot discover the job; a new `SKILL.md` alone makes it discoverable to an opted-in door
- [ ] `make lint && make test-fast` clean

**Risks:**
- Moving the obligation tool breaks the launcher/action wiring — Mitigation: keep the action names + surface-context contract identical; obligation E2E gate.

## Model Assignment

<!-- Rubric: .claude/skills/sprint-planner/resources/model-assignment.md -->

| Stage / milestone | Model | Why |
|-------------------|-------|-----|
| Planning | `claude-opus-4-8` (high) | Decomposition of already-detailed docs; interactive. |
| Execute M1 (elicitation primitive + obligation re-point) | `claude-fable-5` | High subtlety: refactors a **shipped** protocol-boundary flow without regressing it; A2UI-render trap; well-specified → Fable's sweet spot. |
| Execute M2 (floor + request_handoff + front door) | `claude-fable-5` | Security-critical (access-filter clamp) + judgement logic; complete spec. |
| Execute M3 (confirm→switch + FE + tenant) | `claude-fable-5` | Highest subtlety: session continuity on re-issue, the recurring LatencyTracker-bind/action-run silent-no-op trap, never-silent guarantees. |
| Execute M4 (jobs & subagents) | `claude-opus-4-8` (xhigh) | Mostly moving a tool + config + discovery filter; lower subtlety. |
| Evaluation (all rounds) | `claude-opus-4-8` + report-everything | Cross-model check over Fable-written cores; report low-confidence too. |
| Sub-agents (browser verify, test loops, probes) | `claude-sonnet-4-6` | Procedural. |

**Session-model note:** M1–M3 are Fable-assigned by the rubric. **User decision 2026-07-14: run the entire sprint on `claude-opus-4-8` (xhigh)** — no model switch. Rubric recommendation retained above for the record; the subtle-milestone risk is mitigated by the per-milestone E2E gates and adversarial (report-everything) evaluation.

## Day-by-Day Breakdown (indicative — compress to velocity)

- **Day 1–2:** M1 — extract schema, generic transform + builders, entry points; re-point obligation; obligation E2E gate.
- **Day 3–4:** M2 — floor schema, `request_handoff`, front-door skill; `probe` acceptance.
- **Day 5–6:** M3 — confirm→switch loop, frontend confirm card + import fix, dev-tenant cutover; real-browser E2E gate.
- **Day 7:** M4 — obligation-as-job, discovery, subagent pattern; final E2E + quality gates.

## Quality Gates

After each milestone:
```bash
cd backend && make lint && make test-fast
cd frontend && npm run quality:check:fast   # M3+ touch FE → run full quality:check
```
After all milestones: real-browser E2E (aitana-frontend-verify) + `aiplatform skill probe` + obligation-refactor safety run.

## Success Metrics
- [ ] All backend tests passing (`make test-fast`) + lint/format clean
- [ ] Frontend `npm run quality:check` clean
- [ ] Real-browser E2E: land-on-door + L0/L1/L2 handoffs + obligation-as-job all green
- [ ] Obligation "build-from-assumptions" unregressed after the M1 refactor
- [ ] No silent transition anywhere; import-by-reference error now visible

## Dependencies
- A2UI Basic-catalog component ids for Dropdown/Checkbox (verify via `agent-protocols` skill) — blocks M1 builders.
- Deployed dev Firestore admin access for the tenant PATCH — blocks M3 cutover (CLI `aiplatform client set` or REST).

## Open Questions
- 8.1 OQ2: co-locate Dropdown/Checkbox in `a2ui_ppa_render.py` or extract `a2ui_basic.py` now? (Leaning: extract now.)
- 8.2 OQ2: emit a transient "Handing off…" STAGE_PROGRESS before an L0 delegate's first token? (Leaning: yes.)
- 8.3 OQ1: curated door uses explicit `allow` vs `job:true` discovery? (Leaning: explicit for the flagship door.)

## Notes
- Frontend stays thin: the chat-form render path is already generic — M1 is zero-FE, M3's FE is the confirm affordance + one bug fix.
- Never re-derive the A2UI render path; diff against 7.3/7.5 and the 2026-07-11 compare-launcher tracker-bind fix.
