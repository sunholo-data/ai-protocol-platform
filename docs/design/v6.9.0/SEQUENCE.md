# v6.9.0 Build Sequence — Administration

**Gate:** v6.8.0 first-impression/elicited-handoff in progress (M1/M2 shipped). The 2026-07-14 ONE bring-up made the administration gap concrete: seeding a skill required a deploy, the tenant landing required a hand-run Firestore REST PATCH, and granting a group tag has no path at all short of the Firebase Admin SDK.

**Status as of 2026-07-16:** ✅ **9.1–9.4 IMPLEMENTED + deployed** (dev+test) via the ADMIN-COMPLETE sprint ([admin-completion-sprint.md](admin-completion-sprint.md)) — M0 security quick-win → M1 identity+audit → M2 user/group, M3 skill-admin, M4 tenant. Those four docs moved to `implemented/`. **9.5 analytics stays Planned** (deferred — needs a BigQuery/rollup sink that doesn't exist yet). Residual follow-ups tracked in the sprint plan (retire the firestore.rules hardcoded email after terraform rules deploy; M3 Studio polish). Original 2026-07-14 three-part audit mapped the surface; docs authored from it.

**Theme:** *The access model is rich and correct; the administration of it is hand-run scripts, raw Firestore edits, and redeploys.* v6.9.0 adds a coherent, API-first administration layer — one admin identity model, one `/api/admin/*` surface, a thin admin UI, an audit trail — and turns the natively-captured interaction data into a reporting product. The single highest-value hardening: retire the four divergent admin definitions (incl. the hardcoded `owner@yourcompany.com` in `firestore.rules:25`) behind group-tag claims.

---

## Ordering

| Order | Doc | Priority | Est | Depends on | Notes |
|-------|-----|----------|-----|-----------|-------|
| 9.1 | [administration-overview.md](implemented/administration-overview.md) ✅ | **P1** (umbrella) | ~2d (identity + audit foundation) | 6.0.0 auth/access ✅, 6.3.0 client-tenant ✅ | **The shared model.** One admin-identity model driven by claims (`aitana-admin` super-admin + `tenant-admin:{domain}` + fold `one-admin`); `firestore.rules` isAdmin() from claims not a hardcoded email; one `/api/admin/*` surface; a group-tag registry; an append-only audit record used by every mutation. Sets the contract 9.2–9.5 build on. Net axiom **+8**. |
| 9.3 | [user-group-administration.md](implemented/user-group-administration.md) ✅ | **P1** (most acute) | ~3d | 9.1 | **The biggest gap — no path exists today.** Per-user tag grant/revoke (`/api/admin/users/{uid}/groups`, wiring the stubbed `aiplatform groups` CLI); a group-tag registry (first-class tags); effective-access lookup + "who holds tag X" (`/api/admin/access/check`); tool-permission admin; token-refresh propagation; unify the 4 admin definitions. Net axiom **+7**; strongest on SECURE-BY-CONSTRUCTION + EARNED-TRUST. |
| 9.2 | [skill-administration.md](implemented/skill-administration.md) ✅ | **P1** | ~3d | 9.1 | Close the Skill Studio gap (`accessControl`/slug/tags/protocols/shell omitted from save — `page.tsx:1051`); durable Firestore-authoritative skill create (no redeploy); a `managed_by` marker to stop template↔live drift; consolidate the three model-writers; wire or retire `featured`/`usage_count`. Net axiom **+8**. |
| 9.4 | [domain-tenant-administration.md](implemented/domain-tenant-administration.md) ✅ | **P1** | ~3d | 9.1 | Tenant CRUD UI; `POST /api/admin/tenants` onboarding orchestration (slug + bucket + SA-reachability validation, atomic doc); fail-closed unmapped-domain fallback (cross-tenant commingling fix); durable client-config cache; audit. Net axiom **+9**; keeps the confidential-content proxy boundary intact. |
| 9.5 | [analytics-and-reporting.md](analytics-and-reporting.md) | P2 (green-field) | ~4d + per-template | 9.1, 9.3 | Turn natively-captured data (OTel→Cloud Trace/Logging/BigQuery, full content capture, session mirror) into product: a chat-history/trace viewer, usage/cost analytics, and a report-template engine (the AIPLA teacher-report generalisation) — access-scoped, inside the GCP edge. Reports-as-jobs (8.3). Net axiom **+7**; the direct product payoff of OBSERVABLE-BY-DEFAULT. |

---

## Timeline estimate

| Sprint | Doc | Status |
|--------|-----|--------|
| 9.1 | [administration-overview.md](implemented/administration-overview.md) | ✅ Implemented 2026-07-16 (M1: admin_roles + audit + claims-rules additive) |
| 9.3 | [user-group-administration.md](implemented/user-group-administration.md) | ✅ Implemented 2026-07-16 (M2: registry, access-check, tool-perms, force-refresh, /admin/groups) |
| 9.2 | [skill-administration.md](implemented/skill-administration.md) | ✅ Implemented 2026-07-16 (M3: managed_by+seeder-skip, shell/featured, public-confirm; Studio polish deferred) |
| 9.4 | [domain-tenant-administration.md](implemented/domain-tenant-administration.md) | ✅ Implemented 2026-07-16 (M4: validation/onboard API, config cache, TenantEditor+Wizard) |
| 9.5 | [analytics-and-reporting.md](analytics-and-reporting.md) | ⏸ Planned / deferred (needs BigQuery-rollup infra; trace-viewer shipped in the July slice) |

## What ships in v6.9.0

- **9.1** — unified admin identity (claims-driven; hardcoded-email rule retired), one `/api/admin/*` contract, a group-tag registry, an admin audit record; a gated `/admin` UI shell.
- **9.3** — per-user group-tag grant/revoke API+CLI+UI (the stubbed `/api/groups/*` finally wired), effective-access lookup, tool-permission admin, tag registry.
- **9.2** — skill access/publish editable in Skill Studio; durable skill-create without a redeploy; template↔live drift resolved; marketplace `featured`/`usage` wired-or-retired.
- **9.4** — tenant CRUD UI + validated onboarding orchestration; fail-closed bucket fallback; client-config cache; audit.
- **9.5** — trace viewer + usage/cost analytics + a first report template, over existing capture, inside the edge.

## Dependency Graph

```
6.0.0 auth/access ✅ ──────┐
6.3.0 client-tenant ✅ ─────┼─→ 9.1 administration-overview (identity + audit foundation)
6.6.0 fork-convergence ✅ ──┘         │
                                      ├─→ 9.3 user-group-administration  (per-user tags · registry · access-check)
                                      ├─→ 9.2 skill-administration        (Studio access · durable create · drift)
                                      ├─→ 9.4 domain-tenant-administration(tenant CRUD · onboarding · fallback)
                                      └─→ 9.5 analytics-and-reporting     (trace viewer · usage · report engine)
                                            └── consumes OBSERVABLE-BY-DEFAULT capture (OTel→BigQuery) + 8.3 jobs
```
