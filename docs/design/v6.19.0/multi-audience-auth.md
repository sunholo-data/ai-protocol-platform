# Multi-Audience Auth

**Status**: Planned
**Priority**: P1 (Medium)
**Estimated**: 1 day
**Scope**: Fullstack (frontend clients + a backend guard helper)
**Dependencies**: None
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

The template models **one user**: a Firebase-authenticated owner. It also ships a
second auth mode (anonymous group tokens) without giving callers a way to say
*which* audience a request is for. So every module author picks one auth helper
for the whole file and silently breaks the other role.

**Current State:**

- `frontend/src/lib/apiClient.ts` exports two helpers — one minting a group
  token, one minting a Firebase token — and **no way to express "either"**.
- `AGUIProvider` mints the SSE bearer from a single global auth context. For a
  role that context doesn't describe, the gate short-circuits and the stream
  goes out with **no** `Authorization` header at all.

**How it manifested downstream (AIPLA #33, #34):** a dual-audience endpoint
(served to both teachers and students) was called through the teacher helper, so
every student request 401'd; and the teacher chat surface sent no token at all,
because the provider gated on the group context that is permanently null for a
teacher.

**Impact:** The AIPLA log tracks this as a family — #19, #21, #33, #34, plus the
July addendum on token refresh — **five instances of the same mistake**, shipped
separately. Upstream has already fixed #19 and #21. The two that remain are the
structural half: nothing stops the sixth instance.

**Honest scoping note.** Upstream has no teacher/student split, so the *symptom*
does not reproduce here. This is a **prevention** change, not a bug fix, and
should be argued as such. The reason to do it anyway: the template is the thing
forks inherit, and this footgun has now fired five times in the one fork we can
observe.

## Goals

**Primary Goal:** Make the acting audience explicit at the call site, so picking
the wrong token is a type/lint error rather than a 401 in production.

**Success Metrics:**

- One `fetchWithAuth(path, { audience })` entry point; `"either"` prefers
  whichever identity is present.
- `AGUIProvider` takes its token source as an explicit input rather than reading
  a global context.
- A lint rule fails the build when a role-specific helper is imported into a
  surface for the other role.
- One canonical backend `assert_*` guard, rather than a copy-pasted role check
  per route module.

**Non-Goals:**

- Introducing roles the platform does not have. This defines the *seam*; forks
  populate it with their own audiences.
- Changing either auth mechanism. Token minting is unchanged.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | — |
| 2 | EARNED TRUST | +1 | A 401 on a legitimately-permitted document read reads as "the app is broken" |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | +1 | `"either"` resolves to whichever identity exists instead of failing on absence |
| 6 | PROTOCOL OVER CUSTOM | 0 | — |
| 7 | API FIRST | +1 | The audience becomes part of the client contract instead of an implicit import choice |
| 8 | OBSERVABLE BY DEFAULT | 0 | — |
| 9 | SECURE BY CONSTRUCTION | +1 | A single canonical guard cannot drift the way 7 copy-pasted ones did |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Callers stop encoding auth-mode knowledge per module |
| | **Net Score** | **+5** | Threshold: >= +4 |

**Conflict Justifications:** None — no axiom scores -1.

## Design

### Frontend Changes

**Modified Components:**

- `frontend/src/lib/apiClient.ts` — collapse the two exported helpers into one:

  ```ts
  fetchWithAuth(path, { audience: "owner" | "group" | "either" })
  ```

  `"either"` prefers whichever token is present. The existing named helpers stay
  as thin deprecated wrappers for one release so the change is non-breaking.

- `frontend/src/providers/AGUIProvider.tsx` — accept an explicit token getter /
  audience prop. Gate on the auth state that actually describes the acting user,
  not on whichever context happens to be global. This composes with the existing
  `hadTokenOnceRef` behaviour (already fixed as #31) and with `onIdTokenChanged`
  (already fixed as #41) — neither is re-opened here.

**Lint fence:** path-scoped `no-restricted-imports` in the eslint config, so a
role-specific helper imported into the wrong surface directory fails the build,
with a message pointing at the dual-audience escape hatch. This is AIPLA's own
suggestion from #33, since implemented on their fork.

### Backend Changes

**New Services/Modules:**

- `backend/auth/guards.py` — one `assert_audience(user, *, require, detail=...)`
  helper. The AIPLA fork found the same role check copy-pasted byte-for-byte
  across 7 route modules, which then drifted. A single guard makes a
  dual-audience route an explicit, reviewable exception rather than a per-site
  judgement call.

**Modified Endpoints:** Route modules adopt the guard; no path or payload changes.

### API Changes

None on the wire.

## Implementation Plan

### Phase 1: Backend guard (~0.25 day)
- `auth/guards.py` + adopt in existing role-checking routes; tests for allow/deny.

### Phase 2: Frontend client seam (~0.5 day)
- `fetchWithAuth({ audience })`; deprecate the two named helpers; regression test
  asserting group→group token and owner→Firebase token.

### Phase 3: Provider + fence (~0.25 day)
- `AGUIProvider` explicit token source + test (null group user, present owner
  identity → stream still carries a bearer).
- eslint `no-restricted-imports` fence + a deliberately-wrong import proving it fails.

## Migration & Rollout

**Feature Flags:** None.

**Rollback Plan:** The old helpers remain exported through the deprecation
window, so reverting is removing the new entry point.

**Environment Variables:** None.

## Testing Strategy

Vitest for the client seam and the provider; pytest for the guard. The important
assertions are the cross-role ones — *group caller gets the group token*, *owner
caller gets the Firebase token* — since a single-role test passes against today's
broken shape.

## Success Criteria

- [ ] `fetchWithAuth` selects the token from an explicit `audience`
- [ ] `"either"` resolves to whichever identity is present
- [ ] `AGUIProvider` carries a bearer for an audience the global context does not describe
- [ ] Importing a role-specific helper into the wrong surface fails lint
- [ ] Every role-checking route uses the shared guard

## Related Documents

- AIPLA upstream feedback #33, #34 (family: #19, #21, and the #21 July addendum)
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md)
