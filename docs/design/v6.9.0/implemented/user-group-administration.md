# User & Group (Identity/Tag) Administration

**Status**: Implemented
**Priority**: P1 (Medium)
**Estimated**: ~5 days
**Scope**: Fullstack
**Dependencies**: [administration-overview.md](administration-overview.md) (9.1 — shared admin-identity model), 6.0.0 auth-and-permissions ✅, 6.0.0 resource-access-control ✅, 6.3.0 client-tenant-management ✅, 6.6.0 fork-convergence ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-16

## Problem Statement

The platform's authorization model is correct and forge-proof — group tags ride
in the signed Firebase JWT `groupTags` claim (`backend/auth/firebase_auth.py:40-42,
73-83`), the 5-type evaluator reads them (`backend/auth/access_context.py:124-125`),
and Firestore rules mirror it via `hasAny()` (`firestore.rules:62-64`). **But there
is no administrative surface to operate identity.** This is the most acute gap in
the 9.x admin sprint: granting one user access to a tagged skill has *no path at
all* short of the Admin SDK.

**Current State (verified against code, 2026-07-14):**

1. **No per-user group-tag admin surface (the core gap).** Group tags are set only
   via `firebase_admin.auth.set_custom_user_claims` — and the only callers in the
   repo are test/verify scripts (`backend/scripts/verify_rules.py:101`,
   `backend/scripts/whoami_smoke.py:77`). No API, CLI, or UI grants/revokes a tag on
   a real user. The `aiplatform groups add-user/remove-user/list-user` CLI already
   exists and targets `/api/groups/{group}/members`, `/api/users/{uid}/groups`
   (`cli/aiplatform/commands/groups.py:31-60`) — but every command carries
   `# TODO: backend wiring pending`; a grep confirms **no server route exists** for
   any of them.
2. **"Admin" is defined four inconsistent ways** that can silently diverge:
   the `aitana-admin` tag (`backend/admin/auth.py:29`), the `one-admin` tag
   (`SKILL_ADMIN_TAG`, `backend/auth/access_context.py:34`), the SA allowlist
   `ADMIN_SEED_ALLOWED_SAS` (`backend/admin/auth.py:32-34`), and a **hardcoded
   `owner@yourcompany.com`** in `firestore.rules:25`. The rules-admin and the
   backend-admin have no shared source of truth.
3. **Derived group tags are domain-wide, all-or-nothing.** `clients/{domain}.derived_group_tags`
   (`backend/db/clients.py:119-131`) is unioned into the claim per request
   (`backend/auth/firebase_auth.py:125-146`) — the *only* working self-serve tag
   path today, but it grants the tag to *every* user of a domain. There is no way to
   grant a tag to one user, or to except one user within a domain.
4. **No "who has what" visibility.** `GET /api/auth/whoami` is self-only — it echoes
   the *caller's* own claim (`backend/auth/routes.py:17-24`). The stubbed
   `aiplatform access check --as-email` (`cli/aiplatform/commands/access.py:31-54`)
   targets `POST /api/access/check`, which does not exist. No admin can answer "what
   does this user see?" or "who holds tag X?".
5. **Group tags aren't first-class.** A tag is just a string — no registry of valid
   tags, no label, no description of what a tag grants, no membership record.
   (Note: `firestore.rules:202` `/tags/{tagId}` is a *resource-tag vocabulary*, NOT
   identity/group tags — do not conflate the two.)
6. **Two disconnected permission planes.** Skill *access* uses group tags
   (`access_context.py:124`); tool *invocation* uses `tool_permissions` keyed by
   email/domain/wildcard (`backend/auth/permissions.py:14-24`), enforced at
   `backend/adk/callbacks.py:101-108`. A user can reach a skill but be tool-denied
   inside it. `tool_permissions` is seeded only by a dev script
   (`backend/scripts/seed_tool_permissions.py`) — no admin API.
7. **Custom-claim staleness.** Changing `groupTags` takes effect only after the
   client's ID token refreshes (~1h); no force-refresh or revocation is wired, so a
   grant/revoke is silently delayed and a revoke isn't enforceable immediately.
8. **No audit trail.** Every admin mutation emits only `log.info`
   (e.g. `backend/admin/clients.py:103,145,166`) — no queryable who/what/when for
   access, tag, or permission changes.

**Impact:** Onboarding a user onto a restricted skill, or removing their access,
requires an engineer with the Admin SDK. It is error-prone (free-string tag names,
no validation), invisible (no audit), and un-delegatable (no tenant-scoped admin).
Untenable as customer count grows.

## Goals

**Primary Goal:** A deny-by-default `/api/admin/*` identity surface that grants/revokes
group tags per-user, makes group tags first-class (a registry), answers "who has
what" (effective-access lookup), co-manages the tool-permission plane, propagates
claim changes, and audits every mutation — wiring the CLI stubs that already exist.

**Success Metrics:**
- Zero identity operations that *require* a hand-run `set_custom_user_claims` script
  for the common cases (grant a tag, revoke a tag, list a user's tags, list a tag's
  holders).
- One admin-identity model: the four divergent admin definitions and the hardcoded
  `firestore.rules` email retired behind `groupTags.hasAny(['aitana-admin'])`.
- 100% of identity mutations (grant/revoke/tool-permission) written to an append-only
  audit record inside GCP.
- A revoked tag is enforceable in ≤ the token TTL, with an explicit force-refresh path.

**Non-Goals:**
- A new authz model — the 5-type `AccessControl` + tags stays; we add *management*.
- Self-service end-user account management (this is operator/tenant-admin tooling).
- Replacing `derived_group_tags` (domain-wide grants stay; per-user grants are the
  *exception* layer on top).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Admin surface, off the chat latency path. Effective-access is a synchronous admin lookup, not on the hot path. |
| 2 | EARNED TRUST | +1 | Effective-access lookup + audit trail make access decisions inspectable and provenanced (JWT tags ∪ derived ∪ tool_permissions, with *why*). |
| 3 | SKILLS, NOT FEATURES | 0 | Operator/identity tooling — orthogonal to skill authoring; adds no user-facing abstraction beyond the existing tag string. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model involvement. |
| 5 | GRACEFUL DEGRADATION | +1 | Explicit token-staleness handling (force-refresh + degraded notice), tag-name validation against the registry, deny-by-default when the registry/claim is absent. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Retires hand-run `set_custom_user_claims` scripts behind the standard authed admin API; keeps the signed-JWT claims mechanism (no bespoke authz store). |
| 7 | API FIRST | +1 | Whole doc is API-first — one admin API; the stubbed `aiplatform groups`/`access` CLI and the `/admin` UI are thin clients over it. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Append-only audit record for every grant/revoke/permission change, inside GCP; effective-access answers "who has what" without instrumenting after the fact. |
| 9 | SECURE BY CONSTRUCTION | +1 | Unifies the 4 admin definitions behind claims, retires the hardcoded `firestore.rules` email, deny-by-default, tenant-admin scoped to own domain, no content egress. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All logic (effective-access union, tag validation, propagation) in the backend; UI/CLI render responses. |
| | **Net Score** | **+7** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scored -1).

## Design

### Overview

Wire the already-designed-but-unimplemented identity endpoints behind the unified
admin guard from 9.1, make group tags first-class via a `group_tags` registry, and
add per-user grant/revoke that writes the JWT claim + audits + signals propagation.
Effective-access answers "who has what" by unioning the three planes that actually
decide access.

### Backend Changes

**1. Unified admin identity (`backend/auth/admin_roles.py`, from 9.1).** One guard
replaces the four definitions:
- `aitana-admin` — platform super-admin (all tenants/users/tags).
- `tenant-admin:{domain}` — **new tag shape**: manage own domain's users/tags only.
- `one-admin` — folded into `aitana-admin` + skill ownership (see 9.1 OQ2).
- **`firestore.rules:25` `isAdmin()` → `request.auth.token.groupTags.hasAny(['aitana-admin'])`**
  so the rules-admin can no longer diverge from the backend guard. This is the single
  highest-value hardening in this doc.

**2. Group-tag registry (`group_tags` Firestore collection).** Makes a tag more than
a string: `{ id, label, description, grants, tenant_scope? , created_by, created_at }`.
`grants` documents *what the tag unlocks* (which skills/tools reference it). Grant/revoke
validates the tag id against this registry (deny unknown tags — fixes the free-string
hazard). Distinct collection from `firestore.rules:202` `/tags` (resource vocabulary).

**3. Per-user grant/revoke (`backend/admin/identity.py`).** Thin wrapper over
`set_custom_user_claims`: read current `groupTags`, add/remove one tag, write back,
audit, and signal propagation (below). This is the *exception* layer above
`derived_group_tags` (domain-wide grants stay in `clients/{domain}`).

**4. Effective-access lookup.** Computes a user's true access as
`JWT groupTags ∪ derived_group_tags(domain) ∪ tool_permissions(email/domain/wildcard)`
— reusing `resolve_derived_group_tags` (`db/clients.py:119`) and the
`permissions.can_use_tool` lookup order (`permissions.py:92-130`) — annotated with
*why* each tag is present (direct claim vs domain-derived). Powers
`GET /api/admin/access/check` and the reverse "holders of tag X" query.

**5. Tool-permission admin.** CRUD over the `tool_permissions` collection
(`permissions.py:14-24` shape) so the second plane is co-managed, not seed-script-only.
Grant/revoke calls `permissions.clear_cache()` for the affected key (60s TTL cache,
`permissions.py:23`).

**6. Claim propagation.** After a grant/revoke, return a `propagation` block
(`{ effective: "on_next_refresh" | "immediate", tokenTtlSeconds }`) and expose
`POST /api/admin/users/{uid}/refresh-claims` which calls
`firebase_admin.auth.revoke_refresh_tokens(uid)` so a revoke is enforceable within the
verification window rather than silently waiting ~1h. The UI/CLI surface this state
(NEVER-SILENT: the operator sees "takes effect at next sign-in / forced now").

**7. Audit (from 9.1).** Every mutation appends `{ actor_uid, action, target_uid,
tag/tool, before, after, ts }` to an append-only `admin_audit` collection (inside GCP).

### Frontend Changes

**New Components (behind `NEXT_PUBLIC_ENABLE_ADMIN` flag, per 9.1):**
- `src/app/admin/users/` — user lookup → effective-access panel (tags with provenance
  badges: *direct* / *domain-derived* / *tool-perm*), grant/revoke controls with an
  explicit propagation notice.
- `src/app/admin/groups/` — tag registry table (label, what-it-grants, holder count)
  and a "holders of this tag" list.

All logic stays backend (THIN CLIENT); the UI renders admin-API responses. Grant/revoke
gives immediate pending state + terminal success/error (NEVER-SILENT: gate 403s and
unknown-tag rejections render).

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | /api/admin/users/{uid}/groups | List a user's group tags (direct claim). Wires `groups list-user`. | No |
| POST | /api/admin/users/{uid}/groups | Grant a tag `{ "tag": "..." }` (validated vs registry). | No |
| DELETE | /api/admin/users/{uid}/groups/{tag} | Revoke a tag. | No |
| GET | /api/admin/groups/{tag}/members | List holders of a tag. Wires `groups` reverse lookup. | No |
| POST | /api/admin/users/{uid}/refresh-claims | Revoke refresh tokens (force propagation). | No |
| POST | /api/admin/access/check | Effective-access dry-run `{ uid|email, skillId?, toolName? }` → `{ allowed, reason, tags[] }`. Wires `access check`. | No |
| GET | /api/admin/group-tags | List the tag registry. | No |
| PUT | /api/admin/group-tags/{tag} | Upsert a registry entry (label/description/grants). | No |
| GET/PUT/DELETE | /api/admin/tool-permissions/{docId} | CRUD `tool_permissions`. Replaces seed script. | No |

**CLI reconciliation:** the CLI stubs currently target un-prefixed `/api/groups/*`,
`/api/users/{uid}/groups`, `/api/access/check` (`groups.py:38,49,59`; `access.py:53`).
Repoint them to `/api/admin/*` — this also avoids confusion with the **unrelated**
`/api/auth/group/*` anonymous-workshop group-ID flow (`backend/auth/group_routes.py`),
which is a different concept (shared workshop sessions, not identity tags).

### Architecture Diagram

```
[Admin UI / aiplatform CLI] → [/api/proxy] → [/api/admin/* guarded by aitana-admin | tenant-admin:{domain}]
                                                        ↓
                          ┌─────────────────────────────┼──────────────────────────────┐
                    grant/revoke tag              effective-access               tool-perm CRUD
                          ↓                              ↓                              ↓
              set_custom_user_claims         JWT tags ∪ derived_group_tags ∪    tool_permissions
              + revoke_refresh_tokens          tool_permissions (annotated)      (+ clear_cache)
                          ↓                              ↓                              ↓
                    group_tags registry (validate)   →   admin_audit (append-only, inside GCP)
```

## Implementation Plan

### Phase 1: Registry + guard reuse (~1d)
- [ ] `group_tags` collection + Pydantic model; `GET/PUT /api/admin/group-tags` (~120 LOC).
- [ ] Reuse the 9.1 unified admin guard (`admin_roles.py`); scope tenant-admin to own domain (~40 LOC).

### Phase 2: Grant/revoke + propagation (~1.5d)
- [ ] `backend/admin/identity.py` grant/revoke wrapping `set_custom_user_claims`, validating vs registry, auditing (~160 LOC).
- [ ] `GET/POST/DELETE /api/admin/users/{uid}/groups`, `GET /api/admin/groups/{tag}/members` (~140 LOC).
- [ ] `POST .../refresh-claims` (`revoke_refresh_tokens`) + propagation block (~40 LOC).

### Phase 3: Effective-access + tool-perms (~1d)
- [ ] Effective-access union (tags ∪ derived ∪ tool_permissions) + `POST /api/admin/access/check` (~120 LOC).
- [ ] `tool_permissions` CRUD + `clear_cache` on write (~90 LOC).
- [ ] Repoint the 3 CLI stubs to `/api/admin/*`; drop the TODO banners (~30 LOC).

### Phase 4: UI + firestore.rules (~1.5d)
- [ ] `/admin/users` + `/admin/groups` panels behind `NEXT_PUBLIC_ENABLE_ADMIN` (~260 LOC).
- [ ] `firestore.rules` `isAdmin()` → claims check, behind the 9.1 test gate.

## Migration & Rollout

**Database:** additive `group_tags` + `admin_audit` collections; no backfill (a tag
absent from the registry is treated as unknown → reject new grants, but existing
claims keep working until the registry is populated — seed it from tags referenced in
`skills` + `derived_group_tags`).

**Feature flags:** `/admin` UI behind `NEXT_PUBLIC_ENABLE_ADMIN` (mirrors Skill
Studio's `NEXT_PUBLIC_ENABLE_SKILL_STUDIO`). Endpoints ship deny-by-default so they're
inert without an admin caller.

**The one breaking change** is `firestore.rules:25` (hardcoded email → claims). Ship
behind the 9.1 test that asserts the claims-driven rule admits `aitana-admin` and
denies a non-admin *before* removing the email. Rollback = revert the rules file (rules
deploy is independent of the app).

**Environment Variables:** none new (reuses `ADMIN_SEED_ALLOWED_SAS`; `aitana-admin`
lives in claims).

## Testing Strategy

### Backend Tests (pytest)
- [ ] Grant then `whoami`-equivalent reflects the tag; revoke removes it.
- [ ] Unknown-tag grant → 422 (registry validation); non-admin caller → 403; tenant-admin
      cross-domain grant → 403.
- [ ] Effective-access union: user with a direct tag + domain-derived tag + tool-perm
      returns all three with correct provenance; deny path returns a reason.
- [ ] `revoke_refresh_tokens` called on force-refresh; audit row written per mutation.
- [ ] `firestore.rules` emulator: `aitana-admin` claim passes `isAdmin()`, others fail.

### Frontend Tests (Vitest + RTL)
- [ ] Grant/revoke shows pending → terminal state; 403 and unknown-tag errors render
      (NEVER-SILENT); propagation notice shown.

### Manual
- [ ] Real ID token with `aitana-admin` grants a tag to a `.test` user; token refresh
      then reaches a previously-403 tagged skill (verify end-to-end, not just unit).

## Security Considerations

- **Deny-by-default:** every `/api/admin/*` route behind the unified admin guard;
  tenant-admins scoped to their own `{domain}` (no cross-tenant reach) — the highest
  privilege-escalation risk here is a tenant-admin granting a platform-wide tag, so
  registry entries carry `tenant_scope` and grants are checked against it.
- **The firestore.rules hardcoded-email retirement** removes a single-point admin
  identity that can't be rotated or revoked; claims are the forge-proof mechanism
  (`firebase_auth.py:40`).
- **No content egress:** this surface grants/inspects *access*, never streams
  customer content; audit + registry stay inside the GCP project edge (CLAUDE.md
  security hard rule; Axiom #9 boundary).
- **Input validation:** tag ids validated against the registry; `uid`/`email`
  path/body params are looked up, never interpolated into a Firestore path unchecked.

## Success Criteria

- [ ] Grant/revoke a tag, list a user's tags, and list a tag's holders — all via
      `aiplatform groups …` and the `/admin` UI, with **no** hand-run `set_custom_user_claims`.
- [ ] `aiplatform access check --as-email` returns a real allow/deny + reason
      (effective-access union), not a 404.
- [ ] `tool_permissions` manageable via API (seed script retired for ops use).
- [ ] `firestore.rules` `isAdmin()` is claims-driven; the hardcoded email is gone.
- [ ] Every mutation audited; a forced refresh makes a revoke enforceable in ≤ TTL.
- [ ] Backend + frontend tests pass; lint/typecheck clean.

## Open Questions

- OQ1: `tenant-admin:{domain}` as a claim shape vs a `group_memberships` collection?
  Per-tenant claims reintroduce per-user claim writes; a membership record avoids
  claim bloat but adds a Firestore read to the guard. (Inherits 9.1 OQ1.)
- OQ2: Should revoke *always* force `revoke_refresh_tokens` (immediate enforcement,
  but logs the user out of all sessions), or only offer it? Lean: offer it, default
  to it for revokes of security-sensitive tags.
- OQ3: Do we migrate `derived_group_tags` exceptions (grant-to-domain-except-user)
  now, or is per-user grant + a future deny-list enough? Lean: per-user grant only for
  9.3; add per-user deny if a real case appears.

## Related Documents

- [administration-overview.md](administration-overview.md) (9.1 — shared admin model)
- [skill-administration.md](skill-administration.md) (9.2), [domain-tenant-administration.md](domain-tenant-administration.md) (9.4)
- [auth-and-permissions.md](../v6.0.0/implemented/auth-and-permissions.md), [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md), [client-tenant-management.md](../v6.3.0/implemented/client-tenant-management.md)

---

## Implementation Report

**Completed**: 2026-07-16
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
