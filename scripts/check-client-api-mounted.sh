#!/usr/bin/env bash
#
# check-client-api-mounted.sh — an exported API-client function with no caller is
# a feature the user cannot reach.
#
# WHY THIS EXISTS (1.1.135 / TUTOR-2 M6, 2026-09-29).
#
# CLAUDE.md carries the row "A whole stack ships with the control unmounted: the
# endpoint works, the API client has a passing test, and the user cannot do the
# thing". It has fired at least four times — `patchClass` (a teacher could not
# rename a class for months), ALS-SHARE M3b (researcher edit/unpublish/delete,
# three months with no call site while three pages said read-only), and twice
# more found while writing 1.1.135. The row is marked **manual**.
#
# Manual did not work. Writing 1.1.135 I hand-audited this exact question and got
# it wrong IN BOTH DIRECTIONS in one sitting: I reported `createCustomApproach`
# as unmounted when it is mounted and tested (I had grepped a function name I
# invented), and I missed six exports that genuinely were. A design doc, a sprint
# plan and two commit messages went out with the wrong claim in them.
#
# So this is the mechanical version. It cannot be fooled by a misremembered name.
#
# ⚠️ A TEST IS NOT A CALL SITE. That is the whole point of the footgun row — "a
# test on the client function proves the client function". Test files are
# excluded from the search deliberately; do not "fix" a failure by adding a test.
#
# Usage: scripts/check-client-api-mounted.sh   (make check-client-api)
set -uo pipefail
cd "$(dirname "$0")/.."

SRC="frontend/src"
ALLOWLIST="scripts/client-api-unmounted-allowlist.txt"

# Clients in scope: every `frontend/src/lib/*Api.ts`, DISCOVERED, not listed.
# Until 2026-11-05 this was a hand-kept list, and four clients written after it
# (privacyNoticeApi, simDraftsApi, teacherFeaturesApi, teacherSimsApi) were never
# added, so the guard had never looked at them. Worse, a listed file that was
# renamed was skipped with `continue`, so the guard could pass having read
# nothing (the "a checker answers when it could not read" footgun). A new client
# now joins by existing, and finding no clients at all is reported, not passed.
# `apiClient.ts` is the fetch wrapper, not an endpoint client.
[ -d "$SRC" ] || { echo "SKIP: $SRC not found"; exit 0; }

CLIENTS="$(find "$SRC/lib" -maxdepth 1 -name '*Api.ts' ! -name 'apiClient.ts' 2>/dev/null | sort)"
if [ -z "$CLIENTS" ]; then
  echo "NOTHING CHECKED: no $SRC/lib/*Api.ts client modules found."
  echo "  This is not a pass. If API clients live elsewhere in this tree, point the"
  echo "  discovery at them; if there are genuinely none, this guard has no subject."
  exit 0
fi

allowed() {
  [ -f "$ALLOWLIST" ] || return 1
  # An allowlist entry is "<file>::<name>" so the same helper name in two
  # clients cannot be waived by accident.
  grep -qE "^$1::$2([[:space:]]|#|$)" "$ALLOWLIST"
}

fail=0
checked=0
unmounted=""

for client in $CLIENTS; do
  base="$(basename "$client")"

  # `export function f`, `export async function f`, `export const f = (` / `= async (`
  names="$(
    grep -oE '^export (async )?function [a-zA-Z0-9_]+' "$client" | awk '{print $NF}'
    grep -oE '^export const [a-zA-Z0-9_]+ = (async )?\(' "$client" | awk '{print $3}'
  )"

  for name in $names; do
    checked=$((checked + 1))
    # Any reference outside the client itself and outside test files. Types and
    # re-exports count: if another module names it, a human can reach it.
    hits="$(
      grep -rl --include='*.ts' --include='*.tsx' -E "\\b$name\\b" "$SRC" 2>/dev/null \
        | grep -v "/$base\$" \
        | grep -vE '(__tests__|\.test\.|\.spec\.)' \
        | head -1
    )"
    [ -n "$hits" ] && continue
    if allowed "$base" "$name"; then continue; fi
    unmounted="$unmounted  $base::$name\n"
    fail=1
  done
done

if [ "$fail" = "1" ]; then
  echo "FAIL: exported API-client function(s) with no call site outside the client and its tests:"
  printf "%b" "$unmounted"
  echo
  echo "Each of these is an endpoint a user cannot reach. Either:"
  echo "  1. mount it — give it a control in the UI; or"
  echo "  2. delete it — dead client code outlives the memory of why it exists; or"
  echo "  3. add it to $ALLOWLIST as '<file>::<name>  # reason', if it is"
  echo "     deliberately unmounted for now. A reason is required, not optional."
  echo
  echo "⚠️ Adding a TEST does not fix this. A test on the client function proves"
  echo "   the client function; it says nothing about whether anyone can click it."
  exit 1
fi

echo "OK: $checked exported client function(s) checked; every one is reachable or allow-listed."
