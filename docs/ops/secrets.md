# Secrets and Environment Variables

How Cloud Build secrets, Firebase config, and NEXT_PUBLIC_ variables flow into the platform.

## Firebase Config (NEXT_PUBLIC_FIREBASE_*)

Firebase config is stored as a single secret in Secret Manager (`FIREBASE_ENV`) as a
newline-delimited `KEY=value` file. The `get-firebase-config.sh` build step materializes
it as `--build-arg KEY=VALUE` flags for the frontend Docker build.

## The NEXT_PUBLIC_* Trap

> **Critical:** Docker silently ignores `--build-arg` values for ARGs not declared in
> `frontend/Dockerfile`. The build succeeds, Next.js sees `undefined` at build time, and
> the feature appears to work at runtime (server-side env reads the value) but the
> compiled JS bundle has `undefined` baked in.

### Three-step process for any new NEXT_PUBLIC_ variable

**Step 1 — `frontend/Dockerfile`**

Add `ARG` + `ENV` pair in the `# Auth + branding` block near the top of the build stage:

```dockerfile
ARG NEXT_PUBLIC_MY_VAR
ENV NEXT_PUBLIC_MY_VAR=$NEXT_PUBLIC_MY_VAR
```

**Step 2 — FIREBASE_ENV secret**

Update the secret's value in Secret Manager to include the new variable. Format:

```
NEXT_PUBLIC_FIREBASE_API_KEY=AIza...
NEXT_PUBLIC_FIREBASE_PROJECT_ID=my-project
NEXT_PUBLIC_MY_VAR=my-value
...
```

```bash
# To update the secret:
echo "NEXT_PUBLIC_MY_VAR=my-value" >> /tmp/env.txt
gcloud secrets versions add FIREBASE_ENV --data-file=/tmp/env.txt --project=<project>
```

**Step 3 — `cloudbuild.yaml` build-arg list**

Find the `get-firebase-config.sh` step that generates `--build-arg` flags and add the new variable:

```bash
--build-arg NEXT_PUBLIC_MY_VAR=${NEXT_PUBLIC_MY_VAR}
```

### Currently declared NEXT_PUBLIC_* variables

| Variable | Purpose | Required |
|----------|---------|---------|
| `NEXT_PUBLIC_FIREBASE_API_KEY` | Firebase app config | Yes |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | Firebase app config | Yes |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | Firebase app config | Yes |
| `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET` | Firebase app config | Yes |
| `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID` | Firebase app config | Yes |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | Firebase app config | Yes |
| `NEXT_PUBLIC_ADMIN_EMAIL` | Admin user email shown in UI | Yes |
| `NEXT_PUBLIC_BACKEND_URL` | Backend URL for SSR | Yes |
| `NEXT_PUBLIC_MCP_SANDBOX_URL` | MCP App sandbox proxy URL | No — empty disables MCP Apps |
| `NEXT_PUBLIC_AUTH_MODE` | Auth flow (`firebase` or `anonymous_group_id`) | No — defaults to `firebase` |
| `NEXT_PUBLIC_APP_SLUG` | Fork-specific app slug for transport field naming | No — defaults to `platform` |
| `NEXT_PUBLIC_CITATION_SCHEME` | Citation URI scheme (e.g. `inline-citation`) | No — defaults to `inline-citation` |

## Backend Secrets (Secret Manager → Cloud Run)

Backend secrets are injected at deploy time via `--set-secrets` in `cloudbuild.yaml`.

| Secret name | Purpose | Required |
|-------------|---------|---------|
| `AGENT_ENGINE_ID` | Vertex AI Agent Engine resource ID | Yes (production) |
| `ANTHROPIC_API_KEY` | Anthropic model access | No — controlled by `_ENABLE_ANTHROPIC` |
| `TELEGRAM_BOT_TOKEN` | Telegram channel | No — controlled by `_ENABLE_TELEGRAM` |
| `TWILIO_ACCOUNT_SID` | WhatsApp channel | No — controlled by `_ENABLE_WHATSAPP` |
| `TWILIO_AUTH_TOKEN` | WhatsApp channel | No — controlled by `_ENABLE_WHATSAPP` |
| `MAILGUN_API_KEY` | Email channel | No — controlled by `_ENABLE_EMAIL` |
| `MAILGUN_WEBHOOK_SECRET` | Email channel | No — controlled by `_ENABLE_EMAIL` |

The `_ENABLE_*` flags in `cloudbuild.yaml` substitutions control which secrets are
requested at deploy time. Set them to `true` only for channels you have credentials for.

## Local Development

Local development uses `.env` files in `backend/`. The `LOCAL_MODE=1` flag bypasses
Firebase Auth so you don't need a real identity token for local requests.

See [WORKSHOP.md](../../WORKSHOP.md) for the minimal setup to get a local chat UI working.
