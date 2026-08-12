#!/usr/bin/env bash
# scripts/cli-selftest-live.sh — diagnostic smoke against a real backend.
#
# Runs `aiplatform skill probe` against a *running* `make dev` backend
# on :1956 and asserts that a non-zero `first_model_token_ms` lands in
# the LATENCY_REPORT payload. Useful as a one-command "is the chat path
# responsive" check.
#
# Skip-don't-fail policy
# ----------------------
# This script is safe to invoke from CI or unattended sweeps. Any of these
# preconditions missing => exit 0 with a clear `skipping…` line:
#
#   * backend not on :1956                  (caller hasn't run `make dev`)
#   * `aiplatform` binary missing/broken    (caller hasn't `make cli-install`)
#   * AIPLATFORM_ID_TOKEN unset             (no auth — Firebase token minting
#                                            is owned by the operator)
#   * No seed skill id                      (LOCAL_MODE / 1.18 ships seed
#                                            data; until then the operator
#                                            supplies one)
#
# Exit codes:
#   0  — passed OR cleanly skipped (operator-friendly)
#   1  — backend reachable but probe failed (real bug surface)
#
# Usage:
#   scripts/cli-selftest-live.sh                            # default skill from env
#   scripts/cli-selftest-live.sh my-skill-id                # explicit
#
# Env vars:
#   AIPLATFORM_API_URL            — backend URL (default http://localhost:1956)
#   AIPLATFORM_ID_TOKEN           — Firebase ID token (required; skip if unset)
#   AIPLATFORM_SELFTEST_SKILL_ID  — skill id to probe (required; skip if unset)

set -euo pipefail

API_URL="${AIPLATFORM_API_URL:-http://localhost:1956}"
SKILL_ID="${1:-${AIPLATFORM_SELFTEST_SKILL_ID:-}}"

skip() {
    # Print to stderr so callers grepping stdout don't mistake skips for output.
    echo "skipping live smoke — $1" >&2
    exit 0
}

# --- Pre-flights ---

if ! command -v aiplatform >/dev/null 2>&1; then
    skip "\`aiplatform\` not on PATH (run \`make cli-install\`)"
fi
if ! aiplatform --version >/dev/null 2>&1; then
    skip "\`aiplatform\` is on PATH but broken (run \`make cli-reinstall\`)"
fi

if ! curl -fsS -m 3 "${API_URL}/health" >/dev/null 2>&1; then
    skip "backend not reachable at ${API_URL} (start it with \`make dev\`)"
fi

if [[ -z "${AIPLATFORM_ID_TOKEN:-}" ]]; then
    skip "AIPLATFORM_ID_TOKEN not set (operator-supplied; LOCAL_MODE will fix this)"
fi

if [[ -z "${SKILL_ID}" ]]; then
    skip "no seed skill id (pass as \$1 or set AIPLATFORM_SELFTEST_SKILL_ID)"
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: \`jq\` not on PATH. Install via \`brew install jq\` or \`apt install jq\`." >&2
    exit 1
fi

# --- Probe ---

OUTPUT_FILE="$(mktemp "${TMPDIR:-/tmp}/aiplatform-live.XXXXXX")"
trap 'rm -f "$OUTPUT_FILE"' EXIT INT TERM

echo "▶ probing skill=${SKILL_ID} at ${API_URL}…"

set +e
# Use the binary's own --env=local; AIPLATFORM_API_URL override wins.
aiplatform --env local skill probe "${SKILL_ID}" \
    --message "live-smoke ping $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --json \
    > "$OUTPUT_FILE" 2>&1
PROBE_EXIT=$?
set -e

if [[ "${PROBE_EXIT}" -ne 0 ]]; then
    echo "✗ probe exited ${PROBE_EXIT}" >&2
    echo "--- output ---" >&2
    cat "$OUTPUT_FILE" >&2
    echo "--- end output ---" >&2
    exit 1
fi

# --- Assertion: first_model_token_ms is a positive number ---

FIRST_TOKEN_MS="$(jq -er '.first_model_token_ms // empty' < "$OUTPUT_FILE" 2>/dev/null || true)"

if [[ -z "${FIRST_TOKEN_MS}" ]]; then
    echo "✗ LATENCY_REPORT did not include first_model_token_ms" >&2
    echo "  (backend may have AITANA_TTFT_MODE=off, or the model never responded)" >&2
    cat "$OUTPUT_FILE" >&2
    exit 1
fi

# Bash arithmetic doesn't do floats — compare via awk.
if ! awk -v v="${FIRST_TOKEN_MS}" 'BEGIN { exit (v > 0) ? 0 : 1 }'; then
    echo "✗ first_model_token_ms=${FIRST_TOKEN_MS} is not > 0" >&2
    exit 1
fi

MODEL="$(jq -r '.model_used // "?"' < "$OUTPUT_FILE")"
ROUTING="$(jq -r '.routing_choice // "?"' < "$OUTPUT_FILE")"

printf "✓ first_model_token=%sms (model=%s routing=%s skill=%s)\n" \
    "${FIRST_TOKEN_MS}" "${MODEL}" "${ROUTING}" "${SKILL_ID}"
