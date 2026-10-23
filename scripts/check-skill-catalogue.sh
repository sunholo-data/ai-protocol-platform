#!/usr/bin/env bash
# check-skill-catalogue.sh — fail if CLAUDE.md's "Project Skills" catalogue names
# a skill that does not exist in .claude/skills/. Guards against the recurring
# drift where CLAUDE.md tells an agent to load a skill that was never added or was
# removed in a sanitize (handover audit P0.3).
#
# It parses only the bold-backtick bullet HEADERS in the "## Project Skills"
# section (e.g. `- **`aitana-adk-testing`** — ...`), so prose/blockquote
# references (like the "referenced-but-not-present" note) are intentionally
# ignored. Grouped headers (`- **`a` / `b`**`) check every name.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_MD="$ROOT/CLAUDE.md"
SKILLS_DIR="$ROOT/.claude/skills"

# Skills that live outside the project dir (global ~/.claude/skills) — allowed.
GLOBAL_ALLOWLIST=" skill-builder "

# Slice the "## Project Skills" section (up to the next "## " heading).
section="$(awk '/^## Project Skills/{f=1} f&&/^## /&&!/^## Project Skills/{exit} f' "$CLAUDE_MD")"

missing=0
checked=0
while IFS= read -r line; do
  # Only bold-backtick bullet headers: line begins with "- **`"
  case "$line" in
    "- **\`"*) ;;
    *) continue ;;
  esac
  # Leading bold span = text between the first "**" and the next "**".
  bold="${line#- \*\*}"
  bold="${bold%%\*\**}"
  # Extract every `backticked` token from that span.
  names="$(printf '%s\n' "$bold" | grep -oE '`[a-z][a-z0-9-]+`' | tr -d '`' || true)"
  for name in $names; do
    checked=$((checked + 1))
    if [ -d "$SKILLS_DIR/$name" ]; then
      continue
    fi
    case "$GLOBAL_ALLOWLIST" in
      *" $name "*) continue ;;
    esac
    echo "MISSING: CLAUDE.md catalogues skill '$name' but .claude/skills/$name does not exist"
    missing=$((missing + 1))
  done
done <<< "$section"

if [ "$missing" -gt 0 ]; then
  echo ""
  echo "FAIL: $missing catalogued skill(s) missing from .claude/skills/ (checked $checked)."
  echo "Fix: add the skill, or update the catalogue in CLAUDE.md (Project Skills)."
  exit 1
fi

echo "OK: all $checked catalogued project skills exist in .claude/skills/"
