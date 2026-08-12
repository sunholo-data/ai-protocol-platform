"""End-to-end regression: documents attached on the wire reach the agent.

User report 2026-04-28: two doc tabs were ticked in the chat UI, but the
agent replied "I couldn't find an artifact named ..." and tried
``retrieve_artifact`` instead — proof the documents never entered the
agent's context.

Existing tests cover two slices in isolation:

  * ``test_stream_skill_passes_all_document_ids_through_to_agent`` —
    the wire (forwardedProps.document_ids) → ``RunAgentInput.state``
    hop. Mocks ``ADKAgent.run`` so it never exercises ag_ui_adk.

  * ``test_document_loader.py`` — the loader callback called directly
    with a fabricated context. Never goes through ag_ui_adk's session
    state update.

The gap between them is exactly where this regression lives: does
``input.state.document_ids`` actually become ``session.state.document_ids``
inside ag_ui_adk, and does the real ``before_agent_callback`` chain
(``make_document_loader``) then save the artifacts the injector
expects? This test closes that gap by running the full chain — real
``InMemorySessionService`` + ``InMemoryArtifactService``, real
``build_agui_adk_agent``, real ``make_document_loader`` — and asserting
on the side effects (session state + saved artifacts).

The agent under test is a no-op ``BaseAgent`` subclass with the loader
attached: enough to fire ``before_agent_callback`` without needing a
Vertex round-trip.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService

from adk.agui import APP_NAME, build_agui_adk_agent
from adk.callbacks import _STATE_DOCS_LOADED, make_document_loader

_BLOCKS_BY_DOC: dict[str, list[dict[str, Any]]] = {
    "doc-volunteers": [
        {"type": "heading", "text": "Volunteers", "page": 1, "block_id": "v1"},
        {"type": "paragraph", "text": "Volunteer roster.", "page": 1, "block_id": "v2"},
    ],
    "doc-claim": [
        {"type": "heading", "text": "Insurance Claim", "page": 1, "block_id": "c1"},
        {"type": "paragraph", "text": "Data Crime coverage.", "page": 1, "block_id": "c2"},
    ],
}


def _blocks_for(doc_id: str, *_args: Any, **_kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
    return ("ignored", _BLOCKS_BY_DOC[doc_id])


@pytest.fixture(autouse=True)
def _reset_ag_ui_adk_singletons():
    """Reset ag_ui_adk's ``SessionManager`` between tests.

    ``SessionManager`` uses a process-wide singleton (``__new__`` returns
    ``cls._instance``). Without this reset, the second test's
    ``ADKAgent`` reuses the first test's bound ``session_service``, so
    its fresh ``InMemorySessionService`` never sees any sessions and
    the assertions fail for the wrong reason.
    """
    from ag_ui_adk.session_manager import SessionManager

    SessionManager.reset_instance()
    yield
    SessionManager.reset_instance()


class _NoOpAgent(BaseAgent):
    """Stub agent that runs the callback chain but yields no events.

    The before_agent_callback fires before ``_run_async_impl``, so the
    document loader still runs without us needing a real LLM.
    """

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        return
        yield  # unreachable; required to make this an async generator


@pytest.mark.asyncio
async def test_attached_documents_reach_session_state_and_save_artifacts():
    """When the frontend posts ``state.document_ids = [...]`` for a
    chat turn, by the time the run ends the documents must be:

      1. Mirrored into the persisted session state, so the next turn's
         loader sees them in ``state['document_ids']``.
      2. Saved as ``doc:{id}.json`` artifacts, so the
         ``before_model_callback`` (``make_document_injector``) can
         inline them on the next LLM call.

    Either being absent reproduces the user-visible bug from
    2026-04-28 ("I couldn't find an artifact named ...").
    """
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    user_id = "caller-uid"
    thread_id = "thread-attached-docs"

    agent = _NoOpAgent(
        name="doc_loader_under_test",
        before_agent_callback=make_document_loader(),
    )

    agui = build_agui_adk_agent(
        agent,
        user_id=user_id,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    run_input = RunAgentInput(
        thread_id=thread_id,
        run_id="run-1",
        messages=[
            UserMessage(id="m1", role="user", content="the claim incident one"),
        ],
        state={"document_ids": ["doc-volunteers", "doc-claim"]},
        tools=[],
        context=[],
        forwarded_props={},
    )

    with patch(
        "tools.documents.context.build_document_context",
        side_effect=_blocks_for,
    ):
        async for _evt in agui.run(run_input):
            _ = _evt  # drain

    # 1. ag_ui_adk must have applied input.state to the session.
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
    )
    assert session is not None, "ag_ui_adk did not create the backing session"
    assert sorted(session.state.get("document_ids") or []) == [
        "doc-claim",
        "doc-volunteers",
    ], (
        "frontend-set document_ids must reach session state via "
        "ag_ui_adk's update_session_state — got "
        f"{session.state.get('document_ids')!r}"
    )

    # 2. The before_agent_callback (loader) must have fired and recorded
    #    every doc as loaded.
    assert sorted(session.state.get(_STATE_DOCS_LOADED) or []) == [
        "doc-claim",
        "doc-volunteers",
    ], (
        "make_document_loader must run on the first turn for newly "
        "attached docs and record them in app:docs_loaded — got "
        f"{session.state.get(_STATE_DOCS_LOADED)!r}"
    )

    # 3. Each doc must be a session-scoped artifact the injector can
    #    later load by filename. Missing artifacts are exactly what
    #    leaves the agent saying "I couldn't find ...".
    for doc_id in ("doc-volunteers", "doc-claim"):
        artifact = await artifact_service.load_artifact(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=thread_id,
            filename=f"doc:{doc_id}.json",
        )
        assert artifact is not None, (
            f"loader did not save doc:{doc_id}.json — this is the "
            "regression: tabs ticked, but agent has no document context"
        )
        assert artifact.inline_data is not None
        assert artifact.inline_data.mime_type == "application/json"
        blocks = json.loads(artifact.inline_data.data)
        assert blocks == _BLOCKS_BY_DOC[doc_id], f"artifact for {doc_id} has the wrong block payload"


@pytest.mark.asyncio
async def test_first_turn_load_failure_self_heals_on_second_turn():
    """End-to-end self-heal: turn 1 the loader fails (Firestore hiccup);
    turn 2 the user sends another message and the loader succeeds.

    Pre-fix (the user-visible bug from 2026-04-28): turn 1 marked the
    failed id in ``_STATE_DOCS_LOADED`` even though no artifact was
    saved. The injector then iterated the loaded list, called
    ``load_artifact("doc:{id}.json")``, got nothing, silently skipped
    — and the LLM saw no document context, called retrieve_artifact,
    and replied "I couldn't find an artifact named ...".

    Post-fix: turn 1's failure leaves the id OUT of
    ``_STATE_DOCS_LOADED``; turn 2 retries it and the artifact is
    saved. This test runs both turns through the real ag_ui_adk
    pipeline + real loader, asserting the artifact actually appears
    after turn 2.
    """
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    user_id = "caller-uid"
    thread_id = "thread-self-heal"

    agent = _NoOpAgent(
        name="doc_loader_under_test",
        before_agent_callback=make_document_loader(),
    )
    agui = build_agui_adk_agent(
        agent,
        user_id=user_id,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    # Turn 1: build_document_context raises — simulates the transient
    # Firestore failure that strands the doc in the pre-fix code path.
    turn1 = RunAgentInput(
        thread_id=thread_id,
        run_id="run-1",
        messages=[UserMessage(id="m1", role="user", content="tell me about this")],
        state={"document_ids": ["doc-claim"]},
        tools=[],
        context=[],
        forwarded_props={},
    )
    with patch(
        "tools.documents.context.build_document_context",
        side_effect=RuntimeError("Firestore unavailable"),
    ):
        async for _evt in agui.run(turn1):
            _ = _evt

    artifact_after_turn1 = await artifact_service.load_artifact(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
        filename="doc:doc-claim.json",
    )
    assert artifact_after_turn1 is None, "turn 1 raised; no artifact should have been saved"

    session_after_turn1 = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
    )
    assert session_after_turn1 is not None
    assert "doc-claim" not in (session_after_turn1.state.get(_STATE_DOCS_LOADED) or []), (
        "the failed id must NOT be in _STATE_DOCS_LOADED — the whole "
        "point of the self-healing fix is that turn 2 re-reads document_ids "
        "and retries doc-claim because it's still 'unloaded'"
    )

    # Turn 2: backend recovered. Real build_document_context now returns blocks.
    turn2 = RunAgentInput(
        thread_id=thread_id,
        run_id="run-2",
        messages=[UserMessage(id="m2", role="user", content="ok now tell me")],
        state={"document_ids": ["doc-claim"]},
        tools=[],
        context=[],
        forwarded_props={},
    )
    with patch(
        "tools.documents.context.build_document_context",
        side_effect=_blocks_for,
    ):
        async for _evt in agui.run(turn2):
            _ = _evt

    artifact = await artifact_service.load_artifact(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
        filename="doc:doc-claim.json",
    )
    assert artifact is not None, (
        "self-heal failed: turn 2 should have retried the previously-failed doc and saved its artifact"
    )

    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
    )
    assert session is not None
    assert "doc-claim" in (session.state.get(_STATE_DOCS_LOADED) or [])


@pytest.mark.asyncio
async def test_user_adds_doc_mid_session_loads_only_the_new_one():
    """Two-turn scenario closer to the screenshot: turn 1 has one doc
    attached, turn 2 the user opens a second tab and the frontend sends
    both ids. The loader must:

      * Merge the new id into ``session.state['document_ids']``.
      * Save an artifact for the *new* id (docA was already loaded on
        turn 1, so we don't redo it).
      * Leave docA's artifact intact.

    Reproduces the user-visible failure mode: tabs ticked but the agent
    can't see them, because the second turn's state delta never reached
    the loader. The pre-fix symptom is that the new doc's artifact is
    absent on turn 2 even though the frontend sent its id.
    """
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    user_id = "caller-uid"
    thread_id = "thread-mid-session-attach"

    agent = _NoOpAgent(
        name="doc_loader_under_test",
        before_agent_callback=make_document_loader(),
    )
    agui = build_agui_adk_agent(
        agent,
        user_id=user_id,
        session_service=session_service,
        artifact_service=artifact_service,
    )

    # Turn 1: one doc attached.
    turn1 = RunAgentInput(
        thread_id=thread_id,
        run_id="run-1",
        messages=[UserMessage(id="m1", role="user", content="tell me about this")],
        state={"document_ids": ["doc-volunteers"]},
        tools=[],
        context=[],
        forwarded_props={},
    )
    with patch(
        "tools.documents.context.build_document_context",
        side_effect=_blocks_for,
    ):
        async for _evt in agui.run(turn1):
            _ = _evt  # drain the SSE stream — the loader runs as a side effect

    # Turn 2: user opens a second tab; frontend now sends both ids.
    turn2 = RunAgentInput(
        thread_id=thread_id,
        run_id="run-2",
        messages=[UserMessage(id="m2", role="user", content="the claim incident one")],
        state={"document_ids": ["doc-volunteers", "doc-claim"]},
        tools=[],
        context=[],
        forwarded_props={},
    )
    with patch(
        "tools.documents.context.build_document_context",
        side_effect=_blocks_for,
    ):
        async for _evt in agui.run(turn2):
            _ = _evt  # drain

    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=thread_id,
    )
    assert session is not None
    assert sorted(session.state.get("document_ids") or []) == [
        "doc-claim",
        "doc-volunteers",
    ], (
        "mid-session document attachment must merge into session state. "
        "If ag_ui_adk did not propagate the turn-2 state delta this list "
        "would still only have doc-volunteers — exactly the bug shape "
        "the user reported."
    )
    assert sorted(session.state.get(_STATE_DOCS_LOADED) or []) == [
        "doc-claim",
        "doc-volunteers",
    ]

    for doc_id in ("doc-volunteers", "doc-claim"):
        artifact = await artifact_service.load_artifact(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=thread_id,
            filename=f"doc:{doc_id}.json",
        )
        assert artifact is not None, (
            f"doc:{doc_id}.json must exist after turn 2 — newly added ids must load on the turn they arrive, not later"
        )
