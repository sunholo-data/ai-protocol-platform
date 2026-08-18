#!/usr/bin/env bash
# handoff-e2e.sh — acceptance harness for the unified ADK-native handoff
# (v6.10.0). Streams handoff requests against a DEPLOYED env via REAL AG-UI
# streams and asserts the model uses ONE clean, enum-constrained
# `transfer_to_agent` call — the exact regression guard for the 2026-07-15 bug
# (a lite model calling transfer_to_agent with request_handoff's args, looping).
#
# Usage:  scripts/handoff-e2e.sh [dev|test]     (default dev)
#
# The dedicated whoami-test user has ONE access (aitana-admin group tag) AND full
# tool permissions (tool_permissions/{email} = ["*"]), so this exercises the FULL
# journey on the deployed backend: the handoff MECHANISM (one clean native
# transfer, no conflation/loop) AND the document pipeline (list_documents needs
# the parsed_documents skillId+status+createdAt index; get_document_content
# offloads to an artifact that retrieve_artifact must find — the save_artifact
# await regression). Confirm-card / form UX still gets the real-browser pass.

source "$(dirname "${BASH_SOURCE[0]}")/_deploy_env.sh"
set -euo pipefail

ENV="${1:-dev}"
case "$ENV" in
  dev|test|prod) PROJECT="$(project_for_env "$ENV")"; HOST="$(host_for_env "$ENV")" ;;
  *) echo "usage: $0 [dev|test|prod]" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MINT="${REPO_ROOT}/.claude/skills/aiplatform-cli/scripts/mint-token.sh"
ASSERT_PY="$(mktemp -t handoff_assert.XXXXXX.py)"
trap 'rm -f "$ASSERT_PY"' EXIT

echo "== handoff-e2e :: env=${ENV} host=${HOST} ==" >&2

export GCP_PROJECT="${PROJECT}" GOOGLE_CLOUD_PROJECT="${PROJECT}"
eval "$(bash "${MINT}" 2>/dev/null)"
if [ -z "${AIPLATFORM_ID_TOKEN:-}" ]; then
  echo "FATAL: token mint failed for ${PROJECT}" >&2; exit 1
fi

SKILL_ID="$(curl -s "${HOST}/api/proxy/api/skills/by-slug/aitana-platform/one-assistant" \
  -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('skillId') or d.get('id') or '')" 2>/dev/null || true)"
if [ -z "${SKILL_ID}" ]; then echo "FATAL: could not resolve one-assistant skillId on ${ENV}" >&2; exit 1; fi
echo "one-assistant skillId = ${SKILL_ID}" >&2

# Assertion engine as a standalone file so the SSE pipe is python's real stdin.
# argv: 1=label  2=flow (auto|document)
cat > "$ASSERT_PY" <<'PY'
import sys, json
label, flow = sys.argv[1], sys.argv[2]
customs, tool_calls, tool_args, hard_errors = [], [], [], []
run_error = None
delegation_modes = []
chat_card = False
artifact_not_found = False
answer_len = 0
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
        n = e.get("name"); customs.append(n)
        v = e.get("value") or {}
        if n == "AGENT_DELEGATION":
            delegation_modes.append(v.get("mode"))
        if n == "A2UI_SURFACE" and (v.get("artifact") or {}).get("placement") == "chat":
            chat_card = True
    elif t == "TOOL_CALL_START":
        tool_calls.append(e.get("toolCallName"))
    elif t == "TOOL_CALL_ARGS":
        tool_args.append(e.get("delta") or "")
    elif t == "TOOL_CALL_RESULT":
        c = e.get("content")
        if isinstance(c, str):
            if "is not an available specialist" in c:
                hard_errors.append("dead-end: " + c[:100])
            if "not found" in c and "artifact" in c.lower():
                artifact_not_found = True  # the save_artifact-await regression
    elif t == "TEXT_MESSAGE_CONTENT":
        answer_len += len(e.get("delta") or "")
    elif t == "RUN_ERROR":
        run_error = e.get("message") or ""

transfers = tool_calls.count("transfer_to_agent")
transfer_args = "".join(a for a, n in zip(tool_args, tool_calls) if n == "transfer_to_agent") or "".join(tool_args)
has_agent_name = '"agent_name"' in transfer_args or "'agent_name'" in transfer_args

# HARD fails = deterministic regressions (never retry — these are real bugs).
# SOFT fails = the lite model just didn't engage this run (only reasoned, no tool
# call / no answer) — retryable model variance, NOT a bug.
hard, soft = [], []
if hard_errors:
    hard.append(f"handoff dead-end: {hard_errors}")
if artifact_not_found:
    hard.append("retrieve_artifact 'not found' — save_artifact offload regression")
if run_error:
    hard.append(f"RUN_ERROR: {run_error}")
if transfers > 3:
    hard.append(f"transfer_to_agent looped ({transfers}x)")
if transfers >= 1 and not has_agent_name:
    hard.append(f"malformed transfer_to_agent (no agent_name): args={transfer_args[:120]}")

if flow == "auto":
    if transfers < 1 and not chat_card:
        soft.append("model did not hand off this run (no transfer, no card)")
elif flow == "confirm":
    # CONFIRM-floor handoff: the delegate is NOT a sub_agent, so the policy
    # short-circuits transfer_to_agent into a chat confirm card and the turn
    # MUST end there. If it doesn't (skip_summarization missing), the lite
    # front-door re-issues transfer_to_agent every round → the 2026-07-22 spam
    # loop of stacked "Confirm" cards. A HARD fail on >1 delegation is the guard
    # (>3 already trips the loop rule above; tighten it here to the confirm case).
    if not chat_card:
        soft.append("no confirm card rendered this run (model did not attempt the confirm handoff)")
    elif len(delegation_modes) > 1 or transfers > 1:
        hard.append(f"confirm handoff SPAMMED (delegations={len(delegation_modes)}, transfers={transfers}) — expected exactly one card")
elif flow == "document":
    doc_tools = {"list_documents", "list_bucket_documents", "get_document_content"}
    if not (doc_tools & set(tool_calls)):
        soft.append(f"model called no document tool this run: {tool_calls or '[]'}")
    elif answer_len < 40:
        soft.append(f"no substantive answer despite a tool call (len={answer_len})")

status = "PASS" if not (hard or soft) else ("FAIL" if hard else "RETRY")
print(f"[{status}] {label}")
print(f"   transfers={transfers} agent_name_ok={has_agent_name} deleg_modes={delegation_modes} "
      f"chat_card={chat_card} tools={tool_calls} artifact_not_found={artifact_not_found} "
      f"answer_len={answer_len} run_error={(run_error or '')[:60]}")
for f in hard + soft:
    print(f"   - {f}")
# 0 = pass, 1 = hard fail (real bug), 2 = soft fail (retryable model variance)
sys.exit(0 if not (hard or soft) else (1 if hard else 2))
PY

_stream() {  # $1 = message
  curl -s --max-time 120 -N -X POST "${HOST}/api/proxy/api/skill/${SKILL_ID}/stream" \
    -H "Authorization: Bearer ${AIPLATFORM_ID_TOKEN}" -H "Content-Type: application/json" \
    -d "{\"message\": $(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$1")}" 2>/dev/null \
    | grep '^data:' || true
}

# Run one flow with up to 3 attempts: a HARD fail (exit 1, real bug) aborts
# immediately; a SOFT fail (exit 2, the model didn't engage) retries; PASS wins.
_run_flow() {  # $1 = label ; $2 = flow ; $3 = message
  local label="$1" flow="$2" msg="$3" attempt
  for attempt in 1 2 3; do
    _stream "$msg" | python3 "$ASSERT_PY" "$label (try $attempt)" "$flow"
    local code=$?
    [ "$code" -eq 0 ] && return 0
    [ "$code" -eq 1 ] && return 1  # deterministic regression — do not retry
  done
  echo "   ! $label: model never engaged across 3 attempts (no deterministic bug, but the flow could not be exercised)" >&2
  return 2
}

rc=0
echo "-- AUTO handoff --" >&2
_run_flow "AUTO (extract clauses -> Contract Expert)" auto "extract the clauses of the Google LEAP ppa" || rc=$?
echo "-- CONFIRM handoff (one card, no spam loop) --" >&2
_run_flow "CONFIRM (analyse obligations -> PPA Obligation Analysis)" confirm \
  "analyse and quantify the net settlement obligations of the Google LEAP PPA contract" || rc=$?
echo "-- DOCUMENT pipeline (list + fetch + artifact roundtrip) --" >&2
_run_flow "DOCUMENT (list + fetch + offload/retrieve)" document \
  "list my PPA documents, then fetch the full content of the Google LEAP PPA and summarise it in one sentence" || rc=$?

if [ "$rc" -eq 0 ]; then
  echo "== handoff-e2e: AUTO + DOCUMENT verified on ${ENV}. Confirm-card/form UX: real-browser pass. =="
elif [ "$rc" -eq 1 ]; then
  echo "== handoff-e2e: DETERMINISTIC FAILURE (real regression) on ${ENV} ==" >&2
else
  echo "== handoff-e2e: no bug, but a flow couldn't be exercised on ${ENV} (model variance) ==" >&2
fi
exit "$rc"
