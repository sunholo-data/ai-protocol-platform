#!/usr/bin/env bash
# verify-client-logs.sh — the self-verification command for FRONTEND-OBS
# (docs/design/v6.41.0/frontend-observability.md, "Self-Verification").
#
# Proves, against a DEPLOYED env, that a browser event sent through the real
# authenticated route lands in Cloud Logging as a structured browser line and,
# for ERROR severity, in Error Reporting — and that the refusals which would
# otherwise look healthy actually refuse:
#
#   POSITIVE  an authenticated ERROR probe is accepted, appears in Cloud Logging
#             with source="browser", the TOKEN's uid and the Error Reporting
#             envelope, and appears in Error Reporting itself
#   NEGATIVE  an unauthenticated POST is refused
#   NEGATIVE  a uid in the body is not what gets logged
#   NEGATIVE  an over-cap batch is refused whole — its nonce never appears
#   NEGATIVE  an INFO probe is logged but does NOT reach Error Reporting
#
# What a green run does NOT prove: that the browser calls the route (the
# keepalive-on-hide flush needs real Chrome — aitana-frontend-verify), that
# the error boundaries render, or behaviour under load.
#
# Usage:
#   ./scripts/verify-client-logs.sh            # dev
#   ./scripts/verify-client-logs.sh test
#
# Requires: gcloud (authenticated, with logging + errorreporting read on the
# project), jq, uv. Mints a token via the aiplatform-cli skill unless
# AIPLATFORM_ID_TOKEN is already set. Every wait is a bounded poll — Error
# Reporting ingestion is not instantaneous, and a fixed sleep flakes.

source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

ENV="${1:-dev}"
case "$ENV" in
  dev|test|prod) PROJECT="$(project_for_env "$ENV")" ;;
  *) echo "Unknown env: $ENV (use dev|test|prod)"; exit 2 ;;
esac
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="${CLIENT_LOG_SERVICE_NAME:-browser}"   # serviceContext.service, as the backend sets it
POLL_TIMEOUT="${POLL_TIMEOUT:-120}"
POLL_STEP=5
RUN="$(date +%s)-$RANDOM"

for tool in gcloud jq uv curl; do
  command -v "$tool" >/dev/null || { echo "FAIL required tool missing: $tool"; exit 2; }
done

cli() { (cd "$REPO_ROOT/cli" && uv run --quiet aiplatform --env "$ENV" "$@"); }

echo "== verify-client-logs  env=$ENV  project=$PROJECT  run=$RUN =="

# --- identity ----------------------------------------------------------------
if [[ -z "${AIPLATFORM_ID_TOKEN:-}" ]]; then
  echo "-- minting a token (aiplatform-cli skill) --"
  export GCP_PROJECT="$PROJECT" GOOGLE_CLOUD_PROJECT="$PROJECT"
  eval "$("$REPO_ROOT/.claude/skills/aiplatform-cli/scripts/mint-token.sh")"
fi
export AIPLATFORM_ID_TOKEN
BASE_URL="$(cd "$REPO_ROOT/cli" && uv run --quiet python -c "from aiplatform.http import resolve_base_url; print(resolve_base_url('$ENV'))")"
TOKEN_UID="$(curl -sS --max-time 20 -H "Authorization: Bearer $AIPLATFORM_ID_TOKEN" "$BASE_URL/api/auth/whoami" | jq -r '.uid // empty')"
if [[ -z "$TOKEN_UID" ]]; then
  echo "FAIL could not resolve the token's uid from $BASE_URL/api/auth/whoami"; exit 1
fi
echo "identity: uid=$TOKEN_UID  base=$BASE_URL"

overall=0
ok()   { echo "OK   $*"; }
fail() { echo "FAIL $*"; overall=1; }

# Poll Cloud Logging for browser events carrying $1; prints the JSON array.
find_events() {
  local nonce="$1" deadline=$(( $(date +%s) + POLL_TIMEOUT )) out
  while :; do
    out="$(cli logs client --since 30m -n 20 -g "$nonce" --raw 2>/dev/null | sed -n '/^\[/,$p' || true)"
    if [[ -n "$out" && "$(jq 'length' <<<"$out" 2>/dev/null || echo 0)" -gt 0 ]]; then echo "$out"; return 0; fi
    (( $(date +%s) >= deadline )) && { echo "[]"; return 1; }
    sleep "$POLL_STEP"
  done
}

# Error Reporting, via REST — there is no gcloud surface for this. `events.list`
# is scoped to the tenant group (`groupId` is required; the API says "Missing
# group_id" otherwise), so a cross-group search is groupStats.list to find the
# groups for our service in the window, then events.list per group. All our
# probes share one stack and therefore one group; the representative message
# carries only one nonce, which is why the per-group event listing is needed.
_ER="https://clouderrorreporting.googleapis.com/v1beta1/projects/$PROJECT"
_ER_Q="serviceFilter.service=$SERVICE_NAME&timeRange.period=PERIOD_1_HOUR"
er_search() {   # prints found | absent | ERROR:<message>
  local nonce="$1" token body gid events
  token="$(gcloud auth print-access-token 2>/dev/null)" || { echo "ERROR:no gcloud access token"; return 0; }
  body="$(curl -sS --max-time 30 -H "Authorization: Bearer $token" "$_ER/groupStats?$_ER_Q&pageSize=100")"
  if jq -e '.error' <<<"$body" >/dev/null 2>&1; then echo "ERROR:$(jq -r '.error.message' <<<"$body")"; return 0; fi
  for gid in $(jq -r '.errorGroupStats[]?.group.groupId' <<<"$body"); do
    events="$(curl -sS --max-time 30 -H "Authorization: Bearer $token" "$_ER/events?groupId=$gid&$_ER_Q&pageSize=250")"
    if jq -e '.error' <<<"$events" >/dev/null 2>&1; then echo "ERROR:$(jq -r '.error.message' <<<"$events")"; return 0; fi
    if jq -e --arg n "$nonce" '[.errorEvents[]? | select(.message | contains($n))] | length > 0' <<<"$events" >/dev/null 2>&1; then
      echo "found"; return 0
    fi
  done
  echo "absent"
}
er_has() {
  local nonce="$1" deadline=$(( $(date +%s) + POLL_TIMEOUT )) r
  while :; do
    r="$(er_search "$nonce")"
    [[ "$r" == "found" || "$r" == ERROR* ]] && { echo "$r"; return 0; }
    (( $(date +%s) >= deadline )) && { echo "absent"; return 0; }
    sleep "$POLL_STEP"
  done
}

# --- NEGATIVE: unauthenticated -------------------------------------------------
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -X POST -H 'Content-Type: application/json' \
  -d '{"events":[]}' "$BASE_URL/api/client-logs") || code=000
if [[ "$code" == "401" || "$code" == "403" ]]; then ok "anonymous POST /api/client-logs -> $code"
else fail "anonymous POST /api/client-logs -> $code (expected 401: an open ingest route is a log-injection endpoint)"; fi

# --- NEGATIVE: over-cap batch is refused whole ---------------------------------
CAP_NONCE="cap-$RUN"
if out="$(cli logs client --emit --count 51 --nonce "$CAP_NONCE" 2>&1)"; then
  fail "51-event batch was ACCEPTED: $out (expected 413 — truncation wearing a 202)"
elif grep -q "413" <<<"$out"; then ok "51-event batch refused with 413"
else fail "51-event batch refused, but not with 413: $out"; fi

# --- POSITIVE: an ERROR probe lands, with identity and envelope ----------------
ERR_NONCE="err-$RUN"
resp="$(cli logs client --emit --nonce "$ERR_NONCE" | tail -1)"
if [[ "$(jq -r '.response.accepted' <<<"$resp")" == "1" ]]; then ok "ERROR probe accepted ($ERR_NONCE)"
else fail "ERROR probe not accepted: $resp"; fi

echo "-- waiting for Cloud Logging (≤${POLL_TIMEOUT}s) --"
if events="$(find_events "$ERR_NONCE")"; then
  e="$(jq '.[0]' <<<"$events")"
  [[ "$(jq -r '.severity' <<<"$e")" == "ERROR" ]]                 && ok "logged with severity=ERROR"          || fail "severity: $(jq -r .severity <<<"$e")"
  [[ "$(jq -r '.jsonPayload.source' <<<"$e")" == "browser" ]]     && ok "logged with source=browser"           || fail "source: $(jq -r .jsonPayload.source <<<"$e")"
  [[ "$(jq -r '.jsonPayload.uid' <<<"$e")" == "$TOKEN_UID" ]]     && ok "logged uid is the token's"            || fail "uid: $(jq -r .jsonPayload.uid <<<"$e") != $TOKEN_UID"
  [[ -z "$(jq -r '.jsonPayload["@type"] // empty' <<<"$e")" ]] && ok "no @type on the line (BigQuery cannot take that column)" || fail "@type present — the telemetry sink will drop this row"
  [[ "$(jq -r '.jsonPayload.serviceContext.service // empty' <<<"$e")" == "$SERVICE_NAME" ]] && ok "serviceContext.service=$SERVICE_NAME" || fail "serviceContext: $(jq -c .jsonPayload.serviceContext <<<"$e")"
else
  fail "ERROR probe $ERR_NONCE never appeared in Cloud Logging within ${POLL_TIMEOUT}s"
fi

# --- NEGATIVE: a uid in the body is discarded ----------------------------------
FORGE_NONCE="forge-$RUN"
resp="$(cli logs client --emit --nonce "$FORGE_NONCE" --forge-uid "forged-$RUN" | tail -1)"
[[ "$(jq -r '.response.droppedFields' <<<"$resp")" == "2" ]] && ok "forged uid/userId counted as dropped fields" || fail "droppedFields: $resp"
if events="$(find_events "$FORGE_NONCE")"; then
  if grep -q "forged-$RUN" <<<"$events"; then fail "forged uid reached the log"
  elif [[ "$(jq -r '.[0].jsonPayload.uid' <<<"$events")" == "$TOKEN_UID" ]]; then ok "forged uid discarded; logged uid is the token's"
  else fail "logged uid is neither forged nor the token's: $(jq -r '.[0].jsonPayload.uid' <<<"$events")"; fi
else
  fail "forge probe $FORGE_NONCE never appeared in Cloud Logging"
fi

# --- NEGATIVE: INFO is logged but carries no envelope --------------------------
INFO_NONCE="info-$RUN"
cli logs client --emit --severity INFO --nonce "$INFO_NONCE" >/dev/null
if events="$(find_events "$INFO_NONCE")"; then
  [[ "$(jq -r '.[0].severity' <<<"$events")" == "INFO" ]] && ok "INFO probe logged as INFO" || fail "INFO probe severity: $(jq -r '.[0].severity' <<<"$events")"
  [[ -z "$(jq -r '.[0].jsonPayload.serviceContext // empty' <<<"$events")" ]] && ok "INFO line carries no Error Reporting envelope" || fail "INFO line carries serviceContext — over-reporting"
else
  fail "INFO probe $INFO_NONCE never appeared in Cloud Logging"
fi

# --- Error Reporting: the ERROR is there, the INFO is not ----------------------
echo "-- waiting for Error Reporting (≤${POLL_TIMEOUT}s) --"
er="$(er_has "$ERR_NONCE")"
case "$er" in
  found)   ok "ERROR probe present in Error Reporting (service=$SERVICE_NAME)"
           # Same window, same consistency as the positive just observed, so
           # absence here is meaningful rather than merely "not yet".
           if [[ "$(er_has "$INFO_NONCE")" == "found" ]]; then fail "INFO probe reached Error Reporting"
           else ok "INFO probe absent from Error Reporting"; fi ;;
  absent)  fail "ERROR probe $ERR_NONCE not in Error Reporting within ${POLL_TIMEOUT}s — the log line exists but never grouped" ;;
  ERROR*)  fail "Error Reporting API: ${er#ERROR:} (is clouderrorreporting.googleapis.com enabled, and does this identity have errorreporting.viewer?)" ;;
esac

# --- the refused batch must have logged NOTHING --------------------------------
if [[ "$(cli logs client --since 30m -n 5 -g "$CAP_NONCE" --raw 2>/dev/null | sed -n '/^\[/,$p' | jq 'length' 2>/dev/null || echo 0)" == "0" ]]; then
  ok "refused 51-event batch left no trace in Cloud Logging"
else fail "events from the REFUSED batch appeared in Cloud Logging — partial acceptance"; fi

echo ""
if [[ $overall -eq 0 ]]; then echo "== verify-client-logs: all checks passed =="; else echo "== verify-client-logs: FAILED =="; exit 1; fi
