#!/usr/bin/env bash
# model-fallback-e2e.sh — acceptance harness for cross-provider model fallback
# (v6.13.0). Streams a complex prompt against a DEPLOYED env, to a skill whose
# thinking tier is Claude (one-ppa-expert, thinking=claude-opus). When the
# Anthropic provider is down (the org usage cap), the turn must FALL BACK to
# Gemini and still ANSWER — no `tool_call_id` cross-provider crash.
#
# Usage:  scripts/model-fallback-e2e.sh [dev|test]     (default dev)
#
# Opportunistic: if Anthropic is healthy the skill just answers on Claude (still
# a pass — it answered). The HARD assertion is: a tool-using turn NEVER dies on
# `tool_call_id` / a cross-provider RUN_ERROR. When a MODEL_FALLBACK to a
# different provider IS observed, that's the real cross-provider path proven.

source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -uo pipefail

ENV="${1:-dev}"
case "$ENV" in
  dev|test|prod) PROJECT="$(project_for_env "$ENV")"; HOST="$(host_for_env "$ENV")" ;;
  *) echo "usage: $0 [dev|test]" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MINT="${REPO_ROOT}/.claude/skills/aiplatform-cli/scripts/mint-token.sh"
ASSERT_PY="$(mktemp -t xprovider_assert.XXXXXX.py)"
trap 'rm -f "$ASSERT_PY"' EXIT

echo "== model-fallback-e2e :: env=${ENV} host=${HOST} ==" >&2
export GCP_PROJECT="${PROJECT}" GOOGLE_CLOUD_PROJECT="${PROJECT}"
eval "$(bash "${MINT}" 2>/dev/null)"
[ -z "${AIPLATFORM_ID_TOKEN:-}" ] && { echo "FATAL: token mint failed for ${PROJECT}" >&2; exit 1; }

SKILL_SLUG="${SKILL_SLUG:-one-ppa-expert}"   # thinking tier = claude-opus (Anthropic)
SKILL_ID="$(curl -s "${HOST}/api/proxy/api/skills/by-slug/aitana-platform/${SKILL_SLUG}" \
  -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('skillId') or d.get('id') or '')" 2>/dev/null || true)"
[ -z "${SKILL_ID}" ] && { echo "FATAL: could not resolve ${SKILL_SLUG} skillId on ${ENV}" >&2; exit 1; }
echo "${SKILL_SLUG} skillId = ${SKILL_ID}" >&2

cat > "$ASSERT_PY" <<'PY'
import sys, json
label = sys.argv[1]
answer = 0
run_error = None
fallbacks = []          # (from_model, to_model)
resolved = None
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
        if e.get("name") == "MODEL_RESOLVED":
            resolved = v.get("model")
        elif e.get("name") == "MODEL_FALLBACK" and v.get("to_model"):
            fallbacks.append((v.get("from_model"), v.get("to_model")))
    elif t == "TEXT_MESSAGE_CONTENT":
        answer += len(e.get("delta") or "")
    elif t == "RUN_ERROR":
        run_error = e.get("message") or ""

def _provider(m): return (m or "").split("/")[0] if "/" in (m or "") else ("gemini" if "gemini" in (m or "") else "?")
crossed = any(_provider(f) != _provider(t) for f, t in fallbacks if f and t) or \
          any("gemini" in (t or "") for _, t in fallbacks)

hard, soft = [], []
if run_error:
    low = run_error.lower()
    if "tool_call_id" in low:
        hard.append(f"CROSS-PROVIDER tool_call_id crash — the v6.13.0 bug is back: {run_error[:120]}")
    elif any(p in low for p in ("usage limit", "regain access", "quota", "billing")):
        # A cap with NO working fallback = the safety net failed to catch it.
        hard.append(f"capped with no working fallback: {run_error[:120]}")
    else:
        hard.append(f"RUN_ERROR: {run_error[:140]}")

status = "PASS" if not hard and answer > 60 else ("FAIL" if hard else "RETRY")
print(f"[{status}] {label}")
print(f"   resolved={resolved} fallbacks={fallbacks} crossed_provider={crossed} answer_len={answer} "
      f"run_error={(run_error or '')[:60]}")
if answer > 60 and crossed:
    print("   ✓ answered via a CROSS-PROVIDER fallback — the real path proven")
elif answer > 60:
    print("   ✓ answered (Anthropic healthy this run — fallback not exercised)")
for f in hard:
    print(f"   - {f}")
if not hard and answer <= 60:
    soft.append("no substantive answer this run (model variance) — retry")
    print("   - " + soft[0])
sys.exit(0 if (not hard and answer > 60) else (1 if hard else 2))
PY

_stream() {  # $1 = message
  curl -s --max-time 120 -N -X POST "${HOST}/api/proxy/api/skill/${SKILL_ID}/stream" \
    -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" -H "Content-Type: application/json" \
    -d "{\"message\": $(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$1")}" 2>/dev/null \
    | grep '^data:' || true
}

# Complex, tool-plausible prompt → routes to the Claude thinking tier and may use
# tools — the exact shape that broke on a cross-provider fallback.
PROMPT="Reason step by step through the settlement-risk exposure of every party \
if COD slips six months on a pay-as-produced PPA with a price floor. Be rigorous."

rc=0
for attempt in 1 2 3; do
  _stream "$PROMPT" | python3 "$ASSERT_PY" "XPROVIDER (claude-thinking answers, no tool_call_id) try $attempt"
  code=$?
  [ "$code" -eq 0 ] && { rc=0; break; }
  [ "$code" -eq 1 ] && { rc=1; break; }   # deterministic bug — stop
  rc=2
done

if [ "$rc" -eq 0 ]; then
  echo "== model-fallback-e2e: PASS on ${ENV} =="
elif [ "$rc" -eq 1 ]; then
  echo "== model-fallback-e2e: DETERMINISTIC FAILURE on ${ENV} ==" >&2
else
  echo "== model-fallback-e2e: model variance on ${ENV} (no bug, but couldn't exercise) ==" >&2
fi
exit "$rc"
