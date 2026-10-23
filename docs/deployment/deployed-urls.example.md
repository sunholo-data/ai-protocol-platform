# Deployed URLs

Canonical list of your live Cloud Run services, per environment. **This file
ships as a stub — fill it in after your first deploy.**

Cloud Run assigns each service a URL on first deploy and keeps it stable unless
the service is deleted and recreated, so this doc is worth keeping current: the
smoke scripts, the MCP sandbox allowlist, and any A2A agent card all need to
agree on these values.

## How to fill this in

After `gcloud builds submit` (or your first CI deploy) resolves, ask Cloud Run
rather than copying from the build log:

```bash
gcloud run services list \
  --project=<your-project-id> --region=<your-region> \
  --format='table(metadata.name, status.url)'
```

## Service topology

Two deployment models ship in this template; see
[deployment-models.md](deployment-models.md) for which to pick and how to remove
the one you don't use.

| Service | Ingress | Notes |
|---|---|---|
| Frontend (multi-container) | Public | Main container `ui` on **8080** (Cloud Run ingress); backend sidecar on **1956** |
| Backend (standalone) | IAM-protected | Only needed for SA-invoked callers — channels, cron, other services |
| MCP App sandbox | Public | Must be a **separate origin** from the frontend (MCP Apps spec) |

> **Sidecar port gotcha:** the main container owns `:8080`; every sidecar must
> listen elsewhere, and the frontend reaches its sidecar over `localhost:1956`.
> A Next.js 404 HTML page where you expected JSON almost always means the proxy
> is pointed at the wrong port.

> **Health-check gotcha:** smoke `/`, `/api/health`, and `/api/proxy/health` on
> the frontend, and `GET /sandbox.html` on the sandbox — **not** `/healthz`.
> Cloud Run's GFE intercepts `/healthz` for its own probes, so your container
> never sees the request and the check tells you nothing.

## dev

- **Frontend:** `https://<service>-<hash>-<region>.a.run.app`
- **Backend (IAM-protected):** resolve on demand —
  ```bash
  gcloud run services describe <backend-service> \
    --project=<your-project-id> --region=<your-region> \
    --format='value(status.url)'
  ```
  Call it with `gcloud auth print-identity-token --audiences=$URL`.
- **MCP App sandbox:** `https://<sandbox-service>-<hash>-<region>.a.run.app`
  — set as `NEXT_PUBLIC_MCP_SANDBOX_URL` on the frontend, and add the frontend
  origin to the sandbox's `ALLOWED_HOST_ORIGINS`.

## test

_(add once cut)_

## prod

_(add once cut)_

## Verifying

```bash
./scripts/smoke-deployed.sh dev all      # or: frontend | backend
```

Both `cloudbuild.yaml` pipelines end with the same smoke step, so a bad deploy
fails the build rather than going live.
