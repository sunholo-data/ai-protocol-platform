# First-Impression Front Door + Elicited Handoff

**Status**: Planned
**Priority**: P1
**Estimated**: ~4 days (4 phases)
**Scope**: Fullstack (mostly backend + config; minimal frontend)
**Dependencies**: 8.1 elicitation-in-chat-primitive (this doc consumes it), 7.1 skill-delegation ✅ (`delegation` block, `transfer_to_agent`, `AGENT_DELEGATION`), 6.5.0 authenticated-landing ✅ (`resolve_default_skill`), 6.6.0 model tiers ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

## Problem Statement

An `acmeenergy.com` (ONE) user logs straight into `one-ppa-expert` — a heavy 8-tool
specialist with a PPA-specific greeting (`"PPA, PtX, BESS — what would you like to
analyse?"`). There is no fast, always-ready **general** assistant as the front door, and
the delegation machinery that could route from a light assistant to deep specialists
(shipped in 7.1) is **wired into zero skills**.

**Current State:**
- ONE's landing skill (`clients/acmeenergy.com.default_skill`) is `one-ppa-expert`
  (`backend/db/clients.py:98` resolves it). First token carries the weight of an 8-tool
  specialist even for "just chatting."
- `general-assistant` exists (`lite`, 3 tools, TTFT-tuned) but is **not** in ONE's
  `enabled_skills` and wires no delegation.
- The 7.1 `suggest` mode is a **dead-end**: `propose_delegation`
  (`backend/adk/agent.py:417-467`) only returns a `pending_confirmation` record and tells
  the model to *ask in prose*; `DelegationMarker.tsx` is a passive chip with **no confirm
  affordance**, and **nothing switches the active skill on confirm**. A suggested handoff
  cannot actually complete.

**Impact:**
- Every ONE user pays specialist cost on the first turn (Axiom #1).
- The "one fast assistant that hands down to deeper agents as work is requested" model —
  the product's intended shape — is unbuilt despite all primitives existing.

## Goals

**Primary Goal:** ONE users land on a fast "ONE Assistant" that answers general/vocabulary
questions immediately and **hands down to deep specialists on demand**, where each handoff
is a **runtime AI judgement** over three levels, and confirmation (when needed) is an
**A2UI element in the chat area**, never dead air.

**The three levels (AI-judged, policy-bounded):**
- **L0 — auto:** the AI just does it → ADK `transfer_to_agent`, `AGENT_DELEGATION` marker only.
- **L1 — confirm (OK):** needs a user OK → an A2UI **confirm card** in chat (Proceed / Not now).
- **L2 — confirm + info (OK + extra info):** needs OK *plus* extra information → an A2UI
  **elicitation form** in chat (AI-authored fields) → completes the handoff with the collected values.

The AI picks the level; a per-delegate **policy floor** raises it (a trust ceiling — an
expensive/trust-critical delegate can require ≥L1 or ≥L2 regardless of the AI's judgement).

**Success Metrics:**
- Front-door TTFT ≈ `general-assistant` today (`lite`, ~400ms first token; no `thinkingModel` on the door).
- A general question is answered by the door with **no handoff**.
- A PPA read ("extract the clauses of X") → **L0 auto** handoff → `one-ppa-expert` answers on the same thread.
- A compare request → **L1 confirm card in chat** → Proceed → active skill becomes `one-doc-compare`.
- Zero silent transitions: every handoff shows pending → progress → terminal (result or visible error).

**Non-Goals:**
- Mid-turn escalation (stream fast then escalate within the same turn) — see `mid-turn-escalation.md` (deferred).
- Frontend warm-start / TTI hop reduction — see `frontend-warm-start-tti.md` (deferred).
- Removing the skill chooser — it stays; the specialists remain listed and directly selectable.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Front door is `lite` + tiny toolset → fast first token; depth is deferred to on-demand delegates instead of paid up-front. |
| 2 | EARNED TRUST | +1 | L1/L2 keep the human in the loop for consequential handoffs; L2 collected values are read from the surface model, not transcribed by the LLM. |
| 3 | SKILLS, NOT FEATURES | +1 | The front door is just another skill; the handoff level is configured per-delegate in the `delegation` block (Skill-Studio-authorable). No new user abstraction. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Fast model at the door; smart specialists only when the work needs them — the axiom made literal. |
| 5 | GRACEFUL DEGRADATION | +1 | A denied/inaccessible delegate degrades to the door answering; confirm/RUN_ERROR/timeout all render (never-silent). |
| 6 | PROTOCOL OVER CUSTOM | +1 | Confirmation rides the A2UI elicitation primitive + surface-action loop; handoff rides ADK `transfer_to_agent` + `AGENT_DELEGATION`. No new wire format. |
| 7 | API FIRST | +1 | The judgement + floor live server-side in `create_agent`; every channel gets the same handoff behavior (`aiplatform skill probe` verifies headless). |
| 8 | OBSERVABLE BY DEFAULT | +1 | `mark_delegation` + `AGENT_DELEGATION` + STAGE_PROGRESS trace every handoff and its level. |
| 9 | SECURE BY CONSTRUCTION | +1 | Delegates stay access-filtered deny-by-default (`_resolve_accessible_delegates`); the policy floor is a ceiling on AI autonomy, not a bypass. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The level judgement + skill switch happen in the backend; the frontend renders a generic chat form + marker. |
| | **Net Score** | **+10** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Add a new `one-assistant` front-door skill that delegates to the ONE specialists; extend
the `delegation` config with a per-delegate confirmation floor; replace the prose `suggest`
path with primitive-driven L0/L1/L2; close the confirm→switch loop by re-issuing the turn
against the target skill on the same thread.

### Backend Changes

**1. Per-delegate confirmation floor — `DelegationConfig` (`backend/db/models/__init__.py:49`):**
- Allow `delegation.allow` entries to be either a bare skill id (inherits the block `mode`)
  or a structured entry `{ skill, floor: "auto"|"confirm"|"confirm_with_fields", fields?: [...] }`.
- `floor` = the *minimum* confirmation level; the effective level = `max(AI judgement, floor)`.
- Backward-compatible: existing string lists + the `mode: auto|suggest` field keep working
  (`suggest` maps to floor `confirm`). Default off (`enabled: false`).

**2. Judgement + level wiring — `create_agent` (`backend/adk/agent.py:692-724`):**
- The parent gets a single delegation tool, `request_handoff(target_skill_id, reason, level, fields?)`,
  replacing/superseding `propose_delegation`. The model chooses `level`; the tool clamps it
  up to the delegate's `floor` and validates the target against the access-filtered catalog
  (as `propose_delegation` already does).
- `level == "auto"` → the tool signals an immediate transfer; the delegate is also wired as an
  ADK `sub_agent` (existing auto path) so `transfer_to_agent` control flow is available.
- `level in {confirm, confirm_with_fields}` → the tool returns an **elicitation envelope**
  (8.1 primitive) with a reserved action `confirm_delegation` and
  `context = { target_skill_id, collected_fields_spec? }`. For `confirm` it's an OK/cancel card;
  for `confirm_with_fields` the model-authored `fields` render as a chat form.
- Keep `mark_delegation(parent, target, mode/level)` for observability (`AGENT_DELEGATION`).

**3. Close the confirm→switch loop — `surface-action-run` (`backend/protocols/a2ui_surface_action_run_routes.py`):**
- Recognize the reserved `confirm_delegation` action. On confirm/submit: read any collected
  values via the primitive's `read_submitted_values`, then **re-issue the turn against
  `target_skill_id` on the same `thread_id`** — build the target agent via
  `create_agent_with_thinking` (same path the chat endpoint uses) and stream it, seeding the
  collected values into the run state. Session/context carries over natively
  (`use_thread_id_as_session_id=True`, `backend/adk/agui.py`; session keyed by
  `(APP_NAME, user_id, thread_id)`). This path already binds its own `LatencyTracker` — keep that.
- Emit an `AGENT_DELEGATION` marker on the switch so the transcript records the completed handoff.

### Frontend Changes

**Minimal.**
- The confirm card / elicitation form render via the **existing** `placement:"chat"` path
  (`ChatPlacementForms.tsx`) — no new component. The card's Proceed button fires the reserved
  `confirm_delegation` surface-action (generic path, already wired).
- `DelegationMarker.tsx` stays as the persistent transcript record of a completed/auto handoff.
  The interactive confirm is the A2UI card, not the marker (the passive "Suggested X" chip is retired).
- **Bug fix (never-silent):** `handleImportByReference` swallows failures to a `console.error`
  (`frontend/src/components/chat/ChatShell.tsx:1145-1148`) — surface a visible notice. In-scope
  because we're already editing the confirm wiring here.

### Config / Skill Changes

**New `backend/skills/templates/one-assistant/SKILL.md`** (modeled on `general-assistant`):
- `access_control: tagged [ONE, aitana-admin]`; `model: lite`; **no `thinkingModel`** (keep the door fast).
- `tools: [google_search, list_documents, get_document_content]` (tiny — protects TTFT).
- `delegation.enabled: true`, `allow` (**decision 2026-07-14: front-door
  specialists are all `auto`** — transparent `transfer_to_agent`; the confirm /
  confirm_with_fields levels are reserved for the expensive *job* skills in 8.3,
  not these read/compare specialists):
  - `one-ppa-expert` → floor `auto` (vocab, clause extraction, ai_search)
  - `web-researcher` → floor `auto`
  - `one-doc-compare` → floor `auto` (two-contract diff)

  The three-level model (L0/L1/L2) and its machinery (`request_handoff` + the
  elicitation confirm card) remain built and tested; the front door simply uses
  L0 for all its specialists for now. The **confirm→switch loop is therefore not
  built in this push** — it is only needed once a delegate has a confirm floor
  (an 8.3 job), at which point it re-issues the confirmed turn on the target
  skill via `surface-action-run` (design retained below for that work).
- ONE-branded greeting + instruction body: be a fast router; answer general/vocab directly;
  hand heavy work down via `request_handoff` and let the primitive handle any confirm/elicit.

**Tenant config (deployed Firestore, not a repo seed):** set
`clients/acmeenergy.com.default_skill = one-assistant` and add it to `enabled_skills`
(keep the specialists listed — the chooser stays) via `aiplatform client set` / admin REST
PATCH. Mirror in `backend/db/local_fixture.py` (~L732) for `make dev`. **Cutover scope
(decided 2026-07-14): apply to local fixture AND deployed dev** so the real landing is
click-testable on dev; test/prod tenant docs stay on `one-ppa-expert` until promotion.
Reversible by pointing `default_skill` back.

### CLI Surface

- Extend `aiplatform skill probe` (already prints delegation events) to show the chosen
  **level** and (for L1/L2) that a `placement:"chat"` confirm/elicit envelope was emitted —
  so the handoff level is verifiable headless without a browser.

### Architecture Diagram

```
[ONE user] → landing (resolve_default_skill) → one-assistant (lite, fast)
   │ "what's PaP vs PaN?"      → answered at the door (no handoff)
   │ "extract clauses of X"    → request_handoff(level=auto)     → transfer_to_agent → one-ppa-expert
   │ "compare A and B"         → request_handoff(level=confirm)  → A2UI confirm card in chat
   │                                   │ Proceed → confirm_delegation action
   │                                   ▼
   │                            surface-action-run → re-issue turn on one-doc-compare (same thread)
   └ (L2 job) → request_handoff(level=confirm_with_fields) → A2UI form → submit → re-issue with values
```

## Implementation Plan

### Phase 1: Config schema + front-door skill (~1d)
- [ ] `DelegationConfig` per-delegate floor + `fields` (back-compat with string list + `mode`) (~80 LOC + tests).
- [ ] `one-assistant/SKILL.md` template; local-fixture mirror.

### Phase 2: Judgement + levels (~1d)
- [ ] `request_handoff` tool (clamp to floor, access-validate, return transfer-signal or envelope) (~120 LOC).
- [ ] Wire into `create_agent` delegation seam; retire the prose `propose_delegation` path.

### Phase 3: Confirm→switch loop (~1d)
- [ ] `confirm_delegation` handling in `surface-action-run`: re-issue on target skill, same thread, seed values (~120 LOC).
- [ ] `AGENT_DELEGATION` on switch; `probe` prints level.

### Phase 4: Frontend + tenant + verify (~1d)
- [ ] Retire passive "Suggested X" chip; confirm card via existing chat-form path; fix import-by-reference silent error.
- [ ] Set ONE tenant `default_skill`/`enabled_skills`.
- [ ] Real-browser E2E (see Testing).

## Migration & Rollout

- **Rollout control:** `delegation.enabled` is per-skill and default-off; only `one-assistant`
  turns it on. Existing skills unaffected.
- **Tenant switch is reversible:** point `default_skill` back to `one-ppa-expert` to roll back
  the landing change independently of the code.
- **No schema migration** — `delegation.allow` structured entries are additive; string entries still parse.

## Testing Strategy

### Backend Tests (pytest)
- [ ] Floor clamping: AI level < floor → clamped up; AI level ≥ floor → respected.
- [ ] `request_handoff` rejects targets outside the access-filtered catalog.
- [ ] `confirm_delegation` re-issues against the target skill on the same thread and seeds values.

### Manual / Real-stream + Browser (jsdom is NOT sufficient — per CLAUDE.md)
- [ ] `aiplatform skill probe one-assistant`: fast TTFT; PPA read → auto handoff events; compare → `confirm` envelope (not auto transfer).
- [ ] Real browser as a ONE user: lands on `one-assistant`; general Q answered fast; auto handoff to `one-ppa-expert`; compare → confirm card → Proceed → switches; L2 form → submit → completes. No dead air; error/empty paths render.

## Security Considerations

- Delegates remain access-filtered **deny-by-default** (`_resolve_accessible_delegates`,
  `backend/adk/agent.py:378`); `allow` is a ceiling, the floor cannot bypass access.
- The confirm→switch re-issue runs the target skill under the **same authenticated user**;
  no privilege change across the handoff.
- ONE skills are `tagged [ONE, aitana-admin]`; the front door shares that gate so its
  greeting/affordances don't leak to the public marketplace.

## Success Criteria

- [ ] ONE login lands on `one-assistant` with `lite`-class TTFT.
- [ ] Auto (L0), confirm (L1), and confirm+info (L2) handoffs all work end-to-end in a real browser.
- [ ] No silent transition anywhere in the handoff flow; import-by-reference error now visible.
- [ ] Skill chooser still lists and can directly open the specialists.
- [ ] `make lint && make test-fast` (backend) + `npm run quality:check` (frontend) clean.

## Open Questions

- OQ1: **Resolved 2026-07-14 — `request_handoff` supersedes `propose_delegation` entirely.**
  The prose `suggest` path is a dead-end with no external consumers; `make_propose_delegation_tool`
  is removed and the passive "Suggested X" chip retired in favor of the A2UI confirm card.
- OQ2: For L0 auto, do we still emit a transient "Handing off to X…" STAGE_PROGRESS before the
  delegate's first token, or rely on the delegate's `before_agent` `AGENT_DELEGATION`? (Leaning: both — the transient label covers the gap.)
- OQ3: Should the front door's greeting be PPA-aware (ONE hybrid) or neutral-general? (Product call — default to a light PPA-aware greeting that still invites general questions.)

## Related Documents

- [elicitation-in-chat-primitive.md](elicitation-in-chat-primitive.md) — the primitive this consumes (8.1)
- [skill-delegation.md](../v6.7.0/implemented/skill-delegation.md) — the delegation engine (7.1)
- [authenticated-landing.md](../v6.5.0/authenticated-landing.md) — the landing resolver (`default_skill`)
- [jobs-and-subagents.md](jobs-and-subagents.md) — L2 job skills the front door delegates to (8.3)
- [mid-turn-escalation.md](mid-turn-escalation.md), [frontend-warm-start-tti.md](frontend-warm-start-tti.md) — deferred companions
