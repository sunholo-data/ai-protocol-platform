# Auth smoke testing — the whoami round-trip

> One-stop reference for the authenticated `/api/auth/whoami` smoke probe,
> how it verifies custom-claim round-trips, and the gotchas we hit while
> wiring it up (so we don't rediscover them next env-cut).

## What it does

Rotates a dedicated Firebase user's password, signs in via Identity
Toolkit REST, and asserts that the backend's verified identity (uid,
email, `groupTags`) matches what the admin SDK set. Proves the full chain:

```
 admin SDK set_custom_user_claims
   → Firebase mints fresh ID token via signInWithPassword
   → Next.js /api/proxy forwards Authorization: Bearer
   → FastAPI Depends(get_current_user) verifies JWT
   → /api/auth/whoami echoes claims back
```

## How to run

```bash
# Direct:
cd backend && uv run python scripts/whoami_smoke.py --env dev

# Makefile wrapper (equivalent):
cd backend && make smoke-auth

# Bundled with deployed smokes:
./scripts/smoke-deployed.sh dev all auth

# As a pytest integration test:
cd backend && WHOAMI_SMOKE_ENV=dev uv run pytest tests/integration/test_whoami_deployed.py -v
```

Expected output:

```
OK   https://platform-frontend-...a.run.app/api/proxy/api/auth/whoami -> 200
     uid=<uid> groupTags=['aitana-admin-test']
```

## Design decisions

- **Rotate password per run, no persistent secret.** The smoke user
  `whoami-test@yourcompany.test` is created once; every run generates a
  fresh `secrets.token_urlsafe(24)`, updates the user, signs in. This
  avoids any need for Secret Manager IAM, key files, or custom token
  signing (see the "signBlob trap" below).
- **Dedicated `.test`-TLD user, not a real account.** RFC 2606 reserves
  `.test` — no deliverability, no collision with production users.
- **Password sign-in enabled per env via Terraform.** Captured in
  [`multivac-aitana:infrastructure/environments/*/auth_config.tf`](https://github.com/sunholo-data/multivac-aitana/tree/dev/infrastructure/environments)
  so each Firebase project gets the same config when stood up. Same
  capability in dev / test / prod.
- **Admin-SDK operations use plain ADC.** `auth.create_user`,
  `auth.update_user`, and `auth.set_custom_user_claims` are REST calls
  that do not need JWT signing — plain user ADC works.

## Gotchas we hit (preserved so next time is easy)

### The Firebase modular SDK has no `firebase` global

v6 uses the v9+ modular SDK ([frontend/src/lib/firebase.ts](../../frontend/src/lib/firebase.ts)).
Nothing is attached to `window`, so the classic `firebase.auth().currentUser.uid`
snippet throws `ReferenceError: firebase is not defined` in the browser
console even when signed in. The whoami probe replaces that workflow.

### `create_custom_token` needs `iam.signBlob`

Minting custom tokens with `firebase_admin.auth.create_custom_token`
requires the caller to sign a JWT, which requires
`iam.serviceAccounts.signBlob` on `firebase-adminsdk-fbsvc@<project>`.
Plain user ADC does **not** have this permission, and granting it
requires `iam.serviceAccounts.setIamPolicy` which typical project-owner
roles do not cover. Avoid the custom-token path entirely — use
email/password sign-in instead.

### Password sign-in is disabled by default on new Firebase projects

On a fresh project, `signInWithPassword` returns `400
PASSWORD_LOGIN_DISABLED`. Enable via the Terraform resource
`google_identity_platform_config.auth` — see the dev/test/prod
`auth_config.tf` files.

Quick manual enable (for emergency recovery only — let Terraform own it):

```bash
TOKEN=$(gcloud auth print-access-token)
curl -sS -X PATCH \
  "https://identitytoolkit.googleapis.com/admin/v2/projects/<project>/config?updateMask=signIn.email.enabled,signIn.email.passwordRequired" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: <project>" \
  -d '{"signIn":{"email":{"enabled":true,"passwordRequired":true}}}'
```

The `X-Goog-User-Project` header is mandatory when using user ADC —
without it you get a `403 SERVICE_DISABLED` complaining about a
quota project.

### Terraform provider uses the ADC quota project for Identity Platform calls

The Google Terraform provider routes Identity Toolkit API requests
through **the caller's quota project** (which for a user running
`gcloud auth application-default login` is typically
`your-deploy-project-id` — where Cloud Build lives), not the target
project. So the Identity Toolkit API must be enabled on BOTH
`<project_id>` and on `your-deploy-project-id`, otherwise import/apply
returns `403 Identity Toolkit API has not been used in project
<deploy-project-number>`.

One-time fix:

```bash
gcloud services enable identitytoolkit.googleapis.com --project=your-deploy-project-id
```

Propagation takes ~30-60 seconds.

### Singleton resource needs adoption on first apply

`google_identity_platform_config` is a **singleton** per project —
Firebase creates the config automatically when the project is
initialised. A plain `resource` block would fail with 409 ALREADY_EXISTS
on first apply; the [auth_config.tf](https://github.com/sunholo-data/multivac-aitana/blob/dev/infrastructure/environments/dev/auth_config.tf)
in each env pairs the resource with a declarative `import {}` block so
the first apply adopts the auto-created config into state, no manual
`terraform import` step needed:

```hcl
import {
  to = google_identity_platform_config.auth
  id = var.project_id
}

resource "google_identity_platform_config" "auth" { ... }
```

Subsequent applies are no-ops for the import (state already has it).

## Standing up a new env (test / prod promotion)

When merging `dev` → `test` (or `test` → `prod`):

1. **Terraform auth config**: `auth_config.tf` + the paired `import {}`
   block are already copied into the env directory. The first auto-apply
   will adopt the singleton automatically — no manual CLI step required.
   One-time: enable `identitytoolkit.googleapis.com` on
   `your-deploy-project-id` (already done for dev; test/prod inherit).
2. **Smoke script env entry**: add the new project's
   `project_id`, Web API key, and frontend URL to the `ENVIRONMENTS`
   dict in [backend/scripts/whoami_smoke.py](../../backend/scripts/whoami_smoke.py).
   The API key is a public identifier — commit it.
3. **Smoke user**: will be auto-created by the first `whoami_smoke.py`
   run against the new env.
4. **Sanity check**: `./scripts/smoke-deployed.sh <env> all auth` should
   print an OK line.

## Related

- [dev-accounts.md](dev-accounts.md) — dev uid + claims reference.
- [deployed-urls.md](deployed-urls.md) — canonical per-env URLs.
- [../../backend/scripts/whoami_smoke.py](../../backend/scripts/whoami_smoke.py) — the script.
- [../../backend/tests/integration/test_whoami_deployed.py](../../backend/tests/integration/test_whoami_deployed.py) — pytest wrapper.
- [../../scripts/smoke-deployed.sh](../../scripts/smoke-deployed.sh) — laptop-friendly smoke runner.
