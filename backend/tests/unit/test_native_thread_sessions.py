"""Large histories must not be enumerated to start/resume a native-ID chat."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.sessions import Session, VertexAiSessionService

from adk.session_memo import ReadMemoSessionService
from adk.thread_sessions import NativeThreadSessionManager, uses_vertex_sessions
from tests.support.session_doubles import OwnershipEnforcingSessionService


def manager(service):
    return NativeThreadSessionManager(
        session_service=service,
        use_thread_id_as_session_id=True,
        delete_session_on_cleanup=False,
        session_timeout_seconds=86400,
    )


@pytest.mark.asyncio
async def test_new_and_existing_threads_never_load_other_conversations():
    service = OwnershipEnforcingSessionService()
    service.list_sessions = AsyncMock(side_effect=AssertionError("Bulk history read on chat path"))
    m = manager(service)
    try:
        for n in range(700):
            await service.create_session(app_name="app", user_id="alice", session_id=f"old-{n}")
        assert await m.resolve_existing_session("new", "app", "alice") is None
        first, sid = await m.get_or_create_session("new", "app", "alice", initial_state={"marker": "preserved"})
        resumed = await manager(service).resolve_existing_session(sid, "app", "alice")
        assert resumed.id == first.id == "new"
        assert resumed.state["marker"] == "preserved"
        service.list_sessions.assert_not_awaited()
    finally:
        await m.stop_cleanup_task()


@pytest.mark.asyncio
async def test_bootstrapped_session_without_agui_stamps_is_resumed():
    service = OwnershipEnforcingSessionService()
    await service.create_session(app_name="app", user_id="alice", session_id="old", state={"keep": 7})
    found = await manager(service).resolve_existing_session("old", "app", "alice")
    assert found.id == "old" and found.state["keep"] == 7


@pytest.mark.asyncio
async def test_foreign_owner_never_becomes_a_new_session():
    service = OwnershipEnforcingSessionService()
    await service.create_session(app_name="app", user_id="bob", session_id="taken")
    service.create_session = AsyncMock(wraps=service.create_session)
    with pytest.raises(ValueError, match="does not belong"):
        await manager(service).get_or_create_session("taken", "app", "alice")
    service.create_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [{"_ag_ui_thread_id": "other"}, {"_ag_ui_app_name": "other"}])
async def test_engine_wide_app_or_thread_collision_is_rejected(stamp):
    service = MagicMock()
    service.get_session = AsyncMock(return_value=Session(id="taken", app_name="app", user_id="alice", state=stamp))
    service.create_session = AsyncMock()
    with pytest.raises(ValueError, match="does not belong"):
        await manager(service).get_or_create_session("taken", "app", "alice")
    service.create_session.assert_not_awaited()
    service.list_sessions.assert_not_called()


@pytest.mark.asyncio
async def test_transient_and_invalid_reads_never_create_replacement_history():
    for error in (TimeoutError("storage unavailable"), ValueError("invalid session ID")):
        service = MagicMock()
        service.get_session = AsyncMock(side_effect=error)
        service.create_session = AsyncMock()
        with pytest.raises(type(error)):
            await manager(service).get_or_create_session("thread", "app", "alice")
        service.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_create_reuses_owned_winner_without_scan():
    service = OwnershipEnforcingSessionService()
    original_create = service.create_session

    async def racing_create(**kwargs):
        await original_create(**kwargs)
        raise RuntimeError("already exists")

    service.create_session = racing_create
    service.list_sessions = AsyncMock(side_effect=AssertionError("unbounded race recovery"))
    m = manager(service)
    try:
        session, sid = await m.get_or_create_session("raced", "app", "alice")
        assert sid == session.id == "raced"
        service.list_sessions.assert_not_awaited()
    finally:
        await m.stop_cleanup_task()


def test_memo_wrapper_does_not_hide_vertex():
    assert uses_vertex_sessions(ReadMemoSessionService(MagicMock(spec=VertexAiSessionService)))
    assert not uses_vertex_sessions(OwnershipEnforcingSessionService())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thread_id", ["", "../other", "a/b", "a%2Fb", "has space", "projects/p/locations/l/reasoningEngines/e/sessions/s"]
)
async def test_resource_names_and_invalid_ids_cannot_alias_native_threads(thread_id):
    service = MagicMock()
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    with pytest.raises(ValueError, match="Invalid thread ID"):
        await manager(service).get_or_create_session(thread_id, "app", "alice")
    service.get_session.assert_not_awaited()
    service.create_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_request_manager_cleanup_task_is_stopped_after_stream(cancel):
    from ag_ui.core import RunFinishedEvent, RunStartedEvent

    from adk.agui import stream_agui_events

    service = OwnershipEnforcingSessionService()
    m = manager(service)

    class Agent:
        _session_manager = m

        async def run(self, _):
            await m.get_or_create_session("thread", "app", "alice")
            yield RunStartedEvent(thread_id="thread", run_id="run")
            yield RunFinishedEvent(thread_id="thread", run_id="run")

    tracker = MagicMock()
    tracker.drain_stage_events.return_value = []
    with patch("observability.timing.get_current_tracker", return_value=tracker):
        stream = stream_agui_events(Agent(), MagicMock(), heartbeat_seconds=0)
        if cancel:
            await anext(stream)
            await stream.aclose()
        else:
            _ = [event async for event in stream]
    assert m._cleanup_task is None


def test_production_builder_uses_native_manager_and_keeps_durable_settings():
    from google.adk.agents import Agent
    from google.adk.apps import App
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.memory import InMemoryMemoryService

    from adk.agui import build_agui_adk_agent

    agent = Agent(name="test_agent", model="gemini-2.5-flash")
    service = ReadMemoSessionService(MagicMock(spec=VertexAiSessionService))
    with patch("adk.agui._deployment_app", return_value=App(name="app", root_agent=agent)):
        built = build_agui_adk_agent(
            agent,
            user_id="alice",
            app_name="app",
            session_service=service,
            memory_service=InMemoryMemoryService(),
            artifact_service=InMemoryArtifactService(),
        )
    m = built._session_manager
    assert isinstance(m, NativeThreadSessionManager)
    assert m._use_thread_id_as_session_id is True
    assert m._delete_session_on_cleanup is False
    assert m._timeout == 86400
