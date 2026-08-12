#!/usr/bin/env bash
# promote-env.sh — promote a released version between environments.
#
# v6.20.0, AIPLA #46/#47. The SINGLE source of promotion logic: `make promote`
# and `aiplatform deploy promote` both call this, so there is one
# implementation to get right and one to audit.
#
# What it does: runs the target env's promote TRIGGER at a git tag. The trigger
# executes cloudbuild.promote.yaml, which copies the tested backend + toolbox
# images by digest and rebuilds only the frontend (NEXT_PUBLIC_* is
# compile-time-inlined, so a copied UI would carry the SOURCE env's config).
#
# WHY A TRIGGER AND NOT `gcloud builds submit` (AIPLA #46/#47) — three reasons,
# all learned the hard way downstream:
#
#   1. `builds submit .` uploads the OPERATOR'S LOCAL WORKING TREE as the build
#      source. AIPLA's prod ui:v0.1.3 was built from an untagged laptop commit
#      because of exactly this. `triggers run --tag` makes Cloud Build check the
#      repo out AT THE TAG, so what ships cannot depend on what is checked out
#      here. Your local state is irrelevant to a promotion — deliberately.
#   2. `builds submit` without --service-account silently falls back to the
#      Compute Engine default SA, so one pipeline runs as two identities and any
#      IAM grant covers exactly one of them. The symptom is a PERMISSION_DENIED
#      on a cross-project Artifact Registry read, which sends you hunting in the
#      cross-project grant — the wrong place entirely.
#   3. Adding --service-account then surfaces a second requirement invisible
#      from the trigger path: `builds submit` stages a source tarball in the
#      auto-created <project>_cloudbuild bucket, and the named SA needs
#      storage.objectViewer to read it back.
#
# So: no `gcloud builds submit` in this repo, and a test asserts it stays that
# way. See docs/ops/gotchas.md.
#
# Usage:
#   scripts/promote-env.sh --from test --to prod --version v6.20.0            # dry run
#   scripts/promote-env.sh --from test --to prod --version v6.20.0 --yes      # execute
#
# Dry run is the DEFAULT. Nothing mutates without --yes.

set -euo pipefail

REGION="${PROMOTE_REGION:-europe-west1}"
SERVICE_NAME="${PROMOTE_SERVICE_NAME:-platform-frontend}"

# The project that HOSTS THE TRIGGERS, which is NOT the target environment's
# project. All Aitana Cloud Build triggers live in one shared deploy project and
# deploy outward into the per-env projects. Conflating the two makes
# `triggers run` fail with a confusing "trigger not found" against a project
# that looks right — caught by a dry run before the first real promotion.
# (A fork whose triggers live in each env project sets this to the env project.)
TRIGGER_PROJECT="${PROMOTE_TRIGGER_PROJECT:-your-deploy-project-id}"

# Environment -> GCP project. Mirrors cli/aiplatform/config.yaml's
# logging.projects block; keep the two in step.
project_for_env() {
  case "$1" in
    dev)  echo "your-project-id" ;;
    test) echo "your-project-id-test" ;;
    prod) echo "your-project-id-prod" ;;
    *)    return 1 ;;
  esac
}

# Only these promotion edges exist. Notably NOT dev->prod: the whole point is
# that prod receives what test verified.
valid_edge() {
  case "$1->$2" in
    "dev->test"|"test->prod") return 0 ;;
    *) return 1 ;;
  esac
}

FROM_ENV=""
TO_ENV=""
VERSION=""
CONFIRMED=0

usage() {
  cat >&2 <<'USAGE'
Usage: promote-env.sh --from <env> --to <env> --version <tag> [--yes]

  --from <env>      Source environment (dev|test)
  --to <env>        Target environment (test|prod)
  --version <tag>   Release tag to promote, e.g. v6.20.0 (must exist on origin)
  --yes             Actually run it. Without this, prints the plan and exits.

Valid edges: dev->test, test->prod
USAGE
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --from)    FROM_ENV="${2:-}"; shift 2 ;;
    --to)      TO_ENV="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --yes|-y)  CONFIRMED=1; shift ;;
    --dry-run) CONFIRMED=0; shift ;;
    -h|--help) usage ;;
    *) echo "FATAL: unknown argument '$1'" >&2; usage ;;
  esac
done

[ -n "$FROM_ENV" ] || { echo "FATAL: --from is required" >&2; usage; }
[ -n "$TO_ENV" ]   || { echo "FATAL: --to is required" >&2; usage; }
[ -n "$VERSION" ]  || { echo "FATAL: --version is required" >&2; usage; }

if ! valid_edge "$FROM_ENV" "$TO_ENV"; then
  echo "FATAL: '$FROM_ENV -> $TO_ENV' is not a valid promotion edge (dev->test, test->prod)" >&2
  echo "       prod must receive what test verified — there is no dev->prod shortcut." >&2
  exit 1
fi

SOURCE_PROJECT="$(project_for_env "$FROM_ENV")"
TARGET_PROJECT="$(project_for_env "$TO_ENV")"
TRIGGER_NAME="trigger-aitana-${TO_ENV}-promote"

# The tag must exist ON ORIGIN, not just locally: Cloud Build resolves it
# server-side, so a tag that exists only on this machine produces a confusing
# build-time failure rather than a clear one here.
if ! git ls-remote --exit-code --tags origin "refs/tags/${VERSION}" >/dev/null 2>&1; then
  echo "FATAL: tag '${VERSION}' not found on origin." >&2
  echo "       Push it first:  git push origin ${VERSION}" >&2
  exit 1
fi

cat <<PLAN
Promotion plan
  ${FROM_ENV} (${SOURCE_PROJECT})  ->  ${TO_ENV} (${TARGET_PROJECT})
  version:  ${VERSION}  (verified present on origin)
  trigger:  ${TRIGGER_NAME}  (region ${REGION}, hosted in ${TRIGGER_PROJECT})
  service:  ${SERVICE_NAME}

What the trigger will do (cloudbuild.promote.yaml):
  1. copy  backend:${VERSION}  ${FROM_ENV} -> ${TO_ENV}   BY DIGEST, no rebuild
  2. copy  toolbox:${VERSION}  ${FROM_ENV} -> ${TO_ENV}   BY DIGEST, no rebuild
  3. build ui:${VERSION}       from the tag, with ${TO_ENV}'s config
     (the frontend CANNOT be copied: NEXT_PUBLIC_* is compile-time-inlined,
      so a copied UI would ship ${FROM_ENV}'s Firebase project and API URLs)
  4. deploy all three onto the existing ${SERVICE_NAME}, images only
  5. smoke the target; any non-200 fails the promotion

Source is the REPO AT TAG ${VERSION}, not your working tree.
PLAN

# --tag selects the REVISION Cloud Build checks out. It does NOT set the
# _VERSION substitution the pipeline reads to decide which images to copy —
# those are two independent things and the first promotion failed on exactly
# that distinction (guard-version caught it at step 0, which is what that guard
# is for). Both are required, and they must agree.
COMMAND=(gcloud builds triggers run "${TRIGGER_NAME}"
         --tag="${VERSION}"
         --substitutions="_VERSION=${VERSION}"
         --region="${REGION}"
         --project="${TRIGGER_PROJECT}")

if [ "$CONFIRMED" -ne 1 ]; then
  echo
  echo "DRY RUN — nothing has been changed. Would run:"
  printf '  %q' "${COMMAND[@]}"; echo
  echo
  echo "Re-run with --yes to execute."
  exit 0
fi

echo
echo "Running: ${COMMAND[*]}"
"${COMMAND[@]}"

cat <<NEXT

Promotion build submitted. Verify it actually COPIED rather than rebuilt —
digest equality is the whole point:

  aiplatform deploy status --env ${FROM_ENV}
  aiplatform deploy status --env ${TO_ENV}

The backend digests must match. If they differ, the copy step did not do what
it claims and the promotion is not a promotion.
NEXT
