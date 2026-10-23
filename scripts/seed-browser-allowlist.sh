#!/usr/bin/env bash
#
# seed-browser-allowlist.sh — grant browser access, without a redeploy.
#
# The allowlist lives in Firestore admin config rather than an env var for two
# recorded reasons:
#   * per-env values that a code merge does not carry are a recurring source of
#     production drift (docs/ops/env-config-parity.md), and
#   * the admin collection is the durable control plane a deploy does not
#     overwrite, so a grant survives the next release.
#
# Deny-by-default: a user absent from this document cannot browse.
#
# Usage:
#   scripts/seed-browser-allowlist.sh <project-id> --users a@x.com,b@x.com
#   scripts/seed-browser-allowlist.sh <project-id> --domains operator.example
#   scripts/seed-browser-allowlist.sh <project-id> --show
#   scripts/seed-browser-allowlist.sh <project-id> --disable    # kill switch
#   scripts/seed-browser-allowlist.sh <project-id> --enable

set -euo pipefail

PROJECT="${1:-}"
if [[ -z "$PROJECT" || "$PROJECT" == -* ]]; then
    echo "usage: $0 <project-id> [--users a,b] [--domains x,y] [--show] [--enable|--disable]" >&2
    exit 2
fi
shift

USERS=""; DOMAINS=""; ACTION="set"; ENABLED=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --users)   USERS="$2"; shift 2 ;;
        --domains) DOMAINS="$2"; shift 2 ;;
        --show)    ACTION="show"; shift ;;
        --enable)  ENABLED="true"; shift ;;
        --disable) ENABLED="false"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# `uv run --with` rather than bare python3: the firestore client is not in the
# system interpreter, and per CLAUDE.md nothing in this repo should assume a
# global pip. This works from any directory with no venv activated.
uv run --quiet --with google-cloud-firestore python3 - "$PROJECT" "$ACTION" "$USERS" "$DOMAINS" "$ENABLED" <<'PY'
import sys
from google.cloud import firestore

project, action, users, domains, enabled = sys.argv[1:6]
client = firestore.Client(project=project)
ref = client.collection("admin_config").document("browser_allowlist")

if action == "show":
    snap = ref.get()
    if not snap.exists:
        print("no browser_allowlist document — the browser is denied to everyone (deny-by-default)")
        sys.exit(0)
    cfg = snap.to_dict() or {}
    print(f"enabled : {cfg.get('enabled', True)}")
    print(f"users   : {', '.join(cfg.get('users') or []) or '(none)'}")
    print(f"domains : {', '.join(cfg.get('domains') or []) or '(none)'}")
    sys.exit(0)

payload = {}
if users:
    payload["users"] = [u.strip() for u in users.split(",") if u.strip()]
if domains:
    payload["domains"] = [d.strip().lstrip("@") for d in domains.split(",") if d.strip()]
if enabled:
    payload["enabled"] = enabled == "true"

if not payload:
    print("nothing to change; pass --users, --domains, --enable or --disable", file=sys.stderr)
    sys.exit(2)

ref.set(payload, merge=True)
print(f"updated admin_config/browser_allowlist in {project}: {payload}")
print("takes effect within 60s (the service caches the allowlist for that long)")
PY
