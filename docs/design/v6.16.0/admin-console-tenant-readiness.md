# Admin Console — Tenant-Admin Readiness & Consolidation

**Status**: Proposed
**Priority**: P0 (Phase 1 is a release blocker) / P1 (Phases 2–4)
**Estimated**: ~8–11 days across 4 phases
**Scope**: Fullstack (backend authz + admin API, frontend IA)
**Dependencies**: v6.9.0 [administration-overview](../v6.9.0/implemented/administration-overview.md) ✅,
[user-group-administration](../v6.9.0/implemented/user-group-administration.md) ✅,
[domain-tenant-administration](../v6.9.0/implemented/domain-tenant-administration.md) ✅,
[skill-administration](../v6.9.0/implemented/skill-administration.md) ✅
**Created**: 2026-07-21
**Last Updated**: 2026-07-21

## Problem Statement

v6.9.0 built the admin **layer** — one role model (`auth/admin_roles.py`), an
`/api/admin/*` API, an audit trail, and seven admin pages. It succeeded at its
stated goal: the common admin operations no longer require hand-run scripts.

The trigger for this doc is the next step the product is about to take:
**the admin console is to be released to client-side admins for inspection —
not just the platform owner.** Under that requirement the current surface has a
hard blocker and a set of consolidation gaps.

### Blocker: the tenant-admin role is modelled but not enforceable

`auth/admin_roles.py:73` defines `is_tenant_admin(group_tags, domain)`, and
`AccessContext.is_tenant_admin()` (`auth/access_context.py:100`) exposes it.
**It has exactly one call site in the whole backend**:
`admin/tenants.py:109`. Every other admin endpoint gates on
`is_platform_admin` via the `_Admin` dependency defined at
`admin/clients.py:36-42` and re-imported by six route modules
(`access_routes`, `analytics_routes`, `group_tags_routes`,
`platform_config_routes`, `tool_permissions_routes`, `users_routes`).

Consequences for a holder of `tenant-admin:acmeenergy.com`:

- **They never see the Admin link.** `UserMenu.tsx:49` probes
  `GET /api/admin/clients`, which is `aitana-admin`-only → 403 → link hidden.
- **The hub denies them.** `admin/page.tsx:68-96` runs the same probe and
  renders "The admin area requires the `aitana-admin` group."
- **The tenants page copy is misleading.** It describes tenant-admin scoping
  that no endpoint implements.

So the feature we are about to release to client admins is, today, unreachable
by client admins. This is Phase 1 and it blocks release.

### Blocker: cross-tenant data boundary

The endpoints are not merely platform-gated — several are **unscopable as
written**, so making them tenant-reachable without redesign would leak across
tenants:

- `GET /api/admin/clients` (`admin/clients.py:103`) returns **every tenant**
  config. A tenant admin must see exactly one.
- `GET /api/admin/analytics/sessions` (`admin/analytics_routes.py:59`) returns
  sessions across all tenants, including tool `argsJson`. The module header
  already concedes this: *"aitana-admin sees all; per-tenant/per-user scoping
  (9.1/9.4) is a follow-up."* That follow-up is now on the critical path, and
  `argsJson` may carry customer-confidential tool inputs — under the CLAUDE.md
  security rule this must be scoped **and** redacted before any cross-tenant
  reader exists.

**This is the systemic finding, per the skill's step-3 audit check:** the gap is
not one missing check, it is that *tenant scoping was never a parameter of the
admin API*. Fixing endpoints one at a time as client admins hit 403s is the
incremental-special-casing anti-pattern. Phase 1 therefore introduces scoping as
a **shared dependency** that every admin route adopts at once.

### Consolidation gaps (the "more usable" half)

1. **Skill visibility is decided in three places** — skill `accessControl`,
   tenant `enabled_skills` narrowing, and the admin bypass. **Admins bypass the
   tenant narrowing**, so *the admin's own view of a skill list is wrong by
   construction*. This is the exact confusion that prompted this work: the
   tenant screen listed skills that ONE's users do not see.
2. **`/api/admin/access/check` already answers the question, and the UI throws
   two-thirds of it away.** `admin/access_routes.py` returns direct tags,
   domain-derived tags, and the tool-permission decision *with provenance*,
   deliberately replicating `permissions.can_use_tool`'s lookup order so the
   dry-run cannot diverge from enforcement. The groups page wires only the tags
   array. The motivating feature below is mostly **surfacing what exists**.
3. **The audit trail is write-only.** `admin/audit.py` exports exactly
   `record_admin_action` (`__all__` at line 70) — no read function, no
   endpoint, no page. Records accumulate in `admin_audit` and are visible only
   via raw Firestore. An audit trail a client admin cannot read does not
   discharge the accountability promise that justified building it.
4. **Group tags live in three stores** — JWT custom claims, tenant
   `derived_group_tags`, and skill `accessControl.tags` — with no single view.
5. **Seven cards, one per API surface.** The IA mirrors the backend's module
   layout rather than the operator's task ("who can see what?", "why can't this
   user open that skill?"). Fine for the engineer who wrote it; wrong for a
   client admin.

**Correction to an earlier claim (twice revised).** During the audit I reported
*"Skill Studio has no admin check."* That was wrong, and the first correction was
also wrong. Recording both so the doc isn't built on either.

1. Studio's **mutations are gated backend-side** — `skills/routes.py:285`,
   `:297`, `:326` reject non-platform-admin saves of platform-owned skills, and
   the UI says so at `skills/studio/[skillId]/page.tsx:297`.
2. I then proposed gating the *page* in Phase 3, on the grounds that the editing
   surface was URL-discoverable. **That would have been a regression.** Studio is
   not an admin surface: `POST /api/skills` is open to any authenticated user
   (ownerId is taken from the JWT), and `/skills/new` redirects into
   `/skills/studio/new` — it is the ordinary user's authoring entry point.
   Gating it would remove self-service skill authoring for everyone.

**No change is needed.** A non-owner opening a platform skill already gets a
read-only editor with a "Fork to customize" path and a server-side 403 on save,
which is the correct behaviour rather than a leak.

**Impact:** the console cannot be released to client admins (Phase 1 blockers),
and once it can, its information architecture answers questions client admins
do not ask while hiding the one they do.

## Goals

**Primary Goal:** Make the admin console **safely releasable to client-side
tenant admins**, and reorganise it around operator tasks — with *"what your
users actually see"* as the motivating feature that forces the consolidation.

**Success Metrics:**
- A `tenant-admin:{domain}` holder can sign in, see the Admin link, and operate
  every surface scoped to their own domain — with **zero** cross-tenant records
  returned by any endpoint (asserted by test, not inspection).
- One shared scoping dependency covers 100% of `/api/admin/*` routes; no route
  decides tenant scope on its own.
- The effective-access view answers "why can/can't this user see this skill?"
  in **one screen**, including the `enabled_skills` narrowing the admin
  currently bypasses.
- The audit trail is readable in-product by the admin whose tenant it concerns.
- Seven API-shaped cards → five task-shaped areas.

**Non-Goals:**
- A new authz model. The 5-type `AccessControl` + tags model stays; we add
  *scoping and visibility*, not new primitives.
- End-user self-service account management.
- Replacing terraform for GCP provisioning.
- Reworking the chat/skill runtime. This doc is admin-surface only.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Admin surface, off the chat latency path. |
| 2 | EARNED TRUST | +1 | Effective-access makes the *real* decision inspectable, including the narrowing admins currently bypass; audit becomes readable. |
| 3 | SKILLS, NOT FEATURES | +1 | Client admins can see and operate skill visibility for their own tenant without an engineer. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | Orthogonal. |
| 5 | GRACEFUL DEGRADATION | +1 | Scoped-empty and forbidden states render explicitly (never-silent, CLAUDE.md #8) instead of a blank list or a hidden link. |
| 6 | PROTOCOL OVER CUSTOM | 0 | No new formats; reuses the existing admin API + access-check contract. |
| 7 | API FIRST | +1 | Scoping lands in the API as a shared dependency; UI and CLI are thin clients of it. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Audit becomes readable in-product; scope decisions are logged. |
| 9 | SECURE BY CONSTRUCTION | +1 | Deny-by-default scoping shared by every route; closes the cross-tenant read and redacts `argsJson`. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The consolidation is the backend returning the full decision; the UI stops re-deriving and stops discarding. |

**Net: +7** (threshold ≥ +4 ✅). No axiom scores −1, so no conflict
justifications are required. Hard-fail rules: EARNED TRUST +1 ✅;
SECURE BY CONSTRUCTION +1 ✅ (this feature does introduce new data access —
tenant admins reading admin data — which is precisely why Phase 1 is scoping).

## Standards Compliance

No new schema, protocol, or wire format is introduced. This work reuses:

- **Existing admin API** (`/api/admin/*`, FastAPI + Pydantic response models).
- **Firebase custom claims** for role tags (`groupTags`) — the established
  identity mechanism; `tenant-admin:{domain}` is already minted in that
  namespace.
- **The existing `AccessControl` 5-type model** and `tool_permissions` plane.
- **A2UI / AG-UI are not involved** — the admin console is conventional
  request/response React, not an agent surface.

Per step 5b, the check for an applicable open standard was made and the answer
is that the applicable standards here are the project's own already-adopted
ones. Nothing is being reinvented, so Axiom #6 scores 0 rather than −1.

## Design

### Phase 1 — Tenant scoping as a shared dependency (release blocker)

The core move: **one dependency, adopted by every admin route**, that resolves
the caller's admin scope once and hands routes a value they cannot ignore.

```python
# backend/admin/scope.py (new)

@dataclass(frozen=True)
class AdminScope:
    """Resolved admin authority for one request.

    `domains is None` means platform-wide (aitana-admin). Otherwise it is the
    exact set of domains this caller may read or mutate — never empty, since a
    caller with no admin authority is rejected before construction.
    """
    user: User
    domains: frozenset[str] | None

    @property
    def is_platform(self) -> bool:
        return self.domains is None

    def assert_may(self, domain: str) -> None:
        """Raise 403 unless this scope covers `domain`. Deny-by-default."""
        if self.domains is not None and domain not in self.domains:
            raise HTTPException(403, "outside your tenant scope")
```

Resolution reuses the existing role helpers rather than reimplementing them:
`is_platform_admin(user.group_tags)` → platform scope; otherwise collect every
`tenant-admin:{domain}` tag → tenant scope; none → 403.

Adoption is mechanical and **complete in one pass** — this is the point of
doing it as a dependency rather than per-route:

| Route module | Change |
|---|---|
| `clients.py:103` (list) | Filter to `scope.domains`; platform unchanged. |
| `clients.py:120/134/197` | `scope.assert_may(domain)` before read/write/delete. |
| `analytics_routes.py:59/101` | Filter sessions by tenant **and redact `argsJson`** for non-platform scope. |
| `users_routes.py` (4) | Restrict listing/mutation to users in scope domains. |
| `group_tags_routes.py` (2) | A tenant admin may grant only tags their tenant owns. |
| `tool_permissions_routes.py` (4) | Scope by domain key. |
| `access_routes.py` (1) | Target user must be in scope. |
| `platform_config_routes.py` (2) | **Stays platform-only** — the platform preamble is global. |
| `tenants.py:109` | Migrate its bespoke check to the shared dependency. |

**`argsJson` redaction** deserves its own note: tool arguments can contain
customer-confidential inputs (document ids, extracted clause text). For any
non-platform scope, analytics returns tool **names and timings** but replaces
`argsJson` with a redaction marker. This is the CLAUDE.md rule applied to a
derived artefact — a tool-call argument is a derivative of private content.

**Frontend (Phase 1):** replace the `GET /api/admin/clients` probe in
`UserMenu.tsx:49` and `admin/page.tsx:74` with a purpose-built
`GET /api/admin/whoami` returning `{scope: "platform"|"tenant", domains: [...]}`.
Probing a *data* endpoint to infer a *role* is exactly why the link is invisible
to tenant admins; the fix is to ask the role question directly. Fix the
misleading tenant-admin copy in the same change.

### Phase 2 — Effective access: "What your users actually see" (motivating feature)

One screen, per tenant, answering the question that started this work. It is
built on `POST /api/admin/access/check`, which already computes the three
planes with provenance — the work is (a) adding the missing plane and (b)
rendering what the endpoint already returns.

**Backend:** extend the access-check response with the **`enabled_skills`
narrowing** — the plane that is currently invisible precisely because admins
bypass it. The response gains, per skill: the `accessControl` verdict, the
tenant-narrowing verdict, and the **effective** verdict, each with a reason
string. Critically, the endpoint computes this **as the target user**, not as
the caller, so the admin sees the user's truth rather than their own.

**Frontend:** a "What your users actually see" panel — pick a user (or a
representative domain user), get the resolved skill list with a per-skill
"visible / hidden because …" reason. The reason strings come from the backend;
the UI does not re-derive access (Axiom #10).

This feature is the forcing function for the consolidation: it cannot be built
correctly without Phase 1's scoping (you must not resolve a user outside your
tenant) and it is what makes the three-store tag sprawl legible.

### Phase 3 — IA: seven API-shaped cards → five task-shaped areas

| New area | Absorbs | Answers |
|---|---|---|
| **Your tenant** | tenants + **effective access** | "What is my tenant configured to do, and what do my users actually see?" |
| **People & access** | users + groups + tool-permissions | "Who is here and what may they use?" |
| **Skills** | skill visibility + Studio entry | "What is published, to whom?" |
| **Activity & audit** | analytics + **new audit reader** | "What happened, and who changed what?" |
| **Platform** | settings/platform-config | Platform-admin only; hidden entirely for tenant scope. |

Also in Phase 3: gate the Studio *page* on the same scope probe so the editing
surface is not URL-discoverable (the corrected, smaller issue from above).

### Phase 4 — Audit reader

Add `list_admin_actions(scope, *, limit, cursor)` to `admin/audit.py` (breaking
its write-only `__all__`), a `GET /api/admin/audit` endpoint scoped by
`AdminScope`, and the Activity-area view. Tenant admins see actions targeting
their own domain; platform admins see all. Read-only, no mutation, no audit row
for reading (consistent with `access_routes`' existing stance).

### CLI Surface

Per step 5b-bis, the scoping work is developer-facing and currently needs curl
plus a hand-minted token to test:

| Command | Purpose |
|---|---|
| `aiplatform admin whoami` | Print the caller's resolved admin scope. The one-command check for "is my tenant-admin claim working?" |
| `aiplatform admin access-check --user <email> [--skill <slug>]` | Effective-access dry-run from the terminal; the same endpoint the Phase 2 panel uses. |
| `aiplatform admin audit --domain <d> [--limit N]` | Tail the audit trail (Phase 4). |

Each is a Click subcommand + an httpx call + a unit test (~0.25d each).
Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).

## API Changes

| Endpoint | Change |
|---|---|
| `GET /api/admin/whoami` | **New.** Returns resolved scope. Any authenticated user (returns `scope: "none"` rather than 403, so the frontend can render a clean state — never-silent). |
| `GET /api/admin/clients` | Response filtered to caller scope. |
| `GET/PUT/DELETE /api/admin/clients/{domain}` | 403 outside scope. |
| `GET /api/admin/analytics/sessions[/{id}]` | Scoped; `argsJson` redacted for tenant scope. |
| `POST /api/admin/access/check` | Response extended with the `enabled_skills` plane + per-skill effective verdict and reasons. Additive. |
| `GET /api/admin/audit` | **New** (Phase 4), scoped. |
| `/api/admin/platform-config` | Unchanged — platform-admin only. |

No breaking changes for the existing platform admin: every scoped endpoint
returns exactly what it returns today when `scope.is_platform`.

## Migration

- **No data migration.** `tenant-admin:{domain}` claims already mint; the
  `admin_audit` collection already accumulates.
- **No feature flag.** An earlier revision gated the rollout behind
  `ADMIN_TENANT_SCOPE_ENABLED`; it was removed before shipping. A flag that is
  always on is dead weight and a trap (the next reader concludes tenant scoping
  is optional), and runtime env vars **do not promote with code** — a flag set
  in dev and missed in prod would mean the console silently refusing every
  client admin in prod only, which is precisely the recurring bug class in
  [env-config-parity](../../ops/env-config-parity.md).
- **The real gate is the claim.** Nobody is a tenant admin until someone is
  granted `tenant-admin:{domain}`, so "shipped" and "in use" remain separate
  decisions without any flag. At time of writing dev has **zero** tenant-admin
  holders, so enabling this changed no live behaviour.
- **Rollback:** revert the commits. There is no config-level kill switch by
  design — a switch nobody remembers to set is worse than none.
- **Per-env parity warning:** granting `tenant-admin:{domain}` is a **Firebase
  custom claim**, which per the env-config-parity record does **not** promote
  with code. Each env needs claims minted separately; add a row to
  [docs/ops/env-config-parity.md](../../ops/env-config-parity.md) in Phase 1.

## Testing Strategy

**Backend (pytest):**
- `AdminScope` resolution: platform / single-tenant / multi-tenant / none.
- **A parametrised cross-tenant matrix over every scoped endpoint** — a
  `tenant-admin:a.com` caller gets zero `b.com` records from each. This is the
  test that makes "no route decides scope on its own" enforceable rather than
  aspirational, and it must fail loudly when a new admin route is added without
  the dependency.
- `argsJson` redaction asserted for tenant scope, absent for platform.
- Effective-access: a skill visible by `accessControl` but excluded by
  `enabled_skills` reports **hidden**, with the narrowing named as the reason.

**Frontend (Vitest):** the whoami probe drives link visibility for all three
scopes; forbidden and scoped-empty states render visibly.

**Verification (per the repo's live-stream rule and the standing instruction to
API-test rather than drive a browser):** Phase 1 is not done on green unit
tests. It is done when a **real token for a `tenant-admin` user** exercises
every scoped endpoint against deployed dev via `aiplatform` / curl, and the
cross-tenant matrix is confirmed against live data. Browser verification is the
user's step, not an automated one.

## Success Criteria

- [ ] A `tenant-admin:{domain}` user signs in, sees the Admin link, and operates every area scoped to their domain.
- [ ] Cross-tenant matrix test passes for every `/api/admin/*` route; adding an unscoped route fails CI.
- [ ] `argsJson` never reaches a tenant-scoped analytics response.
- [ ] "What your users actually see" reports the `enabled_skills` narrowing the admin previously bypassed.
- [ ] Audit trail readable in-product, scoped.
- [ ] Five task-shaped areas; Platform area hidden for tenant scope.
- [ ] `aiplatform admin whoami` / `access-check` / `audit` work end-to-end against deployed dev.
- [ ] Live API verification against dev with a real tenant-admin token, not just unit tests.

## Implementation Plan

| Phase | Work | Est. |
|---|---|---|
| 1 | `AdminScope` + adoption across 9 route modules; `whoami`; `argsJson` redaction; frontend probe swap + copy fix; cross-tenant matrix tests | ~4d |
| 2 | Access-check `enabled_skills` plane + effective-access panel | ~2.5d |
| 3 | IA consolidation to 5 areas; Studio page gating | ~2d |
| 4 | Audit reader (backend + endpoint + view) | ~1.5d |
| — | CLI commands (3 × 0.25d) | ~0.75d |

Phase 1 is independently shippable and is the release gate. Phases 2–4 are
sequenced but each is independently shippable behind the same flag.

## Open Questions

1. **Multi-domain tenant admins** — `AdminScope.domains` is a set, so the model
   supports it. Does any real customer need one admin over several domains, or
   should we constrain to one and simplify the UI?
2. **Should tenant admins mint group tags at all?** Phase 1 allows granting only
   tags their tenant owns. The stricter alternative is read-only tag visibility
   for tenant scope in v1. Product call.
3. **`enabled_skills` editability** — Phase 2 makes the narrowing *visible*.
   Should a tenant admin also *edit* it, or does that stay platform-owned?
4. **Audit retention** — no TTL on `admin_audit` today. Worth deciding before
   client admins can read it.

## Related Documents

- [administration-overview](../v6.9.0/implemented/administration-overview.md) — the layer this builds on
- [user-group-administration](../v6.9.0/implemented/user-group-administration.md) — access-check origin
- [domain-tenant-administration](../v6.9.0/implemented/domain-tenant-administration.md) — tenant model
- [skill-administration](../v6.9.0/implemented/skill-administration.md) — skill access editing
- [local-dev-cli](../v6.1.0/local-dev-cli.md) — CLI command tree
- [docs/ops/env-config-parity.md](../../ops/env-config-parity.md) — why claims don't promote with code
