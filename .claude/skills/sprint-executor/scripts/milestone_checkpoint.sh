#!/bin/bash
# Post-milestone quality gate
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

MILESTONE="${1:-}"
SPRINT_ID="${2:-}"

if [ -z "$MILESTONE" ]; then
    echo "Usage: $0 <milestone_name> [sprint_id]"
    exit 1
fi

echo "=== Milestone Checkpoint: $MILESTONE ==="
echo ""
PASS=true

# 1. Frontend quality
echo "--- Frontend: Lint + Typecheck ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run quality:check:fast 2>&1 | tail -3); then
        echo "PASS"
    else
        echo "FAIL: Fix lint/typecheck errors before proceeding"
        PASS=false
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 2. Frontend tests
echo "--- Frontend: Tests ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run test:run 2>&1 | tail -3); then
        echo "PASS"
    else
        echo "FAIL: Fix failing tests before proceeding"
        PASS=false
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 3. Backend tests
echo "--- Backend: Tests ---"
if [ -d "$REPO_ROOT/backend/.venv" ]; then
    cd "$REPO_ROOT/backend"
    if source .venv/bin/activate 2>/dev/null && pytest tests/ -m "not slow and not integration" -q --tb=line 2>&1 | tail -3; then
        echo "PASS"
    else
        echo "FAIL: Fix backend test failures"
        PASS=false
    fi
    cd "$REPO_ROOT"
else
    echo "SKIP: Backend venv not found"
fi
echo ""

# 4. Git diff summary
echo "--- Changes in This Milestone ---"
git diff --stat HEAD~1 2>/dev/null || git diff --stat
echo ""

# 5. File size check
echo "--- File Size Check ---"
LARGE_FILES=$(find frontend/src/components -name "*.tsx" -o -name "*.ts" 2>/dev/null | while read f; do
    lines=$(wc -l < "$f" | tr -d ' ')
    if [ "$lines" -gt 300 ]; then
        echo "  WARNING: $f ($lines lines, max 300)"
    fi
done)
if [ -n "$LARGE_FILES" ]; then
    echo "$LARGE_FILES"
    echo "Consider splitting large components"
else
    echo "PASS: No oversized component files"
fi

LARGE_UTILS=$(find frontend/src/utils frontend/src/lib -name "*.ts" 2>/dev/null | while read f; do
    lines=$(wc -l < "$f" | tr -d ' ')
    if [ "$lines" -gt 200 ]; then
        echo "  WARNING: $f ($lines lines, max 200)"
    fi
done)
if [ -n "$LARGE_UTILS" ]; then
    echo "$LARGE_UTILS"
fi
echo ""

# 6. Sprint JSON reminder
if [ -n "$SPRINT_ID" ]; then
    JSON_FILE="$REPO_ROOT/.claude/state/sprints/sprint_${SPRINT_ID}.json"
    if [ -f "$JSON_FILE" ]; then
        echo "--- Sprint JSON Update Reminder ---"
        echo "Update $JSON_FILE:"
        echo "  - Set passes: true/false for milestone $MILESTONE"
        echo "  - Set completed timestamp"
        echo "  - Add notes summary"
    fi
fi
echo ""

# Summary
echo "=== Checkpoint Result ==="
if [ "$PASS" = true ]; then
    echo "PASS - Milestone $MILESTONE checkpoint passed"
    echo "Safe to proceed to next milestone."
    exit 0
else
    echo "FAIL - Fix issues before proceeding"
    echo "DO NOT mark milestone as complete until all checks pass."
    exit 1
fi
