#!/bin/bash
# Launch backend + frontend in LOCAL_MODE for the workshop / template demo.
#
# Usage: ./scripts/dev-local.sh
#
# Differs from scripts/dev.sh:
#   - Forces LOCAL_MODE=1 (in-memory Firestore, auth stub, no GCP credentials needed
#     for Firestore / Vertex Sessions / Cloud Trace)
#   - Forces NEXT_PUBLIC_LOCAL_MODE=1 (frontend yellow banner + LocalAuthProvider)
#   - Forces AITANA_LOCAL_SESSION=memory (in-memory ADK sessions — fast TTFT,
#     no Vertex Agent Engine round-trips)
#   - Auto-seeds the LOCAL_MODE in-memory Firestore on backend boot (demo
#     skills incl. "Workspace Demo" for the MULTI-SURFACE-A2UI sprint demo).
#
# Model auth is still needed (LOCAL_MODE does NOT stub the LLM). The
# simplest setup is GOOGLE_API_KEY (Gemini Express Mode) which avoids
# Vertex entirely — set it in backend/.env:
#
#     GOOGLE_API_KEY=your-gemini-key
#     GOOGLE_GENAI_USE_VERTEXAI=false
#
# Backend:  http://localhost:1956  (FastAPI + ADK, hot-reload)
# Frontend: http://localhost:3456  (Next.js, hot-reload, yellow LOCAL_MODE banner)
#
# Both processes share this terminal — Ctrl-C kills both.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ─── LOCAL_MODE pinning ─────────────────────────────────────────────────────
export LOCAL_MODE=1
export NEXT_PUBLIC_LOCAL_MODE=1
# Force in-memory ADK sessions — Vertex Agent Engine round-trips add ~5s
# TTFT from a laptop. See docs/design/v6.1.0/ttft-optimization.md.
export AITANA_LOCAL_SESSION=memory

# Sprint 2.11 — anonymous group-ID auth requires a signing secret.
# In LOCAL_MODE we set a known dev-only value so the workshop demo
# works out of the box without per-developer setup. The string is
# intentionally a fixed dev marker — if it ever appears in prod logs
# that's a deployment misconfiguration to investigate.
#
# Forks deploying anonymous-group-id-auth to Cloud Run MUST override
# this with a real high-entropy secret. See
# docs/integrations/anonymous-group-id-auth.md.
if [ -z "${GROUP_AUTH_SIGNING_SECRET:-}" ]; then
    export GROUP_AUTH_SIGNING_SECRET="local-mode-anon-group-dev-secret-DO-NOT-USE-IN-PROD"
fi

# Clear cloud-mode env vars that would interfere with LOCAL_MODE:
# - GCP_PROJECT / GOOGLE_CLOUD_PROJECT — LOCAL_MODE doesn't need them
# - ADC vars — Firestore + Vertex Sessions are stubbed; no GCP auth required
# - OTEL exporters — Cloud Trace is disabled; remote OTEL pushes would block
unset GCP_PROJECT GOOGLE_CLOUD_PROJECT GOOGLE_APPLICATION_CREDENTIALS
unset OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_PROTOCOL \
      OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE \
      OTEL_LOGS_EXPORTER OTEL_METRICS_EXPORTER OTEL_TRACES_EXPORTER \
      OTEL_LOG_USER_PROMPTS OTEL_RESOURCE_ATTRIBUTES

# Load backend/.env for model auth (GOOGLE_API_KEY etc.) — but the
# LOCAL_MODE pins above can't be overridden by .env (we re-export below).
ENV_FILE="$REPO_ROOT/backend/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    # Re-pin LOCAL_MODE so .env can't silently turn it off.
    export LOCAL_MODE=1
    export AITANA_LOCAL_SESSION=memory
    # Re-unset cloud-mode vars in case .env set them (cloud-mode dev
    # commonly leaves these in .env). LOCAL_MODE must NOT touch a real
    # GCP project, even for the LLM client's own telemetry.
    unset GCP_PROJECT GOOGLE_CLOUD_PROJECT
    # AGENT_ENGINE_ID would route session writes to Vertex Agent Engine
    # (AITANA_LOCAL_SESSION=memory overrides at the session-service
    # level, but the engine ID still leaks into telemetry resource
    # attributes — clear it for hygiene).
    unset AGENT_ENGINE_ID
    # LLM provider selection.
    #
    # Express Mode (Gemini API direct, no GCP project) requires the key
    # to actually be enabled for generativelanguage.googleapis.com. The
    # generic name `GOOGLE_API_KEY` is often used in shells for OTHER
    # Google APIs (Maps, Firebase, etc.) — auto-switching to Express
    # Mode based on its presence FAILS at first LLM call with "API key
    # not valid" if the key isn't a Gemini key. So we only force
    # Express Mode when `GEMINI_API_KEY` is the explicit one set — that
    # name comes from https://aistudio.google.com/apikey and is
    # unambiguously a Gemini key.
    #
    # If a generic `GOOGLE_API_KEY` is set but no `GEMINI_API_KEY`, we
    # keep Vertex AI mode but unset `GOOGLE_API_KEY` to avoid the
    # backend's startup-guard 401-CREDENTIALS_MISSING warning (the
    # genai client would otherwise attach the key to Vertex calls, and
    # Vertex Sessions/Memory reject API-key auth). Vertex AI then uses
    # ADC + the user's quota project — needs `gcloud auth
    # application-default login` to be current.
    if [ -n "${GEMINI_API_KEY:-}" ]; then
        export GOOGLE_GENAI_USE_VERTEXAI=false
        unset GOOGLE_API_KEY GOOGLE_GENAI_API_KEY  # avoid double-key confusion
        echo "[dev-local] Using GEMINI_API_KEY (Express Mode) — no GCP project needed."
        # Validate the key HERE (v6.20.0, downstream fork entry #3). This block
        # is already careful in one direction — it refuses to auto-enable
        # Express from an ambiguous GOOGLE_API_KEY — but the key it steers you
        # TO was never checked. A wrong or expired one then surfaces at the
        # first chat turn as an opaque model error, several minutes and one
        # "is the platform broken?" later. One cheap call converts that into a
        # sentence, before the server starts.
        if command -v curl >/dev/null 2>&1; then
            _key_probe=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 \
                "https://generativelanguage.googleapis.com/v1beta/models?key=${GEMINI_API_KEY}" 2>/dev/null || echo "000")
            case "$_key_probe" in
                200) echo "[dev-local]   key OK" ;;
                000) echo "[dev-local]   WARN: could not reach generativelanguage.googleapis.com — skipping key check (offline?)" ;;
                *)   echo "" >&2
                     echo "[dev-local] ERROR: GEMINI_API_KEY was rejected (HTTP ${_key_probe})." >&2
                     echo "            Every chat turn will fail with an opaque model error." >&2
                     echo "            Get a key at https://aistudio.google.com/apikey and re-export it," >&2
                     echo "            or unset GEMINI_API_KEY to fall back to Vertex via ADC." >&2
                     exit 1 ;;
            esac
            unset _key_probe
        fi
    elif [ -n "${GOOGLE_API_KEY:-}" ]; then
        # Ambiguous — could be Maps, Firebase, Drive, or Gemini. Don't
        # gamble. Strip it from this process so the genai client
        # doesn't attach it to Vertex calls (Vertex Sessions/Memory
        # reject API-key auth and surface a CREDENTIALS_MISSING 401).
        unset GOOGLE_API_KEY GOOGLE_GENAI_API_KEY GEMINI_API_KEY
        export GOOGLE_GENAI_USE_VERTEXAI=True
        echo "[dev-local] GOOGLE_API_KEY present but not unambiguously a Gemini key."
        echo "[dev-local]   Stripped from process; falling back to Vertex AI via ADC."
        echo "[dev-local]   For Express Mode (no GCP touch): set GEMINI_API_KEY in backend/.env."
    else
        export GOOGLE_GENAI_USE_VERTEXAI=True
        echo "[dev-local] Using Vertex AI via ADC."
        echo "[dev-local]   For Express Mode (no GCP touch): set GEMINI_API_KEY in backend/.env."
    fi
fi

FRONTEND_PORT=3456
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

# Pre-build the MCP sandbox bundle (optional — only needed for MCP App demos).
SANDBOX_DIR="$REPO_ROOT/infrastructure/mcp-sandbox"
if [ -d "$SANDBOX_DIR/node_modules" ]; then
    (cd "$SANDBOX_DIR" && npm run build > /dev/null 2>&1) || true
fi

echo "=== Aitana dev server — LOCAL_MODE ==="
echo "  Backend     → http://localhost:1956   (LOCAL_MODE=1, in-memory Firestore)"
echo "  Frontend    → http://localhost:${FRONTEND_PORT}   (yellow LOCAL_MODE banner)"
echo "  MCP sandbox → http://localhost:${SANDBOX_PORT}   (optional, for MCP App demos)"
echo "  Logs        → $LOG_DIR/"
echo ""
echo "Demo skill for sprint 2.9 (MULTI-SURFACE-A2UI):"
echo "  → Sign in via the workshop stub identity"
echo "  → Pick 'Workspace Demo' from the skills bar"
echo "  → Type: 'show me the dashboard'"
echo ""

cleanup() {
    echo ""
    echo "Stopping dev servers…"
    kill "$BACKEND_PID" "$FRONTEND_PID" ${SANDBOX_PID:-} 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" ${SANDBOX_PID:-} 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --reload-exclude tests/ + scripts/: a reload mid-turn kills the SSE stream
# and hangs the frontend. Editing a test/script must not restart the server;
# source edits under backend/ still hot-reload. (See scripts/dev.sh for detail.)
(cd "$REPO_ROOT/backend" && uv run uvicorn fast_api_app:app \
    --host 127.0.0.1 --port 1956 --reload \
    --reload-exclude "$REPO_ROOT/backend/tests" \
    --reload-exclude "$REPO_ROOT/backend/scripts" 2>&1 | tee "$BACKEND_LOG") &
BACKEND_PID=$!

(cd "$REPO_ROOT/frontend" && PORT=$FRONTEND_PORT npm run dev 2>&1 | tee "$FRONTEND_LOG") &
FRONTEND_PID=$!

if [ -d "$SANDBOX_DIR/node_modules" ]; then
    (cd "$SANDBOX_DIR" && SANDBOX_PORT=$SANDBOX_PORT \
        ALLOWED_HOST_ORIGINS="http://localhost:${FRONTEND_PORT}" \
        npm run dev 2>&1 | tee "$SANDBOX_LOG") &
    SANDBOX_PID=$!
fi

wait "$BACKEND_PID" "$FRONTEND_PID"
