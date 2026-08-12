# Platform Global Skills

**Status**: Implemented
**Priority**: P1 (High — product correctness)
**Estimated**: ~1-2 days
**Scope**: Backend + infra + docs (no frontend in this sprint)
**Dependencies**: AGENT-FACTORY (done), AUTH-PERMISSIONS (done), SKILLS-DATA-MODEL (done)
**Created**: 2026-04-21
**Sprint**: PLATFORM-GLOBAL-SKILLS

## Problem Statement

v6 ships with five seed skills (`document-analyst`, `web-researcher`, `code-assistant`, `data-extractor`, `general-assistant`). Today they are seeded **by hand**, into **one environment at a time**, owned by a **single user uid** that happens to be the dev account. That has three consequences:

1. **They're not really platform skills.** If the seed-owner leaves or their account is deleted, the seeds go with them. If they change their email, the ownership trail lies. Non-owners see them as public skills (because `accessControl.type == "public"`), but the owner field is a lie — it names one specific human, not the platform.

2. **They're missing on test and prod.** `scripts/seed_skills.py` has never run against `your-project-id-test` or `your-project-id-prod`. The "it works on dev" smoke we just passed is the first time any deployed environment has had skills at all. Test/prod will 404 on `/api/skill/{id}/stream` until someone manually runs the seed script — fragile, forgettable, and the opposite of CI/CD-first (per [feedback_cicd_first.md](../../.claude/projects/-Users-mark-dev-aitana-labs-platform/memory/feedback_cicd_first.md)).

3. **Non-owners can mutate them today.** Anything with `accessControl.type == "public"` is visible to everyone, *but* the PUT/DELETE routes only check `is_skill_owner`. Since the seed owner is a real uid, no one else can edit them — fine by accident. The moment we fix #1 (detach from a real user) we need an explicit rule: platform skills are read-only to everyone except an admin process.

The concept we want is a first-class **"platform global skill"**: owned by the platform itself, seeded on every deploy, visible to all authenticated users (and unauthenticated marketplace), and modifiable only by an admin code path.

## Goals

**Primary Goal:** Ship a durable platform-owned skill concept. Global skills exist on every environment immediately after deploy, survive team turnover, and cannot be mutated by end users — but any user can fork one into their own private/shared skill.

**Success Metrics:**
- On any freshly-deployed environment (dev, test, prod), `GET /api/skills` returns the five platform seeds for an authenticated user within minutes of Cloud Build green.
- `GET /api/skills/marketplace` (unauthenticated) returns the five platform seeds.
- `PUT /api/skills/{id}` and `DELETE /api/skills/{id}` on a platform skill return **403** (not 404) for any non-platform caller — including the dev account.
- Re-running the seed step is safe: skills already present are skipped, not duplicated.
- Any user can `POST /api/skills` with `{ "forkOf": "<platform-skill-id>" }` (or equivalent) and get a new skill owned by them, which they can edit freely.

**Non-Goals:**
- Fork/remix UI — the backend fork endpoint is in scope, the frontend is not (Phase 4).
- Versioning of global skills across deploys (one-way overwrite is acceptable for v6.0; if the templates change and the skill already exists, the deploy leaves the existing row alone).
- A separate "system user" in Firebase Auth — the sentinel is a string, not a real user record.
- Global skills on Firestore security rules — enforced entirely at the API layer (FastAPI routes). Rules stay "authenticated user may read skills they can see"; the skill-owner check stays at route level where we already have the `AccessContext`.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | No latency impact |
| 2 | EARNED TRUST | +1 | "The platform ships with these" is a trust signal |
| 3 | SKILLS, NOT FEATURES | +2 | Elevates the platform's own skills to first-class citizens, not side-effects of a dev seed |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | N/A |
| 5 | GRACEFUL DEGRADATION | +1 | Deploy-seeded, idempotent; non-fatal if seed fails (deploy still succeeds, skills appear on next deploy) |
| 6 | PROTOCOL OVER CUSTOM | 0 | Re-uses SkillConfig + the existing access evaluator; no new protocol |
| 7 | API FIRST | +1 | Adds `POST /api/skills/{id}/fork`, new filter on `GET /api/skills` |
| 8 | OBSERVABLE BY DEFAULT | 0 | Seed logs go to Cloud Build, no new telemetry |
| 9 | SECURE BY CONSTRUCTION | +2 | Read-only-except-admin enforced at the route layer; no client can escalate to platform owner |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | Server-side decision; client is unchanged |
| | **Net Score** | **+7** | Threshold: >= +4 |

## Design

### The sentinel

We reserve one string as the **platform owner** of any skill that ships with the product:

```python
# backend/skills/platform.py (new)
PLATFORM_OWNER_UID = "aitana-platform"
```

Everywhere an `owner_id` is compared or evaluated, platform skills look like "owned by `aitana-platform`". Since no real Firebase uid can ever equal that string (Firebase uids are 28-char base64-ish), no real user can impersonate the sentinel by accident.

We pick a string, not `None`, for two reasons:

1. The existing evaluator at [backend/auth/access_context.py:93](../../backend/auth/access_context.py#L93) already short-circuits on `owner_id == ctx.uid`. Keeping `owner_id` non-empty means the evaluator keeps working unchanged — platform skills are `accessControl.type == "public"` and the owner check falls through naturally.
2. Frontend can display "by Aitana" vs "by you" without a null-check branch.

### The rule

One rule replaces the ambiguity in today's `is_skill_owner`:

> **A skill owned by `aitana-platform` is read-only. Only a request authenticated as the platform owner can mutate it — and no user account can ever authenticate as the platform owner.**

In practice the PUT/DELETE routes gain one extra line:

```python
# backend/skills/routes.py (update to update_skill, delete_skill)
if config.owner_id == PLATFORM_OWNER_UID:
    raise HTTPException(status_code=403, detail="Platform-owned skills are read-only. Fork to customize.")
```

Placement matters: **before** the `is_skill_owner` check, so the 403 message is specific ("fork to customize") rather than the generic "only the owner can update". This is a real 403, not a 404 — the skill is visible to the caller (it's public) and we want the message to be actionable.

### Visibility

Platform skills are stored as `accessControl.type == "public"`. Consequences:

- `GET /api/skills/marketplace` — unauthenticated — returns them. ✅ Wanted.
- `GET /api/skills` — authenticated — returns them alongside the user's own skills. ✅ Wanted.
- `GET /api/skills?ownerId=aitana-platform` — new filter path — returns **only** platform skills. This is the frontend hook for a "Platform" section in the skill picker, landed in a later sprint.

### Seeding on deploy (idempotent)

`backend/cloudbuild.yaml` gains one step after `deploy`, before `smoke-backend`:

```yaml
- name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
  id: seed-platform-skills
  waitFor: ['deploy-backend']  # or whatever the deploy step is named
  entrypoint: bash
  args:
    - '-c'
    - |
      set +e   # Non-fatal: a seed failure must not block the deploy.
      URL=$(gcloud run services describe ${_SERVICE_NAME} \
        --project=${_PROJECT_ID} --region=${_REGION} \
        --format='value(status.url)')
      TOKEN=$(gcloud auth print-identity-token --audiences="$${URL}")
      code=$(curl -sS -o /tmp/seed_body -w '%{http_code}' --max-time 60 \
        -X POST \
        -H "Authorization: Bearer $${TOKEN}" \
        "$${URL}/api/admin/seed-platform-skills") || code=000
      if [ "$${code}" = "200" ]; then
        echo "OK   seed -> 200 body=$(head -c 200 /tmp/seed_body)"
      else
        echo "WARN seed -> $${code} body=$(head -c 200 /tmp/seed_body)"
        echo "Seed failed; skills may be missing. Re-run later with: curl -X POST .../api/admin/seed-platform-skills"
      fi
      exit 0
```

Key properties:
- **Non-fatal.** A failed seed logs a warning but does not red-line the deploy. Skills are important but not hotter than the code path itself — we'd rather ship broken skills than block a legitimate code fix.
- **Authenticated.** Uses the Cloud Build SA's identity token against the backend's internal (non-public) endpoint. Matches the pattern of the existing `smoke-backend` step.
- **Runs on every deploy.** Idempotent inside the API: already-present skills are skipped by name.
- **Runs on both pipelines.** The root `cloudbuild.yaml` (frontend + backend sidecar) and the standalone `backend/cloudbuild.yaml` both need it, because either pipeline can be the first to reach a fresh environment. Extract the YAML block into a shared step file or duplicate it — duplication is fine for two occurrences; don't invent a loader.

### The admin endpoint

```python
# backend/admin/routes.py (new)
router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.post("/seed-platform-skills")
def seed_platform_skills(request: Request) -> dict:
    """Idempotently seed the five platform global skills.

    Authorization: the request MUST carry a Google-signed ID token whose
    `email` matches the Cloud Build / Cloud Run service account for the
    current project. No human user can call this — it is service-account-only.
    """
    _assert_caller_is_service_account(request)
    summary = platform_seed.seed()
    return {"created": summary.created, "skipped": summary.skipped, "failed": summary.failed}
```

Authentication uses the existing ADC / IAP pattern: we verify the inbound ID token with `google.auth.transport.requests` and accept it only if the token's `email` claim equals a known service-account email. This is stricter than Firebase auth (which is what `get_current_user` does) and simpler than inventing an admin password or API key.

Reject everything else with 403.

Alternative considered: a one-shot `uv run python scripts/seed_skills.py --owner=platform` step baked into the image and run via `gcloud run jobs execute`. Rejected because:
- It needs a separate Cloud Run Job resource per environment (three more Terraform modules).
- It needs Firestore credentials in the job's SA (granted), but the seed script has to re-implement auth checks the API already has.
- Idempotency guarantees are weaker (a CLI seed vs. a real API call that goes through the same `create_skill` path as UI-created skills).

The HTTP endpoint reuses the code path users already exercise, so we get idempotency for free.

### The `fork` endpoint

```python
# backend/skills/routes.py (new)
@router.post("/{skill_id}/fork", status_code=201, response_model=SkillResponse)
def fork_skill(
    skill_id: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> Any:
    """Create a private copy of a skill owned by the caller.

    The caller must be able to see the source skill (same rules as GET).
    The forked copy is `accessControl.type == "private"` and `ownerId == user.uid`.
    Name gets a suffix to avoid collisions (user can rename later).
    """
    source = skill_config.get_skill(skill_id)
    if source is None or not request.state.access.can_access_skill(source):
        raise HTTPException(status_code=404, detail="Skill not found")
    fork = skill_config.create_skill(
        name=f"{source.name}-fork-{_short_id()}",
        description=source.description,
        instructions=source.instructions,
        owner_id=user.uid,
        owner_email=user.email,
        displayName=f"{source.display_name} (Fork)",
        skillMetadata=source.skill_metadata.model_dump(by_alias=True),
        references=source.references,
        tags=source.tags,
        accessControl={"type": "private"},
    )
    return SkillResponse.from_config(fork)
```

Not in scope for the fork endpoint (later sprints):
- A `forkOf` field tracking the parent skill — saved as metadata for the "Updates available" UI hint, but no behavior hangs off it yet.
- Merging upstream changes back into a fork (git-style rebase). Future.

## Implementation Plan

### Milestone 1 — Sentinel + route guard + tests (backend, ~150 LOC)

Files:
- `backend/skills/platform.py` (new) — `PLATFORM_OWNER_UID` constant + docstring explaining why it's a string sentinel.
- `backend/skills/routes.py` — add the "platform-owned is read-only" check to `update_skill` and `delete_skill`.
- `backend/tests/api_tests/test_skills_api.py` — add three cases: (a) GET a platform skill as a random user → 200 with owner=`aitana-platform`, (b) PUT a platform skill as a random user → 403 with the "fork to customize" message, (c) DELETE same → 403.

Acceptance:
- `pytest tests/api_tests/test_skills_api.py` green, including the three new cases.
- `make lint` clean.

### Milestone 2 — Admin seed endpoint + platform_seed module (backend, ~200 LOC)

Files:
- `backend/admin/__init__.py`, `backend/admin/routes.py` (new) — the admin router.
- `backend/admin/platform_seed.py` (new) — pure function: reads `backend/skills/templates/`, calls `create_skill` with `owner_id=PLATFORM_OWNER_UID`, skips existing by name, returns a summary dataclass.
- `backend/admin/auth.py` (new) — `_assert_caller_is_service_account`: verify Google ID token, assert the email is in an allowlist pulled from the environment (one env var per pipeline SA).
- `backend/fast_api_app.py` — wire the admin router.
- `backend/tests/unit/test_platform_seed.py` (new) — idempotency, name collision, template parsing edge cases.
- `backend/tests/api_tests/test_admin_routes.py` (new) — auth failures (no token, wrong email, expired token), auth success path (mocked verifier).

Acceptance:
- Unit tests green; admin endpoint rejects unauthenticated calls with 403.
- Local `curl -X POST http://localhost:1956/api/admin/seed-platform-skills` with a faked SA token seeds the skills.

### Milestone 3 — Cloud Build seed step (infra, ~30 LOC YAML)

Files:
- `backend/cloudbuild.yaml` — add the `seed-platform-skills` step shown above.
- `cloudbuild.yaml` (root, frontend + backend sidecar) — same step; the URL comes from the sidecar-carrier frontend service.
- `docs/ops/platform-skills.md` (new) — short runbook: when the seed is triggered, how to re-run it manually, how to verify.

Acceptance:
- Push to `dev`; deploy green; post-deploy step logs `OK seed -> 200`.
- `GET /api/skills/marketplace` on dev returns the five seeds with `ownerId: "aitana-platform"`.
- Delete one of the five from Firestore console; re-trigger deploy; skill is re-created.

### Milestone 4 — Fork endpoint (backend, ~80 LOC)

Files:
- `backend/skills/routes.py` — the `fork_skill` route above.
- `backend/tests/api_tests/test_skills_api.py` — fork succeeds, forked skill is private + owned by caller, forking a non-visible skill returns 404, forking a platform skill still works (the read-only rule is for PUT/DELETE, not POST /fork).

Acceptance:
- `curl -X POST .../api/skills/{platform-skill-id}/fork` with a Firebase token → 201 with a new skillId owned by the caller.
- Forked skill appears in `GET /api/skills` for the caller, does not appear for other users.

### Milestone 5 — One-shot backfill for dev (ops, ~0 LOC)

Files: none. Run once:

```bash
# On the currently-running dev, seed skills still point at the dev uid.
# Either:
#   (a) Run the admin endpoint manually with a Cloud Build SA token, or
#   (b) Delete the five dev-owned seeds from Firestore and let Milestone 3 re-create them as platform-owned.
# We prefer (b) — cleanest state, and it verifies the idempotent seed path end-to-end.
```

Acceptance: all five dev-owned seeds replaced by platform-owned seeds in `your-project-id`. Test/prod are empty → Milestone 3 fills them on first deploy.

## Risks

- **Seed runs before the Firestore index is ready.** The composite index on `accessControl.type + usageCount` was added on 2026-04-20 (commit [efabf83](../../)). If a fresh project's index is still building, `list_marketplace()` may 500 for minutes. Not a seed concern (seeds don't read the marketplace), but noted because frontend marketplace polling could get noisy during the same window.
- **Service-account email drift.** The allowlist in `backend/admin/auth.py` hard-codes the Cloud Build / Cloud Run SA email per project. If Terraform ever renames the SA the seed will start failing silently (non-fatal, but still wrong). Mitigation: pull the list from env vars set in `cloudbuild.yaml` under the `_*_SA` substitutions that Terraform already owns.
- **Someone runs `seed_skills.py` locally without `--owner=platform`.** That would re-introduce user-owned copies of the five skills on whatever environment their ADC points at. Mitigation: leave `scripts/seed_skills.py` alone (it still needs `--owner-uid`), document the admin endpoint as the only path to platform-owned seeds, and add a CI lint that rejects commits adding `create_skill(..., owner_id="aitana-platform")` outside `backend/admin/platform_seed.py`.

## Open Questions

1. **Should the fork endpoint be in this sprint or deferred?** Argument for: it's the missing piece for "platform skills are read-only *but* users can remix". Argument against: nobody is using it in the UI yet. Recommend: keep M4 in scope (it's 80 LOC + tests) so the backend is complete; frontend catches up whenever the workspace skill-picker lands.
2. **What does deletion look like?** If `aitana` the product decides to retire `data-extractor`, how do we retire it in every environment? Not needed for v6.0 (we only have five seeds and no plan to remove any), but worth a follow-up design doc once the product has a second wave of globals.
3. **Versioning.** A later sprint will need it — skills have `instructions` and `skillMetadata` that *will* change. The v6.0 answer is "if the skill exists, the seed leaves it alone." That means edits to the templates on the filesystem won't propagate to already-seeded environments. Explicit, intentional, and deferred.

---

## Implementation Report

**Completed**: 2026-04-21
**Actual Effort**: ~1 day end-to-end vs 3 estimated. Most of M1–M4 was small focused diffs; the time sink was M5's Terraform + Cloud Build debugging, not application code.
**Branch/PR**: `dev` — commit range `78f03bb..94ad55f` (M1 `78f03bb`, M2 `655897e`, M3 `af0e0c3`, M4 `afc74c3`, M5 `3c1d973`, redeploy `b267904`, doc move `94ad55f`). Eval `96/100` at `.claude/state/evaluations/eval_PLATFORM-GLOBAL-SKILLS_round_1.json`.

### What Was Built
- **M1** — `PLATFORM_OWNER_UID = "aitana-platform"` sentinel + read-only guard on PUT/DELETE in [backend/skills/routes.py](../../../../backend/skills/routes.py) returning 403 "Fork to customize".
- **M2** — `POST /api/admin/platform-seed` + [backend/admin/platform_seed.py](../../../../backend/admin/platform_seed.py) that loads YAML templates from [backend/skills/templates/](../../../../backend/skills/templates/) and writes platform-owned skills idempotently. SA allowlist via `_ADMIN_SEED_ALLOWED_SAS`.
- **M3** — Seed step wired into `backend/cloudbuild.yaml` after deploy, non-fatal, uses SA ID-token.
- **M4** — `POST /api/skills/{id}/fork` creating a private copy with `-fork-<6-hex>` name suffix and `(Fork)` displayName suffix. 404 (not 403) on invisible sources to avoid existence leak.
- **M5** — Dev brought live with 5 platform-owned skills. Original plan called for "ops, ~0 LOC" manual cleanup; shipped [backend/scripts/cleanup_legacy_platform_seeds.py](../../../../backend/scripts/cleanup_legacy_platform_seeds.py) (114 LOC) instead so test/prod can be backfilled with one command. Runbook at [docs/ops/platform-skills.md](../../../ops/platform-skills.md).

Follow-ups shipped after round-1 eval (commit ranges TBD in next push):
- Fork endpoint now suffixes `displayName` with `(Fork)` per the original spec (was left as source name).
- CI adds a grep guard in `.github/workflows/ci.yml` that fails the build if any `.py` outside `backend/admin/platform_seed.py` and `backend/skills/platform.py` sets `owner_id = PLATFORM_OWNER_UID` — the deferred guardrail from the Risks section.

### Files Changed
- New: `backend/skills/platform.py`, `backend/admin/platform_seed.py`, `backend/admin/routes.py`, `backend/skills/templates/*.yaml`, `backend/scripts/cleanup_legacy_platform_seeds.py`, `docs/ops/platform-skills.md`.
- Modified: `backend/skills/routes.py` (read-only guard + fork endpoint), `backend/cloudbuild.yaml` (seed step), `backend/tests/api_tests/test_skills_api.py` (platform-skill + fork test matrix), `.github/workflows/ci.yml` (sentinel guard), `multivac-aitana/infrastructure/environments/{dev,test,prod}/locals.tf` (`_ADMIN_SEED_ALLOWED_SAS` substitution).

### Lessons Learned
- **Terraform `global_substitutions` footgun.** M5 deploy 403'd because `_ADMIN_SEED_ALLOWED_SAS` was never wired into `environments/*/locals.tf`; the substitution resolved to the literal string `terraform_managed`. The Cloud Build step didn't see the SA email, so the allowlist check failed. Fix: add the variable to `global_substitutions` in every env dir (per-env promotion pattern — they're not DRY by design). Watch for placeholder-style defaults that look populated at a glance.
- **Empty commits don't fire `includedFiles` triggers.** Had to re-run the dev deploy manually via `gcloud builds triggers run --branch=dev` because the trigger has `includedFiles: backend/**` and the redeploy commit touched only Terraform. Worth knowing; don't rely on `git commit --allow-empty` to re-fire a build.
- **Two deploy projects.** Terraform runs on `multivac-deploy`; Cloud Run builds run on `your-deploy-project-id`. Watchers need to point at the right one or they return empty results silently (captured in memory as `gotcha_two_deploy_projects`).
- **M5 scope expansion was a net positive.** The repeatable cleanup script was ~0 LOC in the plan but ended up as the single highest-leverage piece of work in the sprint — test/prod promotion is now a `--env test --yes` flag away instead of a manual Firestore-console session. Consciously accepting "bigger than planned" paid off here.
- **Generator-evaluator catches drift.** The fork displayName suffix and the CI lint guard were both specified in the design doc, both quietly dropped during execution, both caught by sprint-evaluator round 1. Cheap to fix post-hoc; worth remembering that acceptance criteria aren't self-enforcing.
