#!/bin/bash
# Verify acceptance criteria from sprint JSON
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

echo "=== Acceptance Criteria Check: $SPRINT_ID ==="
echo ""

python3 -c "
import json

with open('$JSON_FILE') as f:
    data = json.load(f)

total_criteria = 0
met_criteria = 0

# Support both 'features' (legacy) and 'milestones' (current) key
items = data.get('features') or data.get('milestones', [])

for feat in items:
    label = feat.get('description') or feat.get('name', feat['id'])
    print(f\"--- {feat['id']}: {label} ---\")
    print(f\"  Status: {'PASS' if feat.get('passes') else 'NOT PASSING' if feat.get('passes') is False else 'NOT EVALUATED'}\")

    criteria = feat.get('acceptance_criteria', [])
    total_criteria += len(criteria)

    for i, criterion in enumerate(criteria, 1):
        # If milestone passes, count all its criteria as met
        if feat.get('passes'):
            met_criteria += 1
            print(f'  [PASS] {criterion}')
        else:
            print(f'  [????] {criterion}')

    if feat.get('notes'):
        print(f\"  Notes: {feat['notes']}\")
    print()

pct = (met_criteria / total_criteria * 100) if total_criteria > 0 else 0
print(f'=== Summary ===')
print(f'Total criteria: {total_criteria}')
print(f'Criteria met: {met_criteria}')
print(f'Percentage: {pct:.0f}%')
print()
if pct < 50:
    print('HARD FAIL: Less than 50% of acceptance criteria met')
elif pct < 80:
    print(f'Score: {int(pct * 30 / 100)}/30 points')
else:
    print(f'Score: {int(pct * 30 / 100)}/30 points')
" 2>/dev/null || echo "Error parsing sprint JSON"
