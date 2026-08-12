# Fork-Ready Defaults

**Status**: Planned
**Priority**: P0 (High)
**Estimated**: 2.5 days
**Scope**: Fullstack (backend + build/deploy config)
**Dependencies**: None
**Created**: 2026-07-29
**Last Updated**: 2026-07-29

## Problem Statement

Five separate AIPLA findings share one root cause: **the template's defaults are
correct for us and wrong for everyone else**, and each one fails *quietly*.

**Current State:**

| # | Defect | Failure mode |
|---|---|---|
| **#16** | `backend/auth/group_id_auth.py` still carries the never-landed TODO — anonymous-group state lives in a process-memory dict | Group codes evaporate when Cloud Run scales to zero. Every fork rediscovers the `min-instances=1` workaround, defeating serverless |
| **#17** | `/gcs_config` is mounted read-only by both Dockerfile and cloudbuild; **zero Python reads it** | A forker reasonably assumes skills load from the bucket. Bootstrap scripts create it, nothing populates it, nothing reads it |
| **#18** | `frontend/Dockerfile` honours only pre-declared `NEXT_PUBLIC_*` ARGs; `get-firebase-config.sh` passes them all | Any new var is **silently dropped**. AIPLA's `NEXT_PUBLIC_AUTH_MODE` evaluated undefined → a Sign-In button rendered on a deployment that had suppressed it in source |
| **#42** | `fast_api_app.py:55` `_expected_prefix = "your-project-id"`, warn-only; `app.py:30` `_FALLBACK_PROJECT` defaults to our dev project | Brand-anchored **and** fail-open: warns on a correctly-configured fork, stays quiet on a genuinely wrong project |
| **#36** | The branch-push Cloud Build trigger and GitHub Actions CI are independent | Red CI still deploys. AIPLA shipped a ruff-format failure to dev on 2026-06-17; the same is true here |

**Impact:** These are the first five things a new fork hits, and none of them
announce themselves. They cost the AIPLA fork days across the v0.1 sprint. A
second fork is imminent, so the cost is about to be paid again.

Note on #42: the 2026-07-29 sanitize pass now rewrites the brand strings to
`your-project-id*` in the published template. That removes *our* name but keeps
the design flaw — still a baked literal, still fail-open.

## Goals

**Primary Goal:** A fresh fork can deploy, scale to zero, and add a
`NEXT_PUBLIC_` var without hitting a silent wrong-but-running state.

**Success Metrics:**

- Anonymous-group sessions survive a container restart with `min-instances=0`.
- Adding a `NEXT_PUBLIC_*` var requires no Dockerfile edit — or fails loudly if it does.
- A misconfigured project **refuses to boot** in non-LOCAL_MODE; a correct one is silent.
- A red CI cannot produce a deployed revision.
- `/gcs_config` either does something or is gone.

**Non-Goals:**

- Redesigning anonymous-group auth. #16 is "land the TODO that was always intended", not a rethink.
- Workload Identity Federation for #36 — the inline gate is zero-infra and race-free; WIF is a later optimisation.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | #16 adds a Firestore read on cache miss only |
| 2 | EARNED TRUST | +1 | A group session that silently evaporates mid-demo is the opposite of trustworthy |
| 3 | SKILLS, NOT FEATURES | 0 | — |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | — |
| 5 | GRACEFUL DEGRADATION | +1 | #16 degrades to Firestore instead of losing the session; #18/#42 fail loudly instead of running wrong |
| 6 | PROTOCOL OVER CUSTOM | 0 | — |
| 7 | API FIRST | 0 | — |
| 8 | OBSERVABLE BY DEFAULT | +1 | #42 turns a misleading warning into a real signal; #36 makes deploy state reflect CI state |
| 9 | SECURE BY CONSTRUCTION | +1 | #42 fail-closed boot; #36 stops unreviewed code reaching a running service |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | — |
| | **Net Score** | **+4** | Threshold: >= +4 |

**Conflict Justifications:** None — no axiom scores -1.

## Design

### #16 — Anonymous-group persistence

Port the AIPLA implementation, already proven in production on their fork:
`_persist_group` / `_load_group_from_firestore` / `_mark_revoked_in_firestore`
writing to an `anon_groups` collection; `get_group()` does in-memory cache hit →
Firestore fallback → cache rehydrate; `delete_group()` writes a `revoked` flag.
LOCAL_MODE's `InMemoryFirestoreClient` makes the whole layer a no-op round trip,
so the local story is unchanged.

### #17 — Resolve the dead mount

Decide, then act. Recommended: **delete the plumbing** (Dockerfile `ENV`, both
cloudbuild `--add-volume` / `--add-volume-mount` pairs, and the bootstrap bucket
creation). Runtime-swappable skills are a legitimate feature but not one we have,
and a mounted-unread volume is a standing invitation to assume it works. If we
would rather keep the capability, the alternative is to have the seed step publish
template `SKILL.md` files into the bucket and read them at runtime — but that is a
feature, and should get its own doc rather than being smuggled in here.

### #18 — Stop silently dropping build args

Replace the hand-maintained `ARG` allowlist with a generated one, and fail loudly
on drift:

- Keep a single source of truth for the `NEXT_PUBLIC_*` set.
- Add a build-time assertion: any `--build-arg NEXT_PUBLIC_*` passed but not
  declared causes the build to **fail**, not to proceed with `undefined`.

The deeper fix the AIPLA log suggests — move auth-mode into `branding.ts` so it
needs no build arg at all — is right for that specific var and is folded into the
work, but the generic drop-detection is what stops the next occurrence.

### #42 — Derive the guard, and make it fail loud

- Derive the expected project from `GOOGLE_CLOUD_PROJECT` / ADC, or an explicit
  `PLATFORM_DEFAULT_PROJECT` **with no brand default**.
- In non-LOCAL_MODE, a clearly-wrong project **refuses to boot** rather than
  logging `STARTUP WARNING` and continuing.
- A guard that warns when correct and is silent when wrong is worse than no
  guard; if deriving proves unreliable, delete it instead of keeping it warn-only.

### #36 — Gate deploy on CI

Adopt the AIPLA shape: blocking `ci-gate-backend` and `ci-gate-frontend` steps at
the top of `cloudbuild.yaml` running the *same* checks as CI (`ruff check`,
`ruff format --check`, `pytest -m "not slow"`; `quality:check:fast` + `vitest run`),
with every downstream step `waitFor`-ing both. An emergency `_SKIP_CI_GATE`
substitution is settable only on a manual `triggers run`, never by a push.

Scope is deliberately **correctness**, not the security-audit job — dependency
CVEs are governed separately and should not block a dev deploy.

## Implementation Plan

### Phase 1: Deploy safety (~0.75 day)
- #36 CI gate steps + `_SKIP_CI_GATE` escape hatch.
- #42 derived, fail-loud project guard.

### Phase 2: Build correctness (~0.5 day)
- #18 generated ARG list + drift assertion.

### Phase 3: Anonymous-group persistence (~1 day)
- #16 port + tests, including a scale-to-zero simulation (cold `get_group()` with an empty cache).

### Phase 4: Dead plumbing (~0.25 day)
- #17 removal (or, if we choose otherwise, a follow-up doc).

## Migration & Rollout

**Feature Flags:** None. #42's fail-loud boot is the one behaviour change that
could bite an existing env, so land it after the env-parity docs are confirmed
current for dev/test/prod.

**Rollback Plan:** Each phase is independently revertible. #36 is the only one
touching the deploy path; rollback is deleting two steps.

**Environment Variables:** `PLATFORM_DEFAULT_PROJECT` (no default), `_SKIP_CI_GATE` (manual-run only).

## Testing Strategy

- #16: unit tests over the Firestore round trip + a cold-cache path; LOCAL_MODE no-op assertion.
- #18: a build-arg drift case that must fail the build.
- #42: boot with a mismatched project asserts a hard exit; matching project asserts silence.
- #36: verified by observing a deliberately-red commit failing to produce a revision.

## Success Criteria

- [ ] A group code minted before a container restart still resolves after it, with `min-instances=0`
- [ ] An undeclared `NEXT_PUBLIC_*` build arg fails the build instead of being dropped
- [ ] Boot against a wrong project exits non-zero in non-LOCAL_MODE; a correct project logs nothing
- [ ] A commit with red CI produces no deployed revision
- [ ] `/gcs_config` is either read by code or entirely removed

## Related Documents

- AIPLA upstream feedback #16, #17, #18, #36, #42
- [template-fork-ergonomics.md](../template/template-fork-ergonomics.md)
- [deployment-models.md](../../ops/deployment-models.md)
