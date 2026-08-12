#!/usr/bin/env bash
# gate-obligation-artefact.sh — M1 acceptance gate for the ppa-obligation
# WASM deontic artefact (design doc v6.7.0). Asserts:
#
#   1. BYTE-IDENTICAL  — the WASM engine's settlement report for the
#      DemoSolar payload is byte-for-byte identical to the release `ailang`
#      CLI running the same payload (the determinism / correctness claim).
#   2. RECOMPUTE LATENCY — avg what-if recompute < 20 ms (design gate).
#   3. BOOT — instantiate + go.run + 4-module load, printed vs the 2.5 s gate.
#   4. SIZE — brotli'd wasm size printed (the cold-cache transfer cost).
#
# Asset resolution (prefers the risk-gate scratchpad assets when present, else
# the fetched artefact assets from scripts/fetch-ailang-wasm.sh):
#   WASM   : $WASM_DIR | scratchpad release/wasm | artefact assets
#   ENGINE : ~/.ailang cache | artefact assets/engine | scratchpad
#   CLI    : $AILANG_BIN | scratchpad release/cli/ailang | `ailang` on PATH
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ART_DIR="${REPO_ROOT}/infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1"
ASSETS_DIR="${ART_DIR}/assets"
PAYLOAD="${ASSETS_DIR}/payload.demosolar.json"

# Optional scratchpad from the risk-gate spike (byte-identical v0.29.0 assets).
# Pick the first scratchpad that actually holds the release wasm, not just any.
SCRATCH=""
for d in $(ls -d /private/tmp/claude-*/*/*/scratchpad 2>/dev/null || true); do
  if [ -f "$d/release/wasm/ailang.wasm" ]; then SCRATCH="$d"; break; fi
done

log() { printf '[gate] %s\n' "$*" >&2; }
die() { printf '[gate] FAIL: %s\n' "$*" >&2; exit 1; }

# --- Resolve WASM dir (needs ailang.wasm + wasm_exec.js) ---------------------
resolve_wasm() {
  for d in "${WASM_DIR:-}" "${SCRATCH:+$SCRATCH/release/wasm}" "$ASSETS_DIR"; do
    [ -n "$d" ] && [ -f "$d/ailang.wasm" ] && [ -f "$d/wasm_exec.js" ] && { echo "$d"; return; }
  done
  return 1
}
# --- Resolve engine .ail modules dir ----------------------------------------
resolve_engine() {
  local cache="$HOME/.ailang/cache/registry/sunholo/deontic/0.1.2"
  for d in "${ENGINE_DIR:-}" "$cache" "$ASSETS_DIR/engine" "$SCRATCH"; do
    [ -n "$d" ] && [ -f "$d/api.ail" ] && [ -f "$d/engine.ail" ] && { echo "$d"; return; }
  done
  return 1
}
# --- Resolve ailang CLI ------------------------------------------------------
resolve_cli() {
  if [ -n "${AILANG_BIN:-}" ] && [ -x "$AILANG_BIN" ]; then echo "$AILANG_BIN"; return; fi
  if [ -n "$SCRATCH" ] && [ -x "$SCRATCH/release/cli/ailang" ]; then echo "$SCRATCH/release/cli/ailang"; return; fi
  command -v ailang 2>/dev/null && return
  return 1
}

WASM_DIR_R="$(resolve_wasm)" || die "no WASM assets (run: make fetch-ailang-wasm)"
ENGINE_DIR_R="$(resolve_engine)" || die "no deontic engine .ail modules (run: make fetch-ailang-wasm)"
[ -f "$PAYLOAD" ] || die "missing payload fixture: $PAYLOAD"
command -v node >/dev/null 2>&1 || die "node is required"

log "wasm    : $WASM_DIR_R"
log "engine  : $ENGINE_DIR_R"
log "payload : $PAYLOAD"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- Node helper: boot WASM, compute report, latency probe, emit runner.ail --
cat > "$TMP/gate.mjs" <<'NODE'
import { readFileSync, writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import path from "node:path";
const [WASM, ENGINE, PAYLOAD_PATH, OUT_REPORT, OUT_RUNNER, OUT_METRICS] = process.argv.slice(2);
const P = JSON.parse(readFileSync(PAYLOAD_PATH, "utf8"));

// Suppress the WASM boot banner (printed to stdout by go.run).
const _log = console.log; console.log = () => {};
const bootStart = performance.now();
(0, eval)(readFileSync(path.join(WASM, "wasm_exec.js"), "utf8"));
const go = new globalThis.Go();
const { instance } = await WebAssembly.instantiate(readFileSync(path.join(WASM, "ailang.wasm")), go.importObject);
go.run(instance);
await new Promise((r) => setTimeout(r, 0));
const instMs = performance.now() - bootStart;
const modStart = performance.now();
for (const name of ["types", "settle", "engine", "api"]) {
  const res = globalThis.ailangLoadModule("./" + name, readFileSync(path.join(ENGINE, name + ".ail"), "utf8"));
  if (!res || !res.success) { console.error = _log; console.error("load " + name + " failed: " + (res && res.error)); process.exit(3); }
}
const modMs = performance.now() - modStart;
console.log = _log;

const ob = JSON.stringify(P.obligations), ev = JSON.stringify(P.events), pol = JSON.stringify(P.policy);
const call = () => globalThis.ailangCall("./api", "analyzeContractJson", ob, ev, pol);
const r = call();
if (!r || !r.success) { console.error("engine call failed: " + (r && r.error)); process.exit(3); }
const report = (Array.isArray(r.result) ? r.result : [String(r.result)]).join("\n") + "\n";
writeFileSync(OUT_REPORT, report);

// Latency probe.
const N = 200; const t0 = performance.now();
for (let i = 0; i < N; i++) call();
const perCallMs = (performance.now() - t0) / N;
writeFileSync(OUT_METRICS, JSON.stringify({ instMs, modMs, bootMs: instMs + modMs, perCallMs, N }));

// Structured CLI runner (mirrors ppa_demo.ail shape; avoids JSON-string escaping).
const q = (s) => '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
const ctor = { deliver: (e) => `Deliver(${e.day}, ${q(e.ref)})`, pay: (e) => `Pay(${e.day}, ${q(e.ref)})`,
  amend_price: (e) => `AmendPrice(${e.day}, ${q(e.ref)}, ${e.amt})`, force_majeure: (e) => `ForceMajeure(${e.day}, ${e.amt}, ${e.hi})`,
  notice: (e) => `Notice(${e.day}, ${q(e.ref)})`, waive: (e) => `Waive(${e.day}, ${q(e.ref)})`, terminate: (e) => `Terminate(${e.day}, ${q(e.ref)})` };
const obl = P.obligations.map((o) => `    (${q(o.id)}, ${o.deadline}, ${o.price})`).join(",\n");
const ids = P.obligations.map((o) => q(o.id)).join(", ");
const tl = P.events.map((e) => "    " + (ctor[e.kind] || (() => { throw new Error("kind " + e.kind); }))(e)).join(",\n");
const pp = P.policy;
writeFileSync(OUT_RUNNER, `module sunholo/deontic/runner

import std/io (println)
import ./types (Event, Deliver, Pay, AmendPrice, ForceMajeure, Notice, Waive, Terminate, Policy, initState)
import ./engine (runEvents, report)

func printAll(xs: [string]) -> () ! {IO} {
  match xs { [] => (), h :: t => { println(h); printAll(t) } }
}

export func main() -> () ! {IO} {
  let pol = { penPerDay: ${pp.penPerDay}, penCap: ${pp.penCap}, payWithin: ${pp.payWithin},
              cureDays: ${pp.cureDays}, ratePct: ${pp.ratePct}, ratePeriod: ${pp.ratePeriod} };
  let obligations = [
${obl}
  ];
  let timeline = [
${tl}
  ];
  let st = runEvents(pol, initState(obligations), timeline);
  printAll(report(pol, st, [${ids}]))
}
`);
NODE

node "$TMP/gate.mjs" "$WASM_DIR_R" "$ENGINE_DIR_R" "$PAYLOAD" \
  "$TMP/wasm.out" "$TMP/runner.ail" "$TMP/metrics.json" \
  || die "WASM harness failed"

BOOT_MS=$(node -e 'const m=require(process.argv[1]);console.log(m.bootMs.toFixed(0))' "$TMP/metrics.json")
PERCALL=$(node -e 'const m=require(process.argv[1]);console.log(m.perCallMs.toFixed(3))' "$TMP/metrics.json")

# --- CLI run (byte-identical comparison) ------------------------------------
BYTE_IDENTICAL="skipped"
if CLI="$(resolve_cli)"; then
  WORK="$TMP/cli-work"; mkdir -p "$WORK"
  cp "$ENGINE_DIR_R"/{types,settle,engine,api}.ail "$WORK/"
  # ailang.toml lets the module header resolve ./types etc. Prefer engine dir's
  # own toml; else the deontic cache toml.
  if [ -f "$ENGINE_DIR_R/ailang.toml" ]; then cp "$ENGINE_DIR_R/ailang.toml" "$WORK/";
  elif [ -f "$HOME/.ailang/cache/registry/sunholo/deontic/0.1.2/ailang.toml" ]; then cp "$HOME/.ailang/cache/registry/sunholo/deontic/0.1.2/ailang.toml" "$WORK/"; fi
  cp "$TMP/runner.ail" "$WORK/runner.ail"
  ( cd "$WORK" && AILANG_RELAX_MODULES=1 "$CLI" run --caps IO runner.ail 2>"$TMP/cli.err" >"$TMP/cli.raw" ) \
    || { cat "$TMP/cli.err" >&2; die "CLI run failed"; }
  # The CLI prints progress glyphs (→ Type checking / ✓ Running …) to stdout
  # ahead of the report; drop them so only the settlement report is compared.
  grep -vE '^(→|✓)' "$TMP/cli.raw" > "$TMP/cli.out" || true
  if diff -q "$TMP/wasm.out" "$TMP/cli.out" >/dev/null; then
    BYTE_IDENTICAL="yes"
  else
    log "--- WASM report ---"; cat "$TMP/wasm.out" >&2
    log "--- CLI report ---";  cat "$TMP/cli.out" >&2
    die "byte-identical FAILED (WASM report != CLI report)"
  fi
else
  log "WARN: no ailang CLI resolved — byte-identical vs CLI SKIPPED (WASM golden still asserted below)"
fi

# --- WASM golden assertion (works even without the CLI) ----------------------
GOLDEN_NET="net: Vendor pays Client 125000"
grep -qF "$GOLDEN_NET" "$TMP/wasm.out" || die "WASM report missing golden net line: '$GOLDEN_NET'"

# --- Brotli size --------------------------------------------------------------
WASM_BYTES=$(wc -c < "$WASM_DIR_R/ailang.wasm" | tr -d ' ')
BR_BYTES="n/a"
if [ -f "$ASSETS_DIR/ailang.wasm.br" ]; then BR_BYTES=$(wc -c < "$ASSETS_DIR/ailang.wasm.br" | tr -d ' ');
elif command -v brotli >/dev/null 2>&1; then BR_BYTES=$(brotli -q 11 -c "$WASM_DIR_R/ailang.wasm" | wc -c | tr -d ' '); fi
human() { awk -v b="$1" 'BEGIN{ if(b=="n/a"){print "n/a"} else {printf "%.2f MB", b/1048576} }'; }

# --- Gate evaluation ---------------------------------------------------------
FAIL=0
awk -v v="$PERCALL" 'BEGIN{ exit !(v+0 < 20) }' || { log "latency gate FAIL: ${PERCALL} ms >= 20 ms"; FAIL=1; }
awk -v v="$BOOT_MS" 'BEGIN{ exit !(v+0 <= 2500) }' || { log "boot gate WARN: ${BOOT_MS} ms > 2500 ms (see note)"; }

echo
echo "================ OBLIGATION ARTEFACT GATE ================"
printf '  byte_identical (WASM vs CLI) : %s\n' "$BYTE_IDENTICAL"
printf '  recompute latency (avg/200)  : %s ms   (gate < 20 ms)\n' "$PERCALL"
printf '  boot (instantiate+run+load)  : %s ms   (gate <= 2500 ms)\n' "$BOOT_MS"
printf '  wasm raw size                : %s (%s bytes)\n' "$(human "$WASM_BYTES")" "$WASM_BYTES"
printf '  wasm brotli size             : %s (%s bytes)\n' "$(human "$BR_BYTES")" "$BR_BYTES"
echo "  golden net line              : $GOLDEN_NET"
echo "=========================================================="

[ "$BYTE_IDENTICAL" = "no" ] && FAIL=1
[ "$FAIL" -eq 0 ] || die "one or more gates failed"
echo "[gate] PASS"
