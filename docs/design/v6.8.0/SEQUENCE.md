# v6.8.0 Build Sequence

**Gate:** v6.7.0 substantially landed — 7.1 skill-delegation ✅ (delegation engine, `transfer_to_agent`, `AGENT_DELEGATION`, access-filtered deny-by-default), 7.3/7.5 tool-results-as-a2ui + workbench-artifacts ✅, 7.6 obligation-analysis ✅, 7.7 model-reliability ✅ (heartbeats / never-dead-air), 7.8 obligation-analysis-elicitation ✅ (the A2UI-form-in-chat this generalizes).

**Status as of 2026-07-14:** Planned. Turns the shipped-but-dormant primitives into the product's intended shape — a fast "first impression" front door that hands down to deep specialists on demand, with confirmation rendered as A2UI in chat.

**Theme:** *One fast chat assistant that delegates to slower/smarter specialists on demand, asking for confirmation (or confirmation + extra info) only when the situation warrants it — and never going silent doing it.* The audit (2026-07-14) found the machinery already exists: delegation (7.1) is wired into zero skills; the `suggest` path is a prose dead-end; and the A2UI elicitation-in-chat is hardwired to the obligation tool at M1. This version generalizes the elicitation primitive, makes the handoff a runtime AI judgement over three levels (auto / confirm / confirm+info), and lands ONE users on a fast front door.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 8.1 | [elicitation-in-chat-primitive.md](elicitation-in-chat-primitive.md) | **P1** (foundation) | ~3d (4 phases) | 7.8 ✅ (M1 this generalizes), 7.3 ✅ (result→A2UI registry), a2ui-surface-context ✅ | **Reusable confirm/collect-in-chat primitive.** Extract `ElicitationEnvelope`/`ElicitationField` out of the obligation tool into `backend/adk/elicitation.py`; add a `kind` discriminator (`confirm`\|`confirm_with_fields`) + `message`; widen field types to `date\|text\|number\|select\|bool`; add `Dropdown`/`Checkbox` `_Tree` builders (absent today); promote `obligation_elicitation_form_to_a2ui` to a domain-agnostic registered transform; add an agent-callable `request_confirmation` entry point; generalize the surface read-back. Re-point the obligation flow at the primitive (prove by refactor). **Zero frontend changes** — `placement:"chat"` render is already generic. Delivers the unshipped M2 of `tool-input-elicitation-a2ui.md`. Net axiom **+8**, no −1. |
| 8.2 | [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) | **P1** | ~4d (4 phases) | 8.1 (consumes it), 7.1 ✅ (delegation engine), 6.5.0 ✅ (`resolve_default_skill`), 6.6.0 model tiers ✅ | **Fast "ONE Assistant" front door + three-level elicited handoff.** New `one-assistant` skill (`lite`, tiny toolset, ONE-branded) that delegates to the specialists. Handoff level is a **runtime AI judgement** bounded by a per-delegate policy floor: **L0 auto** (`transfer_to_agent`), **L1 confirm** (A2UI confirm card in chat), **L2 confirm+info** (A2UI form in chat, AI-authored fields). Replaces the prose `suggest` dead-end with a `request_handoff` tool; closes the confirm→switch loop by re-issuing the turn against the target skill on the same thread (`surface-action-run`). Points `clients/acmeenergy.com.default_skill = one-assistant`. Fixes the residual import-by-reference silent error. Net axiom **+10**, no −1; strongest on RIGHT-MODEL-RIGHT-MOMENT + INSTANT-FEEL. |
| 8.3 | [jobs-and-subagents.md](jobs-and-subagents.md) | P2 | ~2.5d (pattern + first job) | 8.2, 8.1, 7.1 ✅, 7.6 ✅ | **Jobs & subagents extensibility.** A "job" = a skill tagged `job:true` with a confirmation floor + its own tier. Make **obligation-analysis** a first-class **L2 job skill** the front door delegates to (today it's a tool+launcher inside `one-ppa-expert`). **Access-scoped discovery** (`job:true`, deny-by-default) so new jobs are found without hand-edited allow-lists. Document subagent assignment (inline `sub_agent` vs `AgentTool` helper) with session continuity. Net axiom **+9**, no −1. |
| 8.4 | [frontend-warm-start-tti.md](frontend-warm-start-tti.md) | P2 (**design-ahead, deferred**) | ~2d | 6.5.0 ✅, 6.1.0 ttft ✅ | **Frontend time-to-interactive.** Warm `/health` during the HomeGate redirect spinner (kill the 5–30s cold-start un-typeable window), collapse the duplicate by-slug + by-id skill fetch, skeleton the bare "Loading…" micro-moment, consider collapsing the two redirect hops. **Deferred by decision 2026-07-14 — captured, not built this push.** |
| 8.5 | [mid-turn-escalation.md](mid-turn-escalation.md) | P2 (**superseded by 8.6**) | — | — | **Intra-skill mid-turn escalation.** Stream a fast answer then escalate to the deep model within the same turn. **Subsumed by 8.6** (its cascade = 8.6 mechanism B); kept for the EARNED-TRUST-tension notes. |
| 8.6 | [complexity-graded-model-routing.md](complexity-graded-model-routing.md) | **P2** | ~4d (ladder) + research spike | 6.0.0 `_HeuristicRouter` ✅, 6.6.0 tiers ✅, 7.7 ✅, 8.2 ✅, 2026-07-14 model refresh (flagship+middle tiers) | **Ascending skill level — route by assessed complexity.** Generalise the binary `_HeuristicRouter`/`_should_think` into an N-rung ladder (fast → mid → top) over the refreshed tier lineup (flash-lite/Luna → Sonnet 5/Pro/Terra → Opus 4.8/Sol/Fable). Two layered mechanisms per the 2026 routing literature: up-front ROUTE (rule pass → cheap classifier) + CASCADE (try cheap, escalate on uncertainty/validation-failure, streamed + marked). Calibrated by escalation rate (~5–50%) via the 9.5 analytics. Subsumes 8.5. Net axiom **+7**; strongest on RIGHT-MODEL-RIGHT-MOMENT + INSTANT-FEEL. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 8.1 | [elicitation-in-chat-primitive.md](elicitation-in-chat-primitive.md) | Planned 2026-07-14 (P1, foundation — build first) |
| 8.2 | [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) | Planned 2026-07-14 (P1) |
| 8.3 | [jobs-and-subagents.md](jobs-and-subagents.md) | Planned 2026-07-14 (P2) |
| 8.4 | [frontend-warm-start-tti.md](frontend-warm-start-tti.md) | Design-ahead / deferred 2026-07-14 |
| 8.5 | [mid-turn-escalation.md](mid-turn-escalation.md) | Superseded by 8.6 (2026-07-14) |
| 8.6 | [complexity-graded-model-routing.md](complexity-graded-model-routing.md) | Planned 2026-07-14 (research + design; supersedes 8.5) |

## What ships in v6.8.0

**From 8.1 (elicitation-in-chat-primitive):**
- `backend/adk/elicitation.py` — shared `ElicitationEnvelope`/`ElicitationField` with `kind` (`confirm`\|`confirm_with_fields`) + `message` + `options`/`context`; field types widened to `date\|text\|number\|select\|bool`.
- `backend/adk/a2ui_elicitation_render.py` — domain-agnostic `elicitation_form_to_a2ui`, generic matcher, `placement:"chat"`, opt-in per tool; new `Dropdown`/`Checkbox` A2UI builders.
- `request_confirmation` FunctionTool (agent-initiated forms) + generic `read_submitted_values` ingress.
- Obligation flow re-pointed at the primitive (no behavior change). **Zero frontend changes.**

**From 8.2 (first-impression-elicited-handoff):**
- `DelegationConfig` per-delegate confirmation **floor** (`auto`\|`confirm`\|`confirm_with_fields`) + optional field spec (back-compat with string `allow` + `mode`).
- `request_handoff(target, reason, level, fields?)` — model chooses level, clamped up to floor, access-validated; supersedes the prose `propose_delegation`.
- Confirm→switch loop: `confirm_delegation` surface-action re-issues the turn on the target skill, same thread, seeding collected values; `AGENT_DELEGATION` on switch.
- New `one-assistant` front-door skill; `clients/acmeenergy.com` → `default_skill=one-assistant` (+ `enabled_skills`), local-fixture mirror.
- Frontend: retire the passive "Suggested X" chip (confirm card via the generic chat-form path); fix the import-by-reference silent error; `aiplatform skill probe` prints the chosen level.

**From 8.3 (jobs-and-subagents):**
- Obligation-analysis promoted to a delegatable **L2 job skill**; `job:true` access-scoped discovery; subagent-assignment pattern (`sub_agent` vs `AgentTool`) documented; `aiplatform skill list --jobs`.

**Deferred (design-ahead, nothing built):** 8.4 frontend-warm-start-tti.
**Planned (research + design):** 8.6 complexity-graded-model-routing (subsumes 8.5 mid-turn-escalation).

## Dependency Graph

```
7.8 obligation-elicitation ✅ ───┐
  (M1 hardcoded form)            │
7.3 result→A2UI registry ✅ ─────┼─→ 8.1 elicitation-in-chat-primitive
a2ui-surface-context ✅ ─────────┘     (generic confirm/collect-in-chat; zero FE change)
                                             │
7.1 skill-delegation ✅ ─────────┐           │
  (transfer_to_agent · marker)   ├───────────┴─→ 8.2 first-impression-elicited-handoff
6.5.0 authenticated-landing ✅ ──┤                 (ONE Assistant door · L0/L1/L2 · confirm→switch)
6.6.0 model tiers ✅ ────────────┘                       │
                                                         ├─→ 8.3 jobs-and-subagents
                                                         │     (obligation-as-job · discovery · subagents)
                                                         │
                        (deferred, design-ahead) ────────┼─→ 8.4 frontend-warm-start-tti
                                                         └─→ 8.6 complexity-graded-model-routing (subsumes 8.5)
                                                               ↑ over the refreshed fast/mid/top tier lineup
```
