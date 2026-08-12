# Sprint Plan: ADMIN-COMPLETE — finish the v6.9.0 administration roadmap

**Status**: Proposed (planning only — user picks what executes)
**Created**: 2026-07-16
**Scope**: Fullstack (backend-heavy foundation, then parallel per-domain work)
**Design docs**: [9.1](administration-overview.md) · [9.2](skill-administration.md) · [9.3](user-group-administration.md) · [9.4](domain-tenant-administration.md) · [9.5](analytics-and-reporting.md)

## Summary

The July admin sprint (`admin-ui-sprint.md`) shipped a **usable UI slice** — `/admin`
hub, `/admin/tenants`, `/admin/users` (grant/revoke), `/admin/analytics` (trace
viewer), Studio access editor — but it deliberately deferred **9.1 (the identity
+ audit foundation)** and left 9.2–9.5 only partially covered. This sprint
finishes the roadmap.

**Key structural facts (from a 4-agent code-verified remainder audit, 2026-07-16):**

1. **9.1 is a hard dependency** for the *guard* and *audit* halves of every other
   slice: 9.2's durable-create + admin_roles swap, 9.3's audit wiring, 9.4's
   audit, 9.5's tenant-scoping all need it. → **9.1 goes first.**
2. **A live cross-tenant leak exists and is NOT gated on anything.**
   `resolve_documents_bucket` (`backend/db/clients.py:79`) returns the shared
   `aitana-documents-bucket` for any *unmapped* domain — it **fails open** today
   (`test_clients.py:32-34` asserts it). This is the single highest-value fix and
   is standalone (~70 LOC). → **pull forward as M0.**
3. **9.5 Phase 2 is blocked on infra that doesn't exist.** There is no
   log→BigQuery sink/table anywhere in the repo; usage/cost analytics needs a
   BigQuery sink *or* a durable Firestore rollup stood up first (cross-repo
   terraform). Reports-as-jobs also blocks on the unbuilt v6.8.0 jobs subsystem.
   → **9.5 is deferred / design-ahead, split from the buildable work.**
4. **After 9.1, the three domain slices (9.2 / 9.3 / 9.4) are independent** —
   different files (skills vs users/groups vs tenants) — so they parallelize.

**Velocity context:** 285 commits / 54.6k insertions in the last 14 days
(autonomous-agent driven). Estimates below are engineering-days of *work*, highly
compressible in wall-clock at this cadence.

## Milestones

### M0 — Security quick-win: fail-closed tenant fallback + admin discoverability (~0.5d, backend + tiny frontend)
Not gated on anything; ship first.
- **Fail-closed unmapped-domain bucket fallback** (9.4). `resolve_documents_bucket`
  must deny (or route to a per-tenant-scoped default) instead of returning the
  shared bucket for an unmapped domain, behind a `TENANT_FALLBACK_FAIL_CLOSED`
  flag (one-release window to map current unmapped uploaders first). Files:
  `backend/db/clients.py`, `backend/tests/unit/test_clients.py` (flip the
  fail-open assertion). **~70 LOC.**
- **Admin nav entry** — the M1-acceptance gap we just hit: `/admin` is URL-only.
  Add a gated "Admin" nav link that renders only when the `/api/admin/clients`
  probe returns 200. Files: `frontend/src/components/navigation/*` (or ShellChrome).
  **~40 LOC.**
- **Accept:** an unmapped domain no longer resolves to the shared bucket (test
  flipped); an admin sees an Admin link, a non-admin doesn't.
- **Risk:** med — behavior change on the bucket path; the flag + a "map these
  domains first" audit query is the safety valve. **Security: this closes a live
  cross-tenant commingling path — highest value in the whole sprint.**

### M1 — 9.1 identity + audit foundation (~2.5d, backend + firestore.rules)
The umbrella everything else builds on. **Security-critical, subtle.**
- **Unified admin-role guard** — one `backend/auth/admin_roles.py` reconciling the
  four divergent notions (`aitana-admin` tag, `one-admin` tag, `ADMIN_SEED_ALLOWED_SAS`,
  hardcoded email) into `aitana-admin` (super) + `tenant-admin:{domain}` (scoped)
  + ownership; fold `one-admin`. Files: `backend/auth/admin_roles.py` (new),
  `backend/admin/auth.py`, `backend/auth/access_context.py`, `backend/skills/routes.py`.
- **Retire the hardcoded email in `firestore.rules:25`** → `request.auth.token.groupTags.hasAny(['aitana-admin'])`.
  Ship behind a test that the claims rule admits `aitana-admin` and denies others
  *before* removing the email.
- **Append-only `admin_audit` collection + write helper** (`actor, action, target,
  before, after, ts`) used by every admin mutation. Files: `backend/admin/audit.py` (new).
- **Accept:** one guard; hardcoded-email gone; `aitana-admin` + `tenant-admin:{domain}`
  the only admin notions; a mutation writes an audit row; a tenant-admin cannot
  reach cross-tenant.
- **Risk:** high — authz + firestore.rules change across multiple gates;
  deny-by-default must be preserved. Real-stream/browser verification required.

### M2 — 9.3 user/group completion (~3.5d, fullstack) — *the most acute gap*
Depends on M1 (guard + audit helper). ~1,340 LOC.
- Group-tag **registry** (`group_tags` collection + model + `GET/PUT /api/admin/group-tags`; grant path validates tag id → 422 on unknown).
- **Tag-holders reverse lookup** (`GET /api/admin/groups/{tag}/members` — needs `list_users()` iteration or a membership index).
- **Effective-access** `POST /api/admin/access/check` (JWT tags ∪ derived ∪ tool-perms, with provenance) — mirror `can_use_tool` order exactly.
- **Tool-permission admin plane** (`GET/PUT/DELETE /api/admin/tool-permissions/{docId}` + `clear_cache` on write).
- **Claim propagation / force-refresh** (`revoke_refresh_tokens` + real UI control replacing the hardcoded notice).
- **CLI reconciliation** — repoint `groups.py` / `access.py` at the real `/api/admin/*` routes; drop TODO banners; fix uid-vs-email.
- **`/admin/groups` UI** + effective-access provenance badges on `/admin/users`.
- **Audit wiring** via the M1 helper.
- **Accept:** grant an unknown tag → 422; "who holds tag X" answers; access-check matches real enforcement; every mutation audited; UI never-silent on 403/unknown-tag.
- **Risk:** med — `list_users()` scale; access-check must not diverge from enforcement.

### M3 — 9.2 skill-admin completion (~2.5–3d, fullstack)
Depends on M1 (durable-create guard + admin_roles swap). ~630 LOC. (`skill_materializer.py` groundwork already landed 2026-07-16.)
- **`POST /api/admin/skills`** durable Firestore-authoritative create (no template, no redeploy; `ownerId` server-side; stamp `managed_by="firestore"`; guarded + audited).
- **`managed_by` field + seeder skip** — seeder (`platform_seed.py:305-352`) must not clobber `managed_by=="firestore"` skills on redeploy (regression test = the guard).
- **Template-diff** (`GET /api/skills/{id}/template-diff`) + Studio drift badge + read-only template-tracked fields.
- **Widen `buildSaveBody`** — slug/tags/protocols/shell/references still dropped; add slug/tags inputs, 409 slug-conflict surfacing, and a **`→ public` confirm gate** (CLAUDE.md: public = unauthenticated marketplace + A2A card).
- **`shell` + admin-gated `featured`** on `UpdateSkillRequest`.
- **Wire `increment_usage`** (fire-and-forget) so marketplace ranking isn't all-zeros; admin-only Featured toggle (or drop `featured` — OQ2).
- **Remove orphans** — `/skill/[skillId]/settings` + `ModelSelector` (dead raw model writer); Studio's `ModelTierPicker` is the single writer.
- **Swap skill-admin tags** for the M1 `admin_roles` guard.
- **Accept:** create a skill in-product with no redeploy that survives a re-seed; `→ public` requires confirm; model edited only via Studio.
- **Risk:** med — seeder behavior change; the public-confirm + 409 flows.

### M4 — 9.4 tenant completion (remainder) (~2d, fullstack)
M0 already took the security fix. Depends on M1 for audit. ~840 LOC remaining.
- **Validation + orchestration API** — `backend/admin/tenants.py` (new): `POST /api/admin/tenants` (atomic onboard) + `GET /api/admin/tenants/{domain}/validate` (dry-run); validators for `enabled_skills`/`default_skill` (slug → 422 unknown), `documents_bucket` (GCS `exists()` + SA-read reachability → warning verdict, name+boolean only), `derived_group_tags` (against the M2 registry). Wire validation into the existing pass-through `PUT /api/admin/clients/{domain}`.
- **Durable client-config cache** (Firestore tier over `get_client_sync`, `map_ppa_obligations` pattern) — removes the ≥2× `clients/{domain}` re-read per request; degrade to miss, never 500.
- **Tenant-mutation audit** via the M1 helper.
- **Frontend** — `TenantEditor` (skill multi-select from the real list; inline render of 422 / IAM-unreachable verdicts — never-silent) + `TenantOnboardWizard` (`POST`, per-step verdicts).
- **Accept:** onboard a tenant with bad skill ref → 422 with a visible verdict; bucket reachability shown (name+bool only); config cache hit path; mutation audited.
- **Risk:** med — the SA-reachability probe needs real GCS creds (integration-marked); the PUT change 422s previously-accepted bad refs.

### M5 (DEFERRED) — 9.5 analytics & reporting (~5d + infra)
**Not buildable in full now.** Split:
- **Prerequisite (infra, cross-repo):** stand up a log→BigQuery sink/table *or* a durable Firestore usage-rollup (Cloud Scheduler → rollup job). Terraform in `multivac-aitana`, not backend code here. **This gates Phase 2.**
- **Buildable now (flat-admin):** a usage/cost dashboard over the rollup + a bespoke report engine (`/api/reports/*` + registry + one exemplar template). ~2,000 LOC.
- **Blocked on M1:** per-tenant/per-user access-scoping of the analytics viewer (today flat `aitana-admin`-sees-all — a cross-tenant read risk once it goes past trace-viewing).
- **Blocked on v6.8.0 jobs:** reports-as-jobs.
- **Recommendation:** design-ahead now; schedule after M1 lands and the BigQuery/rollup infra decision is made. **Security: usage rollups + reports narrate confidential session content — must stay inside the GCP edge; any external report delivery (PDF/email/share-link) is a separate gated decision per CLAUDE.md.**

## Model Assignment

| Stage | Model | Why |
|-------|-------|-----|
| M0 (security fix) | claude-opus-4-8 (xhigh) | Small but a security behavior change on the tenant-isolation path — must be correct. |
| M1 (identity + audit) | claude-opus-4-8 (xhigh) | Authz + firestore.rules across multiple gates; the highest-subtlety, security-critical milestone. |
| M2 (user/group) | claude-opus-4-8 (high) | Authz-adjacent (access-check must mirror enforcement; tool-perm plane) — careful + audited. |
| M3 (skill-admin) | claude-sonnet-5 for UI/CRUD; claude-opus-4-8 for the durable-create + admin_roles swap | Mixed: mechanical Studio/CRUD vs the security-sensitive create + guard swap. |
| M4 (tenant) | claude-sonnet-5 for UI/cache; claude-opus-4-8 for the validation/reachability API | Mostly CRUD + cache; the SA-reachability probe + PUT-validation is the care point. |
| M5 (analytics) | claude-opus-4-8 for scoping; claude-sonnet-5 for dashboards | Deferred; scoping is the security crux, dashboards mechanical. |
| Evaluation | user (end-of-milestone review) | Consistent with the July sprint's cadence. |

(Model ids per the session's Claude 5 / Opus 4.8 registry.)

## Dependency graph & parallelism

```
M0 (security fix + nav) ──────────────┐  (standalone — ship first)
                                       │
M1 (9.1 identity + audit) ─────────────┼─→ M2 (9.3 user/group)   ┐
   guard + tenant-admin + audit helper ├─→ M3 (9.2 skill-admin)  ├─ independent → parallelize
   + firestore.rules hardening         └─→ M4 (9.4 tenant)       ┘
                                            │
M5 (9.5 analytics) — DEFERRED: needs M1 (scoping) + BigQuery/rollup infra (cross-repo) + v6.8.0 jobs (reports-as-jobs)
```

**Buildable now:** M0 + M1 + M2 + M3 + M4 ≈ **~3,400 LOC / ~11–12 engineering-days**
(M2/M3/M4 parallel after M1 → far less wall-clock).
**Deferred:** M5 ≈ ~2,400 LOC / ~5d + a cross-repo infra prerequisite.

## Quality gates (per milestone)
- Frontend: `npm run quality:check` (lint + typecheck + tests + build).
- Backend: `cd backend && make lint && make test-fast`.
- **M0/M1 are security changes — verify on a real deployed stream + browser, not
  just jsdom/unit** (per CLAUDE.md). M1's firestore.rules change needs a live
  admits-`aitana-admin`/denies-others check before the email is removed.
- Deploy to dev via the frontend trigger (chat backend is a sidecar); promote
  dev→test after review.

## Success criteria
- [ ] No unmapped domain resolves to a shared bucket (cross-tenant leak closed).
- [ ] One admin guard; hardcoded-email rule gone; every admin mutation audited.
- [ ] Grant a tag / onboard a tenant / publish a skill / set a landing — all
      in-product, no hand-run script / Firestore-edit / redeploy.
- [ ] "Who holds tag X" + effective-access answerable.
- [ ] Admin surface discoverable (nav) and access-scoped.
- [ ] 9.5 design-ahead recorded; infra prerequisite tracked.
