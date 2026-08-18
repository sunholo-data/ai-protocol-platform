#!/usr/bin/env bash
#
# check-upstream-routing.sh — did this change land in the right repo?
#
# Sprint TEMPLATE-INVERT. Design:
# docs/design/template/template-repo-topology.md
#
#   platform change -> belongs in platform-source, merged down
#   customer change -> belongs here
#
# This is a WARNING, never a blocker. Prototyping a platform change here is
# legitimate and often necessary — you cannot test against the customer's
# skills or a real deployment from a bare platform-source checkout. The mistake
# is not typing it here, it is LEAVING it here.
#
# Why it exists: nothing else detects mis-routing. `make template-parity` stays
# at 0 when you edit shipped code here, because that is not a divergence — just
# a change sitting in the wrong repo. It surfaces later, as a conflict on the
# next `make upstream-merge`.
#
# Usage:
#   scripts/check-upstream-routing.sh                # unpushed commits on this branch
#   scripts/check-upstream-routing.sh <git-range>    # e.g. upstream/main..HEAD

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

RANGE="${1:-}"
if [ -z "$RANGE" ]; then
  if git rev-parse --verify --quiet origin/"$(git branch --show-current)" >/dev/null 2>&1; then
    RANGE="origin/$(git branch --show-current)..HEAD"
  else
    RANGE="HEAD~1..HEAD"
  fi
fi

# Which paths are downstream-only is DERIVED, not hand-listed: a file is
# customer-owned exactly when the sanitizer deletes it, i.e. when it does not
# exist in the published tree. A hand-maintained regex rots the same way
# DELETE_PATHS did — the first version of this script flagged the NDA bundle and
# docs/design/v5.0.0/ as "template content" when both are deleted outright.
SANITIZED="$(mktemp -d)"
trap 'rm -rf "$SANITIZED"' EXIT
if ! bash "$REPO_ROOT/scripts/sanitize-for-template.sh" "$SANITIZED/tree" >/dev/null 2>&1; then
  echo "Could not build the published tree — skipping the routing check." >&2
  exit 0
fi

CHANGED="$(git diff --name-only "$RANGE" 2>/dev/null || true)"
[ -n "$CHANGED" ] || { echo "No changes in $RANGE."; exit 0; }

# Ships => template content. Absent => this deployment owns it.
TEMPLATE_FILES=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  [ -e "$SANITIZED/tree/$f" ] && TEMPLATE_FILES="${TEMPLATE_FILES}${f}"$'\n'
done <<< "$CHANGED"
TEMPLATE_FILES="$(printf '%s' "$TEMPLATE_FILES" | sed '/^$/d')"

if [ -z "$TEMPLATE_FILES" ]; then
  echo "Routing OK — every changed path is customer-owned."
  exit 0
fi

count="$(echo "$TEMPLATE_FILES" | wc -l | tr -d ' ')"
echo
echo "NOTE: $count changed path(s) are TEMPLATE content, not customer content:"
echo "$TEMPLATE_FILES" | head -20 | sed 's/^/    /'
[ "$count" -gt 20 ] && echo "    … and $((count - 20)) more"
echo
echo "  If these are platform improvements, they belong in platform-source so"
echo "  every fork gets them. Porting up is cheap now and expensive later — the"
echo "  next 'make upstream-merge' will conflict on anything that diverged."
echo
echo "  Legitimate reasons to leave them here: still prototyping, or the change"
echo "  is genuinely deployment-specific and the path list needs updating."
echo
echo "  (Advisory only — this never blocks.)"
