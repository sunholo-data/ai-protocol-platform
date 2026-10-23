#!/usr/bin/env bash
#
# security-check.sh — single source of truth for Platform's dep security gate.
#
# Runs audits in sequence and exits non-zero on any failure:
#   1. frontend/  — npm audit --omit=dev --audit-level=high
#   2. infrastructure/mcp-sandbox/ — same
#   3. backend/   — pip-audit on uv-exported production deps (OSV database)
#   4. cli/       — same; its HTTP client has a separate trust store lockfile
#
# Both the CI gate (.github/workflows/ci.yml security-audit job) and the
# local Makefile target (`make security-check`) invoke this script — that
# way they cannot drift out of sync. If you need to change the gate's
# behaviour, change it here.
#
# AUDIT_INCLUDE_DEV=1 includes npm development dependencies (weekly scan).
# Policy: see UPGRADING.md#dependency-security
# Triage runbook for failures: UPGRADING.md#dependency-security
#
# Exit codes:
#   0 — all audits and security floors satisfy the documented policy
#   1 — findings, security-floor failures, or scan errors remain
#   2 — script setup error (missing tool, malformed lockfile, etc.)
#
# Explicit exception (see UPGRADING.md#dependency-security):
#   PYSEC-2026-4066 (LiteLLM / CVE-2026-84377) affects its proxy service.
#   This platform runs the SDK, not that proxy; preserve #78's reviewed pin.
# No Starlette exception: upstream already locks the patched 1.x line.
# No OpenTelemetry exception: the coordinated ADK 2 / OTel 1.45.1 upgrade
# resolves its advisory; keep it visible if a fork regresses the lockfile.
#
set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd)"
cd "$REPO_ROOT"

# Colorise only when stdout is a tty (skip in CI logs).
if [ -t 1 ]; then
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_RED=""
  C_GREEN=""
  C_BOLD=""
  C_RESET=""
fi

# Accumulate failures so we can print a single summary instead of
# bailing on the first failure.
FAILURES=()

audit_npm() {
  local dir="$1"
  local label="$2"
  echo
  echo "${C_BOLD}[$label] npm audit ${NPM_SCOPE[*]} --audit-level=high${C_RESET}"
  if (cd "$dir" && npm audit --package-lock-only "${NPM_SCOPE[@]}" --audit-level=high); then
    echo "${C_GREEN}PASS${C_RESET} $label"
  else
    echo "${C_RED}FAIL${C_RESET} $label"
    FAILURES+=("$label")
  fi
}

audit_python() {
  local dir="$1"
  local label="$2"
  echo
  echo "${C_BOLD}[$label] pip-audit (uv export --frozen --no-dev, OSV)${C_RESET}"
  # --no-emit-project drops the local "-e ." line which pip-audit can't hash.
  # --ignore-vuln: only the LiteLLM proxy-only advisory documented above.
  if (cd "$dir" && requirements="$(mktemp)" && trap 'rm -f "$requirements"' EXIT && \
      uv export --frozen --no-dev --no-emit-project --output-file "$requirements" >/dev/null && \
      uvx pip-audit \
       --requirement "$requirements" \
       --no-deps --disable-pip \
       --strict \
       --ignore-vuln PYSEC-2026-4066 \
       --vulnerability-service osv); then
    echo "${C_GREEN}PASS${C_RESET} $label"
  else
    echo "${C_RED}FAIL${C_RESET} $label"
    FAILURES+=("$label")
  fi
}

NPM_SCOPE=(--omit=dev)
if [ "${AUDIT_INCLUDE_DEV:-0}" = "1" ]; then NPM_SCOPE=(--include=dev); fi

if ! node scripts/check-security-floors.mjs; then
  FAILURES+=("A2UI security floor")
fi
audit_npm "frontend"                       "frontend (npm, high+)"
audit_npm "infrastructure/mcp-sandbox"     "sandbox  (npm, high+)"
audit_python "backend"                     "backend  (pip-audit/OSV)"
audit_python "cli"                         "CLI      (pip-audit/OSV)"

echo
echo "${C_BOLD}=== Security audit summary ===${C_RESET}"
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "${C_GREEN}Dependency security policy passed (review any documented exceptions).${C_RESET}"
  echo "Note: dev-only deps + medium-severity findings are caught by the weekly cron"
  echo "(.github/workflows/security-weekly.yml), not this gate."
  exit 0
else
  echo "${C_RED}FAIL — ${#FAILURES[@]} security checks failed (findings or scan errors):${C_RESET}"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  echo
  echo "Triage runbook: UPGRADING.md#dependency-security"
  echo "Policy doc:     UPGRADING.md#dependency-security"
  exit 1
fi
