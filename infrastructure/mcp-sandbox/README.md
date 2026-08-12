# Aitana MCP Sandbox Proxy

Spec-compliant separate-origin sandbox proxy for MCP App UI resources.
Adopted from [`modelcontextprotocol/ext-apps/examples/basic-host`](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/basic-host)
(commit `0008d3b7`, ext-apps 1.7.1).

See [docs/design/v6.1.0/mcp-sandbox-separate-origin.md](../../docs/design/v6.1.0/mcp-sandbox-separate-origin.md)
for the architectural rationale.

## What this is

A tiny Express server (~120 LOC) that:

1. Serves `sandbox.html` + bundled `sandbox.js` on a different origin than
   the Aitana frontend host. Required by the MCP Apps spec — without it,
   the inner-iframe `allow-same-origin` defeats sandbox isolation by
   letting MCP App content read host cookies.
2. Sets `Content-Security-Policy` HTTP headers built from a `?csp=<json>`
   query parameter (tamper-proof; meta-tag CSP can be modified by the
   served HTML).
3. Validates the embedding host's origin via referrer match against the
   `ALLOWED_HOST_ORIGINS` env var.

## Run locally

```sh
cd infrastructure/mcp-sandbox
npm install
npm run dev   # tsx watch; rebuilds sandbox.js on change; serves on :3457
```

Or via the repo-root `make dev` target (preferred — runs frontend + backend
+ sandbox together with shared logging).

```sh
# Probe it
curl -i 'http://localhost:3457/sandbox.html?csp={"resourceDomains":[]}' | head -20
curl http://localhost:3457/healthz
```

## Build (for Cloud Run)

```sh
npm run build   # outputs public/sandbox.html + public/sandbox.js
npm run start   # runs serve.ts via Node's --experimental-strip-types
```

The Dockerfile (M4) builds a multi-stage Node image and runs
`node --experimental-strip-types serve.ts` as the entrypoint.

## Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `SANDBOX_PORT` | no | `3457` | Port to listen on |
| `ALLOWED_HOST_ORIGINS` | yes (in deployed envs) | `http://localhost:3456` | Comma-separated origins allowed to embed this sandbox. The sandbox JS rejects any other referrer. |

## Security model

- **Different origin from host** — required by the spec. Default Cloud Run
  URLs (`mcp-sandbox-{env}-<hash>.run.app` vs
  `platform-frontend-{env}-<hash>.run.app`) naturally differ.
- **CSP via HTTP headers** — not meta tags; can't be modified by the
  served HTML.
- **Referrer validation** — server-side via `ALLOWED_HOST_ORIGINS`,
  client-side regex check in `sandbox.ts`.
- **Origin validation on every postMessage** — both sides
  (parent → sandbox and inner → sandbox) validated.
- **CSP domain sanitization** — rejects entries containing `;`, newlines,
  quotes, or spaces to prevent CSP directive injection.
- **No Aitana auth tokens cross the sandbox** — the iframe content gets
  the protocol surface (postMessage); auth lives in the host frame.

## Tests

```sh
npm test   # vitest covering CSP builder + sanitizer
```

The `sandbox.ts` bridge logic is integration-tested as part of the M3 dev
demo flow (`/dev/mcp-apps/active`); a node-side unit test for the bridge
itself is captured as a follow-up in the design doc.

## Deploy (M4 — sister sprint scope)

- `infrastructure/mcp-sandbox/Dockerfile` — multi-stage Node build
- `infrastructure/mcp-sandbox/cloudbuild-mcp-sandbox.yaml` — branch-based deploy
- `multivac-aitana` terraform: new `cloud_run_multiple` entry `mcp-sandbox-{env}`
- `multivac-apps` terraform: new Cloud Build trigger
- `platform-frontend-{env}` env: `NEXT_PUBLIC_MCP_SANDBOX_URL` set from
  the sandbox service's URL
- `mcp-sandbox-{env}` env: `ALLOWED_HOST_ORIGINS` set to the deployed
  frontend URL
