# MCP Toolbox sidecar

Google [MCP Toolbox for Databases](https://mcp-toolbox.dev/) v1.7.0, running as
a **third container in the `platform-frontend` Cloud Run service**, serving
hand-authored BigQuery queries to skills as MCP tools.

## Why a sidecar and not its own service

Loopback (`127.0.0.1:5000`) means **no auth to get wrong**: no Cloud Run IAM
hop, no ID-token minting, no cold start in the TTFT path, no extra service per
client. The backend's existing MCP registry already accepts a plain `http` URL
with no auth block, so **this needs zero backend code**.

The trade: a Cloud Run service account is **per service, not per container**, so
Toolbox shares `sa-platform` and cannot be scoped narrower than the frontend.
That is acceptable because the tenant boundary is the **GCP project** (each
data-client gets its own fork/project), and `sa-platform` already reads the client's
data today via `backend/tools/entsoe_query.py` — so there is no new exposure.

**The rule this imposes:** never grant a second client's project to a shared
env's `sa-platform`. If a shared env ever must serve two clients' datasets,
split Toolbox out to a per-client service with its own scoped SA and add an
`auth: gcp_id_token` mode to the registry (design doc, E1).

## Files

| File | Ships in the public template? |
|---|---|
| `Dockerfile` | ✅ pins the upstream image, `COPY tools.yaml` |
| `tools.yaml` | ❌ **excluded** — ONE's MarketData queries (licensed data + the customer's pricing IP) |
| `tools.example.yaml` | ✅ renamed over `tools.yaml` by the sanitize pipeline so the template still builds |

`scripts/sanitize-for-template.sh` enforces both halves and hard-fails if
`tools.yaml` survives into a template build.

## Security — the two rules

Verified empirically against Toolbox v1.7.0 on 2026-07-17 (see the design doc's
Security Model for the full findings):

> `templateParameters` + `allowedValues` is **not** a security control. The
> `allowedValues` check is a **substring** match, not equality — any input
> *containing* an allowed value passes and is interpolated **raw** into the SQL.
> And `allowedDatasets` does **not** gate hand-authored `bigquery-sql` tools
> (only the generic `bigquery-execute-sql` one), so nothing catches it
> downstream. IAM is the only real backstop.

So, never put caller input in an identifier position:

- **C2 — choosing a column?** `CASE @param WHEN 'literal' THEN \`Real Column\` END`.
- **C2b — choosing a table?** `UNION ALL` the branches with a **literal** label
  column, filter on a **bound** param. (Used here: MarketData puts the market in the
  *table name*, e.g. `PPA_sweden_4`.)
- **C3 — never ship a generic `*-execute-sql` tool.** Google marks them "not for
  production agents".
- **C4 —** `tools.yaml` is git-tracked and PR-reviewed. Every tool is a security
  artefact.

`backend/tests/tool_tests/test_toolbox_config_safety.py` enforces C2/C2b/C3 plus
`writeMode: blocked` in CI, over **both** configs. It fails the build if anyone
reintroduces `templateParameters`.

## Local development

`make dev` starts Toolbox on `127.0.0.1:5000` with this `tools.yaml`, using your
ADC credentials — the same loopback URL as deployed, so there is no per-env drift
in the registry entry.

Run it by hand:

```bash
# the binary lives in gs://mcp-toolbox-for-databases (NOT genai-toolbox — renamed)
curl -L -o toolbox https://storage.googleapis.com/mcp-toolbox-for-databases/v1.7.0/darwin/arm64/toolbox
chmod +x toolbox
./toolbox --config tools.yaml --address 127.0.0.1 --port 5000 --disable-reload
```

Probe it (note: the `Accept` header must include **both** types, and the path is
toolset-scoped):

```bash
curl -sS -X POST http://127.0.0.1:5000/mcp/example \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Gotchas (each cost time once)

- **Default port is 5000**, and Cloud Run needs `--address` set explicitly.
- **`writeMode` defaults to `allowed`** — arbitrary INSERT/UPDATE/DROP. Always
  set `blocked`.
- **`--allowed-hosts` / `--allowed-origins` default to `*`** (it warns at
  startup). Irrelevant here only because the bind is loopback-only.
- **`location` must match the dataset's region** — the client's data is `europe-west4`,
  not `EU`.
- **Every parameter needs a `description`** or Toolbox refuses to boot.
- **No `/healthz`** ([upstream #2644](https://github.com/googleapis/mcp-toolbox/issues/2644))
  and the image is **distroless** (no shell) — hence the `tcpSocket` startup
  probe rather than an HTTP or exec one.
- `--tools-file` is deprecated; use `--config`.
