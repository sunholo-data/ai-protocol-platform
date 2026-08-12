#!/usr/bin/env bash
# scripts/create-artifact-bucket.sh
#
# Create the GCS bucket the ADK artifact service writes to (ADK_ARTIFACT_BUCKET).
#
# WHY THIS EXISTS (template-fork-ergonomics.md G13b): the bucket name is
# conventionally "<project>-artifacts", but nothing in the app creates it. In
# this deployment terraform does — a fork has no terraform, so the bucket never
# exists and EVERY chat turn that touches `load_artifacts` fails with
#     404 ... The specified bucket does not exist
# which surfaces as a hung turn rather than a clear error. A fork hits this on
# its first document upload and has nothing to search for.
#
# Usage:
#   ./scripts/create-artifact-bucket.sh --project <project-id> [--region <region>]
#                                       [--bucket <name>] [--sa <runtime-sa-email>]
#
# Idempotent: re-running against an existing bucket reports and exits 0.

set -euo pipefail

PROJECT_ID=""
REGION="europe-west1"
BUCKET=""
RUNTIME_SA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="${2:?--project needs a value}"; shift 2 ;;
    --region)  REGION="${2:?--region needs a value}";      shift 2 ;;
    --bucket)  BUCKET="${2:?--bucket needs a value}";      shift 2 ;;
    --sa)      RUNTIME_SA="${2:?--sa needs a value}";      shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
  echo "ERROR: no project. Pass --project <id> or run 'gcloud config set project <id>'." >&2
  exit 2
fi

# The convention the app documents. Overridable because some orgs enforce a
# bucket-naming policy that "<project>-artifacts" would violate.
BUCKET="${BUCKET:-${PROJECT_ID}-artifacts}"

echo "==> ADK artifact bucket"
echo "    Project : ${PROJECT_ID}"
echo "    Bucket  : gs://${BUCKET}"
echo "    Region  : ${REGION}"
echo ""

if gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "[1/2] Bucket already exists — nothing to create."
else
  echo "[1/2] Creating bucket..."
  # Uniform bucket-level access: artifact objects are authorized by IAM on the
  # bucket, never by per-object ACLs. Public access is prevented explicitly —
  # these objects are user documents and must never be world-readable.
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi

if [[ -n "$RUNTIME_SA" ]]; then
  echo "[2/2] Granting ${RUNTIME_SA} objectAdmin on the bucket..."
  # Only needed when the runtime SA does NOT already hold a project-level
  # storage role. Granting at bucket scope is the least-privilege option for a
  # fork that hasn't set up broader roles yet.
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/storage.objectAdmin" \
    --project="${PROJECT_ID}" >/dev/null
else
  echo "[2/2] No --sa given; skipping IAM grant."
  echo "      The runtime service account needs roles/storage.objectAdmin on this"
  echo "      bucket (or a project-level equivalent) or reads/writes will 403."
fi

echo ""
echo "==> Done. Set this on the backend service:"
echo "      ADK_ARTIFACT_BUCKET=${BUCKET}"
echo ""
echo "    Verify:"
echo "      gcloud storage ls gs://${BUCKET} --project=${PROJECT_ID}"
