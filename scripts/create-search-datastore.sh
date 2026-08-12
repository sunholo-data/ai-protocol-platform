#!/usr/bin/env bash
# scripts/create-search-datastore.sh
#
# Create a Vertex AI Search (Discovery Engine) datastore for the ai_search tool,
# and optionally import documents from a GCS bucket.
#
# WHY THIS EXISTS (template-fork-ergonomics.md G13c): a fork that enables an
# ai_search-backed skill hits
#     400 ... datastore: Invalid Vertex AI datastore resource name
# for either of two reasons, and the error does not distinguish them:
#   1. the datastore simply does not exist, or
#   2. SKILL.md gave a BARE id that was never expanded to a full resource path.
# (2) is handled in code now — backend/tools/resource_ids.py expands bare ids
# using VERTEX_AI_SEARCH_PROJECT / VERTEX_AI_SEARCH_LOCATION — so this script
# handles (1).
#
# Usage:
#   ./scripts/create-search-datastore.sh --project <project-id>
#        [--datastore-id <id>] [--location global|eu|us]
#        [--import-gcs gs://bucket/prefix/**] [--sa <runtime-sa-email>]
#
# Idempotent: an existing datastore is reported, not recreated.

set -euo pipefail

PROJECT_ID=""
DATASTORE_ID="platform-docs"
# Discovery Engine locations are "global" | "eu" | "us" — NOT Cloud Run regions.
# Pick "eu" to keep indexed content in the EU.
LOCATION="eu"
IMPORT_GCS=""
RUNTIME_SA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT_ID="${2:?--project needs a value}";     shift 2 ;;
    --datastore-id) DATASTORE_ID="${2:?--datastore-id needs a value}"; shift 2 ;;
    --location)     LOCATION="${2:?--location needs a value}";      shift 2 ;;
    --import-gcs)   IMPORT_GCS="${2:?--import-gcs needs a value}";  shift 2 ;;
    --sa)           RUNTIME_SA="${2:?--sa needs a value}";          shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
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

case "$LOCATION" in
  global|eu|us) ;;
  *) echo "ERROR: --location must be one of: global, eu, us (got '${LOCATION}')." >&2
     echo "       These are Discovery Engine multi-regions, not Cloud Run regions." >&2
     exit 2 ;;
esac

if [[ "$LOCATION" == "global" ]]; then
  API="https://discoveryengine.googleapis.com/v1"
else
  API="https://${LOCATION}-discoveryengine.googleapis.com/v1"
fi
PARENT="projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection"
FULL_NAME="${PARENT}/dataStores/${DATASTORE_ID}"

echo "==> Vertex AI Search datastore"
echo "    Project   : ${PROJECT_ID}"
echo "    Location  : ${LOCATION}"
echo "    Datastore : ${DATASTORE_ID}"
echo ""

echo "[1/4] Ensuring the Discovery Engine API is enabled..."
gcloud services enable discoveryengine.googleapis.com --project="${PROJECT_ID}"

TOKEN="$(gcloud auth print-access-token)"
auth_curl() { curl -sS -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" "$@"; }

echo "[2/4] Checking whether the datastore already exists..."
if auth_curl "${API}/${FULL_NAME}" | grep -q '"name"'; then
  echo "      Already exists — not recreating."
else
  echo "      Creating datastore..."
  # contentConfig CONTENT_REQUIRED = unstructured documents (PDFs, docs), which
  # is what the ai_search tool + Sources card expect. solutionType SEARCH keeps
  # it a plain search app rather than a recommendation engine.
  PAYLOAD="$(DISPLAY="${DATASTORE_ID}" python3 -c '
import json, os
print(json.dumps({
    "displayName": os.environ["DISPLAY"],
    "industryVertical": "GENERIC",
    "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
    "contentConfig": "CONTENT_REQUIRED",
}))')"
  RESPONSE="$(auth_curl -X POST -d "${PAYLOAD}" \
    "${API}/${PARENT}/dataStores?dataStoreId=${DATASTORE_ID}")"
  if printf '%s' "$RESPONSE" | grep -q '"error"'; then
    echo "" >&2
    echo "ERROR: datastore creation failed:" >&2
    printf '%s\n' "$RESPONSE" | head -30 >&2
    exit 1
  fi
fi

if [[ -n "$IMPORT_GCS" ]]; then
  echo "[3/4] Importing documents from ${IMPORT_GCS}..."
  # Unstructured content: each GCS object becomes a document. This starts a
  # long-running import; indexing continues after the call returns.
  IMPORT_PAYLOAD="$(URI="${IMPORT_GCS}" python3 -c '
import json, os
print(json.dumps({
    "gcsSource": {"inputUris": [os.environ["URI"]], "dataSchema": "content"},
    "reconciliationMode": "INCREMENTAL",
}))')"
  auth_curl -X POST -d "${IMPORT_PAYLOAD}" \
    "${API}/${FULL_NAME}/branches/default_branch/documents:import" | head -20
  echo ""
  echo "      Import started — indexing continues in the background."
  echo "      Documents are NOT searchable until it finishes (minutes to hours)."
else
  echo "[3/4] No --import-gcs given; datastore created empty."
fi

if [[ -n "$RUNTIME_SA" ]]; then
  echo "[4/4] Granting ${RUNTIME_SA} discoveryengine.viewer..."
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/discoveryengine.viewer" >/dev/null
else
  echo "[4/4] No --sa given; skipping IAM grant."
  echo "      The runtime service account needs roles/discoveryengine.viewer on"
  echo "      the project that OWNS the datastore, or every search 403s."
fi

echo ""
echo "==> Done. In your skill's SKILL.md:"
echo "      toolConfigs:"
echo "        ai_search:"
echo "          datastore_id: ${DATASTORE_ID}"
echo ""
echo "    A bare id is expanded using these (set them on the backend when the"
echo "    datastore is not in the app's own project/location):"
echo "      VERTEX_AI_SEARCH_PROJECT=${PROJECT_ID}"
echo "      VERTEX_AI_SEARCH_LOCATION=${LOCATION}"
echo ""
echo "    Or paste the full path instead:"
echo "      ${FULL_NAME}"
