#!/bin/bash
# Clean restart of the local dev stack — the one-command recovery for the
# recurring "frontend 500 / no rendering after a rebuild" gotcha.
#
# Root cause it fixes: running `npm run build` (e.g. via `npm run quality:check`)
# while `next dev` is live overwrites the dev server's `.next` with production
# artifacts, so the live server 500s with "Cannot find module './NNN.js'".
# Big file deletions (e.g. a refactor) can also strand stale chunks. Either way
# the fix is the same: stop the servers, delete `.next`, relaunch.
#
#   make dev-restart      # or: scripts/dev-restart.sh
#
# Prevention (so you don't need this): run the FULL build in CI, not locally —
# use `npm run quality:check:fast` (lint + typecheck, no build) in the inner
# loop; let CI run `quality:check` (with the build). See CLAUDE.md.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Stopping dev processes…"
pkill -f "uvicorn fast_api_app" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
pkill -f "next-server" 2>/dev/null || true
pkill -f "scripts/dev.sh" 2>/dev/null || true
for PORT in 1956 3456 3457; do
    PIDS=$(lsof -ti ":$PORT" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        kill -9 $PIDS 2>/dev/null || true
    fi
done
sleep 2

echo "Clearing Next build cache (frontend/.next)…"
rm -rf "$REPO_ROOT/frontend/.next"

echo "Relaunching dev stack (make dev)…"
exec "$REPO_ROOT/scripts/dev.sh"
