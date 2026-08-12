#!/bin/bash
# Move a design document (and its companion sprint plan) into the
# version directory's `implemented/` subfolder, flipping its Status
# frontmatter from Planned/Proposed -> Implemented.
#
# Repo layout:
#   docs/design/<version>/<name>.md                 <- active/planned
#   docs/design/<version>/<name>-sprint.md
#   docs/design/<version>/implemented/<name>.md     <- completed
#   docs/design/<version>/implemented/<name>-sprint.md
#
# Idempotent: re-running on an already-moved doc is a no-op except for
# refreshing the Last Updated date.
#
# Usage: move_to_implemented.sh <doc-name>
#   Example: move_to_implemented.sh agent-factory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <doc-name>"
    echo ""
    echo "Moves docs/design/<version>/<doc-name>.md (and the companion"
    echo "-sprint.md if present) into docs/design/<version>/implemented/"
    echo "and flips Status: Planned|Proposed -> Implemented."
    echo ""
    echo "Available design docs:"
    find "$REPO_ROOT/docs/design" -maxdepth 3 -name "*.md" -type f 2>/dev/null \
        | sed "s|$REPO_ROOT/||" | sort
    exit 1
fi

DOC_NAME="$1"
TODAY=$(date +%Y-%m-%d)

# Locate the doc in any docs/design/<version>/ directory. Excludes files
# already under implemented/ so re-runs don't pick the moved copy.
SOURCE=$(find "$REPO_ROOT/docs/design" -maxdepth 3 -name "$DOC_NAME.md" -type f \
    -not -path "*/implemented/*" 2>/dev/null | head -1)

# If we can't find it at top level, check if it's already in implemented/
# so we can idempotently refresh it.
if [ -z "$SOURCE" ]; then
    ALREADY=$(find "$REPO_ROOT/docs/design" -maxdepth 4 -path "*/implemented/*" \
        -name "$DOC_NAME.md" -type f 2>/dev/null | head -1)
    if [ -n "$ALREADY" ]; then
        echo "Already implemented: ${ALREADY#$REPO_ROOT/}"
        echo "(Nothing to move — the doc is already in implemented/.)"
        exit 0
    fi
    echo "Error: Could not find $DOC_NAME.md under docs/design/"
    echo ""
    echo "Available (non-implemented) design docs:"
    find "$REPO_ROOT/docs/design" -maxdepth 3 -name "*.md" -type f \
        -not -path "*/implemented/*" 2>/dev/null | sed "s|$REPO_ROOT/||" | sort
    exit 1
fi

VERSION_DIR="$(dirname "$SOURCE")"
TARGET_DIR="$VERSION_DIR/implemented"
mkdir -p "$TARGET_DIR"

# BSD-sed (macOS) vs GNU-sed: -i takes a required '' arg on BSD.
if sed --version >/dev/null 2>&1; then
    SED_INPLACE=(sed -i)
else
    SED_INPLACE=(sed -i '')
fi

flip_status_inplace() {
    local file="$1"
    # Flip Planned|planned|Proposed|proposed -> Implemented (case-insensitive
    # on the value; the key stays '**Status**:'). No-op if the line is
    # absent or already 'Implemented'.
    "${SED_INPLACE[@]}" -E 's/(\*\*Status\*\*:[[:space:]]*)([Pp]lanned|[Pp]roposed)/\1Implemented/' "$file"
    # Refresh Last Updated if the line exists.
    "${SED_INPLACE[@]}" -E "s/(\*\*Last Updated\*\*:[[:space:]]*).*/\1$TODAY/" "$file"
}

append_implementation_report_if_missing() {
    local file="$1"
    if grep -q '^## Implementation Report' "$file"; then
        return 0
    fi
    cat >> "$file" <<REPORT

---

## Implementation Report

**Completed**: $TODAY
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
REPORT
}

# Prefer `git mv` when the file is tracked, so git sees a rename (cleaner
# history / blame). Fall back to plain mv for untracked files.
move_tracked() {
    local src="$1"
    local dst_dir="$2"
    if git -C "$REPO_ROOT" ls-files --error-unmatch "$src" >/dev/null 2>&1; then
        git -C "$REPO_ROOT" mv "$src" "$dst_dir/"
    else
        mv "$src" "$dst_dir/"
    fi
}

move_and_refresh() {
    local src="$1"
    local name
    name="$(basename "$src")"
    move_tracked "$src" "$TARGET_DIR"
    local dst="$TARGET_DIR/$name"
    flip_status_inplace "$dst"
    # Only append an Implementation Report to the *design* doc, not the
    # sprint plan — the sprint JSON already carries per-milestone notes.
    if [ "$name" = "${DOC_NAME}.md" ]; then
        append_implementation_report_if_missing "$dst"
    fi
    echo "Moved ${src#$REPO_ROOT/} -> ${dst#$REPO_ROOT/}"
}

move_and_refresh "$SOURCE"

# Companion sprint plan lives alongside the design doc.
SPRINT_PLAN="$VERSION_DIR/${DOC_NAME}-sprint.md"
if [ -f "$SPRINT_PLAN" ]; then
    move_and_refresh "$SPRINT_PLAN"
fi

echo ""
echo "Next steps:"
echo "  1. Fill in the Implementation Report section"
echo "  2. git status   # Verify renames"
echo "  3. git commit -m 'docs($DOC_NAME): move to implemented'"
