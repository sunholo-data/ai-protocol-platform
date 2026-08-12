#!/usr/bin/env bash
# scripts/verify-a2a.sh — assert the deployed agent card is A2A-spec-
# compliant and ready for Gemini Enterprise / peer-agent discovery.
#
# G43 (template-a2a-spec-compliance.md): three classes of A2A spec
# violations all sailed past the platform's 6 pytest cases — Discovery
# Engine's JSON-schema validator was the first thing strict enough to
# catch them. This probe is that strict-validator-style check, runnable
# in CI BEFORE any Gemini Enterprise registration attempt:
#
#   * url field is a public URL, not localhost/127.0.0.1
#   * protocolVersion field exists (Discovery Engine rejects without it)
#   * capabilities.extensions[] are AgentExtension objects with uri,
#     not bare strings (Discovery Engine: "unexpected instance type")
#   * X-A2A-Extensions request → response negotiation round-trips
#   * Vary: X-A2A-Extensions is set so caches behave
#   * skills[] is non-empty (warning if zero — may be intentional)
#
# Usage:
#   AP_URL=https://<your-fork-host>.run.app ./scripts/verify-a2a.sh
#   ./scripts/verify-a2a.sh                    # defaults below
#
# Exit code 1 on any assertion failure → suitable for CI gates.
# Skip-don't-fail on missing curl/jq so it's safe in lightweight images.
set -euo pipefail

AP_URL="${AP_URL:-http://localhost:3000}"
CARD_URL="${AP_URL%/}/.well-known/agent.json"

skip() { echo "skipping verify-a2a — $1" >&2; exit 0; }
ok()   { printf "  \033[32mOK\033[0m   %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m %s\n" "$1"; FAILED=1; }
warn() { printf "  \033[33mWARN\033[0m %s\n" "$1"; }
info() { printf "  \033[36m...\033[0m  %s\n" "$1"; }

command -v curl >/dev/null 2>&1 || skip "curl not on PATH"
command -v jq   >/dev/null 2>&1 || skip "jq not on PATH"

FAILED=0
echo "verify-a2a: probing ${CARD_URL}"
echo

info "fetching card with X-A2A-Extensions: a2ui-v0.9, a2ui-decoupled-pattern"
HEADERS_FILE="$(mktemp)"
BODY_FILE="$(mktemp)"
trap 'rm -f "$HEADERS_FILE" "$BODY_FILE"' EXIT

STATUS=$(curl -s -o "$BODY_FILE" -D "$HEADERS_FILE" -w "%{http_code}" \
  -H 'X-A2A-Extensions: a2ui-v0.9, a2ui-decoupled-pattern' \
  "$CARD_URL" || echo "000")

if [[ "$STATUS" == "200" ]]; then
  ok "HTTP 200 (unauthenticated discovery)"
else
  fail "expected HTTP 200, got $STATUS"
  echo
  echo "verify-a2a: aborting — no card to validate" >&2
  exit 1
fi

# --- Header round-trip ----------------------------------------------------

NEGOTIATED=$(grep -i '^x-a2a-extensions:' "$HEADERS_FILE" | head -1 | sed 's/^[^:]*: *//' | tr -d '\r')
VARY=$(grep -i '^vary:' "$HEADERS_FILE" | sed 's/^[^:]*: *//' | tr -d '\r')

if [[ -n "$NEGOTIATED" ]]; then
  ok "X-A2A-Extensions echoed on response: ${NEGOTIATED}"
else
  fail "X-A2A-Extensions header missing on response"
fi
if echo "$VARY" | grep -qi "X-A2A-Extensions"; then
  ok "Vary advertises X-A2A-Extensions (cache-correctness)"
else
  fail "Vary does NOT include X-A2A-Extensions"
fi

# --- Required fields ------------------------------------------------------

# protocolVersion is required by Discovery Engine / Gemini Enterprise — a
# missing one makes `agents-cli register-gemini-enterprise --registration-type
# a2a` fail with INVALID_ARGUMENT.
for field in protocolVersion name description url version capabilities skills; do
  if jq -e ".${field}" "$BODY_FILE" >/dev/null 2>&1; then
    ok "card has required field: ${field}"
  else
    fail "card MISSING required field: ${field}"
  fi
done

# --- url field is a public URL --------------------------------------------

ADVERTISED_URL=$(jq -r '.url' "$BODY_FILE")
if [[ "$ADVERTISED_URL" == http*localhost* ]] || [[ "$ADVERTISED_URL" == http*127.0.0.1* ]]; then
  fail "card advertises a non-routable URL: ${ADVERTISED_URL}"
  fail "  → the Next.js proxy must rewrite this from X-Forwarded-Host"
else
  ok "card advertises a public URL: ${ADVERTISED_URL}"
fi

# --- capabilities.extensions[] AgentExtension shape -----------------------

EXT_COUNT=$(jq -r '.capabilities.extensions | length // 0' "$BODY_FILE" 2>/dev/null || echo 0)
if [[ "$EXT_COUNT" -gt 0 ]]; then
  # A2A v0.2 schema: capabilities.extensions[] must be AgentExtension
  # objects with a `uri` field — Discovery Engine / Gemini Enterprise
  # rejects bare strings with "unexpected instance type" (G43 — real
  # failure on the gde-ap-agent fork 2026-06-07).
  ALL_OBJECTS=$(jq -r '.capabilities.extensions | map(type == "object" and has("uri")) | all' "$BODY_FILE")
  if [[ "$ALL_OBJECTS" == "true" ]]; then
    URIS=$(jq -r '.capabilities.extensions | map(.uri) | join(", ")' "$BODY_FILE")
    ok "capabilities.extensions advertises ${EXT_COUNT} AgentExtension descriptor(s)"
    info "uris: ${URIS}"
  else
    fail "capabilities.extensions[] entries are not AgentExtension objects with .uri"
    fail "  → Gemini Enterprise registration will reject with 'unexpected instance type'"
  fi
  if jq -e '.capabilities.extensions | map(.uri // "") | any(endswith("a2a/v0.2") or contains("a2a-v0.2") or contains("A2A/v0.2"))' "$BODY_FILE" >/dev/null 2>&1; then
    ok "advertises an A2A v0.2 extension descriptor"
  else
    warn "capabilities.extensions does not include an A2A v0.2 entry"
  fi
else
  fail "capabilities.extensions is empty or missing"
fi

# --- skills[] non-empty ---------------------------------------------------

SKILL_COUNT=$(jq -r '.skills | length' "$BODY_FILE")
if [[ "$SKILL_COUNT" -gt 0 ]]; then
  ok "card advertises ${SKILL_COUNT} skill(s)"
  jq -r '.skills[].name' "$BODY_FILE" | sed 's/^/         - /'
else
  warn "card advertises zero skills (intentional in a freshly-seeded fork; otherwise check public-marketplace gating)"
fi

# --- G46: defaultInputModes check --------------------------------------
# Without file MIME types on defaultInputModes, Gemini Enterprise strips
# uploaded files at the peer side BEFORE reaching us — the
# FileExtractionInterceptor sees an empty FilePart list and the agent
# silently runs against text-only. Real failure 2026-06-07T22:06 UTC
# on gde-ap-agent. ENABLE_A2A_FILE_INPUT=true on the deployed service
# is necessary but not sufficient — the card must advertise the MIMEs.
INPUT_MODES_COUNT=$(jq -r '.defaultInputModes | length' "$BODY_FILE")
if [[ "$INPUT_MODES_COUNT" -gt 1 ]]; then
  ok "card advertises ${INPUT_MODES_COUNT} defaultInputModes (file inbound supported)"
elif [[ "$INPUT_MODES_COUNT" -eq 1 ]]; then
  warn "card advertises only text on defaultInputModes (file inbound NOT advertised)"
  warn "  → if intentional, ignore. If you expect to receive files, set"
  warn "    A2A_AGENT_INPUT_MIME_TYPES or leave unset (default = 9 file MIMEs)"
else
  fail "card has no defaultInputModes"
fi

# --- POST invocation probe (G45) -----------------------------------------
# Discovery-compliance (everything above) gets a fork to "registerable with
# Gemini Enterprise". But peers actually invoking skills need the /a2a
# JSON-RPC bridge live too — this probe POSTs a minimal message/send to
# the URL the card advertises and asserts the JSON-RPC envelope shape comes
# back. Catches the "registered but every call 405s" failure mode.

# Card.url is what peers POST to; per G45 it points at the A2A invocation
# mount (e.g. https://your-fork.run.app/a2a), not the bare base.
INVOCATION_URL=$(jq -r '.url' "$BODY_FILE")
RPC_ID="verify-a2a-$$"
RPC_BODY=$(jq -n --arg id "$RPC_ID" '{
  jsonrpc: "2.0",
  id: $id,
  method: "message/send",
  params: {
    message: {
      role: "user",
      parts: [{kind: "text", text: "ping"}],
      messageId: $id
    },
    configuration: {acceptedOutputModes: ["text"]}
  }
}')

INVOKE_BODY=$(mktemp)
trap 'rm -f "$HEADERS_FILE" "$BODY_FILE" "$INVOKE_BODY"' EXIT

INVOKE_STATUS=$(curl -s -o "$INVOKE_BODY" -w "%{http_code}" \
  -X POST -H "Content-Type: application/json" \
  --max-time 30 \
  -d "$RPC_BODY" "$INVOCATION_URL" || echo "000")

if [[ "$INVOKE_STATUS" == "200" ]]; then
  if jq -e '.jsonrpc == "2.0" and (has("result") or has("error"))' "$INVOKE_BODY" >/dev/null 2>&1; then
    if jq -e '.result' "$INVOKE_BODY" >/dev/null 2>&1; then
      ok "A2A message/send invocation works (HTTP 200, JSON-RPC result)"
    else
      RPC_ERR=$(jq -r '.error.message // "(no message)"' "$INVOKE_BODY")
      warn "A2A message/send returned JSON-RPC error: ${RPC_ERR}"
    fi
  else
    fail "A2A message/send HTTP 200 but body is not a valid JSON-RPC 2.0 envelope"
  fi
elif [[ "$INVOKE_STATUS" == "401" ]]; then
  warn "A2A message/send returned 401 — invocation gated by Bearer auth"
  warn "  (A2A_INVOCATION_REQUIRE_AUTH=true; peers need an ID token)"
elif [[ "$INVOKE_STATUS" == "404" ]] || [[ "$INVOKE_STATUS" == "405" ]] || [[ "$INVOKE_STATUS" == "501" ]]; then
  fail "A2A message/send returned HTTP ${INVOKE_STATUS} — invocation bridge NOT deployed"
  fail "  → set ENABLE_A2A_INVOCATION=true in cloudbuild.yaml and re-deploy"
else
  fail "A2A message/send returned HTTP ${INVOKE_STATUS} (unexpected)"
fi

echo
if [[ "$FAILED" -eq 0 ]]; then
  printf "\033[32mverify-a2a: all checks passed\033[0m\n"
  exit 0
else
  printf "\033[31mverify-a2a: one or more checks failed\033[0m\n"
  exit 1
fi
