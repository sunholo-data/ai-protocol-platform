"""ADK-contract guard: what a `before_tool_callback` shares with the tool it guards.

Two properties of the before_tool seam that our callbacks lean on:

1. **Once per call.** ``make_permission_enforcer`` emits one ``Calling X…``
   STAGE_PROGRESS label per tool call, and the handoff floor policy decides per
   ``transfer_to_agent`` call. When the model emits a PARALLEL batch, each call
   must get exactly one callback invocation with its own args.
2. **Contextvar visibility.** Our per-request state rides ContextVars — the
   ``LatencyTracker`` (contract C4: ``set_current_tracker``/``get_current_tracker``)
   is the load-bearing one. Whether a contextvar SET in the callback is visible
   inside the tool body decides whether a callback can hand per-call context to
   the tool that way.

ADK 2.x moves this seam (changelog 650: the callback runs inside per-tool
execution rather than the prepare phase; 651: contextvars set in it now
propagate to the tool). This test pins what 1.31.1 does, so the bump shows the
difference instead of a user finding it.

Drives the REAL Runner with a stub model that emits a 3-call parallel batch,
through our real before_tool composer (``compose_before_tool_callbacks``).

Part of `make adk-conformance`. See docs/design/template/template-adk-2-regression-gate.md.
"""

from __future__ import annotations

import contextvars

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.mcp_observability import compose_before_tool_callbacks, make_mcp_before_tool_callback

pytestmark = pytest.mark.adk_contract

_BATCH = ("alpha", "beta", "gamma")

# Set by the callback, read by the tool. Test-local so the run is hermetic; it
# stands in for the production LatencyTracker contextvar.
_CALL_TAG: contextvars.ContextVar[str | None] = contextvars.ContextVar("_CALL_TAG", default=None)

# Module-level recorders (BaseLlm is a pydantic model — see the template).
_MODEL_CALLS: list[int] = []
_CALLBACK_SEEN: list[str] = []
_TOOL_SAW: dict[str, str | None] = {}


def tag_probe(tag: str) -> dict:
    """Report the contextvar value this tool call sees.

    Args:
        tag: the call's tag.
    """
    _TOOL_SAW[tag] = _CALL_TAG.get()
    return {"tag": tag}


class _ParallelBatch(BaseLlm):
    """First call: three parallel `tag_probe` calls in one response. Second call
    (after the merged function responses): a text answer that ends the turn."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _MODEL_CALLS.append(1)
        if len(_MODEL_CALLS) > 1:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="done")]))
            return
        parts = [
            types.Part(function_call=types.FunctionCall(id=f"call-{tag}", name="tag_probe", args={"tag": tag}))
            for tag in _BATCH
        ]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def _set_tag(tool, args: dict, tool_context=None) -> None:
    """A before_tool callback that sets per-call context and lets the call proceed."""
    if getattr(tool, "name", "") != "tag_probe":
        return None
    tag = str((args or {}).get("tag"))
    _CALLBACK_SEEN.append(tag)
    _CALL_TAG.set(f"set-by-callback:{tag}")
    return None


def _run_one_turn() -> list:
    _MODEL_CALLS.clear()
    _CALLBACK_SEEN.clear()
    _TOOL_SAW.clear()
    agent = LlmAgent(
        name="probe",
        model=_ParallelBatch(model="stub-probe"),
        tools=[tag_probe],
        before_tool_callback=compose_before_tool_callbacks(_set_tag, make_mcp_before_tool_callback()),
    )
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="u", app_name="test")
    runner = Runner(agent=agent, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="probe three times")])
    return list(runner.run(new_message=message, user_id="u", session_id=session.id))


def test_callback_runs_once_per_call_in_a_parallel_batch():
    _run_one_turn()
    assert sorted(_CALLBACK_SEEN) == sorted(_BATCH), (
        f"before_tool_callback must run exactly once per parallel call, saw {_CALLBACK_SEEN!r}"
    )
    assert sorted(_TOOL_SAW) == sorted(_BATCH), f"every call in the batch must execute, ran {sorted(_TOOL_SAW)!r}"
    assert len(_MODEL_CALLS) == 2, f"expected batch + summary (2 model calls), got {len(_MODEL_CALLS)}"


def test_contextvar_set_in_callback_reaches_its_own_tool_only():
    """1.31.1 behaviour, pinned: the callback and its tool share one context per
    call, so the tool sees the value its OWN callback set — and never a sibling
    call's value (each parallel call is its own task with its own context copy)."""
    _run_one_turn()
    assert _TOOL_SAW == {tag: f"set-by-callback:{tag}" for tag in _BATCH}, (
        f"contextvar visibility changed: tool saw {_TOOL_SAW!r} — a callback-set contextvar "
        "no longer reaches its tool, or leaks across parallel calls (ADK 650/651)"
    )


def test_contextvar_set_in_callback_does_not_leak_into_the_turn():
    """The per-call context is discarded after the call: nothing a tool callback
    sets is visible to the caller of the Runner."""
    _run_one_turn()
    assert _CALL_TAG.get() is None
