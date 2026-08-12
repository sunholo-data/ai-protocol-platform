# Skill Friendly URLs

**Status**: Planned
**Priority**: P2 (Low)
**Estimated**: 1.5 days
**Scope**: Fullstack
**Dependencies**:
  - [Chat History](../v6.0.0/chat-history.md) — sessions need stable identity before URLs are worth sharing
  - [Skills Data Model](../v6.0.0/implemented/skills-data-model.md) — `slug` field added to `SkillConfig`
  - [Local Dev CLI](local-dev-cli.md) — `aitana skill` commands gain slug-aware forms
**Created**: 2026-04-23
**Last Updated**: 2026-04-23

## Problem Statement

Current skill chat URLs expose raw Firestore document IDs:

```
/chat/63f6d5b2-a206-4ee7-9571-bfa4e7a9ed60
```

**Current State:**
- URLs are unshareable in practice — no human can read, type, or remember a UUID
- The marketplace page links to UUIDs; any link shared externally is indistinguishable from a broken link
- Skill settings has no "your public URL" field — users have no canonical identity for their skill
- `aitana skill list` shows UUIDs; developers cannot tell skills apart at a glance

**Impact:**
- Blocks organic sharing — a user cannot send a colleague a meaningful link to a skill
- Makes CLI workflows fragile — scripts must hard-code UUIDs instead of stable names
- Undermines the "skills as first-class products" narrative: real products have real URLs

## Goals

**Primary Goal:** Give every skill a human-readable, owner-scoped URL (`/chat/@mark/general-assistant`) that is stable, shareable, and backward-compatible with existing UUID links.

**Success Metrics:**
- Slug auto-generated from skill name on create; zero manual setup required for the common case
- UUID URLs return 301 → slug URL when a slug exists; no broken links
- `aitana skill get @mark/general-assistant` resolves in <100ms (single Firestore index read)
- Slug uniqueness enforced per owner at the API layer; collision auto-resolved with suffix

**Non-Goals:**
- Vanity domains (`myskill.yourcompany.com`) — separate DNS/infra work, out of scope
- Organisation-level namespaces (`/chat/@acme/general-assistant`) — depends on org-accounts sprint (v6.2.0)
- Slug history / redirect chains on rename — UUID is the permanent link; slugs are display aliases
- Global slug reservation / squatting protection — not needed at current scale
- Real-time slug availability check in the UI (debounced API call on blur is sufficient)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Slug resolution is a single Firestore index read (<10ms); no impact on streaming latency |
| 2 | EARNED TRUST | 0 | No factual claims or AI-generated content involved |
| 3 | SKILLS, NOT FEATURES | +1 | Makes skills discoverable by name; users can predict a skill's URL without opening the platform |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model calls; slug generation is deterministic string manipulation |
| 5 | GRACEFUL DEGRADATION | +1 | UUID URLs continue to work permanently; slug lookup failure falls through to UUID path |
| 6 | PROTOCOL OVER CUSTOM | 0 | Standard web URL pattern (`@owner/slug`); no novel protocol invented |
| 7 | API FIRST | +1 | `GET /api/skills/by-slug/{owner_id}/{slug}` serves web, CLI, and future channel deep-links equally |
| 8 | OBSERVABLE BY DEFAULT | 0 | Covered by existing request tracing; no new observability surface |
| 9 | SECURE BY CONSTRUCTION | 0 | Slug lookup goes through the same `AccessContext.can_access_skill()` check as UUID lookup |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Resolution and 301 happen server-side; frontend receives the canonical UUID and uses it for all SSE calls |
| | **Net Score** | **+4** | Threshold: >= +4 ✓ |

**Conflict Justifications:** None.

## Design

### Overview

Add a `slug` field to `SkillConfig` (auto-generated from `name` on create, owner-editable). A new backend endpoint resolves `(owner_id, slug)` → `skill_id` via a Firestore composite index. The Next.js route is extended to a catch-all that handles both UUID and `@owner/slug` forms; UUID routes with a slug defined 301 to the friendly form. The canonical internal ID remains UUID throughout — slugs are URL aliases, not keys.

### Slug Format

- Kebab-case, lowercase, alphanumeric + hyphens, 3–60 chars
- Auto-generated: `"General Assistant"` → `general-assistant`
- Collision within owner namespace: append `-2`, `-3`, etc.
- Validation regex: `^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$`
- Reserved words blocked: `new`, `settings`, `marketplace`, `me`

### Interim vs. Full Form

| Phase | URL shape | When |
|-------|-----------|------|
| **Interim** (this sprint) | `/chat/@{owner_id}/{slug}` — owner_id is the Firebase UID | Ships now; no user-profile handles needed |
| **Full** (user-profiles sprint) | `/chat/@{handle}/{slug}` — handle set in profile | Migrated transparently when profiles land |

The interim uses Firebase UID in the URL (`@uid/slug`) which is not pretty but is correct and stable. The profile sprint replaces it with a display handle without breaking existing links.

### Frontend Changes

**Modified Route:**
- `src/app/chat/[skillId]/page.tsx` → `src/app/chat/[...path]/page.tsx`
- Catches `["uuid"]` (existing), `["@owner_id", "slug"]` (new)
- On slug form: calls `GET /api/proxy/api/skills/by-slug/{owner_id}/{slug}`, gets `skillId`, uses that for SSE
- On UUID form: if skill has a `slug`, issues `router.replace` to friendly URL

**Modified Components:**
- Skill settings page — add "URL" field showing `yourcompany.com/chat/@{ownerId}/{slug}` with edit button
- Marketplace skill cards — link to friendly URL if slug present, UUID otherwise

**New Hook:**
- `useSlugResolution(path: string[])` — resolves `[owner, slug]` to `skillId` via the proxy; returns `{ skillId, loading, error }`

### Backend Changes

**`SkillConfig` model** (`backend/db/models/__init__.py`):
```python
slug: str | None = Field(default=None, alias="slug")
```

**New endpoint:**
```
GET /api/skills/by-slug/{owner_id}/{slug}
→ SkillResponse (same shape as GET /api/skills/{skill_id})
```
Looks up via Firestore composite index `(ownerId, slug)`. Applies same visibility check as the UUID endpoint.

**Modified endpoints:**
- `POST /api/skills` — auto-generate slug from `name` if not provided; enforce uniqueness within `ownerId`
- `PUT /api/skills/{skill_id}` — validate new slug uniqueness on update; 409 on collision with suffix suggestion
- `GET /api/skills/{skill_id}` — add 301 hint in response header `Location: /chat/@{ownerId}/{slug}` when slug exists (for UUID-based requests from non-browser clients)

**Slug generation utility** (`backend/skills/slugify.py`, ~40 LOC):
```python
def slugify(name: str) -> str: ...
def unique_slug(owner_id: str, base: str, db) -> str: ...  # adds -2, -3 suffix as needed
```

**Firestore index** (add to `firestore.indexes.json`):
```json
{ "collectionGroup": "skills", "fields": [
    { "fieldPath": "ownerId", "order": "ASCENDING" },
    { "fieldPath": "slug",    "order": "ASCENDING" }
]}
```

### CLI Surface

New commands under `aitana skill`:

```bash
aitana skill list                          # shows slug alongside UUID (existing command, enhanced)
aitana skill get @{owner_id}/{slug}        # resolve by slug, print SkillResponse JSON
aitana skill set-slug {skill_id} {slug}   # set/update slug for a skill
```

`aitana skill get` already accepts a UUID; extend to accept `@owner/slug` form by detecting the `@` prefix and routing to the `by-slug` endpoint.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| GET | `/api/skills/by-slug/{owner_id}/{slug}` | Resolve slug → skill | No (new) |
| POST | `/api/skills` | Auto-sets `slug` from `name` | No (additive) |
| PUT | `/api/skills/{skill_id}` | Validates slug uniqueness | No (additive) |

### Architecture

```
Browser: /chat/@uid/general-assistant
         ↓
Next.js [...path] route
         ↓ GET /api/proxy/api/skills/by-slug/{uid}/general-assistant
         ↓
Backend: Firestore index (ownerId, slug) → skillId
         ↓
Frontend uses skillId for SSE stream (UUID throughout internally)

Browser: /chat/63f6d5b2-...  (old UUID URL)
         ↓
Next.js [...path] route → skill has slug?
         ↓ yes
router.replace('/chat/@uid/general-assistant')  [client-side 301]
```

## Implementation Plan

### Phase 1: Data model + backend (~0.5d)

- [ ] Add `slug: str | None` to `SkillConfig`; add Firestore composite index (~20 LOC)
- [ ] `backend/skills/slugify.py` — `slugify()` + `unique_slug()` with collision handling (~40 LOC + 10 tests)
- [ ] Auto-generate slug on `POST /api/skills` if not provided (~15 LOC)
- [ ] Validate uniqueness on `PUT /api/skills/{id}` (~20 LOC)
- [ ] `GET /api/skills/by-slug/{owner_id}/{slug}` endpoint (~30 LOC + 5 tests)

### Phase 2: Frontend routing (~0.5d)

- [ ] Rename `[skillId]` → `[...path]` route; add slug-resolution branch (~50 LOC)
- [ ] `useSlugResolution` hook (~40 LOC + 6 tests)
- [ ] UUID route: client-side redirect to friendly URL when slug present (~15 LOC)
- [ ] Marketplace cards: link to friendly URL if slug present (~10 LOC)

### Phase 3: Settings UI + CLI (~0.5d)

- [ ] Skill settings — add URL display + slug edit field with uniqueness validation on blur (~60 LOC)
- [ ] `aitana skill get @owner/slug` — slug-aware form of existing command (~30 LOC + 3 tests)
- [ ] `aitana skill set-slug` subcommand (~25 LOC + 2 tests)
- [ ] `aitana skill list` — show slug column (~10 LOC)

## Migration & Rollout

**Database:** Add `slug` field to `SkillConfig`; no backfill required. Existing skills get no slug initially — UUID URLs keep working. A one-off script can bulk-generate slugs for existing skills when desired (not blocking).

**Feature Flags:** None needed — additive only. UUID URLs remain primary until slug is set.

**Rollback Plan:** Drop the `by-slug` endpoint and remove the `[...path]` catch-all; revert to `[skillId]`. No data migration needed on rollback — the `slug` field is optional and ignored by old code.

**Environment Variables:** None.

## Testing Strategy

### Backend Tests (pytest)
- [ ] `test_slugify.py` — `slugify()` edge cases (special chars, unicode, reserved words, length truncation)
- [ ] `test_unique_slug.py` — collision suffix generation (`-2`, `-3`)
- [ ] `test_skills_routes.py` — `GET /by-slug/` returns correct skill; 404 for missing; respects access control
- [ ] `test_skills_routes.py` — `POST /api/skills` auto-sets slug; `PUT` validates uniqueness

### Frontend Tests (Vitest + RTL)
- [ ] `useSlugResolution.test.ts` — resolves slug, handles 404, aborts on unmount
- [ ] `ChatPage.test.tsx` — UUID path renders correctly; slug path resolves then renders
- [ ] `SkillSettings.test.tsx` — slug edit field validates format; shows 409 error on collision

### Manual Testing
- [ ] Navigate to `/chat/@{uid}/general-assistant` → chat loads
- [ ] Navigate to `/chat/{uuid}` for skill with slug → redirects to friendly URL
- [ ] Edit slug to collision → API returns suggestion; UI shows it
- [ ] `aitana skill get @{uid}/general-assistant` → prints skill JSON

## Security Considerations

- Slug lookup applies the same `AccessContext.can_access_skill()` check as the UUID endpoint — private skills return 404 even if slug is guessed
- Slug values are validated against the regex before storage; no injection surface
- `@owner_id` in the URL is the Firebase UID, not user-controlled — no spoofing risk at the URL layer (the backend authoritative check is on the returned skill's `ownerId`)
- Reserved slug words (`new`, `settings`, etc.) blocked to prevent route shadowing

## Performance Considerations

- Slug resolution: single Firestore document read via composite index, <10ms
- No impact on SSE streaming latency — UUID used for all streaming after one-time resolution
- No bundle size increase — `useSlugResolution` is a thin fetch hook (~40 LOC)
- Firestore index: low cardinality (one per skill), negligible cost

## Success Criteria

- [ ] All backend tests passing (`cd backend && pytest tests/`)
- [ ] All frontend tests passing (`npm run test:run`)
- [ ] Lint and typecheck clean (`npm run quality:check:fast`)
- [ ] `/chat/@{uid}/general-assistant` loads the correct skill chat
- [ ] `/chat/{uuid}` for slug-bearing skill redirects to friendly URL
- [ ] Slug auto-generated on new skill create; editable in settings
- [ ] `aitana skill get @{uid}/{slug}` resolves correctly
- [ ] Private skill at friendly URL returns 404 to non-owner

## Open Questions

- **Interim `@uid` ugliness**: Firebase UIDs in URLs are 28-char strings — not pretty. Accept as interim until user-profiles sprint, or generate a short opaque handle immediately? Recommendation: accept interim; don't add a new field (handle) just for this sprint.
- **Slug on fork**: forking a skill — auto-append `-fork` or prompt? Recommendation: auto-append, same as collision resolution.
- **Org ownership**: if skills move to org ownership in v6.2.0, whose namespace does the slug live in? Flag for org-accounts design doc.

## Related Documents

- [Skills Data Model](../v6.0.0/implemented/skills-data-model.md)
- [Chat History](../v6.0.0/chat-history.md) — gating dependency
- [Local Dev CLI](local-dev-cli.md) — CLI surface for `aitana skill get @owner/slug`
- [Frontend Architecture](../v6.0.0/implemented/frontend-architecture.md)
- [SEQUENCE.md](../v6.0.0/SEQUENCE.md) — this is a v6.1.0 item, after 1B.3
