# Authenticated Landing & Skill Focus

**Status**: Implemented (2026-06-19, sprint AUTH-LANDING)
**Priority**: P1

> **As-built notes (2026-06-19).**
> - **Partial-upsert fix (beyond plan):** the existing `client set` / admin
>   upsert wrote `null` for unsent fields, so the ONE rollout would have wiped
>   the domain's `derived_group_tags=[ONE]`. Fixed: `--display-name` no longer
>   always-sent + admin upsert uses `model_dump(exclude_unset=True)`.
> - **Skill set:** `enabled_skills` swapped `general-assistant` → `web-researcher`
>   (the generic web-search skill) per the product decision; final set is
>   `one-ppa-expert, one-doc-compare, web-researcher`, primary `one-ppa-expert`.
> - **Config applied via Firestore REST PATCH**, not the CLI/admin API — an
>   admin Firebase token isn't mintable headlessly. The deployed admin endpoint
>   + CLI are unit-tested; the live write used a field-masked PATCH that
>   preserved all other client fields (verified by re-read).
> - **Verified live:** both deploys SUCCESS; `/api/sessions/recent` +
>   `/api/clients/me` deployed and auth-gated; composite index READY. **Not
>   verified headlessly:** the browser DOM redirect + authed payloads (no Chrome
>   connected) — left as a signed-in eyeball.
**Estimated**: ~2.5 days
**Scope**: Fullstack (frontend routing + one backend resolver endpoint + per-client config)
**Dependencies**: v6.3.0 client-tenant-management (`ClientConfig`, `enabled_skills`, `resolve_enabled_skills`) ✅; v6.4.0 4.2 per-user skill filter ✅; domain-derived group tags (commit 052af86) ✅
**Created**: 2026-06-19
**Last Updated**: 2026-06-19

## Problem Statement

Customer feedback (2026-06-19): the product surfaces too many skills and makes signed-in users start from a marketplace instead of working.

**Current State:**
- The homepage (`app/page.tsx`) renders the same landing + full public marketplace for **everyone** — it has no auth awareness and never redirects.
- A signed-in user lands on the marketplace and must pick a skill before they can do anything — friction on every visit, and there's no "resume where I left off".
- The in-app skill list shows every skill the user can access. The per-client `enabled_skills` filter exists ([routes.py:169](../../../backend/skills/routes.py#L169)) but isn't set for the ONE deployment, so all accessible skills show.
- The marketplace endpoint is intentionally unauthenticated and **not** filtered, so the logged-out homepage lists all public skills.

**Impact:**
- **End users** (the ONE customer): every session starts with a chooser instead of a chat; the surface feels unfocused.
- Severity: **major friction** — it's the first thing a returning user hits.

## Goals

**Primary Goal:** A signed-in user landing on `/` goes straight to their **last chat**, or to a ready-to-go chat with the deployment's primary skill if they have none — and the homepage becomes the **logged-out-only** front door.

**Success Metrics:**
- Signed-in visit to `/` → 0 clicks to a usable chat (redirect to last session, or a fresh primary-skill chat).
- Logged-out visit to `/` → the landing/marketing page (unchanged).
- In-app skill set for `@acme-energy.example` users is exactly `[one-ppa-expert, one-doc-compare, web-researcher]`.

**Non-Goals:**
- Filtering the **logged-out** marketplace to a client's skills — logged-out users can't invoke skills anyway; the homepage is marketing. (Tracked as a possible follow-up; the hero already rebrands per deployment.)
- Multi-tab / multi-window "restore all sessions" — we restore the single most-recent session.
- Changing the chat shell itself (that's SHELL-MODES, shipped).

## Axiom Alignment

Score each axiom per [Product Axioms](../../../docs/product-axioms.md). Net score must be >= +4. Max 2 conflicts (-1) allowed.

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +2 | Returning users skip the marketplace entirely and land in their last chat (or a ready primary chat) — zero clicks to work. The recent-session lookup is one indexed Firestore read. |
| 2 | EARNED TRUST | 0 | No new user-facing data claims; the redirect only routes to the user's own sessions. |
| 3 | SKILLS, NOT FEATURES | +1 | Which skills appear + which is primary are per-client **config** (`enabled_skills`, `default_skill`), not platform code. |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing change. |
| 5 | GRACEFUL DEGRADATION | +2 | Resolution is a fallback chain: last session → primary-skill fresh chat → marketplace. Missing config, no sessions, or a since-disabled skill each fall through cleanly. Logged-out + auth-loading states render the landing/spinner, never a broken redirect. |
| 6 | PROTOCOL OVER CUSTOM | 0 | Reuses existing session + skill APIs; one additive read endpoint. |
| 7 | API FIRST | +1 | The "most-recent session" resolver is a clean `GET /api/sessions/recent` reused by any client (web, future channels). |
| 8 | OBSERVABLE BY DEFAULT | +1 | Landing-resolution outcome (resumed / fresh-primary / marketplace-fallback) emitted as a span attribute for funnel analysis. |
| 9 | SECURE BY CONSTRUCTION | +1 | `/api/sessions/recent` is scoped to `owner_uid` from the JWT and re-checks skill visibility before redirecting — a user can never be routed into a session or skill they can't access. |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Redirect logic is thin client glue over the resolver endpoint + skill config. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None.

## Design

### Overview

Three pieces: (1) a backend "most-recent session for this user" resolver; (2) an auth-aware homepage that redirects signed-in users through a fallback chain to a chat; (3) per-client config (`enabled_skills` + a new `default_skill`) to narrow + focus the skill set.

### Backend Changes

**New endpoint — `GET /api/sessions/recent`:**
- Query `ChatSessionIndex` where `owner_uid == user.uid`, order by `last_message_at` desc, limit a small N; return the first whose skill is **still visible + enabled** for the caller (re-check `resolve_enabled_skills` + access). Returns `{ skillId, slug, ownerId, sessionId }` or `204 No Content` when none qualify.
- New helper `most_recent_session_for_user(uid)` in [db/chat_sessions.py](../../../backend/db/chat_sessions.py) (sibling of `list_sessions_for_skill`). Needs a Firestore composite index `(ownerUid ASC, lastMessageAt DESC)`.

**`ClientConfig.default_skill`:**
- New optional field (`default_skill: str | None`) on [admin/clients.py](../../../backend/admin/clients.py) `ClientConfig` — the skill slug a signed-in user lands on with no prior chat. Same null/merge semantics as `enabled_skills`. Resolved via the existing domain→client lookup. Fallback when unset: `enabled_skills[0]`, else marketplace.

**Data Model Changes:** `default_skill` on the `clients` collection (additive nullable). New composite index for the recent-session query (add to `firestore.indexes.json`).

### Frontend Changes

**Auth-aware homepage (`app/page.tsx`):**
- The landing markup stays a server component, but `/` mounts a small client `HomeGate`:
  - auth **loading** → minimal spinner (no flash of marketing);
  - **logged-out** → render the existing landing (Hero / ProtocolStripe / marketplace cards) unchanged;
  - **logged-in** → resolve the landing target and `router.replace(...)` into the app.
- **Landing-target resolution (client):** `GET /api/sessions/recent` → if a session, redirect to `/chat/@{ownerId}/{slug}?session={sessionId}`. Else fetch the deployment's `default_skill` (via `/api/clients/me`) and redirect to `/chat/@{ownerId}/{slug}` (fresh chat). Else fall back to the marketplace (render landing, don't redirect).

**State/UX:** brief spinner during resolution; the existing `useStableThreadId` + chat route handle the `?session=` resume (already built). No skill picker for the empty state — straight into the primary skill's fresh chat (per product decision).

### CLI Surface

Per design-doc-creator rule 5b-bis:
- Extend `aiplatform client set` with `--default-skill <slug>` (~0.1d) alongside the existing `--enabled-skills`. Backlink: [local-dev-cli.md](../v6.1.0/local-dev-cli.md).
- The ONE narrowing is applied with: `aiplatform client set acme-energy.example --enabled-skills one-ppa-expert,one-doc-compare,web-researcher --default-skill one-ppa-expert`.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | /api/sessions/recent | Most-recent visible+enabled session for the caller, or 204 | No (new) |
| GET | /api/clients/me | Caller's resolved client config (enabled_skills, default_skill) | No (additive) |
| PUT | /api/admin/clients/{domain} | Accepts `default_skill` | No (additive) |

### Architecture Diagram

```
GET /  (HomeGate, client)
  |- auth loading      -> spinner
  |- logged-out        -> landing (Hero + marketplace)  [unchanged]
  '- logged-in         -> resolve landing target:
        GET /api/sessions/recent
          |- 200 {skill, session} -> /chat/@owner/slug?session=...   (resume)
          '- 204 (none)           -> default_skill (client config)
                |- set   -> /chat/@owner/slug                        (fresh primary)
                '- unset -> render landing/marketplace               (fallback)
```

## Implementation Plan

### M1 — Recent-session resolver + config field (backend, ~1d)
- [ ] `most_recent_session_for_user(uid)` + `GET /api/sessions/recent` with visibility/enabled re-check (~80 LOC + pytest: none / one / since-disabled-skill-skipped / access-scoped)
- [ ] `ClientConfig.default_skill` + admin upsert + `/api/clients/me` read (~40 LOC + pytest)
- [ ] Firestore composite index `(ownerUid, lastMessageAt)` in `firestore.indexes.json`
- [ ] span attribute for resolution outcome

### M2 — Auth-aware homepage redirect (frontend, ~1d)
- [ ] `HomeGate` client component: loading / logged-out / logged-in branches (~120 LOC)
- [ ] Landing-target resolution hook (`useLandingTarget`) calling `/api/sessions/recent` + client config (~80 LOC + vitest: resume / fresh-primary / fallback / logged-out-no-redirect)
- [ ] Wire into `app/page.tsx` without disturbing the logged-out landing render

### M3 — Config rollout + CLI + verify (~0.5d)
- [ ] `aiplatform client set --default-skill`
- [ ] Apply ONE config: `enabled_skills=[one-ppa-expert,one-doc-compare,web-researcher]`, `default_skill=one-ppa-expert`
- [ ] Deploy dev; chrome-devtools verify: signed-in with history → resumes; signed-in no history → fresh one-ppa-expert chat; logged-out → landing; in-app skill list shows exactly the 3

## Migration & Rollout

**Database Migrations:** `default_skill` additive nullable; new composite index (deploy via `firestore.indexes.json` before the endpoint goes live).
**Feature Flags:** None — behaviour is gated by whether a client has `default_skill` / `enabled_skills` set; deployments without config keep today's marketplace-first homepage (graceful default).
**Rollback Plan:** revert `HomeGate` (homepage returns to today's all-users landing); the backend endpoint + config field are inert without it.
**Environment Variables:** None.

## Testing Strategy

### Backend (pytest)
- [ ] `most_recent_session_for_user` ordering + owner scoping; `/api/sessions/recent` 200/204; since-disabled skill skipped; access re-check
- [ ] `ClientConfig.default_skill` round-trip + admin upsert

### Frontend (Vitest)
- [ ] `useLandingTarget`: resume / fresh-primary / fallback paths
- [ ] `HomeGate`: logged-out renders landing (no redirect); logged-in triggers redirect; loading shows spinner

### Manual / E2E (chrome-devtools)
- [ ] Signed-in w/ prior chat → lands on it; signed-in w/o → fresh one-ppa-expert; logged-out → landing; SkillsBar shows the 3 enabled skills

## Security Considerations

- `/api/sessions/recent` scoped to `owner_uid` from the JWT; re-checks skill visibility + `enabled_skills` before returning a target — no routing into inaccessible sessions/skills.
- No new public surface; the homepage redirect is client-side off the user's own token.

## Performance Considerations

- One indexed Firestore read for the recent session; one config read (cacheable). Redirect resolves before the chat route mounts — net faster to "usable" than today's marketplace render + click.
- No bundle impact beyond the small `HomeGate` (lazy-safe).

## Success Criteria

- [ ] Signed-in `/` → last chat (or fresh primary) with zero clicks; logged-out `/` → landing
- [ ] `@acme-energy.example` in-app skills = exactly the 3 configured
- [ ] `aiplatform client set --default-skill` works end-to-end
- [ ] Backend + frontend tests, lint, typecheck, build all green
- [ ] Live-verified on dev via chrome-devtools

## Open Questions

- **Q1 — `/api/clients/me` vs branding env.** Confirm the frontend doesn't already have the caller's `default_skill`; if not, add `/api/clients/me` so the primary skill is data-driven per domain rather than baked into branding env. Recommended: `/api/clients/me`.
- **Q2 — Narrow the logged-out marketplace too?** Out of scope for v1 (non-goal); revisit if the customer wants the public page to show only their skills.
- **Q3 — "Ready-to-go" vs literal last chat.** We resume the literal last session; if a customer prefers "always a fresh chat", make it a per-client toggle later.

## Related Documents

- [skill-driven-shell-modes.md](skill-driven-shell-modes.md) — the chat shells users land in (shipped).
- [multi-tenant-demo-readiness.md](../v6.4.0/multi-tenant-demo-readiness.md) — `enabled_skills` per-user filter + per-deployment branding.
- [local-dev-cli.md](../v6.1.0/local-dev-cli.md) — `aiplatform client set`.
