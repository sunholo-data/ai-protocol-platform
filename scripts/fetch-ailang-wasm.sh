#!/usr/bin/env bash
# fetch-ailang-wasm.sh — pull the PINNED AILANG WASM runtime + the pinned
# sunholo/deontic engine package into the ppa-obligation-analysis artefact's
# assets dir, verifying both against published checksums.
#
# WHY: the artefact ships a *verified* deontic reasoning engine (WASM) plus
# the engine source (.ail modules). ailang moves fast, so the runtime version
# lives in exactly one reviewed place (AILANG_VERSION, default below / root
# Makefile var / cloudbuild substitution). Never mix runtime and bundle
# versions (design-doc pinning rule).
#
# The 41 MB ailang.wasm binary is NEVER committed (see assets/.gitignore) —
# this script (or CI) reconstitutes it from the pinned release on demand.
#
# Idempotent: re-running with the same versions and intact, checksum-matching
# assets is a no-op. Bump AILANG_VERSION / DEONTIC_VERSION to refetch.
#
# Usage:
#   scripts/fetch-ailang-wasm.sh
#   AILANG_VERSION=v0.29.0 DEONTIC_VERSION=0.1.2 scripts/fetch-ailang-wasm.sh
set -euo pipefail

AILANG_VERSION="${AILANG_VERSION:-v0.29.0}"
DEONTIC_VERSION="${DEONTIC_VERSION:-0.1.2}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS_DIR="${REPO_ROOT}/infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1/assets"
ENGINE_DIR="${ASSETS_DIR}/engine"
STAMP="${ASSETS_DIR}/.ailang-version"

WASM_TARBALL_URL="https://github.com/sunholo-data/ailang/releases/download/${AILANG_VERSION}/ailang-wasm.tar.gz"
WASM_SHA_URL="${WASM_TARBALL_URL}.sha256"
DEONTIC_BASE="https://storage.googleapis.com/ailang-registry/packages/sunholo/deontic/${DEONTIC_VERSION}"
DEONTIC_TARBALL_URL="${DEONTIC_BASE}/package.tar.gz"
DEONTIC_METADATA_URL="${DEONTIC_BASE}/metadata.json"

log() { printf '[fetch-ailang-wasm] %s\n' "$*" >&2; }
die() { printf '[fetch-ailang-wasm] ERROR: %s\n' "$*" >&2; exit 1; }

# Portable sha256 (macOS `shasum`, Linux `sha256sum`).
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else die "no sha256sum/shasum available"; fi
}

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar  >/dev/null 2>&1 || die "tar is required"

WANT="${AILANG_VERSION}:${DEONTIC_VERSION}"

# --- Idempotency: skip if everything is already in place and matching --------
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$WANT" ] \
   && [ -f "${ASSETS_DIR}/ailang.wasm" ] && [ -f "${ASSETS_DIR}/wasm_exec.js" ] \
   && [ -f "${ENGINE_DIR}/api.ail" ]; then
  log "assets already at ${WANT} — nothing to do (delete ${STAMP} to force)"
  exit 0
fi

mkdir -p "$ASSETS_DIR" "$ENGINE_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- 1. WASM runtime (GitHub release, verified against its .sha256) ----------
log "fetching WASM runtime ${AILANG_VERSION}"
curl -fsSL "$WASM_TARBALL_URL" -o "${TMP}/ailang-wasm.tar.gz" \
  || die "download failed: ${WASM_TARBALL_URL}"
curl -fsSL "$WASM_SHA_URL" -o "${TMP}/ailang-wasm.tar.gz.sha256" \
  || die "download failed: ${WASM_SHA_URL}"

EXPECT_WASM="$(awk '{print $1}' "${TMP}/ailang-wasm.tar.gz.sha256")"
GOT_WASM="$(sha256_of "${TMP}/ailang-wasm.tar.gz")"
[ "$EXPECT_WASM" = "$GOT_WASM" ] \
  || die "WASM tarball checksum mismatch: expected ${EXPECT_WASM}, got ${GOT_WASM}"
log "WASM tarball verified (sha256 ${GOT_WASM})"

tar -xzf "${TMP}/ailang-wasm.tar.gz" -C "$TMP" ailang.wasm wasm_exec.js
mv -f "${TMP}/ailang.wasm"   "${ASSETS_DIR}/ailang.wasm"
mv -f "${TMP}/wasm_exec.js"  "${ASSETS_DIR}/wasm_exec.js"

# --- 2. deontic engine package (registry, verified against tarball_hash) -----
log "fetching sunholo/deontic ${DEONTIC_VERSION}"
curl -fsSL "$DEONTIC_TARBALL_URL"  -o "${TMP}/deontic.tar.gz" \
  || die "download failed: ${DEONTIC_TARBALL_URL}"
curl -fsSL "$DEONTIC_METADATA_URL" -o "${TMP}/metadata.json" \
  || die "download failed: ${DEONTIC_METADATA_URL}"

# tarball_hash is "sha256:<hex>" in metadata.json — no jq dependency.
EXPECT_DEONTIC="$(grep -o '"tarball_hash"[[:space:]]*:[[:space:]]*"sha256:[0-9a-f]*"' "${TMP}/metadata.json" \
  | grep -o 'sha256:[0-9a-f]*' | sed 's/^sha256://')"
[ -n "$EXPECT_DEONTIC" ] || die "could not read tarball_hash from metadata.json"
GOT_DEONTIC="$(sha256_of "${TMP}/deontic.tar.gz")"
[ "$EXPECT_DEONTIC" = "$GOT_DEONTIC" ] \
  || die "deontic tarball checksum mismatch: expected ${EXPECT_DEONTIC}, got ${GOT_DEONTIC}"
log "deontic tarball verified (sha256 ${GOT_DEONTIC})"

rm -rf "${ENGINE_DIR:?}"/*.ail "${ENGINE_DIR}/ailang.toml" 2>/dev/null || true
tar -xzf "${TMP}/deontic.tar.gz" -C "$ENGINE_DIR" \
  types.ail settle.ail engine.ail api.ail ailang.toml

# --- 3. Brotli precompression (best effort; serve.ts falls back to raw) ------
if command -v brotli >/dev/null 2>&1; then
  log "brotli-compressing ailang.wasm (q11)"
  brotli -f -q 11 -o "${ASSETS_DIR}/ailang.wasm.br" "${ASSETS_DIR}/ailang.wasm"
  RAW=$(wc -c < "${ASSETS_DIR}/ailang.wasm")
  BR=$(wc -c < "${ASSETS_DIR}/ailang.wasm.br")
  log "ailang.wasm ${RAW} bytes -> ailang.wasm.br ${BR} bytes"
else
  log "WARN: brotli not found — skipping .br precompression (raw wasm still served)"
  rm -f "${ASSETS_DIR}/ailang.wasm.br" 2>/dev/null || true
fi

printf '%s\n' "$WANT" > "$STAMP"
log "done: runtime=${AILANG_VERSION} engine=${DEONTIC_VERSION} -> ${ASSETS_DIR}"
