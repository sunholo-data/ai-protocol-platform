# Production Semantics in Tests

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: 1 day
**Scope**: Backend (test infrastructure)
**Dependencies**: None
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

Our test doubles are **more permissive than production**, so a whole class of
bug is invisible to CI and only appears on a deployed environment — usually
during a demo.

**Current State:**

- `InMemorySessionService` lets **any uid read any session**. Real
  `VertexAiSessionService` enforces exact owner match and raises
  `"... does not belong to user"`.
- ADK state fixtures use a plain `dict`, which has **no concept of key-prefix
  scoping**. Real `State` treats `app:` as application-global, `user:` as
  per-user, unprefixed as session-scoped.

**Two production incidents, both invisible to CI:**

- **AIPLA #35** — a uid-scheme migration broke live Agent Engine sessions. Chat
  returned no text while MCP-app tool events kept working, a confusing signature.
  Every chat-path test used `InMemorySessionService`, so ownership enforcement
  was never exercised.
- **AIPLA #37 / our issue [#38](https://github.com/sunholo-data/ai-protocol-platform/issues/38)** —
  a per-session turn counter stored under `app:chat_session_turn_count` was one
  **global odometer** shared by every user and session. A teacher report showed
  `turnCount: 259` for an 18-second, 2-message session. The dict-based fixture
  reported it as correct.

**Impact:** #37 is the sharpest evidence that this matters. AIPLA documented it
2026-06-23; **we hit the identical bug and fixed it 2026-07-28** (commit
`4999307`, six mis-scoped keys including a cross-user RAG corpus). A month apart,
same root cause, and CI could not have caught either occurrence. The state-key
fix has landed; the **blind spot that allowed it has not**.

## Goals

**Primary Goal:** Make the two production semantics that have already burned us
— session ownership and state-key scoping — observable in fast unit tests.

**Success Metrics:**

- A test asserting cross-uid session access **fails** unless ownership is honoured.
- A test with two interleaved sessions proves per-session counters stay independent,
  and **fails** if a counter is moved back under an `app:` prefix.
- Both run in the fast suite — no GCP credentials, no `@pytest.mark.integration`.

**Non-Goals:**

- The legacy-owner compatibility shim from AIPLA #35. That solves *their*
  migration; we have no equivalent live migration. The reusable half is the test
  double.
- Replacing `InMemorySessionService` everywhere. The strict double is opt-in for
  suites that care about identity.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | — |
| 2 | EARNED TRUST | +1 | #37's visible symptom was wrong numbers shown to a user |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | 0 | — |
| 6 | PROTOCOL OVER CUSTOM | 0 | — |
| 7 | API FIRST | 0 | — |
| 8 | OBSERVABLE BY DEFAULT | +1 | Moves two production-only failure modes into the fast feedback loop |
| 9 | SECURE BY CONSTRUCTION | +1 | Cross-user state bleed is a security property; one of the six mis-scoped keys was a cross-user RAG corpus |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | — |
| | **Net Score** | **+3** | Threshold: >= +4 |

**Threshold note:** this scores **+3**, below the +4 bar, because it is test
infrastructure — it changes no runtime behaviour and therefore cannot align with
most product axioms by construction. Rather than inflate scores to clear the bar,
the honest read is that the axiom rubric is aimed at user-facing features. The
justification for building it anyway is empirical, not axiomatic: this exact
blind spot has produced two production incidents across two repos in five weeks.
**Flagging explicitly for a human call** — if the threshold is meant to be
absolute, fold this work into the docs it protects (`stream-boundary-invariants`
and `fork-ready-defaults`) rather than shipping it as its own item.

## Design

### Backend Changes

**New Services/Modules:**

- `backend/tests/support/session_doubles.py`
  - `OwnershipEnforcingSessionService` — wraps `InMemorySessionService` and
    replicates Vertex semantics: `get_session` raises when
    `response.user_id != user_id`. Opt-in via fixture.
- `backend/tests/support/state_doubles.py`
  - `ScopedState` — a `State`-shaped double that routes `app:` / `user:` prefixed
    keys to shared stores and everything else per-session, so two interleaved
    sessions observe real isolation.

**Modified Endpoints:** None. Test-only.

**Data Model Changes:** None.

### Static tripwire

Alongside the doubles, a cheap assertion that no per-session callback writes an
`app:`- or `user:`-prefixed key. This is the guard that would have caught our
issue #38 directly, and it costs one test.

The existing `test_model_call_reliability_guard.py` is the precedent: a scanning
test that fails the build on a new violation, with a small reasoned allowlist.

### Residual cleanup

`backend/adk/callbacks.py:788` still documents the old `app:chat_session_initialized`
key in a docstring, though the code is fixed. Correct the text so the next reader
does not copy the prefix back.

## Implementation Plan

### Phase 1: Ownership double (~0.4 day)
- `OwnershipEnforcingSessionService` + a chat-path test using it.
- Control test proving the chain fails without ownership enforcement.

### Phase 2: Scoping double + tripwire (~0.4 day)
- `ScopedState` + interleaved-session counter test.
- Static tripwire over per-session callback factories.

### Phase 3: Residual (~0.2 day)
- Fix the stale docstring; note both doubles in `backend/CLAUDE.md` so they get used.

## Migration & Rollout

**Feature Flags:** None. Test-only.

**Rollback Plan:** Delete the fixtures; no runtime impact.

**Environment Variables:** None.

## Testing Strategy

This *is* testing infrastructure, so the meaningful check is that each double
**fails against the pre-fix code**. Write each test to reproduce the historical
bug first, confirm red, then confirm green — otherwise we ship a double that
asserts nothing.

## Success Criteria

- [ ] A cross-uid `get_session` raises under the strict double
- [ ] Two interleaved sessions keep independent turn counters under `ScopedState`
- [ ] Re-adding an `app:` prefix to a per-session key fails the tripwire
- [ ] Both doubles run in `make test-fast` with no GCP credentials
- [ ] The stale `app:chat_session_initialized` docstring is corrected

## Related Documents

- AIPLA upstream feedback #35, #37 (parts b and c)
- Issue [#38](https://github.com/sunholo-data/ai-protocol-platform/issues/38) — our independent rediscovery of #37
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md)
