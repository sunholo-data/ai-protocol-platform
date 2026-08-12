#!/usr/bin/env bash
# probe-fallback.sh — MODEL-RELIABILITY M4 end-to-end fault-injection probe.
#
# Boots a scratch backend on :19561 with FAULT_INJECT_MODEL armed
# (gemini:503:3 — the first 3 gemini attempts fail), sends one real chat
# turn, and asserts the stream shows the full reliability path:
# MODEL_RETRY (backoff) -> MODEL_FALLBACK (chain rung) -> a real answer.
# Local-only: fault injection refuses to arm on Cloud Run (K_SERVICE).
#
# Requires: ADC for your-project-id + AIPLATFORM_ID_TOKEN
#   eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"
#
# Usage: scripts/probe-fallback.sh [skill_id]   (default: general-assistant)

set -euo pipefail

SKILL="${1:-general-assistant}"
PORT=19561
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${AIPLATFORM_ID_TOKEN:-}" ]]; then
  echo "FAIL: AIPLATFORM_ID_TOKEN not set (mint-token.sh)"; exit 2
fi

echo "== Fault-injection probe: skill=$SKILL, FAULT_INJECT_MODEL=gemini:503:3 =="
cd "$REPO_ROOT/backend"

env FAULT_INJECT_MODEL="gemini:503:3" \
    GOOGLE_CLOUD_PROJECT=your-project-id \
    GCP_PROJECT=your-project-id \
    GOOGLE_CLOUD_LOCATION=europe-west1 \
    GOOGLE_GENAI_USE_VERTEXAI=True \
    MODEL_RESIDENCY_POLICY=unrestricted \
    AGENT_ENGINE_ID="" \
    AITANA_LOCAL_SESSION=memory \
    uv run uvicorn fast_api_app:app --host 127.0.0.1 --port $PORT --log-level warning &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

echo "waiting for backend on :$PORT…"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || curl -sf "http://127.0.0.1:$PORT/openapi.json" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# The stream route takes the skill UUID, not the slug — resolve first.
SKILL_ID=$(curl -s -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" \
  "http://127.0.0.1:$PORT/api/skills" | python3 -c "
import json, sys
skills = json.load(sys.stdin)
match = next((s for s in skills if s.get('slug') == '$SKILL' or s.get('skillId') == '$SKILL'), None)
print(match['skillId'] if match else '')")
[[ -n "$SKILL_ID" ]] || { echo "FAIL: skill '$SKILL' not visible to the test user"; exit 1; }
echo "skill $SKILL -> $SKILL_ID"

OUT=$(mktemp)
curl -sS -N --max-time 120 \
  -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"message": "Reply with the single word OK."}' \
  "http://127.0.0.1:$PORT/api/skill/${SKILL_ID}/stream" > "$OUT" || {
    echo "FAIL: stream request errored"; tail -5 "$OUT"; exit 1; }

PASS=1
grep -q '"MODEL_RETRY"' "$OUT"    && echo "OK   MODEL_RETRY event seen"    || { echo "FAIL missing MODEL_RETRY"; PASS=0; }
grep -q '"MODEL_FALLBACK"' "$OUT" && echo "OK   MODEL_FALLBACK event seen" || { echo "FAIL missing MODEL_FALLBACK"; PASS=0; }
grep -q 'TEXT_MESSAGE_CONTENT' "$OUT" && echo "OK   answer content streamed"   || { echo "FAIL no answer content"; PASS=0; }
grep -q 'RUN_FINISHED' "$OUT"     && echo "OK   run finished cleanly"      || { echo "FAIL no RUN_FINISHED"; PASS=0; }

if [[ "$PASS" == "1" ]]; then
  echo "PASS: retry -> fallback -> answer, end-to-end with fault injection."
else
  echo "--- last events ---"; tail -8 "$OUT"; exit 1
fi
