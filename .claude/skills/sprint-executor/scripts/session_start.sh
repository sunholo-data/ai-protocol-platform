#!/bin/bash
# Resume sprint execution across sessions
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

SPRINT_ID="${1:-}"

if [ -z "$SPRINT_ID" ]; then
    echo "Usage: $0 <sprint_id>"
    echo ""
    echo "Available sprints:"
    ls "$REPO_ROOT/.claude/state/sprints/" 2>/dev/null | sed 's/sprint_//;s/\.json//' || echo "  (none)"
    exit 1
fi

JSON_FILE="$REPO_ROOT/.claude/state/sprints/sprint_${SPRINT_ID}.json"

if [ ! -f "$JSON_FILE" ]; then
    echo "Error: Sprint JSON not found at $JSON_FILE"
    exit 1
fi

echo "=== Sprint Session Start: $SPRINT_ID ==="
echo ""

# Show sprint status
echo "--- Sprint Overview ---"
python3 -c "
import json, sys
with open('$JSON_FILE') as f:
    data = json.load(f)
print(f\"Status: {data['status']}\")
print(f\"Created: {data['created']}\")
print(f\"Design doc: {data.get('design_doc', 'N/A')}\")
print(f\"Sprint plan: {data.get('sprint_plan', 'N/A')}\")
print()
print('--- Feature Progress ---')
for i, feat in enumerate(data.get('features', []), 1):
    status = '?' if feat['passes'] is None else ('PASS' if feat['passes'] else 'FAIL')
    icon = '?' if feat['passes'] is None else ('v' if feat['passes'] else 'x')
    scope = feat.get('scope', 'unknown')
    print(f\"  [{icon}] {feat['id']}: {feat['description']} ({scope}, ~{feat['estimated_loc']} LOC) - {status}\")
print()
vel = data.get('velocity', {})
print('--- Velocity ---')
print(f\"  Target: {vel.get('target_loc_per_day', '?')} LOC/day\")
print(f\"  Estimated total: {vel.get('estimated_total_loc', '?')} LOC\")
print(f\"  Estimated days: {vel.get('estimated_days', '?')}\")
" 2>/dev/null || echo "  (could not parse JSON)"
echo ""

# Recent git activity
echo "--- Recent Commits ---"
git log --oneline -5 2>/dev/null || echo "  (none)"
echo ""

# Quick test check
echo "--- Quick Health Check ---"
echo -n "Frontend lint+typecheck: "
if [ -f "$REPO_ROOT/frontend/package.json" ]; then
    if (cd "$REPO_ROOT/frontend" && npm run quality:check:fast >/dev/null 2>&1); then echo "PASS"; else echo "FAIL"; fi
else
    echo "SKIP (no frontend/package.json)"
fi
echo ""

echo "=== Ready to Resume ==="
echo "Review the progress above and continue with the next pending milestone."
