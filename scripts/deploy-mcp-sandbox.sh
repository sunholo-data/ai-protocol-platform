#!/usr/bin/env bash
# scripts/deploy-mcp-sandbox.sh
#
# Deploy the MCP App sandbox service and print the two env vars that wire it to
# the frontend.
#
# WHY THIS EXISTS (template-fork-ergonomics.md G13d + item #12): MCP Apps render
# in a sandboxed cross-origin iframe served by a SEPARATE Cloud Run service. If
# a fork never deploys it, NEXT_PUBLIC_MCP_SANDBOX_URL is empty (or, worse, was
# once defaulted to this deployment's live URL) and every MCP App artefact
# either 404s or points at someone else's infrastructure.
#
# The separate origin is a SECURITY requirement, not a deployment convenience:
# `allow-same-origin` on the inner iframe is only safe when the sandbox is on a
# different origin from the host app. Do not merge this into the frontend.
#
# Usage:
#   ./scripts/deploy-mcp-sandbox.sh --project <project-id> [--region <region>]
#        [--service <name>] [--host-origins <comma-separated>]
#
# Re-running redeploys in place; Cloud Run keeps the URL stable.

set -euo pipefail

PROJECT_ID=""
REGION="europe-west1"
SERVICE="mcp-sandbox"
HOST_ORIGINS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT_ID="${2:?--project needs a value}";   shift 2 ;;
    --region)       REGION="${2:?--region needs a value}";        shift 2 ;;
    --service)      SERVICE="${2:?--service needs a value}";      shift 2 ;;
    --host-origins) HOST_ORIGINS="${2:?--host-origins needs a value}"; shift 2 ;;
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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX_DIR="${REPO_ROOT}/infrastructure/mcp-sandbox"
if [[ ! -d "$SANDBOX_DIR" ]]; then
  echo "ERROR: ${SANDBOX_DIR} not found — run this from inside the repo." >&2
  exit 2
fi

echo "==> MCP App sandbox"
echo "    Project : ${PROJECT_ID}"
echo "    Region  : ${REGION}"
echo "    Service : ${SERVICE}"
echo ""

echo "[1/3] Ensuring required APIs are enabled..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

echo "[2/3] Building + deploying from ${SANDBOX_DIR}..."
# Deploy from source: Cloud Build builds the image, Cloud Run hosts it. The
# service is PUBLIC by design — it serves the sandbox shell to browsers, and
# the artefacts it hosts must contain no confidential content (see ADR-013 and
# the security rules in CLAUDE.md).
gcloud run deploy "${SERVICE}" \
  --source="${SANDBOX_DIR}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --quiet

SANDBOX_URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)')"

if [[ -z "$SANDBOX_URL" ]]; then
  echo "ERROR: deployed but could not resolve the service URL." >&2
  exit 1
fi

echo "[3/3] Setting ALLOWED_HOST_ORIGINS..."
if [[ -z "$HOST_ORIGINS" ]]; then
  echo "      No --host-origins given; skipping."
  echo "      The sandbox rejects postMessage from any origin not in this list,"
  echo "      so artefacts stay blank until you set it to your frontend origin."
else
  # ^@^ delimiter override: the value contains commas, which gcloud would
  # otherwise split into separate env vars (the G18 foot-gun).
  gcloud run services update "${SERVICE}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --set-env-vars="^@^ALLOWED_HOST_ORIGINS=${HOST_ORIGINS}" \
    --quiet >/dev/null
  echo "      Set to: ${HOST_ORIGINS}"
fi

echo ""
echo "==> Done. Sandbox URL: ${SANDBOX_URL}"
echo ""
echo "    Set on the FRONTEND build (it is a NEXT_PUBLIC_ var, so it must be"
echo "    present at BUILD time, not just at runtime):"
echo "      NEXT_PUBLIC_MCP_SANDBOX_URL=${SANDBOX_URL}"
echo ""
echo "    Smoke it with:"
echo "      curl -sS -o /dev/null -w '%{http_code}\\n' ${SANDBOX_URL}/sandbox.html"
echo "    Expect 200. Do NOT probe /healthz — Cloud Run's GFE intercepts that"
echo "    path for its own probes, so your container never sees the request."
