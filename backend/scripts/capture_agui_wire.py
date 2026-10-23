"""Capture a synthetic real ADK/AG-UI turn for the Node client contract check.

From backend/: uv run python scripts/capture_agui_wire.py > /tmp/agui-wire.jsonl
From frontend/: node scripts/check-agui-wire.mjs /tmp/agui-wire.jsonl
No model API, credentials or customer data are used.
"""

from __future__ import annotations

import asyncio
import json

from ag_ui.core import RunAgentInput, UserMessage
from ag_ui_adk import ADKAgent
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from adk.agui import stream_agui_events


def clear_selection(tool_context: ToolContext) -> dict:
    """Clear the synthetic selection."""
    tool_context.state["selection"] = None
    return {"cleared": True}


class _SyntheticModel(BaseLlm):
    async def generate_content_async(self, llm_request, stream=False):
        if any(part.function_response for content in llm_request.contents for part in content.parts or []):
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="Selection cleared.")])
            )
        else:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(id="clear-1", name="clear_selection", args={}))],
                )
            )


async def capture():
    bridge = ADKAgent(
        adk_agent=LlmAgent(name="contract", model=_SyntheticModel(model="synthetic"), tools=[clear_selection]),
        app_name="wire_contract",
        user_id="synthetic-user",
        session_service=InMemorySessionService(),
        use_in_memory_services=True,
        use_thread_id_as_session_id=True,
    )
    run_input = RunAgentInput(
        thread_id="wire-contract",
        run_id="wire-run",
        state={"selection": "previous"},
        messages=[UserMessage(id="user-1", content="Clear the selection.")],
        tools=[],
        context=[],
        forwarded_props={},
    )
    async for event in stream_agui_events(bridge, run_input, heartbeat_seconds=0):
        print(json.dumps(event))


if __name__ == "__main__":
    asyncio.run(capture())
