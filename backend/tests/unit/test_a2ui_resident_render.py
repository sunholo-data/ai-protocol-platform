"""The resident session story surface (v6.40.0 M7 Phase 2).

WHAT THESE COVER, AND WHAT THEY CANNOT

They cover the shaping: what a task's `events` artifact becomes, what happens
when it is absent or malformed, and the anti-drift property that the
progressive emit and the registered mapping build the SAME tree.

They cannot tell you it renders. jsdom passing is not rendering and neither is
this — CLAUDE.md #7 is explicit that an A2UI-workspace change is not done until
a real browser or a real AG-UI event stream confirms it, and 7.2/7.3/7.5 each
shipped broken by deferring exactly that. `scripts/verify-resident-story.sh` is
the live half.
"""

from __future__ import annotations

import json

import pytest

from adk.a2ui_resident_render import (
    STORY_ENABLED_ENV,
    STORY_STATE_KEY,
    answer_text,
    as_code,
    build_story,
    events_of,
    events_truncated,
    files_touched,
    humanise_call,
    in_flight_tool,
    resident_story_to_a2ui,
    story_enabled,
)


def _task(events=None, answer=None, state="completed", truncated=False):
    artifacts = []
    if events is not None:
        data = {"events": events}
        if truncated:
            data["truncated"] = True
        artifacts.append({"artifactId": "events", "name": "session-events", "parts": [{"kind": "data", "data": data}]})
    if answer is not None:
        artifacts.append(
            {"artifactId": "response", "name": "agent-response", "parts": [{"kind": "text", "text": answer}]}
        )
    return {"id": "t-1", "status": {"state": state}, "artifacts": artifacts}


def _comps(messages):
    return messages[1]["updateComponents"]["components"]


def _texts(messages):
    return [c["text"] for c in _comps(messages) if c["component"] == "Text"]


def _of_type(messages, kind):
    return [c for c in _comps(messages) if c["component"] == kind]


class _State(dict):
    """Stands in for ADK session state, which is dict-like."""


class _Ctx:
    def __init__(self, state=None):
        self.state = state


# ── reading the artifact ─────────────────────────────────────────────────────


def test_events_absent_is_not_an_error():
    """An older instance never writes this artifact. Every hop is additive."""
    assert events_of(_task(answer="hi")) == []
    assert events_of({}) == []
    assert events_of(None) == []


@pytest.mark.parametrize(
    "artifacts",
    [
        [{"artifactId": "events", "parts": [{"kind": "data", "data": "not a dict"}]}],
        [{"artifactId": "events", "parts": [{"kind": "data", "data": {"events": "not a list"}}]}],
        [{"artifactId": "events", "parts": "not a list"}],
        [{"artifactId": "events"}],
        ["not a dict"],
    ],
)
def test_malformed_artifact_degrades_to_empty_rather_than_raising(artifacts):
    """This is read inside a 2s poll loop. A raise here would take down the
    turn to render a decoration."""
    assert events_of({"artifacts": artifacts}) == []


def test_non_dict_events_are_dropped_not_rendered():
    assert events_of(_task(events=[{"type": "text", "text": "ok"}, "junk", None])) == [{"type": "text", "text": "ok"}]


def test_answer_is_read_from_the_response_artifact_by_name():
    """NOT "the first text part of any artifact" — that is `a2a_client.answer_of`,
    and it is only correct while `events` carries no text part. The events
    artifact is written FIRST by the run loop, so it is artifacts[0]."""
    task = _task(events=[{"t": 1, "type": "text", "text": "thinking out loud"}], answer="the answer")
    assert task["artifacts"][0]["artifactId"] == "events"
    assert answer_text(task) == "the answer"


# ── files touched ────────────────────────────────────────────────────────────


def test_files_are_derived_from_tool_arguments():
    events = [
        {"type": "tool_use", "tool": "read", "input": json.dumps({"path": "/w/a.py"})},
        {"type": "tool_use", "tool": "write", "input": json.dumps({"file_path": "/w/b.py", "content": "x"})},
        {"type": "tool_use", "tool": "edit", "input": json.dumps({"filePath": "/w/c.py"})},
    ]
    assert files_touched(events) == [("/w/a.py", "read"), ("/w/b.py", "written"), ("/w/c.py", "edited")]


def test_a_file_read_then_edited_is_an_edited_file():
    events = [
        {"type": "tool_use", "tool": "read", "input": json.dumps({"path": "/w/a.py"})},
        {"type": "tool_use", "tool": "edit", "input": json.dumps({"path": "/w/a.py"})},
    ]
    assert files_touched(events) == [("/w/a.py", "edited")]


def test_bash_contributes_no_file():
    """Guessing a path out of a shell command is how a files list starts lying."""
    events = [{"type": "tool_use", "tool": "bash", "input": json.dumps({"command": "rm -rf /w/a.py"})}]
    assert files_touched(events) == []


def test_unparseable_tool_arguments_are_skipped_silently():
    events = [
        {"type": "tool_use", "tool": "read", "input": "{not json"},
        {"type": "tool_use", "tool": "read", "input": json.dumps(["a", "list"])},
        {"type": "tool_use", "tool": "read", "input": json.dumps({"path": "/w/ok.py"})},
    ]
    assert files_touched(events) == [("/w/ok.py", "read")]


# ── the in-flight indicator ──────────────────────────────────────────────────


def test_in_flight_tool_is_the_one_with_no_result():
    events = [
        {"type": "tool_use", "tool": "bash"},
        {"type": "tool_result", "tool": "bash"},
        {"type": "tool_use", "tool": "read"},
    ]
    assert in_flight_tool(events) == "read"


def test_two_calls_to_the_same_tool_do_not_clear_each_other():
    """Matched by POSITION, not by name. Otherwise the first bash's result
    closes the second and the indicator clears while the agent is still
    working — a silent state, which is what this feature removes."""
    events = [
        {"type": "tool_use", "tool": "bash"},
        {"type": "tool_use", "tool": "bash"},
        {"type": "tool_result", "tool": "bash"},
    ]
    assert in_flight_tool(events) == "bash"


def test_nothing_in_flight_when_every_call_returned():
    events = [{"type": "tool_use", "tool": "bash"}, {"type": "tool_result", "tool": "bash"}]
    assert in_flight_tool(events) == ""


# ── the surface ──────────────────────────────────────────────────────────────


def test_a_run_with_nothing_to_say_produces_no_surface():
    """An empty surface is worse than none: a workbench tab that opens on
    nothing reads as a broken feature rather than an absent one."""
    assert build_story({"task": _task()}) is None
    assert build_story({}) is None
    assert build_story(None) is None


def test_prose_only_when_the_instance_wrote_no_events():
    """New platform, old instance. It must render, not break."""
    messages = build_story({"task": _task(answer="just prose")})
    assert messages is not None
    assert "just prose" in _texts(messages)


def test_the_three_sections_are_tabs():
    messages = build_story({"task": _task(events=[{"t": 1, "type": "text", "text": "hi"}], answer="a")})
    titles = [t["title"] for tabset in _of_type(messages, "Tabs") for t in tabset["tabs"]]
    assert titles == ["Story", "Files", "Answers"]


def test_root_is_present_and_children_are_id_strings():
    """v0.9's refs-not-inline rule: children are component ids, never nested
    objects, and the tree root must be `root`."""
    messages = build_story({"task": _task(events=[{"t": 1, "type": "text", "text": "hi"}], answer="a")})
    comps = _comps(messages)
    ids = {c["id"] for c in comps}
    assert "root" in ids
    for c in comps:
        for child in c.get("children", []):
            assert isinstance(child, str) and child in ids
        if "child" in c:
            assert isinstance(c["child"], str) and c["child"] in ids
        for tab in c.get("tabs", []):
            assert isinstance(tab["child"], str) and tab["child"] in ids


def test_only_components_this_codebase_already_ships_are_used():
    """ "A2UI won't render in the Workspace" is a named recurring bug whose
    remedy is to diff against a known-good path. An unproven component (Icon)
    is how this ships looking correct and rendering blank."""
    messages = build_story(
        {"task": _task(events=[{"t": 1, "type": "tool_use", "tool": "read", "input": '{"path":"/w/a"}'}], answer="a")}
    )
    used = {c["component"] for c in _comps(messages)}
    assert used <= {"Text", "Column", "Row", "Card", "List", "Divider", "Tabs"}


def test_no_caption_variant():
    """The v0.9 React SDK renders <caption>, valid only inside <table>, and it
    triggers a hydration warning."""
    messages = build_story({"task": _task(events=[{"t": 1, "type": "text", "text": "hi"}], answer="a")})
    assert all(c.get("variant") != "caption" for c in _comps(messages))


def test_an_empty_tool_result_is_shown_as_a_fact():
    """Rendering nothing makes "the tool returned nothing" look identical to
    "the tool returned something" — the 2026-09-04 bug, one layer up."""
    messages = build_story(
        {
            "task": _task(
                events=[
                    {"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"},
                    {"t": 2, "type": "tool_result", "tool": "bash", "output": ""},
                ]
            )
        }
    )
    assert any("(no output)" in t for t in _texts(messages))


def test_a_running_turn_names_the_tool_in_flight():
    messages = build_story(
        {"task": _task(events=[{"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"}], state="working")}
    )
    assert any(t == "running bash…" for t in _texts(messages))


def test_a_running_turn_with_no_tool_still_says_something():
    """CLAUDE.md #8: an in-flight run is always observable. "working" with
    nothing after it is the blank wait this replaces."""
    messages = build_story({"task": _task(events=[{"t": 1, "type": "text", "text": "hm"}], state="working")})
    assert "thinking…" in _texts(messages)


def test_the_indicator_is_gone_once_the_run_finished():
    messages = build_story(
        {
            "task": _task(
                events=[{"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"}], answer="done", state="completed"
            )
        }
    )
    assert not any(t.startswith("running ") or t == "thinking…" or "running " in t for t in _texts(messages))


def test_instance_side_truncation_is_still_readable():
    """The instance drops the oldest events at its own cap and flags it. The
    surface discloses its OWN per-turn overflow (it counts the rows it did not
    draw), so the artifact flag is no longer what a reader meets — but it is a
    published field other consumers read, `aiplatform resident story` included,
    and it must not stop being reported."""
    assert events_truncated(_task(events=[{"t": 1, "type": "text", "text": "hi"}], truncated=True))
    assert not events_truncated(_task(events=[{"t": 1, "type": "text", "text": "hi"}]))


def test_a_failed_run_still_renders_its_story():
    """The run with the most to say. Losing it leaves the failure with no
    context at all."""
    messages = build_story(
        {
            "task": _task(
                events=[{"t": 1, "type": "tool_use", "tool": "bash", "input": '{"command":"false"}'}], state="failed"
            )
        }
    )
    assert messages is not None
    assert any("failed" in t for t in _texts(messages))


def test_header_carries_the_instance_by_NAME_not_a_url():
    """CLAUDE.md #9: what a human reads is a name."""
    messages = build_story(
        {"task": _task(answer="a"), "instance": "resident-pi-ailang", "model": "z-ai/glm-5.3-flash", "elapsed_s": 95}
    )
    joined = " ".join(_texts(messages))
    assert "resident-pi-ailang" in joined
    assert "1m 35s" in joined
    assert "https://" not in joined


# ── the registered mapping ───────────────────────────────────────────────────


def test_flag_off_renders_nothing(monkeypatch):
    """Not a broken empty surface — nothing."""
    monkeypatch.delenv(STORY_ENABLED_ENV, raising=False)
    assert story_enabled() is False
    ctx = _Ctx(_State({STORY_STATE_KEY: {"task": _task(answer="a")}}))
    assert resident_story_to_a2ui({"answer": "a"}, ctx) is None


def test_flag_is_read_at_call_time_not_import_time(monkeypatch):
    """A module-level constant freezes it at import, and a test that sets the
    env afterwards then passes while proving nothing."""
    monkeypatch.delenv(STORY_ENABLED_ENV, raising=False)
    assert story_enabled() is False
    monkeypatch.setenv(STORY_ENABLED_ENV, "true")
    assert story_enabled() is True


def test_mapping_reads_the_story_from_session_state(monkeypatch):
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    ctx = _Ctx(_State({STORY_STATE_KEY: {"task": _task(answer="the answer")}}))
    messages = resident_story_to_a2ui({"answer": "the answer"}, ctx)
    assert messages is not None
    assert "the answer" in _texts(messages)


def test_mapping_survives_absent_or_hostile_state(monkeypatch):
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    assert resident_story_to_a2ui({}, None) is None
    assert resident_story_to_a2ui({}, _Ctx(None)) is None
    assert resident_story_to_a2ui({}, _Ctx(_State())) is None
    assert resident_story_to_a2ui({}, _Ctx(_State({STORY_STATE_KEY: "not a dict"}))) is None


def test_the_tool_result_never_carries_the_story(monkeypatch):
    """The result goes to the MODEL. The story travels in session state
    precisely so it does not spend context and invite the model to narrate
    what the surface is already showing."""
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    ctx = _Ctx(
        _State(
            {
                STORY_STATE_KEY: {
                    "task": _task(
                        events=[{"t": 1, "type": "tool_use", "tool": "bash", "input": '{"command":"ls"}'}], answer="a"
                    )
                }
            }
        )
    )
    result = {"answer": "a", "thread": "th", "task_id": "t-1"}
    assert resident_story_to_a2ui(result, ctx) is not None
    assert set(result) == {"answer", "thread", "task_id"}


def test_progressive_and_registered_paths_build_the_SAME_tree(monkeypatch):
    """THE ANTI-DRIFT ASSERTION. Two renderers of one run would eventually
    disagree about it, and the user would watch one story become another."""
    monkeypatch.setenv(STORY_ENABLED_ENV, "1")
    story = {
        "task": _task(
            events=[
                {"t": 1, "type": "tool_use", "tool": "read", "input": '{"path":"/w/a.py"}'},
                {"t": 2, "type": "tool_result", "tool": "read", "output": "contents"},
            ],
            answer="done",
        ),
        "instance": "resident-x",
        "model": "m",
        "elapsed_s": 12,
    }

    progressive = build_story(story)  # the poll tick
    registered = resident_story_to_a2ui({}, _Ctx(_State({STORY_STATE_KEY: story})))  # the callback

    assert progressive == registered


class TestTheSessionReadsAsAConversation:
    """Turns newest-first; rows WITHIN a turn chronological.

    Those pull in opposite directions on purpose. The Basic catalog has no
    scroll container — a Column is exactly as tall as its children — so the top
    of the tab is the only place guaranteed to be in view, and the live turn
    belongs there. Inside one turn, though, a transcript that ran backwards
    would be unreadable: you ask, it calls, it answers.
    """

    def _story(self, n_turns, steps=2):
        turns = []
        for i in range(n_turns):
            events = [{"t": j, "type": "tool_use", "tool": f"tool{i}_{j}", "input": "{}"} for j in range(steps)]
            turns.append(
                {
                    "task_id": f"t{i}",
                    "question": f"question {i}",
                    "state": "completed",
                    "task": _task(events=events, answer=f"answer {i}"),
                }
            )
        return {"turns": turns, "instance": "resident-x"}

    def _texts_of(self, story):
        return [c["text"] for c in _comps(build_story(story)) if c["component"] == "Text"]

    def test_the_newest_TURN_is_first(self):
        texts = self._texts_of(self._story(3))
        qs = [t for t in texts if t.startswith("▸")]
        assert qs[0] == "▸ question 2", "the live turn belongs at the top of a tab that cannot scroll"
        assert "▸ question 0" in qs[-1] or qs[-2] == "▸ question 0"

    def test_rows_WITHIN_a_turn_stay_chronological(self):
        texts = self._texts_of(self._story(1, steps=3))
        calls = [t for t in texts if t.startswith("→")]
        assert calls == ["→ tool0_0", "→ tool0_1", "→ tool0_2"]

    def test_every_turn_is_shown_not_just_the_last(self):
        """The resident remembers the whole conversation; before 2026-09-06 the
        surface replaced it each turn and was the only party losing the thread."""
        texts = self._texts_of(self._story(4))
        for i in range(4):
            assert any(f"question {i}" in t for t in texts)

    def test_the_header_counts_the_turns(self):
        assert any("3 turns" in t for t in self._texts_of(self._story(3)))
        assert any("1 turn" in t and "1 turns" not in t for t in self._texts_of(self._story(1)))

    def test_question_tool_and_result_are_visually_distinct(self):
        """Basic has no colour and Text has only `variant`, so the distinction
        has to be carried by structure and a leading mark."""
        story = {
            "turns": [
                {
                    "task_id": "t",
                    "question": "q",
                    "state": "completed",
                    "task": _task(
                        events=[
                            {"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"},
                            {"t": 2, "type": "tool_result", "tool": "bash", "output": "out"},
                        ],
                        answer="done",
                    ),
                }
            ]
        }
        texts = self._texts_of(story)
        assert any(t.startswith("▸ ") for t in texts), "the question"
        assert any(t.startswith("→ ") for t in texts), "the call"
        assert any(t.startswith("← ") for t in texts), "the result"

    def test_per_turn_steps_are_capped_keeping_the_RECENT_end(self):
        texts = self._texts_of(self._story(1, steps=60))
        calls = [t for t in texts if t.startswith("→")]
        assert len(calls) == 24
        assert calls[-1] == "→ tool0_59", "the cap must drop the oldest, not the newest"
        assert any("earlier step(s) in this turn not shown" in t for t in texts)

    def test_the_older_single_turn_payload_still_renders(self):
        """A surface rehydrated from a session that predates turns must render,
        not vanish."""
        messages = build_story({"task": _task(events=[{"t": 1, "type": "text", "text": "hi"}], answer="a")})
        assert messages is not None
        assert "a" in [c["text"] for c in _comps(messages) if c["component"] == "Text"]

    def test_ordering_does_not_disturb_the_in_flight_match(self):
        events = [
            {"type": "tool_use", "tool": "bash"},
            {"type": "tool_use", "tool": "bash"},
            {"type": "tool_result", "tool": "bash"},
        ]
        assert in_flight_tool(events) == "bash"
        texts = self._texts_of(
            {
                "turns": [
                    {"task_id": "t", "question": "q", "state": "working", "task": _task(events=events, state="working")}
                ]
            }
        )
        assert any("running bash…" in t for t in texts)


class TestARowReadsAsAnActionNotAPayload:
    """`bash · pytest -q`, not `bash {"command":"pytest -q"}`.

    CLAUDE.md #9 applied to arguments rather than ids: the JSON envelope is
    addressing, and someone reading their own agent's transcript should not
    have to parse it to see what it did.
    """

    def test_a_shell_command_is_the_command(self):
        assert humanise_call("bash", '{"command":"pytest -q"}') == "bash · pytest -q"

    @pytest.mark.parametrize("tool", ["read", "write", "edit"])
    def test_a_file_tool_is_the_path(self, tool):
        assert humanise_call(tool, '{"path":"/w/a.py"}') == f"{tool} · /w/a.py"

    def test_an_unknown_tool_still_shows_its_arguments(self):
        """A row that says only the tool name hides what it did — and an
        unknown tool is exactly when the arguments matter most."""
        assert humanise_call("frobnicate", '{"x":1}') == 'frobnicate · {"x":1}'

    def test_a_whole_file_payload_is_not_shown(self):
        """A `write` carrying 40KB of content would bury every other row."""
        out = humanise_call("deploy", '{"content":"' + "x" * 500 + '","target":"prod"}')
        assert out == 'deploy · {"target":"prod"}'
        assert "xxxx" not in out

    def test_unparseable_or_empty_arguments_degrade_to_the_name(self):
        assert humanise_call("bash", "{not json") == "bash"
        assert humanise_call("bash", "{}") == "bash"
        assert humanise_call("", None) == "tool"


def test_the_in_flight_tool_is_named_once_not_twice():
    """The header names it and the newest turn sits directly beneath the
    header, so repeating it in the turn's status line read as a stutter."""
    messages = build_story(
        {
            "turns": [
                {
                    "task_id": "t",
                    "question": "q",
                    "state": "working",
                    "task": _task(
                        events=[{"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"}], state="working"
                    ),
                }
            ]
        }
    )
    texts = [c["text"] for c in _comps(messages) if c["component"] == "Text"]
    assert len([t for t in texts if "running bash" in t]) == 1


def test_a_running_turn_never_shows_a_bare_ellipsis():
    """CLAUDE.md #8: a blank must not leave the user guessing between
    "working" and "finished with nothing to say"."""
    messages = build_story(
        {
            "turns": [
                {
                    "task_id": "t",
                    "question": "q",
                    "state": "working",
                    "task": _task(events=[{"t": 1, "type": "text", "text": "hm"}], state="working"),
                }
            ]
        }
    )
    texts = [c["text"] for c in _comps(messages) if c["component"] == "Text"]
    assert "…" not in texts
    assert "Waiting for the answer…" in texts


class TestTheTranscriptDoesNotEditItsEvidence:
    """`Text` renders markdown, so literal payloads must be escaped into it.

    Found in a browser 2026-09-06: a command containing `**bold**` displayed
    with the bold APPLIED, and a tool output that really did contain asterisks
    showed none of them. Both individually-correct pieces — markdown rendering,
    and passing the payload through — combined into a surface that misreported
    what ran. A transcript that edits its own evidence is not a transcript.
    """

    def test_a_plain_payload_is_fenced(self):
        assert as_code("ls -a") == "`ls -a`"

    def test_markdown_inside_a_payload_survives_verbatim(self):
        assert as_code('print("**b**")') == '`print("**b**")`'

    @pytest.mark.parametrize(
        "payload,expected",
        [("has `one` tick", "``has `one` tick``"), ("has ``two`` ticks", "```has ``two`` ticks```")],
    )
    def test_the_fence_grows_past_backticks_inside(self, payload, expected):
        assert as_code(payload) == expected

    @pytest.mark.parametrize("payload", ["`leading", "trailing`"])
    def test_a_payload_touching_a_backtick_is_padded(self, payload):
        out = as_code(payload)
        assert payload in out
        assert out.startswith("`` ") or out.endswith(" ``")

    def test_empty_stays_empty(self):
        assert as_code("") == ""

    def test_the_command_row_carries_the_payload_verbatim(self):
        messages = build_story(
            {
                "turns": [
                    {
                        "task_id": "t",
                        "question": "q",
                        "state": "completed",
                        "task": _task(
                            events=[
                                {
                                    "t": 1,
                                    "type": "tool_use",
                                    "tool": "bash",
                                    "input": json.dumps({"command": 'echo "**x**"'}),
                                }
                            ],
                            answer="a",
                        ),
                    }
                ]
            }
        )
        row = next(c["text"] for c in _comps(messages) if c["component"] == "Text" and c["text"].startswith("→"))
        assert row == '→ bash · `echo "**x**"`'

    def test_our_own_words_stay_prose(self):
        """`(no output)` is the surface talking, not the tool. Fencing it would
        claim the tool printed those characters."""
        messages = build_story(
            {
                "turns": [
                    {
                        "task_id": "t",
                        "question": "q",
                        "state": "completed",
                        "task": _task(
                            events=[
                                {"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"},
                                {"t": 2, "type": "tool_result", "tool": "bash", "output": ""},
                            ],
                            answer="a",
                        ),
                    }
                ]
            }
        )
        assert "← (no output)" in [c["text"] for c in _comps(messages) if c["component"] == "Text"]


class TestTheAnswerIsSaidOnce:
    """pi's closing `text` event and the response artifact are the same words,
    so every card printed the answer twice a paragraph apart."""

    def _texts(self, answer, narration):
        events = [
            {"t": 1, "type": "tool_use", "tool": "bash", "input": "{}"},
            {"t": 2, "type": "text", "text": narration},
        ]
        messages = build_story(
            {
                "turns": [
                    {"task_id": "t", "question": "q", "state": "completed", "task": _task(events=events, answer=answer)}
                ]
            }
        )
        return [c["text"] for c in _comps(messages) if c["component"] == "Text"]

    def test_a_narration_matching_the_answer_is_dropped(self):
        """The survivor is the ANSWER row, not the narration.

        Counted on the `·` mark rather than on the text, because the Answers tab
        legitimately carries a second copy — it indexes every turn in the
        session — and asserting on a raw count would fail for the wrong reason.
        """
        texts = self._texts("All done.", "All done.")
        assert not [t for t in texts if t.startswith("· ")], "the narration row must be gone"
        assert "All done." in texts

    def test_narration_that_differs_is_kept_alongside_the_answer(self):
        texts = self._texts("All done.", "Checking the directory first…")
        assert "· Checking the directory first…" in texts
        assert "All done." in texts

    def test_newlines_survive_into_the_answer(self):
        """`_clip` collapsed whitespace, which flattens a fenced code block onto
        one line and leaves stray backticks in a run of prose."""
        texts = self._texts("Here:\n\n```\nline1\nline2\n```", "narration")
        assert any("```\nline1\nline2\n```" in t for t in texts)
