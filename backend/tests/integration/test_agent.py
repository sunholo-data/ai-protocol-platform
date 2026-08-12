"""Integration test for the root ADK agent."""

import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app import root_agent


@pytest.mark.integration
def test_agent_stream() -> None:
    """Test that the root agent returns valid streaming responses."""

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="test_user", app_name="aitana-platform")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="aitana-platform")

    message = types.Content(role="user", parts=[types.Part.from_text(text="Hello, what can you help with?")])

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one event"

    has_text = any(
        event.content and event.content.parts and any(p.text for p in event.content.parts) for event in events
    )
    assert has_text, "Expected at least one event with text content"
