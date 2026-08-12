#!/usr/bin/env bash
# Pattern 1 smoke harness — action-triggered A2UI runs end-to-end.
#
# Drives 5 successive `aiplatform sessions trigger-action` calls against
# the LOCAL_MODE demo-click-counter skill (sprint ACTION-TRIGGER M3.1)
# via the new bundled write-and-run endpoint
# `POST /api/skills/demo-click-counter/sessions/{id}/surface-action-run`
# (M1, commit d157ee5). Each iteration asserts the AG-UI SSE stream
# emits RUN_STARTED → TOOL_CALL_START(send_a2ui_json_to_client) →
# RUN_FINISHED and that the streamed A2UI tool args carry both
# surfaceId="counter-main" AND an incrementing counter value matching
# the iteration number. Same session_id across all 5 calls keeps
# counter state continuous — that's the verification that the agent
# is reading the persisted action + previous A2UI surface state.
#
# Usage:
#   scripts/smoke-pattern1.sh                 # run 5 iterations, clean up logs
#   scripts/smoke-pattern1.sh --keep-logs     # leave /tmp/pattern1-smoke-iter-*.jsonl in place
#
# Env overrides:
#   AIPLATFORM_API_URL=<url>   Backend URL (default http://localhost:1956).
#                              The aiplatform CLI reads the same var.
#   LOCAL_MODE=1               Must be set on the BACKEND process. The
#                              demo skill is seeded only in LOCAL_MODE.
#                              The script warns if LOCAL_MODE is not set
#                              in *this* shell (signal, not gate).
#   SMOKE_KEEP_LOGS=1          Equivalent to --keep-logs.
#
# Requires:
#   - backend running with LOCAL_MODE=1 (`LOCAL_MODE=1 make dev` in another
#     terminal). The Pattern 1 contract needs auth-bypassed access to the
#     demo-click-counter skill, which only the local-mode test user gets.
#   - demo-click-counter skill seeded (sprint ACTION-TRIGGER M3.1).
#   - aiplatform CLI installed (sprint ACTION-TRIGGER M3.2, `make cli-install`
#     from the repo root).
#
# Exit codes:
#   0  — all 5 iterations passed every assertion
#   1  — at least one iteration failed an assertion
#   2  — precondition failed (CLI missing / backend unreachable)
#
# Idempotency: a fresh session_id is generated per invocation so the
# counter starts at 0 on every run. Re-running the script doesn't
# inherit state from the previous run.

set -euo pipefail

# ---------------------------------------------------------------------------
# argv / env parsing
# ---------------------------------------------------------------------------
KEEP_LOGS=0
for arg in "$@"; do
  case "$arg" in
    --keep-logs) KEEP_LOGS=1 ;;
    -h|--help)
      sed -n '2,40p' "$0"
      exit 0
      ;;
    *)
      echo "smoke-pattern1: unknown arg: $arg" >&2
      echo "Usage: scripts/smoke-pattern1.sh [--keep-logs]" >&2
      exit 2
      ;;
  esac
done
if [[ "${SMOKE_KEEP_LOGS:-0}" = "1" ]]; then
  KEEP_LOGS=1
fi

SKILL_ID="demo-click-counter"
SURFACE_ID="counter-main"
ACTION_NAME="increment"
BACKEND_URL="${AIPLATFORM_API_URL:-http://localhost:1956}"
SESSION_ID="pattern1-smoke-$(date +%s)-$$"

# ---------------------------------------------------------------------------
# preconditions
# ---------------------------------------------------------------------------
if ! command -v aiplatform >/dev/null 2>&1; then
  echo "smoke-pattern1: PRECONDITION FAILED — 'aiplatform' CLI not on PATH." >&2
  echo "  Install with: (cd $(dirname "$0")/.. && make cli-install)" >&2
  exit 2
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "smoke-pattern1: PRECONDITION FAILED — 'jq' not on PATH." >&2
  echo "  Install with: brew install jq" >&2
  exit 2
fi

# Backend reachability — try /health first (canonical), fall back to
# /openapi.json (always present when get_fast_api_app is mounted).
if ! curl -sS --max-time 5 -o /dev/null "${BACKEND_URL}/health" 2>/dev/null \
   && ! curl -sS --max-time 5 -o /dev/null "${BACKEND_URL}/openapi.json" 2>/dev/null; then
  echo "smoke-pattern1: PRECONDITION FAILED — backend at ${BACKEND_URL} not reachable." >&2
  echo "  Start it with: LOCAL_MODE=1 make dev" >&2
  echo "  Or override the URL: AIPLATFORM_API_URL=<url> scripts/smoke-pattern1.sh" >&2
  exit 2
fi

if [[ "${LOCAL_MODE:-}" != "1" ]]; then
  echo "smoke-pattern1: WARN — LOCAL_MODE is not set in this shell." >&2
  echo "  The ${SKILL_ID} skill is LOCAL_MODE-only — make sure the BACKEND was started with LOCAL_MODE=1." >&2
  echo "  (This is a heads-up, not a gate. Proceeding.)" >&2
fi

echo "smoke-pattern1: env"
echo "  BACKEND_URL = ${BACKEND_URL}"
echo "  SESSION_ID  = ${SESSION_ID}"
echo "  SKILL_ID    = ${SKILL_ID}"
echo "  SURFACE_ID  = ${SURFACE_ID}"
echo "  ACTION      = ${ACTION_NAME}"
echo ""

# ---------------------------------------------------------------------------
# assertion helpers
# ---------------------------------------------------------------------------

# Extract all newline-delimited JSON event objects from a file. The CLI
# prints AG-UI events as JSON lines to stdout; tolerate stderr noise mixed
# in via the 2>&1 in the call site.
assert_iteration() {
  local iter="$1"
  local file="$2"
  local exit_code="$3"
  local -a errors=()

  if [[ "$exit_code" -ne 0 ]]; then
    errors+=("aiplatform exit code ${exit_code} (expected 0)")
  fi

  # Parse JSON lines into a stream and extract event types. Lines that
  # aren't valid JSON (CLI stderr, warnings) are skipped silently by jq's
  # -R input mode + the fromjson? operator.
  local types
  types=$(jq -R 'fromjson? | .type // empty' "$file" 2>/dev/null | tr -d '"' || true)

  if ! grep -qx 'RUN_STARTED' <<< "$types"; then
    errors+=("no RUN_STARTED event in stream")
  fi

  local tool_starts
  tool_starts=$(jq -R 'fromjson? | select(.type=="TOOL_CALL_START") | .toolCallName // empty' "$file" 2>/dev/null | tr -d '"' || true)
  if ! grep -qx 'send_a2ui_json_to_client' <<< "$tool_starts"; then
    errors+=("no TOOL_CALL_START with toolCallName=send_a2ui_json_to_client in stream")
  fi

  # Last non-empty event type must be RUN_FINISHED — G41 dedup ensures
  # exactly one terminal event so equality, not membership, is the right
  # assertion. Tolerate trailing blank lines.
  local last_type
  last_type=$(grep -v '^[[:space:]]*$' <<< "$types" | tail -n 1 || true)
  if [[ "$last_type" != "RUN_FINISHED" ]]; then
    errors+=("final event type was '${last_type:-<none>}' (expected RUN_FINISHED)")
  fi

  # Tool-args delta(s) carry the surface JSON in pieces. Concatenate all
  # TOOL_CALL_ARGS deltas (across all tool calls in the stream) and check
  # the assembled string contains both surfaceId AND the expected counter.
  local args_concat
  args_concat=$(jq -Rrs '[inputs | fromjson? | select(.type=="TOOL_CALL_ARGS") | .delta // ""] | join("")' "$file" 2>/dev/null || true)

  if [[ -z "$args_concat" ]]; then
    errors+=("no TOOL_CALL_ARGS deltas in stream (agent did not emit A2UI surface?)")
  else
    if ! grep -q "${SURFACE_ID}" <<< "$args_concat"; then
      errors+=("A2UI tool args did not reference surfaceId='${SURFACE_ID}'")
    fi
    # Counter assertion: the assembled tool args should contain the
    # iteration number. We accept several plausible serialisations the
    # skill author might emit ("count": N, "counter": N, "value": N,
    # or the raw integer inside a text node). Stay lenient — the spirit
    # of the assertion is "the counter advanced", not "the agent picked
    # field name X".
    if ! grep -qE "\"(count|counter|value)\"[[:space:]]*:[[:space:]]*${iter}([^0-9]|$)" <<< "$args_concat" \
       && ! grep -qE "(^|[^0-9])${iter}([^0-9]|$)" <<< "$args_concat"; then
      errors+=("A2UI tool args did not contain expected counter value ${iter}")
    fi
  fi

  if [[ ${#errors[@]} -eq 0 ]]; then
    echo "  iter ${iter}: PASS"
    return 0
  fi
  echo "  iter ${iter}: FAIL"
  for e in "${errors[@]}"; do
    echo "    - $e"
  done
  # Bonus debug: dump the first 30 lines of the offending file unless the
  # user already asked to keep logs (in which case they can read it
  # themselves). Helps with "what did the backend actually emit?" without
  # forcing the caller to know about --keep-logs.
  if [[ "$KEEP_LOGS" -ne 1 ]]; then
    echo "    (first 30 lines of ${file}):"
    head -n 30 "$file" | sed 's/^/      /'
  fi
  return 1
}

# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
pass_count=0
fail_iters=()
generated_files=()

for i in 1 2 3 4 5; do
  out="/tmp/pattern1-smoke-iter-${i}.jsonl"
  generated_files+=("$out")
  # Capture exit code without tripping set -e.
  set +e
  aiplatform sessions trigger-action "$SESSION_ID" \
    --skill "$SKILL_ID" \
    --surface "$SURFACE_ID" \
    --action "$ACTION_NAME" \
    > "$out" 2>&1
  rc=$?
  set -e

  if assert_iteration "$i" "$out" "$rc"; then
    pass_count=$((pass_count + 1))
  else
    fail_iters+=("$i")
  fi
done

# ---------------------------------------------------------------------------
# verdict + cleanup
# ---------------------------------------------------------------------------
echo ""
if [[ $pass_count -eq 5 ]]; then
  echo "PATTERN 1 SMOKE: 5/5 PASS"
  verdict_rc=0
else
  echo "PATTERN 1 SMOKE: ${pass_count}/5 PASS — failures in iter ${fail_iters[*]}"
  verdict_rc=1
fi

if [[ "$KEEP_LOGS" -eq 1 ]]; then
  echo "logs kept at:"
  for f in "${generated_files[@]}"; do
    echo "  $f"
  done
else
  for f in "${generated_files[@]}"; do
    rm -f "$f"
  done
fi

exit $verdict_rc
