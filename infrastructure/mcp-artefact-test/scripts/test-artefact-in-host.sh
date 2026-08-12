#!/usr/bin/env bash
# test-artefact-in-host.sh — serve ONE platform artefact + open a cloudflared
# quick tunnel, then print the connector URL to paste into an external host
# (ChatGPT dev mode / Claude Desktop / MCP Inspector). Copilot imports via the
# M365 Agents Toolkit, not a URL paste — see docs/ops/mcp-apps-iframe-guide.md.
#
# Usage:
#   scripts/test-artefact-in-host.sh [<artefact-name>]        # default: _template
#   MCP_ARTEFACT_TEST_PORT=3005 scripts/test-artefact-in-host.sh boldkast
#
# Ctrl-C tears down the server + tunnel.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$(cd "$HERE/.." && pwd)"                 # the mcp-artefact-test package
ARTEFACTS="$DIR/../mcp-sandbox/artefacts"
ARTEFACT="${1:-_template}"
PORT="${MCP_ARTEFACT_TEST_PORT:-3001}"

command -v node >/dev/null 2>&1 || { echo "✗ node not found" >&2; exit 1; }
command -v cloudflared >/dev/null 2>&1 || {
  echo "✗ cloudflared not found. Install:  brew install cloudflared" >&2; exit 1; }

if [ ! -d "$ARTEFACTS/$ARTEFACT/v1" ]; then
  echo "✗ artefact not found: infrastructure/mcp-sandbox/artefacts/$ARTEFACT/v1" >&2
  echo "  available:" >&2
  ls "$ARTEFACTS" 2>/dev/null | sed 's/^/    /' >&2
  exit 1
fi

LOGDIR="$(mktemp -d)"; SERVER_LOG="$LOGDIR/server.log"; TUNNEL_LOG="$LOGDIR/tunnel.log"
SERVER_PID=""; TUNNEL_PID=""
cleanup() {
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

[ -d "$DIR/node_modules" ] || {
  echo "→ installing deps (first run)…"
  ( cd "$DIR" && npm install >/dev/null 2>&1 ) || { echo "✗ npm install failed" >&2; exit 1; }
}

echo "→ serving artefact '$ARTEFACT' on :$PORT"
( cd "$DIR" && ARTEFACT="$ARTEFACT" MCP_ARTEFACT_TEST_PORT="$PORT" npm start >"$SERVER_LOG" 2>&1 ) &
SERVER_PID=$!
for _ in $(seq 1 60); do lsof -iTCP:"$PORT" -sTCP:LISTEN -P -n >/dev/null 2>&1 && break; done
lsof -iTCP:"$PORT" -sTCP:LISTEN -P -n >/dev/null 2>&1 || {
  echo "✗ server did not start on :$PORT — log:" >&2; tail -20 "$SERVER_LOG" >&2; exit 1; }

echo "→ opening cloudflared quick tunnel…"
cloudflared tunnel --url "http://localhost:$PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
ROOT=""
for _ in $(seq 1 60); do
  ROOT="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)"
  [ -n "$ROOT" ] && break
done
[ -n "$ROOT" ] || { echo "✗ tunnel URL never appeared — log:" >&2; tail -20 "$TUNNEL_LOG" >&2; exit 1; }

cat <<EOF

────────────────────────────────────────────────────────────────────
  ✅ Artefact '$ARTEFACT' is live on the public internet.

  CONNECTOR URL:
      $ROOT/mcp

  Verify the wire first (before blaming any host):
      $HERE/verify-mcp.sh $ROOT/mcp increment-counter '{"by":1}'

  Import into a host:
    • ChatGPT   Settings → Connectors → Developer mode → Create → URL above.
                Ask it to run "show-artefact", interact, then click +1
                (calls increment-counter — the mutation round-trip).
    • Claude    npx mcp-remote $ROOT/mcp
    • Inspector npx @modelcontextprotocol/inspector → connect to URL above
    • Copilot   M365 Agents Toolkit (a URL paste won't work) — see the iframe guide.

  Reminders: the *.trycloudflare.com URL is EPHEMERAL (re-import on restart /
  after laptop sleep). Hosts cache tools/list at connect — refresh the
  connector after adding a tool. Editing the artefact HTML is live (no restart);
  editing serve.ts needs a restart.
  Logs: server=$SERVER_LOG  tunnel=$TUNNEL_LOG   ·   Ctrl-C to stop.
────────────────────────────────────────────────────────────────────

EOF
wait
