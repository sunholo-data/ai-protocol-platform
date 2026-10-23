#!/usr/bin/env bash
# sync-brand-env.sh — copy the NEXT_PUBLIC_BRAND_* keys of one env's
# FIREBASE_ENV secret onto another env's, as a new secret version.
#
# WHY. Branding (app name, favicon, hero/avatar logos, tagline …) is NOT code:
# frontend/src/lib/branding.ts reads NEXT_PUBLIC_BRAND_* at BUILD time and
# falls back to the upstream Sunholo identity when they are unset. The values
# live in each env's FIREBASE_ENV secret (docs/ops/secrets.md), and both
# cloudbuild.yaml and cloudbuild.promote.yaml bake the TARGET project's own
# secret into the UI image. So a promotion carries the code but not the brand:
# on 2026-09-14 assistant.example.com (prod) served `<title>Sunholo</title>` and
# sunholo-logo.svg because the brand keys had been added to dev and test
# but never to prod. This is the "per-env config a merge does not carry"
# bug class — see docs/ops/env-config-parity.md.
#
# What it does:
#   1. reads FIREBASE_ENV from the source and target projects
#   2. takes every NEXT_PUBLIC_BRAND_* line from the source
#   3. rewrites the target: brand lines replaced/appended, everything else
#      (Firebase keys, backend URL, auth mode …) kept byte-for-byte
#   4. shows a KEY-ONLY diff (values are never printed — the secret holds the
#      Firebase Web API key) and, with --yes, adds the new version
#
# It does NOT rebuild anything. The UI only picks the change up on its next
# build, so follow with a promotion of the version the target already runs:
#     make promote FROM=test TO=prod VERSION=vX.Y.Z GO=1
#
# Usage:
#   scripts/sync-brand-env.sh --from test --to prod          # dry run (default)
#   scripts/sync-brand-env.sh --from test --to prod --yes    # add the new version
#
# Needs secretmanager.versions.access on the source and
# secretmanager.versions.add on the target — an operator account, typically.

source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

SECRET_NAME="${BRAND_SECRET_NAME:-FIREBASE_ENV}"
KEY_PREFIX="NEXT_PUBLIC_BRAND_"

FROM_ENV="" TO_ENV="" APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM_ENV="$2"; shift 2 ;;
    --to)   TO_ENV="$2";   shift 2 ;;
    --yes)  APPLY=1;       shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$FROM_ENV" ] && [ -n "$TO_ENV" ] || { echo "usage: $0 --from <env> --to <env> [--yes]" >&2; exit 2; }
[ "$FROM_ENV" != "$TO_ENV" ] || { echo "ERROR: --from and --to are the same env" >&2; exit 2; }

FROM_PROJECT="$(project_for_env "$FROM_ENV")"
TO_PROJECT="$(project_for_env "$TO_ENV")"
[ -n "$FROM_PROJECT" ] || { echo "ERROR: no project for env '$FROM_ENV' (check deploy.env)" >&2; exit 2; }
[ -n "$TO_PROJECT" ]   || { echo "ERROR: no project for env '$TO_ENV' (check deploy.env)" >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

fetch() { # <project> <outfile>
  gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$1" > "$2"
  # Secret Manager returns the bytes as stored; make sure the last line is
  # newline-terminated so appended keys never glue onto it.
  [ -s "$2" ] && [ "$(tail -c1 "$2" | od -An -c | tr -d ' ')" = '\n' ] || echo >> "$2"
}

echo "source: $FROM_ENV ($FROM_PROJECT)  →  target: $TO_ENV ($TO_PROJECT)  secret: $SECRET_NAME"
fetch "$FROM_PROJECT" "$WORK/from.env"
fetch "$TO_PROJECT"   "$WORK/to.env"

grep -E "^${KEY_PREFIX}[A-Z0-9_]*=" "$WORK/from.env" > "$WORK/brand.env" || true
if [ ! -s "$WORK/brand.env" ]; then
  echo "ERROR: $FROM_ENV has no ${KEY_PREFIX}* keys in $SECRET_NAME — nothing to sync" >&2
  exit 1
fi

# Target minus its existing brand lines, then the source's brand block.
grep -vE "^${KEY_PREFIX}[A-Z0-9_]*=" "$WORK/to.env" > "$WORK/new.env" || true
cat "$WORK/brand.env" >> "$WORK/new.env"

# Key-only report: which brand keys the target gains, and which it already
# had (with whether the value changes). No values are printed.
keys() { grep -E "^${KEY_PREFIX}" "$1" | cut -d= -f1 | sort -u; }
echo
echo "brand keys on $FROM_ENV: $(keys "$WORK/brand.env" | wc -l | tr -d ' ')"
for k in $(keys "$WORK/brand.env"); do
  src="$(grep -E "^${k}=" "$WORK/brand.env" | head -1)"
  cur="$(grep -E "^${k}=" "$WORK/to.env" | head -1 || true)"
  if [ -z "$cur" ]; then         echo "  + $k   (missing on $TO_ENV)"
  elif [ "$cur" != "$src" ]; then echo "  ~ $k   (differs on $TO_ENV, will be overwritten)"
  else                            echo "  = $k   (already identical)"
  fi
done
for k in $(keys "$WORK/to.env"); do
  grep -qE "^${k}=" "$WORK/brand.env" || echo "  - $k   (on $TO_ENV only; dropped, not in $FROM_ENV)"
done

if cmp -s "$WORK/to.env" "$WORK/new.env"; then
  echo
  echo "$TO_ENV already matches — nothing to do."
  exit 0
fi

echo
if [ "$APPLY" -ne 1 ]; then
  echo "DRY RUN — re-run with --yes to add a new $SECRET_NAME version on $TO_PROJECT."
  exit 0
fi

gcloud secrets versions add "$SECRET_NAME" --project="$TO_PROJECT" --data-file="$WORK/new.env"
echo
echo "Added. The UI is rebuilt only on its next build — promote the version $TO_ENV"
echo "already runs to bake it in, e.g.:"
echo "    make promote FROM=$FROM_ENV TO=$TO_ENV VERSION=<current $TO_ENV version> GO=1"
