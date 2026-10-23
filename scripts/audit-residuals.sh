#!/usr/bin/env bash
#
# audit-residuals.sh — list every Aitana-specific reference still in the
# working tree. Diagnostic tool for fork preparation, NOT a CI gate.
#
# Run before cutting a public fork to see what `sanitize-for-template.sh`
# will need to handle. Most matches are expected (dev scripts, design
# docs, internal skills); the suppression list filters out the things
# that are definitively private (sprint state, NDA content, ops runbooks).
#
# Exit codes are informational:
#   0 — nothing found (fork-ready)
#   1 — matches printed (expected for an active Aitana repo; sanitize at fork)
#
# For CI gating, see scripts/check_local_mode_safety.py (different scope:
# refuses LOCAL_MODE leaks into deployed configs).
#
# Run: bash scripts/audit-residuals.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Patterns we treat as Aitana-specific residuals.
PATTERNS=(
  "aitana-multivac"
  "/home/developer"
  "AgentsCLI"
  "fishfood"
)

# Dir basenames to skip — these are intentionally Aitana-internal or
# build artefacts (grep --exclude-dir matches basenames only).
EXCLUDE_DIRS=(
  ".git"
  "node_modules"
  ".venv"
  ".next"
  "__pycache__"
  "dist"
  "AgentsCLI_Share"
  ".dev-logs"
  ".ruff_cache"
  ".pytest_cache"
)

# Path-prefix patterns to filter from the match list (grep --exclude-dir
# can't match paths, so we post-filter). Each match line is rejected if
# its file path begins with any of these prefixes.
SUPPRESS_PATH_PREFIXES=(
  "./.claude/state/"
  "./.claude/skills/platform-v6-deploy/"
  "./docs/ops/"
  "./docs/feedback/"
  "./docs/design/v6.0.0/implemented/"
  "./docs/design/v6.0.0/template-split-strategy.md"
  "./docs/design/v6.1.0/local-mode-and-workshop-readiness.md"
  "./docs/design/v6.1.0/local-mode-and-fork-sprint.md"
  "./scripts/sanitize-for-template.sh"
  "./scripts/audit-residuals.sh"
  "./CLAUDE.md"                # Aitana-internal nav guide; sanitize script
                               # produces the public-template version at
                               # fork time
)

# Build the grep --exclude-dir args.
EXCLUDE_ARGS=()
for d in "${EXCLUDE_DIRS[@]}"; do
  EXCLUDE_ARGS+=("--exclude-dir=$d")
done

# Post-filter helper — drops lines whose file path starts with any
# suppress prefix.
_filter_suppressed() {
  local input="$1"
  local result="$input"
  for prefix in "${SUPPRESS_PATH_PREFIXES[@]}"; do
    # Escape "/" and "." for sed
    local escaped="${prefix//\//\\/}"
    escaped="${escaped//./\\.}"
    result="$(echo "$result" | sed "/^${escaped}/d")"
  done
  echo "$result"
}

FOUND=0
for pat in "${PATTERNS[@]}"; do
  echo "=== Pattern: $pat ==="
  raw=$(grep -rn "$pat" . "${EXCLUDE_ARGS[@]}" 2>/dev/null || true)
  matches=$(_filter_suppressed "$raw")
  matches=$(echo "$matches" | sed '/^$/d')
  if [ -n "$matches" ]; then
    echo "$matches"
    FOUND=1
  else
    echo "  (no matches outside suppression list)"
  fi
  echo
done

if [ "$FOUND" = 1 ]; then
  echo "Audit: found Aitana-specific references outside the suppression list."
  echo "  - If intentional, add the path to SUPPRESS_PATHS in this script."
  echo "  - If accidental, scrub the reference (see sanitize-for-template.sh)."
  exit 1
fi

echo "Audit clean — no Aitana-specific residuals outside the suppression list."
