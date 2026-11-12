"""Generated tool-call errors are soft model failures, not HTTP 400 requests.

Ported from a downstream migration review. Real Runner checks the execution
boundary; scripted streams check retry limits, cleanup and request isolation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk import resilient_llm as rl
from adk.model_errors import ModelTurnError, classify

pytestmark = pytest.mark.adk_contract


def text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


class Scripted:
    def __init__(self, responses: list[list[Any]], model="stub-primary"):
        self.model = model
        self.responses = responses
        self.calls = 0
        self.closed = 0
        self.instructions = []

    async def generate_content_async(self, request, stream=False):
        self.instructions.append(request.config.system_instruction)
        batch = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        try:
            for response in batch:
                if isinstance(response, BaseException):
                    raise response
                yield response
        finally:
            self.closed += 1


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    rl.reset_provider_health()
    monkeypatch.delenv("FAULT_INJECT_MODEL", raising=False)

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(rl, "_async_sleep", no_sleep)
    yield
    rl.reset_provider_health()


@pytest.mark.parametrize("deadline", ["0", "10"])
async def test_soft_failure_is_regenerated_once_on_the_same_model(monkeypatch, deadline):
    monkeypatch.setenv("MODEL_FIRST_TOKEN_DEADLINE_S", deadline)
    invalid = LlmResponse(error_code="MALFORMED_FUNCTION_CALL")
    primary = Scripted([[invalid], [text("Recovered")]])
    notices = []
    model = rl.ResilientLlm([primary], event_sink=lambda *a: notices.append(a))
    request = LlmRequest(
        model=primary.model, config=types.GenerateContentConfig(system_instruction="Original instruction")
    )
    before = request.model_dump_json()
    out = [r async for r in model.generate_content_async(request, stream=True)]
    assert [r.content.parts[0].text for r in out] == ["Recovered"]
    assert primary.calls == primary.closed == 2
    assert [n[0] for n in notices] == ["MODEL_RETRY"]
    assert notices[0][1]["code"] == "MODEL_TOOL_CALL_INVALID"
    assert "MALFORMED_FUNCTION_CALL" in str(primary.instructions[1])
    assert request.model_dump_json() == before


async def test_persistent_failure_is_bounded_and_does_not_switch_models():
    invalid = LlmResponse(error_code="MALFORMED_FUNCTION_CALL")
    primary = Scripted([[invalid]])
    fallback = Scripted([[text("Wrong model")]], model="stub-fallback")
    model = rl.ResilientLlm([primary, fallback], event_sink=lambda *a: None)
    with pytest.raises(ModelTurnError) as caught:
        _ = [r async for r in model.generate_content_async(LlmRequest(), stream=True)]
    assert caught.value.error_class.code == "MODEL_TOOL_CALL_INVALID"
    assert primary.calls == primary.closed == 2
    assert fallback.calls == 0


@pytest.mark.parametrize(
    "visible",
    [
        text("Already visible"),
        LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(function_call=types.FunctionCall(name="mutate", args={}))]
            )
        ),
    ],
)
async def test_after_visible_output_no_text_or_tool_is_replayed(visible):
    primary = Scripted([[visible, LlmResponse(error_code="MALFORMED_FUNCTION_CALL")]])
    notices = []
    model = rl.ResilientLlm([primary], event_sink=lambda *a: notices.append(a))
    out = []
    with pytest.raises(ModelTurnError):
        async for response in model.generate_content_async(LlmRequest(), stream=True):
            out.append(response)
    assert out == [visible]
    assert primary.calls == 1
    assert notices == []


@pytest.mark.parametrize("signal", [asyncio.CancelledError, GeneratorExit])
async def test_control_flow_is_not_treated_as_a_generated_error(signal):
    primary = Scripted([[signal("stop")]])
    model = rl.ResilientLlm([primary], event_sink=lambda *a: None)
    with pytest.raises(signal):
        _ = [r async for r in model.generate_content_async(LlmRequest(), stream=True)]
    assert primary.calls == 1


async def test_real_runner_recovers_and_executes_the_tool_only_once():
    calls, mutations = [], []

    def record_trial() -> dict:
        """Record the synthetic trial once."""
        mutations.append(1)
        return {"ok": True}

    class Model(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            calls.append(1)
            if len(calls) == 1:
                yield LlmResponse(error_code="MALFORMED_FUNCTION_CALL")
                return
            part = (
                types.Part(function_call=types.FunctionCall(name="record_trial", args={}))
                if len(calls) == 2
                else types.Part(text="Trial recorded.")
            )
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="test", user_id="u")
    model = rl.ResilientLlm([Model(model="stub")], event_sink=lambda *a: None)
    runner = Runner(
        agent=LlmAgent(name="tutor", model=model, tools=[record_trial]), app_name="test", session_service=sessions
    )
    events = [
        e
        async for e in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Record the trial")]),
        )
    ]
    assert not any(e.error_code for e in events)
    assert len(calls) == 3 and mutations == [1]
    assert any(
        e.is_final_response() and e.content and any(p.text == "Trial recorded." for p in e.content.parts or [])
        for e in events
    )


def test_http_request_validation_errors_remain_non_retryable():
    from google.genai.errors import ClientError

    error = ClientError(400, {"error": {"message": "Invalid request", "status": "INVALID_ARGUMENT"}})
    classification = classify(error)
    assert classification.code == "MODEL_REQUEST_INVALID"
    assert not classification.transient and not classification.fallbackable


@pytest.mark.parametrize(
    "instruction",
    ["Original instruction", types.Content(parts=[types.Part(text="Original instruction")]), ["Original instruction"]],
)
def test_repair_hint_never_mutates_the_shared_request_config(instruction):
    original = LlmRequest(config=types.GenerateContentConfig(system_instruction=instruction))
    before = original.model_dump_json()
    repaired = rl._tool_call_repair_request(original)
    assert original.model_dump_json() == before
    assert "Original instruction" in repaired.model_dump_json()
    assert "MALFORMED_FUNCTION_CALL" in repaired.model_dump_json()
    assert repaired.config is not original.config


@pytest.mark.parametrize("deadline", ["0", "10"])
async def test_cancellation_during_error_cleanup_is_not_swallowed(monkeypatch, deadline):
    monkeypatch.setenv("MODEL_FIRST_TOKEN_DEADLINE_S", deadline)
    calls, notices = [], []

    class InterruptedClose(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            calls.append(1)
            try:
                yield LlmResponse(error_code="MALFORMED_FUNCTION_CALL")
            finally:
                raise asyncio.CancelledError("cancelled during close")

    model = rl.ResilientLlm([InterruptedClose(model="stub")], event_sink=lambda *a: notices.append(a))
    with pytest.raises(asyncio.CancelledError):
        _ = [r async for r in model.generate_content_async(LlmRequest(), stream=True)]
    assert calls == [1] and notices == []
