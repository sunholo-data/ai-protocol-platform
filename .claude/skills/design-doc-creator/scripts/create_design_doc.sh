#!/bin/bash
# Create a new design document in docs/design/<version>/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
TEMPLATE="$SKILL_DIR/templates/design-doc.md"

if [ -z "${1:-}" ] || [ -z "${2:-}" ]; then
    echo "Usage: $0 <version> <doc-name>"
    echo ""
    echo "Example: $0 6.0.0 slack-integration"
    echo "Creates: docs/design/v6.0.0/slack-integration.md"
    echo ""
    echo "Version format: MAJOR.MINOR.PATCH (e.g., 6.0.0, 6.1.0, 6.0.1)"
    exit 1
fi

VERSION="$1"
DOC_NAME="$2"
TODAY=$(date +%Y-%m-%d)
TARGET_DIR="$REPO_ROOT/docs/design/v$VERSION"
TARGET_FILE="$TARGET_DIR/$DOC_NAME.md"

# Check if doc already exists
if [ -f "$TARGET_FILE" ]; then
    echo "Error: Design doc already exists at $TARGET_FILE"
    exit 1
fi

# Create target directory
mkdir -p "$TARGET_DIR"

# Copy template and fill in dates
sed -e "s/YYYY-MM-DD/$TODAY/g" "$TEMPLATE" > "$TARGET_FILE"

echo "Created design doc: $TARGET_FILE"
echo ""
echo "Next steps:"
echo "  1. Fill in the template sections"
echo "  2. Score against product axioms (docs/product-axioms.md)"
echo "  3. Review with team"
echo "  4. git add $TARGET_FILE && git commit -m 'Add design doc for $DOC_NAME'"

# Show existing design docs for reference
echo ""
echo "=== Existing Design Docs ==="
if [ -d "$REPO_ROOT/docs/design/planned" ]; then
    find "$REPO_ROOT/docs/design/planned" -name "*.md" -type f 2>/dev/null | sort || echo "  (none)"
fi
if [ -d "$REPO_ROOT/docs/design/implemented" ]; then
    find "$REPO_ROOT/docs/design/implemented" -name "*.md" -type f 2>/dev/null | sort || echo "  (none)"
fi
