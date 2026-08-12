#!/bin/bash
# Analyze recent development velocity from git history
set -euo pipefail

DAYS="${1:-7}"
REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT"

echo "=== Development Velocity Analysis (last $DAYS days) ==="
echo ""

# Recent commits
echo "--- Recent Commits (last $DAYS days) ---"
git log --oneline --since="${DAYS} days ago" --no-merges 2>/dev/null | head -20 || echo "  (no commits)"
echo ""

# Commit count
COMMIT_COUNT=$(git log --oneline --since="${DAYS} days ago" --no-merges 2>/dev/null | wc -l | tr -d ' ')
echo "Total commits: $COMMIT_COUNT"
echo ""

# Files changed stats
echo "--- Files Changed (last $DAYS days) ---"
git diff --stat "$(git log --since="${DAYS} days ago" --format=%H --no-merges | tail -1 2>/dev/null || echo HEAD~10)" HEAD 2>/dev/null | tail -1 || echo "  (no changes)"
echo ""

# Frontend vs Backend breakdown
echo "--- Frontend vs Backend Breakdown ---"
FRONTEND_CHANGES=$(git log --since="${DAYS} days ago" --no-merges --name-only --pretty=format: 2>/dev/null | grep -E '^\s*(src/|public/|next\.)' | wc -l | tr -d ' ')
BACKEND_CHANGES=$(git log --since="${DAYS} days ago" --no-merges --name-only --pretty=format: 2>/dev/null | grep -E '^\s*backend/' | wc -l | tr -d ' ')
echo "Frontend file changes: $FRONTEND_CHANGES"
echo "Backend file changes: $BACKEND_CHANGES"
echo ""

# Insertions/deletions
echo "--- LOC Summary ---"
git log --since="${DAYS} days ago" --no-merges --shortstat --pretty=format: 2>/dev/null | \
    awk '/files changed/ {f+=$1; i+=$4; d+=$6} END {
        printf "Files changed: %d\n", f;
        printf "Insertions: +%d\n", i;
        printf "Deletions: -%d\n", d;
        printf "Net LOC: %d\n", i-d;
        if ('$DAYS' > 0) printf "Avg LOC/day: ~%d\n", (i-d)/'$DAYS'
    }' || echo "  (no stats available)"
echo ""

# Active areas
echo "--- Most Active Directories ---"
git log --since="${DAYS} days ago" --no-merges --name-only --pretty=format: 2>/dev/null | \
    grep -v '^$' | \
    sed 's|/[^/]*$||' | \
    sort | uniq -c | sort -rn | head -10 || echo "  (none)"
echo ""

echo "=== Velocity Summary ==="
echo "Use this data to estimate realistic sprint capacity."
echo "Recommended: plan for 60-80% of observed velocity."
