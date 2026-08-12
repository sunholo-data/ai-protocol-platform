# Sprint Plan: ANON-GROUP-AUTH — v6.2.0 Sprint 2.11

## Summary

Implement the fourth auth mode (anonymous group-ID) as designed in
[anonymous-group-id-auth.md](anonymous-group-id-auth.md). This is the
AIPLA fork blocker for v0.1 (Jutland teacher demo 2026-05-27).

**Duration:** 2 focused days + 0.4d buffer (slack against the 8-calendar-day deadline)
**Scope:** Backend auth module + 4 routes + frontend chooser + group-join page + live smoke + howto
**Dependencies:** None — sits alongside existing auth modes
**Risk Level:** Medium-high — `SECURE_BY_CONSTRUCTION` axiom is -2 in the design; every gate enumerated must have an explicit pytest case
**Design Doc:** [anonymous-group-id-auth.md](anonymous-group-id-auth.md) — full design, axiom alignment, threat model, wire formats

## Velocity Context (7-day rolling)

- 61 commits, 22.5k insertions, ~4.25 sprint-days of code over 7 calendar days.
- Sprint 2.9 (multi-surface-rendering) ran 3 days; sprint 2.10 (a2ui-surface-context) ran 0.75d + 0.5d follow-up.
- Sprint 2.11 is comparable in shape to 2.10 (Protocol + endpoint + reference impl + tests + smoke).

## Milestone Breakdown

The design doc's Implementation Plan defines the four phases. Translating verbatim into milestones:

### M1 — Backend auth module + token + rate-limit + 7-gate tests (`backend`, ~0.6d)

**Files**
- `backend/auth/group_id_auth.py` (~150 LOC) — `create_group`, `join_group`, `verify_token`, `User` minting with `auth_mode="anonymous_group_id"` + `group_id`
- `backend/auth/group_rate_limit.py` (~80 LOC) — per-IP token bucket
- Pydantic wire-format models: `CreateGroupRequest`, `JoinGroupRequest`, `JoinGroupResponse`, `GroupRecord`, `JoinResult`
- `backend/tests/unit/test_group_id_auth.py` (~280 LOC) — token mint/verify, expiry, signature rotation, malformed token

**Acceptance criteria**
- Token shape (JWT HS256) exactly matches the design's spec: `sub`, `group_id`, `exp`, `iat`, `auth_mode`
- `User.email` and `User.display_name` are `None` for this mode (no-PII contract enforced at type level)
- Rate-limit: 10 joins/min/IP default, bucket refills over 60s
- Per-group session cap: 100/day default
- Group TTL: 30 days default; configurable per-create
- All 7 gates from the design's join-endpoint contract have explicit pytest cases:
  1. malformed body → 422
  2. unknown group_id → 401
  3. expired group → 401 with retry message
  4. revoked group → 401
  5. rate-limit exceeded → 429
  6. at-capacity (100/day) → 503
  7. happy path → 200 + token

**Risk:** Signing-secret handling — must be configurable via `GROUP_AUTH_SIGNING_SECRET` env var; missing-secret fail-loud at module import.

### M2 — Routes + auth chooser dispatch + permissions integration (`backend`, ~0.5d)

**Files**
- `backend/auth/group_routes.py` (~120 LOC) — 4 endpoints (POST create, POST join, DELETE group, GET group metadata)
- `backend/auth/__init__.py` — token-shape dispatcher in `get_current_user`: Firebase JWT vs HS256-our-secret vs stub-token vs 401
- `backend/auth/permissions.py` — `can_use_tool` falls back to `group/<group_id>` lookup when `user.auth_mode == "anonymous_group_id"`
- `backend/fast_api_app.py` — register `group_routes.router`
- `backend/tests/api_tests/test_group_routes.py` (~250 LOC) — TestClient end-to-end

**Acceptance criteria**
- `POST /api/auth/group/create` requires Firebase auth (teacher path); returns `{group_id, expires_at, join_url}`
- `POST /api/auth/group/join` requires NO auth (anonymous endpoint); returns `{token, expires_at, uid}` or 4xx
- `DELETE /api/auth/group/{group_id}` requires Firebase auth + creator-uid match
- Token-shape dispatcher: existing Firebase + stub callers keep working with zero changes
- Group-level permissions: teacher's allowed-skill list at create-time defines what the group's members can access; a student calling a skill NOT in the allow-list → 403
- E2E test: teacher (Firebase auth) creates group → student (no auth) joins → student calls a permitted skill → 200; student calls a non-permitted skill → 403

**Risk:** `get_current_user` dispatcher must remain back-compat — any regression breaks the entire platform's auth path. Snapshot-test against the existing Firebase + stub flows.

### M3 — Frontend chooser + group-join page (`frontend`, ~0.5d)

**Files**
- `frontend/src/contexts/AnonymousGroupAuthProvider.tsx` (~120 LOC) — state machine `idle | joining | joined | expired`; stores token in `sessionStorage` (not localStorage)
- `frontend/src/contexts/AuthChooser.tsx` (~30 LOC patch) — adds the `NEXT_PUBLIC_AUTH_MODE === "anonymous_group_id"` branch
- `frontend/src/app/group/page.tsx` (~80 LOC) — single-input "Enter group code" + Join button + error messaging
- `frontend/src/contexts/__tests__/AnonymousGroupAuthProvider.test.tsx` (~120 LOC) — state machine transitions
- `frontend/src/app/group/__tests__/page.test.tsx` (~80 LOC) — join flow + error rendering

**Acceptance criteria**
- Provider state machine transitions covered: idle → joining → joined; joined → expired (on 401 from any downstream call); expired → idle (clears storage)
- `fetchWithAuth` reads the bearer from `sessionStorage` when `auth_mode === "anonymous_group_id"` is active
- Join page input accepts uppercase + lowercase + hyphens, normalizes to the canonical group_id shape before POST
- Error states render clean prose: "Code expired", "Code revoked", "Try again in N seconds" (rate-limit), "Group at capacity"
- Existing Firebase + LOCAL_MODE auth paths unchanged (snapshot test)

**Risk:** `sessionStorage` is per-tab; explicitly documented as a feature (anonymous sessions shouldn't survive tab close). Mention in the howto.

### M4 — Live smoke + howto doc + audit-table update + commit (`fullstack`, ~0.4d)

**Files**
- `docs/integrations/anonymous-group-id-auth.md` (~150 LOC) — fork adoption howto: env vars, two-frontend deployment pattern, the PII rule, the recovery story ("ask the teacher")
- `docs/talks/ai-ui-protocol-stack.md` — flip the sprint 2.11 audit-row from 📋 to ✅
- `.dev-logs/anon-group-auth-smoke.png` — chrome-devtools MCP screenshot of the working flow

**Acceptance criteria**
- Live smoke: tab A (Firebase auth) creates a group → copies code → tab B (incognito) opens `/group`, pastes code, joins → tab B calls a skill via `demo-researcher` → response renders
- Revocation smoke: tab A deletes the group → tab B's next request → 401 banner
- Howto covers: NEXT_PUBLIC_AUTH_MODE config, signing-secret rotation, the in-memory rate-limit scale-out caveat (single-instance Cloud Run OK; multi-instance needs Redis or sticky sessions)
- Talk-doc audit row updated with the verification-log entry pattern from sprint 2.10's row

**Risk:** Chrome-devtools MCP smoke needs the dev server up; pre-restart before this milestone if servers were killed during M1–M3.

## Day-by-Day (2-day plan)

| Day | Morning | Afternoon |
|---|---|---|
| 1 | M1: auth module + Pydantic + rate-limit + token mint/verify tests | M1 cont: 7-gate join-endpoint tests; close M1 |
| 2 | M2: routes + dispatcher + permissions + E2E tests; close M2 | M3: frontend provider + chooser + join page + tests; close M3 |
| (slack) | M4: smoke + howto + audit-table; close sprint | Buffer for unknowns / iteration |

The 2-day budget assumes no architectural surprises. If M1 takes >0.8d, pause and reassess scope (the 7-gate test matrix is the most likely place to grow).

## Quality Gates (per milestone close)

```bash
# Backend (M1 + M2)
cd backend && make lint && make test-fast

# Frontend (M3)
cd frontend && npm run quality:check

# Full pre-push parity (M4)
cd backend && make lint && make test-fast
cd frontend && npm run quality:check
```

## Push Policy

- Commit at each milestone close (4 commits expected).
- DO NOT push until user confirms — per project pre-push review convention.
- Final smoke verification via chrome-devtools MCP before requesting push.

## Open Questions (carried from design doc)

1. Per-student opt-in display name — deferring to v2.
2. Group-code aliases — deferring.
3. Multi-instance Cloud Run rate-limit storage — documented as future work in M4 howto.
4. `auth_mode` flowing into AG-UI `forwardedProps` — small follow-up post-sprint.

Resolution: none of these blocks the 2026-05-27 demo.
