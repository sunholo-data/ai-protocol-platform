# Sprint Plan: LOCAL-MODE-AND-FORK — Workshop Readiness + Public Template Prep

## Summary

Land `LOCAL_MODE=1` end-to-end (backend in-memory Firestore + auth stub, frontend banner + stub auth provider, workshop fixture, shared-dev-Firestore tier) so a fresh laptop can `make dev` and chat without any GCP setup — AND complete the pre-fork checklist (Aitana-specific defaults moved to env vars, branding strings extracted, Apache 2.0 LICENSE, CONTRIBUTING.md, hardcoded-path scrub, sanitization script) so `sunholo-data/ai-protocol-platform` can be forked into a public template repo with a single scripted pass and zero new code work at fork time.

**Duration:** ~5 days (M1 1.5d + M2 1d + M3 0.5d + M4 0.5d + M5 1.5d)
**Scope:** Fullstack + Repo hygiene
**Dependencies:** None — all v6.1.0 pre-reqs (1.0–1.3, 1.5, 1.7, 1.8–1.11, 1.13–1.17, 1.20–1.25) shipped
**Risk Level:** Medium — M1 has the largest surface (InMemoryFirestoreClient parity); M5 has many tiny edits across infra/scripts that need verification
**Design Docs:**
- [docs/design/v6.1.0/local-mode-and-workshop-readiness.md](local-mode-and-workshop-readiness.md) (1.18)
- [docs/design/v6.0.0/template-split-strategy.md](../v6.0.0/template-split-strategy.md)

**Push policy:** Commit at every milestone checkpoint to local `dev` branch; **do not `git push` until user signs off**. Review the diff between milestones; push as a batch when complete.

## Current Status Analysis

### Recent Velocity (last 10 days)
- 25+ commits, including MCP-Apps M3 (5656 LOC), MCP-Apps M4 (Cloud Run packaging), MCP-Apps Runtime-Fixes (F1/F2a v1/v2/v3 + F3)
- Sustained ~400–700 LOC/day with tests; large feature days reached 1500+ LOC
- Sprint estimate of ~5 days at ~280 LOC/day average is conservative against this baseline

### Existing Implementation We Build On
- ADK sessions/artifacts already fall back to in-memory when env vars unset ([backend/adk/session.py:88-132](../../../backend/adk/session.py))
- Frontend `firebase.ts` returns `null` cleanly when `NEXT_PUBLIC_FIREBASE_*` unset ([frontend/src/lib/firebase.ts:31](../../../frontend/src/lib/firebase.ts#L31))
- `fetchWithAuth` already centralised — single touch point for stub-token branch
- 380+ vitest passing, 600+ backend pytest passing — strong baseline

### What Doesn't Fall Back Today
- `firestore.Client()` called unconditionally → backend boots but every request 500s
- Firebase Admin SDK requires `GOOGLE_APPLICATION_CREDENTIALS` → auth routes 401
- Frontend "not configured" state has no path to a signed-in identity → chat unsendable
- Vertex AI Search / Cloud Trace / Mailgun init log warnings or fail at import

## Proposed Milestones

### Milestone 1 — Backend LOCAL_MODE
**Scope:** backend
**Goal:** Single source of truth for the flag + InMemoryFirestoreClient + auth stub + paired-config safety asserts.
**Estimated:** ~500 LOC impl + ~150 LOC tests = ~650 LOC
**Duration:** 1.5 days

**Tasks:**
- [ ] `backend/config.py` — `is_local_mode()`, `assert_safe_local_mode()` helpers (~30 LOC)
- [ ] `backend/db/firestore_inmemory.py` — `InMemoryFirestoreClient` implementing the 12 methods v6 uses: `collection().document().get/set/update/delete()`, `collection().where().order_by().limit().stream()`, `Increment`, `SERVER_TIMESTAMP`. Thread-safe nested dict. Optional `~/.aitana-local/firestore.json` persistence behind `LOCAL_MODE_PERSIST=1` (~250 LOC + 100 LOC parity tests)
- [ ] `backend/db/firestore.py:get_client()` — branch on `is_local_mode()`, cache singleton same way (~5 LOC change)
- [ ] `backend/db/local_fixture.py` — seed 1 demo skill (`Demo Researcher`, public) + 1 demo doc + 1 workshop user. Idempotent (only seeds empty collections) (~80 LOC)
- [ ] `backend/auth/local_mode_stub.py` — stub auth dep returning `User(uid="workshop-user", email="workshop@local")` when token == `local-mode-stub-token`; rejects any other token (~40 LOC + 50 LOC tests)
- [ ] `backend/auth/firebase.py` — auth dep factory consults `is_local_mode()` and swaps in stub (~10 LOC change)
- [ ] `backend/fast_api_app.py` — startup banner + **refuse-to-start assert**: `LOCAL_MODE` with any of `K_SERVICE` / `GAE_ENV` / `KUBERNETES_SERVICE_HOST` → fast fail with explicit error. Sessions/artifacts pairing warning when only one of `AGENT_ENGINE_ID`/`ADK_ARTIFACT_BUCKET` set (~30 LOC)
- [ ] `GET /api/local-mode-status` endpoint — returns `{local_mode: bool, missing_services: [...]}`. No auth. (~30 LOC + test)
- [ ] Wrap Vertex AI Search / Cloud Trace / Mailgun init in `if not is_local_mode():` guards. Tool-level: search tool in LOCAL_MODE returns `"search disabled in LOCAL_MODE — set VERTEX_AI_SEARCH_DATASTORE_ID to enable"` (~30 LOC across 3 files)

**Files to Create/Modify:**
- `backend/config.py` (new, ~30 LOC)
- `backend/db/firestore_inmemory.py` (new, ~250 LOC)
- `backend/db/local_fixture.py` (new, ~80 LOC)
- `backend/auth/local_mode_stub.py` (new, ~40 LOC)
- `backend/db/firestore.py` (modify, ~5 LOC)
- `backend/auth/firebase.py` (modify, ~10 LOC)
- `backend/fast_api_app.py` (modify, ~30 LOC)
- `backend/protocols/local_mode_routes.py` (new, ~30 LOC) — for `/api/local-mode-status`
- `backend/tests/db/test_firestore_inmemory.py` (new, ~100 LOC)
- `backend/tests/auth/test_local_mode_stub.py` (new, ~50 LOC)

**Acceptance Criteria:**
- [ ] `LOCAL_MODE=1 make dev` boots with **no** GCP env vars set; `curl http://localhost:1956/api/local-mode-status` returns `{"local_mode": true, ...}`
- [ ] `pytest backend/tests/` passes with `GOOGLE_CLOUD_PROJECT` unset and no ADC
- [ ] `LOCAL_MODE=1 K_SERVICE=foo` → boot fails with explicit error message
- [ ] `LOCAL_MODE=1` + token != `local-mode-stub-token` → 401
- [ ] InMemoryFirestoreClient parity: CRUD, where + order_by + limit, Increment, SERVER_TIMESTAMP, batch ops
- [ ] Setting only one of `AGENT_ENGINE_ID`/`ADK_ARTIFACT_BUCKET` emits a pairing WARNING on boot

**Risks:**
- InMemoryFirestoreClient may miss a v6-used query pattern — Mitigation: ripgrep `firestore.Client` usage across `backend/` first, build the API surface from real call sites, not from Firestore docs
- Tests must run with no GCP creds — Mitigation: add CI matrix row that unsets `GOOGLE_CLOUD_PROJECT` + `GOOGLE_APPLICATION_CREDENTIALS` for backend pytest

---

### Milestone 2 — Frontend LOCAL_MODE + Branding Extraction
**Scope:** frontend
**Goal:** Banner, stub auth provider, AND extract Aitana branding to `branding.ts` so a fork swaps one file (folds pre-fork checklist).
**Estimated:** ~200 LOC impl + ~80 LOC tests = ~280 LOC
**Duration:** 1 day

**Tasks:**
- [ ] `src/lib/branding.ts` — single export for app name (`"Aitana"`), contact email, tagline, marketing copy currently inlined. Defaults stay Aitana — downstream fork rewrites only this file (~40 LOC)
- [ ] `src/components/LocalModeBanner.tsx` — soft-yellow strip, "🛠️ LOCAL_MODE — All data is in-memory and ephemeral. Auth is stubbed. [Connect to your own GCP →] [What's disabled?]". Reads `/api/local-mode-status` for the disabled-services list. Per-session dismissible. (~80 LOC + ~30 LOC Vitest)
- [ ] `src/providers/LocalAuthProvider.tsx` — exposes `uid: workshop-user`, `email: workshop@local`, `displayName: Workshop Attendee`; `getIdToken()` returns `local-mode-stub-token` (~60 LOC + ~30 LOC Vitest)
- [ ] `src/lib/firebase.ts` — top-level `isLocalMode()` early-return; no SDK init, no listeners (~10 LOC change)
- [ ] `src/lib/fetchWithAuth.ts` — LOCAL_MODE branch sends stub token (~5 LOC change)
- [ ] `src/app/layout.tsx` — mount `<LocalModeBanner />` + swap `FirebaseAuthProvider` ↔ `LocalAuthProvider` conditionally (~10 LOC change)
- [ ] Update `src/app/page.tsx` to read app name + tagline from `branding.ts`

**Files to Create/Modify:**
- `frontend/src/lib/branding.ts` (new, ~40 LOC)
- `frontend/src/components/LocalModeBanner.tsx` (new, ~80 LOC)
- `frontend/src/providers/LocalAuthProvider.tsx` (new, ~60 LOC)
- `frontend/src/lib/firebase.ts` (modify, ~10 LOC)
- `frontend/src/lib/fetchWithAuth.ts` (modify, ~5 LOC)
- `frontend/src/app/layout.tsx` (modify, ~10 LOC)
- `frontend/src/app/page.tsx` (modify, ~5 LOC)
- `frontend/src/components/__tests__/LocalModeBanner.test.tsx` (new, ~30 LOC)
- `frontend/src/providers/__tests__/LocalAuthProvider.test.tsx` (new, ~30 LOC)

**Acceptance Criteria:**
- [ ] `NEXT_PUBLIC_LOCAL_MODE=1 npm run dev` boots without `NEXT_PUBLIC_FIREBASE_*`; chat round-trips against M1 backend
- [ ] chrome-devtools MCP smoke: banner visible at top, "Connect to GCP →" link routes to `/workshop`, no Firebase SDK init in network panel (no requests to `identitytoolkit.googleapis.com`)
- [ ] `branding.ts` is the **only** file a fork edits to rebrand (verified by grep)
- [ ] Vitest green; `npm run quality:check:fast` clean
- [ ] `NEXT_PUBLIC_LOCAL_MODE` unset → existing Firebase flow unchanged (regression check)

**Risks:**
- React provider tree swap could break existing auth-aware components — Mitigation: ensure both providers expose identical context shape; lint with TS interface
- Banner styling clashes with existing top-of-page elements — Mitigation: mount above main, fixed 32px height; preview in browser before merge

---

### Milestone 3 — Workshop fixture + WORKSHOP.md + .env.example
**Scope:** fullstack (mostly docs/content)
**Goal:** Demo skills covering W2/W6, public quick-start doc, env-example files, `/workshop` Next.js route. Folds pre-fork `.env.example` checklist item.
**Estimated:** ~150 LOC code + ~250 lines of doc
**Duration:** 0.5 day

**Tasks:**
- [ ] Expand `backend/db/local_fixture.py` with 2 more demo skills: one ADK-vanilla (W2 demo), one A2UI-emitting (W6 demo). MCP-App W7 demo already lives in dev Firestore (~60 LOC)
- [ ] Author top-level `WORKSHOP.md` (~250 lines):
  - Clone → `make dev` → first message walkthrough
  - "Graduating from LOCAL_MODE" 7 ordered steps from design doc § 151–174
  - Mode-tier matrix (LOCAL_MODE / Shared dev / Own GCP)
  - `your-project-id` shared-tier env snippet (lands at M4)
- [ ] `.env.example` files (root + `frontend/` + `backend/`) documenting every variable consumed (no values)
- [ ] Root `README.md` — link to `WORKSHOP.md` at top
- [ ] `frontend/src/app/workshop/page.tsx` — render `WORKSHOP.md` via MDX or next-mdx-remote; banner deep-link `/workshop#graduating` resolves (~40 LOC)

**Files to Create/Modify:**
- `backend/db/local_fixture.py` (modify, ~60 LOC)
- `WORKSHOP.md` (new, ~250 lines)
- `.env.example`, `frontend/.env.example`, `backend/.env.example` (new, ~30 lines each)
- `README.md` (modify, ~3 LOC)
- `frontend/src/app/workshop/page.tsx` (new, ~40 LOC)

**Acceptance Criteria:**
- [ ] Cold checkout + `make dev` (no .env) + WORKSHOP.md → first chat message in **<30 min** on a separate machine / VM (timed dry-run logged)
- [ ] `grep -i "TODO\|FIXME" WORKSHOP.md` returns zero hits
- [ ] Banner's "Connect to your own GCP →" link resolves to a working anchor on `/workshop`
- [ ] All 3 demo skills respond to a test prompt

**Risks:**
- 30-min target may slip if dependency install (uv + npm) takes long on fresh machine — Mitigation: pre-cache instructions in WORKSHOP.md; measure on M3 candidate machine
- MDX rendering of WORKSHOP.md introduces a build-time dep — Mitigation: prefer existing dep if present (`react-markdown`); else accept the one-line `npm install`

---

### Milestone 4 — Shared dev Firestore mode
**Scope:** fullstack + GCP infra
**Goal:** Optional middle tier for attendees who skip GCP setup but want real Firestore semantics. Touches `your-project-id` Firebase project.
**Estimated:** ~50 LOC rules + GCP config + WORKSHOP.md section
**Duration:** 0.5 day

**Tasks:**
- [ ] Enable Anonymous Auth on `your-project-id` Firebase project (gcloud or console)
- [ ] Update `firestore.rules` — anon writes scoped to `chat_sessions/{uid}/**` + `users/{uid}/**`; deny cross-user reads; public skills (`Demo Researcher`) remain read-only-shared (~30 LOC rules)
- [ ] Firestore rules unit test (firebase emulator) — two anon UIDs, verify isolation (~50 LOC)
- [ ] Add $50 GCP budget alert on `your-project-id`
- [ ] Document in `WORKSHOP.md` (extends M3 doc) the public web-API-key + project-id snippet — note: public web keys are not secrets per [Firebase docs](https://firebase.google.com/docs/projects/api-keys)
- [ ] Smoke from clean machine: set only `NEXT_PUBLIC_FIREBASE_*` (no LOCAL_MODE), `make dev`, send message, verify lands at `chat_sessions/{anon-uid}/...` in Firestore

**Files to Create/Modify:**
- `firestore.rules` (modify, ~30 LOC)
- `firestore.rules.test.js` (new or extend, ~50 LOC)
- `WORKSHOP.md` (modify — Shared dev tier section, ~30 lines)
- GCP-side: anon-auth toggle, budget alert (no code changes)

**Acceptance Criteria:**
- [ ] Two attendees on shared tier can't read each other's sessions (rules emulator test green)
- [ ] Budget alert visible in GCP console
- [ ] Smoke from no-gcloud-CLI machine: clone + `NEXT_PUBLIC_FIREBASE_*` env + `make dev` → chat lands in Firestore at expected path

**Risks:**
- Anon auth + rules change touches the shared dev env — Mitigation: land outside business hours; test rules in firebase emulator BEFORE deploying; have rollback rules ready
- Existing dev users might tip into the anon tier accidentally — Mitigation: dev project has `aitana-admin` group membership; the rules layer reads custom claims for Aitana users separately

---

### Milestone 5 — Repo scrub + LICENSE + CONTRIBUTING + env-driven defaults
**Scope:** fullstack + repo hygiene
**Goal:** Combine design doc's M5 with remaining template-split-strategy.md pre-fork checklist items so the public fork is "run sanitize-for-template.sh, push" — no code work at fork time.
**Estimated:** ~250 LOC across ~15 files + 1 new script
**Duration:** 1.5 days

**Tasks (Pre-fork items from template-split-strategy.md lines 184–189):**
- [ ] **`aitana-admin` group + `aitana.ai` domain → env vars** in `backend/auth/permissions.py`. Defaults preserved (so existing dev keeps working). Env names: `PLATFORM_ADMIN_GROUP_TAG`, `PLATFORM_OWNER_EMAIL_DOMAIN` (~15 LOC change)
- [ ] **LICENSE** — Apache 2.0 (~12 KB standard text)
- [ ] **CONTRIBUTING.md** — workshop-focused stub: how to file issues, how to PR a new skill, code-of-conduct link (~80 lines)

**Tasks (Design doc M5 — hardcoded refs):**
- [ ] Replace hardcoded `your-project-id` in: `backend/app.py`, `backend/fast_api_app.py`, `backend/Makefile`, `backend/scripts/*.py`, `scripts/dev.sh`, `cloudbuild.yaml`, `firestore.indexes.json` → `GOOGLE_CLOUD_PROJECT` env-driven, with `your-project-id` as the dev default (~30 LOC across ~10 files)
- [ ] Scrub `<local-path>` hardcoded paths in `.claude/hooks/lint_on_edit.sh`, `.claude/skills/platform-deploy/scripts/audit-drift.sh` — env-templated via `${PLATFORM_ROOT:-$(git rev-parse --show-toplevel)}` (~10 LOC)
- [ ] Update root `CLAUDE.md` — remove the AgentsCLI references at line ~140 (keep private-only mention in a separate `CLAUDE.local.md` if needed for dev guidance)

**Tasks (Fork tooling):**
- [ ] `scripts/sanitize-for-template.sh` (~80 LOC) — runs **at fork time** to produce the public tree. Deletes: `internal preview bundle/`, NDA PDFs in `docs/`, `docs/feedback/internal feedback notes.md`, `docs/design/v6.0.0/implemented/*-sprint.md`, `.claude/state/sprints/`. **Stays in main repo (private)**; not run during this sprint — committed for the future fork operation. Dry-run mode produces a diff summary, not a destructive delete
- [ ] `scripts/check_local_mode_safety.py` (~40 LOC) — CI lint: fails if any `cloudbuild.yaml` / terraform sets `LOCAL_MODE=1`. Add to `.github/workflows/ci.yml`

**Files to Create/Modify:**
- `backend/auth/permissions.py` (modify, ~15 LOC)
- `LICENSE` (new, Apache 2.0)
- `CONTRIBUTING.md` (new, ~80 lines)
- `backend/app.py`, `backend/fast_api_app.py`, `backend/Makefile`, `scripts/dev.sh`, `cloudbuild.yaml`, `firestore.indexes.json` (modify, project-id env-driven)
- `backend/scripts/seed_skills.py`, `backend/scripts/seed_mcp_servers.py`, `backend/scripts/seed_tool_permissions.py`, `backend/scripts/_env.py`, `backend/scripts/bootstrap_agent_engine.py`, `backend/scripts/smoke_test_infra.py`, `backend/scripts/smoke_artifacts.py` (modify, project-id env-driven)
- `.claude/hooks/lint_on_edit.sh` (modify, path-portable)
- `.claude/skills/platform-deploy/scripts/audit-drift.sh` (modify, path-portable)
- `CLAUDE.md` (modify, remove AgentsCLI block)
- `scripts/sanitize-for-template.sh` (new, ~80 LOC)
- `scripts/check_local_mode_safety.py` (new, ~40 LOC)
- `.github/workflows/ci.yml` (modify, +1 step)

**Acceptance Criteria:**
- [ ] `git grep -i "your-project-id\|<local-path>"` returns only intentional matches: this doc, sprint state JSONs (private), ops docs explicitly marked `<!-- private -->`. Verified by a one-liner committed to `scripts/audit-residuals.sh`
- [ ] Dry-run `bash scripts/sanitize-for-template.sh /tmp/template-dryrun` produces a tree where `cd /tmp/template-dryrun/frontend && npm run quality:check:fast` and `cd /tmp/template-dryrun/backend && pytest -m "not slow"` both pass
- [ ] LICENSE present (Apache 2.0); CONTRIBUTING.md present; env-example files from M3 reference real env vars
- [ ] `scripts/check_local_mode_safety.py` exits 0 on current tree, exits 1 if `LOCAL_MODE=1` is added to `cloudbuild.yaml` (self-test)
- [ ] `make dev` continues to work with no env changes (defaults preserved — full regression)

**Risks:**
- Easy to miss a hardcoded reference — Mitigation: combined `git grep` audit + sanitization script dry-run + CI grep check
- Apache 2.0 license has tax implications / copyright assignment questions — Mitigation: confirmed by user at sprint kickoff (default unless changed)
- `scripts/sanitize-for-template.sh` could be misused — Mitigation: hard-coded refuse-to-run-on-`sunholo-data/ai-protocol-platform`-origin guard; require `--target /path/to/empty/dir` flag

---

## Day-by-Day Tasks (rough)

**Day 1 (M1 part 1):** `config.py` + `firestore_inmemory.py` + parity tests
**Day 2 (M1 part 2 + M2 start):** local_fixture + auth stub + `/api/local-mode-status` + startup asserts. Begin `branding.ts` + `LocalModeBanner.tsx`.
**Day 3 (M2 finish + M3):** `LocalAuthProvider` + `firebase.ts` guard + `fetchWithAuth` branch + layout swap. Workshop fixture expand + `WORKSHOP.md` draft + `/workshop` page. .env.example files. **Checkpoint: 30-min dry-run smoke.**
**Day 4 (M4):** Anon auth on dev Firebase + rules + emulator test + budget alert + WORKSHOP.md shared-tier section + clean-machine smoke.
**Day 5 (M5):** Env-driven project IDs + branding/admin env vars + LICENSE + CONTRIBUTING + path scrub + sanitize script + check_local_mode_safety + CI step. Final audit grep.

## Success Metrics

- **Cold-start time:** <30 min from `git clone` to first chat message on a fresh laptop / VM with no GCP CLI installed (timed)
- **Test coverage:**
  - Frontend: `npm run test:run` — 380+ tests passing, +6 new for M2 (banner, provider, fetchWithAuth-stub paths)
  - Backend: `pytest backend/tests/` — 600+ tests passing, +20 new for M1 (InMemoryFirestoreClient parity, auth stub, startup asserts, pairing warning)
  - Rules: firebase emulator test green for M4 anon isolation
- **Lint + typecheck:** `npm run quality:check:fast` clean; `cd backend && uv run ruff check .` clean
- **Build + deploy:** `npm run build` succeeds; `cd backend && uv run python -c "from backend.fast_api_app import app"` imports clean
- **Public-fork readiness:** `bash scripts/sanitize-for-template.sh --dry-run /tmp/x` produces a tree that passes the same test suite

## Quality Gates

- After every milestone: `npm run quality:check:fast && cd backend && pytest -m "not slow"`
- After M3: full chrome-devtools MCP smoke (banner visible, no Firebase init, chat round-trip)
- After M4: firebase rules emulator test
- After M5: audit-residuals.sh + sanitize.sh dry-run

## Out of Scope

- 1.19 a2ui-workshop-demo — follows this sprint, depends on M3 fixture
- 1.26 mcp-app-render-ux Phase A — workshop polish, optional, depends on iframe-context which shipped in 1.25
- Actual GitHub fork operation — that's a separate manual operation post-sprint, using `scripts/sanitize-for-template.sh` once the sprint is shipped
- Multi-tenancy / hosted workshop SaaS tier — explicitly non-goal per design doc

## Related Documents

- [docs/design/v6.1.0/local-mode-and-workshop-readiness.md](local-mode-and-workshop-readiness.md) — design doc this sprint executes
- [docs/design/v6.0.0/template-split-strategy.md](../v6.0.0/template-split-strategy.md) — pre-fork checklist source
- [docs/design/v6.1.0/SEQUENCE.md](SEQUENCE.md) — item 1.18
- [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md) — July 2026 workshop tracker

---

## Implementation Report

**Completed**: 2026-05-15
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
