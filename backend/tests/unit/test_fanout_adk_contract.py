"""ADK-contract guard: parallel fan-out rests on TWO framework behaviours.

Both are ADK's, not ours, and a `google-adk` bump could change either without
any error we would notice — the fan-out would keep returning correct answers,
just serially or as the wrong user. That is why they are asserted here rather
than trusted:

  C-FANOUT-1  Multiple function calls in ONE model response are executed
              CONCURRENTLY (`flows/llm_flows/functions.py` builds a task per
              call and `asyncio.gather`s them). If this regresses, a 3-way
              fan-out silently costs 3x the wall-clock and the merged answer
              looks identical — only timing can tell.

  C-FANOUT-2  `AgentTool.run_async` runs the child in the CALLER's identity and
              forwards its state deltas back. Fan-out has no identity
              propagation of its own precisely because of this; if it regresses,
              branches would run as the wrong principal and read the wrong
              documents.

Part of `make adk-conformance`. See docs/design/v6.31.0/parallel-skill-fanout.md
(M0 measured both against live models; these guards keep them true hermetically).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool, FunctionTool
from google.genai import types

pytestmark = pytest.mark.adk_contract

APP = "fanout_contract"
BRANCH_SLEEP = 0.4
N_BRANCHES = 3

# When each branch tool entered and left, recorded from inside the gathered
# tasks — overlap here IS the concurrency claim.
_SPANS: list[tuple[float, float]] = []


class _BatchingModel(BaseLlm):
    """Emits N function calls in ONE response, then a final text.

    This is what every model measured in M0 actually did (gemini-3-5-flash-lite,
    gemini-3-8-flash, gpt-5.6-luna). Faking it keeps the guard hermetic while
    still driving ADK's real function-call execution path.
    """

    async def generate_content_async(self, llm_request, stream: bool = False):
        called = any(
            getattr(p, "function_response", None) for c in (llm_request.contents or []) for p in (c.parts or [])
        )
        if called:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="merged")]))
            return
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(name="branch", args={"topic": f"t{i}"}) for i in range(N_BRANCHES)
                ],
            )
        )


async def branch(topic: str) -> str:
    """Run one branch.

    Args:
        topic: the branch's objective.
    """
    t0 = time.monotonic()
    await asyncio.sleep(BRANCH_SLEEP)
    _SPANS.append((t0, time.monotonic()))
    return f"done:{topic}"


@pytest.fixture(autouse=True)
def _clean():
    _SPANS.clear()
    yield
    _SPANS.clear()


async def _run(agent: LlmAgent, user_id: str = "caller-1") -> None:
    runner = Runner(app_name=APP, agent=agent, session_service=InMemorySessionService())
    session = await runner.session_service.create_session(app_name=APP, user_id=user_id)
    async for _ in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part.from_text(text="go")]),
    ):
        pass


@pytest.mark.asyncio
async def test_c_fanout_1_parallel_function_calls_run_concurrently():
    """N calls in one response must overlap, not queue."""
    agent = LlmAgent(
        name="parent",
        model=_BatchingModel(model="fake"),
        instruction="x",
        tools=[FunctionTool(branch)],
    )
    await _run(agent)

    assert len(_SPANS) == N_BRANCHES, f"expected {N_BRANCHES} branches, got {len(_SPANS)}"
    span = max(e for _, e in _SPANS) - min(s for s, _ in _SPANS)
    serial = BRANCH_SLEEP * N_BRANCHES
    # Generous bound: concurrent is ~BRANCH_SLEEP, serial is ~3x that. Anything
    # under half the serial sum cannot have been sequential.
    assert span < serial * 0.5, (
        f"branches took {span:.2f}s across {N_BRANCHES} calls of {BRANCH_SLEEP}s "
        f"(serial would be ~{serial:.2f}s): ADK appears to be SERIALISING parallel "
        "function calls. Fan-out's whole benefit is gone — check "
        "google/adk/flows/llm_flows/functions.py for the asyncio.gather."
    )


@pytest.mark.asyncio
async def test_c_fanout_2_agent_tool_runs_the_child_as_the_caller():
    """The branch must see the CALLER's user_id — fan-out relies on this
    instead of propagating identity itself."""
    seen: list[str] = []

    class _Child(BaseLlm):
        async def generate_content_async(self, llm_request, stream: bool = False):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]))

    class _CallsWorkerOnce(BaseLlm):
        async def generate_content_async(self, llm_request, stream: bool = False):
            called = any(
                getattr(p, "function_response", None) for c in (llm_request.contents or []) for p in (c.parts or [])
            )
            if called:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="merged")]))
                return
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_function_call(name="worker", args={"request": "go"})],
                )
            )

    class _CapturingAgentTool(AgentTool):
        async def run_async(self, *, args, tool_context):
            seen.append(tool_context._invocation_context.user_id)
            return await super().run_async(args=args, tool_context=tool_context)

    child = LlmAgent(name="worker", model=_Child(model="fake"), instruction="x")
    parent = LlmAgent(
        name="parent",
        model=_CallsWorkerOnce(model="fake"),
        instruction="x",
        tools=[_CapturingAgentTool(agent=child)],
    )
    await _run(parent, user_id="alice-uid")

    assert seen == ["alice-uid"], (
        f"AgentTool ran the child as {seen!r}, not the caller. Fan-out branches "
        "would read the wrong user's documents — see AgentTool.run_async."
    )
