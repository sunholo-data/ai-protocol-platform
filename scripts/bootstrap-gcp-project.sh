#!/usr/bin/env bash
# scripts/bootstrap-gcp-project.sh
#
# Run ONCE per new GCP project before creating Cloud Build triggers.
#
# Companion: scripts/bootstrap-env.sh does the PER-ENV provisioning (APIs,
# artifact bucket, secrets, Agent Engine) and is re-runnable. This script is
# the one-time Cloud Build enablement that must happen first.
# Post-2024 GCP projects no longer auto-provision the Cloud Build service
# agent or grant it the permissions it needs — this script does that.
# See docs/ops/gotchas.md for the full explanation.
#
# Usage:
#   ./scripts/bootstrap-gcp-project.sh <project-id> <runtime-sa-email>
#
# Example:
#   ./scripts/bootstrap-gcp-project.sh my-fork-dev \
#     platform@my-fork-dev.iam.gserviceaccount.com
#
# Prerequisites:
#   - gcloud authenticated as an Owner or Editor of <project-id>
#   - Cloud Build API enabled: gcloud services enable cloudbuild.googleapis.com
#   - Cloud Storage API enabled (for log bucket)

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <project-id> <runtime-sa-email>}"
RUNTIME_SA="${2:?Usage: $0 <project-id> <runtime-sa-email>}"
REGION="${3:-europe-west1}"

echo "==> Bootstrapping Cloud Build for project: ${PROJECT_ID}"
echo "    Runtime SA : ${RUNTIME_SA}"
echo "    Region     : ${REGION}"
echo ""

# 1. Materialize the Cloud Build service agent.
#    Post-2024 projects don't auto-create this; without it, trigger creation
#    fails with an opaque INVALID_ARGUMENT error.
echo "[1/2] Materializing Cloud Build service agent..."
gcloud beta services identity create \
  --service=cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" \
  --format='value(projectNumber)')
CB_SA="service-${PROJECT_NUMBER}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
echo "      Cloud Build SA: ${CB_SA}"

# 2. Grant the Cloud Build SA permission to impersonate the runtime SA.
#    Required so Cloud Build can deploy Cloud Run services using the
#    runtime SA's identity.
echo "[2/2] Granting Cloud Build SA iam.serviceAccountUser on runtime SA..."
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${CB_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --project="${PROJECT_ID}"

# Runtime buckets are NOT created here — terraform owns them.
#
#   ${PROJECT_ID}-cloudbuild-logs  — Cloud Build logsBucket for app-deploy builds
#   ${PROJECT_ID}-artifacts        — ADK GCS artifact service (ADK_ARTIFACT_BUCKET)
#   ${PROJECT_ID}-platform-logs   — app GenAI/OTEL log bucket (LOGS_BUCKET_NAME)
#
# All three (and the cross-project Cloud Build SA's objectCreator grant on
# the log bucket) are declared in
#   multivac-aitana/infrastructure/environments/<env>/runtime_buckets.tf
# For a fresh env the FIRST terraform apply creates them; existing envs adopt
# them via import blocks. This ordering is safe because the terraform apply
# runs before any app-deploy build (which is what needs cloudbuild-logs) and
# before the backend serves its first chat turn (which is what needs
# -artifacts; a missing artifact bucket 404s load_artifacts_tool and hangs
# every turn — hit on test 2026-07-09, see docs/ops/env-cut-runbook.md).
#
# Do NOT create these here: on a fresh fork prod's runtime_buckets.tf CREATES
# them fresh, so a bootstrap pre-create would 409 the terraform apply. And do
# NOT add a manual bucket-level IAM grant for the runtime SA — it already holds
# project-level roles/storage.objectAdmin via the terraform SA roles (that's
# how dev's artifact bucket works), and a manual grant is exactly the drift the
# 'no manual IAM' rule forbids.
echo ""
echo "==> Bootstrap complete. Runtime buckets are created by terraform"
echo "    (environments/<env>/runtime_buckets.tf) on the first apply."

echo ""
echo "==> Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Register the GitHub repository with Cloud Build v2:"
echo "     gcloud builds repositories create <repo-name> \\"
echo "       --remote-uri=https://github.com/<org>/<repo> \\"
echo "       --connection=<connection-name> \\"
echo "       --project=${PROJECT_ID} --region=${REGION}"
echo "     NOTE: the GitHub account authorizing the connection needs 'admin'"
echo "     on the repository (not just 'push') — see docs/ops/gotchas.md #8."
echo ""
echo "  2. Create the main Cloud Build trigger (deploys frontend + backend):"
echo "     gcloud builds triggers create github \\"
echo "       --name=<service>-<env> \\"
echo "       --service-account=projects/${PROJECT_ID}/serviceAccounts/${RUNTIME_SA} \\"
echo "       --build-config=cloudbuild.yaml \\"
echo "       ... (see cloudbuild.yaml for substitutions)"
echo ""
echo "  3. G37 (template-cloudbuild-hardening.md): create a SEPARATE trigger"
echo "     for the mcp-sandbox service. Without this, edits to"
echo "     infrastructure/mcp-sandbox/artefacts/** never reach the deployed"
echo "     iframe — the host shell updates but the iframe content is stale,"
echo "     and the fork user assumes the deploy didn't happen."
echo "     gcloud builds triggers create github \\"
echo "       --name=mcp-sandbox-<env> \\"
echo "       --service-account=projects/${PROJECT_ID}/serviceAccounts/${RUNTIME_SA} \\"
echo "       --build-config=infrastructure/mcp-sandbox/cloudbuild.yaml \\"
echo "       --included-files='infrastructure/mcp-sandbox/**' \\"
echo "       --branch-pattern=^<branch>\$"
echo ""
echo "  4. Set channel flags in Terraform substitutions if needed:"
echo "     _ENABLE_ANTHROPIC = true"
echo "     _ENABLE_TELEGRAM  = true  # only if TELEGRAM_BOT_TOKEN secret exists"
echo ""
echo "  5. Create the runtime resources this app expects (G13). Each of these"
echo "     is optional-but-silent: skip one and you get a cryptic runtime error"
echo "     rather than a startup failure, which is the single worst part of"
echo "     bringing up a fork. All four are idempotent."
echo ""
echo "     a. ADK artifact bucket — MISSING => every turn that touches"
echo "        load_artifacts 404s and hangs:"
echo "        ./scripts/create-artifact-bucket.sh --project ${PROJECT_ID} \\"
echo "          --region ${REGION} --sa ${RUNTIME_SA}"
echo ""
echo "     b. Vertex Agent Engine — only if you want sessions/memory to survive"
echo "        a restart. Leaving AGENT_ENGINE_ID UNSET is valid; setting it to a"
echo "        placeholder is NOT (400 on every chat turn):"
echo "        ./scripts/create-agent-engine.sh --project ${PROJECT_ID}"
echo ""
echo "     c. Vertex AI Search datastore — only if you enable an ai_search skill"
echo "        (e.g. knowledge-search):"
echo "        ./scripts/create-search-datastore.sh --project ${PROJECT_ID} \\"
echo "          --sa ${RUNTIME_SA}"
echo ""
echo "     d. MCP App sandbox — only if you use MCP App artefacts. Must be a"
echo "        separate origin from the frontend:"
echo "        ./scripts/deploy-mcp-sandbox.sh --project ${PROJECT_ID} \\"
echo "          --region ${REGION} --host-origins https://<your-frontend-url>"
