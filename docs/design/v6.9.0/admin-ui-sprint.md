# Sprint Plan: ADMIN-UI — v6.9.0 admin/analytics implementation

## Summary

Build the admin/analytics **UI + missing endpoints** the v6.9.0 design docs call
for. Tenant-Admin (`/admin/tenants`) already shipped over the existing API; this
sprint adds the shell + the three remaining surfaces. Executed autonomously
(user reviews at the end); each milestone gated (lint/typecheck/build/tests) and
deployed to dev.

**Duration:** 4 milestones (~5–6 engineering-days, compressed to velocity)
**Scope:** Fullstack (frontend-heavy; new backend for M3/M4)
**Design docs:** [9.1](administration-overview.md) · [9.2](skill-administration.md) · [9.3](user-group-administration.md) · [9.5](analytics-and-reporting.md)
**Risk:** M4 is security-sensitive (mutates Firebase custom claims); M3 touches session data (access-scoping is the crux).

## Milestones

### M1 — Admin shell + navigation (~0.5d, frontend)
A gated `/admin` hub page linking the admin surfaces (Tenants ✅, Skill Studio,
Analytics, Users), + an Admin entry in the app nav shown only to admins. Makes
the admin pages discoverable instead of URL-only. **Accept:** `/admin` lists the
surfaces; non-admins see the "admins only" state; nav link visible to admins.

### M2 — Skill access-control editor in Studio (~1d, frontend + tiny backend)
Close the top 9.2 gap: the Studio `buildSaveBody` omits `accessControl`. Add an
AccessControlEditor (public / private / tagged / domain / specific + tag/domain/
email inputs) and include `accessControl` in the save body. Verify PUT
`/api/skills/{id}` persists it. **Accept:** a skill's access is settable from the
Studio and round-trips; a tagged skill created in the UI is gated correctly.

### M3 — Analytics: session/trace viewer (~1.5d, backend + frontend)
9.5 Phase 1. Backend: an access-scoped `GET /api/admin/analytics/sessions`
(list + search, reusing the `sessions_route` reconstruction) + trace read.
Frontend: `/admin/analytics` — browse/search past sessions, open a full trace
(messages, tool calls, delegations, model, tokens). **Accept:** an admin browses
+ searches sessions and opens a trace; access-scoped (tenant/user); no cross-tenant
leak. (Usage/cost dashboards over BigQuery = a later phase.)

### M4 — User/group admin (~1.5d, backend + frontend; security-sensitive)
9.3. Backend: `GET/POST/DELETE /api/admin/users/{email}/groups` (grant/revoke
group tags via `firebase_admin.auth.set_custom_user_claims`, deny-by-default,
audited) + effective-access lookup. Frontend: `/admin/users` — look up a user,
view + grant/revoke tags. **Accept:** an admin grants/revokes a group tag on a
user and it takes effect (after token refresh); every mutation is audited;
non-admins 403.

## Model Assignment

| Stage | Model | Why |
|-------|-------|-----|
| Execute M1 (shell/nav) | claude-opus-4-8 (xhigh) | Straightforward frontend. |
| Execute M2 (access editor) | claude-opus-4-8 (xhigh) | Frontend + a known backend contract. |
| Execute M3 (analytics) | claude-opus-4-8 (xhigh) | Access-scoping is the care point; deterministic. |
| Execute M4 (user/group) | claude-opus-4-8 (xhigh) | Security-sensitive (claims); careful + audited. |
| Evaluation | user (end-of-sprint review) | "I'll check them all once done." |

(User chose to run this session on Opus 4.8; honored.)

## Quality Gates (per milestone)
- Frontend: `npm run quality:check:fast` (lint + typecheck) + `npm run build`.
- Backend: `make lint && make test-fast`.
- Deploy to dev + verify live (seeded/reachable); the user reviews at the end.

## Success Criteria
- `/admin` shell + nav; Tenants (done), Skill access editor, Analytics viewer, User/group admin all reachable + functional on dev.
- Every new admin mutation deny-by-default (aitana-admin) with a 403 state; M4 mutations audited.
- All gates green; changes deployed.

## Notes
- Unified admin identity (9.1) is not refactored here (the hardcoded firestore.rules email stays for now) — these pages use the existing `aitana-admin` gate. The 9.1 consolidation is a separate follow-up.
- Reuses: `/api/admin/clients` (M-tenant, done), `sessions_route` reconstruction (M3), `set_custom_user_claims` (M4), the Studio Field/patterns (M1/M2).
