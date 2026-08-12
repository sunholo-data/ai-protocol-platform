"""Integration test for POST /api/skill/{skill_id}/stream (AGENT-FACTORY M4).

Exercises the full skill-streaming path except the LLM call itself:

  - `get_current_user` dep-override installs a synthetic User + AccessContext.
  - `skill_config.get_skill` is patched to return a seeded SkillConfig.
  - `ADKAgent.run` is patched to yield a deterministic AG-UI event
    sequence (RUN_STARTED → TEXT_MESSAGE_START/CONTENT/END → RUN_FINISHED).

This keeps the test in `make test-fast` (no Vertex call, no GCP creds),
while still verifying: auth enforcement, 404 for missing/invisible skills,
event sequencing through `process_skill_request` + `stream_agui_events`,
and SSE framing in the endpoint handler.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from ag_ui.core import (
    EventType,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from fastapi import Request
from fastapi.testclient import TestClient

from auth import User, build_access_context, get_current_user
from db.models import SkillConfig, SkillMetadata
from db.models.chat_session import ChatSessionIndex

# --- Fixtures ---


def _make_session_index(owner_uid: str = "caller-uid", access_type: str = "public") -> ChatSessionIndex:
    now = datetime.now(UTC)
    return ChatSessionIndex(
        session_id="test-session-id",
        skill_id="test-skill-id",
        owner_uid=owner_uid,
        access_control={"type": access_type},
        first_message_at=now,
        last_message_at=now,
    )


def _make_skill(
    skill_id: str = "test-skill-id",
    access_type: str = "public",
    owner_id: str = "someone-else",
) -> SkillConfig:
    return SkillConfig(
        name="test-skill",
        description="Under test.",
        instructions="Be helpful.",
        skillId=skill_id,
        ownerId=owner_id,
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl={"type": access_type},
    )


def _make_user() -> User:
    return User(uid="caller-uid", email="caller@yourcompany.com", domain="yourcompany.com")


async def _fake_event_stream(input_data) -> AsyncGenerator:
    """Mock replacement for ADKAgent.run() — yields a canonical AG-UI sequence."""
    thread_id = input_data.thread_id
    run_id = input_data.run_id
    yield RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id)
    yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id="m1", role="assistant")
    yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="Hello ")
    yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="world.")
    yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id="m1")
    yield RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)


async def _fake_tool_call_stream(input_data) -> AsyncGenerator:
    """Mock ADKAgent.run() — yields a full TOOL_CALL_* sequence.

    Verifies the SSE endpoint preserves tool-call events end-to-end.
    Shape mirrors what ag-ui-adk produces when an ADK FunctionTool fires.
    """
    thread_id = input_data.thread_id
    run_id = input_data.run_id
    yield RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id)
    yield ToolCallStartEvent(
        type=EventType.TOOL_CALL_START,
        tool_call_id="tc1",
        tool_call_name="get_current_time",
        parent_message_id="m1",
    )
    yield ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id="tc1", delta="{}")
    yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id="tc1")
    yield ToolCallResultEvent(
        type=EventType.TOOL_CALL_RESULT,
        message_id="m1",
        tool_call_id="tc1",
        content="2026-04-21T18:42:23Z",
        role="tool",
    )
    yield RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id)


@pytest.fixture()
def app():
    # Import late so `setup_telemetry()` side-effects (OTEL exporters) don't
    # fire at module import time during test collection.
    import fast_api_app as module

    return module.app


@pytest.fixture()
def client(app):
    async def _override(request: Request) -> User:
        user = _make_user()
        request.state.access = build_access_context(user)
        return user

    app.dependency_overrides[get_current_user] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


# --- Tests ---


def test_stream_skill_passes_resumed_session_flag_into_state(client):
    """When the frontend sets forwardedProps.resumed_session the flag
    must reach the agent's initial state so make_document_injector can
    eager-inject docs only on resume.
    """
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "message": "hi",
                "forwardedProps": {
                    "document_ids": ["docA"],
                    "resumed_session": True,
                },
            },
        )
    assert resp.status_code == 200, resp.text
    assert captured["state"].get("app:resumed_session") is True


def test_stream_skill_omits_resumed_flag_for_fresh_chats(client):
    """Fresh chats (no resumed_session flag) must not have
    app:resumed_session in state — keeps eager injection scoped to the
    user's explicit thread-click intent."""
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hi", "forwardedProps": {"document_ids": ["docA"]}},
        )
    assert resp.status_code == 200
    assert "app:resumed_session" not in captured["state"]


def test_stream_skill_passes_all_document_ids_through_to_agent(client):
    """Multi-doc selection: every document_id from forwardedProps reaches agent state.

    Regression for the "compare these documents" bug where only the active tab
    was sent. The full ``document_ids`` list must arrive on the AG-UI
    ``RunAgentInput.state`` so ``make_document_loader`` can save one artifact
    per doc.
    """
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "message": "compare these",
                "forwardedProps": {"document_ids": ["docA", "docB", "docC"]},
            },
        )
    assert resp.status_code == 200, resp.text
    assert captured["state"].get("document_ids") == ["docA", "docB", "docC"]


def test_stream_skill_no_documents_when_list_omitted(client):
    """Without documentIds in the payload, agent state must not have document_ids."""
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},
        )
    assert resp.status_code == 200
    assert "document_ids" not in captured["state"]


def test_stream_skill_forwardedprops_wins_over_stale_state_document_ids(client):
    """multi-doc-context-fix 1.22 / Phase 2 regression test.

    Mechanism: AG-UI HttpAgent mirrors backend STATE_SNAPSHOT events into
    ``agent.state`` and round-trips that state on every subsequent
    ``runAgent`` call. After turn 2 ships ``state.document_ids = [doc1]``,
    turn 3's body has BOTH:

      - ``state.document_ids = [doc1]``                 (stale, from prior turn)
      - ``forwardedProps.document_ids = [doc1, doc2]``  (fresh, what the
                                                         chat page derived
                                                         from ticked tabs)

    The fresh per-turn signal MUST win — otherwise the user adds a doc
    to turn 3 and the loader keeps reading turn 2's list. Pre-fix
    ``_extract_document_ids`` checked state before forwardedProps and
    returned ``[doc1]``; post-fix forwardedProps wins and we get
    ``[doc1, doc2]``.

    Pinpointed by ./dev-logs/backend.log on 2026-04-28: the chat page
    sent 3 ids in forwardedProps (verified via console.warn diagnostic)
    while the backend loader logged ``document_ids=[doc1]``.
    """
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "message": "how about this doc?",
                # AG-UI HttpAgent's accumulated state from prior STATE_SNAPSHOT
                # events — out of date by definition (one turn behind the
                # chat page).
                "state": {"document_ids": ["doc-stale-from-turn-2"]},
                # Fresh per-turn signal from the chat page's
                # `computeIncludedDocIds(openTabs)`.
                "forwardedProps": {
                    "document_ids": ["doc-stale-from-turn-2", "doc-fresh-this-turn"],
                },
            },
        )
    assert resp.status_code == 200, resp.text
    assert captured["state"].get("document_ids") == [
        "doc-stale-from-turn-2",
        "doc-fresh-this-turn",
    ], (
        "forwardedProps.document_ids must win over state.document_ids. "
        f"Got {captured['state'].get('document_ids')!r}. The state field "
        "is round-tripped from the prior turn's STATE_SNAPSHOT and is "
        "always one turn behind."
    )


def test_stream_skill_falls_back_to_state_document_ids_when_no_forwardedprops(client):
    """Legacy fallback locks: if a client somehow sends only
    ``state.document_ids`` and no forwardedProps (e.g. a future channel
    adapter), the loader still gets the list. Locks the floor so the
    Phase 2 priority reorder doesn't accidentally drop this path.
    """
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "message": "hi",
                "state": {"document_ids": ["legacy-only"]},
                # No forwardedProps.document_ids
            },
        )
    assert resp.status_code == 200
    assert captured["state"].get("document_ids") == ["legacy-only"]


def test_stream_skill_accepts_document_ids_simple_format(client):
    """Simple (CLI / tests) wire format: top-level ``documentIds`` field."""
    captured: dict[str, object] = {}

    async def _capturing_stream(input_data):
        captured["state"] = dict(input_data.state or {})
        async for evt in _fake_event_stream(input_data):
            yield evt

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_capturing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hi", "documentIds": ["docX", "docY"]},
        )
    assert resp.status_code == 200
    assert captured["state"].get("document_ids") == ["docX", "docY"]


def test_stream_skill_returns_sse_event_sequence(client):
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    # Parse SSE frames — each "data: <json>\n\n" is one event.
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    assert len(frames) >= 4  # at least RUN_STARTED + TEXT_MESSAGE_* + RUN_FINISHED

    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    event_types = [e.get("type") for e in events]
    assert "RUN_STARTED" in event_types
    assert "TEXT_MESSAGE_START" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert "TEXT_MESSAGE_END" in event_types
    assert "RUN_FINISHED" in event_types


def test_stream_skill_surfaces_tool_call_events(client):
    """PROTOCOLS-1A5 M1: tool-call events flow through the SSE stream.

    Asserts TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END / TOOL_CALL_RESULT
    frames reach the client unmodified — this is the wire-level contract
    frontends (CopilotKit, AG-UI renderers) rely on to render tool steps.
    """
    skill = _make_skill(skill_id="tool-skill", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_tool_call_stream),
    ):
        resp = client.post(
            "/api/skill/tool-skill/stream",
            json={"message": "what time is it?"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    event_types = [e.get("type") for e in events]
    assert "TOOL_CALL_START" in event_types, f"missing TOOL_CALL_START in {event_types}"
    assert "TOOL_CALL_ARGS" in event_types, f"missing TOOL_CALL_ARGS in {event_types}"
    assert "TOOL_CALL_END" in event_types, f"missing TOOL_CALL_END in {event_types}"
    assert "TOOL_CALL_RESULT" in event_types, f"missing TOOL_CALL_RESULT in {event_types}"


def test_stream_skill_translates_vertex_401_to_run_error(client):
    """When VertexAiSessionService raises 401 CREDENTIALS_MISSING (drifted
    ADC quota_project), the SSE stream must yield exactly one AG-UI RUN_ERROR
    event with an actionable fix message — NOT a broken/empty stream."""
    from google.genai.errors import ClientError

    response_json = {
        "error": {
            "code": 401,
            "status": "UNAUTHENTICATED",
            "message": "API keys are not supported by this API.",
            "details": [{"reason": "CREDENTIALS_MISSING"}],
        }
    }

    async def _failing_stream(input_data):
        # Match the real failure mode: error raised while iterating, after
        # the generator has been entered but before the first yield.
        if input_data is not None:
            raise ClientError(401, response_json, response=None)
        yield

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_failing_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},
        )
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    error_events = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert len(error_events) == 1, f"expected 1 RUN_ERROR, got events: {events}"
    assert error_events[0]["code"] == "VERTEX_AUTH_FAILED"
    assert "gcloud auth application-default set-quota-project" in error_events[0]["message"]


def test_stream_skill_translates_budget_exceeded_to_typed_run_error(client):
    """Sprint 2.12 M3: a BudgetExceededError raised from the before_model
    callback must surface as exactly one AG-UI RUN_ERROR with
    code='BUDGET_EXCEEDED', the typed message, and the retry_after_seconds
    countdown (passthrough field — RunErrorEventSchema allows extras).
    """
    from budget.enforcer import BudgetDecision, BudgetExceededError

    decision = BudgetDecision(
        action="block",
        remaining_usd=0.0,
        period_end="2026-06-01T00:00:00Z",
        message="Cohort PHYS-7K2N is over its monthly budget.",
        retry_after_seconds=3600,
    )

    async def _budget_blocked_stream(input_data):
        if input_data is not None:
            raise BudgetExceededError(decision)
        yield

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_budget_blocked_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},
        )
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    error_events = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert len(error_events) == 1, f"expected 1 RUN_ERROR, got events: {events}"
    err = error_events[0]
    assert err["code"] == "BUDGET_EXCEEDED"
    assert err["message"] == "Cohort PHYS-7K2N is over its monthly budget."
    assert err.get("retry_after_seconds") == 3600


def test_refresh_finds_session_index_after_first_stream_event(client):
    """B1 (chat-history-fixes): the Firestore session-index row must be
    written *synchronously* by ``process_skill_request`` before the first
    SSE event is yielded — not later in an ADK after-agent callback.

    Pre-fix: index write happened in the before_agent_callback during the
    agent run, so a user reload between POST /stream returning and the
    callback completing would 404 on GET /api/sessions/{id}.

    The mocked ``ADKAgent.run`` here does not invoke ADK callbacks (it
    bypasses ADK entirely), so any code path that relies on those
    callbacks would never call ``create_session_index``. Asserting
    ``create_session_index`` is invoked at all proves the synchronous
    prelude landed.
    """
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    written: list[str] = []

    def _fake_create(*, session_id, **_kwargs):
        written.append(session_id)
        return _make_session_index(owner_uid="caller-uid")

    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
        patch("db.chat_sessions.create_session_index", side_effect=_fake_create),
        patch("db.chat_sessions.get_session_index", return_value=None),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "threadId": "thread-fresh-b1",
                "runId": "run-b1",
                "messages": [{"id": "msg-1", "role": "user", "content": "hi"}],
                "state": {},
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )
    assert resp.status_code == 200, resp.text
    assert "thread-fresh-b1" in written, (
        "B1: process_skill_request must call create_session_index synchronously "
        "with the threadId before yielding the first SSE event. Currently the "
        "write only happens in the ADK before_agent_callback during the agent run, "
        "leaving a window where GET /api/sessions/{id} 404s if the user refreshes."
    )


def test_session_index_document_ids_grow_when_doc_added_after_empty_first_turn(client):
    """Regression for the "55 turns / lumped-into-one-thread / not visible
    in the doc panel" bug observed in dev on 2026-04-28.

    Reproduction:

      Turn 1 — user lands on /chat/<skill> with NO ?session= and types a
        message before any doc tab is open (or after a reload to a
        404'd session_id where useSessionDocuments cleared openTabs).
        Backend gets ``forwardedProps.document_ids = None`` →
        ``_ensure_session_index`` writes a row with ``documentIds=[]``.

      Turn 2 — same threadId, the user now has a doc tab open.
        Backend gets ``forwardedProps.document_ids = ['docA']``. But
        ``_ensure_session_index`` short-circuits because the row already
        exists, so the wire-time signal "this turn attaches docA" is
        only persisted via the async ``make_document_loader`` →
        ``add_session_documents`` path. If the loader bails for any
        reason (artifact load fails, ADK callback skipped, mocked
        away in tests), the Firestore row stays at ``documentIds=[]``
        forever.

    Symptom users see:
      * ``list_sessions_for_skill`` (used by the sidebar "Sessions"
        panel) returns the row → user sees a session entry, possibly
        with a high turn_count.
      * ``list_sessions_for_document(docA)`` (used by the per-doc
        "Conversations" panel) uses Firestore ``array_contains`` and
        skips this row because ``documentIds=[]`` → user sees "No
        conversations yet" even though they're clearly chatting with
        docA.

    Expected fix: ``_ensure_session_index`` must ArrayUnion the current
    turn's ``document_ids`` into the session row on **every** turn,
    not just on first creation. This is a synchronous, wire-time
    guarantee independent of the async loader's success.
    """
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    state: dict[str, object] = {"row": None}
    add_calls: list[tuple[str, list[str]]] = []

    def _fake_get(session_id):
        return state["row"]

    def _fake_create(*, session_id, document_ids, **_kwargs):
        idx = _make_session_index(owner_uid="caller-uid")
        # Mutate to reflect what was passed in so subsequent gets reflect reality.
        idx.document_ids = list(document_ids or [])
        state["row"] = idx
        return idx

    def _fake_add_docs(session_id, doc_ids):
        add_calls.append((session_id, list(doc_ids)))
        if state["row"] is not None and doc_ids:
            existing_ids = list(state["row"].document_ids)
            for d in doc_ids:
                if d not in existing_ids:
                    existing_ids.append(d)
            state["row"].document_ids = existing_ids

    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
        patch("db.chat_sessions.create_session_index", side_effect=_fake_create),
        patch("db.chat_sessions.get_session_index", side_effect=_fake_get),
        patch("db.chat_sessions.add_session_documents", side_effect=_fake_add_docs),
    ):
        # Turn 1: no docs attached.
        r1 = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "threadId": "thread-stranded-doc",
                "runId": "run-1",
                "messages": [{"id": "m1", "role": "user", "content": "hi"}],
                "state": {},
                "forwardedProps": {},
            },
        )
        assert r1.status_code == 200, r1.text
        assert state["row"] is not None
        assert state["row"].document_ids == []

        # Turn 2: same threadId, now with docA in forwardedProps. The
        # ADK loader is mocked away (ag_ui_adk.ADKAgent.run is a
        # canned event stream that never invokes callbacks), so the
        # only chance to persist docA into Firestore is the synchronous
        # _ensure_session_index path.
        r2 = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "threadId": "thread-stranded-doc",
                "runId": "run-2",
                "messages": [{"id": "m2", "role": "user", "content": "summarise this"}],
                "state": {},
                "forwardedProps": {"document_ids": ["docA"]},
            },
        )
        assert r2.status_code == 200, r2.text

    assert state["row"].document_ids == ["docA"], (
        "stranded-session bug: turn 2 attached docA but the Firestore row "
        f"still shows documentIds={state['row'].document_ids}. "
        f"add_session_documents calls: {add_calls}. "
        "list_sessions_for_document('docA') uses array_contains and will "
        "never surface this session in the per-doc Conversations panel. "
        "Fix: _ensure_session_index must ArrayUnion the turn's docs even "
        "when the row already exists."
    )


def test_session_index_skill_follows_a_switch(client):
    """v6.10.0 unified-adk-handoff: the confirm→switch re-issues the turn on the
    specialist over the SAME thread. The row was created on the door, so
    _ensure_session_index must re-point skill_id to the specialist (and append
    skillHistory) — otherwise the surface-action binding gate (URL skill ==
    session skill) 403s the specialist's own form (the 2026-07-15 test failure)."""
    door = _make_skill(skill_id="door-skill", access_type="public")
    specialist = _make_skill(skill_id="specialist-skill", access_type="public")
    row = _make_session_index(owner_uid="caller-uid")
    row.skill_id = "door-skill"
    skill_updates: list[tuple[str, str, str | None]] = []

    def _fake_set_skill(session_id, new_skill_id, prev_skill_id=None):
        skill_updates.append((session_id, new_skill_id, prev_skill_id))
        row.skill_id = new_skill_id

    with (
        patch(
            "skills.skill_processor.get_skill",
            side_effect=lambda sid: {"door-skill": door, "specialist-skill": specialist}.get(sid),
        ),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
        patch("db.chat_sessions.get_session_index", side_effect=lambda _sid: row),
        patch("db.chat_sessions.set_session_skill", side_effect=_fake_set_skill),
        patch("db.chat_sessions.add_session_documents"),
    ):
        # Turn re-issued on the SPECIALIST over the door's existing thread.
        resp = client.post(
            "/api/skill/specialist-skill/stream",
            json={
                "threadId": "thread-switch",
                "runId": "run-x",
                "messages": [{"id": "m1", "role": "user", "content": "analyse this"}],
                "state": {},
                "forwardedProps": {},
            },
        )
        assert resp.status_code == 200, resp.text

    assert skill_updates == [("thread-switch", "specialist-skill", "door-skill")], (
        f"session index did not follow the switch: {skill_updates}"
    )
    assert row.skill_id == "specialist-skill"


def test_session_index_no_skill_update_when_same_skill(client):
    """A normal same-skill turn on an existing thread must NOT rewrite skill_id."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    row = _make_session_index(owner_uid="caller-uid")
    row.skill_id = "test-skill-id"
    calls: list = []
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
        patch("db.chat_sessions.get_session_index", side_effect=lambda _sid: row),
        patch("db.chat_sessions.set_session_skill", side_effect=lambda *a, **k: calls.append(a)),
        patch("db.chat_sessions.add_session_documents"),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={
                "threadId": "thread-same",
                "runId": "run-y",
                "messages": [{"id": "m1", "role": "user", "content": "hi again"}],
                "state": {},
                "forwardedProps": {},
            },
        )
        assert resp.status_code == 200, resp.text
    assert calls == [], f"skill_id was rewritten on a same-skill turn: {calls}"


def test_stream_skill_returns_404_when_skill_missing(client):
    with patch("skills.skill_processor.get_skill", return_value=None):
        resp = client.post("/api/skill/missing-skill/stream", json={"message": "hi"})
    assert resp.status_code == 404, f"got {resp.status_code}: {resp.text[:500]}"


def test_stream_skill_returns_404_when_skill_not_visible(client):
    # Private skill owned by a different user — caller shouldn't even learn
    # of its existence, so we expect 404 (not 403).
    skill = _make_skill(skill_id="private-id", access_type="private", owner_id="other-uid")
    with patch("skills.skill_processor.get_skill", return_value=skill):
        resp = client.post("/api/skill/private-id/stream", json={"message": "hi"})
    assert resp.status_code == 404


def test_stream_skill_requires_authentication(app):
    # No dep override — real `get_current_user` runs and rejects unauth.
    client = TestClient(app)
    resp = client.post("/api/skill/whatever/stream", json={"message": "hi"})
    assert resp.status_code == 401


def test_stream_skill_no_session_meta_for_fresh_chat(client):
    """Fresh chat (no sessionId) must not emit session_meta — it is not a valid
    AG-UI event type and causes the frontend Zod parser to throw."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},  # no sessionId
        )
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    types = [e.get("type") for e in events]
    assert "session_meta" not in types, f"session_meta leaked into fresh-chat stream: {types}"
    # RUN_STARTED MUST be the very first frame — the @ag-ui/client state machine
    # rejects any event before it ("First event must be 'RUN_STARTED'"). This
    # regressed once when MODEL_RESOLVED was flushed pre-agent; adk/agui.py now
    # buffers pre-agent events and flushes them AFTER RUN_STARTED.
    assert types[0] == "RUN_STARTED", f"first frame must be RUN_STARTED: {types}"
    # Any MODEL_RESOLVED custom event must come AFTER RUN_STARTED, never at idx 0.
    custom_names = [e.get("name") for e in events if e.get("type") == "CUSTOM"]
    if "MODEL_RESOLVED" in custom_names:
        assert types.index("RUN_STARTED") == 0


_VALID_AGUI_TYPES = {
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TEXT_MESSAGE_CHUNK",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_CHUNK",
    "TOOL_CALL_RESULT",
    "STATE_SNAPSHOT",
    "STATE_DELTA",
    "MESSAGES_SNAPSHOT",
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "STEP_STARTED",
    "STEP_FINISHED",
    "CUSTOM",
    "RAW",
}


def test_stream_skill_emits_only_valid_agui_types_for_fresh_chat(client):
    """All SSE frames for a fresh chat must carry a recognised AG-UI event type.
    Unknown types (e.g. session_meta) crash the frontend Zod discriminated union."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello"},
        )
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    unknown = [e.get("type") for e in events if e.get("type") not in _VALID_AGUI_TYPES]
    assert unknown == [], f"Unknown AG-UI event types in stream: {unknown}"


def test_stream_skill_owner_resume_emits_not_read_only(client):
    """Owner resuming their own session receives isReadOnly:false in first SSE frame."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    session = _make_session_index(owner_uid="caller-uid")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("db.chat_sessions.get_session_index", return_value=session),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello", "sessionId": "test-session-id"},
        )
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    first = json.loads(frames[0][len("data:") :].strip())
    assert first == {"type": "session_meta", "isReadOnly": False}


def test_stream_skill_non_owner_resume_emits_read_only(client):
    """Non-owner with access to a public session receives isReadOnly:true in first SSE frame."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    # Session belongs to someone else but is public — caller can view, not own
    session = _make_session_index(owner_uid="other-user", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("db.chat_sessions.get_session_index", return_value=session),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post(
            "/api/skill/test-skill-id/stream",
            json={"message": "hello", "sessionId": "test-session-id"},
        )
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    first = json.loads(frames[0][len("data:") :].strip())
    assert first == {"type": "session_meta", "isReadOnly": True}


# --- AG-UI wire format tests (HttpAgent protocol boundary) ---
# These tests send the standard AG-UI RunAgentInput body that @ag-ui/client
# HttpAgent produces — NOT the simple {message: str} custom format.
# They exist to catch the protocol boundary mismatch that caused the first
# end-to-end failure: HttpAgent sends {messages:[...], threadId} but the
# endpoint only read {message:str}, so body.message was always "" → RUN_ERROR.


def test_stream_skill_agui_wire_format_extracts_user_message(client):
    """HttpAgent wire format: message extracted from messages array, not message field."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    agui_body = {
        "threadId": "thread-abc123",
        "runId": "run-xyz",
        "messages": [
            {"id": "msg-1", "role": "user", "content": "hello from HttpAgent"},
        ],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/test-skill-id/stream", json=agui_body)
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    types = [e.get("type") for e in events]
    # Must get a real AG-UI event sequence, not an error
    assert "RUN_STARTED" in types, f"Expected RUN_STARTED, got: {types}"
    assert "RUN_FINISHED" in types


def test_stream_skill_agui_wire_format_no_session_meta_for_fresh_thread(client):
    """HttpAgent always sends threadId — a fresh thread must not emit session_meta."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    agui_body = {
        "threadId": "thread-fresh-999",
        "runId": "run-1",
        "messages": [{"id": "msg-1", "role": "user", "content": "hi"}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/test-skill-id/stream", json=agui_body)
    assert resp.status_code == 200
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    types = [e.get("type") for e in events]
    assert "session_meta" not in types, (
        "session_meta must not appear for a fresh HttpAgent thread — it breaks the frontend Zod discriminated union"
    )
    # RUN_STARTED must be the FIRST frame — @ag-ui/client rejects anything before
    # it. adk/agui.py buffers pre-agent events (MODEL_RESOLVED) until after it.
    assert types[0] == "RUN_STARTED", f"first frame must be RUN_STARTED: {types}"


def test_stream_skill_agui_wire_format_emits_only_valid_types(client):
    """All SSE frames from an HttpAgent request must be valid AG-UI event types."""
    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    agui_body = {
        "threadId": "thread-valid-check",
        "runId": "run-2",
        "messages": [{"id": "msg-1", "role": "user", "content": "test"}],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_fake_event_stream),
    ):
        resp = client.post("/api/skill/test-skill-id/stream", json=agui_body)
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line[len("data:") :].strip()) for line in frames]
    unknown = [e.get("type") for e in events if e.get("type") not in _VALID_AGUI_TYPES]
    assert unknown == [], f"Unknown AG-UI event types from HttpAgent request: {unknown}"


# --- MODEL-RELIABILITY M2: LiteLLM provider errors become typed RUN_ERRORs ---
# Pre-M2, only google.genai ClientError was translated; a Claude/OpenAI
# failure via LiteLLM propagated uncaught, the SSE stream just died, and the
# user saw a generic network error when a frontend watchdog eventually fired.
# These pin the new contract: known provider errors → exactly one typed
# RUN_ERROR; unknown exceptions still crash loudly (a code bug must never
# masquerade as a model outage).


def _stream_events(resp):
    frames = [line for line in resp.text.splitlines() if line.startswith("data:")]
    return [json.loads(line[len("data:") :].strip()) for line in frames]


def test_stream_skill_translates_litellm_rate_limit_to_typed_run_error(client):
    """Anthropic 429 via LiteLLM → RUN_ERROR code=MODEL_RATE_LIMITED with
    retry_after_seconds passthrough (was: silent stream death)."""
    import litellm

    async def _failing_stream(input_data):
        if input_data is not None:
            raise litellm.RateLimitError(
                "rate limited. retryDelay: 7s",
                llm_provider="anthropic",
                model="claude-sonnet-4-6",
            )
        yield

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_failing_stream),
    ):
        resp = client.post("/api/skill/test-skill-id/stream", json={"message": "hello"})

    assert resp.status_code == 200
    error_events = [e for e in _stream_events(resp) if e.get("type") == "RUN_ERROR"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "MODEL_RATE_LIMITED"
    assert error_events[0]["retry_after_seconds"] == 7.0


def test_stream_skill_translates_litellm_529_shape_to_model_unavailable(client):
    """Anthropic 529 overloaded arrives as litellm.InternalServerError
    (recorded litellm 1.82.6 mapping) → MODEL_UNAVAILABLE."""
    import litellm

    async def _failing_stream(input_data):
        if input_data is not None:
            raise litellm.InternalServerError(
                "Overloaded (529 overloaded_error)",
                llm_provider="anthropic",
                model="claude-opus-4-7",
            )
        yield

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_failing_stream),
    ):
        resp = client.post("/api/skill/test-skill-id/stream", json={"message": "hello"})

    error_events = [e for e in _stream_events(resp) if e.get("type") == "RUN_ERROR"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "MODEL_UNAVAILABLE"


def test_stream_skill_unknown_exception_still_raises(client):
    """A non-provider exception (i.e. our bug) must NOT be converted into a
    polite model-outage banner — it propagates so it pages like a bug."""
    import pytest as _pytest

    async def _crashing_stream(input_data):
        if input_data is not None:
            raise RuntimeError("programming error, not a provider")
        yield

    skill = _make_skill(skill_id="test-skill-id", access_type="public")
    with (
        patch("skills.skill_processor.get_skill", return_value=skill),
        patch("ag_ui_adk.ADKAgent.run", side_effect=_crashing_stream),
        _pytest.raises(RuntimeError, match="programming error"),
    ):
        client.post("/api/skill/test-skill-id/stream", json={"message": "hello"})
