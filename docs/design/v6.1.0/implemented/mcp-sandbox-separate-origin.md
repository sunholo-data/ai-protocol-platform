# MCP Sandbox Proxy — Separate-Origin Service

**Status**: Planned (lands in sprint MCP-APP-INTEGRATIONS / 1.7 M3 + M4)
**Priority**: P1 — gates spec-correct rendering of MCP App UI resources
**Estimated**: ~1d total — ~0.5d M3 (server + dev orchestration) + ~0.5d M4 (Cloud Run service + Cloud Build trigger + terraform)
**Scope**: Infra (new Node service) + minimal frontend wiring (one env var)
**Dependencies**:
  - [mcp-app-integrations.md](mcp-app-integrations.md) — main MCP Apps integration doc; this is its sandbox-proxy slice
  - M2A frontend `mcpClient.ts` (✅ shipped 2026-04-30) — currently points `SandboxConfig.url` at a placeholder; this doc lands the real URL
**Created**: 2026-04-30
**Last Updated**: 2026-04-30

## Problem Statement

`@mcp-ui/client.AppRenderer` requires a `SandboxConfig` with a `url: URL` pointing at a **double-iframe sandbox proxy**. The MCP Apps spec mandates this proxy be served from a **different origin than the host** so the inner iframe can use `sandbox="allow-scripts allow-same-origin allow-forms"` (which the spec requires for app functionality) without the inner iframe inheriting the host's cookies, localStorage, or being able to talk to the host's origin.

The reference implementation in [`modelcontextprotocol/ext-apps/examples/basic-host`](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/basic-host) is a tiny Express server with two ports: host on 8080, sandbox on 8081. The sandbox server explicitly rejects same-origin requests AND sets CSP via HTTP headers built from a `?csp=<json>` query parameter (tamper-proof; meta-tag CSP can be modified by the served HTML).

**Three paths were evaluated 2026-04-30:**

| Path | Approach | Pros | Cons |
|---|---|---|---|
| **X** | Serve `sandbox.html` from `frontend/public/mcp-sandbox/` (same origin as host) | Lands in <0.1d; demo works | **Security model broken** — inner iframe can read host cookies. Not template-grade. |
| **Y** (chosen) | Add a separate Node service on a different origin (port in dev, separate Cloud Run service in deployed envs) | Spec-compliant; template-grade; downstream forks get correct posture by default | +1d sprint cost (~0.5d M3 + ~0.5d M4); one more moving piece in the dev/deploy stack |
| **Z** | Skip `<AppRenderer>`; use `<AppFrame>` with `srcdoc`-only mounting (no proxy needed; inner iframe gets unique opaque origin via `sandbox="allow-scripts"` without same-origin) | No new infra | Loses some `AppRenderer` features (auto resource fetch, tool list change notifications, full bidirectional bridge richness) — partially undoes the Path A decision rationale |

**Decision: Path Y.** Same logic as Path A choice from 2026-04-30 — v6 is the foundation for a public template (mid-to-late May 2026 fork per `project_template_split` memory). The 1d cost-now is rounding error against the multi-year template lifetime; retrofitting to Y across N forks later is strictly more expensive. Workshop W7 demo gains nothing from cutting corners on the spec-mandated security boundary; the audience won't see the difference but the security model matters when downstream projects ship to real users.

## Goals

**Primary Goal:** Ship a spec-compliant, separately-hosted MCP sandbox proxy that downstream Aitana template forks can use as-is, with no security-model surgery required.

**Success Metrics:**
- Sandbox proxy serves on a different origin than `platform-frontend` in all environments (dev/test/prod)
- `sandbox.html` referrer validation rejects requests from unexpected hosts (testable)
- CSP set via HTTP headers (not meta tags) — verifiable via `curl -I`
- Inner iframe content cannot access host cookies / localStorage (verifiable via DevTools after smoke)
- Adding a new MCP server with arbitrary CSP requirements requires no sandbox changes — `?csp=<json>` query param is the only knob

**Non-Goals:**
- Custom CSP authoring per-app — apps declare their CSP via the MCP `_meta.ui.csp` field; the sandbox just enforces it
- Multi-tenant sandbox sharing across Aitana platform deployments — each platform gets its own sandbox service (operating costs are negligible at scale 0)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Sandbox is loaded once per chat session; cached after first hit. Negligible latency add. |
| 2 | EARNED TRUST | +1 | The visible thing is unchanged (iframe still appears in chat). The invisible thing — proper origin isolation — is what users would expect from a serious chat product. |
| 3 | SKILLS, NOT FEATURES | 0 | Pure infrastructure; not a user-facing surface. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | n/a |
| 5 | GRACEFUL DEGRADATION | +1 | Sandbox unavailable → AppRenderer errors → router catches via `onError` → falls back to `ToolCallChip`. Chat keeps working; only the visual flourish is lost. |
| 6 | PROTOCOL OVER CUSTOM | +2 | This IS the axiom in action. We're spending 1d to adopt the spec's exact security architecture instead of cutting a corner. The sandbox proxy is itself an off-the-shelf reference impl from the MCP org (`modelcontextprotocol/ext-apps/examples/basic-host`); we're operating it, not inventing it. |
| 7 | API FIRST | 0 | No API surface change. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Sandbox CSP failures show up in browser console + can be wired to Cloud Logging. Referrer validation rejections log structured events. |
| 9 | SECURE BY CONSTRUCTION | +2 | The whole point. Same-origin sandbox would be a known-broken security posture shipped to a public template; that's an actively harmful default. Separate-origin closes the loop the spec defines. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | The frontend stays thin (one env var); the protocol-defined isolation lives in the proxy service. |
| | **Net Score** | **+7** | Strong alignment — proceed |

**Conflict Justifications:** None.

## Design

### Overview

A standalone Node + Express service that serves three things:
1. `GET /sandbox.html` — the proxy HTML (~50 LOC, copy from ext-apps reference, branded "Aitana MCP Sandbox" but otherwise unchanged)
2. `GET /sandbox.js` — bundled bridge script (compiled from `src/sandbox.ts`, ~140 LOC, copy + adapt from ext-apps reference)
3. CSP HTTP headers built from `?csp=<json>` query param (sanitized — see ext-apps reference `serve.ts:sanitizeCspDomains`)

Lives in `infrastructure/mcp-sandbox/` — same parent directory as `mcp-ext-apps-map/` (the other Cloud Run sidecar). Local dev: spun up by `scripts/dev.sh` on port 3457 (next to frontend's 3456). Production: separate Cloud Run service `mcp-sandbox-{env}` with its own URL.

### Origin map

| Env | Frontend origin | Sandbox origin | Different? |
|---|---|---|---|
| Local dev | `http://localhost:3456` | `http://localhost:3457` | ✅ port differs |
| Cloud Run dev | `https://platform-frontend-dev-<hash>.run.app` | `https://mcp-sandbox-dev-<hash>.run.app` | ✅ subdomain differs |
| Cloud Run test | same shape with `-test-` | same shape with `-test-` | ✅ |
| Cloud Run prod | same shape with `-prod-` | same shape with `-prod-` | ✅ |

Default Cloud Run URLs naturally differ — no custom domain required for the spec-correct security boundary. (Custom domains for both can come later as a UX polish, separate sprint.)

### Frontend wiring

Single env var: `NEXT_PUBLIC_MCP_SANDBOX_URL` (e.g. `http://localhost:3457/sandbox.html` in dev). `frontend/src/lib/mcpClient.ts` reads it and constructs the `SandboxConfig.url` from there. Default fallback: the localhost dev URL, so `make dev` Just Works. In Cloud Run, the env var is set at deploy time from the sandbox service's URL.

### Service files

```
infrastructure/mcp-sandbox/
├── README.md                     # ~30 LOC; how to run locally + deploy
├── package.json                  # express + cors + @modelcontextprotocol/ext-apps + tsx (dev) + esbuild (build)
├── tsconfig.json
├── sandbox.html                  # ~50 LOC; copy from ext-apps basic-host
├── src/
│   └── sandbox.ts                # ~140 LOC; copy from ext-apps basic-host, with hostname allowlist updated for v6
├── serve.ts                      # ~120 LOC; copy from ext-apps basic-host serve.ts; SANDBOX_PORT defaults to 3457
└── (M4) Dockerfile               # Multi-stage Node build
└── (M4) cloudbuild-mcp-sandbox.yaml
```

### Referrer allowlist

`sandbox.ts` validates referrer against a regex. Update from ext-apps default (`localhost|127.0.0.1`) to:
- Local dev: `http://localhost:3456`
- All Cloud Run host origins for v6 frontend (regex per env or compiled allowlist via env var `ALLOWED_HOST_ORIGINS`)

Externalize as `ALLOWED_HOST_ORIGINS` env var (comma-separated) so deploys don't require code changes.

### CSP

Sandbox builds CSP from `?csp=<json>` per ext-apps reference. Default CSP allows: `'self'` for everything + `'unsafe-inline'` + `'unsafe-eval'` + `blob:` + `data:` + sanitized domains from the resource's CSP metadata. We adopt the reference logic verbatim (`buildCspHeader` from `serve.ts`).

### Local dev orchestration

`scripts/dev.sh` extends the existing 2-process orchestration (backend + frontend) to 3 processes. Add:
- Port 3457 to the kill-on-restart loop
- A new `(cd "$REPO_ROOT/infrastructure/mcp-sandbox" && npm run dev 2>&1 | tee "$LOG_DIR/sandbox.log") &` block
- `SANDBOX_PID` to the cleanup trap

The sandbox process is OPTIONAL in the sense that frontend/backend can run without it (chat still works for non-MCP-App tool calls); but if MCP App rendering is exercised, sandbox MUST be up.

## Implementation Plan

### M3 (lands in sprint 1.7)

- [ ] Copy `sandbox.html`, `src/sandbox.ts`, `serve.ts` from `~/dev/ext-apps/examples/basic-host/` to `infrastructure/mcp-sandbox/` and rebrand
- [ ] Update referrer regex in `sandbox.ts` to read from `ALLOWED_HOST_ORIGINS` env var (default: `^http://localhost:3456`)
- [ ] Add `package.json` with `express`, `cors`, `@modelcontextprotocol/ext-apps`, `tsx` (dev), `typescript`, `@types/express`, `@types/cors`, `@types/node`
- [ ] Add `tsconfig.json` (target ES2022, module ESNext for Node compatibility)
- [ ] Add `npm scripts`: `dev` (tsx watch serve.ts), `build` (compile sandbox.ts → dist/sandbox.js + copy sandbox.html → dist/), `start` (node dist/serve.js)
- [ ] Add port 3457 + sandbox process orchestration to `scripts/dev.sh`
- [ ] Add `NEXT_PUBLIC_MCP_SANDBOX_URL` env var; update `frontend/src/lib/mcpClient.ts` to read it (default `http://localhost:3457/sandbox.html`)
- [ ] Test: bring up `make dev`, verify `curl http://localhost:3457/sandbox.html` returns 200 with CSP headers when `?csp={...}` is provided
- [ ] Test: verify referrer rejection (curl with bad Referer header → error)

### M4 (lands in sprint 1.7 deploy phase)

- [ ] `infrastructure/mcp-sandbox/Dockerfile` — multi-stage Node build
- [ ] `infrastructure/mcp-sandbox/cloudbuild-mcp-sandbox.yaml` — branch-based deploy (dev only this sprint)
- [ ] New `cloud_run_multiple` entry in `multivac-aitana/infrastructure/environments/dev/run_client.tfvars` for `mcp-sandbox-dev`. Reuse `platform@` SA. Public ingress (browser must reach it) — but no ALB needed; default Cloud Run URL is sufficient.
- [ ] New Cloud Build trigger `trigger-mcp-sandbox-dev` in `multivac-apps` (terraform)
- [ ] Add `MCP_SANDBOX_URL` to `platform-frontend-dev` env vars (set from terraform output of the sandbox service URL)
- [ ] `ALLOWED_HOST_ORIGINS` env var on the deployed sandbox service set to the deployed frontend URL
- [ ] Update `docs/ops/deployed-urls.md` with the sandbox service URL
- [ ] Add a smoke probe to `scripts/smoke-deployed.sh` (curl `/sandbox.html` returns 200)

### Test/prod promotion

OUT OF SCOPE for sprint 1.7. Promote dev → test → prod via standard two-PR flow (multivac-aitana then multivac-apps), targeting ~1 week before workshop. Per `reference_env_promotion_audit`, run pre-promotion audit script.

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None — sandbox is required for MCP App rendering. If sandbox is down, AppRenderer errors and the router falls back to `ToolCallChip`.

**Rollback Plan:**
- If the sandbox service misbehaves, revert the `NEXT_PUBLIC_MCP_SANDBOX_URL` env var on `platform-frontend` to a previous known-good URL (or unset to fall back to the localhost dev default — which fails cleanly in deployed env via referrer validation, so users see a graceful error instead of an unsafe sandbox)
- For local dev: just `Ctrl-C` the dev script and restart without the sandbox; chat keeps working for non-MCP-App tool calls

**Environment Variables:**
- `NEXT_PUBLIC_MCP_SANDBOX_URL` (frontend, required for MCP App rendering) — full URL of the sandbox `sandbox.html` endpoint
- `SANDBOX_PORT` (sandbox service, optional, default 3457) — port to listen on
- `ALLOWED_HOST_ORIGINS` (sandbox service, required) — comma-separated list of host origins allowed to embed the sandbox

## Testing Strategy

### Sandbox service tests
- [ ] `infrastructure/mcp-sandbox/__tests__/serve.test.ts` (~80 LOC) — vitest or jest:
  - GET /sandbox.html returns 200 with CSP headers built from `?csp={...}` param
  - GET /sandbox.html with malformed `?csp=` param falls back to default CSP
  - CSP sanitization rejects entries with `;`, newlines, quotes, spaces (per ext-apps reference)
  - `sanitizeCspDomains` test cases
- [ ] `infrastructure/mcp-sandbox/__tests__/sandbox-script.test.ts` (~60 LOC) — node-side test of the bridge logic (mock window/parent/postMessage):
  - Throws if loaded outside iframe (window.self === window.top check)
  - Throws if no document.referrer
  - Rejects messages from unexpected origins
  - Forwards `sandbox-resource-ready` to inner iframe via `document.write`
  - Relays messages bidirectionally between parent and inner

### Frontend wiring tests
- [ ] `frontend/src/lib/__tests__/mcpClient.test.ts` extended — asserts `SandboxConfig.url` reads from `NEXT_PUBLIC_MCP_SANDBOX_URL` env var with the right default

### Manual security smoke (after M4 deploys)
- [ ] Open Aitana frontend; trigger an MCP App render
- [ ] DevTools → Application → Storage: verify the inner iframe origin is the sandbox origin (not the host origin)
- [ ] DevTools → Application → Cookies: verify the inner iframe cannot read `platform-frontend` cookies (separate origin, separate cookie jar)
- [ ] Curl `/sandbox.html` directly with no Referer → script execution should throw "No referrer" client-side (the file still serves, but is unusable without a referrer)
- [ ] Curl `/sandbox.html` with bad Referer → script throws "Embedding domain not allowed"

## Security Considerations

This whole doc IS the security consideration. Key points:
- **Origin separation** is the bedrock — without it, `allow-same-origin` on the inner iframe gives the inner content access to host cookies
- **CSP via HTTP headers**, not meta tags — meta-tag CSP can be modified by the served HTML; HTTP headers cannot
- **Referrer validation** prevents arbitrary sites from embedding the sandbox to siphon postMessages
- **Origin validation** on every message in `sandbox.ts` prevents injection from unexpected windows
- **No Aitana auth tokens cross the sandbox boundary** — the sandbox is unauthenticated; it only serves public HTML + relays JSON-RPC. The MCP `Client` in the host frame holds the Firebase token; iframe content can call back via `tools/call` postMessage which gets relayed to `/api/proxy/mcp/...` which validates auth there
- **Sanitize CSP domains** — reject `;`, newlines, quotes, spaces (per ext-apps reference) so malicious CSP injection doesn't add `unsafe-eval` or break out of the directive

## Performance Considerations

- **First-load latency:** sandbox HTML is ~50 LOC + ~140 LOC bundled JS = ~5KB gzipped. Cached after first hit. Negligible.
- **Per-render overhead:** double-iframe adds one extra postMessage hop in each direction. Spec-mandated; can't avoid.
- **Cold start:** Cloud Run sandbox at `min-instances=0` for dev. Workshop morning: bump to 1 alongside `mcp-ext-apps-map-dev`.
- **Bundle:** the sandbox.ts compiles to a small standalone JS file; no React or @mcp-ui/client deps inside the iframe. Tiny.

## Success Criteria

- [ ] Sandbox service runs locally via `make dev` on port 3457
- [ ] `curl http://localhost:3457/sandbox.html?csp={"resourceDomains":[]}` returns 200 with `Content-Security-Policy` HTTP header set
- [ ] Sandbox service tests passing
- [ ] Frontend `mcpClient.ts` reads `NEXT_PUBLIC_MCP_SANDBOX_URL` (vitest)
- [ ] Local end-to-end smoke: globe renders inside the double-iframe; DevTools confirms the inner iframe origin is the sandbox origin (not host)
- [ ] Deployed dev: `mcp-sandbox-dev` Cloud Run service Ready: True; default URL responds with 200 + correct CSP
- [ ] `docs/ops/deployed-urls.md` updated; smoke probe added to `scripts/smoke-deployed.sh`
- [ ] Workshop demo "the iframe and agent are MCP peers" works AND the security boundary is honest (no host-cookie access from inside the iframe)

## Open Questions

- **Custom domain?** Default Cloud Run URLs (`mcp-sandbox-{env}-<hash>.run.app`) are already different origins from `platform-frontend-{env}-<hash>.run.app`, which is sufficient for spec compliance. Custom domains (`mcp-sandbox.yourcompany.com` etc.) are pure UX polish; defer to a separate sprint unless the workshop deck wants prettier URLs in screenshots.
- **Bundle the sandbox JS into the HTML?** Could inline `<script>...</script>` into `sandbox.html` to ship as one static file. Trade-off: slightly bigger HTML payload + harder to read; benefit: zero additional HTTP requests. Decision deferred to implementation; default to separate file for readability.
- **Reuse for multi-tenant?** If we ever host multiple Aitana platform deployments, each could share one sandbox service (it's stateless). For now, one sandbox per platform — fits the deploy model and keeps `ALLOWED_HOST_ORIGINS` simple.

## Related Documents

- [mcp-app-integrations.md](mcp-app-integrations.md) — main MCP Apps integration doc; this is its sandbox-proxy slice
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — workshop W7 narrative; verification log will record this work after M3+M4 land
- External: [`modelcontextprotocol/ext-apps/examples/basic-host`](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/basic-host) — the reference implementation we're adopting
- External: [MCP Apps blog post](https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/) — protocol context
- External: [SEP-1724](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1724) — capability extensions pattern
