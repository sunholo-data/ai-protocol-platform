#!/usr/bin/env bash
# Try the frontend -> backend proxy bridge locally.
#
# Starts backend (port 1956) and frontend (port 3000) with BACKEND_URL
# pointing at the backend, then curls /api/proxy/health through the frontend
# and prints the result. Cleans up both processes on exit.
#
# Usage:
#   ./scripts/try-proxy-local.sh           # dev mode (next dev)
#   MODE=prod ./scripts/try-proxy-local.sh # standalone build (reproduces the Cloud Run 404 defect)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${MODE:-dev}"

# Force-set both vars — GCP_PROJECT shadows GOOGLE_CLOUD_PROJECT in db/firestore.py.
export GCP_PROJECT=your-project-id
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-europe-west1}"
export GOOGLE_GENAI_USE_VERTEXAI="True"

BACKEND_PORT=1956
FRONTEND_PORT=3000
LOG_DIR="$(mktemp -d)"
echo "Logs: $LOG_DIR"

cleanup() {
  echo ""
  echo "-- Cleaning up --"
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {
  local url="$1" name="$2" tries=60
  until curl -fsS "$url" >/dev/null 2>&1; do
    tries=$((tries - 1))
    if [[ $tries -le 0 ]]; then
      echo "FAIL: $name did not come up at $url"
      return 1
    fi
    sleep 1
  done
  echo "OK: $name up at $url"
}

echo "== Starting backend on :$BACKEND_PORT =="
cd "$REPO_ROOT/backend"
uv run uvicorn fast_api_app:app --host 0.0.0.0 --port "$BACKEND_PORT" \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

wait_for "http://localhost:$BACKEND_PORT/health" "backend"

echo "== Starting frontend on :$FRONTEND_PORT (mode=$MODE) =="
cd "$REPO_ROOT/frontend"
export BACKEND_URL="http://localhost:$BACKEND_PORT"

if [[ "$MODE" == "prod" ]]; then
  echo "Building standalone bundle..."
  npm run build > "$LOG_DIR/frontend-build.log" 2>&1
  # Copy static + public into standalone tree (next build doesn't do this)
  cp -r .next/static .next/standalone/.next/static
  [[ -d public ]] && cp -r public .next/standalone/public || true
  PORT="$FRONTEND_PORT" node .next/standalone/server.js \
    > "$LOG_DIR/frontend.log" 2>&1 &
else
  PORT="$FRONTEND_PORT" npm run dev \
    > "$LOG_DIR/frontend.log" 2>&1 &
fi
FRONTEND_PID=$!

wait_for "http://localhost:$FRONTEND_PORT/api/health" "frontend"

echo ""
echo "== Probing bridge =="
echo "-- direct backend /health --"
curl -sS -i "http://localhost:$BACKEND_PORT/health" | head -20
echo ""
echo "-- frontend /api/proxy/health (direct route) --"
curl -sS -i "http://localhost:$FRONTEND_PORT/api/proxy/health" | head -20
echo ""
echo "-- frontend /api/proxy/something-else (catch-all, expect 404 if removed) --"
curl -sS -i --max-time 5 "http://localhost:$FRONTEND_PORT/api/proxy/something-else" 2>&1 | head -5 || true
echo ""
echo "Bridge check complete. Ctrl-C to stop (or wait)."
wait
