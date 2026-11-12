"""Force the real ADK Runner to compact, then continue through the summary."""

import pytest
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.compaction_summarizer import FIDELITY_PROMPT_TEMPLATE, FidelityEventSummarizer

pytestmark = pytest.mark.adk_contract


async def test_tool_findings_survive_real_compaction_and_reach_the_next_turn():
    requests, summaries = [], []
    marker = "COMPACTED_FINDING: measured speed 42 m/s"

    def read_measurement() -> dict:
        """Read the measured speed."""
        return {"speed": "42 m/s"}

    class Tutor(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            requests.append(" ".join(c.model_dump_json() for c in llm_request.contents))
            part = (
                types.Part(function_call=types.FunctionCall(name="read_measurement", args={}))
                if len(requests) == 1
                else types.Part(text="Let us discuss the experiment.")
            )
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    class Summarizer(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            text = " ".join(c.model_dump_json() for c in llm_request.contents)
            summaries.append(text)
            # ADK 1.x can summarize a later raw window without re-summarizing
            # the earlier summary; 2.x can include it. Never invent a finding
            # that was absent from this summarizer request.
            summary = marker if "42 m/s" in text else "Continued the experiment."
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=summary)]))

    app = App(
        name="test",
        root_agent=LlmAgent(name="tutor", model=Tutor(model="stub"), tools=[read_measurement]),
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=2,
            overlap_size=1,
            summarizer=FidelityEventSummarizer(
                llm=Summarizer(model="stub-summary"), prompt_template=FIDELITY_PROMPT_TEMPLATE
            ),
        ),
    )
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="test", user_id="u")
    runner = Runner(app=app, session_service=sessions)
    for i in range(5):
        events = [
            e
            async for e in runner.run_async(
                user_id="u",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=f"Experiment step {i}")]),
            )
        ]
        assert any(e.is_final_response() and e.content for e in events)
    restored = await sessions.get_session(app_name="test", user_id="u", session_id=session.id)
    assert summaries, "The real Runner never triggered compaction"
    assert any("42 m/s" in prompt for prompt in summaries), "The summarizer lost the tool's finding"
    assert any(e.actions.compaction for e in restored.events), "Compaction was not persisted"
    assert marker in requests[-1], "The continued turn did not receive the saved summary"
