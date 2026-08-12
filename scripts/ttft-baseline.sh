#!/usr/bin/env bash
# scripts/ttft-baseline.sh — A/B-measure instrumentation overhead.
#
# Runs `aiplatform skill probe` N times against a local backend in two modes:
#   AITANA_TTFT_MODE=full  (instrumentation on)
#   AITANA_TTFT_MODE=off   (instrumentation no-op)
#
# Reports p50/p95 first_model_token_ms for each mode and the delta. If
# overhead is >5ms p50, the design contract for ttft-instrumentation.md
# is violated and the sprint should not be moved to implemented/.
#
# IMPORTANT: this script does NOT manage the backend lifecycle. You must
# restart `make dev` between mode flips so the env var is read at the
# correct value (the backend reads AITANA_TTFT_MODE once at import).
#
# Usage:
#   AITANA_TTFT_MODE=full make dev   # in another terminal
#   ./scripts/ttft-baseline.sh full <skill-id> [iterations=20]
#   # then: AITANA_TTFT_MODE=off make dev
#   ./scripts/ttft-baseline.sh off  <skill-id> [iterations=20]
#
# Outputs JSONL to scripts/ttft-baseline-<mode>.jsonl, one report per line.
# Run scripts/ttft-baseline-summarize.sh after both modes to print the
# comparison table.

set -euo pipefail

MODE="${1:?mode required: full or off}"
SKILL_ID="${2:?skill_id required}"
N="${3:-20}"
OUT="scripts/ttft-baseline-${MODE}.jsonl"

if [[ "$MODE" != "full" && "$MODE" != "log" && "$MODE" != "off" ]]; then
    echo "ERROR: mode must be 'full', 'log', or 'off' — got '${MODE}'" >&2
    exit 1
fi

if ! command -v aiplatform >/dev/null 2>&1; then
    echo "ERROR: aiplatform CLI not on PATH. Run: cd cli && uv tool install -e ." >&2
    exit 1
fi

echo "Running ${N} probes in ${MODE} mode against skill=${SKILL_ID}…"
: > "$OUT"

failures=0
for i in $(seq 1 "$N"); do
    if aiplatform --env local skill probe "$SKILL_ID" --json --message "ping ${i}" >> "$OUT" 2>/dev/null; then
        printf "."
    else
        printf "x"
        failures=$((failures + 1))
    fi
done
echo

echo "Wrote $(wc -l < "$OUT" | tr -d ' ') reports to ${OUT} (${failures} failures)."
if [[ "$MODE" != "off" && "$failures" -gt 0 ]]; then
    echo "WARNING: failures in non-off mode usually mean the backend isn't running" >&2
    echo "         in ${MODE} mode (env var not picked up). Restart \`make dev\` with" >&2
    echo "         AITANA_TTFT_MODE=${MODE} explicitly set in the shell." >&2
fi
