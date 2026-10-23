#!/usr/bin/env bash
# Regenerate user-guide screenshots:  make guide-shots [PAGE=folders]
#
# Starts the LOCAL frontend (LOCAL_MODE, neutral branding, port 3470) unless one
# is already up, runs scripts/user-guide/shots/<page>.shots.mjs for PAGE (or every
# page), and stops the server it started. Every API call is answered by the
# synthetic fixture — no deployed env, no login, no network (see shots/lib.mjs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SHOTS="$ROOT/scripts/user-guide/shots"
PORT="${GUIDE_SHOTS_PORT:-3470}"
BASE="http://127.0.0.1:$PORT"

[ -d "$SHOTS/node_modules/playwright-core" ] || (cd "$SHOTS" && npm install --no-audit --no-fund --silent)

started=""
if ! curl -fsS -o /dev/null "$BASE/help" 2>/dev/null; then
  echo "guide-shots: starting the local frontend on :$PORT"
  (cd "$ROOT/frontend" && NEXT_PUBLIC_LOCAL_MODE=1 NEXT_PUBLIC_BRAND_APP_NAME="Acme AI" \
    NEXT_PUBLIC_ENABLE_SKILL_STUDIO=true npx next dev -p "$PORT" >/tmp/guide-shots-frontend.log 2>&1) &
  started=$!
  trap '[ -n "$started" ] && pkill -f "next dev -p $PORT" || true' EXIT
  for _ in $(seq 1 90); do curl -fsS -o /dev/null "$BASE/help" 2>/dev/null && break; sleep 2; done
fi

pages="${PAGE:-}"
if [ -z "$pages" ]; then
  pages="$(cd "$SHOTS" && ls *.shots.mjs | sed 's/\.shots\.mjs$//')"
fi
status=0
for p in $pages; do
  echo "== $p"
  (cd "$SHOTS" && node run.mjs "$p" --base "$BASE") || status=1
done
exit $status
