# Platform-owned skills — seed + ownership

Every tenant ships with a small set of default skills (doc extraction,
data extractor, web researcher, code assistant, general assistant). They
are owned by the **platform sentinel** — a non-Firebase pseudo-uid
`aitana-platform` — so:

- Every tenant sees them (via `accessControl.type = "public"`).
- Nobody can mutate them. PUT/DELETE on a platform skill returns
  **403 "Platform-owned skills are read-only. Fork to customize."**
- Users who want to customize one call `POST /api/skills/{id}/fork` to
  create a private owned copy (see M4).

This doc covers the operational side: how and when seeds run, what to
do when they don't, and how to verify.

## The pieces

| Piece | Location | Role |
|---|---|---|
| Sentinel | [backend/skills/platform.py](../../backend/skills/platform.py) | `PLATFORM_OWNER_UID = "aitana-platform"` |
| Read-only guard | [backend/skills/routes.py](../../backend/skills/routes.py) | 403 on PUT/DELETE of platform skills |
| Admin endpoint | [backend/admin/routes.py](../../backend/admin/routes.py) | `POST /api/admin/seed-platform-skills`, SA-gated |
| Seeder | [backend/admin/platform_seed.py](../../backend/admin/platform_seed.py) | Idempotent-by-name, reads `backend/skills/templates/` |
| SA auth | [backend/admin/auth.py](../../backend/admin/auth.py) | Verifies Google ID token + `ADMIN_SEED_ALLOWED_SAS` allowlist |
| Cloud Build step | [backend/cloudbuild.yaml](../../backend/cloudbuild.yaml), [cloudbuild.yaml](../../cloudbuild.yaml) | Non-fatal POST to the admin endpoint after smoke |
| Templates | [backend/skills/templates/](../../backend/skills/templates/) | One dir per skill, each with a YAML-frontmatter `SKILL.md` |

## When the seed runs

- **Automatic:** Every push to `dev`, `test`, or `prod` fires both Cloud
  Build pipelines (backend standalone + frontend-with-sidecar). Each one
  runs the `seed-platform-skills` step **after** its smoke step. The step
  is `set +e; exit 0` — a seed failure logs a warning but never fails
  the build. It is idempotent, so running it from both pipelines against
  the same Firestore is safe (the second run is 5 skipped, 0 created).
- **Manual re-run:** When you need to re-seed without a deploy (e.g., you
  cleaned Firestore for testing), see below.

## Verification

```bash
# List platform-owned skills on the dev backend
curl -s "https://platform-frontend-<dev-hash>-ew.a.run.app/api/proxy/api/skills/marketplace" \
  | jq '.[] | select(.ownerId == "aitana-platform") | {name, skillId}'
```

After a fresh seed you should see 5 entries. If you see 0, something went
wrong — check the build logs for the `seed-platform-skills` step. If you
see user-owned seeds (ownerId != "aitana-platform"), a previous deploy
ran the seeder under the wrong uid; see "Dev backfill" below.

## Manual re-run

Pick a pipeline to target (frontend sidecar recommended; it's the path
real users hit):

```bash
# 1. Find the target URL
PROJECT=your-project-id REGION=europe-west1 SERVICE=platform-frontend
URL=$(gcloud run services describe $SERVICE \
  --project=$PROJECT --region=$REGION \
  --format='value(status.url)')

# 2. Mint a Google-signed ID token for yourself (assumes your user is in
#    ADMIN_SEED_ALLOWED_SAS, or use a permitted SA via gcloud impersonation)
TOKEN=$(gcloud auth print-identity-token --audiences="$URL")

# 3. Trigger seed
curl -sS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$URL/api/proxy/api/admin/seed-platform-skills"
# → {"created":0,"skipped":5,"failed":[]}   on a re-run
```

### Allowlist env var

`ADMIN_SEED_ALLOWED_SAS` is a comma-separated list of SA emails. On
`your-project-id` this should include the Cloud Build SA used by
`trigger-deploy-platform-frontend` and the backend pipeline. Terraform
manages the value via the `_ADMIN_SEED_ALLOWED_SAS` cloudbuild
substitution.

If you get `403 Not authorized: X not in ADMIN_SEED_ALLOWED_SAS`, your
SA (or user email) is not in the allowlist — update the Cloud Run
service's env var (or the terraform-managed value) and redeploy.

## Backfill — deleting legacy (non-platform) copies of template names

If an environment was seeded **before** PLATFORM-GLOBAL-SKILLS M1 landed,
the five template skills exist twice: once as the real platform-owned
rows (created by the new seed step) and once under whichever uid ran
the legacy seed. Same names, two owners — users see confusing
duplicates in the marketplace.

Fix with [backend/scripts/cleanup_legacy_platform_seeds.py](../../backend/scripts/cleanup_legacy_platform_seeds.py).
It lists every doc whose `name` matches a template and whose `ownerId`
is **not** the sentinel, and deletes them on `--yes`. Dry-run by default.

```bash
cd backend

# Dry run — prints the platform-owned set and the legacy set.
uv run python scripts/cleanup_legacy_platform_seeds.py --env dev

# Commit to the deletion.
uv run python scripts/cleanup_legacy_platform_seeds.py --env dev --yes

# Verify clean state: "Legacy non-platform (0): / Nothing to clean up."
uv run python scripts/cleanup_legacy_platform_seeds.py --env dev
```

The script requires ADC with Firestore write on the target project
(`gcloud auth application-default login`). It does **not** hit the API
— it talks to Firestore directly via the same client as `db.firestore`,
so it works even if `/api/admin/seed-platform-skills` is misconfigured.

**Safe to re-run.** If no legacy rows exist (the common case) it prints
the platform-owned set and exits 0.

### Dev history (one-time, 2026-04-21)

On the initial PLATFORM-GLOBAL-SKILLS rollout, dev had five rows owned
by the founder's Firebase uid (created by the original
`seed_skills.py` script long before M1). The new seed step created five
platform-owned rows with the same names; the cleanup script above
removed the legacy five. Final state: 5 rows, all `ownerId=aitana-platform`.

## Status tags — `system` and friends

A skill's top-level `tags:` frontmatter carries **presentation** status
tags (never access gates — that stays `access_control`):

- `experimental` / `dev-tool` / `a2ui-demo` / `demo` / `workshop` /
  `admin` — non-production markers. The frontend switcher corrals these
  under an "In development" divider (admins only see them anyway);
  the first three also render a status badge/dot.
- **`system` — platform-embedded agent.** For skills a host surface
  mounts directly by slug (the Skill Studio copilot
  `skill-authoring-assistant`; future in-product help assistants).
  System skills are **hidden from the skill switcher entirely** (even
  via search — `isSystemSkill` in `SkillSwitcher.tsx`) and **excluded
  from `GET /api/skills/marketplace`** (`SYSTEM_TAG` in
  `skills/skill_config.py`). They stay reachable by direct slug
  (`GET /api/skills/{slug}`, streaming) subject to normal access
  control, and admin surfaces still list them (with a "System" badge).
  Adding a new system agent = tag its SKILL.md `system`; nothing else
  to wire.

Tag changes reach a deployed env via the normal seed refresh on the
next deploy (template-managed skills only) or `aiplatform skill push`.

## Known failure modes

- **Seed returns 403**: SA email not in `ADMIN_SEED_ALLOWED_SAS`. See
  "Allowlist env var" above.
- **Seed returns 200 but `created: 0, skipped: 0`**: the templates dir
  is empty in the deployed image. Check the `build-backend` step — the
  `backend/skills/templates/` directory should copy into the image.
- **Seed returns 200 with `failed: [...]`**: one or more template
  `SKILL.md` files have malformed YAML frontmatter. Fix the template and
  redeploy.
- **Seed timeouts**: the endpoint has a 60s timeout for the curl, but
  Cloud Run cold starts can eat into that. A warning will log but the
  build still passes (non-fatal). Re-run manually.

## Related

- [agent-factory-smoke.md](agent-factory-smoke.md) — end-to-end SSE probe
  for the agent factory; works against a platform skill id.
- [deployed-urls.md](deployed-urls.md) — service URLs per environment.
- [auth-smoke-testing.md](auth-smoke-testing.md) — Firebase auth round
  trip.
