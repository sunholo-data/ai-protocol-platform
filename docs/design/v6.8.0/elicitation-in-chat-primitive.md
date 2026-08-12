# Elicitation-in-Chat Primitive

**Status**: Planned
**Priority**: P1 (foundation for 8.2 elicited-handoff)
**Estimated**: ~3 days (4 phases)
**Scope**: Backend (+ zero frontend changes)
**Dependencies**: 7.6 ppa-obligation-analysis ✅, 7.8 obligation-analysis-elicitation ✅ (the M1 this generalizes), 7.3 tool-results-as-a2ui ✅ (result→A2UI registry), a2ui-surface-context ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-14

## Problem Statement

The platform has exactly one way to collect structured input from a user *in the
chat area* mid-conversation: the obligation "build-from-assumptions" A2UI form
(7.8). It works, ships to `dev`, and proves the pattern — but the **elicitation
contract is hardwired inside the obligation tool** and cannot be reused.

**Current State:**
- The envelope schemas (`ElicitationEnvelope`, `ElicitationField`) live in
  `backend/tools/schemas/ppa_obligations.py:474-512` — a domain module, not a shared home.
- The field→A2UI transform (`obligation_elicitation_form_to_a2ui`,
  `backend/adk/a2ui_obligation_render.py:180-312`) is bespoke and registered **only**
  for `map_ppa_obligations` via the `_is_needs_assumptions` matcher.
- The field-type vocabulary is only `date | number` (`ElicitationFieldType`,
  `schemas/ppa_obligations.py:474`). There are **no `Dropdown`/`Checkbox` builders**
  in `_Tree` (`backend/adk/a2ui_ppa_render.py`), so `select`/`bool` cannot render.
- The fields are a **hardcoded 9-field tuple** (`_ELICITATION_FIELDS`,
  `schemas/ppa_obligations.py:521-636`) — the "AI constructs the fields, the engine
  validates them" generalization (the intended M2 in
  `tool-input-elicitation-a2ui.md`) was **designed but never shipped**.
- There is **no way for the *agent* to raise a form from its own judgement** — only
  a tool refusal can trigger elicitation today.

**Impact:**
- Blocks 8.2 (elicited handoff): the three-level handoff (auto / confirm / confirm+info)
  needs a generic confirm/collect-in-chat primitive that any skill or the handoff path
  can call.
- Every future "ask the user to pick / confirm / fill in X" interaction would otherwise
  re-implement the obligation plumbing. Violates Axiom #6 (protocol over custom) and the
  repo's "elicitation is the downstream model" principle.

## Goals

**Primary Goal:** A reusable, domain-agnostic "elicitation-in-chat" primitive — any
tool, skill, or the delegation path can request in the chat area either (a) a bare
OK/cancel **confirm**, or (b) a **confirm-with-fields** form whose fields the AI authors
and the backend validates — with **zero new frontend code**.

**Success Metrics:**
- A second consumer (the handoff path, 8.2) renders a chat form with **no frontend changes** and **no obligation imports**.
- Field-type coverage: `date | text | number | select | bool` all render (today only `date|number`).
- The obligation flow is re-pointed at the primitive and still passes its existing E2E (no regression).
- `request_confirmation(...)` callable by an agent yields a chat form within the same turn.

**Non-Goals:**
- Multi-step wizards / branching forms (single form per elicitation; re-elicit loop already covers "fix and resubmit").
- Frontend redesign — the `placement:"chat"` render path stays exactly as-is.
- Removing the obligation-specific *validators* (`build_payload_from_assumptions` stays the engine-validated boundary).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Reuses the streamed out-of-model A2UI emit; a confirm card appears immediately instead of a prose round-trip. |
| 2 | EARNED TRUST | +1 | Values are read authoritatively from the surface data model, not transcribed by the LLM (keeps the trust-critical numbers out of the model's mouth); engine-validated on submit. |
| 3 | SKILLS, NOT FEATURES | +1 | Any skill author gets confirm/collect-in-chat by returning an envelope — no bespoke UI, no orchestration internals exposed. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Neutral — orthogonal to model routing (though it enables the handoff that does route). |
| 5 | GRACEFUL DEGRADATION | +1 | Inherits the never-silent submit/RUN_ERROR/freeze paths; a blank/invalid field re-elicits with a reason instead of failing. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Replaces a bespoke per-tool transform with one registered A2UI (Basic-catalog) transform over the standard surface-action loop. |
| 7 | API FIRST | +1 | The envelope is a tool-result contract, channel-agnostic; the CLI (`aiplatform`) can drive the same submit via `assumptions=`/state. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Covered by existing tool-call + surface-action tracing. |
| 9 | SECURE BY CONSTRUCTION | +1 | Engine re-validates LOUDLY on submit (the A2UI regexp is UX-only); surface-context reads are per-turn snapshots, no new trust surface. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All logic stays server-side; the frontend keeps rendering `placement:"chat"` artifacts generically — zero client changes. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no -1 scores).

## Design

### Overview

Extract the envelope schema and the field→A2UI transform out of the obligation modules
into shared, domain-agnostic homes; widen the field vocabulary and add the missing A2UI
builders; add an agent-callable `request_confirmation` entry point; and re-point the
obligation flow at the shared primitive to prove the extraction (production, not demo).
The transport + render layers are **already generic** and untouched.

### Backend Changes

**New module — `backend/adk/elicitation.py`:**
- `ElicitationField` — moved from `schemas/ppa_obligations.py`. Widen
  `type: Literal["date","text","number","select","bool"]`; keep `name` (doubles as the
  dataModel path AND the ingress key — "the closed loop"), `label`, `help`, `default`,
  `required`, `unit`, `resolves`. Add `options: list[str] | None` for `select`.
- `ElicitationEnvelope` — moved. Add `kind: Literal["confirm","confirm_with_fields"]`
  (default `confirm_with_fields` for back-compat) and optional `message: str`. Keep
  `action`, `reason`, `fields`, plus an opaque `context: dict` passed back on submit.
- `request_confirmation(message, kind="confirm", fields=None, action=..., context=None)` —
  a FunctionTool the *agent* calls (it authors `fields`) that returns an envelope. This is
  the agent-initiated entry point; a tool refusal returning an envelope is the
  tool-initiated entry point. Both converge on the same transform.
- `read_submitted_values(tool_context, surface_hint=None) -> dict` — generalized from
  `_read_assumptions_from_state` (`backend/tools/map_ppa_obligations.py:614-643`): reads
  `state["a2ui_surface_state"]`, prefers the `a2ui_action_trigger.surfaceId` surface,
  returns the submitted dataModel keyed by field name.

**Generalized transform — `backend/adk/a2ui_elicitation_render.py`:**
- `elicitation_form_to_a2ui(envelope)` — promoted from `obligation_elicitation_form_to_a2ui`.
  Domain-agnostic: partitions required/optional, maps each field by `type`
  (`date`→`datetime_input`, `text`/`number`→`text_field` with optional numeric regexp,
  `select`→`dropdown`, `bool`→`checkbox`), emits a submit `button` firing `envelope.action`
  with `envelope.context`, and a seed `updateDataModel`. For `kind:"confirm"` emit just
  `message` text + a primary Button (+ optional cancel) — no field inputs.
- Registered via the existing `register(...)` (`backend/adk/a2ui_result_render.py`) with a
  generic matcher `is_elicitation(result)` (result carries an `elicitation` dict) and
  `artifact_meta -> {"placement":"chat", ...}`; opt-in per tool by `tool_names`.

**New `_Tree` builders — `backend/adk/a2ui_ppa_render.py` (or a shared `a2ui_basic.py`):**
- `dropdown(path, options, ...)` and `checkbox(path, ...)` mapping to the A2UI Basic
  catalog (verify component ids against `agent-protocols` `references/a2ui-v0.9-basic-catalog.md`
  before finalizing). These are additive; existing builders unchanged.

**Re-point the obligation flow (prove by refactor):**
- `schemas/ppa_obligations.py` imports the shared `ElicitationField/Envelope`;
  `_ELICITATION_FIELDS` becomes an obligation-specific field list passed INTO the shared
  transform (the "floor" the design doc always intended), not its own renderer.
- `map_ppa_obligations` returns the shared envelope; `read_submitted_values` replaces
  `_read_assumptions_from_state`; `build_payload_from_assumptions` (the strict validators)
  is unchanged — it remains the engine-validated boundary.

### Frontend Changes

**None.** `ChatPlacementForms.tsx` renders any `placement:"chat"` artifact
(`useArtifacts().filter(a => a.placement === "chat")`), `A2UISurfaceMount` handles submit +
never-silent errors, `useActionDrivenAgent` + `SurfaceRegistry.readA2uiSurfaceState` do the
read-back. Verify the new field types render; do not modify.

### Architecture (round-trip)

```
tool refusal  ──┐                             ┌── kind:confirm         → message + primary button
agent judgement ┴─ ElicitationEnvelope ──►    ┤                        (no fields)
 (request_confirmation)                       └── kind:confirm_with_fields → required/optional field cards
        │                                          │
        │  register(elicitation_form_to_a2ui, placement:"chat")
        ▼                                          ▼
  A2UI_SURFACE (CUSTOM) ──► SurfaceRegistry ──► ChatPlacementForms (generic, unchanged)
        ▲                                          │ user fills + submits
        │  read_submitted_values(state)            ▼
  tool/agent re-run ◄── surface-action-run ◄── triggerAction (a2ui_surface_state snapshot)
```

## Implementation Plan

### Phase 1: Extract the contract (~0.75d)
- [ ] New `backend/adk/elicitation.py`: move `ElicitationField/Envelope`, add `kind`/`message`/`options`/`context`, widen field types (~120 LOC).
- [ ] Keep a thin re-export in `schemas/ppa_obligations.py` for back-compat (~10 LOC).

### Phase 2: Generic transform + builders (~1d)
- [ ] `backend/adk/a2ui_elicitation_render.py`: promote the transform, generic matcher, `placement:"chat"` (~180 LOC).
- [ ] Add `dropdown`/`checkbox` `_Tree` builders (verify against A2UI Basic catalog) (~50 LOC).
- [ ] `register(...)` the transform (opt-in per tool).

### Phase 3: Entry points (~0.5d)
- [ ] `request_confirmation` FunctionTool (~40 LOC) + `read_submitted_values` helper (~40 LOC).

### Phase 4: Re-point obligation + tests (~0.75d)
- [ ] Obligation flow consumes the shared primitive; delete the bespoke renderer path.
- [ ] Unit tests: transform for every field type + `kind:"confirm"`; `read_submitted_values`; obligation regression.

## Migration & Rollout

- **No data migration.** Envelope shape is additive (new optional fields); existing obligation results still parse.
- **Feature flag:** none needed — the primitive is inert until a tool/skill returns an envelope.
- **Rollback:** revert the obligation re-point; the extracted module is dormant if unused.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `elicitation_form_to_a2ui` renders each field type + `confirm` kind (snapshot the A2UI tree shape).
- [ ] `read_submitted_values` prefers the action-trigger surface; handles missing/partial state.
- [ ] Obligation "build-from-assumptions" still produces a valid payload after the re-point.

### Manual / Real-stream
- [ ] `aiplatform` run: a scratch tool returning a `confirm` envelope renders a chat confirm card in a real AG-UI stream (not jsdom).
- [ ] Obligation elicitation form still renders + validates + re-elicits in a real browser.

## Security Considerations

- Submit values are re-validated **loudly** server-side (`build_payload_from_assumptions`
  for obligation; each consumer owns its validator). The A2UI `validation_regexp` is UX-only.
- Reads use the A2UI **surface-context** channel (per-turn dataModel snapshot), not the MCP
  iframe-context channel — no new trust surface; consistent with existing gates.
- `request_confirmation` is a plain tool; it authors UI structure only, never executes an action itself.

## Success Criteria

- [ ] A non-obligation tool returns an envelope and gets a chat form with zero frontend edits.
- [ ] `select`/`bool` fields render (Dropdown/Checkbox).
- [ ] `request_confirmation` raises a chat confirm card from agent judgement in a real stream.
- [ ] Obligation flow unchanged in behavior (E2E green).
- [ ] `cd backend && make lint && make test-fast` clean.

## Open Questions

- OQ1: Should `request_confirmation` and the tool-refusal envelope share one `action`
  namespace, or should confirm/collect carry a reserved action (e.g. `elicit_submit`) that
  the surface-action-run path recognizes generically? (Leaning: reserved action + opaque `context`.)
- OQ2: Do we co-locate `dropdown`/`checkbox` in `a2ui_ppa_render.py` or extract a shared
  `a2ui_basic.py` now? (Leaning: extract now — the PPA coupling of `_Tree` is already a smell.)

## Related Documents

- [obligation-analysis-elicitation.md](../v6.7.0/obligation-analysis-elicitation.md) — the M1 this generalizes (7.8)
- [tool-input-elicitation-a2ui.md](../v6.7.0/tool-input-elicitation-a2ui.md) — the pattern doc whose M2 this delivers
- [first-impression-elicited-handoff.md](first-impression-elicited-handoff.md) — the first new consumer (8.2)
- [tool-results-as-a2ui.md](../v6.7.0/implemented/tool-results-as-a2ui.md) — the result→A2UI registry reused here
