"""Regression shapes from empty OpenAI STOP responses and PPA stall traces."""

from types import SimpleNamespace

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adk import resilient_llm as rl
from adk.model_errors import ModelTurnError
from tests.unit.test_resilient_llm import _ScriptedLlm, _Stall, _StallingLlm, _text, _thought, _wrap


@pytest.fixture(autouse=True)
def clean_health():
    rl.reset_provider_health()
    yield
    rl.reset_provider_health()


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize(
    "responses", [[], [LlmResponse(finish_reason=types.FinishReason.STOP)], [_thought("thinking")]]
)
async def test_empty_or_reasoning_only_call_falls_back_without_empty_final(stream, responses):
    primary = _ScriptedLlm("openai/gpt-5.6-luna", [responses])
    answer = _text("rescued", partial=False)
    fallback = _ScriptedLlm("gemini-fallback", [[answer]])
    wrapper, events = _wrap(primary, fallback)

    result = [r async for r in wrapper.generate_content_async(SimpleNamespace(), stream=stream)]

    assert result[-1] is answer
    assert all(r.partial or r is answer for r in result)
    assert primary.calls == 1
    assert [name for name, _ in events] == [rl.MODEL_FALLBACK_EVENT]


async def test_all_empty_is_a_typed_failure_instead_of_success():
    wrapper, _ = _wrap(_ScriptedLlm("openai/empty", [[LlmResponse()]]))
    with pytest.raises(ModelTurnError) as info:
        _ = [r async for r in wrapper.generate_content_async(None, stream=True)]
    assert info.value.error_class.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    "response",
    [
        LlmResponse(error_code="SAFETY", finish_reason=types.FinishReason.SAFETY),
        LlmResponse(finish_reason=types.FinishReason.SAFETY),
        LlmResponse(finish_reason=types.FinishReason.MAX_TOKENS),
        LlmResponse(interrupted=True),
    ],
)
async def test_explicit_block_or_interruption_is_preserved_without_fallback(response):
    primary = _ScriptedLlm("first", [[response]])
    fallback = _ScriptedLlm("second", [[_text("must not run")]])
    wrapper, events = _wrap(primary, fallback)
    assert [r async for r in wrapper.generate_content_async(None, stream=True)] == [response]
    assert fallback.calls == 0 and events == []


@pytest.mark.parametrize(
    "part",
    [
        types.Part(function_call=types.FunctionCall(name="search", args={"query": "clause"})),
        types.Part(file_data=types.FileData(file_uri="gs://private.example/result.txt", mime_type="text/plain")),
    ],
)
async def test_tool_call_and_file_only_results_are_real_output(part):
    response = LlmResponse(content=types.Content(role="model", parts=[part]))
    wrapper, events = _wrap(_ScriptedLlm("first", [[response]]))
    assert [r async for r in wrapper.generate_content_async(None, stream=True)] == [response]
    assert events == []


async def test_failed_stream_after_visible_output_never_replays_answer():
    import litellm

    primary = _ScriptedLlm(
        "openai/first", [[_text("already sent"), litellm.RateLimitError("rate", model="first", llm_provider="openai")]]
    )
    fallback = _ScriptedLlm("second", [[_text("duplicate")]])
    wrapper, _ = _wrap(primary, fallback)
    with pytest.raises(ModelTurnError):
        _ = [r async for r in wrapper.generate_content_async(None, stream=True)]
    assert primary.calls == 1 and fallback.calls == 0


async def test_healthy_sibling_does_not_erase_stalled_model_cooldown(monkeypatch):
    monkeypatch.setenv(rl.FIRST_TOKEN_DEADLINE_ENV, "0.01")
    primary = _StallingLlm("gemini-primary", [[_Stall()]], provider_hint="gemini")
    fallback = _ScriptedLlm("gemini-sibling", [[_text("ok")]], provider_hint="gemini")
    wrapper, events = _wrap(primary, fallback)
    for _ in range(rl.COOLDOWN_THRESHOLD + 1):
        _ = [r async for r in wrapper.generate_content_async(None, stream=True)]
    assert primary.calls == rl.COOLDOWN_THRESHOLD
    assert fallback.calls == rl.COOLDOWN_THRESHOLD + 1
    assert events[-1][1]["reason"] == "model_cooldown"


def test_tool_schema_size_is_included_in_prefill_allowance():
    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name="search", description="x" * 100_000)])]
    )
    assert rl._prompt_chars(SimpleNamespace(config=config)) >= 100_000
