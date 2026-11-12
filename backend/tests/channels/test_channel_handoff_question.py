"""A channel turn that leaves a hand-off question unasked still asks it.

Live 2026-10-27: the door called the hand-off on Discord and the turn ended
with no text, so the person saw "(no response)".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adk.elicitation import CHANNEL_QUESTIONS, NON_INTERACTIVE
from channels._skill_invoke import collect_reply, stream_skill_events

QUESTION = "Shall I hand this over to **Document Writer**? Reply **yes** to go ahead."


def _run(*deltas: str, ask: bool = True):
    async def fake(**_kwargs):
        assert NON_INTERACTIVE.get() is True  # channels never get a clickable form
        if ask:
            CHANNEL_QUESTIONS.get().append(QUESTION)  # what the hand-off policy records
        for d in deltas:
            yield {"type": "TEXT_MESSAGE_CONTENT", "delta": d}
        yield {"type": "RUN_FINISHED"}

    return fake


async def _reply(fake) -> str:
    with (
        patch("skills.skill_processor.process_skill_request", fake),
        patch("channels._skill_invoke.build_access_context", lambda user: None),
    ):
        return await collect_reply(
            stream_skill_events(skill_id="memo-writer", firebase_uid="u1", message="write a memo", channel_name="discord")
        )


@pytest.mark.asyncio
async def test_silent_turn_gets_the_question_not_no_response():
    assert await _reply(_run()) == QUESTION


@pytest.mark.asyncio
async def test_question_follows_text_that_did_not_ask():
    assert await _reply(_run("Document Writer is the right sheep for this.")) == (
        "Document Writer is the right sheep for this.\n\n" + QUESTION
    )


@pytest.mark.asyncio
async def test_model_that_asked_is_left_alone():
    assert await _reply(_run("Want me to pass this to the Document Writer?")) == (
        "Want me to pass this to the Document Writer?"
    )


@pytest.mark.asyncio
async def test_no_pending_question_no_addition():
    assert await _reply(_run("Hello!", ask=False)) == "Hello!"
