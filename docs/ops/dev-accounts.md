# Dev Accounts (your-project-id)

Purpose: record the Firebase uids, emails, and `groupTags` we use for local +
cloud-dev testing. Firebase uid is a non-secret public identifier within a
Firebase project — safe to commit. Never commit access tokens or JWTs.

## Dev owner — Mark

| Field       | Value                                                          |
|-------------|----------------------------------------------------------------|
| email       | `owner@yourcompany.com`                                           |
| uid         | `uG9Cjk6uMLY7n6drCLGyHrHjik42`                                  |
| group_tags  | `["aitana-admin"]`                                              |
| role        | Sprint lead, seed-script owner-uid, test-matrix "mark" persona  |

**Looking up a uid by email** (requires `gcloud auth application-default login` once):

```bash
cd backend && GOOGLE_CLOUD_PROJECT=your-project-id uv run python -c \
  'import firebase_admin; firebase_admin.initialize_app(); \
   from firebase_admin import auth; \
   print(auth.get_user_by_email("owner@yourcompany.com").uid)'
```

> The frontend uses the modular Firebase SDK v9+ ([frontend/src/lib/firebase.ts](../../frontend/src/lib/firebase.ts)),
> so there is **no** `firebase` global in the browser console — `firebase.auth().currentUser.uid`
> will throw `ReferenceError: firebase is not defined` even when signed in. Use the admin-SDK
> command above instead.

Then set the custom claim (gives the user `aitana-admin` group membership
so `accessControl.type == "tagged"` end-to-end tests can round-trip):

```bash
uv run python -c 'import firebase_admin; firebase_admin.initialize_app(); \
  from firebase_admin import auth; \
  auth.set_custom_user_claims("<uid>", {"groupTags": ["aitana-admin"]})'
```

Custom claims only land on freshly minted tokens. After running the command,
sign out + back in (or force-refresh the ID token) so the next request carries
the new claim.

**Verify the claim is on the fresh token** by hitting `GET /api/auth/whoami`.
It echoes the caller's verified uid/email/domain/`groupTags` — if `groupTags`
includes what you just set, the token is carrying it.

Two ways to exercise it:

```bash
# 1. Automated (preferred) — round-trips a dedicated smoke-test user end-to-end:
cd backend && make smoke-auth
# or, bundled with the rest of the deployed smoke checks:
./scripts/smoke-deployed.sh dev all auth

# 2. Manual (if you need to verify your own account) — copy the Bearer token
#    from any /api/proxy/... request in DevTools → Network, then:
curl -sS -H "Authorization: Bearer <paste-jwt>" \
  https://<frontend-url>/api/proxy/api/auth/whoami | jq
```

Expected body:

```json
{"uid":"<your-uid>","email":"<you>@yourcompany.com","domain":"yourcompany.com","groupTags":["aitana-admin"]}
```

The automated path lives in [backend/scripts/whoami_smoke.py](../../backend/scripts/whoami_smoke.py)
and is wrapped by the pytest integration test
[backend/tests/integration/test_whoami_deployed.py](../../backend/tests/integration/test_whoami_deployed.py)
(gated on `WHOAMI_SMOKE_ENV` so it stays out of `make test-fast`). It uses a
dedicated Firebase user (`whoami-test@yourcompany.test`) whose password is
rotated on every run — no persistent secret to manage.

**Prerequisite:** the dev Firebase project must have email/password sign-in
enabled. This is captured in Terraform
([infrastructure/environments/dev/auth_config.tf](https://github.com/sunholo-data/multivac-aitana/blob/main/infrastructure/environments/dev/auth_config.tf))
so the setting is reproducible — no manual Firebase console clicks required
when standing up a fresh dev project.

Unauthenticated callers get a JSON `401` (not a Next 404) — smoke-tested on
every deploy, see [scripts/smoke-deployed.sh](../../scripts/smoke-deployed.sh).

## Using the uid

Seed scripts that need an owner id:

```bash
# seed_skills.py (AUTH-PERMISSIONS M2)
uv run python scripts/seed_skills.py --owner-uid <mark-uid>

# seed_tool_permissions.py (AUTH-PERMISSIONS M3 — not yet landed)
uv run python scripts/seed_tool_permissions.py --domain yourcompany.com --wildcard
```

## Adding a new dev account

1. Add a row to the table above with email + uid + `group_tags`.
2. Run `auth.set_custom_user_claims(uid, {"groupTags": [...]})` to match.
3. Have the user sign in once to confirm the claim lands on the token.

## Related

- Auth design: [auth-and-permissions.md](../design/v6.0.0/auth-and-permissions.md)
- Resource access: [resource-access-control.md](../design/v6.0.0/resource-access-control.md)
- Sprint: [auth-and-permissions-sprint.md](../design/v6.0.0/auth-and-permissions-sprint.md)
