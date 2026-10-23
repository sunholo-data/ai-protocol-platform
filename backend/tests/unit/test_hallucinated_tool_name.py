"""ADK-contract guard: the model calls a tool that does not exist.

A model can emit a function call for a tool it was never given (a lite model
recalling a tool from another skill, a stale name after a rename). What happens
next differs across ADK versions: 1.x raised immediately; 2.x supplies a bare
BaseTool placeholder to before-tool callbacks, then reports the lookup error
back to the model without running after-tool callbacks. Our composer rejects
that placeholder before side effects, preserving the named RUN_ERROR policy.

What we need to hold across that change:

1. **Our tool callbacks fire balanced.** Every ``before_tool`` our chain runs has
   a matching ``after_tool`` (the STAGE_PROGRESS ``Calling X…`` label, MCP span
   tagging and the A2UI emitter all assume pairs). Today: neither fires for an
   unknown tool.
2. **The user sees a visible notice (CLAUDE.md #8).** The turn must not die
   silently or hang; the wire must carry something that names the problem.

Drives the REAL Runner, and the REAL ag_ui_adk ``ADKAgent`` through
``stream_agui_events``, with a stub model that calls an unknown tool once and
answers in plain text if it is ever asked again.

Part of `make adk-conformance`. See docs/design/template/template-adk-2-regression-gate.md.
"""

from __future__ import annotations

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from ag_ui_adk import ADKAgent
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.agui import stream_agui_events
from adk.mcp_observability import (
    compose_after_tool_callbacks,
    compose_before_tool_callbacks,
    make_mcp_after_tool_callback,
    make_mcp_before_tool_callback,
)

pytestmark = pytest.mark.adk_contract

_UNKNOWN_TOOL = "search_the_web"
_LOOP_CAP = 4

_MODEL_CALLS: list[int] = []
_BEFORE: list[str] = []
_AFTER: list[str] = []


def lookup_contract(contract_id: str) -> dict:
    """The one real tool the agent has.

    Args:
        contract_id: the contract to look up.
    """
    return {"contract_id": contract_id}


class _CallsUnknownTool(BaseLlm):
    """First call: a function call to a tool the agent does not have. Any later
    call (only reachable if ADK reports the error back to the model): text."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _MODEL_CALLS.append(1)
        if len(_MODEL_CALLS) > 1:
            text = "(capped)" if len(_MODEL_CALLS) > _LOOP_CAP else "I could not do that."
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))
            return
        fc = types.FunctionCall(id="call-unknown", name=_UNKNOWN_TOOL, args={"query": "ppa prices"})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))


def _count_before(tool, args: dict, tool_context=None) -> None:
    _BEFORE.append(getattr(tool, "name", ""))
    return None


def _count_after(tool, args: dict, tool_context=None, tool_response=None) -> None:
    _AFTER.append(getattr(tool, "name", ""))
    return None


def _agent() -> LlmAgent:
    _MODEL_CALLS.clear()
    _BEFORE.clear()
    _AFTER.clear()
    # The same composers create_agent uses, with counters in the slots our
    # permission enforcer / mutators occupy in production.
    return LlmAgent(
        name="hallucinator",
        model=_CallsUnknownTool(model="stub-hallucinator"),
        tools=[lookup_contract],
        before_tool_callback=compose_before_tool_callbacks(_count_before, make_mcp_before_tool_callback()),
        after_tool_callback=compose_after_tool_callbacks(_count_after, make_mcp_after_tool_callback()),
    )


async def test_unknown_tool_raises_out_of_the_runner():
    """Our fail-fast policy survives ADK 2's changed unknown-tool handling."""
    agent = _agent()
    session_service = InMemorySessionService()
    session = await session_service.create_session(user_id="u", app_name="test")
    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="search for ppa prices")])
    with pytest.raises(ValueError, match=f"Tool '{_UNKNOWN_TOOL}' not found"):
        async for _ in runner.run_async(new_message=message, user_id="u", session_id=session.id):
            pass
    assert len(_MODEL_CALLS) == 1


async def test_tool_callbacks_fire_balanced():
    agent = _agent()
    session_service = InMemorySessionService()
    session = await session_service.create_session(user_id="u", app_name="test")
    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="search for ppa prices")])
    try:
        async for _ in runner.run_async(new_message=message, user_id="u", session_id=session.id):
            pass
    except ValueError:
        pass  # today's mechanism — asserted in the test above
    assert sorted(_BEFORE) == sorted(_AFTER), f"tool callbacks unbalanced: before={_BEFORE!r} after={_AFTER!r}"
    assert _BEFORE == [], f"no tool callback may fire for an unknown tool, got {_BEFORE!r}"


async def test_user_sees_a_visible_notice_naming_the_tool():
    agui_agent = ADKAgent(
        adk_agent=_agent(),
        app_name="test",
        user_id="u",
        session_service=InMemorySessionService(),
        use_in_memory_services=True,
        use_thread_id_as_session_id=True,
    )
    run_input = RunAgentInput(
        thread_id="thread-hallucinated-tool",
        run_id="run-1",
        state={},
        messages=[UserMessage(id="m1", role="user", content="search for ppa prices")],
        tools=[],
        context=[],
        forwarded_props={},
    )
    out = [ev async for ev in stream_agui_events(agui_agent, run_input, heartbeat_seconds=0)]
    types_on_wire = [e.get("type") for e in out]

    terminals = [t for t in types_on_wire if t in ("RUN_ERROR", "RUN_FINISHED")]
    assert len(terminals) == 1, f"expected exactly one terminal event, got {terminals!r} in {types_on_wire!r}"
    assert types_on_wire[-1] == terminals[0], f"the terminal event must be LAST: {types_on_wire!r}"

    # The notice: today a RUN_ERROR whose message names the unknown tool.
    assert terminals[0] == "RUN_ERROR", f"an unknown tool must surface as a visible RUN_ERROR: {types_on_wire!r}"
    message = str(out[-1].get("message") or "")
    assert _UNKNOWN_TOOL in message, f"the notice must name the unknown tool, got {message!r}"
    assert _BEFORE == _AFTER == [], f"tool callbacks unbalanced on the AG-UI path: {_BEFORE!r} / {_AFTER!r}"
