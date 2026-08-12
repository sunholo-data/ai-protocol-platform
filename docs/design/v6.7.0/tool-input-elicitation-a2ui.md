# Tool-Input Elicitation via A2UI — AI guides the human to supply structured data for structured-data tools

**Status**: Planned (pattern) — first instance (effective-date form) building now
**Priority**: P1 (generalizes a recurring need; the "refinement, not a prose dead-end" principle)
**Scope**: Backend (a typed elicitation envelope + generic render) + frontend (chat-area A2UI form) — one shared mechanism, adopted per tool
**Created**: 2026-07-11
**Motivated by**: Mark — "AI helping human add structured data for its own structured-data tools." A structured-data tool that can't run accurately shouldn't guess (unverifiable) or refuse in prose (dead end) — it should hand the human a structured A2UI form that GUIDES them to supply exactly what the tool needs, then re-run with it.

## Problem

Structured-output tools (`map_ppa_obligations`, `extract_ppa_clauses`,
`compare_ppa_contracts`, and every future one) sometimes cannot run accurately
without more/better human input — a missing effective date, an ambiguous party,
a policy knob the contract leaves blank, which clauses to focus on. Today a tool
has two bad options:
1. **Guess** — produces a plausible-but-wrong result (unverifiable; trust-ending
   for a contract tool).
2. **Refuse in prose** — e.g. `needs_effective_date` renders a paragraph telling
   the user to "tell the assistant the date." A dead end: no structured way to
   comply, the user has to reverse-engineer the magic phrase.

Neither guides the human. The platform already has the ingredients to do better
(A2UI Basic catalog inputs, the surface-action loop, the out-of-model emitter)
but no general mechanism ties them together.

## Vision

Any tool can **elicit** structured input from the human: it returns a typed
"input needed" envelope; the platform renders it as an **A2UI form in the chat
area** (the protocols-first home for user input that feeds the AI); the human
fills it with guidance (labels, types, validation, options); submitting re-runs
the tool with the structured input. This is MCP's **elicitation** capability,
rendered via A2UI + surface-action rather than a client-native modal — so it is
protocol-native, works for **Model-B** skills (the agent never authors UI; the
out-of-model emitter does), never egresses confidential content, and obeys
NEVER-SILENT (#8).

## Design

### The elicitation envelope (tool → platform)
A tool that needs input returns (alongside its human-readable message, so it is
never a silent/opaque refusal) a typed block:

```json
{
  "elicitation": {
    "reason": "No effective date could be established for this contract.",
    "fields": [
      {"name": "effective_date", "type": "date", "label": "Contract effective / start date",
       "help": "Template PPAs often leave this blank; the result records it as an assumption.",
       "required": true, "min": "2000-01-01", "max": "2100-01-01"}
    ],
    "resubmit": {"action": "start_obligation_analysis", "context": {"doc": {"doc_id": "…"}}}
  }
}
```

`type` ∈ `date | text | number | select | bool` maps 1:1 onto A2UI Basic-catalog
components (`DateTimeInput`, `TextField`, number `TextField`+regex, `Dropdown`,
`Checkbox`). `resubmit` names the action to fire and the fixed context (doc
identity, etc.); the field values are merged into the resubmit payload.

### Generic elicitation → A2UI renderer (platform)
One renderer (extends `a2ui_result_render` registry) turns any `elicitation`
envelope into a chat-area A2UI form: the fields as inputs + a submit `Button`
whose `action` = `resubmit.action`, carrying `resubmit.context` + the collected
field values. No per-tool UI code — a tool opts in purely by returning the
envelope. Model-B safe (out-of-model emission).

### Submit → re-run (platform)
The form's Button fires `surface-action-run`; the field values ride the surface
state / action context; the synthetic turn re-invokes the tool with the
structured input (e.g. `map_ppa_obligations(doc, effective_date=…)`). Result
renders (success artefact, or a NEW elicitation if more is still needed) — always
visible, per #8.

### Validation + never-silent
Field-level validation (`required`, `min/max`, `validationRegexp`) renders inline
messages; an invalid submit shows a message, never a silent no-op. While the
re-run works, the form shows a working state and the run streams to Activity.

## First instance (building now)
Effective-date refinement: `map_ppa_obligations`'s `needs_effective_date` refusal
→ a date-input A2UI form in chat → submit re-runs with `effective_date`. This
ships as a concrete feature; the GENERALIZATION below extracts the reusable
envelope + renderer from it.

## Generalization plan (after instance #1 lands + is browser-verified)
1. Extract the `elicitation` Pydantic envelope (`schemas/`) from the
   effective-date shape.
2. Extract the generic elicitation→A2UI renderer (field-type → catalog
   component) from the effective-date render.
3. Re-express the effective-date case as the envelope (no behavior change).
4. Adopt in the next tools that need it — reviewed policy knobs (obligation),
   clause subset (compare), ambiguous-party resolution (extract) — each just
   returns an `elicitation` envelope; zero new UI.

## Production requirement — AI-proposes, host-constrains (this is the DOWNSTREAM MODEL, not a demo form)
This pattern is the reusable model for every downstream skill/agent that elicits
structured input via A2UI — build it to production:
- **AI-constructs the fields** from what the tool actually found in THIS input
  (e.g. the obligation mapper derives fields from its real `unmapped` placeholder
  list — a contract without a floor price gets no floor field). Hardcoded field
  lists are a fallback/floor, not the design.
- **The host CONSTRAINS** every AI-proposed field: it must map to a real
  capability/knob of the consuming tool OR be a disclosed derivation; unmappable
  fields are dropped/flagged; a minimal required floor is enforced. The LLM can
  never emit an unmappable, incompletable, or semantically-wrong form (no
  wrong-but-plausible values). AI proposes; the host validates by construction.
- **Reuse test:** a second, unrelated tool/agent returns an `elicitation`
  envelope and gets a working chat A2UI form with ZERO tool-specific UI code.

## Non-Goals
- LLM-authored form JS (confidential-content rule — A2UI declarative only).
- A client-native modal (breaks Model-B + protocols-first; A2UI-in-chat is the home).
- **Unvalidated LLM-authored field sets** — the AI proposes fields, but the host
  MUST validate them against the tool's capabilities (above); never render raw
  LLM field descriptors unchecked.
- Replacing free-text chat — elicitation is for when STRUCTURED input makes the
  tool accurate; conversational refinement still works alongside.

## Relation to the protocol stack
- **MCP elicitation** — this is the platform's equivalent, rendered via A2UI.
- **A2UI Basic catalog** — the field vocabulary (`DateTimeInput`, `TextField`,
  `Dropdown`, `Checkbox`, `Button.action`).
- **surface-action-run** — the submit → re-run transport.
- **NEVER SILENT (#8)** + **Protocols-first for UI** — the governing principles.

## Verification (non-negotiable)
Real browser (not jsdom-only): a template PPA → refusal → date form in chat →
submit → analysis re-runs. Error/empty/invalid paths render. When the envelope
generalizes, a second tool adopting it with zero UI code proves the abstraction.

## Success Criteria
- [ ] A tool returns an `elicitation` envelope and gets a chat-area A2UI form
      with NO tool-specific UI code.
- [ ] Submit re-runs the tool with the structured input; result (or next
      elicitation) renders — never silent.
- [ ] Effective-date case re-expressed as the envelope (behavior unchanged).
- [ ] A second tool adopts it (reviewed knobs or clause subset).
