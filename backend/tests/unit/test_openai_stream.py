"""Drive real ADK/LiteLLM conversion with synthetic Responses SSE frames.

The failed-event shape is documented in OpenAI's streaming API reference.
Only the HTTP boundary is replaced; the pinned bridge and ADK converter run.
"""

import json

import pytest
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from litellm.completion_extras.litellm_responses_transformation.transformation import (
    OpenAiResponsesToChatCompletionStreamIterator,
)

from adk.agent import resolve_model
from adk.model_errors import classify
from adk.openai_stream import OpenAIResponsesClient


class _Frames:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield "data: " + json.dumps(event)

    async def aclose(self):
        self.closed = True


class _BridgeResult:
    def __init__(self, frames):
        self.completion_stream = OpenAiResponsesToChatCompletionStreamIterator(frames, sync_stream=False)

    def __aiter__(self):
        return self.completion_stream.__aiter__()


def _request():
    return LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Reply exactly TEST.")])])


@pytest.mark.parametrize(
    ("code", "expected", "transient", "fallbackable"),
    [
        ("rate_limit_exceeded", "MODEL_RATE_LIMITED", True, True),
        ("server_error", "MODEL_UNAVAILABLE", True, True),
        ("insufficient_quota", "MODEL_UNAVAILABLE", False, True),
        ("invalid_api_key", "MODEL_AUTH_FAILED", False, True),
        ("invalid_prompt", "MODEL_REQUEST_INVALID", False, False),
        ("bio_policy", "MODEL_REQUEST_INVALID", False, False),
    ],
)
async def test_failed_event_reaches_classifier_instead_of_empty_stop(
    monkeypatch, code, expected, transient, fallbackable
):
    frames = _Frames([{"type": "response.failed", "response": {"status": "failed", "error": {"code": code}}}])

    async def complete(*args, **kwargs):
        return _BridgeResult(frames)

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    llm = LiteLlm(model="openai/gpt-5.6-luna", llm_client=OpenAIResponsesClient())
    with pytest.raises(Exception) as info:
        _ = [r async for r in llm.generate_content_async(_request(), stream=True)]
    result = classify(info.value)
    assert (result.code, result.transient, result.fallbackable) == (expected, transient, fallbackable)
    assert frames.closed


async def test_pinned_bridge_alone_loses_failed_event(monkeypatch):
    """Keep proof of the dependency bug so a future upgrade can remove the shim."""
    frames = _Frames([{"type": "response.failed", "response": {"status": "failed", "error": {"code": "server_error"}}}])

    async def complete(*args, **kwargs):
        return _BridgeResult(frames)

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    llm = LiteLlm(model="openai/gpt-5.6-luna")
    result = [r async for r in llm.generate_content_async(_request(), stream=True)]
    assert result[-1].finish_reason == types.FinishReason.STOP
    assert not result[-1].content.parts


async def test_successful_text_and_tool_streams_survive_bridge(monkeypatch):
    frames = _Frames(
        [
            {"type": "response.created"},
            {"type": "response.reasoning_summary_text.delta", "summary_index": 0, "delta": "Thinking"},
            {"type": "response.output_text.delta", "delta": "TEST"},
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call", "name": "search", "call_id": "call_1"},
            },
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"query":"clause"}'},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{"type": "function_call"}],
                    "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
                },
            },
        ]
    )

    async def complete(*args, **kwargs):
        assert kwargs["model"] == "openai/responses/gpt-6.1-sol"
        return _BridgeResult(frames)

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    llm = resolve_model("gpt-6-1-sol")
    result = [r async for r in llm.generate_content_async(_request(), stream=True)]
    final = result[-1]
    parts = final.content.parts
    assert any(p.text == "TEST" for p in parts)
    assert any(p.function_call and p.function_call.args == {"query": "clause"} for p in parts)
    assert any(p.thought and p.text == "Thinking" for r in result for p in (r.content.parts if r.content else []))
    assert final.usage_metadata.total_token_count == 14
    assert frames.closed


def test_existing_nonreasoning_model_keeps_chat_client():
    assert type(resolve_model("gpt-4o").llm_client) is LiteLLMClient


def test_reasoner_uses_responses_without_changing_model_choice():
    model = resolve_model("gpt-6-1-sol")
    assert model.model == "openai/gpt-6.1-sol"
    assert isinstance(model.llm_client, OpenAIResponsesClient)
    assert model._additional_args["reasoning_effort"] == "high"


@pytest.mark.parametrize("failed", [True, False])
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("with_tool", [True, False])
async def test_real_http_stack_preserves_streamed_provider_error(monkeypatch, failed, stream, with_tool):
    """Exercise routing, Responses parsing, bridge, ADK and classification."""
    import httpx
    import litellm

    seen = []
    response = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": "failed" if failed else "completed",
        "error": {"code": "rate_limit_exceeded", "message": "synthetic rate limit"} if failed else None,
        "model": "gpt-6.1-sol",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }
    events = [{"type": "response.failed", "sequence_number": 0, "response": response}]
    if not failed:
        events = [
            {"type": "response.output_text.delta", "sequence_number": 0, "delta": "TEST"},
            {"type": "response.completed", "sequence_number": 1, "response": response},
        ]
        if with_tool:
            response["output"] = [{"type": "function_call", "name": "search", "call_id": "call_1"}]
            events[1:1] = [
                {"type": "response.output_item.added", "output_index": 0, "item": response["output"][0]},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"query":"clause"}'},
            ]

    async def send(self, request, **kwargs):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=body,
            request=request,
        )

    class EventBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()

        async def aclose(self):
            self.closed = True

    body = EventBody()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://openai.example/v1")
    monkeypatch.setattr(litellm, "disable_aiohttp_transport", True)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    model = resolve_model("gpt-6-1-sol")
    request = _request()
    request.config.http_options = types.HttpOptions(extra_body={"reasoning": {"mode": "pro", "effort": "low"}})
    if failed:
        with pytest.raises(Exception) as info:
            _ = [r async for r in model.generate_content_async(request, stream=stream)]
        assert classify(info.value).code == "MODEL_RATE_LIMITED", str(info.value)
    else:
        results = [r async for r in model.generate_content_async(request, stream=stream)]
        assert results[-1].content.parts[0].text == "TEST"
        assert results[-1].usage_metadata.total_token_count == 14
        if with_tool:
            assert any(
                p.function_call and p.function_call.args == {"query": "clause"} for p in results[-1].content.parts
            )
    assert seen and seen[0][0] == "/v1/responses"
    assert seen[0][1]["model"] == "gpt-6.1-sol"
    assert seen[0][1]["reasoning"] == {"summary": "auto", "effort": "low", "mode": "pro"}
    assert seen[0][1]["stream"] is True
    assert body.closed, "The HTTP stream must close on success and early provider failure"


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.refusal.delta", "delta": "Synthetic refusal"},
        {"type": "response.refusal.done", "refusal": "Synthetic refusal"},
        {"type": "response.content_part.added", "part": {"type": "refusal", "refusal": ""}},
        {
            "type": "response.output_item.done",
            "item": {"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]},
        },
        {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "content_filter"}}},
        {
            "type": "response.completed",
            "response": {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]}]},
        },
    ],
)
async def test_provider_refusal_never_replays_on_fallback(monkeypatch, stream, event):
    from adk.model_errors import ModelTurnError
    from adk.resilient_llm import ResilientLlm
    from tests.unit.test_resilient_llm import _ScriptedLlm, _text

    async def complete(*args, **kwargs):
        assert kwargs["stream"] is True
        return _BridgeResult(_Frames([event]))

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    primary = LiteLlm(model="openai/gpt-5.6-luna", llm_client=OpenAIResponsesClient())
    fallback = _ScriptedLlm("gemini-fallback", [[_text("must not run")]])
    with pytest.raises(ModelTurnError) as info:
        _ = [r async for r in ResilientLlm([primary, fallback]).generate_content_async(_request(), stream=stream)]
    assert info.value.error_class.code == "MODEL_REQUEST_INVALID"
    assert info.value.error_class.fallbackable is False
    assert fallback.calls == 0
