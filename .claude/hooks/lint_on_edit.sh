#!/bin/bash
# Auto-lint TypeScript/Python files after Edit/Write tool calls
# Input comes as JSON via stdin from Claude Code
set -euo pipefail

# Read the tool input from stdin
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Determine repo root — portable across machines. Falls back to the
# script's own location if `git` isn't available for some reason.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../.." && pwd))"

# TypeScript/JavaScript files -> ESLint + tsc check on just this file
if [[ "$FILE_PATH" == *.ts || "$FILE_PATH" == *.tsx || "$FILE_PATH" == *.js || "$FILE_PATH" == *.jsx ]]; then
  cd "$REPO_ROOT/frontend"

  # Run ESLint on just the changed file (fast, targeted)
  LINT_OUTPUT=$(npx eslint "$FILE_PATH" --no-error-on-unmatched-pattern 2>&1) || true
  if [ -n "$LINT_OUTPUT" ]; then
    echo "$LINT_OUTPUT" | head -20
  fi

  # Run typecheck (project-wide, but fast if incremental)
  TSC_OUTPUT=$(npx tsc --noEmit --pretty 2>&1) || true
  if echo "$TSC_OUTPUT" | grep -q "error TS"; then
    echo ""
    echo "TypeScript errors:"
    echo "$TSC_OUTPUT" | grep "error TS" | head -10
  fi
fi

# Python files -> ruff via uv on just the changed file
if [[ "$FILE_PATH" == *.py ]]; then
  cd "$REPO_ROOT/backend"

  # Auto-format in place so CI's `ruff format --check` stays green
  FMT_OUTPUT=$(uv run ruff format "$FILE_PATH" 2>&1) || true
  if echo "$FMT_OUTPUT" | grep -qi "reformatted"; then
    echo "$FMT_OUTPUT" | grep -i "reformatted" | head -3
  fi

  LINT_OUTPUT=$(uv run ruff check "$FILE_PATH" 2>&1) || true
  if [ -n "$LINT_OUTPUT" ]; then
    echo "$LINT_OUTPUT" | head -10
  fi
fi

# Always exit 0 so we don't block the workflow
exit 0
