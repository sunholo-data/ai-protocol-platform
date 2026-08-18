#!/usr/bin/env bash
#
# upstream-merge.sh — pull template changes down from platform-source.
#
# Sprint TEMPLATE-INVERT M7. Design:
# docs/design/template/template-repo-topology.md
#
#   platform change -> make it in platform-source, then run this
#   customer change -> make it here
#
# Usage:
#   make upstream-merge          # fetch, show the plan, stop before committing
#   make upstream-merge GO=1     # ...and commit the merge
#
# WHY A SCRIPT AND NOT "just git merge"
#
# Upstream DELETIONS propagate silently. Git stages a deletion with no conflict
# and no prompt, so a file this deployment owns can vanish in a merge that looks
# clean — a downstream fork lost its own docs/projects/ directory exactly that
# way, and only noticed later. This prints staged deletions before you commit,
# which is the one review step the plain command does not give you.
#
# The merge itself should be boring: parity holds at 0, so upstream and this
# repo are byte-identical on every shared path and conflicts can only come from
# a file BOTH sides genuinely changed.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

GO="${GO:-}"
REMOTE="${UPSTREAM_REMOTE:-upstream}"
BRANCH="${UPSTREAM_BRANCH:-main}"

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "ERROR: no '$REMOTE' remote. Add it:" >&2
  echo "  git remote add $REMOTE https://github.com/sunholo-data/platform-source.git" >&2
  exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is dirty. Commit or stash first — a merge on top of" >&2
  echo "       uncommitted work makes 'what did the merge change?' unanswerable." >&2
  exit 2
fi

echo "Fetching $REMOTE/$BRANCH …"
git fetch --quiet "$REMOTE" "$BRANCH"

BASE="$(git rev-parse HEAD)"
INCOMING="$(git rev-parse "$REMOTE/$BRANCH")"

if git merge-base --is-ancestor "$INCOMING" HEAD 2>/dev/null; then
  echo "Already up to date with $REMOTE/$BRANCH ($(git rev-parse --short "$INCOMING"))."
  exit 0
fi

echo
echo "Incoming commits:"
git --no-pager log --oneline "HEAD..$REMOTE/$BRANCH" | sed 's/^/  /'

echo
echo "Merging (no commit yet) …"
if ! git merge --no-commit --no-ff "$REMOTE/$BRANCH" >/dev/null 2>&1; then
  echo
  echo "CONFLICTS — resolve, then commit:" >&2
  git diff --name-only --diff-filter=U | sed 's/^/  /' >&2
  echo >&2
  echo "Conflicts mean a file was changed on BOTH sides. If it is template" >&2
  echo "content, the upstream version usually wins and your change belongs" >&2
  echo "upstream instead." >&2
  exit 1
fi

DELETIONS="$(git diff --cached --name-only --diff-filter=D)"
if [ -n "$DELETIONS" ]; then
  echo
  echo "!! UPSTREAM DELETIONS — review before committing:"
  echo "$DELETIONS" | sed 's/^/    /'
  echo
  echo "   Git stages these with no conflict, so they are easy to miss. If any"
  echo "   belongs to THIS deployment rather than the template, restore it:"
  echo "       git checkout HEAD -- <path>"
fi

echo
echo "Staged by the merge:"
git diff --cached --stat | tail -20

if [ "$GO" != "1" ]; then
  echo
  echo "Plan only. Re-run with GO=1 to commit, or 'git merge --abort' to back out."
  exit 0
fi

git commit --no-edit
echo
echo "Merged $REMOTE/$BRANCH ($(git rev-parse --short "$INCOMING")) into $(git rev-parse --short "$BASE")."
echo "Run the gates before pushing:  cd backend && make lint && make test-fast"
