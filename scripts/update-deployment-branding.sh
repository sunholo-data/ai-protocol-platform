#!/usr/bin/env bash
#
# Update the per-deployment branding env vars in the FIREBASE_ENV Secret Manager
# secret. Idempotent — safe to re-run; existing NEXT_PUBLIC_BRAND_* lines are
# replaced atomically with the new set, all other secret keys are preserved.
#
# v6.4.0 ONE-DEMO M1: per-deployment branding via build-time env vars
# (see docs/design/v6.4.0/multi-tenant-demo-readiness.md).
#
# Usage:
#   scripts/update-deployment-branding.sh                # interactive, env=dev
#   scripts/update-deployment-branding.sh dev            # explicit
#   scripts/update-deployment-branding.sh dev --yes      # skip confirmation
#
# Safety:
#   - Refuses to write a secret that lacks NEXT_PUBLIC_FIREBASE_PROJECT_ID
#     (proof we read a real Firebase env, not an empty/broken version)
#   - Shows before/after counts and a diff of just BRAND keys before pushing
#   - Disables broken intermediate versions on request
#
# Prereq: gcloud auth (owner@yourcompany.com) with secretmanager.versions.access
#   gcloud config set account owner@yourcompany.com
#
set -euo pipefail

ENV="${1:-dev}"
AUTOCONFIRM=""
[[ "${2:-}" == "--yes" ]] && AUTOCONFIRM="yes"

# --- Per-env config ---
case "$ENV" in
  dev)        PROJECT="your-project-id" ;;
  test)       PROJECT="your-project-id-test" ;;
  prod|production) PROJECT="your-project-id-prod" ;;
  *)
    echo "ERROR: unknown env '$ENV' (expected: dev | test | prod)" >&2
    exit 1
    ;;
esac

SECRET="FIREBASE_ENV"
TMP="$(mktemp -t firebase_env.XXXXXX)"
trap 'rm -f "$TMP" "$TMP.merged"' EXIT

# --- Aitana branding values (edit here to change product strings) ---
# Forks: copy this file, edit BRAND_VARS, run for your env.
BRAND_VARS=$(cat <<'EOF'
NEXT_PUBLIC_BRAND_APP_NAME=Aitana
NEXT_PUBLIC_BRAND_TAGLINE=Contract intelligence for energy advisory
NEXT_PUBLIC_BRAND_DESCRIPTION=Aitana reads contracts clause by clause — extracting every obligation as typed, source-cited terms and computing settlement, indexation and delay damages you can verify.
NEXT_PUBLIC_BRAND_FAVICON=/images/logo/aitana-logo.svg
# LOGO_HERO = the full animated wordmark logo, shown big on the welcome/landing
# screen where it has room. LOGO_AVATAR = the tight, self-contained animated MARK
# (salmon disc + blinking pixel squares + "ai") used top-left AND in every chat
# avatar — one consistent identity that stays legible at 28px (the full hero
# logo's internal margins rendered a tiny smudge at that size). Favicon stays the
# static disc (tabs don't animate SVG reliably).
NEXT_PUBLIC_BRAND_LOGO_HERO=/images/logo/animated-aitana-square.svg
NEXT_PUBLIC_BRAND_LOGO_AVATAR=/images/logo/aitana-mark.svg
NEXT_PUBLIC_BRAND_EMAIL=hello@yourcompany.com
NEXT_PUBLIC_BRAND_GITHUB=
NEXT_PUBLIC_BRAND_DEMO_HERO_EYEBROW=Contract intelligence
NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_A=Read every clause.
NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_B=Verify every number.
NEXT_PUBLIC_BRAND_DEMO_HERO_BODY=Aitana turns your contracts into typed, source-cited obligations — then computes settlement, indexation and delay damages your quants can check to the euro and your counsel can trace to the clause.
NEXT_PUBLIC_BRAND_DEMO_CTA_PRIMARY=Open the assistant
NEXT_PUBLIC_BRAND_DEMO_CTA_SECONDARY=Compare two contracts
NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF=/chat/@aitana-platform/one-assistant
NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF_SECONDARY=/chat/@aitana-platform/one-doc-compare
NEXT_PUBLIC_BRAND_DEMO_TECH_HREF=
EOF
)

echo "=== Update deployment branding ==="
echo "Env:     $ENV"
echo "Project: $PROJECT"
echo "Secret:  $SECRET"
echo "Auth:    $(gcloud config get-value account 2>/dev/null)"
echo ""

# --- Find the last GOOD version to merge from ---
# Strategy: walk back from the latest enabled version, skip any that look broken
# (i.e. fewer than 10 NEXT_PUBLIC_* keys — a real Firebase env has ~12). This
# self-heals from earlier accidental overwrites without manual version-picking.
echo "--- Finding last good version ---"
VERSIONS=$(gcloud secrets versions list "$SECRET" --project="$PROJECT" \
            --filter="state=ENABLED" --format="value(name)" --sort-by="~createTime" 2>/dev/null)

if [[ -z "$VERSIONS" ]]; then
  echo "ERROR: no enabled versions of $SECRET in $PROJECT" >&2
  exit 1
fi

LAST_GOOD=""
for v in $VERSIONS; do
  if gcloud secrets versions access "$v" --secret="$SECRET" --project="$PROJECT" > "$TMP" 2>/dev/null; then
    count=$(grep -c "^NEXT_PUBLIC_" "$TMP" || true)
    if [[ "$count" -ge 10 ]] && grep -q "^NEXT_PUBLIC_FIREBASE_PROJECT_ID=" "$TMP"; then
      LAST_GOOD="$v"
      echo "  v$v: $count NEXT_PUBLIC_* keys, has FIREBASE_PROJECT_ID → using this"
      break
    else
      echo "  v$v: $count NEXT_PUBLIC_* keys, missing FIREBASE_PROJECT_ID → skip (looks broken)"
    fi
  fi
done

if [[ -z "$LAST_GOOD" ]]; then
  echo "ERROR: no version of $SECRET has a complete Firebase config. Aborting." >&2
  exit 1
fi

OLD_COUNT=$(grep -c "^NEXT_PUBLIC_" "$TMP")
echo "  → merging from v$LAST_GOOD ($OLD_COUNT NEXT_PUBLIC_* keys)"
echo ""

# --- Strip any existing NEXT_PUBLIC_BRAND_* lines, then append the new set ---
# This makes the script idempotent — re-running with changed BRAND values
# replaces them rather than duplicating.
grep -v "^NEXT_PUBLIC_BRAND_" "$TMP" > "$TMP.merged"
echo "$BRAND_VARS" >> "$TMP.merged"

NEW_COUNT=$(grep -c "^NEXT_PUBLIC_" "$TMP.merged")
BRAND_COUNT=$(grep -c "^NEXT_PUBLIC_BRAND_" "$TMP.merged" || echo 0)

# --- Sanity check before pushing ---
if ! grep -q "^NEXT_PUBLIC_FIREBASE_PROJECT_ID=" "$TMP.merged"; then
  echo "ERROR: merged file lacks NEXT_PUBLIC_FIREBASE_PROJECT_ID. Refusing to push." >&2
  exit 1
fi

echo "--- About to push new version ---"
echo "  Before: $OLD_COUNT NEXT_PUBLIC_* keys (v$LAST_GOOD)"
echo "  After:  $NEW_COUNT NEXT_PUBLIC_* keys (incl. $BRAND_COUNT BRAND_* keys)"
echo ""
echo "BRAND keys being written:"
grep "^NEXT_PUBLIC_BRAND_" "$TMP.merged" | sed 's/^/  /'
echo ""

if [[ -z "$AUTOCONFIRM" ]]; then
  read -p "Push as new version of $SECRET in $PROJECT? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

# --- Push ---
NEW_VERSION=$(gcloud secrets versions add "$SECRET" --data-file="$TMP.merged" \
              --project="$PROJECT" --format="value(name)")
echo "Created version $NEW_VERSION."
echo ""

# --- Offer to disable any broken intermediate versions ---
# A "broken" version = enabled, NOT the new one, with < 10 NEXT_PUBLIC_* keys.
BROKEN=()
for v in $VERSIONS; do
  [[ "$v" == "$NEW_VERSION" ]] && continue
  if gcloud secrets versions access "$v" --secret="$SECRET" --project="$PROJECT" > "$TMP" 2>/dev/null; then
    count=$(grep -c "^NEXT_PUBLIC_" "$TMP" || true)
    if [[ "$count" -lt 10 ]] || ! grep -q "^NEXT_PUBLIC_FIREBASE_PROJECT_ID=" "$TMP"; then
      BROKEN+=("$v")
    fi
  fi
done

if [[ ${#BROKEN[@]} -gt 0 ]]; then
  echo "--- Found ${#BROKEN[@]} broken version(s): ${BROKEN[*]} ---"
  if [[ -z "$AUTOCONFIRM" ]]; then
    read -p "Disable them? [y/N] " ans
    if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
      for v in "${BROKEN[@]}"; do
        gcloud secrets versions disable "$v" --secret="$SECRET" --project="$PROJECT" >/dev/null
        echo "  v$v: disabled"
      done
    fi
  fi
fi

echo ""
echo "=== Done ==="
echo "Next: trigger a Cloud Build (push to dev branch) to bake the new branding."
