#!/usr/bin/env bash
# scripts/setup-gemini-enterprise.sh — bootstrap a fresh GCP project ready
# to host a Gemini Enterprise app + register this platform's A2A agent.
#
# Walks the operator through the parts that ARE scriptable:
#   1. Create the GCP project (skips if it exists)
#   2. Link it to a billing account
#   3. Enable Discovery Engine + Cloud AI Companion + Dialogflow APIs
#   4. Create a placeholder data store + Gemini Enterprise app (engine)
#      — required BEFORE the subscribe page in Console will resolve
#   5. Pause for the human-only subscription purchase in Console
#   6. Verify the deployed card + print the exact agents-cli register
#      command (with your engine resource path filled in)
#
# Two things the script CANNOT do (deliberate Console-only gates):
#   * Buy the per-seat subscription (Standard ~$30/user/mo) — no public
#     API for the buy click
#   * Reach the subscribe page before an App exists (the page 404s until
#     step 4 completes)
#
# Re-runnable: each step short-circuits if its target already exists.
# Safe to abort and resume at any point.
#
# Usage:
#
#   # Required env (or prompted if interactive):
#   #   PROJECT_ID       — new project ID (e.g. yourorg-gemini-enterprise)
#   #   ORG_ID           — GCP organization ID (gcloud organizations list)
#   #   BILLING_ACCOUNT  — billing account ID (gcloud billing accounts list)
#   #
#   # Optional:
#   #   PROJECT_NAME     — display name (default: derived from PROJECT_ID)
#   #   AP_URL           — your deployed platform URL, for verify-a2a step
#   #   APP_DISPLAY_NAME — Gemini Enterprise app display name (default: PROJECT_ID)
#   #   APP_ID           — engine ID (default: derived from PROJECT_ID + "-demo-app")
#   #   DATASTORE_ID     — data store ID (default: derived from PROJECT_ID + "-demo-ds")
#   #   BUSINESS_NAME    — chat engine business name (default: PROJECT_NAME)
#   #   TIME_ZONE        — chat engine TZ (default: UTC)
#   #
#   PROJECT_ID=yourorg-gemini-enterprise \
#   ORG_ID=$(gcloud organizations list --format='value(ID)' | head -1) \
#   BILLING_ACCOUNT=$(gcloud billing accounts list --filter='OPEN=True' --format='value(ACCOUNT_ID)' | head -1) \
#   AP_URL=https://your-deployed-platform.run.app \
#   ./scripts/setup-gemini-enterprise.sh
#
# Re-runnable: each step short-circuits if its target already exists.
# Safe to abort and resume at any point.

set -euo pipefail

# --- colors / helpers ----------------------------------------------------
ok()    { printf "  \033[32mOK\033[0m   %s\n" "$1"; }
info()  { printf "  \033[36m...\033[0m  %s\n" "$1"; }
warn()  { printf "  \033[33mWARN\033[0m %s\n" "$1"; }
fail()  { printf "  \033[31mFAIL\033[0m %s\n" "$1" >&2; exit 1; }
step()  { printf "\n\033[1m▶ %s\033[0m\n" "$1"; }
note()  { printf "  \033[35mNOTE\033[0m %s\n" "$1"; }

command -v gcloud >/dev/null 2>&1 || fail "gcloud not on PATH"

# --- inputs --------------------------------------------------------------
: "${PROJECT_ID:?set PROJECT_ID (e.g. yourorg-gemini-enterprise)}"
: "${ORG_ID:?set ORG_ID — see: gcloud organizations list}"
: "${BILLING_ACCOUNT:?set BILLING_ACCOUNT — see: gcloud billing accounts list}"

PROJECT_NAME="${PROJECT_NAME:-$PROJECT_ID}"
APP_DISPLAY_NAME="${APP_DISPLAY_NAME:-$PROJECT_ID}"
APP_ID="${APP_ID:-${PROJECT_ID%-*}-demo-app}"
DATASTORE_ID="${DATASTORE_ID:-${PROJECT_ID%-*}-demo-ds}"
BUSINESS_NAME="${BUSINESS_NAME:-$PROJECT_NAME}"
TIME_ZONE="${TIME_ZONE:-Europe/Copenhagen}"
AP_URL="${AP_URL:-}"

echo "Gemini Enterprise project bootstrap"
echo "  project:  $PROJECT_ID  (\"$PROJECT_NAME\")"
echo "  org:      $ORG_ID"
echo "  billing:  $BILLING_ACCOUNT"
[ -n "$AP_URL" ] && echo "  AP_URL:   $AP_URL"
echo

# --- 1. create project ---------------------------------------------------
step "1/5  Create project"
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  ok "project $PROJECT_ID already exists — skipping create"
else
  info "creating project $PROJECT_ID under org $ORG_ID …"
  gcloud projects create "$PROJECT_ID" \
    --organization="$ORG_ID" \
    --name="$PROJECT_NAME" \
    >/dev/null
  ok "project $PROJECT_ID created"
fi

# --- 2. link billing -----------------------------------------------------
step "2/5  Link billing account"
CURRENT_BA=$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)' 2>/dev/null | sed 's|billingAccounts/||')
if [ "$CURRENT_BA" = "$BILLING_ACCOUNT" ]; then
  ok "already linked to $BILLING_ACCOUNT — skipping"
elif [ -n "$CURRENT_BA" ]; then
  warn "project is linked to a DIFFERENT billing account: $CURRENT_BA"
  warn "  → re-linking to $BILLING_ACCOUNT (this changes who pays)"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
  ok "linked to $BILLING_ACCOUNT"
else
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
  ok "linked to $BILLING_ACCOUNT"
fi

# --- 3. enable APIs ------------------------------------------------------
step "3/6  Enable APIs"
# dialogflow is required for Gemini Enterprise app (engine) creation —
# the chatEngineConfig binds to a Dialogflow agent under the hood.
# Without it, engine POST returns 403 even though the API surface is
# discoveryengine.
APIS=(
  discoveryengine.googleapis.com
  cloudaicompanion.googleapis.com
  dialogflow.googleapis.com
)
for api in "${APIS[@]}"; do
  if gcloud services list --enabled --project="$PROJECT_ID" \
       --filter="config.name:$api" --format='value(config.name)' 2>/dev/null | grep -q "$api"; then
    ok "$api already enabled"
  else
    info "enabling $api …"
    gcloud services enable "$api" --project="$PROJECT_ID" >/dev/null
    ok "$api enabled"
  fi
done

# --- 4. create Gemini Enterprise App (HUMAN — Console wizard) ------------
# The Gemini Enterprise "App" is a Discovery Engine engine with the
# specific shape:
#   solutionType: SOLUTION_TYPE_SEARCH
#   searchEngineConfig.searchTier: SEARCH_TIER_ENTERPRISE
#   searchEngineConfig.searchAddOns: [SEARCH_ADD_ON_LLM]
#   searchEngineConfig.requiredSubscriptionTier: SUBSCRIPTION_TIER_SEARCH_AND_ASSISTANT
#   appType: APP_TYPE_INTRANET
#   knowledgeGraphConfig.enablePrivateKnowledgeGraph: true
# plus several `features` flags Google adjusts independently of the docs.
# The shape evolved twice in 9 months (2025-08, 2026-06) and the public
# Discovery Engine REST docs don't currently describe it. Rather than
# guess at an API shape that drifts under us, this script pauses here for
# the Console wizard — which is the source of truth for the current
# Gemini Enterprise app schema.
step "4/6  Create Gemini Enterprise App (HUMAN — Console wizard)"
note "The 'Create app' click is the only reliable way to produce a"
note "Gemini Enterprise app. Direct API calls produce engine variants"
note "(Chat / Vertex Search) that the GE Console hides and Discovery"
note "Engine registration doesn't accept."
echo
echo "  Open:"
echo "    https://console.cloud.google.com/gemini-enterprise/apps?project=$PROJECT_ID"
echo
echo "  Click 'Create a new app' / 'Create app'. Choose:"
echo "    Type:     Gemini Enterprise (the AI agent option)"
echo "    Location: global (recommended — matches agents-cli expectations)"
echo "    Name:     anything you like (Console auto-generates an engine ID"
echo "              like 'gemini-enterprise-<digits>_<digits>')"
echo
read -r -p "  Press Enter once the app shows in the Apps list … " _

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
TOKEN=$(gcloud auth print-access-token 2>/dev/null)
ENGINE_NAME=$(curl -s -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: $PROJECT_ID" \
  "https://discoveryengine.googleapis.com/v1/projects/$PROJECT_ID/locations/global/collections/default_collection/engines" \
  | python3 -c "
import sys, json
try:
  d = json.load(sys.stdin)
  engines = [e for e in d.get('engines', []) if e.get('solutionType') == 'SOLUTION_TYPE_SEARCH']
  print(engines[0]['name'] if engines else '')
except Exception:
  print('')
")
if [ -n "$ENGINE_NAME" ]; then
  ENGINE_PATH="$ENGINE_NAME"
  ok "found Gemini Enterprise app:"
  echo "    $ENGINE_PATH"
else
  warn "no SOLUTION_TYPE_SEARCH engine found — registration step will need you to"
  warn "  paste the resource path manually. Find it in the URL of the engine's"
  warn "  Overview page, after /engines/."
  ENGINE_PATH="projects/$PROJECT_NUMBER/locations/global/collections/default_collection/engines/<your-engine-id>"
fi

# --- 5. human-only subscription step -------------------------------------
step "5/6  Subscribe to Gemini Enterprise (HUMAN — Console only)"
note "Subscription purchase is Console-only (no public API for the buy click)."
note "The /manage-subscription page only resolves AFTER an App exists,"
note "which step 4 just took care of."
echo
echo "  Open (note: 'manage-subscription' singular, not 'manage-subscriptions'):"
echo "    https://console.cloud.google.com/gemini-enterprise/manage-subscription?project=$PROJECT_ID&billingAccountId=${BILLING_ACCOUNT}"
echo
echo "  Choose:"
echo "    Tier:    Standard  (\$30/user/mo annual, \$35/user/mo monthly)"
echo "             OR Plus   (\$50/user/mo annual, \$60/user/mo monthly — adds"
echo "             custom agent dev, NotebookLM authoring, Code Assist Standard)"
echo "    Seats:   start at 1, add as users join"
echo "    Billing: $BILLING_ACCOUNT"
echo
read -r -p "  Press Enter once the subscription is active … " _

# --- 6. verify-a2a + print register command ------------------------------
step "6/6  Verify + register"

if [ -n "$AP_URL" ]; then
  if [ -x "$(dirname "$0")/verify-a2a.sh" ]; then
    info "running verify-a2a against $AP_URL …"
    if AP_URL="$AP_URL" "$(dirname "$0")/verify-a2a.sh"; then
      ok "card is A2A-spec-compliant — safe to register"
    else
      fail "verify-a2a failed — fix card issues before attempting registration"
    fi
  else
    warn "verify-a2a.sh not executable; skipping pre-flight"
  fi
else
  warn "AP_URL not set — skipping verify-a2a pre-flight"
  warn "  → set AP_URL=https://<your-deployed-host> to verify before registering"
fi

echo
echo "Next: register the platform's A2A card with the new GE app."
echo
echo "  agents-cli register-gemini-enterprise \\"
echo "    --registration-type a2a \\"
echo "    --agent-card-url ${AP_URL:-https://<your-deployed-host>}/.well-known/agent.json \\"
echo "    --gemini-enterprise-app-id $ENGINE_PATH \\"
echo "    --display-name \"$APP_DISPLAY_NAME\" \\"
echo "    --deployment-target cloud_run"
echo
echo "See docs/integrations/gemini-enterprise.md for the full registration"
echo "walkthrough + troubleshooting for the failure modes Discovery Engine"
echo "rejects on (protocolVersion, AgentExtension shape, localhost url, …)."
echo
ok "project setup complete: $PROJECT_ID"
