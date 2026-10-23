#!/usr/bin/env bash
# Default: dry run. --apply requires committed lockfiles at remote default HEAD.
# Scan/auth errors fail; npm audit absence never proves an alert fixed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/reconcile_dependabot_alerts.py" "$@"
