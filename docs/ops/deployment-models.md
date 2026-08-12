# Deployment Models — single-service vs paired-services

The template ships **two** `cloudbuild.yaml` files because the platform's own
deploys exercise two different Cloud Run topologies. Your fork only needs one,
or possibly both, depending on what callers reach your backend. Pick before
first deploy — running with the wrong model produces failing builds and orphan
triggers that quietly noise up CI.

This page exists because the dual-cloudbuild layout surfaced as friction in
downstream forks. See [G44](../design/template/template-fork-ergonomics.md#item-g44--dual-cloudbuildyaml-deployment-model-not-discoverable-by-forks)
in the fork-ergonomics tracker.

## TL;DR

| If your fork serves... | You need | You don't need |
|---|---|---|
| **Chat UI only** | root [`cloudbuild.yaml`](../../cloudbuild.yaml) (single sidecar service) | [`backend/cloudbuild.yaml`](../../backend/cloudbuild.yaml) — **delete it** and disable the orphan trigger |
| **Chat UI + channels** (Telegram, email, WhatsApp) | Both files | — |
| **Chat UI + external A2A crawlers** (Gemini Enterprise, agent marketplaces) | Both files (A2A registration calls the standalone backend's public origin via the Next.js proxy at `/.well-known/agent.json`, but channel webhooks bypass the proxy) | — |
| **Chat UI + external MCP callers** (other agents using your backend as an MCP server) | Both files | — |
| **Standalone API only** (no UI) | [`backend/cloudbuild.yaml`](../../backend/cloudbuild.yaml) alone | root `cloudbuild.yaml` |

If you can't articulate why you'd need the standalone backend, you don't.
Default to single-service.

## Model A — single-service sidecar (default for most forks)

**What it is:** one Cloud Run service named `platform-frontend` running two
sidecar containers:

- **Main container:** Next.js 15, owns the public ingress on port 8080
- **Sidecar container:** FastAPI + ADK, listens on `127.0.0.1:1956`, reached
  via the Next.js proxy at `/api/proxy/*`

Built by the root [`cloudbuild.yaml`](../../cloudbuild.yaml). Deploys on every
push to `dev` / `test` / `prod` via the `trigger-<env>-platform-frontend`
Cloud Build triggers.

**Why this is the default for forks:**

- **Cost** — one idle service = $0; two idle services = $0 × 2 but you pay for
  the second cold start the first time a non-UI caller arrives, and any
  monitoring / alerting / dashboards double.
- **Latency** — the proxy hop is loopback (`127.0.0.1`), not a separate Cloud
  Run service-to-service call. Frontend chat TTFT is measurably tighter.
- **CORS** — loopback means no cross-origin headers to negotiate.
- **Operational surface** — one service, one set of logs, one SA, one IAM
  policy.

**What you give up:** non-UI callers (channel webhooks, A2A crawlers, external
MCP clients) can still reach the sidecar **via the public Next.js ingress at
`/api/proxy/...`**, but the proxy is `withAuth`-gated by default. If a caller
can mint a Firebase ID token or a Cloud Run identity token your proxy accepts,
it works. If it can't, you need Model B.

## Model B — paired services (sidecar + standalone)

**What it is:** Model A plus a *second* Cloud Run service named
`platform-backend`, IAM-protected (no public ingress), built by
[`backend/cloudbuild.yaml`](../../backend/cloudbuild.yaml).

The platform's own deploys use both because:

- **Channel webhooks** (Telegram, email/Mailgun, WhatsApp/Twilio) hit the
  standalone backend directly with a shared-secret header — they don't carry
  Firebase ID tokens and don't go through `withAuth`. They're SA-invoked or
  webhook-signed, both of which the standalone backend's IAM policy accepts.
- **External A2A crawlers** (Gemini Enterprise Discovery Engine, agent
  marketplaces) — these go through the **Next.js proxy** at
  `/.well-known/agent.json`, but the agent card they read advertises the
  standalone backend's public-rewritten URL as the `url` field for follow-up
  RPC calls. See G43 / `template-a2a-spec-compliance.md`.
- **Internal SA-to-SA callers** (other Cloud Run services, scheduled jobs,
  Eventarc handlers) that have their own identity tokens but no Firebase
  session.

**When you need it:** at least one of the above use cases is real for your
fork. If none of them apply, deleting the standalone is pure subtraction.

## How to drop Model B from your fork

If you only need Model A (most forks), do all four steps. Skipping any one
leaves either a failing build trigger or a Terraform drift that bites the next
`terraform apply`.

1. **Delete the file:**
   ```bash
   git rm backend/cloudbuild.yaml
   ```
2. **Disable the Cloud Build trigger** in the deploy project (whichever
   project owns your triggers — the platform uses `your-deploy-project-id`,
   your fork is whatever you set in your infrastructure repo):
   ```bash
   gcloud beta builds triggers delete \
     trigger-<env>-platform-backend \
     --project=<your-deploy-project> --region=<your-region>
   ```
   …for each env (`dev`, `test`, `prod`).
3. **Remove the standalone Cloud Run service** if it was ever deployed:
   ```bash
   gcloud run services delete platform-backend \
     --project=<env-project> --region=<your-region> --quiet
   ```
4. **Strip Terraform references** to the standalone service. In a typical fork
   that's:
   - The `platform-backend` row in `tf_account_permissions` (folder-level IAM
     cascade — search `multivac-aitana/infrastructure` for `platform-backend`)
   - Any `google_cloud_run_v2_service_iam_member` resources targeting it
   - The trigger `google_cloudbuild_trigger.platform_backend_*` resources

Then `terraform plan` should show **only** deletions of the three categories
above and **no** changes to your `platform-frontend` resources. If it shows
unrelated drift, stop and read the plan before applying.

## How to drop Model A (rare)

If you're running standalone-API-only (no UI — e.g. a backend that only serves
channels and external agents), delete the root `cloudbuild.yaml` and the
`platform-frontend` triggers. Most forks won't do this; if you're here, you
probably know why already.

## Symptoms that signal the wrong choice

| Symptom | Likely cause |
|---|---|
| `trigger-<env>-platform-backend` builds are FAILING / EMPTY on every push to dev/test/prod | You're running Model A only but didn't delete `backend/cloudbuild.yaml` + disable the trigger |
| Telegram / email webhooks return 401 in production | You're running Model A only and the webhook can't pass `/api/proxy/*` auth — Model B's standalone backend is what these need |
| Gemini Enterprise registration rejects your agent card with `localhost` in the `url` field | Not a model issue — that's G43 / [`template-a2a-spec-compliance.md`](../design/template/template-a2a-spec-compliance.md); the Next.js proxy needs to rewrite `url` via `X-Forwarded-Proto`/`X-Forwarded-Host` |
| Cloud Run shows two services billing for nothing | You're running Model B but no non-UI caller actually exists; drop B |

## Related

- [G44](../design/template/template-fork-ergonomics.md#item-g44--dual-cloudbuildyaml-deployment-model-not-discoverable-by-forks) — the tracker entry that surfaced this doc.
- [deployed-urls.md](deployed-urls.md) — the canonical list of live URLs per env (currently documents both services because the platform's own deploys exercise Model B).
- [template-cloudbuild-hardening.md](../design/template/template-cloudbuild-hardening.md) — `cloudbuild.yaml` defaults that apply equally to both files (per-project log bucket, channel-secret gating, metadata-server token mint).
- [template-a2a-spec-compliance.md](../design/template/template-a2a-spec-compliance.md) — G43; A2A crawler concerns that affect the Model A-vs-B choice if you plan to register with Gemini Enterprise.
