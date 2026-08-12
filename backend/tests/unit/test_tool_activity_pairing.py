"""Tool call/response pairing when ids are absent — v6.23.0 B5 Phase 2.

Measured on dev 2026-08-10 as firing zero times in 100 sessions, so this is a
latent trap rather than an active outage. It is fixed anyway because its failure
mode is the worst kind: a tool that SUCCEEDED renders as FAILED in the admin
trace, which sends an operator to debug something that never happened.
"""

from __future__ import annotations

from types import SimpleNamespace

from protocols.sessions_route import _events_to_tool_activity


def _part(*, call=None, response=None):
    fc = SimpleNamespace(name=call[0], id=call[1], args={}) if call else None
    fr = (
        SimpleNamespace(name=response[0], id=response[1], response=response[2] if len(response) > 2 else {"ok": True})
        if response
        else None
    )
    return SimpleNamespace(text=None, function_call=fc, function_response=fr)


def _event(parts, ts=1_700_000_000.0):
    return SimpleNamespace(author="model", timestamp=ts, content=SimpleNamespace(parts=list(parts)))


def test_response_without_id_pairs_by_name():
    """The trap: a successful tool rendered as failed."""
    items = _events_to_tool_activity(
        [
            _event([_part(call=("map_ppa_obligations", "c1"))]),
            _event([_part(response=("map_ppa_obligations", None))]),
        ]
    )
    assert len(items) == 1
    assert items[0].status == "success"
    assert items[0].resultContent is not None


def test_call_without_id_still_pairs_with_its_response():
    items = _events_to_tool_activity(
        [
            _event([_part(call=("extract_ppa_clauses", None))]),
            _event([_part(response=("extract_ppa_clauses", None))]),
        ]
    )
    assert [i.status for i in items] == ["success"]


def test_id_pairing_still_wins_and_is_not_disturbed():
    """Normal, id-carrying traffic must be untouched by the fallback."""
    items = _events_to_tool_activity(
        [
            _event([_part(call=("a", "c1"))]),
            _event([_part(call=("b", "c2"))]),
            _event([_part(response=("b", "c2", {"which": "b"}))]),
            _event([_part(response=("a", "c1", {"which": "a"}))]),
        ]
    )
    by_name = {i.name: i for i in items}
    assert by_name["a"].status == "success" and '"a"' in by_name["a"].resultContent
    assert by_name["b"].status == "success" and '"b"' in by_name["b"].resultContent


def test_two_idless_responses_pair_fifo_with_two_same_named_calls():
    """FIFO, so a response can never attach to a call that came after it."""
    items = _events_to_tool_activity(
        [
            _event([_part(call=("search", None))], ts=1.0),
            _event([_part(call=("search", None))], ts=2.0),
            _event([_part(response=("search", None, {"n": 1}))], ts=3.0),
            _event([_part(response=("search", None, {"n": 2}))], ts=4.0),
        ]
    )
    assert [i.status for i in items] == ["success", "success"]
    assert '"n": 1' in items[0].resultContent
    assert '"n": 2' in items[1].resultContent


def test_a_genuinely_unanswered_call_is_still_an_error():
    """The fix must not paper over real failures — that would be worse."""
    items = _events_to_tool_activity([_event([_part(call=("never_returned", "c1"))])])
    assert [i.status for i in items] == ["error"]


def test_one_response_for_two_calls_leaves_the_second_errored():
    items = _events_to_tool_activity(
        [
            _event([_part(call=("search", None))]),
            _event([_part(call=("search", None))]),
            _event([_part(response=("search", None))]),
        ]
    )
    assert [i.status for i in items] == ["success", "error"]


def test_idless_response_does_not_leak_across_different_tools():
    items = _events_to_tool_activity(
        [
            _event([_part(call=("tool_a", None))]),
            _event([_part(response=("tool_b", None))]),
        ]
    )
    assert [i.status for i in items] == ["error"]
