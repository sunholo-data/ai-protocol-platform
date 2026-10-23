"""The resident session story as an A2UI surface (v6.40.0 M7).

A resident run is a coding agent working for minutes, and until this the user
saw one line of prose when it was over. The commands it ran, what came back,
the files it touched — all captured, forwarded to AILANG's observatory, and
readable only by a Sunholo operator. The customer who asked could not see the
work done for them. That is a CLAUDE.md #8 gap (never silent) and a #7 gap (the
one tool whose result is genuinely a *document* rendered as a chat line).

WHERE THE PIECES LIVE

    pi --mode json ──NDJSON──▶ instance ──A2A artifact──▶ platform ──AG-UI──▶ here
                               (ailang docker/resident/lib/agent-events.mjs)

The instance writes an additive ``events`` artifact onto the A2A task the
platform ALREADY polls every two seconds. This module turns that into a
surface. No new route, no protocol change, ``streaming: false`` still true on
the agent card.

ONE BUILDER, TWO CALLERS — and this is the part that must not drift. The
registered mapping owns the FINAL surface; it cannot own the intermediate ones,
because ``after_tool_callback`` fires once, at the end. So ``ask_assistant``
also emits on each poll tick. Both go through :func:`build_story`, and
``test_a2ui_resident_render`` asserts the two produce the same tree for the same
terminal task. Two renderers of one run would eventually disagree about it.

WHY THE STORY TRAVELS IN ``tool_context.state`` AND NOT IN THE TOOL RESULT

``ask_assistant`` returns ``{answer, thread, task_id}`` — deliberately small,
because a tool result goes back to the MODEL. Putting 120 events there would
spend context on something the model must not narrate (the surface is the
narration) and invite it to do exactly that. So the tool records the story in
session state and the mapping reads it from ``tool_context``, which is the
pattern ``a2ui_browser_render`` already uses for the same reason.

TWO DEVIATIONS FROM THE DESIGN DOC, both deliberate:

* **No ``Icon``.** The design's element table uses it. Nothing in this backend
  emits one, so nothing proves the frontend renders one — and "A2UI won't
  render in the Workspace" is a named recurring bug whose stated remedy is to
  diff against a known-good path rather than re-derive it. Every component
  below is one this codebase already ships. An icon is a later, separate change
  with its own browser check.
* **No ``variant="caption"``.** The design asks for it; the v0.9 React SDK
  renders ``<caption>``, which is only valid inside a ``<table>`` and triggers a
  hydration warning (recorded in ``a2ui_browser_render``).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from adk.a2ui_result_render import BASIC_CATALOG_ID, WORKSPACE_SURFACE_ID, register

logger = logging.getLogger(__name__)

# Off by default, dev first. Flag off must render NOTHING — not an empty
# surface, which is a worse thing than no surface: a workbench tab that opens
# on nothing reads as a broken feature rather than an absent one.
STORY_ENABLED_ENV = "RESIDENT_SESSION_STORY_ENABLED"

# Where ask_assistant leaves the story for the registered mapping to find.
STORY_STATE_KEY = "resident_story"

RESIDENT_TOOLS = ("ask_assistant",)

# Its own surface, so the story gets its own workbench tab instead of competing
# for the shared workspace one (7.5: one surface, one artifact, one tab).
#
# STABLE, not per-run. A per-run id would be correct for "one surfaceId per
# run" read literally, and it would also grow a tab per question asked. The
# story is "what my assistant is doing", so the current run replaces the last —
# the same choice ppa_comparison and browser already make. Repeat emissions
# within a run therefore UPDATE this surface, which is the success criterion
# that matters: accumulating tabs is the failure that looks like success.
RESIDENT_SURFACE = "resident_session"

# A story longer than this is not read row by row; keep the most recent. The
# instance caps at 120 already, so this is the second line of defence and the
# one that matters if that cap is ever raised.
# Lower than the instance's 120 ON PURPOSE. The Basic catalog has no scroll
# container — a Column is exactly as tall as its children — so "how many rows"
# IS the scroll design here, and sixty cards of tool output made the tab a page
# to scroll past rather than a thing to read. Raise it only when the overflow
# has somewhere to go.
_MAX_ROWS = int(os.environ.get("RESIDENT_STORY_MAX_ROWS", "24"))
# Per-row display trim. The instance clips to 2000; a card in a timeline wants
# far less, and the full text is in the observatory for an operator who needs it.
_MAX_CELL = 400


def story_enabled() -> bool:
    """Read at CALL time, not import time.

    A module-level constant freezes the flag when the worker imports this, so a
    test that sets the env afterwards asserts against the default and passes
    while proving nothing. That trap has already been paid for twice in this
    arc.
    """
    return os.environ.get(STORY_ENABLED_ENV, "").lower() in ("1", "true", "yes", "on")


class _Tree:
    """Flattened A2UI v0.9 components with unique ids.

    Same shape as ``a2ui_ppa_render._Tree`` (the stated template) — copied
    rather than imported, so neither module's layout constrains the other. v0.9
    children are id strings, never inline objects, and the root must be
    ``id: "root"``.
    """

    def __init__(self) -> None:
        self.components: list[dict[str, Any]] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def text(self, text: str, *, variant: str | None = None) -> str:
        # Never variant="caption": the v0.9 React SDK renders <caption>, valid
        # only inside <table>, and it triggers a hydration warning.
        comp: dict[str, Any] = {"id": self._next_id("t"), "component": "Text", "text": text}
        if variant:
            comp["variant"] = variant
        self.components.append(comp)
        return comp["id"]

    def column(self, children: list[str]) -> str:
        cid = self._next_id("col")
        self.components.append({"id": cid, "component": "Column", "children": children})
        return cid

    def row(self, children: list[str], *, justify: str | None = None) -> str:
        cid = self._next_id("row")
        comp: dict[str, Any] = {"id": cid, "component": "Row", "children": children}
        if justify:
            comp["justify"] = justify
        self.components.append(comp)
        return cid

    def list_(self, children: list[str]) -> str:
        cid = self._next_id("list")
        self.components.append({"id": cid, "component": "List", "children": children})
        return cid

    def card(self, child: str) -> str:
        cid = self._next_id("card")
        self.components.append({"id": cid, "component": "Card", "child": child})
        return cid

    def divider(self) -> str:
        cid = self._next_id("div")
        self.components.append({"id": cid, "component": "Divider", "axis": "horizontal"})
        return cid

    def tabs(self, tabs: list[tuple[str, str]]) -> str:
        cid = self._next_id("tabs")
        self.components.append(
            {"id": cid, "component": "Tabs", "tabs": [{"title": title, "child": child} for title, child in tabs]}
        )
        return cid

    def root(self, children: list[str]) -> None:
        self.components.append({"id": "root", "component": "Column", "children": children})

    def messages(self) -> list[dict[str, Any]]:
        # Built against the placeholder surface; the registry retargets it to
        # `surface=` on the way out.
        return [
            {"version": "v0.9", "createSurface": {"surfaceId": WORKSPACE_SURFACE_ID, "catalogId": BASIC_CATALOG_ID}},
            {"version": "v0.9", "updateComponents": {"surfaceId": WORKSPACE_SURFACE_ID, "components": self.components}},
        ]


# ── reading the task ─────────────────────────────────────────────────────────


def events_of(task: Any) -> list[dict[str, Any]]:
    """The normalised events the instance attached, or [].

    Every hop is additive, so absence is the normal case and not an error: an
    older instance never writes this artifact, and the surface degrades to
    prose. A malformed one degrades the same way rather than raising into a
    poll loop.
    """
    if not isinstance(task, dict):
        return []
    for artifact in task.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("artifactId") != "events":
            continue
        for part in artifact.get("parts") or []:
            if not isinstance(part, dict):
                continue
            data = part.get("data")
            if not isinstance(data, dict):
                continue
            events = data.get("events")
            if isinstance(events, list):
                return [e for e in events if isinstance(e, dict)]
    return []


def events_truncated(task: Any) -> bool:
    if not isinstance(task, dict):
        return False
    for artifact in task.get("artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("artifactId") == "events":
            for part in artifact.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("data"), dict):
                    return bool(part["data"].get("truncated"))
    return False


def answer_text(task: Any) -> str:
    """The assistant's prose, from the `response` artifact.

    Deliberately NOT "the first text part of any artifact": that is what
    `a2a_client.answer_of` does, and it is only correct while `events` carries
    no text part. Naming the artifact makes it correct regardless.
    """
    if not isinstance(task, dict):
        return ""
    for artifact in task.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("artifactId") != "response":
            continue
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and part.get("kind") == "text" and part.get("text"):
                return str(part["text"])
    return ""


# pi ships read, write, edit and bash. The first three carry a path; bash does
# not, and guessing one out of a shell command is how a files list starts
# lying.
_PATH_KEYS = ("path", "file_path", "filePath", "filename", "file")
# Strongest verb wins when a path appears more than once — a file that was read
# and then edited is an edited file.
_VERB_RANK = {"read": 0, "written": 1, "edited": 2}
_TOOL_VERB = {
    "read": "read",
    "view": "read",
    "cat": "read",
    "write": "written",
    "edit": "edited",
    "str_replace": "edited",
}


def files_touched(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """``[(path, verb)]`` in first-seen order, derived from tool arguments.

    Derived rather than fetched. "What this session touched, and how" is both
    available today and more useful than a workspace tree, which for a coding
    workspace is mostly noise.

    The verbs are ``read``/``written``/``edited`` and NOT the design's
    ``create``: pi does not say whether a write created the file or replaced
    it, and a list that claims "created" for an overwrite is worse than one
    that says "written" for a new file.
    """
    seen: dict[str, str] = {}
    order: list[str] = []
    for e in events:
        if e.get("type") != "tool_use":
            continue
        verb = _TOOL_VERB.get(str(e.get("tool") or "").lower())
        if not verb:
            continue
        try:
            args = json.loads(e.get("input") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(args, dict):
            continue
        path = next((str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str) and args[k]), "")
        if not path:
            continue
        if path not in seen:
            order.append(path)
            seen[path] = verb
        elif _VERB_RANK[verb] > _VERB_RANK[seen[path]]:
            seen[path] = verb
    return [(p, seen[p]) for p in order]


def in_flight_tool(events: list[dict[str, Any]]) -> str:
    """The tool with no result yet, or "".

    Matched by POSITION rather than by name: two `bash` calls in a row would
    otherwise let the first one's result close the second, and the indicator
    would clear while the agent was still working — a silent state, which is
    the thing this feature exists to remove.
    """
    open_calls: list[str] = []
    for e in events:
        if e.get("type") == "tool_use":
            open_calls.append(str(e.get("tool") or "tool"))
        elif e.get("type") == "tool_result" and open_calls:
            open_calls.pop()
    return open_calls[-1] if open_calls else ""


def as_code(text: str) -> str:
    """Wrap literal text in a markdown code span so it renders VERBATIM.

    `Text` renders markdown now, so a shell command containing `**` or
    backticks was being INTERPRETED: `python3 -c 'print("**bold**")'` displayed
    with bold applied, and a tool output that really did contain asterisks
    showed none of them. The surface was quietly misreporting what ran and what
    came back — worse than ugly, because a transcript that edits its own
    evidence is not a transcript. Seen in a browser 2026-09-06.

    A command IS code, so a code span is also what it should look like. The
    fence grows past the longest backtick run inside (markdown's own way of
    escaping its delimiter), and a pad space stops a payload that begins or ends
    with a backtick from fusing with the fence.
    """
    t = str(text or "")
    if not t:
        return ""
    longest, run = 0, 0
    for ch in t:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    pad = " " if (t.startswith("`") or t.endswith("`")) else ""
    return f"{fence}{pad}{t}{pad}{fence}"


def _clip(v: Any, limit: int = _MAX_CELL, *, oneline: bool = True) -> str:
    """Trim for display.

    `oneline` collapses whitespace, which is right for a row that must stay one
    line — a shell command, a tool's arguments. It is WRONG for anything that
    renders as markdown: flattening newlines turns a fenced code block into a
    run of text with stray backticks in it, and a list into a sentence. Seen in
    a browser 2026-09-06, which is the only place it could have been seen.
    """
    t = str(v or "")
    t = " ".join(t.split()) if oneline else t.strip()
    return t if len(t) <= limit else f"{t[:limit]}…"


def _elapsed(story: dict[str, Any], events: list[dict[str, Any]]) -> str:
    secs = story.get("elapsed_s")
    if not isinstance(secs, (int, float)):
        stamps = [e["t"] for e in events if isinstance(e.get("t"), (int, float))]
        secs = (max(stamps) - min(stamps)) if len(stamps) > 1 else 0
    secs = int(secs or 0)
    return f"{secs}s" if secs < 60 else f"{secs // 60}m {secs % 60}s"


# ── the surface ──────────────────────────────────────────────────────────────

_RUNNING = ("submitted", "working", "unknown")

# Row kinds get a GLYPH, not a colour, because Basic has no colour to give.
#
# `Text` exposes only `variant` (h1..h4/body), so the levers for "these three
# things are different" are: structure (Card, Divider, indentation by nesting),
# heading level, and a leading mark. A glyph is the cheapest of the three and
# the only one that survives at a glance in a dense list, which is what a
# transcript is. They are also directional on purpose — → out to the tool, ←
# back from it — so a reader can follow a call/return pair without reading it.
_ASK = "▸"  # what the user asked
_CALL = "→"  # a tool going out
_BACK = "←"  # its result coming back
_SAID = "\u00b7"  # the agent narrating between calls


# What each tool's arguments MEAN, so a row reads as an action rather than as a
# wire payload. `{"command":"pytest -q"}` is the truth and `pytest -q` is the
# fact — CLAUDE.md #9 applied to arguments rather than ids: the JSON envelope is
# addressing, and a human reading their own agent's transcript should not have
# to parse it.
_ARG_OF = {
    "bash": ("command",),
    "shell": ("command",),
    "read": _PATH_KEYS,
    "view": _PATH_KEYS,
    "cat": _PATH_KEYS,
    "write": _PATH_KEYS,
    "edit": _PATH_KEYS,
    "str_replace": _PATH_KEYS,
}


def humanise_call(tool: str, raw_input: Any) -> str:
    """`bash · pytest -q`, not `bash  {"command":"pytest -q"}`.

    Falls back to the compact JSON for a tool we have no shape for, because a
    row that says only the tool name hides what it did — and an unknown tool is
    exactly when a reader most wants the arguments.
    """
    name = str(tool or "tool")
    try:
        args = json.loads(raw_input or "{}")
    except (ValueError, TypeError):
        args = None
    if not isinstance(args, dict) or not args:
        return name
    for key in _ARG_OF.get(name.lower(), ()):
        v = args.get(key)
        if isinstance(v, str) and v:
            return f"{name} · {v}"
    # No known shape. Show the arguments, minus any whole-file payload — a
    # `write` carrying 40KB of content would bury every other row on the card.
    lean = {k: v for k, v in args.items() if k != "content"}
    return f"{name} · {json.dumps(lean, separators=(',', ':'))}" if lean else name


def _turn_state(turn: dict) -> str:
    """The turn's state, else the TASK's, else unknown.

    ONE definition, because the header and the turn card each had their own and
    disagreed: a turn rebuilt from an artifact carries no state of its own, the
    header read `unknown`, and `unknown` counts as running — so a finished
    session showed "thinking…" for ever.
    """
    task = turn.get("task") if isinstance(turn.get("task"), dict) else {}
    return str(turn.get("state") or (task.get("status") or {}).get("state") or "unknown")


def _turn_card(tree: _Tree, turn: dict, *, max_rows: int) -> str:
    """One question and everything that happened because of it."""
    task = turn.get("task") if isinstance(turn.get("task"), dict) else {}
    events = events_of(task)
    state = _turn_state(turn)
    running = state in _RUNNING

    kids: list[str] = []

    # The QUESTION heads its own turn, so a reader scanning the column sees the
    # thing they asked rather than the machinery it produced.
    q = _clip(turn.get("question"), 220) or "(no question recorded)"
    kids.append(tree.text(f"{_ASK} {q}", variant="h3"))

    # Status line, and while running it names the tool in flight — the answer
    # to "what is it doing NOW", which is the question this surface exists for.
    # NOT the in-flight tool here — the header already names it, the newest
    # turn sits directly under the header, and saying it twice in two lines
    # reads as a stutter rather than as emphasis.
    kids.append(tree.text(" · ".join([state] + [b for b in (_elapsed(turn, events),) if b])))

    shown = events[-max_rows:]
    dropped = len(events) - len(shown)
    # Resolved BEFORE the loop so a narration row that merely repeats it is
    # never built. Building it and unlinking it afterwards left the component
    # orphaned in the tree — invisible, because nothing referenced it, and
    # still riding in a payload the platform re-fetches every second.
    answer = answer_text(task)
    answer_body = _clip(answer, 700, oneline=False) if answer else ""
    for e in shown:
        kind = e.get("type")
        if kind == "tool_use":
            tool = str(e.get("tool") or "tool")
            call = humanise_call(tool, e.get("input"))
            # The tool NAME is prose; its argument is literal payload.
            arg = call[len(tool) + 3 :] if call.startswith(f"{tool} · ") else ""
            row = f"{_CALL} {tool}"
            if arg:
                row += f" · {as_code(_clip(arg, 200))}"
            kids.append(tree.text(row))
        elif kind == "tool_result":
            # An empty result is a FACT worth showing: rendering nothing makes
            # a tool that returned nothing look like one that returned
            # something (the 2026-09-04 bug, one layer up).
            out = _clip(e.get("output"), 320)
            # `(no output)` is OUR words, not the tool's, so it stays prose and
            # cannot be mistaken for something the tool actually printed.
            kids.append(tree.text(f"{_BACK} {as_code(out) if out else '(no output)'}"))
        elif kind == "text":
            # The agent's narration BETWEEN tool calls. Kept as prose (markdown
            # renders) rather than flattened, and marked so the answer-dedupe
            # below can recognise it.
            body = _clip(e.get("text"), 700, oneline=False)
            # SAY IT ONCE. pi's closing `text` event and the response artifact
            # are the same words — the agent narrating its conclusion IS the
            # answer — so every card printed it twice, a paragraph apart, and
            # looked like a bug. The survivor is the ANSWER: below the divider
            # and without the `·` mark it reads as a conclusion rather than as
            # one more remark in passing. Seen in a browser 2026-09-06; nothing
            # in 66 unit tests could notice, because both halves were correct.
            if answer_body and body == answer_body:
                continue
            kids.append(tree.text(f"{_SAID} {body}"))
    if dropped > 0:
        kids.append(tree.text(f"… {dropped} earlier step(s) in this turn not shown"))

    if answer:
        kids.append(tree.divider())
        kids.append(tree.text(_clip(answer, 1200, oneline=False)))
    elif running:
        # Never a bare ellipsis. CLAUDE.md #8: the user must not have to guess
        # whether a blank means "working" or "finished with nothing to say".
        kids.append(tree.text("Waiting for the answer…"))

    return tree.card(tree.column(kids))


def _turns_of(story: dict) -> list[dict]:
    """The session's turns, oldest first.

    Accepts the pre-2026-09-06 single-turn payload too, so a surface rehydrated
    from an older session still renders instead of vanishing.
    """
    turns = story.get("turns")
    if isinstance(turns, list) and turns:
        return [t for t in turns if isinstance(t, dict)]
    if isinstance(story.get("task"), dict):
        return [
            {
                "task": story["task"],
                "state": story.get("state"),
                "question": story.get("question", ""),
                "elapsed_s": story.get("elapsed_s"),
            }
        ]
    return []


def build_story(story: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Story payload -> A2UI messages, or None when there is nothing to show.

    Pure. The single builder behind both the progressive emit and the
    registered mapping, so the surface a user watches during the run and the
    one left behind afterwards cannot disagree.

    The unit is the TURN, not the run: the resident holds one conversation per
    `contextId` and `contextId` is stable for an ADK session, so the workbench
    shows what the agent itself remembers rather than only the last question.
    """
    if not isinstance(story, dict):
        return None
    turns = _turns_of(story)
    if not turns:
        return None
    # Nothing to say at all — an empty surface is worse than none, because a
    # workbench tab that opens on nothing reads as broken rather than absent.
    if not any(events_of(t.get("task") or {}) or answer_text(t.get("task") or {}) for t in turns):
        return None

    tree = _Tree()
    latest = turns[-1]
    live = _turn_state(latest) in _RUNNING

    head = [tree.text("Assistant session", variant="h2")]
    facts = [
        f
        for f in (
            story.get("instance") and f"on {story['instance']}",
            story.get("model") and f"model {story['model']}",
            f"{len(turns)} turn{'s' if len(turns) != 1 else ''}",
        )
        if f
    ]
    head.append(tree.text(" · ".join(facts)))
    if live:
        # CLAUDE.md #8: an in-flight run is always observable.
        busy = in_flight_tool(events_of(latest.get("task") or {}))
        head.append(tree.text(f"running {busy}…" if busy else "thinking…", variant="h3"))
    header = tree.card(tree.column(head))

    # NEWEST TURN FIRST. The Basic catalog has no scroll container — a Column is
    # exactly as tall as its children — so the top of the tab is the only place
    # guaranteed to be in view, and the live turn belongs there.
    cards = [_turn_card(tree, t, max_rows=_MAX_ROWS) for t in reversed(turns)]

    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for t in turns:
        for path, verb in files_touched(events_of(t.get("task") or {})):
            if path not in seen:
                seen.add(path)
                files.append((path, verb))
    files_tab = tree.column(
        [tree.row([tree.text(verb), tree.text(path)]) for path, verb in files]
        if files
        else [tree.text("No files were read or changed.")]
    )

    answers: list[str] = []
    for t in reversed(turns):
        a = answer_text(t.get("task") or {})
        if a:
            answers.append(tree.text(f"{_ASK} {_clip(t.get('question'), 160)}", variant="h3"))
            answers.append(tree.text(_clip(a, 4000, oneline=False)))
    answer_tab = tree.column(
        answers or [tree.text("Still working…" if live else "This session produced no written answer.")]
    )

    tree.root(
        [
            header,
            tree.divider(),
            tree.tabs(
                [
                    ("Story", tree.column(cards)),
                    ("Files", files_tab),
                    ("Answers", answer_tab),
                ]
            ),
        ]
    )
    return tree.messages()


def resident_story_to_a2ui(typed_result: Any, tool_context: Any = None) -> list[dict[str, Any]] | None:
    """The registered mapping: the surface left behind when the turn ends.

    Reads the story from session state rather than from the tool result — see
    the module docstring. Absent state means the tool never recorded one (flag
    off, or a failure before the first poll), and the honest response to that is
    no surface.
    """
    if not story_enabled():
        return None
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is None:
        return None
    try:
        story = state.get(STORY_STATE_KEY)
    except Exception:  # pragma: no cover - defensive
        return None
    if not isinstance(story, dict):
        return None
    return build_story(story)


def _artifact_meta(typed_result: Any) -> dict[str, str]:
    return {
        "kind": "resident-session",
        "title": "Assistant session",
        "description": "What your assistant did, step by step",
    }


register(
    resident_story_to_a2ui,
    tool_names=RESIDENT_TOOLS,
    name="resident_session_story",
    surface=RESIDENT_SURFACE,
    artifact_meta=_artifact_meta,
)

__all__ = [
    "RESIDENT_SURFACE",
    "STORY_ENABLED_ENV",
    "STORY_STATE_KEY",
    "answer_text",
    "as_code",
    "build_story",
    "events_of",
    "events_truncated",
    "files_touched",
    "humanise_call",
    "in_flight_tool",
    "resident_story_to_a2ui",
    "story_enabled",
]
