"""ADK-contract guard C2: an ADK artifact save in a callback must be AWAITED.

`callback_context.save_artifact` / `load_artifact` are coroutines. An un-awaited
save silently never writes (a bare coroutine is never scheduled, raises nothing),
so a later `load_artifact` / `retrieve_artifact` 404s — which broke document fetch
(the doc loader appended the id to app:docs_loaded but no artifact was behind it).
Mock tests can't catch it; a real save -> load roundtrip can.

This drives the REAL `make_document_loader` before_agent_callback through a real
Runner + InMemoryArtifactService and asserts the doc artifact it saves is
retrievable afterward.

Part of `make adk-conformance`. See docs/design/v6.17.0/adk-contract-checklist.md.
"""

from __future__ import annotations

import json

import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adk.callbacks import make_document_loader

pytestmark = pytest.mark.adk_contract

_APP = "test"
_USER = "u"
_DOC_ID = "doc-123"


class _SaysDone(BaseLlm):
    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="done")]))


async def test_document_loader_artifact_is_awaited_and_retrievable(monkeypatch):
    # Force the non-RAG artifact path and stub the block source so the loader
    # exercises save_artifact without touching Firestore/GCS.
    monkeypatch.setattr("adk.callbacks._RAG_DOCUMENTS_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "tools.documents.context.build_document_context",
        lambda doc_id, mode="blocks": ("", [{"id": "b1", "text": "hello"}]),
    )

    artifact_service = InMemoryArtifactService()
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(app_name=_APP, user_id=_USER, state={"document_ids": [_DOC_ID]})

    agent = LlmAgent(name="door", model=_SaysDone(model="stub"), before_agent_callback=make_document_loader())
    runner = Runner(agent=agent, app_name=_APP, session_service=session_service, artifact_service=artifact_service)
    msg = types.Content(role="user", parts=[types.Part.from_text(text="hi")])
    list(
        runner.run(
            new_message=msg,
            user_id=_USER,
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )

    art = await artifact_service.load_artifact(
        app_name=_APP, user_id=_USER, session_id=session.id, filename=f"doc:{_DOC_ID}.json"
    )
    assert art is not None and art.inline_data is not None, (
        "doc artifact missing after the loader ran — save_artifact was not awaited "
        "(a bare coroutine is a silent no-op → the retrieve_artifact 404 that broke document fetch)"
    )
    blocks = json.loads(art.inline_data.data)
    assert blocks and blocks[0]["text"] == "hello"
