"""ADK must build schema/tool requests supported by the whole fallback chain."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm, LlmCapabilities
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from adk.resilient_llm import ResilientLlm

pytestmark = pytest.mark.adk_contract


class _Answer(BaseModel):
    answer: str


def lookup(query: str) -> str:
    """Look up a synthetic fact."""
    return query


@pytest.mark.parametrize("supports", [(True, True), (True, False), (False, True)])
async def test_runner_uses_capability_shared_by_all_chain_members(supports):
    requests = []

    class Stub(BaseLlm):
        supports_schema_tools: bool

        @property
        def capabilities(self):
            return LlmCapabilities(output_schema_and_tools=self.supports_schema_tools)

        async def generate_content_async(self, llm_request, stream=False):
            requests.append(llm_request)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text='{"answer":"ok"}')]))

    model = ResilientLlm([Stub(model=f"stub-{i}", supports_schema_tools=value) for i, value in enumerate(supports)])
    agent = LlmAgent(name="skill", model=model, tools=[lookup], output_schema=_Answer)
    service = InMemorySessionService()
    session = await service.create_session(app_name="test", user_id="u")
    runner = Runner(agent=agent, app_name="test", session_service=service)
    events = [
        event
        async for event in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="hello")]),
        )
    ]
    assert any(event.is_final_response() for event in events)
    assert len(requests) == 1
    assert (requests[0].config.response_schema is not None) == all(supports)
    declarations = [fd.name for tool in requests[0].config.tools for fd in tool.function_declarations or []]
    assert "lookup" in declarations
    assert ("set_model_response" in declarations) == (not all(supports))
