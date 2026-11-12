"""Preserve Responses API failures across the pinned LiteLLM chat bridge.

LiteLLM 1.85.7's bridge maps unhandled ``response.failed`` events to empty
chat chunks. Validate the raw events on this client's iterator before that
conversion; never patch LiteLLM functions or log response bodies/credentials.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.models.lite_llm import LiteLLMClient

logger = logging.getLogger(__name__)


async def _checked_events(stream: Any, model: str):
    import litellm

    try:
        async for event in stream:
            data = event.model_dump() if hasattr(event, "model_dump") else event
            if isinstance(data, str) and data.startswith("data:"):
                try:
                    data = json.loads(data[5:])
                except ValueError:
                    data = None
            if isinstance(data, dict):
                response = data.get("response") or {}
                refusal = (data.get("part") or {}).get("type") == "refusal" or any(
                    part.get("type") == "refusal"
                    for item in [*(response.get("output") or []), data.get("item") or {}]
                    for part in item.get("content") or []
                )
                filtered = (response.get("incomplete_details") or {}).get("reason") == "content_filter"
                if str(data.get("type", "")).startswith("response.refusal.") or refusal or filtered:
                    # The pinned bridge drops refusal/incomplete frames too.
                    # A declined response must end the turn, never be retried
                    # on a different model as if it were an empty success.
                    raise litellm.BadRequestError(
                        message="OpenAI declined the response", model=model, llm_provider="openai"
                    )
                error = response.get("error") or data.get("error") or {}
                if data.get("type") in ("response.failed", "error") or response.get("status") == "failed":
                    code = error.get("code", "server_error") if isinstance(error, dict) else "server_error"
                    # Do not include provider prose: it may echo request data.
                    message = f"OpenAI streamed response failed ({code})"
                    common = {"model": model, "llm_provider": "openai", "message": message}
                    logger.warning("openai_stream_failed: model=%s code=%s", model, code)
                    if code in ("rate_limit_exceeded", "slow_down"):
                        raise litellm.RateLimitError(**common)
                    if code in ("invalid_api_key", "authentication_error"):
                        raise litellm.AuthenticationError(**common)
                    if code in (
                        "invalid_prompt",
                        "invalid_request_error",
                        "context_length_exceeded",
                        "content_policy_violation",
                        "bio_policy",
                        "misalignment_policy_violation",
                    ):
                        raise litellm.BadRequestError(**common)
                    # Billing/usage caps should fall back without same-model
                    # retries; classifier recognizes their capacity message.
                    if code in (
                        "insufficient_quota",
                        "credit_balance_exhausted",
                        "project_spend_limit_exceeded",
                        "organization_spend_limit_exceeded",
                        "organization_usage_limit_exceeded",
                        "usage_limit_exceeded",
                    ):
                        raise litellm.BadRequestError(
                            message="OpenAI provider usage limit reached", model=model, llm_provider="openai"
                        )
                    raise litellm.ServiceUnavailableError(**common)
            yield event
    finally:
        close = getattr(stream, "aclose", None) or getattr(getattr(stream, "response", None), "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:
                logger.debug("OpenAI stream close failed: %s", type(exc).__name__)


class OpenAIResponsesClient(LiteLLMClient):
    """Use Responses explicitly, including models newer than LiteLLM's table."""

    async def acompletion(self, model: Any, messages: Any, tools: Any, **kwargs: Any):
        import litellm
        from litellm.completion_extras.litellm_responses_transformation.transformation import (
            OpenAiResponsesToChatCompletionStreamIterator,
        )
        from litellm.utils import supports_native_streaming

        api_model = model.removeprefix("openai/")
        # LiteLLM otherwise treats an unknown model as NON-streaming, removes
        # stream=true and fabricates chunks from a whole-response request.
        # Its public registration API fills that capability gap for these
        # explicitly selected OpenAI reasoning models; existing pricing stays.
        if not supports_native_streaming(api_model, custom_llm_provider="openai"):
            litellm.register_model(
                {api_model: {"litellm_provider": "openai", "mode": "responses", "supports_native_streaming": True}}
            )
        # This is an endpoint hint consumed by LiteLLM, not a change to the
        # requested provider model. ADK keeps its canonical model identifier.
        routed_model = model.replace("openai/", "openai/responses/", 1)
        # The pinned chat parameter table does not know GPT-6 yet, and can
        # drop stream/effort before the bridge. Pass the canonical Responses
        # fields together so summary does not overwrite reasoning.effort.
        requested_stream = kwargs.get("stream", False)
        # Use the checked transport for whole-response callers too: the
        # non-streaming bridge also discards refusal content. Aggregate the
        # validated chunks back to its normal ModelResponse interface below.
        kwargs["stream"] = True
        body = dict(kwargs.get("extra_body") or {})
        body["stream"] = True
        reasoning = dict(kwargs.pop("reasoning", None) or {})
        effort = kwargs.pop("reasoning_effort", None)
        if effort is not None:
            reasoning["effort"] = effort
        # Explicit per-request settings take precedence over factory defaults.
        reasoning.update(body.get("reasoning") or {})
        if reasoning:
            body["reasoning"] = reasoning
        kwargs["extra_body"] = body
        result = await super().acompletion(model=routed_model, messages=messages, tools=tools, **kwargs)
        bridge = getattr(result, "completion_stream", None)
        if isinstance(bridge, OpenAiResponsesToChatCompletionStreamIterator):
            bridge.streaming_response = _checked_events(bridge.streaming_response, model)
        if not requested_stream:
            chunks = [chunk async for chunk in result]
            return litellm.stream_chunk_builder(chunks, messages=messages)
        return result
