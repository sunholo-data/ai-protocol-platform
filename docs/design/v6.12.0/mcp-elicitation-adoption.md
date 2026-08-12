# MCP Elicitation Adoption — one elicitation envelope, standards-aligned, sourced three ways

**Status**: Planned
**Priority**: P1 (Protocol-Over-Custom convergence; unlocks MCP-server-driven forms + the dormant AI-authored form path)
**Estimated**: ~2 days
**Scope**: Backend (MCP client capability + schema translator + wiring) — frontend reuses the existing A2UI chat form unchanged
**Dependencies**: v6.7.0 tool-input-elicitation-a2ui, v6.8.0 elicitation-in-chat-primitive (both shipped)
**Created**: 2026-07-16
**Last Updated**: 2026-07-16
**Motivated by**: Mark — "like claude-code questions, and I think MCP supports elicitations as well?" We hand-rolled an elicitation primitive (`request_confirmation` + `ElicitationField`) that is functionally MCP's `elicitation/create`. Adopt the standard, and let connected MCP servers ask our users too.

## Problem Statement

The platform built a good elicitation primitive (v6.7.0 `tool-input-elicitation-a2ui`, v6.8.0 `elicitation-in-chat-primitive`): a typed `ElicitationEnvelope` / `ElicitationField`, an A2UI chat-form render, an authoritative surface read-back. But three gaps remain, and one is an architectural smell:

1. **We invented a custom format where an open standard exists (Axiom #6 risk).** MCP added an `elicitation` capability in the 2025-06-18 spec: a server sends `elicitation/create` with a `message` + `requestedSchema` (a restricted JSON-Schema: flat object, primitive properties), the client renders a form and returns `{action: "accept"|"decline"|"cancel", content}`. Our `ElicitationField` (`name`, `type`, `label`, `help`, `options`, `required`) is **nearly 1:1** with MCP's `requestedSchema` property shape (`title`, `description`, `enum`, `required`). We diverged from a standard we should extend.
2. **Connected MCP servers cannot ask our users anything.** Our MCP client (`backend/tools/mcp/registry.py`) advertises a UI extension but **not** the `elicitation` capability, so an MCP server that issues `elicitation/create` gets an unsupported-capability error. Any third-party or first-party MCP tool that needs a runtime value from the human is dead-ended.
3. **The AI-authored form path is built but unwired.** `request_confirmation` (an agent authoring its own fields at runtime) exists, validates, and render-maps — but it is **not in the tool catalog** (`adk/tools.py`), so no deployed agent can call it. The "AI constructs, engine validates" model (the stated target) is dormant.

The through-line: there should be **one elicitation envelope**, rendered one way (A2UI in chat), sourced three ways — a tool authoring its own fields, an agent authoring its own fields, and now **an MCP server authoring its own fields via the standard `elicitation/create`** — with the MCP wire format as the interop boundary, not a competing internal representation.

## Goals

**Primary goal:** adopt MCP's `elicitation` capability at the MCP-client boundary and align our internal envelope to the standard, so an MCP server can elicit structured input from our user through the same A2UI chat form we already ship — and switch on the dormant AI-authored path in the same pass.

**Success metrics:**
- A connected MCP server issuing `elicitation/create` renders an A2UI form in chat; the user's submit returns a spec-correct `ElicitResult` (`action` + `content`) to the server; decline/cancel return the right action with no `content`.
- `ElicitationField` ↔ MCP `requestedSchema` round-trips losslessly for every supported type (string/number/integer/boolean/enum + `format:"date"`).
- `request_confirmation` is callable by at least one live skill (AI authors a form, engine validates, renders in chat, reads back).
- No frontend changes — the existing `placement:"chat"` A2UI render handles all three sources.

## Axiom Alignment

| # | Axiom | Score | Note |
|---|-------|-------|------|
| 1 | INSTANT FEEL | 0 | Elicitation is an interactive pause, not a latency path. |
| 2 | EARNED TRUST | +1 | Authoritative surface read-back (never model-transcribed); engine re-validates on submit; server provenance shown. |
| 3 | SKILLS, NOT FEATURES | +1 | One reusable envelope adopted by tools, agents, and MCP servers alike — not a per-tool widget. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal to model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | `decline`/`cancel` are first-class; an unrenderable/oversized schema degrades to a visible notice, never a silent hang (CLAUDE.md #8). |
| 6 | PROTOCOL OVER CUSTOM | +1 | The whole point: adopt MCP `elicitation` instead of a bespoke format; A2UI stays the render layer. |
| 7 | API FIRST | +1 | The contract is a protocol boundary, testable headlessly (mock MCP server → assert `ElicitResult`) before any browser. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Each `elicitation/create` is traced with the requesting `server_id`; accept/decline/cancel recorded. |
| 9 | SECURE BY CONSTRUCTION | +1 | Enforce the spec's "MUST NOT request sensitive info", show which server is asking, honor decline/cancel, rate-limit — ties to the CLAUDE.md confidential-content rule. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend renders a generic A2UI form; the schema/semantics ride the protocol. Zero bespoke React. |

**Net: +8** (threshold +4). No −1s. Hard-fail checks pass: EARNED TRUST = +1, SECURE BY CONSTRUCTION = +1 (the feature adds a new data-request path and is explicitly guarded).

## Standards Compliance Check

- **Adopted standard:** MCP `elicitation` (spec revision 2025-06-18). Method `elicitation/create`; params `message` + `requestedSchema`; result `{action, content}`; client capability `{"elicitation": {}}`. Verified against `https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation` (2026-07-16).
- **`requestedSchema` subset:** flat object; property types `string` (formats `email`/`uri`/`date`/`date-time`; `minLength`/`maxLength`), `number`/`integer` (`minimum`/`maximum`), `boolean` (`default`), enum (`type:"string"` + `enum` + `enumNames`). No nesting, no arrays-of-objects. Our `ElicitationField` maps cleanly (see translator).
- **No custom format invented** — the internal `ElicitationEnvelope` remains, but as an implementation representation now defined as a *lossless projection of* the MCP schema, not a competitor to it.

## Design

### Overview

Add elicitation as a first-class **inbound** MCP capability. The A2UI chat form (already generic) is the single render target for all three sources. A small bidirectional translator is the only new "schema" code.

```
 MCP server ──elicitation/create──►  MCP client (our backend)
                                        │  translate requestedSchema → ElicitationEnvelope
                                        ▼
                              [ existing A2UI chat form ]  ◄── same render for:
                                        │                     • tool-authored (map_ppa_obligations)
                                        │                     • agent-authored (request_confirmation)
                              user submit / decline / cancel
                                        │  read_submitted_values → {action, content}
                                        ▼
 MCP server ◄──── ElicitResult ────  MCP client
```

### Backend Changes

1. **Declare the capability.** In `backend/tools/mcp/registry.py`, add `"elicitation": {}` to the `ClientSession` capabilities alongside the existing UI extension, and register an **elicitation callback** on the session.
2. **Schema translator (`backend/adk/elicitation_mcp.py`, new).** Two pure functions:
   - `requested_schema_to_envelope(message, requestedSchema) -> ElicitationEnvelope`: `properties[k].title → label`, `.description → help`, `.enum(+enumNames) → options`, `format:"date" → type:"date"`, `type:"integer"|"number" → number`, `boolean → bool`, `required[] → field.required`. Unsupported/nested schema → a `confirm`-kind notice with a clear "this form couldn't be rendered" message (never silent).
   - `submission_to_elicit_result(action, dataModel, fields) -> dict`: `accept` → coerce/validate `content` against the field types; `decline`/`cancel` → `{action}` with no `content`.
3. **Inbound handler.** The elicitation callback: build the envelope (tagging `context.mcp_server_id` + a display name for provenance), emit it as an A2UI chat surface via the existing out-of-model emitter, await the surface submit (the surface-action loop), and return the `ElicitResult`. Reuse `make_elicitation_result` / `read_submitted_values`.
4. **Wire the AI-authored path.** Add `"request_confirmation": lambda _config: FunctionTool(request_confirmation)` to the `adk/tools.py` catalog and list it in an opt-in skill (proposed: **Contract Expert**). This is the same envelope, agent-sourced.
5. **Align field defaults.** Ensure `ElicitationField` accepts the MCP-native keys (`title`, `description`, `enum`, `enumNames`, `format`) as input aliases so a server schema needs no lossy pre-massage.

### Frontend Changes

**None.** `ChatPlacementForms` / `A2UISurfaceMount` already render any `placement:"chat"` A2UI artifact and run the submit→surface-action loop. Provenance ("Requested by <MCP server>") rides in the envelope `message`/header the backend authors. Verify, don't rebuild.

### API Changes

No new HTTP routes. The change is at the **MCP JSON-RPC boundary** (a new client capability + inbound method handler). The existing `/api/skill/{id}/stream` + surface-action-run paths carry the render + read-back unchanged.

### Architecture Diagram

```
                         ┌─────────────────────────────────────────┐
                         │  Backend (MCP client + ADK agent host)   │
 MCP server ──create──►  │  registry.py  ─►  elicitation_mcp.py     │
      ▲                  │   (capability)     (schema translator)   │
      │                  │        │                    │            │
      │                  │        ▼                    ▼            │
      │                  │   out-of-model A2UI emitter ──► surface  │──► browser (A2UI chat form)
      │                  │        ▲                    │            │        │
      └── ElicitResult ──┤  read_submitted_values ◄────┘            │◄── submit / decline / cancel
                         └─────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Schema alignment (~0.5d)
`elicitation_mcp.py` translator both directions + unit tests (every type, formats, enum, unsupported→notice). Add MCP-native input aliases to `ElicitationField`.

### Phase 2: Inbound capability (~1d)
Declare `elicitation` capability, register the callback in `registry.py`, emit via the A2UI emitter, await submit, return `ElicitResult`. Integration test with a mock MCP server.

### Phase 3: AI-authored path + polish (~0.5d)
Catalog `request_confirmation`, add to Contract Expert, provenance header, rate-limit + sensitive-info guard, observability tags. Real-browser pass.

## Migration & Rollout

- Purely additive: no existing skill/tool changes behavior. Gate the inbound capability behind `MCP_ELICITATION_ENABLED` (default on in dev, staged to test/prod) so a misbehaving server can be cut off fast.
- Backward compatible: servers that never elicit are unaffected; `request_confirmation` only appears where a skill opts in.
- Rollback: drop the capability declaration (servers fall back to not-supported) + remove the catalog entry; no data migration.

## Testing Strategy

### Backend Tests (pytest)
- Translator round-trip: `requestedSchema → envelope → ElicitResult` for string/number/integer/boolean/enum/date; unsupported/nested schema → visible notice, not a crash.
- Inbound handler with a **mock MCP server** that issues `elicitation/create`: assert an A2UI surface emits, a simulated submit returns `{action:"accept", content}` matching the schema; decline/cancel omit `content`.
- Security: a schema flagged sensitive is refused; provenance server id is attached.

### Frontend Tests (Vitest + React Testing Library)
- Regression only — the existing `ChatPlacementForms` tests cover render/submit; add one asserting a server-provenance header renders.

### Manual Testing
- Real browser: a mock (or first-party) MCP server elicits mid-tool-call → form in chat → submit → tool continues with the value. Per CLAUDE.md, jsdom is **not** sufficient — verify a real surface emit + read-back over the AG-UI stream first.

## Security Considerations

- Enforce the spec's **"servers MUST NOT request sensitive information"** — a lightweight allow-pattern + a refusal path; never forward a raw credential/secret request to the user.
- **Provenance is mandatory**: the card states which MCP server is asking (opaque server → friendly name, per CLAUDE.md #9 friendly-names rule).
- Honor **decline/cancel** at any time; rate-limit inbound elicitations per server per session.
- Elicited `content` never auto-egresses; it returns only to the requesting server over the in-project MCP channel. Ties to the CLAUDE.md confidential-content rule — an MCP server must not use elicitation to exfiltrate.

## Performance Considerations

- Elicitation blocks the requesting tool call until the user answers — bound it with a timeout that resolves to `action:"cancel"` (never an indefinite hang), matching the never-silent principle.
- The translator is pure/O(fields); no added latency on non-eliciting turns.

## CLI Surface

- `aitana mcp elicit-test <server>` — issue a canned `elicitation/create` against a connected MCP server's client shim (or a local mock) and print the rendered envelope + the `ElicitResult`, so the round-trip is checkable without a browser. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## Success Criteria

- [ ] MCP client declares `{"elicitation": {}}`; a mock server's `elicitation/create` renders an A2UI chat form and returns a spec-correct `ElicitResult`.
- [ ] `ElicitationField ↔ requestedSchema` round-trips losslessly for all supported types; unsupported schema degrades to a visible notice.
- [ ] `request_confirmation` is callable by Contract Expert (AI authors a form → engine validates → renders → reads back).
- [ ] Provenance, decline/cancel, sensitive-info refusal, and rate-limit all covered by tests.
- [ ] Zero frontend render changes; verified in a real browser.

## Open Questions

- **OQ1 — request_confirmation scope:** Contract Expert first, or a broader opt-in? (Lean: PPA Expert first, then generalize once proven.)
- **OQ2 — sensitive-info detection:** allow-list of field intents vs a denylist of patterns (email/uri OK; anything smelling of secret/credential refused)? (Lean: conservative denylist + server allow-list.)
- **OQ3 — `enumNames`:** our `select` options are `{value,label}`; confirm the `enum`/`enumNames` pairing maps to our option label cleanly.

## Related Documents

- [tool-input-elicitation-a2ui.md](../v6.7.0/tool-input-elicitation-a2ui.md) — the original A2UI elicitation envelope + chat-form render (tool-authored).
- [elicitation-in-chat-primitive.md](../v6.8.0/elicitation-in-chat-primitive.md) — the generalized primitive + `request_confirmation` (agent-authored).
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — CLI surface backlink.
- MCP Elicitation spec (2025-06-18): `https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation`.
