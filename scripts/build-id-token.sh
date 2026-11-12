#!/usr/bin/env bash
# build-id-token.sh — print an ID token for the Cloud Build step's own service
# account, for the given audience, with the `email` claim the backend's
# ADMIN_SEED_ALLOWED_SAS check needs (docs/ops/gotchas.md #13 #14).
#
#   TOKEN=$(bash scripts/build-id-token.sh "$URL")
#
# 1. The metadata server's /identity endpoint. Works in private worker pools.
# 2. Fallback: the IAM Credentials API (generateIdToken), as the build's own
#    service account. Needed on the DEFAULT pool, whose metadata server answers
#    /identity with HTTP 404 while /email and /token work — verified on a
#    downstream fork's first deploy (2026-11-10), where the seed step then ran
#    with an empty token and failed without a word. This path needs
#    roles/iam.serviceAccountTokenCreator for the build SA ON ITSELF and the
#    iamcredentials API enabled in the build project.
#
# `gcloud auth print-identity-token` is not an option: it fails under a
# user-managed build SA ("No identity token can be obtained").
#
# Prints the token on stdout and nothing else; explains a failure on stderr and
# exits non-zero, so a caller using `set -e` stops with the reason in the log.
set -euo pipefail

AUDIENCE="${1:?usage: build-id-token.sh <audience-url>}"
MD="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default"

token=$(curl -sf --max-time 10 -H "Metadata-Flavor: Google" \
  "${MD}/identity?audience=${AUDIENCE}&format=full&include_email=true" || true)
if [ -n "${token}" ]; then
  printf '%s' "${token}"
  exit 0
fi

sa=$(curl -sf --max-time 10 -H "Metadata-Flavor: Google" "${MD}/email") || {
  echo "build-id-token: metadata /identity failed and /email is unreachable — not running in Cloud Build?" >&2
  exit 1
}
access=$(curl -sf --max-time 10 -H "Metadata-Flavor: Google" "${MD}/token" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')

resp=$(curl -sS --max-time 20 -X POST \
  -H "Authorization: Bearer ${access}" -H "Content-Type: application/json" \
  -d "{\"audience\": \"${AUDIENCE}\", \"includeEmail\": true}" \
  "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${sa}:generateIdToken")
token=$(printf '%s' "${resp}" | python3 -c 'import json, sys
try:
    print(json.load(sys.stdin).get("token", ""))
except ValueError:
    print("")')

if [ -z "${token}" ]; then
  echo "build-id-token: could not mint an ID token for ${sa}." >&2
  echo "  metadata /identity is unavailable (default build pool?), and generateIdToken said:" >&2
  echo "  $(printf '%s' "${resp}" | head -c 400)" >&2
  echo "  Grant ${sa} roles/iam.serviceAccountTokenCreator on itself, and enable iamcredentials.googleapis.com." >&2
  exit 1
fi
printf '%s' "${token}"
