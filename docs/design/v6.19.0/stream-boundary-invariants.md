# Stream Boundary Invariants

**Status**: Planned
**Priority**: P0 (High)
**Estimated**: 1 day
**Scope**: Backend
**Dependencies**: None (touches `fast_api_app.py` `stream_skill` only)
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

The AG-UI SSE boundary — the async generator in `fast_api_app.py` that turns ADK
events into the wire stream — forwards whatever the adapter yields, with **no
confidentiality boundary**:

**Current State:**

- **AIPLA #39** — privileged tool *results* are mirrored onto the client
  stream. A server-side tool whose result is confidential (their case: a
  judging tool returning the teacher's answer key + rubric) reaches any user
  with devtools open. Nothing marks a tool result as not-client-visible.
- ~~**AIPLA #32** — `RUN_ERROR` terminality~~ — **DESCOPED 2026-07-29.** Found
  already implemented in `backend/adk/agui.py` (`terminal_event_yielded` +
  `_TERMINAL_EVENT_TYPES`) when implementation started. It sits at event
  normalisation rather than the SSE wrapper, which is the better layer — the
  prelude and the main loop are the same code path there — and it is stronger
  than the reported fix: it drops any event after *either* terminal and logs
  `agui_terminal_dedup`. See `docs/design/template/template-agui-terminal-dedup.md`
  and its 10 tests. **My original triage recorded this as open; that was wrong**
  (the check grepped only `fast_api_app.py`).

**Why we never saw it:** closed-loop blindness. We are the MCP server *and* the
host, and every session we test with is a trusted owner session, so nothing
forces us to respect an audience split. It took a fork with two real trust levels
(teacher / student) to surface it.

**Impact:**

- #39 is a **confidentiality hole**, and a structural one: it contradicts the
  repo's own architectural rule that confidential derivatives must sit behind
  the same gate as their source. Any fork with a privileged server tool and a
  lower-trust audience leaks by default.

## Goals

**Primary Goal:** Make the SSE boundary enforce the invariant *tool results are
privileged by default*, so confidentiality does not depend on every future tool
author remembering.

**Success Metrics:**

- A privileged tool result never reaches a lower-trust session; verified by a
  test that asserts the redaction fails **closed** on an unmatched result id.
- The invariant holds regardless of which adapter produced the events (enforced
  at the wrapper, not inside `ag_ui_adk`).

**Non-Goals:**

- Fixing `ag_ui_adk` itself. That is the correct upstream-of-us fix and belongs
  in a PR against that repo; this doc is the belt-and-braces layer that protects
  every fork today regardless.
- A general per-tool ACL system. The default flips to privileged; opting a tool
  into client-visibility is a one-line registration.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Filter is O(1) per event, no added round-trips |
| 2 | EARNED TRUST | +1 | A user seeing another role's answer key is the clearest possible trust breach |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | 0 | — |
| 6 | PROTOCOL OVER CUSTOM | 0 | — |
| 7 | API FIRST | 0 | — |
| 8 | OBSERVABLE BY DEFAULT | +1 | Redaction and drop decisions are logged, so "why did I not see that event" is answerable |
| 9 | SECURE BY CONSTRUCTION | +1 | Deny-by-default at the boundary; a new tool is private unless explicitly published |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | — |
| | **Net Score** | **+4** | Threshold: >= +4 |

**Conflict Justifications:** None — no axiom scores -1.

## Design

### Overview

One new module, `backend/adk/stream_invariants.py`, wrapping the existing event
iterator in `stream_skill`. It is a generator-to-generator filter holding a small
amount of per-request state.

```
ADKAgent.run()  ->  ag_ui_adk events  ->  [privilege gate]  ->  SSE to client
                     (already enforces          (#39, new)
                      terminality)
```

`stream_skill` currently pulls the first event out separately (to surface a 404
before the stream opens) and then loops over the rest — two branches over the
same stream. The wiring **merges them into a single generator** before filtering,
so a future invariant cannot be applied to one branch and not the other. That
prelude/loop split is precisely the trap AIPLA flagged.

### Backend Changes

**New Services/Modules:**

- `backend/adk/stream_invariants.py`
  - `redact_privileged_results(events, *, is_privileged_session: bool)` —
    maps `TOOL_CALL_START` id → tool name, then decides each tool-result event.
    **Fails closed:** an unmatched result id is redacted, because AG-UI result
    events carry no tool name of their own.
  - `CLIENT_VISIBLE_TOOLS: frozenset[str]` — the explicit allowlist. Seeded with
    the genuine client-render paths: A2UI surface emission, MCP-server `ui://`
    results, and the card-safe result tools already mapped to workbench
    artifacts.

**Modified Endpoints:**

- `fast_api_app.py::stream_skill` — wrap `event_iter` in the filter. The
  privilege flag derives from the session's trust level (group-token sessions
  are lower-trust; a Firebase-authenticated owner is not).

**Data Model Changes:** None.

### API Changes

None. This is a wire-behaviour change, not a route change: the same endpoint
emits a strictly smaller, spec-legal subset of what it emits today.

### Interaction with existing offload behaviour

`_handle_large_output` offloads >50K tool results to an artifact and substitutes
a pointer. The redaction filter runs on the *event stream*, after that
substitution, so a redacted-but-offloaded result must also not leak its pointer.
Cover this explicitly in tests — it is exactly the kind of second path that gets
missed (the `_RENDER_PAYLOAD_TOOLS` exemption list is the precedent).

## Implementation Plan

### Phase 1: Privilege gate (#39) (~0.75 day)

- `redact_privileged_results` + the allowlist + session trust derivation.
- Tests: privileged result redacted for a group session; same result passes for
  an owner session; **unmatched result id is redacted** (fail-closed); an
  allowlisted A2UI/`ui://` result passes; an offloaded pointer for a privileged
  tool is redacted.

### Phase 2: Observability + docs (~0.25 day)

- Structured log line per redaction/drop (tool name, session trust, reason) so
  the "why did my event vanish" question is answerable — required by principle
  #8 (NEVER SILENT), which a silent filter would otherwise violate.
- Note the invariant in `backend/adk/CLAUDE.md` next to the existing emission
  playbook.

## Migration & Rollout

**Feature Flags:** None. This is a security fix and ships on. A flag would just
be a way to keep leaking.

**Rollback Plan:** Revert the wrapper call in `stream_skill`; the module is
inert without it.

**Environment Variables:** None.

## Testing Strategy

Backend only (`pytest`). The critical cases are the **negative** ones — the
fail-closed path and the offload branch. A test that only
proves the happy path would have passed against today's broken code.

A real-stream check is also required before calling this done: run a skill via
`aiplatform skill …` and inspect the emitted events, per the repo's standing
rule that jsdom/unit green does not prove wire behaviour.

## Success Criteria

- [ ] Privileged tool results are absent from a group-token session's stream
- [ ] Unmatched result ids fail closed
- [ ] Allowlisted client-render paths (A2UI, `ui://`, workbench artifacts) still work end-to-end
- [ ] Offloaded-artifact pointers respect the same gate
- [ ] Every drop/redaction emits a structured log line

## Related Documents

- AIPLA upstream feedback #39 (#32 descoped — already shipped) — `cphu-aipla-app/docs/upstream-feedback.md`
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md) — triage record
- [adk-contract-checklist.md](../v6.17.0/adk-contract-checklist.md) — the custom↔ADK seam rules this sits on
