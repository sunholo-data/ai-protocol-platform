"""Upgrade gates use real services: a mocked save can hide a missing await."""

import pytest
from google.adk.agents import LlmAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.callbacks import _handle_large_output
from adk.mcp_observability import compose_after_tool_callbacks, make_mcp_after_tool_callback

pytestmark = pytest.mark.adk_contract


async def test_large_tool_output_is_saved_before_the_model_gets_its_pointer():
    seen = []
    payload = "synthetic experiment results\n" * 3000

    def experiment_results() -> str:
        """Get the synthetic experiment results."""
        return payload

    class Model(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            if not seen:
                seen.append("call")
                part = types.Part(function_call=types.FunctionCall(name="experiment_results", args={}))
            else:
                responses = [
                    p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response
                ]
                assert responses and "saved as artifact" in str(responses[-1].response)
                part = types.Part(text="The results are saved.")
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    artifacts = InMemoryArtifactService()
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="test", user_id="u")
    agent = LlmAgent(
        name="experiment",
        model=Model(model="stub"),
        tools=[experiment_results],
        after_tool_callback=compose_after_tool_callbacks(_handle_large_output, make_mcp_after_tool_callback()),
    )
    runner = Runner(agent=agent, app_name="test", session_service=sessions, artifact_service=artifacts)
    events = [
        e
        async for e in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Load my results")]),
        )
    ]
    assert any(e.is_final_response() and e.content for e in events)
    names = await artifacts.list_artifact_keys(app_name="test", user_id="u", session_id=session.id)
    assert len(names) == 1, "The callback returned a pointer but never saved the artifact"
    artifact = await artifacts.load_artifact(app_name="test", user_id="u", session_id=session.id, filename=names[0])
    assert artifact.text == payload
