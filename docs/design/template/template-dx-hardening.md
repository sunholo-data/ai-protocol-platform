# Template Developer Experience Hardening

**Status**: ✅ All 5 items shipped in platform (audit + Sprint QUICK-WINS-3, 2026-06-05) — **template sync pending**.  
**Priority**: P1  
**Estimated**: 1d planned; all items done  
**Scope**: Backend + Frontend + Docs + Config  
**Dependencies**: None  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-05  
**Source items**: #10 #15 #17 #18 #24 (CPH Uni AIPLA upstream feedback)

## Implementation Status (audit 2026-06-05)

| Item | Status | Files |
|------|--------|-------|
| **#10** Anchored `/^join$/i` regex breaks on rebrand | ✅ Fixed | [frontend/src/app/group/__tests__/page.test.tsx:77,99,116,135](../../../frontend/src/app/group/__tests__/page.test.tsx#L77) — all 4 `getByRole(button, name)` matches use the unanchored `/join/i` pattern. Bilingual button text like "Tilslut / Join" still matches. |
| **#15** Skill-invoke endpoint not discoverable | ✅ Already documented | [`README.md:40-44`](../../../README.md) — OpenAPI JSON pointer, skill-invocation endpoint, `app_name=aitana_platform` constant gotcha |
| **#17** `/gcs_config` mount wired but unread | ✅ Removed | No backend references to `_CONFIG_FOLDER` or `/gcs_config` — the dead plumbing has been cleaned up |
| **#18** `frontend/Dockerfile` silently drops `NEXT_PUBLIC_*` ARGs | ✅ Shipped 2026-06-05 | Two-part fix: (a) [`frontend/Dockerfile:9-21`](../../../frontend/Dockerfile#L9) has a prominent warning comment with the 3-step "when adding a new NEXT_PUBLIC_ variable" checklist; (b) [`get-firebase-config.sh:15-37`](../../../get-firebase-config.sh#L15) diffs `.env.local` NEXT_PUBLIC_* keys against the Dockerfile's ARG declarations at Cloud Build time and **fails the build loudly** with the missing-ARG list — silent-drop bugs surface here instead of as `undefined`-at-runtime in production. |
| **#24** Vendored protocol specs as project-local skill | ✅ Shipped | [.claude/skills/agent-protocols/](../../../.claude/skills/agent-protocols/) — SKILL.md + `references/` (10 specs) + `scripts/refresh-specs.sh` (quarterly refresh). Documented in CLAUDE.md project-skills section. |

## Problem Statement

Five DX gaps compound each other: a new fork author spends a working day learning things
the template should have documented or prevented.

**Item #10 — Anchored regex matchers in tests**

`frontend/src/app/group/__tests__/page.test.tsx` line 183 uses `/^join$/i` to find the
"Join" button. Any rebrand of the button text (AIPLA used "Tilslut / Join") breaks this
test with an opaque `Unable to find an accessible element with the role "button" and name
matching /^join$/i`. The error names the regex, not the missing element, making it
non-obvious that the rebrand is the cause.

**Item #15 — Skill-invoke endpoint not discoverable**

A first-time fork author working from the README, WORKSHOP.md, and CLAUDE.md cannot
determine the correct endpoint for invoking a skill. They must read source. The AIPLA
sprint spent real time guessing paths (`/api/skills/<name>/invoke`,
`/v6/skill/.../stream`) before finding `POST /api/skill/{skill_id}/stream`. Additionally:

- `/list-apps` returns filesystem subdirectory names, not the canonical `APP_NAME`
  (`aitana_platform`). Following standard ADK docs leads to `Agent not found: 'aitana_platform'`.
- The `aiplatform-cli` skill is referenced in CLAUDE.md but not in the template's `.claude/skills/`.
- OpenAPI (`/docs`, `/openapi.json`) is not mentioned as the primary discovery mechanism.

**Item #17 — `/gcs_config` volume mount wired but nothing reads it**

`backend/Dockerfile` sets `ENV _CONFIG_FOLDER=/gcs_config`. `cloudbuild.yaml` and
`backend/cloudbuild.yaml` both wire a GCS bucket as a read-only volume mount at `/gcs_config`.
Zero Python code reads `_CONFIG_FOLDER` or `/gcs_config`. The bucket is created, mounted,
never touched. Bootstrap scripts create `gs://<project>-config` because the template
requires it; nothing populates it; nothing reads it.

**Item #18 — `frontend/Dockerfile` silently drops undeclared `NEXT_PUBLIC_*` ARGs**

`frontend/Dockerfile` has a hardcoded list of `ARG NEXT_PUBLIC_*` declarations. Docker
silently ignores `--build-arg` values for ARGs not declared in the Dockerfile. AIPLA
added `NEXT_PUBLIC_AUTH_MODE=anonymous_group_id` to the `FIREBASE_ENV` secret and passed
it as `--build-arg`, but it was never declared in the Dockerfile — so Next.js saw
`undefined`, `process.env.NEXT_PUBLIC_AUTH_MODE` evaluated to `"anonymous_group_id"`
only at runtime (server-side) but to `undefined` at build time, causing the Sign-In
button to render despite being conditionally suppressed in source.

**Item #24 — No vendored protocol specs**

The template advertises a four-protocol stack (Agent Skills + AG-UI + A2UI + MCP/MCP Apps)
but ships no local reference for any of them. Every fork re-fetches from external sites or
relies on training-data memory. AIPLA hit spec-accuracy issues when writing the Boldkast
MCP App design doc: claims about CSP shape and postMessage envelope were informed by
stale training data.

**Impact:**

- #10: A CI failure that is non-obvious and blocks any fork that rebrands any button text.
- #15: Working day lost to endpoint discovery. Affects every new fork author.
- #17: Bootstrap time wasted creating and mounting a bucket that does nothing.
- #18: Silent wrong behavior in production; Google Sign-In button appears in a session
  configured for anonymous-group auth. Took user noticing the button to surface it.
- #24: Design docs written from stale memory; specification compliance is unverifiable
  without re-fetching external resources.

## Goals

**Primary Goal:** A fork author should be able to answer "where is the API?", "what
variables do I need?", and "what does the spec say?" without reading source code or
re-fetching external docs.

**Success Metrics:**
- Test matchers in the template use flexible patterns that survive button-text rebrand.
- README has a "Where does the API live?" section naming `/docs`, `/openapi.json`, and the canonical invoke path.
- `CLAUDE.md` references `/aitana-adk-testing` in the ADK Development section.
- `/gcs_config` volume mount is either removed or wired to something real.
- `frontend/Dockerfile` has a comment documenting the "declare every NEXT_PUBLIC_ ARG" requirement; optional: loop mechanism.
- Template ships an `agent-protocols` project skill with vendored specs.

**Non-Goals:**
- Automating Dockerfile ARG discovery (Docker limitation; documentation is the fix).
- Implementing GCS-backed skill templates (a legitimate feature, but out of scope).

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | |
| 2 | EARNED TRUST | +1 | Spec-accurate design docs; less reliance on stale memory |
| 3 | SKILLS, NOT FEATURES | 0 | |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | |
| 5 | GRACEFUL DEGRADATION | +1 | Flexible test matchers survive rebranding |
| 6 | PROTOCOL OVER CUSTOM | +1 | Vendored specs make spec compliance checkable |
| 7 | API FIRST | +1 | API discovery section in README |
| 8 | OBSERVABLE BY DEFAULT | +1 | Silent Dockerfile ARG drop → explicit documentation |
| 9 | SECURE BY CONSTRUCTION | 0 | |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+5** | Meets threshold |

## Design

### Item #10 — Flexible test matchers

**File:** `frontend/src/app/group/__tests__/page.test.tsx`

```ts
// Before (anchored — breaks on any button text change)
expect(screen.getByRole("button", { name: /^join$/i })).toBeInTheDocument();

// After (substring match — survives "Tilslut / Join", "Join Now", etc.)
expect(screen.getByRole("button", { name: /join/i })).toBeInTheDocument();
```

Audit the rest of the test file for other anchored matchers on user-facing strings.
General rule for the template: use substring matchers (`/join/i`) for user-visible text;
reserve anchored matchers (`/^join$/i`) for internal IDs or programmatic names that must
not drift.

Add a comment in the test file:
```ts
// Use substring matchers (/text/i not /^text$/i) for user-visible strings
// so this test survives button-text rebranding in downstream forks.
```

### Item #15 — API discoverability

**README.md — add "Where does the API live?" section:**

```markdown
## API Reference

The backend exposes a self-documenting API:

- **Interactive docs**: http://localhost:1956/docs (Swagger UI)
- **OpenAPI JSON**: http://localhost:1956/openapi.json — 74 routes, pipe to `jq '.paths | keys'`
- **Skill invocation** (production AG-UI streaming): `POST /api/skill/{skill_id}/stream`
- **Bare ADK routes** (dev only): `GET /run`, `GET /run_sse` — fail with `Agent not found`
  unless you set the correct `app_name` (see ADK quirk below)

**ADK `app_name` quirk:** The backend's canonical `app_name` is `aitana_platform`
(the `APP_NAME` constant in `backend/adk/agui.py`). The dev UI's `/list-apps` returns
filesystem subdirectory names — these do not match `aitana_platform`. Always use
`APP_NAME` in code; never use a path from `/list-apps` as an `app_name`.
```

**CLAUDE.md — add to "ADK Development" section:**

```markdown
**Endpoint discovery:** Run `curl http://localhost:1956/openapi.json | jq '.paths | keys'`
to see all 74 routes. Load the `aitana-adk-testing` skill (`/aitana-adk-testing`) for
curl recipes to inspect sessions, artifacts, and traces.
```

**CLAUDE.md — skill inventory audit:** Remove or annotate references to skills that
aren't in the template's `.claude/skills/` directory (`aiplatform-cli`,
`platform-deploy`, `cloud-run-diagnostics`). Either ship them in the template or say
explicitly "this skill lives only in the Aitana internal repo."

**`/list-apps` fix:** Return `[APP_NAME]` (the canonical constant) instead of listing
filesystem subdirectories. The filesystem layout is an internal detail:

```python
# backend/adk/list_apps_route.py (or wherever /list-apps is handled)
@router.get("/list-apps")
async def list_apps():
    return {"apps": [APP_NAME]}  # canonical, not filesystem-derived
```

### Item #17 — `/gcs_config` dead plumbing

**Two options; prefer Option A:**

**Option A: Remove the dead plumbing (recommended)**

```dockerfile
# Remove from backend/Dockerfile:
# ENV _CONFIG_FOLDER=/gcs_config

# Remove from cloudbuild.yaml and backend/cloudbuild.yaml:
# --add-volume name=gcs_config,type=cloud-storage,...
# --add-volume-mount volume=gcs_config,mount-path=/gcs_config
```

Also remove the `gs://${PROJECT_ID}-config` bucket creation from bootstrap scripts.

Add a note to the design doc history: "GCS config mount removed — nothing read it.
If you want runtime-swappable skill templates via GCS, file a feature request against
the template."

**Option B: Wire it to something real (follow-up feature)**

Let the seed step push template SKILL.md files into the bucket so they're swappable at
runtime without a redeploy. This is a legitimate feature for downstream forks that want
to add skills without rebuilding the image. Track as a separate design doc if there is
demand. Do not ship the mounting without the reading.

### Item #18 — `frontend/Dockerfile` ARG documentation

**Immediate fix (this PR):** Add a prominent comment and ensure every `NEXT_PUBLIC_*`
env var referenced in the codebase has a corresponding `ARG` + `ENV` pair:

```dockerfile
# frontend/Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT: Every NEXT_PUBLIC_* variable used in the Next.js app must be
# declared here as an ARG + ENV pair. Docker silently ignores --build-arg
# values for undeclared ARGs. The build will succeed, Next.js will see
# `undefined` at build time, and the feature will be wrong-but-running.
#
# When adding a new NEXT_PUBLIC_ variable:
#   1. Add it to this Dockerfile (ARG + ENV)
#   2. Add it to the FIREBASE_ENV secret format (see docs/ops/secrets.md)
#   3. Add it to cloudbuild.yaml's --build-arg list in get-firebase-config.sh
# ─────────────────────────────────────────────────────────────────────────────
ARG NEXT_PUBLIC_FIREBASE_API_KEY
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
# ... existing args ...
ARG NEXT_PUBLIC_AUTH_MODE          # add: controls auth flow (firebase | anonymous_group_id)
ARG NEXT_PUBLIC_MCP_SANDBOX_URL    # already declared; keep
```

**Future improvement (follow-up, not this PR):** Switch to a Docker `--secret` mount +
`set -a; source <(cat /run/secrets/env)` pattern so all `NEXT_PUBLIC_` vars flow through
without an explicit ARG list. Requires Docker BuildKit and a supported Cloud Build
executor. Design separately.

Add a `docs/ops/secrets.md` section (or extend existing) that documents the three-step
process above, with the "silent drop" behavior called out explicitly.

### Item #24 — `agent-protocols` project skill

Port the AIPLA fork's `.claude/skills/agent-protocols/` to the template. The skill:

- Vendors each spec under `references/` (~225 KB total):
  - `a2ui-v0.10-protocol.md` — A2UI spec
  - `ag-ui-events.md`, `ag-ui-architecture.md`, `ag-ui-tools.md` — AG-UI
  - `mcp-architecture.md` — MCP
  - `mcp-apps-spec-2026-01-26.md` — MCP Apps SEP-1865
  - `agent-skills-spec.md` — Agent Skills
- `SKILL.md` owns the disambiguation logic: decision table (when to use A2UI vs MCP Apps
  vs AG-UI vs A2A), common mistakes, and one-liner description of each spec file.
- `scripts/refresh-specs.sh` — re-fetch all specs from authoritative URLs; run quarterly.

**SKILL.md trigger keywords:** "which protocol", "A2UI or MCP", "what's the difference",
"ag-ui spec", "a2ui spec", "mcp apps spec", "agent skills spec".

Add to CLAUDE.md "Project Skills" section:
```markdown
- **`agent-protocols`** — Disambiguates the four-protocol stack (AG-UI / A2UI / MCP /
  MCP Apps / Agent Skills) with vendored offline specs. Load when writing design docs,
  implementing a new protocol surface, or verifying spec compliance. Run
  `.claude/skills/agent-protocols/scripts/refresh-specs.sh` quarterly.
```

## Implementation Plan

| Step | File(s) | Effort |
|------|---------|--------|
| 1 | Fix anchored matchers in `page.test.tsx` + audit for others (#10) | 1h |
| 2 | Add "Where does the API live?" to README + CLAUDE.md ADK section (#15) | 1h |
| 3 | Fix `/list-apps` to return `[APP_NAME]` (#15) | 0.5h |
| 4 | Audit CLAUDE.md skill references; annotate or remove unavailable skills (#15) | 0.5h |
| 5 | Remove `/gcs_config` dead plumbing from Dockerfile + cloudbuild.yaml (#17) | 1h |
| 6 | Add ARG documentation comment to `frontend/Dockerfile`; add missing ARGs (#18) | 1h |
| 7 | Write/update `docs/ops/secrets.md` with three-step NEXT_PUBLIC_ process (#18) | 0.5h |
| 8 | Port `agent-protocols` skill from AIPLA; add CLAUDE.md entry (#24) | 2h |

**Total: ~7.5h ≈ 1d**

## Testing Strategy

- **`page.test.tsx`** — verify flexible matcher passes after renaming the button label.
- **`test_list_apps.py`** — assert `/list-apps` returns `[APP_NAME]`, not filesystem paths.
- **Manual smoke:**
  - `curl /openapi.json | jq '.paths | keys'` — confirm route list appears.
  - `docker build --build-arg NEXT_PUBLIC_AUTH_MODE=anonymous_group_id` — confirm `process.env.NEXT_PUBLIC_AUTH_MODE` is set at build time.
  - Load `/agent-protocols` skill in a session; confirm spec references resolve.

## Success Criteria

- [ ] `/^join$/i` matcher replaced with `/join/i`; test passes after button-text rebrand.
- [ ] README has "Where does the API live?" section with `/docs`, `/openapi.json`, and canonical invoke path.
- [ ] `/list-apps` returns `["aitana_platform"]` (or the fork's `APP_NAME`).
- [ ] `frontend/Dockerfile` has the three-step comment; `NEXT_PUBLIC_AUTH_MODE` declared.
- [ ] `/gcs_config` volume mount removed from Dockerfile + both `cloudbuild.yaml` files.
- [ ] `agent-protocols` skill ships in `.claude/skills/agent-protocols/`; CLAUDE.md references it.
- [ ] All template tests pass.

## Related Documents

- [aitana-adk-testing skill](../../../.claude/skills/aitana-adk-testing/SKILL.md)
- [local-dev-cli.md](../../v6.1.0/local-dev-cli.md)
- [template-session-management.md](template-session-management.md) — `/list-apps` and `APP_NAME` relationship
- [SEQUENCE.md](SEQUENCE.md)
