#!/usr/bin/env bash
# Prove the fan-out board streams — that it is PROGRESSIVE, not a burst at the
# end (v6.31.0 FANOUT M2).
#
#   AIPLATFORM_TEST_EMAIL=<in-domain identity> \
#     eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"
#   scripts/verify-fanout-board.sh --env dev --skill <fanout-skill-id>
#
# WHY THIS EXISTS RATHER THAN MORE UNIT TESTS
#
# 19 unit tests shape this board and none of them can tell you it streams.
# A board that only populates when the slowest branch lands renders a perfect
# screenshot and is precisely the eleven-second blank this milestone exists to
# remove — the CONTENT is identical either way, so only wall-clock separates
# them. That is the same lesson v6.40.0 M7 learned the hard way (84af6617):
# arrival SEQUENCE is identical for a trickle and a burst, because nothing else
# emits while the agent is blocked inside its tool calls.
#
# WHAT IT REFUSES TO CALL A PASS
#
#   - TOOL CALLS: none FAILS. The model answering from its own knowledge
#     verifies nothing about fan-out.
#   - FEWER THAN 2 branch calls FAILS. One branch is not a fan-out, and it is
#     what a model does when it ignores the "all in ONE response" instruction.
#   - A SECOND surfaceId FAILS. A workbench tab per branch is the failure that
#     looks like success.
#   - ONE emission FAILS. The board must be drawn as branches start AND updated
#     as they finish.
#   - EVERY EMISSION WITHIN 2s FAILS, naming AGUI_DRAIN_SECONDS. This is the
#     assertion that carries the whole milestone.
set -uo pipefail

ENV_NAME="dev"
SKILL="${FANOUT_SKILL_ID:-demo-research-fanout}"
OUT="${TMPDIR:-/tmp}/fanout-board-$$"
# Three plainly independent topics, so a model that batches has an obvious
# reason to, and one that does not is visibly at fault rather than ambiguous.
PROMPT="I need a briefing covering three separate topics: (1) the 2026 outlook for Iberian solar PPAs, (2) the state of battery storage (BESS) in Spain, and (3) onshore wind permitting delays in Spain. Research each one and give me one merged brief."
KEEP=0

usage() { sed -n '2,31p' "$0"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_NAME="$2"; shift 2;;
    --skill) SKILL="$2"; shift 2;;
    --prompt) PROMPT="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

[[ -n "${AIPLATFORM_ID_TOKEN:-}" ]] || {
  echo "AIPLATFORM_ID_TOKEN is unset. Mint one as an IN-DOMAIN identity:" >&2
  echo "  AIPLATFORM_TEST_EMAIL=<you>@<your-domain> eval \"\$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)\"" >&2
  echo >&2
  echo "The mint script's default lives on a .test domain, which a tag-gated" >&2
  echo "skill cannot see — the run would fail for the wrong reason (CLAUDE.md #10)." >&2
  exit 2
}

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }

# AIPLATFORM_BIN may be a MULTI-WORD command (the documented escape hatch is
# `uv run --directory cli aiplatform`), so it has to be split into an array —
# "$CLI" as one word looks for a binary literally named "uv run --directory …"
# and fails, which this script would then report as "the CLI is too old".
read -r -a CLI <<<"${AIPLATFORM_BIN:-aiplatform}"
if ! "${CLI[@]}" skill chat --help 2>&1 | grep -q -- "--surface-json"; then
  echo "  FAIL  \`$CLI\` has no --surface-json, so this script cannot see what rendered." >&2
  echo "        Reinstall from this checkout:  make cli-install" >&2
  echo "        Or point at it:                AIPLATFORM_BIN=\"uv run --directory cli aiplatform\"" >&2
  exit 2
fi

echo "== one real fan-out on ${ENV_NAME} =="
START=$(date +%s)
TURN="$("${CLI[@]}" --env "$ENV_NAME" skill chat "$SKILL" -m "$PROMPT" --surface-json "$OUT.jsonl" 2>&1)"
TURN_RC=$?
ELAPSED=$(( $(date +%s) - START ))
echo "$TURN" | tail -25
echo

if [[ $TURN_RC -ne 0 ]]; then
  bad "the CLI exited $TURN_RC — the turn did not run, so nothing below means anything"
  exit 1
fi
if grep -q "TOOL CALLS: none" <<<"$TURN"; then
  bad "the model answered WITHOUT calling a branch — nothing was fanned out"
  echo "        A prompt/skill-instruction finding, not a board one."
  exit 1
fi
if grep -qE "^ERRORS" <<<"$TURN"; then
  bad "the turn reported a RUN_ERROR"
  sed -n '/^ERRORS/,$p' <<<"$TURN" | head -6 | sed 's/^/        /'
fi

BRANCHES=$(grep -cE "^  demo_research_worker|^  research_worker" <<<"$TURN" || true)
if [[ "$BRANCHES" -lt 2 ]]; then
  bad "only ${BRANCHES} branch call(s) — that is not a fan-out"
  echo "        The model ignored 'call once per objective in ONE response'."
  echo "        Check the skill prompt before suspecting the board."
  exit 1
fi
ok "${BRANCHES} branches were called"

[[ -s "$OUT.jsonl" ]] || { bad "no surface file was written — did this CLI predate --surface-json?"; exit 1; }

python3 - "$OUT.jsonl" "$ELAPSED" <<'PY'
import json, sys

path, elapsed = sys.argv[1], float(sys.argv[2])
recs, end = [], None
for line in open(path):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    if r.get("type") == "__end__":
        end = r          # the trailer: when the stream itself finished
    else:
        recs.append(r)
board = [r for r in recs if str(r.get("surfaceId", "")).startswith("fanout:")]

P = F = 0
def ok(m):
    global P; P += 1; print(f"  PASS  {m}")
def bad(m, hint=""):
    global F; F += 1; print(f"  FAIL  {m}")
    if hint:
        print(f"        {hint}")

if not board:
    bad("no fanout: surface reached the wire at all",
        "The board never emitted. Check the emitter is composed into the PARENT's "
        "tool callbacks and that the SSE endpoint binds a LatencyTracker "
        "(backend/adk/CLAUDE.md traps 1 and 2).")
    sys.exit(1)

ids = {r["surfaceId"] for r in board}
if len(ids) == 1:
    ok(f"one surface for the whole fan-out ({ids.pop()})")
else:
    bad(f"{len(ids)} surfaces: {sorted(ids)}",
        "A workbench tab per branch buries the Workspace index. The surfaceId "
        "must be keyed by invocation, not by branch.")

if len(board) >= 2:
    ok(f"{len(board)} board updates (drawn as branches start, updated as they finish)")
else:
    bad("only one board update — that is an end-of-turn render, not a live board")

srcs = [r.get("sourceId") for r in board]
if len(srcs) == len(set(srcs)):
    ok("every update carried a distinct sourceId")
else:
    bad("duplicate sourceIds", "The frontend dedupes on sourceId; repeats are dropped silently.")

first = board[0]
if (first.get("artifact") or {}).get("kind") == "fanout":
    ok(f"artifact metadata rode the FIRST update at t={first.get('t')}s — the tab exists while it runs")
else:
    bad("no artifact metadata on the first update",
        "Without it there is no workbench tab and no Workspace index row until the end.")

times = [r["t"] for r in board if isinstance(r.get("t"), (int, float))]
spread = max(times) - min(times) if len(times) > 1 else 0.0
if spread >= 2.0:
    ok(f"emissions spread over {spread:.1f}s — genuinely progressive, not one burst")
else:
    bad(f"every emission landed within {spread:.1f}s of the others: QUEUED, then flushed together",
        "Check AGUI_DRAIN_SECONDS on the backend — if the stream loop only wakes on "
        "the heartbeat, mid-fan-out updates sit on the tracker until the tool returns "
        "and the board's whole claim is false.")

if times and end and isinstance(end.get("t"), (int, float)):
    if min(times) < end["t"] * 0.5:
        ok(f"the board appeared at t={min(times):.1f}s, well inside a {end['t']:.1f}s turn")
    else:
        bad(f"the board's first update was at t={min(times):.1f}s of a {end['t']:.1f}s turn — too late to be progress")

print(f"\n  {P} passed, {F} failed  (turn took ~{elapsed:.0f}s)")
sys.exit(1 if F else 0)
PY
PY_RC=$?

[[ $KEEP -eq 1 ]] && echo "kept: $OUT.jsonl" || rm -f "$OUT.jsonl"
if [[ $FAIL -gt 0 || $PY_RC -ne 0 ]]; then
  echo; echo "FAILED"; exit 1
fi
echo; echo "PASSED"
