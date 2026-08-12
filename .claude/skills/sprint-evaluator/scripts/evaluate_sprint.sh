#!/bin/bash
# Run automated quality checks for sprint evaluation
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

SPRINT_ID="${1:-}"

if [ -z "$SPRINT_ID" ]; then
    echo "Usage: $0 <sprint_id>"
    exit 1
fi

JSON_FILE="$REPO_ROOT/.claude/state/sprints/sprint_${SPRINT_ID}.json"

if [ ! -f "$JSON_FILE" ]; then
    echo "Error: Sprint JSON not found at $JSON_FILE"
    exit 1
fi

echo "=== Sprint Evaluation: $SPRINT_ID ==="
echo ""

HARD_FAIL=false

# 1. Frontend lint + typecheck
echo "--- Frontend: Lint + Typecheck ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run quality:check:fast 2>&1 | tail -5); then
        echo "RESULT: PASS (10/10)"
    else
        echo "RESULT: ISSUES DETECTED"
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 2. Frontend tests
echo "--- Frontend: Tests ---"
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run test:run 2>&1 | tail -10); then
        echo "RESULT: PASS (10/10)"
    else
        echo "RESULT: FAIL - HARD FAIL CONDITION"
        HARD_FAIL=true
    fi
else
    echo "SKIP: frontend/package.json not found"
fi
echo ""

# 3. Backend tests
echo "--- Backend: Tests ---"
if [ -d "$REPO_ROOT/backend/.venv" ]; then
    cd "$REPO_ROOT/backend"
    if source .venv/bin/activate 2>/dev/null && pytest tests/ -v --tb=short 2>&1 | tail -15; then
        echo "RESULT: PASS (10/10)"
    else
        echo "RESULT: FAIL - HARD FAIL CONDITION"
        HARD_FAIL=true
    fi
    cd "$REPO_ROOT"
else
    echo "SKIP: Backend venv not found (manual check required)"
fi
echo ""

# 4. File size check (soft cap: 800 lines)
echo "--- File Size Compliance ---"
OVERSIZED=0
while IFS= read -r f; do
    lines=$(wc -l < "$f" | tr -d ' ')
    if [ "$lines" -gt 800 ]; then
        echo "  OVER 800: $f ($lines lines — consider splitting)"
        OVERSIZED=$((OVERSIZED + 1))
    fi
done < <(find frontend/src backend -name "*.tsx" -o -name "*.ts" -o -name "*.py" 2>/dev/null | grep -v "node_modules\|__pycache__\|\.venv\|\.next" || true)

if [ "$OVERSIZED" -eq 0 ]; then
    echo "  PASS: No files over 800 lines"
else
    echo "  NOTE: $OVERSIZED files over soft cap (-1 point each)"
fi
echo ""

# 5. Sprint JSON completeness
echo "--- Sprint JSON Completeness ---"
python3 -c "
import json
with open('$JSON_FILE') as f:
    data = json.load(f)
items = data.get('features') or data.get('milestones', [])
total = len(items)
complete = sum(1 for m in items if m.get('passes') is not None)
with_notes = sum(1 for m in items if m.get('notes'))
with_timestamps = sum(1 for m in items if m.get('completed'))
print(f'  Milestones: {total}')
print(f'  Evaluated (passes set): {complete}/{total}')
print(f'  With notes: {with_notes}/{total}')
print(f'  With timestamps: {with_timestamps}/{total}')
if complete == total and with_notes == total:
    print('  RESULT: Complete')
else:
    print('  RESULT: Incomplete artifacts')
" 2>/dev/null || echo "  Could not parse sprint JSON"
echo ""

# Summary
echo "=== Evaluation Summary ==="
if [ "$HARD_FAIL" = true ]; then
    echo "HARD FAIL detected - sprint cannot pass regardless of score"
else
    echo "Automated checks complete - proceed to acceptance criteria and scoring"
fi
