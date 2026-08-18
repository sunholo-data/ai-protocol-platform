"""Session reconcile — v6.23.0 B5/F5/F6 Phase 1.

Phase 1 exists to MEASURE, so what these tests pin is the arithmetic: given a
known set of canonical events and a known mirror row, exactly which finding
codes come out. If the reconstruction in `sessions_route` changes, these move —
which is the point, because that reconstruction is the thing under measurement.

The two structural loss paths are asserted directly, because they were found by
reading `_events_to_tool_activity` and are the leading hypotheses for Mark's
"traces not populating correctly":

  * a `function_response` with **no id** is dropped from the response index, so
    its call can never pair and renders as FAILED even though it succeeded
  * a `function_call` with **no id** gets a synthetic key no response can match
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import User, get_current_user

_PLATFORM = User(
    uid="admin-uid",
    email="owner@yourcompany.com",
    domain="yourcompany.com",
    group_tags=frozenset({"aitana-admin"}),
)


def _part(*, text=None, call=None, response=None):
    """An ADK content part. `call`/`response` are (name, id) tuples."""
    fc = SimpleNamespace(name=call[0], id=call[1], args={}) if call else None
    fr = SimpleNamespace(name=response[0], id=response[1], response={"ok": True}) if response else None
    return SimpleNamespace(text=text, function_call=fc, function_response=fr)


def _event(author="model", parts=(), ts=1_700_000_000.0):
    return SimpleNamespace(author=author, timestamp=ts, content=SimpleNamespace(parts=list(parts)))


def _mirror(**kw):
    base = {
        "session_id": "s1",
        "owner_uid": "u1",
        "owner_domain": "acmeenergy.com",
        "skill_id": "one-assistant",
        "turn_count": 2,
        "provisional": False,
        "archived_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _client() -> TestClient:
    from admin.analytics_routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: _PLATFORM
    return TestClient(app, raise_server_exceptions=False)


def _run(mirror, events):
    """Reconcile one session with a stubbed mirror + canonical store."""
    session = None if events is None else SimpleNamespace(events=list(events))
    service = SimpleNamespace(get_session=AsyncMock(return_value=session))
    with (
        patch("admin.analytics_routes.get_session_index", return_value=mirror),
        patch("admin.analytics_routes.get_messages_session_service", return_value=service),
        patch("admin.analytics_routes._resolve_user_directory", return_value={}),
    ):
        res = _client().get("/api/admin/analytics/sessions/s1/reconcile")
    assert res.status_code == 200, res.text
    return res.json()


def _codes(report) -> set[str]:
    return {f["code"] for f in report["findings"]}


def test_healthy_session_reports_ok() -> None:
    events = [
        _event(author="user", parts=[_part(text="hello")]),
        _event(parts=[_part(call=("entsoe_day_ahead_prices", "c1"))]),
        _event(parts=[_part(response=("entsoe_day_ahead_prices", "c1"))]),
        _event(parts=[_part(text="here are the prices")]),
        _event(author="user", parts=[_part(text="thanks")]),
    ]
    rep = _run(_mirror(turn_count=2), events)
    assert _codes(rep) == {"OK"}
    assert rep["raw_tool_calls"] == 1
    assert rep["trace_tools"] == 1
    assert rep["trace_tools_errored"] == 0


def test_response_without_id_is_reported_but_no_longer_breaks_the_trace() -> None:
    """Was THE leading hypothesis for B5; now fixed at the source.

    Before v6.23.0 B5 Phase 2 an id-less `function_response` was dropped, its
    call found no match, and the admin trace rendered a tool that had SUCCEEDED
    as failed. `_events_to_tool_activity` now falls back to pairing by function
    name, so the trace tells the truth.

    The reconcile still reports it — as a WARN, not an error — because the
    fallback is a heuristic that can misattribute between two concurrent calls
    to the same tool, and an operator reading a surprising trace should know it
    was in play.
    """
    events = [
        _event(author="user", parts=[_part(text="run it")]),
        _event(parts=[_part(call=("map_ppa_obligations", "c1"))]),
        _event(parts=[_part(response=("map_ppa_obligations", None))]),  # <- no id
    ]
    rep = _run(_mirror(turn_count=1), events)

    assert "RESPONSE_ID_MISSING" in _codes(rep)
    assert next(f for f in rep["findings"] if f["code"] == "RESPONSE_ID_MISSING")["severity"] == "warn"
    assert rep["responses_missing_id"] == 1
    # The symptom is gone: the call pairs and renders as successful.
    assert rep["trace_tools"] == 1
    assert rep["trace_tools_errored"] == 0
    assert "TOOLS_RENDER_ERRORED" not in _codes(rep)


def test_call_without_id_is_reported() -> None:
    events = [
        _event(parts=[_part(call=("extract_ppa_clauses", None))]),
        _event(parts=[_part(response=("extract_ppa_clauses", "c9"))]),
    ]
    rep = _run(_mirror(turn_count=1), events)
    assert "CALL_ID_MISSING" in _codes(rep)
    assert rep["calls_missing_id"] == 1


def test_delegations_are_not_counted_as_tools() -> None:
    """`transfer_to_agent` / `request_handoff` are delegations, not tool calls.

    Guards the reconcile against reporting a phantom TOOL_CALLS_DROPPED for every
    delegating session — which would bury the real signal.
    """
    events = [
        _event(parts=[_part(call=("transfer_to_agent", "d1"))]),
        _event(parts=[_part(call=("request_handoff", "d2"))]),
        _event(parts=[_part(call=("real_tool", "c1"))]),
        _event(parts=[_part(response=("real_tool", "c1"))]),
    ]
    # transfer_to_agent with no agent_name arg resolves to no target, so the
    # trace legitimately drops it — that is the DELEGATIONS_DROPPED warning.
    rep = _run(_mirror(turn_count=1), events)
    assert rep["raw_tool_calls"] == 1
    assert rep["raw_delegation_calls"] == 2
    assert "TOOL_CALLS_DROPPED" not in _codes(rep)


def test_canonical_missing_with_turns_is_an_error() -> None:
    """The known divergence: mirror kept counting while Vertex appends failed."""
    rep = _run(_mirror(turn_count=121), None)
    assert "CANONICAL_MISSING" in _codes(rep)
    assert next(f for f in rep["findings"] if f["code"] == "CANONICAL_MISSING")["severity"] == "error"
    assert rep["mirror_present"] is True
    assert rep["canonical_present"] is False


def test_canonical_missing_with_zero_turns_is_only_info() -> None:
    """A bootstrapped-but-never-used session is not a defect."""
    rep = _run(_mirror(turn_count=0), None)
    assert next(f for f in rep["findings"] if f["code"] == "CANONICAL_MISSING")["severity"] == "info"


def test_mirror_missing_short_circuits() -> None:
    rep = _run(None, [])
    assert _codes(rep) == {"MIRROR_MISSING"}


def test_blank_owner_domain_is_flagged_as_invisible_to_tenant_admins() -> None:
    events = [_event(author="user", parts=[_part(text="hi")])]
    rep = _run(_mirror(owner_domain="", turn_count=1), events)
    assert "OWNER_DOMAIN_BLANK" in _codes(rep)


def test_turn_count_drift_tolerance_matches_the_writer_s_debounce() -> None:
    """The mirror flushes on turn 1 then every `_TURN_FLUSH_INTERVAL` turns, so
    it can legitimately trail by that much. Tolerating less produces noise: at
    ±1 this flagged 58% of 100 real dev sessions (measured 2026-08-10), which
    buried the findings that mattered. The tolerance is pinned to the writer's
    own constant so the two cannot drift apart."""
    from adk.callbacks import _TURN_FLUSH_INTERVAL

    one_user_event = [_event(author="user", parts=[_part(text="hi")])]
    within = 1 + _TURN_FLUSH_INTERVAL
    beyond = 2 + _TURN_FLUSH_INTERVAL
    assert "TURN_COUNT_DRIFT" not in _codes(_run(_mirror(turn_count=within), one_user_event))
    assert "TURN_COUNT_DRIFT" in _codes(_run(_mirror(turn_count=beyond), one_user_event))


def test_provisional_row_with_turns_is_flagged() -> None:
    events = [_event(author="user", parts=[_part(text="hi")])]
    rep = _run(_mirror(provisional=True, turn_count=3), events)
    assert "PROVISIONAL_STUCK" in _codes(rep)


def test_sweep_tallies_codes_across_sessions() -> None:
    """The sweep's tally IS the Phase 1 deliverable — one session proves nothing."""
    docs = [
        {"__id": "s1", "ownerDomain": "acmeenergy.com", "lastMessageAt": "2026-08-01"},
        {"__id": "s2", "ownerDomain": "acmeenergy.com", "lastMessageAt": "2026-08-02"},
    ]
    broken = [
        _event(parts=[_part(call=("t", "c1"))]),
        _event(parts=[_part(response=("t", None))]),
    ]
    service = SimpleNamespace(get_session=AsyncMock(return_value=SimpleNamespace(events=broken)))
    with (
        patch("admin.analytics_routes.query_documents", return_value=docs),
        patch("admin.analytics_routes.get_session_index", return_value=_mirror(turn_count=1)),
        patch("admin.analytics_routes.get_messages_session_service", return_value=service),
        patch("admin.analytics_routes._resolve_user_directory", return_value={}),
    ):
        res = _client().get("/api/admin/analytics/sessions-reconcile-sweep?limit=10")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scanned"] == 2
    # Both sessions carry the same defect, so the tally shows 2 — the shape that
    # tells you a defect is systemic rather than a one-off.
    assert body["code_counts"]["RESPONSE_ID_MISSING"] == 2


@pytest.mark.parametrize(
    "path", ["/api/admin/analytics/sessions/s1/reconcile", "/api/admin/analytics/sessions-reconcile-sweep"]
)
def test_reconcile_requires_admin(path: str) -> None:
    """A reconcile names sessions and owners, so it is admin-gated like the trace."""
    from admin.analytics_routes import router

    app = FastAPI()
    app.include_router(router)
    plain = User(uid="u", email="alex@acmeenergy.com", domain="acmeenergy.com", group_tags=frozenset())
    app.dependency_overrides[get_current_user] = lambda: plain
    res = TestClient(app, raise_server_exceptions=False).get(path)
    assert res.status_code == 403


# --- mark-transcript-lost (Phase 2) -----------------------------------------


def _mark(docs, mirror, events, *, dry_run=True, already=False):
    session = None if events is None else SimpleNamespace(events=list(events))
    service = SimpleNamespace(get_session=AsyncMock(return_value=session))
    idx = mirror
    if idx is not None and already:
        idx = SimpleNamespace(**{**mirror.__dict__, "transcript_lost": True})
    writes: list[tuple] = []
    with (
        patch("admin.analytics_routes.query_documents", return_value=docs),
        patch("admin.analytics_routes.get_session_index", return_value=idx),
        patch("admin.analytics_routes.get_messages_session_service", return_value=service),
        patch("admin.analytics_routes._resolve_user_directory", return_value={}),
        patch("admin.analytics_routes.update_session_fields", side_effect=lambda *a: writes.append(a)),
    ):
        res = _client().post(f"/api/admin/analytics/sessions-mark-transcript-lost?dry_run={str(dry_run).lower()}")
    assert res.status_code == 200, res.text
    return res.json(), writes


_DOCS = [{"__id": "s1", "ownerDomain": "acmeenergy.com", "lastMessageAt": "2026-07-27"}]


def test_mark_lost_flags_a_session_with_turns_and_no_transcript() -> None:
    body, writes = _mark(_DOCS, _mirror(turn_count=131), None, dry_run=False)
    assert body["marked"] == ["s1"]
    assert writes == [("s1", {"transcriptLost": True})]


def test_mark_lost_is_dry_by_default() -> None:
    """Reports without writing, so an accidental invocation is harmless."""
    body, writes = _mark(_DOCS, _mirror(turn_count=131), None, dry_run=True)
    assert body["marked"] == ["s1"]
    assert writes == []


def test_mark_lost_ignores_a_session_that_merely_has_no_turns() -> None:
    """A bootstrapped-but-unused session has no transcript to lose."""
    body, writes = _mark(_DOCS, _mirror(turn_count=0), None, dry_run=False)
    assert body["marked"] == []
    assert writes == []


def test_mark_lost_ignores_a_healthy_session() -> None:
    events = [_event(author="user", parts=[_part(text="hi")])]
    body, writes = _mark(_DOCS, _mirror(turn_count=1), events, dry_run=False)
    assert body["marked"] == []
    assert writes == []


def test_mark_lost_is_idempotent() -> None:
    body, writes = _mark(_DOCS, _mirror(turn_count=131), None, dry_run=False, already=True)
    assert body["marked"] == []
    assert body["already_marked"] == 1
    assert writes == []


def test_mark_lost_requires_admin() -> None:
    from admin.analytics_routes import router

    app = FastAPI()
    app.include_router(router)
    plain = User(uid="u", email="l@acmeenergy.com", domain="acmeenergy.com", group_tags=frozenset())
    app.dependency_overrides[get_current_user] = lambda: plain
    res = TestClient(app, raise_server_exceptions=False).post("/api/admin/analytics/sessions-mark-transcript-lost")
    assert res.status_code == 403
