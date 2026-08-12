# mcp-ext-apps-map — Cloud Run wrapper for upstream map-server

This directory holds **only** a Dockerfile + cloudbuild.yaml. The actual
MCP server source code is **not vendored** — the Dockerfile clones
[`modelcontextprotocol/ext-apps`](https://github.com/modelcontextprotocol/ext-apps)
at a pinned commit at image-build time and runs the example's
`examples/map-server` from there. License: MIT (upstream).

## Why a wrapper instead of a vendor copy?

The public template story benefits from staying clean — downstream forks
of `sunholo-data/ai-protocol-platform` see a small wrapper they can re-pin or replace,
not 30+ files of unfamiliar third-party code that need to be tracked.
The trade-off is that builds depend on GitHub being reachable from
Cloud Build, but Cloud Build's Cloud NAT egress is reliable enough that
this hasn't been a real risk in practice.

## Pinned commit

`0008d3b7` (= upstream tag `ext-apps@1.7.1`). Bump deliberately by editing
the `EXT_APPS_REF` build arg in the Dockerfile.

## Why a non-trivial build sequence?

The map-server example uses an internal v1.7.x API (`App.registerTool`)
that does NOT exist in the published `@modelcontextprotocol/ext-apps@1.0.0`
on npm. The Dockerfile works around this by:

1. Cloning the monorepo at the pinned commit.
2. Building the local `@modelcontextprotocol/ext-apps` package
   (`bun run build` in the root) and running its `scripts/link-self.mjs`,
   which symlinks the local v1.7.1 build into `node_modules/`.
3. Building the example's `mcp-app.html` browser bundle (`vite build`)
   against the now-correct local package.

Captured in [docs/talks/ai-ui-protocol-stack.md](../../docs/talks/ai-ui-protocol-stack.md)
verification log (2026-04-30 entry "ext-apps@1.7.1 map-server has an
upstream tsc bug"). When upstream publishes a v1.7.x to npm we can
simplify this Dockerfile.

## Local dev

This directory is NOT used for local dev. For local map-server runs:

```bash
cd /path/to/ext-apps/examples/map-server
bun run serve:http   # listens on localhost:3001/mcp
```

(Per `docs/design/v6.1.0/mcp-app-integrations.md` — sprint 1.7 M1 spike
captured the local-dev setup.)

## Cloud Run service

- **Service name:** `mcp-ext-apps-map` (per env: `mcp-ext-apps-map-dev`,
  later `-test`, `-prod`)
- **Project:** `your-project-id-{dev,test,prod}` per env
- **Region:** europe-west1
- **Public:** yes (`--allow-unauthenticated`). The frontend's MCP Client
  reaches it through the backend proxy at `/api/proxy/mcp/ext-apps-map`,
  gated by Firebase auth + per-skill allowlist (sprint 1.7 M2B). The
  agent's McpToolset reaches it directly server-side. Public access is
  required for the agent → server hop because the agent runs on a
  different Cloud Run service from the map-server.

## Smoke

After deploy:

```bash
curl -s -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Expected: 200 + an SSE event listing the `show-map` tool with
`_meta.ui.resourceUri = "ui://cesium-map/mcp-app.html"`.

The `cloudbuild.yaml` runs this same probe as a build step and fails the
build on anything other than 200.
