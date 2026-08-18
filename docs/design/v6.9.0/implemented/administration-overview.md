# Administration — Overview (Skills · Users/Groups · Domains)

**Status**: Implemented
**Priority**: P1
**Estimated**: Umbrella (sets the shared model for 9.2/9.3/9.4)
**Scope**: Fullstack
**Dependencies**: 6.0.0 auth-and-permissions ✅, 6.0.0 resource-access-control ✅, 6.3.0 client-tenant-management ✅, 6.6.0 fork-convergence ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-16

## Problem Statement

The platform's **access model is rich and correct** — a 5-type `AccessControl`
(public / owner-private / domain / specific / tagged), group tags via signed JWT
claims, domain-derived tags, per-tenant `enabled_skills`, and an email/domain
`tool_permissions` plane. But there is **almost no administrative surface to
operate it.** Every real admin action is a hand-run script, a raw Firestore
edit, a redeploy, or a CLI incantation — as this very session demonstrated
(seeding a skill required a deploy; the ONE tenant landing required a Firestore
REST PATCH; granting a group tag has no path at all short of the Admin SDK).

**Current State (from the 2026-07-14 admin audit):**
- **No per-user group-tag management.** Granting/revoking a tag on one user
  requires `firebase_admin.auth.set_custom_user_claims(...)` by hand — no API,
  no CLI, no UI (`backend/auth/firebase_auth.py:40`; only test scripts call it).
  The `aiplatform groups add-user/…` CLI exists but targets `/api/groups/*`
  endpoints that **don't exist** (`cli/aiplatform/commands/groups.py` TODOs).
- **"Admin" is defined four inconsistent ways** that can silently diverge:
  `aitana-admin` tag (`backend/admin/auth.py:29`), `one-admin` tag
  (`backend/auth/access_context.py:34`), an SA allowlist
  (`ADMIN_SEED_ALLOWED_SAS`), and a **hardcoded `owner@yourcompany.com`** in
  `firestore.rules:25`.
- **No tenant-admin UI** — `clients/{domain}` CRUD is CLI/REST only
  (`backend/admin/clients.py`); onboarding is two disjoint manual tracks
  (Firestore doc here + bucket/IAM in a separate terraform repo) with no
  orchestration or validation.
- **Skill access isn't editable from Skill Studio** — `accessControl`, `slug`,
  `tags`, `protocols`, `shell` are omitted from the save body
  (`frontend/src/app/skills/studio/[skillId]/page.tsx:1051`); they require
  curl/PUT/template-frontmatter.
- **No "who has what" visibility and no audit trail** — `whoami` is self-only;
  admin mutations emit only `log.info`.

**Impact:** Onboarding a customer, granting a user access, publishing a skill,
or changing a tenant's landing all require an engineer with gcloud + repo access
running scripts. This blocks non-engineer operation, is error-prone (unvalidated
free-string references), and has no audit story — untenable as the customer
count grows.

## Goals

**Primary Goal:** A coherent, API-first **administration layer** — one admin
identity model, one set of `/api/admin/*` endpoints, a thin admin UI, and an
audit trail — so skills, users/groups, and tenants are managed in-product
instead of by hand-run scripts and raw Firestore edits.

**Success Metrics:**
- Zero admin operations that *require* `set_custom_user_claims`/Firestore-REST/redeploy by hand for the common cases (grant a tag, onboard a tenant, publish a skill, set a landing).
- One admin-role model; the hardcoded-email rule and the 4 divergent admin definitions retired.
- 100% of admin mutations audited (who/what/when).

**Non-Goals:**
- A full RBAC/policy engine (the 5-type `AccessControl` + tags stays the model; we add *management*, not a new authz model).
- Self-service end-user account management (this is operator/admin tooling).
- Replacing terraform for GCP resource provisioning (we *orchestrate/validate* against it, not own it).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Admin surface, off the chat latency path. |
| 2 | EARNED TRUST | +1 | Audit trail + "who has what" visibility make access decisions inspectable. |
| 3 | SKILLS, NOT FEATURES | +1 | Makes skills genuinely operable by non-engineers (publish/share/access without code). |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal. |
| 5 | GRACEFUL DEGRADATION | +1 | Referential validation (skill/bucket refs) prevents silent broken-landing/hidden-skill states. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Consolidates ad-hoc scripts behind the standard authed API; keeps the Agent-Skills + access schema. |
| 7 | API FIRST | +1 | The whole doc is API-first — one admin API, UI + CLI as thin clients. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Admin audit log inside GCP; every mutation traced. |
| 9 | SECURE BY CONSTRUCTION | +1 | Unifies admin identity behind claims (retires the hardcoded-email + SA-allowlist drift), deny-by-default; keeps content inside the edge. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Admin logic in the backend; the UI renders admin API responses. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### The shared model (this umbrella's job; 9.2–9.4 build on it)

**1. One admin-identity model.** Retire the four divergent definitions. A single
role model driven by **group-tag claims** (the existing, signed, forge-proof
mechanism):
- `aitana-admin` — **platform super-admin**: all tenants, all skills, all users, seeding.
- `tenant-admin:{domain}` (new tag shape) — **tenant admin**: manage own tenant's
  users/groups, `enabled_skills`, `default_skill`, tenant-owned skills.
- `one-admin` — folded in as a scoped **skill-admin** capability (or retired in favor of `aitana-admin` + skill ownership).
- **Firestore rules driven from claims**, not a hardcoded email
  (`firestore.rules:25` → `request.auth.token.groupTags.hasAny(['aitana-admin'])`),
  so the rules-admin and backend-admin can no longer diverge.

**2. One admin API surface** (`/api/admin/*`), the fat protocol:
- Skills: publish / set access / manage platform skills (9.2).
- Users & groups: grant/revoke tags per-user, a tag registry, effective-access
  lookup, tool-permission management — **implement the stubbed `/api/groups/*`,
  `/api/users/{uid}/groups`, `/api/access/check`** the CLI already targets (9.3).
- Tenants: `clients/{domain}` CRUD + onboarding orchestration + referential
  validation (9.4).

**3. Thin admin UI + CLI as clients.** A gated `/admin` area in the frontend and
the existing `aiplatform` CLI both render/drive the same admin API — no admin
business logic in either client.

**4. First-class group tags + audit.** A `group_tags` registry (id, label,
description of what it grants, membership) so "what does tag X grant / who holds
it" is answerable; every admin mutation writes an append-only audit record.

### The four focused docs

| Doc | Scope |
|---|---|
| [skill-administration.md](skill-administration.md) (9.2) | Skill CRUD/publish/access from the UI (close the Studio `accessControl`/slug/tags gap), platform-skill vs template drift, durable skill creation without a redeploy, marketplace `featured`/`usage_count`. |
| [user-group-administration.md](user-group-administration.md) (9.3) | Per-user tag grant/revoke, the tag registry, wiring the stubbed group/user/access endpoints, effective-access visibility, tool-permission admin, token-refresh propagation, unify the 4 admin definitions. |
| [domain-tenant-administration.md](domain-tenant-administration.md) (9.4) | Tenant CRUD UI, onboarding orchestration (doc + bucket + IAM + validation), unmapped-domain fallback fix, per-tenant caching/branding considerations, audit. |
| [analytics-and-reporting.md](analytics-and-reporting.md) (9.5) | Turn the natively-captured interaction data (OTel→Cloud Trace/Logging/BigQuery, full content capture, session mirror) into product: a chat-history/trace viewer, usage/cost analytics, and a report-template engine (the AIPLA teacher-report generalisation) — all access-scoped, inside the GCP edge. |

### Cross-cutting: the confidential-content boundary

Admin tooling touches access decisions for restricted customer content. Per
CLAUDE.md's security hard rule, the admin surface **grants/inspects** access but
never exposes content; all admin data stays inside the GCP project edge; the
audit log lives in Cloud Logging/BigQuery (internal).

## Implementation Plan (umbrella-level sequencing)

### Phase 1: Admin identity + audit foundation (~2d)
- [ ] Unify the admin role model behind claims; retire the hardcoded-email rule + reconcile the 4 definitions (a shared `admin_roles.py` guard).
- [ ] Append-only admin audit record + write helper used by every admin mutation.

### Phase 2–4: the three focused docs (9.2 / 9.3 / 9.4)
Each builds on Phase 1. 9.3 (user/group) is the most acute gap (no path at all today) — likely first.

### Phase 5: Thin admin UI (~cross-doc)
- [ ] Gated `/admin` frontend rendering the admin API (skills / users-groups / tenants tabs).

## Migration & Rollout

- **Admin identity:** additive tags; the hardcoded-rules-email change is the one breaking bit — ship behind a test that the claims-driven rule admits `aitana-admin` and denies others before removing the email.
- **Feature-flagged UI:** the `/admin` area behind an env flag (like Skill Studio's `NEXT_PUBLIC_ENABLE_SKILL_STUDIO`).
- **Backwards-compatible API:** existing `/api/admin/clients` + `/api/admin/seed-*` stay; new endpoints are additive.

## Security Considerations

- Every `/api/admin/*` route deny-by-default behind the unified admin guard; tenant-admins scoped to their own domain (no cross-tenant reach).
- The `firestore.rules` admin predicate moves from a hardcoded email to a claims check — the single highest-value hardening here.
- Audit every mutation (grant/revoke/publish/onboard) with actor + target + before/after, inside GCP.

## Success Criteria

- [ ] One admin guard/role model; hardcoded-email rule gone; `aitana-admin` + `tenant-admin:{domain}` the only admin notions.
- [ ] The three focused docs' acceptance criteria met.
- [ ] Every admin mutation audited; effective-access lookup answers "who has what."
- [ ] The common admin ops (grant tag, onboard tenant, publish skill, set landing) need no hand-run script/Firestore-edit/redeploy.

## Open Questions

- OQ1: Tenant-admin as a claim shape (`tenant-admin:{domain}`) vs a Firestore membership record? **DECIDED (2026-07-16, M1): claim shape `tenant-admin:{domain}`** — reuses the existing signed/forge-proof `groupTags` mechanism, needs no extra read on the auth path, and matches ONE's near-term scale (a handful of tenants). The per-user-claim-write cost is acceptable at this scale; revisit a membership collection only if per-tenant claim churn or claim-size limits bite. Implemented in `backend/auth/admin_roles.py` (`is_tenant_admin` / `tenant_admin_domains`).
- OQ2: Do we keep `one-admin` as a distinct scoped role or fold it entirely into `aitana-admin` + ownership? (Lean: fold, unless a real "skill admin who isn't a platform admin" persona exists.)
- OQ3: Admin UI scope for v6.9.0 — full CRUD UI, or API + CLI first with UI as a fast-follow? (Lean: API+CLI first per API-FIRST; minimal UI for the highest-friction ops.)

## Related Documents

- [skill-administration.md](skill-administration.md), [user-group-administration.md](user-group-administration.md), [domain-tenant-administration.md](domain-tenant-administration.md)
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
