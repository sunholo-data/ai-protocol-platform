# First-Run Path Conformance

**Status**: Implemented + verified live (2026-07-29)
**Priority**: P0 (High) — the documented onboarding path is broken
**Estimated**: 1.5 days
**Scope**: Backend + config
**Dependencies**: None
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

A downstream fork seeded from the public template on 2026-07-29, followed
`WORKSHOP.md` verbatim, and could not get a chat turn to complete. Five
findings, and they share one cause:

> **The template's headline onboarding path is the one path nobody runs.**

Our dev loop is Vertex AI with real GCP credentials. The documented tier-1 path
is `LOCAL_MODE=1` + a Gemini **Express Mode** API key + zero GCP. Every defect
below is invisible from the first and fatal on the second.

**Current State** — each verified against source before being accepted:

| # | Defect | Verified |
|---|---|---|
| **2** | `request_confirmation`'s `fields: list[dict[str, Any]]` and `context: dict[str, Any]` make ADK emit `additional_properties`, which the Gemini Express `FunctionDeclaration` proto does not define → **400 on every request** | `FunctionTool(request_confirmation)._get_declaration()` emits it on exactly those two params. `enable_confirmation` defaults `True` (`db/models/__init__.py:199`), so it is attached to **every skill** |
| **1** | `backend/.env.example:40` ships `ADK_ARTIFACT_BUCKET` **uncommented** with a real bucket name, while the neighbouring `DOCUMENTS_BUCKET` is commented out | Confirmed — inconsistent with its own neighbours |
| **4** | LOCAL_MODE's banner claims `Disabled: cloud_trace, cloud_logging`, but OTEL still exports | `observability/telemetry.py` contains no `is_local_mode` / `OTEL_SDK_DISABLED` guard |
| **3** | `dev-local.sh` guards against an ambiguous `GOOGLE_API_KEY` but not against the `GEMINI_API_KEY` it steers you to | Confirmed |
| **5** | 210 Dependabot alerts (7 critical) inherited on first push | Confirmed on the fork |

**Impact:** a workshop attendee or student following the README gets
`RUN_ERROR` on their **first message**, in **every** demo skill, with an error
naming neither the tool nor the env var. The template's stated promise is
"working chat UI in under 30 minutes, zero GCP credentials."

**Why it shipped:** Vertex tolerates the extra schema field; Express Mode
rejects it. Deployed envs never reproduce it. And our unit tests never assemble
the real flattened `FunctionDeclaration` list, so nothing caught the shape.

This is the **second** instance of "provider-side schema constraint discovered
only at the first live turn" — `backend/adk/CLAUDE.md` already documents the
builtin-tools-cannot-combine-with-function-tools 400. Two instances means the
shared guard is overdue, not a third one-off.

## Goals

**Primary Goal:** `LOCAL_MODE=1` + a Gemini Express key produces a working chat
turn on a freshly-seeded fork, and a test fails if that stops being true.

**Success Metrics:**

- A default agent's flattened declaration set contains no field the Gemini
  Express proto rejects — asserted in the fast suite.
- `cp .env.example .env` + `LOCAL_MODE=1` needs no further edits to chat.
- LOCAL_MODE emits zero outbound telemetry, enforced in code rather than by a
  launch script unsetting env vars.
- A bad/absent Gemini key fails at startup with a named error, not at the first
  turn with an opaque one.

**Non-Goals:**

- Fixing the Dependabot backlog (#5). Real, but a dependency-upgrade programme,
  not this sprint. Tracked separately.
- Supporting every provider's schema quirks. Scope is the two we ship against.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | — |
| 2 | EARNED TRUST | +1 | First impression of the product is currently a `RUN_ERROR` |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | +1 | #1/#3/#4 all replace opaque late failures with named early ones |
| 6 | PROTOCOL OVER CUSTOM | +1 | Conform to the provider's declared schema rather than emitting a superset and hoping |
| 7 | API FIRST | 0 | — |
| 8 | OBSERVABLE BY DEFAULT | +1 | #4 makes the LOCAL_MODE banner's claim true instead of aspirational |
| 9 | SECURE BY CONSTRUCTION | +1 | #4 stops a fork leaking our project identity in its telemetry |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | — |
| | **Net Score** | **+5** | Threshold: >= +4 |

**Conflict Justifications:** None.

## Design

### #2 — Sanitize declarations at the model layer (the general fix)

Strip schema fields the target endpoint's proto does not define, in the model
layer, when the provider is Gemini Express. Chosen over retyping the two params
because it protects **every future tool** with a `dict[str, Any]` param — the
narrow fix leaves the trap armed for the next author.

Retyping is *also* worth doing (`context` as a declared model rather than a bare
dict is better regardless), but as cleanup, not as the guard.

### #2 — The conformance test (non-negotiable)

Build a default agent, flatten its `FunctionDeclaration` set, assert no
rejected field appears. Today's unit tests never assemble that list, which is
precisely why this reached a published template. Same class as the
production-semantics doubles in v6.19.0: green tests over a shape production
never sees.

### #1 — Comment out the artifact bucket

One line, matching its own neighbours. In LOCAL_MODE the artifact service should
fall back to in-memory rather than 404 against a bucket that does not exist.

### #4 — Hard-disable OTEL in LOCAL_MODE

Set the SDK to a no-op at init when `is_local_mode()`, rather than relying on a
launch script unsetting env vars. The banner already claims this; make the claim
enforceable.

### #3 — Validate the Gemini key at startup

One cheap `GET /v1beta/models?key=…` when `GEMINI_API_KEY` is set and
LOCAL_MODE is on. Fail with a named, actionable error. Cheap, and it converts
the most likely first-run failure into a sentence.

### Residuals folded in

- **#15** — `CLAUDE.md` tells forkers to load `aitana-adk-testing`, which
  `DELETE_PATHS` excludes. Either ship it or say it does not ship; the fork-note
  already does this for its siblings, so extend it.
- **#45** — `app.py:77` passes a literal `"gemini-2.5-flash"` to
  `get_compaction_config`. The registry seam is enforced for agent models; this
  call bypasses it.

## Implementation Plan

### Phase 1 — the blocker (~0.75d)
- Model-layer declaration sanitize + conformance test (red-then-green).

### Phase 2 — first-run papercuts (~0.5d)
- #1 `.env.example`; #4 OTEL hard-disable; #3 key validation.

### Phase 3 — residuals (~0.25d)
- #15 fork-note; #45 registry seam.

## Migration & Rollout

**Feature Flags:** None — all are correctness fixes on a broken path.
**Rollback Plan:** Each phase is independently revertible.
**Environment Variables:** None new.

## Testing Strategy

The load-bearing test is the conformance one, and it must **fail against
today's code** — a test that passes before the fix asserts nothing. Verify
red-then-green explicitly, as with the v6.19.0 doubles.

`make test-fast` + `npm run quality:check` for regression.

## Success Criteria

- [x] Default agent's declarations carry no Express-rejected field (verified red-then-green: 5 of 15 tests fail against pre-fix code)
- [x] **A real chat turn completes against `generativelanguage.googleapis.com`** — confirmed on the downstream fork, 2026-07-29, LOCAL_MODE + Express key. This is the criterion that matters: schema-side proof is the same class of evidence that let the bug ship, since our dev loop is Vertex and Vertex tolerates the field
- [x] `cp .env.example .env` + `LOCAL_MODE=1` chats with no further edits
- [x] LOCAL_MODE emits no outbound telemetry
- [x] A bad Gemini key fails at startup, named
- [x] `CLAUDE.md` no longer points forkers at an unshipped skill
- [x] No literal model id in `app.py`

## Related Documents

- Downstream fork feedback log (entries 1–5) — the source of every finding here
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md)
- `backend/adk/CLAUDE.md` — the sibling Gemini constraint guard

---

## Implementation Report

**Completed**: 2026-07-29
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
