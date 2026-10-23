"""Phase 0 ADK contract: real Runner with a hermetic BaseLlm stub."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
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


async def test_instruction_template_literals():
    seen = []

    class Capture(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            seen.append(llm_request.config.system_instruction)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))

    # Use a string instruction: callable providers bypass ADK state templating.
    agent = LlmAgent(name="skill", model=Capture(model="stub"), instruction=r"{var} | ${var} | \{var}")
    events = await _turn(agent)
    assert any(e.is_final_response() for e in events)
    assert seen == [r"VALUE | ${var} | \{var}" + '\n\nYou are an agent. Your internal name is "skill".']
