#!/usr/bin/env bash
# scripts/cli-selftest-mock.sh — end-to-end smoke for the aiplatform CLI.
#
# Boots cli/tests/fixtures/mock_backend.py on an OS-assigned port, points
# AIPLATFORM_API_URL at it, runs the *real installed* `aiplatform skill probe`
# binary as a subprocess, and asserts the printed table contains the
# expected stage names + a non-zero `first_model_token` ms value.
#
# Why bash + real subprocess + real socket: the existing pytest cases use
# respx to stub `httpx.Response`, which catches code-level bugs but not
# transport-level ones (SSE buffering, the httpx.stream lifecycle, real
# socket timing, stdin/stdout pipes). This smoke runs the actual binary
# against an actual socket so transport regressions surface here.
#
# Usage:
#   scripts/cli-selftest-mock.sh
#
# Exit codes:
#   0  — smoke passed
#   1  — generic failure (assertion / unexpected output)
#   2  — `aiplatform` binary missing or broken (run `make cli-install`)
#   3  — mock backend failed to start

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_DIR="${REPO_ROOT}/cli"

# --- Pre-flight: binary is installed and runnable ---

if ! command -v aiplatform >/dev/null 2>&1; then
    echo "ERROR: \`aiplatform\` not on PATH." >&2
    echo "Fix:   make cli-install" >&2
    exit 2
fi

if ! aiplatform --version >/dev/null 2>&1; then
    echo "ERROR: \`aiplatform\` is on PATH but broken." >&2
    echo "Fix:   make cli-reinstall" >&2
    exit 2
fi

# --- Boot the mock backend in the background ---

# Use a fifo to read the OS-assigned port back from the mock without racing
# its stderr output.
PORT_FIFO="$(mktemp -u "${TMPDIR:-/tmp}/aiplatform-mock-port.XXXXXX")"
mkfifo "$PORT_FIFO"

cleanup() {
    local exit_code=$?
    if [[ -n "${MOCK_PID:-}" ]]; then
        kill "${MOCK_PID}" 2>/dev/null || true
        wait "${MOCK_PID}" 2>/dev/null || true
    fi
    rm -f "$PORT_FIFO"
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Open fd 3 → fifo write-end so the python process can write the port
# back without exiting (named pipe stays open as long as a writer holds fd).
exec 3<>"$PORT_FIFO"
# The mock module lives under cli/tests/fixtures/, so we cd there for the
# `python3 -m tests.fixtures.mock_backend` import to resolve regardless of
# the caller's CWD. Background PID is the python interpreter; the cd
# affects only this subprocess.
(
    cd "$CLI_DIR" || exit 3
    exec env AIPLATFORM_MOCK_PORT_FD=3 \
        python3 -m tests.fixtures.mock_backend --port 0 \
        > /dev/null 2>&1
) &
MOCK_PID=$!

# Read the port. mock_backend writes "<port>\n" to fd 3 the moment the
# socket is bound, so this returns within milliseconds.
if ! IFS= read -t 5 -r MOCK_PORT <"$PORT_FIFO"; then
    echo "ERROR: mock backend did not report its port within 5s." >&2
    echo "       Check: cd cli && python3 -m tests.fixtures.mock_backend --port 0" >&2
    exit 3
fi

# Sanity-check: did the process actually bind?
if ! kill -0 "$MOCK_PID" 2>/dev/null; then
    echo "ERROR: mock backend exited before serving a request." >&2
    exit 3
fi

# Quick health probe so we fail fast if the bind succeeded but the server
# isn't accepting connections (e.g. firewall trick).
if ! curl -fsS "http://127.0.0.1:${MOCK_PORT}/health" >/dev/null 2>&1; then
    echo "ERROR: mock backend on :${MOCK_PORT} not responding to /health." >&2
    exit 3
fi

# --- Run the probe ---

OUTPUT_FILE="$(mktemp "${TMPDIR:-/tmp}/aiplatform-selftest.XXXXXX")"

set +e
AIPLATFORM_API_URL="http://127.0.0.1:${MOCK_PORT}" \
AIPLATFORM_ID_TOKEN="selftest-fake-token" \
    aiplatform --env local skill probe mock-skill --message "self-test ping" \
    > "$OUTPUT_FILE" 2>&1
PROBE_EXIT=$?
set -e

# --- Assertions ---

PASS=0

assert_contains() {
    local needle="$1"
    if grep -qF -- "$needle" "$OUTPUT_FILE"; then
        return 0
    fi
    echo "  ✗ output missing expected text: ${needle}" >&2
    PASS=1
}

if [[ "$PROBE_EXIT" -ne 0 ]]; then
    echo "  ✗ aiplatform probe exited ${PROBE_EXIT} (expected 0)" >&2
    PASS=1
fi

assert_contains "TTFT breakdown"
assert_contains "request_received"
assert_contains "session_index_done"
assert_contains "before_agent_done"
assert_contains "before_model_done"
assert_contains "first_model_token"
assert_contains "first_agui_event"
assert_contains "first_sse_byte"
assert_contains "← TTFT"
assert_contains "412.30ms"
assert_contains "gemini-2.5-flash"
assert_contains "routing: fast"
assert_contains "mode:"

if [[ "$PASS" -ne 0 ]]; then
    echo
    echo "--- aiplatform output ---" >&2
    cat "$OUTPUT_FILE" >&2
    echo "--- end output ---" >&2
    rm -f "$OUTPUT_FILE"
    exit 1
fi

rm -f "$OUTPUT_FILE"
echo "✓ aiplatform CLI mock smoke passed (port ${MOCK_PORT})."
