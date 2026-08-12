"""Regression test for the confirm-handoff SPAM LOOP (2026-07-22).

Symptom (reported live): a lite ONE front-door on ``gemini-3.5-flash-lite``
emitted the "Hand this conversation to PPA Obligation Analysis?" confirm card
over and over — 10+ stacked cards and 10+ "Delegated to …" markers for one turn.

Root cause: the confirm-floor handoff policy short-circuits ``transfer_to_agent``
by returning the elicitation envelope AS the tool result, but did NOT mark the
turn final. ADK's flow only stops re-calling the model when the last event
``is_final_response()`` — which is true iff ``actions.skip_summarization`` is set
(see ``google.adk.events.Event.is_final_response`` +
``base_llm_flow.run_async``). Without the flag the model is re-invoked with the
envelope, and a well-behaved model re-issues ``transfer_to_agent`` → another card
→ repeat. The fix (``make_elicitation_result`` sets ``skip_summarization``,
mirroring ADK's own ``get_user_choice``) makes the turn END on the first card.

This test drives the REAL ADK flow with a stub model that ALWAYS emits
``transfer_to_agent``. With the fix, the model is called exactly ONCE and exactly
one confirm elicitation is produced. Without it, the stub would be re-invoked
every round (capped here so a regression FAILS the assert instead of hanging).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.agent import make_handoff_policy_callback
from adk.elicitation import NEEDS_INPUT_KEY
from db.models import DelegateRule

# Part of the ADK-contract conformance gate (`make adk-conformance`): a hermetic
# "real ADK flow" guard. See docs/design/v6.17.0/adk-contract-checklist.md (C1).
pytestmark = pytest.mark.adk_contract

_TARGET_AGENT = "one_obligation_analysis"
_LOOP_CAP = 6  # safety valve: a regression stops here (assert fails) rather than hang

# Front-door call count — module-level, not a pydantic field (BaseLlm is a
# pydantic model, and a mutable attr on it fights both pydantic and ruff).
_DOOR_CALLS: list[int] = []


class _AlwaysTransfers(BaseLlm):
    """A stub front-door: every time it is asked, it calls transfer_to_agent —
    exactly the behaviour that turns a missing turn-pause into a runaway loop."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        _DOOR_CALLS.append(1)
        if len(_DOOR_CALLS) > _LOOP_CAP:
            # Regression guard: stop emitting so the test asserts, never hangs.
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="(capped)")]))
            return
        fc = types.FunctionCall(name="transfer_to_agent", args={"agent_name": _TARGET_AGENT})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))


class _NeverCalled(BaseLlm):
    """The confirm delegate's model — must never run, since a confirm handoff is
    short-circuited before any real transfer."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        raise AssertionError("confirm-floor delegate must NOT run — the transfer is short-circuited")
        yield  # unreachable; makes this an async generator


def _run_one_turn():
    _DOOR_CALLS.clear()
    delegate = LlmAgent(name=_TARGET_AGENT, model=_NeverCalled(model="stub-delegate"))
    # The delegate is a sub_agent so ADK exposes `transfer_to_agent` (with this
    # target in its enum). The map keys on the sanitized agent name — floor=confirm.
    skill_stub = SimpleNamespace(
        skill_id="26124699-f558",
        display_name="PPA Obligation Analysis",
        name="PPA Obligation Analysis",
        description="Deep PPA settlement-obligation analysis.",
    )
    delegate_map = {_TARGET_AGENT: (skill_stub, DelegateRule(skill="26124699-f558", floor="confirm"))}
    door = LlmAgent(
        name="door",
        model=_AlwaysTransfers(model="stub-door"),
        sub_agents=[delegate],
        before_tool_callback=make_handoff_policy_callback("door", delegate_map),
    )

    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="u", app_name="test")
    runner = Runner(agent=door, session_service=session_service, app_name="test")
    message = types.Content(role="user", parts=[types.Part.from_text(text="analyse the obligations")])
    return list(
        runner.run(
            new_message=message,
            user_id="u",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )


def _confirm_responses(events) -> list:
    """Function-response events carrying the confirm elicitation envelope."""
    out = []
    for e in events:
        for p in (e.content.parts if e.content else []) or []:
            fr = getattr(p, "function_response", None)
            resp = getattr(fr, "response", None) if fr else None
            if isinstance(resp, dict) and resp.get(NEEDS_INPUT_KEY) is True:
                out.append(resp)
    return out


def test_confirm_handoff_emits_exactly_one_card_and_stops():
    events = _run_one_turn()

    # (1) The turn ENDED after the first confirm card — the front-door model was
    #     asked exactly once. Before the fix this climbs to the loop cap.
    assert len(_DOOR_CALLS) == 1, (
        f"front-door re-invoked {len(_DOOR_CALLS)}x — the confirm card did not end the turn "
        f"(skip_summarization missing → the reported spam loop)"
    )

    # (2) Exactly one confirm elicitation was produced (one card, not a stack).
    cards = _confirm_responses(events)
    assert len(cards) == 1, f"expected exactly one confirm card, got {len(cards)}"
    assert cards[0]["elicitation"]["kind"] == "confirm"


def test_confirm_response_event_is_marked_final():
    """The mechanism, asserted directly: the function-response event that carries
    the confirm card must be is_final_response() — that is the exact flag ADK's
    outer loop checks to stop re-invoking the model."""
    events = _run_one_turn()
    final_confirm = [
        e
        for e in events
        if e.get_function_responses()
        and any((getattr(r, "response", {}) or {}).get(NEEDS_INPUT_KEY) is True for r in e.get_function_responses())
    ]
    assert final_confirm, "no confirm function-response event found"
    assert final_confirm[-1].is_final_response(), (
        "confirm card event must be final (skip_summarization) — else the loop"
    )
