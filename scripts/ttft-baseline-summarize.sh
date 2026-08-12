#!/usr/bin/env bash
# scripts/ttft-baseline-summarize.sh — print the full vs off A/B summary.
#
# Reads scripts/ttft-baseline-full.jsonl and scripts/ttft-baseline-off.jsonl
# (produced by scripts/ttft-baseline.sh) and prints p50/p95 first_model_token_ms
# for each plus the delta.
#
# A delta >5ms p50 violates the M5 contract in
# docs/design/v6.1.0/ttft-instrumentation.md — instrumentation is supposed
# to be free, and a measurable hit means the LatencyTracker hot path needs
# revisiting before this sprint can move to implemented/.

set -euo pipefail

FULL_FILE="${1:-scripts/ttft-baseline-full.jsonl}"
OFF_FILE="${2:-scripts/ttft-baseline-off.jsonl}"

if [[ ! -s "$FULL_FILE" ]]; then
    echo "ERROR: $FULL_FILE missing or empty. Run ttft-baseline.sh full <skill> first." >&2
    exit 1
fi
if [[ ! -s "$OFF_FILE" ]]; then
    echo "ERROR: $OFF_FILE missing or empty. Run ttft-baseline.sh off <skill> first." >&2
    exit 1
fi

# Per-mode summary using python for percentiles (jq lacks p95 natively).
python3 - "$FULL_FILE" "$OFF_FILE" <<'PY'
import json
import sys
from statistics import median


def percentile(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def collect(rows, key):
    return [r[key] for r in rows if isinstance(r.get(key), (int, float))]


full_path, off_path = sys.argv[1], sys.argv[2]
full_rows = load(full_path)
off_rows = load(off_path)


def stats(rows, key):
    vals = collect(rows, key)
    if not vals:
        return None
    return {
        "n": len(vals),
        "p50": median(vals),
        "p95": percentile(vals, 95),
        "min": min(vals),
        "max": max(vals),
    }


def fmt(s):
    if s is None:
        return "no data"
    return f"n={s['n']:>3}  p50={s['p50']:>7.2f}ms  p95={s['p95']:>7.2f}ms  min={s['min']:>7.2f}ms  max={s['max']:>7.2f}ms"


print()
print("TTFT A/B baseline (full vs off mode)")
print("=" * 78)

for key in (
    "first_model_token_ms",
    "first_agui_event_ms",
    "first_sse_byte_ms",
    "before_agent_done_ms",
    "before_model_done_ms",
    "total_response_ms",
):
    full_s = stats(full_rows, key)
    off_s = stats(off_rows, key)
    print()
    print(f"  {key}")
    print(f"    full: {fmt(full_s)}")
    print(f"    off : {fmt(off_s)}")
    if full_s and off_s:
        delta = full_s["p50"] - off_s["p50"]
        marker = ""
        if key == "first_model_token_ms":
            marker = "  ← TTFT — must be <5ms" if delta < 5 else "  ← TTFT — VIOLATION (>5ms p50)"
        print(f"    Δp50 (overhead): {delta:+.2f}ms{marker}")

print()
print("=" * 78)
PY
