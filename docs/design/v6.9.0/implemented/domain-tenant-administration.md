# Domain / Tenant (Client-Org) Administration

**Status**: Implemented
**Priority**: P1
**Estimated**: 4 days
**Scope**: Fullstack
**Dependencies**: 6.3.0 client-tenant-management ✅, 6.4.0 one-app-fork-convergence ✅, 6.5.0 auth-landing ✅, [administration-overview.md](administration-overview.md) (9.1 shared admin identity + audit foundation)
**Created**: 2026-07-14
**Last Updated**: 2026-07-16

## Problem Statement

The **fork-by-config** multi-tenancy surface — one deployment serving multiple
customer orgs keyed on email domain — is real and load-bearing: `clients/{domain}`
drives per-tenant document buckets, skill visibility, landing skill, and
domain-derived group tags (`backend/db/clients.py:19-42`). But there is **no
administrative surface to operate it safely**, no validation of the references it
stores, and one fallback that risks cross-tenant data commingling.

**Current State (verified 2026-07-14):**

- **No frontend tenant-admin surface.** `clients/{domain}` CRUD is CLI/REST only:
  `PUT /api/admin/clients/{domain}` gated on `aitana-admin`
  (`backend/admin/clients.py:28-31,126-152`) and `aiplatform client set`
  (`cli/aiplatform/commands/client.py:52-124`). No in-product tenant roster or
  config visibility. **This session's ONE cutover was a hand-run Firestore REST
  PATCH** — exactly the pain this closes.
- **No onboarding automation.** Onboarding is **two disjoint manual tracks** with
  nothing linking them: (a) the `clients/{domain}` doc (here); (b) the GCS bucket
  + SA IAM, owned by a **separate terraform repo** — `scripts/bootstrap-gcp-project.sh:54`
  ("Runtime buckets are NOT created here — terraform owns them"), and the runtime
  SA `platform@{project}` must be granted read **per-bucket explicitly**
  (`tools/org_documents.py:41-45`: "the SA is granted explicitly per-bucket"). A
  `documents_bucket` string can name a bucket the SA cannot read, unvalidated.
- **No referential validation.** `enabled_skills`, `default_skill`, and
  `documents_bucket` are free strings written straight through
  (`admin/clients.py:136-144`) — unchecked against existing skills, buckets, or
  SA-reachability. A typo in `default_skill` silently breaks landing
  (`db/clients.py:98-116`); a typo in `enabled_skills` silently hides all skills
  from real tenant users (`skills/routes.py:202-206`).
- **Unmapped-domain fallback is a shared bucket.** `resolve_documents_bucket`
  falls back to a hardcoded `"aitana-documents-bucket"` (`db/clients.py:79`) and
  `DOCUMENTS_BUCKET` is **not set** in `cloudbuild.yaml` (only
  `A2A_AGENT_DOCUMENTS_BUCKET`, `cloudbuild.yaml:216`) — so **every unmapped domain
  writes to the same fallback bucket**, a cross-tenant commingling risk.
- **Three bucket concepts, undocumented** (disambiguated in Design below):
  `clients/{domain}.documents_bucket`, `buckets/{id}` `BucketConfig`
  (`db/models/buckets.py:56-70`), and `A2A_AGENT_DOCUMENTS_BUCKET`
  (`tools/org_documents.py:35`).
- **No audit trail; no referential caching.** Mutations emit only `log.info`
  (`admin/clients.py:103,145,166`); `get_client_sync` is uncached and
  `clients/{domain}` is read **≥2× per `/api/skills` request** — in
  `_apply_derived_group_tags` (`auth/firebase_auth.py:125-146`) and
  `resolve_enabled_skills` (`skills/routes.py:202`), plus a third path in
  `protocols/sessions_route.py:402-406`.

**Impact:** Onboarding a customer, or changing a tenant's landing/skills/bucket,
requires an engineer with gcloud + repo access editing Firestore by hand across
two repos, with no validation and no audit. It is error-prone (silent
broken-landing / hidden-skills), has a latent cross-tenant leak in the fallback,
and does not scale past a handful of hand-tended tenants.

## Goals

**Primary Goal:** Make a tenant a **first-class, validated, audited, in-product
object** — onboard, inspect, and edit `clients/{domain}` (doc + bucket ref + IAM
reachability + referential validation) through one admin API, with a thin UI and
the existing CLI as clients — so no common tenant op needs a hand-run Firestore
edit across two repos.

**Success Metrics:**
- Onboarding a tenant (doc + bucket ref + validated IAM + landing) is **one
  orchestrated call**, zero manual Firestore PATCH.
- 100% of `enabled_skills` / `default_skill` / `documents_bucket` writes
  validated against existing skills / buckets / SA-reachability before commit.
- Zero unmapped domains writing to a shared fallback bucket (fail-closed).
- 100% of tenant mutations audited (actor + domain + before/after), inside GCP.

**Non-Goals:**
- Owning GCP resource provisioning — terraform still creates buckets + SA IAM; we
  **validate and orchestrate against** it, never replace it.
- Per-tenant runtime **branding** from a single deployment (see Open Questions —
  today's model is one deployment per customer; this doc documents the constraint,
  it does not lift it).
- A new authz model — the 5-type `AccessControl` + tags stays; we add management.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Durable-Firestore cache for `get_client_sync` removes the 2–3× `clients/{domain}` re-read on the authenticated `/api/skills` + landing path. |
| 2 | EARNED TRUST | +1 | Tenant roster + audit trail make onboarding decisions and per-tenant config inspectable; validation surfaces bad refs instead of hiding them. |
| 3 | SKILLS, NOT FEATURES | +1 | Tenant onboarding + `enabled_skills`/`default_skill` become operable by non-engineers; validation stops a typo from hiding every skill. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal — no model routing. |
| 5 | GRACEFUL DEGRADATION | +1 | Referential validation + fail-closed unmapped-domain fallback replace silent broken-landing / hidden-skill / cross-tenant-commingling states with explicit ones. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Consolidates the hand-run Firestore PATCH behind the standard authed `/api/admin/*` surface; keeps the `ClientConfig` + `AccessControl` schema. |
| 7 | API FIRST | +1 | Onboarding orchestration + validation live in the API; UI and `aiplatform client` are thin clients over the same contract. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Append-only tenant-mutation audit inside GCP (Cloud Logging/Firestore), actor + before/after. |
| 9 | SECURE BY CONSTRUCTION | +1 | Fail-closed fallback removes the cross-tenant leak; IAM-reachability check validates the SA can read; confidential content stays behind the existing auth-gated proxy. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | All validation/orchestration in the backend; the admin UI renders API responses only. |
| | **Net Score** | **+9** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scored -1).

## Design

### Overview

Add three things on top of the existing `clients/{domain}` model: (1) a
**validated, orchestrated** onboarding/update path (`POST /api/admin/tenants`)
that writes the doc, checks the bucket reference + SA read-reachability, and
validates skill-slug references before commit; (2) a **fail-closed**
unmapped-domain resolution so no tenant ever writes to a shared fallback bucket;
(3) a thin **`/admin/tenants`** UI tab + audit + a durable cache — reusing the
proven Firestore-cache pattern from `tools/map_ppa_obligations.py:115-168`.

### The three bucket concepts (disambiguated — normative)

| Concept | Where | Purpose | Access model |
|---|---|---|---|
| `clients/{domain}.documents_bucket` | `db/clients.py:23` | Per-tenant **user uploads** (`users/{uid}/docs/...`) | Path-isolated by uid; resolved from email domain |
| `buckets/{id}` `BucketConfig.gcsBucket` | `db/models/buckets.py:56-70` | Shared **reference** corpus (e.g. `multivac-acme-energy-bucket`) | Own 5-type `accessControl` (e.g. `tagged: [ONE]`) |
| `A2A_AGENT_DOCUMENTS_BUCKET` | `tools/org_documents.py:35` | **Per-deploy** org bucket the agent lists/reads | Deploy env var; SA granted per-bucket |

The onboarding orchestrator touches only the **first**. The second is managed by
9.2/bucket admin. The third is deploy config. Design keeps them distinct.

### How a tenant experience composes (explicit)

A signed-in user's experience is the composition of three tenant knobs, applied
in this order on an authenticated request:

1. `derived_group_tags` (`db/clients.py:119-131`) union into `user.group_tags`
   at auth time (`firebase_auth.py:125-146`) → unlocks `tagged` skills/buckets
   for the whole domain without per-user `set_custom_user_claims`.
2. `enabled_skills` narrows the visible skill list **after** `can_access_skill`
   (`skills/routes.py:202-206`) — a strict narrowing, never a widen.
3. `default_skill` (else `enabled_skills[0]`, else marketplace) picks the landing
   (`db/clients.py:98-116`).

**Admin-bypass nuance (preserve):** `_SKILL_ADMIN_TAGS = {"one-admin",
"aitana-admin"}` (`skills/routes.py:33`) skip the `enabled_skills` narrowing so
admins see WIP/demo skills; a real ONE user sees **only** the whitelist. Any UI
must show both the tenant whitelist *and* flag that admins bypass it.

### Frontend Changes

- `src/app/admin/tenants/page.tsx` — tenant roster (from `GET /api/admin/clients`),
  gated behind the shared `/admin` env flag (9.1).
- `src/components/admin/TenantEditor.tsx` — edit `display_name`, `documents_bucket`,
  `enabled_skills` (multi-select **against the real skill list**, not free text),
  `default_skill`, `derived_group_tags`; renders API validation errors inline
  (NEVER-SILENT: bad-ref + IAM-unreachable are visible).
- `src/components/admin/TenantOnboardWizard.tsx` — new-tenant flow calling
  `POST /api/admin/tenants`, rendering each validation step's verdict.

No business logic client-side (THIN-CLIENT): slug/bucket validity is decided by
the backend; the UI renders verdicts.

### Backend Changes

- `backend/admin/tenants.py` (new) — orchestration + validation wrapping the
  existing `admin/clients.py` CRUD:
  - `enabled_skills`/`default_skill` → each slug resolves via
    `skill_config.find_by_slug` (or the platform set); unknown → 422.
  - `documents_bucket` → GCS `bucket.exists()` + a 1-object list with the runtime
    SA to prove read-reachability (mirrors `org_documents.py:41-45`); failure is a
    **warning verdict**, not a hard block (bucket may await a terraform apply).
  - `derived_group_tags` → checked against the group-tag registry (9.3).
- `backend/db/clients.py` — (a) **fail-closed** `resolve_documents_bucket`: unmapped
  domain + unset `DOCUMENTS_BUCKET` → deny, not the shared default
  (`db/clients.py:79`); (b) wrap `get_client_sync` in the durable Firestore cache.
- **Audit:** each mutation writes an append-only `admin_audit` record (actor,
  domain, before/after) via the 9.1 helper, replacing the bare `log.info`.

**Data model:** `clients/{domain}` unchanged (additive validation). New
`admin_audit` (9.1) + `client_config_cache` (durable-cache, id = domain) collections.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| POST | `/api/admin/tenants` | Orchestrated onboard: validate refs + bucket/IAM reachability, write doc, audit | No (new) |
| PUT | `/api/admin/clients/{domain}` | Now **validates** `enabled_skills`/`default_skill`/`documents_bucket` before commit; audits | Yes — a previously-accepted bad slug/bucket now 422s |
| GET | `/api/admin/tenants/{domain}/validate` | Dry-run: report ref + IAM-reachability verdicts without writing | No (new) |
| GET/DELETE | `/api/admin/clients[/{domain}]` | Unchanged CRUD (now audited) | No |

### Architecture

```
[Admin UI] → /api/proxy → POST /api/admin/tenants (aitana-admin gate)
                                    │
                    ┌───────────────┼────────────────────┐
              validate slugs   validate bucket      audit + write
            (skill_config)   (GCS exists + SA list)  (admin_audit + clients/{domain})
```

## Implementation Plan

### Phase 1: Fail-closed fallback + cache (~1d)
- [ ] `resolve_documents_bucket` fail-closed for unmapped domains when
      `DOCUMENTS_BUCKET` unset; add per-tenant enforcement test (~40 LOC).
- [ ] Durable Firestore cache tier around `get_client_sync` (port the
      `map_ppa_obligations` pattern) with best-effort read/write (~60 LOC).

### Phase 2: Validation + orchestration API (~1.5d)
- [ ] `backend/admin/tenants.py`: slug/bucket/IAM validators + `POST /api/admin/tenants`
      + `GET .../validate` dry-run (~180 LOC).
- [ ] Wire validation into `PUT /api/admin/clients/{domain}`; audit all mutations (~60 LOC).

### Phase 3: Thin admin UI (~1.5d)
- [ ] `/admin/tenants` roster + `TenantEditor` (skill multi-select from real list)
      + `TenantOnboardWizard`, rendering validation verdicts inline (~250 LOC).

## Migration & Rollout

- **Migrations:** none to `clients/{domain}` (additive). New `admin_audit` +
  `client_config_cache` collections on first write.
- **Feature flags:** `/admin/tenants` behind the shared `/admin` env flag (9.1);
  fail-closed fallback behind `TENANT_FALLBACK_FAIL_CLOSED` for one release so
  current unmapped-domain uploaders can be mapped first.
- **Env vars:** optionally set `DOCUMENTS_BUCKET` per-env for a legitimate internal
  default; otherwise fail-closed applies.
- **Rollback:** validation is the only behavior change on the existing PUT — revert
  the `admin/tenants.py` wiring to restore pass-through; cache + fallback are
  independently flagged.

## Testing Strategy

**Backend (pytest):** `resolve_documents_bucket` denies for unmapped domain with
no `DOCUMENTS_BUCKET`; cache = two `get_client_sync` calls → one Firestore read
(error → miss, never 500); validators (unknown slug → 422, unreachable bucket →
warning verdict, happy path writes + audits); composition preserved (admin
bypasses `enabled_skills` narrowing, ONE user sees only the whitelist,
`skills/routes.py:202-206`).

**Frontend (Vitest):** `TenantEditor` renders API validation errors inline
(error + empty paths, not just happy path); skill multi-select is populated from
the real skill list, not free text.

**Manual:** onboard a fresh tenant end-to-end with no Firestore PATCH; confirm the
audit record and that a real domain user lands on `default_skill` and sees only
`enabled_skills`.

## Security Considerations

- Every route deny-by-default behind the unified admin guard (9.1); tenant admin
  scope is a 9.3 concern (this doc uses `aitana-admin`, `admin/clients.py:28-31`).
- **Fail-closed fallback is the highest-value hardening here** — it removes the
  cross-tenant commingling path where unmapped domains share
  `"aitana-documents-bucket"` (`db/clients.py:79`).
- **Confidential-content boundary honored (CLAUDE.md hard rule):** this surface
  *grants/inspects* access and validates bucket references; it **never streams
  content**. Preview/thumbnail bytes stay behind the existing auth-gated proxies
  that re-check `doc.userId == user.uid` before streaming
  (`tools/documents/routes.py:95-171`, `:174-229` — "never a public URL"). All
  admin + audit data stays inside the GCP project edge.
- Bucket-reachability validation uses the runtime SA's own read; it must not
  accept a caller-supplied SA or expose bucket contents in the verdict (name +
  boolean reachability only).

## Success Criteria

- [ ] Onboarding a tenant is one orchestrated, audited call — no manual Firestore PATCH.
- [ ] Bad `enabled_skills`/`default_skill`/`documents_bucket` refs rejected (422) or
      flagged before they can silently break landing or hide skills.
- [ ] No unmapped domain writes to a shared fallback bucket.
- [ ] `clients/{domain}` read once per request (cached); every tenant mutation audited.
- [ ] All backend + frontend tests pass; lint/typecheck clean.

## Open Questions

- **OQ1 — Per-tenant branding:** branding is per-**deployment**
  (`NEXT_PUBLIC_BRAND_*` baked at build, `frontend/src/lib/branding.ts:54-164`),
  so a single deployment cannot rebrand per domain — multi-tenancy today is
  really "one deployment per customer." Options: (a) keep one-deploy-per-customer
  (status quo, simplest); (b) serve a `/api/clients/me`-driven runtime brand
  subset (logo/app-name) client-side for shared deployments. Lean (a) until a
  shared-deployment customer needs (b).
- **OQ2 — Bucket validation hard-block vs warn:** block onboarding on an
  unreachable `documents_bucket`, or warn (bucket may be created by a pending
  terraform apply)? Lean **warn + re-validate** so the two-repo ordering
  (`bootstrap-gcp-project.sh:54`) isn't a hard dependency.
- **OQ3 — Tenant-admin scoping:** does 9.4 need `tenant-admin:{domain}` (edit own
  tenant only), or is `aitana-admin`-only fine for v6.9.0? Defer to 9.3's role model.

## Related Documents

- [administration-overview.md](administration-overview.md) (9.1 — shared admin identity + audit)
- [skill-administration.md](skill-administration.md) (9.2), [user-group-administration.md](user-group-administration.md) (9.3)
- [client-tenant-management.md](../v6.3.0/implemented/client-tenant-management.md), [multi-tenant-demo-readiness.md](../v6.4.0/multi-tenant-demo-readiness.md)
- [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md)

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
