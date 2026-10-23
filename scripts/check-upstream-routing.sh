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
#   scripts/check-upstream-routing.sh <git-range>    # e.g. upstream/dev..HEAD

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

# Which paths are template content is DERIVED, never hand-listed per script.
# Two sources of truth, depending on what kind of repo this is:
#
#  1. A repo that PUBLISHES a template has scripts/sanitize-for-template.sh.
#     There a file is customer-owned exactly when the sanitizer deletes it —
#     the first version of this script used a regex and flagged the NDA bundle
#     and docs/design/v5.0.0/ as "template content" when both are deleted
#     outright. (Aitana, AIPLA.)
#  2. Every other fork (the sanitizer is customer-owned, so upstream never
#     ships it) asks UPSTREAM instead: a path that exists at $UPSTREAM_REF is
#     template content; a NEW path is template content unless it matches one of
#     this fork's customer-owned globs in .customer-paths (default:
#     docs/customers/). Before 2026-09-30 this branch did not exist, so every
#     such fork got "could not build the published tree" and port-up read the
#     failure as "nothing to port".
#
# If neither source is available the script FAILS (exit 2) in PATHS_ONLY mode
# — a caller must never mistake "could not tell" for "nothing to do".

fail() {
  echo "check-upstream-routing: $*" >&2
  if [ "${PATHS_ONLY:-0}" = 1 ]; then exit 2; fi
  echo "Skipping the routing check (advisory)." >&2
  exit 0
}

CHANGED="$(git diff --name-only "$RANGE" 2>/dev/null)" || fail "bad range: $RANGE"
if [ -z "$CHANGED" ]; then
  # PATHS_ONLY is a machine seam: emit NOTHING, or the human message becomes a
  # "path" and the caller tries to port a file named "No changes in …".
  [ "${PATHS_ONLY:-0}" = 1 ] || echo "No changes in $RANGE."
  exit 0
fi

TEMPLATE_FILES=""
if [ -f "$REPO_ROOT/scripts/sanitize-for-template.sh" ]; then
  SANITIZED="$(mktemp -d)"
  trap 'rm -rf "$SANITIZED"' EXIT
  bash "$REPO_ROOT/scripts/sanitize-for-template.sh" "$SANITIZED/tree" >/dev/null 2>&1 \
    || fail "the sanitizer failed — could not build the published tree."
  # Ships => template content. Absent => this deployment owns it.
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -e "$SANITIZED/tree/$f" ] && TEMPLATE_FILES="${TEMPLATE_FILES}${f}"$'\n'
  done <<< "$CHANGED"
else
  UPSTREAM_REF="${UPSTREAM_REF:-upstream/dev}"
  if ! git rev-parse --verify --quiet "$UPSTREAM_REF^{commit}" >/dev/null; then
    # Best effort: a fresh clone has the remote but has never fetched it.
    git fetch -q "${UPSTREAM_REF%%/*}" "${UPSTREAM_REF#*/}" 2>/dev/null || true
    git rev-parse --verify --quiet "$UPSTREAM_REF^{commit}" >/dev/null \
      || fail "no sanitizer and no $UPSTREAM_REF to compare against (set UPSTREAM_REF)."
  fi

  # Customer-owned globs: `case` patterns, one per line, # comments. `*` in a
  # case pattern also matches `/`, so `docs/customers/*` covers the subtree.
  CUSTOMER_GLOBS=("docs/customers/*" ".customer-paths")
  if [ -f "$REPO_ROOT/.customer-paths" ]; then
    while IFS= read -r g; do
      g="${g%%#*}"; g="$(echo "$g" | tr -d '[:space:]')"
      [ -n "$g" ] && CUSTOMER_GLOBS+=("$g")
    done < "$REPO_ROOT/.customer-paths"
  fi
  is_customer() {
    local g
    for g in "${CUSTOMER_GLOBS[@]}"; do
      # shellcheck disable=SC2254  # the glob is the point
      case "$1" in $g) return 0 ;; esac
    done
    return 1
  }

  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if git cat-file -e "$UPSTREAM_REF:$f" 2>/dev/null; then
      # Upstream ships it: template content, whatever the globs say.
      TEMPLATE_FILES="${TEMPLATE_FILES}${f}"$'\n'
    elif ! is_customer "$f"; then
      TEMPLATE_FILES="${TEMPLATE_FILES}${f}"$'\n'
    fi
  done <<< "$CHANGED"
fi
TEMPLATE_FILES="$(printf '%s' "$TEMPLATE_FILES" | sed '/^$/d')"

# --paths-only: emit just the template paths, one per line, nothing else. This
# is the seam scripts/port-up.sh consumes, so the "what is template content"
# derivation lives in ONE place. Duplicating it there would let the two drift,
# and the drift would be silent — port-up would quietly carry (or drop) a file.
if [ "${PATHS_ONLY:-0}" = 1 ]; then
  printf '%s' "$TEMPLATE_FILES"
  [ -n "$TEMPLATE_FILES" ] && echo
  exit 0
fi

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
