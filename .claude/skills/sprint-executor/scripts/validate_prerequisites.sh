#!/bin/bash
# Validate prerequisites before starting sprint execution
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

echo "=== Sprint Prerequisites Check ==="
echo ""

# 1. Git status
echo "--- Git Status ---"
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
echo "Branch: $BRANCH"
echo "Uncommitted changes: $DIRTY"
if [ "$DIRTY" -gt 0 ]; then
    echo "WARNING: Working directory has uncommitted changes"
    git status --short
fi
echo ""

# 2. Frontend quality check
echo "--- Frontend: Quick Quality Check ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run quality:check:fast 2>&1 | tail -5); then
        echo "PASS: Frontend lint + typecheck clean"
    else
        echo "FAIL: Frontend quality issues detected"
        echo "Run: cd frontend && npm run quality:check:fast"
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 3. Frontend tests
echo "--- Frontend: Tests ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run test:run 2>&1 | tail -5); then
        echo "PASS: Frontend tests passing"
    else
        echo "FAIL: Frontend tests failing"
        echo "Run: cd frontend && npm run test:run"
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 4. Backend tests
echo "--- Backend: Tests ---"
if [ -d "$REPO_ROOT/backend" ]; then
    cd "$REPO_ROOT/backend"
    if [ -d ".venv" ]; then
        if source .venv/bin/activate 2>/dev/null && pytest tests/ -m "not slow and not integration" -q --tb=line 2>&1 | tail -5; then
            echo "PASS: Backend tests passing"
        else
            echo "FAIL: Backend tests failing"
            echo "Run: cd backend && source .venv/bin/activate && pytest tests/ -v --tb=short"
        fi
    else
        echo "SKIP: Backend venv not found"
    fi
    cd "$REPO_ROOT"
else
    echo "SKIP: Backend directory not found"
fi
echo ""

echo "=== Prerequisites Summary ==="
echo "Review any FAIL or WARNING items before starting the sprint."
