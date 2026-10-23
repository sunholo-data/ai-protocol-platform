#!/usr/bin/env bash
# deploy-status.sh — what is ACTUALLY running in each environment?
#
# WHY THIS EXISTS (2026-08-04). Restoring prod after INFRA-1, the version to
# promote was taken from two sources that both said v0.1.4: the dead Cloud Run
# revision, and docs/ops/deployed-urls.md. Both were stale — test had moved to
# v0.1.5 the day before. Prod was restored a release behind, and nothing in the
# process would have caught it.
#
# The deploy runbook's own example is `make promote VERSION=v0.1.4 …`, which is
# exactly the number that was wrong. A runbook cannot carry a current version,
# and a doc that must be hand-updated on every tag will drift. So: ask the
# services.
#
# WHY IT FAILS CLOSED (2026-08-13). The first version sent gcloud's stderr to
# /dev/null, so PERMISSION_DENIED and "Reauthentication failed" both collapsed
# into an empty image string, which version_of() rendered as "(not deployed)".
# Two identical "(not deployed)" strings then compare EQUAL — so the script
# signed off "test and prod are level ((not deployed))" having read nothing at
# all. A green verdict from zero data, in the one script whose entire purpose is
# to stop the promote step trusting a number nobody checked. Run under an
# account without access and it told you what you wanted to hear.
#
# So: only "Cannot find service" means not-deployed. Every other failure is
# named, and the script refuses to render a parity verdict and exits non-zero.
# Same principle as check-iam-posture.sh and check-domains — a checker that
# cannot read its subject must say so, not answer anyway.
#
# Read-only. Run before any promotion.
#
# Deployment-agnostic seams. Defaults are this deployment's; a fork overrides
# them (env, or a sourced deploy.env) rather than editing the logic — the
# read-failure classification below is the portable part, the names are not.
#   PROJECT_FOR    printf template taking the env name  → GCP project id
#   SERVICE_APP    the Cloud Run service carrying ui + backend
#   SERVICE_EXTRA  an optional second service (empty to skip)
set -uo pipefail

REGION="${REGION:-europe-north1}"
PROJECT_FOR="${PROJECT_FOR:-aipla-%s-2026}"
SERVICE_APP="${SERVICE_APP:-aipla-v01-frontend}"
SERVICE_EXTRA="${SERVICE_EXTRA:-aipla-v01-sandbox}"
declare -a ENVS=("$@")
[ $# -eq 0 ] && ENVS=(dev test prod)

ERRFILE="$(mktemp "${TMPDIR:-/tmp}/deploy-status.XXXXXX")"
trap 'rm -f "${ERRFILE}"' EXIT

# Describe a service and echo one field.
#   rc 0 — read it; field on stdout
#   rc 1 — service genuinely absent (a real, reportable "(not deployed)")
#   rc 2 — could NOT read (auth, permission, project, API) — verdict is unsafe;
#          the one-line reason is left in ERRFILE
describe_service() { # $1=project $2=service $3=format
  local out rc
  out="$(gcloud run services describe "$2" --region="${REGION}" --project="$1" \
         --format="$3" 2>&1)"
  rc=$?
  if [ ${rc} -eq 0 ]; then
    printf '%s' "${out}"
    return 0
  fi
  case "${out}" in
    *"Cannot find service"*|*"NOT_FOUND"*)
      return 1 ;;
    *)
      # Written to a FILE, not a variable: this function runs inside a command
      # substitution, so any variable it sets dies with the subshell.
      printf '%s' "${out}" \
        | tr '\n' ' ' \
        | sed -e 's/^ERROR: ([^)]*) //' -e 's/  */ /g' \
        | cut -c1-150 > "${ERRFILE}"
      return 2 ;;
  esac
}

version_of() { # strip everything up to the tag/digest
  local img="$1"
  case "${img}" in
    "")         echo "(no such container)" ;;
    *@sha256:*) echo "digest:${img##*@sha256:}" | cut -c1-19 ;;
    *:*)        echo "${img##*:}" ;;
    *)          echo "(untagged)" ;;
  esac
}

# Plain variables, not an associative array: macOS ships bash 3.2, where
# `declare -A` is a syntax error. This script has to run on the operator's
# laptop, which is a Mac.
FE_VER_TEST=""
FE_VER_PROD=""
READ_FAILED=0

# The answer is only as good as the identity that produced it — name it, since
# the wrong active account is exactly what made this script lie.
echo "gcloud account: $(gcloud config get-value account 2>/dev/null)"
echo

for ENV in "${ENVS[@]}"; do
  case "${ENV}" in
    dev|test|prod) PROJECT="$(printf "${PROJECT_FOR}" "${ENV}")" ;;
    *) echo "Unknown env: ${ENV}" >&2; exit 2 ;;
  esac

  echo "== ${ENV} (${PROJECT})"

  IMAGES="$(describe_service "${PROJECT}" "${SERVICE_APP}" \
            'value(spec.template.spec.containers[].image)')"
  case $? in
    0) UIV="$(version_of "$(printf '%s' "${IMAGES}" | tr ';' '\n' | grep '/ui'      | head -1)")"
       BEV="$(version_of "$(printf '%s' "${IMAGES}" | tr ';' '\n' | grep '/backend' | head -1)")" ;;
    1) UIV="(not deployed)"; BEV="(not deployed)" ;;
    *) UIV="(CANNOT READ)";  BEV="(CANNOT READ)"; READ_FAILED=1
       printf "   %-9s %s\n" "!!" "$(cat "${ERRFILE}")" ;;
  esac

  SB="$(describe_service "${PROJECT}" "${SERVICE_EXTRA}" \
        'value(spec.template.spec.containers[].image)')"
  case $? in
    0) SBV="$(version_of "$(printf '%s' "${SB}" | tr ';' '\n' | grep '/sandbox' | head -1)")" ;;
    1) SBV="(not deployed)" ;;
    *) SBV="(CANNOT READ)"; READ_FAILED=1 ;;
  esac

  [ "${ENV}" = "test" ] && FE_VER_TEST="${UIV}"
  [ "${ENV}" = "prod" ] && FE_VER_PROD="${UIV}"

  printf "   %-9s %s\n" "ui"      "${UIV}"
  printf "   %-9s %s\n" "backend" "${BEV}"
  printf "   %-9s %s\n" "sandbox" "${SBV}"

  # Serving or not. A version number is not health — prod sat on a perfectly
  # correct v0.1.4 reference while returning 500, because the images behind it
  # had been deleted.
  URL="$(describe_service "${PROJECT}" "${SERVICE_APP}" 'value(status.url)')"
  URL_RC=$?
  if [ ${URL_RC} -eq 0 ] && [ -n "${URL}" ]; then
    CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 45 "${URL}/" 2>/dev/null)"
    [ "${CODE}" = "200" ] && printf "   %-9s %s (%s)\n" "serving" "200" "${URL}" \
                          || printf "   %-9s %s (%s)  <-- NOT HEALTHY\n" "serving" "${CODE}" "${URL}"
  elif [ ${URL_RC} -eq 2 ]; then
    READ_FAILED=1
  fi
done

# A checker that could not read its subject must not answer anyway. This is the
# whole point of the 2026-08-13 change: no verdict beats a false one.
if [ ${READ_FAILED} -ne 0 ]; then
  echo
  echo "NO VERDICT — at least one environment could not be read, so parity is unknown."
  echo "  Usual cause: the active gcloud account has no access to these projects,"
  echo "  or its credentials need refreshing:"
  echo "    gcloud auth login                       # re-auth"
  echo "    gcloud config set account <account>     # or: CLOUDSDK_CORE_ACCOUNT=<account> make deploy-status"
  exit 1
fi

# The comparison the promote step actually needs. prod behind test is normal
# between releases; it is only a problem when it is UNINTENDED, which is the
# case this makes visible.
if [ -n "${FE_VER_TEST}" ] && [ -n "${FE_VER_PROD}" ]; then
  echo
  case "${FE_VER_TEST}${FE_VER_PROD}" in
    # Never declare parity between two placeholders — that is the bug this
    # script shipped with. Two absent things are not a matching release.
    *"(not deployed)"*|*"(untagged)"*|*"(no such container)"*)
      echo "NO VERDICT — test=${FE_VER_TEST}  prod=${FE_VER_PROD}"
      echo "  One of them is not carrying a release version; compare by hand."
      exit 1 ;;
  esac
  if [ "${FE_VER_TEST}" = "${FE_VER_PROD}" ]; then
    echo "test and prod are level (${FE_VER_PROD})."
  else
    echo "DRIFT: test=${FE_VER_TEST}  prod=${FE_VER_PROD}"
    echo "  To bring prod level:"
    echo "    make promote VERSION=${FE_VER_TEST} FROM=test TO=prod          # dry-run"
    echo "    make promote VERSION=${FE_VER_TEST} FROM=test TO=prod GO=1"
  fi
fi
