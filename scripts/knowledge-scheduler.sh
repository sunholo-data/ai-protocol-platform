#!/usr/bin/env bash
# knowledge-scheduler.sh — create or update the Cloud Scheduler job that keeps
# operator-declared knowledge bases current (knowledge-base-freshness.md §3.1).
#
# Every 15 minutes it POSTs /internal/knowledge/sync-due through the frontend's
# proxy with an OIDC token for the service's own account; the route reconciles
# each knowledge base whose interval (`sync_every_minutes`, default nightly) has
# passed. The route is CLOSED until the service has, as env vars,
#     KNOWLEDGE_SCHEDULER_OIDC_SA   = the account this job signs as
#     KNOWLEDGE_SCHEDULER_AUDIENCE  = the URI this job calls
# which reach the service through the trigger substitutions of the same names
# (prefixed with _). This script prints both values; per-env config a code
# merge does not carry (docs/ops/env-config-parity.md).
#
# Usage:
#   scripts/knowledge-scheduler.sh dev          # show what it would do
#   scripts/knowledge-scheduler.sh dev --yes    # create or update the job
#
# Needs cloudscheduler.jobs.create/update in the env's project, and
# iam.serviceAccounts.actAs on the service account.

source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

ENV_NAME="${1:-}"
APPLY=0
[ "${2:-}" = "--yes" ] && APPLY=1
[ -n "$ENV_NAME" ] || { echo "usage: $0 <dev|test|prod> [--yes]" >&2; exit 2; }

PROJECT="$(project_for_env "$ENV_NAME")"
[ -n "$PROJECT" ] || { echo "ERROR: no project for env '$ENV_NAME' (check deploy.env)" >&2; exit 2; }
REGION="${DEPLOY_REGION:-europe-west1}"
SERVICE="${DEPLOY_SERVICE_NAME:?DEPLOY_SERVICE_NAME is not set (deploy.env)}"
JOB="knowledge-sync-due"
SCHEDULE="*/15 * * * *"

URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" --format='value(status.url)')"
SA="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(spec.template.spec.serviceAccountName)')"
[ -n "$URL" ] && [ -n "$SA" ] || { echo "ERROR: could not read $SERVICE's URL and service account in $PROJECT" >&2; exit 1; }
URI="${URL}/api/proxy/internal/knowledge/sync-due"

echo "env:       $ENV_NAME ($PROJECT, $REGION)"
echo "job:       $JOB  every 15 minutes  POST $URI"
echo "signs as:  $SA (OIDC, audience = the URI)"
echo
echo "The service needs these (trigger substitutions, then a deploy):"
echo "  _KNOWLEDGE_SCHEDULER_OIDC_SA=$SA"
echo "  _KNOWLEDGE_SCHEDULER_AUDIENCE=$URI"
echo

if [ "$APPLY" -ne 1 ]; then
  echo "DRY RUN — re-run with --yes to create or update the job."
  exit 0
fi

flags=(
  --project="$PROJECT" --location="$REGION"
  --schedule="$SCHEDULE" --time-zone="Etc/UTC"
  --uri="$URI" --http-method=POST
  --oidc-service-account-email="$SA" --oidc-token-audience="$URI"
  # sync_due spends at most ~10 minutes; one long first sync may run on.
  --attempt-deadline=1800s
  --description="Sync due knowledge bases (knowledge-base-freshness.md)"
)
if gcloud scheduler jobs describe "$JOB" --project="$PROJECT" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" "${flags[@]}"
  echo "Updated $JOB."
else
  gcloud scheduler jobs create http "$JOB" "${flags[@]}"
  echo "Created $JOB."
fi
