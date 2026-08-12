#!/usr/bin/env bash
# elicitation-e2e.sh — acceptance harness for the AGENT-authored elicitation
# path (v6.12.0). Streams a prompt against a DEPLOYED env via a REAL AG-UI
# stream and asserts the model authors its OWN A2UI chat form via
# `request_confirmation` — the AI constructs the fields, the engine renders them
# as a placement:"chat" form on the wire.
#
# This is the gap `handoff-e2e.sh` left: handoff-e2e proves transfer_to_agent;
# the obligation tests prove the TOOL-authored form; this proves the AGENT can
# raise a form from its own judgement (the "any skill can craft a form" claim).
#
# Usage:  scripts/elicitation-e2e.sh [dev|test]     (default dev)
#
# Streams against a SPECIALIST (one-ppa-expert), NOT the front door — the fast
# front door opts out (`enableConfirmation:false`) for TTFT, so it has no
# request_confirmation tool. The whoami-test user has ONE access + full tool
# perms, so this exercises the real deployed backend.
#
# Scope: asserts the AI authors + the form EMITS on the wire (placement:"chat").
# The submit -> surface read-back -> continue loop is the real-browser pass
# (same split as handoff-e2e's confirm-card UX).
set -euo pipefail

ENV="${1:-dev}"
case "$ENV" in
  dev)  PROJECT="your-project-id";  HOST="https://your-service-url.example" ;;
  test) PROJECT="your-project-id-test"; HOST="https://your-service-url.example" ;;
  *) echo "usage: $0 [dev|test]" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MINT="${REPO_ROOT}/.claude/skills/aiplatform-cli/scripts/mint-token.sh"
ASSERT_PY="$(mktemp -t elicit_assert.XXXXXX.py)"
trap 'rm -f "$ASSERT_PY"' EXIT

echo "== elicitation-e2e :: env=${ENV} host=${HOST} ==" >&2

export GCP_PROJECT="${PROJECT}" GOOGLE_CLOUD_PROJECT="${PROJECT}"
eval "$(bash "${MINT}" 2>/dev/null)"
if [ -z "${AIPLATFORM_ID_TOKEN:-}" ]; then
  echo "FATAL: token mint failed for ${PROJECT}" >&2; exit 1
fi

# Stream to a skill that carries request_confirmation (the front door opts out).
# Default to a pure-GEMINI skill so the test is independent of Anthropic billing
# (a Claude-tier skill RUN_ERRORs when the org hits its Anthropic usage cap —
# env, not a render bug). Override with SKILL_SLUG=one-ppa-expert etc.
SKILL_SLUG="${SKILL_SLUG:-general-assistant}"
SKILL_ID="$(curl -s "${HOST}/api/proxy/api/skills/by-slug/aitana-platform/${SKILL_SLUG}" \
  -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('skillId') or d.get('id') or '')" 2>/dev/null || true)"
if [ -z "${SKILL_ID}" ]; then echo "FATAL: could not resolve one-ppa-expert skillId on ${ENV}" >&2; exit 1; fi
echo "one-ppa-expert skillId = ${SKILL_ID}" >&2

# Assertion engine as a standalone file so the SSE pipe is python's real stdin.
cat > "$ASSERT_PY" <<'PY'
import sys, json
label = sys.argv[1]
tool_calls, tool_args_by_call = [], []
run_error = None
chat_form = False          # a placement:"chat" A2UI artifact reached the wire
elicit_fields = []         # field names the AI authored
answer_len = 0

def _scan_envelope(obj):
    """Pull placement + field names out of a request_confirmation result /
    A2UI artifact, however it's shaped on the wire."""
    global chat_form, elicit_fields
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
    except Exception:
        return
    if not isinstance(obj, dict):
        return
    if obj.get("placement") == "chat":
        chat_form = True
    env = obj.get("elicitation") if isinstance(obj.get("elicitation"), dict) else None
    for src in (obj, env):
        if isinstance(src, dict):
            for f in (src.get("fields") or []):
                if isinstance(f, dict) and f.get("name"):
                    elicit_fields.append(f["name"])

for line in sys.stdin:
    line = line.strip()
    if not line.startswith("data:"):
        continue
    try:
        e = json.loads(line[len("data:"):].strip())
    except Exception:
        continue
    t = e.get("type")
    if t == "CUSTOM":
        v = e.get("value") or {}
        if e.get("name") == "A2UI_SURFACE":
            art = v.get("artifact") or {}
            if art.get("placement") == "chat":
                chat_form = True
            _scan_envelope(art)
    elif t == "TOOL_CALL_START":
        tool_calls.append(e.get("toolCallName"))
    elif t == "TOOL_CALL_ARGS":
        tool_args_by_call.append((tool_calls[-1] if tool_calls else "", e.get("delta") or ""))
    elif t == "TOOL_CALL_RESULT":
        _scan_envelope(e.get("content"))
    elif t == "TEXT_MESSAGE_CONTENT":
        answer_len += len(e.get("delta") or "")
    elif t == "RUN_ERROR":
        run_error = e.get("message") or ""

rc_calls = tool_calls.count("request_confirmation")
# fields the AI authored in the request_confirmation call args
rc_args = "".join(a for n, a in tool_args_by_call if n == "request_confirmation")
try:
    parsed = json.loads(rc_args) if rc_args.strip().startswith("{") else {}
    for f in (parsed.get("fields") or []):
        if isinstance(f, dict) and f.get("name"):
            elicit_fields.append(f["name"])
except Exception:
    pass
elicit_fields = sorted(set(elicit_fields))

hard, soft = [], []
if run_error:
    # An Anthropic org usage/billing cap ("regain access on ...") is an ENV issue,
    # not a render regression — a Claude-tier skill just can't run right now. Treat
    # it as retryable/env (soft), never a code failure.
    _env = any(p in run_error.lower() for p in ("usage limit", "regain access", "usage limits", "billing", "quota"))
    (soft if _env else hard).append(f"RUN_ERROR: {run_error}")
# The AI authored a form, it reached the wire as a chat form, with real fields:
if rc_calls >= 1 and not chat_form:
    hard.append("request_confirmation called but NO placement:'chat' form on the wire (render path broke)")
if rc_calls < 1:
    soft.append(f"model did not author a form this run (tools={tool_calls or '[]'})")
elif not elicit_fields:
    soft.append("form authored but no fields parsed (may be a bare confirm — re-run for a fields form)")

status = "PASS" if not (hard or soft) else ("FAIL" if hard else "RETRY")
print(f"[{status}] {label}")
print(f"   request_confirmation={rc_calls} chat_form={chat_form} fields={elicit_fields} "
      f"tools={tool_calls} answer_len={answer_len} run_error={(run_error or '')[:60]}")
for f in hard + soft:
    print(f"   - {f}")
sys.exit(0 if not (hard or soft) else (1 if hard else 2))
PY

_stream() {  # $1 = message
  curl -s --max-time 120 -N -X POST "${HOST}/api/proxy/api/skill/${SKILL_ID}/stream" \
    -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" -H "Content-Type: application/json" \
    -d "{\"message\": $(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$1")}" 2>/dev/null \
    | grep '^data:' || true
}

_run_flow() {  # $1 = label ; $2 = message
  local label="$1" msg="$2" attempt
  for attempt in 1 2 3; do
    _stream "$msg" | python3 "$ASSERT_PY" "$label (try $attempt)"
    local code=$?
    [ "$code" -eq 0 ] && return 0
    [ "$code" -eq 1 ] && return 1  # deterministic regression — do not retry
  done
  echo "   ! $label: model never authored a form across 3 attempts (no deterministic bug)" >&2
  return 2
}

# A request that is NOT a domain-tool op, so the ONLY way to satisfy it is the
# agent authoring its own ad-hoc form via request_confirmation.
PROMPT="Before you help me, I want to set my preferences. Please show me a form \
to fill in — with these fields: my name (text), preferred market (a dropdown of \
Spain, Denmark, Argentina), and whether to include tax (yes/no). Present the \
form and wait for me to submit; do not assume any answers."

rc=0
echo "-- AGENT-authored form (request_confirmation) --" >&2
_run_flow "ELICIT (agent authors a 3-field chat form)" "$PROMPT" || rc=$?

if [ "$rc" -eq 0 ]; then
  echo "== elicitation-e2e: agent-authored form verified on ${ENV}. Submit->read-back: real-browser pass. =="
elif [ "$rc" -eq 1 ]; then
  echo "== elicitation-e2e: DETERMINISTIC FAILURE (render path regression) on ${ENV} ==" >&2
else
  echo "== elicitation-e2e: no bug, but the model didn't author a form on ${ENV} (model variance) ==" >&2
fi
exit "$rc"
