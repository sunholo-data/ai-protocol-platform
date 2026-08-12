#!/bin/bash
# Create structured JSON progress file for multi-session sprint execution
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
STATE_DIR="$REPO_ROOT/.claude/state/sprints"

if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then
    echo "Usage: $0 <sprint_id> <sprint_plan_md> [design_doc_md]"
    echo ""
    echo "Example: $0 SLACK-INT docs/design/v6.0.0/slack-sprint.md docs/design/v6.0.0/slack-design.md"
    echo ""
    echo "Creates: .claude/state/sprints/sprint_<id>.json"
    exit 1
fi

SPRINT_ID="$1"
SPRINT_PLAN="$2"
DESIGN_DOC="${3:-}"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
JSON_FILE="$STATE_DIR/sprint_${SPRINT_ID}.json"

# Check if already exists
if [ -f "$JSON_FILE" ]; then
    echo "Warning: Sprint JSON already exists at $JSON_FILE"
    echo "Delete it first if you want to recreate."
    exit 1
fi

mkdir -p "$STATE_DIR"

# Create JSON template
cat > "$JSON_FILE" << EOF
{
  "sprint_id": "$SPRINT_ID",
  "created": "$TIMESTAMP",
  "estimated_duration_days": 0,
  "design_doc": "$DESIGN_DOC",
  "sprint_plan": "$SPRINT_PLAN",
  "features": [
    {
      "id": "MILESTONE_ID",
      "description": "PLACEHOLDER - Replace with real milestone",
      "scope": "frontend|backend|fullstack",
      "estimated_loc": 0,
      "actual_loc": null,
      "dependencies": [],
      "acceptance_criteria": [
        "Criterion 1 - REPLACE",
        "Criterion 2 - REPLACE"
      ],
      "files_to_create": [],
      "files_to_modify": [],
      "passes": null,
      "started": null,
      "completed": null,
      "notes": null
    }
  ],
  "velocity": {
    "target_loc_per_day": 0,
    "actual_loc_per_day": null,
    "estimated_total_loc": 0,
    "actual_total_loc": null,
    "estimated_days": 0,
    "actual_days": null
  },
  "github_issues": [],
  "last_session": null,
  "last_checkpoint": null,
  "status": "not_started"
}
EOF

echo "Created sprint JSON: $JSON_FILE"
echo ""
echo "IMPORTANT: This is a TEMPLATE - you MUST populate it with real milestones!"
echo ""
echo "Required edits:"
echo "  1. Replace MILESTONE_ID with real milestone IDs"
echo "  2. Replace PLACEHOLDER descriptions"
echo "  3. Add real acceptance criteria (not 'Criterion 1')"
echo "  4. Set estimated_loc for each milestone"
echo "  5. Set velocity targets and duration"
echo "  6. Add at least 2 milestones"
echo ""
echo "Validation: sprint-executor will REJECT placeholders."
