#!/usr/bin/env bash
#
# THE service spec for the multi-container frontend (ui + backend + toolbox).
#
# WHY THIS FILE EXISTS. Until v6.44.0 this ~170-line `gcloud run deploy` lived
# inline in cloudbuild.yaml, and cloudbuild.promote.yaml deliberately did NOT
# reproduce it — it ran `services update --image` and nothing else, because
# duplicating ~30 env vars and the whole secret set in a second pipeline would
# invite exactly the drift this repo keeps getting bitten by. That reasoning was
# right about duplication and wrong about the consequence: it left prod's SPEC
# with no deployment route at all once the prod branch trigger stopped being the
# way prod ships. On 2026-09-04 prod was found running v6.34.1's spec — no
# `toolbox` container at all, and five env vars (BROWSER_SERVICE_*,
# RESIDENT_AGENT_*) that the v6.43.x code reads simply absent — because every
# promotion since had been images-only, by design.
#
# The fix is not to duplicate the surface. It is to have ONE copy of it, here,
# and let both pipelines call it. Promotion can then carry the whole service
# state — which is what "the artifact test verified" was always supposed to mean
# — with no second definition to drift from.
#
# The move also lifts a real constraint: Cloud Build caps ONE step arg at 10,000
# characters, the inline script had reached ~9.6K, and prose had to be exiled to
# YAML comments to buy headroom (see the comment block above the deploy step in
# cloudbuild.yaml). A shell file has no such cap, so an explanation can live next
# to the line it explains again.
#
# QUOTING — the one thing that silently changed meaning when this left the YAML.
# In cloudbuild.yaml, Cloud Build expanded ${_FOO} TEXTUALLY before any shell saw
# the line, so single quotes around a substitution were harmless. Here the SHELL
# must expand it, and single quotes stop it dead: gcloud then receives the literal
# string "${_ADMIN_SEED_ALLOWED_SAS}". That is not a crash. It is a deploy that
# SUCCEEDS with a wrong value, which is worse. It cost a dev deploy on 2026-09-04
# — the seed step 403'd, and AUTH_OPERATOR_DOMAINS was literal too, which on an
# env with AUTH_REQUIRE_KNOWN_DOMAIN=1 locks out every operator.
#
# Both affected flags now use DOUBLE quotes. The `^|^` prefix is gcloud's
# custom-delimiter syntax (use | instead of , between pairs), needed because both
# values contain commas; double quotes protect the | from the shell while still
# expanding the variable. backend/tests/unit/test_deploy_spec_quoting.py enforces
# that no substitution is ever single-quoted here again.
#
# CONTRACT. Every `_`-prefixed variable below is a Cloud Build substitution
# passed through as an environment variable of the SAME NAME. That is deliberate:
# `grep _BROWSER_SERVICE_URL` finds the trigger substitution, the YAML that
# forwards it, and the flag that consumes it, with no mental translation. Their
# per-env values are terraform-managed (deployment-infrastructure `global_substitutions`
# plus deployment-config `run_client.tfvars`); nothing here invents one.
#
# The three image refs are read from ${IMAGE_DIR}/image_{ui,backend,toolbox},
# written by the calling pipeline's push steps (cloudbuild.yaml) or copy steps
# (cloudbuild.promote.yaml). They are always repo@sha256:… — deploy by DIGEST,
# never by a branch tag (v6.20.0, AIPLA #47): a concurrent build can move a tag
# between the push and this deploy, and a mutable tag cannot be rolled back to.

set -euo pipefail

IMAGE_DIR="${IMAGE_DIR:-/workspace}"

# Fail loudly and by name. An unset substitution used to render as an empty
# string and deploy a subtly wrong env — the failure then surfaced hours later
# in someone's chat turn, not here. `set -u` alone would name only the first
# one, so collect them all and report the set.
missing=()
for v in _PROJECT_ID _REGION _SERVICE_NAME _RESOURCE_PREFIX \
         _ENABLE_ANTHROPIC _ENABLE_OPENAI _ENABLE_TELEGRAM _ENABLE_WHATSAPP _ENABLE_EMAIL \
         _ALLOW_NEW_SERVICE _ADMIN_SEED_ALLOWED_SAS _PLATFORM_OWNER_EMAIL \
         _VERTEX_AI_SEARCH_DATASTORE_ID _VERTEX_AI_SEARCH_PROJECT _VERTEX_AI_SEARCH_LOCATION _A2A_AGENT_DOCUMENTS_BUCKET \
         _AUTH_OPERATOR_DOMAINS _INCLUDE_DEMO_SKILLS _ENTSOE_PROJECT \
         _MODEL_RESIDENCY_POLICY _SLOW_STREAM_PROBE_ENABLED _COMPACTION_SECOND_PASS \
         _COMPACTION_TASKS_TARGET_URL _MEMORY_AUTOSAVE _BROWSER_SERVICE_ENABLED _BROWSER_SERVICE_URL \
         _VISUAL_EVIDENCE_ENABLED _VISUAL_EVIDENCE_ALLOWED_UIDS \
         _VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS _VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS \
         _VISUAL_EVIDENCE_MODEL _VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS \
         _RESIDENT_AGENT_URL _RESIDENT_START_ENDPOINT _RESIDENT_AGENT_PER_USER \
         _RESIDENT_SESSION_STORY_ENABLED \
         _RESIDENT_PUSH_BASE_URL _PUBLIC_BASE_URL; do
  [ -z "${!v+set}" ] && missing+=("$v")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: deploy-run-service.sh called without: ${missing[*]}" >&2
  echo "Each is a Cloud Build substitution forwarded as an env var of the same name." >&2
  echo "An empty value is legal (several are deliberately empty); UNSET is not." >&2
  exit 1
fi

# Deploy by DIGEST, never by the branch tag (v6.20.0, AIPLA #47): a
# concurrent build can move that tag between our push and this deploy,
# and a mutable tag cannot be rolled back to. The push steps resolved
# these and failed the build if any was empty.
UI_IMAGE=$(cat "${IMAGE_DIR}/image_ui")
BACKEND_IMAGE=$(cat "${IMAGE_DIR}/image_backend")
TOOLBOX_IMAGE=$(cat "${IMAGE_DIR}/image_toolbox")
echo "Deploying ui=${UI_IMAGE} backend=${BACKEND_IMAGE} toolbox=${TOOLBOX_IMAGE}"
# Sidecar secrets, always mounted — see the note above this step.
SECRETS="AGENT_ENGINE_ID=AGENT_ENGINE_ID:latest,DOCPARSE_API_KEY=DOCPARSE_API_KEY:latest,MAPS_GROUNDING_API_KEY=MAPS_GROUNDING_API_KEY:latest"
# AI model key — almost always needed; set _ENABLE_ANTHROPIC=false to skip
if [ "${_ENABLE_ANTHROPIC}" = "true" ]; then
  SECRETS="${SECRETS},ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"
fi
# OpenAI (GPT-tier skills). Off by default — the OPENAI_API_KEY +
# OPENAI_API_BASE secrets must exist in this env before enabling, else
# the deploy fails mounting a missing secret. Without it, GPT-pinned
# skills fall back to gemini (models.yaml ladder).
if [ "${_ENABLE_OPENAI}" = "true" ]; then
  SECRETS="${SECRETS},OPENAI_API_KEY=OPENAI_API_KEY:latest,OPENAI_API_BASE=OPENAI_API_BASE:latest"
fi
# Channel secrets — only wired when the channel is enabled
if [ "${_ENABLE_TELEGRAM}" = "true" ]; then
  SECRETS="${SECRETS},TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest"
fi
if [ "${_ENABLE_WHATSAPP}" = "true" ]; then
  SECRETS="${SECRETS},TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest,TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest"
fi
if [ "${_ENABLE_EMAIL}" = "true" ]; then
  SECRETS="${SECRETS},MAILGUN_API_KEY=MAILGUN_API_KEY:latest,MAILGUN_WEBHOOK_SECRET=MAILGUN_WEBHOOK_SECRET:latest"
fi
# ENV_TIER derivation + AI_SEARCH_SRC_BUCKET remap: see YAML comment above.
case "${_PROJECT_ID}" in
  *-dev)        ENV_TIER=dev ;;
  *-test)       ENV_TIER=test ;;
  *-production) ENV_TIER=prod ;;
  *)            ENV_TIER=unknown ;;
esac
# Refuse to deploy under the template's placeholder name. Without this
# a missing _SERVICE_NAME substitution silently stands up a duplicate
# service beside the live one instead of updating it.
if [ "${_SERVICE_NAME}" = "terraform_managed" ]; then
  echo "ERROR: _SERVICE_NAME is still the template placeholder."
  echo "Set it in this environment's Cloud Build trigger substitutions."
  echo "See traps.md #23 — that is TWO triggers, branch and tag-release."
  exit 1
fi
# Guard the hazard, not the name: refuse to CREATE a service beside the
# live one. A wrong substitution is indistinguishable from a legitimate
# name, but it is always wrong to stand up a new service by accident.
if ! gcloud run services describe "${_SERVICE_NAME}" \
     --project="${_PROJECT_ID}" --region="${_REGION}" >/dev/null 2>&1; then
  if [ "${_ALLOW_NEW_SERVICE}" != "true" ]; then
    echo "ERROR: service '${_SERVICE_NAME}' does not exist — deploying would CREATE it."
    echo "Fix the trigger substitution, or set _ALLOW_NEW_SERVICE=true for a new env."
    exit 1
  fi
  echo "NOTE: creating new service '${_SERVICE_NAME}' (_ALLOW_NEW_SERVICE=true)."
fi
# Only pass PUBLIC_BASE_URL when this env supplies one — an empty
# --set-env-vars=PUBLIC_BASE_URL= would blank it rather than omit it.
if [ -n "${_PUBLIC_BASE_URL}" ]; then
  PUBLIC_BASE_URL_FLAG="--set-env-vars=PUBLIC_BASE_URL=${_PUBLIC_BASE_URL}"
else
  PUBLIC_BASE_URL_FLAG=""
fi
case "${ENV_TIER}" in
  dev|test) AI_SEARCH_SRC_BUCKET=${_PROJECT_ID}-${ENV_TIER}-llmops-bucket ;;
  *)        AI_SEARCH_SRC_BUCKET="" ;;
esac
# v6.18.0 Gap B: restrict authentication to the deployment's operator +
# mapped-tenant domains. On for every known tier since 2026-09-14 — prod was
# deferred "until the #31 flip" and then ran ten days with real customer users
# as the LEAST locked-down env; the precondition (the operator domain's own
# prod bucket, clients/<operator-domain>.documents_bucket) is now set. Gap A
# (bucket authz) is on-by-default and needs no flag. An unknown tier stays
# open: a fork's first deploy must not 403 everyone before it has mapped a
# single tenant.
#
# TEMPLATE-INVERT M3: operator domains are DEPLOYMENT IDENTITY and no
# longer have a code default — backend/auth/__init__.py defaults to
# empty so a fork cannot inherit ours (AIPLA #42). This pipeline is
# therefore the sole supplier: without the _AUTH_OPERATOR_DOMAINS
# substitution below, dev/test would 403 every operator, because both
# set AUTH_REQUIRE_KNOWN_DOMAIN=1.
case "${ENV_TIER}" in
  dev|test|prod) AUTH_REQUIRE_KNOWN_DOMAIN=1 ;;
  *)             AUTH_REQUIRE_KNOWN_DOMAIN=0 ;;
esac
# v6.18.0: deny an unmapped tenant the shared fallback bucket (read AND
# write) — closes the shared-bucket exposure alongside Gap-A/B. On for every
# known tier since 2026-09-14 (prod was frozen at 0; see above). An unmapped
# domain now gets a visible 403 on documents rather than a silent shared
# bucket. Coordinated with #31 (write-side UX already shipped).
case "${ENV_TIER}" in
  dev|test|prod) TENANT_FALLBACK_FAIL_CLOSED=1 ;;
  *)             TENANT_FALLBACK_FAIL_CLOSED=0 ;;
esac
# A2A invocation auth — prod true. See YAML comment above this step.
case "${ENV_TIER}" in
  dev|test) A2A_INVOCATION_REQUIRE_AUTH=false ;;
  *)        A2A_INVOCATION_REQUIRE_AUTH=true ;;
esac
# Warm instances. Prod keeps ONE always-on so a customer never pays the cold
# start; dev and test scale to zero because nobody is waiting on them and a warm
# 3-container instance (main 1cpu/2Gi + sidecar 2cpu/4Gi + toolbox 1cpu/512Mi) is
# billed around the clock whether or not anyone calls it.
#
# Set EXPLICITLY for every tier rather than omitted for the zero case: an omitted
# flag means "leave whatever is there", so a value set by hand once would survive
# every subsequent deploy and never appear in this file. Stating both makes the
# spec the source of truth, which is the whole point of this script.
#
# Keyed on ENV_TIER, which is derived from _PROJECT_ID above — never on a ref
# name. BRANCH_NAME is empty on a tag build and TAG_NAME on a branch build, and
# that asymmetry has silently mis-set env-conditionals here before.
case "${ENV_TIER}" in
  prod) MIN_INSTANCES=1 ;;
  *)    MIN_INSTANCES=0 ;;
esac
gcloud run deploy ${_SERVICE_NAME} \
  --region=${_REGION} \
  --project=${_PROJECT_ID} \
  --platform=managed \
  --allow-unauthenticated \
  --max-instances=3 \
  --min-instances=${MIN_INSTANCES} \
  --session-affinity \
  --timeout=3600 \
  --container=main \
  --image=${UI_IMAGE} \
  --port=8080 \
  --memory=2Gi \
  --cpu=1 \
  --set-env-vars=BACKEND_URL=http://127.0.0.1:1956 \
  --container=sidecar \
  --image=${BACKEND_IMAGE} \
  --memory=4Gi \
  --cpu=2 \
  --startup-probe=tcpSocket.port=1956,periodSeconds=5,failureThreshold=24,timeoutSeconds=5 \
  --set-secrets="${SECRETS}" \
  --set-env-vars=TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886 \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=${_PROJECT_ID} \
  --set-env-vars=GOOGLE_CLOUD_LOCATION=${_REGION} \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=True \
  --set-env-vars=ADK_ARTIFACT_BUCKET=${_PROJECT_ID}-artifacts \
  --set-env-vars=LOGS_BUCKET_NAME=${_PROJECT_ID}-${_RESOURCE_PREFIX}-logs \
  --set-env-vars=GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true \
  --set-env-vars=OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true \
  --set-env-vars=OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental \
  --set-env-vars="^|^ADMIN_SEED_ALLOWED_SAS=${_ADMIN_SEED_ALLOWED_SAS}" \
  --set-env-vars=PLATFORM_OWNER_EMAIL=${_PLATFORM_OWNER_EMAIL} \
  --set-env-vars=_INCLUDE_DEMO_SKILLS=${_INCLUDE_DEMO_SKILLS} \
  --set-env-vars=RAG_DOCUMENTS_ENABLED=true \
  --set-env-vars=BROWSER_SERVICE_ENABLED=${_BROWSER_SERVICE_ENABLED} \
  --set-env-vars=BROWSER_SERVICE_URL=${_BROWSER_SERVICE_URL} \
  --set-env-vars=VISUAL_EVIDENCE_ENABLED=${_VISUAL_EVIDENCE_ENABLED} \
  --set-env-vars="^|^VISUAL_EVIDENCE_ALLOWED_UIDS=${_VISUAL_EVIDENCE_ALLOWED_UIDS}" \
  --set-env-vars="^|^VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS=${_VISUAL_EVIDENCE_SYNTHETIC_SOURCE_DIGESTS}" \
  --set-env-vars="^|^VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS=${_VISUAL_EVIDENCE_SYNTHETIC_CAPTURE_PAIRS}" \
  --set-env-vars=VISUAL_EVIDENCE_MODEL=${_VISUAL_EVIDENCE_MODEL} \
  --set-env-vars="^|^VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS=${_VISUAL_EVIDENCE_APPROVED_MODEL_REGIONS}" \
  --set-env-vars=RESIDENT_AGENT_URL=${_RESIDENT_AGENT_URL} \
  --set-env-vars=RESIDENT_START_ENDPOINT=${_RESIDENT_START_ENDPOINT} \
  --set-env-vars=RESIDENT_AGENT_PER_USER=${_RESIDENT_AGENT_PER_USER} \
  --set-env-vars=RESIDENT_SESSION_STORY_ENABLED=${_RESIDENT_SESSION_STORY_ENABLED} \
  --set-env-vars=RESIDENT_PUSH_BASE_URL=${_RESIDENT_PUSH_BASE_URL} \
  --set-env-vars=VERTEX_AI_SEARCH_DATASTORE_ID=${_VERTEX_AI_SEARCH_DATASTORE_ID} \
  --set-env-vars=VERTEX_AI_SEARCH_PROJECT=${_VERTEX_AI_SEARCH_PROJECT} \
  --set-env-vars=VERTEX_AI_SEARCH_LOCATION=${_VERTEX_AI_SEARCH_LOCATION} \
  --set-env-vars=AI_SEARCH_SOURCE_BUCKET_OVERRIDE=${AI_SEARCH_SRC_BUCKET} \
  --set-env-vars=AUTH_REQUIRE_KNOWN_DOMAIN=${AUTH_REQUIRE_KNOWN_DOMAIN} \
  --set-env-vars="^|^AUTH_OPERATOR_DOMAINS=${_AUTH_OPERATOR_DOMAINS}" \
  --set-env-vars=ENTSOE_PROJECT=${_ENTSOE_PROJECT} \
  --set-env-vars=TENANT_FALLBACK_FAIL_CLOSED=${TENANT_FALLBACK_FAIL_CLOSED} \
  --set-env-vars=SLOW_STREAM_PROBE_ENABLED=${_SLOW_STREAM_PROBE_ENABLED} \
  --set-env-vars=MODEL_RESIDENCY_POLICY=${_MODEL_RESIDENCY_POLICY} \
  --set-env-vars=COMPACTION_SECOND_PASS_ENABLED=${_COMPACTION_SECOND_PASS} \
  --set-env-vars=COMPACTION_TASKS_QUEUE=projects/${_PROJECT_ID}/locations/${_REGION}/queues/${_RESOURCE_PREFIX}-compaction \
  --set-env-vars=COMPACTION_TASKS_OIDC_SA=${_RESOURCE_PREFIX}-tasks@${_PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars=COMPACTION_TASKS_TARGET_URL=${_COMPACTION_TASKS_TARGET_URL} \
  --set-env-vars=MEMORY_AUTOSAVE=${_MEMORY_AUTOSAVE} \
  --set-env-vars=ENABLE_A2A_INVOCATION=true \
  --set-env-vars=A2A_INVOCATION_REQUIRE_AUTH=${A2A_INVOCATION_REQUIRE_AUTH} \
  ${PUBLIC_BASE_URL_FLAG} \
  --set-env-vars=ENABLE_A2A_FILE_INPUT=true \
  --set-env-vars=A2A_AGENT_DOCUMENTS_BUCKET=${_A2A_AGENT_DOCUMENTS_BUCKET} \
  --container=toolbox \
  --image=${TOOLBOX_IMAGE} \
  --memory=512Mi \
  --cpu=1 \
  --set-env-vars=TOOLBOX_BQ_PROJECT=${_PROJECT_ID} \
  --startup-probe=tcpSocket.port=5000,periodSeconds=5,failureThreshold=24,timeoutSeconds=5 \
  --args=--config=/app/tools.yaml,--address=0.0.0.0,--port=5000,--disable-reload
