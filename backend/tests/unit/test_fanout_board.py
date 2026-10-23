"""FANOUT M2 — the live fan-out board.

The failure this milestone exists to prevent is not "it renders wrong", it is
"it renders LATE": a board that only populates when the slowest branch lands
looks perfect in a screenshot and is exactly the eleven-second blank that
measurement found. So these tests assert ORDER and PAIRING, not just content —
and the live verifier (scripts/verify-fanout-board.sh) asserts the timing that
only a real stream can show.

See docs/design/v6.31.0/parallel-skill-fanout.md.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from adk.a2ui_fanout_render import Branch, board_components, board_messages, summary_line
from adk.callbacks import make_fanout_board_emitter
from observability.timing import (
    A2UI_SURFACE_EVENT_NAME,
    LatencyTracker,
    reset_current_tracker,
    set_current_tracker,
)

BRANCH_TOOLS = frozenset({"research_worker"})


def _ctx(invocation_id: str = "inv-1", call_id: str = "c1", user_text: str = "do the thing"):
    return SimpleNamespace(
        invocation_id=invocation_id,
        function_call_id=call_id,
        user_content=SimpleNamespace(parts=[SimpleNamespace(text=user_text)]),
    )


def _tool(name: str = "research_worker"):
    return SimpleNamespace(name=name)


def _surfaces(tracker: LatencyTracker) -> list[dict]:
    return [ev.value for ev in tracker.drain_stage_events() if getattr(ev, "name", "") == A2UI_SURFACE_EVENT_NAME]


@pytest.fixture()
def tracker():
    t = LatencyTracker(skill_id="s", session_id="sess", user_id="u")
    token = set_current_tracker(t)
    yield t
    reset_current_tracker(token)


# --- the tree --------------------------------------------------------------


def test_root_is_a_column_and_children_are_id_references():
    """A2UI v0.9 composes by id string; a nested object renders nothing."""
    comps = board_components([Branch(objective="a")])
    root = [c for c in comps if c["id"] == "root"]
    assert root and root[0]["component"] == "Column"
    assert all(isinstance(child, str) for child in root[0]["children"])
    ids = {c["id"] for c in comps}
    for c in comps:
        for ref in c.get("children", []) or ([c["child"]] if c.get("child") else []):
            assert ref in ids, f"{c['id']} references missing component {ref}"


def test_every_branch_gets_a_card_with_objective_and_status():
    comps = board_components([Branch(objective="solar PPAs"), Branch(objective="BESS")])
    texts = [c.get("text", "") for c in comps]
    assert "solar PPAs" in texts and "BESS" in texts
    assert sum(1 for c in comps if c["component"] == "Card") == 2


def test_a_failed_branch_shows_its_reason():
    """A merge over 2 of 3 branches reads as a complete answer — the missing
    third has to say so, or the user cannot tell they were short-changed."""
    b = Branch(objective="wind", status="failed", error="upstream timeout")
    b.ended = b.started + 1.0
    text = " ".join(c.get("text", "") for c in board_components([b]))
    assert "failed" in text and "upstream timeout" in text


def test_a_failed_branch_with_no_reason_still_says_something():
    b = Branch(objective="wind", status="failed")
    b.ended = b.started + 1.0
    assert "no reason given" in " ".join(c.get("text", "") for c in board_components([b]))


def test_merged_brief_is_absent_until_it_exists():
    """An empty 'Merged brief' heading above nothing reads as a failure."""
    without = board_components([Branch(objective="a")])
    assert not any(c["id"] == "merged-h" for c in without)
    with_merge = board_components([Branch(objective="a")], merged="the answer")
    assert any(c.get("text") == "the answer" for c in with_merge)


def test_summary_line_counts_each_state():
    bs = [
        Branch(objective="a", status="done"),
        Branch(objective="b", status="running"),
        Branch(objective="c", status="failed"),
    ]
    line = summary_line(bs)
    assert "3 branches" in line and "1 done" in line and "1 running" in line and "1 failed" in line


def test_messages_carry_create_surface_then_components():
    msgs = board_messages("fanout:inv-1", [Branch(objective="a")])
    assert msgs[0]["createSurface"]["surfaceId"] == "fanout:inv-1"
    assert msgs[1]["updateComponents"]["surfaceId"] == "fanout:inv-1"


# --- the emitter -----------------------------------------------------------


def test_board_appears_on_the_first_branch_start_not_at_the_end(tracker):
    """The whole point: all objectives on screen while the branches run."""
    before, _after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    for i, obj in enumerate(["solar", "BESS", "wind"]):
        before(_tool(), {"request": obj}, _ctx(call_id=f"c{i}"))

    events = _surfaces(tracker)
    assert len(events) == 3, "each branch start must push an update"
    last = " ".join(c.get("text", "") for c in events[-1]["messages"][1]["updateComponents"]["components"])
    assert "solar" in last and "BESS" in last and "wind" in last
    # ...and all of it BEFORE any branch has finished.
    assert "running" in last


def test_one_surface_id_for_every_update(tracker):
    """N branches must not become N workbench artifacts."""
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    for i, obj in enumerate(["a", "b"]):
        before(_tool(), {"request": obj}, _ctx(call_id=f"c{i}"))
    after(_tool(), {"request": "a"}, _ctx(call_id="c0"), "result a")

    ids = {e["surfaceId"] for e in _surfaces(tracker)}
    assert ids == {"fanout:inv-1"}, f"expected one surface, got {ids}"


def test_source_ids_are_unique_per_update(tracker):
    """The frontend dedupes on sourceId — a repeated one silently drops an update."""
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx(call_id="c0"))
    before(_tool(), {"request": "b"}, _ctx(call_id="c1"))
    after(_tool(), {"request": "a"}, _ctx(call_id="c0"), "done")
    src = [e["sourceId"] for e in _surfaces(tracker)]
    assert len(src) == len(set(src)), f"duplicate sourceIds would be deduped away: {src}"


def test_artifact_metadata_rides_the_first_update(tracker):
    """Artifact metadata is what creates the tab — it has to arrive with the
    board at ~2s, not with the result at the end."""
    before, _after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx())
    first = _surfaces(tracker)[0]
    assert first["artifact"]["kind"] == "fanout"
    assert first["artifact"]["title"] == "Research fan-out"


def test_branches_are_paired_by_call_id_not_by_objective_text(tracker):
    """Two branches asking the same question must not collide into one card."""
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "same"}, _ctx(call_id="c0"))
    before(_tool(), {"request": "same"}, _ctx(call_id="c1"))
    after(_tool(), {"request": "same"}, _ctx(call_id="c0"), "result")

    comps = _surfaces(tracker)[-1]["messages"][1]["updateComponents"]["components"]
    assert sum(1 for c in comps if c["component"] == "Card") == 2
    details = [c["text"] for c in comps if c["id"].startswith("d-")]
    assert any("done" in d for d in details) and any("running" in d for d in details)


def test_an_empty_branch_result_is_a_failure_not_a_success(tracker):
    """The 'empty run' failure mode: a done card with nothing behind it is a
    lie the user cannot detect."""
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx(call_id="c0"))
    after(_tool(), {"request": "a"}, _ctx(call_id="c0"), "")
    text = " ".join(c.get("text", "") for c in _surfaces(tracker)[-1]["messages"][1]["updateComponents"]["components"])
    assert "failed" in text and "returned nothing" in text


def test_an_error_result_shows_the_error(tracker):
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx(call_id="c0"))
    after(_tool(), {"request": "a"}, _ctx(call_id="c0"), {"error": "403 not opted in"})
    text = " ".join(c.get("text", "") for c in _surfaces(tracker)[-1]["messages"][1]["updateComponents"]["components"])
    assert "403 not opted in" in text


def test_concurrent_turns_do_not_share_a_board(tracker):
    """Keyed by invocation_id — two turns must not write onto one surface."""
    before, _after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx(invocation_id="inv-1", call_id="c0"))
    before(_tool(), {"request": "b"}, _ctx(invocation_id="inv-2", call_id="c0"))
    assert {e["surfaceId"] for e in _surfaces(tracker)} == {"fanout:inv-1", "fanout:inv-2"}


def test_a_non_branch_tool_is_ignored(tracker):
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool("ai_search"), {"query": "x"}, _ctx())
    after(_tool("ai_search"), {"query": "x"}, _ctx(), "res")
    assert not _surfaces(tracker), "only AgentTool delegates draw the board"


def test_the_emitter_never_breaks_the_turn(tracker):
    """Fail-open: a board bug must not cost the user their answer."""
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    broken = SimpleNamespace(name="research_worker")
    # A context missing everything the emitter reads.
    assert before(broken, {"request": "a"}, SimpleNamespace()) is None
    assert after(broken, {"request": "a"}, SimpleNamespace(), "r") is None


def test_elapsed_is_measured_not_guessed(tracker):
    before, after, _start = make_fanout_board_emitter(BRANCH_TOOLS)
    before(_tool(), {"request": "a"}, _ctx(call_id="c0"))
    time.sleep(0.05)
    after(_tool(), {"request": "a"}, _ctx(call_id="c0"), "ok")
    details = [
        c["text"]
        for c in _surfaces(tracker)[-1]["messages"][1]["updateComponents"]["components"]
        if c["id"].startswith("d-")
    ]
    assert details and "done in 0." in details[0]


# --- the check that catches "renders blank" --------------------------------


def test_board_validates_against_the_real_a2ui_catalog():
    """Schema-validate every state the board can be in.

    "A2UI won't render in the Workspace" is a named recurring bug class
    (CLAUDE.md #7), and an invalid component or prop fails SILENTLY in the
    browser — nothing is logged, the tab is just empty. The validator is the
    only cheap thing that catches it before a real browser does.
    """
    from adk.a2ui_render_preview import validate_a2ui

    done = Branch(objective="solar PPAs", status="done")
    done.ended = done.started + 9.9
    failed = Branch(objective="wind", status="failed", error="boom")
    failed.ended = failed.started + 2.0
    states = [
        [],  # nothing started yet
        [Branch(objective="solar PPAs")],  # one running
        [done, Branch(objective="BESS"), failed],  # mixed
    ]
    for branches in states:
        validate_a2ui(board_messages("fanout:inv-1", branches))
    # ...and with the merged brief present.
    validate_a2ui(board_messages("fanout:inv-1", [done], merged="the merged brief"))


# --- the planning state (the 27-second blank) -------------------------------


def test_the_board_exists_before_any_branch_does(tracker):
    """Measured live on dev: the parent spent 27s of a 92s turn deciding the
    decomposition before issuing a single branch call. Without a board at turn
    start that is 27 seconds with no workbench tab — an immediate pending state
    is what rule #8 asks for, not merely eventual progress."""
    _before, _after, start = make_fanout_board_emitter(BRANCH_TOOLS)
    start(_ctx())

    events = _surfaces(tracker)
    assert len(events) == 1, "the turn start must draw the board"
    assert events[0]["surfaceId"] == "fanout:inv-1"
    assert events[0]["artifact"]["kind"] == "fanout", "the tab has to exist now, not later"
    text = " ".join(c.get("text", "") for c in events[0]["messages"][1]["updateComponents"]["components"])
    assert "planning" in text, "an empty board must say what is happening"


def test_the_planning_board_becomes_the_branch_board(tracker):
    """Same surface — the planning state must not be a second artifact."""
    before, _after, start = make_fanout_board_emitter(BRANCH_TOOLS)
    start(_ctx())
    before(_tool(), {"request": "solar"}, _ctx(call_id="c0"))
    assert {e["surfaceId"] for e in _surfaces(tracker)} == {"fanout:inv-1"}


def test_turn_start_is_idempotent(tracker):
    """A retry or a second before_agent pass must not wipe live branches."""
    before, _after, start = make_fanout_board_emitter(BRANCH_TOOLS)
    start(_ctx())
    before(_tool(), {"request": "solar"}, _ctx(call_id="c0"))
    start(_ctx())  # again
    last = _surfaces(tracker)[-1]["messages"][1]["updateComponents"]["components"]
    assert any(c.get("text") == "solar" for c in last), "the second start erased a running branch"


def test_turn_start_never_breaks_the_turn(tracker):
    _before, _after, start = make_fanout_board_emitter(BRANCH_TOOLS)
    assert start(SimpleNamespace()) is None
