# v6.16.0 — Build Sequence

Admin-console work driven by one product requirement: **the admin console is to
be released to client-side tenant admins for inspection**, not just the platform
owner. v6.9.0 built the admin layer; this version makes it safely releasable to
someone who is not `aitana-admin`, and reorganises it around operator tasks.

## Ordering

| # | Doc | Priority | Est. | Depends on | Notes |
|---|-----|----------|------|------------|-------|
| 1 | [admin-console-tenant-readiness](admin-console-tenant-readiness.md) | P0 (Ph.1) / P1 (Ph.2–4) | ~8–11d | v6.9.0 admin suite ✅ | Phase 1 is the release gate — tenant scoping + cross-tenant data boundary. Phases 2–4 each independently shippable behind the same flag. |

## Timeline estimate

| Phase | Work | Est. | Status |
|-------|------|------|--------|
| 1 | Tenant scoping as a shared dependency (`AdminScope`), `whoami`, `argsJson` redaction, frontend probe swap, cross-tenant matrix tests | ~4d | Proposed — **release blocker** |
| 2 | Effective access — "What your users actually see" (motivating feature) | ~2.5d | Proposed |
| 3 | IA consolidation: 7 API-shaped cards → 5 task-shaped areas; Studio page gating | ~2d | Proposed |
| 4 | Audit reader (backend + endpoint + view) | ~1.5d | Proposed |
| — | CLI: `admin whoami` / `access-check` / `audit` | ~0.75d | Proposed |

## What ships in v6.16.0

- **Phase 1 (blocking):** `tenant-admin:{domain}` becomes a real, enforceable
  role. Today it has exactly one call site (`admin/tenants.py:109`) while every
  other admin route gates on `is_platform_admin` — so a client admin cannot even
  see the Admin link. One shared `AdminScope` dependency is adopted by all nine
  admin route modules at once, closing the cross-tenant reads on
  `/api/admin/clients` and `/api/admin/analytics/sessions` (including redacting
  tool `argsJson`, a derivative of private content).
- **Phase 2:** "What your users actually see" — the effective-access screen.
  `/api/admin/access/check` already computes tags + tool permissions with
  provenance; this adds the missing `enabled_skills` narrowing plane (the one
  admins bypass, which is why the admin's own skill list is wrong by
  construction) and renders the full decision.
- **Phase 3:** the console is reorganised into Your tenant · People & access ·
  Skills · Activity & audit · Platform.
- **Phase 4:** the audit trail becomes readable in-product, scoped to the tenant
  it concerns — `admin/audit.py` is write-only today.

## Dependency graph

```
v6.9.0 admin suite (implemented)
        │
        ▼
  Phase 1 — AdminScope + data boundary  ◄── release gate
        │
        ├──► Phase 2 — effective access ──► Phase 3 — IA consolidation
        │
        └──► Phase 4 — audit reader
```

Phase 2 depends on Phase 1 because resolving another user's effective access
must not be possible outside your own tenant. Phase 3 folds Phase 2's panel
into the "Your tenant" area, so it follows it.
