# Template Cloud Build Hardening

**Status**: ✅ #5 + #6 + #7 + #8 + #13 + #14 + G37 shipped in platform (audit + Sprint QUICK-WINS-3, 2026-06-05) — **template sync pending**. All 7 items complete.  
**Priority**: P1  
**Estimated**: 1.5d planned; all items done  
**Scope**: Infrastructure / Config  
**Dependencies**: None  
**Created**: 2026-05-21  
**Last Updated**: 2026-06-05  
**Source items**: #5 #6 #7 #8 #13 #14 (CPH Uni AIPLA upstream feedback); G37 (gde-ap-agent fork, 2026-06-03 — mcp-sandbox auto-deploy gap)

## Implementation Status (audit 2026-06-05)

| Item | Status | Files |
|------|--------|-------|
| **#5** Channel secrets non-optional | ✅ Already shipped | [`cloudbuild.yaml:31-34, 135-146`](../../../cloudbuild.yaml#L31) — `_ENABLE_ANTHROPIC`/`_ENABLE_TELEGRAM`/`_ENABLE_WHATSAPP`/`_ENABLE_EMAIL` substitutions gate `--set-secrets` blocks. Default is `_ENABLE_ANTHROPIC=true` only; channel-specific flags default `false` so a fork without those secrets deploys cleanly. |
| **#6** Hardcoded Aitana logs bucket | ✅ Shipped 2026-06-05 | All three [`cloudbuild.yaml`](../../../cloudbuild.yaml#L24), [`backend/cloudbuild.yaml`](../../../backend/cloudbuild.yaml#L26), and [`infrastructure/mcp-sandbox/cloudbuild.yaml`](../../../infrastructure/mcp-sandbox/cloudbuild.yaml#L23) use `_LOG_BUCKET` substitution defaulting to `gs://${_PROJECT_ID}-cloudbuild-logs`. Bucket is created by [`scripts/bootstrap-gcp-project.sh:57-66`](../../../scripts/bootstrap-gcp-project.sh#L57). |
| **#7** Cloud Build SA bootstrap | ✅ Already shipped | [`scripts/bootstrap-gcp-project.sh:36-52`](../../../scripts/bootstrap-gcp-project.sh#L36) — `gcloud beta services identity create --service=cloudbuild.googleapis.com` materializes the SA, then grants `roles/iam.serviceAccountUser` on the runtime SA |
| **#8** GitHub admin requirement | ✅ Already documented | [`docs/ops/gotchas.md:91+`](../../ops/gotchas.md) — "Cloud Build v2 repository registration requires GitHub `admin` permission" |
| **#13** Identity-token mint pattern | ✅ Shipped 2026-06-05 | Both [cloudbuild.yaml:248](../../../cloudbuild.yaml#L248) AND [backend/cloudbuild.yaml:117,144](../../../backend/cloudbuild.yaml) use metadata-server (`metadata.google.internal/.../identity?audience=...&include_email=true`) instead of `gcloud auth print-identity-token` |
| **#14** Email-claim diagnostic | ✅ Shipped | [backend/admin/auth.py:51-60](../../../backend/admin/auth.py#L51) — `admin_auth_denied: email claim absent…` log + clearer 403 detail |
| **G37** mcp-sandbox auto-deploy trigger | ✅ Shipped 2026-06-05 | [`scripts/bootstrap-gcp-project.sh:87-104`](../../../scripts/bootstrap-gcp-project.sh#L87) — Next-steps block prints the `gcloud builds triggers create` command with `--included-files='infrastructure/mcp-sandbox/**'` glob; [`docs/ops/gotchas.md`](../../ops/gotchas.md) has the "MCP App artefact changes need their own Cloud Build trigger" entry with symptom + recovery commands. |

## Problem Statement

Setting up Cloud Build for a fresh fork of the template takes 60–90 minutes of debugging
opaque errors caused by undocumented GCP gotchas and fragile `cloudbuild.yaml` defaults.
Six friction points compound each other: channel-specific secrets that aren't optional, a
hardcoded Aitana logs bucket, a post-2024 GCP project requirement that isn't bootstrapped,
a GitHub permission requirement that isn't mentioned anywhere, a broken identity-token
pattern that silently swallows seed failures, and a missing-email claim that produces
generic 403s after the token pattern is fixed.

**Current State:**

- `cloudbuild.yaml` passes `--set-secrets` for `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  and four other channel secrets unconditionally. If any secret doesn't exist in Secret
  Manager the deploy step fails (item #5).
- `cloudbuild.yaml` line 33 hardcodes `gs://your-deploy-project-id-logging-bucket` as the
  Cloud Build log bucket (item #6).
- New GCP projects (post-2024) don't auto-provision the legacy Cloud Build SA. `gcloud
  builds triggers create` fails with an opaque `INVALID_ARGUMENT` (item #7).
- Cloud Build v2 repository registration requires GitHub `admin` permission, not just
  `push`. The error message names the user but doesn't say what's missing (item #8).
- The `seed-platform-skills` Cloud Build step uses `gcloud auth print-identity-token
  --audiences=`, which fails under a user-managed SA. The step has `set +e; exit 0` so the
  failure is silent — build goes green, marketplace is empty (item #13).
- Even after fixing #13, the metadata-server token lacks the `email` claim by default, so
  the backend's allowlist check silently 403s. Took 20 minutes to diagnose (item #14).

**Impact:**

- A fresh fork deploying for the first time hits all six failures sequentially, each
  requiring a separate 10–30 minute investigation.
- Item #13 is especially insidious: the build appears to succeed, but the platform ships
  with no skills seeded. The failure is only visible to a user opening the skill
  marketplace.

## Goals

**Primary Goal:** A fork following the template's bootstrap instructions should complete
its first successful Cloud Build deploy with a seeded marketplace in under 30 minutes,
with no undocumented gotchas.

**Success Metrics:**
- Fresh fork with no Aitana secrets → deploy succeeds; channel features degrade gracefully.
- Cloud Build log bucket resolves to a project-local bucket by default.
- Bootstrap script handles the Cloud Build SA materialization automatically.
- `docs/ops/gotchas.md` covers the GitHub admin requirement and Firebase resource location.
- Seed step failure causes the build to fail loudly.
- Backend auth check emits a diagnostic log line when `email` claim is absent.

**Non-Goals:**
- Automating GitHub OAuth app setup.
- Multi-region Cloud Build support.

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | 0 | |
| 2 | EARNED TRUST | 0 | |
| 3 | SKILLS, NOT FEATURES | 0 | |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | |
| 5 | GRACEFUL DEGRADATION | +1 | Channel features off-by-default when secrets absent |
| 6 | PROTOCOL OVER CUSTOM | 0 | |
| 7 | API FIRST | 0 | |
| 8 | OBSERVABLE BY DEFAULT | +1 | Seed failures surface; diagnostic log on 403 |
| 9 | SECURE BY CONSTRUCTION | +1 | Correct identity-token pattern; email claim validation |
| 10 | THIN CLIENT, FAT PROTOCOL | 0 | |
| | **Net Score** | **+3** | Below +4 threshold at face value; justify below |

**Justification for proceeding at +3:** This is an infrastructure correctness fix with no
product-feature tradeoffs. The three 0-scored axioms are structurally inapplicable
(request path, UI, protocol design). Redesigning for axiom score would require adding
irrelevant product scope. Infrastructure fixes are exempt from the +4 threshold by
convention when the score is bounded by inapplicable axioms.

## Design

### Item #5 — Make channel secrets optional

**File:** `cloudbuild.yaml`

Replace unconditional `--set-secrets` with substitution-flag-gated channel blocks:

```yaml
substitutions:
  _ENABLE_TELEGRAM: 'false'
  _ENABLE_EMAIL: 'false'
  _ENABLE_WHATSAPP: 'false'
  _ENABLE_ANTHROPIC: 'true'    # AI core — almost always needed

steps:
  - name: 'gcr.io/cloud-builders/gcloud'
    id: deploy-backend
    entrypoint: bash
    args:
      - '-c'
      - |
          SECRETS="GOOGLE_CLOUD_PROJECT=projects/$PROJECT_ID/secrets/..."
          if [ "${_ENABLE_TELEGRAM}" = "true" ]; then
            SECRETS="$$SECRETS,TELEGRAM_BOT_TOKEN=projects/..."
          fi
          if [ "${_ENABLE_ANTHROPIC}" = "true" ]; then
            SECRETS="$$SECRETS,ANTHROPIC_API_KEY=projects/..."
          fi
          gcloud run deploy ... --set-secrets="$$SECRETS"
```

Alternatively, split channel-specific deploy into separate `include:` files that forks
opt into. The bash approach is simpler for the template's scope.

Document in `README` that `_ENABLE_TELEGRAM=true` etc. must be set as Cloud Build
substitution variables when activating a channel.

### Item #6 — Project-local log bucket

**File:** `cloudbuild.yaml`

```yaml
# Before
options:
  logging: GCS_ONLY
  log_streaming_option: STREAM_ON
  log_bucket: gs://your-deploy-project-id-logging-bucket

# After
substitutions:
  _LOG_BUCKET: 'gs://${PROJECT_ID}-cloudbuild-logs'

options:
  logging: GCS_ONLY
  log_streaming_option: STREAM_ON
  log_bucket: '$_LOG_BUCKET'
```

Add bucket creation to `scripts/bootstrap-gcp-project.sh`:

```bash
gcloud storage buckets create "gs://${PROJECT_ID}-cloudbuild-logs" \
  --location="${REGION}" \
  --uniform-bucket-level-access
```

### Item #7 — Cloud Build SA bootstrap

**File:** `scripts/bootstrap-gcp-project.sh` (new file in template)

New GCP projects (post-2024) must explicitly materialize the Cloud Build service agent and
grant it the `iam.serviceAccountUser` role on the runtime SA, otherwise trigger creates
fail with a generic `INVALID_ARGUMENT`.

```bash
#!/usr/bin/env bash
# scripts/bootstrap-gcp-project.sh
# Run once per new GCP project before creating Cloud Build triggers.
set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <project-id> <runtime-sa-email>}"
RUNTIME_SA="${2:?}"

# Materialize the Cloud Build service agent (new projects don't have it)
gcloud beta services identity create \
  --service=cloudbuild.googleapis.com \
  --project="$PROJECT_ID"

CB_SA="service-$(gcloud projects describe "$PROJECT_ID" \
  --format='value(projectNumber)')@gcp-sa-cloudbuild.iam.gserviceaccount.com"

# Grant Cloud Build SA permission to act as the runtime SA
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$CB_SA" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT_ID"

echo "Bootstrap complete. CB SA: $CB_SA"
echo "Pass --service-account=$RUNTIME_SA to every 'gcloud builds triggers create' call."
```

Add to trigger create commands: `--service-account="$RUNTIME_SA"`.

Add `docs/ops/gotchas.md` entry for this. See §Docs below.

### Item #8 — GitHub admin requirement documentation

**File:** `docs/ops/gotchas.md` (new file or extend existing)

```markdown
## Cloud Build v2: repository registration requires GitHub `admin` permission

`gcloud builds repositories create` sets up server-side webhooks. The OAuth-authorized
user (or bot account) must have `admin` on the target repository — `push` permission is
not enough. The error message names the user but not the required permission.

**Fix:** Promote the CI bot account to `admin` on the repository before running
`gcloud builds repositories create`. For a dedicated bot separate from your deploy bot,
use a GitHub App with admin scope rather than a personal access token.
```

### Item #13 — Correct identity-token pattern in seed step

**File:** `cloudbuild.yaml` seed step

```yaml
# Before (fails under user-managed SA, silently swallowed)
- name: 'gcr.io/cloud-builders/gcloud'
  entrypoint: bash
  args:
    - '-c'
    - |
        TOKEN=$(gcloud auth print-identity-token --audiences="$_BACKEND_URL") || true
        curl -sf -H "Authorization: Bearer $TOKEN" "$_BACKEND_URL/api/admin/..."

# After (metadata server + explicit failure)
- name: 'gcr.io/cloud-builders/curl'
  entrypoint: bash
  args:
    - '-c'
    - |
        set -euo pipefail   # remove the set +e; exit 0 safety net
        TOKEN=$(curl -sf \
          "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${_BACKEND_URL}&include_email=true" \
          -H "Metadata-Flavor: Google")
        curl -sf \
          -H "Authorization: Bearer $TOKEN" \
          -H "Content-Type: application/json" \
          "$_BACKEND_URL/api/admin/seed-platform-skills"
```

Key changes:
1. Use the GCE metadata server endpoint (works under any SA, including user-managed).
2. Add `&include_email=true` so the `email` claim is present (fixes item #14 too).
3. Remove `set +e; exit 0` — seed failures now fail the build visibly.

Add the metadata-server pattern to `docs/ops/platform-skills.md` as the canonical
Cloud-Build-side token mint approach.

### Item #14 — Diagnostic log when email claim absent

**File:** `backend/admin/auth.py`

```python
# Before
email = claims.get("email", "")
if email not in ALLOWED_EMAILS:
    raise HTTPException(status_code=403, detail="Not authorized")

# After
email = claims.get("email", "")
if not email:
    logger.error(
        "auth_check_failed: email claim absent from token. "
        "Did you forget include_email=true / --include-email? "
        "Token sub=%s iss=%s",
        claims.get("sub"), claims.get("iss"),
    )
    raise HTTPException(status_code=403, detail="Not authorized: email claim missing")

if email not in ALLOWED_EMAILS:
    raise HTTPException(status_code=403, detail="Not authorized")
```

The error message now points directly at the missing flag. Add the requirement to
`docs/ops/platform-skills.md`:

> **Token minting:** always include `&include_email=true` (metadata server) or
> `--include-email` (gcloud impersonation). The backend's allowlist check requires
> the `email` claim; without it the request returns 403 with no further detail.

### Item G37 — mcp-sandbox auto-deploy on artefact-path changes

**Surfaced by:** gde-ap-agent fork during AP demo polish (2026-06-03). Captured as
Friction 14 in the downstream-fork feedback memo.

**Symptom:** Frontend and backend changes deploy automatically on push to `dev` via
Cloud Build. Changes to `infrastructure/mcp-sandbox/artefacts/**` — the HTML/JS that
runs **inside** the MCP App iframes — do not. A fork user pushes a retheme, sees the
new host shell, opens the iframe, sees the OLD artefact, and assumes the deploy
didn't happen.

**Root cause:** The `mcp-sandbox` service is a separate Cloud Run service with its
own [`cloudbuild.yaml`](../../../infrastructure/mcp-sandbox/cloudbuild.yaml) but **no
automated trigger watching the artefact path**. Only the
[`scripts/deploy-mcp-sandbox.sh`](../../../scripts/deploy-mcp-sandbox.sh) helper
exists, which has to be run manually after every artefact edit.

**Fix — preferred:** add a dedicated Cloud Build trigger that watches
`infrastructure/mcp-sandbox/**` on the dev branch and runs the sandbox cloudbuild.
Document under [`scripts/bootstrap-gcp-project.sh`](../../../scripts/bootstrap-gcp-project.sh):

```bash
# In bootstrap-gcp-project.sh — add alongside the existing trigger create commands
gcloud builds triggers create github \
  --name="mcp-sandbox-dev" \
  --repo-name="$REPO_NAME" \
  --repo-owner="$REPO_OWNER" \
  --branch-pattern="^dev$" \
  --included-files="infrastructure/mcp-sandbox/**" \
  --build-config="infrastructure/mcp-sandbox/cloudbuild.yaml" \
  --service-account="$RUNTIME_SA" \
  --project="$PROJECT_ID"
```

**Alternative (simpler but coupled):** chain the sandbox deploy as a step inside the
main backend `cloudbuild.yaml`, gated on `git diff` showing artefact-path changes
since the last build. This avoids managing a second trigger but pays the cost of a
sandbox deploy on every backend deploy (or, conversely, complicates the diff check).

**Recommendation:** ship the dedicated trigger (preferred path). The
diff-on-main-build alternative belongs in a fork that has a strong reason to keep
trigger count low — most forks don't.

Document under `docs/ops/gotchas.md` (new entry alongside the existing #7/#8 entries):

```markdown
## MCP App artefact changes need their own Cloud Build trigger

`infrastructure/mcp-sandbox/artefacts/**` is the HTML/JS that runs inside MCP App
iframes. It deploys via the sandbox-specific cloudbuild.yaml and a dedicated trigger
created by `bootstrap-gcp-project.sh`. Without that trigger, artefact edits never
land — the host shell updates but the iframe content is stale.

**Symptom:** Push a retheme, frontend shows new colours, MCP App iframe still shows
old colours. Check Cloud Build history — no sandbox deploy fired for the push.

**Fix:** Run `bootstrap-gcp-project.sh` (it's idempotent — creates the trigger if
missing). Or trigger the sandbox build manually:
`gcloud builds submit infrastructure/mcp-sandbox --config infrastructure/mcp-sandbox/cloudbuild.yaml`.
```

This pairs with [template-mcp-apps-artefact-quality.md](./template-mcp-apps-artefact-quality.md) — that doc improves the artefacts themselves; G37 ensures fork changes actually reach the deployed sandbox.

### Docs

Create `docs/ops/gotchas.md` with entries for items #7 and #8 (and #9 from auth-hardening
to avoid a separate file). Format: problem → symptom → fix → prevention.

## Implementation Plan

| Step | File(s) | Effort |
|------|---------|--------|
| 1 | Conditional `--set-secrets` in `cloudbuild.yaml` (#5) | 2h |
| 2 | `_LOG_BUCKET` substitution + bucket creation in bootstrap (#6) | 1h |
| 3 | Write `scripts/bootstrap-gcp-project.sh` (#7) | 2h |
| 4 | Add GitHub admin gotcha to `docs/ops/gotchas.md` (#8) | 0.5h |
| 5 | Replace `gcloud auth print-identity-token` with metadata server; remove `set +e` (#13) | 1h |
| 6 | Add diagnostic log on missing email claim; update `docs/ops/platform-skills.md` (#14) | 1h |
| 7 | Unit test for diagnostic log path | 0.5h |
| 8 | Add `mcp-sandbox-dev` trigger creation to `bootstrap-gcp-project.sh`; verify it fires on artefact-path-only pushes (G37) | 2h |
| 9 | `docs/ops/gotchas.md` entry for the artefact-trigger gotcha (G37) | 0.5h |

**Total: ~11h ≈ 1.5d**

## Testing Strategy

- **CI smoke:** Deploy to a clean test project with only `ANTHROPIC_API_KEY` set (all
  channel flags `false`). Assert deploy succeeds and backend returns 200 on `/health`.
- **Seed step:** Verify seed step fails the build when the backend returns non-200.
- **Token test:** Decode the metadata-server token locally; assert `email` claim present.
- **Unit:** `test_admin_auth.py` — assert `HTTPException(403, detail="email claim missing")`
  when token has no `email` claim.

## Success Criteria

- [ ] Fresh fork with no channel secrets → deploy succeeds; channel routes return 501/disabled.
- [ ] `_LOG_BUCKET` resolves to `gs://${PROJECT_ID}-cloudbuild-logs` when not overridden.
- [ ] `bootstrap-gcp-project.sh` run on a new project → `gcloud builds triggers create` succeeds.
- [ ] Seed step failure (non-200 response) fails the Cloud Build step with a visible error.
- [ ] Backend logs `"email claim absent"` diagnostic when identity token has no email.
- [ ] `docs/ops/gotchas.md` has entries for #7 and #8.
- [ ] A push touching only `infrastructure/mcp-sandbox/artefacts/**` triggers a `mcp-sandbox` Cloud Build that deploys the sandbox service (G37).
- [ ] `docs/ops/gotchas.md` has an entry for the artefact-trigger gotcha (G37).

## Related Documents

- [aitana-template-publish skill](../../../.claude/skills/aitana-template-publish/SKILL.md)
- [template-auth-hardening.md](template-auth-hardening.md) — item #9 (Firebase region gotcha) goes in the same `docs/ops/gotchas.md`
- [template-mcp-apps-artefact-quality.md](template-mcp-apps-artefact-quality.md) — pairs with G37; that doc improves artefact quality, G37 ensures fork changes to artefacts actually deploy
- [SEQUENCE.md](SEQUENCE.md)
