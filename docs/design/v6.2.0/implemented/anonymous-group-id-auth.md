# Anonymous group-ID auth — fourth auth mode

**Status**: Proposed
**Priority**: P1 (blocks AIPLA fork v0.1 — Jutland teacher demo 2026-05-27)
**Estimated**: ~2 days (auth provider + rate-limit + token shape + tests + docs)
**Scope**: Backend auth — new module alongside `firebase_auth.py` + `local_mode_stub.py`. Frontend already issues the bearer token via `fetchWithAuth`; only the chooser logic changes.
**Dependencies**: None — sits alongside existing auth modes.
**Surfaced by**: AIPLA fork [ADR-001 — student identity, no auth, anonymous group IDs](https://www.sunholo.com/aipla/architecture.html#adr-001-student-identity-no-auth-anonymous-group-ids).
**Created**: 2026-05-19

---

## Problem Statement

Today the platform has three auth modes:

| Mode | Identity source | When |
|---|---|---|
| `firebase_auth` | Firebase JWT (verified by Identity Platform) | Production / `your-project-id-*` deployments |
| `identity_platform` | OIDC via Google Identity Platform | Enterprise + workspace SSO |
| `local_mode_stub` | Hardcoded `local-mode-stub-token` → `workshop-user` | Workshop / template dev mode |

All three assume **persistent accounts** — a stable `uid` the user can sign in to, with email + display name. That's wrong for an entire class of deployments:

| Deployment | Why persistent accounts fail |
|---|---|
| **Classroom** (AIPLA) | Schools won't let third parties hold student PII. Teachers don't have time to provision 30 accounts. Students rotate every year. |
| **Trade-show kiosk** | Walk-up demos; no signup friction tolerable; account orphans accumulate. |
| **Internal workshop** | Attendees use it once; signup overhead exceeds session length. |
| **Customer demo** | Sales reps want to share a link, no auth wall. |

All four want the same shape: **a short-code the host hands out, anyone who has the code joins a shared session-space, no PII, no recovery, server-side trust that the bearer holds a valid code right now.**

AIPLA is the first concrete consumer asking for it; their need-by is 2026-05-27 (Jutland teacher demo). But this is broadly useful — every template fork that targets classroom/event/kiosk patterns will reinvent it.

### Current state

- `backend/auth/local_mode_stub.py` (85 LOC): hard-coded token, hard-coded uid, single user. Closest existing shape — but accepts ANY caller knowing the secret string, no group concept, no rate limit, no expiry.
- `backend/auth/firebase_auth.py` (108 LOC): full Firebase JWT verification. Stable uid, email, display_name.
- `backend/auth/permissions.py`: per-uid tool permissions. Group-id auth must produce a uid the permission system can key on.
- Frontend: `lib/apiClient.fetchWithAuth` reads the bearer token from `localStorage` (`firebase` mode) or a static stub (`local-mode` mode). Adding a third bearer source is one branch.

### Impact

- **AIPLA blocked at v0.1**: cannot demo to teachers without this. Their fallback ("copy + adapt the `local_mode_stub`") would ship a wide-open back door to production.
- **Every classroom/event fork reinvents the wheel**: each one writes its own short-code provider, each one gets the rate-limit + token-shape + replay-protection wrong differently.
- **Template gap is visible**: the auth axis is "Firebase, Identity Platform, or no auth at all" — there's nothing in between.

---

## Goals

**Primary Goal:** Ship a fourth auth mode `anonymous_group_id` that authenticates a caller as "holder of group-code `<X>`" without persistent accounts, without PII, with rate-limit + replay protection, and produces a `User` object the rest of the stack already understands.

**Success Metrics:**
- A teacher creates a group code (one HTTP call); the code is a short pronounceable string (6–8 chars).
- A student who knows the code can call `POST /api/auth/group/join` and get a bearer token. Token contains: `group_id`, derived synthetic `uid` (`anon-<group>-<random>`), `expires_at`. No email, no name.
- The rest of the platform (skills, sessions, A2UI, MCP) works for that bearer the same way it works for a Firebase user — only the `User.uid` shape differs (synthetic).
- Tool permissions key on `group_id` (not synthetic uid) so all students in a group share the same skill access list.
- Rate-limit: ≤ 10 joins/min/IP, ≤ 100 active sessions/group/day. Configurable.
- AIPLA's Jutland demo runs on this provider, no shimming, by 2026-05-27.

**Non-Goals:**
- Per-student identity within a group. Group-id mode is intentionally shared — pseudonymous within the cohort. Forks that want per-student identity should use one of the existing auth modes.
- Account recovery, password reset, email verification. None apply to anonymous codes.
- Cross-group session sharing. Each group is isolated.
- Replacing Firebase / Identity Platform. This is the fourth mode, not a migration.
- Per-student PII storage. The whole point is no-PII; if a fork needs PII later it switches modes.

---

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Joining via short code is the lowest-friction auth path the platform has ever supported. |
| 2 | EARNED TRUST | +1 | Teachers can show parents "we hold no student PII" — the platform enforces it structurally. |
| 3 | SKILLS, NOT FEATURES | 0 | Neutral — same skills surface for all auth modes. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model-routing implications. |
| 5 | GRACEFUL DEGRADATION | +1 | If the group code is expired or the rate limit fires, the caller gets a clean 401 with a recovery message ("ask your teacher for a fresh code"). |
| 6 | PROTOCOL OVER CUSTOM | 0 | Neutral — short-code session join is a pattern, not a published spec. We document our wire format so forks don't drift. |
| 7 | API FIRST | +1 | Two well-typed endpoints (`POST /api/auth/group/create`, `POST /api/auth/group/join`). Forks reuse both. |
| 8 | OBSERVABLE BY DEFAULT | +1 | `group_id` lands on every span via the sprint 2.14 tenant-id hook — researchers can query "what did cohort X do last lesson". |
| 9 | SECURE BY CONSTRUCTION | **-2** | Anonymous auth is a new attack surface. Honestly the largest -negative in this design. Mitigations below. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Frontend chooser picks the auth provider; everything else is one `fetchWithAuth` call. |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ (tight but defensible) |

**Conflict justifications:**

- **#9 SECURE BY CONSTRUCTION (-2):** Anonymous auth is structurally more risky than account-backed auth. There is no email-recovery to fall back to, no SMS to verify the human, no rate-limit on a per-user basis. Honest mitigations:
  - **Group codes are short-lived**: default TTL 30 days, configurable per-group at creation time (AIPLA wants per-class so likely shorter).
  - **Code shape is high-entropy enough**: 8 alphanumerics minus ambiguous (`0/O/1/I`) = ~38^8 ≈ 4.4 × 10¹², not enumerable at our rate-limit (≤10/min/IP).
  - **Rate-limit at the join endpoint**: IP-based, configurable. Bot-net stuffing remains a risk; mitigation is short codes + monitoring (#8 axiom), not magic.
  - **Per-group active-session cap**: default 100/day, configurable. Limits blast radius of one leaked code.
  - **Synthetic UID is non-reversible**: `anon-<group_id_hash>-<random32>`. Doesn't leak the group_id directly.
  - **No PII collection enforced at the model layer**: `User` returned by this provider has `email=None`, `display_name=None`. Code paths that demand email (some skills' loaders) must check and degrade gracefully — same shape we already need for `LOCAL_MODE`.
  - **Group codes do NOT survive a backend restart in LOCAL_MODE**: they live in InMemoryFirestoreClient (matches the stub-token's lifetime).
  - **Tool permissions key on `group_id`, not the synthetic uid**: so admin actions on the group apply to all current + future students. A leaked code with bad permissions can be revoked by deleting the group.

Net: more attack surface than zero, but every consumer with this deployment shape will build SOMETHING and most will get it wrong. Centralising the implementation is the safer path.

---

## Standards Compliance Check

- **Bearer-token format**: We mint signed JWTs (HS256, server-side secret) so the rest of the stack already validates them. JWT body has the synthetic uid + group_id + expires_at — same shape Firebase tokens produce. **Not** OIDC compliant; this is an internal token.
- **Rate-limiting**: Apply at the `POST /api/auth/group/join` endpoint via FastAPI middleware. Match the shape used by `mcp_proxy`'s gating (Firebase + skill-allowlist) — same memory model, different axis.
- **No new dependency required**: `pyjwt` is already in the backend's dep tree (transitive via Firebase). HS256 secret lives in `GROUP_AUTH_SIGNING_SECRET` env var.

---

## Design

### Overview

```
┌────────────────────────────────────────────────────────────────┐
│ TEACHER FLOW                                                   │
│                                                                │
│  Teacher (Firebase auth)                                       │
│     │                                                          │
│     ▼ POST /api/auth/group/create                              │
│  { "title": "Physics 2A", "ttl_days": 7, "skill_ids": [...] }  │
│     │                                                          │
│     ▼ 201 Created                                              │
│  { "group_id": "PHYS-7K2N", "join_url": "...", "expires_at" }  │
└────────────────────────────────────────────────────────────────┘
                              │ teacher pastes group_id into chat /
                              │ projector / hand-written on board
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ STUDENT FLOW                                                   │
│                                                                │
│  Student (no account)                                          │
│     │                                                          │
│     ▼ POST /api/auth/group/join { "group_id": "PHYS-7K2N" }    │
│     ▼ (rate-limit gate, group-exists gate, group-active gate)  │
│  { "token": "<jwt>", "expires_at": "...", "uid": "anon-..." }  │
│     │                                                          │
│     ▼ Authorization: Bearer <jwt>                              │
│  → /api/skills/* → uses User{uid=anon-..., group_id=PHYS-7K2N} │
│  → /api/skill/{id}/stream → group_id lands on every OTel span  │
└────────────────────────────────────────────────────────────────┘
```

### Backend Changes

**1. New auth module** `backend/auth/group_id_auth.py` (~150 LOC).

Exposes:
- `create_group(title, skill_ids, ttl_days, creator_uid) -> GroupRecord` (called by teacher endpoint).
- `join_group(group_id, client_ip) -> JoinResult` (called by student endpoint). Rate-limited.
- `get_current_user_from_group_token(token) -> User` (the `Depends` for protected routes).

Token shape:
```python
{
  "sub": "anon-PHYS-7K2N-9f4d2b1a",   # synthetic uid
  "group_id": "PHYS-7K2N",
  "exp": 1729012345,                   # expires_at
  "iat": 1726334245,                   # issued_at
  "auth_mode": "anonymous_group_id",   # for downstream routing
}
```

User object:
```python
User(
  uid="anon-PHYS-7K2N-9f4d2b1a",
  email=None,                          # explicitly None — degrade-gracefully signal
  display_name=None,
  domain=None,
  auth_mode="anonymous_group_id",      # NEW field, optional for back-compat
  group_id="PHYS-7K2N",                # NEW field, optional for back-compat
)
```

**2. New routes** `backend/auth/group_routes.py` (~120 LOC).

- `POST /api/auth/group/create` (requires Firebase auth; teacher action). Body: `{title, skill_ids: [...], ttl_days?, max_concurrent_sessions?}`. Returns: `{group_id, expires_at, join_url}`.
- `POST /api/auth/group/join` (no auth required; rate-limited). Body: `{group_id}`. Returns: `{token, expires_at, uid}` or 401 with retry guidance.
- `DELETE /api/auth/group/{group_id}` (requires Firebase auth + creator match). Revokes all tokens minted under this group.
- `GET /api/auth/group/{group_id}` (Firebase or group-token; returns metadata, not the list of joined uids — privacy).

**3. Auth chooser updates** `backend/auth/__init__.py`:

`get_current_user` becomes a dispatcher that inspects the bearer token shape:
- JWT signed by Firebase → existing `firebase_auth.verify`.
- JWT signed by our HS256 secret with `auth_mode=anonymous_group_id` → `group_id_auth.verify`.
- `local-mode-stub-token` literal → existing `local_mode_stub.verify`.
- Anything else → 401.

**4. Permissions integration** `backend/auth/permissions.py`:

`can_use_tool(user, tool_name)` checks:
- If `user.auth_mode == "anonymous_group_id"`: look up `tool_permissions` under `group/<group_id>` (group-level grants); fall back to global permissions.
- Otherwise: existing per-uid lookup.

**5. Rate-limiting** `backend/auth/group_rate_limit.py` (~80 LOC).

In-memory token bucket per IP (mirrors `mcp_proxy`'s session counter). For LOCAL_MODE deployments and small workshops this is fine; for AIPLA's pilot scale (~hundreds of students) still fine. Cloud Run multi-instance scale-out would need Redis; flag as future-work.

### Frontend Changes

**1. New auth provider** `frontend/src/contexts/AnonymousGroupAuthProvider.tsx` (~120 LOC).

Mirrors `LocalAuthProvider` shape. State machine: `{idle, joining, joined, expired}`. Stores token + group_id in `sessionStorage` (NOT localStorage — group sessions end with the tab).

**2. AuthChooser update** `frontend/src/contexts/AuthChooser.tsx` (~30 LOC patch).

Existing chooser picks between `FirebaseAuthProvider` + `LocalAuthProvider` based on `NEXT_PUBLIC_LOCAL_MODE`. Add a third branch: `NEXT_PUBLIC_AUTH_MODE=anonymous_group_id` → `AnonymousGroupAuthProvider`.

**3. Group-join UI page** `frontend/src/app/group/page.tsx` (~80 LOC).

Single-input "Enter group code" + Join button. On success, stores token, redirects to `/`. On failure, shows clean error: "Code expired. Ask your teacher for a new one."

**4. `fetchWithAuth`** unchanged — it reads the bearer from whichever provider is active.

### API surface

```
POST /api/auth/group/create
Authorization: Bearer <firebase-id-token>
Content-Type: application/json
{ "title": "Physics 2A", "skill_ids": ["physics-tutor", "lab-helper"],
  "ttl_days": 7, "max_concurrent_sessions": 100 }

→ 201 Created
{ "group_id": "PHYS-7K2N", "expires_at": "2026-05-26T12:00:00Z",
  "join_url": "https://platform.example/group?code=PHYS-7K2N" }

POST /api/auth/group/join
Content-Type: application/json
{ "group_id": "PHYS-7K2N" }

→ 200 OK
{ "token": "eyJhbGc...", "expires_at": "...", "uid": "anon-PHYS-7K2N-9f4d2b1a" }

→ 401 Unauthorized                                  (group expired/missing)
→ 429 Too Many Requests                             (rate-limited)
→ 503 Service Unavailable                           (group at max_concurrent)
```

---

## Implementation Plan

### Phase 1 — Backend auth module + tests (~0.6d)

- `backend/auth/group_id_auth.py`: `create_group`, `join_group`, `verify`, `User` minting.
- `backend/auth/group_rate_limit.py`: per-IP token bucket.
- Pydantic models for the wire shapes.
- Tests: gate-by-gate (rate-limit, expiry, group-exists, group-active-cap, replay-after-revoke, malformed token, wrong-signature).

### Phase 2 — Routes + chooser dispatch (~0.5d)

- `backend/auth/group_routes.py`: 4 endpoints + Firebase-auth gate on create/delete.
- `backend/auth/__init__.py`: token-shape dispatcher; back-compat with existing Firebase + stub paths.
- `permissions.py`: group-level permission lookup.
- Tests: end-to-end via TestClient — teacher creates group → student joins → student calls a skill → skill responds. Plus revocation flow.

### Phase 3 — Frontend chooser + group-join page (~0.5d)

- `AnonymousGroupAuthProvider` + `AuthChooser` extension.
- `/group` page: single input + Join button + error messaging.
- Vitest cases for the provider state machine + the join page.

### Phase 4 — Smoke + docs (~0.4d)

- Live smoke via chrome-devtools MCP: teacher creates group → student joins → asks a question → response includes `group_id` in OTel attributes (once sprint 2.14 lands; before that, smoke just on the auth path).
- Howto doc at `docs/integrations/anonymous-group-id-auth.md` — how forks adopt this mode + per-fork config knobs.
- Audit-table row in `docs/talks/ai-ui-protocol-stack.md` — mark this as a supported auth axis.

---

## Migration & Rollout

- **Backward compatible**: existing Firebase + LOCAL_MODE paths unchanged. New mode opts in via `NEXT_PUBLIC_AUTH_MODE` env var (frontend) + `GROUP_AUTH_ENABLED` env var (backend).
- **No DB migration**: groups live in Firestore under `groups/{group_id}`. Tool permissions under `tool_permissions/group/{group_id}`.
- **AIPLA fork path**: fork sets `NEXT_PUBLIC_AUTH_MODE=anonymous_group_id`, deploys the frontend behind a sub-domain, teachers use the existing Firebase auth path against the same backend to create groups. Same backend, two frontends — clean separation.
- **LOCAL_MODE compatibility**: when `LOCAL_MODE=1`, groups live in InMemoryFirestoreClient. The stub-token path keeps working — they're not exclusive.

---

## Testing Strategy

### Backend (pytest)

- 7-gate matrix on `POST /api/auth/group/join`: malformed body, unknown group, expired group, revoked group, rate-limited (10/min/IP exceeded), at-capacity (100 sessions/day), happy path.
- Token verification: valid token, expired token, wrong-signature, wrong-issuer, missing `auth_mode` claim.
- Permissions integration: group-level grant takes precedence over global; missing group permission falls through to global; revoking the group invalidates all member tokens (verified via 401 on next call).
- Rate-limit: 11 joins from one IP in 60s → 429 on the 11th; bucket refills.
- Concurrent-session cap: 101st join in a day → 503.

### Frontend (Vitest)

- Provider state machine transitions: idle → joining → joined; joined → expired; expired → idle.
- `fetchWithAuth` reads the right token when `auth_mode === "anonymous_group_id"`.
- Join page: valid code → join; invalid code → error; rate-limited → cool-down message.

### Manual / live smoke

- Two-tab demo: tab A signs in with Firebase, creates group, copies code. Tab B (incognito) opens `/group`, pastes code, joins, calls a skill, sees response.
- Revocation: tab A deletes group, tab B's next request → 401 with "code revoked" message.

---

## Security Considerations

(See axiom #9 conflict above. Headline mitigations consolidated here for the security-review reader.)

- **Anonymous = no PII collection**: `User.email` and `User.display_name` are `None` for this mode. Code paths that require email must check explicitly; skill loaders already need this for LOCAL_MODE so the pattern is established.
- **Code entropy**: 8 chars from a 38-char alphabet (alphanumeric minus ambiguous) ≈ 4.4 × 10¹² codes. At 10 joins/min/IP rate limit, brute-forcing one valid code takes >500,000 IP-years.
- **Per-IP rate limit**: 10 joins/min default. Configurable per-deployment.
- **Per-group session cap**: 100 active/day default. Limits the blast radius of one leaked code.
- **Group TTL**: 30 days default. AIPLA likely sets shorter (per-class).
- **Revocation**: `DELETE /api/auth/group/{id}` invalidates all minted tokens by removing the group from the lookup; verification fails closed (no group → no auth).
- **Token NOT a refresh token**: it expires when the group expires OR after `max_token_lifetime` (configurable, default 8h). No refresh endpoint — caller re-joins after expiry.
- **Signing secret rotation**: `GROUP_AUTH_SIGNING_SECRET` env var; rotation invalidates all live tokens (expected behaviour — admin nuclear option).
- **No cross-group leakage**: synthetic uid prefix encodes the group hash; permissions check the `group_id` claim, not just the uid.
- **Logging**: every join attempt logs `{group_id, client_ip, result, reason}` — observable but never logs PII (because there is none).

---

## Open Questions

1. **Per-student opt-in display name?** Some classroom skills want "What should I call you?" without it being PII. Trade-off: nicer UX vs first crack in the no-PII wall. Recommend leaving out of v1; let the agent ask within the conversation, write to session state, not auth.
2. **Group code aliases?** Teacher creates `PHYS-7K2N` but their slides say `physics2a`. Optional alias map in v2.
3. **Scale-out rate-limit storage?** In-memory works for single-instance Cloud Run with min=1, max=2. Multi-instance needs Redis or Memorystore; AIPLA's pilot probably doesn't hit this, but document the path.
4. **Should `auth_mode` flow into AG-UI `forwardedProps`?** So skills can detect "this is an anonymous classroom user" and adapt prompts? Likely yes, small follow-up.

---

## Related Documents

- [AIPLA ADR-001](https://www.sunholo.com/aipla/architecture.html#adr-001-student-identity-no-auth-anonymous-group-ids) — the request that surfaced this.
- [backend/auth/local_mode_stub.py](../../../../backend/auth/local_mode_stub.py) — closest existing pattern; this design parallels its structure.
- [budget-enforcement.md](../budget-enforcement.md) — sprint 2.12, depends on this for the per-cohort identity in budget keys.
- [tenant-id-span-attribute.md](../tenant-id-span-attribute.md) — sprint 2.14, the `group_id` lands on every span via that hook.
- [ai-ui-protocol-stack.md](../../../talks/ai-ui-protocol-stack.md) — talk audit row marks anonymous-group-id auth as ✅ now that it's shipped (sprint 2.11, 2026-05-19).
