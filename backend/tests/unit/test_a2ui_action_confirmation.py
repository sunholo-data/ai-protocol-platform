import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adk import a2ui_action_confirmation as confirmation


@pytest.mark.asyncio
async def test_confirmation_uses_configured_resilient_model_without_tools(monkeypatch):
    requests = []

    class Model(BaseLlm):
        model: str = "test"

        async def generate_content_async(self, request, stream=False):
            requests.append(request)
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="Project 1201 is saved and verified.")])
            )

    monkeypatch.setattr(confirmation, "resolve_model_chain", lambda model, fallback: Model())
    text = await confirmation.confirmation_text("pro", "ok", "Created project 1201 and verified membership.")
    assert text == "Project 1201 is saved and verified."
    assert not requests[0].config.tools
    assert any("1201" in (part.text or "") for content in requests[0].contents for part in content.parts)


@pytest.mark.asyncio
async def test_confirmation_model_failure_returns_authoritative_result_without_retrying_action(monkeypatch):
    def unavailable(*args):
        raise ValueError("Model unavailable")

    monkeypatch.setattr(confirmation, "resolve_model_chain", unavailable)
    receipt = "Project 1201 was created; membership verification is incomplete. Do not create again."
    assert await confirmation.confirmation_text("pro", "error", receipt) == receipt
