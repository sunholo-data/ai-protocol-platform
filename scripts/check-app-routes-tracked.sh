#!/usr/bin/env bash
# Every Next.js route in the app tree must be tracked by git.
#
# WHY THIS EXISTS (2026-09-11, 1.1.109)
#
# `.gitignore` carried an unanchored `logs/`, meant for log output. Git applies
# such a pattern at EVERY depth, so it also matched
# `frontend/src/app/teacher/research/logs/` — a real route. The result:
#
#   * `git add <that dir>` refused it (with a hint, on stderr, among others)
#   * `git commit` SUCCEEDED
#   * the commit shipped the nav link, the API client and the backend
#   * the page itself and its ten tests were never committed
#
# Nothing failed. `git status` looked clean. The build stayed green, because a
# missing route is not a compile error and the missing test file simply stopped
# being counted. That is the shape this repo's footgun table calls a reassuring
# wrong answer: the failure and the success are indistinguishable at a glance.
#
# The specific rule is fixed (anchored to `/logs/`), but the CLASS is not: the
# ignore file still carries unanchored `dist/`, `build/`, `coverage/` and
# `htmlcov/`, any of which would do the same to a route of that name. Anchoring
# them speculatively risks un-ignoring nested build output that legitimately
# depends on the loose match, so this guard catches the outcome instead of
# trying to enumerate the causes.
#
# Run: make check-routes-tracked
set -euo pipefail

cd "$(dirname "$0")/.."

APP_DIR="frontend/src/app"
if [[ ! -d "$APP_DIR" ]]; then
  echo "check-app-routes-tracked: $APP_DIR not found — nothing to check."
  exit 0
fi

fail=0

# Every file that defines or tests a route. A route directory with an ignored
# page.tsx is the bug; an ignored test file is the same bug one step quieter.
while IFS= read -r f; do
  if git check-ignore -q "$f"; then
    rule=$(git check-ignore -v "$f" | awk '{print $1}')
    echo "IGNORED  $f"
    echo "         matched by $rule"
    fail=1
  elif ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    # Untracked but not ignored: fine locally (a new file mid-edit), but never
    # in CI, where the checkout is exactly what was committed.
    if [[ -n "${CI:-}" ]]; then
      echo "UNTRACKED $f (present in the tree but not committed)"
      fail=1
    fi
  fi
done < <(find "$APP_DIR" -type f \( -name 'page.tsx' -o -name 'layout.tsx' -o -name 'route.ts' -o -name '*.test.tsx' \) | sort)

if [[ "$fail" -ne 0 ]]; then
  cat <<'MSG'

One or more app-router files are invisible to git.

If a .gitignore rule matched: anchor it (`/logs/` not `logs/`) rather than
force-adding the file — a force-add fixes today's commit and leaves the trap
armed for the next file added to that directory.

If it is merely untracked: `git add` it.
MSG
  exit 1
fi

echo "OK: every app-router page/layout/route/test is tracked and un-ignored."
