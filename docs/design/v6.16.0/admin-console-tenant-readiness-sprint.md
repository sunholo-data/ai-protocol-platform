# Sprint: ADMIN-SCOPE — Phase 1 tenant scoping (release gate)

**Design doc**: [admin-console-tenant-readiness.md](admin-console-tenant-readiness.md)
**Sprint ID**: `ADMIN-SCOPE`
**Scope**: Phase 1 only (Phases 2–4 are separate sprints)
**Estimated**: ~4–5 days
**Created**: 2026-07-21

## Sprint Goal

Make `tenant-admin:{domain}` a **real, enforceable role** across every
`/api/admin/*` route, with a cross-tenant data boundary proven by test — so the
admin console can be released to client-side admins.

Phase 1 is the release gate. Nothing here changes what a platform admin sees.

## Findings that changed the plan

Two things surfaced while sizing against the code that the design doc did not
anticipate:

### 1. `chat_sessions` has no tenant field (raises the analytics estimate)

`db/chat_sessions.py:52` writes `ownerUid` and nothing domain-shaped. There is
no field to filter analytics on. Options considered:

- **Resolve uid → domain per row at read time** — an O(rows) Firebase Auth
  lookup per request. Rejected: slow and rate-limit-prone.
- **Write `ownerDomain` at session-create + backfill existing docs** — chosen.
  One field, one backfill script, and it makes the Firestore query a real
  `where()` instead of a post-filter.

This turns analytics scoping from "add a filter" into "schema change +
backfill", hence M4 is the largest milestone.

### 2. RESOLVED — may a tenant admin read their users' chat transcripts?

`GET /api/admin/analytics/sessions/{id}` returns `messages` — **full chat
content**, not just metadata. Scoping it to the tenant makes a client admin able
to read every conversation their users had with the assistant.

That may be entirely legitimate (the tenant is the data controller for its own
users' content) or a GDPR/works-council problem, depending on what customers were
told. Per the CLAUDE.md rule — *"if you have any doubt, stop and ask"* — the
sprint shipped the conservative default and asked.

**Ruled 2026-07-21: tenant admins MAY read full conversations for their own
domain.**

| Endpoint | Tenant admin | Platform admin |
|---|---|---|
| `GET /analytics/sessions` (list metadata) | ✅ own domain | ✅ all |
| `GET /analytics/sessions/{id}` (**transcript**) | ✅ own domain | ✅ all |

Consequences that follow from the ruling and are therefore intended:
- **`argsJson` is no longer redacted** for an in-scope tenant admin. Redacting
  the arguments while serving the conversation they came from protected nothing,
  and dead security code reads as a guarantee that isn't running — so the
  redactor was removed rather than left unreachable.
- A tenant admin can see content that surfaced in a user's session even if it
  came from a document that user held privately. Same-tenant, and implied by
  "full conversations", but worth stating once.

**What did NOT change:** the tenant boundary. A transcript from another domain
is still 403, and a session with a blank `ownerDomain` (pre-backfill,
unattributable) still fails closed for tenant scope — serving a whole
conversation *because* we can't say whose it is would be the worst version of
this endpoint. Both asserted by test, and mutation-verified.

## Milestones

| # | Milestone | Scope | Est. | Risk |
|---|-----------|-------|------|------|
| M1 | `AdminScope` core + `GET /api/admin/whoami` + flag | backend | 0.75d | Security-critical — the primitive everything else trusts |
| M2 | Adopt on domain-keyed routes (clients, tool-permissions, access-check, tenants migration) | backend | 0.75d | Low — mechanical once M1 lands |
| M3 | users + group-tags scoping (email→domain, tag ownership) | backend | 0.75d | Medium — tag-ownership rule is a judgment call |
| M4 | Analytics: `ownerDomain` + backfill + scoped list + `argsJson` redaction + trace policy | backend | 1.25d | **High** — schema change + backfill + privacy default |
| M5 | Cross-tenant matrix test (the enforcement mechanism) | backend | 0.5d | Medium — must fail on *new* unscoped routes, not just today's |
| M6 | Frontend: whoami probe, UserMenu + hub, copy fix, forbidden/empty states | frontend | 0.75d | Low |
| M7 | CLI: `aiplatform admin whoami` / `access-check` | cli | 0.25d | Low |

**Total: ~5 days.** Recent velocity (372 commits/14d, heavy on this subsystem)
supports it, but M4's backfill is the one that can overrun.

### M1 — `AdminScope` core (the primitive)

- `backend/admin/scope.py`: `AdminScope` dataclass, `assert_may()`,
  `filter_domains()`, `require_admin_scope` FastAPI dependency.
- Reuses `tenant_admin_domains()` (`auth/admin_roles.py:59`) — already exists,
  so resolution is ~15 lines, not a new parser.
- `GET /api/admin/whoami` → `{scope, domains}`; returns `scope:"none"` + 200 for
  a non-admin (never-silent: the frontend renders a clean state instead of
  interpreting a 403).
- ~~Flag `ADMIN_TENANT_SCOPE_ENABLED`~~ — **removed before shipping.** It was
  scoped as rollout protection, but the protection is illusory: env vars don't
  promote with code, so the flag's own failure mode (on in dev, missing in prod)
  is worse than what it guarded against. Authority comes from the
  `tenant-admin:{domain}` claim, which is the real gate.

**Acceptance:** platform / single-tenant / multi-tenant / none all resolve
correctly; `assert_may` denies by default; no env var can change the answer.

### M2 — Domain-keyed routes

`clients.py` (4 routes: list filters, get/put/delete assert), 
`tool_permissions_routes.py` (4: doc_id is email/domain/`*` — `*` is
platform-only), `access_routes.py` (1: target user must be in scope),
`tenants.py` (its bespoke `_require_tenant_admin` now delegates to the shared
`resolve_admin_scope` — it stays a function only because its domain arrives in
the request *body*, which a route dependency cannot see in time).

**Acceptance:** no route module computes scope on its own; `tenants.py` behaviour
is unchanged by the migration.

### M3 — users + group-tags

- `users_routes.py` (4): the target user's email domain must be in scope.
- `group_tags_routes.py` (2): a tenant admin may grant/revoke only tags their
  own tenant owns, and **never** `aitana-admin` or another domain's
  `tenant-admin:` tag — privilege escalation is the risk here.

**Acceptance:** a tenant admin cannot mint `aitana-admin`, cannot mint
`tenant-admin:other.com`, and cannot touch a user outside their domain.

### M4 — Analytics (largest)

1. Add `ownerDomain` to `ChatSessionIndex` + `create_session_index`.
2. Backfill script `scripts/backfill_session_owner_domain.py` (idempotent,
   resumable, dry-run default).
3. `list_sessions`: Firestore `where("ownerDomain","==",d)` for tenant scope.
4. **`argsJson` redaction** for any non-platform scope.
5. Trace endpoint tenant-scoped per the ruling above (was platform-only pending it).

**Acceptance:** a tenant admin sees zero rows from another domain; `argsJson`
never appears in a tenant-scoped response; backfill is re-runnable safely.

### M5 — Cross-tenant matrix (the thing that keeps this true)

A parametrised test enumerating **every route on the admin routers** and
asserting a `tenant-admin:a.com` caller gets zero `b.com` data. Built by
introspecting `router.routes` so a newly added unscoped route **fails CI** —
this is what stops the incremental-special-casing regression the design doc
warns about.

**Acceptance:** adding a deliberately unscoped test route makes the suite red.

### M6 — Frontend

Swap the `GET /api/admin/clients` role-probe in `UserMenu.tsx:49` and
`admin/page.tsx:74` for `whoami`; fix the misleading tenant-admin copy on the
tenants page; render explicit forbidden / scoped-empty states.

### M7 — CLI

`aiplatform admin whoami` and `admin access-check` (Click subcommand + httpx +
unit test each).

## Model Assignment

| Stage | Model | Why |
|-------|-------|-----|
| Planning | `claude-opus-4-8` (high) | Decomposition; the design doc holds the hard thinking. |
| M1, M5 execution | `claude-opus-4-8` (xhigh) + **adversarial self-verify** | Rubric scores these high-subtlety (security gate, un-bypassability). The rubric would suggest `claude-fable-5`; the session model is fixed to Opus, so the compensation is an explicit adversarial pass — try to bypass the gate — rather than a model swap. Recorded honestly so a later session knows it was a constraint, not a judgment. |
| M2, M3, M4, M6, M7 | `claude-opus-4-8` (xhigh) | Well-specified; M4's risk is the backfill, not subtlety. |
| Sub-agents (inventory/greps) | `claude-haiku-4-5` / `claude-sonnet-4-6` | Mechanical fan-out. |
| Evaluation | different model than executor where feasible | Cross-model diversity per the rubric. Report **every** finding with a confidence tag — Opus evaluators honour severity filters literally and will otherwise withhold. |

## Quality Gates

Per-milestone: `cd backend && make lint && make test-fast`.
Frontend milestones: `cd frontend && npm run quality:check`.

**Definition of done (from the design doc's verification requirement):** green
tests are necessary, not sufficient. Phase 1 is done when a **real
`tenant-admin` token** exercises every scoped endpoint against deployed dev via
`aiplatform`/curl and the cross-tenant matrix is confirmed on live data. Browser
verification is the user's step.

## Out of scope (deliberately)

- Phase 2 effective-access panel, Phase 3 IA, Phase 4 audit reader.
- The MCP iframe-context gate (`allow_context_writes`) — same delegation-blind
  bug class, tracked separately.
- Deciding the chat-transcript question above.
