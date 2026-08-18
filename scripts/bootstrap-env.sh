#!/usr/bin/env bash
# scripts/bootstrap-env.sh — bring up (or reconcile) one environment.
#
# Orchestrates the steps that Terraform CANNOT own, and fails fast on the ones
# a human must do in a browser. Everything else belongs in `infrastructure/`.
#
#   ./scripts/bootstrap-env.sh --project my-project-dev --region europe-west1
#
# IDEMPOTENT: every step is an `ensure_*` that checks before it creates, so
# re-running after a partial failure is safe and is the intended workflow.
#
# ── Why this script exists at all ────────────────────────────────────────────
# Three categories of work, and only the middle one is ours:
#
#   1. YOUR IaC owns     — APIs, SA + IAM, Firestore, buckets, Artifact
#                          Registry, secret SHELLS, Pub/Sub, Cloud Run.
#
#                          The template does NOT ship Terraform (decision
#                          2026-08-17: the public tier is the gcloud path;
#                          Terraform is the private deploy tier). This is not an
#                          omission you should go looking for a root module to
#                          fill — downstream feedback #17 was exactly that
#                          search, and the misdirection cost real time.
#
#                          The gcloud path here is COMPLETE: bootstrap-gcp-project.sh
#                          then this script gets you a deployed service. Adopt
#                          IaC when you outgrow it, on your own terms.
#   2. THIS SCRIPT owns  — things no IaC provider covers. The load-bearing
#                          one is the Vertex Agent Engine: verified absent from
#                          provider google v6.50.0 (1226 resources, zero
#                          matching `reasoning`/`agent_engine`). Without it,
#                          AGENT_ENGINE_ID is unset and chat history does not
#                          persist across restarts.
#   3. A HUMAN owns      — two OAuth/console handshakes that no API can perform
#                          on your behalf. `verify_prereqs` refuses to continue
#                          without them and prints the exact URL.
#
# ── Relationship to scripts/bootstrap-gcp-project.sh ────────────────────────
# They are not alternatives and the split is deliberate:
#
#   bootstrap-gcp-project.sh  ONE-TIME, per NEW project. Materialises the Cloud
#                             Build service agent (post-2024 projects do not
#                             auto-provision it) and lets it impersonate the
#                             runtime SA. Run it FIRST, once.
#   bootstrap-env.sh (this)   Per-env provisioning, re-runnable. Run it after,
#                             and again whenever you want to reconcile.
#
# ⚠️  NOT YET VERIFIED END-TO-END AGAINST A FRESH GCP PROJECT. The individual
#     create-*.sh scripts it calls were written from downstream failure reports
#     rather than from a live run. Treat first contact with a new project as a
#     debugging session, and please report what breaks.

set -euo pipefail

PROJECT=""
REGION="europe-west1"
CB_CONNECTION="${CB_CONNECTION:-github}"
SA_NAME="${SA_NAME:-platform}"
SKIP_PREREQS=0

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)        PROJECT="${2:?--project needs a value}"; shift 2 ;;
    --region)         REGION="${2:?--region needs a value}"; shift 2 ;;
    --connection)     CB_CONNECTION="${2:?--connection needs a value}"; shift 2 ;;
    --skip-prereqs)   SKIP_PREREQS=1; shift ;;   # escape hatch; you own the consequences
    -h|--help)        usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage 2 ;;
  esac
done

[[ -n "$PROJECT" ]] || { echo "ERROR: --project is required." >&2; exit 2; }

SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. Preconditions a human must satisfy ────────────────────────────────────
# Fail LOUD and EARLY with the exact URL. The alternative — letting the script
# run and break at step 9 — is how an operator loses an afternoon.
verify_prereqs() {
  log "Verifying preconditions on ${PROJECT}"

  gcloud projects describe "$PROJECT" --format='value(lifecycleState)' 2>/dev/null \
    | grep -q ACTIVE || die "Project ${PROJECT} is not ACTIVE (or you cannot see it)."

  gcloud beta billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null \
    | grep -qi true || die \
    "Project ${PROJECT} has no active billing account.
     Cloud Run, Vertex AI and Artifact Registry are ALL unavailable without it.
     Needs roles/billing.user on the billing account."

  # CLICK-OPS #1 — installing the Cloud Build GitHub App is a browser OAuth
  # flow. Terraform can adopt the connection afterwards via `terraform import`,
  # but it cannot click a consent screen.
  gcloud builds connections describe "$CB_CONNECTION" \
    --region="$REGION" --project="$PROJECT" --format='value(installationState.stage)' 2>/dev/null \
    | grep -q COMPLETE || die \
    "Cloud Build connection '${CB_CONNECTION}' in ${REGION} is not COMPLETE.
     Install the Google Cloud Build GitHub App on your GitHub org and
     authorise it for this repo (the authorising account needs ADMIN on the
     repo, not just push — Cloud Build v2 sets up webhooks server-side):
       https://console.cloud.google.com/cloud-build/repositories/2nd-gen?project=${PROJECT}
     Then re-run. Afterwards, import it so Terraform owns it:
       terraform import google_cloudbuildv2_connection.github \\
         projects/${PROJECT}/locations/${REGION}/connections/${CB_CONNECTION}"

  # CLICK-OPS #2 — easy to miss, because everything AFTER it is scripted.
  if command -v firebase >/dev/null 2>&1; then
    firebase projects:list 2>/dev/null | grep -q "$PROJECT" || die \
      "Firebase has not been added to ${PROJECT}.
       Convert the project at https://console.firebase.google.com/ then re-run."
  else
    info "WARN: firebase CLI not installed — cannot verify Firebase is linked."
    info "      If auth misbehaves later, this is the first thing to check."
  fi

  # The Cloud Build SERVICE AGENT is a different thing from the connection, and
  # post-2024 projects do not auto-provision it. Its absence surfaces later as
  # an opaque INVALID_ARGUMENT on trigger creation, so check it here instead.
  local project_number cb_agent
  project_number="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null || true)"
  cb_agent="service-${project_number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
  if [[ -n "$project_number" ]] && ! gcloud iam service-accounts describe "$cb_agent" \
       --project="$PROJECT" >/dev/null 2>&1; then
    info "WARN: Cloud Build service agent not found (${cb_agent})."
    info "      Run scripts/bootstrap-gcp-project.sh ${PROJECT} <runtime-sa-email> first,"
    info "      or trigger creation will fail with an opaque INVALID_ARGUMENT."
  fi

  info "✓ project active + billed · Cloud Build connection COMPLETE · Firebase linked"
}

# ── 2. Steps with no Terraform resource ──────────────────────────────────────
ensure_apis() {
  log "Enabling APIs (idempotent)"
  # serviceusage must already be on for this to work — that is why it is a
  # precondition rather than a step.
  gcloud services enable \
    run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    firestore.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
    aiplatform.googleapis.com discoveryengine.googleapis.com \
    pubsub.googleapis.com cloudscheduler.googleapis.com eventarc.googleapis.com \
    identitytoolkit.googleapis.com firebase.googleapis.com \
    --project="$PROJECT"
  info "✓ APIs enabled"
}

ensure_agent_engine() {
  log "Vertex AI Agent Engine"
  # THE reason this script exists. No Terraform resource exists for a
  # reasoning engine, so this is script-or-nothing.
  if [[ -n "${AGENT_ENGINE_ID:-}" ]]; then
    info "AGENT_ENGINE_ID already set in env — skipping creation."
    return 0
  fi
  bash "${REPO_ROOT}/scripts/create-agent-engine.sh" --project "$PROJECT" --region "$REGION"
  info "Set the printed AGENT_ENGINE_ID on the backend service (or in Secret Manager)."
  info "Leaving it UNSET is valid — sessions stay in-memory and do not persist."
}

ensure_artifact_bucket() {
  log "ADK artifact bucket"
  # Missing bucket => every turn touching load_artifacts 404s and HANGS, with
  # no useful error. Cheap to create, expensive to debug.
  bash "${REPO_ROOT}/scripts/create-artifact-bucket.sh" \
    --project "$PROJECT" --region "$REGION" --sa "$SA_EMAIL"
}

ensure_group_auth_secret() {
  log "GROUP_AUTH_SIGNING_SECRET"
  # Terraform can own the shell AND a random value, but generating it here
  # keeps the seed self-contained. Rotating this invalidates all live tokens.
  if gcloud secrets describe GROUP_AUTH_SIGNING_SECRET --project="$PROJECT" >/dev/null 2>&1; then
    info "✓ already exists (not rotating — that would invalidate live tokens)"
    return 0
  fi
  gcloud secrets create GROUP_AUTH_SIGNING_SECRET --replication-policy=automatic --project="$PROJECT"
  openssl rand -base64 48 \
    | gcloud secrets versions add GROUP_AUTH_SIGNING_SECRET --data-file=- --project="$PROJECT"
  info "✓ created with a random value"
}

# ── 3. What is still owed after this script ─────────────────────────────────
# Printed rather than silently assumed. A fresh env should TELL the operator
# what is left, not let them discover it when something fails oddly.
post_apply_todo() {
  log "Still manual — do these next"
  cat <<EOF
    1. terraform import the Cloud Build connection (command in verify_prereqs above)
    2. terraform apply (if you manage this env with Terraform)
    3. Populate real secret VALUES (Terraform creates shells; values are external)
    4. Seed platform skills into Firestore
    5. Deploy the MCP sandbox if using MCP App artefacts:
         ./scripts/deploy-mcp-sandbox.sh --project ${PROJECT} --region ${REGION} \\
           --host-origins https://<your-frontend-url>
    6. Create a search datastore if using an ai_search skill:
         ./scripts/create-search-datastore.sh --project ${PROJECT} --sa ${SA_EMAIL}
EOF
}

main() {
  log "Env bootstrap — project=${PROJECT} region=${REGION}"
  if [[ "$SKIP_PREREQS" == "1" ]]; then
    info "WARN: --skip-prereqs set. Later steps may fail in confusing ways."
  else
    verify_prereqs
  fi
  ensure_apis
  ensure_artifact_bucket
  ensure_group_auth_secret
  ensure_agent_engine
  post_apply_todo
  log "Done."
}

main "$@"
