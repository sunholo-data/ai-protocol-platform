#!/usr/bin/env bash
# check-auth-dispatcher.sh — fail if a STUDENT-facing route depends on the
# Firebase-ONLY `get_current_user` instead of the token-shape dispatcher.
#
# `auth.get_current_user`               → dispatcher: Firebase JWT *or* the
#                                         anonymous-group student JWT (ADR-001).
# `auth.firebase_auth.get_current_user` → Firebase ONLY. A student's group token
#                                         dies at `verify_id_token` → 401
#                                         "Invalid token", on EVERY request,
#                                         unrecoverably (the token is fine, so
#                                         no amount of refresh helps).
#
# Cost of not having this (2026-08-14, v0.1.20): writing / checklist /
# concept per-group progress all imported the Firebase-only symbol. Student
# writing autosave 401'd on prod for every group, so the text survived only in a
# tab-scoped sessionStorage buffer and died with the tab. Live for 3 days on the
# newest route and over a month on the oldest.
#
# Why unit tests could not catch it: each route's tests `dependency_overrides`
# the same symbol the route imports, so they pass in lockstep with the bug. An
# overridden dependency never exercises the dispatch. Real coverage needs a real
# minted group token — see tests/api_tests/test_dual_auth_rejection.py.
#
# TEACHER/RESEARCHER-only routes are legitimately Firebase-only; they are listed
# in ALLOWLIST below, with the reason. Adding a name here is a claim that NO
# anonymous-group student ever calls that route.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTES_DIR="$ROOT/backend/protocols"

# Teacher/researcher-only surfaces: a group JWT has no business here, and the
# Firebase-only verifier rejecting it is the intended outcome.
ALLOWLIST="
teacher_prefs_routes.py:teacher-only — a group JWT has no teacher account to hold defaults
research_lens_routes.py:researcher-only — R1-quarantined, denies non-researchers
programme_routes.py:researcher/programme-admin only — delegated admin of the access register (1.1.76)
"

fail=0
checked=0

for f in "$ROUTES_DIR"/*.py; do
  [ -f "$f" ] || continue
  base="$(basename "$f")"

  grep -qE '^from auth\.firebase_auth import .*\bget_current_user\b' "$f" || continue
  checked=$((checked + 1))

  reason="$(printf '%s\n' "$ALLOWLIST" | grep "^${base}:" | cut -d: -f2- || true)"
  if [ -n "$reason" ]; then
    printf '  ok (allowlisted) %-38s %s\n' "$base" "$reason"
    continue
  fi

  fail=1
  echo "FAIL: $base imports the Firebase-ONLY get_current_user."
  echo "      Every anonymous-group student token 401s on this route."
  echo "      Fix:  from auth import User, get_current_user"
  echo "      If this route is teacher/researcher-only, add it to ALLOWLIST in $0."
  echo
done

if [ "$fail" -ne 0 ]; then
  echo "Student-facing routes must use the auth.get_current_user dispatcher (ADR-001)."
  exit 1
fi

echo "check-auth-dispatcher: OK (${checked} firebase-only import(s), all allowlisted)"
