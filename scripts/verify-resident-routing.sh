#!/usr/bin/env bash
# Prove a chat turn reaches THIS user's resident instance and no other.
#
# WHY LOG EVIDENCE AND NOT THE ANSWER
#
# The agent's reply cannot prove which box produced it — a shared instance
# answering for everybody sounds exactly like a per-user one, which is the whole
# failure mode `RESIDENT_AGENT_PER_USER` exists to prevent (design D11). So the
# assertion is made on the FLEET: run one turn, then ask every instance whether
# it was called. Exactly one must say yes, and it must be the one the user's
# `resident_agents` record names.
#
# /health and /livez are excluded because the coordinator's idle sweep probes
# every instance every 15 minutes as itself. Counting those would show the whole
# fleet "touched" on every run.
#
#   eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"   # see below
#   scripts/verify-resident-routing.sh --email agent-verify@example.com \
#       --expect resident-7e71c9b736f2
#
# The identity matters: mint with AIPLATFORM_TEST_EMAIL set to the account whose
# routing is being proved, or the run proves somebody else's (CLAUDE.md #10).
set -euo pipefail

SKILL="${RESIDENT_SKILL_ID:-9ffad1e4-e256-47d1-b722-09645fe42d67}"
ENV_NAME="dev"
ESTATE_PROJECT="${RESIDENT_ESTATE_PROJECT:-ailang-multivac-dev}"
EMAIL=""; EXPECT=""; # The default prompt names the ACT, not the question. "What is your hostname?"
# is answerable from the model's own knowledge — and was, on the first run:
# zero tool calls and a confident non-answer about being a large language
# model. A verification prompt that the model can satisfy without the tool
# verifies nothing about the tool (cf. url_processing, which failed for four
# months while the model answered anyway).
PROMPT="Use your resident assistant to run the shell command \`hostname && ls -a ~\` and show me its exact output. Do not answer from your own knowledge."
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) EMAIL="$2"; shift 2;;
    --expect) EXPECT="$2"; shift 2;;
    --prompt) PROMPT="$2"; shift 2;;
    --env) ENV_NAME="$2"; shift 2;;
    --skill) SKILL="$2"; shift 2;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
[[ -n "$EMAIL"  ]] || { echo "--email is required (whose routing is being proved)" >&2; exit 2; }
[[ -n "$EXPECT" ]] || { echo "--expect is required (the instance that must answer)" >&2; exit 2; }
[[ -n "${AIPLATFORM_ID_TOKEN:-}" ]] || {
  echo "AIPLATFORM_ID_TOKEN is unset. Mint one AS THE IDENTITY UNDER TEST:" >&2
  echo "  AIPLATFORM_TEST_EMAIL=${EMAIL} eval \"\$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)\"" >&2
  exit 2
}

# A margin either side: the turn's requests can land a second before the CLI
# returns, and log ingestion is not instant.
START="$(date -u -v-1M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '1 minute ago' '+%Y-%m-%dT%H:%M:%SZ')"
echo "== one real turn as ${EMAIL} =="
TURN="$(aiplatform --env "$ENV_NAME" skill chat "$SKILL" -m "$PROMPT" 2>&1)"
echo "$TURN" | tail -40
echo
# A turn that never called the tool cannot have reached any instance, and the
# fleet check below would then report "untouched" everywhere and look like a
# routing failure. Name the real cause instead.
if echo "$TURN" | grep -q "TOOL CALLS: none"; then
  echo "  FAIL  the model answered WITHOUT calling ask_assistant — nothing was routed."
  echo "        This is a prompt/skill-instruction finding, not a routing one."
  exit 1
fi
echo "== which instances were called since ${START} =="

# `alpha` first because that is where the subcommand lives today (Cloud Run
# instances are in Preview); the GA spelling is tried after, so this keeps
# working rather than starts failing when it graduates.
# A read loop, not `mapfile`: macOS ships bash 3.2, where mapfile does not
# exist and the failure is `command not found` AFTER the turn has already run —
# so the expensive half succeeds and the assertion silently never happens.
FLEET=()
while IFS= read -r _line; do
  [[ -n "$_line" ]] && FLEET+=("$_line")
done < <(gcloud alpha run instances list --project "$ESTATE_PROJECT" --region europe-west4 \
  --format="value(metadata.name)" 2>/dev/null \
  || gcloud run instances list --project "$ESTATE_PROJECT" --region europe-west4 \
       --format="value(metadata.name)" 2>/dev/null)
if [[ "${#FLEET[@]}" -eq 0 ]]; then
  echo "could not list the fleet; falling back to the expected instance plus the shared one" >&2
  FLEET=("$EXPECT" "resident-pi-ailang")
fi

FAIL=0
for INST in "${FLEET[@]}"; do
  [[ -n "$INST" ]] || continue
  N=$(gcloud logging read \
        "resource.type=\"cloud_run_instance\" AND resource.labels.instance_name=\"${INST}\" AND httpRequest.requestUrl!~\"/(health|livez)$\" AND timestamp>=\"${START}\"" \
        --project "$ESTATE_PROJECT" --format="value(timestamp)" --limit 50 2>/dev/null | grep -c . || true)
  if [[ "$INST" == "$EXPECT" ]]; then
    if [[ "$N" -gt 0 ]]; then echo "  PASS  ${INST}: ${N} request(s) — the user's own instance answered"
    else echo "  FAIL  ${INST}: NOTHING reached the instance this user's record names"; FAIL=$((FAIL+1)); fi
  else
    if [[ "$N" -eq 0 ]]; then echo "  PASS  ${INST}: untouched"
    else echo "  FAIL  ${INST}: ${N} request(s) — the turn reached SOMEBODY ELSE'S instance"; FAIL=$((FAIL+1)); fi
  fi
done
echo
[[ "$FAIL" -eq 0 ]] && echo "routing holds: only ${EXPECT} was called" || echo "${FAIL} assertion(s) failed"
exit "$FAIL"
