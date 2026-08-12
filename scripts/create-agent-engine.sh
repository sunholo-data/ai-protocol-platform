#!/usr/bin/env bash
# scripts/create-agent-engine.sh
#
# Create a Vertex AI Agent Engine (ReasoningEngine) and print its full resource
# name for AGENT_ENGINE_ID. This is what backs VertexAiSessionService and
# VertexAiMemoryBankService — i.e. sessions and memory that survive a restart.
#
# WHY THIS EXISTS (template-fork-ergonomics.md G13a): backend/fast_api_app.py
# treats ANY truthy AGENT_ENGINE_ID as "use Vertex sessions". A fork that sets
# the secret to a placeholder (the reported case was the literal "dummy_value")
# gets this on every single chat turn:
#     400 INVALID_ARGUMENT. Invalid ReasoningEngine resource name
# Leaving AGENT_ENGINE_ID UNSET is a valid, working configuration — sessions
# are then in-memory and simply don't persist across restarts. Set it only once
# you have a real engine, which is what this script creates.
#
# Usage:
#   ./scripts/create-agent-engine.sh --project <project-id> [--region <region>]
#                                    [--display-name <name>]
#
# Idempotent: if an engine with the same display name exists, its resource name
# is printed instead of creating a second one.

set -euo pipefail

PROJECT_ID=""
# Agent Engine is not available in every region. europe-west1 hosts the rest of
# this stack; us-central1 is the widest-available fallback.
REGION="europe-west1"
DISPLAY_NAME="platform-agent-engine"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT_ID="${2:?--project needs a value}";        shift 2 ;;
    --region)       REGION="${2:?--region needs a value}";             shift 2 ;;
    --display-name) DISPLAY_NAME="${2:?--display-name needs a value}"; shift 2 ;;
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

API="https://${REGION}-aiplatform.googleapis.com/v1beta1"
PARENT="projects/${PROJECT_ID}/locations/${REGION}"

echo "==> Vertex AI Agent Engine"
echo "    Project      : ${PROJECT_ID}"
echo "    Region       : ${REGION}"
echo "    Display name : ${DISPLAY_NAME}"
echo ""

echo "[1/3] Ensuring the Vertex AI API is enabled..."
gcloud services enable aiplatform.googleapis.com --project="${PROJECT_ID}"

TOKEN="$(gcloud auth print-access-token)"

echo "[2/3] Checking for an existing engine with that display name..."
# Pass the display name through the environment rather than interpolating it
# into the Python source — a name containing a quote would otherwise break the
# script (or worse, execute).
EXISTING="$(curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "${API}/${PARENT}/reasoningEngines" 2>/dev/null \
  | WANT="${DISPLAY_NAME}" python3 -c "
import json, os, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
want = os.environ['WANT']
for engine in data.get('reasoningEngines', []):
    if engine.get('displayName') == want:
        print(engine.get('name', ''))
        break
" || true)"

if [[ -n "$EXISTING" ]]; then
  echo "      Found existing engine."
  ENGINE_NAME="$EXISTING"
else
  echo "[3/3] Creating engine..."
  # A ReasoningEngine with no `spec` is a valid session/memory backing store —
  # ADK only needs the resource to exist. We are NOT deploying agent code to
  # Vertex here; the agent runs in Cloud Run.
  # Build the JSON with a real encoder so a display name containing quotes or
  # backslashes produces valid JSON rather than a malformed request.
  PAYLOAD="$(WANT="${DISPLAY_NAME}" python3 -c \
    'import json, os; print(json.dumps({"displayName": os.environ["WANT"]}))')"

  RESPONSE="$(curl -sS -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" \
    "${API}/${PARENT}/reasoningEngines")"

  ENGINE_NAME="$(printf '%s' "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
# Creation is a long-running operation: the engine name is nested under
# response/metadata rather than returned directly.
name = (
    data.get('response', {}).get('name')
    or data.get('metadata', {}).get('genericMetadata', {}).get('resourceName')
    or data.get('name', '')
)
print(name)
" 2>/dev/null || true)"

  if [[ -z "$ENGINE_NAME" ]]; then
    echo "" >&2
    echo "ERROR: could not parse the engine name from the API response:" >&2
    printf '%s\n' "$RESPONSE" | head -40 >&2
    echo "" >&2
    echo "Agent Engine is not available in every region — if this says the" >&2
    echo "location is unsupported, retry with --region us-central1." >&2
    exit 1
  fi

  # An LRO name looks like .../operations/123; strip that to get the engine.
  ENGINE_NAME="${ENGINE_NAME%%/operations/*}"
fi

echo ""
echo "==> Done. Set this on the backend service:"
echo "      AGENT_ENGINE_ID=${ENGINE_NAME}"
echo ""
echo "    Use the FULL resource name above, not the bare numeric id — the"
echo "    region is parsed out of it to build the Vertex session + memory"
echo "    services. A bare id, a placeholder, or an id from another region"
echo "    all fail the same way: 400 Invalid ReasoningEngine resource name."
echo ""
echo "    Leaving AGENT_ENGINE_ID unset is valid: sessions stay in-memory."
