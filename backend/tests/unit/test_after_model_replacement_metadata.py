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


async def test_after_model_replacement_metadata():
    seen = []
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=11,
        candidates_token_count=7,
        total_token_count=18,
    )

    class Stream(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            assert stream
            for partial, turn_complete in [(True, False), (False, True)]:
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part.from_text(text="original")]),
                    usage_metadata=usage,
                    partial=partial,
                    turn_complete=turn_complete,
                )

    def replace(callback_context, llm_response):
        seen.append((llm_response.usage_metadata, llm_response.partial, llm_response.turn_complete))
        return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="replacement")]))

    events = await _turn(LlmAgent(name="skill", model=Stream(model="stub"), after_model_callback=replace))
    assert seen == [(usage, True, False), (usage, False, True)]
    responses = [e for e in events if e.author == "skill" and e.content]
    assert len(responses) == 2
    assert [e.content.parts[0].text for e in responses] == ["replacement", "replacement"]
    # ADK 2 preserves usage and stream flags on content-only replacements.
    assert [(e.usage_metadata, e.partial, e.turn_complete) for e in responses] == [
        (usage, True, False),
        (usage, False, True),
    ]
