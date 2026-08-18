#!/bin/bash
# Launch backend + frontend for local development.
#
# Usage: ./scripts/dev.sh
#
# Backend:  http://localhost:1956  (FastAPI + ADK, hot-reload)
# Frontend: http://localhost:3456  (Next.js, hot-reload)
#
# Both processes share this terminal — Ctrl-C kills both.
# Firestore + Vertex AI hit the real DEV_GCP_PROJECT (deploy.env) via ADC.
# Run `gcloud auth application-default login` once if credentials are stale.


source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Force-set both vars — GCP_PROJECT shadows GOOGLE_CLOUD_PROJECT in
# db/firestore.py, and the shell may already have GCP_PROJECT pointing at
# a different project (e.g. multivac-internal-dev). Always use dev here.
export GCP_PROJECT="${DEV_GCP_PROJECT}"
export GOOGLE_CLOUD_PROJECT="${DEV_GCP_PROJECT}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-europe-west1}"
export GOOGLE_GENAI_USE_VERTEXAI="True"
# MODEL-RELIABILITY M3: local dev mirrors the deployed dev policy (the
# accepted dev/demo US-egress exception). models.yaml ships eu-strict as the
# fail-safe default, so without this line local smart-tier would resolve to
# the EU variant instead of Claude.
export MODEL_RESIDENCY_POLICY="${MODEL_RESIDENCY_POLICY:-unrestricted}"

# Unset API-key vars so the Vertex SDK uses ADC, not Express Mode. With
# GOOGLE_GENAI_USE_VERTEXAI=true AND GOOGLE_API_KEY set, the genai client
# still attaches the API key to some Vertex calls — and Vertex Sessions /
# Memory APIs reject API-key auth with a 401 ("API keys are not supported
# by this API"). The shell often has GOOGLE_API_KEY exported for other
# tooling; clear it here so it can't leak in.
unset GOOGLE_API_KEY GEMINI_API_KEY GOOGLE_GENAI_API_KEY

# Unset OTEL exporter vars so the backend doesn't try to ship telemetry to
# a collector that isn't running locally (commonly AILANG's localhost:1957).
# Each failed export blocks on TCP retry for ~10s and inflates per-turn
# latency. Aitana's own telemetry config in observability/telemetry.py is
# the source of truth — anything in the shell is noise here.
unset OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_PROTOCOL \
      OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE \
      OTEL_LOGS_EXPORTER OTEL_METRICS_EXPORTER OTEL_TRACES_EXPORTER \
      OTEL_LOG_USER_PROMPTS OTEL_RESOURCE_ATTRIBUTES

# Load backend/.env if present so per-developer overrides (AGENT_ENGINE_ID,
# CHAT_TITLE_MODEL, ALLOW_ORIGINS, etc.) reach the uvicorn process. The
# explicit exports above for the GCP project triple come *after* this so
# they always win over a stale .env. Only KEY=VALUE lines are loaded; lines
# starting with # and blank lines are skipped. Quoted values are stripped.
ENV_FILE="$REPO_ROOT/backend/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    # Re-pin the GCP project triple so .env can never silently downgrade
    # the local dev project (e.g. an old `GCP_PROJECT=multivac-internal-dev`
    # left in someone's .env after porting v5 tooling).
    export GCP_PROJECT="${DEV_GCP_PROJECT}"
    export GOOGLE_CLOUD_PROJECT="${DEV_GCP_PROJECT}"
fi

# Pin frontend to 3456 — 3000 is often occupied by other local servers.
FRONTEND_PORT=3456
# MCP sandbox proxy on its own port (different origin, per MCP Apps spec —
# see docs/design/v6.1.0/mcp-sandbox-separate-origin.md).
SANDBOX_PORT=3457

# Fixed log paths so Claude Code can tail/read them mid-session.
LOG_DIR="$REPO_ROOT/.dev-logs"
mkdir -p "$LOG_DIR"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
SANDBOX_LOG="$LOG_DIR/sandbox.log"

# Kill anything already on the dev ports so restart is clean.
for PORT in 1956 $FRONTEND_PORT $SANDBOX_PORT; do
    PIDS=$(lsof -ti ":$PORT" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "Freeing port $PORT (pid $PIDS)…"
        kill $PIDS 2>/dev/null || true
        sleep 0.5
    fi
done

# Pre-build the sandbox bundle so /sandbox.js doesn't 404 on first request
# while tsx watch is still spinning up.
SANDBOX_DIR="$REPO_ROOT/infrastructure/mcp-sandbox"
if [ -d "$SANDBOX_DIR/node_modules" ]; then
    (cd "$SANDBOX_DIR" && npm run build > /dev/null 2>&1) || true
else
    echo "[dev] mcp-sandbox node_modules missing — run: cd infrastructure/mcp-sandbox && npm install"
fi

# MCP Toolbox: same loopback port local and deployed (see scripts/install-toolbox.sh).
TOOLBOX_BIN="$REPO_ROOT/.bin/toolbox"
TOOLBOX_PORT="${TOOLBOX_PORT:-5000}"
TOOLBOX_LOG="$LOG_DIR/toolbox.log"

echo "=== Aitana dev server ==="
echo "  Backend     → http://localhost:1956"
echo "  Frontend    → http://localhost:${FRONTEND_PORT}"
echo "  MCP sandbox → http://localhost:${SANDBOX_PORT}"
if [ -x "$TOOLBOX_BIN" ]; then
    echo "  MCP Toolbox → http://127.0.0.1:${TOOLBOX_PORT}/mcp/example"
else
    echo "  MCP Toolbox → (not installed — run: make toolbox-install)"
fi
echo "  Project     → $GOOGLE_CLOUD_PROJECT"
echo "  Logs        → $LOG_DIR/"
echo ""

cleanup() {
    echo ""
    echo "Stopping dev servers…"
    kill "$BACKEND_PID" "$FRONTEND_PID" ${SANDBOX_PID:-} ${TOOLBOX_PID:-} 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" ${SANDBOX_PID:-} ${TOOLBOX_PID:-} 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --reload-exclude tests/ + scripts/: editing a test or a one-off script while
# an agent turn is in flight must NOT restart the server — a reload mid-turn
# kills the SSE stream and the frontend hangs with no terminal event (a
# comparison run takes 30-90s, so any save in that window would nuke it).
# Source edits under backend/ still hot-reload as normal. uvicorn treats an
# existing dir passed to --reload-exclude as a whole-subtree exclusion
# (matched via path.parents); absolute paths are required for that to fire.
(cd "$REPO_ROOT/backend" && uv run uvicorn fast_api_app:app \
    --host 127.0.0.1 --port 1956 --reload \
    --reload-exclude "$REPO_ROOT/backend/tests" \
    --reload-exclude "$REPO_ROOT/backend/scripts" 2>&1 | tee "$BACKEND_LOG") &
BACKEND_PID=$!

(cd "$REPO_ROOT/frontend" && PORT=$FRONTEND_PORT npm run dev 2>&1 | tee "$FRONTEND_LOG") &
FRONTEND_PID=$!

# MCP sandbox proxy. Optional in the sense that frontend/backend run fine
# without it for non-MCP-App tool calls, but required for /dev/mcp-apps and
# any chat that exercises an MCP App (e.g. ext-apps map-server).
if [ -d "$SANDBOX_DIR/node_modules" ]; then
    (cd "$SANDBOX_DIR" && SANDBOX_PORT=$SANDBOX_PORT \
        ALLOWED_HOST_ORIGINS="http://localhost:${FRONTEND_PORT}" \
        npm run dev 2>&1 | tee "$SANDBOX_LOG") &
    SANDBOX_PID=$!
fi

# MCP Toolbox (database gateway). Deployed it is a SIDECAR container inside
# the deployed Cloud Run service; locally it is this raw binary. Either way the backend
# reaches it on the SAME loopback URL (127.0.0.1:5000), which is why the
# mcp_servers registry entry needs no per-env variant.
#
# Optional in the same sense as the sandbox above: chat runs fine without it,
# but any skill declaring `toolConfigs.mcp.servers: [toolbox]` will fail
# LOUDLY at agent build (McpServerResolutionError, by design — G42) rather than
# silently lose its tools. Hence the explicit hint instead of a silent skip.
if [ -x "$TOOLBOX_BIN" ]; then
    (cd "$REPO_ROOT/infrastructure/mcp-toolbox" && "$TOOLBOX_BIN" \
        --config tools.yaml --address 127.0.0.1 --port "$TOOLBOX_PORT" \
        --disable-reload 2>&1 | tee "$TOOLBOX_LOG") &
    TOOLBOX_PID=$!
else
    echo "[dev] toolbox binary missing — run: make toolbox-install"
    echo "[dev]   (skills using toolConfigs.mcp.servers: [toolbox] will fail at agent build without it)"
fi

wait "$BACKEND_PID" "$FRONTEND_PID"
