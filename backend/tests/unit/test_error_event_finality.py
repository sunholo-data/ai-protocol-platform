"""ADK-contract guard: a model ERROR event (error_code, no function calls) ends the
turn, and the AG-UI wire carries exactly one, visible, terminal event for it.

When a model call fails softly — Gemini's ``MALFORMED_FUNCTION_CALL``, a safety
block, a provider error mapped to ``LlmResponse.error_code`` — ADK yields an
event with ``error_code``/``error_message`` and no content. Two things decide
what the user sees:

1. ``Event.is_final_response()`` on that event. ADK's flow stops re-invoking
   the model only on a final event (contract C1), and ag_ui_adk's translator
   reads the same flag to close the text stream.
2. Our AG-UI terminal handling (``stream_agui_events``, G41 + never-silent):
   at most ONE terminal event, LAST on the wire, and never a silent
   RUN_FINISHED for a turn that produced nothing (CLAUDE.md #8).

ADK 2.x changelog 728 changes (1): an error event without function calls is
explicitly final. This pins today's answer on 1.31.1 so the bump shows any
difference, and pins (2) end-to-end through the REAL ag_ui_adk ``ADKAgent``.

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

pytestmark = pytest.mark.adk_contract

_ERROR_CODE = "MALFORMED_FUNCTION_CALL"
_ERROR_MESSAGE = "Malformed function call: stub"
_LOOP_CAP = 4  # a regression that re-invokes the model stops here instead of hanging

_MODEL_CALLS: list[int] = []


class _ErrorsOut(BaseLlm):
    """A stub model whose every call fails softly: error_code, no content."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _MODEL_CALLS.append(1)
        if len(_MODEL_CALLS) > _LOOP_CAP:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="(capped)")]))
            return
        yield LlmResponse(error_code=_ERROR_CODE, error_message=_ERROR_MESSAGE)


def _agent() -> LlmAgent:
    return LlmAgent(name="erring", model=_ErrorsOut(model="stub-erring"))


def _runner_events() -> list:
    _MODEL_CALLS.clear()
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="u", app_name="test")
    runner = Runner(agent=_agent(), session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="hello")])
    return list(runner.run(new_message=message, user_id="u", session_id=session.id))


def test_error_event_without_function_calls_is_final():
    events = _runner_events()
    errors = [e for e in events if e.error_code]
    assert len(errors) == 1, f"expected one error event, got {[(e.author, e.error_code) for e in events]}"
    err = errors[0]
    assert err.error_code == _ERROR_CODE
    assert err.error_message == _ERROR_MESSAGE
    assert not err.get_function_calls()
    assert err.is_final_response() is True, "an error event with no function calls must be final"


def test_error_event_ends_the_turn():
    """Because the error event is final, the flow does not re-invoke the model."""
    _runner_events()
    assert len(_MODEL_CALLS) == 1, f"model re-invoked {len(_MODEL_CALLS)}x after an error event"


async def test_agui_wire_has_exactly_one_visible_terminal_event():
    _MODEL_CALLS.clear()
    agui_agent = ADKAgent(
        adk_agent=_agent(),
        app_name="test",
        user_id="u",
        session_service=InMemorySessionService(),
        use_in_memory_services=True,
        use_thread_id_as_session_id=True,
    )
    run_input = RunAgentInput(
        thread_id="thread-error-finality",
        run_id="run-1",
        state={},
        messages=[UserMessage(id="m1", role="user", content="hello")],
        tools=[],
        context=[],
        forwarded_props={},
    )
    out = [ev async for ev in stream_agui_events(agui_agent, run_input, heartbeat_seconds=0)]
    types_on_wire = [e.get("type") for e in out]

    terminals = [t for t in types_on_wire if t in ("RUN_ERROR", "RUN_FINISHED")]
    assert terminals, f"no terminal event on the wire: {types_on_wire!r}"
    assert len(terminals) == 1, f"expected exactly one terminal event, got {terminals!r} in {types_on_wire!r}"
    assert types_on_wire[-1] == terminals[0], f"the terminal event must be LAST: {types_on_wire!r}"
    assert types_on_wire[0] == "RUN_STARTED", f"RUN_STARTED must be first (C7): {types_on_wire!r}"

    # Never silent (CLAUDE.md #8): the errored turn produced no text and no tool
    # call, so the user must get a visible, retryable RUN_ERROR — not a quiet
    # RUN_FINISHED that leaves the chat hanging.
    assert terminals[0] == "RUN_ERROR", f"an errored, output-less turn must end in RUN_ERROR: {types_on_wire!r}"
    err = out[-1]
    assert err.get("message"), f"RUN_ERROR must carry a user-visible message: {err!r}"
    assert len(_MODEL_CALLS) == 1, f"model re-invoked {len(_MODEL_CALLS)}x on the AG-UI path"
