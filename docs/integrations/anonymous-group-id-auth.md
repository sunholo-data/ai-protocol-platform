# Anonymous group-ID auth — fork adoption howto

**Sprint reference:** [v6.2.0 sprint 2.11 design doc](../design/v6.2.0/anonymous-group-id-auth.md)
**For:** Forks deploying the platform to classroom / event / kiosk / customer-demo contexts where students or attendees don't have persistent accounts.

This howto walks a fork operator through enabling the fourth auth mode end-to-end: backend env var, frontend env var, deployment topology, two-frontend pattern, ops concerns (secret rotation, scale-out, no-PII contract).

---

## Quick start (LOCAL_MODE / workshop)

The platform's `make dev-local` target already wires this up for the workshop demo:

```bash
make dev-local
# Backend on :1956 with GROUP_AUTH_SIGNING_SECRET pre-set to a known
# dev value. Frontend on :3456 in LOCAL_MODE (defaults to the stub
# user; /group renders the friendly "not available" fallback).
```

To exercise the form path in dev, set the frontend env var:

```bash
# In frontend/.env.local
NEXT_PUBLIC_AUTH_MODE=anonymous_group_id
```

Restart the frontend (`npm run dev`); navigate to `/group`; type a code.

---

## Production deployment (Cloud Run)

### 1. Backend env vars

Both must be set on the backend Cloud Run service:

| Env var | Required? | Purpose |
|---|---|---|
| `GROUP_AUTH_SIGNING_SECRET` | **Yes** | HS256 signing key for the JWTs. Must be a long random string (≥32 bytes recommended). Rotating it invalidates all live tokens — admin's nuclear option. |
| `NEXT_PUBLIC_AUTH_MODE` | No (backend) | The backend reads token-shape from the JWT itself; it doesn't need this var. Sit it on the frontend only. |

Mint the secret once:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store via Cloud Secret Manager and mount as an env var on the service:

```bash
gcloud secrets create group-auth-signing-secret-prod \
  --replication-policy=automatic \
  --data-file=- < secret.txt
gcloud run services update platform-backend \
  --set-secrets=GROUP_AUTH_SIGNING_SECRET=group-auth-signing-secret-prod:latest \
  --region=europe-west1
```

### 2. Frontend env var

Set on the Cloud Run service that serves the Next.js frontend:

```bash
gcloud run services update platform-frontend-classroom \
  --set-env-vars=NEXT_PUBLIC_AUTH_MODE=anonymous_group_id \
  --region=europe-west1
```

Since `NEXT_PUBLIC_*` vars are baked into the client bundle at build time, the value lands in browsers on the next deploy.

### 3. Two-frontend topology (recommended)

The platform supports a single backend serving multiple frontends — each frontend chooses its own auth mode via its env var:

```
                       ┌──────────────────────────┐
                       │ platform-backend        │
                       │ (Cloud Run, Firebase     │
                       │  Admin SDK + group-auth) │
                       └────────────┬─────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
   │ platform-       │  │ platform-       │  │ aipla-classroom-    │
   │  frontend-admin  │  │  frontend-public │  │  frontend           │
   │ (Firebase auth)  │  │ (Firebase auth)  │  │ (anonymous_group_id)│
   │  → teachers use  │  │  → public users  │  │  → students         │
   │    this to       │  │                  │  │    arrive here      │
   │    create groups │  │                  │  │    via /group?code  │
   └──────────────────┘  └──────────────────┘  └─────────────────────┘
```

A teacher uses the **admin** frontend (Firebase auth) to call `POST /api/auth/group/create`, then shares the join URL — which points at the **classroom** frontend (`anonymous_group_id` mode). The two frontends talk to the same backend; the backend's token-shape dispatcher decides which verifier to run per request.

### 4. Per-fork hostnames

The backend's create endpoint generates a `join_url` from the request's `X-Forwarded-Host` (Cloud Run sets this). If your teacher-admin frontend is on `admin.example.com` and the classroom frontend is on `class.example.com`, set up a proxy or rewrite so the create endpoint returns the classroom URL.

Simplest: make the teacher's create call from the classroom frontend (with `X-Forwarded-Host: class.example.com` in the call), OR override the join_url generation in your fork.

---

## API reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/group/create` | Firebase JWT | Mint a group code. Body: `{title, skill_ids: [...], ttl_days?, max_concurrent_sessions?}`. Returns `{group_id, expires_at, join_url}`. |
| `POST /api/auth/group/join` | None (anonymous) | Exchange code for token. Body: `{group_id}`. Returns `{token, uid, expires_at}` or typed error. |
| `DELETE /api/auth/group/{id}` | Firebase JWT (creator-only) | Revoke a group. All live tokens become invalid. Returns 204. |
| `GET /api/auth/group/{id}` | Firebase JWT or group JWT | Return metadata only (NO member list). Returns 200 or 404. |

### Status-code map for `/join`

The seven-gate matrix from the design doc:

| Status | Reason | Frontend handling |
|---|---|---|
| 200 | Happy path | Persist token; route to / |
| 401 | Unknown / expired / revoked group (one message — privacy) | "Code not found, expired, or revoked. Ask your teacher." |
| 422 | Malformed body | Should never reach end users — caught by Pydantic |
| 429 | Rate-limit (10/min/IP default); `Retry-After` header | "Too many tries. Try again in Ns." |
| 503 | Per-group session cap (100/day default) | "Group at capacity. Try tomorrow or ask your teacher." |

---

## Configuration knobs

### Per-group (set at create-time)

| Field | Default | Notes |
|---|---|---|
| `ttl_days` | 30 | How long the code is valid. AIPLA typically sets shorter (per-lesson or per-week). |
| `max_concurrent_sessions` | 100 | Per-day cap on joins. Limits blast radius if a code leaks. |

### Per-deployment (env vars / module constants)

| Knob | Default | How to override |
|---|---|---|
| Rate-limit | 10 joins/min/IP | Construct `TokenBucketRateLimiter(capacity, refill_seconds)` in your fork; replace the module's `_state.rate_limiter`. |
| Token lifetime | 8 hours | `DEFAULT_TOKEN_LIFETIME_SECONDS` in `auth/group_id_auth.py`. After expiry the user must re-join with the code. |
| Code alphabet | `[A-Z2-9]` minus `0/O/1/I` | `_CODE_ALPHABET` in `auth/group_id_auth.py`. |
| Code length | 8 chars (`XXXX-XXXX`) | `_CODE_LEN_BEFORE_HYPHEN` + `_CODE_LEN_AFTER_HYPHEN`. |

---

## Security & no-PII contract

**The platform enforces no-PII by structure.** When a group token verifies:

- `User.email == ""` (explicit empty string, NOT `None`)
- `User.domain == ""`
- `User.display_name` doesn't exist on the User type (frontend stub displays nothing where a name would otherwise show)
- `User.auth_mode == "anonymous_group_id"` — signal for downstream code that wants to degrade gracefully

If a downstream skill / tool / loader needs an email, it must check explicitly:

```python
if not user.email:
    # Skill or tool needs to either skip this user or fall back to a
    # generic identity. Don't synthesise an email — that defeats the
    # whole point of anonymous mode.
    ...
```

**The signing secret is the root of trust.** Anyone who has it can mint valid tokens. Treat it like a database password:

- Store in Cloud Secret Manager.
- Rotate when staff leaves or after any suspected compromise.
- Rotation invalidates every live token instantly (intended behavior).
- Don't echo it in logs. The default error message names the env var by name, NOT its value.

**Group codes are short-lived AND rate-limited.** 8 chars from a 32-char alphabet = ~3.4 × 10¹¹ codes. At 10 joins/min/IP, brute-forcing one valid code takes >500,000 IP-years. Combined with the per-group session cap, a leaked code's blast radius is bounded.

---

## Ops concerns

### Multi-instance scale-out

The default `TokenBucketRateLimiter` is in-memory. **Single-instance Cloud Run (min=1, max=1) is fine.** Multi-instance needs an external store:

- **Sticky sessions** at the load balancer (cheapest if your traffic pattern allows).
- **Redis / Memorystore** for shared bucket state (write a `TokenBucketRateLimiter` subclass that delegates to Redis).
- **Per-instance generous limit** (set the limit higher per-instance; accept the multiplied-by-N total).

The session-cap counter has the same caveat — it's in-memory per process. The same external-store option applies.

### Logging

Every group action emits a structured log line with the caller's group_id but no PII. Sample lines:

```
group_auth: created group=PHYS-7K2N creator=teacher-uid ttl_days=7 skills=2 cap=100
group_auth: joined group=PHYS-7K2N uid=anon-PHYS7K2N-deadbeef session_n=3
group_auth: revoked group=PHYS-7K2N by uid=teacher-uid
```

For per-cohort research / analytics (AIPLA's case), pair this with sprint 2.14's tenant-ID span attribute — `tenant.group_id` lands on every OTel span emitted during the cohort's sessions, making BigQuery / Cloud Trace filtering trivial.

### Persistence

Groups + sessions are **in-memory** in v1. A backend restart loses all groups. For production deployments where this matters (most AIPLA-shaped use cases), back the state with Firestore — the design doc anticipates this:

- The `AnonymousGroupAuth` class is the single point of state mutation.
- Forks subclass it (or rebind the module-level `_state`) to read/write Firestore instead of dicts.
- The Pydantic models (`GroupRecord`, `JoinResult`) already match the Firestore document shape.

Reference impl for forks: see the `mcp_proxy` pattern in `protocols/mcp_proxy.py` — it switches between in-memory and Firestore based on `LOCAL_MODE`. Mirror that.

### Frontend bundle size

The frontend pieces (provider + page + helpers + lib) add ~350 LOC + a Pydantic-shaped wire-format. No new npm dependencies. The mode is fully tree-shakable in non-anonymous-group builds — webpack drops the branches that `isAnonymousGroupAuthMode()` returns false for.

---

## Migration path from a hand-rolled stub

If your fork already has a hand-rolled equivalent of this:

1. **Identify your existing code.** Likely shaped as a copy of `local_mode_stub.py` with a list of "allowed codes" and no rate-limit.
2. **Replace the bearer-token check.** The new `get_current_user` dispatcher in `auth/__init__.py` already accepts the JWT shape — delete your old check.
3. **Move your codes to `create_group(...)` calls.** Each former allowed-code becomes a `GroupRecord` with the right `skill_ids` list.
4. **Update your frontend to call `/group`.** Replace your custom code-entry UI with a redirect to the new page (or fork the new page if you want different copy).
5. **Verify the seven-gate matrix.** Run `pytest backend/tests/unit/test_group_id_auth.py backend/tests/api_tests/test_group_routes.py` against your deployment to catch any subtle gate skip in your fork.

---

## Open questions / future work

Carried from the design doc:

1. **Per-student opt-in display name.** Currently no display name at all. Some classrooms want "What should I call you?" without it being PII. Defer to a future sprint; the agent can ask within conversation and store the answer in session state.
2. **Group-code aliases.** Teacher creates `PHYS-7K2N` but slides say `physics2a`. Optional alias map. Defer.
3. **`auth_mode` in `forwardedProps`.** Skills could adapt their prompts when they know the user is anonymous (e.g. "you are an anonymous student in cohort X"). Small follow-up.
4. **Headless render preview for artefacts** (cross-references sprint 2.13). Useful in classrooms; punted to a future sprint.

---

## Related

- [Sprint 2.11 design doc](../design/v6.2.0/anonymous-group-id-auth.md) — full threat model + axiom alignment.
- [Sprint 2.12 budget-enforcement](../design/v6.2.0/budget-enforcement.md) — uses `User.group_id` from sprint 2.11 as the budget identity key.
- [Sprint 2.14 tenant-id-span-attribute](../design/v6.2.0/tenant-id-span-attribute.md) — drops `tenant.group_id` on every OTel span so per-cohort research data lands cleanly.
- [AIPLA ADR-001](https://www.sunholo.com/aipla/architecture.html#adr-001-student-identity-no-auth-anonymous-group-ids) — the originating request.
