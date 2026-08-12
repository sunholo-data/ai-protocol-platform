#!/bin/bash
# Finalize a completed sprint
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

SPRINT_ID="${1:-}"

if [ -z "$SPRINT_ID" ]; then
    echo "Usage: $0 <sprint_id>"
    exit 1
fi

JSON_FILE="$REPO_ROOT/.claude/state/sprints/sprint_${SPRINT_ID}.json"
TODAY=$(date +%Y-%m-%d)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ ! -f "$JSON_FILE" ]; then
    echo "Error: Sprint JSON not found at $JSON_FILE"
    exit 1
fi

echo "=== Finalizing Sprint: $SPRINT_ID ==="
echo ""

# Check all milestones pass
INCOMPLETE=$(python3 -c "
import json
with open('$JSON_FILE') as f:
    data = json.load(f)
incomplete = [f['id'] for f in data.get('features', []) if f['passes'] is not True]
if incomplete:
    print(', '.join(incomplete))
" 2>/dev/null)

if [ -n "$INCOMPLETE" ]; then
    echo "WARNING: These milestones are not marked as passing:"
    echo "  $INCOMPLETE"
    echo ""
    echo "Continue anyway? The evaluator may reject this sprint."
    echo ""
fi

# Update sprint JSON status
python3 -c "
import json
with open('$JSON_FILE', 'r') as f:
    data = json.load(f)
data['status'] = 'completed'
data['last_session'] = '$TIMESTAMP'
data['last_checkpoint'] = 'finalized'
with open('$JSON_FILE', 'w') as f:
    json.dump(data, f, indent=2)
print('Updated sprint status to: completed')
" 2>/dev/null || echo "Warning: Could not update JSON status"

# Move design doc to implemented
DESIGN_DOC=$(python3 -c "
import json
with open('$JSON_FILE') as f:
    print(json.load(f).get('design_doc', ''))
" 2>/dev/null)

if [ -n "$DESIGN_DOC" ] && [ -f "$REPO_ROOT/$DESIGN_DOC" ]; then
    echo ""
    echo "Design doc found: $DESIGN_DOC"
    echo "To mark it (and its companion sprint plan) as Implemented, run:"
    echo "  .claude/skills/design-doc-creator/scripts/move_to_implemented.sh $(basename "$DESIGN_DOC" .md)"
    echo "  # Flips **Status**: Planned|Proposed → Implemented in place and"
    echo "  # appends an Implementation Report stub. Idempotent."
fi

echo ""
echo "=== Sprint $SPRINT_ID Finalized ==="
echo ""
echo "Next steps:"
echo "  1. Review final implementation"
echo "  2. Run full quality check: npm run docker:check"
echo "  3. Invoke sprint-evaluator for independent quality assessment"
echo "  4. Commit and push when ready"
