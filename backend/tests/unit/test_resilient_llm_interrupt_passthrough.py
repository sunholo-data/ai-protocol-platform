"""Phase 0 ADK contract: real Runner with a hermetic BaseLlm stub."""

from __future__ import annotations

import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.resilient_llm import ResilientLlm, reset_provider_health

pytestmark = pytest.mark.adk_contract


async def _turn(agent, service=None, session=None):
    service = service if service is not None else InMemorySessionService()
    if session is None:
        session = await service.create_session(app_name="test", user_id="u", state={"var": "VALUE"})
    runner = Runner(agent=agent, session_service=service, app_name="test")
    return [
        event
        async for event in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="hello")]),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    ]


class _NodeInterruptedLike(BaseException):
    """ADK 2 control-flow interrupts inherit directly from BaseException."""


@pytest.mark.parametrize("through_runner", [False, True])
async def test_resilient_llm_interrupt_passthrough(monkeypatch, through_runner):
    # Exercise both the normal path and the streamed first-token deadline path.
    monkeypatch.delenv("FAULT_INJECT_MODEL", raising=False)
    reset_provider_health()
    try:
        for deadline in ["0", "10"]:
            monkeypatch.setenv("MODEL_FIRST_TOKEN_DEADLINE_S", deadline)
            for signal in [asyncio.CancelledError("cancel"), _NodeInterruptedLike("pause")]:
                calls = []
                notices = []

                class Interrupt(BaseLlm):
                    async def generate_content_async(self, llm_request, stream=False):
                        calls.append("primary")  # noqa: B023 — used within this iteration
                        raise signal  # noqa: B023 — used within this iteration
                        yield  # async generator

                class Fallback(BaseLlm):
                    async def generate_content_async(self, llm_request, stream=False):
                        calls.append("fallback")  # noqa: B023 — used within this iteration
                        yield LlmResponse(
                            content=types.Content(
                                role="model",
                                parts=[types.Part.from_text(text="unexpected fallback")],
                            )
                        )

                model = ResilientLlm(
                    [Interrupt(model="stub-primary"), Fallback(model="stub-fallback")],
                    event_sink=lambda *args: notices.append(args),  # noqa: B023 — used within this iteration
                )

                async def invoke(model=model):
                    if through_runner:
                        return await _turn(LlmAgent(name="skill", model=model))
                    return [event async for event in model.generate_content_async(LlmRequest(), stream=True)]

                if through_runner and isinstance(signal, asyncio.CancelledError):
                    # ADK 2 Runner ends a cancelled root node normally. The
                    # wrapper itself must still propagate the exact signal.
                    events = await invoke()
                    assert not any(event.author == "skill" for event in events)
                else:
                    with pytest.raises(type(signal)) as caught:
                        await invoke()
                    assert caught.value is signal
                assert calls == ["primary"]
                assert notices == []
    finally:
        reset_provider_health()
