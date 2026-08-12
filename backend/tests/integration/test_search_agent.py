"""Integration tests for the search sub-agent pattern.

These tests require GCP credentials (ADC) and hit real Vertex AI endpoints.
Run with:  uv run pytest tests/integration/test_search_agent.py -v -m integration

What we verify:
  1. search_agent standalone — google_search returns a grounded answer.
  2. AgentTool pattern     — a Gemini root agent delegates to search_agent and
                             returns text; retrieve_artifact coexists (no 400).
  3. VertexAiSearch        — enterprise search fires when VERTEX_AI_SEARCH_DATASTORE_ID
                             is set. Skipped otherwise with a clear message.
"""

from __future__ import annotations

import os

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool
from google.genai import types

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(agent: LlmAgent, prompt: str) -> list:
    """Run an agent synchronously and return all events."""
    svc = InMemorySessionService()
    session = svc.create_session_sync(user_id="test", app_name="test")
    runner = Runner(agent=agent, session_service=svc, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    return list(
        runner.run(
            new_message=message,
            user_id="test",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )


def _has_text(events: list) -> bool:
    return any(e.content and e.content.parts and any(p.text for p in e.content.parts) for e in events)


# ---------------------------------------------------------------------------
# 1. Search sub-agent standalone
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_search_agent_returns_text_for_factual_query():
    """search_agent can answer a simple factual web query via google_search."""
    from tools.search_agent import create_search_agent

    agent = create_search_agent()
    events = _run(agent, "What is the capital of France?")
    assert _has_text(events), "search_agent produced no text output"


@pytest.mark.integration
def test_search_agent_answer_mentions_paris():
    """Sanity-check grounding: the answer to capital-of-France includes 'Paris'."""
    from tools.search_agent import create_search_agent

    agent = create_search_agent()
    events = _run(agent, "What is the capital of France? Answer in one word.")
    text = " ".join(p.text for e in events if e.content and e.content.parts for p in e.content.parts if p.text)
    assert "Paris" in text, f"Expected 'Paris' in answer, got: {text!r}"


# ---------------------------------------------------------------------------
# 2. AgentTool pattern — root agent + search sub-agent, FunctionTools coexist
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_root_agent_with_google_search_tool_returns_text():
    """A Gemini root agent using AgentTool(search_agent) can answer a web question.

    This is the key regression guard: previously a 400 INVALID_ARGUMENT fired
    because google_search (a grounding built-in) was placed directly on the
    root agent alongside FunctionTools. The sub-agent pattern eliminates that.
    """
    from adk.artifact_tools import retrieve_artifact
    from tools.search_agent import create_search_agent

    search_tool = AgentTool(create_search_agent())

    root = LlmAgent(
        name="test_root",
        model="gemini-2.5-flash",
        instruction="Answer the user's question. Use the search tool for current information.",
        tools=[retrieve_artifact, search_tool],
    )
    events = _run(root, "What year did the Eiffel Tower open?")
    assert _has_text(events), "Root agent with AgentTool(search_agent) produced no text"


@pytest.mark.integration
def test_root_agent_search_does_not_raise_400():
    """No 400 INVALID_ARGUMENT when FunctionTools and search AgentTool coexist.

    Passes if _run() completes without raising. The sub-agent pattern keeps
    grounding built-ins isolated to the search_agent LlmAgent, so Vertex AI
    never sees mixed tool types on the root agent.
    """
    from adk.artifact_tools import retrieve_artifact
    from tools.search_agent import create_search_agent

    root = LlmAgent(
        name="test_root_no400",
        model="gemini-2.5-flash",
        instruction="Use your search tool to answer questions about current events.",
        tools=[retrieve_artifact, AgentTool(create_search_agent())],
    )
    # Should complete without raising google.api_core.exceptions.InvalidArgument
    _run(root, "Who won the most recent FIFA World Cup?")


# ---------------------------------------------------------------------------
# 3. VertexAiSearch (enterprise) — requires env var, skipped otherwise
# ---------------------------------------------------------------------------

_DATASTORE_ID = os.environ.get("VERTEX_AI_SEARCH_DATASTORE_ID")


@pytest.mark.integration
@pytest.mark.skipif(
    not _DATASTORE_ID,
    reason=(
        "VERTEX_AI_SEARCH_DATASTORE_ID not set — skipping enterprise search test. "
        "Set to a valid Discovery Engine datastore resource ID to run."
    ),
)
def test_search_agent_with_vertex_ai_search_returns_text():
    """search_agent with VertexAiSearchTool answers from the enterprise datastore."""
    from tools.search_agent import create_search_agent

    agent = create_search_agent(datastore_id=_DATASTORE_ID)
    events = _run(agent, "What topics does the knowledge base cover?")
    assert _has_text(events), "search_agent with VertexAiSearchTool produced no text"


@pytest.mark.integration
@pytest.mark.skipif(
    not _DATASTORE_ID,
    reason="VERTEX_AI_SEARCH_DATASTORE_ID not set — skipping enterprise search coexistence test.",
)
def test_root_agent_with_vertex_ai_search_and_function_tools():
    """AgentTool(search_agent + VertexAiSearchTool) coexists with FunctionTools on root.

    Verifies the sub-agent pattern holds for enterprise search datastores — the
    root agent still has a clean FunctionTool-only tool list.
    """
    from adk.artifact_tools import retrieve_artifact
    from tools.search_agent import create_search_agent

    search_tool = AgentTool(create_search_agent(datastore_id=_DATASTORE_ID))
    root = LlmAgent(
        name="test_root_vertex",
        model="gemini-2.5-flash",
        instruction="Search the knowledge base to answer the user's question.",
        tools=[retrieve_artifact, search_tool],
    )
    events = _run(root, "Summarise the main topics in the knowledge base.")
    assert _has_text(events), "Root agent with VertexAiSearchTool AgentTool produced no text"
