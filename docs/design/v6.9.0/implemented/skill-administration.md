# Skill Administration

**Status**: Implemented
**Priority**: P1
**Estimated**: 4 days
**Scope**: Fullstack
**Dependencies**: [administration-overview.md](administration-overview.md) (shared admin identity + audit, Phase 1), 6.0.0 auth-and-permissions ✅, 6.0.0 resource-access-control ✅, 6.6.0 fork-convergence ✅
**Created**: 2026-07-14
**Last Updated**: 2026-07-16

## Problem Statement

The skill data model is complete — a 5-type `AccessControl`
(`backend/db/models/access.py:25-36`), slugs, tags, protocols, welcome/shell —
and the PUT contract already accepts most of it (`UpdateSkillRequest`,
`backend/skills/routes.py:70-87`). But the **management surfaces don't reach the
model**: publishing, team-sharing, or standing up a durable skill all fall back
to curl, a redeploy, or a raw Firestore edit.

**Current State (2026-07-14 admin audit — verified against code):**

- **Access control is unreachable from Skill Studio (the #1 gap).**
  `buildSaveBody` (`frontend/src/app/skills/studio/[skillId]/page.tsx:1051-1064`)
  sends only `description`/`instructions`/`displayName`/`skillMetadata`/
  `persona`/`welcome`. `accessControl` is *loaded* into the draft
  (`skillToDraft`, `:1042`) but never written back; `slug`, `tags`,
  `initialMessage`, `protocols`, `shell`, `references` are dropped. So setting a
  skill public/private/domain/specific/tagged needs `curl … PUT`, `skill push`
  with frontmatter, or a Firestore edit — though the backend PUT would accept
  every one of these fields.
- **Seeding a new durable skill needs a code change + deploy.** Platform skills
  are auto-discovered from disk templates at deploy time (`platform_seed.seed()`
  iterates `DEFAULT_TEMPLATES_ROOT`, `backend/admin/platform_seed.py:38,284`).
  There is no admin op to create a durable platform/tenant skill short of
  committing a `templates/<name>/SKILL.md` and redeploying. Live `skill push`
  only works against an **already-seeded** id (it GETs `/api/skills/{id}` first,
  `cli/aiplatform/commands/skill.py:549`).
- **Platform-skill edits drift silently.** An `aitana-admin` may PUT-edit a
  platform skill (`routes.py:275-280`), but the next deploy's seed **refresh**
  overwrites every template-tracked field (`description`/`instructions`/
  `skillMetadata`/`welcome`/`shell`/`accessControl`/`initialMessage`/
  `displayName`/`tags`/`avatar`, `platform_seed.py:315-333`) — the route even
  warns of this (`routes.py:271-274`). Live edits and the `SKILL.md` source
  diverge with no reconciliation; `skill diff`/`push`/`pull`
  (`cli/aiplatform/commands/skill.py:527-627`) are the only drift tools.
- **`featured` + `usage_count` are dead.** `featured` has no writer except dev
  fixtures (`backend/db/local_fixture.py`), default `False`
  (`db/models/__init__.py:359`). `increment_usage` (`skill_config.py:232`) has
  **zero non-test callers**, so the marketplace's `order by usageCount DESC`
  (`skill_config.py:220-229`) ranks every skill at 0 — ordering is unfulfilled.
- **Model control is split across three writers, two schemas.** Studio's
  `ModelTierPicker` writes a tier to `skillMetadata.model` (`page.tsx:399,805-889`);
  the CLI `skill set` writes the same field (`skill.py:407-432`); the older
  `/skill/[skillId]/settings` `ModelSelector` writes a **raw `api_name`** into
  local state only (no PUT anywhere in
  `frontend/src/app/skill/[skillId]/settings/page.tsx`) — an orphan that never persists.
- **Skill admin authz is fragmented.** Edit = owner OR `one-admin`
  (`is_skill_admin`, `access_context.py:79-86`, `SKILL_ADMIN_TAG="one-admin":34`);
  platform-skill edit = `aitana-admin` (`_PLATFORM_ADMIN_TAGS`, `routes.py:38`);
  list-all = `{one-admin, aitana-admin}` (`_SKILL_ADMIN_TAGS`, `routes.py:33`);
  seeding = SA-allowlist OR `aitana-admin` (`backend/admin/auth.py`). Four
  overlapping notions that can diverge.

**Impact:** Publishing, sharing, or standing up a skill needs gcloud + repo
access. Non-engineers cannot operate skills; drift silently reverts admin edits;
marketplace ranking is inert.

## Goals

**Primary Goal:** Make the full skill lifecycle — create (durable), edit,
publish, set access, feature — operable from the Studio UI and CLI over the
existing authed skill API, with no curl / redeploy / Firestore edit for the
common cases, and template↔live drift made explicit rather than silent.

**Success Metrics:**
- Setting a skill public/private/domain/specific/tagged is a Studio action (0 curl/PUT).
- Creating a durable platform/tenant skill needs 0 code changes and 0 redeploys.
- Marketplace `order by usageCount`/`featured` returns a real ordering (both fields have live writers) — or both are removed.
- One skill-admin role model; the `one-admin`/`aitana-admin`/SA-allowlist split reconciled to the umbrella's identity.

**Non-Goals:**
- A new authz model — the 5-type `AccessControl` + tags stays; we add *management*.
- Owning the disk-template pipeline — `SKILL.md` stays the source of truth for template-managed skills; we add a *durable, Firestore-authoritative* class alongside it.
- Group-tag *grant/revoke* itself — that is 9.3 (this doc consumes tags in the access editor's tag picker).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | Admin surface, off the chat latency path. `usage_count` increment is fire-and-forget so it never blocks a turn. |
| 2 | EARNED TRUST | +1 | Template↔live drift becomes visible (diff indicator) instead of a silent re-seed overwrite; access decisions inspectable in the editor. |
| 3 | SKILLS, NOT FEATURES | +1 | The core axiom here — a non-engineer publishes/shares/creates a skill in the Studio in <60s, no code or curl. |
| 4 | RIGHT MODEL, RIGHT MOMENT | +1 | Consolidates three model-writers to the tier picker + `skillMetadata.model`; retires the raw-`api_name` surface that bypasses tier→registry resolution. |
| 5 | GRACEFUL DEGRADATION | +1 | `managed_by` marker stops the seeder clobbering live edits; referential validation of access tags/emails/domain prevents silent hidden-skill states. |
| 6 | PROTOCOL OVER CUSTOM | +1 | Folds curl/PUT/frontmatter/Firestore writers behind the one authed `/api/skills` contract; keeps the Agent-Skills + `AccessControl` schema unchanged. |
| 7 | API FIRST | +1 | Studio, CLI, and seeder all drive the same PUT/POST endpoints; access editing becomes an API capability, not a UI-only hack. |
| 8 | OBSERVABLE BY DEFAULT | +1 | Publish/access/feature mutations write the umbrella's audit record; drift observable via the diff endpoint, inside GCP. |
| 9 | SECURE BY CONSTRUCTION | +1 | Unifies skill-admin authz behind `aitana-admin` + ownership + `tenant-admin:{domain}`; access edits pass the same PUT gate; deny-by-default; no content egress. |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Access/publish/feature logic stays in the PUT handler; Studio only renders the `AccessControl` editor and posts the body. |
| | **Net Score** | **+8** | Threshold: >= +4 ✅ |

**Conflict Justifications:** None (no axiom scored -1).

## Design

### Overview

Close the Studio→model gap by widening `buildSaveBody` and adding an
`AccessControlEditor`; add a durable, Firestore-authoritative skill class
(create without a template); mark each skill `managed_by` so the seeder and the
UI agree on who owns which fields; wire (or drop) `featured`/`usage_count`;
collapse model control to one writer; and align skill-admin authz to the
umbrella's identity.

### Frontend Changes

**New Components:**
- `src/components/studio/AccessControlEditor.tsx` — segmented control for the 5
  `AccessControl.type`s: `domain` → domain input (prefilled from the user's
  domain); `specific` → email chips; `tagged` → tag multi-select fed by 9.3's
  tag registry (`GET /api/admin/group-tags`, degrading to free text if absent).
  Per NEVER-SILENT, 409 `slug_taken` and 403 render inline.

**Modified Components:**
- `frontend/src/app/skills/studio/[skillId]/page.tsx` — widen `buildSaveBody`
  (`:1051`) to include `accessControl`/`slug`/`tags`/`initialMessage`/
  `protocols`/`shell`/`references`; mount `AccessControlEditor` + slug/tags
  fields; surface PUT `409 {error:"slug_taken", suggestion}` (`routes.py:286-293`);
  show a "Featured" toggle only for `aitana-admin`. When `managed_by == "template"`,
  render template-tracked fields read-only with an "edit the SKILL.md + `skill
  push`" hint + a live-vs-template drift badge.
- **Remove** `frontend/src/app/skill/[skillId]/settings/page.tsx` +
  `components/skill/ModelSelector.tsx` (orphan raw-`api_name` surface); redirect
  the route into the Studio, whose `ModelTierPicker` becomes the single UI model writer.

### Backend Changes

**New Endpoint:**
- `POST /api/admin/skills` — create a **durable** platform- or tenant-owned
  skill in Firestore directly (no disk template, no redeploy). Body ≈
  `CreateSkillRequest` + `ownerId` override (`aitana-platform` for platform,
  the tenant owner uid for tenant). Gated by the umbrella admin guard; sets
  `managed_by = "firestore"` so the seeder never touches it.

**Modified Endpoints / Modules:**
- `UpdateSkillRequest` (`backend/skills/routes.py:70-87`) — add `shell` and
  `featured` (the latter accepted only when the caller is `aitana-admin`; a
  non-admin passing it is a 403, not a silent drop).
- `platform_seed._existing_platform_skill_by_name` refresh loop
  (`platform_seed.py:305-352`) — skip any skill whose `managed_by == "firestore"`
  or that carries a `template_locked == false` marker, so admin-created/live-edited
  skills aren't overwritten on the next deploy. Template-managed skills keep
  today's git-is-truth refresh.
- `skill_config.increment_usage` (`skill_config.py:232`) — call it (best-effort,
  fire-and-forget) from the skill-invocation path so marketplace ordering
  (`list_marketplace`, `:220-229`) becomes real. **Decision (OQ2):** wire both;
  if product declines featured curation, drop `featured` from the model + response
  instead of leaving it inert.
- Replace `_SKILL_ADMIN_TAGS`/`_PLATFORM_ADMIN_TAGS` (`routes.py:33,38`) and
  `SKILL_ADMIN_TAG` (`access_context.py:34`) with the umbrella's shared
  `admin_roles` guard: `aitana-admin` = platform super-admin (edit platform
  skills, feature, seed), `tenant-admin:{domain}` = manage tenant-owned skills,
  plus skill ownership. `one-admin` folded into `aitana-admin` + ownership
  (umbrella OQ2).

**Data Model Changes:**
- `SkillConfig` (`backend/db/models/__init__.py:330`) — add
  `managed_by: Literal["template","firestore"] = "template"` (additive,
  defaulted; legacy docs round-trip). Seeder stamps `"template"` on create;
  `POST /api/admin/skills` stamps `"firestore"`.

### API Changes

| Method | Endpoint | Description | Breaking? |
|--------|----------|-------------|-----------|
| PUT | `/api/skills/{id}` | Now reachable from Studio for `accessControl`/`slug`/`tags`/`initialMessage`/`protocols`/`shell`/`references`; `+shell`,`+featured`(admin) accepted | No — additive |
| POST | `/api/admin/skills` | Create a durable Firestore-authoritative platform/tenant skill | No — new |
| GET | `/api/skills/{id}/template-diff` | Live↔`SKILL.md` drift for the Studio badge (reuses `skill diff` logic) | No — new |

### Architecture Diagram

```
[Admin] → [Studio AccessControlEditor] → [/api/proxy] → [PUT /api/skills/{id}]
                                                              ↓ admin_roles guard + audit
                                                        [skill_config.update_skill]
                                                              ↓
[Admin] → [POST /api/admin/skills] → durable skill (managed_by="firestore") → [Firestore]
                                                              ↑ seeder skips it (no clobber)
[Deploy] → [platform_seed.seed()] → refreshes managed_by="template" only
```

## Implementation Plan

### Phase 1: Close the Studio access gap (~1.5d)
- [ ] `AccessControlEditor.tsx` (5-type + tag/email/domain inputs) (~180 LOC)
- [ ] Widen `buildSaveBody`; mount editor + slug/tags; surface 409/403 (~120 LOC)
- [ ] Add `shell` to `UpdateSkillRequest`; test the full round-trip (~40 LOC)

### Phase 2: Durable create + drift reconciliation (~1.5d)
- [ ] `managed_by` on `SkillConfig`; seeder skips `"firestore"` (~60 LOC)
- [ ] `POST /api/admin/skills` behind the umbrella guard + audit (~90 LOC)
- [ ] `GET /api/skills/{id}/template-diff` + Studio drift badge (~110 LOC)

### Phase 3: Model consolidation + marketplace + authz (~1d)
- [ ] Remove `/skill/[skillId]/settings` + `ModelSelector`; redirect to Studio (~40 LOC)
- [ ] Wire `increment_usage` (fire-and-forget) + admin `featured` toggle (~70 LOC)
- [ ] Swap skill-admin tags for the shared `admin_roles` guard (~80 LOC)

## Migration & Rollout

- **Migrations:** `managed_by` is additive/defaulted (`"template"`); existing
  docs read unchanged. A one-off backfill stamps seeded skills `"template"` and
  hand-created ones `"firestore"`.
- **Feature flag:** the editor + durable-create UI ride behind the existing
  `NEXT_PUBLIC_ENABLE_SKILL_STUDIO`; `POST /api/admin/skills` is backend-gated regardless.
- **Rollback:** all additive — reverting the frontend restores the narrow
  `buildSaveBody` (PUT stays compatible). The seeder change is the only
  behavioral shift, guarded by the re-seed test below.
- **Env vars:** none new (`ADMIN_SEED_ALLOWED_SAS` retained for Cloud Build).

## Testing Strategy

**Frontend (Vitest + RTL):** `AccessControlEditor` edits all 5 types + degrades
to free text; `buildSaveBody` includes the seven newly-exposed fields; 409/403
render inline (NEVER-SILENT).

**Backend (pytest):** PUT round-trips each new field, non-admin `featured`/
platform-edit → 403; `POST /api/admin/skills` sets `managed_by="firestore"` and
denies non-admins; **a `managed_by="firestore"` skill survives a re-seed** (the
core regression guard); `increment_usage` bumps `usageCount` and marketplace
orders by it; `admin_roles` admits owner/`aitana-admin`/`tenant-admin:{domain}`.

**Manual:** set a skill `tagged` in Studio → tag-holder sees it, non-holder 404s;
create a durable skill, edit live, redeploy → edit survives.

## Security Considerations

- Every access/publish/feature/create mutation flows through the umbrella's
  unified admin guard (deny-by-default); `tenant-admin:{domain}` is scoped to its
  own tenant (no cross-tenant reach). `POST /api/admin/skills` sets `ownerId`
  server-side, never from the body (as `create_skill`, `routes.py:133`).
- `→ public` is the highest-risk edit: per CLAUDE.md's security hard rule the
  editor requires explicit confirm, warning that public skills appear in the
  unauthenticated marketplace (`routes.py:211-215`) and the A2A agent card.
- Access edits change skill *visibility*, never expose document content; all
  admin data + the audit log stay inside the GCP project edge.

## Success Criteria

- [ ] All frontend tests passing (`npm run test:run`)
- [ ] All backend tests passing (`pytest tests/`)
- [ ] Lint + typecheck clean (`npm run quality:check:fast`; `make lint`)
- [ ] A skill's access type is settable from Studio with no curl/PUT
- [ ] A durable platform/tenant skill is creatable with no code change or redeploy
- [ ] A live-edited/durable skill survives a re-seed (no silent overwrite)
- [ ] Marketplace ordering reflects real `usageCount`/`featured` — or both are removed
- [ ] One skill-admin role model; `one-admin`/SA-allowlist/`aitana-admin` reconciled

## Open Questions

- OQ1: For durable admin-created skills, do we also snapshot a `SKILL.md` to git
  via `skill pull` for source-control, or keep them purely Firestore-authoritative?
  (Lean: Firestore-authoritative; `skill pull --out` remains available for backup.)
- OQ2: Wire `featured` **and** `usage_count`, or drop `featured` as unrequested
  curation and wire only usage? (Lean: wire usage now; make `featured` an
  `aitana-admin` toggle — decide with product before Phase 3.)
- OQ3: Should template-managed skills be editable in-place at all (with a
  drift-and-reconcile flow), or hard read-only in the UI pointing to `skill push`?
  (Lean: read-only template fields + drift badge; non-template fields like
  `featured`/`accessControl` stay live-editable.)

## Related Documents

- [administration-overview.md](administration-overview.md) (9.1 umbrella — shared admin identity + audit)
- [user-group-administration.md](user-group-administration.md) (9.3 — tag registry the access editor consumes)
- [auth-and-permissions.md](../v6.0.0/implemented/auth-and-permissions.md), [resource-access-control.md](../v6.0.0/implemented/resource-access-control.md)
- [fork-convergence.md](../v6.6.0/) (Studio whole-draft Save; `managed_by` complements the fork path)

---

## Implementation Report

**Completed**: 2026-07-16
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
