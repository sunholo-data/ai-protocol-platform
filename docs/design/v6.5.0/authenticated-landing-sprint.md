# Sprint Plan — Authenticated Landing & Skill Focus (AUTH-LANDING)

**Design doc:** [authenticated-landing.md](authenticated-landing.md)
**Sprint key:** `AUTH-LANDING`
**Window:** ~2.5 days, single dev
**Scope:** Fullstack — backend resolver + per-client config; frontend homepage redirect; config rollout
**Status:** Planned (2026-06-19)

## Goal

Signed-in users landing on `/` go straight to their **last chat** (or a fresh primary-skill chat); the homepage is the **logged-out-only** front door; the in-app skill set is narrowed per client. Routing is platform-wide; skills are per-client config.

## Velocity basis

Last 14 days: 104 commits. The ~2.5-day / 3-milestone estimate is well within recent throughput. Most of the machinery exists (enabled_skills filter, session APIs, chat resume) — the net-new is one resolver endpoint, one config field + read, and the homepage gate.

## Confirmed pre-conditions (from investigation)
- `enabled_skills` filter already applied to authed `/api/skills` (`resolve_enabled_skills`) — narrowing is config, not code.
- `ChatSessionIndex` has `owner_uid` + `last_message_at` — clean most-recent query.
- `/api/clients/me` does **not** exist yet → M1 adds it.
- `aiplatform client set` has `--enabled-skills`, lacks `--default-skill` → M3 adds it.
- `firestore.indexes.json` already references `ownerUid`/`lastMessageAt`; M1 verifies/adds the `(ownerUid ASC, lastMessageAt DESC)` composite.
- `web-researcher` has `google_search` wired (real web search).

## Milestones

| M | Title | Scope | Est | Depends |
|---|-------|-------|-----|---------|
| M1 | Recent-session resolver + client config | backend | ~1d | — |
| M2 | Auth-aware homepage redirect | frontend | ~1d | M1 |
| M3 | CLI `--default-skill` + ONE config rollout + live verify | fullstack/ops | ~0.5d | M1, M2 |

M2 depends on M1's endpoints. M3 is the rollout + verification gate (touches the live env).

### M1 — Recent-session resolver + client config (backend, ~1d)
- `most_recent_session_for_user(uid, limit=N)` in `db/chat_sessions.py` (owner-scoped, `last_message_at` desc).
- `GET /api/sessions/recent` — returns the first session whose skill is still **visible + enabled** (re-check `resolve_enabled_skills` + access); `204` when none qualify. `{skillId, slug, ownerId, sessionId}`.
- `ClientConfig.default_skill: str | None` + admin upsert merge (null/empty semantics like `enabled_skills`).
- `GET /api/clients/me` — caller's resolved client config (`enabled_skills`, `default_skill`, `display_name`).
- `(ownerUid, lastMessageAt DESC)` composite in `firestore.indexes.json`.
- Span attribute for resolution outcome (resumed / no-session).

**Acceptance**
- `most_recent_session_for_user` ordering + owner scoping (pytest)
- `/api/sessions/recent`: 200 with newest qualifying session / 204 when none / since-disabled-skill skipped / cross-user isolation (4 pytest)
- `ClientConfig.default_skill` round-trips admin upsert; `/api/clients/me` returns it (2 pytest)
- `cd backend && make lint && make test-fast` green

### M2 — Auth-aware homepage redirect (frontend, ~1d)
- `useLandingTarget()` hook: calls `/api/sessions/recent` then `/api/clients/me`; returns `{kind: "resume"|"fresh"|"landing", href?}`.
- `HomeGate` client component: auth loading → spinner; logged-out → render existing landing; logged-in → `router.replace(target.href)` or render landing on `landing` fallback.
- Wire into `app/page.tsx` keeping the logged-out landing render byte-for-byte.

**Acceptance**
- `useLandingTarget`: resume (session present) / fresh (no session, default_skill set) / landing (no session, no default_skill) (vitest)
- `HomeGate`: logged-out renders landing + no redirect; logged-in resume/fresh calls `router.replace` with the right href; loading shows spinner (vitest)
- Logged-out homepage markup unchanged (snapshot/smoke)
- `cd frontend && npm run quality:check` green

### M3 — CLI + ONE config rollout + live verify (~0.5d)
- `aiplatform client set --default-skill <slug>` (Click option + httpx PUT + unit test).
- Apply ONE config: `aiplatform client set acme-energy.example --enabled-skills one-ppa-expert,one-doc-compare,web-researcher --default-skill one-ppa-expert`.
- Deploy dev (push triggers frontend+backend); chrome-devtools live verify.

**Acceptance**
- `aiplatform client set --default-skill` round-trips (unit test)
- Live (chrome-devtools): signed-in w/ prior chat → resumes; signed-in w/o → fresh one-ppa-expert; logged-out → landing; SkillsBar shows exactly the 3 enabled skills
- ONE client config persisted (verify via `/api/clients/me` or admin GET)

## Out of scope (design non-goals)
Filtering the logged-out marketplace; multi-session restore; chat-shell changes; "always fresh chat" per-client toggle (Q3, later).

## Quality gates
- Per milestone: backend `make lint && make test-fast`; frontend `npm run quality:check:fast`.
- Sprint close: full `npm run quality:check` (incl. build) + `make lint && make test-fast` + chrome-devtools live verify.

## Risks
- **Homepage is a server component**; the redirect must be client-side. Mitigation: a thin `HomeGate` client wrapper; keep the logged-out landing render unchanged (low risk).
- **Composite index** must be deployed before `/api/sessions/recent` serves in prod-like envs. Mitigation: ship the index in M1, confirm built before M3 verify.
- **`enabled_skills` re-check in the resolver** must not 500 if a stored session points at a now-hidden skill. Mitigation: skip-and-continue, covered by the since-disabled test.
