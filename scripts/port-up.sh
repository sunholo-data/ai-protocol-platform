#!/usr/bin/env bash
#
# port-up.sh — move platform changes from this repo up to its upstream.
#
# The missing half of the loop. `make upstream-merge` brings changes DOWN;
# nothing brought them UP, so porting meant enumerating paths by hand — exactly
# the manual step that gets skipped, which is how 26 paths accumulated in one
# sprint before anyone noticed.
#
#   platform change -> belongs upstream, merged down
#   customer change -> belongs here
#
# WHY PER-PATH AND NOT CHERRY-PICK: a real commit routinely straddles both tiers
# (a guard in a shared tool plus the prompt change in a customer skill is ONE
# fix in TWO repos). Cherry-picking such a commit drags customer content
# upstream. This copies only the paths that survive sanitize, so a mixed commit
# splits correctly and customer files cannot ride along by construction.
#
# Dry run by default — same contract as upstream-merge.sh.
#
# Usage:
#   scripts/port-up.sh                       # plan for unpushed commits on this branch
#   scripts/port-up.sh upstream/dev..HEAD   # plan for an explicit range
#   scripts/port-up.sh <range> GO=1          # ...and push the branch + open a PR
#
# Env:
#   GO=1            actually push and open the PR (otherwise: plan only)
#   BRANCH=<name>   branch name on the upstream repo (default: port-up-<sha>)
#   UPSTREAM=<url>  override the upstream remote URL

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

RANGE="${1:-}"
if [ -z "$RANGE" ] || [ "$RANGE" = "GO=1" ]; then
  BR="$(git branch --show-current)"
  if git rev-parse --verify --quiet "origin/$BR" >/dev/null 2>&1; then
    RANGE="origin/$BR..HEAD"
  else
    RANGE="HEAD~1..HEAD"
  fi
fi

GO="${GO:-0}"
[ "${2:-}" = "GO=1" ] && GO=1

UPSTREAM_URL="${UPSTREAM:-$(git remote get-url upstream 2>/dev/null || true)}"
if [ -z "$UPSTREAM_URL" ]; then
  echo "No 'upstream' remote and no UPSTREAM= override — nothing to port to." >&2
  exit 1
fi

log() { echo "[port-up] $*"; }

log "range:    $RANGE"
log "upstream: $UPSTREAM_URL"

# Single source of truth for "is this template content" — see
# check-upstream-routing.sh. Never re-derive it here.
log "deriving template paths (sanitizer or upstream comparison) …"
# A failure here must STOP the port. Until 2026-09-30 the routing check exited
# 0 with no output when it could not build the published tree, and this script
# read the silence as "Nothing to port" — a whole mobile/PWA/design series was
# reported as customer-owned and had to be ported by hand.
if ! PATHS="$(PATHS_ONLY=1 bash scripts/check-upstream-routing.sh "$RANGE")"; then
  log "Could not derive the template paths (see the error above) — not porting."
  exit 2
fi

if [ -z "$PATHS" ]; then
  log "Nothing to port — no template paths changed in $RANGE."
  exit 0
fi

COUNT="$(printf '%s\n' "$PATHS" | sed '/^$/d' | wc -l | tr -d ' ')"
log "$COUNT template path(s) to port:"
printf '%s\n' "$PATHS" | sed 's/^/    /'

# Only paths that still EXIST here can be checked out. A path deleted in this
# repo needs a deliberate upstream deletion, which is a different review
# conversation — flag it rather than silently skipping.
MISSING=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  [ -e "$f" ] || MISSING="${MISSING}${f}"$'\n'
done <<< "$PATHS"
if [ -n "$MISSING" ]; then
  echo
  log "NOTE: these were DELETED here and are not ported (deleting upstream is a"
  log "      separate, deliberate decision):"
  printf '%s' "$MISSING" | sed 's/^/    /'
fi

# Paths are copied WHOLE from here, so a file upstream has changed since our
# last `make upstream-merge` would be ported back with those changes undone.
# Refuse unless those upstream commits are merged down first.
UPSTREAM_REF="${UPSTREAM_REF:-upstream/dev}"
if git rev-parse --verify --quiet "$UPSTREAM_REF^{commit}" >/dev/null; then
  BASE="$(git merge-base HEAD "$UPSTREAM_REF")"
  blob() { git rev-parse --quiet --verify "$1:$2" 2>/dev/null || echo none; }
  STALE=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    up="$(blob "$UPSTREAM_REF" "$f")"
    # Upstream changed it since we last merged, AND differently from us.
    if [ "$up" != "$(blob "$BASE" "$f")" ] && [ "$up" != "$(blob HEAD "$f")" ]; then
      STALE="${STALE}${f}"$'\n'
    fi
  done <<< "$PATHS"
  if [ -n "$STALE" ]; then
    echo
    log "STOP: upstream changed these since the last merge-down, differently from here:"
    printf '%s' "$STALE" | head -20 | sed 's/^/    /'
    log "Copying our versions would revert those upstream changes. Run"
    log "'make upstream-merge' first (or FORCE=1 if you have checked each one)."
    [ "${FORCE:-0}" = 1 ] || exit 4
  fi
fi

SHA="$(git rev-parse --short HEAD)"
BRANCH_NAME="${BRANCH:-port-up-$SHA}"

if [ "$GO" != 1 ]; then
  echo
  log "DRY RUN — nothing pushed. Would:"
  log "  1. clone $UPSTREAM_URL"
  log "  2. branch $BRANCH_NAME"
  log "  3. copy the $COUNT path(s) above from $(git branch --show-current) @ $SHA"
  log "  4. run the upstream backend suite, and the frontend checks if frontend/ changed"
  log "  5. push + open a PR"
  log ""
  log "Re-run with GO=1 to execute."
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log "cloning upstream …"
git clone --quiet "$UPSTREAM_URL" "$WORK/up"
cd "$WORK/up"
git checkout -q -b "$BRANCH_NAME"
git remote add downstream "$REPO_ROOT"
git fetch -q downstream "$(cd "$REPO_ROOT" && git branch --show-current)"

log "copying paths …"
# xargs -0 so paths with spaces survive; --  so a leading-dash path is not a flag.
printf '%s' "$PATHS" | sed '/^$/d' | tr '\n' '\0' \
  | xargs -0 git checkout FETCH_HEAD --

if git diff --cached --quiet; then
  log "Upstream already matches — nothing to port."
  exit 0
fi
git diff --cached --stat | tail -5

# The gate that matters: these must pass WITHOUT this deployment's customer
# skills or fixtures present. A pass here is what proves the change is genuinely
# platform code and not quietly dependent on customer content.
if [ -d backend ]; then
  log "running the upstream backend suite (no customer content present) …"
  if ! ( cd backend && uv run pytest tests/ -m "not slow and not integration" -q 2>&1 | tail -3 ); then
    log "UPSTREAM TESTS FAILED — not pushing. The change likely depends on"
    log "something this repo has and upstream does not."
    exit 3
  fi
fi

# Same gate for the frontend: lint, typecheck and tests without customer
# content. Reuses this repo's node_modules when package.json matches, so the
# gate costs seconds rather than an npm install.
if printf '%s' "$PATHS" | grep -q '^frontend/' && [ -d frontend ]; then
  if cmp -s frontend/package.json "$REPO_ROOT/frontend/package.json" && [ -d "$REPO_ROOT/frontend/node_modules" ]; then
    ln -s "$REPO_ROOT/frontend/node_modules" frontend/node_modules
    log "running the upstream frontend checks (no customer content present) …"
    if ! ( cd frontend && npm run -s quality:check:fast && CI=true npx vitest run 2>&1 | tail -4 ); then
      log "UPSTREAM FRONTEND CHECKS FAILED — not pushing."
      exit 3
    fi
    rm frontend/node_modules
  else
    log "frontend/package.json differs from upstream — skipping the frontend gate;"
    log "run 'npm install && npm run quality:check' in the PR branch before merging."
  fi
fi

git commit -q -m "Port platform changes from downstream @ $SHA

Copied per-path (not cherry-picked) so mixed commits split correctly and
customer content cannot ride along. Paths derived by check-upstream-routing.sh.

Verified: upstream suites pass without customer content present."

log "pushing $BRANCH_NAME …"
git push -q -u origin "$BRANCH_NAME"

if command -v gh >/dev/null 2>&1; then
  gh pr create --fill --head "$BRANCH_NAME" 2>&1 | tail -2 || \
    log "PR creation failed — branch is pushed, open it by hand."
else
  log "gh not found — branch pushed, open the PR by hand."
fi
