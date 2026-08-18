# LOCAL_MODE and Workshop Readiness

**Status**: Implemented
**Priority**: P1 (blocks July 2026 workshop)
**Estimated**: ~4 days (M1 1.5d + M2 0.5d + M3 0.5d + M4 0.5d + M5 1d)
**Scope**: Backend + Frontend + Repo hygiene
**Dependencies**:
  - [Session & Memory](implemented/../v6.0.0/implemented/session-and-memory.md) ✅ — sessions/artifacts already fall back when env vars unset; this doc extends the same pattern to Firestore + auth
  - [Template Split Strategy](../v6.0.0/template-split-strategy.md) — downstream consumer; the public template fork should pick up this work, not redo it
  - [MCP App Integrations](mcp-app-integrations.md) — cross-reference for workshop W7; not blocked by this doc
**Created**: 2026-04-28
**Last Updated**: 2026-05-15

## Problem Statement

We are presenting this codebase as a workshop in **July 2026** ([SEQUENCE.md "Workshop critical path"](SEQUENCE.md), [docs/talks/ai-ui-protocol-stack.md](../../talks/ai-ui-protocol-stack.md)). Attendees should be able to `git clone` and have a working local instance in **under 30 minutes**, with cloud (GCP) options as a separate optional step afterwards. Today the repo does not meet that bar.

**Current State:**

What already falls back cleanly when env vars are unset:
- ADK sessions → `InMemorySessionService` ([backend/adk/session.py:88-96](../../../backend/adk/session.py#L88-L96)) when `AGENT_ENGINE_ID` is unset
- ADK artifacts → `InMemoryArtifactService` ([backend/adk/session.py:130-132](../../../backend/adk/session.py#L130-L132)) when `ADK_ARTIFACT_BUCKET` is unset
- Frontend Firebase init → returns `null` cleanly when `NEXT_PUBLIC_FIREBASE_*` is unset ([frontend/src/lib/firebase.ts:31](../../../frontend/src/lib/firebase.ts#L31))

> ⚠️ **Sessions and artifacts must be configured as a pair.** Setting only `AGENT_ENGINE_ID` (cloud sessions, in-memory artifacts) silently strands every session across backend restarts — `app:docs_loaded` survives but the `doc:{id}.json` blobs behind it don't, and the document injector then has nothing to inline. The loader's orphan-probe (added 2026-04-28, see [backend/adk/callbacks.py](../../../backend/adk/callbacks.py)) self-heals on the next user message, but **LOCAL_MODE should not encourage the asymmetric configuration** — the env-var loader either flips both to cloud or keeps both in-memory.

What does **not** fall back and breaks first-run:
- **Firestore** — [backend/db/firestore.py:27](../../../backend/db/firestore.py#L27) calls `firestore.Client()` unconditionally. Skills config, chat-session mirror, users, doc registry are all on the hot path. Without ADC + a real Firestore project, the backend boots but every request 500s.
- **Backend auth** — Firebase ID-token verification requires a configured Firebase Admin SDK. With no `GOOGLE_APPLICATION_CREDENTIALS`, every authenticated route fails.
- **Frontend auth** — even though `firebase.ts` returns `null`, the auth UI then shows "not configured" and there is no path to a signed-in identity, so attendees cannot send a chat message.
- **Other GCP services** — Vertex AI Search, Cloud Trace/Logging, Mailgun init log warnings or fail at import.

Workshop-readiness gaps beyond LOCAL_MODE:
- Repo is currently **private**, but contains content that blocks public push: `internal preview bundle/` (NDA), two preview PDFs in `docs/`, `docs/feedback/internal feedback notes.md`.
- 20+ internal sprint docs in `docs/design/v6.0.0/implemented/` are not appropriate for attendees of a public workshop fork.
- Hardcoded `your-project-id` project ID at [backend/app.py:29](../../../backend/app.py#L29), [scripts/dev.sh](../../../scripts/dev.sh), [cloudbuild.yaml](../../../cloudbuild.yaml).
- Hardcoded `<local-path>` paths in `.claude/hooks/lint_on_edit.sh` and `scripts/platform-deploy/scripts/audit-drift.sh`.

**Impact:**
- **Workshop attendees**: today, "follow the README" requires standing up a Firebase project, enabling Firestore, downloading service-account keys. That is a 60+ minute prerequisite, not a 30-minute first run. Attrition before the interesting content (ADK / AG-UI / A2UI / MCP Apps) is high.
- **Local development for the team**: `make dev` against the dev Firestore is the only path. There is no offline-train / coffee-shop / no-VPN dev loop.
- **Public fork ([template-split-strategy.md](../v6.0.0/template-split-strategy.md))**: cannot be cut until the NDA content is removed and the codebase is shown to run without internal-project IDs. LOCAL_MODE is the proof.

## Goals

**Primary Goal:** A `git clone` of the (workshop-fork) repo, followed by `make dev`, brings up a fully functional chat UI end-to-end with **zero GCP credentials** and a single env flag (`LOCAL_MODE=1`), in under 30 minutes on a fresh laptop.

**Success Metrics:**
- Time-to-first-message (clone → working chat) on a fresh laptop: <30 min (timed dry-run before the workshop)
- `make dev` with no `.env` set boots and the demo skill responds (verified by chrome-devtools MCP)
- `pytest` backend test suite passes with no GCP project / ADC configured
- `grep` for hardcoded `your-project-id`, `<local-path>`, `AgentsCLI` returns only intentional references (workshop docs, `.gitignore`, this doc)

**Non-Goals:**
- **Not** making every GCP service work in LOCAL_MODE. Vertex AI Search, Cloud Trace, BigQuery telemetry, Mailgun should degrade gracefully to "disabled in LOCAL_MODE" warnings, not fail boot. Attendees who want them set up a real GCP project (see "Graduating from LOCAL_MODE" below).
- **Not** forking the repo as part of this work. The fork is a downstream operation per [template-split-strategy.md](../v6.0.0/template-split-strategy.md). LOCAL_MODE landing first means the fork is "delete internal stuff and squash history," not "rebuild auth too."
- **Not** designing the MCP App workshop demo here. [mcp-app-integrations.md](mcp-app-integrations.md) covers it; we cross-reference but do not duplicate.
- **Not** a sandboxed multi-tenant cloud demo. The "shared dev Firestore" cloud-mode path is opt-in for attendees who want a more-realistic backend; it is not a hosted SaaS workshop tier.

## Axiom Alignment

Score each axiom per [Product Axioms](../../product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | LOCAL_MODE has no effect on streaming or perceived latency. |
| 2 | EARNED TRUST | 0 | No change to factual claim attribution. |
| 3 | SKILLS, NOT FEATURES | +1 | Bundled demo skill in the LOCAL_MODE fixture is a discoverable, runnable skill — reinforces the abstraction at first contact. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing changes. |
| 5 | GRACEFUL DEGRADATION | +1 | LOCAL_MODE *is* graceful degradation: every GCP dependency degrades to in-memory or no-op, with a banner explaining what's missing and how to enable it. |
| 6 | PROTOCOL OVER CUSTOM | 0 | No new protocols; `InMemoryFirestoreClient` mimics existing `firestore.Client` interface. |
| 7 | API FIRST | +1 | LOCAL_MODE preserves identical HTTP/AG-UI surfaces — the only difference is the persistence layer. Attendees can hit the same endpoints they would in cloud mode. |
| 8 | OBSERVABLE BY DEFAULT | 0 | Cloud Trace and BigQuery telemetry no-op in LOCAL_MODE; logs still print to stdout. |
| 9 | SECURE BY CONSTRUCTION | -1 | LOCAL_MODE auth bypass injects a stub identity. **Mitigation:** runtime assert that LOCAL_MODE is rejected when `K_SERVICE` (Cloud Run) or `GAE_ENV` is set; visible UI banner; refusal to start if `LOCAL_MODE=1` is paired with any non-localhost listener. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | The LOCAL_MODE banner is a thin client decoration over a backend-emitted `/api/local-mode-status` flag — frontend asks, doesn't decide. |
| | **Net Score** | **+3** | Marginal. |

**Conflict Justifications:**
- **#9 SECURE BY CONSTRUCTION (-1):** The auth-bypass stub is a security footgun if it ever leaks into a deployed env. Tradeoff is justified because (a) the workshop quick-start path is the entire reason this doc exists; (b) mitigations are deterministic (`K_SERVICE` env var detection, refusal to bind beyond `127.0.0.1`); (c) the hard-fail rule "SECURE BY CONSTRUCTION is not -1 if feature introduces new data access patterns" does not apply — LOCAL_MODE introduces no new data access; it stubs out *existing* access for a local in-memory store.

Net score is +3, marginally below the +4 threshold. The +3 reflects that LOCAL_MODE is operational scaffolding, not a product feature. Recommend proceeding with the SECURE BY CONSTRUCTION mitigations explicit in M1.

## Design

### Overview

A single canonical env flag `LOCAL_MODE=1` (backend) and `NEXT_PUBLIC_LOCAL_MODE=1` (frontend) activates *every* in-memory or stub fallback at once — no per-service flags. Existing `AGENT_ENGINE_ID`-style fallbacks remain in place; LOCAL_MODE simply extends the pattern to Firestore + auth, then adds a UI banner and a documented "graduate to cloud" path.

The mode is **dev-only**, enforced by a startup assertion that LOCAL_MODE cannot coexist with deployment-environment indicators. The visible banner makes it obvious to anyone running the system that they are not in a real environment.

### Frontend Changes

**New Components:**
- `src/components/LocalModeBanner.tsx` — top-of-page banner: "🛠️ Running in LOCAL_MODE — auth is stubbed, data is in-memory and resets on reload. [How to connect to GCP →]". Banner is visible on every page, dismissible per-session but reappears on reload. Link routes to `WORKSHOP.md#graduating-from-local-mode`.
- `src/providers/LocalAuthProvider.tsx` — when `NEXT_PUBLIC_LOCAL_MODE=1`, replaces `FirebaseAuthProvider` in the provider tree. Exposes a fixed identity (`uid: workshop-user`, `email: workshop@local`, `displayName: Workshop Attendee`) and a `getIdToken()` that returns a deterministic token (`local-mode-stub-token`).

**Modified Components:**
- `src/lib/firebase.ts` — top-level `isLocalMode()` check. When true, all exports return early (no Firebase SDK init, no Firestore listeners). Existing `null`-return paths already handle the rest.
- `src/lib/fetchWithAuth.ts` — when `LOCAL_MODE`, send `Authorization: Bearer local-mode-stub-token` so the backend stub recognises it.
- `src/app/layout.tsx` — mount `<LocalModeBanner />` above the main content when `process.env.NEXT_PUBLIC_LOCAL_MODE === "1"`.
- `src/providers/AGUIProvider.tsx` — no behavioural change, just verify it still works against the backend stub.

**State Management:**
- No new contexts beyond `LocalAuthProvider`. The banner is purely a feature-flag boolean read at render.

**UI/UX:**
- Banner uses a soft-yellow / construction theme — visible but not alarming. Inline link to the "graduate" docs section.
- Sign-in screen is bypassed entirely in LOCAL_MODE; user lands directly on the chat page.
- Skill picker shows the seeded demo skill (`Demo Researcher`) so the UI isn't empty.

### Backend Changes

**New Endpoints:**
- `GET /api/local-mode-status` — returns `{ "local_mode": bool, "missing_services": ["vertex_search", "cloud_trace", ...] }`. Frontend banner reads this to show what's specifically disabled. No auth required.

**Modified Endpoints:**
- All authenticated routes — when `LOCAL_MODE=1`, the auth dependency injects a stub `User(uid="workshop-user", email="workshop@local")` instead of verifying the Firebase ID token. Token must literally equal `"local-mode-stub-token"`; any other token is rejected (prevents accidental real-token use against a LOCAL_MODE backend).

**New Services/Modules:**
- `backend/db/firestore_inmemory.py` — `InMemoryFirestoreClient` implementing the subset of `firestore.Client` v6 actually uses: `collection().document().get/set/update/delete()`, `collection().where().order_by().limit().stream()`, and `firestore.Increment` / `firestore.SERVER_TIMESTAMP` semantics. Backed by a thread-safe nested dict. Persistence: optional JSON dump to `~/.aitana-local/firestore.json` on shutdown so attendees keep state between `make dev` runs.
- `backend/db/local_fixture.py` — seeds the in-memory client at startup with: 1 demo skill (`Demo Researcher`, public, simple instruction), 1 demo parsed document (small Markdown), 1 workshop user mapping. Idempotent — only seeds if the collections are empty.
- `backend/auth/local_mode_stub.py` — stub auth dependency: returns the workshop user when `LOCAL_MODE=1` AND token matches the stub. All other paths reject.

**Modified Services:**
- `backend/db/firestore.py:get_client()` — when `LOCAL_MODE=1`, returns `InMemoryFirestoreClient()` instead of `firestore.Client()`. Singleton cached the same way.
- `backend/auth/firebase.py` — auth dependency factory consults `is_local_mode()` and swaps in the stub.
- `backend/fast_api_app.py` — startup banner extended to print "LOCAL_MODE: ON — Firestore in-memory, auth stubbed, data resets on next boot unless persisted to ~/.aitana-local/" when active. Adds runtime assertion: `assert not (LOCAL_MODE and (K_SERVICE or GAE_ENV))` — refuse to start if LOCAL_MODE is paired with deployment env vars.
- Other GCP services (Vertex Search, Cloud Trace exporter, Mailgun) — wrap import / init in `if not is_local_mode():` guards. Tool-level: a search tool called in LOCAL_MODE returns `"search disabled in LOCAL_MODE — set VERTEX_AI_SEARCH_DATASTORE_ID to enable"` as the tool result so the agent sees it and can explain.

**Data Model Changes:**
- None. `InMemoryFirestoreClient` is a drop-in for the same data shapes.

### LOCAL_MODE UI Banner — Detail

The banner is the answer to the user's "we can have a UI indication we are in local mode" requirement. It serves three purposes:

1. **Trust calibration** — attendees know that what they see is local-only and ephemeral; no surprise data loss.
2. **Discoverability** — the "How to connect to GCP →" link inside the banner is the single funnel from local to cloud-mode.
3. **Safety** — if anyone deploys a build accidentally with `NEXT_PUBLIC_LOCAL_MODE=1`, the banner is loud enough to catch attention before the auth-bypass causes harm.

Content (rendered as a thin top strip on every page):

```
🛠️ LOCAL_MODE — All data is in-memory and ephemeral. Auth is stubbed.
                Disabled: Vertex AI Search, Cloud Trace, BigQuery telemetry.
                [Connect to your own GCP →]   [What's disabled?]
```

Both links open `/WORKSHOP.md` anchors (rendered through Next.js MD page) — same source as the README walk-through, no doc duplication.

### Graduating from LOCAL_MODE — Steps Documented in WORKSHOP.md

The `[Connect to your own GCP →]` link routes to a `WORKSHOP.md` section (or `/workshop` Next page) with the following ordered steps. This is the user's "with steps on what is needed to not be local" requirement, baked into the doc the banner points at:

1. **Create a GCP project** (or use an existing one). Enable: Firestore (Native mode), Identity Platform / Firebase Auth, Vertex AI, Cloud Storage.
2. **Run `gcloud auth application-default login`** to give the local backend ADC.
3. **Create a Firebase web app** in the project; copy the config snippet.
4. **Set frontend env vars** in `frontend/.env.local`:
   - `NEXT_PUBLIC_FIREBASE_API_KEY=...`
   - `NEXT_PUBLIC_FIREBASE_PROJECT_ID=...`
   - `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=...`
   - `NEXT_PUBLIC_FIREBASE_APP_ID=...`
   - **Unset** `NEXT_PUBLIC_LOCAL_MODE` (or set to `0`).
5. **Set backend env vars** in `backend/.env`:
   - `GOOGLE_CLOUD_PROJECT=your-project-id`
   - **Unset** `LOCAL_MODE` (or set to `0`).
6. **(Optional) Persistence beyond local Firestore:**
   - Set `AGENT_ENGINE_ID=...` to use Vertex AI Agent Engine for chat history (else stays InMemorySessionService).
   - Set `ADK_ARTIFACT_BUCKET=gs://...` for GCS-backed artifacts (else stays InMemoryArtifactService).
   - Set `VERTEX_AI_SEARCH_DATASTORE_ID=...` for the search tool (else returns disabled message).

   > ⚠️ **Set both `AGENT_ENGINE_ID` and `ADK_ARTIFACT_BUCKET` together, or neither.** Mixing cloud sessions (persistent) with in-memory artifacts (process-local) is the worst configuration: the session's `app:docs_loaded` list survives every backend restart, but the `doc:{id}.json` artifacts behind it don't. The loader sees ids it thinks it already loaded, skips re-saving, and the document injector then loads nothing — the agent ends up saying "I couldn't find an artifact" or "I have no memory of your documents." The 2026-04-28 fix in [backend/adk/callbacks.py](../../../backend/adk/callbacks.py) (the `make_document_loader` orphan-probe) auto-recovers from this so the bug heals on the next user message, but the cleanest answer is parity: either both in-memory or both cloud-backed. See `gotcha_session_artifact_persistence_mismatch` in personal memory for the original incident.

7. **Restart `make dev`.** The banner disappears; you sign in with Google or email/password through Firebase; data persists in Firestore.

### Cloud-Mode for Workshop: Shared Dev Firestore (optional path)

For attendees who want to skip GCP project setup but still see real Firestore behavior, we offer an opt-in **shared workshop Firestore tier** backed by `your-project-id`:

- Pre-existing Firebase web app in `your-project-id`, public web API key shared in workshop materials. Public web API keys are not secrets ([Firebase docs](https://firebase.google.com/docs/projects/api-keys)) — security is enforced by Firestore rules + auth domain restrictions, not key opacity.
- **Anonymous Auth enabled** on the project — attendees sign in anonymously; each gets a per-session UID.
- **Tightened Firestore rules** so anonymous users can only read/write under `chat_sessions/{anonymous-uid}/...` and `users/{anonymous-uid}/...`. No cross-user reads. Public skills (`Demo Researcher` etc.) are read-only-shared.
- **Budget guard:** GCP budget alert at $50 on the dev project before workshop. Firestore rules limit document size + per-uid write rate. Workshop opens late, closes when wifi-stragglers are done — not a long-lived hosted tier.
- Attendees opt in by setting only `NEXT_PUBLIC_FIREBASE_*` (no `NEXT_PUBLIC_LOCAL_MODE`); backend continues to run on their laptop, just talking to the shared dev Firestore.

This is the third tier in the matrix:

| Mode | Effort | What's real | What's stubbed |
|---|---|---|---|
| **LOCAL_MODE** | 0 min | Backend, frontend, agent, AG-UI, A2UI | Firestore, auth, GCS, Vertex Search, telemetry |
| **Shared dev Firestore** | 5 min | + Firestore, Firebase anon auth | Backend still local; GCS, Vertex Search, telemetry off |
| **Own GCP project** | 30–60 min | Everything | Nothing |

### Architecture Diagram

```
LOCAL_MODE=1 path:

[Workshop attendee laptop]
    │
    ├─► [Frontend :3456]
    │       └─► <LocalModeBanner /> + <LocalAuthProvider />
    │       └─► fetchWithAuth → Authorization: Bearer local-mode-stub-token
    │
    └─► [Backend :1956]
            ├─► get_client() → InMemoryFirestoreClient (seeded fixture)
            ├─► get_session_service() → InMemorySessionService (existing)
            ├─► get_artifact_service() → InMemoryArtifactService (existing)
            ├─► auth_dep() → stub User(uid="workshop-user")
            └─► /api/local-mode-status → {"local_mode": true, "missing": [...]}
```

## Implementation Plan

### M1 — Backend LOCAL_MODE (~1.5 days)
- [ ] `backend/config.py:is_local_mode()` helper (~10 LOC) — single source of truth
- [ ] `backend/db/firestore_inmemory.py` — `InMemoryFirestoreClient` with the 12 methods we use (~250 LOC + tests)
- [ ] `backend/db/firestore.py` — `get_client()` branches on `is_local_mode()` (~5 LOC change)
- [ ] `backend/db/local_fixture.py` — seeded demo skill + doc + user (~80 LOC)
- [ ] `backend/auth/local_mode_stub.py` — stub auth dependency (~40 LOC)
- [ ] `backend/auth/firebase.py` — branch on `is_local_mode()` to swap dependency (~10 LOC change)
- [ ] `backend/fast_api_app.py` — startup assert + banner string (~15 LOC change)
- [ ] `GET /api/local-mode-status` endpoint (~30 LOC)
- [ ] Wrap Vertex Search / Cloud Trace / Mailgun init in LOCAL_MODE guards (~30 LOC across 3 files)
- [ ] **Sessions/artifacts pairing guard** — at startup, log a `WARNING` (or refuse to boot in `LOCAL_MODE=1`) when only one of `AGENT_ENGINE_ID` / `ADK_ARTIFACT_BUCKET` is set. Mixed configs strand sessions across restarts (see Problem Statement note); the orphan-probe in [adk/callbacks.py](../../../backend/adk/callbacks.py) self-heals it but the warning catches the misconfiguration up front. Reuse `is_local_mode()` so `LOCAL_MODE=1` always implies "both in-memory" and is mutually exclusive with either cloud var.
- [ ] Tests: `pytest backend/tests/` passes with no GCP env vars set; LOCAL_MODE+K_SERVICE refuses to start; in-memory client matches real Firestore for our usage patterns; setting only one of `AGENT_ENGINE_ID`/`ADK_ARTIFACT_BUCKET` emits the pairing warning
- **Checkpoint:** `LOCAL_MODE=1 uv run uvicorn backend.fast_api_app:app` starts; `curl http://localhost:1956/api/local-mode-status` returns `{"local_mode": true, ...}`. Commit `feat(LOCAL_MODE M1): backend in-memory Firestore + auth stub`.

### M2 — Frontend LOCAL_MODE (~0.5 day)
- [ ] `src/components/LocalModeBanner.tsx` (~80 LOC + Vitest)
- [ ] `src/providers/LocalAuthProvider.tsx` (~60 LOC + Vitest)
- [ ] `src/lib/firebase.ts` — `isLocalMode()` early-return guard (~10 LOC change)
- [ ] `src/lib/fetchWithAuth.ts` — stub-token branch (~5 LOC change)
- [ ] `src/app/layout.tsx` — mount banner + swap provider (~10 LOC change)
- [ ] `frontend/.env.example` — document `NEXT_PUBLIC_LOCAL_MODE=1`
- [ ] Vitest: banner renders only when flag set; `LocalAuthProvider` exposes the stub identity; `fetchWithAuth` uses stub token in LOCAL_MODE
- [ ] chrome-devtools MCP smoke: open `localhost:3456`, see banner, send a chat message, get a response — no Firebase init in network log
- **Checkpoint:** `NEXT_PUBLIC_LOCAL_MODE=1 npm run dev` boots without `NEXT_PUBLIC_FIREBASE_*`; chat works end-to-end. Commit `feat(LOCAL_MODE M2): frontend banner + stub auth provider`.

### M3 — Workshop fixture + WORKSHOP.md (~0.5 day)
- [ ] Expand `backend/db/local_fixture.py` with 2-3 demo skills covering ADK / AG-UI / A2UI demos (so workshop modules W2/W5/W6 each have a working starter skill)
- [ ] Author top-level `WORKSHOP.md` with: clone → `make dev` → first message walkthrough; "Graduating from LOCAL_MODE" section (the 7 ordered steps above); "Shared dev Firestore" optional tier
- [ ] Add `WORKSHOP.md` link to top of root `README.md`
- [ ] Render `WORKSHOP.md` at `/workshop` route on the Next app so the LOCAL_MODE banner can deep-link to it
- **Checkpoint:** A clean checkout + `make dev` followed by `WORKSHOP.md` produces a working chat in <30 minutes (timed dry-run on a fresh laptop / VM). Commit `feat(LOCAL_MODE M3): workshop fixture + WORKSHOP.md`.

### M4 — Shared dev Firestore mode (~0.5 day)
- [ ] Enable Anonymous Auth on `your-project-id` Firebase project
- [ ] Update `firestore.rules` to scope anonymous-uid writes to `chat_sessions/{uid}/**` and `users/{uid}/**`; deny everything else for anon
- [ ] Add a budget alert ($50) on `your-project-id`
- [ ] Document in `WORKSHOP.md` the shared-tier env var snippet (`NEXT_PUBLIC_FIREBASE_*` only, no key rotation needed — public web API keys are documented)
- [ ] Smoke: from a clean machine with no GCP CLI, set the public env vars, run `make dev`, send a message, verify it lands in Firestore at the expected anon-uid path
- **Checkpoint:** Two attendees on the shared tier cannot read each other's chat sessions. Commit `feat(LOCAL_MODE M4): shared workshop Firestore tier + tightened rules`.

### M5 — Repo scrub for public-fork prep (~1 day)
- [ ] Remove `internal preview bundle/` (NDA — covered by internal feedback notes.md (internal notes), but the wheel + skill bundle stay private)
- [ ] Remove NDA PDFs: `docs/External Preview Guide_ Agents CLI (1).pdf`, `docs/vendor/Preview Guide_ Python ADK MCP.pdf`
- [ ] Remove `docs/feedback/` (or relocate to a private repo)
- [ ] Remove `.claude/state/sprints/`, `.claude/hooks/lint_on_edit.sh`'s hardcoded `<local-path>` (templated via env)
- [ ] Replace hardcoded `your-project-id` in `backend/app.py:29`, `scripts/dev.sh`, `cloudbuild.yaml`, `firestore.indexes.json` with env-driven defaults
- [ ] Decide what to keep vs. drop in `docs/design/v6.0.0/implemented/` (likely: keep the architecture / models docs; drop the *-sprint.md files which are workflow ephemera)
- [ ] Add `LICENSE` (Apache 2.0 to match v6 platform open-source intent — confirm with Mark before committing)
- [ ] Add `CONTRIBUTING.md` (workshop-focused: how to file issues, how to open a PR with a new skill)
- [ ] Search for any other Aitana-internal references; replace customer-context examples with neutral demos
- [ ] Update `CLAUDE.md` to remove the AgentsCLI references (already flagged at line ~140 in the project CLAUDE.md)
- **Checkpoint:** `git grep -i "your-project-id\|<local-path>"` returns only intentional matches. Commit `chore(LOCAL_MODE M5): scrub for public fork`.

## Migration & Rollout

**Database Migrations:**
- None. `InMemoryFirestoreClient` matches the existing data shapes; switching to real Firestore writes the same documents.

**Feature Flags:**
- `LOCAL_MODE` (env) and `NEXT_PUBLIC_LOCAL_MODE` (env) — paired. Either both set or neither set in any given deployment. CI lint (a small `scripts/check_local_mode_safety.py`) verifies that production Cloud Build configs never set them.

**Rollback Plan:**
- LOCAL_MODE is purely additive — no existing code paths change behaviour when the flag is off. Rollback is "unset the flag" — no data migration, no service restart sequencing.

**Environment Variables:**

| Var | Where | When set | Effect |
|---|---|---|---|
| `LOCAL_MODE=1` | `backend/.env` | Local dev only | Backend uses InMemoryFirestoreClient + stub auth. **NEVER set in any deployed env.** |
| `NEXT_PUBLIC_LOCAL_MODE=1` | `frontend/.env.local` | Local dev only | Frontend mounts banner + LocalAuthProvider. **NEVER set in any deployed env.** |

## Testing Strategy

### Frontend Tests (Vitest + React Testing Library)
- [ ] `LocalModeBanner` renders only when flag is set; dismiss works; "Connect to GCP" link routes correctly
- [ ] `LocalAuthProvider` exposes deterministic stub identity; `getIdToken()` returns the stub token
- [ ] `fetchWithAuth` sends stub token in LOCAL_MODE
- [ ] Existing auth-flow tests continue to pass when flag is off
- [ ] `firebase.ts` early-return when LOCAL_MODE — no SDK init, no listeners

### Backend Tests (pytest)
- [ ] `InMemoryFirestoreClient` parity tests against the methods we actually use (CRUD + queries + Increment)
- [ ] `get_client()` branches correctly on `LOCAL_MODE`
- [ ] Auth dependency injects stub user when `LOCAL_MODE=1` and token matches
- [ ] `LOCAL_MODE=1` + `K_SERVICE` set → fast_api_app refuses to start (assertion error at boot)
- [ ] `LOCAL_MODE=1` + auth token != stub token → 401
- [ ] `pytest backend/tests/` runs to green with no GCP creds present (CI gate)
- [ ] Workshop fixture seeds idempotently (running twice doesn't duplicate)

### Manual Testing
- [ ] Fresh laptop, `git clone`, `make dev` with no env vars: time to first chat message <30 minutes
- [ ] Banner is visible, dismissible, deep-links to WORKSHOP.md
- [ ] Demo skill responds to a question; tool calls work
- [ ] AG-UI streaming works against the LOCAL_MODE backend (no protocol change; verify with chrome-devtools MCP network panel)
- [ ] A2UI demo skill emits a form, renders, submits, agent sees the values (uses [a2ui-workshop-demo.md](a2ui-workshop-demo.md) — separate doc)

## Security Considerations

- **Auth bypass is the central risk.** Mitigations are layered:
  1. Runtime assertion in `fast_api_app.py` startup: refuse to start if `LOCAL_MODE=1` AND (`K_SERVICE` OR `GAE_ENV` OR `KUBERNETES_SERVICE_HOST`) are set. Cloud Run / App Engine / GKE all set one of these.
  2. CI lint checks no production cloudbuild.yaml or terraform sets `LOCAL_MODE=1`.
  3. Backend stub-token must match exactly `local-mode-stub-token`; any other token is rejected. This means a misconfigured deployment can't accept arbitrary tokens, even if the assertion is somehow bypassed.
  4. Visible banner makes the state obvious to anyone using the system; no silent compromise.
  5. Default backend bind in LOCAL_MODE is `127.0.0.1`, not `0.0.0.0` — the stub-auth surface is not exposed to the network.
- **Shared dev Firestore tier:** isolation is enforced by Firestore rules (per-anon-uid path scoping), not by application code. Rules are unit-tested (firebase emulator) before workshop.
- **No secrets in repo:** `.env.local` and `backend/.env` are correctly gitignored ([.gitignore:22-23](../../../.gitignore#L22-L23)). Workshop materials publish only public Firebase web config (`NEXT_PUBLIC_*`) and the Firestore rules.

## Performance Considerations

- `InMemoryFirestoreClient` is a hashmap-backed structure; query performance is O(n) over collection size. Workshop fixture is <100 documents; not a concern.
- Optional JSON dump on shutdown to `~/.aitana-local/firestore.json` adds ~50ms to graceful shutdown; unnoticeable.
- Bundle-size impact: `LocalModeBanner` and `LocalAuthProvider` are <2KB combined gzipped; tree-shaken out when `NEXT_PUBLIC_LOCAL_MODE` is unset (verify via `next build` analyser).

## Success Criteria

- [ ] `make dev` with no GCP env vars boots; chat UI works end-to-end (send a message, receive a streamed response from the demo skill)
- [ ] `pytest backend/` passes with `GOOGLE_CLOUD_PROJECT` unset and no ADC configured (CI gate)
- [ ] chrome-devtools MCP smoke: load frontend, see LOCAL_MODE banner, send a chat message, AG-UI stream + tool call + response all visible; no Firebase init in network log
- [ ] `LOCAL_MODE=1 K_SERVICE=foo make dev` refuses to start with a clear error
- [ ] Timed dry-run: a fresh laptop or VM, no GCP CLI, follows `WORKSHOP.md` and is sending chat messages in **<30 min** end-to-end
- [ ] `git grep -i "your-project-id\|<local-path>"` returns only intentional references after M5 scrub
- [ ] Two attendees on the shared dev Firestore tier cannot read each other's chat sessions (rules unit test)
- [ ] All frontend tests passing (`npm run test:run`); lint + typecheck clean (`npm run quality:check:fast`)

## Open Questions

- **Firestore emulator vs. custom InMemoryFirestoreClient:** the official emulator is more behaviourally accurate but adds a Java dependency and another process attendees must run. Recommendation: ship the custom in-memory client first; switch to emulator if behavioural drift becomes painful (e.g., transaction semantics, server timestamps). Decision deferred to M1 implementation; revisit if drift bites.
- **Workshop fork timing:** depends on [template-split-strategy.md](../v6.0.0/template-split-strategy.md) (mid-to-late May 2026). LOCAL_MODE should land before the fork so the fork operation is purely deletions, not new-feature work. If the fork is delayed past LOCAL_MODE, that's fine — LOCAL_MODE is independently useful for offline dev.
- **Persistence of LOCAL_MODE state:** opt-in JSON dump to `~/.aitana-local/firestore.json`. Should this be on by default for workshop attendees who want their state to survive a `make dev` restart, or off by default for purity? Recommendation: opt-in via `LOCAL_MODE_PERSIST=1` to keep the default fully ephemeral.
- **Shared dev Firestore tier — wifi-blocked workshops?** If conference wifi is blocked or unreliable, the shared tier becomes useless. LOCAL_MODE remains the fallback. No action required, just document it.

## Related Documents

- [SEQUENCE.md](SEQUENCE.md) — workshop critical path; this doc adds itself as 1.18
- [Template Split Strategy](../v6.0.0/template-split-strategy.md) — downstream consumer
- [MCP App Integrations](mcp-app-integrations.md) — workshop W7; not blocked by LOCAL_MODE but referenced from WORKSHOP.md
- [A2UI Workshop Demo](a2ui-workshop-demo.md) — companion sprint, separate doc; covers W6 demo
- [Session & Memory](../v6.0.0/implemented/session-and-memory.md) — existing fallback pattern this doc extends
- [Local Dev CLI](local-dev-cli.md) — `aitana dev local` could become a thin wrapper around `LOCAL_MODE=1 make dev`; out of scope here, decide in CLI sprint
- [Talks: AI UI Protocol Stack](../../talks/ai-ui-protocol-stack.md) — workshop content this enables

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
