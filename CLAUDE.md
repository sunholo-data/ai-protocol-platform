# CLAUDE.md — Aitana Platform v6

## Security Hard Rules

**NEVER make confidential customer content publicly accessible — including
derived artefacts.**

This includes (non-exhaustive):

- Anything from a tagged-access bucket or skill (e.g.
  `gs://multivac-acme-energy-bucket/`). These buckets host
  customer-confidential contracts, financials, and reference data.
- Page thumbnails, page screenshots, preview images, or any other
  derivative artefact rendered from a private document. **Page 1 of a
  contract still leaks names, parties, jurisdiction, and dates.**
- Snippets, summaries, extracted clauses, or block excerpts pasted into
  Slack/email/code-review tools that egress outside the Aitana GCP
  project edge.
- Public Cloud Run services, public GCS buckets (e.g.
  `gs://aitana-public-bucket/`), CDN-cached URLs, or any path served
  without auth — **`storage.googleapis.com/.../...png` is on the public
  internet, not behind your Firebase login.**

When a feature needs preview / thumbnail / snippet rendering for
restricted content, the artefact must be served behind the same access
gate as the source document — typically a new backend route
(e.g. `GET /api/documents/{doc_id}/thumbnail`) that re-checks
`request.auth.uid == doc.userId` (or equivalent group-tag policy)
before streaming bytes. The backend may fetch from any internal bucket
using its own SA; the frontend must request via `/api/proxy/...` with
the user's Firebase Bearer.

**If you have any doubt about whether content is "OK to make public" —
stop and ask, do not act.** Removing a leak after the fact is
expensive (GCS edge caches stale-serve for hours; overwriting with
blanks is a partial mitigation, not a guarantee). The cost of asking is
seconds; the cost of leaking is incident response, customer trust, and
GDPR/contract exposure.

This rule is **architectural, not advisory**: if a proposed design
would publish a derivative of private content, refuse the design and
propose an authenticated alternative.

## Overview

Aitana Platform v6 is a greenfield rebuild of the Aitana AI assistant platform. Skills replace assistants as the user-facing abstraction. Google ADK replaces Sunholo for agent orchestration.


## Architecture

- **Backend**: Python 3.11+, FastAPI, Google ADK — `backend/`
- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind — `frontend/`
- **CLI**: `aiplatform` CLI tool — `cli/`
- **Infrastructure**: Cloud Run (same as v5), Firestore, Firebase Auth

### Key Principles

1. **Pure ADK + FastAPI** — no Sunholo, no LangChain, no Flask
2. **Skills, not assistants** — skills are the primary user-facing concept
3. **Protocol-native** — AG-UI (streaming), A2UI (declarative UI), MCP Apps (tool UIs), A2A (discovery), MCP (tools)
4. **Three model providers** — Gemini, Claude, OpenAI
5. **Copy proven code from v5** — don't reinvent, wrap as ADK FunctionTools
6. **Speed** — first token <1s without tools, <3s with tools
7. **Protocols first for UI — never bespoke React per tool/skill.** This is
   architectural, not advisory (like the security rule above):
   - **Tool results render as A2UI on the workbench (workspace surface).** A
     tool's structured output is turned into A2UI (server-side result→A2UI
     mapping) and rendered by the generic `A2UISurfaceMount` — NOT a bespoke
     component keyed off tool name. If you find yourself writing a React
     component to render a specific tool's result, stop: register an A2UI
     mapping instead. (See `docs/design/v6.7.0/tool-results-as-a2ui.md`.)
   - **User interaction that feeds data back to the AI renders as A2UI in the
     chat area** (forms, pickers, confirmations) — via the surface-action /
     surface-action-run loop, not a chat-embedded React widget.
   - **Auto-focus new workbench elements** — when a new workspace surface /
     workbench element arrives, switch focus to it (don't just badge the tab).
   - The A2UI Basic-catalog vocabulary lives in the `agent-protocols` skill
     (`references/a2ui-v0.9-basic-catalog.md`) — read it before designing a
     surface; don't re-derive.
   - Two wire hazards to handle **once** (learned the hard way): tool results
     are double-wrapped `{"result":"{…}"}` (unwrap via `src/lib/toolResult.ts`)
     and >50K results are offloaded to an artifact (`_handle_large_output`) —
     any tool feeding a UI mapping must be marked so it's never offloaded.
   - **"A2UI won't render in the Workspace" is a RECURRING bug — do not
     re-derive the render path, diff against a known-good one.** The full
     playbook (proven path + every trap) lives in the folder READMEs that
     auto-load when you touch that code: [`backend/adk/CLAUDE.md`](backend/adk/CLAUDE.md)
     (emission — where it usually breaks) and
     [`frontend/src/components/protocols/CLAUDE.md`](frontend/src/components/protocols/CLAUDE.md)
     (render). Symptom: the
     agent narrates "I've updated the Workspace" but the tab stays on its empty
     state, because the result never registered as a workbench artifact
     (`artifactCount` stays 0, so any launcher/empty-state never yields). A
     result becomes a workbench artifact ONLY if ALL of these hold — check each
     against a feature that already works (7.3 `tool-results-as-a2ui`, 7.5
     `workbench-artifacts-model`): (1) a result→A2UI mapping is registered for
     the tool; (2) the tool is offload-EXEMPT (the >50K hazard above); (3) the
     result isn't double-wrapped past the unwrap; (4) artifact metadata is
     attached so `SurfaceRegistry` counts it; (5) it's emitted to the surfaceId
     the Workspace renders; (6) the SSE endpoint binds a per-request
     `LatencyTracker` to the async context (see next point). **The
     action-triggered-run path (`surface-action-run`, e.g. a launcher's
     `start_compare`) is a SEPARATE emission path from a normal chat turn — and
     the usual culprit.** The emitter/callbacks and mapping ARE wired on both
     paths (both build via `create_agent`), but the surface event is delivered
     by enqueuing onto a per-request `LatencyTracker` bound to the async context
     via `set_current_tracker` and drained by `stream_agui_events`. The chat
     endpoint (`fast_api_app.py` `stream_skill`) binds/resets it; **any SSE
     endpoint that runs `stream_agui_events` MUST do the same bind/reset**, or
     `get_current_tracker()` returns the module NULL tracker and every
     `emit_a2ui_surface` silently no-ops — nothing reaches the wire, the
     frontend registers no artifact, the launcher never hides. (This was the
     compare-launcher bug, fixed 2026-07-11 by binding the tracker in
     `surface-action-run`.) For Model-B skills
     (`a2ui.enabled: false`) the agent never authors the UI — the backend
     out-of-model emitter does; if the agent is describing the result in prose,
     the emitter didn't fire.
   - **Verification requirement (non-negotiable): jsdom/unit tests passing does
     NOT mean it renders.** That gap is exactly how this ships broken (7.2/7.3/
     7.5 deferred real-browser E2E and the compare render still failed live). An
     A2UI-workspace-render change is not done until a **real browser** — or at
     minimum a real AG-UI event stream showing the `A2UI_SURFACE` CUSTOM event
     emit AND `SurfaceRegistry` register it — confirms it. Split
     backend-emission from frontend-render by streaming a real run
     (`aiplatform skill …`) and inspecting the events before touching React.

8. **NEVER SILENT — every user interaction gives visible feedback.** This is
   architectural, not advisory (like the security and protocols-first rules). A
   click, launcher, action-triggered run, chat send, or tool call must ALWAYS
   surface: (a) an **immediate** pending/working state (never a button that just
   greys with nothing else), (b) **live progress** while it runs (events/tool
   calls in the Activity tab — an in-flight run is always observable), and (c) a
   **terminal state** — a result OR a **visible error/notice**, never a silent
   resolve. A hang with no terminal state, or an error swallowed to
   `console.warn` while the UI "stays put", is a BUG regardless of tests. The
   user must never sit and guess. Concretely: gate rejections (403 not opted
   in), network failures, `RUN_ERROR`, timeouts, and empty results must each
   render something the user can see. Every one of the action-run bugs
   (2026-07-11: launcher renders nothing, Activity blind, silent 4xx fallback)
   was a violation of this. When adding any interactive surface, verify the
   error and empty paths render — not just the happy path.

9. **FRIENDLY NAMES, NOT RAW IDS — opaque ids are backend addressing only.**
   This is architectural, not advisory (like #7/#8). Skill/doc UUIDs
   (`26124699-f558-…`), **sanitized ADK agent names** (`s_26124699_f558_…`, from
   `_safe_agent_name`), and `gs://` paths are precise for backends but *bad for
   AIs and humans to know "what is what"* — AIs hallucinate/transform them (e.g.
   copy a sanitized agent name into `request_handoff`) and humans can't tell what
   is what. So any surface an AI or a human **reads or writes** must speak
   friendly names, and the backend resolves **friendly→id, never the reverse**:
   - **Accept aliases on input.** A tool/route taking an id must resolve the
     friendly form (slug, filename, display name, sanitized agent name) back to
     the canonical id — build an alias map, don't require the exact id.
   - **Present friendly on output.** Show filename / display name / slug; frame
     any id the AI must echo as an opaque `[ref: <id>]` with "don't show the user".
   - **Normalize downstream to the canonical id** at the boundary (validators,
     context payloads, and the confirm→switch route validate by raw id).
   - **Test with the DEPLOYED id format (UUID doc-ids), not the local fixture**
     (slug-as-doc-id passes locally, breaks deployed).
   This is a **recurring bug class** — instances: `find_by_slug` delegate
   fallback (deployed slug vs UUID), `list_documents` friendly filenames +
   `[ref:]` hint, `request_handoff` alias resolution, confirm→switch canonical-id
   normalization. When adding anything that takes or shows an id, apply this.

### Protocol Stack

```
Layer 4 — UI: A2UI (declarative JSON) + MCP Apps (sandboxed iframes)
Layer 3 — Transport: AG-UI / CopilotKit (SSE streaming)
Layer 2 — Coordination: A2A (agent discovery) + MCP (tools)
Layer 1 — Framework: Google ADK (orchestration, sessions, memory)
```

## Project Structure

```
platform/
├── frontend/          # Next.js 15 + React 19
├── backend/           # FastAPI + Google ADK
│   ├── app.py         # Root ADK agent definition
│   ├── fast_api_app.py # FastAPI application (uses ADK's get_fast_api_app)
│   ├── skills/        # Skill config, processor, templates
│   ├── adk/           # Agent factory, tool wrappers, sessions
│   ├── tools/         # AI search, file browser, code execution, MCP
│   ├── channels/      # Telegram (primary), email, WhatsApp
│   ├── protocols/     # A2A, MCP server, AG-UI
│   ├── auth/          # Firebase auth, permissions
│   ├── db/            # Firestore client, Pydantic models
│   ├── observability/ # OpenTelemetry, logging
│   └── tests/         # Unit, integration, eval
├── cli/               # `aiplatform` CLI
├── docs/              # Design docs, versioned
├── cloudbuild.yaml    # Branch-based deployment
└── firestore.rules    # Skills collection rules
```

## Commands

### Backend
```bash
cd backend
make install           # Install dependencies with uv
make dev               # FastAPI on port 1956 with hot-reload
make playground        # ADK dev UI on port 8501
make test              # Run all tests
make test-fast         # Fast CI tests (skip slow/integration)
make eval              # Run ADK evaluation suite
make lint              # Ruff + codespell
make format            # Auto-format with ruff
```

**CRITICAL:** Always use `uv run` for backend commands. Never use global `python` or `pip`.

### Frontend
```bash
cd frontend
npm install
npm run dev            # Next.js on port 3000
npm run build          # Production build
npm run quality:check:fast  # Lint + typecheck
```

### Server Ports
- Frontend: http://localhost:3000
- Backend API: http://localhost:1956
- ADK Playground: http://localhost:8501

## Deployment

Same GCP projects as v5, but v6 runs as **new parallel Cloud Run services** so v5 stays untouched during bring-up. DNS cutover is a separate later decision.

- **Project IDs**: `your-project-id`, `your-project-id-test`, `your-project-id-prod` (unchanged)
- **v6 Cloud Run services**: `platform-backend`, `platform-frontend` (new; live in dev once CI-WIRE lands)
- **v5 Cloud Run services**: `backend-api`, `frontend` (still running, will be decommissioned after DNS cutover)
- **How code reaches each env (v6.20.0 build-once artifact promotion)** — canonical runbook: [docs/ops/promotion.md](docs/ops/promotion.md).

  | Env | Fires on | Command |
  |---|---|---|
  | **dev** | push to `dev` | `git push origin dev` |
  | **test** | **a `v*` git tag** (`trigger-aitana-test-release`), or a push to `test` (legacy, still enabled) | `aiplatform deploy release --version vX.Y.Z --yes` |
  | **prod** | push to `prod` — **branch-based, NOT tags yet** | branch merge |

  The tag release is what builds the immutable `:vX.Y.Z` images a later
  promotion copies by digest. Promotion (`make promote FROM=… TO=… VERSION=…`
  → `scripts/promote-env.sh` → `cloudbuild.promote.yaml`) copies tested
  backend+toolbox images and rebuilds only the UI. Valid edges are
  `dev→test` and `test→prod` only — no `dev→prod` shortcut.

  **Prod is deliberately still branch-driven**: no `trigger-aitana-prod-promote`
  or prod release trigger is provisioned, so `--to prod` fails with "trigger not
  found" until prod is flipped to promote-only after v6 is ratified on test.
  The older per-service branch triggers (`trigger-aitana-{test,prod}-platform-*`)
  remain enabled alongside the tag flow — the tag path was added beside them,
  not as a replacement. Default branch is `dev`.
- **Cloud Build connection**: `github-voight` in `your-deploy-project-id/europe-west1` (authorizer `sunholo-voight-kampff`). v5 still uses the older `github` connection.
- **SA for Cloud Run**: `platform@{project_id}.iam.gserviceaccount.com`
- **CI gate**: `.github/workflows/ci.yml` — lint + test-fast on PR and push to `dev`.
- **Post-deploy smoke**: both `cloudbuild.yaml` pipelines end with a smoke step that curls critical endpoints and fails the build on any non-200. Run the same checks from a laptop with `./scripts/smoke-deployed.sh [dev|test|prod] [all|frontend|backend]`. Live service URLs are recorded in [docs/ops/deployed-urls.md](docs/ops/deployed-urls.md).

## Key Differences from v5

| v5 | v6 |
|---|---|
| Assistants | Skills |
| Sunholo + Flask | ADK + FastAPI |
| Custom SSE streaming | AG-UI protocol |
| Bespoke rendering | A2UI + MCP Apps |
| LangChain | Removed |
| Custom memory (10 files) | ADK MemoryService |
| Custom content limiting | ADK Artifacts + Compaction |
| first_impression → orchestrator → smart_model | ADK agent loop (one pass) |
| Langfuse v2 SDK | OpenTelemetry → Cloud Trace + Cloud Logging + BigQuery (all internal) |
| Custom TTS | Gemini Live (ADK LiveRunner) |

## Copying Code from v5

When copying v5 code, follow this pattern:
1. Read the v5 file from `<your-v5-source>/`
2. Strip Sunholo imports and dependencies
3. Wrap as ADK FunctionTool if it's a tool
4. Place in the correct v6 directory (see design doc for mapping)
5. Write tests

**Key v5 files to copy (see design doc for full list):**
- `backend/tools/` → `backend/tools/` (wrap as ADK FunctionTools)
- `backend/telegram_service.py` → `backend/channels/telegram.py`
- `backend/email_integration.py` → `backend/channels/email.py`
- `backend/a2a_config.py` → `backend/protocols/a2a.py`
- `backend/tool_permissions.py` → `backend/auth/permissions.py`
- `backend/tools/mcp_servers.py` → `backend/tools/mcp/registry.py`

## Project Skills (`.claude/skills/`)

Project-local skills auto-load when their trigger keywords match. Live in `.claude/skills/<name>/SKILL.md` with optional `resources/` and `scripts/` siblings. Adding a new skill: `~/.claude/skills/skill-builder/scripts/create_skill.sh --project <name> "<description with triggers>"` or invoke the `skill-builder` skill directly.

**Aitana-specific operational skills** (load when debugging the v6 platform):

- **`aiplatform-cli`** — Operating manual for the `aiplatform` CLI when debugging from a terminal. Bundles a token-mint script that mints a fresh `AIPLATFORM_ID_TOKEN` for the dedicated `whoami-test@yourcompany.test` user, plus curl fallbacks for endpoints the CLI doesn't yet wrap (sessions, skills, whoami, documents). Use when the next step is to reproduce a bug against the running backend, probe TTFT, or run a one-shot API call.
- **`aitana-adk-testing`** — ADK session/event/artifact inspection via the HTTP endpoints `get_fast_api_app(web=True, ...)` ships. Use when the question is "where do messages live", "did the loader save the artifact", or anything that bypasses the Firestore mirror.
- **`aitana-frontend-verify`** — Drive a real Chrome via the chrome-devtools MCP to verify frontend behaviour static checks can't see (SSE streams, hydration, auth state, DOM after click).
- **`platform-deploy`** — dev → test → prod promotion manual, including the three-repo topology, IAM cascade, and pre-promotion audit procedure.
- **`aitana-template-publish`** — refresh the public template at `sunholo-data/ai-protocol-platform`. Load when the user mentions publishing/refreshing the template, GitHub secret-scanner alerts on the template, or any operation that copies content out of this repo. Documents the sanitize pipeline, security gates (Firebase Web API keys are NOT safe in public), and the one-command refresh flow.
- **`cloud-run-diagnostics`** — diagnose Cloud Run service issues (cold starts, IAM, connectivity, deploy failures).

**Cross-project skills** (used everywhere, not Aitana-specific):

- **`adk-cheatsheet` / `adk-dev-guide` / `adk-eval-guide` / `adk-deploy-guide` / `adk-scaffold`** — ADK API + lifecycle references.
- **`design-doc-creator`** — scaffolds new design docs in the right v6.X.Y layout, scores against product axioms, registers in SEQUENCE.md.
- **`sprint-planner` / `sprint-executor` / `sprint-evaluator`** — the planning → execution → quality-check loop for non-trivial work.
- **`skill-builder`** (global) — for creating/optimizing skills like the ones above.
- **`agent-protocols`** — Disambiguates the four-protocol stack (AG-UI / A2UI / MCP / MCP Apps / Agent Skills) with vendored offline specs. Load when writing design docs, implementing a new protocol surface, or verifying spec compliance. Run `.claude/skills/agent-protocols/scripts/refresh-specs.sh` quarterly to update vendored specs.

> **Fork note:** Skills marked "Aitana-specific" above (`aiplatform-cli`, `platform-deploy`,
> `aitana-adk-testing`, `aitana-frontend-verify`, `aitana-template-publish`, `cloud-run-diagnostics`) live only in the
> Aitana internal repo and are not shipped in the template. The `agent-protocols` skill is shipped
> in the template and is the recommended protocol-reference skill for all forks.

**These skills expand as the project grows.** When a recurring debug task or workflow emerges that's worth >10 minutes per session of re-derivation (auth incantations, multi-step CLI sequences, architecture lookups), it's signal to add a new skill or extend an existing one. The `aiplatform-cli` skill in particular is meant to grow new recipes and curl fallbacks as new failure modes appear — invoke `skill-builder` to extend it cleanly.

## ADK Development

### ADK MCP Server (installed globally)
The ADK MCP server provides deep ADK expertise via `search_code` and `read_docs` tools.
Skills available: `/adk-scaffold`, `/adk-cheatsheet`, `/adk-dev-guide`, `/adk-eval-guide`, `/adk-deploy-guide`

**Endpoint discovery:** Run `curl http://localhost:1956/openapi.json | jq '.paths | keys'` to see all routes. Load the `aitana-adk-testing` skill (`/aitana-adk-testing`) for curl recipes to inspect sessions, artifacts, and traces without staring at backend logs.

**APP_NAME constant:** The canonical app name used in all ADK calls is `APP_NAME = "aitana_platform"` (in `backend/adk/agui.py`). The dev UI's `/list-apps` returns this name. Never hardcode `"aitana_platform"` in tests or scripts — import `APP_NAME`.

### ADK Patterns (from reference scaffold)
- Agent definition: `google.adk.agents.Agent` with `google.adk.models.Gemini`
- App wrapper: `google.adk.apps.App`
- FastAPI integration: `google.adk.cli.fast_api.get_fast_api_app()`
- Testing: `google.adk.runners.Runner` with `InMemorySessionService`
- Evaluation: `adk eval` CLI with evalsets and rubric-based scoring

### ADK Reference Project

## Design Documents

- `docs/design/v5.0.0/migration-to-v6.md` — Full migration plan (v5 → v6 decisions, feature map, architecture)
- `docs/design/v6.0.0/` — v6.0.0 core bring-up sprint (see SEQUENCE.md for build order)
- `docs/design/v6.1.0/` — v6.1.0 channels, CLI, MCP apps
- `docs/design/v6.2.0/` — v6.2.0 DB tooling, v5 migration, agent CLI
- `docs/vendor/` — External documentation (ADK MCP guide, etc.)

## Testing

### Backend
```bash
cd backend
make test-fast         # Fast CI tests
make test              # All tests
make eval              # ADK evaluation
```

### Frontend
```bash
cd frontend
npm run test:run       # Vitest
npm run quality:check  # Full quality check
```

### Test Organization
- `backend/tests/unit/` — Unit tests for models, utils
- `backend/tests/integration/` — Integration tests (require GCP)
- `backend/tests/eval/` — ADK evaluation sets and config
- `frontend/src/**/__tests__/` — Component and hook tests

## Code Style

### Backend (Python)
- See `backend/CLAUDE.md` for Python-specific guidelines
- Use `ruff` for linting and formatting
- Type hints on all function signatures
- Async/await for all I/O operations

### Frontend (TypeScript)
- TypeScript strict mode
- React hooks for state/effects
- Radix UI + Tailwind for components
- Follow v5 patterns (copied from `src/contexts/`, `src/components/`)

## Automation Principle

Any local workflow that requires more than one manual step — setting env vars, running commands across directories, starting multiple processes — **must have a script or `make` target**. Never document a multi-step manual process without automating it.

| Task | Command |
|------|---------|
| Start local dev servers | `make dev` |
| Smoke-test proxy bridge | `make proxy-check` |
| Backend tests (fast) | `cd backend && make test-fast` |
| Frontend quality check (inner dev loop, no tests) | `cd frontend && npm run quality:check:fast` |
| **Frontend pre-push CI parity (tests + build)** | `cd frontend && npm run quality:check` |
| **Backend pre-push CI parity (lint + format + tests)** | `cd backend && make lint && make test-fast` |
| **ADK-contract conformance gate (before any `google-adk` bump)** | `make adk-conformance` |
| Install the `aiplatform` CLI globally | `make cli-install` |
| Verify the `aiplatform` CLI works end-to-end | `make cli-selftest` |

When adding a new workflow, add it to `scripts/` and the root `Makefile` in the same PR.

> **Pre-push gotcha:** `npm run quality:check:fast` runs lint + typecheck
> + auth-fetch but NOT tests. `make lint` runs ruff check + format-check
> but NOT pytest. If you've touched backend/frontend code and are about
> to push, use the **CI parity** rows above. The faster checks are for
> inner-loop iteration. The LOCAL-MODE-AND-FORK sprint shipped 9 dev
> commits before noticing CI was red because it relied on the fast
> variants.

## Git Policy

- Push with `sunholo-voight-kampff` account (now an `Aitana-Labs` org member)
- GitHub org: `Aitana-Labs` (transferred from `sunholo-data` on 2026-04-14)
- Repo: `sunholo-data/ai-protocol-platform`
- Never force-push to dev/test/prod
- Commit messages: conventional commits (`feat:`, `fix:`, `docs:`)

## Common Mistakes

### Frontend API Calls
Always use `/api/proxy` to reach the backend — frontend (port 3000) and backend (port 1956) are separate services.

### Wrong Python Environment
Always `cd backend && uv run ...` — never use global `python` or `pip`.

### Copying v5 Code Without Removing Sunholo
Every v5 file has Sunholo imports. Strip them when copying. Replace with direct Firestore/ADK calls.
