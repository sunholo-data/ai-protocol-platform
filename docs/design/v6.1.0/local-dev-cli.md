# Local Dev CLI (`aiplatform`)

**Status**: Planned
**Priority**: P1 (Medium) — quality-of-life multiplier, not blocking the v6 bring-up sprint, but commands ship incrementally as features land
**Estimated**: ~2-3 days for the bring-up surface (`dev`, `auth`, `skill list/run`, `doc parse`); each subsequent feature adds its own command in <0.25 day as part of that feature's design doc
**Scope**: Tooling (Python `cli/` package, no frontend, no backend changes beyond exposing a couple of read-only endpoints the CLI consumes)
**Dependencies**:
- M1A.0 backend skeleton (FastAPI on :1956)
- M1B.0 frontend scaffold (Next.js on :3000)
- v5 batch-extract code at `<your-v5-source>/aitana/` (porting source)
- [tools-porting-guide.md](tools-porting-guide.md) for the bulk-extraction commands
**Created**: 2026-04-11
**Last Updated**: 2026-04-11

## Problem Statement

v6 has four moving local processes during normal development: backend FastAPI (`make dev` on :1956), frontend Next.js (`npm run dev` on :3000), ADK Playground (`make playground` on :8501), and — once the [MCP App integrations](mcp-app-integrations.md) doc lands — `ext-apps/map-server` (:3001). Skills live in Firestore. Documents need parsing through `ailang-parse` to verify A2UI rendering. MCP servers need probing for `tools/list`. Bulk extraction against GCS is still a real user workflow inherited from v5.

Right now every one of those is a manual incantation: a different terminal, a different `make` target, a different curl, a different uv-invocation. There's no single tool that knows the v6 layout.

**Current State:**
- 3-4 terminals open during normal dev work, each running a different `make` or `npm` command
- No way to scaffold a new skill — manual Firestore doc edits, no template
- No local fast path to test `ailang-parse` against a real `.docx` without booting the full backend
- No way to inspect or call an MCP server registered in `mcp_servers.yaml` without writing throwaway Python
- v5 batch-extract users (the bulk GCS workflow) have nothing in v6 yet
- v6 `cli/` directory exists but is empty — placeholder waiting for a decision

**Impact:**
- Affects: every developer touching v6 (currently just Mark, but the talk demo workshop kit will expose this to ~50 attendees in 2026-07)
- Significance: medium friction now, becomes a blocker the moment the first external user tries to set up the demo repo
- Strategic: a clean `aiplatform` CLI is *the* observable surface for "v6 is one tool, not three subprojects" — the same way `wrangler` makes Cloudflare Workers feel coherent

## Goals

**Primary Goal:** Ship a single Python CLI binary `aiplatform` that hosts every local-development affordance for v6 — process orchestration, skill scaffold/sync/run, document parsing, MCP probing, eval, deploy gating, and the v5 batch-extraction workflows — so that a fresh checkout becomes productive in `aiplatform dev up && aiplatform auth` rather than five `README` paragraphs.

**Success Metrics:**
- Time from `git clone` to "first prompt against a deployed skill" for a new contributor: <5 minutes
- Number of terminals required for a normal dev loop: 1 (the one running `aiplatform dev up`)
- Adding a new command for a future feature (e.g., `aiplatform memory inspect` once memory ships): <0.25 day, as part of that feature's design doc
- Workshop attendees in 2026-07 can run the demo with three commands (`pip install aiplatform`, `aiplatform auth`, `aiplatform dev up`)
- v5 batch-extract users can switch to `aiplatform bulk extract` with no behavioural regressions

**Non-Goals:**
- **Not an interactive coding agent.** No file editing, no LLM-driven code suggestions, no Claude-Code-style REPL. (See [Track 2 deferral](#related-documents) — `agent-cli.md` is a separate planned doc for that.)
- **Not a deployment tool that bypasses Cloud Build.** `aiplatform deploy` is a thin gated wrapper that triggers the existing branch-deploy pipeline; it does not replace `gcloud run deploy`.
- **Not a Firestore admin GUI.** `skill push/pull/diff` operate on YAML files; for arbitrary collection editing, use the Firebase console.
- **Not a replacement for `make dev`.** `aiplatform dev up` shells out to the existing `make` and `npm` targets — it's an orchestrator, not a re-implementation. Anyone who prefers running `make dev` directly still can.
- **Not multi-tenant.** This is a developer tool, not a SaaS. Auth comes from the developer's own gcloud + Firebase credentials.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | The CLI itself starts in <100ms (Click + lazy imports). `aiplatform doc parse` runs `ailang-parse` directly, no backend round-trip. |
| 2 | EARNED TRUST | +1 | Every command that touches deployed state (`skill push`, `deploy`, `bulk extract`) prints a confirmation summary and requires `--yes` for non-interactive runs. Dry-run is the default for `skill push`. |
| 3 | SKILLS, NOT FEATURES | +1 | Skills are first-class CLI citizens (`aiplatform skill new/list/run/push/pull/diff`). The CLI reinforces the skill abstraction rather than exposing raw Firestore. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | The CLI doesn't pick models. |
| 5 | GRACEFUL DEGRADATION | +1 | Each command has a clearly scoped failure mode: `dev up` will start whatever it can and report which services failed; `bulk extract` retries per-file and emits a `failed.csv` rather than aborting the whole batch. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Wraps existing tools (`make`, `npm`, `gcloud`, `ailang-parse`, `adk`, `gh`) rather than reimplementing them. Skill YAML uses the same shape as Firestore documents. |
| 7 | API FIRST | +1 | Every CLI command that touches deployed state goes through the public FastAPI surface — no backdoor. The CLI is an API client, not a privileged second path. |
| 8 | OBSERVABLE BY DEFAULT | +1 | `--verbose` toggles structured JSON logs; every API call gets a request ID echoed back so traces are correlatable in Cloud Trace. |
| 9 | SECURE BY CONSTRUCTION | +1 | Reuses gcloud ADC + Firebase tokens (no new credential store). `deploy` requires explicit env confirmation. No long-lived secrets cached on disk. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The CLI carries no business logic — it's a UX layer over backend endpoints, file formats, and existing CLIs. Doc parsing is the only "smart" thing it does locally, and that's just calling the `ailang-parse` library. |
| | **Net Score** | **+8** | Strong alignment — proceed |

**Conflict Justifications:** None — no axioms scored -1.

**Standards compliance check:** No new file formats invented. Skill YAML matches the Firestore schema from [skills-data-model.md](skills-data-model.md). Bulk extract input/output formats preserve v5's JSONL + CSV shapes for backward compatibility. Process orchestration delegates to `make` and `npm` rather than re-encoding the project's build graph.

## Design

### Overview

A single Python package at `cli/` exposing the `aiplatform` binary via `[project.scripts]`. Built on Click (or Typer — to be decided in Phase 1). Lazy-imports heavy dependencies (`google.cloud.firestore`, `ailang_parse`, `httpx`) so subcommands only pay for what they use. The whole package targets <2k LOC at v0.

### Command Surface (v0)

Grouped by purpose. Bold = ships in the bring-up sprint; the rest land as their corresponding features ship.

**Process orchestration**
- **`aiplatform dev up [services...]`** — start backend, frontend, MCP sidecars, ADK playground in one terminal with multiplexed colored output. Default starts everything; pass names to start a subset.
- **`aiplatform dev down`** — kill everything started by the most recent `up`.
- **`aiplatform dev logs <service> [--follow]`** — tail logs from one service.
- **`aiplatform dev status`** — show port + PID + health for each service.

**Auth**
- **`aiplatform auth`** — show current gcloud account, Firebase project, ADC status, and which v6 environment URLs the CLI will hit.
- **`aiplatform auth login`** — wrapper around `gcloud auth application-default login` + Firebase token refresh.

**Skills** (depends on [skills-data-model.md](skills-data-model.md))
- **`aiplatform skill list [--env=local]`** — list skills in Firestore for the selected environment.
- **`aiplatform skill new <name>`** — scaffold `skills/<name>/skill.yaml` + system_prompt template + tools stub from a Cookiecutter-style template.
- `aiplatform skill push <name> [--env=dev] [--dry-run]` — sync local YAML → Firestore. Dry-run is the default.
- `aiplatform skill pull <name> [--env=dev]` — pull deployed YAML to local file (overwrites with confirmation).
- `aiplatform skill diff <name> [--env=dev]` — three-way diff between local YAML, deployed YAML, and the last `pulled-at` snapshot.
- **`aiplatform skill run <name> [--prompt=...]`** — terminal-mode skill invocation. Phase 1 ships this as a thin shell-out to `adk run <agent_path>` (Option 2a from the CLI research). Phase 2+ may replace it with a custom Rich+prompt-toolkit REPL — that's a separate design doc ([agent-cli.md](agent-cli.md), planned).

**Documents** (depends on `ailang-parse` integration)
- **`aiplatform doc parse <file> [--format=json|a2ui]`** — local-only `ailang-parse` invocation, no backend needed. Useful for verifying A2UI rendering on a new file type.
- `aiplatform doc render <file>` — opens `http://localhost:3000/preview?file=...` in the default browser, after copying the file into a watched fixture directory.

**MCP** (depends on [mcp-app-integrations.md](mcp-app-integrations.md))
- `aiplatform mcp list [--skill=<id>]` — show MCP servers registered for the current env, optionally filtered by skill.
- `aiplatform mcp probe <server_id>` — call `tools/list` against the registered server, dump tool schemas as JSON.
- `aiplatform mcp call <server_id> <tool> [args...]` — direct invocation, useful for debugging tool wiring without the agent in the loop.

**Eval** (depends on `backend/tests/eval/`)
- `aiplatform eval list` — list evalsets in `backend/tests/eval/`.
- `aiplatform eval run [name] [--rubric=...]` — wrapper around `adk eval`, with output formatted for the terminal.

**Deploy** (depends on [cloud-infrastructure.md](cloud-infrastructure.md) + existing `cloudbuild.yaml`)
- `aiplatform deploy <env>` — gated wrapper around the Cloud Build trigger for the selected environment. Refuses non-`main` branches for dev, refuses non-`prod` branches for prod. Always shows the diff and prompts for `yes`.
- `aiplatform deploy status <env>` — query Cloud Run + Cloud Build for the latest revision and build status of `platform-backend` + `platform-frontend` + `mcp-ext-apps-map` in the selected environment.

**Buckets / Folders / Groups / Access** (shipped — RESOURCE-ACCESS sprint, 2026-04-22; depends on [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md))

Matches `aiplatform --help` verbatim as of sprint close:

- **`aiplatform bucket list`** — list buckets visible to the caller.
- **`aiplatform bucket show <bucket_id>`** — show a single bucket by ID.
- **`aiplatform bucket create`** — create a new bucket.
- **`aiplatform bucket grant <bucket_id> <user_id>`** — append user to a `specific`-access bucket.
- **`aiplatform bucket revoke <bucket_id> <user_id>`** — remove user from a `specific`-access bucket.
- **`aiplatform folder list <bucket_id>`** — list folders visible to the caller in a bucket.
- **`aiplatform folder create <bucket_id>`** — create a folder (inherits or overrides bucket access).
- **`aiplatform groups add-user <group> <user_id>`** — add a user to a group. *(Backend endpoint pending — v6.1.)*
- **`aiplatform groups remove-user <group> <user_id>`** — remove a user from a group. *(v6.1.)*
- **`aiplatform groups list-user <user_id>`** — list all groups a user belongs to. *(v6.1.)*
- **`aiplatform access check`** — dry-run: would the current user (or `--as-email`) have access to a resource? *(Backend endpoint pending — v6.1.)*
- **`aiplatform skill probe <skill_id>`** — fire one chat turn at a skill with `?probe=1` set, read the SSE stream, and print the per-stage TTFT breakdown from the backend's `LATENCY_REPORT` AG-UI Custom event. Flags: `--message`, `--session`, `--timeout`, `--json`. The terminal-friendly surface for the [TTFT instrumentation](ttft-instrumentation.md) measurement track; foundation for the M5 A/B baseline (full vs off mode).

Auth: the `AitanaClient` reads a bearer token from `$AITANA_ID_TOKEN`, falling back to `gcloud auth print-identity-token`. The `--env` flag (default `local`) resolves the backend URL via `AITANA_API_URL` / `AITANA_API_URL_<ENV>`.

**Bulk** (port from v5 — `<your-v5-source>/aitana/` (the v5 source))
- `aiplatform bulk inspect <skill_id>` — port of v5 `aiplatform inspect`. Show available tools, configs, permissions for a skill.
- `aiplatform bulk extract <skill_id> <gs://bucket/...>` — port of v5 `batch-extract`. Same flag surface (`--prompt`, `--schema-name`, `--max-files`, `--batch-size`, `--file-pattern`, `--output-dir`).
- `aiplatform bulk process files <skill_id>` — port of v5 `process files`.
- `aiplatform bulk process batch <skill_id>` — port of v5 `process batch` (JSONL input).
- `aiplatform bulk create-sample` — port of v5 `create-sample`.

### Package Layout

```
cli/
├── pyproject.toml             # Click, httpx, rich, pyyaml, lazy deps for ailang-parse, google-cloud-firestore
├── README.md                  # Install + quickstart
├── aiplatform/
│   ├── __init__.py
│   ├── __main__.py            # Click root group
│   ├── config.py              # ~/.aiplatform/config.yaml loader (env URLs, default env)
│   ├── auth.py                # gcloud ADC + Firebase token cache
│   ├── api_client.py          # httpx wrapper around v6 FastAPI (request IDs, retries)
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── dev.py             # up/down/logs/status — uses subprocess + a small process supervisor
│   │   ├── auth.py
│   │   ├── skill.py           # new/list/run/push/pull/diff
│   │   ├── doc.py             # parse/render
│   │   ├── mcp.py             # list/probe/call
│   │   ├── eval.py
│   │   ├── deploy.py
│   │   └── bulk.py            # ports from v5 — inspect/extract/process/create-sample
│   ├── templates/
│   │   └── skill/             # cookiecutter-style template for `aiplatform skill new`
│   │       ├── skill.yaml.j2
│   │       ├── system_prompt.md.j2
│   │       └── README.md.j2
│   └── ports/                 # v5 code ported with Sunholo stripped
│       ├── batch_extract.py
│       └── bulk_files.py
└── tests/
    ├── test_dev.py
    ├── test_skill.py
    ├── test_doc.py
    └── test_bulk_extract.py
```

### Backend Touchpoints

The CLI is mostly a client of existing endpoints, but it needs three small additions:

- `GET /api/v1/skills` — list skills the authenticated user can see (the frontend already needs this; CLI consumes the same endpoint).
- `GET /api/v1/mcp_servers?skill_id=<id>` — list MCP servers registered for a skill, returning the same shape as `mcp_servers.yaml` plus a `health` field (for `aiplatform mcp list`).
- `POST /api/v1/admin/skills/<id>` — upsert skill config (for `aiplatform skill push`). Gated to authenticated developers; see [auth-and-permissions.md](auth-and-permissions.md) for the role check.

No new endpoints for `bulk` — those reuse the existing chat/extract endpoints exactly as v5 did.

### Process Orchestrator (`aiplatform dev up`)

The non-trivial bit. The orchestrator needs to:
1. Read a `cli/services.yaml` file that maps service names → start commands → health-check URLs.
2. Spawn each service as a child process, capture stdout/stderr line-by-line, prefix with a colored service name, multiplex to the parent terminal.
3. Wait for each health-check URL to become 200 OK before declaring the service "up".
4. On Ctrl-C, send SIGTERM to all children, wait briefly, then SIGKILL.

Use `python-rich` for output formatting. Avoid Docker Compose — the v6 backend uses `uv run` and the frontend uses `npm run dev`, both of which already do hot-reload locally and would lose that if containerized.

```yaml
# cli/services.yaml — initial entries; new services are added here as features ship
services:
  backend:
    cmd: ["uv", "run", "uvicorn", "fast_api_app:app", "--host", "0.0.0.0", "--port", "1956", "--reload"]
    cwd: backend
    health: http://localhost:1956/health
  frontend:
    cmd: ["npm", "run", "dev"]
    cwd: frontend
    health: http://localhost:3000
  mcp-ext-apps-map:
    cmd: ["npm", "run", "start:http"]
    cwd: ../ext-apps/examples/map-server   # path is overridable via env var
    health: http://localhost:3001/mcp
    optional: true   # don't fail dev up if not present
  adk-playground:
    cmd: ["uv", "run", "make", "playground"]
    cwd: backend
    health: http://localhost:8501
    optional: true
```

### Architecture Diagram

```
                    ┌──────────────────────────┐
                    │  developer terminal      │
                    │  $ aiplatform dev up         │
                    └────────────┬─────────────┘
                                 │
                                 ▼
            ┌────────────────────────────────────────┐
            │  aiplatform (Python, Click)            │
            │  ─ commands/dev.py (orchestrator)      │
            │  ─ commands/skill.py (Firestore CRUD)  │
            │  ─ commands/bulk.py (ported v5)        │
            │  ─ api_client.py (httpx + auth)        │
            └─┬──────────┬──────────┬──────────┬────┘
              │          │          │          │
              ▼          ▼          ▼          ▼
       ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
       │ uv run   │ │ npm run  │ │ npm run  │ │ FastAPI :1956│
       │ uvicorn  │ │ dev      │ │ start:   │ │ (when CLI    │
       │ :1956    │ │ :3000    │ │ http     │ │  hits API)   │
       │ + reload │ │          │ │ :3001    │ │              │
       └──────────┘ └──────────┘ └──────────┘ └──────┬───────┘
                                                     │
                                                     ▼
                                          ┌──────────────────┐
                                          │ Firestore +      │
                                          │ Firebase Auth +  │
                                          │ GCS              │
                                          └──────────────────┘
```

## Implementation Plan

### Phase 1: Bring-up surface (~1.5 days)
- [ ] Scaffold `cli/` Python package with Click + pyproject.toml + `[project.scripts]` entry point (~50 LOC)
- [ ] `aiplatform/config.py` — load `~/.aiplatform/config.yaml`, env-URL resolver (~80 LOC)
- [ ] `aiplatform/auth.py` — gcloud ADC detection + Firebase token cache (port `auth_manager.py` from v5, strip Sunholo) (~120 LOC)
- [ ] `aiplatform/api_client.py` — httpx wrapper with request-ID injection and retry (port `api_client.py` from v5) (~150 LOC)
- [ ] `commands/auth.py` — `aiplatform auth` and `aiplatform auth login` (~60 LOC)
- [ ] `commands/dev.py` — orchestrator + `services.yaml` loader + Rich-multiplexed output (~250 LOC)
- [ ] `cli/services.yaml` — initial three entries (backend, frontend, mcp-ext-apps-map optional) (~30 LOC)
- [ ] `commands/skill.py` partial — `list` and `run` (the `run` is a thin shell-out to `adk run`) (~100 LOC)
- [ ] `commands/doc.py` — `parse` only (lazy-import `ailang_parse`) (~80 LOC)
- [ ] Smoke test: `aiplatform dev up`, `aiplatform auth`, `aiplatform skill list`, `aiplatform skill run hello-world`, `aiplatform doc parse fixture.docx` all work

### Phase 2: Bulk extraction port (~1 day, can run in parallel with Phase 1)
- [ ] Port `aitana/processors/batch_extract.py` from v5 → `aiplatform/ports/batch_extract.py`, strip Sunholo imports (~400 LOC)
- [ ] Port `aitana/processors/bulk_files.py` from v5 → `aiplatform/ports/bulk_files.py` (~300 LOC)
- [ ] `commands/bulk.py` — `inspect`, `extract`, `process files`, `process batch`, `create-sample` (~250 LOC)
- [ ] `tests/test_bulk_extract.py` — fixture-driven smoke test against the local backend (~150 LOC)
- [ ] Verify behavioural parity: run a real bulk extract against a small fixture bucket, compare output CSVs to v5 output

### Phase 3: Skill push/pull/diff (~0.5 day, depends on M1A.4 skills CRUD)
- [ ] `aiplatform skill new` — Cookiecutter-style template + filesystem scaffold (~120 LOC)
- [ ] `aiplatform skill push` — YAML → Firestore upsert via the new admin endpoint (~80 LOC)
- [ ] `aiplatform skill pull` — Firestore → YAML with confirmation prompt (~60 LOC)
- [ ] `aiplatform skill diff` — three-way diff using `difflib` (~100 LOC)
- [ ] Add the two new backend endpoints (`GET /api/v1/skills`, `POST /api/v1/admin/skills/<id>`) — handled in `agent-factory.md` follow-up

### Phase 4: MCP probing (~0.25 day, depends on [mcp-app-integrations.md](mcp-app-integrations.md))
- [ ] `commands/mcp.py` — `list`, `probe`, `call` (~200 LOC, leans on `mcp` Python library)

### Phase 5: Eval and deploy wrappers (~0.5 day, depends on Phase 1 + cloudbuild config)
- [ ] `commands/eval.py` — wrapper around `adk eval` (~80 LOC)
- [ ] `commands/deploy.py` — gated `gcloud builds triggers run` wrapper (~150 LOC)

**Total v0:** ~3 days bring-up + ~1.5 days follow-up = ~4.5 days, but only ~1.5 days is on the bring-up critical path (Phase 1). Phases 2-5 land alongside the features they depend on.

## Migration & Rollout

**Database Migrations:** None.

**Feature Flags:** None — the CLI is an external tool, ships independently of backend versions.

**Rollback Plan:** `pip install aiplatform==<previous_version>`. The CLI is a leaf dependency with no shared state.

**Environment Variables:**
- `AITANA_ENV` — default environment for CLI commands (`local` | `dev` | `test` | `prod`); falls back to `local`
- `AITANA_API_BASE` — override the env URL (used by tests and local development against alternate backends)
- `AIPLATFORM_CONFIG` — override `~/.aiplatform/config.yaml` path (used by tests)
- `MCP_EXT_APPS_MAP_PATH` — local path to a checked-out `ext-apps` repo for the optional `mcp-ext-apps-map` service in `aiplatform dev up`

**Distribution:**
- Phase 1: developer-only — `pip install -e cli/` from a checkout
- After v6.0.0 ship: publish to PyPI as `aiplatform` so workshop attendees can `pip install aiplatform`
- Versioning follows the platform: `aiplatform==6.0.0` matches `platform v6.0.0`

## Testing Strategy

### CLI Tests (pytest + click.testing.CliRunner)
- [ ] `test_dev.py` — orchestrator spawns subprocess mocks, asserts health-check polling, asserts SIGTERM cleanup on Ctrl-C
- [ ] `test_skill.py` — `skill new` produces a valid YAML; `skill push --dry-run` against a Firestore emulator emits the right diff
- [ ] `test_doc.py` — `doc parse fixture.docx` produces non-empty `Block` ADT
- [ ] `test_bulk_extract.py` — port the v5 fixture-driven smoke test, run against a local backend
- [ ] `test_auth.py` — gcloud ADC detection with mocked credentials

### Manual Testing (workshop dry-run)
- [ ] On a fresh checkout: `pip install -e cli/`, `aiplatform auth`, `aiplatform dev up` — confirm all four services come up
- [ ] `aiplatform skill new my-test-skill`, edit the YAML, `aiplatform skill push --dry-run` — confirm the diff
- [ ] `aiplatform doc parse fixtures/q1-financial.docx` — confirm A2UI JSON output
- [ ] `aiplatform bulk extract doc-analyst gs://test-bucket/contracts/ --max-files 3` — confirm CSV outputs match v5
- [ ] `aiplatform skill run hello-world --prompt "hi"` — confirm `adk run` shells out cleanly

## Security Considerations

- **Credentials:** never cached on disk by the CLI itself. gcloud ADC is read via `google.auth.default()`. Firebase tokens are kept in-memory for the lifetime of a single command and discarded.
- **Skill push gating:** the `POST /api/v1/admin/skills/<id>` endpoint requires the developer's Firebase token to carry the `developer` custom claim. Without it, `aiplatform skill push` returns 403.
- **Deploy gating:** `aiplatform deploy prod` requires both `--yes` and an interactive `type the env name to confirm` prompt. Refuses to run if `git status` is dirty. Refuses if `git rev-parse --abbrev-ref HEAD` doesn't match the env's expected branch.
- **Subprocess execution:** `aiplatform dev up` only launches commands listed in `cli/services.yaml`, which is checked into the repo. No arbitrary command execution from user input.
- **Bulk extract:** inherits v5's auth model — calls go through the FastAPI `/process` endpoint with a Firebase token, no direct GCS reads from the CLI process.
- **Audit:** every API call from the CLI emits a request ID; backend logs the request ID + the developer's email. Cloud Trace correlates the request ID across services.

## Performance Considerations

- **CLI startup:** target <100ms cold start. Achieved by lazy-importing heavy deps (`google.cloud.firestore`, `ailang_parse`, `httpx`) inside command handlers, not at module top-level.
- **`aiplatform dev up` startup:** dominated by `npm run dev` (~3-5s) and `uv run uvicorn --reload` (~1-2s). Parallel-spawn all services and aggregate the health-check window — total wall time should be <8s on a warm machine.
- **`aiplatform bulk extract` throughput:** preserves v5's parallel batch behaviour (5-file default `--batch-size`). No regression target.
- **Memory:** target <200MB RSS for any single command. The orchestrator's child processes are out of scope.

## Success Criteria

- [ ] CLI tests passing (`cd cli && uv run pytest`)
- [ ] Lint clean (`cd cli && uv run ruff check`)
- [ ] `aiplatform dev up` brings up backend + frontend + mcp sidecar in a single terminal in <10s
- [ ] `aiplatform skill list` returns the seeded skills from `local` Firestore
- [ ] `aiplatform skill run hello-world` opens an interactive terminal session via `adk run` and prints model output
- [ ] `aiplatform doc parse fixtures/q1-financial.docx` produces valid A2UI JSON
- [ ] `aiplatform bulk extract doc-analyst gs://test-bucket/contracts/ --max-files 3` produces CSV outputs matching the v5 reference output (byte-equal modulo timestamps)
- [ ] Workshop dry-run: a fresh laptop can go from `git clone` → "interactive prompt against a deployed skill" in <5 minutes
- [ ] `aiplatform --help` documents every command and links to the right design doc for each subsystem

## Open Questions

- **Click vs Typer:** Click is more proven, Typer has nicer type-driven UX. v5 uses Click. Default to Click unless Phase 1 finds a concrete pain point — ~30 minutes to switch later.
- **Process orchestrator: roll our own vs `honcho` / `overmind` / `mprocs`:** `honcho` is the closest off-the-shelf option (Foreman in Python). It would shrink Phase 1 by ~150 LOC but adds a dependency and loses some control over health-check polling. Decide in Phase 1 — try `honcho` first, fall back to a hand-rolled supervisor if its log multiplexing isn't clean enough.
- **`aiplatform dev up` on Windows:** Mark works on macOS, the workshop kit assumes Mac/Linux. Defer Windows support unless an attendee asks. Document the limitation in the README.
- **Skill template content:** what should `aiplatform skill new` actually scaffold? Depends on the final shape of [skills-data-model.md](skills-data-model.md). Sync with that doc before Phase 3.
- **PyPI publishing pipeline:** add to `cloudbuild.yaml` or a separate GitHub Action? Defer until after v6.0.0 ships — Phase 1 only needs `pip install -e`.

## Related Documents

- [skills-data-model.md](skills-data-model.md) — skill YAML schema that `aiplatform skill new/push/pull` consumes
- [agent-factory.md](agent-factory.md) — backend code the CLI's `skill push` endpoint will sit alongside
- [tools-porting-guide.md](tools-porting-guide.md) — context for the v5 `aitana/processors/` code being ported to `aiplatform/ports/`
- [auth-and-permissions.md](auth-and-permissions.md) — `developer` custom claim that gates `skill push` and `deploy`
- [mcp-app-integrations.md](mcp-app-integrations.md) — wires `aiplatform mcp list/probe/call` into the registry
- [cloud-infrastructure.md](cloud-infrastructure.md) — the Cloud Build trigger that `aiplatform deploy` wraps
- v5 source: `<your-v5-source>/aitana/` (the v5 source) — port target
- **Track 2 follow-up:** an `agent-cli.md` design doc is planned (post-v6.0.0) for an interactive ADK-Runner-driven REPL with Rich/prompt-toolkit. That is *not* this doc — this doc is the boring dev tool. The agent-CLI doc will also cover [ADK code execution executors](#related-documents) (`ContainerCodeExecutor` etc.) as a path for letting agents shell out to `aiplatform` itself.
