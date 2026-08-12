"""ADK-contract guard C3: a ResilientLlm fallback member must be called with
`llm_request.model` rewritten to ITS OWN id.

ADK stamps `llm_request.model` ONCE with the chain's primary. If a fallback
member is left with the primary's id it is mis-called — a Gemini fallback handed
"claude-opus-4-8" 404s on Vertex's anthropic publisher path (ROOT CAUSE of the
2026-07-16 fallback failures: every fallback target 404'd because the id was
never rewritten). This drives the real `ResilientLlm.generate_content_async`
seam: the primary raises a fallbackable error, and we assert the fallback saw its
OWN model id, not the primary's.

Part of `make adk-conformance`. See docs/design/v6.17.0/adk-contract-checklist.md.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adk.resilient_llm import ResilientLlm, reset_provider_health

pytestmark = pytest.mark.adk_contract

_PRIMARY_ID = "claude-opus-4-8"
_FALLBACK_ID = "gemini-2.5-flash"

# The model id the fallback was actually invoked with (module-level to avoid
# pydantic-field friction on the BaseLlm subclass).
_SEEN: list[str | None] = []


class _FailingPrimary(BaseLlm):
    """Primary that fails before yielding — an unclassified error is
    non-transient + fallbackable, so ResilientLlm hops once to the fallback."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        raise RuntimeError("primary is down")
        yield  # unreachable; makes this an async generator


class _RecordingFallback(BaseLlm):
    """Fallback that records the `llm_request.model` it was called with."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _SEEN.append(getattr(llm_request, "model", None))
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))


@pytest.fixture(autouse=True)
def _clean():
    _SEEN.clear()
    reset_provider_health()  # module-global cooldown store — keep the test hermetic
    yield
    reset_provider_health()


def _has_text(response, needle: str) -> bool:
    parts = getattr(getattr(response, "content", None), "parts", None) or []
    return any(needle in (getattr(p, "text", "") or "") for p in parts)


async def test_fallback_member_called_with_its_own_model_id():
    chain = [_FailingPrimary(model=_PRIMARY_ID), _RecordingFallback(model=_FALLBACK_ID)]
    resilient = ResilientLlm(chain, event_sink=lambda *a, **k: None)

    # ADK stamps the request with the PRIMARY's id, exactly once.
    llm_request = SimpleNamespace(model=_PRIMARY_ID)
    responses = [r async for r in resilient.generate_content_async(llm_request, stream=False)]

    assert any(_has_text(r, "ok") for r in responses), "fallback should have produced the answer"
    assert _SEEN == [_FALLBACK_ID], (
        f"fallback was called with model={_SEEN!r}, expected [{_FALLBACK_ID!r}] — an un-rewritten "
        f"primary id is the 2026-07-16 cross-provider 404 (Gemini called as claude-opus-4-8)"
    )
    # And the request object itself carries the fallback's id after the hop.
    assert llm_request.model == _FALLBACK_ID
