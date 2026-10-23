"""Phase 0 ADK contract: real Runner with a hermetic BaseLlm stub."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.errors.session_not_found_error import SessionNotFoundError
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.session import Session
from google.genai import types

pytestmark = pytest.mark.adk_contract


async def _turn(agent, service=None, session=None):
    service = service if service is not None else InMemorySessionService()
    if session is None:
        session = await service.create_session(app_name="test", user_id="u", state={"var": "VALUE"})
    runner = Runner(agent=agent, session_service=service, app_name="test")
    return [
        event
        async for event in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="hello")]),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    ]


async def test_inmemory_append_unknown_session():
    service = InMemorySessionService()
    unknown = Session(id="missing", app_name="test", user_id="u")
    event = Event(
        invocation_id="inv",
        author="user",
        content=types.Content(
            role="user",
            parts=[types.Part.from_text(text="orphan")],
        ),
    )
    # ADK 2 rejects an orphan rather than silently dropping its event.
    with pytest.raises(SessionNotFoundError, match="missing"):
        await service.append_event(session=unknown, event=event)
    assert await service.get_session(app_name="test", user_id="u", session_id="missing") is None

    class Reply(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))

    # Positive control: local fixtures must create before Runner appends.
    session = await service.create_session(app_name="test", user_id="u")
    events = await _turn(LlmAgent(name="skill", model=Reply(model="stub")), service, session)
    stored = await service.get_session(app_name="test", user_id="u", session_id=session.id)
    assert stored is not None
    assert any(e.author == "user" for e in stored.events)
    assert any(e.author == "skill" and e.is_final_response() for e in events)
