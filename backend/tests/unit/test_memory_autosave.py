"""MEMORY_AUTOSAVE: each finished turn reaches the memory bank (adk/memory_autosave.py).

The end-to-end test drives a real ADK Runner with InMemoryMemoryService, so the
callback goes through the real CallbackContext — the private
``_invocation_context`` it reads is exactly what an ADK bump could break.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.memory import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.memory_autosave import make_memory_autosave_callback, memory_autosave_enabled, turn_text_events


class _FixedReply(BaseLlm):
    model: str = "fixed-reply"

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Noted: you prefer tea.")]))


def _event(invocation_id: str, author: str, *parts: types.Part) -> Event:
    return Event(invocation_id=invocation_id, author=author, content=types.Content(role=author, parts=list(parts)))


def test_flag_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_AUTOSAVE", raising=False)
    assert not memory_autosave_enabled()
    monkeypatch.setenv("MEMORY_AUTOSAVE", "1")
    assert memory_autosave_enabled()


def test_turn_text_events_keeps_only_this_turns_text() -> None:
    call = types.Part(function_call=types.FunctionCall(name="read_doc", args={"id": "x"}))
    result = types.Part(function_response=types.FunctionResponse(name="read_doc", response={"text": "CONFIDENTIAL"}))
    events = [
        _event("old", "user", types.Part(text="an earlier turn")),
        _event("inv", "user", types.Part(text="I prefer tea")),
        _event("inv", "model", types.Part(text="thinking…", thought=True), call),
        _event("inv", "user", result),
        _event("inv", "model", types.Part(text="Noted.")),
    ]
    kept = turn_text_events(events, "inv")
    assert [(e.author, [p.text for p in e.content.parts]) for e in kept] == [
        ("user", ["I prefer tea"]),
        ("model", ["Noted."]),
    ]
    assert "CONFIDENTIAL" not in str([e.content for e in kept])


async def test_real_turn_lands_in_memory_scoped_to_the_user() -> None:
    sessions, memory = InMemorySessionService(), InMemoryMemoryService()
    agent = LlmAgent(name="root", model=_FixedReply(), after_agent_callback=make_memory_autosave_callback())
    runner = Runner(app_name="app", agent=agent, session_service=sessions, memory_service=memory)
    session = await sessions.create_session(app_name="app", user_id="alice")

    async for _ in runner.run_async(
        user_id="alice",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="I prefer tea")]),
    ):
        pass

    found = await memory.search_memory(app_name="app", user_id="alice", query="tea")
    assert {p.text for m in found.memories for p in m.content.parts} == {"I prefer tea", "Noted: you prefer tea."}
    other = await memory.search_memory(app_name="app", user_id="bob", query="tea")
    assert other.memories == []


async def test_streams_with_the_session_and_an_idle_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_AUTOSAVE_IDLE_MINUTES", "2")
    ctx = MagicMock()
    ctx._invocation_context.agent.parent_agent = None
    ctx._invocation_context.invocation_id = "inv"
    ctx._invocation_context.session.id = "s1"
    ctx._invocation_context.session.events = [_event("inv", "user", types.Part(text="hello"))]
    ctx.add_events_to_memory = AsyncMock()

    await make_memory_autosave_callback()(ctx)

    meta = ctx.add_events_to_memory.await_args.kwargs["custom_metadata"]
    assert meta == {"stream_id": "s1", "generation_trigger_config": {"generation_rule": {"idle_duration": "120s"}}}


async def test_sub_agents_do_not_save_a_half_turn() -> None:
    ctx = MagicMock()
    ctx._invocation_context.agent.parent_agent = object()
    ctx.add_events_to_memory = AsyncMock()
    await make_memory_autosave_callback()(ctx)
    ctx.add_events_to_memory.assert_not_awaited()


async def test_a_memory_failure_never_fails_the_turn() -> None:
    ctx = MagicMock()
    ctx._invocation_context.agent.parent_agent = None
    ctx._invocation_context.invocation_id = "inv"
    ctx._invocation_context.session.events = [_event("inv", "user", types.Part(text="hello"))]
    ctx.add_events_to_memory = AsyncMock(side_effect=RuntimeError("memory bank down"))
    assert await make_memory_autosave_callback()(ctx) is None


@pytest.mark.parametrize(
    ("flag", "tool_configs", "wired"),
    [
        ("1", None, True),
        ("1", {"defaults": {"memory": False}}, False),
        ("", None, False),
    ],
)
def test_factory_wires_it_only_for_memory_skills_when_enabled(
    monkeypatch: pytest.MonkeyPatch, flag: str, tool_configs: dict | None, wired: bool
) -> None:
    from adk.agent import create_agent
    from tests.unit.test_create_agent import _skill, _user

    monkeypatch.setenv("MEMORY_AUTOSAVE", flag)
    with patch("adk.agent.make_memory_autosave_callback") as make:
        create_agent(_skill(tool_configs=tool_configs or {}), _user())
    assert make.called is wired
