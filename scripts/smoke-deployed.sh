#!/usr/bin/env bash
# Smoke-test deployed v6 Cloud Run services.
#
# Mirrors the post-deploy `smoke-deployed` / `smoke-backend` steps in
# cloudbuild.yaml so you can validate any env from your laptop without
# waiting for a fresh Cloud Build run.
#
# Usage:
#   ./scripts/smoke-deployed.sh              # dev (default)
#   ./scripts/smoke-deployed.sh test
#   ./scripts/smoke-deployed.sh prod
#   ./scripts/smoke-deployed.sh dev frontend # only the multi-container service
#   ./scripts/smoke-deployed.sh dev backend  # only the IAM-protected standalone
#   ./scripts/smoke-deployed.sh dev sidecars # only the MCP sidecars (sandbox + ext-apps)
#   ./scripts/smoke-deployed.sh dev channels # only channel webhook reachability
#   ./scripts/smoke-deployed.sh dev all auth # also run the authenticated whoami probe
#
# Requires: gcloud auth (`gcloud auth login`) with invoker on platform-backend
# for the `backend` check. `auth` additionally needs Firebase admin on the
# target project (ADC via `gcloud auth application-default login`).


source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

ENV="${1:-dev}"
TARGET="${2:-all}"
WITH_AUTH="${3:-}"
REGION="europe-west1"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

case "$ENV" in
  dev|test|prod) PROJECT="$(project_for_env "$ENV")" ;;
  *) echo "Unknown env: $ENV (use dev|test|prod)"; exit 2 ;;
esac

echo "== Env: $ENV  Project: $PROJECT  Region: $REGION =="

smoke_frontend() {
  local svc="${DEPLOY_SERVICE_NAME}"
  echo ""
  echo "-- $svc (public, multi-container) --"
  local url
  url=$(gcloud run services describe "$svc" \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$url" ]]; then
    echo "FAIL could not resolve URL for $svc"
    return 1
  fi
  echo "URL: $url"
  local fail=0
  # Public endpoints — expect 200
  for path in "/" "/api/health" "/api/proxy/health" "/api/proxy/api/skills/marketplace"; do
    local code body
    code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 "${url}${path}") || code=000
    body=$(head -c 200 /tmp/smoke-body)
    if [[ "$code" = "200" ]]; then
      echo "OK   ${path} -> 200"
    else
      echo "FAIL ${path} -> ${code} body=${body}"
      fail=1
    fi
  done
  # Auth-protected endpoints — expect 401 without a token (not a Next 404,
  # which would mean the catch-all proxy is missing — see FE-BRINGUP-1).
  for path in "/api/proxy/api/skills" "/api/proxy/api/auth/whoami"; do
    local code body
    code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 "${url}${path}") || code=000
    body=$(head -c 200 /tmp/smoke-body)
    if [[ "$code" = "401" ]]; then
      echo "OK   ${path} -> 401 (auth required)"
    else
      echo "FAIL ${path} -> ${code} (expected 401) body=${body}"
      fail=1
    fi
  done
  # A2A agent card is public discovery — reached via the frontend proxy
  # since the backend Cloud Run service is IAM-protected.
  local code body
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    "${url}/api/proxy/.well-known/agent.json") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "200" ]] && grep -q '"skills"' /tmp/smoke-body; then
    echo "OK   /api/proxy/.well-known/agent.json -> 200 (A2A card, skills[] present)"
  else
    echo "FAIL /api/proxy/.well-known/agent.json -> ${code} body=${body}"
    fail=1
  fi
  return $fail
}

smoke_backend() {
  local svc="${DEPLOY_SERVICE_NAME%-frontend}-backend"
  echo ""
  echo "-- $svc (IAM-protected, standalone) --"
  local url
  url=$(gcloud run services describe "$svc" \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$url" ]]; then
    echo "FAIL could not resolve URL for $svc"
    return 1
  fi
  echo "URL: $url"
  # Token acquisition:
  #  - Service accounts (Cloud Build SA in CI): `--audiences=$URL` produces a
  #    proper audience-bound ID token.
  #  - User accounts (laptop): `--audiences` is rejected; fall back to the
  #    default user ID token, which Cloud Run accepts when the caller has
  #    run.invoker even though its audience is the OAuth client ID.
  #  - Optional: IMPERSONATE_SA=<sa-email> forces SA impersonation for a
  #    laptop to get an audience-bound token (user needs tokenCreator on SA).
  local token
  if [[ -n "${IMPERSONATE_SA:-}" ]]; then
    token=$(gcloud auth print-identity-token \
      --impersonate-service-account="$IMPERSONATE_SA" \
      --audiences="$url")
  elif token=$(gcloud auth print-identity-token --audiences="$url" 2>/dev/null); then
    :
  else
    token=$(gcloud auth print-identity-token)
  fi
  local fail=0
  # /health expects 200
  local code body
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $token" "${url}/health") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "200" ]]; then
    echo "OK   /health -> 200 body=${body}"
  else
    echo "FAIL /health -> ${code} body=${body}"
    fail=1
  fi
  # PROTOCOLS-1A5: /api/chat/spike was a bring-up stub and must be gone.
  # A 200 here means a stale revision is still mounted -- fail loudly.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $token" "${url}/api/chat/spike") || code=000
  if [[ "$code" = "404" || "$code" = "405" ]]; then
    echo "OK   /api/chat/spike -> ${code} (spike removed)"
  else
    echo "FAIL /api/chat/spike -> ${code} (expected 404; spike still mounted?)"
    fail=1
  fi
  # Backend also serves the A2A card directly (authenticated path). This
  # confirms the route exists on the backend; the public probe runs via
  # the frontend proxy in smoke_frontend.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $token" "${url}/.well-known/agent.json") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "200" ]] && grep -q '"skills"' /tmp/smoke-body; then
    echo "OK   /.well-known/agent.json (auth) -> 200 (route present on backend)"
  else
    echo "FAIL /.well-known/agent.json (auth) -> ${code} body=${body}"
    fail=1
  fi
  # RESOURCE-ACCESS: /api/buckets must require auth. An anonymous probe
  # should 401 — proves the router is mounted and the auth dependency is
  # wired. (Full matrix is in backend unit tests; this is an existence probe.)
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    "${url}/api/buckets") || code=000
  if [[ "$code" = "401" || "$code" = "403" ]]; then
    echo "OK   /api/buckets (anon) -> ${code} (auth gate present)"
  else
    echo "FAIL /api/buckets (anon) -> ${code} (expected 401 — router mounted?)"
    fail=1
  fi
  # RICH-MEDIA: /api/media/pdf-info must require auth. An anonymous probe
  # should 401 — proves the media_utils router is mounted.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    "${url}/api/media/pdf-info?url=https://storage.googleapis.com/test/test.pdf") || code=000
  if [[ "$code" = "401" || "$code" = "403" ]]; then
    echo "OK   /api/media/pdf-info (anon) -> ${code} (auth gate present)"
  else
    echo "FAIL /api/media/pdf-info (anon) -> ${code} (expected 401 — router mounted?)"
    fail=1
  fi
  # Bearer with a gcloud identity token: gets past Cloud Run's IAM gate
  # (run.invoker) but FastAPI's get_current_user expects a Firebase ID token,
  # so it rejects with 401 "Malformed Authorization header". A 401 here is
  # the correct outcome — it proves both the IAM gate accepted us AND the
  # Firebase auth dependency is wired in front of the route.
  # End-to-end Firebase auth is covered by `make smoke-auth` (uses real
  # Firebase test users), not this CLI-token smoke.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $token" "${url}/api/buckets") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "401" ]]; then
    echo "OK   /api/buckets (gcloud token) -> 401 (Firebase auth gate present; use make smoke-auth for E2E)"
  else
    echo "FAIL /api/buckets (gcloud token) -> ${code} body=${body} (expected 401)"
    fail=1
  fi
  # Admin role probe (v6.16.0 ADMIN-SCOPE). The admin console decides whether
  # to render at all from this route, so if a deploy drops it the whole surface
  # regresses to "Admins only" for everyone — and that failure is invisible
  # until someone opens /admin. A 401 is the correct outcome here for the same
  # reason as /api/buckets above: the IAM gate accepted us, and the Firebase
  # auth dependency is wired in front. A **404 means the route is missing**,
  # which is the regression this check exists to catch.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -H "Authorization: Bearer $token" "${url}/api/admin/whoami") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "401" ]]; then
    echo "OK   /api/admin/whoami (gcloud token) -> 401 (route present, Firebase auth gate in front)"
  else
    echo "FAIL /api/admin/whoami (gcloud token) -> ${code} body=${body} (expected 401; 404 = route missing)"
    fail=1
  fi
  # MCP server mount: POST with a real initialize. A 200 (with JSON-RPC
  # body) or 202 proves the FastMCP sub-app is mounted correctly.
  # Content-type + Accept must match the streamable-HTTP transport spec.
  code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
    -X POST \
    -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
    "${url}/mcp/") || code=000
  body=$(head -c 200 /tmp/smoke-body)
  if [[ "$code" = "200" || "$code" = "202" ]]; then
    echo "OK   POST /mcp/ initialize -> ${code} (MCP mount serving)"
  else
    echo "FAIL POST /mcp/ initialize -> ${code} body=${body}"
    fail=1
  fi
  # SESSION-MEMORY: session continuity is exercised end-to-end via the AG-UI
  # chat endpoint (/api/skill/{id}/run or /run/sse) with a real skill — that
  # requires a seeded skill ID and a live AGENT_ENGINE_ID, so it's not a
  # stateless HTTP smoke check. Manual verification procedure:
  #   1. POST /api/skill/<id>/run turn 1 → note session_id in response
  #   2. POST /api/skill/<id>/run turn 2 with same session_id → assert context retained
  # The session service wiring is verified at unit-test level (test_session_factories.py)
  # and the Agent Engine resource is confirmed live when AGENT_ENGINE_ID is set.
  echo "NOTE session continuity: verified via unit tests + manual two-turn chat (see smoke docs)"
  return $fail
}

smoke_sidecars() {
  # MCP sidecars: deployed alongside platform-backend in the same project.
  # Both are public (no IAM gate) so probing is straight HTTPS.
  #   - mcp-sandbox: serves /sandbox.html (the postMessage-bridged iframe host)
  #   - mcp-ext-apps-map: serves /mcp via streamable-HTTP JSON-RPC
  echo ""
  echo "-- MCP sidecars (public) --"
  local fail=0

  local sandbox_url
  sandbox_url=$(gcloud run services describe "mcp-sandbox" \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$sandbox_url" ]]; then
    echo "FAIL could not resolve URL for mcp-sandbox"
    fail=1
  else
    echo "URL (sandbox): $sandbox_url"
    local code body
    code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
      "${sandbox_url}/sandbox.html") || code=000
    body=$(head -c 200 /tmp/smoke-body)
    if [[ "$code" = "200" ]]; then
      echo "OK   GET /sandbox.html -> 200 (sandbox host serving)"
    else
      echo "FAIL GET /sandbox.html -> ${code} body=${body}"
      fail=1
    fi
  fi

  local map_url
  map_url=$(gcloud run services describe "mcp-ext-apps-map" \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$map_url" ]]; then
    echo "FAIL could not resolve URL for mcp-ext-apps-map"
    fail=1
  else
    echo "URL (map):     $map_url"
    # Streamable-HTTP MCP servers use POST + the JSON-RPC accept tuple.
    # An initialize succeeds without a session, so this proves the /mcp
    # endpoint is mounted and the server responds with the agreed protocol.
    local code body
    code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
      -X POST \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
      "${map_url}/mcp") || code=000
    body=$(head -c 200 /tmp/smoke-body)
    if [[ "$code" = "200" || "$code" = "202" ]]; then
      echo "OK   POST /mcp initialize -> ${code} (map-server JSON-RPC live)"
    else
      echo "FAIL POST /mcp initialize -> ${code} body=${body}"
      fail=1
    fi
  fi
  return $fail
}

smoke_channels() {
  # Channel-reachability probe: enumerate registered channels from the
  # deployed backend's OpenAPI spec and confirm each `/api/{name}/webhook`
  # endpoint is mounted. We do NOT send real signed payloads — that would
  # require per-channel test secrets. We just confirm the route exists
  # (POST without auth returns 401 from the channel's `verify_webhook`,
  # which is exactly what proves the framework + adapter are wired).
  echo ""
  echo "-- channel webhook reachability --"
  local svc="${DEPLOY_SERVICE_NAME%-frontend}-backend"
  local url
  url=$(gcloud run services describe "$svc" \
    --project="$PROJECT" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null || true)
  if [[ -z "$url" ]]; then
    echo "FAIL could not resolve URL for $svc"
    return 1
  fi
  local token
  token=$(gcloud auth print-identity-token 2>/dev/null || true)
  if [[ -z "$token" ]]; then
    echo "FAIL no gcloud identity token (run gcloud auth login)"
    return 1
  fi
  # Pull OpenAPI; parse out channels-tagged webhook paths. Channels mount
  # under tags=["channels"] (see channels/registry.py), so a single jq
  # filter is enough.
  if ! curl -sS --max-time 20 -H "Authorization: Bearer $token" "${url}/openapi.json" -o /tmp/smoke-openapi.json; then
    echo "FAIL could not fetch /openapi.json"
    return 1
  fi
  local channels
  channels=$(python3 -c '
import json, sys
spec = json.load(open("/tmp/smoke-openapi.json"))
out = []
for path, ops in (spec.get("paths") or {}).items():
    for op in ops.values():
        if "channels" in (op.get("tags") or []) and path.endswith("/webhook"):
            out.append(path)
print("\n".join(sorted(set(out))))
' 2>/dev/null || true)
  if [[ -z "$channels" ]]; then
    echo "NOTE no registered channels in OpenAPI (none enabled via env vars in $ENV)"
    return 0
  fi
  local fail=0
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    # Anonymous POST: 401 = framework rejected (verify_webhook said no) —
    # which proves the route exists AND the verify gate is wired. A 404
    # here would mean the route is missing.
    local code body
    code=$(curl -sS -o /tmp/smoke-body -w '%{http_code}' --max-time 20 \
      -X POST -H 'Content-Type: application/json' -d '{}' "${url}${path}") || code=000
    body=$(head -c 200 /tmp/smoke-body)
    if [[ "$code" = "401" || "$code" = "403" ]]; then
      echo "OK   POST ${path} (anon) -> ${code} (channel verify gate present)"
    else
      echo "FAIL POST ${path} (anon) -> ${code} body=${body} (expected 401 — route mounted?)"
      fail=1
    fi
  done <<< "$channels"
  return $fail
}

smoke_auth() {
  echo ""
  echo "-- authenticated whoami round-trip (env=$ENV) --"
  # Delegates to backend/scripts/whoami_smoke.py: ensures the smoke-test user,
  # rotates its password, signs in via Identity Toolkit REST, then GETs
  # /api/proxy/api/auth/whoami and asserts uid + groupTags round-trip.
  #
  # Named environments (--env dev|test|prod) come from backend/scripts/_env.py,
  # which is deployment-private and excluded from the public template. When it
  # is absent, whoami_smoke resolves config from the NEXT_PUBLIC_FIREBASE_*
  # env vars / frontend/.env.local instead, so drop the flag rather than
  # passing an env name that cannot resolve.
  local -a smoke_args=()
  if [[ -f "$REPO_ROOT/backend/scripts/_env.py" ]]; then
    smoke_args+=(--env "$ENV")
  else
    echo "   (no named environments — resolving Firebase config from env / frontend/.env.local)"
  fi
  if ! (cd "$REPO_ROOT/backend" && uv run python scripts/whoami_smoke.py "${smoke_args[@]}"); then
    return 1
  fi
  return 0
}

overall=0
case "$TARGET" in
  all)      smoke_frontend || overall=1; smoke_backend || overall=1; smoke_sidecars || overall=1; smoke_channels || overall=1 ;;
  frontend) smoke_frontend || overall=1 ;;
  backend)  smoke_backend || overall=1 ;;
  sidecars) smoke_sidecars || overall=1 ;;
  channels) smoke_channels || overall=1 ;;
  *) echo "Unknown target: $TARGET (use all|frontend|backend|sidecars|channels)"; exit 2 ;;
esac

if [[ "$WITH_AUTH" = "auth" ]]; then
  smoke_auth || overall=1
fi

echo ""
if [[ $overall -eq 0 ]]; then
  echo "== All smoke checks passed =="
else
  echo "== Smoke checks FAILED =="
  exit 1
fi
