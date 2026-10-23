#!/usr/bin/env bash
# Prove the resident session story reaches a real browser's wire (v6.40.0 M7).
#
#   AIPLATFORM_TEST_EMAIL=<in-domain identity> \
#     eval "$(.claude/skills/aiplatform-cli/scripts/mint-token.sh)"
#   scripts/verify-resident-story.sh --env dev
#
# WHY THIS EXISTS RATHER THAN MORE UNIT TESTS
#
# 46 unit tests shape this feature and NONE of them can tell you it renders.
# CLAUDE.md #7 is explicit: an A2UI-workspace change is not done until a real
# browser — or at minimum a real AG-UI stream showing the A2UI_SURFACE CUSTOM
# event — confirms it, and 7.2, 7.3 and 7.5 each shipped broken by deferring
# exactly that. This is the "at minimum" half; a human glance at the layout is
# still Phase 4's other half.
#
# WHAT IT REFUSES TO CALL A PASS
#
# A verifier that reports a non-event as success is worse than none —
# verify-resident-routing.sh learned this when a run with zero tool calls
# reported the whole fleet untouched and read as a routing bug. So the
# negatives below are the half that pays:
#
#   - TOOL CALLS: none FAILS. A prompt the model can answer from its own
#     knowledge verifies nothing about the tool.
#   - A tool_use with no non-empty tool_result FAILS. That is bug #7 from
#     HANDOVER §8.4 and it renders as a perfectly healthy timeline.
#   - A SECOND surfaceId FAILS. Accumulating a workbench tab per poll tick is
#     the failure that looks like success.
#   - ONE emission FAILS. The registered mapping alone produces exactly one, at
#     the end — which is the behaviour this milestone exists to replace. Two or
#     more sourceIds on one surfaceId is what proves it updated DURING the run.
set -uo pipefail

ENV_NAME="dev"
SKILL="${RESIDENT_SKILL_ID:-9ffad1e4-e256-47d1-b722-09645fe42d67}"
OUT="${TMPDIR:-/tmp}/resident-story-$$"
# The prompt names the ACT and pins a string only the tool can produce. It also
# runs long enough to be polled more than once: at poll_s=2 a sub-second run
# would finish inside a single tick and the progressive half would be untested
# while looking fine.
PROBE="STORY-PROBE-$$"
PROMPT="Use your resident assistant to run this exact shell command and show me its output verbatim: \`sleep 12 && ls -a /workspace && echo ${PROBE}\`. Do not answer from your own knowledge."
KEEP=0

usage() { sed -n '2,32p' "$0"; }
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
  echo "The mint script's default lives on a .test domain, which a domain-gated" >&2
  echo "skill cannot see — the run would then fail for the wrong reason and" >&2
  echo "prove nothing (CLAUDE.md #10)." >&2
  exit 2
}

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }

# The CLI has to be the one from THIS checkout. `aiplatform` on PATH is
# whatever `make cli-install` last installed, and on 2026-09-05 that was a
# build predating --surface-json: the run died on an unknown option and the
# absence-of-a-string check below still scored it a PASS.
CLI="${AIPLATFORM_BIN:-aiplatform}"
if ! "$CLI" skill chat --help 2>&1 | grep -q -- "--surface-json"; then
  echo "  FAIL  \`$CLI\` has no --surface-json, so this script cannot see what rendered." >&2
  echo "        Reinstall from this checkout:  make cli-install" >&2
  echo "        Or point at it:                AIPLATFORM_BIN=\"uv run --directory cli aiplatform\"" >&2
  exit 2
fi

echo "== one real turn on ${ENV_NAME}, probe ${PROBE} =="
TURN="$($CLI --env "$ENV_NAME" skill chat "$SKILL" -m "$PROMPT" --surface-json "$OUT.jsonl" 2>&1)"
TURN_RC=$?
echo "$TURN" | tail -30
echo

# ── the turn has to have used the tool at all ────────────────────────────────
#
# ASSERTED POSITIVELY. The first cut tested for the ABSENCE of "TOOL CALLS:
# none", which a usage error, a 401 and a crash all satisfy — and one did, on
# the first live run: the CLI rejected --surface-json and this reported "the
# turn called the resident tool". A verifier that reports a non-event as a pass
# is the exact thing this script's header promises not to be, so it is worth
# recording that it took one live run to catch it in my own.
if [[ $TURN_RC -ne 0 ]]; then
  bad "the CLI exited $TURN_RC — the turn did not run, so nothing below means anything"
  exit 1
fi
if grep -q "TOOL CALLS: none" <<<"$TURN"; then
  bad "the model answered WITHOUT calling ask_assistant — nothing was delegated, so nothing could be told"
  echo "        A prompt/skill-instruction finding, not a story one."
  exit 1
fi
if ! grep -qE "^TOOL CALLS \([0-9]+\)" <<<"$TURN"; then
  bad "no TOOL CALLS section in the output at all — the turn did not complete normally"
  exit 1
fi
ok "the turn called the resident tool ($(grep -oE '^TOOL CALLS \([0-9]+\)' <<<"$TURN"))"

if grep -qE "^ERRORS" <<<"$TURN"; then
  bad "the turn reported a RUN_ERROR"
  sed -n '/^ERRORS/,$p' <<<"$TURN" | head -6 | sed 's/^/        /'
fi

[[ -s "$OUT.jsonl" ]] || { bad "no surface file was written — did this CLI predate --surface-json?"; exit 1; }

# ── everything else is read out of the surface events themselves ─────────────
python3 - "$OUT.jsonl" "$PROBE" <<'PY'
import json, sys

path, probe = sys.argv[1], sys.argv[2]
records, end = [], None
for line in open(path, encoding="utf-8"):
    r = json.loads(line)
    (records.append(r) if r.get("type") != "__end__" else None)
    if r.get("type") == "__end__":
        end = r

fails = 0
def ok(m):  print(f"  PASS  {m}")
def bad(m):
    global fails; fails += 1; print(f"  FAIL  {m}")

story = [r for r in records if r.get("surfaceId") == "resident_session"]
others = {r.get("surfaceId") for r in records} - {"resident_session"}

if not story:
    bad("no A2UI_SURFACE for `resident_session` reached the wire at all")
    print("        Check, in order: RESIDENT_SESSION_STORY_ENABLED on the backend;")
    print("        the mapping is registered (adk.agent imports a2ui_resident_render);")
    print("        a LatencyTracker is bound to the request (backend/adk/CLAUDE.md).")
    sys.exit(1)
ok(f"{len(story)} surface emission(s) for `resident_session`")

# UPDATING, not accumulating.
sources = [r.get("sourceId") for r in story]
if len(set(sources)) != len(sources):
    bad(f"a sourceId repeated — the registry dedupes on it, so that update was DROPPED: {sources}")
else:
    ok("every emission carried a distinct sourceId")

if len(story) < 2:
    bad("only ONE emission: that is what the registered mapping alone produces, at the end. "
        "The progressive half did not run.")
else:
    ok("the surface was updated more than once, so it moved DURING the run")

# THE ASSERTION THAT ACTUALLY CATCHES THE 2026-09-06 BUG.
#
# Everything above is satisfied by updates that were QUEUED during the run and
# flushed in one burst when the tool returned — which is exactly what happened
# when the stream loop only drained on a 20s heartbeat. Sequence cannot see it,
# because nothing else emits while the agent is blocked in one tool call. Only
# elapsed time can.
times = [r.get("t") for r in story if isinstance(r.get("t"), (int, float))]
if len(times) < 2:
    bad("no arrival times in the surface file — this CLI predates them; `make cli-install`")
else:
    spread = max(times) - min(times)
    if spread >= 2.0:
        ok(f"emissions were spread over {spread:.1f}s — genuinely progressive, not one burst")
    else:
        bad(f"every emission landed within {spread:.1f}s of the others: they were QUEUED during "
            f"the run and flushed together. The surface only LOOKS progressive.")
        print("        Check AGUI_DRAIN_SECONDS on the backend — if the stream loop only wakes")
        print("        on the heartbeat, nothing drains until the tool returns.")

# Ordering: something rendered before the assistant finished speaking.
if end and end.get("lastTextSeq") and story[0]["seq"] < end["lastTextSeq"]:
    ok("the first surface arrived before the reply finished streaming")
else:
    bad("every surface arrived after the reply — nothing was visible while the user waited")

if others:
    bad(f"other surfaceIds appeared, so tabs are accumulating rather than updating: {sorted(others)}")
else:
    ok("exactly one surfaceId — no tab accumulation")

if not story[-1].get("artifact"):
    bad("no artifact metadata — SurfaceRegistry will not count this and no workbench tab appears")
else:
    ok(f"artifact metadata present: {story[-1]['artifact'].get('title')!r}")

# ── content: the story has to actually say what happened ─────────────────────
texts = [
    c.get("text", "")
    for r in story for m in (r.get("messages") or [])
    for c in ((m.get("updateComponents") or {}).get("components") or [])
    if c.get("component") == "Text"
]
blob = "\n".join(texts)

if probe in blob:
    ok(f"the story carries the probe's own output ({probe})")
else:
    bad(f"the probe string {probe} is nowhere in the story — the tool result did not reach it")

if "/workspace" in blob:
    ok("/workspace appears (files touched, or the command that listed it)")
else:
    bad("/workspace appears nowhere — the files derivation and the command echo both missed")

# The 2026-09-04 bug, one layer up: a tool that ran and will not say what it
# returned renders as a perfectly healthy timeline.
if "(no output)" in blob:
    bad("a tool_result rendered as `(no output)` — the result did not survive normalisation")
else:
    ok("no empty tool results")

final = story[-1]
comps = [c for m in (final.get("messages") or [])
         for c in ((m.get("updateComponents") or {}).get("components") or [])]
kinds = {c.get("component") for c in comps}
allowed = {"Text", "Column", "Row", "Card", "List", "Divider", "Tabs"}
if kinds - allowed:
    bad(f"components this codebase has never rendered before: {sorted(kinds - allowed)}")
else:
    ok(f"only proven components: {', '.join(sorted(kinds))}")

if not any(c.get("id") == "root" for c in comps):
    bad("no `root` component — v0.9 requires it and the surface will render blank")
else:
    ok("the tree has a root")

titles = [t.get("title") for c in comps if c.get("component") == "Tabs" for t in (c.get("tabs") or [])]
# "Answers", plural, since 2026-09-06: the tab indexes every turn's answer in
# the session rather than the single run's.
if titles == ["Story", "Files", "Answers"]:
    ok("Story · Files · Answers")
else:
    bad(f"unexpected sections: {titles}")

sys.exit(1 if fails else 0)
PY
PYRC=$?
[[ $PYRC -eq 0 ]] || FAIL=$((FAIL+1))

if [[ $KEEP -eq 1 ]]; then
  echo; echo "surface events kept at $OUT.jsonl"
else
  rm -f "$OUT.jsonl"
fi

echo
if [[ $FAIL -eq 0 ]]; then
  echo "=== the story reached the wire and says what happened ==="
  echo "    NOT proven: that it LOOKS right. Layout, contrast and readability need"
  echo "    aitana-frontend-verify and a human glance — Phase 4's other half."
else
  echo "=== $FAIL check group(s) failed ==="
fi
exit "$FAIL"
