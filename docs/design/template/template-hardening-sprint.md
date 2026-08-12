# Sprint Plan: TEMPLATE-HARDENING — CPH Uni Upstream Fixes

## Summary

Implement all 30 upstream feedback items from the CPH Uni AIPLA fork against the Aitana
platform, then publish to `sunholo-data/ai-protocol-platform` via the template-publish
pipeline. Seven sequential milestones, each an independent PR. Two milestones
(M1 tool-opt-out) are primarily a port from existing AIPLA code; the rest are net-new.

**Duration:** 8.5 days  
**Scope:** Fullstack (backend-heavy)  
**Dependencies:** None — all milestones are independent  
**Risk Level:** Low–Medium (M7 MCP Apps has the most unknowns)  
**Design Docs:** [`docs/design/template/`](.)

---

## Current Status Analysis

### Recent Velocity (14 days)
- **96 commits** in 14 days (≈ 7/day)
- **35,338 lines inserted** across 258 files (≈ 2,500 LOC/day gross)
- **Recent milestone pattern:** Four AIPLA template-extension sprints (2.11–2.14) completed
  in a single day each. Workshop-helper Path B shipped same session.
- **Estimated net velocity (implementation + tests):** ~450 LOC/day conservative

### Existing Implementation Leverage
- Items #22 + #25 (M1) already coded in AIPLA fork — port, not write
- Item #16 (anonymous-group persistence) shipped in v6.2.0 2.11 — just needs template sync
- Item #23 (flex min-h-0) fixed in platform — just needs template sync
- All design docs already written (preceding session); specs are clear

### LOC Budget
| Milestone | Impl LOC | Test LOC | Total | Days |
|-----------|----------|----------|-------|------|
| M1 tool-opt-out | 150 | 150 | 300 | 0.5 |
| M2 auth-hardening | 200 | 220 | 420 | 1.0 |
| M3 cloudbuild-hardening | 280 | 80 | 360 | 1.0 |
| M4 fork-ergonomics | 380 | 200 | 580 | 2.0 |
| M5 session-management | 260 | 250 | 510 | 1.0 |
| M6 dx-hardening | 350 | 160 | 510 | 1.0 |
| M7 mcp-apps-artefacts | 480 | 300 | 780 | 2.0 |
| **Total** | **2,100** | **1,360** | **3,460** | **8.5** |

---

## Milestones

### M1 — Tool Opt-Out (Port from AIPLA)
**Scope:** Backend  
**Estimated:** 300 LOC | **Duration:** 0.5d  
**Source items:** #22 #25  
**Design doc:** [template-tool-opt-out.md](template-tool-opt-out.md)

**Goal:** Port `A2uiToolConfig.enabled` and `toolConfigs.defaults` from AIPLA to the
template. Already coded and tested — this is a cherry-pick + test verification.

**Tasks:**
- [ ] Port `A2uiToolConfig.enabled: bool = True` to `backend/adk/a2ui.py` (~20 LOC)
- [ ] Port factory gate `if a2ui_cfg.enabled: tools.append(make_a2ui_toolset(...))` to `backend/adk/agent.py` (~5 LOC)
- [ ] Port `DefaultToolsConfig(artifacts, memory)` model to `backend/db/models.py` (~25 LOC)
- [ ] Port factory gates for artifacts and memory tools to `backend/adk/agent.py` (~20 LOC)
- [ ] Port 7 test cases from AIPLA (`test_skill_config_a2ui_surface.py` ×5 + `test_create_agent.py` ×2 new) (~150 LOC tests)
- [ ] Add `toolConfigs.defaults` SKILL.md frontmatter example to `docs/` or skill template (~30 LOC docs)

**Files:**
- `backend/adk/a2ui.py` — add `enabled` field
- `backend/adk/agent.py` — two conditional blocks
- `backend/db/models.py` — `DefaultToolsConfig`, `ToolConfigs` update
- `backend/tests/unit/test_skill_config_a2ui_surface.py` — port 5 cases
- `backend/tests/unit/test_create_agent.py` — port 2 cases, verify 2 existing

**Acceptance Criteria:**
- [ ] Skill with `toolConfigs.a2ui.enabled: false` → `send_a2ui_json_to_client` absent from agent tool list
- [ ] Skill with `toolConfigs.defaults.artifacts: false` → `load_artifacts_tool` and `retrieve_artifact` absent
- [ ] Skill with `toolConfigs.defaults.memory: false` → `load_memory_tool` and `preload_memory_tool` absent
- [ ] No `toolConfigs` key → all tools present (backwards compat confirmed)
- [ ] `cd backend && make test-fast` green

**Risks:**
- AIPLA code may have diverged from template's model schema — verify field names match

---

### M2 — Auth Hardening
**Scope:** Backend + Frontend  
**Estimated:** 420 LOC | **Duration:** 1.0d  
**Source items:** #9 #19 #20 #21  
**Design doc:** [template-auth-hardening.md](template-auth-hardening.md)

**Goal:** Guard empty `user_email` in permissions, seed wildcard rule in prod, gate
Firestore listeners for anonymous users, document Firebase region gotcha.

**Tasks:**
- [ ] Guard `user_email` and `user_domain` lookups in `backend/auth/permissions.py` (~15 LOC)
- [ ] Add `_ensure_tool_permissions_wildcard()` to `backend/admin/platform_seed.py` (~40 LOC)
- [ ] Add `tool_permissions_wildcard_seeded: bool` to `SeedSummary` model (~10 LOC)
- [ ] Gate `onSnapshot` listeners in `useDocBrowser.ts` (×2) on `authMode === "firebase"` (~20 LOC)
- [ ] Gate `onSnapshot` listener in `useDocument.ts` (×1) on `authMode === "firebase"` (~10 LOC)
- [ ] Write `docs/ops/gotchas.md` with Firebase resource location entry (#9) (~40 LOC docs)
- [ ] Tests: permissions empty-email, wildcard idempotency, listener gate unit (~285 LOC tests)

**Files:**
- `backend/auth/permissions.py` — guard empty string
- `backend/admin/platform_seed.py` — `_ensure_tool_permissions_wildcard`
- `backend/db/models.py` — `SeedSummary` update
- `frontend/src/hooks/useDocBrowser.ts` — authMode gate
- `frontend/src/hooks/useDocument.ts` — authMode gate
- `backend/tests/unit/test_permissions.py` — empty email case
- `backend/tests/unit/test_platform_seed.py` — wildcard idempotency
- `frontend/src/__tests__/useDocBrowser.test.ts` — listener gate
- `docs/ops/gotchas.md` — Firebase region entry

**Acceptance Criteria:**
- [ ] `can_use_tool("", "web_search", ...)` returns wildcard permission (no 400)
- [ ] `platform_seed.seed()` on fresh Firestore → `tool_permissions/*` doc exists
- [ ] Repeat `seed()` → `tool_permissions_wildcard_seeded: False` (idempotent)
- [ ] `useDocBrowser` in anonymous-group session → zero `onSnapshot` subscriptions
- [ ] `cd backend && make test-fast` green; `npm run quality:check:fast` green

**Risks:**
- `authMode` context shape may differ between auth providers — verify hook reads it correctly

---

### M3 — Cloud Build Hardening
**Scope:** Infrastructure / Config + Backend  
**Estimated:** 360 LOC | **Duration:** 1.0d  
**Source items:** #5 #6 #7 #8 #13 #14  
**Design doc:** [template-cloudbuild-hardening.md](template-cloudbuild-hardening.md)

**Goal:** Make channel secrets optional, use project-local log bucket, write bootstrap
script for new GCP projects, fix identity-token pattern in seed step, add diagnostic log
on missing email claim.

**Tasks:**
- [ ] Refactor `cloudbuild.yaml` `--set-secrets` to conditional on `_ENABLE_*` substitutions (#5) (~60 LOC yaml)
- [ ] Add `_LOG_BUCKET` substitution defaulting to `gs://${PROJECT_ID}-cloudbuild-logs` (#6) (~10 LOC yaml)
- [ ] Write `scripts/bootstrap-gcp-project.sh` — CB SA materialization + `iam.serviceAccountUser` grant (#7) (~80 LOC bash)
- [ ] Replace `gcloud auth print-identity-token` with metadata-server endpoint; remove `set +e; exit 0` in seed step (#13) (~20 LOC yaml)
- [ ] Add diagnostic `logger.error(...)` when `email` claim absent in `backend/admin/auth.py` (#14) (~10 LOC)
- [ ] Add GitHub admin requirement entry to `docs/ops/gotchas.md` (#8) (~20 LOC docs)
- [ ] Add metadata-server token pattern to `docs/ops/platform-skills.md` (#14) (~20 LOC docs)
- [ ] Unit test for email-absent diagnostic path (~80 LOC)

**Files:**
- `cloudbuild.yaml` — `_ENABLE_*` gates + `_LOG_BUCKET` + metadata-server seed step
- `backend/cloudbuild.yaml` — same seed step fix
- `scripts/bootstrap-gcp-project.sh` — new file
- `backend/admin/auth.py` — diagnostic log
- `backend/tests/unit/test_admin_auth.py` — email-absent case
- `docs/ops/gotchas.md` — GitHub admin entry
- `docs/ops/platform-skills.md` — metadata-server token docs

**Acceptance Criteria:**
- [ ] `cloudbuild.yaml` with no channel `_ENABLE_*` flags set → deploy step runs without referencing missing secrets
- [ ] `backend/admin/auth.py` logs `"email claim absent"` when token has no email field
- [ ] Unit test for diagnostic path passes
- [ ] `scripts/bootstrap-gcp-project.sh` is executable and has `set -euo pipefail`
- [ ] `docs/ops/gotchas.md` has GitHub admin + Firebase region entries

**Risks:**
- Cloud Build YAML syntax for conditional secrets — test locally with `gcloud builds submit --config`
- Bootstrap script can't be unit-tested against real GCP; smoke test via manual invocation

---

### M4 — Fork Ergonomics
**Scope:** Backend + Frontend + CLI  
**Estimated:** 580 LOC | **Duration:** 2.0d  
**Source items:** #1 #2 #3 #4 #11 #12  
**Design doc:** [template-fork-ergonomics.md](template-fork-ergonomics.md)

**Goal:** Replace the hardcoded DISPLAY_NAMES/TAGS dict with frontmatter reads, remove
the GCP project pin, add fail-loud startup validation for `PLATFORM_OWNER_EMAIL`, move
CLI URLs to config.yaml, add `branding.ts` constants, set MCP sandbox URL default to empty.

**Tasks:**
- [ ] Replace DISPLAY_NAMES/TAGS/INITIAL_MESSAGES dicts with `_read_skill_md_meta()` in `backend/scripts/seed_skills.py` (#1) (~60 LOC)
- [ ] Replace `pin_project_for_env("dev")` with `PLATFORM_SEED_PROJECT` env-var in seeder (#2) (~30 LOC)
- [ ] Add startup validation for `PLATFORM_OWNER_EMAIL` in `backend/admin/platform_seed.py` (#3) (~20 LOC)
- [ ] Move `_DEFAULT_URLS` to `cli/config.yaml`; remove brand docstring (#4) (~40 LOC)
- [ ] Create `frontend/src/lib/branding.ts` with `CITATION_SCHEME` + `TRANSPORT_FIELD` (#11) (~20 LOC)
- [ ] Wire `CITATION_SCHEME` into `InlineCitation.tsx`; wire `TRANSPORT_FIELD` into passive MCP page (#11) (~30 LOC)
- [ ] Fix remaining "Aitana" literals in dev pages and doc comments (#11) (~20 LOC)
- [ ] Set `_MCP_SANDBOX_URL: ''` in `cloudbuild.yaml`; add `MCP_APPS_ENABLED` guard in backend (#12) (~30 LOC)
- [ ] Add graceful `MCPAppToolCallRouter` stub when `MCP_SANDBOX_URL` is blank (#12) (~20 LOC)
- [ ] Update `.env.example` with `PLATFORM_SEED_PROJECT`, `PLATFORM_OWNER_EMAIL` (#2/#3) (~15 LOC)
- [ ] Tests: seeder frontmatter, startup validation, branding constants, MCP disabled stub (~295 LOC tests)

**Files:**
- `backend/scripts/seed_skills.py` — frontmatter read + env-var project
- `backend/admin/platform_seed.py` — startup validation
- `cli/config.yaml` — new file; `cli/aiplatform/http.py` — read from it
- `frontend/src/lib/branding.ts` — new file
- `frontend/src/components/chat/InlineCitation.tsx` — use `CITATION_SCHEME`
- `frontend/src/app/dev/mcp-apps/passive/page.tsx` — use `TRANSPORT_FIELD`
- `frontend/src/app/dev/*/page.tsx` — de-brand remaining literals
- `cloudbuild.yaml` — `_MCP_SANDBOX_URL` default
- `backend/config.py` — `MCP_APPS_ENABLED`
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — disabled stub
- `.env.example` — new env vars

**Acceptance Criteria:**
- [ ] `seed_skills.py` with a skill outside the original 5 → correct displayName, tags, initialMessage from frontmatter
- [ ] `PLATFORM_OWNER_EMAIL` unset in non-LOCAL_MODE → `RuntimeError` on startup with clear message
- [ ] `PLATFORM_SEED_PROJECT=my-project` → seeder targets that project
- [ ] `_MCP_SANDBOX_URL` blank → `MCPAppToolCallRouter` renders disabled stub (no iframe)
- [ ] `InlineCitation` uses `CITATION_SCHEME` from `branding.ts`; no `aitana://` literal in source
- [ ] All tests pass; no new "aitana" literals in frontend source (except legacy comments)

**Risks:**
- SKILL.md frontmatter parsing must handle missing fields gracefully (not all skills have all three fields)
- `cli/config.yaml` integration needs to be tested against the actual CLI startup path

---

### M5 — Session Management
**Scope:** Backend + Frontend  
**Estimated:** 510 LOC | **Duration:** 1.0d  
**Source items:** #26 #27  
**Design doc:** [template-session-management.md](template-session-management.md)

**Goal:** Fix wrong `app_name` in state GET; add session bootstrap endpoint; call it
fire-and-forget from frontend; add `aiplatform sessions bootstrap` CLI subcommand.

**Tasks:**
- [ ] Fix `app_name=idx.skill_id` → `APP_NAME` in `backend/protocols/sessions_route.py` line 299 (#26) (~3 LOC)
- [ ] Write non-mock test for state GET using real `InMemorySessionService` (#26) (~80 LOC test)
- [ ] Write `backend/protocols/session_bootstrap_routes.py` — `POST /api/sessions/{id}/bootstrap` (#27) (~120 LOC)
- [ ] Wire bootstrap route into FastAPI app (#27) (~5 LOC)
- [ ] Add fire-and-forget bootstrap call to `frontend/src/hooks/useSkillAgent.ts` (#27) (~20 LOC)
- [ ] Write `backend/tests/api_tests/test_session_bootstrap.py` — idempotency + index + session (~150 LOC)
- [ ] Add `aiplatform sessions bootstrap` CLI subcommand (~80 LOC)
- [ ] Frontend test for bootstrap call in `useSkillAgent` (~50 LOC)

**Files:**
- `backend/protocols/sessions_route.py` — one-line fix
- `backend/protocols/session_bootstrap_routes.py` — new file
- `backend/fast_api_app.py` — include bootstrap router
- `frontend/src/hooks/useSkillAgent.ts` — fire-and-forget bootstrap
- `backend/tests/api_tests/test_session_state.py` — non-mock test
- `backend/tests/api_tests/test_session_bootstrap.py` — new file
- `cli/aiplatform/commands/sessions.py` — `bootstrap` subcommand
- `frontend/src/__tests__/useSkillAgent.test.ts` — bootstrap call assertion

**Acceptance Criteria:**
- [ ] `GET /api/sessions/{id}/state` returns correct MCP context (not `{}`) when session was bootstrapped
- [ ] `POST /api/sessions/{id}/bootstrap` → 204; subsequent iframe-context POST → 200 (not 404)
- [ ] Repeated bootstrap calls → 204 each time (idempotent, no Firestore duplicate)
- [ ] `aiplatform sessions bootstrap <id> --skill <name>` → exits 0 against local backend
- [ ] Real `InMemorySessionService` test for state GET; no MagicMock that ignores `app_name`
- [ ] `cd backend && make test-fast` green

**Risks:**
- Bootstrap route must handle `SessionAlreadyExistsError` from ADK service — verify exception type name
- Frontend bootstrap must use `fetchWithAuth`, not bare `fetch()` (per platform convention)

---

### M6 — Developer Experience Hardening
**Scope:** Backend + Frontend + Docs + Config  
**Estimated:** 510 LOC | **Duration:** 1.0d  
**Source items:** #10 #15 #17 #18 #24  
**Design doc:** [template-dx-hardening.md](template-dx-hardening.md)

**Goal:** Fix anchored test matchers, add API discoverability section to README, fix
`/list-apps`, remove dead GCS config mount, document Dockerfile ARG requirement, ship
`agent-protocols` skill with vendored specs.

**Tasks:**
- [ ] Fix `/^join$/i` → `/join/i` + audit other anchored matchers in `page.test.tsx` (#10) (~10 LOC)
- [ ] Add "Where does the API live?" section to README.md (#15) (~30 LOC docs)
- [ ] Add endpoint discovery note to CLAUDE.md ADK section + audit skill references (#15) (~20 LOC docs)
- [ ] Fix `/list-apps` to return `[APP_NAME]` not filesystem paths (#15) (~15 LOC)
- [ ] Remove `/gcs_config` volume mount from `backend/Dockerfile`, `cloudbuild.yaml`, `backend/cloudbuild.yaml` (#17) (~-40 LOC)
- [ ] Add Dockerfile ARG declaration comment + `NEXT_PUBLIC_AUTH_MODE` ARG pair (#18) (~15 LOC)
- [ ] Add `docs/ops/secrets.md` with three-step `NEXT_PUBLIC_*` process (#18) (~40 LOC docs)
- [ ] Port / create `agent-protocols` project skill with 7 vendored spec files (#24) (~250 LOC skill files)
- [ ] Add `agent-protocols` entry to CLAUDE.md Project Skills section (#24) (~10 LOC)
- [ ] Test for `/list-apps` returning `APP_NAME` (~30 LOC)
- [ ] Verify flexible matcher test passes after button-text change (~40 LOC test update)

**Files:**
- `frontend/src/app/group/__tests__/page.test.tsx` — flexible matchers
- `README.md` — API discoverability section
- `CLAUDE.md` — ADK section + skill inventory audit
- `backend/protocols/list_apps_route.py` (or equivalent) — return `[APP_NAME]`
- `backend/Dockerfile` — remove `ENV _CONFIG_FOLDER=/gcs_config`
- `cloudbuild.yaml`, `backend/cloudbuild.yaml` — remove volume mount lines
- `frontend/Dockerfile` — ARG comment + `NEXT_PUBLIC_AUTH_MODE`
- `docs/ops/secrets.md` — new or extended
- `.claude/skills/agent-protocols/` — new directory + SKILL.md + 7 reference files + refresh script

**Acceptance Criteria:**
- [ ] `page.test.tsx` passes with button label changed to "Tilslut / Join"
- [ ] `GET /list-apps` → `{"apps": ["aitana_platform"]}` (not filesystem paths)
- [ ] `backend/Dockerfile` has no reference to `/gcs_config` or `_CONFIG_FOLDER`
- [ ] `docker build --build-arg NEXT_PUBLIC_AUTH_MODE=test` → `process.env.NEXT_PUBLIC_AUTH_MODE === "test"` in Next.js
- [ ] `agent-protocols` skill loads in a session; spec file references resolve
- [ ] `npm run quality:check:fast` green

**Risks:**
- Agent-protocols spec files must be fetched; confirm external URLs are reachable at implementation time
- GCS mount removal: verify no `cloudbuild.yaml` step conditionally reads the mount before removing

---

### M7 — MCP Apps / Iframe Artefact Architecture
**Scope:** Backend + Frontend + Docs  
**Estimated:** 780 LOC | **Duration:** 2.0d  
**Source items:** #28 #29 #30  
**Design doc:** [template-mcp-apps-artefacts.md](template-mcp-apps-artefacts.md)

**Goal:** Ship `useSandboxedIframeMessages` hook, update `_BLOCK_TEMPLATE` with positive
framing, document sandbox-proxy architecture and opaque-origin gotcha, update ADR-013,
create v1 StaticArtefactFrame placeholder doc.

**Tasks:**
- [ ] Port `useSandboxedIframeMessages` hook from AIPLA to `frontend/src/hooks/` (#28) (~70 LOC)
- [ ] Write hook tests (window-identity auth, source filter, cleanup) (#28) (~120 LOC tests)
- [ ] Update ADR-013 with opaque-origin `allow-same-origin` sub-bullet (#28) (~15 LOC docs)
- [ ] Write `docs/ops/mcp-apps-iframe-guide.md` — 5 sections (#30) (~200 LOC docs)
- [ ] Update `_BLOCK_TEMPLATE` in `backend/adk/iframe_context.py` with positive usage instructions (#29) (~20 LOC)
- [ ] Write `backend/tests/unit/test_iframe_context.py` — assert positive guidance present (#29) (~40 LOC)
- [ ] Update `backend/adk/iframe_context.py` docstring / module docs (#29) (~15 LOC)
- [ ] Create placeholder `docs/design/template/template-mcp-apps-static-artefact-v1.md` (#30) (~60 LOC docs)
- [ ] Update `docs/ops/mcp-apps-iframe-guide.md` InstructionProvider framing section (#29) (~30 LOC docs)
- [ ] Verify `MCPAppToolCallRouter` docs mention AppRenderer-as-sandbox-proxy (#30) (~15 LOC comment)
- [ ] Additional tests: `_BLOCK_TEMPLATE` security note still present; hook cleanup on unmount (~180 LOC tests)

**Files:**
- `frontend/src/hooks/useSandboxedIframeMessages.ts` — new file
- `frontend/src/__tests__/useSandboxedIframeMessages.test.ts` — new file
- `docs/ops/mcp-apps-iframe-guide.md` — new file
- `backend/adk/iframe_context.py` — `_BLOCK_TEMPLATE` update
- `backend/tests/unit/test_iframe_context.py` — new or extend
- `docs/design/` ADR-013 (wherever it lives) — opaque-origin sub-bullet
- `docs/design/template/template-mcp-apps-static-artefact-v1.md` — new placeholder
- `frontend/src/components/protocols/MCPAppToolCallRouter.tsx` — sandbox-proxy comment

**Acceptance Criteria:**
- [ ] `useSandboxedIframeMessages` filters by `event.source === iframeRef.current.contentWindow`
- [ ] Message from wrong window → `onMessage` not called (test passes)
- [ ] `wrap_with_iframe_context(state)` output contains `"You SHOULD reference these values"` AND `"Security note"`
- [ ] ADR-013 has `allow-same-origin` / window-identity sub-bullet
- [ ] `docs/ops/mcp-apps-iframe-guide.md` exists with all five sections
- [ ] `npm run quality:check:fast` green; `cd backend && make test-fast` green

**Risks:**
- AIPLA's `useSandboxedIframeMessages` may reference AIPLA-specific types — scrub before porting
- ADR docs location: find the actual ADR-013 file path before editing

---

## Day-by-Day Breakdown

### Day 1 (M1 + start M2) — 0.5d tool-opt-out + 0.5d auth-hardening start
- **Morning:** Port A2UI `enabled` + `defaults` flags from AIPLA; run tests
- **Afternoon:** Start M2 — guard `user_email` in permissions.py; add wildcard seed
- **Checkpoint:** M1 green (`make test-fast`); M2 permissions + seed tests written and passing

### Day 2 (M2 complete + M3) — finish auth-hardening + all cloudbuild-hardening
- **Morning:** Gate Firestore listeners in frontend; write `docs/ops/gotchas.md` Firebase entry
- **Afternoon:** All of M3 — conditional secrets, log bucket, bootstrap script, metadata token, diagnostic log
- **Checkpoint:** M2 fully green; M3 committed; bootstrap script executable

### Day 3 (M4 start) — fork-ergonomics day 1
- **Focus:** Backend half — seeder frontmatter read, `PLATFORM_SEED_PROJECT`, `PLATFORM_OWNER_EMAIL` validation
- **Checkpoint:** `seed_skills.py` test with sixth skill passing; startup validation test passing

### Day 4 (M4 complete) — fork-ergonomics day 2
- **Focus:** Frontend + CLI half — `branding.ts`, `InlineCitation`, passive MCP page, `cli/config.yaml`, MCP sandbox disabled stub
- **Checkpoint:** Full M4 test suite green; no `aitana://` literal in frontend source

### Day 5 (M5) — session-management
- **Morning:** One-line `app_name` fix + non-mock test; write bootstrap route
- **Afternoon:** Frontend fire-and-forget call; CLI `sessions bootstrap` subcommand
- **Checkpoint:** Bootstrap e2e test: iframe-context POST → 200 after bootstrap

### Day 6 (M6) — DX hardening
- **Morning:** Fix matchers, `/list-apps`, README/CLAUDE.md docs, remove GCS mount
- **Afternoon:** Dockerfile ARG docs; fetch and vendor protocol specs for `agent-protocols` skill
- **Checkpoint:** `agent-protocols` skill loads in session; GCS mount absent from all Dockerfiles

### Day 7 (M7 start) — MCP Apps day 1
- **Focus:** `useSandboxedIframeMessages` hook + tests; ADR-013 update; `_BLOCK_TEMPLATE` positive framing
- **Checkpoint:** Hook tests green (window-identity, source filter, cleanup); block template tests green

### Day 8 (M7 complete) — MCP Apps day 2
- **Focus:** `docs/ops/mcp-apps-iframe-guide.md` (5 sections); v1 placeholder doc; remaining tests
- **Checkpoint:** All M7 acceptance criteria met; full test suite green

### Day 8.5 — Template publish + wrap-up
- **Focus:** Run `aitana-template-publish` pipeline for all 7 PRs; verify public template
- **Checkpoint:** Public template updated; smoke test fresh fork against template

---

## Quality Gates

After each milestone:
```bash
cd backend && make test-fast && make lint
npm run quality:check:fast
```

After M4 (frontend-heavy):
```bash
npm run quality:check   # full: lint + typecheck + tests
```

After M7 (final):
```bash
cd backend && make test && make lint
npm run quality:check
```

Before template publish (each PR):
```bash
./scripts/smoke-deployed.sh dev all
```

---

## Template Publish Order

Each milestone is an independent PR. Publish in the same priority order after all tests
pass. Use the `aitana-template-publish` skill for each publish run.

| # | Milestone | Template PR |
|---|-----------|-------------|
| 1 | M1 tool-opt-out | PR #1 — `feat: A2UI opt-out + default tool opt-out flags` |
| 2 | M2 auth-hardening | PR #2 — `fix: auth permissions + wildcard seed + listener gate` |
| 3 | M3 cloudbuild-hardening | PR #3 — `fix: optional secrets + project-local bucket + bootstrap script` |
| 4 | M4 fork-ergonomics | PR #4 — `fix: seeder frontmatter + PLATFORM_OWNER_EMAIL validation + branding.ts` |
| 5 | M5 session-management | PR #5 — `fix: session APP_NAME + bootstrap endpoint` |
| 6 | M6 dx-hardening | PR #6 — `docs+fix: API discoverability + GCS dead plumbing + agent-protocols skill` |
| 7 | M7 mcp-apps-artefacts | PR #7 — `feat: useSandboxedIframeMessages + iframe-context framing + docs` |
