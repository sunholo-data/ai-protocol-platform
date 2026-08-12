#!/usr/bin/env bash
# smoke-long-stream.sh — the long-stream incident regression guard (MODEL-RELIABILITY M1).
#
# Curls GET /api/debug/slow-stream THROUGH the deployed frontend proxy for a
# >5-minute SSE stream with 60s silent gaps, and fails unless the stream ends
# with the backend's positive '"done": true' marker. This exercises every hop
# that killed the v5 stream: the Next.js proxy transport (undici bodyTimeout —
# now node:http), the Cloud Run request timeout (now --timeout=3600), and any
# idle-timeout intermediary.
#
# Usage:
#   scripts/smoke-long-stream.sh [dev|test|prod|local] [total_seconds] [gap_seconds]
#
# Defaults: dev 360 60. Requires AIPLATFORM_ID_TOKEN (mint one with
#   eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"
# ). Trap catalogue: .claude/skills/platform-deploy/resources/traps.md #20.

set -euo pipefail

ENV="${1:-dev}"
TOTAL="${2:-360}"
GAP="${3:-60}"
REGION="europe-west1"

case "$ENV" in
  dev)   PROJECT="your-project-id" ;;
  test)  PROJECT="your-project-id-test" ;;
  prod)  PROJECT="your-project-id-prod" ;;
  local) PROJECT="" ;;
  *) echo "Unknown env: $ENV (use dev|test|prod|local)"; exit 2 ;;
esac

if [[ -z "${AIPLATFORM_ID_TOKEN:-}" ]]; then
  echo "FAIL: AIPLATFORM_ID_TOKEN not set."
  echo "Mint one:  eval \"\$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)\""
  exit 2
fi

if [[ "$ENV" == "local" ]]; then
  URL="http://localhost:3456"
else
  URL=$(gcloud run services describe platform-frontend \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)')
fi
[[ -n "$URL" ]] || { echo "FAIL: could not resolve frontend URL for $ENV"; exit 1; }

PROBE="${URL}/api/proxy/api/debug/slow-stream?seconds=${TOTAL}&gap=${GAP}"
echo "== Long-stream probe: $ENV =="
echo "URL: $PROBE"
echo "Expecting ~$((TOTAL / GAP)) ticks over ${TOTAL}s (gap ${GAP}s). Patience…"

OUT=$(mktemp)
START=$(date +%s)
# -N disables curl buffering; --max-time guards against a truly wedged probe.
HTTP_CODE=$(curl -sS -N -o "$OUT" -w '%{http_code}' \
  --max-time $((TOTAL + 120)) \
  -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" \
  -H "Accept: text/event-stream" \
  "$PROBE") || { echo "FAIL: curl died after $(( $(date +%s) - START ))s (transport reaped the stream?)"; tail -3 "$OUT"; exit 1; }
ELAPSED=$(( $(date +%s) - START ))

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "FAIL: HTTP $HTTP_CODE after ${ELAPSED}s"
  head -c 300 "$OUT"; echo
  exit 1
fi

if ! grep -q '"done": true' "$OUT"; then
  echo "FAIL: stream closed after ${ELAPSED}s WITHOUT the done marker — reaped mid-flight."
  echo "Last output:"; tail -3 "$OUT"
  exit 1
fi

if (( ELAPSED < TOTAL - 10 )); then
  echo "FAIL: done marker arrived after only ${ELAPSED}s (< ${TOTAL}s) — upstream shortened the run?"
  exit 1
fi

TICKS=$(grep -c '"tick"' "$OUT" || true)
echo "OK: stream survived ${ELAPSED}s end-to-end (${TICKS} ticks + done marker)."
rm -f "$OUT"
